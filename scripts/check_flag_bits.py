"""G3: do the undefined FLAGS bits (4, 5, 29, and 3, 8, 13, 16, 18, 30, 31) matter?

  python scripts/check_flag_bits.py              # 2 frames per detector, spread over the survey
  python scripts/check_flag_bits.py --n 4

Reads whole frames from S3 (about 65 MB each) and, for each undefined bit, prints how many frames
have it set, the fraction of pixels, how much of it overlaps pixels already fatal for another
reason, and the median cleaned-looking IMAGE value (pixel minus frame median) of the pixels
where it is the ONLY fatal reason, against the unflagged sky. If a bit is rare or its pixels look
like sky, making it non-fatal costs nothing; if it is common and bright or dark, keep it fatal.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from band_vs_geometry import load_frames, pick_ids  # noqa: E402

from backend.adapters.s3_stamps import S3StampSource  # noqa: E402
from backend.pipeline.clean import FATAL_MASK  # noqa: E402
from contracts import SkyPosition  # noqa: E402

UNDEFINED = (3, 4, 5, 8, 13, 16, 18, 29, 30, 31)


def measure(st) -> dict:
    flags = np.asarray(st.flags).astype("int64") & 0xFFFFFFFF
    img = np.asarray(st.image, dtype="float64")
    ok = np.isfinite(img)
    sky = float(np.nanmedian(img[ok & ((flags & FATAL_MASK) == 0)]))
    mad = float(np.nanmedian(np.abs(img[ok] - sky))) * 1.4826 or 1.0
    out = {}
    for b in UNDEFINED:
        m = ((flags >> b) & 1).astype(bool)
        n = int(m.sum())
        other = FATAL_MASK & ~(1 << b)
        alone = m & ((flags & other) == 0) & ok
        out[b] = {"n": n, "frac": n / flags.size, "alone": int(alone.sum()),
                  "dev": float(np.median((img[alone] - sky) / mad)) if alone.any() else np.nan}
    return out


async def run(args) -> None:
    files = [str(p) for p in sorted(Path(args.catalogue).glob("*.parquet"))]
    src, sem = S3StampSource(), asyncio.Semaphore(3)
    res = []
    try:
        for det in range(1, 7):
            for frame, ra_c, dec_c in load_frames(files, pick_ids(files, det, args.n, 0, 1e9)):
                async with sem:
                    try:
                        st = await src.fetch(frame, SkyPosition(ra=ra_c % 360.0, dec=dec_c), 1020)
                    except Exception as e:
                        print(f"{frame.frame_id}: {type(e).__name__}: {str(e)[:80]}")
                        continue
                res.append(await asyncio.to_thread(measure, st))
                print(f"D{det} {frame.frame_id} read", flush=True)
    finally:
        await src.aclose()
    print(f"\n{len(res)} frames")
    print(f"{'bit':>3} {'frames set':>10} {'median frac':>12} {'max frac':>9} "
          f"{'only-reason px (median)':>24} {'deviation in sigma (median)':>28}")
    for b in UNDEFINED:
        rows = [r[b] for r in res]
        setn = sum(1 for r in rows if r["n"])
        devs = [r["dev"] for r in rows if np.isfinite(r["dev"])]
        print(f"{b:>3} {setn:>10} {np.median([r['frac'] for r in rows]):12.2e} "
              f"{max(r['frac'] for r in rows):9.2e} "
              f"{int(np.median([r['alone'] for r in rows])):>24} "
              f"{(np.median(devs) if devs else float('nan')):28.2f}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--catalogue", default=".cache/catalogue")
    ap.add_argument("--n", type=int, default=2)
    asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    main()
