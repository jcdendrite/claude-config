"""Tests for review-pr-worktree-remove.py -- the cleanup-only removal
review-pr-finish.sh runs in `checkout` mode, under the same lock
review-pr-worktree-replace.py uses for the identical WORKTREE_DIR.

Mirrors test_review_pr_worktree_replace.py's own usage-error and
lock-timeout tests. The happy path also gets its own direct CLI-level test
here (TestHappyPath below), matching TestWorktreeReplaceHappyPath's weight,
rather than being verified only transitively through the much heavier
bash+HOME+provenance+git harness in test_review_pr_finish.py's
TestCheckoutModeRemovesWorktreeAndArtifacts.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from helpers import SCRIPTS_DIR

from .conftest import _git_shim_that_fails_on_worktree_prune

SCRIPT_PATH = SCRIPTS_DIR / "review-pr-worktree-remove.py"


def _run_cli(repo_root: Path, worktree_dir: Path, session_id: str, deadline_seconds) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), str(repo_root), str(worktree_dir), session_id, str(deadline_seconds)],
        capture_output=True,
        text=True,
    )


def _init_repo(repo: Path) -> str:
    """A repo with one commit -- returns its HEAD sha."""
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    (repo / "file.txt").write_text("first\n")
    subprocess.run(["git", "add", "file.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "first"], cwd=repo, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


def _add_worktree(repo: Path, worktree_dir: Path, sha: str) -> None:
    worktree_dir.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "worktree", "add", "--detach", str(worktree_dir), sha], cwd=repo, check=True)


class TestUsageErrors:
    def test_wrong_argc_exits_two(self, tmp_path):
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "one", "two"], capture_output=True, text=True
        )
        assert result.returncode == 2
        assert "usage" in result.stderr.lower()

    def test_non_numeric_deadline_exits_two(self, tmp_path):
        result = _run_cli(tmp_path, tmp_path / "wt", "session-a", "not-a-number")
        assert result.returncode == 2
        assert "DEADLINE_SECONDS" in result.stderr


class TestHappyPath:
    def test_removes_the_worktree_and_prints_nothing(self, tmp_path):
        repo = tmp_path / "repo"
        sha = _init_repo(repo)
        worktree_dir = repo / ".claude" / "worktrees" / "wt"
        _add_worktree(repo, worktree_dir, sha)

        result = _run_cli(repo, worktree_dir, "session-a", 5)
        assert result.returncode == 0, result.stderr
        assert result.stdout == "", "documented 'prints nothing on success' contract"
        assert not worktree_dir.exists()
        # The lock file itself is never removed -- flock keys on inode, not
        # path, matching test_review_pr_finish.py's own assertion for the
        # identical property.
        assert Path(f"{worktree_dir}.lock").exists()

    def test_missing_worktree_still_exits_zero_with_empty_stdout(self, tmp_path):
        """review-pr-finish.sh only ever calls this script when WORKTREE_DIR
        or its lock file already exists, so the lock file's own parent
        directory is guaranteed to exist -- mirrored here by creating it
        without the worktree itself."""
        repo = tmp_path / "repo"
        _init_repo(repo)
        worktree_dir = repo / ".claude" / "worktrees" / "never-created"
        worktree_dir.parent.mkdir(parents=True)

        result = _run_cli(repo, worktree_dir, "session-a", 5)
        assert result.returncode == 0, result.stderr
        assert result.stdout == ""


class TestOwnerSessionMismatch:
    """WORKTREE_DIR is shared across every session that has ever reviewed a
    given PR -- a non-empty, mismatching owner sidecar means a DIFFERENT,
    still-active session's checkout, which this call must never delete."""

    def test_mismatched_owner_session_skips_removal(self, tmp_path):
        repo = tmp_path / "repo"
        sha = _init_repo(repo)
        worktree_dir = repo / ".claude" / "worktrees" / "wt"
        _add_worktree(repo, worktree_dir, sha)
        owner_file = Path(f"{worktree_dir}.owner")
        owner_file.write_text("session-b\n")

        result = _run_cli(repo, worktree_dir, "session-a", 5)
        assert result.returncode == 3
        assert "session-b" in result.stderr
        assert worktree_dir.exists(), "an owner mismatch must never remove the worktree"
        assert owner_file.read_text() == "session-b\n"

    def test_matching_owner_session_removes_and_clears_the_sidecar(self, tmp_path):
        repo = tmp_path / "repo"
        sha = _init_repo(repo)
        worktree_dir = repo / ".claude" / "worktrees" / "wt"
        _add_worktree(repo, worktree_dir, sha)
        owner_file = Path(f"{worktree_dir}.owner")
        owner_file.write_text("session-a\n")

        result = _run_cli(repo, worktree_dir, "session-a", 5)
        assert result.returncode == 0, result.stderr
        assert not worktree_dir.exists()
        assert not owner_file.exists()

    def test_missing_owner_sidecar_falls_through_to_removal(self, tmp_path):
        """No ownership recorded at all (a worktree pre-dating the sidecar,
        or one review-pr-checkout.sh never finished writing it for) must not
        block cleanup -- matches this check's pre-existing absence."""
        repo = tmp_path / "repo"
        sha = _init_repo(repo)
        worktree_dir = repo / ".claude" / "worktrees" / "wt"
        _add_worktree(repo, worktree_dir, sha)

        result = _run_cli(repo, worktree_dir, "session-a", 5)
        assert result.returncode == 0, result.stderr
        assert not worktree_dir.exists()


