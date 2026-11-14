"""Where a job gets its data from. Two modes:

fixtures   The committed demo stamps under fixtures/. No network at all, so the frontend can be
           built and demonstrated offline. Only positions near a fixture have data. With
           fake_latency_s > 0, frame i arrives (i + 1) x that late, which makes the stream look
           like a slow real one.
catalogue  Real frames: CatalogueFrameIndex plus S3 range reads. Wavelengths and the renderer
           are still the stubs (greyscale, fixed per-detector wavelength) until D5.1 and P3.2.
"""
from __future__ import annotations

import asyncio
import math
from pathlib import Path

from backend.adapters.cache import DiskCache, HostLimiter
from backend.adapters.catalogue_index import CatalogueFrameIndex
from backend.adapters.fixtures import (
    FIXTURE_ROOT,
    FixtureFrameIndex,
    FixtureStampSource,
    list_fixtures,
    load_frames,
    load_meta,
)
from backend.adapters.s3_stamps import S3StampSource
from backend.adapters.stubs import (
    DelayedStampSource,
    MemoryCache,
    StubRenderer,
    StubWavelength,
)
from backend.pipeline.select import make_selector
from backend.server.jobs import Deps, NoData
from backend.server.settings import Settings
from contracts import FlipbookRequest
from contracts.interfaces import Cache

FIXTURE_MATCH_DEG = 1.0


def make_cache(settings: Settings) -> Cache:
    return DiskCache(settings.cache_dir) if settings.cache_dir else MemoryCache()


def _separation_deg(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    dra = (ra1 - ra2 + 180.0) % 360.0 - 180.0
    return math.hypot(dra * math.cos(math.radians((dec1 + dec2) / 2)), dec1 - dec2)


class FixtureDeps:
    def __init__(self, settings: Settings, cache: Cache):
        self.s, self.cache = settings, cache
        self.root = Path(settings.fixture_root) if settings.fixture_root else FIXTURE_ROOT
        self.entries = []
        for name in list_fixtures(self.root):
            meta = load_meta(name, self.root)
            self.entries.append({"name": name, "ra": meta["ra"], "dec": meta["dec"],
                                 "synthetic": bool(meta.get("synthetic", False)),
                                 "n_frames": len(meta["frames"])})

    def listing(self) -> list[dict]:
        return list(self.entries)

    async def __call__(self, req: FlipbookRequest) -> Deps:
        near = [(_separation_deg(req.ra, req.dec, e["ra"], e["dec"]), e) for e in self.entries]
        near = [x for x in near if x[0] <= FIXTURE_MATCH_DEG]
        if not near:
            where = ", ".join(f"RA {e['ra']:g}, Dec {e['dec']:g}" for e in self.entries)
            raise NoData("This server is running on demo data only. Try a position near: "
                         f"{where or 'none available'}.")
        name = min(near, key=lambda x: x[0])[1]["name"]
        order = {f.frame_id: i for i, f in enumerate(load_frames(name, self.root))}
        stamps = DelayedStampSource(
            FixtureStampSource(name, self.root),
            delay=lambda f: self.s.fake_latency_s * (order.get(f.frame_id, 0) + 1))
        return Deps(index=FixtureFrameIndex(name, self.root), stamps=stamps,
                    wavelength=StubWavelength(), renderer=StubRenderer(), cache=self.cache,
                    select=make_selector(None))

    async def aclose(self) -> None:
        pass


class CatalogueDeps:
    def __init__(self, settings: Settings, cache: Cache):
        self.cache = cache
        self.index = CatalogueFrameIndex(settings.catalogue_dir)    # loads every shard
        limiter = HostLimiter()                                      # one cap for all jobs
        self._stamps = {True: S3StampSource(limiter=limiter, use_zodi=True),
                        False: S3StampSource(limiter=limiter, use_zodi=False)}

    def listing(self) -> list[dict]:
        return []

    async def __call__(self, req: FlipbookRequest) -> Deps:
        return Deps(index=self.index, stamps=self._stamps[req.use_zodi],
                    wavelength=StubWavelength(), renderer=StubRenderer(), cache=self.cache,
                    select=make_selector(None))

    async def aclose(self) -> None:
        for s in self._stamps.values():
            await s.aclose()


async def build_deps(settings: Settings, cache: Cache) -> FixtureDeps | CatalogueDeps:
    """The object jobs get their data from: callable with a request, with listing() and aclose()."""
    if settings.source == "fixtures":
        return FixtureDeps(settings, cache)
    return await asyncio.to_thread(CatalogueDeps, settings, cache)    # loading shards is slow
