"""Rate tables, per-turn pricing, token counts, context windows, and
requestId-run deduplication -- no dependency on any cmd_* subcommand, scope
resolution, or redaction.
"""
from __future__ import annotations

import sys
from collections.abc import Sequence
from datetime import date, timedelta

from transcript_analysis.corpus import SUBAGENT_SUBDIR

_TOKEN_CLASSES: tuple[str, ...] = ("cache_read", "cache_write_5m", "cache_write_1h", "output", "input")

_PRICING_SOURCE_URL = "https://platform.claude.com/docs/en/about-claude/pricing"
_PRICING_FETCH_DATE = date(2026, 8, 2)

# Multipliers vs. a model's base input rate, per the pricing page's stated ratios.
_OUTPUT_RATE_MULTIPLIER = 5
_CACHE_WRITE_5M_MULTIPLIER = 1.25
_CACHE_WRITE_1H_MULTIPLIER = 2
_CACHE_READ_MULTIPLIER = 0.1

# Multipliers applied to every dollar class when usage.speed/usage.inference_geo
# report that outcome, per platform.claude.com/docs/en/build-with-claude/fast-mode
# and .../about-claude/pricing's data-residency section.
_FAST_MODE_RATE_MULTIPLIER = 2
_INFERENCE_GEO_US_RATE_MULTIPLIER = 1.1

_DEFAULT_REVERIFY_BY = _PRICING_FETCH_DATE + timedelta(days=90)

# Base input $/MTok per model ID, keyed on the exact string Claude Code writes
# to message.model. Source: _PRICING_SOURCE_URL, fetched _PRICING_FETCH_DATE.
# Output/cache-write/cache-read rates are derived from this one base rate per
# model by _model_rates, so each model needs only its base rate kept current.
_MODEL_BASE_INPUT_RATES: dict[str, float] = {
    "claude-opus-5": 5.00,
    "claude-opus-4-8": 5.00,
    "claude-sonnet-5": 2.00,
    "claude-sonnet-4-6": 3.00,
    "claude-haiku-4-5-20251001": 1.00,
    # Exact-match only, unlike _context_window_for_model's prefix match on
    # this same string -- a dated-snapshot variant like
    # "claude-sonnet-4-5-20260115" still 200k-buckets correctly but prices as
    # unpriced here.
    "claude-sonnet-4-5": 3.00,
}

# Re-verify-by date per model ID: fetch-date+90d for every model, since none
# has a vendor-stated rate-change date today.
_MODEL_RATE_EXPIRES: dict[str, date] = dict.fromkeys(_MODEL_BASE_INPUT_RATES, _DEFAULT_REVERIFY_BY)

_CONTEXT_BUCKET_THRESHOLD = 200_000  # inclusive edge of the "≥200k" finding
_CONTEXT_BUCKET_UNDER = "<200k"
_CONTEXT_BUCKET_OVER = ">=200k"

# Context window in tokens per model ID, mirroring
# nudge-handoff-near-context-cap.sh's own CONTEXT_WINDOW case statement
# exactly (same prefix list, same trailing-dash dated-snapshot match), so
# _CONTEXT_DISTRIBUTION_THRESHOLD_PCTS resolves to the window the hook
# multiplies by for the same model ID. A fixed percentage of that window
# still resolves to a different absolute token count on a 200k model than
# on a 1M one — see _CONTEXT_DISTRIBUTION_THRESHOLD_ABS for the sibling
# table expressed directly in absolute tokens instead. Source:
# https://platform.claude.com/docs/en/about-claude/models/overview, fetched
# 2026-08-03; re-verify by 2026-11-03. Verified 200k: Haiku 4.5, Sonnet 4.5,
# Opus 4.5, Opus 4.1. Verified 1M: Fable 5, Mythos 5, Opus 5, Opus
# 4.8/4.7/4.6, Sonnet 5, Sonnet 4.6. An unlisted ID takes the 1M default.
_200K_CONTEXT_MODEL_PREFIXES: tuple[str, ...] = (
    "claude-haiku-4-5",
    "claude-sonnet-4-5",
    "claude-opus-4-5",
    "claude-opus-4-1",
)
_200K_CONTEXT_WINDOW = 200_000
_DEFAULT_CONTEXT_WINDOW = 1_000_000

