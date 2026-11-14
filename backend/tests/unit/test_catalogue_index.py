import json

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from backend.adapters.catalogue_index import CatalogueFrameIndex
from backend.offline.build_catalogue import SCHEMA
from contracts import Plane, SkyPosition
from contracts.interfaces import FrameIndex

PLANES = {"IMAGE": [23040, -32, 0.0, 1.0], "FLAGS": [16683840, 32, 0.0, 1.0],
          "VARIANCE": [33341760, -32, 0.0, 1.0], "ZODI": [49999680, -32, 0.0, 1.0]}
PX = 6.15 / 3600
TARGET = SkyPosition(ra=10.0, dec=0.0)


def row(fid, ra_c, dec_c, t, det=1):
    wcs = {"NAXIS": 2, "NAXIS1": 2040, "NAXIS2": 2040, "CTYPE1": "RA---TAN",
           "CTYPE2": "DEC--TAN", "CRPIX1": 1020.0, "CRPIX2": 1020.0, "CRVAL1": ra_c,
           "CRVAL2": dec_c, "CDELT1": -PX, "CDELT2": PX, "CUNIT1": "deg", "CUNIT2": "deg"}
    return dict(frame_id=fid, detector=det, obsid=None, t_start=t, t_end=t + 0.01,
                release="QR2", version="v1", uri=f"s3://nasa-irsa-spherex/qr2/{fid}.fits",
                ra_c=ra_c, dec_c=dec_c, naxis1=2040, naxis2=2040,
                wcs_json=json.dumps(wcs), planes_json=json.dumps(PLANES))


def write(path, rows):
    pq.write_table(pa.Table.from_pylist(rows, schema=SCHEMA), path)


@pytest.fixture
def index(tmp_path):
    # A sits on the target; B is 1.5 deg north (target near its edge, still on the frame);
    # C is 2.0 deg north (inside the cone cut, but the target is off the frame);
    # D is far away; E straddles RA 0 and is used for the wrap-around test.
    write(tmp_path / "s1.parquet", [row("A", 10.0, 0.0, 3.0), row("C", 10.0, 2.0, 2.0)])
    write(tmp_path / "s2.parquet", [row("B", 10.0, 1.5, 1.0), row("D", 10.0, 30.0, 4.0),
                                    row("E", 359.9, 0.0, 5.0)])
    return CatalogueFrameIndex(tmp_path)


def test_satisfies_the_interface(index):
    assert isinstance(index, FrameIndex)
    assert index.n_frames == 5


async def test_returns_covering_frames_sorted_by_time(index):
    got = await index.frames_at(TARGET)
    assert [f.frame_id for f in got] == ["B", "A"]   # C is off the frame, D is far away


async def test_margin_excludes_frames_where_target_is_near_the_edge(index):
    assert [f.frame_id for f in await index.frames_at(TARGET, margin_px=200)] == ["A"]


async def test_frames_carry_the_catalogue_record(index):
    f = (await index.frames_at(TARGET))[-1]
    assert f.frame_id == "A" and f.release == "QR2" and f.uri.startswith("s3://")
    assert f.planes[Plane.IMAGE].offset == 23040 and f.planes[Plane.IMAGE].bitpix == -32
    assert f.planes[Plane.FLAGS].bitpix == 32
    assert f.wcs_header["CRVAL1"] == 10.0


async def test_ra_wraparound(index):
    got = await index.frames_at(SkyPosition(ra=0.1, dec=0.0))
    assert [f.frame_id for f in got] == ["E"]


async def test_no_coverage_gives_empty_list(index):
    assert await index.frames_at(SkyPosition(ra=200.0, dec=-60.0)) == []


def test_empty_directory_is_a_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        CatalogueFrameIndex(tmp_path)
