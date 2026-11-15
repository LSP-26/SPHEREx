"""G3: centroid track for a mover too slow to jump a whole pixel between frames.

A star like Epsilon Indi B moves a few arcsec a year, under one SPHEREx pixel (6.15 arcsec). The
mover search cannot see it as a track of separate blobs, but its centre can still be measured
in every frame to a fraction of a pixel and the centres fitted against time. Pure: numbers in,
numbers out, no network.

fit_motion  straight-line fit of east/north offsets (arcsec) against time, with 3-sigma clipping.
            Returns the position at the reference time, the speed in arcsec/yr and its error,
            and how many points were kept. The errors come from the scatter of the points
            themselves, not from a model, so they include the frames' pointing errors.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

DAYS_PER_YEAR = 365.25
CLIP_SIGMA = 3.0
MIN_POINTS = 6


@dataclass(frozen=True)
class MotionFit:
    n_used: int
    n_total: int
    t_ref_mjd: float
    east0: float          # arcsec, at t_ref
    north0: float
    pm_east: float        # arcsec per year
    pm_north: float
    pm_east_err: float
    pm_north_err: float
    rms_east: float       # arcsec, scatter of the kept points about the line
    rms_north: float

    @property
    def speed(self) -> float:
        return float(np.hypot(self.pm_east, self.pm_north))

    @property
    def significance(self) -> float:
        """Speed over its error (the larger of the two axis errors): under about 3 the motion
        is not detected."""
        err = max(self.pm_east_err, self.pm_north_err)
        return self.speed / err if err > 0 else float("inf")


def _line(t: np.ndarray, y: np.ndarray, keep: np.ndarray) -> tuple[float, float, float, float]:
    a, b = np.polyfit(t[keep], y[keep], 1)
    resid = y[keep] - (a * t[keep] + b)
    n = int(keep.sum())
    rms = float(np.sqrt(np.sum(resid ** 2) / max(n - 2, 1)))
    sxx = float(np.sum((t[keep] - t[keep].mean()) ** 2))
    err = rms / np.sqrt(sxx) if sxx > 0 else float("inf")
    return float(a), float(b), float(err), rms


def fit_motion(t_mjd, east_arcsec, north_arcsec, t_ref_mjd: float | None = None
               ) -> MotionFit | None:
    """None when fewer than MIN_POINTS finite points, or when all times are equal."""
    t = np.asarray(t_mjd, dtype="float64")
    e = np.asarray(east_arcsec, dtype="float64")
    n = np.asarray(north_arcsec, dtype="float64")
    ok = np.isfinite(t) & np.isfinite(e) & np.isfinite(n)
    if ok.sum() < MIN_POINTS or np.ptp(t[ok]) <= 0:
        return None
    t_ref = float(np.mean(t[ok]) if t_ref_mjd is None else t_ref_mjd)
    ty = (t - t_ref) / DAYS_PER_YEAR
    keep = ok.copy()
    for _ in range(5):
        fits = [_line(ty, v, keep) for v in (e, n)]
        new = ok.copy()
        for v, (a, b, _err, rms) in zip((e, n), fits, strict=True):
            if rms > 0:
                new &= np.abs(v - (a * ty + b)) <= CLIP_SIGMA * rms
        if new.sum() < MIN_POINTS or (new == keep).all():
            break
        keep = new
    (ae, be, ee, re_), (an, bn, en, rn) = (_line(ty, e, keep), _line(ty, n, keep))
    return MotionFit(n_used=int(keep.sum()), n_total=int(ok.sum()), t_ref_mjd=t_ref,
                     east0=be, north0=bn, pm_east=ae, pm_north=an, pm_east_err=ee,
                     pm_north_err=en, rms_east=re_, rms_north=rn)
