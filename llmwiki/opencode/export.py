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
import sqlite3
import sys
from pathlib import Path
from typing import Any, Sequence

from llmwiki.session_export import (  # shared SQLite → JSONL plumbing
    SchemaError,  # noqa: F401 - part of this module's API
    as_int as _as_int,
    connect_readonly,
    delete_export_file as _delete_export_file,
    iso_from_ms,
    load_state,
    parse_since,  # noqa: F401 - part of this module's API
    row_get as _row_get,
    safe_filename as _safe_filename,
    save_state,
    usage_from_tokens as _usage,
)
from llmwiki.session_export import project_slug_for_directory as _project_slug
from llmwiki.session_export import validate_schema as _validate_schema
from llmwiki.session_export import write_jsonl as _write_jsonl

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

def project_slug_for_directory(
    directory: str,
    home: Path | None = None,
    project_roots: Sequence[str | Path] | None = None,
) -> str:
    """Map an OpenCode working directory to a stable project slug.

    OpenCode sessions usually live under ``~/Projects/<name>`` (including
    nested worktrees); see :func:`llmwiki.session_export.project_slug_for_directory`
    for the fallbacks.
    """
    return _project_slug(directory, home, project_roots)


def validate_schema(con: sqlite3.Connection) -> None:
    """Fail clearly when OpenCode changes the columns we depend on."""
    _validate_schema(con, REQUIRED_COLUMNS, label="OpenCode")


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


def _connect(db_path: Path) -> sqlite3.Connection:
    return connect_readonly(db_path, label="OpenCode")


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
