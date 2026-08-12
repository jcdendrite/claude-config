# Guard against two sessions colliding on the same worktree path

## Context

Two Claude Code sessions on this machine can independently anchor into, or
otherwise target via `git -C`/`cd`, the identical linked-worktree path with
no mutual awareness — a real collision already corrupted a mid-merge index
tonight. This matters now because a handoff note flagged it as an open risk
for whoever picks up the next task, and because `plan-it` Step 3 explicitly
warns about running concurrently with other sessions. The intended outcome
is a hook-enforced guard that denies a write from a second live session
into a worktree path a first live session already holds. The lock is
diagnosed as stale in its deny message once the first session's process
exits, but is not auto-cleared by the hook itself — see the Design
section's note on why in-hook auto-eviction was rejected after this
session's own testing showed it reintroduces the collision it exists to
prevent.

This repo's existing worktree enforcement (`require-worktree-for-git-writes.sh`,
`require-worktree-for-file-writes.sh`) already isolates the **main tree**
from concurrent writes by forcing work into linked worktrees. It has no
concept of two sessions sharing the **same linked worktree** — that gap is
exactly what this plan closes.

## Approach

**Root problem:** two live sessions can both hold non-read-only write access
to the same worktree path with no coordination, letting one session's git
write (or file write) race the other's and corrupt shared git state.

**Givens:**
- `EnterWorktree`/`ExitWorktree` and the harness's own session-anchor
  bookkeeping are not repo-owned code — this plan cannot instrument the
  moment a session anchors into a worktree. *Reason: outside this repo's
  reach (harness built-in tool, no source access).*
- This is a developer-machine cooperative guardrail, not an adversarial
  boundary. *Reason: matches the existing hook's own stated threat model
  [verified: `require-worktree-for-git-writes.sh:14-19`] — the agent is
  cooperative, not attacking the gate.*

**Design:** add one new `_lib.sh` function,
`_lib_worktree_collision_guard <path-inside-target-worktree> <REPO_GIT_COMMON_DIR>`,
that resolves the target worktree's root and:

1. Reads current lock state via `git worktree list --porcelain`
   (`_lib_capped`). If it shows `locked` with a PID matching our own
   (already held from an earlier write this session) — allow, no git write
   attempted.
2. Otherwise, attempts to acquire the lock (`git worktree lock <root>
   --reason "claude-code pid <PID>"`, also `_lib_capped`). Success — allow.
   This is the exclusion point for the contended case: two sessions that
   both read "unlocked" in step 1 and both reach step 2 are resolved by
   git's own lock-file write being exclusive-create, not by anything this
   function does — see the verified acquisition-race note below.
3. A failed lock call ("already locked") means someone holds it as of the
   attempt — re-read porcelain to name who, then deny with a
   diagnosis-specific message and no automatic remedy:
   - **Parseable PID, `kill -0` reports it alive** — deny, naming the PID:
     "held by a live session (pid <N>)."
   - **Parseable PID, `kill -0` reports it dead** — deny, naming the PID
     with the manual remedy: "pid <N> is no longer running; if confirmed,
     clear it with `git worktree unlock <path>` and retry."
   - **No parseable PID in the reason** (e.g. a human ran `git worktree
     lock <path> --reason "reviewing"` for an unrelated purpose) — deny
     with the same manual-unlock remedy, since there's no PID to diagnose.
   - **Re-read shows unlocked** (the original holder finished and ran its
     own `unlock` in the window between the failed `lock` call and this
     re-read) — deny with a generic "held at the moment of the write
     attempt, already cleared — retry" message; this is a legitimate,
     narrow race inherent to any read-after-write diagnosis step, not a
     safety gap, since denying (rather than guessing "must be safe now")
     is still the fail-closed direction.

All three git subprocess calls this function makes (`worktree list
--porcelain`, `worktree lock`, and none other — see below) go through
`_lib_capped`, matching every other git call in `_lib.sh`; a timeout or
non-git-related error from any of them denies (fails closed) rather than
falling through to "unlocked."

