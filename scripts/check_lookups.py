"""T5 check on the LIVE services: name resolution and the Gaia cone search.

  python scripts/check_lookups.py

0. Probes each service directly (SIMBAD two ways, Sesame, Gaia) with timings, so a slow or
   blocked service shows up at once instead of as silence.
1. Resolves five names (SIMBAD TAP first, Sesame as the fallback), prints which service answered
   and how far the position is from backend/pipeline/targets.py at J2000 (PASS under 5 arcsec).
2. A name that does not exist must come back as "not found", not as a crash.
3. Gaia cone, radius 1 arcmin, centred on Barnard's Star at the Gaia epoch (J2016.0). The star
   must be in it with a proper motion close to targets.py (PASS: pmdec within 100 mas/yr, and
   within 0.5 arcmin of the centre). A second identical call must come from the cache.
Prints PASS or FAIL per line and RESULT at the end. Send the whole output back.
"""
from __future__ import annotations

import asyncio
import math
import time

import httpx

from backend.adapters.cache import UpstreamError
from backend.adapters.lookups import (
    GAIA_TAP,
    GAIA_VIZIER_ASU,
    SESAME_MIRRORS,
    SIMBAD_TAPS,
    NameNotFound,
    WebLookups,
    id_candidates,
)
from backend.adapters.stubs import MemoryCache
from backend.pipeline.motion import GAIA_EPOCH_MJD
from backend.pipeline.targets import J2000_MJD, TARGETS, position_at
from contracts import SkyPosition

NAMES = [("Barnard's Star", "barnard"), ("eps Ind", "eps_indi_a"), ("R Dra", "r_dra"),
         ("RW Aur", "rw_aur"), ("WISE J085510.83-071442.5", "wise0855")]
PER_NAME_LIMIT_S = 60.0


def p(*a) -> None:
    print(*a, flush=True)


def sep_arcsec(a: tuple[float, float], b: tuple[float, float]) -> float:
    dra = ((a[0] - b[0] + 180.0) % 360.0 - 180.0) * math.cos(math.radians((a[1] + b[1]) / 2))
    return math.hypot(dra, a[1] - b[1]) * 3600.0


async def probe(label: str, make_request) -> None:
    """One raw request with a 20 s limit. Prints status, size, time and the start of the body."""
    t = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=8.0)) as c:
            r = await make_request(c)
        body = r.text[:110].replace("\n", " | ")
        p(f"   {label:<34} HTTP {r.status_code}, {len(r.content)} bytes, "
          f"{time.monotonic() - t:.2f} s  {body!r}")
    except Exception as e:
        p(f"   {label:<34} FAILED after {time.monotonic() - t:.2f} s: "
          f"{type(e).__name__}: {str(e)[:100]}")


def tap(url: str, query: str):
    return lambda c: c.post(url, data={"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "csv",
                                       "QUERY": query})


async def probes() -> None:
    p("0. the services, one raw request each (limit 20 s)")
    one = ("SELECT main_id, ra, dec, pmra, pmdec FROM basic JOIN ident ON oidref = oid "
           "WHERE id = 'NAME Barnard''s star'")
    ids = ", ".join("'" + c.replace("'", "''") + "'" for c in id_candidates("Barnard's Star"))
    many = ("SELECT TOP 1 main_id, ra, dec, pmra, pmdec FROM basic JOIN ident ON oidref = oid "
            f"WHERE id IN ({ids})")
    for mirror in SIMBAD_TAPS:
        host = mirror.split("/")[2]
        await probe(f"SIMBAD one id [{host}]", tap(mirror, one))
        await probe(f"SIMBAD IN list [{host}]", tap(mirror, many))
    for mirror in SESAME_MIRRORS:
        host = mirror.split("/")[2]
        await probe(f"Sesame [{host}]",
                    lambda c, m=mirror: c.get(f"{m}?Barnard%27s%20Star"))
    await probe("Gaia ESA, tiny query",
                tap(GAIA_TAP, "SELECT TOP 1 source_id FROM gaiadr3.gaia_source"))
    for mirror in GAIA_VIZIER_ASU:
        host = mirror.split("/")[2]
        params = {"-source": "I/355/gaiadr3", "-c": "269.452 +4.693", "-c.r": "1",
                  "-c.u": "arcmin", "-out": "Source,RA_ICRS,DE_ICRS,pmRA,pmDE,Plx,Gmag",
                  "-out.max": "3", "-sort": "Gmag"}
        await probe(f"Gaia VizieR ASU [{host}]",
                    lambda c, m=mirror, q=params: c.get(m, params=q))
    p()


