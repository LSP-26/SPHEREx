"""Known asteroids and comets in a field at a moment, from IMCCE's SkyBoT cone search (server side;
the page has its own browser-side client in frontend/src/lib/skybot.ts).

Positions are geocentric (observer code 500). SPHEREx is in low Earth orbit, about 700 km up: for a
main-belt asteroid (2 AU) that shifts the position by under 1 arcsec, a sixth of a pixel; for a
near-Earth object passing close it can be much more, so a near-Earth match may be missed.
Answer checked 2026-11-08: a JSON list with "Name", "RA (hms)", "DEC (dms)", "VMag (mag)",
"dRA (arcsec/h)", "dDEC (arcsec/h)", "Class".
"""

from __future__ import annotations

import asyncio

import httpx

from backend.adapters.cache import UpstreamError
from contracts import ErrorCode

ENDPOINT = "https://ssp.imcce.fr/webservices/skybot/api/conesearch.php"
MJD_TO_JD = 2400000.5


def _sexagesimal(v) -> float | None:
    if isinstance(v, (int, float)):
        return float(v)
    if not isinstance(v, str) or not v.strip():
        return None
    t = v.strip()
    sign = -1.0 if t.startswith("-") else 1.0
    try:
        parts = [float(p) for p in t.lstrip("+-").replace(":", " ").split()]
    except ValueError:
        return None
    a, b, c = (parts + [0.0, 0.0])[:3]
    return sign * (a + b / 60 + c / 3600)


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_skybot(body) -> list[dict]:
    """Rows as dicts: name, ra and dec (deg), vmag, rate_ra and rate_dec (arcsec/h), cls."""
    rows = body if isinstance(body, list) else []
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        ra_h, dec = _sexagesimal(r.get("RA (hms)")), _sexagesimal(r.get("DEC (dms)"))
        if ra_h is None or dec is None:
            continue
        out.append(
            {
                "name": str(r.get("Name", "unnamed")).strip(),
                "ra": ra_h * 15.0,
                "dec": dec,
                "vmag": _num(r.get("VMag (mag)")),
                "rate_ra": _num(r.get("dRA (arcsec/h)")),
                "rate_dec": _num(r.get("dDEC (arcsec/h)")),
                "cls": str(r.get("Class", "")),
            }
        )
    return out


async def skybot_cone(
    client: httpx.AsyncClient,
    ra: float,
    dec: float,
    radius_arcsec: float,
    epoch_mjd: float,
    timeout: float = 30.0,
    attempts: int = 3,
) -> list[dict]:
    """Known solar-system objects within radius_arcsec of (ra, dec) at epoch_mjd (UTC). SkyBoT
    sometimes answers {"flag": -1, "message": "...computeEphemeris: error..."} and the same request
    works a moment later (2026-11-08), so a failure is tried again."""
    for attempt in range(attempts):
        try:
            return await _skybot_once(client, ra, dec, radius_arcsec, epoch_mjd, timeout)
        except UpstreamError:
            if attempt == attempts - 1:
                raise
            await asyncio.sleep(1.0 * (attempt + 1))
    return []


async def _skybot_once(
    client: httpx.AsyncClient,
    ra: float,
    dec: float,
    radius_arcsec: float,
    epoch_mjd: float,
    timeout: float,
) -> list[dict]:
    params = {
        "-ep": f"{epoch_mjd + MJD_TO_JD:.6f}",
        "-ra": f"{ra:.6f}",
        "-dec": f"{dec:.6f}",
        "-rd": f"{min(radius_arcsec, 36000) / 3600:.5f}",
        "-mime": "json",
        "-output": "all",
        "-loc": "500",
        "-filter": "0",
        "-objFilter": "101",
        "-from": "SPHEREx-flipbook",
    }
    # 101 = asteroids and comets, no planets: asking for planets (111) made SkyBoT fail on
    # planetary satellites for some dates and cones ("planetary_ephem: satellite" error)
    try:
        r = await client.get(ENDPOINT, params=params, timeout=timeout)
    except httpx.TransportError as e:
        raise UpstreamError(
            ErrorCode.TIMEOUT, "SkyBoT (known asteroids) is not answering.", retryable=True
        ) from e
    if r.status_code == 204 or (r.status_code == 200 and not r.text.strip()):
        return []  # SkyBoT answers "no object" with HTTP 204 and no body
    if r.status_code != 200:
        raise UpstreamError(
            ErrorCode.INTERNAL,
            f"SkyBoT answered HTTP {r.status_code}.",
            retryable=r.status_code >= 500,
        )
    # an answer we cannot read must not look like "nothing known here": that would turn a known
    # asteroid into a "candidate"
    try:
        body = r.json()
    except ValueError as e:
        raise UpstreamError(
            ErrorCode.INTERNAL, "SkyBoT sent an answer we could not read.", retryable=False
        ) from e
    if not isinstance(body, list):
        raise UpstreamError(
            ErrorCode.INTERNAL, "SkyBoT sent an answer we could not read.", retryable=False
        )
    return parse_skybot(body)
