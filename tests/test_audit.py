"""Privacy audit for raw/wiki/site before publishing."""

from __future__ import annotations

from pathlib import Path

from llmwiki import audit


def test_audit_finds_username_and_secret(tmp_path):
    root = tmp_path / "raw"
    root.mkdir()
    (root / "session.md").write_text(
        "home is /home/alice/project\n"
        "token sk-abcdefghijklmnopqrstuvwx\n",
        encoding="utf-8",
    )

    report = audit.audit_paths([root], username="alice")

    kinds = {finding["kind"] for finding in report["findings"]}
    assert "username" in kinds
    assert "secret" in kinds
    assert report["files_scanned"] == 1


def test_audit_clean_tree_has_no_findings(tmp_path):
    root = tmp_path / "wiki"
    root.mkdir()
    (root / "page.md").write_text("A clean page with no secrets.\n", encoding="utf-8")

    report = audit.audit_paths([root], username="alice")

    assert report["findings"] == []
    assert report["files_scanned"] == 1


def test_cli_audit_json_and_fail_flag(tmp_path, capsys, monkeypatch):
    from llmwiki import cli

    root = tmp_path / "raw"
    root.mkdir()
    (root / "x.md").write_text("user alice\n", encoding="utf-8")
    monkeypatch.setattr(audit, "REPO_ROOT", tmp_path)

    rc = cli.main(
        ["audit", "--path", str(root), "--json", "--fail-on-findings", "--username", "alice"]
    )

    out = capsys.readouterr().out
    assert rc == 1
    assert '"findings"' in out
    assert "alice" in out


def test_audit_parser_accepts_flags():
    from llmwiki.cli import build_parser

    args = build_parser().parse_args(
        ["audit", "--path", "/tmp/raw", "--json", "--fail-on-findings"]
    )

    assert args.path == [Path("/tmp/raw")]
    assert args.json is True
    assert args.fail_on_findings is True