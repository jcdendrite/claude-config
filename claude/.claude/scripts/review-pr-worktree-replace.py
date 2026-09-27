#!/usr/bin/env python3
"""Serializes review-pr-checkout.sh's worktree remove/prune/add sequence
against a concurrent invocation targeting the same PR via `fcntl.flock` on
WORKTREE_DIR + ".lock", which releases automatically on process exit
(including SIGKILL).

Usage: review-pr-worktree-replace.py REPO_ROOT WORKTREE_DIR SHA SESSION_ID DEADLINE_SECONDS

Acquires an exclusive, non-blocking lock on WORKTREE_DIR + ".lock"
(created if absent), retrying at a fixed poll interval until
DEADLINE_SECONDS elapses. Holding the lock: removes any existing
WORKTREE_DIR via `git worktree remove --force`, falling back to `rm -rf`
if that fails (a not-quite-clean prior worktree); runs `git worktree
prune` unconditionally, since a prior run's directory can be gone while
its .git/worktrees/<id> metadata survives, which would otherwise fail the
next, un-forced `worktree add` deterministically; then runs `git worktree
add --detach WORKTREE_DIR SHA`. On a successful add, writes SESSION_ID to
the WORKTREE_DIR + ".owner" sidecar -- still inside the same locked
section, before the lock is released -- so worktree creation and
ownership recording are one atomic unit under the lock
review-pr-worktree-remove.py's own ownership check also runs under. A
caller-side write of that sidecar after this script returns would leave a
window in which a queued removal call could acquire the lock and observe
a stale owner value before the new owner file lands. If the sidecar write
itself fails, rolls back the just-created worktree rather than leaving one
behind with no recorded owner. Prints WORKTREE_DIR to stdout and exits 0
on success. Exits non-zero with a message on stderr on a lock timeout
(before touching the worktree at all), any git failure, or a failure to
write the ownership sidecar (worktree rolled back in that last case).

The lock file itself is never cleaned up: a zero-length file at
WORKTREE_DIR + ".lock" carries no state and needs none.
"""
from __future__ import annotations

import fcntl
import os
import subprocess
import sys

from _review_pr_worktree import GIT_OP_TIMEOUT_SECONDS, acquire_lock, remove_worktree


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, capture_output=True, text=True, timeout=GIT_OP_TIMEOUT_SECONDS
    )


def main(argv: list[str]) -> int:
    if len(argv) != 6:
        print(
            "review-pr-worktree-replace.py: usage: review-pr-worktree-replace.py "
            "REPO_ROOT WORKTREE_DIR SHA SESSION_ID DEADLINE_SECONDS",
            file=sys.stderr,
        )
        return 2
    _, repo_root, worktree_dir, sha, session_id, deadline_arg = argv
    try:
        deadline_seconds = float(deadline_arg)
    except ValueError:
        print(
            f"review-pr-worktree-replace.py: DEADLINE_SECONDS '{deadline_arg}' is not a number.",
            file=sys.stderr,
        )
        return 2

    lock_path = worktree_dir + ".lock"
    try:
        lock_f = acquire_lock(lock_path, deadline_seconds)
    except OSError as exc:
        print(
            f"review-pr-worktree-replace.py: could not open or lock {lock_path}: {exc}.",
            file=sys.stderr,
        )
        return 2
    if lock_f is None:
        print(
            f"review-pr-worktree-replace.py: could not acquire the worktree lock at "
            f"{lock_path} within {deadline_seconds}s -- a concurrent invocation against "
            "the same PR may still be running.",
            file=sys.stderr,
        )
        return 2

    try:
        try:
            remove_worktree(repo_root, worktree_dir)
            # --detach: no branch name, so this worktree is invisible to
            # cleanup-idle-open-pr-worktrees.sh, which classifies reclaim
            # candidates by matching a branch name to an open PR. Accepted
            # gap -- a dedicated reclaim mechanism for detached review-pr
            # worktrees is a larger follow-up, out of scope here.
            add = _run(
                ["git", "-C", repo_root, "worktree", "add", "--detach", worktree_dir, sha]
            )
            if add.returncode != 0:
                print(
                    f"review-pr-worktree-replace.py: git worktree add failed for "
                    f"{worktree_dir} at {sha}: {add.stderr.strip()}",
                    file=sys.stderr,
                )
                return 2
            # O_NOFOLLOW: refuses a symlink at the predictable owner-sidecar
            # path atomically with the write, the same discipline
            # claude/.claude/hooks/_lib.sh's _lib_write_no_follow applies to
            # every other session-keyed review-pr artifact.
            owner_file = worktree_dir + ".owner"
            try:
                owner_fd = os.open(
                    owner_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o666
                )
                with os.fdopen(owner_fd, "w") as owner_f:
                    owner_f.write(session_id + "\n")
            except OSError as exc:
                # Rollback rationale: module docstring above.
                print(
                    f"review-pr-worktree-replace.py: could not write ownership sidecar "
                    f"{owner_file}: {exc}. Rolling back the worktree just created at "
                    f"{worktree_dir}.",
                    file=sys.stderr,
                )
                try:
                    remove_worktree(repo_root, worktree_dir)
                except (RuntimeError, OSError, subprocess.TimeoutExpired) as rollback_exc:
                    # Best-effort: still clear the owner sidecar below even
                    # when the rollback removal itself fails, so a crashed
                    # rollback doesn't also leave a stale sidecar behind.
                    print(
                        f"review-pr-worktree-replace.py: rollback removal of "
                        f"{worktree_dir} failed: {rollback_exc}.",
                        file=sys.stderr,
                    )
                # remove_worktree only touches WORKTREE_DIR itself, never the
                # owner sidecar -- clear it here so a failed write doesn't
                # leave a stale sidecar pointing at a now-removed worktree.
                try:
                    os.unlink(owner_file)
                except OSError as unlink_exc:
                    print(
                        f"review-pr-worktree-replace.py: could not remove ownership "
                        f"sidecar {owner_file} during rollback: {unlink_exc}.",
                        file=sys.stderr,
                    )
                return 2
        except subprocess.TimeoutExpired as exc:
            print(
                f"review-pr-worktree-replace.py: {exc.cmd} timed out after "
                f"{GIT_OP_TIMEOUT_SECONDS}s. Abort.",
                file=sys.stderr,
            )
            return 2
        except RuntimeError as exc:
            print(f"review-pr-worktree-replace.py: {exc}", file=sys.stderr)
            return 2
        except OSError as exc:
            print(f"review-pr-worktree-replace.py: {exc}", file=sys.stderr)
            return 2
        print(worktree_dir)
        return 0
    finally:
        fcntl.flock(lock_f, fcntl.LOCK_UN)
        lock_f.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
