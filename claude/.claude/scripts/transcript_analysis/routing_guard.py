"""The routing-guard command: `--capture` publishes one write-once file per sampled branch, else it computes the guard.

A file holds the token counts of a branch's review loops, so a later run can price them at the
rate table current then. `docs/transcript-analysis.md` § "routing-guard" states what compute reads and prints.

OUTPUT INVARIANT, each clause pinned by the tests it names:
- Free text (assistant and user message text, `Skill` and `Agent` arguments, denial text, a dispatch's meta description)
  reaches no capture file, stdout or stderr. Denial text is read only to classify a record.
  Pinned by `TestFreeText` in test_transcript_routing_guard_capture.py.
- No line names a branch, a config-directory path or an account label.
  Pinned by `_invoke_routing_guard` in _routing_guard_helpers.py, on every exit.
- A capture file stores `message.model` strings verbatim. Every other string it holds is a fixed label.
  Pinned by `TestGoldenFile` in test_transcript_routing_guard_capture.py.
- The session config-keys log is read for a row's session id, `logged_at` and key values. A session id and a
  key value reach no capture file, stdout or stderr, and each key value reaches the file only as `on` or `off`.
  Pinned by `TestKeyStateOutput` in test_transcript_routing_guard_capture.py.
- A transcript `requestId` can reach stderr, and nothing else, through pricing.dedup_turns_by_request_id's rate-limited
  prints. The DO NOT PUBLISH banner covers it. Pinned by `TestFreeText` in test_transcript_routing_guard_capture.py.
- Compute writes no string read from a capture file, a model string included, to stdout, stderr or the `--out` file.
  Pinned by `TestPricing` and `TestCaptureFiles` in test_transcript_routing_guard_compute.py.
"""
from __future__ import annotations

import argparse
import bisect
import contextlib
import enum
import hashlib
import io
import json
import math
import os
import random
import re
import stat
import sys
from collections import Counter, defaultdict
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple, NoReturn

import _config
from transcript_analysis import (
    author_outcome,
    corpus,
    denials,
    ledger_common,
    pricing,
    render,
    review_rounds,
    scope,
    session_pricing,
)

_COMMAND = "routing-guard"

# Bump only when a change alters what a stored row means. Changing the top-level or loop keys strands every standing
# file, bumped or not: the validator accepts only the current keys there and a reader opens only the current revision.
_CAPTURE_REVISION = 1
_CAPTURES_DIR_NAME = "routing-guard-captures"
# docs/design-decisions/review-orchestrator-routing-gate.md: "The guard runs once at least 10 key-on PRs have
# merged, a chosen minimum." Read when the check runs, so a test can lower it.
_MIN_ON_BRANCHES = 10
# The same record: "a percentile bootstrap of 10,000 replicates, a chosen count".
_BOOTSTRAP_REPLICATES = 10_000
# The same record: the lower bound "at 95%, a chosen level".
_GUARD_CI_LEVEL = 0.95
# Chosen: a fixed seed makes a re-run on the same files report the same interval.
_BOOTSTRAP_SEED = 0
# pricing.py's rates are dollars per million tokens.
_TOKENS_PER_MTOK = 1_000_000
# The root of the checkout holding this module. Path.resolve() follows stow's symlinks into it.
_CHECKOUT_ROOT = Path(__file__).resolve().parents[4]

# Placeholder leaf for the git work tree check on the captures directory, which must see the directory itself.
_GIT_TREE_CHECK_LEAF = "capture"
_CAPTURE_TEMP_PREFIX = f".{_COMMAND}-"
_CAPTURE_TEMP_SUFFIX = ".tmp"

# require-review-orchestrator-routing.sh's DENY_GATE_LABEL, which denials._denial_hook_label returns for its denials.
_ROUTING_DENY_LABEL = "review-orchestrator routing"
_WORKTREE_SLUG_INFIX = "--claude-worktrees-"

_UNAUTHENTICATED_INPUTS_STATEMENT = (
    "the key state behind PASSED and FAILED comes from log rows any process running as the account can write,"
    " and compute reads capture files the account can write too, as it can the transcripts they were built from,"
    " so those statuses rest on unauthenticated inputs"
)
_DO_NOT_PUBLISH_BANNER = (
    "DO NOT PUBLISH -- this output belongs to the owner alone.\n"
    "`docs/private-project-redaction.md` section \"A wider corpus goes to the owner, never into a public artifact\""
    " governs every quantity the guard computes."
    " A public artifact states only whether the guard passed and whether the default flipped.\n"
    "Run and read routing-guard outside any agent session.\n"
    f"Note that {_UNAUTHENTICATED_INPUTS_STATEMENT}."
)
_UNAUTHENTICATED_INPUTS_LINE = f"{_COMMAND}: {_UNAUTHENTICATED_INPUTS_STATEMENT}"

_DROPPED_DENY_NOTICE = (
    f"{_COMMAND}: a routing-labeled denial whose tool_use is not in its session was not counted;"
    " if it is a real routing deny, its branch may be missing from the note;"
    ' see docs/transcript-analysis.md routing-guard "Deny reconciliation"'
)

_NOTE_COLUMN_COUNT = 3
_STATUS_MERGED = "merged"
_STATUS_EXCLUDED = "excluded"
_NOTE_STATUSES = (_STATUS_MERGED, _STATUS_EXCLUDED)
_WRONG_COLUMN_COUNT_REASON = (
    "the row needs three columns: config dir, branch and merged or excluded;"
    " the session config-keys log supplies the arm and pilot states"
)
_UNKNOWN_STATUS_REASON = f"the third column must be {_STATUS_MERGED} or {_STATUS_EXCLUDED}"

# The stored and derived state of a key. The routing key's state names the arm.
_STATE_ON = "on"
_STATE_OFF = "off"
# A branch's state for a key when its rounds disagree, or when a round's state is unknown. Neither is stored,
# because capture refuses such a branch.
_STATE_MIXED = "mixed"
_STATE_UNKNOWN = "unknown"
# config-keys.psv keys, read from the session config-keys log. docs/session-config-keys-log.md § "Values" defines the tokens.
_ROUTING_KEY = "review_orchestrator_routing"
_ROUND2_PILOT_KEY = "round_consult_round2_pilot"
_ARCHITECT_PILOT_KEY = "architect_fix_plan_pilot"
_STATE_KEYS = (_ROUTING_KEY, _ROUND2_PILOT_KEY, _ARCHITECT_PILOT_KEY)
_LOG_VALUE_OFF = "false"
_LOG_VALUE_UNREADABLE = "unreadable"

_REASON_NO_RECORDS = "no-records"
_REASON_NO_COMPLETE_LOOP = "no-complete-loop"
_REASON_ORCHESTRATOR_UNREADABLE = "orchestrator-unreadable"
_REASON_KEY_STATE_UNKNOWN = "key-state-unknown"
_REASON_KEY_STATE_MIXED = "key-state-mixed"
_REASON_KEY_STATE_CONTRADICTED = "key-state-contradicted"

# The causes of an unknown key state.
_CAUSE_ROUND_UNTIMED = "round-untimed"
_CAUSE_LOG_UNUSABLE = "log-unusable"
_CAUSE_LOST_EVENT = "lost-event"
_CAUSE_BEFORE_RECORDER = "before-recorder"
_CAUSE_HOOK_NOT_RUN = "hook-not-run"
_CAUSE_UNREADABLE = "unreadable"
_CAUSE_KEY_NOT_IN_ROW = "key-not-in-row"
_CAUSES = (
    _CAUSE_ROUND_UNTIMED,
    _CAUSE_LOG_UNUSABLE,
    _CAUSE_LOST_EVENT,
    _CAUSE_BEFORE_RECORDER,
    _CAUSE_HOOK_NOT_RUN,
    _CAUSE_UNREADABLE,
    _CAUSE_KEY_NOT_IN_ROW,
)

_LOG_FILE_NAME = ".session-config-keys-log.jsonl"
# docs/session-config-keys-log.md § "Retention": a row for a harness-shaped session id is under this bound.
_LOG_LINE_MAX_BYTES = 2048
# One `readline` limit: the line bound plus its newline, so a line of exactly the bound is read whole.
_LOG_LINE_READ_LIMIT = _LOG_LINE_MAX_BYTES + 1
# docs/session-config-keys-log.md § "Joining a row to its transcript" and § "Rows" give the grammars.
_LOG_SESSION_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]+")
_LOG_KEY_NAME_PATTERN = re.compile(r"[a-z0-9_]+")
_LOG_TOKEN_PATTERN = re.compile(r"[a-z0-9_-]+")
_LOG_LOGGED_AT_PATTERN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
# docs/session-config-keys-log.md § "Coverage": the basename of the recorder in a hook attachment's `command`.
_RECORDER_HOOK_BASENAME = "record-session-config-keys.sh"
# The recorder's path is fixed whatever CLAUDE_CONFIG_DIR is, so README's `ls` check form does not apply to it.
_RECORDER_HOOK_PATH = f"~/.claude/hooks/{_RECORDER_HOOK_BASENAME}"
_HOOK_SUCCESS_ATTACHMENT_TYPE = "hook_success"

_CONFIG_READER_NOTICE = (
    "routing-guard: the config reader reported a problem; its message is withheld because it can name a path"
)
_LOOPS_ONLY_NOTE = (
    "the file holds loops and no key states; delete it and capture again while the branch's sessions are inside retention"
)
_LOG_UNUSABLE_NOTICE = (
    f"{_COMMAND}: the session config-keys log is not a regular file this account owns, or cannot be read;"
    " no key state was derived"
)
_HOOK_NOT_RUN_ON_ACCOUNT_LINE = (
    f"{_COMMAND}: the session config-keys recorder has not run on this account; run the `ls` check in README.md"
    f' § "Updating after git pull" on {_RECORDER_HOOK_PATH}'
)
_NO_KEY_OFF_ROW_LINE = (
    f"{_COMMAND}: no row in the session config-keys log shows the routing key off,"
    " so the key-off arm cannot be filled from existing branches"
)
_SKIPPED_LOG_LINES_LINE = f"{_COMMAND}: some lines of the session config-keys log were skipped as invalid"
_FUTURE_SESSIONS_ONLY = "a fix helps future sessions only, so list the branch `excluded` for this run"
# What the deny notice says in place of `_CAUSE_ROUND_UNTIMED`, whose next-step line explains a round, not a deny.
_DENY_UNTIMED_PHRASE = "the deny has no parseable time or no session id"
_NEXT_STEP_LINES = {
    _CAUSE_BEFORE_RECORDER: (
        f"{_COMMAND}: next step, {_CAUSE_BEFORE_RECORDER}: only a branch worked after the log began can be captured;"
        " list the branch `excluded`"
    ),
    _CAUSE_HOOK_NOT_RUN: (
        f"{_COMMAND}: next step, {_CAUSE_HOOK_NOT_RUN}: run the `ls` check in README.md"
        f' § "Updating after git pull" on {_RECORDER_HOOK_PATH}, then read the usual causes in'
        ' docs/session-config-keys-log.md § "Coverage"; a session that began before the log did can read'
        f" {_CAUSE_HOOK_NOT_RUN} too, and either way {_FUTURE_SESSIONS_ONLY}"
    ),
    _CAUSE_LOST_EVENT: (
        f"{_COMMAND}: next step, {_CAUSE_LOST_EVENT}: run the checks in docs/session-config-keys-log.md"
        f' § "Notice"; {_FUTURE_SESSIONS_ONLY}'
    ),
    _CAUSE_LOG_UNUSABLE: (
        f"{_COMMAND}: next step, {_CAUSE_LOG_UNUSABLE}: check that the log is a regular file the account owns"
        " and can be read, then capture again"
    ),
    _CAUSE_ROUND_UNTIMED: (
        f"{_COMMAND}: next step, {_CAUSE_ROUND_UNTIMED}: a round has no parseable time or no session id;"
        " list the branch `excluded`"
    ),
    **{
        reason: f"{_COMMAND}: next step, {reason}: list the branch `excluded`"
        for reason in (_CAUSE_UNREADABLE, _CAUSE_KEY_NOT_IN_ROW, _REASON_KEY_STATE_MIXED, _REASON_KEY_STATE_CONTRADICTED)
    },
}
_NEXT_ACTION_NO_MERGED_ROW = f"{_COMMAND}: next: wait for a post-recorder branch to merge, then list it `{_STATUS_MERGED}`"
_NEXT_ACTION_ACT_ON_LINES = (
    f"{_COMMAND}: next: act on the lines above, and list a row `{_STATUS_EXCLUDED}` when its cause is permanent for that branch"
)
_PRICING_DRIFT_REMEDY = (
    "resolve the usage drift in pricing, then delete the capture file of every row published by a run that printed"
    " the PRICING INTEGRITY line and capture those rows again while their sessions are inside retention,"
    ' before running compute; see docs/transcript-analysis.md routing-guard "The captures directory"'
)
_NEXT_ACTION_RESOLVE_PRICING_DRIFT = f"{_COMMAND}: next: {_PRICING_DRIFT_REMEDY}"
_NEXT_ACTION_ACT_ON_LINES_AND_RESOLVE_DRIFT = f"{_NEXT_ACTION_ACT_ON_LINES}; and also: {_PRICING_DRIFT_REMEDY}"
_NEXT_ACTION_CAPTURE_AGAIN = (
    f"{_COMMAND}: next: capture again after the last sampled branch's sessions end,"
    " then run compute once the note holds enough key-on files and each account has as many key-off files as key-on"
)
_PERMISSIVE_NOTE_NOTICE = "routing-guard: the note has group or other permissions; restrict it to the owner"
_COMPUTE_CONFIG_DIR_MESSAGE = (
    "routing-guard: compute reads no transcripts and takes no top-level --config-dir;"
    " the note names each config directory"
)
_OUT_WITH_CAPTURE_MESSAGE = "routing-guard: --out belongs to compute; drop --capture or drop --out"
_EMPTY_OUT_MESSAGE = "routing-guard: --out is empty; pass a new path (compute only) or drop --out"
_UNEXPECTED_ERROR_MESSAGE = "routing-guard: unexpected error; no traceback is printed because it can name a path or a branch"
_PRICING_INTEGRITY_LINE = (
    "routing-guard: PRICING INTEGRITY -- the transcript usage format may have drifted, so a captured row may be wrong;"
    " this line reports usage drift only;"
    " read this run's stderr for the diagnostic"
)

