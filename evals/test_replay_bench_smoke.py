"""Tests for `smoke`: one block of one run per arm, its calibration, its printout and its smoke-pass record.
A scripted launch stands in for `claude`. No test launches `claude`.
"""
from __future__ import annotations

import pytest
from replay_bench import checks, smoke, smoke_pass
from review_bench import runner as review_runner
from test_replay_bench_checks_support import _line
from test_replay_bench_runner_support import (
    AGENT_ID,
    CLAUDE_MD_TEXT,
    MODEL_ID,
    READING,
    RULE_TEXT,
    Scenario,
    build_replay_world,
    leaking_log_lines,
)

CAP = 2.0


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(review_runner, "read_environment_record", lambda *, checkout_root=None: READING)
    return build_replay_world(tmp_path, monkeypatch)


def run_smoke(world, by_call=None, scenario_for=None):
    launch_stub = world.launch(scenario_for, by_call)
    config = smoke.SmokeConfig(
        model_id=MODEL_ID, budget_cap_usd=CAP, timeout_s=30, fixture_git_timeout_s=60.0, local_dir=world.local_dir,
    )
    exit_code = smoke.run_smoke(config, world.account, world.task, runner_environ={"PATH": "/usr/bin"}, launch=launch_stub)
    return exit_code, launch_stub


def smoke_pass_exists(world) -> bool:
    return smoke_pass.smoke_pass_path(world.local_dir, world.account.key).exists()


def agent_scenarios(**by_agent):
    """A `by_call` choosing a scenario by agent name: `toolkit=Scenario(...)`, `bare=Scenario(...)`."""
    return lambda call: by_agent.get("toolkit" if call.agent_name == "code-writer" else "bare") or Scenario()


class TestAPassingSmoke:
    def test_it_prints_instrument_ok_and_writes_the_account_record_naming_the_arms(self, world, capsys):
        exit_code, launch_stub = run_smoke(world)

        output = capsys.readouterr().out
        assert exit_code == 0 and output.strip().splitlines()[-1] == "instrument: ok"
        assert smoke_pass.has_smoke_pass(world.local_dir, world.account.key, READING, MODEL_ID, ("toolkit", "bare"))
        assert not smoke_pass.has_smoke_pass(world.local_dir, world.account.key, READING, MODEL_ID, ("toolkit", "bare", "other"))
        assert sorted(call.agent_name for call in launch_stub.calls) == ["bare-writer", "code-writer"], "one run per arm"

    def test_each_runs_printout_carries_the_build_seconds_sizes_body_term_and_margin(self, world, capsys):
        run_smoke(world)

        output = capsys.readouterr().out
        assert output.count("fixture build:") == 2
        assert "first-turn prompt sizes: toolkit 6000, bare 1000" in output
        expected_margin = checks.CLAUDE_MD_MARGIN_FRACTION * len(CLAUDE_MD_TEXT) / checks.CHARS_PER_TOKEN_ESTIMATE
        assert f"margin {expected_margin:g} tokens" in output and "body term" in output
        assert "first-turn prompt size: 6000" in output

    def test_the_exclude_note_prints_after_the_runs_and_the_residual_sentence_always_prints(self, world, capsys):
        run_smoke(world)

        output = capsys.readouterr().out
        assert output.index("[bare] status") < output.index(checks.EXCLUDE_NOTE_EXERCISED)
        assert checks.EXCLUDE_NOTE_RESIDUAL in output

    def test_a_bare_run_that_read_no_trigger_path_still_writes_the_record_when_every_condition_holds(self, world, capsys):
        bare_reads_elsewhere = Scenario(tool_calls=[("Read", {"file_path": "src/mod_0.py"}, False)])

        exit_code, _stub = run_smoke(world, by_call=agent_scenarios(bare=bare_reads_elsewhere))

        output = capsys.readouterr().out
        assert exit_code == 0 and smoke_pass_exists(world)
        assert checks.EXCLUDE_NOTE_NOT_EXERCISED in output

    def test_the_printed_ceiling_equals_the_cost_ceiling_for_one_block_and_comes_before_any_launch(self, world, capsys):
        run_smoke(world)

        first_line = capsys.readouterr().out.splitlines()[0]
        ceiling = review_runner.campaign_cost_ceiling(1, 2, 1, budget_cap_usd=CAP).all_retried_usd
        assert f"nominal ceiling ${ceiling:.2f}" in first_line


