# OpenCode Autosync Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Export OpenCode V2 sessions from its SQLite store into llmwiki's JSONL corpus, add a production adapter, and install an OpenCode plugin that runs autosync after each idle session.

**Architecture:** Mirror the existing ZCode integration: a read-only SQLite exporter writes Claude-style JSONL under `~/.llmwiki/opencode/`; a core adapter converts that corpus; `llmwiki opencode autosync` runs export → convert → optional build under the shared `FileLock`; `llmwiki opencode install` writes a small OpenCode V2 server plugin that subscribes to `session.idle` and spawns autosync.

**Tech Stack:** Python 3 stdlib (`sqlite3`, `json`, `argparse`, `pathlib`), pytest, TypeScript/OpenCode V2 plugin API (`@opencode/plugin`).

**Spec:** User-approved design in this document; no separate spec file.

## Global Constraints

- Do not modify the live OpenCode database; open it read-only.
- Do not modify ZCode exporter behavior or refactor its code in this branch.
- OpenCode V2 session tables are `session_v2` and `session_message`; `session_message.data` is JSON text.
- Exported records must use the Claude-style shape already consumed by `render_session_markdown`.
- The exporter must be idempotent and incremental via `session_v2.time_updated`.
- The OpenCode plugin is local user config, written to `~/.config/opencode/plugins/llmwiki-autosync/index.ts`.
- Autosync must tolerate a busy lock and must never raise into the OpenCode plugin process.
- Default subagent behavior: skip sessions with a non-null `parent_id` unless `--include-subagents` is passed.
- All new production behavior is test-first.

## Review Focus

- OpenCode schema drift: a missing `session_message` or `session_v2` column must fail with a clear `SchemaError`, not a traceback.
- Tool-only assistant turns: a session whose only assistant output is a tool call must still export.
- Stale exports: deleted OpenCode sessions must be pruned from the JSONL corpus and state file.
- Concurrent autosync: two idle events must not run export/convert concurrently.
- Path escaping: Python interpreter or repo paths containing spaces must survive embedding in the TypeScript plugin.
- Missing event compatibility: if `session.idle` is unavailable, the plugin must not crash the server.

---

### Task 1: OpenCode exporter

**Files:**
- Create: `llmwiki/opencode/__init__.py`
- Create: `llmwiki/opencode/export.py`
- Test: `tests/test_opencode_export.py`

**Interfaces:**
- Produces:
  - `DEFAULT_DB: Path = Path.home() / ".local/share/opencode/opencode.db"`
  - `DEFAULT_OUT_DIR: Path = Path.home() / ".llmwiki" / "opencode"`
  - `SchemaError(RuntimeError)`
  - `project_slug_for_directory(directory: str, home: Path | None = None, project_roots: Sequence[str | Path] | None = None) -> str`
  - `iso_from_ms(value: Any) -> str | None`
  - `parse_since(value: str | None) -> int | None`
  - `validate_schema(con: sqlite3.Connection) -> None`
  - `session_to_records(con: sqlite3.Connection, session: sqlite3.Row) -> list[dict[str, Any]]`
  - `run_export(db_path: Path, out_dir: Path, state_file: Path | None = None, since: int | None = None, project: str | None = None, include_subagents: bool = False, dry_run: bool = False, project_roots: Sequence[str | Path] | None = None, prune_stale: bool = True) -> dict[str, Any]`

- [ ] **Step 1: Write the failing exporter tests**

Create `tests/test_opencode_export.py` with a fixture DB shaped like OpenCode V2:

