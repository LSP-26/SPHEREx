"""O2.1: the preview job. Yields contract events as frames finish.

Order: heartbeat (at once) -> plan -> progress(0) -> [frame, progress]... -> done.
Failures become ErrorEvents with plain-language messages, never exceptions.
Cancel by setting the `cancel` event or by closing/cancelling the generator: in-flight
fetches are cancelled either way.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import time
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

from backend.adapters.cache import UpstreamError
from contracts import (
    DoneEvent,
    ErrorCode,
    ErrorEvent,
    FlipbookRequest,
    FrameEvent,
    FrameLabel,
    FrameRef,
    HeartbeatEvent,
    PlanEvent,
    ProgressEvent,
    SkyPosition,
    Stage,
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


@dataclass(frozen=True)
class Selection:
    visits_total: int
    chosen: list[tuple[FrameRef, int]]  # (frame, visit_index), in time order


Selector = Callable[[list[FrameRef], FlipbookRequest], Selection]


def simple_select(frames: list[FrameRef], req: FlipbookRequest) -> Selection:
    """STUB until P4.1: every frame is its own 'visit', evenly subsampled in time to the cap.
    P4.1 replaces this with gap clustering and one wavelength-matched frame per visit x detector."""
    frames = sorted(frames, key=lambda f: f.t_start)
    n, cap = len(frames), req.preview_cap
    if n <= cap:
        idx = list(range(n))
    elif cap == 1:
        idx = [0]
    else:
        idx = sorted({round(i * (n - 1) / (cap - 1)) for i in range(cap)})
    return Selection(visits_total=n, chosen=[(frames[i], i) for i in idx])


def stamp_size_px(req: FlipbookRequest) -> int:
    """Odd side length in pixels for the requested field of view."""
    n = max(11, math.ceil(req.fov_arcmin * 60 / PIXEL_SCALE_ARCSEC))
    return n | 1


def _cancelled() -> ErrorEvent:
    return ErrorEvent(code=ErrorCode.CANCELLED, message="Cancelled.", retryable=False)


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
    selection = select(frames, req) if frames else Selection(0, [])
    if not selection.chosen:
        yield ErrorEvent(code=ErrorCode.NO_COVERAGE, retryable=False,
                         message="SPHEREx has not observed this position yet, "
                                 "or no frames match your detector choice.")
        return
    total = len(selection.chosen)

    # ---- one task per frame
    sem = asyncio.Semaphore(concurrency)
    params = {"fov": req.fov_arcmin, "zodi": req.use_zodi, "det": req.detector,
              "view": req.view.value, "stage": "preview"}

    async def one(slot: int, frame: FrameRef, visit: int) -> FrameEvent:
        key_args = dict(frame_id=frame.frame_id, release=frame.release, version=frame.version,
                        ra=req.ra, dec=req.dec, size_px=size, params=params)
        png_key = cache_key(kind="png", **key_args)
        meta_key = cache_key(kind="png-meta", **key_args)
        async with sem:
            hit = await cache.get(meta_key)  # meta is written after the PNG, so a hit is safe
            if hit:
                meta = json.loads(hit)
                label = FrameLabel(**meta["label"])
                width, height = meta["width"], meta["height"]
            else:
                stamp = await stamps.fetch(frame, pos, size // 2)
                ny, nx = stamp.image.shape
                lam = wavelength.lambda_um(frame, stamp.x0 + nx / 2, stamp.y0 + ny / 2)
                rendered = renderer.render_png(stamp, req)
                label = FrameLabel(frame_id=frame.frame_id, detector=frame.detector,
                                   lambda_um=lam, t_mid_mjd=frame.t_mid,
                                   release=frame.release, version=frame.version)
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
    n_ok, failures = 0, []
    try:
        yield PlanEvent(job_id=job_id, n_slots=total, n_visits_total=selection.visits_total,
                        n_visits_shown=total,
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
                except Exception:  # a bug in one frame must not kill the job
                    log.exception("preview frame failed")
                    failures.append(None)
                else:
                    n_ok += 1
                    if n_ok == 1:
                        log.info("time to first frame: %.2f s", ev.elapsed_s)
                    yield ev
                yield ProgressEvent(done=n_ok + len(failures), total=total, stage=Stage.PREVIEW)
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
        else:
            yield ErrorEvent(code=ErrorCode.INTERNAL, retryable=True,
                             message="Something went wrong while building the flipbook.")
        return
    yield DoneEvent(n_frames=n_ok, elapsed_s=round(time.monotonic() - t0, 3))
