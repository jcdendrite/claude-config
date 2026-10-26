"""The routing-guard command: `--capture` publishes one write-once file per sampled branch, else it computes the guard.

A file holds the token counts of a branch's review loops, so a later run can price them at the
rate table current then. `docs/transcript-analysis.md` § "routing-guard" states what compute reads and prints.

OUTPUT INVARIANT, each clause pinned by the tests it names:
- Free text (assistant and user message text, `Skill` and `Agent` arguments, denial text, a dispatch's meta description)
  reaches neither a capture file nor stdout or stderr. Denial text is read only to classify a record.
  `TestFreeText` in test_transcript_routing_guard_capture.py.
  No line names a branch, a config-directory path or an account label. `_invoke_routing_guard` in
  _routing_guard_helpers.py asserts, on every exit, that no note branch, note config-dir cell, absolute cell's final
  path segment or `--config-dir` argument appears in either stream.
- A capture file stores `message.model` strings verbatim. Every other string it holds is a fixed label.
  `TestGoldenFile` there.
- A transcript `requestId` can reach stderr, and nothing else, through pricing.dedup_turns_by_request_id's rate-limited
  prints. The DO NOT PUBLISH banner covers it. `TestFreeText` there.
- Compute writes no string read from a capture file, a model string included, to stdout, stderr or the `--out` file.
  `TestPricing` and `TestCaptureFiles` in test_transcript_routing_guard_compute.py.
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
import sys
from collections import Counter, defaultdict
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple, NoReturn

import _config
from transcript_analysis import author_outcome, corpus, denials, ledger_common, pricing, render, review_rounds, scope

_COMMAND = "routing-guard"

# Bump only when a change alters what a stored row means. A bump leaves every earlier file unpoolable,
# because a reader opens only the file at the current value.
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
# Capture refuses a cwd outside a worktree of it, because scope._repo_scoped_project_slugs scopes the read to the cwd's repo.
_CHECKOUT_ROOT = Path(__file__).resolve().parents[4]

# Placeholder leaf for the git work tree check on the captures directory, which must see the directory itself.
_GIT_TREE_CHECK_LEAF = "capture"
_CAPTURE_TEMP_PREFIX = f".{_COMMAND}-"
_CAPTURE_TEMP_SUFFIX = ".tmp"

# require-review-orchestrator-routing.sh's DENY_GATE_LABEL, which denials._denial_hook_label returns for its denials.
_ROUTING_DENY_LABEL = "review-orchestrator routing"
_WORKTREE_SLUG_INFIX = "--claude-worktrees-"

_DO_NOT_PUBLISH_BANNER = (
    "DO NOT PUBLISH -- this output belongs to the owner alone.\n"
    "`docs/private-project-redaction.md` section \"A wider corpus goes to the owner, never into a public artifact\""
    " allows a public artifact to state only whether the guard passed and whether the default flipped.\n"
    "Run and read routing-guard outside any agent session."
)

_DROPPED_DENY_NOTICE = (
    f"{_COMMAND}: a routing-labeled denial whose tool_use is not in its session was not counted;"
    " if it is a real routing deny, its branch may be missing from the note;"
    ' see docs/transcript-analysis.md routing-guard "Deny reconciliation"'
)

_NOTE_COLUMN_COUNT = 5
_ARM_ON = "on"
_ARM_OFF = "off"
_ARM_UNMERGED = "unmerged"
_NOTE_ARMS = (_ARM_ON, _ARM_OFF, _ARM_UNMERGED)
_PILOT_ON = "on"
_PILOT_OFF = "off"
_PILOT_NONE = "-"
_PILOT_STATES = (_PILOT_ON, _PILOT_OFF)

_REASON_NO_RECORDS = "no-records"
_REASON_NO_COMPLETE_LOOP = "no-complete-loop"
_REASON_ORCHESTRATOR_UNREADABLE = "orchestrator-unreadable"

_CONFIG_READER_NOTICE = (
    "routing-guard: the config reader reported a problem; its message is withheld because it can name a path"
)
_PERMISSIVE_NOTE_NOTICE = "routing-guard: the note is readable by group or others; restrict it to the owner"
_COMPUTE_CONFIG_DIR_MESSAGE = (
    "routing-guard: compute reads no transcripts and takes no top-level --config-dir;"
    " the note names each config directory"
)
_OUT_WITH_CAPTURE_MESSAGE = "routing-guard: --out belongs to compute; drop --capture or drop --out"
_UNEXPECTED_ERROR_MESSAGE = "routing-guard: unexpected error; no traceback is printed because it can name a path or a branch"
_PRICING_INTEGRITY_LINE = (
    "routing-guard: PRICING INTEGRITY -- the transcript format may have drifted, so a captured row may be wrong;"
    " read this run's stderr for the diagnostic"
)

# Every NOT RUN reason is fixed and digit-free. The tuple's order is the order they print in.
_REASON_TOO_FEW_ON = "fewer key-on branches than the guard's minimum"
_REASON_ACCOUNT_NOT_READY = (
    "an account with key-on branches has its key off, a config fault, or no captures directory"
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
    arm: f"{_COMMAND}: some key-{arm} loops are degraded: a dispatch transcript was lost or a model has no price"
    for arm in (_ARM_ON, _ARM_OFF)
}
_UNDEFINED_REPLICATE_NOTICES = {
    arm: f"{_COMMAND}: some bootstrap replicates left the key-{arm} arm with no loop whose growth is defined"
    for arm in (_ARM_ON, _ARM_OFF)
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
    arm: str
    round_consult_round2_pilot: str
    architect_fix_plan_pilot: str


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
    """A captures-directory entry that gives no usable bytes: absent, or present but unreadable or invalid."""

    ABSENT = "absent"
    UNREADABLE = "unreadable"


class _RoundFacts(NamedTuple):
    """The derived facts one code-review round contributes. Never a raw record."""

    session_key: str
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
    """One review loop. The first four fields are the stored shape; the last two feed the per-row line."""

    overhead_tokens: dict[str, dict[str, dict[str, int]]]
    growth_tokens: int | None
    later_turns: dict[str, int]
    degraded: bool
    round_count: int
    joined_denied_opens: int


class _BuiltRow(NamedTuple):
    text: str
    loops: list[_Loop]


class _DenyFact(NamedTuple):
    branch: str
    ts: float | None


class _DenyReconciliation(NamedTuple):
    refusals: list[str]
    notices: list[str]


@dataclass
class _BranchFacts:
    has_timestamped_record: bool = False
    last_record_ts: float | None = None
    orchestrator_unreadable: bool = False


@dataclass
class _ScanFacts:
    branches: dict[str, _BranchFacts] = field(default_factory=dict)
    rounds_by_branch: dict[str, list[_RoundFacts]] = field(default_factory=dict)
    denies: list[_DenyFact] = field(default_factory=list)
    seen_deny_ids: set[str] = field(default_factory=set)
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
    """`config_dir` resolved, or a _NoteRefusal when resolving raises (an embedded NUL, a symlink loop)."""
    try:
        return config_dir.resolve()
    except (OSError, ValueError, RuntimeError):
        raise _NoteRefusal(line_no, "the config directory cannot be resolved") from None


def _validate_note_arm_and_pilots(line_no: int, arm: str, round2_pilot: str, architect_pilot: str) -> None:
    if arm not in _NOTE_ARMS:
        raise _NoteRefusal(line_no, "unknown arm value")
    allowed_pilots = (_PILOT_NONE,) if arm == _ARM_UNMERGED else _PILOT_STATES
    if round2_pilot not in allowed_pilots or architect_pilot not in allowed_pilots:
        raise _NoteRefusal(line_no, "unknown pilot value")


def _parse_note(text: str) -> list[_NoteRow]:
    """The note's rows, one per tab-separated line: config dir, branch, arm and the two pilot states.

    A `#` line and a blank line are skipped and a trailing `\\r` is dropped. Cells are not trimmed.
    Raises _NoteRefusal for the first bad line.
    """
    rows: list[_NoteRow] = []
    seen_branches: set[str] = set()
    for line_no, raw_line in enumerate(text.split("\n"), start=1):
        line = raw_line.removesuffix("\r")
        if not line or line.startswith("#"):
            continue
        cells = line.split("\t")
        if len(cells) != _NOTE_COLUMN_COUNT:
            raise _NoteRefusal(line_no, "wrong column count")
        config_cell, branch, arm, round2_pilot, architect_pilot = cells
        config_dir = _expand_note_config_dir(config_cell, line_no)
        resolved_config_dir = _resolve_note_config_dir(config_dir, line_no)
        if not branch or branch != branch.strip():
            raise _NoteRefusal(line_no, "unknown branch value")
        _validate_note_arm_and_pilots(line_no, arm, round2_pilot, architect_pilot)
        if branch in scope._DEFAULT_BRANCH_CANDIDATES:
            raise _NoteRefusal(line_no, "the branch is a default-branch name")
        if branch in seen_branches:
            raise _NoteRefusal(line_no, "the branch is listed twice")
        seen_branches.add(branch)
        rows.append(_NoteRow(line_no, resolved_config_dir, branch, arm, round2_pilot, architect_pilot))
    return rows


def _load_note_rows(note_arg: str) -> list[_NoteRow]:
    """Read and parse the note, exiting 2 on any refusal. Prints a notice for a note others can read."""
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
    """Whether `config_dir`'s `pr_cost_recording` is on, or the named config fault that blocked the read.

    Never exits. `_config` prints the config file's path and part of a rejected line to stderr, so the
    whole classification runs with stderr captured and discarded, and one fixed notice stands in for it.
    """
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
    """The captures-directory file name for `branch` at `revision`.

    The digest hides the branch only from a reader who does not already suspect it.
    """
    return f"{_branch_digest(branch)}.r{revision}.json"


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    if len({key for key, _value in pairs}) != len(pairs):
        raise ValueError("duplicate key")
    return dict(pairs)


def _reject_non_finite_constant(_constant: str) -> NoReturn:
    raise ValueError("non-finite number")


def _is_count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


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


def _parse_capture_bytes(data: bytes) -> list[dict]:
    """The loops of one capture file's bytes, or _InvalidCapture with a fixed reason that never echoes a cell.

    Shared by every reader and writer of a capture file, so a file is checked the same way on every path.
    """
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise _InvalidCapture("not UTF-8") from None
    try:
        parsed = json.loads(text, object_pairs_hook=_reject_duplicate_keys, parse_constant=_reject_non_finite_constant)
    except (ValueError, RecursionError):
        # ValueError covers invalid JSON, a duplicate key, a non-finite number and an integer past Python's digit limit.
        raise _InvalidCapture("not valid JSON") from None
    if not isinstance(parsed, dict) or set(parsed) != {"loops"}:
        raise _InvalidCapture("the file is not an object holding only loops")
    loops = parsed["loops"]
    if not isinstance(loops, list) or not loops:
        raise _InvalidCapture("loops is not a non-empty list")
    for loop in loops:
        _validate_loop(loop)
    return loops


def _serialize_loops(loops: Sequence[_Loop]) -> str:
    """The capture file text. ASCII-only, so it encodes the same under any locale."""
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
        },
        sort_keys=True, allow_nan=False,
    )


def _read_standing_capture(path: Path) -> bytes | _StandingFile:
    """The bytes of an existing capture file, opened without following a symlink."""
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


# --- scanning ----------------------------------------------------------------


def _capture_scope(root: Path, scan_gaps: Counter[str]) -> _CaptureScope:
    """This repo's live worktree slugs plus the orphaned worktree directories on `root`.

    Exits 1 when the cwd is not in a worktree of the checkout holding this module, since the
    cwd's repo would otherwise be the one read.
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
    """(record index, model, context) for each main-thread assistant turn with usage, a `<synthetic>` record excluded."""
    turns: list[tuple[int, str, int]] = []
    for idx, rec in enumerate(records):
        if rec.get("type") != "assistant" or rec.get("isSidechain"):
            continue
        message = rec.get("message") or {}
        usage = message.get("usage")
        if not usage:
            continue
        model = message.get("model")
        model = model if isinstance(model, str) else ""
        if model == pricing._SYNTHETIC_MODEL_ID:
            continue
        turns.append((idx, model, pricing._context_at_turn(usage)))
    return turns


