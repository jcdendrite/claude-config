#!/usr/bin/env python3
"""transcript-analysis.py — Claude Code transcript analysis toolkit.
pr-link is the only subcommand that touches the network (via gh).
judgment-pair --out writes a file; all other subcommands are read-only.
"""

import argparse
import contextlib
import errno
import fnmatch
import hashlib
import json
import os
import random
import re
import shlex
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from _config_dir import config_dir

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


def _read_session_file(jsonl: Path, include_subagents: bool) -> list[dict]:
    """Read one transcript file's records, merging its subagent files when asked.

    Shared by iter_sessions (which selects files by glob) and _iter_scoped_sessions
    (which selects project dirs by exact-name identity, then reads each file).
    Keeping the per-file read here means the two selection strategies cannot drift
    in how they parse records or merge subagent files.

    When include_subagents=True, records from split subagent files under
    <session_id>/subagents/*.jsonl are appended. Those files carry
    isSidechain: true on assistant records. Returns [] for an unreadable file.
    """
    records: list[dict] = []
    try:
        with open(jsonl) as fh:
            for raw in fh:
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                records.append(rec)
    except OSError:
        return []

    if include_subagents:
        subagent_dir = jsonl.parent / jsonl.stem / SUBAGENT_SUBDIR
        if subagent_dir.is_dir():
            for sub_jsonl in sorted(subagent_dir.glob("*.jsonl")):
                try:
                    with open(sub_jsonl) as fh:
                        for raw in fh:
                            try:
                                rec = json.loads(raw)
                            except json.JSONDecodeError:
                                continue
                            records.append(rec)
                except OSError:
                    continue

    return records


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
    session_iter, scope_label = _resolve_project_scope(args, "buckets")
    _print_resolved_scope("buckets", scope_label)

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
    session_iter, scope_label = _resolve_project_scope(args, "fail-seq")
    _print_resolved_scope("fail-seq", scope_label)

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
    session_iter, scope_label = _resolve_project_scope(args, "struggle")
    _print_resolved_scope("struggle", scope_label)

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
    session_iter, scope_label = _resolve_project_scope(args, "duration")
    _print_resolved_scope("duration", scope_label)

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
    """
    branch_filter = _branch_filter(args)
    session_iter, scope_label = _resolve_project_scope(args, "subagents", include_subagents=True)
    _print_resolved_scope("subagents", scope_label)

    branch_data: dict[str, dict[str, dict[str, int]]] = defaultdict(
        lambda: {"main": defaultdict(int), "sidechain": defaultdict(int)}
    )
    branch_bytes: dict[str, dict[str, int]] = defaultdict(lambda: {"main": 0, "sidechain": 0})
    corpus_spawns = 0
    corpus_sidechain_turns = 0

    for _jsonl, records in session_iter:
        corpus_spawns += _count_subagent_spawns(records)
        for rec in records:
            rec_type = rec.get("type")
            if rec_type == "assistant":
                # corpus_sidechain_turns counts every isSidechain assistant
                # turn read, before the branch filter below — it feeds
                # _warn_if_subagent_format_drift's corpus-wide sanity check,
                # not the per-branch table, so it must not be filtered.
                if bool(rec.get("isSidechain")):
                    corpus_sidechain_turns += 1
                branch = rec.get("gitBranch") or ""
                if not branch or (branch_filter and branch not in branch_filter):
                    continue
                fam = _fam((rec.get("message") or {}).get("model", ""))
                thread = "sidechain" if bool(rec.get("isSidechain")) else "main"
                branch_data[branch][thread][fam] += 1
            elif rec_type == "user":
                branch = rec.get("gitBranch") or ""
                if not branch or (branch_filter and branch not in branch_filter):
                    continue
                thread = "sidechain" if bool(rec.get("isSidechain")) else "main"
                content = (rec.get("message") or {}).get("content") or []
                if not isinstance(content, list):
                    continue
                for block in content:
                    if not isinstance(block, dict) or block.get("type") != "tool_result":
                        continue
                    branch_bytes[branch][thread] += len(_content_text(block.get("content", "")).encode())

    _warn_if_subagent_format_drift(corpus_spawns, corpus_sidechain_turns)

    if not branch_data and not branch_bytes:
        print("No data found.")
        return

    print(
        f"{'Branch':<40} {'Thread':<10} {'Opus':>6} {'Sonnet':>7} {'Haiku':>6} {'Other':>6}"
        f" {'Bytes':>18}"
    )
    print("-" * 99)
    for branch in sorted(set(branch_data) | set(branch_bytes)):
        first = True
        for thread in ("main", "sidechain"):
            d = branch_data[branch][thread]
            bytes_total = branch_bytes[branch][thread]
            if not any(d.values()) and not bytes_total:
                continue
            label = branch if first else ""
            first = False
            print(
                f"{label:<40} {thread:<10} {d.get('opus', 0):>6} {d.get('sonnet', 0):>7} "
                f"{d.get('haiku', 0):>6} {d.get('other', 0):>6} {bytes_total:>18,}"
            )


REVIEW_SKILLS: tuple[str, ...] = ("code-review", "plan-review", "ready-for-review")

# Subdirectory name where Claude Code writes split subagent transcripts.
SUBAGENT_SUBDIR = "subagents"

# Tool names that spawn a subagent in the main thread.
_SPAWN_TOOL_NAMES = ("Agent", "Task")

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

# Reviewer-agent subagent_type prefixes/names counted in review-trace.
_REVIEWER_PREFIX = "staff-"
_REVIEWER_EXACT = "ciso-reviewer"

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
    - reviewer: Agent/Task spawn where subagent_type starts with 'staff-' or == 'ciso-reviewer'

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
    """
    branch_filter = _branch_filter(args)
    deny_only: bool = bool(getattr(args, "deny_only", False))
    deny_summary: bool = bool(getattr(args, "deny_summary", False))
    skill_filter: str | None = getattr(args, "skill", None) or None
    session_iter, scope_label = _resolve_project_scope(args, "review-trace")

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

    # The scope header prints lazily, on the first emitted block — not
    # unconditionally up front — so a run that matches no session still
    # produces byte-for-byte empty output, as it always has.
    scope_header_printed = False

    # --deny-summary's corpus-wide accumulators: hook/gate name -> count,
    # attempted-command shape -> count, (hook, shape) pair -> count (the
    # cross-tab a marginal count alone can't express), friction kind -> count.
    # Populated below in place of the normal per-session printing when
    # --deny-summary is set.
    hook_counts: dict[str, int] = defaultdict(int)
    command_shape_counts: dict[str, int] = defaultdict(int)
    hook_shape_counts: Counter[tuple[str, str]] = Counter()
    friction_counts: dict[str, int] = defaultdict(int)
    # Earliest/latest in-scope record timestamp, and a count of errored,
    # non-gate tool results timestamped before toolDenialKind existed — both
    # --deny-summary only, so the flag's off-path pays no extra bookkeeping.
    corpus_min_ts: float | None = None
    corpus_max_ts: float | None = None
    pre_regime_tool_result_count = 0
    any_session_matched = False

    for jsonl, records in session_iter:
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
                        if not (stype.startswith(_REVIEWER_PREFIX) or stype == _REVIEWER_EXACT):
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
                    deny_summary
                    and not tool_denial_kind
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

        if not events:
            continue
        any_session_matched = True

        # Corpus window reads the full branch-filtered per-session events list
        # before the deny_only skip below, same as the friction tally above —
        # so the reported window matches whatever --branches/--since/--until
        # actually put in scope, not the pre-branch-filter raw record range.
        if deny_summary:
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
        if deny_summary:
            for evt in events:
                if evt["kind"] == "friction":
                    friction_counts[_friction_kind_label(evt["friction_kind"])] += 1

        if deny_only and not has_denial:
            continue

        if deny_summary:
            for evt in events:
                if evt["kind"] != "denial":
                    continue
                hook_label = _denial_hook_label(evt["hook_name"], evt["message"])
                command = tool_use_commands.get(evt["tool_use_id"], "")
                command_shape = _denial_command_shape(command)
                hook_counts[hook_label] += 1
                command_shape_counts[command_shape] += 1
                hook_shape_counts[(hook_label, command_shape)] += 1
            continue

        skill_count = sum(1 for e in events if e["kind"] == "skill")
        denial_count = sum(1 for e in events if e["kind"] == "denial")
        spawn_count = sum(1 for e in events if e["kind"] == "reviewer-spawn")
        branches_seen = ",".join(sorted({e["branch"] for e in events}))
        models_seen = ",".join(sorted({e["model"] for e in events}))

        if not scope_header_printed:
            _print_resolved_scope("review-trace", scope_label)
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

    if deny_summary:
        if sum(hook_counts.values()) or sum(friction_counts.values()):
            if not scope_header_printed:
                _print_resolved_scope("review-trace", scope_label)
                scope_header_printed = True
            _print_deny_summary(
                hook_counts, command_shape_counts, hook_shape_counts, friction_counts,
                pre_regime_tool_result_count, corpus_min_ts, corpus_max_ts,
            )
        elif any_session_matched:
            # Scope resolved and had matching sessions, but none carried a
            # denial — printed explicitly so this reads distinctly from a
            # broken --branches/scope flag matching no sessions at all.
            if not scope_header_printed:
                _print_resolved_scope("review-trace", scope_label)
                scope_header_printed = True
            print("\nNo denials found in scope.")


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
    session_iter, scope_label = _resolve_project_scope(args, "judgment-pair")

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
        _print_resolved_scope("judgment-pair", scope_label)
        print("No judgment pairs found.")
        return

    output_text = "\n\n".join(output_blocks)

    if out_path:
        # Nothing goes to stdout in this branch — --out means the caller wants
        # only the file written. The scope header is still prepended to the
        # file's content so a saved/curated file stays self-documenting about
        # its scope even if pasted elsewhere without the terminal output.
        header = _resolved_scope_header("judgment-pair", scope_label)
        Path(out_path).write_text(header + "\n" + output_text + "\n")
    else:
        _print_resolved_scope("judgment-pair", scope_label)
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
    """
    if roots is None:
        roots = (PROJECTS_DIR,)
    wanted = set(slugs)
    visited_dirs: set[Path] = set()
    for root in roots:
        if not root.is_dir():
            continue
        candidates = (p for p in sorted(root.iterdir()) if p.name in wanted)
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
    for root in roots:
        for project_dir in _dedup_new_project_dirs(sorted(root.glob(projects_glob)), visited_dirs):
            for jsonl in sorted(project_dir.glob("*.jsonl")):
                records = _read_session_file(jsonl, include_subagents)
                if records:
                    yield jsonl, records


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

    `roots` defaults to (PROJECTS_DIR,), so a caller that never passes
    --config-dir is unaffected — currently cost and context-distribution are
    the only subcommands that accept it. --this-repo and multi-root are
    mutually exclusive (enforced at each such subcommand's CLI boundary), so
    the this_repo branch always has a single root in practice; it still
    threads `roots` through for signature parity.

    Under an explicit top-level --config-dir (a different flag from cost's
    and context-distribution's own --config-dir above — see main()), zero of
    the resolved slugs matching an actual directory fails closed
    (sys.exit(1)) instead of returning an iterator that silently yields
    nothing — this is the original reported symptom (declaring no sessions
    exist for a container that has them), so an empty --this-repo scope here
    is far more likely a wrong --config-dir than a genuinely session-less
    repo. Without --config-dir, an empty scope stays silent, matching every
    other subcommand's long-standing behavior. config_dir is read via
    getattr (unlike this_repo above): it's a top-level parser flag rather
    than something _add_project_scope_args wires per subparser, and this
    file's many hand-built test `args` fixtures predate it, so its absence
    means "not passed" (the real default), not a wiring bug. This check
    reads the reassigned PROJECTS_DIR global (set in main()); main() refuses
    the top-level --config-dir outright for both cost and
    context-distribution before dispatch, so PROJECTS_DIR and `roots` can
    never diverge here -- config_dir_arg being truthy guarantees `roots` is
    None (those two subcommands are the only callers that ever pass a
    non-None roots).
    """
    if args.this_repo:
        slugs = getattr(args, "_this_repo_slugs", None)
        if slugs is None:
            slugs = _repo_scoped_project_slugs(subcommand)
            args._this_repo_slugs = slugs
        config_dir_arg = getattr(args, "config_dir", None)
        if config_dir_arg and not any((PROJECTS_DIR / slug).is_dir() for slug in slugs):
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
    if roots is None:
        roots = (PROJECTS_DIR,)
    if len(roots) == 1:
        return iter_sessions(roots[0], glob, include_subagents=include_subagents), glob
    return _iter_glob_scoped_sessions(roots, glob, include_subagents), glob