# Candidate threshold percentages of a model's context window, for
# context-distribution's crossing-count/session-share/dollar-share table.
_CONTEXT_DISTRIBUTION_THRESHOLD_PCTS: tuple[int, ...] = (30, 40, 50, 60)

# Candidate absolute-token thresholds, in the hook's own ESTIMATE unit (input
# + cache_read + cache_creation + output — see _session_peak_context), for
# context-distribution's own crossing-count/session-share/dollar-share table.
# A candidate-threshold sweep like _CONTEXT_DISTRIBUTION_THRESHOLD_PCTS, but
# fixed rather than scaled per model's window. Not the same thing as
# _CONTEXT_BUCKET_THRESHOLD above, which is a fixed *reporting* bucket edge
# consumed by cost/cost-trend, not a candidate-threshold sweep. Spans the
# 200k-model effective floor (80_000, 40% of 200k) through the 1M-model's
# uncapped 40%-of-window value (400_000) and beyond, into the range where
# 1M-model sessions have actually been observed firing. 360_000 is included
# because it is the live 1M-model effective threshold today
# (nudge-handoff-near-context-cap.sh's HANDOFF_NUDGE_ABS_CAP default) — a
# re-run of this report must be able to show the value the hook is actually
# configured to fire at, not just candidates for a future change.
_CONTEXT_DISTRIBUTION_THRESHOLD_ABS: tuple[int, ...] = (
    80_000, 135_000, 180_000, 250_000, 360_000, 400_000, 600_000, 800_000,
)


def _context_window_for_model(model: str) -> int:
    """Context window in tokens for one model ID.

    A prefix requires an exact match or a trailing "-" (dated-snapshot
    suffix), not a bare trailing wildcard, so a longer numeral
    (claude-opus-4-10) can't collide with a shorter one (claude-opus-4-1) by
    string prefix alone — the same collision guard as the bash hook this
    mirrors.
    """
    for prefix in _200K_CONTEXT_MODEL_PREFIXES:
        if model == prefix or model.startswith(prefix + "-"):
            return _200K_CONTEXT_WINDOW
    return _DEFAULT_CONTEXT_WINDOW


def _model_rates(model: str) -> dict[str, float] | None:
    """Return per-MTok dollar rates for one model ID, or None if unpriced."""
    base = _MODEL_BASE_INPUT_RATES.get(model)
    if base is None:
        return None
    return {
        "input": base,
        "output": base * _OUTPUT_RATE_MULTIPLIER,
        "cache_write_5m": base * _CACHE_WRITE_5M_MULTIPLIER,
        "cache_write_1h": base * _CACHE_WRITE_1H_MULTIPLIER,
        "cache_read": base * _CACHE_READ_MULTIPLIER,
    }


def _cache_write_split(usage: dict) -> tuple[int, int]:
    """Return (ephemeral_1h_tokens, ephemeral_5m_tokens) for one usage record.

    Prices the nested cache_creation.{ephemeral_1h,ephemeral_5m}_input_tokens
    block when present. Falls back to the flat cache_creation_input_tokens
    field as 5m-only when the nested block is absent — never counts both,
    since the nested block's own two fields sum exactly to the flat field on
    every real record that carries one.
    """
    nested = usage.get("cache_creation")
    if nested is not None:
        return int(nested.get("ephemeral_1h_input_tokens", 0)), int(nested.get("ephemeral_5m_input_tokens", 0))
    return 0, int(usage.get("cache_creation_input_tokens", 0))


def _context_at_turn(usage: dict) -> int:
    """Total input-side tokens resident in the context at one assistant turn.

    input_tokens + cache_read_input_tokens + ephemeral_1h + ephemeral_5m. This is
    an absolute per-turn snapshot, not a turn-to-turn delta -- read-scope
    differences consecutive snapshots within one context sequence to derive
    prompt-token growth, which is why the sum lives here rather than inline in
    _price_turn's pricing path.
    """
    eph_1h, eph_5m = _cache_write_split(usage)
    return int(usage.get("input_tokens", 0)) + int(usage.get("cache_read_input_tokens", 0)) + eph_1h + eph_5m


