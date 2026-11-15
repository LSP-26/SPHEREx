"""Moving-object search for the showcases, so the "Moving objects" tab works in static replays.

For every entry of frontend/public/showcases/manifest.json it runs the same search as GET /movers
(backend/jobs/movers.py, the default visit of the field, checked against SkyBoT) and writes
<key>/movers.json (the /movers answer) and <key>/movers.png (the picture the tracks are drawn on)
next to events.json, then adds "movers": "<key>/movers.json" to the manifest entry so the page
knows the tab can be opened. A search that could not check SkyBoT is not saved: it would mark
known asteroids as unknown.

  python -m backend.offline.build_movers --catalogue .cache/catalogue            # all showcases
  python -m backend.offline.build_movers --catalogue .cache/catalogue --only barnard
"""
from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Callable
from pathlib import Path

import httpx
import numpy as np

from backend.adapters.skybot import skybot_cone
from backend.jobs.movers import find_movers
from backend.pipeline.render import _png
from backend.server.jobs import NoData
from contracts import FlipbookRequest, SkyPosition

DEFAULT_DIR = "frontend/public/showcases"


async def build_one(entry: dict, deps_factory: Callable, root: Path,
                    log: Callable[[str], None] = print) -> str | None:
    """Write movers.json and movers.png for one manifest entry; the manifest path, or None."""
    key = entry["key"]
    folder = root / key
    if not folder.is_dir():
        log(f"{key}: no showcase folder, skipped")
        return None
    pos = SkyPosition(ra=float(entry["ra"]), dec=float(entry["dec"]))
    fov = float(entry["fov_arcmin"])
    try:
        deps = await deps_factory(FlipbookRequest(ra=pos.ra, dec=pos.dec, fov_arcmin=fov))
        frames = await deps.index.frames_at(pos)
    except NoData as e:
        log(f"{key}: no data ({e})")
        return None
    try:
        async with httpx.AsyncClient() as client:
            async def sky(ra: float, dec: float, r: float, t: float) -> list[dict]:
                return await skybot_cone(client, ra, dec, r, t)
            body, img, covered = await find_movers(pos, fov, frames, deps.stamps.fetch, sky)
    except ValueError as e:
        log(f"{key}: cannot search this field: {e}")
        return None
    if not body["skybot_checked"]:
        log(f"{key}: SkyBoT did not answer, not saved (run again later)")
        return None
    png = await asyncio.to_thread(_png, np.ascontiguousarray(img), np.ascontiguousarray(covered))
    body["png_url"] = "movers.png"          # next to movers.json; the page resolves it
    (folder / "movers.png").write_bytes(png)
    (folder / "movers.json").write_text(json.dumps(body, separators=(",", ":")))
    log(f"{key}: {len(body['tracks'])} track(s), {len(body['known_objects'])} known object(s) "
        f"in the field, visit {body['visit']} -> {folder}")
    return f"{key}/movers.json"


def mark_manifest(root: Path, done: dict[str, str]) -> None:
    """Add "movers" to the manifest entries that now have a saved search."""
    path = root / "manifest.json"
    data = json.loads(path.read_text())
    for e in data["showcases"]:
        if e["key"] in done:
            e["movers"] = done[e["key"]]
    path.write_text(json.dumps(data, indent=1))


async def run(args: argparse.Namespace) -> None:
    from backend.adapters.stubs import MemoryCache
    from backend.server.deps import CatalogueDeps
    from backend.server.settings import Settings

    root = Path(args.out)
    manifest = json.loads((root / "manifest.json").read_text())["showcases"]
    deps = CatalogueDeps(Settings(source="catalogue", catalogue_dir=args.catalogue,
                                  cache_dir=None), MemoryCache())
    done: dict[str, str] = {}
    try:
        for entry in manifest:
            if args.only and entry["key"] not in args.only:
                continue
            path = await build_one(entry, deps, root)
            if path:
                done[entry["key"]] = path
    finally:
        await deps.aclose()
    mark_manifest(root, done)
    print(f"{len(done)} showcase(s) updated in {root / 'manifest.json'}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--catalogue", default=".cache/catalogue")
    ap.add_argument("--out", default=DEFAULT_DIR)
    ap.add_argument("--only", nargs="+", help="showcase keys, e.g. barnard r_dra")
    asyncio.run(run(ap.parse_args(argv)))


if __name__ == "__main__":
    main()
