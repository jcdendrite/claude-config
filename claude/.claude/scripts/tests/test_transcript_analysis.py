"""Tests for transcript-analysis.py."""
import argparse
import importlib.util
import json
import re
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


def _write_subagent_jsonl(
    proj: Path, session_id: str, agent_id: str, records: list[dict]
) -> None:
    """Write records to the split subagent layout: <session_id>/subagents/<agent_id>.jsonl."""
    subdir = proj / session_id / _mod.SUBAGENT_SUBDIR
    subdir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(subdir / f"{agent_id}.jsonl", records)


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

    def test_iso_date_valid_returns_string(self):
        assert _mod._iso_date("2026-05-19") == "2026-05-19"

    def test_iso_date_invalid_month_raises_type_error(self):
        with pytest.raises(argparse.ArgumentTypeError):
            _mod._iso_date("2026-13-01")

    def test_iso_date_garbage_raises_type_error(self):
        with pytest.raises(argparse.ArgumentTypeError):
            _mod._iso_date("garbage")


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
# skill-pair
# ---------------------------------------------------------------------------

# Fixed timestamp used for tests 1–6, 8–10: 2026-05-12T12:00:00Z → ISO 2026-W20
_TS_FIXED = "2026-05-12T12:00:00Z"
# Boundary timestamps for test 7 (ISO week boundary Sun → Mon)
_TS_SUNDAY = "2026-05-17T23:59:59Z"   # ISO 2026-W20 (Sunday = last day of W20)
_TS_MONDAY = "2026-05-18T00:00:01Z"   # ISO 2026-W21 (Monday = first day of W21)


def _skill_pair_args(leader="plan-it", follower="plan-review", *, projects="*", exclude_projects=None, branches=None):
    return type("A", (), {
        "leader": leader,
        "follower": follower,
        "projects": projects,
        "exclude_projects": exclude_projects,
        "branches": branches,
    })()


