"""Does the fast celestial_wcs agree with astropy's own parser on REAL catalogue frames?

  python scripts/check_fast_wcs.py                       # first 300 R Dra frames
  python scripts/check_fast_wcs.py PATH.parquet 1000
Reports the worst disagreement in detector pixels (sky positions at the corners and centre,
then back again) and the time per WCS. PASS needs under 0.001 px and no fallbacks.
"""
import json
import sys
import time

import duckdb
import numpy as np

from backend.adapters import wcs_util
from backend.adapters.wcs_util import celestial_wcs, celestial_wcs_reference

PX_DEG = 6.15 / 3600
PIXELS = np.array([[0.0, 0.0], [2039.0, 0.0], [0.0, 2039.0], [2039.0, 2039.0],
                   [1019.0, 1019.0], [300.0, 1700.0], [1700.0, 300.0]])


def main(path: str, n: int) -> None:
    rows = duckdb.read_parquet(path).project("frame_id, wcs_json").limit(n).fetchall()
    t_fast = t_ref = 0.0
    worst_sky = worst_pix = 0.0
    for _, text in rows:
        h = json.loads(text)
        t = time.perf_counter()
        fast = celestial_wcs(h)
        t_fast += time.perf_counter() - t
        t = time.perf_counter()
        ref = celestial_wcs_reference(h)
        t_ref += time.perf_counter() - t
        a, b = fast.all_pix2world(PIXELS, 0), ref.all_pix2world(PIXELS, 0)
        dra = ((a[:, 0] - b[:, 0] + 180) % 360 - 180) * np.cos(np.radians(b[:, 1]))
        worst_sky = max(worst_sky, float(np.abs(np.c_[dra, a[:, 1] - b[:, 1]]).max()) / PX_DEG)
        worst_pix = max(worst_pix,
                        float(np.abs(fast.all_world2pix(b, 0) - ref.all_world2pix(b, 0)).max()))
    ok = max(worst_sky, worst_pix) < 1e-3 and wcs_util.STATS["fallbacks"] == 0
    print(f"{len(rows)} frames from {path}")
    print(f"time per WCS: fast {1000 * t_fast / len(rows):.2f} ms, "
          f"reference {1000 * t_ref / len(rows):.2f} ms")
    print(f"worst pix2world disagreement: {worst_sky:.2e} px")
    print(f"worst world2pix disagreement: {worst_pix:.2e} px")
    print(f"fallbacks to the slow path: {wcs_util.STATS['fallbacks']}")
    print("RESULT:", "PASS" if ok else "FAIL")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".cache/catalogue_targets/targets_r_dra.parquet",
         int(sys.argv[2]) if len(sys.argv) > 2 else 300)
