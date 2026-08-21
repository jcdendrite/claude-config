# Document that `.claude/plans/` gates a repository change, not a process narrative

## Context

`plan-it` currently has no stated rule for what belongs in `.claude/plans/` —
the closest thing is a passing clause in `branch-management` ("commit the
plan to this feature branch" if one exists), which describes *where* a plan
file goes once one exists, not *whether* one should. This has let a plan file
become a PR's entire payload for work that made no change to the repository
at all — a process narrative (an audit, a status assessment) committed and
reviewed as if it were provenance for an implementation, when the plan itself
was the only thing that shipped. This is a confirmed, recurring failure mode
in at least one downstream project built on this tooling, not a hypothetical.

Investigation into that failure narrowed the fix to a documentation gap, not
an enforcement gap: the confirmed bad instance passed all four review
stations this repo already runs before a PR merges (`plan-review`,
`code-review`, `ready-for-review`, human approval) — none of them exists to
check this, because nothing states the rule for them to check against. That
argues against bolting on a fifth review checkpoint, and for stating the
invariant once, at the earliest point it can act: before `plan-it` Step 1
ever creates a branch, worktree, or plan file.

A separate open question — whether an existing project-specific `plan-it-*`
layer (a precedent for another project on this tooling) already solved this
same invariant, which would argue for giving the affected project its own
layer instead of a change here — is resolved: that layer addresses two
unrelated, genuinely project-specific concerns (grounding designs against
in-repo design docs, and flagging UI/copy files touched by an
infrastructure-labeled ticket). It has no overlap with the "a plan is
provenance, not the deliverable" invariant, so it's neither evidence for nor
against fixing that invariant generically here.

## Approach

State the invariant in `plan-it/SKILL.md` itself, in two places: the
frontmatter `description`'s `DO NOT TRIGGER when:` list (so the model doesn't
invoke `/plan-it` at all for work that's clearly a non-repository-change
pass), and a new lead sentence in Step 1 (so the rule is stated positively
for the cases the frontmatter heuristic doesn't catch — an ambiguous goal
restatement, or deliberate invocation despite the mismatch) with an unwind
procedure for when the mismatch surfaces only later, at Step 5.

**Assumption ledger**

- **Root problem:** `.claude/plans/` has no documented purpose beyond
  `branch-management`'s passing "plan files go on the implementation branch"
  clause, so nothing stops a plan file from being a PR's sole payload for
  work that changes nothing else in the repository.
- **Givens:**
  - Whether the invoking model actually reads and follows the frontmatter
    clause and the Step 1 sentence reliably is outside this plan's reach —
    a skill file can state a rule correctly but can't force compliance with
    it. *Reason:* this is the model's own instruction-following behavior at
    invocation time, not an artifact in this or any other repository the
    plan could edit to change it.