class TestSkillPair:
    def test_empty_jsonl_prints_no_data(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [])
        _mod.cmd_skill_pair(_skill_pair_args())
        assert "No data found." in capsys.readouterr().out

    def test_leader_only_session_counted_with_zero_followers(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts=_TS_FIXED, content=[
                _skill_use("s1", "plan-it"),
            ]),
        ])
        _mod.cmd_skill_pair(_skill_pair_args())
        out = capsys.readouterr().out
        lines = [ln for ln in out.splitlines() if "2026-W20" in ln]
        assert len(lines) == 1
        cols = lines[0].split()
        # cols: ['2026-W20', lead, main, side, pair%]
        assert cols[1] == "1"   # Lead=1
        assert cols[2] == "0"   # Main=0
        assert cols[3] == "0"   # Side=0
        assert "0.0%" in cols[4]

    def test_leader_plus_main_follower_counted(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts=_TS_FIXED, content=[
                _skill_use("s1", "plan-it"),
            ]),
            _asst("claude-sonnet-4-6", branch="main", ts=_TS_FIXED, content=[
                _skill_use("s2", "plan-review"),
            ]),
        ])
        _mod.cmd_skill_pair(_skill_pair_args())
        out = capsys.readouterr().out
        lines = [ln for ln in out.splitlines() if "2026-W20" in ln]
        assert len(lines) == 1
        cols = lines[0].split()
        assert cols[1] == "1"   # Lead=1
        assert cols[2] == "1"   # Main=1
        assert "100.0%" in cols[4]

    def test_leader_plus_sidechain_only_follower(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts=_TS_FIXED, content=[
                _skill_use("s1", "plan-it"),
            ]),
            _asst("claude-sonnet-4-6", branch="main", ts=_TS_FIXED, sidechain=True, content=[
                _skill_use("s2", "plan-review"),
            ]),
        ])
        _mod.cmd_skill_pair(_skill_pair_args())
        out = capsys.readouterr().out
        lines = [ln for ln in out.splitlines() if "2026-W20" in ln]
        assert len(lines) == 1
        cols = lines[0].split()
        assert cols[1] == "1"   # Lead=1
        assert cols[2] == "0"   # Main=0 (no main-thread follower)
        assert cols[3] == "1"   # Side=1

    def test_both_main_and_sidechain_follower_counts_main_only(self, fake_projects, capsys):
        """Session with both main and sidechain follower hits counts in Main, NOT Side."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts=_TS_FIXED, content=[
                _skill_use("s1", "plan-it"),
            ]),
            _asst("claude-sonnet-4-6", branch="main", ts=_TS_FIXED, content=[
                _skill_use("s2", "plan-review"),
            ]),
            _asst("claude-sonnet-4-6", branch="main", ts=_TS_FIXED, sidechain=True, content=[
                _skill_use("s3", "plan-review"),
            ]),
        ])
        _mod.cmd_skill_pair(_skill_pair_args())
        out = capsys.readouterr().out
        lines = [ln for ln in out.splitlines() if "2026-W20" in ln]
        assert len(lines) == 1
        cols = lines[0].split()
        assert cols[2] == "1"   # Main=1
        assert cols[3] == "0"   # Side=0 (sidechain-only requires no main hit)

    def test_multiple_leader_hits_count_as_one_session(self, fake_projects, capsys):
        """Three leader invocations in one session → Lead=1, not 3."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts=_TS_FIXED, content=[
                _skill_use("s1", "plan-it"),
                _skill_use("s2", "plan-it"),
                _skill_use("s3", "plan-it"),
            ]),
        ])
        _mod.cmd_skill_pair(_skill_pair_args())
        out = capsys.readouterr().out
        lines = [ln for ln in out.splitlines() if "2026-W20" in ln]
        assert len(lines) == 1
        assert lines[0].split()[1] == "1"   # Lead=1

    def test_iso_week_boundary_sunday_vs_monday(self, fake_projects, capsys):
        """Leader on Sun 23:59:59 UTC → W20; leader on Mon 00:00:01 UTC → W21."""
        proj2 = fake_projects.parent / "-home-user-testrepo2"
        proj2.mkdir(parents=True)
        _write_jsonl(fake_projects / "sess-sun.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts=_TS_SUNDAY, content=[
                _skill_use("s1", "plan-it"),
            ]),
        ])
        _write_jsonl(proj2 / "sess-mon.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts=_TS_MONDAY, content=[
                _skill_use("s2", "plan-it"),
            ]),
        ])
        _mod.cmd_skill_pair(_skill_pair_args())
        out = capsys.readouterr().out
        data_lines = [ln for ln in out.splitlines() if ln.startswith("2026-W")]
        bins = [ln.split()[0] for ln in data_lines]
        assert "2026-W20" in bins
        assert "2026-W21" in bins
        assert bins != ["2026-W20"]  # both bins present as distinct rows

    def test_projects_glob_excludes_unmatched_project(self, fake_projects, capsys):
        """Only project dirs matching --projects are included."""
        proj_b = fake_projects.parent / "-home-user-other-repo"
        proj_b.mkdir(parents=True)
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts=_TS_FIXED, content=[
                _skill_use("s1", "plan-it"),
            ]),
        ])
        _write_jsonl(proj_b / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts=_TS_FIXED, content=[
                _skill_use("s2", "plan-it"),
            ]),
        ])
        # Only match the first project dir
        _mod.cmd_skill_pair(_skill_pair_args(projects="-home-user-testrepo"))
        out = capsys.readouterr().out
        lines = [ln for ln in out.splitlines() if "2026-W20" in ln]
        assert len(lines) == 1
        # Only 1 leader session (the included project)
        assert lines[0].split()[1] == "1"

    def test_exclude_projects_glob_omits_matching_dir(self, fake_projects, capsys):
        """Project dirs matching --exclude-projects are skipped even when also matching --projects=*."""
        proj_eval = fake_projects.parent / "-tmp-claude-eval-abc123"
        proj_normal = fake_projects.parent / "-home-user-normalrepo"
        proj_eval.mkdir(parents=True)
        proj_normal.mkdir(parents=True)
        # Normal project: 1 leader session
        _write_jsonl(proj_normal / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts=_TS_FIXED, content=[
                _skill_use("s1", "plan-it"),
            ]),
        ])
        # Eval project: 2 leader sessions — should be excluded
        _write_jsonl(proj_eval / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts=_TS_FIXED, content=[
                _skill_use("s2", "plan-it"),
            ]),
        ])
        _write_jsonl(proj_eval / "sess2.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts=_TS_FIXED, content=[
                _skill_use("s3", "plan-it"),
            ]),
        ])
        # fake_projects itself has no sessions for this test; use exclude on the eval dir
        _mod.cmd_skill_pair(_skill_pair_args(exclude_projects="-tmp-claude-eval-*"))
        out = capsys.readouterr().out
        lines = [ln for ln in out.splitlines() if "2026-W20" in ln]
        assert len(lines) == 1
        # Only 1 session from the normal project; eval sessions excluded
        assert lines[0].split()[1] == "1"

    def test_branches_filter_excludes_other_branch(self, fake_projects, capsys):
        """With --branches=branch-a, only sessions on branch-a contribute."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="branch-a", ts=_TS_FIXED, content=[
                _skill_use("s1", "plan-it"),
            ]),
            _asst("claude-sonnet-4-6", branch="branch-a", ts=_TS_FIXED, content=[
                _skill_use("s2", "plan-review"),
            ]),
        ])
        _write_jsonl(fake_projects / "sess2.jsonl", [
            _asst("claude-sonnet-4-6", branch="branch-b", ts=_TS_FIXED, content=[
                _skill_use("s3", "plan-it"),
            ]),
        ])
        _mod.cmd_skill_pair(_skill_pair_args(branches="branch-a"))
        out = capsys.readouterr().out
        lines = [ln for ln in out.splitlines() if "2026-W20" in ln]
        assert len(lines) == 1
        cols = lines[0].split()
        assert cols[1] == "1"   # Lead=1 (branch-a only)
        assert cols[2] == "1"   # Main=1 (follower on branch-a)


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


# ---------------------------------------------------------------------------
# commit-gate
# ---------------------------------------------------------------------------


def _gate_args(skill: str, *, by_permission_mode: bool = False, projects: str = "*",
               exclude_projects: str | None = None, branches: str | None = None):
    """Build a minimal argparse.Namespace for cmd_commit_gate."""
    return type("A", (), {
        "skill": skill,
        "by_permission_mode": by_permission_mode,
        "projects": projects,
        "exclude_projects": exclude_projects,
        "branches": branches,
    })()


class TestCommitGate:
    def test_empty_jsonl_prints_no_data(self, fake_projects, capsys):
        """Empty project dir → 'No data found.' and implicit exit 0."""
        args = _gate_args("code-review")
        _mod.cmd_commit_gate(args)
        out = capsys.readouterr().out
        assert "No data found." in out

    def test_single_session_no_commits_one_skill_invocation(self, fake_projects, capsys):
        """One skill call, no commits: 1 session, 0 commits, 1 invocation, rate > 0."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:00:00.000Z",
                  content=[_skill_use("s1", "code-review")]),
        ])
        args = _gate_args("code-review")
        _mod.cmd_commit_gate(args)
        out = capsys.readouterr().out
        # Default output: bin(0) sessions(1) turns(2) skill-inv(3) skill/1k(4) commits(5)
        #                 w-skill(6) wo-skill(7) no-verify(8)
        # 1 session, 0 commits, 1 skill invocation; rate = 1000/1 = 1000.0
        data_lines = [ln for ln in out.splitlines() if "2026-W" in ln]
        assert len(data_lines) == 1
        cols = data_lines[0].split()
        assert cols[1] == "1"    # sessions
        assert int(cols[5]) == 0  # commits
        assert int(cols[3]) == 1  # skill-inv

    def test_commit_after_skill_is_gated(self, fake_projects, capsys):
        """Skill invocation before commit in same session → commits-with-prior-skill = 1."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:00:00.000Z",
                  content=[_skill_use("s1", "code-review")]),
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:01:00.000Z",
                  content=[_bash_use("b1", "git commit -m 'x'")]),
        ])
        args = _gate_args("code-review")
        _mod.cmd_commit_gate(args)
        out = capsys.readouterr().out
        data_lines = [ln for ln in out.splitlines() if "2026-W" in ln]
        assert len(data_lines) == 1
        cols = data_lines[0].split()
        # Default: bin(0) sessions(1) turns(2) skill-inv(3) skill/1k(4) commits(5)
        #          w-skill(6) wo-skill(7) no-verify(8)
        assert cols[5] == "1"   # commits
        assert cols[6] == "1"   # w-skill
        assert cols[7] == "0"   # wo-skill
        assert cols[8] == "0"   # no-verify

    def test_commit_before_skill_is_ungated(self, fake_projects, capsys):
        """Commit before any skill invocation → commits-without-prior-skill = 1."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:00:00.000Z",
                  content=[_bash_use("b1", "git commit -m 'x'")]),
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:01:00.000Z",
                  content=[_skill_use("s1", "code-review")]),
        ])
        args = _gate_args("code-review")
        _mod.cmd_commit_gate(args)
        out = capsys.readouterr().out
        data_lines = [ln for ln in out.splitlines() if "2026-W" in ln]
        assert len(data_lines) == 1
        cols = data_lines[0].split()
        assert cols[5] == "1"   # commits
        assert cols[6] == "0"   # w-skill
        assert cols[7] == "1"   # wo-skill

    def test_two_commits_one_skill_between_consumes_only_first(self, fake_projects, capsys):
        """Skill between two commits: first commit is gated, second is not (skill consumed)."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:00:00.000Z",
                  content=[_bash_use("b1", "git commit -m 'first'")]),
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:01:00.000Z",
                  content=[_skill_use("s1", "code-review")]),
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:02:00.000Z",
                  content=[_bash_use("b2", "git commit -m 'second'")]),
        ])
        args = _gate_args("code-review")
        _mod.cmd_commit_gate(args)
        out = capsys.readouterr().out
        data_lines = [ln for ln in out.splitlines() if "2026-W" in ln]
        assert len(data_lines) == 1
        cols = data_lines[0].split()
        assert cols[5] == "2"   # commits
        assert cols[6] == "1"   # w-skill (second commit, after skill)
        assert cols[7] == "1"   # wo-skill (first commit, no prior skill)

    def test_no_verify_counted_in_commits_and_no_verify_but_not_gated(self, fake_projects, capsys):
        """git commit --no-verify after /code-review: counted in commits + no-verify, NOT in w-skill."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:00:00.000Z",
                  content=[_skill_use("s1", "code-review")]),
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:01:00.000Z",
                  content=[_bash_use("b1", "git commit --no-verify -m 'bypass'")]),
        ])
        args = _gate_args("code-review")
        _mod.cmd_commit_gate(args)
        out = capsys.readouterr().out
        data_lines = [ln for ln in out.splitlines() if "2026-W" in ln]
        assert len(data_lines) == 1
        cols = data_lines[0].split()
        assert cols[5] == "1"   # commits total
        assert cols[6] == "0"   # w-skill (bypass is NOT credited as gated)
        assert cols[7] == "1"   # wo-skill
        assert cols[8] == "1"   # no-verify

    def test_amend_counted_as_commit(self, fake_projects, capsys):
        """git commit --amend is counted in the commits total."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:00:00.000Z",
                  content=[_bash_use("b1", "git commit --amend --no-edit")]),
        ])
        args = _gate_args("code-review")
        _mod.cmd_commit_gate(args)
        out = capsys.readouterr().out
        data_lines = [ln for ln in out.splitlines() if "2026-W" in ln]
        assert len(data_lines) == 1
        cols = data_lines[0].split()
        assert cols[5] == "1"   # commits

    def test_git_commit_tree_not_counted(self, fake_projects, capsys):
        """git commit-tree must NOT match the commit regex (trailing word boundary)."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:00:00.000Z",
                  content=[_bash_use("b1", "git commit-tree HEAD^{tree} -m 'msg'")]),
        ])
        args = _gate_args("code-review")
        _mod.cmd_commit_gate(args)
        out = capsys.readouterr().out
        data_lines = [ln for ln in out.splitlines() if "2026-W" in ln]
        assert len(data_lines) == 1
        cols = data_lines[0].split()
        assert cols[5] == "0"   # commits — commit-tree must not be counted

    def test_sidechain_skill_not_counted(self, fake_projects, capsys):
        """A /code-review call inside a sidechain record must not credit the main thread."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:00:00.000Z",
                  sidechain=True, content=[_skill_use("s1", "code-review")]),
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:01:00.000Z",
                  content=[_bash_use("b1", "git commit -m 'main'")]),
        ])
        args = _gate_args("code-review")
        _mod.cmd_commit_gate(args)
        out = capsys.readouterr().out
        data_lines = [ln for ln in out.splitlines() if "2026-W" in ln]
        assert len(data_lines) == 1
        cols = data_lines[0].split()
        assert cols[3] == "0"   # skill-invocations: sidechain skill not counted
        assert cols[6] == "0"   # w-skill: sidechain skill must not gate the commit
        assert cols[7] == "1"   # wo-skill

    def test_skill_name_exact_match_plugin_prefix_not_matched(self, fake_projects, capsys):
        """'skill-management:skill-review' does NOT match commit-gate skill-review (byte-equal)."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:00:00.000Z",
                  content=[_skill_use("s1", "skill-management:skill-review")]),
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:01:00.000Z",
                  content=[_bash_use("b1", "git commit -m 'x'")]),
        ])
        args = _gate_args("skill-review")
        _mod.cmd_commit_gate(args)
        out = capsys.readouterr().out
        data_lines = [ln for ln in out.splitlines() if "2026-W" in ln]
        assert len(data_lines) == 1
        cols = data_lines[0].split()
        assert cols[3] == "0"   # skill-invocations: plugin-prefixed name must not match
        assert cols[6] == "0"   # w-skill

    def test_same_record_skill_before_commit_is_gated(self, fake_projects, capsys):
        """Within one assistant record, Skill block at lower index than Bash → commit is gated."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:00:00.000Z",
                  content=[
                      _skill_use("s1", "code-review"),
                      _bash_use("b1", "git commit -m 'x'"),
                  ]),
        ])
        args = _gate_args("code-review")
        _mod.cmd_commit_gate(args)
        out = capsys.readouterr().out
        data_lines = [ln for ln in out.splitlines() if "2026-W" in ln]
        assert len(data_lines) == 1
        cols = data_lines[0].split()
        assert cols[6] == "1"   # w-skill
        assert cols[7] == "0"   # wo-skill

    def test_same_record_commit_before_skill_is_ungated(self, fake_projects, capsys):
        """Within one assistant record, Bash block at lower index than Skill → commit is ungated."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:00:00.000Z",
                  content=[
                      _bash_use("b1", "git commit -m 'x'"),
                      _skill_use("s1", "code-review"),
                  ]),
        ])
        args = _gate_args("code-review")
        _mod.cmd_commit_gate(args)
        out = capsys.readouterr().out
        data_lines = [ln for ln in out.splitlines() if "2026-W" in ln]
        assert len(data_lines) == 1
        cols = data_lines[0].split()
        assert cols[6] == "0"   # w-skill
        assert cols[7] == "1"   # wo-skill

    def test_by_permission_mode_splits_auto_and_default(self, fake_projects, capsys):
        """Two sessions, one with permissionMode 'auto', one without → two rows per bin."""
        proj2 = fake_projects.parent / "-home-user-repo2"
        proj2.mkdir(parents=True)
        # Session with permissionMode='auto'
        auto_rec = _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:00:00.000Z")
        auto_rec["permissionMode"] = "auto"
        _write_jsonl(fake_projects / "auto.jsonl", [auto_rec])
        # Session without permissionMode (→ 'default')
        _write_jsonl(proj2 / "default.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T11:00:00.000Z"),
        ])
        args = _gate_args("code-review", by_permission_mode=True)
        _mod.cmd_commit_gate(args)
        out = capsys.readouterr().out
        data_lines = [ln for ln in out.splitlines() if "2026-W" in ln]
        modes = [ln.split()[1] for ln in data_lines]
        assert "auto" in modes
        assert "default" in modes

    def test_by_permission_mode_sparse_picks_first_carrying_record(self, fake_projects, capsys):
        """permissionMode on a mid-session record (not the first) is still discovered."""
        rec1 = _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:00:00.000Z")
        rec2 = _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:01:00.000Z")
        rec2["permissionMode"] = "bypassPermissions"
        _write_jsonl(fake_projects / "sess.jsonl", [rec1, rec2])
        args = _gate_args("code-review", by_permission_mode=True)
        _mod.cmd_commit_gate(args)
        out = capsys.readouterr().out
        data_lines = [ln for ln in out.splitlines() if "2026-W" in ln]
        assert len(data_lines) == 1
        # The second record carries the mode; our "first carrying record" rule picks it.
        assert "bypassPermissions" in data_lines[0]

    def test_by_permission_mode_extracted_from_user_record(self, fake_projects, capsys):
        """permissionMode on a user record (the real-world shape) is picked up.

        Surfaced by the trend audit: empirically, transcripts carry permissionMode
        on user records (initial session-meta record), not on assistant records.
        Filtering extraction to assistant records misses every real session.
        """
        user_rec = _user_msg("hi", branch="main")
        user_rec["timestamp"] = "2026-05-19T10:00:00.000Z"
        user_rec["permissionMode"] = "auto"
        asst_rec = _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:00:01.000Z")
        _write_jsonl(fake_projects / "sess.jsonl", [user_rec, asst_rec])
        args = _gate_args("code-review", by_permission_mode=True)
        _mod.cmd_commit_gate(args)
        out = capsys.readouterr().out
        data_lines = [ln for ln in out.splitlines() if "2026-W" in ln]
        assert len(data_lines) == 1
        assert "auto" in data_lines[0]

    def test_iso_week_boundary_assigns_correct_bin(self, fake_projects, capsys):
        """Sessions just before and just after a Monday 00:00 UTC get different ISO-week bins."""
        proj2 = fake_projects.parent / "-home-user-repo3"
        proj2.mkdir(parents=True)
        # 2026-05-17 (Sunday) 23:59:59 UTC → W20
        # 2026-05-18 (Monday) 00:00:01 UTC → W21
        _write_jsonl(fake_projects / "sunday.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-17T23:59:59.000Z"),
        ])
        _write_jsonl(proj2 / "monday.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-18T00:00:01.000Z"),
        ])
        args = _gate_args("code-review")
        _mod.cmd_commit_gate(args)
        out = capsys.readouterr().out
        bins = [ln.split()[0] for ln in out.splitlines() if "2026-W" in ln]
        assert "2026-W20" in bins
        assert "2026-W21" in bins

    def test_projects_glob_filters_by_project_dir(self, fake_projects, capsys):
        """--projects glob restricts which project dirs are walked."""
        other_proj = fake_projects.parent / "-home-user-otherrepo"
        other_proj.mkdir(parents=True)
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:00:00.000Z"),
        ])
        _write_jsonl(other_proj / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="main", ts="2026-05-19T10:00:00.000Z"),
        ])
        # Only match fake_projects dir
        args = _gate_args("code-review", projects="-home-user-testrepo")
        _mod.cmd_commit_gate(args)
        out = capsys.readouterr().out
        data_lines = [ln for ln in out.splitlines() if "2026-W" in ln]
        assert len(data_lines) == 1
        assert int(data_lines[0].split()[1]) == 1   # only 1 session

    def test_exclude_projects_omits_matching_dir(self, fake_projects, capsys):
        """--exclude-projects glob removes matching dirs even if --projects would include them."""
        eval_proj = fake_projects.parent / "-tmp-claude-eval-run1"
        eval_proj.mkdir(parents=True)
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:00:00.000Z"),
        ])
        _write_jsonl(eval_proj / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="main", ts="2026-05-19T10:00:00.000Z"),
        ])
        args = _gate_args("code-review", exclude_projects="-tmp-claude-eval-*")
        _mod.cmd_commit_gate(args)
        out = capsys.readouterr().out
        data_lines = [ln for ln in out.splitlines() if "2026-W" in ln]
        assert len(data_lines) == 1
        assert int(data_lines[0].split()[1]) == 1   # only the non-excluded session

    def test_branches_filter_skips_sessions_with_no_matching_branch(self, fake_projects, capsys):
        """--branches filter: sessions whose main-thread records are all on other branches are skipped."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="feat-a", ts="2026-05-19T10:00:00.000Z"),
            _asst("claude-sonnet-4-6", branch="feat-b", ts="2026-05-19T10:01:00.000Z"),
        ])
        args = _gate_args("code-review", branches="feat-a")
        _mod.cmd_commit_gate(args)
        out = capsys.readouterr().out
        # feat-a is present → session counted
        data_lines = [ln for ln in out.splitlines() if "2026-W" in ln]
        assert len(data_lines) == 1

        # Session with ONLY feat-b branch is excluded
        proj2 = fake_projects.parent / "-home-user-repo4"
        proj2.mkdir(parents=True)
        _write_jsonl(proj2 / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="feat-b", ts="2026-05-19T10:00:00.000Z"),
        ])
        args2 = _gate_args("code-review", branches="feat-a")
        _mod.cmd_commit_gate(args2)
        out2 = capsys.readouterr().out
        data_lines2 = [ln for ln in out2.splitlines() if "2026-W" in ln]
        # Still only 1 session (feat-b-only session excluded)
        assert int(data_lines2[0].split()[1]) == 1


# ---------------------------------------------------------------------------
# review-trace
# ---------------------------------------------------------------------------


def _hook_deny(hook_name: str, *, stringified: bool = False) -> dict:
    """Build an attachment/hook_blocking_error record using the real transcript shape.

    Real transcripts nest the human-readable denial text in a "blockingError" key
    inside the blockingError dict (alongside a "command" key).

    When stringified=True, the outer blockingError value is a JSON-encoded string
    of that dict rather than the dict itself (as seen in some real transcripts).
    """
    human_message = f"Hook '{hook_name}' blocked the operation"
    error_dict = {"blockingError": human_message, "command": "git commit -m x"}
    blocking_error = json.dumps(error_dict) if stringified else error_dict
    return {
        "type": "attachment",
        "attachment": {
            "type": "hook_blocking_error",
            "hookName": hook_name,
            "toolUseID": f"toolu_{hook_name[:8]}",
            "blockingError": blocking_error,
        },
    }


def _hook_deny_current(
    message: str,
    *,
    tool_id: str = "toolu_cur",
    ts: str | None = None,
    branch: str = "main",
) -> dict:
    """Build a current-format hook denial.

    Newer Claude Code transcripts no longer emit a hook_blocking_error
    attachment record — a denial surfaces only as a user record whose
    tool_result block carries is_error and the denial text.
    """
    rec: dict = {
        "type": "user",
        "gitBranch": branch,
        "isSidechain": False,
        "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": tool_id,
             "content": message, "is_error": True},
        ]},
    }
    if ts:
        rec["timestamp"] = ts
    return rec


def _review_trace_args(
    *,
    projects: str = "*",
    branches: str | None = None,
    since: str | None = None,
    until: str | None = None,
    deny_only: bool = False,
    skill: str | None = None,
) -> object:
    return type("A", (), {
        "projects": projects,
        "branches": branches,
        "since": since,
        "until": until,
        "deny_only": deny_only,
        "skill": skill,
    })()


class TestReviewTrace:
    def test_skill_invocation_appears_in_output(self, fake_projects, capsys):
        """Main-thread Skill call for a review skill produces a 'skill' event in output."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="feat",
                  ts="2026-05-19T10:00:00.000Z",
                  content=[_skill_use("s1", "code-review")]),
        ])
        _mod.cmd_review_trace(_review_trace_args())
        out = capsys.readouterr().out
        assert "skill" in out
        assert "code-review" in out

    def test_denial_dict_blockingError_parsed(self, fake_projects, capsys):
        """hook_blocking_error with blockingError as a dict produces a denial event."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _hook_deny("require-code-review", stringified=False),
        ])
        _mod.cmd_review_trace(_review_trace_args())
        out = capsys.readouterr().out
        assert "denial" in out
        assert "require-code-review" in out

    def test_denial_stringified_blockingError_parsed_identically(self, fake_projects, capsys):
        """hook_blocking_error with blockingError as a JSON string produces identical output to dict form."""
        _write_jsonl(fake_projects / "dict_form.jsonl", [
            _hook_deny("require-code-review", stringified=False),
        ])
        _write_jsonl(fake_projects / "str_form.jsonl", [
            _hook_deny("require-code-review", stringified=True),
        ])
        _mod.cmd_review_trace(_review_trace_args())
        out = capsys.readouterr().out
        # Event lines have leading whitespace followed by a timestamp bracket.
        sections = out.split("### ")
        dict_section = next((s for s in sections if "dict_form" in s), "")
        str_section = next((s for s in sections if "str_form" in s), "")
        # Each section should have exactly one event line tagged 'denial'.
        dict_denial_lines = [ln for ln in dict_section.splitlines() if ln.startswith("  [") and "denial" in ln]
        str_denial_lines = [ln for ln in str_section.splitlines() if ln.startswith("  [") and "denial" in ln]
        assert len(dict_denial_lines) == 1
        assert len(str_denial_lines) == 1
        # The hook name and message content should be identical between both forms.
        assert "require-code-review" in dict_denial_lines[0]
        assert "require-code-review" in str_denial_lines[0]
        # The human-readable message text must appear in both forms, not a dict repr.
        assert "blocked the operation" in dict_denial_lines[0]
        assert "blocked the operation" in str_denial_lines[0]
        # Must NOT be showing a raw dict repr.
        assert "{'blockingError'" not in dict_denial_lines[0]
        assert "{'blockingError'" not in str_denial_lines[0]

    def test_hook_non_blocking_error_produces_zero_denial_events(self, fake_projects, capsys):
        """hook_non_blocking_error records must NOT appear as denial events."""
        non_blocking_rec = {
            "type": "attachment",
            "attachment": {
                "type": "hook_non_blocking_error",
                "hookName": "some-hook",
                "toolUseID": "toolu_abc",
                "blockingError": {"message": "non-fatal"},
            },
        }
        _write_jsonl(fake_projects / "sess.jsonl", [non_blocking_rec])
        _mod.cmd_review_trace(_review_trace_args())
        out = capsys.readouterr().out
        # No sessions should be emitted — the non-blocking record is not a review event.
        assert "denial" not in out
        assert "denials=1" not in out

    def test_reviewer_spawn_detected_general_purpose_excluded(self, fake_projects, capsys):
        """staff-backend-engineer spawn appears; general-purpose spawn is excluded."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="feat",
                  ts="2026-05-19T10:00:00.000Z",
                  content=[
                      _agent_use("a1", "staff-backend-engineer"),
                      _agent_use("a2", "general-purpose"),
                  ]),
        ])
        _mod.cmd_review_trace(_review_trace_args())
        out = capsys.readouterr().out
        assert "staff-backend-engineer" in out
        assert "general-purpose" not in out
        # Exactly one reviewer-spawn event (event lines start with "  [").
        reviewer_lines = [ln for ln in out.splitlines() if ln.startswith("  [") and "reviewer" in ln]
        assert len(reviewer_lines) == 1

    def test_sidechain_skill_invocation_excluded(self, fake_projects, capsys):
        """A code-review Skill call inside a sidechain record must not appear as a skill event."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="feat",
                  ts="2026-05-19T10:00:00.000Z",
                  sidechain=True,
                  content=[_skill_use("s1", "code-review")]),
        ])
        _mod.cmd_review_trace(_review_trace_args())
        out = capsys.readouterr().out
        # Sidechain skill produces no events → the session block is not printed at all.
        assert out.strip() == ""

    def test_since_boundary_inclusive_record_included(self, fake_projects, capsys):
        """A record whose timestamp matches exactly --since is included."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="feat",
                  ts="2026-05-19T00:00:00Z",
                  content=[_skill_use("s1", "code-review")]),
        ])
        _mod.cmd_review_trace(_review_trace_args(since="2026-05-19"))
        out = capsys.readouterr().out
        assert "skill" in out
        assert "code-review" in out

    def test_until_boundary_inclusive_record_included(self, fake_projects, capsys):
        """A record whose timestamp matches exactly --until is included."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="feat",
                  ts="2026-05-19T23:59:59Z",
                  content=[_skill_use("s1", "plan-review")]),
        ])
        _mod.cmd_review_trace(_review_trace_args(until="2026-05-19"))
        out = capsys.readouterr().out
        assert "skill" in out
        assert "plan-review" in out

    def test_record_with_no_timestamp_excluded_no_crash(self, fake_projects, capsys):
        """A record with no parseable timestamp is excluded when a date filter is active; no crash."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="feat",
                  content=[_skill_use("s1", "code-review")]),  # no ts field
        ])
        # No exception must be raised; the missing-timestamp record is silently skipped.
        _mod.cmd_review_trace(_review_trace_args(since="2026-05-01"))
        # No crash; output may be empty (no matching events survive the date filter).
        capsys.readouterr()  # consume; success if no exception raised above

    def test_deny_only_restricts_to_denial_sessions(self, fake_projects, capsys):
        """--deny-only: only sessions with at least one hook denial appear."""
        # Session A: has a denial.
        _write_jsonl(fake_projects / "with_denial.jsonl", [
            _hook_deny("require-code-review"),
        ])
        # Session B: only a reviewer spawn, no denial.
        _write_jsonl(fake_projects / "no_denial.jsonl", [
            _asst("claude-opus-4-7", branch="feat",
                  ts="2026-05-19T10:00:00.000Z",
                  content=[_agent_use("a1", "staff-backend-engineer")]),
        ])
        _mod.cmd_review_trace(_review_trace_args(deny_only=True))
        out = capsys.readouterr().out
        assert "with_denial" in out
        assert "no_denial" not in out

    def test_until_subsecond_record_included(self, fake_projects, capsys):
        """A record at T23:59:59.500Z on the --until date IS included (sub-second gap fix)."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="feat",
                  ts="2026-05-10T23:59:59.500Z",
                  content=[_skill_use("s1", "code-review")]),
        ])
        _mod.cmd_review_trace(_review_trace_args(until="2026-05-10"))
        out = capsys.readouterr().out
        assert "skill" in out
        assert "code-review" in out

    def test_denial_blockingError_key_used_for_display_message(self, fake_projects, capsys):
        """Denial message displays the nested blockingError string, not a dict repr."""
        human_message = "Hook 'require-code-review' blocked the operation"
        error_dict = {"blockingError": human_message, "command": "git commit -m x"}
        denial_rec = {
            "type": "attachment",
            "attachment": {
                "type": "hook_blocking_error",
                "hookName": "require-code-review",
                "toolUseID": "toolu_abc",
                "blockingError": error_dict,
            },
        }
        _write_jsonl(fake_projects / "sess.jsonl", [denial_rec])
        _mod.cmd_review_trace(_review_trace_args())
        out = capsys.readouterr().out
        denial_lines = [ln for ln in out.splitlines() if "denial" in ln and ln.startswith("  [")]
        assert len(denial_lines) == 1
        # Human-readable text must appear, not a raw dict repr.
        assert "blocked the operation" in denial_lines[0]
        assert "{'blockingError'" not in denial_lines[0]

    def test_no_match_session_produces_no_output(self, fake_projects, capsys):
        """A session with only non-review tool_use (Bash) produces no output block."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="feat",
                  ts="2026-05-19T10:00:00.000Z",
                  content=[_bash_use("b1", "git status")]),
        ])
        _mod.cmd_review_trace(_review_trace_args())
        out = capsys.readouterr().out
        assert out.strip() == ""

    def test_current_format_denial_detected(self, fake_projects, capsys):
        """A current-format is_error tool_result with a hook-denial signature is a denial."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _hook_deny_current("Commit blocked by code-review gate: run /code-review."),
        ])
        _mod.cmd_review_trace(_review_trace_args())
        out = capsys.readouterr().out
        denial_lines = [ln for ln in out.splitlines() if ln.startswith("  [") and "denial" in ln]
        assert len(denial_lines) == 1
        assert "code-review gate" in denial_lines[0]

    def test_current_format_ordinary_error_is_not_a_denial(self, fake_projects, capsys):
        """An is_error tool_result without a hook-denial signature is NOT a denial."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _hook_deny_current("npm ERR! command failed with exit code 1"),
        ])
        _mod.cmd_review_trace(_review_trace_args())
        out = capsys.readouterr().out
        assert out.strip() == ""

    def test_current_format_denial_text_without_is_error_ignored(self, fake_projects, capsys):
        """A tool_result with denial-shaped text but no is_error flag is NOT a denial."""
        rec = _hook_deny_current("Blocked by worktree-enforcement hook: not allowed.")
        rec["message"]["content"][0]["is_error"] = False
        _write_jsonl(fake_projects / "sess.jsonl", [rec])
        _mod.cmd_review_trace(_review_trace_args())
        out = capsys.readouterr().out
        assert out.strip() == ""

    def test_legacy_and_current_shapes_deduped_by_tool_use_id(self, fake_projects, capsys):
        """A denial recorded as both an attachment and an is_error tool_result for one
        tool_use_id collapses to one event. Dedup keeps whichever record appears first
        in the transcript; here the attachment is written ahead of its twin, so the
        retained event carries the hook name the attachment record provides."""
        attach = _hook_deny("worktree")  # toolUseID == "toolu_worktree", hookName "worktree"
        twin = _hook_deny_current(
            "Blocked by worktree-enforcement hook: 'git add' not allowed.",
            tool_id="toolu_worktree",
        )
        _write_jsonl(fake_projects / "sess.jsonl", [attach, twin])
        _mod.cmd_review_trace(_review_trace_args())
        out = capsys.readouterr().out
        denial_lines = [ln for ln in out.splitlines() if ln.startswith("  [") and "denial" in ln]
        assert len(denial_lines) == 1
        assert "denials=1" in out
        # Dedup retains the first-seen record. The attachment is written ahead of the
        # current-format twin above, so the retained event carries hook=worktree; had
        # the twin come first, hook= would be empty.
        assert "hook=worktree" in denial_lines[0]

    def test_multiple_distinct_current_format_denials_each_counted(self, fake_projects, capsys):
        """Two current-format denials with distinct tool_use_ids count as two events —
        dedup collapses same-id pairs, not distinct denials."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _hook_deny_current("Commit blocked by code-review gate.", tool_id="toolu_a"),
            _hook_deny_current("Push blocked by ready-for-review gate.", tool_id="toolu_b"),
        ])
        _mod.cmd_review_trace(_review_trace_args())
        out = capsys.readouterr().out
        denial_lines = [ln for ln in out.splitlines() if ln.startswith("  [") and "denial" in ln]
        assert len(denial_lines) == 2
        assert "denials=2" in out

    def test_current_format_denial_with_list_content_detected(self, fake_projects, capsys):
        """A current-format denial whose tool_result content is a list of text blocks
        (not a bare string) is still detected."""
        rec = _hook_deny_current("placeholder")
        rec["message"]["content"][0]["content"] = [
            {"type": "text", "text": "Commit blocked by code-review gate: run /code-review."},
        ]
        _write_jsonl(fake_projects / "sess.jsonl", [rec])
        _mod.cmd_review_trace(_review_trace_args())
        out = capsys.readouterr().out
        denial_lines = [ln for ln in out.splitlines() if ln.startswith("  [") and "denial" in ln]
        assert len(denial_lines) == 1
        assert "code-review gate" in denial_lines[0]

    def test_deny_only_matches_current_format_denial(self, fake_projects, capsys):
        """--deny-only retains a session whose only denial is current-format."""
        _write_jsonl(fake_projects / "cur.jsonl", [
            _hook_deny_current("Push to a branch blocked by ready-for-review gate."),
        ])
        _write_jsonl(fake_projects / "none.jsonl", [
            _asst("claude-opus-4-7", branch="feat", ts="2026-05-19T10:00:00.000Z",
                  content=[_agent_use("a1", "staff-sdet")]),
        ])
        _mod.cmd_review_trace(_review_trace_args(deny_only=True))
        out = capsys.readouterr().out
        assert "cur.jsonl" in out
        assert "none.jsonl" not in out


# ---------------------------------------------------------------------------
# audit-routing
# ---------------------------------------------------------------------------


def _opus(content: list, *, out: int = 100, cr: int = 0, ts: str = "2026-05-19T10:00:00.000Z") -> dict:
    """Build an Opus assistant record with explicit usage values for audit-routing tests."""
    rec = _asst(
        "claude-opus-4-7",
        branch="main",
        ts=ts,
        content=content,
    )
    rec["message"]["usage"] = {
        "input_tokens": 50,
        "output_tokens": out,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": cr,
    }
    return rec


def _exit_plan_mode(tool_id: str = "epm1") -> dict:
    return {"type": "tool_use", "id": tool_id, "name": "ExitPlanMode", "input": {}}


def _thinking_block() -> dict:
    return {"type": "thinking", "thinking": "some thought"}


def _audit_routing_args(
    *,
    projects: str = "*",
    since: str | None = None,
    top: int = 20,
    redact: bool = False,
) -> object:
    return type("A", (), {
        "projects": projects,
        "since": since,
        "top": top,
        "redact": redact,
    })()


def _extract_corpus_class_tokens(out: str, cls: str) -> int:
    """Parse output-token value for a class from the corpus aggregate section."""
    for line in out.splitlines():
        stripped = line.strip()
        if stripped.startswith(cls):
            parts = stripped.split()
            # Format: "class-name  output_tokens  cache_read_tokens"
            # Output tokens come after the class name (may have commas)
            if len(parts) >= 2:
                return int(parts[1].replace(",", ""))
    return 0


class TestAuditRouting:
    def test_basic_per_class_routing(self, fake_projects, capsys):
        """One Opus turn of each class; corpus aggregate totals must match synthesized usage."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            # orchestration: Agent tool_use
            _opus([_agent_use("a1", "code-writer")], out=100),
            # judgment: Skill tool_use (opens span, turn itself is judgment)
            _opus([_skill_use("s1", "code-review")], out=200),
            # user turn resets span
            _user_msg("hi", branch="main"),
            # code-write: Edit tool_use (span closed)
            _opus([{"type": "tool_use", "id": "e1", "name": "Edit", "input": {}}], out=300),
            # code-read: only Read tool_use
            _opus([{"type": "tool_use", "id": "r1", "name": "Read", "input": {}}], out=400),
            # pure-thinking: thinking block, no tool_use
            _opus([_thinking_block()], out=500),
            # other: no tool_use, no thinking
            _opus([], out=600),
        ])
        _mod.cmd_audit_routing(_audit_routing_args())
        out = capsys.readouterr().out
        assert _extract_corpus_class_tokens(out, "orchestration") == 100
        assert _extract_corpus_class_tokens(out, "judgment") == 200
        assert _extract_corpus_class_tokens(out, "code-write") == 300
        assert _extract_corpus_class_tokens(out, "code-read") == 400
        assert _extract_corpus_class_tokens(out, "pure-thinking") == 500
        assert _extract_corpus_class_tokens(out, "other") == 600

    def test_judgment_span_covers_subsequent_read_and_write_turns(self, fake_projects, capsys):
        """Skill invocation opens a span; Read/Write turns inside span → judgment, not code-read/write.
        User turn resets span; Edit turn after reset → code-write."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            # User turn (normal) — no plan-mode text
            _user_msg("start", branch="main"),
            # Skill invocation: opens span; turn itself → judgment
            _opus([_skill_use("s1", "code-review")], out=10),
            # Read turn inside span → judgment (not code-read)
            _opus([{"type": "tool_use", "id": "r1", "name": "Read", "input": {}}], out=20),
            # Write turn inside span → judgment (not code-write)
            _opus([{"type": "tool_use", "id": "w1", "name": "Write", "input": {}}], out=30),
            # User turn resets span
            _user_msg("continue", branch="main"),
            # Edit turn after span closed → code-write
            _opus([{"type": "tool_use", "id": "e1", "name": "Edit", "input": {}}], out=40),
        ])
        _mod.cmd_audit_routing(_audit_routing_args())
        out = capsys.readouterr().out
        # judgment = skill turn (10) + read inside span (20) + write inside span (30) = 60
        assert _extract_corpus_class_tokens(out, "judgment") == 60
        # code-write = edit after reset = 40
        assert _extract_corpus_class_tokens(out, "code-write") == 40
        # code-read = 0 (the Read turn was inside the span → judgment)
        assert _extract_corpus_class_tokens(out, "code-read") == 0

    def test_plan_mode_span_and_exit_plan_mode(self, fake_projects, capsys):
        """Plan-mode activation → subsequent turns are judgment until ExitPlanMode.
        ExitPlanMode turn itself is still judgment; Edit after exit → code-write."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            # User turn that activates plan mode
            _user_msg([{"type": "text", "text": "Plan mode is active"}], branch="main"),
            # Read turn inside plan-mode → judgment
            _opus([{"type": "tool_use", "id": "r1", "name": "Read", "input": {}}], out=50),
            # ExitPlanMode turn: still in plan-mode span → judgment; clears flag for next turn
            _opus([_exit_plan_mode("epm1")], out=75),
            # Edit turn after plan-mode cleared → code-write
            _opus([{"type": "tool_use", "id": "e1", "name": "Edit", "input": {}}], out=90),
        ])
        _mod.cmd_audit_routing(_audit_routing_args())
        out = capsys.readouterr().out
        # judgment = read (50) + ExitPlanMode (75) = 125
        assert _extract_corpus_class_tokens(out, "judgment") == 125
        # code-write = edit after exit = 90
        assert _extract_corpus_class_tokens(out, "code-write") == 90
        # code-read = 0
        assert _extract_corpus_class_tokens(out, "code-read") == 0

    def test_redact_flag_anonymizes_project_names(self, fake_projects, capsys):
        """--redact replaces project dir names with private-project-1/2/…; claude-config kept."""
        # Session in the default project (-home-user-testrepo → 'home/testrepo' via derivation)
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_agent_use("a1", "code-writer")], out=100),
        ])
        # A second project whose derived name is 'claude-config'
        proj_cc = fake_projects.parent / "-home-user-claude-config"
        proj_cc.mkdir(parents=True)
        _write_jsonl(proj_cc / "sess.jsonl", [
            _opus([_agent_use("a2", "code-writer")], out=200),
        ])
        args = _audit_routing_args(redact=True)
        _mod.cmd_audit_routing(args)
        out = capsys.readouterr().out
        # claude-config must appear without redaction
        assert "claude-config" in out
        # The default project name should NOT appear verbatim (redacted)
        # (the derived label for -home-user-testrepo is 'user/testrepo' or 'testrepo')
        # We just verify that private-project labels appear in the output
        assert "private-project-" in out

    def test_since_filter_excludes_out_of_window_turns(self, fake_projects, capsys):
        """--since filter: turn outside window excluded; only in-window turn appears in aggregate."""
        old_ts = "2020-01-01T00:00:00.000Z"   # far in the past — always out-of-window
        new_ts = "2099-12-31T00:00:00.000Z"   # far in the future — always in-window
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_agent_use("a1", "code-writer")], out=111, ts=old_ts),
            _opus([{"type": "tool_use", "id": "e1", "name": "Edit", "input": {}}], out=222, ts=new_ts),
        ])
        # Use "1d" window: old_ts is excluded, new_ts is included
        args = _audit_routing_args(since="1d")
        _mod.cmd_audit_routing(args)
        out = capsys.readouterr().out
        # Only the new_ts turn (code-write, 222) should appear
        assert _extract_corpus_class_tokens(out, "code-write") == 222
        assert _extract_corpus_class_tokens(out, "orchestration") == 0

    def test_no_opus_turns_produces_empty_aggregate(self, fake_projects, capsys):
        """Session with only Sonnet turns produces no rows and zero corpus totals."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:00:00Z",
                  content=[{"type": "tool_use", "id": "e1", "name": "Edit", "input": {}}]),
        ])
        _mod.cmd_audit_routing(_audit_routing_args())
        out = capsys.readouterr().out
        # Corpus aggregate section must be present but all classes are zero
        assert "Corpus aggregate" in out
        assert _extract_corpus_class_tokens(out, "code-write") == 0

    def test_sonnet_tier_estimate_printed(self, fake_projects, capsys):
        """Sonnet-tier estimate line appears and reflects code-write + code-read total."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([{"type": "tool_use", "id": "e1", "name": "Edit", "input": {}}], out=300),
            _opus([{"type": "tool_use", "id": "r1", "name": "Read", "input": {}}], out=400),
        ])
        _mod.cmd_audit_routing(_audit_routing_args())
        out = capsys.readouterr().out
        assert "Sonnet-tier estimate: 700" in out

    def test_orchestration_takes_priority_over_active_judgment_span(self):
        """orchestration is first-match: Agent turn inside an open span → orchestration, not judgment."""
        result = _mod._classify_opus_turn(
            [_agent_use("a1", "code-writer")],
            in_judgment_span=True,
            plan_mode_active=False,
        )
        assert result == "orchestration"

    def test_opus_turn_with_empty_usage_is_skipped(self, fake_projects, capsys):
        """Opus turn with empty usage dict is excluded; corpus totals stay zero."""
        rec = _asst("claude-opus-4-7", branch="main", ts="2026-05-19T10:00:00.000Z",
                    content=[{"type": "tool_use", "id": "e1", "name": "Edit", "input": {}}])
        # usage is {} by default from _asst — cmd_audit_routing skips falsy usage
        _write_jsonl(fake_projects / "sess.jsonl", [rec])
        _mod.cmd_audit_routing(_audit_routing_args())
        out = capsys.readouterr().out
        assert _extract_corpus_class_tokens(out, "code-write") == 0

    def test_judgment_span_persists_across_unrecognized_skill_invocation(self, fake_projects, capsys):
        """An unrecognized Skill call inside an open span does not close the span."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            # Opens span
            _opus([_skill_use("s1", "code-review")], out=10),
            # Unrecognized skill — span stays open; turn is still judgment
            _opus([_skill_use("s2", "some-unknown-skill")], out=20),
            # Read turn inside still-open span → judgment, not code-read
            _opus([{"type": "tool_use", "id": "r1", "name": "Read", "input": {}}], out=30),
            # User turn closes span
            _user_msg("continue", branch="main"),
            # Read turn after span closed → code-read
            _opus([{"type": "tool_use", "id": "r2", "name": "Read", "input": {}}], out=40),
        ])
        _mod.cmd_audit_routing(_audit_routing_args())
        out = capsys.readouterr().out
        # All three turns inside the span (10 + 20 + 30) → judgment
        assert _extract_corpus_class_tokens(out, "judgment") == 60
        # Only the post-span Read turn → code-read
        assert _extract_corpus_class_tokens(out, "code-read") == 40

    def test_combined_plan_mode_and_judgment_span_independent_tracking(self, fake_projects, capsys):
        """ExitPlanMode clears plan_mode_active but an open judgment span keeps the classification."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            # Activate plan-mode
            _user_msg([{"type": "text", "text": "Plan mode is active"}], branch="main"),
            # Judgment-skill invocation: opens span AND plan-mode is active
            _opus([_skill_use("s1", "code-review")], out=10),
            # ExitPlanMode: clears plan_mode_active; span from code-review still open
            _opus([_exit_plan_mode("epm1")], out=20),
            # Turn after ExitPlanMode: span still open (no user turn yet) → judgment
            _opus([{"type": "tool_use", "id": "r1", "name": "Read", "input": {}}], out=30),
            # User turn resets span
            _user_msg("done", branch="main"),
            # Read turn after both flags cleared → code-read
            _opus([{"type": "tool_use", "id": "r2", "name": "Read", "input": {}}], out=40),
        ])
        _mod.cmd_audit_routing(_audit_routing_args())
        out = capsys.readouterr().out
        # judgment = skill-open (10) + ExitPlanMode (20) + post-exit-still-in-span read (30) = 60
        assert _extract_corpus_class_tokens(out, "judgment") == 60
        # code-read = post-user-reset read = 40
        assert _extract_corpus_class_tokens(out, "code-read") == 40

    def test_since_filter_excludes_turn_with_missing_timestamp(self, fake_projects, capsys):
        """With --since active, turns lacking a timestamp field are excluded."""
        rec_no_ts = _asst("claude-opus-4-7", branch="main",
                           content=[{"type": "tool_use", "id": "e1", "name": "Edit", "input": {}}])
        rec_no_ts["message"]["usage"] = {"input_tokens": 50, "output_tokens": 200,
                                          "cache_creation_input_tokens": 0,
                                          "cache_read_input_tokens": 0}
        # No "timestamp" key on the record
        assert "timestamp" not in rec_no_ts
        _write_jsonl(fake_projects / "sess.jsonl", [rec_no_ts])
        args = _audit_routing_args(since="1d")
        _mod.cmd_audit_routing(args)
        out = capsys.readouterr().out
        # Turn with no timestamp is excluded by the --since filter
        assert _extract_corpus_class_tokens(out, "code-write") == 0


