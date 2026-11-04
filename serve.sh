#!/usr/bin/env bash
# llmwiki — start a local HTTP server on 127.0.0.1:8765.
# Usage: ./serve.sh [--port N] [--host H] [--open]
set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
PY="$SCRIPT_DIR/.venv/bin/python"
[ -x "$PY" ] || PY="python3"
exec "$PY" -m llmwiki serve "$@"
