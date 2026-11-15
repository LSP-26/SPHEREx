import numpy as np

from backend.adapters.stubs import MemoryCache, StubRenderer, StubWavelength
from backend.jobs.flipbook import run_flipbook
from backend.jobs.refine import channel_of, plan_groups, run_refine
from backend.pipeline.render import preview_size_px
from backend.pipeline.select import make_selector
from contracts import (
    DoneEvent,
    ErrorCode,
    ErrorEvent,
    FlipbookRequest,
    FrameEvent,
    FrameRef,
    ProgressEvent,
    Stage,
    Stamp,
)

PX = 6.15 / 3600
HEADER = {"NAXIS": 2, "NAXIS1": 2040, "NAXIS2": 2040, "CTYPE1": "RA---TAN", "CTYPE2": "DEC--TAN",
          "CRPIX1": 1020.0, "CRPIX2": 1020.0, "CRVAL1": 10.0, "CRVAL2": 0.0,
          "CDELT1": -PX, "CDELT2": PX, "CUNIT1": "deg", "CUNIT2": "deg"}
REQ = FlipbookRequest(ra=10.0, dec=0.0, fov_arcmin=5.0, use_zodi=False)

# two visits far apart, three frames each, all on detector 1
FRAMES = [FrameRef(frame_id=f"f{i}", detector=1, t_start=t, t_end=t + 0.01, release="QR3",
                   version="v27", uri=f"s3://x/{i}", wcs_header=HEADER)
          for i, t in enumerate([100.0, 100.5, 101.0, 300.0, 300.5, 301.0])]


class Index:
    async def frames_at(self, pos, margin_px=30):
        return list(FRAMES)


class Source:
    """Stamps cut around the target, with the star at the target pixel."""

    def __init__(self):
        self.fetched = []

    async def fetch(self, frame, pos, half):
        self.fetched.append(frame.frame_id)
        n = 2 * half + 1
        rng = np.random.default_rng(int(frame.frame_id[1:]))
        img = rng.normal(1.0, 0.1, (n, n)).astype("float32")
        img[half, half] += 50.0
        return Stamp(frame=frame, x0=1019 - half, y0=1019 - half, image=img,
                     flags=np.zeros((n, n), "uint32"),
                     variance=np.full((n, n), 0.01, "float32"), zodi=None,
                     wcs_header=dict(HEADER), source="s3")


def deps(src, cache=None):
    return dict(index=Index(), stamps=src, wavelength=StubWavelength(),
                cache=cache or MemoryCache(), select=make_selector(None))


async def refine(src, cache=None, **kw):
    return [e async for e in run_refine(REQ, **deps(src, cache), **kw)]


def test_channel_of_covers_the_detector():
    assert channel_of(0) == 1 and channel_of(119) == 1 and channel_of(120) == 2
    assert channel_of(2039) == 17 and channel_of(5000) == 17


def test_plan_groups_has_one_group_per_slot_and_respects_the_cap():
    sel = make_selector(None)(list(FRAMES), REQ)
    groups = plan_groups(list(FRAMES), sel)
    assert [g.slot for g in groups] == [0, 1] and [len(g.frames) for g in groups] == [3, 3]
    assert [len(g.frames) for g in plan_groups(list(FRAMES), sel, cap=2)] == [1, 1]
    assert groups[0].gid != groups[1].gid


async def test_refined_frames_take_the_preview_slots_and_carry_the_badges():
    src, cache = Source(), MemoryCache()
    events = await refine(src, cache)
    frames = [e for e in events if isinstance(e, FrameEvent)]
    assert sorted(f.slot for f in frames) == [0, 1] and len(src.fetched) == 6
    assert all(f.stage is Stage.REFINED for f in frames)
    f = frames[0]
    assert f.channels_covered == 1 and f.partial is True        # one band of the 17
    assert f.label.frame_id.startswith("coadd_v") and f.label.release == "QR3"
    assert "3 frames co-added" in f.label.flags_summary
    assert f.width == f.height == preview_size_px(5.0)
    png = await cache.get(f.png_url.rsplit("/", 1)[1].removesuffix(".png"))
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    progress = [e for e in events if isinstance(e, ProgressEvent)]
    assert progress[0].done == 0 and progress[-1].done == progress[-1].total == 6
    assert all(p.stage is Stage.REFINED for p in progress)


async def test_a_second_run_uses_the_cached_coadds():
    src, cache = Source(), MemoryCache()
    await refine(src, cache)
    again = await refine(src, cache)
    assert len(src.fetched) == 6                                # nothing fetched again
    assert sum(isinstance(e, FrameEvent) for e in again) == 2


async def test_the_cap_limits_how_many_frames_are_read():
    src = Source()
    await refine(src, max_frames=2)
    assert len(src.fetched) == 2


async def test_cancel_ends_with_a_cancelled_error():
    import asyncio
    cancel = asyncio.Event()
    cancel.set()
    events = await refine(Source(), cancel=cancel)
    assert isinstance(events[-1], ErrorEvent) and events[-1].code is ErrorCode.CANCELLED


async def test_flipbook_sends_refined_frames_before_a_single_done():
    d = deps(Source())
    events = [e async for e in run_flipbook(REQ, refine=True, renderer=StubRenderer(), **d)]
    stages = [e.stage for e in events if isinstance(e, FrameEvent)]
    assert stages.count(Stage.PREVIEW) == 2 and stages.count(Stage.REFINED) == 2
    assert stages.index(Stage.REFINED) > max(i for i, s in enumerate(stages) if s is Stage.PREVIEW)
    assert isinstance(events[-1], DoneEvent) and events[-1].n_frames == 2
    assert sum(isinstance(e, DoneEvent) for e in events) == 1


async def test_flipbook_without_refine_is_just_the_preview():
    events = [e async for e in run_flipbook(REQ, renderer=StubRenderer(), **deps(Source()))]
    assert all(e.stage is Stage.PREVIEW for e in events if isinstance(e, FrameEvent))
    assert isinstance(events[-1], DoneEvent)


async def test_a_refine_crash_keeps_the_preview_and_still_finishes():
    class Broken(Source):
        calls = 0

        async def fetch(self, frame, pos, half):
            Broken.calls += 1
            if Broken.calls > 2:            # the two preview frames work, refine reads fail
                raise RuntimeError("boom")
            return await super().fetch(frame, pos, half)

    events = [e async for e in run_flipbook(REQ, refine=True, renderer=StubRenderer(),
                                            **deps(Broken()))]
    assert sum(isinstance(e, FrameEvent) for e in events) == 2    # previews only
    assert isinstance(events[-1], DoneEvent)


def test_a_refined_picture_fills_a_one_pixel_hole_instead_of_drawing_it_black():
    from backend.jobs.refine import as_cleaned
    from backend.pipeline.render import render_array
    img = np.random.default_rng(1).normal(1.0, 0.05, (30, 30)).astype("float32")
    img[10, 10] = np.nan
    c = as_cleaned(img, np.full((30, 30), 0.0025, "float32"), False)
    assert np.isfinite(c.display[10, 10]) and render_array(c)[10, 10] > 0
