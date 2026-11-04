"""Portable OpenCode autosync: export, convert, optional build."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from llmwiki.lock import FileLock
from llmwiki.opencode import autosync


def _db(tmp_path: Path) -> Path:
    db = tmp_path / "opencode.db"
    db.write_bytes(b"not a real database")
    return db


def test_autosync_runs_export_convert_and_build(tmp_path, monkeypatch):
    exporter = MagicMock(return_value={"exported": 2, "unchanged": 0, "errors": 0})
    converter = MagicMock(return_value=0)
    builder = MagicMock(return_value=[])
    monkeypatch.setattr(autosync, "run_export", exporter)
    monkeypatch.setattr(autosync, "convert_all", converter)
    monkeypatch.setattr("llmwiki.build.build_site", builder)

    rc = autosync.run_autosync(
        db_path=_db(tmp_path),
        out_dir=tmp_path / "jsonl",
        log_path=tmp_path / "autosync.log",
        lock_path=tmp_path / "autosync.lock",
        build=True,
        repo_root=tmp_path,
    )

    assert rc == 0
    exporter.assert_called_once()
    converter.assert_called_once_with(
        adapters=["opencode"], project=None, include_current=True
    )
    builder.assert_called_once()
    assert (tmp_path / "autosync.log").read_text(encoding="utf-8")


def test_autosync_skips_when_lock_is_busy(tmp_path, monkeypatch):
    exporter = MagicMock()
    monkeypatch.setattr(autosync, "run_export", exporter)
    lock_path = tmp_path / "autosync.lock"

    with FileLock(lock_path, timeout=0):
        rc = autosync.run_autosync(
            db_path=_db(tmp_path),
            out_dir=tmp_path / "jsonl",
            log_path=tmp_path / "autosync.log",
            lock_path=lock_path,
            lock_timeout=0,
        )

    assert rc == 0
    exporter.assert_not_called()
    assert "skip" in (tmp_path / "autosync.log").read_text(encoding="utf-8").lower()


def test_autosync_quiet_writes_only_to_log(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        autosync,
        "run_export",
        MagicMock(return_value={"exported": 0, "unchanged": 0, "errors": 0}),
    )
    monkeypatch.setattr(autosync, "convert_all", MagicMock(return_value=0))

    rc = autosync.run_autosync(
        db_path=_db(tmp_path),
        out_dir=tmp_path / "jsonl",
        log_path=tmp_path / "autosync.log",
        lock_path=tmp_path / "autosync.lock",
        quiet=True,
        repo_root=tmp_path,
    )

    assert rc == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    assert "done" in (tmp_path / "autosync.log").read_text(encoding="utf-8").lower()


def test_autosync_returns_error_when_export_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(
        autosync,
        "run_export",
        MagicMock(side_effect=RuntimeError("bad db")),
    )
    converter = MagicMock()
    monkeypatch.setattr(autosync, "convert_all", converter)

    rc = autosync.run_autosync(
        db_path=_db(tmp_path),
        out_dir=tmp_path / "jsonl",
        log_path=tmp_path / "autosync.log",
        lock_path=tmp_path / "autosync.lock",
        repo_root=tmp_path,
    )

    assert rc == 1
    converter.assert_not_called()
    assert "export failed" in (tmp_path / "autosync.log").read_text(
        encoding="utf-8"
    ).lower()
