import pytest
from fastapi.testclient import TestClient

from backend.adapters.cache import UpstreamError
from backend.adapters.lookups import WebLookups
from backend.pipeline.motion import GAIA_EPOCH_MJD
from backend.pipeline.overlay import cone_radius_arcmin
from backend.server.app import create_app
from backend.server.settings import Settings
from contracts import ErrorCode

SOURCE = {"source_id": 1, "ra": 100.0, "dec": 0.0, "pmra": None, "pmdec": None,
          "parallax": None, "g_mag": 12.0, "ref_epoch_mjd": GAIA_EPOCH_MJD}


class FakeLookups:
    def __init__(self, sources=(), error=None):
        self.sources, self.error, self.calls, self.closed = list(sources), error, [], False

    async def gaia_cone(self, pos, radius_arcmin):
        self.calls.append((pos, radius_arcmin))
        if self.error:
            raise self.error
        return self.sources

    async def aclose(self):
        self.closed = True


def client_for(fake):
    return TestClient(create_app(Settings(source="fixtures", cache_dir=None), lookups=fake))


def test_a_star_at_the_centre_comes_back_in_the_middle_of_the_picture():
    fake = FakeLookups([SOURCE])
    with client_for(fake) as c:
        r = c.get("/overlay?ra=100&dec=0")
    assert r.status_code == 200
    body = r.json()
    assert body["size"] == 49 and body["pixel_arcsec"] == 6.15
    assert len(body["stars"]) == 1
    s = body["stars"][0]
    assert s["source_id"] == 1 and s["x"] == pytest.approx(24, abs=0.01)
    assert s["y"] == pytest.approx(24, abs=0.01) and s["g_mag"] == 12.0


def test_the_cone_search_gets_the_position_and_the_radius_for_the_field():
    fake = FakeLookups([SOURCE])
    with client_for(fake) as c:
        c.get("/overlay?ra=100&dec=0")
        c.get("/overlay?ra=100&dec=0&fov=10")
    (pos, radius), (_, radius10) = fake.calls
    assert (pos.ra, pos.dec) == (100.0, 0.0)
    assert radius == pytest.approx(cone_radius_arcmin(5.0))
    assert radius10 == pytest.approx(cone_radius_arcmin(10.0))


def test_the_field_of_view_sets_the_picture_size():
    with client_for(FakeLookups([SOURCE])) as c:
        body = c.get("/overlay?ra=100&dec=0&fov=10").json()
    assert body["size"] == 99
    assert body["stars"][0]["x"] == pytest.approx(49, abs=0.01)


def test_the_answer_may_be_cached_for_an_hour():
    with client_for(FakeLookups()) as c:
        r = c.get("/overlay?ra=100&dec=0")
    assert r.json()["stars"] == []
    assert r.headers["cache-control"] == "public, max-age=3600"


@pytest.mark.parametrize("query", [
    "", "ra=10", "dec=0", "ra=400&dec=0", "ra=-1&dec=0", "ra=abc&dec=0", "ra=1&dec=95",
    "ra=1&dec=1&fov=0", "ra=1&dec=1&fov=121", "ra=1&dec=1&fov=nan", "ra=nan&dec=0",
])
def test_bad_parameters_are_a_422_and_ask_nobody(query):
    fake = FakeLookups([SOURCE])
    with client_for(fake) as c:
        r = c.get(f"/overlay?{query}")
    assert r.status_code == 422
    assert r.json()["code"] == "bad_coords" and r.json()["message"]
    assert fake.calls == []


@pytest.mark.parametrize("error,status,code", [
    (UpstreamError(ErrorCode.TIMEOUT, "Gaia is slow.", retryable=True), 503, "timeout"),
    (UpstreamError(ErrorCode.INTERNAL, "Gaia said no.", retryable=False), 502, "internal"),
])
def test_upstream_trouble_is_a_clear_error(error, status, code):
    with client_for(FakeLookups(error=error)) as c:
        r = c.get("/overlay?ra=100&dec=0")
    assert r.status_code == status
    assert r.json() == {"code": code, "message": error.message}


def test_without_an_injected_lookup_the_app_makes_its_own_and_closes_only_that():
    app = create_app(Settings(source="fixtures", cache_dir=None))
    with TestClient(app):
        assert isinstance(app.state.lookups, WebLookups)
    fake = FakeLookups()
    with client_for(fake):
        pass
    assert fake.closed is False          # a lookup we were handed is not ours to close