async def main() -> None:
    await probes()
    lk = WebLookups(cache=MemoryCache(), attempts=2, timeout=15.0)
    ok = True
    p("1. names")
    for name, key in NAMES:
        p(f"   resolving {name!r} ...")
        t = time.monotonic()
        try:
            info = await asyncio.wait_for(lk.resolve_object(name), PER_NAME_LIMIT_S)
        except (NameNotFound, UpstreamError) as e:
            p(f"   FAIL {name!r}: {type(e).__name__}: {e} ({time.monotonic() - t:.1f} s)")
            ok = False
            continue
        except asyncio.TimeoutError:
            p(f"   FAIL {name!r}: no answer within {PER_NAME_LIMIT_S:.0f} s")
            ok = False
            continue
        d = sep_arcsec((info.ra, info.dec), position_at(key, J2000_MJD))
        good = d < 5.0
        ok &= good
        p(f"   {'PASS' if good else 'FAIL'} {name!r} -> {info.name!r} via {info.source}, "
          f"RA {info.ra:.6f} Dec {info.dec:.6f}, pm ({info.pmra}, {info.pmdec}), "
          f"{d:.1f} arcsec from targets.py, {time.monotonic() - t:.2f} s")
    p("2. a name that does not exist")
    try:
        await asyncio.wait_for(lk.resolve_name("zzzz not a star 9999"), PER_NAME_LIMIT_S)
        p("   FAIL: it was found?!")
        ok = False
    except NameNotFound as e:
        p(f"   PASS: {e}")
    except UpstreamError as e:
        p(f"   FAIL: a service error instead of 'not found': {e}")
        ok = False
    except asyncio.TimeoutError:
        p(f"   FAIL: no answer within {PER_NAME_LIMIT_S:.0f} s")
        ok = False
    p("3. Gaia cone around Barnard's Star at J2016.0")
    ra, dec = position_at("barnard", GAIA_EPOCH_MJD)
    centre = SkyPosition(ra=ra, dec=dec)
    t = time.monotonic()
    try:
        sources = await asyncio.wait_for(lk.gaia_cone(centre, 1.0), PER_NAME_LIMIT_S)
    except (UpstreamError, asyncio.TimeoutError) as e:
        p(f"   FAIL: {type(e).__name__}: {e}")
        sources = []
        ok = False
    first = time.monotonic() - t
    want = TARGETS["barnard"].pmdec
    near = [s for s in sources if s["pmdec"] is not None and abs(s["pmdec"] - want) < 100
            and sep_arcsec((s["ra"], s["dec"]), (ra, dec)) < 30]
    good = bool(near)
    ok &= good
    p(f"   {'PASS' if good else 'FAIL'} {len(sources)} sources in {first:.2f} s; "
      f"the high-proper-motion star: {near[0] if near else 'NOT FOUND'}")
    for s in sources[:5]:
        p(f"      G={s['g_mag']} id={s['source_id']} pm=({s['pmra']}, {s['pmdec']})")
    if sources:
        t = time.monotonic()
        again = await lk.gaia_cone(centre, 1.0)
        cached = again == sources and time.monotonic() - t < 0.05
        ok &= cached
        p(f"   {'PASS' if cached else 'FAIL'} second call from the cache "
          f"({time.monotonic() - t:.3f} s)")
    await lk.aclose()
    p("\nRESULT:", "PASS" if ok else "FAIL")


if __name__ == "__main__":
    asyncio.run(main())
