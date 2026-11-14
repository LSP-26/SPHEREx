"""D7: disk cache, per-host concurrency limits, retry with backoff.

Cache keys come from contracts.cache_key (they include release and version), so entries are
immutable and never expire. IRSA gets at most 2 requests in flight (it returned HTTP 503 at
4 concurrent); S3 gets 16 by default (tuned later by the X4 benchmark).
"""
from __future__ import annotations

import asyncio
import os
import random
import tempfile
from collections import defaultdict
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TypeVar
from urllib.parse import urlparse

from contracts import ErrorCode

T = TypeVar("T")


# ------------------------------------------------------------------ disk cache
class DiskCache:
    """Implements contracts.interfaces.Cache. Files are written atomically, so a crash
    or a cancelled job can never leave a half-written entry behind."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        if not key.isalnum():  # sha256 hex only; blocks '../' tricks
            raise ValueError(f"bad cache key: {key!r}")
        return self.root / key[:2] / key

    async def get(self, key: str) -> bytes | None:
        return await asyncio.to_thread(self._get, key)

    async def put(self, key: str, value: bytes) -> None:
        await asyncio.to_thread(self._put, key, value)

    def _get(self, key: str) -> bytes | None:
        try:
            return self._path(key).read_bytes()
        except FileNotFoundError:
            return None

    def _put(self, key: str, value: bytes) -> None:
        path = self._path(key)
        if path.exists():  # immutable: same key means same bytes
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(value)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise


# ------------------------------------------------------------------ errors
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})


def is_retryable_status(status: int) -> bool:
    return status in RETRY_STATUS


class RetryableError(Exception):
    """Raise this inside a fetch function when trying again might work.
    status is the HTTP code (None for a timeout or dropped connection)."""

    def __init__(self, message: str = "", *, status: int | None = None,
                 retry_after: float | None = None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class UpstreamError(Exception):
    """Retries ran out. code and message are safe to show the user."""

    def __init__(self, code: ErrorCode, message: str, retryable: bool = True):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


# ------------------------------------------------------------------ limiter
DEFAULT_LIMITS = {"irsa": 2, "s3": 16}


class HostLimiter:
    """Caps requests in flight per upstream and retries with jittered exponential backoff.
    The slot is released while waiting between attempts, so a backing-off request
    never blocks others."""

    def __init__(
        self,
        limits: dict[str, int] | None = None,
        default_limit: int = 8,
        attempts: int = 4,
        base_delay: float = 0.5,
        max_delay: float = 8.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rng: Callable[[], float] = random.random,
    ):
        self.limits = {**DEFAULT_LIMITS, **(limits or {})}
        self.default_limit = default_limit
        self.attempts = attempts
        self.base_delay = base_delay
        self.max_delay = max_delay
        self._sleep = sleep
        self._rng = rng
        self._sems: dict[str, asyncio.Semaphore] = {}
        self.current: dict[str, int] = defaultdict(int)
        self.peak: dict[str, int] = defaultdict(int)  # highest in-flight seen (for tests, X4)

    @staticmethod
    def group(url_or_group: str) -> str:
        """IRSA TAP and cutouts share one cap; every S3 host shares another."""
        host = urlparse(url_or_group).hostname or url_or_group
        if host.endswith("irsa.ipac.caltech.edu") or host == "irsa":
            return "irsa"
        if host.endswith("amazonaws.com") or host == "s3":
            return "s3"
        return host

    def _sem(self, group: str) -> asyncio.Semaphore:
        if group not in self._sems:
            self._sems[group] = asyncio.Semaphore(self.limits.get(group, self.default_limit))
        return self._sems[group]

    def _delay(self, attempt: int, err: RetryableError) -> float:
        if err.retry_after is not None:
            return min(err.retry_after, 30.0)
        backoff = min(self.max_delay, self.base_delay * 2**attempt)
        return backoff * (0.5 + 0.5 * self._rng())

    @staticmethod
    def _code(group: str, status: int | None) -> tuple[ErrorCode, str]:
        if status is None:
            return ErrorCode.TIMEOUT, "The data service took too long to answer. Please try again."
        if group == "irsa":
            if status in (429, 503):
                return ErrorCode.IRSA_BUSY, "IRSA is busy right now. Please try again shortly."
            return ErrorCode.IRSA_DOWN, "IRSA is not responding. Cached showcases still work."
        return ErrorCode.S3_DOWN, "The SPHEREx data store is not responding. Showcases still work."

    async def run(self, url: str, fn: Callable[[], Awaitable[T]]) -> T:
        """Call fn() inside the host's limit. Retry only on RetryableError."""
        group = self.group(url)
        sem = self._sem(group)
        last: RetryableError | None = None
        for attempt in range(self.attempts):
            try:
                async with sem:
                    self.current[group] += 1
                    self.peak[group] = max(self.peak[group], self.current[group])
                    try:
                        return await fn()
                    finally:
                        self.current[group] -= 1
            except RetryableError as err:
                last = err
                if attempt < self.attempts - 1:
                    await self._sleep(self._delay(attempt, err))
        assert last is not None
        code, message = self._code(group, last.status)
        raise UpstreamError(code, message) from last
