"""CLI wiring for the OpenCode exporter and autosync."""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import MagicMock

from llmwiki import cli
from llmwiki.opencode import export


def _args(**overrides):
    values = {
        "opencode_export": True,
        "adapter": None,
        "opencode_db": None,
        "since": None,
        "project": None,
        "dry_run": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_sync_parser_has_opencode_export_flags():
    args = cli.build_parser().parse_args(["sync", "--no-opencode-export"])
    assert args.opencode_export is False
    assert args.opencode_db is None


def test_parser_opencode_export():
    args = cli.build_parser().parse_args(["opencode", "export", "--dry-run"])
    assert args.opencode_command == "export"
    assert args.dry_run is True


def test_parser_opencode_autosync():
    args = cli.build_parser().parse_args(
        ["opencode", "autosync", "--build", "--quiet"]
    )
    assert args.opencode_command == "autosync"
    assert args.build is True
    assert args.quiet is True


def test_parser_opencode_install():
    args = cli.build_parser().parse_args(
        ["opencode", "install", "--dry-run", "--force", "--no-build"]
    )
    assert args.opencode_command == "install"
    assert args.dry_run is True
    assert args.force is True
    assert args.no_build is True


def test_maybe_export_skips_when_disabled(monkeypatch):
    runner = MagicMock()
    monkeypatch.setattr(export, "run_export", runner)

    cli._maybe_export_opencode(_args(opencode_export=False))

    runner.assert_not_called()


def test_maybe_export_skips_when_adapter_excludes_opencode(monkeypatch):
    runner = MagicMock()
    monkeypatch.setattr(export, "run_export", runner)

    cli._maybe_export_opencode(_args(adapter=["claude_code"]))

    runner.assert_not_called()


def test_maybe_export_skips_missing_db(monkeypatch, tmp_path):
    runner = MagicMock()
    monkeypatch.setattr(export, "run_export", runner)

    cli._maybe_export_opencode(
        _args(opencode_db=tmp_path / "missing.db")
    )

    runner.assert_not_called()


def test_maybe_export_runs_for_opencode_adapter(monkeypatch, tmp_path):
    db = tmp_path / "opencode.db"
    db.write_bytes(b"db")
    runner = MagicMock(
        return_value={"exported": 2, "unchanged": 1, "errors": 0}
    )
    monkeypatch.setattr(export, "run_export", runner)
    monkeypatch.setattr(cli, "_load_run_config", lambda: {})

    cli._maybe_export_opencode(
        _args(
            adapter=["opencode"],
            opencode_db=db,
            since="2026-01-01",
            project="demo",
            dry_run=True,
        )
    )

    runner.assert_called_once()
    kwargs = runner.call_args.kwargs
    assert kwargs["project"] == "demo"
    assert kwargs["dry_run"] is True
    assert kwargs["since"] == 1767225600000
