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
directly. Rows are attributed to a session by their own `session_id`
field, or by the session id in the filename for a row without one. Session
ids are globally unique (UUIDs), and a branch-keyed file holds several
sessions' rows, so this is a per-root index over every ledger file, not a
1:1 point lookup. The transcript is still the sole source for round-open positions,
dispatch completion ordering, and the marker-write fallback signal. Only
the disposition/authoring-agent values move to the ledger.
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import re
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
_DQ_MARKER_WRITE_WITHOUT_LEDGER_ROW = "rounds with a marker write but no ledger row (every append for the round failed)"
_DQ_ROUND_NUMBER_MISMATCH = "sessions whose ledger round sequence doesn't match the transcript's round-opens"
_DQ_LEDGER_POSSIBLY_SWEPT = "sessions with a code-review round but no ledger file, cold enough to be swept"
_DQ_UNDECIDABLE = "dispatches with no paired tool_result (undecidable)"
_DQ_AUTHORING_AGENT_INCONSISTENT = "authoring_agent inconsistent with the transcript join"
_DQ_MALFORMED_DISPATCH_ID = "dispatches with a missing or empty tool_use_id"
_DATA_QUALITY_KEYS = (
    _DQ_CO_AUTHORED_ROUNDS, _DQ_MARKER_WRITE_WITHOUT_LEDGER_ROW, _DQ_ROUND_NUMBER_MISMATCH,
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


def _read_ledger_rows_from_file(ledger_path: Path) -> tuple[list[dict], bool]:
    """(every JSON row from one ledger file, in file order; whether the
    file itself could be opened at all). A malformed or undecodable line
    (e.g. a torn multibyte write) is skipped, not fatal, mirroring
    corpus._parse_jsonl_records' own per-line tolerance for a transcript
    file with a corrupted line. A file that opens successfully but yields
    zero valid rows still reports opened=True. opened=False only when open()
    itself raised OSError -- e.g. the file matched a caller's earlier
    directory listing but was deleted or became unreadable before this
    call."""
    rows: list[dict] = []
    try:
        # Binary mode so a line that is not valid UTF-8 fails on its own
        # json.loads call instead of aborting the whole file's iteration.
        with open(ledger_path, "rb") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    row = json.loads(raw)
                except (json.JSONDecodeError, UnicodeDecodeError):
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


# Session ids are letters, digits, underscore and hyphen -- review-ledger.sh's
# own _lib_valid_session_id_component pattern. A row naming any other
# session_id is ignored rather than attributed.
_SESSION_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]+")


def _list_ledger_files(ledger_dir: Path) -> list[Path]:
    """Every ledger file directly under ledger_dir, sorted by filename for a
    deterministic read order. [] when the directory does not exist. This is
    the index's only directory listing, so a test can stand in for a file
    that vanishes between the listing and the read."""
    return sorted(ledger_dir.glob("*.jsonl"))


def _ledger_file_session_component(ledger_path: Path) -> str:
    """The session-id slot of a `<repo_hash>.<session_id>.jsonl` filename.
    The repo hash is hex, so the first dot ends it. A branch-keyed file's
    second slot is a branch hash, which no session id equals."""
    return ledger_path.name.removesuffix(".jsonl").partition(".")[2]


def _file_signature(path: Path) -> tuple[int, int] | None:
    """(mtime_ns, size), the change signature the index re-parses on. None
    when the file cannot be stat'ed."""
    try:
        stat_result = path.stat()
    except OSError:
        return None
    return stat_result.st_mtime_ns, stat_result.st_size


def _row_session_id(row: dict, ledger_path: Path) -> str | None:
    """The session a ledger row belongs to: its own `session_id` when it
    has one, else the session id in its file's name (a row from before rows
    carried session_id). None for a row naming an invalid session id."""
    if "session_id" not in row:
        return _ledger_file_session_component(ledger_path) or None
    session_id = row["session_id"]
    if isinstance(session_id, str) and _SESSION_ID_PATTERN.fullmatch(session_id):
        return session_id
    return None


