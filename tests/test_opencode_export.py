"""Tests for the OpenCode V2 SQLite exporter."""

from __future__ import annotations

import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from llmwiki import session_export
from llmwiki.opencode import export

T0 = 1_700_000_000_000
OLD = 1_600_000_000_000


def _session(
    con: sqlite3.Connection,
    session_id: str,
    directory: str,
    *,
    project_id: str = "proj-1",
    parent_id: str | None = None,
    title: str = "",
    created: int = T0,
    updated: int = T0,
    model: str = '{"id":"model-x","providerID":"p"}',
) -> None:
    con.execute(
        "insert into session_v2"
        " (id, project_id, parent_id, directory, title, time_created, time_updated, model)"
        " values (?, ?, ?, ?, ?, ?, ?, ?)",
        (session_id, project_id, parent_id, directory, title, created, updated, model),
    )


def _message(
    con: sqlite3.Connection,
    message_id: str,
    session_id: str,
    seq: int,
    kind: str,
    created: int,
    data: dict,
) -> None:
    con.execute(
        "insert into session_message"
        " (id, session_id, type, seq, time_created, time_updated, data)"
        " values (?, ?, ?, ?, ?, ?, ?)",
        (
            message_id,
            session_id,
            kind,
            seq,
            created,
            created,
            json.dumps(data, ensure_ascii=False),
        ),
    )


def make_db(tmp_path: Path) -> Path:
    db = tmp_path / "opencode.db"
    con = sqlite3.connect(db)
    con.executescript(
        """
        create table project (
            id text primary key,
            worktree text
        );
        create table session_v2 (
            id text primary key,
            project_id text,
            parent_id text,
            directory text,
            title text,
            time_created integer,
            time_updated integer,
            model text
        );
        create table session_message (
            id text primary key,
            session_id text,
            type text,
            seq integer,
            time_created integer,
            time_updated integer,
            data text
        );
        """
    )
    con.execute(
        "insert into project values (?, ?)",
        ("proj-1", "/home/test/Projects/demo"),
    )
    _session(
        con,
        "ses-1",
        "/home/test/Projects/demo",
        title="Demo session",
        updated=T0 + 5_000,
    )
    _message(
        con,
        "m1",
        "ses-1",
        0,
        "user",
        T0,
        {
            "time": {"created": T0},
            "text": "hello",
            "files": [{"name": "shot.png", "mime": "image/png"}],
        },
    )
    _message(
        con,
        "m2",
        "ses-1",
        1,
        "assistant",
        T0 + 1_000,
        {
            "time": {"created": T0 + 1_000},
            "agent": "build",
            "model": {"id": "model-x", "providerID": "p"},
            "content": [
                {"type": "reasoning", "text": "private thinking"},
                {"type": "text", "text": "hi"},
                {
                    "type": "tool",
                    "id": "call-1",
                    "name": "bash",
                    "state": {
                        "status": "completed",
                        "input": {"command": "pwd"},
                        "content": [{"type": "text", "text": "/tmp"}],
                    },
                },
            ],
        },
    )
    _message(
        con,
        "m3",
        "ses-1",
        2,
        "idle",
        T0 + 2_000,
        {"type": "idle", "outcome": "succeeded"},
    )
    _session(
        con,
        "ses-sub",
        "/home/test/Projects/demo",
        parent_id="ses-1",
        title="Subagent",
        updated=T0 + 6_000,
    )
    _message(
        con,
        "m4",
        "ses-sub",
        0,
        "user",
        T0,
        {"time": {"created": T0}, "text": "sub task", "files": []},
    )
    _message(
        con,
        "m5",
        "ses-sub",
        1,
        "assistant",
        T0 + 1_000,
        {
            "time": {"created": T0 + 1_000},
            "content": [{"type": "text", "text": "sub answer"}],
        },
    )
    _session(
        con,
        "ses-empty",
        "/home/test/Projects/demo",
        title="Empty",
        updated=T0 + 7_000,
    )
    _message(
        con,
        "m6",
        "ses-empty",
        0,
        "idle",
        T0 + 7_000,
        {"type": "idle", "outcome": "succeeded"},
    )
    _session(
        con,
        "ses-old",
        "/home/test/Projects/other",
        title="Old",
        created=OLD,
        updated=OLD + 1_000,
    )
    _message(
        con,
        "m7",
        "ses-old",
        0,
        "user",
        OLD,
        {"time": {"created": OLD}, "text": "old question"},
    )
    _message(
        con,
        "m8",
        "ses-old",
        1,
        "assistant",
        OLD + 1_000,
        {
            "time": {"created": OLD + 1_000},
            "content": [{"type": "text", "text": "old answer"}],
        },
    )
    con.commit()
    con.close()
    return db


