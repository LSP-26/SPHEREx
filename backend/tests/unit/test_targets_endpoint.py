from fastapi.testclient import TestClient

from backend.server.app import create_app
from backend.server.settings import Settings
from backend.server.targets import target_list


def test_barnard_is_where_it_is_now_not_at_j2000():
    b = next(t for t in target_list() if t["key"] == "barnard")
    assert abs(b["dec"] - 4.7689) < 0.002          # about 270 arcsec north of 4.693365


def test_wise0855_appears_twice_brief_and_survey_epoch():
    rows = {t["key"]: t for t in target_list()}
    assert rows["wise0855_brief"]["ra"] == 133.7951321
    assert abs(rows["wise0855"]["ra"] - 133.7597) < 0.003   # about 125 arcsec west


def test_endpoint_serves_the_list():
    client = TestClient(create_app(Settings(source="fixtures", cache_dir=None)))
    r = client.get("/targets")
    assert r.status_code == 200
    assert {"barnard", "eps_indi_b", "r_dra", "wise0855_brief"} <= {t["key"] for t in r.json()}
    assert all(0 <= t["ra"] < 360 and -90 <= t["dec"] <= 90 for t in r.json())
