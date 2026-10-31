#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  echo "找不到 .env，請先執行 scripts/install.sh" >&2
  exit 1
fi

uv run --no-sync python -m app.envfile .env.example .env || echo "無法檢查 .env 是否缺少設定，照原本的 .env 繼續啟動" >&2

exec uv run --no-sync --env-file .env python -m app
