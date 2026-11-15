"""T6a: Gaia sources on the flipbook's picture (plan A11). Pure: no network, no disk.

project_sources() puts catalogue stars (the dicts that lookups.WebLookups.gaia_cone returns) on
the same grid the previews are drawn on: make_target_wcs centred on the request, north up and
east left, then flipped so row 0 is the top (render.py). So a star's (x, y) here is where it
sits in the PNG.

IMAGE COORDINATES. x runs left to right and y top to bottom, in pixels, 0-based pixel centres
(x = 0, y = 0 is the centre of the top-left pixel). u = (x + 0.5) / size and v = (y + 0.5) / size
are the same position as fractions of the picture's width and height, which is what a page
needs to place a marker without knowing the displayed size.

MOTION. Every star is moved from its own reference epoch (Gaia: J2016.0) to `epoch_mjd` (default:
the middle of the SPHEREx data so far) with motion.propagate, and (vx, vy) is its velocity on the
picture in px/yr (y down). The page places a star at frame time t as
  x + vx * (t - epoch_mjd) / 365.25,  y + vy * (t - epoch_mjd) / 365.25.
Positions are kept if they fall inside the picture at epoch_mjd (plus margin_px). Over the
survey (about 1.5 years) even Barnard's Star moves only about 2.5 px, so this is enough.
Parallax is ignored.

cone_radius_arcmin() is the radius to ask the Gaia cone search for: the picture's half-diagonal
plus a margin for stars that move into the field between J2016 and the survey.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from backend.pipeline.grid import PIXEL_ARCSEC, make_target_wcs
from backend.pipeline.motion import DAYS_PER_YEAR, GAIA_EPOCH_MJD, propagate
from backend.pipeline.render import preview_size_px
from backend.pipeline.targets import SURVEY_MID_MJD

MAX_CONE_ARCMIN = 120.0      # what lookups.gaia_cone accepts
DEFAULT_MARGIN_ARCMIN = 2.0  # room for stars that drift in: 2 arcmin is 10 years at 12 arcsec/yr
PREFILTER_DEG = 10.0         # stars farther than this from the centre are not projected at all


@dataclass(frozen=True)
class OverlayStar:
    source_id: int | None
    x: float                 # pixels, 0-based pixel centres, y down, at Overlay.epoch_mjd
    y: float
    u: float                 # the same position as a fraction of the picture width
    v: float                 # ... and height (0 = top edge)
    vx: float                # velocity on the picture, px per year (y down)
    vy: float
    g_mag: float | None
    pmra: float | None       # mas/yr, as given by the catalogue (includes cos dec)
    pmdec: float | None
    parallax: float | None


@dataclass(frozen=True)
class Overlay:
    size: int                # the picture is size x size pixels
    epoch_mjd: float         # the date x and y are for
    pixel_arcsec: float
    stars: list[OverlayStar]

    def to_json(self) -> dict:
        """Plain dicts and numbers only, ready for json.dumps."""
        return {"size": self.size, "epoch_mjd": self.epoch_mjd,
                "pixel_arcsec": self.pixel_arcsec,
                "stars": [asdict(s) for s in self.stars]}


def cone_radius_arcmin(fov_arcmin: float, margin_arcmin: float = DEFAULT_MARGIN_ARCMIN) -> float:
    """Gaia cone radius that covers the whole picture (corners included) plus a margin, at
    most MAX_CONE_ARCMIN."""
    _check_fov(fov_arcmin)
    half_diagonal = preview_size_px(fov_arcmin) * math.sqrt(0.5) * PIXEL_ARCSEC / 60.0
    return min(MAX_CONE_ARCMIN, half_diagonal + margin_arcmin)


def _check_fov(fov_arcmin: float) -> None:
    if not 0 < fov_arcmin <= 120:
        raise ValueError("fov_arcmin must be above 0 and at most 120")


def _number(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _separation_deg(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    d1, d2 = math.radians(dec1), math.radians(dec2)
    h = (math.sin((d1 - d2) / 2) ** 2
         + math.cos(d1) * math.cos(d2) * math.sin(math.radians(ra1 - ra2) / 2) ** 2)
    return math.degrees(2 * math.asin(min(1.0, math.sqrt(h))))


def _moved(ra: float, dec: float, pmra: float, pmdec: float, epoch: float,
           mjd: float) -> tuple[float, float]:
    try:
        return propagate(ra, dec, pmra, pmdec, epoch, mjd)
    except ValueError:       # at a pole: leave it where it is
        return ra, dec


def _pixel(wcs, ra: float, dec: float) -> tuple[float, float] | None:
    """0-based (x, y) on the grid with y UP (FITS order), or None if it does not project."""
    try:
        x, y = wcs.all_world2pix([[ra % 360.0, dec]], 0)[0]
    except Exception:
        return None
    if not (math.isfinite(x) and math.isfinite(y)):
        return None
    return float(x), float(y)


def project_sources(sources: list[dict], ra: float, dec: float, fov_arcmin: float, *,
                    epoch_mjd: float = SURVEY_MID_MJD, margin_px: float = 0.0) -> Overlay:
    """The sources that fall on the picture of a flipbook centred on (ra, dec) with this field
    of view, in the catalogue's order (brightest first for gaia_cone)."""
    _check_fov(fov_arcmin)
    size = preview_size_px(fov_arcmin)
    wcs = make_target_wcs(ra, dec, size)
    lo, hi = -0.5 - margin_px, size - 0.5 + margin_px
    top = size - 1               # FITS row 0 is the bottom; the picture is flipped
    out: list[OverlayStar] = []
    for s in sources:
        sra, sdec = _number(s.get("ra")), _number(s.get("dec"))
        if sra is None or sdec is None:
            continue
        if _separation_deg(sra, sdec, ra, dec) > PREFILTER_DEG:
            continue
        pmra, pmdec = _number(s.get("pmra")), _number(s.get("pmdec"))
        epoch = _number(s.get("ref_epoch_mjd")) or GAIA_EPOCH_MJD
        mra, mdec = pmra or 0.0, pmdec or 0.0
        p0 = _pixel(wcs, *_moved(sra, sdec, mra, mdec, epoch, epoch_mjd))
        if p0 is None:
            continue
        x, y = p0[0], top - p0[1]
        if not (lo <= x <= hi and lo <= y <= hi):
            continue
        p1 = _pixel(wcs, *_moved(sra, sdec, mra, mdec, epoch, epoch_mjd + DAYS_PER_YEAR))
        vx, vy = (0.0, 0.0) if p1 is None else (p1[0] - p0[0], -(p1[1] - p0[1]))
        sid = s.get("source_id")
        out.append(OverlayStar(
            source_id=None if sid is None else int(sid), x=x, y=y,
            u=(x + 0.5) / size, v=(y + 0.5) / size, vx=vx, vy=vy,
            g_mag=_number(s.get("g_mag")), pmra=pmra, pmdec=pmdec,
            parallax=_number(s.get("parallax"))))
    return Overlay(size=size, epoch_mjd=epoch_mjd, pixel_arcsec=PIXEL_ARCSEC, stars=out)
