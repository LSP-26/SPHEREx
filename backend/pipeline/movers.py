"""G3 / A9a: search one SPHEREx visit of a field for moving objects (asteroids, comets).

How SPHEREx looks at a spot (catalogue, 2026-11-08): a visit lasts one to four days. In it the spot
is exposed in BURSTS of 2 to 4 exposures 2.1 minutes apart, and bursts repeat 1.5 hours to a day
apart. Each exposure is seen by a detector pair at the same instant (D1+D4, D2+D5, D3+D6). A
main-belt asteroid moves about 6 px (37 arcsec) per hour, so:
  * inside a burst it barely moves (under 1 px in 6 minutes): a real object is in 2 or more
    frames of the burst at one spot, where a cosmic ray or glitch is in one frame only;
  * between bursts it moves many pixels, along a straight line at a steady rate.

Steps, all on the common north-up grid (render.regrid_cleaned), nothing interpolated:
  1. Reference sky for a frame: the per-pixel median of the same detector's frames in the visit
     that are NOT in the frame's burst (so the object cannot be in its own reference).
  2. Residual = frame - reference. Noise per pixel: the larger of the frame's robust noise and the
     spread of the reference stack there (stars change brightness between frames because the
     filter is variable: the spread absorbs that). Pixels on a static source (reference above
     STATIC_SIGMA) and within EDGE_GROW_PX of masked or uncovered pixels are not searched. On real
     frames (2026-11-08, cut at 8 sigma) a star's brightness changes still left peaks on it that
     wobbled by a pixel over a 10-day visit and linked into slow false tracks, so the cut is 3
     sigma: a mover crossing a star is lost there, but a false track is worse.
  3. Detections: local peaks of a 3x3 box sum at DET_SIGMA or more.
  4. A burst detection needs the same spot (MATCH_PX) in at least 2 frames of the burst.
  5. Tracks: burst detections (each at MIN_POINT_SNR or more) in at least MIN_BURSTS bursts on
     one straight line at a constant rate between MIN_RATE_PX_H and MAX_RATE_PX_H (fit residual
     under MAX_RMS_PX), no two points more than MAX_GAP_HOURS apart, moving at least MIN_SHIFT_PX
     overall. A stationary variable star does not move, so it is not a track; a spot that lights
     up again at the same place in other bursts hours away is dropped before linking.

Everything found is a CANDIDATE. The server compares it with SkyBoT (known asteroids and comets);
one that matches nothing known is still only a candidate until someone checks it.
Pure numpy/scipy; the server does the I/O. Tests: test_movers.py (injection and noise-only).
"""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage

from backend.pipeline.clean import robust_sigma

BURST_GAP_DAYS = 10 / 1440  # exposures closer than 10 minutes are one burst
VISIT_GAP_DAYS = 2.0  # bursts closer than 2 days are one visit
DET_SIGMA = 5.0  # 3x3 box sum, in units of its own noise
PEAK_SIGMA = 2.5  # the brightest pixel itself
STATIC_SIGMA = 3.0  # reference this bright = a static source: not searched
EDGE_GROW_PX = 2  # keep this far from masked, saturated or uncovered pixels
MATCH_PX = 1.5  # same object in two frames of one burst
LINK_PX = 2.0  # tolerance on the predicted position in another burst
MIN_BURSTS = 3
MIN_FRAMES_PER_BURST = 2
MIN_REF_FRAMES = 3
MIN_SHIFT_PX = 3.0
MAX_RATE_PX_H = 120.0  # about 12 arcmin per hour: faster than nearly all known NEOs
# 1.5 arcsec/h. Slower "tracks" on real frames were stars whose brightness changed (2026-11-08).
# This also leaves out the most distant objects (Kuiper belt, about 1 to 3 arcsec/h).
MIN_RATE_PX_H = 0.25
MAX_GAP_HOURS = 30.0  # between two points of a track: a main-belt asteroid crosses a
# 10 arcmin field in about 16 h; longer gaps joined unrelated specks
MIN_POINT_SNR = 8.0  # a burst detection, combined over its frames
MAX_RMS_PX = 1.2
# Smooth light that differs between bursts (the bright D1/D2 detector bands land on different sky
# as the pointing changes) is taken out of each residual with a running median this wide. A
# point source is 1 to 3 px across, so it survives. On a real visit the bands had made about 2000
# single-frame detections and a white strip across the picture (2026-11-08).
SMOOTH_BOX_PX = 11
# A spot that lights up again at the same place in 2 or more other bursts at least this far away
# in time is stationary (a variable star or an artefact): even the slowest allowed mover has moved
# 1.5 px by then. One such spot in a real visit flashed in 5 bursts and was chained with two
# faint specks into a false track (2026-11-08). One repeat is allowed: in a busy field a random
# detection lands within 1.5 px of a given spot about one time in eight.
# A real mover is in every frame of its bursts: both exposures and both detectors of a pair (D1+D4,
# D2+D5, D3+D6 see the same sky at once). In the Orion Nebula (2026-11-08) three false tracks were
# built from one detector (D4) only, with S/N about 0 in the D1 frames taken at the same moment:
# nebula features that move with the variable filter. The real Massalia and 1985 QA1 showed in the
# other frames too (QA1 at S/N 1 to 10 per frame). So the other frames of a track's bursts that
# cover the spot must add up to this S/N (sum of their box S/N over the square root of their count).
OTHER_FRAMES_SNR = 3.0
STATIONARY_HOURS = 6.0
STATIONARY_REPEATS = 2


