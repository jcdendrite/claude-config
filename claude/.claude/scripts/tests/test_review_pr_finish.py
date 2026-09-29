"""Tests for review-pr-finish.sh -- the single cleanup call for
/review-pr's deliver step, run on every exit path (posted, declined, or
aborted). Idempotent, and safe to run with nothing in flight.

The worktree sweep's selection rules (which paths count as this session's)
are covered against synthetic porcelain text in test_lib.py's
TestLibReviewPrSelectSessionWorktrees; the subprocess tests here use real
git and cover one case per branch of the sweep itself.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest
from helpers import (
    SCRIPTS_DIR,
    git_main_tree_root,
    head_sha,
    review_pr_completion_marker_path,
    write_review_pr_provenance,
)

from .conftest import _git_shim_that_fails_on_worktree_subcommand, _seed_session

SCRIPT = SCRIPTS_DIR / "review-pr-finish.sh"
PR_IDENTITY = "foo/bar#42"
SID = "test-session-review-pr-finish"
OTHER_SID = "test-session-someone-else"


@pytest.fixture
def isolated_home(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    return home


def _build_repo(tmp_path: Path) -> tuple[Path, str]:
    """A repo with one commit -- returns (repo, head_sha)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    (repo / "file.txt").write_text("main\n")
    subprocess.run(["git", "add", "file.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo, head_sha(repo)


def _add_review_worktree(
    repo: Path, sha: str, session_id: str = SID, pr_number: str = "42", suffix: str = "aB3dE9",
) -> Path:
    """A linked worktree at the shape review-pr-checkout.sh creates:
    <repo>/.claude/worktrees/review-pr-<session-id>-<number>-<suffix>."""
    worktree_dir = repo / ".claude" / "worktrees" / f"review-pr-{session_id}-{pr_number}-{suffix}"
    worktree_dir.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "worktree", "add", "--detach", str(worktree_dir), sha], cwd=repo, check=True)
    return worktree_dir


def _add_neighbors(repo: Path, sha: str) -> tuple[list[Path], Path]:
    """The four shapes finish must never remove: a registered worktree of
    another session, a registered worktree of a session whose id extends
    this one's, a registered worktree carrying this session's name prefix
    with a suffix one character short of mktemp's six, and a plain
    unregistered directory carrying this session's name prefix. Returns
    (the three registered worktrees, the plain directory, which holds
    keep.txt)."""
    other_session = _add_review_worktree(repo, sha, session_id=OTHER_SID)
    prefix_extended_session = _add_review_worktree(repo, sha, session_id=f"{SID}-extra")
    broken_tail_lookalike = _add_review_worktree(repo, sha, suffix="aB3dE")
    plain_directory = repo / ".claude" / "worktrees" / f"review-pr-{SID}-notes"
    plain_directory.mkdir()
    (plain_directory / "keep.txt").write_text("keep\n")
    return [other_session, prefix_extended_session, broken_tail_lookalike], plain_directory


def _assert_neighbors_survive(repo: Path, registered_neighbors: list[Path], plain_directory: Path) -> None:
    registered = _registered_worktree_paths(repo)
    for neighbor in registered_neighbors:
        assert neighbor.exists(), f"{neighbor.name} is not this session's and must not be deleted"
        assert str(neighbor) in registered, f"{neighbor.name} must stay registered"
    assert (plain_directory / "keep.txt").exists(), "an unregistered directory must not be swept"


def _registered_worktree_paths(repo: Path) -> list[str]:
    listing = subprocess.run(
        ["git", "worktree", "list", "--porcelain"], cwd=repo, capture_output=True, text=True, check=True,
    ).stdout
    return [line.removeprefix("worktree ") for line in listing.splitlines() if line.startswith("worktree ")]


def _write_provenance(home: Path, mode: str, head_ref_oid: str, session_id: str = SID) -> Path:
    return write_review_pr_provenance(home, PR_IDENTITY, head_ref_oid, 999, mode=mode, session_id=session_id)


