"""A real uvicorn server on a random local port, and a small SSE client. Not mocks: the
streaming tests need real sockets to show that events arrive one by one."""
from __future__ import annotations

import json
import socket
import threading
import time
from dataclasses import dataclass

import httpx
import uvicorn

from backend.server.app import create_app
from backend.server.settings import Settings


class LiveServer:
    def __init__(self, settings: Settings):
        self.app = create_app(settings)
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.url = f"http://127.0.0.1:{self.sock.getsockname()[1]}"
        self.server = uvicorn.Server(uvicorn.Config(self.app, log_level="warning", lifespan="on"))
        self.thread = threading.Thread(target=self.server.run, kwargs={"sockets": [self.sock]},
                                       daemon=True)

    def __enter__(self) -> LiveServer:
        self.thread.start()
        deadline = time.time() + 10
        while not self.server.started:
            if time.time() > deadline:
                raise RuntimeError("server did not start")
            time.sleep(0.01)
        return self

    def __exit__(self, *exc) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)
        self.sock.close()

    @property
    def runner(self):
        return self.app.state.runner


@dataclass
class Sse:
    id: int | None
    data: dict
    at: float              # seconds since the request was sent


def read_sse(client: httpx.Client, url: str, headers: dict | None = None,
             limit: int | None = None,
             params: dict | None = None) -> tuple[httpx.Response, list[Sse]]:
    """Read data events until the stream ends (or `limit` events), noting when each arrived."""
    out: list[Sse] = []
    t0 = time.monotonic()
    with client.stream("GET", url, headers=headers, params=params) as r:
        if r.status_code != 200:
            return r, out
        ev_id: int | None = None
        for line in r.iter_lines():
            if line.startswith("id:"):
                ev_id = int(line[3:])
            elif line.startswith("data:"):
                out.append(Sse(ev_id, json.loads(line[5:]), time.monotonic() - t0))
                ev_id = None
                if limit is not None and len(out) >= limit:
                    break
    return r, out
