"""CLI integration for ZCode export and auto-export during sync."""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import MagicMock

from llmwiki import cli


def _sync_args(tmp_path: Path, **overrides):
    values = {
        "status": False,
        "adapter": ["zcode"],
        "since": None,
        "project": None,
        "include_current": False,
        "force": False,
        "dry_run": False,
        "auto_build": False,
        "auto_lint": False,
        "vault": None,
        "allow_overwrite": False,
        "zcode_export": True,
        "zcode_db": tmp_path / "db.sqlite",
        "zcode_out_dir": tmp_path / "jsonl",
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_sync_parser_accepts_dry_run():
    from llmwiki.cli import build_parser

    args = build_parser().parse_args(["sync", "--dry-run"])

    assert args.dry_run is True


def test_sync_runs_zcode_export_when_adapter_selected(tmp_path, monkeypatch):
    db = tmp_path / "db.sqlite"
    db.write_bytes(b"not a real database")

    exporter = MagicMock(
        return_value={
            "exported": 2,
            "unchanged": 1,
            "empty": 0,
            "errors": 0,
            "written": 2,
        }
    )
    monkeypatch.setattr("llmwiki.zcode.export.run_export", exporter)
    monkeypatch.setattr("llmwiki.convert.convert_all", lambda **_: 0)
    monkeypatch.setattr(
        cli, "_load_run_config", lambda: {"zcode": {"enabled": True}}
    )

    rc = cli.cmd_sync(_sync_args(tmp_path))

    assert rc == 0
    exporter.assert_called_once()
    assert exporter.call_args.args[0] == db


def test_sync_skips_zcode_export_for_other_adapters(tmp_path, monkeypatch):
    db = tmp_path / "db.sqlite"
    db.write_bytes(b"not a real database")

    exporter = MagicMock()
    monkeypatch.setattr("llmwiki.zcode.export.run_export", exporter)
    monkeypatch.setattr("llmwiki.convert.convert_all", lambda **_: 0)
    monkeypatch.setattr(
        cli, "_load_run_config", lambda: {"zcode": {"enabled": True}}
    )

    rc = cli.cmd_sync(_sync_args(tmp_path, adapter=["claude_code"]))

    assert rc == 0
    exporter.assert_not_called()


def test_sync_skips_zcode_export_when_disabled_in_config(tmp_path, monkeypatch):
    db = tmp_path / "db.sqlite"
    db.write_bytes(b"not a real database")

    exporter = MagicMock()
    monkeypatch.setattr("llmwiki.zcode.export.run_export", exporter)
    monkeypatch.setattr("llmwiki.convert.convert_all", lambda **_: 0)
    monkeypatch.setattr(
        cli, "_load_run_config", lambda: {"zcode": {"enabled": False}}
    )

    rc = cli.cmd_sync(_sync_args(tmp_path, adapter=None))

    assert rc == 0
    exporter.assert_not_called()


def test_explicit_zcode_adapter_overrides_disabled_config(tmp_path, monkeypatch):
    db = tmp_path / "db.sqlite"
    db.write_bytes(b"not a real database")

    exporter = MagicMock(
        return_value={"exported": 0, "unchanged": 0, "empty": 0, "errors": 0}
    )
    monkeypatch.setattr("llmwiki.zcode.export.run_export", exporter)
    monkeypatch.setattr("llmwiki.convert.convert_all", lambda **_: 0)
    monkeypatch.setattr(
        cli, "_load_run_config", lambda: {"zcode": {"enabled": False}}
    )

    rc = cli.cmd_sync(_sync_args(tmp_path, adapter=["zcode"]))

    assert rc == 0
    exporter.assert_called_once()


def test_sync_passes_configured_project_roots(tmp_path, monkeypatch):
    db = tmp_path / "db.sqlite"
    db.write_bytes(b"not a real database")
    exporter = MagicMock(
        return_value={"exported": 0, "unchanged": 0, "errors": 0}
    )
    monkeypatch.setattr("llmwiki.zcode.export.run_export", exporter)
    monkeypatch.setattr("llmwiki.convert.convert_all", lambda **_: 0)
    monkeypatch.setattr(
        cli,
        "_load_run_config",
        lambda: {"zcode": {"enabled": True, "project_roots": ["/srv/dev"]}},
    )

    rc = cli.cmd_sync(_sync_args(tmp_path))

    assert rc == 0
    assert exporter.call_args.kwargs["project_roots"] == ["/srv/dev"]


def test_sync_dry_run_keeps_zcode_export_dry(tmp_path, monkeypatch):
    db = tmp_path / "db.sqlite"
    db.write_bytes(b"not a real database")

    exporter = MagicMock(
        return_value={"exported": 0, "unchanged": 0, "empty": 0, "errors": 0}
    )
    monkeypatch.setattr("llmwiki.zcode.export.run_export", exporter)
    monkeypatch.setattr("llmwiki.convert.convert_all", lambda **_: 0)
    monkeypatch.setattr(
        cli, "_load_run_config", lambda: {"zcode": {"enabled": True}}
    )

    rc = cli.cmd_sync(_sync_args(tmp_path, dry_run=True))

    assert rc == 0
    assert exporter.call_args.kwargs["dry_run"] is True


def test_sync_dry_run_does_not_auto_build(tmp_path, monkeypatch):
    db = tmp_path / "db.sqlite"
    db.write_bytes(b"not a real database")

    build_calls: list[dict] = []
    monkeypatch.setattr(
        "llmwiki.zcode.export.run_export",
        MagicMock(return_value={"exported": 0, "unchanged": 0, "errors": 0}),
    )
    monkeypatch.setattr("llmwiki.convert.convert_all", lambda **_: 0)
    monkeypatch.setattr("llmwiki.build.build_site", lambda **kwargs: build_calls.append(kwargs))
    monkeypatch.setattr(cli, "_load_schedule_config", lambda: {"build": "on-sync"})
    monkeypatch.setattr(
        cli, "_load_run_config", lambda: {"zcode": {"enabled": True}}
    )

    rc = cli.cmd_sync(_sync_args(tmp_path, dry_run=True, auto_build=True))

    assert rc == 0
    assert build_calls == []