**Rejected: in-hook auto-eviction of a dead-PID lock.** An earlier draft
of this design had step 3's dead-PID branch call `git worktree unlock`
then retry the lock, self-healing after a crash with no manual step.
Verified this session that `git worktree unlock <path>` has **no
ownership check** — it unconditionally removes whatever lock currently
exists, including one a different session freshly (re-)acquired after the
evictor's own porcelain read. Live reproduction: session A holds a fresh
live lock (`--reason "pid AAA-live"`); a second `git worktree unlock`
call from an unrelated "session B" that never observed A's lock at all
still succeeds and silently strips it (`exit 0`, lock gone from
`git worktree list --porcelain`). Applied to the eviction path, this means
two sessions racing to evict the *same* dead lock can end up in the exact
double-holder state this whole guard exists to prevent: B's unlock call
can land after A has already unlocked-and-relocked, destroying A's fresh
claim with no error on either side. Since git's `worktree lock` is
exclusive-create but `worktree unlock` is not compare-and-swap, there is
no race-safe way to auto-evict inline without adding a second
coordination layer on top of the first — the "step back and find the
simpler foundation" signal for exactly this kind of compounding defense.
The simpler foundation: never call `unlock` from the hook at all. A
dead-PID lock still denies, but with a message a cooperative human/agent
can act on directly — matching the "ambiguous → deny, name the manual fix"
pattern this hook already uses for ordinary main-tree writes.

Both `require-worktree-for-git-writes.sh` (at its two existing "linked
worktree — allow" points: the no-parser fast path and the per-record slow
path) and `require-worktree-for-file-writes.sh` (at its "already in a
linked worktree — allow" point) call this same function before allowing.
Per the user's decision, both git writes and Edit/Write/MultiEdit are
covered, and a collision is a hard deny — the same posture the existing
hook already uses for main-tree writes, and proportionate given this
already caused real corruption once.

Self-identification (“is this lock mine?”) needs the session's live
Claude-process PID, not the hook script's own transient PID — a hook
process exits before the git write it's gating even completes, so a lock
keyed to `$$` would read as dead on the very next check. `marker.sh`
already resolves exactly this PID (`_walk_session`/`_resolve_claude_pid`,
used when writing active-bypass markers), but that logic is private to
`marker.sh` and not sourceable from a hook without triggering its CLI
argument dispatch. It is extracted into `_lib.sh` as
`_lib_resolve_claude_pid`, with `marker.sh` calling the shared version —
eliminating a third independent ancestor-walk (a second one already exists
inline in `nudge-handoff-near-context-cap.sh`) rather than adding one.

**Mechanisms:**

| # | Mechanism | anchors | Justification |
|---|---|---|---|
| 1 | Extract `_walk_session`/`_resolve_claude_pid` from `marker.sh` into `_lib.sh` as `_lib_resolve_claude_pid` | root | Both new call sites need self-PID resolution on every non-read-only write; `_lib.sh` is the one library every hook already sources [verified: every hook in `claude/.claude/hooks/*.sh` sources `_lib.sh`]. |
| 2 | `_lib_worktree_collision_guard`, backed by `git worktree lock` (never `unlock`) + kill-0 liveness for diagnosis | root | See "lighter primitives considered" below. |
| 3 | Call site in `require-worktree-for-git-writes.sh` fast path (~line 160) | row 2 | This path is the common case once a session is already anchored in a worktree — it `exit 0`s before the per-record loop runs, so skipping it would leave the guard silently inert for most real traffic. |
| 4 | Call site in `require-worktree-for-git-writes.sh` slow path (~line 291) | row 2 | Covers `-C`/`cd`-relocated writes the fast path routes to the parser. |
| 5 | Call site in `require-worktree-for-file-writes.sh` (~line 143) | row 2 | Per the user's decision, Edit/Write/MultiEdit are in scope too. |

**Lighter primitives considered for mechanism 2** (git-native lock chosen):

1. `_worktree-lib.sh`'s existing `worktree_in_use()` (OS-level process-cwd
   scan, already used by the cleanup scripts) — rejected. Verified this
   session that a session's *logical* worktree anchor and its underlying
   process's *actual* OS cwd are different things in this codebase:
   `set-session-title-from-branch.sh` "Reads the payload's `.cwd`, not
   process cwd, so a session anchored in a linked worktree titles from that
   worktree's branch" [verified: `docs/hooks.md:41`]. A cwd scan would show
   no occupant for exactly the anchored-but-idle-between-tool-calls case
   this guard exists to catch.
2. A bespoke marker file under `~/.claude/.worktree-lock-active.d/<hash>`,
   reusing `_lib_active_bypass_marker_live` verbatim — rejected in favor of
   git's own primitive. `git worktree lock` is visible to plain
   `git worktree list`, is already respected by this repo's own
   `cleanup-merged-branches.sh` (skips removing a locked worktree)
   [verified: `cleanup-merged-branches.sh:699-746`], and locking-to-protect-
   from-concurrent-operations is the documented purpose of the git feature
   itself — a bespoke file would be invisible outside our own hooks and
   forfeit that free protection.

**Lighter primitives considered for mechanism 1** (extraction into `_lib.sh`
chosen):

