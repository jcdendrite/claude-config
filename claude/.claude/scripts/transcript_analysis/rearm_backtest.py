"""The rearm-backtest command: cmd_rearm_backtest backtests candidate re-arm
band spacings for the handoff nudge's one-shot fire against the recorded
corpus. .claude/plans/handoff-nudge-rearm-backtest.md holds the design.

Imports handoff_nudge, render, and scope by module (attribute access, not by
name) -- see scope.py's own top-of-file comment for why.
"""
from __future__ import annotations

import argparse
import statistics
import sys
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path

from transcript_analysis import handoff_nudge, render, scope

_REARM_BACKTEST_DEFAULT_SPACINGS: tuple[int, ...] = (40_000, 80_000, 120_000)


def _hook_observable_boundaries(records: Sequence[dict]) -> list[int]:
    """Turn-count positions (0..N, where N is the session's own main-thread
    turn count) at which nudge-handoff-near-context-cap.sh could observe this
    session's growing context. `records` must already be
    _dedup_turns_by_request_id's output -- the same records a caller builds
    its own main_thread_turns list from -- so a returned boundary is directly
    usable as a slice/turn-count index into that list.

    UserPromptSubmit and Stop both check the transcript's latest recorded
    main-thread assistant usage (docs/handoff-nudge.md), so the two fire at
    the same observable point: right after a run of tool-call-only turns
    yields back to a genuine user message. Reusing _is_fresh_user_prompt for
    that user-message half (see its own docstring for what it filters) marks
    every INTERNAL boundary -- one per genuine user message, not one per
    turn, so a multi-tool-call stretch between two user messages contributes
    no boundary of its own. Session start (0, before any turn) and session
    end (N, the full turn count) are the two boundaries no user message can
    supply on their own, and both are always included: the hook's own header
    comment states it is "registered on both events so a session that
    crosses the threshold on its final turn, with no further user prompt,
    still gets warned" -- a boundary set with no session-end entry would make
    a last-turn crossing invisible to the simulation.

    A turn only counts toward the position (and thus toward a boundary) when
    it carries a usage block, matching exactly the predicate a caller uses to
    build main_thread_turns -- a main-thread assistant record with no usage
    block (a synthetic error record, see _dedup_turns_by_request_id's
    docstring) must not desync the two lists' shared indexing.
    """
    boundaries: list[int] = [0]
    main_turn_count = 0
    for rec in records:
        if rec.get("type") == "assistant" and not bool(rec.get("isSidechain")):
            if (rec.get("message") or {}).get("usage"):
                main_turn_count += 1
            continue
        if main_turn_count > 0 and render._is_fresh_user_prompt(rec) and boundaries[-1] != main_turn_count:
            boundaries.append(main_turn_count)
    if boundaries[-1] != main_turn_count:
        boundaries.append(main_turn_count)
    return boundaries


