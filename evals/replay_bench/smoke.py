"""`smoke`: one block of one run per arm on one task, which checks the instrument end to end.

The block calibrates the instrument, since the instruction-load hook cannot show the `CLAUDE.md` a subagent
inherits and no source read says that `bare`'s exclude holds under `-p`. `smoke` writes the account's
smoke-pass record only when every condition holds, and `run` requires that record.
"""
from __future__ import annotations

import sys
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from review_bench import runner as review_runner
from review_bench.runner import EnvironmentReference, HarnessInvalidatedError, RunStore

from replay_bench import arms as arms_mod
from replay_bench import checks, smoke_pass
from replay_bench import tasks as tasks_mod
from replay_bench.account import Account
from replay_bench.attempt import Launch, RunSettings
from replay_bench.campaign import ATTEMPTS_DIR_NAME, CAMPAIGNS_DIR_NAME, SPEND_NAME, STORE_DIR_NAME
from replay_bench.runner import BlockOutcome, RunOutcome, run_block
from replay_bench.spend import SpendAppendError, SpendFile

SMOKE_ARM_NAMES = (arms_mod.TOOLKIT.name, arms_mod.BARE.name)
CONDITION_READ_GRANT_DENIED = "read-grant-denied"
CONDITION_READ_GRANT_UNEXERCISED = "read-grant-unexercised"


@dataclass(frozen=True)
class SmokeConfig:
    model_id: str
    budget_cap_usd: float
    timeout_s: int
    fixture_git_timeout_s: float
    local_dir: Path
    seed: int = 0


def _shown(values) -> str:
    return "[" + ", ".join(repr(value) for value in values) + "]"


def format_run_report(arm_name: str, outcome: RunOutcome) -> list[str]:
    """Everything the engineer reads about one run. Agent-supplied text prints in `!r` form."""
    record = outcome.record
    status = "ok" if record.is_ok else f"missing ({record.missing_reason})"
    lines = [f"[{arm_name}] status: {status}"]
    if record.missing_detail:
        lines.append(f"  detail: {record.missing_detail}")
    loads = {load_class: sorted(paths) for load_class, paths in record.subagent_loads.items()}
    lines.append(f"  subagent instruction loads: {loads!r}")
    lines.append(f"  first-turn prompt size: {record.first_turn_prompt_size}")
    lines.append(f"  recorded Bash commands: {_shown(record.bash_commands)}")
    lines.append(f"  out-of-fixture paths: {_shown(record.out_of_fixture_tokens)}")
    lines.append(f"  out-of-fixture writes: {_shown(record.out_of_fixture_writes)}")
    lines.append(f"  other absolute paths outside the fixture: {_shown(record.other_outside_fixture_tokens)}")
    lines.append(f"  out-of-session paths: {_shown(record.out_of_session_paths)}")
    lines.append(f"  permission denials: {record.permission_denials}")
    denied_allow_list = [read["path"] for read in record.allow_list_reads if read["denied"]]
    lines.append(f"  denied Reads of allow-list paths: {_shown(denied_allow_list)}")
    lines.append(f"  denied Reads of other toolkit files: {_shown(record.denied_other_toolkit_reads)}")
    if arm_name == arms_mod.TOOLKIT.name:
        ran = sum(1 for read in record.allow_list_reads if not read["denied"])
        lines.append(f"  Reads of allow-list paths that ran: {ran}")
    metrics = record.metrics or {}
    lines.append(
        f"  non_file_entries: {metrics.get('non_file_entries', 0)}, ignored_paths: {record.ignored_paths_count}, "
        f"undecodable_files: {metrics.get('undecodable_files', 0)}"
    )
    if metrics:
        lines.append(
            f"  metrics: non-test code lines {metrics['line_counts']['non_test']['code']}, "
            f"comment-plus-docstring words {metrics['comment_words']['non_test']}"
        )
    lines.append(
        f"  cost: ${record.total_cost_usd:.2f}, fixture build: {record.fixture_build_s}, wall clock: {record.wall_clock_s:.1f} s"
    )
    return lines


def _chars(path: Path) -> int:
    try:
        return len(path.read_text(encoding="utf-8", errors="replace"))
    except OSError as exc:
        raise HarnessInvalidatedError(f"cannot read {str(path)!r}: {review_runner._os_error_detail(exc)}") from None


def calibration_terms(account: Account, toolkit_final) -> tuple[float, float, int, list[int]]:
    """The body term and margin in tokens, then the account `CLAUDE.md`'s character count and the
    dispatcher-loaded rule files' character counts."""
    claude_md_chars = _chars(account.config_dir / "CLAUDE.md")
    code_writer_body_chars = arms_mod.agent_body_chars(arms_mod.code_writer_text(account.config_dir))
    bare_writer_body_chars = arms_mod.agent_body_chars(account.bare_writer_markdown)
    rule_chars = [
        _chars(path) for path in checks.dispatcher_rule_files(
            toolkit_final.log, toolkit_final.toolkit, fixture_dir=toolkit_final.fixture_dir,
        )
    ]
    body_term = (code_writer_body_chars - bare_writer_body_chars) / checks.CHARS_PER_TOKEN_ESTIMATE
    margin = (checks.CLAUDE_MD_MARGIN_FRACTION * claude_md_chars + sum(rule_chars)) / checks.CHARS_PER_TOKEN_ESTIMATE
    return body_term, margin, claude_md_chars, rule_chars


