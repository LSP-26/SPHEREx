import pytest

from backend.adapters.fixtures import FixtureFrameIndex, FixtureStampSource
from backend.adapters.stubs import MemoryCache, StubRenderer, StubWavelength
from backend.jobs.preview import run_preview
from backend.pipeline.select import (
    choose_gap,
    cluster_visits,
    even_subsample,
    make_selector,
    resolution_element,
)
from contracts import FlipbookRequest, FrameRef, PlanEvent

REQ = FlipbookRequest(ra=10.0, dec=0.0)


def fr(i, t, det=1):
    return FrameRef(frame_id=f"f{i}", detector=det, t_start=t, t_end=t + 0.01,
                    release="QR2", version="v1", uri=f"s3://x/{i}")


def visit_frames(starts, per_visit=6, spacing=0.5, det=1):
    out, i = [], 0
    for s in starts:
        for k in range(per_visit):
            out.append(fr(i, s + k * spacing, det))
            i += 1
    return out


def test_gap_found_from_data_for_two_very_different_cadences():
    assert 2 < choose_gap(visit_frames([100, 280, 460])) < 100       # months apart
    assert 1 < choose_gap(visit_frames([100, 114, 128, 142])) < 14   # near the pole: weeks apart


def test_gap_falls_back_to_default_when_unclear():
    assert choose_gap(visit_frames([100], per_visit=3)) == 30.0
    assert choose_gap([]) == 30.0


def test_clustering_splits_into_visits():
    frames = visit_frames([100, 280, 460])
    visits = cluster_visits(frames)
    assert [len(v.frame_ids) for v in visits] == [6, 6, 6]
    assert [v.visit_index for v in visits] == [0, 1, 2]
    assert visits[0].t_end < visits[1].t_start
    assert cluster_visits([]) == []


def test_explicit_gap_overrides():
    assert len(cluster_visits(visit_frames([100, 280, 460]), gap_days=1000)) == 1


def test_even_subsample_keeps_ends():
    out = even_subsample(list(range(10)), 4)
    assert out[0] == 0 and out[-1] == 9 and len(out) == 4
    assert even_subsample([1, 2], 5) == [1, 2]
    assert even_subsample([1, 2, 3], 1) == [1]


def test_resolution_element():
    assert resolution_element(2.0) == pytest.approx(0.05)
    assert resolution_element(4.0) == pytest.approx(4.0 / 120)


def lam_table(table):
    return lambda f: table[f.frame_id]


def test_picks_wavelength_matched_frames_across_visits():
    frames = visit_frames([100, 280, 460], per_visit=2)
    lams = {"f0": 0.800, "f1": 0.900,    # visit 0
            "f2": 0.805, "f3": 0.910,    # visit 1 (offset half a channel)
            "f4": 0.795, "f5": 0.890}    # visit 2
    sel = make_selector(lam_table(lams))(frames, REQ)
    assert sel.visits_total == sel.visits_shown == 3
    assert len(sel.chosen) == 3
    got = [lams[f.frame_id] for f, _ in sel.chosen]
    assert max(got) - min(got) < 0.011   # all about the same wavelength


def test_visit_with_no_match_is_left_out_but_still_counted():
    frames = visit_frames([100, 280, 460, 640], per_visit=1)
    lams = {"f0": 1.0, "f1": 1.0, "f2": 1.0, "f3": 1.5}   # last visit sees another channel
    sel = make_selector(lam_table(lams))(frames, REQ)
    assert sel.visits_total == 4 and sel.visits_shown == 3
    assert [v for _, v in sel.chosen] == [0, 1, 2]


def test_one_frame_per_visit_and_detector_in_time_order():
    frames = visit_frames([100, 280], per_visit=2, det=1) + [
        fr(100 + i, 100.2 + 180 * (i // 2) + i % 2, det=2) for i in range(4)]
    lams = {f.frame_id: 1.0 if f.detector == 1 else 3.0 for f in frames}
    sel = make_selector(lam_table(lams))(frames, REQ)
    keys = [(v, f.detector) for f, v in sel.chosen]
    assert len(keys) == len(set(keys)) == 4
    times = [f.t_mid for f, _ in sel.chosen]
    assert times == sorted(times)


def test_without_wavelengths_takes_middle_frame_of_each_visit():
    frames = visit_frames([100, 280], per_visit=3)
    sel = make_selector(None)(frames, REQ)
    assert [f.frame_id for f, _ in sel.chosen] == ["f1", "f4"]


def test_cap_subsamples_visits_evenly():
    frames = (visit_frames([10 * 15 * k for k in range(12)], per_visit=1, det=1)
              + [fr(100 + i, 10 * 15 * i + 0.2, det=2) for i in range(12)])
    sel = make_selector(None, gap_days=5)(frames, REQ.model_copy(update={"preview_cap": 10}))
    assert sel.visits_total == 12 and sel.visits_shown == 5
    assert len(sel.chosen) == 10
    shown = sorted({v for _, v in sel.chosen})
    assert shown[0] == 0 and shown[-1] == 11


async def test_job_uses_selector_and_reports_k_of_n_visits():
    sel = make_selector(None)
    events = [e async for e in run_preview(
        REQ, index=FixtureFrameIndex("synthetic_test"),
        stamps=FixtureStampSource("synthetic_test"), wavelength=StubWavelength(),
        renderer=StubRenderer(), cache=MemoryCache(), select=sel)]
    plan = next(e for e in events if isinstance(e, PlanEvent))
    assert (plan.n_visits_total, plan.n_visits_shown, plan.n_slots) == (2, 2, 4)
