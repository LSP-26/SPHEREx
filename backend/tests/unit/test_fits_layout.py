import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np
import pytest
from astropy.io import fits

from backend.adapters.fits_layout import (
    FileSource,
    HttpRangeSource,
    RangeNotSupported,
    read_layout,
    read_rows,
)

NY, NX = 60, 80


@pytest.fixture(scope="module")
def fits_path(tmp_path_factory):
    rng = np.random.default_rng(1)
    path = tmp_path_factory.mktemp("f") / "frame.fits"
    img = rng.normal(size=(NY, NX)).astype("float32")
    img[5, 5] = np.nan
    flags = rng.integers(0, 2**31, size=(NY, NX)).astype("uint32")   # stored with BZERO
    var = rng.random((NY, NX)).astype("float32")
    zodi = rng.random((NY, NX)).astype("float32")
    wave = fits.BinTableHDU.from_columns(
        [fits.Column(name="x", format="E", array=np.arange(7, dtype="float32"))], name="WCS-WAVE")
    fits.HDUList([
        fits.PrimaryHDU(),
        fits.ImageHDU(img, name="IMAGE"), fits.ImageHDU(flags, name="FLAGS"),
        fits.ImageHDU(var, name="VARIANCE"), fits.ImageHDU(zodi, name="ZODI"),
        wave, fits.ImageHDU(rng.random((9, 9)).astype("float32"), name="PSF"),
    ]).writeto(path)
    return path


def test_layout_finds_every_hdu(fits_path):
    layout = read_layout(FileSource(fits_path))
    assert [h.extname for h in layout] == [None, "IMAGE", "FLAGS", "VARIANCE", "ZODI",
                                           "WCS-WAVE", "PSF"]
    flags = layout[2]
    assert flags.bitpix == 32 and flags.bzero == 2147483648 and flags.shape == (NY, NX)
    assert flags.row_bytes == NX * 4


def test_row_bands_match_astropy_for_every_plane(fits_path):
    src = FileSource(fits_path)
    layout = {h.extname: h for h in read_layout(src)}
    with fits.open(fits_path) as hdul:
        for name in ("IMAGE", "FLAGS", "VARIANCE", "ZODI"):
            got = read_rows(src, layout[name], 20, 31)
            ref = np.asarray(hdul[name].data[20:31], dtype="float64")
            assert np.array_equal(got, ref, equal_nan=True), name
        # the NaN row too
        assert np.array_equal(read_rows(src, layout["IMAGE"], 0, 10),
                              np.asarray(hdul["IMAGE"].data[0:10], "float64"), equal_nan=True)


def test_bad_requests_are_refused(fits_path):
    src = FileSource(fits_path)
    layout = {h.extname: h for h in read_layout(src)}
    with pytest.raises(ValueError):
        read_rows(src, layout["IMAGE"], 50, 70)
    with pytest.raises(ValueError):
        read_rows(src, layout["WCS-WAVE"], 0, 1)


def _serve(path, honour_range=True):
    data = path.read_bytes()

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            rng = self.headers.get("Range")
            if rng and honour_range:
                a, b = rng.removeprefix("bytes=").split("-")
                a, b = int(a), min(int(b), len(data) - 1)
                body = data[a:b + 1]
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {a}-{b}/{len(data)}")
            else:
                body = data
                self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}/frame.fits"


def test_over_http_uses_small_range_reads(fits_path):
    srv, url = _serve(fits_path)
    try:
        src = HttpRangeSource(url)
        layout = {h.extname: h for h in read_layout(src)}
        got = read_rows(src, layout["VARIANCE"], 10, 20)
        with fits.open(fits_path) as hdul:
            assert np.array_equal(got, np.asarray(hdul["VARIANCE"].data[10:20], "float64"))
        assert src.size == fits_path.stat().st_size
        assert src.requests > 1 and src.bytes_read < fits_path.stat().st_size
    finally:
        srv.shutdown()


def test_server_that_ignores_range_is_detected(fits_path):
    srv, url = _serve(fits_path, honour_range=False)
    try:
        with pytest.raises(RangeNotSupported):
            HttpRangeSource(url).read(0, 10)
    finally:
        srv.shutdown()
