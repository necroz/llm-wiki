"""Unified config layering across commands (setup portability)."""

from __future__ import annotations

import json

from llmwiki.config import resolve_config


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_resolve_config_merges_example_and_local_overrides(tmp_path):
    _write(
        tmp_path / "examples" / "sessions_config.json",
        {
            "synthesis": {"backend": "dummy", "timeout": 60},
            "zcode": {"enabled": True},
        },
    )
    _write(tmp_path / "config.json", {"synthesis": {"backend": "ollama"}})
    _write(
        tmp_path / "config.local.json",
        {"synthesis": {"backend": "openai", "base_url": "http://127.0.0.1:8001/v1"}},
    )

    config = resolve_config(tmp_path)

    assert config["synthesis"]["backend"] == "openai"
    assert config["synthesis"]["timeout"] == 60
    assert config["synthesis"]["base_url"] == "http://127.0.0.1:8001/v1"
    assert config["zcode"]["enabled"] is True


def test_resolve_config_ignores_invalid_local_file(tmp_path):
    _write(tmp_path / "examples" / "sessions_config.json", {"synthesis": {"backend": "dummy"}})
    (tmp_path / "config.local.json").write_text("{not json", encoding="utf-8")

    config = resolve_config(tmp_path)

    assert config["synthesis"]["backend"] == "dummy"


def test_convert_load_config_layered_applies_local_redaction(tmp_path):
    from llmwiki.convert import Redactor, load_config_layered

    _write(
        tmp_path / "examples" / "sessions_config.json",
        {"redaction": {"extra_patterns": []}},
    )
    _write(
        tmp_path / "config.local.json",
        {"redaction": {"extra_patterns": [r"LOCALSECRET\d+"]}},
    )

    config = load_config_layered(tmp_path)
    redacted = Redactor(config)("prefix LOCALSECRET123 suffix")

    assert "LOCALSECRET123" not in redacted


def test_schedule_backend_reads_layered_config(tmp_path, monkeypatch):
    from llmwiki import config_schedule

    _write(tmp_path / "examples" / "sessions_config.json", {"synthesis": {"backend": "dummy"}})
    _write(tmp_path / "config.local.json", {"synthesis": {"backend": "openai"}})
    monkeypatch.setattr(config_schedule, "resolve_config", lambda: resolve_config(tmp_path))

    assert config_schedule.load_synthesis_backend() == "openai"


def test_freshness_reads_layered_config(tmp_path):
    from llmwiki.freshness import load_freshness_config

    _write(tmp_path / "examples" / "sessions_config.json", {"freshness": {"green_days": 14}})
    _write(tmp_path / "config.json", {"freshness": {"green_days": 5}})
    _write(tmp_path / "config.local.json", {"freshness": {"green_days": 7, "yellow_days": 30}})

    assert load_freshness_config(tmp_path) == (7, 30)
