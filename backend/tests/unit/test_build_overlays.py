import json

from backend.adapters.cache import UpstreamError
from backend.offline.build_overlays import build_all
from backend.pipeline.motion import GAIA_EPOCH_MJD
from contracts import ErrorCode

SOURCE = {"source_id": 7, "ra": 100.0, "dec": 0.0, "pmra": None, "pmdec": None,
          "parallax": None, "g_mag": 12.0, "ref_epoch_mjd": GAIA_EPOCH_MJD}


class Lookups:
    def __init__(self, fail=()):
        self.fail, self.calls = set(fail), []

    async def gaia_cone(self, pos, radius_arcmin):
        self.calls.append((pos.ra, radius_arcmin))
        if pos.ra in self.fail:
            raise UpstreamError(ErrorCode.TIMEOUT, "Gaia is slow.")
        return [SOURCE]


def make_root(tmp_path, keys=("a", "b"), folders=("a", "b")):
    manifest = [{"key": k, "ra": 100.0 + i, "dec": 0.0, "fov_arcmin": 5.0}
                for i, k in enumerate(keys)]
    (tmp_path / "manifest.json").write_text(json.dumps({"showcases": manifest}))
    for f in folders:
        (tmp_path / f).mkdir()
    return tmp_path


async def test_writes_an_overlay_next_to_each_showcase_in_the_overlay_shape(tmp_path):
    root = make_root(tmp_path)
    built = await build_all(root, Lookups(), log=lambda s: None)
    assert [p.parent.name for p in built] == ["a", "b"]
    body = json.loads((root / "a" / "overlay.json").read_text())
    assert set(body) == {"size", "epoch_mjd", "pixel_arcsec", "stars"}
    assert body["size"] == 49 and len(body["stars"]) == 1
    assert body["stars"][0]["x"] == 24.0 and body["stars"][0]["g_mag"] == 12.0


async def test_a_gaia_failure_skips_that_showcase_only(tmp_path):
    root = make_root(tmp_path)
    logs = []
    built = await build_all(root, Lookups(fail={100.0}), log=logs.append, retries = 1)
    assert [p.parent.name for p in built] == ["b"]
    assert not (root / "a" / "overlay.json").exists()
    assert any("Gaia failed" in line for line in logs)


async def test_only_and_missing_folders_are_respected(tmp_path):
    root = make_root(tmp_path, keys=("a", "b", "c"), folders=("a", "b"))
    lk = Lookups()
    logs = []
    assert len(await build_all(root, lk, only=["b", "c"], log=logs.append)) == 1
    assert len(lk.calls) == 1 and any("no showcase folder" in line for line in logs)
