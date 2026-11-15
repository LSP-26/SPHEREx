"""Flagged-pixel layers for the showcases, so "Show flagged pixels" works in static replays.

For every entry of frontend/public/showcases/manifest.json it takes each single frame the
flipbook shows (the preview frames in events.json), reads the frame's FLAGS plane exactly as
GET /flags does (backend/pipeline/flagmap.py) and writes <key>/flags/<slot>.png (the RGBA layer)
plus <key>/flags.json, a map from frame id to the same JSON /flags answers with (its png_url is
relative to flags.json). The manifest entry gets "flags": "<key>/flags.json".

  python -m backend.offline.build_flags --catalogue .cache/catalogue            # all showcases
  python -m backend.offline.build_flags --catalogue .cache/catalogue --only barnard
"""
from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Callable
from pathlib import Path

from backend.adapters.cache import UpstreamError
from backend.jobs.preview import stamp_size_px
from backend.pipeline.colour import rgba_png
from backend.pipeline.flagmap import FLAGMAP_VERSION, bit_counts, category_grid, render_categories
from backend.pipeline.flagmap import summary as flag_summary
from backend.pipeline.render import preview_size_px
from backend.server.jobs import NoData
from contracts import FlipbookRequest, SkyPosition

DEFAULT_DIR = "frontend/public/showcases"


def preview_frames(events: list[dict]) -> list[tuple[int, str]]:
    """(slot, frame id) of every single catalogue frame in a showcase's events."""
    seen: dict[str, int] = {}
    for e in events:
        if e.get("type") == "frame" and e.get("stage") == "preview" \
                and e["label"].get("detector", 0) != 0:
            seen.setdefault(e["label"]["frame_id"], e["slot"])
    return [(slot, fid) for fid, slot in seen.items()]


async def build_one(entry: dict, deps_factory: Callable, root: Path,
                    log: Callable[[str], None] = print) -> str | None:
    key = entry["key"]
    folder = root / key
    events_path = root / entry["events"]
    if not events_path.is_file():
        log(f"{key}: no events file, skipped")
        return None
    wanted = preview_frames(json.loads(events_path.read_text())["events"])
    pos = SkyPosition(ra=float(entry["ra"]), dec=float(entry["dec"]))
    fov = float(entry["fov_arcmin"])
    req = FlipbookRequest(ra=pos.ra, dec=pos.dec, fov_arcmin=fov)
    try:
        deps = await deps_factory(req)
        frames = {f.frame_id: f for f in await deps.index.frames_at(pos)}
    except NoData as e:
        log(f"{key}: no data ({e})")
        return None
    size = stamp_size_px(req)
    (folder / "flags").mkdir(exist_ok=True)
    out: dict[str, dict] = {}
    for slot, fid in wanted:
        fr = frames.get(fid)
        if fr is None:
            log(f"{key}: frame {fid} is not in the catalogue any more, skipped")
            continue
        try:
            st = await deps.stamps.fetch(fr, pos, size // 2)
            cat, covered = await asyncio.to_thread(
                category_grid, st.flags, st, pos.ra, pos.dec, preview_size_px(fov), True)
        except (UpstreamError, ValueError) as e:
            log(f"{key}: frame {fid}: {e}")
            continue
        rgb, drawn = render_categories(cat)
        name = f"flags/{slot:02d}.png"
        (folder / name).write_bytes(rgba_png(rgb, drawn))
        out[fid] = {"png_url": name, "frame_id": fid, "width": cat.shape[1],
                    "height": cat.shape[0], "categories": flag_summary(cat, covered),
                    "bits": bit_counts(st.flags)}
    if not out:
        log(f"{key}: no flag layers made")
        return None
    (folder / "flags.json").write_text(json.dumps(
        {"version": FLAGMAP_VERSION, "frames": out}, separators=(",", ":")))
    log(f"{key}: {len(out)} of {len(wanted)} frames -> {folder / 'flags.json'}")
    return f"{key}/flags.json"


def mark_manifest(root: Path, done: dict[str, str]) -> None:
    path = root / "manifest.json"
    data = json.loads(path.read_text())
    for e in data["showcases"]:
        if e["key"] in done:
            e["flags"] = done[e["key"]]
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
