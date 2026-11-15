import numpy as np
import pytest

from backend.pipeline.clean import (
    FATAL_MASK,
    SATURATION_MASK,
    clean_arrays,
    fit_plane,
    flags_summary,
    robust_sigma,
)
from backend.pipeline.render import _png, render_array, render_array_covered

N = 47
rng = np.random.default_rng(7)


def sky(noise=0.05, tilt=(0.002, -0.001), level=3.0):
    yy, xx = np.mgrid[0:N, 0:N] - (N - 1) / 2
    return (level + tilt[0] * xx + tilt[1] * yy + rng.normal(0, noise, (N, N))).astype("float32")


def test_plane_is_recovered_despite_a_bright_star():
    img = sky()
    img[20:25, 20:25] += 50.0
    plane = fit_plane(img.astype("float64"), np.ones((N, N), bool))
    truth = img[0:5, 0:5].mean()
    assert abs(plane[2, 2] - truth) < 0.05
    assert abs(plane[23, 23] - 3.0) < 0.05          # star did not drag the fit up


def test_zodi_is_subtracted_only_when_asked():
    zodi = np.full((N, N), 10.0, "float32")
    img = sky(level=0.0) + zodi
    flags, var = np.zeros((N, N), "uint32"), np.full((N, N), 0.0025, "float32")
    a = clean_arrays(img, flags, var, zodi, use_zodi=True)
    b = clean_arrays(img, flags, var, zodi, use_zodi=False)
    assert a.zodi_subtracted and not b.zodi_subtracted
    assert abs(np.nanmedian(a.image)) < 0.02 and abs(np.nanmedian(b.image)) < 0.02  # plane absorbs
    assert abs(np.nanmedian(a.background)) < 0.1 and np.nanmedian(b.background) > 9


def test_fatal_flags_nans_and_bad_variance_are_masked():
    img, flags = sky(), np.zeros((N, N), "uint32")
    var = np.full((N, N), 0.0025, "float32")
    flags[1, 1] = 1 << 0
    flags[2, 2] = 1 << 3          # bit 3 is not in the fatal list
    img[3, 3] = np.nan
    var[4, 4] = 0.0
    c = clean_arrays(img, flags, var, None)
    assert not c.good[1, 1] and c.good[2, 2] and not c.good[3, 3] and not c.good[4, 4]
    assert np.isnan(c.image[1, 1]) and np.isnan(c.variance[4, 4])
    assert c.frac_masked == pytest.approx(3 / N**2)
    assert FATAL_MASK & (1 << 0) and not FATAL_MASK & (1 << 3)


def test_high_bit_flags_do_not_break_the_mask():
    flags = np.zeros((N, N), "uint32")
    flags[0, 0] = 1 << 29
    flags[0, 1] = 1 << 31         # not fatal
    c = clean_arrays(sky(), flags, np.full((N, N), 0.0025, "float32"))
    assert not c.good[0, 0] and c.good[0, 1]


def test_noise_estimate_and_all_masked_do_not_crash():
    c = clean_arrays(sky(noise=0.05), np.zeros((N, N), "uint32"), np.full((N, N), 1.0))
    assert c.noise == pytest.approx(0.05, rel=0.15)
    dead = clean_arrays(sky(), np.full((N, N), FATAL_MASK & ~SATURATION_MASK, "uint32"),
                        np.ones((N, N)))
    assert dead.frac_masked == 1.0 and robust_sigma(dead.image) == 0.0
    assert render_array(dead).max() == 0


def test_summary_is_empty_for_a_clean_frame_and_notes_heavy_masking():
    ok = clean_arrays(sky(), np.zeros((N, N), "uint32"), np.ones((N, N)))
    assert flags_summary(ok) == []
    flags = np.zeros((N, N), "uint32")
    flags[:10] = 1
    assert "flagged" in flags_summary(clean_arrays(sky(), flags, np.ones((N, N))))[0]


def test_render_is_8_bit_shows_the_star_and_fills_a_broken_pixel_from_its_neighbours():
    img = sky()
    img[23, 23] += 30.0
    img[0, 0] = -50.0                              # a COLD pixel: its value is meaningless
    flags = np.zeros((N, N), "uint32")
    flags[0, 0] = 1 << 11
    g = render_array(clean_arrays(img, flags, np.full((N, N), 0.0025)))
    assert g.dtype == np.uint8 and g[23, 23] > 240
    assert abs(int(g[0, 0]) - int(np.median(g))) < 40   # filled in, not a black hole or a spike
    assert np.median(g) < 60                       # sky is dark, not washed out
    png = _png(g)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_a_single_overflow_pixel_is_not_labelled_saturated():
    flags = np.zeros((N, N), "uint32")
    flags[10, 10] = 1 << 1
    assert "saturated" not in flags_summary(clean_arrays(sky(), flags, np.ones((N, N))))
    flags[10:13, 10:13] = 1 << 1
    assert "saturated" in flags_summary(clean_arrays(sky(), flags, np.ones((N, N))))


