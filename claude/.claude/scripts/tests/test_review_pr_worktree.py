"""Direct unit tests for _review_pr_worktree.py's acquire_lock -- the
fcntl.flock-based mutual-exclusion primitive review-pr-worktree-replace.py
and review-pr-worktree-remove.py both import rather than each carrying
their own copy. Moved out of test_review_pr_worktree_replace.py (which
keeps only replace.py's own CLI-level tests) since this primitive is
shared, not owned by either caller.

Importable directly as `_review_pr_worktree` (a valid Python identifier,
unlike either dash-named caller script) since claude/.claude/scripts is on
pythonpath.
"""
from __future__ import annotations

import fcntl
import subprocess
import time
from pathlib import Path

import _review_pr_worktree
import pytest
from _review_pr_worktree import acquire_lock, remove_worktree


class TestAcquireLockUnit:
    def test_returns_a_locked_file_object(self, tmp_path: Path) -> None:
        lock_path = tmp_path / "x.lock"
        lock_f = acquire_lock(str(lock_path), 5.0)
        assert lock_f is not None
        assert lock_path.exists()
        fcntl.flock(lock_f, fcntl.LOCK_UN)
        lock_f.close()

    def test_returns_none_on_timeout_against_an_already_held_lock(self, tmp_path: Path) -> None:
        lock_path = tmp_path / "x.lock"
        holder_f = open(lock_path, "a+")  # noqa: SIM115 -- held across the assertions below, closed in `finally`
        fcntl.flock(holder_f, fcntl.LOCK_EX)
        try:
            started = time.monotonic()
            result = acquire_lock(str(lock_path), 0.3)
            elapsed = time.monotonic() - started
            assert result is None
            assert elapsed < 2
        finally:
            fcntl.flock(holder_f, fcntl.LOCK_UN)
            holder_f.close()

    def test_raises_oserror_for_a_symlink_at_the_lock_path_rather_than_following_it(
        self, tmp_path: Path
    ) -> None:
        real = tmp_path / "real.lock"
        link = tmp_path / "x.lock"
        link.symlink_to(real)
        with pytest.raises(OSError):
            acquire_lock(str(link), 5.0)


class TestRemoveWorktreeRuntimeErrors:
    """remove_worktree's two failure-surfacing paths: unlike
    subprocess.TimeoutExpired (already bounded by GIT_OP_TIMEOUT_SECONDS and
    re-raised as-is by both callers), a `rm -rf` or `git worktree prune`
    exit failure must not be silently read as success -- both
    review-pr-worktree-remove.py and review-pr-worktree-replace.py catch
    this RuntimeError and surface it on stderr with a nonzero exit rather
    than reporting the worktree gone when it may still be on disk.

    subprocess.run is monkeypatched directly (rather than shelled out to a
    real failing git/rm) since these two cases are about remove_worktree's
    own control flow, not about spawning real, deliberately-broken
    binaries -- that CLI-level surfacing is covered separately in
    test_review_pr_worktree_remove.py and test_review_pr_worktree_replace.py.
    """

    def test_raises_when_worktree_remove_and_the_rm_rf_fallback_both_fail(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        worktree_dir = tmp_path / "wt"
        worktree_dir.mkdir()

        def fake_run(args, **kwargs):
            if args[:2] == ["git", "-C"] and "remove" in args:
                return subprocess.CompletedProcess(args, 1, stdout="", stderr="synthetic worktree remove failure")
            if args[:2] == ["rm", "-rf"]:
                return subprocess.CompletedProcess(args, 1, stdout="", stderr="synthetic rm -rf failure")
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        monkeypatch.setattr(_review_pr_worktree.subprocess, "run", fake_run)
        with pytest.raises(RuntimeError, match="rm -rf"):
            remove_worktree(str(tmp_path), str(worktree_dir))

    def test_raises_when_git_worktree_prune_fails(self, tmp_path: Path, monkeypatch) -> None:
        # Never created, so os.path.exists is False and the remove/rm -rf
        # branch never runs -- isolating the unconditional prune call's own
        # failure.
        worktree_dir = tmp_path / "never-created"

        def fake_run(args, **kwargs):
            if args[-2:] == ["worktree", "prune"]:
                return subprocess.CompletedProcess(args, 1, stdout="", stderr="synthetic prune failure")
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        monkeypatch.setattr(_review_pr_worktree.subprocess, "run", fake_run)
        with pytest.raises(RuntimeError, match="prune"):
            remove_worktree(str(tmp_path), str(worktree_dir))
