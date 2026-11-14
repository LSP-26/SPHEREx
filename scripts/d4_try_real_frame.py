"""D4 check on a real frame: does the S3 stamp reader give the same pixels as astropy,
using the frame's own WCS to find the target?

Usage:  python scripts/d4_try_real_frame.py [URL]      (default: first frame from IRSA TAP)
It targets the centre of the frame, reads a 47 x 47 stamp, downloads the whole file
(about 72 MB) and compares all four planes.
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import numpy as np
from astropy.io import fits

from backend.adapters.fits_layout import HttpRangeSource, read_header, read_layout
from backend.adapters.s3_stamps import S3StampSource
from contracts import FrameRef, Plane, PlaneSpec, SkyPosition

sys.path.insert(0, str(Path(__file__).parent))
from x2_check_offsets import pick_urls  # noqa: E402

WCS_PREFIXES = ("CTYPE", "CRPIX", "CRVAL", "CD", "PC", "CDELT", "CUNIT", "A_", "B_", "AP_",
                "BP_", "LONPOLE", "LATPOLE", "RADESYS", "EQUINOX", "NAXIS")
HALF = 23


def build_frame(url: str) -> FrameRef:
    src = HttpRangeSource(url)
    layout = {h.extname: h for h in read_layout(src)}
    hdr = read_header(src, layout["IMAGE"])
    wcs = {k: hdr[k] for k in hdr if k.startswith(WCS_PREFIXES) and not isinstance(hdr[k], bool)}
    print("WCS keys:", len(wcs), "| SIP distortion present:", "A_ORDER" in wcs)
    planes = {Plane(n): PlaneSpec(offset=layout[n].data_start, bitpix=layout[n].bitpix,
                                  bzero=layout[n].bzero, bscale=layout[n].bscale)
              for n in ("IMAGE", "FLAGS", "VARIANCE", "ZODI")}
    ny, nx = layout["IMAGE"].shape
    return FrameRef(frame_id=url.rsplit("/", 1)[1], detector=1, t_start=0.0, t_end=0.0,
                    release="?", version="?", uri=url, wcs_header=wcs, planes=planes,
                    naxis1=nx, naxis2=ny)


async def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else pick_urls(1)[0]
    print("frame:", url.rsplit("/", 1)[1])
    frame = build_frame(url)
    from backend.adapters.wcs_util import celestial_wcs
    wcs = celestial_wcs(frame.wcs_header)
    cx, cy = (frame.naxis1 - 1) / 2, (frame.naxis2 - 1) / 2
    ra, dec = (float(v) for v in wcs.all_pix2world([[cx, cy]], 0)[0])
    print(f"frame centre pixel ({cx:.0f}, {cy:.0f}) is RA {ra:.5f}, Dec {dec:.5f}")

    src = S3StampSource()
    t = time.monotonic()
    st = await src.fetch(frame, SkyPosition(ra=ra % 360, dec=dec), HALF)
    print(f"stamp {st.image.shape} at x0={st.x0}, y0={st.y0}: {src.requests} requests, "
          f"{src.bytes_read:,} bytes, {time.monotonic() - t:.2f} s")
    await src.aclose()

    print("downloading the whole file to compare ...")
    ok = True
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "f.fits"
        urllib.request.urlretrieve(url, path)
        with fits.open(path) as h:
            sl = (slice(st.y0, st.y0 + st.image.shape[0]), slice(st.x0, st.x0 + st.image.shape[1]))
            mine = {"IMAGE": st.image, "VARIANCE": st.variance, "ZODI": st.zodi,
                    "FLAGS": st.flags}
            for name, arr in mine.items():
                ref = h[name].data[sl]
                if name == "FLAGS":
                    ref = ref.astype("int64") & 0xFFFFFFFF
                same = np.array_equal(arr, ref.astype(arr.dtype), equal_nan=True)
                ok &= same
                print(f"  {name:<9} {'MATCH' if same else 'DIFFERENT'}")
    print("RESULT:", "PASS" if ok else "FAIL")


if __name__ == "__main__":
    asyncio.run(main())
