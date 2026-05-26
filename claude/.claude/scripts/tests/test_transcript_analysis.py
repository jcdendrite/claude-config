"""Tests for transcript-analysis.py."""
import argparse
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
