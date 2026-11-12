"""Tests for what a replay run's diff and metrics see, and for the hook-log read. Real git builds the
fixtures, and a scripted launch makes each agent edit. No test launches `claude`.
"""
from __future__ import annotations

import os
import subprocess
import time
import types
from pathlib import Path

import pytest
from replay_bench import arms, attempt, checks
from replay_bench.runner import execute_run
from review_bench import runner as review_runner
from test_replay_bench_checks_support import call_with_deadline
from test_replay_bench_runner_support import (
    Scenario,
    build_replay_world,
    default_edit,
    make_block,
    make_settings,
)

COMMIT_IDENTITY = ["-c", "user.name=agent", "-c", "user.email=agent@example.com", "-c", "commit.gpgsign=false"]


def run_with_edit(world, edit, arm_name=arms.BARE.name):
    launch_stub = world.launch(lambda _n, _a: Scenario(edit=edit))
    return execute_run(make_settings(world, launch_stub), make_block(world), arm_name, 0), launch_stub


def agent_git(fixture_dir: Path, *args: str) -> None:
    subprocess.run(["git", *COMMIT_IDENTITY, *args], cwd=fixture_dir, check=True, capture_output=True)


def commit_edit(fixture_dir: Path) -> None:
    default_edit(fixture_dir)
    agent_git(fixture_dir, "add", "-A")
    agent_git(fixture_dir, "commit", "-q", "-m", "agent commit")


def commit_then_reset_edit(fixture_dir: Path) -> None:
    commit_edit(fixture_dir)
    agent_git(fixture_dir, "reset", "-q", "HEAD~1")


def branch_edit(fixture_dir: Path) -> None:
    default_edit(fixture_dir)
    agent_git(fixture_dir, "checkout", "-q", "-b", "agent-branch")


class TestTheAgentRewritesItsOwnHistory:
    @pytest.mark.parametrize("edit", [commit_edit, commit_then_reset_edit, branch_edit], ids=["commit", "mixed-reset", "branch"])
    def test_the_diff_equals_the_uncommitted_edit_diff(self, world, edit):
        reference, _stub = run_with_edit(world, default_edit)
        outcome, _stub = run_with_edit(world, edit)

        assert outcome.record.missing_reason is None
        assert outcome.diff_text == reference.diff_text


class TestAHostileFixture:
    @pytest.fixture
    def hostile_edit(self, tmp_path):
        sentinel_dir = tmp_path / "sentinels"
        sentinel_dir.mkdir()
        outside_fifo = tmp_path / "outside.fifo"
        os.mkfifo(outside_fifo)

        def edit(fixture_dir: Path) -> None:
            default_edit(fixture_dir)
            git_dir = fixture_dir / ".git"
            with (git_dir / "config").open("a") as config:
                config.write(
                    f"[core]\n\tfsmonitor = touch {sentinel_dir / 'fsmonitor'}\n"
                    f"[filter \"hostile\"]\n\tclean = touch {sentinel_dir / 'filter'}\n"
                )
            (git_dir / "info" / "attributes").write_text("*.py filter=hostile\n")
            (fixture_dir / ".gitattributes").write_text("*.py filter=hostile\n")
            os.mkfifo(fixture_dir / "pipe.fifo")
            os.symlink(outside_fifo, fixture_dir / "link-to-fifo")

        return edit, sentinel_dir

    @pytest.mark.timing
    def test_no_planted_command_runs_the_fifo_is_skipped_and_the_symlink_is_a_non_file_entry(self, world, hostile_edit):
        edit, sentinel_dir = hostile_edit

        outcome, _stub = call_with_deadline(
            lambda: run_with_edit(world, edit), deadline_s=30, hang_message="post-run analysis hung, possibly on the FIFO",
        )

        assert outcome.record.missing_reason is None
        assert list(sentinel_dir.iterdir()) == []
        assert outcome.record.metrics["non_file_entries"] == 1


def nested_repo_edit(commit: bool):
    def edit(fixture_dir: Path) -> None:
        default_edit(fixture_dir)
        nested = fixture_dir / "vendor" / "nested"
        nested.mkdir(parents=True)
        agent_git(nested, "init", "-q")
        (nested / "file.txt").write_text("content\n")
        if commit:
            agent_git(nested, "add", "-A")
            agent_git(nested, "commit", "-q", "-m", "nested")

    return edit


class TestUntrackedTrees:
    def test_a_file_under_a_path_the_base_tree_ignores_lands_in_ignored_paths(self, tmp_path, monkeypatch):
        world = build_replay_world(tmp_path, monkeypatch, base_files={".gitignore": "ignored_dir/\n"})

        def edit(fixture_dir: Path) -> None:
            default_edit(fixture_dir)
            (fixture_dir / "ignored_dir").mkdir()
            (fixture_dir / "ignored_dir" / "log.txt").write_text("x\n")

        outcome, _stub = run_with_edit(world, edit)

        assert outcome.record.ignored_paths == ["ignored_dir/"] and outcome.record.ignored_paths_count == 1
        assert "log.txt" not in outcome.diff_text

    def test_a_nested_repository_with_a_commit_counts_as_a_non_file_entry(self, world):
        outcome, _stub = run_with_edit(world, nested_repo_edit(commit=True))

        assert outcome.record.missing_reason is None
        assert outcome.record.metrics["non_file_entries"] == 1

    def test_a_nested_repository_with_no_commit_records_analysis_error(self, world):
        outcome, stub = run_with_edit(world, nested_repo_edit(commit=False))

        assert outcome.record.missing_reason == attempt.REASON_ANALYSIS_ERROR and len(stub.calls) == 1


