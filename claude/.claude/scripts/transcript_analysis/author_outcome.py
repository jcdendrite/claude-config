"""The author-outcome command family: cmd_author_outcome and every helper
used only by it.

For each `--agent`-typed dispatch (default `code-writer`), answers whether
the `code-review` round that judged its diff recorded a must-fix
(`ADDRESS`) finding -- the numerator GitHub issue #800 asks for. See
docs/transcript-analysis.md's author-outcome section for the full failure
definition and every bucket/counter this module reports, and
.claude/plans/review-ledger-agent-instrumentation.md's "Failure definition"
section for the design rationale.

Imports corpus, pricing, render, review_rounds, and scope by module
(attribute access, not by name) -- matching review_rounds.py's own
cross-module discipline (see scope.py's own top-of-file comment for why).

Reads only the transcript -- no review-narrative-ledger file is ever
opened. Every signal the classifier needs (a round's own `--disposition`/
`--authoring-agent` values, and a clean marker write) is already a
literal argv token on the transcript's own `review-ledger.sh append
code-review` and `marker.sh write code-review` Bash `tool_use` commands,
matched by argv shape rather than joined against a second, on-disk
representation.
"""
from __future__ import annotations

import argparse
import os
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

from transcript_analysis import corpus, pricing, render, review_rounds, scope

# The review_rounds.REVIEW_SKILLS member this subcommand's whole join keys
# on. review-ledger.sh's own gate name ("append code-review") and
# marker.sh's own gate name ("write code-review") happen to share this same
# literal, but each is validated independently by its own hook allowlist --
# this is not a shared enum symbol, just a coincidence of naming.
_CODE_REVIEW_SKILL = "code-review"

# Mirrors review-ledger.sh's own --disposition/--authoring-agent case enums
# -- this module never writes a ledger line, only reads back what
# review-ledger.sh's own hook-enforced argv shape already carries, so these
# are read-side comparison targets, not a second validator.
_DISPOSITION_ADDRESS = "ADDRESS"
_AUTHORING_AGENT_CODE_WRITER = "code-writer"
_AUTHORING_AGENT_INLINE = "inline"
_AUTHORING_AGENT_MIXED = "mixed"
_AUTHORING_AGENT_UNKNOWN = "unknown"

_OUTCOME_FAILURE = "FAILURE"
_OUTCOME_PASS = "PASS"
_OUTCOME_UNRESOLVED = "UNRESOLVED"
_OUTCOME_UNATTRIBUTED = "UNATTRIBUTED"
_OUTCOME_KEYS = (_OUTCOME_FAILURE, _OUTCOME_PASS, _OUTCOME_UNRESOLVED, _OUTCOME_UNATTRIBUTED)

# Data-quality counter labels, shared verbatim between compute_author_outcomes
# (which increments them) and _print_author_outcome_report (which reads them
# back in this fixed order) -- named once here so the two can't drift.
_DQ_CO_AUTHORED_ROUNDS = "rounds co-authored by >1 dispatch"
_DQ_UNPARSEABLE_APPEND = "append calls with unparseable flags (skipped)"
_DQ_REJECTED_APPEND = "append calls rejected by review-ledger.sh (skipped)"
_DQ_UNDECIDABLE = "dispatches with no paired tool_result (undecidable)"
_DQ_AUTHORING_AGENT_INCONSISTENT = "authoring_agent inconsistent with the transcript join"
_DATA_QUALITY_KEYS = (
    _DQ_CO_AUTHORED_ROUNDS, _DQ_UNPARSEABLE_APPEND, _DQ_REJECTED_APPEND,
    _DQ_UNDECIDABLE, _DQ_AUTHORING_AGENT_INCONSISTENT,
)


# The "review-ledger.sh append code-review" shape match below has no hook-level
# enforcement (unlike the marker-write shape enforce-marker-script-shape.sh's allowlist
# enforces), so an append call invoked through indirection silently fails to match --
# see docs/transcript-analysis.md's "Unenforced append-call shape match" for the full caveat.
def _parse_ledger_append_flags(segment: list[str]) -> dict[str, str] | None:
    """The {"disposition": ..., "authoring_agent": ...} values of one
    already-tokenized &&/||/;/|-chained segment (one of
    corpus.split_command_segments' own return elements) invoking
    `review-ledger.sh append code-review`.

    Returns {} -- not None -- for a segment that doesn't match that shape
    at all (basename(argv[0]) != "review-ledger.sh", or the literal
    subcommand pair isn't "append code-review"): an ordinary, unrelated
    segment is simply not an append call, not a parse failure. Returns
    None only when the segment *does* match that shape but yields no
    `--disposition` value -- a genuine parse failure on an identified
    append attempt, which the caller counts under _DQ_UNPARSEABLE_APPEND.

    `authoring_agent` is absent from the returned dict, not empty-stringed,
    when `--authoring-agent` itself is absent from the segment -- an append
    call predating this repo's authoring-agent flag has no key for it at
    all, distinct from an explicitly empty declared value.
    """
    if len(segment) < 3:
        return {}
    if os.path.basename(segment[0]) != "review-ledger.sh":
        return {}
    if segment[1] != "append" or segment[2] != _CODE_REVIEW_SKILL:
        return {}
    flags: dict[str, str] = {}
    for i, token in enumerate(segment):
        if token == "--disposition" and i + 1 < len(segment):
            flags["disposition"] = segment[i + 1]
        elif token == "--authoring-agent" and i + 1 < len(segment):
            flags["authoring_agent"] = segment[i + 1]
    if "disposition" not in flags:
        return None
    return flags


