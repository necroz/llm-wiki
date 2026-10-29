"""Tests for scripts/zcode_export.py.

The exporter is a standalone script, so it is loaded by path rather than
imported as a package module.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

T0 = 1_700_000_000_000
OLD = 1_600_000_000_000

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "zcode_export.py"


def load_exporter():
    assert SCRIPT.exists(), f"missing exporter: {SCRIPT}"
    spec = importlib.util.spec_from_file_location("zcode_export_under_test", SCRIPT)
    assert spec and spec.loader, f"cannot load exporter: {SCRIPT}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _session(
    con: sqlite3.Connection,
    session_id: str,
    directory: str,
    *,
    title: str = "",
    slug: str | None = None,
    task_type: str = "interactive",
    parent_id: str | None = None,
    created: int = T0,
    updated: int = T0,
    permission: str = '{"mode":"yolo"}',
) -> None:
    con.execute(
        "insert into session (id, parent_id, slug, directory, title, permission, time_created, time_updated, task_type)"
        " values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (session_id, parent_id, slug or session_id, directory, title, permission, created, updated, task_type),
    )


def _message(
    con: sqlite3.Connection,
    message_id: str,
    session_id: str,
    sequence: int,
    role: str,
    created: int,
    *,
    metadata: dict | None = None,
    context: dict | None = None,
    extra: dict | None = None,
) -> None:
    data = {"role": role, "time": {"created": created}}
    if role == "assistant":
        data["modelId"] = "model-x"
        data["tokens"] = {"input": 10, "output": 20, "reasoning": 5, "cache": {"read": 3, "write": 4}}
    if metadata is not None:
        data["metadata"] = metadata
    if context is not None:
        data["contextSnapshot"] = {"envInfo": context}
    if extra:
        data.update(extra)
    con.execute(
        "insert into message (id, session_id, time_created, data, sequence) values (?, ?, ?, ?, ?)",
        (message_id, session_id, created, json.dumps(data), sequence),
    )


def _part(
    con: sqlite3.Connection,
    part_id: str,
    message_id: str,
    session_id: str,
    sequence: int,
    data: dict,
    created: int = T0,
) -> None:
    con.execute(
        "insert into part (id, message_id, session_id, time_created, data, sequence) values (?, ?, ?, ?, ?, ?)",
        (part_id, message_id, session_id, created, json.dumps(data, ensure_ascii=False), sequence),
    )


def make_db(tmp_path: Path) -> Path:
    db = tmp_path / "db.sqlite"
    con = sqlite3.connect(db)
    con.executescript(
        """
        create table session (
            id text primary key,
            parent_id text,
            slug text,
            directory text,
            title text,
            permission text,
            time_created integer,
            time_updated integer,
            task_type text
        );
        create table message (
            id text primary key,
            session_id text,
            time_created integer,
            data text,
            sequence integer
        );
        create table part (
            id text primary key,
            message_id text,
            session_id text,
            time_created integer,
            data text,
            sequence integer
        );
        """
    )

    _session(con, "sess-1", "/home/test/Projects/neuromerch_project", title="Test session", updated=T0 + 100_000)
    _session(con, "sess-2", "/home/test/Projects/other", title="Other session", created=OLD, updated=OLD + 1_000)
    _session(
        con,
        "sess-sub",
        "/home/test/Projects/neuromerch_project",
        title="Subagent session",
        task_type="subagent_child",
        parent_id="sess-1",
    )
    _session(con, "sess-empty", "/home/test/Projects/neuromerch_project", title="Empty session")

    # sess-1: real prompt, assistant text + tool call, model-only noise,
    # background task notification, second prompt, attachment-only prompt.
    _message(
        con,
        "m1",
        "sess-1",
        0,
        "user",
        T0,
        context={"cwd": "/home/test/Projects/neuromerch_project", "gitBranch": "main"},
    )
    _part(con, "p1", "m1", "sess-1", 0, {"type": "text", "text": "hello"}, T0)

    _message(
        con,
        "m2",
        "sess-1",
        1,
        "assistant",
        T0 + 1_000,
        extra={"path": {"cwd": "/home/test/Projects/neuromerch_project"}},
    )
    _part(con, "p2", "m2", "sess-1", 0, {"type": "step-start"}, T0 + 1_000)
    _part(con, "p3", "m2", "sess-1", 1, {"type": "reasoning", "text": "private thinking"}, T0 + 1_000)
    _part(con, "p4", "m2", "sess-1", 2, {"type": "text", "text": "Hi there"}, T0 + 1_000)
    _part(
        con,
        "p5",
        "m2",
        "sess-1",
        3,
        {
            "type": "tool",
            "tool": "Bash",
            "callID": "call-1",
            "state": {"status": "completed", "input": {"command": "pwd"}, "output": "/tmp"},
        },
        T0 + 1_500,
    )
    _part(con, "p6", "m2", "sess-1", 4, {"type": "step-finish"}, T0 + 1_500)

    _message(con, "m3", "sess-1", 2, "user", T0 + 2_000, metadata={"visibility": "model-only"}, extra={"synthetic": True})
    _part(con, "p7", "m3", "sess-1", 0, {"type": "text", "text": "system noise"}, T0 + 2_000)

    _message(con, "m4", "sess-1", 3, "user", T0 + 3_000)
    _part(con, "p8", "m4", "sess-1", 0, {"type": "text", "text": "second question"}, T0 + 3_000)

    _message(
        con,
        "m5",
        "sess-1",
        4,
        "user",
        T0 + 4_000,
        metadata={"originMeta": {"source": "background_task"}},
    )
    _part(con, "p9", "m5", "sess-1", 0, {"type": "text", "text": "task notification"}, T0 + 4_000)

    _message(con, "m6", "sess-1", 5, "user", T0 + 5_000)
    _part(
        con,
        "p10",
        "m6",
        "sess-1",
        0,
        {"type": "file", "mime": "image/png", "filename": "shot.png", "url": "zcode-artifact://shot"},
        T0 + 5_000,
    )

    # subagent session
    _message(con, "m10", "sess-sub", 0, "user", T0)
    _part(con, "p20", "m10", "sess-sub", 0, {"type": "text", "text": "sub task"}, T0)
    _message(con, "m11", "sess-sub", 1, "assistant", T0 + 1_000)
    _part(con, "p21", "m11", "sess-sub", 0, {"type": "text", "text": "sub answer"}, T0 + 1_000)

    # older session
    _message(con, "m20", "sess-2", 0, "user", OLD)
    _part(con, "p30", "m20", "sess-2", 0, {"type": "text", "text": "old question"}, OLD)
    _message(con, "m21", "sess-2", 1, "assistant", OLD + 1_000)
    _part(con, "p31", "m21", "sess-2", 0, {"type": "text", "text": "old answer"}, OLD + 1_000)

    con.commit()
    con.close()
    return db


@pytest.mark.parametrize(
    ("directory", "expected"),
    [
        ("/home/test/Projects/neuromerch_project", "neuromerch_project"),
        ("/home/test/Projects/larisa-ngdu/tmp/work", "larisa-ngdu"),
        ("/home/test/.zcode/workspace/default", "workspace_default"),
        ("/home/test/.zcode/workspace/draft-abc", "workspace_drafts"),
        ("/home/test/.zcode/workspace/draft-xyz", "workspace_drafts"),
        ("/home/test/ZCodeProject", "ZCodeProject"),
        ("/home/test/Work/project-a", "project-a"),
        ("/home/test/Work", "Work"),
        ("/tmp/foo", "foo"),
        ("/home/test/Projects/bad name", "bad_name"),
        ("/", "unknown"),
    ],
)
def test_project_slug_for_directory(directory: str, expected: str) -> None:
    exporter = load_exporter()
    assert exporter.project_slug_for_directory(directory, home=Path("/home/test")) == expected


def test_iso_from_ms_formats_utc() -> None:
    exporter = load_exporter()
    assert exporter.iso_from_ms(T0) == "2023-11-14T22:13:20.000Z"
    assert exporter.iso_from_ms(None) is None


def test_parse_since_accepts_date_and_datetime() -> None:
    exporter = load_exporter()
    assert exporter.parse_since(None) is None
    assert exporter.parse_since("2023-11-14") == 1_699_920_000_000
    assert exporter.parse_since("2023-11-14T22:13:20Z") == T0


def test_session_to_records_maps_conversation_and_filters_service_parts(tmp_path: Path) -> None:
    exporter = load_exporter()
    db = make_db(tmp_path)
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    session = con.execute("select * from session where id = ?", ("sess-1",)).fetchone()

    records = exporter.session_to_records(con, session)
    con.close()

    assert [r["type"] for r in records] == ["init", "user", "assistant", "user", "user", "user"]
    assert records[0]["sessionId"] == "sess-1"
    assert records[0]["slug"] == "sess-1"
    assert records[0]["title"] == "Test session"
    assert records[0]["gitBranch"] == "main"
    assert records[0]["permissionMode"] == "yolo"

    assert records[1]["message"]["content"] == "hello"
    assert records[2]["message"]["content"] == [
        {"type": "text", "text": "Hi there"},
        {"type": "tool_use", "id": "call-1", "name": "Bash", "input": {"command": "pwd"}},
    ]
    assert records[2]["message"]["model"] == "model-x"
    assert records[2]["message"]["usage"] == {
        "input_tokens": 10,
        "output_tokens": 20,
        "cache_read_input_tokens": 3,
        "cache_creation_input_tokens": 4,
    }
    assert records[2]["timestamp"] == "2023-11-14T22:13:21.000Z"

    assert records[3]["message"]["content"] == [
        {"type": "tool_result", "tool_use_id": "call-1", "content": "/tmp", "is_error": False}
    ]
    assert records[3]["timestamp"] == "2023-11-14T22:13:21.500Z"

    assert records[4]["message"]["content"] == "second question"
    assert records[5]["message"]["content"] == "[image: shot.png]"
    assert all(r["sessionId"] == "sess-1" for r in records)


def test_validate_schema_reports_missing_columns(tmp_path: Path) -> None:
    exporter = load_exporter()
    db = tmp_path / "broken.sqlite"
    con = sqlite3.connect(db)
    con.executescript(
        """
        create table session (id text, task_type text, parent_id text, time_created integer, time_updated integer);
        create table message (id text, session_id text, time_created integer, data text, sequence integer);
        create table part (id text, message_id text, session_id text, time_created integer, data text, sequence integer);
        """
    )
    with pytest.raises(exporter.SchemaError) as exc:
        exporter.validate_schema(con)
    con.close()
    assert "session.directory" in str(exc.value)


def test_export_writes_jsonl_and_is_idempotent(tmp_path: Path) -> None:
    exporter = load_exporter()
    db = make_db(tmp_path)
    out = tmp_path / "out"

    first = exporter.run_export(db, out)
    assert first["selected"] == 3
    assert first["exported"] == 2
    assert first["unchanged"] == 0
    assert first["empty"] == 1
    assert first["written"] == 2

    path = out / "neuromerch_project" / "sess-1.jsonl"
    assert path.exists()
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [r["type"] for r in records] == ["init", "user", "assistant", "user", "user", "user"]

    second = exporter.run_export(db, out)
    assert second["selected"] == 3
    assert second["exported"] == 0
    assert second["unchanged"] == 2
    assert second["empty"] == 1
    assert second["written"] == 0

    (out / "other" / "sess-2.jsonl").unlink()
    third = exporter.run_export(db, out)
    assert third["exported"] == 1
    assert third["unchanged"] == 1
    assert third["written"] == 1
    assert (out / "other" / "sess-2.jsonl").exists()


def test_export_rewrites_changed_session(tmp_path: Path) -> None:
    exporter = load_exporter()
    db = make_db(tmp_path)
    out = tmp_path / "out"
    exporter.run_export(db, out)

    con = sqlite3.connect(db)
    con.execute("update session set time_updated = ? where id = ?", (T0 + 200_000, "sess-1"))
    _message(con, "m7", "sess-1", 6, "user", T0 + 6_000)
    _part(con, "p11", "m7", "sess-1", 0, {"type": "text", "text": "third question"}, T0 + 6_000)
    con.commit()
    con.close()

    stats = exporter.run_export(db, out)
    assert stats["exported"] == 1
    assert stats["unchanged"] == 1
    assert stats["written"] == 1

    text = (out / "neuromerch_project" / "sess-1.jsonl").read_text(encoding="utf-8")
    assert "third question" in text


def test_export_dry_run_writes_nothing(tmp_path: Path) -> None:
    exporter = load_exporter()
    db = make_db(tmp_path)
    out = tmp_path / "out"

    stats = exporter.run_export(db, out, dry_run=True)
    assert stats["exported"] == 2
    assert stats["written"] == 0
    assert not out.exists()


def test_export_project_filter(tmp_path: Path) -> None:
    exporter = load_exporter()
    db = make_db(tmp_path)
    out = tmp_path / "out"

    stats = exporter.run_export(db, out, project="neuromerch")
    assert stats["selected"] == 2
    assert stats["exported"] == 1
    assert stats["empty"] == 1
    assert (out / "neuromerch_project" / "sess-1.jsonl").exists()
    assert not (out / "other").exists()


def test_export_since_filter(tmp_path: Path) -> None:
    exporter = load_exporter()
    db = make_db(tmp_path)
    out = tmp_path / "out"

    stats = exporter.run_export(db, out, since=T0 - 1)
    assert stats["selected"] == 2
    assert stats["exported"] == 1
    assert (out / "neuromerch_project" / "sess-1.jsonl").exists()
    assert not (out / "other").exists()


def test_export_subagents_only_with_flag(tmp_path: Path) -> None:
    exporter = load_exporter()
    db = make_db(tmp_path)

    default_out = tmp_path / "default"
    default_stats = exporter.run_export(db, default_out)
    assert default_stats["selected"] == 3
    assert not (default_out / "neuromerch_project" / "sess-sub.jsonl").exists()

    all_out = tmp_path / "all"
    all_stats = exporter.run_export(db, all_out, include_subagents=True)
    assert all_stats["selected"] == 4
    assert all_stats["exported"] == 3
    assert (all_out / "neuromerch_project" / "sess-sub.jsonl").exists()


def test_main_dry_run_returns_zero(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    exporter = load_exporter()
    db = make_db(tmp_path)
    out = tmp_path / "out"

    rc = exporter.main(["--db", str(db), "--out-dir", str(out), "--dry-run", "--project", "neuromerch"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "dry-run" in captured.out.lower()
    assert "exported" in captured.out.lower()
    assert not out.exists()