# Every NOT RUN reason is fixed and digit-free.
_REASON_TOO_FEW_ON = "fewer key-on branches than the guard's minimum"
_REASON_ACCOUNT_NOT_READY = (
    "an account with merged branches has its `pr_cost_recording` key off, a config fault, or no captures directory"
)
_REASON_FILE_MISSING = "a listed branch has no capture file at the current revision"
_REASON_FILE_UNUSABLE = "a listed branch's capture file cannot be read or fails validation"
_REASON_MIXED_PILOTS = "an account's key-on branches mix pilot states, so no reference state exists"
_REASON_OFF_PILOT_DIFFERS = (
    "a key-off branch was worked under a different pilot state than its account's key-on branches"
)
_REASON_ARM_SIZE_MISMATCH = "an account lists a different number of key-off branches than key-on branches"
_REASON_NO_GROWTH_DEFINED = "an arm has no loop whose growth is defined"
_REASON_NON_FINITE = "the point net or its lower bound is not finite"
_NOT_RUN_REASONS = (
    _REASON_TOO_FEW_ON,
    _REASON_ACCOUNT_NOT_READY,
    _REASON_FILE_MISSING,
    _REASON_FILE_UNUSABLE,
    _REASON_MIXED_PILOTS,
    _REASON_OFF_PILOT_DIFFERS,
    _REASON_ARM_SIZE_MISMATCH,
    _REASON_NO_GROWTH_DEFINED,
    _REASON_NON_FINITE,
)

_STATUS_PASSED = f"{_COMMAND}: PASSED"
_STATUS_FAILED = f"{_COMMAND}: FAILED"
_STALE_PRICING_LINE = f"{_COMMAND}: STALE PRICING -- a model priced here is past its rate re-verify-by date"
_OUT_PUBLISHED_NOTICE = (
    f"{_COMMAND}: the --out file is DO NOT PUBLISH; read it only outside an agent session,"
    " and delete it once its figures are read"
)
_DEGRADED_NOTICES = {
    arm: (
        f"{_COMMAND}: some key-{arm} loops are degraded:"
        " a dispatch transcript was lost, a turn had malformed usage or a model has no price"
    )
    for arm in (_STATE_ON, _STATE_OFF)
}
_UNDEFINED_REPLICATE_NOTICES = {
    arm: f"{_COMMAND}: some bootstrap replicates left the key-{arm} arm with no loop whose growth is defined"
    for arm in (_STATE_ON, _STATE_OFF)
}

# The speed and inference_geo values pricing._pricing_variant maps back to each label.
_VARIANT_USAGE_FIELDS: dict[str, dict[str, str]] = {
    "standard": {},
    "fast": {"speed": "fast"},
    "us_geo": {"inference_geo": "us"},
    "fast_us_geo": {"speed": "fast", "inference_geo": "us"},
}


class _NoteRefusal(Exception):
    """A refused note row. The message names the note line and never echoes a cell or a path."""

    def __init__(self, line_no: int, reason: str):
        super().__init__(f"{_COMMAND}: note line {line_no}: {reason}")


class _NoteRow(NamedTuple):
    line_no: int
    resolved_config_dir: Path
    branch: str
    status: str


class _InvalidCapture(Exception):
    """A capture file's bytes failed the validator. `reason` is fixed text that never echoes a cell."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class _OptInOutcome(enum.Enum):
    OPTED_IN = "opted in"
    NOT_OPTED_IN = "not opted in"
    SCHEMA_EMPTY = "schema empty"
    ROW_TRUNCATED = "row truncated"
    UNKNOWN_KEY = "unknown key"
    SCHEMA_UNREADABLE = "schema unreadable"
    CONFIG_DIR_UNRESOLVED = "config dir unresolved"


class _StandingFile(enum.Enum):
    """A captures-directory entry that gives no usable record: absent, or present but unreadable or invalid."""

    ABSENT = "absent"
    UNREADABLE = "unreadable"
    INVALID = "invalid"
    LOOPS_ONLY = "loops only"


class _LogStatus(enum.Enum):
    ABSENT = "absent"
    READ = "read"
    UNUSABLE = "unusable"


class _RoundFacts(NamedTuple):
    """The derived facts one code-review round contributes. Never a raw record."""

    session_key: str
    session_id: str | None
    open_idx: int
    open_ts: float | None
    has_marker_write: bool
    context_at_open: int | None
    context_at_window_end: int | None
    later_turns: dict[str, int]
    overhead_tokens: dict[str, dict[str, dict[str, int]]]
    degraded: bool
    denied_open: bool
    spawn_open: bool


class _Loop(NamedTuple):
    overhead_tokens: dict[str, dict[str, dict[str, int]]]
    growth_tokens: int | None
    later_turns: dict[str, int]
    degraded: bool
    round_count: int
    joined_denied_opens: int


class _KeyStates(NamedTuple):
    """A branch's derived state for each key `_STATE_KEYS` names, each `on` or `off`. Stored in the capture file."""

    routing: str
    round_consult_round2_pilot: str
    architect_fix_plan_pilot: str


# The short name each state carries in the per-row line.
_STATE_LABELS = _KeyStates(
    routing="routing", round_consult_round2_pilot="consult-pilot", architect_fix_plan_pilot="fix-plan-pilot",
)


class _CaptureRecord(NamedTuple):
    loops: list[dict]
    states: _KeyStates


class _BuiltRow(NamedTuple):
    text: str
    loops: list[_Loop]
    states: _KeyStates


class _KeyState(NamedTuple):
    """One key's state at one instant: `state` is `on` or `off`, else None and `cause` names why it is unknown."""

    state: str | None
    cause: str | None


class _BranchKeyStates(NamedTuple):
    by_key: dict[str, str]
    causes: tuple[str, ...]


class _WholeFailure(NamedTuple):
    """Why a branch cannot be captured whole. `next_step_classes` are the `_NEXT_STEP_LINES` keys the reason calls for."""

    reason: str
    next_step_classes: frozenset[str]


class _DenyFact(NamedTuple):
    branch: str
    ts: float | None
    session_id: str | None


class _DenyReconciliation(NamedTuple):
    refusals: list[str]
    notices: list[str]
    contradicted_line_nos: set[int]


class _LogRow(NamedTuple):
    logged_at: float
    keys: dict[str, str]


@dataclass
class _SessionConfigKeysLog:
    status: _LogStatus
    rows_by_session: dict[str, list[_LogRow]] = field(default_factory=dict)
    earliest_logged_at: float | None = None
    routing_off_seen: bool = False
    skipped_lines: bool = False


@dataclass
class _RecorderEvents:
    earliest_attachment_time: float | None = None
    lost_event_times: list[float] = field(default_factory=list)


@dataclass
class _BranchFacts:
    has_timestamped_record: bool = False
    first_record_ts: float | None = None
    orchestrator_unreadable: bool = False


@dataclass
class _ScanFacts:
    branches: dict[str, _BranchFacts] = field(default_factory=dict)
    rounds_by_branch: dict[str, list[_RoundFacts]] = field(default_factory=dict)
    denies: list[_DenyFact] = field(default_factory=list)
    seen_deny_ids: set[str] = field(default_factory=set)
    recorder_events: dict[str, _RecorderEvents] = field(default_factory=dict)
    dropped_deny_without_tool_use: bool = False


class _CaptureScope(NamedTuple):
    slugs: list[str]
    project_dir_count: int


def _refuse(message: str, exit_code: int) -> NoReturn:
    print(message, file=sys.stderr)
    sys.exit(exit_code)


# --- the note ---------------------------------------------------------------


def _expand_note_config_dir(cell: str, line_no: int) -> Path:
    if cell.startswith("~/"):
        home = os.environ.get("HOME")
        if not home or not os.path.isabs(home):
            raise _NoteRefusal(line_no, "'~/' needs an absolute HOME")
        return Path(home) / cell[2:]
    if not os.path.isabs(cell):
        raise _NoteRefusal(line_no, "the config directory is not absolute")
    return Path(cell)


def _resolve_note_config_dir(config_dir: Path, line_no: int) -> Path:
    try:
        return config_dir.resolve()
    except (OSError, ValueError, RuntimeError):
        raise _NoteRefusal(line_no, "the config directory cannot be resolved") from None


def _parse_note(text: str) -> list[_NoteRow]:
    rows: list[_NoteRow] = []
    seen_branches: set[str] = set()
    for line_no, raw_line in enumerate(text.split("\n"), start=1):
        line = raw_line.removesuffix("\r")
        if not line or line.startswith("#"):
            continue
        cells = line.split("\t")
        if len(cells) != _NOTE_COLUMN_COUNT:
            raise _NoteRefusal(line_no, _WRONG_COLUMN_COUNT_REASON)
        config_cell, branch, status = cells
        config_dir = _expand_note_config_dir(config_cell, line_no)
        resolved_config_dir = _resolve_note_config_dir(config_dir, line_no)
        if not branch or branch != branch.strip():
            raise _NoteRefusal(line_no, "unknown branch value")
        if status not in _NOTE_STATUSES:
            raise _NoteRefusal(line_no, _UNKNOWN_STATUS_REASON)
        if branch in scope._DEFAULT_BRANCH_CANDIDATES:
            raise _NoteRefusal(line_no, "the branch is a default-branch name")
        if branch in seen_branches:
            raise _NoteRefusal(line_no, "the branch is listed twice")
        seen_branches.add(branch)
        rows.append(_NoteRow(line_no, resolved_config_dir, branch, status))
    return rows


