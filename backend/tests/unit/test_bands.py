import numpy as np

from backend.pipeline.bands import band_stat, summarize

ROWS = np.arange(2040)
BAND = (60, 270)


def profile(amp, base=0.05, seed=0):
    rng = np.random.default_rng(seed)
    med = base + rng.normal(0, 0.003, 2040)
    med[60:271] += amp * np.sin(np.linspace(0.2, np.pi - 0.2, 211))
    return med, np.full(2040, 1800)


def stats(amps):
    return [band_stat(ROWS, *profile(a, seed=i), BAND) for i, a in enumerate(amps)]


def test_amplitude_and_baseline_are_measured_against_the_rows_outside_the_band():
    s = band_stat(ROWS, *profile(0.5), BAND)
    assert s.baseline == np.float64(s.baseline) and abs(s.baseline - 0.05) < 0.005
    assert 0.3 < s.amplitude < 0.5 and 0.45 < s.peak < 0.52


def test_rows_with_few_pixels_do_not_count():
    med, n = profile(0.5)
    assert band_stat(ROWS, med, np.full(2040, 10), BAND) is None


def test_a_steady_band_reads_as_a_calibration_offset():
    r = summarize(stats([0.50, 0.52, 0.49, 0.51, 0.50]))
    assert r["shape_r"] > 0.9 and r["spread"] < 0.2 and "calibration offset" in r["verdict"]


def test_a_band_in_the_same_rows_with_a_changing_amplitude_is_not_a_fixed_offset():
    r = summarize(stats([0.2, 1.0, 0.5, 2.0, 0.3]))
    assert r["spread"] > 0.2 and "scattered light" in r["verdict"]


def test_one_frame_with_a_bright_star_does_not_decide_it():
    r = summarize(stats([0.50, 0.52, 0.49, 0.51, 4.5]))
    assert r["spread"] < 0.2


def test_no_band_and_too_few_frames():
    assert "no bright band" in summarize(stats([0.0, 0.0, 0.0]))["verdict"]
    assert "at least 3" in summarize(stats([0.5, 0.5]))["verdict"]
