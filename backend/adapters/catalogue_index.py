"""D2.2: FrameIndex backed by the catalogue shards (D1.1). Replaces the ~17 s TAP lookup.

Two steps per query:
1. a cheap cone cut in DuckDB on each frame's centre (a frame is 3.5 degrees across, so its
   corners are at most about 2.5 degrees from the centre);
2. the exact test: apply each candidate's own WCS (SIP included) to the target and keep frames
   where it lands at least margin_px inside every edge.
The result is sorted by t_start.
"""
from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path

import duckdb
import numpy as np

from backend.adapters.frame_layout import spec_from_list
from backend.adapters.wcs_util import celestial_wcs
from contracts import FrameRef, Plane, SkyPosition

CONE_DEG = 2.6   # frame half-diagonal is 2.46 degrees (2040 px x 6.15 arcsec, corner to centre)

COLUMNS = ("frame_id", "detector", "obsid", "t_start", "t_end", "release", "version", "uri",
           "naxis1", "naxis2", "wcs_json", "planes_json")

_HEAVY = ", ".join(COLUMNS)

# the cone cut runs on a small in-memory table (position, time, which shard and row); the big
# text columns (the WCS is about 1.6 kB per frame, 3 GB for 1.6 million frames) stay in the
# Parquet files and are read only for the few hundred frames near the target
_SQL = """
SELECT shard, row FROM frames
WHERE dec_c BETWEEN ? AND ?
  AND 2 * asin(sqrt(least(1.0,
        pow(sin(radians(dec_c - ?) / 2), 2)
        + cos(radians(dec_c)) * cos(radians(?)) * pow(sin(radians(ra_c - ?) / 2), 2))))
      < radians(?)
"""


class CatalogueFrameIndex:
    """Implements contracts.interfaces.FrameIndex over the Parquet shards in `root`.
    Keeps only a small table of frame centres in memory (about 0.4 GB for 1.6 million frames,
    against 5.7 GB when every column was loaded); a query reads the full rows of its candidates
    from the shard files."""

    def __init__(self, root: str | Path, cone_deg: float = CONE_DEG):
        self.files = [str(f) for f in sorted(Path(root).glob("*.parquet"))]
        if not self.files:
            raise FileNotFoundError(f"no catalogue shards (*.parquet) in {root}")
        self.cone_deg = cone_deg
        self._lock = threading.Lock()
        self._con = duckdb.connect()
        self._con.execute("CREATE TABLE frames (ra_c DOUBLE, dec_c DOUBLE, t_start DOUBLE, "
                          "shard USMALLINT, row UBIGINT)")
        for i, f in enumerate(self.files):
            self._con.execute(
                "INSERT INTO frames SELECT ra_c, dec_c, t_start, ?, file_row_number "
                "FROM read_parquet(?, file_row_number = true)", [i, f])

    @property
    def n_frames(self) -> int:
        with self._lock:
            return self._con.execute("SELECT count(*) FROM frames").fetchone()[0]

    def _rows(self, pos: SkyPosition, lo: float, hi: float) -> list[tuple]:
        with self._lock:
            cands = self._con.execute(
                _SQL, [lo, hi, pos.dec, pos.dec, pos.ra, self.cone_deg]).fetchall()
        by_shard: dict[int, list[int]] = {}
        for shard, row in cands:
            by_shard.setdefault(shard, []).append(row)
        rows: list[tuple] = []
        cur = self._con.cursor()        # own connection: reading the files needs no lock
        try:
            for shard, rs in by_shard.items():
                rows += cur.execute(
                    f"SELECT {_HEAVY} FROM read_parquet(?, file_row_number = true) "
                    "WHERE file_row_number IN (SELECT unnest(?))",
                    [self.files[shard], rs]).fetchall()
        finally:
            cur.close()
        return rows

    def _frames_at(self, pos: SkyPosition, margin_px: int) -> list[FrameRef]:
        lo = max(-90.0, pos.dec - self.cone_deg)
        hi = min(90.0, pos.dec + self.cone_deg)
        rows = self._rows(pos, lo, hi)
        out: list[FrameRef] = []
        for r in rows:
            rec = dict(zip(COLUMNS, r, strict=True))
            wcs_header = json.loads(rec["wcs_json"])
            try:
                x, y = celestial_wcs(wcs_header).all_world2pix(
                    [[pos.ra, pos.dec]], 0, quiet=True)[0]
            except Exception:
                continue    # a frame whose WCS will not solve cannot be used
            if not (np.isfinite(x) and np.isfinite(y)):
                continue
            nx, ny = rec["naxis1"], rec["naxis2"]
            if not (margin_px <= x <= nx - margin_px and margin_px <= y <= ny - margin_px):
                continue
            planes = {Plane(k): spec_from_list(v)
                      for k, v in json.loads(rec["planes_json"]).items()}
            out.append(FrameRef(
                frame_id=rec["frame_id"], detector=rec["detector"], obsid=rec["obsid"],
                t_start=rec["t_start"], t_end=rec["t_end"], release=rec["release"],
                version=rec["version"], uri=rec["uri"], wcs_header=wcs_header,
                planes=planes, naxis1=nx, naxis2=ny))
        out.sort(key=lambda f: f.t_start)
        return out

    async def frames_at(self, pos: SkyPosition, margin_px: int = 30) -> list[FrameRef]:
        return await asyncio.to_thread(self._frames_at, pos, margin_px)
