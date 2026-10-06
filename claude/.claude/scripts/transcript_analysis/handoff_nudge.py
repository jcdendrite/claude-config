"""The handoff-nudge family's shared core, with no cmd_* of its own: the
hook's fire-threshold mirror, the turn-index ramp curve, the single
per-session dedup-and-price pass, and the bounded .handoff-nudge.log reader
and parser that rearm-backtest, spend-over-threshold, plan-boundary, and
handoff-signal-response share.

Imports corpus, pricing, and scope by module (attribute access, not by name)
-- see scope.py's own top-of-file comment for why.
"""
from __future__ import annotations

import contextlib
from collections import defaultdict
from collections.abc import Iterable, Sequence
from pathlib import Path

from transcript_analysis import corpus, pricing, scope

_NUDGE_LOG_MAX_READ = 2 * 1024 * 1024  # 2 MB


def _read_bounded_log_lines(log_path: Path) -> list[str]:
    """Read an append-only log file's lines, tail-truncated to the last
    _NUDGE_LOG_MAX_READ bytes so an unbounded log can't be pulled fully into
    memory. Returns [] when the file is absent or unreadable -- shared by
    _print_nudge_log_diagnostic and _parse_nudge_log_entries so both read
    ~/.claude/.handoff-nudge.log the same bounded way."""
    try:
        if not log_path.exists():
            return []
        if log_path.stat().st_size > _NUDGE_LOG_MAX_READ:
            raw = log_path.read_bytes()[-_NUDGE_LOG_MAX_READ:]
            return raw.decode(errors="ignore").splitlines()
        return log_path.read_text().splitlines()
    except OSError:
        return []


def _print_nudge_log_diagnostic() -> None:
    """Read ~/.claude/.handoff-nudge.log and report schema-drift count if
    present. Silently skips the diagnostic (never raises) when config_dir()
    can't resolve, since the primary report this footer follows has already
    printed and succeeded. Matches _read_bounded_log_lines' own
    absent/unreadable-file degrade above."""
    try:
        config_directory = scope.config_dir()
    except ValueError:
        return
    log_path = config_directory / ".handoff-nudge.log"
    lines = _read_bounded_log_lines(log_path)
    drift_count = sum(1 for ln in lines if ln.startswith("schema-drift"))
    if drift_count:
        print(f"\nDiagnostic: {drift_count} schema-drift line(s) in {log_path}")
        print("  Schema-drift means the usage block was found but all token fields were 0 or null.")
        print("  The field paths in nudge-handoff-near-context-cap.sh may need updating.")


# Mirrors nudge-handoff-near-context-cap.sh's own HANDOFF_NUDGE_ABS_CAP
# default (docs/handoff-nudge.md's "Why this cap" section). Duplicated
# rather than imported -- there is no mechanism to share a constant between
# a bash hook and a Python script, the same cross-language duplication
# _context_window_for_model's docstring already documents. Not a CLI flag:
# .claude/plans/token-cost-reduction.md's Phase 3 keeps this fixed, and a
# flag would invite a future run to quietly retune it through this tool.
_HANDOFF_NUDGE_ABS_CAP = 150_000

# Mirrors the hook's own `PCT_THRESHOLD=$(( CONTEXT_WINDOW * 40 / 100 ))` --
# the hook fires at the LESSER of 40% of the active model's context window
# and _HANDOFF_NUDGE_ABS_CAP, so a 200k-window model's real fire point
# (80,000) is well under the 1M-window arm's cap-governed 150,000. Neither
# this fraction nor _HANDOFF_NUDGE_ABS_CAP is backtested -- only re-arm
# spacing past whichever of the two governs a given session is.
_HANDOFF_NUDGE_PCT_THRESHOLD = 0.40