def _load_note_rows(note_arg: str) -> list[_NoteRow]:
    if not os.path.isabs(note_arg):
        _refuse(f"{_COMMAND}: --note must be an absolute path", 2)
    note_path = Path(note_arg)
    try:
        resolved_note_path = note_path.resolve()
    except (OSError, ValueError, RuntimeError):
        _refuse(f"{_COMMAND}: --note cannot be resolved to a path", 2)
    if ledger_common._ledger_path_is_git_tracked(resolved_note_path, _COMMAND):
        _refuse(f"{_COMMAND}: --note is inside a git working tree; keep the note outside any git working tree", 2)
    try:
        text = note_path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        _refuse(f"{_COMMAND}: the note could not be read as a UTF-8 text file", 2)
    with contextlib.suppress(OSError):
        if note_path.stat().st_mode & 0o077:
            print(_PERMISSIVE_NOTE_NOTICE, file=sys.stderr)
    try:
        return _parse_note(text)
    except _NoteRefusal as refusal:
        _refuse(str(refusal), 2)


# --- the opt-in --------------------------------------------------------------


def _read_opt_in(config_dir: Path) -> _OptInOutcome:
    try:
        enabled = _config.config_enabled("pr_cost_recording", config_dir_override=config_dir)
    except _config.ConfigSchemaEmptyError:
        return _OptInOutcome.SCHEMA_EMPTY
    except _config.ConfigSchemaRowTruncatedError:
        return _OptInOutcome.ROW_TRUNCATED
    except KeyError:
        # A parsed schema means the key literal is unknown; an empty one means the file was unreadable.
        return _OptInOutcome.UNKNOWN_KEY if _config.schema() else _OptInOutcome.SCHEMA_UNREADABLE
    if enabled is None:
        return _OptInOutcome.CONFIG_DIR_UNRESOLVED
    return _OptInOutcome.OPTED_IN if enabled else _OptInOutcome.NOT_OPTED_IN


def _classify_capture_opt_in(config_dir: Path) -> _OptInOutcome:
    """`_config` prints the config file's path and part of a rejected line to stderr, so one fixed notice stands in for it."""
    reader_stderr = io.StringIO()
    with contextlib.redirect_stderr(reader_stderr):
        outcome = _read_opt_in(config_dir)
    if reader_stderr.getvalue():
        print(_CONFIG_READER_NOTICE, file=sys.stderr)
    return outcome


_OPT_IN_REFUSALS: dict[_OptInOutcome, str] = {
    _OptInOutcome.NOT_OPTED_IN: f"{_COMMAND}: --capture is not opted in (pr_cost_recording) -- see docs/pr-cost.md",
    _OptInOutcome.SCHEMA_EMPTY: (
        f"{_COMMAND}: --capture found config-keys.psv empty or malformed (no parseable schema rows) -- see docs/pr-cost.md"
    ),
    _OptInOutcome.ROW_TRUNCATED: (
        f"{_COMMAND}: --capture found pr_cost_recording's config-keys.psv row truncated"
        " (partial stow-relink or interrupted git pull) -- see docs/pr-cost.md"
    ),
    _OptInOutcome.UNKNOWN_KEY: f"{_COMMAND}: --capture: unknown config key pr_cost_recording",
    _OptInOutcome.SCHEMA_UNREADABLE: (
        f"{_COMMAND}: --capture could not read config-keys.psv (partial stow-relink or interrupted git pull)"
        " -- see docs/pr-cost.md"
    ),
    _OptInOutcome.CONFIG_DIR_UNRESOLVED: (
        f"{_COMMAND}: --capture could not resolve the Claude Code config directory -- see docs/pr-cost.md"
    ),
}


# --- capture files -----------------------------------------------------------


def _branch_digest(branch: str) -> str:
    return hashlib.sha256(branch.encode("utf-8")).hexdigest()


def _capture_file_name(branch: str, revision: int) -> str:
    """The digest hides the branch only from a reader who does not already suspect it."""
    return f"{_branch_digest(branch)}.r{revision}.json"


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    if len({key for key, _value in pairs}) != len(pairs):
        raise ValueError("duplicate key")
    return dict(pairs)


def _reject_non_finite_constant(_constant: str) -> NoReturn:
    raise ValueError("non-finite number")


def _is_count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


_REASON_NOT_AN_OBJECT = "the file is not a JSON object"
_REASON_LOOPS_ONLY = "the file holds loops and no key states"
_REASON_WRONG_KEYS = "the file does not hold exactly the loops and the three key states"
_REASON_KEY_STATE_VALUE = "a key state is neither on nor off"


def _validate_overhead_tokens(overhead_tokens: object) -> None:
    if not isinstance(overhead_tokens, dict):
        raise _InvalidCapture("overhead_tokens is not an object")
    for by_variant in overhead_tokens.values():
        if not isinstance(by_variant, dict):
            raise _InvalidCapture("a model entry is not an object")
        for variant, by_class in by_variant.items():
            if variant not in pricing._PRICING_VARIANTS:
                raise _InvalidCapture("unknown pricing variant")
            if not isinstance(by_class, dict):
                raise _InvalidCapture("a variant entry is not an object")
            for token_class, tokens in by_class.items():
                if token_class not in pricing._TOKEN_CLASSES:
                    raise _InvalidCapture("unknown token class")
                if not _is_count(tokens):
                    raise _InvalidCapture("a token count is not a non-negative integer")


def _validate_loop(loop: object) -> None:
    if not isinstance(loop, dict) or set(loop) != {"overhead_tokens", "growth_tokens", "later_turns", "degraded"}:
        raise _InvalidCapture("a loop does not hold exactly the four loop keys")
    _validate_overhead_tokens(loop["overhead_tokens"])
    growth_tokens = loop["growth_tokens"]
    if growth_tokens is not None and (not isinstance(growth_tokens, int) or isinstance(growth_tokens, bool)):
        raise _InvalidCapture("growth_tokens is neither an integer nor null")
    later_turns = loop["later_turns"]
    if not isinstance(later_turns, dict) or not all(_is_count(count) for count in later_turns.values()):
        raise _InvalidCapture("later_turns is not an object of non-negative integers")
    if not isinstance(loop["degraded"], bool):
        raise _InvalidCapture("degraded is not a boolean")


def _parse_capture_bytes(data: bytes) -> _CaptureRecord:
    """Shared by every reader and writer of a capture file, so a file is checked the same way on every path."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise _InvalidCapture("not UTF-8") from None
    try:
        parsed = json.loads(text, object_pairs_hook=_reject_duplicate_keys, parse_constant=_reject_non_finite_constant)
    except (ValueError, RecursionError):
        # ValueError also covers an integer past Python's digit limit.
        raise _InvalidCapture("not valid JSON") from None
    if not isinstance(parsed, dict):
        raise _InvalidCapture(_REASON_NOT_AN_OBJECT)
    if set(parsed) == {"loops"}:
        raise _InvalidCapture(_REASON_LOOPS_ONLY)
    if set(parsed) != {"loops", *_KeyStates._fields}:
        raise _InvalidCapture(_REASON_WRONG_KEYS)
    for field_name in _KeyStates._fields:
        if parsed[field_name] not in (_STATE_ON, _STATE_OFF):
            raise _InvalidCapture(_REASON_KEY_STATE_VALUE)
    loops = parsed["loops"]
    if not isinstance(loops, list) or not loops:
        raise _InvalidCapture("loops is not a non-empty list")
    for loop in loops:
        _validate_loop(loop)
    return _CaptureRecord(loops, _KeyStates(*(parsed[field_name] for field_name in _KeyStates._fields)))


def _serialize_capture(loops: Sequence[_Loop], states: _KeyStates) -> str:
    """ASCII-only, so the text encodes the same under any locale."""
    return json.dumps(
        {
            "loops": [
                {
                    "overhead_tokens": loop.overhead_tokens,
                    "growth_tokens": loop.growth_tokens,
                    "later_turns": loop.later_turns,
                    "degraded": loop.degraded,
                }
                for loop in loops
            ],
            **states._asdict(),
        },
        sort_keys=True, allow_nan=False,
    )


def _read_standing_capture(path: Path) -> bytes | _StandingFile:
    # This tool is the capture store's only intended writer, so unlike _read_session_config_keys_log this open sets no
    # O_NONBLOCK and makes no regular-file or owner check.
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return _StandingFile.ABSENT
    except OSError:
        return _StandingFile.UNREADABLE
    try:
        with os.fdopen(fd, "rb") as handle:
            return handle.read()
    except OSError:
        return _StandingFile.UNREADABLE


def _load_capture_file(path: Path) -> _CaptureRecord | _StandingFile:
    standing = _read_standing_capture(path)
    if isinstance(standing, _StandingFile):
        return standing
    try:
        return _parse_capture_bytes(standing)
    except _InvalidCapture as invalid:
        return _StandingFile.LOOPS_ONLY if invalid.reason == _REASON_LOOPS_ONLY else _StandingFile.INVALID


# --- the session config-keys log -----------------------------------------------
#
# docs/session-config-keys-log.md states the reader rules the functions below apply.


def _discard_rest_of_line(handle, remaining: int) -> int:
    """Drop the rest of an over-long line without holding it. Returns the bytes still allowed."""
    while remaining > 0:
        chunk = handle.readline(min(_LOG_LINE_READ_LIMIT, remaining))
        remaining -= len(chunk)
        if not chunk or chunk.endswith(b"\n"):
            break
    return remaining


def _is_valid_log_token(value: object, pattern: re.Pattern[str]) -> bool:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _valid_log_row(parsed: object) -> tuple[str, _LogRow] | None:
    if not isinstance(parsed, dict):
        return None
    session_id = parsed.get("session_id")
    keys = parsed.get("keys")
    if (
        not _is_valid_log_token(session_id, _LOG_SESSION_ID_PATTERN)
        or not _is_valid_log_token(parsed.get("source"), _LOG_TOKEN_PATTERN)
        or not _is_valid_log_token(parsed.get("logged_at"), _LOG_LOGGED_AT_PATTERN)
        or not isinstance(keys, dict)
    ):
        return None
    if not all(
        _is_valid_log_token(name, _LOG_KEY_NAME_PATTERN) and _is_valid_log_token(value, _LOG_TOKEN_PATTERN)
        for name, value in keys.items()
    ):
        return None
    logged_at = corpus._parse_ts(parsed["logged_at"])
    if logged_at is None:
        return None
    return session_id, _LogRow(logged_at, keys)


def _parse_log_line(line: bytes) -> tuple[str, _LogRow] | None:
    try:
        parsed = json.loads(line.decode("utf-8"))
    except (ValueError, RecursionError, TypeError):
        return None
    return _valid_log_row(parsed)


def _read_session_config_keys_log(path: Path, wanted_session_ids: Collection[str]) -> _SessionConfigKeysLog:
    """A blank line is no row and is not counted as skipped, because the recorder starts every append with one.
    The log has a writer other than this tool, the recorder hook, so its file type and content are not this tool's to assume.
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return _SessionConfigKeysLog(_LogStatus.ABSENT)
    except OSError:
        return _SessionConfigKeysLog(_LogStatus.UNUSABLE)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
            return _SessionConfigKeysLog(_LogStatus.UNUSABLE)
        log = _SessionConfigKeysLog(_LogStatus.READ)
        remaining = info.st_size
        with os.fdopen(fd, "rb", closefd=False) as handle:
            while remaining > 0:
                line = handle.readline(min(_LOG_LINE_READ_LIMIT, remaining))
                if not line:
                    break
                remaining -= len(line)
                if len(line) > _LOG_LINE_MAX_BYTES and not line.endswith(b"\n"):
                    log.skipped_lines = True
                    remaining = _discard_rest_of_line(handle, remaining)
                    continue
                if not line.strip():
                    continue
                found = _parse_log_line(line)
                if found is None:
                    log.skipped_lines = True
                    continue
                session_id, row = found
                if log.earliest_logged_at is None or row.logged_at < log.earliest_logged_at:
                    log.earliest_logged_at = row.logged_at
                if row.keys.get(_ROUTING_KEY) == _LOG_VALUE_OFF:
                    log.routing_off_seen = True
                # Rows per scanned session are not capped: the recorder adds a row per startup, resume, /clear and compaction.
                if session_id in wanted_session_ids:
                    log.rows_by_session.setdefault(session_id, []).append(row)
        return log
    except OSError:
        return _SessionConfigKeysLog(_LogStatus.UNUSABLE)
    finally:
        os.close(fd)


