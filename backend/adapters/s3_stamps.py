"""D4: StampSource that reads row bands straight from S3 with HTTP range requests.

Needs a catalogue record on the FrameRef (plane offsets and WCS), so headers are never re-read.
Per frame: one small request per plane (IMAGE, FLAGS, VARIANCE, and ZODI if wanted), sent at
the same time. The PSF is never touched. Everything goes through the HostLimiter, so S3 gets
its own cap and 503s are retried with backoff.
"""
from __future__ import annotations

import asyncio

import httpx
import numpy as np

from backend.adapters.cache import RETRY_STATUS, HostLimiter, RetryableError, UpstreamError
from backend.adapters.compressed_plane import (
    decode_rows as decode_compressed_rows,
)
from backend.adapters.compressed_plane import (
    parse_descriptors,
    plan_reads,
)
from backend.adapters.fits_layout import RangeNotSupported, decode_rows
from backend.adapters.frame_layout import EXPECTED_BITPIX
from contracts import ErrorCode, FrameRef, Plane, PlaneSpec, SkyPosition, Stamp


class OutOfFrame(ValueError):
    """The target is not on this frame (the frame index should have filtered it out)."""


class StaleCatalogueRecord(ValueError):
    """The catalogue record describes a plane differently from what this reader can decode.
    Catalogues built before contract v0.2.0 recorded a compressed FLAGS table as BITPIX 8, and
    reading it as pixels would give garbage without any error. Rebuild the catalogue."""


def https_url(uri: str, region: str = "us-east-1") -> str:
    if uri.startswith("s3://"):
        bucket, _, key = uri[5:].partition("/")
        return f"https://{bucket}.s3.{region}.amazonaws.com/{key}"
    return uri


def _retry_after(resp: httpx.Response) -> float | None:
    try:
        return float(resp.headers["Retry-After"])
    except (KeyError, ValueError):
        return None


class S3StampSource:
    def __init__(self, limiter: HostLimiter | None = None, use_zodi: bool = True,
                 client: httpx.AsyncClient | None = None, timeout: float = 30.0):
        self.limiter = limiter or HostLimiter()
        self.use_zodi = use_zodi
        self._client = client
        self._timeout = timeout
        self.requests = 0       # successful range reads (for the X4 benchmark)
        self.bytes_read = 0

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self._timeout, limits=httpx.Limits(max_connections=64))
        return self._client

    @staticmethod
    def target_pixel(frame: FrameRef, pos: SkyPosition) -> tuple[float, float]:
        """0-based (x, y) of the position on this frame, SIP distortion included."""
        from backend.adapters.wcs_util import celestial_wcs
        wcs = celestial_wcs(frame.wcs_header)
        x, y = wcs.all_world2pix([[pos.ra, pos.dec]], 0)[0]
        return float(x), float(y)

    async def _get_range(self, url: str, start: int, length: int) -> bytes:
        try:
            r = await self._http().get(
                url, headers={"Range": f"bytes={start}-{start + length - 1}"})
        except (httpx.TimeoutException, httpx.TransportError) as e:
            raise RetryableError(f"{type(e).__name__}: {e}") from e
        if r.status_code == 206:
            if len(r.content) != length:
                raise RetryableError("short read", status=None)
            self.requests += 1
            self.bytes_read += length
            return r.content
        if r.status_code in RETRY_STATUS:
            raise RetryableError(f"HTTP {r.status_code}", status=r.status_code,
                                 retry_after=_retry_after(r))
        if r.status_code == 200:
            raise RangeNotSupported("server ignored the Range header")
        raise UpstreamError(ErrorCode.S3_DOWN,
                            f"A SPHEREx frame is not available (HTTP {r.status_code}).",
                            retryable=False)

    async def _compressed_rows(self, url: str, frame: FrameRef, spec: PlaneSpec,
                               y0: int, y1: int) -> np.ndarray:
        """Rows y0:y1 of a tile-compressed plane: the descriptors, then the heap bytes they
        point at (usually one range), then decoding in a worker thread."""
        comp = spec.compression
        assert comp is not None
        raw = await self.limiter.run(url, lambda: self._get_range(
            url, spec.offset + y0 * comp.row_bytes, (y1 - y0) * comp.row_bytes))
        tiles = parse_descriptors(raw, comp)
        ranges = plan_reads(tiles)
        parts = await asyncio.gather(*[
            self.limiter.run(url, lambda s=s, n=n: self._get_range(url, comp.heap_offset + s, n))
            for s, n in ranges])
        return await asyncio.to_thread(decode_compressed_rows, comp, frame.naxis1, tiles,
                                       ranges, list(parts), spec.bzero, spec.bscale)

    async def _band(self, url: str, frame: FrameRef, plane: Plane, y0: int, y1: int,
                    x0: int, x1: int) -> np.ndarray:
        spec = frame.planes[plane]  # type: ignore[index]
        if spec.bitpix != EXPECTED_BITPIX[plane]:
            raise StaleCatalogueRecord(
                f"{frame.frame_id}: the catalogue says {plane.value} has BITPIX {spec.bitpix}, "
                f"expected {EXPECTED_BITPIX[plane]}. This record predates compressed FLAGS "
                "support (contract v0.2.0): rebuild the catalogue.")
        if spec.compression is not None:
            return (await self._compressed_rows(url, frame, spec, y0, y1))[:, x0:x1]
        row_bytes = frame.naxis1 * abs(spec.bitpix) // 8
        start, length = spec.offset + y0 * row_bytes, (y1 - y0) * row_bytes
        raw = await self.limiter.run(url, lambda: self._get_range(url, start, length))
        return decode_rows(raw, spec.bitpix, spec.bzero, spec.bscale, frame.naxis1)[:, x0:x1]

    async def fetch(self, frame: FrameRef, pos: SkyPosition, half_size_px: int) -> Stamp:
        if not frame.planes or not frame.wcs_header:
            raise ValueError(f"{frame.frame_id} has no catalogue record (planes/WCS); "
                             "use the IRSA stamp source for it")
        x, y = self.target_pixel(frame, pos)
        ix, iy = round(x), round(y)
        x0, x1 = max(0, ix - half_size_px), min(frame.naxis1, ix + half_size_px + 1)
        y0, y1 = max(0, iy - half_size_px), min(frame.naxis2, iy + half_size_px + 1)
        if x0 >= x1 or y0 >= y1:
            raise OutOfFrame(f"({x:.0f}, {y:.0f}) is off frame {frame.frame_id}")

        url = frame.uri if frame.uri.startswith("http") else https_url(frame.uri)
        names = [Plane.IMAGE, Plane.FLAGS, Plane.VARIANCE] + (
            [Plane.ZODI] if self.use_zodi else [])
        tasks = [asyncio.create_task(self._band(url, frame, p, y0, y1, x0, x1)) for p in names]
        try:
            arrays = dict(zip(names, await asyncio.gather(*tasks), strict=True))
        except BaseException:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

        flags = (arrays[Plane.FLAGS].astype("int64") & 0xFFFFFFFF).astype("uint32")
        zodi = arrays.get(Plane.ZODI)
        return Stamp(
            frame=frame, x0=x0, y0=y0,
            image=arrays[Plane.IMAGE].astype("float32"), flags=flags,
            variance=arrays[Plane.VARIANCE].astype("float32"),
            zodi=None if zodi is None else zodi.astype("float32"),
            wcs_header=dict(frame.wcs_header), source="s3")
