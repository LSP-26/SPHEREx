"""X5: does reusing HTTP connections speed up the catalogue header pass?

  python scripts/bench_header_walk.py [N_FRAMES]        (default 200 frames per run)

Compares two ways of reading a frame's layout (4 range requests each):
  urllib  : a new TCP + TLS connection for every request (what the builder does today)
  pooled  : one shared connection pool, kept alive (backend/adapters/pooled_source.py)
at 16, 32 and 64 worker threads. First it times single requests, to show what a new connection
costs by itself. Then two rounds over different random frames from the target catalogue, with
the order of configurations reversed in round 2 so slow drift does not favour one side. The
pooled runs get a warm-up that opens their connections first, because a real catalogue build
runs for hours and the set-up is paid once.

From a home connection this is a rough guide. Run it again from us-east-1 (I.1).
"""
from __future__ import annotations

import statistics
import sys
import time
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

import duckdb

from backend.adapters.fits_layout import HttpRangeSource
from backend.adapters.frame_layout import read_frame_layout
from backend.adapters.pooled_source import PooledRangeSource, make_client
from backend.adapters.s3_stamps import https_url

GLOB = ".cache/catalogue_targets/*.parquet"
WORKERS = [16, 32, 64]


def sample_uris(n: int, seed: int) -> list[str]:
    return [r[0] for r in duckdb.sql(
        f"SELECT uri FROM read_parquet('{GLOB}') USING SAMPLE reservoir({n} ROWS) "
        f"REPEATABLE ({seed})").fetchall()]


def connection_cost(url: str, n: int = 20) -> tuple[float, float]:
    """Median ms for one tiny range request: a new connection each time, then a reused one."""
    fresh, reused = [], []
    for _ in range(n):
        t = time.perf_counter()
        HttpRangeSource(url).read(0, 1)
        fresh.append(time.perf_counter() - t)
    with make_client(2) as client:
        src = PooledRangeSource(url, client)
        src.read(0, 1)                                   # opens the connection
        for _ in range(n):
            t = time.perf_counter()
            src.read(0, 1)
            reused.append(time.perf_counter() - t)
    return 1000 * statistics.median(fresh), 1000 * statistics.median(reused)


def run(kind: str, workers: int, uris: list[str],
        url_for: Callable[[str], str] = https_url) -> dict:
    latencies: list[float] = []
    errors: Counter = Counter()
    client = make_client(workers) if kind == "pooled" else None
    warm = 0.0
    if client is not None:      # open `workers` connections at the same time, before timing
        t = time.perf_counter()
        with ThreadPoolExecutor(workers) as pool:
            list(pool.map(lambda _: PooledRangeSource(url_for(uris[0]), client).read(0, 1),
                          range(workers)))
        warm = time.perf_counter() - t

    def one(uri: str) -> None:
        url = url_for(uri)
        src = PooledRangeSource(url, client) if client is not None else HttpRangeSource(url)
        t = time.perf_counter()
        try:
            read_frame_layout(src)
        except Exception as e:
            errors[type(e).__name__] += 1
            return
        latencies.append(time.perf_counter() - t)

    t0 = time.perf_counter()
    with ThreadPoolExecutor(workers) as pool:
        list(pool.map(one, uris))
    wall = time.perf_counter() - t0
    if client is not None:
        client.close()
    lat = sorted(latencies) or [float("nan")]
    return {"kind": kind, "workers": workers, "wall": wall, "n": len(latencies),
            "rate": len(latencies) / wall, "p50": 1000 * statistics.median(lat),
            "p95": 1000 * lat[max(0, int(0.95 * len(lat)) - 1)], "errors": sum(errors.values()),
            "warm": warm, "error_types": dict(errors)}


def show(r: dict) -> None:
    print(f"{r['kind']:>7} {r['workers']:>4} {r['wall']:>7.1f} {r['rate']:>9.1f} "
          f"{r['p50']:>8.0f} {r['p95']:>8.0f} {r['errors']:>6}"
          + (f"   (warm-up {r['warm']:.2f} s)" if r["kind"] == "pooled" else "")
          + (f"  {r['error_types']}" if r["errors"] else ""))


def main(n: int) -> None:
    probe = https_url(sample_uris(1, 0)[0])
    fresh, reused = connection_cost(probe)
    print(f"one tiny request: new connection {fresh:.0f} ms (median), reused connection "
          f"{reused:.0f} ms\n")
    print(f"{'reader':>7} {'thr':>4} {'wall s':>7} {'frames/s':>9} {'p50 ms':>8} {'p95 ms':>8} "
          f"{'errors':>6}")
    configs = [(k, w) for w in WORKERS for k in ("urllib", "pooled")]
    results: list[dict] = []
    for rnd, order in enumerate((configs, configs[::-1]), start=1):
        uris = sample_uris(n, rnd)
        print(f"-- round {rnd}: {len(uris)} frames")
        for kind, workers in order:
            r = run(kind, workers, uris)
            show(r)
            results.append(r)
    print("\nmean over both rounds:")
    for w in WORKERS:
        rate = {k: statistics.mean(r["rate"] for r in results
                                   if r["kind"] == k and r["workers"] == w)
                for k in ("urllib", "pooled")}
        print(f"  {w:>3} threads: urllib {rate['urllib']:.1f} frames/s, "
              f"pooled {rate['pooled']:.1f} frames/s  ({rate['pooled'] / rate['urllib']:.1f}x)")
    best = max((r for r in results if r["kind"] == "pooled" and not r["errors"]),
               key=lambda r: r["rate"], default=None)
    if best:
        print(f"\n1.7M frames at the best pooled rate ({best['rate']:.0f} frames/s, "
              f"{best['workers']} threads): {1.7e6 / best['rate'] / 3600:.1f} hours "
              "(this machine)")
    print("errors anywhere above mean that setting is too aggressive, ignore its speed")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 200)
