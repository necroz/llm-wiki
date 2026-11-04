"""Install scripts must create and prefer the checkout's virtualenv."""

from __future__ import annotations

import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _fake_repo(tmp_path: Path, script_name: str) -> Path:
    shutil.copy(REPO_ROOT / script_name, tmp_path / script_name)
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    fake_python = venv_bin / "python"
    fake_python.write_text(
        "#!/bin/sh\nprintf 'VENV_PYTHON %s\\n' \"$*\"\n",
        encoding="utf-8",
    )
    fake_python.chmod(fake_python.stat().st_mode | stat.S_IXUSR)
    return tmp_path


@pytest.mark.parametrize("script_name", ["build.sh", "sync.sh", "serve.sh"])
def test_wrapper_scripts_prefer_venv_python(tmp_path: Path, script_name: str):
    root = _fake_repo(tmp_path, script_name)

    result = subprocess.run(
        ["bash", str(root / script_name), "--help"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "VENV_PYTHON" in result.stdout
    assert "-m llmwiki" in result.stdout


def test_setup_sh_creates_and_installs_into_venv():
    text = (REPO_ROOT / "setup.sh").read_text(encoding="utf-8")

    assert "python3 -m venv" in text
    assert "pip install -e" in text
    assert "pip install --user" not in text


def test_setup_bat_creates_and_installs_into_venv():
    text = (REPO_ROOT / "setup.bat").read_text(encoding="utf-8")

    assert "venv" in text.lower()
    assert "pip install -e" in text
    assert "--user" not in text
