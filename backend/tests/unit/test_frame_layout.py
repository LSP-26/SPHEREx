import numpy as np
import pytest
from astropy.io import fits

from backend.adapters.fits_layout import FileSource, read_layout
from backend.adapters.frame_layout import (
    data_nbytes,
    read_frame_layout,
    spec_from_list,
    spec_to_list,
)
from backend.tests.unit.compressed_fixtures import NX, NY, make_flags, write_frame
from contracts import Compression, Plane, PlaneSpec


class Counting(FileSource):
    def __init__(self, path):
        super().__init__(path)
        self.reads = []

    def read(self, start, length):
        self.reads.append((start, length))
        return super().read(start, length)


@pytest.fixture
def plain(tmp_path):
    path = tmp_path / "plain.fits"
    write_frame(path, compression=None)
    return path


@pytest.fixture
def packed(tmp_path):
    path = tmp_path / "packed.fits"
    write_frame(path)
    return path


def test_plain_file_matches_the_careful_reader_in_four_reads(plain):
    src = Counting(plain)
    # a small first chunk, because this test file is tiny (real frames are 71 MB, so the
    # default 32 KB first read never reaches the FLAGS header)
    got = read_frame_layout(src, first_chunk=8192)
    assert len(src.reads) == 4
    ref = {h.extname: h for h in read_layout(FileSource(plain))}
    assert got.shape == (NY, NX)
    for plane, spec in got.planes.items():
        assert spec.offset == ref[plane.value].data_start
        assert spec.bitpix == ref[plane.value].bitpix and spec.compression is None
    assert got.image_header["DETECTOR"] == 3


def test_compressed_flags_are_described_not_mistaken_for_pixels(packed):
    got = read_frame_layout(Counting(packed))
    flags = got.planes[Plane.FLAGS]
    ref = {h.extname: h for h in read_layout(FileSource(packed))}
    assert ref["FLAGS"].xtension == "BINTABLE" and ref["FLAGS"].compressed
    assert flags.bitpix == 32 and flags.offset == ref["FLAGS"].data_start
    c = flags.compression
    assert c is not None and c.type == "RICE_1" and c.row_bytes == 8
    assert c.heap_offset == flags.offset + 8 * NY
    assert c.heap_bytes == ref["FLAGS"].data_nbytes - 8 * NY
    assert (c.blocksize, c.bytepix) == (32, 4)
    # the planes after the variable-size FLAGS block are still found at the right place
    assert got.planes[Plane.VARIANCE].offset == ref["VARIANCE"].data_start
    assert got.planes[Plane.ZODI].offset == ref["ZODI"].data_start
    assert got.planes[Plane.IMAGE].compression is None


def test_headers_longer_than_the_first_chunk_are_followed(packed):
    src = Counting(packed)
    got = read_frame_layout(src, first_chunk=1000, next_chunk=700)
    assert got.planes == read_frame_layout(FileSource(packed)).planes
    assert len(src.reads) > 4


def test_data_size_formula_covers_images_and_tables(packed):
    hdrs = {h.extname: h for h in read_layout(FileSource(packed))}
    with open(packed, "rb") as f:
        raw = f.read()
    for name in ("IMAGE", "FLAGS"):
        text = raw[hdrs[name].header_start:hdrs[name].data_start].decode()
        assert data_nbytes(fits.Header.fromstring(text)) == hdrs[name].data_nbytes


@pytest.mark.parametrize("kw,match", [
    (dict(compression="GZIP_1"), "only RICE_1"),
    (dict(tile_shape=(2, NX)), "whole rows"),
    (dict(flags=np.zeros((NY, NX), "float32"), compression=None), "BITPIX"),
    (dict(drop=("ZODI",)), "missing planes"),
])
def test_unreadable_formats_fail_loudly(tmp_path, kw, match):
    path = tmp_path / "bad.fits"
    write_frame(path, **kw)
    with pytest.raises(ValueError, match=match):
        read_frame_layout(FileSource(path))


def test_plane_with_the_wrong_shape_is_refused(tmp_path):
    path = tmp_path / "odd.fits"
    write_frame(path, compression=None)
    with fits.open(path) as h:
        hdus = [x.copy() for x in h]
    hdus[3] = fits.ImageHDU(np.zeros((NY + 1, NX), "float32"), name="VARIANCE")
    fits.HDUList(hdus).writeto(path, overwrite=True)
    with pytest.raises(ValueError, match="shape"):
        read_frame_layout(FileSource(path))


@pytest.mark.parametrize("keep,match", [(20000, "missing planes"), (26000, "ran off the end")])
def test_truncated_file_is_an_error_not_a_hang(packed, tmp_path, keep, match):
    cut = tmp_path / "cut.fits"
    cut.write_bytes(packed.read_bytes()[:keep])      # in the IMAGE data, then in a header
    with pytest.raises(ValueError, match=match):
        read_frame_layout(FileSource(cut))


def test_spec_round_trip_and_old_four_item_records():
    plain = PlaneSpec(offset=23040, bitpix=-32)
    comp = PlaneSpec(offset=100, bitpix=32, compression=Compression(
        row_bytes=8, heap_offset=16420, heap_bytes=8_000_000))
    assert spec_to_list(plain) == [23040, -32, 0.0, 1.0]
    assert spec_from_list(spec_to_list(plain)) == plain
    assert spec_from_list(spec_to_list(comp)) == comp
    assert spec_from_list([16683840, 32, 0.0, 1.0]).compression is None


def test_flags_helper_really_produces_set_bits():
    assert (make_flags() != 0).any() and (make_flags(unsigned=True) >= 2**31).any()
