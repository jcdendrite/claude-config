"""The audit-routing command family: cmd_audit_routing,
cmd_audit_routing_shape, and cmd_audit_routing_samples -- per-turn Opus
routing-class classification, the code-read turn-shape distributions, and
a seeded sample of code-read turns for manual curation.

Imports corpus, pricing, redaction, render, and scope by module (attribute
access, not by name) -- see scope.py's own top-of-file comment for why.
"""
from __future__ import annotations

import argparse
import json
import random
import sys

from transcript_analysis import corpus, pricing, redaction, render, scope

# Skills that open a judgment span in audit-routing: any turn within an active span
# (from skill invocation until the next user turn) is classified as `judgment`, not
# by its tool-use contents. Extends REVIEW_TRACE_SKILLS with security-review,
# respond-pr, and ultrareview.
AUDIT_JUDGMENT_SKILLS: frozenset[str] = frozenset({
    "code-review", "plan-review", "ready-for-review", "skill-review",
    "agent-review", "security-review", "respond-pr", "ultrareview", "plan-it",
})


_AUDIT_CLASSES: tuple[str, ...] = (
    "orchestration", "judgment", "code-write", "code-read", "pure-thinking", "other"
)

_ORCHESTRATION_TOOLS: frozenset[str] = frozenset({"Agent", "Task"})
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
    if tool_names & corpus._CODE_WRITE_TOOLS:
        return "code-write"
    if tool_use_blocks and tool_names <= _CODE_READ_TOOLS:
        return "code-read"
    has_thinking = any(isinstance(b, dict) and b.get("type") == "thinking" for b in content)
    if has_thinking and not tool_use_blocks:
        return "pure-thinking"
    return "other"


