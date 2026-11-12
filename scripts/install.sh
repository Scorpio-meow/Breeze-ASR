#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

if [ "$(uname -s)" = Darwin ]; then
  if [ "$(uname -m)" != arm64 ]; then
    echo "這台 Mac 不是 Apple Silicon，或終端機正以 Rosetta 執行：OpenVINO 與 PyTorch 都沒有 Intel Mac 版，請改用 Docker（見 docs/deployment.md 的 macOS 安裝腳本）" >&2
    exit 1
  fi
  macos="$(sw_vers -productVersion)"
  if [ "${macos%%.*}" -lt 14 ]; then
    echo "需要 macOS 14 以上（PyTorch 的 Mac 版要求），這台是 macOS $macos" >&2
    exit 1
  fi
fi

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
  echo "已建立 .env。字幕字型預設是 Windows 的微軟正黑體，啟動前請改成這台電腦的中文字型（見 docs/deployment.md 的字幕字型）"
else
  uv run --no-sync python -m app.envfile .env.example .env
fi

uv run --no-sync python -m app.update --root . skills

echo "安裝完成。執行 scripts/start.sh 啟動；第一次啟動會下載並轉換約 9 GB 的模型。"
