"""The HTTP API.

  POST   /flipbook?ra=..&dec=..       start (or join) a job. Returns a JobHandle at once.
  GET    /flipbook/{job}/stream       the job's events as server-sent events
  GET    /flipbook/{job}/events       the same events by polling: ?after=N&wait=SECONDS
  DELETE /flipbook/{job}              cancel
  GET    /flipbook?ra=..&dec=..       shortcut: start (or join) and stream in one request
  GET    /frames/{key}.png            a rendered frame (immutable, cache it forever)
  GET    /overlay?ra=..&dec=..&fov=.. Gaia stars on the flipbook's picture, as JSON
  GET    /difference?ra&dec&fov&a=<frame id>&b=<frame id>  B minus A in flux units, as a PNG
  GET    /flags?ra&dec&fov&frame=<frame id>[&orient=detector]  the frame's flagged pixels, coloured
  GET    /coverage?ra&dec          every frame of the spot, grouped in visits like the flipbook
  GET    /measure?ra&dec               light curve and spectrum of any position (poll it)
  GET    /movers?ra&dec&fov[&visit=N]  search one visit for moving objects, checked with SkyBoT
  GET    /health, /fixtures           status; demo positions when running on fixtures

Query parameters are the URL state of the page (see contracts.url_state): ra, dec, fov, view,
det, lmin, lmax, cap, zodi, release, orient, fill. Everything except ra and dec is optional.
view=spectral gives the Wavelengths tab: the same job and events, frames sorted by wavelength.

Streaming behind proxies: the stream sends X-Accel-Buffering: no, a 2 KB preamble, and a
heartbeat event every heartbeat_s. If a proxy buffers anyway, the client starts a job with POST
(plain JSON, never buffered for long) and polls instead of streaming.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError

from backend.adapters.cache import UpstreamError
from backend.adapters.lookups import WebLookups
from backend.adapters.skybot import skybot_cone
from backend.jobs.measure import MEASURE_VERSION, measure_position
from backend.jobs.movers import MOVERS_VERSION, find_movers
from backend.jobs.preview import RENDER_VERSION, stamp_size_px
from backend.jobs.refine import REFINE_VERSION
from backend.pipeline.clean import clean_stamp
from backend.pipeline.colour import rgba_png
from backend.pipeline.difference import difference, render_difference
from backend.pipeline.flagmap import FLAGMAP_VERSION, bit_counts, category_grid, render_categories
from backend.pipeline.flagmap import summary as flag_summary
from backend.pipeline.overlay import cone_radius_arcmin, project_sources
from backend.pipeline.render import _png, regrid_cleaned
from backend.pipeline.select import cluster_visits
from backend.server.deps import build_deps, make_cache
from backend.server.jobs import Job, JobRunner, NoData
from backend.server.settings import Settings
from backend.server.sse import event_stream
from backend.server.targets import target_list
from contracts import (
    CONTRACT_VERSION,
    ErrorCode,
    FlipbookRequest,
    JobHandle,
    PollResponse,
    SkyPosition,
    cache_key,
)
from contracts.interfaces import Lookups
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


def parse_overlay(request: Request) -> tuple[SkyPosition, float]:
    """ra, dec and fov (arcmin, default 5) of an /overlay request."""
    q = request.query_params
    for name in ("ra", "dec"):
        if name not in q:
            raise ApiError(422, ErrorCode.BAD_COORDS.value, f"Missing parameter: {name}.")
    try:
        ra, dec, fov = float(q["ra"]), float(q["dec"]), float(q.get("fov", "5"))
    except ValueError as e:
        raise ApiError(422, ErrorCode.BAD_COORDS.value,
                       "ra, dec and fov must be numbers.") from e
    if not 0 <= ra < 360:
        raise ApiError(422, ErrorCode.BAD_COORDS.value, "ra must be from 0 up to 360.")
    if not -90 <= dec <= 90:
        raise ApiError(422, ErrorCode.BAD_COORDS.value, "dec must be from -90 to 90.")
    if not 0 < fov <= 120:
        raise ApiError(422, ErrorCode.BAD_COORDS.value,
                       "fov must be above 0 and at most 120 arcmin.")
    return SkyPosition(ra=ra, dec=dec), fov


def create_app(settings: Settings | None = None,
               lookups: Lookups | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        cache = make_cache(settings)
        deps = await build_deps(settings, cache)
        runner = JobRunner(deps, settings)
        reaper = asyncio.create_task(runner.reap_forever())
        app.state.settings, app.state.cache = settings, cache
        app.state.deps, app.state.runner = deps, runner
        own_lookups = lookups is None      # one we were handed is the caller's to close
        app.state.lookups = WebLookups(cache=cache) if own_lookups else lookups
        try:
            yield
        finally:
            reaper.cancel()
            if (c := measure_res.get("client")) is not None:
                c.close()
            await runner.shutdown()
            await deps.aclose()
            if own_lookups:
                await app.state.lookups.aclose()

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
        # the render versions say which picture code this process loaded (after a git pull the
        # server must be restarted, or it keeps drawing with the old code)
        return {"status": "ok", "contract": CONTRACT_VERSION, "source": settings.source,
                "render_version": RENDER_VERSION, "refine_version": REFINE_VERSION,
                **runner_of(request).stats()}

    @app.get("/fixtures")
    async def fixtures(request: Request) -> list[dict]:
        """Demo positions that have data when the server runs on fixtures (empty otherwise)."""
        return request.app.state.deps.listing()

    @app.get("/targets")
    async def targets() -> list[dict]:
        return target_list()

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

    @app.get("/overlay")
    async def overlay(request: Request) -> Response:
        """Gaia DR3 stars on the picture of a flipbook centred on (ra, dec): pixel position,
        fraction of the picture and velocity in px/yr (see backend/pipeline/overlay.py)."""
        pos, fov = parse_overlay(request)
        try:
            sources = await request.app.state.lookups.gaia_cone(pos, cone_radius_arcmin(fov))
        except UpstreamError as e:
            raise ApiError(503 if e.retryable else 502, e.code.value, e.message) from e
        body = await asyncio.to_thread(project_sources, sources, pos.ra, pos.dec, fov)
        return JSONResponse(body.to_json(), headers={"Cache-Control": "public, max-age=3600"})

    @app.get("/difference")
    async def difference_route(request: Request) -> Response:
        """B minus A for two frames of the field centred on (ra, dec), in flux units, as a
        picture (grey is no change) plus the numbers needed to read it (pipeline/difference)."""
        import numpy as np
        pos, fov = parse_overlay(request)
        q = request.query_params
        a_id, b_id = q.get("a", ""), q.get("b", "")
        if not a_id or not b_id or len(a_id) > 200 or len(b_id) > 200:
            raise ApiError(422, ErrorCode.BAD_COORDS.value, "Give two frames: a and b.")
        if a_id == b_id:
            raise ApiError(422, ErrorCode.BAD_COORDS.value, "Pick two different frames.")
        zodi = q.get("zodi", "1") != "0"
        req = FlipbookRequest(ra=pos.ra, dec=pos.dec, fov_arcmin=fov, use_zodi=zodi)
        try:
            deps = await request.app.state.deps(req)
            frames = {f.frame_id: f for f in await deps.index.frames_at(pos)}
        except NoData as e:
            raise ApiError(404, ErrorCode.NO_COVERAGE.value, str(e)) from e
        except UpstreamError as e:
            raise ApiError(503 if e.retryable else 502, e.code.value, e.message) from e
        fa, fb = frames.get(a_id), frames.get(b_id)
        if fa is None or fb is None:
            raise ApiError(404, ErrorCode.INTERNAL.value,
                           "One of those frames does not cover this position.")
        cross = q.get("cross", "0") == "1"
        if fa.detector != fb.detector and not cross:
            raise ApiError(422, ErrorCode.INTERNAL.value,
                           "These frames are from different detectors (different bandpass and "
                           "calibration). Tick 'Allow different detectors' to subtract anyway.")
        size = stamp_size_px(req)
        cache = request.app.state.cache
        args = dict(frame_id=f"{a_id}~{b_id}", release=fa.release, version=fa.version,
                    ra=pos.ra, dec=pos.dec, size_px=size,
                    params={"fov": fov, "zodi": zodi, "render": RENDER_VERSION, "diff": 1})
        png_key, meta_key = cache_key(kind="diff-png", **args), cache_key(kind="diff-meta", **args)
        hit = await cache.get(meta_key)
        if hit:
            return JSONResponse(json.loads(hit), headers={"Cache-Control": "public, max-age=3600"})
        try:
            sa, sb = await asyncio.gather(deps.stamps.fetch(fa, pos, size // 2),
                                          deps.stamps.fetch(fb, pos, size // 2))
        except UpstreamError as e:
            raise ApiError(503 if e.retryable else 502, e.code.value, e.message) from e

        def work() -> tuple[bytes, dict]:
            grids = []
            for st in (sa, sb):
                c = regrid_cleaned(clean_stamp(st, use_zodi=zodi, fill=False), st,
                                   pos.ra, pos.dec, size)
                if c is None:
                    raise ValueError("a frame has no sky coordinates")
                grids.append(c)
            d, noise = difference(*grids)
            gray, covered, white = render_difference(d, noise)
            png = _png(np.ascontiguousarray(np.flipud(gray)),
                       np.ascontiguousarray(np.flipud(covered)))
            lams = []
            for fr, st in ((fa, sa), (fb, sb)):
                ny, nx = st.image.shape
                lams.append(float(deps.wavelength.lambda_um(fr, st.x0 + nx / 2, st.y0 + ny / 2)))
            la, lb = lams
            return png, {
                "png_url": f"/frames/{png_key}.png",
                "width": gray.shape[1], "height": gray.shape[0],
                "detector": fa.detector, "detector_b": fb.detector,
                "cross_detector": fa.detector != fb.detector, "a": a_id, "b": b_id,
                "lambda_a_um": la, "lambda_b_um": lb,
                "lambda_diff_pct": round(abs(lb - la) / ((la + lb) / 2) * 100, 2),
                "t_a_mjd": fa.t_mid, "t_b_mjd": fb.t_mid,
                "noise": noise, "white": white, "unit": "MJy/sr",
                "covered_fraction": round(float(covered.mean()), 4)}
        try:
            png, meta = await asyncio.to_thread(work)
        except ValueError as e:
            raise ApiError(422, ErrorCode.INTERNAL.value,
                           f"Cannot subtract these frames: {e}.") from e
        await cache.put(png_key, png)
        await cache.put(meta_key, json.dumps(meta).encode())
        return JSONResponse(meta, headers={"Cache-Control": "public, max-age=3600"})

    @app.get("/coverage")
    async def coverage_route(request: Request) -> Response:
        """When SPHEREx looked at this spot: every frame (mid time, detector) and the visits,
        cut the same way the flipbook's selector cuts them (select.cluster_visits)."""
        pos, _ = parse_overlay(request)
        try:
            deps = await request.app.state.deps(FlipbookRequest(ra=pos.ra, dec=pos.dec))
            frames = await deps.index.frames_at(pos)
        except NoData:
            frames = []
        except UpstreamError as e:
            raise ApiError(503 if e.retryable else 502, e.code.value, e.message) from e
        by_id = {f.frame_id: f for f in frames}
        visits = []
        for v in cluster_visits(frames):
            fs = [by_id[i] for i in v.frame_ids]
            visits.append({"index": v.visit_index, "t_start_mjd": v.t_start,
                           "t_end_mjd": v.t_end, "n_frames": len(fs),
                           "detectors": sorted({f.detector for f in fs})})
        body = {"ra": pos.ra, "dec": pos.dec, "n_frames": len(frames), "visits": visits,
                "frames": [[round((f.t_start + f.t_end) / 2, 5), f.detector]
                           for f in sorted(frames, key=lambda f: f.t_start)]}
        return JSONResponse(body, headers={"Cache-Control": "public, max-age=600"})

    @app.get("/flags")
    async def flags_route(request: Request) -> Response:
        """Where the pipeline flagged pixels in one frame of the field, as an RGBA layer laid out
        like the frame's picture, plus the fraction of each group (pipeline/flagmap.py)."""
        pos, fov = parse_overlay(request)
        q = request.query_params
        fid = q.get("frame", "")
        if not fid or len(fid) > 200:
            raise ApiError(422, ErrorCode.BAD_COORDS.value, "Give a frame.")
        north = q.get("orient", "north") != "detector"
        req = FlipbookRequest(ra=pos.ra, dec=pos.dec, fov_arcmin=fov)
        try:
            deps = await request.app.state.deps(req)
            frames = {f.frame_id: f for f in await deps.index.frames_at(pos)}
        except NoData as e:
            raise ApiError(404, ErrorCode.NO_COVERAGE.value, str(e)) from e
        except UpstreamError as e:
            raise ApiError(503 if e.retryable else 502, e.code.value, e.message) from e
        fr = frames.get(fid)
        if fr is None:
            raise ApiError(404, ErrorCode.INTERNAL.value,
                           "Flags exist only for single catalogue frames that cover this "
                           "position (not for co-adds or the colour picture).")
        size = stamp_size_px(req)
        cache = request.app.state.cache
        args = dict(frame_id=fid, release=fr.release, version=fr.version, ra=pos.ra,
                    dec=pos.dec, size_px=size,
                    params={"fov": fov, "north": north, "flags": FLAGMAP_VERSION})
        png_key, meta_key = cache_key(kind="flags-png", **args), cache_key(kind="flags", **args)
        hit = await cache.get(meta_key)
        if hit:
            return JSONResponse(json.loads(hit), headers={"Cache-Control": "public, max-age=3600"})
        try:
            st = await deps.stamps.fetch(fr, pos, size // 2)
        except UpstreamError as e:
            raise ApiError(503 if e.retryable else 502, e.code.value, e.message) from e

        def work() -> tuple[bytes, dict]:
            from backend.pipeline.render import preview_size_px
            cat, covered = category_grid(st.flags, st, pos.ra, pos.dec, preview_size_px(fov),
                                         north)
            rgb, drawn = render_categories(cat)
            return rgba_png(rgb, drawn), {
                "png_url": f"/frames/{png_key}.png", "frame_id": fid,
                "width": cat.shape[1], "height": cat.shape[0],
                "categories": flag_summary(cat, covered), "bits": bit_counts(st.flags)}
        try:
            png, meta = await asyncio.to_thread(work)
        except ValueError as e:
            raise ApiError(422, ErrorCode.INTERNAL.value, f"Cannot map the flags: {e}.") from e
        await cache.put(png_key, png)
        await cache.put(meta_key, json.dumps(meta).encode())
        return JSONResponse(meta, headers={"Cache-Control": "public, max-age=3600"})

    measure_runs: dict[str, dict] = {}
    measure_res: dict = {}          # the solid-angle map and its HTTP client, made on first use

    @app.get("/measure")
    async def measure_route(request: Request) -> Response:
        """Light curve and rough spectrum of whatever is at ra, dec (backend/jobs/measure.py).
        It takes a while, so the first call starts the run and every call returns its state:
        {status: running, done, total} or {status: done, result} or {status: error, message}.
        Poll it (the tunnel does not stream). A finished result is cached."""
        pos, _ = parse_overlay(request)
        pos = SkyPosition(ra=round(pos.ra, 5), dec=round(pos.dec, 5))
        key = cache_key(kind="measure", frame_id="measure", release="all",
                        version=f"m{MEASURE_VERSION}", ra=pos.ra, dec=pos.dec, size_px=0,
                        params={"render": RENDER_VERSION})
        cache = request.app.state.cache
        hit = await cache.get(key)
        if hit:
            return JSONResponse({"status": "done", "result": json.loads(hit)},
                                headers={"Cache-Control": "no-store"})
        run = measure_runs.get(key)
        if run is None:
            deps = request.app.state.deps
            if not hasattr(deps, "index") or settings.source != "catalogue":
                raise ApiError(501, ErrorCode.INTERNAL.value,
                               "Measuring needs the real catalogue (--source catalogue).")
            req = FlipbookRequest(ra=pos.ra, dec=pos.dec, fov_arcmin=5, use_zodi=True)
            run = measure_runs[key] = {"status": "running", "done": 0, "total": 0}

            def progress(done: int, total: int) -> None:
                run["done"], run["total"] = done, total

            async def go() -> None:
                try:
                    d = await deps(req)
                    frames = await d.index.frames_at(pos, margin_px=32)
                    if not frames:
                        raise NoData("No SPHEREx frame covers this position.")
                    if "solid" not in measure_res:
                        from backend.adapters.pooled_source import PooledRangeSource, make_client
                        from backend.adapters.solid_angle import SolidAngleMap
                        client = make_client(32)
                        measure_res["client"] = client
                        measure_res["solid"] = SolidAngleMap(
                            opener=lambda url: PooledRangeSource(url, client))
                    body = await measure_position(pos, frames, stamps=d.stamps,
                                                  wavelength=d.wavelength,
                                                  solid=measure_res["solid"], on_progress=progress)
                    await cache.put(key, json.dumps(body).encode())
                    run["status"] = "done"
                except NoData as e:
                    run.update(status="error", message=str(e))
                except Exception as e:
                    log.exception("measure failed")
                    run.update(status="error", message=f"The measurement failed: {e}")

            run["task"] = asyncio.ensure_future(go())
        out = {k: v for k, v in run.items() if k != "task"}
        if run["status"] == "done":
            measure_runs.pop(key, None)
            hit = await cache.get(key)
            out = {"status": "done", "result": json.loads(hit)} if hit else out
        elif run["status"] == "error":
            measure_runs.pop(key, None)       # the next call tries again
        return JSONResponse(out, headers={"Cache-Control": "no-store"})

    movers_inflight: dict[str, asyncio.Task] = {}

    @app.get("/movers")
    async def movers_route(request: Request) -> Response:
        """Search one visit of the field for moving objects (backend/jobs/movers.py): tracks,
        each marked with its SkyBoT name or as a candidate, the known objects that crossed the
        field, and a picture (the best burst S/N per pixel) to draw the tracks on."""
        import httpx
        import numpy as np
        pos, fov = parse_overlay(request)
        q = request.query_params
        visit: int | None = None
        if q.get("visit"):
            try:
                visit = int(q["visit"])
            except ValueError as e:
                raise ApiError(422, ErrorCode.BAD_COORDS.value, "visit must be a number.") from e
        zodi = q.get("zodi", "1") != "0"
        req = FlipbookRequest(ra=pos.ra, dec=pos.dec, fov_arcmin=fov, use_zodi=zodi)
        args = dict(frame_id=f"movers~{visit if visit is not None else 'auto'}", release="all",
                    version=f"m{MOVERS_VERSION}", ra=pos.ra, dec=pos.dec, size_px=0,
                    params={"fov": fov, "zodi": zodi, "render": RENDER_VERSION})
        png_key, meta_key = cache_key(kind="movers-png", **args), cache_key(kind="movers", **args)
        cache = request.app.state.cache
        hit = await cache.get(meta_key)
        if hit:
            return JSONResponse(json.loads(hit), headers={"Cache-Control": "public, max-age=3600"})

        async def run() -> dict:
            try:
                deps = await request.app.state.deps(req)
                frames = await deps.index.frames_at(pos)
            except NoData as e:
                raise ApiError(404, ErrorCode.NO_COVERAGE.value, str(e)) from e
            async with httpx.AsyncClient() as client:
                async def sky(ra: float, dec: float, r: float, t: float) -> list[dict]:
                    return await skybot_cone(client, ra, dec, r, t)
                body, img, covered = await find_movers(pos, fov, frames, deps.stamps.fetch, sky,
                                                       visit=visit, use_zodi=zodi)
            png = await asyncio.to_thread(_png, np.ascontiguousarray(img),
                                          np.ascontiguousarray(covered))
            body["png_url"] = f"/frames/{png_key}.png"
            await cache.put(png_key, png)
            if body["skybot_checked"]:      # a result missing SkyBoT is not kept: ask again later
                await cache.put(meta_key, json.dumps(body).encode())
            return body

        task = movers_inflight.get(meta_key)
        if task is None:        # identical searches share one run
            task = movers_inflight[meta_key] = asyncio.ensure_future(run())
            task.add_done_callback(lambda _t: movers_inflight.pop(meta_key, None))
        try:
            body = await asyncio.shield(task)
        except ValueError as e:
            raise ApiError(422, ErrorCode.INTERNAL.value, f"Cannot search this field: {e}.") from e
        except UpstreamError as e:
            raise ApiError(503 if e.retryable else 502, e.code.value, e.message) from e
        return JSONResponse(body, headers={"Cache-Control": "public, max-age=3600"})

    return app
