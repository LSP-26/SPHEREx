"""All I/O sits behind these. Each has a Tier 0 and a Tier 1 implementation."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .arrays import Stamp
from .models import FlipbookRequest, FrameRef, SkyPosition


@runtime_checkable
class FrameIndex(Protocol):
    async def frames_at(self, pos: SkyPosition, margin_px: int = 30) -> list[FrameRef]:
        """Frames covering pos, sorted by t_start. Tier 0: TAP (~17 s). Tier 1: catalogue."""


@runtime_checkable
class StampSource(Protocol):
    async def fetch(self, frame: FrameRef, pos: SkyPosition, half_size_px: int) -> Stamp:
        """Tier 0: IRSA cutout, PSF stripped. Tier 1: S3 row-band range reads."""


@runtime_checkable
class WavelengthSource(Protocol):
    def lambda_um(self, frame: FrameRef, x: float, y: float) -> float: ...


@runtime_checkable
class Lookups(Protocol):
    async def resolve_name(self, name: str) -> SkyPosition: ...
    async def gaia_cone(self, pos: SkyPosition, radius_arcmin: float) -> list[dict]: ...


@runtime_checkable
class Cache(Protocol):
    async def get(self, key: str) -> bytes | None: ...
    async def put(self, key: str, value: bytes) -> None: ...


@dataclass(frozen=True)
class Rendered:
    """An 8-bit PNG. The float arrays stay on the server (stretch changes re-render)."""
    png: bytes
    width: int
    height: int


@runtime_checkable
class Renderer(Protocol):
    def render_png(self, stamp: Stamp, req: FlipbookRequest) -> Rendered:
        """Pure: arrays in, PNG bytes out. Real version: clean, stretch (P3.2)."""
