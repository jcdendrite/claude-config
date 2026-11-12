"""`run`: the block-by-block campaign with its pinned manifest, smoke gate, spend guards and halts.

Exit codes follow the review bench CLI: a refusal before the first launch raises `HarnessInvalidatedError`
(exit 2), a halt returns 1, and a campaign that completes every task returns 0.
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import run_skill_evals
from review_bench import runner as review_runner
from review_bench.defects import atomic_write_text
from review_bench.runner import EnvironmentReference, HarnessInvalidatedError, RunStore

from replay_bench import records, smoke_pass
from replay_bench import tasks as tasks_mod
from replay_bench.account import Account
from replay_bench.attempt import Launch, RunSettings
from replay_bench.checks import INSTRUMENT_REASONS
from replay_bench.runner import BlockOutcome, checkout_root_for, read_environment, run_block
from replay_bench.spend import SpendAppendError, SpendFile

CAMPAIGNS_DIR_NAME = "campaigns"
MANIFEST_NAME = "manifest.json"
SPEND_NAME = "spend.jsonl"
STORE_DIR_NAME = "store"
ATTEMPTS_DIR_NAME = "attempts"
# Two task-specific failed blocks in a row halt the campaign.
FAILED_BLOCKS_IN_A_ROW_HALT = 2
_MANIFEST_COMPARED_FIELDS = ("task_set_hash", "arms", "k", "seed", "model_id")


@dataclass(frozen=True)
class CampaignConfig:
    campaign_id: str
    tasks_name: str
    tasks: tuple[tasks_mod.ReplayTask, ...]
    arm_names: tuple[str, ...]
    k: int
    seed: int
    model_id: str
    budget_cap_usd: float
    timeout_s: int
    cumulative_stop_usd: float
    account_stop_usd: float
    allow_early_stop: bool
    fixture_git_timeout_s: float
    local_dir: Path
    workers: int = run_skill_evals.DEFAULT_WORKERS

    @property
    def campaign_dir(self) -> Path:
        return self.local_dir / CAMPAIGNS_DIR_NAME / self.campaign_id


def task_set_hash(task_list: tuple[tasks_mod.ReplayTask, ...]) -> str:
    return hashlib.sha256(json.dumps([task.to_dict() for task in task_list], sort_keys=True).encode()).hexdigest()


def block_ceiling_usd(config: CampaignConfig) -> float:
    return review_runner.campaign_cost_ceiling(
        1, len(config.arm_names), config.k, budget_cap_usd=config.budget_cap_usd,
    ).all_retried_usd


def _manifest_for(
    config: CampaignConfig, accounts_by_repo: Mapping[str, Account], readings: Mapping[str, review_runner.EnvironmentRecord],
) -> dict:
    return {
        "task_set_hash": task_set_hash(config.tasks), "tasks_name": config.tasks_name, "arms": list(config.arm_names),
        "k": config.k, "seed": config.seed, "model_id": config.model_id,
        "repo_accounts": {repo: account.key for repo, account in accounts_by_repo.items()},
        "environments": {
            key: {"cli_version": reading.cli_version, "ambient_config_commit": reading.ambient_config_commit}
            for key, reading in readings.items()
        },
    }


def _check_manifest(manifest: dict, expected: dict, *, path: Path) -> None:
    """Raises HarnessInvalidatedError for a resume whose inputs, repo-to-account pin or environment differ."""
    try:
        for field_name in _MANIFEST_COMPARED_FIELDS:
            if manifest[field_name] != expected[field_name]:
                raise HarnessInvalidatedError(
                    f"{path}: this campaign was started with a different {field_name}; "
                    "resume with the same inputs, or start a new --campaign-id"
                )
        for repo, key in expected["repo_accounts"].items():
            pinned_key = manifest["repo_accounts"].get(repo)
            if pinned_key != key:
                raise HarnessInvalidatedError(
                    f"source repo {repo!r} now resolves to account {key!r}, not the manifest's {pinned_key!r}"
                )
        for key, reading in expected["environments"].items():
            for field_name, value in reading.items():
                pinned_value = manifest["environments"][key][field_name]
                if pinned_value != value:
                    raise HarnessInvalidatedError(
                        f"account {key!r}: {field_name} is {value!r}, not the manifest's {pinned_value!r}"
                    )
    except (KeyError, TypeError, AttributeError) as exc:
        raise HarnessInvalidatedError(f"{path}: the manifest is malformed: {exc!r}") from exc


def _refuse_without_smoke_pass(config: CampaignConfig, readings: Mapping[str, review_runner.EnvironmentRecord]) -> None:
    for key, reading in readings.items():
        if not smoke_pass.has_smoke_pass(config.local_dir, key, reading, config.model_id, config.arm_names):
            raise HarnessInvalidatedError(
                f"account {key!r} has no passing smoke under the current environment, model and arms; "
                "run `smoke` on a task from that account first"
            )


def stop_would_be_passed(spent_usd: float, ceiling_usd: float, stop_usd: float) -> bool:
    """True when the next block could carry spend past the stop. A non-finite figure fails closed."""
    projected = spent_usd + ceiling_usd
    return not math.isfinite(projected) or projected > stop_usd


def _halt(message: str) -> int:
    print(f"halted: {message}", file=sys.stderr)
    return 1


def _arm_failures(block: BlockOutcome, arm_names: tuple[str, ...]) -> dict[str, list[records.ReplayRunRecord]]:
    """Each arm with no valid run in the block, with that arm's records."""
    failing = {}
    for arm in arm_names:
        arm_records = [outcome.record for outcome in block.outcomes_for(arm)]
        if review_runner.all_runs_missing(arm_records):
            failing[arm] = arm_records
    return failing


