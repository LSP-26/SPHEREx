"""INTERIM, NOT THE FINAL PLAN.

The final plan is the full frame catalogue (a lookup table of every SPHEREx frame): D1.1
builds it, D1.2 runs it once from us-east-1, D8 keeps it current. Until that exists, this
builds a tiny catalogue for the demo positions only: one positional TAP query per target
("which frames cover this spot?"), then the same header pass as D1.1. The shards use the same
schema, so CatalogueFrameIndex reads them unchanged.

Positional queries are slow enough that IRSA's gateway cuts a synchronous request off with
HTTP 504, so they run as asynchronous TAP jobs (submit, poll, fetch).

Rules:
- Output goes to .cache/catalogue_targets as targets_<name>.parquet, never shard_*.parquet.
- Delete that folder once the full catalogue exists. Never mix the two in one folder
  (frames would be duplicated) and never point refresh.py at it.

  python -m backend.offline.target_catalogue                   # all six targets
  python -m backend.offline.target_catalogue --only barnard    # one target
"""
from __future__ import annotations

import argparse
import csv
import io
import re
import time
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urljoin

import httpx

from backend.offline.build_catalogue import TAP, build_shard, read_frame_record

TAP_ASYNC = TAP.removesuffix("/sync") + "/async"

# Approximate positions (degrees, ICRS), from memory. The X3 recon confirms or replaces
# them. Epsilon Indi B is roughly 7 arcmin from the primary [verify], so X3 should supply its
# own position. The control is only a placeholder patch away from the others.
TARGETS: dict[str, tuple[float, float]] = {
    "barnard": (269.452, 4.693),
    "eps_indi": (330.840, -56.786),
    "wise0855": (133.7951, -7.2452),
    "rw_aur": (76.957, 30.401),
    "r_dra": (248.172, 66.755),
    "control": (161.25, 58.0),
}

# Footprint column names to try, in order. Not confirmed against IRSA's live schema.
REGION_COLUMNS = ("poly", "s_region", "position_bounds")
ADQL = ("SELECT a.uri AS uri, p.time_bounds_lower AS t0, p.time_bounds_upper AS t1 "
        "FROM spherex.artifact a JOIN spherex.plane p ON a.planeid = p.planeid "
        "WHERE a.uri LIKE '%level2%' "
        "AND CONTAINS(POINT('ICRS', {ra}, {dec}), p.{col}) = 1")


def run_async_query(query: str, client=None, *, poll_s: float = 2.0, max_wait_s: float = 900.0,
                    sleep: Callable[[float], None] = time.sleep) -> SimpleNamespace:
    """Run an ADQL query as a TAP async job. Returns an object with status_code and text, like
    a synchronous response, so callers do not care which was used. A job that ends in ERROR
    comes back as status 400 with IRSA's message (usually a bad column name)."""
    own = client is None
    client = client or httpx.Client(timeout=60.0)
    try:
        r = client.post(TAP_ASYNC, data={"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "csv",
                                         "QUERY": query, "PHASE": "RUN"})
        if r.status_code >= 400:
            return SimpleNamespace(status_code=r.status_code, text=r.text)
        job = r.headers.get("Location")
        if not job:
            m = re.search(r"jobId>\s*([^<\s]+)", r.text)
            if not m:
                raise RuntimeError("IRSA did not return a job id: " + r.text[:300])
            job = f"{TAP_ASYNC}/{m[1]}"
        job = urljoin(TAP_ASYNC, job)
        waited = 0.0
        while True:
            pr = client.get(job + "/phase")
            phase = "EXECUTING" if pr.status_code >= 500 else pr.text.strip()
            if phase == "COMPLETED":
                break
            if phase == "ERROR":
                return SimpleNamespace(status_code=400, text=client.get(job + "/error").text[:500])
            if phase == "ABORTED":
                return SimpleNamespace(status_code=500, text="IRSA aborted the query job")
            if waited >= max_wait_s:
                raise RuntimeError(f"IRSA query still '{phase}' after {max_wait_s:.0f} s")
            sleep(poll_s)
            waited += poll_s
        res = client.get(job + "/results/result")
        return SimpleNamespace(status_code=res.status_code, text=res.text)
    finally:
        if own:
            client.close()


def positional_rows(ra: float, dec: float, post: Callable = run_async_query,
                    sleep: Callable[[float], None] = time.sleep) -> list[dict]:
    """Every level-2 frame whose footprint contains (ra, dec). The exact edge test happens
    later, in CatalogueFrameIndex, using each frame's own WCS."""
    errors = []
    for col in REGION_COLUMNS:
        for attempt in range(3):
            r = post(ADQL.format(col=col, ra=ra, dec=dec))
            if r.status_code < 500 or attempt == 2:
                break
            sleep(10 * (attempt + 1))
        if r.status_code == 200:
            rows = list(csv.DictReader(io.StringIO(r.text)))
            return [x for x in rows if x["uri"].strip().endswith(".fits")]
        if r.status_code >= 500:   # IRSA is struggling; another column name will not help
            raise RuntimeError(f"IRSA TAP failed with HTTP {r.status_code} "
                               f"(not a column problem): {r.text[:200]}")
        errors.append(f"{col}: HTTP {r.status_code}: {r.text[:200]}")
    raise RuntimeError("no footprint column worked:\n" + "\n".join(errors))


def build_all(out: str | Path, targets: dict[str, tuple[float, float]] = TARGETS, *,
              only: list[str] | None = None, limit: int | None = None, workers: int = 16,
              rows_fn: Callable = positional_rows, reader: Callable = read_frame_record,
              log: Callable[[str], None] = print) -> list[Path]:
    """One shard per target. Finished shards are skipped, so a rerun resumes."""
    out = Path(out)
    built: list[Path] = []
    for name, (ra, dec) in targets.items():
        if only and name not in only:
            continue
        path = out / f"targets_{name}.parquet"
        if path.exists():
            log(f"{path.name}: done already, skipping")
            continue
        t = time.monotonic()
        rows = rows_fn(ra, dec)
        n_tap = len(rows)
        if n_tap == 0:
            log(f"{name}: TAP found no frames, nothing written")
            continue
        if limit:
            rows = rows[:limit]
        res = build_shard(path, rows, reader=reader, workers=workers)
        ok, bad = res if res else (0, 0)
        log(f"{path.name}: {n_tap} frames from TAP, {ok} ok, {bad} failed, "
            f"{time.monotonic() - t:.0f} s")
        built.append(path)
    return built


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="INTERIM: tiny catalogue for the demo targets")
    ap.add_argument("--out", default=".cache/catalogue_targets")
    ap.add_argument("--only", nargs="+", choices=sorted(TARGETS))
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--limit", type=int, default=None,
                    help="max frames per target; trial runs only, use a throwaway --out")
    a = ap.parse_args(argv)
    build_all(a.out, only=a.only, limit=a.limit, workers=a.workers)


if __name__ == "__main__":
    main()