def _nudge_conversion_from_log(
    session_traces: dict[str, list[int]], log_entries_by_root: dict[Path, list[dict]]
) -> dict:
    """Classify each fired, in-scope session into one of four nudge-to-handoff
    conversion buckets, per the frozen classification in
    .claude/plans/handoff-nudge-deep-tail-lever.md.

    A session enters the classified population when it appears on at least
    one `nudged` log line AND has a surviving in-scope trace in
    session_traces -- the same population _operator_response_lag_from_log
    joins against. A nudged session with no in-scope trace is excluded and
    counted under "dropped" rather than guessed at (mirroring
    _operator_response_lag_from_log's own excluded_count). A `handoff` line
    for a session that never appears on a `nudged` line at all is outside
    this population and is neither classified nor counted -- this measures
    "did a nudged session convert," not "how many handoffs ran."

    log_entries_by_root groups _parse_nudge_log_entries' own per-root output
    -- a session's own nudged/handoff lines always land in one root's log,
    since the same account writes both. Neither line type carries a
    timestamp, so each root's own file order is the only chronological
    signal available. No cross-root merge is needed as a result.

    Buckets (mutually exclusive, decided by the first `handoff` line reached
    and whether any `action=block` line precedes it):
    - voluntary: a handoff line exists, no block precedes it
    - forced: a handoff line exists, at least one block precedes it
    - blocked_no_handoff: at least one block line, no handoff line
    - no_compliance: neither a block nor a handoff line

    Returns a dict with:
    - "voluntary", "forced", "blocked_no_handoff", "no_compliance": the four
      bucket counts above
    - "dropped": nudged sessions with no surviving in-scope trace, excluded
      rather than guessed at (see above)
    - "join_validity": voluntary + forced, the count of fired, in-scope
      sessions whose handoff line's session id actually matched a nudged
      session id
    - "ignored_values": the `ignored=` value on the last nudged line
      preceding the handoff line, one per voluntary session where that line
      carries the field
    - "no_ignored_field": voluntary sessions whose preceding nudged line
      carries no ignored= field -- counted separately and never defaulted to
      0, which would bias the distribution toward "complied immediately"

    A future nudge tier adding a third `action=` value must update this
    function's own `action == "block"` check (below) alongside
    _operator_response_lag_from_log's.
    """
    # A session id repeated across roots (stale symlink, merged log, PID
    # reuse) is not handled -- entries land in root-scan order.
    # Ordering is approximate because neither line type carries a
    # timestamp.
    per_session: dict[str, list[dict]] = defaultdict(list)
    for entries in log_entries_by_root.values():
        for entry in entries:
            if entry.get("kind") not in ("nudged", "handoff"):
                continue
            session = entry.get("session")
            if session:
                per_session[session].append(entry)

    voluntary = forced = blocked_no_handoff = no_compliance = 0
    dropped = no_ignored_field = 0
    ignored_values: list[int] = []

    for session, entries in per_session.items():
        if not any(e["kind"] == "nudged" for e in entries):
            continue
        if session not in session_traces:
            dropped += 1
            continue

        handoff_index = next((i for i, e in enumerate(entries) if e["kind"] == "handoff"), None)
        block_index = next(
            (i for i, e in enumerate(entries) if e["kind"] == "nudged" and e.get("action") == "block"),
            None,
        )

        if handoff_index is None:
            if block_index is not None:
                blocked_no_handoff += 1
            else:
                no_compliance += 1
            continue

        if block_index is not None and block_index < handoff_index:
            forced += 1
            continue

        voluntary += 1
        last_nudged = next(
            (e for e in reversed(entries[:handoff_index]) if e["kind"] == "nudged"), None
        )
        if last_nudged is not None and "ignored" in last_nudged:
            ignored_values.append(last_nudged["ignored"])
        else:
            no_ignored_field += 1

    return {
        "voluntary": voluntary,
        "forced": forced,
        "blocked_no_handoff": blocked_no_handoff,
        "no_compliance": no_compliance,
        "dropped": dropped,
        # Definitional sum of the two buckets above, not an independent join
        # re-check: the classification loop's own per-session bucket
        # assignment already requires a shared session id before either
        # bucket increments.
        "join_validity": voluntary + forced,
        "ignored_values": ignored_values,
        "no_ignored_field": no_ignored_field,
    }


