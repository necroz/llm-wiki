#!/usr/bin/env python3
"""Export OpenCode V2 sessions from SQLite into Claude-style JSONL.

OpenCode V2 stores sessions in ``~/.local/share/opencode/opencode.db``
(tables ``session_v2`` and ``session_message``).  llmwiki's converter reads
JSONL, so this exporter is the boundary between the two:

    ~/.local/share/opencode/opencode.db
        -> ~/.llmwiki/opencode/<project-slug>/<session-id>.jsonl
        -> llmwiki sync (adapter "opencode")

The exporter is read-only and incremental.  State is keyed by
``session_v2.time_updated`` so a second run with no changes writes nothing.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Sequence

DEFAULT_DB = Path.home() / ".local/share/opencode/opencode.db"
DEFAULT_OUT_DIR = Path.home() / ".llmwiki" / "opencode"

REQUIRED_COLUMNS = {
    "session_v2": {
        "id",
        "project_id",
        "parent_id",
        "directory",
        "title",
        "time_created",
        "time_updated",
    },
    "session_message": {
        "id",
        "session_id",
        "type",
        "seq",
        "time_created",
        "data",
    },
}

_PROJECT_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]")


class SchemaError(RuntimeError):
    """Raised when the OpenCode database schema is not the one we expect."""


@lru_cache(maxsize=512)
def _git_toplevel(directory: str) -> Path | None:
    """Return the git work-tree root for ``directory``, or ``None``."""
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


def project_slug_for_directory(
    directory: str,
    home: Path | None = None,
    project_roots: Sequence[str | Path] | None = None,
) -> str:
    """Map an OpenCode working directory to a stable project slug.

    OpenCode sessions usually live under ``~/Projects/<name>`` (including
    nested worktrees), so the first component under that root is the
    project.  Configured roots and the git top-level are fallbacks for
    other layouts.
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

    parts = relative_parts(home / "Projects")
    if parts:
        name = parts[0]
    else:
        name = ""
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
            git_root = _git_toplevel(str(path))
            if git_root is not None:
                name = git_root.name
        if not name:
            parts = relative_parts(home)
            name = parts[0] if parts and len(parts) == 1 else path.name

    if not name or name in {".", ".."}:
        return "unknown"
    slug = _PROJECT_SLUG_RE.sub("_", name).lstrip(".")
    return slug or "unknown"


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


