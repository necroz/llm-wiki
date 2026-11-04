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
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Sequence

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
_PROJECT_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]")


class SchemaError(RuntimeError):
    """Raised when the ZCode database schema is not the one we expect."""


@lru_cache(maxsize=512)
def _git_toplevel(directory: str) -> Path | None:
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


def project_slug_for_directory(
    directory: str,
    home: Path | None = None,
    project_roots: Sequence[str | Path] | None = None,
) -> str:
    """Map a session working directory to a stable project slug.

    Resolution order:

    1. ``~/Projects/<name>`` — top-level name, so nested worktrees group
       under one project.
    2. ``~/.zcode/workspace/<name>`` — ``workspace_<name>``; ``draft-*``
       collapses into ``workspace_drafts``.
    3. A configured project root (``zcode.project_roots`` /
       ``--project-root``) — the first component under that root.
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

    parts = relative_parts(home / "Projects")
    if parts:
        name = parts[0]
    else:
        parts = relative_parts(home / ".zcode" / "workspace")
        if parts:
            workspace = parts[0]
            if workspace.startswith("draft-"):
                workspace = "drafts"
            name = f"workspace_{workspace}"
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
    """Fail clearly when ZCode changes the columns we depend on."""
    tables = {
        row[0]
        for row in con.execute("select name from sqlite_master where type = 'table'").fetchall()
    }
    missing: list[str] = []
    for table, required in REQUIRED_COLUMNS.items():
        if table not in tables:
            missing.append(table)
            continue
        columns = {row[1] for row in con.execute(f"pragma table_info({table})").fetchall()}
        missing.extend(f"{table}.{column}" for column in sorted(required - columns))
    if missing:
        raise SchemaError("missing required ZCode schema columns: " + ", ".join(missing))


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
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(
            json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _row_get(row: sqlite3.Row, key: str, default: Any = None) -> Any:
    try:
        keys = row.keys()
    except AttributeError:
        return default
    return row[key] if key in keys else default


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


def _safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", value).lstrip(".") or "session"


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _connect(db_path: Path) -> sqlite3.Connection:
    path = Path(db_path).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"ZCode database not found: {path}")
    con = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("pragma query_only = on")
    con.execute("pragma busy_timeout = 30000")
    return con


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
