# 01 — Isolate synth state in tests

- Priority: P0
- Depends on: none
- Size: S (30–60 min)
- Files: `tests/test_synth_pipeline.py`
- PR title: `fix: isolate synth state in tests`

## Read first

- `tests/test_synth_pipeline.py` (the whole file, ~450 lines)
- `llmwiki/synth/pipeline.py`, functions `_resolve_state_file`,
  `_load_state`, `_save_state` (around lines 115–180)

## Problem

`tests/test_synth_pipeline.py` calls `synthesize_new_sessions()` without
passing `state_file`. The function falls back to the real repository
state file:

```
STATE_FILE = REPO_ROOT / ".llmwiki-synth-state.json"
```

Running the test file overwrites that file with six fixture keys:

```
proj/2026-03-01-flickering-orbiting-fern.md
proj/2026-04-01-flickering-orbiting-fern.md
proj/2026-04-01-slug-0.md
...
```

The real file has 800+ keys. After a test run, the next
`llmwiki synthesize` re-processes the whole corpus. That is ~45 minutes
and about $55 by the current estimate.

Reproduced with:

```bash
cp .llmwiki-synth-state.json /tmp/state.before.json
.venv/bin/python -m pytest tests/test_synth_pipeline.py -q
python3 -c "import json; print(len(json.load(open('.llmwiki-synth-state.json'))))"
```

The last command prints `6` instead of 800+.

## Required change

Add one autouse fixture at the top of `tests/test_synth_pipeline.py`,
after the imports:

```python
@pytest.fixture(autouse=True)
def _isolated_synth_state(tmp_path, monkeypatch):
    """Keep the real .llmwiki-synth-state.json out of test runs."""
    monkeypatch.setattr(
        "llmwiki.synth.pipeline.STATE_FILE",
        tmp_path / "synth-state.json",
    )
```

`_resolve_state_file()` reads the module-level `STATE_FILE` at call
time, so monkeypatching the attribute is enough.

Add one regression test in the same file:

```python
def test_synth_state_is_isolated(tmp_path):
    from llmwiki.synth import pipeline

    assert pipeline.STATE_FILE.parent == tmp_path
```

Do not change `llmwiki/synth/pipeline.py`. The production fallback is
correct; the tests must isolate themselves.

## Verification

```bash
cp .llmwiki-synth-state.json /tmp/state.before.json
.venv/bin/python -m pytest tests/test_synth_pipeline.py -q
cmp .llmwiki-synth-state.json /tmp/state.before.json && echo "state unchanged"
```

`cmp` must print nothing before `state unchanged`.

Then run the full suite and repeat the comparison:

```bash
cp .llmwiki-synth-state.json /tmp/state.before-full.json
.venv/bin/python -m pytest tests/ -q
cmp .llmwiki-synth-state.json /tmp/state.before-full.json && echo "state unchanged"
```

If the full suite still changes the file, another test file writes to
the default state. Find it with:

```bash
grep -rn "synthesize_new_sessions(" tests | grep -v "test_synth_pipeline"
```

Add the same fixture to that file. Do not weaken this card to only the
first file.

## Acceptance criteria

- [ ] The autouse fixture exists in `tests/test_synth_pipeline.py`.
- [ ] `test_synth_state_is_isolated` passes.
- [ ] `tests/test_synth_pipeline.py` leaves `.llmwiki-synth-state.json`
      byte-identical.
- [ ] The full `tests/` suite leaves `.llmwiki-synth-state.json`
      byte-identical.
- [ ] `pytest tests/ -q` exits with code 0.
- [ ] `CHANGELOG.md` has one line under `## [Unreleased]`.

## Out of scope

- Refactoring `_load_state` or `_save_state`.
- Changing the production state path.
- Fixing other test files unless the full-suite check proves they
  touch the state file.

## Rollback

Revert the fixture and the new test. No production code changes.
