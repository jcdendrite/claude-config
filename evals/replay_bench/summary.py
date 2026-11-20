"""`summarize`: the terminal-only summary of one campaign's complete blocks.

What a null difference does and does not show is stated in `evals/README.md` § "Replay bench".
"""
from __future__ import annotations

import json
import math
import statistics
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from review_bench import analysis
from review_bench import runner as review_runner
from review_bench.runner import HarnessInvalidatedError, RunStore

from replay_bench import records
from replay_bench import tasks as tasks_mod
from replay_bench.arms import BARE, TOOLKIT
from replay_bench.campaign import CAMPAIGNS_DIR_NAME, MANIFEST_NAME, SPEND_NAME, STORE_DIR_NAME, task_set_hash
from replay_bench.provenance import RunnerProvenance, describe_campaign
from replay_bench.records import ReplayRunRecord
from replay_bench.spend import SpendFile, is_usable_cost

TASKS_DIR_NAME = "tasks"
EXCLUSION_KEYS = ("toolkit", "bare", "both", "code")
PAIRED_ARM_NAMES = (TOOLKIT.name, BARE.name)


@dataclass(frozen=True)
class _ArmCounts:
    numerator: float
    code_lines: float

    @property
    def ratio(self) -> float | None:
        return self.numerator / self.code_lines if self.code_lines else None


def _valid(arm_records: Sequence[ReplayRunRecord]) -> list[ReplayRunRecord]:
    return [record for record in arm_records if record.is_ok and record.metrics is not None]


def _run_words(record: ReplayRunRecord) -> int:
    return record.metrics["comment_words"]["non_test"]


def _run_code_lines(record: ReplayRunRecord) -> int:
    return record.metrics["line_counts"]["non_test"]["code"]


def _run_docstring_lines(record: ReplayRunRecord) -> int:
    return record.metrics["line_counts"]["non_test"]["docstring"]


def _mean_counts_per_run(
    arm_records: Sequence[ReplayRunRecord], run_numerator: Callable[[ReplayRunRecord], int] = _run_words,
) -> _ArmCounts:
    """A task's arm counts are per-run means, so a task with more valid runs weighs no more in a pooled ratio."""
    valid = _valid(arm_records)
    return _ArmCounts(
        numerator=statistics.fmean(run_numerator(record) for record in valid),
        code_lines=statistics.fmean(_run_code_lines(record) for record in valid),
    )


def _ratio_of_sums(task_counts: Iterable[_ArmCounts]) -> float | None:
    task_counts = list(task_counts)
    return _ArmCounts(
        numerator=sum(counts.numerator for counts in task_counts),
        code_lines=sum(counts.code_lines for counts in task_counts),
    ).ratio


def _pooled_ratio(task_ids: Iterable[str], counts_by_task: Mapping[str, _ArmCounts]) -> float | None:
    return _ratio_of_sums(counts_by_task[task_id] for task_id in task_ids)


@dataclass(frozen=True)
class RunSpread:
    standard_deviation: float | None
    task_count: int


def run_spread(arm_records_by_task: Iterable[Sequence[ReplayRunRecord]]) -> RunSpread:
    """Pooled within-task standard deviation of one run's words per code line, over tasks with 2+ valid runs that added code."""
    squared_deviations, degrees_of_freedom, task_count = 0.0, 0, 0
    for arm_records in arm_records_by_task:
        run_ratios = [_run_words(record) / _run_code_lines(record) for record in _valid(arm_records) if _run_code_lines(record)]
        if len(run_ratios) < 2:
            continue
        squared_deviations += statistics.variance(run_ratios) * (len(run_ratios) - 1)
        degrees_of_freedom += len(run_ratios) - 1
        task_count += 1
    if not degrees_of_freedom:
        return RunSpread(None, task_count)
    return RunSpread(math.sqrt(squared_deviations / degrees_of_freedom), task_count)


def _reasons(arm_records: Sequence[ReplayRunRecord]) -> Counter:
    return Counter(record.missing_reason or "unknown" for record in arm_records if not record.is_ok)


def _format_reasons(reasons: Counter) -> str:
    return ", ".join(f"{reason} x{count}" for reason, count in sorted(reasons.items())) or "none"


def _refuse_mixed_environments(all_records: Sequence[ReplayRunRecord]) -> None:
    readings: dict[str, set[tuple[str, str]]] = {}
    for record in all_records:
        readings.setdefault(record.account_key, set()).add((record.cli_version, record.ambient_config_commit))
    for account_key, account_readings in readings.items():
        if len(account_readings) > 1:
            raise HarnessInvalidatedError(
                f"account {account_key!r}: the records span {len(account_readings)} CLI version and config commit "
                "readings; summarize one environment at a time"
            )


