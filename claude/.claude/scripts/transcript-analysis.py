#!/usr/bin/env python3
"""transcript-analysis.py — Claude Code transcript analysis toolkit.
pr-link is the only subcommand that touches the network (via gh).
judgment-pair --out writes a file; all other subcommands are read-only.
"""

import argparse
import contextlib
import errno
import fcntl
import fnmatch
import hashlib
import json
import math
import os
import random
import re
import shlex
import socket
import stat
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from _config_dir import (
    TRANSCRIPT_CONFIG_DIRS_LABEL,
    config_dir,
    declared_roots_file_state,
    declared_transcript_roots,
)

PROJECTS_DIR = config_dir() / "projects"

TEST_RUNNER_RE = re.compile(
    r"\b(vitest|jest|pytest|deno\s+test|npm\s+run\s+(verify|test|lint)|ruff\s+check|cargo\s+test|go\s+test)\b"
)
FAILED_RE = re.compile(r"\b(\d+)\s+failed\b")

STRUGGLE_PHRASES: list[str] = [
    # attested in transcripts
    "hold on",
    "why did you",
    "try again",
    "no not that",
    # predicted patterns
    "no, that",
    "that's wrong",
    "not right",
    "you're wrong",
    "stop doing",
    "don't do that",
    "still broken",
    "still failing",
    "you missed",
    "incorrect",
    "not what i asked",
    "not what i wanted",
    "wrong approach",
    "that doesn't work",
    "please don't",
    # attested in transcripts but missed by prior lexicon (see test_transcript_analysis.py)
    # Excluded: bare "stale" — legitimate technical term with high false-positive risk
    "hallucinat",  # matches "hallucinated", "hallucinating", etc.
    "are you saying",
    "you should be able to",
    "that doesn't exist",
    "that doesn't match",
]


def _projects_glob(args: argparse.Namespace) -> str:
    return getattr(args, "projects", None) or "*"


def _branch_filter(args: argparse.Namespace) -> set[str] | None:
    raw: str | None = getattr(args, "branches", None)
    return {b for b in raw.split(",") if b} if raw else None


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


def _is_fresh_user_prompt(rec: dict) -> bool:
    """Return True iff rec is a genuine new user message (not a tool result or injected record).

    Filters out:
    - Records that are not type=="user"
    - Sidechain records (isSidechain=True)
    - Meta-injected records (isMeta=True)
    - Compaction summary records (isCompactSummary=True)
    - Tool-result-bearing records (content is a list with any block whose type=="tool_result")
    - Records with empty text content
    """
    if rec.get("type") != "user":
        return False
    if rec.get("isSidechain"):
        return False
    if rec.get("isMeta"):
        return False
    if rec.get("isCompactSummary"):
        return False
    content = (rec.get("message") or {}).get("content", "")
    if isinstance(content, list) and any(
        isinstance(b, dict) and b.get("type") == "tool_result" for b in content
    ):
        return False
    return bool(_content_text(content).strip())


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


def _parse_ts(ts_str: str | None) -> float | None:
    if not ts_str:
        return None
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


def _parse_since_nd_arg(args: argparse.Namespace, subcommand: str) -> tuple[float | None, str | None]:
    """Parse the shared --since Nd flag (e.g. "35d") into (since_ts, since_raw).

    since_ts is None when --since is absent; since_raw is the raw flag value
    (or None) so callers can derive their own display label. Exits 1 on a
    malformed value, embedding `subcommand` in the error text to match each
    subcommand's own --since error message.
    """
    since_raw: str | None = getattr(args, "since", None) or None
    since_ts: float | None = None
    if since_raw:
        try:
            days = float(since_raw.rstrip("d"))
            since_ts = time.time() - days * 86400
        except ValueError:
            print(f"{subcommand}: --since: expected Nd like '35d', got {since_raw!r}", file=sys.stderr)
            sys.exit(1)
    return since_ts, since_raw


def _iso_date(s: str) -> str:
    """argparse type: validate a YYYY-MM-DD date string."""
    try:
        datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a valid YYYY-MM-DD date: {s!r}") from None
    return s


def _fmt_date(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=UTC).strftime("%Y-%m-%d")


def _fmt_usd(amount: float) -> str:
    return f"-${-amount:,.2f}" if amount < 0 else f"${amount:,.2f}"


def _parse_jsonl_records(jsonl: Path) -> list[dict] | None:
    """Parse one .jsonl file into records, skipping malformed lines.

    Returns None when the file cannot be opened, which is distinct from an empty
    but readable file ([]): an unreadable main transcript aborts the whole
    session, while an empty one still carries its subagent files.
    """
    records: list[dict] = []
    try:
        with open(jsonl) as fh:
            for raw in fh:
                try:
                    records.append(json.loads(raw))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return None
    return records


def _read_session_file_partitioned(jsonl: Path, include_subagents: bool) -> list[list[dict]]:
    """Read one transcript file's records, keeping each source file's records separate.

    Returns one list per source file: the main transcript first, then each
    <session_id>/subagents/*.jsonl in sorted order. An unreadable main file
    yields [] (no groups at all); an unreadable subagent file is skipped. A
    readable-but-empty main file still yields its subagent groups, matching
    _read_session_file's long-standing behaviour.

    The per-file boundary matters to any caller that differences consecutive
    turns: separate files are separate context windows, so a delta taken across
    a boundary compares two unrelated conversations. _read_session_file flattens
    this for the callers that only need the records. A group's own source-file
    stem is not a reliable stand-in for its records' sessionId -- a subagent
    file is named by its own agent id but its records carry the *parent*
    session's sessionId -- so a caller needing per-record session identity
    (e.g. _read_scope_growth_for_group) keys off each record's own sessionId,
    never off this function's file-level grouping.
    """
    main = _parse_jsonl_records(jsonl)
    if main is None:
        return []
    groups = [main]

    if include_subagents:
        subagent_dir = jsonl.parent / jsonl.stem / SUBAGENT_SUBDIR
        if subagent_dir.is_dir():
            for sub_jsonl in sorted(subagent_dir.glob("*.jsonl")):
                sub_records = _parse_jsonl_records(sub_jsonl)
                if sub_records:
                    groups.append(sub_records)

    return groups


def _read_session_file(jsonl: Path, include_subagents: bool) -> list[dict]:
    """Read one transcript file's records, merging its subagent files when asked.

    Shared by iter_sessions (which selects files by glob) and _iter_scoped_sessions
    (which selects project dirs by exact-name identity, then reads each file).
    Keeping the per-file read here means the two selection strategies cannot drift
    in how they parse records or merge subagent files.

    When include_subagents=True, records from split subagent files under
    <session_id>/subagents/*.jsonl are appended. Those files carry
    isSidechain: true on assistant records. Returns [] for an unreadable file.

    Flattens _read_session_file_partitioned in source-file order, so the merged
    sequence is exactly the concatenation it has always been.
    """
    return [rec for group in _read_session_file_partitioned(jsonl, include_subagents) for rec in group]


def iter_sessions(
    projects_dir: Path,
    projects_glob: str = "*",
    include_subagents: bool = False,
) -> Iterator[tuple[Path, list[dict]]]:
    """Yield (jsonl_path, records) for each transcript file matching the glob.

    Files are yielded in a single flat sort over their full paths — NOT grouped
    by directory. Grouping by directory would misorder projects whenever one
    project-dir name is a lexical prefix of a sibling's (exactly this repo's
    own worktree naming, e.g. -home-u-repo vs -home-u-repo--claude-worktrees-b).
    Redact-label assignment (_build_redact_map) does not depend on this order —
    it sorts the collected labels itself before assigning placeholders — but
    every other caller iterates these results directly, so the flat sort is
    what keeps their output order deterministic and reproducible across runs.
    See _read_session_file for the per-file read and the include_subagents
    merge behavior.
    """
    for jsonl in sorted(projects_dir.glob(f"{projects_glob}/*.jsonl")):
        records = _read_session_file(jsonl, include_subagents)
        if records:
            yield jsonl, records


def _longest_fail_streak(failed_flags: list[bool]) -> int:
    """Return the longest consecutive run of True values in failed_flags."""
    max_streak = current = 0
    for flag in failed_flags:
        if flag:
            current += 1
            max_streak = max(max_streak, current)
        else:
            current = 0
    return max_streak


def cmd_buckets(args: argparse.Namespace) -> None:
    branch_filter = _branch_filter(args)
    roots = _resolve_scan_roots(args)
    session_iter, scope_label = _resolve_project_scope(args, "buckets", roots=roots)
    _print_resolved_scope("buckets", scope_label, roots)

    branch_data: dict[str, dict] = defaultdict(
        lambda: {
            "sessions": 0, "projects": set(), "opus": 0, "sonnet": 0, "haiku": 0, "other": 0,
            "ts_min": float("inf"), "ts_max": float("-inf"),
        }
    )

    for jsonl, records in session_iter:
        file_branches: dict[str, dict] = defaultdict(
            lambda: {"opus": 0, "sonnet": 0, "haiku": 0, "other": 0, "ts_min": float("inf"), "ts_max": float("-inf")}
        )
        for rec in records:
            branch = rec.get("gitBranch") or ""
            if not branch or (branch_filter and branch not in branch_filter):
                continue
            ts = _parse_ts(rec.get("timestamp"))
            if ts is not None:
                file_branches[branch]["ts_min"] = min(file_branches[branch]["ts_min"], ts)
                file_branches[branch]["ts_max"] = max(file_branches[branch]["ts_max"], ts)
            if rec.get("type") == "assistant" and not bool(rec.get("isSidechain")):
                fam = _fam((rec.get("message") or {}).get("model", ""))
                file_branches[branch][fam] += 1

        for branch, fb in file_branches.items():
            d = branch_data[branch]
            d["sessions"] += 1
            d["projects"].add(jsonl.parent.name)
            for fam in ("opus", "sonnet", "haiku", "other"):
                d[fam] += fb[fam]
            if fb["ts_min"] < float("inf"):
                d["ts_min"] = min(d["ts_min"], fb["ts_min"])
            if fb["ts_max"] > float("-inf"):
                d["ts_max"] = max(d["ts_max"], fb["ts_max"])

    if not branch_data:
        print("No data found.")
        return

    print(
        f"{'Branch':<40} {'Proj':>4} {'Sess':>5} {'Total':>7} {'Opus':>6} {'Sonnet':>7} "
        f"{'Haiku':>6} {'Other':>6}  Date range"
    )
    print("-" * 113)
    for branch in sorted(branch_data):
        d = branch_data[branch]
        total = d["opus"] + d["sonnet"] + d["haiku"] + d["other"]
        ts_min = _fmt_date(d["ts_min"]) if d["ts_min"] < float("inf") else "?"
        ts_max = _fmt_date(d["ts_max"]) if d["ts_max"] > float("-inf") else "?"
        print(
            f"{branch:<40} {len(d['projects']):>4} {d['sessions']:>5} {total:>7} {d['opus']:>6} {d['sonnet']:>7} "
            f"{d['haiku']:>6} {d['other']:>6}  {ts_min}..{ts_max}"
        )


def cmd_fail_seq(args: argparse.Namespace) -> None:
    if not getattr(args, "branches", None):
        print("--branches is required for fail-seq", file=sys.stderr)
        sys.exit(1)
    branches: set[str] = {b for b in args.branches.split(",") if b}
    roots = _resolve_scan_roots(args)
    session_iter, scope_label = _resolve_project_scope(args, "fail-seq", roots=roots)
    _print_resolved_scope("fail-seq", scope_label, roots)

    branch_runs: dict[str, list[tuple[str, int]]] = defaultdict(list)

    for _jsonl, records in session_iter:
        if not ({r.get("gitBranch", "") for r in records} & branches):
            continue

        pending: dict[str, str] = {}  # tool_use_id → model_family
        current_branch: str = ""

        for rec in records:
            branch = rec.get("gitBranch") or ""
            if branch != current_branch:
                pending.clear()
                current_branch = branch
            if branch not in branches or bool(rec.get("isSidechain")):
                continue

            rtype = rec.get("type", "")
            msg = rec.get("message") or {}

            if rtype == "assistant":
                fam = _fam(msg.get("model", ""))
                for block in (msg.get("content") or []):
                    if (
                        isinstance(block, dict)
                        and block.get("type") == "tool_use"
                        and block.get("name") == "Bash"
                    ):
                        cmd = (block.get("input") or {}).get("command", "")
                        if TEST_RUNNER_RE.search(cmd):
                            pending[block["id"]] = fam

            elif rtype in ("user", "human"):
                content = msg.get("content") or []
                if not isinstance(content, list):
                    continue
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    tid = block.get("tool_use_id", "")
                    if block.get("type") == "tool_result" and tid in pending:
                        fam = pending.pop(tid)
                        result_text = _content_text(block.get("content", ""))
                        counts = [int(m) for m in FAILED_RE.findall(result_text)]
                        branch_runs[branch].append((fam, max(counts) if counts else 0))

    if not branch_runs:
        print("No test runs found for the specified branches.")
        return

    for branch in sorted(branch_runs):
        runs = branch_runs[branch]
        total = len(runs)
        failing = sum(1 for _, f in runs if f > 0)
        streak = _longest_fail_streak([f > 0 for _, f in runs])
        fail_rate = f"{100 * failing / total:.1f}%" if total else "—"

        fam_total: dict[str, int] = defaultdict(int)
        fam_fail: dict[str, int] = defaultdict(int)
        for fam, f in runs:
            fam_total[fam] += 1
            if f > 0:
                fam_fail[fam] += 1

        print(f"\n### {branch}")
        print(f"Total runs: {total}  Failing: {failing} ({fail_rate})  Longest consecutive-failing streak: {streak}")
        for fam in ("opus", "sonnet", "haiku", "other"):
            if fam_total[fam]:
                fr = f"{100 * fam_fail[fam] / fam_total[fam]:.1f}%"
                print(f"  {fam:<8}: {fam_total[fam]} runs, {fam_fail[fam]} failing ({fr})")
        print(f"Sequence: {' '.join(str(f) for _, f in runs)}")


def cmd_struggle(args: argparse.Namespace) -> None:
    branch_filter = _branch_filter(args)
    roots = _resolve_scan_roots(args)
    session_iter, scope_label = _resolve_project_scope(args, "struggle", roots=roots)
    _print_resolved_scope("struggle", scope_label, roots)

    branch_data: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for _jsonl, records in session_iter:
        last_fam: dict[str, str] = {}
        for rec in records:
            branch = rec.get("gitBranch") or ""
            if not branch or (branch_filter and branch not in branch_filter):
                continue
            if bool(rec.get("isSidechain")):
                continue
            rtype = rec.get("type", "")
            msg = rec.get("message") or {}

            if rtype == "assistant":
                last_fam[branch] = _fam(msg.get("model", ""))
            elif rtype in ("user", "human"):
                text = _content_text(msg.get("content", "")).lower()
                if any(phrase in text for phrase in STRUGGLE_PHRASES):
                    branch_data[branch][last_fam.get(branch, "unknown")] += 1

    if not branch_data:
        print("No struggle signals found.")
        return

    print(f"{'Branch':<40} {'Opus':>6} {'Sonnet':>7} {'Haiku':>6} {'Other':>6} {'Unknown':>8}")
    print("-" * 82)
    for branch in sorted(branch_data):
        d = branch_data[branch]
        print(
            f"{branch:<40} {d.get('opus', 0):>6} {d.get('sonnet', 0):>7} "
            f"{d.get('haiku', 0):>6} {d.get('other', 0):>6} {d.get('unknown', 0):>8}"
        )


def _is_fresh_user_prompt_for_narrative(rec: dict) -> bool:
    """Return True when rec is a genuine user keystroke — not a tool result or system injection.

    Distinct from `_is_fresh_user_prompt` (judgment-pair's discriminator): this one excludes
    on toolUseResult/sourceToolUseID/sourceToolAssistantUUID keys instead of tool_result content
    blocks, and accepts promptId-bearing list content instead of requiring a bare string.

    Accepts two content shapes:
    - Plain string (the common case).
    - List-of-blocks with extractable text and a promptId (text+image pastes, or list-shape
      prompts from older Claude Code versions). promptId is the positive signal that
      distinguishes these from isMeta injections that also use list-of-blocks content.
    """
    if rec.get("type") != "user":
        return False
    if rec.get("isMeta") or rec.get("isSidechain"):
        return False
    if "toolUseResult" in rec or "sourceToolUseID" in rec or "sourceToolAssistantUUID" in rec:
        return False
    content = (rec.get("message") or {}).get("content")
    if isinstance(content, str):
        return True
    if isinstance(content, list) and "promptId" in rec:
        return bool(_content_text(content).strip())
    return False


def _is_unrecognized_user_list_record(rec: dict) -> bool:
    """Return True for user records with list-of-blocks content not handled by any known discriminator.

    Known shapes explicitly excluded:
    - isMeta=True: skill/system injections (correctly excluded from prompts).
    - promptId present: accepted as fresh prompts by _is_fresh_user_prompt_for_narrative (list-content variant).
    - toolUseResult / sourceToolUseID / sourceToolAssistantUUID: tool-result records.

    A non-zero count from this function indicates a genuinely unexpected schema variant
    that the fresh-prompt discriminator may be silently missing.
    """
    return (
        rec.get("type") == "user"
        and isinstance(rec.get("message", {}).get("content"), list)
        and not rec.get("isMeta")
        and "promptId" not in rec
        and "toolUseResult" not in rec
        and "sourceToolUseID" not in rec
        and "sourceToolAssistantUUID" not in rec
    )


def _classify_prompt(text: str, is_initial: bool) -> tuple[str, str]:
    """Classify a fresh prompt as INITIAL, FOLLOWUP, or EXPLICIT_CORRECTION.

    Returns (classification, matched_phrase). matched_phrase is non-empty only
    for EXPLICIT_CORRECTION.
    """
    if is_initial:
        return "INITIAL", ""
    lowered = text.lower()
    for phrase in STRUGGLE_PHRASES:
        if phrase in lowered:
            return "EXPLICIT_CORRECTION", phrase
    return "FOLLOWUP", ""


def _attribute_model_to_prompt(records: list[dict], prompt_index: int, session_id: str) -> str:
    """Scan forward from prompt_index for the next assistant record sharing the session's ID.

    Returns the model family string, or 'unknown' when no attribution is found.
    The session ID is read from the prompt record itself so cross-session attribution
    is not possible.
    """
    prompt_rec = records[prompt_index]
    prompt_session_id = prompt_rec.get("sessionId") or ""
    for rec in records[prompt_index + 1 :]:
        if rec.get("type") != "assistant":
            continue
        if prompt_session_id and rec.get("sessionId") != prompt_session_id:
            continue
        model = (rec.get("message") or {}).get("model", "")
        if model:
            return _fam(model)
    return "unknown"


def _truncate_prompt_text(text: str, limit: int) -> str:
    """Truncate text to limit chars, appending an ellipsis annotation when truncated.

    limit=0 disables truncation entirely.
    """
    if limit == 0 or len(text) <= limit:
        return text
    return text[:limit] + f"… (truncated, {len(text)} chars total)"


def cmd_user_input(args: argparse.Namespace) -> None:
    """Per-session fresh user prompts, classified as INITIAL / FOLLOWUP / EXPLICIT_CORRECTION."""
    projects_glob = _projects_glob(args)
    branch_filter = _branch_filter(args)
    corrections_only: bool = bool(getattr(args, "corrections_only", False))
    _truncate_raw = getattr(args, "truncate_chars", None)
    truncate_chars: int = _truncate_raw if _truncate_raw is not None else 500
    out_path: str | None = getattr(args, "out", None) or None
    redact: bool = bool(getattr(args, "redact", False))

    since_str: str | None = getattr(args, "since", None) or None
    until_str: str | None = getattr(args, "until", None) or None
    since_ts: float | None = _parse_ts(f"{since_str}T00:00:00Z") if since_str else None
    until_epoch: float | None = None
    if until_str:
        day_start = _parse_ts(f"{until_str}T00:00:00Z")
        if day_start is not None:
            until_epoch = day_start + 86400

    redact_map: dict[str, str] = _build_redact_map() if redact else {}
    session_redact_map: dict[str, str] = {}

    # Shape-drift counter: user records with list content missing all tool-result keys.
    unrecognized_shape_count = 0

    # Collected session data for rendering, sorted by first-prompt timestamp.
    # Each entry: {proj_label, branch, date, session_id_prefix, prompts: [...], first_ts}
    session_entries: list[dict] = []

    # Corpus-level counters.
    total_projects_seen: set[str] = set()
    total_session_count = 0
    total_fresh_prompts = 0
    initial_count = 0
    followup_count = 0
    correction_count = 0
    phrase_hits: dict[str, int] = defaultdict(int)
    earliest_ts: float | None = None
    latest_ts: float | None = None

    for jsonl, records in iter_sessions(PROJECTS_DIR, projects_glob):
        proj_label = _derive_proj_label(jsonl)
        total_projects_seen.add(proj_label)

        # Count unrecognized shapes regardless of other filters.
        for rec in records:
            if _is_unrecognized_user_list_record(rec):
                unrecognized_shape_count += 1

        # Collect fresh prompts for this session, applying all filters.
        session_prompts: list[dict] = []
        is_first_in_session = True

        for idx, rec in enumerate(records):
            if not _is_fresh_user_prompt_for_narrative(rec):
                continue

            # Branch filter
            branch = rec.get("gitBranch") or ""
            if branch_filter and branch not in branch_filter:
                continue

            # Date filter
            rec_ts = _parse_ts(rec.get("timestamp"))
            if since_ts is not None or until_epoch is not None:
                if rec_ts is None:
                    continue
                if since_ts is not None and rec_ts < since_ts:
                    continue
                if until_epoch is not None and rec_ts >= until_epoch:
                    continue

            text = _content_text(rec["message"]["content"])
            classification, matched_phrase = _classify_prompt(text, is_first_in_session)
            is_first_in_session = False

            model_fam = _attribute_model_to_prompt(records, idx, rec.get("sessionId") or "")

            date_str = _fmt_date(rec_ts) if rec_ts is not None else "?"
            time_str = (
                datetime.fromtimestamp(rec_ts, tz=UTC).strftime("%H:%M")
                if rec_ts is not None
                else "?"
            )

            session_prompts.append({
                "text": text,
                "date": date_str,
                "time": time_str,
                "ts": rec_ts,
                "branch": branch,
                "session_id": rec.get("sessionId") or jsonl.stem,
                "classification": classification,
                "matched_phrase": matched_phrase,
                "model_fam": model_fam,
            })

        if not session_prompts:
            continue

        total_session_count += 1
        first_ts = session_prompts[0]["ts"]
        if first_ts is not None:
            if earliest_ts is None or first_ts < earliest_ts:
                earliest_ts = first_ts
            last_ts = session_prompts[-1]["ts"]
            if last_ts is not None and (latest_ts is None or last_ts > latest_ts):
                latest_ts = last_ts

        # Accumulate corpus counters.
        for p in session_prompts:
            total_fresh_prompts += 1
            if p["classification"] == "INITIAL":
                initial_count += 1
            elif p["classification"] == "FOLLOWUP":
                followup_count += 1
            elif p["classification"] == "EXPLICIT_CORRECTION":
                correction_count += 1
                if p["matched_phrase"]:
                    phrase_hits[p["matched_phrase"]] += 1

        # Derive session-level metadata.
        session_branch = session_prompts[0]["branch"]
        session_date = session_prompts[0]["date"]
        raw_session_id = session_prompts[0]["session_id"]
        if redact:
            _assign_session_redact_label(raw_session_id, session_redact_map)
            session_id_prefix = _redact_session_id(raw_session_id, session_redact_map)
        else:
            session_id_prefix = raw_session_id[:8]
        session_models = sorted({p["model_fam"] for p in session_prompts})
        session_correction_count = sum(1 for p in session_prompts if p["classification"] == "EXPLICIT_CORRECTION")
        session_followup_count = sum(1 for p in session_prompts if p["classification"] == "FOLLOWUP")
        display_label = _redact_proj_label(proj_label, redact_map) if redact else proj_label

        session_entries.append({
            "proj_label": display_label,
            "branch": session_branch,
            "date": session_date,
            "first_ts": first_ts,
            "session_id_prefix": session_id_prefix,
            "session_models": session_models,
            "prompts": session_prompts,
            "correction_count": session_correction_count,
            "followup_count": session_followup_count,
        })

    # Sort sessions by first-prompt timestamp ascending.
    session_entries.sort(key=lambda e: (e["first_ts"] or 0.0))

    # Build top-5 struggle-phrase list.
    top_phrases = sorted(phrase_hits.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
    top_phrases_str = ", ".join(f'"{ph}" ({n})' for ph, n in top_phrases) if top_phrases else "none"

    date_range_str = (
        f"{_fmt_date(earliest_ts)} → {_fmt_date(latest_ts)}"
        if earliest_ts is not None and latest_ts is not None
        else "no data"
    )

    lines: list[str] = []
    lines.append("# User Input — Conversation Narrative")
    lines.append("")
    lines.append(f"Generated: {_fmt_date(time.time())}")
    lines.append(
        f"Scope: {len(total_projects_seen)} projects, {total_session_count} sessions, "
        f"{total_fresh_prompts} fresh prompts"
    )
    lines.append(f"Date range: {date_range_str}")
    lines.append("")
    lines.append("## Summary")
    lines.append(f"- Fresh prompts: {total_fresh_prompts}")
    lines.append(f"- Initial: {initial_count}")
    lines.append(f"- Followups (quiet redirects): {followup_count}")
    lines.append(f"- Explicit corrections (struggle-phrase match): {correction_count}")
    lines.append(f"- Top struggle phrases: {top_phrases_str}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Sessions")

    for entry in session_entries:
        lines.append("")
        lines.append(f"### {entry['proj_label']} · {entry['branch']} · {entry['date']}")
        models_str = ", ".join(entry["session_models"])
        prompt_count = len(entry["prompts"])
        lines.append(
            f"Session `{entry['session_id_prefix']}` · models: {models_str} · "
            f"{prompt_count} prompt{'s' if prompt_count != 1 else ''} "
            f"({entry['correction_count']} explicit correction{'s' if entry['correction_count'] != 1 else ''}, "
            f"{entry['followup_count']} followup{'s' if entry['followup_count'] != 1 else ''})"
        )

        for prompt in entry["prompts"]:
            classification = prompt["classification"]
            if corrections_only and classification == "INITIAL":
                continue

            lines.append("")
            if classification == "EXPLICIT_CORRECTION":
                lines.append(
                    f"**[{prompt['time']} · {classification} · {prompt['model_fam']}]**"
                    f" (matched: \"{prompt['matched_phrase']}\")"
                )
            else:
                lines.append(f"**[{prompt['time']} · {classification} · {prompt['model_fam']}]**")
            lines.append("~~~text")
            lines.append(_truncate_prompt_text(prompt["text"], truncate_chars))
            lines.append("~~~")

    output = "\n".join(lines) + "\n"

    # Shape-audit line always printed to stderr so it doesn't pollute --out file content.
    print(f"Shape audit: {unrecognized_shape_count} unrecognized user records skipped", file=sys.stderr)

    if out_path:
        try:
            Path(out_path).write_text(output, encoding="utf-8")
            print(f"Wrote output to {out_path}")
        except OSError as exc:
            print(f"user-input: failed to write {out_path}: {exc}", file=sys.stderr)
            sys.exit(1)
    else:
        print(output, end="")


def cmd_duration(args: argparse.Namespace) -> None:
    branch_filter = _branch_filter(args)
    gap_secs: int = (getattr(args, "gap_minutes", None) or 30) * 60
    roots = _resolve_scan_roots(args)
    session_iter, scope_label = _resolve_project_scope(args, "duration", roots=roots)
    _print_resolved_scope("duration", scope_label, roots)

    branch_timestamps: dict[str, list[float]] = defaultdict(list)

    for _jsonl, records in session_iter:
        for rec in records:
            branch = rec.get("gitBranch") or ""
            if not branch or (branch_filter and branch not in branch_filter):
                continue
            ts = _parse_ts(rec.get("timestamp"))
            if ts is not None:
                branch_timestamps[branch].append(ts)

    if not branch_timestamps:
        print("No timestamp data found.")
        return

    print(f"{'Branch':<40} {'Span(min)':>10} {'Active(min)':>11} {'Idle(min)':>10} {'Sessions':>9} {'GapMin':>7}")
    print("-" * 95)
    for branch in sorted(branch_timestamps):
        tss = sorted(branch_timestamps[branch])
        if len(tss) < 2:
            continue
        span_secs = tss[-1] - tss[0]
        idle_gaps = [tss[i + 1] - tss[i] for i in range(len(tss) - 1) if tss[i + 1] - tss[i] > gap_secs]
        idle_secs = sum(idle_gaps)
        active_secs = span_secs - idle_secs
        session_count = len(idle_gaps) + 1
        print(
            f"{branch:<40} {span_secs / 60:>10.0f} {active_secs / 60:>11.0f} "
            f"{idle_secs / 60:>10.0f} {session_count:>9} {gap_secs / 60:>7.0f}"
        )


def _count_subagent_spawns(records: list[dict]) -> int:
    """Count main-thread subagent spawn tool_uses (Agent/Task) across all records.

    Used corpus-wide (ignoring branch filter) as one side of the format-drift
    cross-check: spawns > 0 with zero isSidechain turns read is the drift signature.
    Note: isSidechain records (from subagent files) are excluded by design — nested
    subagent spawns are not counted here.
    """
    count = 0
    for rec in records:
        if rec.get("type") != "assistant" or bool(rec.get("isSidechain")):
            continue
        for block in ((rec.get("message") or {}).get("content") or []):
            if (
                isinstance(block, dict)
                and block.get("type") == "tool_use"
                and block.get("name") in _SPAWN_TOOL_NAMES
            ):
                count += 1
    return count


def _warn_if_subagent_format_drift(total_spawns: int, total_sidechain_turns: int) -> None:
    """Emit a stderr warning when the drift signature is detected.

    Drift signature: spawns recorded in the main thread but zero isSidechain
    assistant turns read after include_subagents merge. Catches both failure
    modes — the subagents/ path relocating (files never read → 0 turns) and a
    field rename (files read but the filter matches 0).

    This is the runtime half of the two-layer guard:
      - Contract test (CI): pins what our code expects from fixtures.
      - This canary (runtime): validates expectation against live on-disk data.
    """
    if total_spawns > 0 and total_sidechain_turns == 0:
        print(
            "WARNING: subagent spawns detected in main thread but zero isSidechain "
            f"assistant turns were read from '{SUBAGENT_SUBDIR}/' subdirectories. "
            "The Claude Code transcript format may have drifted — check that "
            f"subagent files still live under <session>/{SUBAGENT_SUBDIR}/*.jsonl "
            "and that records still carry 'isSidechain': true.",
            file=sys.stderr,
        )


def cmd_subagents(args: argparse.Namespace) -> None:
    """isSidechain turn counts and model split per branch, plus total
    tool-result text bytes per thread-type (main vs. sidechain) per
    branch — a measured signal for whether verbose tool output is being
    delegated to subagents (see the subagent-delegation skill) rather than
    accumulated in the main thread's own prefix. Byte totals cover only
    text-typed tool-result blocks (via _content_text) — non-text blocks
    (e.g. images) are not counted. Byte totals are aggregate only: no
    tool-result content, file paths, session IDs, or cwd are ever printed.
    A second table breaks those same bytes down by the tool name that
    produced them (Read, Bash, Agent, …), still aggregate-only and with
    every mcp__<server>__<tool> name collapsed into one _MCP_TOOL_BUCKET_LABEL
    row — an MCP server name is a per-account integration identifier.

    --since limits both tables to records with a timestamp on or after the
    window start; the corpus-wide spawn and sidechain-turn counters feeding
    _warn_if_subagent_format_drift are read before this filter and are never
    narrowed by it, so a narrow --since window cannot manufacture a false
    format-drift warning. --config-dir (repeatable) scans additional Claude
    Code config directories the same way cost does; under more than one root,
    branch names are redacted (via _assign_root_scoped_redact_label, account-<K>/
    branch-<N>) since a raw branch slug from a foreign account would
    otherwise be printed, and _DO_NOT_PUBLISH_BANNER is stamped on stdout
    and stderr.
    """
    roots = _resolve_cost_roots(args, "subagents")
    multi_root = len(roots) > 1
    branch_filter = _branch_filter(args)
    since_ts, _since_raw = _parse_since_nd_arg(args, "subagents")

    if multi_root:
        print(_DO_NOT_PUBLISH_BANNER)
        print(_DO_NOT_PUBLISH_BANNER, file=sys.stderr)

    session_iter, scope_label = _resolve_project_scope(
        args, "subagents", include_subagents=True, roots=roots
    )
    _print_resolved_scope("subagents", scope_label, roots)

    resolved_roots = [root.resolve() for root in roots] if multi_root else []
    # Resolved-path-sorted, not _root_index_for_path's raw scan-order position
    # — the same physical root must read as the same account-N here as in
    # every other multi-root diagnostic in this file (_build_redact_map,
    # cost's per-row key), regardless of which profile is currently active.
    redact_ordinals: dict[Path, int] = _redaction_ordinals(roots) if multi_root else {}
    branch_redact_map: dict[tuple[int, str], str] = {}

    # Keyed on (root_index_or_None, raw gitBranch) — root_index is always None
    # under single-root scope (the common case, unchanged from before
    # --config-dir existed); a real index under multi-root keeps two
    # accounts' identically-named branch from merging into one row. This
    # index is scan-order, purely for in-run grouping — the printed label
    # (_branch_label, below) translates it through redact_ordinals before
    # ever reaching output.
    branch_data: dict[tuple[int | None, str], dict[str, dict[str, int]]] = defaultdict(
        lambda: {"main": defaultdict(int), "sidechain": defaultdict(int)}
    )
    branch_bytes: dict[tuple[int | None, str], dict[str, int]] = defaultdict(
        lambda: {"main": 0, "sidechain": 0}
    )
    branch_tool_bytes: dict[tuple[int | None, str], dict[str, dict[str, int]]] = defaultdict(
        lambda: {"main": defaultdict(int), "sidechain": defaultdict(int)}
    )
    corpus_spawns = 0
    corpus_sidechain_turns = 0

    for jsonl, records in session_iter:
        root_idx = _root_index_for_path(jsonl, resolved_roots) if multi_root else None
        records = _dedup_turns_by_request_id(records)
        corpus_spawns += _count_subagent_spawns(records)
        # tool_use id -> tool name, built inline as records are walked in
        # order: a tool_result always follows its own tool_use within the
        # same file, and include_subagents=True appends each subagent file
        # as a contiguous block after the main file, so one sequential pass
        # (no second corpus pass) is enough to pair every tool_result seen
        # below with the tool name that produced it.
        tool_use_names: dict[str, str] = {}
        for rec in records:
            rec_type = rec.get("type")
            if rec_type == "assistant":
                # corpus_sidechain_turns counts every isSidechain assistant
                # turn read, before the branch filter below — it feeds
                # _warn_if_subagent_format_drift's corpus-wide sanity check,
                # not the per-branch table, so it must not be filtered.
                if bool(rec.get("isSidechain")):
                    corpus_sidechain_turns += 1
                for block in ((rec.get("message") or {}).get("content") or []):
                    if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("id"):
                        tool_use_names[block["id"]] = block.get("name") or "unknown"
                branch = rec.get("gitBranch") or ""
                if not branch or (branch_filter and branch not in branch_filter):
                    continue
                if since_ts is not None:
                    rec_ts = _parse_ts(rec.get("timestamp"))
                    if rec_ts is None or rec_ts < since_ts:
                        continue
                fam = _fam((rec.get("message") or {}).get("model", ""))
                thread = "sidechain" if bool(rec.get("isSidechain")) else "main"
                branch_data[(root_idx, branch)][thread][fam] += 1
            elif rec_type == "user":
                branch = rec.get("gitBranch") or ""
                if not branch or (branch_filter and branch not in branch_filter):
                    continue
                if since_ts is not None:
                    rec_ts = _parse_ts(rec.get("timestamp"))
                    if rec_ts is None or rec_ts < since_ts:
                        continue
                thread = "sidechain" if bool(rec.get("isSidechain")) else "main"
                content = (rec.get("message") or {}).get("content") or []
                if not isinstance(content, list):
                    continue
                for block in content:
                    if not isinstance(block, dict) or block.get("type") != "tool_result":
                        continue
                    key = (root_idx, branch)
                    nbytes = len(_content_text(block.get("content", "")).encode())
                    branch_bytes[key][thread] += nbytes
                    tool_name = tool_use_names.get(block.get("tool_use_id") or "", "unknown")
                    if tool_name.startswith("mcp__"):
                        tool_name = _MCP_TOOL_BUCKET_LABEL
                    branch_tool_bytes[key][thread][tool_name] += nbytes

    _warn_if_subagent_format_drift(corpus_spawns, corpus_sidechain_turns)

    if not branch_data and not branch_bytes:
        print("No data found.")
        return

    def _branch_label(key: tuple[int | None, str]) -> str:
        root_idx, branch = key
        return (
            _assign_root_scoped_redact_label(
                "branch", redact_ordinals[resolved_roots[root_idx]], branch, branch_redact_map
            )
            if root_idx is not None
            else branch
        )

    print(
        f"{'Branch':<40} {'Thread':<10} {'Opus':>6} {'Sonnet':>7} {'Haiku':>6} {'Other':>6}"
        f" {'Bytes':>18}"
    )
    print("-" * 99)
    for key in sorted(set(branch_data) | set(branch_bytes)):
        label = _branch_label(key)
        first = True
        for thread in ("main", "sidechain"):
            d = branch_data[key][thread]
            bytes_total = branch_bytes[key][thread]
            if not any(d.values()) and not bytes_total:
                continue
            row_label = label if first else ""
            first = False
            print(
                f"{row_label:<40} {thread:<10} {d.get('opus', 0):>6} {d.get('sonnet', 0):>7} "
                f"{d.get('haiku', 0):>6} {d.get('other', 0):>6} {bytes_total:>18,}"
            )

    if any(any(tb.values()) for by_thread in branch_tool_bytes.values() for tb in by_thread.values()):
        # Header says "Side", not "Thread" (unlike the table above): several
        # existing tests anchor _table_cols on header_contains="Thread" and
        # require it to match exactly one printed line.
        print(f"\n{'Branch':<40} {'Side':<10} {'Tool':<20} {'Bytes':>18}")
        print("-" * 92)
        for key in sorted(branch_tool_bytes):
            label = _branch_label(key)
            first = True
            for thread in ("main", "sidechain"):
                tool_bytes = branch_tool_bytes[key][thread]
                for tool_name in sorted(tool_bytes, key=lambda t: (-tool_bytes[t], t)):
                    nbytes = tool_bytes[tool_name]
                    if not nbytes:
                        continue
                    row_label = label if first else ""
                    first = False
                    print(f"{row_label:<40} {thread:<10} {tool_name:<20} {nbytes:>18,}")


REVIEW_SKILLS: tuple[str, ...] = ("code-review", "plan-review", "ready-for-review")

# Subdirectory name where Claude Code writes split subagent transcripts.
SUBAGENT_SUBDIR = "subagents"

# Tool names that spawn a subagent in the main thread.
_SPAWN_TOOL_NAMES = ("Agent", "Task")

# subagents' tool-result byte grouping bucket for every mcp__<server>__<tool>
# tool name — an MCP server name is a per-account integration identifier, so
# every MCP tool call collapses into this one row instead of one row per server.
_MCP_TOOL_BUCKET_LABEL = "mcp__*"

# subagent-mix's model-mix table bucket for a dispatch whose meta.json carries
# no "model" key at all (no explicit model was requested).
_UNREQUESTED_MODEL_LABEL = "(none)"

# Skills counted as review invocations in review-trace.
REVIEW_TRACE_SKILLS: frozenset[str] = frozenset(
    {"code-review", "plan-review", "ready-for-review", "skill-review", "agent-review", "plan-it"}
)

# Skills that open a judgment span in audit-routing: any turn within an active span
# (from skill invocation until the next user turn) is classified as `judgment`, not
# by its tool-use contents. Extends REVIEW_TRACE_SKILLS with security-review,
# respond-pr, and ultrareview.
AUDIT_JUDGMENT_SKILLS: frozenset[str] = frozenset({
    "code-review", "plan-review", "ready-for-review", "skill-review",
    "agent-review", "security-review", "respond-pr", "ultrareview", "plan-it",
})

# Reviewer-agent subagent_type prefixes/names counted in review-trace and
# reviewer-yield. Exact-name reviewers don't share the staff- prefix:
# ciso-reviewer and comment-discipline-reviewer are Change-type-table
# dispatch targets; skill-fidelity-reviewer is spawned only by
# ready-for-review and carries no Item-ownership row of its own.
_REVIEWER_PREFIX = "staff-"
_REVIEWER_EXACT_NAMES: frozenset[str] = frozenset(
    {"ciso-reviewer", "comment-discipline-reviewer", "skill-fidelity-reviewer"}
)

# Shared bound for every hook-name/label capture below (detection and
# extraction alike): a name-shaped character class (word chars, spaces, '.',
# '-') capped at this many characters, matching every hook's own static
# "<name> hook/gate" wording — never an unbounded `.+?`, which would echo
# arbitrary denial-message text (a dynamic file path, say) into
# --deny-summary's output if a future hook ever interpolated one into this
# span.
_DENIAL_HOOK_NAME_MAX_CHARS = 40

# Current-format transcripts record a hook denial as an is_error tool_result,
# distinguishable from an ordinary tool error only by the deny message text —
# hook_denial_key deliberately does not read the parent user record's
# toolDenialKind field, a separate friction-class axis classified by
# _is_nongate_friction_kind below. These patterns match the Claude Code
# hook-denial idiom ("Blocked by <hook>", "blocked by <X> gate", "… invocation
# denied", "<name> gate: …" / "<name> hook: …" — a hook stating its own label
# directly, e.g. "Skill length gate: ..."). Detection is therefore best-effort
# in both directions: an atypically worded hook denial is missed, and an
# ordinary tool error whose text happens to contain the idiom is a false
# positive. review-trace is a candidate locator, not an exact counter —
# callers treat denial counts as approximate. Legacy transcripts additionally
# carry an explicit
# hook_blocking_error attachment record, matched separately and exactly.
_HOOK_DENIAL_SIGNATURE = re.compile(
    r"blocked by .{0,80}?\b(?:hook|gate)\b"
    r"|invocation denied\b"
    rf"|[\w .-]{{1,{_DENIAL_HOOK_NAME_MAX_CHARS}}}\s+(?:hook|gate):",
    re.IGNORECASE | re.DOTALL,
)


def _normalize_blocking_error(raw) -> dict | str:
    """Normalize blockingError — may arrive as a dict or a JSON-stringified dict."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return raw
        if isinstance(parsed, dict):
            return parsed
        return raw
    return raw if raw is not None else {}


def hook_denial_key(item: dict) -> tuple[str, dict | str] | None:
    """Return (tool_use_id, extra) if `item` is a hook denial, else None.

    Detects both denial shapes: a legacy `attachment` record (`item["type"] ==
    "attachment"` with a nested `hook_blocking_error`), or a current-format
    `tool_result` block (`item["type"] == "tool_result"`) carrying `is_error`
    and text matching _HOOK_DENIAL_SIGNATURE. An empty string is a valid
    tool_use_id — a denial whose transcript recorded no tool_use_id; only
    None means "not a denial". `extra` is the already-fetched data this
    predicate needed internally to classify the item — the `attachment`
    dict for the legacy shape, the decoded content text for the tool_result
    shape — returned so callers building an event/message don't re-fetch or
    re-decode it. Callers own the seen-id dedup set and the event/message
    construction; this predicate only classifies.
    """
    item_type = item.get("type")
    if item_type == "attachment":
        att = item.get("attachment") or {}
        if att.get("type") != "hook_blocking_error":
            return None
        return att.get("toolUseID") or "", att
    if item_type == "tool_result":
        if not item.get("is_error"):
            return None
        message = _content_text(item.get("content"))
        if not _HOOK_DENIAL_SIGNATURE.search(message):
            return None
        return item.get("tool_use_id") or "", message
    return None


# Extracts the hook/gate name from a denial message's own "blocked by <name>
# hook/gate" wording — every hook's emit_deny call already writes this shape
# (e.g. "Blocked by code-review gate: ..."), so the name is read off the
# denial text itself rather than an invented category label.
_DENIAL_HOOK_NAME_RE = re.compile(
    rf"blocked by (?P<name>[\w .-]{{1,{_DENIAL_HOOK_NAME_MAX_CHARS}}}?)\s+(?:hook|gate)\b", re.IGNORECASE
)

# Two wordings hooks emit that the "blocked by <name> hook/gate" idiom above
# doesn't cover: enforce-marker-script-shape.sh's path-traversal and
# shape-mismatch denials name their own script directly ("marker.sh
# invocation denied ..."), and several hooks state their own label as the
# message's own prefix rather than via "blocked by" (e.g. check-skill-length.sh's
# "Skill length gate: ..."). Both inherit the same bounded character class and
# _DENIAL_HOOK_NAME_MAX_CHARS cap as the pattern above.
_DENIAL_HOOK_NAME_INVOCATION_DENIED_RE = re.compile(
    rf"(?P<name>[\w .-]{{1,{_DENIAL_HOOK_NAME_MAX_CHARS}}}?)\s+invocation denied\b", re.IGNORECASE
)
_DENIAL_HOOK_NAME_COLON_RE = re.compile(
    rf"(?P<name>[\w .-]{{1,{_DENIAL_HOOK_NAME_MAX_CHARS}}}?)\s+(?:hook|gate):", re.IGNORECASE
)

# The hand-maintained set of prose labels hooks/*.sh actually emits — sourced
# by grepping every hook's emit_deny call, not from hooks/*.sh basenames
# (which match none of these; see the module-level docstring for why). A
# captured name is trusted only if it's a member of this set; anything else
# (a coincidental match, an unanticipated wording, an unbounded interpolated
# value that happened to survive the character-class bound) falls to
# _DENY_SUMMARY_UNMATCHED_HOOK rather than being echoed verbatim. Regression
# coverage: TestDenialHookLabelEnumeration in test_transcript_analysis.py
# drives each hook's real deny-path wording and asserts the label it
# produces is a member here, so a hook's wording change or a new hook shows
# up as a test failure rather than a silently stale set.
_DENIAL_HOOK_LABELS: frozenset[str] = frozenset({
    # "blocked by <name> hook/gate" — one entry per hooks/*.sh label.
    "gh-pr-merge",  # block-gh-pr-merge.sh:49
    "CLAUDE.md length",  # check-claude-md-length.sh:42
    "skill length",  # check-skill-length.sh:41
    "credential-path Bash",  # deny-credential-bash-reads.sh:27
    "credential-file read",  # deny-credential-file-reads.sh:27
    "data-file read",  # deny-data-file-reads.sh:65
    "env-read",  # deny-env-reads.sh:47
    "backtick-escape",  # deny-escaped-backticks-in-pr-body.sh:46
    "network-install",  # deny-network-installs.sh:40
    "PII commit",  # deny-pii-in-commits.sh:127
    "redaction",  # deny-private-project-refs.sh:180
    "repo-relocation",  # deny-repo-relocation.sh:63
    "reviewer-tree-mutation",  # deny-reviewer-tree-mutation.sh:146
    "marker-script-shape",  # enforce-marker-script-shape.sh:68
    "settings session-keys",  # guard-settings-session-keys.sh:49
    "code-review",  # require-code-review.sh:48
    "memory-skill",  # require-memory-skill.sh:59
    "ai-instruction-and-memory-files",  # require-memory-skill.sh:125
    "plan-review",  # require-plan-review.sh:66
    "plan-review routing",  # require-routing-read.sh:68
    "ready-for-review",  # require-ready-for-review.sh:80
    "respond-pr",  # require-respond-pr.sh:69
    "routing-read",  # require-routing-read.sh:27
    "stow-reminder",  # require-stow-reminder.sh:71
    "worktree-enforcement",  # require-worktree-for-file-writes.sh:50, require-worktree-for-git-writes.sh:91
    # "<name> invocation denied" (_DENIAL_HOOK_NAME_INVOCATION_DENIED_RE).
    "marker.sh",  # enforce-marker-script-shape.sh:277,353
    # "<name> gate:"/"<name> hook:" (_DENIAL_HOOK_NAME_COLON_RE) — a hook
    # stating its own label as the message's own prefix. check-claude-md-length.sh:85's
    # message reads "CLAUDE.md/AGENTS.md length gate: ..."; '/' isn't in the
    # name-shaped class, so only the AGENTS.md half of the label survives.
    "AGENTS.md length",  # check-claude-md-length.sh:85
    "Skill length",  # check-skill-length.sh:87
})

# --deny-summary's unmatched-hook-name bucket: a denial matched by
# _HOOK_DENIAL_SIGNATURE (e.g. via the "invocation denied" alternative, which
# names no hook) but from which no enumerated hook/gate name can be extracted.
_DENY_SUMMARY_UNMATCHED_HOOK = "unmatched"

# --deny-summary's attempted-command-shape classifier: an allowlist, not a
# free-text sanitizer. A command failing to normalize into one of the
# multiplexer shapes below falls into "other".
_DENY_SUMMARY_OTHER_COMMAND_SHAPE = "other"

# Strips a leading NAME=VALUE environment-assignment prefix (one such prefix
# is observed in the corpus, wrapping a marker.sh invocation with a live
# per-machine token) before any other normalization runs, so that token never
# reaches printed output.
_DENIAL_COMMAND_ENV_PREFIX_RE = re.compile(r"^(?:[A-Za-z_][A-Za-z0-9_]*=\S+\s+)+")

# The multiplexer commands the corpus is dominated by — only these get a
# command+subcommand shape; every other command shape, including an empty
# command, falls to "other". "marker.sh" is matched post-basename, since the
# real invocation is always a tilde or absolute script path.
_DENIAL_COMMAND_MULTIPLEXERS: frozenset[str] = frozenset({"git", "gh", "marker.sh"})

# Per-multiplexer closed allowlist of real subcommands --deny-summary trusts
# in the printed "<multiplexer> <subcommand>" shape — same discipline as
# _DENIAL_HOOK_LABELS: a candidate subcommand token that isn't a member (a
# credential-shaped string, a path, an unenumerated wording) falls to "other"
# rather than being echoed verbatim. Sourced from the corpus's observed
# denied invocations plus _LIB_READONLY_GIT_SUBCMDS in hooks/_lib.sh for the
# git read-only entries; new entries are added deliberately, not accreted.
_DENIAL_COMMAND_SUBCOMMANDS: dict[str, frozenset[str]] = {
    "git": frozenset({
        "add", "checkout", "commit", "config", "diff", "fetch", "init", "log",
        "merge", "pull", "push", "restore", "rev-parse", "show", "status",
        "symbolic-ref",
    }),
    "gh": frozenset({"api", "auth", "issue", "pr"}),
    "marker.sh": frozenset({"activate", "clear-stale", "deactivate", "status", "write"}),
}

# Second layer beyond the allowlist above, matching _DENIAL_HOOK_NAME_MAX_CHARS's
# defense-in-depth pattern: bounds a future malformed allowlist entry rather
# than serving as the primary defense, which is allowlist membership itself.
_DENIAL_COMMAND_SUBCOMMAND_MAX_CHARS = 20
_DENIAL_COMMAND_SUBCOMMAND_RE = re.compile(rf"^[\w-]{{1,{_DENIAL_COMMAND_SUBCOMMAND_MAX_CHARS}}}$")

# git flags that take their value as the following token — dropping (not
# skipping) both the flag and its value keeps the value from being misread
# as the subcommand, e.g. "git -C <path> commit" would otherwise leave
# <path> at index 1 once "-C" alone is skipped, so a naive scan reads <path>
# as the subcommand instead of "commit" at index 2. -C is
# require-worktree-for-git-writes.sh's own resolution mechanism for a
# compliant worktree write, so it's the dominant separate-token form in the
# worktree-enforcement denial category.
_DENIAL_COMMAND_FLAGS_WITH_SEPARATE_VALUE: frozenset[str] = frozenset({"-C", "-c", "--git-dir", "--work-tree"})

# git flags whose value is glued to the flag by "=" — the value already
# lives inside this one token, so nothing further needs dropping.
_DENIAL_COMMAND_FLAG_VALUE_ATTACHED_PREFIXES: tuple[str, ...] = ("--git-dir=", "--work-tree=")


def _drop_denial_command_flag_values(tokens: list[str]) -> list[str]:
    """Drop (not skip) the values of git's value-taking repo-selection flags.

    A separate-token flag (-C, -c, --git-dir, --work-tree) consumes itself
    and the token after it; an =-attached flag (--git-dir=<path>,
    --work-tree=<path>) consumes only itself, since its value is already
    inside that token. Skipping a flag without dropping its value would
    leave the value in place to be misread as the subcommand.
    """
    kept: list[str] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token in _DENIAL_COMMAND_FLAGS_WITH_SEPARATE_VALUE:
            i += 2  # drop the flag token and its value token
            continue
        if token.startswith(_DENIAL_COMMAND_FLAG_VALUE_ATTACHED_PREFIXES):
            i += 1  # value is glued to this token; nothing further to drop
            continue
        kept.append(token)
        i += 1
    return kept

# Extraction patterns tried in order against a current-shape denial message;
# the first to yield a name in _DENIAL_HOOK_LABELS wins.
_DENIAL_HOOK_NAME_PATTERNS: tuple[re.Pattern, ...] = (
    _DENIAL_HOOK_NAME_RE,
    _DENIAL_HOOK_NAME_INVOCATION_DENIED_RE,
    _DENIAL_HOOK_NAME_COLON_RE,
)


def _denial_hook_label(hook_name: str, message: str) -> str:
    """Return the originating hook/gate name for one denial event.

    Legacy-shape denials carry the name directly (hook_name, from the
    attachment record's hookName field); current-shape denials carry no
    structured hook identity, so the name is extracted from the denial
    message text via each pattern in _DENIAL_HOOK_NAME_PATTERNS in turn.
    Either source is trusted only if the candidate is a member of
    _DENIAL_HOOK_LABELS — an unenumerated hookName (legacy transcripts predate
    this bound entirely) or an unenumerated extracted candidate both fall to
    _DENY_SUMMARY_UNMATCHED_HOOK rather than being echoed verbatim.
    """
    candidate = (hook_name or "").strip()
    if candidate:
        return candidate if candidate in _DENIAL_HOOK_LABELS else _DENY_SUMMARY_UNMATCHED_HOOK
    for pattern in _DENIAL_HOOK_NAME_PATTERNS:
        m = pattern.search(message)
        if m is None:
            continue
        name = m.group("name").strip().removeprefix("the ")
        if name in _DENIAL_HOOK_LABELS:
            return name
    return _DENY_SUMMARY_UNMATCHED_HOOK


def _denial_command_shape(command: str) -> str:
    """Classify a denied Bash command's shape for --deny-summary.

    Normalizes before matching, in order: strips a leading NAME=VALUE
    environment assignment, basenames the first token (an absolute script
    path is a home-rooted path, one of this repo's six always-on structural
    redaction detectors), and drops the values of git's repo-selection flags
    (see _drop_denial_command_flag_values). Only a multiplexer command
    (_DENIAL_COMMAND_MULTIPLEXERS) gets a command+subcommand shape, and only
    when the candidate subcommand token doesn't itself look like a flag — an
    unenumerated flag (one _drop_denial_command_flag_values doesn't know
    about) is left in place rather than dropped, so this guards it from
    being read as, and printed as, the subcommand — and is itself a member of
    that multiplexer's _DENIAL_COMMAND_SUBCOMMANDS allowlist, so an
    unenumerated non-flag token (a credential-shaped string, a raw control
    byte) falls to "other" instead of being echoed verbatim. Anything else,
    including an empty command, falls to "other". Nothing past the
    subcommand token is ever printed, so an argument value — a commit
    message, a path, a control character — never survives to stdout.
    """
    stripped = _DENIAL_COMMAND_ENV_PREFIX_RE.sub("", command)
    try:
        tokens = shlex.split(stripped)
    except ValueError:
        tokens = stripped.split()
    if not tokens:
        return _DENY_SUMMARY_OTHER_COMMAND_SHAPE
    tokens[0] = os.path.basename(tokens[0])
    tokens = _drop_denial_command_flag_values(tokens)
    if len(tokens) > 1 and tokens[0] in _DENIAL_COMMAND_MULTIPLEXERS and not tokens[1].startswith("-"):
        subcommand = tokens[1]
        allowed_subcommands = _DENIAL_COMMAND_SUBCOMMANDS.get(tokens[0], frozenset())
        if subcommand in allowed_subcommands and _DENIAL_COMMAND_SUBCOMMAND_RE.match(subcommand):
            return f"{tokens[0]} {subcommand}"
    return _DENY_SUMMARY_OTHER_COMMAND_SHAPE


# toolDenialKind's gate-axis value — a permission-layer denial (hook denial or
# allowlist miss), already covered by hook_denial_key's message-signature
# match. The four other values (user-rejected, automode-blocked,
# automode-unavailable, interrupted) are friction, not a gate denial.
_GATE_TOOL_DENIAL_KIND = "permission-rule"

# The date toolDenialKind first appears in the corpus (the corpus itself
# starts 2026-06-24), determined by a corpus scan rather than a documented
# Claude Code rollout date — a user/tool_result record timestamped before
# this date structurally cannot carry the field, so --deny-summary's
# friction-kind breakdown must not read a pre-regime record's absent kind as
# zero friction.
_TOOL_DENIAL_KIND_REGIME_START = "2026-07-20"
_TOOL_DENIAL_KIND_REGIME_START_TS = _parse_ts(f"{_TOOL_DENIAL_KIND_REGIME_START}T00:00:00Z")


def _is_nongate_friction_kind(tool_denial_kind: str, already_gate_denied: bool) -> bool:
    """True if a user record's toolDenialKind marks non-gate friction.

    A falsy toolDenialKind means the field is absent from this record — not
    friction. already_gate_denied guards against double-classifying a block
    hook_denial_key already matched via the message-text signature, so a
    record can never produce both a denial event and a friction event.
    """
    if already_gate_denied or not tool_denial_kind:
        return False
    return tool_denial_kind != _GATE_TOOL_DENIAL_KIND


# --deny-summary's/review-trace's printed friction_kind vocabulary — closed,
# so a future harness-added toolDenialKind value prints as _FRICTION_KIND_OTHER
# rather than echoing the raw field verbatim.
_FRICTION_KINDS: frozenset[str] = frozenset({
    "user-rejected",
    "automode-blocked",
    "automode-unavailable",
    "interrupted",
})
_FRICTION_KIND_OTHER = "other-kind"


def _friction_kind_label(tool_denial_kind: str) -> str:
    """Map a friction event's toolDenialKind to its printed label."""
    return tool_denial_kind if tool_denial_kind in _FRICTION_KINDS else _FRICTION_KIND_OTHER


# Defense-in-depth beyond each label source's own closed vocabulary
# (_DENIAL_HOOK_LABELS, _DENIAL_COMMAND_SUBCOMMANDS, _FRICTION_KINDS): strips
# ASCII control characters before a value is written into a --deny-summary
# table cell, the same way the per-session timeline's msg!r already guards
# free-text denial messages.
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")


def _sanitize_table_cell(value: str) -> str:
    """Strip ASCII control characters from a --deny-summary table cell value."""
    return _CONTROL_CHAR_RE.sub("", value)


def _print_deny_summary(
    hook_counts: dict[str, int],
    command_shape_counts: dict[str, int],
    hook_shape_counts: Counter[tuple[str, str]],
    friction_counts: dict[str, int],
    pre_regime_tool_result_count: int,
    corpus_min_ts: float | None,
    corpus_max_ts: float | None,
) -> None:
    """Print --deny-summary's grouped denial-count tables plus the friction breakout.

    hook_shape_counts cross-tabs the hook/gate axis against the command-shape
    axis — the two marginal tables alone can't say which hook denied which
    command shape, which is the whole point of the census this feeds.
    """
    if corpus_min_ts is not None and corpus_max_ts is not None:
        print(f"\nCorpus window: {_fmt_date(corpus_min_ts)} to {_fmt_date(corpus_max_ts)}")

    total = sum(hook_counts.values())
    print(f"\n## Denials by hook/gate ({total} total)\n")
    print(f"{'Hook/gate':<40} {'Count':>6}")
    print("-" * 47)
    for label, count in sorted(hook_counts.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"{_sanitize_table_cell(label):<40} {count:>6}")

    print(f"\n## Denials by attempted command shape ({total} total)\n")
    print(f"{'Shape':<16} {'Count':>6}")
    print("-" * 23)
    for label, count in sorted(command_shape_counts.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"{_sanitize_table_cell(label):<16} {count:>6}")

    # Column set is the observed shapes only (already restricted to A3's
    # classifier output plus "other"), not a fixed enumeration — the
    # multiplexer+subcommand shape space is open-ended by construction.
    # Skipped entirely (rather than rendering a header-only, zero-row table)
    # when scope has zero denials — a friction-only report has nothing to
    # cross-tab.
    if hook_counts or command_shape_counts:
        shapes = sorted(command_shape_counts.keys())
        hooks = sorted(hook_counts.keys())
        col_width = max((len(s) for s in shapes), default=5) + 2
        # Rows/header are indented two spaces — unlike the marginal tables above,
        # deliberately, so a hook-label row here never collides with a
        # column-0 row-label match against the hook/gate marginal table.
        print(f"\n## Denials by hook/gate x command shape ({total} total)\n")
        header = f"  {'Hook':<40}" + "".join(
            f"{_sanitize_table_cell(shape):>{col_width}}" for shape in shapes
        )
        print(header)
        print("  " + "-" * (len(header) - 2))
        for hook in hooks:
            row = f"  {_sanitize_table_cell(hook):<40}" + "".join(
                f"{hook_shape_counts.get((hook, shape), 0):>{col_width}}" for shape in shapes
            )
            print(row)

    friction_total = sum(friction_counts.values())
    print(f"\n## Friction events by kind ({friction_total} total)\n")
    print(f"{'Kind':<24} {'Count':>6}")
    print("-" * 31)
    for label, count in sorted(friction_counts.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"{_sanitize_table_cell(label):<24} {count:>6}")
    print(
        f"\n{pre_regime_tool_result_count} errored, non-gate tool result(s) predate the"
        f" per-record denial-kind field's introduction ({_TOOL_DENIAL_KIND_REGIME_START})"
        " and are excluded from the breakdown above — kind is structurally unmeasurable"
        " before that date, not zero."
    )


def _review_trace_session_events(
    records: list[dict],
    since_ts: float | None,
    until_epoch: float | None,
    branch_filter: set[str] | None,
    skill_filter: str | None = None,
) -> tuple[list[dict], dict[str, str], int]:
    """Detect cmd_review_trace's four per-session event kinds (skill, denial,
    friction, reviewer-spawn) from one session's records.

    Shared by cmd_review_trace's timeline printer and _compute_deny_summary_data
    so the denial/friction detection and dedup rules exist in one place rather
    than two copies kept in sync by hand. The third return value, a count of
    errored tool results predating toolDenialKind's introduction, is always
    computed (cheap) even though only --deny-summary reports it — see
    _print_deny_summary's own explanation of what it means.
    """
    events: list[dict] = []  # ordered, tagged with type/ts/line_no/branch/model
    # Tracks tool_use_ids already emitted as a denial. A legacy denial
    # appears as both an attachment record and an is_error tool_result
    # sharing one tool_use_id; this set collapses the pair to one event.
    seen_denial_ids: set[str] = set()

    # Friction events dedup against their own set, never seen_denial_ids
    # above — sharing it would let a friction event suppress a later
    # legitimate denial sharing a tool_use_id.
    seen_friction_ids: set[str] = set()

    # tool_use_id -> attempted command, for --deny-summary's by-command-shape
    # grouping. Indexed from every assistant tool_use block on the main
    # thread — review-trace's session_iter doesn't request subagent
    # records, so sidechain tool_use blocks are never present to index.
    # Independent of the --since/--until window, since a denial's own
    # event already applies it.
    tool_use_commands: dict[str, str] = {}

    # Carry-forward trackers, updated on every main-thread record before the
    # date filter below — the branch/model attributed to a denial (which
    # carries no message.model of its own) is whatever a prior main-thread
    # record last set, including one outside the --since/--until window.
    last_branch = ""
    last_model = ""
    pre_regime_tool_result_count = 0

    for line_no, rec in enumerate(records, start=1):
        if not bool(rec.get("isSidechain")):
            b = rec.get("gitBranch") or ""
            if b:
                last_branch = b
            if rec.get("type") == "assistant":
                m = (rec.get("message") or {}).get("model") or ""
                if m:
                    last_model = m

        if rec.get("type") == "assistant":
            for block in ((rec.get("message") or {}).get("content") or []):
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                tid = block.get("id")
                if tid:
                    tool_use_commands[tid] = (block.get("input") or {}).get("command", "")

        rec_ts_str: str | None = rec.get("timestamp")
        rec_ts: float | None = _parse_ts(rec_ts_str)

        # Apply date filter: records with no parseable timestamp are excluded when
        # a date boundary is active.
        if (since_ts is not None or until_epoch is not None):
            if rec_ts is None:
                continue
            if since_ts is not None and rec_ts < since_ts:
                continue
            if until_epoch is not None and rec_ts >= until_epoch:
                continue

        rec_type = rec.get("type", "")
        evt_branch = last_branch or "?"
        evt_model = _fam(last_model) if last_model else "?"

        # --- Signals 1 + 3: skill invocations and reviewer-agent spawns ---
        # Both are main-thread assistant tool_use blocks; a single pass over
        # content dispatches on tool name to avoid iterating the list twice.
        if rec_type == "assistant" and not bool(rec.get("isSidechain")):
            for block in ((rec.get("message") or {}).get("content") or []):
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                block_name = block.get("name")
                if block_name == "Skill":
                    skill_name = (block.get("input") or {}).get("skill") or ""
                    if skill_name not in REVIEW_TRACE_SKILLS:
                        continue
                    if skill_filter and skill_name != skill_filter:
                        continue
                    events.append({
                        "kind": "skill",
                        "skill": skill_name,
                        "ts": rec_ts_str,
                        "line_no": line_no,
                        "branch": evt_branch,
                        "model": evt_model,
                    })
                elif block_name in ("Agent", "Task"):
                    stype = (block.get("input") or {}).get("subagent_type") or ""
                    if not (stype.startswith(_REVIEWER_PREFIX) or stype in _REVIEWER_EXACT_NAMES):
                        continue
                    events.append({
                        "kind": "reviewer-spawn",
                        "subagent_type": stype,
                        "ts": rec_ts_str,
                        "line_no": line_no,
                        "branch": evt_branch,
                        "model": evt_model,
                    })

        # --- Signal 2a: hook denials, legacy shape (attachment record) ---
        if rec_type == "attachment":
            denial = hook_denial_key(rec)
            if denial is None:
                continue
            tool_use_id, att = denial
            if tool_use_id and tool_use_id in seen_denial_ids:
                continue
            raw_error = att.get("blockingError")
            normalized = _normalize_blocking_error(raw_error)
            hook_name = att.get("hookName") or ""
            if isinstance(normalized, dict):
                # Real transcripts nest the human-readable text in a "blockingError"
                # key alongside a "command" key; fall back to "message" then repr.
                message = (
                    normalized.get("blockingError")
                    or normalized.get("message")
                    or str(normalized)
                )
            else:
                message = str(normalized) if normalized else ""
            if tool_use_id:
                seen_denial_ids.add(tool_use_id)
            events.append({
                "kind": "denial",
                "hook_name": hook_name,
                "tool_use_id": tool_use_id,
                "message": message,
                "ts": rec_ts_str,
                "line_no": line_no,
                "branch": evt_branch,
                "model": evt_model,
            })

        # --- Signal 2b: hook denials, current shape (is_error tool_result) ---
        # Claude Code stopped emitting the hook_blocking_error attachment
        # record; current transcripts surface a denial only as an is_error
        # tool_result, identified by the hook-denial message signature.
        #
        # --- Signal 2c: non-gate friction, current shape (toolDenialKind) ---
        # toolDenialKind lives on this same `user` record, not on the
        # tool_result block — read once, but classification below still
        # requires the individual block's own is_error, since a parallel
        # tool call can carry an unrelated successful block alongside it.
        if rec_type == "user":
            tool_denial_kind = rec.get("toolDenialKind") or ""
            # A falsy tool_denial_kind this far before the regime start
            # means the field structurally could not exist yet, not that
            # this record measured zero friction. Scoped to the same
            # is_error-and-non-gate-signature population
            # _is_nongate_friction_kind would classify below, so the
            # count reflects records that could plausibly have been
            # friction, not every tool_result in the era (which would
            # count ordinary successful tool calls too) — tallied
            # separately and reported apart from the friction-kind
            # breakdown.
            pre_regime = (
                not tool_denial_kind
                and rec_ts is not None
                and _TOOL_DENIAL_KIND_REGIME_START_TS is not None
                and rec_ts < _TOOL_DENIAL_KIND_REGIME_START_TS
                and (not branch_filter or evt_branch in branch_filter)
            )
            for block in ((rec.get("message") or {}).get("content") or []):
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                denial = hook_denial_key(block)
                already_gate_denied = denial is not None
                if denial is not None:
                    tool_use_id, message = denial
                    if not (tool_use_id and tool_use_id in seen_denial_ids):
                        if tool_use_id:
                            seen_denial_ids.add(tool_use_id)
                        events.append({
                            "kind": "denial",
                            "hook_name": "",
                            "tool_use_id": tool_use_id,
                            "message": message,
                            "ts": rec_ts_str,
                            "line_no": line_no,
                            "branch": evt_branch,
                            "model": evt_model,
                        })

                if block.get("is_error") and _is_nongate_friction_kind(tool_denial_kind, already_gate_denied):
                    friction_tool_use_id = block.get("tool_use_id") or ""
                    if not (friction_tool_use_id and friction_tool_use_id in seen_friction_ids):
                        if friction_tool_use_id:
                            seen_friction_ids.add(friction_tool_use_id)
                        events.append({
                            "kind": "friction",
                            "friction_kind": tool_denial_kind,
                            "tool_use_id": friction_tool_use_id,
                            "message": _content_text(block.get("content")),
                            "ts": rec_ts_str,
                            "line_no": line_no,
                            "branch": evt_branch,
                            "model": evt_model,
                        })
                elif pre_regime and not already_gate_denied and block.get("is_error"):
                    pre_regime_tool_result_count += 1

    # Branch filtering happens after dedup (seen_denial_ids was populated
    # above over every event, unconditionally) so a duplicate-id denial on
    # a differently-branched record is suppressed, not re-emitted as a
    # distinct in-scope event.
    if branch_filter:
        events = [e for e in events if e["branch"] in branch_filter]

    return events, tool_use_commands, pre_regime_tool_result_count


def _compute_deny_summary_data(
    session_iter,
    since_ts: float | None = None,
    until_ts: float | None = None,
    branch_filter: set[str] | None = None,
    deny_only: bool = False,
) -> dict:
    """Corpus-wide --deny-summary accumulation, extracted so cost-ledger's
    per-week denial count and cmd_review_trace's own report share one pass
    over session_iter instead of two implementations kept in sync by hand.

    since_ts/until_ts are explicit epoch-second boundaries (until_ts
    exclusive, the same convention as cmd_review_trace's own until_epoch)
    rather than the CLI's date-string args, so a caller can pass exact week
    boundaries the CLI itself has no flag to reach.
    """
    hook_counts: dict[str, int] = defaultdict(int)
    command_shape_counts: dict[str, int] = defaultdict(int)
    hook_shape_counts: Counter[tuple[str, str]] = Counter()
    friction_counts: dict[str, int] = defaultdict(int)
    corpus_min_ts: float | None = None
    corpus_max_ts: float | None = None
    pre_regime_tool_result_count = 0
    any_session_matched = False

    for _jsonl, records in session_iter:
        events, tool_use_commands, session_pre_regime = _review_trace_session_events(
            records, since_ts, until_ts, branch_filter
        )
        if not events:
            continue
        any_session_matched = True
        pre_regime_tool_result_count += session_pre_regime

        # Corpus window reads the full branch-filtered per-session events list
        # before the deny_only skip below, same as the friction tally below —
        # so the reported window matches whatever --branches/--since/--until
        # actually put in scope, not the pre-branch-filter raw record range.
        for evt in events:
            evt_ts = _parse_ts(evt.get("ts"))
            if evt_ts is None:
                continue
            if corpus_min_ts is None or evt_ts < corpus_min_ts:
                corpus_min_ts = evt_ts
            if corpus_max_ts is None or evt_ts > corpus_max_ts:
                corpus_max_ts = evt_ts

        has_denial = any(e["kind"] == "denial" for e in events)

        # Friction tally reads the full per-session events list before the
        # deny_only skip below, so a friction-only session (has_denial False)
        # still contributes when --deny-only and --deny-summary run together.
        # deny_only's own session-selection stays denial-kind-only, unchanged.
        for evt in events:
            if evt["kind"] == "friction":
                friction_counts[_friction_kind_label(evt["friction_kind"])] += 1

        if deny_only and not has_denial:
            continue

        for evt in events:
            if evt["kind"] != "denial":
                continue
            hook_label = _denial_hook_label(evt["hook_name"], evt["message"])
            command = tool_use_commands.get(evt["tool_use_id"], "")
            command_shape = _denial_command_shape(command)
            hook_counts[hook_label] += 1
            command_shape_counts[command_shape] += 1
            hook_shape_counts[(hook_label, command_shape)] += 1

    return {
        "hook_counts": hook_counts,
        "command_shape_counts": command_shape_counts,
        "hook_shape_counts": hook_shape_counts,
        "friction_counts": friction_counts,
        "corpus_min_ts": corpus_min_ts,
        "corpus_max_ts": corpus_max_ts,
        "pre_regime_tool_result_count": pre_regime_tool_result_count,
        "any_session_matched": any_session_matched,
    }


def cmd_review_trace(args: argparse.Namespace) -> None:
    """Emit an ordered review-event timeline per session.

    Four event types are detected per session:
    - skill: main-thread Skill tool_use where input.skill is in REVIEW_TRACE_SKILLS
    - denial: a hook-blocking denial in either transcript shape — a legacy
      `attachment` record (type==hook_blocking_error) or a current-format
      `tool_result` block with is_error and a hook-denial message signature.
      A denial recorded as both shapes is collapsed to one event by tool_use_id.
    - friction: a current-format `user` record whose own toolDenialKind field
      marks non-gate friction (user-rejected, automode-blocked,
      automode-unavailable, interrupted) — see _is_nongate_friction_kind.
      Deduped by tool_use_id in its own set, independent of denial dedup.
    - reviewer: Agent/Task spawn where subagent_type matches _REVIEWER_PREFIX or is in _REVIEWER_EXACT_NAMES

    denial and friction are deliberately separate event kinds: has_denial,
    denials=N, and --deny-only's session-selection all stay denial-kind-only,
    so a non-gate toolDenialKind value never broadens what those three
    surfaces report — only the default timeline and --deny-summary's own
    friction breakout render friction events.

    Branch and model are resolved per event from the record that produced it,
    not from the session's first record: each is the last non-empty value
    carried forward up to that point, so a session that moves from one branch
    (or model) to another attributes each event correctly instead of labelling
    every event with whatever the session started on. An event whose branch or
    model cannot be resolved renders '?'. --branches filters the emitted event
    list by this per-event value, not by a single session-wide branch.

    --deny-summary delegates its entire accumulation to
    _compute_deny_summary_data instead of running its own pass over
    session_iter, so the corpus-wide grouped-count report and cost-ledger's
    per-week denial count can never drift apart.
    """
    branch_filter = _branch_filter(args)
    deny_only: bool = bool(getattr(args, "deny_only", False))
    deny_summary: bool = bool(getattr(args, "deny_summary", False))
    skill_filter: str | None = getattr(args, "skill", None) or None
    roots = _resolve_scan_roots(args)
    session_iter, scope_label = _resolve_project_scope(args, "review-trace", roots=roots)

    since_str: str | None = getattr(args, "since", None) or None
    until_str: str | None = getattr(args, "until", None) or None
    since_ts: float | None = _parse_ts(f"{since_str}T00:00:00Z") if since_str else None
    # Inclusive-day boundary: compute start of the *next* day and compare with strict <.
    # Adding 86400 seconds covers the entire until-day at any sub-second precision.
    until_epoch: float | None = None
    if until_str:
        day_start = _parse_ts(f"{until_str}T00:00:00Z")
        if day_start is not None:
            until_epoch = day_start + 86400

    if deny_summary:
        data = _compute_deny_summary_data(
            session_iter, since_ts=since_ts, until_ts=until_epoch,
            branch_filter=branch_filter, deny_only=deny_only,
        )
        if sum(data["hook_counts"].values()) or sum(data["friction_counts"].values()):
            _print_resolved_scope("review-trace", scope_label, roots)
            _print_deny_summary(
                data["hook_counts"], data["command_shape_counts"], data["hook_shape_counts"],
                data["friction_counts"], data["pre_regime_tool_result_count"],
                data["corpus_min_ts"], data["corpus_max_ts"],
            )
        elif data["any_session_matched"]:
            # Scope resolved and had matching sessions, but none carried a
            # denial — printed explicitly so this reads distinctly from a
            # broken --branches/scope flag matching no sessions at all.
            _print_resolved_scope("review-trace", scope_label, roots)
            print("\nNo denials found in scope.")
        return

    # The scope header prints lazily, on the first emitted block — not
    # unconditionally up front — so a run that matches no session still
    # produces byte-for-byte empty output, as it always has.
    scope_header_printed = False

    for jsonl, records in session_iter:
        events, tool_use_commands, _pre_regime = _review_trace_session_events(
            records, since_ts, until_epoch, branch_filter, skill_filter=skill_filter
        )
        if not events:
            continue

        has_denial = any(e["kind"] == "denial" for e in events)
        if deny_only and not has_denial:
            continue

        skill_count = sum(1 for e in events if e["kind"] == "skill")
        denial_count = sum(1 for e in events if e["kind"] == "denial")
        spawn_count = sum(1 for e in events if e["kind"] == "reviewer-spawn")
        branches_seen = ",".join(sorted({e["branch"] for e in events}))
        models_seen = ",".join(sorted({e["model"] for e in events}))

        if not scope_header_printed:
            _print_resolved_scope("review-trace", scope_label, roots)
            scope_header_printed = True

        print(f"\n### {jsonl}")
        print(
            f"branches={branches_seen}  models={models_seen}  skills={skill_count}"
            f"  denials={denial_count}  reviewer-spawns={spawn_count}"
        )
        for evt in events:
            ts_label = evt.get("ts") or "?"
            lno = evt["line_no"]
            kind = evt["kind"]
            suffix = f"  (branch={evt['branch']} model={evt['model']})"
            if kind == "skill":
                print(f"  [{ts_label}] line {lno:>5}  skill        {evt['skill']}{suffix}")
            elif kind == "denial":
                hook = evt['hook_name']
                uid = evt['tool_use_id']
                msg = evt['message']
                print(f"  [{ts_label}] line {lno:>5}  denial       hook={hook}  id={uid}  msg={msg!r}{suffix}")
            elif kind == "friction":
                fkind = _friction_kind_label(evt['friction_kind'])
                uid = evt['tool_use_id']
                msg = evt['message']
                print(f"  [{ts_label}] line {lno:>5}  friction     kind={fkind}  id={uid}  msg={msg!r}{suffix}")
            elif kind == "reviewer-spawn":
                print(f"  [{ts_label}] line {lno:>5}  reviewer     {evt['subagent_type']}{suffix}")


def cmd_judgment_pair(args: argparse.Namespace) -> None:
    """Emit (review-skill output, user response) pairs from sessions containing review invocations.

    For each matching Skill invocation in a session:
    - REVIEW OUTPUT: the last main-thread assistant turn with non-empty text in the
      window between the invocation and the next fresh user prompt (or next matching
      invocation, whichever comes first).
    - USER RESPONSE: the first fresh user prompt text after that window closes.

    Uses _is_fresh_user_prompt() to skip tool-result turns, isMeta injections,
    and isCompactSummary injections when locating the user response.

    --branches filters on the invocation record's own gitBranch, not a single
    session-wide branch — a session whose branch changes between the
    invocation and the user response is filtered by where the invocation
    itself happened.
    """
    branch_filter = _branch_filter(args)
    roots = _resolve_scan_roots(args)
    session_iter, scope_label = _resolve_project_scope(args, "judgment-pair", roots=roots)

    since_str: str | None = getattr(args, "since", None) or None
    until_str: str | None = getattr(args, "until", None) or None
    since_ts: float | None = _parse_ts(f"{since_str}T00:00:00Z") if since_str else None
    until_epoch: float | None = None
    if until_str:
        day_start = _parse_ts(f"{until_str}T00:00:00Z")
        if day_start is not None:
            until_epoch = day_start + 86400

    skills_arg: str = getattr(args, "skills", None) or ",".join(REVIEW_SKILLS)
    skill_set: set[str] = {s for s in skills_arg.split(",") if s}
    truncate_chars: int = getattr(args, "truncate_chars", 1000) or 1000
    out_path: str | None = getattr(args, "out", None) or None

    output_blocks: list[str] = []

    for jsonl, records in session_iter:
        proj_label = _derive_proj_label(jsonl)
        session_id_prefix = jsonl.stem[:8]

        for rec_idx, rec in enumerate(records):
            line_no = rec_idx + 1  # 1-based line number for output

            # Detect matching skill invocation: main-thread assistant with Skill tool_use
            # whose input.skill is in the target set.
            if rec.get("type") != "assistant" or bool(rec.get("isSidechain")):
                continue
            content_blocks = (rec.get("message") or {}).get("content") or []
            inv_skill: str | None = None
            for block in content_blocks:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                if block.get("name") != "Skill":
                    continue
                skill_name = (block.get("input") or {}).get("skill") or ""
                if skill_name in skill_set:
                    inv_skill = skill_name
                    break
            if inv_skill is None:
                continue

            invocation_branch = rec.get("gitBranch") or ""
            if branch_filter and invocation_branch not in branch_filter:
                continue

            inv_ts_str: str | None = rec.get("timestamp")
            inv_ts_epoch: float | None = _parse_ts(inv_ts_str)

            # Apply date filter to invocation timestamp.
            if since_ts is not None or until_epoch is not None:
                if inv_ts_epoch is None:
                    continue
                if since_ts is not None and inv_ts_epoch < since_ts:
                    continue
                if until_epoch is not None and inv_ts_epoch >= until_epoch:
                    continue

            # Scan forward to find window_end: the minimum of
            #   (a) the index of the next fresh user prompt, and
            #   (b) the index of the next matching skill invocation after this one.
            # window_end is exclusive (records[rec_idx+1 : window_end]).
            window_end = len(records)
            found_boundary = False
            for scan_idx in range(rec_idx + 1, len(records)):
                scan_rec = records[scan_idx]
                # Bound (a): next fresh user prompt closes the window.
                if _is_fresh_user_prompt(scan_rec):
                    window_end = scan_idx
                    found_boundary = True
                    break
                # Bound (b): next matching skill invocation closes the window.
                if scan_rec.get("type") == "assistant" and not bool(scan_rec.get("isSidechain")):
                    scan_content = (scan_rec.get("message") or {}).get("content") or []
                    for scan_block in scan_content:
                        if not isinstance(scan_block, dict) or scan_block.get("type") != "tool_use":
                            continue
                        if scan_block.get("name") != "Skill":
                            continue
                        scan_skill = (scan_block.get("input") or {}).get("skill") or ""
                        if scan_skill in skill_set:
                            window_end = scan_idx
                            found_boundary = True
                            break
                    if found_boundary:
                        break

            # REVIEW OUTPUT: last main-thread assistant turn with non-empty text in the window.
            review_text = ""
            for window_rec in records[rec_idx + 1 : window_end]:
                if window_rec.get("type") != "assistant" or bool(window_rec.get("isSidechain")):
                    continue
                candidate_text = _content_text(
                    (window_rec.get("message") or {}).get("content", "")
                ).strip()
                if candidate_text:
                    review_text = candidate_text

            # USER RESPONSE: the fresh user prompt at window_end (if it is one).
            if window_end < len(records) and _is_fresh_user_prompt(records[window_end]):
                user_response_text = _content_text(
                    (records[window_end].get("message") or {}).get("content", "")
                ).strip()
            else:
                user_response_text = "(no user response — end of session)"

            # Format the output block.
            date_label = _fmt_date(inv_ts_epoch) if inv_ts_epoch is not None else "?"
            if review_text:
                review_display = review_text[:truncate_chars] + "…" if len(review_text) > truncate_chars else review_text
            else:
                review_display = "(no review text found)"

            block = (
                f"### {proj_label} · {session_id_prefix} · {date_label}\n"
                f"Skill: {inv_skill}  branch={invocation_branch or '?'}  (line {line_no})\n"
                f"\n"
                f"--- REVIEW OUTPUT (truncated to {truncate_chars} chars) ---\n"
                f"{review_display}\n"
                f"\n"
                f"--- USER RESPONSE ---\n"
                f"{user_response_text}\n"
                f"---"
            )
            output_blocks.append(block)

    if not output_blocks:
        _print_resolved_scope("judgment-pair", scope_label, roots)
        print("No judgment pairs found.")
        return

    output_text = "\n\n".join(output_blocks)

    if out_path:
        # Nothing goes to stdout in this branch — --out means the caller wants
        # only the file written. The scope header is still prepended to the
        # file's content so a saved/curated file stays self-documenting about
        # its scope even if pasted elsewhere without the terminal output.
        header = _resolved_scope_header("judgment-pair", scope_label, roots)
        Path(out_path).write_text(header + "\n" + output_text + "\n")
    else:
        _print_resolved_scope("judgment-pair", scope_label, roots)
        print(output_text)


def _path_to_project_slug(path: str) -> str:
    """Map an absolute path to Claude Code's project-directory slug.

    Claude Code names each project dir under ~/.claude/projects/ by taking the
    session's cwd and replacing every '/' and '.' with '-'. Verified against real
    dirs: /home/u/repo -> -home-u-repo;
    /home/u/repo/.claude/worktrees/b -> -home-u-repo--claude-worktrees-b.
    """
    return re.sub(r"[/.]", "-", path)


def _repo_scoped_project_slugs(command_label: str = "skill-invocation") -> list[str]:
    """Exact project-dir slugs for this repo's own worktrees (main + linked).

    This is the minimization control for any caller (`command_label`) that must
    scope a transcript read to this repo: output built from it is routinely
    quoted into public PR descriptions, and transcripts under
    ~/.claude/projects/ span every project on the machine. Scoping the read to
    this repository — by *identity*, via `git worktree list`, matched as exact dir
    names — guarantees no other project's skill names can enter the output. It is
    deliberately not a path glob: a `<slug>*`-style match scopes by where a dir
    sits in the path string, which a foreign repo cloned under this repo's
    worktrees/, a sibling `<repo>-fork`, or a lossy-slug collision all defeat. A
    session started in a repo *subdirectory* is a known exception this exactness
    does not cover: its project dir is slugged from that subdirectory path and
    is string-unequal to every worktree-root slug, so it is silently excluded —
    the documented prefix-glob fallback covers that case instead.

    Fails closed (SystemExit) rather than returning a machine-wide scope whenever
    the environment is not a recognizable git worktree of this repo — a silent
    fallback to "*" would reintroduce the cross-project read this exists to
    prevent.
    """
    try:
        # timeout guards the fail-closed posture: a hung local git (stale lock,
        # network-mounted .git) would otherwise block the whole CLI with no exit.
        # 10s is generous for a local `worktree list` (no network/credential work)
        # while still bounding a wedged invocation.
        proc = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            capture_output=True, text=True, check=True, timeout=10,
        )
    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        FileNotFoundError,
        OSError,
    ) as exc:
        print(
            f"{command_label}: cannot determine repo scope (git worktree list failed: {exc}); "
            "refusing machine-wide scope",
            file=sys.stderr,
        )
        sys.exit(1)

    prefix = "worktree "
    worktree_paths = [
        line[len(prefix):] for line in proc.stdout.splitlines() if line.startswith(prefix)
    ]
    if not worktree_paths:
        print(
            f"{command_label}: git listed no worktrees; refusing machine-wide scope",
            file=sys.stderr,
        )
        sys.exit(1)

    resolved_worktrees = [Path(p).resolve() for p in worktree_paths]

    # Containment guard: cwd must sit at or under one of the resolved worktree
    # roots. Path-segment based, not str.startswith — a sibling sharing a bare
    # string prefix (<repo>-fork) must not be wrongly accepted as "within".
    cwd = Path(os.getcwd()).resolve()
    if not any(cwd == wt or wt in cwd.parents for wt in resolved_worktrees):
        print(
            f"{command_label}: working directory is not within the resolved repo worktrees; "
            "refusing to emit a scope that may not be this repo's",
            file=sys.stderr,
        )
        sys.exit(1)

    # Identity guard: containment alone is not posture-neutral — it would accept
    # a cwd whose own repo resolves elsewhere under an environment override (e.g.
    # GIT_WORK_TREE decoupling toplevel reporting from the worktree-list
    # enumeration) but which happens to sit under one of the paths above.
    # Confirm cwd's own git root is genuinely one of the worktrees: containment
    # then governs *where under the root* the caller stands, this governs
    # *which root* was resolved.
    try:
        # Same local-git timeout rationale as the `worktree list` call above:
        # no network/credential work, so 10s only bounds a wedged invocation.
        toplevel_proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True, timeout=10,
        )
    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        FileNotFoundError,
        OSError,
    ) as exc:
        print(
            f"{command_label}: cannot determine repo identity (git rev-parse failed: {exc}); "
            "refusing machine-wide scope",
            file=sys.stderr,
        )
        sys.exit(1)

    cwd_repo_root = Path(toplevel_proc.stdout.strip()).resolve()
    if cwd_repo_root not in resolved_worktrees:
        print(
            f"{command_label}: working directory's repo root is not among the resolved worktrees; "
            "refusing to emit a scope that may not be this repo's",
            file=sys.stderr,
        )
        sys.exit(1)

    return [_path_to_project_slug(p) for p in worktree_paths]


def _normalize_skill_name(raw: str) -> str:
    """Strip a directory qualifier from a transcript skill name.

    input["skill"] is a display label, not a stable identifier. Claude Code
    qualifies project-scoped skills by the directory they were found in, so the
    same skill is recorded under several spellings depending on the invoking
    session's working directory:

        plan-it
        claude:plan-it
        .claude/worktrees/some-branch/claude:plan-it
        claude/.claude/worktrees/some-branch/nested/claude:plan-it

    Collapsing these spellings stops one skill from splitting across several rows.
    This is row-hygiene, not the security control: within a repo-scoped read the
    only path fragment that can appear is *this* repo's own worktree branch (its
    project dirs are the only ones read), which is public. Cross-project
    minimization is enforced upstream by _repo_scoped_project_slugs, not here.

    A remaining ``plugin:skill`` or ``dir:skill`` prefix is deliberately kept: it
    carries no path, and dropping it would need this script to hardcode the stow
    package name, which varies per installation. Resolving such a prefix to a
    skill body is the reviewer agent's job, not the extractor's.
    """
    return raw.rsplit("/", 1)[-1]


def _dedup_new_project_dirs(candidates: Iterable[Path], visited_dirs: set[Path]) -> Iterator[Path]:
    """Yield each directory in `candidates` at most once, keyed on resolved
    real path, recording it into `visited_dirs` (mutated in place) as it's
    yielded.

    Shared across every multi-root project-dir scan in this file
    (`_iter_scoped_sessions`, `_iter_glob_scoped_sessions`,
    `_scan_root_transcripts`) — extracted after a cumulative review found
    three near-identical copies of this same five-line pattern. Pass one
    `visited_dirs` set across an entire multi-root loop (not a fresh one per
    root) so a project dir aliased, by symlink, to one already yielded from a
    prior root is caught too — not just two roots resolving to the same
    directory.
    """
    for candidate in candidates:
        if not candidate.is_dir():
            continue
        resolved_dir = candidate.resolve()
        if resolved_dir in visited_dirs:
            continue
        visited_dirs.add(resolved_dir)
        yield candidate


def _iter_scoped_sessions(
    slugs: list[str], include_subagents: bool, roots: Sequence[Path] | None = None
):
    """Yield sessions from an explicit set of exact project-dir slugs.

    Matching is by identity, not location: enumerate the directory names under
    each root in `roots` (default: PROJECTS_DIR alone, so every caller other
    than cost's --config-dir is unaffected) and keep only those whose name is
    string-equal to one of the scoped slugs. This deliberately does NOT route
    the slug through Path.glob — a slug containing a glob metacharacter (a
    `*`/`?`/`[` in the machine's home or username path) would otherwise be
    interpreted as a wildcard and could widen the match beyond this repo's own
    worktrees. Visiting each directory at most once (by resolved real path,
    spanning every root — covers a root nested inside another, not just two
    identical roots) also makes double-counting impossible.

    A root that raises OSError while being listed (an unreadable directory,
    not merely a missing one — `root.is_dir()` above only requires the
    *parent* to be searchable) is reported to stderr and skipped, not
    propagated: this function has no `redact` parameter of its own (unlike
    cost's per-root diagnostic), so the reported detail always omits the raw
    path — an OSError's `strerror` (e.g. "Permission denied"), never
    `str(exc)`, which embeds the offending path.
    """
    if roots is None:
        roots = (PROJECTS_DIR,)
    wanted = set(slugs)
    visited_dirs: set[Path] = set()
    multi_root = len(roots) > 1
    # Ordinal, not scan-order index: the same physical root must read as the
    # same account-N here as in every other multi-root diagnostic in this
    # file (_cost_report's per-root summary, --by-project's account column).
    ordinals = _redaction_ordinals(roots) if multi_root else {}
    for root in roots:
        if multi_root:
            # No path in this line: neither function knows whether its caller
            # is a --redact context (cost's own per-root diagnostic handles
            # that separately) -- an unconditional raw config-dir path here
            # would leak account/client identity a redacted run must not print.
            print(f"scanning root {ordinals[root.resolve()]}/{len(roots)}...", file=sys.stderr)
        if not root.is_dir():
            continue
        try:
            candidates = [p for p in sorted(root.iterdir()) if p.name in wanted]
        except OSError as exc:
            root_label = f"account-{ordinals[root.resolve()]}" if multi_root else "the scope root"
            print(
                f"_iter_scoped_sessions: cannot scan {root_label}"
                f" ({exc.strerror or 'permission denied'}) — skipping",
                file=sys.stderr,
            )
            continue
        for project_dir in _dedup_new_project_dirs(candidates, visited_dirs):
            for jsonl in sorted(project_dir.glob("*.jsonl")):
                records = _read_session_file(jsonl, include_subagents)
                if records:
                    yield jsonl, records


def _iter_glob_scoped_sessions(
    roots: Sequence[Path], projects_glob: str, include_subagents: bool
) -> Iterator[tuple[Path, list[dict]]]:
    """Chain iter_sessions' glob match across more than one root.

    Only used when len(roots) > 1 (cost's --config-dir); the single-root case
    keeps calling iter_sessions directly so its documented flat-sort-over-
    full-paths ordering guarantee is untouched for every other caller. Dedups
    matched project directories by resolved real path, spanning all roots —
    covers a --config-dir root nested inside another root's tree, not just
    two roots that resolve to the same directory (already deduped earlier at
    the CLI boundary).
    """
    visited_dirs: set[Path] = set()
    multi_root = len(roots) > 1
    # Ordinal, not scan-order index: the same physical root must read as the
    # same account-N here as in every other multi-root diagnostic in this
    # file (_cost_report's per-root summary, --by-project's account column).
    ordinals = _redaction_ordinals(roots) if multi_root else {}
    for root in roots:
        if multi_root:
            # No path in this line: neither function knows whether its caller
            # is a --redact context (cost's own per-root diagnostic handles
            # that separately) -- an unconditional raw config-dir path here
            # would leak account/client identity a redacted run must not print.
            print(f"scanning root {ordinals[root.resolve()]}/{len(roots)}...", file=sys.stderr)
        for project_dir in _dedup_new_project_dirs(sorted(root.glob(projects_glob)), visited_dirs):
            for jsonl in sorted(project_dir.glob("*.jsonl")):
                records = _read_session_file(jsonl, include_subagents)
                if records:
                    yield jsonl, records


def _resolve_scan_roots(parsed: argparse.Namespace) -> list[Path]:
    """Resolve one invocation's scan roots -- the single funnel every
    _resolve_project_scope caller threads `roots` through, replacing the
    default single-root PROJECTS_DIR scope everywhere except cost and
    context-distribution's own --config-dir extras (_resolve_cost_roots).

    An explicit top-level --config-dir overrides everything else, returning
    that one directory's projects/ subdirectory alone. Absent that, the base
    is PROJECTS_DIR (the module global -- still config_dir()/"projects" at
    import, still reassignable via monkeypatch.setattr(_mod, "PROJECTS_DIR",
    ...)) plus each of declared_transcript_roots()'s own projects/
    subdirectory, deduped by resolved real path. PROJECTS_DIR is listed
    first, so the active profile is always scanned first -- the "active
    profile first" convention analyze-context.py's session lookup also
    relies on.

    config_dir is read via getattr, not attribute access, matching
    _resolve_project_scope's own rationale below: this file's many
    hand-built test `args` fixtures predate the top-level --config-dir flag,
    so its absence means "not passed," not a wiring bug.
    """
    config_dir_arg = getattr(parsed, "config_dir", None)
    if config_dir_arg:
        return [Path(config_dir_arg) / "projects"]

    roots = [PROJECTS_DIR]
    seen_resolved = {PROJECTS_DIR.resolve()}
    for declared_root in declared_transcript_roots():
        candidate = declared_root / "projects"
        resolved = candidate.resolve()
        if resolved in seen_resolved:
            continue
        seen_resolved.add(resolved)
        roots.append(candidate)
    return roots


def _resolve_project_scope(
    args: argparse.Namespace,
    subcommand: str,
    include_subagents: bool = False,
    roots: Sequence[Path] | None = None,
) -> tuple[Iterator[tuple[Path, list[dict]]], str]:
    """Resolve --projects/--this-repo into a fresh session iterator and a scope label.

    A plain function, not a generator: a generator would defer
    _repo_scoped_project_slugs' fail-closed sys.exit(1) to the caller's first
    next() instead of raising at scope-resolution time, unlike every other
    scope decision in this file. Reads args.this_repo unguarded so a subparser
    wired without _add_project_scope_args raises AttributeError rather than
    silently falling through to machine-wide scope. `subcommand` is the CLI
    subcommand name (e.g. "buckets"); it labels _repo_scoped_project_slugs'
    fail-closed messages and, uppercased, the caller's resolved-scope header.
    The resolved slug list is cached on `args` so a caller needing two
    independent iterators over the same scope triggers one `git worktree
    list` call, not two — no current caller does this, but the cache is
    correct if one starts to. This caching relies on main()'s
    single-Namespace-per-process, single-subcommand-dispatch invariant — an
    `args` object reused across two different subcommand invocations would
    silently reuse the first's resolved slugs instead of re-resolving for the
    second.

    `roots` defaults to (PROJECTS_DIR,) when a caller passes none, but every
    cmd_* entry point in this file now threads its own _resolve_scan_roots(args)
    result through (cost, context-distribution, edit-format, subagents, and
    subagent-mix instead thread their own _resolve_cost_roots(args) result,
    which unions the same declared_transcript_roots() plus their own
    repeatable --config-dir extras), so the default only ever fires for a
    caller that predates that threading (this module's own single-root test
    fixtures). --this-repo and multi-root are no longer mutually exclusive: a
    populated ~/.claude/transcript-config-dirs makes --this-repo multi-root by
    default with no --config-dir flag at all, so the this_repo branch below
    unions across every root in `roots` via _iter_scoped_sessions' own
    basename match.

    Under an explicit top-level --config-dir (a different flag from cost's
    and context-distribution's own --config-dir above — see main()), zero of
    the resolved slugs matching an actual directory under ANY resolved root
    fails closed (sys.exit(1)) instead of returning an iterator that silently
    yields nothing — this is the original reported symptom (declaring no
    sessions exist for a container that has them), so an empty --this-repo
    scope here is far more likely a wrong --config-dir than a genuinely
    session-less repo. Without --config-dir, an empty scope stays silent,
    matching every other subcommand's long-standing behavior. config_dir is
    read via getattr (unlike this_repo above): it's a top-level parser flag
    rather than something _add_project_scope_args wires per subparser, and
    this file's many hand-built test `args` fixtures predate it, so its
    absence means "not passed" (the real default), not a wiring bug. Under
    _resolve_scan_roots' own precedence (an explicit top-level --config-dir
    overrides declared roots entirely), that flag makes `roots` a
    single-element list equal to the reassigned PROJECTS_DIR, so this check
    checking every root in `roots` rather than PROJECTS_DIR alone is a
    robustness improvement against a future precedence change, not a fix for
    a divergence reachable today.
    """
    if roots is None:
        roots = (PROJECTS_DIR,)
    if args.this_repo:
        slugs = getattr(args, "_this_repo_slugs", None)
        if slugs is None:
            slugs = _repo_scoped_project_slugs(subcommand)
            args._this_repo_slugs = slugs
        config_dir_arg = getattr(args, "config_dir", None)
        if config_dir_arg and not any((root / slug).is_dir() for root in roots for slug in slugs):
            print(
                f"{subcommand}: --this-repo matched zero project directories under "
                f"--config-dir {config_dir_arg} (checked {len(slugs)} candidate worktree "
                "slug(s)) — refusing to report an empty scope silently; confirm "
                "--config-dir points at the account these sessions actually live in.",
                file=sys.stderr,
            )
            sys.exit(1)
        return (
            _iter_scoped_sessions(slugs, include_subagents, roots=roots),
            f"this repo ({len(slugs)} project dirs)",
        )
    glob = _projects_glob(args)
    if len(roots) == 1:
        return iter_sessions(roots[0], glob, include_subagents=include_subagents), glob
    return _iter_glob_scoped_sessions(roots, glob, include_subagents), glob


def _root_count_desc(roots: Sequence[Path]) -> str:
    """Render the root-count clause of the resolved-scope header (e.g. "1 root
    (no ~/.claude/transcript-config-dirs declared)" or "3 roots").

    At one root, the wording branches on declared_roots_file_state(): "absent"
    is the only state where "no ... declared" is accurate. "unreadable" (a
    permissions problem, not a missing file) gets its own wording rather than
    folding into "present" -- collapsing the two would claim a failed read
    "declared" a root, masking exactly the problem the tri-state exists to
    surface. "present" (the file contributed a root, or contributed nothing
    because every entry failed validation) says "declared but contributed no
    additional root" -- claiming "no ... declared" about a file that exists
    would misrepresent it. The >1-root branch never names the file: extra
    roots may come from --config-dir, not only a declared-roots file.
    """
    if len(roots) != 1:
        return f"{len(roots)} roots"
    state = declared_roots_file_state()
    if state == "absent":
        return f"1 root (no {TRANSCRIPT_CONFIG_DIRS_LABEL} declared)"
    if state == "unreadable":
        return f"1 root ({TRANSCRIPT_CONFIG_DIRS_LABEL} present but unreadable)"
    return f"1 root ({TRANSCRIPT_CONFIG_DIRS_LABEL} declared but contributed no additional root)"


def _resolved_scope_header(subcommand: str, scope_label: str, roots: Sequence[Path]) -> str:
    """Build the one-line resolved-scope header text, shared by _print_resolved_scope
    and any caller (e.g. judgment-pair's --out file) that needs the header written
    somewhere other than a live print call.

    `roots` is required, not defaulted: a call site that forgets to pass it is a
    TypeError at implementation and test time, not a silently-wrong "1 root" line
    printed while the subcommand actually scanned N. The root count is stated
    unconditionally -- even at one root -- so the header discloses scope in the
    exact zero-declared-roots state that produced the corpus-undercount this
    exists to prevent, not only once an operator has already declared a second
    account. A root skipped by index (a stale declared-roots line) is not named
    here; that detail is a separate stderr warning from declared_transcript_roots()
    itself, keeping this return value the single line judgment-pair's --out file
    contract requires.
    """
    return f"{subcommand.upper().replace('-', ' ')} SOURCES ({scope_label}; {_root_count_desc(roots)})"


def _print_resolved_scope(subcommand: str, scope_label: str, roots: Sequence[Path], *, file=None) -> None:
    """Print the one-line resolved-scope header cmd_skill_invocation already uses,
    so machine-wide vs. --this-repo output is never scope-ambiguous. `file` defaults
    to stdout (resolved at call time, not import time — a `sys.stdout` default
    value would bind the stream object process startup captured, bypassing test
    capture and any later reassignment); audit-routing-samples routes it to stderr
    instead, since its stdout is a JSON (or curation-markdown) data stream a header
    line would corrupt."""
    print(_resolved_scope_header(subcommand, scope_label, roots), file=file or sys.stdout)


def cmd_skill_invocation(args: argparse.Namespace) -> None:
    """Per-skill invocation-source tally across the full corpus.

    Three invocation buckets are counted per skill:
    - top-level: Skill tool_use on a main-thread assistant turn with no attributionSkill.
    - routed: Skill tool_use on a main-thread assistant turn where attributionSkill is
      non-empty (the call was fired while another skill's body was active).
    - user-slash: user record whose message content contains a
      <command-name>/skillname</command-name> tag (the /slash invocation path, which
      injects the skill body directly without a Skill tool_use).

    Identifies routed-only candidates (zero top-level or slash) and slash-only candidates
    (zero top-level or routed) for skill-description budget analysis.

    Two consumers ask different questions of this data, so subagent turns are opt-in:

    - Skill-description budget analysis asks whether a skill's *description* draws
      auto-triggers on the main thread. Sidechain turns are noise there, so they are
      excluded by default.
    - Procedural-fidelity review asks which procedures a branch's work committed to.
      A skill invoked inside a spawned agent binds exactly as much as a main-thread
      one, so --include-subagents folds those in and adds a thread column keeping the
      two distinguishable rather than silently merged.

    --branches scopes to named gitBranch values; --projects scopes to project dirs and
    defaults to every project on the machine, which is rarely what a branch-scoped
    caller wants (branch names are not unique across repos).
    """
    branch_filter = _branch_filter(args)
    include_subagents = bool(getattr(args, "include_subagents", False))

    # OUTPUT INVARIANT — provenance, not shape. This output is routinely quoted
    # into public PR descriptions. Its safety rests on WHAT records are read, not
    # on scrubbing names after the fact: skill names are user-defined strings and
    # can themselves be private-project identifiers (a plugin namespace with no
    # path separator at all). So the control is to scope the read to this repo's
    # own project dirs (_repo_scoped_project_slugs) — the default when --projects
    # is unset. An explicit --projects is an escape hatch for corpus analysis;
    # the caller then owns that the output is no longer publish-safe.
    #
    # Supporting rules: only input["skill"] is extracted (never input["args"],
    # which holds absolute paths even for in-scope sessions); and
    # _normalize_skill_name collapses this repo's own worktree-qualified spellings
    # for row-hygiene. Neither is the security boundary — scoping is.
    roots = _resolve_scan_roots(args)

    projects_arg = getattr(args, "projects", None)
    if projects_arg:
        if len(roots) > 1:
            session_iter = _iter_glob_scoped_sessions(roots, projects_arg, include_subagents)
        else:
            session_iter = iter_sessions(roots[0], projects_arg, include_subagents=include_subagents)
    else:
        session_iter = _iter_scoped_sessions(_repo_scoped_project_slugs(), include_subagents, roots=roots)

    # Counters are keyed by (skill, thread). Without --include-subagents every
    # thread is "main", which keeps the default output shape unchanged.
    skill_top: dict[tuple[str, str], int] = defaultdict(int)     # -> top-level count
    skill_routed: dict[tuple[str, str], int] = defaultdict(int)  # -> routed count
    skill_slash: dict[tuple[str, str], int] = defaultdict(int)   # -> user-slash count
    routed_pairs: dict[tuple[str, str], int] = defaultdict(int)  # (parent, child) -> count

    for _jsonl, records in session_iter:
        for rec in records:
            sidechain = bool(rec.get("isSidechain"))
            if sidechain and not include_subagents:
                continue
            # Unfiltered runs must still count records that carry no gitBranch,
            # so the branch test applies only when a filter was requested.
            if branch_filter and (rec.get("gitBranch") or "") not in branch_filter:
                continue
            thread = "sidechain" if sidechain else "main"
            rtype = rec.get("type")
            if rtype == "assistant":
                for block in ((rec.get("message") or {}).get("content") or []):
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    if block.get("name") != "Skill":
                        continue
                    skill = _normalize_skill_name((block.get("input") or {}).get("skill") or "")
                    if not skill:
                        continue
                    attribution = _normalize_skill_name(rec.get("attributionSkill") or "")
                    if attribution:
                        skill_routed[(skill, thread)] += 1
                        routed_pairs[(attribution, skill)] += 1
                    else:
                        skill_top[(skill, thread)] += 1
            elif rtype == "user":
                content_raw = (rec.get("message") or {}).get("content", "")
                content_str = content_raw if isinstance(content_raw, str) else _content_text(content_raw)
                for m in re.finditer(r"<command-name>/([^<]+)</command-name>", content_str):
                    skill_slash[(_normalize_skill_name(m.group(1)), thread)] += 1

    keyed = set(skill_top) | set(skill_routed) | set(skill_slash)
    all_skills: set[str] = {skill for skill, _thread in keyed}

    if not all_skills:
        print("No skill invocations found.")
        return

    def _thread_total(s: str, thread: str) -> int:
        return skill_top[(s, thread)] + skill_routed[(s, thread)] + skill_slash[(s, thread)]

    # Sort by total descending, then alphabetically for ties.
    def _skill_total(s: str) -> int:
        return sum(_thread_total(s, thread) for thread in ("main", "sidechain"))

    sorted_skills = sorted(all_skills, key=lambda s: (-_skill_total(s), s))

    scope_parts = [
        "explicit --projects (not repo-scoped)" if projects_arg else "this repo",
        "main+subagents" if include_subagents else "main thread",
    ]
    if branch_filter:
        scope_parts.append(f"branches: {','.join(sorted(branch_filter))}")
    _print_resolved_scope("skill-invocation", "; ".join(scope_parts), roots)

    if include_subagents:
        header = (
            f"{'skill':<40} {'thread':<10} {'top-level':>10}  "
            f"{'routed':>6}  {'user-slash':>10}  {'total':>7}"
        )
    else:
        header = f"{'skill':<40} {'top-level':>10}  {'routed':>6}  {'user-slash':>10}  {'total':>7}"
    print(header)
    print("-" * len(header))

    for skill in sorted_skills:
        # The skill label repeats on every row rather than blanking on
        # continuation rows: consumers grep individual lines out of this table,
        # and a blanked label makes a grepped line unattributable.
        threads = ("main", "sidechain") if include_subagents else ("main",)
        for thread in threads:
            if include_subagents and not _thread_total(skill, thread):
                continue
            top = skill_top[(skill, thread)]
            routed = skill_routed[(skill, thread)]
            slash = skill_slash[(skill, thread)]
            total = top + routed + slash
            if include_subagents:
                print(
                    f"{skill:<40} {thread:<10} {top:>10}  "
                    f"{routed:>6}  {slash:>10}  {total:>7}"
                )
            else:
                print(f"{skill:<40} {top:>10}  {routed:>6}  {slash:>10}  {total:>7}")

    if routed_pairs:
        print("\nROUTED PAIRS (parent -> child : count)")
        for (parent, child), count in sorted(routed_pairs.items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"  {parent} -> {child} : {count}")

    # Classification summary: load-bearing, routed-only, slash-only. Counts
    # aggregate across threads — the classification answers a per-skill
    # question ("is this description load-bearing?"), not a per-thread one.
    def _agg(counter: dict[tuple[str, str], int], s: str) -> int:
        return sum(counter[(s, thread)] for thread in ("main", "sidechain"))

    load_bearing = [s for s in sorted_skills if _agg(skill_top, s) > 0 or _agg(skill_slash, s) > 0]
    routed_only = [
        s for s in sorted_skills
        if _agg(skill_top, s) == 0 and _agg(skill_slash, s) == 0 and _agg(skill_routed, s) > 0
    ]
    slash_only = [
        s for s in sorted_skills
        if _agg(skill_top, s) == 0 and _agg(skill_routed, s) == 0 and _agg(skill_slash, s) > 0
    ]

    print("\nCLASSIFICATION SUMMARY")
    print("  Load-bearing (any top-level or slash invocations):")
    if load_bearing:
        for s in load_bearing:
            print(f"    {s} ({_agg(skill_top, s)} top, {_agg(skill_slash, s)} slash)")
    else:
        print("    (none)")

    print("  Routed-only candidates (zero top-level and zero slash — name-only eligible):")
    if routed_only:
        for s in routed_only:
            print(f"    {s} (0 top, {_agg(skill_routed, s)} routed, 0 slash)")
    else:
        print("    (none)")

    print("  Slash-only candidates (zero top, zero routed — disable-model-invocation eligible):")
    if slash_only:
        for s in slash_only:
            print(f"    {s} (0 top, 0 routed, {_agg(skill_slash, s)} slash)")
    else:
        print("    (none)")


def cmd_subagent_mix(args: argparse.Namespace) -> None:
    """Subagent_type spawn counts per branch, plus a second, agentType-keyed
    table of each type's model mix: Runs (dispatches with a readable
    meta.json + sibling .jsonl — a dangling pair is excluded from this count
    and reported separately under Dangling), Declared (frontmatter `model:`
    from the dispatch's own root's agents/<agentType>.md, or "built-in" with
    no on-disk file), Requested (meta.json's own "model" key, "(none)" when
    absent), and Observed (the modal real model ID across the dispatch's own
    sidechain, via _fam; "mixed" when two distinct real IDs appear), plus
    Actual $ (and, when --reprice-as is given, Counterfactual $ and Delta).

    --since limits both tables to records timestamped on or after the window
    start. --since-date/--until-date instead bound only the Actual $ /
    Counterfactual $ columns, and do so per sidechain assistant record (not
    per dispatch) — a dispatch straddling the window edge must not attribute
    its whole sidechain's dollars to the window just because it started
    inside it. --reprice-as re-prices that same in-window usage at an
    alternate model ID (validated against _MODEL_BASE_INPUT_RATES's keys),
    adding the Counterfactual $ and Delta (Actual − Counterfactual) columns.
    --config-dir (repeatable) scans additional Claude Code config
    directories the same way cost does; under more than one root, both
    branch names and subagent_type values are redacted
    (_assign_root_scoped_redact_label) — subagent_type can name a
    project-scoped custom agent definition, the same disclosure risk
    gitBranch carries — and the model-mix table is keyed on the redacted
    (root, subagent_type) pair so two accounts' same-named agentType never
    merge into one row. --per-session is refused outright under multi-root,
    since it would otherwise join a foreign account's own session-id prefix
    to its branch name.
    """
    roots = _resolve_cost_roots(args, "subagent-mix")
    multi_root = len(roots) > 1
    branch_filter = _branch_filter(args)
    per_session: bool = bool(getattr(args, "per_session", False))

    if multi_root and per_session:
        print(
            "subagent-mix: --per-session is refused when more than one root is in"
            " scope (--config-dir was given) — a per-session row would join a"
            " foreign account's own session-id prefix to its branch name; drop"
            " --per-session or scope to a single profile",
            file=sys.stderr,
        )
        sys.exit(2)

    reprice_as: str | None = getattr(args, "reprice_as", None) or None
    if reprice_as is not None and reprice_as not in _MODEL_BASE_INPUT_RATES:
        valid = ", ".join(sorted(_MODEL_BASE_INPUT_RATES))
        print(
            f"subagent-mix: --reprice-as: unknown model ID {reprice_as!r}; valid values: {valid}",
            file=sys.stderr,
        )
        sys.exit(1)

    if multi_root:
        print(_DO_NOT_PUBLISH_BANNER)
        print(_DO_NOT_PUBLISH_BANNER, file=sys.stderr)

    since_ts, _since_raw = _parse_since_nd_arg(args, "subagent-mix")

    # Bounds the Actual $ / Counterfactual $ columns only, per sidechain
    # assistant record (see _dispatch_usage_summary) -- independent of
    # since_ts above, which keeps its existing dispatch-level scope over
    # every other column in this table.
    since_date_str: str | None = getattr(args, "since_date", None) or None
    until_date_str: str | None = getattr(args, "until_date", None) or None
    dollar_since_ts: float | None = _parse_ts(f"{since_date_str}T00:00:00Z") if since_date_str else None
    dollar_until_ts: float | None = None
    if until_date_str:
        day_start = _parse_ts(f"{until_date_str}T00:00:00Z")
        if day_start is not None:
            dollar_until_ts = day_start + 86400

    # Read once, matching cost's own "never read the clock inside the
    # per-record loop" rationale -- kept as a plain wall-clock read here
    # (rather than cost's separate entry/report split) since no existing or
    # new test in this file asserts on stale-pricing output for subagent-mix.
    today = datetime.now(UTC).date()
    total_unpriced_turns = 0
    total_unpriced_tokens = 0
    all_stale_models: set[str] = set()

    session_iter, scope_label = _resolve_project_scope(args, "subagent-mix", roots=roots)
    _print_resolved_scope("subagent-mix", scope_label, roots)

    resolved_roots = [root.resolve() for root in roots] if multi_root else []
    # Resolved-path-sorted, not _root_index_for_path's raw scan-order position
    # — the same physical root must read as the same account-N here as in
    # every other multi-root diagnostic in this file (_build_redact_map,
    # cost's per-row key), regardless of which profile is currently active.
    redact_ordinals: dict[Path, int] = _redaction_ordinals(roots) if multi_root else {}
    branch_redact_map: dict[tuple[int, str], str] = {}
    # subagent_type redact map is separate from branch_redact_map so the two
    # kinds' per-account counters (account-<K>/branch-<N> vs.
    # account-<K>/agent-type-<N>) never share a numbering sequence.
    subagent_type_redact_map: dict[tuple[int, str], str] = {}
    # Each root's own agents/ directory, so a dispatch's Declared pin is read
    # from the account it actually came from, not this process's own
    # config_dir() — index 0 is always this process's own root (roots[0]),
    # matching root_idx's None-under-single-root convention below.
    agent_dirs = [root.parent / "agents" for root in roots]

    data: dict[str, dict] = defaultdict(
        lambda: {"sessions": 0, "spawns": defaultdict(int), "skills": defaultdict(int)}
    )
    # (possibly redacted) agentType label -> model-mix row. Only created for
    # a type that has at least one meta.json match (even a dangling one) —
    # a dispatch with no matching meta.json at all is excluded entirely,
    # matching cmd_reviewer_yield's own precedent for the same join. Under
    # multi-root, keying on the redacted label (rather than the raw
    # subagent_type) also root-scopes this table: two accounts' same-named
    # agentType get distinct labels and never merge into one row.
    model_mix: dict[str, dict] = defaultdict(lambda: {
        "runs": 0,
        "dangling": 0,
        "requested": defaultdict(int),
        "observed": defaultdict(int),
        "declared_seen": set(),
        "actual_dollars": 0.0,
        "counterfactual_dollars": 0.0,
    })
    declared_pin_cache: dict[tuple[Path, str], str] = {}
    total_meta_read_errors = 0

    for jsonl, records in session_iter:
        root_idx = _root_index_for_path(jsonl, resolved_roots) if multi_root else None
        agents_dir = agent_dirs[root_idx if root_idx is not None else 0]
        dispatch_index, session_meta_read_errors = _index_subagent_dispatches(jsonl)
        total_meta_read_errors += session_meta_read_errors
        session_data: dict[str, dict] = defaultdict(
            lambda: {"spawns": defaultdict(int), "skills": defaultdict(int)}
        )
        for rec in records:
            if rec.get("type") != "assistant" or bool(rec.get("isSidechain")):
                continue
            branch = rec.get("gitBranch") or ""
            if not branch or (branch_filter and branch not in branch_filter):
                continue
            if since_ts is not None:
                rec_ts = _parse_ts(rec.get("timestamp"))
                if rec_ts is None or rec_ts < since_ts:
                    continue
            for block in ((rec.get("message") or {}).get("content") or []):
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                name = block.get("name")
                inp = block.get("input") or {}
                if name in _SPAWN_TOOL_NAMES:
                    stype = inp.get("subagent_type") or "unknown"
                    stype_label = (
                        _assign_root_scoped_redact_label(
                            "agent-type", redact_ordinals[resolved_roots[root_idx]],
                            stype, subagent_type_redact_map
                        )
                        if root_idx is not None
                        else stype
                    )
                    session_data[branch]["spawns"][stype_label] += 1

                    paired = dispatch_index.get(block.get("id") or "")
                    if paired is not None:
                        paired_jsonl, requested_model = paired
                        row = model_mix[stype_label]
                        # _declared_pin reads from the on-disk agent file, so it
                        # needs the real subagent_type (stype), never the
                        # redacted display label (stype_label).
                        row["declared_seen"].add(_declared_pin(stype, agents_dir, declared_pin_cache))
                        (
                            observed, actual_dollars, _dollars_by_class, counterfactual_dollars,
                            dispatch_unpriced_turns, dispatch_unpriced_tokens, dispatch_stale_models,
                        ) = _dispatch_usage_summary(
                            paired_jsonl, dollar_since_ts, dollar_until_ts, reprice_as, today
                        )
                        total_unpriced_turns += dispatch_unpriced_turns
                        total_unpriced_tokens += dispatch_unpriced_tokens
                        all_stale_models |= dispatch_stale_models
                        if observed is None:
                            row["dangling"] += 1
                        else:
                            row["runs"] += 1
                            row["requested"][requested_model or _UNREQUESTED_MODEL_LABEL] += 1
                            row["observed"][observed] += 1
                            row["actual_dollars"] += actual_dollars
                            if reprice_as:
                                row["counterfactual_dollars"] += counterfactual_dollars or 0.0
                elif name == "Skill":
                    skill = inp.get("skill") or ""
                    if skill in REVIEW_SKILLS:
                        session_data[branch]["skills"][skill] += 1

        for branch, sd in session_data.items():
            branch_label = (
                _assign_root_scoped_redact_label(
                    "branch", redact_ordinals[resolved_roots[root_idx]], branch, branch_redact_map
                )
                if root_idx is not None
                else branch
            )
            key = f"{branch_label} [{jsonl.stem[:8]}]" if per_session else branch_label
            d = data[key]
            d["sessions"] += 1
            for stype, cnt in sd["spawns"].items():
                d["spawns"][stype] += cnt
            for skill, cnt in sd["skills"].items():
                d["skills"][skill] += cnt

    if not data:
        print("No data found.")
        return

    print(f"{'Branch':<45} {'Sess':>5} {'Spawns':>7} {'CR':>3} {'PR':>3} {'RR':>3}  Top subagent types")
    print("-" * 120)
    for key in sorted(data):
        d = data[key]
        spawns_total = sum(d["spawns"].values())
        top = sorted(d["spawns"].items(), key=lambda kv: (-kv[1], kv[0]))
        top_str = ", ".join(f"{t}({n})" for t, n in top[:5]) or "—"
        print(
            f"{key:<45} {d['sessions']:>5} {spawns_total:>7} "
            f"{d['skills'].get('code-review', 0):>3} {d['skills'].get('plan-review', 0):>3} "
            f"{d['skills'].get('ready-for-review', 0):>3}  {top_str}"
        )

    if model_mix:
        header = f"{'AgentType':<28} {'Runs':>5} {'Dangling':>9}  {'Declared':<10} {'Actual$':>12}"
        if reprice_as:
            header += f" {'Counterfactual$':>18} {'Delta':>12}"
        header += f" {'Requested':<30} Observed"
        print(f"\n{header}")
        print("-" * len(header))
        for stype_label in sorted(model_mix):
            row = model_mix[stype_label]
            declared = "/".join(sorted(row["declared_seen"])) or _DECLARED_PIN_BUILT_IN
            requested_str = ", ".join(
                f"{k}({v})" for k, v in sorted(row["requested"].items(), key=lambda kv: (-kv[1], kv[0]))
            ) or "—"
            observed_str = ", ".join(
                f"{k}({v})" for k, v in sorted(row["observed"].items(), key=lambda kv: (-kv[1], kv[0]))
            ) or "—"
            line = (
                f"{stype_label:<28} {row['runs']:>5} {row['dangling']:>9}  {declared:<10} "
                f"{_fmt_usd(row['actual_dollars']):>12}"
            )
            if reprice_as:
                delta = row["actual_dollars"] - row["counterfactual_dollars"]
                line += f" {_fmt_usd(row['counterfactual_dollars']):>18} {_fmt_usd(delta):>12}"
            line += f" {requested_str:<30} {observed_str}"
            print(line)
        # Matches cost's own "(N unpriced turns / M tokens excluded from
        # priced spend)" convention verbatim -- an unknown model ID would
        # otherwise silently read as a genuinely zero-cost dispatch.
        if total_unpriced_turns:
            print(
                f"  ({total_unpriced_turns:,} unpriced turns / {total_unpriced_tokens:,}"
                " tokens excluded from priced spend)"
            )
        # Matches cost's own STALE PRICING banner (_MODEL_RATE_EXPIRES),
        # simplified to the model list -- this table has no single "the
        # figures below" scope to point a successor-rate hint at.
        if all_stale_models:
            print(
                "STALE PRICING — today is past the re-verify-by date for: "
                + ", ".join(sorted(all_stale_models))
                + f". Re-check rates at {_PRICING_SOURCE_URL} before publishing this table's dollar figures."
            )
    # Printed even when model_mix is empty (every dispatch's meta.json was
    # malformed) -- mirrors cmd_reviewer_yield's identical diagnostic, which
    # prints on its own early-return path for the same reason.
    if total_meta_read_errors:
        print(f"\n  ({total_meta_read_errors:,} meta.json files failed to parse, excluded)")


# Reviewer verdict-text patterns for reviewer-yield's dispatch-outcome join.
# Loosened from each reviewer agent's documented `**No X concerns**` /
# `Found <N> issues.` / `**Approve with concerns**` / `**Request changes**`
# contract (claude/.claude/agents/*.md) to tolerate markdown-bold,
# singular/plural, and case variance.
_REVIEWER_NO_CONCERNS_GAP_MAX_CHARS = 40  # bounds "no <...> concerns" to one short phrase, not a whole paragraph
_REVIEWER_NO_CONCERNS_RE = re.compile(
    rf"\bno\b[\w\s/-]{{0,{_REVIEWER_NO_CONCERNS_GAP_MAX_CHARS}}}?\bconcerns\b", re.IGNORECASE
)
_REVIEWER_FOUND_ISSUES_RE = re.compile(r"found\s+(\d+)\s+issues?\b", re.IGNORECASE)
_REVIEWER_APPROVE_WITH_CONCERNS_RE = re.compile(r"\bapprove with concerns\b", re.IGNORECASE)
_REVIEWER_REQUEST_CHANGES_RE = re.compile(r"\brequest changes\b", re.IGNORECASE)

# _classify_reviewer_verdict's bucket labels, shared with cmd_reviewer_yield's
# aggregation branch — named so a typo in either can't silently fall through
# to the "unclassified" bucket.
_REVIEWER_VERDICT_FINDINGS_FOUND = "findings-found"
_REVIEWER_VERDICT_ZERO_FINDING = "zero-finding"
_REVIEWER_VERDICT_UNCLASSIFIED = "unclassified"

# Table 2's minimum Active count per (agent type, bucket) row before Rate is
# reportable — GH-558 (Part B)'s decision 4.
_REVIEWER_YIELD_ACTIVE_FLOOR = 10


def _index_subagent_dispatches(jsonl: Path) -> tuple[dict[str, tuple[Path, str | None]], int]:
    """Map each subagent dispatch's toolUseId to (its paired .jsonl path,
    requested model), for one session.

    Reads subagents/*.meta.json directly rather than through iter_sessions'
    include_subagents merge — that merge flattens every subagent file's
    records into one list with no per-file boundary, which cannot answer
    "this specific dispatch's own last assistant text." The requested model
    is meta.json's own "model" key (absent when the dispatch carried no
    explicit model request) — reading it here, alongside the toolUseId this
    function already parses meta.json for, avoids a second per-dispatch
    meta.json read in subagent-mix's model-mix join.

    Returns (index, meta_read_errors): meta_read_errors counts *.meta.json
    files present but unusable — invalid JSON, valid JSON missing a
    string-typed toolUseId, or valid JSON whose "model" key is present but
    not a string — distinct from a dispatch with no meta.json at all (the
    caller's own, separately-documented exclusion path). meta.json is
    written by Claude Code's own harness, not by this repo, so its "model"
    and "toolUseId" fields are external input: a non-string value for either
    (a future harness change, or a corrupted file) is excluded here rather
    than reaching a caller that would use it as a dict key and crash with an
    uncaught TypeError.
    """
    subagent_dir = jsonl.parent / jsonl.stem / SUBAGENT_SUBDIR
    index: dict[str, tuple[Path, str | None]] = {}
    meta_read_errors = 0
    if not subagent_dir.is_dir():
        return index, meta_read_errors
    for meta_path in sorted(subagent_dir.glob("*.meta.json")):
        try:
            meta = json.loads(meta_path.read_text())
        except (OSError, json.JSONDecodeError):
            meta_read_errors += 1
            continue
        tool_use_id = meta.get("toolUseId")
        if not isinstance(tool_use_id, str) or not tool_use_id:
            meta_read_errors += 1
            continue
        requested_model = meta.get("model")
        if requested_model is not None and not isinstance(requested_model, str):
            meta_read_errors += 1
            continue
        agent_id = meta_path.name.removesuffix(".meta.json")
        index[tool_use_id] = (meta_path.parent / f"{agent_id}.jsonl", requested_model)
    return index, meta_read_errors


_AGENT_FRONTMATTER_MODEL_RE = re.compile(r"(?m)^model:\s*(\S+)\s*$")


def _agent_frontmatter_model(agent_file_text: str) -> str | None:
    """Extract the `model:` frontmatter value from one agent file's raw text.

    Scoped to the leading YAML block (between the first pair of `---` lines)
    so a `model:` mention in the agent's prose body is never matched. Returns
    None when the text has no frontmatter block or the block has no `model:`
    key — the caller renders that as "built-in".
    """
    if not agent_file_text.startswith("---"):
        return None
    end = agent_file_text.find("\n---", 3)
    if end == -1:
        return None
    match = _AGENT_FRONTMATTER_MODEL_RE.search(agent_file_text[3:end])
    return match.group(1) if match else None


_DECLARED_PIN_BUILT_IN = "built-in"

# subagent_type values are harness-generated identifiers (e.g. "staff-sdet",
# "general-purpose") -- never containing "/" or "..". _declared_pin enforces
# this shape before building a filesystem path from one, since under
# --config-dir that value can originate from a scanned foreign root's own
# transcript data, not just this process's own dispatches.
_AGENT_TYPE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _declared_pin(
    agent_type: str, agents_dir: Path, declared_pin_cache: dict[tuple[Path, str], str]
) -> str:
    """Declared model pin for one subagent_type, from agents_dir/<agent_type>.md
    frontmatter — agents_dir is the *dispatch's own* root's agents/ directory
    (not necessarily this process's own config_dir()), since under
    --config-dir a dispatch's declared pin must be read from the account it
    actually came from. Cached per (agents_dir, agent_type), since the same
    agent_type name can resolve to a different on-disk file under a
    different root. "built-in" when no on-disk agent file exists
    (general-purpose, claude-code-guide, Plan carry none), the file has no
    `model:` frontmatter — Claude Code's own default, not a pin this repo
    can assert on — or agent_type fails the on-disk agent-file naming
    allowlist (agent_type is transcript-sourced data; without this guard, an
    absolute-path or `../`-laden value would build a path outside
    agents_dir via Path.__truediv__'s os.path.join semantics).
    """
    key = (agents_dir, agent_type)
    if key in declared_pin_cache:
        return declared_pin_cache[key]
    if not _AGENT_TYPE_NAME_RE.fullmatch(agent_type):
        pin = _DECLARED_PIN_BUILT_IN
    else:
        agent_file = agents_dir / f"{agent_type}.md"
        try:
            text = agent_file.read_text()
        except OSError:
            pin = _DECLARED_PIN_BUILT_IN
        else:
            pin = _agent_frontmatter_model(text) or _DECLARED_PIN_BUILT_IN
    declared_pin_cache[key] = pin
    return pin


def _dispatch_usage_summary(
    jsonl_path: Path,
    since_ts: float | None,
    until_ts: float | None,
    reprice_as: str | None,
    today: date,
) -> tuple[str | None, float, dict[str, float], float | None, int, int, set[str]]:
    """Modal observed-model family plus priced dollar totals for one subagent
    dispatch's own transcript.

    Reads every assistant record's message.model in jsonl_path. Two or more
    distinct real (non-"<synthetic>") model IDs report the literal bucket
    "mixed", never collapsed into one family — an unstable dispatch should be
    visible, not silently assigned one of its models. A single distinct real
    model ID (regardless of how many turns used it) resolves via _fam. No
    real model ID at all (only "<synthetic>" turns) resolves via
    _fam("<synthetic>") -> "other". This bucket is computed over every
    assistant record in the file, regardless of since_ts/until_ts — a
    dispatch's model identity isn't scoped to a reporting window.

    actual_dollars/dollars_by_class price (via _price_turn) only the
    assistant records whose own timestamp falls in [since_ts, until_ts) —
    filtered per record, not by the dispatch's own start time, since a
    dispatch's sidechain can straddle a window edge and a start-time-only
    filter would attribute post-cutoff spend to an "in-window" total.
    counterfactual_dollars re-prices that same in-window usage at
    reprice_as, or is None when reprice_as is not given.

    unpriced_turns/unpriced_tokens count in-window turns _price_turn couldn't
    price (unknown model ID) — matches cost's own convention of surfacing
    this rather than letting it silently read as zero-cost spend.
    stale_models collects any priced model past its _MODEL_RATE_EXPIRES
    re-verify-by date, evaluated against the caller-supplied today (never
    read from the wall clock here, so a caller can hold this deterministic
    for tests) — mirrors cost's own staleness check.

    Returns (observed_bucket, actual_dollars, dollars_by_class,
    counterfactual_dollars, unpriced_turns, unpriced_tokens, stale_models).
    observed_bucket is None, and every other value is 0/0.0/{}/None/empty,
    when jsonl_path doesn't exist or can't be read — the caller's own
    "dangling meta.json" exclusion path (a run requires a readable sibling
    .jsonl, not just a valid meta.json).
    """
    if not jsonl_path.is_file():
        return None, 0.0, {}, None, 0, 0, set()
    real_model_ids: set[str] = set()
    saw_any_model = False
    dollars_by_class: dict[str, float] = defaultdict(float)
    counterfactual_total = 0.0
    unpriced_turns = 0
    unpriced_tokens = 0
    stale_models: set[str] = set()
    try:
        with open(jsonl_path) as fh:
            for raw in fh:
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") != "assistant":
                    continue
                msg = rec.get("message") or {}
                model = msg.get("model")
                if not model:
                    continue
                saw_any_model = True
                if model != "<synthetic>":
                    real_model_ids.add(model)

                usage = msg.get("usage")
                if not usage:
                    continue
                if since_ts is not None or until_ts is not None:
                    rec_ts = _parse_ts(rec.get("timestamp"))
                    if rec_ts is None:
                        continue
                    if since_ts is not None and rec_ts < since_ts:
                        continue
                    if until_ts is not None and rec_ts >= until_ts:
                        continue
                turn_dollars, _ctx, turn_unpriced_tokens = _price_turn(model, usage)
                if turn_dollars is None:
                    unpriced_turns += 1
                    unpriced_tokens += turn_unpriced_tokens
                else:
                    for cls, amount in turn_dollars.items():
                        dollars_by_class[cls] += amount
                    if today > _MODEL_RATE_EXPIRES[model]:
                        stale_models.add(model)
                if reprice_as:
                    cf_dollars, _cf_ctx, _cf_unpriced = _price_turn(reprice_as, usage)
                    if cf_dollars is not None:
                        counterfactual_total += sum(cf_dollars.values())
    except OSError:
        return None, 0.0, {}, None, 0, 0, set()

    if len(real_model_ids) >= 2:
        observed = "mixed"
    elif real_model_ids:
        observed = _fam(next(iter(real_model_ids)))
    else:
        observed = _fam("<synthetic>") if saw_any_model else "other"

    actual_dollars = sum(dollars_by_class.values())
    counterfactual_dollars = counterfactual_total if reprice_as else None
    return (
        observed, actual_dollars, dict(dollars_by_class), counterfactual_dollars,
        unpriced_turns, unpriced_tokens, stale_models,
    )


def _scan_reviewer_transcript(jsonl_path: Path) -> tuple[str, list[str], list[str], str, bool]:
    """Walk one transcript file once, collecting all reviewer-yield join inputs.

    Returns (last_assistant_text, write_content_blobs, write_target_paths,
    transcript_cwd, read_error):
      - last_assistant_text: the last non-empty assistant text block, or ''.
        A trailing assistant record with no text (e.g. a final tool-only turn)
        does not blank out an earlier one — this walks the whole file and
        keeps the most recent non-empty text seen, matching "last assistant
        text block" rather than "last assistant record's text, possibly
        empty."
      - write_content_blobs: every Write tool_use's input.content string
        found along the same walk, in file order.
      - write_target_paths: every Write tool_use's input.file_path found
        along the same walk, in file order — this dispatch's own findings
        file is almost always among them, giving the caller a
        path-normalized set-membership exclusion (see
        _dispatch_self_reference_keys) instead of fragile free-text-prose
        matching against the dispatching prompt.
      - transcript_cwd: the cwd field from the first record in this
        transcript that carries one, or '' if none do. Reviewer-cited
        relative paths were written from the reviewer subagent's own
        working directory, not the dispatching parent's, which can diverge
        under an isolation:worktree reviewer dispatch (ledger row A).
      - read_error: True on OSError opening/reading jsonl_path. A read
        failure is not a legitimate zero-citation transcript, so the caller
        must exclude it from a coverage denominator rather than count it as
        one — every other field is ("", [], [], "") in this case, matching
        the prior ''-on-OSError contract for the text.
    """
    last_text = ""
    write_content_blobs: list[str] = []
    write_target_paths: list[str] = []
    transcript_cwd = ""
    try:
        with open(jsonl_path) as fh:
            for raw in fh:
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not transcript_cwd:
                    rec_cwd = rec.get("cwd")
                    if isinstance(rec_cwd, str) and rec_cwd:
                        transcript_cwd = rec_cwd
                if rec.get("type") != "assistant":
                    continue
                content = (rec.get("message") or {}).get("content", "")
                if isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict) or block.get("type") != "tool_use":
                            continue
                        if block.get("name") != "Write":
                            continue
                        block_input = block.get("input") or {}
                        blob = block_input.get("content")
                        if isinstance(blob, str):
                            write_content_blobs.append(blob)
                        target = block_input.get("file_path")
                        if isinstance(target, str) and target:
                            write_target_paths.append(target)
                text = _content_text(content)
                if text.strip():
                    last_text = text
    except OSError:
        return "", [], [], "", True
    return last_text, write_content_blobs, write_target_paths, transcript_cwd, False


def _classify_reviewer_verdict(text: str) -> tuple[str, int]:
    """Classify one reviewer subagent's final verdict text.

    Returns (bucket, findings): bucket is one of _REVIEWER_VERDICT_FINDINGS_FOUND,
    _REVIEWER_VERDICT_ZERO_FINDING, or _REVIEWER_VERDICT_UNCLASSIFIED; findings
    is the parsed N for a findings-found verdict, else 0.
    "Found 0 issues" is a zero-finding verdict, not findings-found, despite
    matching the found-issues pattern. "Approve with concerns"/"Request
    changes" verdicts carry real findings but no derivable count, so they
    land in findings-found with 0 — the caller's total-findings sum is a
    lower bound, not every findings-found dispatch's true count.
    """
    m = _REVIEWER_FOUND_ISSUES_RE.search(text)
    if m:
        n = int(m.group(1))
        return (_REVIEWER_VERDICT_FINDINGS_FOUND, n) if n > 0 else (_REVIEWER_VERDICT_ZERO_FINDING, 0)
    if _REVIEWER_NO_CONCERNS_RE.search(text):
        return (_REVIEWER_VERDICT_ZERO_FINDING, 0)
    if _REVIEWER_APPROVE_WITH_CONCERNS_RE.search(text) or _REVIEWER_REQUEST_CHANGES_RE.search(text):
        return (_REVIEWER_VERDICT_FINDINGS_FOUND, 0)
    return (_REVIEWER_VERDICT_UNCLASSIFIED, 0)


# Generous bound for any realistic cited path (worktree prefix + repo-relative
# suffix); still a hard cap so a run of pathish characters (a code-fence
# border, `tree` output) can't grow one match unboundedly.
_CITED_PATH_CANDIDATE_MAX_CHARS = 300
# One flat, bounded character class — no group is itself quantified, unlike
# the natural "(?:[\w.-]+/)+[\w.-]+" shape, which backtracks catastrophically
# on a long non-matching slash run. Same safety property as
# _DENIAL_HOOK_NAME_RE, copied for the same reason. Deliberately unselective:
# a bare word matches too (no `/` or `.` required here) — separator and
# extension filtering happens in _normalize_cited_path, not in extraction.
_CITED_PATH_CANDIDATE_RE = re.compile(rf"[\w./~:-]{{1,{_CITED_PATH_CANDIDATE_MAX_CHARS}}}")


def _extract_cited_paths(text: str) -> set[str]:
    """Extract raw candidate path strings from one blob of reviewer prose.

    Returns every run matched by _CITED_PATH_CANDIDATE_RE, deduplicated —
    including runs that turn out to be plain prose words or bare filenames.
    _normalize_cited_path is what decides whether a candidate is a real,
    join-able path; this function only tokenizes.
    """
    return set(_CITED_PATH_CANDIDATE_RE.findall(text))


# Strips a trailing ":line" or ":line:col" suffix (e.g. "foo.py:42" or
# "foo.py:42:7") — normalization step 1.
_CITED_PATH_LINE_SUFFIX_RE = re.compile(r":\d+(?::\d+)?$")
# Matches one ".claude/worktrees/<branch>/" segment, anywhere in the path —
# normalization step 6. `[^/]+` takes only the branch's first path segment,
# a documented bias toward under-stripping on a slash-containing branch slug
# (see _normalize_cited_path's docstring); this is not losslessly decidable
# from the path alone with zero filesystem access.
_CITED_PATH_WORKTREE_PREFIX_RE = re.compile(r"\.claude/worktrees/[^/]+/")


def _normalize_cited_path(candidate: str, cwd: str) -> str | None:
    """Normalize one raw candidate from _extract_cited_paths into a join key,
    or None if the candidate is discarded.

    Lexical only: no Path.resolve(), os.path.realpath, or stat — those chase
    symlinks (e.g. macOS's /tmp -> /private/tmp) and would make the join key
    depend on where each analyst's clone lives, and an OSError from that
    traversal would embed the offending path in its message with no
    top-level handler to catch it. The one exception is os.path.expanduser's
    own pwd.getpwnam lookup for an "~otheruser" candidate (step 3, below) —
    that candidate is discarded regardless of whether the lookup succeeds, so
    it never affects the key of a candidate this function actually resolves.

    Ordered steps (an implementer will get the order wrong otherwise):
      1. Strip a trailing ":line" or ":line:col" suffix.
      2. Reject a candidate with no directory separator — a bare "SKILL.md"
         is ordinary prose, not a path, and resolving it against `cwd` would
         manufacture a false in-repo match.
      3. Expand a leading "~" lexically (os.path.expanduser). A candidate
         still starting with "~" afterward is the unexpandable "~otheruser"
         form and is discarded, not resolved via a directory-service lookup.
         This runs before step 4 (relative-path resolution) because a
         "~"-prefixed candidate is neither absolute nor genuinely relative —
         expanduser is a no-op on a non-leading "~", so this must expand it
         before anything joins it to `cwd`.
      4. Resolve ".." and relative segments against the **unstripped** `cwd`,
         for a candidate still relative after step 3. Must precede step 6:
         "../../../.venv/bin/pytest" (this repo's own CLAUDE.md idiom) means
         three levels above the worktree, and resolving it against an
         already-worktree-stripped `cwd` would silently change what
         directory it names.
      5. Collapse a leading "/private/tmp" to "/tmp" (macOS-only aliasing;
         inert on Linux, where that prefix cannot appear in a transcript).
      6. Strip ".claude/worktrees/<branch>/" to fixpoint, not once, so a
         nested worktree (an isolation:worktree agent under a
         worktree-anchored parent) doesn't leave a dangling second segment.
    """
    path = _CITED_PATH_LINE_SUFFIX_RE.sub("", candidate)  # 1

    if "/" not in path:  # 2
        return None

    if path.startswith("~"):  # 3
        path = os.path.expanduser(path)
        if path.startswith("~"):
            return None  # unexpandable "~otheruser/..." form

    if not path.startswith("/"):  # 4 — still relative after step 3
        path = os.path.normpath(os.path.join(cwd, path))

    if path.startswith("/private/tmp"):  # 5
        path = "/tmp" + path[len("/private/tmp"):]

    while True:  # 6 — to fixpoint
        stripped = _CITED_PATH_WORKTREE_PREFIX_RE.sub("", path)
        if stripped == path:
            break
        path = stripped

    return hashlib.sha256(path.encode()).hexdigest()[:16]


def _is_reviewer_subagent_type(stype: str) -> bool:
    """True for a subagent_type in the shared reviewer-agent set
    (_REVIEWER_PREFIX/_REVIEWER_EXACT_NAMES), used by the
    dispatch-classification loop to decide which Agent/Task tool_use blocks
    to aggregate."""
    return stype.startswith(_REVIEWER_PREFIX) or stype in _REVIEWER_EXACT_NAMES


def _code_write_target_path(tool_input: dict) -> str | None:
    """A code-write tool_use's target path. NotebookEdit carries
    notebook_path instead of file_path; MultiEdit's single file_path already
    covers its own case."""
    return tool_input.get("file_path") or tool_input.get("notebook_path")


def _build_tool_result_ts_map(records: list[dict], since_ts: float | None) -> dict[str, float]:
    """Map each tool_use_id to its tool_result record's timestamp, for one
    session's already-materialized records — no new file I/O. tool_result
    blocks live on user-type records, not the assistant-type records the
    rest of reviewer-yield's loop filters to. A tool_result whose own
    timestamp is missing/unparseable, or outside the --since window, is
    omitted — the caller then treats that dispatch's Active/Edited ordering
    as undecidable rather than guessing at it.
    """
    tool_result_ts: dict[str, float] = {}
    for rec in records:
        if rec.get("type") != "user":
            continue
        rec_ts = _parse_ts(rec.get("timestamp"))
        if rec_ts is None:
            continue
        if since_ts is not None and rec_ts < since_ts:
            continue
        content = (rec.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            tid = block.get("tool_use_id")
            if tid:
                tool_result_ts[tid] = rec_ts
    return tool_result_ts


def _index_parent_edits(records: list[dict], since_ts: float | None) -> dict[str, float]:
    """Parent-main-thread code-write edit index for one session: normalized
    path key -> latest edit timestamp. A second pass over the same
    already-materialized records list iter_sessions handed the caller — no
    new parent-side file I/O.

    A record with no cwd field indexes its edit under a key normalized
    against "" rather than being skipped, so it can silently miss a join
    against a citation whose own cwd is a real path — low-likelihood, since
    Claude Code populates cwd on essentially every record, and not currently
    tested.
    """
    index: dict[str, float] = {}
    for rec in records:
        if rec.get("type") != "assistant" or bool(rec.get("isSidechain")):
            continue
        rec_ts = _parse_ts(rec.get("timestamp"))
        if rec_ts is None:
            continue
        if since_ts is not None and rec_ts < since_ts:
            continue
        cwd = rec.get("cwd") or ""
        for block in (rec.get("message") or {}).get("content") or []:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            if block.get("name") not in _CODE_WRITE_TOOLS:
                continue
            raw_path = _code_write_target_path(block.get("input") or {})
            if not raw_path:
                continue
            key = _normalize_cited_path(raw_path, cwd)
            if key is not None:
                index[key] = max(index.get(key, float("-inf")), rec_ts)
    return index


# Both "~/.claude/plans/x.md" and a repo-relative ".claude/plans/x.md" share
# this literal tail, so a substring check needs no cwd or normalization.
_CITED_PATH_PLAN_FILE_MARKER = ".claude/plans/"


def _is_plan_file_candidate(candidate: str) -> bool:
    """True for a candidate citing a plan file under ~/.claude/plans/ or an
    in-repo .claude/plans/ — a /plan-review dispatch routinely cites the very
    plan the parent session then edits, a guaranteed self-match that would
    otherwise inflate the cited/edited overlap with no fix-work signal."""
    return _CITED_PATH_PLAN_FILE_MARKER in candidate


def _dispatch_self_reference_keys(write_target_paths: list[str], transcript_cwd: str) -> set[str]:
    """Normalized keys of this dispatch's own Write targets (its findings
    file and any other file it wrote) — a path-normalized set-membership
    exclusion, not free-text prose matching. The dispatching parent's prompt
    routinely names the very files under review ("review foo.py, bar.py"),
    so extracting candidates from that prompt text and excluding all of them
    would silently drop legitimate citations of files that really were the
    ones with the issue; the reviewer's own recorded Write targets carry no
    such false-positive risk.
    """
    keys: set[str] = set()
    for target in write_target_paths:
        key = _normalize_cited_path(target, transcript_cwd)
        if key is not None:
            keys.add(key)
    return keys


def _reviewer_yield_cited_keys(
    last_assistant_text: str, write_content_blobs: list[str], cwd: str, self_ref_keys: set[str]
) -> set[str]:
    """Normalized citation keys for one reviewer dispatch: candidates from
    both the last assistant text and every Write blob (deduplicated via set
    union), minus plan-file self-matches and the dispatch's own
    self-referenced paths (see _is_plan_file_candidate and
    _dispatch_self_reference_keys)."""
    raw_candidates = _extract_cited_paths(last_assistant_text)
    for blob in write_content_blobs:
        raw_candidates |= _extract_cited_paths(blob)
    keys: set[str] = set()
    for candidate in raw_candidates:
        if _is_plan_file_candidate(candidate):
            continue
        key = _normalize_cited_path(candidate, cwd)
        if key is None or key in self_ref_keys:
            continue
        keys.add(key)
    return keys


def _compute_reviewer_yield_data(
    session_iter,
    since_ts: float | None = None,
    until_ts: float | None = None,
) -> dict:
    """Corpus-wide reviewer-dispatch accumulation behind both
    cmd_reviewer_yield's own report and cost-ledger's per-week
    reviewer_gap_pp, extracted so the two share one pass over session_iter
    instead of two implementations kept in sync by hand.

    since_ts/until_ts are explicit epoch-second boundaries (until_ts
    exclusive) rather than _parse_since_nd_arg's relative-day CLI parsing,
    which has no until concept, so a caller can bound an exact week. Only
    the reviewer-dispatch detection loop applies until_ts — the paired
    tool_result/edit-index helpers below it stay since_ts-only, matching
    cmd_reviewer_yield's own pre-existing (unwindowed) use of them.
    """
    # agent_type -> {dispatches, findings_found, zero_finding, unclassified, total_findings}
    agg: dict[str, dict[str, int]] = defaultdict(
        lambda: {"dispatches": 0, "findings_found": 0, "zero_finding": 0, "unclassified": 0, "total_findings": 0}
    )
    # (agent_type, bucket) -> {cited, active, edited}
    agg2: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: {"cited": 0, "active": 0, "edited": 0})
    meta_read_errors = 0
    transcript_read_errors = 0

    for jsonl, records in session_iter:
        dispatch_index, session_meta_read_errors = _index_subagent_dispatches(jsonl)
        meta_read_errors += session_meta_read_errors

        tool_result_ts = _build_tool_result_ts_map(records, since_ts)
        # Subagent-transcript edit reads measured ~16.2s slower than a parent-only
        # scan on a --since 30d corpus run — parent-only index shipped; see
        # docs/transcript-analysis.md for the tradeoff.
        edit_index = _index_parent_edits(records, since_ts)
        overall_max_edit_ts = max(edit_index.values()) if edit_index else None

        for rec in records:
            if rec.get("type") != "assistant" or bool(rec.get("isSidechain")):
                continue
            if since_ts is not None or until_ts is not None:
                rec_ts = _parse_ts(rec.get("timestamp"))
                if rec_ts is None:
                    continue
                if since_ts is not None and rec_ts < since_ts:
                    continue
                if until_ts is not None and rec_ts >= until_ts:
                    continue
            for block in ((rec.get("message") or {}).get("content") or []):
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                if block.get("name") not in _SPAWN_TOOL_NAMES:
                    continue
                block_input = block.get("input") or {}
                stype = block_input.get("subagent_type") or ""
                if not _is_reviewer_subagent_type(stype):
                    continue
                tool_use_id = block.get("id") or ""
                paired = dispatch_index.get(tool_use_id)
                if paired is None:
                    continue  # no matching meta.json — excluded entirely, not "unclassified"
                paired_jsonl, _requested_model = paired
                last_assistant_text, write_content_blobs, write_target_paths, transcript_cwd, read_error = (
                    _scan_reviewer_transcript(paired_jsonl)
                )
                if read_error:
                    transcript_read_errors += 1
                bucket, n = _classify_reviewer_verdict(last_assistant_text)
                row = agg[stype]
                row["dispatches"] += 1
                if bucket == _REVIEWER_VERDICT_FINDINGS_FOUND:
                    row["findings_found"] += 1
                    row["total_findings"] += n
                elif bucket == _REVIEWER_VERDICT_ZERO_FINDING:
                    row["zero_finding"] += 1
                else:
                    row["unclassified"] += 1

                if bucket == _REVIEWER_VERDICT_UNCLASSIFIED:
                    continue  # table 2 reports "excluded" for this bucket — no citation scoring

                self_ref_keys = _dispatch_self_reference_keys(write_target_paths, transcript_cwd)
                cited_keys = _reviewer_yield_cited_keys(
                    last_assistant_text, write_content_blobs, transcript_cwd, self_ref_keys
                )
                if not cited_keys:
                    continue
                row2 = agg2[(stype, bucket)]
                row2["cited"] += 1

                threshold = tool_result_ts.get(tool_use_id)
                if threshold is None:
                    continue  # no paired tool_result, or its timestamp was unparseable — ordering undecidable
                if overall_max_edit_ts is None or overall_max_edit_ts <= threshold:
                    continue  # no qualifying edit anywhere in the session after this dispatch returned
                row2["active"] += 1
                if any(edit_index.get(k, float("-inf")) > threshold for k in cited_keys):
                    row2["edited"] += 1

    return {
        "agg": agg,
        "agg2": agg2,
        "meta_read_errors": meta_read_errors,
        "transcript_read_errors": transcript_read_errors,
    }


def cmd_reviewer_yield(args: argparse.Namespace) -> None:
    """Per-reviewer-agent-type dispatch-to-verdict yield, plus cited-path edit overlap.

    Joins each main-thread reviewer-agent dispatch (Agent/Task tool_use with
    subagent_type in the reviewer set — _REVIEWER_PREFIX/_REVIEWER_EXACT_NAMES)
    to its own subagent
    transcript via subagents/<id>.meta.json's toolUseId field, then
    classifies that transcript's last assistant text block as findings-found,
    zero-finding, or unclassified. A dispatch with no matching meta.json is
    excluded entirely (not counted as unclassified) — meta.json is the only
    signal that a subagent transcript for this dispatch exists at all. A
    second, distinct exclusion path is a meta.json file that exists but is
    unreadable (invalid JSON) or missing toolUseId — also excluded entirely,
    and corpus-wide counted in the printed meta-read-errors line.

    A "findings-found" verdict comes from either a numeric "Found <N>
    issues" verdict (contributes N to the Findings column) or a bulleted
    "Approve with concerns"/"Request changes" verdict with no derivable
    count (contributes 0) — the printed Findings total is therefore a lower
    bound on actual findings, not an exact count.

    A second table reports, per (agent type, bucket), whether the dispatch's
    own cited paths were later edited: Cited (>=1 extracted citation after
    excluding the dispatch's own self-referenced/plan-file candidates),
    Active (of those, the session recorded ANY code edit afterward, the null
    control for "was the session still working at all"), and Edited (of the
    Active ones, a cited path itself was among the edited paths). Rate =
    Edited / Active, so it cannot exceed 100%. Active/Edited currently
    reflect parent-main-thread edits only — subagent-transcript edit reads
    were measured against Verification 7(a)'s cost gate (delta ~16.2s over
    the inherited 13.5s baseline) and excluded under the gate's own
    pre-committed fallback, so this undercounts real fix work whenever it
    happened inside a code-writer dispatch, which this repo's own CLAUDE.md
    mandates for implementation work. The unclassified
    bucket is not scored (prints "excluded" for Cited/Active/Edited/Rate) —
    an unreadable subagent transcript lands there via its empty verdict text
    and is separately counted in the printed read-error line, never entered
    as a legitimate zero-citation dispatch.

    --redact is accepted for CLI parity with cost/audit-routing. Cited-path
    candidates are held only as sha256 digests (_normalize_cited_path), never
    as raw paths, so no path can reach this subcommand's aggregate-only
    output by construction — this does not cover the pre-existing
    --projects scope-header line (_print_resolved_scope), a separate,
    unfixed channel shared by every subcommand.

    Delegates its entire accumulation to _compute_reviewer_yield_data instead
    of running its own pass over session_iter, so this report and
    cost-ledger's per-week reviewer_gap_pp can never drift apart.
    """
    since_ts, since_raw = _parse_since_nd_arg(args, "reviewer-yield")
    since_label = since_raw or ""

    roots = _resolve_scan_roots(args)
    session_iter, scope_label = _resolve_project_scope(args, "reviewer-yield", roots=roots)
    _print_resolved_scope("reviewer-yield", scope_label, roots)

    data = _compute_reviewer_yield_data(session_iter, since_ts=since_ts)
    agg = data["agg"]
    agg2 = data["agg2"]
    meta_read_errors = data["meta_read_errors"]
    transcript_read_errors = data["transcript_read_errors"]

    title_since = f"last {since_label}" if since_label else "all time"
    print(f"\n## Reviewer-agent yield ({title_since})\n")

    if not agg:
        print("No reviewer-agent dispatches found.")
        if meta_read_errors:
            print(f"  ({meta_read_errors:,} meta.json files failed to parse, excluded)")
        return

    # Findings is a lower bound: it sums parsed "Found <N> issues" counts plus
    # 0 for each uncounted "Approve with concerns"/"Request changes" verdict.
    header = f"{'AgentType':<28} {'Dispatches':>10} {'Found':>7} {'Zero':>6} {'Unclass':>8} {'Findings':>9}"
    print(header)
    print("-" * len(header))
    for stype in sorted(agg):
        row = agg[stype]
        print(
            f"{stype:<28} {row['dispatches']:>10} {row['findings_found']:>7} "
            f"{row['zero_finding']:>6} {row['unclassified']:>8} {row['total_findings']:>9}"
        )
    if meta_read_errors:
        print(f"\n  ({meta_read_errors:,} meta.json files failed to parse, excluded)")

    print(f"\n## Reviewer-agent cited-path edit overlap ({title_since})\n")
    header2 = (
        f"{'AgentType':<28} {'Bucket':<15} {'Dispatches':>10} {'Cited':>6} {'Active':>6} {'Edited':>6} {'Rate':>12}"
    )
    print(header2)
    print("-" * len(header2))
    for stype in sorted(agg):
        row = agg[stype]
        for bucket, dispatches in (
            (_REVIEWER_VERDICT_FINDINGS_FOUND, row["findings_found"]),
            (_REVIEWER_VERDICT_ZERO_FINDING, row["zero_finding"]),
            (_REVIEWER_VERDICT_UNCLASSIFIED, row["unclassified"]),
        ):
            if dispatches == 0:
                continue
            if bucket == _REVIEWER_VERDICT_UNCLASSIFIED:
                cited_s = active_s = edited_s = rate_s = "excluded"
            else:
                row2 = agg2[(stype, bucket)]
                cited_s, active_s, edited_s = str(row2["cited"]), str(row2["active"]), str(row2["edited"])
                rate_s = (
                    "insufficient"
                    if row2["active"] < _REVIEWER_YIELD_ACTIVE_FLOOR
                    else f"{row2['edited'] / row2['active']:>6.1%}"
                )
            print(
                f"{stype:<28} {bucket:<15} {dispatches:>10} {cited_s:>6} {active_s:>6} {edited_s:>6} {rate_s:>12}"
            )
    print("\n  (Active/Edited count parent-main-thread edits only; see docs for the cost-gate fallback.)")
    if transcript_read_errors:
        print(f"\n  ({transcript_read_errors:,} reviewer transcripts failed to read, excluded from Cited)")


def cmd_skill_pair(args: argparse.Namespace) -> None:
    """Pairing rate between two skills, bucketed by ISO week.

    Counts Skill tool_use blocks regardless of tool_result success — sessions
    where the Skill tool errored (e.g., harnesses without Skill-tool support)
    still count as leader-sessions. Filter such corpora via --exclude-projects.
    """
    leader: str = args.leader
    follower: str = args.follower
    exclude_glob: str | None = getattr(args, "exclude_projects", None)
    branch_filter = _branch_filter(args)
    roots = _resolve_scan_roots(args)
    session_iter, scope_label = _resolve_project_scope(args, "skill-pair", include_subagents=True, roots=roots)
    _print_resolved_scope("skill-pair", scope_label, roots)

    # bin_str -> {leader_sessions, follower_main, follower_sidechain_only}
    data: dict[str, dict[str, int]] = defaultdict(
        lambda: {"leader_sessions": 0, "follower_main": 0, "follower_sidechain_only": 0}
    )
    corpus_spawns = 0
    corpus_sidechain_turns = 0

    for jsonl, records in session_iter:
        # --exclude-projects: skip project dirs whose basename matches the glob
        if exclude_glob and fnmatch.fnmatchcase(jsonl.parent.name, exclude_glob):
            continue

        corpus_spawns += _count_subagent_spawns(records)

        has_leader_hit = False
        leader_first_ts: float | None = None
        has_main_follower = False
        has_sidechain_follower = False

        for rec in records:
            if rec.get("type") != "assistant":
                continue
            if bool(rec.get("isSidechain")):
                corpus_sidechain_turns += 1
            branch = rec.get("gitBranch") or ""
            if branch_filter and branch not in branch_filter:
                continue
            is_sidechain = bool(rec.get("isSidechain"))
            for block in ((rec.get("message") or {}).get("content") or []):
                if not isinstance(block, dict) or block.get("type") != "tool_use" or block.get("name") != "Skill":
                    continue
                skill = (block.get("input") or {}).get("skill") or ""
                if skill == leader and not is_sidechain:
                    if not has_leader_hit:
                        # Timestamp of first leader hit; skip session if unparseable
                        leader_first_ts = _parse_ts(rec.get("timestamp"))
                    has_leader_hit = True
                elif skill == follower:
                    if is_sidechain:
                        has_sidechain_follower = True
                    else:
                        has_main_follower = True

        if not has_leader_hit:
            continue
        # Skip session entirely if the first leader hit has no parseable timestamp
        if leader_first_ts is None:
            continue

        iso = datetime.fromtimestamp(leader_first_ts, tz=UTC).isocalendar()
        bin_str = f"{iso.year}-W{iso.week:02d}"

        d = data[bin_str]
        d["leader_sessions"] += 1
        if has_main_follower:
            d["follower_main"] += 1
        elif has_sidechain_follower:
            # sidechain-only: sidechain follower present AND no main-thread follower
            d["follower_sidechain_only"] += 1

    _warn_if_subagent_format_drift(corpus_spawns, corpus_sidechain_turns)

    if not data:
        print("No data found.")
        return

    print(f"{'Bin':<10} {'Lead':>5} {'Main':>5} {'Side':>5} {'Pair%':>7}")
    print(f"{'-------':<10} {'----':>5} {'----':>5} {'----':>5} {'-----':>7}")
    for bin_str in sorted(data):
        d = data[bin_str]
        lead = d["leader_sessions"]
        main = d["follower_main"]
        side = d["follower_sidechain_only"]
        pair_pct = 100.0 * main / lead if lead else 0.0
        print(f"{bin_str:<10} {lead:>5} {main:>5} {side:>5} {pair_pct:>6.1f}%")


def cmd_pr_link(args: argparse.Namespace) -> None:
    if not getattr(args, "repo", None):
        print("--repo is required for pr-link", file=sys.stderr)
        sys.exit(1)
    if not getattr(args, "branches", None):
        print("--branches is required for pr-link", file=sys.stderr)
        sys.exit(1)

    branches: list[str] = [b.strip() for b in args.branches.split(",") if b.strip()]
    repo: str = args.repo
    author: str = getattr(args, "author", None) or ""
    roots = _resolve_scan_roots(args)
    session_iter, scope_label = _resolve_project_scope(args, "pr-link", roots=roots)

    branch_models: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for _jsonl, records in session_iter:
        for rec in records:
            branch = rec.get("gitBranch") or ""
            if branch not in branches or rec.get("type") != "assistant" or bool(rec.get("isSidechain")):
                continue
            fam = _fam((rec.get("message") or {}).get("model", ""))
            branch_models[branch][fam] += 1

    _print_resolved_scope("pr-link", scope_label, roots)
    print(f"{'Branch':<35} {'PR':>5} {'Opus':>6} {'Sonnet':>7} {'IssueCmt':>9} {'ReviewCmt':>10}")
    print("-" * 80)

    for branch in branches:
        model_split = branch_models.get(branch, {})
        opus_n = model_split.get("opus", 0)
        sonnet_n = model_split.get("sonnet", 0)

        try:
            pr_result = subprocess.run(
                ["gh", "pr", "list", "--head", branch, "--repo", repo, "--state", "all", "--json", "number", "--limit", "1"],
                capture_output=True, text=True, check=True,
            )
            prs = json.loads(pr_result.stdout or "[]")
        except (subprocess.CalledProcessError, json.JSONDecodeError, FileNotFoundError):
            print(f"{branch:<35} {'?':>5} {opus_n:>6} {sonnet_n:>7} {'gh-err':>9} {'':>10}")
            continue

        if not prs:
            print(f"{branch:<35} {'none':>5} {opus_n:>6} {sonnet_n:>7} {'—':>9} {'—':>10}")
            continue

        pr_number = prs[0]["number"]
        issue_comments = review_comments = 0

        try:
            ic = subprocess.run(
                ["gh", "api", f"repos/{repo}/issues/{pr_number}/comments", "--paginate", "--jq", ".[].user.login"],
                capture_output=True, text=True, check=True,
            )
            issue_logins = [ln.strip() for ln in ic.stdout.splitlines() if ln.strip()]
            issue_comments = sum(1 for ln in issue_logins if not author or ln == author)

            rc = subprocess.run(
                ["gh", "api", f"repos/{repo}/pulls/{pr_number}/comments", "--paginate", "--jq", ".[].user.login"],
                capture_output=True, text=True, check=True,
            )
            review_logins = [ln.strip() for ln in rc.stdout.splitlines() if ln.strip()]
            review_comments = sum(1 for ln in review_logins if not author or ln == author)
        except subprocess.CalledProcessError:
            issue_comments = review_comments = -1

        print(f"{branch:<35} {pr_number:>5} {opus_n:>6} {sonnet_n:>7} {issue_comments:>9} {review_comments:>10}")


# Matches `git commit` as a standalone command or after a shell separator,
# but NOT `git commit-tree` or other `git commit`-prefixed subcommands.
# Mirrors the regex in require-code-review.sh line 38.
_GIT_COMMIT_RE = re.compile(r"(^|&&?|;|\|\|?)\s*git\s+commit(\s|$)")
_NO_VERIFY_RE = re.compile(r"\s--no-verify\b")


def cmd_commit_gate(args: argparse.Namespace) -> None:
    skill_name: str = args.skill
    by_mode: bool = bool(getattr(args, "by_permission_mode", False))
    branch_filter = _branch_filter(args)
    exclude_glob: str | None = getattr(args, "exclude_projects", None) or None
    roots = _resolve_scan_roots(args)
    session_iter, scope_label = _resolve_project_scope(args, "commit-gate", roots=roots)
    _print_resolved_scope("commit-gate", scope_label, roots)

    # bin_mode_key -> aggregated counts
    data: dict[tuple[str, str], dict] = defaultdict(lambda: {
        "sessions": 0,
        "turns": 0,
        "skill_invocations": 0,
        "commits": 0,
        "commits_with_prior_skill": 0,
        "commits_without_prior_skill": 0,
        "commits_no_verify": 0,
    })

    for jsonl, records in session_iter:
        # Apply --exclude-projects: skip if project dir basename matches the glob.
        proj_dir_name = jsonl.parent.name
        if exclude_glob and Path(proj_dir_name).match(exclude_glob):
            continue

        # --- per-session derivation ---

        # 1. permissionMode: first record (any type) carrying a non-empty value.
        # Empirically the field lives on `user` records (session-meta initial-user
        # records), not on assistant records — filtering by type misses it.
        permission_mode = "default"
        for rec in records:
            pm = rec.get("permissionMode") or ""
            if pm:
                permission_mode = pm
                break

        # 2. first_turn_ts for ISO-week binning (any record with a timestamp).
        first_turn_ts: float | None = None
        for rec in records:
            ts = _parse_ts(rec.get("timestamp"))
            if ts is not None:
                first_turn_ts = ts
                break
        if first_turn_ts is None:
            continue
        iso_year, iso_week, _ = datetime.fromtimestamp(first_turn_ts, tz=UTC).isocalendar()
        bin_label = f"{iso_year}-W{iso_week:02d}"

        # 3. Branch filter — session contributes if ANY main-thread record is on an allowed branch.
        if branch_filter:
            session_branches = {
                rec.get("gitBranch") or ""
                for rec in records
                if rec.get("type") == "assistant" and not bool(rec.get("isSidechain"))
            }
            if not (session_branches & branch_filter):
                continue

        # 4. Walk records: count turns, skill invocations, and commits with ordering.
        #    Only main-thread (isSidechain != true) assistant records.
        #
        #    Commit gating is tracked by a "skill_since_last_commit" flag that
        #    resets each time a commit is detected.  Within a single assistant
        #    record, content-array index determines ordering between Skill and
        #    Bash blocks.
        session_turns = 0
        session_skill_invocations = 0
        session_commits = 0
        session_commits_with_prior_skill = 0
        session_commits_without_prior_skill = 0
        session_commits_no_verify = 0

        # Tracks whether a qualifying Skill invocation has occurred since the
        # last commit (or session start).
        skill_seen_since_last_commit = False

        for rec in records:
            if rec.get("type") != "assistant" or bool(rec.get("isSidechain")):
                continue
            session_turns += 1

            content = (rec.get("message") or {}).get("content") or []

            # Process each tool_use block in content-array order so that within
            # a single record the Skill/Bash ordering determines gating.
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                block_name = block.get("name")
                inp = block.get("input") or {}

                if block_name == "Skill":
                    if inp.get("skill") == skill_name:
                        session_skill_invocations += 1
                        skill_seen_since_last_commit = True

                elif block_name == "Bash":
                    cmd = inp.get("command", "")
                    if _GIT_COMMIT_RE.search(cmd):
                        session_commits += 1
                        is_no_verify = bool(_NO_VERIFY_RE.search(cmd))
                        if is_no_verify:
                            session_commits_no_verify += 1
                            # --no-verify bypasses the gate entirely; count in
                            # commits and commits-no-verify but NOT in
                            # commits-with-prior-skill.
                            session_commits_without_prior_skill += 1
                        elif skill_seen_since_last_commit:
                            session_commits_with_prior_skill += 1
                        else:
                            session_commits_without_prior_skill += 1
                        # Reset: the skill must fire again to gate the next commit.
                        skill_seen_since_last_commit = False

        bucket_key = (bin_label, permission_mode if by_mode else "all")
        d = data[bucket_key]
        d["sessions"] += 1
        d["turns"] += session_turns
        d["skill_invocations"] += session_skill_invocations
        d["commits"] += session_commits
        d["commits_with_prior_skill"] += session_commits_with_prior_skill
        d["commits_without_prior_skill"] += session_commits_without_prior_skill
        d["commits_no_verify"] += session_commits_no_verify

    if not data:
        print("No data found.")
        return

    if by_mode:
        header = (
            f"{'bin':<12} {'mode':<10} {'sessions':>8} {'turns':>7} "
            f"{'skill-inv':>10} {'skill/1k':>9} {'commits':>7} "
            f"{'w-skill':>8} {'wo-skill':>9} {'no-verify':>10}"
        )
    else:
        header = (
            f"{'bin':<12} {'sessions':>8} {'turns':>7} "
            f"{'skill-inv':>10} {'skill/1k':>9} {'commits':>7} "
            f"{'w-skill':>8} {'wo-skill':>9} {'no-verify':>10}"
        )
    print(header)
    print("-" * len(header))

    for (bin_label, mode) in sorted(data):
        d = data[(bin_label, mode)]
        skill_rate = f"{1000 * d['skill_invocations'] / d['turns']:.1f}" if d["turns"] else "—"
        if by_mode:
            print(
                f"{bin_label:<12} {mode:<10} {d['sessions']:>8} {d['turns']:>7} "
                f"{d['skill_invocations']:>10} {skill_rate:>9} {d['commits']:>7} "
                f"{d['commits_with_prior_skill']:>8} {d['commits_without_prior_skill']:>9} "
                f"{d['commits_no_verify']:>10}"
            )
        else:
            print(
                f"{bin_label:<12} {d['sessions']:>8} {d['turns']:>7} "
                f"{d['skill_invocations']:>10} {skill_rate:>9} {d['commits']:>7} "
                f"{d['commits_with_prior_skill']:>8} {d['commits_without_prior_skill']:>9} "
                f"{d['commits_no_verify']:>10}"
            )


_AUDIT_CLASSES: tuple[str, ...] = (
    "orchestration", "judgment", "code-write", "code-read", "pure-thinking", "other"
)

_ORCHESTRATION_TOOLS: frozenset[str] = frozenset({"Agent", "Task"})
_CODE_WRITE_TOOLS: frozenset[str] = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})
_CODE_READ_TOOLS: frozenset[str] = frozenset({"Read", "Grep", "Glob", "Bash"})


def _classify_opus_turn(
    content: list,
    in_judgment_span: bool,
    plan_mode_active: bool,
) -> str:
    """Classify one Opus assistant turn into an audit routing class.

    Classification is first-match among:
      orchestration  — any Agent/Task tool_use
      judgment       — turn is within an active judgment span (skill or plan-mode)
      code-write     — any Edit/Write/MultiEdit/NotebookEdit tool_use
      code-read      — at least one tool_use, all from Read/Grep/Glob/Bash
      pure-thinking  — thinking blocks only, no tool_use
      other          — none of the above
    """
    tool_use_blocks = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]
    tool_names = {b.get("name") for b in tool_use_blocks}

    if tool_names & _ORCHESTRATION_TOOLS:
        return "orchestration"
    if in_judgment_span or plan_mode_active:
        return "judgment"
    if tool_names & _CODE_WRITE_TOOLS:
        return "code-write"
    if tool_use_blocks and tool_names <= _CODE_READ_TOOLS:
        return "code-read"
    has_thinking = any(isinstance(b, dict) and b.get("type") == "thinking" for b in content)
    if has_thinking and not tool_use_blocks:
        return "pure-thinking"
    return "other"


def _derive_proj_label(jsonl: Path) -> str:
    """Derive a short project label from a jsonl path, matching token-analyzer.py's convention."""
    return jsonl.parent.name.lstrip("-").replace("-", "/", 2).split("/", 2)[-1]


# iter_sessions documents this repo's own worktree naming: a linked worktree's
# project-dir slug is the main dir's slug with --claude-worktrees-<branch>
# appended, which _derive_proj_label carries through unchanged onto its output.
_WORKTREE_SUFFIX_RE = re.compile(r"--claude-worktrees-.+$")


def _project_family(raw_proj_label: str) -> str:
    """Collapse a _derive_proj_label output to its base-repo "family" key.

    One repo's main checkout and every linked worktree derive to distinct
    labels (repo, repo--claude-worktrees-branch-a, ...) that would otherwise
    fragment --by-project's per-project rows across branches of the same repo.

    Matches on the literal substring alone — a project whose own name happens
    to contain "--claude-worktrees-" would have that trailing portion
    stripped and merged into a false family. Below current scale to guard
    against; re-evaluate if --by-project output ever shows an unexpected
    merge.
    """
    return _WORKTREE_SUFFIX_RE.sub("", raw_proj_label)


_REDACT_MAP_MISS_TOKEN = "private-project-unmapped"

# A cost redact-map key is either a plain raw label (single-root reports,
# audit-routing) or a (root_index, raw_label) pair (cost's multi-root
# --config-dir reports) — see _build_redact_map.
_RedactMapKey = str | tuple[int, str]


def _redact_proj_label(proj_label: _RedactMapKey, redact_map: dict[_RedactMapKey, str]) -> str:
    """Apply the redact map to a project label, preserving 'claude-config' as-is.

    proj_label may be a (root_index, raw_label) pair for a multi-root cost
    report (see _build_redact_map); claude-config still passes through
    unredacted regardless of which root it was found under.

    A map miss returns a fixed opaque token rather than the raw label — the
    map is only ever built from a full-corpus scan (_build_redact_map), so a
    miss means the caller passed an incomplete map, and falling back to the
    raw name would silently defeat --redact.
    """
    raw_label = proj_label[1] if isinstance(proj_label, tuple) else proj_label
    if raw_label == "claude-config":
        return raw_label
    return redact_map.get(proj_label, _REDACT_MAP_MISS_TOKEN)


def _sorted_distinct_proj_labels(root: Path) -> list[str]:
    """Distinct project labels found under one root, sorted for deterministic
    ordinal assignment — the per-root scan _build_redact_map shares across its
    single- and multi-root branches.

    Scans via iter_sessions(root, "*"), ignoring any caller's own --projects
    filter, so a project always binds to the same placeholder whether it was
    found by a narrowed cost run or a full audit-routing run — a narrower scan
    would let the same label mean two different projects across two published
    outputs. iter_sessions (not a raw glob) is used because it already
    excludes zero-record transcripts; a raw glob would not, and that
    difference would shift every subsequent private-project-N index.
    """
    labels: list[str] = []
    for jsonl, _records in iter_sessions(root, "*"):
        label = _derive_proj_label(jsonl)
        if label not in labels:
            labels.append(label)
    labels.sort()
    return labels


def _redaction_ordinals(roots: Sequence[Path]) -> dict[Path, int]:
    """Assign each root a stable 1-based ordinal ("account-N"), sorted by
    resolved path once here rather than by each caller's own list order.

    _resolve_scan_roots' scan order puts the active profile first, so a
    position-based ordinal (each caller enumerating `roots` itself) would
    renumber every other declared root depending on which profile produced
    the report. Sorting once, here, and having every ordinal-assigning site
    -- _build_redact_map, cost's per-row redact key, and its --by-project
    account column -- look up the same dict keeps the same physical root at
    the same account-N regardless of scan order, and keeps all three sites
    from independently deriving (and risking desyncing) the same number.
    """
    resolved = sorted({root.resolve() for root in roots})
    return {resolved_root: ordinal for ordinal, resolved_root in enumerate(resolved, start=1)}


def _build_redact_map(roots: Sequence[Path] | None = None) -> dict[_RedactMapKey, str]:
    """Build the project-label -> opaque-token map shared by every --redact caller.

    --since never reaches this map and must not: it would change which
    sessions are found on a per-run basis, shifting every subsequent
    private-project-N index between two runs of the same corpus.

    This means --redact reads every project's transcript bytes off disk even
    under --this-repo, a considered tradeoff in tension with that flag's
    minimization intent elsewhere in this file, not an oversight.

    Ordinals are assigned sequentially over the sorted full-corpus label list,
    not the caller's --this-repo-scoped subset, so a printed private-project-N
    number is shaped by every other private project directory that exists
    locally and sorts before the in-scope one — a structural fingerprint of
    the operator's other projects that a --this-repo-scoped report does not
    otherwise disclose. Narrowing the scan to the caller's own scope would
    close this but breaks the cross-run label-stability guarantee above, so
    this function does not attempt it.

    roots defaults to (PROJECTS_DIR,) — a single root (the default, or any
    caller passing exactly one, e.g. cmd_audit_routing) gets the original flat
    private-project-N map, unnamespaced by account. More than one root
    namespaces every label account-<K>/private-project-N, where <K> is the
    root's ordinal from _redaction_ordinals (resolved-path-sorted, stable
    across which profile is active) — never the config-dir path or its
    basename, which would leak the account/client identifier the directory
    name encodes. <N> restarts at 1 within each account's own scan. Labels
    (and the corpus fingerprint derived from this map) are not comparable
    across two report runs built from different declared-roots files: a
    changed root set can renumber every ordinal. Two runs from the *same*
    declared-roots file, differing only in which profile was active, assign
    the same ordinal to the same physical root and so remain comparable.
    """
    if roots is None:
        roots = (PROJECTS_DIR,)

    redact_map: dict[_RedactMapKey, str] = {}

    if len(roots) <= 1:
        root = roots[0] if roots else PROJECTS_DIR
        num_index = 1
        for label in _sorted_distinct_proj_labels(root):
            if label == "claude-config":
                redact_map[label] = label
            else:
                redact_map[label] = f"private-project-{num_index}"
                num_index += 1
        return redact_map

    ordinals = _redaction_ordinals(roots)
    for root in roots:
        ordinal = ordinals[root.resolve()]
        account_label = f"account-{ordinal}"
        num_index = 1
        for label in _sorted_distinct_proj_labels(root):
            key = (ordinal, label)
            if label == "claude-config":
                redact_map[key] = label
            else:
                redact_map[key] = f"{account_label}/private-project-{num_index}"
                num_index += 1
    return redact_map


def _corpus_fingerprint(redact_map: dict[_RedactMapKey, str]) -> str:
    """Short sha256 prefix of the sorted raw project-label set a redact map was
    built from — a same-corpus indicator only, not a security boundary (see
    _build_redact_map). Two report runs share a fingerprint only when their
    underlying project-label sets are identical; a differing fingerprint means
    ordinals are not comparable between them.
    """
    raw_labels = {key[1] if isinstance(key, tuple) else key for key in redact_map}
    return hashlib.sha256("\n".join(sorted(raw_labels)).encode()).hexdigest()[:12]


_REDACT_SESSION_MISS_TOKEN = "session-unmapped"


def _assign_session_redact_label(session_id: str, session_redact_map: dict[str, str]) -> None:
    """Assign session_id a stable opaque label the first time it's seen this run.

    Unlike project labels, session-id placeholders need no cross-run or
    cross-command stability — each command's own single pass over its corpus
    is the map's only writer, so assignment happens inline as sessions are
    discovered rather than needing a separate first pass.
    """
    if session_id not in session_redact_map:
        session_redact_map[session_id] = f"session-{len(session_redact_map) + 1}"


def _redact_session_id(session_id: str, session_redact_map: dict[str, str]) -> str:
    """Apply a run-scoped session-id redact map; fails closed to a fixed token on a miss."""
    return session_redact_map.get(session_id, _REDACT_SESSION_MISS_TOKEN)


def _assign_root_scoped_redact_label(
    kind: str, ordinal: int, value: str, redact_map: dict[tuple[int, str], str]
) -> str:
    """Assign one (root, value) pair a stable, account-namespaced opaque
    label the first time it's seen this run, and return it.

    `ordinal` must be looked up from _redaction_ordinals(roots), not a raw
    scan-order position (_root_index_for_path) — scan order puts the active
    profile first, so a position-based number would renumber the same
    physical account depending on which profile produced the report, the
    exact desync class _redaction_ordinals exists to prevent everywhere else
    in this file (_build_redact_map, cost's per-row key, its --by-project
    column). Generic across every value kind that needs this exact shape of
    redaction: neither _redact_proj_label nor _assign_session_redact_label
    covers gitBranch or subagent_type, and subagents'/subagent-mix's
    --config-dir multi-root reports need their own primitive so two
    accounts' identically-named value (e.g. both branch "main", or both
    subagent_type "staff-sdet") never collapse into one label or leak a raw
    value. Namespaced by account (account-<K>/<kind>-<N>, N restarting at 1
    per account, tracked in `redact_map` which is scoped to one `kind` per
    caller so branch and subagent_type numbering never share a counter)
    rather than a single flat counter, mirroring _build_redact_map's
    account-<K>/private-project-N convention. Like _assign_session_redact_label,
    this label is stable only within one run, not across runs. subagent-mix's
    exact-cent Actual $/Counterfactual $ columns are a stronger cross-run
    correlation key against this label than the integer spawn counts already
    printed alongside it, though only within an already-DO_NOT_PUBLISH report.
    """
    key = (ordinal, value)
    if key not in redact_map:
        n = sum(1 for k in redact_map if k[0] == ordinal) + 1
        redact_map[key] = f"account-{ordinal}/{kind}-{n}"
    return redact_map[key]


def cmd_audit_routing(args: argparse.Namespace) -> None:
    """Per-turn Opus token breakdown by routing class across all sessions.

    Classifies every Opus assistant turn into: orchestration, judgment,
    code-write, code-read, pure-thinking, or other — then aggregates
    output_tokens and cache_read_input_tokens per class. Emits per-session
    rows sorted descending by total output tokens, plus a corpus aggregate.
    """
    top_n: int = getattr(args, "top", 20) or 20
    redact: bool = bool(getattr(args, "redact", False))

    since_ts, since_raw = _parse_since_nd_arg(args, "audit-routing")
    since_label = since_raw or ""

    roots = _resolve_scan_roots(args)
    multi_root = len(roots) > 1

    # _resolve_project_scope's fail-closed --this-repo check runs before
    # _build_redact_map's full-corpus disk scan, so an out-of-repo failure
    # exits without paying for that scan.
    session_iter, scope_label = _resolve_project_scope(args, "audit-routing", roots=roots)
    _print_resolved_scope("audit-routing", scope_label, roots)

    redact_map: dict[_RedactMapKey, str] = _build_redact_map(roots) if redact else {}
    session_redact_map: dict[str, str] = {}
    # Only computed under multi-root redaction: _root_index_for_path needs
    # already-resolved roots, and _redaction_ordinals is the same
    # resolved-path-sorted mapping _build_redact_map's keys and cost's own
    # per-row lookup share, so a row's ordinal always agrees with the map's.
    resolved_roots = [r.resolve() for r in roots] if (redact and multi_root) else []
    redact_ordinals = _redaction_ordinals(roots) if (redact and multi_root) else {}

    # Per-session accumulators: session_key → {class → {out, cr, dollars}}
    session_rows: list[dict] = []
    # Corpus totals: class → {out, cr, dollars}
    corpus_totals: dict[str, dict[str, float]] = {cls: {"out": 0, "cr": 0, "dollars": 0.0} for cls in _AUDIT_CLASSES}
    # Opus turns whose model ID has no _MODEL_BASE_INPUT_RATES entry — excluded from
    # the dollar headline, counted here so a corpus with unpriced turns doesn't
    # silently under-report (mirrors _cost_report's unpriced-tokens convention).
    unpriced_turns = 0
    unpriced_tokens = 0

    for jsonl, records in session_iter:
        # One API call = one turn: dedup merges a requestId run's content
        # blocks into one union list, so the classification and judgment-span
        # tracking below see every block (e.g. a Skill/ExitPlanMode tool_use
        # on a later block), while the run's dollars are attributed once.
        records = _dedup_turns_by_request_id(records)
        proj_label = _derive_proj_label(jsonl)
        session_id = jsonl.stem[:12]
        if redact:
            _assign_session_redact_label(session_id, session_redact_map)
        if redact and multi_root:
            root_position = _root_index_for_path(jsonl, resolved_roots)
            redact_key: _RedactMapKey = (redact_ordinals[resolved_roots[root_position]], proj_label)
        else:
            redact_key = proj_label

        # Per-session class token accumulators
        session_class_tokens: dict[str, dict[str, float]] = {
            cls: {"out": 0, "cr": 0, "dollars": 0.0} for cls in _AUDIT_CLASSES
        }

        # Judgment span state machine (reset per session)
        in_judgment_span: bool = False
        plan_mode_active: bool = False

        for rec in records:
            rtype = rec.get("type", "")
            msg = rec.get("message") or {}

            # --- State machine updates for user/human records ---
            if rtype in ("user", "human"):
                # Judgment span closes at next user turn
                in_judgment_span = False
                # Detect plan-mode activation
                content_text = _content_text(msg.get("content", ""))
                if "Plan mode is active" in content_text:
                    plan_mode_active = True
                continue

            if rtype != "assistant":
                continue

            # Filter to Opus turns with usage data
            model = msg.get("model", "")
            if _fam(model) != "opus":
                # Still update span state from non-Opus assistant turns (ExitPlanMode)
                content = msg.get("content") or []
                for block in content:
                    if (
                        isinstance(block, dict)
                        and block.get("type") == "tool_use"
                        and block.get("name") == "ExitPlanMode"
                    ):
                        plan_mode_active = False
                continue

            usage = msg.get("usage")
            if not usage:
                continue

            # Apply --since filter
            if since_ts is not None:
                rec_ts = _parse_ts(rec.get("timestamp"))
                if rec_ts is None or rec_ts < since_ts:
                    continue

            content = msg.get("content") or []
            out_tokens: int = usage.get("output_tokens", 0)
            cr_tokens: int = usage.get("cache_read_input_tokens", 0)
            dollars_by_class, _context_at_turn, turn_unpriced_tokens = _price_turn(model, usage)
            if dollars_by_class is None:
                unpriced_turns += 1
                unpriced_tokens += turn_unpriced_tokens
                turn_dollars = 0.0
            else:
                turn_dollars = sum(dollars_by_class.values())

            # Open a judgment span if this turn invokes a judgment skill — evaluated
            # before classification so the invoking turn itself counts as judgment.
            for block in content:
                if (
                    isinstance(block, dict)
                    and block.get("type") == "tool_use"
                    and block.get("name") == "Skill"
                    and (block.get("input") or {}).get("skill") in AUDIT_JUDGMENT_SKILLS
                ):
                    in_judgment_span = True
                    break

            turn_class = _classify_opus_turn(content, in_judgment_span, plan_mode_active)

            # ExitPlanMode clears plan-mode on the *next* turn (the current turn is still in-span).
            for block in content:
                if (
                    isinstance(block, dict)
                    and block.get("type") == "tool_use"
                    and block.get("name") == "ExitPlanMode"
                ):
                    plan_mode_active = False
                    break

            session_class_tokens[turn_class]["out"] += out_tokens
            session_class_tokens[turn_class]["cr"] += cr_tokens
            session_class_tokens[turn_class]["dollars"] += turn_dollars

        session_total_out = sum(v["out"] for v in session_class_tokens.values())
        if not session_total_out:
            continue

        session_rows.append({
            "session_id": session_id,
            "proj_label": proj_label,
            "redact_key": redact_key,
            "classes": session_class_tokens,
            "total_out": session_total_out,
        })

        for cls in _AUDIT_CLASSES:
            corpus_totals[cls]["out"] += session_class_tokens[cls]["out"]
            corpus_totals[cls]["cr"] += session_class_tokens[cls]["cr"]
            corpus_totals[cls]["dollars"] += session_class_tokens[cls]["dollars"]

    # --- Emit per-session table ---
    title_since = f"last {since_label}" if since_label else "all time"
    print(f"\n## Opus turn-class breakdown ({title_since})\n")

    header = (
        f"{'Session':<16} {'Proj':<20} "
        f"{'orch':>8} {'judgment':>9} {'code-write':>11} {'code-read':>10} "
        f"{'thinking':>9} {'other':>7} {'total_out':>11} {'cache_rd':>10}"
    )
    print(header)
    print("─" * len(header))

    sorted_rows = sorted(session_rows, key=lambda r: r["total_out"], reverse=True)
    for row in sorted_rows[:top_n]:
        sid = _redact_session_id(row["session_id"], session_redact_map) if redact else row["session_id"]
        proj = _redact_proj_label(row["redact_key"], redact_map) if redact else row["proj_label"]
        cls = row["classes"]
        total_cr = sum(v["cr"] for v in cls.values())
        print(
            f"{sid:<16} {proj:<20} "
            f"{cls['orchestration']['out']:>8,} {cls['judgment']['out']:>9,} "
            f"{cls['code-write']['out']:>11,} {cls['code-read']['out']:>10,} "
            f"{cls['pure-thinking']['out']:>9,} {cls['other']['out']:>7,} "
            f"{row['total_out']:>11,} {total_cr:>10,}"
        )

    # --- Emit corpus aggregate ---
    print("\n## Corpus aggregate\n")
    print(f"{'Class':<16} {'Output tokens':>15} {'Cache read tokens':>18}")
    total_out_all = 0
    total_cr_all = 0
    for cls in _AUDIT_CLASSES:
        out_val = corpus_totals[cls]["out"]
        cr_val = corpus_totals[cls]["cr"]
        print(f"{cls:<16} {out_val:>15,} {cr_val:>18,}")
        total_out_all += out_val
        total_cr_all += cr_val
    print("─" * 51)
    print(f"{'total':<16} {total_out_all:>15,} {total_cr_all:>18,}")

    sonnet_tier_dollars = corpus_totals["code-write"]["dollars"] + corpus_totals["code-read"]["dollars"]
    priced_total_dollars = sum(corpus_totals[cls]["dollars"] for cls in _AUDIT_CLASSES)
    dollar_pct = f"{100 * sonnet_tier_dollars / priced_total_dollars:.0f}%" if priced_total_dollars else "—"
    print(f"\nSonnet-tier estimate: ${sonnet_tier_dollars:,.2f}")
    print(f"  = {dollar_pct} of priced Opus spend in this window")
    if unpriced_turns:
        print(f"  ({unpriced_turns:,} unpriced turns / {unpriced_tokens:,} tokens excluded from priced spend)")

    sonnet_tier_out = corpus_totals["code-write"]["out"] + corpus_totals["code-read"]["out"]
    sonnet_pct = f"{100 * sonnet_tier_out / total_out_all:.0f}%" if total_out_all else "—"
    print(f"\nSonnet-tier estimate: {sonnet_tier_out:,} output tokens (secondary diagnostic)")
    print(f"  = {sonnet_pct} of Opus output in this window")


_TOKEN_CLASSES: tuple[str, ...] = ("cache_read", "cache_write_5m", "cache_write_1h", "output", "input")

_PRICING_SOURCE_URL = "https://platform.claude.com/docs/en/about-claude/pricing"
_PRICING_FETCH_DATE = date(2026, 8, 2)

# Multipliers vs. a model's base input rate, per the pricing page's stated ratios.
_OUTPUT_RATE_MULTIPLIER = 5
_CACHE_WRITE_5M_MULTIPLIER = 1.25
_CACHE_WRITE_1H_MULTIPLIER = 2
_CACHE_READ_MULTIPLIER = 0.1

# Multipliers applied to every dollar class when usage.speed/usage.inference_geo
# report that outcome, per platform.claude.com/docs/en/build-with-claude/fast-mode
# and .../about-claude/pricing's data-residency section.
_FAST_MODE_RATE_MULTIPLIER = 2
_INFERENCE_GEO_US_RATE_MULTIPLIER = 1.1

_DEFAULT_REVERIFY_BY = _PRICING_FETCH_DATE + timedelta(days=90)

# Base input $/MTok per model ID, keyed on the exact string Claude Code writes
# to message.model. Source: _PRICING_SOURCE_URL, fetched _PRICING_FETCH_DATE.
# Output/cache-write/cache-read rates are derived from this one base rate per
# model by _model_rates, so each model needs only its base rate kept current.
_MODEL_BASE_INPUT_RATES: dict[str, float] = {
    "claude-opus-5": 5.00,
    "claude-opus-4-8": 5.00,
    "claude-sonnet-5": 2.00,
    "claude-sonnet-4-6": 3.00,
    "claude-haiku-4-5-20251001": 1.00,
    # Exact-match only, unlike _context_window_for_model's prefix match on
    # this same string -- a dated-snapshot variant like
    # "claude-sonnet-4-5-20260115" still 200k-buckets correctly but prices as
    # unpriced here.
    "claude-sonnet-4-5": 3.00,
}

# Re-verify-by date per model ID: fetch-date+90d for every model, since none
# has a vendor-stated rate-change date today.
_MODEL_RATE_EXPIRES: dict[str, date] = dict.fromkeys(_MODEL_BASE_INPUT_RATES, _DEFAULT_REVERIFY_BY)

_CONTEXT_BUCKET_THRESHOLD = 200_000  # inclusive edge of the "≥200k" finding
_CONTEXT_BUCKET_UNDER = "<200k"
_CONTEXT_BUCKET_OVER = ">=200k"

# Context window in tokens per model ID, mirroring
# nudge-handoff-near-context-cap.sh's own CONTEXT_WINDOW case statement
# exactly (same prefix list, same trailing-dash dated-snapshot match), so
# _CONTEXT_DISTRIBUTION_THRESHOLD_PCTS resolves to the window the hook
# multiplies by for the same model ID. A fixed percentage of that window
# still resolves to a different absolute token count on a 200k model than
# on a 1M one — see _CONTEXT_DISTRIBUTION_THRESHOLD_ABS for the sibling
# table expressed directly in absolute tokens instead. Source:
# https://platform.claude.com/docs/en/about-claude/models/overview, fetched
# 2026-08-03; re-verify by 2026-11-03. Verified 200k: Haiku 4.5, Sonnet 4.5,
# Opus 4.5, Opus 4.1. Verified 1M: Fable 5, Mythos 5, Opus 5, Opus
# 4.8/4.7/4.6, Sonnet 5, Sonnet 4.6. An unlisted ID takes the 1M default.
_200K_CONTEXT_MODEL_PREFIXES: tuple[str, ...] = (
    "claude-haiku-4-5",
    "claude-sonnet-4-5",
    "claude-opus-4-5",
    "claude-opus-4-1",
)
_200K_CONTEXT_WINDOW = 200_000
_DEFAULT_CONTEXT_WINDOW = 1_000_000

# Candidate threshold percentages of a model's context window, for
# context-distribution's crossing-count/session-share/dollar-share table.
_CONTEXT_DISTRIBUTION_THRESHOLD_PCTS: tuple[int, ...] = (30, 40, 50, 60)

# Candidate absolute-token thresholds, in the hook's own ESTIMATE unit (input
# + cache_read + cache_creation + output — see _session_peak_context), for
# context-distribution's own crossing-count/session-share/dollar-share table.
# A candidate-threshold sweep like _CONTEXT_DISTRIBUTION_THRESHOLD_PCTS, but
# fixed rather than scaled per model's window. Not the same thing as
# _CONTEXT_BUCKET_THRESHOLD above, which is a fixed *reporting* bucket edge
# consumed by cost/cost-trend, not a candidate-threshold sweep. Spans the
# 200k-model effective floor (80_000, 40% of 200k) through the 1M-model's
# uncapped 40%-of-window value (400_000) and beyond, into the range where
# 1M-model sessions have actually been observed firing. 360_000 is included
# because it is the live 1M-model effective threshold today
# (nudge-handoff-near-context-cap.sh's HANDOFF_NUDGE_ABS_CAP default) — a
# re-run of this report must be able to show the value the hook is actually
# configured to fire at, not just candidates for a future change.
_CONTEXT_DISTRIBUTION_THRESHOLD_ABS: tuple[int, ...] = (
    80_000, 135_000, 180_000, 250_000, 360_000, 400_000, 600_000, 800_000,
)


def _context_window_for_model(model: str) -> int:
    """Context window in tokens for one model ID.

    A prefix requires an exact match or a trailing "-" (dated-snapshot
    suffix), not a bare trailing wildcard, so a longer numeral
    (claude-opus-4-10) can't collide with a shorter one (claude-opus-4-1) by
    string prefix alone — the same collision guard as the bash hook this
    mirrors.
    """
    for prefix in _200K_CONTEXT_MODEL_PREFIXES:
        if model == prefix or model.startswith(prefix + "-"):
            return _200K_CONTEXT_WINDOW
    return _DEFAULT_CONTEXT_WINDOW


def _model_rates(model: str) -> dict[str, float] | None:
    """Return per-MTok dollar rates for one model ID, or None if unpriced."""
    base = _MODEL_BASE_INPUT_RATES.get(model)
    if base is None:
        return None
    return {
        "input": base,
        "output": base * _OUTPUT_RATE_MULTIPLIER,
        "cache_write_5m": base * _CACHE_WRITE_5M_MULTIPLIER,
        "cache_write_1h": base * _CACHE_WRITE_1H_MULTIPLIER,
        "cache_read": base * _CACHE_READ_MULTIPLIER,
    }


def _cache_write_split(usage: dict) -> tuple[int, int]:
    """Return (ephemeral_1h_tokens, ephemeral_5m_tokens) for one usage record.

    Prices the nested cache_creation.{ephemeral_1h,ephemeral_5m}_input_tokens
    block when present. Falls back to the flat cache_creation_input_tokens
    field as 5m-only when the nested block is absent — never counts both,
    since the nested block's own two fields sum exactly to the flat field on
    every real record that carries one.
    """
    nested = usage.get("cache_creation")
    if nested is not None:
        return int(nested.get("ephemeral_1h_input_tokens", 0)), int(nested.get("ephemeral_5m_input_tokens", 0))
    return 0, int(usage.get("cache_creation_input_tokens", 0))


def _context_at_turn(usage: dict) -> int:
    """Total input-side tokens resident in the context at one assistant turn.

    input_tokens + cache_read_input_tokens + ephemeral_1h + ephemeral_5m. This is
    an absolute per-turn snapshot, not a turn-to-turn delta -- read-scope
    differences consecutive snapshots within one context sequence to derive
    prompt-token growth, which is why the sum lives here rather than inline in
    _price_turn's pricing path.
    """
    eph_1h, eph_5m = _cache_write_split(usage)
    return int(usage.get("input_tokens", 0)) + int(usage.get("cache_read_input_tokens", 0)) + eph_1h + eph_5m


def _context_bucket(context_at_turn: int) -> str:
    return _CONTEXT_BUCKET_OVER if context_at_turn >= _CONTEXT_BUCKET_THRESHOLD else _CONTEXT_BUCKET_UNDER


def _dedup_turns_by_request_id(records: Sequence[dict]) -> list[dict]:
    """Collapse consecutive same-requestId assistant records into one turn each.

    Claude Code writes one JSONL record per assistant content block (thinking /
    text / tool_use); every record from one API call shares one requestId.
    Measured across 150 transcripts / 15,653 multi-record runs: input_tokens,
    cache_creation_input_tokens, and cache_read_input_tokens are identical
    across every record in a run, but output_tokens ascends within the run
    and completes only on the run's last record (see _warn_if_run_usage_drift
    for the runtime check on the input/cache invariant), so pricing or
    counting per raw record inflates dollars and turn counts by however many
    blocks the response split into, and taking usage from the run's first
    record undercounts output tokens. This merges each run of consecutive
    assistant records sharing one non-empty requestId into a single record:
    message.content becomes the concatenation of the run's own content blocks
    in original order (so a caller that classifies on content sees every
    block, not just one); every other field is taken from the run's first
    record except message.usage, which is taken from the run's last record.
    A missing/null/empty requestId never merges with another missing one:
    each such record stays its own one-record turn, since real transcripts
    carry requestId-less records (synthetic all-zero-usage API-error
    records) that must not
    collapse into each other. Non-assistant records pass through unchanged
    and end any run in progress. Callers must never concatenate records from
    different sessions before calling this: requestId is unique per API call
    and a run's own records are always contiguous, so concatenating one
    session's main transcript with its own subagent transcripts is safe, but
    mixing in another session's records is not. A run continues on
    requestId equality alone (not also isSidechain/type), relying on
    requestId uniqueness. Merging shifts --since semantics: a merged turn's
    timestamp is its first block's, so "since" now means the turn started
    after the cutoff, not that some block of it landed after the cutoff.
    """
    turns: list[dict] = []
    run: list[dict] = []
    run_key: str | None = None

    for rec in records:
        is_assistant = rec.get("type") == "assistant"
        request_id = rec.get("requestId") if is_assistant else None
        continues_run = is_assistant and request_id and request_id == run_key

        if not continues_run and run:
            turns.append(run[0] if len(run) == 1 else _merge_assistant_run(run))
            run = []

        if is_assistant:
            run.append(rec)
            run_key = request_id
        else:
            turns.append(rec)
            run_key = None

    if run:
        turns.append(run[0] if len(run) == 1 else _merge_assistant_run(run))

    return turns


def _merge_assistant_run(run: list[dict]) -> dict:
    """Merge one requestId run of assistant records into a single synthetic record.

    message.content is the concatenation of every record's own content blocks,
    in original order. Every other field (uuid, parentUuid, timestamp) is
    taken from the run's first record -- the documented --since semantics
    depend on the first block's timestamp. message.usage is taken from the
    run's LAST record instead: input_tokens and the cache_* classes are
    identical across a run, but output_tokens ascends within the run and
    only reaches its billed value on the last record (measured across 150
    transcripts / 15,653 multi-record runs -- see _warn_if_run_usage_drift).
    """
    _warn_if_run_usage_drift(run)
    merged = dict(run[0])
    merged_message = dict(merged.get("message") or {})
    merged_content: list = []
    for rec in run:
        merged_content.extend((rec.get("message") or {}).get("content") or [])
    merged_message["content"] = merged_content
    merged_message["usage"] = (run[-1].get("message") or {}).get("usage")
    merged["message"] = merged_message
    return merged


# Names the usage keys _warn_if_run_usage_drift treats as required-invariant
# across a requestId run: measured identical in 15,653/15,653 multi-record
# runs (see _dedup_turns_by_request_id's docstring). output_tokens is
# deliberately excluded -- it ascends within a run by design, completing on
# the last record, so divergence there is the documented norm, not drift.
# cache_creation's nested ephemeral_1h/5m_input_tokens need no separate entry:
# both measured invariant across the same 15,653 runs, and every run's first
# and last record alike carried a cache_creation block.
_USAGE_DRIFT_INVARIANT_KEYS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")

# Rate-limits _warn_if_run_usage_drift to one line per process, mirroring
# _warn_if_subagent_format_drift's pattern -- a whole-corpus scan should
# surface one signal that the format changed, not one per occurrence.
_usage_drift_warned = False


def _warn_if_run_usage_drift(run: list[dict]) -> None:
    """Emit one stderr warning per process when a requestId run's records
    disagree on an input/cache usage class that's measured invariant across
    a run (see _USAGE_DRIFT_INVARIANT_KEYS).

    _merge_assistant_run relies on these classes being identical across every
    record of one API call to price a merged turn correctly regardless of
    which record's value it reads; this is the runtime canary for that
    assumption. A warning rather than a raise, so one malformed session
    doesn't abort a whole-corpus scan.
    """
    global _usage_drift_warned
    if _usage_drift_warned:
        return
    first_usage = (run[0].get("message") or {}).get("usage") or {}
    for rec in run[1:]:
        rec_usage = (rec.get("message") or {}).get("usage") or {}
        if any(rec_usage.get(key) != first_usage.get(key) for key in _USAGE_DRIFT_INVARIANT_KEYS):
            print(
                f"WARNING: requestId {run[0].get('requestId')!r} has non-identical "
                "input/cache usage across its own records — _merge_assistant_run's "
                "invariant-usage assumption may no longer hold (further occurrences "
                "this run of the CLI are suppressed). The Claude Code transcript "
                "format may have changed.",
                file=sys.stderr,
            )
            _usage_drift_warned = True
            return


def _price_turn(model: str, usage: dict) -> tuple[dict[str, float] | None, int, int]:
    """Price one assistant turn's usage against _MODEL_BASE_INPUT_RATES.

    Returns (dollars_by_class, context_at_turn, unpriced_tokens):
    - dollars_by_class holds one raw (unrounded) dollar amount per
      _TOKEN_CLASSES entry when the model has a price-table entry, else
      None — callers must check for None rather than treating a zero total
      as "priced at $0".
    - context_at_turn is input_tokens + cache_read_input_tokens + ephemeral_1h
      + ephemeral_5m tokens for this turn, computed regardless of pricing.
    - unpriced_tokens is the turn's total token count (input + output +
      cache_read + ephemeral_1h + ephemeral_5m) when the model is unpriced,
      else 0.
    """
    input_t = int(usage.get("input_tokens", 0))
    output_t = int(usage.get("output_tokens", 0))
    cache_read_t = int(usage.get("cache_read_input_tokens", 0))
    eph_1h, eph_5m = _cache_write_split(usage)
    # _cache_write_split runs twice for this turn (here and inside
    # _context_at_turn). It is pure, and the per-class splits below need the two
    # halves separately, so sharing the sum is the only deduplication available.
    context_at_turn = _context_at_turn(usage)

    rates = _model_rates(model)
    if rates is None:
        return None, context_at_turn, input_t + output_t + cache_read_t + eph_1h + eph_5m

    dollars = {
        "input": input_t / 1_000_000 * rates["input"],
        "output": output_t / 1_000_000 * rates["output"],
        "cache_read": cache_read_t / 1_000_000 * rates["cache_read"],
        "cache_write_1h": eph_1h / 1_000_000 * rates["cache_write_1h"],
        "cache_write_5m": eph_5m / 1_000_000 * rates["cache_write_5m"],
    }
    # usage.speed/usage.inference_geo report the API's settled outcome, not an
    # echo of the request, so no per-model eligibility list is needed here.
    # Neither field is in _USAGE_DRIFT_INVARIANT_KEYS, so a non-run-invariant
    # value on either gets no runtime drift canary today -- a known, accepted gap.
    if usage.get("speed") == "fast":
        dollars = {cls: val * _FAST_MODE_RATE_MULTIPLIER for cls, val in dollars.items()}
    if usage.get("inference_geo") == "us":
        dollars = {cls: val * _INFERENCE_GEO_US_RATE_MULTIPLIER for cls, val in dollars.items()}
    return dollars, context_at_turn, 0


def _token_counts(usage: dict) -> dict[str, int]:
    """Per-class raw token counts for one turn's usage, keyed like _TOKEN_CLASSES.

    Mirrors _price_turn's own class breakdown (same fields, same
    _cache_write_split reuse) so cost's Tokens column sums the same fields
    its $ column already prices — callers apply it at the same point
    _price_turn's dollar accumulation does, after a turn is confirmed priced,
    so an unpriced model's tokens are excluded here too.
    """
    eph_1h, eph_5m = _cache_write_split(usage)
    return {
        "input": int(usage.get("input_tokens", 0)),
        "output": int(usage.get("output_tokens", 0)),
        "cache_read": int(usage.get("cache_read_input_tokens", 0)),
        "cache_write_1h": eph_1h,
        "cache_write_5m": eph_5m,
    }


def _session_peak_context(main_thread_turns: Sequence[tuple[int, int, int]]) -> tuple[float, int]:
    """Track a session's peak context two ways over the same main-thread turns.

    Each element of main_thread_turns is one turn's
    (context_at_turn, output_tokens, context_window) — context_at_turn from
    _price_turn (input + cache_read + ephemeral_1h + ephemeral_5m).

    Returns (peak_pct, peak_abs_tokens):
    - peak_pct is the session's maximum context_at_turn / context_window.
    - peak_abs_tokens is the session's maximum context_at_turn + output_tokens
      — the hook's own four-field ESTIMATE unit.

    The two are tracked as independent per-turn maxima, never one derived
    from the other (peak_abs_tokens != peak_pct * window): on a session that
    mixes a 200k-window turn with a 1M-window turn, the turn with the
    highest percentage of its own window is not necessarily the turn with
    the highest absolute token count.
    """
    peak_pct = 0.0
    peak_abs_tokens = 0
    for context_at_turn, output_tokens, context_window in main_thread_turns:
        pct = context_at_turn / context_window
        if pct > peak_pct:
            peak_pct = pct
        abs_tokens = context_at_turn + output_tokens
        if abs_tokens > peak_abs_tokens:
            peak_abs_tokens = abs_tokens
    return peak_pct, peak_abs_tokens


def _pct_of(value: float, total: float) -> str:
    """value/total as a percentage string; 0.0% (not an undefined dash) when total is zero."""
    return f"{100 * value / total:.1f}%" if total else "0.0%"


def _pct_value(value: float, total: float) -> float:
    """value/total as a percentage float, matching _pct_of's 0.0-when-zero
    convention -- for a caller (cost-ledger) that stores the number rather
    than printing it."""
    return 100 * value / total if total else 0.0


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


_DO_NOT_PUBLISH_BANNER = (
    "DO NOT PUBLISH — this output contains real project names and session IDs."
)

# Subcommands that resolve their own multi-root scan via their own
# subcommand-level --config-dir (_resolve_cost_roots) instead of the
# top-level --config-dir main() reassigns PROJECTS_DIR from — main() refuses
# the top-level flag outright for each of these, so the two same-named flags
# can never validate against two different accounts.
_SUBCOMMANDS_WITH_OWN_CONFIG_DIR = (
    "cost", "context-distribution", "edit-format", "read-scope", "subagents", "subagent-mix", "cost-trend"
)


def _resolve_cost_roots(args: argparse.Namespace, subcommand: str = "cost") -> list[Path]:
    """Assemble a subcommand's scan roots from the default config dir, every
    declared_transcript_roots() entry, and any --config-dir extras, in that
    order, deduped by resolved path.

    Mirrors post-crash-sessions.py:1067-1111's --config-dir contract: exit 2
    on a --config-dir extra that is not a directory or lacks a projects/
    subdirectory. A declared_transcript_roots() entry never exits 2 on the
    same defect -- that function already validated and skipped it (see its
    own docstring) rather than exiting, since a stale roots-file line must not
    break every invocation the way a bad command-line flag should.
    --this-repo unions across every resolved root here, including
    --config-dir extras: _iter_scoped_sessions matches by basename, and
    _path_to_project_slug derives slugs from `git worktree list` alone, so
    one checkout's slugs are root-independent. --no-redact on more than one
    root would put one client's real project names into a report meant for
    another — refused here, exit 2, rather than silently scoping to the
    wrong thing. Returns each root's projects/ subdirectory, ready for
    _resolve_project_scope's roots parameter.

    `subcommand` labels the printed refusal messages (default "cost", cost's
    own long-standing call sites and tests); context-distribution passes its
    own name so a refusal is attributed to the subcommand the caller actually
    invoked, not always "cost".

    When `subcommand == "cost"` and args.summary is set, this returns
    [config_dir() / "projects"] alone, skipping the declared-roots union
    entirely -- --summary is a single-account, aggregate-only mode, and
    unioning declared_transcript_roots() here would publish another
    account's spend inside a PR authored under this one. Gated on
    `subcommand` too, not just the flag: this function is shared by every
    entry in _SUBCOMMANDS_WITH_OWN_CONFIG_DIR, and only cost's argparser
    defines --summary today, so a bare summary check would silently narrow
    a future subcommand that happens to add a same-named flag.
    """
    if subcommand == "cost" and bool(getattr(args, "summary", False)):
        return [config_dir() / "projects"]

    extra_config_dirs: list[str] = getattr(args, "extra_config_dirs", None) or []

    config_dirs: list[Path] = []
    seen_resolved: set[Path] = set()
    for candidate in (config_dir(), *declared_transcript_roots()):
        resolved = candidate.resolve()
        if resolved in seen_resolved:
            continue
        seen_resolved.add(resolved)
        config_dirs.append(candidate)
    for raw in extra_config_dirs:
        candidate = Path(raw)
        if not candidate.is_dir():
            print(f"{subcommand}: --config-dir {raw!r} is not a directory", file=sys.stderr)
            sys.exit(2)
        if not (candidate / "projects").is_dir():
            print(
                f"{subcommand}: --config-dir {raw!r} rejected: no projects/ subdirectory found",
                file=sys.stderr,
            )
            sys.exit(2)
        resolved = candidate.resolve()
        if resolved in seen_resolved:
            continue
        seen_resolved.add(resolved)
        config_dirs.append(candidate)

    if len(config_dirs) > 1 and getattr(args, "no_redact", False):
        print(
            f"{subcommand}: --no-redact is refused when more than one root is in scope"
            " (--config-dir was given); drop --no-redact or scope to a single profile",
            file=sys.stderr,
        )
        sys.exit(2)

    return [d / "projects" for d in config_dirs]


def _scan_root_transcripts(root: Path, projects_glob: str, slugs: Sequence[str] | None = None) -> tuple[int, int]:
    """Count transcripts found vs. unreadable directly under one cost scan root.

    A dedicated readability probe (open, discard) rather than routing through
    _read_session_file, which swallows OSError into an empty record list
    indistinguishable from a genuinely empty transcript — cmd_cost's per-root
    summary line needs the two counted separately.

    Raises PermissionError when `root` itself is not readable/searchable.
    This is an explicit os.access probe, not reliance on Path.glob to raise —
    verified empirically that glob silently swallows OSError while walking an
    unreadable directory and returns no matches rather than propagating, so a
    permission-restricted root would otherwise misreport as "0 transcripts
    found," the same shape as a genuinely empty scope. An unreadable project
    subdirectory nested under an otherwise-readable root is still silently
    absorbed into the transcript count (glob's real behavior) — this probe
    only covers the root itself, the realistic misconfigured-`--config-dir`
    case. Callers scanning multiple untrusted roots catch this per root so
    one bad root doesn't abort the whole report.

    `slugs`, when given, restricts the scan to those exact project-dir names
    (mirroring _iter_scoped_sessions' identity match, not a glob) instead of
    `projects_glob` — required for --this-repo, whose _projects_glob(args) is
    always "*" regardless of scope. Without this, the diagnostic line would
    report the whole config dir's transcript count under --this-repo, masking
    a genuinely-empty repo-scoped result behind an unrelated nonzero total —
    the exact silent-zero failure Step 8 exists to surface. Both branches dedup
    matched project dirs by resolved real path via _dedup_new_project_dirs, so
    a project dir aliased to another (by symlink, whether reached by slug or
    by glob) doesn't double-count in this diagnostic either.
    """
    if not os.access(root, os.R_OK | os.X_OK):
        raise PermissionError(errno.EACCES, "Permission denied", str(root))
    visited_dirs: set[Path] = set()
    candidates = (root / slug for slug in slugs) if slugs is not None else sorted(root.glob(projects_glob))
    jsonl_paths = [
        jsonl
        for proj_dir in _dedup_new_project_dirs(candidates, visited_dirs)
        for jsonl in proj_dir.glob("*.jsonl")
    ]
    skipped = 0
    for jsonl in jsonl_paths:
        try:
            with open(jsonl):
                pass
        except OSError:
            skipped += 1
    return len(jsonl_paths), skipped


def _root_index_for_path(jsonl: Path, resolved_roots: Sequence[Path]) -> int:
    """Return the 0-based index of the root under which jsonl was found.

    `resolved_roots` must already be resolved (real, symlink-free) paths —
    callers resolve the roots list once, outside cost's per-session loop,
    since this runs once per session and re-resolving every root on every
    call would be a per-element filesystem stat inside that loop.
    `jsonl` is always a file path, so a root can only ever be one of its
    ancestors, never equal to it — `resolved.parents` alone covers every case.

    Returns the FIRST matching root by list order when one declared root is a
    genuine filesystem descendant of another (not just a symlink alias back
    to it, which the dedup guards upstream already collapse to one root's
    worth of sessions) — attributing every session under the nested root to
    whichever sorts earlier, always the default config dir. Untested and
    low-realism given this repo's own per-account layout (sibling
    directories under `~/.config/claude-accounts/`, never nested); revisit if
    `--config-dir` is ever pointed at a directory nested inside another
    declared root.
    """
    resolved = jsonl.resolve()
    for idx, resolved_root in enumerate(resolved_roots):
        if resolved_root in resolved.parents:
            return idx
    # Deliberately omits the raw jsonl path: main() has no top-level exception
    # handler, so this message would otherwise reach stderr uncaught — the
    # same reasoning and fix already applied to the redact-map-miss assertion
    # a few lines below in the same loop (structural sibling, audited after a
    # cumulative-diff review caught the fix hadn't been applied here too).
    path_hash = hashlib.sha256(str(jsonl).encode()).hexdigest()[:12]
    raise AssertionError(
        f"cost: a session path (hash {path_hash}) matched no known scan root — roots list is"
        " out of sync with the session iterator (a symlinked project dir resolving outside"
        " every declared root is one way this can happen)"
    )


# The harness's ephemeral-isolation branch name for an `isolation: "worktree"`
# subagent dispatch (see claude/.claude/CLAUDE.md's Agent Briefing section) —
# not a claim about which branch the dispatched work belongs to.
_WORKTREE_AGENT_BRANCH_PREFIX = "worktree-agent-"


def _session_branch_index(records: Sequence[dict]) -> list[tuple[float, str]]:
    """Build one session's sorted (timestamp, gitBranch) index from its own
    main-thread (non-sidechain) records — the carry-forward source
    _attributed_branch resolves a worktree-agent-* record's branch against.

    Built fresh per session, from that session's records alone: this is new
    machinery, not an extension of GH-482's position-based carry-forward
    (cmd_review_trace/cmd_judgment_pair), which never crosses the main-file/
    subagents-subdirectory boundary. A record with no parseable timestamp
    cannot be placed in timestamp order and is excluded.
    """
    index: list[tuple[float, str]] = []
    for main_rec in records:
        if bool(main_rec.get("isSidechain")):
            continue
        main_branch = main_rec.get("gitBranch")
        if not main_branch:
            continue
        main_ts = _parse_ts(main_rec.get("timestamp"))
        if main_ts is None:
            continue
        index.append((main_ts, main_branch))
    index.sort()
    return index


def _attributed_branch(rec: dict, branch_index: Sequence[tuple[float, str]]) -> str | None:
    """Resolve one record's branch for --branches filtering.

    A record whose own gitBranch starts with _WORKTREE_AGENT_BRANCH_PREFIX is
    resolved instead against branch_index (see _session_branch_index): the
    entry with the largest timestamp <= the record's own, falling forward to
    the index's earliest entry when none precedes it (dispatched before any
    main-thread activity in the session, or the record itself carries no
    parseable timestamp) — the dispatching session's branch active at that
    moment, correctly resolving through a mid-session branch switch. Every
    other record's own gitBranch is returned unchanged.

    Returns None — the "?" sentinel case, reusing GH-482's convention for "no
    signal to carry forward" — when branch_index is empty (no main-thread
    branch-bearing record anywhere in the session) or when rec itself carries
    no gitBranch at all.
    """
    raw_branch = rec.get("gitBranch") or ""
    if not raw_branch.startswith(_WORKTREE_AGENT_BRANCH_PREFIX):
        return raw_branch or None
    if not branch_index:
        return None
    rec_ts = _parse_ts(rec.get("timestamp"))
    if rec_ts is None:
        return branch_index[0][1]
    resolved = branch_index[0][1]
    for entry_ts, entry_branch in branch_index:
        if entry_ts > rec_ts:
            break
        resolved = entry_branch
    return resolved


def cmd_cost(args: argparse.Namespace) -> None:
    """CLI entry point for the cost subcommand.

    Reads the wall-clock date exactly once, here, then delegates to
    _cost_report, which takes `today` as an explicit parameter. The staleness
    banner must never read the clock itself — otherwise every test asserting
    cost's stdout would start failing the moment a rate's `expires` date passes.
    UTC, matching _fmt_date's convention and the UTC-implicit _PRICING_FETCH_DATE
    and _MODEL_RATE_EXPIRES dates — a local-time date.today() could shift the
    staleness banner's boundary day by the operator's UTC offset. Root
    resolution happens here, at the CLI boundary, rather than inside
    _cost_report, so --config-dir validation exits before any scan work.
    """
    roots = _resolve_cost_roots(args)
    _cost_report(args, datetime.now(UTC).date(), roots)


def _accumulate_per_account_turn(
    account_totals: dict, dollars_by_class: dict[str, float], token_counts: dict[str, int],
    turn_total: float, model: str,
) -> None:
    """Add one priced turn's per-class dollars/tokens and per-model dollars
    into one account's per_account entry -- the identical increments
    class_totals/class_token_totals/model_totals receive globally, just
    scoped to a single redact_ordinals ordinal."""
    for cls in _TOKEN_CLASSES:
        account_totals["class_totals"][cls] += dollars_by_class[cls]
        account_totals["class_token_totals"][cls] += token_counts[cls]
    account_totals["model_totals"][model] += turn_total


def _print_token_class_table(
    class_totals: dict[str, float], class_token_totals: dict[str, int], grand_total: float
) -> None:
    print("## Cost by token class\n")
    print(f"{'Class':<16} {'$':>14} {'Share':>7} {'Tokens':>14}")
    for cls in _TOKEN_CLASSES:
        val = class_totals[cls]
        tok = class_token_totals[cls]
        print(f"{cls:<16} {val:>14,.2f} {_pct_of(val, grand_total):>7} {tok:>14,}")
    print(f"{'total':<16} {grand_total:>14,.2f}")


def _print_model_id_table(model_totals: dict[str, float], grand_total: float) -> None:
    print("\n## Cost by model ID\n")
    print(f"{'Model':<28} {'$':>14} {'Share':>7}")
    for model, val in sorted(model_totals.items(), key=lambda kv: kv[1], reverse=True):
        print(f"{model:<28} {val:>14,.2f} {_pct_of(val, grand_total):>7}")


# A root whose earliest in-scope turn is more than this many seconds newer
# than a requested --since window's start fires _cost_report's
# corpus-coverage warning below -- one day, not zero, so ordinary
# per-record timestamp variance right at the window boundary doesn't warn.
_CORPUS_COVERAGE_WARNING_THRESHOLD_SECONDS = 86400


def _cost_report(args: argparse.Namespace, today: date, roots: Sequence[Path] | None = None) -> None:
    """Corpus-wide dollar-cost report by token class, model ID, and context-at-turn bucket.

    Sidechain (subagent) turns are priced exactly once: iter_sessions is
    called with include_subagents=True so subagent-dispatched spend is
    counted toward the total, matching real billing — cmd_audit_routing's
    Opus-only, main-thread-only scope would silently exclude most of it.

    roots is None for every direct caller other than cmd_cost (this module's
    own tests included) — that keeps the single-root report byte-for-byte
    unchanged, including the absence of the per-root scan-summary lines below,
    which only cmd_cost's CLI path (always passing an explicit roots list)
    emits.

    --summary renders a wholly separate, aggregate-only block (session and
    priced-turn counts plus class/model/thread totals, never a per-session or
    per-project row) instead of the full report below — see the summary_mode
    branches threaded through this function. --branches filters the per-turn
    loop on each record's *attributed* branch (_attributed_branch), not its
    literal gitBranch: a worktree-agent-* record (an isolation:"worktree"
    subagent dispatch) is resolved by carry-forward against its own
    session's main-thread branch history instead.
    """
    top_n: int = getattr(args, "top", 20) or 20
    redact: bool = not bool(getattr(args, "no_redact", False))

    scan_roots: Sequence[Path] = roots if roots is not None else (PROJECTS_DIR,)
    multi_root = len(scan_roots) > 1

    summary_mode: bool = bool(getattr(args, "summary", False))
    if summary_mode:
        # --this-repo alone is the gate: every _path_to_project_slug-derived
        # slug is "-"-prefixed, so "any --projects value other than the
        # literal default *" would still admit a machine-wide glob like "-*".
        if not getattr(args, "this_repo", False) or getattr(args, "projects", None) not in (None, "*"):
            print(
                "cost: --summary requires --this-repo and refuses any --projects scope"
                " (including the default glob) — see docs/transcript-analysis.md",
                file=sys.stderr,
            )
            sys.exit(2)
        if (
            bool(getattr(args, "by_project", False))
            or bool(getattr(args, "no_redact", False))
            or getattr(args, "extra_config_dirs", None)
        ):
            print(
                "cost: --summary refuses --by-project, --no-redact, and --config-dir in"
                " combination — it is a fixed, aggregate-only output mode",
                file=sys.stderr,
            )
            sys.exit(2)
        if multi_root:
            # Defense-in-depth: _resolve_cost_roots is the CLI-level
            # enforcement point for --summary's single-account scope, but
            # every direct caller of _cost_report (including this module's
            # own tests) bypasses that boundary — same rationale as the
            # --no-redact guard below.
            print(
                "cost: --summary resolved to more than one root — refusing to"
                " report a multi-account total",
                file=sys.stderr,
            )
            sys.exit(2)

    # Defense-in-depth: _resolve_cost_roots is the CLI-level enforcement point
    # for this refusal, but every direct caller of _cost_report (including
    # this module's own tests) bypasses that boundary — this function is the
    # one that actually prints raw labels when redact is False, so it must
    # not trust an already-validated `roots`/`no_redact` combination.
    if not redact and multi_root:
        print(
            "cost: --no-redact is refused when more than one root is in scope"
            " (--config-dir was given); drop --no-redact or scope to a single profile",
            file=sys.stderr,
        )
        sys.exit(2)

    if not redact:
        print(_DO_NOT_PUBLISH_BANNER)
        print(_DO_NOT_PUBLISH_BANNER, file=sys.stderr)

    since_ts, since_raw = _parse_since_nd_arg(args, "cost")
    since_label = since_raw or ""
    branch_filter = _branch_filter(args)

    # _resolve_project_scope's fail-closed --this-repo check runs before
    # _build_redact_map's full-corpus disk scan, so an out-of-repo failure
    # exits without paying for that scan.
    session_iter, scope_label = _resolve_project_scope(args, "cost", include_subagents=True, roots=roots)

    # Resolved once, outside the per-session loop below — _root_index_for_path
    # runs once per session (per-account ordinal resolution needs it even for
    # unpriced sessions), and re-resolving every root on every call would be
    # a per-element filesystem stat inside that loop.
    resolved_scan_roots = [root.resolve() for root in scan_roots] if multi_root else []
    # redact_ordinals is the resolved-path-sorted mapping _build_redact_map's
    # keys, the per-row/--by-project lookups, and the per-root scan-diagnostic
    # loop below all share, so the same physical root reads as the same
    # account-N everywhere regardless of scan order. Computed unconditionally
    # (not gated on multi_root) -- the diagnostic loop runs at single root too
    # (roots is not None whenever cmd_cost's CLI path is reached, even with
    # zero declared extras), and _redaction_ordinals is correct and cheap on a
    # single-element list. root_by_ordinal is its inverse -- used for the
    # (unreachable when multi_root, since --no-redact is refused above)
    # non-redact display path, and for the corpus-coverage warning below.
    redact_ordinals: dict[Path, int] = _redaction_ordinals(scan_roots)
    root_by_ordinal: dict[int, Path] = {redact_ordinals[root.resolve()]: root for root in scan_roots}
    # A single explicit root's ordinal never varies across sessions, so it's
    # resolved once here rather than per-session like resolved_scan_roots'
    # multi-root lookup below -- None when roots is None (the non-cmd_cost
    # direct-call path, which gets no per-root coverage warning either).
    single_root_ordinal: int | None = (
        redact_ordinals[scan_roots[0].resolve()] if roots is not None and not multi_root else None
    )

    total_transcripts_scanned = 0
    if roots is not None:
        glob = _projects_glob(args)
        # --this-repo's slugs were already resolved (and cached on args) by
        # _resolve_project_scope above; passing them keeps this diagnostic
        # scan repo-scoped instead of falling back to _projects_glob's "*".
        this_repo_slugs = getattr(args, "_this_repo_slugs", None) if args.this_repo else None
        for root in scan_roots:
            # Looked up via redact_ordinals, not enumerate()'s scan-order index —
            # the same physical root must read as the same account-N here as in
            # the report below, regardless of which order scan_roots iterates in.
            root_label = f"account-{redact_ordinals[root.resolve()]}" if redact else str(root.parent)
            try:
                scanned, skipped = _scan_root_transcripts(root, glob, slugs=this_repo_slugs)
            except PermissionError as exc:
                # str(exc) on a PermissionError typically embeds the offending
                # path — suppressed under default redaction so a permission
                # failure can't leak the raw config-dir path it's reporting on.
                detail = str(exc) if not redact else "permission denied"
                print(f"cost: {root_label}: cannot scan ({detail}) — treating as 0 transcripts", file=sys.stderr)
                scanned, skipped = 0, 0
            print(f"cost: {root_label}: scanned {scanned:,} transcripts, {skipped:,} skipped (unreadable)")
            total_transcripts_scanned += scanned
            if scanned == 0:
                print(
                    f"WARNING: cost: {root_label}: no transcripts found for this scope"
                    " — check the config dir and --projects/--this-repo filter."
                )

    # --summary skips the redact map and the per-project-dir-count scope
    # header ("this repo (N project dirs)") -- that count comes from `git
    # worktree list` (this repo's own local worktrees), not account
    # identity; the input that IS identity-keyed under --summary, a raw
    # --projects value, is already refused above. Its own scope line below
    # reports total_transcripts_scanned instead.
    redact_map: dict[_RedactMapKey, str] = {}
    if not summary_mode:
        redact_map = _build_redact_map(roots) if redact else {}
        if redact:
            print(
                f"Corpus fingerprint: {_corpus_fingerprint(redact_map)}"
                "  (private-project labels are not comparable across a different fingerprint)"
            )
        _print_resolved_scope("cost", scope_label, scan_roots)

    session_redact_map: dict[str, str] = {}
    by_project: bool = bool(getattr(args, "by_project", False))

    class_totals: dict[str, float] = dict.fromkeys(_TOKEN_CLASSES, 0.0)
    class_token_totals: dict[str, int] = dict.fromkeys(_TOKEN_CLASSES, 0)
    model_totals: dict[str, float] = defaultdict(float)
    # One class_totals/class_token_totals/model_totals triple per
    # redact_ordinals ordinal, mirroring edit-format's own per_account shape.
    # Initialized up front for every ordinal so a zero-spend account still
    # renders a clean zero-state row instead of a missing key.
    per_account: dict[int, dict] = (
        {
            ordinal: {
                "class_totals": dict.fromkeys(_TOKEN_CLASSES, 0.0),
                "class_token_totals": dict.fromkeys(_TOKEN_CLASSES, 0),
                "model_totals": defaultdict(float),
            }
            for ordinal in redact_ordinals.values()
        }
        if multi_root
        else {}
    )
    unpriced_tokens: dict[str, int] = defaultdict(int)
    bucket_totals: dict[str, float] = defaultdict(float)
    # Earliest in-scope turn timestamp seen per root ordinal, regardless of
    # --since -- feeds the corpus-coverage warning after the loop below, so a
    # well-covered root can't mask a short one.
    root_earliest_ts: dict[int, float] = {}
    session_rows: list[dict] = []
    stale_models: set[str] = set()
    main_total = 0.0
    subagent_total = 0.0
    priced_session_count = 0
    priced_turn_count = 0
    # Keyed on (root_index_or_None, project_family) — see _project_family.
    project_totals: dict[tuple[int | None, str], float] = defaultdict(float)
    # One representative raw scoped_label per project_totals key, for redact
    # lookup (_redact_proj_label) — several worktree-suffixed raw labels can
    # collapse to one family, so the smallest raw label is picked for a
    # deterministic (not iteration-order-dependent) display choice.
    project_repr_label: dict[tuple[int | None, str], _RedactMapKey] = {}

    for jsonl, records in session_iter:
        records = _dedup_turns_by_request_id(records)
        raw_proj_label = _derive_proj_label(jsonl)
        session_id = jsonl.stem[:12]
        if redact and not summary_mode:
            _assign_session_redact_label(session_id, session_redact_map)
        session_total = 0.0

        # Hoisted out of the by_project-only block below so the per-account
        # accumulator (turn loop, further down) has this session's ordinal
        # regardless of --by-project.
        account_ordinal: int | None = None
        if multi_root:
            root_position = _root_index_for_path(jsonl, resolved_scan_roots)
            account_ordinal = redact_ordinals[resolved_scan_roots[root_position]]
        elif single_root_ordinal is not None:
            account_ordinal = single_root_ordinal

        # Only needed when --branches is active — the carry-forward source
        # _attributed_branch resolves each worktree-agent-* record against.
        branch_index = _session_branch_index(records) if branch_filter is not None else None

        for rec in records:
            if rec.get("type") != "assistant":
                continue
            msg = rec.get("message") or {}
            usage = msg.get("usage")
            if not usage:
                continue

            # Parsed unconditionally (not just when since_ts is set) so
            # root_earliest_ts reflects the corpus's actual earliest turn,
            # not just the earliest turn inside an already-applied --since
            # filter.
            rec_ts = _parse_ts(rec.get("timestamp"))
            if account_ordinal is not None and rec_ts is not None:
                earliest_so_far = root_earliest_ts.get(account_ordinal)
                if earliest_so_far is None or rec_ts < earliest_so_far:
                    root_earliest_ts[account_ordinal] = rec_ts

            if since_ts is not None and (rec_ts is None or rec_ts < since_ts):
                continue

            if branch_filter is not None:
                attributed_branch = _attributed_branch(rec, branch_index)
                if attributed_branch is None or attributed_branch not in branch_filter:
                    continue

            model = msg.get("model", "")
            dollars_by_class, context_at_turn, turn_unpriced_tokens = _price_turn(model, usage)

            if dollars_by_class is None:
                unpriced_tokens[model] += turn_unpriced_tokens
                continue

            if today > _MODEL_RATE_EXPIRES[model]:
                stale_models.add(model)

            token_counts = _token_counts(usage)
            turn_total = 0.0
            for cls in _TOKEN_CLASSES:
                class_totals[cls] += dollars_by_class[cls]
                class_token_totals[cls] += token_counts[cls]
                turn_total += dollars_by_class[cls]
            model_totals[model] += turn_total
            # multi_root and summary_mode can never co-occur here -- --summary
            # refuses --config-dir above, so this accumulator is unreachable
            # (not merely unused) under --summary.
            if multi_root:
                _accumulate_per_account_turn(per_account[account_ordinal], dollars_by_class, token_counts, turn_total, model)
            bucket_totals[_context_bucket(context_at_turn)] += turn_total
            session_total += turn_total
            priced_turn_count += 1
            if bool(rec.get("isSidechain")):
                subagent_total += turn_total
            else:
                main_total += turn_total

        if session_total:
            priced_session_count += 1
            if not summary_mode:
                if multi_root:
                    scoped_label: _RedactMapKey = (account_ordinal, raw_proj_label)
                else:
                    scoped_label = raw_proj_label
                if redact:
                    proj_display = _redact_proj_label(scoped_label, redact_map)
                    if proj_display == _REDACT_MAP_MISS_TOKEN:
                        # Deliberately omits raw_proj_label: main() has no top-level
                        # exception handler, so this message would otherwise reach
                        # stderr uncaught — re-leaking the exact client-identifying
                        # string --redact exists to hide. A short hash (like
                        # _corpus_fingerprint's) and the root ordinal are enough to
                        # debug a desync without exposing the plaintext label.
                        root_ordinal = scoped_label[0] if isinstance(scoped_label, tuple) else None
                        label_hash = hashlib.sha256(raw_proj_label.encode()).hexdigest()[:12]
                        root_desc = f"root {root_ordinal}" if root_ordinal is not None else "the single scan root"
                        raise AssertionError(
                            f"cost: redact map has no entry for a project label under {root_desc}"
                            f" (label hash {label_hash}) — the redact map's roots are out of sync"
                            " with the session iterator's roots"
                        )
                else:
                    proj_display = raw_proj_label
                session_rows.append({
                    "session_id": session_id,
                    "proj_label": proj_display,
                    "total": session_total,
                })

                if by_project:
                    root_component = scoped_label[0] if multi_root else None
                    project_key = (root_component, _project_family(raw_proj_label))
                    project_totals[project_key] += session_total
                    raw_part = scoped_label[1] if isinstance(scoped_label, tuple) else scoped_label
                    current_repr = project_repr_label.get(project_key)
                    current_raw_part = current_repr[1] if isinstance(current_repr, tuple) else current_repr
                    if current_repr is None or raw_part < current_raw_part:
                        project_repr_label[project_key] = scoped_label

    if since_ts is not None:
        for ordinal, earliest_ts in sorted(root_earliest_ts.items()):
            if earliest_ts - since_ts > _CORPUS_COVERAGE_WARNING_THRESHOLD_SECONDS:
                root_label = f"account-{ordinal}" if redact else str(root_by_ordinal[ordinal].parent)
                print(
                    f"WARNING: cost: {root_label}: earliest turn found is {_fmt_date(earliest_ts)},"
                    f" more than 1 day after the requested --since window start ({_fmt_date(since_ts)})"
                    " — this root's local corpus does not fully cover the requested window."
                )

    grand_total = sum(class_totals.values())

    # The three invariants below sum the same per-turn dollar increments (the
    # same dollars_by_class value feeds class_totals, main/subagent,
    # project_totals, and per_account in the same loop iteration) through a
    # different accumulator split — they guard the partition/bucketing logic
    # (a branch that double-counts, drops, or misroutes a turn), not
    # _price_turn's dollar math itself, since a wrong per-turn price would
    # move both sides of any comparison together. Any gap beyond float64
    # summation noise (well under a millionth of a dollar here) still means a
    # real bucketing bug, not rounding.
    if abs(main_total + subagent_total - grand_total) > 1e-6:
        raise AssertionError(
            f"cost: main ({main_total:.6f}) + subagent ({subagent_total:.6f}) spend"
            f" does not equal the grand total ({grand_total:.6f}) — the isSidechain"
            " split is out of sync with the token-class totals"
        )

    if by_project:
        project_grand_total = sum(project_totals.values())
        if abs(project_grand_total - grand_total) > 1e-6:
            raise AssertionError(
                f"cost: --by-project rows sum to {project_grand_total:.6f} but the grand"
                f" total is {grand_total:.6f} — per-project aggregation is out of sync"
                " with the token-class totals"
            )

    if multi_root:
        per_account_class_total = sum(sum(acct["class_totals"].values()) for acct in per_account.values())
        per_account_model_total = sum(sum(acct["model_totals"].values()) for acct in per_account.values())
        if abs(per_account_class_total - grand_total) > 1e-6 or abs(per_account_model_total - grand_total) > 1e-6:
            raise AssertionError(
                f"cost: per-account totals (class {per_account_class_total:.6f}, model"
                f" {per_account_model_total:.6f}) do not both equal the grand total"
                f" ({grand_total:.6f}) — the per-account accumulator is out of sync with"
                " the global token-class/model totals"
            )

    title_since = f"last {since_label}" if since_label else "all time"
    if summary_mode:
        print(f"\n## Cost summary ({title_since})\n")
        print(
            f"Scope: this account only ({total_transcripts_scanned:,} transcripts scanned, "
            f"{priced_session_count:,} priced sessions, {priced_turn_count:,} priced turns)"
            " — dropping --summary reports every declared account too"
        )
    else:
        print(f"\n## Cost report ({title_since})\n")

    if stale_models:
        print(
            "STALE PRICING — today is past the re-verify-by date for: "
            + ", ".join(sorted(stale_models))
            + f". Re-check rates at {_PRICING_SOURCE_URL} before publishing the figures below.\n"
        )

    _print_token_class_table(class_totals, class_token_totals, grand_total)
    _print_model_id_table(model_totals, grand_total)
    total_unpriced_tokens = sum(unpriced_tokens.values())
    if summary_mode:
        # A dedicated, always-present line rather than the full report's
        # per-model breakdown below — an unrecognized model ID must never
        # silently understate a published figure with no marker, even at $0.
        print(f"\nUnpriced tokens: {total_unpriced_tokens:,} tokens across {len(unpriced_tokens)} model IDs")
    else:
        for model, tok in sorted(unpriced_tokens.items()):
            print(f"{model:<28} {'unpriced':>14} {tok:>10,} tokens")
        print(f"\nUnpriced tokens (unknown model IDs): {total_unpriced_tokens:,}")

    if not summary_mode:
        print(
            f"\n## Cost by context-at-turn bucket (input_tokens + cache_read_input_tokens"
            f" + ephemeral_1h + ephemeral_5m tokens, {_CONTEXT_BUCKET_THRESHOLD:,} boundary)\n"
        )
        print(f"{'Bucket':<8} {'$':>14} {'Share':>7}")
        for bucket in (_CONTEXT_BUCKET_UNDER, _CONTEXT_BUCKET_OVER):
            val = bucket_totals.get(bucket, 0.0)
            print(f"{bucket:<8} {val:>14,.2f} {_pct_of(val, grand_total):>7}")

    print("\n## Cost by thread\n")
    print(f"{'Thread':<10} {'$':>14} {'Share':>7}")
    print(f"{'main':<10} {main_total:>14,.2f} {_pct_of(main_total, grand_total):>7}")
    print(f"{'subagent':<10} {subagent_total:>14,.2f} {_pct_of(subagent_total, grand_total):>7}")

    if summary_mode:
        return

    if multi_root:
        print("\n## Cost by account\n")
        for ordinal in sorted(per_account):
            account_totals = per_account[ordinal]
            account_grand_total = sum(account_totals["class_totals"].values())
            print(f"\n### account-{ordinal}\n")
            _print_token_class_table(
                account_totals["class_totals"], account_totals["class_token_totals"], account_grand_total
            )
            _print_model_id_table(account_totals["model_totals"], account_grand_total)

    if by_project:
        print("\n## Cost by project\n")
        if not project_totals:
            print("(no priced turns in range)")
        elif multi_root:
            print(f"{'Account':<12} {'Project':<24} {'$':>14} {'Share':>7}")
            for (ordinal, family), val in sorted(project_totals.items(), key=lambda kv: kv[1], reverse=True):
                account_col = f"account-{ordinal}" if redact else str(root_by_ordinal[ordinal].parent)
                repr_label = project_repr_label[(ordinal, family)]
                # The redact map's multi-root value is "account-K/private-
                # project-N" (see _build_redact_map) — strip the account
                # prefix here since it's already the Account column above;
                # printing both is redundant. "claude-config" carries no "/"
                # and passes through unchanged.
                proj_col = (
                    _redact_proj_label(repr_label, redact_map).split("/", 1)[-1] if redact else family
                )
                print(f"{account_col:<12} {proj_col:<24} {val:>14,.2f} {_pct_of(val, grand_total):>7}")
        else:
            print(f"{'Project':<24} {'$':>14} {'Share':>7}")
            for (root_idx, family), val in sorted(project_totals.items(), key=lambda kv: kv[1], reverse=True):
                repr_label = project_repr_label[(root_idx, family)]
                proj_col = _redact_proj_label(repr_label, redact_map) if redact else family
                print(f"{proj_col:<24} {val:>14,.2f} {_pct_of(val, grand_total):>7}")

    print(f"\n## Top {top_n} sessions by dollars\n")
    if not session_rows:
        print("(no priced turns in range)")
    else:
        print(f"{'Session':<16} {'Proj':<24} {'$':>14}")
        for row in sorted(session_rows, key=lambda r: r["total"], reverse=True)[:top_n]:
            sid = _redact_session_id(row["session_id"], session_redact_map) if redact else row["session_id"]
            print(f"{sid:<16} {row['proj_label']:<24} {row['total']:>14,.2f}")


def cmd_context_distribution(args: argparse.Namespace) -> None:
    """CLI entry point for the context-distribution subcommand.

    Root resolution happens here, at the CLI boundary, rather than inside
    _context_distribution_report, mirroring cmd_cost — --config-dir
    validation exits before any scan work.
    """
    roots = _resolve_cost_roots(args, subcommand="context-distribution")
    _context_distribution_report(args, roots)


def _context_distribution_report(args: argparse.Namespace, roots: Sequence[Path] | None = None) -> None:
    """Per-session peak context, bucketed two ways — grounds a handoff-nudge
    threshold choice against measured sessions instead of picking one blind.

    Peak-context tracking is restricted to main-thread (non-sidechain) turns:
    a subagent dispatch pays its own prefix from scratch and is never a
    candidate for /handoff, so folding sidechain turns into a session's peak
    would mix two different context-growth stories into one number. Each
    main-thread turn's context_at_turn (input_tokens + cache_read_input_tokens
    + ephemeral_1h + ephemeral_5m, from _price_turn, same formula cost's own
    bucket logic uses) feeds two independent per-session maxima, tracked by
    _session_peak_context:
    - peak_pct: context_at_turn expressed as a fraction of that turn's own
      model's context window via _context_window_for_model — so a session
      that mixes models with different windows is judged by how close each
      turn came to its own model's limit, not a single window assumed for
      the whole session. Reported against
      _CONTEXT_DISTRIBUTION_THRESHOLD_PCTS.
    - peak_abs_tokens: context_at_turn plus that turn's output_tokens — the
      same four-field sum nudge-handoff-near-context-cap.sh's own ESTIMATE
      computes, so a threshold read off this table transfers directly to the
      hook's unit. Reported against _CONTEXT_DISTRIBUTION_THRESHOLD_ABS.
    Neither is derived from the other (peak_abs_tokens != peak_pct * window):
    on a session mixing a 200k-window turn with a 1M-window turn, the turn
    with the highest percentage of its own window need not be the turn with
    the highest absolute token count.

    Dollar totals per session sum ALL turns (main and sidechain), matching
    cost's own definition of a session's total spend — a session's dollar
    share reflects its full cost including any subagent work it spawned, not
    just its main-thread portion.

    roots is None for every direct caller other than cmd_context_distribution
    (this module's own tests included) — that keeps the single-root report
    byte-for-byte unchanged, including the absence of cmd_cost's per-root
    scan-summary lines, which only cost's own CLI path emits.
    """
    redact: bool = not bool(getattr(args, "no_redact", False))

    scan_roots: Sequence[Path] = roots if roots is not None else (PROJECTS_DIR,)
    multi_root = len(scan_roots) > 1

    # Defense-in-depth: _resolve_cost_roots is the CLI-level enforcement
    # point for this refusal, but every direct caller of this function
    # (including this module's own tests) bypasses that boundary.
    if not redact and multi_root:
        print(
            "context-distribution: --no-redact is refused when more than one root is in scope"
            " (--config-dir was given); drop --no-redact or scope to a single profile",
            file=sys.stderr,
        )
        sys.exit(2)

    if not redact:
        print(_DO_NOT_PUBLISH_BANNER)
        print(_DO_NOT_PUBLISH_BANNER, file=sys.stderr)

    since_ts, since_raw = _parse_since_nd_arg(args, "context-distribution")
    since_label = since_raw or ""

    session_iter, scope_label = _resolve_project_scope(
        args, "context-distribution", include_subagents=True, roots=roots
    )

    if roots is not None:
        # Mirrors cmd_cost's own per-root scan diagnostic (_scan_root_transcripts)
        # -- without it, a multi-root run matching nothing under every declared
        # root would print an empty report with no signal of which root(s) came
        # up empty, now that this subcommand scans multiple roots by default.
        glob = _projects_glob(args)
        this_repo_slugs = getattr(args, "_this_repo_slugs", None) if args.this_repo else None
        # Resolved-path-sorted, like _cost_report's own copy -- the same
        # physical root must read as the same account-N here regardless of
        # scan_roots' iteration order (active profile first). Computed
        # unconditionally, not gated on multi_root: this loop runs at single
        # root too whenever roots is not None, and _redaction_ordinals is
        # correct and cheap on a single-element list.
        redact_ordinals: dict[Path, int] = _redaction_ordinals(scan_roots)
        for root in scan_roots:
            root_label = f"account-{redact_ordinals[root.resolve()]}" if redact else str(root.parent)
            try:
                scanned, skipped = _scan_root_transcripts(root, glob, slugs=this_repo_slugs)
            except PermissionError as exc:
                # str(exc) on a PermissionError typically embeds the offending
                # path — suppressed under default redaction so a permission
                # failure can't leak the raw config-dir path it's reporting on.
                detail = str(exc) if not redact else "permission denied"
                print(
                    f"context-distribution: {root_label}: cannot scan ({detail})"
                    " — treating as 0 transcripts",
                    file=sys.stderr,
                )
                scanned, skipped = 0, 0
            print(
                f"context-distribution: {root_label}: scanned {scanned:,} transcripts,"
                f" {skipped:,} skipped (unreadable)"
            )
            if scanned == 0:
                print(
                    f"WARNING: context-distribution: {root_label}: no transcripts found for this scope"
                    " — check the config dir and --projects/--this-repo filter."
                )

    _print_resolved_scope("context-distribution", scope_label, scan_roots)

    title_since = f"last {since_label}" if since_label else "all time"
    print(f"\n## Context distribution report ({title_since})\n")

    session_peak_pcts: list[float] = []
    session_peak_abs_tokens: list[int] = []
    session_dollars: list[float] = []
    total_dollars = 0.0

    for _jsonl, records in session_iter:
        records = _dedup_turns_by_request_id(records)
        main_thread_turns: list[tuple[int, int, int]] = []
        session_total = 0.0

        for rec in records:
            if rec.get("type") != "assistant":
                continue
            msg = rec.get("message") or {}
            usage = msg.get("usage")
            if not usage:
                continue

            if since_ts is not None:
                rec_ts = _parse_ts(rec.get("timestamp"))
                if rec_ts is None or rec_ts < since_ts:
                    continue

            model = msg.get("model", "")
            dollars_by_class, context_at_turn, _turn_unpriced_tokens = _price_turn(model, usage)

            if dollars_by_class is not None:
                session_total += sum(dollars_by_class.values())

            if not bool(rec.get("isSidechain")):
                output_tokens = int(usage.get("output_tokens", 0))
                main_thread_turns.append((context_at_turn, output_tokens, _context_window_for_model(model)))

        peak_pct, peak_abs_tokens = _session_peak_context(main_thread_turns)

        if session_total == 0.0 and peak_pct == 0.0:
            continue

        session_peak_pcts.append(peak_pct)
        session_peak_abs_tokens.append(peak_abs_tokens)
        session_dollars.append(session_total)
        total_dollars += session_total

    total_sessions = len(session_peak_pcts)
    print(f"Sessions in scope: {total_sessions:,}   Total priced dollars: {total_dollars:,.2f}\n")

    print(
        "## Peak context-at-turn crossing thresholds (share of main-thread turns'"
        " own model context window; dollars include each session's subagent spend)\n"
    )
    print(f"{'Threshold':>10} {'Sessions':>9} {'SessShare':>10} {'$':>14} {'DollarShare':>12}")
    pct_rows = _context_distribution_rows(
        [pct / 100 for pct in _CONTEXT_DISTRIBUTION_THRESHOLD_PCTS], session_peak_pcts, session_dollars
    )
    for pct, row in zip(_CONTEXT_DISTRIBUTION_THRESHOLD_PCTS, pct_rows, strict=True):
        print(
            f"{pct:>9}% {row['sessions']:>9,} {row['session_share']:>10}"
            f" {row['dollars']:>14,.2f} {row['dollar_share']:>12}"
        )

    print(
        "\n## Peak absolute-token crossing thresholds (input + cache_read + cache_creation"
        " + output tokens across main-thread turns — nudge-handoff-near-context-cap.sh's own"
        " ESTIMATE unit; dollars include each session's subagent spend)\n"
    )
    print(f"{'Threshold':>10} {'Sessions':>9} {'SessShare':>10} {'$':>14} {'DollarShare':>12}")
    abs_rows = _context_distribution_rows(
        _CONTEXT_DISTRIBUTION_THRESHOLD_ABS, session_peak_abs_tokens, session_dollars
    )
    for abs_threshold, row in zip(_CONTEXT_DISTRIBUTION_THRESHOLD_ABS, abs_rows, strict=True):
        print(
            f"{abs_threshold:>10,} {row['sessions']:>9,} {row['session_share']:>10}"
            f" {row['dollars']:>14,.2f} {row['dollar_share']:>12}"
        )


# Edit/Write/MultiEdit are the only tools whose call/failure counts
# edit-format tracks; MultiEdit is kept as a member even though the tool no
# longer exists in current transcripts, so a future rename or reintroduction
# is counted rather than silently zeroing its denominator.
EDIT_FAMILY_TOOLS: frozenset[str] = frozenset({"Edit", "Write", "MultiEdit"})

# Known str_replace-mechanical failure shapes, matched case-sensitively
# against a failed Edit/Write/MultiEdit tool_result's own text, in order —
# the first match wins. "noop" matches the literal message Claude Code's
# Edit tool emits when old_string and new_string are identical.
_EDIT_KNOWN_FAILURE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("String to replace not found", "not_found"),
    ("has not been read yet", "unread"),
    ("but replace_all is false", "multi_match"),
    ("old_string and new_string are exactly the same", "noop"),
)

# This repo's own governance-hook/harness denial wordings that can deny an
# edit-family call, matched case-insensitively — a *different*, narrower
# purpose than _denial_hook_label's general "blocked by <name> hook/gate"
# extraction above: three of these six (path-spelling, permissions,
# worktree-isolation) are harness-native denial text, never a hook's own
# "blocked by ... hook/gate" wording, so _denial_hook_label's enumerated
# label set does not cover them.
_EDIT_GOVERNANCE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("blocked by plan-review gate", "plan-review"),
    ("reviewer-tree-mutation", "reviewer-tree"),
    ("worktree-enforcement", "worktree"),
    ("cannot be safely resolved", "path-spelling"),
    ("denied by your permission settings", "permissions"),
    ("isolated in the worktree", "worktree-isolation"),
)

_EDIT_FORMAT_UNCLASSIFIED = "unclassified"

_EDIT_CAUSE_REDACTED_CREDENTIAL = "redacted_credential"
_EDIT_CAUSE_WHITESPACE_ONLY = "whitespace_only"
_EDIT_CAUSE_CONTENT_DIFFERS = "content_differs"
_EDIT_CAUSE_ABANDONED_NO_RETRY = "abandoned_no_retry"
_EDIT_CAUSE_IDENTICAL_RETRY = "identical_retry"
# A not_found failure whose owner isn't "Edit" (MultiEdit's historical
# failure shape can emit the same text): edit_order only tracks Edit's own
# old_string, so there is nothing to pair this failure against for cause
# attribution -- counted here rather than silently dropped or crashing.
_EDIT_CAUSE_OWNER_NOT_TRACKED = "owner_not_tracked"

# redact-credential-values.sh's fixed replacement token (_lib.sh:980) — a
# not_found failure whose old_string or error text carries this was caused by
# the redactor rewriting file content the model had already read, not by a
# genuine uniqueness or staleness mismatch.
_REDACTED_CREDENTIAL_TOKEN = "[REDACTED-CREDENTIAL]"

# old_string length buckets for the size-distribution histogram, each
# (exclusive upper bound, label) tried in order; a length at or past every
# bound falls to _EDIT_OLD_STRING_SIZE_OVERFLOW_LABEL.
_EDIT_OLD_STRING_SIZE_BUCKETS: tuple[tuple[int, str], ...] = (
    (100, "0-99"),
    (300, "100-299"),
    (700, "300-699"),
    (1500, "700-1499"),
)
_EDIT_OLD_STRING_SIZE_OVERFLOW_LABEL = "1500+"

# ~4 chars/token, a standard rough estimate (not a per-tokenizer
# measurement) for English/code text — the same ratio measure_overhead.py
# used to produce this plan's headline token figures.
_EDIT_FORMAT_CHARS_PER_TOKEN = 4


def _old_string_size_bucket(length: int) -> str:
    for upper_bound, label in _EDIT_OLD_STRING_SIZE_BUCKETS:
        if length < upper_bound:
            return label
    return _EDIT_OLD_STRING_SIZE_OVERFLOW_LABEL


def _tool_result_text(content) -> str:
    """A tool_result's content is either a plain string or a content-block
    list; either way, render it to one string for substring matching."""
    return content if isinstance(content, str) else json.dumps(content)


def _new_edit_format_stats() -> dict:
    return {
        "calls": Counter(),  # tool name -> call count
        "known_failures": Counter(),  # (tool, label) -> count
        "governance": Counter(),  # governance label -> count
        "unclassified": 0,  # edit-family errors matching neither list above
        "unpaired": 0,  # is_error tool_result whose tool_use_id has no known owner
        "cause": Counter(),  # not_found cause label -> count
        "old_chars": 0,
        "new_chars": 0,
        "write_chars": 0,
        "output_tokens": 0,
        "old_string_size_hist": Counter(),  # size-bucket label -> count
    }


def _merge_edit_format_stats(dst: dict, src: dict) -> None:
    dst["calls"].update(src["calls"])
    dst["known_failures"].update(src["known_failures"])
    dst["governance"].update(src["governance"])
    dst["unclassified"] += src["unclassified"]
    dst["unpaired"] += src["unpaired"]
    dst["cause"].update(src["cause"])
    dst["old_chars"] += src["old_chars"]
    dst["new_chars"] += src["new_chars"]
    dst["write_chars"] += src["write_chars"]
    dst["output_tokens"] += src["output_tokens"]
    dst["old_string_size_hist"].update(src["old_string_size_hist"])


def _edit_notfound_cause(tool_use_id: str, err_text: str, edit_order: list[tuple[str, str, str]]) -> str:
    """Attribute one Edit `not_found` failure's cause by pairing it with the
    NEXT Edit call on the same file_path (in this session's own record
    order) and diffing the two old_strings under whitespace normalization —
    not by pattern-matching the failed old_string alone, which cannot
    distinguish "this string contains indentation" (true of most code) from
    "this edit failed because of whitespace."
    """
    idx = next((i for i, (tid, _fp, _old) in enumerate(edit_order) if tid == tool_use_id), None)
    if idx is None:
        # owner == "Edit" was already confirmed via `ids` before this is
        # called, so the failing call's own tool_use must already be in
        # edit_order — every Edit tool_use is appended there unconditionally,
        # and a tool_result always follows its tool_use in record order.
        raise AssertionError(
            "edit-format: a not_found failure's owning Edit tool_use is missing from"
            " this session's own edit_order — ids and edit_order disagree"
        )
    _tid, file_path, old = edit_order[idx]
    if _REDACTED_CREDENTIAL_TOKEN in old or _REDACTED_CREDENTIAL_TOKEN in err_text:
        return _EDIT_CAUSE_REDACTED_CREDENTIAL
    next_old = next((o for _tid2, fp2, o in edit_order[idx + 1 :] if fp2 == file_path), None)
    if next_old is None:
        return _EDIT_CAUSE_ABANDONED_NO_RETRY
    if next_old == old:
        return _EDIT_CAUSE_IDENTICAL_RETRY
    # Full whitespace strip, not run-collapse: a spacing-convention change
    # ("x=1" -> "x = 1") inserts whitespace where none existed, which a
    # collapse-runs-to-one-space comparison would treat as still different --
    # stripping entirely is what classifies that case as whitespace_only.
    # Known narrow false-positive this accepts: two strings that differ only
    # in WHERE a whitespace run sits at a token boundary ("foo bar" vs
    # "foob ar") collide after stripping. Below current scale to fix (see
    # docs/case-studies/hashline-edit-format.md's classifier-honesty
    # discussion) -- revisit if this bucket's share grows.
    if re.sub(r"\s+", "", next_old) == re.sub(r"\s+", "", old):
        return _EDIT_CAUSE_WHITESPACE_ONLY
    return _EDIT_CAUSE_CONTENT_DIFFERS


def _scan_edit_format_session(records: list[dict]) -> dict:
    """One session's (main thread + merged subagent files, per _read_session_file)
    Edit/Write/MultiEdit call and failure census, single pass.

    `ids` records every tool_use's id -> name, not just edit-family ones, so
    an is_error tool_result naming a non-edit-family owner (e.g. Bash) can be
    told apart from one whose owner is genuinely unknown (unpaired) rather
    than counting both the same way.
    """
    stats = _new_edit_format_stats()
    ids: dict[str, str] = {}
    edit_order: list[tuple[str, str, str]] = []  # (tool_use_id, file_path, old_string)
    # not_found cause attribution needs the FULL edit_order (including edits
    # that come after the failure) to find the retry, so classification is
    # deferred to a second pass below rather than run inline as each failure
    # is seen -- an inline lookup could only ever see edits already scanned.
    pending_notfound: list[tuple[str, str]] = []  # (tool_use_id, err_text)

    # One API call = one turn: dedup merges a requestId run's usage into a
    # single record, so output_tokens below is summed once per turn, not
    # once per content block.
    records = _dedup_turns_by_request_id(records)

    for rec in records:
        msg = rec.get("message") or {}
        usage = msg.get("usage") or {}
        if isinstance(usage.get("output_tokens"), int):
            stats["output_tokens"] += usage["output_tokens"]
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "tool_use":
                name = block.get("name")
                tool_id = block.get("id")
                ids[tool_id] = name
                tool_input = block.get("input") or {}
                if name == "Edit":
                    stats["calls"]["Edit"] += 1
                    old = tool_input.get("old_string") or ""
                    new = tool_input.get("new_string") or ""
                    stats["old_chars"] += len(old)
                    stats["new_chars"] += len(new)
                    stats["old_string_size_hist"][_old_string_size_bucket(len(old))] += 1
                    edit_order.append((tool_id, tool_input.get("file_path", "?"), old))
                elif name == "Write":
                    stats["calls"]["Write"] += 1
                    stats["write_chars"] += len(tool_input.get("content") or "")
                elif name == "MultiEdit":
                    stats["calls"]["MultiEdit"] += 1
            elif block_type == "tool_result" and block.get("is_error"):
                tool_use_id = block.get("tool_use_id")
                owner = ids.get(tool_use_id)
                if owner is None:
                    stats["unpaired"] += 1
                    continue
                if owner not in EDIT_FAMILY_TOOLS:
                    continue
                text = _tool_result_text(block.get("content"))
                label = next((lbl for pat, lbl in _EDIT_KNOWN_FAILURE_PATTERNS if pat in text), None)
                if label is not None:
                    stats["known_failures"][(owner, label)] += 1
                    if label == "not_found":
                        if owner == "Edit":
                            pending_notfound.append((tool_use_id, text))
                        else:
                            stats["cause"][_EDIT_CAUSE_OWNER_NOT_TRACKED] += 1
                    continue
                lowered = text.lower()
                gov_label = next((lbl for pat, lbl in _EDIT_GOVERNANCE_PATTERNS if pat in lowered), None)
                if gov_label is not None:
                    stats["governance"][gov_label] += 1
                else:
                    stats["unclassified"] += 1

    for tool_use_id, err_text in pending_notfound:
        stats["cause"][_edit_notfound_cause(tool_use_id, err_text, edit_order)] += 1
    return stats


def cmd_edit_format(args: argparse.Namespace) -> None:
    """CLI entry point for the edit-format subcommand.

    Root resolution happens here, mirroring cmd_cost/cmd_context_distribution,
    so --config-dir validation exits before any scan work.
    """
    roots = _resolve_cost_roots(args, subcommand="edit-format")
    _edit_format_report(args, roots)


def _edit_format_report(args: argparse.Namespace, roots: Sequence[Path] | None = None) -> None:
    """Single-pass Edit/Write/MultiEdit call census: per-tool failure
    classification, governance-hook re-bucketing, not_found cause
    attribution, and old_string/new_string/Write token overhead — one
    reproducible scan producing every figure, so separate runs at different
    corpus sizes cannot disagree with each other the way separate ad hoc
    scripts did.

    roots is None for every direct caller other than cmd_edit_format (this
    module's own tests included) — mirrors cost/context-distribution's own
    single-root-by-default contract, including the absence of the per-account
    breakdown below, which only a multi-root scan (an explicit roots list of
    more than one root) emits.

    This report's own content never varies with `redact` — like
    context-distribution, it carries no project name or session ID, and its
    per-account breakdown is always labelled account-N. --no-redact is still
    accepted and still enforces the same multi-root refusal and DO NOT
    PUBLISH banner as cost/context-distribution, for CLI parity.
    """
    redact: bool = not bool(getattr(args, "no_redact", False))
    scan_roots: Sequence[Path] = roots if roots is not None else (PROJECTS_DIR,)
    multi_root = len(scan_roots) > 1

    # Defense-in-depth: _resolve_cost_roots is the CLI-level enforcement
    # point for this refusal, but every direct caller of this function
    # (including this module's own tests) bypasses that boundary.
    if not redact and multi_root:
        print(
            "edit-format: --no-redact is refused when more than one root is in scope"
            " (--config-dir was given); drop --no-redact or scope to a single profile",
            file=sys.stderr,
        )
        sys.exit(2)

    if not redact:
        print(_DO_NOT_PUBLISH_BANNER)
        print(_DO_NOT_PUBLISH_BANNER, file=sys.stderr)

    session_iter, scope_label = _resolve_project_scope(args, "edit-format", include_subagents=True, roots=roots)
    _print_resolved_scope("edit-format", scope_label, scan_roots)

    # Resolved once, outside the per-session loop below, mirroring cost's own
    # _root_index_for_path usage — re-resolving every root on every session
    # would be a per-element filesystem stat inside that loop.
    resolved_scan_roots = [root.resolve() for root in scan_roots] if multi_root else []
    # Keyed by _redaction_ordinals, not _root_index_for_path's raw scan-order
    # position — the same physical root must read as the same account-N here
    # as in cost's and context-distribution's own per-account breakdowns,
    # regardless of which profile is currently active.
    redact_ordinals: dict[Path, int] = _redaction_ordinals(scan_roots) if multi_root else {}

    stats = _new_edit_format_stats()
    per_account: dict[int, dict] = (
        {ordinal: _new_edit_format_stats() for ordinal in redact_ordinals.values()} if multi_root else {}
    )

    for jsonl, records in session_iter:
        session_stats = _scan_edit_format_session(records)
        _merge_edit_format_stats(stats, session_stats)
        if multi_root:
            root_position = _root_index_for_path(jsonl, resolved_scan_roots)
            ordinal = redact_ordinals[resolved_scan_roots[root_position]]
            _merge_edit_format_stats(per_account[ordinal], session_stats)

    _print_edit_format_report(stats, per_account if multi_root else None)


def _print_edit_format_report(stats: dict, per_account: dict[int, dict] | None) -> None:
    calls = stats["calls"]
    edit_n = calls.get("Edit", 0)
    write_n = calls.get("Write", 0)
    multi_edit_n = calls.get("MultiEdit", 0)

    print("\n## Edit-family call census\n")
    print(f"Edit       {edit_n:,}")
    print(f"Write      {write_n:,}")
    print(f"MultiEdit  {multi_edit_n:,}  (recognized tool; expect 0 in a current corpus)")
    print(f"TOTAL      {edit_n + write_n + multi_edit_n:,}")

    print("\n## Failures by tool (str_replace-mechanical + no-op)\n")
    for (tool, label), count in sorted(stats["known_failures"].items()):
        denom = calls.get(tool, 0)
        print(f"  {tool:10} {label:12} count={count:6,}  rate={_pct_of(count, denom)} of {tool}")

    mechanical = sum(
        count
        for (tool, label), count in stats["known_failures"].items()
        if tool == "Edit" and label in ("not_found", "unread", "multi_match")
    )
    noop = stats["known_failures"].get(("Edit", "noop"), 0)
    print(
        "\nstr_replace-mechanical (Edit not_found+unread+multi_match, no-ops excluded): "
        f"{mechanical:,} / {edit_n:,} ({_pct_of(mechanical, edit_n)})"
    )
    print(
        "all non-governance Edit errors (no-ops included): "
        f"{mechanical + noop:,} / {edit_n:,} ({_pct_of(mechanical + noop, edit_n)})"
    )

    print("\n## not_found cause attribution (next-edit-same-file diff, whitespace-normalized)\n")
    cause_total = sum(stats["cause"].values())
    for cause, count in stats["cause"].most_common():
        print(f"  {cause:22} count={count:4,}  share={_pct_of(count, cause_total)}")

    print("\n## Governance-hook denials (excluded from the format failure rate)\n")
    governance_total = sum(stats["governance"].values())
    for _pattern, label in _EDIT_GOVERNANCE_PATTERNS:
        print(f"  {label:20} count={stats['governance'].get(label, 0):6,}")
    print(f"  {'TOTAL':20} count={governance_total:6,}")
    print(f"\n{_EDIT_FORMAT_UNCLASSIFIED} (edit-family errors matching neither list above): {stats['unclassified']:,}")

    print(f"\nunpaired (is_error tool_result with no matching tool_use in this session): {stats['unpaired']:,}")

    print("\n## Token/char overhead\n")
    old_chars = stats["old_chars"]
    new_chars = stats["new_chars"]
    write_chars = stats["write_chars"]
    output_tokens = stats["output_tokens"]
    edit_payload = old_chars + new_chars
    cpt = _EDIT_FORMAT_CHARS_PER_TOKEN
    print(f"old_string chars: {old_chars:,}  (~{old_chars // cpt:,} tok)")
    print(f"new_string chars: {new_chars:,}  (~{new_chars // cpt:,} tok)")
    print(f"old_string share of Edit payload: {_pct_of(old_chars, edit_payload)}")
    print(f"mean old_string chars/edit: {old_chars / edit_n:.0f}" if edit_n else "mean old_string chars/edit: n/a")
    print(f"write content chars: {write_chars:,}  (~{write_chars // cpt:,} tok)")
    print(f"total assistant output tokens (all sessions): {output_tokens:,}")
    print(f"old_string share of total output tokens: {_pct_of(old_chars // cpt, output_tokens)}")
    print(f"(old_string + new_string) share of total output tokens: {_pct_of(edit_payload // cpt, output_tokens)}")

    print("\nold_string size distribution:\n")
    bucket_labels = [label for _upper, label in _EDIT_OLD_STRING_SIZE_BUCKETS] + [_EDIT_OLD_STRING_SIZE_OVERFLOW_LABEL]
    for label in bucket_labels:
        count = stats["old_string_size_hist"].get(label, 0)
        print(f"  {label:10} {count:6,}  ({_pct_of(count, edit_n)})")

    if per_account is not None:
        print("\n## Per-account breakdown\n")
        for ordinal in sorted(per_account):
            account_stats = per_account[ordinal]
            account_label = f"account-{ordinal}"
            a_calls = account_stats["calls"]
            a_edit_n = a_calls.get("Edit", 0)
            if a_edit_n == 0 and a_calls.get("Write", 0) == 0 and a_calls.get("MultiEdit", 0) == 0:
                print(f"  {account_label:10} no edit-family calls")
                continue
            unread = account_stats["known_failures"].get(("Edit", "unread"), 0)
            not_found = account_stats["known_failures"].get(("Edit", "not_found"), 0)
            multi = account_stats["known_failures"].get(("Edit", "multi_match"), 0)
            addressable = not_found + multi
            print(
                f"  {account_label:10} calls={a_edit_n:6,}  unread={unread:4,}  "
                f"not_found={not_found:4,}  multi={multi:3,}  addressable={_pct_of(addressable, a_edit_n)}"
            )


# ~4 chars/token, the same rough English/code estimate _EDIT_FORMAT_CHARS_PER_TOKEN
# uses. A deliberate second pin rather than a shared constant: recalibrating one
# report's published figures must not silently move the other's.
_READ_SCOPE_CHARS_PER_TOKEN = 4

# Tools that answer "which part of this file do I need" before a targeted Read.
# Their mean result size prices the locate step a narrow-read discipline adds.
_READ_SCOPE_LOCATE_TOOLS = frozenset({"Grep", "Glob"})

_READ_SCOPE_COHORT_TARGETED = "targeted"
_READ_SCOPE_COHORT_WHOLE_FILE = "whole_file"
_READ_SCOPE_COHORT_PAGES = "pages"
_READ_SCOPE_COHORT_UNPARSED = "unparsed_input"

_READ_SCOPE_SCOPE_MAIN = "main"
_READ_SCOPE_SCOPE_SUBAGENT = "subagent"

# Result-size histogram buckets, in estimated tokens (chars // 4) — a call's
# result text length, not its input. The 2,000-token boundary lines up with
# the gross-ceiling threshold the case study's ceiling arithmetic uses.
_READ_SCOPE_SIZE_BUCKETS: tuple[tuple[int, str], ...] = (
    (500, "0-499"),
    (2000, "500-1999"),
    (5000, "2000-4999"),
    (15000, "5000-14999"),
)
_READ_SCOPE_SIZE_OVERFLOW_LABEL = "15000+"


def _read_scope_size_bucket(tokens: int) -> str:
    for upper_bound, label in _READ_SCOPE_SIZE_BUCKETS:
        if tokens < upper_bound:
            return label
    return _READ_SCOPE_SIZE_OVERFLOW_LABEL


def _classify_read_call(tool_input: dict) -> str:
    """Classify one Read tool_use's input by scope shape.

    A missing file_path (e.g. only __unparsedToolInput, or an empty input) is
    unparsed_input regardless of any other field: its scope is unknowable, and
    filing it as whole-file would inflate the cohort every published share is
    stated against. pages is checked before offset/limit since a PDF page-range
    read scopes via a different mechanism entirely, not layered on offset/limit.
    offset and limit are each checked with `is not None`, never truthiness --
    offset=0 is a valid first-line read and is falsy in Python.
    """
    if not tool_input.get("file_path"):
        return _READ_SCOPE_COHORT_UNPARSED
    if tool_input.get("pages") is not None:
        return _READ_SCOPE_COHORT_PAGES
    if tool_input.get("offset") is not None or tool_input.get("limit") is not None:
        return _READ_SCOPE_COHORT_TARGETED
    return _READ_SCOPE_COHORT_WHOLE_FILE


def _new_read_scope_stats() -> dict:
    return {
        "read_total": 0,
        "offset_n": 0,  # Read calls carrying offset (is not None)
        "limit_n": 0,  # Read calls carrying limit (is not None)
        "both_n": 0,  # Read calls carrying both offset and limit
        "cohort_n": Counter(),  # cohort label -> Read call count
        "unpaired": 0,  # Read tool_use with no matching tool_result in this session
        "error_result": 0,  # is_error tool_result for a Read call
        "non_text_result": 0,  # non-string tool_result content for a Read call (e.g. an image block)
        "cohort_scope_count": Counter(),  # (cohort, scope) -> result count reaching the histogram
        "cohort_scope_tokens": Counter(),  # (cohort, scope) -> est. token sum
        "size_hist": Counter(),  # (cohort, scope, bucket label) -> count
        # Same key -> summed est. tokens. Counts alone cannot answer "what share
        # of whole-file-read tokens sits above N", which is what any ceiling
        # estimate and the case study's revisit trigger are both stated against.
        "size_hist_tokens": Counter(),
        "read_result_tokens_total": 0,  # every Read tool_result's est. tokens, any cohort/error/text shape
        "all_tool_result_tokens_total": 0,  # every tool_result's est. tokens, any tool -- cross-check denominator
        # Locate-step sizing: a targeted read has to be located first, so any
        # saving quoted against whole-file reads is gross until this is netted.
        "locate_call_n": 0,
        "locate_result_tokens_total": 0,
        "repeat_whole_file_reads": 0,  # whole-file re-reads of a path within the same source-file/sessionId partition
        "repeat_whole_file_tokens": 0,
        "repeat_whole_file_output_log_reads": 0,  # sub-count of the above whose path ends .output/.log
        "growth_tokens": 0,  # prompt-token growth, per "Computing the denominator"
        "growth_unparseable_ts_excluded": 0,  # growth deltas dropped: --since active, owning turn's ts didn't parse
    }


def _merge_read_scope_stats(dst: dict, src: dict) -> None:
    dst["read_total"] += src["read_total"]
    dst["offset_n"] += src["offset_n"]
    dst["limit_n"] += src["limit_n"]
    dst["both_n"] += src["both_n"]
    dst["cohort_n"].update(src["cohort_n"])
    dst["unpaired"] += src["unpaired"]
    dst["error_result"] += src["error_result"]
    dst["non_text_result"] += src["non_text_result"]
    dst["cohort_scope_count"].update(src["cohort_scope_count"])
    dst["cohort_scope_tokens"].update(src["cohort_scope_tokens"])
    dst["size_hist"].update(src["size_hist"])
    dst["size_hist_tokens"].update(src["size_hist_tokens"])
    dst["read_result_tokens_total"] += src["read_result_tokens_total"]
    dst["all_tool_result_tokens_total"] += src["all_tool_result_tokens_total"]
    dst["locate_call_n"] += src["locate_call_n"]
    dst["locate_result_tokens_total"] += src["locate_result_tokens_total"]
    dst["repeat_whole_file_reads"] += src["repeat_whole_file_reads"]
    dst["repeat_whole_file_tokens"] += src["repeat_whole_file_tokens"]
    dst["repeat_whole_file_output_log_reads"] += src["repeat_whole_file_output_log_reads"]
    dst["growth_tokens"] += src["growth_tokens"]
    dst["growth_unparseable_ts_excluded"] += src["growth_unparseable_ts_excluded"]


def _read_scope_growth_for_group(group: list[dict], since_ts: float | None) -> tuple[int, int]:
    """Sum of positive prompt-token growth deltas within one source-file group
    (one _read_session_file_partitioned entry: the main transcript or one
    subagent file).

    Keys the delta chain by each assistant record's own sessionId rather than
    picking one reference id for the whole group: a subagent transcript file
    is named by its own agent id, but its records carry the *parent*
    session's sessionId, so neither the file's own stem nor a first-seen-in-
    iteration guess is a valid "this group's session" ground truth. Keying by
    sessionId means an interleaved foreign-session record simply forms its
    own chain and contributes its own real growth, instead of being dropped
    or corrupting a neighbouring session's delta. A record with no sessionId
    at all folds into the ""-keyed chain -- neither excluded nor treated as
    its own session, preserving this function's long-standing leniency for
    absent ids. Resets the whole per-session map at each compact_boundary
    record, since a compaction boundary applies to the file, not to one
    session. The first turn of every resulting per-session sequence has no
    predecessor and so contributes nothing. A turn with absent or malformed
    usage is skipped without breaking its session's chain for the turn after
    it. A shrinking context contributes nothing (never negative). Every
    chain is built over every record regardless of --since; --since filters
    only the completed deltas, by the later (owning) turn's own timestamp --
    fail-closed: a delta whose owning turn's timestamp doesn't parse is
    excluded rather than included, and counted in the returned exclusion
    total so the report's own growth figure stays auditable against
    --since's promise.

    Returns (growth_tokens, deltas_excluded_for_unparseable_timestamp).
    """
    prev_context_by_session: dict[str, int] = {}
    total = 0
    unparseable_ts_excluded = 0

    for rec in group:
        rec_type = rec.get("type")
        if rec_type == "system" and rec.get("subtype") == "compact_boundary":
            # Resets every session's chain, not just the compacted one: a
            # compact_boundary record carries no sessionId, so which session it
            # belongs to is not recoverable from the data. Costs one real delta
            # for any other session mid-chain in the same file. Inert on every
            # transcript shape seen so far -- a main file's records all carry
            # its own session id, a subagent file's all carry its parent's --
            # so a file holding two live chains does not currently arise.
            prev_context_by_session.clear()
            continue
        if rec_type != "assistant":
            continue

        usage = (rec.get("message") or {}).get("usage")
        if not usage:
            continue

        session_key = rec.get("sessionId") or ""
        context_at_turn = _context_at_turn(usage)
        prev_context = prev_context_by_session.get(session_key)
        if prev_context is not None:
            delta = context_at_turn - prev_context
            if delta > 0:
                rec_ts = _parse_ts(rec.get("timestamp"))
                if since_ts is not None and rec_ts is None:
                    unparseable_ts_excluded += 1
                elif since_ts is None or rec_ts >= since_ts:
                    total += delta
        prev_context_by_session[session_key] = context_at_turn

    return total, unparseable_ts_excluded


def _read_scope_repeat_whole_file_reads(groups: list[list[dict]]) -> tuple[int, int, int]:
    """Repeat whole-file-read detection, scoped to the same partition the
    growth chain uses (per source-file group, and per sessionId within a
    group) rather than to the whole flattened session.

    A parent transcript and its subagent are separate context windows: a
    subagent re-reading a file its parent already read is not a redundant
    read, it's the only way that subagent can see the file at all. Scoping
    to the flattened session would count every such cross-file read as a
    repeat, inflating the figure with reads that were never avoidable.

    Pairs each group's own Read tool_use/tool_result independently (a Read's
    result always lives in the same source file as its call), since this
    detection needs its own per-partition token histories and cannot reuse
    the flat pass's already-merged read_calls table. Returns
    (repeat_reads, repeat_tokens, repeat_output_log_reads) — pure aggregates;
    no file path is retained past this function.
    """
    # (group_index, sessionId-or-"") -> file_path -> [est_tokens, ...] in read order
    sizes_by_partition: dict[tuple[int, str], dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))

    for group_idx, group in enumerate(groups):
        read_calls: dict[str, dict] = {}  # tool_use_id -> {"file_path", "partition_key"}
        for rec in group:
            msg = rec.get("message") or {}
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            session_id = rec.get("sessionId") or ""
            for block in content:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type")
                if block_type == "tool_use":
                    if block.get("name") != "Read":
                        continue
                    tool_input = block.get("input") or {}
                    if _classify_read_call(tool_input) != _READ_SCOPE_COHORT_WHOLE_FILE:
                        continue
                    read_calls[block.get("id")] = {
                        "file_path": tool_input.get("file_path") or "",
                        "partition_key": (group_idx, session_id),
                    }
                elif block_type == "tool_result":
                    owner = read_calls.pop(block.get("tool_use_id"), None)
                    if owner is None or block.get("is_error"):
                        continue
                    result_content = block.get("content")
                    if not isinstance(result_content, str):
                        continue
                    tokens = len(result_content) // _READ_SCOPE_CHARS_PER_TOKEN
                    sizes_by_partition[owner["partition_key"]][owner["file_path"]].append(tokens)

    repeat_reads = 0
    repeat_tokens = 0
    repeat_output_log_reads = 0
    for by_path in sizes_by_partition.values():
        for file_path, sizes in by_path.items():
            if len(sizes) < 2:
                continue
            repeats = sizes[1:]
            repeat_reads += len(repeats)
            repeat_tokens += sum(repeats)
            if file_path.endswith((".output", ".log")):
                repeat_output_log_reads += len(repeats)

    return repeat_reads, repeat_tokens, repeat_output_log_reads


def _scan_read_scope_session(records: list[dict], groups: list[list[dict]], since_ts: float | None) -> dict:
    """One session's Read-call census, single pass over the flattened record
    order, plus per-group prompt-token growth and repeat-whole-file-read
    detection.

    `records` is the main thread + merged subagent files in flat, file-
    concatenation order (per _read_session_file) — everything here except
    growth and repeat-whole-file-read detection runs over it, since
    isSidechain is all that scope (main vs subagent) bucketing needs.
    `groups` is the same session's records kept separate per source file
    (per _read_session_file_partitioned) — growth and repeat-whole-file-read
    detection both need the file (and, within a file, sessionId) boundary
    the flat order discards; see _read_scope_growth_for_group and
    _read_scope_repeat_whole_file_reads.

    Every returned figure is a pure aggregate (counts and token sums) — no
    file path, filename, path fragment, or session identifier is retained
    past this function or printed by any caller.
    """
    stats = _new_read_scope_stats()

    read_calls: dict[str, dict] = {}  # tool_use_id -> {"cohort", "scope", "file_path"}
    locate_calls: set[str] = set()  # tool_use_ids of Grep/Glob calls, for locate-step sizing
    matched: set[str] = set()

    for rec in records:
        msg = rec.get("message") or {}
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        is_subagent = bool(rec.get("isSidechain"))
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "tool_use":
                tool_name = block.get("name")
                if tool_name in _READ_SCOPE_LOCATE_TOOLS:
                    # A targeted read has to be located first. Sizing that step
                    # is what lets a saving be quoted net rather than gross.
                    locate_calls.add(block.get("id"))
                    stats["locate_call_n"] += 1
                    continue
                if tool_name != "Read":
                    continue
                tool_id = block.get("id")
                tool_input = block.get("input") or {}
                has_offset = tool_input.get("offset") is not None
                has_limit = tool_input.get("limit") is not None
                if has_offset:
                    stats["offset_n"] += 1
                if has_limit:
                    stats["limit_n"] += 1
                if has_offset and has_limit:
                    stats["both_n"] += 1
                cohort = _classify_read_call(tool_input)
                stats["read_total"] += 1
                stats["cohort_n"][cohort] += 1
                read_calls[tool_id] = {
                    "cohort": cohort,
                    "scope": _READ_SCOPE_SCOPE_SUBAGENT if is_subagent else _READ_SCOPE_SCOPE_MAIN,
                    "file_path": tool_input.get("file_path") or "",
                }
            elif block_type == "tool_result":
                tool_use_id = block.get("tool_use_id")
                result_content = block.get("content")
                is_error = bool(block.get("is_error"))
                result_tokens = (
                    len(result_content) // _READ_SCOPE_CHARS_PER_TOKEN if isinstance(result_content, str) else 0
                )
                # Cross-check denominator: every tool's own result, Read or not.
                stats["all_tool_result_tokens_total"] += result_tokens
                if tool_use_id in locate_calls:
                    stats["locate_result_tokens_total"] += result_tokens

                owner = read_calls.get(tool_use_id)
                if owner is None:
                    continue
                matched.add(tool_use_id)
                stats["read_result_tokens_total"] += result_tokens

                if is_error:
                    stats["error_result"] += 1
                    continue
                if not isinstance(result_content, str):
                    stats["non_text_result"] += 1
                    continue

                cohort = owner["cohort"]
                if cohort not in (_READ_SCOPE_COHORT_TARGETED, _READ_SCOPE_COHORT_WHOLE_FILE):
                    continue
                scope = owner["scope"]
                stats["cohort_scope_count"][(cohort, scope)] += 1
                stats["cohort_scope_tokens"][(cohort, scope)] += result_tokens
                size_bucket = _read_scope_size_bucket(result_tokens)
                stats["size_hist"][(cohort, scope, size_bucket)] += 1
                stats["size_hist_tokens"][(cohort, scope, size_bucket)] += result_tokens

    stats["unpaired"] = len(read_calls) - len(matched)

    (
        stats["repeat_whole_file_reads"],
        stats["repeat_whole_file_tokens"],
        stats["repeat_whole_file_output_log_reads"],
    ) = _read_scope_repeat_whole_file_reads(groups)

    for group in groups:
        growth, unparseable_ts_excluded = _read_scope_growth_for_group(group, since_ts)
        stats["growth_tokens"] += growth
        stats["growth_unparseable_ts_excluded"] += unparseable_ts_excluded

    return stats


def cmd_read_scope(args: argparse.Namespace) -> None:
    """CLI entry point for the read-scope subcommand.

    Root resolution happens here, mirroring cmd_edit_format, so --config-dir
    validation exits before any scan work.
    """
    roots = _resolve_cost_roots(args, subcommand="read-scope")
    _read_scope_report(args, roots)


def _read_scope_report(args: argparse.Namespace, roots: Sequence[Path] | None = None) -> None:
    """Single-pass Read-call scope census: offset/limit/pages classification
    against the full call count, result-token distribution by targeted/
    whole-file cohort and main/subagent scope, repeat-whole-file-read
    aggregates, and per-file-and-sessionId-partitioned prompt-token growth.
    One reproducible scan producing every figure the case study cites.

    roots is None for every direct caller other than cmd_read_scope (this
    module's own tests included) — mirrors edit-format's own contract,
    including the absence of the per-account breakdown below.

    This report's own content never varies with `redact`: like edit-format,
    it carries no project name, session ID, file path, or path fragment —
    the repeat-whole-file-read aggregates are pure counts and token sums, and
    per-account rows use account-N labels. --no-redact is still accepted and
    still enforces the same multi-root refusal and DO NOT PUBLISH banner, for
    CLI parity.
    """
    redact: bool = not bool(getattr(args, "no_redact", False))
    scan_roots: Sequence[Path] = roots if roots is not None else (PROJECTS_DIR,)
    multi_root = len(scan_roots) > 1

    # Defense-in-depth: _resolve_cost_roots is the CLI-level enforcement
    # point for this refusal, but every direct caller of this function
    # (including this module's own tests) bypasses that boundary.
    if not redact and multi_root:
        print(
            "read-scope: --no-redact is refused when more than one root is in scope"
            " (--config-dir was given); drop --no-redact or scope to a single profile",
            file=sys.stderr,
        )
        sys.exit(2)

    if not redact:
        print(_DO_NOT_PUBLISH_BANNER)
        print(_DO_NOT_PUBLISH_BANNER, file=sys.stderr)

    since_ts, since_raw = _parse_since_nd_arg(args, "read-scope")
    since_label = since_raw or ""

    session_iter, scope_label = _resolve_project_scope(args, "read-scope", include_subagents=True, roots=roots)
    _print_resolved_scope("read-scope", scope_label, scan_roots)

    resolved_scan_roots = [root.resolve() for root in scan_roots] if multi_root else []

    stats = _new_read_scope_stats()
    per_account: list[dict] = [_new_read_scope_stats() for _ in scan_roots] if multi_root else []

    for jsonl, records in session_iter:
        # session_iter already read and parsed this file once internally (to
        # decide whether to yield it at all); this second, partitioned read
        # is the cost of reusing _resolve_project_scope's shared iterator,
        # which has no variant that also exposes the per-file boundary the
        # growth chain and repeat-whole-file-read detection need.
        groups = _read_session_file_partitioned(jsonl, include_subagents=True)
        session_stats = _scan_read_scope_session(records, groups, since_ts)
        _merge_read_scope_stats(stats, session_stats)
        if multi_root:
            idx = _root_index_for_path(jsonl, resolved_scan_roots)
            _merge_read_scope_stats(per_account[idx], session_stats)

    _print_read_scope_report(stats, per_account if multi_root else None, since_label)


def _read_scope_cohort_bucket_token_total(stats: dict, cohort: str) -> int:
    """Sum of `cohort`'s size_hist_tokens across both main and subagent scope
    -- the shared denominator each size-histogram bucket line's percentage
    divides by, so a subagent-dominated cohort's tokens aren't hidden behind
    the printing scope's own, smaller total."""
    bucket_labels = [label for _upper, label in _READ_SCOPE_SIZE_BUCKETS] + [_READ_SCOPE_SIZE_OVERFLOW_LABEL]
    return sum(
        stats["size_hist_tokens"].get((cohort, scope, label), 0)
        for scope in (_READ_SCOPE_SCOPE_MAIN, _READ_SCOPE_SCOPE_SUBAGENT)
        for label in bucket_labels
    )


def _print_read_scope_report(stats: dict, per_account: list[dict] | None, since_label: str) -> None:
    read_total = stats["read_total"]
    cohort_n = stats["cohort_n"]
    targeted_n = cohort_n.get(_READ_SCOPE_COHORT_TARGETED, 0)
    whole_file_n = cohort_n.get(_READ_SCOPE_COHORT_WHOLE_FILE, 0)
    pages_n = cohort_n.get(_READ_SCOPE_COHORT_PAGES, 0)
    unparsed_n = cohort_n.get(_READ_SCOPE_COHORT_UNPARSED, 0)

    print("\n## Read-call census\n")
    print(f"Read calls: {read_total:,}")
    print(f"  offset present: {stats['offset_n']:,}")
    print(f"  limit present:  {stats['limit_n']:,}")
    print(f"  both present:   {stats['both_n']:,}")
    # Cohort shares divide by the full Read call census, never targeted +
    # whole_file — pages/unparsed_input calls are real Read calls that would
    # otherwise silently vanish from the arithmetic.
    print(f"\ntargeted    {targeted_n:,}  ({_pct_of(targeted_n, read_total)} of Read calls)")
    print(f"whole_file  {whole_file_n:,}  ({_pct_of(whole_file_n, read_total)} of Read calls)")
    print(f"\npages (Read calls scoping a PDF via `pages` rather than offset/limit): {pages_n:,}")
    print(
        "unparsed_input (Read tool_use whose input carried no file_path, e.g. only"
        f" __unparsedToolInput -- scope unknowable): {unparsed_n:,}"
    )
    print(f"unpaired (Read tool_use with no matching tool_result found in this session): {stats['unpaired']:,}")
    print(f"error_result (is_error tool_result for a Read call, excluded from the size histogram): {stats['error_result']:,}")
    print(
        "non_text_result (non-string tool_result content for a Read call, e.g. an image"
        f" block, excluded from the size histogram): {stats['non_text_result']:,}"
    )

    print("\n## Result token distribution by cohort x scope\n")
    cpt = _READ_SCOPE_CHARS_PER_TOKEN
    for cohort, cohort_label in (
        (_READ_SCOPE_COHORT_TARGETED, "targeted"),
        (_READ_SCOPE_COHORT_WHOLE_FILE, "whole_file"),
    ):
        for scope in (_READ_SCOPE_SCOPE_MAIN, _READ_SCOPE_SCOPE_SUBAGENT):
            count = stats["cohort_scope_count"].get((cohort, scope), 0)
            tokens = stats["cohort_scope_tokens"].get((cohort, scope), 0)
            print(f"  {cohort_label:12} {scope:9} count={count:8,}  tokens=~{tokens:12,}")

    whole_file_tokens = sum(
        stats["cohort_scope_tokens"].get((_READ_SCOPE_COHORT_WHOLE_FILE, scope), 0)
        for scope in (_READ_SCOPE_SCOPE_MAIN, _READ_SCOPE_SCOPE_SUBAGENT)
    )
    targeted_tokens = sum(
        stats["cohort_scope_tokens"].get((_READ_SCOPE_COHORT_TARGETED, scope), 0)
        for scope in (_READ_SCOPE_SCOPE_MAIN, _READ_SCOPE_SCOPE_SUBAGENT)
    )
    result_tokens_total = whole_file_tokens + targeted_tokens
    print(f"\nwhole_file share of targeted+whole_file result tokens: {_pct_of(whole_file_tokens, result_tokens_total)}")
    subagent_whole_file_tokens = stats["cohort_scope_tokens"].get((_READ_SCOPE_COHORT_WHOLE_FILE, _READ_SCOPE_SCOPE_SUBAGENT), 0)
    print(f"whole_file tokens inside subagents: {_pct_of(subagent_whole_file_tokens, whole_file_tokens)}")

    print("\nsize histogram (est. tokens):\n")
    bucket_labels = [label for _upper, label in _READ_SCOPE_SIZE_BUCKETS] + [_READ_SCOPE_SIZE_OVERFLOW_LABEL]
    for cohort, cohort_label in (
        (_READ_SCOPE_COHORT_TARGETED, "targeted"),
        (_READ_SCOPE_COHORT_WHOLE_FILE, "whole_file"),
    ):
        cohort_tokens = _read_scope_cohort_bucket_token_total(stats, cohort)
        for scope in (_READ_SCOPE_SCOPE_MAIN, _READ_SCOPE_SCOPE_SUBAGENT):
            cohort_scope_count = stats["cohort_scope_count"].get((cohort, scope), 0)
            print(f"  {cohort_label} / {scope}:")
            for label in bucket_labels:
                count = stats["size_hist"].get((cohort, scope, label), 0)
                tokens = stats["size_hist_tokens"].get((cohort, scope, label), 0)
                print(
                    f"    {label:10} {count:6,}  ({_pct_of(count, cohort_scope_count)})"
                    f"  ~{tokens:11,} tok  ({_pct_of(tokens, cohort_tokens)} of {cohort_label} tokens)"
                )

    print(
        "\n## Repeat whole-file reads (same path read whole-file more than once in the same"
        " source file / sessionId -- a subagent re-reading what its parent already read is a"
        " separate context window, not a repeat)\n"
    )
    print(f"repeat reads: {stats['repeat_whole_file_reads']:,}  (~{stats['repeat_whole_file_tokens']:,} tok)")
    print(f"  of which .output/.log suffixed: {stats['repeat_whole_file_output_log_reads']:,}")

    title_since = f"last {since_label}" if since_label else "all time"
    print(f"\n## Prompt-token growth ({title_since})\n")
    growth_tokens = stats["growth_tokens"]
    print(f"prompt-token growth: {growth_tokens:,}")
    print(
        "growth deltas excluded (--since active, owning turn's timestamp unparseable):"
        f" {stats['growth_unparseable_ts_excluded']:,}"
    )
    print(f"Read-result tokens as share of prompt-token growth: {_pct_of(stats['read_result_tokens_total'], growth_tokens)}")
    print(
        "Read-result tokens as share of total tool-result tokens (self-consistent"
        f" cross-check, both ~{cpt} chars/tok): "
        f"{_pct_of(stats['read_result_tokens_total'], stats['all_tool_result_tokens_total'])}"
    )
    locate_n = stats["locate_call_n"]
    locate_mean = stats["locate_result_tokens_total"] // locate_n if locate_n else 0
    print(
        f"\nlocate-step cost (Grep/Glob calls, the step a targeted read adds): {locate_n:,} calls, "
        f"~{stats['locate_result_tokens_total']:,} tok, mean ~{locate_mean:,} tok/call -- "
        f"any saving quoted against whole-file reads is gross until this is netted against it"
    )

    if per_account is not None:
        print("\n## Per-account breakdown\n")
        for idx, account_stats in enumerate(per_account):
            account_label = f"account-{idx + 1}"
            a_read_total = account_stats["read_total"]
            if a_read_total == 0:
                print(f"  {account_label:10} no Read calls")
                continue
            a_cohort_n = account_stats["cohort_n"]
            a_targeted = a_cohort_n.get(_READ_SCOPE_COHORT_TARGETED, 0)
            a_whole_file = a_cohort_n.get(_READ_SCOPE_COHORT_WHOLE_FILE, 0)
            print(
                f"  {account_label:10} calls={a_read_total:6,}  "
                f"targeted={_pct_of(a_targeted, a_read_total):>6}  whole_file={_pct_of(a_whole_file, a_read_total):>6}"
            )


def cmd_cost_trend(args: argparse.Namespace) -> None:
    """CLI entry point for the cost-trend subcommand.

    Reads the wall-clock date exactly once, here, then delegates to
    _cost_trend_report, which takes `today` as an explicit parameter — the
    same split _cost_report uses so the trailing week's "(partial)" label
    doesn't depend on a live clock read inside a function under test.
    """
    _cost_trend_report(args, datetime.now(UTC).date())


def _compute_cost_trend_data(session_iter) -> tuple[dict[str, dict[str, float]], int, int]:
    """Per-ISO-week $/opus-share/>=200k-context-share accumulation behind
    both cost-trend's own report and cost-ledger's per-week row, extracted
    so the two share one scan instead of two implementations kept in sync
    by hand.

    Returns (week_str -> {"total": $, "opus": $, "context_over": $,
    "context_class_dollars": $}, unpriced_turns, unpriced_tokens). A week
    with zero priced turns is simply absent as a key, not present with
    zeros — cost-ledger's own "no row for this week yet" gap detection
    relies on that absence.

    context_over and context_class_dollars are two distinct metrics, not
    two names for one: context_over is the dollar share of turns whose
    context crossed the >=200k bucket (_context_bucket, what cost-trend's
    own printed "Context%" column has always been); context_class_dollars
    is the dollar share attributable to context-class token usage
    (cache_read + both cache_write tiers, i.e. every _price_turn class
    except output) regardless of bucket — GH-554 F1's "context is ~88% of
    the bill" thesis. cost-ledger is the only consumer of
    context_class_dollars; _cost_trend_report's printed table is unchanged.
    """
    data: dict[str, dict[str, float]] = defaultdict(
        lambda: {"total": 0.0, "opus": 0.0, "context_over": 0.0, "context_class_dollars": 0.0}
    )
    unpriced_turns = 0
    unpriced_tokens = 0

    for _jsonl, records in session_iter:
        records = _dedup_turns_by_request_id(records)
        for rec in records:
            if rec.get("type") != "assistant":
                continue
            msg = rec.get("message") or {}
            usage = msg.get("usage")
            if not usage:
                continue
            rec_ts = _parse_ts(rec.get("timestamp"))
            if rec_ts is None:
                continue
            model = msg.get("model", "")
            dollars_by_class, context_at_turn, turn_unpriced_tokens = _price_turn(model, usage)
            if dollars_by_class is None:
                unpriced_turns += 1
                unpriced_tokens += turn_unpriced_tokens
                continue
            turn_total = sum(dollars_by_class.values())
            iso = datetime.fromtimestamp(rec_ts, tz=UTC).isocalendar()
            week_str = f"{iso.year}-W{iso.week:02d}"
            d = data[week_str]
            d["total"] += turn_total
            if _fam(model) == "opus":
                d["opus"] += turn_total
            if _context_bucket(context_at_turn) == _CONTEXT_BUCKET_OVER:
                d["context_over"] += turn_total
            d["context_class_dollars"] += (
                dollars_by_class["cache_read"] + dollars_by_class["cache_write_1h"] + dollars_by_class["cache_write_5m"]
            )

    return dict(data), unpriced_turns, unpriced_tokens


def _cost_trend_report(args: argparse.Namespace, today: date) -> None:
    """Per-ISO-week dollar spend, Opus-family share, and >=200k context-bucket share.

    Reuses _price_turn's per-turn pricing (same as cost) and cmd_handoff_ratio's
    ISO-week bucketing. Sidechain turns are included (include_subagents=True)
    for the same reason _cost_report includes them — most dispatched spend
    would otherwise be silently excluded. The most recent bucket is very
    likely a partial week; it is labeled "(partial)" rather than presented as
    a complete week's total, since a corpus only a few weeks deep would
    otherwise misread a partial trailing week as a real week-over-week drop.
    Turns whose model ID has no _MODEL_BASE_INPUT_RATES entry are excluded
    from every week's totals and counted corpus-wide (mirrors
    cmd_audit_routing's unpriced-turns convention) so they don't silently
    vanish from the reported spend.

    Roots resolve via _resolve_cost_roots (cost's own --config-dir contract),
    not the generic _resolve_scan_roots -- this is the one funnel that
    understands a repeatable --config-dir/extra_config_dirs.
    """
    redact: bool = not bool(getattr(args, "no_redact", False))
    roots = _resolve_cost_roots(args, subcommand="cost-trend")
    session_iter, scope_label = _resolve_project_scope(args, "cost-trend", include_subagents=True, roots=roots)

    # Mirrors cost's/context-distribution's own per-root scan diagnostic --
    # without it, a stale or misconfigured --config-dir root silently
    # contributes nothing to the weekly trend with no signal.
    glob = _projects_glob(args)
    this_repo_slugs = getattr(args, "_this_repo_slugs", None) if args.this_repo else None
    redact_ordinals: dict[Path, int] = _redaction_ordinals(roots)
    for root in roots:
        root_label = f"account-{redact_ordinals[root.resolve()]}" if redact else str(root.parent)
        try:
            scanned, skipped = _scan_root_transcripts(root, glob, slugs=this_repo_slugs)
        except PermissionError as exc:
            # str(exc) on a PermissionError typically embeds the offending
            # path — suppressed under default redaction so a permission
            # failure can't leak the raw config-dir path it's reporting on.
            detail = str(exc) if not redact else "permission denied"
            print(f"cost-trend: {root_label}: cannot scan ({detail}) — treating as 0 transcripts", file=sys.stderr)
            scanned, skipped = 0, 0
        print(f"cost-trend: {root_label}: scanned {scanned:,} transcripts, {skipped:,} skipped (unreadable)")
        if scanned == 0:
            print(
                f"WARNING: cost-trend: {root_label}: no transcripts found for this scope"
                " — check the config dir and --projects/--this-repo filter."
            )

    _print_resolved_scope("cost-trend", scope_label, roots)

    data, unpriced_turns, unpriced_tokens = _compute_cost_trend_data(session_iter)

    if not data:
        print("No priced turns found.")
        if unpriced_turns:
            print(f"  ({unpriced_turns:,} unpriced turns / {unpriced_tokens:,} tokens excluded from priced spend)")
        return

    current_iso = today.isocalendar()
    current_week_str = f"{current_iso.year}-W{current_iso.week:02d}"

    header = f"{'Week':<20} {'$':>14} {'Context%':>9} {'Opus%':>7}"
    print(header)
    print("-" * len(header))
    for week_str in sorted(data):
        d = data[week_str]
        label = f"{week_str} (partial)" if week_str == current_week_str else week_str
        print(
            f"{label:<20} {d['total']:>14,.2f} "
            f"{_pct_of(d['context_over'], d['total']):>9} {_pct_of(d['opus'], d['total']):>7}"
        )
    if unpriced_turns:
        print(f"\n  ({unpriced_turns:,} unpriced turns / {unpriced_tokens:,} tokens excluded from priced spend)")


# --- cost-ledger: local per-week cost/efficiency ledger read/append -------
#
# See docs/cost-ledger.md for the schema and .claude/plans/cost-trend-ledger.md
# for the design rationale (why each column exists, what got dropped, and the
# error-path contracts implemented below).

_COST_LEDGER_COLUMNS = (
    "week", "machine", "rates", "usd", "context_pct", "opus_pct",
    "ge200k_pct", "denials", "reviewer_gap_pp", "note",
)
_COST_LEDGER_HEADER_LINE = "| " + " | ".join(_COST_LEDGER_COLUMNS) + " |"
_COST_LEDGER_SEPARATOR_LINE = "|" + "|".join(["---"] * len(_COST_LEDGER_COLUMNS)) + "|"
# Real git conflict markers are exactly these 7-character prefixes (each
# followed by a ref name on <<<<<<</>>>>>>> or nothing on =======).
_COST_LEDGER_CONFLICT_MARKERS = ("<<<<<<<", "=======", ">>>>>>>")
_COST_LEDGER_ISO_WEEK_RE = re.compile(r"^(\d{4})-W(\d{2})$")
# Short, lowercase-alphanumeric, no spaces or unicode -- wide enough for
# "m1"/"laptop2", narrow enough that a hostname or username can't be
# expressed in it (see docs/cost-ledger.md). \Z (not $) so a trailing
# newline doesn't slip past the anchor.
_MACHINE_LABEL_RE = re.compile(r"^[a-z0-9]{1,8}\Z")
# A note is a rendered markdown table cell (docs/cost-ledger.md, viewed on
# GitHub) and a terminal string (cost-ledger's own read mode) -- printable
# ASCII only blocks both raw control/escape bytes and non-ASCII lookalikes.
_COST_LEDGER_NOTE_MARKDOWN_LINK_RE = re.compile(r"!?\[[^\]]*\]\([^)]*\)")
# Local-lock convenience bound, not a protocol-grounded value -- this guards
# an interactive CLI's own read-check-write window against another local
# --record, not a network call, so no vendor timeout spec applies. 30s
# comfortably exceeds a single --record's read-check-write step (parse,
# upsert, temp-write, atomic rename) while still surfacing a wedged or
# long-running concurrent recorder within one interactive command.
_COST_LEDGER_LOCK_TIMEOUT_S = 30.0
_COST_LEDGER_LOCK_POLL_INTERVAL_S = 0.1


class _CostLedgerParseError(Exception):
    """Raised by _parse_cost_ledger_file_text on any malformed ledger content
    -- the canonical parser fails loud rather than mis-parsing a hand-edited
    or merge-conflicted row."""


def _cost_ledger_path() -> Path:
    """Return the active cost-ledger file path: $COST_LEDGER_PATH if set
    (must be absolute), else config_dir() / "cost-ledger.md"."""
    override = os.environ.get("COST_LEDGER_PATH")
    if override:
        path = Path(override)
        if not path.is_absolute():
            raise ValueError(f"COST_LEDGER_PATH must be an absolute path, got: {override!r}")
        return path
    return config_dir() / "cost-ledger.md"


def _parse_cost_ledger_iso_week(week_str: str) -> None:
    """Raise _CostLedgerParseError unless week_str is a genuine ISO week
    label -- both the YYYY-Www shape and a week number the given year
    actually has (year 2025 has no W53, for instance)."""
    m = _COST_LEDGER_ISO_WEEK_RE.match(week_str)
    if not m:
        raise _CostLedgerParseError(f"malformed week label {week_str!r} (expected YYYY-Www)")
    year, week = int(m.group(1)), int(m.group(2))
    try:
        date.fromisocalendar(year, week, 1)
    except ValueError as exc:
        raise _CostLedgerParseError(f"malformed week label {week_str!r}: {exc}") from None


def _parse_cost_ledger_pct_cell(label: str, cell: str) -> float:
    """Parse a "N.N%"-shaped percentage cell, raising _CostLedgerParseError
    on anything else (missing '%', a non-numeric value in front of it, or a
    non-finite float -- float() itself accepts "nan"/"inf"/"-infinity" with
    no error, which a percentage column must reject the same as any other
    malformed cell)."""
    if not cell.endswith("%"):
        raise _CostLedgerParseError(f"malformed {label} {cell!r} (expected a trailing '%')")
    try:
        value = float(cell[:-1])
    except ValueError:
        raise _CostLedgerParseError(f"non-numeric {label} {cell!r}") from None
    if math.isnan(value) or math.isinf(value):
        raise _CostLedgerParseError(f"non-finite {label} {cell!r}")
    return value


def _cost_ledger_note_violation(note: str) -> str | None:
    """Return a human-readable reason `note` is rejected, or None if it's
    valid. Shared between --record-time validation and the canonical row
    parser, so a hand-edited or PR-introduced row is held to the same
    contract as one written by --record: a raw '|' or newline would corrupt
    the table's row format, a non-printable-ASCII byte (e.g. an ANSI/OSC
    terminal escape sequence) would be interpolated unescaped into
    cost-ledger's terminal read-mode output, and markdown link/image syntax
    would beacon an external server on every GitHub render of
    docs/cost-ledger.md.
    """
    if "|" in note or "\n" in note or "\r" in note:
        return "must not contain '|' or a newline -- either would corrupt the table's row format"
    if not all(32 <= ord(c) <= 126 for c in note):
        return "must contain only printable ASCII -- control and terminal-escape characters are rejected"
    if _COST_LEDGER_NOTE_MARKDOWN_LINK_RE.search(note):
        return "must not contain markdown link/image syntax ('[text](url)' or '![alt](url)')"
    return None


def _parse_cost_ledger_row_cells(cells: list[str], line_no: int) -> dict:
    """Validate and coerce one already-split, already-stripped data row's
    cells into a typed row dict. Raises _CostLedgerParseError naming the
    offending line on any field that doesn't match its column's contract.
    """
    if len(cells) != len(_COST_LEDGER_COLUMNS):
        raise _CostLedgerParseError(
            f"line {line_no}: expected {len(_COST_LEDGER_COLUMNS)} columns, got {len(cells)}"
            " (a stray '|' inside a cell produces this same error)"
        )
    week, machine, rates, usd_s, context_s, opus_s, ge200k_s, denials_s, gap_s, note = cells

    note_violation = _cost_ledger_note_violation(note)
    if note_violation is not None:
        raise _CostLedgerParseError(f"line {line_no}: malformed note {note!r} ({note_violation})")

    try:
        _parse_cost_ledger_iso_week(week)
    except _CostLedgerParseError as exc:
        raise _CostLedgerParseError(f"line {line_no}: {exc}") from None

    if not _MACHINE_LABEL_RE.match(machine):
        raise _CostLedgerParseError(f"line {line_no}: malformed machine label {machine!r}")

    try:
        datetime.strptime(rates, "%Y-%m-%d")
    except ValueError:
        raise _CostLedgerParseError(f"line {line_no}: malformed rates date {rates!r}") from None

    try:
        usd = float(usd_s)
    except ValueError:
        raise _CostLedgerParseError(f"line {line_no}: non-numeric usd {usd_s!r}") from None
    if math.isnan(usd) or math.isinf(usd):
        raise _CostLedgerParseError(f"line {line_no}: non-finite usd {usd_s!r}")

    try:
        context_pct = _parse_cost_ledger_pct_cell("context_pct", context_s)
        opus_pct = _parse_cost_ledger_pct_cell("opus_pct", opus_s)
        ge200k_pct = _parse_cost_ledger_pct_cell("ge200k_pct", ge200k_s)
    except _CostLedgerParseError as exc:
        raise _CostLedgerParseError(f"line {line_no}: {exc}") from None

    try:
        denials = int(denials_s)
    except ValueError:
        raise _CostLedgerParseError(f"line {line_no}: non-numeric denials {denials_s!r}") from None

    reviewer_gap_pp: float | None = None
    if gap_s:
        if not gap_s.endswith("pp"):
            raise _CostLedgerParseError(
                f"line {line_no}: malformed reviewer_gap_pp {gap_s!r} (expected a trailing 'pp')"
            )
        try:
            reviewer_gap_pp = float(gap_s[:-2])
        except ValueError:
            raise _CostLedgerParseError(f"line {line_no}: non-numeric reviewer_gap_pp {gap_s!r}") from None
        if math.isnan(reviewer_gap_pp) or math.isinf(reviewer_gap_pp):
            raise _CostLedgerParseError(f"line {line_no}: non-finite reviewer_gap_pp {gap_s!r}")

    return {
        "week": week, "machine": machine, "rates": rates, "usd": usd,
        "context_pct": context_pct, "opus_pct": opus_pct, "ge200k_pct": ge200k_pct,
        "denials": denials, "reviewer_gap_pp": reviewer_gap_pp, "note": note,
    }


def _parse_cost_ledger_file_text(text: str) -> tuple[str, list[dict]]:
    """Canonical parser for docs/cost-ledger.md's full content.

    Returns (preamble, rows): preamble is everything up to and including the
    table's header and separator rows, verbatim, so a re-render (preamble +
    one rendered line per row) is a byte-identical round trip for an
    already-canonical file. Fails loud (_CostLedgerParseError) on an
    unresolved git merge-conflict marker anywhere in the file, a missing or
    malformed header/separator pair, a data row not wrapped in '|...|', a
    wrong column count (a stray '|' inside a cell surfaces this same error),
    a non-ISO week label, or a non-numeric numeric/percentage cell -- never
    silently misparses a malformed row.
    """
    lines = text.splitlines()
    for marker in _COST_LEDGER_CONFLICT_MARKERS:
        for line_no, line in enumerate(lines, start=1):
            if line.startswith(marker):
                raise _CostLedgerParseError(f"line {line_no}: unresolved merge-conflict marker {marker!r}")

    header_idx = None
    for i, line in enumerate(lines):
        if line.strip() == _COST_LEDGER_HEADER_LINE:
            header_idx = i
            break
    if header_idx is None:
        raise _CostLedgerParseError("could not find the cost-ledger table header row")
    if header_idx + 1 >= len(lines) or lines[header_idx + 1].strip() != _COST_LEDGER_SEPARATOR_LINE:
        raise _CostLedgerParseError("cost-ledger table header is not followed by its separator row")

    rows: list[dict] = []
    for line_no, line in enumerate(lines[header_idx + 2:], start=header_idx + 3):
        if not line.strip():
            continue
        stripped = line.strip()
        if not (stripped.startswith("|") and stripped.endswith("|")):
            raise _CostLedgerParseError(f"line {line_no}: malformed table row {line!r}")
        cells = [c.strip() for c in stripped[1:-1].split("|")]
        rows.append(_parse_cost_ledger_row_cells(cells, line_no))

    preamble = "\n".join(lines[: header_idx + 2]) + "\n"
    return preamble, rows


def _format_cost_ledger_row(row: dict) -> str:
    """Render one row dict as its markdown table line -- the exact inverse
    of _parse_cost_ledger_row_cells."""
    gap = row["reviewer_gap_pp"]
    gap_s = "" if gap is None else f"{gap:+.1f}pp"
    cells = [
        row["week"], row["machine"], row["rates"],
        f"{row['usd']:.2f}", f"{row['context_pct']:.1f}%", f"{row['opus_pct']:.1f}%",
        f"{row['ge200k_pct']:.1f}%", str(row["denials"]), gap_s, row["note"],
    ]
    return "| " + " | ".join(cells) + " |"


def _upsert_cost_ledger_row(existing_rows: list[dict], new_row: dict, force: bool) -> list[dict]:
    """Insert new_row into existing_rows, keyed by (week, machine).

    Refuses (raises ValueError) when a row for that key already exists and
    force is False -- a week's numbers change as the week fills, and
    silently rewriting history is how a ledger stops being one. With force,
    replaces that row in place; every other row's order and content is
    untouched.
    """
    key = (new_row["week"], new_row["machine"])
    for i, row in enumerate(existing_rows):
        if (row["week"], row["machine"]) == key:
            if not force:
                raise ValueError(
                    f"a row for week={new_row['week']} machine={new_row['machine']} already exists"
                    " -- pass --force to overwrite it"
                )
            return [*existing_rows[:i], new_row, *existing_rows[i + 1:]]
    return [*existing_rows, new_row]


def _write_cost_ledger_file(ledger_path: Path, preamble: str, rows: list[dict]) -> None:
    """Crash-safe write: render to a temp file in the ledger's own directory,
    read it back to confirm the write landed intact and parses on the
    canonical parser, then atomically replace the ledger file -- a killed
    process leaves either the old file intact or an orphaned temp file,
    never a half-written row the next run's duplicate check could misread.

    The read-back is a byte-equality check against the text just written,
    not a round trip through row dicts: _format_cost_ledger_row rounds usd
    to cents and percentages to one decimal, so a row's raw float and its
    formatted-then-reparsed value legitimately differ -- that is expected
    precision loss, not a serialization bug, and comparing rows would
    refuse almost every real (non-round-number) row.

    The temp file is chmod'd to the existing ledger file's permission bits
    before the replace -- tempfile.mkstemp creates it 0600, and os.replace
    swaps that mode in along with the content, silently downgrading the
    ledger file's existing permissions on every --record otherwise. If
    ledger_path does not exist yet (this --record is the first ever run
    against it), the chmod is skipped and tempfile.mkstemp's own 0600
    default is left in place.
    """
    new_text = preamble + "\n".join(_format_cost_ledger_row(r) for r in rows) + ("\n" if rows else "")
    fd, tmp_name = tempfile.mkstemp(dir=str(ledger_path.parent), prefix=".cost-ledger-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(new_text)
        written_text = Path(tmp_name).read_text()
        if written_text != new_text:
            raise _CostLedgerParseError("write verification mismatch -- refusing to publish")
        _parse_cost_ledger_file_text(written_text)  # fails loud on the canonical parser before publishing
        if ledger_path.exists():
            os.chmod(tmp_name, stat.S_IMODE(ledger_path.stat().st_mode))
        os.replace(tmp_name, ledger_path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def _reviewer_gap_pp(agg2: dict[tuple[str, str], dict[str, int]]) -> float | None:
    """Percentage-point gap between the findings-found and zero-finding
    cited-path edit rates, aggregated across every reviewer agent type --
    cost-ledger's reviewer_gap_pp column. None (left empty in the row) when
    either side's Active denominator is zero, rather than dividing by zero
    or silently substituting 0%.
    """
    findings_active = sum(
        v["active"] for (_stype, bucket), v in agg2.items() if bucket == _REVIEWER_VERDICT_FINDINGS_FOUND
    )
    findings_edited = sum(
        v["edited"] for (_stype, bucket), v in agg2.items() if bucket == _REVIEWER_VERDICT_FINDINGS_FOUND
    )
    zero_active = sum(
        v["active"] for (_stype, bucket), v in agg2.items() if bucket == _REVIEWER_VERDICT_ZERO_FINDING
    )
    zero_edited = sum(
        v["edited"] for (_stype, bucket), v in agg2.items() if bucket == _REVIEWER_VERDICT_ZERO_FINDING
    )
    if findings_active == 0 or zero_active == 0:
        return None
    return 100 * (findings_edited / findings_active - zero_edited / zero_active)


_COST_LEDGER_READ_HEADER = (
    f"{'Week':<10} {'Machine':<9} {'Rates':<11} {'$':>12} {'Context%':>9} "
    f"{'Opus%':>7} {'>=200k%':>8} {'Denials':>8} {'GapPP':>11}  Note"
)


def _format_cost_ledger_read_row(row: dict) -> str:
    """Render one row dict as a fixed-width terminal line for read mode --
    distinct from _format_cost_ledger_row, which renders the file's own
    markdown-pipe format. Empty reviewer_gap_pp prints as the literal token
    "unmeasured" (never a blank cell) so every column stays a single
    whitespace-delimited token."""
    gap = row["reviewer_gap_pp"]
    gap_s = "unmeasured" if gap is None else f"{gap:+.1f}pp"
    note = row["note"] or "-"
    return (
        f"{row['week']:<10} {row['machine']:<9} {row['rates']:<11} {row['usd']:>12,.2f} "
        f"{row['context_pct']:>8.1f}% {row['opus_pct']:>6.1f}% {row['ge200k_pct']:>7.1f}% "
        f"{row['denials']:>8} {gap_s:>11}  {note}"
    )


def _print_cost_ledger_read(existing_rows: list[dict], args: argparse.Namespace, roots: Sequence[Path]) -> None:
    """Read-mode output: every existing ledger row, then any ISO week present
    in the live corpus that no row (for any machine) has captured yet -- the
    gap between "recorded" and "still recoverable" this ledger exists to close.
    """
    session_iter, scope_label = _resolve_project_scope(args, "cost-ledger", include_subagents=True, roots=roots)
    _print_resolved_scope("cost-ledger", scope_label, roots)

    if not existing_rows:
        print("\nNo rows recorded yet.")
    else:
        print()
        print(_COST_LEDGER_READ_HEADER)
        print("-" * len(_COST_LEDGER_READ_HEADER))
        for row in existing_rows:
            print(_format_cost_ledger_read_row(row))

    cost_weeks, _unpriced_turns, _unpriced_tokens = _compute_cost_trend_data(session_iter)
    recorded_weeks = {row["week"] for row in existing_rows}
    missing_weeks = sorted(w for w in cost_weeks if w not in recorded_weeks)
    if missing_weeks:
        print("\nWeeks present in the live corpus with no ledger row yet:")
        for week_str in missing_weeks:
            print(f"  {week_str}")


def _acquire_cost_ledger_lock(lock_f) -> None:
    """Acquire an exclusive, non-blocking lock on lock_f, retrying at
    _COST_LEDGER_LOCK_POLL_INTERVAL_S intervals until
    _COST_LEDGER_LOCK_TIMEOUT_S elapses. Exits non-zero with a clear message
    rather than blocking indefinitely (fcntl.flock's plain LOCK_EX) on a
    wedged or long-running concurrent --record.
    """
    deadline = time.monotonic() + _COST_LEDGER_LOCK_TIMEOUT_S
    while True:
        try:
            fcntl.flock(lock_f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EAGAIN):
                raise
            if time.monotonic() >= deadline:
                print(
                    "cost-ledger: another cost-ledger --record appears to be running"
                    " (lock held on the ledger's own .lock sibling file -- see"
                    " _cost_ledger_path()) -- timed out after"
                    f" {_COST_LEDGER_LOCK_TIMEOUT_S:.0f}s",
                    file=sys.stderr,
                )
                sys.exit(1)
            time.sleep(_COST_LEDGER_LOCK_POLL_INTERVAL_S)


def cmd_cost_ledger(args: argparse.Namespace) -> None:
    """CLI entry point for the cost-ledger subcommand.

    Reads the wall-clock date exactly once, here, then delegates to
    _cost_ledger_report, which takes `today` as an explicit parameter — the
    same split cmd_cost/cmd_cost_trend use so week-boundary logic is
    deterministic under test.
    """
    roots = _resolve_scan_roots(args)
    _cost_ledger_report(args, datetime.now(UTC).date(), roots)


def _default_cost_ledger_preamble() -> str:
    """Preamble for a ledger file --record creates fresh at a not-yet-existing
    path -- header/separator are byte-identical to the module constants so
    _parse_cost_ledger_file_text round-trips a freshly created file the same
    as an already-canonical one."""
    lines = [
        "# cost-ledger",
        "",
        "Weekly cost/efficiency snapshots recorded by `cost-ledger --record`.",
        _COST_LEDGER_HEADER_LINE,
        _COST_LEDGER_SEPARATOR_LINE,
    ]
    return "\n".join(lines) + "\n"


def _cost_ledger_report(args: argparse.Namespace, today: date, roots: Sequence[Path] | None = None) -> None:
    """Read (default) or append (--record) one row of the cost ledger at
    _cost_ledger_path().

    --record's row reuses _compute_cost_trend_data for usd/context_pct/
    opus_pct/ge200k_pct, and windows _compute_deny_summary_data/
    _compute_reviewer_yield_data to the current ISO week's Monday-through-
    next-Monday UTC boundary for denials/reviewer_gap_pp — see
    docs/cost-ledger.md for why those two are per-week rather than
    corpus-lifetime figures. The corpus scan that computes the row runs
    unlocked; only the final read-check-write step (re-read the ledger,
    check for an existing (week, machine) row, write) holds an exclusive
    lock on a sibling .lock file (never the ledger file itself, so lock
    identity survives the atomic replace in _write_cost_ledger_file), so two
    racing recorders can't both pass the duplicate-row check.
    _acquire_cost_ledger_lock bounds the wait rather than blocking
    indefinitely on a wedged concurrent recorder.
    """
    record: bool = bool(getattr(args, "record", False))
    force: bool = bool(getattr(args, "force", False))
    machine_label: str | None = getattr(args, "machine_label", None) or None
    note: str = getattr(args, "note", None) or ""

    try:
        ledger_path = _cost_ledger_path()
    except ValueError as exc:
        # COST_LEDGER_PATH is new, operator-set, and easy to mistype relative
        # -- route it through this module's standard stderr+exit convention
        # rather than letting a raw traceback reach the terminal.
        print(f"cost-ledger: {exc}", file=sys.stderr)
        sys.exit(1)

    if roots is None:
        roots = _resolve_scan_roots(args)

    if not record:
        if not ledger_path.exists():
            # Omits the resolved path entirely -- it's home-rooted, and this
            # repo's own redaction convention treats home-rooted paths as
            # always-sensitive (command output here routinely gets pasted
            # into public issues).
            print(
                "cost-ledger: no ledger recorded here yet -- --record has never"
                " run against this path (or COST_LEDGER_PATH/CLAUDE_CONFIG_DIR"
                " resolves somewhere unexpected); see docs/cost-ledger.md",
                file=sys.stderr,
            )
            sys.exit(1)
        try:
            _preamble, existing_rows = _parse_cost_ledger_file_text(ledger_path.read_text())
        except _CostLedgerParseError as exc:
            print(f"cost-ledger: {exc}", file=sys.stderr)
            sys.exit(1)
        _print_cost_ledger_read(existing_rows, args, roots)
        return

    sentinel_path = config_dir() / ".cost-ledger-enabled"
    if not sentinel_path.exists():
        # Hardcodes the conventional ~/.claude path rather than sentinel_path
        # itself -- same don't-print-a-resolved-home-rooted-path discipline
        # as the ledger-file-missing message above, applied to a fixed
        # docstring instead of an f-string since CLAUDE_CONFIG_DIR overrides
        # are rare enough that the canonical hint reads clearer.
        print(
            "cost-ledger: --record requires the opt-in sentinel ~/.claude/.cost-ledger-enabled"
            " -- see docs/cost-ledger.md",
            file=sys.stderr,
        )
        sys.exit(1)

    if not machine_label:
        print("cost-ledger: --record requires --machine-label", file=sys.stderr)
        sys.exit(1)
    if not _MACHINE_LABEL_RE.match(machine_label):
        print(f"cost-ledger: --machine-label {machine_label!r} must match ^[a-z0-9]{{1,8}}$", file=sys.stderr)
        sys.exit(1)
    if len(roots) > 1:
        # --record writes to a single resolved ledger path; unioning multiple
        # declared accounts into that one write risks silently committing one
        # account's figures under another's (or a shared) destination, so
        # it's refused outright -- non-record reads still return the union.
        print(
            "cost-ledger: --record is refused when more than one root is in"
            " scope; scope to a single account (--config-dir, or a roots"
            " file declaring only this account) or drop --record",
            file=sys.stderr,
        )
        sys.exit(2)
    # Rejection names the rule, never the compared hostname value -- echoing
    # it would persist recon-value data into the session transcript, the same
    # discipline deny-private-project-refs.sh applies to its own matches.
    # Covers the POSIX hostname only, not macOS's separate ComputerName
    # (`scutil --get ComputerName`) -- see docs/cost-ledger.md.
    if machine_label.lower() == socket.gethostname().lower():
        print(
            "cost-ledger: --machine-label must not equal this machine's hostname"
            " -- publishing a hostname risks deanonymizing this repo's corpus; choose an opaque label instead",
            file=sys.stderr,
        )
        sys.exit(1)
    note_violation = _cost_ledger_note_violation(note)
    if note_violation is not None:
        print(f"cost-ledger: --note {note_violation}", file=sys.stderr)
        sys.exit(1)

    iso = today.isocalendar()
    week_str = f"{iso.year}-W{iso.week:02d}"
    monday = date.fromisocalendar(iso.year, iso.week, 1)
    week_start_ts = datetime(monday.year, monday.month, monday.day, tzinfo=UTC).timestamp()
    week_end_ts = week_start_ts + 7 * 86400

    cost_session_iter, scope_label = _resolve_project_scope(args, "cost-ledger", include_subagents=True, roots=roots)
    deny_session_iter, _scope_label2 = _resolve_project_scope(args, "cost-ledger", roots=roots)
    reviewer_session_iter, _scope_label3 = _resolve_project_scope(args, "cost-ledger", roots=roots)
    _print_resolved_scope("cost-ledger", scope_label, roots)

    cost_weeks, _unpriced_turns, _unpriced_tokens = _compute_cost_trend_data(cost_session_iter)

    if week_str not in cost_weeks:
        print(
            f"cost-ledger: no priced turns found for the current week ({week_str});"
            " refusing to record a blank/zero row",
            file=sys.stderr,
        )
        sys.exit(1)

    max_observed_week = max(cost_weeks)
    if max_observed_week != week_str:
        print(
            f"cost-ledger: clock skew detected -- the corpus's most recent activity is dated"
            f" {max_observed_week}, but this machine's clock resolves the current week as"
            f" {week_str}; refusing to record under a possibly-wrong week label",
            file=sys.stderr,
        )
        sys.exit(1)

    week_data = cost_weeks[week_str]
    # Two distinct metrics: context_pct is the context-class (cache_read +
    # both cache_write tiers) dollar share of the week's total, GH-554 F1's
    # "context is ~88% of the bill" thesis; ge200k_pct is cost-trend's own
    # existing >=200k-context-bucket dollar share (_context_bucket) -- a
    # different corpus slice, not a second name for the same number.
    context_share = _pct_value(week_data["context_class_dollars"], week_data["total"])
    ge200k_share = _pct_value(week_data["context_over"], week_data["total"])
    opus_share = _pct_value(week_data["opus"], week_data["total"])

    deny_data = _compute_deny_summary_data(deny_session_iter, since_ts=week_start_ts, until_ts=week_end_ts)
    denials = sum(deny_data["hook_counts"].values())

    reviewer_data = _compute_reviewer_yield_data(reviewer_session_iter, since_ts=week_start_ts, until_ts=week_end_ts)
    reviewer_gap_pp = _reviewer_gap_pp(reviewer_data["agg2"])

    new_row = {
        "week": week_str,
        "machine": machine_label,
        "rates": _PRICING_FETCH_DATE.isoformat(),
        "usd": week_data["total"],
        "context_pct": context_share,
        "opus_pct": opus_share,
        "ge200k_pct": ge200k_share,
        "denials": denials,
        "reviewer_gap_pp": reviewer_gap_pp,
        "note": note,
    }

    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = ledger_path.with_name(ledger_path.name + ".lock")
    with open(lock_path, "w") as lock_f:
        _acquire_cost_ledger_lock(lock_f)
        try:
            try:
                preamble, existing_rows = _parse_cost_ledger_file_text(ledger_path.read_text())
            except FileNotFoundError:
                # Treated uniformly as "never recorded here" -- doesn't
                # distinguish a dangling symlink or an externally-removed
                # directory from a genuinely fresh path; both are edge cases
                # requiring external tampering, not a normal --record flow.
                preamble, existing_rows = _default_cost_ledger_preamble(), []
            except _CostLedgerParseError as exc:
                print(f"cost-ledger: {exc}", file=sys.stderr)
                sys.exit(1)

            try:
                new_rows = _upsert_cost_ledger_row(existing_rows, new_row, force)
            except ValueError as exc:
                print(f"cost-ledger: {exc}", file=sys.stderr)
                sys.exit(1)

            try:
                _write_cost_ledger_file(ledger_path, preamble, new_rows)
            except _CostLedgerParseError as exc:
                print(f"cost-ledger: {exc}", file=sys.stderr)
                sys.exit(1)
        finally:
            fcntl.flock(lock_f, fcntl.LOCK_UN)

    print(f"cost-ledger: recorded {week_str} / {machine_label}")


def cmd_handoff_ratio(args: argparse.Namespace) -> None:
    """Per-week handoff-vs-compaction ratio.

    Detects two events per session:
    - handoff: main-thread assistant Skill tool_use where input.skill == "handoff"
    - compaction: type == "system" and subtype == "compact_boundary"
      (the record Claude Code writes when auto-compaction fires)

    Output: per-ISO-week table with columns: week, handoffs, compactions, ratio.
    Also reads ~/.claude/.handoff-nudge.log if present and reports schema-drift
    count as a diagnostic footer.
    """
    since_str: str | None = getattr(args, "since", None) or None
    since_ts: float | None = _parse_ts(f"{since_str}T00:00:00Z") if since_str else None
    debug_detector: bool = bool(getattr(args, "debug_detector", False))
    roots = _resolve_scan_roots(args)
    session_iter, scope_label = _resolve_project_scope(args, "handoff-ratio", roots=roots)
    _print_resolved_scope("handoff-ratio", scope_label, roots)

    # week_str -> {"handoffs": int, "compactions": int}
    data: dict[str, dict[str, int]] = defaultdict(lambda: {"handoffs": 0, "compactions": 0})

    for jsonl, records in session_iter:
        session_has_handoff = False
        session_has_compaction = False
        # Use the first timestamp from a main-thread record for week bucketing.
        session_first_ts: float | None = None

        for rec in records:
            rec_ts = _parse_ts(rec.get("timestamp"))
            if since_ts is not None and rec_ts is not None and rec_ts < since_ts:
                continue

            # Track the earliest parseable timestamp for week bucketing.
            if rec_ts is not None and session_first_ts is None:
                session_first_ts = rec_ts

            rec_type = rec.get("type", "")

            # Handoff detection: main-thread assistant Skill tool_use with skill == "handoff".
            if rec_type == "assistant" and not bool(rec.get("isSidechain")):
                for block in ((rec.get("message") or {}).get("content") or []):
                    if (
                        isinstance(block, dict)
                        and block.get("type") == "tool_use"
                        and block.get("name") == "Skill"
                        and (block.get("input") or {}).get("skill") == "handoff"
                    ):
                        session_has_handoff = True

            # Compaction detection: system record with subtype == "compact_boundary".
            # Shape confirmed from transcripts: {"type": "system", "subtype": "compact_boundary", ...}
            if rec_type == "system" and rec.get("subtype") == "compact_boundary":
                session_has_compaction = True
                if debug_detector:
                    print(f"[debug] compaction: {jsonl} ts={rec.get('timestamp')} keys={list(rec.keys())}")

        if not (session_has_handoff or session_has_compaction):
            continue
        if session_first_ts is None:
            continue

        iso = datetime.fromtimestamp(session_first_ts, tz=UTC).isocalendar()
        week_str = f"{iso.year}-W{iso.week:02d}"
        if session_has_handoff:
            data[week_str]["handoffs"] += 1
        if session_has_compaction:
            data[week_str]["compactions"] += 1

    if not data:
        print("No handoff or compaction events found.")
        print("  (0 handoffs, 0 compactions — ratio is undefined)")
        _print_nudge_log_diagnostic()
        return

    print(f"{'Week':<10} {'Handoffs':>9} {'Compactions':>12} {'Ratio':>7}")
    print("-" * 43)
    total_handoffs = total_compactions = 0
    for week_str in sorted(data):
        d = data[week_str]
        h = d["handoffs"]
        c = d["compactions"]
        total_handoffs += h
        total_compactions += c
        denom = h + c
        ratio_str = f"{100.0 * h / denom:.1f}%" if denom else "—"
        print(f"{week_str:<10} {h:>9} {c:>12} {ratio_str:>7}")

    print("-" * 43)
    all_denom = total_handoffs + total_compactions
    all_ratio = f"{100.0 * total_handoffs / all_denom:.1f}%" if all_denom else "—"
    print(f"{'Total':<10} {total_handoffs:>9} {total_compactions:>12} {all_ratio:>7}")
    _print_nudge_log_diagnostic()


_NUDGE_LOG_MAX_READ = 2 * 1024 * 1024  # 2 MB


def _read_bounded_log_lines(log_path: Path) -> list[str]:
    """Read an append-only log file's lines, tail-truncated to the last
    _NUDGE_LOG_MAX_READ bytes so an unbounded log can't be pulled fully into
    memory. Returns [] when the file is absent or unreadable -- shared by
    _print_nudge_log_diagnostic and _parse_nudge_log_entries so both read
    ~/.claude/.handoff-nudge.log the same bounded way."""
    if not log_path.exists():
        return []
    try:
        if log_path.stat().st_size > _NUDGE_LOG_MAX_READ:
            raw = log_path.read_bytes()[-_NUDGE_LOG_MAX_READ:]
            return raw.decode(errors="ignore").splitlines()
        return log_path.read_text().splitlines()
    except OSError:
        return []


def _print_nudge_log_diagnostic() -> None:
    """Read ~/.claude/.handoff-nudge.log and report schema-drift count if present."""
    log_path = config_dir() / ".handoff-nudge.log"
    lines = _read_bounded_log_lines(log_path)
    drift_count = sum(1 for ln in lines if ln.startswith("schema-drift"))
    if drift_count:
        print(f"\nDiagnostic: {drift_count} schema-drift line(s) in {log_path}")
        print("  Schema-drift means the usage block was found but all token fields were 0 or null.")
        print("  The field paths in nudge-handoff-near-context-cap.sh may need updating.")


def _count_read_file_paths(content: list) -> int:
    """Count distinct file_path values across Read tool_use blocks in a turn's content.

    Only Read blocks are counted — Grep/Glob/Bash are intentionally excluded (conservative
    undercount). Returns 0 if there are no Read blocks.
    """
    file_paths: set[str] = set()
    for block in content:
        if (
            isinstance(block, dict)
            and block.get("type") == "tool_use"
            and block.get("name") == "Read"
        ):
            fp = (block.get("input") or {}).get("file_path")
            if fp:
                file_paths.add(fp)
    return len(file_paths)


def _d1_bucket(file_count: int) -> str:
    """Map a Read file-path count to the D1 histogram bucket label."""
    if file_count == 0:
        return "0"
    if file_count == 1:
        return "1"
    if file_count <= 3:
        return "2-3"
    if file_count <= 7:
        return "4-7"
    return "8+"


def _d2_bucket(streak_len: int) -> str:
    """Map a code-read streak length to the D2 histogram bucket label."""
    if streak_len == 1:
        return "1"
    if streak_len == 2:
        return "2"
    if streak_len <= 5:
        return "3-5"
    if streak_len <= 10:
        return "6-10"
    return "11+"


_D1_BUCKETS: tuple[str, ...] = ("0", "1", "2-3", "4-7", "8+")
_D2_BUCKETS: tuple[str, ...] = ("1", "2", "3-5", "6-10", "11+")
_D3_CASES: tuple[str, ...] = ("inline-edit", "dispatched", "neither")

# Buckets / cases that satisfy the dispatchable criterion for the summary line
_D1_DISPATCHABLE_BUCKETS: frozenset[str] = frozenset({"2-3", "4-7", "8+"})
_D3_DISPATCHABLE_CASES: frozenset[str] = frozenset({"dispatched", "neither"})


def cmd_audit_routing_shape(args: argparse.Namespace) -> None:
    """Turn-shape distributions for Opus code-read turns: files-Read per turn (D1),
    code-read streak lengths (D2), and read-then-edit ratio (D3).

    Only code-read and code-write turns outside judgment spans are analysed. The
    judgment-span state machine is intentionally duplicated from cmd_audit_routing —
    tests cross-validate the two copies to guard against drift.
    """
    since_ts, since_raw = _parse_since_nd_arg(args, "audit-routing-shape")
    since_label = since_raw or ""

    roots = _resolve_scan_roots(args)
    session_iter, scope_label = _resolve_project_scope(args, "audit-routing-shape", roots=roots)

    # D1: file-count bucket → {turns, out}
    d1_turns: dict[str, int] = {b: 0 for b in _D1_BUCKETS}
    d1_out: dict[str, int] = {b: 0 for b in _D1_BUCKETS}

    # D2: streak-length bucket → {streak_count, out}
    d2_streaks: dict[str, int] = {b: 0 for b in _D2_BUCKETS}
    d2_out: dict[str, int] = {b: 0 for b in _D2_BUCKETS}

    # D3: case → {turns, out}
    d3_turns: dict[str, int] = {c: 0 for c in _D3_CASES}
    d3_out: dict[str, int] = {c: 0 for c in _D3_CASES}

    # D3 cross-tab: (case, d1_bucket) → {turns, out}
    d3_xtab_turns: dict[tuple[str, str], int] = {
        (case, bkt): 0 for case in _D3_CASES for bkt in _D1_BUCKETS
    }
    d3_xtab_out: dict[tuple[str, str], int] = {
        (case, bkt): 0 for case in _D3_CASES for bkt in _D1_BUCKETS
    }

    # Per-turn records collected per session. Each entry:
    #   class      — routing class string (or "user" for user-turn separators)
    #   out        — output_tokens; 0 for user-turn separators and non-qualifying Opus turns
    #   d1_bucket  — D1 file-count bucket (empty string for non-code-read turns)
    # code-read turns are qualifying entries that feed D1/D2/D3. All Opus-with-usage turns
    # plus user-turn separators are recorded so D2 streaks and D3 lookahead work correctly.
    # User-turn separators break D2 streaks but are skipped in D3's 3-turn Opus window.

    _print_resolved_scope("audit-routing-shape", scope_label, roots)

    for _jsonl, records in session_iter:
        # One API call = one turn: dedup merges a requestId run's content
        # blocks into one union list, so classification and file-count
        # counting below see every block, while the run's output tokens are
        # attributed once. Mirrors cmd_audit_routing's own dedup call.
        records = _dedup_turns_by_request_id(records)
        session_turns: list[dict] = []

        # Judgment span state machine — duplicated from cmd_audit_routing intentionally.
        in_judgment_span: bool = False
        plan_mode_active: bool = False

        for rec in records:
            rtype = rec.get("type", "")
            msg = rec.get("message") or {}

            if rtype in ("user", "human"):
                in_judgment_span = False
                content_text = _content_text(msg.get("content", ""))
                if "Plan mode is active" in content_text:
                    plan_mode_active = True
                # User turns act as streak breakers for D2 (recorded as spacers; out=0 so
                # D3 lookahead does not count them against the 3-Opus-turn window).
                session_turns.append({"class": "user", "out": 0, "d1_bucket": ""})
                continue

            if rtype != "assistant":
                continue

            model = msg.get("model", "")
            if _fam(model) != "opus":
                # Still check for ExitPlanMode in non-Opus turns
                content = msg.get("content") or []
                for block in content:
                    if (
                        isinstance(block, dict)
                        and block.get("type") == "tool_use"
                        and block.get("name") == "ExitPlanMode"
                    ):
                        plan_mode_active = False
                continue

            usage = msg.get("usage")
            if not usage:
                continue

            if since_ts is not None:
                rec_ts = _parse_ts(rec.get("timestamp"))
                if rec_ts is None or rec_ts < since_ts:
                    continue

            content = msg.get("content") or []
            out_tokens: int = usage.get("output_tokens", 0)

            # Open judgment span before classification (same logic as cmd_audit_routing)
            for block in content:
                if (
                    isinstance(block, dict)
                    and block.get("type") == "tool_use"
                    and block.get("name") == "Skill"
                    and (block.get("input") or {}).get("skill") in AUDIT_JUDGMENT_SKILLS
                ):
                    in_judgment_span = True
                    break

            turn_class = _classify_opus_turn(content, in_judgment_span, plan_mode_active)

            # ExitPlanMode clears plan-mode for next turn
            for block in content:
                if (
                    isinstance(block, dict)
                    and block.get("type") == "tool_use"
                    and block.get("name") == "ExitPlanMode"
                ):
                    plan_mode_active = False
                    break

            # code-read turns outside judgment spans qualify for D1/D2/D3 distributions.
            # code-write turns outside judgment spans are recorded so the D3 inline-edit
            # case can be detected in the lookahead window.
            # All other Opus-with-usage turns are recorded as spacers so D3 lookahead
            # correctly counts them against the 3-turn window.
            if turn_class == "code-read":
                file_count = _count_read_file_paths(content)
                bucket = _d1_bucket(file_count)
                session_turns.append({
                    "class": "code-read",
                    "out": out_tokens,
                    "d1_bucket": bucket,
                })
                d1_turns[bucket] += 1
                d1_out[bucket] += out_tokens
            else:
                session_turns.append({
                    "class": turn_class,
                    "out": out_tokens,
                    "d1_bucket": "",
                })

        # --- D2: streak analysis within this session ---
        # A streak is a maximal consecutive run of code-read turns (no non-code-read
        # Opus turn in between, per the recorded session_turns sequence).
        current_streak_len: int = 0
        current_streak_out: int = 0
        for turn in session_turns:
            if turn["class"] == "code-read":
                current_streak_len += 1
                current_streak_out += turn["out"]
            else:
                if current_streak_len > 0:
                    bkt = _d2_bucket(current_streak_len)
                    d2_streaks[bkt] += 1
                    d2_out[bkt] += current_streak_out
                current_streak_len = 0
                current_streak_out = 0
        # Flush trailing streak
        if current_streak_len > 0:
            bkt = _d2_bucket(current_streak_len)
            d2_streaks[bkt] += 1
            d2_out[bkt] += current_streak_out

        # --- D3: read-then-edit lookahead within this session ---
        # For each code-read turn, look ahead up to 3 Opus turns with usage.
        # User-turn separator entries (class="user", out=0) are skipped in the
        # lookahead count — they are not Opus turns with usage.
        for idx, turn in enumerate(session_turns):
            if turn["class"] != "code-read":
                continue
            lookahead_count = 0
            d3_case = "neither"
            for j in range(idx + 1, len(session_turns)):
                next_turn = session_turns[j]
                if next_turn["class"] == "user":
                    # Not an Opus turn with usage — skip without consuming the 3-turn budget.
                    # A user turn between a code-read and a code-write does not weaken the
                    # causal link; only Opus turns count against the lookahead window.
                    continue
                lookahead_count += 1
                if lookahead_count > 3:
                    break
                if next_turn["class"] == "code-write":
                    d3_case = "inline-edit"
                    break
                if next_turn["class"] == "orchestration":
                    d3_case = "dispatched"
                    break

            d3_turns[d3_case] += 1
            d3_out[d3_case] += turn["out"]
            d3_xtab_turns[(d3_case, turn["d1_bucket"])] += 1
            d3_xtab_out[(d3_case, turn["d1_bucket"])] += turn["out"]

    # --- Dispatchable share summary ---
    # A code-read turn is dispatchable via D1 (file-count bucket 2-3, 4-7, or 8+) or
    # D3 (case dispatched or neither). Their union is computed via the D3 cross-tab.
    # D2 streak data is shown separately above as a complementary view.
    total_code_read_out = sum(d1_out.values())
    d1_dispatch_out = sum(d1_out[b] for b in _D1_DISPATCHABLE_BUCKETS)
    d3_dispatch_out = sum(d3_out[c] for c in _D3_DISPATCHABLE_CASES)
    # Intersection of D1 and D3 dispatchable (via cross-tab)
    d1_and_d3_dispatch_out = sum(
        d3_xtab_out[(c, b)]
        for c in _D3_DISPATCHABLE_CASES
        for b in _D1_DISPATCHABLE_BUCKETS
    )
    union_dispatch_out = d1_dispatch_out + d3_dispatch_out - d1_and_d3_dispatch_out
    dispatch_pct = f"{100 * union_dispatch_out / total_code_read_out:.0f}%" if total_code_read_out else "—"

    # --- Emit output ---
    title_since = f"last {since_label}" if since_label else "all time"
    print(f"\n## Opus code-read turn-shape distributions ({title_since})\n")

    # D1
    print("### D1 — Files Read per turn (code-read turns, outside judgment spans)\n")
    d1_header = f"{'Bucket':<8} {'Turns':>8} {'Output tokens':>15}"
    print(d1_header)
    print("─" * len(d1_header))
    for bkt in _D1_BUCKETS:
        print(f"{bkt:<8} {d1_turns[bkt]:>8,} {d1_out[bkt]:>15,}")

    # D2
    print("\n### D2 — Code-read streak length\n")
    d2_header = f"{'Bucket':<8} {'Streaks':>8} {'Output tokens':>15}"
    print(d2_header)
    print("─" * len(d2_header))
    for bkt in _D2_BUCKETS:
        print(f"{bkt:<8} {d2_streaks[bkt]:>8,} {d2_out[bkt]:>15,}")

    # D3
    print("\n### D3 — Read-then-edit ratio (lookahead up to 3 Opus turns)\n")
    d3_header = f"{'Case':<14} {'Turns':>8} {'Output tokens':>15}"
    print(d3_header)
    print("─" * len(d3_header))
    for case in _D3_CASES:
        print(f"{case:<14} {d3_turns[case]:>8,} {d3_out[case]:>15,}")

    print("\n#### D3 × D1 cross-tab\n")
    d3x_header = f"{'Case':<14} {'D1 bucket':<10} {'Turns':>8} {'Output tokens':>15}"
    print(d3x_header)
    print("─" * len(d3x_header))
    for case in _D3_CASES:
        for bkt in _D1_BUCKETS:
            turns_val = d3_xtab_turns[(case, bkt)]
            out_val = d3_xtab_out[(case, bkt)]
            if turns_val > 0:
                print(f"{case:<14} {bkt:<10} {turns_val:>8,} {out_val:>15,}")

    print(
        f"\nDispatchable share: {dispatch_pct} of code-read output tokens"
        " (D1≥2 OR D3-neither/dispatched; D2-streak≥3 shown separately above)"
    )


def cmd_audit_routing_samples(args: argparse.Namespace) -> None:
    """Emit a random sample of Opus code-read turns with prior-user context and next-turn
    lookahead classification, as a JSON array to stdout.

    Each element provides a verbatim prior user message, the first tool_use block of the
    code-read turn, and the kind of action taken in the next non-user Opus turn. Designed
    for manual curation of which turns should/should not have been delegated.

    The judgment-span state machine is intentionally duplicated from cmd_audit_routing —
    tests cross-validate the two copies to guard against drift.
    """
    since_ts, since_raw = _parse_since_nd_arg(args, "audit-routing-samples")

    sample_n: int = getattr(args, "sample", 30) or 30
    seed: int | None = getattr(args, "seed", None)
    roots = _resolve_scan_roots(args)
    session_iter, scope_label = _resolve_project_scope(args, "audit-routing-samples", roots=roots)
    # stderr, not stdout: stdout is this subcommand's JSON/markdown data stream.
    _print_resolved_scope("audit-routing-samples", scope_label, roots, file=sys.stderr)

    candidates: list[dict] = []

    for jsonl, records in session_iter:
        session_id = jsonl.stem
        # One API call = one turn: dedup merges a requestId run's content
        # blocks into one union list, so classification below sees every
        # block (e.g. the first tool_use block promised by this function's
        # own docstring may land on a later raw record). Mirrors
        # cmd_audit_routing's own dedup call.
        records = _dedup_turns_by_request_id(records)

        # Build per-session records list with kind classification.
        # Judgment span state machine — duplicated from cmd_audit_routing intentionally.
        in_judgment_span: bool = False
        plan_mode_active: bool = False

        session_records: list[dict] = []

        for rec in records:
            rtype = rec.get("type", "")
            msg = rec.get("message") or {}

            if rtype in ("user", "human"):
                in_judgment_span = False
                content_text = _content_text(msg.get("content", ""))
                if "Plan mode is active" in content_text:
                    plan_mode_active = True
                user_text = _content_text(msg.get("content", ""))
                session_records.append({
                    "kind": "user",
                    "content": [],
                    "user_text": user_text,
                })
                continue

            if rtype != "assistant":
                continue

            model = msg.get("model", "")
            content = msg.get("content") or []

            if _fam(model) != "opus":
                # Still update span state from non-Opus assistant turns (ExitPlanMode)
                for block in content:
                    if (
                        isinstance(block, dict)
                        and block.get("type") == "tool_use"
                        and block.get("name") == "ExitPlanMode"
                    ):
                        plan_mode_active = False
                continue

            usage = msg.get("usage")
            if not usage:
                continue

            # Open judgment span before classification (same logic as cmd_audit_routing)
            for block in content:
                if (
                    isinstance(block, dict)
                    and block.get("type") == "tool_use"
                    and block.get("name") == "Skill"
                    and (block.get("input") or {}).get("skill") in AUDIT_JUDGMENT_SKILLS
                ):
                    in_judgment_span = True
                    break

            turn_class = _classify_opus_turn(content, in_judgment_span, plan_mode_active)

            # ExitPlanMode clears plan-mode for next turn
            for block in content:
                if (
                    isinstance(block, dict)
                    and block.get("type") == "tool_use"
                    and block.get("name") == "ExitPlanMode"
                ):
                    plan_mode_active = False
                    break

            session_records.append({
                "kind": turn_class,
                "content": content,
                "user_text": "",
                "rec_ts": rec.get("timestamp"),
            })

        # Scan for code-read entries and collect sample candidates.
        for turn_idx, turn in enumerate(session_records):
            if turn["kind"] != "code-read":
                continue

            # Apply --since filter using record timestamp
            if since_ts is not None:
                rec_ts = _parse_ts(turn.get("rec_ts"))
                if rec_ts is None or rec_ts < since_ts:
                    continue

            # prior_user_message: walk backward to find the nearest user turn.
            prior_user_message = ""
            for i in range(turn_idx - 1, -1, -1):
                if session_records[i]["kind"] == "user":
                    prior_user_message = session_records[i]["user_text"]
                    break

            # assistant_tool_call: first tool_use block in this turn's content.
            assistant_tool_call: dict = {"name": "", "input": {}}
            for block in turn["content"]:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    assistant_tool_call = {
                        "name": block.get("name", ""),
                        "input": block.get("input") or {},
                    }
                    break

            # next_assistant_action and next_turn_excerpt: first non-user entry after this turn.
            next_assistant_action = "other"
            next_turn_excerpt = ""
            for j in range(turn_idx + 1, len(session_records)):
                next_entry = session_records[j]
                if next_entry["kind"] == "user":
                    continue
                # Classify the next non-user entry.
                if next_entry["kind"] == "code-write":
                    next_assistant_action = "edit"
                elif next_entry["kind"] == "orchestration":
                    next_assistant_action = "dispatch"
                elif next_entry["kind"] == "code-read":
                    next_assistant_action = "another-read"
                elif next_entry["kind"] == "other":
                    # "respond-to-user" if text-only (no tool_use blocks)
                    has_tool_use = any(
                        isinstance(b, dict) and b.get("type") == "tool_use"
                        for b in next_entry["content"]
                    )
                    next_assistant_action = "other" if has_tool_use else "respond-to-user"
                else:
                    next_assistant_action = "other"
                next_turn_text = _content_text(next_entry["content"])
                next_turn_excerpt = next_turn_text[:200]
                break

            candidates.append({
                "session_id": session_id,
                "turn_index": turn_idx,
                "prior_user_message": prior_user_message,
                "assistant_tool_call": assistant_tool_call,
                "next_assistant_action": next_assistant_action,
                "next_turn_excerpt": next_turn_excerpt,
                "recent_assistant_text": _recent_assistant_text(session_records, turn_idx, _RECENT_LOOKBACK_N),
                "recent_tool_trail": _recent_tool_trail(session_records, turn_idx, _RECENT_LOOKBACK_N),
            })

    # Apply reproducible sampling.
    rng = random.Random(seed)
    rng.shuffle(candidates)
    candidates = candidates[:sample_n]

    output_format: str = getattr(args, "output_format", "json") or "json"
    if output_format == "md":
        print(_format_samples_as_markdown(
            candidates,
            since_raw=since_raw,
            sample_n=sample_n,
            seed=seed,
        ))
    else:
        print(json.dumps(candidates, indent=2))


# ---------------------------------------------------------------------------
# friction-count
# ---------------------------------------------------------------------------

# All three friction signals are weighted equally (1) in the composite —
# stated explicitly here rather than left implicit in the addition below.
_FRICTION_SIGNAL_WEIGHT = 1


def _friction_denial_events(records: list[dict]) -> int:
    """Count hook-denial events in `records`, deduped by tool_use_id.

    Flat, single-file count reusing hook_denial_key for both denial shapes
    (mirrors cmd_review_trace's detection and seen-id dedup exactly), but
    additionally skips isSidechain records — a friction-count-only filter;
    cmd_review_trace's denial detection is not itself isSidechain-filtered.
    """
    seen_denial_ids: set[str] = set()
    count = 0
    for rec in records:
        if bool(rec.get("isSidechain")):
            continue
        rec_type = rec.get("type", "")
        if rec_type == "attachment":
            denial = hook_denial_key(rec)
            if denial is None:
                continue
            tool_use_id, _ = denial
            if tool_use_id and tool_use_id in seen_denial_ids:
                continue
            if tool_use_id:
                seen_denial_ids.add(tool_use_id)
            count += 1
        elif rec_type == "user":
            for block in ((rec.get("message") or {}).get("content") or []):
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                denial = hook_denial_key(block)
                if denial is None:
                    continue
                tool_use_id, _ = denial
                if tool_use_id and tool_use_id in seen_denial_ids:
                    continue
                if tool_use_id:
                    seen_denial_ids.add(tool_use_id)
                count += 1
    return count


def _friction_failed_test_run_events(records: list[dict]) -> int:
    """Count failed test-run events: a Bash tool_use matching TEST_RUNNER_RE
    paired (by tool_use_id) with a tool_result whose max FAILED_RE count > 0.

    Flat, single-file pairing — re-implements the tool_use_id pairing state
    machine independently of cmd_fail_seq (which is branch-grouped and not
    modified), sharing only the TEST_RUNNER_RE/FAILED_RE constants. Counts
    only failing runs (FAILED_RE count > 0), not every matched run — this is
    cmd_fail_seq's "failing" subtotal, not its total run count. isSidechain
    records are skipped.
    """
    pending: set[str] = set()
    count = 0
    for rec in records:
        if bool(rec.get("isSidechain")):
            continue
        rec_type = rec.get("type", "")
        msg = rec.get("message") or {}
        if rec_type == "assistant":
            for block in (msg.get("content") or []):
                if (
                    isinstance(block, dict)
                    and block.get("type") == "tool_use"
                    and block.get("name") == "Bash"
                ):
                    cmd = (block.get("input") or {}).get("command", "")
                    if TEST_RUNNER_RE.search(cmd):
                        pending.add(block["id"])
        elif rec_type in ("user", "human"):
            content = msg.get("content") or []
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                tid = block.get("tool_use_id", "")
                if block.get("type") == "tool_result" and tid in pending:
                    pending.discard(tid)
                    result_text = _content_text(block.get("content", ""))
                    counts = [int(m) for m in FAILED_RE.findall(result_text)]
                    if counts and max(counts) > 0:
                        count += 1
    return count


def _friction_struggle_turn_events(records: list[dict]) -> int:
    """Count user turns whose lowercased text contains a STRUGGLE_PHRASES entry.

    Flat, single-file count — no branch or model-family attribution.
    isSidechain records are skipped.
    """
    count = 0
    for rec in records:
        if bool(rec.get("isSidechain")):
            continue
        if rec.get("type") not in ("user", "human"):
            continue
        msg = rec.get("message") or {}
        text = _content_text(msg.get("content", "")).lower()
        if any(phrase in text for phrase in STRUGGLE_PHRASES):
            count += 1
    return count


def _friction_signals(records: list[dict]) -> dict[str, int]:
    """Return the per-signal friction breakdown plus the all-1-weighted composite."""
    denials = _friction_denial_events(records)
    failed_test_runs = _friction_failed_test_run_events(records)
    struggle_turns = _friction_struggle_turn_events(records)
    composite = (
        _FRICTION_SIGNAL_WEIGHT * denials
        + _FRICTION_SIGNAL_WEIGHT * failed_test_runs
        + _FRICTION_SIGNAL_WEIGHT * struggle_turns
    )
    signals = {
        "denials": denials,
        "failed_test_runs": failed_test_runs,
        "struggle_turns": struggle_turns,
        "composite": composite,
    }
    # Pinned invariant: composite must equal the sum of the three signals.
    # An explicit raise (not `assert`) so the check survives python -O /
    # PYTHONOPTIMIZE, which strips bare asserts.
    if signals["composite"] != denials + failed_test_runs + struggle_turns:
        raise AssertionError(
            "friction-count: composite must equal the sum of denials + failed_test_runs + struggle_turns"
        )
    return signals


# friction-count --checkpoint: incremental byte-offset scan. Avoids reparsing
# the whole transcript on every hook fire — see the hook's own comment for
# why this exists. The checkpoint is a small JSON blob (offset + the three
# running per-signal totals); no cross-call dedup state is needed beyond the
# offset, because each call starts exactly where the previous one stopped, so
# every transcript line is read at most once across the checkpoint's lifetime.
_FRICTION_CHECKPOINT_SIGNAL_KEYS = ("denials", "failed_test_runs", "struggle_turns")


def _is_valid_checkpoint_int(value: object) -> bool:
    """True if `value` is a non-negative int — explicitly excluding bool,
    which subclasses int in Python (`isinstance(True, int)` is True), so an
    unvalidated check would silently accept `{"offset": true}` as offset 1."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _read_friction_checkpoint(checkpoint_path: Path, transcript_path: Path) -> tuple[int, dict[str, int]]:
    """Read a friction-count checkpoint: (byte_offset, running per-signal totals).

    Fails open to a full rescan (offset 0, zero totals) on any absent or
    malformed checkpoint — unreadable file, invalid JSON, wrong shape, a
    non-int/negative/bool offset or totals value, or a stored offset beyond
    the transcript's current size (a stale checkpoint from a transcript that
    was truncated or rewritten while the session_id-keyed checkpoint
    persisted — without this check, seeking past EOF would freeze friction
    counting for that session permanently rather than resetting it). Never
    raises.
    """
    zero_totals = {key: 0 for key in _FRICTION_CHECKPOINT_SIGNAL_KEYS}
    try:
        data = json.loads(checkpoint_path.read_text())
    except (OSError, json.JSONDecodeError):
        return 0, dict(zero_totals)
    if not isinstance(data, dict):
        return 0, dict(zero_totals)
    offset = data.get("offset")
    totals = data.get("totals")
    if not _is_valid_checkpoint_int(offset) or not isinstance(totals, dict):
        return 0, dict(zero_totals)
    running_totals: dict[str, int] = {}
    for key in _FRICTION_CHECKPOINT_SIGNAL_KEYS:
        value = totals.get(key)
        if not _is_valid_checkpoint_int(value):
            return 0, dict(zero_totals)
        running_totals[key] = value
    try:
        transcript_size = transcript_path.stat().st_size
    except OSError:
        # Transcript unreadable — let the caller's own transcript-read
        # attempt raise/report; the checkpoint content isn't the problem.
        return offset, running_totals
    if offset > transcript_size:
        return 0, dict(zero_totals)
    return offset, running_totals


def _write_friction_checkpoint(checkpoint_path: Path, offset: int, totals: dict[str, int]) -> None:
    """Best-effort persist of the new byte offset + running per-signal totals.

    Writes to a temp file in the checkpoint's own directory and atomically
    renames it into place (`os.replace`), rather than truncating the
    checkpoint file in place — an interrupted-and-resubmitted prompt can
    leave two hook invocations for the same session_id alive within the
    hook's 10s timeout, and an in-place write is a lost-update race between
    them. A failed write (unwritable directory, disk full) is swallowed
    rather than raised: the next call re-reads from the old offset and
    rescans those bytes again — correct, just not incremental for that one
    call — matching this subcommand's fail-open posture everywhere else.
    """
    with contextlib.suppress(OSError):
        fd, tmp_name = tempfile.mkstemp(
            dir=checkpoint_path.parent, prefix=f".{checkpoint_path.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(json.dumps({"offset": offset, "totals": totals}))
            os.replace(tmp_name, checkpoint_path)
        except OSError:
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)
            raise


def _consume_new_transcript_lines(transcript_path: Path, offset: int) -> tuple[list[str], int]:
    """Read complete JSONL lines appended to `transcript_path` since `offset`.

    Returns (new_lines, new_offset). A trailing line with no terminating
    newline — the transcript may be mid-write by the harness while the hook
    reads it — is left unconsumed: new_offset stops at the end of the last
    complete line, so the next call picks up the partial line's bytes whole
    once it's terminated, rather than double-reading or losing them.
    """
    with open(transcript_path, "rb") as fh:
        fh.seek(offset)
        chunk = fh.read()
    if not chunk:
        return [], offset
    pieces = chunk.split(b"\n")
    complete_pieces = pieces[:-1]  # last piece is "" (chunk ended in \n) or a partial line
    new_offset = offset + sum(len(piece) + 1 for piece in complete_pieces)
    new_lines = [piece.decode("utf-8", errors="replace") for piece in complete_pieces]
    return new_lines, new_offset


def _emit_friction_result(signals: dict[str, int], *, as_json: bool) -> None:
    """Print either the --json per-signal breakdown or the bare composite integer."""
    if as_json:
        print(json.dumps(signals))
    else:
        print(signals["composite"])


def cmd_sessions(args: argparse.Namespace) -> None:
    """Emit transcript file paths for the resolved scope, one absolute path per line.

    --paths is required: it names the one action this subcommand supports today,
    leaving room for a second sessions action later without a bare `sessions`
    invocation silently doing nothing. Sourced from _resolve_scan_roots plus
    _resolve_project_scope (not a flat glob), so a main session file only reaches
    this repo's own worktrees under --this-repo the same way every other
    subcommand does. --include-subagents additionally emits each split subagent
    file's own path, found the same way _read_session_file (:394-407) locates
    them for its own record merge -- <session>/subagents/*.jsonl under the main
    file's own directory -- rather than a flat glob across the whole scope, so a
    caller that reads only the emitted paths gets exactly the same file set
    _read_session_file(include_subagents=True) would have merged, split back out
    into individually readable files. The resolved-scope header goes to stderr,
    matching audit-routing-samples' convention — stdout here is meant to be
    piped to xargs/Read, not mixed with a header line.
    """
    if not bool(getattr(args, "paths", False)):
        print("sessions: --paths is required (no other sessions action exists yet)", file=sys.stderr)
        sys.exit(2)

    include_subagents = bool(getattr(args, "include_subagents", False))
    roots = _resolve_scan_roots(args)
    session_iter, scope_label = _resolve_project_scope(
        args, "sessions", include_subagents=include_subagents, roots=roots
    )
    _print_resolved_scope("sessions", scope_label, roots, file=sys.stderr)
    for jsonl, _records in session_iter:
        print(jsonl)
        if include_subagents:
            subagent_dir = jsonl.parent / jsonl.stem / SUBAGENT_SUBDIR
            if subagent_dir.is_dir():
                for sub_jsonl in sorted(subagent_dir.glob("*.jsonl")):
                    print(sub_jsonl)


def cmd_friction_count(args: argparse.Namespace) -> None:
    """Composite friction-signal count for a single transcript file.

    Reads exactly one JSONL file (no iter_sessions, no gh) and prints the
    composite integer to stdout. --json prints the per-signal breakdown
    instead of the composite.

    --checkpoint <path> makes the scan incremental: only the bytes appended
    to the transcript since the checkpoint's stored byte offset are parsed,
    the resulting per-signal deltas are added to the checkpoint's running
    totals, the new offset + totals are written back, and the *cumulative*
    totals (not just this call's delta) are what gets printed. Without
    --checkpoint, behavior is unchanged: a full scan every call, no state
    read or written.
    """
    transcript_path = Path(args.transcript)
    checkpoint_arg = getattr(args, "checkpoint", None)

    if checkpoint_arg:
        checkpoint_path = Path(checkpoint_arg)
        offset, running_totals = _read_friction_checkpoint(checkpoint_path, transcript_path)
        try:
            new_lines, new_offset = _consume_new_transcript_lines(transcript_path, offset)
        except OSError:
            print(f"friction-count: cannot read transcript file: {transcript_path}", file=sys.stderr)
            sys.exit(1)

        records: list[dict] = []
        for raw in new_lines:
            try:
                records.append(json.loads(raw))
            except json.JSONDecodeError:
                continue

        deltas = _friction_signals(records)
        for key in _FRICTION_CHECKPOINT_SIGNAL_KEYS:
            running_totals[key] += deltas[key]
        _write_friction_checkpoint(checkpoint_path, new_offset, running_totals)

        composite = sum(running_totals[key] for key in _FRICTION_CHECKPOINT_SIGNAL_KEYS)
        signals = {**running_totals, "composite": composite}
        _emit_friction_result(signals, as_json=getattr(args, "json", False))
        return

    records = []
    try:
        # errors="replace" mirrors the --checkpoint branch's binary-read +
        # decode tolerance: a transcript with invalid Unicode bytes must
        # degrade to lossy decoding, not raise an uncaught
        # UnicodeDecodeError (a ValueError subclass the `except OSError`
        # below does not catch).
        with open(transcript_path, encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                records.append(rec)
    except OSError:
        print(f"friction-count: cannot read transcript file: {transcript_path}", file=sys.stderr)
        sys.exit(1)

    signals = _friction_signals(records)
    _emit_friction_result(signals, as_json=getattr(args, "json", False))


# --- rearm-backtest: backtest candidate re-arm band spacings for the
# handoff nudge's one-shot fire against the recorded corpus. See
# .claude/plans/handoff-nudge-rearm-backtest.md for the full design.

# Mirrors nudge-handoff-near-context-cap.sh's own HANDOFF_NUDGE_ABS_CAP
# default (docs/handoff-nudge.md's "Why this cap" section). Duplicated
# rather than imported -- there is no mechanism to share a constant between
# a bash hook and a Python script, the same cross-language duplication
# _context_window_for_model's docstring already documents. Not a CLI flag:
# .claude/plans/token-cost-reduction.md's Phase 3 keeps this fixed, and a
# flag would invite a future run to quietly retune it through this tool.
_HANDOFF_NUDGE_ABS_CAP = 360_000

# Mirrors the hook's own `PCT_THRESHOLD=$(( CONTEXT_WINDOW * 40 / 100 ))` --
# the hook fires at the LESSER of 40% of the active model's context window
# and _HANDOFF_NUDGE_ABS_CAP, so a 200k-window model's real fire point
# (80,000) is well under the 1M-window arm's cap-governed 360,000. Neither
# this fraction nor _HANDOFF_NUDGE_ABS_CAP is backtested -- only re-arm
# spacing past whichever of the two governs a given session is.
_HANDOFF_NUDGE_PCT_THRESHOLD = 0.40

# PR #605's own turn-index bands (.claude/plans/handoff-boundary-decision-rule.md),
# reused here for comparability with that point-in-time measurement -- the
# dollar/context figures themselves are re-derived from the current corpus on
# every run, never hardcoded. Follows _EDIT_OLD_STRING_SIZE_BUCKETS' own
# cascading less-than convention: a turn index is tested against each bound
# in order and takes the first label whose bound it's under, so an index
# PR #605's own table never explicitly labeled (10-19, between "5-10" and
# "20-40") falls through to "20-40" rather than going unbucketed.
_RAMP_CURVE_TURN_INDEX_BUCKETS: tuple[tuple[int, str], ...] = (
    (5, "0-5"),
    (10, "5-10"),
    (40, "20-40"),
    (80, "40-80"),
    (150, "80-150"),
    (300, "150-300"),
)
_RAMP_CURVE_TURN_INDEX_OVERFLOW_LABEL = "300+"
_RAMP_CURVE_BUCKET_LABELS: tuple[str, ...] = tuple(
    label for _, label in _RAMP_CURVE_TURN_INDEX_BUCKETS
) + (_RAMP_CURVE_TURN_INDEX_OVERFLOW_LABEL,)

_REARM_BACKTEST_DEFAULT_SPACINGS: tuple[int, ...] = (40_000, 80_000, 120_000)

# The three line shapes _parse_nudge_log_entries recognizes: "nudged" and
# "schema-drift" are written by nudge-handoff-near-context-cap.sh itself
# (docs/handoff-nudge.md's "Log location" table); "handoff" is appended by
# the handoff skill's own conversion-signal step
# (claude/.claude/skills/handoff/SKILL.md, "After writing: record the
# conversion signal").
_NUDGE_LOG_LINE_KINDS = ("nudged", "schema-drift", "handoff")


def _ramp_curve_turn_index_bucket(turn_index: int) -> str:
    """Bucket a 0-indexed main-thread turn position (turns since a real or
    simulated fresh session start) into one of PR #605's seven turn-index
    bands, via the cascading less-than lookup _RAMP_CURVE_TURN_INDEX_BUCKETS'
    own docstring explains."""
    for bound, label in _RAMP_CURVE_TURN_INDEX_BUCKETS:
        if turn_index < bound:
            return label
    return _RAMP_CURVE_TURN_INDEX_OVERFLOW_LABEL


def _hook_effective_fire_threshold(model: str) -> int:
    """The real hook's own fire threshold for one model: the lesser of 40% of
    that model's context window (_context_window_for_model, mirroring the
    bash hook's own CONTEXT_WINDOW case statement) and _HANDOFF_NUDGE_ABS_CAP.
    A 200k-window model's real threshold (80,000) is well under a 1M-window
    model's cap-governed one (360,000) -- using _HANDOFF_NUDGE_ABS_CAP alone
    for every session would overstate how early such sessions actually get
    nudged today."""
    pct_threshold = int(_context_window_for_model(model) * _HANDOFF_NUDGE_PCT_THRESHOLD)
    return min(pct_threshold, _HANDOFF_NUDGE_ABS_CAP)


def _hook_observable_boundaries(records: Sequence[dict]) -> list[int]:
    """Turn-count positions (0..N, where N is the session's own main-thread
    turn count) at which nudge-handoff-near-context-cap.sh could observe this
    session's growing context. `records` must already be
    _dedup_turns_by_request_id's output -- the same records a caller builds
    its own main_thread_turns list from -- so a returned boundary is directly
    usable as a slice/turn-count index into that list.

    UserPromptSubmit and Stop both check the transcript's latest recorded
    main-thread assistant usage (docs/handoff-nudge.md), so the two fire at
    the same observable point: right after a run of tool-call-only turns
    yields back to a genuine user message. Reusing _is_fresh_user_prompt for
    that user-message half (see its own docstring for what it filters) marks
    every INTERNAL boundary -- one per genuine user message, not one per
    turn, so a multi-tool-call stretch between two user messages contributes
    no boundary of its own. Session start (0, before any turn) and session
    end (N, the full turn count) are the two boundaries no user message can
    supply on their own, and both are always included: the hook's own header
    comment states it is "registered on both events so a session that
    crosses the threshold on its final turn, with no further user prompt,
    still gets warned" -- a boundary set with no session-end entry would make
    a last-turn crossing invisible to the simulation.

    A turn only counts toward the position (and thus toward a boundary) when
    it carries a usage block, matching exactly the predicate a caller uses to
    build main_thread_turns -- a main-thread assistant record with no usage
    block (a synthetic error record, see _dedup_turns_by_request_id's
    docstring) must not desync the two lists' shared indexing.
    """
    boundaries: list[int] = [0]
    main_turn_count = 0
    for rec in records:
        if rec.get("type") == "assistant" and not bool(rec.get("isSidechain")):
            if (rec.get("message") or {}).get("usage"):
                main_turn_count += 1
            continue
        if main_turn_count > 0 and _is_fresh_user_prompt(rec) and boundaries[-1] != main_turn_count:
            boundaries.append(main_turn_count)
    if boundaries[-1] != main_turn_count:
        boundaries.append(main_turn_count)
    return boundaries


def _extract_rearm_session_turns(records: Sequence[dict]) -> dict:
    """Single dedup+price pass over one session's raw records, shared by
    _ramp_curve_from_corpus and _rearm_backtest_report so each session's
    records are decoded/deduped/priced exactly once per report run instead
    of twice -- every sibling subcommand in this file (`cost`,
    `context-distribution`, etc.) does a single streaming pass over its
    corpus, not two.

    Returns a dict with:
    - "deduped": _dedup_turns_by_request_id's output, for a caller building
      _hook_observable_boundaries from the same records.
    - "main_thread_turns": one (context_at_turn, output_tokens, actual_dollars)
      tuple per main-thread assistant turn carrying a usage block
      (actual_dollars is 0.0 when the turn's model is unpriced), in
      _simulate_rearm_spacing's own input shape.
    - "main_thread_priced": one bool per entry in main_thread_turns, parallel
      to it, True when that turn's model was priced -- _ramp_curve_from_corpus
      only buckets priced turns.
    - "sidechain_dollars_total": summed actual dollars across this session's
      priced sidechain turns.
    - "unpriced_turns" / "unpriced_tokens": counts across both main-thread and
      sidechain turns whose model has no price-table entry.
    - "session_threshold": _hook_effective_fire_threshold for this session's
      first main-thread turn's model (None if the session has no main-thread
      turn with a usage block).
    """
    deduped = _dedup_turns_by_request_id(records)
    main_thread_turns: list[tuple[int, int, float]] = []
    main_thread_priced: list[bool] = []
    sidechain_dollars_total = 0.0
    unpriced_turns = 0
    unpriced_tokens = 0
    session_threshold: int | None = None

    for rec in deduped:
        if rec.get("type") != "assistant":
            continue
        msg = rec.get("message") or {}
        usage = msg.get("usage")
        if not usage:
            continue
        model = msg.get("model", "")
        dollars_by_class, context_at_turn, turn_unpriced_tokens = _price_turn(model, usage)
        output_tokens = int(usage.get("output_tokens", 0))
        if bool(rec.get("isSidechain")):
            if dollars_by_class is not None:
                sidechain_dollars_total += sum(dollars_by_class.values())
            else:
                unpriced_turns += 1
                unpriced_tokens += turn_unpriced_tokens
            continue
        if dollars_by_class is None:
            unpriced_turns += 1
            unpriced_tokens += turn_unpriced_tokens
            actual_dollars = 0.0
        else:
            actual_dollars = sum(dollars_by_class.values())
        if session_threshold is None:
            session_threshold = _hook_effective_fire_threshold(model)
        main_thread_turns.append((context_at_turn, output_tokens, actual_dollars))
        main_thread_priced.append(dollars_by_class is not None)

    return {
        "deduped": deduped,
        "main_thread_turns": main_thread_turns,
        "main_thread_priced": main_thread_priced,
        "sidechain_dollars_total": sidechain_dollars_total,
        "unpriced_turns": unpriced_turns,
        "unpriced_tokens": unpriced_tokens,
        "session_threshold": session_threshold,
    }


def _ramp_curve_from_corpus(sessions: Iterable[dict]) -> tuple[dict[str, dict[str, float]], int]:
    """Re-derive PR #605's fresh-session rebuild ramp from the current corpus
    instead of citing that PR's own table: its source document
    (.claude/plans/handoff-boundary-decision-rule.md) calls the table
    "a point-in-time measurement, not a reproducible report," so this
    subcommand recomputes it every run against whatever corpus is in scope.

    `sessions` is _extract_rearm_session_turns' own output, one dict per
    session -- this function does no I/O or dedup/pricing of its own, only
    the bucket aggregation, so a caller extracts each session's turns
    exactly once and fans the result out to both this function and its own
    main_thread_turns/session_traces bookkeeping.

    Buckets main-thread turns only (no sidechain/subagent turns -- a
    subagent dispatch pays its own prefix from scratch and never represents
    a "turns since a fresh session start" position) by
    _ramp_curve_turn_index_bucket. Each bucket's "rate" is $/1k output
    tokens (dollars / (output_tokens/1000), the same normalize-by-work
    convention PR #605's own table used) and "mean_context" is the
    output-token-weighted mean context_at_turn for turns in that bucket --
    both needed by _simulate_rearm_spacing to price and to estimate the
    context depth of a counterfactually-repriced turn.

    A bucket with zero output tokens in the resolved corpus falls back to the
    corpus-wide rate/mean_context (also 0.0 when the whole corpus has zero
    output tokens) rather than a division-by-zero or NaN -- a corpus that
    doesn't happen to have a session long enough to populate the "300+"
    bucket must still return a usable, defined number for that bucket.

    Returns (curve, total_output_tokens): total_output_tokens is the whole
    resolved corpus's own priced output-token count, letting a caller detect
    the corpus-wide-zero case (every bucket's rate/mean_context silently 0.0,
    with nothing in curve itself distinguishing that from a genuinely cheap
    ramp) distinctly from a normal, populated curve.
    """
    bucket_dollars: dict[str, float] = defaultdict(float)
    bucket_output_tokens: dict[str, int] = defaultdict(int)
    bucket_context_weighted: dict[str, float] = defaultdict(float)
    total_dollars = 0.0
    total_output_tokens = 0
    total_context_weighted = 0.0

    for session in sessions:
        turns = session["main_thread_turns"]
        for turn_index, is_priced in enumerate(session["main_thread_priced"]):
            if not is_priced:
                continue
            context_at_turn, output_tokens, turn_dollars = turns[turn_index]
            label = _ramp_curve_turn_index_bucket(turn_index)
            bucket_dollars[label] += turn_dollars
            bucket_output_tokens[label] += output_tokens
            bucket_context_weighted[label] += context_at_turn * output_tokens
            total_dollars += turn_dollars
            total_output_tokens += output_tokens
            total_context_weighted += context_at_turn * output_tokens

    fallback_rate = (total_dollars / (total_output_tokens / 1000)) if total_output_tokens else 0.0
    fallback_context = (total_context_weighted / total_output_tokens) if total_output_tokens else 0.0

    curve: dict[str, dict[str, float]] = {}
    for label in _RAMP_CURVE_BUCKET_LABELS:
        out_tok = bucket_output_tokens.get(label, 0)
        if out_tok:
            curve[label] = {
                "rate": bucket_dollars[label] / (out_tok / 1000),
                "mean_context": bucket_context_weighted[label] / out_tok,
            }
        else:
            curve[label] = {"rate": fallback_rate, "mean_context": fallback_context}
    return curve, total_output_tokens


def _parse_nudge_log_entries(log_path: Path) -> list[dict]:
    """Parse ~/.claude/.handoff-nudge.log into one dict per recognized line
    (see _NUDGE_LOG_LINE_KINDS), reusing _read_bounded_log_lines' bounded
    2MB tail-read rather than a fresh read implementation. A line that
    doesn't start with a recognized kind, or whose fields don't parse (a
    missing required key, or a non-integer est/window), is silently skipped
    -- this is a best-effort append-only operational log, not a format this
    tool controls.

    Each returned dict carries "kind" plus that kind's own fields:
    - nudged: session, est (int), model, window (int), event
    - schema-drift: session, event
    - handoff: session
    """
    entries: list[dict] = []
    for line in _read_bounded_log_lines(log_path):
        tokens = line.split()
        if not tokens or tokens[0] not in _NUDGE_LOG_LINE_KINDS:
            continue
        kind = tokens[0]
        fields: dict[str, str] = {}
        malformed = False
        for tok in tokens[1:]:
            if "=" not in tok:
                malformed = True
                break
            key, _, value = tok.partition("=")
            fields[key] = value
        if malformed:
            continue

        if kind == "nudged":
            if not {"session", "est", "model", "window", "event"} <= fields.keys():
                continue
            try:
                est = int(fields["est"])
                window = int(fields["window"])
            except ValueError:
                continue
            entries.append({
                "kind": "nudged", "session": fields["session"], "est": est,
                "model": fields["model"], "window": window, "event": fields["event"],
            })
        elif kind == "schema-drift":
            if not {"session", "event"} <= fields.keys():
                continue
            entries.append({"kind": "schema-drift", "session": fields["session"], "event": fields["event"]})
        else:  # handoff
            if "session" not in fields:
                continue
            entries.append({"kind": "handoff", "session": fields["session"]})
    return entries


def _operator_response_lag_from_log(
    session_traces: dict[str, list[int]], log_entries: list[dict]
) -> tuple[list[int], int]:
    """Measure how far past each logged nudge's fire point sessions in scope
    actually kept running, for the compliance-realistic backtest model.

    session_traces maps a full session id (jsonl.stem, matching the hook's
    own SESSION_ID) to that session's ordered per-main-thread-turn abs-token
    values (context_at_turn + output_tokens -- the hook's own ESTIMATE unit).
    A `nudged` log line carries no timestamp (docs/handoff-nudge.md's "Log
    location" table: session=/est=/model=/window=/event= only), so the join
    key is session_id plus a first-crossing rule: the fire turn is the
    trace's first value >= est, matching the real hook's own semantics -- it
    fires once, at the first crossing, never later. A nearest-value join
    would instead risk landing on a turn *after* a mid-session compaction
    (isCompactSummary) dip whose abs-token value happens to be closer to est
    than the true, earlier first-crossing turn, silently corrupting the
    measured lag with no error and no other signal.

    Returns (lags, excluded_count): lags is one non-negative token delta
    (peak abs-tokens reached at or after the identified fire turn, minus est)
    per successfully joined `nudged` line. A `nudged` line whose session_id
    has no entry in session_traces (a since-deleted transcript, or a session
    from an account/root outside the resolved scope), or whose trace never
    reaches est at all, is excluded and counted rather than silently dropped.
    """
    lags: list[int] = []
    excluded = 0
    for entry in log_entries:
        if entry.get("kind") != "nudged":
            continue
        trace = session_traces.get(entry["session"])
        if not trace:
            excluded += 1
            continue
        est = entry["est"]
        fire_idx = next((i for i, value in enumerate(trace) if value >= est), None)
        if fire_idx is None:
            excluded += 1
            continue
        lags.append(max(trace[fire_idx:]) - est)
    return lags, excluded


def _simulate_rearm_spacing(
    main_thread_turns: Sequence[tuple[int, int, float]],
    boundaries: Sequence[int],
    spacing: int,
    ramp_curve: dict[str, dict[str, float]],
    threshold: int,
    *,
    response_lag_tokens: float = 0.0,
) -> tuple[float, float, float]:
    """Replay one session's main-thread turns under one candidate re-arm spacing.

    main_thread_turns is a (context_at_turn, output_tokens, actual_dollars)
    tuple per turn, in order; boundaries is _hook_observable_boundaries'
    output for the same session. Real context/output growth is replayed
    unmodified throughout -- band crossings are detected against the
    session's *actual* recorded trajectory, never a counterfactually-reset
    one. Turns before the first detected crossing keep their actual recorded
    dollars and context. Each crossing "splits" the session: turns from that
    point until the next crossing (or session end) are re-priced by mapping
    their distance from the split to a turns-since-a-fresh-restart position
    and applying ramp_curve's rate/mean_context at that position to the
    turn's own real output-token volume -- work stays constant, only the
    context-depth-driven rate changes, modeling what a fresh session would
    have billed for the same work rather than what the real, ever-growing
    prefix actually cost.

    A crossing is only detectable at a boundary in `boundaries`.
    response_lag_tokens (0.0 for the perfect-compliance model) shifts each
    band's trigger point later by that many tokens, modeling the empirically
    measured gap (_operator_response_lag_from_log) between a nudge firing and
    the operator actually acting on it, for the compliance-realistic model.

    Returns (total_dollars, context_weighted_sum, output_token_weight): the
    last two let a caller aggregate an output-token-weighted mean context
    ("C_bar", `cost ~= N x C_bar x rate` in .claude/plans/token-cost-reduction.md)
    across many sessions without re-deriving per-turn context outside this
    function.
    """
    boundary_set = set(boundaries)
    total = 0.0
    context_weighted = 0.0
    weight = 0.0
    fired_bands = 0
    turns_since_restart = 0
    in_actual_epoch = True

    for i, (context_at_turn, output_tokens, actual_dollars) in enumerate(main_thread_turns):
        if in_actual_epoch:
            total += actual_dollars
            context_weighted += context_at_turn * output_tokens
        else:
            label = _ramp_curve_turn_index_bucket(turns_since_restart)
            bucket = ramp_curve.get(label, {"rate": 0.0, "mean_context": 0.0})
            total += (output_tokens / 1000) * bucket["rate"]
            context_weighted += bucket["mean_context"] * output_tokens
            turns_since_restart += 1
        weight += output_tokens

        abs_tokens = context_at_turn + output_tokens
        band_trigger = threshold + fired_bands * spacing + response_lag_tokens
        if abs_tokens >= band_trigger and (i + 1) in boundary_set:
            fired_bands += 1
            in_actual_epoch = False
            turns_since_restart = 0

    return total, context_weighted, weight


def _parse_rearm_spacings_arg(args: argparse.Namespace) -> list[int]:
    """Parse --spacings' comma-separated token list into a list of positive
    ints, defaulting to _REARM_BACKTEST_DEFAULT_SPACINGS. Exits 2 on a
    non-integer or non-positive value."""
    raw: str = getattr(args, "spacings", None) or ",".join(str(s) for s in _REARM_BACKTEST_DEFAULT_SPACINGS)
    spacings: list[int] = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            value = int(token)
        except ValueError:
            print(
                f"rearm-backtest: --spacings: expected comma-separated integers, got {token!r} in {raw!r}",
                file=sys.stderr,
            )
            sys.exit(2)
        if value <= 0:
            print(f"rearm-backtest: --spacings: values must be positive, got {value}", file=sys.stderr)
            sys.exit(2)
        spacings.append(value)
    if not spacings:
        print("rearm-backtest: --spacings: at least one spacing value is required", file=sys.stderr)
        sys.exit(2)
    return spacings


def _session_matches_rearm_scope(
    records: Sequence[dict], since_ts: float | None, branch_filter: set[str] | None
) -> bool:
    """Whether a whole session belongs in a rearm-backtest run's scope.

    --since and --branches scope entire sessions here, not individual turns
    within one -- unlike `cost`'s per-record --branches filter, a re-arm
    simulation's turns-since-restart positioning depends on a session's own
    turn sequence staying intact, so silently dropping turns mid-session
    would desync _hook_observable_boundaries' turn-count boundaries from
    whatever's left of main_thread_turns.
    """
    if since_ts is not None:
        first_ts = next((ts for r in records if (ts := _parse_ts(r.get("timestamp"))) is not None), None)
        if first_ts is not None and first_ts < since_ts:
            return False
    return branch_filter is None or any(
        r.get("type") == "assistant" and not bool(r.get("isSidechain")) and r.get("gitBranch") in branch_filter
        for r in records
    )


def cmd_rearm_backtest(args: argparse.Namespace) -> None:
    """CLI entry point for the rearm-backtest subcommand.

    Root resolution happens here, at the CLI boundary, rather than inside
    _rearm_backtest_report, mirroring cmd_cost -- --config-dir validation
    exits before any scan work. The wall-clock date is read exactly once,
    here, mirroring cmd_cost_trend's own split.
    """
    roots = _resolve_cost_roots(args, subcommand="rearm-backtest")
    _rearm_backtest_report(args, datetime.now(UTC).date(), roots)


def _rearm_backtest_report(args: argparse.Namespace, today: date, roots: Sequence[Path] | None = None) -> None:
    """Backtest candidate re-arm band spacings against the recorded corpus.

    One row per candidate spacing (--spacings, default
    _REARM_BACKTEST_DEFAULT_SPACINGS) plus an unmodified baseline row
    (today's real recorded one-shot totals, i.e. spacing = never re-arm),
    each under both the perfect-compliance and compliance-realistic models
    (see _simulate_rearm_spacing's response_lag_tokens). Each session's first
    fire point is its own effective threshold (_hook_effective_fire_threshold,
    from that session's own model) -- unchanged from today's real hook
    behavior -- and only re-arm spacing PAST that point varies; model routing
    and the threshold computation itself are held fixed and printed as such,
    so a reader can't mistake "spacing-only" for "everything."
    """
    redact: bool = not bool(getattr(args, "no_redact", False))
    scan_roots: Sequence[Path] = roots if roots is not None else (PROJECTS_DIR,)
    multi_root = len(scan_roots) > 1

    if not redact and multi_root:
        print(
            "rearm-backtest: --no-redact is refused when more than one root is in scope"
            " (--config-dir was given); drop --no-redact or scope to a single profile",
            file=sys.stderr,
        )
        sys.exit(2)
    if not redact:
        print(_DO_NOT_PUBLISH_BANNER)
        print(_DO_NOT_PUBLISH_BANNER, file=sys.stderr)

    spacings = _parse_rearm_spacings_arg(args)
    since_ts, since_raw = _parse_since_nd_arg(args, "rearm-backtest")
    branch_filter = _branch_filter(args)

    session_iter, scope_label = _resolve_project_scope(args, "rearm-backtest", roots=roots)
    _print_resolved_scope("rearm-backtest", scope_label, scan_roots)
    # Each in-scope session's records are deduped and priced exactly once,
    # via _extract_rearm_session_turns, and the same extraction dict feeds
    # both _ramp_curve_from_corpus and this function's own
    # sessions_data/session_traces bookkeeping below -- a single pass over
    # the corpus, matching every sibling subcommand in this file (`cost`,
    # `context-distribution`, etc.).
    scoped_sessions = [
        (jsonl, _extract_rearm_session_turns(records)) for jsonl, records in session_iter
        if _session_matches_rearm_scope(records, since_ts, branch_filter)
    ]

    ramp_curve, ramp_curve_output_tokens = _ramp_curve_from_corpus(data for _jsonl, data in scoped_sessions)

    sessions_data: list[dict] = []
    session_traces: dict[str, list[int]] = {}
    sidechain_dollars_total = 0.0
    unpriced_turns = 0
    unpriced_tokens = 0

    for jsonl, data in scoped_sessions:
        sidechain_dollars_total += data["sidechain_dollars_total"]
        unpriced_turns += data["unpriced_turns"]
        unpriced_tokens += data["unpriced_tokens"]
        main_thread_turns = data["main_thread_turns"]
        if not main_thread_turns:
            continue

        session_id = jsonl.stem
        sessions_data.append({
            "session_id": session_id,
            "main_thread_turns": main_thread_turns,
            "boundaries": _hook_observable_boundaries(data["deduped"]),
            # The real hook resolves its threshold from whichever model is
            # active when it checks (_hook_effective_fire_threshold); a
            # session almost always stays on one model family, so
            # session_threshold (from its first main-thread turn's model)
            # approximates that check well enough for a single per-session
            # scalar -- exactly what _simulate_rearm_spacing's `threshold`
            # param takes.
            "threshold": data["session_threshold"],
        })
        session_traces[session_id] = [c + o for c, o, _d in main_thread_turns]

    if not sessions_data:
        print("No priced main-thread turns found in scope.")
        if unpriced_turns:
            print(f"  ({unpriced_turns:,} unpriced turns / {unpriced_tokens:,} tokens excluded from priced spend)")
        return

    log_entries = _parse_nudge_log_entries(config_dir() / ".handoff-nudge.log")
    lags, excluded_count = _operator_response_lag_from_log(session_traces, log_entries)
    if lags:
        sorted_lags = sorted(lags)
        mid = len(sorted_lags) // 2
        median_lag = float(sorted_lags[mid]) if len(sorted_lags) % 2 else (sorted_lags[mid - 1] + sorted_lags[mid]) / 2
    else:
        median_lag = 0.0

    # Baseline: today's real recorded totals, no counterfactual repricing at all.
    baseline_main_dollars = sum(d for s in sessions_data for _c, _o, d in s["main_thread_turns"])
    baseline_context_weighted = sum(c * o for s in sessions_data for c, o, _d in s["main_thread_turns"])
    baseline_output_tokens = sum(o for s in sessions_data for _c, o, _d in s["main_thread_turns"])
    baseline_total = baseline_main_dollars + sidechain_dollars_total
    baseline_c_bar = (baseline_context_weighted / baseline_output_tokens) if baseline_output_tokens else 0.0

    title_since = f"last {since_raw}" if since_raw else "all time"
    print(f"\n## Re-arm spacing backtest ({title_since}, generated {today.isoformat()})\n")
    print(f"Sessions in scope: {len(sessions_data):,}")
    if unpriced_turns:
        print(f"  ({unpriced_turns:,} unpriced turns / {unpriced_tokens:,} tokens excluded from priced spend)")
    print(
        f"Operator-response-lag sample: {len(lags)} joined 'nudged' log line(s)"
        f" ({excluded_count} excluded -- no matching session in scope), median lag"
        f" {median_lag:,.0f} tokens past the fire point"
    )
    print(
        "\nModel routing and each session's own fire threshold (the lesser of 40% of its model's"
        " context window and the fixed 360,000-token _HANDOFF_NUDGE_ABS_CAP -- mirroring the hook's"
        " real behavior) are held fixed and are NOT backtested by this report -- only re-arm spacing"
        " past the first fire varies."
    )
    if ramp_curve_output_tokens == 0:
        print(
            "\nWARNING: no priced output tokens found anywhere in scope, so the re-arm ramp curve"
            " could not be computed -- every re-armed remainder below is priced at $0.00/1k, not a"
            " genuinely cheap ramp."
        )

    header = f"{'Spacing':>10} {'Model':>12} {'$':>14} {'DeltaUSD':>10} {'C_bar':>10} {'DeltaCbar':>12}"
    print(f"\n{header}")
    print("-" * len(header))
    print(
        f"{'baseline':>10} {'actual':>12} {baseline_total:>14,.2f} {'--':>10}"
        f" {baseline_c_bar:>10,.0f} {'--':>12}"
    )

    for spacing in spacings:
        for compliance_label, lag in (("perfect", 0.0), ("realistic", median_lag)):
            main_dollars = 0.0
            context_weighted = 0.0
            weight = 0.0
            for s in sessions_data:
                dollars, c_weighted, w = _simulate_rearm_spacing(
                    s["main_thread_turns"], s["boundaries"], spacing, ramp_curve,
                    s["threshold"], response_lag_tokens=lag,
                )
                main_dollars += dollars
                context_weighted += c_weighted
                weight += w
            total = main_dollars + sidechain_dollars_total
            c_bar = (context_weighted / weight) if weight else 0.0
            delta = total - baseline_total
            delta_c_bar = c_bar - baseline_c_bar
            print(
                f"{spacing:>10,} {compliance_label:>12} {total:>14,.2f} {delta:>+10,.2f}"
                f" {c_bar:>10,.0f} {delta_c_bar:>+12,.0f}"
            )


def _add_project_scope_args(parser: argparse.ArgumentParser) -> None:
    """Add the shared --projects/--this-repo scope flags to a subparser.

    Mutually exclusive: --this-repo routes through _repo_scoped_project_slugs(),
    an identity-based minimization control; --projects keeps the pre-existing
    machine-wide glob default ("*") so no existing invocation's behavior changes.
    """
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--projects", default="*", metavar="GLOB")
    group.add_argument(
        "--this-repo", action="store_true",
        help="Scope to this repo's own worktrees only (see docs/transcript-analysis.md).",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Claude Code transcript analysis toolkit.")
    # Top-level (not per-subcommand) so main() can reassign PROJECTS_DIR before
    # any subcommand runs, regardless of which one was chosen. Resolving the
    # path inside the script, via a plain CLI flag, rather than through a
    # `CLAUDE_CONFIG_DIR=... python3 ...` shell prefix keeps a non-personal
    # account's ~/.config/claude-accounts/<account> path out of the env-var-
    # assignment shape Claude Code's Bash permission classifier denies on.
    parser.add_argument(
        "--config-dir", metavar="PATH", default=None,
        help=(
            "Resolve sessions under PATH/projects instead of the default "
            "Claude Code config dir (CLAUDE_CONFIG_DIR, or ~/.claude). Must "
            "precede the subcommand name."
        ),
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    p_buckets = sub.add_parser("buckets", help="Assistant turns bucketed by gitBranch × model family.")
    _add_project_scope_args(p_buckets)
    p_buckets.add_argument("--branches", metavar="B1,B2,...", help="Branch name filter (default: all)")
    p_buckets.set_defaults(func=cmd_buckets)

    p_fail = sub.add_parser("fail-seq", help="Ordered test-run failed-count sequence per branch/model.")
    p_fail.add_argument("--branches", required=True, metavar="B1,B2,...")
    _add_project_scope_args(p_fail)
    p_fail.set_defaults(func=cmd_fail_seq)

    p_struggle = sub.add_parser("struggle", help="Correction/frustration signal phrases in user turns, split by model.")
    p_struggle.add_argument("--branches", metavar="B1,B2,...")
    _add_project_scope_args(p_struggle)
    p_struggle.set_defaults(func=cmd_struggle)

    p_user_input = sub.add_parser(
        "user-input",
        help="All fresh user prompts per session, classified as initial / followup / explicit-correction.",
    )
    p_user_input.add_argument("--projects", default="*", metavar="GLOB")
    p_user_input.add_argument("--branches", metavar="B1,B2,...")
    p_user_input.add_argument("--since", metavar="DATE", type=_iso_date, help="Inclusive start date (YYYY-MM-DD)")
    p_user_input.add_argument("--until", metavar="DATE", type=_iso_date, help="Inclusive end date (YYYY-MM-DD)")
    p_user_input.add_argument(
        "--corrections-only", action="store_true",
        help="Show only non-initial prompts.",
    )
    p_user_input.add_argument(
        "--truncate-chars", type=int, default=500, metavar="N",
        help="Truncate prompt text at N chars (0 = no truncation).",
    )
    p_user_input.add_argument("--out", metavar="PATH", help="Write output to a file instead of stdout.")
    p_user_input.add_argument(
        "--redact", action="store_true",
        help=(
            "Anonymize project labels and session IDs for public reporting "
            "(prompt text is not redacted — review before sharing)."
        ),
    )
    p_user_input.set_defaults(func=cmd_user_input)

    p_duration = sub.add_parser("duration", help="Active span vs idle-gap decomposition per branch.")
    p_duration.add_argument("--branches", metavar="B1,B2,...")
    _add_project_scope_args(p_duration)
    p_duration.add_argument("--gap-minutes", type=int, default=30, metavar="N")
    p_duration.set_defaults(func=cmd_duration)

    p_sub = sub.add_parser(
        "subagents",
        help=(
            "isSidechain turn counts and model split per branch, plus tool-result bytes"
            " per thread and per tool name."
        ),
    )
    p_sub.add_argument("--branches", metavar="B1,B2,...")
    _add_project_scope_args(p_sub)
    p_sub.add_argument(
        "--config-dir", action="append", dest="extra_config_dirs", metavar="DIR",
        help=(
            "Additional Claude Code config directory to scan (repeatable). The default resolved"
            " config dir is always scanned first. Each supplied directory must contain a projects/"
            " subdirectory, or it is rejected. Refused together with --this-repo. Branch names are"
            " redacted and _DO_NOT_PUBLISH_BANNER is printed whenever more than one root is in scope."
        ),
    )
    p_sub.add_argument(
        "--since", metavar="Nd",
        help="Limit the reported tables to records with timestamp in the last N days (e.g. 35d).",
    )
    p_sub.set_defaults(func=cmd_subagents)

    p_mix = sub.add_parser(
        "subagent-mix",
        help=(
            "Subagent_type spawn counts per branch, with code/plan/ready-for-review skill"
            " invocations, plus a per-agentType observed/requested/declared model-mix table."
        ),
    )
    p_mix.add_argument("--branches", metavar="B1,B2,...")
    _add_project_scope_args(p_mix)
    p_mix.add_argument(
        "--config-dir", action="append", dest="extra_config_dirs", metavar="DIR",
        help=(
            "Additional Claude Code config directory to scan (repeatable). The default resolved"
            " config dir is always scanned first. Each supplied directory must contain a projects/"
            " subdirectory, or it is rejected. Refused together with --this-repo or --per-session."
            " Branch names are redacted and _DO_NOT_PUBLISH_BANNER is printed whenever more than"
            " one root is in scope."
        ),
    )
    p_mix.add_argument(
        "--since", metavar="Nd",
        help="Limit the reported tables to records with timestamp in the last N days (e.g. 35d).",
    )
    p_mix.add_argument(
        "--per-session",
        action="store_true",
        help="Break out by individual session instead of aggregating per branch. Refused under --config-dir.",
    )
    p_mix.add_argument(
        "--since-date", metavar="DATE", type=_iso_date,
        help=(
            "Inclusive start date (YYYY-MM-DD) for the Actual $ / Counterfactual $ columns only,"
            " filtered per sidechain record — independent of --since Nd, which keeps its existing"
            " dispatch-level scope over every other column."
        ),
    )
    p_mix.add_argument(
        "--until-date", metavar="DATE", type=_iso_date,
        help="Inclusive end date (YYYY-MM-DD) for the Actual $ / Counterfactual $ columns only — see --since-date.",
    )
    p_mix.add_argument(
        "--reprice-as", metavar="MODEL_ID",
        help=(
            "Re-price each in-window dispatch's dollars at this model ID instead of its own real"
            " model, adding Counterfactual $ and Delta columns. Must be a key in"
            " _MODEL_BASE_INPUT_RATES; an unknown value is rejected listing the valid IDs."
        ),
    )
    p_mix.set_defaults(func=cmd_subagent_mix)

    p_reviewer_yield = sub.add_parser(
        "reviewer-yield",
        help=(
            "Per-reviewer-agent-type dispatch-to-verdict yield: findings-found vs."
            " zero-finding vs. unclassified, joined via each dispatch's subagents/*.meta.json."
        ),
    )
    _add_project_scope_args(p_reviewer_yield)
    p_reviewer_yield.add_argument(
        "--since", metavar="Nd",
        help="Limit to dispatches with timestamp in the last N days (e.g. 35d).",
    )
    p_reviewer_yield.add_argument(
        "--redact", action="store_true",
        help=(
            "No-op: reviewer-yield's output is aggregate-only per agent type and"
            " carries no project-label or session-id field to redact. Kept for CLI"
            " parity with cost/audit-routing."
        ),
    )
    p_reviewer_yield.set_defaults(func=cmd_reviewer_yield)

    p_pr = sub.add_parser("pr-link", help="Map branches to GitHub PRs and pull per-PR comment counts. Requires gh.")
    p_pr.add_argument("--repo", required=True, metavar="OWNER/REPO")
    p_pr.add_argument("--branches", required=True, metavar="B1,B2,...")
    p_pr.add_argument("--author", metavar="LOGIN", help="Filter comments to this GitHub login")
    _add_project_scope_args(p_pr)
    p_pr.set_defaults(func=cmd_pr_link)

    p_skill_pair = sub.add_parser(
        "skill-pair",
        help=(
            "Pairing rate between two skills, bucketed by ISO week. "
            "Counts sessions where the leader fired and whether the follower also fired (main vs sidechain-only)."
        ),
    )
    p_skill_pair.add_argument("leader", metavar="LEADER", help="Leading skill name (exact match on input.skill)")
    p_skill_pair.add_argument("follower", metavar="FOLLOWER", help="Following skill name (exact match on input.skill)")
    _add_project_scope_args(p_skill_pair)
    p_skill_pair.add_argument(
        "--exclude-projects", default=None, metavar="GLOB",
        help="Skip project dirs whose basename matches this glob.",
    )
    p_skill_pair.add_argument("--branches", metavar="B1,B2,...")
    p_skill_pair.set_defaults(func=cmd_skill_pair)

    p_gate = sub.add_parser(
        "commit-gate",
        help=(
            "Per-commit gate-compliance: did <skill> precede each commit in the same session?"
            " Optionally split by permissionMode."
        ),
    )
    p_gate.add_argument("skill", help="Skill name to check (byte-equal match against Skill tool_use input.skill).")
    p_gate.add_argument("--by-permission-mode", action="store_true", help="Split rows by permissionMode.")
    _add_project_scope_args(p_gate)
    p_gate.add_argument(
        "--exclude-projects", default=None, metavar="GLOB",
        help="Exclude project dirs whose basename matches this glob.",
    )
    p_gate.add_argument("--branches", metavar="B1,B2,...", help="Branch name filter (default: all)")
    p_gate.set_defaults(func=cmd_commit_gate)

    p_skill_inv = sub.add_parser(
        "skill-invocation",
        help=(
            "Per-skill invocation-source tally: top-level (description-dependent auto-trigger),"
            " routed (fired while another skill's body was active), and user /slash commands."
            " Identifies name-only and disable-model-invocation candidates for budget relief."
        ),
    )
    p_skill_inv_scope = p_skill_inv.add_mutually_exclusive_group()
    p_skill_inv_scope.add_argument(
        "--projects", default=None, metavar="GLOB",
        help="Project-dir glob. Default: this repo's own worktrees only (publish-safe). "
             "Passing an explicit glob is an escape hatch — output is then not scoped to this repo.",
    )
    p_skill_inv_scope.add_argument(
        "--this-repo", action="store_true",
        help=(
            "Explicit no-op: skill-invocation already defaults to this repo's own "
            "worktrees. Kept for flag uniformity with every other --projects subcommand."
        ),
    )
    p_skill_inv.add_argument("--branches", metavar="B1,B2,...", help="Branch name filter (default: all)")
    p_skill_inv.add_argument(
        "--include-subagents", action="store_true",
        help="Count skill invocations inside spawned subagents too, split by a thread column.",
    )
    p_skill_inv.set_defaults(func=cmd_skill_invocation)

    p_review_trace = sub.add_parser(
        "review-trace",
        help=(
            "Ordered review-event timeline per session: skill invocations, hook denials,"
            " and reviewer-agent spawns."
        ),
    )
    _add_project_scope_args(p_review_trace)
    p_review_trace.add_argument("--branches", metavar="B1,B2,...")
    p_review_trace.add_argument("--since", metavar="DATE", type=_iso_date, help="Inclusive start date (YYYY-MM-DD)")
    p_review_trace.add_argument("--until", metavar="DATE", type=_iso_date, help="Inclusive end date (YYYY-MM-DD)")
    p_review_trace.add_argument(
        "--deny-only", action="store_true",
        help="Restrict output to sessions that contain at least one hook denial.",
    )
    p_review_trace.add_argument(
        "--deny-summary", action="store_true",
        help=(
            "Replace the per-session event listing with grouped denial-count"
            " tables — by originating hook/gate, by attempted command shape"
            " (git commit / git checkout / git push / other), and a cross-tab"
            " of the two — plus the corpus date window covered."
        ),
    )
    p_review_trace.add_argument(
        "--skill", metavar="NAME", choices=sorted(REVIEW_TRACE_SKILLS),
        help="Restrict skill-invocation matching to one skill name.",
    )
    p_review_trace.set_defaults(func=cmd_review_trace)

    p_jp = sub.add_parser(
        "judgment-pair",
        help=(
            "Extract (review-skill output, user response) pairs from sessions"
            " where a review skill was invoked."
        ),
    )
    _add_project_scope_args(p_jp)
    p_jp.add_argument("--branches", metavar="B1,B2,...")
    p_jp.add_argument("--since", metavar="DATE", type=_iso_date, help="Inclusive start date (YYYY-MM-DD)")
    p_jp.add_argument("--until", metavar="DATE", type=_iso_date, help="Inclusive end date (YYYY-MM-DD)")
    p_jp.add_argument(
        "--skills",
        metavar="SKILL1,SKILL2,...",
        default=",".join(REVIEW_SKILLS),
        help=f"Comma-separated skill names to match (default: {','.join(REVIEW_SKILLS)}).",
    )
    p_jp.add_argument(
        "--truncate-chars",
        type=int,
        default=1000,
        metavar="N",
        help="Maximum characters for the review output block (default: 1000).",
    )
    p_jp.add_argument(
        "--out",
        metavar="PATH",
        default=None,
        help="Write output to this file instead of stdout.",
    )
    p_jp.set_defaults(func=cmd_judgment_pair)

    p_audit = sub.add_parser(
        "audit-routing",
        help=(
            "Per-turn Opus token breakdown by routing class (orchestration, judgment, code-write,"
            " code-read, pure-thinking, other). Aggregates output_tokens and cache_read_input_tokens"
            " per class across all sessions."
        ),
    )
    _add_project_scope_args(p_audit)
    p_audit.add_argument(
        "--since", metavar="Nd",
        help="Limit to turns with timestamp in the last N days (e.g. 35d).",
    )
    p_audit.add_argument(
        "--top", type=int, default=20, metavar="N",
        help="Maximum number of per-session rows to emit (default: 20).",
    )
    p_audit.add_argument(
        "--redact", action="store_true",
        help=(
            "Replace project dir names with anonymized labels (private-project-1, private-project-2, …)"
            " for public reporting. 'claude-config' is preserved as-is."
        ),
    )
    p_audit.set_defaults(func=cmd_audit_routing)

    p_cost = sub.add_parser(
        "cost",
        help=(
            "Price-weighted dollar cost by token class (cache read/write/output/input), model ID,"
            " and context-at-turn bucket, plus top-N sessions by dollars. Redacted by default."
        ),
    )
    _add_project_scope_args(p_cost)
    p_cost.add_argument(
        "--config-dir", action="append", dest="extra_config_dirs", metavar="DIR",
        help=(
            "Additional Claude Code config directory to scan (repeatable). The default resolved"
            " config dir is always scanned first. Each supplied directory must contain a projects/"
            " subdirectory, or it is rejected. Composes with --this-repo, scoping to this repo"
            " across every resulting root; --no-redact is refused once this puts more than one"
            " root in scope."
        ),
    )
    p_cost.add_argument(
        "--since", metavar="Nd",
        help="Limit to turns with timestamp in the last N days (e.g. 35d).",
    )
    p_cost.add_argument(
        "--top", type=int, default=20, metavar="N",
        help="Maximum number of per-session rows in the top-N-by-dollars section (default: 20).",
    )
    p_cost.add_argument(
        "--by-project", action="store_true",
        help=(
            "Add a per-project cost breakdown, keyed on (account root, project family)."
            " Composes with --projects and --this-repo; one repo's own worktrees"
            " collapse into a single row instead of fragmenting per branch."
        ),
    )
    p_cost.add_argument(
        "--no-redact", action="store_true",
        help=(
            "Emit real project names and session IDs instead of anonymized labels."
            " Never publish --no-redact output — see docs/transcript-analysis.md."
            " Refused when --config-dir puts more than one root in scope."
        ),
    )
    p_cost.add_argument("--branches", metavar="B1,B2,...", help="Branch name filter (default: all)")
    p_cost.add_argument(
        "--summary", action="store_true",
        help=(
            "Compact, aggregate-only block for a PR body: dollars and tokens by token class,"
            " model ID, and thread, plus session and priced-turn counts — no per-session or"
            " per-project row. Requires --this-repo; refuses --projects, --by-project,"
            " --no-redact, and --config-dir."
        ),
    )
    p_cost.set_defaults(func=cmd_cost)

    p_context_dist = sub.add_parser(
        "context-distribution",
        help=(
            "Per-session peak context-at-turn, bucketed both at candidate threshold percentages"
            " (30/40/50/60%%) of the model's context window and at candidate absolute-token"
            " thresholds, with each threshold's session-share and dollar-cost share."
            " Redacted by default."
        ),
    )
    _add_project_scope_args(p_context_dist)
    p_context_dist.add_argument(
        "--config-dir", action="append", dest="extra_config_dirs", metavar="DIR",
        help=(
            "Additional Claude Code config directory to scan (repeatable). The default resolved"
            " config dir is always scanned first. Each supplied directory must contain a projects/"
            " subdirectory, or it is rejected. Composes with --this-repo, scoping to this repo"
            " across every resulting root; --no-redact is refused once this puts more than one"
            " root in scope."
        ),
    )
    p_context_dist.add_argument(
        "--since", metavar="Nd",
        help="Limit to turns with timestamp in the last N days (e.g. 35d).",
    )
    p_context_dist.add_argument(
        "--no-redact", action="store_true",
        help=(
            "This report's output is aggregate-only (no project names or session IDs), so"
            " --no-redact has no effect on its content, but it still prints the DO NOT PUBLISH"
            " banner and enforces the same multi-root refusal as cost, for CLI parity."
            " Refused when --config-dir puts more than one root in scope."
        ),
    )
    p_context_dist.set_defaults(func=cmd_context_distribution)

    p_edit_format = sub.add_parser(
        "edit-format",
        help=(
            "Edit/Write/MultiEdit call census: per-tool failure classification, governance-hook"
            " re-bucketing, not_found cause attribution, and old_string/new_string/Write token"
            " overhead. Aggregate-only output; redacted by default."
        ),
    )
    _add_project_scope_args(p_edit_format)
    p_edit_format.add_argument(
        "--config-dir", action="append", dest="extra_config_dirs", metavar="DIR",
        help=(
            "Additional Claude Code config directory to scan (repeatable). The default resolved"
            " config dir is always scanned first. Each supplied directory must contain a projects/"
            " subdirectory, or it is rejected. Composes with --this-repo, scoping to this repo"
            " across every resulting root; --no-redact is refused once this puts more than one"
            " root in scope."
        ),
    )
    p_edit_format.add_argument(
        "--no-redact", action="store_true",
        help=(
            "This report's output is aggregate-only (no project names or session IDs), so"
            " --no-redact has no effect on its content, but it still prints the DO NOT PUBLISH"
            " banner and enforces the same multi-root refusal as cost, for CLI parity."
            " Refused when --config-dir puts more than one root in scope."
        ),
    )
    p_edit_format.set_defaults(func=cmd_edit_format)

    p_read_scope = sub.add_parser(
        "read-scope",
        help=(
            "Read-call scope census: offset/limit/pages classification against the full call"
            " count, result-token distribution by targeted/whole-file cohort and main/subagent"
            " scope, repeat-whole-file-read aggregates, and prompt-token growth."
            " Aggregate-only output; redacted by default."
        ),
    )
    _add_project_scope_args(p_read_scope)
    p_read_scope.add_argument(
        "--config-dir", action="append", dest="extra_config_dirs", metavar="DIR",
        help=(
            "Additional Claude Code config directory to scan (repeatable). The default resolved"
            " config dir is always scanned first. Each supplied directory must contain a projects/"
            " subdirectory, or it is rejected. Refused together with --this-repo or --no-redact."
        ),
    )
    p_read_scope.add_argument(
        "--since", metavar="Nd",
        help="Limit the prompt-token growth figure to deltas whose owning turn falls in the last N days (e.g. 35d).",
    )
    p_read_scope.add_argument(
        "--no-redact", action="store_true",
        help=(
            "This report's output is aggregate-only (no project names, session IDs, or file"
            " paths), so --no-redact has no effect on its content, but it still prints the"
            " DO NOT PUBLISH banner and enforces the same multi-root refusal as cost, for CLI"
            " parity. Refused when --config-dir puts more than one root in scope."
        ),
    )
    p_read_scope.set_defaults(func=cmd_read_scope)

    p_cost_trend = sub.add_parser(
        "cost-trend",
        help="Per-ISO-week dollar spend, Opus-family share, and >=200k context-bucket share.",
    )
    _add_project_scope_args(p_cost_trend)
    p_cost_trend.add_argument(
        "--config-dir", action="append", dest="extra_config_dirs", metavar="DIR",
        help=(
            "Additional Claude Code config directory to scan (repeatable). The default resolved"
            " config dir is always scanned first. Each supplied directory must contain a projects/"
            " subdirectory, or it is rejected. Composes with --this-repo, scoping to this repo"
            " across every resulting root."
        ),
    )
    p_cost_trend.set_defaults(func=cmd_cost_trend)

    p_cost_ledger = sub.add_parser(
        "cost-ledger",
        help=(
            "Read or append the local per-week cost/efficiency ledger (see COST_LEDGER_PATH)."
            " Default: print existing rows plus any live-corpus weeks not yet recorded."
        ),
    )
    _add_project_scope_args(p_cost_ledger)
    p_cost_ledger.add_argument(
        "--record", action="store_true",
        help="Append the current ISO week's row. Requires ~/.claude/.cost-ledger-enabled and --machine-label.",
    )
    p_cost_ledger.add_argument(
        "--machine-label", metavar="LABEL",
        help="Opaque per-machine label for --record: ^[a-z0-9]{1,8}$, must not equal this machine's hostname.",
    )
    p_cost_ledger.add_argument(
        "--force", action="store_true",
        help="With --record, overwrite an existing row for the same (week, machine) instead of refusing.",
    )
    p_cost_ledger.add_argument(
        "--note", metavar="TEXT", default="",
        help="Free-text note for --record's row: what changed in the workflow this week.",
    )
    p_cost_ledger.set_defaults(func=cmd_cost_ledger)

    p_handoff_ratio = sub.add_parser(
        "handoff-ratio",
        help=(
            "Per-week handoff-vs-compaction ratio: how often sessions use /handoff"
            " versus waiting for auto-compaction."
        ),
    )
    _add_project_scope_args(p_handoff_ratio)
    p_handoff_ratio.add_argument("--since", metavar="DATE", type=_iso_date, help="Inclusive start date (YYYY-MM-DD)")
    p_handoff_ratio.add_argument(
        "--debug-detector",
        action="store_true",
        help="Print candidate compaction records for inspection (useful when schema is uncertain).",
    )
    p_handoff_ratio.set_defaults(func=cmd_handoff_ratio)

    p_rearm_backtest = sub.add_parser(
        "rearm-backtest",
        help=(
            "Backtest candidate re-arm band spacings for the handoff nudge's one-shot fire"
            " against the recorded corpus: predicted total $ and C_bar per spacing, under both"
            " perfect-compliance and compliance-realistic operator-response models."
            " Redacted by default."
        ),
    )
    _add_project_scope_args(p_rearm_backtest)
    p_rearm_backtest.add_argument(
        "--config-dir", action="append", dest="extra_config_dirs", metavar="DIR",
        help=(
            "Additional Claude Code config directory to scan (repeatable). The default resolved"
            " config dir is always scanned first. Each supplied directory must contain a projects/"
            " subdirectory, or it is rejected. Composes with --this-repo, scoping to this repo"
            " across every resulting root; --no-redact is refused once this puts more than one"
            " root in scope."
        ),
    )
    p_rearm_backtest.add_argument(
        "--since", metavar="Nd",
        help="Limit to sessions with a first timestamp in the last N days (e.g. 35d); whole-session scope.",
    )
    p_rearm_backtest.add_argument(
        "--branches", metavar="B1,B2,...",
        help="Whole-session branch filter: a session with no matching main-thread turn is excluded (default: all).",
    )
    p_rearm_backtest.add_argument(
        "--no-redact", action="store_true",
        help=(
            "This report's output is aggregate-only (no project names or session IDs), so"
            " --no-redact has no effect on its content, but it still prints the DO NOT PUBLISH"
            " banner and enforces the same multi-root refusal as cost, for CLI parity."
            " Refused when --config-dir puts more than one root in scope."
        ),
    )
    p_rearm_backtest.add_argument(
        "--spacings", metavar="N1,N2,...", default="40000,80000,120000",
        help="Comma-separated candidate re-arm spacings in tokens past the first fire (default: 40000,80000,120000).",
    )
    p_rearm_backtest.set_defaults(func=cmd_rearm_backtest)

    p_audit_shape = sub.add_parser(
        "audit-routing-shape",
        help=(
            "Turn-shape distributions for Opus code-read turns: files-Read per turn (D1),"
            " code-read streak lengths (D2), and read-then-edit ratio (D3)."
        ),
    )
    _add_project_scope_args(p_audit_shape)
    p_audit_shape.add_argument(
        "--since", metavar="Nd",
        help="Limit to turns with timestamp in the last N days (e.g. 35d).",
    )
    p_audit_shape.set_defaults(func=cmd_audit_routing_shape)

    p_audit_samples = sub.add_parser(
        "audit-routing-samples",
        help=(
            "Emit a random sample of Opus code-read turns with prior-user context and"
            " next-turn lookahead classification. JSON array output for manual curation."
        ),
    )
    _add_project_scope_args(p_audit_samples)
    p_audit_samples.add_argument(
        "--since", metavar="Nd",
        help="Limit to turns with timestamp in the last N days (e.g. 35d).",
    )
    p_audit_samples.add_argument(
        "--sample", type=int, default=30, metavar="N",
        help="Maximum number of sample turns to emit (default: 30).",
    )
    p_audit_samples.add_argument(
        "--seed", type=int, default=None, metavar="N",
        help="Random seed for reproducible sampling.",
    )
    p_audit_samples.add_argument(
        "--format", choices=["json", "md"], default="json", dest="output_format",
        help="Output format: json (default) or md (human-readable markdown for curation).",
    )
    p_audit_samples.set_defaults(func=cmd_audit_routing_samples)

    p_sessions = sub.add_parser(
        "sessions",
        help="Emit transcript file paths for the resolved scope, one absolute path per line.",
    )
    _add_project_scope_args(p_sessions)
    p_sessions.add_argument(
        "--paths", action="store_true",
        help="Print one absolute transcript path per line (the only sessions action today; required).",
    )
    p_sessions.add_argument(
        "--include-subagents", action="store_true",
        help="Also emit split subagent transcript paths under <session>/subagents/*.jsonl.",
    )
    p_sessions.set_defaults(func=cmd_sessions)

    p_friction = sub.add_parser(
        "friction-count",
        help=(
            "Composite friction-signal count (hook denials + failed test runs +"
            " user-correction phrases) for a single transcript file."
        ),
    )
    p_friction.add_argument(
        "--transcript", required=True, metavar="PATH",
        help="Path to one transcript .jsonl file.",
    )
    p_friction.add_argument(
        "--checkpoint", metavar="PATH", default=None,
        help=(
            "Path to a checkpoint file for incremental scanning: only bytes"
            " appended to the transcript since the checkpoint's stored offset"
            " are parsed, and the cumulative composite (not just this call's"
            " delta) is printed. An absent or malformed checkpoint fails open"
            " to a full scan from offset 0. Without this flag, every call"
            " does a full scan with no state read or written."
        ),
    )
    p_friction.add_argument(
        "--json", action="store_true",
        help="Emit the per-signal breakdown as JSON instead of the composite integer.",
    )
    p_friction.set_defaults(func=cmd_friction_count)

    return parser


def main() -> None:
    parser = build_parser()
    parsed = parser.parse_args()
    if parsed.config_dir:
        # These subcommands resolve their own scan roots via their own
        # --config-dir (_resolve_cost_roots), never reading the reassignment
        # below -- refuse outright rather than let the two same-named flags
        # silently diverge (this top-level one would validate one account
        # while the subcommand scans another).
        if parsed.subcommand in _SUBCOMMANDS_WITH_OWN_CONFIG_DIR:
            print(
                f"{parsed.subcommand}: the top-level --config-dir has no effect here, since "
                f"this subcommand resolves its own scan roots via its own --config-dir "
                f"(repeatable, additive) -- use that instead: "
                f"transcript-analysis.py {parsed.subcommand} --config-dir PATH",
                file=sys.stderr,
            )
            sys.exit(2)
        # Every other subcommand's scan roots funnel through _resolve_scan_roots,
        # which reads parsed.config_dir directly for its override branch rather
        # than through this reassignment -- so this can no longer diverge from
        # a subcommand's actual scan roots the way it could before.
        global PROJECTS_DIR
        PROJECTS_DIR = Path(parsed.config_dir) / "projects"
    parsed.func(parsed)


if __name__ == "__main__":
    main()
