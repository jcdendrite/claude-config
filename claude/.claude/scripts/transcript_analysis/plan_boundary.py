"""The plan-boundary command: cmd_plan_boundary reprices each Opus-anchored
session's post-plan-boundary main-thread turns under three arms (continue on
Opus, switch to Sonnet in place, fresh Sonnet handoff), plus each arm pair's
work-inflation breakeven.

Imports handoff_nudge, pricing, render, and scope by module (attribute access,
not by name) -- see scope.py's own top-of-file comment for why.
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path

from transcript_analysis import handoff_nudge, pricing, render, scope

_PLAN_BOUNDARY_SONNET_MODEL = "claude-sonnet-5"


def _plan_boundary_turn_index(
    deduped: Sequence[dict], main_thread_record_positions: Sequence[int]
) -> int | None:
    """0-indexed main_thread_turns position of a session's plan boundary -- the
    FIRST main-thread assistant turn that calls ExitPlanMode or invokes the
    plan-review Skill.

    - First occurrence wins: a later ExitPlanMode/plan-review call is
      re-planning inside work this measurement already treats as post-boundary.
    - A sidechain occurrence of either signal is ignored.
    - Returns None when no such turn exists, or when the triggering record's
      "deduped" index has no matching entry in main_thread_record_positions --
      an unmapped boundary can't be repriced.
    """
    record_index_to_turn_index = {pos: i for i, pos in enumerate(main_thread_record_positions)}
    for record_index, rec in enumerate(deduped):
        if rec.get("type") != "assistant" or bool(rec.get("isSidechain")):
            continue
        content = (rec.get("message") or {}).get("content") or []
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            name = block.get("name")
            is_plan_review = name == "Skill" and (block.get("input") or {}).get("skill") == "plan-review"
            if name == "ExitPlanMode" or is_plan_review:
                return record_index_to_turn_index.get(record_index)
    return None


def _arm_b_boundary_plus_one_dollars(usage: dict, boundary_context_tokens: int) -> float:
    """Arm B's boundary+1 turn: a Sonnet cache-write over the boundary
    context (never a scaled cache-read -- the prompt cache is model-keyed, so
    a model switch forces a full miss) plus Sonnet input/output on this
    turn's own new tokens, with the write priced at the 5m tier per
    _cache_write_split's own no-split fallback.
    """
    rates = pricing._model_rates(_PLAN_BOUNDARY_SONNET_MODEL)
    input_t = int(usage.get("input_tokens", 0))
    output_t = int(usage.get("output_tokens", 0))
    return (
        boundary_context_tokens / 1_000_000 * rates["cache_write_5m"]
        + input_t / 1_000_000 * rates["input"]
        + output_t / 1_000_000 * rates["output"]
    )


def _arm_b_later_turn_dollars(usage: dict) -> float:
    """Arm B's own turns after boundary+1: the observed read/write split
    carried forward unchanged, priced at Sonnet rates instead of the turn's
    real (Opus) model."""
    dollars_by_class, _context_at_turn, _turn_unpriced_tokens = pricing._price_turn(_PLAN_BOUNDARY_SONNET_MODEL, usage)
    return sum(dollars_by_class.values())


def _arm_c_turn_dollars(output_tokens: int, turns_since_boundary: int, ramp_curve: dict[str, dict[str, float]]) -> float:
    """Arm C's (fresh Sonnet handoff) post-boundary turn: (output_tokens/1000)
    * the ramp curve's own bucket rate for this many turns since a fresh
    session start -- _ramp_curve_from_corpus' own multiply-back convention,
    mirroring _simulate_rearm_spacing's non-actual-epoch branch. Never scales
    the turn's actual observed dollars: those already embed both the
    model-price gap and the context-growth gap, so scaling would double-count.
    `ramp_curve` is expected to be Sonnet-scoped (see _plan_boundary_report),
    since this arm models a fresh Sonnet session.
    """
    label = handoff_nudge._ramp_curve_turn_index_bucket(turns_since_boundary)
    bucket = ramp_curve.get(label, {"rate": 0.0, "mean_context": 0.0})
    return (output_tokens / 1000) * bucket["rate"]


def _plan_boundary_work_inflation_breakeven(
    cheaper_dollars: float, delta_dollars: float, post_boundary_turns: int, post_boundary_output_tokens: int
) -> dict[str, float | None]:
    """breakeven_pct = delta_dollars / cheaper_dollars, the fraction of extra
    work that closes the cheaper arm's dollar advantage to zero; all three
    fields are None when cheaper_dollars <= 0 (no observed rate to extrapolate from).
    """
    if cheaper_dollars <= 0:
        return {"pct": None, "extra_turns": None, "extra_output_tokens": None}
    pct = delta_dollars / cheaper_dollars
    return {
        "pct": pct,
        "extra_turns": pct * post_boundary_turns,
        "extra_output_tokens": pct * post_boundary_output_tokens,
    }


def cmd_plan_boundary(args: argparse.Namespace) -> None:
    """CLI entry point for the plan-boundary subcommand.

    Root resolution happens here, mirroring cmd_rearm_backtest --
    --config-dir validation exits before any scan work. The wall-clock date
    is read exactly once, here, mirroring cmd_rearm_backtest's own split.
    """
    roots = scope._resolve_cost_roots(args, subcommand="plan-boundary")
    _plan_boundary_report(args, datetime.now(UTC).date(), roots)


def _plan_boundary_report(args: argparse.Namespace, today: date, roots: Sequence[Path] | None = None) -> None:
    """Aggregate-only report; see docs/transcript-analysis.md's plan-boundary
    section for arm definitions and output contract.

    roots is None only for this module's own tests exercising the report
    body directly; --config-dir CLI validation happens once in
    cmd_plan_boundary.
    """
    redact: bool = not bool(getattr(args, "no_redact", False))
    scan_roots: Sequence[Path] = roots if roots is not None else (scope.PROJECTS_DIR,)
    multi_root = len(scan_roots) > 1

    # Defense-in-depth: _resolve_cost_roots is the CLI-level enforcement
    # point for this refusal, but a direct caller of this function
    # (including this module's own tests) bypasses that boundary.
    if not redact and multi_root:
        print(
            "plan-boundary: --no-redact is refused when more than one root is in scope"
            " (--config-dir was given); drop --no-redact or scope to a single profile",
            file=sys.stderr,
        )
        sys.exit(2)
    if not redact:
        print(scope._DO_NOT_PUBLISH_BANNER)
        print(scope._DO_NOT_PUBLISH_BANNER, file=sys.stderr)

    # Arms B and C reprice every post-boundary turn at
    # _PLAN_BOUNDARY_SONNET_MODEL's rates; checked once here, before scanning
    # any session, so an unpriced model fails the whole report up front
    # instead of crashing mid-scan on an arbitrary turn.
    if pricing._model_rates(_PLAN_BOUNDARY_SONNET_MODEL) is None:
        print(
            f"plan-boundary: {_PLAN_BOUNDARY_SONNET_MODEL} has no _MODEL_BASE_INPUT_RATES entry --"
            " arms B and C cannot be priced",
            file=sys.stderr,
        )
        sys.exit(1)

    since_ts, since_raw = scope._parse_since_nd_arg(args, "plan-boundary")

    session_iter, scope_label = scope._resolve_project_scope(args, "plan-boundary", roots=roots)
    scope.print_resolved_scope("plan-boundary", scope_label, scan_roots)

    # Each in-scope session's records are deduped and priced exactly once,
    # via _extract_rearm_session_turns, mirroring _rearm_backtest_report's
    # own single-pass convention.
    scoped_sessions = [
        handoff_nudge._extract_rearm_session_turns(records) for _jsonl, records in session_iter
        if handoff_nudge._session_matches_rearm_scope(records, since_ts, None)
    ]

    # Scoped to Sonnet-anchored sessions since arm C models a fresh Sonnet
    # session, not a family-mixed average -- falls back to the pooled corpus
    # when that slice has no priced output tokens, mirroring
    # _ramp_curve_from_corpus's own zero-bucket fallback.
    sonnet_scoped_sessions = [
        session for session in scoped_sessions
        if session["main_thread_models"] and render._fam(session["main_thread_models"][0]) == "sonnet"
    ]
    ramp_curve, ramp_curve_output_tokens = handoff_nudge._ramp_curve_from_corpus(sonnet_scoped_sessions)
    if ramp_curve_output_tokens == 0:
        ramp_curve, ramp_curve_output_tokens = handoff_nudge._ramp_curve_from_corpus(scoped_sessions)

    sessions_scanned = 0
    opus_anchored_sessions = 0
    no_boundary_sessions = 0
    boundary_is_final_turn_sessions = 0
    boundary_sessions = 0
    unpriced_turns = 0
    unpriced_tokens = 0

    corpus_arm_a_dollars = 0.0
    corpus_arm_b_dollars = 0.0
    corpus_arm_c_dollars = 0.0
    corpus_post_boundary_turns = 0
    corpus_post_boundary_output_tokens = 0

    real_switch_sessions = 0
    cache_miss_reason_counts: dict[str, int] = defaultdict(int)

    for data in scoped_sessions:
        sessions_scanned += 1
        unpriced_turns += data["unpriced_turns"]
        unpriced_tokens += data["unpriced_tokens"]

        main_thread_turns = data["main_thread_turns"]
        main_thread_priced = data["main_thread_priced"]
        main_thread_models = data["main_thread_models"]
        main_thread_record_positions = data["main_thread_record_positions"]
        deduped = data["deduped"]

        if not main_thread_turns or render._fam(main_thread_models[0]) != "opus":
            continue
        opus_anchored_sessions += 1

        boundary_index = _plan_boundary_turn_index(deduped, main_thread_record_positions)
        if boundary_index is None:
            no_boundary_sessions += 1
            continue

        post_boundary_turns = main_thread_turns[boundary_index + 1:]
        if not post_boundary_turns:
            boundary_is_final_turn_sessions += 1
            continue
        boundary_sessions += 1

        boundary_context_tokens = main_thread_turns[boundary_index][0]

        arm_a_dollars = sum(d for _c, _o, d in post_boundary_turns)
        post_boundary_output_tokens = sum(o for _c, o, _d in post_boundary_turns)

        arm_b_dollars = 0.0
        arm_c_dollars = 0.0
        for offset, turn_index in enumerate(range(boundary_index + 1, len(main_thread_turns))):
            # Arm A already contributes $0 for an unpriced turn (its actual_dollars is
            # 0.0); arms B/C must match that $0 instead of repricing raw tokens.
            if not main_thread_priced[turn_index]:
                continue
            rec = deduped[main_thread_record_positions[turn_index]]
            usage = (rec.get("message") or {}).get("usage") or {}
            if offset == 0:
                arm_b_dollars += _arm_b_boundary_plus_one_dollars(usage, boundary_context_tokens)
            else:
                arm_b_dollars += _arm_b_later_turn_dollars(usage)
            _context_at_turn, output_tokens, _actual_dollars = main_thread_turns[turn_index]
            arm_c_dollars += _arm_c_turn_dollars(output_tokens, offset, ramp_curve)

        corpus_arm_a_dollars += arm_a_dollars
        corpus_arm_b_dollars += arm_b_dollars
        corpus_arm_c_dollars += arm_c_dollars
        corpus_post_boundary_turns += len(post_boundary_turns)
        corpus_post_boundary_output_tokens += post_boundary_output_tokens

        # Ground truth: does the boundary+1 turn show a real model switch,
        # and does Claude Code's own cache_miss_reason diagnostic agree --
        # context only, never fed into the repricing formula above.
        if main_thread_models[boundary_index + 1] != main_thread_models[boundary_index]:
            real_switch_sessions += 1
            boundary_plus_one_rec = deduped[main_thread_record_positions[boundary_index + 1]]
            reason = pricing._cache_miss_reason(boundary_plus_one_rec.get("message") or {})
            cache_miss_reason_counts[reason or "(missing/malformed)"] += 1

    title_since = f"last {since_raw}" if since_raw else "all time"
    print(f"\n## Plan boundary report ({title_since}, generated {today.isoformat()})\n")
    print(f"Sessions scanned: {sessions_scanned:,}")
    print(f"Opus-anchored: {opus_anchored_sessions:,}")
    print(f"  No plan boundary detected: {no_boundary_sessions:,}")
    print(
        "  Boundary is the session's final main-thread turn"
        f" (excluded, no post-boundary work): {boundary_is_final_turn_sessions:,}"
    )
    print(f"  Plan-boundary sessions repriced: {boundary_sessions:,}")
    if unpriced_turns:
        print(f"  ({unpriced_turns:,} unpriced turns / {unpriced_tokens:,} tokens excluded from priced spend)")
    if ramp_curve_output_tokens == 0:
        print(
            "\nWARNING: no priced output tokens found anywhere in scope, so arm C's ramp curve could"
            " not be computed -- its figures below are priced at $0.00/1k, not a genuinely cheap ramp."
        )

    if boundary_sessions == 0:
        print("\nNo plan-boundary sessions with post-boundary work found in scope.")
        return

    print(f"\nPost-boundary main-thread turns repriced: {corpus_post_boundary_turns:,}")
    print(f"Post-boundary output tokens repriced: {corpus_post_boundary_output_tokens:,}")

    header = f"{'Arm':<24} {'$':>14}"
    print(f"\n{header}")
    print("-" * len(header))
    print(f"{'A: continue on Opus':<24} {corpus_arm_a_dollars:>14,.2f}")
    print(f"{'B: switch to Sonnet':<24} {corpus_arm_b_dollars:>14,.2f}")
    print(f"{'C: fresh Sonnet handoff':<24} {corpus_arm_c_dollars:>14,.2f}")

    print("\n## Work-inflation breakeven\n")
    print(
        "How much extra Sonnet work (post-boundary turns/output tokens) the cheaper arm in each"
        " pair could absorb before its dollar advantage disappears -- the mitigation for the"
        " unverifiable assumption that Sonnet completes the same post-boundary work Opus did."
    )
    pairs = (
        ("A vs B", corpus_arm_a_dollars, corpus_arm_b_dollars),
        ("A vs C", corpus_arm_a_dollars, corpus_arm_c_dollars),
        ("B vs C", corpus_arm_b_dollars, corpus_arm_c_dollars),
    )
    for label, left_dollars, right_dollars in pairs:
        left_label, right_label = label.split(" vs ")
        if left_dollars <= right_dollars:
            cheaper_dollars, delta_dollars, winner = left_dollars, right_dollars - left_dollars, left_label
        else:
            cheaper_dollars, delta_dollars, winner = right_dollars, left_dollars - right_dollars, right_label
        breakeven = _plan_boundary_work_inflation_breakeven(
            cheaper_dollars, delta_dollars, corpus_post_boundary_turns, corpus_post_boundary_output_tokens
        )
        if breakeven["pct"] is None:
            print(f"{label}: cheaper arm ({winner}) has $0.00 post-boundary spend -- no rate to extrapolate")
            continue
        print(
            f"{label}: {winner} cheaper by ${delta_dollars:,.2f} -- breakeven at"
            f" +{breakeven['pct'] * 100:,.1f}% more work"
            f" (~{breakeven['extra_turns']:,.0f} extra turns, ~{breakeven['extra_output_tokens']:,.0f}"
            " extra output tokens)"
        )

    print("\n## Ground truth: real model switch at boundary+1\n")
    print(
        f"Sessions with a real model change observed at boundary+1: {real_switch_sessions:,}"
        f" of {boundary_sessions:,}"
    )
    if cache_miss_reason_counts:
        print("cache_miss_reason at boundary+1, for those sessions:")
        for reason in sorted(cache_miss_reason_counts):
            print(f"  {reason}: {cache_miss_reason_counts[reason]:,}")
