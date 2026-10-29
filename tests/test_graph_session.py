"""Tests for the session/project graph and the project index."""

from __future__ import annotations

from pathlib import Path

import llmwiki.graph as graph_mod
from llmwiki.build import ensure_projects_index
from llmwiki.graph import HTML_TEMPLATE, build_session_graph, scan_pages, write_html


def _write_raw(
    path: Path,
    project: str,
    slug: str,
    description: str,
    *,
    source_file: str | None = None,
    type_: str = "source",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    source_file = source_file or f"raw/sessions/{path.name}"
    path.write_text(
        "---\n"
        f'title: "Session: {slug} — 2026-08-01"\n'
        f"type: {type_}\n"
        f'description: "{description}"\n'
        f"source_file: {source_file}\n"
        f"sessionId: {slug}\n"
        f"slug: {slug}\n"
        f"project: {project}\n"
        "---\n\n"
        f"# {slug}\n",
        encoding="utf-8",
    )


def _minimal_graph(title: str) -> dict:
    return {
        "title": title,
        "nodes": [
            {
                "id": "project:alpha",
                "label": "alpha",
                "type": "projects",
                "path": "wiki/projects/alpha.md",
                "site_url": None,
                "in_degree": 0,
                "out_degree": 1,
                "value": 1,
                "cluster": "alpha",
            },
            {
                "id": "session:s1",
                "label": "first prompt",
                "type": "sources",
                "path": "raw/sessions/s1.md",
                "site_url": None,
                "in_degree": 1,
                "out_degree": 0,
                "value": 1,
                "cluster": "alpha",
            },
        ],
        "edges": [{"source": "project:alpha", "target": "session:s1"}],
        "broken_edges": [],
        "stats": {
            "total_pages": 2,
            "total_edges": 1,
            "broken_edges": 0,
            "orphans": [],
            "top_linked": [],
            "top_linking": [],
        },
    }


def test_build_session_graph_creates_project_and_session_nodes(tmp_path: Path) -> None:
    raw = tmp_path / "raw" / "sessions"
    _write_raw(raw / "2026-08-01-alpha-s1.md", "alpha", "s1", "first auth work")
    _write_raw(raw / "2026-08-02-alpha-s2.md", "alpha", "s2", "second auth work")
    _write_raw(raw / "2026-08-03-beta-s3.md", "beta", "s3", "beta work")

    graph = build_session_graph(raw_dir=raw, verify_site_dir=None)
    nodes = {n["id"]: n for n in graph["nodes"]}

    assert set(nodes) == {
        "project:alpha",
        "project:beta",
        "session:s1",
        "session:s2",
        "session:s3",
    }
    assert nodes["project:alpha"]["out_degree"] == 2
    assert nodes["project:alpha"]["value"] == 2
    assert nodes["session:s1"]["in_degree"] == 1
    assert nodes["session:s1"]["label"] == "first auth work"
    assert nodes["session:s1"]["cluster"] == "alpha"
    assert graph["cluster_by"] == "project"
    assert graph["stats"]["total_pages"] == 5
    assert graph["stats"]["total_edges"] == 3
    assert graph["stats"]["orphans"] == []
    assert {(e["source"], e["target"]) for e in graph["edges"]} == {
        ("project:alpha", "session:s1"),
        ("project:alpha", "session:s2"),
        ("project:beta", "session:s3"),
    }


def test_build_session_graph_skips_non_session_pages(tmp_path: Path) -> None:
    raw = tmp_path / "raw" / "sessions"
    _write_raw(raw / "2026-08-01-alpha-s1.md", "alpha", "s1", "keep me")
    _write_raw(raw / "2026-08-02-alpha-index.md", "alpha", "index", "not a session", type_="index")
    (raw / "README.md").write_text("# raw sessions\n", encoding="utf-8")

    graph = build_session_graph(raw_dir=raw, verify_site_dir=None)
    ids = {n["id"] for n in graph["nodes"]}
    assert "session:s1" in ids
    assert "session:index" not in ids


def test_build_session_graph_verifies_site_urls(tmp_path: Path) -> None:
    raw = tmp_path / "raw" / "sessions"
    _write_raw(raw / "2026-08-01-alpha-s1.md", "alpha", "s1", "first auth work")
    site = tmp_path / "site"
    (site / "sessions" / "alpha").mkdir(parents=True)
    (site / "sessions" / "alpha" / "2026-08-01-alpha-s1.html").write_text("<html></html>", encoding="utf-8")
    (site / "projects").mkdir(parents=True)
    (site / "projects" / "alpha.html").write_text("<html></html>", encoding="utf-8")

    graph = build_session_graph(raw_dir=raw, verify_site_dir=site)
    nodes = {n["id"]: n for n in graph["nodes"]}
    assert nodes["project:alpha"]["site_url"] == "projects/alpha.html"
    assert nodes["session:s1"]["site_url"] == "sessions/alpha/2026-08-01-alpha-s1.html"


def test_scan_pages_keeps_nested_index(tmp_path: Path, monkeypatch) -> None:
    wiki = tmp_path / "wiki"
    (wiki / "projects").mkdir(parents=True)
    (wiki / "index.md").write_text(
        '---\ntitle: "Wiki Index"\n---\n\n- [[projects/index]]\n',
        encoding="utf-8",
    )
    (wiki / "projects" / "index.md").write_text(
        '---\ntitle: "Projects"\n---\n\n- [[Foo]]\n',
        encoding="utf-8",
    )
    (wiki / "projects" / "Foo.md").write_text(
        '---\ntitle: "Foo"\n---\n\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(graph_mod, "WIKI_DIR", wiki)
    monkeypatch.setattr(graph_mod, "REPO_ROOT", tmp_path)

    pages = scan_pages()
    assert set(pages) == {"index", "projects/index", "Foo"}
    assert pages["index"]["out_links"] == {"projects/index"}
    assert pages["projects/index"]["out_links"] == {"Foo"}
    assert pages["projects/index"]["site_url"] == "projects/index.html"


def test_write_html_uses_custom_title(tmp_path: Path) -> None:
    out = tmp_path / "graph.html"
    write_html(_minimal_graph("llmwiki — Sessions & Projects"), out)
    text = out.read_text(encoding="utf-8")
    assert "<title>llmwiki — Sessions &amp; Projects</title>" in text
    assert "<h1>llmwiki — Sessions &amp; Projects</h1>" in text
    assert "llmwiki — Knowledge Graph" not in text


def test_project_graph_template_starts_clustered() -> None:
    assert "GRAPH.cluster_by === 'project' ? 'project' : 'off'" in HTML_TEMPLATE
    assert "applyClusterMode(clusterMode);" in HTML_TEMPLATE


def test_project_graph_template_passes_site_url_and_handles_clusters() -> None:
    assert "site_url: n.site_url" in HTML_TEMPLATE
    assert "n.id === 'project:' + project" in HTML_TEMPLATE


def test_ensure_projects_index_links_stubs(tmp_path: Path) -> None:
    projects = tmp_path / "wiki" / "projects"
    projects.mkdir(parents=True)
    (projects / "alpha.md").write_text(
        '---\ntitle: "alpha"\ndescription: "Alpha project"\n---\n\n# alpha\n',
        encoding="utf-8",
    )
    (projects / "beta.md").write_text(
        '---\ntitle: "beta"\ndescription: "Beta project"\n---\n\n# beta\n',
        encoding="utf-8",
    )

    index = ensure_projects_index(projects)
    assert index is not None
    text = index.read_text(encoding="utf-8")
    assert 'title: "Projects"' in text
    assert "type: index" in text
    assert "[[alpha]]" in text
    assert "[[beta]]" in text
    assert "Alpha project" in text
    assert "Beta project" in text
    assert "[[index]]" not in text