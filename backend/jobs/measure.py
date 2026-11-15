"""Measure any position: the light curve and rough spectrum of whatever is at (ra, dec).

The same measurement as the two built-in targets (backend/offline/build_lightcurves.py: every
catalogue frame that covers the position, 2 px aperture photometry, a lower limit when the star
saturates), but run live for a position the user picks. The position is taken as fixed: proper
motion is not known for an arbitrary spot, so a fast-moving star may be missed in the frames
far from the survey's middle date (the centroid search follows it up to 1.5 px).

At most MAX_FRAMES frames are measured; a spot with more is thinned evenly in time
(`thinned_every` in the body says so).
Bump MEASURE_VERSION when the measurement changes (it is part of the cache key).
"""
from __future__ import annotations

from collections.abc import Callable

from backend.offline.build_lightcurves import build_target
from contracts import FrameRef, SkyPosition

MEASURE_VERSION = 1
MAX_FRAMES = 400


class _Fixed:
    """build_target's index: the frames are already known."""

    def __init__(self, frames: list[FrameRef]):
        self.frames = frames

    async def frames_at(self, pos: SkyPosition, margin_px: int = 30) -> list[FrameRef]:
        return self.frames


async def measure_position(pos: SkyPosition, frames: list[FrameRef], *, stamps, wavelength, solid,
                           on_progress: Callable[[int, int], None] | None = None) -> dict:
    """The body of a light-curve file for `pos` (same shape as frontend/public/lightcurves/*.json),
    plus `n_frames_covering` and `thinned_every`."""
    every = max(1, -(-len(frames) // MAX_FRAMES))       # ceiling division
    body = await build_target("here", f"RA {pos.ra:.4f}, Dec {pos.dec:+.4f}", index=_Fixed(frames),
                              stamps=stamps, wavelength=wavelength, solid=solid, every=every,
                              position_fn=lambda _k, _t: (pos.ra, pos.dec), on_progress=on_progress,
                              log=lambda _m: None)
    body.update(n_frames_covering=len(frames), thinned_every=every)
    return body
