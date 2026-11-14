import asyncio

import pytest

from backend.adapters.cache import (
    DiskCache,
    HostLimiter,
    RetryableError,
    UpstreamError,
    is_retryable_status,
)
from contracts import ErrorCode, cache_key
from contracts.interfaces import Cache

IRSA = "https://irsa.ipac.caltech.edu/cgi-bin/Cutouts/nph-cutouts"
S3 = "https://nasa-irsa-spherex.s3.amazonaws.com/qr2/frame.fits"


# ---- cache
async def test_cache_round_trip_and_miss(tmp_path):
    c = DiskCache(tmp_path)
    assert isinstance(c, Cache)
    key = cache_key(frame_id="a", release="QR2", version="v20", ra=1, dec=2, size_px=47)
    assert await c.get(key) is None
    await c.put(key, b"hello")
    assert await c.get(key) == b"hello"
    assert await DiskCache(tmp_path).get(key) == b"hello"  # survives a restart


async def test_cache_concurrent_puts_same_key(tmp_path):
    c = DiskCache(tmp_path)
    await asyncio.gather(*[c.put("abc123", b"x" * 1000) for _ in range(20)])
    assert await c.get("abc123") == b"x" * 1000
    assert not list(tmp_path.rglob("*.tmp"))


async def test_cache_rejects_bad_keys(tmp_path):
    c = DiskCache(tmp_path)
    for bad in ("../evil", "a/b", ""):
        with pytest.raises(ValueError):
            await c.get(bad)


# ---- limiter
async def _peak(limiter, url, n):
    async def work():
        await asyncio.sleep(0.01)
        return 1
    await asyncio.gather(*[limiter.run(url, work) for _ in range(n)])
    return limiter.peak[limiter.group(url)]


async def test_irsa_never_exceeds_two():
    assert await _peak(HostLimiter(), IRSA, 12) == 2


async def test_s3_allows_many():
    assert await _peak(HostLimiter(), S3, 40) == 16


def test_groups():
    g = HostLimiter.group
    assert g(IRSA) == g("https://irsa.ipac.caltech.edu/TAP/sync") == "irsa"
    assert g(S3) == g("https://s3.us-east-1.amazonaws.com/nasa-irsa-spherex/x") == "s3"


# ---- retry
async def test_retries_then_succeeds_with_growing_delays():
    sleeps, calls = [], 0

    async def fake_sleep(d):
        sleeps.append(d)

    async def flaky():
        nonlocal calls
        calls += 1
        if calls < 3:
            raise RetryableError("busy", status=503)
        return "ok"

    lim = HostLimiter(sleep=fake_sleep, rng=lambda: 1.0)
    assert await lim.run(IRSA, flaky) == "ok"
    assert sleeps == [0.5, 1.0]


async def test_retry_after_is_honoured():
    sleeps, calls = [], 0

    async def fake_sleep(d):
        sleeps.append(d)

    async def f():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RetryableError(status=429, retry_after=3.0)
        return 1

    await HostLimiter(sleep=fake_sleep).run(IRSA, f)
    assert sleeps == [3.0]


@pytest.mark.parametrize("url,status,code", [
    (IRSA, 503, ErrorCode.IRSA_BUSY),
    (IRSA, 500, ErrorCode.IRSA_DOWN),
    (S3, 503, ErrorCode.S3_DOWN),
    (S3, None, ErrorCode.TIMEOUT),
])
async def test_exhausted_gives_clear_error(url, status, code):
    async def nosleep(d):
        pass

    async def always():
        raise RetryableError(status=status)

    with pytest.raises(UpstreamError) as e:
        await HostLimiter(sleep=nosleep, attempts=3).run(url, always)
    assert e.value.code is code and e.value.message


async def test_other_errors_not_retried():
    calls = 0

    async def bad():
        nonlocal calls
        calls += 1
        raise ValueError("bug")

    with pytest.raises(ValueError):
        await HostLimiter().run(S3, bad)
    assert calls == 1


async def test_slot_free_while_backing_off():
    """A request waiting to retry must not hold its slot."""
    lim = HostLimiter(limits={"irsa": 1})
    gate, events = asyncio.Event(), []

    async def sleeper(d):
        events.append("sleeping")
        await gate.wait()

    lim._sleep = sleeper
    n = 0

    async def first():
        nonlocal n
        n += 1
        if n == 1:
            raise RetryableError(status=503)
        return "a"

    async def second():
        events.append("second ran")
        return "b"

    t1 = asyncio.create_task(lim.run(IRSA, first))
    await asyncio.sleep(0.01)
    assert await lim.run(IRSA, second) == "b"  # would hang if t1 still held the slot
    gate.set()
    assert await t1 == "a"
    assert events == ["sleeping", "second ran"]


def test_status_helper():
    assert is_retryable_status(503) and is_retryable_status(429)
    assert not is_retryable_status(404) and not is_retryable_status(206)
