from types import SimpleNamespace

import pyarrow.parquet as pq
import pytest

from backend.offline.build_catalogue import SCHEMA
from backend.offline.target_catalogue import TARGETS, build_all, positional_rows


def fake_reader(row):
    rec = {f.name: None for f in SCHEMA}
    rec.update(frame_id=row["uri"], detector=1, t_start=row["t0"], t_end=row["t1"],
               release="QR2", version="v1", uri=row["uri"], ra_c=1.0, dec_c=2.0,
               naxis1=2040, naxis2=2040, wcs_json="{}", planes_json="{}")
    return rec


def rows_for(ra, dec):
    return [{"uri": f"f{ra}_{i}.fits", "t0": 1.0 + i, "t1": 1.01 + i} for i in range(3)]


def test_one_shard_per_target_named_targets(tmp_path):
    logs = []
    built = build_all(tmp_path, only=["barnard", "r_dra"], rows_fn=rows_for,
                      reader=fake_reader, log=logs.append)
    assert sorted(p.name for p in built) == ["targets_barnard.parquet", "targets_r_dra.parquet"]
    assert not list(tmp_path.glob("shard_*"))        # never looks like a full-catalogue shard
    assert pq.read_table(built[0]).num_rows == 3


def test_rerun_skips_finished_targets(tmp_path):
    build_all(tmp_path, only=["barnard"], rows_fn=rows_for, reader=fake_reader, log=print)
    calls = []

    def spy(ra, dec):
        calls.append(ra)
        return rows_for(ra, dec)

    logs = []
    assert build_all(tmp_path, only=["barnard"], rows_fn=spy, reader=fake_reader,
                     log=logs.append) == []
    assert calls == [] and "skipping" in logs[0]


def test_no_frames_writes_nothing(tmp_path):
    logs = []
    build_all(tmp_path, only=["control"], rows_fn=lambda ra, dec: [], reader=fake_reader,
              log=logs.append)
    assert not list(tmp_path.glob("*.parquet")) and "no frames" in logs[0]


def test_positional_query_tries_the_next_column_name():
    asked = []

    def post(query):
        asked.append(query)
        if "p.poly" in query:
            return SimpleNamespace(status_code=400, text="no such column")
        return SimpleNamespace(status_code=200,
                               text="uri,t0,t1\nibe/data/spherex/qr2/a.fits,1.0,1.1\n"
                                    "ibe/data/spherex/qr2/readme.txt,1.0,1.1\n")

    rows = positional_rows(10.0, 5.0, post=post)
    assert "p.s_region" in asked[1] and "POINT('ICRS', 10.0, 5.0)" in asked[1]
    assert [r["uri"] for r in rows] == ["ibe/data/spherex/qr2/a.fits"]


def test_all_columns_failing_is_a_clear_error():
    def post(query):
        return SimpleNamespace(status_code=400, text="bad")

    with pytest.raises(RuntimeError, match="no footprint column worked"):
        positional_rows(1.0, 2.0, post=post)


def test_targets_are_valid_positions():
    for ra, dec in TARGETS.values():
        assert 0 <= ra < 360 and -90 <= dec <= 90
