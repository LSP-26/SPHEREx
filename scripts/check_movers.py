"""G3 check on REAL frames: the moving-object search (backend/jobs/movers.py).

  python scripts/check_movers.py                        # an ecliptic field (many asteroids)
  python scripts/check_movers.py --ra 180 --dec 0 --fov 10 --visit 3

1. Lists the visits of the field and runs the search on one (the busiest by default), with the
   SkyBoT check: prints every track (known name or "candidate") and every known asteroid that
   crossed the searched area, with its V magnitude and whether the search tracked it.
2. Injection: plants fake movers (a Gaussian of the measured PSF width, FWHM about 2 px) into the
   same real frames at several brightnesses and counts how many the search recovers with the
   right rate. That gives the real detection limit, in S/N per frame, on real noise and stars.
Needs the catalogue (.cache/catalogue) and the network (S3 and SkyBoT). Send the output back.
"""

from __future__ import annotations

import argparse
import asyncio
import math

import httpx
import numpy as np

from backend.adapters.skybot import skybot_cone
from backend.adapters.stubs import MemoryCache
from backend.jobs import movers as J
from backend.pipeline import movers as M
from backend.pipeline.clean import robust_sigma
from backend.pipeline.render import preview_size_px
from backend.server.deps import CatalogueDeps
from backend.server.settings import Settings
from contracts import FlipbookRequest, SkyPosition


def inject(frames: list[M.GridFrame], x0, y0, vx, vy, amp, sigma=0.9) -> list[M.GridFrame]:
    t0 = min(f.t_mjd for f in frames)
    n = frames[0].image.shape[0]
    yy, xx = np.mgrid[0:n, 0:n]
    out = []
    for f in frames:
        h = (f.t_mjd - t0) * 24
        blob = amp * np.exp(-((xx - x0 - vx * h) ** 2 + (yy - y0 - vy * h) ** 2) / (2 * sigma**2))
        out.append(M.GridFrame(f.frame_id, f.detector, f.t_mjd, f.image + blob))
    return out


