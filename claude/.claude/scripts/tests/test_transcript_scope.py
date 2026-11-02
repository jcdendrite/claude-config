"""scope.py's tests other than the scan-traversal, scan-gap and glob-selection classes in test_transcript_scope_scan.py."""
import argparse
import importlib.util
import os
import subprocess
import time
from datetime import date
from pathlib import Path

import pytest
from transcript_analysis import buckets, corpus, cost, scope, skill_invocation

from .conftest import _asst, _cost_args, _priced, _worktree_porcelain, _write_cost_root, _write_jsonl


def test_projects_dir_honors_claude_config_dir(monkeypatch, tmp_path):
    """Execs a fresh copy of scope.py because the imported module caches PROJECTS_DIR on first access."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    scope_path = Path(scope.__file__)
    spec = importlib.util.spec_from_file_location("transcript_analysis_scope_config_dir_case", scope_path)
    scope_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scope_mod)
    assert tmp_path / "projects" == scope_mod.PROJECTS_DIR


def test_scope_import_does_not_crash_when_home_unset(monkeypatch):
    """Importing transcript_analysis.scope must not raise even when $HOME is
    unset/empty and CLAUDE_CONFIG_DIR is not set -- PROJECTS_DIR resolves
    lazily, on first access, not at import time, so an importer that never
    touches PROJECTS_DIR (e.g. analyze-context.py's own `--help` path) never
    pays for a $HOME-unset resolution failure it doesn't need to hit."""
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("HOME", "")
    scope_path = Path(scope.__file__)
    spec = importlib.util.spec_from_file_location(
        "transcript_analysis_scope_home_unset_case", scope_path
    )
    scope_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scope_mod)  # must not raise


def test_resolve_scan_roots_default_root_when_home_unset(monkeypatch, tmp_path):
    """resolve_scan_roots' own internal `PROJECTS_DIR` references -- converted
    to `_projects_dir()` calls so they go through PEP 562 lazy resolution --
    must still resolve correctly when $HOME is unset. A bare-name reference
    left unconverted after PROJECTS_DIR became a lazy module attribute would
    raise NameError here: a bare global reference inside the module's own
    function bodies never reaches scope.py's __getattr__, unlike external
    `scope.PROJECTS_DIR` attribute access.
    """
    monkeypatch.setenv("HOME", "")
    config_dir = tmp_path / "active-account"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
    scope_path = Path(scope.__file__)
    spec = importlib.util.spec_from_file_location(
        "transcript_analysis_scope_resolve_roots_home_unset_case", scope_path
    )
    scope_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scope_mod)

    roots = scope_mod.resolve_scan_roots(argparse.Namespace())

    assert roots == [config_dir / "projects"]


class TestIsoDate:
    def test_iso_date_valid_returns_string(self):
        assert scope._iso_date("2026-05-19") == "2026-05-19"

    def test_iso_date_invalid_month_raises_type_error(self):
        with pytest.raises(argparse.ArgumentTypeError):
            scope._iso_date("2026-13-01")

    def test_iso_date_garbage_raises_type_error(self):
        with pytest.raises(argparse.ArgumentTypeError):
            scope._iso_date("garbage")


