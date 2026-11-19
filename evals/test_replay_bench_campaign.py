"""Tests for the `run` campaign: the manifest, smoke gate, spend guards, per-arm stop and resume."""
from __future__ import annotations

import dataclasses
import errno
import io
import json
import os
import re
import sys
import tempfile
import threading
import uuid
from collections import Counter
from pathlib import Path

import measure_subagent_model_resolution as msmr
import pytest
from replay_bench import arms, campaign, checks, records, smoke_pass, summary
from replay_bench import runner as replay_runner
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
    make_account,
    synthetic_task,
)

CAP = 1.0
BLOCK_CEILING = 4.0  # 2 arms x k of 1 x the cap x 2 attempts
# A block spends $1.00 and its ceiling is $4.00, so a stop here lets two blocks run and halts before the third.
EARLY_STOP_USD = 5.5
# The longest a thread-handoff step may take before its test fails instead of hanging.
WAIT_S = 5.0


def failing_bare(call):
    """Task-specific failure: the bare run's model never matches, with no instrument reason."""
    return Scenario(observed_model="other-model") if call.agent_name == arms.BARE.agent_name else Scenario()


READ_PHASES = ("pin", "block start", "block end")


def make_config(local_dir, tasks, **overrides) -> campaign.CampaignConfig:
    defaults = dict(
        campaign_id="test", tasks_name="set", tasks=tuple(tasks), arm_names=("toolkit", "bare"), k=1, seed=0,
        model_id=MODEL_ID, budget_cap_usd=CAP, timeout_s=30, cumulative_stop_usd=100.0, account_stop_usd=100.0,
        allow_early_stop=False, fixture_git_timeout_s=60.0, local_dir=local_dir, workers=1,
    )
    return campaign.CampaignConfig(**{**defaults, **overrides})


class CampaignEnv:
    """The environment reading of each account is scripted by phase, and for a block's reads by the block, so a
    test says which read it injects and a change to how many reads another phase makes shifts nothing."""

    def __init__(self, world, monkeypatch):
        self.world = world
        self.monkeypatch = monkeypatch
        # Per phase, the readings in the order that phase asks for them within one block. The last one repeats. A
        # reading is an environment record, or an exception the read raises.
        self.readings = {phase: [READING] for phase in READ_PHASES}
        # The readings of one block's phase, by block number counted from zero, in place of the phase's.
        self.block_readings = {}
        # A second account's config dir, its readings, and which repo maps to which account.
        self.second_account = None
        self.second_readings = {phase: [READING] for phase in READ_PHASES}
        self.account_by_repo = {}
        self._phase = "block start"
        self._block_number = -1
        self._reads_made = Counter()
        monkeypatch.setattr(review_runner, "read_environment_record", self._read)
        monkeypatch.setattr(campaign, "read_environment", self._in_phase("pin", campaign.read_environment))
        monkeypatch.setattr(campaign, "run_block", self._counting_blocks(campaign.run_block))
        monkeypatch.setattr(
            replay_runner, "_read_end_environment",
            self._in_phase("block end", replay_runner._read_end_environment),
        )
        self.pass_smoke()

    def _counting_blocks(self, run_block):
        def run_counted_block(*args, **kwargs):
            self._block_number += 1
            return run_block(*args, **kwargs)

        return run_counted_block

    def _in_phase(self, phase, read):
        def read_in_phase(*args, **kwargs):
            self._phase = phase
            try:
                return read(*args, **kwargs)
            finally:
                self._phase = "block start"

        return read_in_phase

    def _read(self, *, checkout_root=None):
        is_second = self.second_account is not None and Path(checkout_root).is_relative_to(
            self.second_account.config_dir.parent,
        )
        sequence = (self.second_readings if is_second else self.readings)[self._phase]
        sequence = self.block_readings.get((self._block_number, self._phase), sequence)
        read_key = (is_second, self._phase, self._block_number)
        reading = sequence[min(self._reads_made[read_key], len(sequence) - 1)]
        self._reads_made[read_key] += 1
        if isinstance(reading, Exception):
            raise reading
        return reading

    def read_everywhere_as(self, reading) -> None:
        """The first account's every read, in every phase, returns `reading`."""
        self.readings = {phase: [reading] for phase in READ_PHASES}

    def add_second_account(self, tasks_in_order_of=None):
        """`tasks_in_order_of` receives the first account's task and the second's and returns the task list."""
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
        return make_config(self.world.local_dir, self.world.tasks, **overrides)

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

    def assert_nothing_is_pinned_or_spent(self) -> None:
        assert not (self.campaign_dir / "manifest.json").exists(), "a refused campaign pins no inputs"
        assert not (self.campaign_dir / "spend.jsonl").exists(), "a refused campaign spends nothing"


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
    return env.world.tasks[index].brief


class TestACompletedCampaign:
    def test_it_completes_every_block(self, env, capsys):
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

    def test_the_first_start_prints_the_ceiling_line(self, env, capsys):
        env.world.tasks[:] = env.world.tasks[:1]

        env.run(env.world.launch())

        assert "2 runs in 1 blocks; nominal ceiling $4.00" in capsys.readouterr().out


