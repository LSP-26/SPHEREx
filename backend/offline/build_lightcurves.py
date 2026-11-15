"""T8b2: light curves and a rough spectrum for the demo targets, as static JSON.

For each target it takes every catalogue frame that covers it (optionally thinned), and for each
frame: moves the target to that frame's time (proper motion, targets.position_at), reads a small
stamp from S3, takes the wavelength at the target's pixel and the pixel-area patch, and runs
lightcurve_point (T8b1). Failures are counted by kind and never stop the run. Writes, under --out
(default frontend/public/lightcurves, so Vite serves it and copies it into the build):

  <key>.json   {key, name, ra, dec, note, summary, failed, points, spectrum}
  index.json   the list the source panel reads

Points that could not be measured stay in the file with flux_jy null and a reason. Saturated
targets (Barnard, R Dra) give lower limits: summary.n_lower_limit says how many.

  python -m backend.offline.build_lightcurves --limit 20            # a trial run
  python -m backend.offline.build_lightcurves --every 4             # a quarter of the frames
  python -m backend.offline.build_lightcurves --targets r_dra rw_aur
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import time
from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path

from backend.adapters.wcs_util import celestial_wcs
from backend.pipeline.lightcurve import LightCurvePoint, lightcurve_point
from backend.pipeline.targets import SURVEY_MID_MJD, TARGETS, position_at
from backend.server.targets import LABELS
from contracts import FrameRef, SkyPosition

HALF = 24               # stamp half-size, pixels: covers the 3 x r annulus and the centroid window
RESOLVING_POWER = 40.0  # spectrum bins are one resolution element wide
DEFAULT_OUT = "frontend/public/lightcurves"
NOTE = ("Indicative. Every point is one frame at its own wavelength and date. The spectrum mixes "
        "epochs, so a variable star scatters. Saturated points are lower limits.")


def target_pixel(frame: FrameRef, ra: float, dec: float) -> tuple[float, float]:
    """0-based detector pixel of a sky position on a catalogue frame (SIP included)."""
    x, y = celestial_wcs(frame.wcs_header).all_world2pix([[ra % 360.0, dec]], 0, quiet=True)[0]
    return float(x), float(y)


def thin(frames: Sequence[FrameRef], every: int = 1, limit: int | None = None) -> list[FrameRef]:
    """Every `every`-th frame in time order, then at most `limit` of them."""
    if every < 1:
        raise ValueError("every must be 1 or more")
    out = sorted(frames, key=lambda f: f.t_start)[::every]
    return out[:limit] if limit else out


def summarize(points: Sequence[LightCurvePoint]) -> dict:
    ok = [p for p in points if p.ok]
    return {"n_points": len(points), "n_ok": len(ok),
            "n_lower_limit": sum(1 for p in ok if p.lower_limit),
            "n_centroid": sum(1 for p in ok if p.centroid),
            "n_refused": len(points) - len(ok),
            "refused_reasons": dict(Counter(p.reason for p in points if p.reason))}


def spectrum(points: Sequence[LightCurvePoint], resolving: float = RESOLVING_POWER) -> list[dict]:
    """Flux against wavelength: points in log-wavelength bins one resolution element wide.
    A bin gives the median flux of its measured points and their scatter (None for one point).
    A bin with only saturated points gives their largest value, marked lower_limit."""
    width = math.log1p(1.0 / resolving)
    bins: dict[int, list[LightCurvePoint]] = {}
    for p in points:
        lam = p.lambda_um
        if not p.ok or lam is None or not math.isfinite(lam) or lam <= 0:
            continue
        bins.setdefault(math.floor(math.log(lam) / width), []).append(p)
    out = []
    for idx in sorted(bins):
        pts = bins[idx]
        lam = statistics.median(p.lambda_um for p in pts)
        solid = [p for p in pts if not p.lower_limit]
        if solid:
            fluxes = [p.flux_jy for p in solid]
            out.append({"lambda_um": lam, "flux_jy": statistics.median(fluxes),
                        "scatter_jy": statistics.stdev(fluxes) if len(fluxes) > 1 else None,
                        "n": len(solid), "lower_limit": False})
        else:
            out.append({"lambda_um": lam, "flux_jy": max(p.flux_jy for p in pts),
                        "scatter_jy": None, "n": len(pts), "lower_limit": True})
    return out


async def build_target(key: str, name: str, *, index, stamps, wavelength, solid,
                       position_fn: Callable = position_at, every: int = 1,
                       limit: int | None = None, concurrency: int = 24,
                       log: Callable[[str], None] = print,
                       on_progress: Callable[[int, int], None] | None = None) -> dict:
    """Run T8b1 over the frames of one target and return the body of its JSON file.
    index, stamps, wavelength and solid are the FrameIndex, StampSource, TableWavelength and
    SolidAngleMap (anything with the same methods works, which is how the tests run)."""
    ra0, dec0 = position_fn(key, SURVEY_MID_MJD)
    frames = thin(await index.frames_at(SkyPosition(ra=ra0, dec=dec0), margin_px=HALF + 8),
                  every, limit)
    log(f"{key}: {len(frames)} frames")
    if frames and hasattr(wavelength, "prefetch"):
        await wavelength.prefetch(frames)
    sem = asyncio.Semaphore(concurrency)
    failures: Counter = Counter()
    spent: Counter = Counter()   # summed seconds per stage
    t_start = time.perf_counter()
    done = 0

    async def one(frame: FrameRef) -> LightCurvePoint | None:
        nonlocal done
        try:
            async with sem:
                ra, dec = position_fn(key, frame.t_mid)
                t0 = time.perf_counter()
                stamp = await stamps.fetch(frame, SkyPosition(ra=ra, dec=dec), HALF)
                spent["stamp read"] += time.perf_counter() - t0
                t0 = time.perf_counter()
                try:
                    x, y = target_pixel(frame, ra, dec)
                    lam = float(wavelength.lambda_um(frame, x, y))
                except Exception:
                    lam = float("nan")
                spent["wavelength"] += time.perf_counter() - t0
                ny, nx = stamp.image.shape
                t0 = time.perf_counter()
                area = await solid.region_async(frame.release, frame.detector,
                                                stamp.x0, stamp.y0, nx, ny)
                spent["solid angle"] += time.perf_counter() - t0
                t0 = time.perf_counter()
                point = await asyncio.to_thread(lightcurve_point, stamp, ra, dec, lam, area)
                spent["measure"] += time.perf_counter() - t0
                return point
        except Exception as e:      # one bad frame must not stop the run
            failures[type(e).__name__] += 1
            return None
        finally:
            done += 1
            if on_progress:
                on_progress(done, len(frames))
            if done % 50 == 0:
                rate = done / max(time.perf_counter() - t_start, 1e-9)
                log(f"  {key}: {done} of {len(frames)} frames, {rate:.1f} per second")

    results = await asyncio.gather(*[one(f) for f in frames])
    wall = time.perf_counter() - t_start
    log(f"{key}: {len(frames)} frames in {wall:.0f} s ({len(frames) / max(wall, 1e-9):.1f} per "
        "second). Seconds summed over frames: "
        + ", ".join(f"{k} {v:.0f}" for k, v in spent.items()))
    points = sorted((p for p in results if p), key=lambda p: p.t_mjd)
    return {"key": key, "name": name, "ra": ra0, "dec": dec0, "note": NOTE,
            "summary": summarize(points), "failed": dict(failures),
            "points": [p.to_json() for p in points], "spectrum": spectrum(points)}


def update_index(out: Path, entries: list[dict]) -> None:
    """Merge entries into index.json, so --targets does not drop the other targets."""
    path = out / "index.json"
    old = json.loads(path.read_text())["lightcurves"] if path.exists() else []
    merged = {e["key"]: e for e in old}
    merged.update({e["key"]: e for e in entries})
    out.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"lightcurves": sorted(merged.values(), key=lambda e: e["key"])},
                               indent=1))


async def run(args: argparse.Namespace) -> None:
    from backend.adapters.catalogue_index import CatalogueFrameIndex
    from backend.adapters.pooled_source import PooledRangeSource, make_client
    from backend.adapters.s3_stamps import S3StampSource
    from backend.adapters.solid_angle import SolidAngleMap
    from backend.adapters.wavelength import TableWavelength

    index = CatalogueFrameIndex(args.catalogue)
    stamps = S3StampSource()
    wavelength = TableWavelength(cache_dir=".cache/wavelength")
    client = make_client(32)
    solid = SolidAngleMap(opener=lambda url: PooledRangeSource(url, client))
    out, entries = Path(args.out), []
    try:
        for key in args.targets:
            body = await build_target(key, LABELS.get(key, key), index=index, stamps=stamps,
                                      wavelength=wavelength, solid=solid, every=args.every,
                                      limit=args.limit, concurrency=args.concurrency)
            out.mkdir(parents=True, exist_ok=True)
            (out / f"{key}.json").write_text(json.dumps(body))
            s = body["summary"]
            print(f"{key}: {s['n_ok']} measured of {s['n_points']} "
                  f"({s['n_lower_limit']} saturated lower limits, {s['n_refused']} refused "
                  f"{s['refused_reasons']}), failed {body['failed']}")
            entries.append({"key": key, "name": body["name"], "file": f"{key}.json",
                            "n_points": s["n_points"], "n_ok": s["n_ok"],
                            "n_lower_limit": s["n_lower_limit"]})
    finally:
        await stamps.aclose()
        client.close()
    update_index(out, entries)
    print(f"index: {len(entries)} light curve(s) updated in {out / 'index.json'}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--catalogue", default=".cache/catalogue")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--targets", nargs="+", choices=sorted(TARGETS), default=["r_dra", "rw_aur"])
    ap.add_argument("--every", type=int, default=1, help="use every N-th frame")
    ap.add_argument("--limit", type=int, default=None, help="at most this many frames per target")
    ap.add_argument("--concurrency", type=int, default=24)
    asyncio.run(run(ap.parse_args(argv)))


if __name__ == "__main__":
    main()
