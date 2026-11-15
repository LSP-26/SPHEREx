"""P4.1: group frames into visits and pick the preview set. Pure: no network, no disk.

A "visit" is one pass of SPHEREx over the target (frames close together in time).
The preview shows one wavelength-matched frame per (visit, detector), so frames at
different wavelengths are never blinked against each other.
"""
from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TypeVar

from contracts import FlipbookRequest, FrameRef, Visit

T = TypeVar("T")

DEFAULT_GAP_DAYS = 30.0     # plan: start near 30 d, then check the gap histogram (V1.4)
MATCH_TOLERANCE = 0.5       # resolution elements (plan A4, tau)
MIN_JUMP_RATIO = 5.0
MIN_VISIT_GAP_DAYS = 2.0    # a gap smaller than this is never a split between visits
MAX_VISIT_DAYS = 60.0       # auto mode: a visit longer than this is cut into windows
SPLIT_WINDOW_DAYS = 14.0    # window width, about the two weeks a pass over a point takes


@dataclass(frozen=True)
class Selection:
    visits_total: int                    # every visit observed ('n' in 'k of n visits')
    visits_shown: int                    # visits in the preview ('k')
    chosen: list[tuple[FrameRef, int]]   # (frame, visit_index), in time order


Selector = Callable[[list[FrameRef], FlipbookRequest], Selection]
LambdaOf = Callable[[FrameRef], "float | None"]


def even_subsample(items: Sequence[T], k: int) -> list[T]:
    """k items spread evenly over the sequence, always including first and last."""
    n = len(items)
    if k >= n:
        return list(items)
    if k <= 1:
        return [items[0]]
    return [items[i] for i in sorted({round(j * (n - 1) / (k - 1)) for j in range(k)})]


def simple_select(frames: list[FrameRef], req: FlipbookRequest) -> Selection:
    """Fallback selector: every frame is its own 'visit'.
    Used when no selector is given; the server uses make_selector."""
    ordered = sorted(frames, key=lambda f: f.t_start)
    pairs = even_subsample(list(enumerate(ordered)), req.preview_cap)
    return Selection(visits_total=len(ordered), visits_shown=len(pairs),
                     chosen=[(f, i) for i, f in pairs])


def resolution_element(lam_um: float) -> float:
    """lambda / R. R is about 40 below 3.82 um and about 120 above."""
    return lam_um / (40.0 if lam_um < 3.82 else 120.0)


# ------------------------------------------------------------------ visits
def choose_gap(frames: Sequence[FrameRef], default: float = DEFAULT_GAP_DAYS) -> float:
    """Pick the visit gap from this position's own gap histogram: the biggest jump between
    sorted gaps. Falls back to the default when there is no clear jump. A heuristic: V1.4
    checks it on 10 real positions."""
    ts = sorted(f.t_start for f in frames)
    gaps = sorted(max(b - a, 1e-3) for a, b in zip(ts, ts[1:], strict=False))
    if len(gaps) < 4:
        return default
    best_ratio, best = 1.0, None
    for lo, hi in zip(gaps, gaps[1:], strict=False):
        if hi >= MIN_VISIT_GAP_DAYS and hi / lo > best_ratio:
            best_ratio, best = hi / lo, math.sqrt(lo * hi)
    return best if best is not None and best_ratio >= MIN_JUMP_RATIO else default


