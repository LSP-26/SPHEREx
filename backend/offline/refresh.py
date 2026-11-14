"""D8: release refresh. Extends the catalogue with shards for new observations.

Finds the end of the newest shard, then builds shards up to (now minus LAG_DAYS), because TAP
lags ingestion by about a day and a shard is never rebuilt once written. New releases and
pipeline versions need no special handling: the builder parses release and version from each
file name. Run it weekly (cron), then restart the API so CatalogueFrameIndex reloads.

  python -m backend.offline.refresh --out .cache/catalogue
"""
from __future__ import annotations

import argparse
import math
import re
import time
from collections.abc import Callable
from pathlib import Path

from backend.offline.build_catalogue import (
    DEFAULT_WORKERS,
    build_shard,
    read_frame_record,
    tap_rows,
)

LAG_DAYS = 2.0
MIN_WINDOW_DAYS = 1.0
DEFAULT_START_MJD = 60700.0
_UNIX_EPOCH_MJD = 40587.0
_SHARD = re.compile(r"^shard_(\d+\.\d)_(\d+\.\d)\.parquet$")


def now_mjd() -> float:
    return time.time() / 86400.0 + _UNIX_EPOCH_MJD


def shard_windows(root: str | Path) -> list[tuple[float, float]]:
    """(start, end) MJD of every shard in root, read from the file names, sorted."""
    found = [(float(m[1]), float(m[2])) for p in Path(root).glob("shard_*.parquet")
             if (m := _SHARD.match(p.name))]
    return sorted(found)


def refresh(out: str | Path, *, now: float | None = None, window_days: float = 14.0,
            workers: int = DEFAULT_WORKERS, default_start: float = DEFAULT_START_MJD,
            limit: int | None = None, max_windows: int | None = None, dry_run: bool = False,
            rows_fn: Callable = tap_rows, reader: Callable = read_frame_record,
            log: Callable[[str], None] = print) -> list[Path]:
    """Build the missing shards. Returns their paths (the planned ones, if dry_run)."""
    out = Path(out)
    windows = shard_windows(out)
    for (_, hi), (lo, _) in zip(windows, windows[1:], strict=False):
        if lo - hi > 0.15:
            log(f"WARNING: gap in the catalogue between MJD {hi:.1f} and {lo:.1f}")
    lo = max(hi for _, hi in windows) if windows else default_start
    # floor to 0.1 d so shard names match the window exactly (no rounding gaps between shards)
    cutoff = math.floor(((now_mjd() if now is None else now) - LAG_DAYS) * 10) / 10
    built: list[Path] = []
    while (cutoff - lo >= MIN_WINDOW_DAYS - 1e-9
           and (max_windows is None or len(built) < max_windows)):
        hi = min(round(lo + window_days, 1), cutoff)
        path = out / f"shard_{lo:.1f}_{hi:.1f}.parquet"
        if dry_run:
            log(f"would build {path.name}")
        else:
            t = time.monotonic()
            rows = rows_fn(lo, hi)
            n_tap = len(rows)
            if limit:
                rows = rows[:limit]
            res = build_shard(path, rows, reader=reader, workers=workers)
            ok, bad = res if res else (0, 0)
            log(f"{path.name}: {n_tap} frames from TAP, {ok} ok, {bad} failed, "
                f"{time.monotonic() - t:.0f} s")
        built.append(path)
        lo = hi
    if not built:
        log("catalogue is up to date")
    return built


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=".cache/catalogue")
    ap.add_argument("--window-days", type=float, default=14.0)
    ap.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    ap.add_argument("--dry-run", action="store_true", help="list the shards it would build")
    ap.add_argument("--max-windows", type=int, default=None, help="stop after this many shards")
    ap.add_argument("--limit", type=int, default=None,
                    help="max frames per shard; trial runs only, use a throwaway --out")
    a = ap.parse_args(argv)
    refresh(a.out, window_days=a.window_days, workers=a.workers, dry_run=a.dry_run,
            max_windows=a.max_windows, limit=a.limit)


if __name__ == "__main__":
    main()