# PR #605's own turn-index bands (.claude/plans/handoff-boundary-decision-rule.md),
# reused here for comparability with that point-in-time measurement -- the
# dollar/context figures themselves are re-derived from the current corpus on
# every run, never hardcoded. Uses a cascading less-than lookup: a turn index
# is tested against each bound in order and takes the first label whose bound
# it's under, so an index PR #605's own table never explicitly labeled (10-19,
# between "5-10" and "20-40") falls through to "20-40" rather than going
# unbucketed.
_RAMP_CURVE_TURN_INDEX_BUCKETS: tuple[tuple[int, str], ...] = (
    (5, "0-5"),
    (10, "5-10"),
    (40, "20-40"),
    (80, "40-80"),
    (150, "80-150"),
    (300, "150-300"),
)
_RAMP_CURVE_TURN_INDEX_OVERFLOW_LABEL = "300+"
_RAMP_CURVE_BUCKET_LABELS: tuple[str, ...] = tuple(
    label for _, label in _RAMP_CURVE_TURN_INDEX_BUCKETS
) + (_RAMP_CURVE_TURN_INDEX_OVERFLOW_LABEL,)


# The three line shapes _parse_nudge_log_entries recognizes: "nudged" and
# "schema-drift" are written by nudge-handoff-near-context-cap.sh itself
# (docs/handoff-nudge.md's "Log location" table); "handoff" is appended by
# the handoff skill's own conversion-signal step
# (claude-skills/skills/handoff/SKILL.md, "After writing: record the
# conversion signal").
_NUDGE_LOG_LINE_KINDS = ("nudged", "schema-drift", "handoff")


def _ramp_curve_turn_index_bucket(turn_index: int) -> str:
    """Bucket a 0-indexed main-thread turn position (turns since a real or
    simulated fresh session start) into one of PR #605's seven turn-index
    bands, via the cascading less-than lookup described in the comment above
    _RAMP_CURVE_TURN_INDEX_BUCKETS."""
    for bound, label in _RAMP_CURVE_TURN_INDEX_BUCKETS:
        if turn_index < bound:
            return label
    return _RAMP_CURVE_TURN_INDEX_OVERFLOW_LABEL


def _hook_effective_fire_threshold(model: str) -> int:
    """The real hook's own fire threshold for one model: the lesser of 40% of
    that model's context window (_context_window_for_model, mirroring the
    bash hook's own CONTEXT_WINDOW case statement) and _HANDOFF_NUDGE_ABS_CAP.
    A 200k-window model's real threshold (80,000) is well under a 1M-window
    model's cap-governed one (150,000) -- using _HANDOFF_NUDGE_ABS_CAP alone
    for every session would overstate how early such sessions actually get
    nudged today."""
    pct_threshold = int(pricing._context_window_for_model(model) * _HANDOFF_NUDGE_PCT_THRESHOLD)
    return min(pct_threshold, _HANDOFF_NUDGE_ABS_CAP)