def test_validate_schema_accepts_expected_tables(tmp_path: Path) -> None:
    con = sqlite3.connect(make_db(tmp_path))
    try:
        export.validate_schema(con)
    finally:
        con.close()


def test_validate_schema_rejects_missing_message_data(tmp_path: Path) -> None:
    db = tmp_path / "broken.db"
    con = sqlite3.connect(db)
    con.executescript(
        """
        create table session_v2 (id text, project_id text, parent_id text,
            directory text, title text, time_created integer,
            time_updated integer, model text);
        create table session_message (id text, session_id text, type text,
            seq integer, time_created integer, time_updated integer);
        """
    )
    with pytest.raises(export.SchemaError, match="session_message.data"):
        export.validate_schema(con)
    con.close()


def test_iso_from_ms_formats_utc() -> None:
    assert export.iso_from_ms(T0) == "2023-11-14T22:13:20.000Z"
    assert export.iso_from_ms(None) is None


def test_parse_since_accepts_date_and_datetime() -> None:
    assert export.parse_since(None) is None
    assert export.parse_since("2023-11-14") == 1_699_920_000_000
    assert export.parse_since("2023-11-14T22:13:20Z") == T0


@pytest.mark.parametrize(
    ("directory", "expected"),
    [
        ("/home/test/Projects/demo", "demo"),
        ("/home/test/Projects/demo/nested", "demo"),
        ("/home/test/Work/project-a", "project-a"),
        ("/tmp/foo", "foo"),
        ("/home/test/Projects/bad name", "bad_name"),
    ],
)
def test_project_slug_for_directory(directory: str, expected: str) -> None:
    assert (
        export.project_slug_for_directory(directory, home=Path("/home/test"))
        == expected
    )


def test_session_to_records_maps_user_assistant_and_tool(tmp_path: Path) -> None:
    con = sqlite3.connect(make_db(tmp_path))
    con.row_factory = sqlite3.Row
    try:
        session = con.execute(
            "select * from session_v2 where id = 'ses-1'"
        ).fetchone()
        records = export.session_to_records(con, session)
    finally:
        con.close()

    assert records[0]["type"] == "init"
    assert records[0]["title"] == "Demo session"
    assert records[0]["cwd"] == "/home/test/Projects/demo"

    user = records[1]
    assert user["type"] == "user"
    assert user["message"]["content"] == "hello\n\n[file: shot.png]"

    assistant = records[2]
    assert assistant["type"] == "assistant"
    assert assistant["message"]["content"][0] == {"type": "text", "text": "hi"}
    tool = assistant["message"]["content"][1]
    assert tool["type"] == "tool_use"
    assert tool["name"] == "bash"
    assert tool["input"] == {"command": "pwd"}

    tool_result = records[3]
    assert tool_result["type"] == "user"
    assert tool_result["message"]["content"][0]["type"] == "tool_result"
    assert tool_result["message"]["content"][0]["content"] == "/tmp"
    assert all(
        block.get("type") != "reasoning"
        for record in records
        if record["type"] == "assistant"
        for block in record["message"]["content"]
    )


def test_session_to_records_keeps_tool_only_assistant_turn(
    tmp_path: Path,
) -> None:
    db = make_db(tmp_path)
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    _session(con, "ses-tool", "/home/test/Projects/demo", title="Tool only")
    _message(
        con,
        "m-tool",
        "ses-tool",
        0,
        "assistant",
        T0,
        {
            "time": {"created": T0},
            "content": [
                {
                    "type": "tool",
                    "id": "call-tool",
                    "name": "read",
                    "state": {
                        "status": "completed",
                        "input": {"path": "a.py"},
                        "content": [{"type": "text", "text": "print(1)"}],
                    },
                }
            ],
        },
    )
    con.commit()
    try:
        session = con.execute(
            "select * from session_v2 where id = 'ses-tool'"
        ).fetchone()
        records = export.session_to_records(con, session)
    finally:
        con.close()

    assert records[0]["type"] == "init"
    assert records[1]["type"] == "assistant"
    assert records[1]["message"]["content"][0]["type"] == "tool_use"
    assert records[2]["message"]["content"][0]["tool_use_id"] == "call-tool"