def _context_bucket(context_at_turn: int) -> str:
    return _CONTEXT_BUCKET_OVER if context_at_turn >= _CONTEXT_BUCKET_THRESHOLD else _CONTEXT_BUCKET_UNDER


def dedup_turns_by_request_id(records: Sequence[dict]) -> list[dict]:
    """Collapse each run of same-requestId assistant records into one turn each.

    Claude Code writes one JSONL record per assistant content block (thinking
    / text / tool_use); every record from one API call shares one requestId,
    but a run's own records are not always contiguous -- the harness
    sometimes interleaves a tool_result record between two same-requestId
    assistant records while it executes one multi-tool_use response's tool
    calls one at a time. A contiguous group always merges; a non-contiguous
    group merges only when _non_contiguous_run_usage_matches agrees. A
    merging group's turn is built by _merge_assistant_run and emitted at the
    group's first member's original position; later members are dropped. A
    missing/null/empty requestId never merges with another missing one,
    since real transcripts carry requestId-less synthetic error records that
    must not collapse into each other. Non-assistant records always pass
    through unchanged in their own position. Grouping is on requestId
    equality alone (not also isSidechain/type). Callers must never
    concatenate records from different sessions before calling this:
    requestId is unique per API call, so concatenating one session's main
    transcript with its own subagent transcripts is safe, but mixing in
    another session's records is not -- a non-contiguous group can span the
    entire input, so safety now rests on the usage-corroboration bar
    (_non_contiguous_run_usage_matches) rather than on a contiguity-only
    bound. A merged turn's --since timestamp is its first block's, so a
    non-contiguous group's staleness window can span the entire input too.
    """
    groups: dict[str, list[int]] = {}
    for idx, rec in enumerate(records):
        if rec.get("type") != "assistant":
            continue
        request_id = rec.get("requestId")
        if not request_id:
            continue
        groups.setdefault(request_id, []).append(idx)

    merge_start_idx: dict[int, str] = {}
    skip_idx: set[int] = set()
    for request_id, idxs in groups.items():
        if len(idxs) == 1:
            continue
        is_contiguous = idxs == list(range(idxs[0], idxs[0] + len(idxs)))
        if not is_contiguous:
            run = [records[i] for i in idxs]
            if not _non_contiguous_run_usage_matches(run):
                _log_non_contiguous_merge_decision(request_id, len(idxs), merged=False)
                continue
            _log_non_contiguous_merge_decision(request_id, len(idxs), merged=True)
        merge_start_idx[idxs[0]] = request_id
        skip_idx.update(idxs[1:])

    turns: list[dict] = []
    for idx, rec in enumerate(records):
        if idx in skip_idx:
            continue
        request_id = merge_start_idx.get(idx)
        if request_id is None:
            turns.append(rec)
        else:
            turns.append(_merge_assistant_run([records[i] for i in groups[request_id]]))

    return turns


def _merge_assistant_run(run: list[dict]) -> dict:
    """Merge one requestId run of assistant records into a single synthetic record.

    message.content is the concatenation of every record's own content blocks,
    in original order. Every other field (uuid, parentUuid, timestamp) is
    taken from the run's first record -- the documented --since semantics
    depend on the first block's timestamp. message.usage is taken from the
    run's LAST record instead: input_tokens and the cache_* classes are
    identical across a run, but output_tokens ascends within the run and
    only reaches its billed value on the last record (measured across 150
    transcripts / 15,653 multi-record runs -- see _warn_if_run_usage_drift).
    """
    _warn_if_run_usage_drift(run)
    merged = dict(run[0])
    merged_message = dict(merged.get("message") or {})
    merged_content: list = []
    for rec in run:
        merged_content.extend((rec.get("message") or {}).get("content") or [])
    merged_message["content"] = merged_content
    merged_message["usage"] = (run[-1].get("message") or {}).get("usage")
    merged["message"] = merged_message
    return merged


