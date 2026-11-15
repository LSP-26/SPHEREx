"""P5: co-add cleaned frames of ONE visit and ONE detector (plan A8). Pure.

S = sum(w f) / sum(w) with w = 1/variance, noise = 1/sqrt(sum(w)), NaN where nothing is usable.
Frames must already be on the same pixel grid (the common-grid step does that). Never co-add
across visits: the point of the flipbook is to see change between them.

Outlier clipping only runs with at least MIN_FOR_CLIP frames, and only removes pixels far
from the per-pixel median, so a cosmic ray goes but a normal star does not.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

MIN_FOR_CLIP = 5
CLIP_SIGMA = 5.0
N_CHANNELS = 17


@dataclass(frozen=True)
class CoAdd:
    image: np.ndarray        # float32, NaN where no frame had a usable pixel
    noise: np.ndarray        # float32, 1-sigma per pixel, NaN where image is NaN
    n_used: np.ndarray       # int16, frames that contributed to each pixel
    n_frames: int


def coadd(images: list[np.ndarray], variances: list[np.ndarray]) -> CoAdd:
    if not images or len(images) != len(variances):
        raise ValueError("need the same, non-zero number of images and variances")
    f = np.stack([np.asarray(a, dtype="float64") for a in images])
    v = np.stack([np.asarray(a, dtype="float64") for a in variances])
    if f.shape != v.shape:
        raise ValueError(f"image and variance shapes differ: {f.shape} vs {v.shape}")
    ok = np.isfinite(f) & np.isfinite(v) & (v > 0)
    if f.shape[0] >= MIN_FOR_CLIP:
        fm = np.where(ok, f, np.nan)
        med = np.nanmedian(fm, axis=0)
        scatter = 1.4826 * np.nanmedian(np.abs(fm - med), axis=0)
        sigma = np.maximum(np.sqrt(np.where(ok, v, np.nan)), scatter)
        with np.errstate(invalid="ignore"):
            ok &= np.abs(f - med) <= CLIP_SIGMA * sigma
    w = np.where(ok, 1.0 / np.where(ok, v, 1.0), 0.0)
    wsum = w.sum(axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        s = (w * np.where(ok, f, 0.0)).sum(axis=0) / wsum
        noise = 1.0 / np.sqrt(wsum)
    s[wsum == 0] = np.nan
    noise[wsum == 0] = np.nan
    return CoAdd(image=s.astype("float32"), noise=noise.astype("float32"),
                 n_used=ok.sum(axis=0).astype("int16"), n_frames=f.shape[0])


def coadd_cleaned(frames) -> CoAdd:
    """Co-add a list of clean.Cleaned results (duck-typed: .image and .variance)."""
    return coadd([c.image for c in frames], [c.variance for c in frames])


def channels_covered(channels) -> tuple[int, bool]:
    """(k of 17, partial). Two visits are only comparable if their coverage matches, so a
    co-add that saw fewer than all 17 channels is badged as partial (plan A8)."""
    k = len({int(c) for c in channels if c is not None})
    return k, k < N_CHANNELS