# ---------------------------------------------------------------------------
# handoff-ratio
# ---------------------------------------------------------------------------


def _handoff_skill_block() -> dict:
    """Build a Skill tool_use block for /handoff."""
    return {"type": "tool_use", "id": "tool-handoff-1", "name": "Skill", "input": {"skill": "handoff"}}


def _compaction_record(ts: str = "2026-05-19T10:00:00.000Z") -> dict:
    """Build a compact_boundary system record (the shape Claude Code writes on auto-compaction)."""
    return {
        "type": "system",
        "subtype": "compact_boundary",
        "content": "Conversation compacted",
        "timestamp": ts,
        "compactMetadata": {"trigger": "auto", "preTokens": 160000, "postTokens": 11000, "durationMs": 145000},
    }


def _handoff_args(since: str | None = None, projects: str = "*") -> argparse.Namespace:
    return type("A", (), {"projects": projects, "since": since, "debug_detector": False})()


class TestHandoffRatio:
    def test_handoff_ratio_counts_handoffs_and_compactions(self, fake_projects, capsys):
        """Sessions with handoff skill calls and compaction events are counted correctly."""
        # Session 1: has handoff
        _write_jsonl(fake_projects / "sess1.jsonl", [
            _asst("claude-sonnet-4-6", branch="feat", ts="2026-05-11T09:00:00.000Z",
                  content=[_handoff_skill_block()]),
        ])
        # Session 2: has compaction
        _write_jsonl(fake_projects / "sess2.jsonl", [
            _compaction_record("2026-05-11T10:00:00.000Z"),
        ])
        # Session 3: both handoff and compaction (counts in both columns)
        _write_jsonl(fake_projects / "sess3.jsonl", [
            _asst("claude-opus-4-7", branch="main", ts="2026-05-11T10:00:00.000Z",
                  content=[_handoff_skill_block()]),
            _compaction_record("2026-05-11T11:00:00.000Z"),
        ])
        _mod.cmd_handoff_ratio(_handoff_args())
        out = capsys.readouterr().out
        assert "Handoffs" in out
        assert "Compactions" in out
        # Totals row: 2 handoffs, 2 compactions, 50% ratio.
        total_line = [ln for ln in out.splitlines() if ln.startswith("Total")]
        assert total_line
        parts = total_line[0].split()
        assert int(parts[1]) == 2, f"Expected 2 handoffs in Total row, got: {total_line[0]!r}"
        assert int(parts[2]) == 2, f"Expected 2 compactions in Total row, got: {total_line[0]!r}"
        assert "50.0%" in out

    def test_handoff_ratio_empty_corpus_no_divide_by_zero(self, fake_projects, capsys):
        """Empty corpus (no handoffs or compactions) prints a graceful 'no data' message."""
        # Write a session with no handoff or compaction records.
        _write_jsonl(fake_projects / "plain.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:00:00.000Z"),
        ])
        _mod.cmd_handoff_ratio(_handoff_args())
        out = capsys.readouterr().out
        assert "No handoff or compaction events found" in out

    def test_handoff_ratio_since_filter_excludes_older(self, fake_projects, capsys):
        """Events with timestamps before --since are excluded from counts."""
        # Old session: before cutoff
        _write_jsonl(fake_projects / "old.jsonl", [
            _compaction_record("2026-01-15T10:00:00.000Z"),
        ])
        # New session: after cutoff
        _write_jsonl(fake_projects / "new.jsonl", [
            _asst("claude-sonnet-4-6", branch="feat", ts="2026-05-19T10:00:00.000Z",
                  content=[_handoff_skill_block()]),
        ])
        _mod.cmd_handoff_ratio(_handoff_args(since="2026-05-01"))
        out = capsys.readouterr().out
        # Only the new session (handoff) should appear; old compaction excluded.
        assert "Handoffs" in out
        total_line = [ln for ln in out.splitlines() if ln.startswith("Total")]
        assert total_line
        # Total row: "Total  <handoffs>  <compactions>  <ratio%>"
        parts = total_line[0].split()
        assert int(parts[1]) == 1, f"Expected 1 handoff in Total row, got: {total_line[0]!r}"
        assert int(parts[2]) == 0, f"Expected 0 compactions in Total row, got: {total_line[0]!r}"