def _resolved_scope_header(subcommand: str, scope_label: str) -> str:
    """Build the one-line resolved-scope header text, shared by _print_resolved_scope
    and any caller (e.g. judgment-pair's --out file) that needs the header written
    somewhere other than a live print call."""
    return f"{subcommand.upper().replace('-', ' ')} SOURCES ({scope_label})"


def _print_resolved_scope(subcommand: str, scope_label: str, *, file=None) -> None:
    """Print the one-line resolved-scope header cmd_skill_invocation already uses,
    so machine-wide vs. --this-repo output is never scope-ambiguous. `file` defaults
    to stdout (resolved at call time, not import time — a `sys.stdout` default
    value would bind the stream object process startup captured, bypassing test
    capture and any later reassignment); audit-routing-samples routes it to stderr
    instead, since its stdout is a JSON (or curation-markdown) data stream a header
    line would corrupt."""
    print(_resolved_scope_header(subcommand, scope_label), file=file or sys.stdout)


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
    projects_arg = getattr(args, "projects", None)
    if projects_arg:
        session_iter = iter_sessions(PROJECTS_DIR, projects_arg, include_subagents=include_subagents)
    else:
        session_iter = _iter_scoped_sessions(_repo_scoped_project_slugs(), include_subagents)

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
    print(f"SKILL INVOCATION SOURCES ({'; '.join(scope_parts)})")

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
    branch_filter = _branch_filter(args)
    per_session: bool = bool(getattr(args, "per_session", False))
    session_iter, scope_label = _resolve_project_scope(args, "subagent-mix")
    _print_resolved_scope("subagent-mix", scope_label)

    data: dict[str, dict] = defaultdict(
        lambda: {"sessions": 0, "spawns": defaultdict(int), "skills": defaultdict(int)}
    )

    for jsonl, records in session_iter:
        session_data: dict[str, dict] = defaultdict(
            lambda: {"spawns": defaultdict(int), "skills": defaultdict(int)}
        )
        for rec in records:
            if rec.get("type") != "assistant" or bool(rec.get("isSidechain")):
                continue
            branch = rec.get("gitBranch") or ""
            if not branch or (branch_filter and branch not in branch_filter):
                continue
            for block in ((rec.get("message") or {}).get("content") or []):
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                name = block.get("name")
                inp = block.get("input") or {}
                if name in _SPAWN_TOOL_NAMES:
                    stype = inp.get("subagent_type") or "unknown"
                    session_data[branch]["spawns"][stype] += 1
                elif name == "Skill":
                    skill = inp.get("skill") or ""
                    if skill in REVIEW_SKILLS:
                        session_data[branch]["skills"][skill] += 1

        for branch, sd in session_data.items():
            key = f"{branch} [{jsonl.stem[:8]}]" if per_session else branch
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