1. Shell out to `marker.sh resolve-session-id` as a subprocess from each
   hook — rejected. It returns session_id only, not a PID; a second new
   subcommand would be needed, adding CLI/permissions-allowlist surface
   [verified: `claude/.claude/settings.json:16`'s existing allowlist entry
   for `resolve-session-id`] for a capability only ever needed hook-
   internally, and both hooks would pay a subprocess spawn on every
   non-read-only write.
2. Use the hook script's own `$$` as the lock-owner PID — rejected. The
   hook process is a few-hundred-millisecond subprocess that exits before
   the write it's gating completes, so `kill -0` on it fails on the very
   next check.

**Assumption ledger:**

- `git worktree lock` fails the *second* concurrent caller rather than
  silently overwriting the first. Verified two ways: (1) this session,
  sequential CLI calls — `git worktree lock <wt> --reason first` (exit 0),
  then `git worktree lock <wt> --reason second` on the same worktree (exit
  128, `fatal: '<wt>' is already locked, reason: first`), which proves
  ordering-dependent exclusion but not true simultaneous-write safety on
  its own; (2) `staff-sdet`'s independent re-review round, a genuine
  20-way concurrent-process race against a scratch repo — exactly one
  winner, nineteen clean `exit 128` failures, every run. (2) is the
  stronger evidence for the concurrent-write claim specifically; (1)
  establishes the CLI contract. This is what makes lock-attempt-first (not
  a porcelain-read-then-write pair) the race-safety mechanism for the
  contended path: two sessions that both observe "unlocked" and both then
  call `lock` are resolved by the write itself being exclusive-create, not
  by anything this function's own read ordering does.
- `git worktree unlock <path>` has **no ownership check** — it
  unconditionally removes whatever lock currently exists, verified this
  session by live reproduction: session A acquires a fresh lock
  (`--reason "pid AAA-live"`), then an unrelated `unlock` call that never
  observed A's lock at all still succeeds (`exit 0`) and silently strips
  it. This is why the design does not call `unlock` from the hook at all
  (see the Design section's "Rejected: in-hook auto-eviction" note) — a
  compare-and-swap-free unlock makes any evict-then-relock sequence racy
  against a second evictor in a way `lock`'s own exclusivity cannot cover,
  since the danger is in the *unlock* half of the sequence, not the
  relock half. The same fact also underwrites step 1's self-lock fast
  path (a read, not a write): that path is safe only because nothing in
  this design ever calls `unlock` on a live lock — if some other actor
  outside this guard's control did, in the gap between step 1's read and
  the caller's subsequent git write, a second session's step 2 could
  acquire in that window and both writers would proceed. Bounded by the
  same cooperative threat model as the rest of this hook: no code path
  this plan adds offers an unlock remedy for a *live* lock, only for a
  dead or unparseable one, so no cooperative actor following this guard's
  own messages would ever strip a live lock out from under its holder.
- `kill -0` on a stored PID reports process *existence*, not identity — a
  PID reused by an unrelated process after the original session exited
  would read as "alive" and produce a false deny. This is a real,
  narrowed limitation (a broader "no PID-reuse false positives" claim was
  in an earlier draft of this ledger and was inaccurate for a *stored,
  later-checked* PID; the ancestor walk's own `lstart` validation only
  covers self-identification at write time, not a foreign PID checked
  later — [verified: `marker.sh`'s `_walk_session`,
  `capture-session-id.sh:111-122`] describes the write-time-only
  guarantee). Accepted rather than closed: the false deny is bounded and
  self-clearing (it lasts only until whatever process now holds that PID
  number exits), matching the cooperative-guardrail threat model this
  hook already declares, and closing it would require storing and
  cross-checking `lstart` for a foreign PID too — new machinery for a
  rare edge case with no behavioral payoff, since the safe-direction
  outcome (deny) is identical whether the foreign PID is confirmed-live or
  merely unconfirmed.
- `git worktree lock --reason "<text>"` is preserved verbatim in
  `git worktree list --porcelain`'s `locked <reason>` line, and the
  substring pattern `pid[[:space:]]+([0-9]+)` reliably extracts a numeric
  PID from it [verified: `_worktree-lib.sh:170-172`, already relied on by
  `cleanup-merged-branches.sh`]. The new reason string is written as
  `claude-code pid <PID>` to satisfy that exact pattern.
- `git worktree lock`/`unlock` flags are `git worktree lock [--reason <string>] <worktree>` / `git worktree unlock <worktree>`
  [verified: `git worktree lock -h` / `git worktree unlock -h`, git 2.55.0,
  this session]. The plan does not assert a minimum git version this
  subcommand requires: an absent or too-old `git worktree lock` fails with
  a non-zero exit, which `_lib_worktree_collision_guard`'s fail-closed
  posture (below) already denies on — no separate version check needed.
- A hook script's internal subprocess calls (git, jq, python3, and now
  `git worktree lock`) do not themselves re-trigger `PreToolUse` — hooks
  fire on tool calls the model issues, not on subprocesses a hook's own
  shell body spawns [verified: every existing hook already shells out to
  git/jq/python3 without recursive hook firing].
