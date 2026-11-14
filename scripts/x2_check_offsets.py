"""X2: do plane offsets from small range reads reproduce the real pixels?

Usage:  python scripts/x2_check_offsets.py              # picks 3 frames from IRSA TAP
        python scripts/x2_check_offsets.py URL [URL...]   # or your own S3 URLs
Downloads each whole file once (about 72 MB) to compare against astropy, then deletes it.
"""
from __future__ import annotations

import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
from astropy.io import fits

from backend.adapters.fits_layout import HttpRangeSource, read_layout, read_rows

TAP = "https://irsa.ipac.caltech.edu/TAP/sync"
S3 = "https://nasa-irsa-spherex.s3.us-east-1.amazonaws.com/"
PLANES = ["IMAGE", "FLAGS", "VARIANCE", "ZODI"]
Y0, Y1 = 1000, 1047   # a 47-row band


def pick_urls(n: int) -> list[str]:
    q = urllib.parse.urlencode({"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "csv",
                                "QUERY": f"SELECT TOP {n} a.uri FROM spherex.artifact a"})
    with urllib.request.urlopen(TAP, q.encode(), timeout=120) as r:
        lines = r.read().decode().strip().splitlines()[1:]
    return [S3 + ln.strip().strip('"').removeprefix("ibe/data/spherex/") for ln in lines]


def check(url: str) -> bool:
    print(f"\n=== {url.rsplit('/', 1)[1]}")
    src = HttpRangeSource(url)
    t = time.monotonic()
    layout = read_layout(src)
    print(f"read layout in {time.monotonic() - t:.2f} s with {src.requests} range requests, "
          f"{src.bytes_read:,} bytes (file is {src.size:,})")
    print(f"{'#':>2} {'EXTNAME':<10} {'XTENSION':<9} {'BITPIX':>6} {'shape':<16} "
          f"{'BZERO':>12} {'BSCALE':>7} {'data_start':>11}")
    for h in layout:
        print(f"{h.index:>2} {str(h.extname):<10} {str(h.xtension):<9} {h.bitpix:>6} "
              f"{str(h.shape):<16} {h.bzero:>12.0f} {h.bscale:>7g} {h.data_start:>11,}"
              + ("  COMPRESSED" if h.compressed else ""))
    by_name = {h.extname: h for h in layout}
    missing = [p for p in PLANES if p not in by_name]
    if missing:
        print(f"MISSING planes: {missing}")
        return False

    t = time.monotonic()
    bands, nbytes = {}, 0
    for p in PLANES:
        bands[p] = read_rows(src, by_name[p], Y0, Y1)
        nbytes += (Y1 - Y0) * by_name[p].row_bytes
    print(f"band rows {Y0}:{Y1} of 4 planes: {nbytes:,} bytes in {time.monotonic() - t:.2f} s")

    print(f"downloading {src.size / 1e6:.1f} MB to compare with astropy ...")
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "frame.fits"
        urllib.request.urlretrieve(url, path)
        ok = True
        with fits.open(path) as hdul:
            for p in PLANES:
                ref = np.asarray(hdul[p].data[Y0:Y1], dtype="float64")
                same = np.array_equal(bands[p], ref, equal_nan=True)
                ok &= same
                print(f"  {p:<9} {'MATCH' if same else 'DIFFERENT'}")
    return ok


def main() -> None:
    urls = sys.argv[1:] or pick_urls(3)
    results = [check(u) for u in urls]
    print("\nRESULT:", "PASS" if all(results) else "FAIL", f"({sum(results)}/{len(results)} frames)")


if __name__ == "__main__":
    main()
