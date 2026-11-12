"""One replay run's record, as written under `.local/campaigns/<campaign-id>/runs/<task-id>/`.

`status`, `missing_reason`, `missing_detail` and `attempts` keep the review bench's meanings, so its
`count_outcomes` and `all_runs_missing` apply as they are. A record holds raw agent commands and absolute
machine paths, so it belongs only in gitignored storage.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from review_bench.defects import atomic_write_text
from review_bench.runner import STATUS_OK, HarnessInvalidatedError

RUNS_DIR_NAME = "runs"


@dataclass
class ReplayRunRecord:
    campaign_id: str
    task_id: str
    arm: str
    run_index: int
    status: str
    missing_reason: str | None
    missing_detail: str | None
    attempts: int
    account_key: str
    session_id: str
    cli_version: str
    ambient_config_commit: str
    wall_clock_s: float
    total_cost_usd: float
    cap_charged_attempts: int = 0
    observed_model: str | None = None
    fixture_build_s: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    # `CommentMetrics.to_dict()` of the run's change, or None for a run that ended missing.
    metrics: dict | None = None
    first_turn_prompt_size: int | None = None
    # Class name -> config- or fixture-relative path -> load count, for the subagent's instruction loads.
    subagent_loads: dict = field(default_factory=dict)
    permission_denials: int = 0
    allow_list_reads: list[dict] = field(default_factory=list)
    denied_other_toolkit_reads: list[str] = field(default_factory=list)
    bash_commands: list[str] = field(default_factory=list)
    out_of_fixture_writes: list[str] = field(default_factory=list)
    out_of_fixture_tokens: list[str] = field(default_factory=list)
    other_outside_fixture_tokens: list[str] = field(default_factory=list)
    unparsed: list[str] = field(default_factory=list)
    out_of_session_paths: list[str] = field(default_factory=list)
    ignored_paths_count: int = 0
    ignored_paths: list[str] = field(default_factory=list)

    @property
    def is_ok(self) -> bool:
        return self.status == STATUS_OK

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> ReplayRunRecord:
        """Raises ValueError for a missing required key or an unknown key."""
        known = {item.name for item in fields(cls)}
        unknown = sorted(set(data) - known)
        if unknown:
            raise ValueError(f"unknown record keys {unknown}")
        try:
            return cls(**data)
        except TypeError as exc:
            raise ValueError(str(exc)) from exc


def block_runs_dir(campaign_dir: Path, task_id: str) -> Path:
    return campaign_dir / RUNS_DIR_NAME / task_id


def record_path(campaign_dir: Path, task_id: str, arm: str, run_index: int) -> Path:
    return block_runs_dir(campaign_dir, task_id) / f"{arm}-{run_index}.json"


def diff_path(campaign_dir: Path, task_id: str, arm: str, run_index: int) -> Path:
    return block_runs_dir(campaign_dir, task_id) / f"{arm}-{run_index}.diff"


def write_run(campaign_dir: Path, record: ReplayRunRecord, diff_text: str | None) -> None:
    """The diff first, then the record, each atomically. A block rerun after a crash overwrites its own files."""
    if diff_text is not None:
        atomic_write_text(diff_path(campaign_dir, record.task_id, record.arm, record.run_index), diff_text)
    atomic_write_text(
        record_path(campaign_dir, record.task_id, record.arm, record.run_index), json.dumps(record.to_dict(), indent=2) + "\n",
    )


def read_block_records(campaign_dir: Path, task_id: str) -> list[ReplayRunRecord]:
    """Raises HarnessInvalidatedError, naming the file, for a record that does not parse."""
    records = []
    for path in sorted(block_runs_dir(campaign_dir, task_id).glob("*.json")):
        try:
            records.append(ReplayRunRecord.from_dict(json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            raise HarnessInvalidatedError(f"{path}: cannot read the run record: {exc}") from exc
    return records
