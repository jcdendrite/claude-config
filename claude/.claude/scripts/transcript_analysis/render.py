"""Small display-formatting and text-normalization helpers -- model-family
labels, markdown/table rendering -- no dependency on any cmd_* subcommand,
scope resolution, redaction, or pricing.
"""
from __future__ import annotations

import json
import re
from collections.abc import Sequence
from datetime import UTC, date, datetime


def _fam(model: str) -> str:
    m = model.lower()
    if "opus" in m:
        return "opus"
    if "sonnet" in m:
        return "sonnet"
    if "haiku" in m:
        return "haiku"
    return "other"


_BASH_COMMAND_DISPLAY_CHARS = 80


def _content_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _fmt_usd(amount: float) -> str:
    return f"-${-amount:,.2f}" if amount < 0 else f"${amount:,.2f}"


def _pct_of(value: float, total: float) -> str:
    """value/total as a percentage string; 0.0% (not an undefined dash) when total is zero."""
    return f"{100 * value / total:.1f}%" if total else "0.0%"


def _pct_value(value: float, total: float) -> float:
    """value/total as a percentage float, matching _pct_of's 0.0-when-zero
    convention -- for a caller (cost-ledger) that stores the number rather
    than printing it."""
    return 100 * value / total if total else 0.0


def _fmt_date(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=UTC).strftime("%Y-%m-%d")


def _context_distribution_rows(
    thresholds: Sequence[float], peaks: Sequence[float], dollars: Sequence[float]
) -> list[dict[str, object]]:
    """For each threshold, in peaks' own unit, return a row of crossing-count,
    session-share, crossed-dollars, and dollar-share — the arithmetic shared
    by context-distribution's percentage table and its absolute-token table.

    peaks and dollars are parallel per-session sequences (peaks[i] and
    dollars[i] describe the same session). A session crosses a threshold
    when peaks[i] >= threshold, matching the hook's own >=-shaped trigger
    condition.
    """
    total_sessions = len(peaks)
    total_dollars = sum(dollars)
    rows: list[dict[str, object]] = []
    for threshold in thresholds:
        crossed_count = sum(1 for p in peaks if p >= threshold)
        crossed_dollars = sum(d for p, d in zip(peaks, dollars, strict=True) if p >= threshold)
        rows.append({
            "sessions": crossed_count,
            "session_share": _pct_of(crossed_count, total_sessions),
            "dollars": crossed_dollars,
            "dollar_share": _pct_of(crossed_dollars, total_dollars),
        })
    return rows


_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")


def _sanitize_table_cell(value: str) -> str:
    """Strip ASCII control characters from a --deny-summary table cell value."""
    return _CONTROL_CHAR_RE.sub("", value)


_TASK_NOTIFICATION_RE = re.compile(r"<task-notification>.*?</task-notification>", re.DOTALL)


def _strip_task_notifications(text: str) -> str:
    """Remove `<task-notification>...</task-notification>` spans from a user turn's text.

    The harness delivers a finished background task or subagent as a plain
    `type: "user"` record whose body is that envelope, so its `<summary>` is
    the subagent's own prose about its own findings, not human input.
    """
    # Replaced with a space, not "", so a removed span can't weld the words on either side into a phrase nobody wrote.
    # An unterminated or self-nested envelope is left partially or fully in place rather than swallowing the rest of the turn.
    return _TASK_NOTIFICATION_RE.sub(" ", text)


def _pretty_tool_call(tool_call: dict) -> str:
    """Render a tool call dict as a concise markdown-inline string for curation review."""
    name = tool_call.get("name", "")
    inp = tool_call.get("input") or {}
    if name == "Read":
        file_path = inp.get("file_path", "")
        return f"**Read:** `{file_path}`"
    if name == "Grep":
        pattern = inp.get("pattern", "")
        path = inp.get("path")
        if path:
            return f"**Grep:** `{pattern}` in `{path}`"
        return f"**Grep:** `{pattern}` (repo-wide)"
    if name == "Glob":
        pattern = inp.get("pattern", "")
        path = inp.get("path")
        if path:
            return f"**Glob:** `{pattern}` in `{path}`"
        return f"**Glob:** `{pattern}` (repo-wide)"
    if name == "Bash":
        command = inp.get("command", "")
        description = inp.get("description", "")
        truncated = command[:_BASH_COMMAND_DISPLAY_CHARS] + "…" if len(command) > _BASH_COMMAND_DISPLAY_CHARS else command
        if description:
            return f"**Bash:** {description} — `{truncated}`"
        return f"**Bash:** `{truncated}`"
    compact = json.dumps(inp, separators=(",", ":"))
    return f"**{name}:** `{compact}`"


def _blockquote_user_message(text: str) -> str:
    """Prefix each line of text with '> ' for markdown blockquoting.

    Blank lines render as '>' (no trailing space) to preserve blockquote
    continuity in standard markdown renderers.
    """
    if not text:
        return "> (no preceding user message)"
    lines = text.split("\n")
    return "\n".join((f"> {line}").rstrip() for line in lines)


_NARRATION_EXCERPT_CHARS = 280
_TRAIL_CMD_DISPLAY_CHARS = 100
_RECENT_LOOKBACK_N = 3


