"""Flipbook jobs: run the preview job once, let any number of clients watch it.

A request becomes a Job. The job runs run_preview in the background and appends every event to
a list. Clients (an SSE stream, or polling) read from that list, so:
- a dropped connection resumes where it left off (Last-Event-ID),
- a proxy that buffers the stream can be bypassed by polling the same job,
- two users asking for the same thing share one job (and one set of S3 reads).
A running job that nobody has watched for idle_cancel_s is cancelled, which cancels its
in-flight fetches. Finished jobs are kept for job_ttl_s so a refresh replays instead of redoing.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from backend.jobs.flipbook import run_flipbook
from backend.pipeline.select import Selector
from backend.server.settings import Settings
from contracts import (
    DoneEvent,
    ErrorCode,
    ErrorEvent,
    Event,
    FlipbookRequest,
    HeartbeatEvent,
    ViewMode,
)
from contracts.interfaces import Cache, FrameIndex, Renderer, StampSource, WavelengthSource

log = logging.getLogger(__name__)

# Errors that would come out the same on a retry, so a repeat request may reuse the job.
# Anything else (S3 hiccup, cancellation, a bug) deserves a fresh attempt.
_STABLE_ERRORS = {ErrorCode.NO_COVERAGE, ErrorCode.BAD_COORDS}


class NoData(Exception):
    """The deps factory has nothing for this request. message is safe to show the user."""


@dataclass
class Deps:
    index: FrameIndex
    stamps: StampSource
    wavelength: WavelengthSource
    renderer: Renderer
    cache: Cache
    select: Selector

    def kwargs(self) -> dict:
        return dict(index=self.index, stamps=self.stamps, wavelength=self.wavelength,
                    renderer=self.renderer, cache=self.cache, select=self.select)


DepsFactory = Callable[[FlipbookRequest], Awaitable[Deps]]


def is_terminal(ev: Event) -> bool:
    return isinstance(ev, (DoneEvent, ErrorEvent))


def request_key(req: FlipbookRequest) -> str:
    return hashlib.sha256(req.model_dump_json().encode()).hexdigest()


class Job:
    def __init__(self, job_id: str, key: str, request: FlipbookRequest):
        self.id, self.key, self.request = job_id, key, request
        self.events: list[Event] = []
        self.finished = False
        self.finished_at: float | None = None
        self.created = self.last_active = time.monotonic()
        self.subscribers = 0
        self.cancel = asyncio.Event()
        self.task: asyncio.Task | None = None
        self._cond = asyncio.Condition()

    async def publish(self, ev: Event) -> None:
        async with self._cond:
            self.events.append(ev)
            self._cond.notify_all()

    async def finish(self) -> None:
        async with self._cond:
            self.finished = True
            self.finished_at = time.monotonic()
            self._cond.notify_all()

    async def wait_for(self, index: int, timeout: float) -> bool:
        """Wait until there is an event at `index` or the job has finished. False on timeout."""
        async with self._cond:
            try:
                await asyncio.wait_for(
                    self._cond.wait_for(lambda: len(self.events) > index or self.finished),
                    timeout)
            except TimeoutError:
                return False
        return True


class JobRunner:
    def __init__(self, make_deps: DepsFactory, settings: Settings):
        self.make_deps, self.s = make_deps, settings
        self._jobs: dict[str, Job] = {}
        self._by_key: dict[str, Job] = {}
        self._slots = asyncio.Semaphore(settings.max_running)

    # ------------------------------------------------------------ lookup and submission
    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def _reusable(self, job: Job, now: float) -> bool:
        if job.cancel.is_set() or now - job.created > self.s.job_ttl_s:
            return False
        if not job.finished:
            return True
        last = job.events[-1] if job.events else None
        if isinstance(last, DoneEvent):
            return True
        return isinstance(last, ErrorEvent) and last.code in _STABLE_ERRORS

    def submit(self, req: FlipbookRequest) -> Job:
        now, key = time.monotonic(), request_key(req)
        job = self._by_key.get(key)
        if job is not None and self._reusable(job, now):
            job.last_active = now
            return job
        job = Job(uuid.uuid4().hex, key, req)
        self._jobs[job.id] = self._by_key[key] = job
        job.task = asyncio.create_task(self._run(job))
        self._evict(now)
        return job

    # ------------------------------------------------------------ running
    async def _run(self, job: Job) -> None:
        try:
            await job.publish(HeartbeatEvent())   # at once, even while queued
            async with self._slots:
                if job.cancel.is_set():
                    await job.publish(ErrorEvent(
                        code=ErrorCode.CANCELLED, message="Cancelled.", retryable=True))
                    return
                try:
                    deps = await self.make_deps(job.request)
                except NoData as e:
                    await job.publish(ErrorEvent(code=ErrorCode.NO_COVERAGE, message=str(e)))
                    return
                refine = self.s.refine and job.request.view != ViewMode.SPECTRAL
                async for ev in run_flipbook(job.request, refine=refine, **deps.kwargs(),
                                             cancel=job.cancel, job_id=job.id):
                    if isinstance(ev, HeartbeatEvent):
                        continue          # already sent above
                    await job.publish(ev)
        except asyncio.CancelledError:      # server shutting down
            await job.publish(ErrorEvent(
                code=ErrorCode.CANCELLED, message="Cancelled.", retryable=True))
            raise
        except Exception:
            log.exception("job %s failed", job.id)
            await job.publish(ErrorEvent(
                code=ErrorCode.INTERNAL, retryable=True,
                message="Something went wrong while building the flipbook."))
        finally:
            await job.finish()

    # ------------------------------------------------------------ housekeeping
    def reap(self) -> int:
        """Cancel running jobs nobody watches, drop old finished ones. Returns how many it
        cancelled."""
        now, cancelled = time.monotonic(), 0
        for job in list(self._jobs.values()):
            if (not job.finished and not job.cancel.is_set() and job.subscribers == 0
                    and now - job.last_active > self.s.idle_cancel_s):
                job.cancel.set()
                cancelled += 1
        self._evict(now)
        return cancelled

    def _evict(self, now: float) -> None:
        def drop(job: Job) -> None:
            self._jobs.pop(job.id, None)
            if self._by_key.get(job.key) is job:
                del self._by_key[job.key]

        for job in list(self._jobs.values()):
            if (job.finished and job.subscribers == 0 and job.finished_at is not None
                    and now - job.finished_at > self.s.job_ttl_s):
                drop(job)
        extra = len(self._jobs) - self.s.max_jobs
        if extra > 0:
            for job in sorted((j for j in self._jobs.values() if j.finished),
                              key=lambda j: j.finished_at or 0)[:extra]:
                drop(job)

    async def reap_forever(self) -> None:
        period = max(0.05, min(5.0, self.s.idle_cancel_s / 3))
        while True:
            await asyncio.sleep(period)
            self.reap()

    async def shutdown(self) -> None:
        tasks = [j.task for j in self._jobs.values() if j.task and not j.task.done()]
        for j in self._jobs.values():
            j.cancel.set()
        if tasks:
            await asyncio.wait(tasks, timeout=5)
        for t in tasks:
            if not t.done():
                t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def stats(self) -> dict:
        running = sum(1 for j in self._jobs.values() if not j.finished)
        return {"jobs": len(self._jobs), "running": running}
