from astropy.wcs import WCS

from backend.adapters.wcs_util import _ALT, celestial_wcs

HEADER = {
    "NAXIS": 2, "NAXIS1": 2040, "NAXIS2": 2040,
    "CTYPE1": "RA---TAN", "CTYPE2": "DEC--TAN",
    "CRPIX1": 1020.0, "CRPIX2": 1020.0,
    "CRVAL1": 276.26393, "CRVAL2": 64.82396,
    "CDELT1": -6.15 / 3600, "CDELT2": 6.15 / 3600,
    "CUNIT1": "deg", "CUNIT2": "deg",
    # alternate WCS sets, as in real SPHEREx frames
    "CTYPE1A": "RA---TAN", "CTYPE2A": "DEC--TAN", "CRPIX1A": 1.0, "CRVAL1A": 1.0,
    "CTYPE1W": "WAVE", "CTYPE2W": "LINEAR", "CRPIX1W": 1.0, "CRVAL1W": 1.0, "CUNIT1W": "um",
}


def test_alternate_keys_are_dropped_and_celestial_wcs_works():
    wcs = celestial_wcs(HEADER)
    assert isinstance(wcs, WCS)
    assert list(wcs.wcs.ctype) == ["RA---TAN", "DEC--TAN"]
    ra, dec = wcs.all_pix2world([[1019.0, 1019.0]], 0)[0]  # CRPIX is 1-based
    assert abs(ra - 276.26393) < 1e-6 and abs(dec - 64.82396) < 1e-6


def test_empty_or_none_header_does_not_crash():
    assert isinstance(celestial_wcs(None), WCS)


def test_regex_keeps_primary_and_sip_keys():
    for keep in ("CTYPE1", "CRPIX2", "CRVAL1", "CD1_1", "PC1_1", "CDELT1", "A_0_1", "B_ORDER",
                 "A_ORDER", "AP_1_0", "RADESYS", "LONPOLE", "NAXIS1"):
        assert not _ALT.match(keep), keep
    for drop in ("CTYPE1A", "CTYPE1W", "CRPIX2W", "CUNIT1W", "CRVAL1A", "CD1_1A"):
        assert _ALT.match(drop), drop