@dataclass
class GridFrame:
    """One frame on the common grid. image is NaN wherever the pixel is not usable (masked,
    saturated or outside the frame)."""

    frame_id: str
    detector: int
    t_mjd: float
    image: np.ndarray


@dataclass
class Detection:
    frame_id: str
    detector: int
    t_mjd: float
    x: float
    y: float
    snr: float


@dataclass
class BurstDetection:
    burst: int
    t_mjd: float
    x: float
    y: float
    snr: float  # combined over the frames (quadrature sum)
    frames: list[str]


@dataclass
class Track:
    points: list[BurstDetection]
    x0: float  # position at t0 (px)
    y0: float
    t0: float  # MJD of the first point
    vx: float  # px per hour
    vy: float
    rms: float  # px, scatter about the straight line
    snr: float  # combined over all points

    @property
    def rate_px_h(self) -> float:
        return math.hypot(self.vx, self.vy)

    def at(self, t_mjd: float) -> tuple[float, float]:
        h = (t_mjd - self.t0) * 24
        return self.x0 + self.vx * h, self.y0 + self.vy * h


@dataclass
class SearchResult:
    tracks: list[Track]
    detections: list[Detection]
    bursts: list[float]  # mean MJD of each burst
    frames_searched: int
    frames_skipped: int  # no reference (too few other frames of the detector)
    max_snr: np.ndarray  # per pixel, the best burst-median S/N: shows the movers
    notes: list[str] = field(default_factory=list)


def group_times(times: list[float], gap_days: float) -> list[int]:
    """Group index for each time: times closer than gap_days (in sorted order) share a group."""
    order = sorted(range(len(times)), key=lambda i: times[i])
    out = [0] * len(times)
    g, last = -1, None
    for i in order:
        if last is None or times[i] - last >= gap_days:
            g += 1
        out[i] = g
        last = times[i]
    return out


def group_times_means(frames: list[GridFrame]) -> list[float]:
    """Mean MJD of each burst of these frames."""
    g = group_times([f.t_mjd for f in frames], BURST_GAP_DAYS)
    return [
        float(np.mean([f.t_mjd for f, k in zip(frames, g) if k == b])) for b in range(max(g) + 1)
    ]


