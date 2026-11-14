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

from backend.adapters.wcs_util import celestial_wcs
from contracts import FrameRef, Plane, PlaneSpec, SkyPosition

CONE_DEG = 2.6   # frame half-diagonal is 2.46 degrees (2040 px x 6.15 arcsec, corner to centre)

COLUMNS = ("frame_id", "detector", "obsid", "t_start", "t_end", "release", "version", "uri",
           "naxis1", "naxis2", "wcs_json", "planes_json")

_SQL = f"""
SELECT {", ".join(COLUMNS)} FROM frames
WHERE dec_c BETWEEN ? AND ?
  AND 2 * asin(sqrt(least(1.0,
        pow(sin(radians(dec_c - ?) / 2), 2)
        + cos(radians(dec_c)) * cos(radians(?)) * pow(sin(radians(ra_c - ?) / 2), 2))))
      < radians(?)
"""


class CatalogueFrameIndex:
    """Implements contracts.interfaces.FrameIndex over the Parquet shards in `root`.
    Loads every shard into memory once, at start-up."""

    def __init__(self, root: str | Path, cone_deg: float = CONE_DEG):
        files = sorted(Path(root).glob("*.parquet"))
        if not files:
            raise FileNotFoundError(f"no catalogue shards (*.parquet) in {root}")
        self.cone_deg = cone_deg
        self._lock = threading.Lock()
        self._con = duckdb.connect()
        self._con.execute("CREATE TABLE frames AS SELECT * FROM read_parquet(?)",
                          [[str(f) for f in files]])

    @property
    def n_frames(self) -> int:
        with self._lock:
            return self._con.execute("SELECT count(*) FROM frames").fetchone()[0]

    def _frames_at(self, pos: SkyPosition, margin_px: int) -> list[FrameRef]:
        lo = max(-90.0, pos.dec - self.cone_deg)
        hi = min(90.0, pos.dec + self.cone_deg)
        with self._lock:
            rows = self._con.execute(
                _SQL, [lo, hi, pos.dec, pos.dec, pos.ra, self.cone_deg]).fetchall()
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
            planes = {Plane(k): PlaneSpec(offset=int(v[0]), bitpix=int(v[1]),
                                          bzero=float(v[2]), bscale=float(v[3]))
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