def _recorder_attachment(rec: dict) -> tuple[str, float, bool] | None:
    """(session id, time, whether it is a lost event) for a main-thread recorder hook attachment, else None.

    docs/session-config-keys-log.md § "Coverage" defines a lost event as such an attachment with a non-empty `stdout`.
    """
    if rec.get("type") != "attachment" or rec.get("isSidechain"):
        return None
    attachment = rec.get("attachment")
    if not isinstance(attachment, dict) or attachment.get("type") != _HOOK_SUCCESS_ATTACHMENT_TYPE:
        return None
    command = attachment.get("command")
    if not isinstance(command, str) or command.rsplit("/", 1)[-1] != _RECORDER_HOOK_BASENAME:
        return None
    session_id, stdout, timestamp = rec.get("sessionId"), attachment.get("stdout"), rec.get("timestamp")
    if not (isinstance(session_id, str) and session_id and isinstance(stdout, str) and isinstance(timestamp, str)):
        return None
    ts = corpus._parse_ts(timestamp)
    if ts is None:
        return None
    return session_id, ts, stdout != ""


def _coverage_start(log: _SessionConfigKeysLog, recorder_events: Mapping[str, _RecorderEvents]) -> float | None:
    """The reader doc's coverage start, narrowed to this repo's sessions when it falls back to attachments."""
    if log.earliest_logged_at is not None:
        return log.earliest_logged_at
    return _earliest_ts(events.earliest_attachment_time for events in recorder_events.values())


def _lost_event_hides(round_ts: float, session_rows: Sequence[_LogRow], lost_event_times: Sequence[float]) -> bool:
    """`logged_at` is floored, so a row follows an event only past the event's whole second."""
    return any(
        not any(math.floor(event_ts) < row.logged_at <= round_ts for row in session_rows)
        for event_ts in lost_event_times
        if event_ts <= round_ts
    )


class _RoundRow(NamedTuple):
    """What the log gives every key at one round: the row taken, else the cause that leaves every key unknown."""

    row: _LogRow | None
    cause: str | None


def _round_row_at(
    session_id: str | None, at_ts: float | None, log: _SessionConfigKeysLog,
    recorder_events: Mapping[str, _RecorderEvents], coverage_start: float | None,
) -> _RoundRow:
    if at_ts is None or not session_id:
        return _RoundRow(None, _CAUSE_ROUND_UNTIMED)
    if log.status is _LogStatus.UNUSABLE:
        return _RoundRow(None, _CAUSE_LOG_UNUSABLE)
    session_rows = log.rows_by_session.get(session_id, [])
    events = recorder_events.get(session_id)
    if events is not None and _lost_event_hides(at_ts, session_rows, events.lost_event_times):
        return _RoundRow(None, _CAUSE_LOST_EVENT)
    taken: _LogRow | None = None
    for row in session_rows:
        if row.logged_at <= at_ts and (taken is None or row.logged_at >= taken.logged_at):
            taken = row
    if taken is None:
        if coverage_start is not None and at_ts < coverage_start:
            return _RoundRow(None, _CAUSE_BEFORE_RECORDER)
        return _RoundRow(None, _CAUSE_HOOK_NOT_RUN)
    return _RoundRow(taken, None)


def _key_state_in_round_row(key: str, round_row: _RoundRow) -> _KeyState:
    if round_row.row is None:
        return _KeyState(None, round_row.cause)
    value = round_row.row.keys.get(key)
    if value is None:
        return _KeyState(None, _CAUSE_KEY_NOT_IN_ROW)
    if value == _LOG_VALUE_UNREADABLE:
        return _KeyState(None, _CAUSE_UNREADABLE)
    return _KeyState(_STATE_OFF if value == _LOG_VALUE_OFF else _STATE_ON, None)


def _key_state_at(
    key: str, session_id: str | None, at_ts: float | None, log: _SessionConfigKeysLog,
    recorder_events: Mapping[str, _RecorderEvents], coverage_start: float | None,
) -> _KeyState:
    return _key_state_in_round_row(key, _round_row_at(session_id, at_ts, log, recorder_events, coverage_start))


def _branch_key_states(
    rounds: Sequence[_RoundFacts], log: _SessionConfigKeysLog, recorder_events: Mapping[str, _RecorderEvents],
    coverage_start: float | None,
) -> _BranchKeyStates:
    round_rows = [
        _round_row_at(round_facts.session_id, round_facts.open_ts, log, recorder_events, coverage_start)
        for round_facts in rounds
    ]
    by_key: dict[str, str] = {}
    causes: set[str] = set()
    for key in _STATE_KEYS:
        results = [_key_state_in_round_row(key, round_row) for round_row in round_rows]
        key_causes = {result.cause for result in results if result.cause is not None}
        if key_causes:
            by_key[key] = _STATE_UNKNOWN
            causes |= key_causes
            continue
        states = {result.state for result in results}
        by_key[key] = _STATE_MIXED if len(states) > 1 else next(iter(states), _STATE_UNKNOWN)
    return _BranchKeyStates(by_key, tuple(cause for cause in _CAUSES if cause in causes))


# --- scanning ----------------------------------------------------------------


def _capture_scope(root: Path, scan_gaps: Counter[str]) -> _CaptureScope:
    """The slugs come from `git worktree list` in the cwd, so a cwd outside this checkout's worktrees would read
    that repo's transcripts.
    """
    slugs = scope._repo_scoped_project_slugs(_COMMAND)
    if scope._path_to_project_slug(str(_CHECKOUT_ROOT)) not in slugs:
        _refuse(
            f"{_COMMAND}: --capture must run from a worktree of the checkout holding this script;"
            " run it from a worktree of that checkout",
            1,
        )
    orphan_prefix = slugs[0] + _WORKTREE_SLUG_INFIX
    on_root = scope._list_dir_recording_gaps(root, scan_gaps, scope._SCAN_GAP_ROOT)
    orphan_slugs = [entry.name for entry in on_root if entry.name.startswith(orphan_prefix) and entry.name != orphan_prefix]
    scoped_slugs = list(dict.fromkeys([*slugs, *orphan_slugs]))
    wanted = set(scoped_slugs)
    return _CaptureScope(scoped_slugs, sum(1 for entry in on_root if entry.name in wanted))


def _main_thread_turns(records: list[dict]) -> list[tuple[int, str, int]]:
    """(record index, model, context) per main-thread turn, skipping invalid usage and `<synthetic>` records."""
    turns: list[tuple[int, str, int]] = []
    for idx, rec in enumerate(records):
        if rec.get("type") != "assistant" or rec.get("isSidechain"):
            continue
        usage = (rec.get("message") or {}).get("usage")
        if not usage or session_pricing._usage_is_invalid(usage):
            continue
        model = review_rounds._turn_model(rec)
        if model == pricing._SYNTHETIC_MODEL_ID:
            continue
        turns.append((idx, model, pricing._context_at_turn(usage)))
    return turns


def _hook_denials_in_record(rec: dict) -> Iterable[tuple[str, str, str]]:
    """(tool_use_id, hook name, message) for each hook denial in `rec`, in both transcript shapes."""
    if rec.get("type") == "attachment":
        denial = denials.hook_denial_key(rec)
        if denial is None:
            return
        tool_use_id, attachment = denial
        normalized = denials._normalize_blocking_error(attachment.get("blockingError"))
        if isinstance(normalized, dict):
            message = normalized.get("blockingError") or normalized.get("message") or str(normalized)
        else:
            message = str(normalized) if normalized else ""
        hook_name = attachment.get("hookName")
        yield tool_use_id, hook_name if isinstance(hook_name, str) else "", message if isinstance(message, str) else ""
    elif rec.get("type") == "user":
        for block in (rec.get("message") or {}).get("content") or []:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            denial = denials.hook_denial_key(block)
            if denial is not None:
                denial_text = denial[1]
                yield denial[0], "", denial_text if isinstance(denial_text, str) else ""


def _main_thread_tool_use_ids(records: list[dict]) -> tuple[set[str], set[str]]:
    """(the ids of this session's main-thread tool_use blocks, those among them that are `Skill(code-review)` calls)."""
    tool_use_ids: set[str] = set()
    code_review_skill_ids: set[str] = set()
    for rec in records:
        if rec.get("isSidechain") or rec.get("type") != "assistant":
            continue
        for block in review_rounds._assistant_content_blocks(rec):
            if not (
                isinstance(block, dict) and block.get("type") == "tool_use"
                and isinstance(block.get("id"), str) and block["id"]
            ):
                continue
            tool_use_ids.add(block["id"])
            if review_rounds._skill_block_round_skill([block]) == author_outcome._CODE_REVIEW_SKILL:
                code_review_skill_ids.add(block["id"])
    return tool_use_ids, code_review_skill_ids


def _record_session_id(rec: dict) -> str | None:
    """The record's `sessionId` is the join key to the session config-keys log."""
    session_id = rec.get("sessionId")
    return session_id if isinstance(session_id, str) and session_id else None


def _record_routing_denies(scan: _ScanFacts, records: list[dict], record_branches: list[str]) -> set[str]:
    """A deny identified only by its text signature counts only when its id joins a `Skill(code-review)` tool_use of
    the same session, so an ordinary tool error that carries the gate's wording does not.

    When that drops a deny whose id matches no tool_use of the session, `scan.dropped_deny_without_tool_use`
    is set, because the deny may be real with its tool_use elsewhere.
    """
    tool_use_ids, code_review_skill_ids = _main_thread_tool_use_ids(records)
    denied_tool_use_ids: set[str] = set()
    for idx, rec in enumerate(records):
        if rec.get("isSidechain"):
            continue
        for tool_use_id, hook_name, message in _hook_denials_in_record(rec):
            if denials._denial_hook_label(hook_name, message) != _ROUTING_DENY_LABEL:
                continue
            if not isinstance(tool_use_id, str):
                tool_use_id = ""
            if not hook_name.strip() and tool_use_id not in code_review_skill_ids:
                if tool_use_id not in tool_use_ids:
                    scan.dropped_deny_without_tool_use = True
                continue
            if tool_use_id:
                denied_tool_use_ids.add(tool_use_id)
                if tool_use_id in scan.seen_deny_ids:
                    continue
                scan.seen_deny_ids.add(tool_use_id)
            scan.denies.append(_DenyFact(
                record_branches[idx], corpus._parse_ts(rec.get("timestamp")), _record_session_id(rec),
            ))
    return denied_tool_use_ids


def _record_branch_activity(scan: _ScanFacts, records: list[dict], record_branches: list[str]) -> None:
    for idx, rec in enumerate(records):
        branch = record_branches[idx]
        if rec.get("isSidechain") or not branch:
            continue
        facts = scan.branches.setdefault(branch, _BranchFacts())
        ts = corpus._parse_ts(rec.get("timestamp"))
        if ts is None:
            continue
        facts.has_timestamped_record = True
        if facts.first_record_ts is None or ts < facts.first_record_ts:
            facts.first_record_ts = ts


