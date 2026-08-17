# Atomic worktree-lock acquisition

## Context

`_lib_worktree_collision_guard` (`claude/.claude/hooks/_lib.sh`) exists to
guarantee at most one live Claude Code session holds write access to a
given linked worktree at a time, and its acquisition step relies on
`git worktree lock` for that exclusivity. That reliance is misplaced: git's
own `lock_worktree()` is a plain check-then-write with no atomic create, so
under real contention two sessions can both win. This surfaced as an
intermittent CI failure in `test_20_way_concurrent_lock_race_exactly_one_winner`
(a CI run actually reproduced it — `returncodes=[0, 0, 128×18]`, two racers
both got a successful lock) and, on investigation, turned out to be a real
gap in the guard's own arbitration, not just a flaky test. This plan
replaces the guard's acquisition write with a genuinely atomic one, corrects
the false "exclusive-create, verified empirically" claim everywhere it's
shipped as fact, and turns the flaky test into a deterministic regression
test of the guard's own guarantee.

## Approach

Replace the guard's acquisition step with an `O_EXCL` create (bash
`noclobber`) written directly to the same file git already uses for this
purpose (`<worktree's own git-dir>/locked`), instead of asking
`git worktree lock` to do a non-atomic check-then-write. Everything else —
`git worktree list --porcelain` for reads, `git worktree unlock`/`remove`
for human-facing cleanup — stays exactly as it is; only the acquisition
write moves out of git's hands and into ours, at the one point where git's
own primitive isn't atomic.

**Alternatives considered and set aside:**
- **mkdir-based external mutex** (coordinate around `git worktree lock`
  rather than replacing its write) — rejected. It adds a second piece of
  lifecycle state (the mutex directory) with its own crash-recovery story:
  a session killed between `mkdir` and cleanup either permanently denies
  every future write into that worktree, or needs a stale-mutex recovery
  path that is itself TOCTOU-prone unless carefully arbitrated. That
  liability is worse than the race it closes.
- **Write-then-reread on top of `git worktree lock`'s existing (non-atomic)
  write** — rejected. Worked through the interleavings: it narrows the race
  window (from a full subprocess round-trip to a single write) but does not
  close it — two racers can each pass their own reread before the other's
  write lands. This is exactly the "compounding defensive layers" pattern
  this repo's own CLAUDE.md flags as a wrong-foundation tell: stacking a
  probabilistic check on a foundation that is still not atomic, instead of
  fixing the foundation.

### Assumption ledger

**Root problem:** `_lib_worktree_collision_guard`'s exclusivity guarantee
rests on `git worktree lock` being atomic; it is not (confirmed at git's
own source — see mechanism row below).

**Givens** (git's own command surface and on-disk format — this plan can
choose how to interact with git, not change what git itself does; reach
stops at that platform boundary):
- A hand-written `locked` file, at the exact path and byte format git's own
  `write_file()` produces, round-trips correctly through
  `git worktree list --porcelain`, `git worktree unlock`, and
  `git worktree remove` — on this machine's git (2.55.0). [verified:
  scratch-worktree spike this session — noclobber genuinely refuses to
  overwrite (losing write leaves content untouched), porcelain reports
  `locked claude-code pid 12345` byte-for-byte identical to real
  `git worktree lock --reason "claude-code pid 12345"` output, `unlock`
  clears the hand-written file, `remove` refuses on it with the same
  message git produces for its own lock]
- Same, on CI's git (ubuntu-24.04's default git). [unverified] — not
  re-run against CI's git this session. Accepted as residual risk rather
  than closed: `worktrees/<id>/locked` is documented git-repository-layout
  (`man gitrepository-layout`), not an internal implementation detail
  likely to drift across a minor version gap, and the verification reread
  (below) fails closed if it ever does.