class _LedgerIndex:
    """The parsed rows of every ledger file under one config-dir root,
    attributed to sessions by _row_session_id.

    refresh() re-lists the directory and re-parses only files whose
    (mtime_ns, size) changed since the previous refresh, so a file that
    appears or grows between two session lookups is seen, and an unchanged
    file is parsed once however many sessions read it. A file whose last
    open failed is retried on every refresh.
    """

    def __init__(self, ledger_dir: Path):
        self._ledger_dir = ledger_dir
        self._parsed_files: dict[Path, tuple[tuple[int, int] | None, list[dict], bool]] = {}
        self._rows_by_session: dict[str, list[tuple[Path, dict]]] = {}
        self._sessions_with_opened_file: set[str] = set()

    def refresh(self) -> None:
        parsed_files: dict[Path, tuple[tuple[int, int] | None, list[dict], bool]] = {}
        changed = False
        listed_files = _list_ledger_files(self._ledger_dir)
        for ledger_path in listed_files:
            signature = _file_signature(ledger_path)
            cached = self._parsed_files.get(ledger_path)
            if signature is not None and cached is not None and cached[0] == signature and cached[2]:
                parsed_files[ledger_path] = cached
                continue
            rows, opened = _read_ledger_rows_from_file(ledger_path)
            parsed_files[ledger_path] = (signature, rows, opened)
            changed = True
        if changed or parsed_files.keys() != self._parsed_files.keys():
            self._parsed_files = parsed_files
            self._attribute_rows(listed_files)

    def _attribute_rows(self, listed_files: list[Path]) -> None:
        rows_by_session: dict[str, list[tuple[Path, dict]]] = {}
        sessions_with_opened_file: set[str] = set()
        for ledger_path in listed_files:
            _signature, rows, opened = self._parsed_files[ledger_path]
            if opened:
                sessions_with_opened_file.add(_ledger_file_session_component(ledger_path))
            for row in rows:
                session_id = _row_session_id(row, ledger_path)
                if session_id is not None:
                    rows_by_session.setdefault(session_id, []).append((ledger_path, row))
        self._rows_by_session = rows_by_session
        self._sessions_with_opened_file = sessions_with_opened_file

    def entries_for_session(self, session_id: str) -> tuple[list[tuple[int, dict]], bool]:
        """(the session's rows, each tagged with its source file's position
        among the files the session has rows in, in filename order; whether
        a session-keyed file `<repo_hash>.<session_id>.jsonl` was opened).

        Rows are stable-sorted by their own `event_time` (an ISO-8601 UTC
        string that sorts correctly lexically; missing -> "" so a legacy
        pre-event_time row sorts first). Python's sort is stability-guaranteed,
        so ties keep file-encounter order. review-ledger.sh's own `show`
        (`sort_by(.event_time // "")`) has no such guarantee from jq -- see
        that comment for the caveat.
        """
        file_positions: dict[Path, int] = {}
        entries = [
            (file_positions.setdefault(ledger_path, len(file_positions)), row)
            for ledger_path, row in self._rows_by_session.get(session_id, [])
        ]
        entries.sort(key=lambda entry: entry[1].get("event_time") or "")
        return entries, session_id in self._sessions_with_opened_file


def _read_ledger_row_entries_for_session(
    jsonl: Path, ledger_indexes: dict[Path, _LedgerIndex] | None = None,
) -> tuple[list[tuple[int, dict]], bool]:
    """Every ledger row attributed to this transcript's own session id, as
    (file_index, row) entries (see _LedgerIndex.entries_for_session), and
    whether a session-keyed ledger file for it was opened.

    ledger_indexes holds one index per config-dir root and is shared across
    a caller's lookups so an unchanged file is parsed once. None uses a
    private index, which parses every file it reads.

    The bool is what _ledger_possibly_swept keys on: a session-keyed file
    that matched the directory listing but raised OSError on open -- e.g.
    evicted by review-ledger.sh clear-stale in the window between the
    listing and the read -- does not count. That distinguishes the eviction
    race from a file that opened and holds zero valid JSON rows.
    """
    config_dir_root = _config_dir_root_for_session(jsonl)
    indexes = {} if ledger_indexes is None else ledger_indexes
    index = indexes.get(config_dir_root)
    if index is None:
        index = indexes[config_dir_root] = _LedgerIndex(config_dir_root / _REVIEW_LEDGER_DIRNAME)
    index.refresh()
    return index.entries_for_session(jsonl.stem)


def _round_blocks(row_entries: list[tuple[int, dict]]) -> list[list[dict]]:
    """Group a session's round-keyed rows into blocks: each maximal run of
    one (file, round) pair is one block, in entry order. A row without an
    integer, non-bool `round` (a pre-round legacy row, or a JSON boolean)
    belongs to no block.

    A round value whose rows are split across two non-adjacent runs keeps a
    second, separate block, which dict-style first-occurrence dedup would
    collapse away.
    """
    keyed_entries = [
        (file_index, row) for file_index, row in row_entries
        if isinstance(row.get("round"), int) and not isinstance(row.get("round"), bool)
    ]
    return [
        [row for _file_index, row in run]
        for _key, run in itertools.groupby(
            keyed_entries, key=lambda entry: (entry[0], entry[1]["round"]),
        )
    ]


