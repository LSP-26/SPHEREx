#!/usr/bin/env bash
cd "$(dirname "$0")/.."
source .venv/bin/activate
mkdir -p .cache/catalogue
for i in $(seq 1 30); do
  echo "=== attempt $i, $(date)"
  if python -u -m backend.offline.refresh --out .cache/catalogue --workers 32; then
    echo "=== finished, $(date)"; exit 0
  fi
  echo "=== failed, retrying in 120 s"; sleep 120
done
echo "=== gave up"; exit 1