def test_saturated_pixels_are_drawn_white_and_a_bright_bloom_pixel_is_shown_by_its_value():
    flags = np.zeros((N, N), "uint32")
    img = sky()
    flags[20:23, 20:23] = 1 << 1          # OVERFLOW: the core
    flags[20, 30] = 1 << 26               # BLOOM: flagged, but not saturated
    img[20, 30] = 400.0                   # and very bright, as bloom is in real frames
    c = clean_arrays(img, flags, np.full((N, N), 0.0025, "float32"))
    assert c.saturated.sum() == 9 and c.saturated[21, 21] and not c.saturated[20, 30]
    assert not c.good[21, 21] and not c.good[20, 30]   # still excluded from measurements
    g = render_array(c)
    assert (g[20:23, 20:23] == 255).all()
    assert g[20, 30] == 255


def test_saturation_mask_is_empty_without_overflow_flags():
    c = clean_arrays(sky(), np.zeros((N, N), "uint32"), np.ones((N, N)))
    assert not c.saturated.any() and c.frac_saturated == 0.0


def test_label_says_the_dichroic_band_is_less_reliable_only_when_there_is_one():
    flags = np.zeros((N, N), "uint32")
    c0 = clean_arrays(sky(), flags, np.ones((N, N)))
    assert not any("dichroic" in n for n in flags_summary(c0))
    flags[-5:, :] |= 1 << 7                      # a strip of DICHROIC rows along one edge
    c = clean_arrays(sky(), flags, np.ones((N, N)))
    assert c.frac_dichroic == pytest.approx(5 / N)
    assert "dichroic band, less reliable" in flags_summary(c)
    flags[:] = 0
    flags[0, 0] |= 1 << 7                        # a single stray pixel is not a band
    assert not any("dichroic" in n for n in flags_summary(
        clean_arrays(sky(), flags, np.ones((N, N)))))


def test_flagged_pixels_with_sensible_values_are_shown_but_not_used_for_measurements():
    flags = np.zeros((N, N), "uint32")
    img = sky()
    flags[20:23, 20:23] |= (1 << 0) | (1 << 2)   # TRANSIENT + SUR_ERROR: the ring round a star
    img[20:23, 20:23] = 400.0
    flags[30:33, 30:33] |= 1 << 28               # HALO at about sky level
    c = clean_arrays(img, flags, np.ones((N, N)))
    assert not c.good[20:23, 20:23].any() and not c.good[30:33, 30:33].any()
    gray = render_array(c)
    assert (gray[20:23, 20:23] == 255).all()     # shown, very bright
    assert gray[30:33, 30:33].max() < 120        # shown at sky level, not white and not black


def test_detector_defects_are_hidden_and_small_holes_are_filled():
    flags = np.zeros((N, N), "uint32")
    img = sky()
    for bit, (y, x) in zip((6, 9, 10, 11), ((5, 5), (5, 15), (15, 5), (15, 15)), strict=True):
        flags[y, x] = 1 << bit                   # NONFUNC, MISSING_DATA, HOT, COLD
        img[y, x] = -80.0 if bit == 11 else 9000.0
    c = clean_arrays(img, flags, np.ones((N, N)))
    assert np.isfinite(c.display).all()          # all four are filled from their neighbours
    assert np.abs(c.display).max() < 10          # not -80, not 9000
    assert c.frac_hidden == pytest.approx(4 / N**2)


def test_a_large_hole_keeps_a_black_middle():
    flags = np.zeros((N, N), "uint32")
    flags[10:31, 10:31] = 1 << 11                # 21 x 21 COLD block: too wide to fill
    c = clean_arrays(sky(), flags, np.ones((N, N)))
    assert not np.isfinite(c.display[20, 20]) and render_array(c)[20, 20] == 0
    assert np.isfinite(c.display[11, 11])        # the rim is filled


def test_the_dichroic_band_is_shown_with_its_stars_not_a_black_block():
    flags = np.zeros((N, N), "uint32")
    img = sky()
    flags[:6, :] |= 1 << 7                       # the band: rows 0 to 5
    img[2, 10] += 30.0                           # a star inside it
    c = clean_arrays(img, flags, np.ones((N, N)))
    gray = render_array(c)
    assert gray[2, 10] > 240                     # the star is there
    assert np.median(gray[:6, :]) < 60 and np.isfinite(c.display[:6]).all()


