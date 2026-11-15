import numpy as np

from backend.pipeline.centroid_track import fit_motion


def test_recovers_a_slow_motion():
    rng = np.random.default_rng(3)
    t = np.linspace(60800, 61150, 80)
    yr = (t - t.mean()) / 365.25
    east = 4.0 * yr + rng.normal(0, 0.8, t.size)
    north = -2.5 * yr + rng.normal(0, 0.8, t.size)
    fit = fit_motion(t, east, north)
    assert abs(fit.pm_east - 4.0) < 4 * fit.pm_east_err
    assert abs(fit.pm_north + 2.5) < 4 * fit.pm_north_err
    assert fit.significance > 3


def test_a_still_star_is_not_detected():
    rng = np.random.default_rng(4)
    t = np.linspace(60800, 61150, 60)
    fit = fit_motion(t, rng.normal(0, 1, 60), rng.normal(0, 1, 60))
    assert fit.significance < 4 and fit.speed < 4 * fit.pm_east_err


def test_outliers_are_clipped():
    t = np.linspace(60800, 61150, 40)
    east = np.zeros(40)
    east[5] = 50.0
    fit = fit_motion(t, east + np.random.default_rng(1).normal(0, 0.1, 40), np.zeros(40))
    assert fit.n_used < fit.n_total and abs(fit.pm_east) < 0.5


def test_too_few_points_or_one_time():
    assert fit_motion([1, 2, 3], [0, 0, 0], [0, 0, 0]) is None
    assert fit_motion([5.0] * 10, np.arange(10), np.arange(10)) is None
    assert fit_motion([1, 2, np.nan] + list(range(3, 10)), [0] * 10, [0] * 10) is not None
