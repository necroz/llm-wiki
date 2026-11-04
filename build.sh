#!/usr/bin/env bash
# llmwiki — build the static HTML site.
# Usage: ./build.sh [--synthesize] [--out <dir>]
set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
PY="$SCRIPT_DIR/.venv/bin/python"
[ -x "$PY" ] || PY="python3"
exec "$PY" -m llmwiki build "$@"
