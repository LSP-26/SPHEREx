"""I.2: load and failure tests. Many users at once, and upstreams that fail in each way the
user can be told about (IRSA busy, IRSA down, S3 down, timeout). Everything local: the fixture
data and fake stamp sources, no network."""
import asyncio
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from backend.adapters.cache import UpstreamError
from backend.adapters.fixtures import FixtureFrameIndex, FixtureStampSource
from backend.adapters.stubs import DelayedStampSource, MemoryCache, StubRenderer, StubWavelength
from backend.pipeline.select import make_selector
from backend.server.jobs import Deps, JobRunner
from backend.server.settings import Settings
from backend.tests.unit.server_harness import LiveServer, read_sse
from contracts import DoneEvent, ErrorCode, ErrorEvent, FlipbookRequest

SLOTS = 4        # the synthetic fixture: 2 visits x 2 detectors


def stamps(delay=0.0, fail=lambda f: None):
    return DelayedStampSource(FixtureStampSource("synthetic_test"), lambda f: delay, fail)


def runner(src, **settings):
    cache = MemoryCache()

    async def make_deps(req):
        return Deps(index=FixtureFrameIndex("synthetic_test"), stamps=src,
                    wavelength=StubWavelength(), renderer=StubRenderer(), cache=cache,
                    select=make_selector(None))

    return JobRunner(make_deps, Settings(source="fixtures", cache_dir=None, **settings))


def req(i=0):
    return FlipbookRequest(ra=10.0, dec=0.0, fov_arcmin=3.0 + 0.1 * i)


# ------------------------------------------------------------------ failures
@pytest.mark.parametrize("code", [ErrorCode.IRSA_BUSY, ErrorCode.IRSA_DOWN, ErrorCode.S3_DOWN,
                                  ErrorCode.TIMEOUT])
async def test_every_upstream_failure_ends_in_a_clear_retryable_error(code):
    r = runner(stamps(fail=lambda f: UpstreamError(code, "The data service said no.")))
    job = r.submit(req())
    await asyncio.wait_for(job.task, 10)
    last = job.events[-1]
    assert isinstance(last, ErrorEvent) and last.code is code
    assert last.message and last.retryable
    await r.shutdown()


async def test_a_failed_job_is_not_reused_and_a_retry_can_succeed():
    state = {"down": True}
    r = runner(stamps(fail=lambda f: UpstreamError(ErrorCode.S3_DOWN, "down")
                      if state["down"] else None))
    first = r.submit(req())
    await asyncio.wait_for(first.task, 10)
    assert isinstance(first.events[-1], ErrorEvent)
    state["down"] = False
    second = r.submit(req())
    assert second.id != first.id
    await asyncio.wait_for(second.task, 10)
    assert isinstance(second.events[-1], DoneEvent)
    await r.shutdown()


async def test_part_of_the_frames_failing_still_delivers_the_rest():
    bad = {"synth_00", "synth_01"}
    r = runner(stamps(fail=lambda f: UpstreamError(ErrorCode.S3_DOWN, "down")
                      if f.frame_id in bad else None))
    job = r.submit(req())
    await asyncio.wait_for(job.task, 10)
    assert isinstance(job.events[-1], DoneEvent) and 0 < job.events[-1].n_frames < SLOTS
    await r.shutdown()


# ------------------------------------------------------------------ load
async def test_fifty_identical_requests_share_one_job_and_one_set_of_reads():
    src = stamps(delay=0.05)
    r = runner(src)
    jobs = [r.submit(req()) for _ in range(50)]
    assert len({j.id for j in jobs}) == 1
    await asyncio.wait_for(jobs[0].task, 10)
    assert src.started == SLOTS
    await r.shutdown()


async def test_thirty_different_requests_queue_behind_three_slots_and_all_finish():
    src = stamps(delay=0.01)
    r = runner(src, max_running=3)
    jobs = [r.submit(req(i)) for i in range(30)]
    assert len({j.id for j in jobs}) == 30
    await asyncio.wait_for(asyncio.gather(*[j.task for j in jobs]), 60)
    assert all(isinstance(j.events[-1], DoneEvent) for j in jobs)
    assert r.stats()["running"] == 0
    await r.shutdown()


async def test_jobs_nobody_watches_are_cancelled_whether_running_or_queued():
    src = stamps(delay=5.0)
    r = runner(src, max_running=2, idle_cancel_s=0.2)
    jobs = [r.submit(req(i)) for i in range(10)]
    await asyncio.sleep(0.5)
    assert r.reap() == 10
    await asyncio.wait_for(asyncio.gather(*[j.task for j in jobs]), 10)
    assert all(isinstance(j.events[-1], ErrorEvent) and j.events[-1].code is ErrorCode.CANCELLED
               for j in jobs)
    assert src.cancelled > 0
    await r.shutdown()


# ------------------------------------------------------------------ over real HTTP
def test_twenty_five_users_at_once_all_get_a_finished_flipbook():
    cfg = Settings(source="fixtures", cache_dir=None, fake_latency_s=0.05, max_running=4)

    def user(i):
        with httpx.Client(base_url=s.url, timeout=60) as c:
            _, events = read_sse(c, f"/flipbook?ra=10&dec=0&fov={3 + 0.2 * i}")
        return events

    with LiveServer(cfg) as s, ThreadPoolExecutor(25) as pool:
        runs = list(pool.map(user, range(25)))
    assert all(ev[-1].data["type"] == "done" for ev in runs)
    assert all(sum(e.data["type"] == "frame" for e in ev) == SLOTS for ev in runs)


def test_a_user_stuck_in_the_queue_still_sees_a_first_event_at_once():
    """With one slot, later users wait for earlier jobs. They must not stare at nothing."""
    cfg = Settings(source="fixtures", cache_dir=None, fake_latency_s=0.3, max_running=1)

    def user(i):
        with httpx.Client(base_url=s.url, timeout=60) as c:
            _, events = read_sse(c, f"/flipbook?ra=10&dec=0&fov={3 + 0.2 * i}")
        return events

    with LiveServer(cfg) as s, ThreadPoolExecutor(4) as pool:
        runs = list(pool.map(user, range(4)))
    assert all(ev[-1].data["type"] == "done" for ev in runs)
    assert all(ev[0].data["type"] == "heartbeat" and ev[0].at < 1.0 for ev in runs)
