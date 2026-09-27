"""The cache-rebuild command family: cmd_cache_rebuild and its report -- full-
prefix cache rebuilds after the vendor's 5m/1h cache TTL expires during an
idle gap, priced against a warm-cache read at the same token count
(.claude/plans/context-cost-root-cause.md records the corpus finding this
reproduces).

Imports corpus, pricing, redaction, render, and scope by module -- see
scope.py's own top-of-file comment for why. Imports cache_rebuild_rules'
constants and pure functions by name, since none is reassigned at runtime; a
test that patches one must also patch this module's binding.
"""
from __future__ import annotations

import argparse
import bisect
import statistics
import sys
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path

from transcript_analysis import corpus, pricing, redaction, render, scope
from transcript_analysis.cache_rebuild_rules import (
    _CACHE_MISS_REASON_MODEL_CHANGED,
    _CACHE_REBUILD_ATTRIBUTIONS,
    _CACHE_REBUILD_CAUSES,
    _CACHE_REBUILD_IDLE_5M_SECONDS,
    _CACHE_REBUILD_IDLE_GAP_CAUSES,
    _CACHE_REBUILD_ORIGINS,
    _CACHE_REBUILD_TIER_1H,
    _CACHE_REBUILD_TIER_5M,
    _CACHE_REBUILD_TTL_DOMINANT_TIER_SHARE_MIN,
    _CACHE_REBUILD_TTL_SENSITIVITY_BOUNDARY_SECONDS,
    _CAUSE_IDLE_5M_1H,
    _OWN_BASH_WAIT_SHAPES,
    _TTL_EXCLUDE_NEAR_TIE,
    _TTL_EXCLUDE_NO_DATA,
    _TTL_ROW_NA,
    _TTL_ROW_NOT_APPLICABLE,
    _TTL_TIER_NONE,
    _attribute_idle_gap_cause,
    _cache_rebuild_1h_to_5m_delta_dollars,
    _cache_rebuild_dominant_tier_share,
    _cache_rebuild_excess_dollars,
    _cache_rebuild_gap_seconds,
    _cache_rebuild_in_idle_5m_1h_band,
    _cache_rebuild_root_is_dominant,
    _cache_rebuild_root_verdict_input,
    _cache_rebuild_switch_delta_dollars,
    _cache_rebuild_tier_split_agreement,
    _cache_rebuild_token_tiebreaker_favors_5m,
    _cache_rebuild_ttl_verdict,
    _classify_cache_rebuild_cause,
    _negate_switch_delta_for_display,
)

_CACHE_REBUILD_DEFAULT_THRESHOLD = 100_000
_CACHE_REBUILD_DEFAULT_SINCE = "30d"


def cmd_cache_rebuild(args: argparse.Namespace) -> None:
    """CLI entry point for the cache-rebuild subcommand.

    Root resolution happens here, at the CLI boundary, mirroring cmd_cost --
    --config-dir validation exits before any scan work.
    """
    roots = scope._resolve_cost_roots(args, subcommand="cache-rebuild")
    _cache_rebuild_report(args, roots)


