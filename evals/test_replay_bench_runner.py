"""Tests for replay_bench's attempt and retry logic: each run is driven by a scripted launch that writes a
synthetic session under a `tmp_path` config dir. Real git builds the fixtures. No test launches `claude`.
"""
from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
import threading
import time
import types
from pathlib import Path

import measure_subagent_model_resolution as msmr
import pytest
import run_replay_bench
from replay_bench import account, arms, attempt, checks, diffing, smoke_pass, tasks
from replay_bench import runner as replay_runner
from replay_bench.runner import execute_run
from replay_bench.spend import SpendAppendError, SpendFile
from review_bench import fixture_repo
from review_bench import runner as review_runner
from test_replay_bench_checks_support import _line
from test_replay_bench_runner_support import (
    AGENT_ID,
    MODEL_ID,
    READING,
    Scenario,
    claude_md_line,
    leaking_log_lines,
    make_block,
    make_ignored_local_dir,
    make_settings,
    spend_lines,
    validity_of,
    write_executable,
)

ARM_NAMES = [arms.TOOLKIT.name, arms.BARE.name]
# The bench's own function, captured before the shared world fixture replaces it with a stub.
REAL_WORKTREE_TOP_LEVELS = review_runner._worktree_top_levels


def run(world, arm_name, scenario_for=None, live_checkout_roots=(), **settings_kwargs):
    launch_stub = world.launch(scenario_for)
    settings = make_settings(world, launch_stub, **settings_kwargs)
    block = make_block(world, live_checkout_roots=live_checkout_roots)
    return execute_run(settings, block, arm_name, 0), launch_stub, settings


def leftovers(world, launch_stub) -> list[Path]:
    """The fixtures and attempt dirs that survived the run, found by the directories themselves so a fixture
    built for an attempt that never launched is seen too."""
    survivors = [call.cwd for call in launch_stub.calls if call.cwd.exists()]
    survivors.extend(world.root.glob("tmp/review-bench-*"))
    attempts_root = make_settings(world, launch_stub).attempts_root
    survivors.extend(attempts_root.iterdir() if attempts_root.exists() else [])
    return survivors


class TestAValidRun:
    @pytest.mark.parametrize("arm_name", ARM_NAMES)
    def test_record_diff_metrics_spend_and_cleanup(self, world, arm_name):
        outcome, launch_stub, settings = run(world, arm_name)

        record = outcome.record
        assert (record.status, record.missing_reason, record.attempts) == ("ok", None, 1)
        assert "new_module.py" in outcome.diff_text
        assert record.metrics["line_counts"]["non_test"]["docstring"] == 1
        assert record.metrics["comment_words"]["non_test"] > 0
        assert record.first_turn_prompt_size > 0
        assert [line["cost_usd"] for line in spend_lines(settings)] == [0.5]
        assert leftovers(world, launch_stub) == []
        assert any((world.config_dir / "sessions").iterdir()), "the account's sessions/ file stays"
        assert not list((world.config_dir / "projects").glob("*/*.jsonl")), "the session store is deleted"

    @pytest.mark.parametrize("arm_name", ARM_NAMES)
    def test_launch_argv_prefix_permission_mode_settings_and_allowed_tools_last(self, world, arm_name):
        _outcome, launch_stub, _settings = run(world, arm_name)

        cmd = launch_stub.calls[0].cmd
        assert cmd[:3] == ["/usr/bin/env", "-i", "PATH=/usr/bin"]
        assert cmd[cmd.index("--permission-mode") + 1] == "acceptEdits"
        settings_json = json.loads(cmd[cmd.index("--settings") + 1])
        assert list(settings_json) == ["hooks"] and list(settings_json["hooks"]) == ["InstructionsLoaded"]
        tail = cmd[cmd.index("--allowedTools"):]
        assert tail[0] == "--allowedTools" and all(entry.startswith("Read(//") for entry in tail[1:])
        assert f"Read(/{world.config_dir / 'agents' / 'reviewer.md'})" in tail

    def test_the_read_allow_list_is_identical_for_both_arms(self, world):
        tails = []
        for arm_name in ARM_NAMES:
            _outcome, launch_stub, _settings = run(world, arm_name)
            cmd = launch_stub.calls[0].cmd
            tails.append(cmd[cmd.index("--allowedTools"):])
        assert tails[0] == tails[1]


class TestForgedLog:
    """The verdict derives from the attempt dir alone, so a log written into the fixture is never read."""

    @pytest.mark.parametrize("arm_name", ARM_NAMES)
    def test_a_real_log_without_the_dispatcher_claude_md_fails_despite_a_forged_good_one(self, world, arm_name):
        scenario = Scenario(
            log_lines=lambda session_id: [_line(str(world.config_dir / "agents" / "reviewer.md"), session_id=session_id)],
            forged_log_lines=lambda session_id: [claude_md_line(world, session_id)],
        )
        outcome, launch_stub, _settings = run(world, arm_name, lambda _n, _a: scenario)

        assert outcome.record.missing_reason == checks.REASON_INSTRUMENT_MISSING
        assert len(launch_stub.calls) == 1

    @pytest.mark.parametrize("arm_name", ARM_NAMES)
    def test_a_clean_real_log_passes_despite_a_poisoned_forged_one(self, world, arm_name):
        scenario = Scenario(forged_log_lines=lambda session_id: [
            _line(str(world.config_dir / "rules" / "python.md"), session_id=session_id, agent_id=AGENT_ID),
        ])
        outcome, _launch_stub, _settings = run(world, arm_name, lambda _n, _a: scenario)

        assert outcome.record.missing_reason is None


def tool_scenario(*tool_calls, **kwargs):
    return lambda _number, _agent: Scenario(tool_calls=list(tool_calls), **kwargs)


