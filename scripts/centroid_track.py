"""G3: centroid track. Measure a slow mover's centre in every frame and fit its motion.

  python scripts/centroid_track.py                      # Epsilon Indi B, every 2nd frame
  python scripts/centroid_track.py --target eps_indi_b --every 1 --min-snr 8

For each catalogue frame that covers the target: read a small stamp, clean it, find the
star's centre near the position predicted from its known proper motion (find_centroid), turn
the detector pixel into sky with the frame's own WCS, and take the offset in arcsec from the
mid-survey position. Then fit east and north against time (centroid_track.fit_motion) and
compare the fitted speed with the catalogue proper motion. Prints how many frames gave a
centre and how many were refused (and why), the fitted motion with its errors, and the
catalogue motion. CSV: .cache/inspect/centroid_<target>.csv.

Honest limits: this is ABSOLUTE astrometry. Any frame-to-frame pointing error of the WCS goes
straight into the scatter (the fit's errors include it); no reference stars are subtracted.
A significance under 3 means "motion not detected", not "no motion".
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import math
import sys
from pathlib import Path

import numpy as np

from backend.adapters.catalogue_index import CatalogueFrameIndex
from backend.adapters.s3_stamps import S3StampSource
from backend.adapters.wcs_util import celestial_wcs
from backend.pipeline.astrometry import find_centroid
from backend.pipeline.centroid_track import fit_motion
from backend.pipeline.clean import clean_stamp
from backend.pipeline.targets import SURVEY_MID_MJD, TARGETS, position_at
from contracts import SkyPosition

HALF = 24
MAX_SHIFT = 2.0     # pixels between the centre and the prediction


async def run(args) -> None:
    key = args.target
    ra0, dec0 = position_at(key, SURVEY_MID_MJD)
    tgt = TARGETS[key]
    index = CatalogueFrameIndex(args.catalogue)
    frames = sorted(await index.frames_at(SkyPosition(ra=ra0, dec=dec0), margin_px=HALF + 8),
                    key=lambda f: f.t_start)[::args.every]
    print(f"{key}: {len(frames)} frames")
    src, sem = S3StampSource(), asyncio.Semaphore(16)
    refused: dict[str, int] = {}
    rows = []

    async def one(frame):
        ra, dec = position_at(key, frame.t_mid)
        async with sem:
            try:
                st = await src.fetch(frame, SkyPosition(ra=ra, dec=dec), HALF)
            except Exception as e:
                return None, type(e).__name__
        w = celestial_wcs(frame.wcs_header)
        px, py = w.all_world2pix([[ra % 360.0, dec]], 0, quiet=True)[0]
        c = clean_stamp(st, fill=False)
        cen = find_centroid(c.image, px - st.x0, py - st.y0, c.noise, min_snr=args.min_snr)
        if cen is None:
            return None, "no centre"
        if math.hypot(cen.x - (px - st.x0), cen.y - (py - st.y0)) > MAX_SHIFT:
            return None, "too far from prediction"
        r, d = w.all_pix2world([[cen.x + st.x0, cen.y + st.y0]], 0)[0]
        east = (r - ra0 + 180) % 360 - 180
        east *= math.cos(math.radians(dec0)) * 3600.0
        return (frame, float(east), float((d - dec0) * 3600.0), cen.snr), None

    try:
        for res, why in await asyncio.gather(*[one(f) for f in frames]):
            if res is None:
                refused[why] = refused.get(why, 0) + 1
            else:
                rows.append(res)
    finally:
        await src.aclose()
    print(f"centres found in {len(rows)} of {len(frames)} frames; refused: {refused}")
    if not rows:
        return
    rows.sort(key=lambda r: r[0].t_mid)
    out = Path(".cache/inspect")
    out.mkdir(parents=True, exist_ok=True)
    with open(out / f"centroid_{key}.csv", "w", newline="") as fh:
        wr = csv.writer(fh)
        wr.writerow(["frame", "detector", "mjd", "east_arcsec", "north_arcsec", "snr"])
        for f, e, n, s in rows:
            wr.writerow([f.frame_id, f.detector, f"{f.t_mid:.4f}", f"{e:.3f}", f"{n:.3f}",
                         f"{s:.1f}"])
    t = np.array([r[0].t_mid for r in rows])
    fit = fit_motion(t, [r[1] for r in rows], [r[2] for r in rows], SURVEY_MID_MJD)
    if fit is None:
        print("too few centres to fit")
        return
    print(f"kept {fit.n_used} of {fit.n_total} after 3-sigma clipping; scatter about the line: "
          f"{fit.rms_east:.2f} arcsec east, {fit.rms_north:.2f} arcsec north")
    print(f"fitted motion: {fit.pm_east:+.2f} +- {fit.pm_east_err:.2f} arcsec/yr east, "
          f"{fit.pm_north:+.2f} +- {fit.pm_north_err:.2f} north "
          f"(speed {fit.speed:.2f}, {fit.significance:.1f} sigma)")
    print(f"catalogue:     {tgt.pmra / 1000:+.2f} arcsec/yr east, {tgt.pmdec / 1000:+.2f} north")
    print(f"offset at mid-survey: {fit.east0:+.2f} east, {fit.north0:+.2f} north arcsec "
          "from the predicted position")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--catalogue", default=".cache/catalogue")
    ap.add_argument("--target", default="eps_indi_b", choices=sorted(TARGETS))
    ap.add_argument("--every", type=int, default=2)
    ap.add_argument("--min-snr", type=float, default=8.0)
    asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
