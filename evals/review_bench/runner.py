"""A-bench reviewer-run harness: campaign/block/run execution, per-run
validity checks and retry-then-missing, per-run statistics, environment
recording, and interruption-safe cleanup.

See .claude/plans/measure-review-quality.md's Approach > "Runs and
adjudication" for the full design this module follows. Judge runs
(bench-judge-recall / bench-judge-precision), adjudicate.py, and analysis.py
own the judge-side scope -- this module only runs reviewer arms, but its
per-run validity checks and RunRecord schema are written generically over
"the run's own directories" so the judge runs reuse them unchanged.

Reuses from evals/measure_subagent_model_resolution.py (see that module's
own docstring for the reuse record this file adds):
_run_claude_to_completion, _resolved_temp_project_dir,
subagent_dir_for_session, parse_subagent_dispatches,
PER_RUN_BUDGET_CAP_USD, and BUDGET_CAP_MULTIPLIER. Reuses
run_skill_evals.DEFAULT_WORKERS and DISPATCH_TOOL_NAMES. Session stores are
found by session ID, never through run_skill_evals.compute_session_store_dir().

LOCAL USE ONLY -- never run in CI. `smoke` and `run` launch real `claude -p`
sessions against real Claude subscription auth.
"""
from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import sys
import time
import uuid
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path

import measure_subagent_model_resolution as msmr
import run_skill_evals

from review_bench import arms as arms_mod
from review_bench.defects import ConfirmedDefect
from review_bench.fixture_repo import build_defect_fixture

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
# config_dir imported directly from _config_dir, not through
# run_skill_evals's re-export -- run_skill_evals already performs this same
# sys.path.insert as an import-time side effect, but this insert is kept so
# this module has no hidden ordering dependency on that side effect.
sys.path.insert(0, str(REPO_ROOT / "claude" / ".claude" / "scripts"))
from _config_dir import config_dir  # noqa: E402

# --- Frozen model IDs ------------------------------------------------------
REVIEWER_MODEL_ID = "claude-sonnet-5"
JUDGE_MODEL_ID = "claude-opus-5-5"

# --- Caps (Approach > Runs and adjudication > "Caps") -------------------------

# Reused directly, not re-derived: a measured
# staff-backend-engineer dispatch cost x 10 (see
# measure_subagent_model_resolution.py's own comment for the command and
# date that measured it).
REVIEWER_BUDGET_CAP_USD = msmr.PER_RUN_BUDGET_CAP_USD

# Bootstrap value, pending the smoke campaign. The Caps section's own
# formula is 10x the p95 of this repo's own staff-reviewer dispatch
# durations, but no transcript-analysis.py subcommand yet reports
# per-dispatch wall-clock duration. This reuses run_skill_evals.SAMPLE_TIMEOUT_S
# (this repo's own existing per-sample default) under the same x10
# convention msmr.py's own budget cap uses.
REVIEWER_TIMEOUT_S = run_skill_evals.SAMPLE_TIMEOUT_S * msmr.BUDGET_CAP_MULTIPLIER

# Judge caps and timeouts start at the reviewer's own values -- "the only
# per-run bounds this repo has measured" (Caps) -- until the smoke
# campaign's full-K fixture measures each judge separately. The judge runs
# (bench-judge-recall / bench-judge-precision) are the first consumers of
# these; kept here since they share this module's RunRecord schema and
# validity-check machinery.
RECALL_JUDGE_BUDGET_CAP_USD = REVIEWER_BUDGET_CAP_USD
RECALL_JUDGE_TIMEOUT_S = REVIEWER_TIMEOUT_S
PRECISION_JUDGE_BUDGET_CAP_USD = REVIEWER_BUDGET_CAP_USD
PRECISION_JUDGE_TIMEOUT_S = REVIEWER_TIMEOUT_S

# --- Terms (Approach > Runs and adjudication > "Terms") -----------------------

DEFAULT_K = 10  # runs per arm per defect; the pre-freeze lever is K, not delta (Approach > "K, not delta...")

# read-scope's own chars-per-token estimate, duplicated per the
# small-duplicated-value exception -- read_scope.py is
# mid-extraction by #1116. fixture_repo.py carries its own copy of this
# same constant for the same reason; the two are not imported from each
# other to avoid a cross-module coupling neither side needs.
_READ_SCOPE_CHARS_PER_TOKEN = 4

_PARTIAL_VIEW_MARKER = "PARTIAL view"  # Read's own truncation notice text
_READ_LIKE_TOOLS = frozenset({"Read", "Grep", "Glob"})

# review-bench's own fixture/session-store directory prefix (Cleanup) --
# every _resolved_temp_project_dir() call in this module uses it, so a
# hard-interrupted run's leftover directories are always findable by this
# prefix alone (evals/README.md's own documented recovery instructions).
FIXTURE_DIR_PREFIX = "review-bench-"


@dataclass(frozen=True)
class BlockPlan:
    """One defect's run order: every (arm, run_index) pair, K per arm, in a
    seeded shuffled order (Approach > Terms > "block")."""

    defect_id: str
    ordered_runs: tuple[tuple[str, int], ...]


def build_block_plan(defect_id: str, arms: tuple[str, ...], k: int, seed: int) -> BlockPlan:
    """Deterministic given (defect_id, arms, k, seed) -- same inputs always
    produce the same shuffled order (Verification: "Seeded block order is
    deterministic")."""
    combos = [(arm, run_index) for arm in arms for run_index in range(k)]
    random.Random(f"{seed}:{defect_id}").shuffle(combos)
    return BlockPlan(defect_id=defect_id, ordered_runs=tuple(combos))


# --- Prompts (Approach > "Review prompt" and "Dispatcher") -------------------

REVIEW_PROMPT_TEMPLATE = (
    "Review the change from HEAD~1 to HEAD in this repository. Its commit "
    "subject is: {subject}. The diff is at `.bench/change.diff`, a "
    "function-context version is at `.bench/change-function-context.diff`, "
    "and each changed file's line count is in `.bench/changed-files.tsv`. "
    "Report every finding with its file:line, in your inline output format."
)

DISPATCH_PROMPT_TEMPLATE = (
    "Use the Agent tool once to dispatch the `{agent}` agent with exactly "
    "the prompt between the markers below, unchanged. Do not read files or "
    "do anything else. When it returns, output its result verbatim."
)

_DISPATCH_PROMPT_MARKER_OPEN = "<<<"
_DISPATCH_PROMPT_MARKER_CLOSE = ">>>"


