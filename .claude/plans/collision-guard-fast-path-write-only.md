# Fix: worktree collision guard misdiagnoses non-last worktrees as unlocked, and blocks reads

## Context

`_lib_worktree_collision_guard` (`_lib.sh`, merged today in #624) is meant to
deny a **write** into a linked worktree when a *different* live session
already holds it. Two independent incident reports today described
worktrees that appeared to "lock themselves out": every git command failed,
not just writes, and got told to "retry" — permanently, since the retry
never worked. I hit the identical failure live, in my own freshly-created
worktree for this very fix, while trying to write this plan file: `git
status` and `Write` were both denied with "already clear again — retry",
even though the lock file on disk consistently showed this session's own
PID. The goal is a definitive, scoped fix (not a revert of #624) that
restores correct lock diagnosis and the "reads are always allowed"
invariant, while keeping the write-collision protection #624 added.

Note on how this fix was verified: hooks execute from the **stowed
main-tree symlinks** (`~/.claude/hooks/*.sh` → the main checkout's
`claude/.claude/hooks/*.sh`), not from whatever worktree a session happens
to be anchored in. Editing this worktree's copy — the correct place for a
PR — cannot be live-tested against my own git commands in this same
worktree pre-merge; every git command I ran here kept hitting the
still-unpatched, stowed copy, including after applying the fix. Verification
therefore relies on isolated reproduction of the library functions
(directly, outside any hook context) and the pytest suite, both against the
worktree's own file copies — not on live git commands run from inside it.

## Root cause

Two independent, compounding defects, both confirmed by direct code read
and live reproduction (root-cause-analysis playbook):

### Bug 1 (primary): `_lib_worktree_lock_pid` loses the target's lock state whenever it isn't the last worktree in the porcelain listing

`_lib_worktree_lock_pid` (`_lib.sh:892-925`) scans `git worktree list
--porcelain` line by line, tracking `in_target`/`locked`/`pid` for the one
worktree record matching `worktree_root`. Every `"worktree "*` line —
**including the header line of the *next*, unrelated worktree record** —
unconditionally resets `locked=0; pid=""` (lines 904-905). Since the `locked`/
`pid` *write* side already gates on `in_target -eq 1` (the `"locked"*` case),
this reset is redundant for a worktree that never had a subsequent record —
but for one that does, it silently discards the just-captured, correct lock
state the instant the loop moves on to the next worktree's header. The
function then reaches EOF holding whatever the *last* record in the whole
capture happened to be, not the target's.

Reproduced directly (`_lib_worktree_lock_pid` fed a synthetic 3-worktree
porcelain capture with the locked target in the middle): returns "unlocked"
(rc=1) for a target that is genuinely `locked claude-code pid 68180`. Feeding
the identical target as the *last* record returns the correct `pid=68180,
rc=0`. This is not a race — it is a deterministic parse defect that fires on
any repo with more than one worktree where the contended one isn't porcelain-
last, which is the common case, not the edge case: this repo currently has
35+ worktrees, so almost none of them are last.

This single bug fully explains the "retry never works" experience in both
reports and in my own reproduction, independent of which session holds the
lock: `_lib_worktree_collision_guard`'s first read misreports "unlocked" →
it attempts `git worktree lock` again on an already-locked worktree, which
fails → the diagnostic re-read suffers the identical bug and *also* reports
"unlocked" → the guard concludes "locked at the moment of the write attempt
but already clear again — retry" (`_lib.sh:1027`), a message that can never
resolve because the underlying read is wrong, not stale. This holds whether
the lock is genuinely this session's own (my case, and plausibly report 1's)
or a different session's (report 2's, and possibly report 1's — see below).

**Why this shipped:** every existing test that exercises this function —
both `TestWorktreeLockPid`'s synthetic-porcelain unit tests and every
fixture-backed test via `_add_worktree` — creates exactly one linked
worktree per repo, so the target is always the only (hence last) linked
record in the capture. No test ever put a second worktree record after the
target.

### Bug 2 (secondary): the fast path applies the collision guard to reads too

`require-worktree-for-git-writes.sh`'s "already anchored, no relocation risk"
fast path (lines 187-198) calls `_lib_worktree_collision_guard` and denies on
any failure, for **any** git subcommand — it never checks whether the
subcommand is read-only. The full parser path gets this right: a read-only
subcommand `continue`s past the collision guard entirely (line ~273, via the
`ALLOWED_RE` allowlist), and the guard is reached only for a write record
(lines ~325-333). The file's own header states "a read-only subcommand ... is
always allowed — cwd is irrelevant" (line 32); the fast path violates that
for its own denial branch. This is what turns "a write is correctly denied"
into "even `git status` is blocked" in both reports, and is what made my own
`git status` attempts fail identically to my blocked `Write` attempts.
`TestWorktreeCollisionGuard`'s class docstring (test file, line 987) notes
it covers the fast path, but every test in it uses `git commit -m foo` — no
read command is ever exercised there either.

A second consequence of the same defect: the fast path's collision-guard
call runs unconditionally even for a command that merely *mentions* "git" as
a substring (e.g. a filename like `require-worktree-for-git-writes.sh` in a
grep pattern) with no real git invocation at all — the word-boundary check
at line 142 is deliberately broad (the file's own header calls this safe
because the *parser* recognizes "no real invocation" and allows it), but the
fast path never reaches the parser, so it can call `git worktree lock`
against the session's own worktree as a side effect of a command that isn't
a git command at all. I hit this too, while grepping for other hook call
sites in this same investigation.

### Report 1's PID-identity claim: unconfirmed, not needed to explain the symptom

Report 1 additionally hypothesized that `_lib_resolve_claude_pid` failed to
recognize its own PID. I could not reproduce a defect in that function —
I independently exercised it through a multi-hop ancestor chain (an
ephemeral wrapper shell → the real Claude Code PID) and it resolved
correctly. Separately, the specific PID it cited (35945, worktree
`fix-install-gate-redirect-false-positive`) is, right now, confirmed via
`ps`/`kill -0` to be a different, genuinely live `claude` process (started
`Wed Aug 12 02:52:24`, well before today's investigation) — not evidence of
a resolver bug either way. Bug 1 alone fully explains a "my own lock reads
as gone" experience without any PID-identity failure, so no change to
`_lib_resolve_claude_pid` is proposed.

## Approach

**Fix 1 (`_lib.sh`):** delete the `locked=0; pid=""` reset inside
`_lib_worktree_lock_pid`'s `"worktree "*)` case. The write side already
requires `in_target -eq 1`, so once the target's true state is captured,
nothing later in the loop can overwrite it — the reset was unnecessary and
is exactly what destroys it. Verified against three cases (target
mid-listing and locked; target unlocked with a later *locked* worktree
following it, to confirm no cross-contamination in the other direction;
target locked with an unparseable-reason worktree following it) — all match
expected output after the fix.

**Fix 2 (`require-worktree-for-git-writes.sh`):** make a fast-path collision
denial provisional instead of final — fall through to full parsing instead
of emitting the deny directly, so a read-only command (or one that merely
mentions "git" with no real invocation) still reaches the `ALLOWED_RE
continue` or the parser's empty-record "nothing to judge" allow, while a
write reaches the same `_lib_worktree_collision_guard` call again (via the
parser's write-record branch) and gets the identical deny reason:

```
if $SESSION_IS_WORKTREE; then
  if <no cd/-C/subshell/backtick>; then
    if _lib_worktree_collision_guard "$CWD" "$REPO_GIT_COMMON_DIR" >/dev/null; then
      exit 0
    fi
    # denial is provisional — fall through to full parsing instead of
    # emit_deny, so a read-only command still exits 0 there.
  fi
fi
```

Also update, in the same file: the "Known gaps" bullet (lines ~87-94)
describing "already in a linked worktree" as now a conditional allow —
after this fix it is unconditional for reads and conditional (collision-
gated) for writes only; and the python3-required deny message (line ~204),
whose "the fast path above does not require python3" claim now holds only
when the worktree isn't currently collision-locked.

**Alternatives considered (Fix 2):**

- **A bash-regex "is this definitely read-only" pre-check on the fast path**
  (mirroring the existing conservative relocation-detection regex at lines
  188-191), skipping the collision guard when it matches. Rejected:
  reliably detecting "no non-allowlisted git subcommand is present" for an
  arbitrary shell string (multiple invocations via `&&`/`;`/`|`, quoting) is
  exactly the class of problem this file's own tokenizer
  (`parse-git-command.py`) exists to solve correctly, per its header's
  "Mechanism" section referencing GH-421 ("parse git commands with a real
  tokenizer instead of regex/sed"). A second, bash-regex classifier
  duplicates that logic and risks the same misparse class GH-421 fixed —
  violates single source of truth for "is this a write."
- **Drop the collision guard from the fast path entirely**, running it only
  from the main parser loop's write branch (already correct) and always
  parsing once `SESSION_IS_WORKTREE` is true. Rejected: forces every
  worktree-anchored command — including the common, uncontended case —
  through the python3 parser, regressing the documented "no python3 needed
  once anchored in a worktree" guarantee (line 204) for the overwhelming
  majority of commands. The chosen approach preserves that guarantee except
  in the rare collision-locked case, which already required denying
  something.

There is no alternative to weigh for Fix 1 — it removes two lines of
dead/harmful logic; the write side's existing `in_target` guard already
makes the reset unnecessary.

**Known tradeoff (confirmed acceptable by `staff-platform-engineer` review):**
Fix 2 means a denied write now calls `_lib_worktree_collision_guard` twice
(fast path, then the parser's write branch) instead of once — roughly 2x the
git-subprocess cost, plus one python3 spawn, versus the pre-fix single call.
This lands only on the already-rare, already-blocked deny path; the
collision-clear (allow) case, which is what the `<100ms` per-fire budget
governs, is unchanged in shape and cost. Every subprocess call in the chain
remains individually capped at 5s via `_lib_capped` (pre-existing, unchanged
by this diff); nothing bounds the chain's *aggregate* wall time if every
call independently stalls to its cap — an existing, unrelated known gap
(`_lib_capped_for`'s uncapped fallback when neither `timeout` nor `gtimeout`
is on PATH), made proportionally worse by the doubled call count but not
changed in kind. Flagged as a follow-up, not a blocker for this fix.

### Assumption ledger

**Root problem:** the collision guard both (a) loses a worktree's true lock
state when that worktree isn't porcelain-last, and (b) applies whatever
state it does compute to read-only commands, which should never be gated on
lock state at all.

**Givens:**
- `git worktree lock`/`unlock` exclusive-create and no-ownership-check
  semantics, as documented in `_lib_worktree_collision_guard`'s header and
  `.claude/plans/worktree-collision-guard.md` — [verified: existing code
  comment, not re-verified empirically this session]. Reason: owned by git
  itself; re-verifying is outside this plan's scope.
- The collision guard's other already-documented known gaps (dead-PID lock
  never auto-cleared, PID-reuse liveness edge case) remain accepted,
  unrelated to the bugs being fixed here — [verified: root-cause finding
  above accounts for both incident reports without invoking either gap].
- Hooks execute from the stowed main-tree symlink, not the anchored
  worktree's own copy — [verified: this fix, correctly applied to the
  worktree, had no effect on live git commands run in that same worktree
  until reasoned through explicitly]. Reason: expected pre-merge
  bootstrapping behavior for any hook-fixing PR in this repo, not something
  this plan can or should change.

**Per-mechanism:**
- Remove `_lib_worktree_lock_pid`'s redundant state reset — anchors: root.
  [verified: reproduced the bug and the fix directly against synthetic
  porcelain input, including two non-regression cases]
- Fast path falls through to the parser on a collision denial instead of
  emitting it directly — anchors: root. [verified: read
  `require-worktree-for-git-writes.sh` in full; confirmed the parser
  branch already implements the correct read/write distinction and needs
  no change]
- No change to `_lib_resolve_claude_pid` — anchors: root (report 1's
  PID-identity sub-claim). [verified: reproduced correct multi-hop
  resolution live; confirmed via `ps`/`kill -0` that the cited PID belongs
  to an unrelated, currently-live session]

## Critical files

- `claude/.claude/hooks/_lib.sh` — `_lib_worktree_lock_pid` (remove the
  reset at lines ~904-905).
- `claude/.claude/hooks/require-worktree-for-git-writes.sh` — fast-path fix
  (lines ~187-198); header prose updates (lines ~55-94, ~204). Reuse: the
  existing parser branch's `ALLOWED_RE` skip (~line 273) and write-only
  collision-guard call (~lines 325-333) — no new classification logic.
- `claude/.claude/hooks/tests/test_lib_worktree_collision_guard.py` —
  `TestWorktreeLockPid`: add a case with a *second* worktree record
  following the locked target, asserting the pid is still correctly
  returned (the direct regression test for Bug 1); one confirming an
  unlocked target followed by a *locked* later worktree doesn't leak that
  later lock into the target's result; and the third manually-verified
  case from the root-cause section — a locked target followed by a
  trailing *unparseable-reason* record — since that exercises the trailing
  record's own `pid=""` no-match branch, not just the parseable-pid branch
  the first case covers, and was verified but not yet pinned as a
  permanent test (`staff-sdet` finding 2).
  `TestCollisionGuardBranches` (or a new class alongside it): add the
  original incident's exact shape at the guard level — a repo with **two**
  linked worktrees where the target holds its **own** live-pid lock and is
  not the last porcelain record — asserting the guard returns 0 without
  reattempting `git worktree lock`, using the same git-wrapper
  call-counting technique `test_self_lock_returns_immediately_without_reattempting_lock`
  already establishes for the single-worktree case (`staff-sdet` finding 1,
  highest priority — this is the one shape none of the other proposed
  tests reach, and directly closes the "why this shipped" gap the
  root-cause section names).
- `claude/.claude/hooks/tests/test_require_worktree_for_git_writes.py` —
  `TestWorktreeCollisionGuard`: add a test that a foreign **live** lock
  still allows a read-only command (e.g. `git status`) via the fast path,
  and one for a foreign **dead-pid** lock doing the same (the regression
  test for Bug 2). At least one of these must additionally assert the
  guard was actually invoked and fell through — not simply bypassed by
  some other path — via the same `git`-wrapper call-counting seam
  `TestCollisionGuardRereadRace` uses in the other test file, counting
  `worktree list --porcelain` invocations and asserting exactly one; a
  bare allow/deny assertion on hook stdout cannot distinguish "fell
  through to the parser and got the read exemption" from "the guard was
  never called at all," which is exactly the shape of the rejected
  simpler alternative (drop the guard from the fast path entirely) this
  plan's Approach section argues against on cost grounds (`staff-sdet`
  finding 4). Confirm the existing write-denial tests
  (`test_foreign_live_lock_denies_naming_pid`,
  `test_foreign_dead_lock_denies_with_manual_remedy`,
  `test_unparseable_reason_lock_denies_with_manual_remedy`) still pass
  unchanged — they now exercise the parser-fallthrough path instead of the
  fast path's direct deny, with the same expected deny reason text
  (`staff-sdet` confirmed these remain correctly-flowing, not weakened, by
  tracing the doubled-call control flow directly).

## Verification

- `../../../.venv/bin/pytest claude/.claude/hooks/tests/test_lib_worktree_collision_guard.py claude/.claude/hooks/tests/test_require_worktree_for_git_writes.py -v`
- `../../../.venv/bin/pytest claude/.claude/` (full suite, catches any
  regression in `require-worktree-for-file-writes.sh` or elsewhere)
- `../../../.venv/bin/ruff check claude/.claude/`
- `scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck`
  (run from the worktree root — path is repo-relative)
- Live re-verification is only meaningful **after** this branch is merged
  and pulled into the main checkout (hooks run from the stowed symlink, not
  a worktree) — at that point, reproduce the exact scenario hit this
  session (a worktree locked by this session's own pid, not last among
  several worktrees) and confirm both `git status` and a write succeed
  without a manual `git worktree unlock`.

## Out of scope

- `_lib_resolve_claude_pid` and the other already-documented collision-guard
  known gaps (dead-lock auto-eviction, PID-reuse liveness window) —
  investigation found no defect here; unrelated to both incident reports.
- The worktrees currently locked by other live Claude Code sessions on this
  machine — genuine, live locks, not stale; not touched by this plan.
- `require-worktree-for-file-writes.sh` — audited for the Bug 2 shape; not
  affected, since it only ever fires for Edit/Write/MultiEdit tool calls
  (never reads), so it has no read/write distinction to get wrong. It does
  call the now-fixed `_lib_worktree_collision_guard` (Bug 1's fix benefits
  it directly with no changes needed there).
- Any change to how/where hooks execute relative to a worktree (the stowed-
  symlink bootstrapping behavior noted above) — expected, pre-existing
  design, unrelated to the bugs being fixed.
