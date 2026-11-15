"""Where a job gets its data from. Two modes:

fixtures   The committed demo stamps under fixtures/. No network at all, so the frontend can be
           built and demonstrated offline. Only positions near a fixture have data. With
           fake_latency_s > 0, frame i arrives (i + 1) x that late, which makes the stream look
           like a slow real one.
catalogue  Real frames: CatalogueFrameIndex plus S3 range reads. Wavelengths come from each
           frame's own spectral WCS (TableWavelength, one table per detector and version, saved
           under settings.wavelength_dir), or are fixed per detector with wavelength="stub".
           The renderer is RealRenderer.
"""
from __future__ import annotations

import asyncio
import logging
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
    StubWavelength,
)
from backend.adapters.wavelength import TableWavelength, make_lambda_of
from backend.pipeline.render import RealRenderer
from backend.pipeline.select import make_selector, make_spectral_selector
from backend.server.jobs import Deps, NoData
from backend.server.settings import Settings
from contracts import FlipbookRequest, FrameRef, SkyPosition, ViewMode
from contracts.interfaces import Cache, FrameIndex

log = logging.getLogger(__name__)

FIXTURE_MATCH_DEG = 1.0


def make_cache(settings: Settings) -> Cache:
    return DiskCache(settings.cache_dir) if settings.cache_dir else MemoryCache()


def _separation_deg(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    dra = (ra1 - ra2 + 180.0) % 360.0 - 180.0
    return math.hypot(dra * math.cos(math.radians((dec1 + dec2) / 2)), dec1 - dec2)


class PrefetchingIndex:
    """A FrameIndex that, after each lookup, loads the wavelength table of every
    (detector, version) among the frames it found. Frames only exist once the lookup has run, so
    this is the earliest point to prefetch. Afterwards TableWavelength.lambda_um is a lookup in
    memory, which is what the selector and the labels need. A failure to load some table is
    logged, not raised: the selector then falls back for frames with no wavelength."""

    def __init__(self, inner: FrameIndex, wavelength: TableWavelength):
        self.inner, self.wavelength = inner, wavelength

    async def frames_at(self, pos: SkyPosition, margin_px: int = 30) -> list[FrameRef]:
        frames = await self.inner.frames_at(pos, margin_px)
        try:
            await self.wavelength.prefetch(frames)
        except Exception:
            log.exception("could not load every wavelength table")
        return frames


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
        wavelength = StubWavelength()
        select = (make_spectral_selector(lambda f: wavelength.lambda_um(f, 0.0, 0.0))
                  if req.view == ViewMode.SPECTRAL else make_selector(None))
        return Deps(index=FixtureFrameIndex(name, self.root), stamps=stamps,
                    wavelength=wavelength, renderer=RealRenderer(), cache=self.cache,
                    select=select)

    async def aclose(self) -> None:
        pass


class CatalogueDeps:
    def __init__(self, settings: Settings, cache: Cache):
        self.cache = cache
        self.index = CatalogueFrameIndex(settings.catalogue_dir)    # loads every shard
        limiter = HostLimiter()                                      # one cap for all jobs
        self._stamps = {True: S3StampSource(limiter=limiter, use_zodi=True),
                        False: S3StampSource(limiter=limiter, use_zodi=False)}
        self._wavelength = (TableWavelength(cache_dir=settings.wavelength_dir)
                            if settings.wavelength == "table" else None)
        self._index: FrameIndex = (PrefetchingIndex(self.index, self._wavelength)
                                   if self._wavelength else self.index)

    def listing(self) -> list[dict]:
        return []

    async def __call__(self, req: FlipbookRequest) -> Deps:
        spectral = req.view == ViewMode.SPECTRAL
        if self._wavelength is None:
            wavelength = StubWavelength()
            select = (make_spectral_selector(lambda f: wavelength.lambda_um(f, 0.0, 0.0))
                      if spectral else make_selector(None))
        else:
            pos = SkyPosition(ra=req.ra, dec=req.dec)
            wavelength = self._wavelength
            lambda_of = make_lambda_of(self._wavelength, pos)
            select = make_spectral_selector(lambda_of) if spectral else make_selector(lambda_of)
        return Deps(index=self._index, stamps=self._stamps[req.use_zodi],
                    wavelength=wavelength, renderer=RealRenderer(), cache=self.cache,
                    select=select)

    async def aclose(self) -> None:
        for s in self._stamps.values():
            await s.aclose()


async def build_deps(settings: Settings, cache: Cache) -> FixtureDeps | CatalogueDeps:
    """The object jobs get their data from: callable with a request, with listing() and aclose()."""
    if settings.source == "fixtures":
        return FixtureDeps(settings, cache)
    return await asyncio.to_thread(CatalogueDeps, settings, cache)    # loading shards is slow