class TestTranscriptUnreadable:
    @pytest.mark.parametrize("damage", ["skip", "torn"])
    @pytest.mark.parametrize("arm_name", ARM_NAMES)
    def test_a_damaged_sidecar_that_passes_validity_fails_only_through_the_path_check(self, world, arm_name, damage):
        validity_seen = []
        scenario = Scenario(
            damage_sidecar=damage, after_write=lambda call: validity_seen.append(validity_of(world, call)),
        )

        outcome, launch_stub, _settings = run(world, arm_name, lambda _n, _a: scenario)

        assert [validity.ok for validity in validity_seen] == [True, True], "validity passes the damaged sidecar"
        assert outcome.record.missing_reason == review_runner.VALIDITY_FAIL_TRANSCRIPT_UNREADABLE
        assert len(launch_stub.calls) == 2, "a path reason is not an instrument reason, so it retries once"

    def test_an_unreadable_sidecar_is_a_control_because_validity_returns_the_same_reason(self, world, monkeypatch):
        def unreadable(_path):
            raise OSError("synthetic")

        monkeypatch.setattr(review_runner, "extract_read_like_calls", unreadable)
        outcome, _launch_stub, _settings = run(world, arms.BARE.name)
        assert outcome.record.missing_reason == review_runner.VALIDITY_FAIL_TRANSCRIPT_UNREADABLE


class TestPathReasonsReachTheRecord:
    @pytest.mark.parametrize(("arm_name", "relative_path"), [("toolkit", ""), ("toolkit", "projects"), ("bare", "projects")])
    def test_a_bash_command_naming_the_config_dir_fails_out_of_fixture_path_with_its_token(
        self, world, arm_name, relative_path,
    ):
        """For `bare`, a command on the config dir itself is a toolkit reach, which fails `arm-leak` first."""
        token = str(world.config_dir / relative_path) if relative_path else str(world.config_dir)
        command = f"ls {token}"
        outcome, launch_stub, _settings = run(world, arm_name, tool_scenario(("Bash", {"command": command}, False)))

        assert outcome.record.missing_reason == checks.REASON_OUT_OF_FIXTURE_PATH
        assert token in outcome.record.out_of_fixture_tokens
        assert token in outcome.record.missing_detail
        assert command in outcome.record.bash_commands
        assert len(launch_stub.calls) == 2

    def test_a_bash_command_naming_the_config_dir_in_a_bare_run_fails_arm_leak(self, world):
        outcome, launch_stub, _settings = run(
            world, arms.BARE.name, tool_scenario(("Bash", {"command": f"ls {world.config_dir}"}, False)),
        )

        assert outcome.record.missing_reason == checks.REASON_ARM_LEAK
        assert len(launch_stub.calls) == 1

    @pytest.mark.parametrize("arm_name", ARM_NAMES)
    def test_a_write_outside_the_fixture_fails_out_of_fixture_write(self, world, arm_name):
        target = str(world.root / "elsewhere.py")
        outcome, launch_stub, _settings = run(world, arm_name, tool_scenario(("Write", {"file_path": target}, False)))

        assert outcome.record.missing_reason == checks.REASON_OUT_OF_FIXTURE_WRITE
        assert outcome.record.out_of_fixture_writes == [target]
        assert target in outcome.record.missing_detail
        assert len(launch_stub.calls) == 2

    def test_a_denied_write_outside_the_fixture_reached_nothing_and_fails_nothing(self, world):
        target = str(world.root / "elsewhere.py")
        outcome, _launch_stub, _settings = run(world, arms.BARE.name, tool_scenario(("Write", {"file_path": target}, True)))

        assert outcome.record.missing_reason is None and outcome.record.permission_denials == 1

    def test_commands_shaped_like_the_probe_transcripts_leave_a_bare_run_clean(self, world):
        commands = [
            "cat <<'EOF'\nSee src/app.py and /usr/bin/env for details\nEOF",
            "python3 -c \"print('hello')\" && ls -la src",
            "git status --short",
            f"cd {world.root / 'nowhere'} || true",
        ]
        outcome, _launch_stub, _settings = run(
            world, arms.BARE.name, tool_scenario(*[("Bash", {"command": command}, False) for command in commands]),
        )

        assert outcome.record.missing_reason is None
        assert outcome.record.bash_commands == commands


class TestInstrumentReasons:
    @staticmethod
    def leaking_scenario(world, *, timed_out=False):
        return Scenario(timed_out=timed_out, log_lines=leaking_log_lines(world))

    def test_a_bare_arm_leak_launches_no_retry_and_ends_missing(self, world):
        outcome, launch_stub, _settings = run(world, arms.BARE.name, lambda _n, _a: self.leaking_scenario(world))

        assert outcome.record.missing_reason == checks.REASON_ARM_LEAK
        assert len(launch_stub.calls) == 1

    def test_an_instrument_reason_outranks_a_timeout(self, world):
        outcome, launch_stub, _settings = run(
            world, arms.BARE.name, lambda _n, _a: self.leaking_scenario(world, timed_out=True),
        )

        assert outcome.record.missing_reason == checks.REASON_ARM_LEAK
        assert len(launch_stub.calls) == 1

    def test_a_missing_account_session_file_is_account_hooks_missing_without_a_retry(self, world):
        outcome, launch_stub, _settings = run(
            world, arms.TOOLKIT.name, lambda _n, _a: Scenario(write_account_session_file=False),
        )

        assert outcome.record.missing_reason == checks.REASON_ACCOUNT_HOOKS_MISSING
        assert len(launch_stub.calls) == 1

    def test_a_timed_out_run_with_no_log_is_a_timeout_not_an_instrument_reason(self, world):
        outcome, launch_stub, _settings = run(
            world, arms.TOOLKIT.name, lambda _n, _a: Scenario(timed_out=True, log_lines=[], write_account_session_file=False),
        )

        assert outcome.record.missing_reason == review_runner.MISSING_REASON_TIMEOUT
        assert len(launch_stub.calls) == 2


