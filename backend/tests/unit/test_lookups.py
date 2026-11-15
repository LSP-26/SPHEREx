from urllib.parse import parse_qs, unquote_plus

import httpx
import pytest

from backend.adapters.cache import UpstreamError
from backend.adapters.lookups import (
    MAX_RADIUS_ARCMIN,
    NameNotFound,
    ObjectInfo,
    WebLookups,
    id_candidates,
)
from backend.pipeline.motion import GAIA_EPOCH_MJD
from contracts import ErrorCode, SkyPosition
from contracts.interfaces import Lookups

SIMBAD_CSV = ("main_id,ra,dec,pmra,pmdec\n"
              "NAME Barnard's star,269.4520769,4.6933649,-802.803,10362.542\n")
SIMBAD_EMPTY = "main_id,ra,dec,pmra,pmdec\n"
SESAME_FOUND = (
    '<Sesame><Target option="S"><name>vega x</name>'
    '<Resolver name="S=Simbad (CDS, via client/server)"><otype>*</otype>'
    "<jpos>18:36:56.34 +38:47:01.3</jpos><jradeg>279.23473</jradeg>"
    "<jdedeg>38.78369</jdedeg><oname>* alf Lyr</oname></Resolver></Target></Sesame>")
SESAME_NONE = ('<Sesame><Target option="S"><name>zzz</name><Resolver name="S=Simbad">'
               "<INFO>Nothing found</INFO></Resolver></Target></Sesame>")
GAIA_CSV = ("source_id,ra,dec,pmra,pmdec,parallax,phot_g_mean_mag\n"
            "111,269.46,4.74,,,,17.9\n"
            "4472832130942575872,269.4578,4.7394,-802.1,10362.1,546.9,8.19\n"
            "222,269.47,4.75,1.0,2.0,0.5,\n")


def make(handler, **kw):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sleeps = []

    async def sleep(d):
        sleeps.append(d)

    return WebLookups(client=client, sleep=sleep, **kw), sleeps


def query_of(request):
    return parse_qs(request.content.decode())["QUERY"][0]


class DictCache:
    def __init__(self):
        self.data = {}

    async def get(self, key):
        return self.data.get(key)

    async def put(self, key, value):
        self.data[key] = value


def test_satisfies_the_interface():
    assert isinstance(WebLookups(), Lookups)


def test_identifier_spellings():
    assert id_candidates("  Barnard's   Star ") == [
        "Barnard's Star", "NAME Barnard's Star", "* Barnard's Star", "V* Barnard's Star"]


# ------------------------------------------------------------------ names
async def test_simbad_gives_position_and_proper_motion():
    seen = []

    def handler(request):
        assert request.url.host.startswith("simbad.")
        seen.append(query_of(request))
        return httpx.Response(200, text=SIMBAD_CSV)

    lk, _ = make(handler)
    info = await lk.resolve_object("  Barnard's   Star ")
    assert info.name == "NAME Barnard's star" and info.source == "simbad"
    assert (info.ra, info.dec, info.pmra, info.pmdec) == (269.4520769, 4.6933649, -802.803,
                                                          10362.542)
    assert "'Barnard''s Star'" in seen[0] and "'NAME Barnard''s Star'" in seen[0]
    assert len(seen) == 1
    pos = await lk.resolve_name("Barnard's Star")
    assert pos == SkyPosition(ra=269.4520769, dec=4.6933649)


async def test_at_moves_the_star_to_where_it_is_during_the_survey():
    info = ObjectInfo("Barnard", 269.452077, 4.693365, -802.803, 10362.542)
    moved = info.at()                       # MJD 61100
    assert moved.dec == pytest.approx(4.7687, abs=0.001)    # about 271 arcsec north
    assert moved.ra == pytest.approx(269.4462, abs=0.001)
    assert info.at(51544.5) == info.position                    # at J2000 it has not moved
    assert ObjectInfo("x", 10.0, 5.0).at() == SkyPosition(ra=10.0, dec=5.0)   # no motion known
    assert ObjectInfo("pole", 0.0, 90.0, 1.0, 1.0).at().dec == 90.0           # cannot move


