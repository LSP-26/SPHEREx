"""Find where each plane of a SPHEREx frame starts, with as few range requests as possible.

A frame file is: primary header, IMAGE, FLAGS, VARIANCE, ZODI, then the PSF and WCS-WAVE (which
we never read). Header lengths vary between pipeline versions (and v27 stores FLAGS tile
compressed, so its size varies per frame), so offsets cannot be guessed. Instead we walk the
headers: each extension starts where the previous one ends, and that end follows from the
previous header (BITPIX, NAXISn, PCOUNT). That is one request for the primary and IMAGE headers
together, then one each for FLAGS, VARIANCE and ZODI: 4 requests, about 70 KB, instead of the
13 requests of fits_layout.read_layout.

Every plane is checked against what the stamp reader expects, so a frame in a format we do not
understand fails here, loudly, instead of producing bad pixels later.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

from astropy.io import fits

from backend.adapters.fits_layout import BLOCK, ByteSource
from contracts import Compression, Plane, PlaneSpec

FIRST_CHUNK = 32768   # primary header (2,880) + IMAGE header (25,920 at most, so far)
NEXT_CHUNK = 16384    # FLAGS, VARIANCE and ZODI headers (11,520 to 14,400 so far)
MAX_EXTENSIONS = 12
EXPECTED_BITPIX = {Plane.IMAGE: -32, Plane.FLAGS: 32, Plane.VARIANCE: -32, Plane.ZODI: -32}
_TFORM = re.compile(r"1([PQ])B(\(\d+\))?")


@dataclass(frozen=True)
class FrameLayout:
    planes: dict[Plane, PlaneSpec]
    image_header: fits.Header
    shape: tuple[int, int]            # (rows, columns)


def _padded(n: int) -> int:
    return -(-n // BLOCK) * BLOCK


def data_nbytes(h: fits.Header) -> int:
    """Size of an HDU's data (the FITS formula, valid for images and binary tables)."""
    naxis = int(h.get("NAXIS", 0))
    if naxis == 0:
        return 0
    n = math.prod(int(h[f"NAXIS{i}"]) for i in range(1, naxis + 1))
    return abs(int(h["BITPIX"])) // 8 * int(h.get("GCOUNT", 1)) * (int(h.get("PCOUNT", 0)) + n)


def _find_end(buf: bytes) -> int | None:
    i = buf.find(b"END     ")
    while i != -1 and i % 80:
        i = buf.find(b"END     ", i + 1)
    return None if i == -1 else i


class _Walker:
    def __init__(self, src: ByteSource, first: bytes):
        self.src, self.first = src, first

    def header(self, start: int, chunk: int) -> tuple[fits.Header, int]:
        """The header that starts at `start` and its length (a multiple of 2,880)."""
        buf = self.first[start:] if start < len(self.first) else b""
        while True:
            end = _find_end(buf)
            if end is not None and len(buf) >= _padded(end + 80):
                length = _padded(end + 80)
                return fits.Header.fromstring(buf[:length].decode("ascii", "replace")), length
            more = self.src.read(start + len(buf), chunk)
            if not more:
                raise ValueError(f"ran off the end of the file looking for a header at {start}")
            buf += more


def _compressed_spec(h: fits.Header, data_start: int, name: str) -> PlaneSpec:
    def fail(why: str) -> ValueError:
        return ValueError(f"{name}: unsupported compressed plane ({why})")

    if h.get("XTENSION") != "BINTABLE":
        raise fail("ZIMAGE set but not a binary table")
    ctype = str(h.get("ZCMPTYPE", "")).strip()
    if ctype not in ("RICE_1", "RICE_ONE"):
        raise fail(f"ZCMPTYPE {ctype!r}, only RICE_1 is handled")
    if int(h.get("ZNAXIS", 0)) != 2:
        raise fail("not 2-D")
    ncols, nrows = int(h["ZNAXIS1"]), int(h["ZNAXIS2"])
    if int(h.get("ZTILE1", ncols)) != ncols or int(h.get("ZTILE2", 1)) != 1:
        raise fail("tiles are not whole rows")
    zbitpix = int(h["ZBITPIX"])
    if zbitpix != EXPECTED_BITPIX[Plane(name)]:
        raise fail(f"ZBITPIX {zbitpix}")
    if int(h.get("TFIELDS", 0)) != 1 or str(h.get("TTYPE1", "")).strip() != "COMPRESSED_DATA":
        raise fail("columns other than COMPRESSED_DATA (fallback tiles?)")
    m = _TFORM.fullmatch(str(h.get("TFORM1", "")).replace(" ", "").upper())
    if m is None:
        raise fail(f"TFORM1 {h.get('TFORM1')!r}")
    row_bytes = 8 if m[1] == "P" else 16
    if int(h["NAXIS1"]) != row_bytes or int(h["NAXIS2"]) != nrows:
        raise fail("table shape does not match the image")
    blocksize, bytepix = 32, 4
    i = 1
    while f"ZNAME{i}" in h:
        key = str(h[f"ZNAME{i}"]).strip().upper()
        if key == "BLOCKSIZE":
            blocksize = int(h[f"ZVAL{i}"])
        elif key == "BYTEPIX":
            bytepix = int(h[f"ZVAL{i}"])
        i += 1
    theap = int(h.get("THEAP", row_bytes * nrows))
    heap_bytes = row_bytes * nrows + int(h.get("PCOUNT", 0)) - theap
    if heap_bytes <= 0:
        raise fail("empty heap")
    return PlaneSpec(
        offset=data_start, bitpix=zbitpix, bzero=float(h.get("BZERO", 0.0)),
        bscale=float(h.get("BSCALE", 1.0)),
        compression=Compression(row_bytes=row_bytes, heap_offset=data_start + theap,
                                heap_bytes=heap_bytes, blocksize=blocksize, bytepix=bytepix))


