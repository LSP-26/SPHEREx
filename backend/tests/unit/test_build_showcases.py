import json

from pydantic import TypeAdapter

from backend.adapters.stubs import MemoryCache
from backend.offline.build_showcases import build_one, showcase_targets, update_manifest
from backend.server.deps import FixtureDeps
from backend.server.settings import Settings
from contracts import Event, FrameEvent

SYNTHETIC = {"key": "synthetic", "name": "Synthetic", "ra": 10.0, "dec": 0.0, "note": "test"}


def deps_and_cache():
    cache = MemoryCache()
    return FixtureDeps(Settings(source="fixtures", cache_dir=None), cache), cache


async def test_saves_events_and_frames_that_replay(tmp_path):
    deps, cache = deps_and_cache()
    entry = await build_one(SYNTHETIC, deps, cache, tmp_path, log=lambda s: None)
    assert entry and entry["events"] == "synthetic/events.json" and entry["n_frames"] >= 1
    body = json.loads((tmp_path / "synthetic" / "events.json").read_text())
    events = [TypeAdapter(Event).validate_python(e) for e in body["events"]]   # all valid events
    frames = [e for e in events if isinstance(e, FrameEvent)]
    assert len(frames) == entry["n_frames"]
    for f in frames:                       # relative URLs, files exist and are real PNGs
        assert not f.png_url.startswith("/")
        png = (tmp_path / "synthetic" / f.png_url).read_bytes()
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert (tmp_path / "synthetic" / entry["thumb"]).exists()


async def test_position_without_data_saves_nothing(tmp_path):
    deps, cache = deps_and_cache()
    logs = []
    far = {**SYNTHETIC, "key": "far", "ra": 200.0, "dec": -60.0}
    assert await build_one(far, deps, cache, tmp_path, log=logs.append) is None
    assert not (tmp_path / "far").exists() or not (tmp_path / "far" / "events.json").exists()


def test_manifest_merges_instead_of_overwriting(tmp_path):
    update_manifest(tmp_path, [{"key": "barnard", "name": "B"}, {"key": "r_dra", "name": "R"}])
    update_manifest(tmp_path, [{"key": "barnard", "name": "B2"}])
    items = json.loads((tmp_path / "manifest.json").read_text())["showcases"]
    assert [i["key"] for i in items] == ["barnard", "r_dra"]
    assert items[0]["name"] == "B2"


def test_the_five_targets_and_a_control_are_listed():
    keys = [t["key"] for t in showcase_targets()]
    assert {"barnard", "wise0855", "rw_aur", "r_dra", "control"} <= set(keys)
    assert "wise0855_brief" not in keys


def test_eps_indi_b_has_its_own_showcase_away_from_the_k_star():
    rows = {t["key"]: t for t in showcase_targets()}
    assert "eps_indi_b" in rows
    assert abs(rows["eps_indi_a"]["ra"] - rows["eps_indi_b"]["ra"]) > 0.1   # about 6.7 arcmin