- A lock with no parseable PID in its reason (e.g. a human ran
  `git worktree lock <path> --reason "reviewing"` for an unrelated purpose)
  is treated as foreign-and-undecidable, never auto-evicted. [unverified —
  this is a design choice made for this plan, not sourced from existing
  code; flagging for scrutiny since it trades a possible false deny for
  never silently discarding a lock this guard didn't create.]
- Every `_lib_capped` call in `_lib_worktree_collision_guard` (`list
  --porcelain`, `lock` — `unlock` is never called; see above) denies on
  timeout or non-zero exit rather than falling through to
  `_lib_first_live_linked_worktree`'s existing permissive-on-failure
  behavior — that function's "treat unreadable state as no linked
  worktree" is correct for *its* purpose (deciding whether to nudge a
  session toward a worktree that may not exist) but would be fail-open
  here (a stalled/errored lock-state read must not silently read as
  "unlocked"). [unverified — design choice for this plan; the two
  functions' opposite failure directions are intentional, not an
  oversight, given each function's different consequence for guessing
  wrong.]
- No auto-eviction and no exit-time (`SessionEnd`) unlock — a dead-PID
  lock denies (with a diagnostic message and a one-line manual remedy)
  until a human or agent runs `git worktree unlock <path>` themselves.
  [engineer-verified: user confirmed hard-deny-on-collision scope. The
  no-auto-evict half of this choice follows from the `unlock`
  ownership-check gap above, not from the user's own statement — flagging
  per the ledger's own tagging rule, since only the "hard deny" half was
  something the user actually decided.] Consequence: every worktree a
  session ever wrote into keeps a `locked` entry in plain `git worktree
  list` output until either a human clears it or the worktree is removed
  outright — a human running `git worktree list` between those points sees
  a dead-PID lock that looks alarming but is inert and self-diagnosing
  (the guard's own deny message already told them the exact remedy the
  first time they hit it).

## Critical files

