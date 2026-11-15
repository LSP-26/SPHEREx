import json
import math

import numpy as np
import pytest

from backend.pipeline.lightcurve import DICHROIC_NOTE, lightcurve_point
from backend.pipeline.photometry import JY_PER_MJY_ARCSEC2
from contracts import FrameRef, Stamp

PX = 6.15 / 3600
RA0, DEC0 = 10.0, 0.0
N = 49
HALF = N // 2
X0 = Y0 = 1019 - HALF          # the target is at detector pixel (1019, 1019), stamp (24, 24)
AREA = 37.82
SIGMA = 0.9
OVERFLOW, DICHROIC = 1 << 1, 1 << 7

WCS = {"NAXIS": 2, "NAXIS1": 2040, "NAXIS2": 2040, "CTYPE1": "RA---TAN", "CTYPE2": "DEC--TAN",
       "CRPIX1": 1020.0, "CRPIX2": 1020.0, "CRVAL1": RA0, "CRVAL2": DEC0,
       "CDELT1": -PX, "CDELT2": PX, "CUNIT1": "deg", "CUNIT2": "deg"}


def make_stamp(stars=(), flags=None, header=WCS, detector=3):
    rng = np.random.default_rng(11)
    yy, xx = np.mgrid[0:N, 0:N]
    img = 1.0 + rng.normal(0, 0.05, (N, N))
    for x, y, amp in stars:
        img = img + amp * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * SIGMA**2))
    fl = np.zeros((N, N), "uint32") if flags is None else flags
    frame = FrameRef(frame_id="f1", detector=detector, t_start=61000.0, t_end=61000.02,
                     release="QR3", version="v27", uri="s3://x/f1", wcs_header=header)
    return Stamp(frame=frame, x0=X0, y0=Y0, image=img.astype("float32"), flags=fl,
                 variance=np.full((N, N), 0.0025, "float32"), zodi=None,
                 wcs_header=dict(header) if header else {})


def test_flux_and_position_come_from_the_centroid_of_the_star():
    p = lightcurve_point(make_stamp(stars=[(24.4, 23.7, 100.0)]), RA0, DEC0, 1.65, AREA)
    assert p.ok and p.centroid and not p.lower_limit and p.reason is None
    assert (p.x, p.y) == pytest.approx((X0 + 24.4, Y0 + 23.7), abs=0.15)
    enclosed = 1 - math.exp(-4.0 / (2 * SIGMA**2))
    expected = 100.0 * 2 * math.pi * SIGMA**2 * enclosed * AREA * JY_PER_MJY_ARCSEC2
    assert p.flux_jy == pytest.approx(expected, rel=0.08)
    assert (p.detector, p.release, p.version, p.lambda_um) == (3, "QR3", "v27", 1.65)
    assert p.t_mjd == pytest.approx(61000.01)
    assert p.flags == ()


def test_a_saturated_core_uses_the_prediction_and_gives_a_lower_limit():
    flags = np.zeros((N, N), "uint32")
    flags[23:26, 23:26] = OVERFLOW
    p = lightcurve_point(make_stamp(stars=[(24.0, 24.0, 100.0)], flags=flags),
                         RA0, DEC0, 1.65, AREA)
    assert p.ok and p.lower_limit and not p.centroid and p.flux_jy > 0
    assert "saturated" in p.flags and any("no centroid" in f for f in p.flags)
    assert (p.x, p.y) == pytest.approx((X0 + 24.0, Y0 + 24.0), abs=1e-3)


def test_a_brighter_neighbour_does_not_pull_the_aperture_away_from_the_prediction():
    p = lightcurve_point(make_stamp(stars=[(24.0, 24.0, 100.0), (27.0, 24.0, 300.0)]),
                         RA0, DEC0, 1.65, AREA)
    assert not p.centroid and any("ignored" in f for f in p.flags)
    assert (p.x, p.y) == pytest.approx((X0 + 24.0, Y0 + 24.0), abs=1e-3)


def test_a_frame_without_a_wcs_is_a_refused_point_not_an_exception():
    p = lightcurve_point(make_stamp(stars=[(24.0, 24.0, 100.0)], header=None),
                         RA0, DEC0, 1.65, AREA)
    assert not p.ok and p.reason == "no_wcs" and p.x is None and p.flags == ("no_wcs",)


def test_a_position_off_the_stamp_is_refused_as_outside():
    p = lightcurve_point(make_stamp(stars=[(24.0, 24.0, 100.0)]), RA0 + 100 * PX, DEC0,
                         1.65, AREA)
    assert not p.ok and p.reason == "outside" and p.flux_jy is None


def test_a_target_in_the_dichroic_band_is_flagged():
    flags = np.zeros((N, N), "uint32")
    flags[20:29, :] = DICHROIC
    p = lightcurve_point(make_stamp(stars=[(24.0, 24.0, 100.0)], flags=flags),
                         RA0, DEC0, 4.4, AREA)
    assert not p.ok and DICHROIC_NOTE in p.flags


def test_the_json_form_is_plain_and_keeps_missing_values_as_null():
    good = lightcurve_point(make_stamp(stars=[(24.0, 24.0, 100.0)]), RA0, DEC0, 1.65, AREA)
    bad = lightcurve_point(make_stamp(), RA0 + 100 * PX, DEC0, float("nan"), AREA)
    g = json.loads(json.dumps(good.to_json()))
    b = json.loads(json.dumps(bad.to_json()))
    assert g["flux_jy"] > 0 and g["flags"] == [] and g["centroid"] is True
    assert b["flux_jy"] is None and b["lambda_um"] is None and b["reason"] == "outside"



def test_a_flagged_ring_falls_back_to_a_large_aperture_lower_limit():
    yy, xx = np.mgrid[0:N, 0:N]
    dist = np.hypot(xx - 24.0, yy - 24.0)
    flags = np.zeros((N, N), "uint32")
    flags[dist <= 7.0] = 1 << 2                          # SUR_ERROR ring: fatal, but has values
    flags[23:26, 23:26] |= OVERFLOW
    st = make_stamp(stars=[(24.0, 24.0, 100.0)], flags=flags)
    st.image[23:26, 23:26] = np.nan                      # the core has no number
    p = lightcurve_point(st, RA0, DEC0, 1.65, AREA)
    assert p.ok and p.lower_limit and not p.centroid and p.flux_jy > 0
    assert any("large aperture" in f for f in p.flags)
