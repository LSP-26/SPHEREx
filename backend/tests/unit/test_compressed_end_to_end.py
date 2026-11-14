"""Compressed FLAGS through the real stamp reader, catalogue builder and index, against a local
HTTP server that honours Range (a stand-in for S3)."""
import json

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from astropy.io import fits

from backend.adapters.cache import HostLimiter, UpstreamError
from backend.adapters.catalogue_index import CatalogueFrameIndex
from backend.adapters.fits_layout import FileSource
from backend.adapters.frame_layout import read_frame_layout
from backend.adapters.s3_stamps import OutOfFrame, S3StampSource, StaleCatalogueRecord
from backend.offline import build_catalogue
from backend.offline.build_catalogue import SCHEMA, read_frame_record
from backend.tests.unit.compressed_fixtures import NX, NY, WCS, write_frame
from backend.tests.unit.test_s3_stamps import Server
from contracts import FrameRef, Plane, PlaneSpec, SkyPosition

CENTRE = SkyPosition(ra=10.0, dec=0.0)


@pytest.fixture(scope="module")
def packed(tmp_path_factory):
    path = tmp_path_factory.mktemp("c") / "frame.fits"
    write_frame(path)
    return path


@pytest.fixture
def served(packed):
    servers = []

    def make(**kw):
        s = Server(packed, **kw)
        servers.append(s)
        layout = read_frame_layout(FileSource(packed))   # not through the failing server
        frame = FrameRef(frame_id="packed", detector=1, t_start=1.0, t_end=1.01, release="QR3",
                         version="v27", uri=s.url, wcs_header=WCS, planes=layout.planes,
                         naxis1=NX, naxis2=NY)
        return s, frame

    yield make
    for s in servers:
        s.close()


def nosleep_limiter(**kw):
    async def nosleep(d):
        pass
    return HostLimiter(sleep=nosleep, **kw)


async def test_every_plane_matches_astropy_including_compressed_flags(served, packed):
    srv, frame = served()
    src = S3StampSource()
    st = await src.fetch(frame, CENTRE, 10)
    sl = (slice(st.y0, st.y0 + st.image.shape[0]), slice(st.x0, st.x0 + st.image.shape[1]))
    with fits.open(packed) as h:
        assert np.array_equal(st.image, h["IMAGE"].data[sl])
        assert np.array_equal(st.variance, h["VARIANCE"].data[sl])
        assert np.array_equal(st.zodi, h["ZODI"].data[sl])
        ref = h["FLAGS"].data[sl].astype("int64") & 0xFFFFFFFF
    assert st.flags.dtype == np.uint32 and np.array_equal(st.flags, ref)
    assert (st.flags != 0).any()                  # the comparison is not vacuous
    await src.aclose()


async def test_compressed_flags_cost_two_requests_the_others_one(served):
    srv, frame = served()
    src = S3StampSource()
    await src.fetch(frame, CENTRE, 10)
    assert src.requests == 3 + 2                      # IMAGE, VARIANCE, ZODI, then FLAGS x 2
    flags_reads = [(s, n) for s, n in srv.log if n < 21 * NX * 4]
    assert len(flags_reads) == 2 and flags_reads[0][1] == 21 * 8     # descriptors, then heap
    await src.aclose()


async def test_stamp_clipped_at_the_frame_edge_and_far_target(served):
    srv, frame = served()
    src = S3StampSource()
    d = 6.15 / 3600
    st = await src.fetch(frame, SkyPosition(ra=10.0 + 39 * d, dec=-29 * d), 10)
    assert (st.x0, st.y0) == (0, 0) and st.flags.shape == st.image.shape == (11, 11)
    with pytest.raises(OutOfFrame):
        await src.fetch(frame, SkyPosition(ra=10.0 + 500 * d, dec=0.0), 10)
    await src.aclose()


async def test_busy_server_is_retried_for_compressed_reads_too(served):
    srv, frame = served(fail_first=3)
    src = S3StampSource(limiter=nosleep_limiter())
    st = await src.fetch(frame, CENTRE, 5)
    assert st.flags.shape == (11, 11)
    await src.aclose()


async def test_server_that_stays_down_gives_the_clear_error(served):
    srv, frame = served(fail_first=10_000)
    src = S3StampSource(limiter=nosleep_limiter(attempts=2))
    with pytest.raises(UpstreamError):
        await src.fetch(frame, CENTRE, 5)
    await src.aclose()


async def test_old_catalogue_record_is_refused_instead_of_decoded_as_garbage(served):
    """Before v0.2.0 a compressed FLAGS table was recorded as BITPIX 8."""
    srv, frame = served()
    stale = dict(frame.planes)
    stale[Plane.FLAGS] = PlaneSpec(offset=frame.planes[Plane.FLAGS].offset, bitpix=8)
    src = S3StampSource()
    with pytest.raises(StaleCatalogueRecord, match="rebuild the catalogue"):
        await src.fetch(frame.model_copy(update={"planes": stale}), CENTRE, 5)
    await src.aclose()


# ---------------------------------------------------------------- the catalogue builder
def test_builder_records_the_compression_and_the_frame_centre(served, monkeypatch):
    srv, _ = served()
    monkeypatch.setattr(build_catalogue, "S3_HTTPS", srv.url.rsplit("/", 1)[0] + "/")
    name = "level2_2026W33_2A_0594_4D3_spx_l2b-v27-2026-241.fits"
    rec = read_frame_record({"uri": f"ibe/data/spherex/qr3/level2/x/{name}", "t0": 5.0, "t1": 5.01})
    assert (rec["release"], rec["version"], rec["detector"]) == ("QR3", "v27-2026-241", 3)
    assert (rec["naxis1"], rec["naxis2"]) == (NX, NY)
    planes = json.loads(rec["planes_json"])
    assert list(planes) == ["IMAGE", "FLAGS", "VARIANCE", "ZODI"]
    assert len(planes["IMAGE"]) == 4 and len(planes["FLAGS"]) == 5
    assert planes["FLAGS"][1] == 32 and planes["FLAGS"][4]["type"] == "RICE_1"
    assert abs(rec["ra_c"] - 10.0) < 0.01 and abs(rec["dec_c"]) < 0.01


# ---------------------------------------------------------------- the catalogue index
async def test_index_returns_frames_whose_planes_include_the_compression(served, tmp_path,
                                                                         monkeypatch):
    srv, _ = served()
    monkeypatch.setattr(build_catalogue, "S3_HTTPS", srv.url.rsplit("/", 1)[0] + "/")
    name = "level2_2026W33_2A_0594_4D3_spx_l2b-v27-2026-241.fits"
    rec = read_frame_record({"uri": f"ibe/data/spherex/qr3/level2/x/{name}", "t0": 5.0, "t1": 5.01})
    pq.write_table(pa.Table.from_pylist([rec], schema=SCHEMA), tmp_path / "s.parquet")
    frames = await CatalogueFrameIndex(tmp_path).frames_at(CENTRE, margin_px=5)
    assert len(frames) == 1
    flags = frames[0].planes[Plane.FLAGS]
    assert flags.compression is not None and flags.bitpix == 32
    assert frames[0].planes[Plane.IMAGE].compression is None
    # and the whole chain works: index record -> stamp reader -> pixels
    frame = frames[0].model_copy(update={"uri": srv.url})
    src = S3StampSource()
    st = await src.fetch(frame, CENTRE, 8)
    assert st.flags.shape == (17, 17) and (st.flags != 0).any()
    await src.aclose()
