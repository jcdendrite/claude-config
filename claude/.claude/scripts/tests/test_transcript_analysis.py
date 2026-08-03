"""Tests for transcript-analysis.py."""
import argparse
import importlib.util
import json
import re
import subprocess
import time
from datetime import date
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


def _table_cols(out: str, *, header_contains: str, row_contains: str,
                drop_leading_labels: int = 0,
                max_labels: int | None = None,
                row_startswith: bool = False) -> dict[str, str]:
    """Map column-label -> cell value for the data row matching `row_contains`.

    Anchors column positions to the header row (the line containing
    `header_contains`) instead of hard-coding indices, so a column reorder in
    the source output fails meaningfully rather than silently reading the wrong
    column.

    Precondition: every asserted column's header label AND cell value is a
    single whitespace token (true for all leading label/count columns; trailing
    free-text columns like "Top subagent types" are not assertable this way and
    are not asserted by any test). `drop_leading_labels` lets a caller declare
    that the row deliberately suppresses N leading left-aligned labels (the only
    case: cmd_subagents continuation rows blank the Branch column,
    transcript-analysis.py:688) — declared explicitly per call, never inferred.
    `max_labels` limits labels to only the first N single-token columns, required
    for tables whose header contains a trailing multi-word column name (e.g.,
    cmd_subagent_mix's "Top subagent types") whose tokens would otherwise inflate
    the label count beyond the data row's token count.
    `row_startswith=True` matches only lines where `row_contains` appears at
    column 0, filtering out indented summary/annotation lines that also contain
    the same text (e.g., cmd_skill_invocation summary section).

    Fails loudly (AssertionError) when exactly one header / data row isn't
    found, or when token counts don't line up — a silent mismatch would
    reintroduce the GH-363 bug class under a new cause.
    """
    lines = out.splitlines()
    headers = [ln for ln in lines if header_contains in ln]
    assert len(headers) == 1, f"header match not unique for {header_contains!r}: {len(headers)}"
    header = headers[0]
    if row_startswith:
        rows = [ln for ln in lines if ln.startswith(row_contains) and ln != header]
    else:
        rows = [ln for ln in lines if row_contains in ln and ln != header]
    assert len(rows) == 1, f"row match not unique for {row_contains!r}: {len(rows)}"
    labels = header.split()[drop_leading_labels:]
    if max_labels is not None:
        labels = labels[:max_labels]
    values = rows[0].split()
    assert len(values) >= len(labels), f"row has fewer cells than labels: {rows[0]!r}"
    return dict(zip(labels, values, strict=False))


def _extract_unpriced_total(out: str) -> int:
    """Read cmd_cost's 'Unpriced tokens (unknown model IDs): N' line as an int.

    A single named extractor for this one non-tabular summary line, matching
    _table_cols' role for tabular output — one parse point to update if the
    line's wording changes, instead of an inline regex at each call site.
    """
    match = re.search(r"Unpriced tokens \(unknown model IDs\): ([\d,]+)", out)
    assert match is not None, "unpriced-tokens summary line not found in output"
    return int(match.group(1).replace(",", ""))


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
# _table_cols self-tests
# ---------------------------------------------------------------------------


class TestTableColsHelper:
    """Unit tests for the _table_cols helper — verifies the safety properties
    the refactor depends on."""

    def test_basic_column_lookup(self):
        out = "Branch Lead Main\nfeat   1    0  \n"
        cols = _table_cols(out, header_contains="Lead", row_contains="feat")
        assert cols["Lead"] == "1"
        assert cols["Main"] == "0"

    def test_drop_leading_labels(self):
        out = "Branch Thread Opus Sonnet\nmain   main   1    2\n       sidechain 0  1\n"
        # sidechain row has Branch suppressed — one fewer leading token
        cols = _table_cols(out, header_contains="Thread", row_contains="sidechain",
                           drop_leading_labels=1)
        assert cols["Thread"] == "sidechain"
        assert cols["Opus"] == "0"
        assert cols["Sonnet"] == "1"

    def test_row_not_unique_raises(self):
        out = "Branch Lead\nfeat   1\nfeat   2\n"
        with pytest.raises(AssertionError, match="not unique"):
            _table_cols(out, header_contains="Lead", row_contains="feat")

    def test_row_too_few_tokens_raises(self):
        out = "Branch Lead Main\nfeat   1\n"
        with pytest.raises(AssertionError, match="fewer cells"):
            _table_cols(out, header_contains="Lead", row_contains="feat")

    def test_max_labels_excludes_trailing_multiword_header(self):
        # "Top subagent types" is 3 header tokens but 1 data token (—); max_labels=4
        # limits to Branch/Sess/Spawns/CR so the assertion len(values)>=len(labels) passes.
        out = "Branch Sess Spawns CR Top subagent types\nfeat   1    0      2 —\n"
        cols = _table_cols(out, header_contains="Spawns", row_contains="feat", max_labels=4)
        assert cols["Spawns"] == "0"
        assert cols["CR"] == "2"

    def test_row_startswith_excludes_indented_lines(self):
        # "code-review" appears at column 0 in the data row and indented in the summary.
        out = "skill  top-level user-slash\ncode-review  1  0\n  code-review (1 top)\n"
        cols = _table_cols(out, header_contains="user-slash", row_contains="code-review",
                           row_startswith=True)
        assert cols["top-level"] == "1"
        assert cols["user-slash"] == "0"

    def test_header_not_unique_raises(self):
        # Two lines both contain "Lead" → header ambiguity must raise, not silently pick one.
        out = "Bin Lead Main\n2026-W20 1 0\nLead row duplicate 0 1\n"
        with pytest.raises(AssertionError, match="not unique"):
            _table_cols(out, header_contains="Lead", row_contains="2026-W20")


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
        args = type("A", (), {"projects": "*", "this_repo": False, "branches": None})()
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
        args = type("A", (), {"projects": "*", "this_repo": False, "branches": "feat-a"})()
        _mod.cmd_buckets(args)
        out = capsys.readouterr().out
        assert "feat-a" in out
        assert "feat-b" not in out

    def test_no_data_prints_message(self, fake_projects, capsys):
        args = type("A", (), {"projects": "*", "this_repo": False, "branches": None})()
        _mod.cmd_buckets(args)
        out = capsys.readouterr().out
        assert "No data found." in out

    def test_proj_column_counts_distinct_project_dirs_not_sessions(self, tmp_path, monkeypatch, capsys):
        """Proj tracks a set of project dirs per branch, not a session counter: the
        same project contributing two session files on one branch reports Proj==1,
        Sess==2 — the case that discriminates set semantics from a counter."""
        projects = tmp_path / "projects"
        proj_a = projects / "-home-u-repo-a"
        proj_a.mkdir(parents=True)
        _write_jsonl(proj_a / "sess1.jsonl", [_asst("claude-sonnet-4-6", branch="feat")])
        _write_jsonl(proj_a / "sess2.jsonl", [_asst("claude-sonnet-4-6", branch="feat")])
        monkeypatch.setattr(_mod, "PROJECTS_DIR", projects)

        args = type("A", (), {"projects": "*", "this_repo": False, "branches": None})()
        _mod.cmd_buckets(args)
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Branch", row_contains="feat", max_labels=8)
        assert cols["Proj"] == "1"
        assert cols["Sess"] == "2"

    def test_proj_column_counts_two_project_dirs_sharing_a_branch(self, tmp_path, monkeypatch, capsys):
        projects = tmp_path / "projects"
        proj_a = projects / "-home-u-repo-a"
        proj_b = projects / "-home-u-repo-b"
        proj_a.mkdir(parents=True)
        proj_b.mkdir(parents=True)
        _write_jsonl(proj_a / "sess.jsonl", [_asst("claude-sonnet-4-6", branch="feat")])
        _write_jsonl(proj_b / "sess.jsonl", [_asst("claude-sonnet-4-6", branch="feat")])
        monkeypatch.setattr(_mod, "PROJECTS_DIR", projects)

        args = type("A", (), {"projects": "*", "this_repo": False, "branches": None})()
        _mod.cmd_buckets(args)
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Branch", row_contains="feat", max_labels=8)
        assert cols["Proj"] == "2"
        assert cols["Sess"] == "2"


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
        args = type("A", (), {"projects": "*", "this_repo": False, "branches": None, "per_session": False})()
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
        args = type("A", (), {"projects": "*", "this_repo": False, "branches": None, "per_session": False})()
        _mod.cmd_subagent_mix(args)
        out = capsys.readouterr().out
        # CR=2, PR=1, RR=1; max_labels=6 excludes the trailing multi-word "Top subagent types" column
        cols = _table_cols(out, header_contains="Spawns", row_contains="feat", max_labels=6)
        assert cols["Spawns"] == "0"
        assert cols["CR"] == "2"
        assert cols["PR"] == "1"
        assert cols["RR"] == "1"

    def test_legacy_task_tool_name_also_counted(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="legacy", content=[
                _agent_use("t1", "staff-frontend-engineer", tool_name="Task"),
            ]),
        ])
        args = type("A", (), {"projects": "*", "this_repo": False, "branches": None, "per_session": False})()
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
        args = type("A", (), {"projects": "*", "this_repo": False, "branches": None, "per_session": False})()
        _mod.cmd_subagent_mix(args)
        out = capsys.readouterr().out
        assert "staff-backend-engineer(1)" in out
        assert "ciso-reviewer" not in out

    def test_branch_filter(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="feat-a", content=[_agent_use("a1", "ciso-reviewer")]),
            _asst("claude-opus-4-7", branch="feat-b", content=[_agent_use("a2", "staff-backend-engineer")]),
        ])
        args = type("A", (), {"projects": "*", "this_repo": False, "branches": "feat-a", "per_session": False})()
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
        args = type("A", (), {"projects": "*", "this_repo": False, "branches": None, "per_session": True})()
        _mod.cmd_subagent_mix(args)
        out = capsys.readouterr().out
        # Both sessions should appear with stem prefixes; aggregate "feat" alone should not be present as a row.
        assert "abcd1234" in out
        assert "efgh5678" in out

    def test_no_data_prints_message(self, fake_projects, capsys):
        args = type("A", (), {"projects": "*", "this_repo": False, "branches": None, "per_session": False})()
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


def _skill_pair_args(leader="plan-it", follower="plan-review", *, projects="*", this_repo=False,
                      exclude_projects=None, branches=None):
    return type("A", (), {
        "leader": leader,
        "follower": follower,
        "projects": projects,
        "this_repo": this_repo,
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
        cols = _table_cols(out, header_contains="Lead", row_contains="2026-W20")
        assert cols["Lead"] == "1"
        assert cols["Main"] == "0"
        assert cols["Side"] == "0"
        assert "0.0%" in cols["Pair%"]

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
        cols = _table_cols(out, header_contains="Lead", row_contains="2026-W20")
        assert cols["Lead"] == "1"
        assert cols["Main"] == "1"
        assert "100.0%" in cols["Pair%"]

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
        cols = _table_cols(out, header_contains="Lead", row_contains="2026-W20")
        assert cols["Lead"] == "1"
        assert cols["Main"] == "0"  # no main-thread follower
        assert cols["Side"] == "1"

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
        cols = _table_cols(out, header_contains="Lead", row_contains="2026-W20")
        assert cols["Main"] == "1"
        assert cols["Side"] == "0"  # sidechain-only requires no main hit

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
        cols = _table_cols(out, header_contains="Lead", row_contains="2026-W20")
        assert cols["Lead"] == "1"

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
        # Only 1 leader session (the included project)
        cols = _table_cols(out, header_contains="Lead", row_contains="2026-W20")
        assert cols["Lead"] == "1"

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
        # Only 1 session from the normal project; eval sessions excluded
        cols = _table_cols(out, header_contains="Lead", row_contains="2026-W20")
        assert cols["Lead"] == "1"

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
        cols = _table_cols(out, header_contains="Lead", row_contains="2026-W20")
        assert cols["Lead"] == "1"  # branch-a only
        assert cols["Main"] == "1"  # follower on branch-a


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

        args = type("A", (), {
            "repo": "owner/repo", "branches": "feat-pr", "author": "alice",
            "projects": "*", "this_repo": False,
        })()
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

        args = type("A", (), {"repo": "owner/repo", "branches": "feat-x", "author": "", "projects": "*", "this_repo": False})()
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

        args = type("A", (), {
            "repo": "owner/repo", "branches": "no-pr-branch", "author": "",
            "projects": "*", "this_repo": False,
        })()
        _mod.cmd_pr_link(args)
        out = capsys.readouterr().out
        assert "none" in out


# ---------------------------------------------------------------------------
# commit-gate
# ---------------------------------------------------------------------------


def _gate_args(skill: str, *, by_permission_mode: bool = False, projects: str = "*", this_repo: bool = False,
               exclude_projects: str | None = None, branches: str | None = None):
    """Build a minimal argparse.Namespace for cmd_commit_gate."""
    return type("A", (), {
        "skill": skill,
        "by_permission_mode": by_permission_mode,
        "projects": projects,
        "this_repo": this_repo,
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
        # 1 session, 0 commits, 1 skill invocation; rate = 1000/1 = 1000.0
        cols = _table_cols(out, header_contains="sessions", row_contains="2026-W")
        assert cols["sessions"] == "1"
        assert int(cols["commits"]) == 0
        assert int(cols["skill-inv"]) == 1

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
        cols = _table_cols(out, header_contains="sessions", row_contains="2026-W")
        assert cols["commits"] == "1"
        assert cols["w-skill"] == "1"
        assert cols["wo-skill"] == "0"
        assert cols["no-verify"] == "0"

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
        cols = _table_cols(out, header_contains="sessions", row_contains="2026-W")
        assert cols["commits"] == "1"
        assert cols["w-skill"] == "0"
        assert cols["wo-skill"] == "1"

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
        cols = _table_cols(out, header_contains="sessions", row_contains="2026-W")
        assert cols["commits"] == "2"
        assert cols["w-skill"] == "1"  # second commit, after skill
        assert cols["wo-skill"] == "1"  # first commit, no prior skill

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
        cols = _table_cols(out, header_contains="sessions", row_contains="2026-W")
        assert cols["commits"] == "1"
        assert cols["w-skill"] == "0"  # bypass is NOT credited as gated
        assert cols["wo-skill"] == "1"
        assert cols["no-verify"] == "1"

    def test_amend_counted_as_commit(self, fake_projects, capsys):
        """git commit --amend is counted in the commits total."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:00:00.000Z",
                  content=[_bash_use("b1", "git commit --amend --no-edit")]),
        ])
        args = _gate_args("code-review")
        _mod.cmd_commit_gate(args)
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="sessions", row_contains="2026-W")
        assert cols["commits"] == "1"

    def test_git_commit_tree_not_counted(self, fake_projects, capsys):
        """git commit-tree must NOT match the commit regex (trailing word boundary)."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:00:00.000Z",
                  content=[_bash_use("b1", "git commit-tree HEAD^{tree} -m 'msg'")]),
        ])
        args = _gate_args("code-review")
        _mod.cmd_commit_gate(args)
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="sessions", row_contains="2026-W")
        assert cols["commits"] == "0"  # commit-tree must not be counted

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
        cols = _table_cols(out, header_contains="sessions", row_contains="2026-W")
        assert cols["skill-inv"] == "0"  # sidechain skill not counted
        assert cols["w-skill"] == "0"    # sidechain skill must not gate the commit
        assert cols["wo-skill"] == "1"

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
        cols = _table_cols(out, header_contains="sessions", row_contains="2026-W")
        assert cols["skill-inv"] == "0"  # plugin-prefixed name must not match
        assert cols["w-skill"] == "0"

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
        cols = _table_cols(out, header_contains="sessions", row_contains="2026-W")
        assert cols["w-skill"] == "1"
        assert cols["wo-skill"] == "0"

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
        cols = _table_cols(out, header_contains="sessions", row_contains="2026-W")
        assert cols["w-skill"] == "0"
        assert cols["wo-skill"] == "1"

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
        # Verify both mode values appear; use column-value membership (not positional
        # split-index) so a future column insertion doesn't silently read the wrong field.
        assert any("auto" in ln.split() for ln in data_lines), "expected an 'auto' mode row"
        assert any("default" in ln.split() for ln in data_lines), "expected a 'default' mode row"

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
        cols = _table_cols(out, header_contains="sessions", row_contains="2026-W")
        assert int(cols["sessions"]) == 1  # only 1 session

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
        cols = _table_cols(out, header_contains="sessions", row_contains="2026-W")
        assert int(cols["sessions"]) == 1  # only the non-excluded session

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
        # Still only 1 session (feat-b-only session excluded)
        cols2 = _table_cols(out2, header_contains="sessions", row_contains="2026-W")
        assert int(cols2["sessions"]) == 1


