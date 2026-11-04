"""Shared config resolution for every llmwiki command.

The tracked example config lives at ``examples/sessions_config.json``.
Machine-specific overrides live next to it in the gitignored
``config.json`` and ``config.local.json`` files. Commands used to read
different files, so a redaction or adapter setting in a local override
was silently ignored by ``llmwiki sync``. This module is the single
resolver: base example first, then ``config.json``, then
``config.local.json``.

Only JSON objects are merged. Invalid or missing files are skipped so a
typo in a local override cannot break a sync run.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from llmwiki import REPO_ROOT

CONFIG_RELATIVE_PATHS = (
    Path("examples") / "sessions_config.json",
    Path("config.json"),
    Path("config.local.json"),
)


def read_config_file(path: Path) -> dict[str, Any]:
    """Read one JSON-object config file, or return ``{}``.

    A malformed file is reported on stderr and skipped. The base example
    config is tracked, so a malformed *local* override should not take
    the whole command down with it.
    """
    path = Path(path)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"  warning: ignoring bad config {path}: {exc}", file=sys.stderr)
        return {}
    return data if isinstance(data, dict) else {}


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Merge ``override`` into ``base`` in place and return ``base``.

    Nested dicts merge recursively; every other value (including lists)
    replaces the base value. This is the precedence users expect from a
    local override: set the key you want to change, inherit the rest.
    """
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def resolve_config(repo_root: Path | None = None) -> dict[str, Any]:
    """Return the merged config for a checkout.

    Order: ``examples/sessions_config.json`` → ``config.json`` →
    ``config.local.json``. The last file wins on conflicts.
    """
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    merged: dict[str, Any] = {}
    for relative in CONFIG_RELATIVE_PATHS:
        deep_merge(merged, read_config_file(root / relative))
    return merged
