"""Tests for low-signal session coalescing (#noop-clusters)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from llmwiki.synth.pipeline import (
    _cluster_filename,
    _coalesce_low_signal_sessions,
    _render_cluster_page,
    low_signal_reason,
    synthesize_new_sessions,
)

NO_RESPONSE_BODY = """### Turn 1 — User

Run the next mission step.

### Turn 1 — Assistant
"""

LOCK_SKIP_BODY = """### Turn 1 — User

Run the next mission step.

### Turn 1 — Assistant

RUN.lock существует и его возраст 30 минут — завершаюсь сразу, без действий.
"""

WORK_BODY = """### Turn 1 — User

Run the next mission step.

### Turn 1 — Assistant

Коммит сделан, PROGRESS.md обновлён.
"""


def _meta(slug: str, date: str, *, tools: int, output: int = 0) -> dict:
    return {
        "slug": slug,
        "date": date,
        "project": "proj",
        "tool_calls": str(tools),
        "token_totals": json.dumps({"output": output}),
    }


def _session(tmp_path: Path, meta: dict, body: str):
    path = tmp_path / "raw" / f"{meta['date']}-{meta['slug']}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return (path, meta, body)


def _individual_page(
    sources: Path, meta: dict, *, last_updated: str | None = None
) -> Path:
    page = sources / "proj" / f"{meta['date']}-{meta['slug']}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    updated = last_updated or datetime.now(timezone.utc).date().isoformat()
    page.write_text(
        f"---\ntitle: old\nlast_updated: {updated}\n---\nold body\n",
        encoding="utf-8",
    )
    return page


def test_low_signal_reason_classifies_three_shapes():
    assert low_signal_reason(_meta("a", "2026-01-01", tools=0), NO_RESPONSE_BODY) == "no-response"
    assert low_signal_reason(_meta("b", "2026-01-02", tools=1, output=400), LOCK_SKIP_BODY) == "lock-skip"
    assert low_signal_reason(_meta("c", "2026-01-03", tools=9, output=900), WORK_BODY) is None


def test_lock_skip_patterns_are_configurable():
    body = LOCK_SKIP_BODY.replace(
        "RUN.lock существует и его возраст 30 минут — завершаюсь сразу, без действий.",
        "another run holds the lock; exiting without changes.",
    )

    assert (
        low_signal_reason(
            _meta("a", "2026-01-01", tools=1),
            body,
            lock_skip_patterns=["another run holds the lock"],
        )
        == "lock-skip"
    )
    assert (
        low_signal_reason(
            _meta("b", "2026-01-02", tools=1),
            body,
            lock_skip_patterns=["RUN.lock существует"],
        )
        is None
    )


def test_empty_lock_skip_patterns_disable_that_rule():
    assert (
        low_signal_reason(
            _meta("a", "2026-01-01", tools=1),
            LOCK_SKIP_BODY,
            lock_skip_patterns=[],
        )
        is None
    )


def test_coalesce_replaces_members_with_one_cluster_page(tmp_path):
    sources = tmp_path / "wiki" / "sources"
    sessions = [
        _session(tmp_path, _meta("s1", "2026-01-01", tools=0), NO_RESPONSE_BODY),
        _session(tmp_path, _meta("s2", "2026-01-02", tools=0), NO_RESPONSE_BODY),
        _session(tmp_path, _meta("s3", "2026-01-03", tools=1, output=400), LOCK_SKIP_BODY),
        _session(tmp_path, _meta("s4", "2026-01-04", tools=8, output=800), WORK_BODY),
    ]
    individual = [_individual_page(sources, meta) for _, meta, _ in sessions]
    state: dict[str, float] = {}

    clusters, coalesced, affected = _coalesce_low_signal_sessions(
        sessions,
        sources_out=sources,
        state=state,
        raw_dir=tmp_path / "raw",
        min_group=3,
        window="all",
    )

    assert clusters == 1
    assert len(coalesced) == 3
    assert affected == {"proj"}
    cluster = sources / "proj" / "2026-noop-sessions.md"
    assert cluster.is_file()
    body = cluster.read_text(encoding="utf-8")
    assert "merged_sessions: 3" in body
    assert body.count("| `s") == 3
    assert "no-response" in body and "lock-skip" in body

    # Members are gone; the real work session keeps its page.
    assert not individual[0].exists()
    assert not individual[1].exists()
    assert not individual[2].exists()
    assert individual[3].exists()
    assert len(state) == 3

    manifest = json.loads((sources / "proj" / "_clusters.json").read_text(encoding="utf-8"))
    assert sorted(manifest["2026-noop-sessions.md"]["sessions"]) == ["s1", "s2", "s3"]


def test_group_below_minimum_is_left_alone(tmp_path):
    sources = tmp_path / "wiki" / "sources"
    sessions = [
        _session(tmp_path, _meta("s1", "2026-01-01", tools=0), NO_RESPONSE_BODY),
        _session(tmp_path, _meta("s2", "2026-01-02", tools=0), NO_RESPONSE_BODY),
    ]
    pages = [_individual_page(sources, meta) for _, meta, _ in sessions]
    state: dict[str, float] = {}

    clusters, coalesced, affected = _coalesce_low_signal_sessions(
        sessions,
        sources_out=sources,
        state=state,
        raw_dir=tmp_path / "raw",
        min_group=3,
        window="all",
    )

    assert clusters == 0
    assert coalesced == set()
    assert affected == set()
    assert all(page.exists() for page in pages)
    assert state == {}


def test_render_cluster_page_has_table_and_connections():
    rows = [{
        "date": "2026-01-01",
        "slug": "s1",
        "reason": "no-response",
        "prompt": "abcd1234",
        "tools": 0,
        "output": 0,
        "body": NO_RESPONSE_BODY,
        "meta": _meta("s1", "2026-01-01", tools=0),
        "project": "proj",
    }]
    page = _render_cluster_page("proj", rows, page_name="2026-noop-sessions.md")
    assert 'title: "proj — no-op sessions (2026-01-01..2026-01-01)"' in page
    assert "| 2026-01-01 | `s1` | no-response |" in page
    assert "[[proj]]" in page


def test_cluster_filename_handles_sessions_without_dates():
    rows = [{"date": "", "slug": "s1"}]
    assert _cluster_filename(rows, "all") == "unknown-noop-sessions.md"
    assert _cluster_filename(rows, "month") == "unknown-noop-sessions.md"


def test_edited_individual_page_is_preserved(tmp_path):
    sources = tmp_path / "wiki" / "sources"
    sessions = [
        _session(tmp_path, _meta("s1", "2026-01-01", tools=0), NO_RESPONSE_BODY),
        _session(tmp_path, _meta("s2", "2026-01-02", tools=0), NO_RESPONSE_BODY),
        _session(tmp_path, _meta("s3", "2026-01-03", tools=0), NO_RESPONSE_BODY),
    ]
    edited = _individual_page(sources, sessions[0][1], last_updated="2020-01-01")
    generated = [_individual_page(sources, meta) for _, meta, _ in sessions[1:]]
    state: dict[str, float] = {}

    clusters, _, _ = _coalesce_low_signal_sessions(
        sessions,
        sources_out=sources,
        state=state,
        raw_dir=tmp_path / "raw",
        min_group=3,
        window="all",
    )

    assert clusters == 1
    assert edited.exists()
    assert "old body" in edited.read_text(encoding="utf-8")
    assert all(not page.exists() for page in generated)


def test_stale_cluster_page_is_removed_and_members_requeued(tmp_path):
    sources = tmp_path / "wiki" / "sources"
    sessions = [
        _session(tmp_path, _meta("s1", "2026-01-01", tools=0), NO_RESPONSE_BODY),
        _session(tmp_path, _meta("s2", "2026-01-02", tools=0), NO_RESPONSE_BODY),
        _session(tmp_path, _meta("s3", "2026-01-03", tools=0), NO_RESPONSE_BODY),
    ]
    for _, meta, _ in sessions:
        _individual_page(sources, meta)
    state: dict[str, float] = {}

    _coalesce_low_signal_sessions(
        sessions,
        sources_out=sources,
        state=state,
        raw_dir=tmp_path / "raw",
        min_group=1,
        window="all",
    )
    cluster = sources / "proj" / "2026-noop-sessions.md"
    manifest = sources / "proj" / "_clusters.json"
    assert cluster.is_file()
    assert manifest.is_file()
    assert len(state) == 3

    clusters, coalesced, affected = _coalesce_low_signal_sessions(
        sessions,
        sources_out=sources,
        state=state,
        raw_dir=tmp_path / "raw",
        min_group=5,
        window="all",
    )

    assert clusters == 0
    assert coalesced == set()
    assert affected == {"proj"}
    assert not cluster.exists()
    assert not manifest.exists()
    assert state == {}

def test_synthesize_coalesce_only_needs_no_backend(tmp_path):
    raw = tmp_path / "raw"
    sources = tmp_path / "wiki" / "sources"

    for index, slug in enumerate(("s1", "s2", "s3"), 1):
        date = f"2026-01-0{index}"
        page = raw / f"{date}-{slug}.md"
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text(
            "---\n"
            f"slug: {slug}\n"
            f"date: {date}\n"
            "project: proj\n"
            "tool_calls: 0\n"
            'token_totals: \'{"output": 0}\'\n'
            "---\n"
            f"{NO_RESPONSE_BODY}",
            encoding="utf-8",
        )
        _individual_page(sources, _meta(slug, date, tools=0))

    summary = synthesize_new_sessions(
        backend=None,
        raw_dir=raw,
        wiki_sources_dir=sources,
        state_file=tmp_path / "state.json",
        coalesce_only=True,
        coalesce_low_signal=True,
        coalesce_min_group=3,
    )

    assert summary["synthesized"] == 0
    assert summary["coalesced"] == 1
    assert summary["backend"] == "none"
    assert (sources / "proj" / "2026-noop-sessions.md").is_file()


def test_synthesize_parser_accepts_coalesce_only():
    from llmwiki.cli import build_parser

    args = build_parser().parse_args(["synthesize", "--coalesce-only"])

    assert args.coalesce_only is True


def test_cmd_synthesize_coalesce_only_skips_backend_probe(monkeypatch):
    import argparse
    from unittest.mock import MagicMock

    from llmwiki import cli

    args = argparse.Namespace(
        coalesce_only=True,
        check=False,
        estimate=False,
        list_pending=False,
        complete=None,
        force=False,
        vault=None,
    )
    monkeypatch.setattr(cli, "_load_run_config", lambda: {"synthesis": {}})
    monkeypatch.setattr(
        "llmwiki.synth.pipeline.resolve_backend",
        MagicMock(side_effect=AssertionError("backend must not be resolved")),
    )
    synth = MagicMock(
        return_value={
            "total_scanned": 0,
            "new_files": 0,
            "synthesized": 0,
            "skipped": 0,
            "errors": [],
            "coalesced": 0,
            "backend": "none",
        }
    )
    monkeypatch.setattr("llmwiki.synth.pipeline.synthesize_new_sessions", synth)

    rc = cli.cmd_synthesize(args)

    assert rc == 0
    assert synth.call_args.kwargs["backend"] is None
    assert synth.call_args.kwargs["coalesce_only"] is True