# ---------------------------------------------------------------------------
# review-trace
# ---------------------------------------------------------------------------


def _hook_deny(
    hook_name: str, *, stringified: bool = False, branch: str = "main", ts: str | None = None
) -> dict:
    """Build an attachment/hook_blocking_error record using the real transcript shape.

    Real transcripts nest the human-readable denial text in a "blockingError" key
    inside the blockingError dict (alongside a "command" key).

    When stringified=True, the outer blockingError value is a JSON-encoded string
    of that dict rather than the dict itself (as seen in some real transcripts).

    branch/ts mirror the sibling _hook_deny_current — a synthetic attachment
    denial carries its own gitBranch/timestamp too, so tests can distinguish an
    implementation that reads the record's own branch from one that only
    exercises the carry-forward path.
    """
    human_message = f"Hook '{hook_name}' blocked the operation"
    error_dict = {"blockingError": human_message, "command": "git commit -m x"}
    blocking_error = json.dumps(error_dict) if stringified else error_dict
    rec: dict = {
        "type": "attachment",
        "gitBranch": branch,
        "attachment": {
            "type": "hook_blocking_error",
            "hookName": hook_name,
            "toolUseID": f"toolu_{hook_name[:8]}",
            "blockingError": blocking_error,
        },
    }
    if ts:
        rec["timestamp"] = ts
    return rec


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
    this_repo: bool = False,
    branches: str | None = None,
    since: str | None = None,
    until: str | None = None,
    deny_only: bool = False,
    skill: str | None = None,
) -> object:
    return type("A", (), {
        "projects": projects,
        "this_repo": this_repo,
        "branches": branches,
        "since": since,
        "until": until,
        "deny_only": deny_only,
        "skill": skill,
    })()


