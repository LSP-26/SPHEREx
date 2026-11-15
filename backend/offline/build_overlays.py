"""T6d: Gaia overlay JSON for the showcases, so the Gaia button works in static replays.

For every entry of frontend/public/showcases/manifest.json it asks Gaia (WebLookups.gaia_cone) for
the stars of the showcase's field, projects them onto the picture grid with project_sources (the
same code as GET /overlay) and writes <key>/overlay.json next to events.json. The file has the
shape of the /overlay response (size, epoch_mjd, pixel_arcsec, stars), so the page reads it with
the same types. A showcase whose Gaia query fails is skipped and reported.

  python -m backend.offline.build_overlays                  # all showcases
  python -m backend.offline.build_overlays --only barnard   # one
"""
from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Callable
from pathlib import Path

from backend.adapters.cache import UpstreamError
from backend.adapters.lookups import WebLookups
from backend.adapters.stubs import MemoryCache
from backend.pipeline.overlay import cone_radius_arcmin, project_sources
from contracts import SkyPosition

DEFAULT_DIR = "frontend/public/showcases"
G_LIMIT = 19.0      # fainter stars are of no use: one SPHEREx frame never shows them


async def build_one(entry: dict, lookups, root: Path,
                    log: Callable[[str], None] = print) -> Path | None:
    """Write <root>/<key>/overlay.json for one manifest entry. None if it was skipped."""
    key = entry["key"]
    folder = root / key
    if not folder.is_dir():
        log(f"{key}: no showcase folder, skipped")
        return None
    ra, dec, fov = float(entry["ra"]), float(entry["dec"]), float(entry["fov_arcmin"])
    try:
        sources = await lookups.gaia_cone(SkyPosition(ra=ra, dec=dec), cone_radius_arcmin(fov))
    except UpstreamError as e:
        log(f"{key}: Gaia failed ({e.code.value}): {e.message}")
        return None
    body = (await asyncio.to_thread(project_sources, sources, ra, dec, fov)).to_json()
    path = folder / "overlay.json"
    path.write_text(json.dumps(body, separators=(",", ":")))
    log(f"{key}: {len(body['stars'])} stars of {len(sources)}, "
        f"{path.stat().st_size / 1000:.0f} KB -> {path}")
    return path


async def build_all(root: str | Path, lookups, only: list[str] | None = None,
                    log: Callable[[str], None] = print, retries: int = 3) -> list[Path]:
    root = Path(root)
    manifest = json.loads((root / "manifest.json").read_text())["showcases"]
    built: list[Path] = []
    for entry in manifest:
        if only and entry["key"] not in only:
            continue
        path = None
        for attempt in range(retries):
            path = await build_one(entry, lookups, root, log)
            if path:
                break
            if attempt < retries - 1:
                await asyncio.sleep(5 * (attempt + 1))
        if path:
            built.append(path)
    return built


async def run(args: argparse.Namespace) -> None:
    lookups = WebLookups(cache=MemoryCache(), g_limit=G_LIMIT, first_timeout=90.0)
    try:
        built = await build_all(args.dir, lookups, args.only)
    finally:
        await lookups.aclose()
    print(f"{len(built)} overlay file(s) written")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default=DEFAULT_DIR)
    ap.add_argument("--only", nargs="+", help="showcase keys, e.g. barnard r_dra")
    asyncio.run(run(ap.parse_args(argv)))


if __name__ == "__main__":
    main()
