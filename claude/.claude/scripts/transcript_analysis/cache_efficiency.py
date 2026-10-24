"""The cache-efficiency command: cmd_cache_efficiency, the per-thread cold-cache read-collapse
census of cache read and write tokens, with a per-account breakdown above one root, and its
subparser registration.

Imports corpus, pricing, render, and scope by module (attribute access, not by name) -- see
scope.py's own top-of-file comment for why.
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from transcript_analysis import corpus, pricing, render, scope

# T=0.50 is the case study's highest-scoring threshold for this read-collapse
# rule (max Youden's J) against the alternative cache_creation >
# cache_read_input_tokens rule; see docs/case-studies/cold-cache-attribution.md
# for the full comparison.
_COLD_READ_COLLAPSE_MARGIN = 0.50


def _cache_prefix_total(usage: dict) -> int:
    """cache_read_input_tokens plus both cache_creation tiers for one turn's
    usage -- the prefix total a warm cache would have served whole, and the
    read-collapse classifier's prior-turn denominator
    (docs/case-studies/cold-cache-attribution.md). Deliberately excludes
    input_tokens, unlike _context_at_turn: the classifier compares what the
    cache itself could have served, not the turn's total context."""
    eph_1h, eph_5m = pricing._cache_write_split(usage)
    return int(usage.get("cache_read_input_tokens", 0)) + eph_1h + eph_5m


def _is_cold_read_collapse(prior_prefix_total: int, read_t: int) -> bool:
    """The read-collapse rule: cold when this turn's read falls more than
    _COLD_READ_COLLAPSE_MARGIN below the prior turn's own prefix total.
    prior_prefix_total <= 0 means there is no prefix to collapse from --
    including a session/thread's first turn, which has no prior turn at all
    -- and is never cold."""
    if prior_prefix_total <= 0:
        return False
    return (prior_prefix_total - read_t) / prior_prefix_total > _COLD_READ_COLLAPSE_MARGIN


def _new_cache_efficiency_stats() -> dict:
    return {
        thread: {
            "turns": 0,
            "read_tokens": 0,
            "write_1h_tokens": 0,
            "write_5m_tokens": 0,
            "cold_tokens": 0,
            "cold_events": 0,
        }
        for thread in ("main", "sidechain")
    }


def _merge_cache_efficiency_stats(dst: dict, src: dict) -> None:
    for thread in ("main", "sidechain"):
        d, s = dst[thread], src[thread]
        for key in ("turns", "read_tokens", "write_1h_tokens", "write_5m_tokens", "cold_tokens", "cold_events"):
            d[key] += s[key]


def _scan_cache_efficiency_group(group: list[dict], stats: dict) -> int:
    """Classify one source-file group's (main transcript, or one subagent
    file, per _read_session_file_partitioned) assistant turns for cold-cache
    read collapse and accumulate into `stats`, keyed by thread
    ("main"/"sidechain").

    `group` must already be deduped via _dedup_turns_by_request_id -- an
    un-deduped multi-record run shares one identical cache_read/cache_creation
    usage across every record in the run (see that function's docstring), so
    scanning raw records would compare a turn against itself mid-run and can
    spuriously read as cold whenever that turn's own write exceeds its read.

    Keys the prior-turn chain by each record's own (sessionId, thread),
    mirroring _read_scope_growth_for_group's sessionId keying -- a subagent
    file's records carry the *parent* session's sessionId, so sessionId
    alone is still the correct per-conversation boundary, not per-file
    identity. Thread is included because, unlike _read_scope_growth_for_group
    (which has no per-thread output), this function already buckets its
    output by thread: without it, a defensively-accepted mixed-thread group
    would let one thread's prior prefix leak into the other's first-turn
    classification. The first turn of every resulting per-(session, thread)
    sequence has no predecessor and so is never classified cold. Resets the
    chain at each compact_boundary record, same as
    _read_scope_growth_for_group -- the pre-compaction prefix no longer
    exists to collapse from, so treating it as this turn's "prior" would
    misclassify the first post-compaction turn as cold every time.

    Returns the count of isSidechain assistant records read in this group,
    counted unconditionally before the usage check -- feeds the drift canary
    independently of stats["sidechain"]["turns"] (which only counts turns
    with priced usage), mirroring _cost_report's total_sidechain_turns.
    """
    prior_prefix_by_thread_session: dict[tuple[str, str], int] = {}
    sidechain_turns_read = 0

    for rec in group:
        if rec.get("type") == "system" and rec.get("subtype") == "compact_boundary":
            prior_prefix_by_thread_session.clear()
            continue
        if rec.get("type") != "assistant":
            continue
        thread = "sidechain" if bool(rec.get("isSidechain")) else "main"
        if thread == "sidechain":
            sidechain_turns_read += 1
        usage = (rec.get("message") or {}).get("usage")
        if not usage:
            continue

        session_key = rec.get("sessionId") or ""
        chain_key = (session_key, thread)
        read_t = int(usage.get("cache_read_input_tokens", 0))
        # _cache_write_split runs twice for this turn (here and inside
        # _cache_prefix_total below), mirroring _price_turn's own reuse of
        # it -- it's pure, and the per-tier accumulators need the split
        # separately from the combined prefix total the classifier compares.
        eph_1h, eph_5m = pricing._cache_write_split(usage)
        prefix_total = _cache_prefix_total(usage)

        row = stats[thread]
        row["turns"] += 1
        row["read_tokens"] += read_t
        row["write_1h_tokens"] += eph_1h
        row["write_5m_tokens"] += eph_5m

        prior_prefix_total = prior_prefix_by_thread_session.get(chain_key)
        if prior_prefix_total is not None and _is_cold_read_collapse(prior_prefix_total, read_t):
            row["cold_tokens"] += eph_1h + eph_5m
            row["cold_events"] += 1

        prior_prefix_by_thread_session[chain_key] = prefix_total

    return sidechain_turns_read


