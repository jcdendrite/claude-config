# Fix select-tests.py drift propagating through handoffs

**Status: abandoned.** The systemic fix drafted below (Approach, Critical
files) was rejected by `/plan-review` for a foundation-level defect — see
`docs/design-decisions.md` §49 for the decision and rationale. No code from
Critical files was implemented. This file is kept, uncommitted content
finalized to reflect that outcome, as the durable evidence record §49
cites rather than restates. Critical files below is superseded by the
"Critical files (final)" note at the end of that section.

## Context

Stop the full pytest suite from being run instead of `select-tests.py` on
the claude-config repo by closing the mechanism that lets that instruction
propagate silently across sessions. Investigating machine contention from
many parallel claude-config sessions, the engineer found a session that had
inherited a prior session's handoff instruction to run the full suite by
hand, rather than checking that instruction against CLAUDE.md's own
`select-tests.py` rule. A transcript-corpus audit of the personal account's
684 claude-config transcripts (2026-08-04 to 2026-09-04, the only account
root with claude-config project directories) confirmed this is a recurring
pattern, not a one-off: of 29 sessions whose Bash history matched a
full-suite-shaped pytest invocation after the rule shipped
(2026-08-25T07:49:24Z), 15 genuinely ran the bare full-suite command (the
other 14 were decoys, `--collect-only` checks, or subdirectory-scoped
runs), and 11 of those 15 (73%) inherited the command from a handoff whose
own text cited "the handoff" as authority. All 11 trace to two long-lived,
still-active branches — `prevent-runaway-subagent-cost` and
`review-pipeline-orchestrator-subagent` — each with a plan file whose
Verification section named the raw full-suite command. Both plan files
were written before `select-tests.py` existed (2026-08-21 and 2026-08-23,
predating the rule by 2-4 days) and were never updated after the rule
shipped; each branch's successive handoffs carried the stale command
forward unchanged. Neither `handoff/SKILL.md`'s §3 clause nor
`check-handoff.py` validates a handoff's named verification command against
CLAUDE.md's canonical rule before writing it forward. The intended outcome:
close that validation gap so this class of drift cannot silently recur on
any branch, and correct the two branches already carrying a stale command.

## Approach

Close the drift at the one point where a stale command is *copied forward* rather than where it is *run*: `handoff` §3. Two changes ship together — a `handoff/SKILL.md` §3 clause that requires the verification command be re-derived from the project's current documentation (and the stale plan flagged in §6 when the two disagree), and a new soft check in `check-handoff.py` that fires when §3 names a plan path but cites no command source outside that plan. The two stale plan files are **not** in this diff: neither exists on this branch's tree, so no edit here can reach them; they ship as a fully-specified per-branch follow-up in Out of scope.

The key design fact is one the engineer's own mechanism rationale already conceded: **no mechanical check can tell a stale full-suite command from CLAUDE.md's two legitimate full-suite exceptions**, because those exceptions are stated as conditions on intent ("genuinely calls for a whole-repo claim"), not as a machine-readable predicate. So the check must not try to judge the command. It judges *provenance* instead — whether the author cited a source for the command that is something other than the plan file being implemented. That is mechanizable, project-agnostic, and it is exactly the step the 11 drifted sessions skipped.

Deliberately rejected: keying the check on a list of test-runner tokens (`pytest`, `npm test`, `go test`, …). `check-handoff.py` ships to every stow consumer, so a runner list is both a maintenance surface that will miss runners and a stack-specific token set in a global script. Keying on `.claude/plans/<name>.md` instead needs no list at all, is `plan-it`'s own cross-stack convention, and catches a stale lint or build command as readily as a test one.

**Root problem:** a verification command named in a handoff §3 is copied from its plan file without being re-checked against the project's current documented command, so a plan predating a command change propagates the stale command through every successive handoff on that branch.

