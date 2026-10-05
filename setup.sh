#!/usr/bin/env bash
# Ask for the AI base URL, model name, and API key. The key is written to .env only.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
AGENT="$ROOT/.venv/bin/gsc-agent"
if [[ ! -x "$AGENT" ]]; then
  echo "尚未安裝。請先執行：python3.12 -m venv .venv && .venv/bin/python -m pip install -e ."
  exit 1
fi
exec "$AGENT" setup --config config.toml
