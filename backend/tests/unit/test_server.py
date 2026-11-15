import time

import httpx
import pytest
from pydantic import TypeAdapter

from backend.server.settings import Settings
from backend.tests.unit.server_harness import LiveServer, read_sse
from contracts import Event, JobHandle, PollResponse

POS = "ra=10&dec=0"   # synthetic fixture: 6 frames, 2 visits, detectors 1 and 2, so 4 slots
EVENT = TypeAdapter(Event)


def settings(**kw):
    return Settings(source="fixtures", cache_dir=None, **kw)


@pytest.fixture
def live():
    with LiveServer(settings(fake_latency_s=0.15)) as s, httpx.Client(base_url=s.url,
                                                                       timeout=20) as c:
        yield s, c


def types(events):
    return [e.data["type"] for e in events]


def test_health_fixtures_and_openapi(live):
    s, c = live
    h = c.get("/health").json()
    assert h["status"] == "ok" and h["source"] == "fixtures" and h["contract"] == "0.3.0"
    assert h["render_version"] >= 10 and h["refine_version"] >= 4
    fx = c.get("/fixtures").json()
    assert fx[0]["name"] == "synthetic_test" and fx[0]["ra"] == 10.0 and fx[0]["synthetic"]
    paths = c.get("/openapi.json").json()["paths"]
    for p in ("/flipbook", "/flipbook/{job_id}/stream", "/flipbook/{job_id}/events",
              "/flipbook/{job_id}", "/frames/{key}.png", "/health"):
        assert p in paths


def test_events_arrive_one_by_one_in_the_contract_order(live):
    s, c = live
    r, events = read_sse(c, f"/flipbook?{POS}")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    assert r.headers["x-accel-buffering"] == "no" and "no-cache" in r.headers["cache-control"]
    assert r.headers["x-job-id"]
    parsed = [EVENT.validate_python(e.data) for e in events]      # every event is a valid Event
    kinds = types(events)
    assert kinds[:3] == ["heartbeat", "plan", "progress"] and kinds[-1] == "done"
    assert kinds.count("frame") == 4 and parsed[-1].n_frames == 4
    assert [e.id for e in events] == list(range(len(events)))     # ids index the job's list
    frames = [e for e in events if e.data["type"] == "frame"]
    # streamed, not delivered in one burst at the end: the frames arrive 0.3 to 0.9 s in
    assert frames[-1].at - frames[0].at > 0.35
    assert events[0].at < 0.25 and events[-1].at > frames[0].at + 0.35


def test_sse_preamble_and_retry_come_first(live):
    s, c = live
    with c.stream("GET", f"/flipbook?{POS}") as r:
        first = next(r.iter_bytes())
    assert first.startswith(b"retry: 3000") and len(first) >= 2048


def test_frames_are_served_as_immutable_pngs(live):
    s, c = live
    _, events = read_sse(c, f"/flipbook?{POS}")
    url = next(e.data["png_url"] for e in events if e.data["type"] == "frame")
    r = c.get(url)
    assert r.status_code == 200 and r.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert r.headers["content-type"] == "image/png"
    assert "immutable" in r.headers["cache-control"] and r.headers["etag"]
    assert c.get("/frames/" + "0" * 64 + ".png").status_code == 404
    assert c.get("/frames/not-a-key.png").status_code == 404
    assert c.get("/frames/../etc/passwd.png").status_code == 404


def test_post_gives_a_handle_and_polling_matches_the_stream(live):
    s, c = live
    handle = JobHandle(**c.post(f"/flipbook?{POS}").json())
    assert handle.stream_url == f"/flipbook/{handle.job_id}/stream"
    _, streamed = read_sse(c, handle.stream_url)
    polled, after = [], 0
    for _ in range(50):
        body = PollResponse(**c.get(handle.poll_url, params={"after": after, "wait": 5}).json())
        polled += body.events
        after = body.next
        if body.done:
            break
    assert body.done and len(polled) == len(streamed)
    assert [e.model_dump(mode="json") for e in polled] == [e.data for e in streamed]


def test_polling_waits_for_news_instead_of_returning_empty(live):
    s, c = live
    handle = JobHandle(**c.post(f"/flipbook?{POS}").json())
    t = time.monotonic()
    body = c.get(handle.poll_url, params={"after": 10_000, "wait": 0.3}).json()
    assert body["events"] == [] and not body["done"] and 0.25 < time.monotonic() - t < 2


