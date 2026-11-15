"""Which way up is each frame of a flipbook, as the player draws it?

  python scripts/check_orientation.py [CATALOGUE_DIR] [RA] [DEC]
  (defaults: .cache/catalogue_targets, Barnard's Star at its 2026 position)

For the frames the preview would show, prints where north points on the screen (degrees
clockwise from "up") and whether the sky is drawn as a true view or as a mirror image. The
player draws pixel row 0 at the top, but FITS puts row 0 at the bottom, so a standard frame
comes out mirrored. If north points in different directions from frame to frame, stars will jump
about when the frames are blinked, and the frames need putting on a common grid first.
"""
from __future__ import annotations

import asyncio
import math
import sys

from backend.adapters.catalogue_index import CatalogueFrameIndex
from backend.adapters.wcs_util import celestial_wcs
from backend.pipeline.select import make_selector
from contracts import FlipbookRequest, SkyPosition

CAT = sys.argv[1] if len(sys.argv) > 1 else ".cache/catalogue_targets"
RA = float(sys.argv[2]) if len(sys.argv) > 2 else 269.446
DEC = float(sys.argv[3]) if len(sys.argv) > 3 else 4.769


def orientation(wcs) -> tuple[float, float]:
    """(north, east - north) as clockwise degrees from screen-up. East is 90 degrees
    anticlockwise of north in a true view of the sky (-90), and clockwise in a mirror image."""
    x, y = wcs.all_world2pix([[RA, DEC]], 0)[0]
    nx, ny = wcs.all_world2pix([[RA, DEC + 60 / 3600]], 0)[0]
    ex, ey = wcs.all_world2pix([[RA + 60 / 3600 / math.cos(math.radians(DEC)), DEC]], 0)[0]
    north = math.degrees(math.atan2(nx - x, -(ny - y)))
    east = math.degrees(math.atan2(ex - x, -(ey - y)))
    return north % 360, (east - north + 180) % 360 - 180


async def main() -> None:
    index = CatalogueFrameIndex(CAT)
    req = FlipbookRequest(ra=RA, dec=DEC)
    frames = await index.frames_at(SkyPosition(ra=RA, dec=DEC))
    chosen = make_selector(None)(frames, req).chosen
    print(f"{len(chosen)} preview frames at RA {RA}, Dec {DEC}")
    print("detector  MJD        north points (deg clockwise from screen-up)   sky as drawn")
    seen = set()
    for frame, _visit in chosen:
        north, d = orientation(celestial_wcs(frame.wcs_header))
        sky = "true orientation" if d < 0 else "MIRROR IMAGE"
        seen.add((round(north), sky))
        print(f"D{frame.detector}        {frame.t_mid:9.2f}  {north:6.1f}"
              f"                                       {sky}")
    print()
    directions = sorted({n for n, _ in seen})
    print(f"distinct north directions: {directions}")
    print("-> frames line up (apart from the mirror)" if len(directions) == 1 else
          "-> frames are rotated relative to each other: they need a common grid before blinking")


asyncio.run(main())