- `claude/.claude/hooks/_lib.sh` — add `_lib_resolve_claude_pid` (moved
  from `marker.sh`'s `_walk_session`/`_resolve_claude_pid`, same contract)
  and `_lib_worktree_collision_guard` (new), placed near the existing
  `_lib_active_bypass_marker_live` (kill-0 idiom) and
  `_lib_first_live_linked_worktree` (porcelain-parsing idiom) it reuses the
  patterns of.
- `claude/.claude/scripts/marker.sh` — `_walk_session`/`_resolve_claude_pid`
  become thin wrappers calling the `_lib.sh` version; `_resolve_session_id`
  is unaffected. Behavior-preserving — existing `test_marker_script.py`
  coverage should pass unchanged, plus one new thin test asserting the
  wrapper actually calls `_lib_resolve_claude_pid` (e.g. via a stub/spy on
  the `_lib.sh` function) rather than merely producing the same output —
  passing tests alone don't prove delegation happened instead of a
  silently-forked second implementation.
- `claude/.claude/hooks/require-worktree-for-git-writes.sh` — call
  `_lib_worktree_collision_guard` at the fast-path allow (~line 160) and
  the slow-path per-record allow (~line 291); update the header's
  "Motivation" comment, since it currently describes only main-tree
  isolation and this closes the same-worktree gap it didn't cover, and add
  a line to "Known gaps" for the PID-reuse false-deny limitation and the
  no-auto-eviction limitation, both named in the assumption ledger.
- `claude/.claude/hooks/require-worktree-for-file-writes.sh` — call
  `_lib_worktree_collision_guard` at its "linked worktree — allow" point
  (~line 143).
- `claude/.claude/hooks/tests/test_require_worktree_for_git_writes.py`,
  `test_require_worktree_for_file_writes.py` — extend with collision cases:
  base-case acquire (no prior lock, single `lock` call, no read needed
  first per step 2's ordering), self-lock re-entry is idempotent (second
  write in the same session allows via the step-1 porcelain read with no
  `lock` call attempted), foreign live lock denies naming the PID, foreign
  dead lock denies with the manual-unlock remedy (no auto-evict — assert
  `git worktree unlock` is never invoked by the hook), unparseable-reason
  lock denies with the same remedy, the re-read-shows-unlocked race (force
  it by unlocking between the hook's failed `lock` attempt and its
  diagnosis re-read, e.g. via a test seam/mock on the re-read call) still
  denies rather than allowing, and a pruned/missing worktree root fails
  closed (deny, not a hook crash) rather than being silently unhandled.
- New `claude/.claude/hooks/tests/test_lib_worktree_collision_guard.py` —
  unit coverage for `_lib_worktree_collision_guard` and
  `_lib_resolve_claude_pid` directly, including a codified N-way (e.g.
  20-way, matching this plan's own verification round) concurrent-process
  test asserting exactly one winner and every other caller failing
  "already locked" — the exclusivity property this whole design leans on
  was verified manually during plan review; it must ship as a permanent
  regression test, not live only as a citation in the assumption ledger,
  so a future refactor of step 2 into check-then-lock is caught by CI
  rather than silently reintroducing the race. Reuse opportunity: mirror
  `claude/.claude/scripts/tests/test_cleanup_merged_branches.py`'s
  `conftest.py`-level `_dead_pid()` helper (spawns and reaps a real
  process via `Popen(["true"])`, returning a guaranteed-dead PID) rather
  than a hardcoded PID literal — it already exercises real `git worktree
  lock --reason "... pid <dead>"` calls against a temp repo, which is the
  closer precedent than `test_marker_lib.py`'s marker-file-based liveness
  tests for a design keyed off git's own lock state.
- `docs/hooks.md` — update both hooks' entries to describe the new
  same-worktree collision check.
- `README.md` "Worktree enforcement" section — extend past "isolates each
  session's state" to note that a worktree already held by a live session
  is itself now protected from a second session's writes.

**Reuse opportunities:** `_lib_active_bypass_marker_live`'s kill-0 pattern
(don't reinvent liveness checking); `_worktree-lib.sh`'s
`resolve_worktree_for_branch` deferred-commit porcelain-parsing shape as
the template for the path-keyed lookup inside `_lib_worktree_collision_guard`
(not literally shared code — different file, same parsing idiom).

## Verification

- Run the full hook test suite from a worktree:
  `../../../.venv/bin/pytest claude/.claude/hooks/tests/`.
- Manual end-to-end repro: from worktree A, run a git write to acquire the
  lock; confirm `git worktree list --porcelain` shows `locked claude-code
  pid <N>` with `<N>` this session's live PID. Start a background process
  to stand in for a second live session (`sleep 999 &`, capture `$!` as
  `BGPID`), then **directly fabricate the foreign lock** —
  `git worktree unlock <path>` followed by `git worktree lock <path>
  --reason "claude-code pid $BGPID"` — since a real second Bash-tool
  session isn't available in a single-session repro and the guard's own
  write path would just re-acquire for the *same* session otherwise. Then
  run a git write from this session into the same worktree path and
  confirm denial naming `$BGPID`. `kill $BGPID`, retry, and confirm the
  denial now names `$BGPID` as no-longer-running with the manual-unlock
  remedy (not auto-evicted). Run `git worktree unlock <path>` by hand,
  retry, and confirm success — this is the one step a human/agent takes
  that the hook itself deliberately does not.
- Confirm `../../../.venv/bin/ruff check claude/.claude/` and
  `scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck`
  stay clean.

## Out of scope

- Consolidating `nudge-handoff-near-context-cap.sh`'s separate inline
  PID-ancestry walk onto the new `_lib_resolve_claude_pid` — a legitimate
  follow-up now that a third implementation nearly existed, but touches an
  unrelated hook with no dependency on this fix.
- Instrumenting `EnterWorktree`/`ExitWorktree` directly (e.g. a
  `PreToolUse` hook matching those tool names) — would give earlier
  feedback than the first write, but whether those harness-built-in tools
  fire `PreToolUse` at all is unverified in this repo (no existing
  precedent), so the guard is built entirely on the already-proven `Bash`/
  `Edit|Write|MultiEdit` matchers instead.
- An exit-time (`SessionEnd`) unlock hook — rejected per the assumption
  ledger above in favor of denying with a manual remedy instead.
- Auditing `cleanup-merged-branches.sh`'s own existing dead-PID-lock
  auto-eviction (`unlock` then remove) for the same ownership-check gap
  this plan found in `git worktree unlock` — that script's eviction races
  against another concurrent *cleanup* run, not against a live session's
  git write, a much lower-probability window, and it's a pre-existing
  script this plan doesn't otherwise touch. Worth a follow-up look now
  that the underlying primitive's behavior is understood, not a blocker
  here.
