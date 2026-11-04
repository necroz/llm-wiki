# Windows setup

llmwiki ships Windows `.bat` files alongside the Unix shell scripts. Most things just work. This doc covers the Windows-specific gotchas.

## Prerequisites

- **Python ≥ 3.9** — install from [python.org](https://www.python.org/downloads/windows/) or the Microsoft Store. Make sure "Add Python to PATH" is checked.
- **Git** — install from [git-scm.com](https://git-scm.com/download/win).
- **Claude Code for Windows** — optional but recommended.

## Install

Open a fresh **Command Prompt** or **PowerShell** window after installing Python (to pick up the new PATH).

```cmd
git clone https://github.com/Pratiyush/llm-wiki.git
cd llm-wiki
setup.bat
```

If `setup.bat` says "python is required but was not found", close and re-open your terminal. If that doesn't fix it, your Python install didn't set PATH — reinstall with the PATH checkbox enabled.

`setup.bat` creates a checkout-local `.venv` and installs the package
into it, so it does not depend on global site-packages. The wrapper
scripts use that interpreter automatically.

## Running commands

Same as macOS / Linux, but with `.bat`:

```cmd
sync.bat
build.bat
serve.bat
```

Or run the Python module directly through the venv:

```cmd
.venv\Scripts\python.exe -m llmwiki sync
.venv\Scripts\python.exe -m llmwiki build
.venv\Scripts\python.exe -m llmwiki serve
.venv\Scripts\python.exe -m llmwiki doctor
```

Note: on Windows the command is `python`, not `python3`. The `.bat` files
prefer `.venv\Scripts\python.exe` and fall back to `python` on PATH.

## Paths

Windows paths use backslashes (`C:\Users\...`) but Claude Code on Windows stores sessions at a Unix-looking path:

```
C:\Users\<you>\.claude\projects\<project>\<uuid>.jsonl
```

llmwiki handles this automatically. You don't need to do anything.

## Redaction of Windows paths

The default redaction config covers `/Users/<you>/` and `/home/<you>/` (Unix), but not `C:\Users\<you>\`. Add this to your gitignored `config.local.json` (or `config.json`):

```jsonc
{
  "redaction": {
    "real_username": "<YOUR_WINDOWS_USERNAME>",
    "extra_patterns": [
      "C:\\\\Users\\\\<YOUR_WINDOWS_USERNAME>\\\\[^\\\"]*",
      // ... (keep the defaults too)
    ]
  }
}
```

Replace `<YOUR_WINDOWS_USERNAME>` with your actual Windows username. Note the quadruple backslashes — JSON string escaping + regex escaping.

Every CLI command merges `examples/sessions_config.json` → `config.json`
→ `config.local.json`, so `llmwiki sync` applies these patterns to new
conversions. The `extra_patterns` list replaces the default list, so copy
the default patterns into your override if you want to keep both.

## Line endings

Git on Windows defaults to converting line endings (`core.autocrlf=true`). This is usually fine, but if you ever see weird markdown rendering, check with:

```cmd
git config core.autocrlf
```

Recommended setting for this repo:

```cmd
git config core.autocrlf input
```

## PowerShell execution policy

If PowerShell complains "cannot be loaded because running scripts is disabled on this system" when you try to run `setup.bat`, run this once as Administrator:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

## Opening the browser

`serve.bat --open` will try to open your default browser automatically. If that doesn't work:

1. The server is running at http://127.0.0.1:8765
2. Just paste that into your browser

## Known limitations

- **Claude Code SessionStart hook is still manual.** Add it to `%USERPROFILE%\.claude\settings.json` yourself.
- **ZCode autosync is supported.** Run `.venv\Scripts\python.exe -m llmwiki zcode install --scheduler windows`. It writes `%USERPROFILE%\.llmwiki\zcode\autosync.cmd`, adds the Stop hook, and prints the `schtasks` command to register the daily task. No bash or `flock` is required.
- **No GPG signing setup.** llmwiki doesn't require signed commits for contributions, but if you want them on Windows, install Git for Windows with the GPG support option.
- **Emoji in terminal output** may render as boxes. This is a Windows terminal font issue — everything still works, it just looks odd.

## Getting help

If you hit a Windows-specific issue, open a [bug report](https://github.com/Pratiyush/llm-wiki/issues/new?template=bug_report.md) with the `windows` label.
