"""python -m backend.server [--source fixtures|catalogue] [--port 8000] ...

Fixtures mode needs no data and no network:
  python -m backend.server --fake-latency 0.4      # then open /docs, try RA 10 Dec 0
Catalogue mode serves real frames (needs .cache/catalogue_targets and network access to S3):
  python -m backend.server --source catalogue
  python -m backend.server --source catalogue --wavelength stub   # fixed labels, no table reads
"""
from __future__ import annotations

import argparse
import os
from dataclasses import replace

import uvicorn

from backend.server.app import create_app
from backend.server.settings import SOURCES, WAVELENGTH_MODES, Settings


def limit_malloc_arenas() -> None:
    """Linux/glibc: let the worker threads share two memory pools instead of one each, so freed
    memory is reused rather than piling up (the server's resident size only ever grew). No-op
    elsewhere or if the environment already sets MALLOC_ARENA_MAX."""
    if os.environ.get("MALLOC_ARENA_MAX"):
        return
    try:
        import ctypes
        ctypes.CDLL("libc.so.6").mallopt(-8, 2)     # M_ARENA_MAX = -8
    except (OSError, AttributeError):
        pass


def main(argv: list[str] | None = None) -> None:
    limit_malloc_arenas()      # before any worker thread exists
    base = Settings.from_env()
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", choices=SOURCES, default=base.source)
    ap.add_argument("--catalogue", default=base.catalogue_dir, help="directory of Parquet shards")
    ap.add_argument("--cache", default=base.cache_dir, help="rendered frame cache directory")
    ap.add_argument("--wavelength", choices=WAVELENGTH_MODES, default=base.wavelength,
                    help="catalogue mode: real wavelengths (table) or fixed per detector (stub)")
    ap.add_argument("--fake-latency", type=float, default=base.fake_latency_s,
                    help="fixtures: frame i arrives (i + 1) x this many seconds late")
    ap.add_argument("--heartbeat", type=float, default=base.heartbeat_s)
    ap.add_argument("--refine", action="store_true", help="run the refine stage after the preview")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args(argv)
    settings = replace(base, source=a.source, catalogue_dir=a.catalogue, cache_dir=a.cache,
                       wavelength=a.wavelength, fake_latency_s=a.fake_latency,
                       heartbeat_s=a.heartbeat, refine=a.refine or base.refine)
    uvicorn.run(create_app(settings), host=a.host, port=a.port, log_level="info")


if __name__ == "__main__":
    main()