class TestAgentEditsReachTheDiffOnlyAsIntended:
    def test_a_deleted_file_shows_as_a_deletion_and_the_run_is_valid(self, world):
        def edit(fixture_dir: Path) -> None:
            (fixture_dir / "src" / "app.py").unlink()

        outcome, _stub = run_with_edit(world, edit)

        assert outcome.record.missing_reason is None
        assert "deleted file mode" in outcome.diff_text

    def test_an_added_binary_is_valid_and_adds_no_classified_line(self, world):
        def edit(fixture_dir: Path) -> None:
            (fixture_dir / "blob.bin").write_bytes(b"\x00\x01\x02binary")

        outcome, _stub = run_with_edit(world, edit)

        assert outcome.record.missing_reason is None
        assert sum(outcome.record.metrics["line_counts"]["non_test"].values()) == 0

    def test_a_write_under_the_excluded_claude_dir_never_reaches_the_diff_or_ignored_paths(self, world):
        def edit(fixture_dir: Path) -> None:
            default_edit(fixture_dir)
            (fixture_dir / ".claude" / "notes.md").write_text("notes\n")

        outcome, _stub = run_with_edit(world, edit)

        assert ".claude" not in outcome.diff_text
        assert outcome.record.ignored_paths == []


class SleepLog(list):
    """The sleeps a patched `time.sleep` recorded, with the hook to run on each."""

    def __init__(self) -> None:
        super().__init__()
        self.on_sleep = {"call": lambda count: None}


class TestTheHookLogRead:
    """`wait_for_hook_log_to_settle` mirrors `wait_for_session_flush`'s two-poll rule for a file that function
    does not watch, so it carries its own cases."""

    @pytest.fixture
    def log_path(self, tmp_path):
        path = tmp_path / "instructions-loaded.jsonl"
        path.write_text('{"a": 1}\n')
        return path

    @pytest.fixture
    def sleeps(self, monkeypatch):
        recorded = SleepLog()

        def fake_sleep(seconds):
            recorded.append(seconds)
            recorded.on_sleep["call"](len(recorded))

        monkeypatch.setattr(attempt.time, "sleep", fake_sleep)
        monkeypatch.setattr(review_runner, "SESSION_FLUSH_TIMEOUT_S", 60.0)
        monkeypatch.setattr(review_runner, "SESSION_FLUSH_POLL_INTERVAL_S", 0.25)
        return recorded

    def test_a_log_whose_size_holds_returns_after_one_sleep(self, log_path, sleeps):
        attempt.wait_for_hook_log_to_settle(log_path, timed_out=False)
        assert sleeps == [0.25]

    def test_a_line_appended_during_the_first_interval_is_read_after_a_second_sample(self, log_path, sleeps):
        def append_once(count):
            if count == 1:
                with log_path.open("a") as log:
                    log.write('{"b": 2}\n')

        sleeps.on_sleep["call"] = append_once
        attempt.wait_for_hook_log_to_settle(log_path, timed_out=False)

        assert len(sleeps) == 2

    def test_a_log_that_grows_on_every_poll_stops_at_the_timeout_and_a_torn_tail_is_invalid(
        self, tmp_path, monkeypatch,
    ):
        log_path = tmp_path / "instructions-loaded.jsonl"
        log_path.write_text("")
        clock = {"now": 0.0}
        sleeps = []

        def grow_torn_then_advance(seconds):
            sleeps.append(seconds)
            clock["now"] += seconds
            with log_path.open("a") as log:
                log.write('{"session_id": "s')

        monkeypatch.setattr(
            attempt, "time", types.SimpleNamespace(monotonic=lambda: clock["now"], sleep=grow_torn_then_advance),
        )
        monkeypatch.setattr(review_runner, "SESSION_FLUSH_TIMEOUT_S", 1.0)
        monkeypatch.setattr(review_runner, "SESSION_FLUSH_POLL_INTERVAL_S", 0.25)

        call_with_deadline(
            lambda: attempt.wait_for_hook_log_to_settle(log_path, timed_out=False), deadline_s=10,
            hang_message="the wait never stopped although the log grew past the flush timeout",
        )

        assert len(sleeps) == 4, "it kept sampling until the fake clock reached the one-second timeout, then stopped"
        assert checks.parse_instruction_log(log_path, session_id="s").reason == checks.REASON_INSTRUMENT_LOG_INVALID

    def test_a_log_absent_at_the_first_poll_and_present_at_the_second_counts_as_growth(self, tmp_path, sleeps):
        log_path = tmp_path / "late-log.jsonl"

        def create_log(count):
            if count == 1:
                log_path.write_text('{"a": 1}\n')

        sleeps.on_sleep["call"] = create_log
        attempt.wait_for_hook_log_to_settle(log_path, timed_out=False)

        assert len(sleeps) == 2

    def test_a_log_that_never_appears_does_not_raise(self, tmp_path, sleeps):
        attempt.wait_for_hook_log_to_settle(tmp_path / "never.jsonl", timed_out=False)
        assert len(sleeps) == 1

    def test_a_timed_out_attempt_makes_no_sleep(self, log_path, sleeps):
        attempt.wait_for_hook_log_to_settle(log_path, timed_out=True)
        assert sleeps == []

    @pytest.mark.timing
    def test_the_real_flush_wait_ends_once_the_transcript_and_sidecar_stop_growing(self, world, monkeypatch):
        monkeypatch.setattr(review_runner, "SESSION_FLUSH_POLL_INTERVAL_S", 0.25)
        monkeypatch.setattr(review_runner, "SESSION_FLUSH_TIMEOUT_S", 10.0)
        started = time.monotonic()

        outcome, _stub = run_with_edit(world, default_edit)

        assert outcome.record.missing_reason is None
        assert time.monotonic() - started < 8.0