def _write_artifacts(home: Path, session_id: str = SID) -> list[Path]:
    active_dir = home / ".claude" / ".review-pr-active.d"
    active_dir.mkdir(parents=True, exist_ok=True)
    paths = [
        active_dir / f"{session_id}.body",
        active_dir / f"{session_id}.diff",
        active_dir / f"{session_id}.context.json",
    ]
    for p in paths:
        p.write_text("content\n")
    return paths


def _write_completion_marker(home: Path, main_repo: Path, session_id: str = SID) -> Path:
    marker = review_pr_completion_marker_path(home, main_repo, session_id)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(f"{PR_IDENTITY}\nabc\ndef\ncheckout\n")
    return marker


def _provenance_path(home: Path, session_id: str = SID) -> Path:
    return home / ".claude" / ".review-pr-active.d" / f"{session_id}.provenance"


def _run(cwd: Path, home: Path, *, path_prefix: Path | None = None) -> subprocess.CompletedProcess:
    """path_prefix, when given, is prepended to a real, inherited PATH --
    needed only when a test must override one specific external tool (git,
    rm, sha256sum) the script or a helper it shells out to calls
    internally."""
    env = {"HOME": str(home)}
    if path_prefix is not None:
        env["PATH"] = f"{path_prefix}{os.pathsep}{os.environ.get('PATH', '')}"
    return subprocess.run(
        ["bash", str(SCRIPT)], cwd=cwd, env=env, capture_output=True, text=True, timeout=60,
    )


def _sha256sum_shim_that_produces_no_output(tmp_path: Path) -> Path:
    """A `sha256sum` PATH shim producing no output on every invocation.
    _lib_hash_diff_text treats empty sha256sum output as a hashing failure,
    so this forces _marker_lib_repo_hash to fail the same way a genuine
    sha256sum misbehavior would."""
    shim_dir = tmp_path / "sha256sum_shim"
    shim_dir.mkdir()
    shim = shim_dir / "sha256sum"
    shim.write_text("#!/usr/bin/env bash\nexit 0\n")
    shim.chmod(0o755)
    return shim_dir


