# Worktree-lock self-recognition across `/clear`

*2026-10-22.*

## Failure mode

After `/clear`, the harness issues a new `session_id` inside the same Claude Code process, so the PID is unchanged. The worktree's `locked` file still carries the pre-`/clear` `session_id` and this process's own PID. [Keying self-recognition on `session_id`](worktree-lock-session-id.md) therefore failed for the session's own lock, and `_lib_worktree_collision_guard` fell through to its `kill -0` liveness check. That check succeeded on the session's own live PID, and the guard denied the session's writes into its own worktree, naming its own PID as a live holder. A session in that state cannot recover, because `EnterWorktree` errors when the target path is the current directory.

## Rule

`_lib_worktree_lock_is_mine` (`claude/.claude/hooks/_lib.sh`) is the canonical statement of the rule, and this file records why it has that shape. A lock that records the caller's own PID is the caller's, because `/clear` keeps the process and so the PID is the identity that survives it. A lock from another PID is the caller's only when it carries the caller's `session_id` (a `claude --continue`/`--resume`) and the old PID is not a live Claude Code process. The guard's first read and its reread after a failed write share that one predicate. `_lib_worktree_acquire_lock`'s post-write reread and `_lib_worktree_reclaim_dead_lock`'s content check stay exact comparisons, because they confirm content the caller just wrote or a lock already diagnosed dead, and do not decide whether someone else's lock is the caller's.

## Why the registry is read

The `session_id` match alone cannot see whether the old PID is still running. If the old process was cleared and still runs, admitting a resume of the lock's conversation would put two live processes into one worktree. Reading `sessions/<pid>` with its start-time check tells a live Claude Code process from a PID that is free or was reused by some other program. While the lock's PID is a live registered process, only that process writes. A `kill -0` check on the old PID in this position cannot tell those two apart, and would deny a resumed session whose old PID an unrelated program now holds.

## Alternatives

- **Re-stamping the lock inside the guard.** The git-writes fast path hands every git command, reads included, to the guard once a lock exists. A re-stamp would turn the [first-write-wins tiebreak](worktree-lock-fast-path.md) into first-touch-wins.
- **Re-stamping from `capture-session-id.sh` on `SessionStart` with source `clear`.** It writes where a read suffices, runs in a hook that fails open, cannot find locks in a second repository the session wrote into, and reverses the cohesion argument in [the earlier session_id decision](worktree-lock-session-id.md).
- **Registry-based session liveness in place of `kill -0`.** After `/clear` no process runs the lock's old session, so a foreign session could reclaim a live process's worktree. That outcome is irreversible.

## Accepted costs

The rule over-denies a resumed session while the lock's old PID is a live Claude Code process, whatever session that process runs. It also lets some locks held by another process pass as the caller's. `_lib_worktree_collision_guard`'s known-gaps list in `_lib.sh` states each case with its trigger and, where one exists, its clearing condition, and is the canonical home for them.

Reclaim of a resumed session's worktree does not depend on this rule, because a `session_id` match never re-stamps the lock. It is left for a follow-up, and the known-gaps list records it. The guard assumes one PID namespace and one config directory per worktree.

## Sources

- `.claude/plans/worktree-lock-self-pid-after-clear.md` — full plan, residual list, assumption ledger, and verification steps.