class TestFailedConditions:
    def failing(self, world, capsys, by_call):
        exit_code, _stub = run_smoke(world, by_call=by_call)
        output = capsys.readouterr().out
        return exit_code, output, output.strip().splitlines()[-1]

    def test_a_toolkit_run_with_no_listed_read_fails_read_grant_unexercised_and_writes_no_record(self, world, capsys):
        reads_only_fixture = Scenario(tool_calls=[("Read", {"file_path": "src/app.py"}, False)])

        exit_code, output, final = self.failing(world, capsys, agent_scenarios(toolkit=reads_only_fixture))

        assert exit_code == 2 and final.startswith("instrument: read-grant-unexercised")
        assert not smoke_pass_exists(world)

    def test_a_denied_listed_read_fails_read_grant_denied(self, world, capsys):
        denied = Scenario(tool_calls=[("Read", {"file_path": str(world.config_dir / "agents" / "reviewer.md")}, True)])

        _exit_code, _output, final = self.failing(world, capsys, agent_scenarios(toolkit=denied))

        assert final.startswith("instrument: read-grant-denied")

    def test_a_denied_read_of_an_unlisted_toolkit_file_prints_by_path_and_fails_nothing(self, world, capsys):
        unlisted = str(world.config_dir / "rules" / "python.md")
        scenario = Scenario(tool_calls=[
            ("Read", {"file_path": str(world.config_dir / "agents" / "reviewer.md")}, False),
            ("Read", {"file_path": unlisted}, True),
        ])

        exit_code, output, _final = self.failing(world, capsys, agent_scenarios(toolkit=scenario))

        assert exit_code == 0 and repr(unlisted) in output

    def test_a_bare_run_that_reads_a_listed_path_fails_arm_leak_never_read_grant_denied(self, world, capsys):
        leaking = Scenario(tool_calls=[("Read", {"file_path": str(world.config_dir / "agents" / "reviewer.md")}, True)])

        _exit_code, output, final = self.failing(world, capsys, agent_scenarios(bare=leaking))

        assert final.startswith("instrument: arm-leak") and "read-grant-denied" not in output

    def test_a_toolkit_log_with_no_subagent_rule_load_fails_instrument_missing(self, world, capsys):
        no_control = Scenario(log_lines=lambda sid: [_line(str(world.config_dir / "CLAUDE.md"), session_id=sid)])

        _exit_code, _output, final = self.failing(world, capsys, agent_scenarios(toolkit=no_control))

        assert final.startswith("instrument: instrument-missing")

    def test_a_short_inheritance_gap_fails_arm_config_missing(self, world, capsys):
        short_gap = Scenario(first_turn_size=1500)

        _exit_code, _output, final = self.failing(world, capsys, agent_scenarios(toolkit=short_gap))

        assert final.startswith("instrument: arm-config-missing")

    def test_a_failure_in_each_stage_names_the_first_condition_and_prints_all_three(self, world, capsys):
        leaking = Scenario(tool_calls=[("Read", {"file_path": str(world.config_dir / "agents" / "reviewer.md")}, False)])
        toolkit = Scenario(first_turn_size=1500, tool_calls=[("Read", {"file_path": "src/app.py"}, False)])

        _exit_code, output, final = self.failing(world, capsys, agent_scenarios(toolkit=toolkit, bare=leaking))

        assert final.startswith("instrument: arm-leak")
        failed = [line for line in output.splitlines() if line.startswith("failed: ")]
        assert [line.split(":")[1].strip() for line in failed] == ["arm-leak", "read-grant-unexercised", "arm-config-missing"]

    def test_a_toolkit_run_that_ended_missing_on_a_timeout_reports_it_and_the_verdict_never_runs(self, world, capsys):
        timed_out = Scenario(timed_out=True, cost=None)

        _exit_code, output, final = self.failing(world, capsys, agent_scenarios(toolkit=timed_out))

        assert final.startswith("instrument: timeout") and "toolkit run missing" in final
        assert "first-turn prompt sizes" not in output and "block calibration not computed" in output


class TestThePrintoutHoldsThePathEvidence:
    def test_a_failing_token_and_the_command_that_named_it_print_in_repr_form_with_no_raw_escape(self, world, capsys):
        token = str(world.local_dir / "campaigns" / "other" / "runs")
        command = f"printf '\x1b[31m'\nls {token}"
        scenario = Scenario(tool_calls=[("Bash", {"command": command}, False)])

        exit_code, _stub = run_smoke(world, by_call=agent_scenarios(bare=scenario))

        output = capsys.readouterr().out
        assert exit_code == 2 and "missing (out-of-fixture-path)" in output
        assert f"out-of-fixture paths: [{token!r}]" in output
        assert repr(command) in output and "\x1b" not in output

    def test_a_write_outside_the_fixture_prints_its_target_in_repr_form(self, world, capsys):
        target = str(world.root / "elsewhere\x1b.py")
        scenario = Scenario(tool_calls=[("Write", {"file_path": target}, False)])

        exit_code, _stub = run_smoke(world, by_call=agent_scenarios(bare=scenario))

        output = capsys.readouterr().out
        assert exit_code == 2 and "missing (out-of-fixture-write)" in output
        assert f"out-of-fixture writes: [{target!r}]" in output and "\x1b" not in output