- **Mechanisms:**
  - Frontmatter `DO NOT TRIGGER` clause — `anchors: root` — intercepts before
    Step 1 runs at all, the earliest possible point; catches the clear case
    (goal is legibly "audit" / "status assessment" / "ticket-filing pass")
    before a branch or worktree exists to unwind.
  - Step 1 lead sentence + closing unwind procedure — `anchors: root` —
    states the rule positively ("provenance for a change," not "no code
    diff") so it's judged by whether Step 5's Critical Files section ends up
    empty, not by a brittle keyword match; gives an actionable recovery for
    when the mismatch is discovered after a branch/plan file already exist.
- **Assumption rows:**
  - The confirmed bad instance (a plan file as a PR's sole payload, passing
    all four review stations) is a real, recurring failure —
    `[unverified]`. Sourced from a prior investigation's handoff artifact,
    not independently re-verified in this session (the originating PR lives
    in a different, private repository this session did not open). The fix
    doesn't depend on the exact instance's specifics — only on the general
    fact pattern, which is independently plausible and is what the frontmatter
    clause and Step 1 sentence are written against.
  - The project-specific `plan-it-*` layer checked during this investigation
    addresses two concerns unrelated to this invariant (design-doc grounding,
    UI-touch scope check) — `[verified: direct read this session]`, read
    under this session's explicit in-session authorization.
  - Nothing in this repository states the `.claude/plans/` code-gating
    invariant today, beyond `branch-management`'s passing "plan files go on
    the implementation branch" clause — `[verified: git grep this session]`
    across tracked `.md`/`.sh` files.
  - `require-plan-review.sh`'s existing deny-message already contains the
    exact wording "mv it out of .claude/plans/" as a named remedy for a plan
    file that doesn't belong — `[verified: read this session]`
    (`require-plan-review.sh:156`); reused verbatim in the new unwind
    procedure so the hook's error message and the skill's proactive guidance
    say the same thing instead of drifting.

**Wording refinement over the originating investigation's phrasing:** the
investigation's own framing said "gate a repository *code* diff." The actual
evidence includes a legitimate precedent where the accompanying change was a
docs file, not application code — so this plan uses "a change to the
repository," not "code," to avoid the frontmatter clause false-flagging a
real plan whose accompanying diff happens to be non-code (docs, config,
infrastructure).

**Alternatives considered:**
- **A new `plan-review` tripwire checking for an empty Critical Files
  section.** Rejected: it's a fifth review station layered on top of four
  that already ran and missed this — compounding enforcement on a problem
  that's actually a missing definition, not a missing check.
- **Parsing the Critical Files section's presence inside
  `require-plan-review.sh` mechanically.** Rejected: prose-parsing in a bash
  hook that fails closed is fragile — a plan author who phrases the section
  differently (or omits the heading) breaks the parse, and the hook's
  documented behavior on an unreadable/malformed input is to block, not pass.
- **A `docs/design-decisions.md` entry recording this choice.** Considered
  and skipped: the operative text has to live where enforcement happens
  (`plan-it` Step 1), and a docs/ mirror of the same sentence would duplicate
  it under this repo's own single-source-of-truth rule the moment either
  copy is edited without the other.
- **A project-specific `plan-it-<project>` layer for the affected project**
  (mirroring the precedent layer's shape). Rejected: the precedent layer
  addresses unrelated concerns (see Context), so it isn't evidence for this
  invariant needing project-specific treatment — and a generic invariant
  about what `.claude/plans/` is *for* is exactly the kind of rule that
  belongs in the base skill every project shares, not duplicated per project.

## Critical files

- `claude/.claude/skills/plan-it/SKILL.md`
  - Frontmatter `description`'s `DO NOT TRIGGER when:` list — add a clause
    for work that makes no change to the repository (an audit, a status
    assessment, a documentation or ticket-filing pass), routing it to the
    project's own tracker or doc tool instead.
  - Step 1 — add a lead sentence (before the plan-mode / write-fails /
    otherwise branches) stating the invariant positively, and a closing
    sentence (after the existing "if the plan file already exists" line)
    giving the unwind procedure for when the mismatch surfaces later.
  - **Reuse:** `require-plan-review.sh:156`'s existing "mv it out of
    .claude/plans/" wording for the unwind procedure, rather than inventing
    new phrasing for the same remedy.

## Verification

1. `../../../.venv/bin/pytest claude/.claude/skills/tests/ claude/.claude/hooks/tests/test_require_plan_review.py` from the worktree — confirms the frontmatter still parses strictly, the `TRIGGER`/`DO NOT TRIGGER` structural tests still pass, and the per-skill description-length cap (1,536 chars) isn't exceeded by the added clause.
2. Re-read the edited Step 1 end-to-end for all three branches (plan-mode active / harness-path write fails / otherwise) to confirm the new lead sentence and closing unwind sentence read correctly regardless of which branch actually creates the plan file — the lead states the rule before any branch runs; the close is reachable from all three.
3. Trace the new frontmatter clause against both known precedents: the confirmed bad instance (a plan file as sole payload) should read as clearly `DO NOT TRIGGER`; a legitimate plan with a thin-but-real accompanying repository change should not, since the clause's wording is "no repository change," not "a small change."
4. This is a `SKILL.md` change — `/skill-review`'s hook-enforced marker gate on `git commit` applies; run it and confirm the added clauses read as a scope clarification of the existing skill, not a new capability.
5. `skill-review`'s own guidance calls for `evals/run_skill_evals.py --skill plan-it` after a `DO NOT TRIGGER` change. `plan-it` has no `*-cases.json` fixture under `evals/fixtures/` today (no skill in this repo does yet), so the harness has nothing to run against this change — noted here rather than silently skipped. Building that fixture is a separate, unscoped effort, not implied by this fix.

## Out of scope

- Reconciling or modifying the project-specific `plan-it-*` layer checked
  during this investigation — confirmed to address an unrelated concern (see
  Context); no action needed.
- The companion edit naming the affected project's actual tracker/doc tools
  in its own `CLAUDE.md`. That edit belongs in that project's own repository
  under its own redaction posture — not part of this repository's diff.
- Adding a new `plan-review` tripwire, or mechanically parsing the Critical
  Files section inside `require-plan-review.sh` — both evaluated and
  rejected (see "Alternatives considered").
- Auditing other repositories' `.claude/plans/` directories for further
  instances of the same mistake, or locating a previously-mentioned earlier
  instance that remains unlocated — not required to land this fix.
- A `docs/design-decisions.md` entry for this choice — considered and
  skipped (see "Alternatives considered").
