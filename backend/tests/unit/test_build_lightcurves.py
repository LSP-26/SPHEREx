import json
import math
from dataclasses import replace

import numpy as np
import pytest

from backend.offline.build_lightcurves import (
    build_target,
    spectrum,
    summarize,
    thin,
    update_index,
)
from backend.pipeline.lightcurve import LightCurvePoint
from backend.tests.unit.test_lightcurve import DEC0, RA0, WCS, make_stamp
from contracts import FrameRef


def pt(lam, flux=1.0, lower=False, reason=None, centroid=True, t=61000.0):
    return LightCurvePoint(frame_id="f", detector=1, release="QR3", version="v27", t_mjd=t,
                           lambda_um=lam, x=1.0, y=1.0, centroid=centroid, flux_jy=flux,
                           err_jy=0.1, lower_limit=lower, reason=reason, flags=())


def frame(i):
    return FrameRef(frame_id=f"f{i}", detector=3, t_start=61000.0 + 10 * i,
                    t_end=61000.02 + 10 * i, release="QR3", version="v27",
                    uri=f"s3://x/f{i}", wcs_header=WCS)


def test_thin_keeps_time_order_then_applies_every_and_limit():
    fs = [frame(i) for i in (4, 0, 2, 1, 3)]
    assert [f.frame_id for f in thin(fs)] == ["f0", "f1", "f2", "f3", "f4"]
    assert [f.frame_id for f in thin(fs, every=2)] == ["f0", "f2", "f4"]
    assert [f.frame_id for f in thin(fs, every=2, limit=2)] == ["f0", "f2"]
    with pytest.raises(ValueError):
        thin(fs, every=0)


def test_summary_counts_measured_saturated_and_refused_points():
    pts = [pt(1.0), pt(1.0, lower=True), pt(1.0, flux=None, reason="masked"),
           pt(1.0, centroid=False)]
    s = summarize(pts)
    assert (s["n_points"], s["n_ok"], s["n_lower_limit"], s["n_refused"]) == (4, 3, 1, 1)
    assert s["n_centroid"] == 2 and s["refused_reasons"] == {"masked": 1}


def test_spectrum_bins_by_resolution_element_and_marks_lower_limits():
    pts = [pt(1.000, flux=1.0), pt(1.001, flux=3.0),          # one bin
           pt(2.0, flux=5.0, lower=True), pt(2.0, flux=7.0, lower=True),   # saturated only
           pt(3.0, flux=None, reason="masked"), pt(float("nan"), flux=1.0), pt(None, flux=1.0)]
    s = spectrum(pts)
    assert len(s) == 2
    assert s[0]["flux_jy"] == pytest.approx(2.0) and s[0]["n"] == 2 and not s[0]["lower_limit"]
    assert s[0]["scatter_jy"] == pytest.approx(math.sqrt(2.0))
    assert s[1]["lower_limit"] and s[1]["flux_jy"] == 7.0 and s[1]["scatter_jy"] is None
    assert s[0]["lambda_um"] < s[1]["lambda_um"]


class Index:
    async def frames_at(self, pos, margin_px=30):
        return [frame(i) for i in range(5)]


class Stamps:
    async def fetch(self, fr, pos, half):
        if fr.frame_id == "f2":
            raise ValueError("boom")
        return replace(make_stamp(stars=[(24.0, 24.0, 100.0)]), frame=fr)


class Wavelength:
    async def prefetch(self, frames):
        pass

    def lambda_um(self, fr, x, y):
        return 1.65


class Solid:
    async def region_async(self, release, detector, x0, y0, nx, ny):
        return np.full((ny, nx), 37.82)


def run(**kw):
    return build_target("t", "Test", index=Index(), stamps=Stamps(), wavelength=Wavelength(),
                        solid=Solid(), position_fn=lambda key, mjd: (RA0, DEC0),
                        log=lambda s: None, **kw)


async def test_a_failing_frame_is_counted_and_the_rest_are_measured():
    body = await run()
    assert body["summary"]["n_ok"] == 4 and body["failed"] == {"ValueError": 1}
    times = [p["t_mjd"] for p in body["points"]]
    assert times == sorted(times) and len(times) == 4
    assert all(p["flux_jy"] > 0 and p["lambda_um"] == 1.65 for p in body["points"])
    assert len(body["spectrum"]) == 1 and body["spectrum"][0]["n"] == 4
    json.dumps(body)                                  # plain JSON all the way down


async def test_every_and_limit_reach_the_run():
    assert (await run(every=2))["summary"]["n_points"] == 2     # f0, f4 (f2 fails)
    assert (await run(limit=2))["summary"]["n_points"] == 2


def test_index_merges_instead_of_overwriting(tmp_path):
    update_index(tmp_path, [{"key": "r_dra", "n_points": 1}, {"key": "rw_aur", "n_points": 2}])
    update_index(tmp_path, [{"key": "r_dra", "n_points": 9}])
    items = json.loads((tmp_path / "index.json").read_text())["lightcurves"]
    assert [i["key"] for i in items] == ["r_dra", "rw_aur"] and items[0]["n_points"] == 9


async def test_measure_position_thins_big_fields_and_reports_progress(monkeypatch):
    from backend.jobs import measure
    from backend.jobs.measure import measure_position
    from contracts import SkyPosition
    monkeypatch.setattr(measure, "MAX_FRAMES", 2)
    seen = []
    frames = [frame(i) for i in range(5)]
    body = await measure_position(SkyPosition(ra=RA0, dec=DEC0), frames, stamps=Stamps(),
                                  wavelength=Wavelength(), solid=Solid(),
                                  on_progress=lambda d, t: seen.append((d, t)))
    assert body["n_frames_covering"] == 5 and body["thinned_every"] == 3
    assert seen[-1] == (2, 2) and body["key"] == "here" and body["summary"]["n_points"] >= 1
