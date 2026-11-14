"""Find where each plane of a FITS file starts, using only small range reads.

Used by the X2 check now, and by the catalogue builder (D1.1) and S3 stamp reader (D4) later.
Planes are uncompressed and row-major, so a row band is one contiguous byte range.
Sync on purpose: it is a small helper. Async callers feed it a source of their own.
"""
from __future__ import annotations

import math
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
from astropy.io import fits

BLOCK = 2880
_DTYPES = {8: "u1", 16: ">i2", 32: ">i4", 64: ">i8", -32: ">f4", -64: ">f8"}


class RangeNotSupported(RuntimeError):
    pass


class ByteSource(Protocol):
    @property
    def size(self) -> int: ...
    def read(self, start: int, length: int) -> bytes: ...


class FileSource:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    @property
    def size(self) -> int:
        return self.path.stat().st_size

    def read(self, start: int, length: int) -> bytes:
        with open(self.path, "rb") as f:
            f.seek(start)
            return f.read(length)


class HttpRangeSource:
    """Reads byte ranges over HTTP. Raises RangeNotSupported if the server ignores Range."""

    def __init__(self, url: str, timeout: float = 30.0):
        self.url, self.timeout = url, timeout
        self._size: int | None = None
        self.requests = 0
        self.bytes_read = 0

    @property
    def size(self) -> int:
        if self._size is None:
            self.read(0, 1)
        assert self._size is not None
        return self._size

    def read(self, start: int, length: int) -> bytes:
        req = urllib.request.Request(
            self.url, headers={"Range": f"bytes={start}-{start + length - 1}"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            if r.status != 206:
                raise RangeNotSupported(f"expected HTTP 206, got {r.status}")
            m = re.match(r"bytes \d+-\d+/(\d+)", r.headers.get("Content-Range", ""))
            if m:
                self._size = int(m.group(1))
            data = r.read()
        self.requests += 1
        self.bytes_read += len(data)
        return data


@dataclass(frozen=True)
class HduLayout:
    index: int
    extname: str | None
    xtension: str | None      # None for the primary HDU
    bitpix: int
    shape: tuple[int, ...]    # numpy order: (rows, columns) for a 2-D image
    bzero: float
    bscale: float
    header_start: int
    data_start: int           # byte offset of the first data byte
    data_nbytes: int
    compressed: bool

    @property
    def is_image2d(self) -> bool:
        return self.xtension in (None, "IMAGE") and len(self.shape) == 2 and not self.compressed

    @property
    def row_bytes(self) -> int:
        return self.shape[-1] * abs(self.bitpix) // 8


def _read_header(src: ByteSource, start: int) -> tuple[fits.Header, int]:
    """Returns (header, length of the header in bytes, a multiple of 2880)."""
    chunks, pos = [], start
    while True:
        n = min(4 * BLOCK, src.size - pos)
        if n <= 0:
            raise ValueError("ran off the end of the file looking for END")
        chunks.append(src.read(pos, n))
        pos += n
        text = b"".join(chunks).decode("ascii", errors="replace")
        for i in range(0, len(text), 80):
            if text[i:i + 8] == "END     ":
                length = ((i + 80 + BLOCK - 1) // BLOCK) * BLOCK
                return fits.Header.fromstring(text[:length]), length


def read_header(src: ByteSource, hdu: HduLayout) -> fits.Header:
    return _read_header(src, hdu.header_start)[0]


def read_layout(src: ByteSource) -> list[HduLayout]:
    out: list[HduLayout] = []
    offset, size = 0, src.size
    while offset < size:
        header, hlen = _read_header(src, offset)
        naxis = int(header.get("NAXIS", 0))
        axes = [int(header[f"NAXIS{i}"]) for i in range(1, naxis + 1)]
        bitpix = int(header["BITPIX"])
        n = math.prod(axes) if axes else 0
        nbytes = (abs(bitpix) // 8) * int(header.get("GCOUNT", 1)) * (
            int(header.get("PCOUNT", 0)) + n) if axes else 0
        data_start = offset + hlen
        out.append(HduLayout(
            index=len(out), extname=header.get("EXTNAME"), xtension=header.get("XTENSION"),
            bitpix=bitpix, shape=tuple(reversed(axes)), bzero=float(header.get("BZERO", 0.0)),
            bscale=float(header.get("BSCALE", 1.0)), header_start=offset,
            data_start=data_start, data_nbytes=nbytes,
            compressed=bool(header.get("ZIMAGE", False))))
        offset = data_start + ((nbytes + BLOCK - 1) // BLOCK) * BLOCK
    return out


def read_rows(src: ByteSource, hdu: HduLayout, y0: int, y1: int) -> np.ndarray:
    """Rows y0 (inclusive) to y1 (exclusive), full width, as float64 with BSCALE/BZERO applied.
    One contiguous byte range."""
    if not hdu.is_image2d:
        raise ValueError(f"HDU {hdu.index} ({hdu.extname}) is not an uncompressed 2-D image")
    if not 0 <= y0 < y1 <= hdu.shape[0]:
        raise ValueError(f"rows {y0}:{y1} outside 0:{hdu.shape[0]}")
    raw = src.read(hdu.data_start + y0 * hdu.row_bytes, (y1 - y0) * hdu.row_bytes)
    return decode_rows(raw, hdu.bitpix, hdu.bzero, hdu.bscale, hdu.shape[1])


def decode_rows(raw: bytes, bitpix: int, bzero: float, bscale: float, ncols: int) -> np.ndarray:
    """Big-endian FITS bytes to float64 rows, with BSCALE/BZERO applied."""
    arr = np.frombuffer(raw, dtype=_DTYPES[bitpix]).reshape(-1, ncols).astype("float64")
    if bscale != 1.0 or bzero != 0.0:
        arr = arr * bscale + bzero
    return arr