def _residuals(frames: list[GridFrame], burst_of: list[int]):
    """Per frame: (residual, per-pixel noise, static source mask) or None (no reference)."""
    out: list[tuple[np.ndarray, np.ndarray, np.ndarray] | None] = [None] * len(frames)
    by_det: dict[int, list[int]] = {}
    for i, f in enumerate(frames):
        by_det.setdefault(f.detector, []).append(i)
    for idx in by_det.values():
        stack = np.stack([frames[i].image.astype("float64") for i in idx])
        for b in sorted({burst_of[i] for i in idx}):
            inside = [k for k, i in enumerate(idx) if burst_of[i] == b]
            others = [k for k, i in enumerate(idx) if burst_of[i] != b]
            if len(others) < MIN_REF_FRAMES:
                continue
            ref_stack = stack[others]
            n_ok = np.isfinite(ref_stack).sum(axis=0)
            with warnings.catch_warnings():  # all-NaN pixels: handled just below
                warnings.simplefilter("ignore", RuntimeWarning)
                ref = np.nanmedian(ref_stack, axis=0)
                spread = 1.4826 * np.nanmedian(np.abs(ref_stack - ref), axis=0)
            ref[n_ok < MIN_REF_FRAMES] = np.nan
            for k in inside:
                r = stack[k] - ref
                fin = np.isfinite(r)
                if fin.any():
                    r = r - ndimage.median_filter(np.where(fin, r, 0.0), size=SMOOTH_BOX_PX)
                    r[~fin] = np.nan
                sig = robust_sigma(r)
                if not sig > 0:
                    continue
                noise = np.fmax(np.nan_to_num(spread, nan=sig), sig)
                static = np.nan_to_num(ref, nan=0.0) > STATIC_SIGMA * sig
                out[idx[k]] = (r, noise, static)
    return out


def detect(
    residual: np.ndarray, noise: np.ndarray, static: np.ndarray
) -> list[tuple[float, float, float]]:
    """(x, y, snr) of point-like positive peaks in one residual frame."""
    finite = np.isfinite(residual)
    if not finite.any():
        return []
    r = np.where(finite, residual, 0.0)
    box = ndimage.uniform_filter(r, 3, mode="constant") * 9
    s = box / (3 * noise)
    z = r / noise
    # masked/uncovered pixels are widened (a half-masked star leaves a fake peak beside the hole);
    # a static source is not: its brightness changes are in `noise` already, and widening it
    # hid about a fifth of a crowded field, movers included
    unusable = ndimage.binary_dilation(~finite, iterations=EDGE_GROW_PX) | static
    edge = np.zeros_like(finite)
    edge[: EDGE_GROW_PX + 1, :] = edge[-EDGE_GROW_PX - 1 :, :] = True
    edge[:, : EDGE_GROW_PX + 1] = edge[:, -EDGE_GROW_PX - 1 :] = True
    peaks = (
        (s == ndimage.maximum_filter(s, 5))
        & (s >= DET_SIGMA)
        & (z >= PEAK_SIGMA)
        & ~unusable
        & ~edge
    )
    out = []
    for y, x in zip(*np.nonzero(peaks)):
        win = np.clip(r[y - 1 : y + 2, x - 1 : x + 2], 0, None)
        w = win.sum()
        if w > 0:
            yy, xx = np.mgrid[-1:2, -1:2]
            out.append(
                (x + float((xx * win).sum() / w), y + float((yy * win).sum() / w), float(s[y, x]))
            )
    return out


def _burst_detections(
    dets: list[Detection], burst_of_frame: dict[str, int], burst_t: list[float]
) -> list[BurstDetection]:
    """Spots seen in at least MIN_FRAMES_PER_BURST different frames of one burst."""
    out: list[BurstDetection] = []
    by_burst: dict[int, list[Detection]] = {}
    for d in dets:
        by_burst.setdefault(burst_of_frame[d.frame_id], []).append(d)
    for b, ds in by_burst.items():
        ds = sorted(ds, key=lambda d: -d.snr)
        used = [False] * len(ds)
        for i, d in enumerate(ds):
            if used[i]:
                continue
            group = [d]
            used[i] = True
            for j in range(i + 1, len(ds)):
                if (
                    not used[j]
                    and ds[j].frame_id not in {g.frame_id for g in group}
                    and math.hypot(ds[j].x - d.x, ds[j].y - d.y) <= MATCH_PX
                ):
                    group.append(ds[j])
                    used[j] = True
            combined = math.sqrt(sum(g.snr**2 for g in group))
            if (
                len({g.frame_id for g in group}) >= MIN_FRAMES_PER_BURST
                and combined >= MIN_POINT_SNR
            ):
                out.append(
                    BurstDetection(
                        burst=b,
                        t_mjd=burst_t[b],
                        x=float(np.mean([g.x for g in group])),
                        y=float(np.mean([g.y for g in group])),
                        snr=float(combined),
                        frames=[g.frame_id for g in group],
                    )
                )
    return out


