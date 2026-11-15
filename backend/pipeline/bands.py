"""T9b2: is a bright detector band a fixed calibration offset or a signal that changes between
observations? Pure: arrays in, numbers out.

For each frame, from its row profile (median of the unflagged pixels of every detector row):
  amplitude  median of the band rows minus the median of the rows outside the band (a margin
             either side is left out)
  shape      the profile over the band window, minus that outside level
Across frames of one detector:
  spread     1.4826 x MAD / median of the amplitudes. Robust, so one frame with a bright star
             in its rows does not decide it.
  shape r    median correlation of each frame's shape with the mean shape. High means the band
             sits in the same rows every time.

READING IT. Same rows (shape r high) and a steady amplitude (spread under STABLE_SPREAD) is what
a calibration offset looks like. Same rows and an amplitude that changes is an additive signal
that depends on the observation (scattered light, sky or pointing dependent). It cannot tell
those two apart from a calibration term that itself drifts, and a bright star in the rows
raises that frame's amplitude.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

MARGIN = 20
MIN_OK = 50                 # pixels a row median must use
MIN_BAND_ROWS = 5
MIN_OUTSIDE_ROWS = 20
STABLE_SPREAD = 0.20
SAME_ROWS_R = 0.8


@dataclass(frozen=True)
class BandStat:
    amplitude: float
    peak: float
    baseline: float
    shape: np.ndarray       # profile minus baseline over the window, NaN where unusable


def band_stat(rows, median, n_ok, band: tuple[int, int]) -> BandStat | None:
    rows = np.asarray(rows)
    med = np.asarray(median, dtype="float64")
    ok = (np.asarray(n_ok) > MIN_OK) & np.isfinite(med)
    lo, hi = band
    in_band = ok & (rows >= lo) & (rows <= hi)
    outside = ok & ((rows < lo - MARGIN) | (rows > hi + MARGIN))
    if in_band.sum() < MIN_BAND_ROWS or outside.sum() < MIN_OUTSIDE_ROWS:
        return None
    base = float(np.median(med[outside]))
    window = (rows >= lo - MARGIN) & (rows <= hi + MARGIN)
    shape = np.where(window & ok, med - base, np.nan)[window]
    return BandStat(amplitude=float(np.median(med[in_band])) - base,
                    peak=float(np.max(med[in_band])) - base, baseline=base, shape=shape)


def _corr(a: np.ndarray, b: np.ndarray) -> float | None:
    m = np.isfinite(a) & np.isfinite(b)
    if m.sum() < MIN_BAND_ROWS or np.std(a[m]) == 0 or np.std(b[m]) == 0:
        return None
    return float(np.corrcoef(a[m], b[m])[0, 1])


def summarize(stats: list[BandStat]) -> dict:
    """Spread of the amplitude, shape agreement and a plain-words reading."""
    if len(stats) < 3:
        return {"n": len(stats), "verdict": "need at least 3 frames"}
    amps = np.array([s.amplitude for s in stats])
    med = float(np.median(amps))
    out = {"n": len(stats), "median_amplitude": med, "min_amplitude": float(amps.min()),
           "max_amplitude": float(amps.max())}
    if med <= 0:
        return {**out, "verdict": "no bright band: the median amplitude is not above the sky"}
    spread = float(1.4826 * np.median(np.abs(amps - med)) / med)
    mean_shape = np.nanmean(np.stack([s.shape for s in stats]), axis=0)
    rs = [r for s in stats if (r := _corr(s.shape, mean_shape)) is not None]
    shape_r = float(np.median(rs)) if rs else float("nan")
    base = np.array([s.baseline for s in stats])
    vs_sky = _corr(amps, base) if len(stats) >= 4 else None
    out.update(spread=spread, shape_r=shape_r, amplitude_vs_sky_r=vs_sky)
    if not shape_r >= SAME_ROWS_R:
        out["verdict"] = "the band does not keep one shape: not a fixed feature of the detector"
    elif spread < STABLE_SPREAD:
        out["verdict"] = "same rows and a steady amplitude: looks like a calibration offset"
    else:
        out["verdict"] = ("same rows but the amplitude changes between observations: an "
                          "additive signal that depends on the observation (scattered light, "
                          "or sky or pointing dependent), not a fixed offset")
    return out