def _record_recorder_events(scan: _ScanFacts, records: list[dict]) -> None:
    """Pool this session's recorder hook attachment times per `sessionId`, whichever file they came from."""
    for rec in records:
        found = _recorder_attachment(rec)
        if found is None:
            continue
        session_id, ts, is_lost_event = found
        events = scan.recorder_events.setdefault(session_id, _RecorderEvents())
        if events.earliest_attachment_time is None or ts < events.earliest_attachment_time:
            events.earliest_attachment_time = ts
        if is_lost_event:
            events.lost_event_times.append(ts)


def _opens_with_denied_skill(rec: dict, denied_tool_use_ids: Collection[str]) -> bool:
    return any(
        isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "Skill"
        and isinstance(block.get("id"), str) and block["id"] in denied_tool_use_ids
        for block in (rec.get("message") or {}).get("content") or []
    )


def _add_session_facts(scan: _ScanFacts, jsonl: Path, raw_records: list[dict]) -> None:
    """Nothing here keeps a reference to a record."""
    records = pricing.dedup_turns_by_request_id(raw_records)
    windows = review_rounds.detect_round_windows(records)
    record_branches = review_rounds._session_record_branches(records, windows)
    _record_branch_activity(scan, records, record_branches)
    _record_recorder_events(scan, records)
    denied_tool_use_ids = _record_routing_denies(scan, records, record_branches)
    code_review_spans = dict(author_outcome._code_review_rounds(records))
    if not code_review_spans:
        return

    succeeded_tool_use_ids = author_outcome._succeeded_tool_use_ids(records)
    round_entries = review_rounds._price_session_rounds(
        jsonl, records, windows, record_branches, None, defaultdict(float), collect_tokens=True,
    )
    unreadable_counter: Counter[str] = Counter()
    orchestrator_spawns = author_outcome._orchestrator_spawns(
        jsonl, records, list(code_review_spans.items()), author_outcome._AUTHORING_AGENT_CODE_WRITER, unreadable_counter,
    )
    turns = _main_thread_turns(records)
    turn_indices = [idx for idx, _model, _context in turns]
    window_opens = [open_idx for open_idx, _window_end, _skill in windows]
    window_ends = [window_end for _open_idx, window_end, _skill in windows]
    outside_turns = []
    for idx, model, _context in turns:
        position = bisect.bisect_right(window_opens, idx) - 1
        if position < 0 or idx >= window_ends[position]:
            outside_turns.append((idx, model))
    outside_indices = [idx for idx, _model in outside_turns]

    session_key = str(jsonl)
    round_branches: set[str] = set()
    for (open_idx, window_end, skill), entry in zip(windows, round_entries, strict=True):
        branch = entry["branch_key"][1]
        if skill != author_outcome._CODE_REVIEW_SKILL or not branch:
            continue
        round_branches.add(branch)
        open_turn = bisect.bisect_left(turn_indices, open_idx)
        end_turn = bisect.bisect_left(turn_indices, window_end)
        first_later = bisect.bisect_left(outside_indices, window_end)
        open_rec = records[open_idx]
        scan.rounds_by_branch.setdefault(branch, []).append(_RoundFacts(
            session_key=session_key,
            session_id=_record_session_id(open_rec),
            open_idx=open_idx,
            open_ts=corpus._parse_ts(entry["ts"]),
            has_marker_write=author_outcome._round_has_marker_write(
                records, open_idx, code_review_spans[open_idx], orchestrator_spawns,
                succeeded_tool_use_ids=succeeded_tool_use_ids,
            ),
            context_at_open=turns[open_turn][2] if open_turn < len(turns) else None,
            context_at_window_end=turns[end_turn][2] if end_turn < len(turns) else None,
            later_turns=dict(Counter(model for _idx, model in outside_turns[first_later:])),
            overhead_tokens=entry["tokens"],
            degraded=entry["dangling"] > 0 or entry["invalid_usage_records"] > 0,
            denied_open=_opens_with_denied_skill(open_rec, denied_tool_use_ids),
            spawn_open=review_rounds.round_opened_by_spawn(open_rec),
        ))
    if unreadable_counter[author_outcome._DQ_ORCHESTRATOR_TRANSCRIPT_UNREADABLE] > 0:
        for branch in round_branches:
            scan.branches.setdefault(branch, _BranchFacts()).orchestrator_unreadable = True


def _scan_sessions(root: Path, capture_scope: _CaptureScope, scan_gaps: Counter[str]) -> _ScanFacts:
    scan = _ScanFacts()
    announced_project_dirs: set[Path] = set()
    # include_subagents is False because subagent transcripts reach overhead_tokens through each round's dispatch
    # index, and are never scanned as sessions of their own.
    for jsonl, raw_records in scope._iter_scoped_sessions(capture_scope.slugs, False, [root], scan_gaps=scan_gaps):
        if jsonl.parent not in announced_project_dirs:
            announced_project_dirs.add(jsonl.parent)
            total = max(capture_scope.project_dir_count, len(announced_project_dirs))
            print(f"{_COMMAND}: scanning project directory {len(announced_project_dirs)} of {total}", file=sys.stderr)
        _add_session_facts(scan, jsonl, raw_records)
    return scan


# --- loops and the whole verdict ---------------------------------------------


def _whole_failure(
    branch_facts: _BranchFacts | None, rounds: Sequence[_RoundFacts], key_states: _BranchKeyStates,
) -> _WholeFailure | None:
    """Why a branch cannot be captured whole, or None when it can.

    `key-state-contradicted` is not decided here; the deny reconciliation produces it.
    """
    if branch_facts is None or not branch_facts.has_timestamped_record:
        return _WholeFailure(_REASON_NO_RECORDS, frozenset())
    if not any(round_facts.has_marker_write for round_facts in rounds):
        return _WholeFailure(_REASON_NO_COMPLETE_LOOP, frozenset())
    if branch_facts.orchestrator_unreadable:
        return _WholeFailure(_REASON_ORCHESTRATOR_UNREADABLE, frozenset())
    if _STATE_UNKNOWN in key_states.by_key.values():
        return _WholeFailure(
            f"{_REASON_KEY_STATE_UNKNOWN}: {', '.join(key_states.causes)}", frozenset(key_states.causes),
        )
    if _STATE_MIXED in key_states.by_key.values():
        return _WholeFailure(_REASON_KEY_STATE_MIXED, frozenset({_REASON_KEY_STATE_MIXED}))
    return None


def _round_sort_key(round_facts: _RoundFacts) -> tuple[float, str, int]:
    # Builds the same key as `compute_review_round_costs`' `sort_key` (review_rounds.py, the `entry["sort_key"]` assignment).
    open_ts = round_facts.open_ts
    return (open_ts if open_ts is not None else float("inf"), round_facts.session_key, round_facts.open_idx)


def _merge_overhead_tokens(rounds: Sequence[_RoundFacts]) -> dict[str, dict[str, dict[str, int]]]:
    merged: dict[str, dict[str, dict[str, int]]] = {}
    for round_facts in rounds:
        for model, by_variant in round_facts.overhead_tokens.items():
            for variant, by_class in by_variant.items():
                target = merged.setdefault(model, {}).setdefault(variant, {})
                for token_class, tokens in by_class.items():
                    target[token_class] = target.get(token_class, 0) + tokens
    return merged


def _loop_growth_tokens(first_round: _RoundFacts, last_round: _RoundFacts) -> int | None:
    if first_round.session_key != last_round.session_key:
        return None
    if first_round.context_at_open is None or last_round.context_at_window_end is None:
        return None
    return last_round.context_at_window_end - first_round.context_at_open


def _joined_denied_opens(rounds: Sequence[_RoundFacts]) -> int:
    return sum(
        1 for position, round_facts in enumerate(rounds)
        if round_facts.denied_open and any(later.spawn_open for later in rounds[position + 1:])
    )


def _loop_groups(rounds: Sequence[_RoundFacts]) -> list[list[_RoundFacts]]:
    groups: list[list[_RoundFacts]] = []
    open_loop: list[_RoundFacts] = []
    for round_facts in sorted(rounds, key=_round_sort_key):
        open_loop.append(round_facts)
        if round_facts.has_marker_write:
            groups.append(open_loop)
            open_loop = []
    return groups


def _rounds_in_loops(rounds: Sequence[_RoundFacts]) -> list[_RoundFacts]:
    return [round_facts for group in _loop_groups(rounds) for round_facts in group]


def _assemble_loops(rounds: Sequence[_RoundFacts]) -> list[_Loop]:
    return [
        _Loop(
            overhead_tokens=_merge_overhead_tokens(group),
            growth_tokens=_loop_growth_tokens(group[0], group[-1]),
            later_turns=dict(group[-1].later_turns),
            degraded=any(member.degraded for member in group),
            round_count=len(group),
            joined_denied_opens=_joined_denied_opens(group),
        )
        for group in _loop_groups(rounds)
    ]


def _build_row(rounds: Sequence[_RoundFacts], key_states: _BranchKeyStates) -> _BuiltRow:
    """The row for a branch whose key states are all `on` or `off`."""
    loops = _assemble_loops(rounds)
    states = _KeyStates(
        routing=key_states.by_key[_ROUTING_KEY],
        round_consult_round2_pilot=key_states.by_key[_ROUND2_PILOT_KEY],
        architect_fix_plan_pilot=key_states.by_key[_ARCHITECT_PILOT_KEY],
    )
    return _BuiltRow(_serialize_capture(loops, states), loops, states)


# --- deny reconciliation -----------------------------------------------------


def _utc_text(ts: float | None, pattern: str, unknown: str) -> str:
    if ts is None:
        return unknown
    try:
        return datetime.fromtimestamp(ts, tz=UTC).strftime(pattern)
    except (ValueError, OverflowError, OSError):
        return unknown


def _utc_minute(ts: float | None) -> str:
    return _utc_text(ts, "%Y-%m-%d %H:%M UTC", "an unknown time")


def _earliest_ts(timestamps: Iterable[float | None]) -> float | None:
    known = [ts for ts in timestamps if ts is not None]
    return min(known) if known else None


