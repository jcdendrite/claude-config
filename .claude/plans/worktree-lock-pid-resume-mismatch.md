# Worktree lock: fix self-recognition false-deny after `--continue`/`--resume`

## Context

Fix a bug where the worktree-lock collision guard denies a resumed session
write access to a worktree it legitimately holds. `_lib_worktree_collision_guard`
(`claude/.claude/hooks/_lib.sh`) recognizes "this is my own lock" by comparing
the PID stored in the worktree's `locked` file (written once, at acquisition
time) against the caller's freshly-resolved current PID. `claude --continue`/
`--resume` keeps a session's conversation and identity alive but assigns the
CLI process a new PID, so after a resume the stored PID no longer matches —
self-recognition fails, and the guard falls through to a `kill -0` liveness
check on the now-stale PID. That check either reports the lock dead (asking
for a manual `git worktree unlock`, even though the session is still very
much alive) or — if the OS has already reissued that exact PID number to an
unrelated process — falsely reports the worktree "already in use by a live
Claude Code session," neither of which is true. Why now: the failure mode was
root-caused this session from a user-reported incident and is a near-certain
consequence of every `--continue`/`--resume` on an already-locked worktree,
not a rare edge case. Intended outcome: a resumed session recognizes its own
pre-resume lock immediately, with no behavior change for genuinely foreign
locks.

## Approach

Key the guard's self-recognition on the acquiring session's `session_id`
instead of its PID. `session_id` is assigned by the Claude Code harness and
**stays the same** across `--continue`/`--resume` — only the PID changes —
so storing it in the lock file at acquisition time and comparing it (instead
of PID) at every later check makes self-recognition survive a resume
entirely. `kill -0` PID liveness stays exactly as it is today, but only runs
for locks that don't carry a matching session_id — i.e. genuinely foreign
locks, which is the scenario that check was always meant to cover.

This was chosen over the alternative of rewriting the lock file's stored PID
from `capture-session-id.sh` on every `SessionStart` (which already rewrites
two other PID-keyed lookups it owns for the same `--continue` reason). That
alternative needs a new mechanism to discover which worktree(s) a session's
*prior* PID might hold a lock in — no such index exists today, and
`capture-session-id.sh` has no reason to know about worktree locks, which
are a `_lib.sh`/collision-guard concern. Session-id keying needs no new
discovery mechanism and dissolves the bug at its source: self-recognition
succeeds before the guard ever reaches the `kill -0` check that misfires
today. Confirmed with the engineer this session (see ledger row U1).

**Root problem:** the guard's self-recognition check compares the acquiring
PID, which `--continue`/`--resume` changes, so a resumed session can't
recognize its own pre-resume lock and falls through to a PID-liveness check
that misfires on both a freed and a reused PID.

**Givens:**
- **G1** — Claude Code's `session_id` is assigned and kept stable across
  `--continue`/`--resume` by the harness itself; this repo has no visibility
  into or control over that assignment — it's an external product behavior.
  [verified: code.claude.com/docs/en/hooks, fetched this session — confirms
  session_id "stays the same" on resume, and that `SessionStart`'s `source`
  matcher includes a `resume` value precisely for this event]
- **G2** — `git worktree list --porcelain`'s `locked <reason>` line preserves
  the `<git-dir>/locked` file's raw content verbatim as free text; git
  imposes no format constraint of its own. [verified: the existing test
  suite already round-trips the current multi-token `claude-code pid <N>`
  reason through a real `git worktree add` → direct file write → real
  `git worktree list --porcelain` read —
  `test_lib_worktree_collision_guard.py::TestCollisionGuardBranches::test_acquires_lock_when_unlocked`,
  rerun this session: 27/27 passing on the pre-fix baseline]
