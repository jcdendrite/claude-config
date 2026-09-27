"""Shared review-pr worktree-lifecycle primitives: the fcntl.flock-based
mutual exclusion on a WORKTREE_DIR + ".lock" file, and the git worktree
remove/prune sequence built on top of it. review-pr-worktree-replace.py's
own remove/prune/add sequence and review-pr-worktree-remove.py's
cleanup-only removal both import acquire_lock() and remove_worktree() from
here rather than each carrying their own copy. Both callers derive
WORKTREE_DIR via _lib_main_repo_root (claude/.claude/hooks/_lib.sh), so the
two scripts always serialize against the identical lock file for the same
PR.
"""
from __future__ import annotations

import errno
import fcntl
import os
import subprocess
import time

# Matches transcript-analysis.py's _COST_LEDGER_LOCK_POLL_INTERVAL_S
# precedent for a LOCK_EX|LOCK_NB poll against a time.monotonic() deadline.
LOCK_POLL_INTERVAL_SECONDS = 0.1

# Bounds each individual git call in remove_worktree -- a stalled `git
# worktree remove`/`prune` (a locked .git/index, a wedged filesystem) must
# not hang forever while still holding the lock.
GIT_OP_TIMEOUT_SECONDS = 30


def acquire_lock(lock_path: str, deadline_seconds: float):
    """Returns an open, locked file object, or None on a timed-out
    acquisition. The file is opened "a+" (create if absent, never
    truncate) since its only purpose is to be an inode `flock` can key on."""
    # A `with` block (SIM115) would close the file, releasing the lock,
    # before the caller -- who owns it past this function's return -- ever
    # uses it.
    lock_f = open(lock_path, "a+")  # noqa: SIM115
    deadline = time.monotonic() + deadline_seconds
    while True:
        try:
            fcntl.flock(lock_f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return lock_f
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EAGAIN):
                raise
            if time.monotonic() >= deadline:
                lock_f.close()
                return None
            time.sleep(LOCK_POLL_INTERVAL_SECONDS)


def remove_worktree(
    repo_root: str, worktree_dir: str, *, timeout_seconds: float = GIT_OP_TIMEOUT_SECONDS
) -> None:
    """Removes worktree_dir via `git worktree remove --force` if present,
    falling back to `rm -rf` if that fails (a not-quite-clean prior
    worktree), then runs `git worktree prune` unconditionally -- a prior
    run's directory can be gone while its .git/worktrees/<id> metadata
    survives, which would otherwise fail the next, un-forced `worktree add`
    deterministically. Caller must hold the WORKTREE_DIR lock for the
    duration. Raises subprocess.TimeoutExpired if any step exceeds
    timeout_seconds, or RuntimeError if the `rm -rf` fallback or the final
    `git worktree prune` exits non-zero -- both are checked so a caller
    that reads "no exception" as success is never told that a directory
    still on disk was actually removed.
    """
    if os.path.exists(worktree_dir):
        remove = subprocess.run(
            ["git", "-C", repo_root, "worktree", "remove", "--force", worktree_dir],
            capture_output=True, text=True, timeout=timeout_seconds,
        )
        if remove.returncode != 0:
            fallback = subprocess.run(
                ["rm", "-rf", "--", worktree_dir], capture_output=True, text=True, timeout=timeout_seconds
            )
            if fallback.returncode != 0:
                raise RuntimeError(f"rm -rf {worktree_dir} failed: {fallback.stderr.strip()}")
    prune = subprocess.run(
        ["git", "-C", repo_root, "worktree", "prune"],
        capture_output=True, text=True, timeout=timeout_seconds,
    )
    if prune.returncode != 0:
        raise RuntimeError(f"git worktree prune failed: {prune.stderr.strip()}")
