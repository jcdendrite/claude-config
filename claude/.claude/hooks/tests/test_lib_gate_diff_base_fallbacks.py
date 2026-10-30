"""Unit tests for _lib.sh's _lib_gate_diff_base merge-tree failure, cap-fault and no-cap fallbacks and its documented
origin-forgery behavior, the round-state key during a rebase, and _lib_staged_diff_hash."""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from helpers import (
    _make_blocking_diff_git,
    _make_blocking_merge_tree_git,
    _make_git_blocking_on_arg,
    _make_git_exiting_with_status,
    _run_git,
    _timeout_binary_present,
    bare_remote_with_default_branch,
    build_conflicted_cherry_pick,
    build_conflicted_rebase,
    build_conflicted_revert,
    build_path_without,
    init_git_repo_with_commit,
    reviewer_round_state_key,
    staged_diff_hash,
    staged_diff_hash_at_base,
)

from ._lib_sh_helpers import (
    _assert_valid_tree_oid,
    _forge_state_marker,
    _gate_diff_base,
    _staged_diff_hash,
)


def _make_blocking_absolute_git_dir_git(bin_dir: Path) -> Path:
    """Shim at bin_dir/git that blocks on _lib_gate_diff_base's initial
    `git rev-parse --absolute-git-dir` call."""
    return _make_git_blocking_on_arg(bin_dir, "--absolute-git-dir")


def _make_blocking_tree_verify_git(bin_dir: Path) -> Path:
    """Shim at bin_dir/git that blocks on _lib_gate_diff_base's final
    `git rev-parse --verify --quiet "${tree_oid}^{tree}"` validation call."""
    return _make_git_blocking_on_arg(bin_dir, '*"^{tree}"')