```python
T0 = 1_700_000_000_000

def make_db(tmp_path: Path) -> Path:
    db = tmp_path / "opencode.db"
    con = sqlite3.connect(db)
    con.executescript("""
        create table session_v2 (
            id text primary key, project_id text, parent_id text,
            directory text, title text, time_created integer,
            time_updated integer, model text
        );
        create table session_message (
            id text primary key, session_id text, type text,
            seq integer, time_created integer, time_updated integer,
            data text
        );
    """)
    con.execute(
        "insert into session_v2 values (?,?,?,?,?,?,?,?)",
        ("ses-1", "proj-1", None, "/home/test/Projects/demo",
         "Demo session", T0, T0 + 5_000, '{"id":"model-x","providerID":"p"}'),
    )
    con.execute(
        "insert into session_message values (?,?,?,?,?,?,?)",
        ("m1", "ses-1", "user", 0, T0, T0,
         json.dumps({"time": {"created": T0}, "text": "hello",
                     "files": [{"name": "shot.png", "mime": "image/png"}]})),
    )
    con.execute(
        "insert into session_message values (?,?,?,?,?,?,?)",
        ("m2", "ses-1", "assistant", 1, T0 + 1_000, T0 + 1_000,
         json.dumps({"time": {"created": T0 + 1_000},
                     "agent": "build", "model": {"id": "model-x"},
                     "content": [
                         {"type": "reasoning", "text": "private"},
                         {"type": "text", "text": "hi"},
                         {"type": "tool", "id": "call-1", "name": "bash",
                          "state": {"status": "completed",
                                    "input": {"command": "pwd"},
                                    "content": [{"type": "text", "text": "/tmp"}]}}
                     ]})),
    )
    con.execute(
        "insert into session_message values (?,?,?,?,?,?,?)",
        ("m3", "ses-1", "idle", 2, T0 + 2_000, T0 + 2_000,
         json.dumps({"type": "idle", "outcome": "succeeded"})),
    )
    con.commit(); con.close()
    return db
```

Assertions:
- `validate_schema` passes on the fixture; remove `session_message.data` in a second DB and assert `SchemaError` names `session_message.data`.
- `session_to_records` returns an `init` record plus user/assistant/tool-result records; reasoning and idle are absent; user text includes `[file: shot.png]`; tool call is in assistant content; tool output is in a following user `tool_result` record.
- `run_export` writes `<out>/<slug>/ses-1.jsonl`; second run reports `unchanged=1`; after bumping `time_updated` and adding a message, it reports `exported=1`.
- `run_export` skips a `parent_id` session by default, includes it with `include_subagents=True`, and names the file `subagent-ses-child.jsonl`.
- `run_export` prunes a deleted session’s JSONL and state entry.
- `run_export(dry_run=True)` writes nothing.
- `test_project_slug_for_directory` covers `~/Projects/demo`, nested worktree path, and `/tmp/foo`.
- `test_parse_since` and `test_iso_from_ms` match ZCode behavior.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_opencode_export.py -q`
Expected: FAIL because `llmwiki.opencode.export` does not exist.

- [ ] **Step 3: Implement `llmwiki/opencode/export.py`**

Implement the interfaces above. Keep the exporter self-contained; do not import private ZCode helpers. Record mapping:

- `init`: `{"type":"init","sessionId":..., "cwd":directory, "timestamp":iso_from_ms(time_created), "title":title}`
- user: `{"type":"user","timestamp":..., "sessionId":..., "cwd":..., "message":{"role":"user","content":text + file placeholders}}`
- assistant: blocks for `text` and `tool`; skip `reasoning`; tool block `{"type":"tool_use","id":..., "name":..., "input": state.input}`
- tool result: `{"type":"user", ..., "message":{"role":"user","content":[{"type":"tool_result","tool_use_id":..., "content":..., "is_error": status == "error"}]}}`
- skip `synthetic`, `shell`, `system`, `skill`, `idle`, `compaction`, `agent-switched`, `model-switched`, `location-switched`.

`session_v2.directory` fallback: if empty, look up `project.worktree` by `project_id`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_opencode_export.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add llmwiki/opencode/__init__.py llmwiki/opencode/export.py tests/test_opencode_export.py
git commit -m "feat: export OpenCode sessions to llmwiki JSONL"
```

---

### Task 2: Promote the OpenCode adapter

**Files:**
- Move: `llmwiki/adapters/contrib/opencode.py` → `llmwiki/adapters/opencode.py`
- Modify: `llmwiki/adapters/__init__.py`
- Modify: `tests/test_opencode_adapter.py`
- Test: `tests/test_opencode_adapter.py`

**Interfaces:**
- Consumes: exported corpus at `~/.llmwiki/opencode/<slug>/*.jsonl`.
- Produces: `OpenCodeAdapter` registered under `"opencode"` and loaded by `discover_adapters()`.
- `DEFAULT_ROOTS` includes `~/.llmwiki/opencode`, `~/.config/opencode/sessions`, `~/.config/openclaw/sessions`, plus macOS/Windows legacy paths.

- [ ] **Step 1: Write failing adapter tests**

