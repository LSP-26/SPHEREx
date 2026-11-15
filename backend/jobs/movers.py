"""Moving-object search for one field and one visit: fetch the visit's frames, run
backend/pipeline/movers.search, compare what it found with SkyBoT, and describe it as JSON.

Used by GET /movers (backend/server/app.py) and scripts/check_movers.py.
"""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Awaitable, Callable

import numpy as np

from backend.adapters.cache import UpstreamError
from backend.pipeline import movers as M
from backend.pipeline.clean import clean_stamp
from backend.pipeline.grid import PIXEL_ARCSEC, make_target_wcs
from backend.pipeline.render import preview_size_px, regrid_cleaned
from contracts import FrameRef, SkyPosition

log = logging.getLogger(__name__)

MOVERS_VERSION = 2  # part of the cache key: bump when the search or the JSON changes
MAX_FRAMES = 120  # one visit can have over 100 frames; more is slow, not more useful
MAX_FOV_ARCMIN = 30.0
MATCH_ARCSEC = 20.0  # a track point this close to a SkyBoT position is that object
SEEN_PX = 3.0  # a known object counts as seen in a burst this close to a detection
SKYBOT_PARALLEL = 4

SkybotFn = Callable[[float, float, float, float], Awaitable[list[dict]]]


def visits_of(frames: list[FrameRef]) -> list[list[FrameRef]]:
    """Frames grouped into visits (VISIT_GAP_DAYS), oldest first."""
    g = M.group_times([f.t_mid for f in frames], M.VISIT_GAP_DAYS)
    out: list[list[FrameRef]] = [[] for _ in range(max(g) + 1)] if frames else []
    for f, k in zip(frames, g):
        out[k].append(f)
    return out


def describe_visit(i: int, v: list[FrameRef]) -> dict:
    bursts = max(M.group_times([f.t_mid for f in v], M.BURST_GAP_DAYS)) + 1
    return {
        "index": i,
        "t_start_mjd": min(f.t_mid for f in v),
        "t_end_mjd": max(f.t_mid for f in v),
        "n_frames": len(v),
        "n_bursts": bursts,
        "detectors": sorted({f.detector for f in v}),
    }


def default_visit(visits: list[list[FrameRef]]) -> int:
    """The visit with the most bursts (more points on a track), then the most frames."""
    return max(
        range(len(visits)), key=lambda i: (describe_visit(i, visits[i])["n_bursts"], len(visits[i]))
    )


def _thin(frames: list[FrameRef], n: int) -> list[FrameRef]:
    """At most n frames, spread over the visit (every burst keeps some)."""
    if len(frames) <= n:
        return frames
    frames = sorted(frames, key=lambda f: f.t_mid)
    keep = np.linspace(0, len(frames) - 1, n).round().astype(int)
    return [frames[i] for i in sorted(set(keep))]


def to_grid_frames(pairs, pos: SkyPosition, size: int, use_zodi: bool) -> list[M.GridFrame]:
    out = []
    for fr, st in pairs:
        c = regrid_cleaned(
            clean_stamp(st, use_zodi=use_zodi, fill=False), st, pos.ra, pos.dec, size
        )
        if c is None:
            continue
        # every pixel with a real number (what the picture shows, holes NOT filled), saturated
        # ones out. The stricter c.good drops pixels the pipeline flags (TRANSIENT, SUR_ERROR...)
        # even when their values are fine: on real frames that hid 16 to 30 percent of the sky
        # and Massalia (V 10.9) itself (2026-11-08). One-frame glitches that come back in are
        # rejected by the two-frames-per-burst rule.
        src = c.display if c.display is not None else np.where(c.good, c.image, np.nan)
        img = np.where(np.isfinite(src) & ~c.saturated, src, np.nan).astype("float64")
        out.append(M.GridFrame(fr.frame_id, fr.detector, fr.t_mid, img))
    return out


