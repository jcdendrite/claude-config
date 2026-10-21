"""Tests for transcript_analysis/buckets.py's cmd_buckets: model-family turn counts, --branches, the Proj
column's worktree collapse, and inline sidechain records in buckets and duration.
"""
from transcript_analysis import buckets, duration, scope

from ._session_signal_helpers import _SONNET, _branch_args
from .conftest import _asst, _attr_ts, _table_cols, _write_jsonl


class TestBuckets:
    def test_counts_non_sidechain_assistant_turns(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="feat-a"),
            _asst("claude-sonnet-4-6", branch="feat-a"),
            _asst("claude-sonnet-4-6", branch="feat-a", sidechain=True),  # excluded
        ])
        args = type("A", (), {"projects": "*", "this_repo": False, "branches": None})()
        buckets.cmd_buckets(args)
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
        buckets.cmd_buckets(args)
        out = capsys.readouterr().out
        assert "feat-a" in out
        assert "feat-b" not in out

    def test_no_data_prints_message(self, fake_projects, capsys):
        args = type("A", (), {"projects": "*", "this_repo": False, "branches": None})()
        buckets.cmd_buckets(args)
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
        monkeypatch.setattr(scope, "PROJECTS_DIR", projects)

        args = type("A", (), {"projects": "*", "this_repo": False, "branches": None})()
        buckets.cmd_buckets(args)
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
        monkeypatch.setattr(scope, "PROJECTS_DIR", projects)

        args = type("A", (), {"projects": "*", "this_repo": False, "branches": None})()
        buckets.cmd_buckets(args)
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Branch", row_contains="feat", max_labels=8)
        assert cols["Proj"] == "2"
        assert cols["Sess"] == "2"

    def test_proj_column_collapses_a_worktree_dir_into_its_main_repo(self, tmp_path, monkeypatch, capsys):
        """A repo's main checkout and its own linked worktree are one repo, not two --
        Proj must collapse the worktree-suffixed slug back to the main slug before counting."""
        projects = tmp_path / "projects"
        proj_main = projects / "-home-u-repo-a"
        proj_worktree = projects / "-home-u-repo-a--claude-worktrees-feat"
        proj_main.mkdir(parents=True)
        proj_worktree.mkdir(parents=True)
        _write_jsonl(proj_main / "sess.jsonl", [_asst("claude-sonnet-4-6", branch="feat")])
        _write_jsonl(proj_worktree / "sess.jsonl", [_asst("claude-sonnet-4-6", branch="feat")])
        monkeypatch.setattr(scope, "PROJECTS_DIR", projects)

        args = type("A", (), {"projects": "*", "this_repo": False, "branches": None})()
        buckets.cmd_buckets(args)
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Branch", row_contains="feat", max_labels=8)
        assert cols["Proj"] == "1"
        assert cols["Sess"] == "2"

    def test_proj_column_collapses_three_worktree_dirs_into_one_main_repo(self, tmp_path, monkeypatch, capsys):
        """Cardinality beyond a single worktree pair: a main checkout plus two
        differently-suffixed worktree dirs off the same repo all collapse to
        Proj == 1."""
        projects = tmp_path / "projects"
        proj_main = projects / "-home-u-repo-a"
        proj_worktree_x = projects / "-home-u-repo-a--claude-worktrees-feat-x"
        proj_worktree_y = projects / "-home-u-repo-a--claude-worktrees-feat-y"
        proj_main.mkdir(parents=True)
        proj_worktree_x.mkdir(parents=True)
        proj_worktree_y.mkdir(parents=True)
        _write_jsonl(proj_main / "sess.jsonl", [_asst("claude-sonnet-4-6", branch="feat")])
        _write_jsonl(proj_worktree_x / "sess.jsonl", [_asst("claude-sonnet-4-6", branch="feat")])
        _write_jsonl(proj_worktree_y / "sess.jsonl", [_asst("claude-sonnet-4-6", branch="feat")])
        monkeypatch.setattr(scope, "PROJECTS_DIR", projects)

        args = type("A", (), {"projects": "*", "this_repo": False, "branches": None})()
        buckets.cmd_buckets(args)
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Branch", row_contains="feat", max_labels=8)
        assert cols["Proj"] == "1"
        assert cols["Sess"] == "3"

    def test_proj_column_counts_same_repo_name_under_two_home_dirs_as_two_projects(self, tmp_path, monkeypatch, capsys):
        """Regression guard: routing the raw slug through _derive_proj_label first
        would discard the home/user prefix. That would falsely merge these two
        distinct repos into one family and undercount Proj."""
        projects = tmp_path / "projects"
        proj_alice = projects / "-home-alice-repo"
        proj_bob = projects / "-home-bob-repo"
        proj_alice.mkdir(parents=True)
        proj_bob.mkdir(parents=True)
        _write_jsonl(proj_alice / "sess.jsonl", [_asst("claude-sonnet-4-6", branch="feat")])
        _write_jsonl(proj_bob / "sess.jsonl", [_asst("claude-sonnet-4-6", branch="feat")])
        monkeypatch.setattr(scope, "PROJECTS_DIR", projects)

        args = type("A", (), {"projects": "*", "this_repo": False, "branches": None})()
        buckets.cmd_buckets(args)
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Branch", row_contains="feat", max_labels=8)
        assert cols["Proj"] == "2"
        assert cols["Sess"] == "2"

    def test_proj_column_collapses_worktree_while_counting_same_basename_sibling_separately(
        self, tmp_path, monkeypatch, capsys
    ):
        """The collapse fix and the over-collapse guard rail both hold in the same
        counting pass: a worktree checkout collapses into its own main repo, while
        a same-repo-name checkout under a different home directory stays a
        distinct family."""
        projects = tmp_path / "projects"
        proj_main = projects / "-home-u-repo-a"
        proj_worktree = projects / "-home-u-repo-a--claude-worktrees-feat"
        proj_sibling = projects / "-home-v-repo-a"
        proj_main.mkdir(parents=True)
        proj_worktree.mkdir(parents=True)
        proj_sibling.mkdir(parents=True)
        _write_jsonl(proj_main / "sess.jsonl", [_asst("claude-sonnet-4-6", branch="feat")])
        _write_jsonl(proj_worktree / "sess.jsonl", [_asst("claude-sonnet-4-6", branch="feat")])
        _write_jsonl(proj_sibling / "sess.jsonl", [_asst("claude-sonnet-4-6", branch="feat")])
        monkeypatch.setattr(scope, "PROJECTS_DIR", projects)

        args = type("A", (), {"projects": "*", "this_repo": False, "branches": None})()
        buckets.cmd_buckets(args)
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Branch", row_contains="feat", max_labels=8)
        assert cols["Proj"] == "2"
        assert cols["Sess"] == "3"


