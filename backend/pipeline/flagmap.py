"""Which pixels of a frame the SPHEREx pipeline flagged, as a coloured layer for the picture.

The picture (clean.display) shows every pixel that has a number, flagged or not; measurements use
only unflagged ones (clean.py, "TWO USES OF THE FLAGS"). This layer shows where the flags are, so
a viewer can tell a real feature from a flagged one. Each pixel gets the FIRST group in CATEGORIES
that matches one of its bits, so a saturated star core is "saturated", not also "around a bright
star". SOURCE (21) is not drawn: it marks known sources, not bad data, and covers about 45 percent
of a v27 frame; its fraction is still given. Pure: arrays in, arrays out.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from backend.pipeline.clean import BIT_NAMES, DICHROIC_BIT, DISPLAY_HIDDEN_BITS


@dataclass(frozen=True)
class Category:
    key: str
    label: str
    bits: tuple[int, ...]
    rgb: tuple[int, int, int]
    drawn: bool = True


CATEGORIES: tuple[Category, ...] = (
    Category("hidden", "Detector defect (not shown in the picture)", DISPLAY_HIDDEN_BITS,
             (148, 163, 184)),
    Category("saturated", "Saturated", (1,), (239, 68, 68)),
    Category("bright", "Around a bright star (bloom, halo, nonlinear, transient)",
             (0, 2, 15, 26, 28), (249, 115, 22)),
    Category("artefact", "Artefact (ghost, persistence, streak, snowball, outlier)",
             (14, 17, 19, 20, 22, 23, 24, 25, 27), (217, 70, 239)),
    Category("dichroic", "Dichroic edge strip (D3, D4)", (DICHROIC_BIT,), (234, 179, 8)),
    Category("source", "Known source (information only, not drawn)", (21,), (34, 211, 238),
             drawn=False),
)
FLAGMAP_VERSION = 1  # part of the cache key: bump when the groups or colours change


def categorise(flags: np.ndarray) -> np.ndarray:
    """uint8 category per pixel: 0 = none of the groups, k = CATEGORIES[k - 1]."""
    f = np.asarray(flags).astype("int64")
    out = np.zeros(f.shape, "uint8")
    for k in range(len(CATEGORIES), 0, -1):  # first category wins: paint it last
        mask = sum(1 << b for b in CATEGORIES[k - 1].bits)
        out[(f & mask) != 0] = k
    return out


def bit_counts(flags: np.ndarray) -> dict[str, int]:
    """Pixels carrying each named bit (a pixel can carry several)."""
    f = np.asarray(flags).astype("int64")
    out = {}
    for b, name in sorted(BIT_NAMES.items()):
        n = int(((f >> b) & 1).sum())
        if n:
            out[name] = n
    return out


def render_categories(cat: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(RGB uint8, drawn mask) for a category map."""
    rgb = np.zeros(cat.shape + (3,), "uint8")
    drawn = np.zeros(cat.shape, bool)
    for k, c in enumerate(CATEGORIES, start=1):
        if c.drawn:
            m = cat == k
            rgb[m] = c.rgb
            drawn |= m
    return rgb, drawn


def summary(cat: np.ndarray, covered: np.ndarray) -> list[dict]:
    """Per category: key, label, colour, drawn, fraction of the covered pixels."""
    n = max(1, int(covered.sum()))
    out = []
    for k, c in enumerate(CATEGORIES, start=1):
        out.append({
            "key": c.key,
            "label": c.label,
            "colour": "#%02x%02x%02x" % c.rgb,
            "drawn": c.drawn,
            "bits": [BIT_NAMES.get(b, str(b)) for b in c.bits],
            "fraction": round(float(((cat == k) & covered).sum()) / n, 4),
        })
    return out


def category_grid(
    flags: np.ndarray, stamp, ra: float, dec: float, size: int, north: bool = True
) -> tuple[np.ndarray, np.ndarray]:
    """(category map, covered) laid out like the frame's picture: on the north-up grid of the
    field (nearest neighbour, the mapping the picture uses) or, with north False, as the detector
    saw it. Both flipped so row 0 is the top, like the PNG. A stamp with no WCS is drawn as the
    detector saw it, as the picture is (render.render_stamp_covered)."""
    cat = categorise(flags)
    header = getattr(stamp, "wcs_header", None) or getattr(stamp.frame, "wcs_header", None)
    if not north or not header:
        return np.ascontiguousarray(np.flipud(cat)), np.ones(cat.shape, bool)
    from reproject import reproject_interp

    from backend.adapters.wcs_util import celestial_wcs
    from backend.pipeline.grid import make_target_wcs, stamp_wcs

    src = stamp_wcs(celestial_wcs(header), stamp.x0, stamp.y0, cat.shape)
    out, _ = reproject_interp((cat.astype("float64"), src), make_target_wcs(ra, dec, size),
                              shape_out=(size, size), order="nearest-neighbor")
    covered = np.isfinite(out)
    grid = np.where(covered, np.rint(np.nan_to_num(out)), 0).astype("uint8")
    return np.ascontiguousarray(np.flipud(grid)), np.ascontiguousarray(np.flipud(covered))