def evaluate_conditions(block: BlockOutcome, account: Account) -> tuple[list[tuple[str, str]], list[str]]:
    """The failed conditions in the plan's order as (name, text) pairs, and the calibration lines to print."""
    by_arm = {arm: outcome for arm, _index, outcome in block.runs}
    toolkit_outcome, bare_outcome = by_arm[arms_mod.TOOLKIT.name], by_arm[arms_mod.BARE.name]
    failed: list[tuple[str, str]] = []
    for arm_name, outcome in by_arm.items():
        if not outcome.record.is_ok:
            failed.append((outcome.record.missing_reason or "unknown", f"{arm_name} run missing"))
    toolkit_final, bare_final = toolkit_outcome.final, bare_outcome.final
    printed: list[str] = []
    if toolkit_final.completed:
        reads = toolkit_outcome.record.allow_list_reads
        if any(read["denied"] for read in reads):
            failed.append((CONDITION_READ_GRANT_DENIED, "a toolkit Read of an allow-list path was denied"))
        if not any(not read["denied"] for read in reads):
            failed.append((CONDITION_READ_GRANT_UNEXERCISED, "no toolkit Read of an allow-list path ran"))
    if toolkit_final.completed and bare_final.completed and toolkit_final.log is not None:
        body_term, margin, claude_md_chars, rule_chars = calibration_terms(account, toolkit_final)
        verdict = checks.block_calibration_verdict(
            toolkit_final.log, toolkit_first_turn_size=toolkit_final.first_turn_prompt_size,
            bare_first_turn_size=bare_final.first_turn_prompt_size, toolkit=toolkit_final.toolkit,
            fixture_dir=toolkit_final.fixture_dir, claude_md_chars=claude_md_chars,
            code_writer_body_chars=arms_mod.agent_body_chars(arms_mod.code_writer_text(account.config_dir)),
            bare_writer_body_chars=arms_mod.agent_body_chars(account.bare_writer_markdown),
            dispatcher_rule_chars=rule_chars,
        )
        printed.append(
            f"first-turn prompt sizes: toolkit {toolkit_final.first_turn_prompt_size}, bare {bare_final.first_turn_prompt_size}; "
            f"body term {body_term:g} tokens; margin {margin:g} tokens"
        )
        failed.extend((reason, f"block calibration: {reason}") for reason in verdict)
        bare_transcript = bare_final.transcript or checks.SubagentTranscript((), 0, None)
        printed.extend(checks.exclude_note_lines(
            toolkit_final.log, bare_transcript, toolkit=toolkit_final.toolkit,
            toolkit_fixture_dir=toolkit_final.fixture_dir, bare_fixture_dir=bare_final.fixture_dir,
        ))
    else:
        printed.append("block calibration not computed: a run did not reach its end")
        printed.append(checks.EXCLUDE_NOTE_RESIDUAL)
    return failed, printed


def run_smoke(
    config: SmokeConfig, account: Account, task: tasks_mod.ReplayTask, *, runner_environ: Mapping[str, str], launch: Launch,
) -> int:
    """Exit 0 on `instrument: ok`, 2 otherwise. Raises RunStoreLocked, HarnessInvalidatedError and an interrupt."""
    campaign_id = f"smoke-{uuid.uuid4().hex[:8]}"
    campaign_dir = config.local_dir / CAMPAIGNS_DIR_NAME / campaign_id
    ceiling = review_runner.campaign_cost_ceiling(1, len(SMOKE_ARM_NAMES), 1, budget_cap_usd=config.budget_cap_usd)
    print(f"smoke {campaign_id}: nominal ceiling ${ceiling.all_retried_usd:.2f}")
    run_store = RunStore(campaign_dir / STORE_DIR_NAME)
    run_store.acquire_lock()
    try:
        spend = SpendFile(campaign_dir / SPEND_NAME, budget_cap_usd=config.budget_cap_usd)
        settings = RunSettings(
            campaign_id=campaign_id, attempts_root=campaign_dir / ATTEMPTS_DIR_NAME, local_dir=config.local_dir,
            model_id=config.model_id, budget_cap_usd=config.budget_cap_usd, timeout_s=config.timeout_s,
            fixture_git_timeout_s=config.fixture_git_timeout_s, run_store=run_store, spend=spend,
            runner_environ=runner_environ, launch=launch,
        )
        try:
            block = run_block(settings, task, account, SMOKE_ARM_NAMES, 1, config.seed, EnvironmentReference())
        except SpendAppendError as exc:
            launches = "; ".join(f"task {task_id} session {session_id}" for task_id, session_id in settings.uncharged_launches)
            print(f"{exc}. Launches begun and not charged: {launches or 'none'}", file=sys.stderr)
            return 2
        if block.end_drift is not None:
            print(block.end_drift, file=sys.stderr)
            return 2
        for arm_name in SMOKE_ARM_NAMES:
            for outcome in block.outcomes_for(arm_name):
                print("\n".join(format_run_report(arm_name, outcome)))
        failed, printed = evaluate_conditions(block, account)
        print("\n".join(printed))
        for name, text in failed:
            print(f"failed: {name}: {text}")
        if failed:
            print(f"instrument: {failed[0][0]} ({failed[0][1]})")
            return 2
        record = block.outcomes_for(arms_mod.TOOLKIT.name)[0].record
        reading = review_runner.EnvironmentRecord(record.cli_version, record.ambient_config_commit)
        smoke_pass.write_smoke_pass(config.local_dir, account.key, reading, config.model_id, SMOKE_ARM_NAMES)
        print("instrument: ok")
        return 0
    finally:
        run_store.release_lock()