class TestRetryThenMissing:
    def test_two_failed_attempts_record_missing_with_summed_cost_and_leave_nothing_behind(self, world):
        outcome, launch_stub, settings = run(world, arms.BARE.name, lambda _n, _a: Scenario(observed_model="other-model"))

        record = outcome.record
        assert (record.status, record.missing_reason, record.attempts) == ("missing", "model-mismatch", 2)
        assert record.total_cost_usd == 1.0
        assert len(spend_lines(settings)) == 2
        assert leftovers(world, launch_stub) == []

    def test_a_fail_once_then_succeed_run_takes_its_diff_from_the_second_fixture(self, world):
        def scenario_for(number, _agent):
            return Scenario(observed_model="other-model") if number == 0 else Scenario()

        outcome, launch_stub, _settings = run(world, arms.TOOLKIT.name, scenario_for)

        assert outcome.record.status == "ok" and outcome.record.attempts == 2
        assert launch_stub.calls[0].cwd != launch_stub.calls[1].cwd
        assert leftovers(world, launch_stub) == []

    def test_a_timeout_and_a_launch_exception_are_each_charged_at_the_cap(self, world):
        outcome, _launch_stub, settings = run(
            world, arms.TOOLKIT.name,
            lambda number, _a: Scenario(timed_out=True, cost=None) if number == 0 else Scenario(raises=OSError("synthetic")),
            budget_cap_usd=7.0,
        )

        assert [(line["cost_usd"], line["is_cap_charge"]) for line in spend_lines(settings)] == [(7.0, True), (7.0, True)]
        assert outcome.record.missing_reason == attempt.REASON_LAUNCH_ERROR
        assert outcome.record.cap_charged_attempts == 2

    def test_an_empty_stdout_records_launch_no_output(self, world):
        outcome, launch_stub, _settings = run(world, arms.TOOLKIT.name, lambda _n, _a: Scenario(no_output=True))

        assert outcome.record.missing_reason == attempt.REASON_LAUNCH_NO_OUTPUT and len(launch_stub.calls) == 2

    def test_an_analysis_exception_after_a_launch_records_analysis_error_with_the_cost_and_no_second_launch(
        self, world, monkeypatch,
    ):
        def failing_capture(*_args, **_kwargs):
            raise ValueError("synthetic")

        monkeypatch.setattr(diffing, "capture_change", failing_capture)
        outcome, launch_stub, settings = run(world, arms.BARE.name)

        assert outcome.record.missing_reason == attempt.REASON_ANALYSIS_ERROR and len(launch_stub.calls) == 1
        assert outcome.record.total_cost_usd == 0.5 and len(spend_lines(settings)) == 1
        assert leftovers(world, launch_stub) == []

    def test_a_prune_that_raises_records_fixture_error_with_no_launch(self, world, monkeypatch):
        def failing_prune(_dest_dir):
            raise RuntimeError("synthetic prune failure")

        monkeypatch.setattr(tasks, "prune_instruction_files", failing_prune)
        outcome, launch_stub, settings = run(world, arms.BARE.name)

        assert outcome.record.missing_reason == attempt.REASON_FIXTURE_ERROR and launch_stub.calls == []
        assert spend_lines(settings) == []
        assert leftovers(world, launch_stub) == []

    def test_an_extraction_refusal_after_members_reached_disk_leaves_no_fixture_or_attempt_dir(self, world, monkeypatch):
        def refusing_extraction(_source, _commit, dest_dir, **_kwargs):
            (dest_dir / "partial.txt").write_text("written before the refusal")
            raise fixture_repo.UnsafeFixtureConfigError("refused member")

        monkeypatch.setattr(fixture_repo, "_extract_commit_tree", refusing_extraction)
        outcome, launch_stub, _settings = run(world, arms.TOOLKIT.name)

        assert outcome.record.missing_reason == attempt.REASON_FIXTURE_ERROR and launch_stub.calls == []
        assert "refused member" in outcome.record.missing_detail
        assert leftovers(world, launch_stub) == []
        assert not list(world.root.glob("tmp/review-bench-*"))

    @pytest.mark.parametrize("failing_step", ["build", "capture"])
    def test_a_git_timeout_names_the_limit_and_the_flag(self, world, monkeypatch, failing_step):
        def timing_out(*_args, **_kwargs):
            raise subprocess.TimeoutExpired("git", 1)

        if failing_step == "build":
            monkeypatch.setattr(tasks, "build_task_fixture", timing_out)
        else:
            monkeypatch.setattr(diffing, "capture_change", timing_out)
        outcome, _launch_stub, _settings = run(world, arms.TOOLKIT.name, fixture_git_timeout_s=37.5)

        expected_reason = attempt.REASON_FIXTURE_ERROR if failing_step == "build" else attempt.REASON_ANALYSIS_ERROR
        assert outcome.record.missing_reason == expected_reason
        assert outcome.record.missing_detail == "git timed out after 37.5 s; raise --fixture-git-timeout-s"

    def test_a_generator_refusal_records_fixture_error_with_the_path_and_no_launch_or_spend(self, world):
        offending = world.config_dir / "skills" / "we(ird)"
        offending.mkdir()
        (offending / "SKILL.md").write_text("skill\n")
        outcome, launch_stub, settings = run(world, arms.TOOLKIT.name)

        assert outcome.record.missing_reason == attempt.REASON_FIXTURE_ERROR and launch_stub.calls == []
        assert "we(ird)" in outcome.record.missing_detail
        assert spend_lines(settings) == []
        assert len(launch_stub.calls) == 0, "fixture-error never retries"

    def test_an_attempt_dir_that_already_exists_records_fixture_error_and_launches_nothing(self, world):
        settings = make_settings(world, world.launch())
        existing = settings.attempts_root / f"{world.task.id}-bare-0-1"
        existing.mkdir(parents=True)
        outcome = execute_run(settings, make_block(world), arms.BARE.name, 0)

        assert outcome.record.missing_reason == attempt.REASON_FIXTURE_ERROR
        assert str(existing) in outcome.record.missing_detail
        assert existing.exists(), "an attempt dir the attempt did not create is not deleted"