async def main(a: argparse.Namespace) -> None:
    pos = SkyPosition(ra=a.ra, dec=a.dec)
    deps_f = CatalogueDeps(
        Settings(source="catalogue", catalogue_dir=a.catalogue, cache_dir=None), MemoryCache()
    )
    deps = await deps_f(FlipbookRequest(ra=a.ra, dec=a.dec, fov_arcmin=a.fov))
    client = httpx.AsyncClient()
    try:
        frames = await deps.index.frames_at(pos)
        visits = J.visits_of(frames)
        print(f"{len(frames)} frames, {len(visits)} visits at RA {a.ra} Dec {a.dec}")
        for i, v in enumerate(visits):
            d = J.describe_visit(i, v)
            print(
                f"  visit {i}: MJD {d['t_start_mjd']:.2f} to {d['t_end_mjd']:.2f}, "
                f"{d['n_frames']} frames, {d['n_bursts']} bursts, detectors {d['detectors']}"
            )

        async def sky(ra, dec, r, t):
            return await skybot_cone(client, ra, dec, r, t)

        body, _, _ = await J.find_movers(pos, a.fov, frames, deps.stamps.fetch, sky, visit=a.visit)
        print(
            f"\n1. visit {body['visit']}: {body['frames_used']} frames read, "
            f"{body['frames_searched']} searched, {len(body['bursts_mjd'])} bursts, "
            f"{body['n_detections']} single-frame detections"
        )
        for n in body["notes"]:
            print("   note:", n)
        print(f"   {len(body['tracks'])} track(s):")
        for t in body["tracks"]:
            p0 = t["points"][0]
            print(
                f"   - {t['status']:9} {t['known'] or '':22} {len(t['points'])} bursts, "
                f"{t['rate_arcsec_h']:6.1f} arcsec/h, PA {t['pa_deg']:5.1f}, rms {t['rms_px']} px, "
                f"S/N {t['snr']}, start RA {p0['ra']:.4f} Dec {p0['dec']:.4f}"
            )
        print(
            f"   known objects inside the searched area (SkyBoT checked: {body['skybot_checked']}):"
        )
        for o in body["known_objects"]:
            print(
                f"   - {o['name']:22} V {o['vmag']}  {o['rate_arcsec_h']:.0f} arcsec/h  in field for "
                f"{o['bursts_in_field']} bursts, on a track in {o['bursts_on_track']}: "
                f"{'TRACKED' if o['tracked'] else 'not tracked'}"
            )

        # 2. injection into the same frames
        vi = body["visit"]
        size = preview_size_px(min(a.fov, J.MAX_FOV_ARCMIN))
        chosen = J._thin(visits[vi], J.MAX_FRAMES)
        stamps = await asyncio.gather(
            *(deps.stamps.fetch(f, pos, size // 2) for f in chosen), return_exceptions=True
        )
        pairs = [(f, s) for f, s in zip(chosen, stamps) if not isinstance(s, BaseException)]
        grid = await asyncio.to_thread(J.to_grid_frames, pairs, pos, size, True)
        # robust (MAD) noise: a plain standard deviation is inflated by the stars, which made
        # the injected movers far brighter than their label
        noise = float(np.median([robust_sigma(f.image) for f in grid]))
        print(
            f"\n2. injection into {len(grid)} real frames ({size} px grid, "
            f"median frame noise {noise:.3g} MJy/sr)"
        )
        rng = np.random.default_rng(0)
        t_first = min(f.t_mjd for f in grid)
        bursts = sorted({round(t, 4) for t in M.group_times_means(grid)})

        def draw():
            """A path inside the field (5 px from the edge) for 3 bursts close enough in time."""
            while True:
                rate = rng.uniform(1, 8)  # px/h, main belt is about 6
                ang = rng.uniform(0, 2 * math.pi)
                vx, vy = rate * math.cos(ang), rate * math.sin(ang)
                tc = bursts[rng.integers(len(bursts))]
                cx, cy = size / 2 + rng.uniform(-size / 4, size / 4, 2)
                hc = (tc - t_first) * 24
                x0, y0 = cx - vx * hc, cy - vy * hc
                ts = [
                    t
                    for t in bursts
                    if 5 <= x0 + vx * (t - t_first) * 24 <= size - 6
                    and 5 <= y0 + vy * (t - t_first) * 24 <= size - 6
                ]
                # in view for 3 bursts with no gap over MAX_GAP_HOURS (else it cannot be found)
                if any(
                    all((ts[j + k + 1] - ts[j + k]) * 24 <= M.MAX_GAP_HOURS for k in range(2))
                    for j in range(len(ts) - 2)
                ):
                    return vx, vy, x0, y0

        for snr in (3, 5, 8, 12, 20):
            ok = 0
            for k in range(a.trials):
                vx, vy, x0, y0 = draw()
                # amp so that the 3x3 box sum is `snr` times its noise for this peak height
                amp = (
                    snr
                    * 3
                    * noise
                    / sum(
                        math.exp(-(i * i + j * j) / (2 * 0.81))
                        for i in (-1, 0, 1)
                        for j in (-1, 0, 1)
                    )
                )
                r = await asyncio.to_thread(M.search, inject(grid, x0, y0, vx, vy, amp))
                if any(abs(t.vx - vx) < 0.3 and abs(t.vy - vy) < 0.3 for t in r.tracks):
                    ok += 1
            print(f"   box S/N {snr:>2} per frame: recovered {ok} of {a.trials}")
        print("\nRESULT: see the lines above (no PASS/FAIL: this measures the limit)")
    finally:
        await client.aclose()
        await deps_f.aclose()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--ra", type=float, default=180.0)
    ap.add_argument("--dec", type=float, default=0.0)
    ap.add_argument("--fov", type=float, default=10.0)
    ap.add_argument("--visit", type=int, default=None)
    ap.add_argument("--trials", type=int, default=6)
    ap.add_argument("--catalogue", default=".cache/catalogue")
    asyncio.run(main(ap.parse_args()))