class TestParseSinceNdArg:
    """The shared --since Nd parser behind cmd_audit_routing, _cost_report,
    cmd_audit_routing_shape, and cmd_audit_routing_samples."""

    @pytest.mark.timing
    def test_valid_nd_value_returns_timestamp_and_raw_string(self):
        since_ts, since_raw = scope._parse_since_nd_arg(
            argparse.Namespace(since="1d"), "cost"
        )
        assert since_raw == "1d"
        assert since_ts == pytest.approx(time.time() - 86400, abs=1)

    def test_absent_since_returns_none_for_both(self):
        since_ts, since_raw = scope._parse_since_nd_arg(
            argparse.Namespace(since=None), "cost"
        )
        assert since_ts is None
        assert since_raw is None

    def test_malformed_value_exits_nonzero_with_subcommand_in_message(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            scope._parse_since_nd_arg(argparse.Namespace(since="not-a-window"), "cost")
        assert exc_info.value.code == 1
        assert "cost: --since: expected Nd like '35d'" in capsys.readouterr().err


class TestParseAbsoluteWindowArgs:
    """The shared inclusive-day absolute --since/--until DATE parser extracted
    from cmd_user_input/cmd_review_trace/cmd_judgment_pair/cmd_subagent_mix's
    identical six-line conversion."""

    def test_default_attrs_parse_since_and_until_as_inclusive_day_bounds(self):
        since_ts, until_ts = scope._parse_absolute_window_args(
            argparse.Namespace(since="2026-05-01", until="2026-05-03"), "review-trace",
        )
        assert since_ts == corpus._parse_ts("2026-05-01T00:00:00Z")
        assert until_ts == corpus._parse_ts("2026-05-03T00:00:00Z") + 86400

    def test_absent_since_and_until_returns_none_for_both(self):
        since_ts, until_ts = scope._parse_absolute_window_args(
            argparse.Namespace(since=None, until=None), "review-trace",
        )
        assert since_ts is None
        assert until_ts is None

    def test_parameterized_attrs_read_subagent_mix_since_date_until_date(self):
        """cmd_subagent_mix's own dest names (since_date/until_date) resolve
        through the same helper as the default since/until attrs -- the
        parameterized call cmd_subagent_mix's own call site uses."""
        since_ts, until_ts = scope._parse_absolute_window_args(
            argparse.Namespace(since_date="2026-07-01", until_date="2026-07-02"), "subagent-mix",
            since_attr="since_date", until_attr="until_date",
        )
        assert since_ts == corpus._parse_ts("2026-07-01T00:00:00Z")
        assert until_ts == corpus._parse_ts("2026-07-02T00:00:00Z") + 86400


class TestRepoScopedProjectSlugsGuard:
    """_repo_scoped_project_slugs' three fail-closed sys.exit sites and the
    row-20 containment-plus-identity cwd guard, exercised via a non-
    skill-invocation caller label to cover the newly-generic path."""

    def test_fails_closed_when_worktree_list_command_fails(self, monkeypatch, capsys):
        def boom(cmd, *a, **k):
            raise FileNotFoundError("git")
        monkeypatch.setattr(subprocess, "run", boom)
        with pytest.raises(SystemExit):
            scope._repo_scoped_project_slugs("buckets")
        assert "buckets:" in capsys.readouterr().err

    def test_fails_closed_when_worktree_list_returns_no_worktrees(self, monkeypatch, capsys):
        def fake_run(cmd, *a, **k):
            return subprocess.CompletedProcess(cmd, 0, "", "")
        monkeypatch.setattr(subprocess, "run", fake_run)
        with pytest.raises(SystemExit):
            scope._repo_scoped_project_slugs("buckets")
        assert "buckets:" in capsys.readouterr().err

    def test_fails_closed_when_cwd_outside_all_worktrees(self, monkeypatch, capsys):
        monkeypatch.setattr(os, "getcwd", lambda: "/somewhere/else")

        def fake_run(cmd, *a, **k):
            return subprocess.CompletedProcess(cmd, 0, _worktree_porcelain("/repo"), "")
        monkeypatch.setattr(subprocess, "run", fake_run)
        with pytest.raises(SystemExit):
            scope._repo_scoped_project_slugs("duration")
        assert "duration:" in capsys.readouterr().err

    def test_cwd_inside_worktree_subdirectory_resolves_successfully(self, monkeypatch):
        """Containment (not slug equality) lets --this-repo run from a subdirectory
        of the worktree root."""
        monkeypatch.setattr(os, "getcwd", lambda: "/repo/nested/dir")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                return subprocess.CompletedProcess(cmd, 0, _worktree_porcelain("/repo"), "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        slugs = scope._repo_scoped_project_slugs("duration")
        assert slugs == [scope._path_to_project_slug("/repo")]

    def test_sibling_repo_sharing_string_prefix_is_rejected(self, monkeypatch, capsys):
        """cwd inside a sibling path sharing the same string prefix (<repo>-fork)
        must exit 1 — the case bare str.startswith containment would wrongly
        accept."""
        monkeypatch.setattr(os, "getcwd", lambda: "/repo-fork/nested")

        def fake_run(cmd, *a, **k):
            return subprocess.CompletedProcess(cmd, 0, _worktree_porcelain("/repo"), "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit):
            scope._repo_scoped_project_slugs("duration")
        assert capsys.readouterr().out == ""

    def test_fails_closed_when_cwd_repo_root_diverges_from_worktree_list(self, monkeypatch, capsys):
        """Identity guard: cwd sits under a listed worktree (containment passes)
        but `git rev-parse --show-toplevel` reports a different root — the case
        an environment override like GIT_WORK_TREE could produce by decoupling
        toplevel reporting from the worktree-list enumeration. Every other test
        in this class returns the same path from both git calls, so this is the
        only test that would catch a typo bug in the identity check itself."""
        monkeypatch.setattr(os, "getcwd", lambda: "/repo/nested")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                return subprocess.CompletedProcess(cmd, 0, _worktree_porcelain("/repo"), "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/outer\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit):
            scope._repo_scoped_project_slugs("duration")
        assert "repo root is not among the resolved worktrees" in capsys.readouterr().err

    def test_fails_closed_when_rev_parse_toplevel_command_fails(self, monkeypatch, capsys):
        """The identity-check subprocess call erroring must fail closed, not
        fall back to a machine-wide scope, even though worktree list succeeded."""
        monkeypatch.setattr(os, "getcwd", lambda: "/repo/nested")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                return subprocess.CompletedProcess(cmd, 0, _worktree_porcelain("/repo"), "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            raise FileNotFoundError("git")
        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit):
            scope._repo_scoped_project_slugs("duration")
        assert "duration:" in capsys.readouterr().err


class TestResolveProjectScope:
    """The scope-dispatch helper behind --projects/--this-repo on the 16
    non-skill-invocation subcommands."""

    def test_this_repo_unset_raises_rather_than_defaulting_to_machine_wide(self, fake_projects):
        """A subparser wired without _add_project_scope_args (missing this_repo
        entirely) must raise, not silently fall through to '*' scope."""
        args = type("A", (), {"projects": "*"})()  # no this_repo attribute at all
        with pytest.raises(AttributeError):
            scope._resolve_project_scope(args, "buckets")

    def test_called_twice_from_one_args_yields_same_session_list_both_times(self, tmp_path, monkeypatch):
        """cmd_audit_routing's redact pass and main pass both call the scope helper
        on the same args object — a shared/exhausted generator would make the
        second pass silently empty."""
        projects = tmp_path / "projects"
        mine = projects / "-repo-main"
        mine.mkdir(parents=True)
        monkeypatch.setattr(scope, "PROJECTS_DIR", projects)
        _write_jsonl(mine / "s.jsonl", [_asst("claude-sonnet-4-6", branch="main")])
        monkeypatch.setattr(os, "getcwd", lambda: "/repo/main")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                return subprocess.CompletedProcess(cmd, 0, _worktree_porcelain("/repo/main"), "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo/main\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        args = argparse.Namespace(this_repo=True, projects="*")
        iter1, label1 = scope._resolve_project_scope(args, "audit-routing")
        iter2, label2 = scope._resolve_project_scope(args, "audit-routing")
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
        monkeypatch.setattr(scope, "PROJECTS_DIR", projects)
        _write_jsonl(mine / "s.jsonl", [_asst("claude-sonnet-4-6", branch="feat")])
        monkeypatch.setattr(os, "getcwd", lambda: "/repo/main")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                return subprocess.CompletedProcess(cmd, 0, _worktree_porcelain("/repo/main"), "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo/main\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        buckets.cmd_buckets(type("A", (), {"projects": "*", "this_repo": True, "branches": None})())
        out_repo = capsys.readouterr().out
        assert (
            "BUCKETS SOURCES (this repo (1 project dirs); "
            "1 root (no ~/.claude/transcript-config-dirs declared))"
        ) in out_repo

        buckets.cmd_buckets(type("A", (), {"projects": "*", "this_repo": False, "branches": None})())
        out_glob = capsys.readouterr().out
        assert "BUCKETS SOURCES (*; 1 root (no ~/.claude/transcript-config-dirs declared))" in out_glob


class TestPathToProjectSlug:
    """The slug transform and its known, accepted lossiness."""

    def test_main_repo_path(self):
        assert scope._path_to_project_slug("/home/<user>/repo") == "-home-<user>-repo"

    def test_worktree_path(self):
        assert (scope._path_to_project_slug("/home/<user>/repo/.claude/worktrees/b")
                == "-home-<user>-repo--claude-worktrees-b")

    def test_known_slug_collision_is_accepted(self):
        """`/` and `.` both map to `-`, so distinct paths can collapse to one slug.
        This is Claude Code's own dir-naming scheme, not ours to change; the repo
        scoping accepts this residual (see _repo_scoped_project_slugs). Pinned so a
        later refactor cannot silently assume injectivity."""
        assert scope._path_to_project_slug("/home/<user>/a/b") == scope._path_to_project_slug("/home/<user>/a.b")


class TestThisRepoUnionsAcrossRoots:
    """--this-repo (and, at the _iter_scoped_sessions layer it shares, every
    other slug-scoped caller) unions across every resolved root, whether
    those roots came from a declared-roots file or from cost's own explicit
    --config-dir extras -- the union mechanism at this layer is the same
    regardless of which resolver assembled `roots`."""

    def test_multi_root_slug_match_is_name_only_and_dedupes_across_roots(self, tmp_path):
        """Direct coverage of _iter_scoped_sessions(roots=[a, b]): the same
        slug present under two roots unions both sessions, and a third root
        that's a symlink alias to one of the first two contributes no
        duplicate (dedup spans every root, not just identical-path roots)."""
        root_a = tmp_path / "acct-a"
        proj_a = root_a / "-repo-main"
        proj_a.mkdir(parents=True)
        _write_jsonl(proj_a / "sess-a.jsonl", [_asst("claude-sonnet-4-6", branch="from-root-a")])

        root_b = tmp_path / "acct-b"
        proj_b = root_b / "-repo-main"
        proj_b.mkdir(parents=True)
        _write_jsonl(proj_b / "sess-b.jsonl", [_asst("claude-sonnet-4-6", branch="from-root-b")])

        root_c_alias = tmp_path / "acct-a-alias"
        root_c_alias.symlink_to(root_a)

        sessions = list(scope._iter_scoped_sessions(
            ["-repo-main"], False, roots=[root_a, root_b, root_c_alias],
        ))
        branches_seen = {rec["gitBranch"] for _jsonl, records in sessions for rec in records}
        assert branches_seen == {"from-root-a", "from-root-b"}
        assert len(sessions) == 2  # root_c_alias contributes no duplicate of root_a's session

    def test_this_repo_excludes_foreign_project_dirs_under_extra_root(self, tmp_path):
        """The minimization guard that replaces the (out-of-scope-here)
        refusal on cost's --this-repo + --config-dir combination — must not
        be skipped: a directory under a declared
        root that isn't one of the resolved worktree slugs stays excluded,
        proving the union doesn't widen matching to every project on that root."""
        root_a = tmp_path / "acct-a"
        proj_a = root_a / "-repo-main"
        proj_a.mkdir(parents=True)
        _write_jsonl(proj_a / "sess-a.jsonl", [_asst("claude-sonnet-4-6", branch="from-root-a")])

        root_b = tmp_path / "acct-b"
        proj_b_matching = root_b / "-repo-main"
        proj_b_matching.mkdir(parents=True)
        _write_jsonl(proj_b_matching / "sess-b.jsonl", [_asst("claude-sonnet-4-6", branch="from-root-b")])
        proj_b_foreign = root_b / "-home-user-unrelated-secret-clientname"
        proj_b_foreign.mkdir(parents=True)
        _write_jsonl(proj_b_foreign / "sess-foreign.jsonl", [_asst("claude-sonnet-4-6", branch="foreign-branch")])

        sessions = list(scope._iter_scoped_sessions(["-repo-main"], False, roots=[root_a, root_b]))
        branches_seen = {rec["gitBranch"] for _jsonl, records in sessions for rec in records}
        assert branches_seen == {"from-root-a", "from-root-b"}
        assert "foreign-branch" not in branches_seen

    def test_this_repo_slug_collision_admits_foreign_project_with_identical_slug(
        self, tmp_path, monkeypatch
    ):
        """Pins a known, plan-accepted residual risk (see Step 21 of
        .claude/plans/transcript-corpus-multi-account-scope.md: "[/.]→- is
        not injective, so /a/b.c and /a/b/c collide"), not a bug to fix here.
        _iter_scoped_sessions matches by basename equality alone, so it
        cannot distinguish this repo's own project dir from a foreign
        account's unrelated project dir that happens to collide onto the
        same slug -- under --this-repo, both are admitted. A future change to
        slug derivation that silently alters this posture, in either
        direction, must fail this test."""
        this_repo_slug = scope._path_to_project_slug("/a/b/c")
        foreign_path_slug = scope._path_to_project_slug("/a/b.c")
        assert this_repo_slug == foreign_path_slug == "-a-b-c"  # the collision this test pins

        root_a = tmp_path / "acct-a"
        proj_a = root_a / this_repo_slug
        proj_a.mkdir(parents=True)
        _write_jsonl(proj_a / "sess-a.jsonl", [_asst("claude-sonnet-4-6", branch="this-repo-session")])

        root_b = tmp_path / "acct-b"
        proj_b_colliding = root_b / foreign_path_slug
        proj_b_colliding.mkdir(parents=True)
        _write_jsonl(proj_b_colliding / "sess-foreign.jsonl",
                     [_asst("claude-sonnet-4-6", branch="foreign-colliding-session")])

        args = type("A", (), {"projects": "*", "this_repo": True})()
        args._this_repo_slugs = [this_repo_slug]
        monkeypatch.setattr(scope, "_repo_scoped_project_slugs", lambda *a, **k: args._this_repo_slugs)

        session_iter, _scope_label = scope._resolve_project_scope(args, "buckets", roots=[root_a, root_b])
        branches_seen = {rec["gitBranch"] for _jsonl, records in session_iter for rec in records}
        assert branches_seen == {"this-repo-session", "foreign-colliding-session"}

    def test_this_repo_unions_same_slug_across_roots(self, tmp_path, monkeypatch, capsys):
        """End-to-end: buckets --this-repo, with a declared-roots file adding
        a second root that also contains this repo's own worktree slug --
        both roots' sessions appear in one report, not just the active
        profile's."""
        root_a = tmp_path / "acct-a"
        proj_a = root_a / "-repo-main"
        proj_a.mkdir(parents=True)
        _write_jsonl(proj_a / "sess-a.jsonl", [_asst("claude-sonnet-4-6", branch="feat-in-root-a")])
        monkeypatch.setattr(scope, "PROJECTS_DIR", root_a)

        root_b = tmp_path / "acct-b-config"
        proj_b = root_b / "projects" / "-repo-main"
        proj_b.mkdir(parents=True)
        _write_jsonl(proj_b / "sess-b.jsonl", [_asst("claude-sonnet-4-6", branch="feat-in-root-b")])
        roots_file = tmp_path / "roots"
        roots_file.write_text(f"{root_b}\n")
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))

        monkeypatch.setattr(os, "getcwd", lambda: "/repo/main")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                porcelain = _worktree_porcelain("/repo/main")
                return subprocess.CompletedProcess(cmd, 0, porcelain, "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo/main\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        buckets.cmd_buckets(type("A", (), {"projects": "*", "this_repo": True, "branches": None})())
        out = capsys.readouterr().out
        assert "feat-in-root-a" in out
        assert "feat-in-root-b" in out
        assert "2 roots" in out

    def test_this_repo_fail_closed_across_all_roots(self, tmp_path):
        """The fail-closed deny case: zero slug matches under EVERY
        resolved root still exits 1 — direct unit coverage of `any(...
        for root in roots for slug in slugs)` iterating every root, not just
        roots[0]. Calls _resolve_project_scope directly with a hand-built
        multi-element `roots` list (unreachable through the real CLI today,
        since an explicit top-level --config-dir always collapses `roots` to
        one element — see scope.resolve_scan_roots) as a robustness pin against a
        future precedence change, per the plan's own framing."""
        root_a = tmp_path / "acct-a"
        root_a.mkdir()
        root_b = tmp_path / "acct-b"
        root_b.mkdir()
        args = argparse.Namespace(this_repo=True, projects="*", config_dir=str(root_a), _this_repo_slugs=["-repo-main"])
        with pytest.raises(SystemExit) as exc_info:
            scope._resolve_project_scope(args, "buckets", roots=[root_a, root_b])
        assert exc_info.value.code == 1

    def test_this_repo_allow_case_when_only_a_non_first_root_matches(self, tmp_path):
        """Companion allow-case to the deny test above: a match under the
        SECOND resolved root (not roots[0]) must not fail closed — proves the
        `any(...)` genuinely iterates every root rather than short-circuiting
        on the first."""
        root_a = tmp_path / "acct-a"
        root_a.mkdir()
        root_b = tmp_path / "acct-b"
        (root_b / "-repo-main").mkdir(parents=True)
        args = argparse.Namespace(this_repo=True, projects="*", config_dir=str(root_a), _this_repo_slugs=["-repo-main"])
        session_iter, scope_label = scope._resolve_project_scope(args, "buckets", roots=[root_a, root_b])
        assert list(session_iter) == []  # no sessions written, but no SystemExit either
        assert scope_label == "this repo (1 project dirs)"


class TestSingleLevelProjectsGlob:
    """_single_level_projects_glob: the runtime validator that rejects a
    --projects value Path.glob would read as more than one directory-name
    match, or a different shape entirely, than the root-level fnmatch
    matching in _iter_glob_scoped_sessions selects for. Called only from a
    multi-root branch (_resolve_project_scope's own, and cmd_skill_invocation's
    inline equivalent) -- never wired as an argparse type=, since whether the
    restriction applies depends on how many roots this invocation resolves at
    runtime, not on the flag's syntax alone. A single-root invocation reads
    corpus.iter_sessions, whose own Path.glob supports a nested-directory
    pattern and must keep accepting one unchanged."""

    @pytest.mark.parametrize("value", ["*", "-home-user-repo*", "feat-?", "[ab]*", ""])
    def test_accepts_single_level_glob_values_unchanged(self, value):
        assert scope._single_level_projects_glob(value) == value

    @pytest.mark.parametrize("value", ["a/b", "a/", "/a", "**", "a**", ".", ".."])
    def test_rejects_values_path_glob_would_read_differently(self, value):
        with pytest.raises(argparse.ArgumentTypeError):
            scope._single_level_projects_glob(value)

    def test_multi_root_scope_rejects_a_multi_segment_projects_value_at_runtime(self, tmp_path):
        """The one-level `--projects` restriction applies in
        `_resolve_project_scope`'s multi-root branch: a nested-directory value
        under two roots exits 2."""
        root_a = tmp_path / "acct-a"
        root_a.mkdir()
        root_b = tmp_path / "acct-b"
        root_b.mkdir()
        args = argparse.Namespace(this_repo=False, projects="a/b")
        with pytest.raises(SystemExit) as exc:
            scope._resolve_project_scope(args, "buckets", roots=[root_a, root_b])
        assert exc.value.code == 2

    def test_single_root_scope_accepts_a_multi_segment_projects_value_at_runtime(self, tmp_path):
        """The single-root branch reads corpus.iter_sessions, whose own
        Path.glob supports a nested-directory pattern, so this branch accepts
        a multi-segment `--projects` value."""
        root = tmp_path / "acct-a"
        nested = root / "sub" / "-repo-main"
        nested.mkdir(parents=True)
        _write_jsonl(nested / "sess.jsonl", [_asst("claude-sonnet-4-6", branch="from-nested")])
        args = argparse.Namespace(this_repo=False, projects="sub/-repo-main")
        session_iter, _scope_label = scope._resolve_project_scope(args, "buckets", roots=[root])
        branches_seen = {rec["gitBranch"] for _jsonl, records in session_iter for rec in records}
        assert branches_seen == {"from-nested"}

    def test_single_root_scope_rejects_a_parent_traversing_projects_value(self, tmp_path):
        """A '..' component in --projects is a real parent-directory step for
        Path.glob, not a no-op: iter_sessions must yield nothing for it
        instead of yielding a sibling account's own session file."""
        root = tmp_path / "acct-a"
        root.mkdir()
        sibling = tmp_path / "acct-b"
        sibling.mkdir()
        _write_jsonl(sibling / "secret.jsonl", [_asst("claude-sonnet-4-6", branch="from-sibling")])
        args = argparse.Namespace(this_repo=False, projects="../acct-b")
        session_iter, _scope_label = scope._resolve_project_scope(args, "buckets", roots=[root])
        assert list(session_iter) == []

    def test_single_root_scope_reads_a_project_dir_symlinked_outside_the_root(self, tmp_path):
        """Only a '..' in the raw glob is rejected. With a single scan root,
        a project directory symlinked to a readable location outside the
        root stays in scope with the default glob. Under more than one root
        the same input has no owning root, so consumers that attribute each
        session to a root abort."""
        root = tmp_path / "acct-a"
        root.mkdir()
        outside = tmp_path / "elsewhere" / "-repo-relocated"
        outside.mkdir(parents=True)
        _write_jsonl(outside / "sess.jsonl", [_asst("claude-sonnet-4-6", branch="from-relocated")])
        (root / "-repo-relocated").symlink_to(outside)
        args = argparse.Namespace(this_repo=False, projects="*")
        session_iter, _scope_label = scope._resolve_project_scope(args, "buckets", roots=[root])
        branches_seen = {rec["gitBranch"] for _jsonl, records in session_iter for rec in records}
        assert branches_seen == {"from-relocated"}

    def test_single_root_scope_skips_a_symlink_loop_transcript_without_raising(self, tmp_path):
        """A looped .jsonl symlink fails open() with OSError, which the
        record parser treats as an unreadable file, so the healthy sibling
        transcript is still read and nothing raises."""
        root = tmp_path / "acct-a"
        proj = root / "-repo-main"
        proj.mkdir(parents=True)
        _write_jsonl(proj / "sess.jsonl", [_asst("claude-sonnet-4-6", branch="from-healthy")])
        loop_link = proj / "loop.jsonl"
        loop_link.symlink_to(loop_link)
        args = argparse.Namespace(this_repo=False, projects="*")
        session_iter, _scope_label = scope._resolve_project_scope(args, "buckets", roots=[root])
        branches_seen = {rec["gitBranch"] for _jsonl, records in session_iter for rec in records}
        assert branches_seen == {"from-healthy"}

    def test_cost_scan_diagnostic_rejects_a_parent_traversing_projects_value(
        self, tmp_path, monkeypatch, capsys,
    ):
        """cost's own per-root scan diagnostic (scope._scan_root_transcripts)
        is reached with the raw, unvalidated --projects value, a separate
        path from _resolve_project_scope's already-guarded iter_sessions
        call above: a '..' value must not count a sibling account's own
        transcripts there either."""
        default_config = tmp_path / "default-account"
        default_proj = default_config / "projects" / "-home-user-repo-a"
        default_proj.mkdir(parents=True)
        _write_jsonl(default_proj / "sess-a.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])
        monkeypatch.setattr(scope, "config_dir", lambda: default_config)

        sibling_proj = tmp_path / "secret-account" / "projects" / "-home-secret-repo"
        sibling_proj.mkdir(parents=True)
        _write_jsonl(sibling_proj / "secret.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])

        cost.cmd_cost(_cost_args(projects="../../secret-account/projects/-home-secret-repo"))
        out = capsys.readouterr().out
        assert "cost: account-1: scanned 0 transcripts, 0 skipped (unreadable)" in out
        assert "WARNING: cost: account-1: no transcripts found for this scope" in out

    def test_skill_invocation_multi_root_rejects_a_multi_segment_projects_value_at_runtime(
        self, tmp_path, monkeypatch, capsys,
    ):
        """cmd_skill_invocation inlines the same len(roots) > 1 /
        _iter_glob_scoped_sessions branch _resolve_project_scope's own
        multi-root branch does, so it needs the identical runtime check."""
        root_a = tmp_path / "acct-a"
        root_a.mkdir()
        root_b = tmp_path / "acct-b"
        root_b.mkdir()
        args = argparse.Namespace(projects="a/b", branches=None, include_subagents=False, config_dir=None)
        monkeypatch.setattr(scope, "resolve_scan_roots", lambda _args: [root_a, root_b])
        with pytest.raises(SystemExit) as exc:
            skill_invocation.cmd_skill_invocation(args)
        assert exc.value.code == 2
        assert "--projects" in capsys.readouterr().err


class TestPoisonedProjectsDirGlobal:
    """PROJECTS_DIR pointed at a nonexistent path, with one real root
    declared via TRANSCRIPT_CONFIG_DIRS_FILE — sessions are still found
    through the declared root, proving no funnel site still reads
    PROJECTS_DIR as an unthreaded, single-root default."""

    def test_sessions_found_via_declared_root_despite_nonexistent_projects_dir(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.setattr(scope, "PROJECTS_DIR", tmp_path / "nonexistent-active-profile" / "projects")
        real_root = tmp_path / "real-account"
        proj = real_root / "projects" / "-home-user-testrepo"
        proj.mkdir(parents=True)
        _write_jsonl(proj / "sess.jsonl", [_asst("claude-sonnet-4-6", branch="feat-declared-root")])
        roots_file = tmp_path / "roots"
        roots_file.write_text(f"{real_root}\n")
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))

        buckets.cmd_buckets(type("A", (), {"projects": "*", "this_repo": False, "branches": None})())
        out = capsys.readouterr().out
        assert "feat-declared-root" in out
        assert "2 roots" in out


class TestResolveScanRoots:
    """scope.resolve_scan_roots as a directly callable, unit-testable function --
    the explicit top-level --config-dir precedence over a populated
    declared-roots file, and getattr-based tolerance of a hand-built `args`
    Namespace that lacks the config_dir attribute entirely (e.g. the ad-hoc
    `type("A", (), {...})()` args the cmd_buckets tests pass)."""

    def test_no_config_dir_no_declared_roots_returns_projects_dir_alone(self, monkeypatch, tmp_path):
        monkeypatch.setattr(scope, "PROJECTS_DIR", tmp_path / "active" / "projects")
        args = argparse.Namespace(config_dir=None)
        assert scope.resolve_scan_roots(args) == [tmp_path / "active" / "projects"]

    def test_no_config_dir_with_declared_roots_unions_both(self, monkeypatch, tmp_path):
        monkeypatch.setattr(scope, "PROJECTS_DIR", tmp_path / "active" / "projects")
        declared = tmp_path / "declared-account"
        (declared / "projects").mkdir(parents=True)
        roots_file = tmp_path / "roots"
        roots_file.write_text(f"{declared}\n")
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))

        args = argparse.Namespace(config_dir=None)
        assert scope.resolve_scan_roots(args) == [
            tmp_path / "active" / "projects", declared / "projects",
        ]

    def test_explicit_top_level_config_dir_overrides_populated_declared_roots_file(
        self, monkeypatch, tmp_path
    ):
        """The flagship precedence rule: an explicit --config-dir wins outright,
        returning that one directory's projects/ subdirectory alone — the
        declared-roots file's entries are not unioned in on top of it."""
        monkeypatch.setattr(scope, "PROJECTS_DIR", tmp_path / "active" / "projects")
        declared = tmp_path / "declared-account"
        (declared / "projects").mkdir(parents=True)
        roots_file = tmp_path / "roots"
        roots_file.write_text(f"{declared}\n")
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))

        override_dir = tmp_path / "explicit-override"
        args = argparse.Namespace(config_dir=str(override_dir))
        assert scope.resolve_scan_roots(args) == [override_dir / "projects"]

    def test_config_dir_attribute_absent_from_parsed_does_not_raise(self, monkeypatch, tmp_path):
        """A hand-built test `args` object with no config_dir attribute (the
        shape the cmd_buckets tests pass) must not raise AttributeError -- its
        absence means "not passed," the real default."""
        monkeypatch.setattr(scope, "PROJECTS_DIR", tmp_path / "active" / "projects")
        args = type("A", (), {})()  # no config_dir attribute at all
        assert scope.resolve_scan_roots(args) == [tmp_path / "active" / "projects"]

    def test_declared_root_matching_active_profile_is_deduped_not_double_listed(
        self, monkeypatch, tmp_path
    ):
        """PROJECTS_DIR is pre-seeded into seen_resolved before iterating
        declared roots, so a declared root that IS (or symlink-aliases) the
        active profile is deduped, not double-listed."""
        active_config = tmp_path / "active-account"
        (active_config / "projects").mkdir(parents=True)
        monkeypatch.setattr(scope, "PROJECTS_DIR", active_config / "projects")
        roots_file = tmp_path / "roots"
        roots_file.write_text(f"{active_config}\n")
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))

        args = argparse.Namespace(config_dir=None)
        assert scope.resolve_scan_roots(args) == [active_config / "projects"]


