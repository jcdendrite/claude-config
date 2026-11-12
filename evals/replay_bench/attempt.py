"""One launch attempt of one replay run: build the fixture, launch, check, capture the change, and clean up.

Every attempt ends in a `finally` that deletes its fixture, its session store and its attempt dir, whether it
was valid, invalid, timed out, raised or was interrupted. Its `sessions/` file stays, as every Claude Code
session leaves one.

The instruction-load verdict derives from the attempt dir alone: the log read is
`<attempt dir>/instructions-loaded.jsonl`, and nothing in the fixture is read as a log.
"""
from __future__ import annotations

import subprocess
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

import measure_subagent_model_resolution as msmr
from review_bench import runner as review_runner
from review_bench.fixture_repo import UnsafeFixtureConfigError
from review_bench.runner import HarnessInvalidatedError, RunStore

from replay_bench import account as account_mod
from replay_bench import checks, diffing, launch, tasks
from replay_bench.arms import Arm
from replay_bench.render import safe_text
from replay_bench.spend import SpendAppendError, SpendFile, SpendLine, is_usable_cost

REASON_FIXTURE_ERROR = "fixture-error"
REASON_ANALYSIS_ERROR = "analysis-error"
REASON_LAUNCH_NO_OUTPUT = "launch-no-output"
REASON_LAUNCH_ERROR = "launch-error"
# A retry would fail the same way on the same input, or would hide evidence about the instrument.
NO_RETRY_REASONS = checks.INSTRUMENT_REASONS | {REASON_FIXTURE_ERROR, REASON_ANALYSIS_ERROR}
# The run never reached its end, so a missing log or session file says nothing about the instrument.
# `launch-no-output` and `launch-error` return before this check runs, so they are not listed.
_RUN_DID_NOT_COMPLETE_REASONS = frozenset({
    review_runner.MISSING_REASON_TIMEOUT, review_runner.VALIDITY_FAIL_NO_RESULT_EVENT,
    review_runner.VALIDITY_FAIL_SESSION_STORE_NOT_FOUND,
})
_ABSENCE_INSTRUMENT_REASONS = frozenset({
    checks.REASON_INSTRUMENT_MISSING, checks.REASON_INSTRUMENT_LOG_INVALID, checks.REASON_ACCOUNT_HOOKS_MISSING,
})
BASELINE_GIT_NAME = "baseline.git"
# How many failing tokens a `missing_detail` names. The run record holds all of them.
MAX_DETAIL_ITEMS = 5

Launch = Callable[[list[str], Path, int], tuple[list[bytes], bool]]


@dataclass(frozen=True)
class RunSettings:
    """Campaign-level inputs every attempt shares."""

    campaign_id: str
    attempts_root: Path
    local_dir: Path
    model_id: str
    budget_cap_usd: float
    timeout_s: int
    fixture_git_timeout_s: float
    run_store: RunStore
    spend: SpendFile
    runner_environ: Mapping[str, str]
    launch: Launch
    # Set by the first fatal worker failure. No run that has not launched yet launches after it.
    stop: threading.Event = field(default_factory=threading.Event)
    # (task ID, session ID) of each launch that began and could not be charged.
    uncharged_launches: list[tuple[str, str]] = field(default_factory=list)
    # Fatal worker failures other than the abort a failure causes in its siblings, in the order they were caught.
    failures: list[BaseException] = field(default_factory=list)


@dataclass(frozen=True)
class BlockContext:
    """Inputs one task's block shares: the task, its account, and the block's start-of-block environment reading."""

    task: tasks.ReplayTask
    account: account_mod.Account
    environment: review_runner.EnvironmentRecord
    live_checkout_roots: tuple[Path, ...]


