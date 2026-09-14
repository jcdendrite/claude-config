# Worktree-lock fast path: reads stop reacquiring the lock

*2026-08-28. Formerly `docs/design-decisions.md` §32.*

The fast path calls `_lib_worktree_collision_guard` only when the lock is already present, so a read never reaches the guard's "unlocked" diagnosis. An absent lock falls through to full parsing, where a read is allowed unconditionally and a write re-runs the guard. Acquisition is a write-only side effect on both paths.

The guard's contention tiebreak is first-write-wins: the first *write* — a git write via this hook, or a file write via `require-worktree-for-file-writes.sh`'s identical guard call — is what claims the worktree. This is the correct direction for a guard whose stated purpose is preventing two sessions from writing into one worktree. A read-only session was never the invariant it needed to protect.

Two tradeoffs are accepted rather than closed.

- The fast path's exclusion list (`cd`, `-C`, `(`, backtick) doesn't cover `||`/`&`. A `||`/`&`-chained git write such as `git fetch || git commit -m x` denies when the lock is absent, via the slow path's `||`/`&` write-cwd-ambiguity check. That check cannot distinguish a relocation-risky chain from a bare read-then-write chain with no `cd`. The deny names its own remedy and the window self-heals on the first lock acquisition from any source. Relaxing that deny for a no-`cd` chain is deferred to a separate change, since it loosens a security-relevant gate in the permissive direction and deserves its own review.
- The fast path's python3-free exit now requires an already-held lock, narrower than before:
  - A never-locked worktree's first git operation, read or write, falls through to full parsing.
  - A foreign-locked worktree's python3-less write denies citing python3 rather than the true foreign-lock holder, because the fast path checks only the guard's exit code, not its reason. `test_python3_absent_against_foreign_lock_gives_misleading_reason` pins this case.

## Sources

- `.claude/plans/worktree-lock-conditional-reacquire.md` — full assumption ledger, the over-powered-primitive check (a bash-side read/write pre-filter and a non-acquiring "peek" guard mode were both rejected), and the behavioral test matrix.