def test_a_saturated_core_with_no_number_is_white():
    flags = np.zeros((N, N), "uint32")
    img = sky()
    flags[20:23, 20:23] = 1 << 1
    img[20:23, 20:23] = np.nan                   # the core of a real frame has no value
    c = clean_arrays(img, flags, np.ones((N, N)))
    assert (render_array(c)[20:23, 20:23] == 255).all()


def test_a_stamp_in_a_known_bright_band_gets_a_note_and_others_do_not():
    from backend.pipeline.clean import band_note
    assert band_note(1, 100, 49) and band_note(1, 270, 10) and band_note(2, 1990, 49)
    assert band_note(1, 400, 49) is None and band_note(1, 0, 40) is None
    assert band_note(3, 100, 49) is None and band_note(2, 500, 49) is None


def test_a_hole_inside_a_bright_blob_is_white_but_a_hole_in_dark_sky_or_at_the_edge_is_black():
    flags = np.zeros((N, N), "uint32")
    img = sky()
    img[11:30, 11:30] += 40.0                     # a bright star ...
    img[16:25, 16:25] = np.nan                    # ... no number in its core (too wide to fill)
    img[35:45, 2:12] = np.nan                     # a big hole in dark sky (nothing is bright there)
    img[0:10, 30:40] = np.nan                     # a hole on the edge
    c = clean_arrays(img, flags, np.full((N, N), 0.0025, "float32"))
    g = render_array(c)
    assert (g[18:23, 18:23] >= 254).all()   # bright all through, no black hole
    assert g[38, 5] == 0 and g[3, 35] == 0


def test_png_with_coverage_is_grey_plus_alpha_and_transparent_where_there_is_no_data():
    import struct
    import zlib
    g = np.full((4, 6), 90, "uint8")
    cov = np.ones((4, 6), bool)
    cov[0, :] = False
    png = _png(g, cov)
    assert struct.unpack(">IIBBBBB", png[16:29])[3] == 4          # colour type 4: grey + alpha
    idat = png[png.index(b"IDAT") + 4:png.index(b"IEND") - 8]
    raw = zlib.decompress(idat)
    rows = [raw[i * 13 + 1:(i + 1) * 13] for i in range(4)]   # filter byte, then 6 x (grey, alpha)
    assert rows[0][1::2] == bytes(6)                                # no data: transparent
    assert rows[1][1::2] == bytes([255] * 6) and rows[1][0::2] == bytes([90] * 6)
    assert struct.unpack(">IIBBBBB", _png(g)[16:29])[3] == 0        # no coverage given: plain grey


def test_without_fill_broken_pixels_stay_holes_and_no_core_is_invented():
    flags = np.zeros((N, N), "uint32")
    img = sky()
    flags[5, 5] = 1 << 11                          # a COLD pixel
    img[11:30, 11:30] += 40.0
    img[16:25, 16:25] = np.nan                     # a core with no number
    c = clean_arrays(img, flags, np.ones((N, N)), fill=False)
    gray, covered = render_array_covered(c, fill=False)
    assert not covered[5, 5] and not covered[20, 20]       # no data: left empty (transparent)
    assert covered[8, 8] and covered[12, 12]
    filled = clean_arrays(img, flags, np.ones((N, N)))
    assert render_array_covered(filled)[1][5, 5]           # the default fills it


def test_a_mostly_flagged_stamp_is_not_washed_grey_by_its_few_good_pixels():
    rng = np.random.default_rng(3)
    n = 60
    flags = np.zeros((n, n), "uint32")
    flags[2:, :] |= 1 << 7                          # all but two rows are in the dichroic band
    img = rng.normal(0.0, 3.0, (n, n))              # the band is noisier than the quiet good rows
    img[:2, :] = rng.normal(0.0, 0.3, (2, n))
    img[30, 30] += 120.0                            # a star in the band
    c = clean_arrays(img, flags, np.full((n, n), 1.0))
    assert c.good.mean() < 0.05 and c.display_noise > 2.0
    g = render_array(c)
    assert np.median(g) < 70 and g[30, 30] > 230     # dark sky, bright star, not a grey wash


def test_a_normal_stamp_still_uses_its_good_pixels_for_the_stretch():
    c = clean_arrays(sky(), np.zeros((N, N), "uint32"), np.ones((N, N)))
    assert c.display_noise == 0.0 and c.display_basis is None
