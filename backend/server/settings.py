"""Server settings. Defaults suit local development; production sets them through the
environment (SPHEREX_*) or the command line (python -m backend.server --help)."""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

SOURCES = ("fixtures", "catalogue")


@dataclass(frozen=True)
class Settings:
    source: str = "fixtures"            # "fixtures": committed demo stamps, no network at all
    #                                     "catalogue": real frames, S3 range reads
    catalogue_dir: str = ".cache/catalogue_targets"
    cache_dir: str | None = ".cache/api_cache"     # None keeps rendered frames in memory only
    fixture_root: str | None = None     # default: the repository's fixtures/ directory
    fake_latency_s: float = 0.0         # fixtures: frame i arrives (i + 1) x this late
    heartbeat_s: float = 15.0           # idle stream sends a heartbeat event this often
    idle_cancel_s: float = 30.0         # a running job nobody watches for this long is cancelled
    job_ttl_s: float = 600.0            # finished jobs are kept (for replay and polling) this long
    max_running: int = 8                # jobs running at once, the rest wait for a slot
    max_jobs: int = 500
    cors_origins: tuple[str, ...] = ("*",)

    def __post_init__(self) -> None:
        if self.source not in SOURCES:
            raise ValueError(f"source must be one of {SOURCES}, not {self.source!r}")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        d = cls()
        cache = env.get("SPHEREX_CACHE", d.cache_dir or "")
        origins = tuple(o.strip() for o in env.get("SPHEREX_CORS", "*").split(",") if o.strip())
        return cls(
            source=env.get("SPHEREX_SOURCE", d.source),
            catalogue_dir=env.get("SPHEREX_CATALOGUE", d.catalogue_dir),
            cache_dir=cache or None,
            fixture_root=env.get("SPHEREX_FIXTURES") or None,
            fake_latency_s=float(env.get("SPHEREX_FAKE_LATENCY", d.fake_latency_s)),
            heartbeat_s=float(env.get("SPHEREX_HEARTBEAT", d.heartbeat_s)),
            cors_origins=origins or d.cors_origins,
        )
