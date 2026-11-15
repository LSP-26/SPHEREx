import numpy as np
import pytest

from backend.pipeline.colour import channel, merge_colour

N = 20
rng = np.random.default_rng(0)


def det(level=0.0, star=None):
    img = rng.normal(level, 0.05, (N, N))
    if star:
        img[10, 10] += star
    return img, np.full((N, N), 0.0025)


def test_inverse_variance_mean_and_nan_where_nothing_is_usable():
    a = (np.full((3, 3), 10.0), np.full((3, 3), 1.0))
    b = (np.full((3, 3), 20.0), np.full((3, 3), 4.0))
    assert channel([a, b])[0, 0] == pytest.approx((10 + 20 / 4) / 1.25)
    bad = (np.full((3, 3), np.nan), np.ones((3, 3)))
    assert np.isnan(channel([bad])).all() and channel([]) is None


def test_a_red_star_is_red_and_a_channel_without_detectors_is_reported():
    c = merge_colour({5: det(star=50.0), 6: det(star=50.0), 3: det(), 1: det()})
    r, g, b = (int(x) for x in c.rgb[10, 10])
    assert r > 200 and g < 80 and b < 80 and c.missing == ()
    c2 = merge_colour({5: det(), 1: det()})
    assert c2.missing == ("G",) and (c2.rgb[..., 1] == 0).all()


def test_flat_sky_is_dark_and_nothing_given_is_an_error():
    c = merge_colour({d: det() for d in range(1, 7)})
    assert np.median(c.rgb) < 60 and c.covered.all()
    with pytest.raises(ValueError):
        merge_colour({})
