#!/usr/bin/env python3
"""transcript-analysis.py — Claude Code transcript analysis toolkit.
No writes; pr-link is the only subcommand that touches the network (via gh).
"""

import argparse
import fnmatch
import json
import re
import subprocess
import sys
import time
from collections import defaultdict
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

PROJECTS_DIR = Path.home() / ".claude" / "projects"

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
    "wrong approach",
    "that doesn't work",
    "please don't",
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


def _content_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _parse_ts(ts_str: str | None) -> float | None:
    if not ts_str:
        return None
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


def _iso_date(s: str) -> str:
    """argparse type: validate a YYYY-MM-DD date string."""
    try:
        datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a valid YYYY-MM-DD date: {s!r}") from None
    return s


def _fmt_date(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=UTC).strftime("%Y-%m-%d")


def iter_sessions(projects_dir: Path, projects_glob: str = "*") -> Iterator[tuple[Path, list[dict]]]:
    """Yield (jsonl_path, records) for each transcript file matching the glob."""
    for jsonl in sorted(projects_dir.glob(f"{projects_glob}/*.jsonl")):
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
            continue
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
    projects_glob = _projects_glob(args)
    branch_filter = _branch_filter(args)

    branch_data: dict[str, dict] = defaultdict(
        lambda: {"sessions": 0, "opus": 0, "sonnet": 0, "haiku": 0, "other": 0, "ts_min": float("inf"), "ts_max": float("-inf")}
    )

    for _jsonl, records in iter_sessions(PROJECTS_DIR, projects_glob):
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
            for fam in ("opus", "sonnet", "haiku", "other"):
                d[fam] += fb[fam]
            if fb["ts_min"] < float("inf"):
                d["ts_min"] = min(d["ts_min"], fb["ts_min"])
            if fb["ts_max"] > float("-inf"):
                d["ts_max"] = max(d["ts_max"], fb["ts_max"])

    if not branch_data:
        print("No data found.")
        return

    print(f"{'Branch':<40} {'Sess':>5} {'Total':>7} {'Opus':>6} {'Sonnet':>7} {'Haiku':>6} {'Other':>6}  Date range")
    print("-" * 108)
    for branch in sorted(branch_data):
        d = branch_data[branch]
        total = d["opus"] + d["sonnet"] + d["haiku"] + d["other"]
        ts_min = _fmt_date(d["ts_min"]) if d["ts_min"] < float("inf") else "?"
        ts_max = _fmt_date(d["ts_max"]) if d["ts_max"] > float("-inf") else "?"
        print(
            f"{branch:<40} {d['sessions']:>5} {total:>7} {d['opus']:>6} {d['sonnet']:>7} "
            f"{d['haiku']:>6} {d['other']:>6}  {ts_min}..{ts_max}"
        )


def cmd_fail_seq(args: argparse.Namespace) -> None:
    if not getattr(args, "branches", None):
        print("--branches is required for fail-seq", file=sys.stderr)
        sys.exit(1)
    branches: set[str] = {b for b in args.branches.split(",") if b}
    projects_glob = _projects_glob(args)

    branch_runs: dict[str, list[tuple[str, int]]] = defaultdict(list)

    for _jsonl, records in iter_sessions(PROJECTS_DIR, projects_glob):
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
    projects_glob = _projects_glob(args)
    branch_filter = _branch_filter(args)

    branch_data: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for _jsonl, records in iter_sessions(PROJECTS_DIR, projects_glob):
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
    projects_glob = _projects_glob(args)
    branch_filter = _branch_filter(args)
    gap_secs: int = (getattr(args, "gap_minutes", None) or 30) * 60

    branch_timestamps: dict[str, list[float]] = defaultdict(list)

    for _jsonl, records in iter_sessions(PROJECTS_DIR, projects_glob):
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


def cmd_subagents(args: argparse.Namespace) -> None:
    projects_glob = _projects_glob(args)
    branch_filter = _branch_filter(args)

    branch_data: dict[str, dict[str, dict[str, int]]] = defaultdict(
        lambda: {"main": defaultdict(int), "sidechain": defaultdict(int)}
    )

    for _jsonl, records in iter_sessions(PROJECTS_DIR, projects_glob):
        for rec in records:
            if rec.get("type") != "assistant":
                continue
            branch = rec.get("gitBranch") or ""
            if not branch or (branch_filter and branch not in branch_filter):
                continue
            fam = _fam((rec.get("message") or {}).get("model", ""))
            thread = "sidechain" if bool(rec.get("isSidechain")) else "main"
            branch_data[branch][thread][fam] += 1

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


