import numpy as np
import pytest

from backend.pipeline.clean import Cleaned
from backend.pipeline.difference import difference, render_difference


def frame(img, sat=None):
    img = np.asarray(img, "float32")
    n = img.shape
    return Cleaned(image=img, variance=np.ones(n, "float32"), good=np.isfinite(img),
                   background=np.zeros(n, "float32"), noise=1.0, zodi_subtracted=True,
                   frac_masked=0.0, frac_saturated=0.0,
                   saturated=np.zeros(n, bool) if sat is None else sat)


def test_difference_is_b_minus_a_and_unknown_where_either_is_missing():
    rng = np.random.default_rng(1)
    a = rng.normal(0, 1, (40, 40))
    b = a.copy()
    b[10, 10] += 50            # something appears in B
    a[20, 20] += 50            # something is in A only
    a[5, 5] = np.nan           # no measurement in A
    d, noise = difference(frame(a), frame(b))
    assert d[10, 10] == pytest.approx(50, abs=1e-4)
    assert d[20, 20] == pytest.approx(-50, abs=1e-4)
    assert np.isnan(d[5, 5])
    assert noise == 0.0        # everything else is identical


def test_saturated_pixels_are_unknown():
    sat = np.zeros((10, 10), bool)
    sat[3, 3] = True
    d, _ = difference(frame(np.ones((10, 10)), sat), frame(np.ones((10, 10)) * 2))
    assert np.isnan(d[3, 3]) and d[0, 0] == pytest.approx(1.0)


def test_different_grids_are_refused():
    with pytest.raises(ValueError):
        difference(frame(np.zeros((5, 5))), frame(np.zeros((6, 6))))


def test_picture_is_grey_for_no_change_light_for_b_and_dark_for_a_and_clear_for_unknown():
    d = np.zeros((20, 20), "float32")
    d[2, 2], d[3, 3], d[4, 4] = 100.0, -100.0, np.nan
    rng = np.random.default_rng(2)
    d += rng.normal(0, 1, d.shape).astype("float32")
    gray, covered, white = render_difference(d, 1.0)
    assert abs(int(gray[10, 10]) - 128) < 20
    assert gray[2, 2] > 240 and gray[3, 3] < 15
    assert not covered[4, 4] and covered[0, 0]
    assert white >= 10.0