@dataclass(frozen=True)
class AttemptResult:
    """`log`, `transcript`, `toolkit` and `fixture_dir` are the evidence smoke's block calibration reads after
    the fixture and the attempt dir are gone. They are None when the attempt failed before they existed."""

    reason: str | None
    detail: str | None
    session_id: str = ""
    launched: bool = False
    completed: bool = False
    cost_usd: float = 0.0
    is_cap_charge: bool = False
    usage: review_runner.ResultUsage = field(default_factory=review_runner.ResultUsage.unavailable)
    wall_clock_s: float = 0.0
    fixture_build_s: float | None = None
    observed_model: str | None = None
    capture: diffing.ChangeCapture | None = None
    first_turn_prompt_size: int | None = None
    subagent_loads: Mapping = field(default_factory=dict)
    permission_denials: int = 0
    allow_list_reads: tuple[checks.AllowListRead, ...] = ()
    denied_other_toolkit_reads: tuple[str, ...] = ()
    path_check: checks.PathCheck | None = None
    out_of_session_paths: tuple[str, ...] = ()
    log: checks.InstructionLog | None = None
    transcript: checks.SubagentTranscript | None = None
    toolkit: checks.ToolkitPaths | None = None
    fixture_dir: Path | None = None

    def __post_init__(self) -> None:
        if self.detail is not None:
            object.__setattr__(self, "detail", safe_text(self.detail))

    @property
    def ok(self) -> bool:
        return self.reason is None


@dataclass
class _Progress:
    """What an attempt has made so far, for the charge on an interrupt and for the cleanup."""

    attempt_dir: Path | None = None
    fixture_dir: Path | None = None
    session_id: str = ""
    launched: bool = False
    charged: bool = False


def git_timeout_detail(limit_s: float) -> str:
    return f"git timed out after {limit_s:g} s; raise --fixture-git-timeout-s"


def exception_detail(exc: BaseException) -> str:
    """The class and a message that cannot hold a child's output or argv."""
    if isinstance(exc, subprocess.TimeoutExpired):
        return "a git call timed out"
    if isinstance(exc, subprocess.CalledProcessError):
        return f"git exited {exc.returncode}"
    if isinstance(exc, OSError):
        return review_runner._os_error_detail(exc)
    return f"{type(exc).__name__}: {exc}"


def _log_size(log_path: Path) -> int | None:
    try:
        return log_path.stat().st_size
    except OSError:
        return None


def wait_for_hook_log_to_settle(log_path: Path, *, timed_out: bool) -> None:
    """Returns once the log's size holds across two polls, or at `SESSION_FLUSH_TIMEOUT_S`. The hook runs
    asynchronously, so a line can land after the session's own files stop growing. A log absent at one poll
    and present at the next counts as growth, and an absent log never raises. A timed-out attempt skips the wait."""
    if timed_out:
        return
    deadline = time.monotonic() + review_runner.SESSION_FLUSH_TIMEOUT_S
    previous_size = _log_size(log_path)
    while True:
        time.sleep(review_runner.SESSION_FLUSH_POLL_INTERVAL_S)
        size = _log_size(log_path)
        if size == previous_size or time.monotonic() >= deadline:
            return
        previous_size = size


def _failed(reason: str, detail: str, progress: _Progress, **fields) -> AttemptResult:
    return AttemptResult(reason=reason, detail=detail, session_id=progress.session_id, **fields)


def _charge(
    spend: SpendFile, block: BlockContext, arm: Arm, run_index: int, attempt_number: int, progress: _Progress,
    usage_cost: float | None,
) -> tuple[float, bool]:
    """Writes the attempt's spend line. An attempt with no usable cost figure, a non-finite or negative one
    included, is charged at the budget cap."""
    is_cap_charge = not is_usable_cost(usage_cost)
    cost = spend.budget_cap_usd if is_cap_charge else usage_cost
    spend.append(SpendLine(
        task_id=block.task.id, arm=arm.name, run_index=run_index, attempt=attempt_number,
        account_key=block.account.key, session_id=progress.session_id, cost_usd=cost, is_cap_charge=is_cap_charge,
    ))
    progress.charged = True
    return cost, is_cap_charge


