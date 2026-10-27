"""ZCode session-store adapter.

ZCode sessions are exported by ``scripts/zcode_export.py`` into a flat,
Claude-style JSONL corpus:

    ~/.llmwiki/zcode/<project-slug>/<session-id>.jsonl

The exporter already applies the ZCode-specific filters (subagents,
model-only messages, reasoning parts, tool collapsing), so this adapter
only needs discovery, project-slug derivation, and availability checks.
The default ``normalize_records`` pass-through is correct because the
exporter writes the shared Claude-style record shape.
"""

from __future__ import annotations

from pathlib import Path

from llmwiki.adapters import register
from llmwiki.adapters.base import BaseAdapter


@register("zcode")
class ZcodeAdapter(BaseAdapter):
    """ZCode — reads ~/.llmwiki/zcode/<project>/*.jsonl"""

    SUPPORTED_SCHEMA_VERSIONS = ["v1"]

    session_store_path = Path.home() / ".llmwiki" / "zcode"

    @classmethod
    def is_available(cls) -> bool:
        """Available when the export directory contains at least one session."""
        try:
            instance = cls()
        except Exception:
            return False
        paths = instance.session_store_path
        if isinstance(paths, Path):
            paths = [paths]
        for path in paths:
            root = Path(path).expanduser()
            if root.exists() and any(root.rglob("*.jsonl")):
                return True
        return False
