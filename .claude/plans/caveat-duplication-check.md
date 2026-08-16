# Detect evidence restated across a plan's mechanisms

## Context

**Goal:** make `/plan-review` catch a plan whose mechanisms each write the
same measurement, citation, or investigation result into a different file
without naming one of them as its home — before that shape reaches code.

An error-mode analysis of this repo's 2026-08-02 → 2026-08-14 session corpus
found 6 instances of the agent defaulting to a heavier, multi-site fix and
being redirected to a lighter, single-source one. One reached PR review —
#631, where the reviewer wrote: *"Wrong solution overall. You added the plan
mode caveat all over the place. This is compounding engineering."*

**What that evidence supports.** Only PR #631 produced a shipped diff, so it
is the only instance confirmed to match this shape. The other 5 were
conversational corrections with nothing left to inspect; they establish that
the family of defect recurs, not that this check would have fired on them.

**Why the plan stage specifically.** The duplication a human caught on #631
was designed in `.claude/plans/plan-mode-model-routing.md` before any code
existed: mechanisms M1/M2/M4/M8 each independently instruct adding the claim
*with its measurement*, and none is designated the home. That same plan
carries M3 as the author's own counter-example ("cross-reference `CLAUDE.md`,
don't restate"), so the shape was distinguishable at plan time by the author
who wrote both.

The code stage is deliberately left alone. `comment-discipline-reviewer`
already caught duplication on #631 without any named angle — commit
`962779fb` records two rounds of it finding restatements the first fix round
introduced. That mechanism works; this plan does not touch it.

Intended outcome: one foundation tripwire in `plan-review` Step 4, and one
decision record.

## Approach

`plan-review` Step 4 already runs a list of foundation tripwires that fire on
observable plan text — `Over-powered primitive`, `Compounding layers`,
`Self-referential findings`, `Misordered observe-then-mutate steps`,
`Overcorrection`, `Unjustified given`. This adds a seventh in the same shape.

**The firing condition is duplicated evidence with no named home.** Not
duplicated rule text: a rule may be restated wherever it is needed, which is
what `docs/design-decisions.md` §4 sanctions so each skill stands alone. Not
a compressed summary that points at a named home either — that is a
legitimate altitude difference, and excluding it is load-bearing rather than
a nicety (see below). What the tripwire catches is a mechanism list where the
same evidence lands in several files and the plan never says which one owns
it.

**Why this is answerable at plan stage but not at code-review stage.** A
plan-stage reviewer asks the author to name a home; the author knows their own
altitudes and can answer. A diff-stage reviewer would have to *infer* whether
a given restatement is a summary pointing at a deeper home or a genuine
duplicate — which requires reasoning about document altitude and load paths.

That distinction is what confined this plan to one station. A code-stage
version was attempted as a sixth review angle in `comment-discipline-reviewer`
and abandoned after three discriminator drafts, each falsified by a case in
this repo:

- *Duplicated rationale, not instruction* — fires on the `## Reconciliation`
  block (`code-review/SKILL.md:269-284` and `plan-review/ROUTING.md:53-68`),
  deliberate duplication of explanatory prose with its own pytest.
- *Reader reachability* — deciding whether a reader can reach the canonical
  home means investigating load paths, which is open-ended architecture work.
  `docs/design-decisions.md` §9 records that this agent "reads a diff and a
  fixed rule set, no shell needed," and that closed-form property is the
  stated justification for its `effort: medium` and its omission of `Bash`.
- *Evidence, not rule text* — fires on commit `962779fb`, the corrective
  commit it was meant to exonerate: that diff adds `178/178` at three sites,
  `92/95` at two, each alongside a pointer to the case study it creates as
  the home. [verified: `git show 962779fb`, this session]. Separating those
  legitimate summaries from duplicates needs the same altitude judgment the
  reachability draft was rejected for.

Three failures of the same kind is the signal that the angle does not fit that
persona, which is the call `docs/design-decisions.md` §9's decision tree
exists to make. The plan stage, where the author can simply be asked to name
the home, does not have the problem.

**Threshold is 2, not 3.** From CLAUDE.md's own wording — "Before writing
something a second time, pick the canonical home" — rather than inherited
from `code-review` checklist item 9's code-duplication count of 3.

**Alternatives set aside.** A new dedicated reviewer agent: rejected as the
heavier primitive, and `comment-discipline-reviewer`'s own history argues
against it — that agent shipped for comment verbosity and the defect recurred
within 48 hours on PR #645 (error-mode report row #10). A new numbered
checklist item or Step 1.5 tripwire in `code-review`: rejected per
`docs/design-decisions.md` §13, which considered and rejected a `/code-review`
checklist item for this rule on the grounds that "it would be a second copy of
an always-loaded rule on a surface that can drift from it — the exact failure
the rule names." Extending `comment-discipline-reviewer` to run on plan files:
rejected on angle fit — its "used to be X" angle collides with the ledger's
mandated dated revision notes, and its one-line-not-a-paragraph angle collides
with a plan's Approach section being multi-paragraph rationale by design.

