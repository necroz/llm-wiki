"""Session-config helpers extracted from cli.py (#691 / #arch-h8).

Schedule and synthesis settings from ``examples/sessions_config.json``
(the canonical shipped config) plus the gitignored ``config.json`` /
``config.local.json`` overrides. ``cli.py`` re-exports schedule helpers
under the original underscored names for back-compat.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from llmwiki import REPO_ROOT
from llmwiki.config import read_config_file, resolve_config

_SESSIONS_CONFIG = REPO_ROOT / "examples" / "sessions_config.json"


def _load_sessions_config(config_path: Path | None = None) -> dict[str, Any]:
    """Return one config file, or the full layered config when no path is given."""
    if config_path is not None:
        return read_config_file(config_path)
    return resolve_config()


def load_schedule_config() -> dict[str, str]:
    """Load build/lint schedule config from sessions_config.json.

    Returns a dict with at minimum ``build`` and ``lint`` keys, each
    one of ``on-sync``, ``daily``, ``weekly``, ``manual``, or ``never``.
    Defaults are ``build: on-sync, lint: manual`` when the config file
    is missing or malformed.
    """
    data = _load_sessions_config()
    schedule = data.get("schedule", {})
    return {
        "build": schedule.get("build", "on-sync"),
        "lint": schedule.get("lint", "manual"),
    }


def should_run_after_sync(schedule: str) -> bool:
    """Return True if the schedule value indicates running after sync.

    Accepted values: ``on-sync``, ``daily``, ``weekly``, ``manual``,
    ``never``. Only ``on-sync`` triggers from cmd_sync. ``daily`` /
    ``weekly`` run from a scheduled task; ``manual`` and ``never``
    never auto-run.
    """
    return schedule.lower() == "on-sync"


def load_synthesis_backend(config_path: Path | None = None) -> str:
    """Return ``synthesis.backend`` from sessions config (default: ``dummy``)."""
    data = _load_sessions_config(config_path)
    synthesis = data.get("synthesis", {})
    if not isinstance(synthesis, dict):
        return "dummy"
    backend = synthesis.get("backend", "dummy")
    return str(backend) if backend else "dummy"


def synthesis_status_hint(backend: str | None = None) -> str | None:
    """One-line hint for ``sync --status`` when wiki/sources/ may stay empty."""
    name = (backend or load_synthesis_backend()).strip().lower()
    if name == "dummy":
        return (
            "Synthesis backend: dummy — wiki/sources/ stays stub-only until you set "
            "synthesis.backend to ollama (or agent-delegate) in "
            "examples/sessions_config.json and run `llmwiki synthesize`, or use "
            "`llmwiki all --with-synth` after configuring a real backend."
        )
    return (
        f"Synthesis backend: {name} — run `llmwiki synthesize` to fill wiki/sources/, "
        "or `llmwiki all --with-synth` to chain synthesis before build."
    )