# ---------------------------------------------------------------------------
# audit-routing-shape
# ---------------------------------------------------------------------------


def _read_use(tool_id: str, file_path: str) -> dict:
    """Build a Read tool_use block with the given file_path."""
    return {"type": "tool_use", "id": tool_id, "name": "Read", "input": {"file_path": file_path}}


def _grep_use(tool_id: str) -> dict:
    return {"type": "tool_use", "id": tool_id, "name": "Grep", "input": {"pattern": "x", "path": "."}}


def _glob_use(tool_id: str) -> dict:
    return {"type": "tool_use", "id": tool_id, "name": "Glob", "input": {"pattern": "**/*.py", "path": "."}}


def _audit_routing_shape_args(
    *,
    projects: str = "*",
    since: str | None = None,
) -> object:
    return type("A", (), {
        "projects": projects,
        "since": since,
    })()


def _extract_shape_d1(out: str, bucket: str) -> tuple[int, int]:
    """Parse (turn_count, output_tokens) for a D1 bucket from audit-routing-shape output."""
    in_d1 = False
    for line in out.splitlines():
        stripped = line.strip()
        if "D1" in stripped and "Files Read" in stripped:
            in_d1 = True
            continue
        if in_d1 and stripped.startswith("###"):
            break
        if in_d1 and stripped.startswith(bucket):
            parts = stripped.split()
            if len(parts) >= 3:
                return int(parts[1].replace(",", "")), int(parts[2].replace(",", ""))
    return 0, 0


def _extract_shape_d2(out: str, bucket: str) -> tuple[int, int]:
    """Parse (streak_count, output_tokens) for a D2 bucket from audit-routing-shape output."""
    in_d2 = False
    for line in out.splitlines():
        stripped = line.strip()
        if "D2" in stripped and "streak" in stripped.lower():
            in_d2 = True
            continue
        if in_d2 and stripped.startswith("###"):
            break
        if in_d2 and stripped.startswith(bucket):
            parts = stripped.split()
            if len(parts) >= 3:
                return int(parts[1].replace(",", "")), int(parts[2].replace(",", ""))
    return 0, 0


def _extract_shape_d3(out: str, case: str) -> tuple[int, int]:
    """Parse (turn_count, output_tokens) for a D3 case from audit-routing-shape output."""
    in_d3 = False
    for line in out.splitlines():
        stripped = line.strip()
        if "D3" in stripped and "Read-then-edit" in stripped:
            in_d3 = True
            continue
        if in_d3 and stripped.startswith("###"):
            break
        if in_d3 and stripped.startswith("####"):
            break
        if in_d3 and stripped.startswith(case):
            parts = stripped.split()
            if len(parts) >= 3:
                return int(parts[1].replace(",", "")), int(parts[2].replace(",", ""))
    return 0, 0


def _extract_shape_d3_xtab(out: str, case: str, bucket: str) -> tuple[int, int]:
    """Parse (turn_count, output_tokens) for a D3 × D1 cross-tab cell."""
    in_xtab = False
    for line in out.splitlines():
        stripped = line.strip()
        if "D3 × D1 cross-tab" in stripped or "D3 x D1 cross-tab" in stripped.lower():
            in_xtab = True
            continue
        if in_xtab and (stripped.startswith("###") or stripped.startswith("Dispatchable")):
            break
        if in_xtab and stripped.startswith(case) and bucket in stripped:
            parts = stripped.split()
            # Format: case  bucket  turns  tokens
            if len(parts) >= 4:
                return int(parts[2].replace(",", "")), int(parts[3].replace(",", ""))
    return 0, 0


class TestAuditRoutingShape:
    def test_d1_buckets(self, fake_projects, capsys):
        """One turn per D1 bucket (0, 1, 2, 5, 9 Reads); each bucket has turn count 1 and correct tokens."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            # 0 Reads → bucket "0"
            _opus([_bash_use("b1", "ls")], out=10),
            # 1 Read → bucket "1"
            _opus([_read_use("r1", "/a.txt")], out=20),
            # 2 Reads → bucket "2-3"
            _opus([_read_use("r2", "/a.txt"), _read_use("r3", "/b.txt")], out=30),
            # 5 Reads → bucket "4-7"
            _opus([_read_use(f"r{i}", f"/f{i}.txt") for i in range(10, 15)], out=40),
            # 9 Reads → bucket "8+"
            _opus([_read_use(f"r{i}", f"/f{i}.txt") for i in range(20, 29)], out=50),
        ])
        _mod.cmd_audit_routing_shape(_audit_routing_shape_args())
        out = capsys.readouterr().out
        assert _extract_shape_d1(out, "0") == (1, 10)
        assert _extract_shape_d1(out, "1") == (1, 20)
        assert _extract_shape_d1(out, "2-3") == (1, 30)
        assert _extract_shape_d1(out, "4-7") == (1, 40)
        assert _extract_shape_d1(out, "8+") == (1, 50)

    def test_d1_excludes_grep_glob_bash(self, fake_projects, capsys):
        """Turns with only Grep or Glob tool_use land in bucket '0' (neither counts as a Read)."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_grep_use("g1")], out=77),
            _opus([_glob_use("gl1")], out=88),
        ])
        _mod.cmd_audit_routing_shape(_audit_routing_shape_args())
        out = capsys.readouterr().out
        turns_0, tokens_0 = _extract_shape_d1(out, "0")
        assert turns_0 == 2
        assert tokens_0 == 165  # 77 + 88
        # bucket "1" must be empty
        assert _extract_shape_d1(out, "1") == (0, 0)

    def test_d2_streak_1(self, fake_projects, capsys):
        """Two sessions each with one code-read turn surrounded by non-code-read turns produce
        two distinct streaks of length 1; bucket '1' streak count = 2."""
        # Session A: code-write, code-read, code-write → streak of length 1
        _write_jsonl(fake_projects / "sess_a.jsonl", [
            _opus([{"type": "tool_use", "id": "e1", "name": "Edit", "input": {}}], out=100),
            _opus([_read_use("r1", "/a.txt")], out=50),
            _opus([{"type": "tool_use", "id": "e2", "name": "Edit", "input": {}}], out=100),
        ])
        # Session B: code-write, code-read, code-write → streak of length 1
        _write_jsonl(fake_projects / "sess_b.jsonl", [
            _opus([{"type": "tool_use", "id": "e3", "name": "Edit", "input": {}}], out=100),
            _opus([_read_use("r2", "/b.txt")], out=60),
            _opus([{"type": "tool_use", "id": "e4", "name": "Edit", "input": {}}], out=100),
        ])
        _mod.cmd_audit_routing_shape(_audit_routing_shape_args())
        out = capsys.readouterr().out
        streak_count, streak_out = _extract_shape_d2(out, "1")
        assert streak_count == 2
        assert streak_out == 110  # 50 + 60

    def test_d2_streak_2(self, fake_projects, capsys):
        """Two consecutive code-read turns followed by a non-code-read: one streak, bucket '2', count = 1."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/a.txt")], out=30),
            _opus([_read_use("r2", "/b.txt")], out=40),
            _opus([{"type": "tool_use", "id": "e1", "name": "Edit", "input": {}}], out=100),
        ])
        _mod.cmd_audit_routing_shape(_audit_routing_shape_args())
        out = capsys.readouterr().out
        streak_count, streak_out = _extract_shape_d2(out, "2")
        assert streak_count == 1
        assert streak_out == 70  # 30 + 40

    def test_d2_streak_3_5(self, fake_projects, capsys):
        """A session with 4 consecutive code-read turns: one streak, bucket '3-5', count = 1."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use(f"r{i}", f"/f{i}.txt")], out=25) for i in range(4)
        ])
        _mod.cmd_audit_routing_shape(_audit_routing_shape_args())
        out = capsys.readouterr().out
        streak_count, streak_out = _extract_shape_d2(out, "3-5")
        assert streak_count == 1
        assert streak_out == 100  # 4 × 25

    def test_d2_streak_6_10(self, fake_projects, capsys):
        """Seven consecutive code-read turns: one streak, bucket '6-10', count = 1."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use(f"r{i}", f"/f{i}.txt")], out=10) for i in range(7)
        ])
        _mod.cmd_audit_routing_shape(_audit_routing_shape_args())
        out = capsys.readouterr().out
        streak_count, streak_out = _extract_shape_d2(out, "6-10")
        assert streak_count == 1
        assert streak_out == 70  # 7 × 10

    def test_d2_streak_11_plus(self, fake_projects, capsys):
        """Twelve consecutive code-read turns: one streak, bucket '11+', count = 1."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use(f"r{i}", f"/f{i}.txt")], out=10) for i in range(12)
        ])
        _mod.cmd_audit_routing_shape(_audit_routing_shape_args())
        out = capsys.readouterr().out
        streak_count, streak_out = _extract_shape_d2(out, "11+")
        assert streak_count == 1
        assert streak_out == 120  # 12 × 10

    def test_d2_streak_split_by_user(self, fake_projects, capsys):
        """A user turn between two code-read turns splits them into two streaks of length 1.
        Specifically, [r1, user-turn, r2] must NOT be treated as one streak of 2."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/a.txt")], out=10),
            _user_msg("continue", branch="main"),
            _opus([_read_use("r2", "/b.txt")], out=10),
        ])
        _mod.cmd_audit_routing_shape(_audit_routing_shape_args())
        out = capsys.readouterr().out
        # User turn breaks the streak — two separate streaks of length 1, not one streak of 2
        streak_1_count, _ = _extract_shape_d2(out, "1")
        streak_2_count, _ = _extract_shape_d2(out, "2")
        streak_35_count, _ = _extract_shape_d2(out, "3-5")
        assert streak_1_count == 2
        assert streak_2_count == 0
        assert streak_35_count == 0

    def test_d3_inline_edit(self, fake_projects, capsys):
        """A code-read turn followed within 3 turns by a code-write with no orchestration → case inline-edit."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/a.txt")], out=100),
            _opus([{"type": "tool_use", "id": "e1", "name": "Edit", "input": {}}], out=200),
        ])
        _mod.cmd_audit_routing_shape(_audit_routing_shape_args())
        out = capsys.readouterr().out
        turns, tokens = _extract_shape_d3(out, "inline-edit")
        assert turns == 1
        assert tokens == 100

    def test_d3_dispatched(self, fake_projects, capsys):
        """A code-read turn followed within 3 turns by an orchestration turn → case dispatched."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/a.txt")], out=100),
            _opus([_agent_use("a1", "code-writer")], out=200),
        ])
        _mod.cmd_audit_routing_shape(_audit_routing_shape_args())
        out = capsys.readouterr().out
        turns, tokens = _extract_shape_d3(out, "dispatched")
        assert turns == 1
        assert tokens == 100

    def test_d3_neither(self, fake_projects, capsys):
        """A code-read turn with no code-write or orchestration within 3 turns → case neither."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/a.txt")], out=100),
            # Three non-code-write, non-orchestration turns
            _opus([], out=10),
            _opus([], out=10),
            _opus([], out=10),
            # code-write beyond the 3-turn window — must not affect classification
            _opus([{"type": "tool_use", "id": "e1", "name": "Edit", "input": {}}], out=200),
        ])
        _mod.cmd_audit_routing_shape(_audit_routing_shape_args())
        out = capsys.readouterr().out
        turns, tokens = _extract_shape_d3(out, "neither")
        assert turns == 1
        assert tokens == 100
        # inline-edit must be 0 turns (the edit was outside the 3-turn window)
        assert _extract_shape_d3(out, "inline-edit") == (0, 0)

    def test_d3_inline_edit_at_window_boundary(self, fake_projects, capsys):
        """A code-write at exactly lookahead position 3 (the last inclusive position) is inline-edit."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/a.txt")], out=100),
            _opus([], out=10),  # lookahead 1: other
            _opus([], out=10),  # lookahead 2: other
            _opus([{"type": "tool_use", "id": "e1", "name": "Edit", "input": {}}], out=200),  # lookahead 3
        ])
        _mod.cmd_audit_routing_shape(_audit_routing_shape_args())
        out = capsys.readouterr().out
        turns, tokens = _extract_shape_d3(out, "inline-edit")
        assert turns == 1
        assert tokens == 100
        assert _extract_shape_d3(out, "neither") == (0, 0)

    def test_d3_inline_edit_across_user_turn(self, fake_projects, capsys):
        """A code-read turn followed by a user turn then a code-write within 3 Opus turns
        must still classify as inline-edit — user turns don't consume lookahead budget."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/a.txt")], out=100),
            _user_msg("continue", branch="main"),
            _opus([{"type": "tool_use", "id": "e1", "name": "Edit", "input": {}}], out=200),
        ])
        _mod.cmd_audit_routing_shape(_audit_routing_shape_args())
        out = capsys.readouterr().out
        turns, tokens = _extract_shape_d3(out, "inline-edit")
        assert turns == 1
        assert tokens == 100
        assert _extract_shape_d3(out, "neither") == (0, 0)

    def test_d3_cross_tab_file_bucket(self, fake_projects, capsys):
        """A code-read turn with 2 Reads followed by an inline edit appears in cross-tab inline-edit × 2-3."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/a.txt"), _read_use("r2", "/b.txt")], out=100),
            _opus([{"type": "tool_use", "id": "e1", "name": "Edit", "input": {}}], out=200),
        ])
        _mod.cmd_audit_routing_shape(_audit_routing_shape_args())
        out = capsys.readouterr().out
        turns, tokens = _extract_shape_d3_xtab(out, "inline-edit", "2-3")
        assert turns == 1
        assert tokens == 100

    def test_d2_and_d3_streak_then_edit(self, fake_projects, capsys):
        """Three consecutive code-read turns followed by a code-write: D2 sees one streak in
        '3-5' AND D3 sees all three turns as inline-edit (the edit is within 3 Opus turns of each)."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/a.txt")], out=100),
            _opus([_read_use("r2", "/b.txt")], out=100),
            _opus([_read_use("r3", "/c.txt")], out=100),
            _opus([{"type": "tool_use", "id": "e1", "name": "Edit", "input": {}}], out=200),
        ])
        _mod.cmd_audit_routing_shape(_audit_routing_shape_args())
        out = capsys.readouterr().out
        streak_count, streak_out = _extract_shape_d2(out, "3-5")
        assert streak_count == 1
        assert streak_out == 300  # 3 × 100
        d3_turns, d3_out = _extract_shape_d3(out, "inline-edit")
        assert d3_turns == 3  # all three code-read turns see the edit within 3 Opus turns
        assert d3_out == 300

    def test_cross_validation_with_audit_routing(self, fake_projects, capsys):
        """D1 total output tokens across all buckets must equal audit-routing's corpus code-read total.
        This guards against drift between the duplicated judgment-span state machines.
        A Read turn inside a judgment span (99 tokens) must be excluded from both totals."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            # Skill invocation opens a judgment span; turn itself → judgment (50 tokens)
            _opus([_skill_use("s1", "code-review")], out=50),
            # Read inside judgment span → judgment, NOT code-read (99 tokens — must not appear in D1)
            _opus([_read_use("rx", "/span-file.txt")], out=99),
            # User turn resets span
            _user_msg("done", branch="main"),
            # 3 code-read turns outside span (100 each) + 1 code-write turn (200)
            _opus([_read_use("r1", "/a.txt")], out=100),
            _opus([_read_use("r2", "/b.txt")], out=100),
            _opus([_read_use("r3", "/c.txt")], out=100),
            _opus([{"type": "tool_use", "id": "e1", "name": "Edit", "input": {}}], out=200),
        ])
        # Run audit-routing and capture code-read corpus total
        _mod.cmd_audit_routing(_audit_routing_args())
        routing_out = capsys.readouterr().out
        routing_code_read = _extract_corpus_class_tokens(routing_out, "code-read")

        # Run audit-routing-shape and sum D1 output tokens across all buckets
        _mod.cmd_audit_routing_shape(_audit_routing_shape_args())
        shape_out = capsys.readouterr().out
        d1_total = sum(_extract_shape_d1(shape_out, bkt)[1] for bkt in _mod._D1_BUCKETS)

        assert d1_total == routing_code_read  # both should be 300 (judgment-span Read excluded from both)

    def test_judgment_span_excluded_from_distributions(self, fake_projects, capsys):
        """A Read turn inside a judgment span must not appear in D1, D2, or D3."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            # Open judgment span
            _opus([_skill_use("s1", "code-review")], out=50),
            # Read inside judgment span — must be excluded from all distributions
            _opus([_read_use("r1", "/a.txt")], out=99),
            # User turn closes span
            _user_msg("done", branch="main"),
            # Read after span closed — must appear in D1
            _opus([_read_use("r2", "/b.txt")], out=77),
        ])
        _mod.cmd_audit_routing_shape(_audit_routing_shape_args())
        out = capsys.readouterr().out
        # Only the post-span Read (77 tokens, 1 Read → bucket "1") should appear
        turns_1, tokens_1 = _extract_shape_d1(out, "1")
        assert turns_1 == 1
        assert tokens_1 == 77
        # Total D1 output tokens across all buckets must be 77 (not 176)
        d1_total = sum(_extract_shape_d1(out, bkt)[1] for bkt in _mod._D1_BUCKETS)
        assert d1_total == 77

    def test_dispatchable_share_summary_value(self, fake_projects, capsys):
        """Dispatchable-share percentage is computed correctly for a controlled fixture.
        4 code-read turns: A(D1=2-3, D3=inline-edit), B(D1=1, D3=inline-edit),
        C(D1=1, D3=dispatched), D(D1=1, D3=neither). Tokens: 100+200+300+400=1000.
        Dispatchable via D1∪D3: A+C+D = 100+300+400=800 → 80%."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            # Turn A: 2 Reads → D1="2-3" (dispatchable). Followed by inline-edit.
            _opus([_read_use("a1", "/a1.txt"), _read_use("a2", "/a2.txt")], out=100),
            _opus([{"type": "tool_use", "id": "ea", "name": "Edit", "input": {}}], out=50),
            # Turn B: 1 Read → D1="1". Followed by inline-edit within 3 turns.
            _opus([_read_use("b1", "/b1.txt")], out=200),
            _opus([{"type": "tool_use", "id": "eb", "name": "Edit", "input": {}}], out=50),
            # Turn C: 1 Read → D1="1". Followed by orchestration.
            _opus([_read_use("c1", "/c1.txt")], out=300),
            _opus([_agent_use("ag1", "code-writer")], out=50),
            # Turn D: 1 Read → D1="1". No code-write or orchestration within 3 turns.
            _opus([_read_use("d1", "/d1.txt")], out=400),
        ])
        _mod.cmd_audit_routing_shape(_audit_routing_shape_args())
        out = capsys.readouterr().out
        assert "Dispatchable share: 80%" in out

    def test_dispatchable_share_empty_corpus(self, fake_projects, capsys):
        """No code-read turns → dispatchable share prints '—'."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([{"type": "tool_use", "id": "e1", "name": "Edit", "input": {}}], out=100),
        ])
        _mod.cmd_audit_routing_shape(_audit_routing_shape_args())
        out = capsys.readouterr().out
        assert "Dispatchable share: —" in out


