import math

import numpy as np
import pytest

from backend.pipeline.coadd import channels_covered, coadd
from backend.pipeline.motion import GAIA_EPOCH_MJD, propagate, total_motion_px

rng = np.random.default_rng(2)


def test_inverse_variance_weighting_and_noise():
    a, b = np.full((4, 4), 10.0), np.full((4, 4), 20.0)
    c = coadd([a, b], [np.full((4, 4), 1.0), np.full((4, 4), 4.0)])   # weights 1 and 0.25
    assert c.image[0, 0] == pytest.approx((10 + 0.25 * 20) / 1.25)
    assert c.noise[0, 0] == pytest.approx(1 / math.sqrt(1.25))
    assert (c.n_used == 2).all() and c.n_frames == 2


def test_masked_pixels_are_skipped_and_all_masked_is_nan():
    a, b = np.ones((3, 3)), np.full((3, 3), 5.0)
    a[1, 1] = np.nan
    b[1, 1] = np.nan
    b[0, 0] = np.nan
    c = coadd([a, b], [np.ones((3, 3))] * 2)
    assert c.image[0, 0] == 1.0 and c.n_used[0, 0] == 1
    assert np.isnan(c.image[1, 1]) and np.isnan(c.noise[1, 1]) and c.n_used[1, 1] == 0


def test_noise_falls_as_root_n():
    frames = [rng.normal(0, 1, (60, 60)) for _ in range(16)]
    c = coadd(frames, [np.ones((60, 60))] * 16)
    assert c.image.std() == pytest.approx(0.25, rel=0.15)
    assert c.noise[0, 0] == pytest.approx(0.25)


def test_cosmic_ray_is_clipped_with_five_frames_but_not_with_two():
    frames = [np.zeros((5, 5)) + rng.normal(0, 0.1, (5, 5)) for _ in range(5)]
    frames[2][2, 2] = 100.0
    v = [np.full((5, 5), 0.01)] * 5
    assert abs(coadd(frames, v).image[2, 2]) < 0.3
    assert coadd(frames[1:4], v[1:4]).image[2, 2] > 10          # too few frames to clip


def test_a_real_star_survives_clipping():
    frames = [np.zeros((5, 5)) + rng.normal(0, 0.1, (5, 5)) for _ in range(6)]
    for f in frames:
        f[2, 2] += 50.0
    assert coadd(frames, [np.full((5, 5), 0.01)] * 6).image[2, 2] == pytest.approx(50, abs=1)


def test_bad_input_is_refused():
    with pytest.raises(ValueError):
        coadd([], [])
    with pytest.raises(ValueError):
        coadd([np.ones((2, 2))], [np.ones((3, 3))])


def test_channels_covered_badges_partial_coadds():
    assert channels_covered(range(1, 18)) == (17, False)
    assert channels_covered([1, 1, 2, None]) == (2, True)


def test_propagation_directions_and_size():
    ra, dec = propagate(100.0, 0.0, 1000.0, 2000.0, GAIA_EPOCH_MJD, GAIA_EPOCH_MJD + 365.25)
    assert dec == pytest.approx(2.0 / 3600) and ra == pytest.approx(100.0 + 1.0 / 3600)
    assert propagate(10, 5, 5, 5, 60000, 60000) == (10.0, 5)


def test_ra_motion_is_stretched_by_cos_dec_and_wraps():
    ra, _ = propagate(0.0, 60.0, -1000.0, 0.0, GAIA_EPOCH_MJD, GAIA_EPOCH_MJD + 365.25)
    assert ra == pytest.approx(360.0 - 2.0 / 3600)           # 1" on sky = 2" of RA at 60 deg


def test_barnard_like_motion_is_about_two_pixels_in_two_months_of_survey_scale():
    # 10.3 arcsec/yr over 1.4 years, in 6.15" pixels (plan: roughly 2 px per 0.2 yr)
    assert total_motion_px(0, 10300, 0, 365.25 * 1.4) == pytest.approx(2.34, abs=0.05)


def test_pole_is_refused():
    with pytest.raises(ValueError):
        propagate(0, 90, 1, 1, 0, 1)
