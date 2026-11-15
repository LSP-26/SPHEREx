"""Do the light-curve JSON files hold physical numbers?

  python scripts/check_lightcurve_json.py [DIR]             (default frontend/public/lightcurves)
  python scripts/check_lightcurve_json.py --dedupe          also removes repeated flag texts

Per file and detector: points, flux min, median and max (Jy). Then how many fluxes are at or
below 0, how many points have no wavelength, and how many carry the same flag text twice (the
bug fixed in photometry.py: old files have it, --dedupe repairs them without a rebuild).
"""
import json
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

args = [a for a in sys.argv[1:] if not a.startswith("--")]
FIX = "--dedupe" in sys.argv
DIR = Path(args[0] if args else "frontend/public/lightcurves")

for path in sorted(DIR.glob("*.json")):
    if path.name == "index.json":
        continue
    d = json.loads(path.read_text())
    pts = d["points"]
    done = [p for p in pts if p["flux_jy"] is not None]
    print(f"\n{path.name}: {len(pts)} points, {len(done)} measured, "
          f"{sum(p['lower_limit'] for p in done)} lower limits")
    by_det = defaultdict(list)
    for p in done:
        by_det[p["detector"]].append(p["flux_jy"])
    for k in sorted(by_det):
        v = by_det[k]
        print(f"  D{k}: n={len(v):>5}  min {min(v):9.3g}  median {st.median(v):9.3g}"
              f"  max {max(v):9.3g} Jy")
    print(f"  flux <= 0: {sum(p['flux_jy'] <= 0 for p in done)}")
    print(f"  no wavelength: {sum(p['lambda_um'] is None for p in pts)}")
    dups = sum(len(set(p["flags"])) != len(p["flags"]) for p in pts)
    print(f"  points with a repeated flag text: {dups}")
    if FIX and dups:
        for p in pts:
            p["flags"] = list(dict.fromkeys(p["flags"]))
        path.write_text(json.dumps(d))
        print("  -> repeated flags removed, file rewritten")
