"""T8a2: the solid-angle pixel map at a frame's pixels, keyed by (release, detector).

Photometry needs the area of each pixel: Jy = (IMAGE in MJy/sr) x area (arcsec^2) x 2.3504e-5
(docs/format_notes.md, T7). The map is one float32 2040 x 2040 image per detector and per
calibration, uncompressed, so a block of rows is one contiguous range read, like the frame planes.

  QR2 -> qr2/solid_angle_pixel_map/cal-sapm-v2-2025-164/<d>/
         solid_angle_pixel_map_D<d>_spx_cal-sapm-v2-2025-164.fits
  QR3 -> qr3/solid_angle_pixel_map/cal-sapm-v3-2026-191/<d>/
         solid_angle_pixel_map_D<d>_spx_cal-sapm-v3-2026-191.fits

The QR3 path was listed in 2026-11 but its files were not opened until scripts/x7_compare.py.
An unknown release is an error (UnknownRelease), never a guess: R Dra and RW Aur span QR2 and QR3,
and a wrong map would shift fluxes without any message.

Each map is checked once, when first opened: BUNIT (if present) must be arcsec2, and the median of
its middle row must be a plausible pixel area (30 to 46 arcsec^2; a 6.15 arcsec pixel is 37.82).
Every region read is checked to be finite and above 0.

  sam = SolidAngleMap()
  area = sam.region("QR3", 4, st.x0, st.y0, nx, ny)          # same shape as the stamp
  area = await sam.region_async("QR3", 4, st.x0, st.y0, nx, ny)
"""
from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable

import numpy as np

from backend.adapters.fits_layout import (
    ByteSource,
    HduLayout,
    HttpRangeSource,
    read_header,
    read_layout,
    read_rows,
)
from backend.adapters.s3_stamps import https_url

BUCKET = "nasa-irsa-spherex"
MAPS = {"QR2": "cal-sapm-v2-2025-164", "QR3": "cal-sapm-v3-2026-191"}
PLAUSIBLE_ARCSEC2 = (30.0, 46.0)


class UnknownRelease(ValueError):
    """No solid-angle map is known for this release."""


def map_key(release: str, detector: int, maps: dict[str, str] = MAPS) -> str:
    """The S3 key of the map for (release, detector). UnknownRelease for any other release."""
    rel = str(release).strip().upper()
    if rel not in maps:
        raise UnknownRelease(f"no solid-angle map is known for release {release!r} "
                             f"(known: {', '.join(sorted(maps))})")
    if not (isinstance(detector, (int, np.integer)) and 1 <= int(detector) <= 6):
        raise ValueError(f"detector must be a whole number from 1 to 6, not {detector!r}")
    d, cal = int(detector), maps[rel]
    return (f"{rel.lower()}/solid_angle_pixel_map/{cal}/{d}/"
            f"solid_angle_pixel_map_D{d}_spx_{cal}.fits")


def _unit_ok(bunit) -> bool:
    return str(bunit).lower().replace(" ", "").replace("^", "").replace("**", "") == "arcsec2"


class SolidAngleMap:
    def __init__(self, opener: Callable[[str], ByteSource] | None = None,
                 maps: dict[str, str] | None = None):
        """opener(url) -> a ByteSource. Default: HTTP range reads from S3."""
        self._opener = opener or HttpRangeSource
        self._maps = dict(MAPS if maps is None else maps)
        self._open_maps: dict[str, tuple[ByteSource, HduLayout]] = {}
        self._lock = threading.Lock()

    def key(self, release: str, detector: int) -> str:
        return map_key(release, detector, self._maps)

    def _open(self, release: str, detector: int) -> tuple[ByteSource, HduLayout]:
        key = self.key(release, detector)
        with self._lock:
            hit = self._open_maps.get(key)
        if hit is not None:
            return hit
        src = self._opener(https_url(f"s3://{BUCKET}/{key}"))       # network I/O: outside the lock
        layout = read_layout(src)
        hdu = next((h for h in layout if h.extname == "IMAGE" and h.is_image2d), None) \
            or next((h for h in layout if h.is_image2d), None)
        if hdu is None:
            raise ValueError(f"{key}: no uncompressed 2-D image in the file")
        bunit = read_header(src, hdu).get("BUNIT")
        if bunit is not None and not _unit_ok(bunit):
            raise ValueError(f"{key}: BUNIT is {bunit!r}, expected arcsec2")
        mid = hdu.shape[0] // 2
        median = float(np.nanmedian(read_rows(src, hdu, mid, mid + 1)))
        lo, hi = PLAUSIBLE_ARCSEC2
        if not lo <= median <= hi:
            raise ValueError(f"{key}: median pixel area {median:.4g} is not a plausible value "
                             f"in arcsec^2 ({lo:g} to {hi:g}); wrong units or wrong file")
        with self._lock:
            return self._open_maps.setdefault(key, (src, hdu))

    def region(self, release: str, detector: int, x0: int, y0: int, nx: int, ny: int
               ) -> np.ndarray:
        """Pixel areas (arcsec^2, float64, shape (ny, nx)) of columns x0..x0+nx-1 and rows
        y0..y0+ny-1, in the same 0-based detector pixels as Stamp.x0, Stamp.y0."""
        src, hdu = self._open(release, detector)
        nrows, ncols = hdu.shape
        if nx < 1 or ny < 1 or x0 < 0 or y0 < 0 or x0 + nx > ncols or y0 + ny > nrows:
            raise ValueError(f"region x {x0}:{x0 + nx}, y {y0}:{y0 + ny} is outside the "
                             f"{ncols} x {nrows} map")
        out = np.ascontiguousarray(read_rows(src, hdu, y0, y0 + ny)[:, x0:x0 + nx])
        if not (np.isfinite(out).all() and (out > 0).all()):
            raise ValueError(f"{self.key(release, detector)}: the map is not finite and above 0 "
                             f"in x {x0}:{x0 + nx}, y {y0}:{y0 + ny}")
        return out

    def at(self, release: str, detector: int, x: float, y: float) -> float:
        """The pixel area at the pixel containing (x, y), 0-based pixel centres."""
        return float(self.region(release, detector, int(round(x)), int(round(y)), 1, 1)[0, 0])

    async def region_async(self, release: str, detector: int, x0: int, y0: int, nx: int,
                           ny: int) -> np.ndarray:
        return await asyncio.to_thread(self.region, release, detector, x0, y0, nx, ny)
