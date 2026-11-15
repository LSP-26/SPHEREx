"""The flagged-pixel layer (backend/pipeline/flagmap.py)."""

import numpy as np

from backend.pipeline.flagmap import CATEGORIES, categorise, render_categories, summary

KEYS = [c.key for c in CATEGORIES]


def test_each_pixel_gets_the_first_matching_group_and_source_is_not_drawn():
    f = np.zeros((2, 4), "int64")
    f[0, 0] = (1 << 1) | (1 << 26)   # saturated core inside a bloom: saturated
    f[0, 1] = 1 << 26                 # bloom only
    f[0, 2] = (1 << 21) | (1 << 17)   # known source with persistence: artefact
    f[0, 3] = 1 << 21                 # known source only
    f[1, 0] = (1 << 10) | (1 << 1)    # hot pixel that also overflowed: defect
    f[1, 1] = 1 << 7                  # dichroic strip
    f[1, 2] = 1 << 12                 # FULLSAMPLE: information only, no group
    cat = categorise(f)
    name = {0: None} | {k + 1: key for k, key in enumerate(KEYS)}
    assert [name[int(v)] for v in cat[0]] == ["saturated", "bright", "artefact", "source"]
    assert [name[int(v)] for v in cat[1]] == ["hidden", "dichroic", None, None]
    rgb, drawn = render_categories(cat)
    assert drawn.tolist() == [[True, True, True, False], [True, True, False, False]]
    assert tuple(rgb[0, 0]) == CATEGORIES[KEYS.index("saturated")].rgb


def test_fractions_count_only_covered_pixels():
    cat = np.array([[2, 2, 0, 0]], "uint8")
    covered = np.array([[True, True, True, False]])
    s = {c["key"]: c["fraction"] for c in summary(cat, covered)}
    assert s["saturated"] == round(2 / 3, 4) and s["hidden"] == 0