def _nest_token_sink(token_sink: Mapping[tuple[str, str], Mapping[str, int]]) -> dict[str, dict[str, dict[str, int]]]:
    nested: dict[str, dict[str, dict[str, int]]] = {}
    for (model, variant), counts in token_sink.items():
        # A None model and an empty model name share one stored key, so their counts add.
        target = nested.setdefault(model if isinstance(model, str) else "", {}).setdefault(variant, {})
        for token_class, tokens in counts.items():
            target[token_class] = target.get(token_class, 0) + tokens
    return nested


def _hook_denials_in_record(rec: dict) -> Iterable[tuple[str, str, str]]:
    """(tool_use_id, hook name, message) for each hook denial in `rec`, in both transcript shapes."""
    if rec.get("type") == "attachment":
        denial = denials.hook_denial_key(rec)
        if denial is None:
            return
        tool_use_id, attachment = denial
        if not isinstance(attachment, dict):
            return
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
            skill_input = block.get("input")
            if block.get("name") == "Skill" and not (isinstance(skill_input, dict) and isinstance(skill_input.get("skill"), str)):
                continue  # _skill_block_round_skill raises on a Skill block whose input or skill is not the expected type
            if review_rounds._skill_block_round_skill([block]) == author_outcome._CODE_REVIEW_SKILL:
                code_review_skill_ids.add(block["id"])
    return tool_use_ids, code_review_skill_ids