def _read_reference_metrics(
    local_dir: Path, tasks_name: str | None, pinned_hash: object,
) -> tuple[dict[str, dict], str | None]:
    if not tasks_name:
        return {}, "the manifest names no task set"
    try:
        task_list = tasks_mod.read_task_set(local_dir / TASKS_DIR_NAME, tasks_name)
    except (tasks_mod.TaskFileError, ValueError) as exc:
        return {}, f"task set {tasks_name!r} could not be read: {exc}"
    if pinned_hash is None:
        return {}, "the manifest records no task set hash"
    if task_set_hash(tuple(task_list)) != pinned_hash:
        return {}, f"task set {tasks_name!r} is not the one this campaign pinned"
    return {task.id: task.reference_metrics.to_dict() for task in task_list}, None


def _read_manifest(manifest_path: Path) -> tuple[dict, str | None]:
    if not manifest_path.exists():
        return {}, "the campaign has no manifest"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {}, f"the manifest could not be read: {type(exc).__name__}"
    if not isinstance(manifest, dict):
        return {}, "the manifest is not a JSON object"
    return manifest, None


def _docstring_rate(metrics_list: Sequence[dict]) -> float | None:
    code = sum(metrics["line_counts"]["non_test"]["code"] for metrics in metrics_list)
    docstring = sum(metrics["line_counts"]["non_test"]["docstring"] for metrics in metrics_list)
    return 100 * docstring / code if code else None


def _arm_docstring_counts(task_records: Sequence[ReplayRunRecord], arm_name: str) -> _ArmCounts:
    arm_records = [record for record in task_records if record.arm == arm_name]
    return _mean_counts_per_run(arm_records, _run_docstring_lines)


def _docstring_rate_of_counts(task_counts: Iterable[_ArmCounts]) -> float | None:
    ratio = _ratio_of_sums(task_counts)
    return None if ratio is None else 100 * ratio


def _number(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.4f}"


@dataclass(frozen=True)
class PairedOutcome:
    differences: dict[str, float]
    pooled_ratios: dict[str, float]
    pooled_difference: float | None
    pooled_interval: tuple[float, float] | None
    unweighted_mean: float | None
    unweighted_interval: tuple[float, float] | None
    run_spreads: dict[str, RunSpread]
    excluded_counts: dict[str, int]
    excluded_records: dict[str, list[ReplayRunRecord]]


def _interval_over_tasks(task_ids: Sequence[str], statistic: Callable[[Sequence[str]], float]) -> tuple[float, float] | None:
    return analysis.bootstrap_interval(
        list(task_ids), statistic, cluster_by_defect={task_id: (task_id, "") for task_id in task_ids},
    )


def paired_outcome(block_records: Mapping[str, Sequence[ReplayRunRecord]]) -> PairedOutcome:
    differences: dict[str, float] = {}
    counts: dict[str, dict[str, _ArmCounts]] = {arm_name: {} for arm_name in PAIRED_ARM_NAMES}
    paired_records: dict[str, list[Sequence[ReplayRunRecord]]] = {arm_name: [] for arm_name in PAIRED_ARM_NAMES}
    excluded: dict[str, list[ReplayRunRecord]] = {key: [] for key in EXCLUSION_KEYS}
    excluded_counts = dict.fromkeys(excluded, 0)
    for task_id, task_records in block_records.items():
        toolkit_records = [record for record in task_records if record.arm == TOOLKIT.name]
        bare_records = [record for record in task_records if record.arm == BARE.name]
        has_toolkit, has_bare = bool(_valid(toolkit_records)), bool(_valid(bare_records))
        if not has_toolkit or not has_bare:
            key = "both" if not has_toolkit and not has_bare else "toolkit" if not has_toolkit else "bare"
            excluded_counts[key] += 1
            missing_side = {"toolkit": toolkit_records, "bare": bare_records, "both": task_records}[key]
            excluded[key].extend(record for record in missing_side if not record.is_ok)
            continue
        toolkit_counts, bare_counts = _mean_counts_per_run(toolkit_records), _mean_counts_per_run(bare_records)
        if toolkit_counts.ratio is None or bare_counts.ratio is None:
            excluded_counts["code"] += 1
            continue
        differences[task_id] = toolkit_counts.ratio - bare_counts.ratio
        counts[TOOLKIT.name][task_id], counts[BARE.name][task_id] = toolkit_counts, bare_counts
        paired_records[TOOLKIT.name].append(toolkit_records)
        paired_records[BARE.name].append(bare_records)
    run_spreads = {arm_name: run_spread(paired_records[arm_name]) for arm_name in PAIRED_ARM_NAMES}
    if not differences:
        return PairedOutcome(differences, {}, None, None, None, None, run_spreads, excluded_counts, excluded)

    def pooled_difference(task_ids: Sequence[str]) -> float:
        return _pooled_ratio(task_ids, counts[TOOLKIT.name]) - _pooled_ratio(task_ids, counts[BARE.name])

    def unweighted_mean(task_ids: Sequence[str]) -> float:
        return statistics.fmean(differences[task_id] for task_id in task_ids)

    paired_ids = list(differences)
    return PairedOutcome(
        differences=differences,
        pooled_ratios={arm_name: _pooled_ratio(paired_ids, counts[arm_name]) for arm_name in PAIRED_ARM_NAMES},
        pooled_difference=pooled_difference(paired_ids),
        pooled_interval=_interval_over_tasks(paired_ids, pooled_difference),
        unweighted_mean=unweighted_mean(paired_ids),
        unweighted_interval=_interval_over_tasks(paired_ids, unweighted_mean),
        run_spreads=run_spreads, excluded_counts=excluded_counts, excluded_records=excluded,
    )


