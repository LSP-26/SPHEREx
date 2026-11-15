import math

import numpy as np
import pytest

from backend.pipeline.photometry import (
    JY_PER_MJY_ARCSEC2,
    aperture_photometry,
    circle_weights,
)

N = 49
AREA = 37.82
rng = np.random.default_rng(4)


def star(x, y, amp, sigma=0.6):
    yy, xx = np.mgrid[0:N, 0:N]
    return amp * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * sigma**2))


def stamp(level=0.0, noise=0.01, **stars):
    img = level + rng.normal(0, noise, (N, N)) if noise else np.full((N, N), float(level))
    var = np.full((N, N), max(noise, 0.02) ** 2)
    return img, var


def test_the_unit_constant_is_jy_per_mjy_per_sr_arcsec2():
    assert JY_PER_MJY_ARCSEC2 == pytest.approx(2.3504e-5, rel=1e-4)


def test_aperture_weights_add_up_to_the_circle_area_wherever_the_centre_is():
    for x, y in [(24.0, 24.0), (24.3, 23.8), (24.5, 24.5)]:
        assert circle_weights((N, N), x, y, 2.0).sum() == pytest.approx(math.pi * 4, rel=0.02)


def test_flux_is_recovered_through_a_background_offset_and_converted_to_jy():
    img, var = stamp(level=5.0)
    img = img + star(24.3, 23.8, 10.0)
    p = aperture_photometry(img, var, 24.3, 23.8, AREA)
    expected = 10.0 * 2 * math.pi * 0.6**2 * AREA * JY_PER_MJY_ARCSEC2
    assert p.ok and p.flux_jy == pytest.approx(expected, rel=0.03)
    assert p.background == pytest.approx(5.0, abs=0.01)
    assert p.flags == () and not p.lower_limit and p.reason is None


def test_error_is_variance_plus_the_background_term_and_matches_a_noise_free_stamp():
    img, var = stamp(level=1.0, noise=0.0)               # no scatter: sigma comes from VARIANCE
    img = img + star(24.0, 24.0, 10.0)
    p = aperture_photometry(img, var, 24.0, 24.0, AREA)
    w = circle_weights((N, N), 24.0, 24.0, 2.0)
    sigma = math.sqrt(var[0, 0])
    want = math.sqrt((w**2).sum() * var[0, 0] + (w.sum() * sigma / math.sqrt(p.n_annulus)) ** 2)
    assert p.err_jy == pytest.approx(want * AREA * JY_PER_MJY_ARCSEC2, rel=1e-6)


def test_the_error_matches_the_real_scatter_of_many_noisy_measurements():
    fluxes, errs = [], []
    for _ in range(300):
        img = rng.normal(0, 0.05, (N, N)) + star(24.0, 24.0, 5.0)
        p = aperture_photometry(img, np.full((N, N), 0.05**2), 24.0, 24.0, AREA)
        fluxes.append(p.flux_jy)
        errs.append(p.err_jy)
    assert np.std(fluxes) == pytest.approx(np.mean(errs), rel=0.2)


def test_a_per_pixel_area_patch_gives_the_same_answer_as_the_scalar():
    img, var = stamp(level=2.0)
    img = img + star(24.0, 24.0, 8.0)
    a = aperture_photometry(img, var, 24.0, 24.0, AREA)
    b = aperture_photometry(img, var, 24.0, 24.0, np.full((N, N), AREA))
    assert a.flux_jy == pytest.approx(b.flux_jy) and a.err_jy == pytest.approx(b.err_jy)


def test_a_saturated_core_gives_a_flagged_lower_limit_not_nothing():
    img, var = stamp()
    img = img + star(24.0, 24.0, 10.0)
    ref = aperture_photometry(img, var, 24.0, 24.0, AREA)      # before the core is blanked
    sat = np.zeros((N, N), bool)
    sat[23:26, 23:26] = True
    img[sat] = np.nan
    var[sat] = np.nan
    p = aperture_photometry(img, var, 24.0, 24.0, AREA, saturated=sat)
    assert p.ok and p.saturated and p.lower_limit and "saturated" in p.flags
    assert not ref.saturated and ref.ok
    assert 0 < p.flux_jy < ref.flux_jy                         # a lower limit, below the truth

def test_a_neighbour_in_the_annulus_is_flagged_crowded():
    img, var = stamp()
    img = img + star(24.0, 24.0, 100.0) + star(29.0, 24.0, 50.0, sigma=1.5)
    p = aperture_photometry(img, var, 24.0, 24.0, AREA)
    assert p.crowded and "crowded" in p.flags
    clean_img, clean_var = stamp()
    assert not aperture_photometry(clean_img + star(24.0, 24.0, 100.0), clean_var,
                                   24.0, 24.0, AREA).crowded


