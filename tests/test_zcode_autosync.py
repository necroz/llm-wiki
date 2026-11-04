"""Portable autosync: cross-platform lock and command wiring."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from llmwiki import cli
from llmwiki.lock import FileLock
from llmwiki.zcode import autosync


def _db(tmp_path: Path) -> Path:
    db = tmp_path / "db.sqlite"
    db.write_bytes(b"not a real database")
    return db


def test_file_lock_excludes_second_instance(tmp_path):
    path = tmp_path / "autosync.lock"

    first = FileLock(path, timeout=0)
    assert first.acquire() is True
    try:
        assert FileLock(path, timeout=0).acquire() is False
    finally:
        first.release()

    second = FileLock(path, timeout=0)
    assert second.acquire() is True
    second.release()


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
        adapters=["zcode"], project=None, include_current=True
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


def test_autosync_coalesce_calls_deterministic_pipeline(tmp_path, monkeypatch):
    monkeypatch.setattr(
        autosync,
        "run_export",
        MagicMock(return_value={"exported": 0, "unchanged": 0, "errors": 0}),
    )
    monkeypatch.setattr(autosync, "convert_all", MagicMock(return_value=0))
    synth = MagicMock(return_value={"coalesced": 1})
    monkeypatch.setattr("llmwiki.synth.pipeline.synthesize_new_sessions", synth)

    rc = autosync.run_autosync(
        db_path=_db(tmp_path),
        out_dir=tmp_path / "jsonl",
        log_path=tmp_path / "autosync.log",
        lock_path=tmp_path / "autosync.lock",
        coalesce=True,
        lock_timeout=0,
    )

    assert rc == 0
    assert synth.call_args.kwargs["coalesce_only"] is True
    assert synth.call_args.kwargs["coalesce_low_signal"] is True


def test_autosync_returns_one_on_export_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(
        autosync, "run_export", MagicMock(side_effect=OSError("db is locked"))
    )

    rc = autosync.run_autosync(
        db_path=_db(tmp_path),
        out_dir=tmp_path / "jsonl",
        log_path=tmp_path / "autosync.log",
        lock_path=tmp_path / "autosync.lock",
    )

    assert rc == 1
    assert "db is locked" in (tmp_path / "autosync.log").read_text(encoding="utf-8")


def test_zcode_autosync_parser_accepts_flags():
    args = cli.build_parser().parse_args(
        ["zcode", "autosync", "--build", "--project", "demo"]
    )

    assert args.zcode_command == "autosync"
    assert args.build is True
    assert args.project == "demo"


def test_shell_hook_delegates_to_python_autosync():
    hook = Path(__file__).resolve().parents[1] / "scripts" / "zcode_sync_hook.sh"
    text = hook.read_text(encoding="utf-8")

    assert "zcode autosync" in text
    assert "flock" not in text