def _event_suffix_branch_model(line: str) -> tuple[str, str]:
    """Parse the trailing '(branch=X model=Y)' suffix off a review-trace event line."""
    m = re.search(r"\(branch=(\S+) model=(\S+)\)", line)
    assert m, f"no branch/model suffix found in line: {line!r}"
    return m.group(1), m.group(2)


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

    # -----------------------------------------------------------------------
    # GH-482: per-record branch/model attribution
    # -----------------------------------------------------------------------

    def test_gh482_events_attributed_to_own_branch_not_session_first_branch(self, fake_projects, capsys):
        """A session opening on one branch, then moving to another before any review
        event fires, must attribute every event to its own (later) branch — and
        --branches must select by that per-event value, not the session's first
        record's branch (the 53-session class from row 4 of the GH-482 plan)."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T09:00:00.000Z"),
            _asst("claude-sonnet-4-6", branch="feature-x", ts="2026-05-19T10:00:00.000Z",
                  content=[_skill_use("s1", "code-review")]),
            _asst("claude-opus-4-7", branch="feature-x", ts="2026-05-19T10:05:00.000Z",
                  content=[_agent_use("a1", "staff-backend-engineer")]),
        ])

        _mod.cmd_review_trace(_review_trace_args())
        out = capsys.readouterr().out
        event_lines = [ln for ln in out.splitlines() if ln.startswith("  [")]
        assert len(event_lines) == 2
        for ln in event_lines:
            branch, _model = _event_suffix_branch_model(ln)
            assert branch == "feature-x", f"event must attribute to feature-x, not main: {ln!r}"

        _mod.cmd_review_trace(_review_trace_args(branches="feature-x"))
        out_feature = capsys.readouterr().out
        assert "skill" in out_feature
        assert "reviewer" in out_feature

        _mod.cmd_review_trace(_review_trace_args(branches="main"))
        out_main = capsys.readouterr().out
        assert out_main.strip() == "", "the session's first-record branch must return zero events"

    def test_header_branches_and_models_are_distinct_sorted_sets(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="feat-a", ts="2026-05-19T10:00:00.000Z",
                  content=[_skill_use("s1", "code-review")]),
            _asst("claude-opus-4-7", branch="feat-b", ts="2026-05-19T10:05:00.000Z",
                  content=[_agent_use("a1", "staff-backend-engineer")]),
        ])
        _mod.cmd_review_trace(_review_trace_args())
        out = capsys.readouterr().out
        header = next(ln for ln in out.splitlines() if ln.startswith("branches="))
        branches = re.search(r"branches=(\S+)", header).group(1).split(",")
        models = re.search(r"models=(\S+)", header).group(1).split(",")
        assert set(branches) == {"feat-a", "feat-b"}
        assert set(models) == {"sonnet", "opus"}

    def test_denial_stamped_with_its_own_branch_not_carried_forward(self, fake_projects, capsys):
        """An attachment denial record carrying its own gitBranch, differing from the
        carried-forward branch, is stamped with the record's own value."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:00:00.000Z"),
            _hook_deny("require-code-review", branch="feature-y", ts="2026-05-19T10:05:00.000Z"),
        ])
        _mod.cmd_review_trace(_review_trace_args())
        out = capsys.readouterr().out
        denial_line = next(ln for ln in out.splitlines() if ln.startswith("  [") and "denial" in ln)
        branch, _model = _event_suffix_branch_model(denial_line)
        assert branch == "feature-y"

    def test_denial_inherits_last_assistant_model_not_other(self, fake_projects, capsys):
        """A denial carries no message.model of its own — it must inherit the last
        main-thread assistant model family, not render 'other'."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="main", ts="2026-05-19T10:00:00.000Z"),
            _hook_deny("require-code-review", branch="main", ts="2026-05-19T10:05:00.000Z"),
        ])
        _mod.cmd_review_trace(_review_trace_args())
        out = capsys.readouterr().out
        denial_line = next(ln for ln in out.splitlines() if ln.startswith("  [") and "denial" in ln)
        _branch, model = _event_suffix_branch_model(denial_line)
        assert model == "opus"

    def test_unresolvable_branch_renders_sentinel(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="", ts="2026-05-19T10:00:00.000Z",
                  content=[_skill_use("s1", "code-review")]),
        ])
        _mod.cmd_review_trace(_review_trace_args())
        out = capsys.readouterr().out
        event_line = next(ln for ln in out.splitlines() if ln.startswith("  ["))
        branch, _model = _event_suffix_branch_model(event_line)
        assert branch == "?"

    def test_branch_carry_forward_crosses_since_boundary(self, fake_projects, capsys):
        """An in-window event with no gitBranch of its own inherits the branch of an
        out-of-window record — carry-forward crosses the --since boundary."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="old-branch", ts="2026-05-01T10:00:00.000Z"),
            _asst("claude-sonnet-4-6", branch="", ts="2026-05-19T10:00:00.000Z",
                  content=[_skill_use("s1", "code-review")]),
        ])
        _mod.cmd_review_trace(_review_trace_args(since="2026-05-10"))
        out = capsys.readouterr().out
        event_line = next(ln for ln in out.splitlines() if ln.startswith("  ["))
        branch, _model = _event_suffix_branch_model(event_line)
        assert branch == "old-branch"

    def test_deny_only_with_branches_filters_before_gating(self, fake_projects, capsys):
        """The sole denial sits on a branch --branches excludes: no block is emitted
        (filter-then-deny), not a block that still prints because the session
        qualified for --deny-only before filtering was applied."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _hook_deny("require-code-review", branch="wrong-branch"),
        ])
        _mod.cmd_review_trace(_review_trace_args(deny_only=True, branches="right-branch"))
        out = capsys.readouterr().out
        assert out.strip() == ""

    def test_dedup_before_branch_filter_pins_ordering(self, fake_projects, capsys):
        """A duplicate-id denial recorded on two different branches must still
        collapse to one event when --branches includes both branches — dedup (step 3)
        is global and runs before branch filtering (step 5), not scoped per branch."""
        attach = _hook_deny("worktree", branch="branch-a")
        twin = _hook_deny_current(
            "Blocked by worktree-enforcement hook: 'git add' not allowed.",
            tool_id="toolu_worktree", branch="branch-b",
        )
        _write_jsonl(fake_projects / "sess.jsonl", [attach, twin])

        _mod.cmd_review_trace(_review_trace_args())
        out = capsys.readouterr().out
        denial_lines = [ln for ln in out.splitlines() if ln.startswith("  [") and "denial" in ln]
        assert len(denial_lines) == 1
        assert "denials=1" in out

        # attach (branch-a) is the first-occurring record, so dedup collapses the
        # pair to a single event attributed to branch-a — filtering to branch-b
        # alone (the second-occurring, non-surviving branch) must then drop that
        # event entirely. A filter-before-dedup implementation would instead
        # exclude attach before dedup ever runs, letting twin (branch-b) through
        # undeduped and yielding one event — the regression this pins against.
        _mod.cmd_review_trace(_review_trace_args(branches="branch-b"))
        out_branch_b_only = capsys.readouterr().out
        denial_lines_branch_b_only = [
            ln for ln in out_branch_b_only.splitlines() if ln.startswith("  [") and "denial" in ln
        ]
        assert len(denial_lines_branch_b_only) == 0
        assert out_branch_b_only == ""


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


def _priced(
    model: str,
    *,
    input: int = 0,
    cache_read: int = 0,
    ephemeral_1h: int = 0,
    ephemeral_5m: int = 0,
    output: int = 0,
    flat_cache_creation: int | None = None,
    ts: str = "2026-05-19T10:00:00.000Z",
) -> dict:
    """Build an assistant record with explicit priced usage fields for cost tests.

    flat_cache_creation=None (the default) emits the nested cache_creation
    block from ephemeral_1h/ephemeral_5m, with the flat cache_creation_input_tokens
    field set to their sum — matching every real usage record sampled, where the
    two always agree. flat_cache_creation=N omits the nested block entirely and
    emits only the flat field (the pre-nested-block fallback shape), ignoring
    ephemeral_1h/ephemeral_5m.
    """
    rec = _asst(model, ts=ts, content=[])
    usage: dict = {
        "input_tokens": input,
        "output_tokens": output,
        "cache_read_input_tokens": cache_read,
    }
    if flat_cache_creation is not None:
        usage["cache_creation_input_tokens"] = flat_cache_creation
    else:
        usage["cache_creation_input_tokens"] = ephemeral_1h + ephemeral_5m
        usage["cache_creation"] = {
            "ephemeral_1h_input_tokens": ephemeral_1h,
            "ephemeral_5m_input_tokens": ephemeral_5m,
        }
    rec["message"]["usage"] = usage
    return rec


def _cost_args(
    *,
    projects: str = "*",
    since: str | None = None,
    top: int = 20,
    no_redact: bool = False,
) -> object:
    return type("A", (), {
        "projects": projects,
        "since": since,
        "top": top,
        "no_redact": no_redact,
    })()


def _exit_plan_mode(tool_id: str = "epm1") -> dict:
    return {"type": "tool_use", "id": tool_id, "name": "ExitPlanMode", "input": {}}


def _thinking_block() -> dict:
    return {"type": "thinking", "thinking": "some thought"}


def _audit_routing_args(
    *,
    projects: str = "*",
    this_repo: bool = False,
    since: str | None = None,
    top: int = 20,
    redact: bool = False,
) -> object:
    return type("A", (), {
        "projects": projects,
        "this_repo": this_repo,
        "since": since,
        "top": top,
        "redact": redact,
    })()


def _extract_corpus_class_tokens(out: str, cls: str) -> int:
    """Parse output-token value for a class from the corpus aggregate section."""
    # Locate the header to anchor the column index for "Output tokens".
    # "Output" is the unique leading word of the "Output tokens" column.
    header_line = next((ln for ln in out.splitlines() if "Class" in ln and "Output tokens" in ln), None)
    out_idx = header_line.split().index("Output") if header_line else 1
    for line in out.splitlines():
        stripped = line.strip()
        if stripped.startswith(cls):
            parts = stripped.split()
            if len(parts) > out_idx:
                return int(parts[out_idx].replace(",", ""))
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
# cost
# ---------------------------------------------------------------------------


class TestCost:
    def test_price_turn_hand_computed_dollar_total(self):
        """_price_turn's per-class dollars match a hand-computed total, read back
        through the named extractor (_price_turn itself) rather than a string
        match on formatted `$`-prefixed, comma-separated table output."""
        usage = _priced(
            "claude-sonnet-5",
            input=100_000, cache_read=200_000, ephemeral_1h=10_000, ephemeral_5m=20_000, output=5_000,
        )["message"]["usage"]
        dollars, context_at_turn, unpriced = _mod._price_turn("claude-sonnet-5", usage)
        assert unpriced == 0
        assert context_at_turn == 100_000 + 200_000 + 10_000 + 20_000
        # Sonnet 5 base $2/MTok: output 5x=$10, cache_write_5m 1.25x=$2.5,
        # cache_write_1h 2x=$4, cache_read 0.1x=$0.2, input=$2 (all per MTok).
        assert dollars["input"] == pytest.approx(100_000 / 1_000_000 * 2.0)
        assert dollars["output"] == pytest.approx(5_000 / 1_000_000 * 10.0)
        assert dollars["cache_read"] == pytest.approx(200_000 / 1_000_000 * 0.2)
        assert dollars["cache_write_1h"] == pytest.approx(10_000 / 1_000_000 * 4.0)
        assert dollars["cache_write_5m"] == pytest.approx(20_000 / 1_000_000 * 2.5)

    def test_raw_dollars_summed_before_rounding_not_after(self, fake_projects, capsys):
        """Three sub-cent turns ($0.166 each) sum to $0.498, rendering '0.50' — a
        round-then-sum bug would render each turn as $0.17 and total '0.51'."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=83_000),
            _priced("claude-sonnet-5", input=83_000),
            _priced("claude-sonnet-5", input=83_000),
        ])
        _mod._cost_report(_cost_args(), date(2026, 8, 2))
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Class", row_contains="input", row_startswith=True)
        assert cols["$"] == "0.50"

    def test_nested_cache_creation_priced_separately_1h_and_5m(self):
        """Nested ephemeral_1h/ephemeral_5m tokens are split and never summed together."""
        usage = _priced("claude-sonnet-5", ephemeral_1h=10_000, ephemeral_5m=20_000)["message"]["usage"]
        assert _mod._cache_write_split(usage) == (10_000, 20_000)

    def test_flat_and_nested_both_present_not_double_counted(self):
        """flat cache_creation_input_tokens sums the nested fields on every real
        record (row2); the split must read only the nested block, never add
        flat on top of it."""
        usage = _priced("claude-sonnet-5", ephemeral_1h=10_000, ephemeral_5m=20_000)["message"]["usage"]
        assert usage["cache_creation_input_tokens"] == 30_000  # flat present too
        eph_1h, eph_5m = _mod._cache_write_split(usage)
        assert eph_1h + eph_5m == 30_000  # not 60,000 (double-counted)

    def test_flat_cache_creation_fallback_priced_as_5m_only(self):
        """Nested block absent (the untested-by-real-data fallback path): the flat
        total is priced entirely as 5m, at 1.25x — never split into 1h at 2x."""
        usage = _priced("claude-sonnet-5", flat_cache_creation=40_000)["message"]["usage"]
        assert "cache_creation" not in usage
        assert _mod._cache_write_split(usage) == (0, 40_000)
        dollars, _context_at_turn, unpriced = _mod._price_turn("claude-sonnet-5", usage)
        assert unpriced == 0
        assert dollars["cache_write_5m"] == pytest.approx(40_000 / 1_000_000 * 2.5)
        assert dollars["cache_write_1h"] == 0.0

    def test_empty_nested_cache_creation_block_not_treated_as_absent(self):
        """A present-but-empty nested block ({}) means zero of both ephemeral kinds —
        it must not fall through to the flat field as if the nested block were
        absent, even though both {} and a missing key are falsy."""
        usage = {
            "input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 40_000, "cache_creation": {},
        }
        assert _mod._cache_write_split(usage) == (0, 0)

    def test_per_model_id_selection_sonnet5_vs_sonnet46(self, fake_projects, capsys):
        """Sonnet 5 ($2 base) and Sonnet 4.6 ($3 base) price the same input tokens differently."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000),
            _priced("claude-sonnet-4-6", input=1_000_000),
        ])
        _mod._cost_report(_cost_args(), date(2026, 8, 2))
        out = capsys.readouterr().out
        sonnet5 = _table_cols(out, header_contains="Model", row_contains="claude-sonnet-5")
        sonnet46 = _table_cols(out, header_contains="Model", row_contains="claude-sonnet-4-6")
        assert sonnet5["$"] == "2.00"
        assert sonnet46["$"] == "3.00"

    def test_unknown_model_id_surfaced_and_excluded_from_total(self, fake_projects, capsys):
        """Unknown model IDs are named in the model table and their tokens counted
        separately, never silently folded into the priced total at $0."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("<synthetic>", input=1_000_000, output=500_000, cache_read=200_000),
            _priced("claude-sonnet-5", input=1_000_000),
        ])
        _mod._cost_report(_cost_args(), date(2026, 8, 2))
        out = capsys.readouterr().out
        assert "<synthetic>" in out
        assert "unpriced" in out
        assert _extract_unpriced_total(out) == 1_000_000 + 500_000 + 200_000
        cols = _table_cols(out, header_contains="Class", row_contains="input", row_startswith=True)
        assert cols["$"] == "2.00"  # only the priced sonnet-5 turn's $2/MTok * 1M input tokens

    def test_mixed_model_ids_within_session_sums_per_turn(self, fake_projects, capsys):
        """A session mixing Sonnet 5 and Sonnet 4.6 turns prices each against its own
        model ID; the session total is their sum, not a dominant-family pick
        (token-analyzer.py:16-23's per-session _fam shape, which this must not copy)."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000),      # $2.00
            _priced("claude-sonnet-4-6", input=1_000_000),    # $3.00
        ])
        _mod._cost_report(_cost_args(), date(2026, 8, 2))
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Session", row_contains="private-project-1")
        assert cols["$"] == "5.00"

    def test_context_bucket_threshold_boundaries(self):
        """199,999 is <200k; exactly 200,000 and 200,001 are >=200k (inclusive edge)."""
        assert _mod._context_bucket(199_999) == "<200k"
        assert _mod._context_bucket(200_000) == ">=200k"
        assert _mod._context_bucket(200_001) == ">=200k"

    def test_price_turn_rate_independent_of_context_bucket(self):
        """The $/token rate does not change with context size — pins row4 (no
        >200k long-context premium applies to any model in the corpus) against
        a future regression."""
        usage_small = _priced("claude-sonnet-5", input=199_999)["message"]["usage"]
        usage_large = _priced("claude-sonnet-5", input=200_001)["message"]["usage"]
        dollars_small, ctx_small, _ = _mod._price_turn("claude-sonnet-5", usage_small)
        dollars_large, ctx_large, _ = _mod._price_turn("claude-sonnet-5", usage_large)
        assert _mod._context_bucket(ctx_small) == "<200k"
        assert _mod._context_bucket(ctx_large) == ">=200k"
        assert dollars_small["input"] / 199_999 == pytest.approx(dollars_large["input"] / 200_001)

    def test_cost_by_context_bucket_section_reflects_boundary(self, fake_projects, capsys):
        """A <200k turn and a >=200k turn land in their respective bucket rows."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=100_000),    # <200k, $0.20
            _priced("claude-sonnet-5", input=1_000_000),  # >=200k, $2.00
        ])
        _mod._cost_report(_cost_args(), date(2026, 8, 2))
        out = capsys.readouterr().out
        under = _table_cols(out, header_contains="Bucket", row_contains="<200k")
        over = _table_cols(out, header_contains="Bucket", row_contains=">=200k")
        assert under["$"] == "0.20"
        assert over["$"] == "2.00"

    def test_empty_corpus_renders_clean_zero_state(self, fake_projects, capsys):
        """No priced turns at all: every share renders 0.0% and there's no traceback."""
        _mod._cost_report(_cost_args(), date(2026, 8, 2))
        out = capsys.readouterr().out
        assert "0.0%" in out
        assert "(no priced turns in range)" in out

    def test_staleness_banner_fires_when_today_past_expires(self, fake_projects, capsys):
        """today past Sonnet 5's 2026-08-31 expiry: the banner fires in the same
        output block as the dollar tables, not a separate log line."""
        _write_jsonl(fake_projects / "sess.jsonl", [_priced("claude-sonnet-5", input=1_000)])
        _mod._cost_report(_cost_args(), date(2026, 9, 1))
        out = capsys.readouterr().out
        assert "STALE PRICING" in out
        assert "claude-sonnet-5" in out.split("## Cost by token class")[0]

    def test_staleness_banner_absent_when_today_before_expires(self, fake_projects, capsys):
        """today before expiry: no banner — a one-direction test would pass even
        if the banner always printed."""
        _write_jsonl(fake_projects / "sess.jsonl", [_priced("claude-sonnet-5", input=1_000)])
        _mod._cost_report(_cost_args(), date(2026, 8, 2))
        out = capsys.readouterr().out
        assert "STALE PRICING" not in out

    def test_sidechain_turns_priced_exactly_once(self, fake_projects, capsys):
        """cost prices subagent (isSidechain) turns via include_subagents=True —
        exactly once, not skipped and not duplicated."""
        session_id = "sess-side"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000),  # main thread: $2.00
        ])
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [
            _priced("claude-sonnet-5", input=1_000_000),  # sidechain: $2.00
        ])
        _mod._cost_report(_cost_args(), date(2026, 8, 2))
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Class", row_contains="input", row_startswith=True)
        assert cols["$"] == "4.00"

    def test_redact_proj_label_map_miss_returns_opaque_token_not_raw_name(self):
        """A project label absent from the map renders as the fixed opaque token,
        never the raw project name — a map miss must fail closed. Built with
        two real labels so the map can express "one mapped, one missing"; a
        single-label fixture can't distinguish a miss from an empty map."""
        redact_map = {"project-a": "private-project-1"}  # "project-b" deliberately absent
        assert _mod._redact_proj_label("project-a", redact_map) == "private-project-1"
        assert _mod._redact_proj_label("project-b", redact_map) == _mod._REDACT_MAP_MISS_TOKEN
        assert _mod._REDACT_MAP_MISS_TOKEN != "project-b"

    def test_redact_session_id_map_miss_returns_opaque_token_not_raw_id(self):
        """Same fail-closed contract as the project-label map, for session IDs:
        a session_id absent from the run-scoped map renders as the fixed
        opaque token, never the raw session ID."""
        session_map = {"sess-real-id": "session-1"}  # "sess-other-id" deliberately absent
        assert _mod._redact_session_id("sess-real-id", session_map) == "session-1"
        assert _mod._redact_session_id("sess-other-id", session_map) == _mod._REDACT_SESSION_MISS_TOKEN
        assert _mod._REDACT_SESSION_MISS_TOKEN != "sess-other-id"

    def test_redact_proj_label_claude_config_passthrough_preserved(self):
        """claude-config still passes through unredacted after the fail-closed rewrite."""
        assert _mod._redact_proj_label("claude-config", {}) == "claude-config"

    def test_cost_redact_default_hides_project_names_and_session_ids(self, fake_projects, capsys):
        """Default (no --no-redact): raw project labels and raw session IDs are
        absent from stdout across every cost section — catches a partial leak,
        not just the total no-op test_redact_flag_anonymizes_project_names checks."""
        _write_jsonl(fake_projects / "sess-one.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])
        proj2 = fake_projects.parent / "-home-user-otherrepo"
        proj2.mkdir(parents=True)
        _write_jsonl(proj2 / "sess-two.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])
        _mod._cost_report(_cost_args(), date(2026, 8, 2))
        out = capsys.readouterr().out
        assert "testrepo" not in out
        assert "otherrepo" not in out
        assert "sess-one" not in out
        assert "sess-two" not in out
        assert "private-project-1" in out
        assert "private-project-2" in out

    def test_cost_no_redact_emits_real_label_and_session_id(self, fake_projects, capsys):
        """--no-redact emits the real project label and real session ID — proving
        the default redaction didn't silently become the only mode."""
        _write_jsonl(fake_projects / "sess-plain.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])
        _mod._cost_report(_cost_args(no_redact=True), date(2026, 8, 2))
        out = capsys.readouterr().out
        assert "testrepo" in out
        assert "sess-plain" in out

    def test_shared_redact_map_binds_same_project_across_cost_and_audit_routing(self, fake_projects, capsys):
        """cost and audit-routing share _build_redact_map: a project binds to the
        same private-project-N placeholder from both, even though cost's own
        --projects filter here is narrower than the full corpus — a per-command
        map built only from the narrowed glob would assign zzzlast
        private-project-1 instead of -2, since it would be the only project seen."""
        proj_b = fake_projects.parent / "-home-user-zzzlast"
        proj_b.mkdir(parents=True)
        _write_jsonl(fake_projects / "sess-a.jsonl", [_opus([_agent_use("a1", "code-writer")], out=100)])
        _write_jsonl(proj_b / "sess-b.jsonl", [
            _opus([_agent_use("a1", "code-writer")], out=100),
            _priced("claude-sonnet-5", input=1_000_000),
        ])

        expected_map = _mod._build_redact_map()
        assert expected_map["testrepo"] == "private-project-1"
        assert expected_map["zzzlast"] == "private-project-2"

        _mod.cmd_audit_routing(_audit_routing_args(projects="*", redact=True))
        audit_out = capsys.readouterr().out
        assert "private-project-2" in audit_out

        _mod._cost_report(_cost_args(projects="-home-user-zzzlast", no_redact=False), date(2026, 8, 2))
        cost_out = capsys.readouterr().out
        assert "private-project-2" in cost_out
        assert "private-project-1" not in cost_out  # testrepo's session wasn't in this narrowed run

    def test_since_filter_excludes_out_of_window_turn(self, fake_projects, capsys):
        """--since window: a turn timestamped outside the window is excluded from the total."""
        old_ts = "2020-01-01T00:00:00.000Z"   # far in the past — always out-of-window
        new_ts = "2099-12-31T00:00:00.000Z"   # far in the future — always in-window
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts=old_ts),
            _priced("claude-sonnet-5", input=2_000_000, ts=new_ts),
        ])
        _mod._cost_report(_cost_args(since="1d"), date(2026, 8, 2))
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Class", row_contains="input", row_startswith=True)
        assert cols["$"] == "4.00"  # only the 2,000,000-input-token turn is in-window

    def test_since_malformed_value_exits_nonzero(self, fake_projects, capsys):
        """A malformed --since value (not 'Nd') fails closed with a usage message, not a traceback."""
        with pytest.raises(SystemExit):
            _mod._cost_report(_cost_args(since="not-a-window"), date(2026, 8, 2))
        assert "expected Nd like '35d'" in capsys.readouterr().err

    def test_top_n_truncates_session_rows(self, fake_projects, capsys):
        """--top N keeps only the N highest-dollar sessions; excluded sessions don't appear."""
        for i in range(5):
            _write_jsonl(fake_projects / f"sess-{i}.jsonl", [_priced("claude-sonnet-5", input=(i + 1) * 1_000_000)])
        _mod._cost_report(_cost_args(top=2), date(2026, 8, 2))
        out = capsys.readouterr().out
        top_section = out.split("## Top 2 sessions by dollars")[1]
        session_lines = [ln for ln in top_section.splitlines() if ln.startswith("session-")]
        assert len(session_lines) == 2
        # The two highest-dollar sessions (i=3,4 -> $8.00, $10.00) survive; i=0,1,2 are truncated.
        assert "10.00" in top_section
        assert "8.00" in top_section
        assert "2.00" not in top_section


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
    return type("A", (), {
        "projects": projects, "this_repo": False, "since": since, "debug_detector": False,
    })()


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
        cols = _table_cols(out, header_contains="Handoffs", row_contains="Total")
        assert int(cols["Handoffs"]) == 2, "Expected 2 handoffs in Total row"
        assert int(cols["Compactions"]) == 2, "Expected 2 compactions in Total row"
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
        cols = _table_cols(out, header_contains="Handoffs", row_contains="Total")
        assert int(cols["Handoffs"]) == 1, "Expected 1 handoff in Total row"
        assert int(cols["Compactions"]) == 0, "Expected 0 compactions in Total row"


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
    this_repo: bool = False,
    since: str | None = None,
) -> object:
    return type("A", (), {
        "projects": projects,
        "this_repo": this_repo,
        "since": since,
    })()