def test_a_few_masked_pixels_are_filled_in_and_labelled_partial():
    img, var = stamp(level=1.0, noise=0.0)
    img = img + star(24.0, 24.0, 10.0)
    ref = aperture_photometry(img, var, 24.0, 24.0, AREA)
    bad = np.zeros((N, N), bool)
    bad[24, 26] = True                                   # one outer pixel
    img2 = np.where(bad, np.nan, img)
    p = aperture_photometry(img2, var, 24.0, 24.0, AREA, good=~bad)
    assert p.ok and any(f.startswith("partial") for f in p.flags)
    assert p.flux_jy == pytest.approx(ref.flux_jy, rel=0.05)


def test_a_mostly_masked_aperture_is_refused():
    img, var = stamp()
    bad = np.zeros((N, N), bool)
    bad[20:29, 20:29] = True
    img[bad] = np.nan
    p = aperture_photometry(img, var, 24.0, 24.0, AREA, good=~bad)
    assert not p.ok and p.reason == "masked" and p.flux_jy is None


def test_edges_and_bad_input():
    img, var = stamp()
    assert aperture_photometry(img, var, 1.0, 24.0, AREA).reason == "outside"
    assert aperture_photometry(img, var, float("nan"), 24.0, AREA).reason == "outside"
    img[:, :] = np.nan
    img[22:27, 22:27] = 1.0
    assert aperture_photometry(img, var, 24.0, 24.0, AREA).reason == "annulus"
    with pytest.raises(ValueError):
        aperture_photometry(img, var[:5], 24.0, 24.0, AREA)
    with pytest.raises(ValueError):
        aperture_photometry(*stamp(), 24.0, 24.0, 0.0)


def test_a_masked_annulus_can_be_widened_when_asked():
    img, var = stamp(level=1.0)
    img = img + star(24.0, 24.0, 10.0)
    yy, xx = np.mgrid[0:N, 0:N]
    dist = np.hypot(xx - 24.0, yy - 24.0)
    good = ~((dist >= 4.0) & (dist <= 6.0))              # the annulus is masked
    sat = np.zeros((N, N), bool)
    sat[23:26, 23:26] = True
    plain = aperture_photometry(img, var, 24.0, 24.0, AREA, good=good, saturated=sat)
    wide = aperture_photometry(img, var, 24.0, 24.0, AREA, good=good, saturated=sat,
                               widen_background=True)
    assert not plain.ok and plain.reason == "annulus"
    assert wide.ok and wide.lower_limit and wide.flux_jy is not None
    assert sum("wider ring" in f for f in wide.flags) == 1
    assert wide.background == pytest.approx(1.0, abs=0.05)


def test_a_saturated_aperture_with_no_usable_pixel_is_refused_not_zero():
    img, var = stamp(level=1.0)
    yy, xx = np.mgrid[0:N, 0:N]
    good = np.hypot(xx - 24.0, yy - 24.0) > 7.0          # the whole aperture is masked
    sat = np.zeros((N, N), bool)
    sat[23:26, 23:26] = True
    p = aperture_photometry(img, var, 24.0, 24.0, AREA, good=good, saturated=sat,
                            widen_background=True)
    assert not p.ok and p.reason == "masked" and p.flux_jy is None and p.saturated



def test_large_aperture_sums_flagged_pixels_and_gives_a_lower_limit():
    from backend.pipeline.photometry import large_aperture_photometry
    img, var = stamp(level=1.0)
    model = star(24.0, 24.0, 50.0, sigma=2.0)
    img = img + model
    img[23:26, 23:26] = np.nan                           # saturated core: no number
    fin = np.isfinite(img)
    p = large_aperture_photometry(img, 24.0, 24.0, AREA, good=fin, saturated=True)
    w = circle_weights((N, N), 24.0, 24.0, 8.0)
    expected = float((w[fin] * model[fin]).sum()) * AREA * JY_PER_MJY_ARCSEC2
    assert p.ok and p.lower_limit and p.saturated and p.reason is None
    assert p.flux_jy == pytest.approx(expected, rel=0.03)
    assert "large aperture" in p.flags[0] and p.err_jy > 0


def test_large_aperture_is_refused_when_empty_or_without_sky():
    from backend.pipeline.photometry import large_aperture_photometry
    img, var = stamp(level=1.0)
    yy, xx = np.mgrid[0:N, 0:N]
    holes = img.copy()
    holes[np.hypot(xx - 24.0, yy - 24.0) <= 8.0] = np.nan
    assert large_aperture_photometry(holes, 24.0, 24.0, AREA,
                                     good=np.isfinite(holes)).reason == "masked"
    assert large_aperture_photometry(img, 24.0, 24.0, AREA,
                                     good=np.zeros((N, N), bool)).reason == "annulus"
    assert large_aperture_photometry(img, 3.0, 24.0, AREA,
                                     good=np.isfinite(img)).reason == "outside"