def _round_number_mismatch(row_entries: list[tuple[int, dict]], round_open_count: int) -> bool:
    """True if this session's ledger rows -- each tagged with its source
    file's index (see _read_ledger_row_entries_for_session) -- don't map
    one-to-one, by rank, onto the transcript's own round-opens. The k-th
    round-open takes the k-th block (see _round_blocks), so a mismatch is:

    - a different number of blocks than round-opens (a round-open whose
      round never got a ledger row, or a ledger round with no round-open)
    - block round values that are not strictly increasing (rounds recorded
      out of sequence, a round value reappearing after a different one, or
      the same round number claimed by more than one source file -- a
      worktree-subagent race with no safe way to pick one file's row as
      authoritative)

    Round values are compared only for order, never for equality with the
    round-open ordinal: rounds are branch-scoped, so a later session on a
    branch starts above 1.

    A ledger with zero rows carrying a `round` key at all is not
    evaluated: entirely legacy rows (pre-schema-v2), or no ledger rows for
    this session. There is no schema-v2 sequence to compare, so this
    always returns False for that case rather than flagging every
    pre-migration or ledger-less session as a mismatch.

    A round with no rows makes the block count short, so it counts as a
    mismatch. A stray extra block that makes up for a missing round still
    matches by count and order; docs/transcript-analysis.md records that
    residual.
    """
    blocks = _round_blocks(row_entries)
    if not blocks:
        return False
    if len(blocks) != round_open_count:
        return True
    round_values = [block[0]["round"] for block in blocks]
    return any(later <= earlier for earlier, later in zip(round_values, round_values[1:], strict=False))


def _ledger_possibly_swept(
    code_review_rounds: list[tuple[int, int]],
    ledger_rows: list[dict],
    records: list[dict],
    *,
    any_ledger_file_found: bool,
    now: float | None = None,
) -> bool:
    """True iff this session opened >=1 code-review round, has no ledger
    rows attributed to it, has no session-keyed ledger file that opened
    (not merely zero rows in whichever files did), and the record at its
    EARLIEST code-review round's own open_idx is older than
    _LEDGER_SWEEP_FLOOR_DAYS -- review-ledger.sh's `append` command (the
    dominant eviction path) passes that fixed floor directly, not the
    dynamically-resolved window `clear-stale` uses. False otherwise,
    including when that record has no parseable timestamp to compare.

    Keyed on the earliest round-open, not the newest record, to never
    miss a truly-swept file -- see docs/transcript-analysis.md's
    "Ledger-possibly-swept check" section for why.

    This checks only the earliest round-open's own record for a
    timestamp, with no fallback to any other record in the session if
    that one is unparseable -- accepted because Claude Code transcript
    records reliably carry a `timestamp` field.

    any_ledger_file_found is the caller's own
    _read_ledger_row_entries_for_session result, resolved once per
    session and passed straight through here instead of re-reading.
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
    round_rows: list[dict],
    has_marker_write: bool,
    data_quality: Counter,
) -> tuple[str, list[dict]]:
    """(classification, that round's own ledger rows) for one round, per the
    three-test failure definition in docs/transcript-analysis.md's
    author-outcome section.

    round_rows is the round's own block (see _round_blocks), matched to the
    round by rank rather than by `round` value. A round with no block --
    including a session whose rows are all legacy (no `round` key) -- falls
    through to the marker-write fallback below. That is the same path a
    round for which every append failed also takes.
    """
    if any(row.get("disposition") == _DISPOSITION_ADDRESS for row in round_rows):
        return _OUTCOME_FAILURE, round_rows
    if round_rows:
        return _OUTCOME_PASS, round_rows
    if has_marker_write:
        # No ledger row at all for this round -- every append attempt
        # errored before landing. But the round's own clean-marker write
        # still ran, so the review did conclude clean. Distinct from a
        # genuine ledger-backed PASS: this bucket is inferred, not asserted.
        data_quality[_DQ_MARKER_WRITE_WITHOUT_LEDGER_ROW] += 1
        return _OUTCOME_PASS, round_rows
    return _OUTCOME_UNATTRIBUTED, round_rows


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

    # One _LedgerIndex per config-dir root, shared by every session's lookup.
    ledger_indexes: dict[Path, _LedgerIndex] = {}

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
        # classification below, instead of re-reading per lookup.
        ledger_row_entries, any_ledger_file_found = _read_ledger_row_entries_for_session(
            jsonl, ledger_indexes,
        )
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

        # The k-th round-open takes the k-th block of the session's rows. A
        # mismatched session's round join is untrusted, so its rounds are
        # not classified and read no ledger rows: classifying them as
        # row-less would count every marker-write round as having no row.
        round_blocks = [] if session_round_mismatch else _round_blocks(ledger_row_entries)

        for round_index, (open_idx, span_end) in enumerate(code_review_rounds):
            if session_round_mismatch:
                classification, matching_ledger_rows = None, []
            else:
                round_rows = round_blocks[round_index] if round_index < len(round_blocks) else []
                has_marker_write = _round_has_marker_write(records, open_idx, span_end)
                classification, matching_ledger_rows = _classify_round(
                    round_rows, has_marker_write, data_quality,
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
    session's own rows in the review-narrative-ledger for disposition.

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
