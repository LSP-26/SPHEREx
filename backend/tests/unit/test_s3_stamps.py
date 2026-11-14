import asyncio
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import pytest
from astropy.io import fits

from backend.adapters.cache import HostLimiter, UpstreamError
from backend.adapters.fits_layout import FileSource, RangeNotSupported, read_layout
from backend.adapters.s3_stamps import OutOfFrame, S3StampSource, https_url
from contracts import ErrorCode, FrameRef, Plane, PlaneSpec, SkyPosition
from contracts.interfaces import StampSource

NY, NX = 60, 80
CRPIX = (40.0, 30.0)   # FITS 1-based, so the target sits at 0-based pixel (39, 29)
WCS = {"NAXIS": 2, "NAXIS1": NX, "NAXIS2": NY, "CTYPE1": "RA---TAN", "CTYPE2": "DEC--TAN",
       "CRPIX1": CRPIX[0], "CRPIX2": CRPIX[1], "CRVAL1": 10.0, "CRVAL2": 0.0,
       "CDELT1": -6.15 / 3600, "CDELT2": 6.15 / 3600, "CUNIT1": "deg", "CUNIT2": "deg"}
CENTRE = SkyPosition(ra=10.0, dec=0.0)


@pytest.fixture(scope="module")
def fits_path(tmp_path_factory):
    rng = np.random.default_rng(3)
    path = tmp_path_factory.mktemp("s3") / "frame.fits"
    flags = rng.integers(-2**31, 2**31 - 1, size=(NY, NX)).astype("int32")  # signed, like SPHEREx
    fits.HDUList([
        fits.PrimaryHDU(),
        fits.ImageHDU(rng.normal(size=(NY, NX)).astype("float32"), name="IMAGE"),
        fits.ImageHDU(flags, name="FLAGS"),
        fits.ImageHDU(rng.random((NY, NX)).astype("float32"), name="VARIANCE"),
        fits.ImageHDU(rng.random((NY, NX)).astype("float32"), name="ZODI"),
        fits.ImageHDU(rng.random((5, 9, 9)).astype("float32"), name="PSF"),
    ]).writeto(path)
    return path


class Server:
    """Local stand-in for S3: honours Range, logs requests, can inject failures."""

    def __init__(self, path, honour_range=True, fail_first=0, fail_status=503, delay=0.0):
        self.data = path.read_bytes()
        self.log, self.fail_left, self.active, self.peak = [], fail_first, 0, 0
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                outer.active += 1
                outer.peak = max(outer.peak, outer.active)
                try:
                    time.sleep(delay)
                    if outer.fail_left > 0:
                        outer.fail_left -= 1 if fail_status != 0 else 0
                        self.send_response(fail_status)
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    rng = self.headers.get("Range")
                    if rng and honour_range:
                        a, b = rng.removeprefix("bytes=").split("-")
                        a, b = int(a), min(int(b), len(outer.data) - 1)
                        outer.log.append((a, b - a + 1))
                        body = outer.data[a:b + 1]
                        self.send_response(206)
                        self.send_header("Content-Range", f"bytes {a}-{b}/{len(outer.data)}")
                    else:
                        body = outer.data
                        self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                finally:
                    outer.active -= 1

            def log_message(self, *a):
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_port}/frame.fits"

    def close(self):
        self.srv.shutdown()


def frame_for(path, url):
    layout = {h.extname: h for h in read_layout(FileSource(path))}
    planes = {Plane(n): PlaneSpec(offset=layout[n].data_start, bitpix=layout[n].bitpix,
                                  bzero=layout[n].bzero, bscale=layout[n].bscale)
              for n in ("IMAGE", "FLAGS", "VARIANCE", "ZODI")}
    return FrameRef(frame_id="synth", detector=1, t_start=1.0, t_end=1.01, release="QR2",
                    version="v1", uri=url, wcs_header=WCS, planes=planes, naxis1=NX, naxis2=NY)


@pytest.fixture
def setup(fits_path):
    servers = []

    def make(**kw):
        s = Server(fits_path, **kw)
        servers.append(s)
        return s, frame_for(fits_path, s.url)

    yield make
    for s in servers:
        s.close()


async def nosleep(d):
    pass


