"""Stand-ins so jobs can be built and tested before the real adapters exist.
Everything here is replaced by real code later."""
from __future__ import annotations

import asyncio
import struct
import zlib
from collections.abc import Callable

import numpy as np

from contracts import FlipbookRequest, FrameRef, SkyPosition, Stamp
from contracts.interfaces import Rendered, StampSource

# STUB wavelengths: rough middle of each detector's band (um). Real values come from D5.1.
_STUB_LAMBDA = {1: 0.93, 2: 1.37, 3: 2.03, 4: 3.12, 5: 4.12, 6: 4.71}


class MemoryCache:
    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}

    async def get(self, key: str) -> bytes | None:
        return self.data.get(key)

    async def put(self, key: str, value: bytes) -> None:
        self.data[key] = value


class StubWavelength:
    def lambda_um(self, frame: FrameRef, x: float, y: float) -> float:
        return _STUB_LAMBDA[frame.detector]


class DelayedStampSource:
    """Wraps a StampSource, adds a per-frame delay, and counts what happened."""

    def __init__(self, inner: StampSource, delay: Callable[[FrameRef], float] = lambda f: 0.0,
                 fail: Callable[[FrameRef], Exception | None] = lambda f: None):
        self.inner, self.delay, self.fail = inner, delay, fail
        self.started = self.finished = self.cancelled = 0

    async def fetch(self, frame: FrameRef, pos: SkyPosition, half_size_px: int) -> Stamp:
        self.started += 1
        try:
            await asyncio.sleep(self.delay(frame))
            err = self.fail(frame)
            if err:
                raise err
            out = await self.inner.fetch(frame, pos, half_size_px)
            self.finished += 1
            return out
        except asyncio.CancelledError:
            self.cancelled += 1
            raise


def _png(gray: np.ndarray) -> bytes:
    h, w = gray.shape

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    raw = b"".join(b"\x00" + gray[y].tobytes() for y in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


class StubRenderer:
    """Greyscale asinh stretch to 8-bit PNG. The real renderer (P3.2) adds colour and labels."""

    def render_png(self, stamp: Stamp, req: FlipbookRequest) -> Rendered:
        img = np.nan_to_num(stamp.image.astype("float64"))
        sky = np.median(img)
        noise = 1.4826 * np.median(np.abs(img - sky)) or 1.0
        s = np.arcsinh((img - sky) / (3 * noise))
        s = (s - s.min()) / (np.ptp(s) or 1.0)
        gray = (s * 255).astype("uint8")
        return Rendered(png=_png(gray), width=gray.shape[1], height=gray.shape[0])
