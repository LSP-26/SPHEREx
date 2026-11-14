"""A ByteSource (see fits_layout) that reuses HTTP connections.

fits_layout.HttpRangeSource uses urllib, which opens a new TCP and TLS connection for every
request. Reading one frame's layout is 4 requests, so connection set-up can dominate. This one
sends all requests through a shared httpx.Client (thread-safe), so connections stay open between
requests and between frames. S3 serves every frame from one host, so a pool of N connections
is enough for N worker threads.

  client = make_client(workers=32)
  layout = read_frame_layout(PooledRangeSource(url, client))
"""
from __future__ import annotations

import re

import httpx

from backend.adapters.fits_layout import RangeNotSupported


def make_client(workers: int = 32, timeout: float = 30.0) -> httpx.Client:
    """One client for the whole run. HTTP/1.1 with keep-alive, at least one connection per
    worker thread, so threads never queue for a connection."""
    n = max(4, workers)
    return httpx.Client(limits=httpx.Limits(max_connections=n, max_keepalive_connections=n),
                        timeout=timeout)


class PooledRangeSource:
    def __init__(self, url: str, client: httpx.Client):
        self.url, self.client = url, client
        self._size: int | None = None
        self.requests = 0
        self.bytes_read = 0

    @property
    def size(self) -> int:
        if self._size is None:
            self.read(0, 1)
        assert self._size is not None
        return self._size

    def read(self, start: int, length: int) -> bytes:
        """Bytes [start, start + length), fewer at the end of the file. Past the end of the file
        (HTTP 416) returns b"", which callers treat as 'ran off the end'. Server errors and
        dropped connections raise httpx errors, which the catalogue builder retries."""
        r = self.client.get(self.url, headers={"Range": f"bytes={start}-{start + length - 1}"})
        if r.status_code == 416:
            return b""
        if r.status_code == 200:
            raise RangeNotSupported("expected HTTP 206, got 200")
        r.raise_for_status()
        m = re.match(r"bytes \d+-\d+/(\d+)", r.headers.get("Content-Range", ""))
        if m:
            self._size = int(m.group(1))
        self.requests += 1
        self.bytes_read += len(r.content)
        return r.content