class TestEveryPostRunGitCallCarriesTheTimeout:
    def test_the_five_post_run_git_calls_carry_the_configured_limit(self, world, monkeypatch):
        calls = []
        real_run = subprocess.run

        def spy(command, *args, **kwargs):
            if any(str(part).startswith("--git-dir=") for part in command):
                calls.append((command, kwargs.get("timeout")))
            return real_run(command, *args, **kwargs)

        monkeypatch.setattr(subprocess, "run", spy)
        run(world, arms.BARE.name, fixture_git_timeout_s=37.5)

        subcommands = [next(part for part in command if part in {"add", "diff", "cat-file", "ls-files"}) for command, _t in calls]
        assert sorted(subcommands) == ["add", "cat-file", "diff", "diff", "ls-files"]
        assert {limit for _command, limit in calls} == {37.5}


class TestTheFixtureTheAgentSees:
    """A scripted launch cannot see the fixture unless the test looks, so the launch call records it."""

    @pytest.mark.parametrize("arm_name", ARM_NAMES)
    def test_both_arms_get_bare_writer_and_the_opt_out_and_only_bare_gets_the_exclude_settings(self, world, arm_name):
        _outcome, launch_stub, _settings = run(world, arm_name)

        call = launch_stub.calls[0]
        assert call.fixture_files[".claude/agents/bare-writer.md"] == world.account.bare_writer_markdown
        assert call.fixture_files[".claude/worktree-optout"] == ""
        settings_text = call.fixture_files[".claude/settings.json"]
        if arm_name == arms.BARE.name:
            excludes = arms.claude_md_excludes(world.config_dir)
            assert excludes and json.loads(settings_text) == {"claudeMdExcludes": excludes}
        else:
            assert settings_text is None
        assert call.fixture_status == "", "none of the written files shows in the work tree's status"


class TestGuardedLocationsThroughAnAttempt:
    def bash(self, command):
        return tool_scenario(("Bash", {"command": command}, False))

    def test_a_command_naming_a_path_under_the_local_dir_fails_out_of_fixture_path(self, world):
        token = str(world.local_dir / "campaigns" / "other" / "runs" / "t" / "toolkit-0.diff")

        outcome, _stub, _settings = run(world, arms.BARE.name, self.bash(f"cat {token}"))

        assert outcome.record.missing_reason == checks.REASON_OUT_OF_FIXTURE_PATH
        assert token in outcome.record.out_of_fixture_tokens

    def test_commands_naming_a_live_checkouts_git_dir_or_changed_file_fail_and_an_unrelated_file_passes(self, world):
        live = world.root / "live-checkout"
        (live / "src").mkdir(parents=True)
        changed_path = world.task.changed_paths[0]
        for token in (str(live / ".git"), str(live / changed_path)):
            outcome, _stub, _settings = run(
                world, arms.BARE.name, self.bash(f"cat {token}"), live_checkout_roots=(live,),
            )
            assert outcome.record.missing_reason == checks.REASON_OUT_OF_FIXTURE_PATH, token
            assert token in outcome.record.out_of_fixture_tokens

        unrelated = str(live / "src" / "unrelated.py")
        outcome, _stub, _settings = run(
            world, arms.BARE.name, self.bash(f"cat {unrelated}"), live_checkout_roots=(live,),
        )
        assert outcome.record.missing_reason is None
        assert unrelated in outcome.record.other_outside_fixture_tokens

    def test_the_runs_own_session_files_are_exempt_and_a_sibling_sessions_files_are_not(self, world):
        projects = world.config_dir / "projects" / "synthetic-project"

        own, _stub, _settings = run(
            world, arms.BARE.name, lambda _n, _a: Scenario(
                tool_calls=lambda call: [("Bash", {"command": f"cat {projects / call.session_id}.jsonl"}, False)],
            ),
        )
        sibling_path = str(projects / "another-session.jsonl")
        sibling, _stub, _settings = run(world, arms.BARE.name, self.bash(f"cat {sibling_path}"))

        assert own.record.missing_reason is None
        assert sibling.record.missing_reason == checks.REASON_OUT_OF_FIXTURE_PATH
        assert sibling_path in sibling.record.out_of_fixture_tokens

    def test_the_default_live_roots_join_the_source_repos_worktrees_and_dedupe(self, world, monkeypatch):
        first, second = world.root / "live-a", world.root / "live-b"
        monkeypatch.setattr(review_runner, "default_live_checkout_roots", lambda: (first, second))
        monkeypatch.setattr(review_runner, "_worktree_top_levels", lambda checkout: (second, world.root / "live-c"))

        assert replay_runner.live_checkout_roots_for(world.task) == (first, second, world.root / "live-c")

    def test_the_environment_checkout_root_is_the_parent_of_the_resolved_claude_md(self, world):
        stowed = world.root / "stowed-checkout" / "claude"
        stowed.mkdir(parents=True)
        (stowed / "CLAUDE.md").write_text("claude md")
        (world.config_dir / "CLAUDE.md").unlink()
        (world.config_dir / "CLAUDE.md").symlink_to(stowed / "CLAUDE.md")

        assert replay_runner.checkout_root_for(world.account) == stowed.resolve()


class TestTheRecordHoldsWhatTheCheckSaw:
    def test_unflagged_absolute_tokens_unparsed_commands_and_out_of_session_reads_reach_the_record(self, world):
        elsewhere = str(world.root / "elsewhere")
        outside_read = str(world.root / "somewhere" / "notes.txt")
        outcome, _stub, _settings = run(world, arms.BARE.name, tool_scenario(
            ("Bash", {"command": f"ls {elsewhere}"}, False),
            ("Bash", {"command": "echo 'unterminated"}, False),
            ("Read", {"file_path": outside_read}, False),
        ))

        record = outcome.record
        assert record.missing_reason is None
        assert elsewhere in record.other_outside_fixture_tokens
        assert any("cannot be split" in entry for entry in record.unparsed)
        assert outside_read in record.out_of_session_paths