# Reviewer-agent subagent_type additionally counted by reviewer-yield (not
# review-trace's _REVIEWER_PREFIX/_REVIEWER_EXACT): skill-fidelity-reviewer
# doesn't match either, but is one of #558's own reviewer-agent table entries.
_REVIEWER_YIELD_EXTRA_EXACT = "skill-fidelity-reviewer"

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


def _index_subagent_dispatches(jsonl: Path) -> tuple[dict[str, Path], int]:
    """Map each subagent dispatch's toolUseId to its paired .jsonl path, for one session.

    Reads subagents/*.meta.json directly rather than through iter_sessions'
    include_subagents merge — that merge flattens every subagent file's
    records into one list with no per-file boundary, which cannot answer
    "this specific dispatch's own last assistant text."

    Returns (index, meta_read_errors): meta_read_errors counts *.meta.json
    files present but unusable — invalid JSON, or valid JSON missing
    toolUseId — distinct from a dispatch with no meta.json at all (the
    caller's own, separately-documented exclusion path).
    """
    subagent_dir = jsonl.parent / jsonl.stem / SUBAGENT_SUBDIR
    index: dict[str, Path] = {}
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
        if not tool_use_id:
            meta_read_errors += 1
            continue
        agent_id = meta_path.name.removesuffix(".meta.json")
        index[tool_use_id] = meta_path.parent / f"{agent_id}.jsonl"
    return index, meta_read_errors