### Assumption ledger

**Root problem:** nothing in `/plan-review` names duplicated evidence across a
plan's mechanisms as a defect, so a plan can designate no home for a
measurement and pass review.

**Givens:**

| # | Given | Reason |
|---|---|---|
| G1 | The check can only be a model judgment, not a hook | A capability boundary of the runtime, not an artifact any repo owns. A hook cannot distinguish a restated measurement from a coincidentally equal number, recognise the same result stated in different words, identify which site the author intends as the home, or separate a compressed summary from a duplicate. [verified: `plan-review/SKILL.md:82-87` — every existing Step 4 tripwire is a model-judgment check, none is hook-backed] |

**Mechanisms:**

- **M1 — `plan-review` Step 4: one foundation tripwire.** Extends an existing
  six-item list in its established shape. `anchors: root`
- **M2 — `docs/design-decisions.md` §25 (new).** Records the decision and,
  critically, why the code-stage angle was attempted and abandoned — so the
  next session reaching for it finds the three falsified drafts rather than
  repeating them. §13 and §9 are left byte-for-byte intact; both are dated
  decision records and preserved content under CLAUDE.md §Working Style Axis
  3. `anchors: root`

**Why one station.** The over-powered-primitive check applies reflexively. A
second station was designed, reviewed across three drafts, and dropped on
evidence — see Approach. The confirmed failure was at plan stage, and the code
stage retains the mechanism that already catches this class there.

**Assumptions:**

- No pytest pins `plan-review` Step 4's tripwire list; `test_agent_roster.py`
  pins agent frontmatter only. [verified: subagent sweep of
  `claude/.claude/hooks/tests/` this session]
- Length cap is not at risk: `plan-review/SKILL.md` is 272 lines against the
  500 `check-skill-length.sh:10-15` grants it. [verified: `wc -l` and
  `check-skill-length.sh:10-15`, this session]
- `skill-review` is required and hook-enforced at commit for the
  `plan-review/SKILL.md` edit. No `staff-*` spawn is triggered: the edit adds
  a tripwire, it does not change the output a `staff-*` lane reviews, and it
  touches no `agents/*.md` scope language. [verified:
  `.claude/rules/review-pipeline-dispatch.md`; `code-review/SKILL.md:249,251`]

## Critical files

All paths relative to the worktree
`.claude/worktrees/caveat-duplication-check/`.

**1. `claude/.claude/skills/plan-review/SKILL.md`** — modify.
Append a seventh foundation tripwire to the Step 4 list, after
`Unjustified given` (ends line 87). Draft text:

> - **Evidence restated across mechanisms.** Two or more mechanisms write the
>   same measurement, citation, or investigation result into different files
>   in full, rather than one holding it and the others pointing at it.
>   Required: name the site that holds it and reduce the others to a pointer,
>   or state per mechanism why its site must carry the evidence in full. A
>   compressed summary that points at the holding site is not a finding; a
>   rule restated at sites that must each stand alone is not one either.

*Reuse:* matches the existing tripwire shape — bolded name, observable
condition, `Required:` clause. Extends a list of six; adds no new section, no
`ROUTING.md` change, and no Item-ownership row (Step 4 tripwires carry none).

**2. `docs/design-decisions.md`** — modify.
Add `## 25. Duplicated-evidence detection sited at plan review, not code
review (2026-08-15)` after §24, which ends at line 279 and is the last section
[verified: `grep -n "^## 2[0-9]\."`, this session]. What it must record:

1. The firing condition, and why it excludes both restated rule text (§4) and
   compressed summaries pointing at a named home.
2. **Why there is no code-stage counterpart**, naming §9. Three discriminator
   drafts were falsified — rationale-vs-instruction on the `## Reconciliation`
   block, reader-reachability on §9's closed-form record, evidence-vs-rule on
   commit `962779fb` itself. Anyone reaching for a `comment-discipline-reviewer`
   angle should find this and the reason before re-attempting it.
3. That §13 rejected a `/code-review` checklist item for this rule, and that a
   Step 4 tripwire firing on plan text is not one.