class TestSpendFigures:
    @pytest.mark.parametrize("bad_cost", [float("nan"), float("inf"), -1.0], ids=["nan", "inf", "negative"])
    def test_a_non_finite_or_negative_cost_is_charged_at_the_cap(self, world, bad_cost):
        outcome, _stub, settings = run(world, arms.BARE.name, lambda _n, _a: Scenario(cost=bad_cost), budget_cap_usd=7.0)

        assert [(line["cost_usd"], line["is_cap_charge"]) for line in spend_lines(settings)] == [(7.0, True)]
        assert outcome.record.total_cost_usd == 7.0 and outcome.record.cap_charged_attempts == 1

    def test_a_finite_cost_is_charged_as_reported(self, world):
        _outcome, _stub, settings = run(world, arms.BARE.name, lambda _n, _a: Scenario(cost=0.25))

        assert [(line["cost_usd"], line["is_cap_charge"]) for line in spend_lines(settings)] == [(0.25, False)]


class TestAFailingWorkerStopsItsSiblings:
    @pytest.fixture(autouse=True)
    def stub_environment(self, monkeypatch):
        monkeypatch.setattr(review_runner, "read_environment_record", lambda *, checkout_root=None: READING)

    def test_a_failed_spend_append_starts_no_further_launch_and_names_the_uncharged_one(self, world, monkeypatch):
        def failing_append(self, line):
            raise SpendAppendError("disk full")

        monkeypatch.setattr(SpendFile, "append", failing_append)
        launch_stub = world.launch()
        settings = make_settings(world, launch_stub)

        with pytest.raises(SpendAppendError):
            replay_runner.run_block(
                settings, world.task, world.account, ("toolkit", "bare"), 2, 0, review_runner.EnvironmentReference(),
                workers=1,
            )

        assert len(launch_stub.calls) == 1, "the other three runs never launched"
        assert settings.uncharged_launches == [(world.task.id, launch_stub.calls[0].session_id)]
        assert leftovers(world, launch_stub) == []

    def test_an_unexpected_exception_in_a_worker_starts_no_further_launch(self, world):
        launch_stub = world.launch(lambda _n, _a: Scenario(raises=RuntimeError("synthetic")))
        settings = make_settings(world, launch_stub)

        with pytest.raises(RuntimeError, match="synthetic"):
            replay_runner.run_block(
                settings, world.task, world.account, ("toolkit", "bare"), 2, 0, review_runner.EnvironmentReference(),
                workers=1,
            )

        assert len(launch_stub.calls) == 1
        assert [(line["cost_usd"], line["is_cap_charge"]) for line in spend_lines(settings)] == [(10.0, True)]

    def test_an_interrupt_stops_a_run_still_building_its_fixture_before_it_launches_or_is_charged(
        self, world, monkeypatch,
    ):
        """Kills: the interrupt arm calling `abort_launches()` alone, which leaves `settings.stop` unset so the
        pre-launch check lets the run reach `settings.launch` and be cap-charged for a child that never spawned."""
        in_fixture_build = threading.Event()
        released_by = {}

        def held_build(*_args, **_kwargs):
            in_fixture_build.set()
            released_by["stop"] = settings.stop.wait(timeout=5)
            return "0" * 40

        def interrupting_runs():
            yield ("toolkit", 0)
            assert in_fixture_build.wait(timeout=10), "the first run never reached its fixture build"
            raise KeyboardInterrupt

        monkeypatch.setattr(tasks, "build_task_fixture", held_build)
        monkeypatch.setattr(
            review_runner, "build_block_plan", lambda *a, **k: types.SimpleNamespace(ordered_runs=interrupting_runs()),
        )
        launch_stub = world.launch()
        settings = make_settings(world, launch_stub)

        with pytest.raises(KeyboardInterrupt):
            replay_runner.run_block(
                settings, world.task, world.account, ("toolkit", "bare"), 1, 0, review_runner.EnvironmentReference(),
                workers=2,
            )

        assert released_by == {"stop": True}
        assert launch_stub.calls == [] and spend_lines(settings) == []

    def test_an_interrupt_aborts_a_launch_already_in_flight_and_charges_it_once(self, world, monkeypatch):
        """Kills: reducing `_stop_further_launches` on the interrupt arm to `settings.stop.set()`, which leaves the
        in-flight paid session running until its own timeout."""
        in_flight = threading.Event()
        outcome = {}

        def blocking_launch(_cmd, _cwd, _timeout_s):
            in_flight.set()
            outcome["released_by_abort"] = msmr._launches_aborted.wait(timeout=5)
            raise msmr.LaunchAbortedError("aborted while the child was running")

        def interrupting_runs():
            yield ("toolkit", 0)
            assert in_flight.wait(timeout=10), "the first run never reached its launch"
            raise KeyboardInterrupt

        monkeypatch.setattr(
            review_runner, "build_block_plan", lambda *a, **k: types.SimpleNamespace(ordered_runs=interrupting_runs()),
        )
        settings = make_settings(world, blocking_launch)

        with pytest.raises(KeyboardInterrupt):
            replay_runner.run_block(
                settings, world.task, world.account, ("toolkit", "bare"), 1, 0, review_runner.EnvironmentReference(),
                workers=2,
            )

        assert outcome == {"released_by_abort": True}
        assert [line["is_cap_charge"] for line in spend_lines(settings)] == [True]

    def test_a_sibling_failing_at_launch_aborts_a_run_held_in_its_fixture_build_and_the_root_cause_surfaces(
        self, world, monkeypatch,
    ):
        """Kills: `abort_launches()` missing from the failing worker (the wait times out), the pre-launch recheck
        missing (the held run launches), and the root-cause remap missing (LaunchAbortedError surfaces)."""
        first_arm, _index = review_runner.build_block_plan(world.task.id, ("toolkit", "bare"), 1, 0).ordered_runs[0]
        real_build = tasks.build_task_fixture
        released_by = {}

        def build(task, dest_dir, baseline_git_dir, **kwargs):
            if f"-{first_arm}-" not in baseline_git_dir.parent.name:
                return real_build(task, dest_dir, baseline_git_dir, **kwargs)
            released_by["abort"] = msmr._launches_aborted.wait(timeout=10)
            return "0" * 40

        monkeypatch.setattr(tasks, "build_task_fixture", build)
        launch_stub = world.launch(by_call=lambda call: Scenario(raises=SpendAppendError("disk full")))
        settings = make_settings(world, launch_stub)

        with pytest.raises(SpendAppendError):
            replay_runner.run_block(
                settings, world.task, world.account, ("toolkit", "bare"), 1, 0, review_runner.EnvironmentReference(),
                workers=2,
            )

        assert released_by == {"abort": True}
        assert [call.agent_name for call in launch_stub.calls] != [arms.ARMS[first_arm].agent_name]
        assert len(launch_stub.calls) == 1, "only the run that failed launched; the held run made no launch call"

    def test_run_block_hands_the_live_checkout_roots_to_the_path_check(self, world, monkeypatch):
        """Kills: `BlockContext(task, account, environment, ())` in `run_block`, which guards no live checkout."""
        live = world.root / "live-checkout"
        live.mkdir()
        monkeypatch.setattr(review_runner, "default_live_checkout_roots", lambda: (live,))
        monkeypatch.setattr(review_runner, "_worktree_top_levels", lambda checkout: ())
        reading_the_checkouts_git_dir = Scenario(tool_calls=[("Bash", {"command": f"cat {live / '.git'}"}, False)])
        launch_stub = world.launch(
            by_call=lambda call: reading_the_checkouts_git_dir if call.agent_name == "bare-writer" else Scenario(),
        )
        settings = make_settings(world, launch_stub)

        block = replay_runner.run_block(
            settings, world.task, world.account, ("toolkit", "bare"), 1, 0, review_runner.EnvironmentReference(), workers=1,
        )

        bare_record = block.outcomes_for("bare")[0].record
        assert bare_record.missing_reason == checks.REASON_OUT_OF_FIXTURE_PATH
        assert str(live / ".git") in bare_record.out_of_fixture_tokens
        assert block.outcomes_for("toolkit")[0].record.missing_reason is None