def _plane_spec(h: fits.Header, data_start: int, name: str) -> tuple[PlaneSpec, tuple[int, int]]:
    """(spec, (rows, columns)) for one plane, or ValueError if we cannot read it."""
    if h.get("ZIMAGE") is True:
        spec = _compressed_spec(h, data_start, name)
        return spec, (int(h["ZNAXIS2"]), int(h["ZNAXIS1"]))
    if h.get("XTENSION") != "IMAGE" or int(h.get("NAXIS", 0)) != 2:
        raise ValueError(f"{name}: not a plain 2-D image")
    bitpix = int(h["BITPIX"])
    if bitpix != EXPECTED_BITPIX[Plane(name)]:
        raise ValueError(f"{name}: BITPIX {bitpix}, expected {EXPECTED_BITPIX[Plane(name)]}")
    spec = PlaneSpec(offset=data_start, bitpix=bitpix, bzero=float(h.get("BZERO", 0.0)),
                     bscale=float(h.get("BSCALE", 1.0)))
    return spec, (int(h["NAXIS2"]), int(h["NAXIS1"]))


def read_frame_layout(src: ByteSource, *, first_chunk: int = FIRST_CHUNK,
                      next_chunk: int = NEXT_CHUNK) -> FrameLayout:
    """Walk the headers of one frame. ValueError means the file is not in a format we can read."""
    walker = _Walker(src, src.read(0, first_chunk))
    primary, length = walker.header(0, first_chunk)
    pos = length + _padded(data_nbytes(primary))
    planes: dict[Plane, PlaneSpec] = {}
    shape: tuple[int, int] | None = None
    image_header: fits.Header | None = None
    wanted = {p.value for p in Plane}
    for _ in range(MAX_EXTENSIONS):
        if pos >= src.size:     # no more extensions: report what is missing below
            break
        header, length = walker.header(pos, next_chunk)
        data_start = pos + length
        name = header.get("EXTNAME")
        if name in wanted and Plane(name) not in planes:
            spec, this = _plane_spec(header, data_start, name)
            if shape is not None and this != shape:
                raise ValueError(f"{name}: shape {this}, other planes are {shape}")
            shape = this
            planes[Plane(name)] = spec
            if name == Plane.IMAGE.value:
                image_header = header
            if len(planes) == len(wanted):
                break
        pos = data_start + _padded(data_nbytes(header))
    missing = sorted(wanted - {p.value for p in planes})
    if missing or shape is None or image_header is None:
        raise ValueError(f"missing planes {missing}")
    return FrameLayout(planes=planes, image_header=image_header, shape=shape)


# ---------------------------------------------------------------- catalogue serialisation
def spec_to_list(spec: PlaneSpec) -> list:
    """[offset, bitpix, bzero, bscale] (the original shape), plus a dict for compressed planes."""
    out: list = [spec.offset, spec.bitpix, spec.bzero, spec.bscale]
    if spec.compression is not None:
        out.append(spec.compression.model_dump(mode="json"))
    return out


def spec_from_list(v: list) -> PlaneSpec:
    """Inverse of spec_to_list. Four-item lists (shards written before v0.2.0) still load."""
    return PlaneSpec(offset=int(v[0]), bitpix=int(v[1]), bzero=float(v[2]), bscale=float(v[3]),
                     compression=Compression(**v[4]) if len(v) > 4 and v[4] else None)
