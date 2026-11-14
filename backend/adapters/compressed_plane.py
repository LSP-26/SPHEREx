"""Read rows of a FITS tile-compressed plane (FLAGS from pipeline v27 on) with two small reads.

The plane is a BINTABLE with one row per image row. Each row is a descriptor
(n_bytes, heap_offset), big-endian, and the Rice-compressed bytes of that image row sit in a
heap after the table. A band of rows therefore costs:

  1. the descriptors of the wanted rows (8 bytes each),
  2. the heap bytes they point at (usually one contiguous range, about 4 KB per row).

Decoding is left to astropy, through its public API: we assemble a small, valid FITS file in
memory (the same compressed table, only the wanted rows) and let astropy open it. That avoids
depending on astropy's private codec module and on a Rice decoder of our own.
"""
from __future__ import annotations

import io
import warnings
from bisect import bisect_right

import numpy as np
from astropy.io import fits

from contracts import Compression

BLOCK = 2880
MAX_GAP = 4096   # heap ranges closer than this are fetched as one request


def parse_descriptors(raw: bytes, comp: Compression) -> list[tuple[int, int]]:
    """[(n_bytes, heap_offset), ...] for consecutive rows, each checked against the heap."""
    if not raw or len(raw) % comp.row_bytes:
        raise ValueError(f"descriptor read is {len(raw)} bytes, not a multiple of "
                         f"{comp.row_bytes}")
    dtype = ">i4" if comp.row_bytes == 8 else ">i8"
    out = []
    for count, offset in np.frombuffer(raw, dtype=dtype).reshape(-1, 2).tolist():
        if count <= 0 or offset < 0 or offset + count > comp.heap_bytes:
            raise ValueError(f"bad tile descriptor (n_bytes {count}, heap offset {offset}, "
                             f"heap is {comp.heap_bytes} bytes)")
        out.append((count, offset))
    return out


def plan_reads(tiles: list[tuple[int, int]], max_gap: int = MAX_GAP) -> list[tuple[int, int]]:
    """Heap ranges (start, length) that cover every tile; near neighbours are merged."""
    merged: list[list[int]] = []
    for start, end in sorted((off, off + n) for n, off in tiles):
        if merged and start <= merged[-1][1] + max_gap:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(s, e - s) for s, e in merged]


def _padded(n: int) -> int:
    return -(-n // BLOCK) * BLOCK


def _file_bytes(comp: Compression, ncols: int, table: bytes, heap: bytes, n_rows: int,
                max_count: int, bzero: float, bscale: float) -> bytes:
    cards = [
        ("XTENSION", "BINTABLE"), ("BITPIX", 8), ("NAXIS", 2), ("NAXIS1", comp.row_bytes),
        ("NAXIS2", n_rows), ("PCOUNT", len(heap)), ("GCOUNT", 1), ("TFIELDS", 1),
        ("TTYPE1", "COMPRESSED_DATA"),
        ("TFORM1", f"1{'P' if comp.row_bytes == 8 else 'Q'}B({max_count})"),
        ("ZIMAGE", True), ("ZCMPTYPE", comp.type), ("ZBITPIX", 32), ("ZNAXIS", 2),
        ("ZNAXIS1", ncols), ("ZNAXIS2", n_rows), ("ZTILE1", ncols), ("ZTILE2", 1),
        ("ZNAME1", "BLOCKSIZE"), ("ZVAL1", comp.blocksize),
        ("ZNAME2", "BYTEPIX"), ("ZVAL2", comp.bytepix),
    ]
    if bzero != 0.0:
        cards.append(("BZERO", bzero))
    if bscale != 1.0:
        cards.append(("BSCALE", bscale))
    primary = fits.PrimaryHDU().header.tostring().encode("ascii")
    header = fits.Header(cards).tostring().encode("ascii")
    data = table + heap
    return primary + header + data + b"\0" * (_padded(len(data)) - len(data))


def decode_rows(comp: Compression, ncols: int, tiles: list[tuple[int, int]],
                ranges: list[tuple[int, int]], parts: list[bytes],
                bzero: float = 0.0, bscale: float = 1.0) -> np.ndarray:
    """Decode the rows described by `tiles`, whose bytes were fetched as `parts`
    (one per entry of `ranges`, from plan_reads). Returns float64, shape (len(tiles), ncols),
    like fits_layout.decode_rows does for plain planes."""
    starts = [s for s, _ in ranges]
    pointer = ">i4" if comp.row_bytes == 8 else ">i8"
    heap, table = bytearray(), bytearray()
    for count, offset in tiles:
        i = bisect_right(starts, offset) - 1
        if (i < 0 or offset + count > ranges[i][0] + ranges[i][1]
                or len(parts[i]) != ranges[i][1]):
            raise ValueError("tile bytes are missing from the fetched heap ranges")
        start = ranges[i][0]
        table += np.array([count, len(heap)], dtype=pointer).tobytes()
        heap += parts[i][offset - start:offset - start + count]
    blob = _file_bytes(comp, ncols, bytes(table), bytes(heap), len(tiles),
                       max(n for n, _ in tiles), bzero, bscale)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with fits.open(io.BytesIO(blob), memmap=False) as hdul:
                data = np.array(hdul[1].data)
    except Exception as e:      # astropy raises its own types (CfitsioException, OSError, ...)
        raise ValueError(f"could not decode compressed rows: {e}") from e
    if data.shape != (len(tiles), ncols) or data.dtype.kind not in "iuf":
        raise ValueError(f"decoded {data.shape} {data.dtype}, expected "
                         f"{(len(tiles), ncols)} integers")
    return data.astype("float64")
