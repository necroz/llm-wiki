#!/usr/bin/env bash
# Compatibility wrapper for the ZCode autosync job.
#
# Prefer `llmwiki zcode install`, which writes a portable Stop-hook and a
# native scheduler entry. This script remains for existing hook/cron
# entries and simply delegates to the Python command.
#
# Project scope: LLMWIKI_ZCODE_PROJECT (default: all projects).
set -uo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${REPO_ROOT}/.venv/bin/python"
[ -x "$PY" ] || PY="python3"

ARGS=("$@")
if [ -n "${LLMWIKI_ZCODE_PROJECT:-}" ]; then
  ARGS+=(--project "$LLMWIKI_ZCODE_PROJECT")
fi

exec "$PY" -m llmwiki zcode autosync "${ARGS[@]}"