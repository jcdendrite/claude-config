"""Tests for the `run` campaign: the manifest, smoke gate, spend guards, per-arm stop and resume. A scripted
launch stands in for `claude`, and `read_environment_record` is stubbed to a fixed reading. No test launches `claude`.
"""
from __future__ import annotations

import dataclasses
import json
import tempfile
import threading
import uuid
from pathlib import Path

import measure_subagent_model_resolution as msmr
import pytest
from replay_bench import arms, campaign, checks, records, smoke_pass
from replay_bench.spend import SpendAppendError, SpendFile, SpendLine
from review_bench import runner as review_runner
from review_bench.runner import HarnessInvalidatedError, RunStore
from test_replay_bench_runner_support import (
    MODEL_ID,
    READING,
    Scenario,
    add_second_account,
    build_replay_world,
    leaking_log_lines,
)

CAP = 1.0
BLOCK_CEILING = 4.0  # 2 arms x k of 1 x the cap x 2 attempts
# A block spends $1.00 and its ceiling is $4.00, so a stop here lets two blocks run and halts before the third.
EARLY_STOP_USD = 5.5


def failing_bare(call):
    """Task-specific failure: the bare run's model never matches, with no instrument reason."""
    return Scenario(observed_model="other-model") if call.agent_name == arms.BARE.agent_name else Scenario()


class CampaignEnv:
    def __init__(self, world, monkeypatch, *, readings=None):
        self.world = world
        self.monkeypatch = monkeypatch
        self.readings = readings if readings is not None else [READING]
        self.reading_calls = 0
        # A second account's config dir, its readings in order, and which repo maps to which account.
        self.second_account = None
        self.second_readings = [READING]
        self.second_reading_calls = 0
        self.account_by_repo = {}
        monkeypatch.setattr(review_runner, "read_environment_record", self._read)
        self.pass_smoke()

    def _read(self, *, checkout_root=None):
        if self.second_account is not None and Path(checkout_root).is_relative_to(self.second_account.config_dir.parent):
            reading = self.second_readings[min(self.second_reading_calls, len(self.second_readings) - 1)]
            self.second_reading_calls += 1
            return reading
        reading = self.readings[min(self.reading_calls, len(self.readings) - 1)]
        self.reading_calls += 1
        return reading

    def add_second_account(self, tasks_in_order_of=None):
        """A second account with its own repo and task. `tasks_in_order_of` receives the first account's task and
        the second's and returns the task list."""
        self.second_account, second_tasks = add_second_account(self.world)
        self.account_by_repo[second_tasks[0].source_repo] = self.second_account
        smoke_pass.write_smoke_pass(
            self.world.local_dir, self.second_account.key, READING, MODEL_ID, ("toolkit", "bare"),
        )
        self.world.tasks[:] = tasks_in_order_of(self.world.tasks[0], second_tasks[0])
        return second_tasks[0]

    def pass_smoke(self, reading=READING, model_id=MODEL_ID, arm_names=("toolkit", "bare")):
        smoke_pass.write_smoke_pass(self.world.local_dir, self.world.account.key, reading, model_id, arm_names)

    def config(self, **overrides) -> campaign.CampaignConfig:
        defaults = dict(
            campaign_id="test", tasks_name="set", tasks=tuple(self.world.tasks), arm_names=("toolkit", "bare"), k=1, seed=0,
            model_id=MODEL_ID, budget_cap_usd=CAP, timeout_s=30, cumulative_stop_usd=100.0, account_stop_usd=100.0,
            allow_early_stop=False, fixture_git_timeout_s=60.0, local_dir=self.world.local_dir, workers=1,
        )
        return campaign.CampaignConfig(**{**defaults, **overrides})

    def run(self, launch_stub, **overrides) -> int:
        config = self.config(**overrides)
        accounts = {task.source_repo: self.account_by_repo.get(task.source_repo, self.world.account) for task in config.tasks}
        return campaign.run_campaign(config, accounts, runner_environ={"PATH": "/usr/bin"}, launch=launch_stub)

    @property
    def campaign_dir(self) -> Path:
        return self.world.local_dir / "campaigns" / "test"

    def completed(self) -> set[str]:
        return RunStore(self.campaign_dir / "store").completed_block_ids()

    def spend(self) -> SpendFile:
        return SpendFile(self.campaign_dir / "spend.jsonl", budget_cap_usd=CAP)


