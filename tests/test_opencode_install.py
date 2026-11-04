"""Install the OpenCode autosync plugin."""

from __future__ import annotations

from llmwiki.opencode import install


def test_install_plugin_writes_index_ts(tmp_path):
    messages = install.install_plugin(
        home=tmp_path,
        python_exe="/venv/bin/python",
        repo_root=tmp_path / "repo",
    )

    path = install.plugin_path(home=tmp_path)
    assert path == (
        tmp_path
        / ".config"
        / "opencode"
        / "plugins"
        / "llmwiki-autosync"
        / "index.ts"
    )
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "session.idle" in text
    assert "llmwiki" in text
    assert "opencode" in text
    assert "autosync" in text
    assert "/venv/bin/python" in text
    assert str(tmp_path / "repo") in text
    assert any("installed" in message.lower() for message in messages)


def test_install_plugin_is_idempotent(tmp_path):
    first = install.install_plugin(
        home=tmp_path,
        python_exe="/venv/bin/python",
        repo_root=tmp_path,
    )
    path = install.plugin_path(home=tmp_path)
    before = path.read_text(encoding="utf-8")

    second = install.install_plugin(
        home=tmp_path,
        python_exe="/venv/bin/python",
        repo_root=tmp_path,
    )

    assert path.read_text(encoding="utf-8") == before
    assert any("already installed" in message.lower() for message in second)
    assert any("installed" in message.lower() for message in first)


def test_install_plugin_force_overwrites(tmp_path):
    install.install_plugin(
        home=tmp_path,
        python_exe="/venv/bin/python",
        repo_root=tmp_path,
    )
    path = install.plugin_path(home=tmp_path)
    path.write_text("// sentinel\n", encoding="utf-8")

    install.install_plugin(
        home=tmp_path,
        python_exe="/venv/bin/python",
        repo_root=tmp_path,
        force=True,
    )

    assert "sentinel" not in path.read_text(encoding="utf-8")


def test_install_plugin_dry_run_writes_nothing(tmp_path):
    messages = install.install_plugin(
        home=tmp_path,
        python_exe="/venv/bin/python",
        repo_root=tmp_path,
        dry_run=True,
    )

    assert not install.plugin_path(home=tmp_path).exists()
    assert any("would" in message.lower() for message in messages)


def test_install_plugin_escapes_paths_with_spaces(tmp_path):
    python_exe = "/tmp/venv with space/bin/python"
    repo_root = tmp_path / "repo with space"

    install.install_plugin(
        home=tmp_path,
        python_exe=python_exe,
        repo_root=repo_root,
    )

    text = install.plugin_path(home=tmp_path).read_text(encoding="utf-8")
    assert f'"{python_exe}"' in text
    assert f'"{repo_root}"' in text


def test_install_plugin_handles_async_spawn_errors(tmp_path):
    install.install_plugin(
        home=tmp_path,
        python_exe="/venv/bin/python",
        repo_root=tmp_path,
    )

    text = install.plugin_path(home=tmp_path).read_text(encoding="utf-8")
    assert 'child.once("error"' in text or 'child.on("error"' in text


def test_install_all_returns_plugin_messages(tmp_path):
    messages = install.install_all(
        home=tmp_path,
        python_exe="/venv/bin/python",
        repo_root=tmp_path,
    )

    assert install.plugin_path(home=tmp_path).is_file()
    assert messages
