"""The whole flipbook job: preview, then (optionally) refine, then done.

run_preview ends with a DoneEvent. With refine on, that event is held back, the refine stage
runs and sends REFINED frames that replace the preview ones, and the DoneEvent goes out last.
If refine fails for any reason the preview stays and the job still finishes normally.
Cancelling during refine ends the job with the usual 'cancelled' error.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator

from backend.jobs.preview import run_preview
from backend.jobs.refine import run_refine
from contracts import DoneEvent, ErrorEvent, FlipbookRequest

log = logging.getLogger(__name__)


async def run_flipbook(req: FlipbookRequest, *, refine: bool = False,
                       cancel: asyncio.Event | None = None, job_id: str | None = None,
                       **deps) -> AsyncIterator:
    t0 = time.monotonic()
    held: DoneEvent | None = None
    async for ev in run_preview(req, cancel=cancel, job_id=job_id, **deps):
        if refine and isinstance(ev, DoneEvent):
            held = ev
            continue
        yield ev
    if held is None:
        return
    try:
        async for ev in run_refine(req, cancel=cancel, t0=t0, **deps):
            yield ev
            if isinstance(ev, ErrorEvent):
                return          # only ever 'cancelled'
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("refine stage failed, keeping the preview")
    yield DoneEvent(n_frames=held.n_frames, elapsed_s=round(time.monotonic() - t0, 3))