class TestRedactionOrdinalStability:
    """_redaction_ordinals assigns the same ordinal to the same physical
    root regardless of which profile is active (list order) -- guards
    against the bug a local-sort-inside-_build_redact_map approach would
    reintroduce."""

    def test_same_two_roots_different_scan_order_yield_identical_ordinals(self, tmp_path):
        root_a = tmp_path / "acct-a"
        root_a.mkdir()
        root_b = tmp_path / "acct-b"
        root_b.mkdir()

        ordinals_a_first = scope._redaction_ordinals([root_a, root_b])
        ordinals_b_first = scope._redaction_ordinals([root_b, root_a])

        assert ordinals_a_first == ordinals_b_first
        assert ordinals_a_first[root_a.resolve()] == ordinals_b_first[root_a.resolve()]
        assert ordinals_a_first[root_b.resolve()] == ordinals_b_first[root_b.resolve()]

    def test_cost_report_assigns_same_account_label_regardless_of_which_profile_is_active(
        self, tmp_path, capsys
    ):
        """End-to-end version of the ordinal-stability fix: the same physical
        root reads as the same account-N whether it's scanned as the active
        profile (scanned first) or as a declared extra (scanned second)."""
        root_alpha = _write_cost_root(tmp_path, "acct-alpha", "-home-user-repo-alpha", "sess-alpha",
                                       [_priced("claude-sonnet-5", input=1_000_000)])
        root_zulu = _write_cost_root(tmp_path, "acct-zulu", "-home-user-repo-zulu", "sess-zulu",
                                      [_priced("claude-sonnet-5", input=1_000_000)])

        cost._cost_report(_cost_args(), date(2026, 8, 2), roots=[root_alpha, root_zulu])
        out_alpha_active = capsys.readouterr().out

        cost._cost_report(_cost_args(), date(2026, 8, 2), roots=[root_zulu, root_alpha])
        out_zulu_active = capsys.readouterr().out

        # root_alpha resolves alphabetically first, so it holds account-1 in both
        # runs regardless of which one was scanned first (which is active).
        assert "account-1/private-project-1" in out_alpha_active
        assert "account-2/private-project-1" in out_alpha_active
        assert "account-1/private-project-1" in out_zulu_active
        assert "account-2/private-project-1" in out_zulu_active


