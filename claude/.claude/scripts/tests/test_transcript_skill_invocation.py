"""Tests for transcript_analysis/skill_invocation.py's cmd_skill_invocation: invocation buckets, --branches and
--include-subagents scoping, name normalization, provenance, the repo-scoped default, and /slash <command-args>
exclusion."""
import argparse
import os
import subprocess

import pytest
from transcript_analysis import scope, skill_invocation

from ._skill_helpers import _skill_inv_args
from .conftest import (
    _asst,
    _attr_ts,
    _bash_use,
    _skill_use,
    _table_cols,
    _user_msg,
    _write_jsonl,
    _write_subagent_jsonl,
)


class TestSkillInvocation:
    def _run(self, capsys):
        args = argparse.Namespace(projects="*")
        skill_invocation.cmd_skill_invocation(args)
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
        monkeypatch.setattr(scope, "PROJECTS_DIR", projects)
        # repoA has a skill invocation; repoB has a different skill
        rec_a = _asst("claude-sonnet-4-6", branch="main", content=[_skill_use("ta1", "code-review")])
        rec_b = _asst("claude-sonnet-4-6", branch="main", content=[_skill_use("tb1", "plan-review")])
        _write_jsonl(proj_a / "s_a.jsonl", [rec_a])
        _write_jsonl(proj_b / "s_b.jsonl", [rec_b])
        # Filter to repoA only
        args = argparse.Namespace(projects="-home-user-repoA")
        skill_invocation.cmd_skill_invocation(args)
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
        monkeypatch.setattr(scope, "PROJECTS_DIR", projects)
        rec_a = _asst("claude-sonnet-4-6", branch="main", content=[_skill_use("tc1", "code-review")])
        rec_b = _asst("claude-sonnet-4-6", branch="main", content=[_skill_use("tc2", "code-review")])
        _write_jsonl(proj_a / "s_a.jsonl", [rec_a])
        _write_jsonl(proj_b / "s_b.jsonl", [rec_b])
        args = argparse.Namespace(projects="*")
        skill_invocation.cmd_skill_invocation(args)
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="user-slash", row_contains="code-review", row_startswith=True)
        assert int(cols["top-level"]) == 2, f"expected 2 top-level from both projects: {cols}"


class TestSkillInvocationScoping:
    """Covers the branch/subagent scoping the procedural-fidelity consumer needs.

    The default (no flags) path is pinned by TestSkillInvocation above; these
    cases assert the opt-in behavior and that enabling it does not change what
    the default reports. They pass projects='*' (explicit escape hatch) so the
    git-derivation path is exercised separately in TestSkillInvocationRepoScope.
    """

    def _run(self, capsys, **kw):
        skill_invocation.cmd_skill_invocation(_skill_inv_args(**kw))
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
        """A record with no gitBranch is counted when no --branches filter is given."""
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
        """--branches composes with --include-subagents: only subagent invocations on a
        wanted branch are counted."""
        session_id = "sess-mix"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="wanted", ts=_attr_ts(1)),
            _asst("claude-opus-4-7", branch="other", ts=_attr_ts(10)),
        ])
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [
            _asst("claude-sonnet-4-6", branch="wanted", sidechain=True, ts=_attr_ts(5),
                  content=[_skill_use("s1", "code-review")]),
            _asst("claude-sonnet-4-6", branch="other", sidechain=True, ts=_attr_ts(15),
                  content=[_skill_use("s2", "plan-review")]),
        ])
        out = self._run(capsys, branches="wanted", include_subagents=True)
        assert "code-review" in out
        assert "plan-review" not in out, "off-branch subagent invocation must be excluded"


