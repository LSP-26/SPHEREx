"""D5.1: wavelength at a detector pixel, from the file's own spectral WCS.

Measured on real frames (X0, 2026-11): every L2 file carries an alternate WCS 'W' in the IMAGE
header (CTYPE 'WAVE-TAB', two output axes: central wavelength and bandwidth, both in um) and a
tiny last extension, WCS-WAVE, holding the lookup table (control points X, Y and VALUES of shape
(2, ny, nx); 11 x 11 points in pipeline v20, 9 x 9 in v27). astropy evaluates it with
WCS(image_header, key='W', naxis=2, fobj=<hdulist with the table>), and that is what we use, so
the answer is whatever IRSA's own WCS gives. Wavelength changes mostly along y (about 120 rows
per spectral channel) and hardly at all along x.

The table sits in the last 5-6 KB of the file, so one small range read gets it. It depends on
the detector and on the calibration, not on the exposure, so TableWavelength reads it once per
(detector, version) and reuses it for every frame of that kind (and, with cache_dir, across
restarts). scripts/check_wavelength.py checks that assumption on real frames.

Result (2026-11-05, 286 groups, 54 version pairs): the table is identical within every
(detector, version) group. D1 to D4 are identical in all versions, but D5 and D6 differ
by 6.4 nm between v27 and the earlier families, which is above the 0.5 nm limit and 0.4
of the matching tolerance, so the key stays (detector, version).

  wl = TableWavelength()
  await wl.prefetch(frames)               # a handful of small reads, concurrently
  lam = wl.lambda_um(frame, x, y)         # contracts.interfaces.WavelengthSource
"""
from __future__ import annotations

import asyncio
import io
import re
import threading
from collections.abc import Callable
from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS

BLOCK = 2880
# Only the alternate WCS 'W' (the wavelength lookup). The header also carries SIP distortion
# coefficients for the SKY position (A_ORDER, A_2_0, ...), which differ a little between
# exposures. Left in, astropy applies them to the wavelength lookup as well ("inconsistent SIP
# distortion" message) and the same table gives wavelengths that differ by up to 5e-4 um from
# frame to frame (found by scripts/check_wavelength.py, 2026-11).
_W_KEYS = re.compile(
    r"^(WCSAXES|WCSNAME|CNAME\d|CRPIX\d|CRVAL\d|CDELT\d|CUNIT\d|CTYPE\d|PV\d_\d|PS\d_\d"
    r"|PC\d_\d|CD\d_\d)W$")
TAIL_BLOCKS = 12          # the WCS-WAVE header and data are 2 to 4 blocks; read a bit more
_BINTABLE = b"XTENSION= 'BINTABLE'"


def read_wave_hdul(src) -> fits.HDUList:
    """The WCS-WAVE table of a frame (src has .size and .read(start, length)), as an HDUList
    that astropy's WCS(fobj=...) accepts. Raises ValueError if the last extension is not it."""
    size = src.size
    n = min(TAIL_BLOCKS * BLOCK, size)
    start = size - n
    start -= start % BLOCK
    buf = src.read(start, size - start)
    i = buf.rfind(_BINTABLE)
    while i != -1 and (start + i) % BLOCK:
        i = buf.rfind(_BINTABLE, 0, i)
    if i == -1:
        raise ValueError("no binary table in the last blocks of the file")
    prim = fits.PrimaryHDU().header.tostring().encode("ascii")
    hdul = fits.open(io.BytesIO(prim + buf[i:]), memmap=False)
    if str(hdul[1].header.get("EXTNAME", "")).strip() != "WCS-WAVE":
        raise ValueError(f"last extension is {hdul[1].header.get('EXTNAME')!r}, not WCS-WAVE")
    _ = hdul[1].data      # load now, the buffer is already in memory
    return hdul