class BlockBufferedStream(io.StringIO):
    """Reports each complete line a block-buffered file would still hold unflushed: one a later write overtakes, or the last."""

    def __init__(self) -> None:
        super().__init__()
        self.stranded_lines: list[str] = []
        self._partial_line = ""
        self._line_awaiting_flush: str | None = None

    def write(self, text: str) -> int:
        if self._line_awaiting_flush is not None:
            self.stranded_lines.append(self._line_awaiting_flush)
            self._line_awaiting_flush = None
        self._partial_line += text
        if text.endswith("\n"):
            self._line_awaiting_flush, self._partial_line = self._partial_line.rstrip("\n"), ""
        return super().write(text)

    def flush(self) -> None:
        self._line_awaiting_flush = None
        super().flush()

    def lines_lost_if_the_process_died_now(self) -> list[str]:
        return [*self.stranded_lines, *([self._line_awaiting_flush] if self._line_awaiting_flush is not None else [])]


class TestProgressReachesARedirectedStdout:
    """Kills: dropping `flush=True` from a campaign print. A detached run's stdout is block-buffered."""

    def test_a_first_start_flushes_every_line(self, env, monkeypatch):
        env.world.tasks[:] = env.world.tasks[:1]
        stream = BlockBufferedStream()
        monkeypatch.setattr(sys, "stdout", stream)

        assert env.run(env.world.launch()) == 0

        assert "nominal ceiling" in stream.getvalue() and "arms with no valid run" in stream.getvalue()
        assert stream.lines_lost_if_the_process_died_now() == []

    def test_a_resume_flushes_every_line(self, env, monkeypatch):
        env.world.tasks[:] = env.world.tasks[:1]

        def crash(self, defect_id):
            raise RuntimeError("synthetic crash")

        with monkeypatch.context() as crashing:
            crashing.setattr(RunStore, "mark_block_complete", crash)
            with pytest.raises(RuntimeError):
                env.run(env.world.launch())
        gone = Path(tempfile.gettempdir()) / f"{review_runner.FIXTURE_DIR_PREFIX}replay-gone"
        RunStore(env.campaign_dir / "store").record_directory(env.world.tasks[0].id, gone, str(uuid.UUID(int=1)))
        stream = BlockBufferedStream()
        monkeypatch.setattr(sys, "stdout", stream)

        assert env.run(env.world.launch()) == 0

        assert "have no spend line" in stream.getvalue() and "spend on blocks not yet complete" in stream.getvalue()
        assert stream.lines_lost_if_the_process_died_now() == []


class TestTwoAccounts:
    def test_the_sweep_reaches_the_second_accounts_projects_root(self, env, capsys):
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

    def test_the_account_stop_counts_only_that_accounts_spend(self, env, capsys):
        env.add_second_account(lambda first, second: [second, first])
        env.spend().append(SpendLine(env.world.tasks[1].id, "bare", 0, 1, env.world.account.key, "s", 9.0, False))

        code = env.run(env.world.launch(), cumulative_stop_usd=100, account_stop_usd=12.0, allow_early_stop=True)

        assert code == 1
        assert env.completed() == {env.world.tasks[0].id}, "the second account's block ran, the first account's did not"
        assert f"account stop for {env.world.account.key!r}" in capsys.readouterr().err
        assert env.spend().totals().total_usd == 9.0 + 1.0

    def test_the_cumulative_stop_counts_both_accounts_spend(self, env, capsys):
        env.add_second_account(lambda first, second: [first, second])
        launch_stub = env.world.launch()
        # The first block spends $1.00 on the first account. The second account has spent nothing, so $0.00 + $4.00
        # clears its stop, while $1.00 + $4.00 across both accounts passes the cumulative stop.
        stop_usd = BLOCK_CEILING + 0.5

        code = env.run(launch_stub, cumulative_stop_usd=stop_usd, account_stop_usd=stop_usd, allow_early_stop=True)

        assert code == 1
        assert env.completed() == {env.world.tasks[0].id}, "the first account's block ran, the second account's did not"
        assert len(launch_stub.calls) == 2, "only the first block's two runs launched"
        error = capsys.readouterr().err
        assert "the cumulative stop" in error and "account stop" not in error

    def test_drift_on_the_second_account_halts_at_its_block(self, env, capsys):
        second_task = env.add_second_account(lambda first, second: [first, second])
        env.second_readings["block end"] = [dataclasses.replace(READING, cli_version="2.9.9")]

        assert env.run(env.world.launch()) == 1

        assert env.completed() == {env.world.tasks[0].id}
        assert second_task.id in capsys.readouterr().err

    def test_two_repos_sharing_a_config_dir_are_one_account(self, env):
        env.add_second_account(lambda first, second: [first, second])
        env.account_by_repo[env.world.tasks[1].source_repo] = env.world.account  # both repos now share the first account

        assert env.run(env.world.launch()) == 0

        manifest = json.loads((env.campaign_dir / "manifest.json").read_text())
        assert list(manifest["environments"]) == [env.world.account.key]
        assert set(manifest["repo_accounts"].values()) == {env.world.account.key} and len(manifest["repo_accounts"]) == 2
        assert list(env.spend().totals().by_account_usd) == [env.world.account.key]