@pytest.fixture
def env(tmp_path, monkeypatch):
    return CampaignEnv(build_replay_world(tmp_path, monkeypatch, task_count=4), monkeypatch)


def failing_tasks_launch(env, failing_briefs):
    def by_call(call):
        if any(brief in call.inner_prompt for brief in failing_briefs):
            return failing_bare(call)
        return Scenario()

    return env.world.launch(by_call=by_call)


def brief_of(env, index):
    """The commit subject of the task at `index` in task-file order (newest first)."""
    return f"Add module {len(env.world.tasks) - 1 - index}"


class TestACompletedCampaign:
    def test_it_writes_each_runs_record_and_diff_marks_every_block_complete_and_exits_zero(self, env, capsys):
        env.world.tasks[:] = env.world.tasks[:2]

        assert env.run(env.world.launch()) == 0

        for task in env.world.tasks:
            assert {path.name for path in records.block_runs_dir(env.campaign_dir, task.id).iterdir()} == {
                "toolkit-0.json", "toolkit-0.diff", "bare-0.json", "bare-0.diff",
            }
        assert env.completed() == {task.id for task in env.world.tasks}
        printed = capsys.readouterr().out
        assert printed.count("arms with no valid run: none") == 2
        assert "cumulative $2.00 of $100.00" in printed

    def test_the_first_start_prints_the_run_count_and_the_nominal_ceiling_before_any_launch(self, env, capsys):
        env.world.tasks[:] = env.world.tasks[:1]

        env.run(env.world.launch())

        assert "2 runs in 1 blocks; nominal ceiling $4.00" in capsys.readouterr().out


class TestTwoAccounts:
    def test_an_abandoned_attempt_under_the_second_accounts_projects_root_is_swept_and_reported(self, env, capsys):
        second_task = env.add_second_account(lambda first, second: [second, first])
        session_id = str(uuid.UUID(int=2))
        abandoned = env.second_account.config_dir / "projects" / "synthetic-project"
        abandoned.mkdir(parents=True)
        (abandoned / f"{session_id}.jsonl").write_text("{}\n")
        gone = Path(tempfile.gettempdir()) / f"{review_runner.FIXTURE_DIR_PREFIX}replay-gone"
        RunStore(env.campaign_dir / "store").record_directory(second_task.id, gone, session_id)

        assert env.run(env.world.launch()) == 0

        assert not (abandoned / f"{session_id}.jsonl").exists(), "the sweep reached the second account's root"
        assert "1 logged attempts with a session ID have no spend line" in capsys.readouterr().out
        assert env.completed() == {task.id for task in env.world.tasks}

    def test_the_account_stop_counts_only_that_accounts_spend_while_the_cumulative_stop_counts_both(self, env, capsys):
        env.add_second_account(lambda first, second: [second, first])
        env.spend().append(SpendLine(env.world.tasks[1].id, "bare", 0, 1, env.world.account.key, "s", 9.0, False))

        code = env.run(env.world.launch(), cumulative_stop_usd=100, account_stop_usd=12.0, allow_early_stop=True)

        assert code == 1
        assert env.completed() == {env.world.tasks[0].id}, "the second account's block ran, the first account's did not"
        assert f"account stop for {env.world.account.key!r}" in capsys.readouterr().err
        assert env.spend().totals().total_usd == 9.0 + 1.0

    def test_a_drift_on_the_second_accounts_environment_halts_at_its_block_and_names_that_task(self, env, capsys):
        second_task = env.add_second_account(lambda first, second: [first, second])
        env.second_readings = [READING, READING, dataclasses.replace(READING, cli_version="2.9.9")]

        assert env.run(env.world.launch()) == 1

        assert env.completed() == {env.world.tasks[0].id}
        assert second_task.id in capsys.readouterr().err

    def test_two_repos_that_share_one_config_dir_are_one_account_with_one_pin_and_one_spend_pool(self, env):
        env.add_second_account(lambda first, second: [first, second])
        env.account_by_repo[env.world.tasks[1].source_repo] = env.world.account  # both repos now share the first account

        assert env.run(env.world.launch()) == 0

        manifest = json.loads((env.campaign_dir / "manifest.json").read_text())
        assert list(manifest["environments"]) == [env.world.account.key]
        assert set(manifest["repo_accounts"].values()) == {env.world.account.key} and len(manifest["repo_accounts"]) == 2
        assert list(env.spend().totals().by_account_usd) == [env.world.account.key]