def _last_assistant_text(jsonl_path: Path) -> str:
    """Return the last non-empty assistant text block in one transcript file, or ''.

    A trailing assistant record with no text (e.g. a final tool-only turn)
    does not blank out an earlier one — this walks the whole file and keeps
    the most recent non-empty text seen, matching "last assistant text
    block" rather than "last assistant record's text, possibly empty."
    """
    last_text = ""
    try:
        with open(jsonl_path) as fh:
            for raw in fh:
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") != "assistant":
                    continue
                text = _content_text((rec.get("message") or {}).get("content", ""))
                if text.strip():
                    last_text = text
    except OSError:
        return ""
    return last_text


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


def cmd_reviewer_yield(args: argparse.Namespace) -> None:
    """Per-reviewer-agent-type dispatch-to-verdict yield.

    Joins each main-thread reviewer-agent dispatch (Agent/Task tool_use with
    subagent_type in the reviewer set — review-trace's _REVIEWER_PREFIX/
    _REVIEWER_EXACT plus skill-fidelity-reviewer) to its own subagent
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

    --redact is accepted for CLI parity with cost/audit-routing; this
    subcommand's output is aggregate-only (per-agent-type rows), so there is
    currently no project-label or session-id field to redact.
    """
    since_ts: float | None = None
    since_label: str = ""
    since_raw: str | None = getattr(args, "since", None) or None
    if since_raw:
        try:
            days = float(since_raw.rstrip("d"))
            since_ts = time.time() - days * 86400
            since_label = since_raw
        except ValueError:
            print(f"reviewer-yield: --since: expected Nd like '35d', got {since_raw!r}", file=sys.stderr)
            sys.exit(1)

    session_iter, scope_label = _resolve_project_scope(args, "reviewer-yield")
    _print_resolved_scope("reviewer-yield", scope_label)

    # agent_type -> {dispatches, findings_found, zero_finding, unclassified, total_findings}
    agg: dict[str, dict[str, int]] = defaultdict(
        lambda: {"dispatches": 0, "findings_found": 0, "zero_finding": 0, "unclassified": 0, "total_findings": 0}
    )
    meta_read_errors = 0

    for jsonl, records in session_iter:
        dispatch_index, session_meta_read_errors = _index_subagent_dispatches(jsonl)
        meta_read_errors += session_meta_read_errors
        for rec in records:
            if rec.get("type") != "assistant" or bool(rec.get("isSidechain")):
                continue
            if since_ts is not None:
                rec_ts = _parse_ts(rec.get("timestamp"))
                if rec_ts is None or rec_ts < since_ts:
                    continue
            for block in ((rec.get("message") or {}).get("content") or []):
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                if block.get("name") not in _SPAWN_TOOL_NAMES:
                    continue
                stype = (block.get("input") or {}).get("subagent_type") or ""
                if not (
                    stype.startswith(_REVIEWER_PREFIX)
                    or stype in (_REVIEWER_EXACT, _REVIEWER_YIELD_EXTRA_EXACT)
                ):
                    continue
                paired_jsonl = dispatch_index.get(block.get("id") or "")
                if paired_jsonl is None:
                    continue  # no matching meta.json — excluded entirely, not "unclassified"
                bucket, n = _classify_reviewer_verdict(_last_assistant_text(paired_jsonl))
                row = agg[stype]
                row["dispatches"] += 1
                if bucket == _REVIEWER_VERDICT_FINDINGS_FOUND:
                    row["findings_found"] += 1
                    row["total_findings"] += n
                elif bucket == _REVIEWER_VERDICT_ZERO_FINDING:
                    row["zero_finding"] += 1
                else:
                    row["unclassified"] += 1

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
    session_iter, scope_label = _resolve_project_scope(args, "skill-pair", include_subagents=True)
    _print_resolved_scope("skill-pair", scope_label)

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
    session_iter, scope_label = _resolve_project_scope(args, "pr-link")

    branch_models: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for _jsonl, records in session_iter:
        for rec in records:
            branch = rec.get("gitBranch") or ""
            if branch not in branches or rec.get("type") != "assistant" or bool(rec.get("isSidechain")):
                continue
            fam = _fam((rec.get("message") or {}).get("model", ""))
            branch_models[branch][fam] += 1

    _print_resolved_scope("pr-link", scope_label)
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
    session_iter, scope_label = _resolve_project_scope(args, "commit-gate")
    _print_resolved_scope("commit-gate", scope_label)

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
    private-project-N map, unnamespaced by account. More than one root (cost's
    --config-dir) namespaces every label account-<K>/private-project-N, where
    <K> is the root's 1-based position in `roots` (scan order) — never the
    config-dir path or its basename, which would leak the account/client
    identifier the directory name encodes. <N> restarts at 1 within each
    account's own scan. Labels (and the corpus fingerprint derived from this
    map) are not comparable across two separate report runs, single- or
    multi-root: a changed corpus can renumber every ordinal.
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

    for root_idx, root in enumerate(roots):
        account_label = f"account-{root_idx + 1}"
        num_index = 1
        for label in _sorted_distinct_proj_labels(root):
            key = (root_idx, label)
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

    # _resolve_project_scope's fail-closed --this-repo check runs before
    # _build_redact_map's full-corpus disk scan, so an out-of-repo failure
    # exits without paying for that scan.
    session_iter, scope_label = _resolve_project_scope(args, "audit-routing")
    _print_resolved_scope("audit-routing", scope_label)

    redact_map: dict[str, str] = _build_redact_map() if redact else {}
    session_redact_map: dict[str, str] = {}

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
        proj_label = _derive_proj_label(jsonl)
        session_id = jsonl.stem[:12]
        if redact:
            _assign_session_redact_label(session_id, session_redact_map)

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
        proj = _redact_proj_label(row["proj_label"], redact_map) if redact else row["proj_label"]
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

