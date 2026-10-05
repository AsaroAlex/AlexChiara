#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

# Preserve the existing owner login while every private workspace requires an account.
: "${FILO_ACCESS_PASSWORD:?Imposta FILO_ACCESS_PASSWORD nelle variabili Railway.}"
export ALEXCHIARA_DATA_DIR="${ALEXCHIARA_DATA_DIR:-/data}"
export ALEXCHIARA_ALLOWED_HOSTS="${ALEXCHIARA_ALLOWED_HOSTS:+${ALEXCHIARA_ALLOWED_HOSTS},}${RAILWAY_PUBLIC_DOMAIN:-},${RAILWAY_PRIVATE_DOMAIN:-},healthcheck.railway.app"

exec python -m uvicorn app.main:create_app --factory \
  --host 0.0.0.0 --port "${PORT:-8000}" --workers 1 \
  --proxy-headers --forwarded-allow-ips '*' --no-access-log