def _simulate_rearm_spacing(
    main_thread_turns: Sequence[tuple[int, int, float]],
    boundaries: Sequence[int],
    spacing: int,
    ramp_curve: dict[str, dict[str, float]],
    threshold: int,
    *,
    response_lag_tokens: float = 0.0,
) -> tuple[float, float, float]:
    """Replay one session's main-thread turns under one candidate re-arm spacing.

    main_thread_turns is a (context_at_turn, output_tokens, actual_dollars)
    tuple per turn, in order; boundaries is _hook_observable_boundaries'
    output for the same session. Real context/output growth is replayed
    unmodified throughout -- band crossings are detected against the
    session's *actual* recorded trajectory, never a counterfactually-reset
    one. Turns before the first detected crossing keep their actual recorded
    dollars and context. Each crossing "splits" the session: turns from that
    point until the next crossing (or session end) are re-priced by mapping
    their distance from the split to a turns-since-a-fresh-restart position
    and applying ramp_curve's rate/mean_context at that position to the
    turn's own real output-token volume -- work stays constant, only the
    context-depth-driven rate changes, modeling what a fresh session would
    have billed for the same work rather than what the real, ever-growing
    prefix actually cost.

    A crossing is only detectable at a boundary in `boundaries`.
    response_lag_tokens (0.0 for the perfect-compliance model) shifts each
    band's trigger point later by that many tokens, modeling the empirically
    measured gap (_operator_response_lag_from_log) between a nudge firing and
    the operator actually acting on it, for the compliance-realistic model.

    Returns (total_dollars, context_weighted_sum, output_token_weight): the
    last two let a caller aggregate an output-token-weighted mean context
    ("C_bar", `cost ~= N x C_bar x rate` in .claude/plans/token-cost-reduction.md)
    across many sessions without re-deriving per-turn context outside this
    function.
    """
    boundary_set = set(boundaries)
    total = 0.0
    context_weighted = 0.0
    weight = 0.0
    fired_bands = 0
    turns_since_restart = 0
    in_actual_epoch = True

    for i, (context_at_turn, output_tokens, actual_dollars) in enumerate(main_thread_turns):
        if in_actual_epoch:
            total += actual_dollars
            context_weighted += context_at_turn * output_tokens
        else:
            label = handoff_nudge._ramp_curve_turn_index_bucket(turns_since_restart)
            bucket = ramp_curve.get(label, {"rate": 0.0, "mean_context": 0.0})
            total += (output_tokens / 1000) * bucket["rate"]
            context_weighted += bucket["mean_context"] * output_tokens
            turns_since_restart += 1
        weight += output_tokens

        abs_tokens = context_at_turn + output_tokens
        band_trigger = threshold + fired_bands * spacing + response_lag_tokens
        if abs_tokens >= band_trigger and (i + 1) in boundary_set:
            fired_bands += 1
            in_actual_epoch = False
            turns_since_restart = 0

    return total, context_weighted, weight


def _parse_rearm_spacings_arg(args: argparse.Namespace) -> list[int]:
    """Parse --spacings' comma-separated token list into a list of positive
    ints, defaulting to _REARM_BACKTEST_DEFAULT_SPACINGS. Exits 2 on a
    non-integer or non-positive value."""
    raw: str = getattr(args, "spacings", None) or ",".join(str(s) for s in _REARM_BACKTEST_DEFAULT_SPACINGS)
    spacings: list[int] = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            value = int(token)
        except ValueError:
            print(
                f"rearm-backtest: --spacings: expected comma-separated integers, got {token!r} in {raw!r}",
                file=sys.stderr,
            )
            sys.exit(2)
        if value <= 0:
            print(f"rearm-backtest: --spacings: values must be positive, got {value}", file=sys.stderr)
            sys.exit(2)
        spacings.append(value)
    if not spacings:
        print("rearm-backtest: --spacings: at least one spacing value is required", file=sys.stderr)
        sys.exit(2)
    return spacings


def _rearm_backtest_log_size_lines(
    per_root_sizes: Sequence[tuple[Path, int | None]],
    *,
    multi_root: bool,
    redact: bool,
    redact_ordinals: dict[Path, int],
) -> list[str]:
    """Render the nudge-log byte-size disclosure line(s) for
    _rearm_backtest_report from each root's already-resolved byte size
    (None means unreadable). Multi-root scope pools every root into one
    aggregate line: a per-root byte count is itself a per-account figure,
    which docs/private-project-redaction.md's Account-cardinality bar
    prohibits. Single-root scope prints that root's own account-N-labeled
    (or raw path under --no-redact) line directly.

    Pure over already-resolved sizes so it's unit-testable without a
    filesystem.
    """
    if multi_root:
        total_bytes = sum(size for _root, size in per_root_sizes if size is not None)
        # Boolean-only, never a count: same cardinality-leak concern as above.
        any_truncated = any(
            size is not None and size > handoff_nudge._NUDGE_LOG_MAX_READ for _root, size in per_root_sizes
        )
        any_unreadable = any(size is None for _root, size in per_root_sizes)
        note = ""
        if any_truncated:
            note += " (some roots truncated -- oldest lines dropped)"
        if any_unreadable:
            note += " (some roots unreadable)"
        return [f"  nudge logs across every resolved root: {total_bytes:,} bytes{note}"]

    # multi_root=False implies exactly one entry: the sole caller derives
    # multi_root from the same scan_roots that produced per_root_sizes.
    root, size = per_root_sizes[0]
    log_path = root.parent / ".handoff-nudge.log"
    root_label = f"account-{redact_ordinals[root.resolve()]}" if redact else str(log_path)
    if size is None:
        return [f"  {root_label} nudge log: unreadable"]
    truncated_note = " [truncated -- oldest lines dropped]" if size > handoff_nudge._NUDGE_LOG_MAX_READ else ""
    return [f"  {root_label} nudge log: {size:,} bytes{truncated_note}"]


