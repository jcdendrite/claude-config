"""`summarize`: the terminal-only summary of one campaign's complete blocks. It writes no file.

The primary outcome, fixed before any run, is comment-plus-docstring words per added non-test code line.
For a task and an arm it is a ratio of sums over that arm's valid runs of the task. A null difference shows
only that the writing agent's instructions leave its first draft's comment density unchanged on these tasks:
no arm runs the review loop through which the toolkit also shapes comments.
"""
from __future__ import annotations

import json
import statistics
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from review_bench import analysis
from review_bench import runner as review_runner
from review_bench.runner import HarnessInvalidatedError, RunStore

from replay_bench import records
from replay_bench import tasks as tasks_mod
from replay_bench.arms import BARE, TOOLKIT
from replay_bench.campaign import CAMPAIGNS_DIR_NAME, MANIFEST_NAME, SPEND_NAME, STORE_DIR_NAME
from replay_bench.records import ReplayRunRecord
from replay_bench.spend import SpendFile

TASKS_DIR_NAME = "tasks"


@dataclass(frozen=True)
class _ArmSums:
    words: int
    code_lines: int

    @property
    def ratio(self) -> float | None:
        return self.words / self.code_lines if self.code_lines else None


def _valid(arm_records: Sequence[ReplayRunRecord]) -> list[ReplayRunRecord]:
    return [record for record in arm_records if record.is_ok and record.metrics is not None]


def _sums(arm_records: Sequence[ReplayRunRecord]) -> _ArmSums:
    valid = _valid(arm_records)
    return _ArmSums(
        words=sum(record.metrics["comment_words"]["non_test"] for record in valid),
        code_lines=sum(record.metrics["line_counts"]["non_test"]["code"] for record in valid),
    )


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


def _read_reference_metrics(local_dir: Path, tasks_name: str | None) -> dict[str, dict]:
    if not tasks_name:
        return {}
    try:
        task_list = tasks_mod.read_task_set(local_dir / TASKS_DIR_NAME, tasks_name)
        return {task.id: task.reference_metrics.to_dict() for task in task_list}
    except (tasks_mod.TaskFileError, ValueError):
        return {}


def _docstring_rate(metrics_list: Sequence[dict]) -> float | None:
    code = sum(metrics["line_counts"]["non_test"]["code"] for metrics in metrics_list)
    docstring = sum(metrics["line_counts"]["non_test"]["docstring"] for metrics in metrics_list)
    return 100 * docstring / code if code else None


def _number(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.4f}"


def summarize(campaign_dir: Path, *, local_dir: Path, budget_cap_usd: float) -> list[str]:
    """The summary lines. Raises HarnessInvalidatedError for a campaign that does not exist, a record that
    does not parse, or one account whose records span more than one environment reading."""
    if not (campaign_dir / records.RUNS_DIR_NAME).is_dir() and not (campaign_dir / MANIFEST_NAME).exists():
        raise HarnessInvalidatedError(f"{str(campaign_dir)!r} holds no campaign")
    completed = RunStore(campaign_dir / STORE_DIR_NAME).completed_block_ids()
    runs_dir = campaign_dir / records.RUNS_DIR_NAME
    on_disk = sorted(path.name for path in runs_dir.iterdir() if path.is_dir()) if runs_dir.is_dir() else []
    complete_ids = [task_id for task_id in on_disk if task_id in completed]
    incomplete_blocks = len(on_disk) - len(complete_ids)
    block_records = {task_id: records.read_block_records(campaign_dir, task_id) for task_id in complete_ids}
    all_records = [record for task_records in block_records.values() for record in task_records]
    _refuse_mixed_environments(all_records)

    lines = [f"campaign {campaign_dir.name!r}: {len(complete_ids)} complete blocks, {incomplete_blocks} incomplete blocks"]
    arm_names = sorted({record.arm for record in all_records})
    for arm_name in arm_names:
        arm_records = [record for record in all_records if record.arm == arm_name]
        counts = review_runner.count_outcomes(arm_records)
        lines.append(f"  {arm_name}: {review_runner.format_outcome_counts(counts)}")

    differences: dict[str, float] = {}
    excluded: dict[str, list[ReplayRunRecord]] = {key: [] for key in ("toolkit", "bare", "both", "code")}
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
        toolkit_ratio, bare_ratio = _sums(toolkit_records).ratio, _sums(bare_records).ratio
        if toolkit_ratio is None or bare_ratio is None:
            excluded_counts["code"] += 1
            continue
        differences[task_id] = toolkit_ratio - bare_ratio

    lines.append("primary outcome: comment-plus-docstring words per added non-test code line (ratio of sums per task and arm)")
    if differences:
        difference = statistics.fmean(differences.values())
        interval = analysis.bootstrap_interval(
            list(differences), lambda ids: statistics.fmean(differences[task_id] for task_id in ids),
            cluster_by_defect={task_id: (task_id, "") for task_id in differences},
        )
        interval_text = (
            f", 95% interval [{interval[0]:.4f}, {interval[1]:.4f}]" if interval
            else ", no interval for fewer than two paired tasks"
        )
        lines.append(f"paired difference toolkit - bare: {difference:.4f}{interval_text}")
    else:
        lines.append("paired difference toolkit - bare: n/a")
    lines.append(f"paired {len(differences)} of {len(complete_ids)} complete tasks")
    labels = {
        "toolkit": "toolkit with no valid run", "bare": "bare with no valid run",
        "both": "both arms with no valid run", "code": "no non-test code line in an arm",
    }
    for key, label in labels.items():
        suffix = f" (missing reasons: {_format_reasons(_reasons(excluded[key]))})" if key != "code" else ""
        lines.append(f"  excluded, {label}: {excluded_counts[key]}{suffix}")

    manifest_path = campaign_dir / MANIFEST_NAME
    tasks_name = None
    if manifest_path.exists():
        try:
            tasks_name = json.loads(manifest_path.read_text(encoding="utf-8")).get("tasks_name")
        except (OSError, ValueError, AttributeError):
            tasks_name = None
    reference = _read_reference_metrics(local_dir, tasks_name)
    lines.append("secondary outcome: Python docstring lines per 100 added non-test code lines")
    reference_rate = _docstring_rate([reference[task_id] for task_id in complete_ids if task_id in reference])
    lines.append(f"  reference (the merged commits) docstring lines per 100 code lines: {_number(reference_rate)}")
    for arm_name in arm_names:
        valid = _valid([record for record in all_records if record.arm == arm_name])
        arm_rate = _docstring_rate([record.metrics for record in valid])
        lines.append(f"  {arm_name} docstring lines per 100 code lines: {_number(arm_rate)}")

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
            f"non_file_entries {sum(r.metrics['non_file_entries'] for r in valid)}, "
            f"ignored_paths {sum(r.ignored_paths_count for r in arm_records)}, "
            f"undecodable_files {sum(r.metrics['undecodable_files'] for r in valid)}"
        )

    totals = SpendFile(campaign_dir / SPEND_NAME, budget_cap_usd=budget_cap_usd).totals()
    lines.append("spend by account:")
    lines.extend(f"  {account_key!r}: ${totals.account_usd(account_key):.2f}" for account_key in sorted(totals.by_account_usd))
    if totals.unparsed_lines:
        lines.append(
            f"  {totals.unparsed_lines} spend lines did not parse and count at ${budget_cap_usd:.2f} each against every account"
        )
    return lines


def campaign_dir_for(local_dir: Path, campaign_id: str) -> Path:
    return local_dir / CAMPAIGNS_DIR_NAME / campaign_id