def _rm_shim_that_fails_on_recursive_delete(tmp_path: Path) -> Path:
    """An `rm` PATH shim failing every `rm -rf`, delegating any other
    invocation (the script's own artifact `rm -f`) to the real rm."""
    real_rm = shutil.which("rm")
    shim_dir = tmp_path / "rm_shim"
    shim_dir.mkdir()
    shim = shim_dir / "rm"
    shim.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env bash
        if [[ "$1" == "-rf" ]]; then
          echo "synthetic rm failure" >&2
          exit 1
        fi
        exec {shlex.quote(real_rm)} "$@"
    """))
    shim.chmod(0o755)
    return shim_dir


class TestUsageErrors:
    def test_any_argument_exits_two_with_usage(self, isolated_home, tmp_path):
        _seed_session(isolated_home, SID)
        env = {"HOME": str(isolated_home)}
        result = subprocess.run(
            ["bash", str(SCRIPT), "unexpected"], cwd=tmp_path, env=env, capture_output=True, text=True,
            timeout=60,
        )
        assert result.returncode == 2
        assert "Usage" in result.stderr


class TestNothingInFlight:
    def test_exits_zero_with_no_provenance_at_all(self, isolated_home, tmp_path):
        _seed_session(isolated_home, SID)
        result = _run(tmp_path, isolated_home)
        assert result.returncode == 0, result.stderr

    def test_is_idempotent_across_two_consecutive_runs(self, isolated_home, tmp_path):
        _seed_session(isolated_home, SID)
        first = _run(tmp_path, isolated_home)
        assert first.returncode == 0, first.stderr
        second = _run(tmp_path, isolated_home)
        assert second.returncode == 0, second.stderr


class TestArtifactAndMarkerRemoval:
    def test_removes_provenance_artifacts_and_marker(self, isolated_home, tmp_path):
        repo, sha = _build_repo(tmp_path)
        _seed_session(isolated_home, SID)
        _write_provenance(isolated_home, mode="checkout", head_ref_oid=sha)
        artifact_paths = _write_artifacts(isolated_home)
        marker = _write_completion_marker(isolated_home, repo)

        result = _run(repo, isolated_home)
        assert result.returncode == 0, result.stderr

        for p in artifact_paths:
            assert not p.exists()
        assert not _provenance_path(isolated_home).exists()
        assert not marker.exists()

    def test_marker_is_removed_when_run_from_inside_a_linked_worktree(self, isolated_home, tmp_path):
        """The marker is keyed to the main tree's root, so running from a
        linked worktree removes the same marker."""
        repo, sha = _build_repo(tmp_path)
        _seed_session(isolated_home, SID)
        marker = _write_completion_marker(isolated_home, repo)
        linked_worktree = tmp_path / "linked-session-worktree"
        subprocess.run(["git", "worktree", "add", "--detach", str(linked_worktree), sha], cwd=repo, check=True)

        result = _run(linked_worktree, isolated_home)
        assert result.returncode == 0, result.stderr
        assert not marker.exists()


class TestSessionWideWorktreeSweep:
    def test_removes_the_sessions_own_worktree_even_when_run_from_inside_it(self, isolated_home, tmp_path):
        repo, sha = _build_repo(tmp_path)
        _seed_session(isolated_home, SID)
        worktree_dir = _add_review_worktree(repo, sha)

        result = _run(worktree_dir, isolated_home)
        assert result.returncode == 0, result.stderr

        assert not worktree_dir.exists()
        assert str(worktree_dir) not in _registered_worktree_paths(repo)
        assert "found 1 review worktree(s)" in result.stderr
        assert f"removed review worktree {worktree_dir}" in result.stderr

    def test_leaves_other_sessions_and_non_matching_directories_untouched(self, isolated_home, tmp_path):
        repo, sha = _build_repo(tmp_path)
        _seed_session(isolated_home, SID)
        own_worktree = _add_review_worktree(repo, sha)
        registered_neighbors, plain_directory = _add_neighbors(repo, sha)

        result = _run(repo, isolated_home)
        assert result.returncode == 0, result.stderr

        assert not own_worktree.exists()
        _assert_neighbors_survive(repo, registered_neighbors, plain_directory)

    def test_leaves_other_sessions_artifacts_and_marker_untouched(self, isolated_home, tmp_path):
        repo, _ = _build_repo(tmp_path)
        _seed_session(isolated_home, SID)
        other_artifacts = _write_artifacts(isolated_home, OTHER_SID)
        other_marker = _write_completion_marker(isolated_home, repo, OTHER_SID)

        result = _run(repo, isolated_home)
        assert result.returncode == 0, result.stderr

        for p in other_artifacts:
            assert p.exists(), f"{p.name} belongs to another session and must survive"
        assert other_marker.exists()

    def test_zero_matches_is_logged_with_the_inspected_directory(self, isolated_home, tmp_path):
        repo, _ = _build_repo(tmp_path)
        _seed_session(isolated_home, SID)

        result = _run(repo, isolated_home)
        assert result.returncode == 0, result.stderr

        assert "found 0 review worktree(s)" in result.stderr
        assert f"{git_main_tree_root(repo)}/.claude/worktrees" in result.stderr

    def test_two_worktrees_in_one_session_are_both_removed_and_the_count_is_logged(
        self, isolated_home, tmp_path
    ):
        repo, sha = _build_repo(tmp_path)
        _seed_session(isolated_home, SID)
        first = _add_review_worktree(repo, sha, pr_number="42", suffix="AAAAAA")
        second = _add_review_worktree(repo, sha, pr_number="7", suffix="BBBBBB")

        result = _run(repo, isolated_home)
        assert result.returncode == 0, result.stderr

        assert not first.exists()
        assert not second.exists()
        assert "found 2 review worktree(s)" in result.stderr
        assert f"removed review worktree {first}" in result.stderr
        assert f"removed review worktree {second}" in result.stderr

    def test_a_failed_removal_leaves_the_worktree_for_a_retry(self, isolated_home, tmp_path):
        """Both `git worktree remove` and the `rm -rf` fallback fail, so the
        worktree stays registered and on disk, discoverable by the next run
        -- which, with the failure gone, removes it."""
        repo, sha = _build_repo(tmp_path)
        _seed_session(isolated_home, SID)
        worktree_dir = _add_review_worktree(repo, sha)
        artifact_paths = _write_artifacts(isolated_home)
        failing_git = _git_shim_that_fails_on_worktree_subcommand(tmp_path, "remove")
        failing_rm = _rm_shim_that_fails_on_recursive_delete(tmp_path)
        shim_dir = tmp_path / "combined_shims"
        shim_dir.mkdir()
        (shim_dir / "git").symlink_to(failing_git / "git")
        (shim_dir / "rm").symlink_to(failing_rm / "rm")

        first = _run(repo, isolated_home, path_prefix=shim_dir)
        assert first.returncode == 0, first.stderr
        assert f"could not remove review worktree {worktree_dir}" in first.stderr
        assert worktree_dir.exists()
        assert str(worktree_dir) in _registered_worktree_paths(repo)
        for p in artifact_paths:
            assert not p.exists(), "artifact cleanup must proceed when a worktree removal fails"

        second = _run(repo, isolated_home)
        assert second.returncode == 0, second.stderr
        assert not worktree_dir.exists()
        assert str(worktree_dir) not in _registered_worktree_paths(repo)

    def test_git_removal_failure_falls_back_to_deleting_only_this_sessions_directory_and_pruning(
        self, isolated_home, tmp_path
    ):
        """The fallback is finish's only `rm -rf`, so it runs here with
        every neighbor shape present: a widened delete would take one."""
        repo, sha = _build_repo(tmp_path)
        _seed_session(isolated_home, SID)
        worktree_dir = _add_review_worktree(repo, sha)
        registered_neighbors, plain_directory = _add_neighbors(repo, sha)

        result = _run(
            repo, isolated_home, path_prefix=_git_shim_that_fails_on_worktree_subcommand(tmp_path, "remove"),
        )
        assert result.returncode == 0, result.stderr

        assert not worktree_dir.exists()
        assert "deleted the directory instead" in result.stderr
        # The registration outlives a plain `rm -rf` unless the fallback's
        # own `git worktree prune` ran afterwards.
        assert str(worktree_dir) not in _registered_worktree_paths(repo)
        _assert_neighbors_survive(repo, registered_neighbors, plain_directory)

    def test_fallback_on_a_locked_worktree_warns_that_a_registration_may_remain(
        self, isolated_home, tmp_path
    ):
        """`git worktree prune` skips a locked registration, so a fallback
        delete of a locked worktree can leave the registration behind. The
        message must not present the worktree as fully handled, and a later
        unshimmed run must clear the leftover registration."""
        repo, sha = _build_repo(tmp_path)
        _seed_session(isolated_home, SID)
        worktree_dir = _add_review_worktree(repo, sha)
        subprocess.run(
            ["git", "worktree", "lock", "--reason", "held by another session", str(worktree_dir)],
            cwd=repo, check=True,
        )

        result = _run(
            repo, isolated_home, path_prefix=_git_shim_that_fails_on_worktree_subcommand(tmp_path, "remove"),
        )
        assert result.returncode == 0, result.stderr

        assert not worktree_dir.exists()
        assert "deleted the directory instead" in result.stderr
        assert "registration for it may remain" in result.stderr
        assert "a later run of this script retries it" in result.stderr
        assert str(worktree_dir) in _registered_worktree_paths(repo), "the prune skips a locked registration"

        retry = _run(repo, isolated_home)
        assert retry.returncode == 0, retry.stderr
        assert str(worktree_dir) not in _registered_worktree_paths(repo), retry.stderr

    def test_a_locked_worktree_is_removed_by_git_rather_than_the_fallback(self, isolated_home, tmp_path):
        """A single `--force` refuses a locked worktree, and the collision
        guard locks a review worktree once the session writes into it, so
        finish must force twice and leave no registration behind."""
        repo, sha = _build_repo(tmp_path)
        _seed_session(isolated_home, SID)
        worktree_dir = _add_review_worktree(repo, sha)
        subprocess.run(
            ["git", "worktree", "lock", "--reason", "held by another session", str(worktree_dir)],
            cwd=repo, check=True,
        )

        result = _run(repo, isolated_home)
        assert result.returncode == 0, result.stderr

        assert not worktree_dir.exists()
        assert str(worktree_dir) not in _registered_worktree_paths(repo)
        assert f"removed review worktree {worktree_dir}" in result.stderr
        assert "deleted the directory instead" not in result.stderr

    def test_a_registration_whose_directory_is_already_gone_is_cleared(self, isolated_home, tmp_path):
        """A user's own `rm -rf` of a review worktree leaves it registered;
        the next finish must still clear the registration."""
        repo, sha = _build_repo(tmp_path)
        _seed_session(isolated_home, SID)
        worktree_dir = _add_review_worktree(repo, sha)
        shutil.rmtree(worktree_dir)
        assert str(worktree_dir) in _registered_worktree_paths(repo)

        result = _run(repo, isolated_home)
        assert result.returncode == 0, result.stderr

        assert "found 1 review worktree(s)" in result.stderr
        assert str(worktree_dir) not in _registered_worktree_paths(repo)

    def test_prune_failure_after_the_fallback_is_reported_and_still_exits_zero(
        self, isolated_home, tmp_path
    ):
        repo, sha = _build_repo(tmp_path)
        _seed_session(isolated_home, SID)
        worktree_dir = _add_review_worktree(repo, sha)
        artifact_paths = _write_artifacts(isolated_home)

        result = _run(
            repo, isolated_home,
            path_prefix=_git_shim_that_fails_on_worktree_subcommand(tmp_path, "remove", "prune"),
        )
        assert result.returncode == 0, result.stderr

        assert not worktree_dir.exists()
        assert "git worktree prune failed" in result.stderr
        for p in artifact_paths:
            assert not p.exists()


class TestInteriorFailureStillReachesExitZero:
    """The usage banner promises "Always exits 0, whether or not anything
    was in flight" -- pinned here against each interior failure after the
    session is known that `set -e` would otherwise propagate: an
    uncomputable marker key, an unresolvable main tree root, a failing
    `git worktree list`, and a failing `rm -f` on the marker or an
    artifact. The first three are guarded by their own `if ...; then ...
    else ...`, the last by `|| true`."""

    def test_repo_hash_computation_failure_skips_only_the_marker_cleanup(self, isolated_home, tmp_path):
        repo, sha = _build_repo(tmp_path)
        _seed_session(isolated_home, SID)
        _write_provenance(isolated_home, mode="checkout", head_ref_oid=sha)
        artifact_paths = _write_artifacts(isolated_home)
        marker = _write_completion_marker(isolated_home, repo)
        worktree_dir = _add_review_worktree(repo, sha)

        result = _run(repo, isolated_home, path_prefix=_sha256sum_shim_that_produces_no_output(tmp_path))

        assert result.returncode == 0, result.stderr
        assert "could not compute the review-pr marker key" in result.stderr
        for p in artifact_paths:
            assert not p.exists()
        assert not _provenance_path(isolated_home).exists()
        assert not worktree_dir.exists(), "the worktree sweep does not depend on the marker key"
        assert marker.exists(), "the hash never resolved, so the marker path is unknown and must survive"

    def test_worktree_list_failure_skips_the_sweep_but_still_removes_the_files(self, isolated_home, tmp_path):
        repo, sha = _build_repo(tmp_path)
        _seed_session(isolated_home, SID)
        _write_provenance(isolated_home, mode="checkout", head_ref_oid=sha)
        artifact_paths = _write_artifacts(isolated_home)
        marker = _write_completion_marker(isolated_home, repo)
        worktree_dir = _add_review_worktree(repo, sha)

        result = _run(
            repo, isolated_home, path_prefix=_git_shim_that_fails_on_worktree_subcommand(tmp_path, "list"),
        )

        assert result.returncode == 0, result.stderr
        assert "could not list this repository's worktrees" in result.stderr
        for p in artifact_paths:
            assert not p.exists()
        assert not _provenance_path(isolated_home).exists()
        assert not marker.exists()
        assert worktree_dir.exists(), "a skipped sweep deletes nothing"

    @pytest.mark.parametrize("unremovable", ["artifact", "marker"])
    def test_an_unremovable_file_does_not_stop_the_rest_of_the_cleanup(
        self, isolated_home, tmp_path, unremovable
    ):
        repo, sha = _build_repo(tmp_path)
        _seed_session(isolated_home, SID)
        _write_provenance(isolated_home, mode="checkout", head_ref_oid=sha)
        artifact_paths = _write_artifacts(isolated_home)
        marker = _write_completion_marker(isolated_home, repo)
        worktree_dir = _add_review_worktree(repo, sha)
        # `rm -f` refuses a directory, so one sitting at the path makes that
        # removal fail even when the tests run as root.
        blocked = marker if unremovable == "marker" else artifact_paths[0]
        blocked.unlink()
        blocked.mkdir()
        (blocked / "occupant.txt").write_text("occupant\n")

        result = _run(repo, isolated_home)

        assert result.returncode == 0, result.stderr
        assert blocked.is_dir()
        for removable in [*artifact_paths, marker]:
            if removable != blocked:
                assert not removable.exists(), f"{removable.name} must still be removed"
        assert not _provenance_path(isolated_home).exists()
        assert not worktree_dir.exists(), "the worktree sweep must still run"

    def test_outside_a_git_repository_still_removes_artifacts(self, isolated_home, tmp_path):
        _seed_session(isolated_home, SID)
        _write_provenance(isolated_home, mode="diff-only", head_ref_oid="a" * 40)
        artifact_paths = _write_artifacts(isolated_home)

        result = _run(tmp_path, isolated_home)

        assert result.returncode == 0, result.stderr
        assert "could not compute the review-pr marker key" in result.stderr
        assert "could not resolve this repository's main tree root" in result.stderr
        for p in artifact_paths:
            assert not p.exists()


class TestInteriorFailureBeforeSessionIsKnownStillExitsZero:
    """Two more "Always exits 0" guards, each a step upstream of session/
    provenance resolution: CONFIG_DIR and SESSION_ID. Without either guard,
    `set -e` would abort the script non-zero before any cleanup is even
    attempted -- and there is nothing to clean up in either case, since
    CONFIG_DIR/SESSION_ID are what locate every other artifact."""

    def test_config_dir_resolution_failure_exits_zero(self, tmp_path):
        result = subprocess.run(
            ["bash", str(SCRIPT)], cwd=tmp_path, env={"HOME": ""}, capture_output=True, text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr
        assert "could not resolve the Claude Code config directory" in result.stderr

    def test_session_id_resolution_failure_exits_zero(self, isolated_home, tmp_path):
        """isolated_home is a valid, resolvable $HOME with no session file
        seeded under it (no _seed_session call) -- resolve-session-id's own
        ancestor walk finds no match."""
        result = _run(tmp_path, isolated_home)
        assert result.returncode == 0, result.stderr
        assert "could not resolve this session's id" in result.stderr