def _extract_shape_d1(out: str, bucket: str) -> tuple[int, int]:
    """Parse (turn_count, output_tokens) for a D1 bucket from audit-routing-shape output."""
    in_d1 = False
    header_line: str | None = None
    for line in out.splitlines():
        stripped = line.strip()
        if "D1" in stripped and "Files Read" in stripped:
            in_d1 = True
            continue
        if in_d1 and stripped.startswith("###"):
            break
        if in_d1 and "Bucket" in stripped and "Turns" in stripped:
            header_line = stripped
            continue
        if in_d1 and stripped.startswith(bucket):
            parts = stripped.split()
            # Anchor Turns and Output tokens columns by the header row.
            # "Output" is the unique leading word of the "Output tokens" column.
            turns_idx = header_line.split().index("Turns") if header_line else 1
            out_idx = header_line.split().index("Output") if header_line else turns_idx + 1
            if len(parts) > out_idx:
                return int(parts[turns_idx].replace(",", "")), int(parts[out_idx].replace(",", ""))
    return 0, 0


def _extract_shape_d2(out: str, bucket: str) -> tuple[int, int]:
    """Parse (streak_count, output_tokens) for a D2 bucket from audit-routing-shape output."""
    in_d2 = False
    header_line: str | None = None
    for line in out.splitlines():
        stripped = line.strip()
        if "D2" in stripped and "streak" in stripped.lower():
            in_d2 = True
            continue
        if in_d2 and stripped.startswith("###"):
            break
        if in_d2 and "Bucket" in stripped and "Streaks" in stripped:
            header_line = stripped
            continue
        if in_d2 and stripped.startswith(bucket):
            parts = stripped.split()
            # Anchor Streaks and Output tokens columns by the header row.
            # "Output" is the unique leading word of the "Output tokens" column.
            streaks_idx = header_line.split().index("Streaks") if header_line else 1
            out_idx = header_line.split().index("Output") if header_line else streaks_idx + 1
            if len(parts) > out_idx:
                return int(parts[streaks_idx].replace(",", "")), int(parts[out_idx].replace(",", ""))
    return 0, 0


def _extract_shape_d3(out: str, case: str) -> tuple[int, int]:
    """Parse (turn_count, output_tokens) for a D3 case from audit-routing-shape output."""
    in_d3 = False
    header_line: str | None = None
    for line in out.splitlines():
        stripped = line.strip()
        if "D3" in stripped and "Read-then-edit" in stripped:
            in_d3 = True
            continue
        if in_d3 and stripped.startswith("###"):
            break
        if in_d3 and stripped.startswith("####"):
            break
        if in_d3 and "Case" in stripped and "Turns" in stripped:
            header_line = stripped
            continue
        if in_d3 and stripped.startswith(case):
            parts = stripped.split()
            # Anchor Turns and Output tokens columns by the header row.
            # "Output" is the unique leading word of the "Output tokens" column.
            turns_idx = header_line.split().index("Turns") if header_line else 1
            out_idx = header_line.split().index("Output") if header_line else turns_idx + 1
            if len(parts) > out_idx:
                return int(parts[turns_idx].replace(",", "")), int(parts[out_idx].replace(",", ""))
    return 0, 0