class TestAFailedSpendAppendHaltsTheCampaign:
    def test_the_halt_names_each_uncharged_launch_by_task_and_session_and_starts_no_run_after_the_first_failure(
        self, env, monkeypatch, capsys,
    ):
        workers = 2  # fewer than the block's four runs, so the bound below can fail
        env.world.tasks[:] = env.world.tasks[:1]

        def failing_append(self, line):
            raise SpendAppendError("could not append to the spend file: OSError: disk full")

        monkeypatch.setattr(SpendFile, "append", failing_append)
        launch_stub = env.world.launch()

        assert env.run(launch_stub, k=2, workers=workers, cumulative_stop_usd=200, account_stop_usd=200) == 1

        error = capsys.readouterr().err
        assert len(launch_stub.calls) <= workers < 4, "at most the runs already in flight launched"
        assert env.completed() == set()
        for call in launch_stub.calls:
            assert f"task {env.world.tasks[0].id} session {call.session_id}" in error


class TestParallelWorkers:
    def test_four_workers_over_a_k_of_two_block_write_one_spend_line_per_attempt_and_every_record(self, env):
        env.world.tasks[:] = env.world.tasks[:1]

        assert env.run(env.world.launch(), k=2, workers=4, cumulative_stop_usd=200, account_stop_usd=200) == 0

        spend_lines = (env.campaign_dir / "spend.jsonl").read_text().splitlines()
        assert len(spend_lines) == 4 and all(json.loads(line)["cost_usd"] == 0.5 for line in spend_lines)
        assert len(list(records.block_runs_dir(env.campaign_dir, env.world.tasks[0].id).glob("*.json"))) == 4


class TestThePerArmStop:
    def test_fail_pass_fail_pass_completes_all_four_and_exits_zero(self, env, capsys):
        launch_stub = failing_tasks_launch(env, [brief_of(env, 0), brief_of(env, 2)])

        assert env.run(launch_stub) == 0
        assert len(env.completed()) == 4
        printed = capsys.readouterr().out.splitlines()

        def progress_of(index):
            return next(line for line in printed if line.startswith(env.world.tasks[index].id))

        assert "arms with no valid run: bare (model-mismatch)" in progress_of(0)
        assert "tasks dropped so far: toolkit 0, bare 1" in progress_of(0)
        assert "arms with no valid run: none" in progress_of(1)
        assert "tasks dropped so far: toolkit 0, bare 2" in progress_of(3)

    def test_two_failing_tasks_halt_with_both_complete_and_the_third_never_launching(self, env, capsys):
        launch_stub = failing_tasks_launch(env, [brief_of(env, 0), brief_of(env, 1)])

        assert env.run(launch_stub) == 1

        assert env.completed() == {env.world.tasks[0].id, env.world.tasks[1].id}
        assert not any(brief_of(env, 2) in call.inner_prompt for call in launch_stub.calls)
        message = capsys.readouterr().err
        assert "bare" in message and "model-mismatch" in message and env.world.tasks[1].id in message

    def test_a_resume_after_the_halt_reruns_neither_failed_block_and_goes_on(self, env):
        env.run(failing_tasks_launch(env, [brief_of(env, 0), brief_of(env, 1)]))
        resumed = env.world.launch()

        assert env.run(resumed) == 0

        briefs = [brief_of(env, index) for index in range(4)]
        assert {brief for call in resumed.calls for brief in briefs if brief in call.inner_prompt} == set(briefs[2:])

    def test_two_failing_tasks_where_the_second_is_the_last_exit_one_not_zero(self, env):
        env.world.tasks[:] = env.world.tasks[:2]

        assert env.run(env.world.launch(by_call=failing_bare)) == 1

    def test_one_failing_task_that_is_last_after_a_passing_one_exits_zero(self, env):
        env.world.tasks[:] = env.world.tasks[:2]
        launch_stub = failing_tasks_launch(env, [brief_of(env, 1)])

        assert env.run(launch_stub) == 0

    @pytest.mark.parametrize("failing_agents", [{"code-writer"}, {"code-writer", "bare-writer"}], ids=["toolkit", "both"])
    def test_a_failing_toolkit_arm_or_both_arms_count_as_failed_blocks(self, env, failing_agents):
        env.world.tasks[:] = env.world.tasks[:2]

        def by_call(call):
            return Scenario(observed_model="other-model") if call.agent_name in failing_agents else Scenario()

        assert env.run(env.world.launch(by_call=by_call)) == 1

    def test_a_block_with_k_two_where_one_arm_has_one_valid_run_is_not_a_failed_block(self, env):
        env.world.tasks[:] = env.world.tasks[:2]
        attempts_seen = {"bare": 0}

        def by_call(call):
            if call.agent_name == "bare-writer":
                attempts_seen["bare"] += 1
                if attempts_seen["bare"] <= 2:
                    return Scenario(observed_model="other-model")
            return Scenario()

        assert env.run(env.world.launch(by_call=by_call), k=2, cumulative_stop_usd=200, account_stop_usd=200) == 0

    def test_an_instrument_failure_halts_at_the_blocks_end_with_the_block_complete_and_a_resume_goes_on(self, env, capsys):
        env.world.tasks[:] = env.world.tasks[:2]
        leaking = Scenario(log_lines=leaking_log_lines(env.world))
        first = env.world.launch(by_call=lambda call: leaking if call.agent_name == "bare-writer" else Scenario())

        assert env.run(first) == 1

        assert env.completed() == {env.world.tasks[0].id}
        error = capsys.readouterr().err
        assert env.world.tasks[0].id in error and "bare" in error and checks.REASON_ARM_LEAK in error
        assert sum(call.agent_name == "bare-writer" for call in first.calls) == 1, "no retry"
        assert env.run(env.world.launch()) == 0
        assert len(env.completed()) == 2


