#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  echo "找不到 .env，請先執行 bash scripts/install.sh" >&2
  exit 1
fi

uv run --no-sync python -m app.envfile .env.example .env || echo "無法檢查 .env 是否缺少設定，照原本的 .env 繼續啟動" >&2

while true; do
  status=0
  uv run --no-sync --env-file .env python -m app --updatable || status=$?
  if [ ! -f .update/request.json ]; then
    exit "$status"
  fi
  echo "下載並套用新版…"
  if uv run --no-sync python -m app.update --root . apply --yes; then
    if ! group="$(uv run --no-sync python -m app.update --root . group)" \
      || ! uv sync --frozen --no-default-groups --group "$group"; then
      echo "安裝新版的套件失敗，還原成更新前的檔案" >&2
      uv run --no-sync python -m app.update --root . rollback
      group="$(uv run --no-sync python -m app.update --root . group)"
      uv sync --frozen --no-default-groups --group "$group"
    fi
  else
    echo "更新沒有完成，繼續用原本的版本" >&2
  fi
  uv run --no-sync python -m app.envfile .env.example .env || echo "無法檢查 .env 是否缺少設定" >&2
done