def test_session_to_records_returns_empty_for_no_conversation(tmp_path: Path) -> None:
    con = sqlite3.connect(make_db(tmp_path))
    con.row_factory = sqlite3.Row
    try:
        session = con.execute(
            "select * from session_v2 where id = 'ses-empty'"
        ).fetchone()
        assert export.session_to_records(con, session) == []
    finally:
        con.close()


def test_session_directory_falls_back_to_project_worktree(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    con = sqlite3.connect(db)
    con.execute(
        "update session_v2 set directory = '' where id = 'ses-1'"
    )
    con.commit()
    con.close()

    out = tmp_path / "jsonl"
    stats = export.run_export(db, out)
    assert stats["exported"] == 2
    assert (out / "demo" / "ses-1.jsonl").is_file()


def test_run_export_writes_jsonl_and_is_incremental(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    out = tmp_path / "jsonl"

    first = export.run_export(db, out)
    assert first["exported"] == 2
    assert first["unchanged"] == 0
    assert (out / "demo" / "ses-1.jsonl").is_file()
    assert (out / "other" / "ses-old.jsonl").is_file()

    second = export.run_export(db, out)
    assert second["exported"] == 0
    assert second["unchanged"] == 2

    con = sqlite3.connect(db)
    con.execute(
        "update session_v2 set time_updated = ? where id = 'ses-1'",
        (T0 + 9_000,),
    )
    _message(
        con,
        "m9",
        "ses-1",
        3,
        "assistant",
        T0 + 9_000,
        {
            "time": {"created": T0 + 9_000},
            "content": [{"type": "text", "text": "more"}],
        },
    )
    con.commit()
    con.close()

    third = export.run_export(db, out)
    assert third["exported"] == 1
    assert third["unchanged"] == 1


def test_run_export_skips_and_includes_subagents(tmp_path: Path) -> None:
    db = make_db(tmp_path)

    skipped = export.run_export(db, tmp_path / "default")
    assert skipped["skipped_subagents"] == 1
    assert not (tmp_path / "default" / "demo" / "subagent-ses-sub.jsonl").exists()

    included = export.run_export(
        db, tmp_path / "included", include_subagents=True
    )
    assert included["exported"] == 3
    assert (
        tmp_path / "included" / "demo" / "subagent-ses-sub.jsonl"
    ).is_file()


def test_run_export_prunes_stale_sessions(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    out = tmp_path / "jsonl"
    export.run_export(db, out)
    stale = out / "other" / "ses-old.jsonl"
    assert stale.is_file()

    con = sqlite3.connect(db)
    con.execute("delete from session_v2 where id = 'ses-old'")
    con.execute("delete from session_message where session_id = 'ses-old'")
    con.commit()
    con.close()

    stats = export.run_export(db, out)
    assert stats["stale"] == 1
    assert stats["stale_deleted"] == 1
    assert not stale.exists()


def test_run_export_does_not_prune_when_database_has_no_sessions(
    tmp_path: Path,
) -> None:
    db = make_db(tmp_path)
    out = tmp_path / "jsonl"
    export.run_export(db, out)
    stale = out / "other" / "ses-old.jsonl"
    assert stale.is_file()

    con = sqlite3.connect(db)
    con.execute("delete from session_message")
    con.execute("delete from session_v2")
    con.commit()
    con.close()

    stats = export.run_export(db, out)

    assert stats["total_sessions"] == 0
    assert stats["stale_deleted"] == 0
    assert stale.is_file()


def test_write_jsonl_uses_distinct_temp_files(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "session.jsonl"
    seen: list[str] = []
    barrier = threading.Barrier(2)
    real_replace = session_export.os.replace

    def replace(src, dst):
        seen.append(Path(src).name)
        barrier.wait(timeout=5)
        real_replace(src, dst)

    monkeypatch.setattr(session_export.os, "replace", replace)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(export._write_jsonl, path, [{"i": index}])
            for index in (1, 2)
        ]
        for future in futures:
            future.result()

    assert len(set(seen)) == 2


def test_run_export_dry_run_writes_nothing(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    out = tmp_path / "jsonl"
    stats = export.run_export(db, out, dry_run=True)
    assert stats["exported"] == 2
    assert not out.exists()
