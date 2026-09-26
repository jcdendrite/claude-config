"""The author-outcome command family: cmd_author_outcome and every helper
used only by it.

For each `--agent`-typed dispatch (default `code-writer`), answers whether
the `code-review` round that judged its diff recorded a must-fix
(`ADDRESS`) finding -- the numerator GitHub issue #800 asks for. See
docs/transcript-analysis.md's author-outcome section for the full failure
definition and every bucket/counter this module reports.

Imports corpus, pricing, render, review_rounds, and scope by module
(attribute access, not by name). This matches review_rounds.py's own
cross-module discipline (see scope.py's own top-of-file comment for why).

Classifies each round by reading its own review-narrative-ledger files
directly. Every file matching a session-id glob is read and merged --
session ids are globally unique (UUIDs), but a session spanning more than
one git worktree of the same repo appends under more than one repo-hash
prefix, so this is a merge across every matching file, not a 1:1 point
lookup. The transcript is still the sole source for round-open positions,
dispatch completion ordering, and the marker-write fallback signal. Only
the disposition/authoring-agent values move to the ledger.
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
import time
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

from transcript_analysis import corpus, pricing, render, review_rounds, scope

# review-ledger.sh's `append code-review` gate and marker.sh's `write code-review`
# gate happen to share this literal by coincidence, not as a shared enum.
# Each is validated independently by its own hook allowlist.
_CODE_REVIEW_SKILL = "code-review"

# Mirrors review-ledger.sh's own --disposition/--authoring-agent case enums.
# This module never writes a ledger line, only reads back what
# review-ledger.sh already wrote, so these are read-side comparison
# targets, not a second validator.
_DISPOSITION_ADDRESS = "ADDRESS"
_AUTHORING_AGENT_CODE_WRITER = "code-writer"
_AUTHORING_AGENT_INLINE = "inline"
_AUTHORING_AGENT_MIXED = "mixed"
_AUTHORING_AGENT_UNKNOWN = "unknown"
# None of these three is ever a real subagent_type. Each is synthesized by
# review-ledger.sh or this module's own transcript join, not a name --agent
# could legitimately be given.
_RESERVED_AGENT_TYPES = (_AUTHORING_AGENT_INLINE, _AUTHORING_AGENT_MIXED, _AUTHORING_AGENT_UNKNOWN)

_OUTCOME_FAILURE = "FAILURE"
_OUTCOME_PASS = "PASS"
_OUTCOME_UNRESOLVED = "UNRESOLVED"
_OUTCOME_UNATTRIBUTED = "UNATTRIBUTED"
_OUTCOME_KEYS = (_OUTCOME_FAILURE, _OUTCOME_PASS, _OUTCOME_UNRESOLVED, _OUTCOME_UNATTRIBUTED)

# Data-quality counter labels, shared verbatim between compute_author_outcomes
# (which increments them) and _print_author_outcome_report (which reads them
# back in this fixed order) -- named once here so the two can't drift.
_DQ_CO_AUTHORED_ROUNDS = "rounds co-authored by >1 dispatch"
_DQ_KILL_SWITCH_INFERRED_CLEAN = "rounds with a marker write but no ledger row (kill-switch inferred clean)"
_DQ_ROUND_NUMBER_MISMATCH = "sessions whose ledger round sequence doesn't match the transcript's round-opens"
_DQ_LEDGER_POSSIBLY_SWEPT = "sessions with a code-review round but no ledger file, cold enough to be swept"
_DQ_UNDECIDABLE = "dispatches with no paired tool_result (undecidable)"
_DQ_AUTHORING_AGENT_INCONSISTENT = "authoring_agent inconsistent with the transcript join"
_DQ_MALFORMED_DISPATCH_ID = "dispatches with a missing or empty tool_use_id"
_DATA_QUALITY_KEYS = (
    _DQ_CO_AUTHORED_ROUNDS, _DQ_KILL_SWITCH_INFERRED_CLEAN, _DQ_ROUND_NUMBER_MISMATCH,
    _DQ_LEDGER_POSSIBLY_SWEPT, _DQ_UNDECIDABLE, _DQ_AUTHORING_AGENT_INCONSISTENT,
    _DQ_MALFORMED_DISPATCH_ID,
)

# review-narrative-ledger's own directory name, one level under a Claude
# Code config-dir root -- mirrors review-ledger.sh's own $LEDGER_DIR.
_REVIEW_LEDGER_DIRNAME = "review-narrative-ledger"

# Fixed floor _ledger_possibly_swept compares against below: see _lib.sh's
# own _LEDGER_SWEEP_FLOOR_DAYS for the retention rationale (GH-973) and the
# duplicated-literal precedent. review-ledger.sh's own clear-stale
# subcommand is the only production reader of cleanupPeriodDays; this
# module never reads that setting.
_LEDGER_SWEEP_FLOOR_DAYS = 30


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


def _round_has_marker_write(records: list[dict], open_idx: int, span_end: int) -> bool:
    """True iff any Bash tool_use command inside this round's own outcome
    span [open_idx, span_end) is the hook-allowlisted `marker.sh write
    code-review` shape -- the fallback signal used only when the round has
    no ledger row at all (see _classify_round)."""
    for rec in records[open_idx:span_end]:
        if rec.get("isSidechain"):
            continue
        if rec.get("type") != "assistant":
            continue
        for block in (rec.get("message") or {}).get("content") or []:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            if block.get("name") != "Bash":
                continue
            command = (block.get("input") or {}).get("command") or ""
            if _is_clean_marker_write(command):
                return True
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


def _config_dir_root_for_session(jsonl: Path) -> Path:
    """The Claude Code config-dir root a transcript file lives under.

    jsonl is always <config_dir_root>/projects/<project-slug>/<session_id>.jsonl,
    scope.PROJECTS_DIR's own layout. Every other root scope.resolve_scan_roots
    can produce (a --config-dir root) shares that identical <dir>/projects
    layout. The config-dir root is therefore three parents up regardless of
    which root produced this path.
    """
    return jsonl.parent.parent.parent


def _ledger_files_for_session(jsonl: Path) -> list[Path]:
    """Every review-narrative-ledger file for this transcript's own
    session id, sorted by filename for a deterministic read order. The
    ledger filename is `<repo_hash>.<session_id>.jsonl` -- one per git
    worktree that appended to this session id -- so a session spanning
    more than one worktree of the same repo matches more than one file
    here, all of which _read_ledger_row_entries_for_session reads and
    merges.

    [] when no such file exists:
    - the kill switch was on for the session's entire lifetime
    - the session predates review-ledger.sh
    - every one of its ledger files was already swept
    """
    session_id = jsonl.stem
    ledger_dir = _config_dir_root_for_session(jsonl) / _REVIEW_LEDGER_DIRNAME
    return sorted(ledger_dir.glob(f"*.{session_id}.jsonl"))


def _read_ledger_rows_from_file(ledger_path: Path) -> tuple[list[dict], bool]:
    """(every JSON row from one ledger file, in file order; whether the
    file itself could be opened at all). A malformed line is skipped, not
    fatal, mirroring corpus._parse_jsonl_records' own per-line tolerance
    for a transcript file with a corrupted line. A file that opens
    successfully but yields zero valid rows still reports opened=True.
    opened=False only when open() itself raised OSError -- e.g. the file
    matched a caller's earlier glob but was deleted or became unreadable
    before this call."""
    rows: list[dict] = []
    try:
        with open(ledger_path) as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    row = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except OSError as exc:
        print(
            f"author-outcome: couldn't read ledger file {ledger_path}: {exc}",
            file=sys.stderr,
        )
        return [], False
    return rows, True


def _read_ledger_row_entries_for_session(jsonl: Path) -> tuple[list[tuple[int, dict]], bool]:
    """Every JSON row from every ledger file matching this session's own
    id, each tagged with source_index -- that file's own position in
    _ledger_files_for_session's sorted order.

    Rows are concatenated file by file in that sorted order, then
    stable-sorted by each row's own `event_time` field (an ISO-8601 UTC
    string that sorts correctly lexically; missing -> "" so a legacy
    pre-event_time row sorts first). Python's sort is stability-guaranteed,
    so ties keep that original concatenation (file-encounter) order.
    review-ledger.sh's own `show` merge (`sort_by(.event_time // "")`) has
    no such guarantee from jq -- see that comment for the caveat.

    Returns (entries, any_file_found). any_file_found is True iff at
    least one glob-matched file was also successfully opened. A file that
    matched the glob but raised OSError on open() -- e.g. evicted by
    review-ledger.sh clear-stale in the window between the glob and this
    read -- does not count, which is what distinguishes that race from N
    opened files that all round-trip to zero valid JSON rows. See
    _ledger_possibly_swept, the only caller that cares about the
    distinction.
    """
    files = _ledger_files_for_session(jsonl)
    entries: list[tuple[int, dict]] = []
    any_file_opened = False
    for file_index, ledger_path in enumerate(files):
        rows, opened = _read_ledger_rows_from_file(ledger_path)
        any_file_opened = any_file_opened or opened
        entries.extend((file_index, row) for row in rows)
    entries.sort(key=lambda entry: entry[1].get("event_time") or "")
    return entries, any_file_opened


def _round_number_mismatch(row_entries: list[tuple[int, dict]], round_open_count: int) -> bool:
    """True if this session's ledger rows -- merged across every
    worktree that appended to it, each tagged with its own source file's
    index (see _read_ledger_row_entries_for_session) -- don't resolve to
    the exact 1..round_open_count sequence the transcript's own
    round-open detector found for this session:

    - a gap (a round-open whose round never got a ledger row)
    - a ledger round number with no corresponding round-open
    - round rows recorded out of sequence
    - a round value reappearing non-contiguously after a different round
      value already appeared (e.g. [1, 2, 1])
    - the same round number claimed by more than one source file -- a
      worktree-subagent race where two files each independently append
      their own row for what the transcript treats as a single
      round-open, with no safe way to pick one file's row as
      authoritative

    A ledger with zero rows carrying a `round` key at all is not
    evaluated: entirely legacy rows (pre-schema-v2), or no ledger file for
    this session. There is no schema-v2 sequence to compare, so this
    always returns False for that case rather than flagging every
    pre-migration or ledger-less session as a mismatch.
    """
    keyed_entries = [
        (file_index, row["round"]) for file_index, row in row_entries
        if isinstance(row.get("round"), int) and not isinstance(row.get("round"), bool)
    ]
    if not keyed_entries:
        return False
    # groupby collapses each maximal run of an identical (file, round)
    # pair into one block. A round value whose rows are split across two
    # non-adjacent blocks (the reappearance case above, or a genuine
    # cross-file split) keeps a second, separate entry here even though
    # dict.fromkeys-style first-occurrence dedup would collapse it away.
    blocks = [key for key, _ in itertools.groupby(keyed_entries)]
    round_sequence = [round_value for _file_index, round_value in blocks]
    return round_sequence != list(range(1, round_open_count + 1))


def _ledger_possibly_swept(
    code_review_rounds: list[tuple[int, int]],
    ledger_rows: list[dict],
    records: list[dict],
    *,
    any_ledger_file_found: bool,
    now: float | None = None,
) -> bool:
    """True iff this session opened >=1 code-review round, has no ledger
    file at all (zero files matched the session's own glob -- not merely
    zero rows across whichever files did match), and the record at its
    EARLIEST code-review round's own open_idx is older than
    _LEDGER_SWEEP_FLOOR_DAYS -- review-ledger.sh's `append` command (the
    dominant eviction path) passes that fixed floor directly, not the
    dynamically-resolved window `clear-stale` uses. False otherwise,
    including when that record has no parseable timestamp to compare.

    Keyed on the EARLIEST round's own open, not the session's newest
    record: a swept file's last successful append is necessarily older
    than the floor, and that append happened inside some round that
    opened at or before it, so the earliest round's own open timestamp is
    always at or before every append -- keying there never misses a
    truly-swept file. It only over-excludes a session whose later rounds
    never appended at all, which carries no ledger signal to lose anyway.
    Keying on the newest record instead could read such a session as
    recent even though its ledger file is actually swept, letting a real
    FAILURE round misclassify as a kill-switch-inferred PASS. See
    docs/transcript-analysis.md's "Ledger-possibly-swept check" section
    for the rationale.

    This checks only the earliest round-open's own record for a
    timestamp, with no fallback to any other record in the session if
    that one is unparseable -- accepted because Claude Code transcript
    records reliably carry a `timestamp` field.

    any_ledger_file_found is the caller's own
    _read_ledger_row_entries_for_session result, resolved once per
    session and passed straight through here instead of re-globbing.
    """
    if not code_review_rounds:
        return False
    if ledger_rows or any_ledger_file_found:
        return False
    earliest_open_idx = code_review_rounds[0][0]
    ts = corpus._parse_ts(records[earliest_open_idx].get("timestamp"))
    if ts is None:
        return False
    now = time.time() if now is None else now
    return ts < now - _LEDGER_SWEEP_FLOOR_DAYS * 86400


def _classify_round(
    round_ordinal: int,
    ledger_rows: list[dict],
    has_marker_write: bool,
    data_quality: Counter,
) -> tuple[str, list[dict]]:
    """(classification, matching ledger rows) for one round, per the
    three-test failure definition in docs/transcript-analysis.md's
    author-outcome section.

    Ledger rows are matched to this round by exact `round` field equality
    against round_ordinal, this round's own 1-indexed position in the
    transcript's own code-review-open sequence. A legacy row (no `round`
    key) has round None, which can never equal an int, so it never matches
    any round. It falls through with every other round that has no
    matching ledger rows to the marker-write fallback below. That is the
    same path a round the kill switch suppressed every append for also
    takes.
    """
    matching = [
        row for row in ledger_rows
        if not isinstance(row.get("round"), bool) and row.get("round") == round_ordinal
    ]
    if any(row.get("disposition") == _DISPOSITION_ADDRESS for row in matching):
        return _OUTCOME_FAILURE, matching
    if matching:
        return _OUTCOME_PASS, matching
    if has_marker_write:
        # No ledger row at all for this round -- the kill switch was on,
        # or every append attempt errored before landing. But the round's
        # own marker.sh write code-review call still ran, so the review
        # did conclude clean. Distinct from a genuine ledger-backed PASS:
        # this bucket is inferred, not asserted.
        data_quality[_DQ_KILL_SWITCH_INFERRED_CLEAN] += 1
        return _OUTCOME_PASS, matching
    return _OUTCOME_UNATTRIBUTED, matching


def _agent_dispatch_tool_use_ids(
    records: list[dict], agent_type: str, data_quality: Counter,
) -> list[tuple[str, int]]:
    """Every (tool_use_id, record_idx) for an Agent/Task dispatch of
    `agent_type` on the main thread, in record order."""
    dispatches: list[tuple[str, int]] = []
    for idx, rec in enumerate(records):
        if rec.get("isSidechain"):
            continue
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
                data_quality[_DQ_MALFORMED_DISPATCH_ID] += 1
                continue
            dispatches.append((tool_use_id, idx))
    return dispatches


def _build_tool_result_index_map(records: list[dict]) -> dict[str, int]:
    """Map each tool_use_id to the record index into `records` of its own
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
        if rec.get("isSidechain"):
            continue
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
    agent_type: str = _AUTHORING_AGENT_CODE_WRITER,
    since_ts: float | None = None,
    now: float | None = None,
) -> dict:
    """Single pass over session_iter (main-thread only, no subagent merge --
    every transcript-side signal this join needs lives on the main thread;
    the disposition side comes from each session's own ledger file).

    now is forwarded to _ledger_possibly_swept as its own clock override --
    lets a caller/test inject a fixed value instead of monkeypatching the
    global time.time. Defaults to None, which leaves the real clock in
    place.

    Returns {"outcomes": Counter over _OUTCOME_KEYS, "data_quality": Counter
    over _DATA_QUALITY_KEYS}. See docs/transcript-analysis.md's
    author-outcome section for the UNRESOLVED/FAILURE/PASS/UNATTRIBUTED
    classification this implements, and the precondition (a dispatch with
    no paired tool_result is UNDECIDABLE, counted only under data_quality,
    never entering the three-test walk).
    """
    outcomes: Counter = Counter({key: 0 for key in _OUTCOME_KEYS})
    data_quality: Counter = Counter({key: 0 for key in _DATA_QUALITY_KEYS})

    # (jsonl path, round open_idx) -> round bookkeeping.
    # dispatch_count is the --since-filtered count: it gates "Dispatches in
    # scope" and which dispatches are charged an outcome.
    # unfiltered_dispatch_count is a separate, --since-unfiltered count the
    # authoring_agent inconsistency cross-check reads instead.
    # Reading one field two ways would report every round whose authoring
    # dispatch falls just outside a --since cutoff as spuriously
    # inconsistent, even though the round's own ledger rows are themselves
    # read without regard to --since at all.
    rounds_by_key: dict[tuple[Path, int], dict] = {}

    for jsonl, raw_records in session_iter:
        records = pricing.dedup_turns_by_request_id(raw_records)
        tool_result_index = _build_tool_result_index_map(records)
        code_review_rounds = _code_review_rounds(records)
        # Resolved once per session and reused for the round-number-mismatch
        # check, the possibly-swept check, and every round's own
        # classification below, instead of re-globbing per lookup.
        ledger_row_entries, any_ledger_file_found = _read_ledger_row_entries_for_session(jsonl)
        ledger_rows = [row for _file_index, row in ledger_row_entries]

        session_round_mismatch = _round_number_mismatch(ledger_row_entries, len(code_review_rounds))
        if session_round_mismatch:
            data_quality[_DQ_ROUND_NUMBER_MISMATCH] += 1

        session_ledger_possibly_swept = _ledger_possibly_swept(
            code_review_rounds, ledger_rows, records,
            any_ledger_file_found=any_ledger_file_found, now=now,
        )
        if session_ledger_possibly_swept:
            data_quality[_DQ_LEDGER_POSSIBLY_SWEPT] += 1

        for round_ordinal, (open_idx, span_end) in enumerate(code_review_rounds, start=1):
            has_marker_write = _round_has_marker_write(records, open_idx, span_end)
            classification, matching_ledger_rows = _classify_round(
                round_ordinal, ledger_rows, has_marker_write, data_quality,
            )
            rounds_by_key[(jsonl, open_idx)] = {
                "classification": classification,
                "matching_ledger_rows": matching_ledger_rows,
                "dispatch_count": 0,
                "unfiltered_dispatch_count": 0,
            }

        for tool_use_id, dispatch_idx in _agent_dispatch_tool_use_ids(records, agent_type, data_quality):
            # dispatch_idx is the dispatch's start (Agent/Task tool_use) record, so --since
            # filters by the dispatch's start timestamp, not its completion timestamp.
            ts = corpus._parse_ts(records[dispatch_idx].get("timestamp"))
            in_scope = since_ts is None or (ts is not None and ts >= since_ts)
            # A round-number-mismatched or possibly-swept-ledger session's
            # ledger/round join can't be trusted, so its dispatches still
            # count toward _DQ_ROUND_NUMBER_MISMATCH/_DQ_LEDGER_POSSIBLY_SWEPT
            # above but are excluded from the headline outcomes/"Dispatches
            # in scope" numerator-denominator.
            counts_toward_headline = (
                in_scope and not session_round_mismatch and not session_ledger_possibly_swept
            )

            completion_idx = tool_result_index.get(tool_use_id)
            if completion_idx is None:
                if in_scope:
                    # Deliberately not gated by session_round_mismatch or
                    # session_ledger_possibly_swept: a missing tool-result
                    # completion is a transcript-side attribution fact, not
                    # something either ledger-side exclusion affects.
                    data_quality[_DQ_UNDECIDABLE] += 1
                continue
            attributed_open_idx = next(
                (open_idx for open_idx, _span_end in code_review_rounds if open_idx > completion_idx),
                None,
            )
            if attributed_open_idx is None:
                if counts_toward_headline:
                    outcomes[_OUTCOME_UNRESOLVED] += 1
                continue
            round_entry = rounds_by_key[(jsonl, attributed_open_idx)]
            round_entry["unfiltered_dispatch_count"] += 1
            if in_scope:
                round_entry["dispatch_count"] += 1
            if counts_toward_headline:
                outcomes[round_entry["classification"]] += 1

    for round_entry in rounds_by_key.values():
        if round_entry["dispatch_count"] > 1:
            # Deliberately not gated by session_round_mismatch or
            # session_ledger_possibly_swept: more than one dispatch
            # attributed to a round is a transcript-side attribution fact,
            # not something either ledger-side exclusion affects.
            data_quality[_DQ_CO_AUTHORED_ROUNDS] += 1
        transcript_side = (
            agent_type if round_entry["unfiltered_dispatch_count"] >= 1
            else _AUTHORING_AGENT_INLINE
        )
        for row in round_entry["matching_ledger_rows"]:
            declared = row.get("authoring_agent") or ""
            if declared in ("", _AUTHORING_AGENT_UNKNOWN):
                continue
            consistent = (
                transcript_side == agent_type if declared == _AUTHORING_AGENT_MIXED
                else declared == transcript_side
            )
            if not consistent:
                data_quality[_DQ_AUTHORING_AGENT_INCONSISTENT] += 1

    return {"outcomes": outcomes, "data_quality": data_quality}


def cmd_author_outcome(args: argparse.Namespace) -> None:
    """For each `--agent`-typed dispatch (default code-writer), what share
    of the code-review rounds that judged its diff recorded a must-fix
    (ADDRESS) finding -- the numerator issue #800 defines. Read-only: no
    `gh` calls. Reads the transcript for round/dispatch structure and each
    session's own review-narrative-ledger file for disposition.

    See docs/transcript-analysis.md's author-outcome section for the full
    output shape, every named bias/counter, and this subcommand's
    documented scope gaps (e.g. a cross-session handoff split).
    """
    agent_type: str = args.agent
    # Exact-case match only -- a near-miss (e.g. "INLINE") doesn't collide with
    # any of the sentinels this guards against, so it's accepted, not normalized.
    if agent_type in _RESERVED_AGENT_TYPES:
        print(
            f"author-outcome: --agent {agent_type!r} is a reserved sentinel value "
            "(no dispatch attributed), not a valid --agent value",
            file=sys.stderr,
        )
        sys.exit(1)
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
    _OUTCOME_UNATTRIBUTED: "UNATTRIBUTED (round ran, no ledger row, no marker)",
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