class TestTheManifestAndTheSmokeGate:
    @pytest.mark.parametrize(
        ("field_name", "override"),
        [
            ("k", {"k": 2}), ("seed", {"seed": 9}), ("model_id", {"model_id": "claude-other"}),
            ("arms", {"arm_names": ("toolkit",)}),
        ],
    )
    def test_a_resume_with_a_different_pinned_input_exits_two_before_any_launch(self, env, field_name, override):
        env.world.tasks[:] = env.world.tasks[:1]
        env.run(env.world.launch())
        resumed = env.world.launch()

        with pytest.raises(HarnessInvalidatedError, match=field_name):
            env.run(resumed, **override, cumulative_stop_usd=1000, account_stop_usd=1000)
        assert resumed.calls == []

    def test_a_resume_with_a_different_task_set_exits_two(self, env):
        env.world.tasks[:] = env.world.tasks[:2]
        env.run(env.world.launch())
        env.world.tasks[:] = env.world.tasks[:1]

        with pytest.raises(HarnessInvalidatedError, match="task_set_hash"):
            env.run(env.world.launch())

    def test_a_resume_with_a_changed_fixture_git_timeout_passes_the_comparison(self, env):
        env.world.tasks[:] = env.world.tasks[:1]
        env.run(env.world.launch())

        assert env.run(env.world.launch(), fixture_git_timeout_s=99.0) == 0

    def test_a_resume_after_an_accounts_environment_changed_exits_two_naming_the_account_and_field(self, env):
        env.world.tasks[:] = env.world.tasks[:1]
        env.run(env.world.launch())
        env.readings = [dataclasses.replace(READING, ambient_config_commit="synthetic-commit-b")]
        resumed = env.world.launch()

        with pytest.raises(HarnessInvalidatedError, match=r"ambient_config_commit.*synthetic-commit-b"):
            env.run(resumed)
        assert resumed.calls == []

    def test_a_resume_whose_repo_resolves_to_another_account_key_exits_two_naming_the_repo(self, env):
        env.world.tasks[:] = env.world.tasks[:1]
        env.run(env.world.launch())
        moved = dataclasses.replace(env.world.account, key="/another/account")
        smoke_pass.write_smoke_pass(env.world.local_dir, moved.key, READING, MODEL_ID, ("toolkit", "bare"))

        with pytest.raises(HarnessInvalidatedError, match=r"resolves to account"):
            campaign.run_campaign(
                env.config(), {env.world.tasks[0].source_repo: moved}, runner_environ={}, launch=env.world.launch(),
            )

    def smoke_gate_refusal(self, env) -> str:
        env.world.tasks[:] = env.world.tasks[:1]
        launch_stub = env.world.launch()
        with pytest.raises(HarnessInvalidatedError) as raised:
            env.run(launch_stub)
        assert launch_stub.calls == []
        return str(raised.value)

    def test_no_smoke_pass_record_refuses(self, env):
        smoke_pass.smoke_pass_path(env.world.local_dir, env.world.account.key).unlink()
        assert "no passing smoke" in self.smoke_gate_refusal(env)

    def test_a_record_for_another_reading_refuses_at_the_campaign_level(self, env):
        env.pass_smoke(reading=dataclasses.replace(READING, ambient_config_commit="other-commit"))
        assert "no passing smoke" in self.smoke_gate_refusal(env)

    @pytest.mark.parametrize(
        ("field_name", "stale"),
        [
            ("control", {}),
            ("cli_version", {"reading": dataclasses.replace(READING, cli_version="2.9.9")}),
            ("ambient_config_commit", {"reading": dataclasses.replace(READING, ambient_config_commit="other-commit")}),
            ("model_id", {"model_id": "claude-other"}),
            ("arms", {"arm_names": ("toolkit",)}),
        ],
    )
    def test_each_stale_field_alone_fails_the_record_and_the_matching_one_passes(self, env, field_name, stale):
        env.pass_smoke(**stale)

        accepted = smoke_pass.has_smoke_pass(
            env.world.local_dir, env.world.account.key, READING, MODEL_ID, ("toolkit", "bare"),
        )

        assert accepted is (field_name == "control")

    @pytest.mark.parametrize("damage", ["not json", "{}", '{"account_key": "different"}'])
    def test_a_record_that_does_not_parse_lacks_a_key_or_holds_another_account_refuses_without_a_traceback(self, env, damage):
        smoke_pass.smoke_pass_path(env.world.local_dir, env.world.account.key).write_text(damage)
        assert "no passing smoke" in self.smoke_gate_refusal(env)


