# Arm the plan-review gate during harness plan mode

## Context

`require-plan-review.sh` does not gate `ExitPlanMode` at all when a session
is in harness plan mode, so a plan can be presented for approval with zero
review. The goal is to close that gap without weakening the gate's existing
content-addressed design or widening `marker.sh`'s zero-argument invocation
contract.

Harness plan mode restricts a session to read-only tools plus writes to
exactly one harness-designated path (named in the plan-mode system reminder,
under `<config-dir>/plans/`), which sits outside every git repository.
`_lib_active_plan_hash` (`claude/.claude/hooks/_lib.sh:339-417`) globs only
`<repo>/.claude/plans/*.{md,txt}`. During plan mode that glob is always
empty, so `require-plan-review.sh`'s `CURRENT_HASH` is empty, and its
`[ -z "$CURRENT_HASH" ] && exit 0` lets `ExitPlanMode` through unconditionally
— no marker lookup happens at all. `claude/.claude/CLAUDE.md` § Plan Review
currently asserts the hook "backs this mechanically... when calling
`ExitPlanMode` from the harness built-in plan workflow." That is false today,
for every stow user, whenever plan mode is used without a pre-existing
repo-relative plan.

This is the sixth design attempted for this problem. Five prior directions
were designed and killed on review — each closed the previous round's hole
and opened a new one. The dead directions, and why, for reviewers who want
the full record: exempting `marker.sh`'s plan-review write arm from
`_refuse_main_tree_under_enforcement` (writes a vacuous marker — plan mode's
repo-relative `.claude/plans/` is empty); having `plan-it` create a worktree
during plan mode (plan mode is read-only-plus-one-path, categorically, not as
an edge case); hashing a `PostToolUse`-captured file path (never bound to
what `ExitPlanMode` actually presents, so a reviewed-plan-A / presented-plan-B
swap was possible); hashing `tool_input.plan` text directly via a new
`marker.sh write plan-review <path>` argument (denied outright by
`enforce-marker-script-shape.sh`'s allowlist and `permissions.allow`'s
exact-match rule, both of which reject any argument on any `marker.sh`
subcommand today, and fixing that needs a glob that
`claude/.claude/CLAUDE.md` explicitly forbids); and self-deriving the
write-side path via a mtime-newest-file scan of `~/.claude/plans/` (that
directory is unguarded for writes and accumulates unboundedly, so a session
could plant unreviewed content, make it mtime-newest, and have an unrelated
`/plan-review` run hash it instead of what was actually reviewed).

## Approach

**Read side and write side split cleanly, and only one of them needed new
state.**

**Read side — `require-plan-review.sh`, `ExitPlanMode` branch only.** The
harness populates `tool_input.plan` and `tool_input.planFilePath` on the
actual `ExitPlanMode` call being gated — confirmed against this codebase's
own test fixture, `claude/.claude/tests/helpers.py:449-459`
(`exitplanmode_input()`), which documents both fields as "verified
empirically via live plan-mode session observation." Because this value
arrives on the very call being gated, the hook can hash it fresh from disk
at gate-check time with no stored state and no trust dependency on anything
written earlier in the session — the same freshness guarantee
`_lib_active_plan_hash` already gives the repo-relative case. Add a branch:
when `TOOL_NAME` is `ExitPlanMode` and `tool_input.planFilePath` is
non-empty, hash that file's content and check it against
`plan-review-markers/` (same `REPO_HASH`-prefixed lookup
`_lib_marker_value_present` already does) **before** falling through to the
existing repo-relative check. This ordering is required, not cosmetic: a
session that already has a reviewed repo-relative plan marker and opens a
*nested* plan-mode question would otherwise satisfy the gate on that stale
repo marker while presenting unreviewed plan-mode content. Giving plan-mode
priority on `ExitPlanMode` specifically closes that without touching the
Write/Edit/MultiEdit branches, which have no `planFilePath` to prioritize
over anything.