def cmd_audit_routing(args: argparse.Namespace) -> None:
    """Per-turn Opus token breakdown by routing class across all sessions.

    Classifies every Opus assistant turn into: orchestration, judgment,
    code-write, code-read, pure-thinking, or other — then aggregates
    output_tokens and cache_read_input_tokens per class. Emits per-session
    rows sorted descending by total output tokens, plus a corpus aggregate.
    """
    top_n: int = getattr(args, "top", 20) or 20
    redact: bool = bool(getattr(args, "redact", False))

    since_ts, since_raw = scope._parse_since_nd_arg(args, "audit-routing")
    since_label = since_raw or ""

    roots = scope.resolve_scan_roots(args)
    multi_root = len(roots) > 1

    # scope._resolve_project_scope's fail-closed --this-repo check runs before
    # redaction._build_redact_map's full-corpus disk scan, so an out-of-repo failure
    # exits without paying for that scan.
    session_iter, scope_label = scope._resolve_project_scope(args, "audit-routing", roots=roots)
    scope.print_resolved_scope("audit-routing", scope_label, roots)

    redact_map: dict[redaction._RedactMapKey, str] = redaction._build_redact_map(roots) if redact else {}
    session_redact_map: dict[str, str] = {}
    # Only computed under multi-root redaction: scope._root_index_for_path needs
    # already-resolved roots, and scope._redaction_ordinals is the same
    # resolved-path-sorted mapping redaction._build_redact_map's keys and cost's own
    # per-row lookup share, so a row's ordinal always agrees with the map's.
    resolved_roots = [r.resolve() for r in roots] if (redact and multi_root) else []
    redact_ordinals = scope._redaction_ordinals(roots) if (redact and multi_root) else {}

    # Per-session accumulators: session_key → {class → {out, cr, dollars}}
    session_rows: list[dict] = []
    # Corpus totals: class → {out, cr, dollars}
    corpus_totals: dict[str, dict[str, float]] = {cls: {"out": 0, "cr": 0, "dollars": 0.0} for cls in _AUDIT_CLASSES}
    # Opus turns whose model ID has no _MODEL_BASE_INPUT_RATES entry — excluded from
    # the dollar headline, counted here so a corpus with unpriced turns doesn't
    # silently under-report (mirrors _cost_report's unpriced-tokens convention).
    unpriced_turns = 0
    unpriced_tokens = 0

    for jsonl, records in session_iter:
        # One API call = one turn: dedup merges a requestId run's content
        # blocks into one union list, so the classification and judgment-span
        # tracking below see every block (e.g. a Skill/ExitPlanMode tool_use
        # on a later block), while the run's dollars are attributed once.
        records = pricing.dedup_turns_by_request_id(records)
        proj_label = redaction._derive_proj_label(jsonl)
        session_id = jsonl.stem[:12]
        if redact:
            redaction._assign_session_redact_label(session_id, session_redact_map)
        if redact and multi_root:
            root_position = scope._root_index_for_path(jsonl, resolved_roots)
            redact_key: redaction._RedactMapKey = (redact_ordinals[resolved_roots[root_position]], proj_label)
        else:
            redact_key = proj_label

        # Per-session class token accumulators
        session_class_tokens: dict[str, dict[str, float]] = {
            cls: {"out": 0, "cr": 0, "dollars": 0.0} for cls in _AUDIT_CLASSES
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
                content_text = render._content_text(msg.get("content", ""))
                if "Plan mode is active" in content_text:
                    plan_mode_active = True
                continue

            if rtype != "assistant":
                continue

            # Filter to Opus turns with usage data
            model = msg.get("model", "")
            if render._fam(model) != "opus":
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
                rec_ts = corpus._parse_ts(rec.get("timestamp"))
                if rec_ts is None or rec_ts < since_ts:
                    continue

            content = msg.get("content") or []
            out_tokens: int = usage.get("output_tokens", 0)
            cr_tokens: int = usage.get("cache_read_input_tokens", 0)
            dollars_by_class, _context_at_turn, turn_unpriced_tokens = pricing._price_turn(model, usage)
            if dollars_by_class is None:
                unpriced_turns += 1
                unpriced_tokens += turn_unpriced_tokens
                turn_dollars = 0.0
            else:
                turn_dollars = sum(dollars_by_class.values())

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
            session_class_tokens[turn_class]["dollars"] += turn_dollars

        session_total_out = sum(v["out"] for v in session_class_tokens.values())
        if not session_total_out:
            continue

        session_rows.append({
            "session_id": session_id,
            "proj_label": proj_label,
            "redact_key": redact_key,
            "classes": session_class_tokens,
            "total_out": session_total_out,
        })

        for cls in _AUDIT_CLASSES:
            corpus_totals[cls]["out"] += session_class_tokens[cls]["out"]
            corpus_totals[cls]["cr"] += session_class_tokens[cls]["cr"]
            corpus_totals[cls]["dollars"] += session_class_tokens[cls]["dollars"]

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
        sid = redaction._redact_session_id(row["session_id"], session_redact_map) if redact else row["session_id"]
        proj = redaction._redact_proj_label(row["redact_key"], redact_map) if redact else row["proj_label"]
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

    sonnet_tier_dollars = corpus_totals["code-write"]["dollars"] + corpus_totals["code-read"]["dollars"]
    priced_total_dollars = sum(corpus_totals[cls]["dollars"] for cls in _AUDIT_CLASSES)
    dollar_pct = f"{100 * sonnet_tier_dollars / priced_total_dollars:.0f}%" if priced_total_dollars else "—"
    print(f"\nSonnet-tier estimate: ${sonnet_tier_dollars:,.2f}")
    print(f"  = {dollar_pct} of priced Opus spend in this window")
    if unpriced_turns:
        print(f"  ({unpriced_turns:,} unpriced turns / {unpriced_tokens:,} tokens excluded from priced spend)")

    sonnet_tier_out = corpus_totals["code-write"]["out"] + corpus_totals["code-read"]["out"]
    sonnet_pct = f"{100 * sonnet_tier_out / total_out_all:.0f}%" if total_out_all else "—"
    print(f"\nSonnet-tier estimate: {sonnet_tier_out:,} output tokens (secondary diagnostic)")
    print(f"  = {sonnet_pct} of Opus output in this window")


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
    since_ts, since_raw = scope._parse_since_nd_arg(args, "audit-routing-shape")
    since_label = since_raw or ""

    roots = scope.resolve_scan_roots(args)
    session_iter, scope_label = scope._resolve_project_scope(args, "audit-routing-shape", roots=roots)

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

    scope.print_resolved_scope("audit-routing-shape", scope_label, roots)

    for _jsonl, records in session_iter:
        # One API call = one turn: dedup merges a requestId run's content
        # blocks into one union list, so classification and file-count
        # counting below see every block, while the run's output tokens are
        # attributed once. Mirrors cmd_audit_routing's own dedup call.
        records = pricing.dedup_turns_by_request_id(records)
        session_turns: list[dict] = []

        # Judgment span state machine — duplicated from cmd_audit_routing intentionally.
        in_judgment_span: bool = False
        plan_mode_active: bool = False

        for rec in records:
            rtype = rec.get("type", "")
            msg = rec.get("message") or {}

            if rtype in ("user", "human"):
                in_judgment_span = False
                content_text = render._content_text(msg.get("content", ""))
                if "Plan mode is active" in content_text:
                    plan_mode_active = True
                # User turns act as streak breakers for D2 (recorded as spacers; out=0 so
                # D3 lookahead does not count them against the 3-Opus-turn window).
                session_turns.append({"class": "user", "out": 0, "d1_bucket": ""})
                continue

            if rtype != "assistant":
                continue

            model = msg.get("model", "")
            if render._fam(model) != "opus":
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
                rec_ts = corpus._parse_ts(rec.get("timestamp"))
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
    since_ts, since_raw = scope._parse_since_nd_arg(args, "audit-routing-samples")

    sample_n: int = getattr(args, "sample", 30) or 30
    seed: int | None = getattr(args, "seed", None)
    roots = scope.resolve_scan_roots(args)
    session_iter, scope_label = scope._resolve_project_scope(args, "audit-routing-samples", roots=roots)
    # stderr, not stdout: stdout is this subcommand's JSON/markdown data stream.
    scope.print_resolved_scope("audit-routing-samples", scope_label, roots, file=sys.stderr)

    candidates: list[dict] = []

    for jsonl, records in session_iter:
        session_id = jsonl.stem
        # One API call = one turn: dedup merges a requestId run's content
        # blocks into one union list, so classification below sees every
        # block (e.g. the first tool_use block promised by this function's
        # own docstring may land on a later raw record). Mirrors
        # cmd_audit_routing's own dedup call.
        records = pricing.dedup_turns_by_request_id(records)

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
                content_text = render._content_text(msg.get("content", ""))
                if "Plan mode is active" in content_text:
                    plan_mode_active = True
                user_text = render._content_text(msg.get("content", ""))
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

            if render._fam(model) != "opus":
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
                rec_ts = corpus._parse_ts(turn.get("rec_ts"))
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
                next_turn_text = render._content_text(next_entry["content"])
                next_turn_excerpt = next_turn_text[:200]
                break

            candidates.append({
                "session_id": session_id,
                "turn_index": turn_idx,
                "prior_user_message": prior_user_message,
                "assistant_tool_call": assistant_tool_call,
                "next_assistant_action": next_assistant_action,
                "next_turn_excerpt": next_turn_excerpt,
                "recent_assistant_text": render._recent_assistant_text(session_records, turn_idx, render._RECENT_LOOKBACK_N),
                "recent_tool_trail": render._recent_tool_trail(session_records, turn_idx, render._RECENT_LOOKBACK_N),
            })

    # Apply reproducible sampling.
    rng = random.Random(seed)
    rng.shuffle(candidates)
    candidates = candidates[:sample_n]

    output_format: str = getattr(args, "output_format", "json") or "json"
    if output_format == "md":
        print(render._format_samples_as_markdown(
            candidates,
            since_raw=since_raw,
            sample_n=sample_n,
            seed=seed,
        ))
    else:
        print(json.dumps(candidates, indent=2))
