"""build_flags: which frames of a showcase get a flag layer, and the manifest entry."""
import json

from backend.offline.build_flags import mark_manifest, preview_frames


def _ev(slot, stage, detector, fid):
    return {"type": "frame", "slot": slot, "stage": stage,
            "label": {"frame_id": fid, "detector": detector}}


def test_only_single_preview_frames_count_once_each():
    events = [_ev(0, "preview", 1, "a"), _ev(0, "refined", 1, "a"), _ev(1, "preview", 4, "b"),
              _ev(2, "refined", 0, "colour_v0"), {"type": "done"}]
    assert preview_frames(events) == [(0, "a"), (1, "b")]


def test_mark_manifest_adds_flags_only_where_built(tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps({"showcases": [{"key": "a"}, {"key": "b"}]}))
    mark_manifest(tmp_path, {"a": "a/flags.json"})
    got = {e["key"]: e for e in json.loads((tmp_path / "manifest.json").read_text())["showcases"]}
    assert got["a"]["flags"] == "a/flags.json" and "flags" not in got["b"]
