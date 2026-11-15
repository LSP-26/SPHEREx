import math

import pytest

from backend.pipeline.targets import SURVEY_MID_MJD, TARGETS, position_at, year_to_mjd


def arcsec(a, b):
    dra = (a[0] - b[0]) * math.cos(math.radians(b[1])) * 3600
    return math.hypot(dra, (a[1] - b[1]) * 3600)


def test_barnard_has_moved_about_270_arcsec_north_by_2026():
    t = TARGETS["barnard"]
    ra, dec = position_at("barnard")
    assert (dec - t.dec) * 3600 == pytest.approx(272, abs=3)
    assert (ra - t.ra) * 3600 == pytest.approx(-21, abs=2)


def test_wise0855_matches_the_hst_2016_position_within_a_pixel():
    # HST proposal 14157: RA 133.7826204 Dec -7.24410 at epoch 2016 (about 2016.2)
    p = position_at("wise0855", year_to_mjd(2016.2))
    assert arcsec(p, (133.7826204, -7.24410)) < 6.15


def test_wise0855_has_moved_about_20_pixels_since_the_brief():
    t = TARGETS["wise0855"]
    assert arcsec(position_at("wise0855"), (t.ra, t.dec)) / 6.15 == pytest.approx(20.6, abs=1.5)


def test_motion_over_the_survey_matches_the_plans_pixel_estimates():
    def px(name, t0, t1):
        return arcsec(position_at(name, t1), position_at(name, t0)) / 6.15
    assert px("barnard", 60902.9, 61305.6) == pytest.approx(1.9, abs=0.2)
    assert px("wise0855", 60800.7, 61174.3) == pytest.approx(1.4, abs=0.2)
    assert px("eps_indi_b", 60789.9, 61315.5) == pytest.approx(1.1, abs=0.2)


def test_target_at_its_own_epoch_does_not_move():
    for name, t in TARGETS.items():
        assert position_at(name, t.epoch_mjd) == pytest.approx((t.ra, t.dec))
    assert SURVEY_MID_MJD > 60790


def test_eps_indi_b_is_about_400_arcsec_from_the_k_star_and_moves_with_it():
    a, b = TARGETS["eps_indi_a"], TARGETS["eps_indi_b"]
    sep0 = arcsec((a.ra, a.dec), (b.ra, b.dec))
    assert 395 < sep0 < 410
    assert abs(arcsec(position_at("eps_indi_a"), position_at("eps_indi_b")) - sep0) < 3


def test_eps_indi_b_position_for_2026_matches_the_frontend_quick_fill():
    # frontend/src/lib/targets.ts holds these numbers: keep them in step
    ra, dec = position_at("eps_indi_b")
    assert ra == pytest.approx(331.0970, abs=5e-4) and dec == pytest.approx(-56.8008, abs=2e-4)
    ra, dec = position_at("rw_aur")
    assert ra == pytest.approx(76.9566, abs=5e-5) and dec == pytest.approx(30.4013, abs=5e-5)
    ra, dec = position_at("r_dra")
    assert ra == pytest.approx(248.1672, abs=5e-5) and dec == pytest.approx(66.7550, abs=5e-5)
