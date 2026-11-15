"""Injection tests for the moving-object search (backend/pipeline/movers.py) on synthetic visits
shaped like real SPHEREx ones: bursts of 3 exposures 2.1 min apart, a detector pair per exposure,
bursts hours apart, stars whose brightness changes between frames (variable filter)."""

import math

import numpy as np
import pytest

from backend.pipeline.movers import GridFrame, group_times, render_max_snr, search

SIZE = 101
BURST_HOURS = [0.0, 1.5, 6.0, 12.0, 24.0]


def psf(x: float, y: float, amp: float, sigma: float = 0.9) -> np.ndarray:
    yy, xx = np.mgrid[0:SIZE, 0:SIZE]
    return amp * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * sigma**2))


def visit(
    seed=1, mover=None, flare=None, n_stars=25, cosmic=True, hours=BURST_HOURS, mover_dets=(1, 4)
):
    """mover: (x0, y0, vx px/h, vy px/h, amp), drawn in mover_dets only. flare: (x, y, amp,
    burst index)."""
    rng = np.random.default_rng(seed)
    stars = [
        (rng.uniform(5, SIZE - 5), rng.uniform(5, SIZE - 5), rng.uniform(5, 200))
        for _ in range(n_stars)
    ]
    frames = []
    for b, h0 in enumerate(hours):
        for k in range(3):
            h = h0 + k * 2.1 / 60
            for det in (1, 4):
                img = rng.normal(0, 1, (SIZE, SIZE))
                for x, y, a in stars:
                    img += psf(x, y, a * rng.uniform(0.8, 1.2))  # filter changes the flux
                if mover and det in mover_dets:
                    x0, y0, vx, vy, amp = mover
                    img += psf(x0 + vx * h, y0 + vy * h, amp)
                if flare and flare[3] == b:
                    img += psf(flare[0], flare[1], flare[2])
                if cosmic:
                    cy, cx = rng.integers(3, SIZE - 3, 2)
                    img[cy, cx] += 60
                frames.append(GridFrame(f"f{b}_{k}_{det}", det, 61000 + h / 24, img))
    return frames


def test_bursts_are_grouped_by_time():
    t = [0, 1 / 1440, 2 / 1440, 0.1, 0.1 + 1 / 1440, 5]
    assert group_times(t, 10 / 1440) == [0, 0, 0, 1, 1, 2]


def test_finds_an_injected_mover_and_its_rate():
    r = search(visit(mover=(20, 30, 3.0, 1.5, 6.0)))
    assert len(r.tracks) == 1, [(len(t.points), t.vx, t.vy) for t in r.tracks]
    t = r.tracks[0]
    assert len(t.points) == len(BURST_HOURS)
    assert t.vx == pytest.approx(3.0, abs=0.15) and t.vy == pytest.approx(1.5, abs=0.15)
    x, y = t.at(61000)
    assert math.hypot(x - 20, y - 30) < 0.6
    assert r.frames_skipped == 0 and r.notes == []


@pytest.mark.parametrize("seed", [2, 3, 4])
def test_noise_stars_and_cosmic_rays_give_no_track(seed):
    r = search(visit(seed=seed))
    assert r.tracks == []


def test_a_track_seen_by_one_detector_and_not_its_pair_is_dropped():
    # the Orion Nebula false tracks: D4 only, nothing in the D1 frames of the same moments
    r = search(visit(mover=(20, 30, 3.0, 1.5, 6.0), mover_dets=(4,)))
    assert r.tracks == []


def test_a_star_flaring_in_one_burst_is_not_a_mover():
    r = search(visit(flare=(50, 50, 30.0, 2)))
    assert r.tracks == []


def test_a_slow_mover_is_found_and_a_still_one_is_not():
    slow = search(visit(seed=5, mover=(30, 60, 0.4, 0.0, 6.0)))  # 9.6 px in a day
    assert len(slow.tracks) == 1 and slow.tracks[0].vx == pytest.approx(0.4, abs=0.05)
    still = search(visit(seed=5, mover=(30, 60, 0.0, 0.0, 6.0)))
    assert still.tracks == []


def test_recovery_falls_off_with_brightness():
    """Bright movers are always found; one at the noise level is not (no false track either)."""
    found = {
        amp: len(search(visit(seed=10 + i, mover=(15, 20, 2.5, 2.0, amp))).tracks)
        for i, amp in enumerate([1.0, 8.0])
    }
    assert found == {1.0: 0, 8.0: 1}


def test_too_few_bursts_says_so():
    r = search(visit(hours=[0.0, 3.0], mover=(20, 30, 3.0, 1.5, 6.0)))
    assert r.tracks == [] and "needs 3" in r.notes[0]


def test_masked_pixels_are_not_searched():
    frames = visit(mover=(20, 30, 3.0, 1.5, 6.0))
    for f in frames:
        f.image[:, :50] = np.nan  # the mover's whole path is in the masked half
    assert search(frames).tracks == []


def test_picture_shows_the_mover():
    r = search(visit(mover=(20, 30, 3.0, 1.5, 6.0)))
    img, covered = render_max_snr(r.max_snr)
    assert img.dtype == np.uint8 and covered.mean() > 0.9
    assert img[30, 20] > 2.5 * np.median(img)  # the mover (first burst) stands out


def test_a_spot_that_flashes_in_several_bursts_is_not_used_for_a_track():
    frames = visit(mover=(20, 30, 3.0, 1.5, 6.0))
    for f, b in zip(frames, [int(f.frame_id[1]) for f in frames]):
        if b in (0, 2, 4):  # flashes in three bursts, always at the same spot
            f.image += psf(70, 70, 8.0)
    r = search(frames)
    assert len(r.tracks) == 1 and r.tracks[0].vx == pytest.approx(3.0, abs=0.15)
    assert all(math.hypot(p.x - 70, p.y - 70) > 3 for t in r.tracks for p in t.points)
