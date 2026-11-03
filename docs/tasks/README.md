# Task cards — bug fixes after the synthesis/coalescing work

These cards are written so a cheap model can execute one card at a time
without reading the whole repository. Each card is one branch and one PR.

## How to hand a card to a model

Use this prompt template:

```
Implement exactly one task card from docs/tasks/.
Read docs/tasks/<card>.md completely before touching code.
Read only the files listed under "Read first".
Make the smallest change that satisfies the acceptance criteria.
Run every command under "Verification".
Do not start another card.
Report: files changed, commands run, command output, anything that failed.
```

Do not give a model more than one card per run. Do not let it "clean up"
unrelated code.

## Order and dependencies

| # | Card | Priority | Size | Depends on | Status |
|---|---|---|---:|---|---|
| 01 | [Isolate synth state in tests](01-test-isolate-synth-state.md) | P0 | S | — | done |
| 02 | [Preserve manual text in project Sessions](02-project-sessions-markers.md) | P0 | M | — | done |
| 03 | [Valid frontmatter for projects index](03-projects-index-frontmatter.md) | P1 | S | — | done |
| 04 | [Force graphify overwrite after page removal](04-graphify-force-overwrite.md) | P1 | S | — | done |
| 05 | [Add graph runtime dependencies](05-graph-extra-dependencies.md) | P1 | S | — | done |
| 06 | [Make --estimate honest about the backend](06-estimate-backend-pricing.md) | P2 | S | — | done |
| 07 | [Do not retry non-retryable OpenAI errors](07-openai-retry-fallback.md) | P2 | M | — | done |
| 08 | [Make wikilink stripping configurable](08-wikilink-strip-flag.md) | P3 | S | — | done |

Optional follow-ups, not carded yet:

- **09 — Coalesce-only hook.** The Stop hook runs `sync` but not
  `synthesize`, so new low-signal sessions are not merged until someone
  runs synthesis. Add `llmwiki synthesize --coalesce-only` and call it
  from `scripts/zcode_sync_hook.sh`.
- **10 — Seed page cleanup.** Eight seed pages (`concepts/*`,
  `entities/*`, `overview`) produce stale-content warnings. Archive them
  or refresh `last_updated`.

## Before you start

Until card 01 is merged, running the test suite mutates the real
synthesis state. Back it up before every test run:

```bash
cp .llmwiki-synth-state.json /tmp/llmwiki-synth-state.bak
```

After the test run:

```bash
cmp .llmwiki-synth-state.json /tmp/llmwiki-synth-state.bak
```

`cmp` must print nothing. If it prints a difference, the test suite
touched production state; restore it:

```bash
cp /tmp/llmwiki-synth-state.bak .llmwiki-synth-state.json
```

## Verification baseline

Run from the repository root:

```bash
.venv/bin/python -m pytest tests/ -q
.venv/bin/python -m llmwiki lint
.venv/bin/python -m llmwiki --version
```

Expected:

- `pytest` exits with code 0.
- `lint` reports `0 errors`. Warnings are allowed.
- `--version` prints the version.

## Rules for every card

- One concern per PR. The repository enforces this in
  `.kiro/steering/contributing-rules.md`.
- Branch name: `fix/<card-slug>`, for example `fix/test-isolate-synth-state`.
- Commit title: `fix: <short description>`.
- Do not modify `raw/`. It is immutable.
- Do not modify `site/`. It is generated.
- Do not rewrite unrelated code, rename functions, or reformat files.
- Add or update a test for every code change. If a test is impractical,
  say why in the PR body.
- Update `CHANGELOG.md` under `## [Unreleased]` with one line.
- Report the exact commands you ran and their output.

## Status tracking

Update the `Status` column in the table above when a card is done:
`todo` → `in-progress` → `done`. Keep the card file as the record of
what was required.
