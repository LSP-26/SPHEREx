"""C2: is solid-angle map pixel (x, y) the same pixel as IMAGE pixel (x, y)?

  python scripts/check_sapm_alignment.py                 # D1 (SWIR) and D4 (MWIR)
  python scripts/check_sapm_alignment.py 1 2 3 4 5 6

The map varies smoothly by 1.7 to 2.4 percent across the detector. The frame's own WCS (SIP
included) gives the true sky area of every pixel from its distortion. If the map is laid out
like IMAGE, the two patterns must agree in place. For each detector this takes one catalogue
frame, computes the WCS pixel area on a 20 x 20 grid, reads the map at the same pixels and at
the flipped versions (X, Y, both, transposed), and prints the correlation of each.
PASS: unflipped has the highest correlation, above 0.9, and beats the next by at least 0.1.
If every correlation is under 0.5 the map does not follow the WCS distortion, so this test
cannot decide (INCONCLUSIVE); then check with a bright star near a detector edge instead.
Needs the catalogue and the network (one 16 MB map per detector).
"""
from __future__ import annotations

import asyncio
import sys

import numpy as np

from backend.adapters.catalogue_index import CatalogueFrameIndex
from backend.adapters.solid_angle import SolidAngleMap
from backend.adapters.wcs_util import celestial_wcs
from backend.pipeline.targets import SURVEY_MID_MJD, position_at
from contracts import SkyPosition

GRID = np.arange(50, 2040, 100)        # 20 sample pixels per axis (0-based)
CATALOGUE = ".cache/catalogue"


def wcs_area(wcs, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
    """Pixel area in arcsec^2 at 0-based pixel centres, from central differences of the WCS."""
    pts = np.array([(x, y) for y in ys for x in xs], dtype=float)
    c = wcs.all_pix2world(pts, 0)
    cosd = np.cos(np.radians(c[:, 1]))

    def offset(dx: float, dy: float) -> np.ndarray:
        w = wcs.all_pix2world(pts + [dx, dy], 0)
        dra = ((w[:, 0] - c[:, 0] + 180) % 360 - 180) * cosd
        return np.c_[dra, w[:, 1] - c[:, 1]] * 3600.0

    vx = offset(0.5, 0) - offset(-0.5, 0)
    vy = offset(0, 0.5) - offset(0, -0.5)
    return np.abs(vx[:, 0] * vy[:, 1] - vx[:, 1] * vy[:, 0]).reshape(len(ys), len(xs))


def corr(a: np.ndarray, b: np.ndarray) -> float:
    a, b = a.ravel() - a.mean(), b.ravel() - b.mean()
    d = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(a @ b / d) if d > 0 else float("nan")


async def main(dets: list[int]) -> None:
    index = CatalogueFrameIndex(CATALOGUE)
    ra, dec = position_at("barnard", SURVEY_MID_MJD)
    frames = await index.frames_at(SkyPosition(ra=ra, dec=dec))
    sam = SolidAngleMap()
    all_pass = True
    for det in dets:
        cand = [f for f in frames if f.detector == det]
        if not cand:
            print(f"D{det}: no frame near Barnard's Star, skipped")
            continue
        f = cand[len(cand) // 2]
        area = wcs_area(celestial_wcs(f.wcs_header), GRID, GRID)
        m = await sam.region_async(f.release, det, 0, 0, 2040, 2040)
        ix = np.ix_(GRID, GRID)
        variants = {
            "as is": m[ix],
            "flip X": m[np.ix_(GRID, 2039 - GRID)],
            "flip Y": m[np.ix_(2039 - GRID, GRID)],
            "flip X and Y": m[np.ix_(2039 - GRID, 2039 - GRID)],
            "transposed": m.T[ix],
        }
        scores = {k: corr(area, v) for k, v in variants.items()}
        print(f"\nD{det} {f.frame_id} ({f.release} {f.version})")
        print(f"  WCS pixel area: median {np.median(area):.3f}, spread "
              f"{100 * (area.max() - area.min()) / np.median(area):.2f}% ; "
              f"map: median {np.median(m):.3f}, spread "
              f"{100 * (m.max() - m.min()) / np.median(m):.2f}%")
        for k, v in sorted(scores.items(), key=lambda kv: -kv[1]):
            print(f"  {k:<13} correlation {v:+.3f}")
        ranked = sorted(scores.items(), key=lambda kv: -kv[1])
        best, second = ranked[0], ranked[1]
        if best[1] < 0.5:
            print("  INCONCLUSIVE: the map does not follow the WCS distortion at all")
            all_pass = False
        elif best[0] == "as is" and best[1] > 0.9 and best[1] - second[1] >= 0.1:
            print("  PASS: map pixel (x, y) is IMAGE pixel (x, y)")
        else:
            print(f"  FAIL: best match is '{best[0]}' ({best[1]:+.3f}), not 'as is'")
            all_pass = False
    print("\nRESULT:", "PASS" if all_pass else "CHECK THE LINES ABOVE")


if __name__ == "__main__":
    asyncio.run(main([int(a) for a in sys.argv[1:]] or [1, 4]))
