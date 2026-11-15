"""T10 on REAL frames: start the server with --refine, run Barnard's Star, count the frames.

  python scripts/check_refine_colour.py [CATALOGUE_DIR]    (default .cache/catalogue, else _targets)
"""
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

import httpx

cat = sys.argv[1] if len(sys.argv) > 1 else next(
    (d for d in (".cache/catalogue", ".cache/catalogue_targets") if list(Path(d).glob("*.parquet"))), None)
if not cat:
    sys.exit("NO CATALOGUE: neither .cache/catalogue nor .cache/catalogue_targets has shards. Tell me.")
print("catalogue:", cat)
srv = subprocess.Popen([sys.executable, "-m", "backend.server", "--source", "catalogue",
                        "--catalogue", cat, "--refine", "--port", "8123",
                        "--cache", ".cache/refine_check_cache"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
try:
    base = "http://127.0.0.1:8123"
    for _ in range(120):
        try:
            if httpx.get(base + "/health", timeout=2).status_code == 200:
                break
        except Exception:
            time.sleep(2)
    else:
        sys.exit("server did not start")
    t0 = time.time()
    h = httpx.post(base + "/flipbook?ra=269.4462&dec=4.7687&fov=5", timeout=30).json()
    events, after = [], 0
    while time.time() - t0 < 1200:
        r = httpx.get(base + h["poll_url"], params={"after": after, "wait": 20}, timeout=60).json()
        events += r["events"]
        after = r["next"]
        if r["done"]:
            break
    frames = [e for e in events if e["type"] == "frame"]
    print(f"{time.time() - t0:.0f} s, {len(events)} events, last: {events[-1]['type']}")
    print("frames by (stage, detector):", dict(Counter((f["stage"], f["label"]["detector"]) for f in frames)))
    colour = [f for f in frames if f["label"]["detector"] == 0]
    for f in colour:
        print("COLOUR", f["slot"], f["width"], "x", f["height"], "partial", f["partial"], f["label"]["flags_summary"])
        png = httpx.get(base + f["png_url"]).content
        Path(f".cache/colour_v{f['visit_index']}.png").write_bytes(png)
    print("RESULT:", "PASS" if colour else "NO COLOUR FRAMES (see counts above)")
finally:
    srv.terminate()
