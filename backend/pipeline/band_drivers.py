"""T9b4: what does a bright detector band's amplitude follow across frames? Pure.

bands.py measures the amplitude in one frame. This module takes the amplitudes of many frames
and the same frames' time, sky geometry and bright-source counts, and says which of them the
amplitude tracks (Spearman rank correlation, so a few outliers or a bright star in the rows
cannot decide it).

READING IT. In a survey, time, zodiacal level, ecliptic latitude and pointing move together, so
a sample spread over the whole survey can show that the amplitude follows "time and sky" but not
which. A sample from a short window separates them: a slow drift cannot act within days.
"""
from __future__ import annotations

import numpy as np
from scipy.stats import spearmanr

MIN_FRAMES = 8
STRONG_RHO = 0.5
STRONG_P = 0.01
TIME_VARS = frozenset({"mjd"})
SKY_VARS = frozenset({"zodi_band", "zodi_all", "elongation", "ecl_lat", "moon_sep"})
SOURCE_VARS = frozenset({"n_overflow", "ghost", "ghost_ext"})


def rank_correlations(amplitude, variables: dict) -> dict[str, tuple[float, float, int]]:
    """{name: (rho, p, n)} for every variable that can be tested. Pairs with a NaN are dropped;
    a variable with fewer than MIN_FRAMES pairs or no variation is left out."""
    a = np.asarray(amplitude, dtype="float64")
    out: dict[str, tuple[float, float, int]] = {}
    for name, v in variables.items():
        x = np.asarray(v, dtype="float64")
        if x.shape != a.shape:
            raise ValueError(f"{name}: {x.shape} values for {a.shape} amplitudes")
        m = np.isfinite(a) & np.isfinite(x)
        if m.sum() < MIN_FRAMES or np.ptp(a[m]) == 0 or np.ptp(x[m]) == 0:
            continue
        rho, p = spearmanr(a[m], x[m])
        out[name] = (float(rho), float(p), int(m.sum()))
    return out


def strong_drivers(corr: dict) -> list[str]:
    return sorted(k for k, (rho, p, _) in corr.items()
                  if abs(rho) >= STRONG_RHO and p < STRONG_P)


def verdict(corr: dict) -> str:
    if not corr:
        return "too few frames (or nothing varies) to test"
    strong = set(strong_drivers(corr))
    names = ", ".join(sorted(strong))
    if strong & SOURCE_VARS:
        return (f"amplitude follows bright sources ({names}): points to scattered or ghost "
                "light from bright stars")
    if strong & SKY_VARS and strong & TIME_VARS:
        return (f"amplitude follows both time and sky geometry ({names}); in a survey those "
                "move together, so this sample cannot separate them: run a short --window")
    if strong & SKY_VARS:
        return (f"amplitude follows sky geometry ({names}) but not time: points to scattered "
                "light that depends on where SPHEREx looks")
    if strong & TIME_VARS:
        return ("amplitude follows time only, not sky geometry or bright sources: points to a "
                "slow drift of the instrument or calibration")
    if strong:
        return f"amplitude follows {names}"
    return ("amplitude follows none of the tested variables (not conclusive: the sample may "
            "be small, or the driver is not among them)")
