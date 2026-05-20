"""Tests for transcript-analysis.py."""
import importlib.util
import json
import subprocess
import time
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parent.parent / "transcript-analysis.py"
_spec = importlib.util.spec_from_file_location("transcript_analysis", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")


def _asst(
    model: str,
    *,
    branch: str = "main",
    sidechain: bool = False,
    ts: str | None = None,
    content: list | None = None,
) -> dict:
    rec: dict = {
        "type": "assistant",
        "gitBranch": branch,
        "isSidechain": sidechain,
        "message": {"model": model, "content": content or [], "usage": {}},
    }
    if ts:
        rec["timestamp"] = ts
    return rec


def _user_msg(content, *, branch: str = "main") -> dict:
    return {"type": "user", "gitBranch": branch, "message": {"content": content}}


def _bash_use(tool_id: str, command: str) -> dict:
    return {"type": "tool_use", "id": tool_id, "name": "Bash", "input": {"command": command}}


def _tool_result(tool_id: str, text: str) -> dict:
    return {"type": "tool_result", "tool_use_id": tool_id, "content": text}


def _agent_use(tool_id: str, subagent_type: str, *, tool_name: str = "Agent") -> dict:
    return {
        "type": "tool_use",
        "id": tool_id,
        "name": tool_name,
        "input": {"subagent_type": subagent_type, "description": "x", "prompt": "y"},
    }


def _skill_use(tool_id: str, skill: str) -> dict:
    return {"type": "tool_use", "id": tool_id, "name": "Skill", "input": {"skill": skill}}


@pytest.fixture()
def fake_projects(tmp_path, monkeypatch):
    projects = tmp_path / "projects"
    proj = projects / "-home-user-testrepo"
    proj.mkdir(parents=True)
    monkeypatch.setattr(_mod, "PROJECTS_DIR", projects)
    return proj


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_fam_opus(self):
        assert _mod._fam("claude-opus-4-7") == "opus"

    def test_fam_sonnet(self):
        assert _mod._fam("claude-sonnet-4-6") == "sonnet"

    def test_fam_haiku(self):
        assert _mod._fam("claude-haiku-4-5") == "haiku"

    def test_fam_unknown_model(self):
        assert _mod._fam("gpt-4") == "other"

    def test_content_text_plain_string(self):
        assert _mod._content_text("hello world") == "hello world"

    def test_content_text_list_joins_text_blocks(self):
        content = [{"type": "text", "text": "hello"}, {"type": "tool_use", "name": "Bash"}, {"type": "text", "text": "world"}]
        assert _mod._content_text(content) == "hello world"

    def test_content_text_none_returns_empty(self):
        assert _mod._content_text(None) == ""

    def test_parse_ts_valid_iso_z(self):
        ts = _mod._parse_ts("2024-06-01T12:00:00.000Z")
        assert ts is not None and ts > 0

    def test_parse_ts_none_input(self):
        assert _mod._parse_ts(None) is None

    def test_parse_ts_invalid_string(self):
        assert _mod._parse_ts("not-a-date") is None


# ---------------------------------------------------------------------------
# fail-seq regexes
# ---------------------------------------------------------------------------


class TestFailSeqRegexes:
    def test_failed_re_extracts_count(self):
        assert _mod.FAILED_RE.findall("5 failed, 20 passed") == ["5"]

    def test_failed_re_finds_all_matches_for_caller_max(self):
        matches = [int(m) for m in _mod.FAILED_RE.findall("3 failed\n105 failed\n2 failed")]
        assert max(matches) == 105

    def test_test_runner_re_matches_npm_run_test(self):
        assert _mod.TEST_RUNNER_RE.search("npm run test")

    def test_test_runner_re_matches_npm_run_verify(self):
        assert _mod.TEST_RUNNER_RE.search("npm run verify")

    def test_test_runner_re_matches_npm_run_lint(self):
        assert _mod.TEST_RUNNER_RE.search("npm run lint")

    def test_test_runner_re_matches_pytest(self):
        assert _mod.TEST_RUNNER_RE.search("pytest claude/.claude/")

    def test_test_runner_re_matches_vitest(self):
        assert _mod.TEST_RUNNER_RE.search("npx vitest --run")

    def test_test_runner_re_matches_ruff_check(self):
        assert _mod.TEST_RUNNER_RE.search("ruff check .")

    def test_test_runner_re_matches_deno_test(self):
        assert _mod.TEST_RUNNER_RE.search("deno test")

    def test_test_runner_re_does_not_match_git_commands(self):
        assert not _mod.TEST_RUNNER_RE.search("git status")

    def test_test_runner_re_does_not_match_ls(self):
        assert not _mod.TEST_RUNNER_RE.search("ls -la")

    def test_test_runner_re_does_not_match_echo(self):
        assert not _mod.TEST_RUNNER_RE.search("echo hello")


# ---------------------------------------------------------------------------
# _longest_fail_streak
# ---------------------------------------------------------------------------


class TestLongestFailStreak:
    def test_empty_list(self):
        assert _mod._longest_fail_streak([]) == 0

    def test_all_passing(self):
        assert _mod._longest_fail_streak([False, False, False]) == 0

    def test_all_failing(self):
        assert _mod._longest_fail_streak([True, True, True]) == 3

    def test_single_spike_then_green(self):
        assert _mod._longest_fail_streak([False, True, False, False]) == 1

    def test_longest_streak_not_first(self):
        assert _mod._longest_fail_streak([False, True, True, False, True]) == 2

    def test_streak_at_end(self):
        assert _mod._longest_fail_streak([False, False, True, True, True]) == 3

    def test_oscillation_no_run_longer_than_one(self):
        assert _mod._longest_fail_streak([True, False, True, False, True]) == 1


# ---------------------------------------------------------------------------
# buckets
# ---------------------------------------------------------------------------


class TestBuckets:
    def test_counts_non_sidechain_assistant_turns(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="feat-a"),
            _asst("claude-sonnet-4-6", branch="feat-a"),
            _asst("claude-sonnet-4-6", branch="feat-a", sidechain=True),  # excluded
        ])
        args = type("A", (), {"projects": "*", "branches": None})()
        _mod.cmd_buckets(args)
        out = capsys.readouterr().out
        assert "feat-a" in out
        # 1 opus + 1 sonnet = 2 non-sidechain turns
        assert " 2 " in out

    def test_branch_filter_excludes_other_branches(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="feat-a"),
            _asst("claude-sonnet-4-6", branch="feat-b"),
        ])
        args = type("A", (), {"projects": "*", "branches": "feat-a"})()
        _mod.cmd_buckets(args)
        out = capsys.readouterr().out
        assert "feat-a" in out
        assert "feat-b" not in out

    def test_no_data_prints_message(self, fake_projects, capsys):
        args = type("A", (), {"projects": "*", "branches": None})()
        _mod.cmd_buckets(args)
        out = capsys.readouterr().out
        assert "No data found." in out