def cmd_review_trace(args: argparse.Namespace) -> None:
    """Emit an ordered review-event timeline per session.

    Three event types are detected per session:
    - skill: main-thread Skill tool_use where input.skill is in REVIEW_TRACE_SKILLS
    - denial: a hook-blocking denial in either transcript shape — a legacy
      `attachment` record (type==hook_blocking_error) or a current-format
      `tool_result` block with is_error and a hook-denial message signature.
      A denial recorded as both shapes is collapsed to one event by tool_use_id.
    - reviewer: Agent/Task spawn where subagent_type starts with 'staff-' or == 'ciso-reviewer'
    """
    projects_glob = _projects_glob(args)
    branch_filter = _branch_filter(args)
    deny_only: bool = bool(getattr(args, "deny_only", False))
    skill_filter: str | None = getattr(args, "skill", None) or None

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

    for jsonl, records in iter_sessions(PROJECTS_DIR, projects_glob):
        events: list[dict] = []  # ordered, tagged with type/ts/line_no
        # Tracks tool_use_ids already emitted as a denial. A legacy denial
        # appears as both an attachment record and an is_error tool_result
        # sharing one tool_use_id; this set collapses the pair to one event.
        seen_denial_ids: set[str] = set()

        # Determine session model family from first non-empty main-thread assistant record.
        session_model = ""
        for rec in records:
            if rec.get("type") == "assistant" and not bool(rec.get("isSidechain")):
                session_model = (rec.get("message") or {}).get("model", "")
                if session_model:
                    break
        fam = _fam(session_model)

        # Determine primary branch from first main-thread record that has one.
        session_branch = ""
        for rec in records:
            if not bool(rec.get("isSidechain")):
                b = rec.get("gitBranch") or ""
                if b:
                    session_branch = b
                    break

        if branch_filter and session_branch not in branch_filter:
            continue

        for line_no, rec in enumerate(records, start=1):
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
                        })

            # --- Signal 2a: hook denials, legacy shape (attachment record) ---
            if rec_type == "attachment":
                att = rec.get("attachment") or {}
                if att.get("type") != "hook_blocking_error":
                    continue
                tool_use_id = att.get("toolUseID") or ""
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
                })

            # --- Signal 2b: hook denials, current shape (is_error tool_result) ---
            # Claude Code stopped emitting the hook_blocking_error attachment
            # record; current transcripts surface a denial only as an is_error
            # tool_result, identified by the hook-denial message signature.
            if rec_type == "user":
                for block in ((rec.get("message") or {}).get("content") or []):
                    if not isinstance(block, dict) or block.get("type") != "tool_result":
                        continue
                    if not block.get("is_error"):
                        continue
                    message = _content_text(block.get("content"))
                    if not _HOOK_DENIAL_SIGNATURE.search(message):
                        continue
                    tool_use_id = block.get("tool_use_id") or ""
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
                    })

        if not events:
            continue

        has_denial = any(e["kind"] == "denial" for e in events)
        if deny_only and not has_denial:
            continue

        skill_count = sum(1 for e in events if e["kind"] == "skill")
        denial_count = sum(1 for e in events if e["kind"] == "denial")
        spawn_count = sum(1 for e in events if e["kind"] == "reviewer-spawn")

        print(f"\n### {jsonl}")
        print(
            f"branch={session_branch}  model={fam}  skills={skill_count}"
            f"  denials={denial_count}  reviewer-spawns={spawn_count}"
        )
        for evt in events:
            ts_label = evt.get("ts") or "?"
            lno = evt["line_no"]
            kind = evt["kind"]
            if kind == "skill":
                print(f"  [{ts_label}] line {lno:>5}  skill        {evt['skill']}")
            elif kind == "denial":
                hook = evt['hook_name']
                uid = evt['tool_use_id']
                msg = evt['message']
                print(f"  [{ts_label}] line {lno:>5}  denial       hook={hook}  id={uid}  msg={msg!r}")
            elif kind == "reviewer-spawn":
                print(f"  [{ts_label}] line {lno:>5}  reviewer     {evt['subagent_type']}")