def _record_routing_denies(scan: _ScanFacts, records: list[dict], record_branches: list[str]) -> set[str]:
    """Add this session's routing-gate denies to `scan`, deduped by non-empty tool_use_id, and return those ids.

    A tool_use_id that is not a string counts as empty.

    A deny the harness attests with a `hookName` counts as it stands. A deny identified only by its text
    signature counts only when its tool_use_id is a `Skill(code-review)` tool_use of the same session, so an
    ordinary tool error that carries the gate's wording does not. A dropped one whose id matches no tool_use of
    the session sets `scan.dropped_deny_without_tool_use`, because the deny may be real with its tool_use elsewhere.
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
            scan.denies.append(_DenyFact(record_branches[idx], corpus._parse_ts(rec.get("timestamp"))))
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
        if facts.last_record_ts is None or ts > facts.last_record_ts:
            facts.last_record_ts = ts


def _opens_with_denied_skill(rec: dict, denied_tool_use_ids: Collection[str]) -> bool:
    return any(
        isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "Skill"
        and isinstance(block.get("id"), str) and block["id"] in denied_tool_use_ids
        for block in (rec.get("message") or {}).get("content") or []
    )


def _add_session_facts(scan: _ScanFacts, jsonl: Path, raw_records: list[dict]) -> None:
    """Fold one session into `scan` as derived facts. Nothing here keeps a reference to a record."""
    records = pricing.dedup_turns_by_request_id(raw_records)
    windows = review_rounds.detect_round_windows(records)
    record_branches = review_rounds._session_record_branches(records, windows)
    _record_branch_activity(scan, records, record_branches)
    denied_tool_use_ids = _record_routing_denies(scan, records, record_branches)
    code_review_spans = dict(author_outcome._code_review_rounds(records))
    if not code_review_spans:
        return

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
            open_idx=open_idx,
            open_ts=corpus._parse_ts(entry["ts"]),
            has_marker_write=author_outcome._round_has_marker_write(
                records, open_idx, code_review_spans[open_idx], orchestrator_spawns,
            ),
            context_at_open=turns[open_turn][2] if open_turn < len(turns) else None,
            context_at_window_end=turns[end_turn][2] if end_turn < len(turns) else None,
            later_turns=dict(Counter(model for _idx, model in outside_turns[first_later:])),
            overhead_tokens=_nest_token_sink(entry["tokens"]),
            degraded=entry["dangling"] > 0,
            denied_open=_opens_with_denied_skill(open_rec, denied_tool_use_ids),
            spawn_open=review_rounds.round_opened_by_spawn(open_rec),
        ))
    if unreadable_counter[author_outcome._DQ_ORCHESTRATOR_TRANSCRIPT_UNREADABLE] > 0:
        for branch in round_branches:
            scan.branches.setdefault(branch, _BranchFacts()).orchestrator_unreadable = True


def _scan_sessions(root: Path, capture_scope: _CaptureScope, scan_gaps: Counter[str]) -> _ScanFacts:
    scan = _ScanFacts()
    announced_project_dirs: set[Path] = set()
    # include_subagents is False because every figure reads main-thread turns.
    for jsonl, raw_records in scope._iter_scoped_sessions(capture_scope.slugs, False, [root], scan_gaps=scan_gaps):
        if jsonl.parent not in announced_project_dirs:
            announced_project_dirs.add(jsonl.parent)
            total = max(capture_scope.project_dir_count, len(announced_project_dirs))
            print(f"{_COMMAND}: scanning project directory {len(announced_project_dirs)} of {total}", file=sys.stderr)
        _add_session_facts(scan, jsonl, raw_records)
    return scan


# --- loops and the whole verdict ---------------------------------------------


def _whole_reason(branch_facts: _BranchFacts | None, rounds: Sequence[_RoundFacts]) -> str | None:
    """Why a branch cannot be captured whole, or None when it can. The first failing condition names the reason."""
    if branch_facts is None or not branch_facts.has_timestamped_record:
        return _REASON_NO_RECORDS
    if not any(round_facts.has_marker_write for round_facts in rounds):
        return _REASON_NO_COMPLETE_LOOP
    if branch_facts.orchestrator_unreadable:
        return _REASON_ORCHESTRATOR_UNREADABLE
    return None


def _round_sort_key(round_facts: _RoundFacts) -> tuple[float, str, int]:
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
    """Context at the last round's window end less context at the first round's open, or None across sessions."""
    if first_round.session_key != last_round.session_key:
        return None
    if first_round.context_at_open is None or last_round.context_at_window_end is None:
        return None
    return last_round.context_at_window_end - first_round.context_at_open


def _joined_denied_opens(rounds: Sequence[_RoundFacts]) -> int:
    """How many denied round-opens have a later orchestrator-spawn round in the same loop."""
    return sum(
        1 for position, round_facts in enumerate(rounds)
        if round_facts.denied_open and any(later.spawn_open for later in rounds[position + 1:])
    )


def _assemble_loops(rounds: Sequence[_RoundFacts]) -> list[_Loop]:
    """A branch's loops: each is the run of rounds up to and including the first one whose span writes the marker.

    Rounds sort by open time, session path and open index, the order compute_review_round_costs uses.
    Rounds after the last marker write belong to no loop and are dropped.
    """
    loops: list[_Loop] = []
    open_loop: list[_RoundFacts] = []
    for round_facts in sorted(rounds, key=_round_sort_key):
        open_loop.append(round_facts)
        if not round_facts.has_marker_write:
            continue
        loops.append(_Loop(
            overhead_tokens=_merge_overhead_tokens(open_loop),
            growth_tokens=_loop_growth_tokens(open_loop[0], open_loop[-1]),
            later_turns=dict(round_facts.later_turns),
            degraded=any(member.degraded for member in open_loop),
            round_count=len(open_loop),
            joined_denied_opens=_joined_denied_opens(open_loop),
        ))
        open_loop = []
    return loops


def _build_row(rounds: Sequence[_RoundFacts]) -> _BuiltRow:
    loops = _assemble_loops(rounds)
    return _BuiltRow(_serialize_loops(loops), loops)


# --- deny reconciliation -----------------------------------------------------


def _utc_minute(ts: float | None) -> str:
    if ts is None:
        return "an unknown time"
    return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%d %H:%M UTC")


def _earliest_ts(timestamps: Iterable[float | None]) -> float | None:
    known = [ts for ts in timestamps if ts is not None]
    return min(known) if known else None


def _reconcile_denies(
    denies: Sequence[_DenyFact],
    note_rows: Sequence[_NoteRow],
    account_config_dir: Path,
    built_line_nos: Collection[int],
    branch_facts: Mapping[str, _BranchFacts],
) -> _DenyReconciliation:
    """Check this root's routing-gate denies against the note, so an unlisted branch with a deny here cannot shrink an arm.

    A deny on an off row of this account, or on a branch the note does not list, is a refusal.
    A deny on a branch listed for another config dir is not, because no note edit could clear it.
    The two row notices fire only for rows in `built_line_nos`, the rows this run builds.
    """
    rows_by_branch = {row.branch: row for row in note_rows}
    refusals: list[str] = []
    notices: list[str] = []
    denied_branches = {deny.branch for deny in denies}
    if any(not deny.branch or deny.branch in scope._DEFAULT_BRANCH_CANDIDATES for deny in denies):
        notices.append(f"{_COMMAND}: a routing deny on an empty or default-branch name cannot belong to a sampled branch")

    refused_off_rows: set[int] = set()
    unlisted_branches: dict[str, float | None] = {}
    for deny in denies:
        row = rows_by_branch.get(deny.branch)
        if row is None:
            if deny.branch and deny.branch not in scope._DEFAULT_BRANCH_CANDIDATES and deny.branch not in unlisted_branches:
                unlisted_branches[deny.branch] = _earliest_ts(d.ts for d in denies if d.branch == deny.branch)
        elif row.resolved_config_dir == account_config_dir and row.arm == _ARM_OFF and row.line_no not in refused_off_rows:
            refused_off_rows.add(row.line_no)
            refusals.append(
                f"{_COMMAND}: note line {row.line_no} is listed off but this root holds a routing deny for it;"
                " the branch was worked with the key on;"
                ' see docs/transcript-analysis.md routing-guard "Deny reconciliation" for the sanctioned note edit'
            )
    for earliest_ts in unlisted_branches.values():
        refusals.append(
            f"{_COMMAND}: a routing deny at {_utc_minute(earliest_ts)} falls on a branch the note does not list;"
            " in a terminal outside any agent session, run review-trace with the same top-level --config-dir"
            " given to capture and --deny-only, once with --this-repo and, if no row matches that minute,"
            f" once with --projects='<main-project-slug>{_WORKTREE_SLUG_INFIX}*' for a removed worktree's"
            " sessions; match the row at that minute, and list its branch as on if it merged, else as unmerged;"
            ' see docs/transcript-analysis.md routing-guard "Deny reconciliation"'
        )

    first_deny_ts = _earliest_ts(deny.ts for deny in denies)
    for row in note_rows:
        if row.resolved_config_dir != account_config_dir:
            if row.branch in branch_facts or row.branch in denied_branches:
                notices.append(
                    f"{_COMMAND}: note line {row.line_no} is listed for another config directory"
                    " but has records on this root; its rounds are split across accounts"
                )
            continue
        if row.line_no not in built_line_nos:
            continue
        if row.arm == _ARM_ON and row.branch not in denied_branches:
            notices.append(
                f"{_COMMAND}: note line {row.line_no} is listed on but no routing deny was found for it;"
                " a typed /code-review or an unrouted round leaves none"
            )
        if row.arm == _ARM_OFF:
            last_record_ts = branch_facts[row.branch].last_record_ts if row.branch in branch_facts else None
            if first_deny_ts is not None and last_record_ts is not None and last_record_ts > first_deny_ts:
                notices.append(
                    f"{_COMMAND}: note line {row.line_no} is listed off but has records later than this account's"
                    " first routing deny; the key may have been turned off again"
                )
    return _DenyReconciliation(refusals, notices)


# --- the capture run ---------------------------------------------------------


def _yes_no(flag: bool) -> str:
    return "yes" if flag else "no"


def _row_line(line_no: int, outcome: str) -> str:
    return f"{_COMMAND}: note line {line_no}: {outcome}"


def _captured_line(line_no: int, built: _BuiltRow) -> str:
    rounds = sum(loop.round_count for loop in built.loops)
    growth_defined = sum(1 for loop in built.loops if loop.growth_tokens is not None)
    joined = sum(loop.joined_denied_opens for loop in built.loops)
    degraded = any(loop.degraded for loop in built.loops)
    return _row_line(
        line_no,
        f"captured ({rounds} rounds, {len(built.loops)} loops, {growth_defined} with growth defined,"
        f" {joined} denied round-opens joined, degraded: {_yes_no(degraded)})",
    )


def _standing_line(line_no: int, standing: bytes | _StandingFile) -> tuple[str, bool]:
    """The per-row line for an existing file, and whether the row is good."""
    if isinstance(standing, _StandingFile):
        return _row_line(line_no, "invalid"), False
    try:
        loops = _parse_capture_bytes(standing)
    except _InvalidCapture:
        return _row_line(line_no, "invalid"), False
    growth_defined = sum(1 for loop in loops if loop["growth_tokens"] is not None)
    degraded = any(loop["degraded"] for loop in loops)
    return _row_line(
        line_no,
        f"already captured ({len(loops)} loops, {growth_defined} with growth defined, degraded: {_yes_no(degraded)})",
    ), True


def _exit_publish_failed(outcome: ledger_common._PublishOutcome, published_count: int) -> NoReturn:
    """Exit 4 for any outcome that left no file published. An outcome this function does not name reads "published"."""
    action = {
        ledger_common._PublishOutcome.WRITE_FAILED: "written",
        ledger_common._PublishOutcome.LINK_FAILED: "linked into place",
    }.get(outcome, "published")
    _refuse(
        f"{_COMMAND}: a capture file could not be {action}; this run published {published_count} files,"
        " and a re-run reports them already captured",
        4,
    )


def _refuse_unsafe_captures_dir(captures_dir: Path) -> None:
    """Exit 2 when capture files would land inside a git work tree, or the directory is a symlink.

    Runs before the scan and again just before the first publish, since the scan can run long.
    """
    if ledger_common._ledger_path_is_git_tracked(captures_dir.resolve() / _GIT_TREE_CHECK_LEAF, _COMMAND):
        _refuse(
            f"{_COMMAND}: the captures directory is inside a git working tree; capture files are never"
            " written inside one",
            2,
        )
    if captures_dir.is_symlink():
        _refuse(f"{_COMMAND}: the captures directory is a symlink; capture files are never written through one", 2)


def _refuse_unless_still_opted_in(account_dir: Path) -> None:
    """Exit 1 unless `account_dir`'s `pr_cost_recording` still resolves on.

    Runs again just before the first publish, since the scan can run long and the owner can revoke consent meanwhile.
    """
    opt_in = _classify_capture_opt_in(account_dir)
    if opt_in is not _OptInOutcome.OPTED_IN:
        _refuse(_OPT_IN_REFUSALS[opt_in], 1)


def _capture_account_rows(
    account_rows: Sequence[_NoteRow], standing_by_line: Mapping[int, bytes | _StandingFile],
    scan: _ScanFacts, account_dir: Path,
) -> bool:
    """Build, publish and report each row. Returns whether every row is captured or already captured."""
    captures_dir = account_dir / _CAPTURES_DIR_NAME
    published_count = 0
    captures_dir_ready = False
    all_good = True
    for row in account_rows:
        path = captures_dir / _capture_file_name(row.branch, _CAPTURE_REVISION)
        standing = standing_by_line[row.line_no]
        if standing is _StandingFile.ABSENT:
            rounds = scan.rounds_by_branch.get(row.branch, [])
            reason = _whole_reason(scan.branches.get(row.branch), rounds)
            if reason is not None:
                print(_row_line(row.line_no, f"missing ({reason})"))
                all_good = False
                continue
            built = _build_row(rounds)
            text_bytes = built.text.encode("utf-8")
            try:
                _parse_capture_bytes(text_bytes)
            except _InvalidCapture:
                _refuse(
                    f"{_COMMAND}: internal error: the built row failed validation; this run published"
                    f" {published_count} files, and a re-run reports them already captured",
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
                print(_captured_line(row.line_no, built))
                continue
            if outcome is not ledger_common._PublishOutcome.EXISTS:
                _exit_publish_failed(outcome, published_count)
            standing = _read_standing_capture(path)
            if standing is _StandingFile.ABSENT:
                standing = _StandingFile.UNREADABLE
        line, row_is_good = _standing_line(row.line_no, standing)
        print(line)
        all_good = all_good and row_is_good
    return all_good


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

    capturable_rows = [row for row in account_rows if row.arm in (_ARM_ON, _ARM_OFF)]
    standing_by_line = {
        row.line_no: _read_standing_capture(captures_dir / _capture_file_name(row.branch, _CAPTURE_REVISION))
        for row in capturable_rows
    }
    built_line_nos = {line_no for line_no, standing in standing_by_line.items() if standing is _StandingFile.ABSENT}
    reconciliation = _reconcile_denies(scan.denies, note_rows, resolved_account_dir, built_line_nos, scan.branches)
    if scan.dropped_deny_without_tool_use:
        print(_DROPPED_DENY_NOTICE, file=sys.stderr)
    for notice in reconciliation.notices:
        print(notice, file=sys.stderr)
    if reconciliation.refusals:
        for refusal in reconciliation.refusals:
            print(refusal, file=sys.stderr)
        sys.exit(2)

    all_good = _capture_account_rows(capturable_rows, standing_by_line, scan, account_dir)
    drift = pricing._format_drift_detected()
    if drift:
        print(_PRICING_INTEGRITY_LINE)
    sys.exit(0 if all_good and not drift else 3)


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
    """Whether the account's key resolves true and its captures directory exists.

    The key is checked first, so an account with its key off has none of its files opened.
    """
    if _classify_capture_opt_in(config_dir) is not _OptInOutcome.OPTED_IN:
        return False
    return (config_dir / _CAPTURES_DIR_NAME).is_dir()


def _ready_accounts(note_rows: Sequence[_NoteRow]) -> set[Path]:
    """The config dirs with an `on` row that pass _account_is_ready, checked in note order."""
    accounts_with_on_rows = dict.fromkeys(row.resolved_config_dir for row in note_rows if row.arm == _ARM_ON)
    return {config_dir for config_dir in accounts_with_on_rows if _account_is_ready(config_dir)}


def _load_capture_loops(captures_dir: Path, branch: str) -> list[dict] | _StandingFile:
    """The validated loops of `branch`'s file at the current revision, opened by exact name.

    ABSENT means the name does not exist. Any other failure to read or validate gives UNREADABLE.
    """
    standing = _read_standing_capture(captures_dir / _capture_file_name(branch, _CAPTURE_REVISION))
    if isinstance(standing, _StandingFile):
        return standing
    try:
        return _parse_capture_bytes(standing)
    except _InvalidCapture:
        return _StandingFile.UNREADABLE


def _load_sample_files(note_rows: Sequence[_NoteRow], ready: Collection[Path]) -> dict[int, list[dict] | _StandingFile]:
    """Each `on` and `off` row's file, for the rows of a ready account only."""
    return {
        row.line_no: _load_capture_loops(row.resolved_config_dir / _CAPTURES_DIR_NAME, row.branch)
        for row in note_rows
        if row.arm in (_ARM_ON, _ARM_OFF) and row.resolved_config_dir in ready
    }


