"""Tests for replay_bench's attempt and retry logic and for the `run_replay_bench` command line. Real git builds
the fixtures.
"""
from __future__ import annotations

import argparse
import dataclasses
import errno
import inspect
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import threading
import time
import types
import uuid
from pathlib import Path

import measure_subagent_model_resolution as msmr
import pytest
import run_replay_bench
from replay_bench import account, arms, attempt, campaign, checks, diffing, smoke, smoke_pass, tasks
from replay_bench import runner as replay_runner
from replay_bench.runner import execute_run
from replay_bench.spend import SpendAppendError, SpendFile
from review_bench import fixture_repo
from review_bench import runner as review_runner
from test_replay_bench_checks_support import _line
from test_replay_bench_runner_support import (
    AGENT_ID,
    BLOCK_DENIAL,
    ENTRY_DENIAL,
    MODEL_ID,
    READING,
    SLEEP_PAST_TIMEOUT_S,
    SLEEP_REFUSAL_BOUND_S,
    Scenario,
    build_replay_world,
    claude_md_line,
    leaking_log_lines,
    make_block,
    make_ignored_local_dir,
    make_settings,
    spend_lines,
    stub_session_store,
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


def without_account_evidence(**scenario_fields) -> Scenario:
    return Scenario(log_lines=[], write_account_session_file=False, **scenario_fields)


def delete_session_transcript(call) -> None:
    (stub_session_store(call.config_dir / "projects", call.session_id) / f"{call.session_id}.jsonl").unlink()


def leftovers(world, launch_stub) -> list[Path]:
    """The fixtures, attempt dirs and session stores that survived the run, found by the directories themselves so a
    fixture built for an attempt that never launched is seen too."""
    survivors = [call.cwd for call in launch_stub.calls if call.cwd.exists()]
    survivors.extend(world.root.glob("tmp/review-bench-*"))
    attempts_root = make_settings(world, launch_stub).attempts_root
    survivors.extend(attempts_root.iterdir() if attempts_root.exists() else [])
    projects_root = world.config_dir / "projects"
    survivors.extend(projects_root.iterdir() if projects_root.exists() else [])
    return survivors


class TestAValidRun:
    @pytest.mark.parametrize("arm_name", ARM_NAMES)
    def test_the_run_is_recorded_ok(self, world, arm_name):
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
        assert not list((world.config_dir / "projects").glob("*/*.jsonl")), "the dispatcher transcript is deleted"

    @pytest.mark.parametrize("arm_name", ARM_NAMES)
    def test_the_launch_argv_is_wired(self, world, arm_name):
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

    def test_cleanup_deletes_only_the_runs_own_session_store(self, world):
        sibling_store = world.config_dir / "projects" / "another-project"
        sibling_store.mkdir(parents=True)
        sibling_transcript = sibling_store / "another-session.jsonl"
        sibling_transcript.write_text("{}\n")

        outcome, launch_stub, _settings = run(world, arms.TOOLKIT.name)

        own_store = stub_session_store(world.config_dir / "projects", launch_stub.calls[0].session_id)
        assert outcome.record.status == "ok", "the run reached cleanup with its own store written"
        assert sibling_transcript.is_file(), "another session's store under the projects dir survives"
        assert not own_store.exists(), "the run's own store is gone"


class TestForgedLog:
    @pytest.mark.parametrize("arm_name", ARM_NAMES)
    def test_a_forged_good_log_does_not_rescue_a_failing_run(self, world, arm_name):
        scenario = Scenario(
            log_lines=lambda session_id: [_line(str(world.config_dir / "agents" / "reviewer.md"), session_id=session_id)],
            forged_log_lines=lambda session_id: [claude_md_line(world, session_id)],
        )
        outcome, launch_stub, _settings = run(world, arm_name, lambda _n, _a: scenario)

        assert outcome.record.missing_reason == checks.REASON_INSTRUMENT_MISSING
        assert len(launch_stub.calls) == 1

    @pytest.mark.parametrize("arm_name", ARM_NAMES)
    def test_a_forged_poisoned_log_does_not_fail_a_clean_run(self, world, arm_name):
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
    def test_a_damaged_sidecar_fails_transcript_unreadable(self, world, arm_name, damage):
        validity_seen = []
        scenario = Scenario(
            damage_sidecar=damage, after_write=lambda call: validity_seen.append(validity_of(world, call)),
        )

        outcome, launch_stub, _settings = run(world, arm_name, lambda _n, _a: scenario)

        assert [validity.ok for validity in validity_seen] == [True, True], "validity passes the damaged sidecar"
        assert outcome.record.missing_reason == review_runner.VALIDITY_FAIL_TRANSCRIPT_UNREADABLE
        assert len(launch_stub.calls) == 2, "a path reason is not an instrument reason, so it retries once"

    def test_a_sidecar_read_error_maps_to_the_unreadable_transcript_reason(self, world, monkeypatch):
        def unreadable(_path):
            raise OSError("synthetic")

        monkeypatch.setattr(review_runner, "extract_read_like_calls", unreadable)
        outcome, _launch_stub, _settings = run(world, arms.BARE.name)
        assert outcome.record.missing_reason == review_runner.VALIDITY_FAIL_TRANSCRIPT_UNREADABLE


class TestPathReasonsReachTheRecord:
    @pytest.mark.parametrize(("arm_name", "relative_path"), [("toolkit", ""), ("toolkit", "projects"), ("bare", "projects")])
    def test_a_bash_command_on_the_config_dir_fails_out_of_fixture_path(
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

    def test_a_bare_bash_command_on_the_config_dir_fails_arm_leak(self, world):
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

    @pytest.mark.parametrize("denial_shape", [ENTRY_DENIAL, BLOCK_DENIAL])
    def test_a_denied_write_outside_the_fixture_fails_nothing(self, world, denial_shape):
        """`ENTRY_DENIAL` is the shape recorded on a live CLI, so the record, the denial count and the out-of-fixture
        exemption are exercised end to end in it as well as in the block-level variant."""
        target = str(world.root / "elsewhere.py")
        outcome, _launch_stub, _settings = run(
            world, arms.BARE.name, tool_scenario(("Write", {"file_path": target}, True), denial_shape=denial_shape),
        )

        assert outcome.record.missing_reason is None and outcome.record.permission_denials == 1
        assert outcome.record.out_of_fixture_writes == []

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

    def test_a_bare_arm_leak_is_not_retried(self, world):
        outcome, launch_stub, _settings = run(world, arms.BARE.name, lambda _n, _a: self.leaking_scenario(world))

        assert outcome.record.missing_reason == checks.REASON_ARM_LEAK
        assert len(launch_stub.calls) == 1

    def test_an_instrument_reason_outranks_a_timeout(self, world):
        outcome, launch_stub, _settings = run(
            world, arms.BARE.name, lambda _n, _a: self.leaking_scenario(world, timed_out=True),
        )

        assert outcome.record.missing_reason == checks.REASON_ARM_LEAK
        assert len(launch_stub.calls) == 1

    @pytest.mark.parametrize("run_end", [{"timed_out": True}, {"omit_result_event": True}], ids=["timeout", "no-result-event"])
    def test_a_fixture_instruction_load_outranks_a_run_that_did_not_complete(self, world, run_end):
        fixture_load = Scenario(
            log_lines=lambda session_id, fixture_dir: [
                claude_md_line(world, session_id),
                _line(str(fixture_dir / "src" / "app.py"), "path_glob_match", agent_id=AGENT_ID, session_id=session_id),
            ],
            **run_end,
        )

        outcome, launch_stub, _settings = run(world, arms.TOOLKIT.name, lambda _n, _a: fixture_load)

        assert outcome.record.missing_reason == checks.REASON_FIXTURE_INSTRUCTION_LOAD
        assert len(launch_stub.calls) == 1

    @staticmethod
    def dispatching_twice_scenario(world, *, dispatches: int):
        """Every subagent loads an instruction file under its own agent ID, as `dispatches` subagents would."""

        def append_dispatches(call) -> None:
            session_jsonl = stub_session_store(call.config_dir / "projects", call.session_id) / f"{call.session_id}.jsonl"
            with session_jsonl.open("a") as handle:
                for number in range(2, dispatches + 1):
                    handle.write(json.dumps({"type": "assistant", "message": {"content": [{
                        "type": "tool_use", "id": f"toolu_dispatch_{number}", "name": "Agent",
                        "input": {"subagent_type": call.agent_name, "prompt": call.inner_prompt},
                    }]}}) + "\n")

        return Scenario(
            log_lines=lambda session_id: [
                claude_md_line(world, session_id),
                *(
                    _line(
                        str(world.config_dir / "rules" / "python.md"), "path_glob_match", agent_id=f"agent-{number}",
                        session_id=session_id,
                    )
                    for number in range(1, dispatches + 1)
                ),
            ],
            after_write=append_dispatches,
        )

    def test_a_dispatcher_that_dispatched_twice_records_the_validity_reason_and_retries_once(self, world):
        outcome, launch_stub, _settings = run(
            world, arms.TOOLKIT.name, lambda _n, _a: self.dispatching_twice_scenario(world, dispatches=2),
        )

        assert outcome.record.missing_reason == review_runner.VALIDITY_FAIL_EXTRA_DISPATCHER_TOOL_CALL
        assert len(launch_stub.calls) == 2

    def test_two_agent_ids_without_a_second_dispatch_are_instrument_log_invalid_and_not_retried(self, world):
        scenario = self.dispatching_twice_scenario(world, dispatches=2)
        scenario = dataclasses.replace(scenario, after_write=None)

        outcome, launch_stub, _settings = run(world, arms.TOOLKIT.name, lambda _n, _a: scenario)

        assert outcome.record.missing_reason == checks.REASON_INSTRUMENT_LOG_INVALID
        assert len(launch_stub.calls) == 1

    def test_a_missing_account_session_file_is_account_hooks_missing(self, world):
        outcome, launch_stub, _settings = run(
            world, arms.TOOLKIT.name, lambda _n, _a: Scenario(write_account_session_file=False),
        )

        assert outcome.record.missing_reason == checks.REASON_ACCOUNT_HOOKS_MISSING
        assert len(launch_stub.calls) == 1

    @pytest.mark.parametrize(
        ("scenario", "expected_reason", "expected_cap_charges"),
        [
            (without_account_evidence(timed_out=True), review_runner.MISSING_REASON_TIMEOUT, [True, True]),
            (
                without_account_evidence(omit_result_event=True), review_runner.VALIDITY_FAIL_NO_RESULT_EVENT,
                [True, True],
            ),
            (
                without_account_evidence(after_write=delete_session_transcript),
                review_runner.VALIDITY_FAIL_SESSION_STORE_NOT_FOUND, [False, False],
            ),
            (
                Scenario(timed_out=True, log_lines=["not an object"], write_account_session_file=False),
                review_runner.MISSING_REASON_TIMEOUT, [True, True],
            ),
        ],
        ids=["timeout", "no-result-event", "session-store-not-found", "timeout-with-a-non-object-log-line"],
    )
    def test_a_run_that_did_not_complete_keeps_its_own_reason(
        self, world, scenario, expected_reason, expected_cap_charges,
    ):
        """A launch that ends in the timeout kill leaves no result event, so its attempts are charged at the cap."""
        outcome, launch_stub, settings = run(world, arms.TOOLKIT.name, lambda _n, _a: scenario)

        assert outcome.record.missing_reason == expected_reason
        assert len(launch_stub.calls) == 2
        assert [line["is_cap_charge"] for line in spend_lines(settings)] == expected_cap_charges

    def test_a_missing_dispatcher_transcript_leaves_the_store_dir_cleanup_cannot_find(self, world):
        """Cleanup finds a session store through its dispatcher transcript, so a store whose transcript is gone stays
        and nothing else of the run does."""
        _outcome, launch_stub, _settings = run(
            world, arms.TOOLKIT.name,
            lambda _n, _a: without_account_evidence(after_write=delete_session_transcript),
        )

        projects_root = world.config_dir / "projects"
        stores = {stub_session_store(projects_root, call.session_id) for call in launch_stub.calls}
        assert len(stores) == 2 and set(leftovers(world, launch_stub)) == stores

    @pytest.mark.parametrize(
        "result_event_fields",
        [
            {"is_error": True, "subtype": "error_during_execution"},
            {"terminal_reason": "budget_exceeded"},
        ],
        ids=["result-error", "budget"],
    )
    def test_a_run_that_reached_its_end_with_no_account_evidence_is_instrument_missing(
        self, world, result_event_fields,
    ):
        outcome, launch_stub, _settings = run(
            world, arms.TOOLKIT.name,
            lambda _n, _a: without_account_evidence(result_event_fields=result_event_fields),
        )

        assert outcome.record.missing_reason == checks.REASON_INSTRUMENT_MISSING
        assert len(launch_stub.calls) == 1


class TestRetryThenMissing:
    def test_two_failed_attempts_record_missing(self, world):
        outcome, launch_stub, settings = run(world, arms.BARE.name, lambda _n, _a: Scenario(observed_model="other-model"))

        record = outcome.record
        assert (record.status, record.missing_reason, record.attempts) == ("missing", "model-mismatch", 2)
        assert record.total_cost_usd == 1.0
        assert len(spend_lines(settings)) == 2
        assert leftovers(world, launch_stub) == []

    def test_a_retried_run_takes_its_diff_from_the_second_fixture(self, world):
        def scenario_for(number, _agent):
            edited_file = "failed_attempt.py" if number == 0 else "second_attempt.py"
            return Scenario(
                observed_model="other-model" if number == 0 else MODEL_ID,
                edit=lambda fixture_dir: (fixture_dir / "src" / edited_file).write_text("value = 1\n"),
            )

        outcome, launch_stub, _settings = run(world, arms.TOOLKIT.name, scenario_for)

        assert outcome.record.status == "ok" and outcome.record.attempts == 2
        assert "second_attempt.py" in outcome.diff_text and "failed_attempt.py" not in outcome.diff_text
        assert launch_stub.calls[0].cwd != launch_stub.calls[1].cwd
        assert leftovers(world, launch_stub) == []

    def test_failed_launches_are_charged_at_the_cap(self, world):
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

    def test_an_analysis_exception_records_analysis_error(
        self, world, monkeypatch,
    ):
        def failing_capture(*_args, **_kwargs):
            raise ValueError("synthetic")

        monkeypatch.setattr(diffing, "capture_change", failing_capture)
        outcome, launch_stub, settings = run(world, arms.BARE.name)

        assert outcome.record.missing_reason == attempt.REASON_ANALYSIS_ERROR and len(launch_stub.calls) == 1
        assert outcome.record.total_cost_usd == 0.5 and len(spend_lines(settings)) == 1
        assert leftovers(world, launch_stub) == []

    def test_a_prune_that_raises_records_fixture_error(self, world, monkeypatch):
        def failing_prune(_dest_dir):
            raise RuntimeError("synthetic prune failure")

        monkeypatch.setattr(tasks, "prune_instruction_files", failing_prune)
        outcome, launch_stub, settings = run(world, arms.BARE.name)

        assert outcome.record.missing_reason == attempt.REASON_FIXTURE_ERROR and launch_stub.calls == []
        assert spend_lines(settings) == []
        assert leftovers(world, launch_stub) == []

    def test_an_extraction_refusal_leaves_nothing_on_disk(self, world, monkeypatch):
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
    def test_a_git_timeout_records_the_limit_detail(self, world, monkeypatch, failing_step):
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

    def test_a_generator_refusal_records_fixture_error(self, world):
        offending = world.config_dir / "skills" / "we(ird)"
        offending.mkdir()
        (offending / "SKILL.md").write_text("skill\n")
        outcome, launch_stub, settings = run(world, arms.TOOLKIT.name)

        assert outcome.record.missing_reason == attempt.REASON_FIXTURE_ERROR and launch_stub.calls == []
        assert "we(ird)" in outcome.record.missing_detail
        assert spend_lines(settings) == []
        assert outcome.record.attempts == 1, "fixture-error never retries"

    def test_an_attempt_dir_that_already_exists_records_fixture_error(self, world):
        settings = make_settings(world, world.launch())
        existing = settings.attempts_root / f"{world.task.id}-bare-0-1"
        existing.mkdir(parents=True)
        outcome = execute_run(settings, make_block(world), arms.BARE.name, 0)

        assert outcome.record.missing_reason == attempt.REASON_FIXTURE_ERROR
        assert str(existing) in outcome.record.missing_detail
        assert existing.exists(), "an attempt dir the attempt did not create is not deleted"


class TestGitDirCallsCarryTheTimeout:
    def test_each_git_dir_call_carries_the_configured_limit(self, world, monkeypatch):
        limits = []
        real_run = subprocess.run

        def spy(command, *args, **kwargs):
            if any(str(part).startswith("--git-dir=") for part in command):
                limits.append(kwargs.get("timeout"))
            return real_run(command, *args, **kwargs)

        monkeypatch.setattr(subprocess, "run", spy)
        run(world, arms.BARE.name, fixture_git_timeout_s=37.5)

        assert limits and set(limits) == {37.5}


class TestTheFixtureTheAgentSees:
    """A scripted launch cannot see the fixture unless the test looks, so the launch call records it."""

    @pytest.mark.parametrize("arm_name", ARM_NAMES)
    def test_each_arm_gets_its_fixture_files(self, world, arm_name):
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

    def test_a_command_reading_the_local_dir_fails_out_of_fixture_path(self, world):
        token = str(world.local_dir / "campaigns" / "other" / "runs" / "t" / "toolkit-0.diff")

        outcome, _stub, _settings = run(world, arms.BARE.name, self.bash(f"cat {token}"))

        assert outcome.record.missing_reason == checks.REASON_OUT_OF_FIXTURE_PATH
        assert token in outcome.record.out_of_fixture_tokens

    def test_a_live_checkouts_git_dir_and_changed_file_are_guarded(self, world):
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

    def test_only_the_runs_own_session_files_are_exempt(self, world):
        projects_root = world.config_dir / "projects"

        own, _stub, _settings = run(
            world, arms.BARE.name, lambda _n, _a: Scenario(
                tool_calls=lambda call: [(
                    "Bash",
                    {"command": f"cat {stub_session_store(projects_root, call.session_id) / call.session_id}.jsonl"},
                    False,
                )],
            ),
        )
        sibling_paths: list[str] = []

        def read_a_sibling_file_in_the_runs_own_store(call):
            sibling_path = str(stub_session_store(projects_root, call.session_id) / "another-session.jsonl")
            sibling_paths.append(sibling_path)
            return [("Bash", {"command": f"cat {sibling_path}"}, False)]

        sibling, launch_stub, _settings = run(
            world, arms.BARE.name, lambda _n, _a: Scenario(tool_calls=read_a_sibling_file_in_the_runs_own_store),
        )
        assert len(sibling_paths) == len(launch_stub.calls), "every launch read a sibling file in its own store"
        sibling_path = sibling_paths[-1]

        assert own.record.missing_reason is None
        assert sibling.record.missing_reason == checks.REASON_OUT_OF_FIXTURE_PATH
        assert sibling_path in sibling.record.out_of_fixture_tokens

    def test_the_live_roots_join_the_defaults_with_the_worktrees(self, world, monkeypatch):
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
    def test_unflagged_findings_reach_the_record(self, world):
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


class TestWorkerFailures:
    @pytest.fixture(autouse=True)
    def stub_environment(self, monkeypatch):
        monkeypatch.setattr(review_runner, "read_environment_record", lambda *, checkout_root=None: READING)

    def test_an_unwritable_spend_file_raises_spend_append_error(
        self, world, tmp_path,
    ):
        """Kills: dropping the `OSError` translation in `SpendFile.append`, which would escape as a raw `OSError`,
        so the uncharged launch is never reported."""
        blocker = tmp_path / "blocker"
        blocker.write_text("a file, not a directory")
        launch_stub = world.launch()
        real_settings = make_settings(world, launch_stub)
        settings = dataclasses.replace(
            real_settings, spend=SpendFile(blocker / "spend.jsonl", budget_cap_usd=real_settings.budget_cap_usd),
        )

        with pytest.raises(SpendAppendError, match="could not append to the spend file"):
            execute_run(settings, make_block(world), arms.BARE.name, 0)

        assert settings.uncharged_launches == [(world.task.id, launch_stub.calls[0].session_id)]

    def test_a_failed_spend_append_starts_no_further_launch(self, world, monkeypatch):
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

    def test_a_refused_cap_charge_surfaces_the_append_error(
        self, world, monkeypatch,
    ):
        """Kills: dropping the `uncharged_launches` append from `run_attempt`'s failed cap charge, which lets the
        campaign halt read `Launches begun and not charged: none` for a paid session missing from the ledger."""

        def failing_append(self, line):
            raise SpendAppendError("disk full")

        monkeypatch.setattr(SpendFile, "append", failing_append)
        launch_stub = world.launch(lambda _n, _a: Scenario(raises=RuntimeError("synthetic")))
        settings = make_settings(world, launch_stub)

        with pytest.raises(SpendAppendError, match="disk full"):
            execute_run(settings, make_block(world), arms.BARE.name, 0)

        assert settings.uncharged_launches == [(world.task.id, launch_stub.calls[0].session_id)]

    def test_a_launch_failure_on_a_working_disk_is_cap_charged(self, world):
        launch_stub = world.launch(lambda _n, _a: Scenario(raises=RuntimeError("synthetic")))
        settings = make_settings(world, launch_stub)

        with pytest.raises(RuntimeError, match="synthetic"):
            execute_run(settings, make_block(world), arms.BARE.name, 0)

        assert [line["is_cap_charge"] for line in spend_lines(settings)] == [True]
        assert settings.uncharged_launches == []

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

    def test_an_interrupt_stops_a_run_still_building_its_fixture(
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

    def test_an_interrupt_aborts_a_launch_in_flight(self, world, monkeypatch):
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

    def test_a_sibling_failing_at_launch_aborts_a_run_held_in_its_fixture_build(
        self, world, monkeypatch,
    ):
        """Kills:
        - `abort_launches()` missing from the failing worker: the wait times out.
        - The pre-launch recheck missing: the held run launches."""
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

    @pytest.mark.parametrize(
        "incidental_error",
        [OSError(errno.ENOSPC, "synthetic: no space left"), review_runner.HarnessInvalidatedError("synthetic refusal")],
        ids=["os-error", "harness-invalidated"],
    )
    def test_a_spend_failure_outranks_an_earlier_runs_incidental_error(self, world, monkeypatch, incidental_error):
        """The earlier run in the plan fails after a sibling's cap charge did. The block reports the spend failure,
        whose halt names the launch begun and not charged; the incidental error would end the run without it."""
        first_arm, _index = review_runner.build_block_plan(world.task.id, ("toolkit", "bare"), 1, 0).ordered_runs[0]
        real_build = tasks.build_task_fixture
        real_record_directory = review_runner.RunStore.record_directory
        held_fixture_dirs = []

        def build(task, dest_dir, baseline_git_dir, **kwargs):
            if f"-{first_arm}-" not in baseline_git_dir.parent.name:
                return real_build(task, dest_dir, baseline_git_dir, **kwargs)
            held_fixture_dirs.append(dest_dir)
            assert msmr._launches_aborted.wait(timeout=10), "the sibling's failure never stopped launches"
            return "0" * 40

        def record_directory(self, task_id, fixture_dir, session_id):
            if session_id and fixture_dir in held_fixture_dirs:
                raise incidental_error
            return real_record_directory(self, task_id, fixture_dir, session_id)

        def failing_append(self, line):
            raise SpendAppendError("disk full")

        monkeypatch.setattr(tasks, "build_task_fixture", build)
        monkeypatch.setattr(review_runner.RunStore, "record_directory", record_directory)
        monkeypatch.setattr(SpendFile, "append", failing_append)
        launch_stub = world.launch(by_call=lambda call: Scenario(raises=RuntimeError("synthetic")))
        settings = make_settings(world, launch_stub)

        with pytest.raises(SpendAppendError, match="disk full") as halt:
            replay_runner.run_block(
                settings, world.task, world.account, ("toolkit", "bare"), 1, 0, review_runner.EnvironmentReference(),
                workers=2,
            )

        assert incidental_error in settings.failures, "the earlier run failed with its own error"
        assert settings.uncharged_launches == [(world.task.id, launch_stub.calls[0].session_id)]
        assert f"1 other failure(s) in the block: {type(incidental_error).__name__}: {incidental_error}" in str(halt.value)
        spend_failure = halt.value.__cause__
        assert isinstance(spend_failure, SpendAppendError) and str(spend_failure) == "disk full"
        assert isinstance(spend_failure.__context__, RuntimeError), "the failure that preceded the append is kept"

    def test_a_non_spend_failure_at_launch_is_reported_over_the_abort_it_caused(self, world, monkeypatch):
        """Kills: the root-cause choice returning the earlier run's `LaunchAbortedError` instead of the recorded
        failure that caused it, which reports "a sibling run failed" with no sign of the failure itself."""

        class SyntheticWorkerFailure(Exception):
            pass

        first_arm, _index = review_runner.build_block_plan(world.task.id, ("toolkit", "bare"), 1, 0).ordered_runs[0]
        real_build = tasks.build_task_fixture

        def build(task, dest_dir, baseline_git_dir, **kwargs):
            if f"-{first_arm}-" not in baseline_git_dir.parent.name:
                return real_build(task, dest_dir, baseline_git_dir, **kwargs)
            assert msmr._launches_aborted.wait(timeout=10), "the sibling's failure never stopped launches"
            return "0" * 40

        monkeypatch.setattr(tasks, "build_task_fixture", build)
        launch_stub = world.launch(by_call=lambda call: Scenario(raises=SyntheticWorkerFailure("synthetic")))
        settings = make_settings(world, launch_stub)

        with pytest.raises(SyntheticWorkerFailure, match="synthetic"):
            replay_runner.run_block(
                settings, world.task, world.account, ("toolkit", "bare"), 1, 0, review_runner.EnvironmentReference(),
                workers=2,
            )

        assert len(launch_stub.calls) == 1, "the held earlier run was aborted before it launched"

    def test_an_interrupt_is_reported_as_the_interrupt_over_a_recorded_spend_failure(self, world, monkeypatch, capsys):
        """Kills: dropping the non-`Exception` guard in the root-cause choice, which reports an interrupt that lands
        after a spend failure as a spend halt and loses the interrupt's exit status."""

        def interrupting_runs():
            raise KeyboardInterrupt
            yield

        monkeypatch.setattr(
            review_runner, "build_block_plan", lambda *a, **k: types.SimpleNamespace(ordered_runs=interrupting_runs()),
        )
        settings = make_settings(world, world.launch())
        settings.failures.append(SpendAppendError("disk full"))

        with pytest.raises(KeyboardInterrupt):
            replay_runner.run_block(
                settings, world.task, world.account, ("toolkit", "bare"), 1, 0, review_runner.EnvironmentReference(),
                workers=2,
            )

        assert capsys.readouterr().err == "", "no launch went uncharged, so there is nothing to name"

    def test_an_interrupt_names_the_launch_it_left_uncharged(self, world, monkeypatch, capsys):
        """Kills: `run_block` ending an interrupt without naming `settings.uncharged_launches`, which leaves a paid
        session missing from the spend file with no sign of it on the interrupt's exit path."""
        in_flight = threading.Event()
        launched_session_ids = []

        def blocking_launch(cmd, _cwd, _timeout_s):
            launched_session_ids.append(cmd[cmd.index("--session-id") + 1])
            in_flight.set()
            assert msmr._launches_aborted.wait(timeout=10), "the interrupt never aborted the launch"
            raise msmr.LaunchAbortedError("aborted while the child was running")

        def interrupting_runs():
            yield ("toolkit", 0)
            assert in_flight.wait(timeout=10), "the first run never reached its launch"
            raise KeyboardInterrupt

        def failing_append(self, line):
            raise SpendAppendError("disk full")

        monkeypatch.setattr(SpendFile, "append", failing_append)
        monkeypatch.setattr(
            review_runner, "build_block_plan", lambda *a, **k: types.SimpleNamespace(ordered_runs=interrupting_runs()),
        )
        settings = make_settings(world, blocking_launch)

        with pytest.raises(KeyboardInterrupt):
            replay_runner.run_block(
                settings, world.task, world.account, ("toolkit", "bare"), 1, 0, review_runner.EnvironmentReference(),
                workers=2,
            )

        assert capsys.readouterr().err == (
            f"Launches begun and not charged: task {world.task.id} session {launched_session_ids[0]}\n"
        )

    def test_a_spend_failure_landing_after_the_earlier_runs_error_is_consumed_is_still_reported(
        self, world, monkeypatch,
    ):
        """Kills: choosing the root cause before the pool joins, which reports the earlier run's error and loses the
        spend failure that lands while the pool ends."""

        class SyntheticWorkerFailure(Exception):
            pass

        first_arm, later_arm = (
            arm for arm, _index in review_runner.build_block_plan(world.task.id, ("toolkit", "bare"), 1, 0).ordered_runs
        )
        later_run_launching = threading.Event()
        consumer_stopped_launches = threading.Event()
        abort_calls = []
        real_abort_launches = msmr.abort_launches

        def counting_abort_launches():
            # The first call is the earlier run's worker, the second is the consumer taking its error.
            real_abort_launches()
            abort_calls.append(None)
            if len(abort_calls) == 2:
                consumer_stopped_launches.set()

        def scenario(call):
            if call.agent_name == arms.ARMS[first_arm].agent_name:
                assert later_run_launching.wait(timeout=10), "the later run never reached its launch"
                return Scenario(raises=SyntheticWorkerFailure("synthetic"))
            later_run_launching.set()
            assert consumer_stopped_launches.wait(timeout=10), "the consumer never took the earlier run's error"
            return Scenario()

        def failing_append(self, line):
            if line.arm == later_arm:
                raise SpendAppendError("disk full")

        monkeypatch.setattr(msmr, "abort_launches", counting_abort_launches)
        monkeypatch.setattr(SpendFile, "append", failing_append)
        launch_stub = world.launch(by_call=scenario)
        settings = make_settings(world, launch_stub)

        with pytest.raises(SpendAppendError, match="disk full") as halt:
            replay_runner.run_block(
                settings, world.task, world.account, ("toolkit", "bare"), 1, 0, review_runner.EnvironmentReference(),
                workers=2,
            )

        assert "SyntheticWorkerFailure: synthetic" in str(halt.value)
        assert settings.uncharged_launches == [(world.task.id, next(
            call.session_id for call in launch_stub.calls if call.agent_name == arms.ARMS[later_arm].agent_name
        ))]

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


class TestAttemptsRunningTogether:
    @pytest.fixture(autouse=True)
    def stub_environment(self, monkeypatch):
        monkeypatch.setattr(review_runner, "read_environment_record", lambda *, checkout_root=None: READING)

    def test_attempts_that_overlap_share_no_state_and_each_keeps_its_own_record(self, world):
        """Every attempt waits at the barrier between its fixture build and its cleanup, so all of them hold their
        fixture and session store at once. The first launch to return then finishes, cleanup included, before any
        other attempt reads its session, so state that two attempts share is deleted under the others."""
        arm_names, k, workers = ("toolkit", "bare"), 2, 4
        attempts_in_flight = threading.Barrier(workers, timeout=60)

        def hold_until_the_first_launch_is_cleaned_up(call):
            if launch_stub.calls.index(call) >= workers:
                return  # only the first wave meets at the barrier; a retry starts after it
            attempts_in_flight.wait()
            first_call = launch_stub.calls[0]
            if call is first_call:
                return
            deadline = time.monotonic() + 60
            while first_call.cwd.exists():
                assert time.monotonic() < deadline, "the first attempt never finished its cleanup"
                time.sleep(0.05)

        launch_stub = world.launch(by_call=lambda _call: Scenario(after_write=hold_until_the_first_launch_is_cleaned_up))
        settings = make_settings(world, launch_stub)

        block = replay_runner.run_block(
            settings, world.task, world.account, arm_names, k, 0, review_runner.EnvironmentReference(), workers=workers,
        )

        planned_runs = review_runner.build_block_plan(world.task.id, arm_names, k, 0).ordered_runs
        assert len(planned_runs) == workers
        assert [(arm, index) for arm, index, _outcome in block.runs] == list(planned_runs)
        for arm, index, outcome in block.runs:
            assert (outcome.record.arm, outcome.record.run_index) == (arm, index)
            assert (outcome.record.status, outcome.record.attempts) == ("ok", 1)
        spent = spend_lines(settings)
        assert sorted((line["arm"], line["run_index"], line["attempt"]) for line in spent) == sorted(
            (arm, index, 1) for arm, index in planned_runs
        )
        assert len({call.session_id for call in launch_stub.calls}) == workers
        assert len({call.cwd for call in launch_stub.calls}) == workers
        assert leftovers(world, launch_stub) == []


class TestAWorktreeListFailure:
    def test_only_the_exit_status_is_reported(self, world, tmp_path, monkeypatch):
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

    def test_missing_required_flags_exit_two(self, capsys):
        assert main_run("run", "--tasks", "x", "--cumulative-stop-usd", "10") == 2
        assert "the following arguments are required: --k" in capsys.readouterr().err
        assert main_run("run", "--tasks", "x", "--k", "1") == 2
        assert "the following arguments are required: --cumulative-stop-usd" in capsys.readouterr().err

    def test_smoke_takes_no_k(self, capsys):
        assert main_run("smoke", "--tasks", "x", "--task-id", "00000000-000000000000", "--k", "2") == 2
        assert "unrecognized arguments: --k" in capsys.readouterr().err

    def test_a_malformed_task_id_exits_two(self, capsys):
        assert main_run("smoke", "--tasks", "x", "--task-id", "not-an-id") == 2
        assert "not-an-id" in capsys.readouterr().err

    def test_a_zero_selection_limit_exits_two(self, monkeypatch, capsys):
        monkeypatch.setattr(tasks, "mine_tasks", lambda *a, **k: pytest.fail("mine_tasks must not run"))

        code = main_run(*TASKS_ARGV, "--max-count", "0")

        assert code == 2 and "--max-count" in capsys.readouterr().err

    def test_a_malformed_task_file_exits_two(self, local_dir, capsys):
        (local_dir / "tasks").mkdir(parents=True)
        (local_dir / "tasks" / "broken.json").write_text("{not json")

        assert main_run("run", "--tasks", "broken", "--k", "1", "--cumulative-stop-usd", "10") == 2
        assert "broken.json" in capsys.readouterr().err

    def test_a_task_set_name_breaking_its_grammar_exits_two(self, capsys):
        assert main_run("tasks", "--source-repo", "/x", "--trusted-author", "a@b.c", "--name", "../escape") == 2
        assert "invalid campaign ID" in capsys.readouterr().err

    def test_tasks_for_an_existing_task_set_exits_two(
        self, world, local_dir, monkeypatch, capsys,
    ):
        existing_path = tasks.write_task_set(local_dir / "tasks", "n", world.tasks)
        before = existing_path.read_text()
        monkeypatch.setattr(tasks, "mine_tasks", lambda *a, **k: pytest.fail("mine_tasks must not run"))

        assert main_run(*TASKS_ARGV) == 2

        assert "task set 'n' already exists" in capsys.readouterr().err
        assert existing_path.read_text() == before

    def test_an_empty_local_dir_exits_two(
        self, local_dir, capsys,
    ):
        assert main_run("--local-dir", "", "summarize", "--campaign-id", "c1") == 2

        error = capsys.readouterr().err
        assert "argument --local-dir: must not be empty" in error
        assert not local_dir.exists()

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

    @pytest.mark.parametrize("kind, refusal_fragment", [
        # No relative path equals its own absolute realpath, so this one also fails the resolved-path rule.
        ("relative", "is not an absolute path"),
        # Git reports a path through a symlink as not ignored, so the ignore rule also refuses this one.
        ("symlink", "passes through a symlink"),
        ("name", "must end in"),
        ("unignored", "is not ignored by the git work tree"),
        # Git exits 128, not 1, outside any work tree, so the refusal cannot claim the path is unignored.
        ("outside-work-tree", "git could not confirm that the directory is ignored"),
    ])
    def test_a_refused_local_dir_exits_two(
        self, tmp_path, capsys, kind, refusal_fragment,
    ):
        unignored_parent = tmp_path / "unignored-parent"
        unignored_parent.mkdir()
        if kind == "unignored":
            subprocess.run(["git", "init", "-q"], cwd=unignored_parent, check=True, capture_output=True)
        if kind == "symlink":
            (tmp_path / "link-parent").symlink_to(make_ignored_local_dir(tmp_path).parent)
        if kind == "name":
            ignoring_work_tree = tmp_path / "name-checkout"
            ignoring_work_tree.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=ignoring_work_tree, check=True, capture_output=True)
            (ignoring_work_tree / ".gitignore").write_text("ignored-but-misnamed/\n")
        target = {
            "relative": Path("relative-parent") / ".local",
            "symlink": tmp_path / "link-parent" / ".local",
            "name": tmp_path / "name-checkout" / "ignored-but-misnamed",
            "unignored": unignored_parent / ".local",
            "outside-work-tree": unignored_parent / ".local",
        }[kind]

        assert main_run("--local-dir", str(target), "summarize", "--campaign-id", "c1") == 2
        assert refusal_fragment in capsys.readouterr().err
        assert not target.exists()

    def test_a_timed_out_ignore_probe_refuses_with_the_fixed_limit_not_claiming_the_dir_is_unignored(
        self, tmp_path, monkeypatch,
    ):
        ignored_local_dir = make_ignored_local_dir(tmp_path)

        def probe_that_times_out(command, **kwargs):
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])

        monkeypatch.setattr(run_replay_bench.subprocess, "run", probe_that_times_out)

        with pytest.raises(review_runner.HarnessInvalidatedError) as refusal:
            run_replay_bench.check_local_dir(ignored_local_dir)

        message = str(refusal.value)
        assert "timed out" in message and f"{review_runner._ENVIRONMENT_READ_TIMEOUT_S} s" in message
        assert "no flag" in message and "is not ignored" not in message

    @pytest.mark.parametrize("probe_returncode", [2, 128])
    def test_a_failed_ignore_probe_refuses_without_claiming_the_dir_is_unignored(
        self, tmp_path, monkeypatch, probe_returncode,
    ):
        ignored_local_dir = make_ignored_local_dir(tmp_path)

        def probe_that_fails(command, **_kwargs):
            return subprocess.CompletedProcess(command, probe_returncode, stdout=b"", stderr=b"")

        monkeypatch.setattr(run_replay_bench.subprocess, "run", probe_that_fails)

        with pytest.raises(review_runner.HarnessInvalidatedError) as refusal:
            run_replay_bench.check_local_dir(ignored_local_dir)

        message = str(refusal.value)
        assert "could not confirm" in message and "is not ignored" not in message

    def test_the_default_local_dir_is_ignored_by_this_checkout(self):
        run_replay_bench.check_local_dir(REAL_DEFAULT_LOCAL_DIR)

    def test_a_pre_existing_loose_local_dir_is_restricted_to_owner_only(self, local_dir):
        local_dir.mkdir(parents=True)
        local_dir.chmod(0o755)

        main_run(*TASKS_ARGV, "--max-count", "0")

        assert stat.S_IMODE(local_dir.stat().st_mode) == 0o700

    def test_the_local_dir_and_its_files_are_owner_only(
        self, world, local_dir, monkeypatch,
    ):
        monkeypatch.setattr(tasks, "mine_tasks", lambda *a, **k: tasks.MiningReport(tasks=list(world.tasks)))

        assert main_run(*TASKS_ARGV) == 0

        assert stat.S_IMODE(local_dir.stat().st_mode) == 0o700
        assert stat.S_IMODE((local_dir / "tasks" / "n.json").stat().st_mode) == 0o600

    def test_git_below_2_32_exits_two(self, tmp_path, monkeypatch, capsys):
        fake_bin = tmp_path / "fakebin"
        write_executable(fake_bin / "git", 'echo "git version 2.31.0"\n')
        monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ['PATH']}")

        assert main_run("preflight", "--tasks", "x") == 2
        assert "git 2.32" in capsys.readouterr().err

    def test_an_interrupt_aborts_launches_exits_130_and_prints_a_rerun_hint(
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
        assert err.strip() == "tasks: interrupted; rerun the command."

    @pytest.mark.parametrize(
        "argv",
        [
            ("preflight", "--tasks", "set"),
            ("summarize", "--campaign-id", "finished"),
        ],
        ids=["preflight", "summarize"],
    )
    def test_the_interrupt_hint_of_a_command_that_launches_nothing_is_a_rerun_hint(self, argv):
        args = run_replay_bench.build_parser().parse_args(list(argv))

        assert run_replay_bench._interrupt_hint(args) == f"{argv[0]}: interrupted; rerun the command."

    def test_the_interrupt_hint_of_smoke_names_the_leftovers(
        self, monkeypatch,
    ):
        monkeypatch.setattr(tempfile, "tempdir", "/synthetic/tmp")
        args = run_replay_bench.build_parser().parse_args(["smoke", "--tasks", "set", "--task-id", "t", "--timeout-s", "90"])

        hint = run_replay_bench._interrupt_hint(args)

        assert "wait at least --timeout-s (90 s)" in hint
        assert f"{attempt.REPLAY_FIXTURE_DIR_PREFIX}* under TMPDIR ('/synthetic/tmp')" in hint
        assert "remove by hand" in hint and "projects/" in hint
        assert "resume with" not in hint and "same TMPDIR" not in hint

    def test_the_interrupt_hint_of_run_names_the_resume(self, monkeypatch):
        monkeypatch.setattr(tempfile, "tempdir", "/synthetic/tmp")
        args = run_replay_bench.build_parser().parse_args(
            [*self.RUN_ARGV, "--campaign-id", "interrupted", "--timeout-s", "90"],
        )

        hint = run_replay_bench._interrupt_hint(args)

        assert "resume with --campaign-id interrupted" in hint
        assert "wait at least --timeout-s (90 s)" in hint
        assert "same TMPDIR ('/synthetic/tmp')" in hint

    def test_the_interrupt_hint_names_the_system_temp_dir_when_only_temp_is_set(self, tmp_path, monkeypatch):
        monkeypatch.delenv("TMPDIR", raising=False)
        monkeypatch.delenv("TMP", raising=False)
        monkeypatch.setenv("TEMP", str(tmp_path))
        monkeypatch.setattr(tempfile, "tempdir", None)
        args = run_replay_bench.build_parser().parse_args([*self.RUN_ARGV, "--campaign-id", "interrupted"])

        hint = run_replay_bench._interrupt_hint(args)

        assert f"({tempfile.gettempdir()!r})" in hint
        assert f"({str(tmp_path)!r})" in hint

    def test_a_whole_run_through_main_is_summarized(self, world, local_dir, monkeypatch, capsys):
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

    def test_an_unreadable_environment_at_a_later_blocks_start_exits_two(
        self, tmp_path, local_dir, monkeypatch, capsys,
    ):
        two_task_world = build_replay_world(tmp_path, monkeypatch, task_count=2)
        # The first read pins the campaign, then each block reads at its start and its end.
        readings = iter([READING, READING, READING, review_runner.HarnessInvalidatedError("read timed out")])

        def read_environment_record(*, checkout_root=None):
            reading = next(readings, READING)
            if isinstance(reading, Exception):
                raise reading
            return reading

        monkeypatch.setattr(review_runner, "read_environment_record", read_environment_record)
        monkeypatch.setattr(account, "preflight_repo", lambda repo, **_k: two_task_world.account)
        smoke_pass.write_smoke_pass(local_dir, two_task_world.account.key, READING, MODEL_ID, ("toolkit", "bare"))
        tasks.write_task_set(local_dir / "tasks", "set", two_task_world.tasks)
        launch_stub = two_task_world.launch()
        monkeypatch.setattr(msmr, "_run_claude_to_completion", launch_stub)

        code = main_run(
            "run", "--tasks", "set", "--k", "1", "--cumulative-stop-usd", "1000", "--campaign-id", "unreadable",
            "--budget-cap-usd", "1.0",
        )

        assert code == 2 and "could not read the CLI version" in capsys.readouterr().err
        assert len(launch_stub.calls) == 2, "only the first block's two runs launched"
        spend_path = local_dir / campaign.CAMPAIGNS_DIR_NAME / "unreadable" / "spend.jsonl"
        assert len(spend_path.read_text().splitlines()) == 2

    @pytest.fixture
    def run_files(self, world, local_dir):
        smoke_pass.write_smoke_pass(local_dir, world.account.key, READING, MODEL_ID, ("toolkit", "bare"))
        tasks.write_task_set(local_dir / "tasks", "set", world.tasks)
        return world

    @pytest.fixture
    def run_ready(self, run_files, monkeypatch):
        monkeypatch.setattr(review_runner, "read_environment_record", lambda *, checkout_root=None: READING)
        monkeypatch.setattr(account, "preflight_repo", lambda repo, **_k: run_files.account)
        return run_files

    RUN_ARGV = ("run", "--tasks", "set", "--k", "1", "--cumulative-stop-usd", "1000", "--budget-cap-usd", "1.0")

    def test_an_interrupted_run_prints_the_generated_id_in_its_resume_command(
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

    @staticmethod
    def paths_under(root):
        return sorted(str(path.relative_to(root)) for path in root.rglob("*"))

    @pytest.mark.parametrize("campaign_id", ["", "..", "../x", ".hidden", "a/b"])
    def test_a_run_campaign_id_that_is_not_one_path_component_is_refused(
        self, run_ready, tmp_path, local_dir, monkeypatch, capsys, campaign_id,
    ):
        launch_stub = run_ready.launch()
        monkeypatch.setattr(msmr, "_run_claude_to_completion", launch_stub)
        before = self.paths_under(tmp_path)

        assert main_run(*self.RUN_ARGV, "--campaign-id", campaign_id) == 2

        assert f"invalid campaign ID {campaign_id!r}" in capsys.readouterr().err
        assert launch_stub.calls == [] and self.paths_under(tmp_path) == before
        assert not (local_dir / campaign.CAMPAIGNS_DIR_NAME).exists()

    def test_a_run_campaign_id_of_one_path_component_is_accepted(
        self, run_ready, local_dir, monkeypatch,
    ):
        launch_stub = run_ready.launch()
        monkeypatch.setattr(msmr, "_run_claude_to_completion", launch_stub)

        assert main_run(*self.RUN_ARGV, "--campaign-id", "accepted.1") == 0

        assert launch_stub.calls != [] and (local_dir / campaign.CAMPAIGNS_DIR_NAME / "accepted.1").is_dir()

    @pytest.mark.parametrize("campaign_id", ["", "..", "../x", ".hidden", "a/b"])
    def test_a_summarize_campaign_id_that_is_not_one_path_component_is_refused(
        self, local_dir, tmp_path, capsys, campaign_id,
    ):
        """A manifest where `../x` would land makes the summary succeed if the ID is not validated."""
        (local_dir / "x").mkdir(parents=True)
        (local_dir / "x" / "manifest.json").write_text("{}")
        (local_dir / campaign.CAMPAIGNS_DIR_NAME).mkdir()
        before = self.paths_under(tmp_path)

        assert main_run("summarize", "--campaign-id", campaign_id) == 2

        assert f"invalid campaign ID {campaign_id!r}" in capsys.readouterr().err
        assert self.paths_under(tmp_path) == before

    @pytest.mark.parametrize("k", ["0", "-1"])
    def test_a_k_below_one_is_refused(
        self, run_ready, local_dir, monkeypatch, capsys, k,
    ):
        launch_stub = run_ready.launch()
        monkeypatch.setattr(msmr, "_run_claude_to_completion", launch_stub)
        argv = list(self.RUN_ARGV)
        argv[argv.index("--k") + 1] = k

        assert main_run(*argv, "--campaign-id", "no-k") == 2

        assert "--k must be at least 1" in capsys.readouterr().err
        assert launch_stub.calls == [] and not (local_dir / campaign.CAMPAIGNS_DIR_NAME).exists()

    def test_an_empty_task_set_is_refused(
        self, run_ready, local_dir, monkeypatch, capsys,
    ):
        tasks.write_task_set(local_dir / "tasks", "empty", [])
        launch_stub = run_ready.launch()
        monkeypatch.setattr(msmr, "_run_claude_to_completion", launch_stub)
        argv = [("empty" if part == "set" else part) for part in self.RUN_ARGV]

        assert main_run(*argv, "--campaign-id", "no-tasks") == 2

        assert "task set 'empty' holds no tasks" in capsys.readouterr().err
        assert launch_stub.calls == [] and not (local_dir / campaign.CAMPAIGNS_DIR_NAME).exists()

    def test_a_smoke_task_id_the_set_lacks_is_refused(
        self, run_ready, monkeypatch, capsys,
    ):
        launch_stub = run_ready.launch()
        monkeypatch.setattr(msmr, "_run_claude_to_completion", launch_stub)
        absent_task_id = "00000000-000000000000"
        assert absent_task_id not in {task.id for task in run_ready.tasks}
        argv = self.launching_argv(run_ready, "smoke")
        argv[argv.index("--task-id") + 1] = absent_task_id

        assert main_run(*argv) == 2

        assert f"holds no task {absent_task_id!r}" in capsys.readouterr().err
        assert launch_stub.calls == []

    @pytest.mark.timing
    def test_direnv_timeout_s_reaches_the_probe(self, run_files, tmp_path, monkeypatch, capsys):
        fake_direnv = write_executable(tmp_path / "bin" / "direnv", f"exec sleep {SLEEP_PAST_TIMEOUT_S}\n")
        monkeypatch.setenv("PATH", f"{fake_direnv.parent}:{os.environ['PATH']}")
        started = time.monotonic()

        code = main_run("--local-dir", str(run_files.local_dir), "preflight", "--tasks", "set", "--direnv-timeout-s", "1")

        assert code == 2 and "timed out after 1 s; raise --direnv-timeout-s" in capsys.readouterr().err
        assert time.monotonic() - started < SLEEP_REFUSAL_BOUND_S, "the kill did not reach the sleeper"

    def test_fixture_git_timeout_s_reaches_each_git_dir_call(
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
        assert limits and set(limits) == {37.5}

    def test_a_failing_environment_read_prints_only_the_exit_status(
        self, run_files, tmp_path, monkeypatch, capsys,
    ):
        # The real `read_environment_record` runs, with a `claude` that fails. Only the preflight is stubbed.
        write_executable(tmp_path / "bin" / "claude", 'echo "proxy http://user:sentinel-credential@proxy.invalid" >&2\nexit 3\n')
        monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:{os.environ['PATH']}")
        monkeypatch.setattr(account, "preflight_repo", lambda repo, **_k: run_files.account)

        code = main_run("--local-dir", str(run_files.local_dir), *self.RUN_ARGV)

        err = capsys.readouterr().err
        assert code == 2 and "exit 3" in err and "sentinel-credential" not in err

    # Gated on the feature, not `sys.version_info`.
    @pytest.mark.skipif(
        "color" not in inspect.signature(argparse.ArgumentParser).parameters,
        reason="argparse colours its output only where ArgumentParser accepts `color` (Python 3.14+)",
    )
    def test_argparses_own_output_is_not_escaped_under_forced_colour(self, monkeypatch, capsys):
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
        # Colour is on, so a raw escape byte must reach the output; a colour-off run would pass vacuously.
        assert "\x1b" in help_output.out
        literal_escape = "\\x1b"
        assert literal_escape not in help_output.out + help_output.err + error_output.out + error_output.err
        assert "usage:" in help_output.out and "usage:" in error_output.err

    def test_hostile_stdout_text_is_escaped(self, monkeypatch, capsys):
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
    def test_a_non_finite_stop_is_refused_with_exit_two(self, run_ready, monkeypatch, flag, value):
        launch_stub = run_ready.launch()
        monkeypatch.setattr(msmr, "_run_claude_to_completion", launch_stub)
        stops = {"--cumulative-stop-usd": "1000", "--account-stop-usd": "1000", flag: value}
        stop_flags = [part for item in stops.items() for part in item]
        argv = ["run", "--tasks", "set", "--k", "1", "--budget-cap-usd", "1.0", *stop_flags]

        assert main_run(*argv) == 2
        assert launch_stub.calls == []

    @pytest.fixture
    def campaign_config_seen_by_run(self, run_ready, monkeypatch):
        seen = []
        monkeypatch.setattr(campaign, "run_campaign", lambda config, *_args, **_kwargs: seen.append(config) or 0)
        return seen

    def test_run_flags_land_in_the_campaign_config(self, campaign_config_seen_by_run):
        argv = [
            "run", "--tasks", "set", "--k", "2", "--budget-cap-usd", "1.0", "--cumulative-stop-usd", "900",
            "--account-stop-usd", "500", "--seed", "7", "--allow-early-stop", "--arm", "bare",
        ]

        assert main_run(*argv) == 0

        (config,) = campaign_config_seen_by_run
        assert (config.cumulative_stop_usd, config.account_stop_usd, config.seed) == (900.0, 500.0, 7)
        assert config.allow_early_stop is True and config.arm_names == ("bare",)

    def test_run_without_the_optional_flags_takes_the_defaults(
        self, campaign_config_seen_by_run,
    ):
        assert main_run(*self.RUN_ARGV) == 0

        (config,) = campaign_config_seen_by_run
        assert config.account_stop_usd == config.cumulative_stop_usd == 1000.0
        assert config.allow_early_stop is False and config.seed == run_replay_bench.DEFAULT_SEED
        assert config.arm_names == arms.DEFAULT_ARM_NAMES

    @staticmethod
    def launching_argv(world, subcommand):
        return {
            "smoke": ["smoke", "--tasks", "set", "--task-id", world.tasks[0].id, "--budget-cap-usd", "1.0"],
            "run": ["run", "--tasks", "set", "--k", "1", "--cumulative-stop-usd", "1000", "--budget-cap-usd", "1.0"],
        }[subcommand]

    @pytest.mark.parametrize("value", ["0", "-5"])
    @pytest.mark.parametrize("subcommand", ["smoke", "run"])
    def test_a_non_positive_timeout_s_is_refused(
        self, run_ready, monkeypatch, capsys, subcommand, value,
    ):
        launch_stub = run_ready.launch()
        monkeypatch.setattr(msmr, "_run_claude_to_completion", launch_stub)

        assert main_run(*self.launching_argv(run_ready, subcommand), "--timeout-s", value) == 2
        assert "--timeout-s" in capsys.readouterr().err
        assert launch_stub.calls == []

    @pytest.mark.parametrize("subcommand", ["smoke", "run"])
    def test_the_same_argv_with_a_positive_timeout_s_reaches_the_launch(self, run_ready, monkeypatch, subcommand):
        launch_stub = run_ready.launch()
        monkeypatch.setattr(msmr, "_run_claude_to_completion", launch_stub)

        main_run(*self.launching_argv(run_ready, subcommand), "--timeout-s", "30")

        assert launch_stub.calls != []

    @pytest.mark.parametrize("subcommand", ["smoke", "run"])
    def test_a_held_run_store_lock_exits_one(
        self, run_ready, local_dir, monkeypatch, capsys, subcommand,
    ):
        """A double start of one campaign must read as "another run in progress" (1), not a refusal (2) or a traceback."""
        launch_stub = run_ready.launch()
        monkeypatch.setattr(msmr, "_run_claude_to_completion", launch_stub)
        fixed_smoke_id = uuid.UUID(int=1)
        monkeypatch.setattr(smoke, "uuid", types.SimpleNamespace(uuid4=lambda: fixed_smoke_id))
        campaign_id = {"smoke": "smoke-00000000", "run": "held"}[subcommand]
        argv = self.launching_argv(run_ready, subcommand)
        if subcommand == "run":
            argv += ["--campaign-id", campaign_id]
        holder = review_runner.RunStore(local_dir / campaign.CAMPAIGNS_DIR_NAME / campaign_id / campaign.STORE_DIR_NAME)
        holder.acquire_lock()
        try:
            code = main_run(*argv)
        finally:
            holder.release_lock()

        assert code == 1
        error = capsys.readouterr().err
        assert f"held by pid {os.getpid()}" in error and "another run on this store is still in progress" in error
        assert launch_stub.calls == []

    def test_the_campaign_id_line_is_flushed_to_a_redirected_stdout(
        self, run_ready, monkeypatch, tmp_path,
    ):
        """Kills: dropping `flush=True` from the `campaign <id>` print. A detached run's stdout is a block-buffered
        file, so an unflushed line is lost with the process."""
        monkeypatch.setattr(msmr, "_run_claude_to_completion", run_ready.launch())
        redirected = tmp_path / "stdout.txt"
        seen_while_running = []

        def observe(*_args, **_kwargs):
            seen_while_running.append(redirected.read_text())
            return 0

        monkeypatch.setattr(campaign, "run_campaign", observe)
        with redirected.open("w", buffering=8192) as stream:
            monkeypatch.setattr(sys, "stdout", stream)
            code = main_run(*self.RUN_ARGV, "--campaign-id", "detached")

        assert code == 0
        assert len(seen_while_running) == 1 and seen_while_running[0].endswith("campaign detached\n")

    def test_a_hostile_refusal_reaches_stderr_escaped(
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
