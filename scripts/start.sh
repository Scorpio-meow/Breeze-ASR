#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  echo "找不到 .env，請先執行 scripts/install.sh" >&2
  exit 1
fi

exec uv run --no-sync --env-file .env python -m app
