from dataclasses import replace

import numpy as np

from backend.adapters.fixtures import FixtureFrameIndex, FixtureStampSource
from backend.adapters.stubs import MemoryCache, StubRenderer, StubWavelength
from backend.jobs.preview import MAX_MASKED_FRACTION, masked_fraction, run_preview
from contracts import (
    DoneEvent,
    ErrorCode,
    ErrorEvent,
    FlipbookRequest,
    FrameEvent,
    ProgressEvent,
)

REQ = FlipbookRequest(ra=10.0, dec=0.0, fov_arcmin=5.0)
DICHROIC = 1 << 7
COLD = 1 << 11    # a detector defect: the picture cannot show it


class MaskedSource:
    """Fixture stamps; the named frames come back mostly flagged (COLD unless told)."""

    def __init__(self, masked_ids, fraction=0.8, flag=COLD):
        self.inner = FixtureStampSource("synthetic_test")
        self.masked_ids, self.fraction, self.flag, self.fetches = set(masked_ids), fraction, flag, 0

    async def fetch(self, frame, pos, half_size_px):
        self.fetches += 1
        st = await self.inner.fetch(frame, pos, half_size_px)
        if frame.frame_id in self.masked_ids:
            flags = np.zeros(st.image.shape, "uint32")
            flags[: int(self.fraction * flags.shape[0])] = self.flag
            st = replace(st, flags=flags)
        return st


def deps(stamps, cache=None):
    return dict(index=FixtureFrameIndex("synthetic_test"), stamps=stamps,
                wavelength=StubWavelength(), renderer=StubRenderer(), cache=cache or MemoryCache())


async def collect(**kw):
    return [e async for e in run_preview(REQ, **kw)]


def test_masked_fraction_counts_only_pixels_the_picture_cannot_show():
    flags = np.zeros((10, 10), "uint32")
    flags[:5] = COLD               # a detector defect: hidden
    flags[7:9] = DICHROIC          # fatal for measurements but shown, so it does not count
    flags[5:7] = 1 << 21           # SOURCE: not fatal
    assert masked_fraction(flags) == 0.5
    assert MAX_MASKED_FRACTION == 0.5


async def test_mostly_masked_frames_are_skipped_and_the_rest_delivered():
    events = await collect(**deps(MaskedSource({"synth_00", "synth_03"})))
    frames = [e for e in events if isinstance(e, FrameEvent)]
    assert len(frames) == 4
    assert {f.label.frame_id for f in frames}.isdisjoint({"synth_00", "synth_03"})
    assert isinstance(events[-1], DoneEvent) and events[-1].n_frames == 4
    last_progress = [e for e in events if isinstance(e, ProgressEvent)][-1]
    assert (last_progress.done, last_progress.total) == (6, 6)    # skipped frames still count


async def test_a_lightly_masked_frame_is_kept():
    events = await collect(**deps(MaskedSource({"synth_00"}, fraction=0.3)))
    assert sum(isinstance(e, FrameEvent) for e in events) == 6


async def test_everything_masked_gives_a_clear_error():
    ids = {f"synth_0{i}" for i in range(6)}
    events = await collect(**deps(MaskedSource(ids)))
    assert isinstance(events[-1], ErrorEvent) and events[-1].code is ErrorCode.NO_COVERAGE
    assert "masked" in events[-1].message


async def test_a_skipped_frame_is_remembered_so_the_second_run_does_not_refetch_it():
    cache = MemoryCache()
    src = MaskedSource({"synth_00"})
    await collect(**deps(src, cache))
    first = src.fetches
    events = await collect(**deps(src, cache))
    assert src.fetches == first                       # nothing fetched again, skipped one included
    assert sum(isinstance(e, FrameEvent) for e in events) == 5


async def test_frames_that_are_mostly_dichroic_are_kept_because_their_pixels_are_shown():
    events = await collect(**deps(MaskedSource({"synth_00", "synth_03"}, flag=DICHROIC)))
    frames = [e for e in events if isinstance(e, FrameEvent)]
    assert not any(isinstance(e, ErrorEvent) for e in events)
    assert {"synth_00", "synth_03"} <= {f.label.frame_id for f in frames}