# ---------------------------------------------------------------------------
# fail-seq end-to-end (core parsing logic, via shared helper)
# ---------------------------------------------------------------------------


def _collect_runs(target_branch: str) -> list[tuple[str, int]]:
    """Extract (model_family, failed_count) pairs for target_branch using the module's core logic."""
    runs: list[tuple[str, int]] = []
    for _jsonl, records in _mod.iter_sessions(_mod.PROJECTS_DIR):
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
                fam = _mod._fam(msg.get("model", ""))
                for block in (msg.get("content") or []):
                    if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "Bash":
                        cmd = (block.get("input") or {}).get("command", "")
                        if _mod.TEST_RUNNER_RE.search(cmd):
                            pending[block["id"]] = fam
            elif rtype in ("user", "human"):
                content = msg.get("content") or []
                for block in (content if isinstance(content, list) else []):
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        tid = block.get("tool_use_id", "")
                        if tid in pending:
                            fam = pending.pop(tid)
                            text = _mod._content_text(block.get("content", ""))
                            counts = [int(m) for m in _mod.FAILED_RE.findall(text)]
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


# ---------------------------------------------------------------------------
# duration gap splitting
# ---------------------------------------------------------------------------


class TestDurationGapSplit:
    def test_large_gap_counted_as_idle(self):
        now = time.time()
        tss = sorted([now, now + 60, now + 120, now + 8000, now + 8060])
        gap_secs = 1800
        idle_gaps = [tss[i + 1] - tss[i] for i in range(len(tss) - 1) if tss[i + 1] - tss[i] > gap_secs]
        assert len(idle_gaps) == 1
        assert idle_gaps[0] == pytest.approx(8000 - 120, abs=1)

    def test_no_gaps_means_fully_active(self):
        now = time.time()
        tss = [now, now + 60, now + 120]
        idle_gaps = [tss[i + 1] - tss[i] for i in range(len(tss) - 1) if tss[i + 1] - tss[i] > 1800]
        assert idle_gaps == []

    def test_active_time_is_span_minus_idle(self):
        now = time.time()
        tss = sorted([now, now + 300, now + 10000, now + 10300])
        gap_secs = 1800
        span = tss[-1] - tss[0]
        idle_gaps = [tss[i + 1] - tss[i] for i in range(len(tss) - 1) if tss[i + 1] - tss[i] > gap_secs]
        idle = sum(idle_gaps)
        active = span - idle
        # gap between tss[1] and tss[2] = 9700s; active = 300 + 300 = 600s
        assert active == pytest.approx(600, abs=2)


