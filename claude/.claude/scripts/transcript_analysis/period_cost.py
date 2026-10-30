"""The period-cost command: cmd_period_cost and every helper used only by it -- per-PR and per-review Claude Code
spend for an inclusive UTC date range across every repository the active account worked in, as Slack mrkdwn.

Imports corpus, cost, gh_cli, pr_cost_ledger, pricing, render, review_rounds, and scope by module
(attribute access, not by name) -- see scope.py's own top-of-file comment for why.
"""
from __future__ import annotations

import argparse
import enum
import math
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path, PurePosixPath
from typing import Any, NamedTuple, NoReturn

from transcript_analysis import corpus, cost, gh_cli, pr_cost_ledger, pricing, render, review_rounds, scope

_SECONDS_PER_DAY = 86400

# 480 s is the 600 s foreground Bash ceiling (the Bash tool's `timeout` parameter: "max 600000 for a foreground
# command", in milliseconds), less one in-flight bulk list call (gh_cli._GH_PR_LIST_TIMEOUT_S, the longest gh call
# this command makes) and a 30 s operational margin for assembly, rendering, and interpreter start (not measured).
_RUN_DEADLINE_S = 480

# A retry would first wait GitHub's minimum between retries (gh_cli._PR_COST_RATE_LIMIT_MIN_BACKOFF_S, 60 s), which an
# account with more than a few repositories cannot afford inside _RUN_DEADLINE_S, so a failed list call is reported
# rather than retried.
_PR_LIST_MAX_ATTEMPTS = 1

# github.com is the one host whose repositories are listed with no stored-login check.
_GITHUB_HOST = "github.com"

_NOTICE_SCOPE = "SCOPE"
_NOTICE_SHARING = "SHARING"
_NOTICE_COVERAGE_WARNING = "COVERAGE WARNING"
_NOTICE_NO_ACTIVITY = "NO ACTIVITY"
_NOTICE_EXCLUDED_SPEND = "EXCLUDED SPEND"
_NOTICE_STALE_PRICING = "STALE PRICING"
_NOTICE_PRICING_INTEGRITY = "PRICING INTEGRITY"
_NOTICE_REVIEW_WINDOW_FALLBACK = "REVIEW WINDOW FALLBACK"
_NOTICE_GH_UNAVAILABLE = "GH UNAVAILABLE"
_NOTICE_PR_LIST_LIMIT = "PR LIST LIMIT"
_NOTICE_OTHER_BREAKDOWN = "OTHER BREAKDOWN"
_NOTICE_TAGS = (
    _NOTICE_SCOPE, _NOTICE_SHARING, _NOTICE_COVERAGE_WARNING, _NOTICE_NO_ACTIVITY, _NOTICE_EXCLUDED_SPEND,
    _NOTICE_STALE_PRICING, _NOTICE_PRICING_INTEGRITY, _NOTICE_REVIEW_WINDOW_FALLBACK, _NOTICE_GH_UNAVAILABLE,
    _NOTICE_PR_LIST_LIMIT, _NOTICE_OTHER_BREAKDOWN,
)
# Bounds the entry count of the SCOPE repository list, the OTHER BREAKDOWN branch and review lists, REVIEW WINDOW FALLBACK,
# and GH UNAVAILABLE. Entry length is not bounded, and EXCLUDED SPEND is not capped.
_NOTICE_LIST_CAP = 10

_REPORT_FIRST_LINE_PREFIX = "*Claude Code spend, "
_UNDETERMINED_GROUP_LABEL = "Repository not determined"
_OTHER_ROW_LABEL_LISTED = "Work not linked to a listed PR"
_OTHER_ROW_LABEL_UNDETERMINED = "Work not linked to a repository"
_TITLE_UNAVAILABLE = "(title unavailable)"
_NO_MODEL_FIELD_LABEL = "(no model field)"
_REVIEW_TITLE_PREFIX = "Review: "
_TITLE_CELL_CAP = 32
_PR_LIST_UNAVAILABLE_SUFFIX = " (PR list unavailable)"

_REVIEW_PR_SKILL_NAME = "review-pr"
_ACQUIRE_SCRIPT_NAME = "review-pr-acquire.sh"
_FINISH_SCRIPT_NAME = "review-pr-finish.sh"
# [0-9] rather than \d, which also matches non-ASCII digits that _lib_parse_pr_identity's ^[0-9]+$ rejects.
_PR_NUMBER_RE = re.compile(r"[0-9]+")
_CLOSE_KIND_NEXT_REVIEW = "next review"
_CLOSE_KIND_FRESH_PROMPT = "next prompt"
_CLOSE_KIND_SESSION_END = "session end"

# gh pr list's state names are uppercase; the report shows only these lowercased forms.
_SHOWN_PR_STATES = frozenset({"open", "closed", "merged"})
_PR_LIST_JSON_FIELDS = "number,title,state,headRefName,isCrossRepository"

_GH_KIND_UNPARSEABLE = "unparseable"
_GH_KIND_UNEXPECTED_PAYLOAD = "unexpected payload"
_GH_KIND_NO_WORKING_LOGIN = "no working login"
_GH_KIND_GH_MISSING = "gh missing"
_GH_KIND_TIME_BUDGET = "time budget"
_GH_STATUS_TO_KIND: dict[str, str] = {
    gh_cli._GH_CALL_DEGRADED_AUTH: gh_cli._GH_ERROR_KIND_AUTH,
    gh_cli._GH_CALL_DEGRADED_HOST_MISMATCH: gh_cli._GH_ERROR_KIND_HOST_MISMATCH,
    pr_cost_ledger._PR_COST_STATUS_DEGRADED_RATE_LIMIT: gh_cli._GH_ERROR_KIND_RATE_LIMIT,
    pr_cost_ledger._PR_COST_STATUS_DEGRADED_NETWORK: gh_cli._GH_ERROR_KIND_NETWORK,
    gh_cli._GH_PR_LIST_UNPARSEABLE: _GH_KIND_UNPARSEABLE,
}

# Tolerance for float64 summation noise, as cost._cost_report uses for its own conservation checks.
_CONSERVATION_TOLERANCE = 1e-6

_ROOT_LISTING_FAILURE_MESSAGE = (
    "period-cost: could not list the active account's projects directory -- no report written"
)
_CONFIG_DIR_UNRESOLVABLE_MESSAGE = (
    "period-cost: the active config directory could not be resolved -- CLAUDE_CONFIG_DIR must be an absolute path, "
    "or HOME must be set -- no report written"
)
_GIT_MISSING_MESSAGE = (
    "period-cost: git is not installed or not on PATH, so no directory's repository can be read -- no report written"
)

_Identity = tuple[str, str]


def _utc_today() -> date:
    return datetime.now(UTC).date()


def _monotonic() -> float:
    return time.monotonic()


class _PeriodWindow(NamedTuple):
    """An inclusive UTC day range: `since_ts <= ts < until_epoch`. `partial` is true when `until` is today or later."""

    since: str
    until: str
    since_ts: float
    until_epoch: float
    partial: bool


def _refuse(message: str) -> NoReturn:
    print(f"period-cost: {message}", file=sys.stderr)
    sys.exit(2)


def _utc_day(epoch: float) -> date:
    return datetime.fromtimestamp(epoch, tz=UTC).date()


def _window_from_bounds(since_ts: float, until_epoch: float, today: date) -> _PeriodWindow:
    since_day = _utc_day(since_ts)
    until_day = _utc_day(until_epoch - _SECONDS_PER_DAY)
    return _PeriodWindow(
        since=since_day.isoformat(), until=until_day.isoformat(), since_ts=since_ts, until_epoch=until_epoch,
        partial=until_day >= today,
    )


def _validated_window(args: argparse.Namespace, today: date) -> _PeriodWindow | None:
    """The requested window, or None for `--sample`. Exits 2 on every refusal; a message names the flag and the
    zero-padded YYYY-MM-DD form and never echoes the value."""
    since: str | None = getattr(args, "since", None) or None
    until: str | None = getattr(args, "until", None) or None
    if bool(getattr(args, "sample", False)):
        if since is not None or until is not None:
            _refuse("--sample cannot be combined with --since or --until")
        return None
    missing = [flag for flag, value in (("--since", since), ("--until", until)) if value is None]
    if missing:
        verb = "is" if len(missing) == 1 else "are"
        _refuse(f"{' and '.join(missing)} {verb} required (zero-padded YYYY-MM-DD dates), or pass --sample")
    since_ts, until_epoch = scope._parse_absolute_window_args(args, "period-cost")
    if since_ts is None or until_epoch is None:
        unparsed = [flag for flag, bound in (("--since", since_ts), ("--until", until_epoch)) if bound is None]
        _refuse(f"{' and '.join(unparsed)} must be a zero-padded YYYY-MM-DD date")
    window = _window_from_bounds(since_ts, until_epoch, today)
    if since_ts > until_epoch - _SECONDS_PER_DAY:
        _refuse("--since must not be after --until (zero-padded YYYY-MM-DD dates)")
    if _utc_day(since_ts) > today:
        _refuse("--since must not be after today's UTC date (zero-padded YYYY-MM-DD)")
    return window


def _projects_root() -> Path:
    return scope.config_dir() / "projects"


def _account_source() -> str:
    """Where the active account's config directory came from, named without its path."""
    return "CLAUDE_CONFIG_DIR" if os.environ.get("CLAUDE_CONFIG_DIR") else "the default config directory"


# ---------------------------------------------------------------------------------------------------------------
# Review windows and anchor directories
# ---------------------------------------------------------------------------------------------------------------


def _conventional_worktree_main_checkout(path: str) -> str | None:
    """The main checkout for the last `.claude/worktrees/<name>` segment of an absolute path at or under one, or None."""
    if not os.path.isabs(path):
        return None
    parts = PurePosixPath(path).parts
    for index in range(len(parts) - 3, 0, -1):
        if parts[index] == ".claude" and parts[index + 1] == "worktrees":
            return str(PurePosixPath(*parts[:index]))
    return None


def _valid_cwd(rec: dict) -> str | None:
    """A record's `cwd` when it is a string holding an absolute path, else None."""
    cwd = rec.get("cwd")
    return cwd if isinstance(cwd, str) and os.path.isabs(cwd) else None