def _make_call_logging_git(bin_dir: Path, log_file: Path) -> Path:
    """Shim at bin_dir/git: appends the full argument list to log_file, one
    invocation per line, then proxies to the real git."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    shim = bin_dir / "git"
    shim.write_text(
        '#!/bin/bash\n'
        f'printf "%s\\n" "$*" >> "{log_file}"\n'
        'exec "$REAL_GIT" "$@"\n'
    )
    shim.chmod(0o755)
    return shim


class TestGateDiffBaseMergeTreeFailure:
    """A real merge-tree failure falls back to the empty base."""

    @staticmethod
    def _repo_with_a_root_commit_and_a_second_commit(tmp_path: Path) -> tuple[Path, str, str]:
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        root_oid = _run_git(repo, "rev-parse", "HEAD").strip()
        (repo / "f.txt").write_text("y\n")
        _run_git(repo, "add", "f.txt")
        _run_git(repo, "commit", "-qm", "second")
        second_oid = _run_git(repo, "rev-parse", "HEAD").strip()
        return repo, root_oid, second_oid

    @pytest.mark.parametrize("state", ["rebase", "cherry-pick", "revert"])
    def test_root_commit_state_oid_falls_back_to_empty_base(
        self, tmp_path: Path, state: str
    ) -> None:
        """REBASE_HEAD^/CHERRY_PICK_HEAD^/REVERT_HEAD^ is invalid when the
        state ref names a root commit (no parent) -- a git-primitive fact,
        so the merge-tree call this design issues fails."""
        repo, root_oid, _ = self._repo_with_a_root_commit_and_a_second_commit(tmp_path)
        _forge_state_marker(repo / ".git", state, root_oid)
        result = _gate_diff_base(repo)
        assert result.returncode == 1
        assert result.stdout == ""

    @pytest.mark.parametrize("state", ["rebase", "cherry-pick", "revert"])
    def test_same_forge_with_a_state_oid_that_has_a_parent_computes_a_base(
        self, tmp_path: Path, state: str
    ) -> None:
        """Control for the root-commit test above: the identical forged
        marker, but naming the second commit, reaches merge-tree and gets
        a tree back. So that test's exit 1 comes from the merge-tree
        failure, not from an earlier return."""
        repo, _, second_oid = self._repo_with_a_root_commit_and_a_second_commit(tmp_path)
        _forge_state_marker(repo / ".git", state, second_oid)
        result = _gate_diff_base(repo)
        assert result.returncode == 0
        _assert_valid_tree_oid(repo, result.stdout)

    def test_merge_of_an_unrelated_history_falls_back_to_empty_base(self, tmp_path: Path) -> None:
        """origin/<default> forged at an orphan commit makes MERGE_HEAD pass
        the anchor check, and `merge-tree` then refuses to merge unrelated
        histories."""
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        _run_git(repo, "checkout", "-q", "--orphan", "orphan")
        (repo / "orphan.txt").write_text("z\n")
        _run_git(repo, "add", "orphan.txt")
        _run_git(repo, "commit", "-qm", "orphan commit")
        orphan_oid = _run_git(repo, "rev-parse", "HEAD").strip()
        _run_git(repo, "checkout", "-q", "main")
        _run_git(repo, "update-ref", "refs/remotes/origin/main", orphan_oid)
        _forge_state_marker(repo / ".git", "merge", orphan_oid)
        result = _gate_diff_base(repo)
        assert result.returncode == 1
        assert result.stdout == ""

    def test_same_merge_forge_with_a_related_history_computes_a_base(self, tmp_path: Path) -> None:
        """Control for the unrelated-history merge test above: origin/<default>
        and MERGE_HEAD forged at a commit descending from HEAD reach
        merge-tree and get a tree back."""
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        _run_git(repo, "checkout", "-qb", "side")
        (repo / "side.txt").write_text("s\n")
        _run_git(repo, "add", "side.txt")
        _run_git(repo, "commit", "-qm", "side commit")
        side_oid = _run_git(repo, "rev-parse", "HEAD").strip()
        _run_git(repo, "checkout", "-q", "main")
        _run_git(repo, "update-ref", "refs/remotes/origin/main", side_oid)
        _forge_state_marker(repo / ".git", "merge", side_oid)
        result = _gate_diff_base(repo)
        assert result.returncode == 0
        _assert_valid_tree_oid(repo, result.stdout)


class TestGateDiffBaseCapFaultInjection:
    @pytest.mark.timing
    @pytest.mark.skipif(
        not _timeout_binary_present(), reason="no timeout/gtimeout on PATH to fire the cap"
    )
    def test_blocking_merge_tree_returns_undetermined_with_empty_stdout(
        self, tmp_path: Path
    ) -> None:
        """Proves the safety property directly -- a partial or candidate
        OID interrupted mid-write must never escape onto stdout, since
        every caller consumes stdout unconditionally regardless of exit
        status. timeout=30 bounds a cap regression to a fast failure
        instead of a 20s hang (the shim's own sleep)."""
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        build_conflicted_revert(repo)
        bin_dir = tmp_path / "bin-blocking-merge-tree"
        _make_blocking_merge_tree_git(bin_dir)
        env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "REAL_GIT": shutil.which("git")}
        result = _gate_diff_base(repo, env=env, timeout=30)
        assert result.returncode == 2
        assert result.stdout == ""

    @pytest.mark.timing
    @pytest.mark.skipif(
        not _timeout_binary_present(), reason="no timeout/gtimeout on PATH to fire the cap"
    )
    def test_blocking_absolute_git_dir_returns_undetermined_with_empty_stdout(
        self, tmp_path: Path
    ) -> None:
        """Same safety property as the merge-tree case above, for
        _lib_gate_diff_base's initial `git rev-parse --absolute-git-dir`
        call -- its own independent `|| return 2` must not let a partial
        gitdir escape onto stdout either."""
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        bin_dir = tmp_path / "bin-blocking-absolute-git-dir"
        _make_blocking_absolute_git_dir_git(bin_dir)
        env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "REAL_GIT": shutil.which("git")}
        result = _gate_diff_base(repo, env=env, timeout=30)
        assert result.returncode == 2
        assert result.stdout == ""

    @pytest.mark.timing
    @pytest.mark.skipif(
        not _timeout_binary_present(), reason="no timeout/gtimeout on PATH to fire the cap"
    )
    def test_blocking_tree_verify_returns_undetermined_with_empty_stdout(
        self, tmp_path: Path
    ) -> None:
        """Same safety property as the merge-tree case above, for
        _lib_gate_diff_base's final `git rev-parse --verify --quiet
        "${tree_oid}^{tree}"` validation call -- its own independent
        exit-code-match block must not let the unvalidated tree_oid escape
        onto stdout either."""
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        build_conflicted_revert(repo)
        bin_dir = tmp_path / "bin-blocking-tree-verify"
        _make_blocking_tree_verify_git(bin_dir)
        env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "REAL_GIT": shutil.which("git")}
        result = _gate_diff_base(repo, env=env, timeout=30)
        assert result.returncode == 2
        assert result.stdout == ""


class TestGateDiffBaseCapKillStatusesAreUndetermined:
    """Every status a cap kill can produce -- 124 (GNU SIGTERM kill), 143
    (BusyBox SIGTERM kill), and 137 (SIGKILL after the grace, on both) --
    must read as undetermined (exit 2), not as the "no override" answer
    (exit 1). See _lib_capped_for's header in _lib.sh for the mapping. No
    real cap is engaged: the shim exits with the status directly."""

    @pytest.mark.parametrize("cap_kill_status", [124, 137, 143])
    @pytest.mark.parametrize(
        "arg_pattern",
        ["merge-tree", '*"^{tree}"'],
        ids=["merge-tree", "tree-verify"],
    )
    def test_cap_kill_status_returns_undetermined_with_empty_stdout(
        self, tmp_path: Path, arg_pattern: str, cap_kill_status: int
    ) -> None:
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        build_conflicted_revert(repo)
        bin_dir = tmp_path / "bin-exiting-with-status"
        _make_git_exiting_with_status(bin_dir, arg_pattern, cap_kill_status)
        env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "REAL_GIT": shutil.which("git")}
        result = _gate_diff_base(repo, env=env)
        assert result.returncode == 2
        assert result.stdout == ""


