import numpy as np
import pytest
from astropy.wcs import WCS

from backend.adapters import wcs_util
from backend.adapters.wcs_util import celestial_wcs, celestial_wcs_reference

PX = 6.15 / 3600
BASE = {"NAXIS": 2, "NAXIS1": 2040, "NAXIS2": 2040, "CTYPE1": "RA---TAN", "CTYPE2": "DEC--TAN",
        "CRPIX1": 1020.0, "CRPIX2": 1020.0, "CRVAL1": 276.26393, "CRVAL2": 64.82396,
        "CUNIT1": "deg", "CUNIT2": "deg", "RADESYS": "ICRS"}
CDELT = {**BASE, "CDELT1": -PX, "CDELT2": PX}
CD = {**BASE, "CD1_1": -PX * 0.99, "CD1_2": PX * 0.01, "CD2_1": PX * 0.01, "CD2_2": PX * 0.99}
SIP = {**CD, "CTYPE1": "RA---TAN-SIP", "CTYPE2": "DEC--TAN-SIP", "A_ORDER": 2, "B_ORDER": 2,
       "A_2_0": 2e-7, "A_1_1": -1e-7, "A_0_2": 3e-7, "B_2_0": -1e-7, "B_1_1": 2e-7,
       "B_0_2": 1e-7}
ALT = {**SIP, "CTYPE1A": "RA---TAN", "CTYPE2A": "DEC--TAN", "CRPIX1A": 1.0, "CRVAL1A": 1.0,
       "CTYPE1W": "WAVE", "CTYPE2W": "LINEAR", "CRPIX1W": 1.0, "CUNIT1W": "um"}

NO_CRPIX = {k: v for k, v in CDELT.items() if not k.startswith("CRPIX")}

PIXELS = np.array([[0.0, 0.0], [2039.0, 0.0], [0.0, 2039.0], [2039.0, 2039.0],
                   [1019.0, 1019.0], [300.0, 1700.0]])


@pytest.mark.parametrize("header", [CDELT, CD, SIP, ALT], ids=["cdelt", "cd", "sip", "alt-keys"])
def test_fast_wcs_matches_the_reference(header):
    before = wcs_util.STATS["fallbacks"]
    fast, ref = celestial_wcs(header), celestial_wcs_reference(header)
    assert wcs_util.STATS["fallbacks"] == before        # the fast path really was used
    assert np.allclose(fast.all_pix2world(PIXELS, 0), ref.all_pix2world(PIXELS, 0),
                       atol=1e-9, rtol=0)                # 1e-9 degrees is 4e-6 arcsec
    sky = ref.all_pix2world(PIXELS, 0)
    assert np.allclose(fast.all_world2pix(sky, 0), PIXELS, atol=1e-5)


def test_sip_terms_really_change_the_result():
    plain = celestial_wcs(CD).all_pix2world(PIXELS, 0)
    bent = celestial_wcs(SIP).all_pix2world(PIXELS, 0)
    assert np.abs(plain - bent).max() > 1e-6             # so the test above is not vacuous


def test_unusable_header_falls_back_to_the_reference():
    before = wcs_util.STATS["fallbacks"]
    assert isinstance(celestial_wcs(NO_CRPIX), WCS)
    assert wcs_util.STATS["fallbacks"] == before + 1