# ---------------------------------------------------------------------------
# audit-routing-samples
# ---------------------------------------------------------------------------


def _edit_use(tool_id: str) -> dict:
    return {"type": "tool_use", "id": tool_id, "name": "Edit", "input": {"file_path": "/foo.py"}}


def _audit_routing_samples_args(
    *,
    projects: str = "*",
    since: str | None = None,
    sample: int = 100,
    seed: int | None = 42,
    output_format: str = "json",
) -> object:
    return type("A", (), {
        "projects": projects,
        "since": since,
        "sample": sample,
        "seed": seed,
        "output_format": output_format,
    })()


class TestAuditRoutingSamples:
    def test_code_read_turn_emitted(self, fake_projects, capsys):
        """A single code-read turn produces one record; verify session_id, turn_index,
        and assistant_tool_call fields."""
        _write_jsonl(fake_projects / "abc12345-test.jsonl", [
            _opus([_read_use("r1", "/foo/bar.py")], out=100),
        ])
        _mod.cmd_audit_routing_samples(_audit_routing_samples_args())
        records = json.loads(capsys.readouterr().out)
        assert len(records) == 1
        rec = records[0]
        assert rec["session_id"] == "abc12345-test"
        assert rec["turn_index"] == 0
        assert rec["assistant_tool_call"] == {"name": "Read", "input": {"file_path": "/foo/bar.py"}}

    def test_prior_user_message_captured(self, fake_projects, capsys):
        """A user message immediately before the code-read turn is captured in prior_user_message."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _user_msg([{"type": "text", "text": "Please read the file"}]),
            _opus([_read_use("r1", "/a.py")], out=100),
        ])
        _mod.cmd_audit_routing_samples(_audit_routing_samples_args())
        records = json.loads(capsys.readouterr().out)
        assert len(records) == 1
        assert records[0]["prior_user_message"] == "Please read the file"

    def test_prior_user_message_empty_when_none(self, fake_projects, capsys):
        """No preceding user turn → prior_user_message is empty string."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/a.py")], out=100),
        ])
        _mod.cmd_audit_routing_samples(_audit_routing_samples_args())
        records = json.loads(capsys.readouterr().out)
        assert len(records) == 1
        assert records[0]["prior_user_message"] == ""

    def test_next_action_edit(self, fake_projects, capsys):
        """Next Opus turn is Edit (code-write) → next_assistant_action is 'edit'."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/a.py")], out=100),
            _opus([_edit_use("e1")], out=200),
        ])
        _mod.cmd_audit_routing_samples(_audit_routing_samples_args())
        records = json.loads(capsys.readouterr().out)
        assert len(records) == 1
        assert records[0]["next_assistant_action"] == "edit"

    def test_next_action_dispatch(self, fake_projects, capsys):
        """Next Opus turn spawns an Agent (orchestration) → next_assistant_action is 'dispatch'."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/a.py")], out=100),
            _opus([_agent_use("a1", "code-writer")], out=200),
        ])
        _mod.cmd_audit_routing_samples(_audit_routing_samples_args())
        records = json.loads(capsys.readouterr().out)
        assert len(records) == 1
        assert records[0]["next_assistant_action"] == "dispatch"

    def test_next_action_another_read(self, fake_projects, capsys):
        """Next Opus turn is another Read (code-read) → next_assistant_action is 'another-read'."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/a.py")], out=100),
            _opus([_read_use("r2", "/b.py")], out=150),
        ])
        _mod.cmd_audit_routing_samples(_audit_routing_samples_args())
        records = json.loads(capsys.readouterr().out)
        # Two code-read turns produce two records; only check the first turn here
        first = next(r for r in records if r["turn_index"] == 0)
        assert first["next_assistant_action"] == "another-read"

    def test_next_action_respond_to_user(self, fake_projects, capsys):
        """Next Opus turn has no tool_use (text-only 'other' class) → next_assistant_action is 'respond-to-user'."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/a.py")], out=100),
            _opus([{"type": "text", "text": "Here is what I found"}], out=50),
        ])
        _mod.cmd_audit_routing_samples(_audit_routing_samples_args())
        records = json.loads(capsys.readouterr().out)
        assert len(records) == 1
        assert records[0]["next_assistant_action"] == "respond-to-user"

    def test_next_action_other_when_no_next(self, fake_projects, capsys):
        """Code-read is the last turn in the session → next_assistant_action is 'other'."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/a.py")], out=100),
        ])
        _mod.cmd_audit_routing_samples(_audit_routing_samples_args())
        records = json.loads(capsys.readouterr().out)
        assert len(records) == 1
        assert records[0]["next_assistant_action"] == "other"

    def test_next_turn_excerpt_populated(self, fake_projects, capsys):
        """next_turn_excerpt contains text from the next Opus turn."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/a.py")], out=100),
            _opus([{"type": "text", "text": "I read the file and found the answer"}], out=50),
        ])
        _mod.cmd_audit_routing_samples(_audit_routing_samples_args())
        records = json.loads(capsys.readouterr().out)
        assert len(records) == 1
        assert "I read the file" in records[0]["next_turn_excerpt"]

    def test_sample_limit_respected(self, fake_projects, capsys):
        """10 eligible turns with sample=5 → exactly 5 records returned."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use(f"r{i}", f"/f{i}.py")], out=100) for i in range(10)
        ])
        args = _audit_routing_samples_args(sample=5, seed=0)
        _mod.cmd_audit_routing_samples(args)
        records = json.loads(capsys.readouterr().out)
        assert len(records) == 5

    def test_seed_produces_deterministic_output(self, fake_projects, capsys):
        """Same seed on the same corpus produces the same turn selection."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use(f"r{i}", f"/f{i}.py")], out=100) for i in range(20)
        ])
        args_a = _audit_routing_samples_args(sample=5, seed=7)
        _mod.cmd_audit_routing_samples(args_a)
        records_a = json.loads(capsys.readouterr().out)

        args_b = _audit_routing_samples_args(sample=5, seed=7)
        _mod.cmd_audit_routing_samples(args_b)
        records_b = json.loads(capsys.readouterr().out)

        assert [r["turn_index"] for r in records_a] == [r["turn_index"] for r in records_b]

    def test_judgment_span_excluded(self, fake_projects, capsys):
        """Read turns inside a judgment span (opened by a judgment skill) are NOT included."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            # Opens judgment span; turn itself → judgment
            _opus([_skill_use("s1", "code-review")], out=50),
            # Read inside judgment span — must be excluded
            _opus([_read_use("r1", "/span-file.py")], out=99),
            # User turn closes span
            _user_msg([{"type": "text", "text": "continue"}]),
            # Read after span closed — must be included
            _opus([_read_use("r2", "/normal.py")], out=77),
        ])
        _mod.cmd_audit_routing_samples(_audit_routing_samples_args())
        records = json.loads(capsys.readouterr().out)
        # Only the post-span Read should appear
        assert len(records) == 1
        assert records[0]["assistant_tool_call"]["input"]["file_path"] == "/normal.py"

    def test_since_filter_excludes_old_turns(self, fake_projects, capsys):
        """Turns with timestamps older than --since Nd are excluded."""
        old_ts = "2020-01-01T00:00:00.000Z"
        new_ts = "2099-12-31T00:00:00.000Z"
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/old.py")], out=100, ts=old_ts),
            _opus([_read_use("r2", "/new.py")], out=100, ts=new_ts),
        ])
        args = _audit_routing_samples_args(since="1d")
        _mod.cmd_audit_routing_samples(args)
        records = json.loads(capsys.readouterr().out)
        assert len(records) == 1
        assert records[0]["assistant_tool_call"]["input"]["file_path"] == "/new.py"

    def test_non_code_read_turns_not_in_output(self, fake_projects, capsys):
        """code-write, orchestration, and other turns produce no records."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_edit_use("e1")], out=100),
            _opus([_agent_use("a1", "code-writer")], out=200),
            _opus([], out=50),
        ])
        _mod.cmd_audit_routing_samples(_audit_routing_samples_args())
        records = json.loads(capsys.readouterr().out)
        assert len(records) == 0

    def test_cross_validation_with_audit_routing(self, fake_projects, capsys):
        """The count of code-read samples (with sample large enough) equals the D1 total turn
        count from cmd_audit_routing_shape for the same fixture.

        This guards against state-machine drift between the two subcommands. A Read turn
        inside a judgment span (99 tokens) must be excluded from both counts."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            # Skill invocation opens a judgment span; turn itself → judgment (50 tokens)
            _opus([_skill_use("s1", "code-review")], out=50),
            # Read inside judgment span → excluded from both subcommands
            _opus([_read_use("rx", "/span-file.txt")], out=99),
            # User turn resets span
            _user_msg("done"),
            # 3 code-read turns outside span + 1 code-write
            _opus([_read_use("r1", "/a.txt")], out=100),
            _opus([_read_use("r2", "/b.txt")], out=100),
            _opus([_read_use("r3", "/c.txt")], out=100),
            _opus([_edit_use("e1")], out=200),
        ])

        # Run audit-routing-shape: sum D1 turn counts across all buckets
        _mod.cmd_audit_routing_shape(_audit_routing_shape_args())
        shape_out = capsys.readouterr().out
        d1_total_turns = sum(_extract_shape_d1(shape_out, bkt)[0] for bkt in _mod._D1_BUCKETS)

        # Run audit-routing-samples with a large sample to capture all eligible turns
        args = _audit_routing_samples_args(sample=1000, seed=0)
        _mod.cmd_audit_routing_samples(args)
        samples = json.loads(capsys.readouterr().out)

        assert len(samples) == d1_total_turns  # both should be 3

    def test_user_turn_skipped_in_forward_scan(self, fake_projects, capsys):
        """A user turn between a code-read and a code-write is skipped in the forward scan;
        next_assistant_action must still be 'edit' not 'other'."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/a.py")], out=100),
            _user_msg([{"type": "text", "text": "looks good"}]),
            _opus([_edit_use("e1")], out=200),
        ])
        _mod.cmd_audit_routing_samples(_audit_routing_samples_args())
        records = json.loads(capsys.readouterr().out)
        assert len(records) == 1
        assert records[0]["next_assistant_action"] == "edit"

    def test_prior_user_message_nonadjacent(self, fake_projects, capsys):
        """The backward walk finds the most recent user turn even with intervening code-read turns."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _user_msg([{"type": "text", "text": "investigate this"}]),
            _opus([_read_use("r1", "/first.py")], out=100),
            _opus([_read_use("r2", "/second.py")], out=100),
        ])
        _mod.cmd_audit_routing_samples(_audit_routing_samples_args())
        records = json.loads(capsys.readouterr().out)
        assert len(records) == 2
        # Both reads should trace back to the same user message
        assert records[0]["prior_user_message"] == "investigate this"
        assert records[1]["prior_user_message"] == "investigate this"

    def test_next_turn_excerpt_truncated_at_200(self, fake_projects, capsys):
        """next_turn_excerpt is truncated to at most 200 characters."""
        long_text = "x" * 400
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/a.py")], out=100),
            _opus([{"type": "text", "text": long_text}], out=50),
        ])
        _mod.cmd_audit_routing_samples(_audit_routing_samples_args())
        records = json.loads(capsys.readouterr().out)
        assert len(records) == 1
        assert len(records[0]["next_turn_excerpt"]) == 200

    def test_different_seed_produces_different_output(self, fake_projects, capsys):
        """Different seeds on the same corpus produce different selections (shuffle is live)."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use(f"r{i}", f"/f{i}.py")], out=100) for i in range(20)
        ])
        args_a = _audit_routing_samples_args(sample=5, seed=1)
        _mod.cmd_audit_routing_samples(args_a)
        records_a = json.loads(capsys.readouterr().out)

        args_b = _audit_routing_samples_args(sample=5, seed=99)
        _mod.cmd_audit_routing_samples(args_b)
        records_b = json.loads(capsys.readouterr().out)

        assert [r["turn_index"] for r in records_a] != [r["turn_index"] for r in records_b]

    def test_format_md_emits_markdown_document(self, fake_projects, capsys):
        """--format md produces a markdown document, not a JSON array."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/foo/bar.py")], out=100),
        ])
        args = _audit_routing_samples_args(output_format="md")
        _mod.cmd_audit_routing_samples(args)
        out = capsys.readouterr().out
        assert out.startswith("# audit-routing-samples curation")

    def test_format_md_one_section_per_record(self, fake_projects, capsys):
        """--format md produces exactly one ## section header per sampled record."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/a.py")], out=100),
            _opus([_read_use("r2", "/b.py")], out=100),
            _opus([_read_use("r3", "/c.py")], out=100),
        ])
        args = _audit_routing_samples_args(output_format="md", sample=10)
        _mod.cmd_audit_routing_samples(args)
        out = capsys.readouterr().out
        # Count lines that start with '## ' (section headers, not the h1)
        section_headers = [line for line in out.splitlines() if line.startswith("## ")]
        assert len(section_headers) == 3

    def test_format_md_renders_read_tool_call(self, fake_projects, capsys):
        """--format md renders a Read tool call as **Read:** `<file_path>`."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "src/foo/bar.py")], out=100),
        ])
        args = _audit_routing_samples_args(output_format="md")
        _mod.cmd_audit_routing_samples(args)
        out = capsys.readouterr().out
        assert "**Read:** `src/foo/bar.py`" in out

    def test_format_md_renders_grep_tool_call(self, fake_projects, capsys):
        """--format md renders a Grep tool call as **Grep:** `<pattern>` in `<path>`."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([{"type": "tool_use", "id": "g1", "name": "Grep",
                    "input": {"pattern": "render_widget", "path": "src/"}}], out=100),
        ])
        args = _audit_routing_samples_args(output_format="md")
        _mod.cmd_audit_routing_samples(args)
        out = capsys.readouterr().out
        assert "**Grep:** `render_widget` in `src/`" in out

    def test_format_md_renders_bash_tool_call_truncated(self, fake_projects, capsys):
        """--format md truncates a Bash command longer than 80 chars to exactly 80 chars + '…'."""
        long_command = "a" * 100
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([{"type": "tool_use", "id": "b1", "name": "Bash",
                    "input": {"command": long_command}}], out=100),
        ])
        args = _audit_routing_samples_args(output_format="md")
        _mod.cmd_audit_routing_samples(args)
        out = capsys.readouterr().out
        assert "**Bash:**" in out
        # Extract the backtick-delimited command portion
        match = re.search(r"\*\*Bash:\*\* `([^`]*)`", out)
        assert match is not None
        rendered_command = match.group(1)
        assert rendered_command == "a" * _mod._BASH_COMMAND_DISPLAY_CHARS + "…"

    def test_format_md_fallback_for_unknown_tool(self):
        """_pretty_tool_call renders an unrecognised tool name using the **<Name>:** fallback."""
        # SomeOtherTool is not in _CODE_READ_TOOLS, so a turn using only it would never
        # be classified as code-read and would not reach _pretty_tool_call via the full
        # pipeline.  Testing the helper directly exercises the fallback rendering path.
        rendered = _mod._pretty_tool_call({"name": "SomeOtherTool", "input": {"x": 1}})
        assert "**SomeOtherTool:**" in rendered

    def test_format_md_blockquotes_multiline_user_message(self, fake_projects, capsys):
        """--format md blockquotes each line of a multiline prior user message."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _user_msg([{"type": "text", "text": "line one\nline two"}]),
            _opus([_read_use("r1", "/a.py")], out=100),
        ])
        args = _audit_routing_samples_args(output_format="md")
        _mod.cmd_audit_routing_samples(args)
        out = capsys.readouterr().out
        assert "> line one" in out
        assert "> line two" in out

    def test_format_json_unchanged_by_default(self, fake_projects, capsys):
        """Default output (no --format flag) is a JSON array starting with '['."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/foo/bar.py")], out=100),
        ])
        args = _audit_routing_samples_args()  # output_format defaults to "json"
        _mod.cmd_audit_routing_samples(args)
        out = capsys.readouterr().out
        assert out.strip().startswith("[")

    def test_format_md_renders_glob_tool_call(self, fake_projects, capsys):
        """--format md renders a Glob tool call as **Glob:** `<pattern>` in `<path>`."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([{"type": "tool_use", "id": "gl1", "name": "Glob",
                    "input": {"pattern": "**/*.ts", "path": "src/"}}], out=100),
        ])
        args = _audit_routing_samples_args(output_format="md")
        _mod.cmd_audit_routing_samples(args)
        out = capsys.readouterr().out
        assert "**Glob:** `**/*.ts` in `src/`" in out

    def test_format_md_renders_bash_tool_call_short(self, fake_projects, capsys):
        """--format md renders a short Bash command without truncation or ellipsis."""
        short_command = "git status"
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([{"type": "tool_use", "id": "b2", "name": "Bash",
                    "input": {"command": short_command}}], out=100),
        ])
        args = _audit_routing_samples_args(output_format="md")
        _mod.cmd_audit_routing_samples(args)
        out = capsys.readouterr().out
        assert f"**Bash:** `{short_command}`" in out
        assert "…" not in out

    def test_format_md_blockquotes_blank_lines_in_user_message(self, fake_projects, capsys):
        """--format md preserves blank-line continuity: blank lines render as '>' not bare empty."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _user_msg([{"type": "text", "text": "para one\n\npara two"}]),
            _opus([_read_use("r1", "/a.py")], out=100),
        ])
        args = _audit_routing_samples_args(output_format="md")
        _mod.cmd_audit_routing_samples(args)
        out = capsys.readouterr().out
        lines = out.splitlines()
        # Find the blockquoted user section; the blank line between paragraphs must be ">"
        # (not an empty string), so blockquote continuity is preserved in markdown renderers.
        assert ">" in lines   # at least one plain ">" line exists
        # Verify the blank line between the two paragraphs is ">" not ""
        # by finding the two paragraph lines and checking what's between them
        para1_idx = next(i for i, ln in enumerate(lines) if ln == "> para one")
        para2_idx = next(i for i, ln in enumerate(lines) if ln == "> para two")
        for between_line in lines[para1_idx + 1:para2_idx]:
            assert between_line == ">"

    def test_pretty_tool_call_bash_with_description(self):
        """_pretty_tool_call renders Bash with description as 'description — `cmd`'."""
        result = _mod._pretty_tool_call({
            "name": "Bash",
            "input": {"command": "grep -rn foo bar/", "description": "Find foo"},
        })
        assert result == "**Bash:** Find foo — `grep -rn foo bar/`"

    def test_pretty_tool_call_bash_without_description(self):
        """_pretty_tool_call Bash without description falls back to bare command."""
        result = _mod._pretty_tool_call({
            "name": "Bash",
            "input": {"command": "ls /tmp"},
        })
        assert result == "**Bash:** `ls /tmp`"

    def test_format_md_narration_section_present(self, fake_projects, capsys):
        """--format md includes **Recent agent narration:** when prior text exists."""
        # Two Opus turns: first has text, second is the code-read (sampled).
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([{"type": "text", "text": "Checking dependencies first."},
                   _read_use("r0", "/other.py")], out=100),
            _opus([_read_use("r1", "/foo/bar.py")], out=100),
        ])
        args = _audit_routing_samples_args(output_format="md")
        _mod.cmd_audit_routing_samples(args)
        out = capsys.readouterr().out
        assert "**Recent agent narration:**" in out
        assert "Checking dependencies first." in out

    def test_format_md_narration_section_absent_when_no_prior_text(self, fake_projects, capsys):
        """--format md omits **Recent agent narration:** when there is no prior assistant text."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/foo/bar.py")], out=100),
        ])
        args = _audit_routing_samples_args(output_format="md")
        _mod.cmd_audit_routing_samples(args)
        out = capsys.readouterr().out
        assert "**Recent agent narration:**" not in out

    def test_format_md_tool_trail_section_present(self, fake_projects, capsys):
        """--format md includes **Recent tool trail:** when prior tool calls exist."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r0", "/other.py")], out=100),
            _opus([_read_use("r1", "/foo/bar.py")], out=100),
        ])
        args = _audit_routing_samples_args(output_format="md")
        _mod.cmd_audit_routing_samples(args)
        out = capsys.readouterr().out
        assert "**Recent tool trail:**" in out
        assert "Read: /other.py" in out

    def test_format_md_tool_trail_section_absent_at_session_start(self, fake_projects, capsys):
        """--format md omits **Recent tool trail:** when candidate is the first turn."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/foo/bar.py")], out=100),
        ])
        args = _audit_routing_samples_args(output_format="md")
        _mod.cmd_audit_routing_samples(args)
        out = capsys.readouterr().out
        assert "**Recent tool trail:**" not in out

    def test_format_md_trail_and_narration_cap_at_n(self, fake_projects, capsys):
        """Lookback caps at 3: 10 prior assistant turns yields exactly 3 narration/trail entries."""
        prior_turns = [
            _opus([{"type": "text", "text": f"Step {i}."},
                   _read_use(f"r{i}", f"/f{i}.py")], out=100)
            for i in range(10)
        ]
        candidate = _opus([_read_use("rc", "/candidate.py")], out=100)
        _write_jsonl(fake_projects / "sess.jsonl", prior_turns + [candidate])
        args = _audit_routing_samples_args(output_format="md")
        _mod.cmd_audit_routing_samples(args)
        out = capsys.readouterr().out
        # Isolate only the card for /candidate.py (the last candidate turn)
        sections = out.split("---")
        candidate_section = next((s for s in sections if "/candidate.py" in s), None)
        assert candidate_section is not None, "No section found for /candidate.py"
        # Count narration blockquote lines starting with "> " that contain "Step"
        narration_lines = [ln for ln in candidate_section.splitlines() if ln.startswith("> ") and "Step" in ln]
        assert len(narration_lines) == 3
        # Count trail bullet lines
        trail_lines = [ln for ln in candidate_section.splitlines() if ln.startswith("- Read:") or ln.startswith("- Bash:")]
        assert len(trail_lines) == 3

    def test_format_md_fewer_than_3_priors(self, fake_projects, capsys):
        """Lookback with 1 prior turn yields exactly 1 narration and 1 trail entry."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([{"type": "text", "text": "Only prior."},
                   _read_use("r0", "/only.py")], out=100),
            _opus([_read_use("rc", "/candidate.py")], out=100),
        ])
        args = _audit_routing_samples_args(output_format="md")
        _mod.cmd_audit_routing_samples(args)
        out = capsys.readouterr().out
        narration_lines = [ln for ln in out.splitlines() if ln.startswith("> ") and "Only prior." in ln]
        assert len(narration_lines) == 1
        trail_lines = [ln for ln in out.splitlines() if "Read: /only.py" in ln]
        assert len(trail_lines) == 1

    def test_format_md_tool_only_turns_skipped_by_narration(self, fake_projects, capsys):
        """Narration walker skips turns with no text block; still collects text-bearing turns."""
        # 3 text-bearing turns, then 3 tool-only turns, then candidate.
        # Walking backward from candidate: hit 3 tool-only (skipped), then 3 text-bearing (collected).
        tool_only = [_opus([_read_use(f"r{i}", f"/t{i}.py")], out=100) for i in range(3)]
        text_bearing = [
            _opus([{"type": "text", "text": f"Text {i}."}, _read_use(f"rt{i}", f"/tb{i}.py")], out=100)
            for i in range(3)
        ]
        candidate = _opus([_read_use("rc", "/candidate.py")], out=100)
        _write_jsonl(fake_projects / "sess.jsonl", text_bearing + tool_only + [candidate])
        args = _audit_routing_samples_args(output_format="md")
        _mod.cmd_audit_routing_samples(args)
        out = capsys.readouterr().out
        # Isolate the candidate card and assert narration contains the text-bearing entries
        sections = out.split("---")
        candidate_section = next((s for s in sections if "/candidate.py" in s), None)
        assert candidate_section is not None, "No section found for /candidate.py"
        for i in range(3):
            assert f"Text {i}." in candidate_section

    def test_format_md_newlines_stripped_in_narration(self, fake_projects, capsys):
        """Newlines in narration text are replaced with spaces (no blockquote corruption)."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([{"type": "text", "text": "Line one.\nLine two."},
                   _read_use("r0", "/a.py")], out=100),
            _opus([_read_use("rc", "/candidate.py")], out=100),
        ])
        args = _audit_routing_samples_args(output_format="md")
        _mod.cmd_audit_routing_samples(args)
        out = capsys.readouterr().out
        # Should appear as a single blockquote line with a space, not two separate lines
        assert "> Line one. Line two." in out

    def test_format_md_cross_session_isolation(self, fake_projects, capsys):
        """Candidate in session B does not pick up session A's narration or trail."""
        # Session A: has a text turn with distinctive content.
        (fake_projects / "sess_a.jsonl").write_text(
            "\n".join(
                json.dumps(r) for r in [
                    _opus([{"type": "text", "text": "Session A unique text."},
                           _read_use("ra", "/a.py")], out=100),
                ]
            ) + "\n"
        )
        # Session B: single code-read candidate, no prior turns.
        (fake_projects / "sess_b.jsonl").write_text(
            "\n".join(
                json.dumps(r) for r in [
                    _opus([_read_use("rb", "/b.py")], out=100),
                ]
            ) + "\n"
        )
        args = _audit_routing_samples_args(output_format="md", sample=10)
        _mod.cmd_audit_routing_samples(args)
        out = capsys.readouterr().out
        # Split by section headers and check B's card specifically.
        sections = out.split("---")
        b_section = next((s for s in sections if "/b.py" in s), None)
        if b_section:  # only assert if B was sampled
            assert "Session A unique text." not in b_section

    def test_format_json_record_key_set(self, fake_projects, capsys):
        """JSON output record has exactly the expected key set (no missing, no extra)."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/foo.py")], out=100),
        ])
        args = _audit_routing_samples_args()  # default JSON format
        _mod.cmd_audit_routing_samples(args)
        out = capsys.readouterr().out
        records = json.loads(out)
        assert len(records) == 1
        assert set(records[0].keys()) == {
            "session_id", "turn_index", "prior_user_message",
            "assistant_tool_call", "next_assistant_action", "next_turn_excerpt",
            "recent_assistant_text", "recent_tool_trail",
        }


# ---------------------------------------------------------------------------
# cmd_struggle — new STRUGGLE_PHRASES entries
# ---------------------------------------------------------------------------


class TestCmdStruggle:
    def test_hallucinat_phrase_registers_as_struggle(self, fake_projects, capsys):
        """"I think you hallucinated about this" should register; "stale cache" should not."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            # Assistant turn so branch has a model family to attribute to.
            _asst("claude-sonnet-4-6", branch="feat"),
            # Human turn containing the new phrase — should register as struggle.
            _user_msg(
                [{"type": "text", "text": "I think you hallucinated about this"}],
                branch="feat",
            ),
            # Control: "stale" is excluded from STRUGGLE_PHRASES (legitimate technical term).
            _user_msg(
                [{"type": "text", "text": "the stale cache needs clearing"}],
                branch="feat",
            ),
        ])
        args = type("A", (), {"projects": "*", "branches": "feat"})()
        _mod.cmd_struggle(args)
        out = capsys.readouterr().out

        # The output table should include "feat" with at least one struggle signal.
        assert "feat" in out, "branch not found in output"
        # Parse the data row for "feat": columns are Branch Opus Sonnet Haiku Other Unknown
        data_lines = [ln for ln in out.splitlines() if ln.startswith("feat")]
        assert len(data_lines) == 1, f"expected one data row for 'feat', got: {data_lines}"
        cols = data_lines[0].split()
        # cols[0]=branch, cols[1]=opus, cols[2]=sonnet, cols[3]=haiku, cols[4]=other, cols[5]=unknown
        total_signals = sum(int(c) for c in cols[1:])
        assert total_signals == 1, (
            f"expected exactly 1 struggle signal (from 'hallucinated', not from 'stale cache'); got cols={cols}"
        )

    def test_stale_control_phrase_does_not_inflate_count(self, fake_projects, capsys):
        """Bare "stale" must not match — excluded from STRUGGLE_PHRASES due to false-positive risk."""
        # Confirm "stale" alone is absent from STRUGGLE_PHRASES.
        assert not any(p == "stale" for p in _mod.STRUGGLE_PHRASES), (
            "bare 'stale' was added to STRUGGLE_PHRASES; it is intentionally excluded — "
            "remove it to avoid false positives on technical uses like 'stale cache'"
        )
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="feat"),
            _user_msg(
                [{"type": "text", "text": "the stale cache needs clearing"}],
                branch="feat",
            ),
        ])
        args = type("A", (), {"projects": "*", "branches": "feat"})()
        _mod.cmd_struggle(args)
        out = capsys.readouterr().out
        # "stale cache" alone should produce no struggle signals — branch absent from output.
        assert "feat" not in out, (
            f"'stale cache' should not register as a struggle signal; branch appeared in output: {out!r}"
        )

    @pytest.mark.parametrize(
        "phrase,text",
        [
            ("are you saying", "Are you saying this is correct?"),
            ("you should be able to", "You should be able to run it directly."),
            ("that doesn't exist", "that doesn't exist in the repo"),
            ("that doesn't match", "that doesn't match what I see"),
            ("not what i wanted", "that's not what i wanted at all"),
            ("not what i asked", "that's not what i asked you to do"),
            ("hallucinat", "I think you hallucinated that function"),
        ],
    )
    def test_new_phrases_register_as_struggle(self, phrase, text, fake_projects, capsys):
        """Each newly-added STRUGGLE_PHRASES entry must register when present in a user turn."""
        assert any(phrase in p for p in _mod.STRUGGLE_PHRASES), (
            f"phrase {phrase!r} not found in STRUGGLE_PHRASES — was it removed or mistyped?"
        )
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="feat"),
            _user_msg([{"type": "text", "text": text}], branch="feat"),
        ])
        args = type("A", (), {"projects": "*", "branches": "feat"})()
        _mod.cmd_struggle(args)
        out = capsys.readouterr().out
        assert "feat" in out, (
            f"phrase {phrase!r} (text={text!r}) should register as a struggle signal; branch absent from output: {out!r}"
        )