def build_review_prompt(subject: str) -> str:
    return REVIEW_PROMPT_TEMPLATE.format(subject=subject)


def build_dispatcher_prompt(agent: str, inner_prompt: str) -> str:
    """The dispatcher's own `-p` prompt: DISPATCH_PROMPT_TEMPLATE plus
    inner_prompt (the review or judge prompt) between the marker lines
    (Approach > "Dispatcher")."""
    header = DISPATCH_PROMPT_TEMPLATE.format(agent=agent)
    return f"{header}\n\n{_DISPATCH_PROMPT_MARKER_OPEN}\n{inner_prompt}\n{_DISPATCH_PROMPT_MARKER_CLOSE}"


def build_dispatch_command(
    dispatch_prompt: str, *, model_id: str, session_id: str, budget_cap_usd: float,
) -> list[str]:
    """The thin dispatcher's own launch command (Approach > "Dispatcher").
    No --permission-mode flag, matching run_skill_evals's own launch shape:
    default headless mode."""
    return [
        "claude", "-p", dispatch_prompt,
        "--output-format", "stream-json",
        "--verbose",
        "--include-partial-messages",
        "--model", model_id,
        "--session-id", session_id,
        "--max-budget-usd", str(budget_cap_usd),
    ]


# --- RunRecord -----------------------------------------------------------------

STATUS_OK = "ok"
STATUS_MISSING = "missing"

# Dispatch prompt's own naming -- these three are the values with special
# downstream meaning: invalid-answer is judge-only. Every other validity-check
# failure below is also a legal missing_reason string, named for the check it
# names (e.g. "wrong-agent") -- missing_reason takes the name of whichever
# check the second attempt failed, so it is not a closed enum.
MISSING_REASON_BUDGET = "budget"
MISSING_REASON_TIMEOUT = "timeout"
MISSING_REASON_INVALID_ANSWER = "invalid-answer"  # judge runs only

# Per-run validity check failure reasons (Approach > "Per-run validity
# checks"), one constant per bullet, kebab-cased like the three above.
VALIDITY_FAIL_PROMPT_MISMATCH = "prompt-mismatch"
VALIDITY_FAIL_EXTRA_DISPATCHER_TOOL_CALL = "extra-dispatcher-tool-call"
VALIDITY_FAIL_WRONG_AGENT = "wrong-agent"
VALIDITY_FAIL_MODEL_MISMATCH = "model-mismatch"
VALIDITY_FAIL_UNDECLARED_TOOL = "undeclared-tool"
VALIDITY_FAIL_LIVE_CHECKOUT_LEAK = "live-checkout-leak"
VALIDITY_FAIL_CONFIG_DIR_LEAK = "config-dir-leak"
VALIDITY_FAIL_RESULT_ERROR = "result-error"
VALIDITY_FAIL_SESSION_STORE_NOT_FOUND = "session-store-not-found"
VALIDITY_FAIL_SIDECAR_MISSING = "sidecar-missing"


@dataclass
class RunRecord:
    """RunRecord's JSONL schema, written verbatim to the campaign's records file."""

    campaign_id: str
    defect_id: str
    arm: str
    run_index: int
    opaque_run_id: str
    status: str
    missing_reason: str | None
    observed_model: str | None
    observed_tools: tuple[str, ...]
    out_of_session_paths: tuple[str, ...]
    findings_text: str | None
    wall_clock_s: float
    read_calls: int
    read_tokens_est: int
    partial_view_reads: int
    paged_followups: int
    whole_file_reads_of_changed_files: int
    dispatch_prompt_verbatim: bool
    cli_version: str
    ambient_config_commit: str

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> RunRecord:
        data = dict(data)
        data["observed_tools"] = tuple(data.get("observed_tools") or ())
        data["out_of_session_paths"] = tuple(data.get("out_of_session_paths") or ())
        return cls(**data)


def append_run_record(path: Path, record: RunRecord) -> None:
    append_run_records(path, (record,))


def append_run_records(path: Path, records: Sequence[RunRecord]) -> None:
    """Append every record in one open+write+close -- a block's own set of
    records is written in one call (run_campaign), not one file open per
    record."""
    if not records:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(record.to_dict()) + "\n" for record in records)
    with open(path, "a") as fh:
        fh.write(text)


def read_run_records(path: Path) -> list[RunRecord]:
    if not path.exists():
        return []
    records = []
    for line in path.read_text().splitlines():
        if line.strip():
            records.append(RunRecord.from_dict(json.loads(line)))
    return records


# --- Session-store lookup by session ID -----------------------------------


def find_session_jsonl_by_id(projects_root: Path, session_id: str) -> Path | None:
    """The one directory under projects_root holding <session_id>.jsonl,
    whatever that directory's own name -- never derived from the fixture
    path (Cleanup)."""
    if not projects_root.is_dir():
        return None
    matches = sorted(projects_root.glob(f"*/{session_id}.jsonl"))
    return matches[0] if matches else None


def session_store_dir_for(projects_root: Path, session_id: str) -> Path | None:
    jsonl = find_session_jsonl_by_id(projects_root, session_id)
    return jsonl.parent if jsonl is not None else None


# --- Dispatcher transcript parsing --------------------------------------------


@dataclass(frozen=True)
class DispatcherToolCall:
    tool_use_id: str
    name: str
    input: dict


@dataclass(frozen=True)
class DispatcherTranscript:
    tool_calls: tuple[DispatcherToolCall, ...]


def read_dispatcher_transcript(session_jsonl: Path) -> DispatcherTranscript:
    """Every Agent/Task-or-other tool_use the dispatcher's own persisted
    session transcript recorded -- the per-run validity check needs to see
    ALL of them (not only Agent/Task) to confirm the dispatcher's only tool
    call is the one expected Agent dispatch."""
    tool_calls: list[DispatcherToolCall] = []
    try:
        with open(session_jsonl) as fh:
            for raw in fh:
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") != "assistant":
                    continue
                for block in (rec.get("message") or {}).get("content") or []:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        tool_calls.append(
                            DispatcherToolCall(
                                tool_use_id=block.get("id", ""), name=block.get("name", ""),
                                input=block.get("input") or {},
                            )
                        )
    except OSError:
        pass
    return DispatcherTranscript(tool_calls=tuple(tool_calls))