def _has_growth_defined_loop(rows: Sequence[_NoteRow], loaded_loops: Mapping[int, list[dict]]) -> bool:
    return any(loop["growth_tokens"] is not None for row in rows for loop in loaded_loops[row.line_no])


def _account_reasons(
    account_rows: Sequence[_NoteRow], ready: bool, loaded: Mapping[int, list[dict] | _StandingFile],
) -> list[_NotRunReason]:
    """The NOT RUN reasons one account with `on` rows contributes."""
    on_rows = [row for row in account_rows if row.arm == _ARM_ON]
    off_rows = [row for row in account_rows if row.arm == _ARM_OFF]
    reasons: list[_NotRunReason] = []
    if ready:
        for row in [*on_rows, *off_rows]:
            fetched = loaded[row.line_no]
            if fetched is _StandingFile.ABSENT:
                reasons.append(_NotRunReason(_REASON_FILE_MISSING, row.line_no))
            elif fetched is _StandingFile.UNREADABLE:
                reasons.append(_NotRunReason(_REASON_FILE_UNUSABLE, row.line_no))
    else:
        reasons.append(_NotRunReason(_REASON_ACCOUNT_NOT_READY, account_rows[0].line_no))
    reference_states = {(row.round_consult_round2_pilot, row.architect_fix_plan_pilot) for row in on_rows}
    if len(reference_states) > 1:
        reasons.append(_NotRunReason(_REASON_MIXED_PILOTS, on_rows[0].line_no))
    else:
        reasons.extend(
            _NotRunReason(_REASON_OFF_PILOT_DIFFERS, row.line_no)
            for row in off_rows
            if (row.round_consult_round2_pilot, row.architect_fix_plan_pilot) not in reference_states
        )
    if len(off_rows) != len(on_rows):
        reasons.append(_NotRunReason(_REASON_ARM_SIZE_MISMATCH, account_rows[0].line_no))
    return reasons


