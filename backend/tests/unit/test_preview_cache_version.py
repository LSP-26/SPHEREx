import backend.jobs.preview as preview
from backend.adapters.stubs import MemoryCache
from backend.tests.unit.test_preview_masked import MaskedSource, collect, deps


async def test_changing_the_render_version_invalidates_cached_frames(monkeypatch):
    cache, src = MemoryCache(), MaskedSource(set())
    await collect(**deps(src, cache))
    first = src.fetches
    await collect(**deps(src, cache))
    assert src.fetches == first                       # same version: served from the cache
    monkeypatch.setattr(preview, "RENDER_VERSION", preview.RENDER_VERSION + 1)
    await collect(**deps(src, cache))
    assert src.fetches == 2 * first                   # new version: everything is rendered again