def _recent_assistant_text(
    session_records: list[dict], turn_idx: int, n: int
) -> list[str]:
    """Return up to n most-recent assistant text excerpts before turn_idx.

    Walks backward through session_records, collects the last text block
    from each assistant turn (kind != 'user'), skips turns with no text
    block. Returns entries in chronological order (oldest first).
    Newlines in each excerpt are replaced with spaces so the caller can
    render each as a single blockquote line.
    """
    excerpts: list[str] = []
    for i in range(turn_idx - 1, -1, -1):
        if len(excerpts) >= n:
            break
        entry = session_records[i]
        if entry["kind"] == "user":
            continue
        last_text = ""
        for block in entry.get("content", []):
            if isinstance(block, dict) and block.get("type") == "text":
                last_text = block.get("text") or ""
        if not last_text:
            continue
        flat = last_text.replace("\n", " ")
        if len(flat) > _NARRATION_EXCERPT_CHARS:
            flat = flat[:_NARRATION_EXCERPT_CHARS] + "…"
        excerpts.append(flat)
    excerpts.reverse()
    return excerpts


def _recent_tool_trail(
    session_records: list[dict], turn_idx: int, n: int
) -> list[str]:
    """Return up to n most-recent tool-call summary strings before turn_idx.

    Walks backward through session_records collecting the first tool_use
    block in each assistant turn, oldest-first in the returned list. Each
    entry is a one-line human-readable label:
      - Read: <file_path>
      - Grep: <pattern> in <path>  /  Grep: <pattern> (repo-wide)
      - Glob: <pattern> in <path>  /  Glob: <pattern> (repo-wide)
      - Bash: <description> — `<truncated_command>`  (or just `<cmd>` if no description)
      - <Name>: <compact_json_input>  (fallback)
    """
    trail: list[str] = []
    for i in range(turn_idx - 1, -1, -1):
        if len(trail) >= n:
            break
        entry = session_records[i]
        if entry["kind"] == "user":
            continue
        for block in entry.get("content", []):
            if isinstance(block, dict) and block.get("type") == "tool_use":
                name = block.get("name", "")
                inp = block.get("input") or {}
                if name == "Read":
                    label = f"Read: {inp.get('file_path', '')}"
                elif name == "Grep":
                    pat = inp.get("pattern", "")
                    path = inp.get("path")
                    label = f"Grep: {pat} in {path}" if path else f"Grep: {pat} (repo-wide)"
                elif name == "Glob":
                    pat = inp.get("pattern", "")
                    path = inp.get("path")
                    label = f"Glob: {pat} in {path}" if path else f"Glob: {pat} (repo-wide)"
                elif name == "Bash":
                    command = inp.get("command", "")
                    description = (inp.get("description") or "").replace("\n", " ")
                    truncated = command[:_TRAIL_CMD_DISPLAY_CHARS] + "…" if len(command) > _TRAIL_CMD_DISPLAY_CHARS else command
                    label = f"Bash: {description} — `{truncated}`" if description else f"Bash: `{truncated}`"
                else:
                    compact = json.dumps(inp, separators=(",", ":"))
                    label = f"{name}: {compact}"
                trail.append(label)
                break
    trail.reverse()
    return trail


def _format_samples_as_markdown(
    records: list[dict],
    *,
    since_raw: str | None,
    sample_n: int,
    seed: int | None,
) -> str:
    """Return a full markdown document for human curation of audit-routing-samples output."""
    today = date.today().isoformat()
    since_display = since_raw or "(none)"
    seed_display = str(seed) if seed is not None else "(none)"
    filter_line = f"`--since {since_display}  --sample {sample_n}  --seed {seed_display}`"

    header = (
        f"# audit-routing-samples curation — {len(records)} turns\n"
        f"\n"
        f"Generated: {today}  ·  Filter: {filter_line}\n"
        f"\n"
        f"For each turn: read the three context blocks, then check ONE verdict box.\n"
        f"- `true (delegate)` — content sat in context with no immediate consumer;"
        f" delegation would have saved tokens\n"
        f"- `false (inline correct)` — content fed an immediate edit or comprehension-driven response\n"
        f"- `skip` — ambiguous or noise; drop from curated set\n"
    )

    sections: list[str] = []
    total = len(records)
    for i, rec in enumerate(records):
        session_id = rec.get("session_id", "")
        turn_index = rec.get("turn_index", 0)
        prior_user_message = rec.get("prior_user_message", "")
        assistant_tool_call = rec.get("assistant_tool_call") or {"name": "", "input": {}}
        next_assistant_action = rec.get("next_assistant_action", "")
        next_turn_excerpt = rec.get("next_turn_excerpt", "")

        blockquoted = _blockquote_user_message(prior_user_message)
        tool_line = _pretty_tool_call(assistant_tool_call)
        next_excerpt_line = next_turn_excerpt.replace("\n", " ") if next_turn_excerpt else "(none)"

        recent_text = rec.get("recent_assistant_text") or []
        recent_trail = rec.get("recent_tool_trail") or []

        narration_block = ""
        if recent_text:
            lines = "\n".join(f"> {t}" for t in recent_text)
            narration_block = f"\n**Recent agent narration:**\n{lines}\n"

        trail_block = ""
        if recent_trail:
            lines = "\n".join(f"- {t}" for t in recent_trail)
            trail_block = f"\n**Recent tool trail:**\n{lines}\n"

        section = (
            f"## {i + 1}/{total} — session `{session_id}` turn {turn_index}\n"
            f"\n"
            f"**User:**\n"
            f"{blockquoted}\n"
            f"\n"
            f"{tool_line}\n"
            f"{narration_block}"
            f"{trail_block}"
            f"\n"
            f"**Next:** `{next_assistant_action}`\n"
            f"> {next_excerpt_line}\n"
            f"\n"
            f"**Verdict** (check one):\n"
            f"- [ ] true (delegate)\n"
            f"- [ ] false (inline correct)\n"
            f"- [ ] skip\n"
        )
        sections.append(section)

    return header + "\n---\n\n" + "\n---\n\n".join(sections)
