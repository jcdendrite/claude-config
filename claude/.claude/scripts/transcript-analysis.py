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

    parsed = parser.parse_args()
    parsed.func(parsed)


if __name__ == "__main__":
    main()
