import numpy as np

from backend.adapters import wcs_util
from backend.adapters.wcs_util import celestial_wcs, celestial_wcs_reference
from backend.tests.unit.test_wcs_util_fast import CD, PIXELS, SIP

# what real SPHEREx headers look like: SIP coefficients, but a plain TAN CTYPE
PLAIN_CTYPE = {**SIP, "CTYPE1": "RA---TAN", "CTYPE2": "DEC--TAN"}


def test_sip_terms_get_the_suffix_and_the_numbers_do_not_change():
    before = wcs_util.STATS["fallbacks"]
    fast, ref = celestial_wcs(PLAIN_CTYPE), celestial_wcs_reference(PLAIN_CTYPE)
    assert wcs_util.STATS["fallbacks"] == before
    assert list(fast.wcs.ctype) == ["RA---TAN-SIP", "DEC--TAN-SIP"]
    assert np.allclose(fast.all_pix2world(PIXELS, 0), ref.all_pix2world(PIXELS, 0),
                       atol=1e-9, rtol=0)


def test_no_sip_means_no_suffix():
    assert list(celestial_wcs(CD).wcs.ctype) == ["RA---TAN", "DEC--TAN"]