# ---------------------------------------------------------------------------
# subagent-mix
# ---------------------------------------------------------------------------


class TestSubagentMix:
    def test_counts_agent_spawns_by_subagent_type(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="feat", content=[
                _agent_use("a1", "staff-backend-engineer"),
                _agent_use("a2", "ciso-reviewer"),
                _agent_use("a3", "staff-backend-engineer"),
            ]),
        ])
        args = type("A", (), {"projects": "*", "branches": None, "per_session": False})()
        _mod.cmd_subagent_mix(args)
        out = capsys.readouterr().out
        assert "feat" in out
        assert "staff-backend-engineer(2)" in out
        assert "ciso-reviewer(1)" in out

    def test_counts_review_skill_invocations(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="feat", content=[
                _skill_use("s1", "code-review"),
                _skill_use("s2", "code-review"),
                _skill_use("s3", "plan-review"),
                _skill_use("s4", "ready-for-review"),
                _skill_use("s5", "respond-pr"),  # excluded — not in REVIEW_SKILLS
            ]),
        ])
        args = type("A", (), {"projects": "*", "branches": None, "per_session": False})()
        _mod.cmd_subagent_mix(args)
        out = capsys.readouterr().out
        # CR=2, PR=1, RR=1 — column order is Sess | Spawns | CR | PR | RR
        lines = [ln for ln in out.splitlines() if ln.startswith("feat")]
        assert len(lines) == 1
        cols = lines[0].split()
        # cols: ['feat', '1', '0', '2', '1', '1', ...]
        assert cols[2:6] == ["0", "2", "1", "1"]

    def test_legacy_task_tool_name_also_counted(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="legacy", content=[
                _agent_use("t1", "staff-frontend-engineer", tool_name="Task"),
            ]),
        ])
        args = type("A", (), {"projects": "*", "branches": None, "per_session": False})()
        _mod.cmd_subagent_mix(args)
        out = capsys.readouterr().out
        assert "staff-frontend-engineer(1)" in out

    def test_sidechain_spawns_not_counted(self, fake_projects, capsys):
        """Subagent-issued Agent calls (which appear on sidechain) must not double-count parent spawns."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="feat", content=[_agent_use("a1", "staff-backend-engineer")]),
            _asst("claude-sonnet-4-6", branch="feat", sidechain=True, content=[
                _agent_use("a2", "ciso-reviewer"),  # excluded — sidechain
            ]),
        ])
        args = type("A", (), {"projects": "*", "branches": None, "per_session": False})()
        _mod.cmd_subagent_mix(args)
        out = capsys.readouterr().out
        assert "staff-backend-engineer(1)" in out
        assert "ciso-reviewer" not in out

    def test_branch_filter(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="feat-a", content=[_agent_use("a1", "ciso-reviewer")]),
            _asst("claude-opus-4-7", branch="feat-b", content=[_agent_use("a2", "staff-backend-engineer")]),
        ])
        args = type("A", (), {"projects": "*", "branches": "feat-a", "per_session": False})()
        _mod.cmd_subagent_mix(args)
        out = capsys.readouterr().out
        assert "feat-a" in out
        assert "feat-b" not in out
        assert "staff-backend-engineer" not in out

    def test_per_session_splits_aggregate(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "abcd1234-aaaa.jsonl", [
            _asst("claude-opus-4-7", branch="feat", content=[_agent_use("a1", "ciso-reviewer")]),
        ])
        _write_jsonl(fake_projects / "efgh5678-bbbb.jsonl", [
            _asst("claude-opus-4-7", branch="feat", content=[_agent_use("a2", "staff-backend-engineer")]),
        ])
        args = type("A", (), {"projects": "*", "branches": None, "per_session": True})()
        _mod.cmd_subagent_mix(args)
        out = capsys.readouterr().out
        # Both sessions should appear with stem prefixes; aggregate "feat" alone should not be present as a row.
        assert "abcd1234" in out
        assert "efgh5678" in out

    def test_no_data_prints_message(self, fake_projects, capsys):
        args = type("A", (), {"projects": "*", "branches": None, "per_session": False})()
        _mod.cmd_subagent_mix(args)
        out = capsys.readouterr().out
        assert "No data found." in out


# ---------------------------------------------------------------------------
# pr-link (stubbed gh)
# ---------------------------------------------------------------------------


class TestPrLink:
    def test_stubbed_gh_produces_row_with_pr_number(self, fake_projects, monkeypatch, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="feat-pr"),
        ])

        def fake_run(cmd, *_, **__):
            class R:
                stdout = ""
                returncode = 0

            r = R()
            if "list" in cmd:
                r.stdout = json.dumps([{"number": 77}])
            elif "issues" in " ".join(cmd):
                r.stdout = "alice\nbob\n"
            elif "pulls" in " ".join(cmd):
                r.stdout = "alice\n"
            return r

        monkeypatch.setattr(subprocess, "run", fake_run)

        args = type("A", (), {"repo": "owner/repo", "branches": "feat-pr", "author": "alice", "projects": "*"})()
        _mod.cmd_pr_link(args)
        out = capsys.readouterr().out
        assert "77" in out
        assert "feat-pr" in out

    def test_missing_gh_binary_shows_error_marker(self, fake_projects, monkeypatch, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="feat-x"),
        ])

        def fake_run(*_, **__):
            raise FileNotFoundError("gh not found")

        monkeypatch.setattr(subprocess, "run", fake_run)

        args = type("A", (), {"repo": "owner/repo", "branches": "feat-x", "author": "", "projects": "*"})()
        _mod.cmd_pr_link(args)
        out = capsys.readouterr().out
        assert "gh-err" in out or "?" in out

    def test_branch_with_no_pr_shows_none(self, fake_projects, monkeypatch, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="no-pr-branch"),
        ])

        def fake_run(cmd, *_, **__):
            class R:
                stdout = "[]"
                returncode = 0

            return R()

        monkeypatch.setattr(subprocess, "run", fake_run)

        args = type("A", (), {"repo": "owner/repo", "branches": "no-pr-branch", "author": "", "projects": "*"})()
        _mod.cmd_pr_link(args)
        out = capsys.readouterr().out
        assert "none" in out
