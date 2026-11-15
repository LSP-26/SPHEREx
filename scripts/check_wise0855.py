"""C1: can SPHEREx see WISE 0855 at all, frame by frame and stacked, and at which wavelengths?

  python scripts/check_wise0855.py                 # every 3rd frame, full catalogue
  python scripts/check_wise0855.py --every 1 --catalogue .cache/catalogue

For every catalogue frame that covers WISE 0855 (position moved to the frame's date with its
proper motion), reads a small stamp, and measures a 2 px aperture AT THE PREDICTED POSITION with
lightcurve_point (the centroid is only used if it is bright enough, so an empty frame gives
noise, not a made-up detection). The pixel area is a constant 37.82 arcsec^2: it cancels in S/N.
Per detector it prints: frames, how many reach S/N 3 and 5, and an inverse-variance stack of
all its frames (assumes a constant flux, so it is a rough depth, not a spectrum).
It also lists the wavelengths of the S/N >= 3 frames. Needs the network (S3).
Interpretation: a stack S/N under 3 on every detector means "not detected", which is what the
limits panel already says. Record the numbers.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict

import numpy as np

from backend.adapters.catalogue_index import CatalogueFrameIndex
from backend.adapters.s3_stamps import S3StampSource
from backend.adapters.wavelength import TableWavelength
from backend.pipeline.lightcurve import lightcurve_point
from backend.pipeline.targets import SURVEY_MID_MJD, position_at
from contracts import SkyPosition

HALF = 24
AREA = 37.82


async def main(a: argparse.Namespace) -> None:
    ra0, dec0 = position_at("wise0855", SURVEY_MID_MJD)
    index = CatalogueFrameIndex(a.catalogue)
    frames = sorted(await index.frames_at(SkyPosition(ra=ra0, dec=dec0), margin_px=HALF + 8),
                    key=lambda f: f.t_start)[::a.every]
    print(f"{len(frames)} frames cover WISE 0855 (every {a.every}th)")
    wl = TableWavelength(cache_dir=".cache/wavelength")
    await wl.prefetch(frames)
    src, sem = S3StampSource(), asyncio.Semaphore(16)

    async def one(f):
        ra, dec = position_at("wise0855", f.t_mid)
        async with sem:
            try:
                st = await src.fetch(f, SkyPosition(ra=ra, dec=dec), HALF)
            except Exception as e:
                return f, None, type(e).__name__
        x, y = S3StampSource.target_pixel(f, SkyPosition(ra=ra, dec=dec))
        try:
            lam = float(wl.lambda_um(f, x, y))
        except Exception:
            lam = float("nan")
        return f, lightcurve_point(st, ra, dec, lam, AREA), None

    try:
        results = await asyncio.gather(*[one(f) for f in frames])
    finally:
        await src.aclose()

    by_det: dict[int, list] = defaultdict(list)
    failed: dict[str, int] = defaultdict(int)
    for f, p, why in results:
        if p is None:
            failed[why] += 1
        elif p.ok and not p.lower_limit and p.err_jy:
            by_det[f.detector].append(p)
        else:
            failed[p.reason or "saturated or no measurement"] += 1
    print(f"not measurable: {dict(failed)}\n")
    print(f"{'det':>3} {'n':>5} {'S/N>=3':>7} {'S/N>=5':>7} {'median err (Jy)':>16} "
          f"{'stack flux (Jy)':>16} {'stack S/N':>10}")
    for det in sorted(by_det):
        ps = by_det[det]
        snr = np.array([p.flux_jy / p.err_jy for p in ps])
        w = np.array([1 / p.err_jy ** 2 for p in ps])
        fl = np.array([p.flux_jy for p in ps])
        mean, err = float((w * fl).sum() / w.sum()), float(1 / np.sqrt(w.sum()))
        print(f"D{det:<2} {len(ps):>5} {int((snr >= 3).sum()):>7} {int((snr >= 5).sum()):>7} "
              f"{np.median([p.err_jy for p in ps]):>16.3g} {mean:>16.3g} {mean / err:>10.1f}")
        hits = sorted({round(p.lambda_um, 2) for p, s in zip(ps, snr) if s >= 3 and np.isfinite(p.lambda_um)})
        if hits:
            print(f"      wavelengths (um) of S/N >= 3 frames: {hits[:12]}")
    print("\nA stack S/N under 3 on every detector means WISE 0855 is not detected by this method.")
    print("(A motion error of a few px would also do this: check the position first with "
          "scripts/centroid_track.py --target wise0855.)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--catalogue", default=".cache/catalogue")
    ap.add_argument("--every", type=int, default=3)
    asyncio.run(main(ap.parse_args()))