class TestEnvironmentDrift:
    def test_drift_at_a_blocks_start_halts_with_no_record_and_the_block_incomplete(self, env, capsys):
        env.world.tasks[:] = env.world.tasks[:1]
        env.readings = [READING, dataclasses.replace(READING, cli_version="2.9.9")]

        assert env.run(env.world.launch()) == 1

        assert env.completed() == set() and not (env.campaign_dir / "runs").exists()
        assert "0 attempts" in capsys.readouterr().err

    def test_drift_at_a_blocks_end_halts_with_no_record_and_prints_the_blocks_spend(self, env, capsys):
        env.world.tasks[:] = env.world.tasks[:1]
        env.readings = [READING, READING, dataclasses.replace(READING, cli_version="2.9.9")]

        assert env.run(env.world.launch()) == 1

        assert env.completed() == set() and not (env.campaign_dir / "runs").exists()
        error = capsys.readouterr().err
        assert "2 attempts" in error and "$1.00 spent" in error


class TestResumeAndAttempts:
    def test_a_crash_between_the_record_writes_and_mark_block_complete_then_a_rerun_leaves_one_file_set(self, env, monkeypatch):
        env.world.tasks[:] = env.world.tasks[:1]
        real_mark = RunStore.mark_block_complete

        def crash(self, defect_id):
            raise RuntimeError("synthetic crash")

        monkeypatch.setattr(RunStore, "mark_block_complete", crash)
        with pytest.raises(RuntimeError):
            env.run(env.world.launch())
        monkeypatch.setattr(RunStore, "mark_block_complete", real_mark)

        assert env.run(env.world.launch()) == 0
        assert len(list(records.block_runs_dir(env.campaign_dir, env.world.tasks[0].id).iterdir())) == 4

    def test_a_nested_attempt_dir_is_gone_after_the_resume(self, env):
        env.world.tasks[:] = env.world.tasks[:1]
        env.run(env.world.launch())
        stale = env.campaign_dir / "attempts" / "stale" / "nested"
        stale.mkdir(parents=True)

        env.run(env.world.launch())

        assert not stale.exists()

    def test_an_absent_attempts_dir_prints_nothing_to_stderr(self, env, capsys):
        env.world.tasks[:] = env.world.tasks[:1]
        env.run(env.world.launch())
        env.run(env.world.launch())  # this resume deletes the empty attempts dir
        capsys.readouterr()

        env.run(env.world.launch())

        assert not (env.campaign_dir / "attempts").exists()
        assert capsys.readouterr().err == ""

    def test_an_attempts_dir_that_is_a_symlink_refuses_naming_the_path_with_the_sentinel_intact(self, env, tmp_path):
        env.world.tasks[:] = env.world.tasks[:1]
        env.run(env.world.launch())
        sentinel_dir = tmp_path / "sentinel"
        sentinel_dir.mkdir()
        (sentinel_dir / "keep.txt").write_text("keep")
        attempts = env.campaign_dir / "attempts"
        if attempts.exists():
            attempts.rmdir()
        attempts.symlink_to(sentinel_dir)

        with pytest.raises(HarnessInvalidatedError, match="attempts"):
            env.run(env.world.launch())
        assert (sentinel_dir / "keep.txt").read_text() == "keep"

    def test_a_swept_entry_with_a_session_id_and_no_spend_line_is_reported_and_not_charged(self, env, capsys):
        env.world.tasks[:] = env.world.tasks[:1]
        store = RunStore(env.campaign_dir / "store")
        gone = Path(tempfile.gettempdir()) / f"{review_runner.FIXTURE_DIR_PREFIX}replay-gone"
        store.record_directory(env.world.tasks[0].id, gone, str(uuid.UUID(int=1)))

        assert env.run(env.world.launch()) == 0

        printed = capsys.readouterr().out
        assert "1 logged attempts with a session ID have no spend line and were not charged" in printed
        assert "leaves out the uncharged attempts" in printed
        assert env.spend().totals().total_usd == 1.0, "only the block's own two attempts, at 0.5 each, are charged"
        assert env.completed() == {env.world.tasks[0].id}