class TestTheMarginsRuleTerm:
    def test_a_rule_the_dispatcher_loaded_at_session_start_raises_the_printed_margin_by_its_size_over_four(
        self, world, capsys,
    ):
        rule = str(world.config_dir / "rules" / "python.md")
        with_dispatcher_rule = Scenario(log_lines=lambda sid: [
            _line(str(world.config_dir / "CLAUDE.md"), session_id=sid),
            _line(rule, session_id=sid),
            _line(rule, "path_glob_match", agent_id=AGENT_ID, session_id=sid),
        ])

        exit_code, _stub = run_smoke(world, by_call=agent_scenarios(toolkit=with_dispatcher_rule))

        expected = (checks.CLAUDE_MD_MARGIN_FRACTION * len(CLAUDE_MD_TEXT) + len(RULE_TEXT)) / checks.CHARS_PER_TOKEN_ESTIMATE
        assert exit_code == 0 and f"margin {expected:g} tokens" in capsys.readouterr().out


class TestNoRetryOnInstrumentReasons:
    def scenario_for_reason(self, world, reason):
        return {
            checks.REASON_ARM_LEAK: ("bare-writer", Scenario(log_lines=leaking_log_lines(world))),
            checks.REASON_INSTRUMENT_MISSING: ("code-writer", Scenario(log_lines=[])),
            checks.REASON_INSTRUMENT_LOG_INVALID: ("code-writer", Scenario(log_lines=["not an object"])),
            checks.REASON_FIXTURE_INSTRUCTION_LOAD: ("code-writer", Scenario(
                log_lines=lambda sid, cwd: [
                    _line(str(world.config_dir / "CLAUDE.md"), session_id=sid),
                    _line(str(cwd / "src" / "app.py"), "path_glob_match", agent_id=AGENT_ID, session_id=sid),
                ],
            )),
            checks.REASON_ACCOUNT_HOOKS_MISSING: ("code-writer", Scenario(write_account_session_file=False)),
        }[reason]

    @pytest.mark.parametrize("reason", sorted(checks.INSTRUMENT_REASONS))
    def test_each_instrument_reason_ends_the_run_missing_after_one_attempt_with_no_record(self, world, capsys, reason):
        agent, failing = self.scenario_for_reason(world, reason)
        by_call = lambda call: failing if call.agent_name == agent else Scenario()  # noqa: E731

        exit_code, launch_stub = run_smoke(world, by_call=by_call)

        output = capsys.readouterr().out
        assert exit_code == 2 and f"missing ({reason})" in output
        assert sum(call.agent_name == agent for call in launch_stub.calls) == 1
        assert not smoke_pass_exists(world)

    @pytest.mark.parametrize("first_failure", [
        Scenario(no_output=True), Scenario(timed_out=True, cost=None),
        Scenario(raises=OSError("synthetic")),
    ], ids=["launch-no-output", "timeout", "launch-error"])
    def test_a_non_instrument_failure_then_a_clean_attempt_retries_once_and_the_calibration_runs(
        self, world, capsys, first_failure,
    ):
        seen = {"toolkit": 0}

        def by_call(call):
            if call.agent_name == "code-writer":
                seen["toolkit"] += 1
                return first_failure if seen["toolkit"] == 1 else Scenario()
            return Scenario()

        exit_code, launch_stub = run_smoke(world, by_call=by_call)

        assert exit_code == 0 and sum(call.agent_name == "code-writer" for call in launch_stub.calls) == 2
        assert "first-turn prompt sizes" in capsys.readouterr().out

    def test_a_timed_out_attempt_whose_log_carries_a_bare_rule_load_records_arm_leak_and_launches_no_retry(
        self, world, capsys,
    ):
        leaking = Scenario(timed_out=True, cost=None, log_lines=leaking_log_lines(world))

        exit_code, launch_stub = run_smoke(world, by_call=agent_scenarios(bare=leaking))

        assert exit_code == 2 and "missing (arm-leak)" in capsys.readouterr().out
        assert sum(call.agent_name == "bare-writer" for call in launch_stub.calls) == 1

    def test_launch_no_output_then_arm_leak_ends_missing_as_arm_leak_with_two_attempts(self, world, capsys):
        seen = {"bare": 0}

        def by_call(call):
            if call.agent_name == "bare-writer":
                seen["bare"] += 1
                return Scenario(no_output=True) if seen["bare"] == 1 else Scenario(log_lines=leaking_log_lines(world))
            return Scenario()

        exit_code, launch_stub = run_smoke(world, by_call=by_call)

        assert exit_code == 2 and "missing (arm-leak)" in capsys.readouterr().out
        assert sum(call.agent_name == "bare-writer" for call in launch_stub.calls) == 2