def _cleanup(progress: _Progress, projects_root: Path) -> None:
    if progress.session_id:
        store_dir = review_runner.session_store_dir_for(projects_root, progress.session_id)
        if store_dir is not None and store_dir.is_dir():
            review_runner.remove_tree_reporting_failure(store_dir)
    for directory in (progress.fixture_dir, progress.attempt_dir):
        if directory is not None and directory.is_dir():
            review_runner.remove_tree_reporting_failure(directory)


def run_attempt(
    settings: RunSettings, block: BlockContext, arm: Arm, run_index: int, attempt_number: int,
) -> AttemptResult:
    """Never retries. Raises HarnessInvalidatedError for a fixture directory under `$HOME`,
    LaunchAbortedError for a run that starts after a sibling failed, and SpendAppendError for a failed spend
    append. It re-raises an interrupt after charging the cap."""
    progress = _Progress()
    try:
        _refuse_after_a_sibling_failed(settings)
        return _attempt_body(settings, block, arm, run_index, attempt_number, progress)
    except BaseException as exc:
        if progress.launched and not progress.charged:
            try:
                if not isinstance(exc, SpendAppendError):
                    _charge(settings.spend, block, arm, run_index, attempt_number, progress, None)
            except SpendAppendError:
                settings.uncharged_launches.append((block.task.id, progress.session_id))
                raise
            if isinstance(exc, SpendAppendError):
                settings.uncharged_launches.append((block.task.id, progress.session_id))
        raise
    finally:
        _cleanup(progress, block.account.config_dir / "projects")


def _refuse_after_a_sibling_failed(settings: RunSettings) -> None:
    if settings.stop.is_set():
        raise msmr.LaunchAbortedError("a sibling run failed, so no further launch starts")


