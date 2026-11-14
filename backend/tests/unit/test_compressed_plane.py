import numpy as np
import pytest
from astropy.io import fits

from backend.adapters.compressed_plane import decode_rows, parse_descriptors, plan_reads
from backend.adapters.fits_layout import FileSource
from backend.adapters.frame_layout import read_frame_layout
from backend.tests.unit.compressed_fixtures import NX, NY, make_flags, write_frame
from contracts import Compression, Plane


def read_band(path, y0, y1, max_gap=None):
    """What S3StampSource does, against a local file: descriptors, then heap ranges, then decode."""
    src = FileSource(path)
    spec = read_frame_layout(src).planes[Plane.FLAGS]
    comp = spec.compression
    raw = src.read(spec.offset + y0 * comp.row_bytes, (y1 - y0) * comp.row_bytes)
    tiles = parse_descriptors(raw, comp)
    ranges = plan_reads(tiles) if max_gap is None else plan_reads(tiles, max_gap)
    parts = [src.read(comp.heap_offset + s, n) for s, n in ranges]
    return decode_rows(comp, NX, tiles, ranges, parts, spec.bzero, spec.bscale), ranges


@pytest.fixture
def packed(tmp_path):
    path = tmp_path / "packed.fits"
    write_frame(path)
    return path


@pytest.mark.parametrize("y0,y1", [(0, 1), (0, NY), (13, 14), (20, 47), (NY - 5, NY)])
def test_bands_equal_astropy_exactly(packed, y0, y1):
    got, ranges = read_band(packed, y0, y1)
    with fits.open(packed) as h:
        ref = h["FLAGS"].data[y0:y1]
    assert got.shape == (y1 - y0, NX)
    assert np.array_equal(got.astype("int64") & 0xFFFFFFFF, ref.astype("int64") & 0xFFFFFFFF)
    assert len(ranges) == 1          # astropy writes the rows one after another


def test_a_band_is_read_with_far_less_than_the_whole_table(packed):
    src = FileSource(packed)
    comp = read_frame_layout(src).planes[Plane.FLAGS].compression
    got, ranges = read_band(packed, 10, 15)
    assert sum(n for _, n in ranges) < comp.heap_bytes / 4
    assert got.shape == (5, NX)


def test_many_separate_ranges_decode_the_same(packed):
    one, r1 = read_band(packed, 5, 40)
    many, r2 = read_band(packed, 5, 40, max_gap=-1)    # never merge
    assert len(r1) == 1 and len(r2) == 35
    assert np.array_equal(one, many)


def test_unsigned_flags_with_the_high_bit_set(tmp_path):
    path = tmp_path / "u.fits"
    flags = write_frame(path, flags=make_flags(unsigned=True))
    assert (flags >= 2**31).any()
    got, _ = read_band(path, 0, NY)
    with fits.open(path) as h:
        ref = h["FLAGS"].data
    assert np.array_equal(got.astype("int64") & 0xFFFFFFFF, ref.astype("int64") & 0xFFFFFFFF)
    assert np.array_equal(got.astype("int64") & 0xFFFFFFFF, flags.astype("int64"))


def test_all_zero_flags(tmp_path):
    path = tmp_path / "z.fits"
    write_frame(path, flags=np.zeros((NY, NX), "int32"))
    got, _ = read_band(path, 0, NY)
    assert not got.any()


COMP = Compression(row_bytes=8, heap_offset=0, heap_bytes=1000)


def desc(*pairs):
    return np.array(pairs, dtype=">i4").tobytes()


def test_descriptors_are_parsed_and_checked():
    assert parse_descriptors(desc((10, 0), (20, 10)), COMP) == [(10, 0), (20, 10)]
    for bad in [desc((0, 0)), desc((-5, 0)), desc((10, -1)), desc((10, 995)), b"", b"\0" * 12]:
        with pytest.raises(ValueError):
            parse_descriptors(bad, COMP)


def test_q_descriptors_are_eight_byte_pairs():
    q = Compression(row_bytes=16, heap_offset=0, heap_bytes=2**33)
    raw = np.array([(5, 2**32 + 7)], dtype=">i8").tobytes()
    assert parse_descriptors(raw, q) == [(5, 2**32 + 7)]


def test_plan_reads_merges_only_near_neighbours():
    tiles = [(100, 0), (100, 100), (100, 5000), (100, 5100), (100, 20000)]
    assert plan_reads(tiles) == [(0, 200), (5000, 200), (20000, 100)]
    assert plan_reads(tiles, max_gap=15_000) == [(0, 20100)]
    shuffled = [tiles[i] for i in (3, 0, 4, 2, 1)]
    assert plan_reads(shuffled) == plan_reads(tiles)         # order does not matter
    assert plan_reads([(10, 0), (30, 5)]) == [(0, 35)]        # overlapping ranges


def test_missing_heap_bytes_are_detected(packed):
    src = FileSource(packed)
    spec = read_frame_layout(src).planes[Plane.FLAGS]
    comp = spec.compression
    tiles = parse_descriptors(src.read(spec.offset, 8 * 3), comp)
    ranges = plan_reads(tiles)
    with pytest.raises(ValueError, match="missing"):
        decode_rows(comp, NX, tiles, ranges, [b"short"] * len(ranges))
    with pytest.raises(ValueError, match="missing"):
        decode_rows(comp, NX, tiles, [], [])


def test_corrupt_compressed_bytes_raise_value_error(packed):
    src = FileSource(packed)
    spec = read_frame_layout(src).planes[Plane.FLAGS]
    comp = spec.compression
    tiles = parse_descriptors(src.read(spec.offset, 8 * 4), comp)
    ranges = plan_reads(tiles)
    junk = [bytes(np.random.default_rng(0).integers(0, 256, n, dtype="uint8")) for _, n in ranges]
    try:
        out = decode_rows(comp, NX, tiles, ranges, junk)
    except ValueError:
        return
    # Rice has no checksum, so random bytes can decode to *something*; it must at least
    # have the right shape and never crash with a different exception type.
    assert out.shape == (4, NX)
