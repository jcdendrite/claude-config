"""Tests for transcript_analysis/fail_seq.py: TEST_RUNNER_RE, FAILED_RE, _longest_fail_streak, and
cmd_fail_seq's attribution and run-pairing behavior (TestFailSeqEndToEnd, TestFailSeqAttribution), all
calling cmd_fail_seq itself rather than a test-local reimplementation of its pairing logic.
"""
from transcript_analysis import fail_seq

from ._session_signal_helpers import _SONNET, _branch_args
from .conftest import _asst, _attr_ts, _bash_use, _tool_result, _user_msg, _write_jsonl


def _run_fail_seq(branches: str, capsys) -> str:
    fail_seq.cmd_fail_seq(_branch_args(branches=branches))
    return capsys.readouterr().out


class TestFailSeqRegexes:
    def test_failed_re_extracts_count(self):
        assert fail_seq.FAILED_RE.findall("5 failed, 20 passed") == ["5"]

    def test_failed_re_finds_all_matches_for_caller_max(self):
        matches = [int(m) for m in fail_seq.FAILED_RE.findall("3 failed\n105 failed\n2 failed")]
        assert max(matches) == 105

    def test_test_runner_re_matches_npm_run_test(self):
        assert fail_seq.TEST_RUNNER_RE.search("npm run test")

    def test_test_runner_re_matches_npm_run_verify(self):
        assert fail_seq.TEST_RUNNER_RE.search("npm run verify")

    def test_test_runner_re_matches_npm_run_lint(self):
        assert fail_seq.TEST_RUNNER_RE.search("npm run lint")

    def test_test_runner_re_matches_pytest(self):
        assert fail_seq.TEST_RUNNER_RE.search("pytest claude/.claude/")

    def test_test_runner_re_matches_vitest(self):
        assert fail_seq.TEST_RUNNER_RE.search("npx vitest --run")

    def test_test_runner_re_matches_ruff_check(self):
        assert fail_seq.TEST_RUNNER_RE.search("ruff check .")

    def test_test_runner_re_matches_deno_test(self):
        assert fail_seq.TEST_RUNNER_RE.search("deno test")

    def test_test_runner_re_does_not_match_git_commands(self):
        assert not fail_seq.TEST_RUNNER_RE.search("git status")

    def test_test_runner_re_does_not_match_ls(self):
        assert not fail_seq.TEST_RUNNER_RE.search("ls -la")

    def test_test_runner_re_does_not_match_echo(self):
        assert not fail_seq.TEST_RUNNER_RE.search("echo hello")


class TestLongestFailStreak:
    def test_empty_list(self):
        assert fail_seq._longest_fail_streak([]) == 0

    def test_all_passing(self):
        assert fail_seq._longest_fail_streak([False, False, False]) == 0

    def test_all_failing(self):
        assert fail_seq._longest_fail_streak([True, True, True]) == 3

    def test_single_spike_then_green(self):
        assert fail_seq._longest_fail_streak([False, True, False, False]) == 1

    def test_longest_streak_not_first(self):
        assert fail_seq._longest_fail_streak([False, True, True, False, True]) == 2

    def test_streak_at_end(self):
        assert fail_seq._longest_fail_streak([False, False, True, True, True]) == 3

    def test_oscillation_no_run_longer_than_one(self):
        assert fail_seq._longest_fail_streak([True, False, True, False, True]) == 1


class TestFailSeqEndToEnd:
    """Each test calls cmd_fail_seq itself, so a regression in its real pairing/filtering logic (the
    sidechain skip, the branch-reset trigger, the max-count selection) fails these tests directly
    instead of only a test-local reimplementation that never runs the production code."""

    def test_extracts_fail_and_pass_counts(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst(_SONNET, branch="feat", ts=_attr_ts(1), content=[_bash_use("t1", "pytest")]),
            _user_msg([_tool_result("t1", "3 failed, 17 passed")], branch="feat", ts=_attr_ts(2)),
            _asst(_SONNET, branch="feat", ts=_attr_ts(3), content=[_bash_use("t2", "pytest")]),
            _user_msg([_tool_result("t2", "all passed")], branch="feat", ts=_attr_ts(4)),
        ])
        out = _run_fail_seq("feat", capsys)
        assert "### feat" in out
        assert "Sequence: 3 0" in out

    def test_sidechain_bash_calls_not_tracked(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst(_SONNET, branch="feat", ts=_attr_ts(1), sidechain=True, content=[_bash_use("ts", "pytest")]),
            _user_msg([_tool_result("ts", "7 failed")], branch="feat", ts=_attr_ts(2)),
        ])
        out = _run_fail_seq("feat", capsys)
        assert "No test runs found for the specified branches." in out

    def test_non_test_bash_not_tracked(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst(_SONNET, branch="feat", ts=_attr_ts(1), content=[_bash_use("t1", "git status")]),
            _user_msg([_tool_result("t1", "M file.txt")], branch="feat", ts=_attr_ts(2)),
        ])
        out = _run_fail_seq("feat", capsys)
        assert "No test runs found for the specified branches." in out

    def test_pending_cleared_on_branch_change(self, fake_projects, capsys):
        """tool_use from branch-a is not matched to tool_result after branch switches."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst(_SONNET, branch="feat-a", ts=_attr_ts(1), content=[_bash_use("t1", "pytest")]),
            _asst(_SONNET, branch="feat-b", ts=_attr_ts(2)),  # branch switches → pending cleared
            _user_msg([_tool_result("t1", "5 failed")], branch="feat-b", ts=_attr_ts(3)),
        ])
        # t1 was from feat-a; after branch switch pending clears → not attributed to feat-a
        out = _run_fail_seq("feat-a", capsys)
        assert "No test runs found for the specified branches." in out

    def test_max_count_used_when_multiple_failed_in_output(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="feat", ts=_attr_ts(1), content=[_bash_use("t1", "npm run test")]),
            _user_msg([_tool_result("t1", "2 failed\n105 failed\n1 failed")], branch="feat", ts=_attr_ts(2)),
        ])
        out = _run_fail_seq("feat", capsys)
        assert "Sequence: 105" in out
        assert "opus" in out and "1 failing (100.0%)" in out


class TestFailSeqAttribution:
    def test_prefix_test_run_with_its_result_in_the_prefix_counts_under_the_branch_entered(
        self, fake_projects, capsys
    ):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst(_SONNET, branch="main", ts=_attr_ts(1), content=[_bash_use("t1", "pytest")]),
            _user_msg([_tool_result("t1", "3 failed, 17 passed")], branch="main", ts=_attr_ts(2)),
            _asst(_SONNET, branch="feature-x", ts=_attr_ts(5)),
        ])
        out = _run_fail_seq("feature-x", capsys)
        assert "### feature-x" in out
        assert "Sequence: 3" in out

    def test_test_run_spanning_the_branch_switch_is_dropped_by_the_literal_tag_reset(
        self, fake_projects, capsys
    ):
        """The pending-pair reset marks a physical checkout change, so it reads the
        record's own tag: the tool_use on the last `main` record is not paired with a
        tool_result on the first feature-x record."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst(_SONNET, branch="main", ts=_attr_ts(1), content=[_bash_use("t1", "pytest")]),
            _user_msg([_tool_result("t1", "3 failed, 17 passed")], branch="feature-x", ts=_attr_ts(5)),
        ])
        out = _run_fail_seq("feature-x", capsys)
        assert "No test runs found for the specified branches." in out
