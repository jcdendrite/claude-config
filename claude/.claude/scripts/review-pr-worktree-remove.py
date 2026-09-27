#!/usr/bin/env python3
"""Removes a review worktree under the same lock review-pr-worktree-replace.py
uses for the identical WORKTREE_DIR, so review-pr-finish.sh's cleanup-only
removal can never race that script's own remove/prune/add sequence for the
same PR. Unlike review-pr-worktree-replace.py, this never adds a worktree
back -- a cleanup call must never trigger an add, so the two scripts share
only _review_pr_worktree.py's lock-acquire and remove-worktree primitives,
not this whole file.

Usage: review-pr-worktree-remove.py REPO_ROOT WORKTREE_DIR SESSION_ID DEADLINE_SECONDS

Acquires an exclusive, non-blocking lock on WORKTREE_DIR + ".lock" (created
if absent), retrying at a fixed poll interval until DEADLINE_SECONDS
elapses. Holding the lock: compares SESSION_ID against the contents of the
WORKTREE_DIR + ".owner" sidecar review-pr-worktree-replace.py writes,
inside its own locked section, at checkout time. A non-empty, mismatching
owner means a different, still-active
session's checkout occupies WORKTREE_DIR, so this exits 3 without touching
the worktree; a missing or empty owner file falls through to removal,
matching the pre-existing no-ownership-recorded default. This comparison
runs only after the lock is held, so it is atomic with respect to a
concurrent checkout that replaces both the worktree and the owner file.

On a match (or no sidecar), removes WORKTREE_DIR via `git worktree remove
--force` if present, falling back to `rm -rf` if that fails; runs `git
worktree prune` unconditionally, since a prior run's directory can be gone
while its .git/worktrees/<id> metadata survives; then removes the owner
sidecar. Prints nothing on success and exits 0, including when WORKTREE_DIR
never existed. Exits 3 with a message on stderr when a different session
owns the worktree. Exits non-zero (2) with a message on stderr on a lock
timeout (before touching the worktree at all) or any git failure.
"""
from __future__ import annotations

import contextlib
import fcntl
import os
import subprocess
import sys

from _review_pr_worktree import GIT_OP_TIMEOUT_SECONDS, acquire_lock, remove_worktree


def _read_owner_session_id(owner_file: str) -> str:
    """Returns the owner file's stripped content, or "" on any read failure
    (absent file or unreadable). The caller treats "" as "no ownership
    recorded" rather than an error."""
    try:
        with open(owner_file) as f:
            return f.read().strip()
    except OSError:
        return ""


def main(argv: list[str]) -> int:
    if len(argv) != 5:
        print(
            "review-pr-worktree-remove.py: usage: review-pr-worktree-remove.py "
            "REPO_ROOT WORKTREE_DIR SESSION_ID DEADLINE_SECONDS",
            file=sys.stderr,
        )
        return 2
    _, repo_root, worktree_dir, session_id, deadline_arg = argv
    try:
        deadline_seconds = float(deadline_arg)
    except ValueError:
        print(
            f"review-pr-worktree-remove.py: DEADLINE_SECONDS '{deadline_arg}' is not a number.",
            file=sys.stderr,
        )
        return 2

    lock_path = worktree_dir + ".lock"
    try:
        lock_f = acquire_lock(lock_path, deadline_seconds)
    except OSError as exc:
        print(
            f"review-pr-worktree-remove.py: could not open or lock {lock_path}: {exc}.",
            file=sys.stderr,
        )
        return 2
    if lock_f is None:
        print(
            f"review-pr-worktree-remove.py: could not acquire the worktree lock at "
            f"{lock_path} within {deadline_seconds}s -- a concurrent invocation against "
            "the same PR may still be running.",
            file=sys.stderr,
        )
        return 2

    try:
        owner_file = worktree_dir + ".owner"
        owner_session_id = _read_owner_session_id(owner_file)
        if owner_session_id and owner_session_id != session_id:
            print(
                f"review-pr-worktree-remove.py: worktree {worktree_dir} is owned by a "
                f"different, still-active session ({owner_session_id}) -- skipping removal.",
                file=sys.stderr,
            )
            return 3
        try:
            remove_worktree(repo_root, worktree_dir)
        except subprocess.TimeoutExpired as exc:
            print(
                f"review-pr-worktree-remove.py: {exc.cmd} timed out after "
                f"{GIT_OP_TIMEOUT_SECONDS}s. Abort.",
                file=sys.stderr,
            )
            return 2
        except RuntimeError as exc:
            print(f"review-pr-worktree-remove.py: {exc}", file=sys.stderr)
            return 2
        except OSError as exc:
            print(f"review-pr-worktree-remove.py: {exc}", file=sys.stderr)
            return 2
        with contextlib.suppress(OSError):
            os.remove(owner_file)
        return 0
    finally:
        fcntl.flock(lock_f, fcntl.LOCK_UN)
        lock_f.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