def _extract_shape_d3_xtab(out: str, case: str, bucket: str) -> tuple[int, int]:
    """Parse (turn_count, output_tokens) for a D3 × D1 cross-tab cell."""
    in_xtab = False
    header_line: str | None = None
    for line in out.splitlines():
        stripped = line.strip()
        if "D3 × D1 cross-tab" in stripped or "D3 x D1 cross-tab" in stripped.lower():
            in_xtab = True
            continue
        if in_xtab and (stripped.startswith("###") or stripped.startswith("Dispatchable")):
            break
        if in_xtab and "Case" in stripped and "Turns" in stripped:
            header_line = stripped
            continue
        if in_xtab and stripped.startswith(case) and bucket in stripped:
            parts = stripped.split()
            # "D1 bucket" is a 2-token header name but maps to 1 data token (e.g. "2-3").
            # header.split().index("Turns") = 3, but the data-row Turns value is at position 2;
            # subtract 1 to correct for the header's extra token from the multi-word column.
            turns_idx = header_line.split().index("Turns") - 1 if header_line else 2
            if len(parts) > turns_idx + 1:
                return int(parts[turns_idx].replace(",", "")), int(parts[turns_idx + 1].replace(",", ""))
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
    this_repo: bool = False,
    since: str | None = None,
    sample: int = 100,
    seed: int | None = 42,
    output_format: str = "json",
) -> object:
    return type("A", (), {
        "projects": projects,
        "this_repo": this_repo,
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
        args = type("A", (), {"projects": "*", "this_repo": False, "branches": "feat"})()
        _mod.cmd_struggle(args)
        out = capsys.readouterr().out

        # The output table should include "feat" with at least one struggle signal.
        assert "feat" in out, "branch not found in output"
        # Parse the data row for "feat": columns are Branch Opus Sonnet Haiku Other Unknown
        cols = _table_cols(out, header_contains="Opus", row_contains="feat")
        total_signals = sum(int(cols[k]) for k in ["Opus", "Sonnet", "Haiku", "Other", "Unknown"])
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
        args = type("A", (), {"projects": "*", "this_repo": False, "branches": "feat"})()
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
        args = type("A", (), {"projects": "*", "this_repo": False, "branches": "feat"})()
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
        # Top-level column should be ≥1
        cols = _table_cols(out, header_contains="user-slash", row_contains="code-review", row_startswith=True)
        assert int(cols["top-level"]) >= 1, f"expected top-level ≥1, got: {cols}"
        assert int(cols["routed"]) == 0, f"expected routed=0, got: {cols}"
        assert int(cols["user-slash"]) == 0, f"expected slash=0, got: {cols}"

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
        cols = _table_cols(out, header_contains="user-slash", row_contains="plan-review", row_startswith=True)
        assert int(cols["top-level"]) == 0, f"expected top-level=0, got: {cols}"
        assert int(cols["routed"]) >= 1, f"expected routed≥1, got: {cols}"

    def test_slash_invocation_counted(self, fake_projects, capsys):
        """User record whose content contains <command-name>/plan-it</command-name> → user-slash count."""
        user_rec = _user_msg(
            "<command-message>plan-it</command-message>\n<command-name>/plan-it</command-name>",
            branch="main",
        )
        _write_jsonl(fake_projects / "s4.jsonl", [user_rec])
        out = self._run(capsys)
        # plan-it must appear in the table; slash column should be ≥1
        assert "plan-it" in out
        cols = _table_cols(out, header_contains="user-slash", row_contains="plan-it", row_startswith=True)
        assert int(cols["user-slash"]) >= 1, f"expected slash≥1, got: {cols}"

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
        cr_cols = _table_cols(out, header_contains="user-slash", row_contains="code-review", row_startswith=True)
        assert int(cr_cols["top-level"]) == 1, f"code-review top-level count should be 1: {cr_cols}"
        pr_cols = _table_cols(out, header_contains="user-slash", row_contains="plan-review", row_startswith=True)
        assert int(pr_cols["top-level"]) == 1, f"plan-review top-level count should be 1: {pr_cols}"

    def test_multiple_slash_tags_in_one_user_record(self, fake_projects, capsys):
        """Two <command-name> tags in a single user record → both skill names counted in slash column."""
        user_rec = _user_msg(
            "<command-name>/plan-it</command-name>\n<command-name>/code-review</command-name>",
            branch="main",
        )
        _write_jsonl(fake_projects / "s_multi_slash.jsonl", [user_rec])
        out = self._run(capsys)
        pi_cols = _table_cols(out, header_contains="user-slash", row_contains="plan-it", row_startswith=True)
        assert int(pi_cols["user-slash"]) >= 1, f"plan-it slash count should be ≥1: {pi_cols}"
        cr_cols = _table_cols(out, header_contains="user-slash", row_contains="code-review", row_startswith=True)
        assert int(cr_cols["user-slash"]) >= 1, f"code-review slash count should be ≥1: {cr_cols}"

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
        cols = _table_cols(out, header_contains="user-slash", row_contains="plan-it", row_startswith=True)
        assert int(cols["user-slash"]) >= 1, f"plan-it slash count should be ≥1: {cols}"

    def test_mixed_top_and_routed_in_same_session(self, fake_projects, capsys):
        """A session with both top-level and routed calls for the same skill tallies both columns."""
        top_rec = _asst("claude-sonnet-4-6", branch="main", content=[_skill_use("t10a", "code-review")])
        # No attributionSkill → top-level
        routed_rec = _asst("claude-haiku-4-5", branch="main", content=[_skill_use("t10b", "code-review")])
        routed_rec["attributionSkill"] = "ready-for-review"
        _write_jsonl(fake_projects / "s10.jsonl", [top_rec, routed_rec])
        out = self._run(capsys)
        cols = _table_cols(out, header_contains="user-slash", row_contains="code-review", row_startswith=True)
        assert int(cols["top-level"]) == 1, f"expected top-level=1, got: {cols}"
        assert int(cols["routed"]) == 1, f"expected routed=1, got: {cols}"
        assert int(cols["total"]) == 2, f"expected total=2, got: {cols}"

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
        cols = _table_cols(out, header_contains="user-slash", row_contains="code-review", row_startswith=True)
        assert int(cols["user-slash"]) == 1, f"expected slash=1, got: {cols}"

    def test_multiple_skill_blocks_on_routed_record(self, fake_projects, capsys):
        """An attributed record with two Skill tool_use blocks counts both as routed, not top-level."""
        rec = _asst("claude-haiku-4-5", branch="main", content=[
            _skill_use("tr1", "code-review"),
            _skill_use("tr2", "plan-review"),
        ])
        rec["attributionSkill"] = "ready-for-review"
        _write_jsonl(fake_projects / "s_multi_routed.jsonl", [rec])
        out = self._run(capsys)
        cr_cols = _table_cols(out, header_contains="user-slash", row_contains="code-review", row_startswith=True)
        assert int(cr_cols["top-level"]) == 0, f"code-review top-level should be 0: {cr_cols}"
        assert int(cr_cols["routed"]) == 1, f"code-review routed should be 1: {cr_cols}"
        pr_cols = _table_cols(out, header_contains="user-slash", row_contains="plan-review", row_startswith=True)
        assert int(pr_cols["top-level"]) == 0, f"plan-review top-level should be 0: {pr_cols}"
        assert int(pr_cols["routed"]) == 1, f"plan-review routed should be 1: {pr_cols}"

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
        cols = _table_cols(out, header_contains="user-slash", row_contains="code-review", row_startswith=True)
        assert int(cols["top-level"]) == 2, f"expected 2 top-level from both projects: {cols}"


# ---------------------------------------------------------------------------
# skill-invocation: --branches, --include-subagents, name normalization
# ---------------------------------------------------------------------------


def _skill_inv_args(*, projects="*", branches=None, include_subagents=False):
    """Args object for cmd_skill_invocation. projects='*' takes the explicit-glob
    escape hatch, bypassing git derivation — the default for cases that only
    exercise counting/filtering logic on fake_projects data."""
    return argparse.Namespace(
        projects=projects, branches=branches, include_subagents=include_subagents
    )


class TestSkillInvocationScoping:
    """Covers the branch/subagent scoping the procedural-fidelity consumer needs.

    The default (no flags) path is pinned by TestSkillInvocation above; these
    cases assert the opt-in behavior and that enabling it does not change what
    the default reports. They pass projects='*' (explicit escape hatch) so the
    git-derivation path is exercised separately in TestSkillInvocationRepoScope.
    """

    def _run(self, capsys, **kw):
        _mod.cmd_skill_invocation(_skill_inv_args(**kw))
        return capsys.readouterr().out

    def test_branch_filter_excludes_other_branches(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "s.jsonl", [
            _asst("claude-sonnet-4-6", branch="wanted", content=[_skill_use("b1", "code-review")]),
            _asst("claude-sonnet-4-6", branch="other", content=[_skill_use("b2", "plan-review")]),
        ])
        out = self._run(capsys, branches="wanted")
        assert "code-review" in out, "on-branch skill should be counted"
        assert "plan-review" not in out, "off-branch skill must be excluded"

    def test_branch_filter_accepts_comma_separated_list(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "s.jsonl", [
            _asst("claude-sonnet-4-6", branch="b-one", content=[_skill_use("m1", "code-review")]),
            _asst("claude-sonnet-4-6", branch="b-two", content=[_skill_use("m2", "plan-review")]),
            _asst("claude-sonnet-4-6", branch="b-three", content=[_skill_use("m3", "handoff")]),
        ])
        out = self._run(capsys, branches="b-one,b-two")
        assert "code-review" in out and "plan-review" in out
        assert "handoff" not in out, "branch outside the list must be excluded"

    def test_branch_filter_applies_to_slash_invocations(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "s.jsonl", [
            _user_msg("<command-name>/plan-it</command-name>", branch="wanted"),
            _user_msg("<command-name>/handoff</command-name>", branch="other"),
        ])
        out = self._run(capsys, branches="wanted")
        assert "plan-it" in out
        assert "handoff" not in out, "off-branch slash invocation must be excluded"

    def test_unfiltered_run_still_counts_records_without_gitbranch(self, fake_projects, capsys):
        """A record with no gitBranch is counted when no --branches filter is given —
        guards the default-output regression noted in the plan."""
        rec = {
            "type": "assistant",
            "message": {"model": "claude-sonnet-4-6", "content": [_skill_use("nb", "code-review")]},
        }
        _write_jsonl(fake_projects / "s.jsonl", [rec])
        out = self._run(capsys)
        assert "code-review" in out, "branchless record must still count when unfiltered"

    def test_subagent_invocation_ignored_by_default(self, fake_projects, capsys):
        session_id = "sess-sub"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [_asst("claude-opus-4-7", branch="main")])
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [
            _asst("claude-sonnet-4-6", branch="main", sidechain=True,
                  content=[_skill_use("sa1", "code-review")]),
        ])
        out = self._run(capsys)
        assert "No skill invocations found." in out

    def test_include_subagents_reports_sidechain_row(self, fake_projects, capsys):
        session_id = "sess-sub"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [_asst("claude-opus-4-7", branch="main")])
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [
            _asst("claude-sonnet-4-6", branch="main", sidechain=True,
                  content=[_skill_use("sa1", "code-review")]),
        ])
        out = self._run(capsys, include_subagents=True)
        cols = _table_cols(out, header_contains="thread", row_contains="code-review",
                           row_startswith=True)
        assert cols["thread"] == "sidechain", f"expected sidechain thread row: {cols}"
        assert int(cols["top-level"]) == 1, f"expected 1 sidechain invocation: {cols}"

    def test_main_and_sidechain_kept_on_separate_rows(self, fake_projects, capsys):
        session_id = "sess-both"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[_skill_use("m1", "code-review")]),
        ])
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [
            _asst("claude-sonnet-4-6", branch="main", sidechain=True,
                  content=[_skill_use("s1", "code-review")]),
        ])
        out = self._run(capsys, include_subagents=True)
        rows = [ln for ln in out.splitlines() if ln.startswith("code-review")]
        assert len(rows) == 2, f"expected separate main and sidechain rows, got: {rows}"
        # The thread value is the second whitespace-delimited column; compare on the
        # parsed token rather than a padded-substring match, which would break on a
        # benign column-width change.
        threads = {r.split()[1] for r in rows}
        assert threads == {"main", "sidechain"}, f"expected one main and one sidechain row, got: {rows}"

    def test_thread_column_absent_without_flag(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "s.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", content=[_skill_use("t1", "code-review")]),
        ])
        out = self._run(capsys)
        header = [ln for ln in out.splitlines() if "user-slash" in ln][0]
        assert "thread" not in header, f"default output must not gain a thread column: {header!r}"

    def test_branch_filter_composes_with_include_subagents(self, fake_projects, capsys):
        session_id = "sess-mix"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [_asst("claude-opus-4-7", branch="wanted")])
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [
            _asst("claude-sonnet-4-6", branch="wanted", sidechain=True,
                  content=[_skill_use("s1", "code-review")]),
            _asst("claude-sonnet-4-6", branch="other", sidechain=True,
                  content=[_skill_use("s2", "plan-review")]),
        ])
        out = self._run(capsys, branches="wanted", include_subagents=True)
        assert "code-review" in out
        assert "plan-review" not in out, "off-branch subagent invocation must be excluded"


class TestSkillInvocationNameNormalization:
    """Worktree-qualified and colon-prefixed skill-name handling (row hygiene)."""

    def _run(self, capsys, **kw):
        _mod.cmd_skill_invocation(_skill_inv_args(**kw))
        return capsys.readouterr().out

    def test_worktree_qualified_name_collapses_to_bare(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "s.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", content=[
                _skill_use("c1", ".claude/worktrees/b1/claude:code-review"),
                _skill_use("c2", "claude:code-review"),
            ]),
        ])
        out = self._run(capsys)
        rows = [ln for ln in out.splitlines() if ln.startswith("claude:code-review")]
        assert len(rows) == 1, f"expected one collapsed row, got: {rows}"
        cols = _table_cols(out, header_contains="user-slash",
                           row_contains="claude:code-review", row_startswith=True)
        assert int(cols["top-level"]) == 2, f"both spellings should sum into one row: {cols}"
        assert "worktrees" not in out, "path fragment must not survive normalization"

    def test_plugin_prefix_preserved(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "s.jsonl", [
            _asst("claude-sonnet-4-6", branch="main",
                  content=[_skill_use("p1", "skill-management:skill-review")]),
        ])
        out = self._run(capsys)
        assert "skill-management:skill-review" in out, "plugin qualifier must be preserved"

    def test_attribution_parent_normalized_in_routed_pairs(self, fake_projects, capsys):
        """The ROUTED PAIRS parent is normalized too, so a worktree-qualified
        attributionSkill does not carry its path into output."""
        rec = _asst("claude-sonnet-4-6", branch="main",
                    content=[_skill_use("r1", "code-review")])
        rec["attributionSkill"] = ".claude/worktrees/some-branch/claude:ready-for-review"
        _write_jsonl(fake_projects / "s.jsonl", [rec])
        out = self._run(capsys)
        assert "ready-for-review -> code-review" in out, "normalized pair should appear"
        assert "worktrees" not in out, "attribution path fragment must not survive"


class TestSkillInvocationProvenance:
    """Only input['skill'] is extracted — never input['args'], which carries
    absolute paths even for an in-scope session's own record. Scoping the read to
    this repo cannot prevent this leak (the path is inside a dir that IS in
    scope), so the extract-only-skill rule is its own supporting invariant and
    needs its own regression test."""

    def test_args_value_never_appears_in_output(self, fake_projects, capsys):
        skill_with_args = {
            "type": "tool_use", "id": "a1", "name": "Skill",
            "input": {"skill": "code-review", "args": "/abs/path/secret-name"},
        }
        _write_jsonl(fake_projects / "s.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", content=[skill_with_args]),
        ])
        _mod.cmd_skill_invocation(_skill_inv_args())
        out = capsys.readouterr().out
        assert "code-review" in out, "the skill name must still be counted"
        assert "secret-name" not in out, "input['args'] value must never reach output"
        assert "/abs/path" not in out, "no fragment of input['args'] may reach output"