async def test_sesame_is_the_fallback_for_names_simbad_does_not_know():
    urls = []

    def handler(request):
        if request.url.host.startswith("simbad."):
            return httpx.Response(200, text=SIMBAD_EMPTY)
        urls.append(request.url)
        return httpx.Response(200, text=SESAME_FOUND)

    lk, _ = make(handler)
    info = await lk.resolve_object("vega")
    assert info.source == "sesame" and info.name == "* alf Lyr"
    assert (info.ra, info.dec, info.pmra) == (279.23473, 38.78369, None)
    assert urls[0].path.endswith("/-ox/S") and "vega" in urls[0].query.decode()


async def test_an_unknown_name_is_a_clear_not_found():
    def handler(request):
        if request.url.host.startswith("simbad."):
            return httpx.Response(200, text=SIMBAD_EMPTY)
        return httpx.Response(200, text=SESAME_NONE)

    lk, _ = make(handler)
    with pytest.raises(NameNotFound, match='No object called "zzz"'):
        await lk.resolve_name("zzz")


@pytest.mark.parametrize("bad", ["", "   ", "x" * 500])
async def test_empty_or_silly_names_make_no_request(bad):
    def handler(request):
        raise AssertionError("no request expected")

    lk, _ = make(handler)
    with pytest.raises(NameNotFound):
        await lk.resolve_name(bad)


async def test_both_services_down_is_a_retryable_error_not_a_not_found():
    def handler(request):
        raise httpx.ConnectError("down")

    lk, sleeps = make(handler)
    with pytest.raises(UpstreamError) as e:
        await lk.resolve_name("Vega")
    assert e.value.code is ErrorCode.TIMEOUT and e.value.retryable and e.value.message
    assert sleeps == [0.5, 1.0, 0.5, 1.0]       # two retries for each of the two services


async def test_a_busy_server_is_retried_and_then_answers():
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(503)
        return httpx.Response(200, text=SIMBAD_CSV)

    lk, sleeps = make(handler)
    assert (await lk.resolve_object("Barnard's Star")).dec == 4.6933649
    assert len(calls) == 3 and sleeps == [0.5, 1.0]


async def test_a_simbad_error_falls_through_to_sesame():
    def handler(request):
        if request.url.host.startswith("simbad."):
            return httpx.Response(400, text="bad query")
        return httpx.Response(200, text=SESAME_FOUND)

    lk, _ = make(handler)
    assert (await lk.resolve_object("vega")).source == "sesame"


# ------------------------------------------------------------------ Gaia
POS = SkyPosition(ra=269.45, dec=4.74)


async def test_gaia_cone_parses_sorts_and_asks_the_right_question():
    seen = []

    def handler(request):
        assert request.url.host == "gea.esac.esa.int"
        seen.append(query_of(request))
        return httpx.Response(200, text=GAIA_CSV)

    lk, _ = make(handler)
    sources = await lk.gaia_cone(POS, 1.0)
    assert [s["source_id"] for s in sources] == [4472832130942575872, 111, 222]
    barnard = sources[0]
    assert barnard["pmdec"] == 10362.1 and barnard["g_mag"] == 8.19
    assert barnard["ref_epoch_mjd"] == GAIA_EPOCH_MJD
    assert sources[1]["pmra"] is None and sources[1]["parallax"] is None
    assert sources[2]["g_mag"] is None                      # no magnitude: listed last
    q = seen[0]
    assert "FROM gaiadr3.gaia_source" in q and "TOP 2000" in q
    assert "CIRCLE('ICRS', 269.4500000, 4.7400000, 0.016667)" in q
    assert "phot_g_mean_mag <" not in q


async def test_magnitude_limit_and_source_cap_reach_the_query():
    seen = []

    def handler(request):
        seen.append(query_of(request))
        return httpx.Response(200, text=GAIA_CSV)

    lk, _ = make(handler, g_limit=19.5, max_sources=50)
    await lk.gaia_cone(POS, 2.0)
    assert "TOP 50" in seen[0] and "phot_g_mean_mag < 19.50" in seen[0]


async def test_second_identical_cone_comes_from_the_cache():
    calls, cache = [], DictCache()

    def handler(request):
        calls.append(1)
        return httpx.Response(200, text=GAIA_CSV)

    lk, _ = make(handler, cache=cache)
    first = await lk.gaia_cone(POS, 1.0)
    assert await lk.gaia_cone(POS, 1.0) == first and len(calls) == 1
    await lk.gaia_cone(POS, 2.0)                            # another radius: another entry
    assert len(calls) == 2 and len(cache.data) == 2


