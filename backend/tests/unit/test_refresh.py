import pyarrow.parquet as pq

from backend.offline.build_catalogue import SCHEMA
from backend.offline.refresh import refresh, shard_windows


def fake_reader(row):
    rec = {f.name: None for f in SCHEMA}
    rec.update(frame_id=row["uri"], detector=1, t_start=row["t0"], t_end=row["t1"],
               release="QR3", version="v1", uri=row["uri"], ra_c=1.0, dec_c=2.0,
               naxis1=2040, naxis2=2040, wcs_json="{}", planes_json="{}")
    return rec


class Rows:
    """Stands in for the TAP query and remembers which windows were asked for."""

    def __init__(self):
        self.calls = []

    def __call__(self, lo, hi):
        self.calls.append((lo, hi))
        return [{"uri": f"u{lo}", "t0": lo + 0.1, "t1": lo + 0.11}]


def run(tmp_path, now, rows=None, **kw):
    kw.setdefault("redo_days", 0.0)
    logs = []
    rows = rows or Rows()
    built = refresh(tmp_path, now=now, rows_fn=rows, reader=fake_reader, log=logs.append, **kw)
    return built, rows, logs


def test_empty_catalogue_is_built_from_the_default_start(tmp_path):
    built, rows, _ = run(tmp_path, now=60737.5)    # cutoff is 60735.5 (two days of lag)
    assert rows.calls == [(60700.0, 60714.0), (60714.0, 60728.0), (60728.0, 60735.5)]
    assert [p.name for p in built][-1] == "shard_60728.0_60735.5.parquet"
    assert pq.read_table(built[-1]).column("release").to_pylist() == ["QR3"]
    assert shard_windows(tmp_path)[0] == (60700.0, 60714.0)


def test_second_run_continues_where_the_first_stopped(tmp_path):
    run(tmp_path, now=60737.5)
    built, rows, logs = run(tmp_path, now=60738.0)   # under a day of new data
    assert built == [] and rows.calls == [] and "up to date" in logs[-1]
    built, rows, _ = run(tmp_path, now=60740.0)
    assert rows.calls == [(60735.5, 60738.0)]
    assert [p.name for p in built] == ["shard_60735.5_60738.0.parquet"]


def test_gap_between_shards_is_reported(tmp_path):
    (tmp_path / "shard_60700.0_60714.0.parquet").touch()
    (tmp_path / "shard_60720.0_60734.0.parquet").touch()
    built, rows, logs = run(tmp_path, now=60735.0)
    assert any("gap" in line and "60714.0" in line for line in logs)
    assert built == [] and rows.calls == []        # carries on from the newest shard


def test_dry_run_builds_nothing(tmp_path):
    built, rows, logs = run(tmp_path, now=60737.5, dry_run=True)
    assert len(built) == 3 and rows.calls == []
    assert not list(tmp_path.glob("*.parquet"))
    assert logs[0].startswith("would build shard_60700.0_60714.0")


def test_max_windows_stops_early(tmp_path):
    built, rows, _ = run(tmp_path, now=60737.5, max_windows=1)
    assert len(built) == 1 and rows.calls == [(60700.0, 60714.0)]


def test_recent_shards_are_rebuilt(tmp_path):
    run(tmp_path, now=60737.5)                       # shards up to 60735.5
    built, rows, logs = run(tmp_path, now=60740.0, redo_days=5.0)
    assert any("deleted shard_60728.0_60735.5" in line for line in logs)
    assert rows.calls == [(60728.0, 60738.0)]
    assert [p.name for p in built] == ["shard_60728.0_60738.0.parquet"]
