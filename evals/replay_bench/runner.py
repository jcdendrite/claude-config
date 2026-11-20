"""Retry-then-missing for one replay run, and one task's block of runs."""
from __future__ import annotations

import re
import sys
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import measure_subagent_model_resolution as msmr
import run_skill_evals
from review_bench import runner as review_runner
from review_bench.runner import EnvironmentMismatchError, EnvironmentReference, HarnessInvalidatedError

from replay_bench import checks, tasks
from replay_bench.account import Account
from replay_bench.arms import ARMS
from replay_bench.attempt import NO_RETRY_REASONS, AttemptResult, BlockContext, RunSettings, run_attempt
from replay_bench.records import ReplayRunRecord
from replay_bench.spend import SpendAppendError

# Basis: the Fixture section of .claude/plans/docstring-verbosity-experiment.md.
FIXTURE_GIT_TIMEOUT_S = 110


@dataclass(frozen=True)
class RunOutcome:
    """`final` is the run's last attempt, whose evidence (log, transcript, sizes) smoke's calibration reads."""

    record: ReplayRunRecord
    diff_text: str | None
    final: AttemptResult


def _sum_attempts(results: list[AttemptResult], field_name: str) -> int | None:
    return review_runner._sum_optional([getattr(result.usage, field_name) for result in results])


def _build_record(
    settings: RunSettings, block: BlockContext, arm_name: str, run_index: int, results: list[AttemptResult],
) -> ReplayRunRecord:
    final = results[-1]
    path_check = final.path_check
    return ReplayRunRecord(
        campaign_id=settings.campaign_id, task_id=block.task.id, arm=arm_name, run_index=run_index,
        status=review_runner.STATUS_OK if final.ok else review_runner.STATUS_MISSING,
        missing_reason=final.reason, missing_detail=final.detail, attempts=len(results),
        account_key=block.account.key, session_id=final.session_id, cli_version=block.environment.cli_version,
        ambient_config_commit=block.environment.ambient_config_commit,
        wall_clock_s=sum(result.wall_clock_s for result in results),
        total_cost_usd=sum(result.cost_usd for result in results),
        cap_charged_attempts=sum(result.is_cap_charge for result in results),
        observed_model=final.observed_model, fixture_build_s=final.fixture_build_s,
        input_tokens=_sum_attempts(results, "input_tokens"), output_tokens=_sum_attempts(results, "output_tokens"),
        cache_read_input_tokens=_sum_attempts(results, "cache_read_input_tokens"),
        cache_creation_input_tokens=_sum_attempts(results, "cache_creation_input_tokens"),
        metrics=final.capture.metrics.to_dict() if final.capture is not None else None,
        first_turn_prompt_size=final.first_turn_prompt_size,
        subagent_loads={load_class: dict(paths) for load_class, paths in final.subagent_loads.items()},
        permission_denials=final.permission_denials,
        allow_list_reads=[{"path": read.path, "denied": read.denied} for read in final.allow_list_reads],
        denied_other_toolkit_reads=list(final.denied_other_toolkit_reads),
        bash_commands=list(path_check.bash_commands) if path_check is not None else [],
        out_of_fixture_writes=list(path_check.out_of_fixture_writes) if path_check is not None else [],
        out_of_fixture_tokens=list(path_check.out_of_fixture_tokens) if path_check is not None else [],
        other_outside_fixture_tokens=list(path_check.other_outside_fixture_tokens) if path_check is not None else [],
        unparsed=list(path_check.unparsed) if path_check is not None else [],
        out_of_session_paths=list(final.out_of_session_paths),
        ignored_paths_count=final.capture.ignored_path_count if final.capture is not None else 0,
        ignored_paths=list(final.capture.ignored_paths) if final.capture is not None else [],
        denied_tool_calls=checks.denied_tool_calls(final.transcript) if final.transcript is not None else [],
    )


def execute_run(settings: RunSettings, block: BlockContext, arm_name: str, run_index: int) -> RunOutcome:
    arm = ARMS[arm_name]
    results: list[AttemptResult] = []
    for attempt_number in range(1, review_runner.ATTEMPTS_PER_RUN + 1):
        result = run_attempt(settings, block, arm, run_index, attempt_number)
        results.append(result)
        if result.ok or result.reason in NO_RETRY_REASONS:
            break
    final = results[-1]
    return RunOutcome(
        _build_record(settings, block, arm_name, run_index, results),
        final.capture.diff_text if final.capture is not None else None, final,
    )


@dataclass(frozen=True)
class BlockOutcome:
    task: tasks.ReplayTask
    runs: tuple[tuple[str, int, RunOutcome], ...]  # in the block plan's order
    # Set when the block's end reading differs from the reference: the block's records must then be discarded.
    end_drift: str | None = None
    # Set when the end reading could not be read after a retry. The records are discarded as for drift, but the
    # reading is no evidence of drift.
    end_unread: str | None = None

    @property
    def records(self) -> list[ReplayRunRecord]:
        return [outcome.record for _arm, _index, outcome in self.runs]

    def outcomes_for(self, arm_name: str) -> list[RunOutcome]:
        return [outcome for arm, _index, outcome in self.runs if arm == arm_name]