_SONNET_5_PROMO_EXPIRES = date(2026, 8, 31)  # vendor-stated introductory-rate end
# Published standard rate taking effect the day after _SONNET_5_PROMO_EXPIRES
# ($3.00/MTok input, up from the $2.00 introductory rate); see
# _PRICING_SOURCE_URL. Recorded here so updating _MODEL_BASE_INPUT_RATES once
# the STALE PRICING banner fires doesn't need a fresh vendor-page lookup.
_SONNET_5_SUCCESSOR_BASE_RATE = 3.00
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
}

# Re-verify-by date per model ID: Sonnet 5's introductory rate has a
# vendor-stated end date; every other model has none, so it gets
# fetch-date+90d as a re-verify checkpoint instead.
_MODEL_RATE_EXPIRES: dict[str, date] = {
    model: (_SONNET_5_PROMO_EXPIRES if model == "claude-sonnet-5" else _DEFAULT_REVERIFY_BY)
    for model in _MODEL_BASE_INPUT_RATES
}

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


def _context_bucket(context_at_turn: int) -> str:
    return _CONTEXT_BUCKET_OVER if context_at_turn >= _CONTEXT_BUCKET_THRESHOLD else _CONTEXT_BUCKET_UNDER


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
    context_at_turn = input_t + cache_read_t + eph_1h + eph_5m

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


