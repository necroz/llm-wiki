"""Synthesis pipeline — orchestrates auto-ingest from raw → wiki (v0.5 · #36).

The main entry point is `synthesize_new_sessions()` which:

1. Scans `raw/sessions/` for markdown files
2. Compares against an mtime state file to find NEW files since the last run
3. For each new file, calls the configured synthesizer backend to produce
   a wiki source page
4. Writes `wiki/sources/<slug>.md` with proper frontmatter
5. Updates the mtime state file so re-runs are a no-op
6. Appends to `wiki/log.md`

Idempotency: the pipeline uses `.llmwiki-synth-state.json` (same pattern
as the converter's `.llmwiki-state.json`) to track which files have been
synthesized. Re-running on an unchanged tree is a sub-second no-op.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from llmwiki import REPO_ROOT
# #py-m1 (#587) / #arch-h5 (#610): import directly from _frontmatter
# instead of via build.py. The build module pulls in 145+ transitive
# imports; the parser sits cleanly in _frontmatter.py with no deps.
from llmwiki._frontmatter import parse_frontmatter
from llmwiki.synth.base import BaseSynthesizer, DummySynthesizer


# G-21 (#307): shell- and URL-unsafe chars we scrub from slugs at
# synthesize-time. Spaces → hyphens; filesystem-reserved + Windows-
# unsafe chars → hyphens; collapse repeats.
_SLUG_UNSAFE = re.compile(r'[\s/\\:*?"<>|]+')
_SLUG_DASH_RUN = re.compile(r"-{2,}")


def _normalise_slug(raw: str) -> str:
    """Return a URL-safe + shell-safe slug. Preserves case + unicode.

    Examples:
      ``"00 - Master Framework Index"`` → ``"00-Master-Framework-Index"``
      ``"path/with/slashes"``            → ``"path-with-slashes"``
      ``"weird:chars<here>"``            → ``"weird-chars-here"``
    """
    if not raw:
        return "unknown"
    cleaned = _SLUG_UNSAFE.sub("-", raw)
    # Collapse runs of consecutive dashes so "00 - X" doesn't become
    # "00---X" — consecutive hyphens are ugly in URLs and filesystems.
    cleaned = _SLUG_DASH_RUN.sub("-", cleaned).strip("-")
    return cleaned or "unknown"


def resolve_backend(
    cfg: Optional[dict[str, Any]] = None,
) -> BaseSynthesizer:
    """Pick a synthesizer backend from ``cfg["synthesis"]["backend"]``.

    Supported values:
      - ``"dummy"`` (default) — canned offline backend for previews/tests
      - ``"ollama"`` — local Ollama HTTP backend (#35)
      - ``"openai"`` — any OpenAI-compatible ``/v1`` server (vLLM,
        SGLang, LM Studio, LiteLLM, hosted providers)
      - ``"agent"`` — defer to the running Claude Code / Codex CLI
        agent (#316). Writes pending prompts to
        ``.llmwiki-pending-prompts/`` for the slash-command layer to
        pick up on the next agent turn.  No HTTP, no API key.

    Unknown values fall back to the dummy backend with a warning so a
    typo in config.json doesn't crash sync.
    """
    synth_cfg = (cfg or {}).get("synthesis", {}) or {}
    name = (synth_cfg.get("backend") or "dummy").strip().lower()

    if name == "ollama":
        # Imported lazily so the `urllib`-based module isn't loaded when
        # users stick with the default dummy backend.
        from llmwiki.synth.ollama import OllamaSynthesizer, load_ollama_config

        return OllamaSynthesizer(config=load_ollama_config(cfg))

    if name in {"openai", "openai-compatible", "openai_compat", "vllm"}:
        from llmwiki.synth.openai_compat import (
            OpenAICompatSynthesizer,
            load_openai_config,
        )

        return OpenAICompatSynthesizer(config=load_openai_config(cfg))

    if name in {"agent", "agent_delegate", "agent-delegate"}:
        # Imported lazily — the agent backend is a thin file-I/O layer
        # but we keep symmetry with the other backends' lazy import
        # pattern so ``import llmwiki.synth.pipeline`` stays cheap.
        from llmwiki.synth.agent_delegate import AgentDelegateSynthesizer

        return AgentDelegateSynthesizer()

    if name != "dummy":
        import logging
        logging.getLogger(__name__).warning(
            "Unknown synthesis.backend %r — falling back to dummy", name
        )
    return DummySynthesizer()

RAW_SESSIONS = REPO_ROOT / "raw" / "sessions"
WIKI_SOURCES = REPO_ROOT / "wiki" / "sources"
WIKI_LOG = REPO_ROOT / "wiki" / "log.md"
STATE_FILE = REPO_ROOT / ".llmwiki-synth-state.json"
PROMPT_TEMPLATE_PATH = Path(__file__).parent / "prompts" / "source_page.md"

# Allow user override of the prompt template: if
# `wiki/prompts/source_page.md` exists, use it instead of the
# built-in one. This lets users customize the synthesis prompt
# without forking the codebase.
USER_PROMPT_OVERRIDE = REPO_ROOT / "wiki" / "prompts" / "source_page.md"


def _load_prompt_template() -> str:
    """Load the synthesis prompt template. User override wins."""
    if USER_PROMPT_OVERRIDE.is_file():
        return USER_PROMPT_OVERRIDE.read_text(encoding="utf-8")
    return PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8")


def _resolve_state_file(state_file: Optional[Path] = None) -> Path:
    """Return the synth state-file path.

    #420: when running in vault-overlay mode, the state file must live
    *under the vault root*, not the repo root — otherwise two different
    vaults synthesised against the same repo silently share idempotency
    state and one vault's run marks the other vault's files unchanged.
    Callers pass ``state_file`` explicitly when in vault mode; default
    falls back to the repo-root location for the no-vault case.
    """
    return state_file if state_file is not None else STATE_FILE


def _load_state(state_file: Optional[Path] = None) -> dict[str, float]:
    """Load the mtime state file. Returns {relative_path: mtime}.

    #sec-16 (#560): validate the schema before trusting it. A
    corrupted or hand-edited state file used to be returned verbatim,
    which then crashed every downstream consumer that expected
    `{str: float}`. Now: must be a dict, every value must be int/float,
    every key must be str. Anything else → reset to empty so synthesis
    re-runs from scratch (worst case: extra work, never wrong work).
    """
    target = _resolve_state_file(state_file)
    if not target.is_file():
        return {}
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except ValueError:
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, float] = {}
    for k, v in raw.items():
        if not isinstance(k, str):
            continue
        if isinstance(v, (int, float)):
            out[k] = float(v)
        # Other shapes silently dropped — caller treats as "needs synth"
    return out


def _save_state(state: dict[str, float], state_file: Optional[Path] = None) -> None:
    target = _resolve_state_file(state_file)
    target.write_text(
        json.dumps(state, indent=2, sort_keys=True), encoding="utf-8"
    )


def _append_log(
    title: str,
    *,
    log_path: Optional[Path] = None,
    operation: str = "synthesize",
    details: Optional[dict[str, Any]] = None,
) -> None:
    """Append a rich structured entry to wiki/log.md.

    Parameters
    ----------
    title : str
        Human-readable title for the log entry (e.g. "project/slug").
    log_path : Path, optional
        Override for the log file path — used by tests to avoid writing
        to the real wiki/log.md.  Defaults to ``WIKI_LOG``.
    operation : str
        Operation type: synthesize, ingest, query, lint, build, sync.
    details : dict, optional
        Rich details — created pages, updated pages, entities extracted, etc.
    """
    target = log_path or WIKI_LOG
    if not target.parent.exists():
        return

    # Auto-archive when log exceeds 50 KB
    _auto_archive_log(target)

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [f"\n## [{date_str}] {operation} | {title}\n"]
    if details:
        if details.get("processed"):
            lines.append(f"- Processed: {details['processed']}\n")
        if details.get("created"):
            lines.append(f"- Created: {', '.join(details['created'])}\n")
        if details.get("updated"):
            lines.append(f"- Updated: {', '.join(details['updated'])}\n")
        if details.get("entities"):
            lines.append(f"- Entities extracted: {', '.join(details['entities'])}\n")
        if details.get("errors"):
            lines.append(f"- Errors: {len(details['errors'])}\n")
    with open(target, "a", encoding="utf-8") as f:
        f.writelines(lines)


LOG_ARCHIVE_THRESHOLD = 50 * 1024  # 50 KB


def _auto_archive_log(log_path: Path) -> Optional[Path]:
    """Archive log.md when it exceeds 50 KB. Returns archive path or None."""
    if not log_path.is_file():
        return None
    if log_path.stat().st_size < LOG_ARCHIVE_THRESHOLD:
        return None

    year = datetime.now(timezone.utc).strftime("%Y")
    archive = log_path.parent / f"log-archive-{year}.md"

    content = log_path.read_text(encoding="utf-8")
    # Keep the header (first 5 lines), archive the rest
    lines = content.split("\n")
    header = "\n".join(lines[:5])
    body = "\n".join(lines[5:])

    # G-10 (#296): seed frontmatter on first write so lint's
    # frontmatter_completeness rule doesn't fail on the archive file.
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    first_write = not archive.is_file()
    if first_write:
        archive.write_text(
            f'---\ntitle: "Wiki log archive — {year}"\n'
            f'type: navigation\nauto_generated: true\n'
            f'last_updated: "{today}"\n---\n',
            encoding="utf-8",
        )

    # Append to archive
    with open(archive, "a", encoding="utf-8") as f:
        f.write(f"\n# Archived from log.md — {year}\n\n")
        f.write(body)

    # Reset log to header only
    log_path.write_text(header + "\n\n---\n", encoding="utf-8")
    return archive


_SOURCES_HEADING = re.compile(r"^##\s+Sources(?:\s*\(\d+\))?\s*$", re.MULTILINE)
_NEXT_H2 = re.compile(r"^##\s+", re.MULTILINE)


def _rebuild_index(wiki_dir: Path) -> Optional[Path]:
    """Rewrite the ``## Sources`` section of ``wiki/index.md`` (G-09 · #295).

    Walks ``wiki_dir/sources/**/*.md`` and emits one bullet per source
    page, preserving every other section (Overview, Entities, Concepts,
    hand-curated text) untouched.  If ``wiki/index.md`` is missing the
    caller gets a freshly seeded index with just Sources in it.
    """
    index = wiki_dir / "index.md"
    sources_dir = wiki_dir / "sources"
    if not sources_dir.is_dir():
        return None

    # Collect (relpath, title, one-line-summary) for every source page.
    bullets: list[str] = []
    for p in sorted(sources_dir.rglob("*.md")):
        if p.name.startswith("_"):
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        meta, _ = parse_frontmatter(text)
        rel = p.relative_to(wiki_dir).as_posix()
        title = meta.get("title") or p.stem
        date = str(meta.get("date", "")).strip()
        project = meta.get("project", "")
        suffix_parts: list[str] = []
        if project:
            suffix_parts.append(str(project))
        if date:
            suffix_parts.append(date)
        suffix = f" — {' · '.join(suffix_parts)}" if suffix_parts else ""
        bullets.append(f"- [{title}]({rel}){suffix}")

    sources_block = f"## Sources ({len(bullets)})\n" + (
        "\n".join(bullets) if bullets else "*(none yet)*"
    ) + "\n"

    if index.is_file():
        original = index.read_text(encoding="utf-8")
    else:
        original = (
            "# Wiki Index\n\n"
            "This file is auto-maintained by synthesize. "
            "Update-in-place only inside this file — sections outside "
            "`## Sources` are preserved.\n\n"
            "## Sources\n*(placeholder)*\n"
        )

    # Older runs appended a second Sources block when the heading carried a
    # count and the regex missed it. Collapse duplicates before replacing.
    matches = list(_SOURCES_HEADING.finditer(original))
    if len(matches) > 1:
        kept: list[str] = []
        cursor = 0
        for dup in matches[1:]:
            kept.append(original[cursor:dup.start()])
            tail = original[dup.end():]
            next_match = _NEXT_H2.search(tail)
            cursor = dup.end() + next_match.start() if next_match else len(original)
        kept.append(original[cursor:])
        original = "".join(kept)

    match = _SOURCES_HEADING.search(original)
    if not match:
        # No Sources section yet — append one at the end.
        new_text = original.rstrip() + "\n\n" + sources_block
    else:
        start = match.start()
        # Find the next `## ` heading *after* the Sources heading.
        tail = original[match.end():]
        next_match = _NEXT_H2.search(tail)
        if next_match:
            end = match.end() + next_match.start()
        else:
            end = len(original)
        new_text = original[:start] + sources_block + "\n" + original[end:]

    # Only write when content changes — avoids bumping mtime needlessly.
    if not index.is_file() or index.read_text(encoding="utf-8") != new_text:
        index.write_text(new_text, encoding="utf-8")
    return index


def _discover_raw_sessions(
    raw_dir: Optional[Path] = None,
) -> list[tuple[Path, dict[str, Any], str]]:
    """Walk raw/sessions/ and return (path, meta, body) for each .md file."""
    root = raw_dir or RAW_SESSIONS
    if not root.is_dir():
        return []
    out: list[tuple[Path, dict[str, Any], str]] = []
    for p in sorted(root.rglob("*.md")):
        if p.name.startswith("_"):
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        meta, body = parse_frontmatter(text)
        out.append((p, meta, body))
    return out


# ─── low-signal coalescing ────────────────────────────────────────────
#
# Hourly autonomous workers leave behind two kinds of zero-value
# sessions: provider-limit failures (the prompt is stored, the agent
# never answers) and lock-contention skips (the worker exits because
# another run holds RUN.lock). Synthesizing one page per such launch
# floods the wiki with near-identical summaries. These helpers detect
# them and replace all member pages with one deterministic cluster page.

_TURN_MARKER = re.compile(r"^### Turn \d+ — (User|Assistant)\s*$", re.MULTILINE)
_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]*))?\]\]")

# Default phrases used by the autonomous worker this feature was built
# for. They are a fallback, not the contract: set
# ``synthesis.lock_skip_patterns`` to match another worker's wording,
# or to ``[]`` to disable lock-skip detection entirely.
DEFAULT_LOCK_SKIP_PATTERNS = (
    "run.lock существует",
    "завершаюсь сразу",
    "запуск пропущен",
)


def _strip_unresolved_wikilinks(wiki_dir: Path, sources_dir: Path) -> int:
    """Replace wikilinks whose target page does not exist with plain text.

    LLM-generated pages often reference entities that were never
    written. Leaving them makes ``link_integrity`` noisy, so the
    pipeline degrades them to plain text after synthesis. The alias is
    kept when present.
    """
    slugs = {p.stem for p in wiki_dir.rglob("*.md")}
    removed = 0
    for page in sorted(sources_dir.rglob("*.md")):
        if page.name.startswith("_"):
            continue
        try:
            text = page.read_text(encoding="utf-8")
        except OSError:
            continue
        match = re.match(r"^(---\n.*?\n---\n)(.*)$", text, re.S)
        head, body = (match.group(1), match.group(2)) if match else ("", text)

        def repl(link: re.Match[str]) -> str:
            nonlocal removed
            target = link.group(1).strip()
            alias = link.group(2)
            base = target.split("#", 1)[0].strip()
            if base in slugs:
                return link.group(0)
            removed += 1
            return alias.strip() if alias else target

        new_body = _WIKILINK_RE.sub(repl, body)
        if new_body != body:
            page.write_text(head + new_body, encoding="utf-8")
    return removed


def _split_turns(body: str) -> tuple[list[str], list[str]]:
    """Return (user_texts, assistant_texts) from a converted transcript."""
    parts = _TURN_MARKER.split(body)
    users: list[str] = []
    assistants: list[str] = []
    for i in range(1, len(parts) - 1, 2):
        target = users if parts[i] == "User" else assistants
        target.append(parts[i + 1])
    return users, assistants


def _output_tokens(meta: dict[str, Any]) -> int:
    """Read output-token count from either a dict or an inline-JSON string."""
    totals = meta.get("token_totals")
    if isinstance(totals, dict):
        return int(totals.get("output", 0) or 0)
    try:
        return int(json.loads(str(totals)).get("output", 0) or 0)
    except (ValueError, TypeError, json.JSONDecodeError):
        return 0


def low_signal_reason(
    meta: dict[str, Any],
    body: str,
    lock_skip_patterns: tuple[str, ...] | list[str] | None = None,
) -> str | None:
    """Classify a session that produced no work, or return None.

    Only two shapes qualify:

    * ``no-response`` — zero tool calls, zero output tokens, and no
      non-empty assistant turn. A provider quota/limit failure leaves
      exactly this shape.
    * ``lock-skip`` — the worker exited immediately because another run
      held a lock (at most three tool calls). The phrases are configurable
      through ``synthesis.lock_skip_patterns``; an empty list disables
      this rule.

    Anything with a commit, a non-empty answer, or more than three tool
    calls is deliberately excluded.
    """
    users, assistants = _split_turns(body)
    if not users:
        return None
    tools = int(meta.get("tool_calls") or 0)
    last = next((a.strip() for a in reversed(assistants) if a.strip()), "")
    if tools == 0 and not last and _output_tokens(meta) == 0:
        return "no-response"
    patterns = (
        DEFAULT_LOCK_SKIP_PATTERNS
        if lock_skip_patterns is None
        else tuple(lock_skip_patterns)
    )
    low = last.lower()
    if tools <= 3 and any(
        str(pattern).lower() in low for pattern in patterns if str(pattern).strip()
    ):
        return "lock-skip"
    return None


def _prompt_fingerprint(body: str) -> str:
    users, _ = _split_turns(body)
    if not users:
        return ""
    normalised = " ".join(users[0].split())
    return hashlib.md5(normalised.encode("utf-8")).hexdigest()[:8]


def _session_page_path(sources_out: Path, meta: dict[str, Any]) -> Path:
    """Mirror the per-session output path used by the main loop."""
    project = str(meta.get("project") or "unknown")
    slug = _normalise_slug(str(meta.get("slug") or "unknown"))
    date = str(meta.get("date") or "").strip()
    filename = f"{date}-{slug}" if date else slug
    return sources_out / project / f"{filename}.md"


def _cluster_filename(rows: list[dict[str, Any]], window: str) -> str:
    dates = sorted(str(r["date"]) for r in rows if r.get("date"))
    if window == "month" and dates:
        return f"{dates[-1][:7]}-noop-sessions.md"
    years = sorted({d[:4] for d in dates})
    if not years:
        # Sessions without a date still coalesce; use a stable prefix so
        # repeated runs keep the same page name.
        return "unknown-noop-sessions.md"
    prefix = years[0] if len(years) == 1 else f"{years[0]}-{years[-1]}"
    return f"{prefix}-noop-sessions.md"


def _quote_from(text: str, needle: str = "", limit: int = 220) -> str:
    """Return one representative line, optionally containing ``needle``."""
    for line in text.splitlines():
        clean = " ".join(line.split())
        if not clean:
            continue
        if not needle or needle.lower() in clean.lower():
            return clean[:limit]
    return ""


def _render_cluster_page(
    project: str,
    rows: list[dict[str, Any]],
    *,
    page_name: str,
    lock_skip_patterns: tuple[str, ...] | list[str] | None = None,
) -> str:
    """Render one deterministic page for a group of low-signal sessions."""
    rows = sorted(rows, key=lambda r: (str(r.get("date") or ""), r["slug"]))
    dates = [str(r["date"]) for r in rows if r.get("date")]
    date_range = f"{dates[0]}..{dates[-1]}" if dates else "unknown"
    reasons = sorted({str(r["reason"]) for r in rows})
    reason_counts = {
        reason: sum(1 for r in rows if r["reason"] == reason) for reason in reasons
    }
    prompts = {str(r["prompt"]) for r in rows if r.get("prompt")}
    reasons_joined = ", ".join(reasons)
    noun = "session" if len(rows) == 1 else "sessions"
    today = datetime.now(timezone.utc).date().isoformat()

    tags: list[str] = ["zcode", "session-transcript", project, "noop"]
    for reason in reasons:
        if reason not in tags:
            tags.append(reason)

    if reason_counts.get("no-response") and reason_counts.get("lock-skip"):
        why = (
            f"{reason_counts['no-response']} had no assistant response "
            f"(zero tool calls, zero output tokens), and "
            f"{reason_counts['lock-skip']} exited immediately because "
            f"`RUN.lock` was held by another run."
        )
    elif reason_counts.get("no-response"):
        why = "all had no assistant response (zero tool calls, zero output tokens)."
    else:
        why = "all exited immediately because `RUN.lock` was held by another run."

    lines = [
        "---",
        f'title: "{project} — no-op sessions ({date_range})"',
        "type: source",
        f"tags: [{', '.join(tags)}]",
        f"date: {dates[-1] if dates else today}",
        f"project: {project}",
        f'date_range: "{date_range}"',
        f"merged_sessions: {len(rows)}",
        f'merge_reasons: "{reasons_joined}"',
        f"last_updated: {dates[-1] if dates else today}",
        "---",
        "<!-- Auto-generated cluster page. Raw transcripts stay in raw/sessions/. -->",
        "",
        "## Summary",
        "",
        f"This page coalesces {len(rows)} {noun} that produced no work. "
        f"They span {date_range} and {why} No commits, `PROGRESS.md` "
        "updates, or journal entries were produced by these runs.",
        "",
        "## Key Claims",
        "",
        f"- {len(rows)} low-signal {noun} across {len(prompts)} distinct prompt fingerprint(s).",
        "- Every member session has either no assistant turn and zero output tokens, "
        "or an immediate lock-skip exit.",
        "- No repository changes were recorded; these runs did not advance the mission.",
        "- Raw transcripts remain under `raw/sessions/`; each table row keeps its session ID.",
        "",
        "## Sessions",
        "",
        "| Date | Session | Reason | Prompt | Tools | Output tokens |",
        "|---|---|---|---|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row.get('date') or ''} | `{row['slug']}` | {row['reason']} | "
            f"`{row.get('prompt') or ''}` | {row.get('tools', 0)} | {row.get('output', 0)} |"
        )
    lines += ["", "## Key Quotes", ""]
    first_prompt = _quote_from(_split_turns(rows[0]["body"])[0][0]) if rows[0]["body"] else ""
    if first_prompt:
        lines.append(f'> "{first_prompt}" — representative worker prompt.')
    lock_quote = ""
    patterns = (
        DEFAULT_LOCK_SKIP_PATTERNS
        if lock_skip_patterns is None
        else tuple(lock_skip_patterns)
    )
    for row in rows:
        if row["reason"] != "lock-skip":
            continue
        assistants = _split_turns(row["body"])[1]
        last = next((a for a in reversed(assistants) if a.strip()), "")
        for pattern in patterns:
            lock_quote = _quote_from(last, needle=str(pattern))
            if lock_quote:
                break
        if lock_quote:
            break
    if lock_quote:
        lines.append(f'> "{lock_quote}" — why a lock-skip run exited.')
    lines += [
        "",
        "## Connections",
        "",
        f"- [[{project}]] — parent project.",
        "",
        "## Contradictions",
        "",
        "- None recorded.",
        "",
    ]
    return "\n".join(lines)


def _cluster_manifest_path(sources_out: Path, project: str) -> Path:
    return sources_out / project / "_clusters.json"


def _load_cluster_manifest(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


_SESSIONS_START = "<!-- sessions:auto:start -->"
_SESSIONS_END = "<!-- sessions:auto:end -->"

# Wikilinks that may appear inside a source-page summary. They cannot
# stay nested inside the ``[[stem|label]]`` link we generate below.
_WIKILINK_IN_TEXT_RE = re.compile(r"!?\[\[([^\]|]*)(?:\|([^\]]*))?\]\]")


def _plain_wikilink_text(text: str) -> str:
    """Flatten wikilinks to their display text and drop link delimiters."""

    def replace(match: re.Match[str]) -> str:
        target, alias = match.group(1), match.group(2)
        return (alias or target).strip()

    text = _WIKILINK_IN_TEXT_RE.sub(replace, text)
    for token in ("|", "[[", "]]"):
        text = text.replace(token, " ")
    return " ".join(text.split())


def _short_summary(text: str, limit: int = 140) -> str:
    """Return a one-sentence summary cut at a word boundary."""

    if len(text) <= limit:
        return text
    cut = text[:limit]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut


def _source_page_looks_edited(page: Path) -> bool:
    """Best-effort check that a source page changed after synthesis.

    Synthesis stamps ``last_updated`` with the current date. A file
    modified on a later day therefore carries edits we should not
    delete silently. A page without a parseable ``last_updated`` is
    also treated as edited.
    """
    try:
        text = page.read_text(encoding="utf-8")
        mtime = page.stat().st_mtime
    except OSError:
        return True
    meta, _body = parse_frontmatter(text)
    updated = str(meta.get("last_updated") or "").strip()
    try:
        updated_date = datetime.fromisoformat(updated).date()
    except ValueError:
        return True
    written_date = datetime.fromtimestamp(mtime, tz=timezone.utc).date()
    return written_date > updated_date


def _sync_project_sessions(wiki_dir: Path, project: str) -> None:
    """Rewrite the generated block in ``## Sessions`` from the source pages on disk.

    Only the text between the ``sessions:auto`` markers is regenerated; any
    manual notes outside the markers are left untouched.
    """
    page = wiki_dir / "projects" / f"{project}.md"
    sources = wiki_dir / "sources" / project
    if not page.exists() or not sources.is_dir():
        return
    rows: list[tuple[str, str, str]] = []
    for p in sorted(sources.glob("*.md")):
        if p.name.startswith("_"):
            continue
        try:
            meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
        except OSError:
            continue
        date = str(meta.get("date") or "")
        summary = ""
        match = re.search(r"## Summary\s*\n(.*?)(?:\n## |\Z)", body, re.S)
        if match:
            first = " ".join(match.group(1).split())
            first = re.split(r"(?<=[.!?])\s", first, maxsplit=1)[0]
            summary = _plain_wikilink_text(_short_summary(first).rstrip(" .;:"))
        rows.append((date, p.stem, summary))
    rows.sort(key=lambda r: (r[0], r[1]), reverse=True)
    bullet_lines = []
    for date, stem, summary in rows:
        label = f"{date} — {summary}" if summary else date
        bullet_lines.append(f"- [[{stem}|{label}]]")
    auto_block = (
        f"{_SESSIONS_START}\n"
        + "\n".join(bullet_lines)
        + f"\n{_SESSIONS_END}"
    )
    original = page.read_text(encoding="utf-8")
    text = original
    if _SESSIONS_START in text and _SESSIONS_END in text:
        pattern = re.compile(
            re.escape(_SESSIONS_START) + r".*?" + re.escape(_SESSIONS_END),
            re.S,
        )
        text = pattern.sub(lambda _: auto_block, text, count=1)
    else:
        heading = re.search(r"^## Sessions(?:[ \t]*\(\d+\))?[ \t]*$", text, flags=re.M)
        if heading:
            after = text[heading.end():].lstrip("\n")
            text = text[: heading.end()] + "\n\n" + auto_block + "\n\n" + after
        else:
            section = f"## Sessions\n\n{auto_block}"
            if "## Connections" in text:
                text = text.replace(
                    "## Connections", section + "\n\n## Connections", 1
                )
            else:
                text = text.rstrip() + "\n\n" + section + "\n"
    if text != original:
        page.write_text(text, encoding="utf-8")


def _coalesce_low_signal_sessions(
    sessions: list[tuple[Path, dict[str, Any], str]],
    *,
    sources_out: Path,
    state: dict[str, float],
    raw_dir: Path,
    min_group: int,
    window: str,
    lock_skip_patterns: tuple[str, ...] | list[str] | None = None,
) -> tuple[int, set[str], set[str]]:
    """Replace low-signal per-session pages with one cluster page each.

    Returns ``(clusters_written, coalesced_rel_paths, affected_projects)``.
    Sessions below ``min_group`` keep their individual page so a single
    failure is not hidden.
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for p, meta, body in sessions:
        reason = low_signal_reason(meta, body, lock_skip_patterns=lock_skip_patterns)
        if not reason:
            continue
        project = str(meta.get("project") or p.parent.name)
        date = str(meta.get("date") or "")
        bucket = date[:7] if window == "month" else "all"
        key = f"{project}\x00{bucket}"
        try:
            rel = str(p.relative_to(raw_dir))
        except ValueError:
            continue
        groups.setdefault(key, []).append({
            "path": p,
            "body": body,
            "rel": rel,
            "reason": reason,
            "project": project,
            "date": date,
            "slug": str(meta.get("slug") or p.stem),
            "meta": meta,
            "tools": int(meta.get("tool_calls") or 0),
            "output": _output_tokens(meta),
            "prompt": _prompt_fingerprint(body),
        })

    clusters = 0
    coalesced: set[str] = set()
    affected: set[str] = set()
    produced: dict[str, set[str]] = {}
    for rows in groups.values():
        if len(rows) < min_group:
            continue
        project = rows[0]["project"]
        page_name = _cluster_filename(rows, window)
        produced.setdefault(project, set()).add(page_name)
        page_path = sources_out / project / page_name
        page_path.parent.mkdir(parents=True, exist_ok=True)
        content = _render_cluster_page(
            project,
            rows,
            page_name=page_name,
            lock_skip_patterns=lock_skip_patterns,
        )
        changed = False
        if not page_path.exists() or page_path.read_text(encoding="utf-8") != content:
            page_path.write_text(content, encoding="utf-8")
            changed = True

        for row in rows:
            individual = _session_page_path(sources_out, row["meta"])
            # Delete only pages that still look machine-generated. A page
            # edited after synthesis keeps its content and stays visible.
            if individual.exists() and not _source_page_looks_edited(individual):
                individual.unlink()
                changed = True
            try:
                state[row["rel"]] = row["path"].stat().st_mtime
            except OSError:
                pass
            coalesced.add(row["rel"])

        manifest_path = _cluster_manifest_path(sources_out, project)
        manifest = _load_cluster_manifest(manifest_path)
        dates = sorted(str(r["date"]) for r in rows if r["date"])
        entry = {
            "sessions": sorted(str(r["slug"]) for r in rows),
            "reasons": sorted({str(r["reason"]) for r in rows}),
            "date_range": f"{dates[0]}..{dates[-1]}" if dates else "",
        }
        previous = manifest.get(page_name) or {}
        if any(previous.get(key) != value for key, value in entry.items()) or not previous:
            manifest[page_name] = {
                **entry,
                "updated": datetime.now(timezone.utc).date().isoformat(),
            }
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            changed = True

        if changed:
            clusters += 1
            affected.add(project)

    # Reconcile cluster pages that are no longer valid: a group can
    # shrink below ``min_group`` or change its window, and the old
    # cluster page would otherwise keep claiming those sessions were
    # no-ops. Dropping the members from ``state`` lets the normal
    # synthesis pass recreate their individual pages.
    rel_by_slug: dict[tuple[str, str], str] = {}
    for p, meta, _body in sessions:
        project = str(meta.get("project") or p.parent.name)
        slug = str(meta.get("slug") or p.stem)
        try:
            rel_by_slug[(project, slug)] = str(p.relative_to(raw_dir))
        except ValueError:
            continue

    for manifest_path in sorted(sources_out.glob("*/_clusters.json")):
        project = manifest_path.parent.name
        manifest = _load_cluster_manifest(manifest_path)
        stale = sorted(set(manifest) - produced.get(project, set()))
        if not stale:
            continue
        for page_name in stale:
            if "/" in page_name or "\\" in page_name:
                continue
            entry = manifest.pop(page_name, {}) or {}
            page_path = sources_out / project / page_name
            try:
                generated = "Auto-generated cluster page" in page_path.read_text(
                    encoding="utf-8"
                )
            except OSError:
                generated = False
            if generated:
                page_path.unlink()
            for slug in entry.get("sessions", []) or []:
                rel = rel_by_slug.get((project, str(slug)))
                if rel is not None:
                    state.pop(rel, None)
        if manifest:
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        else:
            manifest_path.unlink()
        affected.add(project)

    return clusters, coalesced, affected


# ─── #351: AI-suggested tags ──────────────────────────────────────────

# Emitted by the LLM as the first line of its response — we strip it
# from the body before writing, merge the tags into the frontmatter.
# Format: ``<!-- suggested-tags: a, b, c -->``
_SUGGESTED_TAGS_RE = re.compile(
    r"^\s*<!--\s*suggested-tags:\s*(?P<body>[^>]*?)\s*-->\s*\n?",
    re.IGNORECASE,
)

# Cap on AI-suggested tags per page (deterministic baseline is separate
# and never counted against this budget).  5 keeps the frontmatter list
# readable and prevents runaway tag-space growth on noisy sessions.
_AI_TAG_CAP = 5

# Tags the LLM sometimes proposes that duplicate the deterministic
# baseline or add no value — drop silently so we don't pollute the tag
# space with boilerplate the pipeline already emits.
_AI_TAG_STOPWORDS = frozenset({
    "session-transcript", "session", "claude-code", "codex-cli", "cursor",
    "copilot-chat", "gemini-cli", "opencode", "chatgpt", "obsidian",
    "claude", "gpt", "gemini", "llama", "opus",
    "summary", "discussion", "conversation", "transcript",
    # Empty-ish noise from malformed LLM responses.
    "", "-", "tag", "tags",
})


def _extract_suggested_tags(body: str) -> tuple[list[str], str]:
    """Pull the ``<!-- suggested-tags: … -->`` block off the top of
    ``body`` and return ``(tags, cleaned_body)``.

    Invariants:

    * Missing / malformed block → ``([], body)`` (body untouched).
    * Tags are kebab-cased + lowercased + deduped preserving order.
    * Empty strings / stop-words filtered out.
    * Hard-capped at :data:`_AI_TAG_CAP` before stop-word filtering so
      a noisy LLM can't drown out the cap check.

    Runs in pure Python — no LLM call.  This just parses whatever the
    synthesizer already produced.
    """
    m = _SUGGESTED_TAGS_RE.match(body)
    if not m:
        return [], body
    raw = m.group("body") or ""
    cleaned_body = body[m.end():]
    # Split on comma, normalise each.
    tags: list[str] = []
    seen: set[str] = set()
    for part in raw.split(","):
        t = part.strip().lower().replace(" ", "-")
        if not t or t in seen or t in _AI_TAG_STOPWORDS:
            continue
        tags.append(t)
        seen.add(t)
        if len(tags) >= _AI_TAG_CAP:
            break
    return tags, cleaned_body


def _merge_tags(
    baseline: list[str],
    suggested: list[str],
    existing: Optional[list[str]] = None,
) -> list[str]:
    """Merge the three tag sources into the final frontmatter list.

    Precedence (first-win, order preserved):

    1. Maintainer-curated ``existing`` tags (preserve on re-synthesize).
    2. Deterministic ``baseline`` (adapter + project + model family).
    3. AI-``suggested`` topical tags — only if they don't collide with
       or near-duplicate something already in 1 or 2.

    Near-duplicate detection uses ``tags.near_duplicate_tags`` so we
    reject ``prompt-cache`` when ``prompt-caching`` is already present.
    """
    # Local import to avoid a circular at module load.
    from llmwiki.tags import near_duplicate_tags, TagEntry

    out: list[str] = []
    seen: set[str] = set()

    def _push(tag: str) -> None:
        t = tag.strip()
        if not t:
            return
        key = t.lower()
        if key in seen:
            return
        out.append(t)
        seen.add(key)

    for t in (existing or []):
        _push(t)
    for t in baseline:
        _push(t)
    # For each suggested tag, skip if a near-duplicate already exists.
    # Uses a tighter threshold (0.80) than the CLI default (0.85) — we
    # want auto-merge to be conservative so ``prompt-cache`` (0.846 vs
    # ``prompt-caching``) gets rejected at ingest time.  Maintainers can
    # still add it explicitly via ``llmwiki tag add``.
    if suggested:
        existing_snapshot = list(out)
        for candidate in suggested:
            candidate_lc = candidate.lower()
            # Cheap prefix check: one is a prefix of the other.
            def _substr_near(a: str, b: str) -> bool:
                a_l, b_l = a.lower(), b.lower()
                if a_l == b_l:
                    return True
                shorter, longer = sorted((a_l, b_l), key=len)
                return len(shorter) >= 5 and shorter in longer
            if any(_substr_near(candidate, existing_t) for existing_t in existing_snapshot):
                continue
            # Expensive fuzzy check for other near-dupes (typos, plural, etc.).
            entries = [
                TagEntry(page=Path("/virtual"), field="tags", tag=t)
                for t in existing_snapshot + [candidate]
            ]
            dups = near_duplicate_tags(entries, threshold=0.80)
            collides = any(
                candidate_lc in (a.lower(), b.lower()) and a.lower() != b.lower()
                for (a, b, _score) in dups
            )
            if collides:
                continue
            _push(candidate)
            existing_snapshot.append(candidate)
    return out


def _derive_baseline_tags(meta: dict[str, Any]) -> list[str]:
    """Return a never-empty baseline tag list for synthesized source pages.

    Takes the raw session's ``meta["tags"]`` and augments it with tags
    derived from the project slug + model (when the raw list is empty
    or just carries the boilerplate ``[session-transcript]``).  The
    goal: **every** synthesized page leaves the pipeline with at least
    one meaningful tag so filters / graph chips / the new
    ``tags_topics_convention`` lint rule don't see empty lists.
    """
    out: list[str] = []
    seen: set[str] = set()
    # Start with whatever the raw frontmatter shipped.
    for t in meta.get("tags", []) or []:
        t = str(t).strip()
        if t and t not in seen:
            out.append(t)
            seen.add(t)
    # Ensure the adapter source stamp (claude-code / codex-cli / obsidian / …)
    # appears at least as session-transcript so routing by source stays cheap.
    if "session-transcript" not in seen and "claude-code" not in seen:
        out.append("session-transcript")
        seen.add("session-transcript")
    # Add the project slug as a tag so filters-by-project work out-of-the-box.
    project = str(meta.get("project", "") or "").strip()
    if project and project != "unknown" and project not in seen:
        out.append(project)
        seen.add(project)
    # Model family as a coarse bucket (claude-sonnet-4-6 → claude).
    model = str(meta.get("model", "") or "").strip().lower()
    for family in ("claude", "gpt", "gemini", "llama", "opus"):
        if family in model and family not in seen:
            out.append(family)
            seen.add(family)
            break
    return out


def _build_source_page(
    meta: dict[str, Any],
    synthesized_body: str,
    existing_page_path: Optional[Path] = None,
) -> str:
    """Combine frontmatter + synthesized body into a full wiki source page.

    #351: If the ``synthesized_body`` starts with a
    ``<!-- suggested-tags: ... -->`` block (emitted by the LLM per the
    ``source_page.md`` prompt), the tags are extracted, de-duplicated
    against the deterministic baseline, and merged into the frontmatter.
    The comment is stripped from the body so it never reaches the
    rendered site.

    If ``existing_page_path`` points at an existing wiki source file,
    its current frontmatter ``tags`` are preserved verbatim (maintainer
    curation is never overwritten on re-synthesize).
    """
    slug = meta.get("slug", "unknown")
    title = meta.get("title", f"Source: {slug}")
    project = meta.get("project", "unknown")
    date = meta.get("date", "")
    model = meta.get("model", "")
    source_file = meta.get("source_file", "")

    # #351: pull AI-suggested tags off the top of the body.
    ai_tags, clean_body = _extract_suggested_tags(synthesized_body)

    # Preserve any maintainer-curated tags on re-synthesize.
    # #py-h5 (#584): the broad `except Exception` was eating real
    # parse failures + unicode errors silently, dropping the curated
    # tags on every regression. Narrow to the failures that are
    # actually expected here (file read OSError, frontmatter format
    # issues): everything else (MemoryError, KeyboardInterrupt,
    # surprise type errors) bubbles up so the regression is visible
    # instead of silently producing a tag-loss diff.
    existing_tags: list[str] = []
    if existing_page_path is not None and existing_page_path.exists():
        try:
            existing_meta, _existing_body = parse_frontmatter(
                existing_page_path.read_text(encoding="utf-8")
            )
            existing_tags = list(existing_meta.get("tags", []) or [])
        except (OSError, ValueError, UnicodeDecodeError) as e:
            # Log loud — silent drop is what #584 was about.
            import sys as _sys
            print(
                f"warning: could not preserve tags from "
                f"{existing_page_path}: {e}",
                file=_sys.stderr,
            )
            existing_tags = []

    baseline = _derive_baseline_tags(meta)
    tags = _merge_tags(baseline, ai_tags, existing_tags)

    fm = [
        "---",
        f'title: "{title}"',
        "type: source",
        f"tags: [{', '.join(tags)}]",
        f"date: {date}",
        f"source_file: {source_file}",
        f"project: {project}",
        f"model: {model}",
        f"last_updated: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
        "---",
        "",
    ]
    return "\n".join(fm) + clean_body


def synthesize_new_sessions(
    backend: Optional[BaseSynthesizer] = None,
    raw_dir: Optional[Path] = None,
    wiki_sources_dir: Optional[Path] = None,
    dry_run: bool = False,
    force: bool = False,
    log_path: Optional[Path] = None,
    state_file: Optional[Path] = None,
    max_workers: int = 1,
    coalesce_low_signal: bool = False,
    coalesce_min_group: int = 3,
    coalesce_window: str = "all",
    lock_skip_patterns: tuple[str, ...] | list[str] | None = None,
    coalesce_only: bool = False,
    strip_unresolved_wikilinks: bool = True,
) -> dict[str, Any]:
    """Main entry point. Returns a summary dict:

    {
        "total_scanned": int,
        "new_files": int,
        "synthesized": int,
        "coalesced": int,
        "skipped": int,
        "errors": list[str],
        "backend": str,
    }
    """
    sources_out = wiki_sources_dir or WIKI_SOURCES
    prompt_template = _load_prompt_template()
    state = {} if force else _load_state(state_file)
    sessions = _discover_raw_sessions(raw_dir)
    raw_root = raw_dir or RAW_SESSIONS

    new_files: list[tuple[Path, dict[str, Any], str]] = []
    for p, meta, body in sessions:
        rel = str(p.relative_to(raw_root))
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        if rel in state and state[rel] >= mtime and not force:
            continue
        new_files.append((p, meta, body))

    summary: dict[str, Any] = {
        "total_scanned": len(sessions),
        "new_files": len(new_files),
        "synthesized": 0,
        "coalesced": 0,
        "skipped": 0,
        "errors": [],
        "backend": backend.name if backend is not None else "none",
    }

    # Coalescing is deterministic and does not need a backend, so it runs
    # before the availability probe. This keeps cleanup possible while
    # the LLM server is down.
    if coalesce_low_signal and not dry_run:
        clusters, coalesced_rels, affected = _coalesce_low_signal_sessions(
            sessions,
            sources_out=sources_out,
            state=state,
            raw_dir=raw_root,
            min_group=max(1, int(coalesce_min_group)),
            window=coalesce_window,
            lock_skip_patterns=lock_skip_patterns,
        )
        summary["coalesced"] = clusters
        summary["coalesced_projects"] = sorted(affected)
        if coalesced_rels:
            new_files = [
                item
                for item in new_files
                if str(item[0].relative_to(raw_root)) not in coalesced_rels
            ]
            summary["new_files"] = len(new_files)
            for project in affected:
                _sync_project_sessions(sources_out.parent, project)
        if clusters:
            print(f"  coalesced: {clusters} cluster page(s)")

    if coalesce_only:
        # Deterministic cleanup only: no backend probe, no per-session
        # pages. The Stop-hook can call this without an LLM server.
        summary["backend"] = "none"
        if not dry_run:
            _save_state(state, state_file)
            if summary.get("coalesced", 0) > 0:
                try:
                    _rebuild_index(sources_out.parent)
                except (OSError, ValueError, RuntimeError) as e:
                    summary["errors"].append(f"index rebuild: {e}")
        return summary

    if backend is None:
        backend = DummySynthesizer()
        summary["backend"] = backend.name

    if new_files and not backend.is_available():
        summary["errors"].append(f"Backend {backend.name} is not available")
        # A --force run starts from an empty state. Saving it here would
        # wipe the record of every already-synthesized page, so leave the
        # on-disk state untouched when the backend cannot be reached.
        if not force:
            _save_state(state, state_file)
        return summary

    if dry_run:
        summary["skipped"] = len(new_files)
        print(
            f"[dry-run] Would synthesize {len(new_files)} new sessions "
            f"using {backend.name}"
        )
        for p, meta, _ in new_files:
            print(f"  {meta.get('slug', p.stem)}")
        return summary

    if max_workers < 1:
        max_workers = 1
    lock = threading.Lock()

    def _process_one(
        item: tuple[Path, dict[str, Any], str],
    ) -> tuple[str, str, Optional[float], str, str, Optional[str]]:
        p, meta, body = item
        raw_slug = meta.get("slug", p.stem)
        project = meta.get("project", p.parent.name)
        rel = str(p.relative_to(raw_dir or RAW_SESSIONS))
        slug = _normalise_slug(raw_slug)
        date = str(meta.get("date", "")).strip()
        filename = f"{date}-{slug}" if date else slug
        try:
            synthesized = backend.synthesize_source_page(body, meta, prompt_template)
            out_dir = sources_out / project
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"{filename}.md"
            page_content = _build_source_page(
                meta, synthesized, existing_page_path=out_path
            )
            out_path.write_text(page_content, encoding="utf-8")
            return ("ok", rel, p.stat().st_mtime, project, filename, None)
        except Exception as e:
            return ("error", rel, None, project, filename, f"{slug}: {e}")

    def _record(
        result: tuple[str, str, Optional[float], str, str, Optional[str]],
    ) -> None:
        status, rel, mtime, project, filename, error = result
        with lock:
            if status == "ok" and mtime is not None:
                state[rel] = mtime
                summary["synthesized"] += 1
                print(f"  synthesized: {project} → {filename}")
                # Checkpoint so an interrupted long run keeps the
                # pages it already produced.
                if summary["synthesized"] % 10 == 0:
                    _save_state(state, state_file)
            else:
                summary["errors"].append(error or "unknown error")
                summary["skipped"] += 1
                print(f"  error: {error}")

    if max_workers > 1:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [pool.submit(_process_one, item) for item in new_files]
            for future in as_completed(futures):
                _record(future.result())
    else:
        for item in new_files:
            _record(_process_one(item))

    if summary["synthesized"] > 0:
        # LLM pages often link to concepts that were never written.
        # Degrade unresolved links to plain text so link_integrity
        # stays clean, then refresh the project session indexes.
        # strip_unresolved_wikilinks=False keeps the red links so pages
        # created later still receive them.
        if strip_unresolved_wikilinks:
            try:
                summary["stripped_links"] = _strip_unresolved_wikilinks(
                    sources_out.parent, sources_out
                )
            except OSError as e:
                summary["errors"].append(f"link cleanup: {e}")
        touched = {
            str(meta.get("project") or p.parent.name)
            for p, meta, _ in new_files
        }
        for project in sorted(touched):
            _sync_project_sessions(sources_out.parent, project)

    # G-20 (#306): emit ONE summary log entry per invocation, not one
    # per page. Includes project counts + error count. The old per-page
    # entries flooded wiki/log.md (60+ lines per run).
    if (
        summary["synthesized"] > 0
        or summary.get("coalesced", 0) > 0
        or summary["errors"]
    ):
        projects_touched: dict[str, int] = {}
        for p, meta, _ in new_files:
            project = meta.get("project", p.parent.name)
            projects_touched[project] = projects_touched.get(project, 0) + 1
        parts: list[str] = []
        if summary["synthesized"]:
            parts.append(
                f"{summary['synthesized']} sessions across "
                f"{len(projects_touched)} projects"
            )
        if summary.get("coalesced"):
            parts.append(f"{summary['coalesced']} cluster page(s)")
        _append_log(
            " and ".join(parts) if parts else "0 sessions",
            log_path=log_path,
            operation="synthesize",
            details={
                "processed": summary["synthesized"],
                "coalesced": summary.get("coalesced", 0),
                "created": sorted(
                    set(projects_touched) | set(summary.get("coalesced_projects", []))
                ),
                "errors": summary["errors"],
            },
        )

    _save_state(state, state_file)

    # G-09 (#295): rebuild wiki/index.md so lint's index_sync rule
    # passes on fresh synthesized corpora. Synthesize is authoritative
    # for `## Sources` — the index reflects whatever's on disk now.
    # #arch-m7 (#619): gate behind a "did anything actually change?"
    # check. The index rebuild walks the entire wiki + parses every
    # frontmatter; on a 5k-page corpus that's seconds. Skip when zero
    # pages were synthesized in this pass.
    if summary.get("synthesized", 0) > 0 or summary.get("coalesced", 0) > 0:
        try:
            _rebuild_index(sources_out.parent)
        except (OSError, ValueError, RuntimeError) as e:
            summary["errors"].append(f"index rebuild: {e}")

    return summary
