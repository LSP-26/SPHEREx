import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from backend.adapters.fits_layout import FileSource, HttpRangeSource, RangeNotSupported
from backend.adapters.frame_layout import read_frame_layout
from backend.adapters.pooled_source import PooledRangeSource, make_client
from backend.tests.unit.compressed_fixtures import write_frame


class KeepAliveServer:
    """A range server that speaks HTTP/1.1 keep-alive and counts TCP connections."""

    def __init__(self, data: bytes, honour_range: bool = True, status: int = 0):
        self.connections = 0
        outer = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self):
                rng = self.headers.get("Range")
                if status:
                    self.send_response(status)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if rng and honour_range:
                    a, b = rng.removeprefix("bytes=").split("-")
                    a, b = int(a), int(b)
                    if a >= len(data):
                        self.send_response(416)
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    b = min(b, len(data) - 1)
                    body = data[a:b + 1]
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {a}-{b}/{len(data)}")
                else:
                    body = data
                    self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        class S(ThreadingHTTPServer):
            def process_request(self, request, client_address):
                outer.connections += 1
                super().process_request(request, client_address)

        self.srv = S(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_port}/f.fits"

    def close(self):
        self.srv.shutdown()


@pytest.fixture
def frame_bytes(tmp_path):
    path = tmp_path / "f.fits"
    write_frame(path)
    return path, path.read_bytes()


@pytest.fixture
def server(frame_bytes):
    s = KeepAliveServer(frame_bytes[1])
    yield s
    s.close()


def test_reads_the_right_bytes_and_learns_the_size(server, frame_bytes):
    data = frame_bytes[1]
    with make_client(4) as client:
        src = PooledRangeSource(server.url, client)
        assert src.read(100, 50) == data[100:150]
        assert src.size == len(data)
        assert src.read(len(data) - 10, 100) == data[-10:]        # clipped at the end
        assert src.requests == 2 and src.bytes_read == 60      # size came with the first read


def test_size_alone_costs_one_byte(server, frame_bytes):
    with make_client(4) as client:
        src = PooledRangeSource(server.url, client)
        assert src.size == len(frame_bytes[1]) and src.bytes_read == 1


def test_connections_are_reused_unlike_urllib(server):
    with make_client(4) as client:
        src = PooledRangeSource(server.url, client)
        for i in range(20):
            src.read(i * 10, 10)
    assert server.connections == 1
    before = server.connections
    plain = HttpRangeSource(server.url)
    for i in range(20):
        plain.read(i * 10, 10)
    assert server.connections - before == 20      # urllib: a new connection every time


def test_past_the_end_is_empty_not_an_error(server, frame_bytes):
    with make_client(4) as client:
        assert PooledRangeSource(server.url, client).read(len(frame_bytes[1]) + 5, 10) == b""


def test_server_that_ignores_range_is_detected(frame_bytes):
    s = KeepAliveServer(frame_bytes[1], honour_range=False)
    try:
        with make_client(2) as client, pytest.raises(RangeNotSupported):
            PooledRangeSource(s.url, client).read(0, 10)
    finally:
        s.close()


def test_server_errors_raise_for_the_builder_to_retry(frame_bytes):
    s = KeepAliveServer(frame_bytes[1], status=503)
    try:
        with make_client(2) as client, pytest.raises(httpx.HTTPStatusError):
            PooledRangeSource(s.url, client).read(0, 10)
    finally:
        s.close()


def test_the_layout_walk_works_through_it_with_a_single_connection(server, frame_bytes):
    path, _ = frame_bytes
    with make_client(4) as client:
        got = read_frame_layout(PooledRangeSource(server.url, client), first_chunk=8192)
    assert got.planes == read_frame_layout(FileSource(path), first_chunk=8192).planes
    assert server.connections == 1


def test_many_threads_share_the_pool(server, frame_bytes):
    from concurrent.futures import ThreadPoolExecutor
    path, _ = frame_bytes
    ref = read_frame_layout(FileSource(path), first_chunk=8192).planes
    with make_client(8) as client, ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda _: read_frame_layout(
            PooledRangeSource(server.url, client), first_chunk=8192).planes, range(40)))
    assert all(r == ref for r in results)
    assert server.connections <= 8 + 2        # about one per thread, not one per request
