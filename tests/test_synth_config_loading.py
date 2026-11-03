"""Config layering for `llmwiki synthesize` (gitignored local overrides)."""

from __future__ import annotations

import json

from llmwiki import cli


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_load_run_config_merges_local_override(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "REPO_ROOT", tmp_path)
    _write(
        tmp_path / "examples" / "sessions_config.json",
        {"synthesis": {"backend": "dummy", "timeout": 60}, "zcode": {"enabled": True}},
    )
    _write(
        tmp_path / "config.local.json",
        {"synthesis": {"backend": "openai", "base_url": "http://127.0.0.1:8001/v1"}},
    )

    config = cli._load_run_config()

    assert config["synthesis"] == {
        "backend": "openai",
        "timeout": 60,
        "base_url": "http://127.0.0.1:8001/v1",
    }
    assert config["zcode"] == {"enabled": True}


def test_config_local_wins_over_config_json(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "REPO_ROOT", tmp_path)
    _write(tmp_path / "config.json", {"synthesis": {"backend": "ollama"}})
    _write(tmp_path / "config.local.json", {"synthesis": {"backend": "openai"}})

    assert cli._load_run_config()["synthesis"]["backend"] == "openai"


def test_invalid_override_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "REPO_ROOT", tmp_path)
    _write(tmp_path / "examples" / "sessions_config.json", {"synthesis": {"backend": "dummy"}})
    (tmp_path / "config.local.json").write_text("{not json", encoding="utf-8")

    assert cli._load_run_config()["synthesis"]["backend"] == "dummy"


def test_config_int_falls_back_on_garbage():
    assert cli._config_int("abc", 3) == 3
    assert cli._config_int(None, 4) == 4
    assert cli._config_int("0", 3) == 1


def test_config_bool_parses_strings():
    assert cli._config_bool("false", True) is False
    assert cli._config_bool("no", True) is False
    assert cli._config_bool("true", False) is True
    assert cli._config_bool(None, True) is True
    assert cli._config_bool(0, True) is False