def extract_final_result(lines: list[bytes]) -> tuple[bool | None, str | None]:
    """(is_error, terminal_reason) from the raw stream's last "result"
    event -- never persisted to the on-disk transcript (mirrors
    measure_subagent_model_resolution._extract_total_cost_usd's own reverse
    scan of the same stream). (None, None) when no result event was ever
    emitted (e.g. the process was killed on timeout first)."""
    for raw in reversed(lines):
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict) or rec.get("type") != "result":
            continue
        is_error = rec.get("is_error")
        return (is_error if isinstance(is_error, bool) else None), rec.get("terminal_reason")
    return None, None


# --- Subagent read-like call + tool-result extraction -------------------------


@dataclass(frozen=True)
class ReadLikeCall:
    tool_use_id: str
    tool_name: str  # "Read", "Grep", or "Glob"
    path: str
    offset: int | None  # Read's own paging parameter; None for Grep/Glob and for an unpaged Read


def extract_read_like_calls(subagent_jsonl: Path) -> list[ReadLikeCall]:
    calls: list[ReadLikeCall] = []
    try:
        with open(subagent_jsonl) as fh:
            for raw in fh:
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") != "assistant":
                    continue
                for block in (rec.get("message") or {}).get("content") or []:
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    name = block.get("name")
                    if name not in _READ_LIKE_TOOLS:
                        continue
                    tool_input = block.get("input") or {}
                    path = tool_input.get("file_path") if name == "Read" else tool_input.get("path")
                    if isinstance(path, str) and path:
                        calls.append(
                            ReadLikeCall(
                                tool_use_id=block.get("id", ""), tool_name=name, path=path,
                                offset=tool_input.get("offset") if isinstance(tool_input.get("offset"), int) else None,
                            )
                        )
    except OSError:
        pass
    return calls


def _tool_result_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(c.get("text", "") for c in content if isinstance(c, dict))
    return ""


def extract_tool_results(subagent_jsonl: Path) -> dict[str, str]:
    """tool_use_id -> its tool_result text, from "user" records in one
    subagent's own transcript."""
    results: dict[str, str] = {}
    try:
        with open(subagent_jsonl) as fh:
            for raw in fh:
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") != "user":
                    continue
                for block in (rec.get("message") or {}).get("content") or []:
                    if not isinstance(block, dict) or block.get("type") != "tool_result":
                        continue
                    tool_use_id = block.get("tool_use_id")
                    if isinstance(tool_use_id, str):
                        results[tool_use_id] = _tool_result_text(block.get("content"))
    except OSError:
        pass
    return results


def extract_final_text(subagent_jsonl: Path) -> str:
    """The subagent's own last assistant text block -- its findings
    (RunRecord.findings_text)."""
    last_text = ""
    try:
        with open(subagent_jsonl) as fh:
            for raw in fh:
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") != "assistant":
                    continue
                for block in (rec.get("message") or {}).get("content") or []:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = block.get("text")
                        if isinstance(text, str):
                            last_text = text
    except OSError:
        pass
    return last_text


@dataclass(frozen=True)
class ReadStats:
    read_calls: int
    read_tokens_est: int
    partial_view_reads: int
    paged_followups: int
    whole_file_reads_of_changed_files: int

    @classmethod
    def empty(cls) -> ReadStats:
        return cls(0, 0, 0, 0, 0)


def compute_read_stats(
    calls: list[ReadLikeCall], tool_results: dict[str, str], *, fixture_dir: Path, changed_relpaths: frozenset[str],
) -> ReadStats:
    """fixture_dir anchors both sides of the whole-file-read comparison: a
    real Read call's own file_path is always absolute, while
    changed_relpaths are bare repo-relative strings from `git diff
    --name-only`. Comparing the two unresolved always misses, since
    neither side ever equals the other as raw text."""
    read_calls = 0
    read_tokens_est = 0
    partial_view_reads = 0
    paged_followups = 0
    whole_file_reads = 0
    partial_seen_paths: set[str] = set()
    changed_abspaths = frozenset(_resolve(relpath, base_dir=fixture_dir) for relpath in changed_relpaths)

    for call in calls:
        if call.tool_name != "Read":
            continue
        read_calls += 1
        text = tool_results.get(call.tool_use_id, "")
        read_tokens_est += len(text) // _READ_SCOPE_CHARS_PER_TOKEN
        is_partial = _PARTIAL_VIEW_MARKER in text
        if is_partial:
            partial_view_reads += 1
            partial_seen_paths.add(call.path)
        elif call.offset is not None and call.path in partial_seen_paths:
            paged_followups += 1
        if call.offset is None and not is_partial and _resolve(call.path, base_dir=fixture_dir) in changed_abspaths:
            whole_file_reads += 1

    return ReadStats(
        read_calls=read_calls, read_tokens_est=read_tokens_est, partial_view_reads=partial_view_reads,
        paged_followups=paged_followups, whole_file_reads_of_changed_files=whole_file_reads,
    )


# --- Leak / out-of-session classification (Approach > "Out-of-session reads") -


def _resolve(raw_path: str, *, base_dir: Path) -> Path:
    """Resolve raw_path against base_dir when it isn't already absolute.
    Path.resolve() alone would anchor a relative path to this process's
    own cwd, not the subagent's actual runtime directory (ctx.fixture_dir)."""
    path = Path(raw_path)
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()


def is_changed_file_leak(resolved_path: Path, live_checkout_roots: tuple[Path, ...], changed_relpaths: tuple[str, ...]) -> bool:
    """True when resolved_path IS, or is a directory that CONTAINS, the live
    checkout's own copy of a file the defect's introducing or fix commit
    changed (Approach > "Per-run validity checks", first leak bullet)."""
    for root in live_checkout_roots:
        root = root.resolve()
        for relpath in changed_relpaths:
            target = (root / relpath).resolve()
            if target == resolved_path or target.is_relative_to(resolved_path):
                return True
    return False


def is_config_dir_leak(resolved_path: Path, projects_root: Path, own_session_paths: tuple[Path, ...]) -> bool:
    """True when resolved_path is the active config dir's projects/ root, is
    under it, or is a directory containing it -- except this run's own
    session transcript file or its own subagent sidecar directory (second
    leak bullet). own_session_paths must name exactly those two paths, not
    the whole per-arm session-store directory K concurrent runs of one arm
    share (Approach > "Cleanup"), or a sibling run's own transcript would
    be exempted too."""
    if any(resolved_path.is_relative_to(p.resolve()) for p in own_session_paths):
        return False
    projects_root = projects_root.resolve()
    return resolved_path.is_relative_to(projects_root) or projects_root.is_relative_to(resolved_path)


