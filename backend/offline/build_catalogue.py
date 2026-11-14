"""D1.1: build the frame catalogue as Parquet shards (one per time window).

TAP gives uri and time bounds. One header pass per frame (range reads, never the full file)
gives detector, obsid, WCS and the four plane offsets. Rerun the same command to resume:
finished shards are skipped. Frames whose header pass fails are listed in <shard>.failed.json.

  python -m backend.offline.build_catalogue --out .cache/catalogue
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import tempfile
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pyarrow as pa
import pyarrow.parquet as pq

from backend.adapters.fits_layout import HttpRangeSource
from backend.adapters.frame_layout import read_frame_layout, spec_to_list
from backend.adapters.wcs_util import _ALT, celestial_wcs

TAP = "https://irsa.ipac.caltech.edu/TAP/sync"
BUCKET = "nasa-irsa-spherex"
S3_HTTPS = f"https://{BUCKET}.s3.us-east-1.amazonaws.com/"
PLANES = ("IMAGE", "FLAGS", "VARIANCE", "ZODI")
WCS_PREFIXES = ("CTYPE", "CRPIX", "CRVAL", "CD", "PC", "CDELT", "CUNIT", "A_", "B_", "AP_",
                "BP_", "LONPOLE", "LATPOLE", "RADESYS", "EQUINOX", "NAXIS")
# Column names p.time_bounds_lower/upper come from the spxquery package; confirm on first run.
ADQL = ("SELECT a.uri AS uri, p.time_bounds_lower AS t0, p.time_bounds_upper AS t1 "
        "FROM spherex.artifact a JOIN spherex.plane p ON a.planeid = p.planeid "
        "WHERE a.uri LIKE '%level2%' "
        "AND p.time_bounds_lower >= {lo} AND p.time_bounds_lower < {hi}")

SCHEMA = pa.schema([
    ("frame_id", pa.string()), ("detector", pa.int32()), ("obsid", pa.string()),
    ("t_start", pa.float64()), ("t_end", pa.float64()),
    ("release", pa.string()), ("version", pa.string()), ("uri", pa.string()),
    ("ra_c", pa.float64()), ("dec_c", pa.float64()),   # frame centre, for a cone prefilter
    ("naxis1", pa.int32()), ("naxis2", pa.int32()),
    ("wcs_json", pa.string()),      # celestial WCS keys only (alternate sets dropped)
    ("planes_json", pa.string()),   # {"IMAGE": [offset, bitpix, bzero, bscale], ...}
])


# ------------------------------------------------------------------ parsing
def key_from_uri(uri: str) -> str:
    return uri.strip().removeprefix("ibe/data/spherex/")


def parse_release(key: str) -> str:
    m = re.search(r"(?:^|/)(qr\d+)/", key, re.I)
    return m.group(1).upper() if m else "UNKNOWN"


def parse_version(name: str) -> str:
    m = re.search(r"-(v\d+(?:-\d+)*)\.fits$", name)
    return m.group(1) if m else "unknown"


def parse_detector(name: str) -> int | None:
    m = re.search(r"_\dD(\d)_", name)
    return int(m.group(1)) if m else None


# ------------------------------------------------------------------ header pass
def _retry(fn: Callable, attempts: int = 3):
    """Retry on network trouble. A ValueError means the file itself is in a format we cannot
    read, so retrying cannot help and it is raised at once."""
    for i in range(attempts):
        try:
            return fn()
        except ValueError:
            raise
        except Exception:
            if i == attempts - 1:
                raise
            time.sleep(1.0 * (i + 1))


def read_frame_record(row: dict) -> dict:
    """One frame: walk its headers (4 range requests) and return a catalogue row."""
    key = key_from_uri(row["uri"])
    name = key.rsplit("/", 1)[-1]
    src = HttpRangeSource(S3_HTTPS + key)
    layout = _retry(lambda: read_frame_layout(src))
    hdr = layout.image_header
    ny, nx = layout.shape
    wcs = {k: hdr[k] for k in hdr
           if k.startswith(WCS_PREFIXES) and not _ALT.match(k)
           and isinstance(hdr[k], (str, int, float)) and not isinstance(hdr[k], bool)}
    ra, dec = (float(v) for v in celestial_wcs(wcs).all_pix2world(
        [[(nx - 1) / 2, (ny - 1) / 2]], 0)[0])
    det = None
    if "DETECTOR" in hdr:
        m = re.search(r"\d", str(hdr["DETECTOR"]))
        det = int(m.group()) if m else None
    if det is None:
        det = parse_detector(name)
    if det is None:
        raise ValueError(f"{name}: cannot work out the detector")
    return {
        "frame_id": name, "detector": det, "obsid": str(hdr.get("OBSID", "")) or None,
        "t_start": float(row["t0"]), "t_end": float(row["t1"]),
        "release": parse_release(key), "version": parse_version(name),
        "uri": f"s3://{BUCKET}/{key}", "ra_c": ra % 360, "dec_c": dec,
        "naxis1": nx, "naxis2": ny,
        "wcs_json": json.dumps(wcs),
        "planes_json": json.dumps({p.value: spec_to_list(s) for p, s in layout.planes.items()}),
    }


# ------------------------------------------------------------------ shards
def tap_rows(lo: float, hi: float) -> list[dict]:
    r = httpx.post(TAP, data={"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "csv",
                              "QUERY": ADQL.format(lo=lo, hi=hi)}, timeout=300.0)
    if r.status_code != 200:
        raise RuntimeError(f"TAP HTTP {r.status_code}: {r.text[:500]}")
    rows = list(csv.DictReader(io.StringIO(r.text)))
    return [x for x in rows if x["uri"].strip().endswith(".fits")]


def _atomic_write(path: Path, write: Callable[[Path], None]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    os.close(fd)
    try:
        write(Path(tmp))
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def build_shard(path: Path, rows: list[dict], reader: Callable[[dict], dict] = read_frame_record,
                workers: int = 16) -> tuple[int, int] | None:
    """Write one shard. Returns (ok, failed), or None if the shard already exists."""
    if path.exists():
        return None
    out, failed = [], []

    def one(row: dict):
        try:
            return reader(row)
        except Exception as e:
            failed.append({"uri": row["uri"], "error": f"{type(e).__name__}: {e}"})
            return None

    with ThreadPoolExecutor(max_workers=workers) as ex:
        out = [r for r in ex.map(one, rows) if r is not None]
    out.sort(key=lambda r: (r["t_start"], r["detector"]))
    table = pa.Table.from_pylist(out, schema=SCHEMA)
    if failed:
        path.with_suffix(".failed.json").write_text(json.dumps(failed, indent=1))
    _atomic_write(path, lambda p: pq.write_table(table, p))
    return len(out), len(failed)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=".cache/catalogue")
    ap.add_argument("--start-mjd", type=float, default=60700.0)
    ap.add_argument("--end-mjd", type=float, default=61400.0)
    ap.add_argument("--window-days", type=float, default=14.0)
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--limit", type=int, default=None,
                    help="max frames per shard; for trial runs only, use a throwaway --out")
    a = ap.parse_args(argv)
    out = Path(a.out)
    lo = a.start_mjd
    while lo < a.end_mjd:
        hi = min(lo + a.window_days, a.end_mjd)
        path = out / f"shard_{lo:.1f}_{hi:.1f}.parquet"
        if path.exists():
            print(f"{path.name}: done already, skipping")
        else:
            t = time.monotonic()
            rows = tap_rows(lo, hi)
            if a.limit:
                rows = rows[:a.limit]
            res = build_shard(path, rows, workers=a.workers)
            ok, bad = res if res else (0, 0)
            print(f"{path.name}: {len(rows)} frames from TAP, {ok} ok, {bad} failed, "
                  f"{time.monotonic() - t:.0f} s")
        lo = hi


if __name__ == "__main__":
    main()