def _missing_summary(arm_records: list[records.ReplayRunRecord], *, with_detail: bool) -> str:
    parts = []
    for record in arm_records:
        if not record.is_ok:
            text = record.missing_reason or "unknown"
            if with_detail and record.missing_detail:
                text += f" ({record.missing_detail})"
            parts.append(text)
    return "; ".join(parts)


def _progress_line(
    block: BlockOutcome, config: CampaignConfig, account: Account, failures: Mapping[str, list], dropped: Mapping[str, int],
    spend: SpendFile,
) -> str:
    counts = []
    for arm in config.arm_names:
        outcome_counts = review_runner.count_outcomes([outcome.record for outcome in block.outcomes_for(arm)])
        counts.append(f"{arm} ok {outcome_counts.ok} missing {outcome_counts.missing}")
    no_valid = "; ".join(
        f"{arm} ({_missing_summary(arm_records, with_detail=False)})" for arm, arm_records in failures.items()
    ) or "none"
    totals = spend.totals()
    return (
        f"{block.task.id}: {', '.join(counts)} | arms with no valid run: {no_valid} | "
        f"tasks dropped so far: {', '.join(f'{arm} {dropped[arm]}' for arm in config.arm_names)} | "
        f"block spend ${totals.by_task_usd.get(block.task.id, 0.0):.2f} | "
        f"cumulative ${totals.total_usd:.2f} of ${config.cumulative_stop_usd:.2f} | "
        f"account {account.key!r} ${totals.account_usd(account.key):.2f} of ${config.account_stop_usd:.2f}"
    )


def _drift_halt(block: BlockOutcome | None, task: tasks_mod.ReplayTask, message: str, spend: SpendFile) -> int:
    attempts = sum(record.attempts for record in block.records) if block is not None else 0
    block_spend = spend.totals().by_task_usd.get(task.id, 0.0)
    return _halt(f"{message} -- {task.id}: {attempts} attempts, ${block_spend:.2f} spent in the block")