def _picture_xy(x: float, y: float, size: int) -> dict:
    """Grid pixel (FITS order, row 0 south) to the picture's coordinates (north up, y down)."""
    yp = size - 1 - y
    return {"x": x, "y": yp, "u": (x + 0.5) / size, "v": (yp + 0.5) / size}


def known_match(result: M.SearchResult, known: dict[float, list[dict]], wcs, size: int):
    """(name per track or None, list of known objects in the field with how often each was seen)."""

    def pix(o):
        x, y = wcs.world_to_pixel_values(o["ra"], o["dec"])
        return float(x), float(y)

    names = []
    for t in result.tracks:
        votes: dict[str, int] = {}
        for p in t.points:
            ra, dec = wcs.pixel_to_world_values(p.x, p.y)
            for o in known.get(p.t_mjd, []):
                sep = (
                    math.hypot(
                        (o["ra"] - float(ra)) * math.cos(math.radians(o["dec"])),
                        o["dec"] - float(dec),
                    )
                    * 3600
                )
                if sep <= MATCH_ARCSEC:
                    votes[o["name"]] = votes.get(o["name"], 0) + 1
        best = max(votes.items(), key=lambda kv: kv[1], default=None)
        names.append(best[0] if best and best[1] * 2 >= len(t.points) else None)

    bdet_by_t: dict[float, list[tuple[float, float]]] = {}
    for t in result.tracks:
        for p in t.points:
            bdet_by_t.setdefault(p.t_mjd, []).append((p.x, p.y))
    objects: dict[str, dict] = {}
    for t_b, objs in known.items():
        for o in objs:
            x, y = pix(o)
            xi, yi = round(x), round(y)
            if not (0 <= xi < size and 0 <= yi < size) or not np.isfinite(result.max_snr[yi, xi]):
                continue
            e = objects.setdefault(
                o["name"],
                {
                    "name": o["name"],
                    "vmag": o["vmag"],
                    "cls": o["cls"],
                    "rate_arcsec_h": math.hypot(o["rate_ra"] or 0, o["rate_dec"] or 0),
                    "bursts_in_field": 0,
                    "bursts_on_track": 0,
                    "path": [],
                },
            )
            e["bursts_in_field"] += 1
            e["path"].append({"t_mjd": t_b, **_picture_xy(x, y, size)})
            if any(math.hypot(x - a, y - b) <= SEEN_PX for a, b in bdet_by_t.get(t_b, [])):
                e["bursts_on_track"] += 1
    found = set(n for n in names if n)
    for e in objects.values():
        e["tracked"] = e["name"] in found
    return names, sorted(objects.values(), key=lambda e: (e["vmag"] is None, e["vmag"] or 0))


