"""X3: where the test targets really are, and how they move. Pure.

Positions are ICRS degrees at the epoch given, proper motions in mas/yr (pmra includes cos dec).
position_at(name, mjd) moves a target to any date with motion.propagate, so the flipbook can be
centred on where the target IS during the SPHEREx survey (MJD 60790 to 61315 so far), not where
it was in 2000. Sources are in the comments. scripts/x3_recon.py checks them against SIMBAD
(2026-11-05: all agree within 2 arcsec at J2000).
"""
from __future__ import annotations

from dataclasses import dataclass

from backend.pipeline.motion import propagate

J2000_MJD = 51544.5
DAYS_PER_YEAR = 365.25
SURVEY_MID_MJD = 61100.0       # about 2026-03-01, middle of the data so far


def year_to_mjd(year: float) -> float:
    return J2000_MJD + (year - 2000.0) * DAYS_PER_YEAR


@dataclass(frozen=True)
class Target:
    name: str
    ra: float
    dec: float
    pmra: float            # mas/yr, includes cos(dec)
    pmdec: float
    epoch_mjd: float       # epoch of (ra, dec)
    source: str
    note: str = ""


TARGETS: dict[str, Target] = {t.name: t for t in (
    # SIMBAD/Gaia J2000 position (17 57 48.49847 +04 41 36.1139), Gaia DR3 proper motion.
    # SIMBAD's own proper motion differs by 1 mas/yr (0.03 arcsec over 26 years): ignored.
    Target("barnard", 269.452077, 4.693365, -802.803, 10362.542, J2000_MJD,
           "Wikipedia (Gaia DR3) 2026-11",
           "10.4 arcsec/yr: about 270 arcsec north of its J2000 position in 2026, so a 5 arcmin "
           "field centred on the J2000 position MISSES it. Saturates (V 9.5, J 5.2)."),
    # Gaia DR3 via JWST proposal 2243 ("epoch of position 2000.0"). SIMBAD agrees to 0.0 arcsec.
    Target("eps_indi_a", 330.8402233, -56.78598, 3966.661, -2536.192, J2000_MJD,
           "JWST proposal 2243 / Gaia DR3 / SIMBAD",
           "The K star. ~120 arcsec from its J2000 position in 2026. Very bright, expect "
           "saturation. Epsilon Indi B is NOT here: it is about 400 arcsec (6.7 arcmin) away "
           "(1459 AU at 3.626 pc), see eps_indi_b."),
    # SIMBAD '* eps Ind B', epoch J2000 (x3_recon, 2026-11-05). About 402 arcsec east of A and
    # moving with it (SIMBAD pm differs from A's by 15 and 70 mas/yr).
    Target("eps_indi_b", 331.0441466, -56.782844, 3981.977, -2466.832, J2000_MJD,
           "SIMBAD (* eps Ind B) via x3_recon 2026-11-05",
           "Brown dwarf pair Ba/Bb, 0.7 arcsec apart, so unresolved at 6.15 arcsec. About 400 "
           "arcsec from Epsilon Indi A, so A is outside a 5 arcmin field. About 1.1 px of "
           "motion over the data so far: needs centroiding, not eyeballing."),
    # The NASA brief's position. 133.7951321 -7.24515 is NOT epoch J2000: the HST proposal 14157
    # gives 133.7826204 -7.24410 at epoch 2016 with pm (-8.1, +0.7) arcsec/yr, which puts the
    # brief's position at about 2010.7 (WISE epoch, 4 May 2010 first image). SIMBAD's J2000
    # position (133.818908, -7.247048) is 2.0 arcsec from this one propagated back to J2000,
    # which is 0.25 yr of motion: the epoch is good to about a quarter of a pixel.
    Target("wise0855", 133.7951321, -7.24515, -8123.7, 673.2, year_to_mjd(2010.7),
           "NASA brief position; epoch inferred from HST 14157 and checked against SIMBAD; "
           "pm Kirkpatrick+2021 (via Wikipedia)",
           "Moved about 125 arcsec (20 px) west since the brief's position. Y4 dwarf, J 25: "
           "expect it only in the longest channels, if at all."),
    # SIMBAD 'V* RW Aur', epoch J2000 (x3_recon, 2026-11-05).
    Target("rw_aur", 76.9565260, 30.401438, 7.230, -20.330, J2000_MJD,
           "SIMBAD (V* RW Aur) via x3_recon 2026-11-05",
           "Young variable binary. Bright: check saturation."),
    # SIMBAD 'V* R Dra', epoch J2000 (x3_recon, 2026-11-05).
    Target("r_dra", 248.1676108, 66.754979, -20.643, 6.149, J2000_MJD,
           "SIMBAD (V* R Dra) via x3_recon 2026-11-05",
           "Mira variable near the north ecliptic pole: about 2,400 frames in 35 visits."),
)}


def position_at(name: str, mjd: float = SURVEY_MID_MJD) -> tuple[float, float]:
    """(ra, dec) in degrees of target `name` at `mjd`."""
    t = TARGETS[name]
    return propagate(t.ra, t.dec, t.pmra, t.pmdec, t.epoch_mjd, mjd)