- **G3** — This repo owns every consumer of the lock reason string's shape;
  no external tool parses it. [verified: grepped `hooks/*.sh` and
  `scripts/*.sh` for `_lib_worktree_lock_pid` and `_lib_worktree_collision_guard`
  this session — the only call sites are `_lib.sh` itself (definitions) and
  `require-worktree-for-git-writes.sh` / `require-worktree-for-file-writes.sh`,
  both of which just forward the guard's returned message as-is]

**Per-mechanism ledger:**
- **Row 1** (anchors: root) — Extend the lock file's reason string to also
  carry `session_id`, written once at acquisition alongside the existing PID.
  Lighter alternatives considered: (a) a separate per-worktree side-file
  recording session_id — rejected, introduces a second piece of on-disk
  state that can desync from the `locked` file itself, trading one
  atomicity concern for two; (b) looking up the lock's original PID in
  `<config-dir>/sessions/<pid>` (which `_lib_resolve_claude_pid` already
  reads) instead of storing session_id redundantly — rejected, that file is
  keyed by the *current live* PID chain; after a resume the lock's original
  PID has no live process to look it up from, which is exactly the bug. The
  session_id has to be captured at acquisition time, in the one place that's
  guaranteed to still be readable later: the lock file itself.
- **Row 2** (anchors: row1) — All three self-recognition comparisons in
  `_lib_worktree_collision_guard` (the read-only fast path, the
  post-acquisition confirm, and the post-contention self-race check — same
  comparison shape at each site per CLAUDE.md's audit-structural-siblings
  rule) switch from `locked_pid == my_pid` to
  `-n locked_session_id && locked_session_id == my_session_id`, falling back
  to today's PID-based comparison whenever `locked_session_id` is empty
  (old-format lock, predating this fix, or a lock write truncated before the
  session field completed — see Critical files below). The explicit
  non-empty guard makes the fallback a structural certainty rather than
  relying on `my_session_id` never being empty, an invariant that holds
  today per `_lib_resolve_claude_pid`'s contract but isn't re-verified at
  the comparison site itself. Lighter alternative
  considered: require session_id unconditionally and treat any old-format
  lock as immediately foreign — rejected, this would deny every
  already-open worktree the instant this fix ships, rather than degrading
  the way the guard's other branches already do (a dead or unparseable lock
  is never auto-evicted; it clears on its own next use). Old-format locks
  keep exactly today's behavior — including today's bug — until they're
  naturally released.
- **Row 3** (anchors: root) — `_lib_worktree_lock_pid`'s regex gains an
  *optional* trailing ` session <ID>` capture (old-format lines with no
  session field still parse; the session id comes back empty), reusing
  `_lib_valid_session_id_component`'s existing `[A-Za-z0-9_-]+` character
  class for the capture group rather than inventing a new one. Lighter
  alternative considered: a second function doing its own porcelain walk to
  pull just the session id — rejected, duplicates the walk
  `_lib_worktree_lock_pid` already does, where this file already has a
  working precedent for one function returning a paired result from a
  single printf (`_lib_resolve_claude_pid` already returns
  `"<session_id> <pid>"`).

**Assumption ledger:**
- **U1** `[engineer-verified]` — session-id-keyed self-recognition (this
  approach) was chosen over rewriting the lock's stored PID on resume,
  confirmed via AskUserQuestion this session, on the grounds that it fully
  dissolves the bug rather than partially mitigating it and needs no new
  discovery mechanism.

## Critical files

- **`claude/.claude/hooks/_lib.sh`** — the entire fix lives here:
  - `_lib_worktree_collision_guard` (~L963-1060): resolve `my_session_id`
    from the pair `_lib_resolve_claude_pid` already returns (it currently
    only extracts `my_pid` from that pair — the session_id half is already
    available, unused); write `session <SESSION_ID>` into the acquisition
    line (~L1015) alongside the existing `pid <N>`; update all three
    self-recognition comparisons per Row 2 above, splitting each
    `_lib_worktree_lock_pid` result with `read -r locked_pid locked_session_id <<< "$output"`
    rather than parameter-expansion trimming — `read` leaves
    `locked_session_id` empty whenever the second token is genuinely absent,
    with no dependency on the writer always emitting a trailing separator.
    Only this function's header comment needs a one-line pointer to
    `_lib_worktree_lock_pid`'s header as the format's source of truth — it
    doesn't currently restate the exact reason-string shape (that's owned by
    `_lib_worktree_lock_pid`'s header per its own cross-reference comment)
    and shouldn't start now.
  - `_lib_worktree_lock_pid` (~L878-923): extend the regex per Row 3;
    change the return to a `"<pid> <session_id>"` pair (mirroring
    `_lib_resolve_claude_pid`'s existing convention), always printed with
    the separating space even when session_id is empty (`printf '%s %s' "$pid" "$session_id"`) —
    this is the single spot that owns the format description; update its
    header comment, which currently documents the single-PID-only shape,
    accordingly.
  - Reuse: `_lib_resolve_claude_pid`'s existing pair-return convention and
    `_lib_valid_session_id_component`'s existing character class — don't
    reinvent either.
- **`claude/.claude/hooks/require-worktree-for-git-writes.sh`** — its
  "Known gaps" comment block currently states the `kill -0`-can't-distinguish-
  dead-from-reused limitation without qualification; narrow it in one line
  to note it now applies only to a foreign (different-session_id) or
  old-format lock, since a same-session resume no longer reaches that check
  at all. Add one more sentence to the existing mid-write-truncation bullet
  (the 5s-timeout-kill gap already documented for the PID field) noting the
  session field inherits the same truncation exposure: a truncated session
  token fails the character-class regex and is treated as unparseable,
  matching the guard's fail-closed default — state that fact plainly in the
  comment; "Row 2" is this plan's own internal shorthand (see Approach
  above) and shouldn't appear in the shipped comment text. This is a
  description of current
  behavior (Axis 3 fair game), not a historical record —
  `.claude/plans/worktree-collision-guard.md`, which *is* a historical
  record of the original design decision, is intentionally left untouched.
