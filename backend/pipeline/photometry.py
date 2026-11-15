"""T8a: aperture photometry on one cleaned stamp (plan A10). Pure: no network, no disk.

aperture_photometry() measures the flux of one source from a stamp that clean.py has already
cleaned (zodi and a plane subtracted, NaN where masked). Units:
  image, variance   MJy/sr and (MJy/sr)^2, as in the frame files
  area_arcsec2      solid angle of a pixel in arcsec^2 (the solid-angle map is in arcsec^2, T7).
                    A scalar, or an array the shape of the stamp (the map patch).
  flux, error       Jy
  Jy = sum over the aperture of (IMAGE - background) x area_arcsec2 x 2.3504e-5

STEPS
1. The aperture is a circle of radius r_ap pixels at (x, y). Each pixel gets the fraction of
   its area inside the circle (5 x 5 subsampling), so a moving centre does not make the flux jump.
2. The background is the mean of the annulus (2 to 3 times r_ap), after 3-sigma clipping.
   clean.py already took out a plane, so this only removes the local residual.
3. Error = sqrt( sum(w^2 variance) + (aperture size x background error)^2 ), in flux units.
   The background error is annulus scatter / sqrt(n pixels); it is the same for every pixel of
   the aperture, so it adds linearly, not in quadrature.

FLAGS (Photometry.flags, plain words)
  saturated  an OVERFLOW pixel touches the aperture. The flux is then a LOWER LIMIT
             (lower_limit is True): saturated pixels count as zero and nothing is scaled up.
  crowded    more than 10 percent of the annulus is brighter than the clip level: a neighbour
             is in the background annulus, so the background and the flux are doubtful.
  partial    some aperture pixels are masked (up to max_masked). Their share is filled in by
             scaling the rest. Over max_masked the measurement is refused.
A refused measurement has flux_jy = None and a `reason`: "outside", "annulus" or "masked".

Not here: neighbours from a catalogue (plan A10: neighbour within 2 px above 10 percent of the
flux). That needs Gaia, so it belongs to the caller.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

# 1 MJy = 1e6 Jy and 1 arcsec^2 = (pi / 648000)^2 sr, so Jy per (MJy/sr x arcsec^2) is 2.3504e-5
JY_PER_MJY_ARCSEC2 = 1e6 * (math.pi / 648000.0) ** 2

R_AP = 2.0
ANNULUS_SCALE = (2.0, 3.0)
SUBSAMPLE = 5
MAX_MASKED = 0.25
CLIP_SIGMA = 3.0
CLIP_ITERATIONS = 3
MIN_ANNULUS_PIXELS = 12
CROWDED_FRACTION = 0.10


@dataclass(frozen=True)
class Photometry:
    flux_jy: float | None
    err_jy: float | None
    background: float          # MJy/sr subtracted per pixel (annulus level)
    n_aperture: float          # aperture area in pixels (sum of the weights)
    n_annulus: int             # annulus pixels kept after clipping
    masked_fraction: float     # share of the aperture that had no good pixel
    saturated: bool
    crowded: bool
    lower_limit: bool
    reason: str | None
    flags: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return self.flux_jy is not None


def circle_weights(shape: tuple[int, int], x: float, y: float, r: float,
                   sub: int = SUBSAMPLE) -> np.ndarray:
    """Fraction (0 to 1) of each pixel inside the circle. Pixel centres are at integers."""
    ny, nx = shape
    w = np.zeros(shape, dtype="float64")
    x0, x1 = max(0, math.floor(x - r) - 1), min(nx, math.ceil(x + r) + 2)
    y0, y1 = max(0, math.floor(y - r) - 1), min(ny, math.ceil(y + r) + 2)
    if x0 >= x1 or y0 >= y1:
        return w
    off = (np.arange(sub) + 0.5) / sub - 0.5
    yy = np.arange(y0, y1)[:, None, None, None] + off[None, None, :, None]
    xx = np.arange(x0, x1)[None, :, None, None] + off[None, None, None, :]
    inside = (xx - x) ** 2 + (yy - y) ** 2 <= r * r
    w[y0:y1, x0:x1] = inside.mean(axis=(2, 3))
    return w


def _refused(reason: str, **kw) -> Photometry:
    base = dict(flux_jy=None, err_jy=None, background=float("nan"), n_aperture=0.0,
                n_annulus=0, masked_fraction=0.0, saturated=False, crowded=False,
                lower_limit=False, reason=reason, flags=(reason,))
    base.update(kw)
    return Photometry(**base)


def aperture_photometry(image, variance, x: float, y: float, area_arcsec2, *,
                        good=None, saturated=None, r_ap: float = R_AP,
                        annulus_scale: tuple[float, float] = ANNULUS_SCALE,
                        max_masked: float = MAX_MASKED, clip: float = CLIP_SIGMA,
                        widen_background: bool = False) -> Photometry:
    """Flux of the source at array position (x = column, y = row) of a cleaned stamp."""
    img = np.asarray(image, dtype="float64")
    var = np.asarray(variance, dtype="float64")
    if img.ndim != 2 or img.shape != var.shape:
        raise ValueError(f"image and variance must be 2-D and the same shape: "
                         f"{img.shape} vs {var.shape}")
    if not (r_ap > 0 and 1 <= annulus_scale[0] < annulus_scale[1]):
        raise ValueError("r_ap must be above 0 and 1 <= annulus_scale[0] < annulus_scale[1]")
    area = np.broadcast_to(np.asarray(area_arcsec2, dtype="float64"), img.shape)
    sat = np.zeros(img.shape, bool) if saturated is None else np.asarray(saturated, bool)
    ok_pix = np.isfinite(img) & np.isfinite(var) & (var > 0)
    if good is not None:
        ok_pix &= np.asarray(good, bool)
    ok_pix &= ~sat

    ny, nx = img.shape
    if not (math.isfinite(x) and math.isfinite(y)) or x - r_ap < -0.5 or y - r_ap < -0.5 \
            or x + r_ap > nx - 0.5 or y + r_ap > ny - 0.5:
        return _refused("outside")

    w = circle_weights(img.shape, x, y, r_ap)
    ap = w > 0
    if not (np.isfinite(area[ap]).all() and (area[ap] > 0).all()):
        raise ValueError("area_arcsec2 must be finite and above 0 under the aperture")
    total_w = float(w.sum())
    sat_in = bool((sat & ap).any())

    # ---- background from the annulus
    yy, xx = np.indices(img.shape)
    d = np.hypot(xx - x, yy - y)
    r_in, r_out = annulus_scale[0] * r_ap, annulus_scale[1] * r_ap
    bg_note = None
    vals = img[(d >= r_in) & (d <= r_out) & ok_pix]
    if vals.size < MIN_ANNULUS_PIXELS and widen_background:
        # the ring round a saturated star is flagged fatal and swallows the annulus: look
        # further out, then at the whole stamp (clean.py already took out a plane)
        for lo, hi in ((r_out, 5.0 * r_ap), (r_in, math.inf)):
            more = img[(d >= lo) & (d <= hi) & ok_pix]
            if more.size >= MIN_ANNULUS_PIXELS:
                vals, r_in, r_out = more, lo, hi
                bg_note = ("background from a wider ring (the annulus is masked)"
                           if hi < math.inf else
                           "background from the whole stamp (the annulus is masked)")
                break
    if vals.size < MIN_ANNULUS_PIXELS:
        return _refused("annulus", saturated=sat_in)
    keep = np.ones(vals.size, bool)
    for _ in range(CLIP_ITERATIONS):
        med = float(np.median(vals[keep]))
        s = 1.4826 * float(np.median(np.abs(vals[keep] - med)))
        if s <= 0:
            break
        new = np.abs(vals - med) <= clip * s
        if new.sum() < MIN_ANNULUS_PIXELS or new.sum() == keep.sum():
            break
        keep = new
    kept = vals[keep]
    background = float(kept.mean())
    # never trust a scatter below what the pipeline's own VARIANCE says (a smooth or
    # noise-free annulus would otherwise give a background error of about zero)
    sigma_pipe = math.sqrt(float(np.median(var[(d >= r_in) & (d <= r_out) & ok_pix])))
    sigma = max(float(kept.std(ddof=1)), sigma_pipe)
    crowded = float(np.mean(vals - background > clip * sigma)) > CROWDED_FRACTION
    sigma_bg = sigma / math.sqrt(kept.size)

    # ---- the aperture
    valid = ap & ok_pix
    valid_w = float(w[valid].sum())
    masked_fraction = 1.0 - valid_w / total_w
    if sat_in and valid_w > 0:
        scale = 1.0                    # lower limit: saturated pixels count as zero
    elif valid_w <= 0 or masked_fraction > max_masked:
        return _refused("masked", masked_fraction=masked_fraction, crowded=crowded,
                        saturated=sat_in,
                        background=background, n_aperture=total_w, n_annulus=int(kept.size))
    else:
        scale = total_w / valid_w
    wv, av = w[valid], area[valid]
    flux = float((wv * (img[valid] - background) * av).sum()) * scale * JY_PER_MJY_ARCSEC2
    var_term = float((wv ** 2 * var[valid] * av ** 2).sum()) * scale ** 2
    bg_term = (float((wv * av).sum()) * scale * sigma_bg) ** 2
    err = math.sqrt(var_term + bg_term) * JY_PER_MJY_ARCSEC2

    flags = []
    if sat_in:
        flags.append("saturated")
    if bg_note:
        flags.append(bg_note)
    if crowded:
        flags.append("crowded")
    if not sat_in and masked_fraction > 1e-9:
        flags.append(f"partial ({100 * masked_fraction:.0f}% of the aperture masked)")
    return Photometry(flux_jy=flux, err_jy=err, background=background, n_aperture=total_w,
                      n_annulus=int(kept.size), masked_fraction=masked_fraction,
                      saturated=sat_in, crowded=crowded, lower_limit=sat_in, reason=None,
                      flags=tuple(flags))


LARGE_R_AP = 8.0
SKY_INNER = 15.0
MIN_SKY_PIXELS = 20
MIN_FINITE_FRACTION = 0.5


def large_aperture_photometry(display, x: float, y: float, area_arcsec2, *, good,
                              saturated: bool = False, r_ap: float = LARGE_R_AP,
                              sky_inner: float = SKY_INNER,
                              min_finite: float = MIN_FINITE_FRACTION) -> Photometry:
    """LOWER LIMIT for a source whose core is saturated and whose ring is flagged (R Dra).

    Sums (display - sky) over every pixel within r_ap that has a number, flagged or not
    (TRANSIENT, SUR_ERROR, BLOOM keep sensible values, see clean.py). Pixels with no number,
    the saturated core included, count as zero, so the flux can only be too low. Sky is the
    median of the good pixels at least sky_inner pixels away. The error is statistical only.
    Refused: "outside", "masked" (under min_finite of the aperture has a number) or
    "annulus" (too few sky pixels)."""
    img = np.asarray(display, dtype="float64")
    ny, nx = img.shape
    if not (math.isfinite(x) and math.isfinite(y)) or x - r_ap < -0.5 or y - r_ap < -0.5 \
            or x + r_ap > nx - 0.5 or y + r_ap > ny - 0.5:
        return _refused("outside")
    area = np.broadcast_to(np.asarray(area_arcsec2, dtype="float64"), img.shape)
    w = circle_weights(img.shape, x, y, r_ap)
    ap = w > 0
    fin = ap & np.isfinite(img)
    if not (np.isfinite(area[ap]).all() and (area[ap] > 0).all()):
        raise ValueError("area_arcsec2 must be finite and above 0 under the aperture")
    total_w = float(w.sum())
    frac = float(w[fin].sum()) / total_w
    if frac < min_finite:
        return _refused("masked", masked_fraction=1.0 - frac, saturated=saturated)
    yy, xx = np.indices(img.shape)
    d = np.hypot(xx - x, yy - y)
    sky_pix = img[(d >= sky_inner) & np.asarray(good, bool) & np.isfinite(img)]
    if sky_pix.size < MIN_SKY_PIXELS:
        return _refused("annulus", saturated=saturated)
    sky = float(np.median(sky_pix))
    sigma = 1.4826 * float(np.median(np.abs(sky_pix - sky)))
    wv, av = w[fin], area[fin]
    flux = float((wv * (img[fin] - sky) * av).sum()) * JY_PER_MJY_ARCSEC2
    err = math.sqrt(sigma ** 2 * float(((wv * av) ** 2).sum())
                    + (sigma / math.sqrt(sky_pix.size) * float((wv * av).sum())) ** 2) \
        * JY_PER_MJY_ARCSEC2
    flags = [f"large aperture ({r_ap:g} px), flagged pixels included, a lower limit"]
    if frac < 1.0 - 1e-9:
        flags.append(f"{100 * (1.0 - frac):.0f}% of the aperture has no value (counted as zero)")
    return Photometry(flux_jy=flux, err_jy=err, background=sky, n_aperture=total_w,
                      n_annulus=int(sky_pix.size), masked_fraction=1.0 - frac,
                      saturated=saturated, crowded=False, lower_limit=True, reason=None,
                      flags=tuple(flags))