class TestAWorktreeListFailure:
    def test_the_exit_status_is_reported_and_the_childs_stderr_is_not(self, world, tmp_path, monkeypatch):
        monkeypatch.setattr(review_runner, "_worktree_top_levels", REAL_WORKTREE_TOP_LEVELS)
        write_executable(tmp_path / "bin" / "git", 'echo "remote http://user:sentinel-credential@proxy.invalid" >&2\nexit 4\n')
        monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:{os.environ['PATH']}")

        with pytest.raises(review_runner.HarnessInvalidatedError) as raised:
            replay_runner.live_checkout_roots_for(world.task)

        assert "exit 4" in str(raised.value) and "sentinel-credential" not in str(raised.value)


# --- CLI ---------------------------------------------------------------------------

REAL_DEFAULT_LOCAL_DIR = run_replay_bench.DEFAULT_LOCAL_DIR
TASKS_ARGV = ("tasks", "--source-repo", "/x", "--trusted-author", "a@b.c", "--name", "n")


def main_run(*argv: str) -> int:
    return run_replay_bench.main(list(argv))


class TestTheCommandLine:
    """The only tests that call `main()`, which sets process-wide signal handlers and the umask."""

    @pytest.fixture(autouse=True)
    def local_dir(self, tmp_path, monkeypatch):
        directory = make_ignored_local_dir(tmp_path)
        monkeypatch.setattr(run_replay_bench, "DEFAULT_LOCAL_DIR", directory)
        monkeypatch.setattr(review_runner, "default_live_checkout_roots", lambda: ())
        return directory

    def test_run_without_its_required_flags_exits_two(self):
        assert main_run("run", "--tasks", "x", "--cumulative-stop-usd", "10") == 2
        assert main_run("run", "--tasks", "x", "--k", "1") == 2

    def test_smoke_takes_no_k(self):
        assert main_run("smoke", "--tasks", "x", "--task-id", "00000000-000000000000", "--k", "2") == 2

    def test_a_task_id_breaking_its_grammar_exits_two_with_the_id_in_the_message(self, capsys):
        assert main_run("smoke", "--tasks", "x", "--task-id", "not-an-id") == 2
        assert "not-an-id" in capsys.readouterr().err

    def test_a_zero_selection_limit_exits_two_naming_the_flag_and_never_mines(self, monkeypatch, capsys):
        monkeypatch.setattr(tasks, "mine_tasks", lambda *a, **k: pytest.fail("mine_tasks must not run"))

        code = main_run(*TASKS_ARGV, "--max-count", "0")

        assert code == 2 and "--max-count" in capsys.readouterr().err

    def test_a_run_whose_task_file_is_malformed_exits_two_naming_the_file(self, local_dir, capsys):
        (local_dir / "tasks").mkdir(parents=True)
        (local_dir / "tasks" / "broken.json").write_text("{not json")

        assert main_run("run", "--tasks", "broken", "--k", "1", "--cumulative-stop-usd", "10") == 2
        assert "broken.json" in capsys.readouterr().err

    def test_a_task_set_name_breaking_its_grammar_exits_two(self, capsys):
        assert main_run("tasks", "--source-repo", "/x", "--trusted-author", "a@b.c", "--name", "../escape") == 2
        assert "invalid campaign ID" in capsys.readouterr().err

    def test_tasks_with_no_limit_flag_passes_the_default_limits(self, monkeypatch):
        captured = {}

        def fake_mine(_repos, _authors, limits, **_kwargs):
            captured["limits"] = limits
            return tasks.MiningReport()

        monkeypatch.setattr(tasks, "mine_tasks", fake_mine)

        assert main_run(*TASKS_ARGV) == 0
        assert captured["limits"] == tasks.DEFAULT_LIMITS

    def test_tasks_given_all_nine_limit_flags_lands_each_value_in_its_own_field(self, monkeypatch):
        captured = {}

        def fake_mine(_repos, _authors, limits, **_kwargs):
            captured["limits"] = limits
            return tasks.MiningReport()

        monkeypatch.setattr(tasks, "mine_tasks", fake_mine)
        values = {
            "rev": "release", "max_count": 11, "max_tasks": 12, "max_files": 13, "min_code_lines": 14, "max_code_lines": 15,
            "max_prompt_chars": 16, "max_base_mb": 17, "max_base_entries": 18,
        }
        flags = [part for name, value in values.items() for part in ("--" + name.replace("_", "-"), str(value))]

        assert main_run(*TASKS_ARGV, *flags) == 0
        assert captured["limits"] == tasks.SelectionLimits(**values)

    @pytest.mark.parametrize("kind", ["relative", "symlink", "name", "unignored"])
    def test_a_refused_local_dir_exits_two_with_the_target_still_absent(self, tmp_path, kind):
        real_parent = tmp_path / "real-parent"
        real_parent.mkdir()
        target = {
            "relative": Path("relative-parent") / ".local",
            "symlink": tmp_path / "link-parent" / ".local",
            "name": make_ignored_local_dir(tmp_path / "other").parent / "elsewhere",
            "unignored": real_parent / ".local",
        }[kind]
        if kind == "symlink":
            (tmp_path / "link-parent").symlink_to(real_parent)
        if kind == "name":
            target.parent.mkdir(parents=True, exist_ok=True)

        assert main_run("--local-dir", str(target), "summarize", "--campaign-id", "c1") == 2
        assert not (real_parent / ".local").exists() and not target.exists()

    def test_the_default_local_dir_is_ignored_by_this_checkout(self):
        run_replay_bench.check_local_dir(REAL_DEFAULT_LOCAL_DIR)

    def test_a_pre_existing_loose_local_dir_is_restricted_to_owner_only(self, local_dir):
        local_dir.mkdir(parents=True)
        local_dir.chmod(0o755)

        main_run(*TASKS_ARGV, "--max-count", "0")

        assert stat.S_IMODE(local_dir.stat().st_mode) == 0o700

    def test_the_local_dir_main_creates_is_owner_only_and_a_file_the_runner_writes_in_it_is_0600(
        self, world, local_dir, monkeypatch,
    ):
        monkeypatch.setattr(tasks, "mine_tasks", lambda *a, **k: tasks.MiningReport(tasks=list(world.tasks)))

        assert main_run(*TASKS_ARGV) == 0

        assert stat.S_IMODE(local_dir.stat().st_mode) == 0o700
        assert stat.S_IMODE((local_dir / "tasks" / "n.json").stat().st_mode) == 0o600

    def test_python_below_3_12_exits_two_before_any_preflight(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, "version_info", (3, 11, 0, "final", 0))
        monkeypatch.setattr(account, "preflight_repo", lambda *a, **k: pytest.fail("preflight must not run"))

        assert main_run("preflight", "--tasks", "x") == 2
        assert "Python 3.12" in capsys.readouterr().err

    def test_git_below_2_32_exits_two_before_any_preflight(self, tmp_path, monkeypatch, capsys):
        fake_bin = tmp_path / "fakebin"
        write_executable(fake_bin / "git", 'echo "git version 2.31.0"\n')
        monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ['PATH']}")

        assert main_run("preflight", "--tasks", "x") == 2
        assert "git 2.32" in capsys.readouterr().err

    def test_an_interrupt_aborts_launches_and_prints_the_resume_command_with_its_wait_and_tmpdir_line(
        self, monkeypatch, capsys,
    ):
        aborted = []
        monkeypatch.setattr(msmr, "abort_launches", lambda: aborted.append(True))

        def interrupted(*_args, **_kwargs):
            raise KeyboardInterrupt

        monkeypatch.setattr(tasks, "mine_tasks", interrupted)

        code = main_run(*TASKS_ARGV)

        err = capsys.readouterr().err
        assert code == 130 and aborted == [True]
        assert "wait at least --timeout-s" in err and "same TMPDIR" in err

    def test_a_whole_run_through_main_completes_and_summarize_reads_it_back(self, world, local_dir, monkeypatch, capsys):
        monkeypatch.setattr(review_runner, "read_environment_record", lambda *, checkout_root=None: READING)
        monkeypatch.setattr(account, "preflight_repo", lambda repo, **_k: world.account)
        smoke_pass.write_smoke_pass(local_dir, world.account.key, READING, MODEL_ID, ("toolkit", "bare"))
        tasks.write_task_set(local_dir / "tasks", "set", world.tasks)
        launch_stub = world.launch()
        monkeypatch.setattr(msmr, "_run_claude_to_completion", launch_stub)

        code = main_run(
            "run", "--tasks", "set", "--k", "1", "--cumulative-stop-usd", "1000", "--campaign-id", "whole",
            "--budget-cap-usd", "1.0",
        )

        assert code == 0 and len(launch_stub.calls) == 2
        assert main_run("summarize", "--campaign-id", "whole", "--budget-cap-usd", "1.0") == 0
        assert "paired 1 of 1 complete tasks" in capsys.readouterr().out


    @pytest.fixture
    def run_files(self, world, local_dir):
        """A world with a smoke pass and a task set on disk, and no stub."""
        smoke_pass.write_smoke_pass(local_dir, world.account.key, READING, MODEL_ID, ("toolkit", "bare"))
        tasks.write_task_set(local_dir / "tasks", "set", world.tasks)
        return world

    @pytest.fixture
    def run_ready(self, run_files, monkeypatch):
        """`run_files` with the environment reading and the preflight stubbed, so `run` can start."""
        monkeypatch.setattr(review_runner, "read_environment_record", lambda *, checkout_root=None: READING)
        monkeypatch.setattr(account, "preflight_repo", lambda repo, **_k: run_files.account)
        return run_files

    RUN_ARGV = ("run", "--tasks", "set", "--k", "1", "--cumulative-stop-usd", "1000", "--budget-cap-usd", "1.0")

    def test_an_interrupted_run_without_a_campaign_id_prints_the_generated_id_in_its_resume_command(
        self, run_ready, monkeypatch, capsys,
    ):
        interrupting = run_ready.launch(lambda _n, _a: Scenario(raises=KeyboardInterrupt()))
        monkeypatch.setattr(msmr, "_run_claude_to_completion", interrupting)

        code = main_run(*self.RUN_ARGV)

        printed = capsys.readouterr()
        generated = re.search(r"^campaign (run-[0-9a-f]{8})$", printed.out, flags=re.M)[1]
        assert code == 130
        assert f"resume with --campaign-id {generated}" in printed.err

    def test_a_duplicate_arm_is_refused_with_exit_two(self, run_ready, capsys):
        assert main_run(*self.RUN_ARGV, "--arm", "bare", "--arm", "bare") == 2
        assert "more than once" in capsys.readouterr().err

    @pytest.mark.timing
    def test_direnv_timeout_s_reaches_the_probe_and_the_refusal_names_the_flag(self, run_files, tmp_path, monkeypatch, capsys):
        fake_direnv = write_executable(tmp_path / "bin" / "direnv", "exec sleep 10\n")
        monkeypatch.setenv("PATH", f"{fake_direnv.parent}:{os.environ['PATH']}")
        started = time.monotonic()

        code = main_run("--local-dir", str(run_files.local_dir), "preflight", "--tasks", "set", "--direnv-timeout-s", "1")

        assert code == 2 and "--direnv-timeout-s" in capsys.readouterr().err
        assert time.monotonic() - started < 5

    def test_fixture_git_timeout_s_reaches_every_post_run_git_call_and_defaults_to_the_runner_constant(
        self, run_ready, monkeypatch,
    ):
        parsed = run_replay_bench.build_parser().parse_args(["summarize", "--campaign-id", "x"])
        assert parsed.fixture_git_timeout_s == replay_runner.FIXTURE_GIT_TIMEOUT_S == 110
        monkeypatch.setattr(msmr, "_run_claude_to_completion", run_ready.launch())
        limits = []
        real_run = subprocess.run

        def spy(command, *args, **kwargs):
            if any(str(part).startswith("--git-dir=") for part in command):
                limits.append(kwargs.get("timeout"))
            return real_run(command, *args, **kwargs)

        monkeypatch.setattr(subprocess, "run", spy)

        assert main_run("--fixture-git-timeout-s", "37.5", *self.RUN_ARGV, "--campaign-id", "limits") == 0
        assert len(limits) == 10, "five post-run git calls for each of the two runs"
        assert set(limits) == {37.5}

    def test_a_failing_environment_read_prints_the_exit_status_and_never_the_childs_stderr(
        self, run_files, tmp_path, monkeypatch, capsys,
    ):
        # The real `read_environment_record` runs, with a `claude` that fails. Only the preflight is stubbed.
        write_executable(tmp_path / "bin" / "claude", 'echo "proxy http://user:sentinel-credential@proxy.invalid" >&2\nexit 3\n')
        monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:{os.environ['PATH']}")
        monkeypatch.setattr(account, "preflight_repo", lambda repo, **_k: run_files.account)

        code = main_run("--local-dir", str(run_files.local_dir), *self.RUN_ARGV)

        err = capsys.readouterr().err
        assert code == 2 and "exit 3" in err and "sentinel-credential" not in err

    def test_argparses_own_help_and_error_output_is_not_escaped_even_with_colour_forced(self, monkeypatch, capsys):
        """Kills: parsing inside the wrapper. On Python 3.14 argparse colours its output when `FORCE_COLOR` is set,
        and the wrapper would turn each escape into literal `\\x1b` text."""
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.delenv("PYTHON_COLORS", raising=False)
        monkeypatch.setenv("FORCE_COLOR", "1")

        help_code = main_run("--help")
        help_output = capsys.readouterr()
        error_code = main_run("--no-such-flag")
        error_output = capsys.readouterr()

        assert help_code == 0 and error_code == 2
        if sys.version_info >= (3, 14):
            # Colour is on, so a raw escape byte must reach the output; a colour-off run would pass vacuously.
            assert "\x1b" in help_output.out
        literal_escape = "\\x1b"
        assert literal_escape not in help_output.out + help_output.err + error_output.out + error_output.err
        assert "usage:" in help_output.out and "usage:" in error_output.err

    def test_hostile_text_printed_to_stdout_reaches_it_escaped_with_its_line_breaks_kept(self, monkeypatch, capsys):
        """Kills: deleting `redirect_stdout`, and flipping `keep_line_breaks` to False."""
        monkeypatch.setattr(tasks, "mine_tasks", lambda *a, **k: tasks.MiningReport())
        monkeypatch.setattr(
            tasks, "format_mining_summary", lambda report: ["first \x1b]0;forged title\x07 line", "second\rline"],
        )

        assert main_run(*TASKS_ARGV) == 0

        out = capsys.readouterr().out
        assert not {"\x1b", "\x07", "\r"} & set(out)
        assert "first \\x1b]0;forged title\\x07 line\nsecond\\rline\n" in out

    @pytest.mark.parametrize("flag", ["--cumulative-stop-usd", "--account-stop-usd"])
    @pytest.mark.parametrize("value", ["nan", "inf"])
    def test_a_non_finite_stop_is_refused_with_exit_two_before_any_launch(self, run_ready, monkeypatch, flag, value):
        launch_stub = run_ready.launch()
        monkeypatch.setattr(msmr, "_run_claude_to_completion", launch_stub)
        stops = {"--cumulative-stop-usd": "1000", "--account-stop-usd": "1000", flag: value}
        stop_flags = [part for item in stops.items() for part in item]
        argv = ["run", "--tasks", "set", "--k", "1", "--budget-cap-usd", "1.0", *stop_flags]

        assert main_run(*argv) == 2
        assert launch_stub.calls == []

    def test_a_refusal_carrying_an_escape_an_osc_sequence_and_a_bell_reaches_stderr_with_none_of_them_raw(
        self, run_ready, monkeypatch, capsys,
    ):
        hostile = "refused \x1b[31mred\x1b]0;forged title\x07 and\rcarriage"

        def refusing_preflight(_repo, **_kwargs):
            raise review_runner.HarnessInvalidatedError(hostile)

        monkeypatch.setattr(account, "preflight_repo", refusing_preflight)

        code = main_run("preflight", "--tasks", "set")

        err = capsys.readouterr().err
        assert code == 2
        assert not {"\x1b", "\x07", "\r"} & set(err)
        assert "refused \\x1b[31mred\\x1b]0;forged title\\x07 and\\rcarriage" in err
