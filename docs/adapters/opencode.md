---
title: "OpenCode / OpenClaw adapter"
type: navigation
docs_shell: true
---

# OpenCode / OpenClaw adapter

OpenCode V2 stores sessions in a live SQLite database. llmwiki exports
that database to a Claude-style JSONL corpus and converts it with the
`opencode` adapter. Legacy OpenCode / OpenClaw JSONL stores are still
supported.

**AI-session adapter** (`is_ai_session = True`) — fires by default when
the export corpus or a legacy session store contains a `.jsonl` file.

## Session store

OpenCode V2 stores its database in the path reported by
`opencode debug paths` → `db`. On this Linux checkout that is
`~/.local/share/opencode/opencode.db`; use the `db` line from that command
on macOS and Windows as well (typically the same `.local/share/opencode`
location on macOS and `%USERPROFILE%\.local\share\opencode` on Windows).

The exporter writes:

```text
~/.llmwiki/opencode/<project-slug>/<session-id>.jsonl
```

Legacy OpenCode / OpenClaw stores are also detected:

- `~/.config/opencode/sessions/` (Linux)
- `~/.config/openclaw/sessions/` (Linux)
- the equivalent macOS / Windows application-support paths

## Export

```bash
llmwiki opencode export
llmwiki opencode export --project demo_project --dry-run
llmwiki opencode export --since 2026-09-01
llmwiki opencode export --include-subagents
```

The exporter opens the database read-only, ignores non-message rows
(`idle`, `system`, `synthetic`, `shell`, `compaction`), drops private
reasoning blocks, and preserves text, tool calls, tool results, and file
placeholders. `session_v2.time_updated` is the incremental key. A full
export prunes JSONL for sessions deleted from the database.

`llmwiki sync` runs the same export automatically when the database
exists. Disable it with `--no-opencode-export`.

## Autosync

```bash
llmwiki opencode autosync
llmwiki opencode autosync --build
```

The command uses the shared OS-level `FileLock`, so two concurrent
`session.idle` events cannot run export/convert at the same time. Logs
go to `~/.llmwiki/opencode/autosync.log`.

To run autosync after every finished OpenCode turn, install the plugin:

```bash
llmwiki opencode install --dry-run
llmwiki opencode install
```

The plugin is written to
`$XDG_CONFIG_HOME/opencode/plugins/llmwiki-autosync/index.ts` (by default
`~/.config/opencode/...`, the same directory OpenCode reads). It subscribes
to OpenCode's `session.idle` event and spawns:

```text
<python> -m llmwiki opencode autosync --quiet --lock-timeout 0 [--build]
```

`--lock-timeout 0` makes a busy cycle a no-op: if the previous autosync is
still running, the new one exits at once instead of waiting for the lock,
so idle sessions never pile up waiting processes. The next idle event (or
a scheduled run without the flag) picks up what was skipped.

OpenCode watches the plugin directory and loads it automatically. The
plugin catches spawn errors so a missing interpreter cannot break the
OpenCode server.

## Enable or disable

The adapter fires automatically when the export corpus has sessions.
`opencode.enabled: false` stops the automatic export refresh:

```jsonc
// sessions_config.json
{ "opencode": { "enabled": false } }
```

It does **not** remove an existing export corpus from conversion. To
exclude OpenCode from a sync entirely, list the adapters explicitly
without it:

```bash
llmwiki sync --adapter claude_code zcode --no-opencode-export
```

## Output layout

Standard `raw/sessions/<YYYY-MM-DDTHH-MM>-<project>-<slug>.md`.
Subagent sessions are skipped by default; `--include-subagents` writes
`subagent-<session-id>.jsonl` so the adapter can mark them.

## Code

- `llmwiki/opencode/export.py`
- `llmwiki/opencode/autosync.py`
- `llmwiki/opencode/install.py`
- `llmwiki/adapters/opencode.py`
- Tests: `tests/test_opencode_export.py`, `tests/test_opencode_adapter.py`,
  `tests/test_opencode_autosync.py`, `tests/test_opencode_install.py`
- Issue history: #43 (initial adapter), OpenCode V2 SQLite support.

## See also

- [All adapters](../../README.md#works-with) — comparison table of
  every agent adapter llmwiki supports out of the box.
