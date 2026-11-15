"""D1.3 and V1.4: does our catalogue agree with IRSA, and how do visits look at different
ecliptic latitudes?

  python scripts/check_catalogue_vs_tap.py [CATALOGUE_DIR]      (default .cache/catalogue)

For 10 positions from the ecliptic plane to both poles it
  1. asks IRSA TAP which frames cover the position (async job, cached in .cache/d13/),
  2. asks our catalogue the same, with no edge margin,
  3. compares the two lists, only over the dates the catalogue covers,
  4. prints the visit-gap histogram and the visit count, as gap_histogram.py does.
The two lists can differ at the frame edge, because IRSA tests a footprint polygon and we test
each frame's own WCS. A disagreement within EDGE_PX pixels of an edge is normal. What matters:
a frame IRSA lists that we do not have (and that did not fail during the build), or a
disagreement far from any edge. The report is also saved to .cache/d13/report.md.
"""
from __future__ import annotations

import asyncio
import json
import math
import sys
from pathlib import Path

import duckdb
from astropy import units as u
from astropy.coordinates import BarycentricMeanEcliptic, SkyCoord

from backend.adapters.catalogue_index import CatalogueFrameIndex
from backend.adapters.wcs_util import celestial_wcs
from backend.offline.target_catalogue import positional_rows
from backend.pipeline.select import choose_gap, cluster_visits
from contracts import SkyPosition

CATALOGUE = sys.argv[1] if len(sys.argv) > 1 else ".cache/catalogue"
OUT = Path(".cache/d13")
LATS = [0, 15, 30, 45, 60, 75, 85, -30, -60, -85]     # ecliptic latitude, degrees
EDGE_PX = 15.0
EDGES = [0.0, 0.01, 0.1, 0.5, 1, 2, 5, 10, 20, 40, 80, 1e9]
LINES: list[str] = []


def p(line: str = "") -> None:
    print(line, flush=True)
    LINES.append(line)


def positions() -> list[tuple[int, int, float, float]]:
    out = []
    for i, lat in enumerate(LATS):
        lon = (37 + 71 * i) % 360
        c = SkyCoord(lon=lon * u.deg, lat=lat * u.deg, frame=BarycentricMeanEcliptic).icrs
        out.append((lat, lon, float(c.ra.deg), float(c.dec.deg)))
    return out


def failed_names(root: str) -> set[str]:
    names: set[str] = set()
    for f in Path(root).glob("*.failed.json"):
        for item in json.loads(f.read_text()):
            names.add(item["uri"].strip().rsplit("/", 1)[-1])
    return names


def tap_names(ra: float, dec: float, lo: float, hi: float) -> set[str]:
    path = OUT / f"tap_{ra:.4f}_{dec:.4f}.json"
    if path.exists():
        rows = json.loads(path.read_text())
    else:
        rows = [[r["uri"].strip().rsplit("/", 1)[-1], float(r["t0"])]
                for r in positional_rows(ra, dec)]
        path.write_text(json.dumps(rows))
    return {name for name, t in rows if lo <= t <= hi}


def margin_px(header: dict, nx: int, ny: int, ra: float, dec: float) -> float | None:
    """Pixels from the position to the nearest frame edge (negative = off the frame)."""
    try:
        x, y = celestial_wcs(header).all_world2pix([[ra, dec]], 0, quiet=True)[0]
    except Exception:
        return None
    if not (math.isfinite(x) and math.isfinite(y)):
        return None
    return float(min(x, nx - x, y, ny - y))


def table_margin(index: CatalogueFrameIndex, name: str, ra: float, dec: float):
    """(is the frame in the catalogue, its edge margin in pixels)."""
    row = duckdb.connect().execute(
        "SELECT wcs_json, naxis1, naxis2 FROM read_parquet(?) WHERE frame_id = ?",
        [index.files, name]).fetchone()
    if row is None:
        return False, None
    return True, margin_px(json.loads(row[0]), row[1], row[2], ra, dec)


def gap_report(frames) -> None:
    ts = sorted(f.t_start for f in frames)
    if len(ts) < 2:
        p(f"   {len(ts)} frames: too few for a gap histogram")
        return
    gaps = [b - a for a, b in zip(ts, ts[1:])]
    cells = []
    for a, b in zip(EDGES, EDGES[1:]):
        label = f">{a:g}" if b > 1e8 else f"{a:g}-{b:g}"
        cells.append(f"{label}:{sum(a <= g < b for g in gaps)}")
    p(f"   {len(ts)} frames, span {ts[-1] - ts[0]:.0f} d, gap chosen {choose_gap(frames):.1f} d, "
      f"{len(cluster_visits(frames))} visits")
    p("   gaps in days  " + "  ".join(cells))
    p("   largest gaps  " + ", ".join(f"{g:.1f}" for g in sorted(gaps)[-6:]))


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    p(f"loading {CATALOGUE} (about 6 GB of RAM) ...")
    index = CatalogueFrameIndex(CATALOGUE)
    with index._lock:
        lo, hi = index._con.execute("SELECT min(t_start), max(t_start) FROM frames").fetchone()
    failed = failed_names(CATALOGUE)
    p(f"{index.n_frames} frames, MJD {lo:.1f} to {hi:.1f}, "
      f"{len(failed)} frames failed during the build\n")

    problems, compared = 0, 0
    for i, (lat, lon, ra, dec) in enumerate(positions(), start=1):
        pos = SkyPosition(ra=ra, dec=dec)
        p(f"== {i}/{len(LATS)}  ecliptic lat {lat:+d}, lon {lon}, RA {ra:.4f} Dec {dec:.4f}")
        cat = await index.frames_at(pos, margin_px=0)
        by_name = {f.frame_id: f for f in cat}
        try:
            tap = tap_names(ra, dec, lo, hi)
        except Exception as e:
            p(f"   TAP failed: {type(e).__name__}: {str(e)[:150]} (rerun to retry)")
            tap = None
        if tap is not None:
            compared += 1
            both = tap & by_name.keys()
            n_failed = n_missing = n_edge = n_far = 0
            for name in tap - by_name.keys():
                present, d = table_margin(index, name, ra, dec)
                if not present:
                    if name in failed:
                        n_failed += 1
                    else:
                        n_missing += 1
                elif d is None or d < -EDGE_PX:
                    n_far += 1
                else:
                    n_edge += 1
            c_edge = c_deep = 0
            for name in by_name.keys() - tap:
                f = by_name[name]
                d = margin_px(f.wcs_header or {}, f.naxis1, f.naxis2, ra, dec)
                if d is not None and d > EDGE_PX:
                    c_deep += 1
                else:
                    c_edge += 1
            p(f"   TAP {len(tap)}, catalogue {len(by_name)}, both {len(both)}")
            p(f"   only TAP: {n_failed} failed in build, {n_missing} MISSING, "
              f"{n_edge} at the edge, {n_far} far from the edge")
            p(f"   only catalogue: {c_edge} at the edge, {c_deep} deep inside")
            problems += n_missing + n_far + c_deep
        gap_report(await index.frames_at(pos))
        p()

    p(f"compared {compared} of {len(LATS)} positions; unexplained disagreements: {problems}")
    p("D1.3 RESULT: " + ("PASS" if compared == len(LATS) and problems == 0 else "CHECK THE LINES ABOVE"))
    (OUT / "report.md").write_text("\n".join(LINES) + "\n")
    p(f"report saved to {OUT / 'report.md'}")


if __name__ == "__main__":
    asyncio.run(main())
