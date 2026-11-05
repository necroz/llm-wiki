#!/usr/bin/env python3
"""Export ZCode sessions from db.sqlite into Claude-style JSONL transcripts.

The exporter is the boundary between ZCode's live SQLite store and the
llmwiki converter:

    ~/.zcode/cli/db/db.sqlite
        -> ~/.llmwiki/zcode/<project-slug>/<session-id>.jsonl
        -> llmwiki sync (adapter "zcode")

It is deliberately read-only and incremental. The DB is opened with
``mode=ro`` and a busy timeout; incremental state is keyed by
``session.time_updated`` so a second run with no changes writes nothing.

Usage:
    llmwiki zcode export --project demo_project --dry-run
    llmwiki zcode export --project demo_project
    llmwiki zcode export --since 2026-09-01

The standalone ``scripts/zcode_export.py`` wrapper remains for existing
hooks and cron entries.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Sequence

from llmwiki.session_export import (  # shared SQLite → JSONL plumbing
    SchemaError,
    as_int as _as_int,
    as_text as _as_text,
    connect_readonly,
    delete_export_file as _delete_export_file,
    iso_from_ms,
    load_state,
    parse_since,
    row_get as _row_get,
    safe_filename as _safe_filename,
    save_state,
    usage_from_tokens as _usage,
)
from llmwiki.session_export import project_slug_for_directory as _project_slug
from llmwiki.session_export import validate_schema as _validate_schema
from llmwiki.session_export import write_jsonl as _write_jsonl

DEFAULT_DB = Path.home() / ".zcode" / "cli" / "db" / "db.sqlite"
DEFAULT_OUT_DIR = Path.home() / ".llmwiki" / "zcode"

# Columns the exporter actually depends on. ``title``, ``slug`` and
# ``permission`` are optional metadata, so a schema that drops them still
# exports conversation data instead of failing.
REQUIRED_COLUMNS = {
    "session": {"id", "directory", "task_type", "time_created", "time_updated"},
    "message": {"id", "session_id", "time_created", "data", "sequence"},
    "part": {"id", "message_id", "session_id", "time_created", "data", "sequence"},
}

SERVICE_PART_TYPES = {"reasoning", "step-start", "step-finish", "timeline", "compaction"}
MODEL_ONLY_VISIBILITY = {"model-only", "hidden"}
def _zcode_workspace_rule(path: Path, home: Path) -> str:
    """``~/.zcode/workspace/<name>`` → ``workspace_<name>``; ``draft-*`` → ``workspace_drafts``."""
    try:
        parts = path.relative_to(home / ".zcode" / "workspace").parts
    except ValueError:
        return ""
    if not parts:
        return ""
    workspace = "drafts" if parts[0].startswith("draft-") else parts[0]
    return f"workspace_{workspace}"


def project_slug_for_directory(
    directory: str,
    home: Path | None = None,
    project_roots: Sequence[str | Path] | None = None,
) -> str:
    """Map a ZCode working directory to a stable project slug.

    Generic rules live in :func:`llmwiki.session_export.project_slug_for_directory`;
    ZCode adds one: sessions under ``~/.zcode/workspace/<name>`` become
    ``workspace_<name>``, and one-off ``draft-*`` workspaces collapse into
    ``workspace_drafts``.
    """
    return _project_slug(
        directory, home, project_roots, extra_rule=_zcode_workspace_rule
    )


def validate_schema(con: sqlite3.Connection) -> None:
    """Fail clearly when ZCode changes the columns we depend on."""
    _validate_schema(con, REQUIRED_COLUMNS, label="ZCode")


def _context(data: dict[str, Any]) -> tuple[str, str]:
    context = data.get("contextSnapshot") or {}
    environment = context.get("envInfo") or {}
    return str(environment.get("cwd") or ""), str(environment.get("gitBranch") or "")


def _is_service_message(data: dict[str, Any]) -> bool:
    metadata = data.get("metadata") or {}
    if data.get("visibility") in MODEL_ONLY_VISIBILITY:
        return True
    if metadata.get("visibility") in MODEL_ONLY_VISIBILITY:
        return True
    origin = metadata.get("originMeta") or {}
    if origin.get("source") == "background_task":
        return True
    if (data.get("originMeta") or {}).get("source") == "background_task":
        return True
    return metadata.get("source") == "background_task"


def _fallback_user_text(data: dict[str, Any]) -> str:
    metadata = data.get("metadata") or {}
    for key in ("conversationInputIntent", "inputIntent"):
        intent = metadata.get(key) or {}
        text = intent.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()
    return ""


def _file_placeholder(part: dict[str, Any]) -> str:
    filename = str(part.get("filename") or "")
    if not filename:
        url = str(part.get("url") or "")
        filename = Path(url).name if url else ""
    if not filename:
        filename = str(part.get("mime") or "attachment")
    mime = str(part.get("mime") or "")
    kind = "image" if mime.startswith("image/") else "file"
    return f"[{kind}: {filename}]"


def _model(data: dict[str, Any]) -> str:
    model = data.get("modelId") or data.get("model") or "unknown"
    if isinstance(model, dict):
        model = model.get("modelId") or model.get("id") or "unknown"
    return str(model)


def _permission_mode(session: sqlite3.Row) -> str:
    raw = _row_get(session, "permission", "")
    if not raw:
        return ""
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, json.JSONDecodeError):
        return ""
    if isinstance(value, dict):
        return str(value.get("mode") or "")
    return ""


def session_to_records(con: sqlite3.Connection, session: sqlite3.Row) -> list[dict[str, Any]]:
    """Convert one ZCode session into the Claude-style record sequence."""
    session_id = str(session["id"])
    directory = str(_row_get(session, "directory", "") or "")
    title = str(_row_get(session, "title", "") or "")
    slug = str(_row_get(session, "slug", "") or session_id)
    permission_mode = _permission_mode(session)

    messages = con.execute(
        "select id, time_created, data, sequence from message"
        " where session_id = ? order by sequence, time_created",
        (session_id,),
    ).fetchall()

    records: list[dict[str, Any]] = []
    cwd = directory
    git_branch = ""

    for message in messages:
        try:
            data = json.loads(message["data"])
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict) or _is_service_message(data):
            continue

        role = data.get("role")
        message_time = _as_int(data.get("time", {}).get("created")) or _as_int(message["time_created"])
        context_cwd, context_branch = _context(data)
        if context_cwd:
            cwd = context_cwd
        if context_branch and not git_branch:
            git_branch = context_branch

        part_rows = con.execute(
            "select id, time_created, data, sequence from part"
            " where message_id = ? order by sequence, time_created",
            (message["id"],),
        ).fetchall()

        if role == "user":
            text_parts: list[str] = []
            for part_row in part_rows:
                try:
                    part = json.loads(part_row["data"])
                except (TypeError, json.JSONDecodeError):
                    continue
                if not isinstance(part, dict):
                    continue
                part_type = part.get("type")
                if part_type == "text":
                    text = str(part.get("text") or "").strip()
                    if text:
                        text_parts.append(text)
                elif part_type == "file":
                    text_parts.append(_file_placeholder(part))
            content = "\n\n".join(text_parts).strip()
            if not content:
                content = _fallback_user_text(data)
            if content:
                records.append(
                    {
                        "type": "user",
                        "timestamp": iso_from_ms(message_time),
                        "sessionId": session_id,
                        "cwd": cwd,
                        "message": {"role": "user", "content": content},
                    }
                )
            continue

        if role != "assistant":
            continue

        blocks: list[dict[str, Any]] = []
        tool_results: list[dict[str, Any]] = []
        tool_result_time = message_time
        for part_row in part_rows:
            try:
                part = json.loads(part_row["data"])
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(part, dict):
                continue
            part_type = part.get("type")
            if part_type == "text":
                text = str(part.get("text") or "").strip()
                if text:
                    blocks.append({"type": "text", "text": text})
            elif part_type == "file":
                blocks.append({"type": "text", "text": _file_placeholder(part)})
            elif part_type == "tool":
                state = part.get("state") or {}
                if not isinstance(state, dict):
                    state = {}
                tool_input = state.get("input")
                if not isinstance(tool_input, dict):
                    tool_input = {"value": tool_input} if tool_input is not None else {}
                call_id = str(part.get("callID") or part_row["id"])
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call_id,
                        "name": str(part.get("tool") or "Tool"),
                        "input": tool_input,
                    }
                )
                output = _as_text(state.get("output")).strip()
                if output:
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": call_id,
                            "content": output,
                            "is_error": state.get("status") in {"error", "failed"},
                        }
                    )
                    tool_result_time = max(tool_result_time, _as_int(part_row["time_created"]))
            elif part_type in SERVICE_PART_TYPES:
                continue

        if blocks:
            records.append(
                {
                    "type": "assistant",
                    "timestamp": iso_from_ms(message_time),
                    "sessionId": session_id,
                    "cwd": cwd,
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
                    "timestamp": iso_from_ms(tool_result_time),
                    "sessionId": session_id,
                    "cwd": cwd,
                    "message": {"role": "user", "content": tool_results},
                }
            )

    if not any(record["type"] in {"user", "assistant"} for record in records):
        return []

    init: dict[str, Any] = {
        "type": "init",
        "sessionId": session_id,
        "cwd": cwd,
        "timestamp": iso_from_ms(session["time_created"]),
        "slug": slug,
    }
    if title:
        init["title"] = title
    if git_branch:
        init["gitBranch"] = git_branch
    if permission_mode:
        init["permissionMode"] = permission_mode
    return [init, *records]


def _connect(db_path: Path) -> sqlite3.Connection:
    return connect_readonly(db_path, label="ZCode")


def schema_version(con: sqlite3.Connection) -> int | None:
    """Return ZCode's max ``schema_migration.id`` when the table exists."""
    try:
        row = con.execute("select max(id) from schema_migration").fetchone()
    except sqlite3.Error:
        return None
    if row is None or row[0] is None:
        return None
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return None


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
    """Export matching sessions and return counters for the CLI summary."""
    out_dir = Path(out_dir).expanduser()
    state_file = Path(state_file).expanduser() if state_file else out_dir / ".export-state.json"
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
        "schema_version": None,
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
        stats["schema_version"] = schema_version(con)
        sessions = con.execute("select * from session order by time_created, id").fetchall()
        for session in sessions:
            stats["total_sessions"] += 1
            session_id = str(session["id"])
            seen_sessions.add(session_id)
            task_type = str(_row_get(session, "task_type", "") or "")
            if not include_subagents and (task_type == "subagent_child" or _row_get(session, "parent_id")):
                stats["skipped_subagents"] += 1
                continue

            directory = str(_row_get(session, "directory", "") or "")
            slug = project_slug_for_directory(directory, project_roots=project_roots)
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
            out_path = out_dir / slug / f"{_safe_filename(session_id)}.jsonl"
            previous = sessions_state.get(session_id)
            previous_time = previous.get("time_updated") if isinstance(previous, dict) else previous
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export ZCode sessions to Claude-style JSONL for llmwiki.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s --project demo_project --dry-run\n"
            "  %(prog)s --project demo_project\n"
            "  %(prog)s --since 2026-09-01\n"
        ),
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="path to ZCode db.sqlite")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR, help="JSONL output directory")
    parser.add_argument("--state-file", type=Path, default=None, help="incremental state file")
    parser.add_argument("--since", default=None, help="only sessions updated on/after YYYY-MM-DD")
    parser.add_argument("--project", default=None, help="case-insensitive project path or slug substring")
    parser.add_argument(
        "--project-root",
        dest="project_roots",
        action="append",
        type=Path,
        default=None,
        help="treat this directory as a project root (repeatable)",
    )
    parser.add_argument("--include-subagents", action="store_true", help="include subagent_child sessions")
    parser.add_argument(
        "--keep-stale",
        action="store_true",
        help="do not remove JSONL for sessions deleted from the database",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit 1 when any session fails, even if others exported",
    )
    parser.add_argument("--dry-run", action="store_true", help="show counters without writing files")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        since_ms = parse_since(args.since)
    except ValueError as exc:
        parser.error(str(exc))

    try:
        stats = run_export(
            db_path=args.db,
            out_dir=args.out_dir,
            state_file=args.state_file,
            since=since_ms,
            project=args.project,
            include_subagents=args.include_subagents,
            dry_run=args.dry_run,
            project_roots=args.project_roots,
            prune_stale=not args.keep_stale,
        )
    except (SchemaError, sqlite3.Error, OSError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print("ZCode export")
    print(f"  db: {args.db}")
    print(f"  out: {args.out_dir}")
    if stats.get("schema_version") is not None:
        print(f"  schema: {stats['schema_version']}")
    print(f"  sessions: {stats['total_sessions']} total, {stats['selected']} selected")
    print(
        "  skipped: "
        f"{stats['skipped_subagents']} subagent, "
        f"{stats['skipped_project']} project, "
        f"{stats['skipped_since']} since"
    )
    print(
        f"  exported: {stats['exported']}, unchanged: {stats['unchanged']}, "
        f"empty: {stats['empty']}, errors: {stats['errors']}"
    )
    if args.dry_run:
        print("  dry-run: no files written")
    else:
        print(f"  written: {stats['written']} files")
    if stats.get("stale"):
        print(
            f"  stale: {stats['stale']} session(s) pruned, "
            f"{stats['stale_deleted']} file(s) removed"
        )
    if stats["errors"] and (args.strict or stats["written"] == 0):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