# ---------------------------------------------------------------------------
# skill-invocation
# ---------------------------------------------------------------------------


class TestSkillInvocation:
    def _run(self, capsys):
        args = argparse.Namespace(projects="*")
        _mod.cmd_skill_invocation(args)
        return capsys.readouterr().out

    def test_top_level_invocation_counted(self, fake_projects, capsys):
        """Assistant record with Skill tool_use and no attributionSkill → counted as top-level."""
        asst_rec = _asst("claude-opus-4-7", branch="main", content=[
            _skill_use("t1", "code-review")
        ])
        # No attributionSkill field → top-level
        _write_jsonl(fake_projects / "s1.jsonl", [asst_rec])
        out = self._run(capsys)
        assert "code-review" in out
        # Top-level column should be ≥1; find the data row for code-review
        for line in out.splitlines():
            if line.startswith("code-review"):
                parts = line.split()
                # Format: skill top-level routed user-slash total
                assert int(parts[1]) >= 1, f"expected top-level ≥1, got: {line!r}"
                assert int(parts[2]) == 0, f"expected routed=0, got: {line!r}"
                assert int(parts[3]) == 0, f"expected slash=0, got: {line!r}"
                break
        else:
            pytest.fail("code-review data row not found in output")

    def test_routed_invocation_counted(self, fake_projects, capsys):
        """Assistant record with Skill tool_use AND attributionSkill → counted as routed."""
        asst_rec = _asst("claude-haiku-4-5", branch="main", content=[
            _skill_use("t2", "code-review")
        ])
        asst_rec["attributionSkill"] = "ready-for-review"
        _write_jsonl(fake_projects / "s2.jsonl", [asst_rec])
        out = self._run(capsys)
        # Routed pair must appear in the ROUTED PAIRS section with count 1
        pair_lines = [ln for ln in out.splitlines() if "ready-for-review -> code-review" in ln]
        assert pair_lines, "ready-for-review -> code-review pair line not found in output"
        assert ": 1" in pair_lines[0], f"expected count=1 in pair line: {pair_lines[0]!r}"

    def test_routed_column_count_correct(self, fake_projects, capsys):
        """Routed invocation increments the routed column and not top-level."""
        asst_rec = _asst("claude-haiku-4-5", branch="main", content=[
            _skill_use("t3", "plan-review")
        ])
        asst_rec["attributionSkill"] = "plan-it"
        _write_jsonl(fake_projects / "s3.jsonl", [asst_rec])
        out = self._run(capsys)
        for line in out.splitlines():
            if line.startswith("plan-review"):
                parts = line.split()
                assert int(parts[1]) == 0, f"expected top-level=0, got: {line!r}"
                assert int(parts[2]) >= 1, f"expected routed≥1, got: {line!r}"
                break
        else:
            pytest.fail("plan-review data row not found in output")

    def test_slash_invocation_counted(self, fake_projects, capsys):
        """User record whose content contains <command-name>/plan-it</command-name> → user-slash count."""
        user_rec = _user_msg(
            "<command-message>plan-it</command-message>\n<command-name>/plan-it</command-name>",
            branch="main",
        )
        _write_jsonl(fake_projects / "s4.jsonl", [user_rec])
        out = self._run(capsys)
        # plan-it must appear in the table
        assert "plan-it" in out
        # The slash column for plan-it should be ≥1
        for line in out.splitlines():
            if line.startswith("plan-it"):
                parts = line.split()
                assert int(parts[3]) >= 1, f"expected slash≥1, got: {line!r}"
                break

    def test_sidechain_excluded(self, fake_projects, capsys):
        """Sidechain assistant records are not counted in any bucket."""
        asst_rec = _asst("claude-opus-4-7", branch="main", sidechain=True, content=[
            _skill_use("t5", "code-review")
        ])
        _write_jsonl(fake_projects / "s5.jsonl", [asst_rec])
        out = self._run(capsys)
        # No non-sidechain invocations → no data → "No skill invocations found."
        assert "No skill invocations found." in out

    def test_routed_pair_grouping(self, fake_projects, capsys):
        """Two records both with attributionSkill='ready-for-review' and skill='code-review'
        → routed_pairs contains (ready-for-review, code-review): 2."""
        rec1 = _asst("claude-haiku-4-5", branch="main", content=[_skill_use("t6a", "code-review")])
        rec1["attributionSkill"] = "ready-for-review"
        rec2 = _asst("claude-haiku-4-5", branch="main", content=[_skill_use("t6b", "code-review")])
        rec2["attributionSkill"] = "ready-for-review"
        _write_jsonl(fake_projects / "s6.jsonl", [rec1, rec2])
        out = self._run(capsys)
        # The pair should appear exactly once in the routed pairs section with count 2
        pair_lines = [ln for ln in out.splitlines() if "ready-for-review -> code-review" in ln]
        assert len(pair_lines) == 1, f"Expected exactly one pair line, got: {pair_lines}"
        assert ": 2" in pair_lines[0], f"Expected count=2 in pair line: {pair_lines[0]!r}"

    def test_routed_only_candidate_in_summary(self, fake_projects, capsys):
        """A skill with only routed invocations appears in the 'Routed-only candidates' section."""
        rec = _asst("claude-haiku-4-5", branch="main", content=[_skill_use("t7", "agent-review")])
        rec["attributionSkill"] = "code-review"
        _write_jsonl(fake_projects / "s7.jsonl", [rec])
        out = self._run(capsys)
        assert "Routed-only candidates" in out, f"section header not found in output: {out!r}"
        assert "agent-review" in out
        # agent-review must appear in the routed-only section, not load-bearing
        routed_only_start = out.find("Routed-only candidates")
        slash_only_start = out.find("Slash-only candidates")
        assert routed_only_start != -1, "Routed-only candidates section not found"
        assert slash_only_start != -1, "Slash-only candidates section not found"
        routed_only_section = out[routed_only_start:slash_only_start]
        assert "agent-review" in routed_only_section

    def test_load_bearing_in_summary(self, fake_projects, capsys):
        """A skill with top-level invocations appears in the 'Load-bearing' section."""
        rec = _asst("claude-sonnet-4-6", branch="main", content=[_skill_use("t8", "skill-review")])
        _write_jsonl(fake_projects / "s8.jsonl", [rec])
        out = self._run(capsys)
        load_bearing_start = out.find("Load-bearing")
        routed_only_start = out.find("Routed-only candidates")
        assert load_bearing_start != -1, "Load-bearing section not found"
        assert routed_only_start != -1, "Routed-only candidates section not found"
        load_bearing_section = out[load_bearing_start:routed_only_start]
        assert "skill-review" in load_bearing_section

    def test_slash_only_candidate_in_summary(self, fake_projects, capsys):
        """A skill with only slash invocations appears in the 'Slash-only candidates' section."""
        user_rec = _user_msg(
            "<command-name>/handoff</command-name>",
            branch="main",
        )
        _write_jsonl(fake_projects / "s9.jsonl", [user_rec])
        out = self._run(capsys)
        slash_only_start = out.find("Slash-only candidates")
        assert slash_only_start != -1, "Slash-only candidates section not found"
        slash_only_section = out[slash_only_start:]
        assert "handoff" in slash_only_section

    def test_no_invocations_prints_not_found(self, fake_projects, capsys):
        """Empty corpus prints 'No skill invocations found.'"""
        # Write a session with only a Bash call — no Skill tool_use or slash
        _write_jsonl(fake_projects / "s_empty.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", content=[
                _bash_use("b1", "git status")
            ])
        ])
        out = self._run(capsys)
        assert "No skill invocations found." in out

    def test_projects_filter_restricts_to_named_project(self, tmp_path, monkeypatch, capsys):
        """--projects filter causes only the named project dir to be scanned."""
        projects = tmp_path / "projects"
        proj_a = projects / "-home-user-repoA"
        proj_b = projects / "-home-user-repoB"
        proj_a.mkdir(parents=True)
        proj_b.mkdir(parents=True)
        monkeypatch.setattr(_mod, "PROJECTS_DIR", projects)
        # repoA has a skill invocation; repoB has a different skill
        rec_a = _asst("claude-sonnet-4-6", branch="main", content=[_skill_use("ta1", "code-review")])
        rec_b = _asst("claude-sonnet-4-6", branch="main", content=[_skill_use("tb1", "plan-review")])
        _write_jsonl(proj_a / "s_a.jsonl", [rec_a])
        _write_jsonl(proj_b / "s_b.jsonl", [rec_b])
        # Filter to repoA only
        args = argparse.Namespace(projects="-home-user-repoA")
        _mod.cmd_skill_invocation(args)
        out = capsys.readouterr().out
        assert "code-review" in out, "repoA skill not found"
        assert "plan-review" not in out, "repoB skill leaked through the project filter"

    def test_empty_skill_field_in_tool_use_skipped(self, fake_projects, capsys):
        """Skill tool_use with empty or absent input.skill is not counted."""
        rec_empty = _asst("claude-sonnet-4-6", branch="main", content=[
            {"type": "tool_use", "id": "te1", "name": "Skill", "input": {"skill": ""}},
        ])
        rec_absent = _asst("claude-sonnet-4-6", branch="main", content=[
            {"type": "tool_use", "id": "te2", "name": "Skill", "input": {}},
        ])
        _write_jsonl(fake_projects / "s_empty_skill.jsonl", [rec_empty, rec_absent])
        out = self._run(capsys)
        assert "No skill invocations found." in out

    def test_multiple_skills_in_one_assistant_record(self, fake_projects, capsys):
        """Two Skill tool_use blocks in a single assistant record's content → both counted."""
        rec = _asst("claude-sonnet-4-6", branch="main", content=[
            _skill_use("tm1", "code-review"),
            _skill_use("tm2", "plan-review"),
        ])
        _write_jsonl(fake_projects / "s_multi_skill.jsonl", [rec])
        out = self._run(capsys)
        found_code_review = found_plan_review = False
        for line in out.splitlines():
            if line.startswith("code-review"):
                found_code_review = True
                assert int(line.split()[1]) == 1, f"code-review top-level count should be 1: {line!r}"
            if line.startswith("plan-review"):
                found_plan_review = True
                assert int(line.split()[1]) == 1, f"plan-review top-level count should be 1: {line!r}"
        assert found_code_review, "code-review data row not found in output"
        assert found_plan_review, "plan-review data row not found in output"

    def test_multiple_slash_tags_in_one_user_record(self, fake_projects, capsys):
        """Two <command-name> tags in a single user record → both skill names counted in slash column."""
        user_rec = _user_msg(
            "<command-name>/plan-it</command-name>\n<command-name>/code-review</command-name>",
            branch="main",
        )
        _write_jsonl(fake_projects / "s_multi_slash.jsonl", [user_rec])
        out = self._run(capsys)
        found_plan_it = found_code_review = False
        for line in out.splitlines():
            if line.startswith("plan-it"):
                found_plan_it = True
                assert int(line.split()[3]) >= 1, f"plan-it slash count should be ≥1: {line!r}"
            if line.startswith("code-review"):
                found_code_review = True
                assert int(line.split()[3]) >= 1, f"code-review slash count should be ≥1: {line!r}"
        assert found_plan_it, "plan-it data row not found in output"
        assert found_code_review, "code-review data row not found in output"

    def test_slash_detection_with_list_form_user_content(self, fake_projects, capsys):
        """User record whose content is a list-of-blocks → slash command still detected via _content_text."""
        user_rec = {
            "type": "user",
            "gitBranch": "main",
            "message": {
                "content": [
                    {"type": "text", "text": "<command-name>/plan-it</command-name>"},
                ]
            },
        }
        _write_jsonl(fake_projects / "s_list_content.jsonl", [user_rec])
        out = self._run(capsys)
        assert "plan-it" in out
        for line in out.splitlines():
            if line.startswith("plan-it"):
                assert int(line.split()[3]) >= 1, f"plan-it slash count should be ≥1: {line!r}"
                break

    def test_mixed_top_and_routed_in_same_session(self, fake_projects, capsys):
        """A session with both top-level and routed calls for the same skill tallies both columns."""
        top_rec = _asst("claude-sonnet-4-6", branch="main", content=[_skill_use("t10a", "code-review")])
        # No attributionSkill → top-level
        routed_rec = _asst("claude-haiku-4-5", branch="main", content=[_skill_use("t10b", "code-review")])
        routed_rec["attributionSkill"] = "ready-for-review"
        _write_jsonl(fake_projects / "s10.jsonl", [top_rec, routed_rec])
        out = self._run(capsys)
        for line in out.splitlines():
            if line.startswith("code-review"):
                parts = line.split()
                assert int(parts[1]) == 1, f"expected top-level=1, got: {line!r}"
                assert int(parts[2]) == 1, f"expected routed=1, got: {line!r}"
                assert int(parts[4]) == 2, f"expected total=2, got: {line!r}"
                break
        else:
            pytest.fail("code-review data row not found in output")

    def test_sidechain_user_records_excluded_from_slash(self, fake_projects, capsys):
        """User records with isSidechain=True are not scanned for slash invocations."""
        sidechain_user = {
            "type": "user",
            "isSidechain": True,
            "gitBranch": "main",
            "message": {"content": "<command-name>/code-review</command-name>"},
        }
        _write_jsonl(fake_projects / "s_sidechain_user.jsonl", [sidechain_user])
        out = self._run(capsys)
        assert "No skill invocations found." in out

    def test_sidechain_user_excluded_main_thread_user_counted(self, fake_projects, capsys):
        """Mixed session: sidechain user slash is excluded, main-thread user slash is counted."""
        sidechain_user = {
            "type": "user",
            "isSidechain": True,
            "gitBranch": "main",
            "message": {"content": "<command-name>/plan-review</command-name>"},
        }
        main_user = _user_msg("<command-name>/code-review</command-name>", branch="main")
        _write_jsonl(fake_projects / "s_sidechain_mixed.jsonl", [sidechain_user, main_user])
        out = self._run(capsys)
        assert "code-review" in out, "main-thread slash should be counted"
        assert "plan-review" not in out, "sidechain slash should be excluded"
        for line in out.splitlines():
            if line.startswith("code-review"):
                assert int(line.split()[3]) == 1, f"expected slash=1, got: {line!r}"
                break
        else:
            pytest.fail("code-review data row not found in output")

    def test_multiple_skill_blocks_on_routed_record(self, fake_projects, capsys):
        """An attributed record with two Skill tool_use blocks counts both as routed, not top-level."""
        rec = _asst("claude-haiku-4-5", branch="main", content=[
            _skill_use("tr1", "code-review"),
            _skill_use("tr2", "plan-review"),
        ])
        rec["attributionSkill"] = "ready-for-review"
        _write_jsonl(fake_projects / "s_multi_routed.jsonl", [rec])
        out = self._run(capsys)
        found_code_review = found_plan_review = False
        for line in out.splitlines():
            if line.startswith("code-review"):
                found_code_review = True
                parts = line.split()
                assert int(parts[1]) == 0, f"code-review top-level should be 0: {line!r}"
                assert int(parts[2]) == 1, f"code-review routed should be 1: {line!r}"
            if line.startswith("plan-review"):
                found_plan_review = True
                parts = line.split()
                assert int(parts[1]) == 0, f"plan-review top-level should be 0: {line!r}"
                assert int(parts[2]) == 1, f"plan-review routed should be 1: {line!r}"
        assert found_code_review, "code-review data row not found in output"
        assert found_plan_review, "plan-review data row not found in output"

    def test_cross_project_aggregation(self, tmp_path, monkeypatch, capsys):
        """Default glob aggregates counts across multiple project directories."""
        projects = tmp_path / "projects"
        proj_a = projects / "-home-user-repoA"
        proj_b = projects / "-home-user-repoB"
        proj_a.mkdir(parents=True)
        proj_b.mkdir(parents=True)
        monkeypatch.setattr(_mod, "PROJECTS_DIR", projects)
        rec_a = _asst("claude-sonnet-4-6", branch="main", content=[_skill_use("tc1", "code-review")])
        rec_b = _asst("claude-sonnet-4-6", branch="main", content=[_skill_use("tc2", "code-review")])
        _write_jsonl(proj_a / "s_a.jsonl", [rec_a])
        _write_jsonl(proj_b / "s_b.jsonl", [rec_b])
        args = argparse.Namespace(projects="*")
        _mod.cmd_skill_invocation(args)
        out = capsys.readouterr().out
        for line in out.splitlines():
            if line.startswith("code-review"):
                assert int(line.split()[1]) == 2, f"expected 2 top-level from both projects: {line!r}"
                break
        else:
            pytest.fail("code-review data row not found in output")


