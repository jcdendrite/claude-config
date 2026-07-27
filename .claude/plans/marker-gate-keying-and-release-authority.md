# Marker gate keying and gate-release authority

## Context

**Goal:** make a review marker's stored content the sole authorization for
releasing a review gate, and make it impossible for a subagent with a narrow
mandate to release a gate it could not have satisfied.

Two independent defects in the review-marker gates surfaced together. A
session resumed under a new `session_id`, which made its own completed,
still-valid plan review unreachable — the completion marker is written to
`~/.claude/plan-review-markers/<repo-hash>.<session_id>` and read back at
exactly that path, so a new `session_id` reads a path that does not exist and
the gate re-arms with no review actually missing. Every `Edit` was denied.
Three `code-writer` subagents were dispatched into that state. Two stopped and
reported the denial. The third compared the stored hash against the
recomputed one, found them equal, and ran `marker.sh write plan-review` for
itself, releasing the gate for the whole session.

No review was faked — the hashes did match. But `marker.sh` resolves
`session_id` by walking the process ancestor chain to the Claude main process,
so a subagent's marker write is indistinguishable from the main session's, and
an agent whose mandate is "implement this change" unilaterally released an
enforcement gate.

**Why now:** the duplicate-marker signature is already recurring rather than a
one-off. Two repo hashes each hold two byte-identical completion markers under
different session ids: `6bf5f7c2…` (02:17 and 02:59) and `6618085c…` (01:36
and 02:54). Six worktrees currently sit with armed plan gates, so the next
resume reproduces the trigger. Independently, issue #426's census of 1120 hook
denials across 335 sessions found `require-plan-review` at 79 denials with 19
false positives (24%) — every one an `already-satisfied` case of this same
over-keying defect.

**Intended outcome:** a resumed or parallel session inherits any review that
covers the exact state it is about to act on; and `marker.sh write` /
`activate` from a mandate-scoped agent is denied with a message directing it to
report to the parent instead — converting the forge into the report that the
two well-behaved agents already produced.

**User surface and threat model.** Developer-machine enforcement guardrails for
Claude Code sessions, under a *cooperative* agent model: the failure being
prevented is an agent taking an unsanctioned shortcut, not a hostile human with
shell access (who can simply delete the hooks). The repo is public and
stow-distributed, so these hooks run on any cloner's machine — but no
production surface, no network boundary, and no user data are involved.

## Approach

Two changes that address opposite failure directions of the same field. The
read-side change fixes false denials (a gate closed when review exists); the
write-side change fixes unauthorized release (a gate opened by an agent that
did not review). They are independent, and neither is a hardening layer on the
other.

### Read side — stored content authorizes; filename keys only namespace

Every completion marker is already content-addressed: the file's *content* is
a hash of exactly the state under review (`plan-review` — the active plan
set's paths + contents; `code-review` / `skill-review` — the staged diff;
`ready-for-review` — the HEAD sha). The `<repo-hash>.<session_id>` filename
exists to keep parallel sessions from overwriting each other's markers — a
write-path concern. Using those same keys as *read* predicates narrows the
gate to "this session reviewed this state" when the property the gate wants is
"this state has been reviewed."

Add one shared helper to `claude/.claude/hooks/_lib.sh`:

    _lib_marker_value_present <markers-dir> <expected-value> <glob-prefix>...

Returns 0 iff some file in `<markers-dir>` matching any supplied prefix has
content (whitespace-stripped) equal to `<expected-value>`. Implemented as a
single `grep -lFx` over the globbed set rather than a per-file `cat` loop, so
process count stays constant regardless of marker count.

**The glob must be `nullglob`-safe.** Bash's default glob behavior expands a
zero-match `<repo-hash>.*` to the literal, unexpanded pattern string, which
`grep` then tries to open as a real path and fails with "no such file"
(exit 2) rather than "ran, found nothing" (exit 1) — and "no marker exists yet
for this repo-hash" is the single most common call (first-ever review, or any
repo with no prior marker), so a naive implementation misclassifies the common
case as an error. Set `shopt -s nullglob` for the glob expansion (portable to
bash 3.2+, no GNU/BSD divergence) or pre-check for an empty match set before
invoking `grep`.

Applied per gate:

