"""A9: the difference of two frames of the same field, in flux units (MJy/sr).

Both frames are cleaned and put on the same north-up grid first (render.regrid_cleaned), so a
pixel is the same piece of sky in both. The difference B minus A is taken only where both
frames have a measured pixel (not masked, not saturated); everywhere else it is unknown and the
picture leaves it transparent. Nothing is interpolated.

Each frame has had its own smooth sky level (a plane) removed, so a change in the broad sky
brightness between the two dates does not show: only localised differences do. A source that
moved shows as a bright blob where it is in B beside a dark blob where it was in A. Frames at
different wavelengths see different brightness (the filter is variable), so such a pair can
differ without anything having changed: the caller says so.

Picture: grey 128 is no difference. Brighter in B is lighter, brighter in A is darker, on an
asinh scale in units of the noise of the difference.
"""
from __future__ import annotations

import numpy as np

from backend.pipeline.clean import Cleaned, robust_sigma

SOFTENING = 3.0       # in units of the noise of the difference
MIN_WHITE_SIGMA = 10.0


def difference(a: Cleaned, b: Cleaned) -> tuple[np.ndarray, float]:
    """(B - A as float32, NaN where either frame has no measured pixel; its robust noise)."""
    if a.image.shape != b.image.shape:
        raise ValueError("the two frames are not on the same grid")
    both = (np.isfinite(a.image) & np.isfinite(b.image) & a.good & b.good
            & ~a.saturated & ~b.saturated)
    d = np.where(both, b.image.astype("float64") - a.image.astype("float64"), np.nan)
    return d.astype("float32"), robust_sigma(d)


def render_difference(d: np.ndarray, noise: float) -> tuple[np.ndarray, np.ndarray, float]:
    """(uint8 picture, bool covered, the |difference| that maps to full black or white)."""
    covered = np.isfinite(d)
    sigma = noise if noise > 0 else 1.0
    mags = np.abs(d[covered])
    white = max(MIN_WHITE_SIGMA * sigma, float(np.percentile(mags, 99.5)) if mags.size else 0.0)
    scale = SOFTENING * sigma
    s = np.arcsinh(np.where(covered, d, 0.0) / scale) / np.arcsinh(white / scale)
    out = np.rint(127.5 + 127.5 * np.clip(s, -1.0, 1.0)).astype("uint8")
    out[~covered] = 128
    return out, covered, white
