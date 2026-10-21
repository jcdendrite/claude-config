"""Tests for transcript_analysis/fail_seq.py: TEST_RUNNER_RE, FAILED_RE, _longest_fail_streak, cmd_fail_seq's
pre-branch attribution, and five TestFailSeqEndToEnd tests that exercise a test-local copy of the run-pairing loop
(_collect_runs) rather than cmd_fail_seq.
"""
from transcript_analysis import corpus, fail_seq, render, scope

from ._session_signal_helpers import _SONNET, _branch_args
from .conftest import _asst, _attr_ts, _bash_use, _tool_result, _user_msg, _write_jsonl


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


def _collect_runs(target_branch: str) -> list[tuple[str, int]]:
    """Extract (model_family, failed_count) pairs for target_branch using the module's core logic."""
    runs: list[tuple[str, int]] = []
    for _jsonl, records in corpus.iter_sessions(scope.PROJECTS_DIR):
        pending: dict[str, str] = {}
        current_branch: str = ""
        for rec in records:
            branch = rec.get("gitBranch") or ""
            if branch != current_branch:
                pending.clear()
                current_branch = branch
            if branch != target_branch or bool(rec.get("isSidechain")):
                continue
            rtype = rec.get("type", "")
            msg = rec.get("message") or {}
            if rtype == "assistant":
                fam = render._fam(msg.get("model", ""))
                for block in (msg.get("content") or []):
                    if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "Bash":
                        cmd = (block.get("input") or {}).get("command", "")
                        if fail_seq.TEST_RUNNER_RE.search(cmd):
                            pending[block["id"]] = fam
            elif rtype in ("user", "human"):
                content = msg.get("content") or []
                for block in (content if isinstance(content, list) else []):
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        tid = block.get("tool_use_id", "")
                        if tid in pending:
                            fam = pending.pop(tid)
                            text = render._content_text(block.get("content", ""))
                            counts = [int(m) for m in fail_seq.FAILED_RE.findall(text)]
                            runs.append((fam, max(counts) if counts else 0))
    return runs


class TestFailSeqEndToEnd:
    def test_extracts_fail_and_pass_counts(self, fake_projects):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="feat", content=[_bash_use("t1", "pytest")]),
            _user_msg([_tool_result("t1", "3 failed, 17 passed")], branch="feat"),
            _asst("claude-sonnet-4-6", branch="feat", content=[_bash_use("t2", "pytest")]),
            _user_msg([_tool_result("t2", "all passed")], branch="feat"),
        ])
        assert _collect_runs("feat") == [("sonnet", 3), ("sonnet", 0)]

    def test_sidechain_bash_calls_not_tracked(self, fake_projects):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="feat", sidechain=True, content=[_bash_use("ts", "pytest")]),
            _user_msg([_tool_result("ts", "7 failed")], branch="feat"),
        ])
        assert _collect_runs("feat") == []

    def test_non_test_bash_not_tracked(self, fake_projects):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="feat", content=[_bash_use("t1", "git status")]),
            _user_msg([_tool_result("t1", "M file.txt")], branch="feat"),
        ])
        assert _collect_runs("feat") == []

    def test_pending_cleared_on_branch_change(self, fake_projects):
        """tool_use from branch-a is not matched to tool_result after branch switches."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="feat-a", content=[_bash_use("t1", "pytest")]),
            _asst("claude-sonnet-4-6", branch="feat-b"),  # branch switches → pending cleared
            _user_msg([_tool_result("t1", "5 failed")], branch="feat-b"),
        ])
        # t1 was from feat-a; after branch switch pending clears → not attributed to feat-a
        assert _collect_runs("feat-a") == []

    def test_max_count_used_when_multiple_failed_in_output(self, fake_projects):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="feat", content=[_bash_use("t1", "npm run test")]),
            _user_msg([_tool_result("t1", "2 failed\n105 failed\n1 failed")], branch="feat"),
        ])
        runs = _collect_runs("feat")
        assert runs == [("opus", 105)]


class TestFailSeqAttribution:
    @staticmethod
    def _run_fail_seq(capsys) -> str:
        fail_seq.cmd_fail_seq(_branch_args(branches="feature-x"))
        return capsys.readouterr().out

    def test_prefix_test_run_with_its_result_in_the_prefix_counts_under_the_branch_entered(
        self, fake_projects, capsys
    ):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst(_SONNET, branch="main", ts=_attr_ts(1), content=[_bash_use("t1", "pytest")]),
            _user_msg([_tool_result("t1", "3 failed, 17 passed")], branch="main", ts=_attr_ts(2)),
            _asst(_SONNET, branch="feature-x", ts=_attr_ts(5)),
        ])
        out = self._run_fail_seq(capsys)
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
        out = self._run_fail_seq(capsys)
        assert "No test runs found for the specified branches." in out
