import asyncio

import pytest
from pydantic import TypeAdapter, ValidationError

from backend.adapters.fixtures import FixtureFrameIndex, FixtureStampSource, list_fixtures
from contracts import (
    DoneEvent,
    ErrorCode,
    ErrorEvent,
    Event,
    FlipbookRequest,
    SkyPosition,
    cache_key,
)
from contracts.interfaces import FrameIndex, StampSource
from contracts.url_state import from_query, to_query


def test_cache_key_stable_and_sensitive():
    kw = dict(frame_id="a", release="QR2", version="v20", ra=10.0, dec=-7.0, size_px=47)
    k = cache_key(**kw)
    assert k == cache_key(**kw)
    assert k == cache_key(**{**kw, "ra": 10.0 + 1e-9})          # float noise does not split
    assert k != cache_key(**{**kw, "version": "v25"})            # version always matters
    assert k != cache_key(**{**kw, "release": "QR3"})
    assert k != cache_key(**{**kw, "params": {"use_zodi": False}})
    assert len(k) == 64


@pytest.mark.parametrize("req", [
    FlipbookRequest(ra=133.7951, dec=-7.2452),
    FlipbookRequest(ra=269.452, dec=4.693, fov_arcmin=12.5, view="mover", detector=3,
                    use_zodi=False, preview_cap=24),
])
def test_url_round_trip(req):
    assert from_query(to_query(req)) == req


def test_url_defaults_omitted():
    assert to_query(FlipbookRequest(ra=1, dec=2)) == "ra=1&dec=2"


def test_bad_coords_rejected():
    with pytest.raises(ValidationError):
        FlipbookRequest(ra=400, dec=0)
    with pytest.raises(ValidationError):
        FlipbookRequest(ra=10, dec=95)


def test_event_union_discriminates():
    ta = TypeAdapter(Event)
    ev = ta.validate_python({"type": "error", "code": "irsa_busy", "message": "IRSA is busy"})
    assert isinstance(ev, ErrorEvent) and ev.code is ErrorCode.IRSA_BUSY
    assert isinstance(ta.validate_json(DoneEvent(n_frames=3, elapsed_s=1.2).model_dump_json()),
                      DoneEvent)


def test_fixture_adapters_satisfy_interfaces():
    assert "synthetic_test" in list_fixtures()
    idx, src = FixtureFrameIndex("synthetic_test"), FixtureStampSource("synthetic_test")
    assert isinstance(idx, FrameIndex) and isinstance(src, StampSource)
    pos = SkyPosition(ra=10.0, dec=0.0)
    frames = asyncio.run(idx.frames_at(pos))
    assert [f.t_start for f in frames] == sorted(f.t_start for f in frames)
    st = asyncio.run(src.fetch(frames[0], pos, 23))
    assert st.image.shape == (47, 47)