def _record_blocks(rec: dict) -> list:
    message = rec.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    return content if isinstance(content, list) else []


def _is_review_pr_invocation(rec: dict) -> bool:
    """True for a Skill tool_use or a /slash user record naming review-pr. Only input["skill"] is read; input["args"]
    can carry an absolute local path and is never touched."""
    rtype = rec.get("type")
    if rtype == "assistant":
        for block in _record_blocks(rec):
            if not isinstance(block, dict) or block.get("type") != "tool_use" or block.get("name") != "Skill":
                continue
            block_input = block.get("input")
            raw_skill = block_input.get("skill") if isinstance(block_input, dict) else None
            if isinstance(raw_skill, str) and review_rounds._round_skill_name(raw_skill) == _REVIEW_PR_SKILL_NAME:
                return True
        return False
    if rtype == "user":
        message = rec.get("message")
        content = message.get("content", "") if isinstance(message, dict) else ""
        content_text = content if isinstance(content, str) else render._content_text(content)
        return any(
            review_rounds._round_skill_name(match.group(1)) == _REVIEW_PR_SKILL_NAME
            for match in review_rounds._SLASH_COMMAND_RE.finditer(content_text)
        )
    return False


def _acquire_identity(segment: Sequence[str]) -> tuple[str, int] | None:
    """(lowercased owner/repo, PR number) from a `review-pr-acquire.sh <owner>/<repo>#<number>` shell segment, else None.

    Mirrors _lib_parse_pr_identity in claude/.claude/hooks/_lib.sh: the text after the last `#` must be all digits and
    the text before it a plain owner/repo. The owner/repo check runs before lowercasing, since str.lower maps U+212A to k.
    """
    if len(segment) < 2 or os.path.basename(segment[0]) != _ACQUIRE_SCRIPT_NAME:
        return None
    owner_repo, separator, number_text = segment[1].rpartition("#")
    if not separator or _PR_NUMBER_RE.fullmatch(number_text) is None or not gh_cli._is_plain_owner_repo(owner_repo):
        return None
    try:
        number = int(number_text)
    except ValueError:  # int() refuses a digit string past Python's integer-conversion length limit.
        return None
    return owner_repo.lower(), number


def _invokes_finish_script(segment: Sequence[str]) -> bool:
    """True when the whole shell segment is the bare `review-pr-finish.sh` path; a trailing redirect is not detected."""
    return len(segment) == 1 and os.path.basename(segment[0]) == _FINISH_SCRIPT_NAME


@dataclass
class _ReviewWindow:
    """One /review-pr review: the main-thread records in [open_idx, close_idx) plus the sidechain records
    timestamped in [start_ts, end_ts). `fallback_close_kind` is set when the window closed with no finish call."""

    open_idx: int
    close_idx: int = -1
    identity: tuple[str, int] | None = None
    finished: bool = False
    fallback_close_kind: str | None = None
    window_directory: str | None = None
    start_ts: float | None = None
    end_ts: float = math.inf
    absorbed_dollars: float = 0.0


def _main_thread_records(records: Sequence[dict], start: int, stop: int | None = None) -> Iterator[dict]:
    for rec in records[start:stop]:
        if not bool(rec.get("isSidechain")):
            yield rec


def _close_review_window(
    window: _ReviewWindow, close_idx: int, records: Sequence[dict], close_kind: str,
) -> _ReviewWindow:
    """Fix the window's span at `close_idx`. `close_kind` is recorded only when the window never saw a finish call."""
    window.close_idx = close_idx
    window.fallback_close_kind = None if window.finished else close_kind
    inside = list(_main_thread_records(records, window.open_idx, close_idx))
    window.window_directory = next((cwd for rec in inside if (cwd := _valid_cwd(rec)) is not None), None)
    window.start_ts = next((ts for rec in inside if (ts := corpus._parse_ts(rec.get("timestamp"))) is not None), None)
    window.end_ts = next(
        (ts for rec in _main_thread_records(records, close_idx) if (ts := corpus._parse_ts(rec.get("timestamp"))) is not None),
        math.inf,
    )
    return window


def _scan_window_bash(window: _ReviewWindow, rec: dict) -> None:
    """Record the window's first valid acquire identity and whether a finish call ran, from one record's Bash blocks."""
    if rec.get("type") != "assistant":
        return
    for block in _record_blocks(rec):
        if not isinstance(block, dict) or block.get("type") != "tool_use" or block.get("name") != "Bash":
            continue
        block_input = block.get("input")
        command = block_input.get("command") if isinstance(block_input, dict) else None
        if not isinstance(command, str):
            continue
        for segment in corpus.split_command_segments(command):
            if _invokes_finish_script(segment):
                window.finished = True
            elif window.identity is None:
                window.identity = _acquire_identity(segment)


def _review_pr_windows(records: Sequence[dict]) -> list[_ReviewWindow]:
    """Every /review-pr window in one session's deduped records, in file order. Main-thread records only open, scan,
    and close windows."""
    windows: list[_ReviewWindow] = []
    current: _ReviewWindow | None = None
    for index, rec in enumerate(records):
        if bool(rec.get("isSidechain")):
            continue
        if _is_review_pr_invocation(rec):
            # An invocation before any identity or finish continues the window (a slash command followed by its Skill call).
            if current is None or current.identity is not None or current.finished:
                if current is not None:
                    windows.append(_close_review_window(current, index, records, _CLOSE_KIND_NEXT_REVIEW))
                current = _ReviewWindow(open_idx=index)
        elif current is not None and current.finished and render._is_fresh_user_prompt(rec):
            windows.append(_close_review_window(current, index, records, _CLOSE_KIND_FRESH_PROMPT))
            current = None
            continue
        if current is not None:
            _scan_window_bash(current, rec)
    if current is not None:
        windows.append(_close_review_window(current, len(records), records, _CLOSE_KIND_SESSION_END))
    return windows


class _Anchor(NamedTuple):
    """The directories a record's repository comes from. `successor` is set only when the pre-branch step moved the
    record's branch and the record at the successor entry has a valid `cwd`."""

    own: str | None
    successor: str | None


def _session_anchor_index(records: Sequence[dict], ctx: scope._SessionBranchContext) -> dict[tuple[float | None, str], str]:
    """Map each (timestamp, branch) timeline entry to the `cwd` of the first main-thread record there with a valid `cwd`."""
    index: dict[tuple[float | None, str], str] = {}
    for rec in records:
        if bool(rec.get("isSidechain")):
            continue
        branch, timestamp = scope._timeline_entry(rec, ctx)
        cwd = _valid_cwd(rec)
        if branch is not None and cwd is not None:
            index.setdefault((timestamp, branch), cwd)
    return index


def _anchor_directories(
    rec: dict, ctx: scope._SessionBranchContext, anchor_index: Mapping[tuple[float | None, str], str],
) -> _Anchor:
    """A main-thread record's own anchor is its own `cwd`; a sidechain record's is its dispatching main-thread record's
    `cwd` at its timeline entry, never its own."""
    timeline_branch, timeline_ts = scope._timeline_entry(rec, ctx)
    if bool(rec.get("isSidechain")):
        own = anchor_index.get((timeline_ts, timeline_branch)) if timeline_branch is not None else None
    else:
        own = _valid_cwd(rec)
    run = ctx.pre_branch_run
    remapped = scope._attribute_pre_branch(timeline_branch, timeline_ts, run) != timeline_branch
    successor = anchor_index.get((run.boundary_ts, run.successor_branch)) if remapped and run is not None else None
    return _Anchor(own=own, successor=successor)


# ---------------------------------------------------------------------------------------------------------------
# Stream assignment
# ---------------------------------------------------------------------------------------------------------------


class _BranchKey(NamedTuple):
    own_anchor: str
    successor_anchor: str | None
    branch: str


class _ReviewKey(NamedTuple):
    window_directory: str | None
    owner_repo: str
    number: int


class _FallbackWindow(NamedTuple):
    """A fallback-closed review window that absorbed in-period dollars. `review_key` is None for an unidentified review."""

    start_date: str
    close_kind: str
    review_key: _ReviewKey | None
    directory: str | None
    dollars: float


class _Destination(enum.Enum):
    BRANCH = "branch"
    REVIEW = "review"
    DEFAULT_BRANCH = "default branch"
    UNIDENTIFIED_REVIEW = "unidentified review"
    NO_ANCHOR = "no anchor"


class _Target(NamedTuple):
    """Where one priced record's dollars go. `key` is a _BranchKey, a _ReviewKey, a directory, a (directory, start date)
    pair, or None, by `destination`."""

    destination: _Destination
    key: Any


@dataclass
class _StreamTotals:
    branch_dollars: dict[_BranchKey, float] = field(default_factory=lambda: defaultdict(float))
    review_dollars: dict[_ReviewKey, float] = field(default_factory=lambda: defaultdict(float))
    # Keyed by anchor directory.
    default_dollars: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    # Keyed by (window directory, start date).
    unidentified_review_dollars: dict[tuple[str, str], float] = field(default_factory=lambda: defaultdict(float))
    no_anchor_dollars: float = 0.0
    no_anchor_records: int = 0
    outside_branch_keys: set[_BranchKey] = field(default_factory=set)
    outside_review_keys: set[_ReviewKey] = field(default_factory=set)
    fallback_windows: list[_FallbackWindow] = field(default_factory=list)
    unpriced_tokens: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    priced_models: set[str] = field(default_factory=set)
    undated_turns: int = 0
    invalid_usage_records: int = 0
    earliest_ts: float | None = None
    dated_usage_in_period: bool = False
    in_period_priced_dollars: float = 0.0
    spawns: int = 0
    sidechain_turns: int = 0


def _record_usage(rec: dict) -> dict | None:
    message = rec.get("message")
    usage = message.get("usage") if isinstance(message, dict) else None
    return usage if isinstance(usage, dict) and usage else None


def _record_model_id(rec: dict) -> str:
    message = rec.get("message")
    model = message.get("model") if isinstance(message, dict) else None
    return model if isinstance(model, str) else ""


def _window_for_record(windows: Sequence[_ReviewWindow], index: int, rec: dict, ts: float) -> _ReviewWindow | None:
    """The first window a record belongs to: a main-thread record by index, a sidechain record by timestamp."""
    if bool(rec.get("isSidechain")):
        return next((w for w in windows if w.start_ts is not None and w.start_ts <= ts < w.end_ts), None)
    return next((w for w in windows if w.open_idx <= index < w.close_idx), None)