def test_reconnecting_resumes_after_the_last_event_id(live):
    s, c = live
    handle = JobHandle(**c.post(f"/flipbook?{POS}").json())
    _, head = read_sse(c, handle.stream_url, limit=4)
    assert [e.id for e in head] == [0, 1, 2, 3]
    r, rest = read_sse(c, handle.stream_url, headers={"Last-Event-ID": "3"})
    assert rest[0].id == 4 and types(rest)[-1] == "done"
    # a finished job with nothing left to send tells EventSource to stop reconnecting
    r, nothing = read_sse(c, handle.stream_url, headers={"Last-Event-ID": str(rest[-1].id)})
    assert r.status_code == 204 and nothing == []


def test_identical_requests_share_one_job(live):
    s, c = live
    a = c.post(f"/flipbook?{POS}").json()["job_id"]
    b = c.post(f"/flipbook?{POS}").json()["job_id"]
    d = c.post(f"/flipbook?{POS}&fov=8").json()["job_id"]
    assert a == b and a != d


def test_position_without_data_is_a_clear_error_event(live):
    s, c = live
    r, events = read_sse(c, "/flipbook?ra=200&dec=-60")
    assert types(events) == ["heartbeat", "error"]
    assert events[1].data["code"] == "no_coverage" and "RA 10" in events[1].data["message"]
    assert events[1].data["retryable"] is False


@pytest.mark.parametrize("query", ["ra=400&dec=0", "ra=abc&dec=0", "dec=5", "ra=1&dec=95",
                                   f"{POS}&nonsense=1", f"{POS}&view=sideways", ""])
def test_bad_parameters_are_a_422_with_a_code(live, query):
    s, c = live
    for r in (c.post(f"/flipbook?{query}"), c.get(f"/flipbook?{query}")):
        assert r.status_code == 422
        assert r.json()["code"] == "bad_coords" and r.json()["message"]


def test_unknown_job_is_a_404(live):
    s, c = live
    for r in (c.get("/flipbook/nope/stream"), c.get("/flipbook/nope/events"),
              c.delete("/flipbook/nope")):
        assert r.status_code == 404 and r.json()["message"]


def test_cancel_stops_the_job_with_a_cancelled_event(live):
    s, c = live
    handle = JobHandle(**c.post(f"/flipbook?{POS}&fov=7").json())
    time.sleep(0.2)
    assert c.delete(handle.job_url).status_code == 202
    body = PollResponse(**c.get(handle.poll_url, params={"after": 0, "wait": 5}).json())
    deadline = time.time() + 5
    while not body.done and time.time() < deadline:
        body = PollResponse(**c.get(handle.poll_url, params={"after": 0, "wait": 1}).json())
    assert body.done and body.events[-1].code.value == "cancelled"
    assert c.post(f"/flipbook?{POS}&fov=7").json()["job_id"] != handle.job_id   # not reused


def test_cors_allows_any_origin_by_default(live):
    s, c = live
    r = c.get("/health", headers={"Origin": "https://example.org"})
    assert r.headers["access-control-allow-origin"] == "*"
    pre = c.options("/flipbook", headers={"Origin": "https://example.org",
                                          "Access-Control-Request-Method": "POST"})
    assert pre.status_code == 200 and "POST" in pre.headers["access-control-allow-methods"]


def test_idle_stream_sends_heartbeats_as_real_events():
    with LiveServer(settings(fake_latency_s=1.2, heartbeat_s=0.25)) as s, \
            httpx.Client(base_url=s.url, timeout=20) as c:
        _, events = read_sse(c, f"/flipbook?{POS}")
    beats = [e for e in events if e.data["type"] == "heartbeat"]
    assert len(beats) >= 4                      # one at the start, then every 0.25 s while idle
    assert all(b.id is None for b in beats[1:])  # idle heartbeats are not part of the job's list


def test_a_job_nobody_watches_is_cancelled():
    with LiveServer(settings(fake_latency_s=3.0, idle_cancel_s=0.4)) as s, \
            httpx.Client(base_url=s.url, timeout=20) as c:
        handle = JobHandle(**c.post(f"/flipbook?{POS}").json())
        time.sleep(1.5)                          # nobody streams or polls
        body = PollResponse(**c.get(handle.poll_url, params={"after": 0}).json())
        assert body.done and body.events[-1].code.value == "cancelled"


def test_a_watched_job_is_not_cancelled():
    with LiveServer(settings(fake_latency_s=0.3, idle_cancel_s=0.4)) as s, \
            httpx.Client(base_url=s.url, timeout=20) as c:
        _, events = read_sse(c, f"/flipbook?{POS}")
    assert types(events)[-1] == "done"


