"""Shared building blocks for SQLite → JSONL session exporters.

ZCode (``llmwiki.zcode.export``) and OpenCode (``llmwiki.opencode.export``)
both read a local SQLite store and write Claude-style JSONL for the
converter. ZCode is an OpenCode fork, so most of the plumbing is the
same: read-only connection, schema check, incremental state, atomic
JSONL writes, project-slug resolution. That plumbing lives here; each
exporter keeps only what is specific to its schema (how messages and
parts map to records).

Also holds the one-line log writer shared by the autosync commands.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import tempfile
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

_PROJECT_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]")


class SchemaError(RuntimeError):
    """Raised when a session database schema is not the one we expect."""


# ─── SQLite ────────────────────────────────────────────────────────────────


def connect_readonly(db_path: Path, *, label: str) -> sqlite3.Connection:
    """Open ``db_path`` read-only (``mode=ro`` + ``query_only``).

    ``label`` names the tool in the error ("ZCode database not found").
    """
    path = Path(db_path).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"{label} database not found: {path}")
    con = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("pragma query_only = on")
    con.execute("pragma busy_timeout = 30000")
    return con


def validate_schema(
    con: sqlite3.Connection,
    required_columns: Mapping[str, set[str]],
    *,
    label: str,
) -> None:
    """Fail clearly when the tool changes the columns we depend on.

    Table names come from the exporter's own constant, never from input,
    so interpolating them into ``pragma table_info`` is safe.
    """
    tables = {
        row[0]
        for row in con.execute(
            "select name from sqlite_master where type = 'table'"
        ).fetchall()
    }
    missing: list[str] = []
    for table, required in required_columns.items():
        if table not in tables:
            missing.append(table)
            continue
        columns = {
            row[1] for row in con.execute(f"pragma table_info({table})").fetchall()
        }
        missing.extend(f"{table}.{column}" for column in sorted(required - columns))
    if missing:
        raise SchemaError(
            f"missing required {label} schema columns: " + ", ".join(missing)
        )


def row_get(row: sqlite3.Row, key: str, default: Any = None) -> Any:
    """``row[key]`` when the column exists, else ``default``."""
    try:
        keys = row.keys()
    except AttributeError:
        return default
    return row[key] if key in keys else default


# ─── value coercion ────────────────────────────────────────────────────────


def as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def iso_from_ms(value: Any) -> str | None:
    """Convert a Unix millisecond timestamp to an ISO-8601 UTC string."""
    if value is None:
        return None
    try:
        milliseconds = int(value)
    except (TypeError, ValueError):
        return None
    return (
        datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def parse_since(value: str | None) -> int | None:
    """Parse ``--since`` as YYYY-MM-DD or ISO-8601 and return epoch ms."""
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid --since value: {value}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def usage_from_tokens(data: dict[str, Any]) -> dict[str, int]:
    """Map OpenCode-style ``tokens`` (shared by ZCode) to Claude usage keys."""
    tokens = data.get("tokens") or {}
    cache = tokens.get("cache") or {}
    return {
        "input_tokens": as_int(tokens.get("input")),
        "output_tokens": as_int(tokens.get("output")),
        "cache_read_input_tokens": as_int(cache.get("read")),
        "cache_creation_input_tokens": as_int(cache.get("write")),
    }


# ─── project slugs ─────────────────────────────────────────────────────────


@lru_cache(maxsize=512)
def git_toplevel(directory: str) -> Path | None:
    """Return the git work-tree root for ``directory``, or ``None``.

    Cached per directory string: one export run touches the same handful
    of project paths hundreds of times.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", directory, "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    text = proc.stdout.strip()
    return Path(text) if text else None


# A tool-specific rule: given the resolved session path and home, return a
# project name, or "" to fall through to the generic rules.
SlugRule = Callable[[Path, Path], str]


def project_slug_for_directory(
    directory: str,
    home: Path | None = None,
    project_roots: Sequence[str | Path] | None = None,
    *,
    extra_rule: Optional[SlugRule] = None,
) -> str:
    """Map a session working directory to a stable project slug.

    Resolution order:

    1. ``~/Projects/<name>`` — top-level name, so nested worktrees group
       under one project.
    2. ``extra_rule`` — a tool-specific layout (ZCode workspaces).
    3. A configured project root (``project_roots`` / ``--project-root``)
       — the first component under that root.
    4. The git work-tree root — fixes nested directories on machines
       whose projects live in ``~/dev``, ``~/src``, ``D:\\work``, etc.
    5. The home-directory component or the directory basename.
    """
    home = Path(home or Path.home())
    raw = str(directory or "").strip()
    if not raw:
        return "unknown"

    path = Path(raw).expanduser()
    try:
        path = path.resolve()
    except OSError:
        pass

    def relative_parts(root: Path) -> tuple[str, ...] | None:
        try:
            return path.relative_to(root).parts
        except ValueError:
            return None

    name = ""
    parts = relative_parts(home / "Projects")
    if parts:
        name = parts[0]
    if not name and extra_rule is not None:
        name = extra_rule(path, home)
    if not name:
        for configured in project_roots or ():
            root = Path(configured).expanduser()
            try:
                root = root.resolve()
            except OSError:
                pass
            configured_parts = relative_parts(root)
            if configured_parts:
                name = configured_parts[0]
                break
    if not name:
        git_root = git_toplevel(str(path))
        if git_root is not None:
            name = git_root.name
    if not name:
        parts = relative_parts(home)
        name = parts[0] if parts and len(parts) == 1 else path.name

    if not name or name in {".", ".."}:
        return "unknown"
    slug = _PROJECT_SLUG_RE.sub("_", name).lstrip(".")
    return slug or "unknown"


# ─── files and state ───────────────────────────────────────────────────────


def safe_filename(value: str) -> str:
    """Make a session id safe as a file name (no separators, no dotfiles)."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", value).lstrip(".") or "session"


def _atomic_write(path: Path, write: Callable[[Any], None]) -> None:
    """Write via a uniquely named temp file in ``path``'s directory, then
    ``os.replace``. Two writers never share a temp file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            write(handle)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    def write(handle: Any) -> None:
        for record in records:
            handle.write(
                json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
            )

    _atomic_write(path, write)


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "sessions": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read export state {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"invalid export state {path}: expected an object")
    data.setdefault("version", 1)
    sessions = data.setdefault("sessions", {})
    if not isinstance(sessions, dict):
        raise RuntimeError(f"invalid export state {path}: 'sessions' must be an object")
    return data


def save_state(path: Path, state: dict[str, Any]) -> None:
    text = json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    _atomic_write(path, lambda handle: handle.write(text))


def delete_export_file(out_dir: Path, relative: str) -> bool:
    """Delete one JSONL file under ``out_dir``; refuse path traversal."""
    target = (out_dir / relative).resolve()
    try:
        target.relative_to(out_dir.resolve())
    except ValueError:
        return False
    if target.suffix != ".jsonl" or not target.is_file():
        return False
    target.unlink()
    return True


# ─── autosync log ──────────────────────────────────────────────────────────


def autosync_log(message: str, *, log_path: Path, quiet: bool) -> None:
    """Append one timestamped line to ``log_path``; echo unless ``quiet``."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    line = f"{stamp} {message}"
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    if not quiet:
        print(line)