def _window_review_key(window: _ReviewWindow) -> _ReviewKey | None:
    if window.identity is None:
        return None
    owner_repo, number = window.identity
    return _ReviewKey(window.window_directory, owner_repo, number)


def _window_start_date(window: _ReviewWindow) -> str:
    return render._fmt_date(window.start_ts) if window.start_ts is not None else "undated"


def _review_window_target(window: _ReviewWindow) -> _Target:
    review_key = _window_review_key(window)
    if review_key is not None:
        return _Target(_Destination.REVIEW, review_key)
    if window.window_directory is None:
        return _Target(_Destination.NO_ANCHOR, None)
    return _Target(_Destination.UNIDENTIFIED_REVIEW, (window.window_directory, _window_start_date(window)))


def _branch_target(
    rec: dict, ctx: scope._SessionBranchContext, anchor_index: Mapping[tuple[float | None, str], str],
) -> _Target:
    anchor = _anchor_directories(rec, ctx, anchor_index)
    if anchor.own is None:
        return _Target(_Destination.NO_ANCHOR, None)
    branch = scope._attributed_branch(rec, ctx)
    timeline_branch = scope._timeline_entry(rec, ctx)[0]
    if branch != timeline_branch and anchor.successor is None:
        # A moved record whose successor entry has no usable directory keeps its recorded branch.
        branch = timeline_branch
    if branch is None or branch in scope._DEFAULT_BRANCH_CANDIDATES or scope._names_no_pr_branch(branch):
        return _Target(_Destination.DEFAULT_BRANCH, anchor.own)
    return _Target(_Destination.BRANCH, _BranchKey(anchor.own, anchor.successor, branch))


def _credit_target(totals: _StreamTotals, target: _Target, dollars: float, *, in_period: bool) -> None:
    destination, key = target
    if destination is _Destination.BRANCH:
        if in_period:
            totals.branch_dollars[key] += dollars
        else:
            totals.outside_branch_keys.add(key)
    elif destination is _Destination.REVIEW:
        if in_period:
            totals.review_dollars[key] += dollars
        else:
            totals.outside_review_keys.add(key)
    elif not in_period:
        return  # Other rows carry no outside-period marker.
    elif destination is _Destination.DEFAULT_BRANCH:
        totals.default_dollars[key] += dollars
    elif destination is _Destination.UNIDENTIFIED_REVIEW:
        totals.unidentified_review_dollars[key] += dollars
    else:
        totals.no_anchor_dollars += dollars
        totals.no_anchor_records += 1


def _accumulate_session(
    totals: _StreamTotals, records: Sequence[dict], *, since_ts: float, until_epoch: float,
) -> None:
    ctx = scope._session_branch_context(records)
    anchor_index = _session_anchor_index(records, ctx)
    windows = _review_pr_windows(records)
    for index, rec in enumerate(records):
        if rec.get("type") != "assistant":
            continue
        if bool(rec.get("isSidechain")):
            totals.sidechain_turns += 1
        usage = _record_usage(rec)
        if usage is None:
            continue
        if cost._usage_is_invalid(usage):
            totals.invalid_usage_records += 1
            continue
        ts = corpus._parse_ts(rec.get("timestamp"))
        if ts is None:
            totals.undated_turns += 1
            continue
        if totals.earliest_ts is None or ts < totals.earliest_ts:
            totals.earliest_ts = ts
        in_period = since_ts <= ts < until_epoch
        if in_period:
            totals.dated_usage_in_period = True
        model = _record_model_id(rec)
        dollars_by_class, _context_at_turn, unpriced_tokens = pricing._price_turn(model, usage)
        if dollars_by_class is None:
            if in_period:
                totals.unpriced_tokens[model] += unpriced_tokens
            continue
        dollars = sum(dollars_by_class.values())
        window = _window_for_record(windows, index, rec, ts)
        target = _review_window_target(window) if window is not None else _branch_target(rec, ctx, anchor_index)
        _credit_target(totals, target, dollars, in_period=in_period)
        if in_period:
            totals.priced_models.add(model)
            totals.in_period_priced_dollars += dollars
            if window is not None:
                window.absorbed_dollars += dollars
    for window in windows:
        if window.fallback_close_kind is not None and window.absorbed_dollars > 0:
            totals.fallback_windows.append(_FallbackWindow(
                start_date=_window_start_date(window), close_kind=window.fallback_close_kind,
                review_key=_window_review_key(window), directory=window.window_directory, dollars=window.absorbed_dollars,
            ))


def _period_stream_totals(
    sessions: Iterable[tuple[Path, list[dict]]], *, since_ts: float, until_epoch: float,
) -> _StreamTotals:
    """Price every assistant turn of every session and assign it to a review window, a PR-candidate branch key, or an
    Other part. Pure over `sessions`: it calls no subprocess and reads nothing but the records."""
    totals = _StreamTotals()
    for _path, records in sessions:
        deduped = pricing.dedup_turns_by_request_id(records)
        totals.spawns += pricing._count_subagent_spawns(deduped)
        _accumulate_session(totals, deduped, since_ts=since_ts, until_epoch=until_epoch)
    pricing._warn_if_subagent_format_drift(totals.spawns, totals.sidechain_turns)
    keyed = (
        sum(totals.branch_dollars.values()) + sum(totals.review_dollars.values()) + sum(totals.default_dollars.values())
        + sum(totals.unidentified_review_dollars.values()) + totals.no_anchor_dollars
    )
    if abs(keyed - totals.in_period_priced_dollars) > _CONSERVATION_TOLERANCE:
        raise AssertionError(
            f"period-cost: keyed spend ({keyed:.6f}) does not equal the in-period priced total"
            f" ({totals.in_period_priced_dollars:.6f}) -- the stream assignment is out of sync with the pricing pass"
        )
    return totals


# ---------------------------------------------------------------------------------------------------------------
# Transcript scan
# ---------------------------------------------------------------------------------------------------------------


@dataclass
class _ScanTally:
    """What the transcript scan saw, for SCOPE and NO ACTIVITY. `dropped` counts transcripts left out for resolving
    outside the root or failing to resolve."""

    project_directories: set[Path] = field(default_factory=set)
    transcripts: int = 0
    dropped: int = 0


def _contained_sessions(
    sessions: Iterable[tuple[Path, list[dict]]], projects_root: Path, tally: _ScanTally,
) -> Iterator[tuple[Path, list[dict]]]:
    """Yield each session whose transcript resolves inside the root, once per resolved path. Subagent files are not
    checked: they live under their parent session's directory."""
    try:
        resolved_root = projects_root.resolve()
    except (OSError, RuntimeError):
        resolved_root = projects_root
    seen: set[Path] = set()
    for jsonl, records in sessions:
        try:
            resolved = jsonl.resolve()
        except (OSError, RuntimeError):
            tally.dropped += 1
            continue
        if not resolved.is_relative_to(resolved_root):
            tally.dropped += 1
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        tally.transcripts += 1
        tally.project_directories.add(jsonl.parent)
        yield jsonl, records


# ---------------------------------------------------------------------------------------------------------------
# Repository resolution
# ---------------------------------------------------------------------------------------------------------------


class _DirectoryOutcome(enum.Enum):
    UNRESOLVED = "unresolved"
    NOT_CHECKED = "not checked"


_Resolution = tuple[str, str] | _DirectoryOutcome


def _directory_exists(directory: str) -> bool:
    """True when `directory` exists. Raises OSError for any failure but an absent path, and ValueError for an embedded NUL;
    os.stat is called directly because Path.exists swallows different errors on different Python versions."""
    try:
        os.stat(directory)
    except (FileNotFoundError, NotADirectoryError):
        return False
    return True


def _resolve_directories(directories: Iterable[str], *, deadline: float) -> dict[str, _Resolution]:
    """Map each distinct directory to its repository identity, UNRESOLVED, or NOT_CHECKED (the run deadline passed first).

    One git call runs per distinct candidate directory. A directory that no longer exists but lies under a
    `.claude/worktrees/<name>` segment is read through the main checkout when that exists. The existence check is not
    time-bounded and assumes each recorded directory sits on a filesystem that answers a stat.
    """
    resolution: dict[str, _Resolution] = {}
    identity_by_candidate: dict[str, _Resolution] = {}
    for directory in dict.fromkeys(directories):
        if _monotonic() >= deadline:
            resolution[directory] = _DirectoryOutcome.NOT_CHECKED
            continue
        try:
            candidate: str | None = directory
            if not _directory_exists(directory):
                main_checkout = _conventional_worktree_main_checkout(directory)
                candidate = main_checkout if main_checkout is not None and _directory_exists(main_checkout) else None
        except (OSError, ValueError):
            candidate = None
        if candidate is None:
            resolution[directory] = _DirectoryOutcome.UNRESOLVED
            continue
        if candidate not in identity_by_candidate:
            identity = gh_cli._origin_identity_at(candidate)
            identity_by_candidate[candidate] = identity if identity is not None else _DirectoryOutcome.UNRESOLVED
        resolution[directory] = identity_by_candidate[candidate]
    return resolution


def _directories_to_resolve(totals: _StreamTotals) -> list[str]:
    """Every directory a key carries, those with in-period spend first so the run deadline cuts off outside-only ones first."""
    in_period: list[str | None] = [
        *(directory for key in totals.branch_dollars for directory in (key.own_anchor, key.successor_anchor)),
        *(key.window_directory for key in totals.review_dollars),
        *totals.default_dollars,
        *(directory for directory, _start_date in totals.unidentified_review_dollars),
    ]
    outside_only: list[str | None] = [
        *(directory for key in totals.outside_branch_keys for directory in (key.own_anchor, key.successor_anchor)),
        *(key.window_directory for key in totals.outside_review_keys),
    ]
    return [directory for directory in dict.fromkeys([*in_period, *outside_only]) if directory is not None]


def _display_name(identity: _Identity) -> str:
    host, owner_repo = identity
    return owner_repo if host == _GITHUB_HOST else f"{host}/{owner_repo}"


