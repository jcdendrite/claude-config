"""The workstream-cost command: cmd_workstream_cost and
_print_workstream_session_stats -- per-branch session count and
continuation startup-burn dollars, with an optional --check-pr-status gh
classification pass.

Imports cost, gh_cli, render, and scope by module (attribute access, not by
name) -- see scope.py's own top-of-file comment for why.
"""
from __future__ import annotations

import argparse
import statistics
import sys
from datetime import UTC, datetime

from transcript_analysis import cost, gh_cli, render, scope


def _print_workstream_session_stats(workstream: dict[str, dict]) -> None:
    """Prints workstream-cost's "Sessions per branch" mean/median line and
    "Startup-burn dollars" share line, computed from
    _compute_workstream_dollars's own per-branch result."""
    session_counts = [w["session_count"] for w in workstream.values()]
    total_dollars = sum(w["total_dollars"] for w in workstream.values())
    total_startup_burn = sum(w["startup_burn_dollars"] for w in workstream.values())

    print(
        f"Sessions per branch -- mean: {statistics.mean(session_counts):.2f},"
        f" median: {statistics.median(session_counts):.2f}"
    )
    print(
        f"Startup-burn dollars: {render._fmt_usd(total_startup_burn)} of {render._fmt_usd(total_dollars)} total branch dollars"
        f" ({render._pct_of(total_startup_burn, total_dollars)})"
    )


def cmd_workstream_cost(args: argparse.Namespace) -> None:
    """CLI entry point for the workstream-cost subcommand.

    Default mode: pure transcript data, no gh calls, corpus-wide across
    every resolved root (the same root union every other read-only
    subcommand resolves via _resolve_scan_roots). Prints sessions-per-branch
    and continuation "startup burn" (_compute_workstream_dollars), free
    across every account.

    --check-pr-status additionally classifies every branch with no PR match
    at all (neither merged nor closed-unmerged) by its last local-activity
    age. A branch whose every turn carries an unparseable timestamp has no
    last-activity age to sort by and is silently omitted from that listing.
    It pins one gh repo identity the same way pr-cost pins one
    (_resolve_pinned_gh_repo, from this invocation's own git remote).
    --this-repo scopes the corpus side of that match to this repo's own
    worktrees when the corpus spans more than one repo (see pr-cost's own
    --this-repo caveat).
    """
    roots = scope.resolve_scan_roots(args)
    session_iter, scope_label = scope._resolve_project_scope(
        args, "workstream-cost", include_subagents=True, roots=roots,
    )
    scope.print_resolved_scope("workstream-cost", scope_label, roots)

    workstream = cost._compute_workstream_dollars(session_iter)
    if not workstream:
        print("No branches with corpus activity were found.")
        return

    print(f"Branches: {len(workstream)}")
    _print_workstream_session_stats(workstream)

    if not bool(getattr(args, "check_pr_status", False)):
        return

    corpus_host, corpus_repo = gh_cli._git_remote_origin_host_and_owner_repo()
    if not gh_cli._gh_auth_preflight_ok(corpus_host):
        print(
            "workstream-cost: gh auth status failed -- run `gh auth login` before --check-pr-status",
            file=sys.stderr,
        )
        sys.exit(1)
    redact_ordinals = scope._redaction_ordinals(roots)
    pinned_repo, _repo_map = gh_cli._resolve_pinned_gh_repo(
        corpus_host, corpus_repo, ordinal=redact_ordinals[roots[0].resolve()]
    )
    merged_branches = {
        pr["headRefName"] for pr in gh_cli._gh_discover_merged_prs(corpus_host, pinned_repo) if pr.get("headRefName")
    }
    closed_unmerged_branches = gh_cli._gh_discover_closed_unmerged_pr_branches(corpus_host, pinned_repo)

    now_ts = datetime.now(UTC).timestamp()
    no_match_ages_days = sorted(
        (
            (now_ts - agg["last_activity_ts"]) / 86400
            for branch, agg in workstream.items()
            if branch not in merged_branches and branch not in closed_unmerged_branches and agg["last_activity_ts"]
        ),
        reverse=True,
    )
    print("\nBranches with no PR match at all (merged or closed-unmerged), by last-activity age (days), oldest first:")
    if not no_match_ages_days:
        print("  (none)")
    for age_days in no_match_ages_days:
        print(f"  {age_days:.1f}")