Update `tests/test_opencode_adapter.py`:
- import `from llmwiki.adapters.opencode import OpenCodeAdapter`
- assert `llmwiki.adapters.opencode` import works
- assert `OpenCodeAdapter.DEFAULT_ROOTS[0] == Path.home() / ".llmwiki" / "opencode"`
- assert `discover_adapters()` registers `"opencode"` without calling `discover_all()`
- assert `derive_project_slug` returns the parent directory for `<store>/<slug>/<session>.jsonl`
- assert `is_available()` is false for an existing but empty export dir, true after a `.jsonl` appears.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_opencode_adapter.py -q`
Expected: FAIL on import path or missing core registration.

- [ ] **Step 3: Move and update the adapter**

Use `git mv`; remove `opencode` from `CONTRIB_ADAPTERS`; add `opencode` to `discover_adapters()` imports. Add the export root first. Override `is_available()` to require at least one `.jsonl`, like `ZcodeAdapter`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_opencode_adapter.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add llmwiki/adapters/opencode.py llmwiki/adapters/__init__.py tests/test_opencode_adapter.py
git commit -m "feat: promote OpenCode adapter to core"
```

---

### Task 3: Autosync and CLI commands

**Files:**
- Create: `llmwiki/opencode/autosync.py`
- Modify: `llmwiki/cli.py`
- Test: `tests/test_opencode_autosync.py`
- Test: `tests/test_opencode_cli.py`

**Interfaces:**
- `run_autosync(*, db_path: Path | None = None, out_dir: Path | None = None, project: str | None = None, build: bool = False, lock_timeout: float = 600, lock_path: Path | None = None, log_path: Path | None = None, quiet: bool = False, repo_root: Path | None = None) -> int`
- CLI: `llmwiki opencode export`, `llmwiki opencode autosync`, `llmwiki opencode install`.
- `cmd_sync` gains `--opencode-export/--no-opencode-export` default True and `--opencode-db PATH`; `_maybe_export_opencode(args)` runs before `convert_all`.

- [ ] **Step 1: Write failing autosync/CLI tests**

`tests/test_opencode_autosync.py`:
- monkeypatch `run_export`, `convert_all`, `build_site`; assert `run_autosync(build=True)` calls export, `convert_all(adapters=["opencode"], include_current=True, project=None)`, then build.
- acquire the same `FileLock` first; assert second run returns 0 and logs `skip`.
- assert `--quiet` writes only to the log.

`tests/test_opencode_cli.py`:
- `cli.build_parser().parse_args(["opencode", "export", "--dry-run"])` sets `opencode_command == "export"`.
- `parse_args(["opencode", "autosync", "--build", "--quiet"])` sets flags.
- `parse_args(["opencode", "install", "--dry-run"])` sets flags.
- `_maybe_export_opencode` returns without calling `run_export` when `args.opencode_export` is false or `--adapter` excludes `opencode`.
- `_maybe_export_opencode` calls `run_export` with `dry_run`, `since`, and `project` when the DB exists.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_opencode_autosync.py tests/test_opencode_cli.py -q`
Expected: FAIL because the module and CLI commands do not exist.

- [ ] **Step 3: Implement `autosync.py` and CLI wiring**

Mirror `llmwiki/zcode/autosync.py`: acquire `FileLock` at `~/.llmwiki/opencode/autosync.lock`, log to `~/.llmwiki/opencode/autosync.log`, call `run_export`, `convert_all(adapters=["opencode"], project=project, include_current=True)`, optional `build_site`.

Add `_maybe_export_opencode(args)` to `cli.py`; skip when the DB is absent; print a one-line summary; warn and continue on failure. Add `--opencode-export/--no-opencode-export` and `--opencode-db` to the sync parser and call `_maybe_export_opencode(args)` before `_maybe_export_zcode(args)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_opencode_autosync.py tests/test_opencode_cli.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add llmwiki/opencode/autosync.py llmwiki/cli.py tests/test_opencode_autosync.py tests/test_opencode_cli.py
git commit -m "feat: add OpenCode autosync and CLI commands"
```

---

### Task 4: OpenCode plugin installer

**Files:**
- Create: `llmwiki/opencode/install.py`
- Modify: `llmwiki/cli.py` (install command handler)
- Test: `tests/test_opencode_install.py`

**Interfaces:**
- `install_plugin(*, home: Path | None = None, python_exe: Path | str | None = None, repo_root: Path | None = None, build: bool = False, dry_run: bool = False, force: bool = False) -> list[str]`
- `plugin_path(home: Path | None = None) -> Path`
- Produces `~/.config/opencode/plugins/llmwiki-autosync/index.ts`.

- [ ] **Step 1: Write failing installer tests**

- `install_plugin(home=tmp_path, python_exe="/venv/bin/python", repo_root=tmp_path)` creates `index.ts`.
- Content contains `session.idle`, `llmwiki opencode autosync`, the interpreter path, and the repo root.
- Second call without `force` returns an "already installed" message and does not change the file mtime.
- `force=True` overwrites.
- `dry_run=True` writes nothing.
- Paths with spaces are embedded safely (assert the JSON-escaped string appears).
- CLI parser accepts `opencode install --dry-run --force --no-build`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_opencode_install.py -q`
Expected: FAIL because the module does not exist.