def _reconcile_denies(
    denies: Sequence[_DenyFact],
    deny_routing_states: Sequence[_KeyState],
    note_rows: Sequence[_NoteRow],
    account_config_dir: Path,
    built_line_nos: Collection[int],
    branch_facts: Mapping[str, _BranchFacts],
    routing_by_line: Mapping[int, str],
) -> _DenyReconciliation:
    """Check this root's routing-gate denies against the note, so an unlisted branch with a deny here cannot shrink an arm.

    A deny on a branch listed for another config dir is a notice, because no note edit could clear a refusal.
    """
    rows_by_branch = {row.branch: row for row in note_rows}
    refusals: list[str] = []
    notices: list[str] = []
    contradicted_line_nos = {
        row.line_no
        for deny, deny_state in zip(denies, deny_routing_states, strict=True)
        if deny_state.state == _STATE_OFF
        and (row := rows_by_branch.get(deny.branch)) is not None
        and row.resolved_config_dir == account_config_dir
        and row.status == _STATUS_MERGED
        and row.line_no in built_line_nos
    }
    denied_branches = {deny.branch for deny in denies}
    if any(not deny.branch or deny.branch in scope._DEFAULT_BRANCH_CANDIDATES for deny in denies):
        notices.append(f"{_COMMAND}: a routing deny on an empty or default-branch name cannot belong to a sampled branch")

    unlisted_branches: dict[str, float | None] = {}
    unknown_state_noticed: set[int] = set()
    routing_off_noticed: set[int] = set()
    for deny, deny_state in zip(denies, deny_routing_states, strict=True):
        row = rows_by_branch.get(deny.branch)
        if row is None:
            if deny.branch and deny.branch not in scope._DEFAULT_BRANCH_CANDIDATES and deny.branch not in unlisted_branches:
                unlisted_branches[deny.branch] = _earliest_ts(d.ts for d in denies if d.branch == deny.branch)
            continue
        if row.resolved_config_dir != account_config_dir or row.status != _STATUS_MERGED:
            continue
        if deny_state.state is None and row.line_no not in unknown_state_noticed:
            unknown_state_noticed.add(row.line_no)
            cause_text = _DENY_UNTIMED_PHRASE if deny_state.cause == _CAUSE_ROUND_UNTIMED else deny_state.cause
            notices.append(
                f"{_COMMAND}: note line {row.line_no} holds a routing deny, but the log leaves the routing state"
                f" at that deny unknown ({cause_text})"
            )
        if (
            routing_by_line.get(row.line_no) == _STATE_OFF and row.line_no not in contradicted_line_nos
            and row.line_no not in routing_off_noticed
        ):
            routing_off_noticed.add(row.line_no)
            notices.append(
                f"{_COMMAND}: note line {row.line_no} holds a routing deny, but the routing key reads off for it"
            )
    for earliest_ts in unlisted_branches.values():
        refusals.append(
            f"{_COMMAND}: a routing deny at {_utc_minute(earliest_ts)} falls on a branch the note does not list;"
            " in a terminal outside any agent session, run review-trace with the same top-level --config-dir"
            " given to capture and --deny-only, once with --this-repo and, if no row matches that minute,"
            f" once with --projects='<main-project-slug>{_WORKTREE_SLUG_INFIX}*' for a removed worktree's"
            " sessions; match the row at that minute, then add a note row for its branch:"
            f" {_STATUS_MERGED} if the branch belongs in the sample, else {_STATUS_EXCLUDED};"
            ' see docs/transcript-analysis.md routing-guard "Deny reconciliation"'
        )

    for row in note_rows:
        if row.resolved_config_dir != account_config_dir:
            if row.branch in branch_facts or row.branch in denied_branches:
                notices.append(
                    f"{_COMMAND}: note line {row.line_no} is listed for another config directory"
                    " but has records on this root; its rounds are split across accounts"
                )
            continue
        if row.status != _STATUS_MERGED or row.line_no not in built_line_nos:
            continue
        if routing_by_line.get(row.line_no) == _STATE_ON and row.branch not in denied_branches:
            notices.append(
                f"{_COMMAND}: note line {row.line_no} was worked with the routing key on but no routing deny was found for it;"
                " a typed /code-review or an unrouted round leaves none"
            )
    return _DenyReconciliation(refusals, notices, contradicted_line_nos)


# --- the capture run ---------------------------------------------------------


def _yes_no(flag: bool) -> str:
    return "yes" if flag else "no"


def _row_line(line_no: int, outcome: str) -> str:
    return f"{_COMMAND}: note line {line_no}: {outcome}"


def _utc_date(ts: float | None) -> str:
    return _utc_text(ts, "%Y-%m-%d", "an unknown date")


def _states_text(states: _KeyStates) -> str:
    return "/".join(f"{label}={state}" for label, state in zip(_STATE_LABELS, states, strict=True))


def _captured_line(line_no: int, built: _BuiltRow, earliest_record_ts: float | None) -> str:
    rounds = sum(loop.round_count for loop in built.loops)
    growth_defined = sum(1 for loop in built.loops if loop.growth_tokens is not None)
    joined = sum(loop.joined_denied_opens for loop in built.loops)
    degraded = any(loop.degraded for loop in built.loops)
    return _row_line(
        line_no,
        f"captured ({rounds} rounds, {len(built.loops)} loops, {growth_defined} with growth defined,"
        f" {joined} denied round-opens joined, degraded: {_yes_no(degraded)}, states: {_states_text(built.states)},"
        f" earliest record {_utc_date(earliest_record_ts)})",
    )


def _standing_line(line_no: int, standing: _CaptureRecord | _StandingFile) -> tuple[str, bool]:
    if standing is _StandingFile.LOOPS_ONLY:
        return _row_line(line_no, f"invalid ({_LOOPS_ONLY_NOTE})"), False
    if isinstance(standing, _StandingFile):
        return _row_line(line_no, "invalid"), False
    growth_defined = sum(1 for loop in standing.loops if loop["growth_tokens"] is not None)
    degraded = any(loop["degraded"] for loop in standing.loops)
    return _row_line(
        line_no,
        f"already captured ({len(standing.loops)} loops, {growth_defined} with growth defined,"
        f" degraded: {_yes_no(degraded)}, states: {_states_text(standing.states)})",
    ), True


def _published_files_text(published_count: int) -> str:
    if published_count == 0:
        return "this run published 0 files"
    noun, pronoun = ("file", "it") if published_count == 1 else ("files", "them")
    return f"this run published {published_count} {noun}, and a re-run reports {pronoun} already captured"


def _exit_publish_failed(outcome: ledger_common._PublishOutcome, published_count: int) -> NoReturn:
    action = {
        ledger_common._PublishOutcome.WRITE_FAILED: "written",
        ledger_common._PublishOutcome.LINK_FAILED: "linked into place "
        + ledger_common._hard_link_requirement("the captures directory"),
    }.get(outcome, "published")
    _refuse(
        f"{_COMMAND}: a capture file could not be {action}; {_published_files_text(published_count)}",
        4,
    )


def _refuse_unsafe_captures_dir(captures_dir: Path) -> None:
    """Runs before the scan and again just before the first publish, since the scan can run long."""
    if ledger_common._ledger_path_is_git_tracked(captures_dir.resolve() / _GIT_TREE_CHECK_LEAF, _COMMAND):
        _refuse(
            f"{_COMMAND}: the captures directory is inside a git working tree; capture files are never"
            " written inside one",
            2,
        )
    if captures_dir.is_symlink():
        _refuse(f"{_COMMAND}: the captures directory is a symlink; capture files are never written through one", 2)


def _refuse_unless_still_opted_in(account_dir: Path) -> None:
    """Runs again just before the first publish, since the scan can run long and the owner can revoke consent meanwhile."""
    opt_in = _classify_capture_opt_in(account_dir)
    if opt_in is not _OptInOutcome.OPTED_IN:
        _refuse(_OPT_IN_REFUSALS[opt_in], 1)


class _RowKind(enum.Enum):
    CAPTURED = "captured"
    ALREADY_CAPTURED = "already captured"
    MISSING = "missing"
    INVALID = "invalid"


@dataclass
class _AccountReport:
    kinds: Counter[_RowKind] = field(default_factory=Counter)
    key_on_files: int = 0
    key_off_files: int = 0
    next_step_classes: set[str] = field(default_factory=set)

    def count_file(self, kind: _RowKind, states: _KeyStates) -> None:
        self.kinds[kind] += 1
        if states.routing == _STATE_ON:
            self.key_on_files += 1
        else:
            self.key_off_files += 1


def _capture_account_rows(
    merged_rows: Sequence[_NoteRow], standing_by_line: Mapping[int, _CaptureRecord | _StandingFile],
    key_states_by_line: Mapping[int, _BranchKeyStates], contradicted_line_nos: Collection[int],
    scan: _ScanFacts, account_dir: Path,
) -> _AccountReport:
    captures_dir = account_dir / _CAPTURES_DIR_NAME
    report = _AccountReport()
    published_count = 0
    captures_dir_ready = False
    for row in merged_rows:
        path = captures_dir / _capture_file_name(row.branch, _CAPTURE_REVISION)
        standing = standing_by_line[row.line_no]
        if standing is _StandingFile.ABSENT:
            rounds = scan.rounds_by_branch.get(row.branch, [])
            key_states = key_states_by_line[row.line_no]
            failure = _whole_failure(scan.branches.get(row.branch), rounds, key_states)
            if failure is None and row.line_no in contradicted_line_nos:
                failure = _WholeFailure(_REASON_KEY_STATE_CONTRADICTED, frozenset({_REASON_KEY_STATE_CONTRADICTED}))
            if failure is not None:
                print(_row_line(row.line_no, f"missing ({failure.reason})"))
                report.kinds[_RowKind.MISSING] += 1
                report.next_step_classes |= failure.next_step_classes
                continue
            built = _build_row(rounds, key_states)
            text_bytes = built.text.encode("utf-8")
            try:
                _parse_capture_bytes(text_bytes)
            except _InvalidCapture:
                _refuse(
                    f"{_COMMAND}: internal error: the built row failed validation; "
                    f"{_published_files_text(published_count)}",
                    4,
                )
            # The directory is created just before the first publish, so a run that publishes nothing creates nothing.
            if not captures_dir_ready:
                _refuse_unless_still_opted_in(account_dir)
                _refuse_unsafe_captures_dir(captures_dir)
                captures_dir.mkdir(mode=0o700, exist_ok=True)
                captures_dir_ready = True
            outcome = ledger_common._publish_new_file(path, built.text, _CAPTURE_TEMP_PREFIX, _CAPTURE_TEMP_SUFFIX)
            if outcome is ledger_common._PublishOutcome.CREATED:
                published_count += 1
                print(_captured_line(row.line_no, built, scan.branches[row.branch].first_record_ts))
                report.count_file(_RowKind.CAPTURED, built.states)
                continue
            if outcome is not ledger_common._PublishOutcome.EXISTS:
                _exit_publish_failed(outcome, published_count)
            standing = _load_capture_file(path)
            if standing is _StandingFile.ABSENT:
                standing = _StandingFile.UNREADABLE
        line, row_is_good = _standing_line(row.line_no, standing)
        print(line)
        if row_is_good:
            report.count_file(_RowKind.ALREADY_CAPTURED, standing.states)
        else:
            report.kinds[_RowKind.INVALID] += 1
    return report


def _account_level_lines(log: _SessionConfigKeysLog, coverage_start: float | None) -> list[str]:
    """No coverage start means no valid row and no recorder attachment of any kind, which is the reader doc's
    "the hook has not run".
    """
    if log.status is _LogStatus.UNUSABLE:
        return [_LOG_UNUSABLE_NOTICE]
    lines: list[str] = []
    if coverage_start is None:
        lines.append(_HOOK_NOT_RUN_ON_ACCOUNT_LINE)
    if log.status is _LogStatus.READ and not log.routing_off_seen:
        lines.append(_NO_KEY_OFF_ROW_LINE)
    if log.status is _LogStatus.READ and log.skipped_lines:
        lines.append(_SKIPPED_LOG_LINES_LINE)
    return lines


def _summary_lines(
    report: _AccountReport, merged_row_count: int, excluded_row_count: int, *, pricing_drift: bool,
) -> list[str]:
    counts = report.kinds
    summary = (
        f"{_COMMAND}: summary: {counts[_RowKind.CAPTURED]} captured, {counts[_RowKind.ALREADY_CAPTURED]} already captured,"
        f" {counts[_RowKind.MISSING]} missing, {counts[_RowKind.INVALID]} invalid, {excluded_row_count} excluded;"
        f" key-on files: {report.key_on_files}, key-off files: {report.key_off_files}"
    )
    if merged_row_count == 0:
        next_action = _NEXT_ACTION_NO_MERGED_ROW
    elif counts[_RowKind.MISSING] or counts[_RowKind.INVALID]:
        next_action = _NEXT_ACTION_ACT_ON_LINES_AND_RESOLVE_DRIFT if pricing_drift else _NEXT_ACTION_ACT_ON_LINES
    elif pricing_drift:
        next_action = _NEXT_ACTION_RESOLVE_PRICING_DRIFT
    else:
        next_action = _NEXT_ACTION_CAPTURE_AGAIN
    return [summary, next_action]


