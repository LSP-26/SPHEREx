"""O2.1: the preview job. Yields contract events as frames finish.

Order: heartbeat (at once) -> plan -> progress(0) -> [frame, progress]... -> done.
Failures become ErrorEvents with plain-language messages, never exceptions.
Cancel by setting the `cancel` event or by closing/cancelling the generator: in-flight
fetches are cancelled either way.

Frames whose stamp is mostly undisplayable (more than MAX_MASKED_FRACTION of pixels carry a
detector-defect flag: NONFUNC, MISSING_DATA, HOT, COLD) are skipped: they show nothing. Pixels
with other fatal flags, including the dichroic strip on detectors 3 and 4, are shown, so those
frames are kept. A skipped frame still counts as done in the progress, but sends no frame
event.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import time
import uuid
from collections.abc import AsyncIterator

import numpy as np

from backend.adapters.cache import UpstreamError
from backend.pipeline.clean import DISPLAY_HIDDEN_MASK
from backend.pipeline.select import Selection, Selector, simple_select
from contracts import (
    DoneEvent,
    ErrorCode,
    ErrorEvent,
    FlipbookRequest,
    FrameEvent,
    FrameLabel,
    FrameRef,
    HeartbeatEvent,
    Orientation,
    PlanEvent,
    ProgressEvent,
    SkyPosition,
    Stage,
    ViewMode,
    cache_key,
)
from contracts.interfaces import (
    Cache,
    FrameIndex,
    Renderer,
    StampSource,
    WavelengthSource,
)

log = logging.getLogger(__name__)

PIXEL_SCALE_ARCSEC = 6.15
BYTES_PER_ROW_PLANE = 2040 * 4
PLANES = 4  # IMAGE, FLAGS, VARIANCE, ZODI
MAX_MASKED_FRACTION = 0.5   # frames with more undisplayable pixels than this are skipped
RENDER_VERSION = 11   # part of every cache key: bump it whenever render.py changes the pixels
#   (1 = native orientation, 2 = regridded north-up and flipped,
#   3 = saturated label needs 5 pixels, 4 = saturated pixels drawn white,
#   5 = masked pixels far above the white point drawn white too,
#   6 = but not inside the dichroic band, which stays uniformly black,
#   7 = the picture shows every flagged pixel that has a number; only detector defects are
#       hidden and small holes are filled from neighbours: see clean.py,
#   8 = a hole ringed by bright pixels, away from the edge, is a star core and is drawn white,
#   9 = PNGs are grey + alpha: pixels with no data are transparent, not black;
#       the fill=0 option leaves broken pixels as holes,
#  10 = a mostly flagged stamp takes its sky level, noise and white point from all usable pixels,
#  11 = and the pixels it takes them from are regridded with the picture: a mostly flagged stamp
#       smaller than the field (near the detector edge) used to fail, and a full one used them
#       unaligned)

class Skipped(Exception):
    """This frame is mostly masked, so it is left out of the flipbook."""


def stamp_size_px(req: FlipbookRequest) -> int:
    """Odd side length in pixels for the requested field of view."""
    n = max(11, math.ceil(req.fov_arcmin * 60 / PIXEL_SCALE_ARCSEC))
    return n | 1


def masked_fraction(flags: np.ndarray) -> float:
    """Fraction of pixels the picture cannot show: the detector-defect flags (NONFUNC,
    MISSING_DATA, HOT, COLD). Other fatal flags (DICHROIC, TRANSIENT, ...) are shown, so a frame
    that is mostly DICHROIC, like D4 with Barnard's Star in the band, is not skipped."""
    return float(((np.asarray(flags).astype("int64") & DISPLAY_HIDDEN_MASK) != 0).mean())


def _cancelled() -> ErrorEvent:
    return ErrorEvent(code=ErrorCode.CANCELLED, message="Cancelled.", retryable=True)