**Givens** (fixed, beyond this design's reach):
- **G1** — Plan files are committed to their own implementation branch and are invisible from another branch's tree. `plan-it` Step 7 and `branch-management` own that convention.
- **G2** — Handoff files are consumed on resume and do not persist; any durable record must land in the plan file or in §6, never in the handoff. `resume-context` owns that lifecycle.
- **G3** — CLAUDE.md's two full-suite exceptions are prose conditions about intent, not a predicate; no check at any interception point can discriminate them. Dissolving this needs a decision about CLAUDE.md's own grammar, outside this plan. `[engineer-verified]` — stated in the Step 4 mechanism rationale.
- **G4** — `check-handoff.py` installs to every stow consumer, so any check it adds must hold for every stack. The stow package's own scope.
- **G5** — The two stale plan files sit on separate still-active branches whose worktrees may be lock-held by live sessions. Another session owns them.

**Mechanisms:**
- **M1 — `check_section3_plan_command_provenance` soft check** (`anchors: root`). Lighter primitives weighed and *kept alongside* rather than substituted: a `RESIDUAL_CHECKLIST_ITEMS` entry alone fails because that block is unconditional boilerplate printed on every run, and the drifted sessions read past exactly this prose class; a SKILL.md §3 clause alone fails because the §3 clause *is* the surface that drifted. Both ship as M2 — the WARN is the increment over them, quoting the specific plan path back at the author. Heavier alternatives rejected: a PreToolUse Bash hook (engineer-rejected, and G3 means it cannot discriminate either), and a project-config file enumerating allowed commands (a new file format and a new drift source).
- **M2 — `handoff/SKILL.md` §3 clause, checklist-bullet extension, warn-list sentence** (`anchors: root`). The §3 clause's current "its verification command" has an antecedent pointing at the plan; that ambiguity is the authoring-time half of the defect.
- **M3 — Scope split: systemic fix on this branch, plan corrections as a specified per-branch follow-up** (`anchors: row G1, row G5`).

**Assumption ledger:**

1. Neither stale plan file exists on this branch's tree. `[verified: Glob of `.claude/plans/{prevent-runaway-subagent-cost,review-pipeline-orchestrator-subagent,select-tests-fallback-audit}.md` in this worktree returned only `select-tests-fallback-audit.md`]`
2. A write into another linked worktree is either denied by the collision guard or silently re-acquires that worktree's lock from the session holding it. `[verified: require-worktree-for-file-writes.sh lines 155-166 — `_lib_worktree_collision_guard` denies, and the `WAS_UNLOCKED` branch emits `_lib_emit_allow_with_context` announcing lock re-acquisition]`
3. Editing `handoff/SKILL.md` makes `/skill-review` hook-enforced on commit. `[verified: .claude/rules/review-pipeline-dispatch.md]`
4. `select-tests.py` maps every path this plan touches, with no unmatched-path widening. `[verified: select-tests.py DOMAIN_RULES lines 295-296, CROSS_DOMAIN_EXCEPTIONS lines 371 and 386; `.claude/plans/` maps to `()` at line 300]`
5. Extending SKILL.md's existing `code-writer` checklist bullet does not disturb the residual-items drift guard — that guard pins five keywords, none matching this bullet. `[verified: test_check_handoff.py `RESIDUAL_ITEM_KEYWORDS`]`
6. `check_section3_anchor_shapes` strips code spans, so a backticked plan path or command is invisible to it; the new check must scan the **raw** §3 body. `[verified: check-handoff.py line 192]`
7. The engineer's "Both" fixes the required *outcome*, not a same-diff requirement. `[engineer-verified]` — mechanism resolved here per `plan-it`'s question-the-prescribed-approach step.
8. The two plans' stale Verification text is as the Step 3 exploration reported. `[verified: Step 3 findings; files not reopened]`
9. Whether either stale plan's Verification legitimately needs a whole-repo claim under CLAUDE.md's second exception. `[unverified]` — the follow-up must check before substituting, and a blanket replace would be the same unexamined-copy behavior this plan exists to stop.
10. `.claude/plans/` is a cross-stack convention of this stow package, not a project-specific token. `[verified: plan-it/SKILL.md lines 17, 23]`

**Dispatch:** one `code-writer` dispatch for all four files. The script check, its tests, and the SKILL.md prose describing that same warn are one design; splitting would require restating it in every prompt, which `plan-it` Step 5 bars.

## Critical files

**`claude/.claude/scripts/check-handoff.py`** — add one module-level regex pair and one check function; register it in `soft_checks` alongside the existing two.

- `_PLAN_PATH_RE = re.compile(r"\.claude/plans/[^\s`)]+\.md")` — `plan-it`'s own path convention, not a stack token.
- `_VERIFIED_TAG_RE = re.compile(r"\[verified:([^\]]*)\]")`.
- `check_section3_plan_command_provenance(draft_text) -> list[str]`: pull §3 via the existing `extract_h2_sections` (do not re-parse). Scan the **raw** body, not `strip_code_spans`'d — a plan path and a command both arrive backticked, so the stripping the sibling anchor-shape check relies on would blind this one. Return no warning when §3 names no plan path. Otherwise suppress when any `[verified: X]` evidence string contains neither `plan` nor `handoff` (case-insensitive) — that is a citation to a source outside the plan being implemented. Warn text, matching the script's existing `--` style: `f"§3 names a plan path ({path!r}) but cites no verification-command source outside it -- re-derive the command from the project's current documentation and tag it [verified: <that doc>]"`.
- Known false non-suppression worth a one-line comment: an evidence string legitimately naming a doc with `handoff` in its filename (e.g. `docs/handoff-nudge.md`) does not suppress. Document it; do not add a second guard for it.
- Do **not** add a `RESIDUAL_CHECKLIST_ITEMS` entry — this item is mechanized, so it belongs in `soft_checks`, and adding one would force a paired edit to the drift guard's keyword tuple.