class TestGateDiffBaseMergeTreeSkippedWhenAnchorFails:
    def test_merge_tree_never_invoked_when_state_reaches_no_anchor(
        self, tmp_path: Path
    ) -> None:
        """merge-tree runs only after an ancestry check succeeds, never
        unconditionally and discarded on failure -- otherwise the 5s
        cap and merge-tree's rename-detection cost would be charged on
        every ordinary conflicted rebase, the exact arm this design newly
        gates."""
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        build_conflicted_cherry_pick(repo)  # source never pushed: untrusted
        log_file = tmp_path / "git-calls.log"
        bin_dir = tmp_path / "bin-logging"
        _make_call_logging_git(bin_dir, log_file)
        env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "REAL_GIT": shutil.which("git")}
        result = _gate_diff_base(repo, env=env)
        assert result.returncode == 1
        calls = log_file.read_text() if log_file.exists() else ""
        assert "merge-tree" not in calls


class TestGateDiffBaseSingleCallInvocationCount:
    """One call to _lib_gate_diff_base must not internally invoke state
    detection, merge-tree, or the ancestor checks more than the documented
    number of times -- a per-call invocation-count property. Whether a
    caller resolves the base once per hook invocation and threads it
    through, rather than re-resolving inside a per-file loop, is a property
    of that caller's own call site (e.g. a while-loop body iterating staged
    files), which has no such call site in this codebase yet to test."""

    def test_single_invocation_computes_merge_tree_and_ancestor_check_once(
        self, tmp_path: Path
    ) -> None:
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        build_conflicted_revert(repo)  # HEAD-anchor trusted, no origin configured
        log_file = tmp_path / "git-calls.log"
        bin_dir = tmp_path / "bin-logging"
        _make_call_logging_git(bin_dir, log_file)
        env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "REAL_GIT": shutil.which("git")}
        result = _gate_diff_base(repo, env=env)
        assert result.returncode == 0
        calls = log_file.read_text().splitlines()
        merge_tree_calls = [c for c in calls if "merge-tree" in c]
        ancestor_calls = [c for c in calls if "merge-base" in c and "--is-ancestor" in c]
        assert len(merge_tree_calls) == 1
        # No origin is configured in this fixture, so _lib_default_branch_or_guess
        # returns empty and the origin leg's is-ancestor call never runs --
        # only the HEAD leg does.
        assert len(ancestor_calls) == 1


class TestGateDiffBaseNoCapBinary:
    def test_runs_uncapped_and_still_succeeds_without_timeout_or_gtimeout(
        self, tmp_path: Path
    ) -> None:
        """With neither timeout(1) nor gtimeout(1) on PATH, the wrapped git
        commands run to completion uncapped rather than failing or
        returning a cap-driven status 2 -- proving the *absence* of a cap
        actually degrades to running uncapped, not only that the capped
        path works."""
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        build_conflicted_revert(repo)
        farm_dir = tmp_path / "path-without-timeout"
        farm_dir.mkdir()
        restricted_path = build_path_without("timeout", farm_dir)
        gtimeout_shim = farm_dir / "gtimeout"
        if gtimeout_shim.exists():
            gtimeout_shim.unlink()
        env = {"PATH": restricted_path, "HOME": str(tmp_path)}
        result = _gate_diff_base(repo, env=env)
        assert result.returncode == 0
        assert result.stdout != ""


class TestGateDiffBaseOriginForgeryDocumentedBehavior:
    def test_explicit_destination_fetch_forges_origin_default_and_is_trusted(
        self, tmp_path: Path
    ) -> None:
        """A documented-behavior assertion, not a self-healing guarantee.
        The payload must be a descendant of origin/<default>'s
        current tip -- an explicit-destination fetch to an existing
        remote-tracking ref is still subject to ordinary non-fast-forward
        rejection without a leading + or --force, and this is specifically
        meant to demonstrate the ordinary, unforced form."""
        bare, clone = bare_remote_with_default_branch(tmp_path)
        _run_git(clone, "checkout", "-qb", "payload-branch")
        (clone / "f").write_text("payload\n")
        _run_git(clone, "add", "f")
        _run_git(clone, "commit", "-qm", "unreviewed payload")
        payload_oid = _run_git(clone, "rev-parse", "HEAD").strip()
        _run_git(clone, "checkout", "-q", "main")

        # Ordinary, unforced explicit-destination fetch from the local repo
        # itself -- no attacker infrastructure needed to demonstrate.
        _run_git(clone, "fetch", "-q", ".", "payload-branch:refs/remotes/origin/main")
        assert _run_git(clone, "rev-parse", "origin/main").strip() == payload_oid

        _forge_state_marker(clone / ".git", "merge", payload_oid)
        result = _gate_diff_base(clone)
        assert result.returncode == 0, (
            "the forged origin/main ref must be trusted exactly as a genuine one would be"
        )
        _assert_valid_tree_oid(clone, result.stdout)


