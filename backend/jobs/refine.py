"""O3: the refine job. Yields REFINED FrameEvents that replace preview frames in the same slot.

The preview shows one single frame per (visit, detector). Refine fetches every frame of those
visits (capped at MAX_REFINE_FRAMES overall, spread evenly in time), cleans each stamp, puts it on
the common north-up grid, and co-adds per (visit, detector) with inverse-variance weights
(pipeline/coadd.py). Visits are never combined with each other.

Details that matter:
- The slot of a refined frame is the slot the preview gave that (visit, detector), so the player
  swaps it in place. The slot map comes from running the same selector again, and visits from
  cluster_visits with its automatic gap, which is what make_selector(None) uses.
- channels_covered is the number of distinct 120-row bands of the detector (about one spectral
  channel each) that the stamps came from, out of 17. Fewer than 17 is badged partial.
- The float co-add (image and variance) is cached, keyed by the exact set of member frames, so a
  new frame arriving in a visit changes the key. PNGs are rendered from it with one white level
  per detector, so blinking between visits of one detector does not flicker in brightness
  (the known limit noted in render.py).
- A frame that fails or is mostly masked is left out of its co-add. A group with no usable frame
  produces no event, and the preview frame stays.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import time
from collections import Counter, defaultdict
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace

import numpy as np

from backend.adapters.cache import UpstreamError
from backend.adapters.wcs_util import celestial_wcs
from backend.jobs.preview import MAX_MASKED_FRACTION, masked_fraction, stamp_size_px
from backend.pipeline.clean import Cleaned, clean_stamp, fill_holes, flags_summary, robust_sigma
from backend.pipeline.coadd import channels_covered, coadd
from backend.pipeline.colour import merge_colour, rgba_png
from backend.pipeline.grid import make_target_wcs, regrid, regrid_mask, stamp_wcs
from backend.pipeline.render import MIN_WHITE_SIGMA, WHITE_PERCENTILE, _png, render_array_covered
from backend.pipeline.select import (
    Selection,
    Selector,
    cluster_visits,
    even_subsample,
    simple_select,
)
from contracts import (
    ErrorCode,
    ErrorEvent,
    FlipbookRequest,
    FrameEvent,
    FrameLabel,
    FrameRef,
    ProgressEvent,
    SkyPosition,
    Stage,
    cache_key,
)
from contracts.interfaces import Cache, FrameIndex, StampSource, WavelengthSource

log = logging.getLogger(__name__)

REFINE_VERSION = 4            # bump when this file changes what a co-add or its picture contains
MAX_REFINE_FRAMES = 600       # plan budget: 500 to 700 frames per position
CHANNEL_ROWS = 120            # rows per spectral channel (2040 / 17)
N_CHANNELS = 17
REGRID_METHOD = "exact"       # flux conserving; may be swapped for a cheaper method


# ------------------------------------------------------------------ planning
@dataclass(frozen=True)
class Group:
    slot: int
    visit: int
    detector: int
    frames: list[FrameRef]
    gid: str                   # identity of this exact set of frames (used in cache keys)


@dataclass(frozen=True)
class Member:
    image: np.ndarray          # on the common grid, NaN where unusable
    variance: np.ndarray
    lam: float
    t_mid: float
    channel: int
    saturated: bool
    release: str
    version: str
    sat_mask: np.ndarray | None = None    # OVERFLOW pixels on the common grid


@dataclass(frozen=True)
class GroupResult:
    group: Group
    image: np.ndarray
    variance: np.ndarray
    meta: dict
    saturated: np.ndarray | None = None   # co-add pixels drawn white


def channel_of(y: float) -> int:
    return int(min(N_CHANNELS, max(1, y // CHANNEL_ROWS + 1)))


def group_id(visit: int, detector: int, frames: list[FrameRef]) -> str:
    digest = hashlib.sha256(
        "|".join(sorted(f"{f.frame_id}:{f.version}" for f in frames)).encode()).hexdigest()[:16]
    return f"coadd_v{visit}_D{detector}_{digest}"


def plan_groups(frames: list[FrameRef], selection: Selection,
                cap: int = MAX_REFINE_FRAMES) -> list[Group]:
    """One group per preview slot, holding the frames of that (visit, detector), thinned evenly
    in time so that the total stays near `cap`."""
    slot_of = {(v, f.detector): slot for slot, (f, v) in enumerate(selection.chosen)}
    visit_of = {fid: v.visit_index for v in cluster_visits(frames) for fid in v.frame_ids}
    members: dict[tuple[int, int], list[FrameRef]] = defaultdict(list)
    for f in sorted(frames, key=lambda f: f.t_start):
        key = (visit_of.get(f.frame_id, -1), f.detector)
        if key in slot_of:
            members[key].append(f)
    per_group = max(1, cap // max(1, len(members)))
    out = []
    for key, fs in sorted(members.items(), key=lambda kv: slot_of[kv[0]]):
        fs = even_subsample(fs, per_group)
        out.append(Group(slot_of[key], key[0], key[1], fs, group_id(key[0], key[1], fs)))
    return out


# ------------------------------------------------------------------ pure array steps
def prepare(stamp, req: FlipbookRequest, size: int, wavelength: WavelengthSource,
            method: str = REGRID_METHOD) -> Member | None:
    """Clean one stamp and put it on the common grid. None if it carries no WCS."""
    header = getattr(stamp, "wcs_header", None) or getattr(stamp.frame, "wcs_header", None)
    if not header:
        return None
    cleaned = clean_stamp(stamp, use_zodi=req.use_zodi)
    ny, nx = cleaned.image.shape
    src = stamp_wcs(celestial_wcs(header), stamp.x0, stamp.y0, (ny, nx))
    target = make_target_wcs(req.ra, req.dec, size)
    g = regrid(cleaned.image, cleaned.variance, src, target, (size, size), method)
    sat = regrid_mask(cleaned.saturated, src, target, (size, size))
    try:
        lam = float(wavelength.lambda_um(stamp.frame, stamp.x0 + nx / 2, stamp.y0 + ny / 2))
    except Exception:
        lam = float("nan")
    return Member(g.image, g.variance, lam, stamp.frame.t_mid, channel_of(stamp.y0 + ny / 2),
                  "saturated" in flags_summary(cleaned), stamp.frame.release,
                  stamp.frame.version, sat)


def combine(members: list[Member]) -> tuple[np.ndarray, np.ndarray, dict]:
    """Inverse-variance co-add of one group, and the facts the label needs."""
    co = coadd([m.image for m in members], [m.variance for m in members])
    k, partial = channels_covered([m.channel for m in members])
    lams = [m.lam for m in members if np.isfinite(m.lam)]
    versions = {m.version for m in members}
    good = np.isfinite(co.image)
    meta = {
        "lambda_um": float(np.mean(lams)) if lams else 0.0,
        "t_mid_mjd": float(np.mean([m.t_mid for m in members])),
        "release": Counter(m.release for m in members).most_common(1)[0][0],
        "version": (next(iter(versions)) if len(versions) == 1
                    else f"mixed ({len(versions)} versions)"),
        "n_frames": len(members),
        "channels": k,
        "partial": partial,
        "saturated": any(m.saturated for m in members),
        "frac_masked": float((~good).mean()),
    }
    return co.image, (co.noise ** 2).astype("float32"), meta


def saturated_mask(members: list[Member]) -> np.ndarray:
    """Co-add pixels to draw white: saturated in at least half of the frames that carry a
    mask. A star that saturates in only a few frames of a visit is still measured from the
    others, so it is not painted white."""
    masks = [m.sat_mask for m in members if m.sat_mask is not None]
    if not masks:
        return np.zeros(members[0].image.shape, bool)
    return np.mean(np.stack(masks).astype("float32"), axis=0) >= 0.5


def as_cleaned(image: np.ndarray, variance: np.ndarray, use_zodi: bool,
               saturated: np.ndarray | None = None) -> Cleaned:
    good = np.isfinite(image)
    sat = np.zeros(image.shape, bool) if saturated is None else saturated
    # Like the preview, small holes (pixels no frame could measure) are filled from their
    # neighbours rather than drawn black; a large hole keeps a black middle.
    return Cleaned(image=image, variance=variance, good=good,
                   display=fill_holes(image).astype("float32"),
                   background=np.zeros(image.shape, "float32"),
                   noise=robust_sigma(image[good]), zodi_subtracted=use_zodi,
                   frac_masked=float((~good).mean()), frac_saturated=float(sat.mean()),
                   saturated=sat)


def shared_scale(cleaned: list[Cleaned]) -> tuple[float | None, float]:
    """(white level, noise) shared by every frame of one detector."""
    tops = [float(np.percentile(c.image[c.good], WHITE_PERCENTILE)) for c in cleaned
            if c.good.any()]
    noises = [c.noise for c in cleaned if c.noise > 0]
    noise = float(np.median(noises)) if noises else 1.0
    if not tops:
        return None, noise
    return max(float(np.median(tops)), MIN_WHITE_SIGMA * noise), noise


def render_detector(results: list[GroupResult], use_zodi: bool):
    """[(result, png bytes, width, height)] and the shared (white, noise)."""
    cleaned = [as_cleaned(r.image, r.variance, use_zodi, r.saturated) for r in results]
    white, noise = shared_scale(cleaned)
    out = []
    for r, c in zip(results, cleaned, strict=True):
        gray, cov = render_array_covered(replace(c, noise=noise), white)
        gray, cov = np.ascontiguousarray(np.flipud(gray)), np.ascontiguousarray(np.flipud(cov))
        out.append((r, _png(gray, cov), gray.shape[1], gray.shape[0]))
    return out, white, noise


def label_notes(meta: dict) -> list[str]:
    n = meta["n_frames"]
    notes = ["saturated"] if meta["saturated"] else []
    if meta["frac_masked"] >= 0.05:
        notes.append(f"{100 * meta['frac_masked']:.0f}% of pixels had no good measurement")
    notes.append(f"{n} frame{'s' if n != 1 else ''} co-added")
    return notes


def pack(image: np.ndarray, variance: np.ndarray,
         saturated: np.ndarray | None = None) -> bytes:
    sat = np.zeros(image.shape, bool) if saturated is None else saturated
    buf = io.BytesIO()
    np.savez_compressed(buf, image=image, variance=variance, saturated=sat)
    return buf.getvalue()


def unpack(raw: bytes) -> tuple[np.ndarray, np.ndarray]:
    with np.load(io.BytesIO(raw)) as z:
        return z["image"], z["variance"]


def unpack_with_mask(raw: bytes) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    with np.load(io.BytesIO(raw)) as z:
        sat = z["saturated"].astype(bool) if "saturated" in z.files \
            else np.zeros(z["image"].shape, bool)
        return z["image"], z["variance"], sat


# ------------------------------------------------------------------ the job
def _cancelled() -> ErrorEvent:
    return ErrorEvent(code=ErrorCode.CANCELLED, message="Cancelled.", retryable=True)


async def run_refine(
    req: FlipbookRequest,
    *,
    index: FrameIndex,
    stamps: StampSource,
    wavelength: WavelengthSource,
    cache: Cache,
    select: Selector = simple_select,
    renderer=None,                       # accepted so Deps.kwargs() can be passed straight in
    cancel: asyncio.Event | None = None,
    t0: float | None = None,
    concurrency: int = 32,
    max_frames: int = MAX_REFINE_FRAMES,
    method: str = REGRID_METHOD,
) -> AsyncIterator:
    t0 = time.monotonic() if t0 is None else t0
    pos = SkyPosition(ra=req.ra, dec=req.dec)
    size = stamp_size_px(req)
    try:
        frames = await index.frames_at(pos)
    except UpstreamError as e:
        log.warning("refine: frame lookup failed (%s), keeping the preview", e.code)
        return
    if req.detector is not None:
        frames = [f for f in frames if f.detector == req.detector]
    if not frames:
        return
    selection = await asyncio.to_thread(select, frames, req)
    groups = plan_groups(frames, selection, max_frames)
    if not groups:
        return
    total = sum(len(g.frames) for g in groups)

    base = {"zodi": req.use_zodi, "det": req.detector, "fov": req.fov_arcmin}

    def key(g: Group, kind: str, **extra) -> str:
        return cache_key(kind=kind, frame_id=g.gid, release="refine",
                         version=f"r{REFINE_VERSION}-{method}", ra=req.ra, dec=req.dec,
                         size_px=size, params={**base, **extra})

    # ---- co-adds already in the cache
    cached: dict[int, GroupResult] = {}
    for gi, g in enumerate(groups):
        raw_meta = await cache.get(key(g, "coadd-meta"))
        raw = await cache.get(key(g, "coadd")) if raw_meta else None
        if raw_meta and raw:
            image, variance, sat = unpack_with_mask(raw)
            cached[gi] = GroupResult(g, image, variance, json.loads(raw_meta), sat)

    # ---- one task per frame of every group that is not cached
    sem = asyncio.Semaphore(concurrency)

    async def fetch_one(gi: int, frame: FrameRef):
        try:
            async with sem:
                stamp = await stamps.fetch(frame, pos, size // 2)
                if masked_fraction(stamp.flags) > MAX_MASKED_FRACTION:
                    return gi, None
                return gi, await asyncio.to_thread(prepare, stamp, req, size, wavelength, method)
        except UpstreamError:
            return gi, None
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("refine frame failed")
            return gi, None

    left = {gi: len(g.frames) for gi, g in enumerate(groups) if gi not in cached}
    members: dict[int, list[Member]] = defaultdict(list)
    pending = {asyncio.create_task(fetch_one(gi, f))
               for gi, g in enumerate(groups) if gi in left for f in g.frames}
    cancel_waiter = asyncio.create_task(cancel.wait()) if cancel is not None else None
    det_left = Counter(g.detector for g in groups)
    det_results: dict[int, list[GroupResult]] = defaultdict(list)
    done_frames = sum(len(groups[gi].frames) for gi in cached)

    async def emit(det: int):
        results = sorted(det_results[det], key=lambda r: r.group.slot)
        if not results:
            return
        rendered, white, noise = await asyncio.to_thread(render_detector, results, req.use_zodi)
        for r, png, width, height in rendered:
            png_key = key(r.group, "png-refined", white=None if white is None else round(white, 4),
                          noise=round(noise, 6))
            await cache.put(png_key, png)
            m = r.meta
            label = FrameLabel(frame_id=f"coadd_v{r.group.visit}_D{r.group.detector}",
                               detector=r.group.detector, lambda_um=m["lambda_um"],
                               t_mid_mjd=m["t_mid_mjd"], release=m["release"],
                               version=m["version"], flags_summary=label_notes(m))
            yield FrameEvent(slot=r.group.slot, stage=Stage.REFINED,
                             png_url=f"/frames/{png_key}.png", width=width, height=height,
                             label=label, visit_index=r.group.visit,
                             channels_covered=m["channels"], partial=m["partial"],
                             elapsed_s=round(time.monotonic() - t0, 3))

    async def emit_colour():
        """One merged-colour frame per visit that has at least two colour channels, after the
        per-detector frames, in slots past the preview's."""
        if req.detector is not None:
            return
        by_visit: dict[int, dict[int, GroupResult]] = defaultdict(dict)
        for rs in det_results.values():
            for r in rs:
                by_visit[r.group.visit][r.group.detector] = r
        slot = max((g.slot for g in groups), default=-1) + 1
        for visit in sorted(by_visit):
            rs = by_visit[visit]
            if len(rs) < 2:
                continue
            try:
                col = await asyncio.to_thread(
                    merge_colour, {d: (r.image, r.variance) for d, r in rs.items()})
            except ValueError:
                continue
            if len(col.missing) > 1:
                continue
            metas = [r.meta for r in rs.values()]
            gid = hashlib.sha256("|".join(sorted(r.group.gid for r in rs.values())).encode()
                                 ).hexdigest()[:16]
            png = rgba_png(np.ascontiguousarray(np.flipud(col.rgb)),
                           np.ascontiguousarray(np.flipud(col.covered)))
            png_key = cache_key(kind="png-colour", frame_id=f"colour_v{visit}_{gid}",
                                release="refine", version=f"r{REFINE_VERSION}-colour",
                                ra=req.ra, dec=req.dec, size_px=size, params=base)
            await cache.put(png_key, png)
            notes = [f"merged colour of D{', D'.join(str(d) for d in sorted(rs))}"]
            if col.missing:
                notes.append(f"no detector for {', '.join(col.missing)}: that colour is left out")
            notes.append("colours are indicative (no flat-spectrum calibration yet)")
            label = FrameLabel(
                frame_id=f"colour_v{visit}", detector=0,
                lambda_um=float(np.mean([m["lambda_um"] for m in metas])),
                t_mid_mjd=float(np.mean([m["t_mid_mjd"] for m in metas])),
                release=metas[0]["release"], version=metas[0]["version"], flags_summary=notes)
            yield FrameEvent(slot=slot, stage=Stage.REFINED, png_url=f"/frames/{png_key}.png",
                             width=col.rgb.shape[1], height=col.rgb.shape[0], label=label,
                             visit_index=visit, channels_covered=None,
                             partial=bool(col.missing),
                             elapsed_s=round(time.monotonic() - t0, 3))
            slot += 1

    def register(gi: int, res: GroupResult | None) -> int | None:
        det = groups[gi].detector
        if res is not None:
            det_results[det].append(res)
        det_left[det] -= 1
        return det if det_left[det] == 0 else None

    try:
        yield ProgressEvent(done=done_frames, total=total, stage=Stage.REFINED)
        for gi, res in cached.items():
            det = register(gi, res)
            if det is not None:
                async for ev in emit(det):
                    yield ev
        while pending:
            waiting = pending | ({cancel_waiter} if cancel_waiter else set())
            done, _ = await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)
            if cancel_waiter in done:
                yield _cancelled()
                return
            for task in done:
                pending.discard(task)
                gi, member = task.result()
                if member is not None:
                    members[gi].append(member)
                left[gi] -= 1
                done_frames += 1
                yield ProgressEvent(done=done_frames, total=total, stage=Stage.REFINED)
                if left[gi] > 0:
                    continue
                res = None
                if members[gi]:
                    image, variance, meta = await asyncio.to_thread(combine, members[gi])
                    sat = await asyncio.to_thread(saturated_mask, members[gi])
                    g = groups[gi]
                    await cache.put(key(g, "coadd"), pack(image, variance, sat))
                    await cache.put(key(g, "coadd-meta"), json.dumps(meta).encode())
                    res = GroupResult(g, image, variance, meta, sat)
                    members[gi].clear()
                det = register(gi, res)
                if det is not None:
                    async for ev in emit(det):
                        yield ev
        async for ev in emit_colour():
            yield ev
    finally:
        for task in pending:
            task.cancel()
        if cancel_waiter:
            cancel_waiter.cancel()
        await asyncio.gather(*pending, *([cancel_waiter] if cancel_waiter else []),
                             return_exceptions=True)
