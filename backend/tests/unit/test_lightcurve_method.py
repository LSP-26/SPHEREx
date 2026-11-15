import numpy as np

from backend.pipeline.lightcurve import lightcurve_point
from backend.tests.unit.test_lightcurve import AREA, DEC0, RA0, N, make_stamp


def test_a_normal_point_is_a_2px_aperture_point():
    p = lightcurve_point(make_stamp(stars=[(24.0, 24.0, 100.0)]), RA0, DEC0, 1.65, AREA)
    assert p.method == "aperture" and p.to_json()["method"] == "aperture"


def test_the_flagged_ring_fallback_is_labelled_large_aperture():
    yy, xx = np.mgrid[0:N, 0:N]
    flags = np.zeros((N, N), "uint32")
    flags[np.hypot(xx - 24.0, yy - 24.0) <= 7.0] = 1 << 2
    flags[23:26, 23:26] |= 1 << 1
    st = make_stamp(stars=[(24.0, 24.0, 100.0)], flags=flags)
    st.image[23:26, 23:26] = np.nan
    p = lightcurve_point(st, RA0, DEC0, 1.65, AREA)
    assert p.ok and p.method == "large_aperture"