def _extract_rearm_session_turns(records: Sequence[dict]) -> dict:
    """Single dedup+price pass over one session's raw records, shared by
    _ramp_curve_from_corpus and _rearm_backtest_report so each session's
    records are decoded/deduped/priced exactly once per report run instead
    of twice.

    Returns a dict with:
    - "deduped": pricing.dedup_turns_by_request_id's output, for a caller
      building _hook_observable_boundaries from the same records.
    - "main_thread_turns": one (context_at_turn, output_tokens, actual_dollars)
      tuple per main-thread assistant turn carrying a usage block
      (actual_dollars is 0.0 when the turn's model is unpriced), in
      _simulate_rearm_spacing's own input shape.
    - "main_thread_priced": one bool per entry in main_thread_turns, parallel
      to it, True when that turn's model was priced -- _ramp_curve_from_corpus
      only buckets priced turns.
    - "main_thread_models": one model ID per entry in main_thread_turns,
      parallel to it -- plan-boundary's own ground-truth model-switch check
      needs each turn's model, not just its price-table membership.
    - "main_thread_record_positions": one "deduped" list index per entry in
      main_thread_turns, parallel to it -- lets a caller (plan-boundary) fetch
      a main-thread turn's own raw record (and its usage/diagnostics fields)
      by main_thread_turns index without a second scan of "deduped", and
      without this list's own filtering (usage-block-only, main-thread-only)
      desyncing from a plain enumerate() over "deduped".
    - "sidechain_dollars_total": summed actual dollars across this session's
      priced sidechain turns.
    - "unpriced_turns" / "unpriced_tokens": counts across both main-thread and
      sidechain turns whose model has no price-table entry.
    - "session_threshold": _hook_effective_fire_threshold for this session's
      first main-thread turn's model (None if the session has no main-thread
      turn with a usage block).
    """
    deduped = pricing.dedup_turns_by_request_id(records)
    main_thread_turns: list[tuple[int, int, float]] = []
    main_thread_priced: list[bool] = []
    main_thread_models: list[str] = []
    main_thread_record_positions: list[int] = []
    sidechain_dollars_total = 0.0
    unpriced_turns = 0
    unpriced_tokens = 0
    session_threshold: int | None = None

    for record_index, rec in enumerate(deduped):
        if rec.get("type") != "assistant":
            continue
        msg = rec.get("message") or {}
        usage = msg.get("usage")
        if not usage:
            continue
        model = msg.get("model", "")
        dollars_by_class, context_at_turn, turn_unpriced_tokens = pricing._price_turn(model, usage)
        output_tokens = int(usage.get("output_tokens", 0))
        if bool(rec.get("isSidechain")):
            if dollars_by_class is not None:
                sidechain_dollars_total += sum(dollars_by_class.values())
            else:
                unpriced_turns += 1
                unpriced_tokens += turn_unpriced_tokens
            continue
        if dollars_by_class is None:
            unpriced_turns += 1
            unpriced_tokens += turn_unpriced_tokens
            actual_dollars = 0.0
        else:
            actual_dollars = sum(dollars_by_class.values())
        if session_threshold is None:
            session_threshold = _hook_effective_fire_threshold(model)
        main_thread_turns.append((context_at_turn, output_tokens, actual_dollars))
        main_thread_priced.append(dollars_by_class is not None)
        main_thread_models.append(model)
        main_thread_record_positions.append(record_index)

    return {
        "deduped": deduped,
        "main_thread_turns": main_thread_turns,
        "main_thread_priced": main_thread_priced,
        "main_thread_models": main_thread_models,
        "main_thread_record_positions": main_thread_record_positions,
        "sidechain_dollars_total": sidechain_dollars_total,
        "unpriced_turns": unpriced_turns,
        "unpriced_tokens": unpriced_tokens,
        "session_threshold": session_threshold,
    }


