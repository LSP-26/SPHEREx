from types import SimpleNamespace

import numpy as np
import pytest
from astropy.wcs import WCS

from backend.pipeline.render import RealRenderer, preview_size_px, render_stamp
from contracts import Orientation

PX = 6.15 / 3600
RA0, DEC0 = 10.0, 0.0
N = 61


def frame_wcs(roll_deg):
    """A frame whose pixel (1020, 1020) is the target, rolled by roll_deg (SPHEREx rolls)."""
    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crval, w.wcs.crpix = [RA0, DEC0], [1020.0, 1020.0]
    c, s = np.cos(np.radians(roll_deg)), np.sin(np.radians(roll_deg))
    w.wcs.cd = [[-PX * c, PX * s], [PX * s, PX * c]]
    w.wcs.set()
    return w


def header(w):
    h = {"NAXIS": 2, "NAXIS1": 2040, "NAXIS2": 2040, "CTYPE1": "RA---TAN", "CTYPE2": "DEC--TAN",
         "CRPIX1": w.wcs.crpix[0], "CRPIX2": w.wcs.crpix[1], "CRVAL1": RA0, "CRVAL2": DEC0,
         "CD1_1": w.wcs.cd[0][0], "CD1_2": w.wcs.cd[0][1], "CD2_1": w.wcs.cd[1][0],
         "CD2_2": w.wcs.cd[1][1], "CUNIT1": "deg", "CUNIT2": "deg"}
    return h


def stamp_with_stars(roll, stars, flags=None):
    """A 61 x 61 stamp cut from the rolled frame around the target. `stars` are (dra_px,
    ddec_px, amplitude) on the sky: east and north offsets in 6.15 arcsec pixels."""
    w = frame_wcs(roll)
    x0 = y0 = 1019 - N // 2
    rng = np.random.default_rng(3)
    img = rng.normal(1.0, 0.05, (N, N))
    for de, dn, amp in stars:
        sky = (RA0 + de * PX, DEC0 + dn * PX)          # dec 0, so no cos(dec) factor
        sx, sy = w.all_world2pix([sky], 0)[0]
        img[int(round(sy)) - y0, int(round(sx)) - x0] += amp
    fl = np.zeros((N, N), "uint32") if flags is None else flags
    frame = SimpleNamespace(wcs_header=header(w))
    return SimpleNamespace(image=img.astype("float32"), flags=fl,
                           variance=np.full((N, N), 0.0025, "float32"), zodi=None,
                           x0=x0, y0=y0, wcs_header=header(w), frame=frame)


def req(fov=3.0):
    return SimpleNamespace(ra=RA0, dec=DEC0, fov_arcmin=fov, use_zodi=False)


def peak(gray):
    r, c = np.unravel_index(np.argmax(gray), gray.shape)
    return int(r), int(c)


@pytest.mark.parametrize("roll", [0.0, 2.4, 178.0, 358.0, 90.0])
def test_north_is_up_and_east_is_left_whatever_the_roll(roll):
    size = preview_size_px(3.0)
    mid = size // 2
    north = stamp_with_stars(roll, [(0, 12, 200.0)])
    east = stamp_with_stars(roll, [(12, 0, 200.0)])
    r, c = peak(render_stamp(north, req())[0])
    assert abs(c - mid) <= 1 and abs(r - (mid - 12)) <= 1          # above the centre
    r, c = peak(render_stamp(east, req())[0])
    assert abs(r - mid) <= 1 and abs(c - (mid - 12)) <= 1          # left of the centre


def test_a_blink_between_visits_with_different_roll_keeps_stars_still():
    a = peak(render_stamp(stamp_with_stars(358.0, [(5, -9, 200.0)]), req())[0])
    b = peak(render_stamp(stamp_with_stars(178.0, [(5, -9, 200.0)]), req())[0])
    assert abs(a[0] - b[0]) <= 1 and abs(a[1] - b[1]) <= 1


def test_the_picture_has_the_size_of_the_requested_field():
    gray, _ = render_stamp(stamp_with_stars(0.0, []), req(2.0))
    assert gray.shape == (21, 21) == (preview_size_px(2.0),) * 2


def test_pixels_outside_the_stamp_are_black():
    gray, _ = render_stamp(stamp_with_stars(0.0, []), req(10.0))   # 99 px grid, 61 px stamp
    assert gray.shape == (99, 99) and gray[0, 0] == 0 and gray[-1, -1] == 0
    assert gray[49, 49] > 0 or gray[49, 50] > 0 or gray[50, 50] > 0   # the sky itself is drawn


def test_stamp_without_wcs_is_only_flipped():
    st = stamp_with_stars(0.0, [])
    st.wcs_header, st.frame.wcs_header = None, None
    st.image[5, 30] += 200.0                    # row 5 is near the south edge in the array
    gray, _ = render_stamp(st, req())
    assert gray.shape == (N, N) and peak(gray) == (N - 1 - 5, 30)


def test_saturation_is_reported_from_the_original_stamp():
    flags = np.zeros((N, N), "uint32")
    flags[28:33, 28:33] = 1 << 1                       # OVERFLOW
    r = RealRenderer().render_png(stamp_with_stars(0.0, [], flags), req())
    assert "saturated" in r.flags_summary
    assert r.png[:8] == b"\x89PNG\r\n\x1a\n" and r.width == r.height == preview_size_px(3.0)
    

@pytest.mark.parametrize("roll", [0.0, 2.4, 90.0])
def test_a_saturated_core_is_white_after_regridding_not_a_hole(roll):
    flags = np.zeros((N, N), "uint32")
    flags[28:33, 28:33] = 1 << 1                       # OVERFLOW block at the stamp centre
    gray, _ = render_stamp(stamp_with_stars(roll, [], flags), req())
    mid = preview_size_px(3.0) // 2
    assert gray[mid, mid] == 255
    assert 15 <= int((gray == 255).sum()) <= 40        # the block, nothing else is white


def test_detector_orientation_keeps_the_stamp_as_it_is_with_no_black_corners():
    st = stamp_with_stars(30.0, [])
    st.image[5, 30] += 200.0                    # row 5 is near the south edge of the array
    r = SimpleNamespace(ra=RA0, dec=DEC0, fov_arcmin=3.0, use_zodi=False,
                        orient=Orientation.DETECTOR)
    gray, _ = render_stamp(st, r)
    assert gray.shape == (N, N) and peak(gray) == (N - 1 - 5, 30)  # only flipped
    north, _ = render_stamp(st, req(10.0))     # 99 px grid around a 61 px stamp: black borders
    assert (gray == 0).mean() < (north == 0).mean()


def test_north_is_still_the_default_when_the_request_has_no_orientation():
    gray, _ = render_stamp(stamp_with_stars(30.0, []), req(3.0))
    assert gray.shape == (preview_size_px(3.0),) * 2


def test_a_mostly_flagged_stamp_smaller_than_the_field_still_renders():
    flags = np.zeros((N, N), "uint32")
    flags[:40, :] = 1 << 7          # 65 percent dichroic: the stretch uses all usable pixels
    gray, _ = render_stamp(stamp_with_stars(0.0, [], flags), req(10.0))   # 99 px grid, 61 px
    assert gray.shape == (99, 99)
