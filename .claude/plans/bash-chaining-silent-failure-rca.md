# Explain silent worktree-lock reacquisition after `git worktree unlock`

## Context

`.claude/plans/bash-chaining-silent-failure-rca.md` follows up on
`~/.claude/briefs/bash-chaining-silent-failure-rca-task.md`'s RCA (now
consumed; see this branch's prior handoff for the investigation). The RCA's
"case 3" finding — a plain `git status` run after an explicit
`git worktree unlock`, in a separate Bash call, silently re-locks the
worktree — turned out **not** to be a defect. `.claude/plans/worktree-collision-guard.md`
(row 3 of its Mechanisms table) and `test_require_worktree_for_git_writes.py::TestWorktreeCollisionGuard::test_read_in_freshly_unlocked_worktree_still_acquires_lock`
[verified: read both this session; the test currently passes] both confirm
this is the collision guard's deliberate, tested design — the fast path in
`require-worktree-for-git-writes.sh` calls `_lib_worktree_collision_guard`
for every non-relocating git-mentioning command specifically because "this
path is the common case once a session is already anchored in a worktree
... skipping it would leave the guard silently inert for most real
traffic."

Presented with this finding, the engineer asked for a lighter fix: not
changing the relock behavior, but surfacing *why* it happened — "have an
error message or something that tells the agent why this is happening."
[engineer-confirmed, this session]

## Approach

Add an informational `additionalContext` note to the PreToolUse allow
response whenever a git-mentioning command's collision-guard call is the
one that transitions a worktree from unlocked to locked — i.e., whenever
this call is the reason the lock now exists, not merely a command that
found it already self-held. [verified this session: Claude Code's
PreToolUse `hookSpecificOutput` supports `additionalContext` alongside
`permissionDecision: "allow"` — https://code.claude.com/docs/en/hooks,
"Decision Control"/"JSON Output Schema" sections, confirmed via
`claude-code-guide` subagent dispatch.]

**Detecting "did this call just acquire the lock" without changing
`_lib_worktree_collision_guard`'s contract.** The guard's stdout-on-success
contract is `""` (empty) in every success branch, and this is pinned by
multiple existing tests in `test_lib_worktree_collision_guard.py`
(`test_acquires_lock_when_unlocked`, `test_self_lock_returns_immediately_without_reattempting_lock`,
`test_self_race_on_write_attempt_still_allows_via_diagnostic_reread`, and
others — all assert `result.stdout == ""` on a 0 exit) [verified: read
this session]. Changing that contract to signal acquired-vs-held would
break this established invariant across all three of the guard's callers,
not just the one this fix cares about — the wrong place to carry this
distinction.

Instead, each call site does a **plain file-existence pre-check** on the
worktree's own `<git-dir>/locked` file — the exact path
`_lib_worktree_collision_guard` itself writes to (`_lib.sh` line ~1034;
see its own docstring for why this file, not `git worktree lock`, is the
mechanism) — immediately before calling the guard:

- File exists before the call → whatever this call gets back (allow or
  deny) was decided against a lock already in place; if it allows, this
  call recognized a pre-existing self-lock, not a fresh one. No message.
- File does not exist before the call, and the guard then allows → the
  only way that combination happens is the guard's own O_EXCL write inside
  this call just created it (a pre-existing *foreign* lock always denies,
  never allows — the O_EXCL create would collide and fall through to the
  deny branch). This call is the reason the worktree is now locked. Emit
  the message.