def _two_in_a_row_message(lost_tasks: list[str], failing_blocks: list[Mapping[str, list]]) -> str:
    lines = [
        f"{FAILED_BLOCKS_IN_A_ROW_HALT} task-specific failed blocks in a row; tasks {lost_tasks} are lost from "
        "the sample, and a resume goes on past them"
    ]
    for task_id, failures in zip(lost_tasks, failing_blocks, strict=True):
        for arm, arm_records in failures.items():
            lines.append(f"  {task_id} {arm}: {_missing_summary(arm_records, with_detail=True)}")
    return "\n".join(lines)


def run_campaign(
    config: CampaignConfig, accounts_by_repo: Mapping[str, Account], *, runner_environ: Mapping[str, str], launch: Launch,
) -> int:
    campaign_dir = config.campaign_dir
    accounts = {account.key: account for account in accounts_by_repo.values()}
    readings = {
        key: read_environment(checkout_root_for(account))
        for key, account in accounts.items()
    }
    expected_manifest = _manifest_for(config, accounts_by_repo, readings)
    manifest_path = campaign_dir / MANIFEST_NAME
    is_resume = manifest_path.exists()
    if is_resume:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HarnessInvalidatedError(f"{manifest_path}: cannot read the manifest: {exc}") from exc
        _check_manifest(manifest, expected_manifest, path=manifest_path)
    _refuse_without_smoke_pass(config, readings)
    if not is_resume:
        atomic_write_text(manifest_path, json.dumps(expected_manifest, indent=2) + "\n")

    run_store = RunStore(campaign_dir / STORE_DIR_NAME)
    run_store.acquire_lock()
    try:
        return _run_locked(config, accounts, accounts_by_repo, readings, run_store, runner_environ, launch, is_resume=is_resume)
    finally:
        run_store.release_lock()