**Write side — `marker.sh write plan-review` must stay zero-argument.**
Every one of `marker.sh`'s 14 invocation shapes is argument-free today, both
in `enforce-marker-script-shape.sh`'s allowlist regex and in
`permissions.allow`'s exact-match rule (`claude/.claude/settings.json:2-18`)
— widening either to accept a path is the CLAUDE.md-forbidden glob a prior
round already tried. So `marker.sh` cannot itself learn the plan-mode file's
path as an argument, and it has no other way to discover it: the path is
named only in the plan-mode system reminder shown to the *model*, not
exposed to a Bash subprocess by the harness. Only the session running
`/plan-review` actually knows it.

The lever this uses: `enforce-marker-script-shape.sh`'s Write/Edit/MultiEdit
arm (`claude/.claude/hooks/enforce-marker-script-shape.sh:117-135`) only
denies a payload carrying `.agent_type` — i.e. a harness-dispatched
subagent. A payload with no `agent_type` key at all (the main session,
confirmed by reading the hook's in-shell fast-reject: `case "$INPUT" in
*'"agent_type"'*) ;; *) exit 0 ;; esac`) passes through unconditionally.
`claude/.claude/settings.json` has zero `Write(...)` entries, so no new
`permissions.allow` rule is needed either — this is a plain Write-tool call
under the harness's ordinary permission mode, same as any other main-session
Write. So `/plan-review`'s own Step 0 — immediately after the existing
`marker.sh activate plan-review` call that already creates
`.plan-review-active.d/` and writes the PID file — can declare the plan-mode
path directly via its own Write tool into the same protected marker-shape
state that already denies restricted subagents (`skill-review` flagged Step
0, not Step 1, as the right placement: it keeps every marker-state write
this review performs in one place, and avoids a window where Step 1 is
interrupted after identifying plan-mode content but before the sibling file
exists) —
closing the round-5 flaw (an inferred, gameable path) by using a path the
session *asserts*, not one a heuristic *infers*. A forger would need to be
the trusted main session making a false assertion — the identical trust
floor the repo-relative case already accepts everywhere in this system
(nothing independently verifies a main session genuinely read a
repo-relative plan either), so this is parity with the existing model, not
a new weaker oracle.

**State shape: a sibling file, not a second line.** The existing
`.plan-review-active.d/$SESSION_ID` file holds the review PID for
`_lib_active_bypass_marker_live`'s liveness check
(`claude/.claude/hooks/_lib.sh:728-756`), read via `cat "$marker" | tr -d
'[:space:]'`. That strips *all* whitespace, including newlines — a second
line appended to the same file would concatenate into the PID string and
break the `^[0-9]+$` liveness regex for every marker sharing this helper
(plan-review, ready-for-review, respond-pr, memory-skill all key off it).
Storing the declared path in a **new sibling file** in the same directory —
`.plan-review-active.d/$SESSION_ID.planmode-path` — avoids touching that
parsing at all, and needs no new protected-path pattern:
`enforce-marker-script-shape.sh`'s existing case arm matches
`*/.claude/.*-active.d/*`, which already covers any file inside
`.plan-review-active.d/`, sibling or not.

`marker.sh write plan-review` then becomes: if
`.plan-review-active.d/$SESSION_ID.planmode-path` exists and names a
readable file, hash **that file's current content, read fresh at write
time** (not the path's mere existence, and not any hash computed earlier) —
this mirrors the codebase's existing determinism contract that write-side
and read-side always hash live disk content, so a plan revision mid-review
is still caught. Otherwise, fall through to today's
`_lib_active_plan_hash "$REPO_ROOT"` behavior, unchanged. These two cases
are mutually exclusive in the flows `plan-it` already documents (plan mode
and repo-relative `.claude/plans/` are alternatives for a given plan, never
both for the same review), so no priority/combination logic is needed here
— only the read side's `ExitPlanMode` branch needs the priority rule, since
that is the one path where a stale repo marker and fresh plan-mode content
can coexist in the same session.

`marker.sh deactivate plan-review` gains a matching `rm -f` for the sibling
file, symmetric with its existing cleanup of the PID file, the
routing-read marker, and the pending-read marker.

**Fail-closed and bounded, on both new hash calls — required, not optional.**
Two review passes (`staff-sdet`, `staff-platform-engineer`) independently
identified that the description above under-specifies error handling, and
these are two distinct failure modes needing two distinct fixes at the same
two call sites:
- **Unbounded read (availability).** `staff-platform-engineer`: neither new
  `sha256sum` call (read side, against `tool_input.planFilePath`'s target;
  write side, against the sibling file's declared target) is specified as
  routed through `_lib_capped`, unlike every existing git/sha256sum call in
  this hook family (`_lib_active_plan_hash`, `_lib.sh:339-408`). Both
  `planFilePath` and the sibling file's declared target can point under
  `$CLAUDE_CONFIG_DIR`/`$HOME`, which is not guaranteed to be a local disk —
  a stalled network mount would hang the read() indefinitely, blocking the
  gate check (read side) or the `/plan-review` skill run (write side) with
  no cap, no log line, and no visible error. Both new calls must route
  through `_lib_capped`, matching `_lib_active_plan_hash`'s existing
  pattern exactly — this is a required "Critical files" line item on both
  files, not an implementation detail left to discretion.
- **Missing/unreadable target, once the read *does* return (correctness).**
  `staff-sdet`: a present-but-empty `tool_input.planFilePath`, an absent
  field, and a non-empty field naming a file that doesn't exist or can't be
  read are three different states this plan's prose (as originally written)
  collapsed into one. Read side: a target that fails to hash (empty
  `sha256sum` output after `_lib_capped` returns, whether from timeout,
  missing file, or permission) must **deny** — falling through to the
  repo-relative check would silently re-permit the exact silent-allow bug
  this plan exists to close, if a stale repo-relative marker happens to be
  present. Write side: a sibling file that exists but names a target that
  fails to hash must **abort without writing a marker** (matching
  `_lib_active_plan_hash`'s own "abort without writing" contract for an
  unreadable plan file) — falling through to `_lib_active_plan_hash` here
  would silently write a completion marker that doesn't cover what was
  actually reviewed. A sibling file that simply doesn't exist is the only
  case that correctly falls through to `_lib_active_plan_hash`.

**`clear-stale` must not evict the sibling file.** `ciso-reviewer` and
`staff-sdet` independently found the same defect: `marker.sh clear-stale`
iterates every entry under each `.*-active.d/` directory and evicts any
whose content doesn't match the PID regex `^[0-9]+$`
(`enforce-marker-script-shape.sh`'s own comment: "`clear-stale` only evicts
dead-PID bypass markers" — a claim this plan would make false). The sibling
file holds a path, never a PID, so any `clear-stale` run — a normal,
CLAUDE.md-documented user self-service action for an unrelated stuck gate —
unconditionally deletes it, live review or not. This fails closed (a
spurious deny after an apparently-clean review), not open, but it is a real
reliability defect in the feature this plan ships, not a hypothetical.
`clear-stale`'s eviction loop must skip `.plan-review-active.d/` entries
whose filename ends in `.planmode-path` (a static, name-based exemption —
no PID-liveness question applies to this file at all, so "skip by name" is
correct, not merely convenient).

**Alternatives considered and set aside:**
- *Extending the PID file to two lines* — set aside per the
  `_lib_active_bypass_marker_live` parsing conflict above; a sibling file
  costs one more `mkdir -p`-free file in an already-`mkdir -p`'d directory
  and touches zero shared parsing.
- *A brand-new top-level directory (e.g. `.plan-review-planmode-active.d/`)*
  — set aside because it would need its own entry in
  `enforce-marker-script-shape.sh`'s protected-path case arm, whereas the
  sibling-file approach is covered by the existing pattern with a zero-line
  hook diff for that file. Fewer lines touched in a security-relevant hook
  is the deciding factor, not a meaningful functional difference.
- *Having the read side also prioritize plan-mode content on Write/Edit,
  not just `ExitPlanMode`* — set aside because Write/Edit calls never carry
  `tool_input.planFilePath` (only `ExitPlanMode` does). The reason this is
  safe is the always-fresh-hash property, not a harness restriction on where
  Write/Edit can target during plan mode — `ciso-reviewer` flagged that
  citing the latter overstates what actually holds: `claude/.claude/CLAUDE.md`
  § Agent Briefing documents plan-mode tool restriction as model-obeyed for a
  dispatched subagent ("the agent obeying an instruction, not a hard harness
  block"), and this plan never independently confirmed the main session's own
  restriction is a hard block either. Soundness never
  depended on that restriction: `ExitPlanMode` always re-hashes fresh
  regardless of what wrote to the file in between, so no Write/Edit
  prioritization is needed either way.

**New pattern this introduces, named explicitly for review.** Every existing
`SKILL.md` that touches marker state does so exclusively through
`marker.sh` (confirmed: `grep -rl "active.d\|-markers/"
claude/.claude/skills/*/SKILL.md` returns only `code-review` and `handoff`,
neither of which writes marker state directly — grep for the specific
shapes found no direct writes anywhere). This plan makes `plan-review` the
first skill to Write directly into protected marker-shape state itself,
rather than exclusively through `marker.sh`. The mechanism-level reasoning
above is why this specific delta is believed safe (parity with existing
trust floor, no new permission surface, no pattern change needed in the
enforcement hook) — flagged here so `ciso-reviewer` and `staff-sdet`
evaluate that specific claim rather than the feature as a whole.

### Assumption ledger

**Root:** `ExitPlanMode` is unguarded whenever a session is in harness plan
mode with no pre-existing repo-relative plan, which is the common case —
`plan-review` cannot record a review of plan-mode content today.

**Givens:**
- Harness plan mode restricts writes to exactly one path and gives no
  argument-passing surface into `marker.sh` — a platform boundary, outside
  this plan's reach. `[verified: this session's own plan-mode system
  reminder text, and ExitPlanMode's tool description via ToolSearch, which
  states "This tool does NOT take the plan content as a parameter... it will
  read the plan from the file you wrote"]`
- `marker.sh`'s zero-argument invocation shape is a deliberate security
  property (per `enforce-marker-script-shape.sh`'s own header comment) that
  this plan must not widen. `[engineer-verified — CLAUDE.md: "Don't add
  globs... to permissions.allow"]`

**Mechanisms:**
- `tool_input.planFilePath` exists on the real `ExitPlanMode` payload.
  `[verified: claude/.claude/tests/helpers.py:449-459, exitplanmode_input(),
  doc-commented as empirically verified against a live plan-mode session]`
  anchors: root
- Main-session Write to a marker-shape path is not denied by
  `enforce-marker-script-shape.sh`. `[verified: read the hook's Write/Edit/
  MultiEdit arm directly, claude/.claude/hooks/enforce-marker-script-shape.sh
  lines ~117-135 — fast-rejects any payload without a literal `"agent_type"`
  substring]` anchors: row (write-side mechanism)
- No new `permissions.allow` entry is needed for that Write.
  `[verified: claude/.claude/settings.json has zero Write(...) entries under
  permissions.allow/ask/deny — read directly]` anchors: row (write-side
  mechanism)
- A second line in `.plan-review-active.d/$SESSION_ID` would break
  `_lib_active_bypass_marker_live`'s liveness parsing.
  `[verified: read _lib.sh:728-756 — cat | tr -d '[:space:]' collapses
  multi-line content before the `^[0-9]+$` regex test]` anchors: row
  (state-shape mechanism)
- A sibling file in `.plan-review-active.d/` matches
  `enforce-marker-script-shape.sh`'s existing protected-path glob with no
  hook change. `[verified: read the case arm's pattern,
  `*/.claude/.*-active.d/*`, which matches any file under that directory
  name, not a specific filename]` anchors: row (state-shape mechanism)
- Plan mode and a repo-relative active `.claude/plans/` plan are mutually
  exclusive for a single plan under `plan-it`'s own flow.
  `[verified: claude/.claude/skills/plan-it/SKILL.md:17 — plan-mode branch
  writes to the harness path and explicitly moves it into
  `.claude/plans/<slug>.md` only after `ExitPlanMode` is approved, never
  both at once]` anchors: row (write-side mutual-exclusion claim) — but see
  **Out of scope** below for the nested-plan-mode case this doesn't cover.
- Every existing git/sha256sum call in this hook family routes through
  `_lib_capped`, and `marker.sh clear-stale`'s eviction loop treats any
  non-PID-shaped entry under a `.*-active.d/` directory as a dead marker to
  remove. `[verified: staff-platform-engineer read _lib_active_plan_hash's
  existing _lib_capped usage directly; ciso-reviewer and staff-sdet
  independently read clear-stale's eviction loop and enforce-marker-script-
  shape.sh's own "clear-stale only evicts dead-PID bypass markers" comment]`
  anchors: row (write-side mechanism, state-shape mechanism) — the new
  sibling-file branches on both hooks must conform to both existing
  behaviors rather than silently deviating from them; see the Approach
  section's fail-closed/`_lib_capped`/`clear-stale` subsections.
- The harness's `ExitPlanMode` approval UI renders content from
  `planFilePath` (the file this gate hashes), not independently from the
  same payload's `tool_input.plan` (inline text). `[unverified — BLOCKING:
  the existing test fixture (`exitplanmode_input()`) sets `plan` and
  `planFilePath` to different content specifically to isolate them for unit
  testing, which does not establish what the real harness does; see
  Verification's blocking precondition]` anchors: root — if this proves
  false, the gate's chosen hash target is wrong and the design needs to
  hash `.plan` instead of (or in addition to) the file.

**Over-powered-primitive check (per plan-it Step 5):** the chosen mechanism
(direct Write from the main session into existing protected state) is the
*lighter* of the two primitives available — the heavier one, a new
`marker.sh` argument, was already tried and correctly rejected in a prior
round for widening a deliberately narrow invocation contract. A second
lighter alternative considered: a `PostToolUse` hook auto-capturing the
plan-mode file path with no explicit skill-side declaration — rejected
(round 3) because it captures *a* file, not necessarily the one later
presented to `ExitPlanMode`, reopening the reviewed-A/presented-B binding
gap this design closes by construction (the read side hashes
`tool_input.planFilePath` directly, so no capture step can ever drift from
what is actually presented).

## Critical files

- `claude/.claude/hooks/require-plan-review.sh` — add the `ExitPlanMode`
  plan-mode branch (hash `tool_input.planFilePath` via `_lib_capped
  sha256sum`, denying if that hash fails — empty/absent field falls through
  to the existing repo-relative check unchanged, but a non-empty field
  naming an unreadable or timed-out target denies rather than falling
  through; check a successful hash against `plan-review-markers/`, priority
  over the repo-relative check on this tool only). Reuse
  `_lib_marker_value_present` and `_marker_lib_repo_hash` exactly as the
  existing repo-relative check does — no new marker-directory scanning logic
  needed.
- `claude/.claude/scripts/marker.sh` — `write plan-review` case: check for
  `.plan-review-active.d/$SESSION_ID.planmode-path` via `_lib_capped`; if
  present, hash its target fresh via `_lib_capped sha256sum`, aborting
  without writing a marker if that hash fails (unreadable/timed-out target —
  matching `_lib_active_plan_hash`'s existing abort contract, not a silent
  fall-through); if absent, fall back to `_lib_active_plan_hash` unchanged.
  `deactivate plan-review` case: add the sibling-file `rm -f`. `clear-stale`
  case: skip `.plan-review-active.d/*.planmode-path` entries in its eviction
  loop (name-based exemption, not a PID-liveness check).
- `claude/.claude/skills/plan-review/SKILL.md` — Step 0 ("Activate gate
  session"), immediately after the existing `marker.sh activate plan-review`
  call: add the plan-mode branch (detect the plan-mode system reminder, read
  the harness-designated path from it, Write the sibling declaration file).
  **The plan does not draft this addition's literal text** — `skill-review`
  flagged that a description is not sufficient to implement or re-review
  this step from: it is a new file-write side effect inside an existing
  step, with its own `HOOK_TEST_FIXTURE`-style anchor comment, and
  `skill-review`'s own checklist (voice, length, behavioral-equivalence)
  can't be applied to a paraphrase. Drafting the literal addition — matching
  the existing `activate-gate` / `deactivate-gate` / `record-completion`
  fixture-comment convention already in this file, since
  `claude/.claude/tests/helpers.py`'s `run_skill_command` +
  `extract_skill_command` machinery re-reads fenced blocks directly from
  this file rather than duplicating the recipe in test code — is
  implementation work, not a plan-stage deliverable; it must pass its own
  `skill-review` pass against the drafted text at implementation time,
  per this skill's own Domain: Claude Code config routing.
- `claude/.claude/hooks/_lib.sh` — no change. Confirmed
  `_lib_active_bypass_marker_live` and `_lib_active_plan_hash` are both
  reused as-is; the sibling file is read directly by `marker.sh`, not
  through either helper.
- `claude/.claude/hooks/enforce-marker-script-shape.sh` — no change.
  Confirmed the existing protected-path case arm already covers the new
  sibling file's path shape.
- `claude/.claude/CLAUDE.md` § Plan Review — no textual change needed; the
  existing claim ("backs this mechanically... when calling `ExitPlanMode`
  from the harness built-in plan workflow") becomes true once this ships,
  rather than needing correction.

## Verification

**Blocking precondition, before implementation proceeds.** `ciso-reviewer`
flagged that this plan's soundness assumes the harness's `ExitPlanMode`
approval UI renders content sourced from `planFilePath` (the file this gate
hashes) and not independently from `tool_input.plan` (the inline text field
also present on the same payload, per `exitplanmode_input()`). Nothing in
this codebase confirms which field the UI actually renders from — the two
are set to *deliberately different* content in the existing test fixture,
which proves only that the fixture isolates the two fields for unit testing,
not that the real harness ever produces divergent content between them. If
they can diverge and the UI renders `.plan` while this gate hashes
`.planFilePath`, a human could approve content different from what the gate
verified. Confirm this empirically (a scratch plan-mode session, or a
maintainer with harness-internals visibility) before relying on this gate
for anything beyond same-team, non-adversarial plan authors; if they can
diverge, hash `.plan` instead of (or in addition to) the file.

- `claude/.claude/hooks/tests/test_require_plan_review.py`: new cases using
  the existing `exitplanmode_input()` helper —
  - matching plan-mode hash allows `ExitPlanMode` (marker's stored value
    equals a fresh hash of the `planFilePath` target);
  - stale/no marker denies `ExitPlanMode` with `planFilePath` set and no
    repo-relative plan present (today's silent-allow case — the regression
    test for the actual bug this plan fixes);
  - nested-plan-mode case, constructed with two **distinct, non-equal**
    hashes (a repo-relative plan file and a `planFilePath` target with
    different content) so the test is actually sensitive to the ordering
    bug it claims to catch: a repo-relative plan marker is valid and fresh,
    but `tool_input.planFilePath` names different, unreviewed content —
    must deny, proving plan-mode priority over the stale repo marker. Add a
    companion case on the *pre-fix* branch ordering (repo-check-first)
    asserting it would wrongly allow, confirming the test would actually
    fail without this plan's ordering fix;
  - `planFilePath` absent vs. present-but-empty-string vs. present-and-set —
    assert the first two both fall through to today's repo-relative
    behavior unchanged (do not assume `// empty` treats them identically
    without a test);
  - `planFilePath` naming a nonexistent or unreadable target (present,
    non-empty, file missing or permission-denied) — must **deny**, not fall
    through to repo-relative, even when a valid repo-relative marker exists;
  - `planFilePath` naming a target that hangs the read (simulate via
    `_lib_capped`'s timeout path, not an actual hang) — must deny within the
    capped budget, not block the tool call.
- `claude/.claude/hooks/tests/test_marker_script.py`: new cases —
  - `write plan-review` with a valid `.planmode-path` sibling file present
    stores a hash of that target's current content, not
    `_lib_active_plan_hash`'s result;
  - editing the target between sibling-file declaration and `write
    plan-review` changes the stored hash (freshness, not a cached value);
  - sibling file **absent** falls back to `_lib_active_plan_hash`
    (unchanged behavior) — distinct from the next case;
  - sibling file **present but its declared target is missing/unreadable**
    aborts without writing any marker (matching
    `_lib_active_plan_hash`'s existing abort contract) — must NOT silently
    fall back to `_lib_active_plan_hash` and write a marker that doesn't
    cover what was reviewed;
  - `deactivate plan-review` removes the sibling file alongside the existing
    three;
  - `clear-stale` does **not** evict a live sibling file — run `clear-stale`
    with the sibling file present (regardless of the PID file's liveness
    state) and assert the sibling file survives;
  - `activate plan-review` behavior is unchanged (no new file at activate
    time — the sibling file is written by the skill's Step 0, not by
    `marker.sh activate`).
- `claude/.claude/hooks/tests/test_enforce_marker_script_shape.py`: a case
  confirming a main-session (no `agent_type`) Write to
  `.plan-review-active.d/$SESSION_ID.planmode-path` passes through, and a
  restricted-subagent payload targeting the same path is still denied —
  the specific claim flagged for `ciso-reviewer`/`staff-sdet` sign-off.
- `claude/.claude/hooks/tests/test_marker_lib.py` (or wherever
  `_lib_active_bypass_marker_live` is covered): confirm a live PID marker
  with a sibling `.planmode-path` file present in the same directory still
  liveness-checks correctly, **and** the reverse — the sibling file's own
  content/hash-read logic is unaffected by the PID file's liveness state —
  the two-way non-interference claim this plan's state shape depends on.
- The three existing `HOOK_TEST_FIXTURE` recipes (`activate-gate`,
  `deactivate-gate`, `record-completion`) are executed — not merely
  scanned — via `extract_skill_command` + `run_skill_command` inside
  **`test_require_plan_review.py`** (not `test_hook_alignment.py`, which
  contains no such execution machinery and only checks doc/gate-wiring
  alignment). The new Step 0 fixture must be added the same way, in the
  same file, asserting the sibling file actually lands on disk with the
  correct path and content — a source-text scan of the fenced block proves
  nothing about whether the recipe behaves correctly.
- One chained integration-style test in `test_require_plan_review.py`:
  execute the Step 0 recipe → `marker.sh write plan-review` (subprocess) →
  `require-plan-review.sh`'s `ExitPlanMode` check (subprocess) → assert
  allow — catching a cross-file data-shape mismatch (e.g. trailing-newline
  or relative-path convention drift) that three independently-mocked unit
  tests could each pass while the composed path fails.
- Manual end-to-end (after the blocking precondition above is confirmed):
  enter harness plan mode in a scratch session, write a plan, run
  `/plan-review`, confirm `ExitPlanMode` is denied before the review and
  allowed after; edit the plan-mode file after a clean review and confirm
  `ExitPlanMode` denies again before a fresh review.

## Out of scope

- A session that enters plan mode a *second* time after already completing
  one plan-mode review in the same session (not the nested-repo-relative
  case this plan closes, but two sequential plan-mode reviews) — the sibling
  file is overwritten by Step 0 each time, so this should already work
  correctly, but is not enumerated as its own test case above; add if
  `/plan-review` reveals it during implementation.
- Correcting `claude/.claude/CLAUDE.md` § Plan Review's current wording is
  not needed (see Critical files) but re-verify after implementation that
  the claim is now literally true, not just directionally true.
- **Forensic recoverability of the plan-mode file's path.** `ciso-reviewer`
  noted (low severity, not gate-defeating) that the completion marker stores
  only an opaque hash with no recorded link back to the plan-mode file's
  path — if that file is later deleted or edited, a post-incident reviewer
  cannot reconstruct what was actually reviewed from the marker alone. The
  repo-relative case has the same property for an already-merged plan (the
  file itself is the record) but not for one still in flight. Not addressed
  here — the gate's job is authorization, not audit trail — but noted for a
  future plan if audit recoverability becomes a stated need.