class TestResolvedScopeHeaderDirectUnit:
    """Direct unit coverage of _resolved_scope_header's pure string
    formatting — the many end-to-end subcommand tests exercise this function
    only through a full report run; these pin its exact return value at each
    root-count branch."""

    def test_one_root_states_no_declared_roots_file(self, tmp_path):
        header = scope._resolved_scope_header("buckets", "*", [tmp_path / "acct-a" / "projects"])
        assert header == (
            "BUCKETS SOURCES (*; 1 root (no ~/.claude/transcript-config-dirs declared))"
        )

    def test_n_roots_states_the_count(self, tmp_path):
        roots = [
            tmp_path / "acct-a" / "projects",
            tmp_path / "acct-b" / "projects",
            tmp_path / "acct-c" / "projects",
        ]
        header = scope._resolved_scope_header("cost", "this repo (2 project dirs)", roots)
        assert header == "COST SOURCES (this repo (2 project dirs); 3 roots)"


class TestRootCountDescDirectUnit:
    """Mechanism 2: _root_count_desc's one-root branch must distinguish the
    roots file being absent from it being present but contributing no
    additional root -- claiming "no ... declared" about a file that exists
    would misrepresent it."""

    def test_one_root_absent_roots_file_states_no_declared_roots_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(tmp_path / "does-not-exist"))
        assert scope._root_count_desc([tmp_path / "acct-a" / "projects"]) == (
            "1 root (no ~/.claude/transcript-config-dirs declared)"
        )

    def test_one_root_present_but_additive_nothing_does_not_claim_undeclared(self, tmp_path, monkeypatch):
        """The populated-but-additive-nothing case: the roots file exists
        (comments-only, here) but every line was skipped, so exactly one
        root is in scope -- the text must not claim the file is undeclared."""
        roots_file = tmp_path / "roots"
        roots_file.write_text("# just a comment\n")
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))
        desc = scope._root_count_desc([tmp_path / "acct-a" / "projects"])
        assert "no ~/.claude/transcript-config-dirs declared" not in desc
        assert "~/.claude/transcript-config-dirs" in desc

    def test_one_root_unreadable_roots_file_states_unreadable_not_declared(self, tmp_path, monkeypatch):
        """A directory at the roots-file path raises OSError on read --
        "unreadable", not "absent", and distinct from "present" (a failed
        read never honored any declarations, so wording it as "declared"
        would misrepresent the read that never happened); see
        declared_roots_file_state()'s own docstring for why a directory (not
        chmod) simulates this without silently degrading under a
        root-executing test runner."""
        roots_file_as_dir = tmp_path / "roots-is-a-directory"
        roots_file_as_dir.mkdir()
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file_as_dir))
        assert scope._root_count_desc([tmp_path / "acct-a" / "projects"]) == (
            "1 root (~/.claude/transcript-config-dirs present but unreadable)"
        )

    def test_multi_root_never_names_the_roots_file(self, tmp_path, monkeypatch):
        """Extra roots may come from --config-dir, not only a declared-roots
        file, so the >1-root branch must not name the file at all."""
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(tmp_path / "does-not-exist"))
        roots = [tmp_path / "acct-a" / "projects", tmp_path / "acct-b" / "projects"]
        assert scope._root_count_desc(roots) == "2 roots"
        assert "transcript-config-dirs" not in scope._root_count_desc(roots)