def drop_stationary(bdets: list[BurstDetection], dets: list[Detection]) -> list[BurstDetection]:
    """Burst detections whose spot does not light up again in STATIONARY_REPEATS other bursts
    STATIONARY_HOURS or more away."""
    if not dets:
        return bdets
    xy = np.array([(d.x, d.y) for d in dets])
    t = np.array([d.t_mjd for d in dets])
    keep = []
    for b in bdets:
        near = np.hypot(xy[:, 0] - b.x, xy[:, 1] - b.y) <= MATCH_PX
        far = np.abs(t - b.t_mjd) * 24 >= STATIONARY_HOURS
        # distinct times rounded to bursts: frames of one burst are minutes apart
        bursts = {round(x * 24 / 0.5) for x in t[near & far]}
        if len(bursts) < STATIONARY_REPEATS:
            keep.append(b)
    return keep


def _fit(points: list[BurstDetection]) -> tuple[float, float, float, float, float, float]:
    """Straight line x(t), y(t) through the points: (x0, y0, t0, vx, vy, rms), v in px/h."""
    t0 = min(p.t_mjd for p in points)
    h = np.array([(p.t_mjd - t0) * 24 for p in points])
    xs, ys = np.array([p.x for p in points]), np.array([p.y for p in points])
    a = np.vstack([np.ones_like(h), h]).T
    (x0, vx), *_ = np.linalg.lstsq(a, xs, rcond=None)
    (y0, vy), *_ = np.linalg.lstsq(a, ys, rcond=None)
    res = np.hypot(xs - (x0 + vx * h), ys - (y0 + vy * h))
    return float(x0), float(y0), t0, float(vx), float(vy), float(np.sqrt(np.mean(res**2)))


def _longest_run(points: list[BurstDetection]) -> list[BurstDetection]:
    """The longest stretch of time-sorted points with no gap over MAX_GAP_HOURS."""
    best: list[BurstDetection] = []
    run: list[BurstDetection] = []
    for p in points:
        if run and (p.t_mjd - run[-1].t_mjd) * 24 > MAX_GAP_HOURS:
            run = []
        run.append(p)
        if len(run) > len(best):
            best = list(run)
    return best


def link(bdets: list[BurstDetection]) -> list[Track]:
    """Straight, steady tracks through at least MIN_BURSTS bursts, best first, no point shared."""
    by_burst: dict[int, list[BurstDetection]] = {}
    for d in bdets:
        by_burst.setdefault(d.burst, []).append(d)
    bursts = sorted(by_burst, key=lambda b: by_burst[b][0].t_mjd)
    found: dict[frozenset[int], Track] = {}
    for ia, a in enumerate(bursts):
        for b in bursts[ia + 1 :]:
            for p in by_burst[a]:
                for q in by_burst[b]:
                    dt = (q.t_mjd - p.t_mjd) * 24
                    if dt <= 0:
                        continue
                    vx, vy = (q.x - p.x) / dt, (q.y - p.y) / dt
                    if not MIN_RATE_PX_H <= math.hypot(vx, vy) <= MAX_RATE_PX_H:
                        continue
                    members = [p, q]
                    for c in bursts:
                        if c in (a, b):
                            continue
                        h = (by_burst[c][0].t_mjd - p.t_mjd) * 24
                        px, py = p.x + vx * h, p.y + vy * h
                        best = min(by_burst[c], key=lambda d: math.hypot(d.x - px, d.y - py))
                        if math.hypot(best.x - px, best.y - py) <= LINK_PX:
                            members.append(best)
                    if len(members) < MIN_BURSTS:
                        continue
                    members.sort(key=lambda d: d.t_mjd)
                    members = _longest_run(members)
                    if len(members) < MIN_BURSTS:
                        continue
                    key = frozenset(id(m) for m in members)
                    if key in found:
                        continue
                    x0, y0, t0, fvx, fvy, rms = _fit(members)
                    shift = math.hypot(members[-1].x - members[0].x, members[-1].y - members[0].y)
                    if (
                        rms > MAX_RMS_PX
                        or shift < MIN_SHIFT_PX
                        or not MIN_RATE_PX_H <= math.hypot(fvx, fvy) <= MAX_RATE_PX_H
                    ):
                        continue
                    snr = float(math.sqrt(sum(m.snr**2 for m in members)))
                    found[key] = Track(
                        points=members, x0=x0, y0=y0, t0=t0, vx=fvx, vy=fvy, rms=rms, snr=snr
                    )
    tracks = sorted(found.values(), key=lambda t: (-len(t.points), -t.snr, t.rms))
    kept: list[Track] = []
    used: set[int] = set()
    for t in tracks:
        ids = {id(m) for m in t.points}
        if ids & used:
            continue
        kept.append(t)
        used |= ids
    return kept


