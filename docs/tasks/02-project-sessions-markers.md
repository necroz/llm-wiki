# 02 — Preserve manual text in project Sessions sections

- Priority: P0
- Depends on: none
- Size: M (1–2 hours)
- Files: `llmwiki/synth/pipeline.py`, `wiki/projects/*.md`,
  `tests/test_synth_pipeline.py` or a new `tests/test_project_sessions.py`
- PR title: `fix: preserve manual project session notes`

## Read first

- `llmwiki/synth/pipeline.py`, function `_sync_project_sessions`
  (around line 627)
- `wiki/projects/example-app.md`, section `## Sessions`
- `wiki/projects/other-app.md`, section `## Sessions`
- `AGENTS.md`, the "Entity / Concept page" format

## Problem

`_sync_project_sessions()` replaces the whole `## Sessions` section on
every synthesis run:

```python
text = re.sub(r"^## Sessions\s*\n.*?(?=^## |\Z)", "", text, flags=re.M | re.S)
```

Project pages are documented as hand-editable. A manual note inside
`## Sessions` is lost on the next `llmwiki synthesize`. This already
happened: the curated line for the `example-app` stop-button bug
was replaced by a truncated auto summary.

## Required change

Wrap the generated block in HTML comment markers:

```python
_SESSIONS_START = "<!-- sessions:auto:start -->"
_SESSIONS_END = "<!-- sessions:auto:end -->"
```

Build the auto block from the generated rows only:

```python
auto_block = (
    f"{_SESSIONS_START}\n"
    + "\n".join(bullet_lines)
    + f"\n{_SESSIONS_END}"
)
```

Update `_sync_project_sessions()` to follow this order:

1. Build the bullet lines and `auto_block` as today.
2. If both markers are present, replace only the text between them:

   ```python
   pattern = re.compile(
       re.escape(_SESSIONS_START) + r".*?" + re.escape(_SESSIONS_END),
       re.S,
   )
   text = pattern.sub(auto_block, text, count=1)
   ```

3. If the markers are absent but `## Sessions` exists, insert the auto
   block immediately after the heading and leave the existing section
   body below it. Add one blank line between the end marker and the old
   body. This preserves manual text that existed before the migration.
4. If there is no `## Sessions` heading, insert
   `## Sessions\n\n{auto_block}` before `## Connections`, or append it
   at the end when `## Connections` is absent.

Do not delete any line outside the marker pair.

### One-off migration

Current `wiki/projects/*.md` sections contain only generated links.
After the code change, run `_sync_project_sessions` for every project
once. It will insert the markers and keep the old list below them,
which duplicates links.

Clean the duplicates with a one-off script (do not commit the script):

```python
from pathlib import Path
import re

start = "<!-- sessions:auto:start -->"
end = "<!-- sessions:auto:end -->"
for page in Path("wiki/projects").glob("*.md"):
    text = page.read_text(encoding="utf-8")
    m = re.search(re.escape(start) + r"(.*?)" + re.escape(end), text, re.S)
    if not m:
        continue
    auto_targets = set(re.findall(r"\[\[([^\]|]+)", m.group(1)))
    head, tail = text[: m.end()], text[m.end() :]
    kept = []
    for line in tail.splitlines():
        link = re.match(r"^- \[\[([^\]|]+)", line)
        if link and link.group(1) in auto_targets:
            continue
        kept.append(line)
    page.write_text(head + "\n".join(kept).rstrip() + "\n", encoding="utf-8")
```

Inspect `wiki/projects/example-app.md` before and after. Manual
notes that are not session bullets must remain.

## Tests

Add to `tests/test_synth_pipeline.py` (or a new
`tests/test_project_sessions.py`):

```python
def test_sync_project_sessions_preserves_manual_notes(tmp_path):
    from llmwiki.synth.pipeline import _sync_project_sessions

    wiki = tmp_path / "wiki"
    sources = wiki / "sources" / "proj"
    projects = wiki / "projects"
    sources.mkdir(parents=True)
    projects.mkdir(parents=True)
    (sources / "2026-01-01-s1.md").write_text(
        '---\ntitle: "s1"\ntype: source\ndate: 2026-01-01\n'
        'project: proj\n---\n\n## Summary\n\nOne session.\n',
        encoding="utf-8",
    )
    page = projects / "proj.md"
    page.write_text(
        '---\ntitle: "proj"\n---\n\n# proj\n\n## Sessions\n\n'
        "- manual note: do not lose this\n\n"
        "## Connections\n\n- [[llm-wiki]]\n",
        encoding="utf-8",
    )

    _sync_project_sessions(wiki, "proj")
    first = page.read_text(encoding="utf-8")
    assert "manual note: do not lose this" in first
    assert "sessions:auto:start" in first
    assert "sessions:auto:end" in first

    _sync_project_sessions(wiki, "proj")
    assert page.read_text(encoding="utf-8") == first
```

## Verification

```bash
.venv/bin/python -m pytest tests/test_synth_pipeline.py -q
.venv/bin/python -m pytest tests/ -q
.venv/bin/python -m llmwiki lint
grep -c "sessions:auto:start" wiki/projects/*.md
```

Expected:

- Tests pass.
- Lint reports `0 errors`.
- Every project page has exactly one start marker.
- `grep "manual note" wiki/projects/example-app.md` finds nothing
  only if no manual note was present. Do not add one just to make the
  check pass.

## Acceptance criteria

- [ ] `_sync_project_sessions` replaces only the marker block.
- [ ] Existing manual text outside the markers is preserved.
- [ ] Running it twice produces byte-identical output.
- [ ] Every `wiki/projects/*.md` page has one marker pair.
- [ ] The new test passes.
- [ ] `pytest tests/ -q` and `llmwiki lint` pass.
- [ ] `CHANGELOG.md` updated.

## Out of scope

- Rewriting project descriptions or topics.
- Changing the source-page format.
- Touching `raw/`.

## Rollback

Revert the function and remove the markers from project pages. The
generated session links can be regenerated by `llmwiki synthesize`.
