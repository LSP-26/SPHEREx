"""build_movers: the manifest learns which showcases have a recorded moving-object search."""
import json

from backend.offline.build_movers import mark_manifest


def test_mark_manifest_adds_movers_only_where_a_search_was_saved(tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps(
        {"showcases": [{"key": "a", "name": "A"}, {"key": "b", "name": "B"}]}))
    mark_manifest(tmp_path, {"a": "a/movers.json"})
    got = {e["key"]: e for e in json.loads((tmp_path / "manifest.json").read_text())["showcases"]}
    assert got["a"]["movers"] == "a/movers.json"
    assert "movers" not in got["b"]