def _cache_miss_reason(message: dict) -> str | None:
    """message.diagnostics.cache_miss_reason.type for one assistant turn's own
    (possibly merged) record, or None when absent/malformed.

    _merge_assistant_run takes every non-content, non-usage field from a
    requestId run's first record unchanged, so a merged turn's "diagnostics"
    already reflects the run's own opening call with no extra collapsing
    logic needed here. cache_miss_reason is always a dict shaped
    {"type": <str>, "cache_missed_input_tokens": <int>} across a 3,926-record
    survey of real transcripts, with "cache_missed_input_tokens" absent only
    for reason types "previous_message_not_found" and "unavailable" -- no
    bare-string form was observed, so this does not fall back to one. Returns
    None when "diagnostics" is missing or not a dict, or "cache_miss_reason"
    is missing or not a dict, or its "type" is missing or not a string.
    """
    diagnostics = message.get("diagnostics")
    if not isinstance(diagnostics, dict):
        return None
    reason = diagnostics.get("cache_miss_reason")
    if not isinstance(reason, dict):
        return None
    reason_type = reason.get("type")
    return reason_type if isinstance(reason_type, str) else None


# Names the usage keys _warn_if_run_usage_drift treats as required-invariant
# across a requestId run: measured identical in 15,653/15,653 multi-record
# runs (see dedup_turns_by_request_id's docstring). output_tokens is
# deliberately excluded -- it ascends within a run by design, completing on
# the last record, so divergence there is the documented norm, not drift.
# cache_creation's nested ephemeral_1h/5m_input_tokens need no separate entry:
# both measured invariant across the same 15,653 runs, and every run's first
# and last record alike carried a cache_creation block.
_USAGE_DRIFT_INVARIANT_KEYS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")

# Usage keys a non-contiguous same-requestId run must agree on before
# dedup_turns_by_request_id merges it: _USAGE_DRIFT_INVARIANT_KEYS plus
# output_tokens. A genuinely once-billed non-contiguous run carries
# byte-identical usage (including output_tokens) across every record --
# unlike a contiguous run, where output_tokens ascends (12/12 observed
# non-contiguous pairs).
_NON_CONTIGUOUS_MERGE_REQUIRED_MATCH_KEYS = _USAGE_DRIFT_INVARIANT_KEYS + ("output_tokens",)

# Rate-limits _warn_if_run_usage_drift to one line per process, mirroring
# _warn_if_subagent_format_drift's pattern -- a whole-corpus scan should
# surface one signal that the format changed, not one per occurrence.
_usage_drift_warned = False


def _warn_if_run_usage_drift(run: list[dict]) -> None:
    """Emit one stderr warning per process when a requestId run's records
    disagree on an input/cache usage class that's measured invariant across
    a run (see _USAGE_DRIFT_INVARIANT_KEYS).

    _merge_assistant_run relies on these classes being identical across every
    record of one API call to price a merged turn correctly regardless of
    which record's value it reads; this is the runtime canary for that
    assumption. A warning rather than a raise, so one malformed session
    doesn't abort a whole-corpus scan.
    """
    global _usage_drift_warned
    if _usage_drift_warned:
        return
    first_usage = (run[0].get("message") or {}).get("usage") or {}
    for rec in run[1:]:
        rec_usage = (rec.get("message") or {}).get("usage") or {}
        if any(rec_usage.get(key) != first_usage.get(key) for key in _USAGE_DRIFT_INVARIANT_KEYS):
            print(
                f"WARNING: requestId {run[0].get('requestId')!r} has non-identical "
                "input/cache usage across its own records — _merge_assistant_run's "
                "invariant-usage assumption may no longer hold (further occurrences "
                "this run of the CLI are suppressed). The Claude Code transcript "
                "format may have changed.",
                file=sys.stderr,
            )
            _usage_drift_warned = True
            return


def _non_contiguous_run_usage_matches(run: list[dict]) -> bool:
    """Whether every record in a non-contiguous same-requestId run agrees with
    the first record on all of _NON_CONTIGUOUS_MERGE_REQUIRED_MATCH_KEYS.

    dedup_turns_by_request_id gates a non-contiguous merge on this: bare
    requestId equality alone would also merge two distinct API calls that
    happen to share a requestId (e.g. a hook-denial retry), so this compares
    every member (not just adjacent pairs) against the run's first record.
    A record with no usage dict at all has no evidence to corroborate on, so
    it fails the match rather than vacuously agreeing with another empty one.
    """
    first_usage = (run[0].get("message") or {}).get("usage") or {}
    if not first_usage:
        return False
    for rec in run[1:]:
        rec_usage = (rec.get("message") or {}).get("usage") or {}
        if not rec_usage or any(
            rec_usage.get(key) != first_usage.get(key) for key in _NON_CONTIGUOUS_MERGE_REQUIRED_MATCH_KEYS
        ):
            return False
    return True