def cmd_rearm_backtest(args: argparse.Namespace) -> None:
    """CLI entry point for the rearm-backtest subcommand.

    Root resolution happens here, at the CLI boundary, rather than inside
    _rearm_backtest_report, mirroring cmd_cost -- --config-dir validation
    exits before any scan work. The wall-clock date is read exactly once,
    here, mirroring cmd_cost_trend's own split.
    """
    roots = scope._resolve_cost_roots(args, subcommand="rearm-backtest")
    _rearm_backtest_report(args, datetime.now(UTC).date(), roots)


def _rearm_backtest_report(args: argparse.Namespace, today: date, roots: Sequence[Path] | None = None) -> None:
    """Backtest candidate re-arm band spacings against the recorded corpus.

    One row per candidate spacing (--spacings, default
    _REARM_BACKTEST_DEFAULT_SPACINGS) plus an unmodified baseline row
    (today's real recorded one-shot totals, i.e. spacing = never re-arm),
    each under both the perfect-compliance and compliance-realistic models
    (see _simulate_rearm_spacing's response_lag_tokens). Each session's first
    fire point is its own effective threshold (_hook_effective_fire_threshold,
    from that session's own model) -- unchanged from today's real hook
    behavior -- and only re-arm spacing PAST that point varies; model routing
    and the threshold computation itself are held fixed and printed as such,
    so a reader can't mistake "spacing-only" for "everything."
    """
    redact: bool = not bool(getattr(args, "no_redact", False))
    scan_roots: Sequence[Path] = roots if roots is not None else (scope.PROJECTS_DIR,)
    multi_root = len(scan_roots) > 1

    if not redact and multi_root:
        print(
            "rearm-backtest: --no-redact is refused when more than one root is in scope"
            " (--config-dir was given); drop --no-redact or scope to a single profile",
            file=sys.stderr,
        )
        sys.exit(2)
    if not redact:
        print(scope._DO_NOT_PUBLISH_BANNER)
        print(scope._DO_NOT_PUBLISH_BANNER, file=sys.stderr)

    spacings = _parse_rearm_spacings_arg(args)
    since_ts, since_raw = scope._parse_since_nd_arg(args, "rearm-backtest")
    branch_filter = scope._branch_filter(args)

    session_iter, scope_label = scope._resolve_project_scope(args, "rearm-backtest", roots=roots)
    scope.print_resolved_scope("rearm-backtest", scope_label, scan_roots)
    # Each in-scope session's records are deduped and priced exactly once,
    # via _extract_rearm_session_turns, and the same extraction dict feeds
    # both _ramp_curve_from_corpus and this function's own
    # sessions_data/session_traces bookkeeping below -- a single pass over
    # the corpus, matching every sibling subcommand in this file (`cost`,
    # `context-distribution`, etc.).
    scoped_sessions = [
        (jsonl, handoff_nudge._extract_rearm_session_turns(records)) for jsonl, records in session_iter
        if handoff_nudge._session_matches_rearm_scope(records, since_ts, branch_filter)
    ]

    ramp_curve, ramp_curve_output_tokens = handoff_nudge._ramp_curve_from_corpus(data for _jsonl, data in scoped_sessions)

    sessions_data: list[dict] = []
    session_traces: dict[str, list[int]] = {}
    sidechain_dollars_total = 0.0
    unpriced_turns = 0
    unpriced_tokens = 0

    for jsonl, data in scoped_sessions:
        sidechain_dollars_total += data["sidechain_dollars_total"]
        unpriced_turns += data["unpriced_turns"]
        unpriced_tokens += data["unpriced_tokens"]
        main_thread_turns = data["main_thread_turns"]
        if not main_thread_turns:
            continue

        session_id = jsonl.stem
        sessions_data.append({
            "session_id": session_id,
            "main_thread_turns": main_thread_turns,
            "boundaries": _hook_observable_boundaries(data["deduped"]),
            # The real hook resolves its threshold from whichever model is
            # active when it checks (_hook_effective_fire_threshold); a
            # session almost always stays on one model family, so
            # session_threshold (from its first main-thread turn's model)
            # approximates that check well enough for a single per-session
            # scalar -- exactly what _simulate_rearm_spacing's `threshold`
            # param takes.
            "threshold": data["session_threshold"],
        })
        session_traces[session_id] = [c + o for c, o, _d in main_thread_turns]

    if not sessions_data:
        print("No priced main-thread turns found in scope.")
        if unpriced_turns:
            print(f"  ({unpriced_turns:,} unpriced turns / {unpriced_tokens:,} tokens excluded from priced spend)")
        return

    # scan_roots is already resolved (with its own exit(2) handling) via
    # _resolve_cost_roots, so no config_dir() call is needed here. Per-root
    # join avoids biasing lag/conversion toward one account while
    # session_traces spans every root.
    log_entries_by_root: dict[Path, list[dict]] = {}
    per_root_sizes: list[tuple[Path, int | None]] = []
    for root in scan_roots:
        log_path = root.parent / ".handoff-nudge.log"
        log_entries_by_root[root] = handoff_nudge._parse_nudge_log_entries(log_path)
        # This duplicates _read_bounded_log_lines' own exists/stat/read guard
        # rather than reusing it. The two stay in sync only because both
        # currently catch plain OSError -- re-check both sites together if
        # either's caught exception type narrows.
        try:
            log_size = log_path.stat().st_size if log_path.exists() else 0
        except OSError:
            per_root_sizes.append((root, None))
            continue
        per_root_sizes.append((root, log_size))
    redact_ordinals: dict[Path, int] = scope._redaction_ordinals(scan_roots)
    for line in _rearm_backtest_log_size_lines(
        per_root_sizes, multi_root=multi_root, redact=redact, redact_ordinals=redact_ordinals
    ):
        print(line)
    log_entries = [entry for entries in log_entries_by_root.values() for entry in entries]
    lags, excluded_count = handoff_nudge._operator_response_lag_from_log(session_traces, log_entries)
    if lags:
        sorted_lags = sorted(lags)
        mid = len(sorted_lags) // 2
        median_lag = float(sorted_lags[mid]) if len(sorted_lags) % 2 else (sorted_lags[mid - 1] + sorted_lags[mid]) / 2
    else:
        median_lag = 0.0

    # Baseline: today's real recorded totals, no counterfactual repricing at all.
    baseline_main_dollars = sum(d for s in sessions_data for _c, _o, d in s["main_thread_turns"])
    baseline_context_weighted = sum(c * o for s in sessions_data for c, o, _d in s["main_thread_turns"])
    baseline_output_tokens = sum(o for s in sessions_data for _c, o, _d in s["main_thread_turns"])
    baseline_total = baseline_main_dollars + sidechain_dollars_total
    baseline_c_bar = (baseline_context_weighted / baseline_output_tokens) if baseline_output_tokens else 0.0

    title_since = f"last {since_raw}" if since_raw else "all time"
    print(f"\n## Re-arm spacing backtest ({title_since}, generated {today.isoformat()})\n")
    print(f"Sessions in scope: {len(sessions_data):,}")
    if unpriced_turns:
        print(f"  ({unpriced_turns:,} unpriced turns / {unpriced_tokens:,} tokens excluded from priced spend)")
    print(
        f"Operator-response-lag sample: {len(lags)} joined 'nudged' log line(s)"
        f" ({excluded_count} excluded -- no matching session in scope), median lag"
        f" {median_lag:,.0f} tokens past the fire point"
    )
    print(
        "\nModel routing and each session's own fire threshold (the lesser of 40% of its model's"
        " context window and the fixed 150,000-token _HANDOFF_NUDGE_ABS_CAP -- mirroring the hook's"
        " real behavior) are held fixed and are NOT backtested by this report -- only re-arm spacing"
        " past the first fire varies."
    )
    if ramp_curve_output_tokens == 0:
        print(
            "\nWARNING: no priced output tokens found anywhere in scope, so the re-arm ramp curve"
            " could not be computed -- every re-armed remainder below is priced at $0.00/1k, not a"
            " genuinely cheap ramp."
        )

    header = f"{'Spacing':>10} {'Model':>12} {'$':>14} {'DeltaUSD':>10} {'C_bar':>10} {'DeltaCbar':>12}"
    print(f"\n{header}")
    print("-" * len(header))
    print(
        f"{'baseline':>10} {'actual':>12} {baseline_total:>14,.2f} {'--':>10}"
        f" {baseline_c_bar:>10,.0f} {'--':>12}"
    )

    for spacing in spacings:
        for compliance_label, lag in (("perfect", 0.0), ("realistic", median_lag)):
            main_dollars = 0.0
            context_weighted = 0.0
            weight = 0.0
            for s in sessions_data:
                dollars, c_weighted, w = _simulate_rearm_spacing(
                    s["main_thread_turns"], s["boundaries"], spacing, ramp_curve,
                    s["threshold"], response_lag_tokens=lag,
                )
                main_dollars += dollars
                context_weighted += c_weighted
                weight += w
            total = main_dollars + sidechain_dollars_total
            c_bar = (context_weighted / weight) if weight else 0.0
            delta = total - baseline_total
            delta_c_bar = c_bar - baseline_c_bar
            print(
                f"{spacing:>10,} {compliance_label:>12} {total:>14,.2f} {delta:>+10,.2f}"
                f" {c_bar:>10,.0f} {delta_c_bar:>+12,.0f}"
            )

    conversion = _nudge_conversion_from_log(session_traces, log_entries_by_root)
    fired = (
        conversion["voluntary"] + conversion["forced"]
        + conversion["blocked_no_handoff"] + conversion["no_compliance"]
    )
    print(f"\n## Nudge->handoff conversion ({title_since}, generated {today.isoformat()})\n")
    print(f"Fired sessions in scope: {fired:,} ({conversion['dropped']:,} dropped -- no in-scope trace)")

    bucket_header = f"{'Bucket':<24} {'Count':>8} {'Rate':>8}"
    print(f"\n{bucket_header}")
    print("-" * len(bucket_header))
    for label, key in (
        ("voluntary", "voluntary"),
        ("forced", "forced"),
        ("blocked-no-handoff", "blocked_no_handoff"),
        ("no-compliance-observed", "no_compliance"),
    ):
        count = conversion[key]
        print(f"{label:<24} {count:>8,} {render._pct_of(count, fired):>8}")

    converted = conversion["voluntary"] + conversion["forced"]
    block_reach = conversion["forced"] + conversion["blocked_no_handoff"]
    print(
        f"\nConversion rate (voluntary + forced / fired): {render._pct_of(converted, fired)}"
        f" ({converted:,}/{fired:,})"
    )
    print(
        f"Block-reach rate (forced + blocked-no-handoff / fired): {render._pct_of(block_reach, fired)}"
        f" ({block_reach:,}/{fired:,})"
    )
    print(
        "Join validity (fired sessions with a matching handoff line):"
        f" {conversion['join_validity']:,}"
    )

    ignored_values = conversion["ignored_values"]
    if ignored_values:
        median_ignored = statistics.median(ignored_values)
        print(
            f"Re-arms tolerated at voluntary compliance: median ignored={median_ignored:,.0f}"
            f" across {len(ignored_values):,} voluntary session(s)"
            f" ({conversion['no_ignored_field']:,} voluntary session(s) missing ignored=)"
        )
    else:
        print(
            "Re-arms tolerated at voluntary compliance: no voluntary session(s) with ignored="
            f" present ({conversion['no_ignored_field']:,} voluntary session(s) missing ignored=)"
        )