# ---------------------------------------------------------------------------
# iter_sessions subagent merge
# ---------------------------------------------------------------------------


class TestIterSessionsSubagentMerge:
    def test_include_subagents_false_ignores_subdir(self, fake_projects):
        """include_subagents=False must not read the subagents/ subdirectory."""
        session_id = "sess-abc"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="main"),
        ])
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [
            _asst("claude-sonnet-4-6", branch="main", sidechain=True),
        ])
        results = list(_mod.iter_sessions(fake_projects.parent, include_subagents=False))
        assert len(results) == 1
        _path, records = results[0]
        # Only the main record; sidechain record must not appear.
        assert len(records) == 1
        assert records[0].get("isSidechain") is False

    def test_include_subagents_true_merges_subagent_records(self, fake_projects):
        """include_subagents=True appends subagent file records to the session's record list."""
        session_id = "sess-def"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="main"),
        ])
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [
            _asst("claude-sonnet-4-6", branch="main", sidechain=True),
        ])
        results = list(_mod.iter_sessions(fake_projects.parent, include_subagents=True))
        assert len(results) == 1
        _path, records = results[0]
        assert len(records) == 2
        types = [r.get("isSidechain") for r in records]
        assert False in types
        assert True in types

    def test_missing_subagent_dir_is_safe(self, fake_projects):
        """A session with no subagents/ directory yields normally with include_subagents=True."""
        _write_jsonl(fake_projects / "lone-session.jsonl", [
            _asst("claude-opus-4-7", branch="main"),
        ])
        # No subagents/ dir for this session.
        results = list(_mod.iter_sessions(fake_projects.parent, include_subagents=True))
        assert len(results) == 1
        _path, records = results[0]
        assert len(records) == 1

    def test_multiple_subagent_files_all_merged(self, fake_projects):
        """All *.jsonl files in the subagents/ dir are merged into one record list."""
        session_id = "sess-multi"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="main"),
        ])
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [
            _asst("claude-sonnet-4-6", branch="main", sidechain=True),
        ])
        _write_subagent_jsonl(fake_projects, session_id, "agent-2", [
            _asst("claude-haiku-4-5", branch="main", sidechain=True),
        ])
        results = list(_mod.iter_sessions(fake_projects.parent, include_subagents=True))
        assert len(results) == 1
        _path, records = results[0]
        assert len(records) == 3, "expected main + 2 subagent records"
        models = [(r.get("message") or {}).get("model") for r in records]
        assert "claude-opus-4-7" in models
        assert "claude-sonnet-4-6" in models
        assert "claude-haiku-4-5" in models

    def test_corrupt_line_in_subagent_file_skipped_gracefully(self, fake_projects):
        """A corrupt JSON line in a subagent file is skipped; valid records still merge."""
        session_id = "sess-corrupt"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="main"),
        ])
        # Write a subagent file with one valid record and one corrupt line.
        subdir = fake_projects / session_id / _mod.SUBAGENT_SUBDIR
        subdir.mkdir(parents=True, exist_ok=True)
        (subdir / "agent-corrupt.jsonl").write_text(
            '{"type":"assistant","isSidechain":true,"gitBranch":"main",'
            '"message":{"model":"claude-sonnet-4-6","content":[],"usage":{}}}\n'
            'THIS IS NOT JSON\n'
        )
        results = list(_mod.iter_sessions(fake_projects.parent, include_subagents=True))
        assert len(results) == 1
        _path, records = results[0]
        sidechain = [r for r in records if r.get("isSidechain")]
        assert len(sidechain) == 1, "corrupt line skipped; valid sidechain record present"


# ---------------------------------------------------------------------------
# subagents (cmd_subagents)
# ---------------------------------------------------------------------------


def _subagents_args(*, projects: str = "*", branches: str | None = None) -> object:
    return type("A", (), {"projects": projects, "branches": branches})()


class TestSubagents:
    def test_split_subagent_file_populates_sidechain_row(self, fake_projects, capsys):
        """Sidechain records from a split subagent file appear in the sidechain row."""
        session_id = "sess-split"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="test-branch"),
        ])
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [
            _asst("claude-sonnet-4-6", branch="test-branch", sidechain=True),
        ])
        _mod.cmd_subagents(_subagents_args(branches="test-branch"))
        out = capsys.readouterr().out
        main_lines = [ln for ln in out.splitlines() if "main" in ln]
        sidechain_lines = [ln for ln in out.splitlines() if "sidechain" in ln]
        assert main_lines, "expected a main row in output"
        assert sidechain_lines, "expected a sidechain row in output"
        # Verify actual counts: 1 opus main turn, 1 sonnet sidechain turn.
        # main row format: branch thread opus sonnet haiku other
        main_cols = main_lines[0].split()
        assert main_cols[2] == "1", "expected 1 opus main turn"
        assert main_cols[3] == "0", "expected 0 sonnet main turns"
        # sidechain row: branch label absent on second row → thread opus sonnet haiku other
        sidechain_cols = sidechain_lines[0].split()
        assert sidechain_cols[1] == "0", "expected 0 opus sidechain turns"
        assert sidechain_cols[2] == "1", "expected 1 sonnet sidechain turn"

    def test_branch_filter_still_applies_to_output(self, fake_projects, capsys):
        """Branch filter limits the output rows even with split subagent files."""
        session_id = "sess-two-branches"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="branch-a"),
            _asst("claude-opus-4-7", branch="branch-b"),
        ])
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [
            _asst("claude-sonnet-4-6", branch="branch-a", sidechain=True),
        ])
        _mod.cmd_subagents(_subagents_args(branches="branch-a"))
        out = capsys.readouterr().out
        assert "branch-a" in out
        assert "branch-b" not in out


# ---------------------------------------------------------------------------
# skill-pair: subagent file support
# ---------------------------------------------------------------------------


class TestSkillPairSubagentFile:
    def test_follower_in_subagent_file_increments_sidechain_only(self, fake_projects, capsys):
        """Follower skill in a split subagent file (isSidechain=True) counts as Side, not Main."""
        session_id = "sess-pair"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts=_TS_FIXED, content=[
                _skill_use("s1", "plan-it"),
            ]),
        ])
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [
            _asst("claude-sonnet-4-6", branch="main", ts=_TS_FIXED, sidechain=True, content=[
                _skill_use("s2", "plan-review"),
            ]),
        ])
        _mod.cmd_skill_pair(_skill_pair_args())
        out = capsys.readouterr().out
        lines = [ln for ln in out.splitlines() if "2026-W20" in ln]
        assert len(lines) == 1
        cols = lines[0].split()
        assert cols[1] == "1"   # Lead=1
        assert cols[2] == "0"   # Main=0
        assert cols[3] == "1"   # Side=1


# ---------------------------------------------------------------------------
# format-drift canary
# ---------------------------------------------------------------------------


class TestFormatDriftCanary:
    def test_drift_warning_emitted_when_spawns_but_no_sidechain_turns(
        self, fake_projects, capsys
    ):
        """Spawn tool_use with no subagent files → WARNING on stderr."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[
                _agent_use("a1", "staff-backend-engineer"),
            ]),
        ])
        _mod.cmd_subagents(_subagents_args())
        captured = capsys.readouterr()
        assert "WARNING" in captured.err
        assert "isSidechain" in captured.err
        # The warning must not bleed into stdout table rows.
        assert "WARNING" not in captured.out

    def test_no_warning_when_no_spawns(self, fake_projects, capsys):
        """No Agent/Task tool_uses → no drift warning."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main"),
        ])
        _mod.cmd_subagents(_subagents_args())
        assert "WARNING" not in capsys.readouterr().err

    def test_no_warning_old_inlined_format(self, fake_projects, capsys):
        """Agent spawn + isSidechain records inlined in the top-level file → no warning."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[
                _agent_use("a1", "staff-backend-engineer"),
            ]),
            _asst("claude-sonnet-4-6", branch="main", sidechain=True),
        ])
        _mod.cmd_subagents(_subagents_args())
        assert "WARNING" not in capsys.readouterr().err

    def test_drift_warning_also_fires_in_skill_pair(self, fake_projects, capsys):
        """cmd_skill_pair also emits the drift warning when spawns have no sidechain turns."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts=_TS_FIXED, content=[
                _skill_use("s1", "plan-it"),
                _agent_use("a1", "staff-backend-engineer"),
            ]),
        ])
        _mod.cmd_skill_pair(_skill_pair_args())
        assert "WARNING" in capsys.readouterr().err

    def test_subagent_records_without_issidechain_field_treated_as_main_thread(
        self, fake_projects, capsys
    ):
        """Subagent records missing the isSidechain field are counted as main-thread.

        This documents the failure mode if the transcript format drifts to drop
        isSidechain — subagent turns silently appear in the main row, not sidechain.
        """
        session_id = "sess-no-sidechain-field"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[
                _agent_use("a1", "check-runner"),
            ]),
        ])
        # Subagent file record WITHOUT isSidechain field.
        subdir = fake_projects / session_id / _mod.SUBAGENT_SUBDIR
        subdir.mkdir(parents=True, exist_ok=True)
        rec = _asst("claude-sonnet-4-6", branch="main", sidechain=True)
        del rec["isSidechain"]  # simulate format drift
        (subdir / "agent-drifted.jsonl").write_text(json.dumps(rec) + "\n")
        _mod.cmd_subagents(_subagents_args())
        captured = capsys.readouterr()
        sidechain_lines = [ln for ln in captured.out.splitlines() if "sidechain" in ln]
        # Without isSidechain=True, the subagent record is not a sidechain turn.
        assert not sidechain_lines, (
            "subagent records without isSidechain field must not appear in sidechain row"
        )
        # The drift canary fires: spawns=1 (main-thread Agent), sidechain_turns=0
        # (subagent record has no isSidechain field → not counted as sidechain).
        assert "WARNING" in captured.err


