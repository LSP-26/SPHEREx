from backend.pipeline.select import cluster_visits, make_selector
from contracts import FlipbookRequest, FrameRef


def fr(i, t, det=1):
    return FrameRef(frame_id=f"f{i}", detector=det, t_start=t, t_end=t + 0.01,
                    release="QR2", version="v1", uri=f"s3://x/{i}")


def continuous(n=1000, step=0.2, start=100.0):
    """Pole-like coverage: a frame every few hours with no gap structure at all."""
    return [fr(i, start + step * i) for i in range(n)]


def test_continuous_coverage_is_cut_into_two_week_windows():
    frames = continuous()
    visits = cluster_visits(frames)
    assert 14 <= len(visits) <= 16
    assert all(v.t_end - v.t_start <= 14.1 for v in visits)
    assert sum(len(v.frame_ids) for v in visits) == len(frames)
    assert [v.visit_index for v in visits] == list(range(len(visits)))
    assert all(a.t_end < b.t_start for a, b in zip(visits, visits[1:], strict=False))


def test_explicit_gap_is_used_exactly_as_given():
    assert len(cluster_visits(continuous(), gap_days=1000)) == 1
    assert len(cluster_visits(continuous(), gap_days=1.0)) == 1   # no gap above 1 d here


def test_normal_visits_are_left_alone():
    frames, i = [], 0
    for start in (100.0, 280.0, 460.0):
        for k in range(6):
            frames.append(fr(i, start + 0.5 * k))
            i += 1
    assert [len(v.frame_ids) for v in cluster_visits(frames)] == [6, 6, 6]


def test_selector_shows_many_visits_for_continuous_coverage():
    req = FlipbookRequest(ra=10.0, dec=0.0, preview_cap=6)
    sel = make_selector(None)(continuous(), req)
    assert sel.visits_total >= 14
    assert sel.visits_shown == 6 and len(sel.chosen) == 6
    times = [f.t_mid for f, _ in sel.chosen]
    assert times == sorted(times) and times[-1] - times[0] > 150   # spread over the whole span
