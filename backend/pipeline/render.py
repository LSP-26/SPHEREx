"""P3.2: the real Renderer. Pure: arrays in, 8-bit PNG out. Replaces StubRenderer.

Per frame: clean (clean.py), regrid onto a north-up grid centred on the request (nearest
neighbour, so the undersampled PSF is not blurred), flip so row 0 is the top of the picture,
asinh stretch with the softening set from the robust sky noise, 8-bit greyscale PNG. Masked
pixels and pixels outside the frame are drawn black, except saturated (OVERFLOW) pixels,
which are drawn white. The browser only gets 8 bits, so the float arrays stay on the
server and a stretch change re-renders.

ORIENTATION. FITS puts pixel row 0 at the bottom (south on the common grid), and the PNG writer
puts row 0 at the top, so the picture is flipped vertically after regridding: north up, east left,
the sky as you would see it. Every frame goes through the same grid, so stars do not rotate when
the frames are blinked (SPHEREx's roll differs between visits).

A stamp without a WCS (the synthetic fixtures) cannot be regridded: it is only flipped.

KNOWN LIMIT: each frame is scaled by its own noise and brightest pixels, so a blink of two
frames can flicker in brightness. For the refine stage, pass a shared `white` to render_array.
Colour (merged detectors) needs several frames and is added with the co-add (P5).
"""
from __future__ import annotations

import math
import struct
import zlib
from dataclasses import replace

import numpy as np
from scipy import ndimage

from backend.adapters.wcs_util import celestial_wcs
from backend.pipeline.clean import (  # noqa: F401  (the two constants are imported from here)
    MIN_WHITE_SIGMA,
    WHITE_PERCENTILE,
    Cleaned,
    band_note,
    clean_stamp,
    flags_summary,
    robust_sigma,
    white_point,
)
from backend.pipeline.grid import PIXEL_ARCSEC, make_target_wcs, regrid, regrid_mask, stamp_wcs
from contracts import Orientation
from contracts.interfaces import Rendered

SOFTENING = 3.0      # in units of the sky noise
BLACK_SIGMA = 1.0    # black point, in sigma below the sky