def _split_long(group: list[FrameRef], width: float) -> list[list[FrameRef]]:
    """Cut a time-sorted group into consecutive windows of `width` days (empty ones dropped)."""
    t0 = group[0].t_start
    bins: dict[int, list[FrameRef]] = {}
    for f in group:
        bins.setdefault(int((f.t_start - t0) // width), []).append(f)
    return [bins[k] for k in sorted(bins)]


def cluster_visits(frames: Sequence[FrameRef], gap_days: float | None = None,
                   max_span_days: float = MAX_VISIT_DAYS,
                   window_days: float = SPLIT_WINDOW_DAYS) -> list[Visit]:
    """Split frames (sorted by time) wherever the gap to the previous frame exceeds gap_days.

    In auto mode (gap_days is None) a visit longer than max_span_days is then cut into
    windows of window_days. Near the ecliptic poles coverage is almost continuous (R Dra:
    2,365 frames over 519 d, no gap above 38 d), so there is no gap structure to find and
    the gap rule alone gives one huge visit. An explicit gap_days is used as given."""
    if not frames:
        return []
    ordered = sorted(frames, key=lambda f: f.t_start)
    gap = gap_days if gap_days is not None else choose_gap(ordered)
    groups: list[list[FrameRef]] = [[ordered[0]]]
    latest_end = ordered[0].t_end
    for f in ordered[1:]:
        if f.t_start - latest_end > gap:
            groups.append([])
            latest_end = f.t_end
        groups[-1].append(f)
        latest_end = max(latest_end, f.t_end)
    if gap_days is None:
        cut: list[list[FrameRef]] = []
        for g in groups:
            span = max(f.t_end for f in g) - g[0].t_start
            cut.extend(_split_long(g, window_days) if span > max_span_days else [g])
        groups = cut
    return [Visit(visit_index=i, t_start=g[0].t_start, t_end=max(f.t_end for f in g),
                  frame_ids=[f.frame_id for f in g]) for i, g in enumerate(groups)]


# ------------------------------------------------------------------ wavelength matching
def _reference_lambda(rows: list[tuple[int, FrameRef, float]]) -> float:
    """The wavelength that the most visits have a frame near (ties: shortest wavelength)."""
    best_count, best_lam = -1, 0.0
    for lam in sorted({r[2] for r in rows}):
        tol = MATCH_TOLERANCE * resolution_element(lam)
        count = len({v for v, _, other in rows if abs(other - lam) <= tol})
        if count > best_count:
            best_count, best_lam = count, lam
    return best_lam


def make_selector(lambda_of: LambdaOf | None = None, gap_days: float | None = None) -> Selector:
    """Build the preview selector. lambda_of(frame) is the wavelength at the target (D5.1).
    Without it, we cannot match wavelengths, so we take the middle frame of each
    (visit, detector). That fallback is NOT wavelength-matched: the UI should say so."""

    def select(frames: list[FrameRef], req: FlipbookRequest) -> Selection:
        by_id = {f.frame_id: f for f in frames}
        visits = cluster_visits(frames, gap_days)
        rows_by_det: dict[int, list[tuple[int, FrameRef, float | None]]] = defaultdict(list)
        for v in visits:
            for fid in v.frame_ids:
                f = by_id[fid]
                rows_by_det[f.detector].append(
                    (v.visit_index, f, lambda_of(f) if lambda_of else None))

        picks: dict[tuple[int, int], FrameRef] = {}  # (visit, detector) -> frame
        for det, rows in rows_by_det.items():
            if all(lam is not None for _, _, lam in rows):
                known = [(v, f, lam) for v, f, lam in rows if lam is not None]
                ref = _reference_lambda(known)
                tol = MATCH_TOLERANCE * resolution_element(ref)
                best: dict[int, tuple[float, FrameRef]] = {}
                for v, f, lam in known:
                    d = abs(lam - ref)
                    if d <= tol and (v not in best or d < best[v][0]):
                        best[v] = (d, f)
                for v, (_, f) in best.items():
                    picks[(v, det)] = f
            else:  # wavelength unknown: middle frame in time
                per_visit: dict[int, list[FrameRef]] = defaultdict(list)
                for v, f, _ in rows:
                    per_visit[v].append(f)
                for v, fs in per_visit.items():
                    picks[(v, det)] = fs[len(fs) // 2]

        have = sorted({v for v, _ in picks})
        per_visit: dict[int, int] = defaultdict(int)
        for v, _ in picks:
            per_visit[v] += 1
        # Show as many visits as fit under the cap. A visit gives one frame per detector that
        # saw the target: 2 near the ecliptic poles (one detector pair per exposure), up to 6
        # elsewhere. The old rule divided the cap by the detectors seen overall, which left
        # R Dra with 12 frames out of 36.
        k = len(have)
        while k > 1 and sum(per_visit[v] for v in even_subsample(have, k)) > req.preview_cap:
            k -= 1
        shown = set(even_subsample(have, k))
        chosen = sorted(((f, v) for (v, _), f in picks.items() if v in shown),
                        key=lambda fv: (fv[0].t_mid, fv[0].detector))
        chosen = even_subsample(chosen, req.preview_cap)
        return Selection(visits_total=len(visits), visits_shown=len(shown), chosen=chosen)

    return select


def make_spectral_selector(lambda_of: LambdaOf | None) -> Selector:
    """A selector for the wavelength flipbook: frames from every visit, sorted by the wavelength
    at the target, spread evenly over them (shortest first). Frames whose wavelength is unknown
    are left out. The visit index of each frame is kept, so the page can still tell the dates
    apart."""

    def select(frames: list[FrameRef], req: FlipbookRequest) -> Selection:
        visit_of = {fid: v.visit_index for v in cluster_visits(frames) for fid in v.frame_ids}
        rows = []
        for f in frames:
            lam = lambda_of(f) if lambda_of else None
            if lam is not None and lam > 0:
                rows.append((lam, f))
        rows.sort(key=lambda r: (r[0], r[1].t_mid, r[1].detector))
        picked = even_subsample(rows, req.preview_cap)
        chosen = [(f, visit_of.get(f.frame_id, 0)) for _, f in picked]
        return Selection(visits_total=len(set(visit_of.values())),
                         visits_shown=len({v for _, v in chosen}), chosen=chosen)

    return select
