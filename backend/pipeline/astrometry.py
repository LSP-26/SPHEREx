"""V1.1 helpers (also the base of the centroid track, plan A10). Pure: no network, no disk.

find_centroid       the flux-weighted centre of the star nearest a predicted position
offset_stats        median and spread of a list of (dx, dy) offsets
isolated_candidates Gaia sources that make good astrometry checks: bright enough, alone
close_neighbours    Gaia sources near a target at a given date (the light-curve neighbour flag)

Coordinates are array coordinates: x is the column and y the row of an image[y, x] array, and a
pixel's centre is at integer values.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from backend.pipeline.motion import GAIA_EPOCH_MJD, propagate

PIXEL_ARCSEC = 6.15


@dataclass(frozen=True)
class Centroid:
    x: float
    y: float
    snr: float          # peak value over the noise


def find_centroid(image: np.ndarray, x: float, y: float, noise: float, *, search: int = 4,
                  box: int = 3, min_snr: float = 15.0, iterations: int = 3) -> Centroid | None:
    """Centre of the star near the predicted (x, y), or None if there is no trustworthy one.

    The brightest pixel within `search` pixels of the prediction is the star's peak. Its centre
    is the flux-weighted mean (negative pixels count as zero) of the (2 box + 1)^2 pixels round
    it, recentred up to `iterations` times. None when: any pixel of the search window is NaN
    (masked, saturated or off the stamp), the window leaves the array, the peak sits on the
    window's edge (a brighter star is just outside, so this one is not the peak), or the peak is
    under min_snr times the noise.
    """
    if not noise > 0 or not (math.isfinite(x) and math.isfinite(y)):
        return None
    img = np.asarray(image, dtype="float64")
    ny, nx = img.shape
    ix, iy = int(round(x)), int(round(y))
    x0, x1, y0, y1 = ix - search, ix + search + 1, iy - search, iy + search + 1
    if x0 < 0 or y0 < 0 or x1 > nx or y1 > ny:
        return None
    win = img[y0:y1, x0:x1]
    if not np.isfinite(win).all():
        return None
    py, px = np.unravel_index(int(np.argmax(win)), win.shape)
    if px in (0, 2 * search) or py in (0, 2 * search):
        return None
    snr = float(win[py, px]) / noise
    if snr < min_snr:
        return None
    cx, cy = float(x0 + px), float(y0 + py)
    for _ in range(iterations):
        jx, jy = int(round(cx)), int(round(cy))
        bx0, bx1, by0, by1 = jx - box, jx + box + 1, jy - box, jy + box + 1
        if bx0 < 0 or by0 < 0 or bx1 > nx or by1 > ny:
            return None
        sub = img[by0:by1, bx0:bx1]
        if not np.isfinite(sub).all():
            return None
        w = np.clip(sub, 0.0, None)
        total = float(w.sum())
        if total <= 0:
            return None
        yy, xx = np.mgrid[by0:by1, bx0:bx1]
        cx, cy = float((w * xx).sum() / total), float((w * yy).sum() / total)
    return Centroid(cx, cy, snr)


def offset_stats(offsets: list[tuple[float, float]]) -> dict | None:
    """n, median dx and dy, and the median, 90th percentile and largest radial offset (all in
    the offsets' own unit). None for an empty list."""
    if not offsets:
        return None
    a = np.asarray(offsets, dtype="float64")
    r = np.hypot(a[:, 0], a[:, 1])
    return {"n": int(len(a)), "median_dx": float(np.median(a[:, 0])),
            "median_dy": float(np.median(a[:, 1])), "median_r": float(np.median(r)),
            "p90_r": float(np.percentile(r, 90)), "max_r": float(r.max())}


def _number(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def isolated_candidates(sources: list[dict], g_min: float, g_max: float,
                        radius_arcsec: float = 6 * PIXEL_ARCSEC,
                        contrast_mag: float = 3.0) -> list[dict]:
    """The sources (as the dicts gaia_cone returns) with g_min <= G <= g_max that have no
    neighbour within radius_arcsec brighter than their own G + contrast_mag. Neighbours with no
    G magnitude cannot be judged and are ignored. Flat-sky separations, which is fine at these
    scales. Catalogue order is kept."""
    pts = []
    for s in sources:
        ra, dec, g = _number(s.get("ra")), _number(s.get("dec")), _number(s.get("g_mag"))
        if ra is not None and dec is not None and g is not None:
            pts.append((s, ra, dec, g))
    out = []
    for i, (s, ra, dec, g) in enumerate(pts):
        if not g_min <= g <= g_max:
            continue
        cosd = math.cos(math.radians(dec))
        crowded = False
        for j, (_, ra2, dec2, g2) in enumerate(pts):
            if j == i or g2 >= g + contrast_mag:
                continue
            dra = ((ra - ra2 + 180.0) % 360.0 - 180.0) * cosd
            if math.hypot(dra, dec - dec2) * 3600.0 < radius_arcsec:
                crowded = True
                break
        if not crowded:
            out.append(s)
    return out


def close_neighbours(sources: list[dict], ra: float, dec: float, mjd: float, radius_arcsec: float,
                     g_max: float, self_arcsec: float = 2.0) -> list[dict]:
    """The Gaia sources (as gaia_cone returns them) with G <= g_max within radius_arcsec of the
    sky position (ra, dec) at `mjd`, nearest first, as {source_id, sep_arcsec, g_mag}.

    Each source is moved from its own epoch to `mjd` by its proper motion (none if it has none).
    A source within self_arcsec of (ra, dec) is the target itself and is left out. Sources with
    no G magnitude cannot be judged and are ignored. Flat-sky separations."""
    cosd = math.cos(math.radians(dec))
    out = []
    for s in sources:
        g = _number(s.get("g_mag"))
        sra, sdec = _number(s.get("ra")), _number(s.get("dec"))
        if g is None or g > g_max or sra is None or sdec is None:
            continue
        pmra, pmdec = _number(s.get("pmra")), _number(s.get("pmdec"))
        if pmra is not None and pmdec is not None:
            try:
                sra, sdec = propagate(sra, sdec, pmra, pmdec,
                                      _number(s.get("ref_epoch_mjd")) or GAIA_EPOCH_MJD, mjd)
            except ValueError:
                continue
        dra = ((sra - ra + 180.0) % 360.0 - 180.0) * cosd
        sep = math.hypot(dra, sdec - dec) * 3600.0
        if self_arcsec <= sep <= radius_arcsec:
            out.append({"source_id": s.get("source_id"), "sep_arcsec": sep, "g_mag": g})
    return sorted(out, key=lambda n: n["sep_arcsec"])
