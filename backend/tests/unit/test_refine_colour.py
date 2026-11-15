from backend.adapters.stubs import MemoryCache, StubWavelength
from backend.jobs.refine import run_refine
from backend.pipeline.select import make_selector
from backend.tests.unit.test_refine_job import HEADER, REQ, Source
from contracts import FrameEvent, FrameRef

FR = [FrameRef(frame_id=f"f{10 * v + d}", detector=d, t_start=t + 0.01 * d, t_end=t + 0.02 +
               0.01 * d, release="QR3", version="v27", uri=f"s3://x/{v}{d}", wcs_header=HEADER)
      for v, t in enumerate([100.0, 300.0]) for d in (1, 3, 5)]


class Index:
    async def frames_at(self, pos, margin_px=30):
        return list(FR)


async def run(req=REQ):
    return [e async for e in run_refine(req, index=Index(), stamps=Source(),
                                        wavelength=StubWavelength(),
                                        cache=MemoryCache(), select=make_selector(None))]


async def test_each_visit_gets_one_merged_colour_frame_after_the_detector_frames():
    frames = [e for e in await run() if isinstance(e, FrameEvent)]
    colour = [f for f in frames if f.label.detector == 0]
    assert len(frames) == 8 and len(colour) == 2
    assert sorted(f.slot for f in colour) == [6, 7] and not colour[0].partial
    assert "merged colour of D1, D3, D5" in colour[0].label.flags_summary[0]


async def test_a_single_detector_request_gets_no_colour():
    frames = [e for e in await run(REQ.model_copy(update={"detector": 3}))
                if isinstance(e, FrameEvent)]
    assert all(f.label.detector != 0 for f in frames)
