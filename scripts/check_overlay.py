"""V1.1: do the Gaia stars of /overlay land on the real stars in real frames?

  python scripts/check_overlay.py                          # Barnard's Star field, 15 arcmin
  python scripts/check_overlay.py --target r_dra --fov 10
  python scripts/check_overlay.py --ra 269.4462 --dec 4.7687 --catalogue .cache/catalogue

Asks Gaia (the same WebLookups the endpoint uses) for the stars of the field, keeps the bright
(G 10 to 14) isolated ones, picks one catalogue frame per (detector, pipeline version) over that
field (up to --max-frames), reads each stamp from S3 and, for every star, finds its centroid in
the pixels (backend/pipeline/astrometry.py). The star's predicted position is where Gaia puts it
moved to the frame's date. Two comparisons:

  GRID      project_sources (what /overlay returns) against the centroid on the flipbook's grid
            (stamp regridded with reproject_exact). This is the overlay's own test. PASS needs
            at least 10 stars, median offset 0.5 px or less and 90th percentile 1.0 px or less.
  DETECTOR  the frame's WCS with its SIP terms against the same WCS with them stripped, both
            against the same centroids, in detector pixels. This settles INT.8: it says whether
            the SIP terms help, hurt or change nothing.

Needs the internet (Gaia, S3) and a catalogue (default .cache/catalogue_targets, which is enough
for the demo targets; use --catalogue .cache/catalogue for any other position).
The report is also saved to .cache/v11/report.md, the offsets to .cache/v11/offsets.json.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np

from backend.adapters.cache import UpstreamError
from backend.adapters.catalogue_index import CatalogueFrameIndex
from backend.adapters.lookups import WebLookups
from backend.adapters.s3_stamps import S3StampSource
from backend.adapters.stubs import MemoryCache
from backend.adapters.wcs_util import celestial_wcs
from backend.pipeline.astrometry import find_centroid, isolated_candidates, offset_stats
from backend.pipeline.clean import clean_stamp, robust_sigma
from backend.pipeline.grid import make_target_wcs, regrid, stamp_wcs
from backend.pipeline.motion import GAIA_EPOCH_MJD, propagate
from backend.pipeline.overlay import project_sources
from backend.pipeline.render import preview_size_px
from backend.pipeline.select import even_subsample
from backend.pipeline.targets import TARGETS, position_at
from contracts import SkyPosition

OUT = Path(".cache/v11")
SIP_PREFIXES = ("A_", "B_", "AP_", "BP_")
MIN_STARS = 10
MAX_MEDIAN_R = 0.5
MAX_P90_R = 1.0
LINES: list[str] = []


def p(line: str = "") -> None:
    print(line, flush=True)
    LINES.append(line)


def fmt(st: dict | None) -> str:
    if st is None:
        return "no stars"
    return (f"{st['n']} stars, median offset ({st['median_dx']:+.2f}, {st['median_dy']:+.2f}) px, "
            f"median radius {st['median_r']:.2f}, 90th percentile {st['p90_r']:.2f}, "
            f"worst {st['max_r']:.2f}")


def without_sip(header: dict) -> dict:
    return {k: v for k, v in header.items() if not k.startswith(SIP_PREFIXES)}


def has_sip(header: dict) -> bool:
    return int(header.get("A_ORDER", 0)) > 1


def star_at(s: dict, mjd: float) -> tuple[float, float]:
    ra, dec = float(s["ra"]), float(s["dec"])
    epoch = s.get("ref_epoch_mjd") or GAIA_EPOCH_MJD
    try:
        return propagate(ra, dec, s.get("pmra") or 0.0, s.get("pmdec") or 0.0, epoch, mjd)
    except ValueError:
        return ra, dec


def pixel(wcs, ra: float, dec: float) -> tuple[float, float] | None:
    try:
        x, y = wcs.all_world2pix([[ra % 360.0, dec]], 0, quiet=True)[0]
    except Exception:
        return None
    if not (math.isfinite(x) and math.isfinite(y)):
        return None
    return float(x), float(y)


async def main(args: argparse.Namespace) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    if args.ra is not None and args.dec is not None:
        ra, dec, where = args.ra, args.dec, "given position"
    else:
        ra, dec = position_at(args.target)
        where = f"{args.target} at its 2026 position"
    pos = SkyPosition(ra=ra, dec=dec)
    size = preview_size_px(args.fov)
    half_stamp = int(1.45 * (size // 2)) + 3      # covers the grid even when the frame is rolled
    radius = min(120.0, half_stamp * math.sqrt(2) * 6.15 / 60.0 + 3.0)
    p(f"V1.1 on {where}: RA {ra:.5f} Dec {dec:.5f}, field {args.fov:g} arcmin "
      f"({size} px grid), stamps {2 * half_stamp + 1} px")

    lookups = WebLookups(cache=MemoryCache(), g_limit=17.0)
    try:
        sources = await lookups.gaia_cone(pos, radius)
    except UpstreamError as e:
        p(f"FAIL: could not get Gaia stars ({e.code.value}): {e.message}")
        p("Run scripts/check_lookups.py and send that output.")
        return
    finally:
        await lookups.aclose()
    candidates = isolated_candidates(sources, args.gmin, args.gmax)
    p(f"Gaia: {len(sources)} stars within {radius:.1f} arcmin (G < 17), "
      f"{len(candidates)} isolated with G {args.gmin:g} to {args.gmax:g}")

    index = CatalogueFrameIndex(args.catalogue)
    frames = await index.frames_at(pos, margin_px=half_stamp + 1)
    groups = defaultdict(list)
    for f in frames:
        groups[(f.detector, f.version)].append(f)
    picks = [sorted(fs, key=lambda f: f.t_start)[len(fs) // 2] for _, fs in sorted(groups.items())]
    picks = even_subsample(picks, args.max_frames)
    p(f"catalogue: {len(frames)} frames cover the field with room for the stamp, "
      f"{len(groups)} (detector, version) groups, reading {len(picks)}\n")

    sip_off: list[tuple[float, float]] = []
    flat_off: list[tuple[float, float]] = []
    shifts: list[float] = []
    grid_off: list[tuple[float, float]] = []
    grid_by_det: dict[int, list[tuple[float, float]]] = defaultdict(list)
    any_sip = False
    src = S3StampSource()
    for f in picks:
        tag = f"D{f.detector} {f.version:<14} MJD {f.t_mid:.1f}"
        try:
            st = await src.fetch(f, pos, half_stamp)
        except Exception as e:
            p(f"{tag}: could not read the stamp ({type(e).__name__}: {str(e)[:100]})")
            continue
        cleaned = clean_stamp(st, use_zodi=True)
        header = f.wcs_header
        wcs_sip, wcs_flat = celestial_wcs(header), celestial_wcs(without_sip(header))
        any_sip |= has_sip(header)

        n_det = 0
        for s in candidates:                                   # detector pixels
            sra, sdec = star_at(s, f.t_mid)
            a, b = pixel(wcs_sip, sra, sdec), pixel(wcs_flat, sra, sdec)
            if a is None or b is None:
                continue
            ax, ay = a[0] - st.x0, a[1] - st.y0
            c = find_centroid(cleaned.image, ax, ay, cleaned.noise, min_snr=args.min_snr)
            if c is None:
                continue
            n_det += 1
            sip_off.append((c.x - ax, c.y - ay))
            flat_off.append((c.x - (b[0] - st.x0), c.y - (b[1] - st.y0)))
            shifts.append(math.hypot(a[0] - b[0], a[1] - b[1]))

        ov = project_sources(candidates, ra, dec, args.fov, epoch_mjd=f.t_mid)   # the grid
        src_wcs = stamp_wcs(wcs_sip, st.x0, st.y0, cleaned.image.shape)
        g = regrid(cleaned.image, cleaned.variance, src_wcs, make_target_wcs(ra, dec, ov.size),
                   (ov.size, ov.size), "exact")
        noise_g = robust_sigma(g.image[np.isfinite(g.image)])
        here: list[tuple[float, float]] = []
        if noise_g > 0:
            for s in ov.stars:
                row = (ov.size - 1) - s.y          # the overlay's y points down, the array's up
                c = find_centroid(g.image, s.x, row, noise_g, min_snr=args.min_snr)
                if c is not None:
                    here.append((c.x - s.x, -(c.y - row)))
        grid_off += here
        grid_by_det[f.detector] += here
        st_here = offset_stats(here)
        p(f"{tag}: {n_det} stars found in detector pixels, {len(here)} on the grid"
          + (f", grid median radius {st_here['median_r']:.2f} px" if st_here else ""))
    await src.aclose()

    p("\n== GRID: project_sources against the picture (dx right, dy down, px)")
    for det in sorted(grid_by_det):
        p(f"   D{det}: {fmt(offset_stats(grid_by_det[det]))}")
    grid = offset_stats(grid_off)
    p(f"   all: {fmt(grid)}")

    p("\n== DETECTOR PIXELS: the frame's WCS with and without its SIP terms")
    s_sip, s_flat = offset_stats(sip_off), offset_stats(flat_off)
    p(f"   with SIP:    {fmt(s_sip)}")
    p(f"   without SIP: {fmt(s_flat)}")
    if shifts:
        p(f"   SIP moves the predicted position by a median {float(np.median(shifts)):.3f} px "
          f"(largest {max(shifts):.2f})")
    if not any_sip:
        p("   SIP: no frame in this run carries SIP terms, nothing to decide.")
    elif s_sip and s_flat and shifts:
        shift = float(np.median(shifts))
        diff = s_flat["median_r"] - s_sip["median_r"]
        if shift < 0.05:
            sip_verdict = "SIP changes the position by under 0.05 px: either choice is fine."
        elif diff > 0.05:
            sip_verdict = "KEEP the SIP terms: they bring the prediction closer to the stars."
        elif diff < -0.05:
            sip_verdict = "The SIP terms make it WORSE (INT.8)."
        else:
            sip_verdict = "No clear difference between with and without SIP."
        p(f"   INT.8: {sip_verdict}")

    p()
    if grid is None or grid["n"] < MIN_STARS:
        p(f"V1.1 RESULT: NOT ENOUGH STARS (need {MIN_STARS}, got {0 if grid is None else grid['n']}): "
          "try a bigger --fov, a denser field, or --min-snr 8")
    else:
        ok = grid["median_r"] <= MAX_MEDIAN_R and grid["p90_r"] <= MAX_P90_R
        p("V1.1 RESULT: " + ("PASS" if ok else "FAIL (read the lines above)"))
    (OUT / "report.md").write_text("\n".join(LINES) + "\n")
    (OUT / "offsets.json").write_text(json.dumps(
        {"grid": grid_off, "detector_sip": sip_off, "detector_flat": flat_off, "sip_shift": shifts}))
    p(f"report saved to {OUT / 'report.md'}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", choices=sorted(TARGETS), default="barnard")
    ap.add_argument("--ra", type=float)
    ap.add_argument("--dec", type=float)
    ap.add_argument("--fov", type=float, default=15.0, help="field of view, arcmin")
    ap.add_argument("--catalogue", default=".cache/catalogue_targets")
    ap.add_argument("--max-frames", type=int, default=12)
    ap.add_argument("--gmin", type=float, default=10.0)
    ap.add_argument("--gmax", type=float, default=14.0)
    ap.add_argument("--min-snr", type=float, default=15.0)
    asyncio.run(main(ap.parse_args()))