async def test_a_damaged_cache_entry_is_fetched_again_and_repaired():
    calls, cache = [], DictCache()

    def handler(request):
        calls.append(1)
        return httpx.Response(200, text=GAIA_CSV)

    lk, _ = make(handler, cache=cache)
    await lk.gaia_cone(POS, 1.0)
    key = next(iter(cache.data))
    cache.data[key] = b"not json"
    assert len(await lk.gaia_cone(POS, 1.0)) == 3 and len(calls) == 2
    assert cache.data[key].startswith(b"[")


async def test_a_cache_that_cannot_write_does_not_break_the_lookup():
    class Broken(DictCache):
        async def put(self, key, value):
            raise OSError("disk full")

    lk, _ = make(lambda request: httpx.Response(200, text=GAIA_CSV), cache=Broken())
    assert len(await lk.gaia_cone(POS, 1.0)) == 3


async def test_an_empty_field_is_a_valid_empty_answer():
    lk, _ = make(lambda r: httpx.Response(
        200, text="source_id,ra,dec,pmra,pmdec,parallax,phot_g_mean_mag\n"))
    assert await lk.gaia_cone(POS, 1.0) == []


@pytest.mark.parametrize("radius", [0, -1, MAX_RADIUS_ARCMIN + 1])
async def test_bad_radius_is_refused_before_any_request(radius):
    def handler(request):
        raise AssertionError("no request expected")

    lk, _ = make(handler)
    with pytest.raises(ValueError):
        await lk.gaia_cone(POS, radius)


async def test_gaia_failures_are_clear_errors():
    lk, _ = make(lambda r: httpx.Response(400, text="bad ADQL"))
    with pytest.raises(UpstreamError) as e:
        await lk.gaia_cone(POS, 1.0)
    assert e.value.code is ErrorCode.INTERNAL and not e.value.retryable
    lk, _ = make(lambda r: httpx.Response(200, text="<html>maintenance</html>"))
    with pytest.raises(UpstreamError) as e:
        await lk.gaia_cone(POS, 1.0)
    assert e.value.code is ErrorCode.INTERNAL
    lk, _ = make(lambda r: httpx.Response(503))
    with pytest.raises(UpstreamError) as e:
        await lk.gaia_cone(POS, 1.0)
    assert e.value.code is ErrorCode.TIMEOUT and e.value.retryable


# ------------------------------------------------------------------ mirrors
async def test_harvard_mirrors_are_tried_first():
    hosts = []

    def handler(request):
        hosts.append(request.url.host)
        return httpx.Response(200, text=SIMBAD_CSV)

    lk, _ = make(handler)
    await lk.resolve_object("Barnard's Star")
    assert hosts == ["simbad.cfa.harvard.edu"]


async def test_a_blocked_primary_fails_over_at_once_and_is_remembered():
    hosts = []

    def handler(request):
        hosts.append(request.url.host)
        if request.url.host == "simbad.cfa.harvard.edu":
            raise httpx.ConnectTimeout("blocked")
        return httpx.Response(200, text=SIMBAD_CSV)

    lk, sleeps = make(handler)
    assert (await lk.resolve_object("Barnard's Star")).dec == 4.6933649
    assert hosts == ["simbad.cfa.harvard.edu", "simbad.cds.unistra.fr"] and sleeps == []
    await lk.resolve_object("Barnard's Star")
    assert hosts[2:] == ["simbad.cds.unistra.fr"]       # the dead mirror is skipped


async def test_sesame_fails_over_too():
    hosts = []

    def handler(request):
        hosts.append(request.url.host)
        if request.url.host.startswith("simbad."):
            return httpx.Response(200, text=SIMBAD_EMPTY)
        if request.url.host == "vizier.cfa.harvard.edu":
            raise httpx.ConnectTimeout("blocked")
        return httpx.Response(200, text=SESAME_FOUND)

    lk, sleeps = make(handler)
    assert (await lk.resolve_object("vega")).source == "sesame"
    assert hosts[-2:] == ["vizier.cfa.harvard.edu", "cds.unistra.fr"] and sleeps == []