def is_out_of_session(resolved_path: Path, own_dirs: tuple[Path, ...]) -> bool:
    return not any(resolved_path.is_relative_to(d.resolve()) for d in own_dirs)


# --- Per-run validity checks (Approach > "Per-run validity checks") -----------


@dataclass(frozen=True)
class RunValidity:
    ok: bool
    failure_reason: str | None
    observed_model: str | None
    observed_tools: tuple[str, ...]
    out_of_session_paths: tuple[str, ...]
    findings_text: str | None
    stats: ReadStats
    # True only once the dispatcher's Agent prompt has actually been
    # compared and matched -- not merely "the failure wasn't
    # prompt-mismatch". A failure recorded before that comparison runs
    # (extra-dispatcher-tool-call, wrong-agent, prompt-mismatch itself)
    # carries False here, since the prompt was never confirmed.
    prompt_verbatim: bool = False


def _fail(
    reason: str, *, observed_model: str | None = None, observed_tools: tuple[str, ...] = (),
    prompt_verbatim: bool = False,
) -> RunValidity:
    return RunValidity(
        ok=False, failure_reason=reason, observed_model=observed_model, observed_tools=observed_tools,
        out_of_session_paths=(), findings_text=None, stats=ReadStats.empty(), prompt_verbatim=prompt_verbatim,
    )


def evaluate_run_validity(
    *,
    dispatcher_session_jsonl: Path,
    stream_lines: list[bytes],
    timed_out: bool,
    expected_agent_name: str,
    expected_inner_prompt: str,
    expected_model_id: str,
    agent_declared_tools: frozenset[str],
    fixture_dir: Path,
    own_dirs: tuple[Path, ...],
    projects_root: Path,
    own_session_paths: tuple[Path, ...],
    live_checkout_roots: tuple[Path, ...],
    changed_relpaths: tuple[str, ...],
) -> RunValidity:
    """Run every check in Approach > "Per-run validity checks", in the order
    a cheap check can short-circuit an expensive one. Returns the first
    failure found, or an ok RunValidity carrying every observable stat."""
    if timed_out:
        return _fail(MISSING_REASON_TIMEOUT)

    dispatcher = read_dispatcher_transcript(dispatcher_session_jsonl)
    if len(dispatcher.tool_calls) != 1:
        return _fail(VALIDITY_FAIL_EXTRA_DISPATCHER_TOOL_CALL)
    call = dispatcher.tool_calls[0]
    if call.name not in run_skill_evals.DISPATCH_TOOL_NAMES:
        return _fail(VALIDITY_FAIL_EXTRA_DISPATCHER_TOOL_CALL)
    if call.input.get("subagent_type") != expected_agent_name:
        return _fail(VALIDITY_FAIL_WRONG_AGENT)
    if call.input.get("prompt") != expected_inner_prompt:
        return _fail(VALIDITY_FAIL_PROMPT_MISMATCH)

    # Every failure from here on happens after the prompt comparison above
    # already matched, so each carries prompt_verbatim=True.
    is_error, terminal_reason = extract_final_result(stream_lines)
    if terminal_reason and "budget" in terminal_reason:
        return _fail(MISSING_REASON_BUDGET, prompt_verbatim=True)
    if is_error:
        return _fail(VALIDITY_FAIL_RESULT_ERROR, prompt_verbatim=True)

    dispatches = msmr.parse_subagent_dispatches(
        dispatcher_session_jsonl, requested_agent_declared_tools=agent_declared_tools
    )
    if not dispatches or dispatches[0].sidecar_missing:
        return _fail(VALIDITY_FAIL_SIDECAR_MISSING, prompt_verbatim=True)
    dispatch = dispatches[0]

    if dispatch.observed_model_ids != frozenset({expected_model_id}):
        return _fail(
            VALIDITY_FAIL_MODEL_MISMATCH, observed_model=next(iter(dispatch.observed_model_ids), None),
            observed_tools=tuple(sorted(dispatch.observed_tools)), prompt_verbatim=True,
        )
    undeclared = dispatch.observed_tools - agent_declared_tools
    if undeclared:
        return _fail(
            VALIDITY_FAIL_UNDECLARED_TOOL, observed_model=expected_model_id,
            observed_tools=tuple(sorted(dispatch.observed_tools)), prompt_verbatim=True,
        )

    subagent_dir = msmr.subagent_dir_for_session(dispatcher_session_jsonl)
    subagent_jsonls = sorted(subagent_dir.glob("*.jsonl"))
    if not subagent_jsonls:
        return _fail(VALIDITY_FAIL_SIDECAR_MISSING, prompt_verbatim=True)
    subagent_jsonl = subagent_jsonls[0]

    read_like_calls = extract_read_like_calls(subagent_jsonl)
    out_of_session: list[str] = []
    for read_call in read_like_calls:
        resolved = _resolve(read_call.path, base_dir=fixture_dir)
        if is_changed_file_leak(resolved, live_checkout_roots, changed_relpaths):
            return _fail(
                VALIDITY_FAIL_LIVE_CHECKOUT_LEAK, observed_model=expected_model_id,
                observed_tools=tuple(sorted(dispatch.observed_tools)), prompt_verbatim=True,
            )
        if is_config_dir_leak(resolved, projects_root, own_session_paths):
            return _fail(
                VALIDITY_FAIL_CONFIG_DIR_LEAK, observed_model=expected_model_id,
                observed_tools=tuple(sorted(dispatch.observed_tools)), prompt_verbatim=True,
            )
        if is_out_of_session(resolved, own_dirs):
            out_of_session.append(read_call.path)

    tool_results = extract_tool_results(subagent_jsonl)
    stats = compute_read_stats(
        read_like_calls, tool_results, fixture_dir=fixture_dir, changed_relpaths=frozenset(changed_relpaths)
    )
    findings_text = extract_final_text(subagent_jsonl)

    return RunValidity(
        ok=True, failure_reason=None, observed_model=expected_model_id,
        observed_tools=tuple(sorted(dispatch.observed_tools)), out_of_session_paths=tuple(out_of_session),
        prompt_verbatim=True,
        findings_text=findings_text, stats=stats,
    )


# --- Environment record (Approach > "Environment record") --------------------


@dataclass(frozen=True)
class EnvironmentRecord:
    cli_version: str
    ambient_config_commit: str
    dirty: bool


