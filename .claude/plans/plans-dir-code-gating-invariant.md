# Document that `.claude/plans/` gates a commit, not a `/plan-it` invocation

## Context

`plan-it` needs a stated rule for what belongs in `.claude/plans/`, and the
rule needs to gate the right thing: whether the plan file gets *committed*,
not whether `/plan-it` runs at all. A first attempt at this fix shipped as
PR #715 (open, unmerged, on this same branch) and got that distinction
backwards: it added a frontmatter `DO NOT TRIGGER` clause that stops
`/plan-it` from being invoked at all for work that makes no change to the
repository — an audit, a status assessment. That discards the
specialist-reviewer pipeline (`/plan-review`'s `staff-*`/`ciso-reviewer`
dispatch, assumption-ledger rigor) for exactly the kind of work that
benefits from it as much as implementation work does. The engineer's own
correction, given directly this session: "There is MAJOR benefit to running
the full plan-it review on tasks that don't lead to implementation - for
example an assessment or an audit. The reviewer agents have TREMENDOUS
value there. The only problem that we had here was that the actual shape of
the DELIVERABLE was wrong. Even a non-durable plan file was fine... it just
didn't need to get committed if there was no code." This plan supersedes
PR #715's design in place, on the same branch: it removes the invocation-time
block and moves the deliverable-shape decision to where it actually belongs
— the commit step at the end of the pipeline.

PR #715's second piece — a Step 1 closing sentence with an unwind procedure
(`mv` the plan out of `.claude/plans/`, drop the branch/worktree, route the
narrative elsewhere) for when the mismatch is discovered late — is close to
correct in substance but wrong in framing: it reads as an exceptional
recovery path bolted onto Step 1, not the standard, expected outcome for a
whole legitimate class of `/plan-it` invocations. This plan promotes it to
that standard status, and relocates it to where the commit decision is
actually made (Step 7), so it is stated once rather than twice — the same
duplication class `/code-review`'s `comment-discipline-reviewer` already
caught once in PR #715 (the frontmatter clause restating Step 1's routing
rationale).

## Approach

Revert PR #715's frontmatter `DO NOT TRIGGER` clause outright so audits,
assessments, and other no-repository-change work run the full `/plan-it` →
`/plan-review` pipeline exactly as code-gating work does, and move the
deliverable-shape decision to a single downstream place: Step 7 branches on
whether the reviewed plan's Critical files section named any file — commit
the plan when it did, run the existing `mv`-out-and-drop-the-branch unwind
when it did not. Step 1 keeps a two-sentence informational statement of what
`.claude/plans/` is for and points forward at Step 7; Step 5 gains one
sentence stating that "None" is a legitimate Critical-files result, so the
Step 7 branch can't be dodged by inventing paths just to avoid the unwind.

**Assumption ledger**

- **Root problem:** PR #715 closed a deliverable-shape defect (a plan file
  committed under `.claude/plans/` as a PR's sole payload, with no
  accompanying repository change) with an invocation-time block, so
  `/plan-it` now declines to run at all for audits and status assessments —
  discarding `/plan-review`'s specialist dispatch and assumption-ledger
  rigor for a class of work that gains as much from them as implementation
  work does. Only the *commit* at the end of the pipeline was ever wrong.
- **Givens:**
  - Whether the invoking model actually reads and follows the Step 1 /
    Step 5 / Step 7 prose is outside this plan's reach. *Reason:* it is the
    model's own instruction-following behavior at invocation time, not an
    artifact in this or any repository that an edit could change.
  - What `git worktree remove` does when run against the worktree the
    session is currently anchored in is fixed. *Reason:* platform boundary —
    git's own behavior, which the skill can word an instruction around but
    not alter from within.