def _print_cache_efficiency_table(stats: dict) -> None:
    # Every header label is a single whitespace token (Write1h, not "Write
    # 1h") so this table stays parseable by the test suite's own
    # header-anchored column reader (_table_cols), matching every other
    # fixed-width table in this file.
    print(
        f"{'Thread':<10} {'Turns':>10} {'Read':>16} {'Write1h':>14} {'Write5m':>14}"
        f" {'ColdTok':>16} {'Cold/Wr':>11} {'Cold/Rd':>10} {'ColdEvts':>10} {'AvgEvt':>10}"
    )
    for thread in ("main", "sidechain"):
        row = stats[thread]
        write_total = row["write_1h_tokens"] + row["write_5m_tokens"]
        cold_events = row["cold_events"]
        avg_event = row["cold_tokens"] / cold_events if cold_events else 0
        print(
            f"{thread:<10} {row['turns']:>10,} {row['read_tokens']:>16,} {row['write_1h_tokens']:>14,}"
            f" {row['write_5m_tokens']:>14,} {row['cold_tokens']:>16,} {render._pct_of(row['cold_tokens'], write_total):>11}"
            f" {render._pct_of(row['cold_tokens'], row['read_tokens']):>10} {cold_events:>10,} {avg_event:>10,.0f}"
        )


def _print_cache_efficiency_report(stats: dict, per_account: dict[int, dict] | None) -> None:
    print("\n## Cache efficiency by thread\n")
    _print_cache_efficiency_table(stats)

    if per_account is not None:
        print("\n## Cache efficiency by account\n")
        for ordinal in sorted(per_account):
            print(f"\n### account-{ordinal}\n")
            _print_cache_efficiency_table(per_account[ordinal])


def cmd_cache_efficiency(args: argparse.Namespace) -> None:
    """CLI entry point for the cache-efficiency subcommand.

    Root resolution happens here, mirroring cmd_cost/cmd_edit_format/
    cmd_read_scope, so --config-dir validation exits before any scan work.
    """
    roots = scope._resolve_cost_roots(args, subcommand="cache-efficiency")
    _cache_efficiency_report(args, roots)


