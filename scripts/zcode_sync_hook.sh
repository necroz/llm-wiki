#!/usr/bin/env bash
# Refresh the llmwiki ZCode corpus after a ZCode Stop event or from a timer.
#
# Project scope: LLMWIKI_ZCODE_PROJECT (default: neuromerch_project).
# Set it to an empty string to export every project:
#   LLMWIKI_ZCODE_PROJECT= scripts/zcode_sync_hook.sh --build
#
# Stop hooks run detached; flock serialises them with the nightly timer.
set -uo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT="${LLMWIKI_ZCODE_PROJECT-neuromerch_project}"
BUILD=0
case "${1:-}" in
  --build) BUILD=1 ;;
  ""|--no-build) BUILD=0 ;;
  *) echo "usage: $0 [--build]" >&2; exit 2 ;;
esac

LOG_DIR="${HOME}/.llmwiki/zcode"
mkdir -p "$LOG_DIR"
LOG="${LOG_DIR}/autosync.log"
LOCK="${LOG_DIR}/autosync.lock"

exec 9>"$LOCK"
if ! flock -w 600 9; then
  printf '%s skip: another sync is still running\n' "$(date -Is)" >> "$LOG"
  exit 0
fi

PY="${REPO_ROOT}/.venv/bin/python"
[ -x "$PY" ] || PY="python3"

{
  printf '=== %s start project=%s build=%s ===\n' "$(date -Is)" "${PROJECT:-<all>}" "$BUILD"
  if [ -n "$PROJECT" ]; then
    "$PY" "$REPO_ROOT/scripts/zcode_export.py" --project "$PROJECT" || echo "export failed"
    (
      cd "$REPO_ROOT" || exit 1
      "$PY" -m llmwiki sync --adapter zcode --project "$PROJECT" --include-current \
        $([ "$BUILD" -eq 1 ] && echo --auto-build || echo --no-auto-build)
    ) || echo "sync failed"
  else
    "$PY" "$REPO_ROOT/scripts/zcode_export.py" || echo "export failed"
    (
      cd "$REPO_ROOT" || exit 1
      "$PY" -m llmwiki sync --adapter zcode --include-current \
        $([ "$BUILD" -eq 1 ] && echo --auto-build || echo --no-auto-build)
    ) || echo "sync failed"
  fi
  printf '=== %s done ===\n' "$(date -Is)"
} >> "$LOG" 2>&1

exit 0