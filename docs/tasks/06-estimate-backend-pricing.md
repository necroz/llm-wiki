# 06 — Make `--estimate` honest about the backend

- Priority: P2
- Depends on: none
- Size: S (30–45 min)
- Files: `llmwiki/cli.py`, `tests/test_synthesize_estimate.py`
- PR title: `fix: label synthesize estimate with the active backend`

## Read first

- `llmwiki/cli.py`, `_synthesize_estimate` (around line 558) and
  `cmd_synthesize` (around line 472)
- `llmwiki/synth/estimate.py`, `synthesize_estimate_report`
- `llmwiki/cache.py`, `MODEL_PRICING` and `estimate_cost`
- `tests/test_synthesize_estimate.py`

## Problem

`_synthesize_estimate()` calls:

```python
report = synthesize_estimate_report()
```

`estimate_cost()` rejects unknown models with `ValueError`. The report
therefore always uses `DEFAULT_MODEL` (currently a Claude model) and
prints:

```
Model: claude-sonnet-4-6
Incremental sync: $55.4754
```

when the configured backend is `openai` with `model: qwen3.8`, which
runs on a local server. The dollar figure is meaningless and
misleading.

Do not pass `qwen3.8` directly to `estimate_cost`; it will raise
`ValueError: unknown model`.

## Required change

Pass the loaded config into `_synthesize_estimate`. In `cmd_synthesize`
the config is already loaded before the estimate branch:

```python
if args.estimate:
    return _synthesize_estimate(config)
```

Change the signature:

```python
def _synthesize_estimate(config: dict | None = None) -> int:
```

At the start of the function, inspect the backend:

```python
from llmwiki.cache import MODEL_PRICING

synth = (config or {}).get("synthesis", {}) or {}
backend = str(synth.get("backend") or "dummy")
model = str(synth.get("model") or "")
priced = model in MODEL_PRICING
```

Print one note when the active backend is not priced by
`MODEL_PRICING`:

```python
if backend not in {"anthropic", "claude"} or (model and not priced):
    print(
        f"note: cost estimate uses Claude API pricing; "
        f"backend {backend!r} may bill differently "
        f"(local models are usually free)."
    )
```

Keep the existing report and numbers unchanged. The note is enough for
this card. Do not build a new pricing table.

If `model` is in `MODEL_PRICING`, pass it through:

```python
report = synthesize_estimate_report(model=model if priced else None)
```

## Tests

Add to `tests/test_synthesize_estimate.py`:

- One test that captures output for
  `config = {"synthesis": {"backend": "openai", "model": "qwen3.8"}}`
  and asserts the note is printed.
- One test for
  `config = {"synthesis": {"backend": "dummy"}}` that asserts the note
  is printed.
- One test for a Claude model that is in `MODEL_PRICING` and asserts
  the note is not printed. Use a model key from `MODEL_PRICING` rather
  than a hardcoded name.

Use `capsys` and call `_synthesize_estimate(config)` directly. Stub the
report if the test becomes slow:

```python
monkeypatch.setattr(
    "llmwiki.cli.synthesize_estimate_report",
    lambda **kwargs: {...},
)
```

If the function is imported inside `_synthesize_estimate`, patch the
module attribute it actually looks up.

## Verification

```bash
.venv/bin/python -m pytest tests/test_synthesize_estimate.py -q
.venv/bin/python -m llmwiki synthesize --estimate
```

Expected in this repository:

```
note: cost estimate uses Claude API pricing; backend 'openai' may bill differently ...
```

The existing `Corpus`, `Already synthesized`, and cost lines remain.

## Acceptance criteria

- [ ] `_synthesize_estimate` receives the config.
- [ ] A non-Claude backend prints the note.
- [ ] A priced Claude model prints no note.
- [ ] Existing estimate tests still pass.
- [ ] `CHANGELOG.md` updated.

## Out of scope

- Adding pricing for local or OpenAI-compatible models.
- Changing `synthesize_estimate_report`.
- Changing the default model.

## Rollback

Revert the signature, the note, and the tests.