def validate_schema(con: sqlite3.Connection) -> None:
    """Fail clearly when OpenCode changes the columns we depend on."""
    tables = {
        row[0]
        for row in con.execute(
            "select name from sqlite_master where type = 'table'"
        ).fetchall()
    }
    missing: list[str] = []
    for table, required in REQUIRED_COLUMNS.items():
        if table not in tables:
            missing.append(table)
            continue
        columns = {
            row[1] for row in con.execute(f"pragma table_info({table})").fetchall()
        }
        missing.extend(f"{table}.{column}" for column in sorted(required - columns))
    if missing:
        raise SchemaError(
            "missing required OpenCode schema columns: " + ", ".join(missing)
        )


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
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        temporary.write_text(
            json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def _row_get(row: sqlite3.Row, key: str, default: Any = None) -> Any:
    try:
        keys = row.keys()
    except AttributeError:
        return default
    return row[key] if key in keys else default


def _session_directory(
    con: sqlite3.Connection, session: sqlite3.Row
) -> str:
    directory = str(_row_get(session, "directory", "") or "")
    if directory:
        return directory
    project_id = str(_row_get(session, "project_id", "") or "")
    if not project_id:
        return ""
    try:
        row = con.execute(
            "select worktree from project where id = ?", (project_id,)
        ).fetchone()
    except sqlite3.Error:
        return ""
    return str(row[0] or "") if row else ""


def _file_placeholder(item: dict[str, Any]) -> str:
    filename = str(item.get("name") or "")
    if not filename:
        source = item.get("source") or {}
        if isinstance(source, dict):
            filename = Path(str(source.get("uri") or "")).name
    if not filename:
        filename = str(item.get("mime") or "attachment")
    return f"[file: {filename}]"


def _tool_output(state: dict[str, Any]) -> str:
    content = state.get("content") or []
    texts: list[str] = []
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                text = str(block.get("text") or "").strip()
                if text:
                    texts.append(text)
    if texts:
        return "\n".join(texts)
    error = state.get("error") or {}
    if isinstance(error, dict):
        return str(error.get("message") or "").strip()
    return ""


def _usage(data: dict[str, Any]) -> dict[str, int]:
    tokens = data.get("tokens") or {}
    cache = tokens.get("cache") or {}
    return {
        "input_tokens": _as_int(tokens.get("input")),
        "output_tokens": _as_int(tokens.get("output")),
        "cache_read_input_tokens": _as_int(cache.get("read")),
        "cache_creation_input_tokens": _as_int(cache.get("write")),
    }


def _model(data: dict[str, Any]) -> str:
    model = data.get("model") or "unknown"
    if isinstance(model, dict):
        model = model.get("id") or model.get("modelID") or "unknown"
    return str(model)


def session_to_records(
    con: sqlite3.Connection, session: sqlite3.Row
) -> list[dict[str, Any]]:
    """Convert one OpenCode V2 session into Claude-style records."""
    session_id = str(_row_get(session, "id", ""))
    directory = _session_directory(con, session)
    title = str(_row_get(session, "title", "") or "")

    messages = con.execute(
        "select id, type, seq, time_created, data from session_message"
        " where session_id = ? order by seq, time_created",
        (session_id,),
    ).fetchall()

    records: list[dict[str, Any]] = []

    for message in messages:
        try:
            data = json.loads(message["data"])
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue

        kind = str(_row_get(message, "type", "") or data.get("type") or "")
        message_time = _as_int(
            (data.get("time") or {}).get("created")
        ) or _as_int(_row_get(message, "time_created", 0))

        if kind == "user":
            text = str(data.get("text") or "").strip()
            parts = [text] if text else []
            files = data.get("files") or []
            if isinstance(files, list):
                for item in files:
                    if isinstance(item, dict):
                        parts.append(_file_placeholder(item))
            content = "\n\n".join(parts).strip()
            if content:
                records.append(
                    {
                        "type": "user",
                        "timestamp": iso_from_ms(message_time),
                        "sessionId": session_id,
                        "cwd": directory,
                        "message": {"role": "user", "content": content},
                    }
                )
            continue

        if kind != "assistant":
            continue

        blocks: list[dict[str, Any]] = []
        tool_results: list[dict[str, Any]] = []
        content = data.get("content") or []
        if not isinstance(content, list):
            content = []
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "text":
                text = str(block.get("text") or "").strip()
                if text:
                    blocks.append({"type": "text", "text": text})
            elif block_type == "tool":
                state = block.get("state") or {}
                if not isinstance(state, dict):
                    state = {}
                call_id = str(block.get("id") or block.get("callID") or "")
                tool_input = state.get("input")
                if not isinstance(tool_input, dict):
                    tool_input = (
                        {"value": tool_input} if tool_input is not None else {}
                    )
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call_id,
                        "name": str(block.get("name") or "Tool"),
                        "input": tool_input,
                    }
                )
                output = _tool_output(state)
                if output:
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": call_id,
                            "content": output,
                            "is_error": state.get("status") in {"error", "failed"},
                        }
                    )

        if blocks:
            records.append(
                {
                    "type": "assistant",
                    "timestamp": iso_from_ms(message_time),
                    "sessionId": session_id,
                    "cwd": directory,
                    "message": {
                        "role": "assistant",
                        "model": _model(data),
                        "content": blocks,
                        "usage": _usage(data),
                    },
                }
            )
        if tool_results:
            records.append(
                {
                    "type": "user",
                    "timestamp": iso_from_ms(message_time),
                    "sessionId": session_id,
                    "cwd": directory,
                    "message": {"role": "user", "content": tool_results},
                }
            )

    if not any(record["type"] in {"user", "assistant"} for record in records):
        return []

    init: dict[str, Any] = {
        "type": "init",
        "sessionId": session_id,
        "cwd": directory,
        "timestamp": iso_from_ms(_row_get(session, "time_created", 0)),
    }
    if title:
        init["title"] = title
    return [init, *records]


def _safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", value).lstrip(".") or "session"


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            for record in records:
                handle.write(
                    json.dumps(record, ensure_ascii=False, separators=(",", ":"))
                    + "\n"
                )
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _connect(db_path: Path) -> sqlite3.Connection:
    path = Path(db_path).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"OpenCode database not found: {path}")
    con = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("pragma query_only = on")
    con.execute("pragma busy_timeout = 30000")
    return con


