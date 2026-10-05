#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
.venv/bin/python -m pip check
.venv/bin/python -m pytest -q
node --check static/app.js
node --check static/site.js
