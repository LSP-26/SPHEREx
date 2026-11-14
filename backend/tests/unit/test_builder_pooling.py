import pyarrow.parquet as pq
import pytest

from backend.offline import build_catalogue, refresh, target_catalogue
from backend.offline.build_catalogue import DEFAULT_WORKERS, build_shard
from backend.tests.unit.compressed_fixtures import write_frame
from backend.tests.unit.test_pooled_source import KeepAliveServer

NAME = "level2_2026W33_2A_0594_4D3_spx_l2b-v27-2026-241.fits"


@pytest.fixture
def server(tmp_path):
    path = tmp_path / "f.fits"
    write_frame(path)
    s = KeepAliveServer(path.read_bytes())
    yield s
    s.close()


def test_the_builder_reuses_connections_across_frames(server, tmp_path, monkeypatch):
    monkeypatch.setattr(build_catalogue, "S3_HTTPS", server.url.rsplit("/", 1)[0] + "/")
    rows = [{"uri": f"qr3/level2/x/{i}/{NAME}", "t0": float(i), "t1": i + 0.01}
            for i in range(24)]
    res = build_shard(tmp_path / "s.parquet", rows, workers=4)
    assert res == (24, 0)
    assert pq.read_table(tmp_path / "s.parquet").num_rows == 24
    # 24 frames x about 3 requests each. One connection per request would be 70 or more;
    # a pool of 4 threads should need little more than 4.
    assert server.connections <= 8


def test_every_entry_point_defaults_to_the_benchmarked_worker_count():
    assert DEFAULT_WORKERS == 32
    import inspect
    assert inspect.signature(build_shard).parameters["workers"].default == DEFAULT_WORKERS
    assert inspect.signature(target_catalogue.build_all).parameters["workers"].default == 32
    assert inspect.signature(refresh.refresh).parameters["workers"].default == 32