def _delete_export_file(out_dir: Path, relative: str) -> bool:
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


def run_export(
    db_path: Path,
    out_dir: Path,
    state_file: Path | None = None,
    since: int | None = None,
    project: str | None = None,
    include_subagents: bool = False,
    dry_run: bool = False,
    project_roots: Sequence[str | Path] | None = None,
    prune_stale: bool = True,
) -> dict[str, Any]:
    """Export matching OpenCode sessions and return counters."""
    out_dir = Path(out_dir).expanduser()
    state_file = (
        Path(state_file).expanduser()
        if state_file
        else out_dir / ".export-state.json"
    )
    stats: dict[str, Any] = {
        "total_sessions": 0,
        "selected": 0,
        "exported": 0,
        "unchanged": 0,
        "empty": 0,
        "errors": 0,
        "written": 0,
        "skipped_subagents": 0,
        "skipped_project": 0,
        "skipped_since": 0,
        "stale": 0,
        "stale_deleted": 0,
        "files": [],
    }
    state = load_state(state_file)
    sessions_state = state.setdefault("sessions", {})
    state_dirty = False
    project_text = project.lower() if project else None
    since_ms = int(since) if since is not None else None
    seen_sessions: set[str] = set()

    con = _connect(db_path)
    try:
        validate_schema(con)
        sessions = con.execute(
            "select * from session_v2 order by time_created, id"
        ).fetchall()
        for session in sessions:
            stats["total_sessions"] += 1
            session_id = str(session["id"])
            seen_sessions.add(session_id)

            if not include_subagents and _row_get(session, "parent_id"):
                stats["skipped_subagents"] += 1
                continue

            directory = _session_directory(con, session)
            slug = project_slug_for_directory(
                directory, project_roots=project_roots
            )
            effective_time = max(
                _as_int(_row_get(session, "time_updated")),
                _as_int(_row_get(session, "time_created")),
            )
            if since_ms is not None and effective_time < since_ms:
                stats["skipped_since"] += 1
                continue
            if project_text and project_text not in f"{directory} {slug}".lower():
                stats["skipped_project"] += 1
                continue

            stats["selected"] += 1
            filename = (
                f"subagent-{_safe_filename(session_id)}.jsonl"
                if _row_get(session, "parent_id")
                else f"{_safe_filename(session_id)}.jsonl"
            )
            out_path = out_dir / slug / filename
            previous = sessions_state.get(session_id)
            previous_time = (
                previous.get("time_updated")
                if isinstance(previous, dict)
                else previous
            )
            current_time = _as_int(_row_get(session, "time_updated"))
            if previous_time == current_time and out_path.exists():
                stats["unchanged"] += 1
                continue

            try:
                records = session_to_records(con, session)
            except Exception as exc:  # noqa: BLE001 - one bad session must not stop the run
                stats["errors"] += 1
                print(f"error: {session_id}: {exc}", file=sys.stderr)
                continue
            if not records:
                stats["empty"] += 1
                continue

            stats["exported"] += 1
            stats["files"].append(out_path)
            if dry_run:
                continue
            try:
                _write_jsonl(out_path, records)
            except OSError as exc:
                stats["errors"] += 1
                print(f"error: {session_id}: {exc}", file=sys.stderr)
                continue
            stats["written"] += 1
            new_rel = str(out_path.relative_to(out_dir))
            if isinstance(previous, dict):
                old_rel = previous.get("path")
                if old_rel and old_rel != new_rel:
                    _delete_export_file(out_dir, str(old_rel))
            sessions_state[session_id] = {
                "time_updated": current_time,
                "path": new_rel,
                "project": slug,
            }
            state_dirty = True
    finally:
        con.close()

    if (
        prune_stale
        and not dry_run
        and project_text is None
        and since_ms is None
        and stats["total_sessions"] > 0
    ):
        stale_ids = sorted(set(sessions_state) - seen_sessions)
        for session_id in stale_ids:
            entry = sessions_state.pop(session_id, None)
            stats["stale"] += 1
            state_dirty = True
            if isinstance(entry, dict):
                relative = entry.get("path")
                if relative and _delete_export_file(out_dir, str(relative)):
                    stats["stale_deleted"] += 1

    if state_dirty and not dry_run:
        save_state(state_file, state)
    return stats
