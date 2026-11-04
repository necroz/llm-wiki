#!/usr/bin/env python3
"""Compatibility wrapper for the ZCode exporter.

The implementation now lives in :mod:`llmwiki.zcode.export` and is
available as ``llmwiki zcode export``. This wrapper keeps existing
Stop-hooks, timers, and cron entries working while they migrate.
"""

from __future__ import annotations

import sys
from pathlib import Path

# ``python scripts/zcode_export.py`` puts scripts/ on sys.path, not the
# repository root. Add the root so ``import llmwiki`` works from a clone
# that has not been pip-installed.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from llmwiki.zcode.export import (  # noqa: E402,F401
    DEFAULT_DB,
    DEFAULT_OUT_DIR,
    REQUIRED_COLUMNS,
    SERVICE_PART_TYPES,
    SchemaError,
    build_parser,
    iso_from_ms,
    load_state,
    main,
    parse_since,
    project_slug_for_directory,
    run_export,
    save_state,
    schema_version,
    session_to_records,
    validate_schema,
)

if __name__ == "__main__":
    raise SystemExit(main())
