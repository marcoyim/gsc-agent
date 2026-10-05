#!/usr/bin/env bash
# Use the saved AI URL and key, then write the recommendation report.
# Usage: ./start.sh [property] [start-date] [end-date]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
AGENT="$ROOT/.venv/bin/gsc-agent"
if [[ ! -x "$AGENT" ]]; then
  echo "尚未安裝。請先執行：python3.12 -m venv .venv && .venv/bin/python -m pip install -e ."
  exit 1
fi
if ! "$AGENT" setup --check --config config.toml; then
  "$ROOT/setup.sh"
fi

property="${1:-}"
start_date="${2:-}"
end_date="${3:-}"
if [[ -z "$property" ]]; then
  read -r -p "Search Console 資源（先用 gsc-agent properties 取得完整字串）： " property
fi
if [[ -z "$start_date" ]]; then
  read -r -p "開始日期 YYYY-MM-DD： " start_date
fi
if [[ -z "$end_date" ]]; then
  read -r -p "結束日期 YYYY-MM-DD： " end_date
fi

echo "若 setup 選的是雲端平台，查詢字、網址與指標會送到該平台。若選的是本機 Ollama，資料留在這台電腦。"
exec "$AGENT" agent --config config.toml --allow-remote-llm \
  --property "$property" \
  --start "$start_date" \
  --end "$end_date"