def _ramp_curve_from_corpus(sessions: Iterable[dict]) -> tuple[dict[str, dict[str, float]], int]:
    """Re-derive PR #605's fresh-session rebuild ramp from the current corpus
    instead of citing that PR's own table: its source document
    (.claude/plans/handoff-boundary-decision-rule.md) calls the table
    "a point-in-time measurement, not a reproducible report," so this
    subcommand recomputes it every run against whatever corpus is in scope.

    `sessions` is _extract_rearm_session_turns' own output, one dict per
    session -- this function does no I/O or dedup/pricing of its own, only
    the bucket aggregation, so a caller extracts each session's turns
    exactly once and fans the result out to both this function and its own
    main_thread_turns/session_traces bookkeeping.

    Buckets main-thread turns only (no sidechain/subagent turns -- a
    subagent dispatch pays its own prefix from scratch and never represents
    a "turns since a fresh session start" position) by
    _ramp_curve_turn_index_bucket. Each bucket's "rate" is $/1k output
    tokens (dollars / (output_tokens/1000), the same normalize-by-work
    convention PR #605's own table used) and "mean_context" is the
    output-token-weighted mean context_at_turn for turns in that bucket --
    both needed by _simulate_rearm_spacing to price and to estimate the
    context depth of a counterfactually-repriced turn.

    A bucket with zero output tokens in the resolved corpus falls back to the
    corpus-wide rate/mean_context (also 0.0 when the whole corpus has zero
    output tokens) rather than a division-by-zero or NaN -- a corpus that
    doesn't happen to have a session long enough to populate the "300+"
    bucket must still return a usable, defined number for that bucket.

    Returns (curve, total_output_tokens): total_output_tokens is the whole
    resolved corpus's own priced output-token count, letting a caller detect
    the corpus-wide-zero case (every bucket's rate/mean_context silently 0.0,
    with nothing in curve itself distinguishing that from a genuinely cheap
    ramp) distinctly from a normal, populated curve.
    """
    bucket_dollars: dict[str, float] = defaultdict(float)
    bucket_output_tokens: dict[str, int] = defaultdict(int)
    bucket_context_weighted: dict[str, float] = defaultdict(float)
    total_dollars = 0.0
    total_output_tokens = 0
    total_context_weighted = 0.0

    for session in sessions:
        turns = session["main_thread_turns"]
        for turn_index, is_priced in enumerate(session["main_thread_priced"]):
            if not is_priced:
                continue
            context_at_turn, output_tokens, turn_dollars = turns[turn_index]
            label = _ramp_curve_turn_index_bucket(turn_index)
            bucket_dollars[label] += turn_dollars
            bucket_output_tokens[label] += output_tokens
            bucket_context_weighted[label] += context_at_turn * output_tokens
            total_dollars += turn_dollars
            total_output_tokens += output_tokens
            total_context_weighted += context_at_turn * output_tokens

    fallback_rate = (total_dollars / (total_output_tokens / 1000)) if total_output_tokens else 0.0
    fallback_context = (total_context_weighted / total_output_tokens) if total_output_tokens else 0.0

    curve: dict[str, dict[str, float]] = {}
    for label in _RAMP_CURVE_BUCKET_LABELS:
        out_tok = bucket_output_tokens.get(label, 0)
        if out_tok:
            curve[label] = {
                "rate": bucket_dollars[label] / (out_tok / 1000),
                "mean_context": bucket_context_weighted[label] / out_tok,
            }
        else:
            curve[label] = {"rate": fallback_rate, "mean_context": fallback_context}
    return curve, total_output_tokens


def _parse_nudge_log_entries(log_path: Path) -> list[dict]:
    """Parse ~/.claude/.handoff-nudge.log into one dict per recognized line
    (see _NUDGE_LOG_LINE_KINDS), reusing _read_bounded_log_lines' bounded
    2MB tail-read rather than a fresh read implementation. A line that
    doesn't start with a recognized kind, or whose fields don't parse (a
    missing required key, or a non-integer est/window), is silently skipped
    -- this is a best-effort append-only operational log, not a format this
    tool controls.

    Each returned dict carries "kind" plus that kind's own fields:
    - nudged: session, est (int), model, window (int), event
      - action: present only on a hard-block fire (action=block); absent on an advisory fire
      - ignored (int), skills: present on log lines written by hook versions that record per-fire telemetry; absent on older lines
    - schema-drift: session, event
    - handoff: session
    """
    entries: list[dict] = []
    for line in _read_bounded_log_lines(log_path):
        tokens = line.split()
        if not tokens or tokens[0] not in _NUDGE_LOG_LINE_KINDS:
            continue
        kind = tokens[0]
        fields: dict[str, str] = {}
        malformed = False
        for tok in tokens[1:]:
            if "=" not in tok:
                malformed = True
                break
            key, _, value = tok.partition("=")
            fields[key] = value
        if malformed:
            continue

        if kind == "nudged":
            if not {"session", "est", "model", "window", "event"} <= fields.keys():
                continue
            try:
                est = int(fields["est"])
                window = int(fields["window"])
            except ValueError:
                continue
            entry = {
                "kind": "nudged", "session": fields["session"], "est": est,
                "model": fields["model"], "window": window, "event": fields["event"],
            }
            if "action" in fields:
                entry["action"] = fields["action"]
            if "ignored" in fields:
                with contextlib.suppress(ValueError):
                    entry["ignored"] = int(fields["ignored"])
            if "skills" in fields:
                entry["skills"] = fields["skills"]
            entries.append(entry)
        elif kind == "schema-drift":
            if not {"session", "event"} <= fields.keys():
                continue
            entries.append({"kind": "schema-drift", "session": fields["session"], "event": fields["event"]})
        else:  # handoff
            if "session" not in fields:
                continue
            entries.append({"kind": "handoff", "session": fields["session"]})
    return entries


