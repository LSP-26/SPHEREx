import asyncio
import struct

import pytest

from backend.adapters.cache import UpstreamError
from backend.adapters.fixtures import FixtureFrameIndex, FixtureStampSource, load_frames
from backend.adapters.stubs import DelayedStampSource, MemoryCache, StubRenderer, StubWavelength
from backend.jobs.preview import run_preview, stamp_size_px
from contracts import (
    DoneEvent,
    ErrorCode,
    ErrorEvent,
    FlipbookRequest,
    FrameEvent,
    HeartbeatEvent,
    PlanEvent,
    ProgressEvent,
)

REQ = FlipbookRequest(ra=10.0, dec=0.0, fov_arcmin=5.0)


def make(delay=lambda f: 0.0, fail=lambda f: None, index=None):
    stamps = DelayedStampSource(FixtureStampSource("synthetic_test"), delay, fail)
    return dict(index=index or FixtureFrameIndex("synthetic_test"), stamps=stamps,
                wavelength=StubWavelength(), renderer=StubRenderer(), cache=MemoryCache())


async def collect(req=REQ, **kw):
    return [e async for e in run_preview(req, **kw)]


async def test_event_order_and_slots():
    # later frames finish first, so frames arrive out of order
    deps = make(delay=lambda f: 0.05 - 0.008 * int(f.frame_id[-1]))
    events = await collect(**deps)
    assert isinstance(events[0], HeartbeatEvent)
    assert isinstance(events[1], PlanEvent) and events[1].n_slots == 6
    assert isinstance(events[2], ProgressEvent) and events[2].done == 0
    assert isinstance(events[-1], DoneEvent) and events[-1].n_frames == 6
    frames = [e for e in events if isinstance(e, FrameEvent)]
    assert sorted(f.slot for f in frames) == list(range(6))
    assert [f.slot for f in frames] != list(range(6))  # really did arrive out of order
    progress = [e.done for e in events if isinstance(e, ProgressEvent)]
    assert progress == list(range(7))
    assert frames[0].label.lambda_um > 0 and frames[0].label.release == "QR2"


async def test_pngs_are_real_and_stored():
    deps = make()
    events = await collect(**deps)
    f = next(e for e in events if isinstance(e, FrameEvent))
    key = f.png_url.rsplit("/", 1)[1].removesuffix(".png")
    png = deps["cache"].data[key]
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    w, h = struct.unpack(">II", png[16:24])
    assert (w, h) == (f.width, f.height) == (47, 47)


async def test_preview_cap_and_visit_counts():
    events = await collect(REQ.model_copy(update={"preview_cap": 3}), **make())
    plan = next(e for e in events if isinstance(e, PlanEvent))
    assert (plan.n_slots, plan.n_visits_total, plan.n_visits_shown) == (3, 6, 3)
    assert sum(isinstance(e, FrameEvent) for e in events) == 3


async def test_detector_filter():
    events = await collect(REQ.model_copy(update={"detector": 1}), **make())
    labels = [e.label for e in events if isinstance(e, FrameEvent)]
    assert len(labels) == 3 and {lb.detector for lb in labels} == {1}


async def test_warm_cache_skips_fetching():
    deps = make()
    await collect(**deps)
    first_fetches = deps["stamps"].started
    events = await collect(**deps)
    assert deps["stamps"].started == first_fetches  # nothing new fetched
    assert isinstance(events[-1], DoneEvent) and events[-1].n_frames == 6


async def test_cancel_event_stops_job_and_inflight_fetches():
    deps = make(delay=lambda f: 5.0)
    cancel = asyncio.Event()
    events = []
    async for e in run_preview(REQ, cancel=cancel, **deps):
        events.append(e)
        if isinstance(e, ProgressEvent):
            cancel.set()
    assert isinstance(events[-1], ErrorEvent) and events[-1].code is ErrorCode.CANCELLED
    assert deps["stamps"].cancelled == 6


async def test_closing_the_generator_cancels_fetches():
    deps = make(delay=lambda f: 5.0)
    gen = run_preview(REQ, **deps)
    async for e in gen:
        if isinstance(e, ProgressEvent):
            break
    await asyncio.sleep(0.05)  # let the fetches actually start
    assert deps["stamps"].started == 6
    await gen.aclose()  # what happens when the browser disconnects
    assert deps["stamps"].cancelled == 6


async def test_no_coverage():
    class Empty:
        async def frames_at(self, pos, margin_px=30):
            return []

    events = await collect(**make(index=Empty()))
    assert isinstance(events[-1], ErrorEvent) and events[-1].code is ErrorCode.NO_COVERAGE


async def test_index_failure_becomes_clear_error():
    class Busy:
        async def frames_at(self, pos, margin_px=30):
            raise UpstreamError(ErrorCode.IRSA_BUSY, "IRSA is busy right now.")

    events = await collect(**make(index=Busy()))
    assert isinstance(events[-1], ErrorEvent) and events[-1].code is ErrorCode.IRSA_BUSY
    assert events[-1].retryable


async def test_some_frames_fail_job_still_finishes():
    bad = load_frames("synthetic_test")[0].frame_id
    deps = make(fail=lambda f: UpstreamError(ErrorCode.S3_DOWN, "down") if f.frame_id == bad
                else None)
    events = await collect(**deps)
    assert isinstance(events[-1], DoneEvent) and events[-1].n_frames == 5
    assert [e.done for e in events if isinstance(e, ProgressEvent)][-1] == 6


async def test_all_frames_fail_gives_error():
    deps = make(fail=lambda f: UpstreamError(ErrorCode.IRSA_BUSY, "busy"))
    events = await collect(**deps)
    assert isinstance(events[-1], ErrorEvent) and events[-1].code is ErrorCode.IRSA_BUSY


async def test_unexpected_bug_in_one_frame_does_not_crash():
    deps = make(fail=lambda f: RuntimeError("boom") if f.frame_id.endswith("2") else None)
    events = await collect(**deps)
    assert isinstance(events[-1], DoneEvent) and events[-1].n_frames == 5


@pytest.mark.parametrize("fov,expected", [(0.1, 11), (5.0, 49), (30.0, 293)])
def test_stamp_size_is_odd_and_scales(fov, expected):
    n = stamp_size_px(REQ.model_copy(update={"fov_arcmin": fov}))
    assert n == expected and n % 2 == 1