async def run_preview(
    req: FlipbookRequest,
    *,
    index: FrameIndex,
    stamps: StampSource,
    wavelength: WavelengthSource,
    renderer: Renderer,
    cache: Cache,
    select: Selector = simple_select,
    cancel: asyncio.Event | None = None,
    job_id: str | None = None,
    concurrency: int = 32,
) -> AsyncIterator:
    t0 = time.monotonic()
    job_id = job_id or uuid.uuid4().hex
    pos = SkyPosition(ra=req.ra, dec=req.dec)
    size = stamp_size_px(req)

    yield HeartbeatEvent()  # the client sees something within a second, even if TAP is slow

    # ---- find frames
    try:
        frames = await index.frames_at(pos)
    except UpstreamError as e:
        yield ErrorEvent(code=e.code, message=e.message, retryable=e.retryable)
        return
    if req.detector is not None:
        frames = [f for f in frames if f.detector == req.detector]
    if cancel is not None and cancel.is_set():
        yield _cancelled()
        return
    # in a thread: with real wavelengths the selector evaluates a WCS per frame, which takes
    # seconds for a field with thousands of frames (R Dra) and must not block the event loop
    selection = await asyncio.to_thread(select, frames, req) if frames else Selection(0, 0, [])
    if not selection.chosen:
        yield ErrorEvent(code=ErrorCode.NO_COVERAGE, retryable=False,
                         message="SPHEREx has not observed this position yet, "
                                 "or no frames match your detector choice.")
        return
    total = len(selection.chosen)

    # ---- one task per frame
    sem = asyncio.Semaphore(concurrency)
    params = {"fov": req.fov_arcmin, "zodi": req.use_zodi, "det": req.detector,
              "view": "sky" if req.view == ViewMode.SPECTRAL else req.view.value,
              "stage": "preview", "render": RENDER_VERSION}
    if req.orient != Orientation.NORTH:  # only when set, so existing cache entries stay valid
        params["orient"] = req.orient.value
    if not req.fill:
        params["fill"] = False

    async def one(slot: int, frame: FrameRef, visit: int) -> FrameEvent:
        key_args = dict(frame_id=frame.frame_id, release=frame.release, version=frame.version,
                        ra=req.ra, dec=req.dec, size_px=size, params=params)
        png_key = cache_key(kind="png", **key_args)
        meta_key = cache_key(kind="png-meta", **key_args)
        async with sem:
            hit = await cache.get(meta_key)  # meta is written after the PNG, so a hit is safe
            if hit:
                meta = json.loads(hit)
                if meta.get("skipped"):
                    raise Skipped(frame.frame_id)
                label = FrameLabel(**meta["label"])
                width, height = meta["width"], meta["height"]
            else:
                stamp = await stamps.fetch(frame, pos, size // 2)
                if masked_fraction(stamp.flags) > MAX_MASKED_FRACTION:
                    await cache.put(meta_key, json.dumps({"skipped": True}).encode())
                    raise Skipped(frame.frame_id)
                ny, nx = stamp.image.shape
                lam = wavelength.lambda_um(frame, stamp.x0 + nx / 2, stamp.y0 + ny / 2)
                rendered = renderer.render_png(stamp, req)
                label = FrameLabel(frame_id=frame.frame_id, detector=frame.detector,
                                   lambda_um=lam, t_mid_mjd=frame.t_mid,
                                   release=frame.release, version=frame.version,
                                   flags_summary=rendered.flags_summary)
                width, height = rendered.width, rendered.height
                await cache.put(png_key, rendered.png)
                await cache.put(meta_key, json.dumps(
                    {"label": label.model_dump(mode="json"), "width": width, "height": height}
                ).encode())
        return FrameEvent(slot=slot, stage=Stage.PREVIEW, png_url=f"/frames/{png_key}.png",
                          width=width, height=height, label=label, visit_index=visit,
                          elapsed_s=round(time.monotonic() - t0, 3))

    pending = {asyncio.create_task(one(slot, f, v))
               for slot, (f, v) in enumerate(selection.chosen)}
    cancel_waiter = asyncio.create_task(cancel.wait()) if cancel is not None else None
    n_ok, skipped, failures = 0, 0, []
    try:
        yield PlanEvent(job_id=job_id, n_slots=total, n_visits_total=selection.visits_total,
                        n_visits_shown=selection.visits_shown,
                        est_bytes=total * PLANES * size * BYTES_PER_ROW_PLANE)
        yield ProgressEvent(done=0, total=total, stage=Stage.PREVIEW)
        while pending:
            waiting = pending | ({cancel_waiter} if cancel_waiter else set())
            done, _ = await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)
            if cancel_waiter in done:
                yield _cancelled()
                return
            for task in done:
                pending.discard(task)
                try:
                    ev = task.result()
                except UpstreamError as e:
                    failures.append(e)
                except Skipped:
                    skipped += 1
                except Exception:  # a bug in one frame must not kill the job
                    log.exception("preview frame failed")
                    failures.append(None)
                else:
                    n_ok += 1
                    if n_ok == 1:
                        log.info("time to first frame: %.2f s", ev.elapsed_s)
                    yield ev
                yield ProgressEvent(done=n_ok + skipped + len(failures), total=total,
                                    stage=Stage.PREVIEW)
    finally:
        for task in pending:
            task.cancel()
        if cancel_waiter:
            cancel_waiter.cancel()
        await asyncio.gather(*pending, *( [cancel_waiter] if cancel_waiter else []),
                             return_exceptions=True)

    if n_ok == 0:
        first = next((f for f in failures if f is not None), None)
        if first:
            yield ErrorEvent(code=first.code, message=first.message, retryable=first.retryable)
        elif skipped:
            yield ErrorEvent(code=ErrorCode.NO_COVERAGE, retryable=False,
                             message="SPHEREx observed this position, but it falls where the "
                                     "detector is masked (for example the edge of a "
                                     "wavelength band), so there is nothing to show yet.")
        else:
            yield ErrorEvent(code=ErrorCode.INTERNAL, retryable=True,
                             message="Something went wrong while building the flipbook.")
        return
    yield DoneEvent(n_frames=n_ok, elapsed_s=round(time.monotonic() - t0, 3))