def _operator_response_lag_from_log(
    session_traces: dict[str, list[int]], log_entries: list[dict]
) -> tuple[list[int], int]:
    """Measure how far past each logged nudge's fire point sessions in scope
    actually kept running, for the compliance-realistic backtest model.

    session_traces maps a full session id (jsonl.stem, matching the hook's
    own SESSION_ID) to that session's ordered per-main-thread-turn abs-token
    values (context_at_turn + output_tokens -- the hook's own ESTIMATE unit).
    A `nudged` log line carries no timestamp (docs/handoff-nudge.md's "Log
    location" table enumerates its fields), so the join key is session_id
    plus a first-crossing rule: the fire turn is the
    trace's first value >= est, matching the real hook's own semantics -- it
    fires once, at the first crossing, never later. A nearest-value join
    would instead risk landing on a turn *after* a mid-session compaction
    (isCompactSummary) dip whose abs-token value happens to be closer to est
    than the true, earlier first-crossing turn, silently corrupting the
    measured lag with no error and no other signal.

    Returns (lags, excluded_count): lags is one non-negative token delta
    (peak abs-tokens reached at or after the identified fire turn, minus est)
    per successfully joined `nudged` line. A `nudged` line whose session_id
    has no entry in session_traces (a since-deleted transcript, or a session
    from an account/root outside the resolved scope), or whose trace never
    reaches est at all, is excluded and counted rather than silently dropped.
    A hard-block fire (action=block) is excluded too: its overshoot is
    forced by the block, not the voluntary operator-response lag this
    function measures.
    """
    lags: list[int] = []
    excluded = 0
    for entry in log_entries:
        if entry.get("kind") != "nudged":
            continue
        if entry.get("action") == "block":
            excluded += 1
            continue
        trace = session_traces.get(entry["session"])
        if not trace:
            excluded += 1
            continue
        est = entry["est"]
        fire_idx = next((i for i, value in enumerate(trace) if value >= est), None)
        if fire_idx is None:
            excluded += 1
            continue
        lags.append(max(trace[fire_idx:]) - est)
    return lags, excluded


def _session_matches_rearm_scope(
    records: Sequence[dict], since_ts: float | None, branch_filter: set[str] | None
) -> bool:
    """Whether a whole session belongs in a rearm-backtest run's scope.

    --since and --branches scope entire sessions here, not individual turns
    within one -- unlike `cost`'s per-record --branches filter, a re-arm
    simulation's turns-since-restart positioning depends on a session's own
    turn sequence staying intact, so silently dropping turns mid-session
    would desync _hook_observable_boundaries' turn-count boundaries from
    whatever's left of main_thread_turns.
    """
    if since_ts is not None:
        first_ts = next((ts for r in records if (ts := corpus._parse_ts(r.get("timestamp"))) is not None), None)
        if first_ts is not None and first_ts < since_ts:
            return False
    return branch_filter is None or any(
        r.get("type") == "assistant" and not bool(r.get("isSidechain")) and r.get("gitBranch") in branch_filter
        for r in records
    )
