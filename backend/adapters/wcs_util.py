import re

from astropy.io import fits
from astropy.wcs import WCS

_ALT = re.compile(
    r"^(CTYPE|CRVAL|CRPIX|CUNIT|CDELT|CD\d_|PC\d_|PV\d_|PS\d_|WCSNAME|CRDER|CSYER)\d*[A-Z]$")


def celestial_wcs(wcs_header):
    """RA/Dec + SIP WCS only. Drops alternate WCS sets (suffix A and W), which make
    wcslib fail with a fake MemoryError."""
    hdr = fits.Header()
    for k, v in (wcs_header or {}).items():
        if not _ALT.match(k):
            hdr[k] = v
    return WCS(hdr, naxis=2)