VIZ_TSV = ("#\n#   VizieR (CDS)\n"
           "Source\tRA_ICRS\tDE_ICRS\tpmRA\tpmDE\tPlx\tGmag\n"
           "\tdeg\tdeg\tmas/yr\tmas/yr\tmas\tmag\n"
           "-----\t---\t---\t---\t---\t---\t---\n"
           "111\t269.46\t4.74\t\t\t\t17.9\n"
           "4472832130942575872\t269.4578\t4.7394\t-802.1\t10362.1\t546.9\t8.19\n")


async def test_gaia_falls_over_to_vizier_asu():
    seen = []

    def handler(request):
        seen.append((request.url.host, dict(request.url.params)))
        if request.url.host in ("gea.esac.esa.int", "dc.g-vo.org"):
            raise httpx.ReadTimeout("slow")
        return httpx.Response(200, text=VIZ_TSV)

    lk, sleeps = make(handler)
    sources = await lk.gaia_cone(POS, 1.0)
    assert [h for h, _ in seen] == ["gea.esac.esa.int", "dc.g-vo.org", "vizier.cfa.harvard.edu"]
    assert sleeps == []
    assert [s["source_id"] for s in sources] == [4472832130942575872, 111]
    assert sources[0]["pmdec"] == 10362.1 and sources[0]["g_mag"] == 8.19
    assert sources[1]["pmra"] is None and sources[1]["ref_epoch_mjd"] == GAIA_EPOCH_MJD
    q = seen[2][1]
    assert q["-source"] == "I/355/gaiadr3" and q["-out.max"] == "2000"
    assert q["-c"] == "269.4500000 +4.7400000" and q["-c.r"] == "1.0000"
    assert "Gmag" not in q
    await lk.gaia_cone(SkyPosition(ra=269.45, dec=4.75), 1.0)
    assert [h for h, _ in seen][3:] == ["vizier.cfa.harvard.edu"]   # both TAPs skipped now


async def test_vizier_gets_the_magnitude_limit():
    seen = []

    def handler(request):
        seen.append(dict(request.url.params))
        if request.url.host in ("gea.esac.esa.int", "dc.g-vo.org"):
            raise httpx.ReadTimeout("slow")
        return httpx.Response(200, text=VIZ_TSV)

    lk, _ = make(handler, g_limit=19.5, max_sources=50)
    await lk.gaia_cone(POS, 2.0)
    assert seen[2]["Gmag"] == "<19.50" and seen[2]["-out.max"] == "50"


async def test_gaia_falls_over_to_gavo_tap():
    seen = []

    def handler(request):
        seen.append((request.url.host, request.content.decode()))
        if request.url.host == "gea.esac.esa.int":
            return httpx.Response(503)
        return httpx.Response(200, text=GAIA_CSV)

    lk, _ = make(handler, attempts=1)
    sources = await lk.gaia_cone(POS, 1.0)
    assert [h for h, _ in seen] == ["gea.esac.esa.int", "dc.g-vo.org"]
    assert "gaia.dr3lite" in unquote_plus(seen[1][1]) and len(sources) > 0


async def test_a_404_from_one_backend_moves_on_and_all_failing_is_an_error():
    def ok(request):
        if request.url.host == "gea.esac.esa.int":
            return httpx.Response(404, text="nope")
        if request.url.host == "vizier.cfa.harvard.edu":
            return httpx.Response(404, text="nope")
        return httpx.Response(200, text=VIZ_TSV)

    lk, _ = make(ok)
    assert len(await lk.gaia_cone(POS, 1.0)) == 2          # the third backend answered
    lk, _ = make(lambda r: httpx.Response(404, text="nope"))
    with pytest.raises(UpstreamError) as e:
        await lk.gaia_cone(POS, 1.0)
    assert e.value.code is ErrorCode.INTERNAL


def test_vizier_parser_handles_empty_and_html_answers():
    from backend.adapters.lookups import parse_vizier_tsv
    assert parse_vizier_tsv("#c\nSource\tRA_ICRS\tDE_ICRS\n\tdeg\tdeg\n---\t---\t---\n") == []
    with pytest.raises(ValueError):
        parse_vizier_tsv("<html>maintenance</html>")
