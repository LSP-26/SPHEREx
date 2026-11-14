"""Cache keys: sha256 of frame, release, version, position, size and parameters."""
from __future__ import annotations

import hashlib
import json
from typing import Any


def cache_key(
    *,
    frame_id: str,
    release: str,
    version: str,
    ra: float,
    dec: float,
    size_px: int,
    params: dict[str, Any] | None = None,
    kind: str = "stamp",
) -> str:
    """Stable across processes. Position is rounded to 1e-6 deg (~4 mas) so float noise
    cannot split the cache. Release and version are always part of the key."""
    payload = {
        "kind": kind,
        "frame_id": frame_id,
        "release": release,
        "version": version,
        "ra": round(float(ra), 6),
        "dec": round(float(dec), 6),
        "size_px": int(size_px),
        "params": params or {},
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode()).hexdigest()
