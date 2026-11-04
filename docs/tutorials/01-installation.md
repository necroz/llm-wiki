---
title: "01 · Installation"
type: tutorial
docs_shell: true
---

# 01 · Installation

**Time:** 5 minutes
**You'll need:** Python 3.9+, `git`, and at least one AI-coding agent already installed with session history on disk.
**Result:** A working `llmwiki` CLI in `.venv` (run `.venv/bin/llmwiki` or activate the venv), plus wrapper scripts that work without activation.

---

## Why this matters

llmwiki runs **locally**. Every session transcript stays on your machine. No
telemetry, no account, no network calls at build time. The install is
deliberately boring: clone, run the setup script, done.

---

## Step 1 — Check your toolchain

```bash
python3 --version          # expect 3.9 or newer
git --version
```

macOS and most Linux distros already ship both. Windows: install Python from
[python.org](https://python.org) and git from [git-scm.com](https://git-scm.com).

## Step 2 — Clone the repo

```bash
git clone https://github.com/Pratiyush/llm-wiki.git
cd llm-wiki
```

## Step 3 — Run the setup script

### macOS / Linux

```bash
./setup.sh
```

### Windows

```cmd
setup.bat
```

The setup script is idempotent. Running it twice is safe.

It will:

- Create a checkout-local `.venv` (so the system Python and PEP 668 do not matter)
- Install the package into that venv (editable, so source edits apply)
- Create `raw/`, `wiki/`, `site/` directories if they don't exist
- Seed `wiki/index.md`, `wiki/overview.md`, `wiki/log.md`, `wiki/CRITICAL_FACTS.md`
- Run `llmwiki doctor` to show which adapters and ZCode paths are visible

Expected output ends with:

```
================================================================
  Setup complete.
================================================================
```

The wrapper scripts (`sync.sh`, `build.sh`, `serve.sh`) use
`.venv/bin/python` automatically; you do not need to activate the venv
in a new shell.

## Step 4 — (Optional) Install via PyPI instead

Once [#246](https://github.com/Pratiyush/llm-wiki/issues/246) is set up:

```bash
pip install llm-notebook
llmwiki --version
```

Or via Homebrew, once [#247](https://github.com/Pratiyush/llm-wiki/issues/247) is set up:

```bash
brew install Pratiyush/tap/llmwiki
```

Until then the clone-and-run path above is authoritative.

## Step 5 — (Optional) Install via Docker

Zero-touch, no Python on your machine:

```bash
docker run -p 8765:8765 -v $PWD/wiki:/wiki ghcr.io/pratiyush/llm-wiki:latest
```

See [deploy/docker.md](../deploy/docker.md) for the full Compose setup.

---

## Verify

```bash
.venv/bin/python -m llmwiki --version  # → llmwiki <version>
.venv/bin/python -m llmwiki adapters   # lists every adapter and whether it's configured
.venv/bin/python -m llmwiki doctor     # Python, markdown, venv, config, ZCode paths
```

The `adapters` output tells you which agents have session stores on this
machine — your first sync pulls from every one marked `active`.

---

## Troubleshooting

**`command not found: python3`** — install Python from python.org and re-open your terminal.

**`setup.sh: permission denied`** — the shell scripts are executable in git; if your checkout lost the bit, run `chmod +x setup.sh` once.

**`ModuleNotFoundError: No module named 'markdown'`** — run `./setup.sh` again, or `.venv/bin/python -m pip install -e .`.

**`externally-managed-environment` (PEP 668)** — do not use `pip install --user`; `./setup.sh` creates a local `.venv` and installs there.

**`ImportError` on Python 3.8 or older** — llmwiki requires ≥ 3.9. Upgrade Python; on macOS use `brew install python@3.12`.

---

## Next

→ **[02 · First sync](02-first-sync.md)** — point llmwiki at your session history and build the site.