# ---------------------------------------------------------------------------------------------------------------
# GitHub PR lists
# ---------------------------------------------------------------------------------------------------------------


class _ListedPullRequest(NamedTuple):
    number: int
    title: str | None
    state: str
    head_ref_name: str
    is_cross_repository: bool


@dataclass(frozen=True)
class _PrListResult:
    """One repository's PR list: the validated PRs, or the kind that made it unavailable."""

    pull_requests: tuple[_ListedPullRequest, ...] = ()
    unavailable_kind: str = ""
    listed_count: int = 0

    @property
    def limit_reached(self) -> bool:
        return _reached_list_limit(self.listed_count)


def _reached_list_limit(listed_count: int) -> bool:
    return listed_count >= gh_cli._PR_COST_GH_PR_LIST_LIMIT


def _gh_kind_for_status(status: str) -> str:
    """The unavailable kind for a non-empty _gh_pr_list status. A status this table does not know is reported as an
    unexpected payload rather than aborting the run."""
    return _GH_STATUS_TO_KIND.get(status, _GH_KIND_UNEXPECTED_PAYLOAD)


def _validated_pr_list(payload: object, status: str) -> _PrListResult:
    """Validate one `_gh_pr_list` answer. The status decides first; only an empty status reaches the shape check.

    A JSON `null` arrives as a None payload with an empty status, so it fails the list check as an unexpected payload
    rather than reading as a failed call. Empty stdout is read by _gh_pr_list as `[]`: a repository with no PRs.
    """
    if status:
        return _PrListResult(unavailable_kind=_gh_kind_for_status(status))
    if not isinstance(payload, list):
        return _PrListResult(unavailable_kind=_GH_KIND_UNEXPECTED_PAYLOAD)
    listed: list[_ListedPullRequest] = []
    for entry in payload:
        if not isinstance(entry, dict):
            return _PrListResult(unavailable_kind=_GH_KIND_UNEXPECTED_PAYLOAD)
        number = entry.get("number")
        head_ref_name = entry.get("headRefName")
        is_cross_repository = entry.get("isCrossRepository")
        if (
            not isinstance(number, int) or isinstance(number, bool)
            or not isinstance(head_ref_name, str) or not isinstance(is_cross_repository, bool)
        ):
            return _PrListResult(unavailable_kind=_GH_KIND_UNEXPECTED_PAYLOAD)
        title = entry.get("title")
        raw_state = entry.get("state")
        state = raw_state.lower() if isinstance(raw_state, str) else ""
        listed.append(_ListedPullRequest(
            number=number, title=title if isinstance(title, str) else None,
            state=state if state in _SHOWN_PR_STATES else "", head_ref_name=head_ref_name,
            is_cross_repository=is_cross_repository,
        ))
    return _PrListResult(pull_requests=tuple(listed), listed_count=len(listed))


def _pr_for_branch(pull_requests: Iterable[_ListedPullRequest], branch: str) -> _ListedPullRequest | None:
    """The highest-numbered same-repository PR whose head is exactly `branch`. A default branch, a detached head, and a
    harness worktree-agent branch never join; a cross-repository PR never joins."""
    if branch in scope._DEFAULT_BRANCH_CANDIDATES or scope._names_no_pr_branch(branch):
        return None
    matches = [pr for pr in pull_requests if not pr.is_cross_repository and pr.head_ref_name == branch]
    return max(matches, key=lambda pr: pr.number) if matches else None


