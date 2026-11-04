---
title: "CLI reference"
type: navigation
docs_shell: true
---

# CLI reference

**Every** `python3 -m llmwiki <subcommand>` — with every flag, realistic
examples, and expected output. If a command isn't listed here it isn't
shipping. This page is generated against the live argparse tree, so
adding a flag without documenting it will fail the guardrail test.

Global flags: `-h` / `--help` on every command, `--version` at the root.

---

## Top-level

```bash
python3 -m llmwiki --version    # → llmwiki <version>
python3 -m llmwiki --help       # list every subcommand
python3 -m llmwiki              # same as --help
```

The shorter alias `llmwiki` works too once the package is installed
(`pip install llm-notebook` or via Homebrew — see
[`deploy/pypi-publishing.md`](../deploy/pypi-publishing.md) /
[`deploy/homebrew-setup.md`](../deploy/homebrew-setup.md)).

---

## `init` — scaffold `raw/` / `wiki/` / `site/`

Creates the three data directories + seeds nine navigation files inside
`wiki/`.

```bash
python3 -m llmwiki init
```

**Flags:** none.

**Expected output:**

```
  raw/sessions/
  wiki/sources/
  wiki/entities/
  wiki/concepts/
  wiki/syntheses/
  site/
  seeded wiki/dashboard.md
  seeded wiki/index.md
  ...
```

**Idempotent.** Safe to re-run — it never overwrites files that exist.

---

## `sync` — convert `.jsonl` sessions to markdown

The workhorse. Walks every configured adapter, converts new sessions
into `raw/sessions/`, then (by default) auto-builds and auto-lints.

```bash
python3 -m llmwiki sync
python3 -m llmwiki sync --since 2026-04-01 --project llm-wiki
python3 -m llmwiki sync --adapter claude_code codex_cli
python3 -m llmwiki sync --no-auto-build --no-auto-lint
python3 -m llmwiki sync --vault "~/Documents/Obsidian Vault"
python3 -m llmwiki sync --vault ~/my-vault --allow-overwrite
python3 -m llmwiki sync --force
python3 -m llmwiki sync --dry-run
python3 -m llmwiki sync --adapter zcode --no-zcode-export
python3 -m llmwiki sync --adapter opencode --no-opencode-export
```

### Flags

| Flag | What |
|---|---|
| `--adapter NAME [NAME ...]` | Limit to specific adapters. Default: every adapter with a session store on disk. |
| `--since YYYY-MM-DD` | Only sessions on/after this date (e.g. `--since 2026-04-01`). |
| `--project SUBSTRING` | Filter by project-slug substring. |
| `--include-current` | Include sessions < 60 min old (default skips live ones). |
| `--force` | Ignore the mtime state file, reconvert everything. |
| `--dry-run` | Preview conversions; does not write `raw/`, refresh the ZCode/OpenCode exports, or auto-build. |
| `--zcode-export` / `--no-zcode-export` | Refresh ZCode's JSONL corpus before converting (default: on when the database exists). |
| `--zcode-db PATH` | ZCode database path. Default: `~/.zcode/cli/db/db.sqlite`. |
| `--opencode-export` / `--no-opencode-export` | Refresh OpenCode's JSONL corpus before converting (default: on when the database exists). |
| `--opencode-db PATH` | OpenCode database path. Default: `~/.local/share/opencode/opencode.db`. |
| `--auto-build` / `--no-auto-build` | Rebuild `site/` after sync (default: on). |
| `--auto-lint` / `--no-auto-lint` | Run `lint` after sync (default: on). |
| `--vault PATH` | Vault-overlay mode — write new pages inside the given Obsidian / Logseq vault instead of `wiki/`. See [`guides/existing-vault.md`](../guides/existing-vault.md). |
| `--allow-overwrite` | With `--vault`: allow clobbering existing vault pages (default: refuse, append under `## Connections` instead). |

### Expected output (typical)

```
==> claude_code: 3 new sessions since last sync
✓ wrote 3 pages under raw/sessions/
✓ ingested into wiki/sources/ (2 new entities, 1 new concept)
✓ auto-build: site/ rebuilt (690 HTML files)
✓ auto-lint: 28 issues: 0 errors, 22 warnings, 6 info
```

