"""WCS helpers.

celestial_wcs builds the RA/Dec + SIP WCS of a SPHEREx frame from the catalogue's header dict.
The fast path sets the astropy objects directly. Going through a fits.Header and WCS(header)
cost about 11 ms per frame (measured on R Draconis, 2026-10-31: 98 % of a catalogue lookup,
27 s for 2,482 frames). celestial_wcs_reference is the slow, obviously correct version, kept
as the yardstick for the tests and scripts/check_fast_wcs.py.

SPHEREx headers carry SIP coefficients (A_ORDER, ...) but a plain TAN CTYPE. astropy applies the
SIP terms anyway and logs a long INFO message about the missing "-SIP" suffix every time such a
WCS is copied or sliced (once per frame). The fast path therefore adds the suffix itself when SIP
terms are present. The numbers do not change, only the noise.
"""
import re

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS, Sip

_ALT = re.compile(
    r"^(CTYPE|CRVAL|CRPIX|CUNIT|CDELT|CD\d_|PC\d_|PV\d_|PS\d_|WCSNAME|CRDER|CSYER)\d*[A-Z]$")

STATS = {"fallbacks": 0}   # how often the fast path failed and the slow one was used


def celestial_wcs_reference(wcs_header):
    """RA/Dec + SIP WCS only, via astropy's own header parser. Drops alternate WCS sets
    (suffix A and W), which make wcslib fail with a fake MemoryError. Slow (about 11 ms)."""
    hdr = fits.Header()
    for k, v in (wcs_header or {}).items():
        if not _ALT.match(k):
            hdr[k] = v
    return WCS(hdr, naxis=2)


def _sip_matrix(h, name):
    order = int(h.get(f"{name}_ORDER", 0))
    if order < 1:
        return None
    m = np.zeros((order + 1, order + 1))
    for i in range(order + 1):
        for j in range(order + 1 - i):
            v = h.get(f"{name}_{i}_{j}")
            if v is not None:
                m[i, j] = float(v)
    return m


def _fast(h):
    w = WCS(naxis=2)
    ctype = [str(h["CTYPE1"]), str(h["CTYPE2"])]
    has_sip = int(h.get("A_ORDER", 0)) > 1     # astropy ignores SIP of order 1 or less, so do we
    if has_sip:
        ctype = [c if c.endswith("-SIP") else c + "-SIP" for c in ctype]
    w.wcs.ctype = ctype
    w.wcs.crval = [float(h["CRVAL1"]), float(h["CRVAL2"])]
    w.wcs.crpix = [float(h["CRPIX1"]), float(h["CRPIX2"])]
    if "CD1_1" in h or "CD2_2" in h:
        w.wcs.cd = [[float(h.get("CD1_1", 0.0)), float(h.get("CD1_2", 0.0))],
                    [float(h.get("CD2_1", 0.0)), float(h.get("CD2_2", 0.0))]]
    else:
        w.wcs.cdelt = [float(h.get("CDELT1", 1.0)), float(h.get("CDELT2", 1.0))]
        if any(k in h for k in ("PC1_1", "PC1_2", "PC2_1", "PC2_2")):
            w.wcs.pc = [[float(h.get("PC1_1", 1.0)), float(h.get("PC1_2", 0.0))],
                        [float(h.get("PC2_1", 0.0)), float(h.get("PC2_2", 1.0))]]
    for key, attr in (("LONPOLE", "lonpole"), ("LATPOLE", "latpole"), ("EQUINOX", "equinox")):
        if key in h:
            setattr(w.wcs, attr, float(h[key]))
    if "RADESYS" in h:
        w.wcs.radesys = str(h["RADESYS"])
    if has_sip:
        w.sip = Sip(_sip_matrix(h, "A"), _sip_matrix(h, "B"),
                    _sip_matrix(h, "AP"), _sip_matrix(h, "BP"), np.array(w.wcs.crpix))
    w.wcs.set()
    return w


def celestial_wcs(wcs_header):
    """Fast path first. Any surprise (missing key, odd header) falls back to the reference."""
    try:
        return _fast(wcs_header or {})
    except Exception:
        STATS["fallbacks"] += 1
        return celestial_wcs_reference(wcs_header)