def _run_locked(
    config: CampaignConfig, accounts: Mapping[str, Account], accounts_by_repo: Mapping[str, Account],
    readings: Mapping[str, review_runner.EnvironmentRecord], run_store: RunStore, runner_environ: Mapping[str, str],
    launch: Launch, *, is_resume: bool,
) -> int:
    campaign_dir = config.campaign_dir
    attempts_root = campaign_dir / ATTEMPTS_DIR_NAME
    if attempts_root.is_symlink():
        raise HarnessInvalidatedError(f"{str(attempts_root)!r} is a symlink; remove it and resume")
    spend = SpendFile(campaign_dir / SPEND_NAME, budget_cap_usd=config.budget_cap_usd)
    spend.repair_torn_tail()

    swept = {}
    for account in accounts.values():
        for entry in run_store.sweep_abandoned(account.config_dir / "projects"):
            swept[(entry.defect_id, entry.directory, entry.session_id)] = entry
    recorded_sessions = spend.totals().session_ids
    uncharged = sum(1 for entry in swept.values() if entry.session_id and entry.session_id not in recorded_sessions)
    if uncharged:
        print(
            f"{uncharged} logged attempts with a session ID have no spend line and were not charged "
            "(an attempt killed before its launch counts too)"
        )

    completed = run_store.completed_block_ids()
    incomplete = [task for task in config.tasks if task.id not in completed]
    totals = spend.totals()
    ceiling = block_ceiling_usd(config)
    campaign_ceiling = totals.total_usd + ceiling * len(incomplete)
    qualifier = " (leaves out the uncharged attempts above)" if uncharged else ""
    if is_resume:
        incomplete_spend = sum(totals.by_task_usd.get(task.id, 0.0) for task in incomplete)
        print(f"spend on blocks not yet complete: ${incomplete_spend:.2f}{qualifier}")
    print(
        f"{len(incomplete) * len(config.arm_names) * config.k} runs in {len(incomplete)} blocks; "
        f"nominal ceiling ${campaign_ceiling:.2f}{qualifier}"
    )
    if ceiling > config.cumulative_stop_usd or ceiling > config.account_stop_usd:
        raise HarnessInvalidatedError(
            f"one block's nominal ceiling ${ceiling:.2f} exceeds --cumulative-stop-usd "
            f"${config.cumulative_stop_usd:.2f} or --account-stop-usd ${config.account_stop_usd:.2f}"
        )
    if (not math.isfinite(campaign_ceiling) or campaign_ceiling > config.cumulative_stop_usd) and not config.allow_early_stop:
        raise HarnessInvalidatedError(
            f"the campaign's nominal ceiling ${campaign_ceiling:.2f} exceeds --cumulative-stop-usd "
            f"${config.cumulative_stop_usd:.2f}; raise the stop or pass --allow-early-stop"
        )
    if attempts_root.is_dir():
        review_runner.remove_tree_reporting_failure(attempts_root)

    references = {
        key: EnvironmentReference({"cli_version": reading.cli_version, "ambient_config_commit": reading.ambient_config_commit})
        for key, reading in readings.items()
    }
    settings = RunSettings(
        campaign_id=config.campaign_id, attempts_root=attempts_root, local_dir=config.local_dir, model_id=config.model_id,
        budget_cap_usd=config.budget_cap_usd, timeout_s=config.timeout_s, fixture_git_timeout_s=config.fixture_git_timeout_s,
        run_store=run_store, spend=spend, runner_environ=runner_environ, launch=launch,
    )
    dropped = dict.fromkeys(config.arm_names, 0)
    failed_blocks: list[tuple[str, Mapping[str, list]]] = []
    for task in incomplete:
        account = accounts_by_repo[task.source_repo]
        totals = spend.totals()
        if stop_would_be_passed(totals.total_usd, ceiling, config.cumulative_stop_usd):
            return _halt(
                f"the cumulative stop: ${totals.total_usd:.2f} spent, and the next block's ceiling ${ceiling:.2f} "
                f"would pass ${config.cumulative_stop_usd:.2f}"
            )
        if stop_would_be_passed(totals.account_usd(account.key), ceiling, config.account_stop_usd):
            return _halt(
                f"the account stop for {account.key!r}: ${totals.account_usd(account.key):.2f} spent, and the next "
                f"block's ceiling ${ceiling:.2f} would pass ${config.account_stop_usd:.2f}"
            )
        try:
            block = run_block(
                settings, task, account, config.arm_names, config.k, config.seed, references[account.key],
                workers=config.workers,
            )
        except review_runner.EnvironmentMismatchError as exc:
            return _drift_halt(None, task, str(exc), spend)
        except SpendAppendError as exc:
            launches = "; ".join(f"task {task_id} session {session_id}" for task_id, session_id in settings.uncharged_launches)
            return _halt(
                f"{exc}; {task.id} is not marked complete and a resume reruns it. "
                f"Launches begun and not charged: {launches or 'none'}"
            )
        if block.end_drift is not None:
            return _drift_halt(block, task, block.end_drift, spend)
        for _arm, _index, outcome in block.runs:
            records.write_run(campaign_dir, outcome.record, outcome.diff_text)
        run_store.mark_block_complete(task.id)

        failures = _arm_failures(block, config.arm_names)
        for arm in failures:
            dropped[arm] += 1
        print(_progress_line(block, config, account, failures, dropped, spend))

        instrument_failures = [
            outcome.record for _arm, _index, outcome in block.runs if outcome.record.missing_reason in INSTRUMENT_REASONS
        ]
        if instrument_failures:
            first = instrument_failures[0]
            return _halt(f"instrument failure in {task.id}, arm {first.arm}: {first.missing_reason}; fix the cause and resume")
        if failures:
            failed_blocks.append((task.id, failures))
            if len(failed_blocks) >= FAILED_BLOCKS_IN_A_ROW_HALT:
                lost = failed_blocks[-FAILED_BLOCKS_IN_A_ROW_HALT:]
                return _halt(_two_in_a_row_message([task_id for task_id, _ in lost], [blocks for _, blocks in lost]))
        else:
            failed_blocks.clear()
    return 0