def _attempt_body(
    settings: RunSettings, block: BlockContext, arm: Arm, run_index: int, attempt_number: int, progress: _Progress,
) -> AttemptResult:
    task, account = block.task, block.account
    attempt_dir = settings.attempts_root / f"{task.id}-{arm.name}-{run_index}-{attempt_number}"
    try:
        settings.attempts_root.mkdir(parents=True, exist_ok=True)
        attempt_dir.mkdir()
    except FileExistsError:
        return _failed(REASON_FIXTURE_ERROR, f"attempt dir {str(attempt_dir)!r} already exists", progress)
    except OSError as exc:
        return _failed(REASON_FIXTURE_ERROR, exception_detail(exc), progress)
    progress.attempt_dir = attempt_dir

    fixture_dir = msmr._resolved_temp_project_dir(f"{review_runner.FIXTURE_DIR_PREFIX}replay-")
    progress.fixture_dir = fixture_dir
    settings.run_store.record_directory(task.id, fixture_dir, "")
    account_mod.refuse_fixture_dir_under_home(fixture_dir, account.home)

    try:
        settings_text = arm.settings_text(account.config_dir)
        allow_paths = launch.read_allow_paths(account.config_dir, account.home)
    except launch.UnsafeConfigPathError as exc:
        return _failed(REASON_FIXTURE_ERROR, str(exc), progress)
    toolkit = checks.ToolkitPaths.from_config_dir(account.config_dir, allow_paths)

    build_started = time.monotonic()
    try:
        base_commit = tasks.build_task_fixture(
            task, fixture_dir, attempt_dir / BASELINE_GIT_NAME, bare_writer_markdown=account.bare_writer_markdown,
            timeout_s=settings.fixture_git_timeout_s, settings_text=settings_text,
        )
    except subprocess.TimeoutExpired:
        return _failed(REASON_FIXTURE_ERROR, git_timeout_detail(settings.fixture_git_timeout_s), progress)
    except (
        UnsafeFixtureConfigError, tasks.TaskSourceError, subprocess.CalledProcessError, OSError, ValueError, RuntimeError,
    ) as exc:
        return _failed(REASON_FIXTURE_ERROR, exception_detail(exc), progress)
    fixture_build_s = time.monotonic() - build_started

    session_id = str(uuid.uuid4())
    progress.session_id = session_id
    settings.run_store.record_directory(task.id, fixture_dir, session_id)
    log_path = attempt_dir / launch.INSTRUCTIONS_LOG_NAME
    inner_prompt = tasks.build_inner_prompt(task)
    command = [
        *account.launch_prefix(settings.runner_environ),
        *review_runner.build_dispatch_command(
            review_runner.build_dispatcher_prompt(arm.agent_name, inner_prompt), model_id=settings.model_id,
            session_id=session_id, budget_cap_usd=settings.budget_cap_usd,
        ),
        *launch.PERMISSION_MODE_ARGV, "--settings", launch.settings_json(log_path), *launch.allowed_tools_tail(allow_paths),
    ]

    _refuse_after_a_sibling_failed(settings)
    started = time.monotonic()
    progress.launched = True
    evidence = {"fixture_dir": fixture_dir, "toolkit": toolkit, "fixture_build_s": fixture_build_s}
    try:
        lines, timed_out = settings.launch(command, fixture_dir, settings.timeout_s)
    except OSError as exc:
        cost, is_cap = _charge(settings.spend, block, arm, run_index, attempt_number, progress, None)
        return _failed(
            REASON_LAUNCH_ERROR, exception_detail(exc), progress, launched=True, cost_usd=cost, is_cap_charge=is_cap,
            wall_clock_s=time.monotonic() - started, **evidence,
        )
    wall_clock_s = time.monotonic() - started
    usage = review_runner.extract_result_usage(lines)
    cost, is_cap = _charge(settings.spend, block, arm, run_index, attempt_number, progress, usage.total_cost_usd)
    launched_fields = {
        "launched": True, "cost_usd": cost, "is_cap_charge": is_cap, "usage": usage, "wall_clock_s": wall_clock_s,
        **evidence,
    }
    if not lines and not timed_out:
        return _failed(REASON_LAUNCH_NO_OUTPUT, "the launch produced no output", progress, **launched_fields)

    try:
        return _evaluate_launched(
            settings, block, arm, progress, lines=lines, timed_out=timed_out, base_commit=base_commit,
            log_path=log_path, inner_prompt=inner_prompt, attempt_dir=attempt_dir, launched_fields=launched_fields,
        )
    except HarnessInvalidatedError:
        raise
    except Exception as exc:
        detail = (
            git_timeout_detail(settings.fixture_git_timeout_s) if isinstance(exc, subprocess.TimeoutExpired)
            else exception_detail(exc)
        )
        return _failed(REASON_ANALYSIS_ERROR, detail, progress, **launched_fields)


def _failure_detail(path_check: checks.PathCheck | None, reason: str | None) -> str | None:
    if path_check is None:
        return None
    if reason == checks.REASON_OUT_OF_FIXTURE_PATH:
        shown = [repr(token) for token in path_check.out_of_fixture_tokens[:MAX_DETAIL_ITEMS]]
        return "tokens that reach outside the fixture: " + ", ".join(shown)
    if reason == checks.REASON_OUT_OF_FIXTURE_WRITE:
        shown = [repr(target) for target in path_check.out_of_fixture_writes[:MAX_DETAIL_ITEMS]]
        return "writes outside the fixture: " + ", ".join(shown)
    return None


