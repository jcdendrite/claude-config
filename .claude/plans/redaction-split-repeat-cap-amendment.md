# Prospective scope for the pooled-measurement carve-out's repeat cap

## Context

Amend `docs/private-project-redaction.md`'s pooled-tooling-measurement
carve-out so the "one boundary-crossing exception, ever" cap is
unambiguously prospective — spent only by a split or bin actually
published under this carve-out's own Approval gate, never by content
that predates the carve-out's introduction — and so the Approval gate's
general prior-publication search explicitly reaches pre-carve-out
content too, through the composition bar rather than the cap.

Why now: a sibling branch (`background-wait-phase2-measurement`, not
touched by this plan) needs to use the carve-out's one permitted split
to produce a genuine before/after measurement for
`docs/design-decisions/schedulewakeup-denied-by-bare-tool-name.md`'s
Revisit condition. The engineer explicitly decided this evaluation must
be data-driven — an actual before/after split — rather than inferred
from anecdotal "observed instances," since judging whether a design
change caused a behavior shift needs a measured comparison, not an
anecdote. That made the split's availability the live question: as
currently worded, the cap bullet's own two sentences disagree about
scope — the first sentence says "under this carve-out," the second
drops that qualifier and reaches "in this repository or any other
artifact" — so a reviewer could read the pre-existing case studies,
which predate the carve-out by weeks and were never actual instances of
"the split" on their own structure, as having already spent the one
permitted exception, permanently foreclosing it.

