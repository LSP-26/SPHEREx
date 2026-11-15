import json

import pytest

from backend.pipeline.motion import DAYS_PER_YEAR, GAIA_EPOCH_MJD
from backend.pipeline.overlay import cone_radius_arcmin, project_sources
from backend.pipeline.render import preview_size_px, render_stamp
from backend.tests.unit.test_render_regrid import DEC0, PX, RA0, peak, req, stamp_with_stars

RA, DEC = 100.0, 0.0
FOV = 5.0                       # a 49 x 49 picture, centre pixel 24
MID = 24
SKY_PX = 6.15 / 3600            # one pixel in degrees


def star(ra=RA, dec=DEC, **kw):
    return {"source_id": 1, "ra": ra, "dec": dec, "pmra": None, "pmdec": None,
            "parallax": None, "g_mag": 12.0, "ref_epoch_mjd": GAIA_EPOCH_MJD, **kw}


def one(src, ra=RA, dec=DEC, fov=FOV, **kw):
    kw.setdefault("epoch_mjd", GAIA_EPOCH_MJD)
    o = project_sources([src], ra, dec, fov, **kw)
    return o.stars[0] if o.stars else None


def test_a_star_at_the_centre_is_in_the_middle_of_the_picture():
    s = one(star())
    assert (s.x, s.y) == pytest.approx((MID, MID), abs=1e-6)
    assert (s.u, s.v) == pytest.approx((0.5, 0.5), abs=1e-6)
    assert (s.vx, s.vy) == (0.0, 0.0)


def test_north_is_up_and_east_is_left():
    north = one(star(dec=DEC + 10 * SKY_PX))
    assert north.x == pytest.approx(MID, abs=0.05) and north.y == pytest.approx(MID - 10, abs=0.05)
    east = one(star(ra=RA + 10 * SKY_PX))
    assert east.y == pytest.approx(MID, abs=0.05) and east.x == pytest.approx(MID - 10, abs=0.05)


def test_proper_motion_becomes_a_velocity_on_the_picture_whatever_the_declination():
    # 6.15 arcsec/yr on the sky, east and north: one pixel per year, left and up
    s = one(star(ra=RA, dec=60.0, pmra=6150.0, pmdec=6150.0), dec=60.0)
    assert s.vx == pytest.approx(-1.0, abs=0.01) and s.vy == pytest.approx(-1.0, abs=0.01)
    assert (s.pmra, s.pmdec) == (6150.0, 6150.0)


def test_the_star_is_moved_from_its_own_epoch_to_the_requested_date():
    later = GAIA_EPOCH_MJD + 10 * DAYS_PER_YEAR
    s = one(star(pmdec=6150.0), epoch_mjd=later)          # ten years of 1 px/yr northwards
    assert s.x == pytest.approx(MID, abs=0.01) and s.y == pytest.approx(MID - 10, abs=0.01)
    assert s.vy == pytest.approx(-1.0, abs=0.01)
    at_epoch = one(star(pmdec=6150.0), epoch_mjd=GAIA_EPOCH_MJD)
    assert at_epoch.y == pytest.approx(MID, abs=0.01)


def test_missing_catalogue_values_mean_no_motion_and_do_not_crash():
    s = one(star(pmra=None, pmdec=None, parallax=None, g_mag=None, source_id=None))
    assert s.source_id is None and s.g_mag is None and s.parallax is None
    assert (s.vx, s.vy) == (0.0, 0.0)


def test_stars_off_the_picture_are_dropped_and_a_margin_keeps_them():
    far = star(ra=RA + 40 * SKY_PX)                       # 40 px east: x = -16
    assert one(far) is None
    kept = one(far, margin_px=20)
    assert kept is not None and kept.x == pytest.approx(MID - 40, abs=0.1)
    assert kept.u < 0


def test_bad_rows_and_the_far_side_of_the_sky_are_skipped_quietly():
    sources = [star(ra=None), star(dec=None), star(ra=RA + 180.0), star(source_id=7)]
    o = project_sources(sources, RA, DEC, FOV, epoch_mjd=GAIA_EPOCH_MJD)
    assert [s.source_id for s in o.stars] == [7]


def test_the_catalogue_order_is_kept():
    a, b = star(source_id=1, g_mag=8.0), star(source_id=2, ra=RA + 3 * SKY_PX, g_mag=15.0)
    o = project_sources([a, b], RA, DEC, FOV, epoch_mjd=GAIA_EPOCH_MJD)
    assert [s.source_id for s in o.stars] == [1, 2]


def test_the_result_is_plain_json():
    o = project_sources([star(pmra=10.0, pmdec=-5.0, parallax=1.5)], RA, DEC, FOV)
    body = json.loads(json.dumps(o.to_json()))
    assert body["size"] == preview_size_px(FOV) == 49 and body["epoch_mjd"] == o.epoch_mjd
    assert body["pixel_arcsec"] == 6.15
    assert set(body["stars"][0]) == {"source_id", "x", "y", "u", "v", "vx", "vy", "g_mag",
                                     "pmra", "pmdec", "parallax"}


def test_cone_radius_covers_the_corners_plus_a_margin_and_is_capped():
    assert cone_radius_arcmin(5.0) == pytest.approx(5.55, abs=0.01)    # 3.55 half-diagonal + 2
    assert cone_radius_arcmin(5.0, margin_arcmin=0.0) == pytest.approx(3.55, abs=0.01)
    assert cone_radius_arcmin(120.0) == pytest.approx(86.87, abs=0.01)   # still under the cap
    assert cone_radius_arcmin(120.0, margin_arcmin=100.0) == 120.0       # capped


@pytest.mark.parametrize("fov", [0, -1, 121])
def test_a_silly_field_of_view_is_refused(fov):
    with pytest.raises(ValueError):
        cone_radius_arcmin(fov)
    with pytest.raises(ValueError):
        project_sources([], RA, DEC, fov)


@pytest.mark.parametrize("roll", [0.0, 2.4, 90.0, 178.0])
def test_a_projected_star_lands_on_the_same_pixel_the_renderer_draws_it(roll):
    gray, _ = render_stamp(stamp_with_stars(roll, [(12, 7, 200.0)]), req(3.0))
    row, col = peak(gray)
    gaia = star(ra=RA0 + 12 * PX, dec=DEC0 + 7 * PX)
    s = project_sources([gaia], RA0, DEC0, 3.0, epoch_mjd=GAIA_EPOCH_MJD).stars[0]
    assert abs(s.x - col) <= 1 and abs(s.y - row) <= 1