def _is_clean_marker_write(command: str) -> bool:
    """True iff any &&/||/;/|-chained segment of `command` is the
    hook-allowlisted `marker.sh write code-review` shape: basename(argv[0])
    == "marker.sh" followed by exactly "write" "code-review".

    Stricter than a substring search, and deliberately weaker than
    reimplementing enforce-marker-script-shape.sh's own MARKER_SHAPE regex
    -- sufficient here because that hook already guarantees no wrapper or
    variable form of this call can exist in the transcript in the first
    place.
    """
    for segment in corpus.split_command_segments(command):
        if len(segment) < 3:
            continue
        if os.path.basename(segment[0]) != "marker.sh":
            continue
        if segment[1] == "write" and segment[2] == _CODE_REVIEW_SKILL:
            return True
    return False


# Rescans the full session's records per matched append call rather than the round-scoped
# slice, O(n^2) worst case across a round's matched calls; acceptable for an occasional batch
# CLI run, revisit if a real corpus run shows it dominating wall-clock.
def _is_append_call_rejected(records: list[dict], tool_use_id: str) -> bool:
    """True iff `tool_use_id`'s own paired `tool_result` block carries
    `is_error` -- review-ledger.sh append rejecting an invalid
    `--disposition`/`--authoring-agent` enum or an over-cap value at
    runtime.

    A matched append call with no paired tool_result at all (the Bash call
    never completed) is treated as accepted rather than rejected: every
    append call inside an already-closed round span has necessarily
    completed by the time that round opened.
    """
    for rec in records:
        if rec.get("type") != "user":
            continue
        content = (rec.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            if block.get("tool_use_id") == tool_use_id:
                return bool(block.get("is_error"))
    return False


def _code_review_rounds(records: list[dict]) -> list[tuple[int, int]]:
    """Every code-review round's (open_idx, span_end) in one session's
    already-deduped, main-thread records.

    span_end is the *next* code-review round's own open_idx, or
    len(records) for the last one -- deliberately not
    review_rounds.detect_round_windows' own window_end, which closes at the
    next fresh user prompt (too narrow: a user interjection between the
    review and its marker write must not end the round's own outcome span).
    """
    opens = [
        open_idx for open_idx, _window_end, skill in review_rounds.detect_round_windows(records)
        if skill == _CODE_REVIEW_SKILL
    ]
    return [
        (open_idx, opens[i + 1] if i + 1 < len(opens) else len(records))
        for i, open_idx in enumerate(opens)
    ]


def _round_ledger_signals(
    records: list[dict], open_idx: int, span_end: int, data_quality: Counter,
) -> tuple[list[dict[str, str]], bool]:
    """(accepted append-call flag dicts, whether a clean marker-write call
    appears) inside one round's own outcome span [open_idx, span_end).

    A segment matching the append shape but yielding no `--disposition` is
    counted under _DQ_UNPARSEABLE_APPEND and excluded. An append call whose
    paired tool_result is is_error is counted under _DQ_REJECTED_APPEND and
    excluded too -- a rejected review-ledger.sh append wrote nothing to the
    real ledger, so it must not satisfy the classifier's own "≥ 1 append
    call" criterion.
    """
    append_calls: list[dict[str, str]] = []
    has_marker_write = False
    for rec in records[open_idx:span_end]:
        if rec.get("type") != "assistant":
            continue
        for block in (rec.get("message") or {}).get("content") or []:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            if block.get("name") != "Bash":
                continue
            command = (block.get("input") or {}).get("command") or ""
            for segment in corpus.split_command_segments(command):
                flags = _parse_ledger_append_flags(segment)
                if flags is None:
                    data_quality[_DQ_UNPARSEABLE_APPEND] += 1
                    continue
                if not flags:
                    continue
                tool_use_id = block.get("id") or ""
                if tool_use_id and _is_append_call_rejected(records, tool_use_id):
                    data_quality[_DQ_REJECTED_APPEND] += 1
                    continue
                append_calls.append(flags)
            if _is_clean_marker_write(command):
                has_marker_write = True
    return append_calls, has_marker_write


def _classify_round(append_calls: list[dict[str, str]], has_marker_write: bool) -> str:
    """Classification for one round's own outcome span, per the
    three-test failure definition in docs/transcript-analysis.md's
    author-outcome section. Test 1 (no attributed round -> UNRESOLVED) is
    handled by the caller before a round ever reaches this function.
    """
    if any(call.get("disposition") == _DISPOSITION_ADDRESS for call in append_calls):
        return _OUTCOME_FAILURE
    if append_calls or has_marker_write:
        return _OUTCOME_PASS
    return _OUTCOME_UNATTRIBUTED


def _agent_dispatch_tool_use_ids(records: list[dict], agent_type: str) -> list[tuple[str, int]]:
    """Every (tool_use_id, record_idx) for an Agent/Task dispatch of
    `agent_type` on the main thread, in record order."""
    dispatches: list[tuple[str, int]] = []
    for idx, rec in enumerate(records):
        if rec.get("type") != "assistant":
            continue
        for block in (rec.get("message") or {}).get("content") or []:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            if block.get("name") not in pricing._SPAWN_TOOL_NAMES:
                continue
            tool_input = block.get("input") or {}
            if tool_input.get("subagent_type") != agent_type:
                continue
            tool_use_id = block.get("id") or ""
            if not tool_use_id:
                continue
            dispatches.append((tool_use_id, idx))
    return dispatches


def _build_tool_result_index_map(records: list[dict]) -> dict[str, int]:
    """Map each tool_use_id to the record index (into `records`) of its own
    paired tool_result -- the dispatch's completion position, used as the
    ordering key against each round's own open_idx (mechanism
    justifications: completion-keyed, not start-keyed, since a round can
    legitimately open while a dispatch is still running).

    Mirrors reviewer_yield._build_tool_result_ts_map's own scan shape, but
    returns the record's index rather than its timestamp -- both keys must
    be indices into the same (already deduped) records list, never a
    timestamp compared against an index.
    """
    index_map: dict[str, int] = {}
    for idx, rec in enumerate(records):
        if rec.get("type") != "user":
            continue
        content = (rec.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            tid = block.get("tool_use_id")
            if tid:
                index_map[tid] = idx
    return index_map


def compute_author_outcomes(
    session_iter: Iterator[tuple[Path, list[dict]]],
    *,
    agent_type: str = "code-writer",
    since_ts: float | None = None,
) -> dict:
    """Single pass over session_iter (main-thread only, no subagent merge --
    every signal this join needs lives on the main thread).

    Returns {"outcomes": Counter over _OUTCOME_KEYS, "data_quality": Counter
    over _DATA_QUALITY_KEYS}. See the "Failure definition" section of
    .claude/plans/review-ledger-agent-instrumentation.md for the
    UNRESOLVED/FAILURE/PASS/UNATTRIBUTED classification this implements,
    and the precondition (a dispatch with no paired tool_result is
    UNDECIDABLE, counted only under data_quality, never entering the
    three-test walk).
    """
    outcomes: Counter = Counter({key: 0 for key in _OUTCOME_KEYS})
    data_quality: Counter = Counter({key: 0 for key in _DATA_QUALITY_KEYS})

    # (jsonl path, round open_idx) -> round bookkeeping. dispatch_count is
    # the --since-filtered count (gates "Dispatches in scope" and which
    # dispatches are charged an outcome); unfiltered_dispatch_count is a
    # separate, --since-unfiltered count the authoring_agent inconsistency
    # cross-check reads instead -- one field read two ways would report
    # every round whose authoring dispatch falls just outside --since as
    # spuriously inconsistent, even though the round's own append/marker
    # signals are themselves evaluated without regard to --since at all.
    rounds_by_key: dict[tuple[Path, int], dict] = {}

    for jsonl, raw_records in session_iter:
        records = pricing.dedup_turns_by_request_id(raw_records)
        code_review_rounds = _code_review_rounds(records)
        for open_idx, span_end in code_review_rounds:
            append_calls, has_marker_write = _round_ledger_signals(records, open_idx, span_end, data_quality)
            classification = _classify_round(append_calls, has_marker_write)
            rounds_by_key[(jsonl, open_idx)] = {
                "classification": classification,
                "append_calls": append_calls,
                "dispatch_count": 0,
                "unfiltered_dispatch_count": 0,
            }

        tool_result_index = _build_tool_result_index_map(records)
        for tool_use_id, dispatch_idx in _agent_dispatch_tool_use_ids(records, agent_type):
            ts = corpus._parse_ts(records[dispatch_idx].get("timestamp"))
            in_scope = since_ts is None or (ts is not None and ts >= since_ts)

            completion_idx = tool_result_index.get(tool_use_id)
            if completion_idx is None:
                if in_scope:
                    data_quality[_DQ_UNDECIDABLE] += 1
                continue
            attributed_open_idx = next(
                (open_idx for open_idx, _span_end in code_review_rounds if open_idx > completion_idx),
                None,
            )
            if attributed_open_idx is None:
                if in_scope:
                    outcomes[_OUTCOME_UNRESOLVED] += 1
                continue
            round_entry = rounds_by_key[(jsonl, attributed_open_idx)]
            round_entry["unfiltered_dispatch_count"] += 1
            if in_scope:
                round_entry["dispatch_count"] += 1
                outcomes[round_entry["classification"]] += 1

    for round_entry in rounds_by_key.values():
        if round_entry["dispatch_count"] > 1:
            data_quality[_DQ_CO_AUTHORED_ROUNDS] += 1
        transcript_side = (
            _AUTHORING_AGENT_CODE_WRITER if round_entry["unfiltered_dispatch_count"] >= 1
            else _AUTHORING_AGENT_INLINE
        )
        for call in round_entry["append_calls"]:
            declared = call.get("authoring_agent") or ""
            if declared in ("", _AUTHORING_AGENT_UNKNOWN):
                continue
            if declared == _AUTHORING_AGENT_MIXED:
                consistent = transcript_side == _AUTHORING_AGENT_CODE_WRITER
            else:
                consistent = declared == transcript_side
            if not consistent:
                data_quality[_DQ_AUTHORING_AGENT_INCONSISTENT] += 1

    return {"outcomes": outcomes, "data_quality": data_quality}


def cmd_author_outcome(args: argparse.Namespace) -> None:
    """For each `--agent`-typed dispatch (default code-writer), what share
    of the code-review rounds that judged its diff recorded a must-fix
    (ADDRESS) finding -- the numerator issue #800 defines. Read-only, no gh calls,
    and reads only the transcript -- no ledger file is ever opened.

    See docs/transcript-analysis.md's author-outcome section for the full
    output shape, every named bias/counter, and this subcommand's
    documented scope gaps (e.g. a cross-session handoff split).
    """
    agent_type: str = getattr(args, "agent", None) or "code-writer"
    since_ts, since_raw = scope._parse_since_nd_arg(args, "author-outcome")

    roots = scope.resolve_scan_roots(args)
    session_iter, scope_label = scope._resolve_project_scope(args, "author-outcome", roots=roots)
    scope.print_resolved_scope("author-outcome", scope_label, roots)

    result = compute_author_outcomes(session_iter, agent_type=agent_type, since_ts=since_ts)
    _print_author_outcome_report(result, agent_type=agent_type, since_raw=since_raw)


_OUTCOME_LABELS = {
    _OUTCOME_FAILURE: "FAILURE      (round had >=1 ADDRESS)",
    _OUTCOME_PASS: "PASS         (round concluded clean)",
    _OUTCOME_UNRESOLVED: "UNRESOLVED   (no subsequent round)",
    _OUTCOME_UNATTRIBUTED: "UNATTRIBUTED (round ran, no append, no marker)",
}


def _print_author_outcome_report(result: dict, *, agent_type: str, since_raw: str | None) -> None:
    outcomes = result["outcomes"]
    data_quality = result["data_quality"]
    window_label = f"last {since_raw}" if since_raw else "all time"
    print(f"agent={agent_type}  window={window_label}")
    print()

    failure = outcomes[_OUTCOME_FAILURE]
    passed = outcomes[_OUTCOME_PASS]
    total = sum(outcomes[key] for key in _OUTCOME_KEYS)
    resolved = failure + passed

    label_width = max(len(label) for label in _OUTCOME_LABELS.values())
    print(f"{'Dispatches in scope':<{label_width}} {total:>3}")
    for key in _OUTCOME_KEYS:
        print(f"  {_OUTCOME_LABELS[key]:<{label_width - 2}} {outcomes[key]:>3}")
    print(f"Failure share: {failure} of {resolved} resolved dispatches ({render._pct_of(failure, resolved)})")
    print()

    print("Data quality")
    dq_label_width = max(len(label) for label in _DATA_QUALITY_KEYS)
    for key in _DATA_QUALITY_KEYS:
        print(f"  {key:<{dq_label_width}} {data_quality[key]:>3}")
