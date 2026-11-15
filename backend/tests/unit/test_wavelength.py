import asyncio

import numpy as np
import pytest
from astropy.io import fits

from backend.adapters.wavelength import (
    SpectralWCS,
    TableWavelength,
    read_wave_hdul,
)

N = 9
CTRL = np.array([1, 256, 511, 766, 1021, 1275, 1530, 1785, 2040])   # like the real v27 table


class Bytes:
    def __init__(self, data):
        self.data = data

    @property
    def size(self):
        return len(self.data)

    def read(self, start, length):
        return self.data[start:start + length]


def lam_true(x, y):
    """Linear in y (0-based pixel), a little x dependence, like the real thing."""
    return 1.126 - 0.000189 * y - 0.000002 * x


def w_cards():
    """The real alternate-WCS 'W' keywords of a v27 IMAGE header (measured in X0)."""
    c = dict(WCSAXESW=2, WCSNAMEW="Approximated per-pixel wavelength and bandwidth",
             CNAME1W="Wavelength", CNAME2W="Bandwidth", CRPIX1W=1, CRPIX2W=1, CDELT1W=1,
             CDELT2W=1, CUNIT1W="um", CUNIT2W="um", CTYPE1W="WAVE-TAB", CTYPE2W="WAVE-TAB",
             CRVAL1W=1, CRVAL2W=1, PV1_3W=1, PV2_3W=2, PS1_0W="WCS-WAVE", PS2_0W="WCS-WAVE",
             PS1_1W="VALUES", PS2_1W="VALUES", PS1_2W="X", PS2_2W="Y")
    return c


def make_file(path, detector=1, pad_psf=40000):
    values = np.zeros((1, N, N, 2), "float32")      # numpy order (row=Y, col=X, (lam, bw))
    for j, y in enumerate(CTRL):
        for i, x in enumerate(CTRL):
            values[0, j, i] = (lam_true(x - 1, y - 1), 0.025)
    tab = fits.BinTableHDU.from_columns([
        fits.Column(name="X", format=f"{N}J", dim=f"({N})", array=CTRL[None, :].astype("int32")),
        fits.Column(name="Y", format=f"{N}J", dim=f"({N})", array=CTRL[None, :].astype("int32")),
        fits.Column(name="VALUES", format=f"{2 * N * N}E", dim=f"(2,{N},{N})", array=values),
    ], name="WCS-WAVE")
    tab.header["DETECTOR"] = str(detector)
    img = fits.ImageHDU(np.zeros((40, 40), "float32"), name="IMAGE")
    img.header.update(w_cards())
    # a big PSF-like extension before the table, so the tail read has to find the right one
    epsf = fits.BinTableHDU.from_columns([fits.Column(
        name="D", format="E", array=np.zeros(pad_psf, "float32"))], name="EPSF")
    fits.HDUList([fits.PrimaryHDU(), img, epsf, tab]).writeto(path, overwrite=True)
    return img.header


@pytest.fixture
def frame(tmp_path):
    p = tmp_path / "f.fits"
    hdr = make_file(p)
    return p, hdr


def test_tail_read_finds_the_wave_table_not_the_other_binary_table(frame):
    path, _ = frame
    hdul = read_wave_hdul(Bytes(path.read_bytes()))
    assert hdul[1].header["EXTNAME"] == "WCS-WAVE"
    assert hdul[1].data["VALUES"].shape == (1, N, N, 2)


def test_wavelength_matches_the_table_at_many_pixels(frame):
    path, hdr = frame
    s = SpectralWCS(hdr, read_wave_hdul(Bytes(path.read_bytes())))
    xs, ys = np.array([0, 5, 1019, 2039, 400, 1500]), np.array([0, 2039, 1019, 0, 1800, 300])
    lam, bw = s.at(xs, ys)
    # the exact astropy convention differs from our 0-based formula by
    # well under a resolution element (lam / 40 is about 0.02 um)
    assert np.allclose(lam, lam_true(xs, ys), atol=2e-3)
    assert np.allclose(bw, 0.025)
    assert s.detector == 1
    assert s.lambda_um(1019, 1019) == pytest.approx(lam_true(1019, 1019), abs=2e-3)
    assert lam[1] < lam[0]            # wavelength falls with y, like the real detector 1


def test_file_without_wave_table_is_a_clear_error(tmp_path):
    p = tmp_path / "x.fits"
    fits.HDUList([fits.PrimaryHDU(), fits.ImageHDU(np.zeros((9, 9), "float32"),
                                                   name="IMAGE")]).writeto(p)
    with pytest.raises(ValueError):
        read_wave_hdul(Bytes(p.read_bytes()))


class Frame:
    def __init__(self, fid, detector, version):
        self.frame_id, self.detector, self.version = fid, detector, version
        self.uri = f"s3://x/{fid}"


def test_one_read_per_detector_and_version(frame, monkeypatch):
    path, hdr = frame
    data = path.read_bytes()
    opened = []

    import backend.adapters.wavelength as w
    monkeypatch.setattr(w, "load_spectral_wcs",
                        lambda src: SpectralWCS(hdr, read_wave_hdul(src)))

    def opener(f):
        opened.append(f.frame_id)
        return Bytes(data)

    t = TableWavelength(opener=opener)
    frames = [Frame(f"a{i}", 1, "v27") for i in range(20)] + [Frame("b", 1, "v20")]
    asyncio.run(t.prefetch(frames))
    assert sorted(opened) == ["a0", "b"]
    assert t.lambda_um(frames[5], 1019, 1019) == pytest.approx(lam_true(1019, 1019), abs=2e-3)
    assert sorted(opened) == ["a0", "b"]            # nothing more was read


def test_tables_survive_a_restart_through_the_disk_cache(frame, tmp_path, monkeypatch):
    path, hdr = frame
    data = path.read_bytes()
    import backend.adapters.wavelength as w
    monkeypatch.setattr(w, "load_spectral_wcs",
                        lambda src: SpectralWCS(hdr, read_wave_hdul(src)))
    first = TableWavelength(opener=lambda f: Bytes(data), cache_dir=tmp_path / "wl")
    want = first.lambda_um(Frame("a", 1, "v27-2026-222"), 1019, 300)
    assert len(list((tmp_path / "wl").glob("*.fits"))) == 1

    def boom(f):
        raise AssertionError("must not read the network again")

    second = TableWavelength(opener=boom, cache_dir=tmp_path / "wl")      # a new process
    assert second.lambda_um(Frame("b", 1, "v27-2026-222"), 1019, 300) == pytest.approx(want)


def test_sky_distortion_keywords_do_not_leak_into_the_wavelength(frame):
    """Real headers carry SIP coefficients for the sky WCS. They must not move the wavelength."""
    path, hdr = frame
    wave = read_wave_hdul(Bytes(path.read_bytes()))
    plain = SpectralWCS(hdr, wave)
    bent = hdr.copy()
    bent.update({"A_ORDER": 2, "B_ORDER": 2, "A_2_0": 3e-4, "A_0_2": 3e-4, "B_2_0": 3e-4,
                 "B_0_2": 3e-4, "CTYPE1": "RA---TAN", "CTYPE2": "DEC--TAN"})
    xs, ys = np.array([0, 1019, 2039, 400]), np.array([0, 1019, 2039, 1800])
    assert np.array_equal(SpectralWCS(bent, wave).at(xs, ys)[0], plain.at(xs, ys)[0])
