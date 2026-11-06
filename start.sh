#!/usr/bin/env bash
# 一键启动本地网页版
cd "$(dirname "$0")"
PORT="${1:-8787}"
echo "→ http://127.0.0.1:${PORT}"
exec python3 -m nodl serve --port "$PORT"