def _reference_pooled_ratio(paired_ids: Iterable[str], reference: Mapping[str, dict]) -> float | None:
    return _ratio_of_sums(
        _ArmCounts(
            numerator=reference[task_id]["comment_words"]["non_test"],
            code_lines=reference[task_id]["line_counts"]["non_test"]["code"],
        )
        for task_id in paired_ids if task_id in reference
    )


def _difference_text(difference: float | None, interval: tuple[float, float] | None) -> str:
    if difference is None:
        return "n/a"
    if interval is None:
        return f"{difference:.4f}, no interval for fewer than two paired tasks"
    return f"{difference:.4f}, 95% interval [{interval[0]:.4f}, {interval[1]:.4f}]"


def summarize(campaign_dir: Path, *, local_dir: Path, budget_cap_usd: float) -> list[str]:
    if not (campaign_dir / records.RUNS_DIR_NAME).is_dir() and not (campaign_dir / MANIFEST_NAME).exists():
        raise HarnessInvalidatedError(f"{str(campaign_dir)!r} holds no campaign")
    store_dir = campaign_dir / STORE_DIR_NAME
    # `RunStore` creates its directory, so a campaign with no store is not opened through it.
    completed = RunStore(store_dir).completed_block_ids() if store_dir.is_dir() else set()
    runs_dir = campaign_dir / records.RUNS_DIR_NAME
    on_disk = sorted(path.name for path in runs_dir.iterdir() if path.is_dir()) if runs_dir.is_dir() else []
    complete_ids = [task_id for task_id in on_disk if task_id in completed]
    incomplete_blocks = len(on_disk) - len(complete_ids)
    block_records = {task_id: records.read_block_records(campaign_dir, task_id) for task_id in complete_ids}
    all_records = [record for task_records in block_records.values() for record in task_records]
    _refuse_mixed_environments(all_records)

    manifest, manifest_problem = _read_manifest(campaign_dir / MANIFEST_NAME)
    runner_clause = describe_campaign(
        RunnerProvenance.from_manifest(manifest),
        (RunnerProvenance(record.runner_commit, record.runner_dirty) for record in all_records),
    )
    lines = [
        f"campaign {campaign_dir.name!r}: {len(complete_ids)} complete blocks, {incomplete_blocks} incomplete blocks; "
        f"{runner_clause}"
    ]
    arm_names = sorted({record.arm for record in all_records})
    for arm_name in arm_names:
        arm_records = [record for record in all_records if record.arm == arm_name]
        counts = review_runner.count_outcomes(arm_records)
        lines.append(f"  {arm_name}: {review_runner.format_outcome_counts(counts)}")

    paired = paired_outcome(block_records)

    manifest, manifest_problem = _read_manifest(campaign_dir / MANIFEST_NAME)
    reference, reference_problem = _read_reference_metrics(local_dir, manifest.get("tasks_name"), manifest.get("task_set_hash"))
    reference_problem = manifest_problem or reference_problem

    lines.append(
        "primary outcome: comment-plus-docstring words per added non-test code line, pooled over the paired tasks "
        "(a task's counts are its valid runs' means)"
    )
    for arm_name in PAIRED_ARM_NAMES:
        lines.append(f"  {arm_name} words per code line: {_number(paired.pooled_ratios.get(arm_name))}")
    reference_ratio = _reference_pooled_ratio(paired.differences, reference)
    lines.append(f"  reference (the merged commits) words per code line: {_number(reference_ratio)}")
    lines.append(f"paired difference toolkit - bare: {_difference_text(paired.pooled_difference, paired.pooled_interval)}")
    lines.append(
        "  sensitivity, unweighted mean of per-task differences: "
        f"{_difference_text(paired.unweighted_mean, paired.unweighted_interval)}"
    )
    lines.append("  run-to-run spread, the within-task standard deviation of one run's words per code line:")
    for arm_name in PAIRED_ARM_NAMES:
        spread = paired.run_spreads[arm_name]
        lines.append(
            f"    {arm_name}: {_number(spread.standard_deviation)} over {spread.task_count} paired tasks "
            "with two or more runs that added code"
        )
    lines.append(f"paired {len(paired.differences)} of {len(complete_ids)} complete tasks")
    labels = {
        "toolkit": "toolkit with no valid run", "bare": "bare with no valid run",
        "both": "both arms with no valid run", "code": "no non-test code line in an arm",
    }
    for key, label in labels.items():
        suffix = f" (missing reasons: {_format_reasons(_reasons(paired.excluded_records[key]))})" if key != "code" else ""
        lines.append(f"  excluded, {label}: {paired.excluded_counts[key]}{suffix}")

    # The primary outcome's own pairs, so an arm's rate is not skewed by a task the other arm has no valid run for.
    paired_ids = list(paired.differences)
    lines.append(
        "secondary outcome: Python docstring lines per 100 added non-test code lines, over the paired tasks "
        "of the primary outcome (a task's counts are its valid runs' means)"
    )
    reference_rate = _docstring_rate([reference[task_id] for task_id in paired_ids if task_id in reference])
    lines.append(f"  reference (the merged commits), paired tasks pooled: {_number(reference_rate)}")
    if reference_problem:
        lines.append(f"  reference unavailable: {reference_problem}")
    for arm in (TOOLKIT, BARE):
        pooled_rate = _docstring_rate_of_counts(
            _arm_docstring_counts(block_records[task_id], arm.name) for task_id in paired_ids
        )
        lines.append(f"  {arm.name}, paired tasks pooled: {_number(pooled_rate)}")
    for task_id in paired_ids:
        toolkit_rate, bare_rate = (
            _docstring_rate_of_counts([_arm_docstring_counts(block_records[task_id], arm.name)]) for arm in (TOOLKIT, BARE)
        )
        lines.append(f"  paired task {task_id}: toolkit {_number(toolkit_rate)}, bare {_number(bare_rate)}")

    lines.append("diagnostics per arm:")
    for arm_name in arm_names:
        arm_records = [record for record in all_records if record.arm == arm_name]
        valid = _valid(arm_records)
        total_lines = sum(sum(scope.values()) for record in valid for scope in record.metrics["line_counts"].values())
        unclassified = sum(scope["unclassified"] for record in valid for scope in record.metrics["line_counts"].values())
        no_code_runs = sum(1 for record in valid if record.metrics["line_counts"]["non_test"]["code"] == 0)
        lines.append(
            f"  {arm_name}: unclassified-line share {_number(unclassified / total_lines if total_lines else None)}, "
            f"python files ast.parse rejected {sum(r.metrics['python_files_ast_rejected'] for r in valid)}, "
            f"runs with a permission-rule denial {sum(1 for r in arm_records if r.permission_denials)}, "
            f"runs with no non-test code line {no_code_runs}, "
            f"runs with an empty final text {sum(1 for r in arm_records if r.empty_final_text)}, "
            f"non_file_entries {sum(r.metrics['non_file_entries'] for r in valid)}, "
            f"ignored_paths {sum(r.ignored_paths_count for r in arm_records)}, "
            f"undecodable_files {sum(r.metrics['undecodable_files'] for r in valid)}"
        )

    pinned_cap = manifest.get("budget_cap_usd")
    has_pinned_cap = is_usable_cost(pinned_cap) and pinned_cap > 0
    unparsed_cap_usd = pinned_cap if has_pinned_cap else budget_cap_usd
    totals = SpendFile(campaign_dir / SPEND_NAME, budget_cap_usd=unparsed_cap_usd).totals()
    lines.append("spend by account:")
    lines.extend(f"  {account_key!r}: ${totals.account_usd(account_key):.2f}" for account_key in sorted(totals.by_account_usd))
    if totals.unparsed_lines:
        lines.append(
            f"  {totals.unparsed_lines} spend lines did not parse and count at ${unparsed_cap_usd:.2f} each "
            f"({'the cap the campaign pinned' if has_pinned_cap else '--budget-cap-usd, since the manifest pins no usable cap'}) "
            "against every account"
        )
    return lines


def campaign_dir_for(local_dir: Path, campaign_id: str) -> Path:
    return local_dir / CAMPAIGNS_DIR_NAME / campaign_id
