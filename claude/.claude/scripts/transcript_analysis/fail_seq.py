"""The fail-seq command: cmd_fail_seq, each branch's ordered test-run failed-count
sequence with its failing rate and longest failing streak, the TEST_RUNNER_RE and
FAILED_RE patterns friction_count.py also reads, and its subparser registration.

Imports render and scope by module (attribute access, not by name) -- see
scope.py's own top-of-file comment for why.
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict

from transcript_analysis import render, scope

TEST_RUNNER_RE = re.compile(
    r"\b(vitest|jest|pytest|deno\s+test|npm\s+run\s+(verify|test|lint)|ruff\s+check|cargo\s+test|go\s+test)\b"
)
FAILED_RE = re.compile(r"\b(\d+)\s+failed\b")


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


def cmd_fail_seq(args: argparse.Namespace) -> None:
    if not getattr(args, "branches", None):
        print("--branches is required for fail-seq", file=sys.stderr)
        sys.exit(1)
    branches: set[str] = {b for b in args.branches.split(",") if b}
    roots = scope.resolve_scan_roots(args)
    session_iter, scope_label = scope._resolve_project_scope(args, "fail-seq", roots=roots)
    scope.print_resolved_scope("fail-seq", scope_label, roots)

    branch_runs: dict[str, list[tuple[str, int]]] = defaultdict(list)

    for _jsonl, records in session_iter:
        # Every attributed branch is some record's own tag, so a session with none in the
        # filter set cannot contribute.
        if not ({r.get("gitBranch", "") for r in records} & branches):
            continue

        pending: dict[str, str] = {}  # tool_use_id → model_family
        current_branch: str = ""
        branch_context = scope._session_branch_context(records)

        for rec in records:
            # The pending-pair reset follows the record's literal tag: it marks a physical
            # checkout change, which attribution must not hide. Only the filter and the
            # runs key take the attributed branch.
            literal_branch = rec.get("gitBranch") or ""
            if literal_branch != current_branch:
                pending.clear()
                current_branch = literal_branch
            branch = scope._attributed_branch(rec, branch_context) or ""
            if branch not in branches or bool(rec.get("isSidechain")):
                continue

            rtype = rec.get("type", "")
            msg = rec.get("message") or {}

            if rtype == "assistant":
                fam = render._fam(msg.get("model", ""))
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
                        result_text = render._content_text(block.get("content", ""))
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


def _add_fail_seq_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Register the fail-seq subparser on build_parser()'s subparsers action,
    binding cmd_fail_seq through set_defaults(func=...).
    """
    p_fail = subparsers.add_parser("fail-seq", help="Ordered test-run failed-count sequence per branch/model.")
    p_fail.add_argument("--branches", required=True, metavar="B1,B2,...")
    scope._add_project_scope_args(p_fail)
    p_fail.set_defaults(func=cmd_fail_seq)