def cmd_subagent_mix(args: argparse.Namespace) -> None:
    projects_glob = _projects_glob(args)
    branch_filter = _branch_filter(args)
    per_session: bool = bool(getattr(args, "per_session", False))

    data: dict[str, dict] = defaultdict(
        lambda: {"sessions": 0, "spawns": defaultdict(int), "skills": defaultdict(int)}
    )

    for jsonl, records in iter_sessions(PROJECTS_DIR, projects_glob):
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
                if name in ("Agent", "Task"):
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
    projects_glob = _projects_glob(args)
    exclude_glob: str | None = getattr(args, "exclude_projects", None)
    branch_filter = _branch_filter(args)

    # bin_str -> {leader_sessions, follower_main, follower_sidechain_only}
    data: dict[str, dict[str, int]] = defaultdict(
        lambda: {"leader_sessions": 0, "follower_main": 0, "follower_sidechain_only": 0}
    )

    for jsonl, records in iter_sessions(PROJECTS_DIR, projects_glob):
        # --exclude-projects: skip project dirs whose basename matches the glob
        if exclude_glob and fnmatch.fnmatchcase(jsonl.parent.name, exclude_glob):
            continue

        has_leader_hit = False
        leader_first_ts: float | None = None
        has_main_follower = False
        has_sidechain_follower = False

        for rec in records:
            if rec.get("type") != "assistant":
                continue
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
    projects_glob = _projects_glob(args)

    branch_models: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for _jsonl, records in iter_sessions(PROJECTS_DIR, projects_glob):
        for rec in records:
            branch = rec.get("gitBranch") or ""
            if branch not in branches or rec.get("type") != "assistant" or bool(rec.get("isSidechain")):
                continue
            fam = _fam((rec.get("message") or {}).get("model", ""))
            branch_models[branch][fam] += 1

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
    projects_glob = _projects_glob(args)
    branch_filter = _branch_filter(args)
    exclude_glob: str | None = getattr(args, "exclude_projects", None) or None

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

    for jsonl, records in iter_sessions(PROJECTS_DIR, projects_glob):
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


def _redact_proj_label(proj_label: str, redact_map: dict[str, str]) -> str:
    """Apply the redact map to a project label, preserving 'claude-config' as-is."""
    if proj_label == "claude-config":
        return proj_label
    return redact_map.get(proj_label, proj_label)


