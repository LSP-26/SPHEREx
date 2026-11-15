"""P2: put stamps on a common pixel grid (plan A7). Pure apart from reading a WCS.

make_target_wcs builds a TAN grid centred on a sky position. regrid() resamples one cleaned
stamp onto it:
- "exact" (default): flux-conserving reproject_exact, for stacks and photometry. Values are
  surface brightness (MJy/sr), so each output pixel is the area-weighted average of the input.
- "nearest": nearest-neighbour, for fidelity views. SPHEREx is undersampled, and smoothing
  blurs the PSF.

Masked pixels would spread into their neighbours if reprojected as NaN (measured: one NaN
became 9). So the values (with masked pixels set to 0) and a 1/0 good mask are reprojected
separately and divided. An output pixel is valid where at least MIN_COVERAGE of its area
came from good input pixels.

Variance is reprojected the same way, which treats neighbouring pixels as fully correlated
(plan: resampled variance is correlated). That is the cautious choice.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from astropy.wcs import WCS

MIN_COVERAGE = 0.5
PIXEL_ARCSEC = 6.15


@dataclass(frozen=True)
class Gridded:
    image: np.ndarray       # float32, NaN where not covered by good pixels
    variance: np.ndarray    # float32, NaN where image is NaN
    coverage: np.ndarray    # float32, 0 to 1, fraction of the pixel's area from good input


def make_target_wcs(ra: float, dec: float, size_px: int,
                    pixel_arcsec: float = PIXEL_ARCSEC) -> WCS:
    """North-up TAN WCS with (ra, dec) at the centre of a size_px x size_px image."""
    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crval = [ra, dec]
    w.wcs.crpix = [(size_px + 1) / 2, (size_px + 1) / 2]       # FITS 1-based centre
    d = pixel_arcsec / 3600.0
    w.wcs.cdelt = [-d, d]
    w.wcs.cunit = ["deg", "deg"]
    return w


def stamp_wcs(frame_wcs: WCS, x0: int, y0: int, shape: tuple[int, int]) -> WCS:
    """The WCS of a stamp cut from a frame at array offset (x0, y0), SIP included."""
    ny, nx = shape
    return frame_wcs.slice((slice(y0, y0 + ny), slice(x0, x0 + nx)))


def regrid(image: np.ndarray, variance: np.ndarray, src_wcs: WCS, target_wcs: WCS,
           shape_out: tuple[int, int], method: str = "exact") -> Gridded:
    """Resample one cleaned stamp (NaN where masked) onto the target grid."""
    from reproject import reproject_exact, reproject_interp

    def run(arr: np.ndarray) -> np.ndarray:
        if method == "exact":
            out, _ = reproject_exact((arr, src_wcs), target_wcs, shape_out=shape_out)
        elif method == "nearest":
            out, _ = reproject_interp((arr, src_wcs), target_wcs, shape_out=shape_out,
                                      order="nearest-neighbor")
        else:
            raise ValueError(f"method must be 'exact' or 'nearest', not {method!r}")
        return np.nan_to_num(out, nan=0.0)

    good = np.isfinite(image) & np.isfinite(variance)
    cover = run(good.astype("float64"))
    img = run(np.where(good, image, 0.0))
    var = run(np.where(good, variance, 0.0))
    ok = cover >= MIN_COVERAGE
    with np.errstate(invalid="ignore", divide="ignore"):
        img_out = np.where(ok, img / cover, np.nan)
        var_out = np.where(ok, var / cover, np.nan)
    return Gridded(image=img_out.astype("float32"), variance=var_out.astype("float32"),
                   coverage=np.clip(cover, 0, 1).astype("float32"))


def regrid_mask(mask: np.ndarray, src_wcs: WCS, target_wcs: WCS,
                shape_out: tuple[int, int]) -> np.ndarray:
    """A boolean mask (for example saturated pixels) on the target grid, by nearest neighbour,
    the same mapping regrid(..., 'nearest') uses for the image. Outside the input is False."""
    from reproject import reproject_interp

    out, _ = reproject_interp((np.asarray(mask).astype("float64"), src_wcs), target_wcs,
                              shape_out=shape_out, order="nearest-neighbor")
    return np.nan_to_num(out, nan=0.0) > 0.5
