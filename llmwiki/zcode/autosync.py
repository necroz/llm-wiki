"""Portable ZCode autosync: export JSONL, convert, optionally build.

This replaces the bash ``flock`` hook. The same command works from:

* a ZCode Stop-hook (Linux, macOS, Windows),
* a systemd user timer,
* a launchd agent,
* Windows Task Scheduler,
* a manual shell.

The lock is OS-level (:mod:`llmwiki.lock`), so a crashed run cannot leave
a stale lock behind.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from llmwiki import REPO_ROOT
from llmwiki.convert import convert_all
from llmwiki.lock import FileLock
from llmwiki.zcode.export import DEFAULT_DB, DEFAULT_OUT_DIR, run_export


def _default_log_path() -> Path:
    return Path.home() / ".llmwiki" / "zcode" / "autosync.log"


def _log(message: str, *, log_path: Path, quiet: bool) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    line = f"{stamp} {message}"
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    if not quiet:
        print(line)


def run_autosync(
    *,
    db_path: Optional[Path] = None,
    out_dir: Optional[Path] = None,
    project: Optional[str] = None,
    build: bool = False,
    coalesce: bool = False,
    lock_timeout: float = 600,
    lock_path: Optional[Path] = None,
    log_path: Optional[Path] = None,
    quiet: bool = False,
    repo_root: Optional[Path] = None,
) -> int:
    """Run one autosync cycle. Returns 0 on success or when the lock is busy."""
    db = Path(db_path).expanduser() if db_path is not None else DEFAULT_DB
    out = Path(out_dir).expanduser() if out_dir is not None else DEFAULT_OUT_DIR
    log = Path(log_path) if log_path is not None else _default_log_path()
    lock_file = Path(lock_path) if lock_path is not None else log.parent / "autosync.lock"
    root = Path(repo_root) if repo_root is not None else Path(REPO_ROOT)

    lock = FileLock(lock_file, timeout=lock_timeout)
    if not lock.acquire():
        _log("skip: another autosync is still running", log_path=log, quiet=quiet)
        return 0

    try:
        _log(
            f"start project={project or '<all>'} build={build} coalesce={coalesce}",
            log_path=log,
            quiet=quiet,
        )
        try:
            from llmwiki.config import resolve_config

            zcode_cfg = resolve_config(root).get("zcode") or {}
            roots = (
                zcode_cfg.get("project_roots")
                if isinstance(zcode_cfg, dict)
                and isinstance(zcode_cfg.get("project_roots"), list)
                else None
            )
        except Exception:
            roots = None
        try:
            stats: dict[str, Any] = run_export(
                db, out, project=project, project_roots=roots
            )
        except Exception as exc:  # noqa: BLE001 - report, do not traceback
            _log(f"export failed: {exc}", log_path=log, quiet=quiet)
            return 1

        _log(
            "export: "
            f"{stats.get('exported', 0)} exported, "
            f"{stats.get('unchanged', 0)} unchanged, "
            f"{stats.get('errors', 0)} error(s)",
            log_path=log,
            quiet=quiet,
        )
        if stats.get("errors"):
            _log(
                f"warning: export finished with {stats['errors']} error(s)",
                log_path=log,
                quiet=quiet,
            )

        rc = convert_all(
            adapters=["zcode"], project=project, include_current=True
        )
        if rc != 0:
            _log("sync failed", log_path=log, quiet=quiet)
            return 1

        if coalesce:
            from llmwiki.config import resolve_config
            from llmwiki.synth.pipeline import synthesize_new_sessions

            synth_cfg = resolve_config(root).get("synthesis") or {}
            raw_patterns = (
                synth_cfg.get("lock_skip_patterns")
                if isinstance(synth_cfg, dict)
                else None
            )
            patterns = (
                [str(pattern) for pattern in raw_patterns]
                if isinstance(raw_patterns, list)
                else None
            )
            summary = synthesize_new_sessions(
                coalesce_only=True,
                coalesce_low_signal=True,
                coalesce_min_group=int(
                    (synth_cfg.get("coalesce_min_group") if isinstance(synth_cfg, dict) else 3)
                    or 3
                ),
                coalesce_window=str(
                    (synth_cfg.get("coalesce_window") if isinstance(synth_cfg, dict) else "all")
                    or "all"
                ),
                lock_skip_patterns=patterns,
            )
            _log(
                f"coalesced: {summary.get('coalesced', 0)} cluster page(s)",
                log_path=log,
                quiet=quiet,
            )

        if build:
            from llmwiki.build import build_site

            _log("build: regenerating site/", log_path=log, quiet=quiet)
            build_site(out_dir=root / "site", seed_project_stubs=True)

        _log("done", log_path=log, quiet=quiet)
        return 0
    except Exception as exc:  # noqa: BLE001 - a scheduler must get one clean line
        _log(f"failed: {exc}", log_path=log, quiet=quiet)
        return 1
    finally:
        lock.release()