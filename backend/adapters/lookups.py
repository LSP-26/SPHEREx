"""T5 (D6.1): name resolution and a cached Gaia cone search (contracts.interfaces.Lookups).

resolve_name(name)   SIMBAD TAP first (exact identifier, so it also gives the proper motion),
                     then the CDS Sesame resolver (forgiving about spelling and capitals, but
                     position only). The position is J2000 and is NOT moved for proper motion.
                     resolve_object returns an ObjectInfo whose .at(mjd) applies the motion.
gaia_cone(pos, r)    Gaia DR3 sources within r arcmin, from the ESA archive, brightest first.
                     Positions are at the Gaia epoch (J2016.0), not moved. Cached for good, because
                     the catalogue never changes.

Everything goes through one httpx.AsyncClient (injectable, so the tests use a mock transport).
A service that does not answer is retried with backoff, then reported as an UpstreamError with a
message that is safe to show. A name nobody knows is a NameNotFound (a ValueError).

LIVE CHECK (scripts/check_lookups.py, 2026-11-07): names resolve through the CfA (Harvard)
mirrors of SIMBAD and Sesame, which are tried first because the CDS (Strasbourg) hosts time
out on connect from some networks. The ESA Gaia archive timed out from the same network, so
Gaia has a fallback: VizieR's copy of Gaia DR3 at the CfA. The VizieR column names are from
its documentation and still to be seen working.
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import math
import time
import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import quote

import httpx

from backend.adapters.cache import RETRY_STATUS, UpstreamError
from backend.pipeline.motion import GAIA_EPOCH_MJD, propagate
from backend.pipeline.targets import J2000_MJD, SURVEY_MID_MJD
from contracts import ErrorCode, SkyPosition, cache_key
from contracts.interfaces import Cache

log = logging.getLogger(__name__)

# Mirrors, tried in order. The CfA (Harvard) mirrors answer in under half a second where the
# Strasbourg (CDS) hosts time out on connect (seen 2026-11-07), so they come first. CDS stays
# as the fallback: a mirror that fails is skipped for DEAD_FOR_S.
SIMBAD_TAPS = ("https://simbad.cfa.harvard.edu/simbad/sim-tap/sync",
               "https://simbad.cds.unistra.fr/simbad/sim-tap/sync")
SESAME_MIRRORS = ("https://vizier.cfa.harvard.edu/viz-bin/nph-sesame/-ox/S",
                  "https://cds.unistra.fr/cgi-bin/nph-sesame/-ox/S")
SIMBAD_TAP, SESAME = SIMBAD_TAPS[0], SESAME_MIRRORS[0]   # the primaries (scripts import these)
CONNECT_TIMEOUT = 8.0           # a blocked host fails in 8 s, not 20
DEAD_FOR_S = 300.0              # a mirror that failed to connect is skipped this long
GAIA_TAP = "https://gea.esac.esa.int/tap-server/tap/sync"
# GAVO's copy of Gaia DR3 at Heidelberg (table gaia.dr3lite: every source, fewer columns, the
# same column names as ESA's). It answered in under a second on 2026-11-08 while ESA sent 503
# and VizieR's CfA database was down ("Postgres connect error").
GAIA_GAVO_TAP = "https://dc.g-vo.org/tap/sync"
GAIA_TAPS = {GAIA_TAP: "gaiadr3.gaia_source", GAIA_GAVO_TAP: "gaia.dr3lite"}
MAX_RADIUS_ARCMIN = 120.0       # the form allows a 120 arcmin field
DEFAULT_MAX_SOURCES = 2000
BIG_CONE_ARCMIN = 10.0
BIG_CONE_G_LIMIT = 17.0
# Gaia DR3 sources: the ESA archive first, then VizieR's copy of Gaia DR3 (catalogue
# I/355/gaiadr3) through VizieR's plain query interface (asu-tsv), at the CfA and then CDS.
# The CfA TAP path returned HTTP 404 and ESA timed out on 2026-11-07, from one network.
GAIA_VIZIER_ASU = ("https://vizier.cfa.harvard.edu/viz-bin/asu-tsv",
                   "https://vizier.cds.unistra.fr/viz-bin/asu-tsv")
MAX_NAME_LENGTH = 200
MAX_XML_BYTES = 200_000
CACHE_VERSION = "v1"            # bump if the stored shape of a Gaia source changes


class NameNotFound(ValueError):
    """No object has this name. str(e) is safe to show the user."""


def _wrap_ra(ra: float) -> float:
    ra = ra % 360.0
    return 0.0 if ra >= 360.0 else ra


@dataclass(frozen=True)
class ObjectInfo:
    name: str                       # the archive's own name for the object
    ra: float                       # degrees, ICRS, epoch J2000
    dec: float
    pmra: float | None = None       # mas/yr, includes cos(dec). None if the service had none
    pmdec: float | None = None
    source: str = "simbad"          # "simbad" or "sesame"

    @property
    def position(self) -> SkyPosition:
        """The J2000 position."""
        return SkyPosition(ra=_wrap_ra(self.ra), dec=self.dec)

    def at(self, mjd: float = SURVEY_MID_MJD) -> SkyPosition:
        """Where the object is at `mjd` (default: the middle of the SPHEREx data so far). Without
        a proper motion, or where it cannot be applied, this is the J2000 position."""
        if self.pmra is None or self.pmdec is None:
            return self.position
        try:
            ra, dec = propagate(self.ra, self.dec, self.pmra, self.pmdec, J2000_MJD, mjd)
            return SkyPosition(ra=ra, dec=dec)
        except ValueError:      # at a pole, or moved off the sphere
            return self.position


def _num(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _adql_string(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def id_candidates(name: str) -> list[str]:
    """The identifier spellings SIMBAD may know a typed name by, most literal first."""
    n = " ".join(name.split())
    out: list[str] = []
    for c in (n, f"NAME {n}", f"* {n}", f"V* {n}"):
        if c not in out:
            out.append(c)
    return out


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _first_text(el: ET.Element, name: str) -> str | None:
    for e in el.iter():
        if _local(e.tag) == name and e.text and e.text.strip():
            return e.text.strip()
    return None


def parse_sesame(text: str, asked: str) -> ObjectInfo | None:
    """The first resolver in a Sesame XML answer that found the object, or None."""
    root = ET.fromstring(text)
    blocks = [e for e in root.iter() if _local(e.tag) == "Resolver"] or [root]
    for block in blocks:
        ra, dec = _num(_first_text(block, "jradeg")), _num(_first_text(block, "jdedeg"))
        if ra is None or dec is None:
            continue
        return ObjectInfo(name=_first_text(block, "oname") or asked, ra=ra, dec=dec,
                          pmra=_num(_first_text(block, "pmRA")),
                          pmdec=_num(_first_text(block, "pmDE")), source="sesame")
    return None


def parse_gaia_csv(text: str) -> list[dict]:
    """Gaia rows as dicts, brightest first (sources with no G magnitude last). ValueError if the
    text is not a Gaia table."""
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or "source_id" not in reader.fieldnames:
        raise ValueError("not a Gaia source table")
    out = []
    for row in reader:
        ra, dec = _num(row.get("ra")), _num(row.get("dec"))
        try:
            source_id = int(row["source_id"])
        except (KeyError, TypeError, ValueError):
            continue
        if ra is None or dec is None:
            continue
        out.append({"source_id": source_id, "ra": ra, "dec": dec,
                    "pmra": _num(row.get("pmra")), "pmdec": _num(row.get("pmdec")),
                    "parallax": _num(row.get("parallax")),
                    "g_mag": _num(row.get("phot_g_mean_mag")),
                    "ref_epoch_mjd": GAIA_EPOCH_MJD})
    out.sort(key=lambda s: (s["g_mag"] is None, s["g_mag"] or 0.0))
    return out


def parse_vizier_tsv(text: str) -> list[dict]:
    """Gaia rows from a VizieR asu-tsv answer (catalogue I/355/gaiadr3, columns Source, RA_ICRS,
    DE_ICRS, pmRA, pmDE, Plx, Gmag), in the same shape as parse_gaia_csv, brightest first.
    Comment lines, the units line and the dashes line are skipped. ValueError if there is no
    header with a Source column (an HTML error page, for example)."""
    names: list[str] | None = None
    out = []
    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        cells = [c.strip() for c in line.split("\t")]
        if names is None:
            if "Source" in cells:
                names = cells
            continue
        row = dict(zip(names, cells))
        try:
            source_id = int(row["Source"])
        except (KeyError, ValueError):
            continue            # the units line and the dashes line
        ra, dec = _num(row.get("RA_ICRS")), _num(row.get("DE_ICRS"))
        if ra is None or dec is None:
            continue
        out.append({"source_id": source_id, "ra": ra, "dec": dec,
                    "pmra": _num(row.get("pmRA")), "pmdec": _num(row.get("pmDE")),
                    "parallax": _num(row.get("Plx")), "g_mag": _num(row.get("Gmag")),
                    "ref_epoch_mjd": GAIA_EPOCH_MJD})
    if names is None:
        raise ValueError("not a VizieR Gaia table")
    out.sort(key=lambda s: (s["g_mag"] is None, s["g_mag"] or 0.0))
    return out


class WebLookups:
    """Implements contracts.interfaces.Lookups
    over SIMBAD, Sesame and Gaia DR3 (ESA, GAVO, VizieR)."""

    def __init__(self, cache: Cache | None = None, client: httpx.AsyncClient | None = None, *,
                 attempts: int = 3, base_delay: float = 0.5, timeout: float = 20.0,
                 max_sources: int = DEFAULT_MAX_SOURCES, g_limit: float | None = None,
                 first_timeout: float = 40.0,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self.cache, self._client, self._timeout = cache, client, timeout
        self.first_timeout = first_timeout
        self.attempts, self.base_delay = attempts, base_delay
        self.max_sources, self.g_limit = max_sources, g_limit
        self._sleep = sleep
        self._dead: dict[str, float] = {}

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout, connect=CONNECT_TIMEOUT))
        return self._client

    # ------------------------------------------------------------ transport
    def _order(self, urls: tuple[str, ...]) -> list[str]:
        """The mirrors worth trying now: those not recently unreachable, or all of them."""
        now = time.monotonic()
        alive = [u for u in urls if u not in self._dead or now - self._dead[u] > DEAD_FOR_S]
        return alive or list(urls)

    async def _fetch(self, service: str, method: str, url: str | tuple[str, ...],
                     **kw) -> httpx.Response:
        """One request, retried on network trouble and 429/5xx. Other statuses are returned.
        `url` may be several mirrors: a connection failure moves on to the next one at once;
        a 429/5xx ends the round and the usual backoff applies."""
        urls = (url,) if isinstance(url, str) else tuple(url)
        n = kw.pop("attempts", None) or self.attempts
        for attempt in range(n):
            for u in self._order(urls):
                try:
                    r = await self._http().request(method, u, **kw)
                except httpx.TransportError as e:
                    log.warning("%s: %s failed (%s: %s)", service, u, type(e).__name__, e)
                    self._dead[u] = time.monotonic()
                    continue
                self._dead.pop(u, None)
                if r.status_code not in RETRY_STATUS:
                    return r
                break
            if attempt < n - 1:
                await self._sleep(self.base_delay * 2 ** attempt)
        raise UpstreamError(ErrorCode.TIMEOUT,
                            f"{service} is not answering. Please try again shortly.",
                            retryable=True)

    async def _tap(self, service: str, url: str | tuple[str, ...], query: str,
                   attempts: int | None = None, **kw) -> str:
        r = await self._fetch(service, "POST", url, attempts=attempts, **kw, data={
            "REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "csv", "QUERY": query})
        if r.status_code != 200:
            raise UpstreamError(ErrorCode.INTERNAL,
                                f"{service} did not accept the request (HTTP {r.status_code}).",
                                retryable=False)
        return r.text

    # ------------------------------------------------------------ names
    async def _simbad(self, name: str) -> ObjectInfo | None:
        ids = ", ".join(_adql_string(c) for c in id_candidates(name))
        text = await self._tap(
            "The SIMBAD name service", SIMBAD_TAPS,
            "SELECT TOP 1 main_id, ra, dec, pmra, pmdec FROM basic JOIN ident ON oidref = oid "
            f"WHERE id IN ({ids})")
        for row in csv.DictReader(io.StringIO(text)):
            ra, dec = _num(row.get("ra")), _num(row.get("dec"))
            if ra is not None and dec is not None:
                return ObjectInfo(name=row.get("main_id") or name, ra=ra, dec=dec,
                                  pmra=_num(row.get("pmra")), pmdec=_num(row.get("pmdec")))
        return None

    async def _sesame(self, name: str) -> ObjectInfo | None:
        service = "The CDS name resolver"
        q = quote(name, safe="")
        r = await self._fetch(service, "GET", tuple(f"{u}?{q}" for u in SESAME_MIRRORS))
        if r.status_code != 200 or len(r.content) > MAX_XML_BYTES:
            raise UpstreamError(ErrorCode.INTERNAL,
                                f"{service} did not accept the request (HTTP {r.status_code}).",
                                retryable=False)
        try:
            return parse_sesame(r.text, name)
        except ET.ParseError as e:
            raise UpstreamError(ErrorCode.INTERNAL, f"{service} sent an answer we could not "
                                "read.", retryable=False) from e

    async def resolve_object(self, name: str) -> ObjectInfo:
        """Position (J2000) and proper motion (if known) of a named object."""
        q = " ".join(name.split())
        if not q:
            raise NameNotFound("Type a name first.")
        if len(q) > MAX_NAME_LENGTH:
            raise NameNotFound("That name is too long.")
        failures: list[UpstreamError] = []
        for lookup in (self._simbad, self._sesame):
            try:
                found = await lookup(q)
            except UpstreamError as e:
                failures.append(e)
                continue
            if found is not None:
                return found
        if len(failures) == 2:
            raise failures[0]
        raise NameNotFound(f'No object called "{q}" was found.')

    async def resolve_name(self, name: str) -> SkyPosition:
        """J2000 position, not moved for proper motion (use resolve_object(...).at(mjd))."""
        return (await self.resolve_object(name)).position

    # ------------------------------------------------------------ Gaia
    def _cone_key(self, pos: SkyPosition, radius_arcmin: float, g_limit: float | None) -> str:
        return cache_key(kind="gaia-cone", frame_id="gaia_cone", release="gaiadr3",
                         version=CACHE_VERSION, ra=pos.ra, dec=pos.dec, size_px=0,
                         params={"radius_arcmin": round(radius_arcmin, 4),
                                 "g_limit": g_limit, "max": self.max_sources})

    async def _cached(self, key: str) -> list[dict] | None:
        if self.cache is None:
            return None
        try:
            raw = await self.cache.get(key)
            if raw is None:
                return None
            data = json.loads(raw)
        except Exception:
            log.warning("unreadable Gaia cache entry, fetching again")
            return None
        ok = isinstance(data, list) and all(isinstance(s, dict) for s in data)
        return data if ok else None

    async def gaia_cone(self, pos: SkyPosition, radius_arcmin: float) -> list[dict]:
        """Gaia DR3 sources within radius_arcmin of pos, brightest first: dicts with source_id,
        ra, dec (epoch J2016.0), pmra and pmdec (mas/yr), parallax (mas), g_mag, ref_epoch_mjd.
        At most max_sources of them (the brightest). Missing values are None."""
        if not 0 < radius_arcmin <= MAX_RADIUS_ARCMIN:
            raise ValueError(f"radius must be above 0 and at most {MAX_RADIUS_ARCMIN:g} arcmin")
        # a big cone in a crowded field (Orion at 27 arcmin) holds tens of thousands of stars;
        # sorting them all made the ESA archive time out. Beyond 10 arcmin only G < 17 is asked for.
        g_limit = self.g_limit if self.g_limit is not None else (
            BIG_CONE_G_LIMIT if radius_arcmin > BIG_CONE_ARCMIN else None)
        key = self._cone_key(pos, radius_arcmin, g_limit)
        hit = await self._cached(key)
        if hit is not None:
            return hit
        top = int(self.max_sources)
        g = None if g_limit is None else f"{g_limit:.2f}"

        def adql(table: str) -> str:
            return (
                f"SELECT TOP {top} source_id, ra, dec, pmra, pmdec, parallax, phot_g_mean_mag "
                f"FROM {table} WHERE 1 = CONTAINS(POINT('ICRS', ra, dec), "
                f"CIRCLE('ICRS', {pos.ra:.7f}, {pos.dec:.7f}, {radius_arcmin / 60:.6f}))"
                + (f" AND phot_g_mean_mag < {g}" if g else "")
                + " ORDER BY phot_g_mean_mag ASC")
        asu = {"-source": "I/355/gaiadr3", "-c": f"{pos.ra:.7f} {pos.dec:+.7f}",
               "-c.r": f"{radius_arcmin:.4f}", "-c.u": "arcmin",
               "-out": "Source,RA_ICRS,DE_ICRS,pmRA,pmDE,Plx,Gmag",
               "-out.max": str(top), "-sort": "Gmag", "-oc.form": "d"}
        if g:
            asu["Gmag"] = f"<{g}"
        order = self._order((*GAIA_TAPS, *GAIA_VIZIER_ASU))
        sources: list[dict] | None = None
        first_error: UpstreamError | None = None
        for i, url in enumerate(order):
            last = i == len(order) - 1
            tries = None if last else 1     # a backend with another behind it gets one attempt
            body = ""
            try:
                if url in GAIA_TAPS:
                    kw = {} if last else {
                            "timeout": httpx.Timeout(self.first_timeout, connect=CONNECT_TIMEOUT)}
                    text = body = await self._tap("The Gaia archive", url, adql(GAIA_TAPS[url]),
                                                  attempts=tries, **kw)
                    sources = parse_gaia_csv(text)
                else:
                    r = await self._fetch("The Gaia archive", "GET", url, attempts=tries,
                                          params=asu)
                    if r.status_code != 200:
                        raise UpstreamError(
                            ErrorCode.INTERNAL, "The Gaia archive did not accept the request "
                            f"(HTTP {r.status_code}).", retryable=False)
                    body = r.text
                    sources = parse_vizier_tsv(r.text)
                break
            except UpstreamError as e:
                first_error = first_error or e
            except ValueError:
                # the start of what came back says why (an error page, a maintenance notice)
                lines = body.splitlines()
                data = [ln for ln in lines if not ln.startswith("#")][:4]
                # VizieR puts its reason in "#INFO Error=..." lines (e.g. its database is down)
                errors = [ln.strip() for ln in lines if ln.startswith("#INFO") and "Error=" in ln
                          and ln.split("Error=", 1)[1].strip()][:4]
                notes = errors or [ln for ln in lines
                                   if ln.startswith("#") and "INFO" not in ln][-3:]
                log.warning("Gaia answer from %s could not be read. First data lines: %r; "
                            "last comment lines: %r; %d bytes", url, data, notes, len(body))
                first_error = first_error or UpstreamError(
                    ErrorCode.INTERNAL, "The Gaia archive sent an answer we could not read.",
                    retryable=False)
        if sources is None:
            assert first_error is not None
            raise first_error
        if self.cache is not None:
            try:
                await self.cache.put(key, json.dumps(sources).encode())
            except Exception:   # a full disk must not break a lookup
                log.warning("could not store the Gaia cone in the cache", exc_info=True)
        return sources