class TestSkillInvocationRepoScope:
    """The repo-scoped default (--projects unset) derives this repo's worktree
    slugs via git and fails closed. subprocess.run is stubbed at the module seam
    used by TestPrLink, so no real git repo is needed."""

    def _worktree_porcelain(self, *paths):
        return "\n".join(f"worktree {p}\nHEAD 0000\nbranch refs/heads/x\n" for p in paths)

    def test_scoped_default_reads_only_this_repos_dirs(self, tmp_path, monkeypatch, capsys):
        projects = tmp_path / "projects"
        mine = projects / "-repo-main"
        theirs = projects / "-other-project"
        mine.mkdir(parents=True)
        theirs.mkdir(parents=True)
        monkeypatch.setattr(_mod, "PROJECTS_DIR", projects)
        _write_jsonl(mine / "s.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", content=[_skill_use("x1", "code-review")]),
        ])
        _write_jsonl(theirs / "s.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", content=[_skill_use("x2", "plan-review")]),
        ])
        # cwd "/repo/main" maps via the real _path_to_project_slug to "-repo-main"
        # (only '/' and '.' become '-'); git lists that one worktree. Let the real
        # function run — a hand-copied lambda would freeze it against future drift.
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/main")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                return subprocess.CompletedProcess(cmd, 0, self._worktree_porcelain("/repo/main"), "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo/main\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        _mod.cmd_skill_invocation(argparse.Namespace(projects=None, branches=None, include_subagents=False))
        out = capsys.readouterr().out
        assert "code-review" in out, "in-scope skill must appear"
        assert "plan-review" not in out, "another project's skill must not appear"

    def test_fail_closed_when_git_unavailable(self, fake_projects, monkeypatch, capsys):
        """git failing must exit non-zero with NOTHING on stdout — not fall back to '*'."""
        def boom(cmd, *a, **k):
            raise FileNotFoundError("git")
        monkeypatch.setattr(subprocess, "run", boom)
        with pytest.raises(SystemExit):
            _mod.cmd_skill_invocation(argparse.Namespace(projects=None, branches=None, include_subagents=False))
        captured = capsys.readouterr()
        assert captured.out == "", "fail-closed path must print nothing to stdout"
        assert "refusing" in captured.err.lower(), "should explain the refusal on stderr"

    def test_fail_closed_when_cwd_not_in_worktrees(self, fake_projects, monkeypatch, capsys):
        """git succeeds but resolves a repo unrelated to cwd (GIT_DIR / submodule):
        the cwd-not-in-slugs guard must fail closed."""
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/somewhere/else")

        def fake_run(cmd, *a, **k):
            return subprocess.CompletedProcess(cmd, 0, self._worktree_porcelain("/a/foreign/repo"), "")
        monkeypatch.setattr(subprocess, "run", fake_run)
        with pytest.raises(SystemExit):
            _mod.cmd_skill_invocation(argparse.Namespace(projects=None, branches=None, include_subagents=False))
        assert capsys.readouterr().out == "", "fail-closed path must print nothing to stdout"

    def test_explicit_projects_never_invokes_git(self, fake_projects, monkeypatch, capsys):
        """The escape hatch: an explicit --projects must not touch the git derivation."""
        def boom(cmd, *a, **k):
            raise AssertionError("git must not be called when --projects is explicit")
        monkeypatch.setattr(subprocess, "run", boom)
        _write_jsonl(fake_projects / "s.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", content=[_skill_use("e1", "code-review")]),
        ])
        # Must not raise AssertionError from boom, and must produce output.
        _mod.cmd_skill_invocation(argparse.Namespace(projects="*", branches=None, include_subagents=False))
        assert "code-review" in capsys.readouterr().out

    def test_scope_matches_by_literal_name_not_glob(self, tmp_path, monkeypatch, capsys):
        """A derived slug is matched as a literal directory name, not a glob. A
        slug containing a glob metacharacter (from a `*`/`?`/`[` in the home or
        username path) must not widen the read to a sibling project dir the
        wildcard would otherwise match — string equality does not; Path.glob
        would."""
        projects = tmp_path / "projects"
        mine = projects / "-home-u-r*-main"       # in-scope slug carries a '*'
        theirs = projects / "-home-u-rX-main"     # a wildcard on 'mine' would match this
        mine.mkdir(parents=True)
        theirs.mkdir(parents=True)
        monkeypatch.setattr(_mod, "PROJECTS_DIR", projects)
        _write_jsonl(mine / "s.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", content=[_skill_use("y1", "code-review")]),
        ])
        _write_jsonl(theirs / "s.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", content=[_skill_use("y2", "plan-review")]),
        ])
        # The real _path_to_project_slug maps "/home/u/r*/main" -> "-home-u-r*-main"
        # ('/' and '.' -> '-'; the '*' is preserved), so no monkeypatch is needed —
        # letting it run is what makes this a real test of the '*' surviving into a
        # slug and still being matched literally.
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/home/u/r*/main")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                return subprocess.CompletedProcess(cmd, 0, self._worktree_porcelain("/home/u/r*/main"), "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/home/u/r*/main\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        _mod.cmd_skill_invocation(argparse.Namespace(projects=None, branches=None, include_subagents=False))
        out = capsys.readouterr().out
        assert "code-review" in out, "the exact-name dir must be read"
        assert "plan-review" not in out, "a glob metacharacter must not widen the match to a sibling dir"

    def test_multiple_worktrees_all_in_scope(self, tmp_path, monkeypatch, capsys):
        """git worktree list returns main + a linked worktree, and cwd is the
        *linked* one (a non-first entry). Both of this repo's worktree project dirs
        must contribute — not just the first — which a first-match-only bug in the
        slug list would break."""
        projects = tmp_path / "projects"
        main_dir = projects / "-repo"                        # slug of /repo
        linked_dir = projects / "-repo--claude-worktrees-feat"  # slug of the linked worktree
        main_dir.mkdir(parents=True)
        linked_dir.mkdir(parents=True)
        monkeypatch.setattr(_mod, "PROJECTS_DIR", projects)
        _write_jsonl(main_dir / "s.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", content=[_skill_use("m1", "code-review")]),
        ])
        _write_jsonl(linked_dir / "s.jsonl", [
            _asst("claude-sonnet-4-6", branch="feat", content=[_skill_use("l1", "handoff")]),
        ])
        # cwd is the linked worktree (the second git entry), not the main one.
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/.claude/worktrees/feat")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                return subprocess.CompletedProcess(
                    cmd, 0,
                    self._worktree_porcelain("/repo", "/repo/.claude/worktrees/feat"), "",
                )
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo/.claude/worktrees/feat\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        _mod.cmd_skill_invocation(argparse.Namespace(projects=None, branches=None, include_subagents=False))
        out = capsys.readouterr().out
        assert "code-review" in out, "the main worktree's skill must appear"
        assert "handoff" in out, "the linked worktree's skill must appear"


class TestRepoScopedProjectSlugsGuard:
    """_repo_scoped_project_slugs' three fail-closed sys.exit sites and the
    row-20 containment-plus-identity cwd guard, exercised via a non-
    skill-invocation caller label to cover the newly-generic path."""

    def _worktree_porcelain(self, *paths):
        return "\n".join(f"worktree {p}\nHEAD 0000\nbranch refs/heads/x\n" for p in paths)

    def test_fails_closed_when_worktree_list_command_fails(self, monkeypatch, capsys):
        def boom(cmd, *a, **k):
            raise FileNotFoundError("git")
        monkeypatch.setattr(subprocess, "run", boom)
        with pytest.raises(SystemExit):
            _mod._repo_scoped_project_slugs("buckets")
        assert "buckets:" in capsys.readouterr().err

    def test_fails_closed_when_worktree_list_returns_no_worktrees(self, monkeypatch, capsys):
        def fake_run(cmd, *a, **k):
            return subprocess.CompletedProcess(cmd, 0, "", "")
        monkeypatch.setattr(subprocess, "run", fake_run)
        with pytest.raises(SystemExit):
            _mod._repo_scoped_project_slugs("buckets")
        assert "buckets:" in capsys.readouterr().err

    def test_fails_closed_when_cwd_outside_all_worktrees(self, monkeypatch, capsys):
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/somewhere/else")

        def fake_run(cmd, *a, **k):
            return subprocess.CompletedProcess(cmd, 0, self._worktree_porcelain("/repo"), "")
        monkeypatch.setattr(subprocess, "run", fake_run)
        with pytest.raises(SystemExit):
            _mod._repo_scoped_project_slugs("duration")
        assert "duration:" in capsys.readouterr().err

    def test_cwd_inside_worktree_subdirectory_resolves_successfully(self, monkeypatch):
        """Containment (not slug equality) lets --this-repo run from a subdirectory
        of the worktree root."""
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/nested/dir")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                return subprocess.CompletedProcess(cmd, 0, self._worktree_porcelain("/repo"), "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        slugs = _mod._repo_scoped_project_slugs("duration")
        assert slugs == [_mod._path_to_project_slug("/repo")]

    def test_sibling_repo_sharing_string_prefix_is_rejected(self, monkeypatch, capsys):
        """cwd inside a sibling path sharing the same string prefix (<repo>-fork)
        must exit 1 — the case bare str.startswith containment would wrongly
        accept."""
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo-fork/nested")

        def fake_run(cmd, *a, **k):
            return subprocess.CompletedProcess(cmd, 0, self._worktree_porcelain("/repo"), "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit):
            _mod._repo_scoped_project_slugs("duration")
        assert capsys.readouterr().out == ""

    def test_fails_closed_when_cwd_repo_root_diverges_from_worktree_list(self, monkeypatch, capsys):
        """Identity guard: cwd sits under a listed worktree (containment passes)
        but `git rev-parse --show-toplevel` reports a different root — the case
        an environment override like GIT_WORK_TREE could produce by decoupling
        toplevel reporting from the worktree-list enumeration. Every other test
        in this class returns the same path from both git calls, so this is the
        only test that would catch a typo bug in the identity check itself."""
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/nested")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                return subprocess.CompletedProcess(cmd, 0, self._worktree_porcelain("/repo"), "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/outer\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit):
            _mod._repo_scoped_project_slugs("duration")
        assert "repo root is not among the resolved worktrees" in capsys.readouterr().err

    def test_fails_closed_when_rev_parse_toplevel_command_fails(self, monkeypatch, capsys):
        """The identity-check subprocess call erroring must fail closed, not
        fall back to a machine-wide scope, even though worktree list succeeded."""
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/nested")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                return subprocess.CompletedProcess(cmd, 0, self._worktree_porcelain("/repo"), "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            raise FileNotFoundError("git")
        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit):
            _mod._repo_scoped_project_slugs("duration")
        assert "duration:" in capsys.readouterr().err


class TestResolveProjectScope:
    """The scope-dispatch helper behind --projects/--this-repo on the 15
    non-skill-invocation subcommands."""

    def _worktree_porcelain(self, *paths):
        return "\n".join(f"worktree {p}\nHEAD 0000\nbranch refs/heads/x\n" for p in paths)

    def test_this_repo_unset_raises_rather_than_defaulting_to_machine_wide(self, fake_projects):
        """A subparser wired without _add_project_scope_args (missing this_repo
        entirely) must raise, not silently fall through to '*' scope."""
        args = type("A", (), {"projects": "*"})()  # no this_repo attribute at all
        with pytest.raises(AttributeError):
            _mod._resolve_project_scope(args, "buckets")

    def test_called_twice_from_one_args_yields_same_session_list_both_times(self, tmp_path, monkeypatch):
        """cmd_audit_routing's redact pass and main pass both call the scope helper
        on the same args object — a shared/exhausted generator would make the
        second pass silently empty."""
        projects = tmp_path / "projects"
        mine = projects / "-repo-main"
        mine.mkdir(parents=True)
        monkeypatch.setattr(_mod, "PROJECTS_DIR", projects)
        _write_jsonl(mine / "s.jsonl", [_asst("claude-sonnet-4-6", branch="main")])
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/main")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                return subprocess.CompletedProcess(cmd, 0, self._worktree_porcelain("/repo/main"), "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo/main\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        args = argparse.Namespace(this_repo=True, projects="*")
        iter1, label1 = _mod._resolve_project_scope(args, "audit-routing")
        iter2, label2 = _mod._resolve_project_scope(args, "audit-routing")
        sessions1 = list(iter1)
        sessions2 = list(iter2)
        assert len(sessions1) == 1
        assert len(sessions2) == 1
        assert label1 == label2

    def test_resolved_scope_header_renders_under_this_repo_and_under_a_glob(self, tmp_path, monkeypatch, capsys):
        """The resolved-scope header prints for a non-skill-invocation subcommand
        both under --this-repo and under the default machine-wide glob, so no
        output is scope-ambiguous."""
        projects = tmp_path / "projects"
        mine = projects / "-repo-main"
        mine.mkdir(parents=True)
        monkeypatch.setattr(_mod, "PROJECTS_DIR", projects)
        _write_jsonl(mine / "s.jsonl", [_asst("claude-sonnet-4-6", branch="feat")])
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/main")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                return subprocess.CompletedProcess(cmd, 0, self._worktree_porcelain("/repo/main"), "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo/main\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        _mod.cmd_buckets(type("A", (), {"projects": "*", "this_repo": True, "branches": None})())
        out_repo = capsys.readouterr().out
        assert "BUCKETS SOURCES (this repo (1 project dirs))" in out_repo

        _mod.cmd_buckets(type("A", (), {"projects": "*", "this_repo": False, "branches": None})())
        out_glob = capsys.readouterr().out
        assert "BUCKETS SOURCES (*)" in out_glob


class TestBuildParser:
    """build_parser() as a testable seam — the argparse layer without executing
    a subcommand against the real ~/.claude/projects."""

    def test_this_repo_and_projects_mutually_exclusive_on_generic_subcommand(self):
        parser = _mod.build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["buckets", "--this-repo", "--projects", "x"])

    def test_this_repo_and_projects_mutually_exclusive_on_skill_invocation(self):
        parser = _mod.build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["skill-invocation", "--this-repo", "--projects", "x"])

    def test_this_repo_alone_parses_without_error(self):
        parser = _mod.build_parser()
        parsed = parser.parse_args(["buckets", "--this-repo"])
        assert parsed.projects == "*"
        assert parsed.this_repo is True


class TestIterSessionsOrdering:
    """iter_sessions must yield in a single flat sort over full file paths, NOT
    grouped by directory. cmd_audit_routing's redact-label first pass assigns
    Project-N labels by first-seen order, and this repo's own worktree dirs
    (-home-u-repo vs -home-u-repo--claude-worktrees-b) are the prefix-colliding
    pair whose flat-sort vs dir-grouped order differs."""

    def test_flat_path_order_across_prefix_colliding_dirs(self, tmp_path):
        projects = tmp_path / "projects"
        main_dir = projects / "-home-u-repo"
        wt_dir = projects / "-home-u-repo--claude-worktrees-b"
        main_dir.mkdir(parents=True)
        wt_dir.mkdir(parents=True)
        _write_jsonl(main_dir / "a.jsonl", [_asst("claude-sonnet-4-6", branch="main")])
        _write_jsonl(wt_dir / "m.jsonl", [_asst("claude-sonnet-4-6", branch="b")])
        yielded = [jsonl for jsonl, _records in _mod.iter_sessions(projects, "*")]
        # The flat full-path sort places the worktree dir's file first ('-' 0x2D <
        # '/' 0x2F at the byte after the shared prefix); a dir-grouped traversal
        # would place the main dir first. Pin the flat order explicitly.
        expected = sorted(projects.glob("*/*.jsonl"))
        assert yielded == expected, (
            f"iter_sessions must yield in flat full-path sort order; got "
            f"{[p.parent.name + '/' + p.name for p in yielded]}"
        )