# ---------------------------------------------------------------------------
# subagent format contract
# ---------------------------------------------------------------------------


class TestSubagentFormatContract:
    """Pin the on-disk subagent transcript format the reader depends on.

    These assertions describe what the code EXPECTS from the JSONL on disk.
    If any of these fail on a real corpus run (not in CI), the transcript format
    has drifted — update the reader (and iter_sessions) before trusting metric output.
    """

    def test_subagent_dir_path_convention(self, fake_projects):
        """Subagent files live at <session_id>/subagents/*.jsonl relative to project dir."""
        session_id = "abc123"
        agent_id = "agent-xyz"
        _write_subagent_jsonl(
            fake_projects, session_id, agent_id,
            [_asst("claude-sonnet-4-6", sidechain=True)]
        )
        subdir = fake_projects / session_id / _mod.SUBAGENT_SUBDIR
        assert subdir.is_dir(), f"Expected subagent dir at {subdir}"
        files = list(subdir.glob("*.jsonl"))
        assert len(files) == 1

    def test_subagent_assistant_record_carries_required_fields(self, fake_projects):
        """Assistant-type subagent records carry isSidechain, gitBranch, type, message.model
        when read back through iter_sessions with include_subagents=True."""
        session_id = "contract-sess"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="feat"),
        ])
        _write_subagent_jsonl(
            fake_projects, session_id, "agent-contract",
            [_asst("claude-sonnet-4-6", branch="feat", sidechain=True)]
        )
        results = list(_mod.iter_sessions(fake_projects.parent, include_subagents=True))
        assert len(results) == 1
        _path, records = results[0]
        sidechain_records = [r for r in records if r.get("isSidechain")]
        assert len(sidechain_records) == 1, "expected one sidechain record from subagent file"
        rec = sidechain_records[0]
        assert rec.get("isSidechain") is True
        assert rec.get("gitBranch") == "feat"
        assert rec.get("type") == "assistant"
        assert (rec.get("message") or {}).get("model") == "claude-sonnet-4-6"

    def test_user_msg_helper_has_no_message_model(self):
        """User-type records from _user_msg() have no message.model.

        This documents the helper's shape, not an iter_sessions invariant — user
        records in subagent files are not relied on by the script's analysis paths.
        """
        rec = _user_msg("some content", branch="feat")
        # isSidechain may be absent on user records; message.model must not be asserted on them
        assert rec.get("type") == "user"
        assert (rec.get("message") or {}).get("model") is None


# ---------------------------------------------------------------------------
# judgment-pair
# ---------------------------------------------------------------------------


def _judgment_pair_args(
    *,
    projects: str = "*",
    branches: str | None = None,
    since: str | None = None,
    until: str | None = None,
    skills: str | None = None,
    truncate_chars: int = 1000,
    out: str | None = None,
) -> object:
    return type("A", (), {
        "projects": projects,
        "branches": branches,
        "since": since,
        "until": until,
        "skills": skills or ",".join(_mod.REVIEW_SKILLS),
        "truncate_chars": truncate_chars,
        "out": out,
    })()


def _skill_use_rec(skill: str, ts: str, branch: str = "main", tool_id: str = "s1") -> dict:
    """Main-thread assistant record containing a Skill tool_use.

    Pass distinct tool_id values when two invocations appear in the same session
    to match production transcript invariants (tool_use ids are unique per block).
    """
    return _asst("claude-sonnet-4-6", branch=branch, ts=ts, content=[_skill_use(tool_id, skill)])


def _review_asst(text: str, ts: str, branch: str = "main") -> dict:
    """Main-thread assistant record with plain text content."""
    return _asst("claude-sonnet-4-6", branch=branch, ts=ts, content=[{"type": "text", "text": text}])


def _user_reply(text: str, branch: str = "main") -> dict:
    """Plain user message (fresh user prompt)."""
    return _user_msg(text, branch=branch)


class TestIsFreshUserPrompt:
    def test_plain_text_user_record_returns_true(self):
        rec = _user_msg("Please fix that", branch="main")
        assert _mod._is_fresh_user_prompt(rec) is True

    def test_tool_result_bearing_record_returns_false(self):
        rec = _user_msg([_tool_result("t1", "some result")], branch="main")
        assert _mod._is_fresh_user_prompt(rec) is False

    def test_ismeta_true_returns_false(self):
        rec = {
            "type": "user",
            "isMeta": True,
            "message": {"content": "injected meta"},
            "gitBranch": "main",
        }
        assert _mod._is_fresh_user_prompt(rec) is False

    def test_issidechain_true_returns_false(self):
        rec = {
            "type": "user",
            "isSidechain": True,
            "message": {"content": "sidechain msg"},
            "gitBranch": "main",
        }
        assert _mod._is_fresh_user_prompt(rec) is False

    def test_iscompactsummary_true_returns_false(self):
        rec = {
            "type": "user",
            "isCompactSummary": True,
            "message": {"content": "summary"},
            "gitBranch": "main",
        }
        assert _mod._is_fresh_user_prompt(rec) is False

    def test_empty_text_content_returns_false(self):
        rec = _user_msg("", branch="main")
        assert _mod._is_fresh_user_prompt(rec) is False

    def test_assistant_record_returns_false(self):
        rec = _asst("claude-sonnet-4-6", branch="main")
        assert _mod._is_fresh_user_prompt(rec) is False


class TestJudgmentPair:
    def test_single_turn_review_emits_pair(self, fake_projects, capsys):
        """Invocation record followed by one review text turn and a user reply emits one pair."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("code-review", "2026-05-20T10:00:00.000Z"),
            _review_asst("Found 3 issues with the auth middleware.", "2026-05-20T10:01:00.000Z"),
            _user_reply("Thanks, I'll fix those auth issues."),
        ])
        _mod.cmd_judgment_pair(_judgment_pair_args())
        out = capsys.readouterr().out
        assert "Found 3 issues with the auth middleware." in out
        assert "Thanks, I'll fix those auth issues." in out

    def test_multi_turn_review_captures_last_text_turn(self, fake_projects, capsys):
        """Among multiple assistant turns in the window, the last with non-empty text is used."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("code-review", "2026-05-20T10:00:00.000Z"),
            _review_asst("Preamble: reviewing now.", "2026-05-20T10:00:30.000Z"),
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-20T10:00:45.000Z",
                  content=[_bash_use("b1", "ls")]),  # tool-only, no text
            _review_asst("Synthesis: two real issues found in cache invalidation.", "2026-05-20T10:01:00.000Z"),
            _user_reply("Got it, will fix cache invalidation."),
        ])
        _mod.cmd_judgment_pair(_judgment_pair_args())
        out = capsys.readouterr().out
        assert "Synthesis: two real issues found in cache invalidation." in out
        assert "Preamble: reviewing now." not in out

    def test_skill_outside_set_excluded(self, fake_projects, capsys):
        """Invocation of a skill not in the default set produces no pair."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-20T10:00:00.000Z",
                  content=[_skill_use("s1", "handoff")]),
            _review_asst("Handoff summary.", "2026-05-20T10:01:00.000Z"),
            _user_reply("OK."),
        ])
        _mod.cmd_judgment_pair(_judgment_pair_args())
        out = capsys.readouterr().out
        assert "No judgment pairs found." in out

    def test_skills_override_filters_correctly(self, fake_projects, capsys):
        """--skills plan-review causes only the plan-review pair to be emitted."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("plan-review", "2026-05-20T10:00:00.000Z", tool_id="s1"),
            _review_asst("Plan looks good but step 3 is ambiguous.", "2026-05-20T10:01:00.000Z"),
            _user_reply("I'll clarify step 3."),
            _skill_use_rec("code-review", "2026-05-20T10:10:00.000Z", tool_id="s2"),
            _review_asst("Code has an off-by-one in the loop.", "2026-05-20T10:11:00.000Z"),
            _user_reply("Fixing the loop now."),
        ])
        _mod.cmd_judgment_pair(_judgment_pair_args(skills="plan-review"))
        out = capsys.readouterr().out
        assert "Plan looks good but step 3 is ambiguous." in out
        assert "Code has an off-by-one in the loop." not in out
        assert "I'll clarify step 3." in out

    def test_sidechain_invocation_excluded(self, fake_projects, capsys):
        """Skill tool_use on a sidechain assistant record is not detected as an invocation."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-20T10:00:00.000Z",
                  sidechain=True, content=[_skill_use("s1", "code-review")]),
            _review_asst("Some review text.", "2026-05-20T10:01:00.000Z"),
            _user_reply("Thanks."),
        ])
        _mod.cmd_judgment_pair(_judgment_pair_args())
        out = capsys.readouterr().out
        assert "No judgment pairs found." in out

    def test_tool_result_user_not_taken_as_response(self, fake_projects, capsys):
        """A user record bearing tool_result blocks is skipped; the next real user prompt is used."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("code-review", "2026-05-20T10:00:00.000Z"),
            _review_asst("Missing null check in parser.", "2026-05-20T10:01:00.000Z"),
            _user_msg([_tool_result("t1", "tool output text")]),  # tool result — not a fresh prompt
            _user_reply("I'll add the null check."),
        ])
        _mod.cmd_judgment_pair(_judgment_pair_args())
        out = capsys.readouterr().out
        assert "I'll add the null check." in out
        assert "tool output text" not in out

    def test_ismeta_user_not_taken_as_response(self, fake_projects, capsys):
        """A user record with isMeta=True is skipped; the next real user turn is used."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("code-review", "2026-05-20T10:00:00.000Z"),
            _review_asst("Security issue in token validation.", "2026-05-20T10:01:00.000Z"),
            {"type": "user", "isMeta": True,
             "message": {"content": "meta injection"}, "gitBranch": "main"},
            _user_reply("I'll harden the token validation."),
        ])
        _mod.cmd_judgment_pair(_judgment_pair_args())
        out = capsys.readouterr().out
        assert "I'll harden the token validation." in out
        assert "meta injection" not in out

    def test_iscompactsummary_user_not_taken_as_response(self, fake_projects, capsys):
        """A user record with isCompactSummary=True is skipped; the next real user turn is used."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("code-review", "2026-05-20T10:00:00.000Z"),
            _review_asst("Duplicate logic in helper.", "2026-05-20T10:01:00.000Z"),
            {"type": "user", "isCompactSummary": True,
             "message": {"content": "compaction summary text"}, "gitBranch": "main"},
            _user_reply("Extracting the duplicate now."),
        ])
        _mod.cmd_judgment_pair(_judgment_pair_args())
        out = capsys.readouterr().out
        assert "Extracting the duplicate now." in out
        assert "compaction summary text" not in out

    def test_since_filter_excludes_old_invocation(self, fake_projects, capsys):
        """Invocation with timestamp before --since is excluded."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("code-review", "2026-04-01T10:00:00.000Z"),
            _review_asst("Old review output.", "2026-04-01T10:01:00.000Z"),
            _user_reply("Response to old review."),
        ])
        _mod.cmd_judgment_pair(_judgment_pair_args(since="2026-05-01"))
        out = capsys.readouterr().out
        assert "No judgment pairs found." in out

    def test_until_filter_excludes_future_invocation(self, fake_projects, capsys):
        """Invocation with timestamp after --until is excluded."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("code-review", "2026-06-20T10:00:00.000Z"),
            _review_asst("Future review output.", "2026-06-20T10:01:00.000Z"),
            _user_reply("Response to future review."),
        ])
        _mod.cmd_judgment_pair(_judgment_pair_args(until="2026-06-01"))
        out = capsys.readouterr().out
        assert "No judgment pairs found." in out

    def test_missing_timestamp_no_filter_emits_sentinel_date(self, fake_projects, capsys):
        """Invocation with no timestamp field and no date filter emits pair with '?' date label."""
        invocation_no_ts = {
            "type": "assistant",
            "message": {"content": [_skill_use("sv1", "code-review")]},
            "gitBranch": "main",
            # deliberately no "timestamp" key
        }
        _write_jsonl(fake_projects / "sess.jsonl", [
            invocation_no_ts,
            _review_asst("Review emitted despite missing timestamp.", "2026-05-20T10:01:00.000Z"),
            _user_reply("Understood."),
        ])
        _mod.cmd_judgment_pair(_judgment_pair_args())
        out = capsys.readouterr().out
        assert "Review emitted despite missing timestamp." in out
        assert "Understood." in out
        assert "· ?" in out  # sentinel date label from the missing-timestamp guard

    def test_no_user_response_emits_sentinel(self, fake_projects, capsys):
        """When no user turn follows the review window, the sentinel message is shown."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("code-review", "2026-05-20T10:00:00.000Z"),
            _review_asst("Looks good overall.", "2026-05-20T10:01:00.000Z"),
        ])
        _mod.cmd_judgment_pair(_judgment_pair_args())
        out = capsys.readouterr().out
        assert "(no user response — end of session)" in out

    def test_truncate_chars_truncates_review_output(self, fake_projects, capsys):
        """Review text longer than --truncate-chars is cut and suffixed with ellipsis."""
        long_review = "X" * 50
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("code-review", "2026-05-20T10:00:00.000Z"),
            _review_asst(long_review, "2026-05-20T10:01:00.000Z"),
            _user_reply("Got it."),
        ])
        _mod.cmd_judgment_pair(_judgment_pair_args(truncate_chars=10))
        out = capsys.readouterr().out
        assert "XXXXXXXXXX…" in out
        assert "X" * 11 not in out

    def test_branches_filter(self, fake_projects, capsys):
        """--branches restricts output to sessions on the matching branch."""
        _write_jsonl(fake_projects / "sess-main.jsonl", [
            _skill_use_rec("code-review", "2026-05-20T10:00:00.000Z", branch="main"),
            _review_asst("Main branch review.", "2026-05-20T10:01:00.000Z", branch="main"),
            _user_reply("Main branch reply.", branch="main"),
        ])
        _write_jsonl(fake_projects / "sess-feat.jsonl", [
            _skill_use_rec("code-review", "2026-05-20T10:00:00.000Z", branch="feat"),
            _review_asst("Feature branch review.", "2026-05-20T10:01:00.000Z", branch="feat"),
            _user_reply("Feature branch reply.", branch="feat"),
        ])
        _mod.cmd_judgment_pair(_judgment_pair_args(branches="feat"))
        out = capsys.readouterr().out
        assert "Feature branch review." in out
        assert "Main branch review." not in out

    def test_out_path_writes_to_file(self, fake_projects, capsys, tmp_path):
        """--out writes the pair block to the specified file instead of stdout."""
        out_file = tmp_path / "output.txt"
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("code-review", "2026-05-20T10:00:00.000Z"),
            _review_asst("Logic error in retry loop.", "2026-05-20T10:01:00.000Z"),
            _user_reply("Will fix the retry logic."),
        ])
        _mod.cmd_judgment_pair(_judgment_pair_args(out=str(out_file)))
        out_stdout = capsys.readouterr().out
        assert out_stdout == ""  # nothing printed to stdout
        file_content = out_file.read_text()
        assert "Logic error in retry loop." in file_content
        assert "Will fix the retry logic." in file_content

    def test_no_pairs_found_prints_message(self, fake_projects, capsys):
        """When no matching sessions exist, 'No judgment pairs found.' is printed."""
        _mod.cmd_judgment_pair(_judgment_pair_args())
        out = capsys.readouterr().out
        assert "No judgment pairs found." in out

    def test_out_path_with_no_pairs_prints_to_stdout(self, fake_projects, capsys, tmp_path):
        """--out set but no pairs found: sentinel goes to stdout, no file is created."""
        out_file = tmp_path / "output.txt"
        _mod.cmd_judgment_pair(_judgment_pair_args(out=str(out_file)))
        out_stdout = capsys.readouterr().out
        assert "No judgment pairs found." in out_stdout
        assert not out_file.exists()

    def test_back_to_back_invocations_get_distinct_outputs(self, fake_projects, capsys):
        """Two consecutive skill invocations each get their own distinct review output block."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("code-review", "2026-05-20T10:00:00.000Z", tool_id="s1"),
            _review_asst("First review: missing validation.", "2026-05-20T10:01:00.000Z"),
            _skill_use_rec("plan-review", "2026-05-20T10:02:00.000Z", tool_id="s2"),
            _review_asst("Second review: plan is incomplete.", "2026-05-20T10:03:00.000Z"),
            _user_reply("Thanks for both reviews."),
        ])
        _mod.cmd_judgment_pair(_judgment_pair_args())
        out = capsys.readouterr().out
        # Both review texts are present.
        assert "First review: missing validation." in out
        assert "Second review: plan is incomplete." in out
        # The first invocation's window closes at the second invocation, so its user
        # response is the sentinel (no user prompt between the two skill calls).
        assert "(no user response — end of session)" in out
        # The second invocation's window closes at the user reply.
        assert "Thanks for both reviews." in out
        # Sanity: two block headers (structural, supplements content checks above).
        assert out.count("--- REVIEW OUTPUT") == 2

    def test_back_to_back_invocations_with_no_text_between(self, fake_projects, capsys):
        """Two skill invocations with no assistant turns between: first emits '(no review text found)'."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("code-review", "2026-05-20T10:00:00.000Z", tool_id="s1"),
            _skill_use_rec("plan-review", "2026-05-20T10:01:00.000Z", tool_id="s2"),
            _review_asst("Plan is missing rollback steps.", "2026-05-20T10:02:00.000Z"),
            _user_reply("Adding rollback steps now."),
        ])
        _mod.cmd_judgment_pair(_judgment_pair_args())
        out = capsys.readouterr().out
        # First invocation window is empty (no assistant turns before the next invocation).
        assert "(no review text found)" in out
        # Second invocation window contains the review text.
        assert "Plan is missing rollback steps." in out