class TestAFailedSpendAppendHaltsTheCampaign:
    def test_the_halt_names_each_uncharged_launch_and_starts_no_run_after_the_first_failure(
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


    def test_a_launch_that_dies_on_a_disk_that_refuses_the_cap_charge_is_named_in_the_halt(self, env, monkeypatch, capsys):
        env.world.tasks[:] = env.world.tasks[:1]

        def failing_append(self, line):
            raise SpendAppendError("could not append to the spend file: OSError: disk full")

        monkeypatch.setattr(SpendFile, "append", failing_append)
        launch_stub = env.world.launch(by_call=lambda call: Scenario(raises=RuntimeError("synthetic")))

        assert env.run(launch_stub, workers=1) == 1

        error = capsys.readouterr().err
        assert f"task {env.world.tasks[0].id} session {launch_stub.calls[0].session_id}" in error
        assert "not charged: none" not in error


class TestASingleArmCampaign:
    @pytest.mark.parametrize("arm_name", ["toolkit", "bare"])
    def test_a_one_arm_campaign_runs_that_arm_only(self, env, arm_name):
        env.world.tasks[:] = env.world.tasks[:1]
        launch_stub = env.world.launch()

        assert env.run(launch_stub, arm_names=(arm_name,)) == 0

        assert {path.name for path in records.block_runs_dir(env.campaign_dir, env.world.tasks[0].id).iterdir()} == {
            f"{arm_name}-0.json", f"{arm_name}-0.diff",
        }
        assert len(launch_stub.calls) == 1
        lines = summary.summarize(env.campaign_dir, local_dir=env.world.local_dir, budget_cap_usd=CAP)
        assert "1 complete blocks, 0 incomplete blocks" in lines[0]
        missing_arm = ({"toolkit", "bare"} - {arm_name}).pop()
        assert "paired difference toolkit - bare: n/a" in lines
        assert f"  excluded, {missing_arm} with no valid run: 1 (missing reasons: none)" in lines

    def test_a_two_arm_campaign_over_a_smoke_that_passed_one_arm_is_refused(self, env):
        env.pass_smoke(arm_names=("toolkit",))
        env.world.tasks[:] = env.world.tasks[:1]
        launch_stub = env.world.launch()

        with pytest.raises(HarnessInvalidatedError, match="no passing smoke"):
            env.run(launch_stub)
        assert launch_stub.calls == []
        env.assert_nothing_is_pinned_or_spent()


class TestTheSmokePassRecord:
    ACCOUNT_KEY = "/synthetic/account"

    def write(self, local_dir, **overrides):
        record = {"reading": READING, "model_id": MODEL_ID, "arm_names": ("toolkit", "bare"), **overrides}
        smoke_pass.write_smoke_pass(
            local_dir, self.ACCOUNT_KEY, record["reading"], record["model_id"], record["arm_names"],
        )

    @pytest.mark.parametrize(
        ("requested", "accepted"),
        [(("bare",), True), (("toolkit",), True), (("toolkit", "bare"), True), (("toolkit", "bare", "other"), False)],
        ids=["bare", "toolkit", "both", "an-arm-the-record-lacks"],
    )
    def test_a_smoke_record_of_both_arms_covers_any_subset_of_them(self, tmp_path, requested, accepted):
        self.write(tmp_path)

        assert smoke_pass.has_smoke_pass(tmp_path, self.ACCOUNT_KEY, READING, MODEL_ID, requested) is accepted

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
    def test_only_a_stale_field_fails_the_record(self, tmp_path, field_name, stale):
        self.write(tmp_path, **stale)

        accepted = smoke_pass.has_smoke_pass(tmp_path, self.ACCOUNT_KEY, READING, MODEL_ID, ("toolkit", "bare"))

        assert accepted is (field_name == "control")


class PinnedCampaign:
    """A campaign whose manifest was written for synthetic inputs. Nothing runs and no fixture is built, so a
    resume that is refused refuses on the manifest alone."""

    def __init__(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        self.account = make_account(home / ".claude", home)
        self.local_dir = tmp_path / "local"
        self.tasks = (synthetic_task(),)
        self.accounts = {self.tasks[0].source_repo: self.account}
        self.manifest_path = self.local_dir / "campaigns" / "test" / "manifest.json"
        monkeypatch.setattr(review_runner, "read_environment_record", lambda *, checkout_root=None: READING)
        self.manifest_path.parent.mkdir(parents=True)
        self.manifest_path.write_text(json.dumps(campaign._manifest_for(
            self.config(), self.accounts, {self.account.key: READING},
        )))

    def config(self, **overrides) -> campaign.CampaignConfig:
        return make_config(self.local_dir, self.tasks, **overrides)

    @staticmethod
    def never_launched(_command, _cwd, _timeout_s):
        pytest.fail("a resume refused on its manifest launched a run")

    def resume(self, **overrides) -> int:
        return campaign.run_campaign(
            self.config(**overrides), self.accounts, runner_environ={}, launch=self.never_launched,
        )


@pytest.fixture
def pinned(tmp_path, monkeypatch):
    return PinnedCampaign(tmp_path, monkeypatch)


class TestAResumeAgainstThePinnedManifest:
    @pytest.mark.parametrize(
        ("field_name", "override"),
        [
            ("k", {"k": 2}), ("seed", {"seed": 9}), ("model_id", {"model_id": "claude-other"}),
            ("arms", {"arm_names": ("toolkit",)}), ("budget_cap_usd", {"budget_cap_usd": 2.0}),
            ("timeout_s", {"timeout_s": 60}),
        ],
    )
    def test_a_resume_with_a_different_input_exits_two(self, pinned, field_name, override):
        with pytest.raises(HarnessInvalidatedError, match=f"{field_name}.*start a new --campaign-id"):
            pinned.resume(**override, cumulative_stop_usd=1000, account_stop_usd=1000)

    @pytest.mark.parametrize(
        ("damage", "refusal"),
        [
            pytest.param(lambda manifest: "not json", "cannot read the manifest", id="invalid-json"),
            pytest.param(
                lambda manifest: "{}", "lacks the key 'task_set_hash'; start a new --campaign-id", id="empty-object",
            ),
            pytest.param(lambda manifest: "[]", "malformed: .*; start a new --campaign-id", id="array"),
            pytest.param(lambda manifest: "null", "malformed: .*; start a new --campaign-id", id="null"),
            pytest.param(lambda manifest: "5", "malformed: .*; start a new --campaign-id", id="number"),
            pytest.param(
                lambda manifest: json.dumps({**manifest, "environments": {}}),
                "lacks the key .*; start a new --campaign-id", id="no-environments-entry",
            ),
            pytest.param(
                lambda manifest: json.dumps({**manifest, "repo_accounts": []}),
                "malformed: .*; start a new --campaign-id", id="wrong-type",
            ),
            pytest.param(
                lambda manifest: json.dumps({key: value for key, value in manifest.items() if key != "budget_cap_usd"}),
                "lacks the key 'budget_cap_usd'; start a new --campaign-id", id="no-budget-cap-pin",
            ),
            pytest.param(
                lambda manifest: json.dumps({key: value for key, value in manifest.items() if key != "timeout_s"}),
                "lacks the key 'timeout_s'; start a new --campaign-id", id="no-timeout-pin",
            ),
        ],
    )
    def test_a_resume_over_a_damaged_manifest_exits_two(self, pinned, damage, refusal):
        pinned.manifest_path.write_text(damage(json.loads(pinned.manifest_path.read_text())))

        with pytest.raises(HarnessInvalidatedError, match=refusal):
            pinned.resume()

    def test_a_resume_whose_manifest_path_is_a_directory_exits_two(self, pinned):
        pinned.manifest_path.unlink()
        pinned.manifest_path.mkdir()

        with pytest.raises(HarnessInvalidatedError, match="cannot read the manifest"):
            pinned.resume()

    def test_a_resume_with_the_pinned_inputs_is_not_refused_on_the_manifest(self, pinned):
        """The control for the refusals above: with nothing changed the manifest check passes and the resume goes on,
        here to the smoke gate, which no smoke record passes."""
        with pytest.raises(HarnessInvalidatedError, match="no passing smoke"):
            pinned.resume()


class TestParallelWorkers:
    def test_four_workers_write_one_spend_line_per_attempt(self, env):
        env.world.tasks[:] = env.world.tasks[:1]

        assert env.run(env.world.launch(), k=2, workers=4, cumulative_stop_usd=200, account_stop_usd=200) == 0

        spend_lines = (env.campaign_dir / "spend.jsonl").read_text().splitlines()
        assert len(spend_lines) == 4 and all(json.loads(line)["cost_usd"] == 0.5 for line in spend_lines)
        assert len(list(records.block_runs_dir(env.campaign_dir, env.world.tasks[0].id).glob("*.json"))) == 4


class TestThePerArmStop:
    def test_alternating_failures_complete_all_four_blocks(self, env, capsys):
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

    def test_two_failing_tasks_in_a_row_halt_the_campaign(self, env, capsys):
        launch_stub = failing_tasks_launch(env, [brief_of(env, 0), brief_of(env, 1)])

        assert env.run(launch_stub) == 1

        assert env.completed() == {env.world.tasks[0].id, env.world.tasks[1].id}
        assert not any(brief_of(env, 2) in call.inner_prompt for call in launch_stub.calls)
        message = capsys.readouterr().err
        assert "bare" in message and "model-mismatch" in message and env.world.tasks[1].id in message

    def test_a_resume_after_the_halt_skips_both_failed_blocks(self, env):
        env.run(failing_tasks_launch(env, [brief_of(env, 0), brief_of(env, 1)]))
        resumed = env.world.launch()

        assert env.run(resumed) == 0

        briefs = [brief_of(env, index) for index in range(4)]
        assert {brief for call in resumed.calls for brief in briefs if brief in call.inner_prompt} == set(briefs[2:])

    def test_two_failing_tasks_ending_the_campaign_exit_one(self, env):
        env.world.tasks[:] = env.world.tasks[:2]

        assert env.run(env.world.launch(by_call=failing_bare)) == 1

    def test_a_lone_failing_last_task_exits_zero(self, env):
        env.world.tasks[:] = env.world.tasks[:2]
        first_task, last_task = env.world.tasks
        launch_stub = failing_tasks_launch(env, [last_task.brief])

        assert env.run(launch_stub) == 0

        assert env.completed() == {first_task.id, last_task.id}
        assert {record.missing_reason for record in records.read_block_records(env.campaign_dir, first_task.id)} == {None}
        last_block = {record.arm: record for record in records.read_block_records(env.campaign_dir, last_task.id)}
        assert last_block[arms.BARE.name].missing_reason == "model-mismatch"

    @pytest.mark.parametrize("failing_agents", [{"code-writer"}, {"code-writer", "bare-writer"}], ids=["toolkit", "both"])
    def test_a_block_with_no_valid_toolkit_run_counts_as_failed(self, env, failing_agents):
        env.world.tasks[:] = env.world.tasks[:2]

        def by_call(call):
            return Scenario(observed_model="other-model") if call.agent_name in failing_agents else Scenario()

        assert env.run(env.world.launch(by_call=by_call)) == 1

    def test_an_arm_with_one_valid_run_is_not_a_failed_block(self, env):
        briefs = [brief_of(env, 0), brief_of(env, 1)]
        env.world.tasks[:] = env.world.tasks[:2]
        bare_attempts_seen_per_task = dict.fromkeys(briefs, 0)

        def by_call(call):
            if call.agent_name == arms.BARE.agent_name:
                brief = next(brief for brief in briefs if brief in call.inner_prompt)
                bare_attempts_seen_per_task[brief] += 1
                # A failed attempt retries once, so a task's first two bare attempts fail.
                if bare_attempts_seen_per_task[brief] <= review_runner.ATTEMPTS_PER_RUN:
                    return Scenario(observed_model="other-model")
            return Scenario()

        assert env.run(env.world.launch(by_call=by_call), k=2, cumulative_stop_usd=200, account_stop_usd=200) == 0

    def test_an_instrument_failure_halts_at_the_blocks_end(self, env, capsys):
        env.world.tasks[:] = env.world.tasks[:2]
        leaking = Scenario(log_lines=leaking_log_lines(env.world))
        first = env.world.launch(by_call=lambda call: leaking if call.agent_name == "bare-writer" else Scenario())

        assert env.run(first) == 1

        assert env.completed() == {env.world.tasks[0].id}
        error = capsys.readouterr().err
        assert env.world.tasks[0].id in error and "bare" in error and checks.REASON_ARM_LEAK in error
        assert "goes on past the block only while" in error
        assert "takes no uncommitted edits" in error
        assert "makes the resume exit 2" in error
        assert "needs a new --campaign-id" in error
        assert sum(call.agent_name == "bare-writer" for call in first.calls) == 1, "no retry"
        assert env.run(env.world.launch()) == 0
        assert len(env.completed()) == 2


class TestTheManifestAndTheSmokeGate:
    def test_a_resume_with_a_different_input_exits_two(self, env):
        env.world.tasks[:] = env.world.tasks[:1]
        env.run(env.world.launch())
        resumed = env.world.launch()

        with pytest.raises(HarnessInvalidatedError, match="k.*start a new --campaign-id"):
            env.run(resumed, k=2, cumulative_stop_usd=1000, account_stop_usd=1000)
        assert resumed.calls == []

    def test_a_resume_over_a_damaged_manifest_exits_two(self, env):
        env.world.tasks[:] = env.world.tasks[:1]
        env.run(env.world.launch())
        (env.campaign_dir / "manifest.json").write_text("not json")
        resumed = env.world.launch()

        with pytest.raises(HarnessInvalidatedError, match="cannot read the manifest"):
            env.run(resumed)
        assert resumed.calls == []

    def test_a_manifest_is_not_written_while_another_invocation_holds_the_campaign_lock(self, env):
        env.world.tasks[:] = env.world.tasks[:1]
        holder = RunStore(env.campaign_dir / "store")
        holder.acquire_lock()
        try:
            launch_stub = env.world.launch()
            with pytest.raises(review_runner.RunStoreLocked):
                env.run(launch_stub, k=2)
        finally:
            holder.release_lock()

        assert not (env.campaign_dir / "manifest.json").exists() and launch_stub.calls == []

    def test_a_first_run_pins_k(self, env):
        env.world.tasks[:] = env.world.tasks[:1]

        assert env.run(env.world.launch(), k=2) == 0

        assert json.loads((env.campaign_dir / "manifest.json").read_text())["k"] == 2

    @pytest.mark.parametrize(
        ("refused_stop_usd", "refusal"),
        [
            pytest.param(5.0, "one block's nominal ceiling", id="block-ceiling"),
            pytest.param(10.0, "allow-early-stop", id="campaign-ceiling"),
        ],
    )
    def test_a_campaign_refused_for_its_ceiling_pins_nothing(self, env, refused_stop_usd, refusal):
        # At k=2 a block's ceiling is $8.00 and two incomplete blocks' is $16.00. A $5.00 stop trips the block
        # ceiling. A $10.00 stop clears it and trips only the campaign ceiling. At k=1 the campaign's is $8.00.
        env.world.tasks[:] = env.world.tasks[:2]
        refused = env.world.launch()
        with pytest.raises(HarnessInvalidatedError, match=refusal):
            env.run(refused, k=2, cumulative_stop_usd=refused_stop_usd, account_stop_usd=1000)
        assert refused.calls == []
        env.assert_nothing_is_pinned_or_spent()

        assert env.run(env.world.launch(), k=1, cumulative_stop_usd=10.0, account_stop_usd=1000) == 0

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

    def test_a_resume_after_an_environment_change_exits_two(self, env):
        env.world.tasks[:] = env.world.tasks[:1]
        env.run(env.world.launch())
        env.read_everywhere_as(dataclasses.replace(READING, ambient_config_commit="synthetic-commit-b"))
        resumed = env.world.launch()

        with pytest.raises(HarnessInvalidatedError, match=r"ambient_config_commit.*synthetic-commit-b"):
            env.run(resumed)
        assert resumed.calls == []

    def test_a_resume_whose_repo_resolves_to_another_account_key_exits_two(self, env):
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
        env.assert_nothing_is_pinned_or_spent()
        return str(raised.value)

    def test_no_smoke_pass_record_refuses(self, env):
        smoke_pass.smoke_pass_path(env.world.local_dir, env.world.account.key).unlink()
        assert "no passing smoke" in self.smoke_gate_refusal(env)

    def test_a_record_for_another_reading_refuses(self, env):
        env.pass_smoke(reading=dataclasses.replace(READING, ambient_config_commit="other-commit"))
        assert "no passing smoke" in self.smoke_gate_refusal(env)

    @pytest.mark.parametrize(
        "second_accounts_record",
        [
            pytest.param(None, id="missing"),
            pytest.param({"reading": dataclasses.replace(READING, cli_version="2.9.9")}, id="stale-reading"),
            pytest.param({"model_id": "claude-other"}, id="stale-model"),
            pytest.param({"arm_names": ("toolkit",)}, id="stale-arms"),
        ],
    )
    def test_a_second_account_without_a_current_smoke_is_refused(
        self, env, second_accounts_record,
    ):
        env.add_second_account(lambda first, second: [first, second])
        second_key = env.second_account.key
        if second_accounts_record is None:
            smoke_pass.smoke_pass_path(env.world.local_dir, second_key).unlink()
        else:
            record = {"reading": READING, "model_id": MODEL_ID, "arm_names": ("toolkit", "bare"), **second_accounts_record}
            smoke_pass.write_smoke_pass(
                env.world.local_dir, second_key, record["reading"], record["model_id"], record["arm_names"],
            )
        launch_stub = env.world.launch()

        with pytest.raises(HarnessInvalidatedError, match=f"account {re.escape(repr(second_key))} has no passing smoke"):
            env.run(launch_stub)
        assert launch_stub.calls == []
        env.assert_nothing_is_pinned_or_spent()

    @pytest.mark.parametrize("damage", ["not json", "{}", {"account_key": "different"}])
    def test_a_damaged_record_refuses(self, env, damage):
        record_path = smoke_pass.smoke_pass_path(env.world.local_dir, env.world.account.key)
        if isinstance(damage, dict):
            damage = json.dumps({**json.loads(record_path.read_text()), **damage})
        record_path.write_text(damage)
        assert "no passing smoke" in self.smoke_gate_refusal(env)


class TestEnvironmentDrift:
    def test_drift_at_a_blocks_start_halts_without_launching(self, env, capsys):
        env.world.tasks[:] = env.world.tasks[:1]
        env.readings["block start"] = [dataclasses.replace(READING, cli_version="2.9.9")]

        launch_stub = env.world.launch()

        assert env.run(launch_stub) == 1

        assert launch_stub.calls == []
        assert env.completed() == set() and not (env.campaign_dir / "runs").exists()
        stderr = capsys.readouterr().err
        assert "0 attempts" in stderr
        assert stderr.startswith("halted: ") and not stderr.startswith("halted: halted: ")
        assert "pays for it again" not in stderr, "a halt that launched nothing has nothing to pay again"

    def test_an_unreadable_start_reading_raises(self, env):
        env.world.tasks[:] = env.world.tasks[:2]
        unreadable = HarnessInvalidatedError("environment record: read timed out")
        env.block_readings[(1, "block start")] = [unreadable]
        launch_stub = env.world.launch()

        with pytest.raises(HarnessInvalidatedError, match="could not read the CLI version"):
            env.run(launch_stub)

        completed = env.completed()
        assert len(completed) == 1
        assert len(launch_stub.calls) == 2, "only the first block's two runs launched"
        assert set(env.spend().totals().by_task_usd) == completed

    def test_an_unlistable_worktree_set_at_a_later_blocks_start_raises(self, env):
        env.world.tasks[:] = env.world.tasks[:2]
        listings = []

        def fail_the_second_listing(checkout):
            listings.append(checkout)
            if len(listings) == 2:
                raise HarnessInvalidatedError("git worktree list failed")
            return ()

        env.monkeypatch.setattr(review_runner, "_worktree_top_levels", fail_the_second_listing)
        launch_stub = env.world.launch()

        with pytest.raises(HarnessInvalidatedError, match="could not list the worktrees"):
            env.run(launch_stub)

        completed = env.completed()
        assert len(completed) == 1
        assert len(launch_stub.calls) == 2, "only the first block's two runs launched"
        assert set(env.spend().totals().by_task_usd) == completed

    def test_drift_at_a_blocks_end_halts_with_the_block_unmarked(self, env, capsys):
        env.world.tasks[:] = env.world.tasks[:1]
        env.readings["block end"] = [dataclasses.replace(READING, cli_version="2.9.9")]

        assert env.run(env.world.launch()) == 1

        assert env.completed() == set() and not (env.campaign_dir / "runs").exists()
        error = capsys.readouterr().err
        assert "2 attempts" in error and "$1.00 spent" in error
        assert "not marked complete" in error and error.count("pays for it again") == 1

    def test_a_resume_after_a_blocks_end_drift_reruns_the_block(self, env):
        env.world.tasks[:] = env.world.tasks[:1]
        env.readings["block end"] = [dataclasses.replace(READING, cli_version="2.9.9")]
        assert env.run(env.world.launch()) == 1
        assert env.completed() == set()

        env.readings["block end"] = [READING]

        assert env.run(env.world.launch()) == 0
        assert env.completed() == {env.world.tasks[0].id}
        assert env.spend().totals().total_usd == 2.0, "the rerun paid for the block again"


    def test_an_end_reading_that_fails_once_is_read_again(self, env):
        env.world.tasks[:] = env.world.tasks[:1]
        env.readings["block end"] = [HarnessInvalidatedError("environment record: read timed out"), READING]

        assert env.run(env.world.launch()) == 0

        assert env.completed() == {env.world.tasks[0].id}

    def test_an_end_reading_that_fails_twice_halts_as_a_failed_read(self, env, capsys):
        env.world.tasks[:] = env.world.tasks[:1]
        unreadable = HarnessInvalidatedError("environment record: read timed out")
        env.readings["block end"] = [unreadable]

        assert env.run(env.world.launch()) == 1

        error = capsys.readouterr().err
        assert "not drift" in error and "2 attempts" in error and "$1.00 spent" in error
        assert "not marked complete" in error and "summarize leaves it out of the arm counts and the paired difference" in error
        assert "a resume reruns the block and pays for it again" in error
        assert env.completed() == set() and not (env.campaign_dir / "runs").exists()


class TestAPaidBlockWhoseRecordsCannotBePersisted:
    @staticmethod
    def disk_full(*_args, **_kwargs):
        raise OSError(errno.ENOSPC, "No space left on device", "/synthetic/path")

    @pytest.mark.parametrize(
        "failing_target",
        [(records, "atomic_write_text"), (RunStore, "mark_block_complete")],
        ids=["record-write", "complete-mark"],
    )
    def test_a_failed_write_halts_with_the_blocks_cost(
        self, env, capsys, failing_target,
    ):
        env.world.tasks[:] = env.world.tasks[:1]
        owner, attribute = failing_target
        with env.monkeypatch.context() as failing:
            failing.setattr(owner, attribute, self.disk_full)

            assert env.run(env.world.launch()) == 1

        error = capsys.readouterr().err
        assert error.startswith("halted: ") and "OSError: No space left on device" in error
        assert "/synthetic/path" not in error
        assert "2 attempts" in error and "$1.00 spent" in error
        assert "not marked complete" in error and "summarize leaves it out of the arm counts and the paired difference" in error
        assert "a resume reruns the block and pays for it again" in error
        assert env.completed() == set()

        assert env.run(env.world.launch()) == 0
        assert env.completed() == {env.world.tasks[0].id}
        assert env.spend().totals().total_usd == 2.0, "the rerun paid for the block again"

    def test_a_resume_after_a_partial_write_ends_with_one_file_set(
        self, env, capsys,
    ):
        env.world.tasks[:] = env.world.tasks[:1]
        task_id = env.world.tasks[0].id
        block_dir = records.block_runs_dir(env.campaign_dir, task_id)
        full_file_set = {"toolkit-0.json", "toolkit-0.diff", "bare-0.json", "bare-0.diff"}
        real_write = records.atomic_write_text
        writes_before_failure = 2
        write_calls = []

        def fail_after_the_earlier_writes(path, text):
            write_calls.append(path)
            if len(write_calls) > writes_before_failure:
                self.disk_full()
            real_write(path, text)

        # The stub replaces atomic_write_text wholesale, so a stray temp file from a real failure is not exercised.
        with env.monkeypatch.context() as failing:
            failing.setattr(records, "atomic_write_text", fail_after_the_earlier_writes)

            assert env.run(env.world.launch()) == 1

        assert "OSError: No space left on device" in capsys.readouterr().err
        assert len(write_calls) == writes_before_failure + 1
        written = {path.name for path in block_dir.iterdir()}
        assert len(written) == writes_before_failure and written < full_file_set, "the earlier files stay on disk"
        assert env.completed() == set()
        summary_lines = summary.summarize(env.campaign_dir, local_dir=env.world.local_dir, budget_cap_usd=CAP)
        assert "0 complete blocks, 1 incomplete blocks" in summary_lines[0]

        assert env.run(env.world.launch()) == 0
        assert {path.name for path in block_dir.iterdir()} == full_file_set
        assert env.completed() == {task_id}
        assert env.spend().totals().total_usd == 2.0, "the rerun paid for the block again"


class TestResumeAndAttempts:
    def test_a_rerun_after_a_pre_mark_crash_leaves_one_file_set(self, env, monkeypatch):
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

    def test_a_resume_removes_a_nested_attempt_dir(self, env):
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

    def test_an_attempts_dir_that_is_a_symlink_refuses(self, env, tmp_path):
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

    def test_a_resume_over_a_torn_spend_line_charges_every_later_attempt_on_a_line_of_its_own(self, env):
        env.run(env.world.launch(), cumulative_stop_usd=EARLY_STOP_USD, account_stop_usd=1000, allow_early_stop=True)
        assert len(env.completed()) == 2
        with env.spend().path.open("a") as handle:
            handle.write('{"task_id": "t2", "ar')

        assert env.run(env.world.launch(), cumulative_stop_usd=1000, account_stop_usd=1000) == 0

        totals = env.spend().totals()
        assert totals.unparsed_lines == 0
        assert totals.total_usd == 4.0, "eight attempts at 0.5 each, with none fused into the torn bytes"

    def test_a_swept_entry_with_no_spend_line_is_reported(self, env, capsys):
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
    def test_a_block_ceiling_over_a_stop_exits_two(self, env):
        launch_stub = env.world.launch()
        with pytest.raises(HarnessInvalidatedError, match="one block's nominal ceiling"):
            env.run(launch_stub, cumulative_stop_usd=BLOCK_CEILING - 1, account_stop_usd=BLOCK_CEILING - 1)
        assert launch_stub.calls == []

    def test_a_campaign_ceiling_over_the_cumulative_stop_exits_two(self, env):
        launch_stub = env.world.launch()
        with pytest.raises(HarnessInvalidatedError, match="allow-early-stop"):
            env.run(launch_stub, cumulative_stop_usd=BLOCK_CEILING * 2, account_stop_usd=BLOCK_CEILING * 2)
        assert launch_stub.calls == []

    def test_allow_early_stop_halts_at_the_cumulative_stop(self, env, capsys):
        assert env.run(
            env.world.launch(), cumulative_stop_usd=EARLY_STOP_USD, account_stop_usd=1000, allow_early_stop=True,
        ) == 1

        assert len(env.completed()) == 2
        assert "cumulative stop" in capsys.readouterr().err

    def test_the_account_stop_halts_the_whole_campaign(self, env, capsys):
        assert env.run(env.world.launch(), cumulative_stop_usd=1000, account_stop_usd=EARLY_STOP_USD, allow_early_stop=True) == 1

        assert len(env.completed()) == 2
        assert "account stop" in capsys.readouterr().err

    def test_a_resume_compares_the_spend_so_far_plus_the_remaining_ceilings_with_the_stop(self, env):
        env.run(env.world.launch(), cumulative_stop_usd=EARLY_STOP_USD, account_stop_usd=1000, allow_early_stop=True)
        # Two blocks spent $2.00 and two remain with a ceiling of $8.00. The remaining blocks alone and the
        # spend so far plus them ($10.00) differ. The all-tasks ceiling is $16.00.
        resumed = env.world.launch()
        with pytest.raises(HarnessInvalidatedError, match="allow-early-stop"):
            env.run(resumed, cumulative_stop_usd=9.0, account_stop_usd=1000)
        assert resumed.calls == []

        assert env.run(resumed, cumulative_stop_usd=10.0, account_stop_usd=1000) == 0

    def test_the_resumes_stops_count_a_lost_blocks_spend_lines(self, env):
        env.world.tasks[:] = env.world.tasks[:2]
        env.spend().append(SpendLine(env.world.tasks[1].id, "bare", 0, 1, env.world.account.key, "s", 9.0, False))

        assert env.run(env.world.launch(), cumulative_stop_usd=13.5, account_stop_usd=1000, allow_early_stop=True) == 1

    def test_a_spend_append_that_raises_halts_with_exit_one(self, env, monkeypatch, capsys):
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

    def test_an_append_under_a_parent_that_is_a_regular_file_raises_spend_append_error(self, tmp_path):
        blocker = tmp_path / "blocker"
        blocker.write_text("a file, not a directory")
        spend = SpendFile(blocker / "spend.jsonl", budget_cap_usd=CAP)

        with pytest.raises(SpendAppendError, match="could not append to the spend file"):
            spend.append(self.line())

    def short_write_once(self, monkeypatch, written_bytes=7):
        real_write = os.write
        calls = []

        def partial_write(descriptor, data):
            calls.append(len(data))
            return real_write(descriptor, data[:written_bytes] if len(calls) == 1 else data)

        monkeypatch.setattr(os, "write", partial_write)

    def test_a_short_write_raises_spend_append_error(self, tmp_path, monkeypatch):
        spend = SpendFile(tmp_path / "spend.jsonl", budget_cap_usd=CAP)
        self.short_write_once(monkeypatch)

        with pytest.raises(SpendAppendError, match="torn line"):
            spend.append(self.line())

    def test_an_append_after_a_short_write_ends_the_torn_line(
        self, tmp_path, monkeypatch,
    ):
        spend = SpendFile(tmp_path / "spend.jsonl", budget_cap_usd=CAP)
        self.short_write_once(monkeypatch)
        with pytest.raises(SpendAppendError):
            spend.append(self.line(2.0))

        spend.append(self.line(3.0))
        spend.append(self.line(4.0))

        totals = spend.totals()
        assert totals.unparsed_lines == 1
        assert totals.total_usd == CAP + 3.0 + 4.0
        assert spend.path.read_text().count("\n\n") == 0, "only the first append after the short write adds a newline"

    def test_an_append_during_a_short_write_waits_for_it(
        self, tmp_path, monkeypatch,
    ):
        """The lock is what makes the torn-tail decision and the write one step. Without it the second append reads
        the flag before the first sets it and fuses its line onto the torn one."""
        spend = SpendFile(tmp_path / "spend.jsonl", budget_cap_usd=CAP)
        first_has_short_written = threading.Event()
        release_first_write = threading.Event()
        second_reached_the_lock = threading.Event()
        lock_entries = []
        errors = {}

        class AnnouncingLock:
            def __init__(self) -> None:
                self._lock = threading.Lock()

            def __enter__(self):
                lock_entries.append(threading.current_thread().name)
                if len(lock_entries) == 2:
                    second_reached_the_lock.set()
                return self._lock.__enter__()

            def __exit__(self, *exception_info):
                return self._lock.__exit__(*exception_info)

        real_write = os.write
        writes = []

        def holding_write(descriptor, data):
            writes.append(data)
            if len(writes) > 1:
                return real_write(descriptor, data)
            written = real_write(descriptor, data[:7])
            first_has_short_written.set()
            release_first_write.wait(timeout=WAIT_S)
            return written

        def append_recording_error(name, line):
            try:
                spend.append(line)
            except SpendAppendError as exc:
                errors[name] = exc

        spend._lock = AnnouncingLock()  # the only seam that shows the second append is blocked on the lock
        monkeypatch.setattr(os, "write", holding_write)
        first = threading.Thread(target=append_recording_error, args=("first", self.line(2.0)), name="first")
        second = threading.Thread(target=append_recording_error, args=("second", self.line(3.0)), name="second")
        try:
            first.start()
            assert first_has_short_written.wait(timeout=WAIT_S)
            second.start()
            assert second_reached_the_lock.wait(timeout=WAIT_S), "the second append never contended for the lock"
            assert len(writes) == 1, "the second append wrote while the first held the lock"
        finally:
            release_first_write.set()
            first.join()
            second.join()

        assert list(errors) == ["first"] and "torn line" in str(errors["first"])
        totals = spend.totals()
        assert totals.unparsed_lines == 1
        assert totals.total_usd == CAP + 3.0

    def test_repairing_a_torn_last_line_truncates_it_so_the_next_append_starts_a_line(self, tmp_path):
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

    def test_lines_under_one_account_key_sum_into_one_account_total(self, tmp_path):
        spend = SpendFile(tmp_path / "spend.jsonl", budget_cap_usd=CAP)
        for task in ("repo-a-task", "repo-b-task"):
            spend.append(self.line(1.5, "shared-account", task))
        spend.append(self.line(4.0, "other-account"))

        totals = spend.totals()

        assert totals.account_usd("shared-account") == 3.0 and totals.account_usd("other-account") == 4.0

    def test_four_threads_appending_leave_no_torn_line(self, tmp_path):
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
