from backend.server.deps import PrefetchingIndex
from contracts import FrameRef, SkyPosition
from contracts.interfaces import FrameIndex

POS = SkyPosition(ra=10.0, dec=0.0)
FRAMES = [FrameRef(frame_id=f"f{i}", detector=1, t_start=1.0 + i, t_end=1.01 + i,
                   release="QR3", version="v27", uri=f"s3://x/{i}") for i in range(3)]


class Inner:
    def __init__(self):
        self.margins = []

    async def frames_at(self, pos, margin_px=30):
        self.margins.append(margin_px)
        return FRAMES


class Wavelength:
    def __init__(self, fail=False):
        self.fail, self.seen = fail, []

    async def prefetch(self, frames):
        self.seen.append(list(frames))
        if self.fail:
            raise RuntimeError("table unreadable")


async def test_tables_are_prefetched_for_the_frames_that_were_found():
    inner, wl = Inner(), Wavelength()
    idx = PrefetchingIndex(inner, wl)
    assert isinstance(idx, FrameIndex)
    assert await idx.frames_at(POS, 45) == FRAMES
    assert inner.margins == [45] and wl.seen == [FRAMES]


async def test_a_table_that_cannot_be_loaded_does_not_break_the_lookup():
    idx = PrefetchingIndex(Inner(), Wavelength(fail=True))
    assert await idx.frames_at(POS) == FRAMES
