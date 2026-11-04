"""Concurrent sync/synthesize runs must not corrupt the state files.

The Claude Code Stop-hook runs ``llmwiki sync`` after every reply, in
every open session, so several converters can start at once. Before
this guard they raced on ``.llmwiki-state.json``: a reader that caught a
half-written file got ``{}`` and re-processed the whole corpus. The
synth state has the same shape and a re-run there costs LLM calls.
"""

from __future__ import annotations

import json
from pathlib import Path

from llmwiki import convert
from llmwiki.lock import FileLock, atomic_write_text, lock_path_for
from llmwiki.synth import pipeline


def test_lock_path_sits_next_to_state_file(tmp_path: Path) -> None:
    state = tmp_path / ".llmwiki-state.json"
    assert lock_path_for(state) == tmp_path / ".llmwiki-state.json.lock"


def test_atomic_write_replaces_content_and_leaves_no_temp(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    target.write_text("old", encoding="utf-8")

    atomic_write_text(target, '{"a": 1}')

    assert target.read_text(encoding="utf-8") == '{"a": 1}'
    assert sorted(p.name for p in tmp_path.iterdir()) == ["state.json"]


def test_convert_save_state_is_atomic(tmp_path: Path) -> None:
    target = tmp_path / ".llmwiki-state.json"
    convert.save_state(target, {"k": 1.0})

    assert json.loads(target.read_text(encoding="utf-8")) == {"k": 1.0}
    assert sorted(p.name for p in tmp_path.iterdir()) == [".llmwiki-state.json"]


def test_convert_all_skips_when_another_sync_holds_the_lock(
    tmp_path: Path, capsys
) -> None:
    state = tmp_path / ".llmwiki-state.json"
    with FileLock(lock_path_for(state)):
        rc = convert.convert_all(
            out_dir=tmp_path / "raw", state_file=state, lock_timeout=0
        )

    assert rc == 0
    assert not state.exists()
    assert "another sync is running" in capsys.readouterr().err


def test_convert_all_holds_the_lock_while_converting(
    tmp_path: Path, monkeypatch
) -> None:
    state = tmp_path / ".llmwiki-state.json"
    seen: list[bool] = []

    def fake_convert(**kwargs) -> int:
        probe = FileLock(lock_path_for(state), timeout=0)
        acquired = probe.acquire()
        if acquired:
            probe.release()
        seen.append(acquired)
        return 0

    monkeypatch.setattr(convert, "_convert_all_unlocked", fake_convert)

    assert convert.convert_all(state_file=state) == 0
    assert seen == [False]
    # Released afterwards.
    with FileLock(lock_path_for(state), timeout=0):
        pass


def test_synthesize_skips_when_another_run_holds_the_lock(
    tmp_path: Path, capsys
) -> None:
    state = tmp_path / ".llmwiki-synth-state.json"
    with FileLock(lock_path_for(state)):
        summary = pipeline.synthesize_new_sessions(
            raw_dir=tmp_path / "raw",
            wiki_sources_dir=tmp_path / "wiki" / "sources",
            state_file=state,
            lock_timeout=0,
        )

    assert summary["locked"] is True
    assert summary["synthesized"] == 0
    assert summary["errors"] == []
    assert not state.exists()
    assert "another synthesize is running" in capsys.readouterr().err


def test_synth_save_state_is_atomic(tmp_path: Path) -> None:
    target = tmp_path / ".llmwiki-synth-state.json"
    pipeline._save_state({"p.md": 1.0}, target)

    assert json.loads(target.read_text(encoding="utf-8")) == {"p.md": 1.0}
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        ".llmwiki-synth-state.json"
    ]