def other_frames_snr(
    track: Track, frames: list[GridFrame], burst_of: list[int], res: list
) -> tuple[float, int]:
    """(combined box S/N, number of frames) at the track's points in the frames of its bursts that
    were not part of the point and can see the spot (finite, not a static source)."""
    total, n = 0.0, 0
    for p in track.points:
        x, y = int(round(p.x)), int(round(p.y))
        used = set(p.frames)
        for f, b, r in zip(frames, burst_of, res):
            if b != p.burst or r is None or f.frame_id in used:
                continue
            resid, noise, static = r
            if not (1 <= x < resid.shape[1] - 1 and 1 <= y < resid.shape[0] - 1):
                continue
            win = resid[y - 1 : y + 2, x - 1 : x + 2]
            if not np.isfinite(win).all() or static[y - 1 : y + 2, x - 1 : x + 2].any():
                continue
            total += float(win.sum()) / (3 * float(noise[y, x]))
            n += 1
    return (total / math.sqrt(n) if n else 0.0), n


def search(frames: list[GridFrame]) -> SearchResult:
    """Run the whole search on the frames of ONE visit (all detectors, one common grid)."""
    if not frames:
        raise ValueError("no frames")
    shape = frames[0].image.shape
    burst_of = group_times([f.t_mjd for f in frames], BURST_GAP_DAYS)
    n_b = max(burst_of) + 1
    burst_t = [
        float(np.mean([f.t_mjd for f, b in zip(frames, burst_of) if b == k])) for k in range(n_b)
    ]
    res = _residuals(frames, burst_of)
    dets: list[Detection] = []
    snr_by_burst: dict[int, list[np.ndarray]] = {}
    for i, (f, r) in enumerate(zip(frames, res)):
        if r is None:
            continue
        resid, noise, static = r
        for x, y, s in detect(resid, noise, static):
            dets.append(Detection(f.frame_id, f.detector, f.t_mjd, x, y, s))
        z = np.where(static, np.nan, resid / noise)
        snr_by_burst.setdefault(burst_of[i], []).append(z)
    max_snr = np.full(shape, np.nan)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        for zs in snr_by_burst.values():
            if len(zs) >= MIN_FRAMES_PER_BURST:  # the median of 2+ frames drops cosmic rays
                max_snr = np.fmax(max_snr, np.nanmedian(np.stack(zs), axis=0))
    bdets = _burst_detections(dets, {f.frame_id: b for f, b in zip(frames, burst_of)}, burst_t)
    bdets = drop_stationary(bdets, dets)
    searched = sum(r is not None for r in res)
    notes = []
    usable_bursts = len({b for b in snr_by_burst if len(snr_by_burst[b]) >= MIN_FRAMES_PER_BURST})
    if usable_bursts < MIN_BURSTS:
        notes.append(
            f"Only {usable_bursts} burst(s) in this visit could be searched; a track "
            f"needs {MIN_BURSTS}, so nothing can be found here. Try another visit."
        )
    tracks = []
    for t in link(bdets):
        snr, n = other_frames_snr(t, frames, burst_of, res)
        if n >= MIN_FRAMES_PER_BURST and snr < OTHER_FRAMES_SNR:
            continue  # seen in some frames of its bursts and not in the others: not a mover
        tracks.append(t)
    return SearchResult(
        tracks=tracks,
        detections=dets,
        bursts=burst_t,
        frames_searched=searched,
        frames_skipped=len(frames) - searched,
        max_snr=max_snr,
        notes=notes,
    )


def render_max_snr(max_snr: np.ndarray, top: float = 10.0) -> tuple[np.ndarray, np.ndarray]:
    """(uint8 picture, covered) of the best burst S/N: 0 sigma and below black, `top` white."""
    covered = np.isfinite(max_snr)
    s = np.clip(np.where(covered, max_snr, 0.0) / top, 0.0, 1.0)
    return (np.sqrt(s) * 255).astype("uint8"), covered
