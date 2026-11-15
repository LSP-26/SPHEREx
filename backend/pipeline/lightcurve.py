"""T8b1: one light-curve point from one frame (plan A10). Pure: no network, no disk.

lightcurve_point() takes a stamp (contracts.Stamp, duck-typed), the sky position where the target
should be AT THIS FRAME'S TIME (the caller propagates proper motion, for example with
backend.pipeline.targets.position_at), the wavelength at the target's pixel and the pixel-area
patch from SolidAngleMap.region (arcsec^2, same shape as the stamp, or a scalar).

STEPS
1. Clean the stamp (clean.py, no hole filling: photometry does not use the picture).
2. Put the sky position on the detector with the frame's own WCS (SIP included).
3. Try find_centroid near that pixel. The centroid is used only if it is within max_shift pixels
   of the prediction (a brighter neighbour inside the search window would otherwise win). With no
   usable centroid (a saturated core has no numbers) the predicted position is used and the
   point says so.
4. aperture_photometry at that position, with the cleaned `good` and `saturated` masks.

A point that could not be measured is still returned, with flux_jy None and a `reason`
("no_wcs", "no_position", "outside", "annulus", "masked"), so a builder never has to catch.
A saturated source gives a LOWER LIMIT (lower_limit True), see photometry.py.
x and y are DETECTOR pixels (0-based), not stamp pixels.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from backend.adapters.wcs_util import celestial_wcs
from backend.pipeline.astrometry import find_centroid
from backend.pipeline.clean import DICHROIC_BIT, band_note, clean_stamp
from backend.pipeline.photometry import aperture_photometry, large_aperture_photometry

MAX_CENTROID_SHIFT = 1.5     # pixels between the centroid and the prediction
MIN_CENTROID_SNR = 15.0
SATURATED_RADIUS = 3.0       # pixels round the target searched for OVERFLOW
DICHROIC_RADIUS = 3.0        # pixels round the target searched for the DICHROIC flag
DICHROIC_NOTE = "dichroic band, less reliable"


def _num(v) -> float | None:
    if v is None:
        return None
    x = float(v)
    return x if math.isfinite(x) else None


@dataclass(frozen=True)
class LightCurvePoint:
    frame_id: str
    detector: int
    release: str
    version: str
    t_mjd: float
    lambda_um: float
    x: float | None              # detector pixel the aperture was centred on
    y: float | None
    centroid: bool               # True: centred on a measured centroid, False: on the prediction
    flux_jy: float | None
    err_jy: float | None
    lower_limit: bool            # saturated: flux_jy is a lower limit
    reason: str | None           # why flux_jy is None
    flags: tuple[str, ...]
    method: str = "aperture"     # "aperture" (2 px) or "large_aperture" (8 px fallback)

    @property
    def ok(self) -> bool:
        return self.flux_jy is not None

    def to_json(self) -> dict:
        """Plain numbers, strings and lists only (NaN and infinity become null)."""
        return {"frame_id": self.frame_id, "detector": self.detector, "release": self.release,
                "version": self.version, "t_mjd": _num(self.t_mjd),
                "lambda_um": _num(self.lambda_um), "x": _num(self.x), "y": _num(self.y),
                "centroid": self.centroid, "flux_jy": _num(self.flux_jy),
                "err_jy": _num(self.err_jy), "lower_limit": self.lower_limit,
                "reason": self.reason, "flags": list(self.flags),
                "method": self.method}


def lightcurve_point(stamp, ra: float, dec: float, lambda_um: float, area_arcsec2, *,
                     use_zodi: bool = True, max_shift: float = MAX_CENTROID_SHIFT,
                     min_snr: float = MIN_CENTROID_SNR) -> LightCurvePoint:
    frame = stamp.frame
    base = dict(frame_id=frame.frame_id, detector=frame.detector, release=frame.release,
                version=frame.version, t_mjd=float(frame.t_mid), lambda_um=float(lambda_um))

    def refused(reason: str) -> LightCurvePoint:
        return LightCurvePoint(**base, x=None, y=None, centroid=False, flux_jy=None,
                               err_jy=None, lower_limit=False, reason=reason, flags=(reason,))

    header = getattr(stamp, "wcs_header", None) or getattr(frame, "wcs_header", None)
    if not header:
        return refused("no_wcs")
    try:
        px, py = celestial_wcs(header).all_world2pix([[ra % 360.0, dec]], 0, quiet=True)[0]
    except Exception:
        return refused("no_position")
    if not (math.isfinite(px) and math.isfinite(py)):
        return refused("no_position")
    px, py = float(px), float(py)                     # detector pixels, 0-based
    x_pred, y_pred = px - stamp.x0, py - stamp.y0     # stamp pixels

    cleaned = clean_stamp(stamp, use_zodi=use_zodi, fill=False)
    notes: list[str] = []
    x, y, used = x_pred, y_pred, False
    c = find_centroid(cleaned.image, x_pred, y_pred, cleaned.noise, min_snr=min_snr)
    if c is None:
        notes.append("no centroid, used the predicted position")
    else:
        shift = math.hypot(c.x - x_pred, c.y - y_pred)
        if shift <= max_shift:
            x, y, used = c.x, c.y, True
        else:
            notes.append(f"centroid {shift:.1f} px from the prediction, ignored")

    phot = aperture_photometry(cleaned.image, cleaned.variance, x, y, area_arcsec2,
                               good=cleaned.good, saturated=cleaned.saturated,
                               widen_background=True)
    yy, xx = np.indices(cleaned.image.shape)
    dist = np.hypot(xx - x, yy - y)
    if (not phot.ok and phot.reason in ("masked", "annulus")
            and bool(cleaned.saturated[dist <= SATURATED_RADIUS].any())):
        # bright star: the ring round the saturated core is flagged, so the 2 px aperture
        # is empty. Fall back to a large-aperture lower limit.
        big = large_aperture_photometry(cleaned.display, x, y, area_arcsec2,
                                        good=cleaned.good, saturated=True)
        if big.ok:
            phot = big
    flags = list(phot.flags) + notes

    near = dist <= DICHROIC_RADIUS
    raw = np.asarray(stamp.flags).astype("int64") & 0xFFFFFFFF
    if near.any() and bool(((raw >> DICHROIC_BIT) & 1).astype(bool)[near].any()):
        flags.append(DICHROIC_NOTE)
    band = band_note(frame.detector, int(round(stamp.y0 + y)), 1)
    if band:
        flags.append(band)

    method = ("large_aperture" if any(f.startswith("large aperture") for f in phot.flags)
              else "aperture")
    return LightCurvePoint(
        **base, x=stamp.x0 + x, y=stamp.y0 + y, centroid=used, flux_jy=phot.flux_jy,
        err_jy=phot.err_jy, lower_limit=phot.lower_limit, reason=phot.reason,
        flags=tuple(flags), method=method)