def _run_capture(args: argparse.Namespace) -> None:
    print(_DO_NOT_PUBLISH_BANNER)
    if not args.config_dir:
        _refuse(f"{_COMMAND}: --capture needs the top-level --config-dir (before `{_COMMAND}`) naming the account to capture", 2)
    note_rows = _load_note_rows(args.note)
    account_dir = Path(args.config_dir)
    resolved_account_dir = account_dir.resolve()
    account_rows = [row for row in note_rows if row.resolved_config_dir == resolved_account_dir]
    if not account_rows:
        _refuse(f"{_COMMAND}: the --config-dir account is not listed in the note", 2)

    _refuse_unless_still_opted_in(account_dir)
    captures_dir = account_dir / _CAPTURES_DIR_NAME
    _refuse_unsafe_captures_dir(captures_dir)

    scan_gaps: Counter[str] = Counter()
    root = account_dir / "projects"
    scan = _scan_sessions(root, _capture_scope(root, scan_gaps), scan_gaps)
    if scan_gaps:
        _refuse(
            f"{_COMMAND}: part of the scan root could not be read, so a capture could silently miss records;"
            " restore read access and re-run. Nothing was published",
            2,
        )

    wanted_session_ids = {
        round_facts.session_id for rounds in scan.rounds_by_branch.values() for round_facts in rounds if round_facts.session_id
    } | {deny.session_id for deny in scan.denies if deny.session_id}
    log = _read_session_config_keys_log(account_dir / _LOG_FILE_NAME, wanted_session_ids)
    coverage_start = _coverage_start(log, scan.recorder_events)

    merged_rows = [row for row in account_rows if row.status == _STATUS_MERGED]
    standing_by_line = {
        row.line_no: _load_capture_file(captures_dir / _capture_file_name(row.branch, _CAPTURE_REVISION))
        for row in merged_rows
    }
    built_line_nos = {line_no for line_no, standing in standing_by_line.items() if standing is _StandingFile.ABSENT}
    key_states_by_line: dict[int, _BranchKeyStates] = {}
    routing_by_line: dict[int, str] = {}
    for row in merged_rows:
        standing = standing_by_line[row.line_no]
        if isinstance(standing, _CaptureRecord):
            routing_by_line[row.line_no] = standing.states.routing
        elif row.line_no in built_line_nos:
            loop_rounds = _rounds_in_loops(scan.rounds_by_branch.get(row.branch, []))
            key_states = _branch_key_states(loop_rounds, log, scan.recorder_events, coverage_start)
            key_states_by_line[row.line_no] = key_states
            routing_by_line[row.line_no] = key_states.by_key[_ROUTING_KEY]
    deny_routing_states = [
        _key_state_at(_ROUTING_KEY, deny.session_id, deny.ts, log, scan.recorder_events, coverage_start)
        for deny in scan.denies
    ]
    reconciliation = _reconcile_denies(
        scan.denies, deny_routing_states, note_rows, resolved_account_dir, built_line_nos, scan.branches, routing_by_line,
    )
    if scan.dropped_deny_without_tool_use:
        print(_DROPPED_DENY_NOTICE, file=sys.stderr)
    for notice in reconciliation.notices:
        print(notice, file=sys.stderr)
    if reconciliation.refusals:
        for refusal in reconciliation.refusals:
            print(refusal, file=sys.stderr)
        sys.exit(2)

    report = _capture_account_rows(
        merged_rows, standing_by_line, key_states_by_line, reconciliation.contradicted_line_nos, scan, account_dir,
    )
    drift = pricing._format_drift_detected()
    if drift:
        print(_PRICING_INTEGRITY_LINE)
    for next_step_class in (*_CAUSES, _REASON_KEY_STATE_MIXED, _REASON_KEY_STATE_CONTRADICTED):
        if next_step_class in report.next_step_classes:
            print(_NEXT_STEP_LINES[next_step_class])
    for line in _account_level_lines(log, coverage_start):
        print(line)
    excluded_row_count = sum(1 for row in account_rows if row.status == _STATUS_EXCLUDED)
    for line in _summary_lines(report, len(merged_rows), excluded_row_count, pricing_drift=drift):
        print(line)
    needs_attention = report.kinds[_RowKind.MISSING] or report.kinds[_RowKind.INVALID]
    sys.exit(3 if needs_attention or drift else 0)


# --- the compute run ---------------------------------------------------------


class _NotRunReason(NamedTuple):
    reason: str
    line_no: int | None


class _SampleSelection(NamedTuple):
    on_rows: list[_NoteRow]
    off_rows: list[_NoteRow]
    loops_by_line: dict[int, list[dict]]
    reasons: list[_NotRunReason]


class _BranchScalars(NamedTuple):
    """One branch's figures, computed once before any replicate.

    `carry_rate_sum` is dollars per context token summed over the branch's later turns, and is zero for a key-on branch.
    """

    digest: str
    loop_count: int
    growth_sum: int
    growth_defined_loops: int
    overhead_dollars: float
    carry_rate_sum: float
    degraded_loops: int
    priced_models: frozenset[str]


class _ArmTotals(NamedTuple):
    branch_count: int
    loop_count: int
    growth_sum: int
    growth_defined_loops: int
    growth_defined_branches: int
    overhead_dollars: float
    carry_rate_sum: float


class _NetFigures(NamedTuple):
    net: float
    overhead_difference: float
    carry: float
    gap: float


class _BootstrapResult(NamedTuple):
    lower: float
    upper: float
    replicates: int
    undefined_on: int
    undefined_off: int
    undefined_total: int


def _account_is_ready(config_dir: Path) -> bool:
    """The key is checked first, so an account with its key off has none of its capture files opened."""
    if _classify_capture_opt_in(config_dir) is not _OptInOutcome.OPTED_IN:
        return False
    return (config_dir / _CAPTURES_DIR_NAME).is_dir()


def _merged_rows(note_rows: Sequence[_NoteRow]) -> list[_NoteRow]:
    return [row for row in note_rows if row.status == _STATUS_MERGED]


def _ready_accounts(note_rows: Sequence[_NoteRow]) -> set[Path]:
    accounts_with_merged_rows = dict.fromkeys(row.resolved_config_dir for row in _merged_rows(note_rows))
    return {config_dir for config_dir in accounts_with_merged_rows if _account_is_ready(config_dir)}


def _load_sample_files(
    note_rows: Sequence[_NoteRow], ready: Collection[Path],
) -> dict[int, _CaptureRecord | _StandingFile]:
    return {
        row.line_no: _load_capture_file(
            row.resolved_config_dir / _CAPTURES_DIR_NAME / _capture_file_name(row.branch, _CAPTURE_REVISION),
        )
        for row in _merged_rows(note_rows)
        if row.resolved_config_dir in ready
    }


def _has_growth_defined_loop(rows: Sequence[_NoteRow], loaded_loops: Mapping[int, list[dict]]) -> bool:
    return any(loop["growth_tokens"] is not None for row in rows for loop in loaded_loops[row.line_no])


def _pilot_states(record: _CaptureRecord) -> tuple[str, str]:
    return (record.states.round_consult_round2_pilot, record.states.architect_fix_plan_pilot)


def _row_load_reasons(
    rows_by_account: Mapping[Path, Sequence[_NoteRow]], ready: Collection[Path],
    loaded: Mapping[int, _CaptureRecord | _StandingFile],
) -> list[_NotRunReason]:
    reasons: list[_NotRunReason] = []
    for config_dir, account_rows in rows_by_account.items():
        if config_dir not in ready:
            reasons.append(_NotRunReason(_REASON_ACCOUNT_NOT_READY, account_rows[0].line_no))
            continue
        for row in account_rows:
            fetched = loaded[row.line_no]
            if fetched is _StandingFile.ABSENT:
                reasons.append(_NotRunReason(_REASON_FILE_MISSING, row.line_no))
            elif isinstance(fetched, _StandingFile):
                reasons.append(_NotRunReason(_REASON_FILE_UNUSABLE, row.line_no))
    return reasons


def _account_count_reasons(
    account_rows: Sequence[_NoteRow], records: Mapping[int, _CaptureRecord],
) -> tuple[list[_NotRunReason], list[_NoteRow], list[_NoteRow]]:
    """(reasons, key-on rows, key-off rows) of one account whose files all loaded."""
    on_rows: list[_NoteRow] = []
    off_rows: list[_NoteRow] = []
    for row in account_rows:
        (on_rows if records[row.line_no].states.routing == _STATE_ON else off_rows).append(row)
    if not on_rows:
        return [], [], []
    reasons: list[_NotRunReason] = []
    reference_states = {_pilot_states(records[row.line_no]) for row in on_rows}
    if len(reference_states) > 1:
        reasons.append(_NotRunReason(_REASON_MIXED_PILOTS, on_rows[0].line_no))
    else:
        reasons.extend(
            _NotRunReason(_REASON_OFF_PILOT_DIFFERS, row.line_no)
            for row in off_rows
            if _pilot_states(records[row.line_no]) not in reference_states
        )
    if len(off_rows) != len(on_rows):
        reasons.append(_NotRunReason(_REASON_ARM_SIZE_MISMATCH, account_rows[0].line_no))
    return reasons, on_rows, off_rows


def _select_sample(
    note_rows: Sequence[_NoteRow], ready: Collection[Path], loaded: Mapping[int, _CaptureRecord | _StandingFile],
) -> _SampleSelection:
    rows_by_account: dict[Path, list[_NoteRow]] = {}
    for row in _merged_rows(note_rows):
        rows_by_account.setdefault(row.resolved_config_dir, []).append(row)
    reasons = _row_load_reasons(rows_by_account, ready, loaded)
    on_rows: list[_NoteRow] = []
    off_rows: list[_NoteRow] = []
    loops_by_line: dict[int, list[dict]] = {}
    if not reasons:
        records = {line_no: found for line_no, found in loaded.items() if isinstance(found, _CaptureRecord)}
        for account_rows in rows_by_account.values():
            account_reasons, account_on_rows, account_off_rows = _account_count_reasons(account_rows, records)
            reasons.extend(account_reasons)
            on_rows.extend(account_on_rows)
            off_rows.extend(account_off_rows)
        loops_by_line = {row.line_no: records[row.line_no].loops for row in [*on_rows, *off_rows]}
        if len(on_rows) < _MIN_ON_BRANCHES:
            reasons.append(_NotRunReason(_REASON_TOO_FEW_ON, None))
        if not reasons and not (
            _has_growth_defined_loop(on_rows, loops_by_line) and _has_growth_defined_loop(off_rows, loops_by_line)
        ):
            reasons.append(_NotRunReason(_REASON_NO_GROWTH_DEFINED, None))
    reasons.sort(key=lambda found: (_NOT_RUN_REASONS.index(found.reason), found.line_no or 0))
    return _SampleSelection(on_rows, off_rows, loops_by_line, reasons)


def _usage_from_variant_tokens(variant: str, token_counts: Mapping[str, int]) -> dict:
    """A usage dict that pricing._price_turn prices as `token_counts` at `variant`'s multipliers.

    The token-class keys are pricing._token_counts' own.
    """
    usage: dict = {
        "input_tokens": token_counts.get("input", 0),
        "output_tokens": token_counts.get("output", 0),
        "cache_read_input_tokens": token_counts.get("cache_read", 0),
        "cache_creation": {
            "ephemeral_1h_input_tokens": token_counts.get("cache_write_1h", 0),
            "ephemeral_5m_input_tokens": token_counts.get("cache_write_5m", 0),
        },
    }
    usage.update(_VARIANT_USAGE_FIELDS[variant])
    return usage


def _loop_overhead(loop: Mapping, priced_models: set[str]) -> tuple[float, bool]:
    """(dollars, whether a model was skipped) for one loop's overhead tokens at the current rate table."""
    dollars = 0.0
    skipped = False
    for model, by_variant in loop["overhead_tokens"].items():
        for variant, token_counts in by_variant.items():
            dollars_by_class, _context, _unpriced = pricing._price_turn(model, _usage_from_variant_tokens(variant, token_counts))
            if dollars_by_class is None:
                skipped = True
                continue
            dollars += sum(dollars_by_class.values())
            priced_models.add(model)
    return dollars, skipped


