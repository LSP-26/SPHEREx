"""X4: how many simultaneous S3 range requests pay off?

Usage: python scripts/x4_concurrency.py
Reads a 47-row band of IMAGE, FLAGS, VARIANCE and ZODI (4 requests, 1.53 MB per frame) for
24 frames at each concurrency level, and prints wall time, requests per second and latency.
From a home connection this is only a rough guide. Run it again from us-east-1 (I.1).
"""
import asyncio
import random
import statistics
import time
import urllib.parse
import urllib.request

import httpx

TAP = "https://irsa.ipac.caltech.edu/TAP/sync"
S3 = "https://nasa-irsa-spherex.s3.us-east-1.amazonaws.com/"
# plane data offsets, measured in X2/X0 (identical on all 3 frames tested)
OFFSETS = {"IMAGE": 23_040, "FLAGS": 16_683_840, "VARIANCE": 33_341_760, "ZODI": 49_999_680}
ROW_BYTES = 2040 * 4
ROWS = 47
LEVELS = [4, 8, 16, 32, 64]
N_FRAMES = 24


def pick_urls(n: int) -> list[str]:
    query = ("SELECT TOP %d a.uri FROM spherex.artifact a "
             "WHERE a.uri LIKE '%%level2%%'" % n)
    q = urllib.parse.urlencode({"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "csv",
                                "QUERY": query})
    with urllib.request.urlopen(TAP, q.encode(), timeout=120) as r:
        lines = r.read().decode().strip().splitlines()[1:]
    return [S3 + ln.strip().strip('"').removeprefix("ibe/data/spherex/") for ln in lines]


async def one(client, sem, url, plane, y0, lat, errors):
    start = OFFSETS[plane] + y0 * ROW_BYTES
    length = ROWS * ROW_BYTES
    async with sem:
        t = time.monotonic()
        try:
            r = await client.get(url, headers={"Range": f"bytes={start}-{start + length - 1}"})
            if r.status_code != 206 or len(r.content) != length:
                errors.append(f"HTTP {r.status_code}, {len(r.content)} bytes")
                return
        except httpx.HTTPError as e:
            errors.append(type(e).__name__)
            return
        lat.append(time.monotonic() - t)


async def run_level(urls, conc):
    sem = asyncio.Semaphore(conc)
    lat, errors = [], []
    limits = httpx.Limits(max_connections=conc, max_keepalive_connections=conc)
    async with httpx.AsyncClient(limits=limits, timeout=60.0, http2=False) as client:
        t = time.monotonic()
        await asyncio.gather(*[
            one(client, sem, u, p, random.randint(50, 1900), lat, errors)
            for u in urls for p in OFFSETS])
        wall = time.monotonic() - t
    return wall, lat, errors


async def main() -> None:
    print("asking IRSA TAP for frame URLs (can take ~17 s) ...")
    urls = pick_urls(N_FRAMES)
    print(f"got {len(urls)} frames; {len(urls) * len(OFFSETS)} requests per level\n")
    print(f"{'conc':>5} {'wall s':>7} {'req/s':>7} {'MB/s':>6} {'p50 s':>6} {'p95 s':>6} "
          f"{'frames/s':>8} {'errors':>6}")
    for conc in LEVELS:
        wall, lat, errors = await run_level(urls, conc)
        n = len(lat)
        if n == 0:
            print(f"{conc:>5}  all requests failed: {errors[:3]}")
            continue
        p50 = statistics.median(lat)
        p95 = sorted(lat)[max(0, int(0.95 * n) - 1)]
        mb = n * ROWS * ROW_BYTES / 1e6
        print(f"{conc:>5} {wall:>7.2f} {n / wall:>7.1f} {mb / wall:>6.1f} {p50:>6.2f} "
              f"{p95:>6.2f} {len(urls) / wall:>8.2f} {len(errors):>6}")
        if errors:
            print("      first errors:", errors[:3])


if __name__ == "__main__":
    asyncio.run(main())
