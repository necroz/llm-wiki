# 08 — Make wikilink stripping configurable

- Priority: P3
- Depends on: none
- Size: S (30–45 min)
- Files: `llmwiki/synth/pipeline.py`, `llmwiki/cli.py`,
  `examples/sessions_config.json`, `tests/test_synth_pipeline.py`
- PR title: `feat: make unresolved wikilink stripping configurable`

## Read first

- `llmwiki/synth/pipeline.py`, `_strip_unresolved_wikilinks`
  (around line 389) and `synthesize_new_sessions` (around line 900)
- `llmwiki/cli.py`, `cmd_synthesize` (around line 472)
- `examples/sessions_config.json`, the `synthesis` block
- `AGENTS.md`, the "Cross-link everything" hard rule

## Problem

After every synthesis, `_strip_unresolved_wikilinks` replaces every
`[[Target]]` whose page does not exist with plain text. This keeps
`link_integrity` clean, but it is permanent. If the user creates the
missing page later, the old source pages no longer link to it.

Some users prefer red links: they show what the wiki should contain.

## Required change

Add a boolean option, default `True` to preserve current behavior.

1. Add a parameter to `synthesize_new_sessions`:

   ```python
   strip_unresolved_wikilinks: bool = True,
   ```

2. Guard the cleanup block:

   ```python
   if strip_unresolved_wikilinks and summary["synthesized"] > 0:
       ...
   ```

3. In `cmd_synthesize`, read the config:

   ```python
   strip_links = bool(synth_cfg.get("strip_unresolved_wikilinks", True))
   ```

   Pass it to `synthesize_new_sessions`.

4. Add the key to `examples/sessions_config.json`:

   ```json
   "strip_unresolved_wikilinks": true
   ```

5. Document the trade-off in one sentence in the `synthesis` block
   comment. Keep it short.

Do not change `_strip_unresolved_wikilinks` itself.

## Tests

Add to `tests/test_synth_pipeline.py`:

- A test with a fake backend that returns a body containing
  `[[MissingPage]]`.
- Run `synthesize_new_sessions(..., strip_unresolved_wikilinks=False)`
  against a temporary raw/sources tree.
- Assert the written page still contains `[[MissingPage]]`.
- Run the same setup with `strip_unresolved_wikilinks=True`.
- Assert the page contains `MissingPage` without brackets.

Use the existing `_seed_raw` helper or copy its fixture shape. Do not
touch the real wiki.

## Verification

```bash
.venv/bin/python -m pytest tests/test_synth_pipeline.py -q
.venv/bin/python -m pytest tests/ -q
.venv/bin/python -m llmwiki lint
```

Expected: tests pass, lint reports `0 errors`, and the default behavior
is unchanged.

## Acceptance criteria

- [ ] `synthesize_new_sessions` accepts `strip_unresolved_wikilinks`.
- [ ] The CLI reads `synthesis.strip_unresolved_wikilinks`.
- [ ] Default is `true`; existing runs behave as before.
- [ ] `false` preserves unresolved wikilinks.
- [ ] Both test cases pass.
- [ ] `CHANGELOG.md` updated.

## Out of scope

- Restoring links that were already stripped in existing pages.
- Changing the `link_integrity` lint rule.
- A per-project override.

## Rollback

Remove the parameter and the config key. Default behavior does not
change, so no data migration is needed.
