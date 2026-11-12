#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

if ! grep -q '^name = "breeze-asr-webapp"$' pyproject.toml 2>/dev/null; then
  echo "找不到 Breeze ASR：這支腳本要放在 Breeze ASR 資料夾的 scripts 裡執行" >&2
  exit 1
fi
homepage="$(sed -n 's/^Homepage = "\(.*\)"$/\1/p' pyproject.toml)"
if [ -z "$homepage" ]; then
  echo "這個版本的 pyproject.toml 沒有記錄下載位置（0.3.0 以前的版本），請到 GitHub 的 Releases 頁面手動下載新版" >&2
  exit 1
fi
mkdir -p .update
echo "下載最新的更新程式…"
curl -fsSL -o .update/update.py "$homepage/releases/latest/download/update.py"

uv run --no-sync python .update/update.py --root . apply
if ! group="$(uv run --no-sync python .update/update.py --root . group)" \
  || ! uv sync --frozen --no-default-groups --group "$group"; then
  echo "安裝新版的套件失敗，還原成更新前的檔案" >&2
  uv run --no-sync python .update/update.py --root . rollback
  group="$(uv run --no-sync python .update/update.py --root . group)"
  uv sync --frozen --no-default-groups --group "$group"
  exit 1
fi
uv run --no-sync python -m app.envfile .env.example .env
echo "完成。執行 bash scripts/start.sh 啟動。"
