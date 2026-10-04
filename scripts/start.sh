#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [ ! -x .venv/bin/python ]; then
  echo 'Esegui prima bash scripts/setup.sh' >&2
  exit 1
fi
exec .venv/bin/python -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port "${FILO_PORT:-8000}" --workers 1 --no-access-log
