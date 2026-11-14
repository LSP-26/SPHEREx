"""V1.4 on the stopgap catalogue: how are the frames over each demo target spread in time?

  python scripts/gap_histogram.py
For each target: how many frames cover it, how long the lookup took, the visit gap that
choose_gap picked and how many visits that gives, then a count of the gaps between
consecutive frames in bins of days. A clean visit structure shows up as a big step between
a crowd of tiny gaps and a crowd of long ones, with little in between.
"""
import asyncio
import time

from backend.adapters.catalogue_index import CatalogueFrameIndex
from backend.offline.target_catalogue import TARGETS
from backend.pipeline.select import choose_gap, cluster_visits
from contracts import SkyPosition

EDGES = [0.0, 0.01, 0.1, 0.5, 1, 2, 5, 10, 20, 40, 80, 1e9]


async def main(catalogue: str) -> None:
    idx = CatalogueFrameIndex(catalogue)
    for name, (ra, dec) in TARGETS.items():
        t = time.monotonic()
        fs = await idx.frames_at(SkyPosition(ra=ra, dec=dec))
        lookup = time.monotonic() - t
        ts = sorted(f.t_start for f in fs)
        gaps = [b - a for a, b in zip(ts, ts[1:])]
        visits = cluster_visits(fs)
        print(f"\n{name}: {len(fs)} frames, lookup {lookup:.2f} s, "
              f"span {ts[-1] - ts[0]:.0f} d, gap chosen {choose_gap(fs):.1f} d, "
              f"{len(visits)} visits")
        cells = []
        for lo, hi in zip(EDGES, EDGES[1:]):
            n = sum(lo <= g < hi for g in gaps)
            cells.append(f"{'>' + format(lo, 'g') if hi > 1e8 else format(lo, 'g') + '-' + format(hi, 'g')}:{n}")
        print("  gaps in days  " + "  ".join(cells))
        print("  largest gaps  " + ", ".join(f"{g:.1f}" for g in sorted(gaps)[-8:]))


if __name__ == "__main__":
    asyncio.run(main(".cache/catalogue_targets"))