- **`CHANGELOG.md`** — append an entry under `## [Unreleased]` → `### Fixed`,
  matching this repo's established one-bold-lead-plus-explanation style for
  hook-behavior fixes (e.g. the `deny-network-installs.sh` and
  `cleanup-merged-branches.sh` entries already there): name the symptom
  (false deny / false "in use" after `--continue`/`--resume`), the root
  cause (PID-keyed self-recognition), and the fix (session_id-keyed,
  backward-compatible for old-format locks). This repo documents every
  notable hook-behavior change here.
- **`claude/.claude/hooks/tests/test_lib_worktree_collision_guard.py`** —
  - `TestWorktreeLockPid`: add cases for the new two-token return (new
    format with session id; old format with empty session id); confirm the
    existing single-PID cases keep passing under the new optional-group
    regex.
  - `TestCollisionGuardBranches`: add the direct regression case for this
    bug — a lock with this session's `session_id` but a *different* (dead)
    PID is still self-recognized, with no `kill -0` call ever needed. Add a
    same-session, live-but-reused-PID variant (using the existing `live_pid`
    fixture to stand in for the unrelated process that grabbed the freed
    PID) proving self-recognition still short-circuits before the liveness
    check that misfires today. Add an old-format-lock case confirming
    unchanged (PID-based) behavior for pre-upgrade locks. Reuse:
    `_seed_session(home, session_id, pid=...)` already supports seeding a
    session_id at a PID other than the test's own — exactly what these
    cases need — and `_dead_pid()`/`live_pid` already exist for the
    dead/foreign-live PID cases.
- **`docs/design-decisions.md`** — append a new numbered entry documenting
  this decision (session_id as the stable identity key across a harness-level
  resume, PID reserved for liveness only), cross-referencing entry 2's
  identical reasoning for the code-review marker gate (`docs/design-decisions.md:13`)
  as direct precedent for the same pattern.

## Verification

- `../../../.venv/bin/pytest claude/.claude/hooks/tests/test_lib_worktree_collision_guard.py claude/.claude/hooks/tests/test_require_worktree_for_git_writes.py claude/.claude/hooks/tests/test_require_worktree_for_file_writes.py claude/.claude/hooks/tests/test_lib.py -q` — the modified file plus both of its callers' hook-level test files, plus `_lib.sh`'s own general test file.
- Full suite before code-review: `../../../.venv/bin/pytest claude/.claude/hooks/`.
- `scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck` (repo-wide, per this repo's standard lint command).
- Scenario coverage the new tests must exercise end-to-end: (a) same
  session, new PID, old PID now dead — self-recognized, no deny; (b) same
  session, new PID, old PID now reused by an unrelated live process —
  self-recognized, no deny, `kill -0` never reached; (c) different session,
  live PID — still denied as "in use by a live session," unchanged; (d)
  old-format lock (no session id) — unchanged PID-based behavior in every
  branch, proving the transition is backward-compatible; (e) a lock reason
  with a truncated/malformed trailing `session` token (e.g. `locked
  claude-code pid 4242 session` with nothing after it) — unparseable, denies
  with the existing manual-remedy message, never a false self-match.

## Out of scope

- The `/handoff`-based resume path (a brand-new session with a genuinely new
  `session_id`, started via `resume-context`) is a different session by
  design — it doesn't inherit a prior session's lock automatically, and
  that's correct: this bug is specific to `--continue`/`--resume`, where the
  harness itself keeps `session_id` stable.
- No `lstart`-based reuse detection added to the foreign-holder `kill -0`
  path. The design doc's existing accepted-risk reasoning for that gap
  remains valid now that the common `--continue` case no longer reaches it
  at all — closing it further would be new machinery for an already-rare
  residual case, matching this hook's own established accepted-risk style.
- No migration of already-on-disk old-format locks. They keep today's exact
  behavior (bug included) until naturally released — self-clearing, not a
  flag-day cutover.
