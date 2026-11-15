import struct
import zlib

import numpy as np

from backend.adapters.stubs import MemoryCache, StubWavelength
from backend.jobs import refine as refine_mod
from backend.jobs.refine import (
    Member,
    pack,
    run_refine,
    saturated_mask,
    unpack,
    unpack_with_mask,
)
from backend.pipeline.select import make_selector
from backend.tests.unit.test_refine_job import FRAMES, HEADER, REQ, Index
from contracts import FrameEvent, Stamp

OVERFLOW = 1 << 1


def gray_of(png: bytes) -> np.ndarray:
    """Decode the grey channel of the 8-bit PNGs that render.py writes (filter 0 on every row;
    grey, or grey + alpha when the picture marks pixels with no data)."""
    pos, idat, w, h, ctype = 8, b"", 0, 0, 0
    while pos < len(png):
        n = struct.unpack(">I", png[pos:pos + 4])[0]
        tag, body = png[pos + 4:pos + 8], png[pos + 8:pos + 8 + n]
        if tag == b"IHDR":
            w, h = struct.unpack(">II", body[:8])
            ctype = body[9]
        elif tag == b"IDAT":
            idat += body
        pos += 12 + n
    raw = zlib.decompress(idat)
    step = 2 if ctype == 4 else 1
    rows = [np.frombuffer(raw[i * (w * step + 1) + 1:(i + 1) * (w * step + 1)], "uint8")
            for i in range(h)]
    return np.stack(rows)[:, ::step]


class SatSource:
    """Stamps with a bright star at the target and a 3 x 3 OVERFLOW block on its core."""

    async def fetch(self, frame, pos, half):
        n = 2 * half + 1
        rng = np.random.default_rng(int(frame.frame_id[1:]))
        img = rng.normal(1.0, 0.1, (n, n)).astype("float32")
        img[half - 1:half + 2, half - 1:half + 2] += 50.0
        flags = np.zeros((n, n), "uint32")
        flags[half - 1:half + 2, half - 1:half + 2] = OVERFLOW
        return Stamp(frame=frame, x0=1019 - half, y0=1019 - half, image=img, flags=flags,
                     variance=np.full((n, n), 0.01, "float32"), zodi=None,
                     wcs_header=dict(HEADER), source="s3")


async def refined_grays(cache):
    events = [e async for e in run_refine(
        REQ, index=Index(), stamps=SatSource(), wavelength=StubWavelength(), cache=cache,
        select=make_selector(None))]
    out = []
    for e in events:
        if isinstance(e, FrameEvent):
            png = await cache.get(e.png_url.rsplit("/", 1)[1].removesuffix(".png"))
            out.append(gray_of(png))
    return out


async def test_refined_frames_draw_the_saturated_core_white():
    grays = await refined_grays(MemoryCache())
    assert len(grays) == 2
    for g in grays:
        mid = g.shape[0] // 2
        assert g[mid, mid] == 255
        assert 5 <= int((g == 255).sum()) <= 20          # the block, nothing else is white


async def test_the_white_core_survives_the_cache():
    cache = MemoryCache()
    first = await refined_grays(cache)
    second = await refined_grays(cache)                  # co-adds come from the cache now
    assert len(second) == 2
    for a, b in zip(first, second, strict=True):
        assert np.array_equal(a, b) and b[b.shape[0] // 2, b.shape[1] // 2] == 255


def member(sat, shape=(5, 5)):
    return Member(np.ones(shape, "float32"), np.ones(shape, "float32"), 1.0, 100.0, 1, False,
                  "QR3", "v27", sat)


def test_a_pixel_is_saturated_when_at_least_half_the_frames_say_so():
    a, b, c = (np.zeros((5, 5), bool) for _ in range(3))
    a[1, 1] = b[1, 1] = True                  # 2 of 3
    c[3, 3] = True                            # 1 of 3
    m = saturated_mask([member(a), member(b), member(c)])
    assert m[1, 1] and not m[3, 3] and m.sum() == 1
    assert saturated_mask([member(None), member(None)]).sum() == 0     # no masks at all


def test_the_mask_is_cached_with_the_co_add_and_old_entries_still_load():
    img, var = np.ones((4, 4), "float32"), np.ones((4, 4), "float32")
    sat = np.zeros((4, 4), bool)
    sat[2, 2] = True
    i, v, s = unpack_with_mask(pack(img, var, sat))
    assert np.array_equal(s, sat) and np.array_equal(i, img) and np.array_equal(v, var)
    assert unpack(pack(img, var, sat))[0].shape == (4, 4)
    assert not unpack_with_mask(pack(img, var))[2].any()               # no mask given


def test_the_version_was_bumped_so_old_co_adds_are_not_reused():
    assert refine_mod.REFINE_VERSION >= 2
    assert len(FRAMES) == 6                   # the fixtures this test file relies on