4. **The evidence ratio: 1 of 6 instances is confirmed to match this shape**
   (PR #631, the only one with a shipped diff). Do not cite "6 instances" as
   undifferentiated recurrence evidence.
5. **A revisit trigger.** The check is a model judgment (G1), so false
   positives have no automatic detector. If two false positives are reported,
   narrow the tripwire or drop it.

*Reuse:* follows the existing dated-section format, with a `### Sources`
subsection where sources are cited.

**3. `.claude/plans/caveat-duplication-check.md`** — this file, committed to
the branch per `branch-management`.

## Verification

1. **Known-positive case.** Read `.claude/plans/plan-mode-model-routing.md` as
   it stood at commit `e119b47f` and confirm the drafted tripwire fires on its
   M1/M2/M4/M8 mechanism list. If it does not fire on the one plan known to
   carry this shape, M1 is unjustified and should be dropped rather than
   reworded. That plan's M3 is the author's own non-duplicating mechanism in
   the same file, so the case discriminates rather than merely matching.
2. **Held-out negative — the altitude case.** Apply the drafted tripwire to a
   hypothetical mechanism list describing what commit `962779fb` actually did:
   one mechanism creating `docs/case-studies/plan-mode-model-resolution.md` as
   the home, and three writing compressed restatements of `178/178` / `92/95`
   into `docs/auto-mode.md`, `docs/cost-levers-considered.md`, and
   `docs/case-studies.md`, each pointing at it. It must **not** fire. This is
   the case that falsified the code-stage design; if the plan-stage wording
   fails it too, the whole approach is wrong, not just the wording.
3. **Held-out negative — the `§4` case.** Apply it to a mechanism list where
   two `SKILL.md` files each state the same convention because each must stand
   alone (`plan-it/SKILL.md:35` and `plan-review/SKILL.md:42` both carry "Pass
   an explicit `model: sonnet` per `CLAUDE.md`'s Model Routing rule"). Must
   **not** fire — rule text, no evidence.
4. **Named-home-but-still-duplicated case.** Apply the tripwire to a mechanism
   list that designates one site as holding the evidence *and* still writes
   the same figures in full into two others. It must **fire**. This is the
   case an earlier wording missed by testing whether a home was *named*
   rather than whether the others were reduced to pointers; the condition now
   tests the substance, and this check is what holds it there.
5. **Deference check.** Confirm the drafted tripwire states an observable and
   defers to CLAUDE.md for the rule rather than arguing for
   single-source-of-truth itself. If it has grown into an argument for the
   principle, cut it back.
6. **Self-test.** Run `/code-review` on the staged diff. Weak evidence by
   construction — self-graded — so treat a pass as a sanity check. A failure
   is still decisive.
7. **`skill-review`** on `plan-review/SKILL.md` (hook-enforced at commit).
8. **Test suite and lint:** `../../../.venv/bin/pytest claude/.claude/` and
   `../../../.venv/bin/ruff check claude/.claude/` from the worktree (the
   contributor `.venv` lives at the main worktree root only). No Python
   changes; run as a regression check on the length and skill-review hooks.

**Checks 1–4 are not blind** — the wording was authored knowing all four
cases. They are regression cases, not validation. No genuinely held-out case
is constructible now; the first real one is the next plan a session writes
without having read this. §25's revisit trigger is what covers that gap.

**No pinning test is proposed.** One tripwire in a file at 272 of a 500-line
cap carries little deletion pressure, and prose-snapshotting costs more than
it buys. `test_agent_roster.py` is the established home if that changes.

## Out of scope

- **A code-stage counterpart.** Attempted and abandoned on evidence — see
  Approach and §25 item 2. `comment-discipline-reviewer` is left unmodified,
  including its charter, angle list, frontmatter, and Output format.

  **Residual seam this leaves.** The tripwire checks a plan's stated design,
  not that the implementation follows it. A plan that correctly designates a
  holding site and then drifts during implementation is caught only by
  `comment-discipline-reviewer`'s existing unmodified trigger, which has one
  cited instance of catching evidence duplication (`962779fb`) and took two
  rounds to do it. Accepted rather than closed: closing it is what the three
  falsified code-stage drafts were for. §25's revisit trigger covers the
  first real occurrence.
- **PR #631** — merged and closed. Not re-opened, edited, or reverted.
- **The 5 live-correction instances** — conversational corrections, never
  shipped code. Nothing to change retroactively.
- **Author-side stations** (`plan-it` Step 5, `code-writer` step 6). Inside
  reach, declined: the confirmed evidence is one case, which one reviewer-side
  station covers. Revisit only on evidence M1 is insufficient.
- **A general-purpose duplication detector.** Logic duplication is
  `code-review` item 9's job; comment verbosity is item 12a's.
- **Editing `docs/design-decisions.md` §13 or §9.** Dated decision records,
  preserved content under CLAUDE.md §Working Style Axis 3. §25 records the
  new decision alongside them.
- **A hook enforcing the check.** Declined on G1's grounds — a hook cannot
  decide the predicate it would gate.
- **Changing the no-shared-partials policy** (root `CLAUDE.md`;
  `docs/design-decisions.md` §4). Inside reach and declined: the policy is
  correct, and the tripwire is built to coexist with it — restated rule text
  carries no evidence and never fires.
- **Editing `CLAUDE.md` §Engineering Judgment.** The principles are complete;
  the gap this closes is activation, not statement.