class SpectralWCS:
    """(wavelength, bandwidth) in um at 0-based detector pixels (x, y)."""

    def __init__(self, image_header: fits.Header, wave_hdul: fits.HDUList):
        only_w = fits.Header()
        for card in image_header.cards:
            if _W_KEYS.match(card.keyword):
                only_w.append(card)
        self.image_header, self.wave_hdul = only_w, wave_hdul
        self.wcs = WCS(only_w, key="W", naxis=2, fobj=wave_hdul)
        self.detector = int(str(wave_hdul[1].header.get("DETECTOR", "0")).strip() or 0)

    def at(self, x, y) -> tuple[np.ndarray, np.ndarray]:
        x, y = np.atleast_1d(np.asarray(x, float)), np.atleast_1d(np.asarray(y, float))
        out = self.wcs.all_pix2world(np.column_stack([x, y]), 0)
        return out[:, 0], out[:, 1]

    def lambda_um(self, x: float, y: float) -> float:
        return float(self.at(x, y)[0][0])

    def bandwidth_um(self, x: float, y: float) -> float:
        return float(self.at(x, y)[1][0])


    def save(self, path: Path) -> None:
        """A small FITS file (IMAGE header with the W keys + the WCS-WAVE table), about 10 KB."""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        fits.HDUList([fits.PrimaryHDU(), fits.ImageHDU(header=self.image_header, name="IMAGE"),
                      self.wave_hdul[1].copy()]).writeto(tmp, overwrite=True)
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path) -> SpectralWCS:
        with fits.open(path, memmap=False) as h:
            hdul = fits.HDUList([fits.PrimaryHDU(), h["WCS-WAVE"].copy()])
            _ = hdul[1].data
            return cls(h["IMAGE"].header.copy(), hdul)


def load_spectral_wcs(src) -> SpectralWCS:
    """From a ByteSource over one frame: IMAGE header (4 range reads) plus the table tail."""
    from backend.adapters.frame_layout import read_frame_layout
    layout = read_frame_layout(src)
    return SpectralWCS(layout.image_header, read_wave_hdul(src))


class TableWavelength:
    """Implements contracts.interfaces.WavelengthSource. One table per (detector, version)."""

    def __init__(self, opener: Callable | None = None, cache_dir: str | Path | None = None):
        """opener(frame) -> a ByteSource. Default: pooled HTTPS range reads from S3.
        cache_dir: tables are also saved there, so a restart does not read them again."""
        self._opener = opener
        self._dir = Path(cache_dir) if cache_dir else None
        self._cache: dict[tuple[int, str], SpectralWCS] = {}
        self._lock = threading.Lock()
        self._client = None

    @staticmethod
    def key(frame) -> tuple[int, str]:
        return frame.detector, frame.version

    def _open(self, frame):
        if self._opener is not None:
            return self._opener(frame)
        from backend.adapters.pooled_source import PooledRangeSource, make_client
        from backend.adapters.s3_stamps import https_url
        if self._client is None:
            self._client = make_client(8)
        return PooledRangeSource(https_url(frame.uri), self._client)

    def get(self, frame) -> SpectralWCS:
        k = self.key(frame)
        with self._lock:
            hit = self._cache.get(k)
        if hit is not None:
            return hit
        saved = self._dir / f"d{k[0]}_{k[1]}.fits" if self._dir else None
        if saved is not None and saved.exists():
            wcs = SpectralWCS.load(saved)
        else:
            wcs = load_spectral_wcs(self._open(frame))  # outside the lock: it is network I/O
            if saved is not None:
                wcs.save(saved)
        with self._lock:
            return self._cache.setdefault(k, wcs)

    async def prefetch(self, frames) -> None:
        """Load the table for every (detector, version) among `frames`, concurrently."""
        reps = {}
        for f in frames:
            reps.setdefault(self.key(f), f)
        todo = [f for k, f in reps.items() if k not in self._cache]
        await asyncio.gather(*[asyncio.to_thread(self.get, f) for f in todo])

    def lambda_um(self, frame, x: float, y: float) -> float:
        return self.get(frame).lambda_um(x, y)


def make_lambda_of(wavelength, pos) -> Callable:
    """The `lambda_of(frame)` that select.make_selector wants: wavelength at the target's pixel.
    Needs frame.wcs_header (a catalogue frame) and wavelength.prefetch(frames) already awaited."""
    from backend.adapters.s3_stamps import S3StampSource

    def lambda_of(frame) -> float | None:
        try:
            x, y = S3StampSource.target_pixel(frame, pos)
            return wavelength.lambda_um(frame, x, y)
        except Exception:
            return None
    return lambda_of
