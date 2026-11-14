"""Check the stamp reader against astropy on REAL frames: one per pipeline layout.

  python scripts/check_real_frames.py                  # a v20, a v24 and a v27 frame from the
                                                       # target catalogue (about 72 MB each)
  python scripts/check_real_frames.py KEY [KEY ...]    # bucket keys, e.g. qr3/level2/.../x.fits

For each frame it walks the headers (the same code the catalogue builder uses), reads a 47 x 47
stamp at the frame centre and at an off-centre pixel through S3StampSource, downloads the whole
file, and compares IMAGE, VARIANCE, ZODI and FLAGS with astropy. PASS needs every plane to match
on every frame. The count of non-zero flag pixels is printed so a match is not vacuous.
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import duckdb
import numpy as np
from astropy.io import fits

from backend.adapters.frame_layout import spec_from_list
from backend.adapters.s3_stamps import S3StampSource
from backend.adapters.wcs_util import celestial_wcs
from backend.offline.build_catalogue import S3_HTTPS, read_frame_record
from contracts import FrameRef, Plane, SkyPosition

HALF = 23
GLOB = ".cache/catalogue_targets/*.parquet"


def default_keys() -> list[str]:
    keys = []
    for prefix in ("v20", "v24", "v27"):
        rows = duckdb.sql(f"SELECT uri FROM read_parquet('{GLOB}') WHERE version LIKE '{prefix}%' "
                          "LIMIT 1").fetchall()
        if rows:
            keys.append(rows[0][0].removeprefix("s3://nasa-irsa-spherex/"))
        else:
            print(f"(no {prefix} frame in the target catalogue, skipping)")
    return keys


async def check_frame(key: str, base_url: str = S3_HTTPS) -> bool:
    url = base_url + key
    t = time.monotonic()
    rec = read_frame_record({"uri": key, "t0": 0.0, "t1": 0.0})
    planes = {Plane(k): spec_from_list(v) for k, v in json.loads(rec["planes_json"]).items()}
    print(f"\n{key.rsplit('/', 1)[1]}\n  header walk {time.monotonic() - t:.2f} s, "
          f"FLAGS: {'RICE_1 compressed' if planes[Plane.FLAGS].compression else 'plain int32'}, "
          f"data starts {[planes[p].offset for p in Plane]}")
    frame = FrameRef(frame_id=key, detector=rec["detector"], t_start=0.0, t_end=0.0,
                     release=rec["release"], version=rec["version"], uri=url,
                     wcs_header=json.loads(rec["wcs_json"]), planes=planes,
                     naxis1=rec["naxis1"], naxis2=rec["naxis2"])
    wcs = celestial_wcs(frame.wcs_header)
    spots = {"centre": (frame.naxis1 / 2, frame.naxis2 / 2), "off-centre": (400.0, 1500.0)}

    src = S3StampSource()
    stamps = {}
    for label, (px, py) in spots.items():
        ra, dec = (float(v) for v in wcs.all_pix2world([[px, py]], 0)[0])
        t = time.monotonic()
        before = (src.requests, src.bytes_read)
        stamps[label] = await src.fetch(frame, SkyPosition(ra=ra % 360, dec=dec), HALF)
        print(f"  {label}: {src.requests - before[0]} requests, {src.bytes_read - before[1]:,} "
              f"bytes, {time.monotonic() - t:.2f} s")
    await src.aclose()

    print("  downloading the whole file to compare ...")
    ok = True
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "f.fits"
        urllib.request.urlretrieve(url, path)
        with fits.open(path) as h:
            for label, st in stamps.items():
                sl = (slice(st.y0, st.y0 + st.image.shape[0]),
                      slice(st.x0, st.x0 + st.image.shape[1]))
                mine = {"IMAGE": st.image, "VARIANCE": st.variance, "ZODI": st.zodi,
                        "FLAGS": st.flags}
                for name, arr in mine.items():
                    ref = h[name].data[sl]
                    if name == "FLAGS":
                        ref = ref.astype("int64") & 0xFFFFFFFF
                    same = np.array_equal(arr, ref.astype(arr.dtype), equal_nan=True)
                    ok &= same
                    extra = f"  ({int((arr != 0).sum())} non-zero)" if name == "FLAGS" else ""
                    print(f"  {label:<10} {name:<9} {'MATCH' if same else 'DIFFERENT'}{extra}")
    return ok


async def main(keys: list[str]) -> None:
    results = [await check_frame(k) for k in keys]
    print("\nRESULT:", "PASS" if results and all(results) else "FAIL",
          f"({sum(results)}/{len(results)} frames)")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:] or default_keys()))
