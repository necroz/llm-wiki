"""Tests for the ZCode session-store adapter."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from llmwiki.adapters import REGISTRY, discover_adapters
from llmwiki.adapters.status import adapter_status
from llmwiki.adapters.zcode import ZcodeAdapter
from llmwiki.convert import Redactor, convert_all, load_config

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_CONFIG = REPO_ROOT / "examples" / "sessions_config.json"


def _write_session(store: Path, project: str = "neuromerch_project", session: str = "sess-1") -> Path:
    path = store / project / f"{session}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    records = [
        {
            "type": "init",
            "sessionId": session,
            "cwd": f"/tmp/{project}",
            "timestamp": "2026-08-12T11:05:18.098Z",
            "slug": session,
            "title": "ZCode test session",
        },
        {
            "type": "user",
            "timestamp": "2026-08-12T11:05:19.000Z",
            "sessionId": session,
            "cwd": f"/tmp/{project}",
            "message": {"role": "user", "content": "hello from zcode"},
        },
        {
            "type": "assistant",
            "timestamp": "2026-08-12T11:05:20.000Z",
            "sessionId": session,
            "cwd": f"/tmp/{project}",
            "message": {
                "role": "assistant",
                "model": "model-x",
                "content": [{"type": "text", "text": "answer from zcode"}],
                "usage": {"input_tokens": 1, "output_tokens": 2},
            },
        },
    ]
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n", encoding="utf-8")
    return path


def test_zcode_adapter_is_registered_by_discover_adapters() -> None:
    code = (
        "from llmwiki.adapters import REGISTRY, discover_adapters; "
        "discover_adapters(); "
        "print(REGISTRY['zcode'].name)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "zcode"


def test_zcode_adapter_metadata() -> None:
    discover_adapters()
    assert REGISTRY.get("zcode") is ZcodeAdapter
    assert ZcodeAdapter.is_ai_session is True
    assert ZcodeAdapter.SUPPORTED_SCHEMA_VERSIONS == ["v1"]
    assert ZcodeAdapter.description()


def test_zcode_is_available_requires_a_jsonl(tmp_path: Path, monkeypatch) -> None:
    store = tmp_path / "store"
    store.mkdir()
    monkeypatch.setattr(ZcodeAdapter, "session_store_path", store)
    assert ZcodeAdapter.is_available() is False

    _write_session(store)
    assert ZcodeAdapter.is_available() is True


def test_zcode_is_unavailable_when_store_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(ZcodeAdapter, "session_store_path", tmp_path / "missing")
    assert ZcodeAdapter.is_available() is False


def test_zcode_is_unavailable_when_store_is_a_file(tmp_path: Path, monkeypatch) -> None:
    store = tmp_path / "store"
    store.write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr(ZcodeAdapter, "session_store_path", store)
    assert ZcodeAdapter.is_available() is False


def test_zcode_discovers_sessions_and_derives_project_slug(tmp_path: Path, monkeypatch) -> None:
    store = tmp_path / "store"
    first = _write_session(store, "neuromerch_project", "sess-1")
    second = _write_session(store, "neuromerch_project", "sess-2")
    (store / "notes.txt").write_text("not a session", encoding="utf-8")
    monkeypatch.setattr(ZcodeAdapter, "session_store_path", store)

    adapter = ZcodeAdapter()
    paths = adapter.discover_sessions()
    assert [p.name for p in paths] == ["sess-1.jsonl", "sess-2.jsonl"]
    assert adapter.derive_project_slug(first) == "neuromerch_project"
    assert adapter.derive_project_slug(second) == "neuromerch_project"


def test_zcode_sync_converts_exported_jsonl(tmp_path: Path, monkeypatch) -> None:
    store = tmp_path / "store"
    _write_session(store)
    monkeypatch.setattr(ZcodeAdapter, "session_store_path", store)

    out_dir = tmp_path / "raw"
    state_file = tmp_path / "state.json"
    ignore_file = tmp_path / "ignore"
    ignore_file.write_text("", encoding="utf-8")

    rc = convert_all(
        adapters=["zcode"],
        out_dir=out_dir,
        state_file=state_file,
        config_file=EXAMPLE_CONFIG,
        ignore_file=ignore_file,
    )
    assert rc == 0

    files = list(out_dir.glob("*.md"))
    assert len(files) == 1, files
    text = files[0].read_text(encoding="utf-8")
    assert "hello from zcode" in text
    assert "answer from zcode" in text
    assert "project: neuromerch_project" in text
    assert "tags: [zcode, session-transcript]" in text


def test_zcode_example_config_enables_adapter(tmp_path: Path, monkeypatch) -> None:
    store = tmp_path / "store"
    _write_session(store)
    monkeypatch.setattr(ZcodeAdapter, "session_store_path", store)

    config = json.loads(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    assert adapter_status("zcode", ZcodeAdapter, config) == ("explicit", "yes")


def test_example_config_redacts_url_credentials() -> None:
    config = load_config(EXAMPLE_CONFIG)
    redacted = Redactor(config)("postgresql://app:s3cr3tP@db-host/internal")
    assert "s3cr3tP" not in redacted
    assert "<REDACTED>" in redacted
