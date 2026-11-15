import numpy as np
import pytest

from backend.pipeline.band_drivers import rank_correlations, verdict


def test_a_driver_that_tracks_the_amplitude_is_found_and_noise_is_not():
    rng = np.random.default_rng(0)
    n = 40
    t = np.arange(n, dtype=float)
    amp = 0.1 * t + rng.normal(0, 0.2, n)
    c = rank_correlations(amp, {"mjd": t, "moon_sep": rng.normal(size=n)})
    rho, p, k = c["mjd"]
    assert rho > 0.9 and p < 1e-6 and k == n
    assert abs(c["moon_sep"][0]) < 0.6


def test_nan_pairs_are_dropped_and_flat_or_tiny_samples_are_skipped():
    a = np.arange(20, dtype=float)
    x = a.copy()
    x[3] = np.nan
    a2 = a.copy()
    a2[5] = np.nan
    c = rank_correlations(a2, {"x": x, "flat": np.ones(20)})
    assert c["x"][2] == 18 and "flat" not in c
    assert rank_correlations(a[:5], {"x": a[:5]}) == {}
    with pytest.raises(ValueError):
        rank_correlations(a, {"x": a[:3]})


def corr(**kw):
    return {k: (r, p, 30) for k, (r, p) in kw.items()}


def test_time_only_is_a_drift():
    v = verdict(corr(mjd=(0.9, 1e-9), zodi_band=(0.1, 0.5)))
    assert "drift" in v and "cannot separate" not in v


def test_sky_only_is_scattered_light_that_depends_on_the_pointing():
    v = verdict(corr(mjd=(0.1, 0.5), zodi_band=(0.8, 1e-6)))
    assert "sky geometry" in v and "cannot separate" not in v


def test_time_and_sky_together_cannot_be_separated():
    assert "cannot separate" in verdict(corr(mjd=(0.9, 1e-9), zodi_band=(0.8, 1e-6)))


def test_bright_sources_come_first():
    assert "bright sources" in verdict(corr(mjd=(0.9, 1e-9), n_overflow=(0.7, 1e-5)))


def test_weak_or_insignificant_correlations_do_not_count():
    assert "none of the tested" in verdict(corr(mjd=(0.3, 1e-4), zodi_band=(0.9, 0.2)))
    assert "too few" in verdict({})