def ambient_config_checkout_root() -> Path:
    """The checkout that <config-dir>/CLAUDE.md resolves into (following the
    stow symlink) -- `git` commands work from any subdirectory of a repo,
    so this need not be the repo's own top-level directory."""
    return (config_dir() / "CLAUDE.md").resolve().parent


def default_live_checkout_roots() -> tuple[Path, ...]:
    """The two live checkouts a per-run validity check must never let a
    Read/Grep/Glob reach into (Approach > "Per-run validity checks"): the
    harness's own checkout, and the one the ambient config resolves into --
    distinct locations under this repo's own worktree-isolation model
    (repo-root CLAUDE.md), deduped here since they coincide outside it."""
    roots = {REPO_ROOT.resolve(), ambient_config_checkout_root().resolve()}
    return tuple(sorted(roots))


# Local process/git calls only, no network I/O -- guards a hung `claude
# --version` or a stale-locked local .git from blocking a block's start/end
# environment reading with no exit, mirroring fixture_repo.py's own
# _LOCAL_GIT_TIMEOUT_S rationale for the same magnitude.
_ENVIRONMENT_READ_TIMEOUT_S = 10.0


def read_environment_record(*, checkout_root: Path | None = None) -> EnvironmentRecord:
    root = checkout_root if checkout_root is not None else ambient_config_checkout_root()
    version_proc = subprocess.run(
        ["claude", "--version"], capture_output=True, text=True, check=False, timeout=_ENVIRONMENT_READ_TIMEOUT_S,
    )
    cli_version = (version_proc.stdout or version_proc.stderr).strip()
    commit_proc = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=False,
        timeout=_ENVIRONMENT_READ_TIMEOUT_S,
    )
    status_proc = subprocess.run(
        ["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True, check=False,
        timeout=_ENVIRONMENT_READ_TIMEOUT_S,
    )
    return EnvironmentRecord(
        cli_version=cli_version, ambient_config_commit=commit_proc.stdout.strip(),
        dirty=bool(status_proc.stdout.strip()),
    )


# --- Run store: write-ahead record, resume sweep, lock (Approach > "Cleanup") -

_LOCK_FILENAME = "lock.pid"
_WRITE_AHEAD_FILENAME = "write-ahead.jsonl"
_COMPLETED_BLOCKS_FILENAME = "completed-blocks.json"


def _atomic_write_text(path: Path, text: str) -> None:
    """Write text via a same-directory temp file plus os.replace (atomic on
    POSIX), so a crash mid-write leaves the previous complete file in place
    rather than a truncated one -- mirrors review_bench.defects's own
    _atomic_write_text, duplicated rather than imported since that helper
    is that module's own private convention, not a public export."""
    tmp_path = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    tmp_path.write_text(text)
    os.replace(tmp_path, path)


class RunStoreLocked(RuntimeError):
    pass


@dataclass(frozen=True)
class WriteAheadEntry:
    defect_id: str
    directory: str
    session_id: str  # "" for a directory-creation-only entry, before any run against it has a session ID yet


# The directory-creation half of a write-ahead record (Approach > "Cleanup":
# "each directory it creates, as soon as it exists") predates any run
# launching against it, so no session ID exists yet to pair with it.
_NO_SESSION_ID_YET = ""


class RunStore:
    """The local run store `smoke`/`run`/`judge` write ahead to, so a hard
    interruption's sweep on resume deletes exactly what an abandoned
    attempt recorded -- never a directory-name glob (Approach > "Cleanup")."""

    def __init__(self, store_dir: Path):
        self.store_dir = store_dir
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self.lock_path = store_dir / _LOCK_FILENAME
        self.write_ahead_path = store_dir / _WRITE_AHEAD_FILENAME
        self.completed_blocks_path = store_dir / _COMPLETED_BLOCKS_FILENAME

    def acquire_lock(self, *, pid: int | None = None, is_alive=None) -> None:
        """Acquire the lock via os.link, an atomically-exclusive create whose
        target's content is always complete the instant it exists -- write
        the pid to a temp file first, then os.link() it into place, so no
        caller ever observes a lock file that exists but is still empty
        (which os.O_EXCL alone would allow, in the gap between an exclusive
        create and its content write). On finding an existing lock naming a
        dead PID, unlink it and retry rather than writing over it directly,
        since a concurrent acquirer could win the race between this check
        and the unlink."""
        pid = pid if pid is not None else os.getpid()
        is_alive = is_alive if is_alive is not None else pid_is_alive
        while True:
            tmp_path = self.lock_path.with_name(f"{self.lock_path.name}.tmp-{uuid.uuid4().hex}")
            # A hard kill (SIGKILL/OOM) between this write and the os.link
            # below leaves one orphaned tmp-<uuid> file, unswept -- same
            # order of severity as this store's other accepted residuals.
            tmp_path.write_text(str(pid))
            try:
                os.link(tmp_path, self.lock_path)
            except FileExistsError:
                tmp_path.unlink(missing_ok=True)
                try:
                    existing_pid = int(self.lock_path.read_text().strip())
                except (ValueError, OSError):
                    existing_pid = None
                if existing_pid is not None and is_alive(existing_pid):
                    raise RunStoreLocked(
                        f"run store lock at {self.lock_path} is held by live pid {existing_pid}"
                    ) from None
                self.lock_path.unlink(missing_ok=True)
                continue
            tmp_path.unlink(missing_ok=True)
            return

    def release_lock(self) -> None:
        self.lock_path.unlink(missing_ok=True)

    def record_directory(self, defect_id: str, directory: Path, session_id: str) -> None:
        """Append-only and safe to call from concurrent worker-pool threads:
        each call opens its own append-mode ("a") file descriptor, and a
        single small write() of one already-formatted line is one syscall
        under POSIX O_APPEND, which never interleaves with another
        process's or thread's own single-syscall append."""
        entry = WriteAheadEntry(defect_id=defect_id, directory=str(directory), session_id=session_id)
        with open(self.write_ahead_path, "a") as fh:
            fh.write(json.dumps(asdict(entry)) + "\n")

    def completed_block_ids(self) -> set[str]:
        if not self.completed_blocks_path.exists():
            return set()
        return set(json.loads(self.completed_blocks_path.read_text()))

    def mark_block_complete(self, defect_id: str) -> None:
        completed = self.completed_block_ids()
        completed.add(defect_id)
        _atomic_write_text(self.completed_blocks_path, json.dumps(sorted(completed)))

    def pending_entries(self) -> list[WriteAheadEntry]:
        if not self.write_ahead_path.exists():
            return []
        completed = self.completed_block_ids()
        entries = []
        for line in self.write_ahead_path.read_text().splitlines():
            if not line.strip():
                continue
            data = json.loads(line)
            if data["defect_id"] not in completed:
                entries.append(WriteAheadEntry(**data))
        return entries

    def sweep_abandoned(self, projects_root: Path) -> list[WriteAheadEntry]:
        """Delete every directory and session store a still-pending
        write-ahead entry names, then return the entries swept -- the
        caller reruns each swept defect's block whole."""
        swept = self.pending_entries()
        for entry in swept:
            directory = Path(entry.directory)
            if directory.exists():
                shutil.rmtree(directory, ignore_errors=True)
            if not entry.session_id:
                continue  # a directory-creation-only entry: no run launched against it yet
            store_dir = session_store_dir_for(projects_root, entry.session_id)
            if store_dir is not None and store_dir.exists():
                shutil.rmtree(store_dir, ignore_errors=True)
        return swept


def pid_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # process exists, just owned by someone else
    except OSError:
        return False
    return True


# --- Fault injection (smoke only) -----------------------------------------

FAULT_WRONG_AGENT = "wrong-agent"
FAULT_EXTRA_TOOL_CALL = "extra-tool-call"
KNOWN_SMOKE_FAULTS: frozenset[str] = frozenset({FAULT_WRONG_AGENT, FAULT_EXTRA_TOOL_CALL})


def apply_fault_injection(dispatch_prompt: str, *, fault: str | None) -> str:
    """Mutate the dispatcher's own -p prompt so a real `smoke` launch can
    drive a genuine validity-check failure through retry-then-missing
    against the real CLI. `run` never calls this: its own CLI wiring
    (run_review_bench.py) has no fault-injection argument to pass through."""
    if fault is None:
        return dispatch_prompt
    if fault == FAULT_WRONG_AGENT:
        return dispatch_prompt + "\n\nActually, dispatch the `Explore` agent instead of the one named above."
    if fault == FAULT_EXTRA_TOOL_CALL:
        return dispatch_prompt + "\n\nAlso call the Bash tool once, running `true`, before you finish."
    raise ValueError(f"unknown smoke fault {fault!r}, expected one of {sorted(KNOWN_SMOKE_FAULTS)}")


# --- Single-run execution -----------------------------------------------------


@dataclass(frozen=True)
class RunContext:
    """Everything one run needs beyond its own (arm, run_index): the
    defect's identity and fixture, and the arm's own agent name/tools."""

    campaign_id: str
    defect_id: str
    subject: str
    agent_name: str
    model_id: str
    agent_declared_tools: frozenset[str]
    fixture_dir: Path
    live_checkout_roots: tuple[Path, ...]
    changed_relpaths: tuple[str, ...]
    budget_cap_usd: float
    timeout_s: int
    # The block's own start-of-block reading (Approach > "Environment
    # record") -- stamped onto every RunRecord in the block, not
    # re-measured per run. Only the block itself reads the environment
    # again, once, at its end, to detect drift.
    environment: EnvironmentRecord


def execute_run(
    ctx: RunContext, *, arm: str, run_index: int, session_id: str, fault: str | None = None,
    launch=msmr._run_claude_to_completion,
) -> RunRecord:
    """Launch one reviewer run under its caller-assigned session_id and
    evaluate its validity. Never retries -- the caller (run_one_with_retry)
    owns retry-then-missing and must write this run's session ID ahead of
    calling this function (Approach > "Cleanup": "each run's session ID,
    before that run launches")."""
    inner_prompt = build_review_prompt(ctx.subject)
    dispatch_prompt = apply_fault_injection(
        build_dispatcher_prompt(ctx.agent_name, inner_prompt), fault=fault
    )
    cmd = build_dispatch_command(
        dispatch_prompt, model_id=ctx.model_id, session_id=session_id, budget_cap_usd=ctx.budget_cap_usd,
    )

    start = time.monotonic()
    lines, timed_out = launch(cmd, ctx.fixture_dir, ctx.timeout_s)
    wall_clock_s = time.monotonic() - start

    projects_root = config_dir() / "projects"
    session_jsonl = find_session_jsonl_by_id(projects_root, session_id)

    if session_jsonl is None:
        validity = _fail(VALIDITY_FAIL_SESSION_STORE_NOT_FOUND)
    else:
        # own_session_paths is exactly this run's own session transcript and
        # its own subagent sidecar dir, not session_jsonl.parent (the whole
        # per-arm session-store directory K concurrent runs of this arm
        # share) -- see is_config_dir_leak's own docstring.
        subagent_dir = msmr.subagent_dir_for_session(session_jsonl)
        validity = evaluate_run_validity(
            dispatcher_session_jsonl=session_jsonl, stream_lines=lines, timed_out=timed_out,
            expected_agent_name=ctx.agent_name, expected_inner_prompt=inner_prompt,
            expected_model_id=ctx.model_id, agent_declared_tools=ctx.agent_declared_tools,
            fixture_dir=ctx.fixture_dir, own_dirs=(ctx.fixture_dir, session_jsonl, subagent_dir),
            projects_root=projects_root, own_session_paths=(session_jsonl, subagent_dir),
            live_checkout_roots=ctx.live_checkout_roots, changed_relpaths=ctx.changed_relpaths,
        )

    status = STATUS_OK if validity.ok else STATUS_MISSING
    stats = validity.stats
    record = RunRecord(
        campaign_id=ctx.campaign_id, defect_id=ctx.defect_id, arm=arm, run_index=run_index,
        opaque_run_id=uuid.uuid4().hex[:12], status=status, missing_reason=validity.failure_reason,
        observed_model=validity.observed_model, observed_tools=validity.observed_tools,
        out_of_session_paths=validity.out_of_session_paths, findings_text=validity.findings_text,
        wall_clock_s=wall_clock_s, read_calls=stats.read_calls, read_tokens_est=stats.read_tokens_est,
        partial_view_reads=stats.partial_view_reads, paged_followups=stats.paged_followups,
        whole_file_reads_of_changed_files=stats.whole_file_reads_of_changed_files,
        dispatch_prompt_verbatim=validity.prompt_verbatim,
        cli_version=ctx.environment.cli_version, ambient_config_commit=ctx.environment.ambient_config_commit,
    )
    return record


@dataclass(frozen=True)
class RunAttempt:
    record: RunRecord
    session_id: str


def run_one_with_retry(
    ctx: RunContext, *, arm: str, run_index: int, fault: str | None = None,
    launch=msmr._run_claude_to_completion, run_store: RunStore | None = None,
) -> RunAttempt:
    """Execute one run against ctx.fixture_dir (shared by every run of this
    (defect, arm) -- Approach > "Cleanup": "K concurrent runs share one
    fixture per arm"); on validity failure, retry exactly once with a fresh
    session ID before recording it missing (Approach > "Per-run validity
    checks": "A failed run is retried once. A run that fails twice is
    recorded as missing...").  The fixture itself is read-only to every
    tool an arm holds, so a retry never needs a fresh one.

    Each attempt's session ID is write-ahead recorded before its own
    execute_run()/launch() call, not after. A hard interruption during the
    up-to-REVIEWER_TIMEOUT_S launch is the highest-probability window for
    one, which the write-ahead record exists to make recoverable on resume
    (Approach > "Cleanup").

    Returns every attempt's own session ID too (not only the winning one),
    so the caller's cleanup can find every session store this run actually
    created, including a discarded first attempt's."""
    attempt: RunAttempt | None = None
    for _try in range(2):
        session_id = str(uuid.uuid4())
        if run_store is not None:
            run_store.record_directory(ctx.defect_id, ctx.fixture_dir, session_id)
        record = execute_run(ctx, arm=arm, run_index=run_index, session_id=session_id, fault=fault, launch=launch)
        attempt = RunAttempt(record=record, session_id=session_id)
        if record.status == STATUS_OK:
            return attempt
    return attempt


# --- Block / campaign orchestration (Approach > "Terms", "Cleanup") ----------


@dataclass(frozen=True)
class DefectFixtureSpec:
    """One defect's per-arm launch inputs, already built by the caller
    (fixture_repo.build_defect_fixture + arms.install_arm) -- runner.py
    itself never builds fixtures or arms; it only launches against them."""

    defect_id: str
    subject: str
    arm_fixture_dirs: dict[str, Path]  # arm -> its one shared fixture directory
    arm_agent_names: dict[str, str]  # arm -> "bench-<lens>"
    agent_declared_tools: frozenset[str]
    live_checkout_roots: tuple[Path, ...]
    changed_relpaths: tuple[str, ...]


@dataclass(frozen=True)
class BlockResult:
    records: tuple[RunRecord, ...]
    # One representative session ID per arm actually launched -- every run
    # (and every retry) against one arm's fixture_dir lands in the same
    # on-disk session store (Claude Code hashes only the project path), so
    # any one of them locates it for cleanup: found by session ID, never
    # derived from the fixture path.
    representative_session_id_by_arm: dict[str, str]


# Caps run_defect_block's environment-drift rerun loop. Each rerun re-spends
# every (arm, run_index) pair's real, billable claude -p dispatch. A
# flapping ambient checkout is routine on this repo's own multi-worktree
# setup, so an uncapped retry against one would re-spend real Claude
# subscription budget with no bound.
MAX_ENVIRONMENT_DRIFT_RETRIES = 2


class EnvironmentDriftExceededError(RuntimeError):
    """Raised when a defect's block still drifts after MAX_ENVIRONMENT_DRIFT_RETRIES reruns."""


def run_defect_block(
    spec: DefectFixtureSpec, *, arms: tuple[str, ...], k: int, seed: int, campaign_id: str,
    model_id: str = REVIEWER_MODEL_ID, budget_cap_usd: float = REVIEWER_BUDGET_CAP_USD,
    timeout_s: int = REVIEWER_TIMEOUT_S, run_store: RunStore | None = None,
    launch=msmr._run_claude_to_completion, fault: str | None = None,
    workers: int = run_skill_evals.DEFAULT_WORKERS,
) -> BlockResult:
    """Run every (arm, run_index) in spec.defect_id's seeded block order
    through a worker pool (Approach > Terms > "campaign"). Rerun the whole
    block, replacing its first attempt's records rather than joining them,
    when the environment reading at the block's start differs from the one
    at its end (Approach > "Environment record"). Reruns at most
    MAX_ENVIRONMENT_DRIFT_RETRIES times, printing what changed on every
    retried rerun. The final, fatal drift is folded into
    EnvironmentDriftExceededError's own message instead."""
    block_plan = build_block_plan(spec.defect_id, arms, k, seed)

    for attempt_number in range(MAX_ENVIRONMENT_DRIFT_RETRIES + 1):
        env_start = read_environment_record()

        # env_start is bound as a default argument, not read from the
        # enclosing scope, so each loop iteration's closure captures its own
        # attempt's reading rather than whichever one is live when pool.map
        # actually calls it.
        def _run_one(arm_and_index: tuple[str, int], *, env_start=env_start) -> RunAttempt:
            arm, run_index = arm_and_index
            ctx = RunContext(
                campaign_id=campaign_id, defect_id=spec.defect_id, subject=spec.subject,
                agent_name=spec.arm_agent_names[arm], model_id=model_id,
                agent_declared_tools=spec.agent_declared_tools, fixture_dir=spec.arm_fixture_dirs[arm],
                live_checkout_roots=spec.live_checkout_roots, changed_relpaths=spec.changed_relpaths,
                budget_cap_usd=budget_cap_usd, timeout_s=timeout_s, environment=env_start,
            )
            return run_one_with_retry(ctx, arm=arm, run_index=run_index, fault=fault, launch=launch, run_store=run_store)

        # Threads, not run_skill_evals's ProcessPoolExecutor precedent: each
        # worker's own work is a subprocess launch plus disk/file-glob reads, all
        # I/O that releases the GIL, and _run_one is a closure over per-block
        # state that a process pool would need to pickle.
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            attempts = list(pool.map(_run_one, block_plan.ordered_runs))

        records = [attempt.record for attempt in attempts]
        session_id_by_arm: dict[str, str] = {}
        for (arm, _run_index), attempt in zip(block_plan.ordered_runs, attempts, strict=True):
            session_id_by_arm.setdefault(arm, attempt.session_id)

        env_end = read_environment_record()
        if env_start == env_end:
            if run_store is not None:
                run_store.mark_block_complete(spec.defect_id)
            return BlockResult(records=tuple(records), representative_session_id_by_arm=session_id_by_arm)

        drift_description = (
            f"cli_version {env_start.cli_version!r}->{env_end.cli_version!r}, "
            f"ambient_config_commit {env_start.ambient_config_commit!r}->{env_end.ambient_config_commit!r}, "
            f"dirty {env_start.dirty}->{env_end.dirty}"
        )

        if attempt_number == MAX_ENVIRONMENT_DRIFT_RETRIES:
            raise EnvironmentDriftExceededError(
                f"{spec.defect_id}'s block still drifted after {MAX_ENVIRONMENT_DRIFT_RETRIES} rerun(s) -- "
                f"the ambient checkout or CLI version isn't holding still long enough for one full block "
                f"to complete ({drift_description})"
            )

        print(
            f"run: {spec.defect_id}'s block drifted during its run (rerun "
            f"{attempt_number + 1}/{MAX_ENVIRONMENT_DRIFT_RETRIES}) -- "
            f"{drift_description}; rerunning the whole block",
            file=sys.stderr,
        )


def cleanup_defect_block(spec: DefectFixtureSpec, block_result: BlockResult, *, projects_root: Path) -> None:
    """Delete every arm's fixture directory and session store for one
    defect -- called only after every run and retry in its block has
    finished (Approach > "Cleanup"). Each store is found by a representative
    session ID, never computed from the fixture path."""
    for arm, fixture_dir in spec.arm_fixture_dirs.items():
        session_id = block_result.representative_session_id_by_arm.get(arm)
        if session_id is not None:
            store_dir = session_store_dir_for(projects_root, session_id)
            if store_dir is not None and store_dir.exists():
                shutil.rmtree(store_dir, ignore_errors=True)
        if fixture_dir.exists():
            shutil.rmtree(fixture_dir, ignore_errors=True)


@dataclass(frozen=True)
class CampaignResult:
    campaign_id: str
    block_results: dict[str, BlockResult]


def run_campaign(
    defect_ids: list[str], *, build_spec, arms: tuple[str, ...], k: int, seed: int, campaign_id: str,
    run_store: RunStore, records_path: Path, projects_root: Path,
    model_id: str = REVIEWER_MODEL_ID, budget_cap_usd: float = REVIEWER_BUDGET_CAP_USD,
    timeout_s: int = REVIEWER_TIMEOUT_S, launch=msmr._run_claude_to_completion, fault: str | None = None,
    workers: int = run_skill_evals.DEFAULT_WORKERS,
) -> CampaignResult:
    """Runs its blocks one at a time (Approach > Terms > "campaign"). On
    resume, skips every already-completed block and, first, sweeps whatever
    an abandoned attempt's write-ahead record left of a partial one
    (Approach > "Cleanup"). `build_spec(defect_id) -> DefectFixtureSpec`
    builds that defect's fixtures and arm files -- kept as a caller-supplied
    callback so this function needs no ConfirmedDefect/source-repo
    knowledge of its own."""
    run_store.acquire_lock()
    try:
        swept = run_store.sweep_abandoned(projects_root)
        if swept:
            print(
                f"run: swept {len(swept)} directory/session-store pair(s) left by an abandoned attempt",
                file=sys.stderr,
            )
        completed = run_store.completed_block_ids()
        block_results: dict[str, BlockResult] = {}
        for defect_id in defect_ids:
            if defect_id in completed:
                continue
            spec = build_spec(defect_id)
            result = run_defect_block(
                spec, arms=arms, k=k, seed=seed, campaign_id=campaign_id, model_id=model_id,
                budget_cap_usd=budget_cap_usd, timeout_s=timeout_s, run_store=run_store,
                launch=launch, fault=fault, workers=workers,
            )
            append_run_records(records_path, result.records)
            cleanup_defect_block(spec, result, projects_root=projects_root)
            block_results[defect_id] = result
        return CampaignResult(campaign_id=campaign_id, block_results=block_results)
    finally:
        run_store.release_lock()


# --- CLI wiring: fixture + arm construction for one defect --------------------


def _head_commit_subject(fixture_dir: Path) -> str:
    result = subprocess.run(
        ["git", "log", "-1", "--format=%s", "HEAD"], cwd=fixture_dir, capture_output=True, text=True,
        check=True, timeout=_ENVIRONMENT_READ_TIMEOUT_S,
    )
    return result.stdout.strip()


ARMS_SNAPSHOT_ROOT = REPO_ROOT / "evals" / "review_bench" / "arms"


def build_defect_fixture_spec(
    defect: ConfirmedDefect, *, arm_names: tuple[str, ...], source_repo: Path,
    live_checkout_roots: tuple[Path, ...], arms_snapshot_root: Path = ARMS_SNAPSHOT_ROOT,
    run_store: RunStore | None = None,
) -> DefectFixtureSpec:
    """Build one defect's per-arm fixture directory (fixture_repo.py) and
    install that arm's already-frozen `bench-<lens>.md` snapshot
    (evals/review_bench/arms/<arm>/, written by `snapshot-arms` at freeze
    time) into it. Never re-renders from the live production agent files
    here: a run must exercise the frozen arm body, not whatever production
    has drifted to since the freeze (Approach > "Freeze and invalidation")."""
    arm_fixture_dirs: dict[str, Path] = {}
    changed_relpaths: tuple[str, ...] = ()
    subject = ""
    for arm in arm_names:
        fixture_dir = msmr._resolved_temp_project_dir(FIXTURE_DIR_PREFIX)
        if run_store is not None:
            # As soon as it exists, before this defect's fixture is even
            # populated (Approach > "Cleanup") -- no session ID exists yet,
            # since no run has launched against it.
            run_store.record_directory(defect.id, fixture_dir, _NO_SESSION_ID_YET)
        fixture = build_defect_fixture(source_repo, defect, fixture_dir)
        changed_relpaths = tuple(stat.path for stat in fixture.changed_files)
        subject = _head_commit_subject(fixture_dir)
        snapshot_path = arms_snapshot_root / arm / f"bench-{defect.lens}.md"
        agents_dir = fixture_dir / ".claude" / "agents"
        agents_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(snapshot_path, agents_dir / snapshot_path.name)
        arm_fixture_dirs[arm] = fixture_dir

    return DefectFixtureSpec(
        defect_id=defect.id, subject=subject, arm_fixture_dirs=arm_fixture_dirs,
        arm_agent_names={arm: f"bench-{defect.lens}" for arm in arm_names},
        agent_declared_tools=frozenset(arms_mod.ARM_TOOLS), live_checkout_roots=live_checkout_roots,
        changed_relpaths=changed_relpaths,
    )
