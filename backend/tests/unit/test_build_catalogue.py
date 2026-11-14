import pyarrow.parquet as pq

from backend.offline.build_catalogue import (
    SCHEMA,
    build_shard,
    key_from_uri,
    parse_detector,
    parse_release,
    parse_version,
)

KEY = "qr2/level2/2025W18_1B/0001/level2_2025W18_1B_0001_1D1_spx_l2b-v20-2025-241.fits"


def test_parsers_on_a_real_file_name():
    name = KEY.rsplit("/", 1)[1]
    assert parse_release(KEY) == "QR2"
    assert parse_version(name) == "v20-2025-241"
    assert parse_detector(name) == 1
    assert key_from_uri("ibe/data/spherex/" + KEY) == KEY
    assert parse_version("odd.fits") == "unknown" and parse_detector("odd.fits") is None


def _row(uri, t=1.0):
    return {"uri": uri, "t0": t, "t1": t + 0.01}


def _fake(row):
    if "bad" in row["uri"]:
        raise RuntimeError("boom")
    rec = {f.name: None for f in SCHEMA}
    rec.update(frame_id=row["uri"], detector=1, t_start=row["t0"], t_end=row["t1"],
               release="QR2", version="v1", uri=row["uri"], ra_c=1.0, dec_c=2.0,
               naxis1=2040, naxis2=2040, wcs_json="{}", planes_json="{}")
    return rec


def test_shard_written_sorted_with_failures_recorded(tmp_path):
    path = tmp_path / "s.parquet"
    rows = [_row("b", 2.0), _row("bad", 3.0), _row("a", 1.0)]
    assert build_shard(path, rows, reader=_fake, workers=2) == (2, 1)
    t = pq.read_table(path)
    assert t.schema.names == SCHEMA.names
    assert t.column("frame_id").to_pylist() == ["a", "b"]
    assert path.with_suffix(".failed.json").exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_finished_shard_is_skipped(tmp_path):
    path = tmp_path / "s.parquet"
    build_shard(path, [_row("a")], reader=_fake)
    assert build_shard(path, [_row("zzz")], reader=_fake) is None
    assert pq.read_table(path).num_rows == 1
