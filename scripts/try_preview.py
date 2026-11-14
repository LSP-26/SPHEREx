"""Real-data smoke run of the preview job (INTERIM: uses the stopgap target catalogue).

Real: CatalogueFrameIndex, S3StampSource, DiskCache, make_selector.
Still stubs: the renderer (plain greyscale) and the wavelengths (a fixed value per detector),
so labels are NOT real and frames are NOT wavelength-matched until D5.1 and P3.2 land.

  python scripts/try_preview.py barnard
  python scripts/try_preview.py wise0855 --fov 10

PNGs go to .cache/preview_try/<target>/, named by slot. Delete .cache/try_preview_cache for a
cold-cache timing (a second run on the same target is served from the cache).
"""
from __future__ import annotations

import argparse
import asyncio
import time
from pathlib import Path

from backend.adapters.cache import DiskCache
from backend.adapters.catalogue_index import CatalogueFrameIndex
from backend.adapters.s3_stamps import S3StampSource
from backend.adapters.stubs import StubRenderer, StubWavelength
from backend.jobs.preview import run_preview
from backend.offline.target_catalogue import TARGETS
from backend.pipeline.select import make_selector
from contracts import DoneEvent, ErrorEvent, FlipbookRequest, FrameEvent, PlanEvent


async def main(name: str, fov: float, catalogue: str) -> None:
    ra, dec = TARGETS[name]
    index = CatalogueFrameIndex(catalogue)
    print(f"catalogue: {index.n_frames} frames loaded from {catalogue}")
    stamps = S3StampSource()
    cache = DiskCache(".cache/try_preview_cache")
    out = Path(".cache/preview_try") / name
    out.mkdir(parents=True, exist_ok=True)
    req = FlipbookRequest(ra=ra, dec=dec, fov_arcmin=fov)
    t0 = time.monotonic()
    try:
        async for ev in run_preview(req, index=index, stamps=stamps,
                                    wavelength=StubWavelength(), renderer=StubRenderer(),
                                    cache=cache, select=make_selector(None)):
            if isinstance(ev, PlanEvent):
                print(f"plan: {ev.n_slots} frames, {ev.n_visits_shown} of "
                      f"{ev.n_visits_total} visits, about {(ev.est_bytes or 0) / 1e6:.0f} MB "
                      f"(at {time.monotonic() - t0:.2f} s)")
            elif isinstance(ev, FrameEvent):
                png = await cache.get(ev.png_url.rsplit("/", 1)[1].removesuffix(".png"))
                lb = ev.label
                (out / f"{ev.slot:02d}_D{lb.detector}_mjd{lb.t_mid_mjd:.2f}.png").write_bytes(png)
                print(f"  frame slot {ev.slot:>2}  D{lb.detector}  mjd {lb.t_mid_mjd:.2f}  "
                      f"{ev.width}x{ev.height}  at {ev.elapsed_s:.2f} s")
            elif isinstance(ev, ErrorEvent):
                print(f"ERROR {ev.code.value}: {ev.message}")
            elif isinstance(ev, DoneEvent):
                print(f"done: {ev.n_frames} frames in {ev.elapsed_s:.2f} s -> {out}")
    finally:
        await stamps.aclose()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("target", choices=sorted(TARGETS))
    ap.add_argument("--fov", type=float, default=5.0, help="field of view in arcmin")
    ap.add_argument("--catalogue", default=".cache/catalogue_targets")
    a = ap.parse_args()
    asyncio.run(main(a.target, a.fov, a.catalogue))
