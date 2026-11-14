"""Server-sent events: wire format and the streaming generator.

Every event is a default 'message' event whose data is the JSON of a contracts Event (it has a
`type` field), so a client needs one onmessage handler. The SSE `id` is the event's index in the
job's list: after a dropped connection EventSource sends Last-Event-ID and the stream resumes
there. Heartbeats sent while idle are real data events (EventSource never shows comments, and
the client uses them to tell a live stream from a buffered one) and carry no id.
"""
from __future__ import annotations

import time
from collections.abc import AsyncIterator

from backend.server.jobs import Job, is_terminal
from contracts import Event, HeartbeatEvent

# Some proxies hold back a response until a few KB have arrived. This is sent first so that
# the first real event is not stuck behind it. retry: tells EventSource how long to wait
# before reconnecting.
PREAMBLE = b"retry: 3000\n\n: " + b" " * 2048 + b"\n\n"


def format_event(ev: Event, index: int | None = None) -> bytes:
    head = f"id: {index}\n" if index is not None else ""
    return f"{head}data: {ev.model_dump_json()}\n\n".encode()


async def event_stream(job: Job, after: int, heartbeat_s: float) -> AsyncIterator[bytes]:
    """Events of `job` from index `after`, then a heartbeat whenever nothing happens for
    heartbeat_s, until a terminal event (done or error) has been sent."""
    job.subscribers += 1
    try:
        yield PREAMBLE
        i = after
        while True:
            if i < len(job.events):
                ev = job.events[i]
                yield format_event(ev, i)
                job.last_active = time.monotonic()
                i += 1
                if is_terminal(ev):
                    return
                continue
            if job.finished:
                return
            if not await job.wait_for(i, heartbeat_s):
                yield format_event(HeartbeatEvent())
    finally:
        job.subscribers -= 1
        job.last_active = time.monotonic()
