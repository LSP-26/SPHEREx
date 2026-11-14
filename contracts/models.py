"""Contracts v0: the shapes every lane codes against. Change via PR and a version bump."""
from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

CONTRACT_VERSION = "0.1.1"


class _M(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Plane(str, Enum):
    IMAGE = "IMAGE"
    FLAGS = "FLAGS"
    VARIANCE = "VARIANCE"
    ZODI = "ZODI"


class ViewMode(str, Enum):
    SKY = "sky"
    MOVER = "mover"  # follow-the-mover (Tier 1)


class Stage(str, Enum):
    PREVIEW = "preview"
    REFINED = "refined"


class ErrorCode(str, Enum):
    BAD_COORDS = "bad_coords"
    NO_COVERAGE = "no_coverage"
    IRSA_BUSY = "irsa_busy"  # HTTP 503 from IRSA
    IRSA_DOWN = "irsa_down"
    S3_DOWN = "s3_down"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    INTERNAL = "internal"


# ---------------------------------------------------------------- positions and requests
class SkyPosition(_M):
    ra: float = Field(ge=0.0, lt=360.0, description="ICRS RA, degrees")
    dec: float = Field(ge=-90.0, le=90.0, description="ICRS Dec, degrees")


class FlipbookRequest(_M):
    """Everything that defines a flipbook. All fields also live in the URL query string."""
    ra: float = Field(ge=0.0, lt=360.0)
    dec: float = Field(ge=-90.0, le=90.0)
    fov_arcmin: float = Field(default=5.0, gt=0.0, le=120.0)
    view: ViewMode = ViewMode.SKY
    detector: int | None = Field(default=None, ge=1, le=6, description="None = merged colour")
    lambda_min_um: float | None = Field(default=None, gt=0)
    lambda_max_um: float | None = Field(default=None, gt=0)
    preview_cap: int = Field(default=36, ge=1, le=200)
    use_zodi: bool = True
    release: str | None = Field(default=None, description="None = latest")


# ---------------------------------------------------------------- frames
class PlaneSpec(_M):
    """Where one plane's pixels start in the FITS file, and how to decode them."""
    offset: int = Field(ge=0, description="byte offset of the first pixel of row 0")
    bitpix: int
    bzero: float = 0.0
    bscale: float = 1.0


class FrameRef(_M):
    frame_id: str
    detector: int = Field(ge=1, le=6)
    obsid: str | None = None
    t_start: float = Field(description="MJD")
    t_end: float = Field(description="MJD")
    release: str
    version: str
    uri: str = Field(description="s3://nasa-irsa-spherex/... key, derived from IRSA metadata")
    # Tier 1 catalogue extras, None on the Tier 0 (TAP) path
    wcs_header: dict[str, str | float | int] | None = None
    planes: dict[Plane, PlaneSpec] | None = None
    naxis1: int = 2040
    naxis2: int = 2040

    @property
    def t_mid(self) -> float:
        return 0.5 * (self.t_start + self.t_end)


class FrameLabel(_M):
    """What the UI prints under a frame: wavelength, date, provenance."""
    frame_id: str
    detector: int
    lambda_um: float
    channel: int | None = Field(default=None, ge=1, le=17)
    t_mid_mjd: float
    release: str
    version: str
    flags_summary: list[str] = Field(default_factory=list)  # e.g. ["saturated"]


class Visit(_M):
    visit_index: int
    t_start: float
    t_end: float
    frame_ids: list[str]


# ---------------------------------------------------------------- SSE events
class PlanEvent(_M):
    type: Literal["plan"] = "plan"
    job_id: str
    n_slots: int
    n_visits_total: int
    n_visits_shown: int  # UI prints 'k of n visits'
    est_bytes: int | None = None


class ProgressEvent(_M):
    type: Literal["progress"] = "progress"
    done: int
    total: int
    stage: Stage


class FrameEvent(_M):
    """A rendered frame. A REFINED event with the same slot replaces the PREVIEW one."""
    type: Literal["frame"] = "frame"
    slot: int
    stage: Stage
    png_url: str
    width: int
    height: int
    label: FrameLabel
    visit_index: int
    channels_covered: int | None = Field(default=None, ge=0, le=17)  # None for previews
    partial: bool = False
    elapsed_s: float


class HeartbeatEvent(_M):
    type: Literal["heartbeat"] = "heartbeat"


class DoneEvent(_M):
    type: Literal["done"] = "done"
    n_frames: int
    elapsed_s: float


class ErrorEvent(_M):
    type: Literal["error"] = "error"
    code: ErrorCode
    message: str  # plain language, safe to show
    retryable: bool = False


Event = Annotated[
    Union[PlanEvent, ProgressEvent, FrameEvent, HeartbeatEvent, DoneEvent, ErrorEvent],
    Field(discriminator="type"),
]