def _select_sample(
    note_rows: Sequence[_NoteRow], ready: Collection[Path], loaded: Mapping[int, list[dict] | _StandingFile],
) -> _SampleSelection:
    """The sampled rows of each arm and every NOT RUN reason the sample checks find.

    `off` rows of an account with no `on` row, and every `unmerged` row, are ignored. The growth check
    runs only when no other reason holds, since a row that failed to load would otherwise read as an arm
    without growth.
    """
    rows_by_account: dict[Path, list[_NoteRow]] = {}
    for row in note_rows:
        rows_by_account.setdefault(row.resolved_config_dir, []).append(row)
    reasons: list[_NotRunReason] = []
    if sum(1 for row in note_rows if row.arm == _ARM_ON) < _MIN_ON_BRANCHES:
        reasons.append(_NotRunReason(_REASON_TOO_FEW_ON, None))
    on_rows: list[_NoteRow] = []
    off_rows: list[_NoteRow] = []
    loaded_loops: dict[int, list[dict]] = {}
    for config_dir, account_rows in rows_by_account.items():
        if not any(row.arm == _ARM_ON for row in account_rows):
            continue
        reasons.extend(_account_reasons(account_rows, config_dir in ready, loaded))
        for row in account_rows:
            fetched = loaded.get(row.line_no)
            if isinstance(fetched, list):
                loaded_loops[row.line_no] = fetched
                (on_rows if row.arm == _ARM_ON else off_rows).append(row)
    if not reasons and not (
        _has_growth_defined_loop(on_rows, loaded_loops) and _has_growth_defined_loop(off_rows, loaded_loops)
    ):
        reasons.append(_NotRunReason(_REASON_NO_GROWTH_DEFINED, None))
    reasons.sort(key=lambda found: (_NOT_RUN_REASONS.index(found.reason), found.line_no or 0))
    return _SampleSelection(on_rows, off_rows, loaded_loops, reasons)


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
    """One branch's scalars from its validated loops. A loop with a skipped model counts as degraded.

    Later turns are priced only for a key-off branch.
    """
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
    """The net per loop, or None when an arm has no loop whose growth is defined, which leaves no gap.

    The net is the key-on overhead per loop less the key-off overhead per loop, less the growth gap
    times the key-off carry rate sum per loop.
    """
    if on.growth_defined_loops == 0 or off.growth_defined_loops == 0:
        return None
    overhead_difference = on.overhead_dollars / on.loop_count - off.overhead_dollars / off.loop_count
    gap = off.growth_sum / off.growth_defined_loops - on.growth_sum / on.growth_defined_loops
    carry = gap * off.carry_rate_sum / off.loop_count
    return _NetFigures(overhead_difference - carry, overhead_difference, carry, gap)