def _loop_carry_rate(loop: Mapping, priced_models: set[str]) -> tuple[float, bool]:
    """(dollars per context token, whether a model was skipped) over one loop's later turns, at each turn's cache-read rate."""
    rate_sum = 0.0
    skipped = False
    for model, turn_count in loop["later_turns"].items():
        rates = pricing._model_rates(model)
        if rates is None:
            skipped = True
            continue
        rate_sum += turn_count * rates["cache_read"] / _TOKENS_PER_MTOK
        priced_models.add(model)
    return rate_sum, skipped


def _branch_scalars(loops: Sequence[Mapping], digest: str, *, price_later_turns: bool) -> _BranchScalars:
    growth_sum = 0
    growth_defined_loops = 0
    overhead_dollars = 0.0
    carry_rate_sum = 0.0
    degraded_loops = 0
    priced_models: set[str] = set()
    for loop in loops:
        loop_dollars, overhead_skipped = _loop_overhead(loop, priced_models)
        overhead_dollars += loop_dollars
        carry_skipped = False
        if price_later_turns:
            loop_rate, carry_skipped = _loop_carry_rate(loop, priced_models)
            carry_rate_sum += loop_rate
        if loop["growth_tokens"] is not None:
            growth_sum += loop["growth_tokens"]
            growth_defined_loops += 1
        if loop["degraded"] or overhead_skipped or carry_skipped:
            degraded_loops += 1
    return _BranchScalars(
        digest, len(loops), growth_sum, growth_defined_loops, overhead_dollars, carry_rate_sum, degraded_loops,
        frozenset(priced_models),
    )


def _arm_totals(branches: Iterable[_BranchScalars]) -> _ArmTotals:
    branch_count = loop_count = growth_sum = growth_defined_loops = growth_defined_branches = 0
    overhead_dollars = carry_rate_sum = 0.0
    for branch in branches:
        branch_count += 1
        loop_count += branch.loop_count
        growth_sum += branch.growth_sum
        growth_defined_loops += branch.growth_defined_loops
        growth_defined_branches += 1 if branch.growth_defined_loops else 0
        overhead_dollars += branch.overhead_dollars
        carry_rate_sum += branch.carry_rate_sum
    return _ArmTotals(
        branch_count, loop_count, growth_sum, growth_defined_loops, growth_defined_branches, overhead_dollars,
        carry_rate_sum,
    )


def _net_from_totals(on: _ArmTotals, off: _ArmTotals) -> _NetFigures | None:
    if on.growth_defined_loops == 0 or off.growth_defined_loops == 0:
        return None
    overhead_difference = on.overhead_dollars / on.loop_count - off.overhead_dollars / off.loop_count
    gap = off.growth_sum / off.growth_defined_loops - on.growth_sum / on.growth_defined_loops
    carry = gap * off.carry_rate_sum / off.loop_count
    return _NetFigures(overhead_difference - carry, overhead_difference, carry, gap)


def _arm_net(on: Iterable[_BranchScalars], off: Iterable[_BranchScalars]) -> _NetFigures | None:
    return _net_from_totals(_arm_totals(on), _arm_totals(off))


def _bootstrap_net(
    on: Sequence[_BranchScalars], off: Sequence[_BranchScalars], *, replicates: int, rng: random.Random,
) -> _BootstrapResult:
    """Each arm is sorted by digest before drawing, so the result does not depend on note order.

    A replicate whose resample leaves an arm with no growth-defined loop has no net. It is never redrawn and ranks
    above every defined net as math.inf, so the replicate count is exact and the lower bound can only rise.
    """
    on_sorted = sorted(on, key=lambda branch: branch.digest)
    off_sorted = sorted(off, key=lambda branch: branch.digest)
    nets: list[float] = []
    undefined_on = undefined_off = undefined_total = 0
    for _ in range(replicates):
        on_totals = _arm_totals(rng.choices(on_sorted, k=len(on_sorted)))
        off_totals = _arm_totals(rng.choices(off_sorted, k=len(off_sorted)))
        figures = _net_from_totals(on_totals, off_totals)
        if figures is None:
            nets.append(math.inf)
            undefined_total += 1
            undefined_on += 1 if on_totals.growth_defined_loops == 0 else 0
            undefined_off += 1 if off_totals.growth_defined_loops == 0 else 0
        else:
            nets.append(figures.net)
    nets.sort()
    lower, upper = review_rounds._resample_percentile(nets, level=_GUARD_CI_LEVEL)
    return _BootstrapResult(lower, upper, replicates, undefined_on, undefined_off, undefined_total)


def _format_not_run(found: _NotRunReason) -> str:
    locator = f" (note line {found.line_no})" if found.line_no is not None else ""
    return f"{_COMMAND}: NOT RUN: {found.reason}{locator}"


def _exit_not_run(reasons: Sequence[_NotRunReason]) -> NoReturn:
    for found in reasons:
        print(_format_not_run(found))
    sys.exit(3)


def _format_upper_bound(upper: float) -> str:
    return "unbounded" if math.isinf(upper) else render._fmt_usd(upper)


def _arm_figure_lines(arm: str, totals: _ArmTotals) -> list[str]:
    return [
        f"key-{arm} branches: {totals.branch_count}",
        f"key-{arm} loops: {totals.loop_count}",
        f"key-{arm} growth-defined branches: {totals.growth_defined_branches}",
        f"key-{arm} growth-defined loops: {totals.growth_defined_loops}",
    ]


def _out_file_text(figures: _NetFigures, bootstrap: _BootstrapResult, on: _ArmTotals, off: _ArmTotals) -> str:
    lines = [
        _DO_NOT_PUBLISH_BANNER,
        f"net per loop: {render._fmt_usd(figures.net)}",
        f"{_GUARD_CI_LEVEL:.0%} interval: {render._fmt_usd(bootstrap.lower)} to {_format_upper_bound(bootstrap.upper)}",
        f"overhead difference (key-on minus key-off) per loop: {render._fmt_usd(figures.overhead_difference)}",
        f"carry (subtracted) per loop: {render._fmt_usd(figures.carry)}",
        f"growth gap, key-off minus key-on, tokens per loop: {figures.gap:,.1f}",
        *_arm_figure_lines(_STATE_ON, on),
        *_arm_figure_lines(_STATE_OFF, off),
        f"undefined replicates: {bootstrap.undefined_total} of {bootstrap.replicates}",
    ]
    return "\n".join(lines) + "\n"


def _print_compute_notices(
    on: Sequence[_BranchScalars], off: Sequence[_BranchScalars], bootstrap: _BootstrapResult,
) -> None:
    for arm, branches, undefined_replicates in (
        (_STATE_ON, on, bootstrap.undefined_on), (_STATE_OFF, off, bootstrap.undefined_off),
    ):
        if any(branch.degraded_loops for branch in branches):
            print(_DEGRADED_NOTICES[arm], file=sys.stderr)
        if undefined_replicates:
            print(_UNDEFINED_REPLICATE_NOTICES[arm], file=sys.stderr)
    priced_models = frozenset().union(*(branch.priced_models for branch in [*on, *off]))
    as_of = datetime.now(UTC).date()
    if any(pricing._rate_expired(model, as_of) for model in priced_models):
        print(_STALE_PRICING_LINE, file=sys.stderr)


def _run_compute(args: argparse.Namespace) -> None:
    if args.config_dir:
        _refuse(_COMPUTE_CONFIG_DIR_MESSAGE, 2)
    if args.out:
        ledger_common._refuse_unusable_out_path(args.out, _COMMAND)
    note_rows = _load_note_rows(args.note)
    ready = _ready_accounts(note_rows)
    loaded = _load_sample_files(note_rows, ready)
    selection = _select_sample(note_rows, ready, loaded)
    if selection.reasons:
        _exit_not_run(selection.reasons)

    def arm_scalars(rows: Sequence[_NoteRow], *, price_later_turns: bool) -> list[_BranchScalars]:
        scalars = (
            _branch_scalars(
                selection.loops_by_line[row.line_no], _branch_digest(row.branch), price_later_turns=price_later_turns,
            )
            for row in rows
        )
        return sorted(scalars, key=lambda branch: branch.digest)

    on = arm_scalars(selection.on_rows, price_later_turns=False)
    off = arm_scalars(selection.off_rows, price_later_turns=True)
    figures = _arm_net(on, off)
    if figures is None or not math.isfinite(figures.net):
        _exit_not_run([_NotRunReason(_REASON_NON_FINITE, None)])
    bootstrap = _bootstrap_net(
        on, off, replicates=_BOOTSTRAP_REPLICATES, rng=random.Random(_BOOTSTRAP_SEED),
    )
    if not math.isfinite(bootstrap.lower):
        _exit_not_run([_NotRunReason(_REASON_NON_FINITE, None)])

    if args.out:
        ledger_common._publish_new_owner_file(
            args.out, _out_file_text(figures, bootstrap, _arm_totals(on), _arm_totals(off)), _COMMAND,
        )
        print(_OUT_PUBLISHED_NOTICE, file=sys.stderr)
    _print_compute_notices(on, off, bootstrap)
    print(_UNAUTHENTICATED_INPUTS_LINE, file=sys.stderr)
    if bootstrap.lower > 0:
        print(_STATUS_FAILED)
        sys.exit(1)
    print(_STATUS_PASSED)
    sys.exit(0)


def _innermost_frame_name(exc: BaseException) -> str:
    """Returns only the function name, never a file path, line number or exception text."""
    innermost = exc.__traceback__
    if innermost is None:
        return "unknown"
    while innermost.tb_next is not None:
        innermost = innermost.tb_next
    return innermost.tb_frame.f_code.co_name


def cmd_routing_guard(args: argparse.Namespace) -> None:
    """Every exit goes through sys.exit, because main() ignores a handler's return value."""
    try:
        if args.out == "":
            _refuse(_EMPTY_OUT_MESSAGE, 2)
        if args.capture:
            if args.out:
                _refuse(_OUT_WITH_CAPTURE_MESSAGE, 2)
            _run_capture(args)
        else:
            _run_compute(args)
    except OSError as exc:
        print(f"{_COMMAND}: I/O error: {exc.strerror or 'unknown error'}", file=sys.stderr)
        sys.exit(4)
    except Exception as exc:
        print(f"{_UNEXPECTED_ERROR_MESSAGE} ({type(exc).__name__} in {_innermost_frame_name(exc)})", file=sys.stderr)
        sys.exit(4)


def _add_routing_guard_subparser(subparsers: argparse._SubParsersAction) -> None:
    p_routing_guard = subparsers.add_parser(
        "routing-guard",
        help=(
            "Capture each sampled branch's review loops (--capture, one account per run) and compute the"
            " review-orchestrator routing gate's cost guard from the captured files. Owner-only output;"
            " run outside any agent session."
        ),
    )
    p_routing_guard.add_argument(
        "--note", required=True, metavar="PATH",
        help=(
            "Absolute path to the note, outside any git work tree: one tab-separated row per branch holding"
            " config dir, branch and merged|excluded."
        ),
    )
    p_routing_guard.add_argument(
        "--capture", action="store_true",
        help=(
            "Publish a write-once file for each `merged` row of the --config-dir account that is captured whole"
            " and has no file yet. Needs the top-level --config-dir (before `routing-guard`) and that account's"
            " pr_cost_recording opt-in."
        ),
    )
    p_routing_guard.add_argument(
        "--out", default=None, metavar="PATH",
        help=(
            "Compute only: write the figures to this new owner-only file outside any git work tree. Compute prints a"
            " status line (PASSED, FAILED, or NOT RUN with each reason) and owner-only notices, never a figure."
        ),
    )
    p_routing_guard.set_defaults(func=cmd_routing_guard)
