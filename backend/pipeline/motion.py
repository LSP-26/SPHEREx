"""P6: proper-motion propagation (plan A10, A9a). Pure.

Flat-sky approximation, fine for the arcsecond-to-arcminute shifts we need over a few years:
  dec(t) = dec0 + pmdec * dt
  ra(t)  = ra0  + pmra * dt / cos(dec0)       (pmra already includes cos(dec), as in Gaia)
with dt in years. Proper motions are in mas/yr. It is NOT valid for objects with huge motions
over decades, and it ignores parallax (SPHEREx moves in space too, plan section 5).
"""
from __future__ import annotations

import math

MAS_PER_DEG = 3_600_000.0
DAYS_PER_YEAR = 365.25
GAIA_EPOCH_MJD = 51544.5 + 16 * DAYS_PER_YEAR     # J2016.0 = MJD 57388.5


def propagate(ra0: float, dec0: float, pmra_mas_yr: float, pmdec_mas_yr: float,
              epoch_mjd: float, t_mjd: float) -> tuple[float, float]:
    """(ra, dec) in degrees at t_mjd, given the position at epoch_mjd."""
    dt = (t_mjd - epoch_mjd) / DAYS_PER_YEAR
    cosd = math.cos(math.radians(dec0))
    if abs(cosd) < 1e-9:
        raise ValueError("cannot propagate RA at the celestial pole")
    dra = pmra_mas_yr * dt / MAS_PER_DEG / cosd
    ddec = pmdec_mas_yr * dt / MAS_PER_DEG
    return (ra0 + dra) % 360.0, dec0 + ddec


def total_motion_px(pmra_mas_yr: float, pmdec_mas_yr: float, t_start_mjd: float,
                    t_end_mjd: float, pixel_arcsec: float = 6.15) -> float:
    """How far the object moves between two dates, in detector pixels."""
    dt = (t_end_mjd - t_start_mjd) / DAYS_PER_YEAR
    return math.hypot(pmra_mas_yr, pmdec_mas_yr) * dt / 1000.0 / pixel_arcsec
