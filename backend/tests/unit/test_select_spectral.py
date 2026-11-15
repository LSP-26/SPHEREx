from backend.pipeline.select import make_spectral_selector
from contracts import FlipbookRequest, FrameRef


def fr(i, t):
    return FrameRef(frame_id=f"f{i}", detector=1, t_start=t, t_end=t + 0.01,
                    release="QR2", version="v1", uri=f"s3://x/{i}")


def test_frames_from_all_visits_are_sorted_by_wavelength_and_spread_evenly():
    frames = [fr(i, 100.0 + 20.0 * (i % 5) + 0.01 * i) for i in range(100)]
    lam = {f.frame_id: 5.0 - 0.04 * int(f.frame_id[1:]) for f in frames}   # decreasing with id
    req = FlipbookRequest(ra=10.0, dec=0.0, preview_cap=10)
    sel = make_spectral_selector(lambda f: lam[f.frame_id])(frames, req)
    got = [lam[f.frame_id] for f, _ in sel.chosen]
    assert len(got) == 10 and got == sorted(got)
    assert got[0] == min(lam.values()) and got[-1] == max(lam.values())
    assert sel.visits_total == 5


def test_frames_without_a_wavelength_are_left_out():
    frames = [fr(i, 100.0 + i) for i in range(6)]
    req = FlipbookRequest(ra=10.0, dec=0.0)
    def lam(f):
        return None if f.frame_id in ("f0", "f1") else 1.0 + int(f.frame_id[1:])
    sel = make_spectral_selector(lam)(frames, req)
    assert [f.frame_id for f, _ in sel.chosen] == ["f2", "f3", "f4", "f5"]
