"""Plan A10: mark light-curve points that have a Gaia neighbour close to the target.

For every point of frontend/public/lightcurves/<key>.json it moves the target to the point's date
(targets.position_at) and looks for Gaia stars (G <= G_MAX) within NEIGHBOUR_PX pixels (the 2 px
aperture plus one pixel of PSF wing). Each point that has one gets the flag NEIGHBOUR_FLAG, with
the nearest neighbour's distance and G. Flags only: no flux changes. One Gaia cone per target
(WebLookups, so ESA then GAVO then VizieR). Safe to rerun: an old neighbour flag is replaced.

Gaia G is a poor guide to what SPHEREx sees (red stars are brighter at 1 to 5 um than in G), so
read the flag as "look at this point", not "this point is wrong".

  python -m backend.offline.flag_neighbours                  # r_dra and rw_aur
  python -m backend.offline.flag_neighbours --targets rw_aur
  python -m backend.offline.flag_neighbours --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from backend.adapters.lookups import WebLookups
from backend.adapters.stubs import MemoryCache
from backend.pipeline.astrometry import PIXEL_ARCSEC, close_neighbours
from backend.pipeline.targets import SURVEY_MID_MJD, TARGETS, position_at
from contracts import SkyPosition

DEFAULT_DIR = "frontend/public/lightcurves"
NEIGHBOUR_PX = 3.0
G_MAX = 15.0
CONE_ARCMIN = 1.5
NEIGHBOUR_FLAG = "Gaia neighbour"      # a flag text starts with this; the detail follows


def flag_text(n: dict) -> str:
    return f"{NEIGHBOUR_FLAG} {n['sep_arcsec']:.0f} arcsec away (G {n['g_mag']:.1f})"


def flag_points(body: dict, sources: list[dict]) -> int:
    """Rewrite the neighbour flag on every point of a light-curve file body. Returns how many
    points now carry it."""
    key, n = body["key"], 0
    for p in body["points"]:
        p["flags"] = [f for f in p["flags"] if not f.startswith(NEIGHBOUR_FLAG)]
        ra, dec = position_at(key, p["t_mjd"])
        near = close_neighbours(sources, ra, dec, p["t_mjd"], NEIGHBOUR_PX * PIXEL_ARCSEC, G_MAX)
        if near:
            p["flags"].append(flag_text(near[0]))
            n += 1
    return n


async def run(args: argparse.Namespace) -> None:
    root = Path(args.dir)
    lookups = WebLookups(cache=MemoryCache(), first_timeout=90.0)
    try:
        for key in args.targets:
            path = root / f"{key}.json"
            body = json.loads(path.read_text())
            ra, dec = position_at(key, SURVEY_MID_MJD)
            sources = await lookups.gaia_cone(SkyPosition(ra=ra, dec=dec), CONE_ARCMIN)
            n = flag_points(body, sources)
            print(f"{key}: {len(sources)} Gaia stars in {CONE_ARCMIN} arcmin, "
                  f"{n} of {len(body['points'])} points have a neighbour within "
                  f"{NEIGHBOUR_PX:g} px (G <= {G_MAX:g})")
            if not args.dry_run:
                path.write_text(json.dumps(body))
    finally:
        await lookups.aclose()


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default=DEFAULT_DIR)
    ap.add_argument("--targets", nargs="+", choices=sorted(TARGETS), default=["r_dra", "rw_aur"])
    ap.add_argument("--dry-run", action="store_true")
    asyncio.run(run(ap.parse_args(argv)))


if __name__ == "__main__":
    main()