async def find_movers(
    pos: SkyPosition,
    fov: float,
    frames: list[FrameRef],
    fetch: Callable[[FrameRef, SkyPosition, int], Awaitable],
    skybot: SkybotFn | None,
    visit: int | None = None,
    use_zodi: bool = True,
) -> tuple[dict, np.ndarray, np.ndarray]:
    """(JSON body, picture uint8 north up, covered mask) for one visit of the field."""
    fov = min(fov, MAX_FOV_ARCMIN)
    visits = visits_of(frames)
    if not visits:
        raise ValueError("no frames cover this position")
    vi = default_visit(visits) if visit is None else visit
    if not 0 <= vi < len(visits):
        raise ValueError(f"visit must be 0 to {len(visits) - 1}")
    chosen = _thin(visits[vi], MAX_FRAMES)
    size = preview_size_px(fov)

    async def fetch_grid(f: FrameRef) -> list[M.GridFrame]:
        # each stamp becomes one grid picture as soon as it arrives and is then dropped, so the
        # raw planes of all the visit's frames are never held at once (same pictures as before)
        st = await fetch(f, pos, size // 2)
        return await asyncio.to_thread(to_grid_frames, [(f, st)], pos, size, use_zodi)

    got_grids = await asyncio.gather(*(fetch_grid(f) for f in chosen), return_exceptions=True)
    ok = [g for g in got_grids if not isinstance(g, BaseException)]
    failed = len(chosen) - len(ok)
    for g in got_grids:
        if isinstance(g, BaseException) and not isinstance(g, UpstreamError):
            log.warning("mover search: a frame failed: %r", g)
    if not ok:
        if isinstance(got_grids[0], UpstreamError):
            raise got_grids[0]
        raise ValueError("no frame of this visit could be read")
    grid = [g for frames_ in ok for g in frames_]   # in the order of `chosen`

    result = await asyncio.to_thread(lambda: M.search(grid) if grid else None)
    if result is None:
        raise ValueError("the frames carry no sky coordinates")

    wcs = make_target_wcs(pos.ra, pos.dec, size)
    known: dict[float, list[dict]] = {}
    skybot_error = None
    if skybot is not None:
        radius = fov * 60 * 0.75
        sem = asyncio.Semaphore(SKYBOT_PARALLEL)

        async def one(t):
            async with sem:
                known[t] = await skybot(pos.ra, pos.dec, radius, t)

        got = await asyncio.gather(*(one(t) for t in result.bursts), return_exceptions=True)
        errs = [g for g in got if isinstance(g, BaseException)]
        if errs:
            log.warning("SkyBoT failed for %d of %d bursts: %s", len(errs), len(got), errs[0])
            skybot_error = (
                f"SkyBoT (known asteroids) did not answer for {len(errs)} of "
                f"{len(got)} bursts, so a track may be marked unknown wrongly."
            )
    names, objects = known_match(result, known, wcs, size)

    tracks = []
    for t, name in zip(result.tracks, names):
        pts = []
        for p in t.points:
            ra, dec = wcs.pixel_to_world_values(p.x, p.y)
            pts.append(
                {
                    "t_mjd": p.t_mjd,
                    **_picture_xy(p.x, p.y, size),
                    "ra": float(ra),
                    "dec": float(dec),
                    "snr": round(p.snr, 1),
                    "n_frames": len(p.frames),
                }
            )
        # picture y points down, so the velocity's y flips
        tracks.append(
            {
                "points": pts,
                "rate_px_h": round(t.rate_px_h, 3),
                "rate_arcsec_h": round(t.rate_px_h * PIXEL_ARCSEC, 1),
                "vx_px_h": round(t.vx, 3),
                "vy_px_h": round(-t.vy, 3),
                "pa_deg": round(math.degrees(math.atan2(-t.vx, t.vy)) % 360, 1),
                "rms_px": round(t.rms, 2),
                "snr": round(t.snr, 1),
                "known": name,
                "status": "known" if name else "candidate",
            }
        )
    notes = list(result.notes)
    if len(visits[vi]) > len(chosen):
        notes.append(
            f"This visit has {len(visits[vi])} frames; {len(chosen)} spread over it were used."
        )
    if failed:
        notes.append(f"{failed} frame(s) could not be read and were left out.")
    if skybot_error:
        notes.append(skybot_error)
    if skybot is None:
        notes.append("Known asteroids were not checked (SkyBoT is off).")
    body = {
        "ra": pos.ra,
        "dec": pos.dec,
        "fov_arcmin": fov,
        "size": size,
        "pixel_arcsec": PIXEL_ARCSEC,
        "visit": vi,
        "visits": [describe_visit(i, v) for i, v in enumerate(visits)],
        "bursts_mjd": result.bursts,
        "frames_used": len(ok),
        "frames_searched": result.frames_searched,
        "frames_skipped": result.frames_skipped,
        "n_detections": len(result.detections),
        "tracks": tracks,
        "known_objects": objects,
        "skybot_checked": skybot is not None and skybot_error is None,
        "notes": notes,
        "version": MOVERS_VERSION,
    }
    img, covered = M.render_max_snr(result.max_snr)
    return body, np.ascontiguousarray(np.flipud(img)), np.ascontiguousarray(np.flipud(covered))
