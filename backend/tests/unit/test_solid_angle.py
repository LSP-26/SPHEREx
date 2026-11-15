import numpy as np
import pytest
from astropy.io import fits

from backend.adapters.fits_layout import FileSource
from backend.adapters.solid_angle import (
    MAPS,
    SolidAngleMap,
    UnknownRelease,
    map_key,
)

NY, NX = 60, 80


def write_map(path, value=37.8, bunit="arcsec2", nan_at=None):
    yy, xx = np.mgrid[0:NY, 0:NX]
    data = (value + 0.001 * xx + 0.002 * yy).astype("float32")
    if nan_at is not None:
        data[nan_at] = np.nan
    img = fits.ImageHDU(data, name="IMAGE")
    if bunit is not None:
        img.header["BUNIT"] = bunit
    fits.HDUList([fits.PrimaryHDU(), img]).writeto(path, overwrite=True)
    return data


class Files:
    """An opener that serves local files by S3 key, and counts how often it is asked."""

    def __init__(self, tmp_path):
        self.dir, self.files, self.opened, self.data = tmp_path, {}, [], {}

    def add(self, release, detector, **kw):
        key = map_key(release, detector)
        path = self.dir / key.replace("/", "_")
        self.data[key] = write_map(path, **kw)
        self.files[key] = path
        return self.data[key]

    def __call__(self, url):
        key = url.split(".amazonaws.com/")[1]
        self.opened.append(key)
        return FileSource(self.files[key])


@pytest.fixture
def files(tmp_path):
    return Files(tmp_path)


def test_the_keys_follow_the_real_layout_and_each_release_has_its_own_calibration():
    assert map_key("QR2", 1) == ("qr2/solid_angle_pixel_map/cal-sapm-v2-2025-164/1/"
                                 "solid_angle_pixel_map_D1_spx_cal-sapm-v2-2025-164.fits")
    assert map_key("qr3", 4) == ("qr3/solid_angle_pixel_map/cal-sapm-v3-2026-191/4/"
                                 "solid_angle_pixel_map_D4_spx_cal-sapm-v3-2026-191.fits")
    assert set(MAPS) == {"QR2", "QR3"}


def test_an_unknown_release_or_detector_fails_loudly():
    with pytest.raises(UnknownRelease, match="QR9"):
        map_key("QR9", 1)
    with pytest.raises(UnknownRelease):
        SolidAngleMap(opener=lambda u: None).region("QR1", 1, 0, 0, 1, 1)
    for bad in (0, 7, 2.5, "1"):
        with pytest.raises(ValueError):
            map_key("QR2", bad)


def test_a_region_equals_astropy_exactly(files):
    data = files.add("QR2", 1)
    got = SolidAngleMap(opener=files).region("QR2", 1, 20, 10, 6, 5)
    assert got.shape == (5, 6) and got.dtype == np.float64
    assert np.array_equal(got, data[10:15, 20:26].astype("float64"))


def test_at_reads_one_pixel_and_the_release_picks_the_calibration(files):
    files.add("QR2", 3, value=37.8)
    files.add("QR3", 3, value=38.5)
    sam = SolidAngleMap(opener=files)
    assert sam.at("QR2", 3, 20, 10) == pytest.approx(37.8 + 0.001 * 20 + 0.002 * 10, abs=1e-4)
    assert sam.at("QR3", 3, 20.2, 9.8) == pytest.approx(38.5 + 0.001 * 20 + 0.002 * 10, abs=1e-4)
    assert sam.at("qr3", 3, 20, 10) == sam.at("QR3", 3, 20, 10)       # case does not matter


def test_a_map_is_opened_once_and_each_detector_has_its_own(files):
    files.add("QR2", 1)
    files.add("QR2", 4, value=37.0)
    sam = SolidAngleMap(opener=files)
    for _ in range(5):
        sam.region("QR2", 1, 0, 0, 4, 4)
    assert len(files.opened) == 1
    sam.region("QR2", 4, 0, 0, 4, 4)
    assert len(files.opened) == 2


def test_wrong_units_and_implausible_values_are_refused(files):
    files.add("QR2", 1, bunit="sr")
    with pytest.raises(ValueError, match="BUNIT"):
        SolidAngleMap(opener=files).region("QR2", 1, 0, 0, 2, 2)
    files.add("QR2", 2, value=8.9e-10, bunit=None)       # steradians, no unit card
    with pytest.raises(ValueError, match="plausible"):
        SolidAngleMap(opener=files).region("QR2", 2, 0, 0, 2, 2)
    files.add("QR2", 3, bunit="arcsec^2")                # another spelling is fine
    assert SolidAngleMap(opener=files).at("QR2", 3, 1, 1) > 37


def test_regions_outside_the_map_or_with_bad_values_are_refused(files):
    files.add("QR2", 1, nan_at=(5, 5))
    sam = SolidAngleMap(opener=files)
    for args in [(75, 0, 10, 5), (0, 58, 5, 5), (-1, 0, 3, 3), (0, 0, 0, 3)]:
        with pytest.raises(ValueError, match="outside"):
            sam.region("QR2", 1, *args)
    with pytest.raises(ValueError, match="finite"):
        sam.region("QR2", 1, 0, 0, 10, 10)
    assert sam.region("QR2", 1, 10, 10, 5, 5).shape == (5, 5)


async def test_the_async_version_gives_the_same_answer(files):
    files.add("QR3", 6)
    sam = SolidAngleMap(opener=files)
    a = await sam.region_async("QR3", 6, 10, 20, 7, 3)
    assert np.array_equal(a, sam.region("QR3", 6, 10, 20, 7, 3))
