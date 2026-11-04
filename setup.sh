#!/usr/bin/env bash
# llmwiki — one-click installer for macOS / Linux.
#
# Usage: ./setup.sh [--graph]
# Idempotent — safe to re-run.
#
# The installer owns a local .venv so it does not depend on the system
# Python's packages (PEP 668 blocks user-level pip installs on current
# Debian/Ubuntu and Homebrew Python). Wrapper scripts prefer the same
# interpreter.

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

EXTRAS=""
case "${1:-}" in
  "") ;;
  --graph) EXTRAS="[graph]" ;;
  *) echo "usage: $0 [--graph]" >&2; exit 2 ;;
esac

echo "==> llmwiki setup"
echo "    root: $SCRIPT_DIR"

# 1. Python check
if ! command -v python3 >/dev/null 2>&1; then
  echo "error: python3 is required but was not found in PATH" >&2
  exit 1
fi
PY_VER=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')
if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)'; then
  echo "error: Python 3.9+ is required, found $PY_VER" >&2
  exit 1
fi
echo "    python: $PY_VER"

# 2. Create the checkout-local virtualenv. This avoids PEP 668
#    ("externally-managed-environment") on modern Linux and Homebrew.
if [ ! -x "$SCRIPT_DIR/.venv/bin/python" ]; then
  echo "==> creating .venv"
  if ! python3 -m venv "$SCRIPT_DIR/.venv"; then
    echo "error: cannot create .venv." >&2
    echo "       Debian/Ubuntu: sudo apt install python3-venv" >&2
    exit 1
  fi
fi
PY="$SCRIPT_DIR/.venv/bin/python"

# 3. Install the package into the venv (editable, so source edits apply).
#    Skip the network when the existing venv already has everything.
if [ -n "$EXTRAS" ] || ! "$PY" -c "import llmwiki, markdown" 2>/dev/null; then
  echo "==> installing llmwiki${EXTRAS} into .venv"
  "$PY" -m pip install -e "${SCRIPT_DIR}${EXTRAS}" --quiet
fi

if ! "$PY" -c "import markdown" 2>/dev/null; then
  echo "error: the 'markdown' dependency is missing from .venv" >&2
  echo "       run: $PY -m pip install -e $SCRIPT_DIR" >&2
  exit 1
fi

# 4. Scaffold raw/ wiki/ site/
"$PY" -m llmwiki init

# 5. Show available adapters
"$PY" -m llmwiki adapters

# 6. First sync (status probe so users see how many sessions exist
#    without actually converting them yet)
echo
echo "==> sync status:"
"$PY" -m llmwiki sync --status || true

echo
echo "==> environment check:"
"$PY" -m llmwiki doctor || true

echo
echo "================================================================"
echo "  Setup complete."
echo "================================================================"
echo
echo "Next steps:"
echo "  source .venv/bin/activate   # or call .venv/bin/llmwiki directly"
echo "  ./sync.sh                   # export ZCode + convert new sessions"
echo "  ./build.sh                  # generate the static HTML site"
echo "  ./serve.sh                  # browse at http://127.0.0.1:8765/"
echo "  .venv/bin/python -m llmwiki zcode export   # ZCode store -> JSONL"
echo
echo "Optional SessionStart hook — auto-sync on every Claude Code launch:"
echo "  Add this to ~/.claude/settings.json under 'hooks':"
echo '    "SessionStart": [ { "hooks": [ { "type": "command",'
echo "      \"command\": \"($PY -m llmwiki sync --no-auto-build > /tmp/llmwiki-sync.log 2>&1 &) ; exit 0\" } ] } ]"
echo
echo "For ZCode Stop-hook and timer setup, see ZCODE.md."
