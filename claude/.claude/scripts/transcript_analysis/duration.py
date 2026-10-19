"""The duration command: cmd_duration, each branch's span, active, and idle minutes
and activity-burst count, and its subparser registration.

Imports corpus and scope by module (attribute access, not by name) -- see
scope.py's own top-of-file comment for why.
"""
from __future__ import annotations

import argparse
from collections import defaultdict

from transcript_analysis import corpus, scope


def cmd_duration(args: argparse.Namespace) -> None:
    """Per-branch span/active/idle time split and activity-burst count.

    The "Bursts" column is len(idle_gaps) + 1. docs/transcript-analysis.md
    explains what it counts.
    """
    branch_filter = scope._branch_filter(args)
    gap_secs: int = (getattr(args, "gap_minutes", None) or 30) * 60
    roots = scope.resolve_scan_roots(args)
    session_iter, scope_label = scope._resolve_project_scope(args, "duration", roots=roots)
    scope.print_resolved_scope("duration", scope_label, roots)

    branch_timestamps: dict[str, list[float]] = defaultdict(list)

    for _jsonl, records in session_iter:
        branch_context = scope._session_branch_context(records)
        for rec in records:
            branch = scope._attributed_branch(rec, branch_context) or ""
            if not branch or (branch_filter and branch not in branch_filter):
                continue
            ts = corpus._parse_ts(rec.get("timestamp"))
            if ts is not None:
                branch_timestamps[branch].append(ts)

    if not branch_timestamps:
        print("No timestamp data found.")
        return

    print(f"{'Branch':<40} {'Span(min)':>10} {'Active(min)':>11} {'Idle(min)':>10} {'Bursts':>9} {'GapMin':>7}")
    print("-" * 95)
    for branch in sorted(branch_timestamps):
        tss = sorted(branch_timestamps[branch])
        if len(tss) < 2:
            continue
        span_secs = tss[-1] - tss[0]
        idle_gaps = [tss[i + 1] - tss[i] for i in range(len(tss) - 1) if tss[i + 1] - tss[i] > gap_secs]
        idle_secs = sum(idle_gaps)
        active_secs = span_secs - idle_secs
        burst_count = len(idle_gaps) + 1
        print(
            f"{branch:<40} {span_secs / 60:>10.0f} {active_secs / 60:>11.0f} "
            f"{idle_secs / 60:>10.0f} {burst_count:>9} {gap_secs / 60:>7.0f}"
        )


def _add_duration_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Register the duration subparser on build_parser()'s subparsers action,
    binding cmd_duration through set_defaults(func=...).
    """
    p_duration = subparsers.add_parser("duration", help="Active span vs idle-gap decomposition per branch.")
    p_duration.add_argument("--branches", metavar="B1,B2,...")
    scope._add_project_scope_args(p_duration)
    p_duration.add_argument("--gap-minutes", type=int, default=30, metavar="N")
    p_duration.set_defaults(func=cmd_duration)
