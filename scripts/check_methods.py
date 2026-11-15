"""Count light-curve points by measurement method; --backfill adds `method` to old files.

  python scripts/check_methods.py [--backfill] [DIR]
"""
import json
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

args = [a for a in sys.argv[1:] if not a.startswith("--")]
FIX = "--backfill" in sys.argv
DIR = Path(args[0] if args else "frontend/public/lightcurves")

for path in sorted(DIR.glob("*.json")):
    if path.name == "index.json":
        continue
    d = json.loads(path.read_text())
    g = defaultdict(list)
    for p in d["points"]:
        if "method" not in p:
            big = any(f.startswith("large aperture") for f in p["flags"])
            p["method"] = "large_aperture" if big else "aperture"
        if p["flux_jy"] is not None:
            g[(p["detector"], p["method"])].append(p["flux_jy"])
    print(path.name)
    for (det, m), v in sorted(g.items()):
        print(f"  D{det} {m:<15} n={len(v):5d}  median {st.median(v):9.3g}  max {max(v):9.3g} Jy")
    if FIX:
        path.write_text(json.dumps(d))
        print("  -> method written")
