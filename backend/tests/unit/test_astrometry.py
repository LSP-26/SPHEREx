import numpy as np
import pytest

from backend.pipeline.astrometry import find_centroid, isolated_candidates, offset_stats


def star_image(shape, x, y, amp, sigma=1.2, noise=0.05, seed=1):
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:shape[0], 0:shape[1]]
    return amp * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * sigma**2)) \
        + rng.normal(0, noise, shape)


def test_the_centroid_is_found_to_a_few_hundredths_of_a_pixel():
    img = star_image((40, 40), 20.3, 15.7, 100.0)
    for guess in [(21.0, 15.0), (23.0, 18.0), (20.3, 15.7)]:    # prediction off by up to 3 px
        c = find_centroid(img, *guess, noise=0.05)
        assert c is not None
        assert (c.x, c.y) == pytest.approx((20.3, 15.7), abs=0.05)
        assert c.snr > 1000


def test_a_faint_star_is_refused():
    img = star_image((40, 40), 20.0, 20.0, 3.0, noise=0.5)
    assert find_centroid(img, 20.0, 20.0, noise=0.5) is None            # S/N about 6 to 8
    assert find_centroid(img, 20.0, 20.0, noise=0.5, min_snr=3.0) is not None


def test_a_masked_pixel_in_the_window_means_no_centroid():
    img = star_image((40, 40), 20.0, 20.0, 100.0)
    img[22, 21] = np.nan
    assert find_centroid(img, 20.0, 20.0, noise=0.05) is None


def test_a_brighter_neighbour_at_the_edge_of_the_window_means_no_centroid():
    img = star_image((40, 40), 20.0, 20.0, 50.0) + star_image((40, 40), 24.0, 20.0, 200.0, seed=2)
    assert find_centroid(img, 20.0, 20.0, noise=0.05) is None


def test_a_window_that_leaves_the_array_means_no_centroid():
    img = star_image((40, 40), 2.0, 20.0, 100.0)
    assert find_centroid(img, 2.0, 20.0, noise=0.05) is None


@pytest.mark.parametrize("noise", [0.0, -1.0, float("nan")])
def test_a_bad_noise_means_no_centroid(noise):
    assert find_centroid(star_image((40, 40), 20.0, 20.0, 100.0), 20.0, 20.0, noise) is None


def test_offset_stats():
    s = offset_stats([(0.1, 0.0), (0.0, 0.2), (0.3, 0.4)])
    assert s["n"] == 3 and s["median_dx"] == pytest.approx(0.1)
    assert s["median_dy"] == pytest.approx(0.2) and s["median_r"] == pytest.approx(0.2)
    assert s["p90_r"] == pytest.approx(0.44) and s["max_r"] == pytest.approx(0.5)
    assert offset_stats([]) is None


def src(i, g, dec_arcsec):
    return {"source_id": i, "ra": 100.0, "dec": 10.0 + dec_arcsec / 3600.0, "g_mag": g}


def test_only_bright_enough_lonely_stars_are_candidates():
    sources = [src(1, 12.0, 0), src(2, 12.5, 20),        # a close pair: both rejected
               src(3, 12.0, 600), src(4, 17.0, 610),     # a faint neighbour does not matter
               src(5, None, 603),                        # no magnitude: ignored as a neighbour
               src(6, 8.0, 3000), src(7, 15.0, 5000)]    # outside the magnitude range
    assert [s["source_id"] for s in isolated_candidates(sources, 10.0, 14.0)] == [3]
    assert [s["source_id"] for s in
            isolated_candidates(sources, 10.0, 14.0, radius_arcsec=10.0)] == [1, 2, 3]