**`claude/.claude/scripts/tests/test_check_handoff.py`** — new `TestCheckSection3PlanCommandProvenance` class, unit-level, mirroring `TestCheckSection3AnchorShapes`'s shape (`_doc(overrides={3: ...})`, assert on the returned list). Cover: no plan path → no warning; plan path with a full-suite command and no verified tag → warns; the same draft with `[verified: CLAUDE.md Commands block]` → suppressed; `[verified: the plan file's Verification section]` → still warns; a plan path inside a backtick span still fires (the raw-scan inversion — this is the assertion that pins point 6 above). The incident-reproduction case (plan path plus a bare full-suite pytest command, untagged) is the class's centerpiece.

`TestCli._clean_draft`'s §3 is `"Run the test suite. [assumed]"` — no plan path, so the new check cannot fire there and `test_clean_fixture_passes_every_hard_check_and_exits_0` needs no change. Leave that fixture alone.

**`claude/.claude/skills/handoff/SKILL.md`** — three edits.

1. §3 (line 103), replacing the existing clause:

   > When the next step implements an approved plan, write it as the dispatch, not the work: name `code-writer`, the plan path, the phase, and the verification command, per `subagent-delegation`'s default. Re-derive that command from the project's own current documentation (its `CLAUDE.md` or README), not from the plan file or a prior handoff — a plan written before the project's test command changed carries the old one forward, and each handoff copies it again. When the plan's command and the project's documentation disagree, name the documented one here and record the plan as stale in §6. 'Implement Phase 2' reads to the resuming session as work to do inline.

2. Pre-write checklist, extending the existing `code-writer` bullet rather than adding a new one:

   > - If §3's next step implements an approved plan, it names `code-writer` as the dispatch rather than describing the work to do inline, and its verification command was re-derived this session from the project's current documentation rather than copied from the plan

3. The warn-list sentence (line 162), gaining a third clause: `…, and a §3 naming a plan path with no [verified: …] source outside that plan.`

**`claude/.claude/skills/tests/test_skills.py`** — one test in the class holding `test_handoff_prewrite_checklist_crosschecks_section3`, matching its exact-substring convention, pinning the §3 clause's re-derivation instruction and its §6 stale-plan routing. This is the clause whose drift caused the incident; nothing else stops it being reworded back.

**`.claude/plans/select-tests-handoff-drift.md`** — this plan, committed per `plan-it` Step 7 (Critical files names at least one file).

**Reuse:** `extract_h2_sections` for §3 extraction. `soft_checks` registration follows the existing `(label, result)` tuple form in `main`. No new CLI flags, no new file formats.

**Critical files (final): None.** `/plan-review` rejected the design for a foundation-level defect (see Status note, top of file). Separately, the engineer concluded no enforcement mechanism should be built at all, since the root cause traces to two aging pre-`select-tests.py` plans rather than a live systemic gap. This plan's deliverable is the root-cause investigation and assumption ledger above, per `plan-it`'s own "work that changes no repository file" convention. The two stale plan files this investigation traces the drift to were corrected by their own branches' sessions directly, not from here (see `docs/design-decisions.md` §49).

## Verification

From this worktree:

- `../../../.venv/bin/python3 claude/.claude/scripts/select-tests.py` — the project's documented test command, worktree-relative per README's Tests section. This diff touches `claude/.claude/scripts/` (→ scripts tests), `claude/.claude/skills/handoff/SKILL.md` (→ skills tests, plus scripts and hooks tests via its cross-domain exception), and `.claude/plans/` (no targets), so the selection is scoped, not widened. Running the full suite by hand here would be the exact behavior this plan exists to stop.
- `../../../.venv/bin/ruff check claude/.claude/` — Python changed.
- `/skill-review` — hook-enforced by `require-skill-review.sh` because `handoff/SKILL.md` is staged; `git commit` is blocked until its marker is written.
- `/code-review` before commit, per CLAUDE.md.

ShellCheck is not needed — no shell file changes.

The behavioral proof (a draft reproducing the incident shape warns; the same draft with a doc-sourced `[verified: …]` tag does not) lives in the new test class, so the `select-tests.py` run covers it. No manual script invocation step.

## Out of scope

**The two stale plan files — a follow-up on each branch's own worktree, not this diff.** Neither exists on this branch's tree (ledger row 1), so no edit here can reach them; the correction has to be a commit on each branch. Reaching across into their worktrees from this session is also wrong mechanically: the write either trips `_lib_worktree_collision_guard` and is denied, or it silently re-acquires that worktree's lock from a live session's in-flight work (ledger row 2). Raise this to the PR reviewer per CLAUDE.md Axis 1 bucket 3, and execute it per-branch when next anchored in each worktree:

- `.claude/plans/prevent-runaway-subagent-cost.md` (on branch `prevent-runaway-subagent-cost`) — Verification section: `../../../.venv/bin/pytest claude/.claude/` → `../../../.venv/bin/python3 claude/.claude/scripts/select-tests.py`, keeping the `ruff check` line unchanged.
- `.claude/plans/review-pipeline-orchestrator-subagent.md` (on branch `review-pipeline-orchestrator-subagent`) — Verification step 2: the same substitution.

Check each plan's Verification against CLAUDE.md's second named exception before substituting — if that plan genuinely needs a whole-repo claim, the current command is correct and only the *rationale* is missing (ledger row 9). Substituting blind would repeat the unexamined-copy behavior this plan exists to stop.

**`pr-description/SKILL.md`'s Test Plan wording** — flagged in GH-765's audit as still not distinguishing a scoped result from a whole-repo claim, left unfixed. It governs PR-body prose, not the command a resuming session runs, so it is not on this propagation vector. Real residual gap; separate change.

**Re-validating an existing plan's Verification section during `/plan-review`** — would not have caught either flagged branch, because `plan-review` runs at authoring/revision time and neither dormant plan was ever revised after the rule shipped. Adding it buys nothing against this vector.

**The five surfaces GH-765 already fixed** (`plan-it/SKILL.md`, `subagent-delegation/SKILL.md`, `ready-for-review/SKILL.md`, and the two `settings.json` permission rules) — verified correct at line 108 of `plan-it/SKILL.md`; do not re-touch.

**A drift guard pinning SKILL.md's warn-list sentence to the script's `soft_checks` labels** — a second sync mechanism layered on the one that already exists for the residual list. The duplication is three short clauses in one sentence; a guard for it is the compounding-defensive-layer shape CLAUDE.md warns about.

**A PreToolUse Bash hook inspecting live pytest invocations** — engineer-rejected in Step 4, and G3 means it could not discriminate the legitimate exceptions any better than this check can.
