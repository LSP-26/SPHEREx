import numpy as np
import pytest
from astropy.wcs import WCS, Sip

from backend.pipeline.grid import make_target_wcs, regrid, regrid_mask, stamp_wcs

N = 47
PX = 6.15 / 3600


def frame_wcs(crval=(10.0, 0.0), crpix=(1020.0, 1020.0), rot_deg=0.0, sip=False):
    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN-SIP", "DEC--TAN-SIP"] if sip else ["RA---TAN", "DEC--TAN"]
    w.wcs.crval, w.wcs.crpix = list(crval), list(crpix)
    c, s = np.cos(np.radians(rot_deg)), np.sin(np.radians(rot_deg))
    w.wcs.cd = [[-PX * c, PX * s], [PX * s, PX * c]]
    if sip:
        a, b = np.zeros((3, 3)), np.zeros((3, 3))
        a[2, 0], a[1, 1], a[0, 2] = 2e-7, -1e-7, 3e-7
        b[2, 0], b[1, 1], b[0, 2] = -1e-7, 2e-7, 1e-7
        w.sip = Sip(a, b, None, None, np.array(w.wcs.crpix))
    w.wcs.set()
    return w


def star(shape, x, y, amp=100.0, sigma=1.2):
    yy, xx = np.mgrid[0:shape[0], 0:shape[1]]
    return amp * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * sigma**2))


def centroid(img):
    a = np.nan_to_num(img, nan=0.0)
    yy, xx = np.mgrid[0:a.shape[0], 0:a.shape[1]]
    return (a * xx).sum() / a.sum(), (a * yy).sum() / a.sum()


def test_target_wcs_centre_is_the_middle_pixel():
    w = make_target_wcs(10.0, 5.0, 47)
    x, y = w.all_world2pix([[10.0, 5.0]], 0)[0]
    assert (x, y) == pytest.approx((23.0, 23.0), abs=1e-6)
    assert abs(w.wcs.cdelt[1]) * 3600 == pytest.approx(6.15)


@pytest.mark.parametrize("rot,sip", [(0.0, False), (37.0, False), (0.0, True), (90.0, True)])
def test_a_star_lands_on_the_same_sky_position_even_if_the_frame_is_rotated(rot, sip):
    # a frame whose pixel (1020+30, 1020-10) sits at the target; it may be rotated or have SIP
    fw = frame_wcs(crval=(10.0, 0.0), rot_deg=rot, sip=sip)
    target = make_target_wcs(10.0, 0.0, N)
    sky = target.all_pix2world([[26.3, 21.6]], 0)[0]             # star position in the output
    sx, sy = fw.all_world2pix([sky], 0)[0]
    x0, y0 = int(round(sx)) - 23, int(round(sy)) - 23
    sw = stamp_wcs(fw, x0, y0, (N, N))
    img = star((N, N), sx - x0, sy - y0)
    out = regrid(img, np.full((N, N), 0.01), sw, target, (N, N))
    cx, cy = centroid(out.image)
    assert (cx, cy) == pytest.approx((26.3, 21.6), abs=0.15)


@pytest.mark.parametrize("method", ["exact", "nearest"])
def test_a_flat_sky_stays_flat(method):
    fw = frame_wcs(sip=True)
    sw = stamp_wcs(fw, 1000, 1000, (N, N))
    target = make_target_wcs(*fw.all_pix2world([[1023.0, 1023.0]], 0)[0], 31)
    out = regrid(np.full((N, N), 4.0), np.full((N, N), 0.25), sw, target, (31, 31), method)
    assert np.nanmin(out.image) == pytest.approx(4.0) and np.nanmax(out.image) == pytest.approx(4.0)
    assert np.nanmax(out.variance) == pytest.approx(0.25)


def test_one_masked_pixel_does_not_spread_into_its_neighbours():
    w = frame_wcs()
    target = make_target_wcs(*w.all_pix2world([[1019.0, 1019.0]], 0)[0], N)
    sw = stamp_wcs(w, 996, 996, (N, N))
    img = np.ones((N, N))
    img[20, 20] = np.nan
    out = regrid(img, np.ones((N, N)), sw, target, (N, N))
    assert np.isnan(out.image).sum() == 1
    assert np.isnan(out.image[20, 20]) and out.coverage[20, 20] < 1e-7


def test_outside_the_stamp_is_nan_with_zero_coverage():
    w = frame_wcs()
    sw = stamp_wcs(w, 1000, 1000, (N, N))
    centre = w.all_pix2world([[1023.0, 1023.0]], 0)[0]
    out = regrid(np.ones((N, N)), np.ones((N, N)), sw, make_target_wcs(*centre, 101), (101, 101))
    assert np.isnan(out.image[0, 0]) and out.coverage[0, 0] == 0
    assert np.isfinite(out.image[50, 50])


def test_nearest_does_not_smooth_the_data():
    w = frame_wcs()
    sw = stamp_wcs(w, 996, 996, (N, N))
    target = make_target_wcs(*w.all_pix2world([[1019.0, 1019.0]], 0)[0], N)
    img = np.random.default_rng(0).normal(size=(N, N))
    out = regrid(img, np.ones((N, N)), sw, target, (N, N), "nearest")
    assert np.allclose(out.image, img, atol=1e-5)


def test_unknown_method_is_refused():
    w = frame_wcs()
    with pytest.raises(ValueError):
        regrid(np.ones((5, 5)), np.ones((5, 5)), w, w, (5, 5), "bilinear")


def test_stamp_wcs_agrees_with_the_full_frame_including_sip():
    fw = frame_wcs(sip=True)
    sw = stamp_wcs(fw, 1500, 300, (N, N))
    pts = np.array([[0.0, 0.0], [46.0, 46.0], [10.0, 30.0]])
    assert np.allclose(fw.all_pix2world(pts + [1500, 300], 0), sw.all_pix2world(pts, 0), atol=1e-12)


def test_a_mask_keeps_its_shape_on_the_same_grid_and_is_false_outside_the_stamp():
    w = frame_wcs()
    sw = stamp_wcs(w, 996, 996, (N, N))
    centre = w.all_pix2world([[1019.0, 1019.0]], 0)[0]
    mask = np.zeros((N, N), bool)
    mask[10:13, 20:24] = True
    same = regrid_mask(mask, sw, make_target_wcs(*centre, N), (N, N))
    assert np.array_equal(same, mask)
    big = regrid_mask(mask, sw, make_target_wcs(*centre, 101), (101, 101))
    assert big.shape == (101, 101) and not big[0, 0] and big.sum() == mask.sum()