def _resolve_cost_roots(args: argparse.Namespace, subcommand: str = "cost") -> list[Path]:
    """Assemble a subcommand's scan roots from the default config dir plus any
    --config-dir extras, in argument order, deduped by resolved path.

    Mirrors post-crash-sessions.py:1067-1111's --config-dir contract: exit 2
    on a root that is not a directory or lacks a projects/ subdirectory.
    --this-repo cannot filter a foreign config dir's worktrees, and
    --no-redact on more than one root would put one client's real project
    names into a report meant for another — both are refused here, exit 2,
    rather than silently scoping to the wrong thing. Returns each root's
    projects/ subdirectory, ready for _resolve_project_scope's roots
    parameter.

    `subcommand` labels the printed refusal messages (default "cost", cost's
    own long-standing call sites and tests); context-distribution passes its
    own name so a refusal is attributed to the subcommand the caller actually
    invoked, not always "cost".
    """
    extra_config_dirs: list[str] = getattr(args, "extra_config_dirs", None) or []

    if args.this_repo and extra_config_dirs:
        print(
            f"{subcommand}: --this-repo and --config-dir are mutually exclusive"
            " (--this-repo cannot filter a foreign config dir's worktrees)",
            file=sys.stderr,
        )
        sys.exit(2)

    default_dir = config_dir()
    config_dirs = [default_dir]
    seen_resolved = {default_dir.resolve()}
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
    since this runs once per priced session and re-resolving every root on
    every call would be a per-element filesystem stat inside that loop.
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
    # runs once per priced session, and re-resolving every root on every call
    # would be a per-element filesystem stat inside that loop.
    resolved_scan_roots = [root.resolve() for root in scan_roots] if multi_root else []

    total_transcripts_scanned = 0
    if roots is not None:
        glob = _projects_glob(args)
        # --this-repo's slugs were already resolved (and cached on args) by
        # _resolve_project_scope above; passing them keeps this diagnostic
        # scan repo-scoped instead of falling back to _projects_glob's "*".
        this_repo_slugs = getattr(args, "_this_repo_slugs", None) if args.this_repo else None
        for idx, root in enumerate(scan_roots):
            root_label = f"account-{idx + 1}" if redact else str(root.parent)
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
    # header — nothing it prints is identity-keyed; its own scope line below
    # reports total_transcripts_scanned, the scan step's post-existence-check
    # count, instead.
    redact_map: dict[_RedactMapKey, str] = {}
    if not summary_mode:
        redact_map = _build_redact_map(roots) if redact else {}
        if redact:
            print(
                f"Corpus fingerprint: {_corpus_fingerprint(redact_map)}"
                "  (private-project labels are not comparable across a different fingerprint)"
            )
        _print_resolved_scope("cost", scope_label)

    session_redact_map: dict[str, str] = {}
    by_project: bool = bool(getattr(args, "by_project", False))

    class_totals: dict[str, float] = dict.fromkeys(_TOKEN_CLASSES, 0.0)
    class_token_totals: dict[str, int] = dict.fromkeys(_TOKEN_CLASSES, 0)
    model_totals: dict[str, float] = defaultdict(float)
    unpriced_tokens: dict[str, int] = defaultdict(int)
    bucket_totals: dict[str, float] = defaultdict(float)
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
        raw_proj_label = _derive_proj_label(jsonl)
        session_id = jsonl.stem[:12]
        if redact and not summary_mode:
            _assign_session_redact_label(session_id, session_redact_map)
        session_total = 0.0

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

            if since_ts is not None:
                rec_ts = _parse_ts(rec.get("timestamp"))
                if rec_ts is None or rec_ts < since_ts:
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
                    scoped_label: _RedactMapKey = (_root_index_for_path(jsonl, resolved_scan_roots), raw_proj_label)
                else:
                    scoped_label = raw_proj_label
                if redact:
                    proj_display = _redact_proj_label(scoped_label, redact_map)
                    if proj_display == _REDACT_MAP_MISS_TOKEN:
                        # Deliberately omits raw_proj_label: main() has no top-level
                        # exception handler, so this message would otherwise reach
                        # stderr uncaught — re-leaking the exact client-identifying
                        # string --redact exists to hide. A short hash (like
                        # _corpus_fingerprint's) and the root index are enough to
                        # debug a desync without exposing the plaintext label.
                        root_idx = scoped_label[0] if isinstance(scoped_label, tuple) else None
                        label_hash = hashlib.sha256(raw_proj_label.encode()).hexdigest()[:12]
                        root_desc = f"root {root_idx}" if root_idx is not None else "the single scan root"
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

    grand_total = sum(class_totals.values())

    # Both invariants below sum the same per-turn dollar increments (the same
    # dollars_by_class value feeds class_totals, main/subagent, and
    # project_totals in the same loop iteration) through a different
    # accumulator split — they guard the partition/bucketing logic (a branch
    # that double-counts, drops, or misroutes a turn), not _price_turn's
    # dollar math itself, since a wrong per-turn price would move both sides
    # of either comparison together. Any gap beyond float64 summation noise
    # (well under a millionth of a dollar here) still means a real bucketing
    # bug, not rounding.
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

    title_since = f"last {since_label}" if since_label else "all time"
    if summary_mode:
        print(f"\n## Cost summary ({title_since})\n")
        print(
            f"Scope: {total_transcripts_scanned:,} transcripts scanned, "
            f"{priced_session_count:,} priced sessions, {priced_turn_count:,} priced turns"
        )
    else:
        print(f"\n## Cost report ({title_since})\n")

    if stale_models:
        # claude-sonnet-5's specific successor rate is recorded so this banner
        # is actionable on sight — without it, an operator seeing the warning
        # still has to re-fetch the vendor page to learn what to update
        # _MODEL_BASE_INPUT_RATES to, which is the exact re-lookup this
        # constant exists to save.
        successor_hint = (
            f" claude-sonnet-5's recorded successor base rate is"
            f" ${_SONNET_5_SUCCESSOR_BASE_RATE:.2f}/MTok input — confirm against"
            f" {_PRICING_SOURCE_URL} before updating _MODEL_BASE_INPUT_RATES."
            if "claude-sonnet-5" in stale_models
            else ""
        )
        print(
            "STALE PRICING — today is past the re-verify-by date for: "
            + ", ".join(sorted(stale_models))
            + f". Re-check rates at {_PRICING_SOURCE_URL} before publishing the figures below."
            + successor_hint
            + "\n"
        )

    print("## Cost by token class\n")
    print(f"{'Class':<16} {'$':>14} {'Share':>7} {'Tokens':>14}")
    for cls in _TOKEN_CLASSES:
        val = class_totals[cls]
        tok = class_token_totals[cls]
        print(f"{cls:<16} {val:>14,.2f} {_pct_of(val, grand_total):>7} {tok:>14,}")
    print(f"{'total':<16} {grand_total:>14,.2f}")

    print("\n## Cost by model ID\n")
    print(f"{'Model':<28} {'$':>14} {'Share':>7}")
    for model, val in sorted(model_totals.items(), key=lambda kv: kv[1], reverse=True):
        print(f"{model:<28} {val:>14,.2f} {_pct_of(val, grand_total):>7}")
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

    if by_project:
        print("\n## Cost by project\n")
        if not project_totals:
            print("(no priced turns in range)")
        elif multi_root:
            print(f"{'Account':<12} {'Project':<24} {'$':>14} {'Share':>7}")
            for (root_idx, family), val in sorted(project_totals.items(), key=lambda kv: kv[1], reverse=True):
                account_col = f"account-{root_idx + 1}" if redact else str(scan_roots[root_idx].parent)
                repr_label = project_repr_label[(root_idx, family)]
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
    _print_resolved_scope("context-distribution", scope_label)

    title_since = f"last {since_label}" if since_label else "all time"
    print(f"\n## Context distribution report ({title_since})\n")

    session_peak_pcts: list[float] = []
    session_peak_abs_tokens: list[int] = []
    session_dollars: list[float] = []
    total_dollars = 0.0

    for _jsonl, records in session_iter:
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
    _print_resolved_scope("edit-format", scope_label)

    # Resolved once, outside the per-session loop below, mirroring cost's own
    # _root_index_for_path usage — re-resolving every root on every session
    # would be a per-element filesystem stat inside that loop.
    resolved_scan_roots = [root.resolve() for root in scan_roots] if multi_root else []

    stats = _new_edit_format_stats()
    per_account: list[dict] = [_new_edit_format_stats() for _ in scan_roots] if multi_root else []

    for jsonl, records in session_iter:
        session_stats = _scan_edit_format_session(records)
        _merge_edit_format_stats(stats, session_stats)
        if multi_root:
            idx = _root_index_for_path(jsonl, resolved_scan_roots)
            _merge_edit_format_stats(per_account[idx], session_stats)

    _print_edit_format_report(stats, per_account if multi_root else None)


