"""The moving-object search for one field (backend/jobs/movers.py) and GET /movers, with
synthetic frames and a fake SkyBoT (no network)."""

import asyncio

import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend.adapters.cache import UpstreamError
from backend.jobs import movers as J
from backend.pipeline.grid import make_target_wcs
from backend.server import app as app_module
from backend.server.app import create_app
from backend.server.settings import Settings
from backend.tests.unit.test_movers import BURST_HOURS, SIZE, visit
from contracts import ErrorCode, FrameRef, SkyPosition

POS = SkyPosition(ra=180.0, dec=0.0)
MOVER = (20, 30, 3.0, 1.5, 6.0)


def refs_and_grid(t0=61000.0, mover=MOVER, prefix=""):
    grid = visit(mover=mover)
    refs = []
    for g in grid:
        g.t_mjd = t0 + (g.t_mjd - 61000.0)
        g.frame_id = prefix + g.frame_id
        refs.append(
            FrameRef(
                frame_id=g.frame_id,
                detector=g.detector,
                t_start=g.t_mjd,
                t_end=g.t_mjd,
                release="qr2",
                version="v1",
                uri="s3://x",
            )
        )
    return refs, {g.frame_id: g for g in grid}


def patched(monkeypatch, by_id):
    monkeypatch.setattr(J, "preview_size_px", lambda fov: SIZE)
    monkeypatch.setattr(
        J, "to_grid_frames", lambda pairs, pos, size, zodi: [by_id[f.frame_id] for f, _ in pairs]
    )

    async def fetch(f, pos, half):
        return object()

    return fetch


def skybot_at(name, track):
    """A fake SkyBoT that knows one object moving like `track` (x0, y0, vx, vy px/h, t0 MJD)."""
    wcs = make_target_wcs(POS.ra, POS.dec, SIZE)
    x0, y0, vx, vy, t0 = track

    async def sky(ra, dec, r, t):
        h = (t - t0) * 24
        ra_o, dec_o = wcs.pixel_to_world_values(x0 + vx * h, y0 + vy * h)
        return [
            {
                "name": name,
                "ra": float(ra_o),
                "dec": float(dec_o),
                "vmag": 17.0,
                "rate_ra": 18.0,
                "rate_dec": 9.0,
                "cls": "MB",
            }
        ]

    return sky


def run(coro):
    return asyncio.run(coro)


def test_a_mover_that_skybot_knows_is_named(monkeypatch):
    refs, by_id = refs_and_grid()
    fetch = patched(monkeypatch, by_id)
    sky = skybot_at("Testasteroid", (20, 30, 3.0, 1.5, 61000.0))
    body, img, covered = run(J.find_movers(POS, 10, refs, fetch, sky))
    assert len(body["tracks"]) == 1
    t = body["tracks"][0]
    assert t["status"] == "known" and t["known"] == "Testasteroid"
    assert t["rate_px_h"] == pytest.approx(3.354, abs=0.1)
    assert t["vy_px_h"] == pytest.approx(-1.5, abs=0.1)  # picture y points down
    p0 = t["points"][0]
    assert p0["x"] == pytest.approx(20, abs=0.6) and p0["y"] == pytest.approx(
        SIZE - 1 - 30, abs=0.6
    )
    assert body["known_objects"][0]["tracked"] is True
    assert body["skybot_checked"] is True and body["frames_used"] == len(refs)
    assert img.shape == (SIZE, SIZE) and img.dtype == np.uint8
    assert img[SIZE - 1 - 30, 20] > 2 * np.median(img)  # north up, like the frames


def test_an_unknown_mover_is_a_candidate_and_a_skybot_failure_is_said(monkeypatch):
    refs, by_id = refs_and_grid()
    fetch = patched(monkeypatch, by_id)

    async def nothing(ra, dec, r, t):
        return []

    body, _, _ = run(J.find_movers(POS, 10, refs, fetch, nothing))
    assert body["tracks"][0]["status"] == "candidate" and body["tracks"][0]["known"] is None

    async def down(ra, dec, r, t):
        raise UpstreamError(ErrorCode.TIMEOUT, "SkyBoT (known asteroids) is not answering.")

    body, _, _ = run(J.find_movers(POS, 10, refs, fetch, down))
    assert body["skybot_checked"] is False
    assert any("SkyBoT" in n for n in body["notes"])


def test_visits_are_split_and_the_busiest_is_searched_unless_one_is_asked(monkeypatch):
    a_refs, a = refs_and_grid(61000.0)
    b_refs, b = refs_and_grid(61200.0, mover=None, prefix="b")
    b_refs = b_refs[:12]  # 2 bursts only
    fetch = patched(monkeypatch, {**a, **b})

    async def nothing(ra, dec, r, t):
        return []

    body, _, _ = run(J.find_movers(POS, 10, a_refs + b_refs, fetch, nothing))
    assert body["visit"] == 0 and len(body["visits"]) == 2
    assert body["visits"][0]["n_bursts"] == len(BURST_HOURS) and body["visits"][1]["n_bursts"] == 2
    body, _, _ = run(J.find_movers(POS, 10, a_refs + b_refs, fetch, nothing, visit=1))
    assert body["tracks"] == [] and "needs 3" in body["notes"][0]
    with pytest.raises(ValueError):
        run(J.find_movers(POS, 10, a_refs, fetch, nothing, visit=5))


# ------------------------------------------------------------------ GET /movers
CANNED = {"tracks": [], "known_objects": [], "skybot_checked": True, "notes": [], "visits": []}


def test_route_answers_caches_and_serves_the_picture(monkeypatch):
    calls = []

    async def fake_find(pos, fov, frames, fetch, sky, visit=None, use_zodi=True):
        calls.append((pos.ra, pos.dec, fov, visit, use_zodi))
        return dict(CANNED), np.zeros((5, 5), np.uint8), np.ones((5, 5), bool)

    monkeypatch.setattr(app_module, "find_movers", fake_find)
    with TestClient(create_app(Settings(source="fixtures", cache_dir=None))) as c:
        r = c.get("/movers?ra=10&dec=0&fov=10&visit=2&zodi=0")
        assert r.status_code == 200, r.text
        body = r.json()
        assert calls == [(10.0, 0.0, 10.0, 2, False)]
        assert c.get(body["png_url"]).headers["content-type"] == "image/png"
        assert c.get("/movers?ra=10&dec=0&fov=10&visit=2&zodi=0").json() == body
        assert len(calls) == 1  # second answer from the cache
        assert c.get("/movers?ra=10&dec=0&visit=x").status_code == 422


def test_route_does_not_cache_a_result_without_skybot_and_maps_errors(monkeypatch):
    calls = []

    async def no_skybot(*a, **k):
        calls.append(1)
        return (
            {**CANNED, "skybot_checked": False},
            np.zeros((5, 5), np.uint8),
            np.ones((5, 5), bool),
        )

    monkeypatch.setattr(app_module, "find_movers", no_skybot)
    with TestClient(create_app(Settings(source="fixtures", cache_dir=None))) as c:
        c.get("/movers?ra=10&dec=0")
        c.get("/movers?ra=10&dec=0")
        assert len(calls) == 2

    async def bad_visit(*a, **k):
        raise ValueError("visit must be 0 to 3")

    monkeypatch.setattr(app_module, "find_movers", bad_visit)
    with TestClient(create_app(Settings(source="fixtures", cache_dir=None))) as c:
        r = c.get("/movers?ra=10&dec=0&visit=9")
        assert r.status_code == 422 and "visit must be 0 to 3" in r.text