class TestSpendGuards:
    def test_a_block_ceiling_over_a_stop_exits_two_before_any_launch(self, env):
        launch_stub = env.world.launch()
        with pytest.raises(HarnessInvalidatedError, match="one block's nominal ceiling"):
            env.run(launch_stub, cumulative_stop_usd=BLOCK_CEILING - 1, account_stop_usd=BLOCK_CEILING - 1)
        assert launch_stub.calls == []

    def test_a_campaign_ceiling_over_the_cumulative_stop_exits_two_without_allow_early_stop(self, env):
        launch_stub = env.world.launch()
        with pytest.raises(HarnessInvalidatedError, match="allow-early-stop"):
            env.run(launch_stub, cumulative_stop_usd=BLOCK_CEILING * 2, account_stop_usd=BLOCK_CEILING * 2)
        assert launch_stub.calls == []

    def test_with_allow_early_stop_the_cumulative_stop_halts_before_the_block_that_would_pass_it(self, env, capsys):
        assert env.run(
            env.world.launch(), cumulative_stop_usd=EARLY_STOP_USD, account_stop_usd=1000, allow_early_stop=True,
        ) == 1

        assert len(env.completed()) == 2
        assert "cumulative stop" in capsys.readouterr().err

    def test_the_account_stop_halts_the_whole_campaign_naming_the_account(self, env, capsys):
        assert env.run(env.world.launch(), cumulative_stop_usd=1000, account_stop_usd=EARLY_STOP_USD, allow_early_stop=True) == 1

        assert len(env.completed()) == 2
        assert "account stop" in capsys.readouterr().err

    def test_a_resume_compares_the_spend_so_far_plus_the_remaining_ceilings_with_the_stop(self, env):
        env.run(env.world.launch(), cumulative_stop_usd=EARLY_STOP_USD, account_stop_usd=1000, allow_early_stop=True)
        # Two blocks spent $2.00 and two remain with a ceiling of $8.00. The remaining blocks alone ($8.00) and the
        # spend so far plus them ($10.00) differ, and the all-tasks ceiling is $16.00.
        resumed = env.world.launch()
        with pytest.raises(HarnessInvalidatedError, match="allow-early-stop"):
            env.run(resumed, cumulative_stop_usd=9.0, account_stop_usd=1000)
        assert resumed.calls == []

        assert env.run(resumed, cumulative_stop_usd=10.0, account_stop_usd=1000) == 0

    def test_the_resumes_stops_count_a_lost_blocks_spend_lines(self, env):
        env.world.tasks[:] = env.world.tasks[:2]
        env.spend().append(SpendLine(env.world.tasks[1].id, "bare", 0, 1, env.world.account.key, "s", 9.0, False))

        assert env.run(env.world.launch(), cumulative_stop_usd=13.5, account_stop_usd=1000, allow_early_stop=True) == 1

    def test_a_spend_append_that_raises_halts_with_exit_one_and_leaves_the_block_incomplete(self, env, monkeypatch, capsys):
        env.world.tasks[:] = env.world.tasks[:1]

        def failing_append(self, line):
            raise SpendAppendError("could not append to the spend file: OSError: disk full")

        monkeypatch.setattr(SpendFile, "append", failing_append)

        assert env.run(env.world.launch()) == 1
        assert env.completed() == set() and "disk full" in capsys.readouterr().err

    def test_an_interrupt_during_a_live_launch_leaves_the_aborted_attempts_cap_charge(self, env, monkeypatch):
        env.world.tasks[:] = env.world.tasks[:1]

        with pytest.raises(msmr.LaunchAbortedError):
            env.run(env.world.launch(by_call=lambda call: Scenario(raises=msmr.LaunchAbortedError("aborted"))))

        lines = [json.loads(line) for line in (env.campaign_dir / "spend.jsonl").read_text().splitlines()]
        assert lines and all(line["is_cap_charge"] and line["cost_usd"] == CAP for line in lines)


