"""T10: merged colour from the co-adds of ONE visit. Pure.

B = D1 + D2, G = D3 + D4, R = D5 + D6 (plan A8). Each colour channel is the inverse-variance
mean of the detectors that have a measurement there. A channel with no detector at all is
reported in `missing` and drawn black, never filled in. One asinh stretch (shared scale and
white point) is used for all three channels, so colours are not distorted. Equal weights, no
flat-F_nu calibration yet: the colours are indicative.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from backend.pipeline.clean import robust_sigma

CHANNELS = {"R": (5, 6), "G": (3, 4), "B": (1, 2)}
SOFTENING = 3.0
BLACK_SIGMA = 1.0
MIN_WHITE_SIGMA = 10.0
WHITE_PERCENTILE = 99.5


@dataclass(frozen=True)
class Colour:
    rgb: np.ndarray        # uint8, shape (ny, nx, 3)
    covered: np.ndarray    # bool, True where at least one channel has a number
    missing: tuple[str, ...]


def channel(parts: list[tuple[np.ndarray, np.ndarray]]) -> np.ndarray | None:
    """Inverse-variance mean of (image, variance) pairs, NaN where none is usable."""
    if not parts:
        return None
    f = np.stack([np.asarray(i, "float64") for i, _ in parts])
    v = np.stack([np.asarray(x, "float64") for _, x in parts])
    ok = np.isfinite(f) & np.isfinite(v) & (v > 0)
    w = np.where(ok, 1.0 / np.where(ok, v, 1.0), 0.0)
    ws = w.sum(axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        out = (w * np.where(ok, f, 0.0)).sum(axis=0) / ws
    out[ws == 0] = np.nan
    return out


def merge_colour(dets: dict[int, tuple[np.ndarray, np.ndarray]]) -> Colour:
    """dets maps detector number to its (image, variance) co-add on the common grid."""
    chans = {k: channel([dets[d] for d in ds if d in dets]) for k, ds in CHANNELS.items()}
    present = {k: c for k, c in chans.items() if c is not None}
    if not present:
        raise ValueError("no detector given")
    shape = next(iter(present.values())).shape
    if any(c.shape != shape for c in present.values()):
        raise ValueError("the detectors are not on the same grid")
    covered = np.zeros(shape, bool)
    for c in present.values():
        covered |= np.isfinite(c)
    sigmas = [s for s in (robust_sigma(c[np.isfinite(c)]) for c in present.values()) if s > 0]
    noise = float(np.median(sigmas)) if sigmas else 1.0
    scale = SOFTENING * noise
    vals = np.concatenate([c[np.isfinite(c)] for c in present.values()])
    white = max(float(np.percentile(vals, WHITE_PERCENTILE)) if vals.size else 0.0,
                MIN_WHITE_SIGMA * noise)
    lo, hi = np.arcsinh(-BLACK_SIGMA / SOFTENING), np.arcsinh(white / scale)
    rgb = np.zeros(shape + (3,), "uint8")
    for i, k in enumerate("RGB"):
        c = present.get(k)
        if c is None:
            continue
        s = (np.arcsinh(np.nan_to_num(c, nan=0.0) / scale) - lo) / (hi - lo)
        rgb[..., i] = (np.clip(s, 0.0, 1.0) * 255).astype("uint8")
    rgb[~covered] = 0
    return Colour(rgb=rgb, covered=covered,
                  missing=tuple(k for k in "RGB" if k not in present))
def rgba_png(rgb: np.ndarray, covered: np.ndarray) -> bytes:
    """8-bit RGBA PNG: pixels with no data are transparent (like the grey pictures)."""
    import struct
    import zlib
    h, w, _ = rgb.shape
    alpha = np.where(covered, 255, 0).astype("uint8")
    rows = np.dstack([rgb, alpha]).reshape(h, w * 4)

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    raw = b"".join(b"\x00" + rows[y].tobytes() for y in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
