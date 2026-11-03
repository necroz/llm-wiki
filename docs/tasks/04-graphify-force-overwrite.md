# 04 — Force graphify overwrite after page removal

- Priority: P1
- Depends on: none
- Size: S (15–30 min)
- Files: `llmwiki/graphify_bridge.py`, `tests/test_graphify_bridge.py`
- PR title: `fix: force graphify overwrite after page removal`

## Read first

- `llmwiki/graphify_bridge.py`, function `build_graphify_graph`
  (around line 221), especially the `to_json(...)` call near line 332
- `tests/test_graphify_bridge.py`
- `graphify/export.py`, signature of `to_json` (installed package):
  it accepts `force: bool = False`

## Problem

After pages are deleted or coalesced, `llmwiki graph` prints:

```
[graphify] WARNING: Refusing to overwrite — saved semantic node(s) are missing
```

Then it falls back to the builtin engine and leaves `graphify-out/`
stale. The cause: `to_json` protects an existing graph from shrinking.
`llmwiki` regenerates the graph from `wiki/`, so the old graph should be
replaced.

This happened after the 54-to-1 session coalescing. The workaround was
`rm -rf graphify-out`, which is not acceptable for users.

## Required change

In `llmwiki/graphify_bridge.py` change:

```python
to_json(G, communities, str(json_path))
```

to:

```python
to_json(G, communities, str(json_path), force=True)
```

Add a short comment:

```python
# llmwiki owns this graph: it is regenerated from wiki/ on every run.
# Without force=True, graphify refuses to shrink a graph after pages
# are deleted or coalesced.
```

Do not pass `force=True` to `to_html` or other exporters unless their
signatures accept it.

## Test

`build_graphify_graph` is heavy and depends on the optional `graphify`
package. A unit test that runs the whole pipeline is impractical.

Add one focused test to `tests/test_graphify_bridge.py` that checks the
call contract. Keep it skipped when graphify is not installed:

```python
@pytest.mark.skipif(
    not _GRAPHIFY_INSTALLED,
    reason="graphify is an optional dependency",
)
def test_build_graphify_graph_forces_overwrite():
    import inspect
    from llmwiki.graphify_bridge import build_graphify_graph

    source = inspect.getsource(build_graphify_graph)
    assert "force=True" in source
    assert "to_json(" in source
```

The source assertion is intentionally narrow. It guards the regression
without building a graph.

## Integration check

This is the real acceptance test. Use a disposable source page so no
content is lost:

```bash
cp wiki/sources/llm-wiki/2026-10-29-session_d45df3ef6596bc2f63c549dda7fc2060.md /tmp/keep.md
mv wiki/sources/llm-wiki/2026-10-29-session_d45df3ef6596bc2f63c549dda7fc2060.md /tmp/deleted.md
.venv/bin/python -m llmwiki graph 2>&1 | tee /tmp/graph-check.log
! grep -q "Refusing to overwrite" /tmp/graph-check.log
```

Expected: graphify writes `graphify-out/graph.json` and prints no
"Refusing to overwrite" warning.

Restore the page and rebuild:

```bash
mv /tmp/deleted.md wiki/sources/llm-wiki/2026-10-29-session_d45df3ef6596bc2f63c549dda7fc2060.md
.venv/bin/python -m llmwiki graph
```

Check that the graph contains the expected node count:

```bash
python3 -c "import json; d=json.load(open('graphify-out/graph.json')); print(len(d['nodes']))"
```

## Acceptance criteria

- [ ] `to_json(..., force=True)` is in the bridge.
- [ ] The focused test passes.
- [ ] The integration check prints no "Refusing to overwrite".
- [ ] `graphify-out/graph.json` is rewritten (mtime changes).
- [ ] `llmwiki lint` still reports `0 errors`.
- [ ] `CHANGELOG.md` updated.

## Out of scope

- Adding a `--force` CLI flag. Always forcing is correct here because
  llmwiki owns the graph.
- Changing graphify itself.
- Deleting `graphify-out` in code.

## Rollback

Remove `force=True`. Users can still recover with
`rm -rf graphify-out`.
