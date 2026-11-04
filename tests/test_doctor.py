"""Environment diagnostics for cross-machine installs."""

from __future__ import annotations

from llmwiki import doctor


def test_collect_report_reports_core_environment(tmp_path):
    report = doctor.collect_report(repo_root=tmp_path, home=tmp_path)

    assert report["python"]["ok"] is True
    assert report["markdown"]["ok"] is True
    assert "version" in report["python"]["detail"].lower() or report["python"]["detail"]


def test_collect_report_flags_missing_zcode_paths(tmp_path):
    report = doctor.collect_report(repo_root=tmp_path, home=tmp_path)

    assert report["zcode_db"]["ok"] is False
    assert report["zcode_export"]["ok"] is False
    assert "not found" in report["zcode_db"]["detail"].lower()


def test_collect_report_detects_zcode_paths(tmp_path):
    db = tmp_path / ".zcode" / "cli" / "db" / "db.sqlite"
    db.parent.mkdir(parents=True)
    db.write_bytes(b"")
    export_dir = tmp_path / ".llmwiki" / "zcode" / "project"
    export_dir.mkdir(parents=True)
    (export_dir / "session.jsonl").write_text("{}\n", encoding="utf-8")

    report = doctor.collect_report(repo_root=tmp_path, home=tmp_path)

    assert report["zcode_db"]["ok"] is True
    assert report["zcode_export"]["ok"] is True


def test_cmd_doctor_returns_zero_when_core_checks_pass(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(doctor, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(doctor.Path, "home", classmethod(lambda cls: tmp_path))

    rc = doctor.cmd_doctor()

    out = capsys.readouterr().out
    assert rc == 0
    assert "python" in out.lower()
    assert "zcode" in out.lower()