def _stored_gh_login_works(host: str) -> bool | None:
    """True when `gh auth status --hostname <host>` exits 0, False on any other exit, None on a timeout or OSError.

    The child's environment drops gh's token variables, so the check sees stored logins only and an ambient token never
    reaches a host that only a recorded `origin` named.
    """
    try:
        proc = subprocess.run(
            ["gh", "auth", "status", "--hostname", host], capture_output=True, text=True,
            timeout=gh_cli._GH_CALL_TIMEOUT_S, encoding="utf-8", errors="replace",
            env=gh_cli._environ_without_gh_tokens(),
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    return proc.returncode == 0


def _fetch_pr_lists(repositories: Sequence[_Identity], *, deadline: float) -> dict[_Identity, _PrListResult]:
    """One single-attempt `gh pr list --state all` per repository, in the order given. A failure marks that repository
    unavailable and never aborts the run. No gh call starts after the deadline."""
    results: dict[_Identity, _PrListResult] = {}
    if not repositories:
        return results
    if shutil.which("gh") is None:
        return dict.fromkeys(repositories, _PrListResult(unavailable_kind=_GH_KIND_GH_MISSING))
    login_by_host: dict[str, bool | None] = {}
    for identity in repositories:
        host, owner_repo = identity
        if _monotonic() >= deadline:
            results[identity] = _PrListResult(unavailable_kind=_GH_KIND_TIME_BUDGET)
            continue
        if host != _GITHUB_HOST:
            if host not in login_by_host:
                login_by_host[host] = _stored_gh_login_works(host)
            login = login_by_host[host]
            if login is None:
                results[identity] = _PrListResult(unavailable_kind=gh_cli._GH_ERROR_KIND_NETWORK)
                continue
            if not login:
                results[identity] = _PrListResult(unavailable_kind=_GH_KIND_NO_WORKING_LOGIN)
                continue
            if _monotonic() >= deadline:
                results[identity] = _PrListResult(unavailable_kind=_GH_KIND_TIME_BUDGET)
                continue
        payload, status = gh_cli._gh_pr_list(
            host, owner_repo, state="all", json_fields=_PR_LIST_JSON_FIELDS, label="pr list (all)",
            command_name="period-cost", max_attempts=_PR_LIST_MAX_ATTEMPTS,
            env=gh_cli._environ_without_gh_tokens() if host != _GITHUB_HOST else None,
        )
        results[identity] = _validated_pr_list(payload, status)
    return results


# ---------------------------------------------------------------------------------------------------------------
# Title and notice text
# ---------------------------------------------------------------------------------------------------------------


def _strip_cc_cf(text: str) -> str:
    # Cc and Cf are the control and format characters that can move, hide, or reorder text.
    # mark-terminal.py and parse-manifest-dependencies.py delete the same set.
    # str.isprintable would also drop private-use and unassigned characters.
    return "".join(char for char in text if unicodedata.category(char) not in ("Cc", "Cf"))


def _flatten_untrusted_text(text: str) -> str:
    """Collapse every whitespace run (U+2028 and U+2029 included) to one space, then delete Cc and Cf characters.

    The collapse runs first because \\n, \\t, and U+0085 are themselves Cc: stripping first would join the words around
    them. A deleted non-whitespace character can leave a doubled, leading, or trailing space, so the result is not
    idempotent.
    """
    return _strip_cc_cf(" ".join(text.split()))


def _sanitize_title(title: str, *, review: bool) -> str:
    """The title cell text; `_TITLE_CELL_CAP` counts the "Review: " prefix of a review row."""
    cell = _flatten_untrusted_text(title).replace("`", "'")
    if review:
        cell = _REVIEW_TITLE_PREFIX + cell
    if len(cell) > _TITLE_CELL_CAP:
        return cell[: _TITLE_CELL_CAP - 1] + "…"
    return cell


def _to_cents(dollars: float) -> Decimal:
    return Decimal(str(dollars)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _usd(dollars: float) -> str:
    return render._fmt_usd(float(_to_cents(dollars)))


def _capped_entries(dollars_by_name: Mapping[str, float], render_name: Callable[[str], str] = str) -> list[str]:
    """The 10 entries with the largest dollars (ties broken by raw name) as `name ($X)`, then `and <N> more ($<sum>)`.

    Entries rank and tie-break on the raw name; `render_name` shapes only the printed form.
    """
    ranked = sorted(dollars_by_name.items(), key=lambda item: (-item[1], item[0]))
    entries = [f"{render_name(name)} ({_usd(dollars)})" for name, dollars in ranked[:_NOTICE_LIST_CAP]]
    overflow = ranked[_NOTICE_LIST_CAP:]
    if overflow:
        entries.append(f"and {len(overflow)} more ({_usd(sum(dollars for _name, dollars in overflow))})")
    return entries


# ---------------------------------------------------------------------------------------------------------------
# Report groups
# ---------------------------------------------------------------------------------------------------------------


class _RowKind(enum.Enum):
    PR = 0
    REVIEW = 1
    OTHER = 2


@dataclass(frozen=True)
class _ReportRow:
    kind: _RowKind
    number: int | None
    title: str
    status: str
    dollars: float
    outside_spend: bool = False
    fallback_fed: bool = False
    title_missing_from_list: bool = False


@dataclass(frozen=True)
class _ReportGroup:
    display_name: str
    rows: tuple[_ReportRow, ...]
    unavailable: bool = False
    limit_reached: bool = False
    undetermined: bool = False


@dataclass
class _OtherBreakdown:
    """Where one group's Other row comes from. The first block applies to a repository, the second to the undetermined group."""

    display_name: str
    default_branch_dollars: float = 0.0
    moved_on_dollars: float = 0.0
    unlinked_branch_dollars: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    unidentified_review_dollars: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    unavailable_candidate_dollars: float = 0.0
    unresolved_directory_dollars: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    not_checked_directory_dollars: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    no_anchor_records: int = 0
    no_anchor_dollars: float = 0.0
    fallback_fed: bool = False
    touched: bool = False

    @property
    def dollars(self) -> float:
        return (
            self.default_branch_dollars + self.moved_on_dollars + sum(self.unlinked_branch_dollars.values())
            + sum(self.unidentified_review_dollars.values()) + self.unavailable_candidate_dollars
            + sum(self.unresolved_directory_dollars.values()) + sum(self.not_checked_directory_dollars.values())
            + self.no_anchor_dollars
        )


@dataclass
class _RowBuilder:
    dollars: float = 0.0
    title: str = _TITLE_UNAVAILABLE
    status: str = ""
    outside_spend: bool = False
    fallback_fed: bool = False
    title_missing_from_list: bool = False


@dataclass
class _GroupBuilder:
    identity: _Identity
    unavailable: bool
    limit_reached: bool
    pull_requests: dict[int, _RowBuilder] = field(default_factory=dict)
    reviews: dict[int, _RowBuilder] = field(default_factory=dict)
    other: _OtherBreakdown = field(init=False)

    def __post_init__(self) -> None:
        self.other = _OtherBreakdown(display_name=_display_name(self.identity))


@dataclass(frozen=True)
class _Assembly:
    groups: tuple[_ReportGroup, ...]
    breakdowns: tuple[_OtherBreakdown, ...]
    undetermined: _OtherBreakdown
    directories_not_checked: bool


def _review_identity(key: _ReviewKey, resolution: Mapping[str, _Resolution]) -> _Identity:
    """A review's repository: its acquire identity, on the host of the window directory when that directory resolved to the
    same owner/repo, else on github.com."""
    window_resolution = resolution.get(key.window_directory) if key.window_directory is not None else None
    if isinstance(window_resolution, tuple) and window_resolution[1] == key.owner_repo:
        return window_resolution
    return _GITHUB_HOST, key.owner_repo


def _resolved_identity(resolution: Mapping[str, _Resolution], directory: str) -> _Identity | None:
    outcome = resolution.get(directory, _DirectoryOutcome.UNRESOLVED)
    return outcome if isinstance(outcome, tuple) else None


class _BranchPlacement(NamedTuple):
    """`own_identity` is None when the key's own directory is unresolved or not checked. `moved_on` is true when a
    pre-branch-remapped key's successor directory does not resolve to the same repository."""

    own_identity: _Identity | None
    moved_on: bool


def _branch_key_placement(key: _BranchKey, resolution: Mapping[str, _Resolution]) -> _BranchPlacement:
    """Where a branch key's spend goes. A pre-branch-remapped key keeps its successor branch only when its own and
    successor anchors resolve to the same repository; otherwise it is default-branch spend of its own repository."""
    own_identity = _resolved_identity(resolution, key.own_anchor)
    if own_identity is None or key.successor_anchor is None:
        return _BranchPlacement(own_identity, moved_on=False)
    return _BranchPlacement(own_identity, moved_on=_resolved_identity(resolution, key.successor_anchor) != own_identity)


def _repositories_to_list(totals: _StreamTotals, resolution: Mapping[str, _Resolution]) -> list[_Identity]:
    """Repositories with an in-period PR-candidate branch or an in-period review, in display-name order."""
    identities: set[_Identity] = set()
    for key in totals.branch_dollars:
        placement = _branch_key_placement(key, resolution)
        if placement.own_identity is not None and not placement.moved_on:
            identities.add(placement.own_identity)
    identities.update(_review_identity(key, resolution) for key in totals.review_dollars)
    return sorted(identities, key=_display_name)


def _other_for_directory(
    builders: dict[_Identity, _GroupBuilder], undetermined: _OtherBreakdown, resolution: Mapping[str, _Resolution],
    pr_lists: Mapping[_Identity, _PrListResult], directory: str,
) -> tuple[_OtherBreakdown, dict[str, float] | None]:
    """The Other breakdown a directory's spend lands in, and for an unresolved or unchecked directory the undetermined
    group's per-directory bucket that records it (None when the directory resolved to a repository)."""
    identity = _resolved_identity(resolution, directory)
    if identity is not None:
        other = _group_for(builders, identity, pr_lists).other
        other.touched = True
        return other, None
    undetermined.touched = True
    if resolution.get(directory) is _DirectoryOutcome.NOT_CHECKED:
        return undetermined, undetermined.not_checked_directory_dollars
    return undetermined, undetermined.unresolved_directory_dollars


def _group_for(
    builders: dict[_Identity, _GroupBuilder], identity: _Identity, pr_lists: Mapping[_Identity, _PrListResult],
) -> _GroupBuilder:
    if identity not in builders:
        listed = pr_lists.get(identity)
        builders[identity] = _GroupBuilder(
            identity=identity, unavailable=listed is not None and bool(listed.unavailable_kind),
            limit_reached=listed is not None and listed.limit_reached,
        )
    return builders[identity]


def _listed_pr_by_number(result: _PrListResult, number: int) -> _ListedPullRequest | None:
    return next((pr for pr in result.pull_requests if pr.number == number), None)


def _title_or_unavailable(pull_request: _ListedPullRequest) -> str:
    return pull_request.title if pull_request.title is not None else _TITLE_UNAVAILABLE


def _place_branch_spend(
    builders: dict[_Identity, _GroupBuilder], undetermined: _OtherBreakdown, resolution: Mapping[str, _Resolution],
    pr_lists: Mapping[_Identity, _PrListResult], key: _BranchKey, dollars: float,
) -> None:
    placement = _branch_key_placement(key, resolution)
    identity = placement.own_identity
    if identity is None:
        _other, bucket = _other_for_directory(builders, undetermined, resolution, pr_lists, key.own_anchor)
        if bucket is not None:
            bucket[key.own_anchor] += dollars
        return
    group = _group_for(builders, identity, pr_lists)
    if placement.moved_on:
        # A repository reached only by moved-on spend is never listed (_repositories_to_list), so no PR list exists here.
        group.other.touched = True
        group.other.moved_on_dollars += dollars
        return
    result = pr_lists[identity]
    pull_request = None if result.unavailable_kind else _pr_for_branch(result.pull_requests, key.branch)
    if pull_request is None:
        group.other.touched = True
        if result.unavailable_kind:
            group.other.unavailable_candidate_dollars += dollars
        else:
            group.other.unlinked_branch_dollars[key.branch] += dollars
        return
    row = group.pull_requests.setdefault(
        pull_request.number, _RowBuilder(title=_title_or_unavailable(pull_request), status=pull_request.state),
    )
    row.dollars += dollars


def _place_review_spend(
    builders: dict[_Identity, _GroupBuilder], resolution: Mapping[str, _Resolution],
    pr_lists: Mapping[_Identity, _PrListResult], key: _ReviewKey, dollars: float,
) -> None:
    identity = _review_identity(key, resolution)
    group = _group_for(builders, identity, pr_lists)
    result = pr_lists[identity]
    row = group.reviews.get(key.number)
    if row is None:
        row = _RowBuilder()
        group.reviews[key.number] = row
        pull_request = None if result.unavailable_kind else _listed_pr_by_number(result, key.number)
        if pull_request is not None:
            row.title = _title_or_unavailable(pull_request)
            row.status = pull_request.state
        elif not result.unavailable_kind:
            row.title_missing_from_list = True
    row.dollars += dollars


def _mark_outside_spend(
    builders: Mapping[_Identity, _GroupBuilder], resolution: Mapping[str, _Resolution],
    pr_lists: Mapping[_Identity, _PrListResult], totals: _StreamTotals,
) -> None:
    """Mark the PR and review rows that also carry priced spend dated outside the period."""
    for key in totals.outside_branch_keys:
        placement = _branch_key_placement(key, resolution)
        identity = placement.own_identity
        if identity is None or placement.moved_on or identity not in builders or identity not in pr_lists:
            continue
        pull_request = _pr_for_branch(pr_lists[identity].pull_requests, key.branch)
        row = builders[identity].pull_requests.get(pull_request.number) if pull_request is not None else None
        if row is not None:
            row.outside_spend = True
    for review_key in totals.outside_review_keys:
        identity = _review_identity(review_key, resolution)
        row = builders[identity].reviews.get(review_key.number) if identity in builders else None
        if row is not None:
            row.outside_spend = True


def _mark_fallback_spend(
    builders: Mapping[_Identity, _GroupBuilder], undetermined: _OtherBreakdown,
    resolution: Mapping[str, _Resolution], totals: _StreamTotals,
) -> None:
    """Mark the rows a fallback-closed review window fed."""
    for fallback in totals.fallback_windows:
        if fallback.review_key is not None:
            identity = _review_identity(fallback.review_key, resolution)
            row = builders[identity].reviews.get(fallback.review_key.number) if identity in builders else None
            if row is not None:
                row.fallback_fed = True
            continue
        directory_identity = _resolved_identity(resolution, fallback.directory) if fallback.directory is not None else None
        if directory_identity is not None and directory_identity in builders:
            builders[directory_identity].other.fallback_fed = True
        else:
            undetermined.fallback_fed = True


def _assemble_groups(
    totals: _StreamTotals, resolution: Mapping[str, _Resolution], pr_lists: Mapping[_Identity, _PrListResult],
) -> _Assembly:
    """Join the stream totals to repositories and PR lists. Every unrounded dollar lands in exactly one row."""
    builders: dict[_Identity, _GroupBuilder] = {}
    undetermined = _OtherBreakdown(display_name=_UNDETERMINED_GROUP_LABEL)

    for key, dollars in totals.branch_dollars.items():
        _place_branch_spend(builders, undetermined, resolution, pr_lists, key, dollars)
    for review_key, dollars in totals.review_dollars.items():
        _place_review_spend(builders, resolution, pr_lists, review_key, dollars)
    for directory, dollars in totals.default_dollars.items():
        other, bucket = _other_for_directory(builders, undetermined, resolution, pr_lists, directory)
        if bucket is not None:
            bucket[directory] += dollars
        else:
            other.default_branch_dollars += dollars
    for (directory, start_date), dollars in totals.unidentified_review_dollars.items():
        other, bucket = _other_for_directory(builders, undetermined, resolution, pr_lists, directory)
        if bucket is not None:
            bucket[directory] += dollars
        else:
            other.unidentified_review_dollars[start_date] += dollars
    if totals.no_anchor_records:
        undetermined.touched = True
        undetermined.no_anchor_records = totals.no_anchor_records
        undetermined.no_anchor_dollars = totals.no_anchor_dollars

    _mark_outside_spend(builders, resolution, pr_lists, totals)
    _mark_fallback_spend(builders, undetermined, resolution, totals)

    groups = [_report_group(builder) for builder in builders.values()]
    breakdowns = [builder.other for builder in builders.values()]
    if undetermined.touched:
        groups.append(_ReportGroup(
            display_name=_UNDETERMINED_GROUP_LABEL, undetermined=True, rows=(_ReportRow(
                kind=_RowKind.OTHER, number=None, title=_OTHER_ROW_LABEL_UNDETERMINED, status="",
                dollars=undetermined.dollars, fallback_fed=undetermined.fallback_fed,
            ),),
        ))
        breakdowns.append(undetermined)
    assembled = sum(row.dollars for group in groups for row in group.rows)
    if abs(assembled - totals.in_period_priced_dollars) > _CONSERVATION_TOLERANCE:
        raise AssertionError(
            f"period-cost: report rows sum to {assembled:.6f} but the in-period priced total is"
            f" {totals.in_period_priced_dollars:.6f} -- the group assembly is out of sync with the stream totals"
        )
    return _Assembly(
        groups=tuple(groups), breakdowns=tuple(breakdowns), undetermined=undetermined,
        directories_not_checked=any(outcome is _DirectoryOutcome.NOT_CHECKED for outcome in resolution.values()),
    )


def _report_group(builder: _GroupBuilder) -> _ReportGroup:
    rows = [
        _ReportRow(
            kind=_RowKind.PR, number=number, title=row.title, status=row.status, dollars=row.dollars,
            outside_spend=row.outside_spend, fallback_fed=row.fallback_fed,
        )
        for number, row in builder.pull_requests.items()
    ]
    rows.extend(
        _ReportRow(
            kind=_RowKind.REVIEW, number=number, title=row.title, status=row.status, dollars=row.dollars,
            outside_spend=row.outside_spend, fallback_fed=row.fallback_fed, title_missing_from_list=row.title_missing_from_list,
        )
        for number, row in builder.reviews.items()
    )
    if builder.other.touched:
        rows.append(_ReportRow(
            kind=_RowKind.OTHER, number=None, title=_OTHER_ROW_LABEL_LISTED, status="", dollars=builder.other.dollars,
            fallback_fed=builder.other.fallback_fed,
        ))
    return _ReportGroup(
        display_name=_display_name(builder.identity), rows=tuple(rows), unavailable=builder.unavailable,
        limit_reached=builder.limit_reached,
    )


# ---------------------------------------------------------------------------------------------------------------
# Slack report
# ---------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _ReportFlags:
    """The stdout note triggers that do not come from the groups themselves."""

    coverage_date: str | None = None
    read_failures: bool = False
    excluded_spend: bool = False
    stale_date: str | None = None
    directories_not_checked: bool = False
    dated_usage_in_period: bool = True


_NOTE_SCOPE = (
    "_Counts one Claude Code account's spend across every repository its sessions worked in. Other accounts and other"
    " people's work are not included._"
)


def _repository_count_phrase(count: int) -> str:
    if count == 0:
        return "no repository identified"
    return "1 repository" if count == 1 else f"{count} repositories"


def _row_label(row: _ReportRow) -> str:
    if row.kind is _RowKind.PR:
        return f"#{row.number}"
    if row.kind is _RowKind.REVIEW:
        return f"Rev #{row.number}"
    return "Other"


def _row_title_cell(row: _ReportRow) -> str:
    return _sanitize_title(row.title, review=row.kind is _RowKind.REVIEW)


def _row_marker_slot(row: _ReportRow) -> str:
    return ("+" if row.outside_spend else " ") + ("?" if row.fallback_fed else " ")


def _displayed_groups(groups: Iterable[_ReportGroup]) -> tuple[list[tuple[_ReportGroup, list[tuple[_ReportRow, Decimal]]]], bool]:
    """The groups with at least one row that rounds to a cent or more, sorted for output, and whether any row was omitted."""
    displayed: list[tuple[_ReportGroup, list[tuple[_ReportRow, Decimal]]]] = []
    omitted = False
    for group in sorted(groups, key=lambda g: (g.undetermined, g.display_name)):
        shown: list[tuple[_ReportRow, Decimal]] = []
        for row in sorted(group.rows, key=lambda r: (r.kind.value, r.number if r.number is not None else 0)):
            cents = _to_cents(row.dollars)
            if cents == 0:
                omitted = True
            else:
                shown.append((row, cents))
        if shown:
            displayed.append((group, shown))
    return displayed, omitted


def _dollar_text(cents: Decimal) -> str:
    return render._fmt_usd(float(cents))


def _render_group_block(
    group: _ReportGroup, shown: Sequence[tuple[_ReportRow, Decimal]], widths: tuple[int, int, int, int],
) -> list[str]:
    label_width, title_width, status_width, cost_width = widths

    def table_line(label: str, title: str, status: str, cost_text: str, marker_slot: str = "") -> str:
        line = f"{label:<{label_width}}  {title:<{title_width}}  {status:<{status_width}}  {cost_text:>{cost_width}}"
        return f"{line}  {marker_slot}".rstrip()

    suffix = _PR_LIST_UNAVAILABLE_SUFFIX if group.unavailable else ""
    lines = [f"*{group.display_name}*{suffix}", "```", table_line("PR", "Title", "Status", "Cost")]
    lines.extend(
        table_line(_row_label(row), _row_title_cell(row), row.status, _dollar_text(cents), _row_marker_slot(row))
        for row, cents in shown
    )
    lines.append("```")
    lines.append(f"_Subtotal: {_dollar_text(sum((cents for _row, cents in shown), Decimal(0)))}_")
    return lines


def _table_widths(shown: Sequence[tuple[_ReportRow, Decimal]]) -> tuple[int, int, int, int]:
    return (
        max([len("PR"), *(len(_row_label(row)) for row, _cents in shown)]),
        max([len("Title"), *(len(_row_title_cell(row)) for row, _cents in shown)]),
        max([len("Status"), *(len(row.status) for row, _cents in shown)]),
        max([len("Cost"), *(len(_dollar_text(cents)) for _row, cents in shown)]),
    )


def _flag_notes(flags: _ReportFlags) -> list[str]:
    """The coverage, read-failure, excluded-spend, and stale-rate notes: the conditions that can make the figures understate or
    rest on stale rates."""
    notes: list[str] = []
    if flags.coverage_date is not None:
        notes.append(
            f"_Note: no Claude Code records were found before {flags.coverage_date}; spend before that date, if any,"
            " is not included._"
        )
    if flags.read_failures:
        notes.append(
            "_Note: some session records could not be read and are left out, so these figures may understate the spend._"
        )
    if flags.excluded_spend:
        notes.append("_Note: some usage could not be priced or dated and is left out, so these figures understate the spend._")
    if flags.stale_date is not None:
        notes.append(
            f"_Note: these figures use list rates that were due to be rechecked on {flags.stale_date}, so any rate change"
            " since then is not reflected._"
        )
    return notes


def _table_notes(
    displayed: Sequence[tuple[_ReportGroup, Sequence[tuple[_ReportRow, Decimal]]]], omitted: bool, flags: _ReportFlags,
) -> tuple[list[str], list[str]]:
    """(the period, outside-period-marker, and fallback-marker notes; the unavailable-list, under-half-cent, list-limit,
    title-unavailable, and time-limit notes) for a report that shows a table."""
    rows = [(group, row) for group, shown in displayed for row, _cents in shown]
    leading = ["_Each row counts only spend dated within this period._"]
    if any(row.outside_spend for _group, row in rows):
        leading.append("_+ Also has spend outside this period, which is not counted here._")
    if any(row.fallback_fed for _group, row in rows):
        leading.append(
            "_? A review's end was not recorded, so a marked row may include later work that belongs to another row._"
        )
    trailing: list[str] = []
    if any(group.unavailable for group, _shown in displayed):
        trailing.append(
            "_Note: for a repository marked PR list unavailable, GitHub's PR list could not be read, so its PR spend is"
            " shown under Other._"
        )
    if omitted:
        trailing.append("_Note: items that each cost under half a cent are left out of the rows and totals._")
    if any(group.limit_reached for group, _shown in displayed):
        trailing.append(
            "_Note: a repository has more PRs than this report reads, so spend on its oldest PRs is shown under Other._"
        )
    if any(row.title_missing_from_list and not group.unavailable for group, row in rows):
        trailing.append(
            "_Note: a review row titled (title unavailable) is for a PR missing from GitHub's PR list for that repository._"
        )
    if flags.directories_not_checked:
        trailing.append(
            "_Note: the run's time limit cut short the repository lookup, so some spend is shown under Repository not"
            " determined._"
        )
    return leading, trailing


def _render_slack_report(groups: Iterable[_ReportGroup], flags: _ReportFlags, window: _PeriodWindow, run_date: date) -> str:
    """The Slack mrkdwn report. Pure: sorting, rounding, widths, and notes all come from the arguments."""
    displayed, omitted = _displayed_groups(groups)
    partial_suffix = f", partial: through {run_date.isoformat()}" if window.partial else ""
    repository_count = sum(1 for group, _shown in displayed if not group.undetermined)
    lines = [
        f"{_REPORT_FIRST_LINE_PREFIX}{window.since} to {window.until} (UTC){partial_suffix}*",
        f"One Claude Code account, {_repository_count_phrase(repository_count)}, generated {run_date.isoformat()} (UTC)",
    ]
    if displayed:
        for group, shown in displayed:
            lines.append("")
            lines.extend(_render_group_block(group, shown, _table_widths(shown)))
        total = sum((cents for _group, shown in displayed for _row, cents in shown), Decimal(0))
        lines.append("")
        lines.append(f"*Total: {_dollar_text(total)}*")
        leading, trailing = _table_notes(displayed, omitted, flags)
        lines.extend(leading)
        lines.append(_NOTE_SCOPE)
        lines.extend(_flag_notes(flags))
        lines.extend(trailing)
    else:
        lines.append(
            "No Claude Code usage was found in this period in the sessions this report covers."
            if not flags.dated_usage_in_period
            else "Claude Code usage was found in this period, but no row's priced spend reaches $0.01."
        )
        lines.append(_NOTE_SCOPE)
        lines.extend(_flag_notes(flags))
    lines.append(f"_{cost._LIST_PRICE_CAVEAT}_")
    return "\n".join(line.rstrip() for line in lines)


# ---------------------------------------------------------------------------------------------------------------
# Synthetic sample
# ---------------------------------------------------------------------------------------------------------------

_SAMPLE_RUN_DATE = date(2026, 10, 2)
_SAMPLE_WINDOW = _window_from_bounds(
    datetime(2026, 9, 1, tzinfo=UTC).timestamp(), datetime(2026, 10, 1, tzinfo=UTC).timestamp(), _SAMPLE_RUN_DATE,
)
_SAMPLE_GROUPS = (
    _ReportGroup(display_name="acme/billing-api", rows=(
        _ReportRow(_RowKind.PR, 57, "Retry failed webhook deliveries", "open", 12.40),
        _ReportRow(_RowKind.REVIEW, 61, "Ledger export", "merged", 3.05),
        _ReportRow(_RowKind.OTHER, None, _OTHER_ROW_LABEL_LISTED, "", 1.10),
    )),
    _ReportGroup(display_name="acme/widgets", rows=(
        _ReportRow(_RowKind.PR, 142, "Add invoice export", "merged", 38.20, outside_spend=True),
        _ReportRow(_RowKind.PR, 151, "Rate-limit @channel alerts", "open", 21.70),
        _ReportRow(_RowKind.REVIEW, 139, "Onboarding flow", "merged", 4.10),
        _ReportRow(_RowKind.OTHER, None, _OTHER_ROW_LABEL_LISTED, "", 0.85),
    )),
    _ReportGroup(display_name=_UNDETERMINED_GROUP_LABEL, undetermined=True, rows=(
        _ReportRow(_RowKind.OTHER, None, _OTHER_ROW_LABEL_UNDETERMINED, "", 0.40),
    )),
)


def _render_sample() -> str:
    """The synthetic report `--sample` prints and the docs show; it reads no transcript and runs no subprocess."""
    return _render_slack_report(_SAMPLE_GROUPS, _ReportFlags(), _SAMPLE_WINDOW, _SAMPLE_RUN_DATE)


# ---------------------------------------------------------------------------------------------------------------
# Notices (stderr)
# ---------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _UnavailableRepository:
    display_name: str
    host: str
    kind: str


class _FallbackNotice(NamedTuple):
    start_date: str
    close_kind: str
    group_name: str
    row_label: str
    dollars: float


@dataclass(frozen=True)
class _NoticeFacts:
    account_source: str
    project_directories: int
    transcripts: int
    read_failure_floor: int
    table_shown: bool
    dated_usage_in_period: bool
    displayed_repositories: tuple[tuple[str, float], ...]
    unresolved_directories: int
    not_checked_directories: int
    no_anchor_records: int
    unlinked_cents: Decimal
    total_cents: Decimal
    since_ts: float
    earliest_ts: float | None
    undated_turns: int
    invalid_usage_records: int
    unpriced_tokens: Mapping[str, int]
    stale_models: tuple[tuple[str, date], ...]
    pricing_integrity: bool
    fallback_notices: tuple[_FallbackNotice, ...]
    unavailable_repositories: tuple[_UnavailableRepository, ...]
    list_counts: tuple[tuple[str, int], ...]
    other_breakdowns: tuple[_OtherBreakdown, ...]


def _coverage_gap_date(earliest_ts: float | None, since_ts: float) -> str | None:
    """The earliest record's date when it is more than a day after the window start, else None."""
    if earliest_ts is None or earliest_ts - since_ts <= cost._CORPUS_COVERAGE_WARNING_THRESHOLD_SECONDS:
        return None
    return render._fmt_date(earliest_ts)


def _stale_models(priced_models: Iterable[str], today: date) -> list[tuple[str, date]]:
    return sorted(
        (model, pricing._MODEL_RATE_EXPIRES[model]) for model in priced_models if today > pricing._MODEL_RATE_EXPIRES[model]
    )


def _notice(tag: str, text: str) -> str:
    return f"period-cost: {tag}: {text}"


def _model_label(model_id: str) -> str:
    return _flatten_untrusted_text(model_id) or _NO_MODEL_FIELD_LABEL


_RATE_LIMIT_ACTION = (
    "gh reported a rate limit, an HTTP 429, or an HTTP 403, and a 403 can also be a permission refusal; a re-run helps only"
    " after a rate limit resets, and the marked report is sendable"
)
_NETWORK_ACTION = (
    "gh's login check or list call failed in a way this tool does not classify further, such as a timeout or a missing or"
    " unreachable repository; a re-run helps only after a transient failure, and the marked report is sendable"
)
_TIME_BUDGET_ACTION = (
    "the run's time budget ended before this repository was listed; a re-run will likely repeat the same scan and reach"
    " the same point, and the marked report is sendable"
)
_AUTH_ACTION = (
    "gh's login for {host} was refused; run gh auth login --hostname {host} in a separate terminal, then re-run; for"
    " github.com, if GH_TOKEN or GITHUB_TOKEN is set, gh may be sending that token instead, and a new login does not"
    " change it; replace or refresh the token, or, only after confirming the host's stored login is the intended"
    " account, unset the variable in the launching shell, because gh then falls back to that stored login, which may be a"
    " different or broader account"
)
_GH_UNAVAILABLE_ACTIONS: dict[str, str] = {
    gh_cli._GH_ERROR_KIND_AUTH: _AUTH_ACTION,
    _GH_KIND_NO_WORKING_LOGIN: (
        "gh reports no working login for {host} (or the host was unreachable), a host named by a directory's origin"
        " remote; if it is a real host you recognize, run gh auth login --hostname {host} in a separate terminal, then"
        " re-run; if it is an SSH alias, no gh login helps, and that repository's PR spend stays under Other"
    ),
    _GH_KIND_GH_MISSING: "gh is not installed or not on PATH, so no repository's PRs were listed",
    gh_cli._GH_ERROR_KIND_RATE_LIMIT: _RATE_LIMIT_ACTION,
    gh_cli._GH_ERROR_KIND_NETWORK: _NETWORK_ACTION,
    _GH_KIND_TIME_BUDGET: _TIME_BUDGET_ACTION,
    gh_cli._GH_ERROR_KIND_HOST_MISMATCH: (
        "gh's GH_HOST setting conflicts with this host; re-run from a terminal with GH_HOST unset"
    ),
    _GH_KIND_UNPARSEABLE: "report it",
    _GH_KIND_UNEXPECTED_PAYLOAD: "report it",
}


def _scope_notice_lines(facts: _NoticeFacts) -> list[str]:
    lines = [
        _notice(
            _NOTICE_SCOPE,
            f"only the active account's projects directory is read, taken from {facts.account_source}; the declared-roots"
            " file is not read",
        ),
        _notice(
            _NOTICE_SCOPE,
            f"project directories: {facts.project_directories}; transcripts: {facts.transcripts}; read failures: at least"
            f" {facts.read_failure_floor} (subagent files are not counted in that floor)",
        ),
    ]
    repositories = _capped_entries(dict(facts.displayed_repositories))
    lines.append(_notice(
        _NOTICE_SCOPE,
        f"resolved repositories: {len(facts.displayed_repositories)} ({', '.join(repositories) or 'none'}); unresolved"
        f" directories: {facts.unresolved_directories}; directories not checked before the time limit:"
        f" {facts.not_checked_directories}; records with no recorded directory: {facts.no_anchor_records}",
    ))
    if facts.table_shown:
        percent = (facts.unlinked_cents * 100 / facts.total_cents).quantize(Decimal(1), rounding=ROUND_HALF_UP)
        lines.append(_notice(
            _NOTICE_SCOPE,
            f"Unlinked: {_dollar_text(facts.unlinked_cents)} of {_dollar_text(facts.total_cents)} ({percent}%) is in Other"
            " rows or Repository not determined.",
        ))
    return lines


def _other_breakdown_line(breakdown: _OtherBreakdown) -> str | None:
    parts: list[str] = []
    if breakdown.unlinked_branch_dollars:
        parts.append("unlinked branches " + ", ".join(
            _capped_entries(breakdown.unlinked_branch_dollars, render_name=_flatten_untrusted_text)
        ))
    if breakdown.default_branch_dollars:
        parts.append(f"default-branch, detached, and unbranched spend {_usd(breakdown.default_branch_dollars)}")
    if breakdown.moved_on_dollars:
        parts.append(
            "leading default-branch spend from sessions whose successor directory did not resolve to this repository"
            f" {_usd(breakdown.moved_on_dollars)}"
        )
    if breakdown.unidentified_review_dollars:
        parts.append("unidentified reviews started " + ", ".join(_capped_entries(breakdown.unidentified_review_dollars)))
    if breakdown.unavailable_candidate_dollars:
        parts.append(f"PR-candidate branches of an unavailable repository {_usd(breakdown.unavailable_candidate_dollars)}")
    for label, by_directory in (
        ("unresolved directories", breakdown.unresolved_directory_dollars),
        ("directories not checked before the time limit", breakdown.not_checked_directory_dollars),
    ):
        if by_directory:
            parts.append(f"{label} {len(by_directory)} ({_usd(sum(by_directory.values()))})")
    if breakdown.no_anchor_records:
        parts.append(
            f"records with no recorded directory {breakdown.no_anchor_records} ({_usd(breakdown.no_anchor_dollars)})"
        )
    if not parts:
        return None
    return _notice(_NOTICE_OTHER_BREAKDOWN, f"{breakdown.display_name}: " + "; ".join(parts))


def _fallback_notice_lines(notices: Sequence[_FallbackNotice]) -> list[str]:
    """One line per fallback-closed window, largest dollars first, capped."""
    ranked = sorted(notices, key=lambda item: (-item.dollars, item.start_date, item.group_name, item.row_label))
    lines = [
        _notice(
            _NOTICE_REVIEW_WINDOW_FALLBACK,
            f"the review started {item.start_date} in {item.group_name} ended at {item.close_kind} with no finish call, so"
            f" {item.row_label} may hold later work ({_usd(item.dollars)})",
        )
        for item in ranked[:_NOTICE_LIST_CAP]
    ]
    overflow = ranked[_NOTICE_LIST_CAP:]
    if overflow:
        lines.append(_notice(
            _NOTICE_REVIEW_WINDOW_FALLBACK, f"and {len(overflow)} more ({_usd(sum(item.dollars for item in overflow))})",
        ))
    return lines


def _unavailable_notice_lines(unavailable: Sequence[_UnavailableRepository]) -> list[str]:
    ranked = sorted(unavailable, key=lambda item: item.display_name)
    lines = [
        _notice(
            _NOTICE_GH_UNAVAILABLE,
            f"{item.display_name} ({item.kind}): {_GH_UNAVAILABLE_ACTIONS[item.kind].format(host=item.host)}",
        )
        for item in ranked[:_NOTICE_LIST_CAP]
    ]
    if len(ranked) > _NOTICE_LIST_CAP:
        lines.append(_notice(_NOTICE_GH_UNAVAILABLE, f"and {len(ranked) - _NOTICE_LIST_CAP} more repositories"))
    return lines


def _notice_lines(facts: _NoticeFacts) -> list[str]:
    """Every stderr notice line, built from already computed facts. Transcript-derived names pass through
    _flatten_untrusted_text once, as each line is built."""
    lines = _scope_notice_lines(facts)
    lines.append(_notice(
        _NOTICE_SHARING,
        "the report names the account's repositories, PRs, titles, and spend; share it privately, only with the owner of those"
        " repositories, and never commit it or paste it into a PR, issue, or public channel",
    ))
    coverage_date = _coverage_gap_date(facts.earliest_ts, facts.since_ts)
    if coverage_date is not None:
        lines.append(_notice(
            _NOTICE_COVERAGE_WARNING,
            f"no Claude Code records were found before {coverage_date}; spend before that date, if any, is not included;"
            " either retention deleted older transcripts or the account was quiet",
        ))
    if not facts.table_shown and not facts.dated_usage_in_period:
        what_was_found = (
            "no transcripts were found" if facts.transcripts == 0
            else "transcripts were found but none holds usage dated in this period"
        )
        lines.append(_notice(
            _NOTICE_NO_ACTIVITY,
            f"{what_was_found} (project directories: {facts.project_directories}; transcripts: {facts.transcripts};"
            f" undated usage-bearing turns: {facts.undated_turns}; read failures: at least {facts.read_failure_floor});"
            " check the account, the period, retention, and any non-zero undated or read-failure count",
        ))
    reportable_models = pricing._reportable_unpriced_model_ids(dict(facts.unpriced_tokens))
    if reportable_models or facts.undated_turns or facts.invalid_usage_records:
        excluded = [
            f"{_model_label(model_id)} ({facts.unpriced_tokens[model_id]:,} tokens)" for model_id in sorted(reportable_models)
        ]
        if facts.undated_turns:
            excluded.append(f"{facts.undated_turns} usage-bearing turns with no timestamp")
        if facts.invalid_usage_records:
            excluded.append(f"{facts.invalid_usage_records} usage-bearing turns with malformed usage token fields")
        lines.append(_notice(
            _NOTICE_EXCLUDED_SPEND,
            "usage left out of the figures: " + "; ".join(excluded) + "; the figures understate the spend",
        ))
    if facts.stale_models:
        stale = ", ".join(f"{model} (due {due.isoformat()})" for model, due in facts.stale_models)
        lines.append(_notice(
            _NOTICE_STALE_PRICING, f"list rates were due to be rechecked: {stale}; any rate change since is not reflected",
        ))
    if facts.pricing_integrity:
        lines.append(_notice(
            _NOTICE_PRICING_INTEGRITY,
            "a transcript-format change this tool does not recognize was detected, so the figures may be incomplete or wrong;"
            " report it",
        ))
    lines.extend(_fallback_notice_lines(facts.fallback_notices))
    lines.extend(_unavailable_notice_lines(facts.unavailable_repositories))
    limit_reached = sorted(name for name, count in facts.list_counts if _reached_list_limit(count))
    if limit_reached:
        lines.append(_notice(
            _NOTICE_PR_LIST_LIMIT,
            f"the PR list reached its limit of {gh_cli._PR_COST_GH_PR_LIST_LIMIT} for {', '.join(limit_reached)}; older PRs may"
            " be missing, their branches' spend is in that repository's Other row, and reviews of them keep their own rows"
            " without a title",
        ))
    lines.extend(line for breakdown in facts.other_breakdowns if (line := _other_breakdown_line(breakdown)) is not None)
    return lines


# ---------------------------------------------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------------------------------------------


def _fallback_notices(totals: _StreamTotals, resolution: Mapping[str, _Resolution]) -> tuple[_FallbackNotice, ...]:
    notices: list[_FallbackNotice] = []
    for fallback in totals.fallback_windows:
        if fallback.review_key is not None:
            group_name = _display_name(_review_identity(fallback.review_key, resolution))
            row_label = f"Rev #{fallback.review_key.number}"
        else:
            directory_identity = _resolved_identity(resolution, fallback.directory) if fallback.directory is not None else None
            group_name = _display_name(directory_identity) if directory_identity is not None else _UNDETERMINED_GROUP_LABEL
            row_label = "Other"
        notices.append(_FallbackNotice(fallback.start_date, fallback.close_kind, group_name, row_label, fallback.dollars))
    return tuple(notices)


def _displayed_summary(
    displayed: Sequence[tuple[_ReportGroup, Sequence[tuple[_ReportRow, Decimal]]]],
) -> tuple[tuple[tuple[str, float], ...], Decimal, Decimal]:
    """(displayed repositories with their subtotals, the Other and undetermined cents, the total cents)."""
    repositories = tuple(
        (group.display_name, float(sum((cents for _row, cents in shown), Decimal(0))))
        for group, shown in displayed if not group.undetermined
    )
    unlinked = sum((cents for _group, shown in displayed for row, cents in shown if row.kind is _RowKind.OTHER), Decimal(0))
    total = sum((cents for _group, shown in displayed for _row, cents in shown), Decimal(0))
    return repositories, unlinked, total


def _period_cost_report(
    window: _PeriodWindow, today: date, deadline: float, projects_root: Path, account_source: str,
) -> tuple[str, list[str]]:
    """Scan, resolve, list, assemble, and render. Returns (stdout report, stderr notice lines) and prints nothing itself;
    all local work, git included, finishes before the first gh call."""
    scan_gaps: Counter[str] = Counter()
    tally = _ScanTally()
    sessions = scope._iter_glob_scoped_sessions([projects_root], "*", True, scan_gaps=scan_gaps)
    totals = _period_stream_totals(
        _contained_sessions(sessions, projects_root, tally), since_ts=window.since_ts, until_epoch=window.until_epoch,
    )
    if scan_gaps[scope._SCAN_GAP_ROOT]:
        print(_ROOT_LISTING_FAILURE_MESSAGE, file=sys.stderr)
        sys.exit(1)
    resolution = _resolve_directories(_directories_to_resolve(totals), deadline=deadline)
    pr_lists = _fetch_pr_lists(_repositories_to_list(totals, resolution), deadline=deadline)
    assembly = _assemble_groups(totals, resolution, pr_lists)

    read_failure_floor = sum(scan_gaps.values()) + tally.dropped
    reportable_unpriced = pricing._reportable_unpriced_model_ids(totals.unpriced_tokens)
    stale_models = _stale_models(totals.priced_models, today)
    flags = _ReportFlags(
        coverage_date=_coverage_gap_date(totals.earliest_ts, window.since_ts),
        read_failures=read_failure_floor > 0,
        excluded_spend=bool(reportable_unpriced) or totals.undated_turns > 0 or totals.invalid_usage_records > 0,
        stale_date=min(due for _model, due in stale_models).isoformat() if stale_models else None,
        directories_not_checked=assembly.directories_not_checked,
        dated_usage_in_period=totals.dated_usage_in_period,
    )
    displayed, _omitted = _displayed_groups(assembly.groups)
    displayed_repositories, unlinked_cents, total_cents = _displayed_summary(displayed)
    displayed_names = {group.display_name for group, _shown in displayed}
    shown_other_names = {
        group.display_name for group, shown in displayed if any(row.kind is _RowKind.OTHER for row, _cents in shown)
    }
    facts = _NoticeFacts(
        account_source=account_source, project_directories=len(tally.project_directories), transcripts=tally.transcripts,
        read_failure_floor=read_failure_floor, table_shown=bool(displayed), dated_usage_in_period=totals.dated_usage_in_period,
        displayed_repositories=displayed_repositories,
        unresolved_directories=len(assembly.undetermined.unresolved_directory_dollars),
        not_checked_directories=len(assembly.undetermined.not_checked_directory_dollars),
        no_anchor_records=assembly.undetermined.no_anchor_records, unlinked_cents=unlinked_cents, total_cents=total_cents,
        since_ts=window.since_ts, earliest_ts=totals.earliest_ts, undated_turns=totals.undated_turns,
        invalid_usage_records=totals.invalid_usage_records, unpriced_tokens=totals.unpriced_tokens,
        stale_models=tuple(stale_models),
        pricing_integrity=pricing._format_drift_detected(), fallback_notices=_fallback_notices(totals, resolution),
        unavailable_repositories=tuple(
            _UnavailableRepository(_display_name(identity), identity[0], result.unavailable_kind)
            for identity, result in pr_lists.items()
            if result.unavailable_kind and _display_name(identity) in displayed_names
        ),
        list_counts=tuple(
            (_display_name(identity), result.listed_count)
            for identity, result in pr_lists.items()
            if not result.unavailable_kind and _display_name(identity) in displayed_names
        ),
        other_breakdowns=tuple(sorted(
            (breakdown for breakdown in assembly.breakdowns if breakdown.display_name in shown_other_names),
            key=lambda breakdown: (breakdown.display_name == _UNDETERMINED_GROUP_LABEL, breakdown.display_name),
        )),
    )
    return _render_slack_report(assembly.groups, flags, window, today), _notice_lines(facts)


def cmd_period_cost(args: argparse.Namespace) -> None:
    """CLI entry point for the period-cost subcommand.

    Reads the clock once for the run date and once for the run deadline, then writes every stderr notice before the one
    stdout write, so the report is the last thing in the Bash result. `--sample` prints the synthetic report and
    touches no transcript, config directory, git, or gh.
    """
    today = _utc_today()
    deadline = _monotonic() + _RUN_DEADLINE_S
    window = _validated_window(args, today)
    if window is None:
        sys.stdout.write(_render_sample() + "\n")
        sys.stdout.flush()
        return
    try:
        projects_root = _projects_root()
    except ValueError:
        print(_CONFIG_DIR_UNRESOLVABLE_MESSAGE, file=sys.stderr)
        sys.exit(2)
    if shutil.which("git") is None:
        print(_GIT_MISSING_MESSAGE, file=sys.stderr)
        sys.exit(1)
    report, notice_lines = _period_cost_report(window, today, deadline, projects_root, _account_source())
    for line in notice_lines:
        print(line, file=sys.stderr)
    sys.stderr.flush()
    sys.stdout.write(report + "\n")
    sys.stdout.flush()
