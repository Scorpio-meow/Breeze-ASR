#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

device="${1:-}"
if [ -z "$device" ]; then
  if command -v nvidia-smi >/dev/null 2>&1; then
    device=cuda
  elif command -v rocm-smi >/dev/null 2>&1 || command -v amd-smi >/dev/null 2>&1; then
    device=rocm
  else
    device=cpu
  fi
  echo "偵測到的裝置：$device（可用 scripts/install.sh cpu|cuda|rocm 指定）"
fi

case "$device" in
  cpu) group=cpu ;;
  cuda) group=cu130 ;;
  rocm) group=rocm72 ;;
  *) echo "用法：scripts/install.sh [cpu|cuda|rocm]" >&2; exit 2 ;;
esac

if ! command -v uv >/dev/null 2>&1; then
  echo "安裝 uv…"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  . "$HOME/.local/bin/env"
fi

echo "安裝套件（PyTorch $group）…"
uv sync --frozen --no-default-groups --group "$group"

if [ ! -f .env ]; then
  cp .env.example .env
  echo "已建立 .env，可依需要修改"
fi

echo "安裝完成。執行 scripts/start.sh 啟動；第一次啟動會下載並轉換約 9 GB 的模型。"