def checkout_root_for(account: Account) -> Path:
    """A directory inside the config checkout."""
    return (account.config_dir / "CLAUDE.md").resolve().parent


_EXIT_STATUS_RE = re.compile(r"exited (\d+)")


def _without_child_output[ResultT](call: Callable[[], ResultT], description: str) -> ResultT:
    """A failure carries the exit status only, since the bench's own message holds the child's stderr, which can
    name an environment-derived value."""
    try:
        return call()
    except HarnessInvalidatedError as exc:
        status = _EXIT_STATUS_RE.search(str(exc))
        cause = f"exit {status[1]}" if status else "no exit status reported"
        raise HarnessInvalidatedError(f"{description} ({cause})") from None


def live_checkout_roots_for(task: tasks.ReplayTask) -> tuple[Path, ...]:
    """Called through `review_runner`'s module attributes, so a test's stub reaches them."""
    def roots() -> tuple[Path, ...]:
        return tuple(dict.fromkeys((
            *review_runner.default_live_checkout_roots(), *review_runner._worktree_top_levels(Path(task.source_repo)),
        )))

    return _without_child_output(roots, "live checkout roots: could not list the worktrees")


def read_environment(checkout_root: Path) -> review_runner.EnvironmentRecord:
    return _without_child_output(
        lambda: review_runner.read_environment_record(checkout_root=checkout_root),
        "environment record: could not read the CLI version or config commit",
    )


def _read_end_environment(checkout_root: Path) -> review_runner.EnvironmentRecord:
    for _attempt in range(review_runner.ATTEMPTS_PER_RUN - 1):
        try:
            return read_environment(checkout_root)
        except HarnessInvalidatedError:
            continue
    return read_environment(checkout_root)


def _stop_further_launches(settings: RunSettings) -> None:
    settings.stop.set()
    msmr.abort_launches()


def _reported_root_cause(settings: RunSettings, raised: BaseException) -> BaseException:
    """An interrupt is returned as it is, which keeps its exit status. Among failures, a spend failure outranks every
    other: only its halt names the launches begun and not charged. The failures it outranks are named in its message.
    Otherwise a sibling's abort yields to the failure that caused it."""
    if not isinstance(raised, Exception):
        return raised
    spend_failure = next((failure for failure in settings.failures if isinstance(failure, SpendAppendError)), None)
    if spend_failure is not None:
        outranked = [failure for failure in settings.failures if failure is not spend_failure]
        if not outranked:
            return spend_failure
        detail = "; ".join(f"{type(failure).__name__}: {failure}" for failure in outranked)
        halt = SpendAppendError(f"{spend_failure}. {len(outranked)} other failure(s) in the block: {detail}")
        halt.__cause__ = spend_failure
        return halt
    if settings.failures and isinstance(raised, msmr.LaunchAbortedError):
        return settings.failures[0]
    return raised


def run_block(
    settings: RunSettings, task: tasks.ReplayTask, account: Account, arm_names: tuple[str, ...], k: int, seed: int,
    environment_reference: EnvironmentReference, *, workers: int = run_skill_evals.DEFAULT_WORKERS,
) -> BlockOutcome:
    """Raises EnvironmentMismatchError when the start reading differs from the reference."""
    checkout_root = checkout_root_for(account)
    environment = read_environment(checkout_root)
    environment_reference.require_match(environment, where=f"{task.id}'s block start")
    block = BlockContext(task, account, environment, live_checkout_roots_for(task))
    plan = review_runner.build_block_plan(task.id, arm_names, k, seed)

    def run_one(arm_and_index: tuple[str, int]) -> RunOutcome:
        try:
            return execute_run(settings, block, *arm_and_index)
        except BaseException as exc:
            # The in-order consumer sees this failure only after earlier runs finish, so stop later launches now.
            if not isinstance(exc, msmr.LaunchAbortedError):
                settings.failures.append(exc)
            _stop_further_launches(settings)
            raise

    raised: BaseException | None = None
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        try:
            outcomes = list(pool.map(run_one, plan.ordered_runs))
        except BaseException as exc:
            # Worker threads never see the interrupt, and the pool's exit joins them, so in-flight children stop first.
            _stop_further_launches(settings)
            raised = exc
    if raised is not None:
        # Chosen after the join, so a sibling's failure that lands while it ends is still seen.
        root_cause = _reported_root_cause(settings, raised)
        if not isinstance(root_cause, Exception) and settings.uncharged_launches:
            # An interrupt keeps its exit status and resume hint, and neither names the uncharged launches.
            print(settings.uncharged_launches_report(), file=sys.stderr)
        raise root_cause
    end_drift = end_unread = None
    try:
        environment_reference.require_match(
            _read_end_environment(checkout_root), where=f"{task.id}'s block end",
        )
    except EnvironmentMismatchError as exc:
        end_drift = str(exc)
    except HarnessInvalidatedError as exc:
        end_unread = str(exc)
    runs = tuple((arm, index, outcome) for (arm, index), outcome in zip(plan.ordered_runs, outcomes, strict=True))
    return BlockOutcome(task, runs, end_drift, end_unread)


def new_campaign_suffix() -> str:
    return uuid.uuid4().hex[:8]
