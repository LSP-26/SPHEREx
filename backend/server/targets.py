"""GET /targets: the demo targets as they ARE during the survey, from backend/pipeline/targets.py.

The frontend quick-fill buttons read this, so the numbers live in one place. Positions are
propagated to the middle of the survey so far (SURVEY_MID_MJD). WISE 0855 also appears at the
position printed in the NASA brief, which is an older epoch (about 2010.7).
"""
from __future__ import annotations

from backend.pipeline.targets import SURVEY_MID_MJD, TARGETS, position_at

LABELS = {
    "barnard": "Barnard's Star",
    "eps_indi_a": "Epsilon Indi (the K star)",
    "eps_indi_b": "Epsilon Indi B (brown dwarf pair)",
    "wise0855": "WISE 0855",
    "rw_aur": "RW Aurigae",
    "r_dra": "R Draconis",
}

def target_list(mjd: float = SURVEY_MID_MJD) -> list[dict]:
    out = []
    for key, t in TARGETS.items():
        ra, dec = position_at(key, mjd)
        out.append({"key": key, "name": LABELS.get(key, key), "ra": round(ra, 6),
                    "dec": round(dec, 6), "note": t.note, "source": t.source,
                    "position_epoch": "survey"})
    w = TARGETS["wise0855"]
    out.append({"key": "wise0855_brief", "name": "WISE 0855 (NASA brief position)",
                "ra": w.ra, "dec": w.dec,
                "note": "Position printed in the brief, epoch about 2010.7 [verify]. "
                        "The object has since moved about 20 pixels west of this position.",
                "source": w.source, "position_epoch": "brief"})
    return out