class TestInlineSidechainRecordsFollowTheDispatchingBranchInBucketsAndDuration:
    """A sidechain record sitting inline in a main transcript file takes the branch its
    main thread was on, so neither a foreign tag nor a worktree-agent-* tag becomes a row."""

    def test_buckets_folds_inline_sidechain_records_into_the_dispatching_branch_date_range(
        self, fake_projects, capsys
    ):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst(_SONNET, branch="feature-x", ts="2026-06-02T10:00:00.000Z"),
            _asst(_SONNET, branch="feature-x", ts="2026-06-02T10:05:00.000Z"),
            _asst(_SONNET, branch="foreign-branch", sidechain=True, ts="2026-06-05T10:00:00.000Z"),
            _asst(_SONNET, branch="worktree-agent-abc123", sidechain=True, ts="2026-06-06T10:00:00.000Z"),
        ])
        buckets.cmd_buckets(_branch_args())
        out = capsys.readouterr().out
        assert "foreign-branch" not in out
        assert "worktree-agent" not in out
        cols = _table_cols(out, header_contains="Branch", row_contains="feature-x", max_labels=8)
        assert cols["Total"] == "2"  # sidechain records never count as turns
        assert cols["Sess"] == "1"
        assert "2026-06-02..2026-06-06" in out

    def test_duration_folds_inline_sidechain_records_into_the_dispatching_branch_span(
        self, fake_projects, capsys
    ):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst(_SONNET, branch="feature-x", ts=_attr_ts(0)),
            _asst(_SONNET, branch="feature-x", ts=_attr_ts(5)),
            _asst(_SONNET, branch="foreign-branch", sidechain=True, ts=_attr_ts(20)),
            _asst(_SONNET, branch="foreign-branch", sidechain=True, ts=_attr_ts(22)),
            _asst(_SONNET, branch="worktree-agent-abc123", sidechain=True, ts=_attr_ts(25)),
            _asst(_SONNET, branch="worktree-agent-abc123", sidechain=True, ts=_attr_ts(27)),
        ])
        duration.cmd_duration(_branch_args())
        out = capsys.readouterr().out
        assert "foreign-branch" not in out
        assert "worktree-agent" not in out
        cols = _table_cols(out, header_contains="Span(min)", row_contains="feature-x")
        assert cols["Span(min)"] == "27"  # 12:00 to 12:27, not 12:00 to 12:05
        assert cols["Active(min)"] == "27"