class TestGitFailureSurfaces:
    """remove_worktree's RuntimeError on a failed `git worktree prune` (unit-
    tested directly against remove_worktree in test_review_pr_worktree.py)
    must also surface through this script's own CLI: a nonzero exit and the
    underlying git failure on stderr, not a false-success exit 0."""

    def test_failed_prune_exits_nonzero_with_the_git_failure_on_stderr(self, tmp_path):
        repo = tmp_path / "repo"
        _init_repo(repo)
        # Never created: os.path.exists is False, so only the unconditional
        # prune call runs, isolating its own failure.
        worktree_dir = repo / ".claude" / "worktrees" / "never-created"
        worktree_dir.parent.mkdir(parents=True)

        shim_dir = _git_shim_that_fails_on_worktree_prune(tmp_path)
        env = {**os.environ, "PATH": str(shim_dir) + os.pathsep + os.environ.get("PATH", "")}
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), str(repo), str(worktree_dir), "session-a", "5"],
            capture_output=True, text=True, env=env,
        )
        assert result.returncode == 2
        assert "synthetic prune failure" in result.stderr


class TestLockTimeout:
    def test_live_holder_denies_at_the_deadline_without_touching_the_worktree(self, tmp_path):
        worktree_dir = tmp_path / "wt"
        worktree_dir.mkdir()
        (worktree_dir / "marker.txt").write_text("still here\n")
        lock_path = Path(f"{worktree_dir}.lock")

        holder = subprocess.Popen(
            [
                sys.executable, "-c",
                "import fcntl, sys, time\n"
                "f = open(sys.argv[1], 'a+')\n"
                "fcntl.flock(f, fcntl.LOCK_EX)\n"
                "print('locked', flush=True)\n"
                "time.sleep(float(sys.argv[2]))\n",
                str(lock_path), "10",
            ],
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            assert holder.stdout.readline().strip() == "locked"
            result = _run_cli(tmp_path, worktree_dir, "session-a", 1)
            assert result.returncode != 0
            assert "could not acquire the worktree lock" in result.stderr
            assert (worktree_dir / "marker.txt").exists(), "a lock-timeout abort must never touch the worktree"
        finally:
            holder.wait(timeout=15)


class TestOwnershipCheckAtomicWithLock:
    """The ownership comparison runs only after this script's own lock is
    held, not before the lock is even requested.

    Proves this by mutating the owner sidecar (and the worktree's own
    content) while a removal call sits blocked waiting for a lock a holder
    process controls. The removal call must see the mutated value once it
    finally acquires the lock, not whatever the sidecar held before the
    mutation.
    """

    def test_owner_file_mutated_while_blocked_on_the_lock_is_still_observed(self, tmp_path):
        repo = tmp_path / "repo"
        sha = _init_repo(repo)
        worktree_dir = repo / ".claude" / "worktrees" / "wt"
        _add_worktree(repo, worktree_dir, sha)
        owner_file = Path(f"{worktree_dir}.owner")
        owner_file.write_text("session-a\n")
        lock_path = Path(f"{worktree_dir}.lock")

        holder = subprocess.Popen(
            [
                sys.executable, "-c",
                "import fcntl, sys, time\n"
                "f = open(sys.argv[1], 'a+')\n"
                "fcntl.flock(f, fcntl.LOCK_EX)\n"
                "print('locked', flush=True)\n"
                "time.sleep(float(sys.argv[2]))\n",
                str(lock_path), "2",
            ],
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            assert holder.stdout.readline().strip() == "locked"

            # A removal call for the ORIGINAL owner ("session-a") starts now
            # and blocks behind the holder's lock.
            removal = subprocess.Popen(
                [
                    sys.executable, str(SCRIPT_PATH), str(repo), str(worktree_dir), "session-a", "10",
                ],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )

            # While the removal call is still blocked, simulate a concurrent
            # checkout completing entirely: it replaces the worktree's
            # content and overwrites the owner sidecar with a new, still-
            # active session.
            (worktree_dir / "canary.txt").write_text("new checkout content\n")
            owner_file.write_text("session-b\n")

            holder.wait(timeout=15)
            stdout, stderr = removal.communicate(timeout=15)
        finally:
            holder.wait(timeout=15)

        assert removal.returncode == 3, (stdout, stderr)
        assert "session-b" in stderr
        assert worktree_dir.exists(), "the concurrent checkout's new worktree must survive"
        assert (worktree_dir / "canary.txt").exists()
        assert owner_file.read_text() == "session-b\n"