# Rate-limits _log_non_contiguous_merge_decision to one NOTICE per process
# per decision kind ("merged" vs. "rejected"), mirroring _usage_drift_warned
# generalized to two independently-rate-limited kinds.
_non_contiguous_merge_notices_logged: set[str] = set()


def _log_non_contiguous_merge_decision(request_id: str, record_count: int, *, merged: bool) -> None:
    """Emit one stderr NOTICE per process per decision kind when
    dedup_turns_by_request_id resolves a non-contiguous same-requestId run,
    so a later audit can tell whether the usage-corroboration bar
    (_NON_CONTIGUOUS_MERGE_REQUIRED_MATCH_KEYS) is miscalibrated without
    re-deriving the empirical basis for its match keys from scratch.
    """
    kind = "merged" if merged else "rejected"
    if kind in _non_contiguous_merge_notices_logged:
        return
    outcome = (
        "merged into one turn (usage matched on every record)"
        if merged
        else "left as separate turns (usage did not match on every record)"
    )
    print(
        f"NOTICE: non-contiguous requestId run {kind} -- requestId {request_id!r} has "
        f"{record_count} non-contiguous assistant records, {outcome} (further "
        f"{kind} occurrences this run of the CLI are suppressed).",
        file=sys.stderr,
    )
    _non_contiguous_merge_notices_logged.add(kind)


# Subagent spawn tool_use names, corpus-wide across every command that
# cross-checks spawns against isSidechain turns read.
_SPAWN_TOOL_NAMES = ("Agent", "Task")


def _count_subagent_spawns(records: list[dict]) -> int:
    """Count main-thread subagent spawn tool_uses (Agent/Task) across all records.

    Used corpus-wide (ignoring branch filter) as one side of the format-drift
    cross-check: spawns > 0 with zero isSidechain turns read is the drift signature.
    Note: isSidechain records (from subagent files) are excluded by design — nested
    subagent spawns are not counted here.
    """
    count = 0
    for rec in records:
        if rec.get("type") != "assistant" or bool(rec.get("isSidechain")):
            continue
        for block in ((rec.get("message") or {}).get("content") or []):
            if (
                isinstance(block, dict)
                and block.get("type") == "tool_use"
                and block.get("name") in _SPAWN_TOOL_NAMES
            ):
                count += 1
    return count


def _warn_if_subagent_format_drift(total_spawns: int, total_sidechain_turns: int) -> None:
    """Emit a stderr warning when the drift signature is detected.

    Drift signature: spawns recorded in the main thread but zero isSidechain
    assistant turns read after include_subagents merge. Catches both failure
    modes — the subagents/ path relocating (files never read → 0 turns) and a
    field rename (files read but the filter matches 0).

    This is the runtime half of the two-layer guard:
      - Contract test (CI): pins what our code expects from fixtures.
      - This canary (runtime): validates expectation against live on-disk data.
    """
    if total_spawns > 0 and total_sidechain_turns == 0:
        print(
            "WARNING: subagent spawns detected in main thread but zero isSidechain "
            f"assistant turns were read from '{SUBAGENT_SUBDIR}/' subdirectories. "
            "The Claude Code transcript format may have drifted — check that "
            f"subagent files still live under <session>/{SUBAGENT_SUBDIR}/*.jsonl "
            "and that records still carry 'isSidechain': true.",
            file=sys.stderr,
        )


