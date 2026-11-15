"""Is the wavelength table the same for every frame of one detector and pipeline version?

  python scripts/check_wavelength.py [PARQUET_GLOB]     # default .cache/catalogue_targets/*.parquet

TableWavelength reads the table once per (detector, version) and reuses it. That is only right if
the table does not depend on the exposure, and cheaper still if it does not depend on the version.
Three checks, all at the same 40 random pixels, differences in um (one resolution element is about
0.02 um, the limit here is 0.5 nm = 5e-4 um):
  1. within a (detector, version) group: up to 4 frames' OWN tables against the first one;
  2. across versions of one detector: one table from each version family (v20, v24, v27, ...);
  3. how many groups were compared, and how many were skipped for having a single frame.
It ends with a recommendation: one table per detector, one per (detector, version family), one
per (detector, version), or one per frame.

Network hiccups are retried (4 tries per read). A group that still cannot be read is listed at
the end and left out of the verdict, which is then marked INCOMPLETE: rerun to retry.
"""
import random
import sys
import time
from collections import defaultdict

import duckdb
import numpy as np

from backend.adapters.pooled_source import PooledRangeSource, make_client
from backend.adapters.s3_stamps import https_url
from backend.adapters.wavelength import load_spectral_wcs

LIMIT = 5e-4
GLOB = sys.argv[1] if len(sys.argv) > 1 else ".cache/catalogue_targets/*.parquet"
rows = duckdb.sql(f"SELECT uri, detector, version FROM read_parquet('{GLOB}')").fetchall()
groups = defaultdict(list)
for uri, det, ver in rows:
    groups[(det, ver)].append(uri)
random.seed(1)
rng = np.random.default_rng(1)
xs, ys = rng.integers(0, 2040, 40), rng.integers(0, 2040, 40)
client = make_client(4)
failed: list[str] = []


def lam(uri):
    for attempt in range(4):
        try:
            return load_spectral_wcs(PooledRangeSource(https_url(uri), client)).at(xs, ys)[0]
        except ValueError:
            raise              # the file itself is unreadable, retrying cannot help
        except Exception:
            if attempt == 3:
                raise
            time.sleep(1.0 * (attempt + 1))


def family(version):
    return version.split("-")[0]


print(f"{len(groups)} (detector, version) groups in {len(rows)} frames\n")
print("1. within a group")
compared = skipped = 0
worst_within, ok_within = 0.0, True
within = []
for (det, ver), uris in sorted(groups.items()):
    if len(uris) < 2:
        skipped += 1
        continue
    picks = random.sample(uris, min(4, len(uris)))
    try:
        lams = [lam(u) for u in picks]
    except Exception as e:
        failed.append(f"D{det} {ver}")
        print(f"   D{det} {ver:<14} COULD NOT READ ({type(e).__name__}: {str(e)[:80]}), left out")
        continue
    d = max(float(np.abs(x - lams[0]).max()) for x in lams[1:])
    compared += 1
    within.append((d, det, ver))
    worst_within = max(worst_within, d)
    ok_within &= d < LIMIT
    print(f"   D{det} {ver:<14} {len(uris):>5} frames, {len(picks)} compared, max diff {d:.2e} um")
print(f"   compared {compared} groups, skipped {skipped} single-frame groups; "
      f"worst {worst_within:.2e} um")
for d, det, ver in sorted(within, reverse=True)[:3]:
    print(f"   worst groups: D{det} {ver} {d:.2e} um")
print()

print("2. across versions of the same detector (one table per version family)")
fams = defaultdict(lambda: defaultdict(list))
for (det, ver), uris in groups.items():
    fams[det][family(ver)].append(uris[0])
worst_across, ok_across, pairs = 0.0, True, 0
for det, byfam in sorted(fams.items()):
    tables = {}
    for f in sorted(byfam):
        try:
            tables[f] = lam(byfam[f][0])
        except Exception as e:
            failed.append(f"D{det} {f} (across-version table)")
            print(f"   D{det} {f} COULD NOT READ ({type(e).__name__}: {str(e)[:80]}), left out")
    names = sorted(tables)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            d = float(np.abs(tables[a] - tables[b]).max())
            pairs += 1
            worst_across = max(worst_across, d)
            ok_across &= d < LIMIT
            print(f"   D{det} {a} vs {b}: max diff {d:.2e} um")
print(f"   {pairs} pairs compared; worst {worst_across:.2e} um\n")

if failed:
    print(f"WARNING: {len(failed)} read(s) failed and were left out: {failed}")
if compared == 0 or pairs == 0:
    advice = "NOT ENOUGH DATA: no group or no version pair could be compared"
elif ok_within and ok_across:
    advice = "one table per DETECTOR is enough (key on detector only)"
elif ok_within:
    advice = "key on (detector, version FAMILY, e.g. v27), or keep (detector, version)"
else:
    advice = "the table changes between exposures: read it per frame"
if failed:
    advice += f" [INCOMPLETE: {len(failed)} read(s) failed, rerun to retry]"
print(f"RESULT: within groups {'PASS' if ok_within else 'FAIL'} ({compared} compared, "
      f"{skipped} skipped), across versions {'PASS' if ok_across else 'FAIL'} ({pairs} pairs)")
print("ADVICE:", advice)