def cmd_audit_routing(args: argparse.Namespace) -> None:
    """Per-turn Opus token breakdown by routing class across all sessions.

    Classifies every Opus assistant turn into: orchestration, judgment,
    code-write, code-read, pure-thinking, or other — then aggregates
    output_tokens and cache_read_input_tokens per class. Emits per-session
    rows sorted descending by total output tokens, plus a corpus aggregate.
    """
    projects_glob = _projects_glob(args)
    top_n: int = getattr(args, "top", 20) or 20
    redact: bool = bool(getattr(args, "redact", False))

    since_ts: float | None = None
    since_label: str = ""
    since_raw: str | None = getattr(args, "since", None) or None
    if since_raw:
        try:
            days = float(since_raw.rstrip("d"))
            since_ts = time.time() - days * 86400
            since_label = since_raw
        except ValueError:
            print(f"audit-routing: --since: expected Nd like '35d', got {since_raw!r}", file=sys.stderr)
            sys.exit(1)

    # --- First pass: collect all project labels for redact mapping ---
    all_proj_labels: list[str] = []
    if redact:
        for jsonl, _ in iter_sessions(PROJECTS_DIR, projects_glob):
            label = _derive_proj_label(jsonl)
            if label not in all_proj_labels:
                all_proj_labels.append(label)
        all_proj_labels.sort()
        # Build stable numeric mapping; claude-config is kept as-is.
        num_index = 1
        redact_map: dict[str, str] = {}
        for label in all_proj_labels:
            if label == "claude-config":
                redact_map[label] = label
            else:
                redact_map[label] = f"private-project-{num_index}"
                num_index += 1
    else:
        redact_map = {}

    # Per-session accumulators: session_key → {class → {out, cr}}
    session_rows: list[dict] = []
    # Corpus totals: class → {out, cr}
    corpus_totals: dict[str, dict[str, int]] = {cls: {"out": 0, "cr": 0} for cls in _AUDIT_CLASSES}

    for jsonl, records in iter_sessions(PROJECTS_DIR, projects_glob):
        proj_label = _derive_proj_label(jsonl)
        session_id = jsonl.stem[:12]

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
        sid = row["session_id"]
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
    projects_glob = _projects_glob(args)
    since_str: str | None = getattr(args, "since", None) or None
    since_ts: float | None = _parse_ts(f"{since_str}T00:00:00Z") if since_str else None
    debug_detector: bool = bool(getattr(args, "debug_detector", False))

    # week_str -> {"handoffs": int, "compactions": int}
    data: dict[str, dict[str, int]] = defaultdict(lambda: {"handoffs": 0, "compactions": 0})

    for jsonl, records in iter_sessions(PROJECTS_DIR, projects_glob):
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
    log_path = Path.home() / ".claude" / ".handoff-nudge.log"
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
    projects_glob = _projects_glob(args)

    since_ts: float | None = None
    since_label: str = ""
    since_raw: str | None = getattr(args, "since", None) or None
    if since_raw:
        try:
            days = float(since_raw.rstrip("d"))
            since_ts = time.time() - days * 86400
            since_label = since_raw
        except ValueError:
            print(f"audit-routing-shape: --since: expected Nd like '35d', got {since_raw!r}", file=sys.stderr)
            sys.exit(1)

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

    for _jsonl, records in iter_sessions(PROJECTS_DIR, projects_glob):
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Claude Code transcript analysis toolkit.")
    sub = parser.add_subparsers(dest="subcommand", required=True)

    p_buckets = sub.add_parser("buckets", help="Assistant turns bucketed by gitBranch × model family.")
    p_buckets.add_argument("--projects", default="*", metavar="GLOB")
    p_buckets.add_argument("--branches", metavar="B1,B2,...", help="Branch name filter (default: all)")
    p_buckets.set_defaults(func=cmd_buckets)

    p_fail = sub.add_parser("fail-seq", help="Ordered test-run failed-count sequence per branch/model.")
    p_fail.add_argument("--branches", required=True, metavar="B1,B2,...")
    p_fail.add_argument("--projects", default="*", metavar="GLOB")
    p_fail.set_defaults(func=cmd_fail_seq)

    p_struggle = sub.add_parser("struggle", help="Correction/frustration signal phrases in user turns, split by model.")
    p_struggle.add_argument("--branches", metavar="B1,B2,...")
    p_struggle.add_argument("--projects", default="*", metavar="GLOB")
    p_struggle.set_defaults(func=cmd_struggle)

    p_duration = sub.add_parser("duration", help="Active span vs idle-gap decomposition per branch.")
    p_duration.add_argument("--branches", metavar="B1,B2,...")
    p_duration.add_argument("--projects", default="*", metavar="GLOB")
    p_duration.add_argument("--gap-minutes", type=int, default=30, metavar="N")
    p_duration.set_defaults(func=cmd_duration)

    p_sub = sub.add_parser("subagents", help="isSidechain turn counts and model split per branch.")
    p_sub.add_argument("--branches", metavar="B1,B2,...")
    p_sub.add_argument("--projects", default="*", metavar="GLOB")
    p_sub.set_defaults(func=cmd_subagents)

    p_mix = sub.add_parser(
        "subagent-mix",
        help="Subagent_type spawn counts per branch, with code/plan/ready-for-review skill invocations.",
    )
    p_mix.add_argument("--branches", metavar="B1,B2,...")
    p_mix.add_argument("--projects", default="*", metavar="GLOB")
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
    p_pr.add_argument("--projects", default="*", metavar="GLOB")
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
    p_skill_pair.add_argument("--projects", default="*", metavar="GLOB")
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
    p_gate.add_argument("--projects", default="*", metavar="GLOB")
    p_gate.add_argument(
        "--exclude-projects", default=None, metavar="GLOB",
        help="Exclude project dirs whose basename matches this glob.",
    )
    p_gate.add_argument("--branches", metavar="B1,B2,...", help="Branch name filter (default: all)")
    p_gate.set_defaults(func=cmd_commit_gate)

    p_review_trace = sub.add_parser(
        "review-trace",
        help=(
            "Ordered review-event timeline per session: skill invocations, hook denials,"
            " and reviewer-agent spawns."
        ),
    )
    p_review_trace.add_argument("--projects", default="*", metavar="GLOB")
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

    p_audit = sub.add_parser(
        "audit-routing",
        help=(
            "Per-turn Opus token breakdown by routing class (orchestration, judgment, code-write,"
            " code-read, pure-thinking, other). Aggregates output_tokens and cache_read_input_tokens"
            " per class across all sessions."
        ),
    )
    p_audit.add_argument("--projects", default="*", metavar="GLOB")
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

    p_handoff_ratio = sub.add_parser(
        "handoff-ratio",
        help=(
            "Per-week handoff-vs-compaction ratio: how often sessions use /handoff"
            " versus waiting for auto-compaction."
        ),
    )
    p_handoff_ratio.add_argument("--projects", default="*", metavar="GLOB")
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
    p_audit_shape.add_argument("--projects", default="*", metavar="GLOB")
    p_audit_shape.add_argument(
        "--since", metavar="Nd",
        help="Limit to turns with timestamp in the last N days (e.g. 35d).",
    )
    p_audit_shape.set_defaults(func=cmd_audit_routing_shape)

    parsed = parser.parse_args()
    parsed.func(parsed)


if __name__ == "__main__":
    main()
