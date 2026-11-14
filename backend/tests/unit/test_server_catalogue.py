"""Catalogue mode end to end: HTTP request -> catalogue index -> S3 range reads (compressed
FLAGS included) -> rendered PNG, against a local server standing in for S3."""
import httpx
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from backend.offline import build_catalogue
from backend.offline.build_catalogue import SCHEMA, read_frame_record
from backend.server.app import create_app
from backend.server.settings import Settings
from backend.tests.unit.compressed_fixtures import write_frame
from backend.tests.unit.server_harness import LiveServer, read_sse
from backend.tests.unit.test_pooled_source import KeepAliveServer

NAME = "level2_2026W33_2A_0594_4D3_spx_l2b-v27-2026-241.fits"


@pytest.fixture
def s3_and_shards(tmp_path, monkeypatch):
    path = tmp_path / "f.fits"
    write_frame(path, ny=200, nx=200)     # FLAGS tile-compressed, like v27; big enough
    #                                      that the target is 30 px clear of every edge
    s3 = KeepAliveServer(path.read_bytes())
    monkeypatch.setattr(build_catalogue, "S3_HTTPS", s3.url.rsplit("/", 1)[0] + "/")
    rows = []
    for i in range(3):          # three "visits" of the same sky, far apart in time
        rec = read_frame_record({"uri": f"qr3/level2/x{i}/{NAME}", "t0": 100.0 + 90 * i,
                                 "t1": 100.01 + 90 * i})
        rec["uri"] = s3.url                              # the stamp reader fetches from here
        rec["frame_id"] = f"frame_{i}"
        rows.append(rec)
    shards = tmp_path / "shards"
    shards.mkdir()
    pq.write_table(pa.Table.from_pylist(rows, schema=SCHEMA), shards / "targets_x.parquet")
    yield s3, str(shards)
    s3.close()


def test_a_real_request_flows_through_the_catalogue_to_pngs(s3_and_shards):
    s3, shards = s3_and_shards
    settings = Settings(source="catalogue", catalogue_dir=shards, cache_dir=None)
    with LiveServer(settings) as s, httpx.Client(base_url=s.url, timeout=30) as c:
        assert c.get("/health").json()["source"] == "catalogue"
        r, events = read_sse(c, "/flipbook?ra=10&dec=0&fov=2")
        kinds = [e.data["type"] for e in events]
        assert kinds[0] == "heartbeat" and kinds[-1] == "done", kinds
        frames = [e.data for e in events if e.data["type"] == "frame"]
        assert len(frames) == 3 and {f["label"]["release"] for f in frames} == {"QR3"}
        assert all(f["width"] == f["height"] for f in frames)
        png = c.get(frames[0]["png_url"])
        assert png.status_code == 200 and png.content[:8] == b"\x89PNG\r\n\x1a\n"
        assert s3.connections >= 1


def test_a_position_outside_the_catalogue_says_no_coverage(s3_and_shards):
    s3, shards = s3_and_shards
    settings = Settings(source="catalogue", catalogue_dir=shards, cache_dir=None)
    with LiveServer(settings) as s, httpx.Client(base_url=s.url, timeout=30) as c:
        _, events = read_sse(c, "/flipbook?ra=200&dec=-60")
        assert events[-1].data["type"] == "error" and events[-1].data["code"] == "no_coverage"


async def test_missing_catalogue_directory_fails_at_start_up_not_per_request(tmp_path):
    app = create_app(Settings(source="catalogue", catalogue_dir=str(tmp_path / "nope"),
                              cache_dir=None))
    with pytest.raises(FileNotFoundError):
        async with app.router.lifespan_context(app):
            pass