def _arm_net(on: Iterable[_BranchScalars], off: Iterable[_BranchScalars]) -> _NetFigures | None:
    """The net per loop over two multisets of branches."""
    return _net_from_totals(_arm_totals(on), _arm_totals(off))


def _bootstrap_net(
    on: Sequence[_BranchScalars], off: Sequence[_BranchScalars], *, replicates: int, rng: random.Random,
) -> _BootstrapResult:
    """The percentile bootstrap of the net over branches resampled within each arm.

    Each arm is sorted by digest before drawing, so the result does not depend on note order. A
    replicate whose resample leaves an arm with no growth-defined loop has no net. It is never
    redrawn and ranks above every defined net as math.inf, so the replicate count is exact and the
    lower bound can only rise. The replicate count and the RNG are required; every module constant
    is read when the call runs.
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
    """The `--out` file: the banner, then every figure. An upper bound on an undefined replicate reads `unbounded`."""
    lines = [
        _DO_NOT_PUBLISH_BANNER,
        f"net per loop: {render._fmt_usd(figures.net)}",
        f"{_GUARD_CI_LEVEL:.0%} interval: {render._fmt_usd(bootstrap.lower)} to {_format_upper_bound(bootstrap.upper)}",
        f"overhead difference per loop: {render._fmt_usd(figures.overhead_difference)}",
        f"carry per loop: {render._fmt_usd(figures.carry)}",
        f"growth gap, key-off minus key-on, tokens per loop: {figures.gap:,.1f}",
        *_arm_figure_lines(_ARM_ON, on),
        *_arm_figure_lines(_ARM_OFF, off),
        f"undefined replicates: {bootstrap.undefined_total} of {bootstrap.replicates}",
    ]
    return "\n".join(lines) + "\n"


def _print_compute_notices(
    on: Sequence[_BranchScalars], off: Sequence[_BranchScalars], bootstrap: _BootstrapResult,
) -> None:
    for arm, branches, undefined_replicates in (
        (_ARM_ON, on, bootstrap.undefined_on), (_ARM_OFF, off, bootstrap.undefined_off),
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
    if bootstrap.lower > 0:
        print(_STATUS_FAILED)
        sys.exit(1)
    print(_STATUS_PASSED)
    sys.exit(0)


def _innermost_frame_name(exc: BaseException) -> str:
    """The function name of the deepest traceback frame. Never a file path, line number or exception text."""
    innermost = exc.__traceback__
    if innermost is None:
        return "unknown"
    while innermost.tb_next is not None:
        innermost = innermost.tb_next
    return innermost.tb_frame.f_code.co_name


def cmd_routing_guard(args: argparse.Namespace) -> None:
    """Capture each sampled branch's review loops with `--capture`, else compute the guard from the captured files.

    Every exit goes through sys.exit, because main() ignores a handler's return value. One
    except clause maps an unexpected failure to exit 4, so a crash never reads as a refusal.
    Only a fixed message, an OSError's strerror, or an unexpected failure's exception class and innermost
    function name is printed, never a traceback.
    """
    try:
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
    """Register the routing-guard subparser on build_parser()'s subparsers action,
    binding cmd_routing_guard through set_defaults(func=...).
    """
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
            " config dir, branch, on|off|unmerged and the two pilot states."
        ),
    )
    p_routing_guard.add_argument(
        "--capture", action="store_true",
        help=(
            "Publish a write-once file for each `on` and `off` row of the --config-dir account that is captured whole"
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