class TestPathToProjectSlug:
    """The slug transform and its known, accepted lossiness."""

    def test_main_repo_path(self):
        assert _mod._path_to_project_slug("/home/u/repo") == "-home-u-repo"

    def test_worktree_path(self):
        assert (_mod._path_to_project_slug("/home/u/repo/.claude/worktrees/b")
                == "-home-u-repo--claude-worktrees-b")

    def test_known_slug_collision_is_accepted(self):
        """`/` and `.` both map to `-`, so distinct paths can collapse to one slug.
        This is Claude Code's own dir-naming scheme, not ours to change; the repo
        scoping accepts this residual (see _repo_scoped_project_slugs). Pinned so a
        later refactor cannot silently assume injectivity."""
        assert _mod._path_to_project_slug("/home/u/a/b") == _mod._path_to_project_slug("/home/u/a.b")


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
    return type("A", (), {"projects": projects, "this_repo": False, "branches": branches})()


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
        assert any("main" in ln for ln in out.splitlines()), "expected a main row in output"
        assert any("sidechain" in ln for ln in out.splitlines()), "expected a sidechain row in output"
        # Verify actual counts: 1 opus main turn, 1 sonnet sidechain turn.
        # main row: Branch label present → drop_leading_labels=0
        main_cols = _table_cols(out, header_contains="Thread", row_contains="main")
        assert main_cols["Opus"] == "1", "expected 1 opus main turn"
        assert main_cols["Sonnet"] == "0", "expected 0 sonnet main turns"
        # sidechain row: Branch label absent on second row → drop_leading_labels=1
        sidechain_cols = _table_cols(out, header_contains="Thread", row_contains="sidechain",
                                     drop_leading_labels=1)
        assert sidechain_cols["Opus"] == "0", "expected 0 opus sidechain turns"
        assert sidechain_cols["Sonnet"] == "1", "expected 1 sonnet sidechain turn"

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
        cols = _table_cols(out, header_contains="Lead", row_contains="2026-W20")
        assert cols["Lead"] == "1"
        assert cols["Main"] == "0"
        assert cols["Side"] == "1"


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
    this_repo: bool = False,
    branches: str | None = None,
    since: str | None = None,
    until: str | None = None,
    skills: str | None = None,
    truncate_chars: int = 1000,
    out: str | None = None,
) -> object:
    return type("A", (), {
        "projects": projects,
        "this_repo": this_repo,
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

    def test_branches_filter_selects_by_invocation_branch_across_a_branch_change(self, fake_projects, capsys):
        """--branches filters on the invocation record's own gitBranch, not a single
        session-wide branch — a session whose branch changes between the invocation
        and the user response is selected by where the invocation itself happened."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("code-review", "2026-05-20T10:00:00.000Z", branch="feat"),
            _review_asst("Review while still on feat.", "2026-05-20T10:01:00.000Z", branch="feat"),
            _user_reply("Reply after switching to main.", branch="main"),
        ])
        _mod.cmd_judgment_pair(_judgment_pair_args(branches="feat"))
        out_feat = capsys.readouterr().out
        assert "Review while still on feat." in out_feat

        _mod.cmd_judgment_pair(_judgment_pair_args(branches="main"))
        out_main = capsys.readouterr().out
        assert "No judgment pairs found." in out_main

    def test_out_path_writes_to_file(self, fake_projects, capsys, tmp_path):
        """--out writes the pair block to the specified file instead of stdout,
        with the resolved-scope header prepended so the file stays
        self-documenting about its scope even pasted elsewhere without the
        original terminal output."""
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
        assert file_content.splitlines()[0] == "JUDGMENT PAIR SOURCES (*)"
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


# ---------------------------------------------------------------------------
# friction-count
# ---------------------------------------------------------------------------


def _friction_count_args(
    transcript: str, *, json_output: bool = False, checkpoint: str | None = None
) -> object:
    return type("A", (), {"transcript": transcript, "json": json_output, "checkpoint": checkpoint})()


class TestFrictionCount:
    def test_legacy_denial_shape_counted(self, fake_projects, capsys):
        """A legacy attachment/hook_blocking_error record counts as one denial."""
        path = fake_projects / "sess.jsonl"
        _write_jsonl(path, [_hook_deny("require-code-review")])
        _mod.cmd_friction_count(_friction_count_args(str(path)))
        out = capsys.readouterr().out.strip()
        assert out == "1"

    def test_current_format_denial_shape_counted(self, fake_projects, capsys):
        """A current-format is_error tool_result with the denial signature counts as one denial."""
        path = fake_projects / "sess.jsonl"
        _write_jsonl(path, [_hook_deny_current("Commit blocked by code-review gate.")])
        _mod.cmd_friction_count(_friction_count_args(str(path)))
        out = capsys.readouterr().out.strip()
        assert out == "1"

    def test_denial_deduped_across_shapes_by_tool_use_id(self, fake_projects, capsys):
        """A denial recorded as both an attachment and its is_error twin (same
        tool_use_id) collapses to one denial event — mirrors cmd_review_trace's dedup."""
        path = fake_projects / "sess.jsonl"
        attach = _hook_deny("worktree")  # toolUseID == "toolu_worktree"
        twin = _hook_deny_current(
            "Blocked by worktree-enforcement hook: 'git add' not allowed.",
            tool_id="toolu_worktree",
        )
        _write_jsonl(path, [attach, twin])
        _mod.cmd_friction_count(_friction_count_args(str(path), json_output=True))
        signals = json.loads(capsys.readouterr().out)
        assert signals["denials"] == 1

    def test_distinct_denials_each_counted(self, fake_projects, capsys):
        """Two current-format denials with distinct tool_use_ids count as two denials."""
        path = fake_projects / "sess.jsonl"
        _write_jsonl(path, [
            _hook_deny_current("Commit blocked by code-review gate.", tool_id="toolu_a"),
            _hook_deny_current("Push blocked by ready-for-review gate.", tool_id="toolu_b"),
        ])
        _mod.cmd_friction_count(_friction_count_args(str(path), json_output=True))
        signals = json.loads(capsys.readouterr().out)
        assert signals["denials"] == 2

    def test_counts_all_branches_regardless_of_gitBranch(self, fake_projects, capsys):
        """friction-count is flat per-file — struggle signals on two different branches
        in the same transcript both count; this is intentional (no branch filter)."""
        path = fake_projects / "sess.jsonl"
        _write_jsonl(path, [
            _asst("claude-sonnet-4-6", branch="feat-a"),
            _user_msg([{"type": "text", "text": "hold on, that's wrong"}], branch="feat-a"),
            _asst("claude-sonnet-4-6", branch="feat-b"),
            _user_msg([{"type": "text", "text": "try again please"}], branch="feat-b"),
        ])
        _mod.cmd_friction_count(_friction_count_args(str(path), json_output=True))
        signals = json.loads(capsys.readouterr().out)
        assert signals["struggle_turns"] == 2

    def test_failed_test_pairing_counts_only_failing_runs(self, fake_projects, capsys):
        """A failing run (N failed > 0) counts; a passing run does not."""
        path = fake_projects / "sess.jsonl"
        _write_jsonl(path, [
            _asst("claude-sonnet-4-6", branch="feat", content=[_bash_use("t1", "pytest")]),
            _user_msg([_tool_result("t1", "3 failed, 17 passed")], branch="feat"),
            _asst("claude-sonnet-4-6", branch="feat", content=[_bash_use("t2", "pytest")]),
            _user_msg([_tool_result("t2", "all passed")], branch="feat"),
        ])
        _mod.cmd_friction_count(_friction_count_args(str(path), json_output=True))
        signals = json.loads(capsys.readouterr().out)
        assert signals["failed_test_runs"] == 1

    def test_struggle_phrase_match_counted(self, fake_projects, capsys):
        """A user turn containing a STRUGGLE_PHRASES entry counts as one struggle turn."""
        path = fake_projects / "sess.jsonl"
        _write_jsonl(path, [
            _asst("claude-sonnet-4-6", branch="feat"),
            _user_msg([{"type": "text", "text": "no not that, try again"}], branch="feat"),
        ])
        _mod.cmd_friction_count(_friction_count_args(str(path), json_output=True))
        signals = json.loads(capsys.readouterr().out)
        assert signals["struggle_turns"] == 1

    def test_isSidechain_records_skipped(self, fake_projects, capsys):
        """isSidechain denial/failed-test/struggle records are excluded from every signal."""
        path = fake_projects / "sess.jsonl"
        _write_jsonl(path, [
            _asst("claude-sonnet-4-6", branch="feat", sidechain=True,
                  content=[_bash_use("t1", "pytest")]),
            {
                **_user_msg([_tool_result("t1", "3 failed")], branch="feat"),
                "isSidechain": True,
            },
            {
                **_user_msg([{"type": "text", "text": "hold on, try again"}], branch="feat"),
                "isSidechain": True,
            },
        ])
        _mod.cmd_friction_count(_friction_count_args(str(path), json_output=True))
        signals = json.loads(capsys.readouterr().out)
        assert signals == {"denials": 0, "failed_test_runs": 0, "struggle_turns": 0, "composite": 0}

    def test_empty_transcript_is_zero(self, fake_projects, capsys):
        """An empty transcript file produces a composite of 0, no crash."""
        path = fake_projects / "sess.jsonl"
        path.write_text("")
        _mod.cmd_friction_count(_friction_count_args(str(path)))
        out = capsys.readouterr().out.strip()
        assert out == "0"

    def test_malformed_line_skipped_gracefully(self, fake_projects, capsys):
        """A malformed JSONL line is silently skipped; valid lines around it still count."""
        path = fake_projects / "sess.jsonl"
        good_denial = json.dumps(_hook_deny_current("Commit blocked by code-review gate."))
        path.write_text(f"{good_denial}\nnot json at all\n{{broken\n")
        _mod.cmd_friction_count(_friction_count_args(str(path)))
        out = capsys.readouterr().out.strip()
        assert out == "1"

    def test_json_breakdown_and_composite_equals_sum(self, fake_projects, capsys):
        """--json emits all four keys; composite equals the sum of the three signals."""
        path = fake_projects / "sess.jsonl"
        _write_jsonl(path, [
            _hook_deny("require-code-review"),
            _asst("claude-sonnet-4-6", branch="feat", content=[_bash_use("t1", "pytest")]),
            _user_msg([_tool_result("t1", "2 failed")], branch="feat"),
            _user_msg([{"type": "text", "text": "still failing, hold on"}], branch="feat"),
        ])
        _mod.cmd_friction_count(_friction_count_args(str(path), json_output=True))
        signals = json.loads(capsys.readouterr().out)
        assert set(signals.keys()) == {"denials", "failed_test_runs", "struggle_turns", "composite"}
        assert signals["denials"] == 1
        assert signals["failed_test_runs"] == 1
        assert signals["struggle_turns"] == 1
        assert signals["composite"] == 3
        assert signals["composite"] == (
            signals["denials"] + signals["failed_test_runs"] + signals["struggle_turns"]
        )


# ---------------------------------------------------------------------------
# friction-count cross-path equality — pins hook_denial_key and the failed-test
# signal against the two subcommands they must never silently drift from.
# ---------------------------------------------------------------------------


class TestFrictionCountCrossPathEquality:
    def test_denial_count_matches_review_trace(self, fake_projects, capsys):
        """friction-count's denial count over one file equals cmd_review_trace's denial
        count over that same session. No isSidechain denial records in this fixture,
        so cmd_review_trace's (unfiltered) and friction-count's (isSidechain-filtered)
        counts are directly comparable."""
        path = fake_projects / "sess.jsonl"
        _write_jsonl(path, [
            _hook_deny("require-code-review"),
            _hook_deny_current("Push blocked by ready-for-review gate.", tool_id="toolu_b"),
            _asst("claude-opus-4-7", branch="feat", ts="2026-05-19T10:00:00.000Z",
                  content=[_agent_use("a1", "staff-backend-engineer")]),
        ])
        _mod.cmd_review_trace(_review_trace_args())
        trace_out = capsys.readouterr().out
        m = re.search(r"denials=(\d+)", trace_out)
        assert m is not None, f"no denials= marker found in review-trace output: {trace_out!r}"
        review_trace_denials = int(m.group(1))
        assert review_trace_denials == 2

        _mod.cmd_friction_count(_friction_count_args(str(path), json_output=True))
        signals = json.loads(capsys.readouterr().out)
        assert signals["denials"] == review_trace_denials

    def test_failed_test_count_matches_fail_seq_failing_subtotal(self, fake_projects, capsys):
        """friction-count's failed-test count equals cmd_fail_seq's failing-run
        subtotal (f > 0), not its total matched-run count — cmd_fail_seq records
        every matched run including passing ones."""
        path = fake_projects / "sess.jsonl"
        _write_jsonl(path, [
            _asst("claude-sonnet-4-6", branch="feat", content=[_bash_use("t1", "pytest")]),
            _user_msg([_tool_result("t1", "3 failed, 17 passed")], branch="feat"),
            _asst("claude-sonnet-4-6", branch="feat", content=[_bash_use("t2", "pytest")]),
            _user_msg([_tool_result("t2", "all passed")], branch="feat"),
            _asst("claude-sonnet-4-6", branch="feat", content=[_bash_use("t3", "pytest")]),
            _user_msg([_tool_result("t3", "1 failed, 19 passed")], branch="feat"),
        ])
        fail_seq_args = type("A", (), {"branches": "feat", "projects": "*", "this_repo": False})()
        _mod.cmd_fail_seq(fail_seq_args)
        fail_seq_out = capsys.readouterr().out
        m = re.search(r"Total runs: (\d+)\s+Failing: (\d+)", fail_seq_out)
        assert m is not None, f"no Total runs/Failing marker found in fail-seq output: {fail_seq_out!r}"
        total_runs, failing_subtotal = int(m.group(1)), int(m.group(2))
        assert total_runs == 3
        assert failing_subtotal == 2

        _mod.cmd_friction_count(_friction_count_args(str(path), json_output=True))
        signals = json.loads(capsys.readouterr().out)
        assert signals["failed_test_runs"] == failing_subtotal
        assert signals["failed_test_runs"] != total_runs


# ---------------------------------------------------------------------------
# friction-count --checkpoint — incremental byte-offset scan.
# ---------------------------------------------------------------------------


class TestFrictionCountCheckpoint:
    def test_first_call_with_no_checkpoint_scans_from_zero_and_creates_file(self, fake_projects, capsys):
        """No checkpoint file yet: full scan from offset 0, and a checkpoint
        file is written afterward recording the offset and per-signal totals."""
        path = fake_projects / "sess.jsonl"
        checkpoint = fake_projects / "checkpoint.json"
        _write_jsonl(path, [
            _hook_deny_current("Commit blocked by code-review gate.", tool_id="toolu_a"),
        ])
        assert not checkpoint.exists()

        _mod.cmd_friction_count(_friction_count_args(str(path), checkpoint=str(checkpoint)))
        out = capsys.readouterr().out.strip()
        assert out == "1"

        assert checkpoint.exists()
        state = json.loads(checkpoint.read_text())
        assert state["offset"] == path.stat().st_size
        assert state["totals"] == {"denials": 1, "failed_test_runs": 0, "struggle_turns": 0}

    def test_incremental_call_matches_full_rescan_baseline(self, fake_projects, capsys):
        """A second checkpointed call after appending new lines re-scans only
        the appended bytes and returns the cumulative composite — asserted
        against a full-rescan baseline (no --checkpoint) over the same final
        file, to catch incremental/full-scan drift."""
        path = fake_projects / "sess.jsonl"
        checkpoint = fake_projects / "checkpoint.json"
        _write_jsonl(path, [
            _hook_deny_current("Commit blocked by code-review gate.", tool_id="toolu_a"),
            _asst("claude-sonnet-4-6", branch="feat", content=[_bash_use("t1", "pytest")]),
            _user_msg([_tool_result("t1", "3 failed, 17 passed")], branch="feat"),
        ])
        _mod.cmd_friction_count(_friction_count_args(str(path), checkpoint=str(checkpoint)))
        first_composite = int(capsys.readouterr().out.strip())
        assert first_composite == 2  # 1 denial + 1 failed test run

        with path.open("a") as fh:
            for rec in (
                _hook_deny_current("Push blocked by ready-for-review gate.", tool_id="toolu_b"),
                _user_msg([{"type": "text", "text": "hold on, try again"}], branch="feat"),
            ):
                fh.write(json.dumps(rec) + "\n")

        _mod.cmd_friction_count(_friction_count_args(str(path), checkpoint=str(checkpoint)))
        incremental_composite = int(capsys.readouterr().out.strip())
        assert incremental_composite == 4  # 2 denials + 1 failed test run + 1 struggle turn
        # Non-vacuous: the incremental call actually counted the newly appended signals.
        assert incremental_composite != first_composite

        _mod.cmd_friction_count(_friction_count_args(str(path)))  # no checkpoint: full rescan
        baseline_composite = int(capsys.readouterr().out.strip())
        assert incremental_composite == baseline_composite

    def test_malformed_checkpoint_json_falls_back_to_full_rescan(self, fake_projects, capsys):
        """A checkpoint file that isn't valid JSON is treated as absent: full
        rescan from offset 0, no error raised."""
        path = fake_projects / "sess.jsonl"
        checkpoint = fake_projects / "checkpoint.json"
        checkpoint.write_text("not json at all")
        _write_jsonl(path, [
            _hook_deny_current("Commit blocked by code-review gate.", tool_id="toolu_a"),
        ])

        _mod.cmd_friction_count(_friction_count_args(str(path), checkpoint=str(checkpoint)))
        out = capsys.readouterr().out.strip()
        assert out == "1"

    def test_checkpoint_missing_totals_field_falls_back_to_full_rescan(self, fake_projects, capsys):
        """A checkpoint file missing the 'totals' key is treated as malformed
        as a whole (not partially trusted): full rescan from offset 0, not a
        scan starting from the recorded offset with zero totals."""
        path = fake_projects / "sess.jsonl"
        checkpoint = fake_projects / "checkpoint.json"
        checkpoint.write_text(json.dumps({"offset": 5}))  # no "totals" key
        _write_jsonl(path, [
            _hook_deny_current("Commit blocked by code-review gate.", tool_id="toolu_a"),
        ])

        _mod.cmd_friction_count(_friction_count_args(str(path), checkpoint=str(checkpoint)))
        out = capsys.readouterr().out.strip()
        assert out == "1"

    def test_checkpoint_negative_offset_falls_back_to_full_rescan(self, fake_projects, capsys):
        """A negative offset is malformed: full rescan from offset 0."""
        path = fake_projects / "sess.jsonl"
        checkpoint = fake_projects / "checkpoint.json"
        checkpoint.write_text(json.dumps({
            "offset": -1,
            "totals": {"denials": 0, "failed_test_runs": 0, "struggle_turns": 0},
        }))
        _write_jsonl(path, [
            _hook_deny_current("Commit blocked by code-review gate.", tool_id="toolu_a"),
        ])

        _mod.cmd_friction_count(_friction_count_args(str(path), checkpoint=str(checkpoint)))
        out = capsys.readouterr().out.strip()
        assert out == "1"

    def test_checkpoint_non_int_offset_falls_back_to_full_rescan(self, fake_projects, capsys):
        """A string offset is malformed: full rescan from offset 0."""
        path = fake_projects / "sess.jsonl"
        checkpoint = fake_projects / "checkpoint.json"
        checkpoint.write_text(json.dumps({
            "offset": "5",
            "totals": {"denials": 0, "failed_test_runs": 0, "struggle_turns": 0},
        }))
        _write_jsonl(path, [
            _hook_deny_current("Commit blocked by code-review gate.", tool_id="toolu_a"),
        ])

        _mod.cmd_friction_count(_friction_count_args(str(path), checkpoint=str(checkpoint)))
        out = capsys.readouterr().out.strip()
        assert out == "1"

    def test_checkpoint_bool_offset_falls_back_to_full_rescan(self, fake_projects, capsys):
        """Python's bool subclasses int (isinstance(True, int) is True); a
        checkpoint with offset: true must still be rejected as malformed,
        not silently treated as offset 1."""
        path = fake_projects / "sess.jsonl"
        checkpoint = fake_projects / "checkpoint.json"
        checkpoint.write_text(json.dumps({
            "offset": True,
            "totals": {"denials": 0, "failed_test_runs": 0, "struggle_turns": 0},
        }))
        _write_jsonl(path, [
            _hook_deny_current("Commit blocked by code-review gate.", tool_id="toolu_a"),
        ])

        _mod.cmd_friction_count(_friction_count_args(str(path), checkpoint=str(checkpoint)))
        out = capsys.readouterr().out.strip()
        assert out == "1"

    def test_checkpoint_negative_totals_value_falls_back_to_full_rescan(self, fake_projects, capsys):
        """A negative per-key totals value is malformed: full rescan from offset 0."""
        path = fake_projects / "sess.jsonl"
        checkpoint = fake_projects / "checkpoint.json"
        checkpoint.write_text(json.dumps({
            "offset": 5,
            "totals": {"denials": -1, "failed_test_runs": 0, "struggle_turns": 0},
        }))
        _write_jsonl(path, [
            _hook_deny_current("Commit blocked by code-review gate.", tool_id="toolu_a"),
        ])

        _mod.cmd_friction_count(_friction_count_args(str(path), checkpoint=str(checkpoint)))
        out = capsys.readouterr().out.strip()
        assert out == "1"

    def test_checkpoint_non_int_totals_value_falls_back_to_full_rescan(self, fake_projects, capsys):
        """A string per-key totals value is malformed: full rescan from offset 0."""
        path = fake_projects / "sess.jsonl"
        checkpoint = fake_projects / "checkpoint.json"
        checkpoint.write_text(json.dumps({
            "offset": 5,
            "totals": {"denials": "1", "failed_test_runs": 0, "struggle_turns": 0},
        }))
        _write_jsonl(path, [
            _hook_deny_current("Commit blocked by code-review gate.", tool_id="toolu_a"),
        ])

        _mod.cmd_friction_count(_friction_count_args(str(path), checkpoint=str(checkpoint)))
        out = capsys.readouterr().out.strip()
        assert out == "1"

    def test_checkpoint_totals_missing_one_of_three_keys_falls_back_to_full_rescan(
        self, fake_projects, capsys
    ):
        """A 'totals' dict present but missing exactly one of the three
        required keys (struggle_turns here) is malformed as a whole: full
        rescan from offset 0, not a partial trust of the two present keys."""
        path = fake_projects / "sess.jsonl"
        checkpoint = fake_projects / "checkpoint.json"
        checkpoint.write_text(json.dumps({
            "offset": 5,
            "totals": {"denials": 1, "failed_test_runs": 2},  # no "struggle_turns"
        }))
        _write_jsonl(path, [
            _hook_deny_current("Commit blocked by code-review gate.", tool_id="toolu_a"),
        ])

        _mod.cmd_friction_count(_friction_count_args(str(path), checkpoint=str(checkpoint)))
        out = capsys.readouterr().out.strip()
        assert out == "1"

    def test_checkpoint_offset_beyond_transcript_size_falls_back_to_full_rescan(self, fake_projects, capsys):
        """A checkpoint whose stored offset exceeds the transcript's current
        size (e.g. the transcript was truncated/rewritten while the
        session-keyed checkpoint persisted) must not seek past EOF and
        freeze forever at the checkpoint's stale totals — it must fail open
        to a full rescan, exactly like a malformed checkpoint."""
        path = fake_projects / "sess.jsonl"
        checkpoint = fake_projects / "checkpoint.json"
        _write_jsonl(path, [
            _hook_deny_current("Commit blocked by code-review gate.", tool_id="toolu_a"),
        ])
        # A well-formed checkpoint whose offset is larger than the actual
        # transcript file's current byte size.
        checkpoint.write_text(json.dumps({
            "offset": path.stat().st_size + 1000,
            "totals": {"denials": 7, "failed_test_runs": 0, "struggle_turns": 0},
        }))

        _mod.cmd_friction_count(_friction_count_args(str(path), checkpoint=str(checkpoint)))
        out = capsys.readouterr().out.strip()
        assert out == "1", "a stale offset must trigger a full rescan, not a frozen stale composite"

    def test_checkpoint_offset_never_rereads_consumed_bytes(self, fake_projects, capsys):
        """Calling friction-count again with no new appended lines must not
        double-count the already-consumed denial — this is only true if the
        checkpoint's stored offset actually gates re-reading those bytes."""
        path = fake_projects / "sess.jsonl"
        checkpoint = fake_projects / "checkpoint.json"
        _write_jsonl(path, [
            _hook_deny_current("Commit blocked by code-review gate.", tool_id="toolu_a"),
        ])
        _mod.cmd_friction_count(_friction_count_args(str(path), checkpoint=str(checkpoint)))
        assert capsys.readouterr().out.strip() == "1"

        # No new lines appended — a second call over the unchanged file must
        # return the same cumulative composite, not double it.
        _mod.cmd_friction_count(_friction_count_args(str(path), checkpoint=str(checkpoint)))
        second_out = capsys.readouterr().out.strip()
        assert second_out == "1", "re-reading already-consumed bytes would double-count the denial"

    def test_partial_trailing_line_not_consumed_until_terminated(self, fake_projects, capsys):
        """A transcript with a trailing unterminated line (as if the harness is
        actively writing it) only advances the checkpoint offset to the last
        complete line boundary. The partial line's content is picked up whole,
        exactly once, on the next call once it's terminated — not dropped, not
        double-read."""
        path = fake_projects / "sess.jsonl"
        checkpoint = fake_projects / "checkpoint.json"
        complete_denial = json.dumps(
            _hook_deny_current("Commit blocked by code-review gate.", tool_id="toolu_a")
        )
        partial_denial = json.dumps(
            _hook_deny_current("Push blocked by ready-for-review gate.", tool_id="toolu_b")
        )
        # The second record's bytes have no trailing newline, as if the
        # transcript writer stopped mid-record.
        path.write_text(f"{complete_denial}\n{partial_denial[:20]}")

        _mod.cmd_friction_count(_friction_count_args(str(path), checkpoint=str(checkpoint)))
        first_out = capsys.readouterr().out.strip()
        assert first_out == "1", "only the complete first line should be counted"
        state = json.loads(checkpoint.read_text())
        assert state["offset"] == len(complete_denial) + 1, (
            "offset must stop at the complete-line boundary, not consume the partial trailing bytes"
        )

        # Complete and terminate the partial line, simulating the writer
        # finishing the record on the next turn.
        with path.open("a") as fh:
            fh.write(partial_denial[20:] + "\n")

        _mod.cmd_friction_count(_friction_count_args(str(path), checkpoint=str(checkpoint)))
        second_out = capsys.readouterr().out.strip()
        assert second_out == "2", "the now-terminated second denial is picked up whole, not double-read"

    def test_checkpoint_and_json_combined_returns_same_four_keys_as_non_checkpoint_json(
        self, fake_projects, capsys
    ):
        """--checkpoint and --json together (a real, reachable CLI combination
        the hook itself never exercises) must build the same four-key shape
        as the non-checkpoint --json path — this dict is constructed
        differently (**running_totals spread) than the non-checkpoint path's
        _friction_signals return, so a key-naming/ordering regression here
        would not be caught by any non-checkpoint --json test."""
        path = fake_projects / "sess.jsonl"
        checkpoint = fake_projects / "checkpoint.json"
        _write_jsonl(path, [
            _hook_deny_current("Commit blocked by code-review gate.", tool_id="toolu_a"),
        ])

        _mod.cmd_friction_count(
            _friction_count_args(str(path), checkpoint=str(checkpoint), json_output=True)
        )
        checkpoint_signals = json.loads(capsys.readouterr().out)

        _mod.cmd_friction_count(_friction_count_args(str(path), json_output=True))
        non_checkpoint_signals = json.loads(capsys.readouterr().out)

        assert set(checkpoint_signals.keys()) == set(non_checkpoint_signals.keys())
        assert set(checkpoint_signals.keys()) == {
            "denials", "failed_test_runs", "struggle_turns", "composite",
        }
        assert checkpoint_signals == non_checkpoint_signals