def _evaluate_launched(
    settings: RunSettings, block: BlockContext, arm: Arm, progress: _Progress, *, lines: list[bytes], timed_out: bool,
    base_commit: str, log_path: Path, inner_prompt: str, attempt_dir: Path, launched_fields: dict,
) -> AttemptResult:
    account, task = block.account, block.task
    fixture_dir: Path = launched_fields["fixture_dir"]
    toolkit: checks.ToolkitPaths = launched_fields["toolkit"]
    session_id = progress.session_id
    projects_root = account.config_dir / "projects"

    session_jsonl = review_runner.wait_for_session_flush(projects_root, session_id, timed_out=timed_out)
    if session_jsonl is None:
        reason = review_runner.MISSING_REASON_TIMEOUT if timed_out else review_runner.VALIDITY_FAIL_SESSION_STORE_NOT_FOUND
        validity = review_runner._fail(reason)
    else:
        own_session_paths = review_runner.own_session_paths_for(session_jsonl)
        validity = review_runner.evaluate_run_validity(
            dispatcher_session_jsonl=session_jsonl, stream_lines=lines, timed_out=timed_out,
            expected_agent_name=arm.agent_name, expected_inner_prompt=inner_prompt, expected_model_id=settings.model_id,
            agent_declared_tools=account.code_writer_tools, fixture_dir=fixture_dir,
            own_dirs=(fixture_dir, *own_session_paths), projects_root=projects_root,
            own_session_paths=own_session_paths, live_checkout_roots=block.live_checkout_roots,
            changed_relpaths=task.changed_paths,
        )

    transcript = path_check = None
    if session_jsonl is not None:
        sidecars = sorted(msmr.subagent_dir_for_session(session_jsonl).glob("*.jsonl"))
        if sidecars:
            transcript = checks.read_subagent_transcript(sidecars[0])
            path_check = checks.check_tool_call_paths(
                transcript, fixture_dir=fixture_dir, config_dir=account.config_dir, projects_root=projects_root,
                own_session_paths=review_runner.own_session_paths_for(session_jsonl), local_dir=settings.local_dir,
                live_checkout_roots=block.live_checkout_roots, changed_relpaths=task.changed_paths,
            )

    # The log path comes from the attempt dir, never from the fixture the agent could write into.
    wait_for_hook_log_to_settle(log_path, timed_out=timed_out)
    log = checks.parse_instruction_log(log_path, session_id=session_id)
    expectation = arm.load_expectation(account.config_dir / "CLAUDE.md")
    load_check = checks.check_instruction_loads(log, expectation, toolkit=toolkit, fixture_dir=fixture_dir)
    instrument_reasons = list(load_check.reasons)
    if transcript is not None:
        instrument_reasons.extend(checks.toolkit_reach_reasons(transcript, expectation, toolkit=toolkit, fixture_dir=fixture_dir))
    hook_reason = checks.check_account_hooks(account.config_dir, session_id)
    if hook_reason is not None:
        instrument_reasons.append(hook_reason)
    run_completed = validity.failure_reason not in _RUN_DID_NOT_COMPLETE_REASONS
    instrument_reasons = [
        reason for reason in dict.fromkeys(instrument_reasons)
        if run_completed or reason not in _ABSENCE_INSTRUMENT_REASONS
    ]

    reason: str | None = None
    detail: str | None = None
    if instrument_reasons:
        reason = instrument_reasons[0]
        detail = log.detail if reason == log.reason else None
    elif not validity.ok:
        reason, detail = validity.failure_reason, validity.failure_detail
    elif path_check is not None and path_check.failure_reasons:
        reason = path_check.failure_reasons[0]
        detail = _failure_detail(path_check, reason)

    grants = (
        checks.record_read_grants(transcript, toolkit=toolkit, fixture_dir=fixture_dir) if transcript is not None
        else checks.ReadGrantRecords((), ())
    )
    capture = None
    if reason is None:
        capture = diffing.capture_change(
            attempt_dir / BASELINE_GIT_NAME, fixture_dir, base_commit, timeout_s=settings.fixture_git_timeout_s,
        )
    return AttemptResult(
        reason=reason, detail=detail, session_id=session_id, completed=session_jsonl is not None and not timed_out,
        observed_model=validity.observed_model, capture=capture,
        first_turn_prompt_size=transcript.first_turn_prompt_size if transcript is not None else None,
        subagent_loads=load_check.subagent_loads_by_class,
        permission_denials=transcript.permission_denials if transcript is not None else 0,
        allow_list_reads=grants.allow_list_reads, denied_other_toolkit_reads=grants.denied_other_toolkit_reads,
        path_check=path_check, out_of_session_paths=validity.out_of_session_paths, log=log, transcript=transcript,
        **launched_fields,
    )