class TestTheSpendFile:
    def line(self, cost=1.0, account="account-a", task="t1"):
        return SpendLine(task, "bare", 0, 1, account, "session", cost, False)

    @pytest.mark.parametrize("unusable", ["NaN", "Infinity", "-1.0", "true", '"1.0"'])
    def test_a_line_whose_cost_is_not_a_finite_non_negative_number_counts_at_the_cap(self, tmp_path, unusable):
        spend = SpendFile(tmp_path / "spend.jsonl", budget_cap_usd=CAP)
        spend.append(self.line(2.0))
        with spend.path.open("a") as handle:
            handle.write(
                '{"task_id": "t", "arm": "bare", "run_index": 0, "attempt": 1, "account_key": "account-a", '
                f'"session_id": "s", "cost_usd": {unusable}, "is_cap_charge": false}}\n'
            )

        totals = spend.totals()

        assert totals.unparsed_lines == 1 and totals.total_usd == 2.0 + CAP

    @pytest.mark.parametrize(
        ("spent", "stop_passed"),
        [(1.0, False), (96.0, False), (96.5, True), (float("nan"), True), (float("inf"), True)],
    )
    def test_a_stop_fails_closed_on_a_total_that_is_not_finite(self, spent, stop_passed):
        assert campaign.stop_would_be_passed(spent, 4.0, 100.0) is stop_passed

    def test_a_torn_last_line_is_truncated_at_resume_and_the_next_append_parses(self, tmp_path):
        spend = SpendFile(tmp_path / "spend.jsonl", budget_cap_usd=CAP)
        spend.append(self.line())
        with spend.path.open("a") as handle:
            handle.write('{"task_id": "t2", "ar')
        spend.repair_torn_tail()
        spend.append(self.line(2.0))

        assert spend.totals().total_usd == 3.0 and spend.totals().unparsed_lines == 0

    def test_an_unparseable_line_that_is_not_the_last_counts_at_the_cap_against_every_account(self, tmp_path):
        spend = SpendFile(tmp_path / "spend.jsonl", budget_cap_usd=CAP)
        spend.append(self.line(2.0, "account-a"))
        with spend.path.open("a") as handle:
            handle.write("not json\n")
        spend.append(self.line(3.0, "account-b"))

        totals = spend.totals()

        assert totals.total_usd == 2.0 + CAP + 3.0
        assert totals.account_usd("account-a") == 2.0 + CAP and totals.account_usd("account-b") == 3.0 + CAP

    def test_two_repos_with_one_config_dir_share_one_accounts_spend(self, tmp_path):
        spend = SpendFile(tmp_path / "spend.jsonl", budget_cap_usd=CAP)
        for task in ("repo-a-task", "repo-b-task"):
            spend.append(self.line(1.5, "shared-account", task))
        spend.append(self.line(4.0, "other-account"))

        totals = spend.totals()

        assert totals.account_usd("shared-account") == 3.0 and totals.account_usd("other-account") == 4.0

    def test_four_threads_appending_leave_no_torn_line_and_the_total_equal_to_the_sum(self, tmp_path):
        spend = SpendFile(tmp_path / "spend.jsonl", budget_cap_usd=CAP)

        def append_many():
            for _ in range(25):
                spend.append(self.line(0.5))

        threads = [threading.Thread(target=append_many) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert len(spend.path.read_text().splitlines()) == 100
        assert spend.totals().total_usd == 50.0 and spend.totals().unparsed_lines == 0