- `noclobber` is genuinely `O_EXCL`-atomic on the filesystems this plan's
  worktrees actually sit on. [verified: `staff-platform-engineer` review —
  30-way concurrent-process race against a fresh path, 5 trials, exactly
  one winner every time on this machine's local filesystem; symlink and
  dangling-symlink targets both block the write rather than opening a
  bypass] Same accepted-residual-risk framing as CI's git version extends
  to a network-mounted worktree (old NFS's `O_EXCL` has a documented
  history of non-atomicity) — out of this plan's stated single-developer-
  machine-plus-CI threat model, and the verification reread fails closed
  there too if it ever doesn't hold.

**Other assumptions** (checkable repo facts, within this plan's reach in
principle — recorded for verification status, not treated as fixed):
- Both existing callers pre-filter to linked-worktree targets before ever
  calling the guard — the guard is never invoked against the main working
  tree today. [verified: `require-worktree-for-git-writes.sh:333`
  (`if [ "$eff_git_dir" != "$eff_common_dir" ]`), `:196` (fast path, same
  precondition established earlier in the script),
  `require-worktree-for-file-writes.sh:148`
  (`if [ "$GIT_DIR_ABS" != "$GIT_COMMON_DIR" ]`)]. This plan does not
  change either caller — mechanism 3 (below) re-establishes the same
  precondition inside the guard itself as defense-in-depth for a value
  the guard must now compute anyway (see Out of Scope for why the
  caller-side checks aren't also touched).

**Mechanisms:**

| # | Mechanism | anchors | Justification |
|---|---|---|---|
| 1 | `O_EXCL` create of `<worktree's git-dir>/locked` via bash `noclobber`, wrapped in `_lib_capped` for the same stall-protection every other git subprocess call in this function already gets | root | `git worktree lock`'s write is confirmed non-atomic at git's own source: `lock_worktree()` (`builtin/worktree.c`) reads via `worktree_lock_reason()` then separately calls `write_file()`; `write_file()` (`wrapper.c`) opens with `O_WRONLY\|O_CREAT\|O_TRUNC` — no `O_EXCL`. A CI run reproduced the resulting race directly (2 of 20 concurrent `git worktree lock` calls both returned 0). Lighter primitives considered: (a) keep using `git worktree lock` — rejected, this is the confirmed-broken primitive; (b) an external mkdir mutex — rejected in Approach above (lifecycle liability). |
| 2 | Post-write verification reread via the existing `_lib_worktree_lock_pid` parser (unchanged), fail-closed on any mismatch | root | Reuses the parser the contended path already has; this is not a race-narrowing check like the rejected write-then-reread alternative (which sat on top of a non-atomic write) — the write underneath it is now genuinely atomic, so a mismatch here means the on-disk format didn't round-trip the way we expect, not that a second racer landed. |
| 3 | Explicit `git-dir == common-dir` precondition, checked before the write | row 2 (givens: "callers pre-filter") | `git worktree lock` internally rejects the main worktree (`is_main_worktree()`) as a side effect of validation we're bypassing by not calling it. We must resolve the worktree's own git-dir anyway (it's the write target), so checking it against common-dir is a one-line reuse of a value already computed, not a new coordination layer — defense-in-depth for a precondition callers already guarantee, re-established at the layer now responsible for it. |

## Critical files

- **`claude/.claude/hooks/_lib.sh`** — rewrite `_lib_worktree_collision_guard`'s
  acquisition step (currently the `git worktree lock` call and its
  immediate `return 0`) to:
  1. Resolve the worktree's own git-dir (`git -C "$worktree_root" rev-parse
     --path-format=absolute --git-dir`, via `_lib_capped`); deny if it
     equals `wt_common_dir` (mechanism 3).
  2. Attempt the write under `noclobber`, wrapped in `_lib_capped` for the
     same stall-timeout protection every other git call in this function
     has — the `O_EXCL` atomicity is a property of the underlying `open()`
     syscall regardless of which process makes it. **Pass `$my_pid` and
     `$wt_git_dir` as positional parameters, not string-interpolated into
     the `bash -c` command**, or they won't resolve in the child's
     environment (they're locals, not exported) — and interpolating
     `$wt_git_dir` directly into the command string is a shell-injection
     surface if the worktree path ever contains metacharacters:
     `_lib_capped bash -c 'set -o noclobber; printf "claude-code pid %s\n" "$1" > "$2/locked"' _ "$my_pid" "$wt_git_dir"`.
  3. On success, reread porcelain and confirm `_lib_worktree_lock_pid`
     reports our own `$my_pid` (mechanism 2); return 0 only then, deny
     (fail closed) otherwise, with a message distinct from every other
     deny path in this function (e.g. `printf 'the worktree lock for %s
     could not be confirmed after acquiring it — treating as unresolved'
     "$worktree_root"` — exact wording is implementation's call, but it
     must not collide with the diagnosis-reread branch's messages, since
     the new test below asserts on this distinctness). Assign via the same
     `locked_pid=$(_lib_worktree_lock_pid ...) && state=0 || state=$?`
     idiom already used at lines 994-999 (not a bare assignment) — this is
     the fail-closed mismatch case mechanism 2 exists to catch, so it's the
     highest-value place to get `set -e` safety right.
  4. On failure (file already existed — the contended case), fall through
     to the existing diagnosis-reread path unchanged.

  Net new subprocess spawns per the platform review: **2** on the
  acquisition/winning path (step 1's git-dir resolve + step 3's
  verification reread) and **1** on the contended/retry path (step 1's
  git-dir resolve only — the diagnosis reread already existed), each
  ~50-65ms locally; only the self-lock fast path (already-held lock, lines
  1001-1003) is untouched. Acceptable for a hook that fires on git writes,
  not the hot per-tool-call path, but worth naming precisely rather than
  as "one new subprocess."

  Rewrite the function's preceding comment block (currently asserting
  "exclusive-create (verified empirically...)" as the exclusion mechanism)
  to describe the actual mechanism and cite git's source, not the 20-way
  observation — state what the code does now, not what it used to claim or
  why the old claim was wrong (that history belongs in the commit message
  and this plan, per this repo's own CLAUDE.md comment rules: no "used to
  be X" framing in shipped comments). Reuse: `_lib_capped`,
  `_lib_worktree_lock_pid` (same parser for both the diagnosis reread and
  the new verification reread — no new parsing code), `_lib_resolve_claude_pid`
  (unchanged).

- **`.claude/plans/worktree-collision-guard.md`** — correct every site
  citing the disproven "exclusive-create, verified empirically" claim as
  fact: the Design section's step 2 (lines ~49-54) and the "Rejected:
  in-hook auto-eviction" paragraph (~94-96), which both assert git's write
  is "the exclusion point"; the assumption ledger's "verified two ways"
  bullet (~173-187), which cites the 20-way race as proof of exclusivity;
  and the "Critical files" test mandate (~314-322), which prescribes
  shipping that exact (disproven) claim as a permanent regression test.
  This is a factual-accuracy correction to a load-bearing assumption, not
  a rewrite of the document's history — replace the false claim with the
  correct one (git's TOCTOU behavior, cited from its source) and note that
  the design changed as a result.

- **`claude/.claude/hooks/tests/test_lib_worktree_collision_guard.py`** —
  replace `TestConcurrentLockRace::test_20_way_concurrent_lock_race_exactly_one_winner`
  (lines 99-135, which races raw `git worktree lock` — upstream git's
  behavior, not this repo's code, and a structural CI flake under
  `pytest -n auto`'s scheduling contention) with a 20-way race against
  `_lib_worktree_collision_guard` itself. Under the O_EXCL rewrite this is
  a *deterministic* assertion (exactly one winner, guaranteed by the OS,
  not just "usually true") rather than a probabilistic one — that
  determinism is the actual proof the fix works.

  Each racer needs its own resolvable claude-pid identity, or
  `_lib_resolve_claude_pid`'s ancestor walk gives every racer the same
  identity (all spawned as direct children of the same pytest process, so
  `$PPID` inside a naively-spawned racer is identical across all 20) and
  the self-lock fast path passes everyone vacuously.

  **The obvious construction for this is wrong — verified empirically, not
  assumed.** `bash -c '<poll>; bash -c "..."'` (inner call as the outer
  `-c` string's literal last statement) does NOT fork: bash's tail-call
  exec optimization replaces the outer process's image with the inner one
  in place, so `Popen(["bash","-c", script])`'s pid, the "outer" pid, and
  the "inner" pid are all the *same* OS process — reproduced directly
  (`subprocess.Popen(["bash","-c", "echo outer pid=$$; bash -c \"echo
  inner pid=$$\""])` prints identical pids both lines). This silently
  defeats the whole design: every racer's `$PPID` stays the shared
  pytest/xdist-worker pid, and the self-lock fast path passes everyone
  vacuously — a real regression in the guard's exclusivity would not be
  caught. Background the inner call and explicitly wait on it instead,
  which forces a genuine fork (a backgrounded job can't be tail-call
  exec-collapsed) — also verified empirically, including that `wait`
  alone does NOT propagate the inner's exit code as the outer script's own
  (the outer's exit code is its own *last command's* status, so a bare
  `wait "$inner"` with nothing after it silently reports 0 regardless of
  what the inner returned); `exit $?` immediately after `wait` is required:

  ```bash
  while [ ! -f "$HOME/.claude/sessions/$$" ]; do sleep 0.01; done
  bash -c '. "$1"; _lib_worktree_collision_guard "$2" "$3"' _ "$LIB_SH" "$WT" "$COMMON_DIR" &
  inner=$!
  wait "$inner"
  exit $?
  ```

  Seed each racer's session file for the *outer* PID (which Python knows
  synchronously from `Popen().pid` — the inner's own pid is a grandchild
  Python never needs to know) via `_seed_session`'s existing `pid=`
  parameter (`conftest.py` — no change needed there) before releasing the
  racer; the poll-loop above ensures seeding can never race a racer past
  its own identity resolution. Reuse: `_init_opted_in_repo`,
  `_add_worktree`, `_git_common_dir` (all unchanged, already present in
  this file), `_seed_session` (`conftest.py`, unchanged). Roughly 40 lines
  of new scaffolding beyond what exists today.

  Assertions: exactly one racer returns 0; the rest return 1 (a real deny,
  not an unrelated resolution failure); the persisted lock reason names the
  winning racer's own outer PID. Deny-message *phrasing* is already covered
  by the existing single-shot tests in this file (`test_foreign_live_lock_denies_naming_pid`
  etc.) — this test's job is proving the exclusivity property under real
  concurrency, not re-asserting message content.

  **Verified, no change needed:** the two existing porcelain-read-count
  assertions in this repo (`test_reread_shows_unlocked_still_denies` here,
  and `test_foreign_live_lock_still_allows_read_via_fast_path` in
  `test_require_worktree_for_git_writes.py`) both pin `count == 2` on the
  *contended* path (initial read, failed acquisition attempt, diagnosis
  reread) — traced against the rewritten function and confirmed this path
  is structurally unchanged: the acquisition attempt still either succeeds
  or fails as a single step, just via `noclobber` instead of `git worktree
  lock`. The read added by mechanism 2 is on the *winning* path, which
  neither existing assertion covers.

  **New coverage for mechanism 2's fail-closed branch** (the 20-way test
  above only exercises the happy path — winner's verification reread
  matches, so it never proves the reread actually blocks a mismatch): add
  a single-shot test in this file mirroring
  `test_reread_shows_unlocked_still_denies`'s existing technique (a fake
  `git` wrapper placed first on `PATH`) but targeting the *verification*
  reread instead of the *diagnosis* one — intercept the porcelain call that
  follows a successful `noclobber` write and have the wrapper report the
  worktree as unlocked (simulating a human's `git worktree unlock` landing
  in the narrow window between our write and our reread). Assert the guard
  denies rather than returning 0 despite the write itself having succeeded
  — this is the fail-closed property mechanism 2 exists to provide, and
  without this test it is only asserted in this plan's prose, not proven
  in CI. Also assert `result.stdout` is non-empty and distinguishable from
  every other deny message in this file (a substring unique to this
  branch, not exact phrasing) — this is a brand-new code path with no
  other test covering it at all, unlike the 20-way test's losers, so
  exit-code-only coverage here can't tell "denies with a clear diagnostic"
  apart from "denies with an empty or wrong message."

- **`docs/hooks.md:20`** — acquisition no longer literally invokes
  `git worktree lock`, though the artifact and semantics it produces are
  unchanged. Replace "already holds that same worktree path via
  `git worktree lock`" with "already holds that same worktree path" —
  drop the trailing clause naming the specific subcommand rather than
  swap in a new one, since the sentence's point (a different session
  already holds it) doesn't depend on which mechanism records that.

## Verification

1. `../../../.venv/bin/pytest claude/.claude/hooks/tests/test_lib_worktree_collision_guard.py claude/.claude/hooks/tests/test_require_worktree_for_git_writes.py claude/.claude/hooks/tests/test_require_worktree_for_file_writes.py -v`
2. Run the new 20-way test repeatedly (10-20x) locally — expect 100% pass,
   not "usually passes." That determinism under repetition is the actual
   evidence the fix closed the race, mirroring how the original bug was
   found (a CI run that happened to reproduce a probabilistic failure).
3. Full suite + lint: `../../../.venv/bin/pytest claude/.claude/ plugins/`
   and `../../../.venv/bin/ruff check claude/.claude/`;
   `scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck` for
   the `_lib.sh` change (repo-root `.shellcheckrc` flags apply).
4. Manually re-run the scratch-worktree spike's round-trip checks against
   the *actual* rewritten `_lib_worktree_collision_guard` (not the
   standalone bash snippet used to validate the primitive) — two concurrent
   `bash -c '. _lib.sh; _lib_worktree_collision_guard ...'` invocations
   against a real linked worktree, confirm exactly one succeeds, and that
   `git worktree unlock`/`remove` still behave correctly against the
   result afterward.

## Out of scope

- **`.claude/plans/collision-guard-fast-path-write-only.md:205-209`** — a
  separate, already-written plan that explicitly declined to re-verify the
  exclusivity claim and instead cited `_lib_worktree_collision_guard`'s
  header comment and `worktree-collision-guard.md` as its source
  (`"[verified: existing code comment, not re-verified empirically this
  session]"`). Both of its cited sources are corrected by this plan, but
  editing that plan file itself is a different ticket's document — flagged
  here rather than bundled in.
- **CI git version verification** — the round-trip spike ran against this
  machine's git (2.55.0) only, not CI's (ubuntu-24.04 default). Accepted
  as residual risk per the assumption ledger above, not verified by this
  plan.
- **`_lib_resolve_claude_pid`'s ancestor-walk mechanism** — unchanged by
  this plan; the new test's racer-identity scaffolding works within its
  existing contract, not around it.
- **Moving the git-dir/common-dir precondition into the two callers**
  instead of (or in addition to) the guard — declined. Both callers
  already establish this precondition before calling the guard (see the
  "other assumptions" ledger row above), so duplicating it there would be
  pure redundancy; mechanism 3 re-establishes it inside the guard only
  because the guard now needs the git-dir value directly for the write
  path regardless, making the check a one-line reuse rather than new
  surface.

### Review surface

Four files change: one hook library function plus its comment (the actual
fix, in security/integrity-relevant code that gates every write across
every stow consumer of this repo), one test file (new deterministic
regression coverage), one plan doc (factual correction to a load-bearing
assumption), one doc-prose line (wording accuracy). Risk concentrates in
`_lib.sh` — the guard's contract with its two callers (return code +
stdout reason) is unchanged, but its internals are the actual exclusion
mechanism for real concurrent writes across the whole repo's worktree
enforcement, so it warrants close review despite the small diff.
