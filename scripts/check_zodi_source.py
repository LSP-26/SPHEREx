"""T9: does subtracting ZODI help, and should SOURCE-flagged pixels stay out of the plane fit?

  python scripts/check_zodi_source.py [CATALOGUE_DIR]      (default .cache/catalogue)

At 6 positions from the ecliptic plane to the pole, reads 301 px stamps from up to 12 frames each
and cleans every stamp three ways: zodi on (today), zodi off, zodi on + SOURCE pixels left out of
the fit. Noise is the robust sigma of the cleaned image measured on the SAME pixels for all three,
so the numbers can be compared. Lower is better.
"""
import asyncio
import sys
from collections import defaultdict

import numpy as np
from astropy import units as u
from astropy.coordinates import BarycentricMeanEcliptic, SkyCoord

from backend.adapters.catalogue_index import CatalogueFrameIndex
from backend.adapters.s3_stamps import S3StampSource
from backend.pipeline.clean import FATAL_MASK, clean_arrays, robust_sigma
from backend.pipeline.select import even_subsample
from contracts import SkyPosition

CAT = sys.argv[1] if len(sys.argv) > 1 else ".cache/catalogue"
LATS = [0, 20, 40, 60, 80, 88]
HALF, PER_POS = 150, 12
SOURCE = 1 << 21


async def main() -> None:
    index = CatalogueFrameIndex(CAT)
    src = S3StampSource()
    rows = defaultdict(list)
    for lat in LATS:
        c = SkyCoord(lon=100 * u.deg, lat=lat * u.deg, frame=BarycentricMeanEcliptic).icrs
        pos = SkyPosition(ra=float(c.ra.deg), dec=float(c.dec.deg))
        frames = await index.frames_at(pos, margin_px=HALF + 5)
        picks = even_subsample(sorted(frames, key=lambda f: f.t_start), PER_POS)
        print(f"ecliptic lat {lat}: {len(frames)} frames, reading {len(picks)}", flush=True)
        for f in picks:
            try:
                st = await src.fetch(f, pos, HALF)
            except Exception as e:
                print(f"  skip {f.frame_id}: {type(e).__name__}")
                continue
            a = clean_arrays(st.image, st.flags, st.variance, st.zodi, use_zodi=True)
            b = clean_arrays(st.image, st.flags, st.variance, st.zodi, use_zodi=False)
            s = clean_arrays(st.image, st.flags, st.variance, st.zodi, use_zodi=True,
                             fatal_mask=FATAL_MASK | SOURCE)
            common = a.good & b.good & s.good
            if common.sum() < 500:
                continue
            n = [robust_sigma(x.image[common]) for x in (a, b, s)]
            rows[lat].append((n[0], n[1], n[2], float((~s.good).mean())))
    await src.aclose()

    print("\nlat  frames  noise(zodi on)  noise(zodi off)  noise(zodi on, SOURCE out)  extra masked")
    tot = []
    for lat in LATS:
        r = np.array(rows[lat])
        if not len(r):
            print(f"{lat:>3}  no usable frames")
            continue
        m = np.median(r, axis=0)
        tot.append(r)
        print(f"{lat:>3}  {len(r):>6}  {m[0]:>14.4g}  {m[1]:>15.4g}  {m[2]:>26.4g}  {m[3]:>11.0%}")
    if not tot:
        sys.exit("nothing measured")
    all_ = np.vstack(tot)
    z = np.median(all_[:, 1] / all_[:, 0])
    s = np.median(all_[:, 2] / all_[:, 0])
    print(f"\nmedian noise ratio, zodi off / zodi on: {z:.3f}")
    print(f"median noise ratio, SOURCE out / today: {s:.3f}")
    print("ZODI:", "KEEP use_zodi=True (subtracting it lowers the noise)" if z > 1.03 else
          "ZODI makes little difference at this stamp size: keep the default" if z > 0.97 else
          "ZODI subtraction makes it WORSE")
    print("SOURCE:", "EXCLUDE SOURCE from the plane fit (lower noise)" if s < 0.97 else
          "leave as is (no real gain)" if s < 1.03 else "excluding SOURCE is WORSE: leave as is")

asyncio.run(main())