This needs no new subprocess on the common-case path: every call site
already computes the worktree's absolute git-dir before invoking the guard
(fast path's `SESSION_GIT_DIR_ABS`, slow path's `eff_git_dir`, file-writes
hook's `GIT_DIR_ABS`) — `git rev-parse --absolute-git-dir` resolves
identically from any subdirectory of a worktree, so the value already in
scope at each site is exactly what the guard computes internally as
`wt_git_dir` for the same target path. A `[ -e "$THAT_VAR/locked" ]` test
right before the existing guard call is the entire pre-check. **Precise
subprocess claim** (tightened from an earlier draft's blanket "no new
subprocess" per `staff-platform-engineer` this round): the fast path's
dominant steady-state case is a command hitting an *already self-locked*
worktree (per this hook's own header comment on why the fast path exists
at all), where the pre-check finds the file already present and the new
helper never fires — zero new subprocesses, same as today. Only the
fresh-acquisition transition (the case this feature targets) invokes
`_lib_emit_allow_with_context`, which spawns the same `_lib_jq` subprocess
`_lib_emit_deny` already uses — new only for that one call site's allow
branch, not new to the hook file as a whole (every hook already pays this
cost once per invocation via `_lib_parse_tool_input_or_deny`).

**Accepted race:** a narrow TOCTOU window exists between this stat and the
guard's own O_EXCL write (a foreign session could acquire in between,
which would flip the pre-check's premise). Same cooperative,
single-developer-machine threat model this hook already documents in its
"Known gaps" section for several other narrow races — not adversarial,
accepted rather than closed. A second, same-session variant of this race
is also possible: two near-simultaneous calls from the same session (e.g.
two parallel subagents sharing this worktree) can both see "unlocked" at
their respective pre-checks before either write lands, so both could fire
the message even though only one is the guard's actual O_EXCL winner. Low
stakes — both calls belong to the same session and the substance of the
message ("this session just freshly locked this worktree") is true either
way, just possibly reported twice instead of once. [flagged by
`claude-hook-review`, this round]

The new pre-check is a bash builtin `[ -e ... ]` (a single `stat(2)`), not
wrapped in `_lib_capped` — matching this file's own existing precedent for
the `cd` builtin, which the header's "Known gaps" section already
documents as deliberately unguarded for the same reason (wrapping a
builtin needs an extra `bash -c` layer this file doesn't otherwise pay
for). This is a distinct fact from the TOCTOU race above — an uncapped
stat is a hang-risk characteristic (stalled network-mounted worktree
path), where the TOCTOU race is a correctness characteristic (wrong
lock-attribution outcome) — both belong in the header's "Known gaps"
update, stated separately. [flagged by `staff-platform-engineer`, this
round]

**Shared emit helper.** The message needs to be constructed identically at
three call sites (jq-encode into `hookSpecificOutput.additionalContext`,
degrade to a silent bare allow if `jq`/`_lib_jq` is unavailable — losing an
informational note is not the fail-closed case `_lib_emit_deny` protects
against). Add `_lib_emit_allow_with_context` to `_lib.sh`, placed directly
after `_lib_emit_deny`, mirroring its jq-encode-or-degrade shape but
degrading to silent allow instead of a hard block on encode failure. Two
details of that mirroring must be explicit in the implementation, not left
implied by "mirrors `_lib_emit_deny`'s shape": (1) `permissionDecision`
must be the exact lowercase literal `"allow"` — the harness is
case-sensitive here the same way it is for `"deny"` (`_lib_emit_deny`'s own
header note); (2) the helper must gate on the jq-encoded value being
non-empty before printing anything, mirroring `_lib_emit_deny`'s
`[ -z "$reason_json" ]` check at `_lib.sh:145`, and print nothing (silent
allow) when it is empty. Printing a half-built envelope happens to be
harmless *here* only because a parse failure still resolves to "no
decision" → default allow, which is already the outcome this caller wants
— that's a property of this caller, not of the helper in general, so the
explicit empty-check keeps the helper's own contract correct independent
of who calls it next. [flagged by `staff-platform-engineer`, this round]
Caller contract matches `_lib_emit_deny`'s: the function prints the JSON
envelope (or nothing, on the fallback path) and returns; the caller still
issues its own `exit 0` afterward, exactly as every existing `emit_deny`
call site already does for the deny path.

**Message content** (drafted, refine during implementation/review): one or
two sentences — what happened (this command re-acquired the worktree lock
as a side effect of running any git command here) and the remedy if
unintended (`git worktree unlock <path>` again). Reference the path via
whatever variable is already in scope at that call site (`$CWD`,
`$effective_cwd`, `$FILE_PATH`'s worktree) — no new resolution needed.

**Scope: all three call sites, not just the fast path.** Per this repo's
"audit structural siblings" rule, the slow path's per-record write-allow
branch (`require-worktree-for-git-writes.sh` ~line 342) and
`require-worktree-for-file-writes.sh`'s one call site (~line 149) share
the identical silent-reacquire-with-no-signal shape — same guard, same
missing explanation. Apply the identical pre-check + message pattern at
all three.

**Alternatives considered:**
- *Change the guard's own return contract to signal acquired-vs-held* —
  rejected: breaks a pinned, multi-test invariant (`stdout == ""` on every
  success) across all three callers to serve only one caller's UX need.
- *Fire the message unconditionally on every fast-path allow* — rejected:
  the fast path is this hook's hottest path (every non-relocating
  git-mentioning command in a worktree session); a note on every call
  would be noise, not signal, and cost context on the overwhelmingly
  common case where nothing surprising happened.
- *A second `git worktree list --porcelain` read as the pre-check,
  mirroring the guard's own porcelain-parsing idiom* — rejected: a plain
  `[ -e ... ]` stat test is zero-subprocess and answers the only question
  the caller needs (was the lock file there before), where a porcelain
  read would additionally need parsing self-vs-foreign, which the caller
  doesn't need to distinguish (see Approach above for why plain existence
  suffices).

## Critical files

- `claude/.claude/hooks/_lib.sh` — add `_lib_emit_allow_with_context`
  (~after `_lib_emit_deny`, line ~161).
- `claude/.claude/hooks/require-worktree-for-git-writes.sh` — fast path
  (~line 197-210: pre-check before the existing `_lib_worktree_collision_guard`
  call, branch to the new emit helper when the pre-check says "was
  unlocked"); slow path per-record write-allow branch (~line 339-347,
  same pattern using `$eff_git_dir`). Update the header's "Known gaps"
  list with two separate facts: the new pre-check's own narrow TOCTOU race
  (including the same-session double-message variant), and the stat being
  uncapped (bash builtin, not `_lib_capped`-wrapped — matching this file's
  existing `cd`-builtin precedent).
- `claude/.claude/hooks/require-worktree-for-file-writes.sh` — its one
  collision-guard call site (~line 148-154), same pattern using
  `$GIT_DIR_ABS`. Same header "Known gaps" update as above — this file has
  its own header/known-gaps section distinct from the git-writes hook's.
- `claude/.claude/hooks/tests/test_require_worktree_for_git_writes.py` —
  update `test_read_in_freshly_unlocked_worktree_still_acquires_lock`'s
  docstring (the side effect is no longer un-signaled; the lock-acquisition
  behavior itself is still unchanged) and add an assertion that
  `additionalContext` now carries the explanation. Add a sibling test
  confirming the self-lock-reentry case (`test_self_lock_reentry_is_idempotent`'s
  scenario) still gets a *silent* allow — no message when nothing changed.
  Add equivalent fresh-acquire-message / self-lock-silent coverage for the
  slow path (a relocating write into a freshly-unlocked worktree, e.g. via
  `cd <worktree> && git commit`).
- `claude/.claude/hooks/tests/test_require_worktree_for_file_writes.py` —
  same fresh-acquire-message / self-lock-silent pair for this hook's call
  site.
- `claude/.claude/hooks/tests/test_lib.py` (or wherever `_lib_emit_deny`
  itself is unit-tested, if anywhere — check before assuming a new file is
  needed) — thin coverage for `_lib_emit_allow_with_context`: emits the
  expected envelope shape, and degrades to no output when `_lib_jq` fails.
- `docs/hooks.md` — extend both hooks' existing entries to mention the new
  informational note on a fresh lock acquisition.

## Verification

- `../../../.venv/bin/pytest claude/.claude/hooks/tests/test_require_worktree_for_git_writes.py claude/.claude/hooks/tests/test_require_worktree_for_file_writes.py claude/.claude/hooks/tests/test_lib.py -q`
  (or the closest existing `_lib.sh`-coverage file for the new-helper
  addition).
- `../../../.venv/bin/ruff check claude/.claude/` and
  `scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck`
  stay clean.
- Manual smoke check: in a linked worktree, run a git write to acquire the
  lock, `git worktree unlock <path>`, then a plain `git status` — confirm
  the hook's stdout now carries `additionalContext` naming the
  reacquisition, where it previously allowed silently.

## Out of scope

- Any change to the collision guard's actual lock/allow/deny decisions —
  this is a messaging-only addition; `worktree-collision-guard.md`'s design
  stands as-is.
- Cases 1 (marker.sh chaining) and 2 (redaction gate on
  `git commit --allow-empty`) from the original RCA brief — both did not
  reproduce as defects (see the prior handoff for that investigation); no
  fix is in scope for either here.
- A richer message that distinguishes *why* the worktree was unlocked
  (crash recovery vs. deliberate handoff vs. manual intervention) — the
  guard has no way to know that; the message can only state what happened
  (reacquired) and the remedy (unlock again), not the original unlock's
  intent.