class TestReviewerRoundStateKeyDuringRebase:
    """HEAD is detached for the whole duration of a rebase, so
    _lib_reviewer_round_state_key (keyed on branch name) cannot resolve --
    the round-3 architect-consult gate is disarmed for the operation's
    duration. This is a property of _lib_reviewer_round_state_key's
    existing branch-keying design, unrelated to and unaffected by the
    novel-content base the other tests here and in test_lib_gate_diff_base.py cover."""

    def test_plain_rebase_leaves_head_detached(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        build_conflicted_rebase(repo)
        symbolic_ref = subprocess.run(
            ["git", "symbolic-ref", "-q", "--short", "HEAD"], cwd=repo, capture_output=True
        )
        assert symbolic_ref.returncode != 0
        assert reviewer_round_state_key(repo) == ""

    def test_interactive_rebase_leaves_head_detached(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        build_conflicted_rebase(repo, interactive=True)
        symbolic_ref = subprocess.run(
            ["git", "symbolic-ref", "-q", "--short", "HEAD"], cwd=repo, capture_output=True
        )
        assert symbolic_ref.returncode != 0
        assert reviewer_round_state_key(repo) == ""


class TestStagedDiffHash:
    def test_empty_base_matches_production_recipe(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        (repo / "f.txt").write_text("changed\n")
        _run_git(repo, "add", "f.txt")
        result = _staged_diff_hash(repo, "")
        assert result.returncode == 0
        assert result.stdout == staged_diff_hash(repo)

    def test_non_empty_base_matches_independent_oracle(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        tree_oid = _run_git(repo, "rev-parse", "HEAD^{tree}").strip()
        (repo / "f.txt").write_text("changed\n")
        _run_git(repo, "add", "f.txt")
        result = _staged_diff_hash(repo, tree_oid)
        assert result.returncode == 0
        assert result.stdout == staged_diff_hash_at_base(repo, tree_oid)

    def test_pathspec_restricts_diff(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        (repo / "other.txt").write_text("z\n")
        _run_git(repo, "add", "other.txt")
        _run_git(repo, "commit", "-qm", "add other.txt")
        (repo / "f.txt").write_text("changed\n")
        (repo / "other.txt").write_text("changed too\n")
        _run_git(repo, "add", "f.txt", "other.txt")
        result = _staged_diff_hash(repo, "", "f.txt")
        expected_diff = subprocess.run(
            ["git", "diff", "--cached", "--", "f.txt"], cwd=repo, capture_output=True, check=True
        ).stdout
        expected = hashlib.sha256(expected_diff).hexdigest()
        assert result.returncode == 0
        assert result.stdout == expected

    def test_sha256sum_absent_returns_empty_and_fails_closed(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        (repo / "f.txt").write_text("changed\n")
        _run_git(repo, "add", "f.txt")
        farm_dir = tmp_path / "path-without-sha256sum"
        farm_dir.mkdir()
        restricted_path = build_path_without("sha256sum", farm_dir)
        env = {"PATH": restricted_path, "HOME": str(tmp_path)}
        result = _staged_diff_hash(repo, "", env=env)
        assert result.returncode == 1
        assert result.stdout == ""

    @pytest.mark.timing
    @pytest.mark.skipif(
        not _timeout_binary_present(), reason="no timeout/gtimeout on PATH to fire the cap"
    )
    def test_blocking_diff_returns_empty_stdout_not_partial_hash(
        self, tmp_path: Path
    ) -> None:
        """Proves the same safety property TestGateDiffBaseCapFaultInjection
        proves for `merge-tree` -- ${PIPESTATUS[0]} must catch a `git diff`
        killed past the cap so a partial or interrupted diff never gets
        hashed onto stdout. timeout=30 bounds a cap regression to a fast
        failure instead of a 20s hang (the shim's own sleep)."""
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        (repo / "f.txt").write_text("changed\n")
        _run_git(repo, "add", "f.txt")
        bin_dir = tmp_path / "bin-blocking-diff"
        _make_blocking_diff_git(bin_dir)
        env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "REAL_GIT": shutil.which("git")}
        result = _staged_diff_hash(repo, "", env=env, timeout=30)
        assert result.returncode == 1
        assert result.stdout == ""