def _price_turn(model: str, usage: dict) -> tuple[dict[str, float] | None, int, int]:
    """Price one assistant turn's usage against _MODEL_BASE_INPUT_RATES.

    Returns (dollars_by_class, context_at_turn, unpriced_tokens):
    - dollars_by_class holds one raw (unrounded) dollar amount per
      _TOKEN_CLASSES entry when the model has a price-table entry, else
      None — callers must check for None rather than treating a zero total
      as "priced at $0".
    - context_at_turn is input_tokens + cache_read_input_tokens + ephemeral_1h
      + ephemeral_5m tokens for this turn, computed regardless of pricing.
    - unpriced_tokens is the turn's total token count (input + output +
      cache_read + ephemeral_1h + ephemeral_5m) when the model is unpriced,
      else 0.
    """
    input_t = int(usage.get("input_tokens", 0))
    output_t = int(usage.get("output_tokens", 0))
    cache_read_t = int(usage.get("cache_read_input_tokens", 0))
    eph_1h, eph_5m = _cache_write_split(usage)
    # _cache_write_split runs twice for this turn (here and inside
    # _context_at_turn). It is pure, and the per-class splits below need the two
    # halves separately, so sharing the sum is the only deduplication available.
    context_at_turn = _context_at_turn(usage)

    rates = _model_rates(model)
    if rates is None:
        return None, context_at_turn, input_t + output_t + cache_read_t + eph_1h + eph_5m

    dollars = {
        "input": input_t / 1_000_000 * rates["input"],
        "output": output_t / 1_000_000 * rates["output"],
        "cache_read": cache_read_t / 1_000_000 * rates["cache_read"],
        "cache_write_1h": eph_1h / 1_000_000 * rates["cache_write_1h"],
        "cache_write_5m": eph_5m / 1_000_000 * rates["cache_write_5m"],
    }
    # usage.speed/usage.inference_geo report the API's settled outcome, not an
    # echo of the request, so no per-model eligibility list is needed here.
    # Neither field is in _USAGE_DRIFT_INVARIANT_KEYS, so a non-run-invariant
    # value on either gets no runtime drift canary today -- a known, accepted gap.
    if usage.get("speed") == "fast":
        dollars = {cls: val * _FAST_MODE_RATE_MULTIPLIER for cls, val in dollars.items()}
    if usage.get("inference_geo") == "us":
        dollars = {cls: val * _INFERENCE_GEO_US_RATE_MULTIPLIER for cls, val in dollars.items()}
    return dollars, context_at_turn, 0


def _token_counts(usage: dict) -> dict[str, int]:
    """Per-class raw token counts for one turn's usage, keyed like _TOKEN_CLASSES.

    Mirrors _price_turn's own class breakdown (same fields, same
    _cache_write_split reuse) so cost's Tokens column sums the same fields
    its $ column already prices — callers apply it at the same point
    _price_turn's dollar accumulation does, after a turn is confirmed priced,
    so an unpriced model's tokens are excluded here too.
    """
    eph_1h, eph_5m = _cache_write_split(usage)
    return {
        "input": int(usage.get("input_tokens", 0)),
        "output": int(usage.get("output_tokens", 0)),
        "cache_read": int(usage.get("cache_read_input_tokens", 0)),
        "cache_write_1h": eph_1h,
        "cache_write_5m": eph_5m,
    }


def _session_peak_context(main_thread_turns: Sequence[tuple[int, int, int]]) -> tuple[float, int]:
    """Track a session's peak context two ways over the same main-thread turns.

    Each element of main_thread_turns is one turn's
    (context_at_turn, output_tokens, context_window) — context_at_turn from
    _price_turn (input + cache_read + ephemeral_1h + ephemeral_5m).

    Returns (peak_pct, peak_abs_tokens):
    - peak_pct is the session's maximum context_at_turn / context_window.
    - peak_abs_tokens is the session's maximum context_at_turn + output_tokens
      — the hook's own four-field ESTIMATE unit.

    The two are tracked as independent per-turn maxima, never one derived
    from the other (peak_abs_tokens != peak_pct * window): on a session that
    mixes a 200k-window turn with a 1M-window turn, the turn with the
    highest percentage of its own window is not necessarily the turn with
    the highest absolute token count.
    """
    peak_pct = 0.0
    peak_abs_tokens = 0
    for context_at_turn, output_tokens, context_window in main_thread_turns:
        pct = context_at_turn / context_window
        if pct > peak_pct:
            peak_pct = pct
        abs_tokens = context_at_turn + output_tokens
        if abs_tokens > peak_abs_tokens:
            peak_abs_tokens = abs_tokens
    return peak_pct, peak_abs_tokens
