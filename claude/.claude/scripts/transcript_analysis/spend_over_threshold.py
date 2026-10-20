"""The spend-over-threshold command: cmd_spend_over_threshold reports each
ISO week's share of session spend earned at or above the handoff nudge's own
fire threshold.

Imports corpus, handoff_nudge, render, and scope by module (attribute access,
not by name) -- see scope.py's own top-of-file comment for why.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import UTC, datetime

from transcript_analysis import corpus, handoff_nudge, render, scope


def cmd_spend_over_threshold(args: argparse.Namespace) -> None:
    """Per-week share of session dollar spend earned at or above the handoff
    nudge's own fire threshold.

    For each session, sums `actual_dollars` (via _extract_rearm_session_turns,
    shared with rearm-backtest) across main-thread turns whose context_at_turn
    is at or above that session's own _hook_effective_fire_threshold (from its
    first main-thread turn's model), against the session's total main-thread
    actual_dollars. A session with no main-thread turn carrying a usage block
    (session_threshold is None) or with total_dollars == 0 (every turn
    unpriced) is excluded from the report -- neither has a meaningful share to
    report.

    Output: per-ISO-week table with columns: week, sessions, above-threshold
    $, total $, share. Also reads ~/.claude/.handoff-nudge.log if present and
    reports schema-drift count as a diagnostic footer.
    """
    since_str: str | None = getattr(args, "since", None) or None
    since_ts: float | None = corpus._parse_ts(f"{since_str}T00:00:00Z") if since_str else None
    roots = scope.resolve_scan_roots(args)
    session_iter, scope_label = scope._resolve_project_scope(args, "spend-over-threshold", roots=roots)
    scope.print_resolved_scope("spend-over-threshold", scope_label, roots)

    # week_str -> {"sessions": int, "above": float, "total": float}
    data: dict[str, dict[str, float]] = defaultdict(lambda: {"sessions": 0.0, "above": 0.0, "total": 0.0})

    for _jsonl, records in session_iter:
        # A session's dollar totals depend on its full, un-truncated turn
        # sequence (_extract_rearm_session_turns), so --since scopes whole
        # sessions here (by first timestamp), not individual records within
        # one -- matching _session_matches_rearm_scope's own convention for
        # this same per-turn machinery.
        first_ts = next((ts for r in records if (ts := corpus._parse_ts(r.get("timestamp"))) is not None), None)
        if since_ts is not None and (first_ts is None or first_ts < since_ts):
            continue
        if first_ts is None:
            continue

        extracted = handoff_nudge._extract_rearm_session_turns(records)
        session_threshold = extracted["session_threshold"]
        if session_threshold is None:
            continue

        above_dollars = 0.0
        total_dollars = 0.0
        for context_at_turn, _output_tokens, actual_dollars in extracted["main_thread_turns"]:
            total_dollars += actual_dollars
            if context_at_turn >= session_threshold:
                above_dollars += actual_dollars
        if total_dollars == 0:
            continue

        iso = datetime.fromtimestamp(first_ts, tz=UTC).isocalendar()
        week_str = f"{iso.year}-W{iso.week:02d}"
        data[week_str]["sessions"] += 1
        data[week_str]["above"] += above_dollars
        data[week_str]["total"] += total_dollars

    if not data:
        print("No sessions with a resolvable handoff-nudge threshold and priced spend were found.")
        handoff_nudge._print_nudge_log_diagnostic()
        return

    print(f"{'Week':<10} {'Sessions':>8} {'AboveUSD':>14} {'TotalUSD':>14} {'Share':>7}")
    print("-" * 57)
    total_sessions = 0
    total_above = total_total = 0.0
    for week_str in sorted(data):
        d = data[week_str]
        sessions = int(d["sessions"])
        above = d["above"]
        total = d["total"]
        total_sessions += sessions
        total_above += above
        total_total += total
        print(f"{week_str:<10} {sessions:>8} {above:>14,.2f} {total:>14,.2f} {render._pct_of(above, total):>7}")

    print("-" * 57)
    print(
        f"{'Total':<10} {total_sessions:>8} {total_above:>14,.2f} {total_total:>14,.2f} "
        f"{render._pct_of(total_above, total_total):>7}"
    )
    handoff_nudge._print_nudge_log_diagnostic()