def _print_edit_format_report(stats: dict, per_account: list[dict] | None) -> None:
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
        for idx, account_stats in enumerate(per_account):
            account_label = f"account-{idx + 1}"
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


def cmd_cost_trend(args: argparse.Namespace) -> None:
    """CLI entry point for the cost-trend subcommand.

    Reads the wall-clock date exactly once, here, then delegates to
    _cost_trend_report, which takes `today` as an explicit parameter — the
    same split _cost_report uses so the trailing week's "(partial)" label
    doesn't depend on a live clock read inside a function under test.
    """
    _cost_trend_report(args, datetime.now(UTC).date())


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
    """
    session_iter, scope_label = _resolve_project_scope(args, "cost-trend", include_subagents=True)
    _print_resolved_scope("cost-trend", scope_label)

    # week_str -> {"total": $, "opus": $, "context_over": $}
    data: dict[str, dict[str, float]] = defaultdict(lambda: {"total": 0.0, "opus": 0.0, "context_over": 0.0})
    unpriced_turns = 0
    unpriced_tokens = 0

    for _jsonl, records in session_iter:
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
    session_iter, scope_label = _resolve_project_scope(args, "handoff-ratio")
    _print_resolved_scope("handoff-ratio", scope_label)

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


def _print_nudge_log_diagnostic() -> None:
    """Read ~/.claude/.handoff-nudge.log and report schema-drift count if present."""
    log_path = config_dir() / ".handoff-nudge.log"
    if not log_path.exists():
        return
    try:
        MAX_LOG_READ = 2 * 1024 * 1024  # 2 MB
        if log_path.stat().st_size > MAX_LOG_READ:
            raw = log_path.read_bytes()[-MAX_LOG_READ:]
            lines = raw.decode(errors="ignore").splitlines()
        else:
            lines = log_path.read_text().splitlines()
    except OSError:
        return
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

    session_iter, scope_label = _resolve_project_scope(args, "audit-routing-shape")

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

    _print_resolved_scope("audit-routing-shape", scope_label)

    for _jsonl, records in session_iter:
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
    session_iter, scope_label = _resolve_project_scope(args, "audit-routing-samples")
    # stderr, not stdout: stdout is this subcommand's JSON/markdown data stream.
    _print_resolved_scope("audit-routing-samples", scope_label, file=sys.stderr)

    candidates: list[dict] = []

    for jsonl, records in session_iter:
        session_id = jsonl.stem

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
        help="isSidechain turn counts and model split per branch, plus tool-result bytes per thread.",
    )
    p_sub.add_argument("--branches", metavar="B1,B2,...")
    _add_project_scope_args(p_sub)
    p_sub.set_defaults(func=cmd_subagents)

    p_mix = sub.add_parser(
        "subagent-mix",
        help="Subagent_type spawn counts per branch, with code/plan/ready-for-review skill invocations.",
    )
    p_mix.add_argument("--branches", metavar="B1,B2,...")
    _add_project_scope_args(p_mix)
    p_mix.add_argument(
        "--per-session",
        action="store_true",
        help="Break out by individual session instead of aggregating per branch.",
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
            " subdirectory, or it is rejected. Refused together with --this-repo or --no-redact."
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
            " subdirectory, or it is rejected. Refused together with --this-repo or --no-redact."
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
            " subdirectory, or it is rejected. Refused together with --this-repo or --no-redact."
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

    p_cost_trend = sub.add_parser(
        "cost-trend",
        help="Per-ISO-week dollar spend, Opus-family share, and >=200k context-bucket share.",
    )
    _add_project_scope_args(p_cost_trend)
    p_cost_trend.set_defaults(func=cmd_cost_trend)

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
        # cost, context-distribution, and edit-format resolve their own scan
        # roots via their own --config-dir (_resolve_cost_roots), never
        # reading the reassignment below -- refuse outright rather than let
        # the two same-named flags silently diverge (this top-level one
        # would validate one account while the subcommand scans another).
        if parsed.subcommand in ("cost", "context-distribution", "edit-format"):
            print(
                f"{parsed.subcommand}: the top-level --config-dir has no effect here, since "
                f"this subcommand resolves its own scan roots via its own --config-dir "
                f"(repeatable, additive) -- use that instead: "
                f"transcript-analysis.py {parsed.subcommand} --config-dir PATH",
                file=sys.stderr,
            )
            sys.exit(2)
        global PROJECTS_DIR
        PROJECTS_DIR = Path(parsed.config_dir) / "projects"
    parsed.func(parsed)


if __name__ == "__main__":
    main()