def _cache_efficiency_report(args: argparse.Namespace, roots: Sequence[Path] | None = None) -> None:
    """Per-thread cold-cache read-collapse census: assistant turn counts,
    cache read/write token totals, and cold-write volume/rate, classified by
    the read-collapse rule at T=_COLD_READ_COLLAPSE_MARGIN
    (docs/case-studies/cold-cache-attribution.md). `cost` buckets spend by
    token class only; this distinguishes a cold prefix re-write from an
    ordinary incremental append within that spend.

    roots is None for every direct caller other than cmd_cache_efficiency
    (this module's own tests included) -- mirrors cost/edit-format/read-scope's
    own single-root-by-default contract, including the absence of the
    per-account breakdown below.

    This report's own content never varies with `redact`: like edit-format
    and read-scope, it carries no project name or session ID -- per-account
    rows use account-N labels. --no-redact is still accepted and still
    enforces the same multi-root refusal and DO NOT PUBLISH banner as
    cost/edit-format/read-scope, for CLI parity.
    """
    redact: bool = not bool(getattr(args, "no_redact", False))
    scan_roots: Sequence[Path] = roots if roots is not None else (scope.PROJECTS_DIR,)
    multi_root = len(scan_roots) > 1

    # Defense-in-depth: _resolve_cost_roots is the CLI-level enforcement
    # point for this refusal, but every direct caller of this function
    # (including this module's own tests) bypasses that boundary.
    if not redact and multi_root:
        print(
            "cache-efficiency: --no-redact is refused when more than one root is in scope"
            " (--config-dir was given); drop --no-redact or scope to a single profile",
            file=sys.stderr,
        )
        sys.exit(2)

    if not redact:
        print(scope._DO_NOT_PUBLISH_BANNER)
        print(scope._DO_NOT_PUBLISH_BANNER, file=sys.stderr)

    session_iter, scope_label = scope._resolve_project_scope(
        args, "cache-efficiency", include_subagents=True, roots=roots
    )
    scope.print_resolved_scope("cache-efficiency", scope_label, scan_roots)

    resolved_scan_roots = [root.resolve() for root in scan_roots] if multi_root else []
    redact_ordinals: dict[Path, int] = scope._redaction_ordinals(scan_roots) if multi_root else {}

    stats = _new_cache_efficiency_stats()
    per_account: dict[int, dict] = (
        {ordinal: _new_cache_efficiency_stats() for ordinal in redact_ordinals.values()} if multi_root else {}
    )
    total_spawns = 0
    total_sidechain_turns = 0

    for jsonl, records in session_iter:
        records = pricing.dedup_turns_by_request_id(records)
        total_spawns += pricing._count_subagent_spawns(records)
        # session_iter already read and parsed this file once internally (to
        # decide whether to yield it at all); this second, partitioned read
        # is the cost of reusing _resolve_project_scope's shared iterator,
        # mirroring read-scope's own growth-chain reuse note -- the
        # classifier's prior-turn chain needs the per-file boundary the flat
        # merge discards (a subagent's own cache prefix is not continuous
        # with the main thread's, or with a sibling subagent's).
        groups = corpus._read_session_file_partitioned(jsonl, include_subagents=True)
        session_stats = _new_cache_efficiency_stats()
        for group in groups:
            total_sidechain_turns += _scan_cache_efficiency_group(pricing.dedup_turns_by_request_id(group), session_stats)
        _merge_cache_efficiency_stats(stats, session_stats)
        if multi_root:
            root_position = scope._root_index_for_path(jsonl, resolved_scan_roots)
            ordinal = redact_ordinals[resolved_scan_roots[root_position]]
            _merge_cache_efficiency_stats(per_account[ordinal], session_stats)

    pricing._warn_if_subagent_format_drift(total_spawns, total_sidechain_turns)

    _print_cache_efficiency_report(stats, per_account if multi_root else None)


def _add_cache_efficiency_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Register the cache-efficiency subparser on build_parser()'s subparsers action,
    binding cmd_cache_efficiency through set_defaults(func=...).
    """
    p_cache_efficiency = subparsers.add_parser(
        "cache-efficiency",
        help=(
            "Per-thread (main/sidechain) cold-cache read-collapse census: assistant turn counts,"
            " cache read/write token totals, and cold-write volume/rate, classified by the"
            " validated read-collapse rule (docs/case-studies/cold-cache-attribution.md)."
            " Aggregate-only output; redacted by default."
        ),
    )
    scope._add_project_scope_args(p_cache_efficiency)
    p_cache_efficiency.add_argument(
        "--config-dir", action="append", dest="extra_config_dirs", metavar="DIR",
        help=(
            "Additional Claude Code config directory to scan (repeatable). The default resolved"
            " config dir is always scanned first. Each supplied directory must contain a projects/"
            " subdirectory, or it is rejected. Composes with --this-repo, scoping to this repo"
            " across every resulting root; --no-redact is refused once this puts more than one"
            " root in scope."
        ),
    )
    p_cache_efficiency.add_argument(
        "--no-redact", action="store_true",
        help=(
            "This report's output is aggregate-only (no project names or session IDs), so"
            " --no-redact has no effect on its content, but it still prints the DO NOT PUBLISH"
            " banner and enforces the same multi-root refusal as cost, for CLI parity."
            " Refused when --config-dir puts more than one root in scope."
        ),
    )
    p_cache_efficiency.set_defaults(func=cmd_cache_efficiency)
