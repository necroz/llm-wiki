"""Shared SQLite → JSONL plumbing used by the ZCode and OpenCode exporters."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from llmwiki import session_export as common
from llmwiki.opencode import export as opencode_export
from llmwiki.zcode import export as zcode_export


def test_both_exporters_use_the_shared_helpers() -> None:
    # Guard against the copy-paste coming back.
    for name in ("iso_from_ms", "load_state", "save_state", "SchemaError"):
        assert getattr(zcode_export, name) is getattr(common, name)
        assert getattr(opencode_export, name) is getattr(common, name)


def test_extra_rule_runs_after_projects_and_before_configured_roots(tmp_path) -> None:
    home = tmp_path / "home"
    calls: list[Path] = []

    def rule(path: Path, rule_home: Path) -> str:
        calls.append(path)
        return "from_rule"

    # ~/Projects wins; the rule is not even consulted.
    assert common.project_slug_for_directory(
        str(home / "Projects" / "app" / "sub"), home=home, extra_rule=rule
    ) == "app"
    assert calls == []

    # Outside ~/Projects the rule beats a configured root.
    root = tmp_path / "work"
    assert common.project_slug_for_directory(
        str(root / "proj"), home=home, project_roots=[root], extra_rule=rule
    ) == "from_rule"


def test_extra_rule_returning_empty_falls_through(tmp_path) -> None:
    root = tmp_path / "work"
    assert common.project_slug_for_directory(
        str(root / "proj" / "nested"),
        home=tmp_path / "home",
        project_roots=[root],
        extra_rule=lambda path, home: "",
    ) == "proj"


def test_zcode_keeps_its_workspace_rule_and_opencode_does_not(tmp_path) -> None:
    home = tmp_path
    draft = str(home / ".zcode" / "workspace" / "draft-abc")
    assert zcode_export.project_slug_for_directory(draft, home=home) == "workspace_drafts"
    assert opencode_export.project_slug_for_directory(draft, home=home) != "workspace_drafts"


def test_connect_and_schema_errors_name_the_tool(tmp_path) -> None:
    with pytest.raises(FileNotFoundError, match="OpenCode database not found"):
        opencode_export._connect(tmp_path / "missing.db")
    with pytest.raises(FileNotFoundError, match="ZCode database not found"):
        zcode_export._connect(tmp_path / "missing.db")

    db = tmp_path / "empty.db"
    sqlite3.connect(db).close()
    con = common.connect_readonly(db, label="X")
    with pytest.raises(common.SchemaError, match="missing required X schema columns: t"):
        common.validate_schema(con, {"t": {"a"}}, label="X")
    con.close()


def test_connect_readonly_refuses_writes(tmp_path) -> None:
    db = tmp_path / "data.db"
    setup = sqlite3.connect(db)
    setup.execute("create table t (a)")
    setup.commit()
    setup.close()

    con = common.connect_readonly(db, label="X")
    with pytest.raises(sqlite3.OperationalError):
        con.execute("insert into t values (1)")
    con.close()


def test_save_state_round_trips_and_leaves_no_temp(tmp_path) -> None:
    target = tmp_path / "state" / ".export-state.json"
    common.save_state(target, {"version": 1, "sessions": {"s": 1}})

    assert common.load_state(target) == {"version": 1, "sessions": {"s": 1}}
    assert [p.name for p in target.parent.iterdir()] == [".export-state.json"]
    assert target.read_text(encoding="utf-8").endswith("\n")


def test_write_jsonl_writes_one_compact_record_per_line(tmp_path) -> None:
    target = tmp_path / "p" / "s.jsonl"
    common.write_jsonl(target, [{"a": 1}, {"b": "ü"}])

    lines = target.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line) for line in lines] == [{"a": 1}, {"b": "ü"}]
    assert lines[1] == '{"b":"ü"}'