While investigating, this session also found two long-open items from
`.claude/plans/redaction-carveout-followups.md` (PR #952) ready to
close out. Phase C (the own-history multi-account/machine question) is
already answered in substance by the additions PR #972 made to this same doc
and needs only a closure note, not a new decision or a GitHub issue.
Phase D (case-study remediation) is not resolved despite a large PR
#977 sweep (`.claude/plans/redact-account-root-count.md`, 65 files
changed per that PR's own merged diff stat) that addressed much of the
barred-figure surface but left both case studies out of full
compliance — this plan corrects that record rather than attempting the
remediation, which the engineer confirmed is a separate undertaking on
the scale of PR #977.

Intended outcome: three sentences of doc prose resolving the cap's
scope ambiguity, plus a status-correction section appended to
`redaction-carveout-followups.md` — no code, no test, no change to what
the carve-out permits.

## Approach

Amend two sentences' worth of prose in `docs/private-project-redaction.md` so the repeat cap reads as a budget on exceptions this carve-out actually granted, and so the Approval gate's search duty reaches prior publications regardless of whether they shipped under the carve-out. Then append a status section to `.claude/plans/redaction-carveout-followups.md` closing Phase C and correcting Phase D's recorded status. No code, no test, no hook — the carve-out's enforcement tier is deliberately human, and nothing here changes what is permitted.

The design rests on one distinction verified against the bar's own text: bar 2 and bar 3 are different instruments. Bar 2's allowance sentence already carries the qualifier — "Across everything ever published under this carve-out, at most one of the two … may ever be published" — while the sentence immediately after it drops that qualifier and reaches "in this repository or any other artifact." That asymmetry inside one bullet is the whole ambiguity: a reader can take the second sentence as reaching back over content the first sentence's qualifier excludes. Bar 3 is already unscoped as to provenance ("any set of published figures that together produce a barred result, whether they land in one artifact or in separate publications months apart"), so pre-carve-out content is already governed — by the right instrument, without charging a meter it never tripped.

**Why the three-sentence append rather than the one-word fix.** The lighter primitive is real and was weighed: add "under this carve-out" to the second sentence and stop. It fails on the job this amendment has to do. The appended form states the meter positively ("only a split or a bin published under this carve-out spends this allowance"), names the predating case explicitly rather than leaving it to inference from a qualifier, and routes predating content to bar 3 instead of leaving a reader to discover that bar on their own. A sibling proposal has to survive a reviewer round on this reading; a qualifier that must be inferred does not survive that, and re-litigating it costs more than three sentences.

**Rejected alternatives**, each argued at length in this session's consult and recorded here so a later revision diffs against them rather than re-deriving: *retroactive / cap-already-spent* — kills the split permanently, forces every future proposal into a public pre-approval argument, and requires assuming the case studies are settled remediation targets, which Phase D's own status says they are not; *retroactive with a one-time re-grant* — requires the doc to assert the case studies were splits, which is false on their own structure, and starts an amendment-log-inside-rule-text pattern; *counting calendar boundaries rather than gate-passes* (the honest fully-retroactive form) — kills the sibling measurement outright and reintroduces the unbounded historical-search problem four prior review rounds already removed.

**Terminology note.** The originally-drafted wording used "this section" and "Content predating this section." The bullet it lands in says "this carve-out" three times already, and "this section" is ambiguous between the whole carve-out and the `### Three standing bars` subsection it physically sits in. The text below uses "this carve-out" in both places — a two-word terminology alignment that preserves the intended meaning exactly, per `CLAUDE.md` § Prose and Output Format's one-term-per-concept rule, and holds the bullet's existing term.

### Assumption ledger

**Root problem.** The repeat cap's own bullet is internally asymmetric about what spends the allowance, so a reviewer can read pre-carve-out content as having already spent the one permitted split or bin — which would close an exception the carve-out deliberately grants, while leaving the instrument that actually governs that content (the composition bar) uncited.

**Givens** (fixed beyond this plan's reach):

- **G1.** The pooled-measurement tier is reviewer discipline with no hook behind it; a figure's safety depends on how it was computed, which a `PreToolUse` hook cannot see. This plan adds no mechanical enforcement and none is available to it. [verified: `docs/private-project-redaction.md` § "Publishing a pooled tooling measurement", opening paragraph]
- **G2.** Remediation of an already-published wrongly-scoped figure is the owner's call, and a history rewrite is the owner's to run personally — so Phase D's record can be corrected here but its substance cannot be remediated by any agent under this plan's approval. Another party owns it. [verified: `docs/private-project-redaction.md` § "Remediation"]
- **G3.** The cap's "one, ever; a further instance needs an amendment PR" design — as against a tracked marker or spent-exception ledger — is settled by four prior review rounds and is not reopened here; this amendment clarifies what "ever" is scoped to, nothing else. [verified: `.claude/plans/pooled-measurement-time-series-carveout.md` rows 31, 34, 36, 37]

**Assumption rows:**

1. The ambiguity is textual and locatable, not interpretive: bar 2's allowance sentence carries "under this carve-out" and the sentence immediately following it drops the qualifier while widening the reach to "this repository or any other artifact." [verified: `docs/private-project-redaction.md` § "Three standing bars", bar 2]
2. Bar 3 already reaches pre-carve-out content without any amendment — its scope clause is about what figures together yield, with no provenance condition. So routing predating content there is a pointer, not a new rule, and adds no second home for one rule. [verified: same section, bar 3]
3. The two case studies were never instances of "the split" on their own structure — they fail the split's stated conditions on their face, including its one-account/one-machine condition, its no-per-side-pool-size condition, and (in `docs/case-studies/handoff-threshold-impact.md`) its two-point-only shape. Charging the cap for them would conflate the meter with the violation. [verified: `docs/private-project-redaction.md` § "The one permitted split"; structural read of both case studies this session, values not recorded]
4. The Approval gate's split/bin-specific search sentence is correctly cap-scoped as written and must stay byte-identical — the "Own-history counts were never inside this class" section cross-references it ("the same search the Approval gate below already requires"), so rewriting it would break a live cross-reference. Edit 2 touches only the general sentence preceding it. [verified: `docs/private-project-redaction.md`, both sections]
5. The "what the combination would newly disclose" register already exists in the own-history bullet, so edit 2's second sentence reuses the doc's own standard rather than inventing a second one. [verified: same section's final bullet]
6. No test pins this doc's prose. The only mechanical dependency is heading resolution — citation tests resolve `§ "Publishing a pooled tooling measurement"` and `§ "Three standing bars"` against this file, and neither edit touches a heading. [verified: `claude-skills/skills/tests/test_skills.py` citation tests]
7. `select-tests.py` maps `docs/**` to the hooks and skills test directories and maps `.claude/plans/**` to no tests at all, so the selection for this diff is driven entirely by the doc edit. [verified: `claude/.claude/scripts/select-tests.py`, `DOCS_DIR` and `PLANS_DIR` rows]
8. `require-plan-review.sh` hashes every *modified* committed plan file into its active-plan set, and exempts only writes whose own target is a plan file. Editing `.claude/plans/redaction-carveout-followups.md` therefore re-arms the gate against every subsequent non-plan write until `/plan-review` re-runs or the change is committed. This fixes the edit order. [verified: `claude/.claude/hooks/require-plan-review.sh`, the `_lib_is_repo_plan_file` exemption and the active-plan-hash comment block]
9. `.claude/skills/code-review-claude-config/SKILL.md`'s P1 bullet needs no sync. It defers to this doc for "the carve-out's conditions," and its own enumerated triggers are about a figure's shape — a split failing a split condition, a figure composing into a barred total — none of which this amendment changes. Its composition trigger already reaches a new figure sitting beside pre-carve-out content. [verified: the P1 bullet's full text]
10. The sweep in PR #977 deliberately preserved several figure classes under its own scope test — this-repo-scoped totals, shares, and a per-machine median — which is precisely why gap classes survive in both case studies under the doc's current text. The Phase D note describes this as an incomplete sweep with a documented scope rationale, not as a sweep that missed things at random. [verified: `.claude/plans/redact-account-root-count.md` § Approach (the per-site scope test and categories I/K/L) and its Critical files entries for both case studies]
11. Phase C's substantive answer landed in `docs/private-project-redaction.md` (§ "Own-history counts were never inside this class" and § "Account and machine scope"), not in root `CLAUDE.md` as M-C prescribed. That is consistent, not a gap: `CLAUDE.md` already delegates the carve-out's scope limits to this doc by name, so the doc is the canonical home and no `CLAUDE.md` edit is warranted. [verified: both doc sections; root `CLAUDE.md` § "Also redact structural fingerprints and provenance"]
12. Whether a GitHub issue was ever actually filed under M-C is unknown to this plan. It neither files nor closes one; if an issue exists, closing it is the owner's action. [unverified]
13. Correct Phase D's record rather than remediating it, and make this amendment now rather than treating the cap as spent, so the sibling branch's data-driven before/after measurement stays possible. [engineer-verified]
14. This plan file's own prose publishes no figure value, count, percentage, or date range drawn from the case studies, and publishes nothing under the carve-out itself. Gap classes are named by category and file only. [verified: `CLAUDE.md` § "Plans in this repo affect all stow users"; precedent set by `.claude/plans/redact-account-root-count.md`'s own self-republication rule]

### Mechanisms

- **Append three sentences to bar 2 rather than qualifying its second sentence** — `anchors: row1, row2`. The qualifier-only fix leaves the meter stated by inference and leaves bar 3 uncited, which is exactly the reading a reviewer round has to be able to settle in one pass.
- **Widen only the Approval gate's general search sentence, leaving the split/bin sentence untouched** — `anchors: row4`. The split/bin sentence is the cap's own diligence step and is correctly scoped; the general sentence is about composition, which bar 3 already governs without provenance.
- **Reuse the own-history bullet's "would newly disclose" phrasing** — `anchors: row5`. One standard, stated once, cited by the second site rather than restated in a second register.
- **Append a status section to the followups plan; do not rewrite its recorded items** — `anchors: row10, row11, root`. A committed plan is a record under `CLAUDE.md` Axis 3; an appended, explicitly-labelled status section updates the reader without altering what the record says.
- **Add a one-line status pointer to items 1 and 4, leaving their original sentences byte-identical** — `anchors: row10, row11`. Item 4 currently reads as an open question that is in fact answered, and item 1 reads as un-swept when a large sweep has landed; a reader reaching either item without a pointer carries away a stale claim. The pointer is new text beside the record, not a change to the record's claims.
- **Sequence the doc edits before the plan-file edits inside the single dispatch** — `anchors: row8`. Any write to a non-plan file after the followups file is modified denies against the re-armed plan-review gate.

**Over-powered-primitive check.** The heavier mechanisms available were each rejected against source. A tracked marker or spent-exception ledger recording which exception has been used: rejected upstream by four prior review rounds (G3), and it would answer a question this amendment does not ask. A new always-on detector in `deny-private-project-refs.sh`: rejected on the doc's own stated ground that a pooled figure's safety depends on how it was computed, which a hook cannot see (G1) — and there is no string to match here in any case. A new subsection defining "prospective" as a term: heavier than the three sentences it would replace and would create a second home for a rule bar 2 already states. The lighter primitive — a single "under this carve-out" qualifier on bar 2's second sentence — is named and rejected in Approach above, on the grounds that it leaves the predating case to inference.

## Critical files

Two files, one `code-writer` dispatch, `model: sonnet`, no `isolation: "worktree"` (the session is already anchored in this branch's worktree and the output must land here). Not split further: the two files' edits share the same background (the bar-2/bar-3 distinction and the redaction constraint on plan prose), and restating that background in two prompts invites two agents to resolve the same wording question differently.

**Order inside the dispatch is load-bearing** — doc first, plan file second, per row 8.

**`docs/private-project-redaction.md`** *(modify, two edits)* — both in the carve-out's own sections.

1. **Bar 2 of § "Three standing bars"** — append to the end of that numbered bullet, immediately after the sentence ending "…not a fresh proposal under it.", with no other change to the bullet:

   > Only a split or a bin published under this carve-out spends this allowance. Content predating this carve-out does not, whatever shape it takes. Where such content sits beside a new proposal, the composition bar below governs instead.

   "the composition bar below" resolves to bar 3, which physically follows bar 2 in the same list — verified, do not reword the cross-reference.

2. **§ "Approval gate", the search-duty paragraph** — replace only the sentence currently reading "The proposal also names where the agent looked and what it found: any prior publication of the same or a composing statistic." with:

   > The proposal also names where the agent looked and what it found: any prior publication of the same or a composing statistic, whether or not it was published under this carve-out. Where one exists, the proposal states what the combination would newly disclose.
   >
   > *Worked disclosure.* A new split's before-side window overlaps a pre-existing publication's own dated activity checkpoints — say, the pre-existing publication states pooled activity volumes as of two dated checkpoints, and the new split's before-side window falls between them. Neither figure states a pool size on its own, but a reader combining the split's share with the pre-existing publication's dated checkpoints can narrow the window the split's own before-side activity falls in more tightly than either figure discloses alone. That narrowing — not merely the pre-existing publication's existence — is what the proposal must name.

   **Do not touch the sentence immediately following it** ("For a split or a bin, it additionally searches this repository's own history…") — it is correctly cap-scoped, and § "Own-history counts were never inside this class" cross-references it (row 4).

   **Reuse:** the exact phrase "what the combination would newly disclose," already used in that own-history section — match it verbatim, do not paraphrase it into a second, driftable string. The worked-disclosure example mirrors bar 3's existing "*Worked rejection.*" pattern (`docs/private-project-redaction.md` § "Three standing bars", bar 3) — reuse of an instrument the doc already relies on for this class of judgment call, not a new one. Its numbers are illustrative placeholders, not drawn from either case study.

**`.claude/plans/redaction-carveout-followups.md`** *(modify)* — append one new top-level section at the end of the file, plus two one-line pointers. Do not edit any existing sentence, ledger row, or mechanism entry; do not renumber anything.

- **New trailing section** carrying two entries:
  - **Phase C (Context item 4 / M-C) — resolved.** Both sub-questions are answered in substance by `docs/private-project-redaction.md` § "Own-history counts were never inside this class" and § "Account and machine scope". Q1 (single machine, several accounts): a `transcript-analysis.py` Count or Cost/Duration measurement stays inside the carve-out's machinery regardless of `--this-repo` scoping — the own-history exemption explicitly does not extend to it. Q2 (across machines): governed identically, with a single-account-and-machine default and a dimensionless share as the only exception. Note that the answer landed in this doc rather than root `CLAUDE.md` as M-C prescribed, and that this is consistent because `CLAUDE.md` already delegates the carve-out's scope limits to the doc by name (row 11). State that no issue is filed or closed by this plan (row 12).
  - **Phase D (Context item 1 / M-D) — status corrected, not resolved.** PR #977 (`.claude/plans/redact-account-root-count.md`) executed a large repo-wide sweep reaching both case studies, and deliberately preserved this-repo-scoped totals, dimensionless shares, and a per-machine median under its own per-site scope test (row 10). Neither case study is in full compliance with the doc's current text. Name the remaining gap classes **by file, by category, with no line numbers and no values**: `docs/case-studies/handoff-threshold-impact.md` — raw pooled dollar totals, a raw token decomposition, a mean published beside its own pool-size count (the composition bar's barred raw-total equivalent), an interquartile range, per-era pool sizes, and a three-era progression; `docs/case-studies/handoff-hard-block-position.md` — a mean beside its own pool-size count, an interquartile range, per-machine dollar medians, and per-era pool sizes. State explicitly that full remediation is deferred to a separate future plan on this one's own scale, and that this plan attempts none of it (row 13, G2).
- **Two pointers**, each a single appended sentence routing the reader to the new section: one on Context item 1, one on Context item 4. The items' existing sentences stay byte-identical.

**Reuse across both files:** the register and the no-restated-value discipline `.claude/plans/redact-account-root-count.md` already established for exactly this constraint — describe a barred figure by its category, never by its value (row 14).

**Not modified, and verified so rather than assumed:** `.claude/skills/code-review-claude-config/SKILL.md` (row 9), root `CLAUDE.md` (row 11), any test file (row 6), any hook.

## Verification

Repo-documented command, scoped to the diff, per `CLAUDE.md`'s Commands section:

```bash
.venv/bin/python3 claude/.claude/scripts/select-tests.py
```

Confirm it selected the hooks and skills test directories before trusting a green result — that selection comes from the `docs/**` rule; the plan-file edit maps to no tests on its own (row 7). A selection missing either directory is a rule-table bug, not a licence to widen the run by hand.

Targeted run while iterating:

```bash
.venv/bin/pytest claude-skills/skills/tests/test_skills.py -k citation
```

Citation tests resolve headings against `docs/private-project-redaction.md`. Neither edit touches a heading, so these must stay green — a failure means an edit landed in the wrong place (row 6).

**No new test.** This is a scoping clarification of prose in a tier whose enforcement is deliberately human (G1), with no behavior to pin; the only mechanically-checkable property of the file — that its headings still resolve for every citing site — is already covered. A test asserting the three new sentences exist verbatim would be a change-detector, not enforcement.

**Manual checks, both cheap and both required:**

1. Re-read bar 2 whole after the append and confirm the added sentences do not contradict its "Once either lands, in this repository or any other artifact…" sentence — the append narrows what counts as "landing," it does not narrow where a landing counts.
2. Confirm the Approval gate's split/bin search sentence is unchanged, and that § "Own-history counts were never inside this class"'s cross-reference to "the same search the Approval gate below already requires" still resolves to it (row 4).

**Review pipeline.** No `SKILL.md`, agent file, rule file, or plugin file is in the diff, so no `/skill-review`, `/agent-review`, or `plugin-semver` dispatch applies. `/code-review` runs as normal, and its project layer's P1 bullet applies to this diff's own prose: the diff, the plan file, the commit message, and the PR body publish no figure value from either case study and publish nothing under the carve-out (row 14).

**Sequencing footgun.** Once `.claude/plans/redaction-carveout-followups.md` is modified, `require-plan-review.sh` folds it into the active-plan hash and denies every subsequent write to a non-plan file until `/plan-review` re-runs or the change is committed (row 8). Land the doc edits first. If a post-review fix to the doc turns out to be needed after the plan-file notes are in, commit first — a committed, unmodified plan file is historical and disarms the gate.

## Out of scope

- **Full Phase D remediation.** Correcting the record is this plan's deliverable; bringing either case study into compliance is a separate undertaking on the scale of PR #977, requiring its own plan, its own reviewer rounds, and — per the doc's Remediation section — the owner's own call on each already-published figure (G2). Engineer's explicit scope decision this session (row 13). This plan edits no figure in any case study.
- **Any change to `background-wait-phase2-measurement.md`, its branch, or its worktree.** That plan is a sibling branch this one does not touch. Three problems this session's consult found in it, recorded here for whoever picks it back up, none of them acted on by this plan: its design pools several accounts and roots, which collides head-on with the split's "One account, one machine" condition; its extraction list needs to become a share per side rather than raw counts per side, since a raw total is never a valid split form and per-side pool sizes are barred outright; and a `--this-repo` framing exempts it from nothing — the own-history bullet says so explicitly for exactly this measurement type.
- **Reopening the cap's "one, ever" design.** See G3 — already fixed beyond this plan's reach, not a scoping choice this plan makes.
- **Any edit to root `CLAUDE.md`.** The plan could add a pointer there and deliberately will not: `CLAUDE.md` already delegates the carve-out's scope limits to this doc by name, so a second statement would split one rule across two homes and spend always-loaded context for nothing (row 11).
- **Any edit to `.claude/skills/code-review-claude-config/SKILL.md`.** Verified unnecessary against the current P1 bullet rather than assumed (row 9).
- **Filing or closing any GitHub issue.** Phase C's question is answered in the doc; filing an issue for an answered question is the wrong mechanism, and closing one that may exist under M-C is the owner's action (row 12).
- **Publishing any figure under the carve-out.** Nothing in this change proposes, computes, or publishes a pooled figure, so the Approval gate does not fire for this PR.