- **Mechanisms:**
  - Revert the frontmatter clause to its pre-#715 text — `anchors: root` —
    the clause is the only artifact that prevents the pipeline from running
    at all; nothing narrower than removal restores the invocation, and every
    retained character costs always-loaded description budget (see
    assumption row 6). Two narrower alternatives were checked and rejected:
    keeping the clause but narrowing its wording to only "ticket-filing
    pass" fails because a ticket-filing pass isn't distinguishable in
    advance from an audit that turns out to warrant a real change, so any
    surviving wording re-creates the exact under-trigger failure this fix
    removes; keeping the clause and adding a caveat that it doesn't apply
    when specialist review is wanted fails because the model choosing
    whether to invoke `/plan-it` is the same model that would have to judge
    its own need for review before ever seeing one, which is circular.
  - Step 1 lead paragraph reframed as an informational forward-pointer;
    Step 1's closing unwind sentence deleted — `anchors: root`,
    `anchors: row1` — states the invariant once, at the point a reader is
    deciding whether to scaffold a plan file, and resolves that decision as
    "scaffold it anyway." Deleting the Step 1 unwind sentence keeps the
    procedure stated exactly once, at its new canonical home in Step 7 (see
    row 3).
  - Step 5's Critical-files instruction gains one sentence licensing
    "None" — `anchors: row1` — with the commit decision keyed to that
    section, an author facing the unwind has a cheap dodge available
    (listing plausible paths that don't actually need touching); the
    licensing sentence lands at the exact point of authorship where that
    pressure would apply.
  - Step 7 splits into a commit branch and an unwind branch —
    `anchors: row2` — this is the engineer-confirmed check point; the
    unwind text is the one already shipped in PR #715, re-homed and promoted
    from caveat to one of two normal outcomes rather than an exception.
  - New `plan-it/evals/trigger-cases.json` — `anchors: row7` —
    `skill-review`'s own rule requires an eval run after a `DO NOT TRIGGER`
    change, and currently no-ops for want of a fixture. Two lighter
    primitives were checked and rejected: the existing pytest description
    contracts assert only that a `DO NOT TRIGGER when:` block exists and
    that the description fits the length cap — they can't tell whether an
    audit-shaped query actually matches (row 6); a one-shot manual trace
    (Verification step 3 below) is a single judgment call this session
    makes once and that no later description edit re-runs. The fixture is
    auto-discovered and statically validated with no registration step
    (row 7), so it adds no wiring cost beyond the file itself.
- **Assumption rows:**
  - The specialist-reviewer pipeline has major value on work that never
    reaches implementation, and the only defect in the observed case was the
    shape of the deliverable — a non-durable plan file is fine, it just
    shouldn't be committed when there's no code —
    `[engineer-verified]`.
  - The "did this need a real repository change?" check fires **after
    `/plan-review` completes, inside Step 7** — not immediately after Step 5
    drafts the plan — so Step 6 runs unconditionally and only the commit is
    conditional — `[engineer-verified]`.
  - PR #715's own `/code-review` caught the frontmatter clause restating
    Step 1's routing rationale in always-loaded description text, and fixed
    it by trimming the frontmatter — `[verified: git show 7cf49209 commit
    message, this session]`. This redesign inherits that constraint: the
    unwind procedure is stated once, at Step 7.
  - Nothing else in the repository states this invariant, the frontmatter
    clause, or the unwind procedure — `[verified: git grep this session]`
    over `no repository change` / `status assessment` / `ticket-filing` /
    `Critical Files` / `claude/plans` across tracked files. The two nearest
    sites state something different and stay correct unchanged:
    `branch-management/SKILL.md:118-125` ("plan files go on the
    implementation branch") governs *where* an existing plan file goes, and
    its antecedent dissolves cleanly when the unwind drops the branch;
    `brief/SKILL.md:27` ("Committed to the repo.") contrasts durability
    surfaces for the ordinary case and needs no qualification.
  - `plan-review` has no checklist item requiring a non-empty Critical-files
    section, and its B17 ("Plan and implementation in sync") flags only when
    "implementation is underway and the plan is in a separate branch" — so
    an audit plan reviewed at Step 6 with no implementation never trips it
    — `[verified: read of claude/.claude/skills/plan-review/SKILL.md:144-150
    this session]`.
  - The skill description contract tests require a `DO NOT TRIGGER when:`
    block to exist but pin no specific clause text for `plan-it`, and the
    per-skill harness cap is 1,536 chars — the revert only shortens the
    description, so both hold — `[verified: read of
    claude/.claude/skills/tests/test_skills.py:275-289 this session]`.
  - `skill-review/SKILL.md` requires `python evals/run_skill_evals.py
    --skill <name>` after a TRIGGER-block change; four fixtures exist today
    (`code-review`, `subagent-delegation`, `test-conventions`,
    `test-evaluation`), `plan-it` has none, and
    `test_trigger_cases_files_well_formed`
    (`claude/.claude/skills/tests/test_skills.py:1357-1397`) discovers and
    statically validates any new `*-cases.json` with no registration step —
    `[verified: ls + read this session]`. This corrects the prior (now
    superseded) plan's row asserting no skill in this repo has a fixture
    yet.
  - `require-plan-review.sh` gates only `Write`/`Edit`/`MultiEdit`/
    `ExitPlanMode`, arms only on an uncommitted-or-modified plan file, and
    already names `mv it out of .claude/plans/` as the sanctioned remedy at
    line 156 — so the Step 7 unwind runs through Bash, ungated, and the gate
    disarms the moment the file leaves the directory —
    `[verified: read of claude/.claude/hooks/require-plan-review.sh:130-164
    this session]`. The hook needs no change.
  - The confirmed bad instance (a plan file as a PR's sole payload, passing
    all four review stations) is real — `[unverified]`, carried from the
    superseded plan; the originating PR is in a private repository not
    opened this session. The redesign depends only on the general fact
    pattern, not this instance's specifics.
  - Whether `git worktree remove` succeeds against the worktree the session
    is anchored in is untested here — `[unverified]`. The "remove the
    branch and worktree created for it" wording ships unchanged from #715;
    this design promotes how often that path runs, not what it says.

**Wording carried over from the superseded plan:** "a change to the
repository," not "code" — a legitimate plan's accompanying diff can be
non-code (docs, config, infrastructure), and "code" would false-flag it.

**Alternatives considered and set aside** (mechanism-level alternatives are
inline above; these are approach-level):

- **Firing the empty-Critical-files check right after Step 5, before
  `/plan-review` runs.** Set aside per the engineer-verified row 2 above —
  it would skip the review for exactly the work that benefits from it most.
- **Keeping the Step 1 unwind sentence and adding a separate Step 7
  branch.** Set aside as the duplication class row 3 already caught once in
  PR #715 — restating the same procedure in both places drifts the moment
  either copy is edited alone.
- **A separate Step 7.5 for the unwind**, rather than a branch inside
  Step 7. Set aside: it's one branch of a single commit decision, not a
  separate phase; a distinct step would have to restate the triggering
  condition.
- **Mechanically parsing the Critical-files section inside
  `require-plan-review.sh`, or adding a new `plan-review` tripwire.**
  Carried over from the superseded plan and still rejected: prose-parsing
  in a fail-closed bash hook breaks on a reworded heading, and a new review
  station compounds enforcement on what is a routing decision, not a missed
  check.
- **A `docs/design-decisions.md` entry for this choice.** Set aside — the
  operative text lives where the decision is made (Step 7), and a docs
  mirror would duplicate it under this repo's own single-source-of-truth
  rule.

## Critical files

- `claude/.claude/skills/plan-it/SKILL.md`

  1. **Frontmatter `description` (lines 3–10):** revert to the byte-identical
     pre-PR-#715 text —

     ```yaml
     description: >
       Produces an implementation plan and hands off to /plan-review.
       TRIGGER when: asked for a plan or implementation strategy
       for work spanning multiple files or domains. DO NOT TRIGGER when:
       single-file tweaks, "just implement it" requests, or when a plan
       already exists (use /plan-review instead).
     ```

  2. **Step 1 lead paragraph (line 19):** replace with an informational
     forward-pointer —

     > `.claude/plans/` holds plans that gate a change to this repository —
     > the plan file is provenance for that change, not the deliverable
     > itself. Work that turns out to make no repository change (an audit,
     > a status assessment) still runs this skill end to end, `/plan-review`
     > included; scaffold the plan file here as normal, and Step 7 decides
     > whether it gets committed.

  3. **Step 1 closing unwind sentence (line 29):** delete, along with its
     surrounding blank line. Line 27 ("If `.claude/plans/<topic-slug>.md`
     already exists…") becomes the last line of Step 1.

  4. **Step 5's section list, item 3 "Critical files" (line 73):** append —

     > When the work changes no repository file — an audit, a status
     > assessment — write `None` plus what the deliverable is instead;
     > that's a real result Step 7 acts on, not a gap to fill with
     > speculative paths.

  5. **Step 7 heading and opening (lines 87–89):** replace —

     ```markdown
     ## Step 7 — Commit or unwind the plan, then choose where implementation runs

     **If the Critical files section names at least one file:** commit the
     reviewed plan to the implementation branch before implementation
     begins — it makes an approved plan durable before a phase that
     rewrites the working tree, and `handoff` §5 already requires it.

     **If it names none:** the plan is the deliverable rather than
     provenance for a change, so it isn't committed — `mv` the plan file
     out of `.claude/plans/`, remove the branch and worktree created for
     it, and route the narrative through the project's own tracker or
     documentation tool (see that project's `CLAUDE.md`, or ask the
     engineer if undocumented). The review it just passed still counts;
     what ships is the findings, through that channel. Stop here — the
     choice below is about where implementation runs, and there is none.
     ```

     Line 91 onward ("Then choose the session…") is unchanged.
     "Critical files" is capitalized to match Step 5's actual heading, so
     the cross-reference is literal.

- `claude/.claude/skills/plan-it/evals/trigger-cases.json` (new)

  Mirror `claude/.claude/skills/test-conventions/evals/trigger-cases.json`'s
  shape: `{"skill_name": "plan-it", "method": "description-fidelity",
  "cases": [...]}`, each case `{"id", "query", "should_trigger"}`. Cases:

  - `audit-multi-service` — an audit-shaped request spanning several files
    → `should_trigger: true` (the case PR #715 broke).
  - `status-assessment` — a request to plan how to assess where a migration
    stands across several services → `true`.
  - `multi-file-feature` — the ordinary code-gating case → `true`.
  - `single-file-tweak` → `false`.
  - `just-implement-it` → `false`.
  - `plan-already-exists` — names an existing `.claude/plans/<slug>.md` →
    `false`.

  **Reuse:** `require-plan-review.sh:156`'s existing "mv it out of
  .claude/plans/" phrasing (already reused by PR #715, retained verbatim so
  the hook's deny message and the skill's guidance stay identical);
  `test-conventions`' fixture as the structural template;
  `test_trigger_cases_files_well_formed` for static validation with no
  registration step.

## Verification

1. `../../../.venv/bin/pytest claude/.claude/skills/tests/ claude/.claude/hooks/tests/test_require_plan_review.py`
   from the worktree — frontmatter still parses strictly, the `TRIGGER` /
   `DO NOT TRIGGER` structural contracts hold, the 1,536-char per-skill cap
   and the aggregate listing budget both pass (the revert only shortens),
   and the new `trigger-cases.json` passes `test_trigger_cases_files_well_formed`
   with no model call.
2. `python evals/run_skill_evals.py --skill plan-it` — required by
   `skill-review/SKILL.md` for a `DO NOT TRIGGER` change, and now actually
   runnable. Confirm the audit and status-assessment cases come back
   `should_trigger: true`.
3. Trace the reverted frontmatter against both known instances by hand: a
   legitimate audit request must read as clearly *triggering*; the
   confirmed-bad instance must **also** trigger, with the correction landing
   at Step 7's unwind rather than at invocation — that inversion versus
   PR #715 is the point of this change, not a regression.
4. Read the edited skill end-to-end on both terminal paths: (a) Critical
   files non-empty → Step 1 scaffolds, Step 6 reviews, Step 7 commits,
   session choice runs; (b) Critical files `None` → Step 1 still scaffolds,
   Step 5 licenses `None`, Step 6 reviews unchanged, Step 7 unwinds and
   stops. Re-check path (a) through all three Step 1 branches (plan mode
   active / harness-path write fails / otherwise), since the lead paragraph
   now precedes all three.
5. `git diff 7cf49209^ -- claude/.claude/skills/plan-it/SKILL.md` — the
   frontmatter hunk must come back empty, confirming the revert is
   byte-exact rather than a reworded approximation.
6. `git grep -n -e "no repository change" -e "unwind" -e "Critical files" -- claude/ docs/ CLAUDE.md`
   after the edit — confirm the unwind procedure appears exactly once
   (Step 7) and no second site restates the commit condition.
7. This is a `SKILL.md` change: `/skill-review`'s hook-enforced marker gate
   on `git commit` applies. Confirm the diff reads as restoring an
   invocation surface plus relocating one procedure, not as adding a new
   capability.

## Out of scope

- **A plan already committed on this branch in an earlier session, then
  descoped to `None` Critical files.** Deliberately not given explicit
  handling: the Step 7 unwind's "remove the branch and worktree created for
  it" already covers it, because a `plan-it` branch is unmerged by
  construction (planning precedes implementation) — dropping the branch
  takes the earlier plan commit with it and nothing needs a revert. Once
  such a branch has merged, the plan is committed history under this
  repo's preserved-record axis and isn't rewritten regardless. Adding a
  clause here would restate a consequence the instruction already entails.
- **Changing `require-plan-review.sh`.** In reach but needs nothing: it
  arms only on uncommitted-or-modified plan content, gates no Bash, and
  already names the `mv` remedy this design reuses.
- **Hardening or verifying the "remove the branch and worktree" step
  against the anchored-worktree case.** Wording carried unchanged from
  PR #715; worktree lifecycle belongs to `branch-management`, and verifying
  git's behavior there is a separate, unscoped effort.
- **Qualifying `brief/SKILL.md:27`'s "Committed to the repo."** It
  contrasts durability surfaces for the ordinary case; qualifying it would
  put a second statement of the commit condition in a second file — the
  exact duplication this design exists to avoid.
- **Adding eval fixtures for skills other than `plan-it`.** Only this
  skill's TRIGGER block is changing.
- **Auditing other repositories' `.claude/plans/` directories for further
  instances of the deliverable-shape mistake.** Carried from the superseded
  plan; not required to land this fix.
- **Reconciling or modifying the project-specific `plan-it-*` layer**
  checked during the original investigation — confirmed to address an
  unrelated concern (design-doc grounding, UI-touch scope), carried from
  the superseded plan; no action needed.
