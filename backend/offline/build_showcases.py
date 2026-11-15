"""Showcase builder: runs the LIVE preview job for each demo target and saves what it sent.

For each target it writes, under --out (default frontend/public/showcases, so Vite serves it in
development and copies it into the production build):

  <key>/events.json       every event the job sent, with frame URLs made relative
  <key>/frames/NN_stage.png   the rendered frames
  manifest.json           the list the page's gallery reads

Because the files are the live job's own output, the static replay and the live view cannot drift.
Rebuild after anything that changes the pictures (real wavelengths, regrid, new catalogue).

  python -m backend.offline.build_showcases                 # all targets
  python -m backend.offline.build_showcases --only barnard  # one target
"""
from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Callable
from pathlib import Path

from backend.jobs.preview import run_preview
from backend.offline.target_catalogue import TARGETS as POSITIONS
from backend.server.jobs import NoData
from backend.server.targets import target_list
from contracts import DoneEvent, ErrorEvent, FlipbookRequest, FrameEvent

DEFAULT_OUT = "frontend/public/showcases"
FOV_ARCMIN = {"wise0855": 10.0}     # it moves fast, so show more sky around it
DEFAULT_FOV = 5.0


def showcase_targets() -> list[dict]:
    """The five test targets at their survey-time positions, plus the control field."""
    rows = [t for t in target_list() if t["key"] != "wise0855_brief"]
    ra, dec = POSITIONS["control"]
    rows.append({"key": "control", "name": "Control field (nothing changes)", "ra": ra,
                 "dec": dec, "note": "A patch where nothing should change between visits."})
    return rows


async def build_one(target: dict, deps_factory: Callable, cache, out: Path,
                    fov: float = DEFAULT_FOV, log: Callable[[str], None] = print) -> dict | None:
    """Run the preview job for one target and save it. Returns its manifest entry, or None if
    the job produced no frames."""
    key = target["key"]
    req = FlipbookRequest(ra=target["ra"], dec=target["dec"], fov_arcmin=fov)
    try:
        deps = await deps_factory(req)
    except NoData as e:
        log(f"{key}: no data ({e})")
        return None
    folder = out / key
    (folder / "frames").mkdir(parents=True, exist_ok=True)
    events: list[dict] = []
    thumbs: dict[int, str] = {}
    n_frames, elapsed, error = 0, 0.0, None
    async for ev in run_preview(req, **deps.kwargs()):
        if isinstance(ev, FrameEvent):
            png = await cache.get(ev.png_url.rsplit("/", 1)[1].removesuffix(".png"))
            if png is None:
                continue
            name = f"frames/{ev.slot:02d}_{ev.stage.value}.png"
            (folder / name).write_bytes(png)
            thumbs[ev.slot] = name
            ev = ev.model_copy(update={"png_url": name})
        elif isinstance(ev, DoneEvent):
            n_frames, elapsed = ev.n_frames, ev.elapsed_s
        elif isinstance(ev, ErrorEvent):
            error = ev.message
        events.append(ev.model_dump(mode="json"))
    if not thumbs:
        log(f"{key}: no frames ({error or 'unknown reason'}), nothing saved")
        return None
    (folder / "events.json").write_text(json.dumps({"events": events}))
    log(f"{key}: {n_frames} frames in {elapsed:.1f} s -> {folder}")
    return {"key": key, "name": target["name"], "ra": target["ra"], "dec": target["dec"],
            "fov_arcmin": fov, "n_frames": n_frames, "elapsed_s": elapsed,
            "events": f"{key}/events.json", "thumb": thumbs[min(thumbs)],
            "note": target.get("note", "")}


def update_manifest(out: Path, entries: list[dict]) -> None:
    """Merge entries into manifest.json (so --only does not drop the other showcases)."""
    path = out / "manifest.json"
    old = json.loads(path.read_text())["showcases"] if path.exists() else []
    merged = {e["key"]: e for e in old}
    merged.update({e["key"]: e for e in entries})
    order = [t["key"] for t in showcase_targets()]
    items = sorted(merged.values(),
                   key=lambda e: order.index(e["key"]) if e["key"] in order else len(order))
    out.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"showcases": items}, indent=1))


async def run(args: argparse.Namespace) -> None:
    from backend.adapters.stubs import MemoryCache
    from backend.server.deps import CatalogueDeps
    from backend.server.settings import Settings

    cache = MemoryCache()
    deps = CatalogueDeps(Settings(source="catalogue", catalogue_dir=args.catalogue,
                                  cache_dir=None), cache)
    out, entries = Path(args.out), []
    try:
        for t in showcase_targets():
            if args.only and t["key"] not in args.only:
                continue
            entry = await build_one(t, deps, cache, out, FOV_ARCMIN.get(t["key"], args.fov))
            if entry:
                entries.append(entry)
    finally:
        await deps.aclose()
    update_manifest(out, entries)
    print(f"manifest: {len(entries)} showcase(s) updated in {out / 'manifest.json'}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--catalogue", default=".cache/catalogue_targets")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--only", nargs="+", help="target keys, e.g. barnard r_dra control")
    ap.add_argument("--fov", type=float, default=DEFAULT_FOV, help="field of view, arcmin")
    asyncio.run(run(ap.parse_args(argv)))


if __name__ == "__main__":
    main()
