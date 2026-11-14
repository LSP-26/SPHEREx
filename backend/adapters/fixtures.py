"""Fixture loader. Format (one directory per target under fixtures/):
  meta.json   {"name", "ra", "dec", "synthetic": bool, "frames": [FrameRef-as-dict, ...]}
  <frame_id>.npz  arrays: image, flags, variance, (zodi); scalars: x0, y0
Fixtures are small committed stamps. .fits and .npy stay git-ignored; use .npz."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from contracts import FrameRef, SkyPosition, Stamp

FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "fixtures"


def list_fixtures(root: Path = FIXTURE_ROOT) -> list[str]:
    return sorted(p.parent.name for p in root.glob("*/meta.json"))


def load_meta(name: str, root: Path = FIXTURE_ROOT) -> dict:
    return json.loads((root / name / "meta.json").read_text())


def load_frames(name: str, root: Path = FIXTURE_ROOT) -> list[FrameRef]:
    frames = [FrameRef(**f) for f in load_meta(name, root)["frames"]]
    return sorted(frames, key=lambda f: f.t_start)


def load_stamp(name: str, frame: FrameRef, root: Path = FIXTURE_ROOT) -> Stamp:
    z = np.load(root / name / f"{frame.frame_id}.npz")
    return Stamp(
        frame=frame, x0=int(z["x0"]), y0=int(z["y0"]),
        image=z["image"], flags=z["flags"], variance=z["variance"],
        zodi=z["zodi"] if "zodi" in z.files else None, source="fixture",
    )


class FixtureFrameIndex:
    def __init__(self, name: str, root: Path = FIXTURE_ROOT):
        self.name, self.root = name, root

    async def frames_at(self, pos: SkyPosition, margin_px: int = 30) -> list[FrameRef]:
        return load_frames(self.name, self.root)


class FixtureStampSource:
    def __init__(self, name: str, root: Path = FIXTURE_ROOT):
        self.name, self.root = name, root

    async def fetch(self, frame: FrameRef, pos: SkyPosition, half_size_px: int) -> Stamp:
        return load_stamp(self.name, frame, self.root)