def test_disconnecting_stream_releases_the_subscriber():
    with LiveServer(settings(fake_latency_s=0.3)) as s, \
            httpx.Client(base_url=s.url, timeout=20) as c:
        handle = JobHandle(**c.post(f"/flipbook?{POS}").json())
        read_sse(c, handle.stream_url, limit=2)          # reads two events, then drops
        time.sleep(0.5)
        assert s.runner.get(handle.job_id).subscribers == 0


def test_more_jobs_than_slots_all_finish():
    with LiveServer(settings(fake_latency_s=0.05, max_running=1)) as s, \
            httpx.Client(base_url=s.url, timeout=30) as c:
        ids = [c.post(f"/flipbook?{POS}&fov={f}").json()["job_id"] for f in (3, 4, 5)]
        for job_id in ids:
            body = PollResponse(**c.get(f"/flipbook/{job_id}/events",
                                        params={"after": 0, "wait": 20}).json())
            while not body.done:
                body = PollResponse(**c.get(f"/flipbook/{job_id}/events",
                                            params={"after": body.next, "wait": 20}).json())
            assert body.events[-1].type == "done"


def test_difference_route_checks_its_input_and_subtracts_same_detector_frames(live):
    s, c = live
    _, events = read_sse(c, f"/flipbook?{POS}")
    labels = [e.data["label"] for e in events if e.data["type"] == "frame"]
    by_det: dict[int, list[str]] = {}
    for lab in labels:
        by_det.setdefault(lab["detector"], []).append(lab["frame_id"])
    base = "/difference?ra=10&dec=0&fov=5"
    assert c.get(base).status_code == 422                               # no frames named
    assert c.get(f"{base}&a={labels[0]['frame_id']}&b={labels[0]['frame_id']}").status_code == 422
    assert c.get(f"{base}&a=nope&b=nada").status_code == 404
    dets = sorted(by_det)
    if len(dets) > 1:                                                   # different detectors
        r = c.get(f"{base}&a={by_det[dets[0]][0]}&b={by_det[dets[1]][0]}")
        assert r.status_code == 422 and "detector" in r.json()["message"]
    pair = next((ids[:2] for ids in by_det.values() if len(ids) >= 2), None)
    if pair:
        r = c.get(f"{base}&a={pair[0]}&b={pair[1]}")
        assert r.status_code in (200, 422), r.text
        if r.status_code == 200:
            meta = r.json()
            assert meta["unit"] == "MJy/sr" and 0 < meta["covered_fraction"] <= 1
            assert c.get(meta["png_url"]).headers["content-type"] == "image/png"
            assert c.get(f"{base}&a={pair[0]}&b={pair[1]}").json() == meta     # cached, same answer


def test_flags_route_maps_a_frames_flagged_pixels(live):
    s, c = live
    _, events = read_sse(c, f"/flipbook?{POS}")
    fid = next(e.data["label"]["frame_id"] for e in events if e.data["type"] == "frame")
    base = "/flags?ra=10&dec=0&fov=5"
    assert c.get(base).status_code == 422
    assert c.get(f"{base}&frame=colour_v0").status_code == 404
    for orient in ("", "&orient=detector"):
        r = c.get(f"{base}&frame={fid}{orient}")
        assert r.status_code == 200, r.text
        meta = r.json()
        assert [k["key"] for k in meta["categories"]][:2] == ["hidden", "saturated"]
        assert all(0 <= k["fraction"] <= 1 for k in meta["categories"])
        png = c.get(meta["png_url"])
        assert png.headers["content-type"] == "image/png" and png.content[25] == 6  # RGBA
        assert c.get(f"{base}&frame={fid}{orient}").json() == meta


def test_coverage_route_lists_every_frame_in_visits(live):
    s, c = live
    body = c.get("/coverage?ra=10&dec=0").json()
    assert body["n_frames"] == 6 and len(body["frames"]) == 6   # the fixture: 6 frames, 2 visits
    assert len(body["visits"]) == 2 and sum(v["n_frames"] for v in body["visits"]) == 6
    assert body["frames"] == sorted(body["frames"])
    assert c.get("/coverage?ra=200&dec=-80").json()["n_frames"] == 0   # nothing there
    assert c.get("/coverage?dec=0").status_code == 422


def test_spectral_view_streams_the_frames_sorted_by_wavelength(live):
    s, c = live
    r, events = read_sse(c, f"/flipbook?{POS}&view=spectral")
    assert r.status_code == 200
    frames = [e.data for e in events if e.data["type"] == "frame"]
    assert len(frames) > 1
    by_slot = sorted(frames, key=lambda f: f["slot"])
    lams = [f["label"]["lambda_um"] for f in by_slot]
    assert lams == sorted(lams)
    assert all(f["stage"] == "preview" for f in frames)       # no co-adds in this view
    assert types(events)[-1] == "done"
