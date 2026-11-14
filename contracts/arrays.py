"""Array-carrying types (not JSON). Pipeline code is pure: arrays in, arrays out."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .models import FrameRef


@dataclass(frozen=True)
class Stamp:
    """A row band (full width) or cutout of one frame, decoded to float32.
    x0, y0 are the pixel coordinates of array element [0, 0] in the full 2040x2040 frame."""
    frame: FrameRef
    x0: int
    y0: int
    image: np.ndarray            # MJy/sr
    flags: np.ndarray            # uint32 bit field
    variance: np.ndarray
    zodi: np.ndarray | None = None
    wcs_header: dict = field(default_factory=dict)
    source: str = "s3"           # "s3" | "irsa"

    def __post_init__(self) -> None:
        shapes = {self.image.shape, self.flags.shape, self.variance.shape}
        if self.zodi is not None:
            shapes.add(self.zodi.shape)
        if len(shapes) != 1:
            raise ValueError(f"plane shapes differ: {shapes}")