### Common recipes

- Nightly cron-style sync of one project only:
  `llmwiki sync --project my-project --no-auto-lint --since $(date -v-1d +%Y-%m-%d)`
- Vault-overlay round-trip: `llmwiki sync --vault "~/Documents/Obsidian Vault"`

---

## `build` — compile the static HTML site

Turns `wiki/` markdown into `site/` HTML.

```bash
python3 -m llmwiki build
python3 -m llmwiki build --out ~/public_html
python3 -m llmwiki build --search-mode tree
python3 -m llmwiki build --synthesize --claude /usr/local/bin/claude
python3 -m llmwiki build --vault ~/my-vault --out ~/site
```

### Flags

| Flag | What |
|---|---|
| `--out PATH` | Output directory. Default: `./site/`. |
| `--synthesize` | Call the `claude` CLI for overview synthesis (experimental). |
| `--claude PATH` | Path to the `claude` binary. Default: `/usr/local/bin/claude`. |
| `--search-mode {auto,tree,flat}` | Search routing mode (#53). `auto` picks tree vs flat from heading depth; `tree` / `flat` force the mode. Default: `auto`. |
| `--vault PATH` | Vault-overlay mode — build from an existing Obsidian / Logseq vault. Output still lands at `--out`. |

### Expected output (final lines)

```
  wrote search-index.json (7 KB meta) + 30 chunks (904 KB total) · tree mode · 64% deep pages
  wrote 7 AI-consumable exports: ai-readme.md, graph.jsonld, llms-full.txt, llms.txt, robots.txt, rss.xml, sitemap.xml
  wrote site/graph.html (interactive graph viewer)
  wrote site/prototypes/index.html (6 prototype states)
  wrote site/docs/ (94 editorial pages: hub + tutorials + style guide)
==> build complete: 703 HTML files, 61 MB
```

---

## `serve` — start a local HTTP server

```bash
python3 -m llmwiki serve
python3 -m llmwiki serve --port 9000
python3 -m llmwiki serve --dir ~/public_html
python3 -m llmwiki serve --open
```

### Flags

| Flag | What |
|---|---|
| `--dir PATH` | Directory to serve. Default: `./site/`. |
| `--port N` | Port. Default: `8765`. |
| `--host ADDR` | Bind address. Default: `127.0.0.1`. Use `0.0.0.0` to share on LAN. |
| `--open` | Open the browser at the root URL after starting. |

**Stdlib only** — it's `http.server` underneath. Safe for local use;
don't expose to the public internet.

---

## `adapters` — list every adapter + its status

```bash
python3 -m llmwiki adapters
```

**Flags:** none.

**Expected output:**

```
Registered adapters:
  name              default   configured    description
  ----------------  --------  ------------  ----------------------------------------
  chatgpt           no        -             ChatGPT — parses conversations.json …
  claude_code       yes       ✓            Claude Code — reads ~/.claude/projects/
  codex_cli         no        ✓            Codex CLI — reads ~/.codex/sessions/
  copilot           no        -             GitHub Copilot — reads VS Code …
  cursor            no        -             Cursor — reads VS Code workspaceStorage
  gemini_cli        no        -             Gemini CLI — reads ~/.gemini/
  jira              no        -             Jira — reads via REST API
  meeting           no        -             Meeting transcripts (VTT/SRT)
  obsidian          no        -             Obsidian — reads a vault
  opencode          no        -             OpenCode / OpenClaw sessions
  web_clipper       no        -             Obsidian Web Clipper intake
```

Columns: **default** (runs when you don't pass `--adapter`), **configured**
(adapter sees a valid session store on this machine).

---

## `zcode` — export the ZCode session store

ZCode keeps sessions in a live SQLite database, not in per-session
JSONL files. This command writes an incremental, Claude-style JSONL
corpus that the `zcode` adapter can convert. `llmwiki sync` runs the
same export automatically when the database exists and the `zcode`
adapter is selected.

```bash
python3 -m llmwiki zcode export
python3 -m llmwiki zcode export --project demo_project --dry-run
python3 -m llmwiki zcode export --since 2026-09-01
python3 -m llmwiki zcode export --project-root ~/dev
python3 -m llmwiki zcode export --include-subagents
```

### `zcode export` flags

| Flag | What |
|---|---|
| `--db PATH` | ZCode database. Default: `~/.zcode/cli/db/db.sqlite`. |
| `--out-dir DIR` | JSONL output. Default: `~/.llmwiki/zcode/`. |
| `--state-file PATH` | Incremental state. Default: `<out-dir>/.export-state.json`. |
| `--since YYYY-MM-DD` | Only sessions updated on or after this date. |
| `--project SUBSTR` | Case-insensitive project path or slug filter. |
| `--project-root DIR` | Treat this directory as a project root (repeatable). |
| `--include-subagents` | Include `subagent_child` sessions. Off by default. |
| `--keep-stale` | Keep JSONL for sessions deleted from the database. |
| `--strict` | Exit 1 if any session fails, even when others exported. |
| `--dry-run` | Print counters without writing files. |

The export is read-only against ZCode's database and idempotent:
`session.time_updated` is the incremental key. A full export also
removes JSONL for sessions that disappeared from the database.

### `zcode autosync`

Portable scheduler entry point. It uses an OS-level lock instead of
`flock`, so it works on Linux, macOS, and Windows.

```bash
python3 -m llmwiki zcode autosync
python3 -m llmwiki zcode autosync --build
python3 -m llmwiki zcode autosync --coalesce --build
```

| Flag | What |
|---|---|
| `--project SUBSTR` | Export/convert one project substring. |
| `--build` | Rebuild `site/` after conversion. |
| `--coalesce` | Run deterministic low-signal coalescing (no LLM). |
| `--lock-timeout SECONDS` | Wait for another autosync. Default: 600. |
| `--log PATH` | Log file. Default: `~/.llmwiki/zcode/autosync.log`. |
| `--quiet` | Write only to the log. |

### `zcode install`

Installs the ZCode `Stop` hook and one native scheduler entry:
systemd user timer (Linux), launchd agent (macOS), or Task Scheduler
(Windows). The hook is idempotent.

```bash
python3 -m llmwiki zcode install --dry-run
python3 -m llmwiki zcode install --scheduler systemd --at 03:30
python3 -m llmwiki zcode install --scheduler launchd --coalesce
python3 -m llmwiki zcode install --scheduler windows --no-build
```

| Flag | What |
|---|---|
| `--scheduler {auto,systemd,launchd,windows,none}` | Default: auto-detect. |
| `--at HH:MM` | Daily start time. Default: `03:30`. |
| `--python PATH` | Interpreter for the scheduled command. Default: current. |
| `--repo-root PATH` | Checkout used as `WorkingDirectory`. |
| `--build` / `--no-build` | Rebuild `site/` after sync (default: on). |
| `--coalesce` | Also run deterministic coalescing. |
| `--no-hook` | Skip the ZCode Stop hook. |
| `--dry-run` | Print changes only. |
| `--force` | Replace an existing autosync hook. |

---

## `opencode` — export the OpenCode V2 session store

OpenCode V2 keeps sessions in a live SQLite database
(`~/.local/share/opencode/opencode.db`). This command writes an
incremental, Claude-style JSONL corpus under `~/.llmwiki/opencode/`.
`llmwiki sync` runs the same export automatically when the database
exists and the `opencode` adapter is selected.

```bash
python3 -m llmwiki opencode export
python3 -m llmwiki opencode export --project demo --dry-run
python3 -m llmwiki opencode export --since 2026-09-01
python3 -m llmwiki opencode export --include-subagents
```

### `opencode export` flags

| Flag | What |
|---|---|
| `--db PATH` | OpenCode database. Default: `~/.local/share/opencode/opencode.db`. |
| `--out-dir DIR` | JSONL output. Default: `~/.llmwiki/opencode/`. |
| `--state-file PATH` | Incremental state. Default: `<out-dir>/.export-state.json`. |
| `--since YYYY-MM-DD` | Only sessions updated on or after this date. |
| `--project SUBSTR` | Case-insensitive project path or slug filter. |
| `--project-root DIR` | Treat this directory as a project root (repeatable). |
| `--include-subagents` | Include child sessions. Off by default. |
| `--keep-stale` | Keep JSONL for sessions deleted from the database. |
| `--strict` | Exit 1 if any session fails, even when others exported. |
| `--dry-run` | Print counters without writing files. |

The export is read-only against OpenCode's database and idempotent:
`session_v2.time_updated` is the incremental key.

### `opencode autosync`

Portable scheduler entry point. It uses the shared OS-level lock, so
concurrent `session.idle` events cannot overlap.

```bash
python3 -m llmwiki opencode autosync
python3 -m llmwiki opencode autosync --build
```

| Flag | What |
|---|---|
| `--project SUBSTR` | Export/convert one project substring. |
| `--build` | Rebuild `site/` after conversion. |
| `--lock-timeout SECONDS` | Wait for another autosync. Default: 600. |
| `--log PATH` | Log file. Default: `~/.llmwiki/opencode/autosync.log`. |
| `--quiet` | Write only to the log. |

### `opencode install`

Writes the OpenCode server plugin that runs autosync on `session.idle`:

```text
~/.config/opencode/plugins/llmwiki-autosync/index.ts
```

```bash
python3 -m llmwiki opencode install --dry-run
python3 -m llmwiki opencode install
python3 -m llmwiki opencode install --no-build --force
```

| Flag | What |
|---|---|
| `--python PATH` | Interpreter for the plugin command. Default: current. |
| `--repo-root PATH` | llmwiki checkout used as the plugin working directory. |
| `--build` | Rebuild `site/` after each autosync (default: off). |
| `--no-build` | Explicitly disable rebuilds. |
| `--dry-run` | Print the path without writing. |
| `--force` | Replace an existing plugin file. |

---

## `doctor` — check the local install

Prints Python, `markdown`, virtualenv, config files, and ZCode database /
export paths. Exits `1` only when a required check fails.

```bash
python3 -m llmwiki doctor
```

Typical output:

```
llmwiki doctor 1.3.82
  [ok  ] python       3.12.4
  [ok  ] markdown     installed
  [ok  ] venv         active
  [ok  ] config       examples/sessions_config.json, config.local.json
  [ok  ] zcode_db     /home/me/.zcode/cli/db/db.sqlite
  [ok  ] zcode_export 842 session file(s) in /home/me/.llmwiki/zcode
```

---

## `audit` — scan for usernames and secrets

Read-only pre-publish check over `raw/`, `wiki/`, and `site/`. It reports
matches; it does not edit files.

```bash
python3 -m llmwiki audit
python3 -m llmwiki audit --path site --json
python3 -m llmwiki audit --fail-on-findings
```

| Flag | What |
|---|---|
| `--path PATH` | File or directory to scan (repeatable). Default: `raw/ wiki/ site/`. |
| `--username NAME` | Username to search for. Default: `redaction.real_username` or `$USER`. |
| `--json` | Emit a machine-readable report. |
| `--fail-on-findings` | Exit 1 when anything is found (publish gate). |

Findings are grouped as `username`, `secret` (built-in token patterns),
or `pattern` (user `redaction.extra_patterns`). Matched text is masked
in the preview.

---

## `graph` — build the knowledge graph

```bash
python3 -m llmwiki graph                              # builtin wikilink graph
python3 -m llmwiki graph --engine graphify             # AI-powered graph (requires graphifyy)
python3 -m llmwiki graph --format json
python3 -m llmwiki graph --format html
```

### Flags

| Flag | What |
|---|---|
| `--format {json,html,both}` | Output format(s). Default: `both`. |
| `--engine {builtin,graphify}` | Graph engine. `builtin` = stdlib wikilink graph. `graphify` = AI-powered with community detection, confidence-scored edges, god nodes. Requires `pip install graphifyy`. Default: `builtin`. |

**Builtin engine:** Emits `graph/graph.json` (nodes + edges) and/or `graph/graph.html`
(vis-network interactive viewer). The interactive version is also
auto-copied into `site/graph.html` on every `build`.

**Graphify engine:** Runs the [Graphify](https://github.com/safishamsi/graphify) pipeline:
tree-sitter AST extraction for code, semantic analysis for docs, Leiden community
detection, god-node analysis. Outputs to `graphify-out/` (graph.json, graph.html,
GRAPH_REPORT.md) and copies to `graph/` for build compatibility. Install:
`pip install llm-notebook[graph]` or `pip install graphifyy`.

---

## `export` — AI-consumable site exports

Single positional argument picks the format.

```bash
python3 -m llmwiki export llms-txt
python3 -m llmwiki export llms-full-txt
python3 -m llmwiki export jsonld
python3 -m llmwiki export sitemap
python3 -m llmwiki export rss
python3 -m llmwiki export robots
python3 -m llmwiki export ai-readme
python3 -m llmwiki export all --out ~/custom-site
```

### Positional

| Value | Writes |
|---|---|
| `llms-txt` | `site/llms.txt` — llmstxt.org spec |
| `llms-full-txt` | `site/llms-full.txt` — flattened plain-text corpus (≤ 5 MB) |
| `jsonld` | `site/graph.jsonld` — schema.org entity graph |
| `sitemap` | `site/sitemap.xml` |
| `rss` | `site/rss.xml` |
| `robots` | `site/robots.txt` |
| `ai-readme` | `site/ai-readme.md` |
| `all` | all of the above |

### Flags

| Flag | What |
|---|---|
| `--out PATH` | Output directory. Default: `./site/`. |

---

## `lint` — run 14 wiki-quality rules

```bash
python3 -m llmwiki lint
python3 -m llmwiki lint --json
python3 -m llmwiki lint --fail-on-errors
python3 -m llmwiki lint --rules link_integrity,orphan_detection
python3 -m llmwiki lint --include-llm
python3 -m llmwiki lint --wiki-dir ~/another-wiki
```

### Flags

| Flag | What |
|---|---|
| `--wiki-dir PATH` | Wiki dir. Default: `./wiki`. |
| `--rules NAMES` | Comma-separated rule names. Default: all applicable. |
| `--include-llm` | Also run the three LLM-powered rules (requires a callback wired in). |
| `--json` | JSON output. |
| `--fail-on-errors` | Exit 1 if any error-severity issues. |

### Rules

8 structural (`frontmatter_completeness`, `frontmatter_validity`,
`link_integrity`, `orphan_detection`, `content_freshness`,
`entity_consistency`, `duplicate_detection`, `index_sync`) + 3 LLM-powered
(`contradiction_detection`, `claim_verification`, `summary_accuracy`) +
2 v1.1+ (`stale_candidates`, `cache_tier_consistency`).

### Expected output

```
  scanned 31 pages
  28 issues: 0 errors, 22 warnings, 6 info

## link_integrity (22)
  [warning] entities/GPT5.md: broken wikilink [[MultimodalModels]]
  ...
```

---

## `candidates` — approval workflow

Positional `action` picks `list` / `promote` / `merge` / `discard`.

```bash
python3 -m llmwiki candidates list
python3 -m llmwiki candidates list --stale --stale-days 60
python3 -m llmwiki candidates list --json
python3 -m llmwiki candidates promote --slug NewEntity
python3 -m llmwiki candidates promote --slug NewEntity --kind concepts
python3 -m llmwiki candidates merge --slug DuplicateFoo --into Foo
python3 -m llmwiki candidates discard --slug BogusEntity --reason "LLM hallucinated"
```

### Flags

| Flag | What |
|---|---|
| `--slug NAME` | Candidate slug. **Required** for `promote` / `merge` / `discard`. |
| `--into NAME` | For `merge`: target slug. |
| `--reason TEXT` | For `discard`: why (written to archive's `.reason.txt`). |
| `--kind {entities,concepts,sources,syntheses}` | Subtree. Auto-detected if omitted. |
| `--wiki-dir PATH` | Wiki dir. Default: `./wiki`. |
| `--stale` | With `list`: only stale candidates. |
| `--stale-days N` | Staleness threshold. Default: 30. |
| `--json` | JSON output for `list`. |

See [`guides/existing-vault.md`](../guides/existing-vault.md) for the
round-trip semantics when a candidate lives inside a vault.

---

## `synthesize` — LLM-backed source-page synthesis

```bash
python3 -m llmwiki synthesize --check            # probe the backend
python3 -m llmwiki synthesize --estimate         # cost preview, no API calls
python3 -m llmwiki synthesize --force            # re-synth everything
python3 -m llmwiki synthesize --coalesce-only    # deterministic cleanup, no LLM
python3 -m llmwiki synthesize                    # real run
```

### Flags

| Flag | What |
|---|---|
| `--check` | Probe backend availability + exit (0 if reachable). |
| `--force` | Ignore state, re-synth every source. |
| `--estimate` | Print cached-vs-fresh token + dollar estimate (#50). |
| `--coalesce-only` | Merge low-signal sessions into cluster pages; no backend call. |

Backend is picked from `synthesis.backend` in `sessions_config.json`
(`dummy` by default, `ollama` for local, future `anthropic`). See
[`reference/prompt-caching.md`](prompt-caching.md).

### Auto-tagging (#351)

Every `synthesize` call now produces **topical** tags alongside the
deterministic baseline.  The synthesizer emits a
`<!-- suggested-tags: prompt-caching, rag, github-actions -->` block
as the first line of its response; the pipeline parses it, strips it
from the body, and merges the tags into frontmatter with:

- **Baseline preserved** — adapter, project slug, model family stay.
- **Maintainer wins** — on `--force`, whatever you added via
  `llmwiki tag add` is kept at the front of the list.
- **Stop-word filter** — the LLM can't re-add boilerplate tags
  (`session`, `summary`, `claude-code`, etc.).
- **Cap 5** — max 5 AI tags per page to prevent drift.
- **Near-dup rejection** — `prompt-cache` is blocked when
  `prompt-caching` is already on the page (threshold 0.80 + prefix
  check).

No extra API round-trip — rides the existing synthesis call, so cost
estimates from `--estimate` are unchanged.  If the backend returns no
suggested-tags block (dummy backend, malformed output), the page still
ships with baseline tags.

---

## `version` — print the installed version

```bash
python3 -m llmwiki version
python3 -m llmwiki --version
```

Both print `llmwiki <version>`.

---

## `query` — search the knowledge graph

```bash
python3 -m llmwiki query "what projects is Pratiyush working on"
python3 -m llmwiki query "Flutter mobile" --depth 2 --budget 1000
```

### Flags

| Flag | What |
|---|---|
| `--depth N` | BFS traversal depth. Default: `3`. |
| `--budget N` | Max output tokens. Default: `2000`. |

Requires Graphify (`pip install llm-notebook[graph]`). Run `llmwiki graph` first
to build the graph.

---

## `all` — run the full pipeline

Convenience entry point that runs `build` → `graph` → `export all` → `lint`
in order. This is the one command to run after `sync` to produce a
CI-ready site.

```bash
python3 -m llmwiki all
python3 -m llmwiki all --graph-engine builtin   # skip optional graphify
python3 -m llmwiki all --skip-graph --strict    # fail CI on any lint issue
```

### Flags

| Flag | What |
|---|---|
| `--out DIR` | Output dir for build + export. Default: `site/`. |
| `--search-mode {auto,tree,flat}` | Forwarded to `build`. Default: `auto`. |
| `--graph-engine {builtin,graphify}` | Forwarded to `graph`. Default: `graphify`. |
| `--skip-graph` | Skip the graph step entirely (useful when graphify is not installed). |
| `--fail-fast` | Stop at the first non-zero step. Default: continue, report the worst exit code. |
| `--strict` | Exit `2` if `lint` reports any errors/warnings. |

Exit codes:

- `0` — every step succeeded.
- non-zero — forwarded from the first (or worst) failing step.
- `2` — `--strict` and lint reported issues.

---

## Exit codes (conventions)

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | Operation failed (user-visible error) |
| `2` | Usage error (bad flags, missing file, etc.) |

Subcommands document their own non-zero exit conditions where relevant
(`lint --fail-on-errors`).

---

## Related

- **[Slash commands](slash-commands.md)** — the `/wiki-*` surface used from Claude Code.
- **[UI reference](ui.md)** — every screen + nav surface on the compiled site.
- **[Configuration](../configuration.md)** · **[Full configuration reference](../configuration-reference.md)**.