def _cache_rebuild_report(args: argparse.Namespace, roots: Sequence[Path] | None = None) -> None:
    """Idle-gap prompt-cache TTL-expiry rebuild measurement: per-call write
    distribution, cause classification, concurrency split, and priced
    excess, broken down by account-N ordinal. Redacted by default.

    Each session's own deduped turn sequence is scanned once per
    _read_session_file_partitioned group (the main thread, then each of its
    own subagent files) to classify every threshold-crossing cache write and
    to append every parseable-timestamp call into one corpus-wide
    (timestamp, transcript) index. is_first_call/gap_seconds/model_changed
    reset at every group boundary -- a group is its own context, so a delta
    taken across a boundary would compare two unrelated conversations (see
    _read_session_file_partitioned's own docstring). Within one group, that
    same reset is further keyed per origin (main vs. subagent, via each
    record's own isSidechain flag): an inline sidechain record living inside
    the main transcript file must never be classified against whichever
    record precedes it in file order when that record is the other origin.
    Binary-searches one pre-sorted global (timestamp, transcript) index per
    idle-gap call instead of re-scanning per gap -- O(n log n) total, not
    O(gaps x calls).

    `--since` only gates whether a call is *counted*, never whether it can
    see its own prior turn (same contract as _cost_report's since_ts).

    Also splits idle-gap rebuilds and the 5m-tier cache-write-token volume
    by origin (main vs. subagent), and prices the dollar delta a 5m-to-1h
    cacheTtl switch would make to subagent traffic. That delta is not
    simply the subagent share of the priced excess above, because the
    switch raises the write rate from 1.25x to 2x on every 5m-tier write
    the origin makes, not only the idle-gap-rebuilt tokens the excess
    figure already prices -- see
    .claude/plans/subagent-idle-gap-cache-rebuild-split.md's Approach
    section for the full derivation.

    roots is None only for this module's own tests exercising the report
    body directly; --this-repo/--config-dir CLI validation happens once in
    cmd_cache_rebuild.
    """
    redact: bool = not bool(getattr(args, "no_redact", False))
    ttl_verdict: bool = bool(getattr(args, "ttl_verdict", False))
    scan_roots: Sequence[Path] = roots if roots is not None else (scope.PROJECTS_DIR,)
    multi_root = len(scan_roots) > 1

    # Defense-in-depth: _resolve_cost_roots is the CLI-level enforcement
    # point for this refusal, but every direct caller of this function
    # (including this module's own tests) bypasses that boundary.
    if not redact and multi_root:
        print(
            "cache-rebuild: --no-redact is refused when more than one root is in scope"
            " (--config-dir was given); drop --no-redact or scope to a single profile",
            file=sys.stderr,
        )
        sys.exit(2)

    if not redact:
        print(scope._DO_NOT_PUBLISH_BANNER)
        print(scope._DO_NOT_PUBLISH_BANNER, file=sys.stderr)

    threshold_arg = getattr(args, "threshold", None)
    threshold: int = _CACHE_REBUILD_DEFAULT_THRESHOLD if threshold_arg is None else int(threshold_arg)
    since_ts, since_raw = scope._parse_since_nd_arg(args, "cache-rebuild")
    since_label = since_raw or ""

    session_iter, scope_label = scope._resolve_project_scope(args, "cache-rebuild", include_subagents=True, roots=roots)

    # redact_map is used only for the fingerprint below (this report prints
    # no per-row project label), but the fingerprint must hash the same
    # full-corpus label set every other --redact caller does to stay
    # cross-run comparable, and that set can differ from session_iter's own
    # (possibly --projects-narrowed) scope, so this second disk scan cannot
    # be folded into the one below.
    redact_map: dict[redaction._RedactMapKey, str] = redaction._build_redact_map(roots) if redact else {}
    if redact:
        print(
            f"Corpus fingerprint: {redaction._corpus_fingerprint(redact_map)}"
            "  (private-project labels are not comparable across a different fingerprint)"
        )
    scope.print_resolved_scope("cache-rebuild", scope_label, scan_roots)

    resolved_scan_roots = [root.resolve() for root in scan_roots] if multi_root else []
    redact_ordinals: dict[Path, int] = scope._redaction_ordinals(scan_roots)
    single_root_ordinal: int | None = redact_ordinals[scan_roots[0].resolve()] if not multi_root else None

    total_calls_in_scope = 0
    tail_write_sizes: list[int] = []
    cause_counts: dict[str, int] = dict.fromkeys(_CACHE_REBUILD_CAUSES, 0)
    idle_gap_candidates: list[dict] = []
    global_timeline: list[tuple[float, str]] = []
    unpriced_idle_gap_turns = 0
    unpriced_idle_gap_tokens = 0
    # Every account ordinal in scope is pre-seeded with a zero row -- a
    # valid-but-empty root, or one with no idle-gap rebuilds, still renders
    # a clean zero-state row instead of vanishing from the breakdown.
    per_account_rebuilds: dict[int, int] = dict.fromkeys(redact_ordinals.values(), 0) if multi_root else {}
    per_account_excess: dict[int, float] = dict.fromkeys(redact_ordinals.values(), 0.0) if multi_root else {}

    # Origin split (main vs. subagent, per-record via isSidechain -- see
    # this function's own docstring). Always seeded with both keys, unlike
    # the per-account dicts above, since the origin split prints
    # unconditionally rather than only under a multi-root scope -- a corpus
    # with no sidechain records at all must still render a zero subagent
    # row rather than vanishing.
    origin_rebuilds: dict[str, int] = dict.fromkeys(_CACHE_REBUILD_ORIGINS, 0)
    origin_excess: dict[str, float] = dict.fromkeys(_CACHE_REBUILD_ORIGINS, 0.0)
    # Subagent-origin idle-gap cause attribution (see
    # .claude/plans/subagent-idle-gap-cause-attribution.md's Approach
    # section) -- zero-seeded for the same zero-state-row reason as the
    # origin dicts above. attribution_shares holds each attributed
    # candidate's covered_share for the table's per-row median.
    attribution_rebuilds: dict[str, int] = dict.fromkeys(_CACHE_REBUILD_ATTRIBUTIONS, 0)
    attribution_excess: dict[str, float] = dict.fromkeys(_CACHE_REBUILD_ATTRIBUTIONS, 0.0)
    attribution_band_excess: dict[str, float] = dict.fromkeys(_CACHE_REBUILD_ATTRIBUTIONS, 0.0)
    attribution_shares: dict[str, list[float]] = {attribution: [] for attribution in _CACHE_REBUILD_ATTRIBUTIONS}
    # Own-Bash wait-shape sub-split. Zero-seeded for the same zero-state-row
    # reason as attribution_* above. Populated only for candidates whose
    # bash_shape is not None -- the subset of the "waiting on own Bash call" row.
    bash_shape_rebuilds: dict[str, int] = dict.fromkeys(_OWN_BASH_WAIT_SHAPES, 0)
    bash_shape_excess: dict[str, float] = dict.fromkeys(_OWN_BASH_WAIT_SHAPES, 0.0)
    bash_shape_band_excess: dict[str, float] = dict.fromkeys(_OWN_BASH_WAIT_SHAPES, 0.0)
    bash_shape_shares: dict[str, list[float]] = {shape: [] for shape in _OWN_BASH_WAIT_SHAPES}
    # W5m/X/switch-delta (Approach section) -- accumulated threshold-
    # independently (every in-scope 5m-tier write, not only tail calls),
    # since the 2x uplift a cacheTtl switch would charge applies to warm
    # incremental writes too, not just rebuilds.
    w5m_by_origin: dict[str, int] = dict.fromkeys(_CACHE_REBUILD_ORIGINS, 0)
    x_by_origin: dict[str, int] = dict.fromkeys(_CACHE_REBUILD_ORIGINS, 0)
    switch_delta_by_origin: dict[str, float] = dict.fromkeys(_CACHE_REBUILD_ORIGINS, 0.0)
    unpriced_switch_delta_turns = 0
    unpriced_switch_delta_tokens = 0
    # This family is never read on the default path: w5m_by_origin/x_by_origin/
    # switch_delta_by_origin above remain the sole source for every
    # default-path figure.
    w5m_by_origin_root: dict[tuple[str, int], int] = defaultdict(int)
    w5m_dollars_by_origin_root: dict[tuple[str, int], float] = defaultdict(float)
    x_by_origin_root: dict[tuple[str, int], int] = defaultdict(int)
    switch_delta_5m_to_1h_by_origin_root: dict[tuple[str, int], float] = defaultdict(float)
    # Duplicates the idle-band classification at
    # _CACHE_REBUILD_TTL_SENSITIVITY_BOUNDARY_SECONDS instead of
    # _CACHE_REBUILD_IDLE_5M_SECONDS, for the two-point sensitivity check.
    switch_delta_5m_to_1h_at_60_by_origin_root: dict[tuple[str, int], float] = defaultdict(float)
    w1h_by_origin_root: dict[tuple[str, int], int] = defaultdict(int)
    w1h_dollars_by_origin_root: dict[tuple[str, int], float] = defaultdict(float)
    z_by_origin_root: dict[tuple[str, int], int] = defaultdict(int)
    switch_delta_1h_to_5m_by_origin_root: dict[tuple[str, int], float] = defaultdict(float)
    # Duplicates the idle-band classification at
    # _CACHE_REBUILD_TTL_SENSITIVITY_BOUNDARY_SECONDS instead of
    # _CACHE_REBUILD_IDLE_5M_SECONDS, for the two-point sensitivity check.
    switch_delta_1h_to_5m_at_60_by_origin_root: dict[tuple[str, int], float] = defaultdict(float)
    # Calls this family skips for lacking a price-table entry, pooled across
    # both directions and every root -- disclosed in the --ttl-verdict
    # section rather than split per root, since a call is unpriced by model,
    # not by which root or direction it landed in.
    unpriced_ttl_verdict_turns = 0
    unpriced_ttl_verdict_tokens = 0
    # Cross-tab of the gap-derived idle-5m-1h cause against
    # pricing._cache_miss_reason's own "model_changed" signal -- never feeds
    # back into any accumulator above.
    cache_miss_reason_agree = 0
    cache_miss_reason_discrepancy = 0
    # One entry per subagent-file group (one dispatch's own conversation),
    # for the ex-post per-dispatch dispersion figures -- kept separate from
    # w5m_by_origin/x_by_origin above, which pool every subagent-origin
    # record regardless of which group (or, for an inline sidechain record,
    # which file) it came from.
    per_group_dispersion: list[dict] = []
    # Priced subagent-origin W5m tokens that landed inside a subagent-file
    # group -- a strict subset of w5m_by_origin["subagent"] above. The gap
    # (printed as the dispersion block's coverage disclosure) is:
    #   - an unpriced subagent-origin call (enters no group, priced or not)
    #   - an inline sidechain record inside the main transcript file
    #     (group_index == 0, so is_subagent_group is False even though its
    #     own origin is "subagent")
    subagent_origin_w5m_in_groups = 0

    for jsonl, _flat_records in session_iter:
        session_key = str(jsonl.resolve())

        account_ordinal: int | None = None
        if multi_root:
            root_position = scope._root_index_for_path(jsonl, resolved_scan_roots)
            account_ordinal = redact_ordinals[resolved_scan_roots[root_position]]
        elif single_root_ordinal is not None:
            account_ordinal = single_root_ordinal

        # session_iter already read and parsed this file once internally (to
        # decide whether to yield it at all); this second, partitioned read
        # is the cost of reusing _resolve_project_scope's shared iterator,
        # which has no variant that also exposes the per-file group boundary
        # classification needs (mirrors read-scope's own _scan_read_scope_session
        # call site). Classification (is_first_call/gap_seconds/model_changed)
        # resets at every group boundary: each group is its own context (the
        # main thread, or one subagent's own turn sequence), so a delta taken
        # across a boundary would compare two unrelated conversations (see
        # _read_session_file_partitioned's own docstring). session_key stays
        # file-level across every group, though -- a subagent's own calls are
        # still this session's activity for the concurrency check below, not
        # another session's.
        for group_index, group in enumerate(corpus._read_session_file_partitioned(jsonl, include_subagents=True)):
            group_records = pricing.dedup_turns_by_request_id(group)

            # A subagent-file group is one dispatch's own conversation --
            # group_index 0 is always the main transcript (see
            # _read_session_file_partitioned's own docstring). This is used
            # only for the per-dispatch dispersion figures below, never for
            # origin classification itself: an inline sidechain record can
            # still appear inside group_index 0, and is classified as
            # subagent origin regardless via its own isSidechain flag.
            is_subagent_group = group_index > 0
            group_w5m_tokens = 0
            group_x_tokens = 0
            group_delta_dollars = 0.0

            # Sequential classification state, keyed per origin (mirroring
            # _scan_cache_efficiency_group's own chain_key = (session_key,
            # thread) pattern) rather than shared across the whole group --
            # an inline sidechain record interleaved with main-thread
            # records must never be classified against the other origin's
            # own prior call.
            chain_state: dict[str, dict] = {
                origin: {"i": 0, "prev_ts": None, "prev_index": None, "prev_model": None}
                for origin in _CACHE_REBUILD_ORIGINS
            }

            for idx, rec in enumerate(group_records):
                if rec.get("type") != "assistant":
                    continue
                msg = rec.get("message") or {}
                usage = msg.get("usage")
                if not usage:
                    continue
                model = msg.get("model", "")
                if model == "<synthetic>":
                    continue

                origin = "subagent" if bool(rec.get("isSidechain")) else "main"
                chain = chain_state[origin]

                cur_ts = corpus._parse_ts(rec.get("timestamp"))
                is_first_call = chain["i"] == 0
                gap_seconds = None if is_first_call else _cache_rebuild_gap_seconds(chain["prev_ts"], cur_ts)
                model_changed = not is_first_call and model != chain["prev_model"]
                eph_1h, eph_5m = pricing._cache_write_split(usage)
                pure_1h_tier_write = eph_1h > 0 and eph_5m == 0
                cause = _classify_cache_rebuild_cause(is_first_call, gap_seconds, model_changed, pure_1h_tier_write)

                # Every parseable-timestamp call is corpus "activity", tail or
                # not -- the concurrency check below asks whether ANY call
                # happened during a gap, regardless of that call's own size.
                if cur_ts is not None:
                    global_timeline.append((cur_ts, session_key))

                in_scope = since_ts is None or (cur_ts is not None and cur_ts >= since_ts)
                if in_scope:
                    total_calls_in_scope += 1

                # Threshold-independent: W5m/X accumulate over every
                # in-scope 5m-tier write, not only tail (>= threshold)
                # calls -- the 2x uplift a cacheTtl switch would charge
                # applies to warm incremental writes too, not only rebuilds.
                if in_scope and eph_5m > 0:
                    w5m_by_origin[origin] += eph_5m
                    is_idle_5m_1h_cause = cause == _CAUSE_IDLE_5M_1H
                    if is_idle_5m_1h_cause:
                        x_by_origin[origin] += eph_5m
                    delta_dollars, turn_unpriced_tokens = _cache_rebuild_switch_delta_dollars(
                        model, usage, is_idle_5m_1h_cause=is_idle_5m_1h_cause
                    )
                    if delta_dollars is None:
                        unpriced_switch_delta_turns += 1
                        unpriced_switch_delta_tokens += turn_unpriced_tokens
                    else:
                        switch_delta_by_origin[origin] += delta_dollars
                        if is_subagent_group:
                            group_w5m_tokens += eph_5m
                            if is_idle_5m_1h_cause:
                                group_x_tokens += eph_5m
                            group_delta_dollars += delta_dollars
                            if origin == "subagent":
                                subagent_origin_w5m_in_groups += eph_5m

                # --ttl-verdict's own second, parallel accumulation: keyed
                # on (origin, root_ordinal), never read on the default
                # path above. root_key's own ordinal is always an int here
                # (redact_ordinals seeds single_root_ordinal too), but the
                # None guard mirrors the per_account_* sites' own defensive
                # style.
                if ttl_verdict and in_scope and account_ordinal is not None:
                    root_key = (origin, account_ordinal)
                    read_tokens = int(usage.get("cache_read_input_tokens", 0))
                    # The idle flags here are the gap test alone.
                    # A call's own cache-write tier gates whether a 5-minute expiry
                    # forced its write, not whether a live 1-hour tier served its read.
                    is_idle_primary = _cache_rebuild_in_idle_5m_1h_band(is_first_call, gap_seconds)
                    is_idle_sensitivity = _cache_rebuild_in_idle_5m_1h_band(
                        is_first_call, gap_seconds,
                        idle_5m_boundary_seconds=_CACHE_REBUILD_TTL_SENSITIVITY_BOUNDARY_SECONDS,
                    )

                    in_w5m_branch = eph_5m > 0
                    # A call in the sensitivity idle band (the wider of the
                    # two boundaries) may carry read tokens the mirror
                    # direction needs even when it wrote no 1h-tier tokens
                    # at all -- the common case for a live-1h-tier root's
                    # warm read.
                    in_w1h_branch = eph_1h > 0 or (read_tokens > 0 and is_idle_sensitivity)

                    # Unpriced-ness depends only on model/usage, the same
                    # regardless of which branch(es) below this record
                    # enters, so it's resolved once here rather than once
                    # per branch -- a record eligible for both branches (a
                    # mixed-tier write, or a sensitivity-band warm read
                    # alongside a 5m-tier write) is counted at most once.
                    dollars_by_class: dict[str, float] | None = None
                    if in_w5m_branch or in_w1h_branch:
                        dollars_by_class, _context_at_turn, turn_unpriced_tokens = pricing._price_turn(model, usage)
                        if dollars_by_class is None:
                            unpriced_ttl_verdict_turns += 1
                            unpriced_ttl_verdict_tokens += turn_unpriced_tokens

                    if in_w5m_branch:
                        w5m_by_origin_root[root_key] += eph_5m
                        # Reuses _price_turn's own priced classes so the margin denominator
                        # carries the same fast-mode/US-geo multipliers the net's own
                        # per-call delta already applies.
                        if dollars_by_class is not None:
                            w5m_dollars_by_origin_root[root_key] += dollars_by_class["cache_write_5m"]
                        # X is a primary-boundary-only quantity (the report's
                        # own display column) -- only switch_delta_5m_to_1h_*
                        # needs the sensitivity boundary too, for the
                        # two-point margin check.
                        if is_idle_primary:
                            x_by_origin_root[root_key] += eph_5m
                        for is_idle_at_boundary, delta_dict in (
                            (is_idle_primary, switch_delta_5m_to_1h_by_origin_root),
                            (is_idle_sensitivity, switch_delta_5m_to_1h_at_60_by_origin_root),
                        ):
                            boundary_delta, _turn_unpriced_tokens = _cache_rebuild_switch_delta_dollars(
                                model, usage, is_idle_5m_1h_cause=is_idle_at_boundary
                            )
                            if boundary_delta is not None:
                                delta_dict[root_key] += boundary_delta

                    if in_w1h_branch:
                        w1h_by_origin_root[root_key] += eph_1h
                        if dollars_by_class is not None:
                            w1h_dollars_by_origin_root[root_key] += dollars_by_class["cache_write_1h"]
                        # Z is a primary-boundary-only quantity, the same
                        # reason as X above.
                        if is_idle_primary:
                            z_by_origin_root[root_key] += read_tokens
                        for is_idle_at_boundary, delta_dict in (
                            (is_idle_primary, switch_delta_1h_to_5m_by_origin_root),
                            (is_idle_sensitivity, switch_delta_1h_to_5m_at_60_by_origin_root),
                        ):
                            boundary_delta, _turn_unpriced_tokens = _cache_rebuild_1h_to_5m_delta_dollars(
                                model, usage, is_idle_5m_1h_cause=is_idle_at_boundary
                            )
                            if boundary_delta is not None:
                                delta_dict[root_key] += boundary_delta

                    # Disclosed only, never fed back into any accumulator
                    # above -- see _CAUSE_IDLE_5M_1H's own docstring caveat
                    # that a model/effort switch outside the classified
                    # window can masquerade as idle-gap expiry.
                    if cause == _CAUSE_IDLE_5M_1H:
                        if pricing._cache_miss_reason(msg) == _CACHE_MISS_REASON_MODEL_CHANGED:
                            cache_miss_reason_discrepancy += 1
                        else:
                            cache_miss_reason_agree += 1

                write_tokens = eph_1h + eph_5m
                in_tail = write_tokens >= threshold

                if in_tail and in_scope:
                    tail_write_sizes.append(write_tokens)
                    cause_counts[cause] += 1

                    if cause in _CACHE_REBUILD_IDLE_GAP_CAUSES:
                        excess_dollars, turn_unpriced_tokens = _cache_rebuild_excess_dollars(model, usage)
                        if excess_dollars is None:
                            unpriced_idle_gap_turns += 1
                            unpriced_idle_gap_tokens += turn_unpriced_tokens
                        else:
                            attribution: str | None = None
                            covered_share: float | None = None
                            bash_shape: str | None = None
                            if origin == "subagent":
                                window = group_records[chain["prev_index"] + 1 : idx]
                                attribution, covered_share, bash_shape = _attribute_idle_gap_cause(
                                    group_records[chain["prev_index"]], window,
                                    gap_start_ts=chain["prev_ts"], gap_seconds=gap_seconds,
                                )
                            idle_gap_candidates.append({
                                "session_key": session_key,
                                "gap_start_ts": chain["prev_ts"],
                                "gap_end_ts": cur_ts,
                                "excess_dollars": excess_dollars,
                                "account_ordinal": account_ordinal,
                                "origin": origin,
                                "cause": cause,
                                "attribution": attribution,
                                "covered_share": covered_share,
                                "bash_shape": bash_shape,
                            })

                chain["prev_ts"] = cur_ts if cur_ts is not None else chain["prev_ts"]
                # prev_index names the record prev_ts came from -- paired so
                # a later candidate's window always starts right after the
                # record whose timestamp is that candidate's gap_start_ts.
                chain["prev_index"] = idx if cur_ts is not None else chain["prev_index"]
                chain["prev_model"] = model
                chain["i"] += 1

            if is_subagent_group:
                per_group_dispersion.append({
                    "w5m": group_w5m_tokens,
                    "x": group_x_tokens,
                    "delta_dollars": group_delta_dollars,
                })

    # One sort, once, over the whole corpus -- every idle-gap candidate below
    # binary-searches this same index rather than re-scanning per gap.
    global_timeline.sort(key=lambda entry: entry[0])
    global_ts = [ts for ts, _key in global_timeline]
    global_keys = [key for _ts, key in global_timeline]

    concurrent_rebuilds = 0
    concurrent_excess = 0.0
    idle_break_rebuilds = 0
    idle_break_excess = 0.0

    for cand in idle_gap_candidates:
        # Open interval: bisect_right/bisect_left exclude a call landing
        # exactly on gap_start_ts or gap_end_ts, since those endpoints are
        # this transcript's own calls.
        # The exclusion is timestamp-value-based, so a different concurrent
        # session's call at that same exact instant is also excluded, not
        # just this transcript's own.
        lo = bisect.bisect_right(global_ts, cand["gap_start_ts"])
        hi = bisect.bisect_left(global_ts, cand["gap_end_ts"])
        # Indexed range with early exit, not global_keys[lo:hi], so a
        # concurrent-activity match short-circuits without first copying the
        # whole candidate window.
        other_active = any(global_keys[j] != cand["session_key"] for j in range(lo, hi))
        if other_active:
            concurrent_rebuilds += 1
            concurrent_excess += cand["excess_dollars"]
        else:
            idle_break_rebuilds += 1
            idle_break_excess += cand["excess_dollars"]
        if multi_root and cand["account_ordinal"] is not None:
            per_account_rebuilds[cand["account_ordinal"]] += 1
            per_account_excess[cand["account_ordinal"]] += cand["excess_dollars"]
        origin_rebuilds[cand["origin"]] += 1
        origin_excess[cand["origin"]] += cand["excess_dollars"]
        attribution = cand["attribution"]
        if attribution is not None:
            attribution_rebuilds[attribution] += 1
            attribution_excess[attribution] += cand["excess_dollars"]
            if cand["cause"] == _CAUSE_IDLE_5M_1H:
                attribution_band_excess[attribution] += cand["excess_dollars"]
            if cand["covered_share"] is not None:
                attribution_shares[attribution].append(cand["covered_share"])
        bash_shape = cand["bash_shape"]
        if bash_shape is not None:
            bash_shape_rebuilds[bash_shape] += 1
            bash_shape_excess[bash_shape] += cand["excess_dollars"]
            if cand["cause"] == _CAUSE_IDLE_5M_1H:
                bash_shape_band_excess[bash_shape] += cand["excess_dollars"]
            if cand["covered_share"] is not None:
                bash_shape_shares[bash_shape].append(cand["covered_share"])

    title_since = f"last {since_label}" if since_label else "all time"
    print(f"\n## Cache-rebuild report ({title_since}, threshold >= {threshold:,} cache-write tokens)\n")

    total_tail_calls = sum(cause_counts.values())
    print(f"Calls scanned: {total_calls_in_scope:,}")
    print(
        f"Calls writing >= {threshold:,} tokens: {total_tail_calls:,}"
        f" ({render._pct_of(total_tail_calls, total_calls_in_scope)} of calls)"
    )

    if tail_write_sizes:
        sorted_sizes = sorted(tail_write_sizes)
        median = statistics.median(sorted_sizes)
        p90 = sorted_sizes[min(len(sorted_sizes) - 1, int(0.9 * (len(sorted_sizes) - 1)))]
        print(
            f"Per-call write distribution: min={sorted_sizes[0]:,}  median={median:,.0f}"
            f"  p90={p90:,}  max={sorted_sizes[-1]:,}"
        )

    print("\n## Cause breakdown\n")
    print(f"{'Cause':<32} {'Calls':>8} {'Share':>7}")
    for cause in _CACHE_REBUILD_CAUSES:
        count = cause_counts[cause]
        print(f"{cause:<32} {count:>8,} {render._pct_of(count, total_tail_calls):>7}")

    idle_gap_total = concurrent_rebuilds + idle_break_rebuilds
    idle_gap_excess = concurrent_excess + idle_break_excess
    print(
        "\n## Idle-gap concurrency split [unverified]\n\n"
        "Classifies each idle-gap rebuild by whether any other transcript, in any\n"
        "account, had a call inside the gap window. This is an association, not proof\n"
        "the operator was attending that other session. [unverified]\n"
    )
    print(f"{'':<28} {'Rebuilds':>9} {'Excess $':>12}")
    print(f"{'Another session active':<28} {concurrent_rebuilds:>9,} {concurrent_excess:>12,.2f}")
    print(f"{'Everything idle (a break)':<28} {idle_break_rebuilds:>9,} {idle_break_excess:>12,.2f}")
    print(f"{'Total idle-gap rebuilds':<28} {idle_gap_total:>9,} {idle_gap_excess:>12,.2f}")

    if multi_root:
        print("\n## Idle-gap excess by account\n")
        print(f"{'Account':<16} {'Rebuilds':>9} {'Excess $':>12}")
        for ordinal in sorted(per_account_rebuilds):
            print(f"{f'account-{ordinal}':<16} {per_account_rebuilds[ordinal]:>9,} {per_account_excess[ordinal]:>12,.2f}")

    if unpriced_idle_gap_turns:
        print(
            f"\n  ({unpriced_idle_gap_turns:,} idle-gap tail calls / {unpriced_idle_gap_tokens:,} tokens"
            " excluded from priced excess -- model has no price-table entry)"
        )

    print("\n## Idle-gap rebuilds by origin\n")
    print(f"{'Origin':<10} {'Rebuilds':>9} {'Excess $':>12}")
    for origin in _CACHE_REBUILD_ORIGINS:
        print(f"{origin:<10} {origin_rebuilds[origin]:>9,} {origin_excess[origin]:>12,.2f}")

    print(
        "\n## Subagent idle-gap cause attribution [unverified]\n\n"
        "Sub-classifies the subagent row above (idle 5m-1h and idle >1h pooled,\n"
        "so the rows below sum exactly to that row) by the last marker record\n"
        "found in each gap's own window, scanning forward:\n"
        "  - a tool_result for that call's own Bash tool_use -> waiting on own Bash call\n"
        "  - a background-task notification -> waiting on background task\n"
        "  - a coordinator message -> waiting on coordinator message\n"
        "  - no marker at all -> unattributed\n"
        "Last marker before the gap-closing call wins. 5m-1h $ restricts Excess $\n"
        "to the idle 5m-1h band only, the band a cacheTtl switch could actually\n"
        "rescue (idle >1h stays cold under either tier). Median cov. is the\n"
        "median (marker_ts - gap_start_ts) / gap_seconds across each row's own\n"
        "attributed candidates, not clamped to [0, 1]: near 100% means the\n"
        "marker sits at the gap's end and the attribution is tight, a low value\n"
        "means the marker landed early and most of the gap is still unexplained.\n"
        "It renders n/a whenever the winning marker's own timestamp is missing\n"
        "or unparseable, which never changes the cause itself. Main origin is\n"
        "excluded from this sub-table because experimental.cacheTtl is a\n"
        "subagent-frontmatter lever and cannot reach main-conversation\n"
        "traffic. The main bucket's own lever is promptCacheTtl. A large\n"
        "'unattributed' share means the marker taxonomy is incomplete, not that\n"
        "the gaps are causeless -- a transcript records the marker the harness\n"
        "delivered, never a statement of why the subagent was idle. [unverified]\n"
    )
    print(f"{'Cause':<32} {'Rebuilds':>9} {'Excess $':>12} {'5m-1h $':>12} {'Median cov.':>12}")
    for attribution in _CACHE_REBUILD_ATTRIBUTIONS:
        shares = attribution_shares[attribution]
        median_cov = render._pct_of(statistics.median(shares), 1.0) if shares else "n/a"
        print(
            f"{attribution:<32} {attribution_rebuilds[attribution]:>9,}"
            f" {attribution_excess[attribution]:>12,.2f} {attribution_band_excess[attribution]:>12,.2f}"
            f" {median_cov:>12}"
        )

    print(
        "\n## Own-Bash wait shape [unverified]\n\n"
        "Sub-splits the 'waiting on own Bash call' row above (the three rows\n"
        "below sum exactly to it) by the shape of the winning Bash tool_use's\n"
        "own recorded command:\n"
        "  - a sleep <number> in shell command position -> sleep-poll wait\n"
        "  - any other recorded command -> other Bash wait\n"
        "  - no command recorded (a Bash block with no input) -> no command recorded\n"
        "Only the gap-closing call's own winning command is classified, so a\n"
        "repeated 'sleep N; check' loop is classified once per gap it closed,\n"
        "not once per sleep. The match is textual, with no shell parsing.\n"
        "A quoted or heredoc-embedded sleep counts as a match, over-counting\n"
        "the row below for text that only mentions sleep without waiting on\n"
        "it. sleep $VAR (no literal leading digit) does not match,\n"
        "under-counting the row below by missing a real sleep-poll wait. A\n"
        "high share there points at no lever: see docs/cost-levers-considered.md's\n"
        "'From background-slow-bash-calls.md' section. [unverified]\n"
    )
    print(f"{'Shape':<32} {'Rebuilds':>9} {'Excess $':>12} {'5m-1h $':>12} {'Median cov.':>12}")
    for shape in _OWN_BASH_WAIT_SHAPES:
        shape_shares = bash_shape_shares[shape]
        shape_median_cov = render._pct_of(statistics.median(shape_shares), 1.0) if shape_shares else "n/a"
        print(
            f"{shape:<32} {bash_shape_rebuilds[shape]:>9,}"
            f" {bash_shape_excess[shape]:>12,.2f} {bash_shape_band_excess[shape]:>12,.2f}"
            f" {shape_median_cov:>12}"
        )

    print(
        "\n## Cache-write tier switch delta (5m -> 1h), threshold-independent\n\n"
        "W5m/X below accumulate over every in-scope call regardless of the\n"
        "--threshold value above -- this is a different denominator than the\n"
        "tail-only cause breakdown, and must not be divided into those figures.\n"
        "X excludes idle >1h and pure-1h-tier writes: a 1-hour cache is also cold\n"
        "past 3600s, so those rebuilds happen under either tier. Net$ is\n"
        "savings-positive: what a 5m-to-1h cacheTtl switch would save (or cost,\n"
        "if negative) against this origin's own traffic. The main row reads\n"
        "zero only when every root's main traffic sits on the vendor's own\n"
        "default tier: one hour under a Claude subscription within plan usage,\n"
        "five minutes otherwise. promptCacheTtl is unset for main (see\n"
        "docs/design-decisions/main-bucket-prompt-cache-ttl-stays-unset.md). A\n"
        "corpus mixing billing regimes, or forcing a tier via an env var, will\n"
        "not read zero here. The per-root\n"
        "--ttl-verdict gate below, not this pooled, threshold-independent\n"
        "row, is what actually decides a tier change.\n"
    )
    print(f"{'Origin':<10} {'W5m':>14} {'X':>14} {'Ratio':>8} {'Net$':>10}")
    for origin in _CACHE_REBUILD_ORIGINS:
        w5m = w5m_by_origin[origin]
        x_tokens = x_by_origin[origin]
        net_dollars = _negate_switch_delta_for_display(switch_delta_by_origin[origin])
        print(f"{origin:<10} {w5m:>14,} {x_tokens:>14,} {render._pct_of(x_tokens, w5m):>8} {net_dollars:>10,.2f}")

    if unpriced_switch_delta_turns:
        print(
            f"\n  ({unpriced_switch_delta_turns:,} 5m-tier write calls / {unpriced_switch_delta_tokens:,} tokens"
            " excluded from the switch-delta figures above -- model has no price-table entry)"
        )

    # Ex-post oracle bound: a group with w5m==0 has an undefined ratio and
    # is excluded from every figure below, though its (zero) delta still
    # contributes nothing to clearing_net. "Clears" uses the cents-rounded
    # sign, not the raw float, for the same reason
    # _negate_switch_delta_for_display rounds before printing -- a
    # dispatch built at the exact break-even boundary can leave a +-1e-16
    # residual that a raw `< 0` comparison would misclassify.
    eligible_groups = [g for g in per_group_dispersion if g["w5m"] > 0]
    clearing_groups = [g for g in eligible_groups if round(g["delta_dollars"], 2) < 0]
    total_subagent_group_w5m = sum(g["w5m"] for g in per_group_dispersion)
    clearing_w5m = sum(g["w5m"] for g in clearing_groups)
    # Sum the raw (un-rounded) per-group deltas first, then negate/round the
    # sum once -- rounding each group to cents before summing can drift a
    # many-group total by up to $0.005 per group against the raw sum, the
    # same reason the pooled origin-row Net$ figures above round only once.
    clearing_net = _negate_switch_delta_for_display(sum(g["delta_dollars"] for g in clearing_groups))
    # Priced subagent-origin W5m tokens that landed in no dispatch group at
    # all (see subagent_origin_w5m_in_groups' own comment above) -- a
    # non-zero figure means the oracle bound below is missing coverage in
    # the exclusion direction (undercounts what a selective policy could
    # find), not the inclusion direction.
    uncovered_subagent_w5m = w5m_by_origin["subagent"] - subagent_origin_w5m_in_groups

    print(
        "\n## Subagent per-dispatch dispersion (ex-post oracle bound)\n\n"
        "Dispatches selected by their own realized ratio, which a policy fixed\n"
        "before the dispatch cannot do -- a one-sided test for whether a\n"
        "selective lever is excluded, never a validation that one would work.\n"
    )
    print(f"Subagent dispatches (dispatches with any 5m-tier write): {len(eligible_groups):,}")
    print(f"Dispatches individually clearing their own break-even ratio: {len(clearing_groups):,}")
    print(
        "Their share of per-dispatch subagent W5m (not the pooled row above):"
        f" {render._pct_of(clearing_w5m, total_subagent_group_w5m)}"
    )
    print(f"Net $ restricted to clearing dispatches: {clearing_net:,.2f}")
    print(
        f"\n  ({uncovered_subagent_w5m:,} of {w5m_by_origin['subagent']:,} pooled subagent W5m tokens landed in no"
        " dispatch group above -- an unpriced-model call, or an inline sidechain record inside the main"
        " transcript file, neither of which belongs to any subagent-file group; 0 here means the oracle bound"
        " above has exact W5m coverage, not merely assumed)"
    )

    if ttl_verdict:
        print(
            "\n## TTL-verdict per-root analysis (--ttl-verdict) [unverified]\n\n"
            "Per-root break-even verdict for each bucket's own live TTL tier -- see"
            " .claude/plans/cache-ttl-tuning-analysis.md's Approach section for the derivation, the ship rule,"
            " and every caveat this print omits, and .claude/plans/cache-ttl-verdict-gate-fix.md's Approach"
            " section for the dominant-tier-share eligibility test below. A root is consistent by whichever"
            " tier holds at least a"
            f" {_CACHE_REBUILD_TTL_DOMINANT_TIER_SHARE_MIN:.0%} share of its own W5m + W1h; a root below that"
            " share (near-tie) or with neither tier nonzero (no data) is excluded from this bucket's verdict"
            " entirely, never counted toward either direction. Clears"
            " requires the margin to hold at both the"
            f" {_CACHE_REBUILD_IDLE_5M_SECONDS}s and {_CACHE_REBUILD_TTL_SENSITIVITY_BOUNDARY_SECONDS}s boundary,"
            " and, for every 1h-tier root, the raw-token tiebreaker to agree with the dollar accounting's own"
            " sign. [unverified]\n"
        )
        all_root_ordinals: tuple[int, ...] = tuple(sorted(set(redact_ordinals.values())))
        for ttl_origin in _CACHE_REBUILD_ORIGINS:
            consistent_5m_roots = 0
            consistent_1h_roots = 0
            excluded_roots = 0
            root_inputs: list[dict[str, object]] = []
            print(f"\n### {ttl_origin}\n")
            print(
                f"{'Root':<12} {'Tier':>6} {'W5m/W1h':>14} {'X/Z':>14} {'Net$':>10}"
                f" {'Favors':>8} {'Clears':>8} {'Share':>8}"
            )
            for root_ordinal in all_root_ordinals:
                root_key = (ttl_origin, root_ordinal)
                # --no-redact is refused once more than one root is in
                # scope, so scan_roots[0] is the only root this branch can
                # reach when not redact.
                root_label = f"account-{root_ordinal}" if redact else str(scan_roots[0].parent)
                root_w5m = w5m_by_origin_root.get(root_key, 0)
                root_w1h = w1h_by_origin_root.get(root_key, 0)
                if root_w5m == 0 and root_w1h == 0:
                    # No data in either tier excludes this root from the
                    # bucket's verdict. The row still prints so the reason
                    # is visible rather than only folded into the lumped
                    # count.
                    excluded_roots += 1
                    print(
                        f"{root_label:<12} {_TTL_TIER_NONE:>6} {0:>14,} {0:>14,} {_TTL_ROW_NA:>10}"
                        f" {_TTL_ROW_NOT_APPLICABLE:>8} {_TTL_EXCLUDE_NO_DATA:>8} {_TTL_ROW_NA:>8}"
                    )
                    continue
                # A mixed root still gets its own row, naming the dominant
                # tier's own accumulators (eligibility rule: see
                # _CACHE_REBUILD_TTL_DOMINANT_TIER_SHARE_MIN in cache_rebuild_rules.py).
                is_mixed = root_w5m > 0 and root_w1h > 0
                share = _cache_rebuild_dominant_tier_share(root_w5m, root_w1h)
                if root_w5m >= root_w1h:
                    tier = _CACHE_REBUILD_TIER_5M
                    net_primary = _negate_switch_delta_for_display(
                        switch_delta_5m_to_1h_by_origin_root.get(root_key, 0.0)
                    )
                    net_sensitivity = _negate_switch_delta_for_display(
                        switch_delta_5m_to_1h_at_60_by_origin_root.get(root_key, 0.0)
                    )
                    volume = w5m_dollars_by_origin_root.get(root_key, 0.0)
                    root_input = _cache_rebuild_root_verdict_input(
                        net_primary=net_primary, net_sensitivity=net_sensitivity, volume=volume,
                        positive_favors=_CACHE_REBUILD_TIER_1H, negative_favors=_CACHE_REBUILD_TIER_5M,
                        apply_tiebreaker=False,
                    )
                    row_prefix = (
                        f"{root_label:<12} {_CACHE_REBUILD_TIER_5M:>6} {root_w5m:>14,}"
                        f" {x_by_origin_root.get(root_key, 0):>14,} {render._fmt_usd(net_primary):>10}"
                        f" {root_input['favors']:>8}"
                    )
                else:
                    tier = _CACHE_REBUILD_TIER_1H
                    net_primary = _negate_switch_delta_for_display(
                        switch_delta_1h_to_5m_by_origin_root.get(root_key, 0.0)
                    )
                    net_sensitivity = _negate_switch_delta_for_display(
                        switch_delta_1h_to_5m_at_60_by_origin_root.get(root_key, 0.0)
                    )
                    volume = w1h_dollars_by_origin_root.get(root_key, 0.0)
                    root_z = z_by_origin_root.get(root_key, 0)
                    root_input = _cache_rebuild_root_verdict_input(
                        net_primary=net_primary, net_sensitivity=net_sensitivity, volume=volume,
                        positive_favors=_CACHE_REBUILD_TIER_5M, negative_favors=_CACHE_REBUILD_TIER_1H,
                        apply_tiebreaker=True,
                        tiebreaker_favors_5m=_cache_rebuild_token_tiebreaker_favors_5m(root_z, root_w1h),
                    )
                    row_prefix = (
                        f"{root_label:<12} {_CACHE_REBUILD_TIER_1H:>6} {root_w1h:>14,} {root_z:>14,}"
                        f" {render._fmt_usd(net_primary):>10} {root_input['favors']:>8}"
                    )
                if not _cache_rebuild_root_is_dominant(share):
                    excluded_roots += 1
                    print(f"{row_prefix} {_TTL_EXCLUDE_NEAR_TIE:>8} {share:>8.3f}")
                else:
                    if tier == _CACHE_REBUILD_TIER_5M:
                        consistent_5m_roots += 1
                    else:
                        consistent_1h_roots += 1
                    root_inputs.append(root_input)
                    print(f"{row_prefix} {str(root_input['clears']):>8} {share:>8.3f}")
                if is_mixed:
                    # Two-slice cross-check, informative only. The unanimity
                    # unit is the root, not a root-slice.
                    # net_5m_slice/net_1h_slice recompute the same
                    # switch_delta lookup-and-negate expression as the
                    # tier branches above. Keep both call sites in sync
                    # if that expression changes.
                    net_5m_slice = _negate_switch_delta_for_display(
                        switch_delta_5m_to_1h_by_origin_root.get(root_key, 0.0)
                    )
                    net_1h_slice = _negate_switch_delta_for_display(
                        switch_delta_1h_to_5m_by_origin_root.get(root_key, 0.0)
                    )
                    favors_5m_slice, favors_1h_slice, agreement = _cache_rebuild_tier_split_agreement(
                        net_5m_slice, net_1h_slice
                    )
                    print(
                        f"tier-split {root_label}: 5m-slice favors {favors_5m_slice}, "
                        f"1h-slice favors {favors_1h_slice} ({agreement})"
                    )
            verdict = _cache_rebuild_ttl_verdict(root_inputs)
            print(
                f"\n{ttl_origin}: consistent 5m roots={consistent_5m_roots}"
                f"  consistent 1h roots={consistent_1h_roots}"
                f"  excluded (near-tie or no data) roots={excluded_roots}  verdict={verdict}"
            )

        if unpriced_ttl_verdict_turns:
            print(
                f"\n  ({unpriced_ttl_verdict_turns:,} calls / {unpriced_ttl_verdict_tokens:,} tokens excluded from"
                " every Net$ figure and its own margin volume above -- model has no price-table entry)"
            )

        cache_miss_reason_total = cache_miss_reason_agree + cache_miss_reason_discrepancy
        if cache_miss_reason_discrepancy:
            print(
                f"\n  (cache-miss-reason cross-tab: {cache_miss_reason_discrepancy:,} of"
                f" {cache_miss_reason_total:,} idle-5m-1h-classified calls carry a vendor cache_miss_reason of"
                f" {_CACHE_MISS_REASON_MODEL_CHANGED!r} -- a discrepancy between the gap-derived cause and Claude"
                " Code's own miss signal. Every W5m/X/W1h/Z figure above still reflects the gap-derived"
                " classification, never this signal.)"
            )
