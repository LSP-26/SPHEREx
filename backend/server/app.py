"""The HTTP API.

  POST   /flipbook?ra=..&dec=..       start (or join) a job. Returns a JobHandle at once.
  GET    /flipbook/{job}/stream       the job's events as server-sent events
  GET    /flipbook/{job}/events       the same events by polling: ?after=N&wait=SECONDS
  DELETE /flipbook/{job}              cancel
  GET    /flipbook?ra=..&dec=..       shortcut: start (or join) and stream in one request
  GET    /frames/{key}.png            a rendered frame (immutable, cache it forever)
  GET    /health, /fixtures           status; demo positions when running on fixtures

Query parameters are the URL state of the page (see contracts.url_state): ra, dec, fov, view,
det, lmin, lmax, cap, zodi, release. Everything except ra and dec is optional.

Streaming behind proxies: the stream sends X-Accel-Buffering: no, a 2 KB preamble, and a
heartbeat event every heartbeat_s. If a proxy buffers anyway, the client starts a job with POST
(plain JSON, never buffered for long) and polls instead of streaming.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError

from backend.server.deps import build_deps, make_cache
from backend.server.jobs import Job, JobRunner
from backend.server.settings import Settings
from backend.server.sse import event_stream
from contracts import CONTRACT_VERSION, ErrorCode, FlipbookRequest, JobHandle, PollResponse
from contracts.url_state import from_query

log = logging.getLogger(__name__)

FRAME_KEY = re.compile(r"[0-9a-f]{64}")
SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}
SSE_RESPONSE = {200: {"description": "Server-sent events. Each event's data is the JSON of a "
                                     "contracts Event (see Event.json).",
                      "content": {"text/event-stream": {"schema": {"type": "string"}}}},
                204: {"description": "The job is finished and the client has every event "
                                     "(stops EventSource reconnecting)."}}


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message


def parse_request(request: Request) -> FlipbookRequest:
    try:
        return from_query(request.url.query)
    except ValidationError as e:
        why = "; ".join(f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}"
                        for err in e.errors())
        raise ApiError(422, ErrorCode.BAD_COORDS.value, f"Invalid parameters ({why}).") from e
    except ValueError as e:
        raise ApiError(422, ErrorCode.BAD_COORDS.value, f"Invalid parameters ({e}).") from e


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        cache = make_cache(settings)
        deps = await build_deps(settings, cache)
        runner = JobRunner(deps, settings)
        reaper = asyncio.create_task(runner.reap_forever())
        app.state.settings, app.state.cache = settings, cache
        app.state.deps, app.state.runner = deps, runner
        try:
            yield
        finally:
            reaper.cancel()
            await runner.shutdown()
            await deps.aclose()

    app = FastAPI(title="SPHEREx Flipbook API", version=CONTRACT_VERSION, lifespan=lifespan,
                  description=__doc__)
    app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins),
                       allow_methods=["GET", "POST", "DELETE"], allow_headers=["*"],
                       expose_headers=["X-Job-Id"])

    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status,
                            content={"code": exc.code, "message": exc.message})

    def runner_of(request: Request) -> JobRunner:
        return request.app.state.runner

    def job_or_404(request: Request, job_id: str) -> Job:
        job = runner_of(request).get(job_id)
        if job is None:
            raise ApiError(404, ErrorCode.INTERNAL.value,
                           "Unknown or expired job. Start a new one with POST /flipbook.")
        return job

    def stream_response(request: Request, job: Job) -> Response:
        try:
            after = int(request.headers.get("last-event-id", "-1")) + 1
        except ValueError:
            after = 0
        after = max(0, after)
        if job.finished and after >= len(job.events):
            return Response(status_code=204)
        return StreamingResponse(
            event_stream(job, after, settings.heartbeat_s), media_type="text/event-stream",
            headers={**SSE_HEADERS, "X-Job-Id": job.id})

    @app.get("/health")
    async def health(request: Request) -> dict:
        return {"status": "ok", "contract": CONTRACT_VERSION, "source": settings.source,
                **runner_of(request).stats()}

    @app.get("/fixtures")
    async def fixtures(request: Request) -> list[dict]:
        """Demo positions that have data when the server runs on fixtures (empty otherwise)."""
        return request.app.state.deps.listing()

    @app.post("/flipbook", response_model=JobHandle)
    async def start(request: Request) -> JobHandle:
        job = runner_of(request).submit(parse_request(request))
        return JobHandle(job_id=job.id, stream_url=f"/flipbook/{job.id}/stream",
                         poll_url=f"/flipbook/{job.id}/events", job_url=f"/flipbook/{job.id}")

    @app.get("/flipbook", responses=SSE_RESPONSE)
    async def start_and_stream(request: Request) -> Response:
        return stream_response(request, runner_of(request).submit(parse_request(request)))

    @app.get("/flipbook/{job_id}/stream", responses=SSE_RESPONSE)
    async def stream(job_id: str, request: Request) -> Response:
        return stream_response(request, job_or_404(request, job_id))

    @app.get("/flipbook/{job_id}/events", response_model=PollResponse)
    async def poll(job_id: str, request: Request, after: int = Query(0, ge=0),
                   wait: float = Query(0.0, ge=0.0, le=25.0)) -> PollResponse:
        """Events from index `after`. With wait > 0 and nothing new, waits up to that many
        seconds for something to arrive."""
        job = job_or_404(request, job_id)
        job.last_active = time.monotonic()
        if after >= len(job.events) and not job.finished and wait > 0:
            await job.wait_for(after, wait)
        events = job.events[after:after + 200]
        nxt = after + len(events)
        job.last_active = time.monotonic()
        return PollResponse(events=events, next=nxt, done=job.finished and nxt >= len(job.events))

    @app.delete("/flipbook/{job_id}", status_code=202)
    async def cancel(job_id: str, request: Request) -> dict:
        job = job_or_404(request, job_id)
        job.cancel.set()
        return {"job_id": job.id, "status": "cancelling" if not job.finished else "finished"}

    @app.get("/frames/{key}.png", response_class=Response,
             responses={200: {"content": {"image/png": {}}}, 404: {"description": "Not cached"}})
    async def frame(key: str, request: Request) -> Response:
        if not FRAME_KEY.fullmatch(key):
            raise ApiError(404, ErrorCode.INTERNAL.value, "No such frame.")
        png = await request.app.state.cache.get(key)
        if png is None:
            raise ApiError(404, ErrorCode.INTERNAL.value, "No such frame.")
        return Response(png, media_type="image/png", headers={
            "Cache-Control": "public, max-age=31536000, immutable", "ETag": f'"{key}"'})

    return app
