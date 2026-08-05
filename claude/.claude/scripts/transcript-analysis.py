#!/usr/bin/env python3
"""transcript-analysis.py — Claude Code transcript analysis toolkit.
pr-link is the only subcommand that touches the network (via gh).
judgment-pair --out writes a file; all other subcommands are read-only.
"""

import argparse
import contextlib
import fnmatch
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from collections.abc import Iterator
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
    branch_filter = _branch_filter(args)
    session_iter, scope_label = _resolve_project_scope(args, "subagents", include_subagents=True)
    _print_resolved_scope("subagents", scope_label)

    branch_data: dict[str, dict[str, dict[str, int]]] = defaultdict(
        lambda: {"main": defaultdict(int), "sidechain": defaultdict(int)}
    )
    corpus_spawns = 0
    corpus_sidechain_turns = 0

    for _jsonl, records in session_iter:
        corpus_spawns += _count_subagent_spawns(records)
        for rec in records:
            if rec.get("type") != "assistant":
                continue
            if bool(rec.get("isSidechain")):
                corpus_sidechain_turns += 1
            branch = rec.get("gitBranch") or ""
            if not branch or (branch_filter and branch not in branch_filter):
                continue
            fam = _fam((rec.get("message") or {}).get("model", ""))
            thread = "sidechain" if bool(rec.get("isSidechain")) else "main"
            branch_data[branch][thread][fam] += 1

    _warn_if_subagent_format_drift(corpus_spawns, corpus_sidechain_turns)

    if not branch_data:
        print("No data found.")
        return

    print(f"{'Branch':<40} {'Thread':<10} {'Opus':>6} {'Sonnet':>7} {'Haiku':>6} {'Other':>6}")
    print("-" * 83)
    for branch in sorted(branch_data):
        first = True
        for thread in ("main", "sidechain"):
            d = branch_data[branch][thread]
            if not any(d.values()):
                continue
            label = branch if first else ""
            first = False
            print(
                f"{label:<40} {thread:<10} {d.get('opus', 0):>6} {d.get('sonnet', 0):>7} "
                f"{d.get('haiku', 0):>6} {d.get('other', 0):>6}"
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

# Current-format transcripts record a hook denial as an is_error tool_result
# with no structured marker — it is distinguishable from an ordinary tool
# error only by the deny message text. These patterns match the Claude Code
# hook-denial idiom ("Blocked by <hook>", "blocked by <X> gate", "… invocation
# denied"). Detection is therefore best-effort in both directions: an
# atypically worded hook denial is missed, and an ordinary tool error whose
# text happens to contain the idiom is a false positive. review-trace is a
# candidate locator, not an exact counter — callers treat denial counts as
# approximate. Legacy transcripts additionally carry an explicit
# hook_blocking_error attachment record, matched separately and exactly.
_HOOK_DENIAL_SIGNATURE = re.compile(
    r"blocked by .{0,80}?\b(?:hook|gate)\b|invocation denied\b",
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


def cmd_review_trace(args: argparse.Namespace) -> None:
    """Emit an ordered review-event timeline per session.

    Three event types are detected per session:
    - skill: main-thread Skill tool_use where input.skill is in REVIEW_TRACE_SKILLS
    - denial: a hook-blocking denial in either transcript shape — a legacy
      `attachment` record (type==hook_blocking_error) or a current-format
      `tool_result` block with is_error and a hook-denial message signature.
      A denial recorded as both shapes is collapsed to one event by tool_use_id.
    - reviewer: Agent/Task spawn where subagent_type starts with 'staff-' or == 'ciso-reviewer'

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

    for jsonl, records in session_iter:
        events: list[dict] = []  # ordered, tagged with type/ts/line_no/branch/model
        # Tracks tool_use_ids already emitted as a denial. A legacy denial
        # appears as both an attachment record and an is_error tool_result
        # sharing one tool_use_id; this set collapses the pair to one event.
        seen_denial_ids: set[str] = set()

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
            if rec_type == "user":
                for block in ((rec.get("message") or {}).get("content") or []):
                    if not isinstance(block, dict) or block.get("type") != "tool_result":
                        continue
                    denial = hook_denial_key(block)
                    if denial is None:
                        continue
                    tool_use_id, message = denial
                    if tool_use_id and tool_use_id in seen_denial_ids:
                        continue
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

        # Branch filtering happens after dedup (seen_denial_ids was populated
        # above over every event, unconditionally) so a duplicate-id denial on
        # a differently-branched record is suppressed, not re-emitted as a
        # distinct in-scope event.
        if branch_filter:
            events = [e for e in events if e["branch"] in branch_filter]

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


def _iter_scoped_sessions(slugs: list[str], include_subagents: bool):
    """Yield sessions from an explicit set of exact project-dir slugs.

    Matching is by identity, not location: enumerate the directory names under
    PROJECTS_DIR and keep only those whose name is string-equal to one of the
    scoped slugs. This deliberately does NOT route the slug through Path.glob —
    a slug containing a glob metacharacter (a `*`/`?`/`[` in the machine's home
    or username path) would otherwise be interpreted as a wildcard and could
    widen the match beyond this repo's own worktrees. Visiting each directory at
    most once also makes double-counting impossible.
    """
    wanted = set(slugs)
    if not PROJECTS_DIR.is_dir():
        return
    for project_dir in sorted(PROJECTS_DIR.iterdir()):
        if project_dir.name in wanted and project_dir.is_dir():
            for jsonl in sorted(project_dir.glob("*.jsonl")):
                records = _read_session_file(jsonl, include_subagents)
                if records:
                    yield jsonl, records


def _resolve_project_scope(
    args: argparse.Namespace, subcommand: str, include_subagents: bool = False
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
    """
    if args.this_repo:
        slugs = getattr(args, "_this_repo_slugs", None)
        if slugs is None:
            slugs = _repo_scoped_project_slugs(subcommand)
            args._this_repo_slugs = slugs
        return _iter_scoped_sessions(slugs, include_subagents), f"this repo ({len(slugs)} project dirs)"
    glob = _projects_glob(args)
    return iter_sessions(PROJECTS_DIR, glob, include_subagents=include_subagents), glob


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


_REDACT_MAP_MISS_TOKEN = "private-project-unmapped"


def _redact_proj_label(proj_label: str, redact_map: dict[str, str]) -> str:
    """Apply the redact map to a project label, preserving 'claude-config' as-is.

    A map miss returns a fixed opaque token rather than the raw label — the
    map is only ever built from a full-corpus scan (_build_redact_map), so a
    miss means the caller passed an incomplete map, and falling back to the
    raw name would silently defeat --redact.
    """
    if proj_label == "claude-config":
        return proj_label
    return redact_map.get(proj_label, _REDACT_MAP_MISS_TOKEN)


def _build_redact_map() -> dict[str, str]:
    """Build the project-label -> opaque-token map shared by every --redact caller.

    Always scans the full corpus via iter_sessions(PROJECTS_DIR, "*"), ignoring
    the caller's own --projects filter, so a project always binds to the same
    placeholder whether it was found by a narrowed cost run or a full
    audit-routing run — a narrower scan would let the same label mean two
    different projects across two published outputs. iter_sessions (not a raw
    glob) is used because it already excludes zero-record transcripts; a raw
    glob would not, and that difference would shift every subsequent
    private-project-N index. --since never reaches this map and must not: it
    would change which sessions are found on a per-run basis, with the same
    label-drift consequence.

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
    """
    labels: list[str] = []
    for jsonl, _records in iter_sessions(PROJECTS_DIR, "*"):
        label = _derive_proj_label(jsonl)
        if label not in labels:
            labels.append(label)
    labels.sort()

    redact_map: dict[str, str] = {}
    num_index = 1
    for label in labels:
        if label == "claude-config":
            redact_map[label] = label
        else:
            redact_map[label] = f"private-project-{num_index}"
            num_index += 1
    return redact_map


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

    # Per-session accumulators: session_key → {class → {out, cr}}
    session_rows: list[dict] = []
    # Corpus totals: class → {out, cr}
    corpus_totals: dict[str, dict[str, int]] = {cls: {"out": 0, "cr": 0} for cls in _AUDIT_CLASSES}

    for jsonl, records in session_iter:
        proj_label = _derive_proj_label(jsonl)
        session_id = jsonl.stem[:12]
        if redact:
            _assign_session_redact_label(session_id, session_redact_map)

        # Per-session class token accumulators
        session_class_tokens: dict[str, dict[str, int]] = {
            cls: {"out": 0, "cr": 0} for cls in _AUDIT_CLASSES
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

    sonnet_tier_out = corpus_totals["code-write"]["out"] + corpus_totals["code-read"]["out"]
    sonnet_pct = f"{100 * sonnet_tier_out / total_out_all:.0f}%" if total_out_all else "—"
    print(f"\nSonnet-tier estimate: {sonnet_tier_out:,} output tokens")
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


def _pct_of(value: float, total: float) -> str:
    """value/total as a percentage string; 0.0% (not an undefined dash) when total is zero."""
    return f"{100 * value / total:.1f}%" if total else "0.0%"


def cmd_cost(args: argparse.Namespace) -> None:
    """CLI entry point for the cost subcommand.

    Reads the wall-clock date exactly once, here, then delegates to
    _cost_report, which takes `today` as an explicit parameter. The staleness
    banner must never read the clock itself — otherwise every test asserting
    cost's stdout would start failing the moment a rate's `expires` date passes.
    UTC, matching _fmt_date's convention and the UTC-implicit _PRICING_FETCH_DATE
    and _MODEL_RATE_EXPIRES dates — a local-time date.today() could shift the
    staleness banner's boundary day by the operator's UTC offset.
    """
    _cost_report(args, datetime.now(UTC).date())


def _cost_report(args: argparse.Namespace, today: date) -> None:
    """Corpus-wide dollar-cost report by token class, model ID, and context-at-turn bucket.

    Sidechain (subagent) turns are priced exactly once: iter_sessions is
    called with include_subagents=True so subagent-dispatched spend is
    counted toward the total, matching real billing — cmd_audit_routing's
    Opus-only, main-thread-only scope would silently exclude most of it.
    """
    top_n: int = getattr(args, "top", 20) or 20
    redact: bool = not bool(getattr(args, "no_redact", False))

    since_ts, since_raw = _parse_since_nd_arg(args, "cost")
    since_label = since_raw or ""

    # _resolve_project_scope's fail-closed --this-repo check runs before
    # _build_redact_map's full-corpus disk scan, so an out-of-repo failure
    # exits without paying for that scan.
    session_iter, scope_label = _resolve_project_scope(args, "cost", include_subagents=True)
    _print_resolved_scope("cost", scope_label)

    redact_map: dict[str, str] = _build_redact_map() if redact else {}
    session_redact_map: dict[str, str] = {}

    class_totals: dict[str, float] = dict.fromkeys(_TOKEN_CLASSES, 0.0)
    model_totals: dict[str, float] = defaultdict(float)
    unpriced_tokens: dict[str, int] = defaultdict(int)
    bucket_totals: dict[str, float] = defaultdict(float)
    session_rows: list[dict] = []
    stale_models: set[str] = set()

    for jsonl, records in session_iter:
        proj_label = _derive_proj_label(jsonl)
        session_id = jsonl.stem[:12]
        if redact:
            _assign_session_redact_label(session_id, session_redact_map)
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
            dollars_by_class, context_at_turn, turn_unpriced_tokens = _price_turn(model, usage)

            if dollars_by_class is None:
                unpriced_tokens[model] += turn_unpriced_tokens
                continue

            if today > _MODEL_RATE_EXPIRES[model]:
                stale_models.add(model)

            turn_total = 0.0
            for cls in _TOKEN_CLASSES:
                class_totals[cls] += dollars_by_class[cls]
                turn_total += dollars_by_class[cls]
            model_totals[model] += turn_total
            bucket_totals[_context_bucket(context_at_turn)] += turn_total
            session_total += turn_total

        if session_total:
            session_rows.append({
                "session_id": session_id,
                "proj_label": proj_label,
                "total": session_total,
            })

    grand_total = sum(class_totals.values())

    title_since = f"last {since_label}" if since_label else "all time"
    print(f"\n## Cost report ({title_since})\n")

    if stale_models:
        print(
            "STALE PRICING — today is past the re-verify-by date for: "
            + ", ".join(sorted(stale_models))
            + f". Re-check rates at {_PRICING_SOURCE_URL} before publishing the figures below.\n"
        )

    print("## Cost by token class\n")
    print(f"{'Class':<16} {'$':>14} {'Share':>7}")
    for cls in _TOKEN_CLASSES:
        val = class_totals[cls]
        print(f"{cls:<16} {val:>14,.2f} {_pct_of(val, grand_total):>7}")
    print(f"{'total':<16} {grand_total:>14,.2f}")

    print("\n## Cost by model ID\n")
    print(f"{'Model':<28} {'$':>14} {'Share':>7}")
    for model, val in sorted(model_totals.items(), key=lambda kv: kv[1], reverse=True):
        print(f"{model:<28} {val:>14,.2f} {_pct_of(val, grand_total):>7}")
    for model, tok in sorted(unpriced_tokens.items()):
        print(f"{model:<28} {'unpriced':>14} {tok:>10,} tokens")
    total_unpriced_tokens = sum(unpriced_tokens.values())
    print(f"\nUnpriced tokens (unknown model IDs): {total_unpriced_tokens:,}")

    print(
        f"\n## Cost by context-at-turn bucket (input_tokens + cache_read_input_tokens"
        f" + ephemeral_1h + ephemeral_5m tokens, {_CONTEXT_BUCKET_THRESHOLD:,} boundary)\n"
    )
    print(f"{'Bucket':<8} {'$':>14} {'Share':>7}")
    for bucket in (_CONTEXT_BUCKET_UNDER, _CONTEXT_BUCKET_OVER):
        val = bucket_totals.get(bucket, 0.0)
        print(f"{bucket:<8} {val:>14,.2f} {_pct_of(val, grand_total):>7}")

    print(f"\n## Top {top_n} sessions by dollars\n")
    if not session_rows:
        print("(no priced turns in range)")
    else:
        print(f"{'Session':<16} {'Proj':<24} {'$':>14}")
        for row in sorted(session_rows, key=lambda r: r["total"], reverse=True)[:top_n]:
            sid = _redact_session_id(row["session_id"], session_redact_map) if redact else row["session_id"]
            proj = _redact_proj_label(row["proj_label"], redact_map) if redact else row["proj_label"]
            print(f"{sid:<16} {proj:<24} {row['total']:>14,.2f}")


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

    p_duration = sub.add_parser("duration", help="Active span vs idle-gap decomposition per branch.")
    p_duration.add_argument("--branches", metavar="B1,B2,...")
    _add_project_scope_args(p_duration)
    p_duration.add_argument("--gap-minutes", type=int, default=30, metavar="N")
    p_duration.set_defaults(func=cmd_duration)

    p_sub = sub.add_parser("subagents", help="isSidechain turn counts and model split per branch.")
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
        "--since", metavar="Nd",
        help="Limit to turns with timestamp in the last N days (e.g. 35d).",
    )
    p_cost.add_argument(
        "--top", type=int, default=20, metavar="N",
        help="Maximum number of per-session rows in the top-N-by-dollars section (default: 20).",
    )
    p_cost.add_argument(
        "--no-redact", action="store_true",
        help=(
            "Emit real project names and session IDs instead of anonymized labels."
            " Never publish --no-redact output — see docs/transcript-analysis.md."
        ),
    )
    p_cost.set_defaults(func=cmd_cost)

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
    parsed.func(parsed)


if __name__ == "__main__":
    main()