| Gate | Read glob after change | Session key | Repo key |
| --- | --- | --- | --- |
| `require-plan-review.sh` | `<repo-hash>.*` first, then every sibling worktree's repo-hash on a miss | dropped | widened to sibling worktrees |
| `require-code-review.sh` | `<repo-hash>.*` | dropped | kept |
| `require-ready-for-review.sh` | `<repo-hash>.*` | dropped | kept |
| `require-skill-review.sh` (plugin) | `<repo-hash>.*` | dropped | kept |

**Active-bypass markers stay strictly session-keyed and are not touched.**
`.plan-review-active.d/<session_id>` and
`.ready-for-review-active.d/<session_id>` assert "review is running in this
process right now" and are PID-liveness-checked. That is a genuinely
per-session property; only *completion* markers change.

**The plan-review read is two-tier, for latency.** The naive form — enumerate
every worktree, hash each root, glob them all — runs on every
`Write`/`Edit`/`MultiEdit`/`ExitPlanMode`, and `_marker_lib_repo_hash` forks
`sha256sum` plus `awk` per path. At this repo's 21 worktrees that is ~42 forks
plus a `git worktree list` on every gate hit, on top of the two capped git
calls `_lib_active_plan_hash` already spends. So: check the current repo-hash
prefix first (one hash, today's cost) — that alone covers the
resumed-session case, which is the common one — and only on a miss enumerate
sibling worktrees and scan their prefixes, which covers the copied-plan case.
The expensive path then runs only when the gate is about to deny anyway.

**The plan-review row deviates from a fully repo-agnostic read, deliberately.**
Dropping the repo-hash key outright would close #426 but would also let a
marker written in an *unrelated* repository release the gate here whenever two
repos happened to hold plan files with identical relative paths and contents —
the plan text would have been reviewed, but against a different codebase.
Enumerating the current repository's own worktree roots via
`git worktree list --porcelain`, hashing each, and globbing that bounded set
closes #426 with no cross-repo exposure. This is the lighter primitive that
satisfies the requirement; the wider read is not needed to get it.

**What the sibling-worktree scan actually authorizes, named explicitly.** The
scan validates content-identity of the plan text (the active-plan-set hash
matches some sibling worktree's marker), not state-identity of that sibling's
full checkout. Two worktrees on divergent branches that happen to hold
byte-identical plan files (a shared template, or a coincidentally identical
plan) would cross-validate even though the review in one never assessed the
other's actual HEAD. This is bounded to same-repo worktrees, so it is not a
new external-attacker surface, but it is a broader acceptance than "the #426
repro" alone — noted here so a future reader doesn't read the glob as a
stronger state guarantee than it provides.

**Divergence from #426's own proposed fix, stated for the reviewer.** The issue
proposes content-keying "while keeping the session-scoping guarantee."
Content-keying already shipped (the marker holds
`_lib_active_plan_hash`'s output); the residual defect is the *path* key, which
is the repo-hash. This plan additionally drops session scoping, because that
scoping is not a safety property: the stored hash already proves the review
covered this exact state, and the hook's own comment justifies per-session
keying solely as parallel-overwrite protection, which the filename retains.

### Write side — deny gate release to agents that cannot have reviewed

Extend `claude/.claude/hooks/enforce-marker-script-shape.sh`, which already
gates `marker.sh` Bash invocations fail-closed and already sources `_lib.sh`.
Read `.agent_type` fail-closed — the same trust-boundary handling
`deny-reviewer-tree-mutation.sh` uses — and deny when the caller is a
mandate-scoped agent and the command carries a `write` or `activate` operation.

New closed set in `_lib.sh`, alongside the existing `_LIB_REVIEW_ONLY_AGENTS`:

    _LIB_NO_GATE_RELEASE_AGENTS = _LIB_REVIEW_ONLY_AGENTS + code-writer

Grounding for the boundary: none of these agent types carries the `Skill`
tool (`code-writer` → Read/Edit/Write/Bash/Grep/Glob; the `staff-*`,
`ciso-reviewer`, `skill-fidelity-reviewer` set → Read/Grep/Glob/Bash/Write), so
none of them can invoke a review skill at all — any marker write from them is
necessarily unearned. `Explore` and `Plan` do carry `Skill` but are review-only
by mandate and are already enumerated. `general-purpose` and `claude` carry
`Tools: *` and can genuinely run the skill, so they keep the escape hatch
documented in `claude/.claude/CLAUDE.md`.

**Placement: immediately after the hook's Stage 1 substring test, before the
traversal guard and Stage 2's anchored-path check.** Stage 2 deliberately
fast-exits on wrapped forms (`bash -c '…/marker.sh write plan-review'`,
env-var prefix, relative path), leaving them to `permissions.allow`. An
`.agent_type` check placed after Stage 2 would inherit that hole, and
`permissions.allow` is not a reliable backstop under permissive permission
modes. Matching on `marker\.sh[[:space:]]+(write|activate)` anywhere in the
command catches tilde, absolute, relative, chained, and wrapped forms
uniformly.

Residual false-deny, accepted and to be named in the hook comment: a
review-only agent grepping for the literal string `marker.sh write` while
reviewing this repo is denied. Matching the op keyword rather than the bare
tool name keeps ordinary `grep -rn marker.sh` available, which is the common
reviewer action.

**`activate` is included, and it is the more dangerous verb.**
`marker.sh activate plan-review` writes the active-bypass marker holding the
live Claude PID, which the gate honors for all `Write`/`Edit` with no hash
comparison and no review whatsoever. The reported incident used `write`, which
at least carries a hash. Gating only `write` would leave the stronger bypass
open. `deactivate` and `clear-stale` are not gated — they only re-arm gates.

**`deactivate`'s narrow blast radius, noted for completeness.** A
mandate-scoped subagent can still call `marker.sh deactivate plan-review`,
which resolves `session_id` via the same ancestor walk and clears the
*parent* session's active-bypass marker. This is directionally safe — it
re-arms a gate rather than releasing one — but it is still a narrow-mandate
agent mutating the parent's review-in-progress state, which could disrupt a
live `/plan-review` run outside its own turn. Not gated by this change; worth
one line in the hook comment alongside the existing `deactivate`/`clear-stale`
rationale.

### Prose

Two strings become false and are corrected in place rather than supplemented:

1. `claude/.claude/CLAUDE.md`'s marker bullet says "if the skill is
   harness-blocked, spawn a subagent that can run it" without saying which
   subagents can. Drafted replacement for the final two sentences of that
   bullet, so it can be reviewed as text rather than as intent:

   > If a commit is blocked, run the review skill the hook names; if the skill
   > is harness-blocked, delegate it to a `general-purpose` subagent, which
   > carries the `Skill` tool. `code-writer` and the reviewer agents cannot run
   > review skills and are denied marker writes — when one hits a review gate,
   > it reports the denial and the dispatching session resolves it. A general
   > "ship it" instruction is not authorization to forge a marker.

2. All four gates' deny messages assert session scoping — most explicitly
   `require-ready-for-review.sh`'s "has not been gated by /ready-for-review in
   **THIS session**" and "in THIS session, or HEAD has moved." After this
   change those claims are wrong and would send an agent to re-run a review
   that already covers the state. Each must be reworded to the state-based
   claim ("no review covering the current staged diff / plan set / HEAD was
   found").

No new rule is added anywhere — the hook's deny message teaches at the moment
of need, which is the surface the agent actually reads.

### Alternatives set aside

- **Commit the outstanding plan files and stop there.** Disarms the six
  currently-armed gates but leaves the defect: any plan legitimately in flight
  re-arms on the next resume. Addresses the trigger, not the mechanism.
- **Re-key markers to the new session on resume** (SessionStart hook rewriting
  marker filenames). Requires knowing the prior `session_id`, which
  `SessionStart` does not supply, so it degrades into "adopt any dead-PID
  marker for this repo" — the read-side relaxation with extra machinery and a
  new write path. Strictly heavier for the same outcome.
- **Block subagent marker writes outright** (deny on any non-empty
  `.agent_type`). One-bit rule, but contradicts the documented escape hatch and
  would deadlock a fully-delegated `general-purpose` agent that legitimately
  ran the review itself.
- **A new `deny-subagent-gate-release.sh` hook.** Cleaner single
  responsibility, but would re-implement `enforce-marker-script-shape.sh`'s
  understanding of what a `marker.sh write` invocation looks like — duplicating
  the knowledge rather than referencing it.

### Assumption ledger

**Root problem:** a review marker's filename keys are read as authorization
predicates, so a valid review becomes unreachable across sessions and
worktrees; and nothing distinguishes a marker written by an agent that ran the
review from one written by an agent that could not have.

| # | Assumption / mechanism | Anchor | Tag |
| --- | --- | --- | --- |
| 1 | All four completion markers store a hash/sha of the exact state under review, so content alone is sufficient authorization | anchors: root | `[verified: marker.sh:136–197 write arms; _lib.sh:151 _lib_active_plan_hash]` |
| 2 | All four gates read the marker at `<dir>/<repo-hash>.<session_id>` and nowhere else | anchors: root | `[verified: require-plan-review.sh:136, require-code-review.sh:90, require-ready-for-review.sh:166, plugins/skill-management/hooks/require-skill-review.sh:181]` |
| 3 | Per-session keying was introduced to prevent parallel overwrite, not to scope authorization | anchors: root | `[verified: require-plan-review.sh:34–35 comment]` |
| 4 | A resumed session receives a new `session_id` | anchors: root | `[engineer-verified]` |
| 5 | `marker.sh` resolves `session_id` from the process ancestor chain, so a subagent's write is attributed to the parent session | anchors: root | `[verified: marker.sh:31–57 _walk_session]` |
| 6 | `.agent_type` is present on PreToolUse payloads for both Bash and Edit/Write, and empty for the main session | anchors: row 8 | `[verified: deny-reviewer-tree-mutation.sh:130–143, merged 3ea14d4; wired under both matchers]` |
| 7 | `activate` releases the plan gate with no hash check | anchors: row 9 | `[verified: require-plan-review.sh:118–127 active-marker bypass]` |
| 8 | Agent types in the no-release set cannot invoke a review skill (no `Skill` tool), making any marker write from them unearned | anchors: row 8 | `[verified: agent roster tool lists]` |
| 9 | #426's repro is a same-repository worktree copy, so a sibling-worktree glob closes it without cross-repo release | anchors: row 4 | `[verified: issue #426 body — "copied into a fresh worktree path"; plan hash uses repo-RELATIVE paths per _lib.sh:104–108, so a copy at the same relative path hashes identically]` |
| 10 | Marker directories are unbounded and already large (~1000+ entries), so the read must not fork per file | anchors: row 1 | `[verified: plan-review-markers/ listing 111.7KB]` |
| 11 | Stage 2 of `enforce-marker-script-shape.sh` fast-exits wrapped forms, so an agent check placed after it is bypassable | anchors: row 10 | `[verified: enforce-marker-script-shape.sh:68–70 + its own header comment]` |
| 12 | `permissions.allow` is not a dependable backstop for wrapped forms under permissive permission modes | anchors: row 10 | `[unverified]` — asserted from the hook's own "permissions.allow is their gate" comment; not tested per-mode. Does not change the design (placing the check early is correct regardless) but do not cite it as fact in a code comment. |

Row 12 is the one remaining unverified assumption; it motivates but does not
determine the placement decision.

## Implementation steps

Ordered so each step leaves the tree green. Steps 1–2 are the shared
foundation; 3–6 are independent per-gate applications; 7–9 close out.

1. **`claude/.claude/hooks/_lib.sh`** — add `_lib_marker_value_present`
   (single `grep -lFx` over globbed prefixes, `shopt -s nullglob`'d so a
   zero-match glob returns "not found" rather than a grep error on a literal
   pattern string; return non-zero on absent directory or no match). Add
   `_LIB_NO_GATE_RELEASE_AGENTS` and
   `_lib_is_no_gate_release_agent` directly beside the existing
   `_LIB_REVIEW_ONLY_AGENTS` / `_lib_is_review_only_agent`, reusing that
   exact-match loop shape. No call sites yet.
2. **`claude/.claude/hooks/tests/test_lib.py`** — cover the new helper and
   predicate before wiring callers: match, no-match, trailing newline,
   multiple glob prefixes, absent directory, a matching value present under
   a *non*-matching prefix (must not match), a stored value that is a proper
   substring/prefix of the expected value (must not match — pins the `-x`
   whole-line exactness against a future regression to bare `-lF`), and a
   zero-match glob returning cleanly rather than a `grep` error on the
   unexpanded literal pattern.
3. **`claude/.claude/hooks/require-plan-review.sh`** — replace the
   single-path read (~135–142) with the two-tier lookup: current repo-hash
   prefix, then sibling-worktree prefixes on a miss. Enumerate worktrees via
   `_lib_capped git worktree list --porcelain`, status-checked and
   fail-closed on partial enumeration, matching `_lib_active_plan_hash`'s
   discipline. Reword the deny message off "this session." Leave the
   active-marker block (~118–127) untouched. **`test_require_plan_review.py`
   already has two tests that encode the invariant this step reverses —
   `test_other_sessions_marker_does_not_authorize` and
   `test_no_session_id_in_input_denies`. Rewrite them deliberately to assert
   the new cross-session-acceptance behavior; do not let them surface as
   surprise CI failures and patch them reactively — they are the canary for
   the defect this plan closes.**
4. **`claude/.claude/hooks/require-code-review.sh`** — replace read at ~90–95
   with `_lib_marker_value_present` on `<repo-hash>.*`; reword deny message.
5. **`claude/.claude/hooks/require-ready-for-review.sh`** — replace read at
   ~164–170; reword both deny messages off "THIS session"; leave the
   active-marker block at ~143 untouched.
6. **`claude/.claude/hooks/enforce-marker-script-shape.sh`** — insert the
   `.agent_type` gate immediately after the Stage 1 substring test, matching
   `marker\.sh[[:space:]]+(write|activate)`. Document the accepted
   false-deny (grepping the literal op string) and the reason for the early
   placement in the header comment.
7. **Plugin arm** — duplicate `_lib_marker_value_present` into
   `plugins/skill-management/hooks/_lib.sh` (the plugin ships its own
   `_lib.sh`; no shared partials across the plugin boundary), apply it in
   `require-skill-review.sh` at ~181–186, reword its deny message, and bump
   `plugins/skill-management/.claude-plugin/plugin.json` per `plugin-semver`.
8. **Tests for steps 3–7** — see Verification.
9. **Prose** — apply the two drafted CLAUDE.md sentences; run
   `/ai-instruction-and-memory-files` on that text. Update the README hooks
   table and any per-hook doc that states session-scoped marker semantics
   (`git grep -n 'THIS session\|per-session marker\|session_id' docs/ README.md`
   to enumerate before editing).

## Critical files

**Create:** none.

**Modify:** the files named in steps 1–9 above, plus:

- `claude/.claude/hooks/tests/test_require_plan_review.py`,
  `test_require_code_review.py`, and `test_require_skill_review.py` (lines
  ~504–604 in the plan-review file, parallel blocks in the others) — these
  re-read the `HOOK_TEST_FIXTURE` fenced blocks from each skill's SKILL.md and
  assert they match the hook's marker-write recipe. Changing the read path may
  break those assertions; check and update in step 8. (`test_hook_alignment.py`
  is a separate, generic three-layer suite — docs-coverage, hook-class header
  validation, deny-envelope-on-malformed-input — with no `HOOK_TEST_FIXTURE`
  references; it is not the file that needs updating for this change.)
- `claude/.claude/hooks/tests/test_require_plan_review.py` — additionally,
  rewrite `test_other_sessions_marker_does_not_authorize` and
  `test_no_session_id_in_input_denies` (see step 3): these currently assert
  the cross-session-denial behavior this plan makes obsolete.
- `claude/.claude/hooks/tests/test_agent_roster.py` — add a mandatory (not
  conditional) roster-sync test: for every name in
  `_LIB_NO_GATE_RELEASE_AGENTS`, assert `agents/<name>.md`'s `tools:`
  frontmatter excludes `Skill`, mirroring the shape of the existing
  roster-sync assertions. This is the mechanical enforcement for the
  boundary's own grounding (assumption row 8): a future agent added to or
  removed from the no-release set without a matching `tools:` change must
  fail this test, not rely on manual verification.

**Reuse rather than reimplement:**

- `_marker_lib_repo_hash` (`_lib.sh:100`) for every repo-hash computation on
  both sides — do not inline `sha256sum`.
- `_lib_capped` for the new `git worktree list` call; a partial enumeration
  must fail closed, not silently scan fewer markers.
- `_lib_is_review_only_agent`'s exact-match loop shape for the new predicate.
- `_lib_parse_tool_input_or_deny` and each hook's existing `emit_deny`.
- Test helpers in `claude/.claude/hooks/tests/helpers.py`:
  `plan_review_marker_path`, `write_plan_review_marker`, `run_hook`,
  `run_hook_reason`, `edit_input`, `write_input`, `bash_input`.

## Verification

**Automated:**

    ../../../.venv/bin/pytest claude/.claude/ plugins/
    ../../../.venv/bin/ruff check claude/.claude/
    scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck

New test cases:

- `test_require_plan_review.py` — a marker written under session A releases for
  session B at the same plan hash; a plan edit still re-arms; the #426 repro
  (plan copied into a sibling worktree) passes; a marker from an unrelated repo
  root with identical plan content does **not** release; a byte-different
  decoy marker coexisting in the same scanned repo-hash prefix as the correct
  one does **not** cause a false accept; partial `git worktree list` output
  fails closed. Plus the rewritten
  `test_other_sessions_marker_does_not_authorize` and
  `test_no_session_id_in_input_denies` (see step 3).
- `test_require_code_review.py`, `test_require_ready_for_review.py`, and the
  plugin's `require-skill-review` tests — cross-session acceptance at a
  matching value; rejection at a changed value; rejection of a matching value
  stored under a different repo-hash prefix.
- `test_enforce_marker_script_shape.py` — `code-writer` and each review-only
  agent denied on `write` (all four skills) and `activate` (all four targets),
  including inside an `&&` chain **and** in wrapped form
  (`bash -c '~/.claude/scripts/marker.sh write plan-review'`); `deactivate`
  and `clear-stale` still allowed; `general-purpose`, `claude`, and empty
  `.agent_type` allowed; unreadable `.agent_type` denies; a review-only agent's
  plain `grep -rn marker.sh` still allowed.

**Accepted, untested edge cases (stated, not silent).** A torn/partial marker
write read mid-scan, and a stale sibling-worktree repo-hash whose checkout has
since been removed, both fail closed by construction (a torn read either
mismatches the expected hash or errors past `grep -lFx`; a stale hash yields a
no-match glob) — the plan's cooperative, non-adversarial threat model does not
warrant dedicated tests for either, but both are noted here rather than left
as an unstated gap.

**Manual end-to-end** (the incident, replayed): in a worktree with one
uncommitted plan, run `/plan-review`, note the marker, then start a fresh
session in the same worktree and attempt an `Edit` — allowed, no new review.
Then dispatch a `code-writer` subagent and have it attempt
`marker.sh write plan-review` — denied, reports back.

**Latency check:** an automated pytest case (not a one-time manual `time`
invocation — the marker directory's growth is unbounded and deferred, see Out
of scope, so a manual check validates today's size once and gives no
regression signal as it grows) that seeds N synthetic markers, runs
`require-plan-review.sh` against both tiers — the common-path hit (current
repo-hash) and the fallback scan — and asserts wall-time stays under a
numeric budget set from the existing two capped git calls' own budget.

**Rollback:** all changes are shell and test files; `git revert` restores prior
behavior with no state migration, since existing markers keep their filenames
and the write path is untouched. Note that `claude/` is stowed, so a landed
regression goes live for anyone who pulls — verify on this branch's worktree
before merging.

## Out of scope

- **Committing the six worktrees' active plan files** (GH-241, GH-333, GH-472,
  GH-477, `lovable-migration-revert-safety`, `sessions-dir-gc`). Each belongs
  to its own branch; committing them means six commits on six unrelated
  branches. Listed in the handoff for a per-branch decision. The read-side fix
  removes their resume hazard regardless.
- **Marker-directory garbage collection.** The directories grow without bound
  and this change reads more of them than before. Related to the in-flight
  `sessions-dir-gc` work; file separately rather than bundling.
- **`_marker_lib_repo_hash`'s `sha256sum` dependency has no BSD/macOS
  fallback** (unlike `_lib_jq`/`_lib_capped`, which document one). Pre-existing
  gap, not introduced by this change — but the sibling-worktree fallback tier
  turns one call into ~21 (today's worktree count) on the miss path, all
  against the same missing binary, on the machine class (stow-distributed, any
  cloner) this repo targets. Accepted as-is for this change; fixing it is a
  separate, broader fix to a helper with many other call sites.
- **Issues #430 and #415**, which touch adjacent surfaces (the marker-shape
  hook's heredoc scanning; the ready-for-review push gate) but are distinct
  defects.
- **Specialist plan review — complete.** `ciso-reviewer`,
  `staff-platform-engineer`, and `staff-sdet` were spawned against this plan on
  resume. Their findings are incorporated above: the `nullglob` fix (Read
  side), the content-identity-vs-state-identity note (Read side), the
  `deactivate` blast-radius note (Write side), the mandatory roster-sync test
  and corrected `test_hook_alignment.py` citation (Critical files), the
  automated latency assertion and new edge-case tests (Verification), the two
  named existing tests to rewrite (step 3), and the `sha256sum` fallback note
  (above). No blocking findings remained unaddressed.
