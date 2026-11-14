"""python -m backend.server [--source fixtures|catalogue] [--port 8000] ...

Fixtures mode needs no data and no network:
  python -m backend.server --fake-latency 0.4      # then open /docs, try RA 10 Dec 0
Catalogue mode serves real frames (needs .cache/catalogue_targets and network access to S3):
  python -m backend.server --source catalogue
"""
from __future__ import annotations

import argparse
from dataclasses import replace

import uvicorn

from backend.server.app import create_app
from backend.server.settings import SOURCES, Settings


def main(argv: list[str] | None = None) -> None:
    base = Settings.from_env()
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", choices=SOURCES, default=base.source)
    ap.add_argument("--catalogue", default=base.catalogue_dir, help="directory of Parquet shards")
    ap.add_argument("--cache", default=base.cache_dir, help="rendered frame cache directory")
    ap.add_argument("--fake-latency", type=float, default=base.fake_latency_s,
                    help="fixtures: frame i arrives (i + 1) x this many seconds late")
    ap.add_argument("--heartbeat", type=float, default=base.heartbeat_s)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args(argv)
    settings = replace(base, source=a.source, catalogue_dir=a.catalogue, cache_dir=a.cache,
                       fake_latency_s=a.fake_latency, heartbeat_s=a.heartbeat)
    uvicorn.run(create_app(settings), host=a.host, port=a.port, log_level="info")


if __name__ == "__main__":
    main()
