from backend.pipeline.select import make_selector
from contracts import FlipbookRequest, FrameRef

REQ = FlipbookRequest(ra=10.0, dec=0.0)   # preview_cap defaults to 36


def fr(i, t, det):
    return FrameRef(frame_id=f"f{i}", detector=det, t_start=t, t_end=t + 0.01,
                    release="QR2", version="v1", uri=f"s3://x/{i}")


def visits(n, dets_for, spacing=20.0):
    out, i = [], 0
    for v in range(n):
        for d in dets_for(v):
            out.append(fr(i, 100.0 + spacing * v + 0.01 * d, d))
            i += 1
    return out


def select(frames, req=REQ):
    return make_selector(None, gap_days=5)(frames, req)


def test_two_detectors_per_visit_fills_the_cap():
    sel = select(visits(30, lambda v: (1, 2)))
    assert sel.visits_total == 30 and sel.visits_shown == 18 and len(sel.chosen) == 36


def test_six_detectors_per_visit_still_gives_six_visits():
    sel = select(visits(10, lambda v: range(1, 7)))
    assert sel.visits_shown == 6 and len(sel.chosen) == 36


def test_mixed_visits_never_exceed_the_cap():
    sel = select(visits(20, lambda v: range(1, 7) if v % 2 == 0 else (1, 2)))
    assert len(sel.chosen) <= 36 and sel.visits_shown >= 6
    assert len(sel.chosen) > 18


def test_fewer_frames_than_the_cap_shows_everything():
    sel = select(visits(5, lambda v: (1, 2)))
    assert sel.visits_shown == 5 and len(sel.chosen) == 10


def test_chosen_frames_stay_in_time_order_and_span_the_whole_range():
    sel = select(visits(30, lambda v: (1, 2)))
    times = [f.t_mid for f, _ in sel.chosen]
    assert times == sorted(times) and times[0] < 101 and times[-1] > 100 + 20 * 28
