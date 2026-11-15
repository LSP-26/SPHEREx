"""P1: clean one stamp. Pure: arrays in, arrays out, no network or disk.

Steps (plan A6):
1. mask pixels with a fatal flag bit, non-finite pixels and non-positive variance;
2. subtract ZODI (the zodiacal model is NOT subtracted in IMAGE) when use_zodi is on;
3. fit a sigma-clipped plane to what is left and subtract it (the residual background).

FLAG BITS (X0, 2026-11). Names and meanings are from the MSKN/MSKD cards in the FLAGS header of
a real v27 frame (level2_2026W30_1B_0001_1D1_spx_l2b-v27-2026-222). The v20 header has no such
cards, but its per-flag pixel counts (N_OVERFLOW, N_SOURCE, ...) match the same bit numbers.
Bits 3, 4, 5, 8, 13, 16, 18 and 29 to 31 are not defined in the header: 4, 5 and 29 stay
fatal because the SPExPI list had them (conservative), the rest are ignored.
Not fatal on purpose: 12 FULLSAMPLE (information only), 20 CROSSTALK (small, flagged in the
label), 21 SOURCE (45 percent of a v27 frame is flagged SOURCE: "mapped to a known source",
not bad data) and 23/25 GHOST_FPA/STREAK (not seen yet, decide when one shows up).
TWO USES OF THE FLAGS (2026-11). A flag means "the pipeline doubts this number", not "the number is
meaningless". Measurements (good, image, variance) ignore every fatal-flagged pixel. The PICTURE
(display) shows every pixel that has a number, flagged or not, because on real frames 86 to 98
percent of OUTLIER, PERSIST, HALO and DICHROIC pixels sit within a factor of 2 of their clean
neighbours (and the ring round a saturated star is TRANSIENT/SUR_ERROR pixels of hundreds of
MJy/sr). Only DISPLAY_HIDDEN_BITS (detector defects: NONFUNC, MISSING_DATA, HOT, COLD, whose
values are meaningless, COLD is negative) and non-finite pixels are hidden, and small holes are
filled from their neighbours (fill_holes). Big holes stay black.
SATURATION is bit 1, OVERFLOW: its pixel count equals the header's N_OVERFLOW (600 in the v27
frame, 279 in the v20 frame) and its median IMAGE value is 160 to 180 MJy/sr against 0.3 for the
sky. Bit 26 BLOOM marks the artifact around a saturated source.
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

BIT_NAMES = {
    0: "TRANSIENT", 1: "OVERFLOW", 2: "SUR_ERROR", 6: "NONFUNC", 7: "DICHROIC",
    9: "MISSING_DATA", 10: "HOT", 11: "COLD", 12: "FULLSAMPLE", 14: "PHANMISS",
    15: "NONLINEAR", 17: "PERSIST", 19: "OUTLIER", 20: "CROSSTALK", 21: "SOURCE",
    22: "GHOST", 23: "GHOST_FPA", 24: "GHOST_EXT", 25: "STREAK", 26: "BLOOM",
    27: "SNOWBALL", 28: "HALO",
}
FATAL_BITS = (0, 1, 2, 4, 5, 6, 7, 9, 10, 11, 14, 15, 17, 19, 22, 24, 26, 27, 28, 29)
FATAL_MASK = sum(1 << b for b in FATAL_BITS)
SATURATION_BITS: tuple[int, ...] = (1,)     # OVERFLOW
SATURATION_MASK = sum(1 << b for b in SATURATION_BITS)

CLIP_SIGMA = 3.0
CLIP_ITERATIONS = 5
MIN_FIT_PIXELS = 12
MIN_SATURATED_PIXELS = 5   # one stray OVERFLOW pixel is not worth a label
DICHROIC_BIT = 7
MIN_DICHROIC_FRACTION = 0.01   # a label says so once a hundredth of the stamp is in the band
DISPLAY_HIDDEN_BITS = (6, 9, 10, 11)   # NONFUNC, MISSING_DATA, HOT, COLD: meaningless values
DISPLAY_HIDDEN_MASK = sum(1 << b for b in DISPLAY_HIDDEN_BITS)
FILL_PASSES = 3            # a hole is filled from its neighbours up to this many pixels deep
WHITE_PERCENTILE = 99.5    # the picture's white point (render.py)
MIN_WHITE_SIGMA = 10.0


def white_point(values: np.ndarray, noise: float) -> float:
    """Flux that maps to 255 in the preview: the 99.5th percentile of the good pixels, never
    below 10 sigma of the sky noise."""
    noise = noise if noise > 0 else 1.0
    top = np.percentile(values, WHITE_PERCENTILE) if values.size else 0.0
    return max(float(top), MIN_WHITE_SIGMA * noise)


@dataclass(frozen=True)
class Cleaned:
    image: np.ndarray        # float32, background-subtracted, NaN where masked
    variance: np.ndarray     # float32, NaN where masked
    good: np.ndarray         # bool, True where the pixel is usable
    background: np.ndarray   # float32, the plane that was subtracted (zodi not included)
    noise: float             # robust per-pixel noise of the clean image (same units as image)
    zodi_subtracted: bool
    frac_masked: float
    frac_saturated: float    # fraction of pixels with an OVERFLOW flag
    saturated: np.ndarray    # bool, True where the pixel has an OVERFLOW flag (drawn white)
    # float32, what the picture is drawn from: every pixel with a usable number (flagged or not),
    # background-subtracted, with small holes filled from neighbours; NaN where nothing sensible
    # is left. None for a Cleaned built elsewhere (the picture then uses image where good).
    display: np.ndarray | None = None
    # fraction of pixels with a DICHROIC flag: the strip at the edge of D3 (bottom) and D4 (top).
    # Shown, but the label says it is less reliable (2026-11 survey of 290 frames)
    frac_dichroic: float = 0.0
    frac_hidden: float = 0.0   # fraction of pixels the picture cannot show (before filling)
    # When most of a stamp is flagged (a stamp inside the dichroic band is about 97 percent
    # flagged) the few good pixels are too small a sample to set the picture's sky level, noise
    # and white point, and the picture comes out washed grey. Then the picture uses its own
    # background and noise from all usable pixels (display_basis), and the measurements keep
    # using the good pixels. display_noise 0 means: use `noise` and `good` as usual.
    display_noise: float = 0.0
    display_basis: np.ndarray | None = None


# Below this fraction of good pixels the picture's stretch is taken from all usable pixels.
DISPLAY_MIN_GOOD = 0.5


def fill_holes(values: np.ndarray, passes: int = FILL_PASSES) -> np.ndarray:
    """Fill NaN pixels with the median of their finite 3x3 neighbours, up to `passes` pixels deep.
    A hole wider than 2 x passes keeps a NaN middle."""
    out = values.astype("float64").copy()
    for _ in range(passes):
        holes = ~np.isfinite(out)
        if not holes.any():
            break
        win = sliding_window_view(np.pad(out, 1, constant_values=np.nan), (3, 3))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)   # windows that are all NaN
            med = np.nanmedian(win.reshape(*out.shape, 9), axis=2)
        fill = holes & np.isfinite(med)
        if not fill.any():
            break
        out[fill] = med[fill]
    return out


def robust_sigma(values: np.ndarray) -> float:
    """1.4826 x median absolute deviation. NaN-safe; 0.0 if there is nothing to measure."""
    v = values[np.isfinite(values)]
    if v.size == 0:
        return 0.0
    return float(1.4826 * np.median(np.abs(v - np.median(v))))


def fit_plane(img: np.ndarray, use: np.ndarray, clip: float = CLIP_SIGMA,
              iterations: int = CLIP_ITERATIONS) -> np.ndarray:
    """Sigma-clipped least-squares plane a + b*x + c*y over the pixels where `use` is True.
    With too few pixels it falls back to a flat level (the median)."""
    ny, nx = img.shape
    yy, xx = np.mgrid[0:ny, 0:nx].astype("float64")
    xx -= (nx - 1) / 2
    yy -= (ny - 1) / 2
    keep = use & np.isfinite(img)
    if keep.sum() < MIN_FIT_PIXELS:
        level = float(np.median(img[keep])) if keep.any() else 0.0
        return np.full(img.shape, level, dtype="float64")
    # First pass against the median, so a bright star cannot drag the starting plane.
    med = float(np.median(img[keep]))
    sig0 = robust_sigma(img[keep] - med)
    if sig0 > 0:
        first = keep & (np.abs(img - med) < clip * sig0 + 3 * sig0)
        if first.sum() >= MIN_FIT_PIXELS:
            keep = first
    coef = np.zeros(3)
    for _ in range(iterations):
        a = np.column_stack([np.ones(keep.sum()), xx[keep], yy[keep]])
        coef, *_ = np.linalg.lstsq(a, img[keep].astype("float64"), rcond=None)
        plane = coef[0] + coef[1] * xx + coef[2] * yy
        resid = (img - plane)
        sigma = robust_sigma(resid[keep])
        if sigma == 0.0:
            break
        new_keep = keep & (np.abs(resid) < clip * sigma)
        if new_keep.sum() < MIN_FIT_PIXELS or new_keep.sum() == keep.sum():
            break
        keep = new_keep
    return coef[0] + coef[1] * xx + coef[2] * yy


def clean_arrays(image, flags, variance, zodi=None, *, use_zodi: bool = True,
                 fatal_mask: int = FATAL_MASK, fill: bool = True) -> Cleaned:
    image = np.asarray(image, dtype="float64")
    variance = np.asarray(variance, dtype="float64")
    flags = np.asarray(flags).astype("int64") & 0xFFFFFFFF
    bad = ((flags & fatal_mask) != 0) | ~np.isfinite(image) | ~np.isfinite(variance) \
        | (variance <= 0)
    zodi_used = use_zodi and zodi is not None
    work = image - np.asarray(zodi, dtype="float64") if zodi_used else image.copy()
    good = ~bad & np.isfinite(work)
    plane = fit_plane(work, good)
    out = work - plane
    noise = robust_sigma(out[good])
    dich = ((flags >> DICHROIC_BIT) & 1).astype(bool)
    broken = ((flags & DISPLAY_HIDDEN_MASK) != 0) | ~np.isfinite(out)
    display_noise, basis = 0.0, None
    if good.mean() < DISPLAY_MIN_GOOD:
        sat_px = (flags & SATURATION_MASK) != 0 if SATURATION_MASK else np.zeros(flags.shape, bool)
        basis = ~broken & ~sat_px & np.isfinite(work)
        if basis.sum() >= MIN_FIT_PIXELS:
            shown_out = work - fit_plane(work, basis)
            display_noise = robust_sigma(shown_out[basis])
            out_display = shown_out
        else:
            basis, out_display = None, out
    else:
        out_display = out
    shown = np.where(broken, np.nan, out_display)
    # fill=False leaves the broken pixels as holes, so the picture holds measured values only
    display = (fill_holes(shown) if fill else shown).astype("float32")
    out[~good] = np.nan
    var = variance.copy()
    var[~good] = np.nan
    sat = ((flags & SATURATION_MASK) != 0) if SATURATION_MASK else np.zeros(flags.shape, bool)
    return Cleaned(
        image=out.astype("float32"), variance=var.astype("float32"), good=good,
        background=plane.astype("float32"), noise=noise,
        zodi_subtracted=bool(zodi_used), frac_masked=float((~good).mean()),
        frac_saturated=float(sat.mean()), saturated=sat, display=display,
        frac_dichroic=float(dich.mean()), frac_hidden=float(broken.mean()),
        display_noise=float(display_noise), display_basis=basis)


def clean_stamp(stamp, use_zodi: bool = True, fill: bool = True) -> Cleaned:
    """Clean a contracts.Stamp (duck-typed: needs image, flags, variance, zodi)."""
    return clean_arrays(stamp.image, stamp.flags, stamp.variance, stamp.zodi, use_zodi=use_zodi,
                        fill=fill)


# Detector rows (0-based, row 0 at the south edge) where an unflagged bright band was seen in the
# 2026-11-06 survey of 290 frames (scripts/survey_rows.py). It is smooth, not flagged, and
# 0.2 to 4.6 MJy/sr brighter than the sky in D1 (a bump) and D2 (a rise to the edge). Cause
# unknown. These are noted on the label, not removed.
# T9b2 (2026-11-07, scripts/analyze_bands.py, 12 v20 frames each): the bands are in the same
# rows in every frame (shape r 1.00) but the amplitude varies 0.12 to 3.1 MJy/sr (D1) and
# 0.12 to 1.2 (D2), so they are NOT a fixed calibration offset. The amplitude does not follow
# the frame's sky level and changes smoothly with observation week. Scattered light is
# plausible; a drifting calibration or thermal term is not excluded (only v20 was sampled).
# T9b4 (2026-11-08, scripts/band_vs_geometry.py, 30 frames per detector over the whole survey and
# over one week): no correlation with time, and the one-week spread equals the survey spread, so
# it is NOT a slow drift. D1 follows Moon angle, elongation and ecliptic latitude, D2 weakly.
# Pointing-dependent (likely scattered light), not a fixed offset. Not subtracted.
BRIGHT_BANDS = {1: (60, 270), 2: (1985, 2039)}


def band_note(detector: int, y0: int, height: int) -> str | None:
    """A label note when a stamp covering detector rows y0 .. y0+height-1 touches a known band."""
    band = BRIGHT_BANDS.get(detector)
    if band is None or height <= 0:
        return None
    if y0 <= band[1] and y0 + height - 1 >= band[0]:
        return "in a bright detector band (cause unknown), brightness here may not be real"
    return None


def flags_summary(c: Cleaned) -> list[str]:
    """Short plain-language notes for FrameLabel.flags_summary."""
    notes = []
    if round(c.frac_saturated * c.image.size) >= MIN_SATURATED_PIXELS:
        notes.append("saturated")
    if c.frac_masked >= 0.05:
        notes.append(f"{100 * c.frac_masked:.0f}% of pixels flagged by the pipeline")
    if c.frac_dichroic >= MIN_DICHROIC_FRACTION:
        notes.append("dichroic band, less reliable")   # the strip along one edge of D3 and D4
    return notes
