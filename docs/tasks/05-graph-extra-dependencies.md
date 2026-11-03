# 05 — Add graph runtime dependencies

- Priority: P1
- Depends on: none
- Size: S (15–30 min)
- Files: `pyproject.toml`, `tests/test_graphify_bridge.py`
- PR title: `fix: include graph runtime dependencies in extras`

## Read first

- `pyproject.toml`, sections `[project.optional-dependencies]`, keys
  `graph` (around line 60) and `all` (around line 80)
- `tests/test_graphify_bridge.py`, function
  `test_pyproject_has_graph_extra` (around line 92)

## Problem

The `graph` extra contains only:

```toml
graph = ["graphifyy>=0.4.20"]
```

But the graph pipeline needs two packages that `graphifyy` does not
declare:

- `matplotlib` — report and HTML rendering. Without it the graph
  crashes with `ImportError: matplotlib not installed`.
- `scipy` — community detection and analysis. Without it the graph
  crashes with `ModuleNotFoundError: No module named 'scipy'`.

Both were installed manually in this environment. A fresh
`pip install llmwiki[graph]` leaves `llmwiki graph` broken: it falls
back to the builtin engine with a warning.

## Required change

In `pyproject.toml` update both extras:

```toml
graph = [
    "graphifyy>=0.4.20",
    "matplotlib>=3.8",
    "scipy>=1.11",
]
all = [
    "graphifyy>=0.4.20",
    "matplotlib>=3.8",
    "scipy>=1.11",
]
```

Keep the existing formatting style of the file. Do not add a version
pin tighter than the minimums above unless the repository already pins
those packages elsewhere.

## Test

Update `tests/test_graphify_bridge.py::test_pyproject_has_graph_extra`:

```python
def test_pyproject_has_graph_extra():
    from llmwiki import REPO_ROOT

    content = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'graph = [' in content
    assert "graphifyy" in content
    assert "matplotlib" in content
    assert "scipy" in content
```

The `all` extra uses the same packages; the test can also assert that
the string `matplotlib` appears at least twice, but a simple presence
check is enough.

## Verification

```bash
.venv/bin/pip install -e '.[graph]'
.venv/bin/python -c "import matplotlib, scipy; print(matplotlib.__version__, scipy.__version__)"
.venv/bin/python -m pytest tests/test_graphify_bridge.py -q
.venv/bin/python -m llmwiki graph
```

Expected:

- Both imports succeed.
- The graph command completes without `ModuleNotFoundError`.
- `graphify-out/graph.json` is written.

If `pip install -e '.[graph]'` changes installed versions, record the
diff in the PR body. Do not commit a lock file.

## Acceptance criteria

- [ ] `graph` extra lists `matplotlib` and `scipy`.
- [ ] `all` extra lists `matplotlib` and `scipy`.
- [ ] The updated test passes.
- [ ] `llmwiki graph` completes without a missing-module fallback.
- [ ] `CHANGELOG.md` updated.

## Out of scope

- Updating `README.md` and `docs/configuration.md`. The repository rule
  says new runtime deps need docs; add one line there only if the
  installation instructions mention the `graph` extra. If they do,
  update the same PR.
- Changing graphify itself.
- Adding `numpy` or other transitive dependencies; `graphifyy`
  already declares them.

## Rollback

Revert `pyproject.toml` and the test.
