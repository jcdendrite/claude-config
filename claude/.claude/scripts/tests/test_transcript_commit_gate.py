"""Tests for transcript_analysis/commit_gate.py's cmd_commit_gate: commit gating by a prior skill invocation,
--no-verify, amend, and commit-tree handling, sidechain and exact-name skill matching, --by-permission-mode,
ISO-week bins, and the --projects, --exclude-projects, and --branches filters.
"""
from transcript_analysis import commit_gate

from ._commit_gate_helpers import _gate_args
from .conftest import _asst, _bash_use, _skill_use, _table_cols, _user_msg, _write_jsonl


class TestCommitGate:
    def test_empty_jsonl_prints_no_data(self, fake_projects, capsys):
        """Empty project dir → 'No data found.' and implicit exit 0."""
        args = _gate_args("code-review")
        commit_gate.cmd_commit_gate(args)
        out = capsys.readouterr().out
        assert "No data found." in out

    def test_single_session_no_commits_one_skill_invocation(self, fake_projects, capsys):
        """One skill call, no commits: 1 session, 0 commits, 1 invocation, rate > 0."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:00:00.000Z",
                  content=[_skill_use("s1", "code-review")]),
        ])
        args = _gate_args("code-review")
        commit_gate.cmd_commit_gate(args)
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
        commit_gate.cmd_commit_gate(args)
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
        commit_gate.cmd_commit_gate(args)
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
        commit_gate.cmd_commit_gate(args)
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
        commit_gate.cmd_commit_gate(args)
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
        commit_gate.cmd_commit_gate(args)
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
        commit_gate.cmd_commit_gate(args)
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
        commit_gate.cmd_commit_gate(args)
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
        commit_gate.cmd_commit_gate(args)
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
        commit_gate.cmd_commit_gate(args)
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
        commit_gate.cmd_commit_gate(args)
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
        commit_gate.cmd_commit_gate(args)
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
        commit_gate.cmd_commit_gate(args)
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
        commit_gate.cmd_commit_gate(args)
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
        commit_gate.cmd_commit_gate(args)
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
        commit_gate.cmd_commit_gate(args)
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
        commit_gate.cmd_commit_gate(args)
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
        commit_gate.cmd_commit_gate(args)
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
        commit_gate.cmd_commit_gate(args2)
        out2 = capsys.readouterr().out
        # Still only 1 session (feat-b-only session excluded)
        cols2 = _table_cols(out2, header_contains="sessions", row_contains="2026-W")
        assert int(cols2["sessions"]) == 1