def _png(gray: np.ndarray, covered: np.ndarray | None = None) -> bytes:
    """8-bit grey PNG. With `covered`, grey + alpha: pixels with no data are transparent, so the
    page can show them in its own colour instead of the black of a dark sky."""
    h, w = gray.shape

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    if covered is None:
        rows, ctype = gray, 0
    else:
        alpha = np.where(covered, 255, 0).astype("uint8")
        rows, ctype = np.dstack([gray, alpha]).reshape(h, w * 2), 4
    raw = b"".join(b"\x00" + rows[y].tobytes() for y in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, ctype, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def _star_cores(holes: np.ndarray, s: np.ndarray) -> np.ndarray:
    """Holes (no number at all) that sit inside the picture and are ringed by bright pixels.
    The core of a saturated star has no value, and not always an OVERFLOW flag; it is white,
    not a black hole. A hole that touches the edge (outside the data, a missing strip) or is
    ringed by dark sky stays black."""
    if not holes.any():
        return holes
    labels, n = ndimage.label(holes)
    out = np.zeros_like(holes)
    for i in range(1, n + 1):
        comp = labels == i
        if comp[0].any() or comp[-1].any() or comp[:, 0].any() or comp[:, -1].any():
            continue
        ring = ndimage.binary_dilation(comp, iterations=2) & ~holes
        if ring.any() and float(np.median(s[ring])) > 0.5:
            out |= comp
    return out


def render_array(c: Cleaned, white: float | None = None) -> np.ndarray:
    return render_array_covered(c, white)[0]


def render_array_covered(c: Cleaned, white: float | None = None,
                         fill: bool = True) -> tuple[np.ndarray, np.ndarray]:
    """uint8 image. `white` is the flux that maps to 255 (default: per-frame, see KNOWN LIMIT).

    Drawn from c.display, which keeps every pixel with a usable number (flagged or not) and has
    small holes filled; only what is still NaN there is black. A Cleaned without a display image
    falls back to its good pixels. The white point comes from the good pixels only, so bright
    flagged pixels (the ring round a saturated star) clip to white instead of moving the scale.
    """
    noise = c.display_noise if c.display_noise > 0 else c.noise
    noise = noise if noise > 0 else 1.0
    disp = c.display if c.display is not None else np.where(c.good, c.image, np.nan)
    shown = np.isfinite(disp)
    img = np.where(shown, disp, 0.0).astype("float64")
    scale = SOFTENING * noise
    if white is None:
        if c.display_basis is not None and c.display is not None and c.display_noise > 0:
            white = white_point(c.display[c.display_basis & np.isfinite(c.display)], noise)
        else:
            white = white_point(c.image[c.good], noise)
    lo, hi = np.arcsinh(-BLACK_SIGMA / SOFTENING), np.arcsinh(white / scale)
    s = (np.arcsinh(img / scale) - lo) / (hi - lo)
    out = (np.clip(s, 0.0, 1.0) * 255).astype("uint8")
    out[~shown] = 0
    cores = _star_cores(~shown, s) if fill else np.zeros(shown.shape, bool)
    out[cores] = 255            # a hole in the middle of a bright blob is a star's core
    out[c.saturated] = 255      # a saturated core is the brightest thing there, not a hole
    return out, shown | cores | c.saturated   # second: pixels that carry data (the rest is no data)


def preview_size_px(fov_arcmin: float) -> int:
    """Odd side length in pixels of the common grid (same rule as jobs.preview.stamp_size_px)."""
    return max(11, math.ceil(fov_arcmin * 60 / PIXEL_ARCSEC)) | 1


def regrid_cleaned(c: Cleaned, stamp, ra: float, dec: float, size: int) -> Cleaned | None:
    """The cleaned stamp resampled onto a north-up grid centred on (ra, dec), or None if the
    stamp carries no WCS. Pixels the stamp does not cover become NaN (drawn black)."""
    header = getattr(stamp, "wcs_header", None) or getattr(stamp.frame, "wcs_header", None)
    if not header:
        return None
    src = stamp_wcs(celestial_wcs(header), stamp.x0, stamp.y0, c.image.shape)
    target = make_target_wcs(ra, dec, size)
    g = regrid(c.image, c.variance, src, target, (size, size), "nearest")
    sat = regrid_mask(c.saturated, src, target, (size, size))
    basis = (None if c.display_basis is None
             else regrid_mask(c.display_basis, src, target, (size, size)))
    disp = c.display if c.display is not None else np.where(c.good, c.image, np.nan)
    gd = regrid(disp, np.ones(disp.shape, "float32"), src, target, (size, size), "nearest")
    good = np.isfinite(g.image)
    return replace(c, image=g.image, variance=g.variance, good=good, saturated=sat,
                   display=gd.image, display_basis=basis,
                   background=np.zeros(g.image.shape, "float32"),
                   noise=robust_sigma(g.image[good]))

def render_stamp(stamp, req) -> tuple[np.ndarray, Cleaned]:
    gray, cleaned, _ = render_stamp_covered(stamp, req)
    return gray, cleaned


def render_stamp_covered(stamp, req) -> tuple[np.ndarray, Cleaned, np.ndarray]:
    """(uint8 picture, north up unless req.orient is "detector"; the cleaned stamp it came from).
    The summary notes (saturation, masked fraction) are read from the cleaned stamp, before
    regridding."""
    fill = bool(getattr(req, "fill", True))
    cleaned = clean_stamp(stamp, use_zodi=req.use_zodi, fill=fill)
    if getattr(req, "orient", Orientation.NORTH) == Orientation.DETECTOR:
        # as the detector saw it: no regrid, so no black corners. FITS row 0 is the bottom row.
        gray, cov = render_array_covered(cleaned, fill=fill)
        return np.ascontiguousarray(np.flipud(gray)), cleaned, np.ascontiguousarray(np.flipud(cov))
    on_grid = regrid_cleaned(cleaned, stamp, req.ra, req.dec, preview_size_px(req.fov_arcmin))
    gray, cov = render_array_covered(on_grid if on_grid is not None else cleaned, fill=fill)
    return np.ascontiguousarray(np.flipud(gray)), cleaned, np.ascontiguousarray(np.flipud(cov))


class RealRenderer:
    """Implements contracts.interfaces.Renderer."""

    def render_png(self, stamp, req) -> Rendered:
        gray, cleaned, covered = render_stamp_covered(stamp, req)
        notes = flags_summary(cleaned)
        detector = getattr(getattr(stamp, "frame", None), "detector", None)
        band = (band_note(detector, getattr(stamp, "y0", 0), stamp.image.shape[0])
                if detector is not None else None)
        if band:
            notes.append(band)
        return Rendered(png=_png(gray, covered), width=gray.shape[1], height=gray.shape[0],
                        flags_summary=notes)