async def test_stamp_matches_astropy_exactly(setup, fits_path):
    srv, frame = setup()
    src = S3StampSource()
    assert isinstance(src, StampSource)
    st = await src.fetch(frame, CENTRE, 10)
    assert (st.x0, st.y0, st.image.shape) == (29, 19, (21, 21))
    assert st.flags.dtype == np.uint32 and st.image.dtype == np.float32
    with fits.open(fits_path) as h:
        sl = (slice(19, 40), slice(29, 50))
        assert np.array_equal(st.image, h["IMAGE"].data[sl])
        assert np.array_equal(st.variance, h["VARIANCE"].data[sl])
        assert np.array_equal(st.zodi, h["ZODI"].data[sl])
        assert np.array_equal(st.flags, h["FLAGS"].data[sl].astype("int64") & 0xFFFFFFFF)
    await src.aclose()


async def test_reads_only_the_row_band_of_four_planes(setup):
    srv, frame = setup()
    src = S3StampSource()
    await src.fetch(frame, CENTRE, 10)
    assert len(srv.log) == 4 and all(n == 21 * NX * 4 for _, n in srv.log)
    assert src.bytes_read == 4 * 21 * NX * 4
    await src.aclose()


async def test_zodi_can_be_skipped(setup):
    srv, frame = setup()
    src = S3StampSource(use_zodi=False)
    st = await src.fetch(frame, CENTRE, 5)
    assert st.zodi is None and len(srv.log) == 3
    await src.aclose()


async def test_stamp_is_clipped_at_the_frame_edge(setup):
    srv, frame = setup()
    src = S3StampSource()
    # 0-based pixel (0, 0) is CRPIX offset by (-39, -29) pixels
    d = 6.15 / 3600
    corner = SkyPosition(ra=10.0 + 39 * d, dec=-29 * d)
    st = await src.fetch(frame, corner, 10)
    assert (st.x0, st.y0) == (0, 0) and st.image.shape == (11, 11)
    far = SkyPosition(ra=10.0 + 500 * d, dec=0.0)
    with pytest.raises(OutOfFrame):
        await src.fetch(frame, far, 10)
    await src.aclose()


async def test_busy_server_is_retried(setup):
    srv, frame = setup(fail_first=2)
    src = S3StampSource(limiter=HostLimiter(sleep=nosleep))
    st = await src.fetch(frame, CENTRE, 5)
    assert st.image.shape == (11, 11)
    await src.aclose()


async def test_server_that_stays_down_gives_clear_error(setup):
    srv, frame = setup(fail_first=10_000)
    src = S3StampSource(limiter=HostLimiter(sleep=nosleep, attempts=2))
    with pytest.raises(UpstreamError) as e:
        await src.fetch(frame, CENTRE, 5)
    assert e.value.code is ErrorCode.S3_DOWN
    await src.aclose()


async def test_missing_frame_is_not_retried(setup):
    srv, frame = setup(fail_first=10_000, fail_status=404)
    src = S3StampSource(limiter=HostLimiter(sleep=nosleep))
    with pytest.raises(UpstreamError) as e:
        await src.fetch(frame, CENTRE, 5)
    assert e.value.code is ErrorCode.S3_DOWN and not e.value.retryable
    await src.aclose()


async def test_server_ignoring_range_is_an_error(setup):
    srv, frame = setup(honour_range=False)
    src = S3StampSource()
    with pytest.raises(RangeNotSupported):
        await src.fetch(frame, CENTRE, 5)
    await src.aclose()


async def test_frame_without_catalogue_record_is_refused(setup):
    srv, frame = setup()
    bare = frame.model_copy(update={"planes": None, "wcs_header": None})
    with pytest.raises(ValueError):
        await S3StampSource().fetch(bare, CENTRE, 5)


async def test_many_frames_at_once_respect_the_limit(setup):
    srv, frame = setup(delay=0.03)
    lim = HostLimiter(limits={"127.0.0.1": 4})
    src = S3StampSource(limiter=lim)
    stamps = await asyncio.gather(*[src.fetch(frame, CENTRE, 5) for _ in range(12)])
    assert len(stamps) == 12
    assert 1 < srv.peak <= 4 and lim.peak["127.0.0.1"] <= 4
    await src.aclose()


def test_https_url():
    assert https_url("s3://nasa-irsa-spherex/qr2/a.fits") == \
        "https://nasa-irsa-spherex.s3.us-east-1.amazonaws.com/qr2/a.fits"
    assert https_url("https://x/y") == "https://x/y"
