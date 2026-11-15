import math

from backend.offline.flag_neighbours import NEIGHBOUR_FLAG, flag_points
from backend.pipeline.astrometry import close_neighbours
from backend.pipeline.motion import GAIA_EPOCH_MJD
from backend.pipeline.targets import position_at

RA, DEC = 100.0, 20.0


def star(dra_arcsec, ddec_arcsec, g, **kw):
    return {"source_id": kw.pop("sid", 1), "g_mag": g, "pmra": None, "pmdec": None,
            "ref_epoch_mjd": GAIA_EPOCH_MJD,
            "ra": RA + dra_arcsec / 3600 / math.cos(math.radians(DEC)),
            "dec": DEC + ddec_arcsec / 3600, **kw}


def near(sources, **kw):
    return close_neighbours(sources, RA, DEC, GAIA_EPOCH_MJD, kw.pop("radius", 18.0), 15.0, **kw)


def test_a_star_inside_the_radius_is_found_with_its_distance():
    out = near([star(10, 0, 12.0)])
    assert len(out) == 1 and abs(out[0]["sep_arcsec"] - 10.0) < 0.01 and out[0]["g_mag"] == 12.0


def test_the_target_itself_far_stars_faint_stars_and_unmeasured_stars_are_not_neighbours():
    sources = [star(0.5, 0, 9.0), star(30, 0, 12.0), star(10, 0, 16.5, sid=3),
               star(10, 0, None, sid=4)]
    assert near(sources) == []


def test_neighbours_come_nearest_first():
    out = near([star(15, 0, 12.0, sid=1), star(5, 0, 14.0, sid=2)])
    assert [n["source_id"] for n in out] == [2, 1]


def test_proper_motion_moves_a_neighbour_in_or_out():
    mover = star(100, 0, 12.0, pmra=-5000.0, pmdec=0.0)    # 5 arcsec/yr toward the target
    assert near([mover]) == []                              # at the Gaia epoch it is 100" away
    later = close_neighbours([mover], RA, DEC, GAIA_EPOCH_MJD + 365.25 * 19, 18.0, 15.0)
    assert len(later) == 1 and later[0]["sep_arcsec"] < 10


def test_flag_points_adds_replaces_and_removes_the_flag():
    t = 61100.0
    ra, dec = position_at("rw_aur", t)
    neighbour = {"source_id": 5, "ra": ra + 8 / 3600 / math.cos(math.radians(dec)), "dec": dec,
                 "pmra": None, "pmdec": None, "g_mag": 13.2, "ref_epoch_mjd": GAIA_EPOCH_MJD}
    body = {"key": "rw_aur", "points": [
        {"t_mjd": t, "flags": ["crowded"]},
        {"t_mjd": t, "flags": [f"{NEIGHBOUR_FLAG} 99 arcsec away (G 1.0)"]}]}
    assert flag_points(body, [neighbour]) == 2
    for p in body["points"]:
        assert len([f for f in p["flags"] if f.startswith(NEIGHBOUR_FLAG)]) == 1
        assert "8 arcsec away (G 13.2)" in p["flags"][-1]
    assert body["points"][0]["flags"][0] == "crowded"
    assert flag_points(body, []) == 0                       # rerun with no neighbours
    assert body["points"][0]["flags"] == ["crowded"] and body["points"][1]["flags"] == []
