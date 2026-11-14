from backend.pipeline.select import make_selector
from contracts import FlipbookRequest, FrameRef


def fr(i, t, det):
    return FrameRef(frame_id=f"f{i}", detector=det, t_start=t, t_end=t + 0.01,
                    release="QR2", version="v1", uri=f"s3://x/{i}")


def test_pole_like_data_fills_the_cap_even_when_all_six_detectors_appear_somewhere():
    frames, i = [], 0
    for v in range(30):
        dets = (1, 4) if v % 3 else (2, 5) if v % 2 else (3, 6)
        for d in dets:
            frames.append(fr(i, 100.0 + 20.0 * v + 0.01 * d, d))
            i += 1
    sel = make_selector(None, gap_days=5)(frames, FlipbookRequest(ra=10.0, dec=0.0))
    assert sel.visits_total == 30
    assert all(d in {f.detector for f, _ in sel.chosen} for d in (1, 4))
    assert len(sel.chosen) >= 30          # the old rule gave 12 here
    assert len(sel.chosen) <= 36