- [ ] **Step 3: Implement plugin template and installer**

The template must use the V2 API:

```ts
import { Plugin } from "@opencode/plugin"
import { spawn } from "node:child_process"

export default Plugin.define({
  id: "llmwiki.autosync",
  async setup(ctx) {
    const controller = new AbortController()
    void (async () => {
      for await (const event of ctx.event.subscribe({ signal: controller.signal })) {
        if (event.type !== "session.idle") continue
        const child = spawn(PYTHON, ["-m", "llmwiki", "opencode", "autosync", "--quiet", ...BUILD_ARGS], {
          cwd: REPO_ROOT,
          detached: true,
          stdio: "ignore",
        })
        child.unref()
      }
    })().catch(() => {})
    return () => controller.abort()
  },
})
```

Use `json.dumps()` for the Python path and repo root strings so spaces/quotes are safe. Do not add a scheduler in this task.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_opencode_install.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add llmwiki/opencode/install.py llmwiki/cli.py tests/test_opencode_install.py
git commit -m "feat: install OpenCode autosync plugin"
```

---

### Task 5: Sync integration, docs, full verification

**Files:**
- Modify: `examples/sessions_config.json`
- Modify: `README.md`
- Modify: `docs/adapters/opencode.md`
- Modify: `docs/reference/cli.md`
- Modify: `CHANGELOG.md`
- Test: full suite

- [ ] **Step 1: Add config and docs**

Add `"opencode": {"enabled": true}` with a comment next to `zcode`. Update the README “Works with” row from `⏸ Deferred` to `✅ Production` and describe `llmwiki opencode export|autosync|install`. Update `docs/adapters/opencode.md` with the V2 SQLite path, exported corpus, plugin path, and event name. Add CLI examples to `docs/reference/cli.md`. Add a CHANGELOG entry.

- [ ] **Step 2: Run the full test suite**

Run: `pytest -q`
Expected: PASS with no failures. If a pre-existing failure appears, report it by name; do not hide it.

- [ ] **Step 3: Run an end-to-end smoke test against the real OpenCode DB**

```bash
.venv/bin/python -m llmwiki opencode export --db ~/.local/share/opencode/opencode.db --out-dir /tmp/llmwiki-opencode-smoke --dry-run
.venv/bin/python -m llmwiki opencode export --db ~/.local/share/opencode/opencode.db --out-dir /tmp/llmwiki-opencode-smoke
test -n "$(find /tmp/llmwiki-opencode-smoke -name '*.jsonl' | head -1)"
```

Expected: `exported` is non-zero and at least one JSONL file exists. Do not commit the smoke output.

- [ ] **Step 4: Commit**

```bash
git add examples/sessions_config.json README.md docs/adapters/opencode.md docs/reference/cli.md CHANGELOG.md
git commit -m "docs: document OpenCode autosync integration"
```

- [ ] **Step 5: Report**

Report the branch, commit hashes, test summary, smoke-test result, and any pre-existing failures. Do not push unless asked.

---

## Self-Review

- Spec coverage: exporter, adapter, autosync, plugin installer, CLI, docs — all covered.
- Type consistency: `session_to_records` and `run_export` signatures match the adapter and autosync callers.
- Review focus: schema drift, tool-only turns, stale pruning, lock contention, path escaping, event compatibility each have a test.
- Proportion: implementation is split into five independently testable commits; no task contains a full transcript of production code.