class TestSkillInvocationNameNormalization:
    """Worktree-qualified and colon-prefixed skill-name handling (row hygiene)."""

    def _run(self, capsys, **kw):
        skill_invocation.cmd_skill_invocation(_skill_inv_args(**kw))
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
        skill_invocation.cmd_skill_invocation(_skill_inv_args())
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
        monkeypatch.setattr(scope, "PROJECTS_DIR", projects)
        _write_jsonl(mine / "s.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", content=[_skill_use("x1", "code-review")]),
        ])
        _write_jsonl(theirs / "s.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", content=[_skill_use("x2", "plan-review")]),
        ])
        # cwd "/repo/main" maps via the real _path_to_project_slug to "-repo-main"
        # (only '/' and '.' become '-'); git lists that one worktree. Let the real
        # function run — a hand-copied lambda would freeze it against future drift.
        monkeypatch.setattr(os, "getcwd", lambda: "/repo/main")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                return subprocess.CompletedProcess(cmd, 0, self._worktree_porcelain("/repo/main"), "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo/main\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        skill_invocation.cmd_skill_invocation(argparse.Namespace(projects=None, branches=None, include_subagents=False))
        out = capsys.readouterr().out
        assert "code-review" in out, "in-scope skill must appear"
        assert "plan-review" not in out, "another project's skill must not appear"

    def test_fail_closed_when_git_unavailable(self, fake_projects, monkeypatch, capsys):
        """git failing must exit non-zero with NOTHING on stdout — not fall back to '*'."""
        def boom(cmd, *a, **k):
            raise FileNotFoundError("git")
        monkeypatch.setattr(subprocess, "run", boom)
        with pytest.raises(SystemExit):
            skill_invocation.cmd_skill_invocation(argparse.Namespace(projects=None, branches=None, include_subagents=False))
        captured = capsys.readouterr()
        assert captured.out == "", "fail-closed path must print nothing to stdout"
        assert "refusing" in captured.err.lower(), "should explain the refusal on stderr"

    def test_fail_closed_when_cwd_not_in_worktrees(self, fake_projects, monkeypatch, capsys):
        """git succeeds but resolves a repo unrelated to cwd (GIT_DIR / submodule):
        the cwd-not-in-slugs guard must fail closed."""
        monkeypatch.setattr(os, "getcwd", lambda: "/somewhere/else")

        def fake_run(cmd, *a, **k):
            return subprocess.CompletedProcess(cmd, 0, self._worktree_porcelain("/a/foreign/repo"), "")
        monkeypatch.setattr(subprocess, "run", fake_run)
        with pytest.raises(SystemExit):
            skill_invocation.cmd_skill_invocation(argparse.Namespace(projects=None, branches=None, include_subagents=False))
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
        skill_invocation.cmd_skill_invocation(argparse.Namespace(projects="*", branches=None, include_subagents=False))
        assert "code-review" in capsys.readouterr().out

    def test_scope_matches_by_literal_name_not_glob(self, tmp_path, monkeypatch, capsys):
        """A derived slug is matched as a literal directory name, not a glob. A
        slug containing a glob metacharacter (`*`/`?`/`[` in any path component)
        must not widen the read to a sibling project dir the wildcard would
        otherwise match — string equality does not; Path.glob would."""
        projects = tmp_path / "projects"
        mine = projects / "-r*-main"       # in-scope slug carries a '*'
        theirs = projects / "-rX-main"     # a wildcard on 'mine' would match this
        mine.mkdir(parents=True)
        theirs.mkdir(parents=True)
        monkeypatch.setattr(scope, "PROJECTS_DIR", projects)
        _write_jsonl(mine / "s.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", content=[_skill_use("y1", "code-review")]),
        ])
        _write_jsonl(theirs / "s.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", content=[_skill_use("y2", "plan-review")]),
        ])
        # The real _path_to_project_slug maps "/r*/main" -> "-r*-main"
        # ('/' and '.' -> '-'; the '*' is preserved), so no monkeypatch is needed —
        # letting it run is what makes this a real test of the '*' surviving into a
        # slug and still being matched literally.
        monkeypatch.setattr(os, "getcwd", lambda: "/r*/main")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                return subprocess.CompletedProcess(cmd, 0, self._worktree_porcelain("/r*/main"), "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/r*/main\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        skill_invocation.cmd_skill_invocation(argparse.Namespace(projects=None, branches=None, include_subagents=False))
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
        monkeypatch.setattr(scope, "PROJECTS_DIR", projects)
        _write_jsonl(main_dir / "s.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", content=[_skill_use("m1", "code-review")]),
        ])
        _write_jsonl(linked_dir / "s.jsonl", [
            _asst("claude-sonnet-4-6", branch="feat", content=[_skill_use("l1", "handoff")]),
        ])
        # cwd is the linked worktree (the second git entry), not the main one.
        monkeypatch.setattr(os, "getcwd", lambda: "/repo/.claude/worktrees/feat")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                return subprocess.CompletedProcess(
                    cmd, 0,
                    self._worktree_porcelain("/repo", "/repo/.claude/worktrees/feat"), "",
                )
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo/.claude/worktrees/feat\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        skill_invocation.cmd_skill_invocation(argparse.Namespace(projects=None, branches=None, include_subagents=False))
        out = capsys.readouterr().out
        assert "code-review" in out, "the main worktree's skill must appear"
        assert "handoff" in out, "the linked worktree's skill must appear"


def test_slash_invocation_counts_the_command_name_and_never_its_command_args(fake_projects, capsys):
    """A /slash invocation contributes only its <command-name>; <command-args> text, which can hold absolute paths,
    never reaches output."""
    _write_jsonl(fake_projects / "s.jsonl", [
        _user_msg(
            "<command-message>x</command-message>\n<command-name>/x</command-name>\n"
            "<command-args>/abs/secret</command-args>"
        ),
    ])
    skill_invocation.cmd_skill_invocation(_skill_inv_args(projects="*"))
    captured = capsys.readouterr()
    for stream_text in (captured.out, captured.err):
        assert "secret" not in stream_text, "command-args text must never reach output"
        assert "/abs" not in stream_text, "no fragment of command-args may reach output"
    cols = _table_cols(captured.out, header_contains="user-slash", row_contains="x", row_startswith=True)
    assert int(cols["user-slash"]) == 1, f"expected the x row to show one /slash invocation: {cols}"
