"""The buckets command: cmd_buckets, the per-branch count of main-thread assistant
turns by model family with project and session counts and date range, and its
subparser registration.

Imports corpus, redaction, render, and scope by module (attribute access, not by
name) -- see scope.py's own top-of-file comment for why.
"""
from __future__ import annotations

import argparse
from collections import defaultdict

from transcript_analysis import corpus, redaction, render, scope


def cmd_buckets(args: argparse.Namespace) -> None:
    branch_filter = scope._branch_filter(args)
    roots = scope.resolve_scan_roots(args)
    session_iter, scope_label = scope._resolve_project_scope(args, "buckets", roots=roots)
    scope.print_resolved_scope("buckets", scope_label, roots)

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
        branch_context = scope._session_branch_context(records)
        for rec in records:
            branch = scope._attributed_branch(rec, branch_context) or ""
            if not branch or (branch_filter and branch not in branch_filter):
                continue
            ts = corpus._parse_ts(rec.get("timestamp"))
            if ts is not None:
                file_branches[branch]["ts_min"] = min(file_branches[branch]["ts_min"], ts)
                file_branches[branch]["ts_max"] = max(file_branches[branch]["ts_max"], ts)
            if rec.get("type") == "assistant" and not bool(rec.get("isSidechain")):
                fam = render._fam((rec.get("message") or {}).get("model", ""))
                file_branches[branch][fam] += 1

        for branch, fb in file_branches.items():
            d = branch_data[branch]
            d["sessions"] += 1
            d["projects"].add(redaction._project_family(jsonl.parent.name))
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
        ts_min = render._fmt_date(d["ts_min"]) if d["ts_min"] < float("inf") else "?"
        ts_max = render._fmt_date(d["ts_max"]) if d["ts_max"] > float("-inf") else "?"
        print(
            f"{branch:<40} {len(d['projects']):>4} {d['sessions']:>5} {total:>7} {d['opus']:>6} {d['sonnet']:>7} "
            f"{d['haiku']:>6} {d['other']:>6}  {ts_min}..{ts_max}"
        )


def _add_buckets_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Register the buckets subparser on build_parser()'s subparsers action,
    binding cmd_buckets through set_defaults(func=...).
    """
    p_buckets = subparsers.add_parser("buckets", help="Assistant turns bucketed by branch × model family.")
    scope._add_project_scope_args(p_buckets)
    p_buckets.add_argument("--branches", metavar="B1,B2,...", help="Branch name filter (default: all)")
    p_buckets.set_defaults(func=cmd_buckets)
