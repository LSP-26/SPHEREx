"""The URL is the state. Parameters go in the query string (EventSource is GET-only)."""
from __future__ import annotations

from urllib.parse import parse_qsl, urlencode

from .models import FlipbookRequest, Orientation, ViewMode

_SHORT = {"fov_arcmin": "fov", "detector": "det", "lambda_min_um": "lmin",
          "lambda_max_um": "lmax", "preview_cap": "cap", "use_zodi": "zodi"}
_LONG = {v: k for k, v in _SHORT.items()}


def to_query(req: FlipbookRequest) -> str:
    d = req.model_dump(mode="json", exclude_none=True)
    defaults = FlipbookRequest(ra=0, dec=0).model_dump(mode="json", exclude_none=True)
    out = {}
    for k, v in d.items():
        if k not in ("ra", "dec") and defaults.get(k) == v:
            continue
        if isinstance(v, bool):
            v = int(v)
        out[_SHORT.get(k, k)] = f"{v:.6f}".rstrip("0").rstrip(".") if isinstance(v, float) else v
    return urlencode(out)


def from_query(qs: str) -> FlipbookRequest:
    raw = {_LONG.get(k, k): v for k, v in parse_qsl(qs)}
    if "use_zodi" in raw:
        raw["use_zodi"] = raw["use_zodi"] in ("1", "true", "True")
    if "view" in raw:
        raw["view"] = ViewMode(raw["view"])
    if "fill" in raw:
        raw["fill"] = raw["fill"] in ("1", "true", "True")
    if "orient" in raw:
        raw["orient"] = Orientation(raw["orient"])
    return FlipbookRequest(**raw)
