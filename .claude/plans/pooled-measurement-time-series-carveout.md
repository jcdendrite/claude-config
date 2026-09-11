# Permit a public-pivot two-point comparison in the redaction carve-out

## Context

Amend `docs/private-project-redaction.md`'s "Publishing a pooled tooling
measurement" carve-out so a bounded, two-point before/after comparison
can be published without re-exposing engagement cadence, unblocking
evaluation of design-decision Revisit conditions that need to detect a
behavior shift around a public config change.

The carve-out currently bars this outright: "A pooled figure may be
reported for the whole period covered only, never as a time series,"
plus a composition-reconstruction clause immediately after it barring
successive whole-period figures that together reconstruct a series.
The concrete motivating case is
`docs/design-decisions/schedulewakeup-denied-by-bare-tool-name.md`'s
first Revisit condition ("the model substitutes a worse wait mechanism
for the removed tool in production ... a Bash sleep ..."), which can
only be evaluated by comparing sleep-poll prevalence before vs. after
the `ScheduleWakeup` deny shipped — a comparison the current rule
prohibits outright regardless of how it's framed.

Why now: this gap surfaced live while executing
`.claude/plans/background-wait-phase2-measurement.md` on a sibling
branch (`background-wait-phase2-measurement`, not this branch), which
needs exactly this kind of comparison and is now blocked pending this
amendment.

Intended outcome: the narrowest amendment that unblocks this — restrict
any permitted before/after comparison to a pivot that is itself
independently verifiable from this repo's own public commit history,
never an owner-nominated or private-engagement-linked date. The pivot
carries no private information regardless of what corpus it's applied
to, which is what closes the cadence-reconstruction risk — not agent or
owner judgment applied case by case. Every other constraint of the
existing carve-out (scope: this repo's own tooling only; the approval
gate; the composition-reconstruction bar for any figure outside the
bounded comparison itself) stays exactly as strict as today. The
approval-citation mechanism itself is a separate, independently-tracked
amendment — not touched here.

## Approach

Add one exception, as two nested sub-bullets under the existing
time-series bar in `docs/private-project-redaction.md`'s "How it may be
reported" list: a permitted whole-period figure may be reported once as
a before value and an after value either side of a pivot, when the
pivot is a commit in this repository's own public history and both
sides are dimensionless. Then add one sentence to the composition
bullet so successive splits at different pivots (or a later whole-period
figure recombining with an already-published split) are barred as the
series or reconstruction they amount to, and make
`code-review-claude-config`'s mirroring P1 bullet defer to the doc
instead of restating a rule it no longer states correctly. One further
addition, found necessary during `/plan-review`'s `ciso-reviewer` pass
rather than part of the original ask: one sentence added to the
approval-gate paragraph requiring a pivot-split proposal to disclose
each side's window bounds and pool size to the owner as approval-only
input, since the gate as originally drafted gave the owner nothing to
judge per-side thinness with.

### Is a pivot-anchored two-point split meaningfully distinct from the barred time series?

Yes — but not because two points is fewer than three, and that
distinction is the whole load-bearing argument, so it goes in the plan
rather than being assumed.

The bar's own stated reason is that "a series re-exposes the cadence the
excluded bullet above already closed off" (`:135-137`), and cadence is
defined two bullets earlier as "how often releases happen… the
engagements' own schedule" (`:132-134`). Cadence reaches a reader
through exactly two channels:

1. **Boundary choice.** Where the buckets fall encodes *when* something
   happened, if whoever chose the boundaries knew private timing.
2. **Per-bucket volume.** A count in each bucket traces activity level
   against calendar time, which is the engagement schedule directly.

A public-pivot, dimensionless, exhaustive two-point split closes both.
Channel 1 is empty because the boundary is a fact any reader reads out
of this repo's own git log — the pivot carries no private information
regardless of which corpus it is applied to, which is why it is a
structural condition and not a judgment call. Channel 2 is empty
because a share, rate, or median does not scale with pool volume — the
same reasoning the doc already applies to Cost at `:129-131` ("A raw
total is barred outright — it scales with pool volume"), reused rather
than reinvented. What survives publication is "the measured behavior
differed across a publicly-known config change" — a statement about
the tooling's behavior, which is what the carve-out exists to permit.

Where it is *not* distinct, and therefore stays barred, is what the two
added conditions cover: a per-side count is channel 2 restored, and
repeated splits at different pivots are channel 1 restored, since this
repository's history offers a commit near any date a reader might want.
Those two are not new defensive layers stacked on the exception — they
are the boundary of the exception itself, in the same sentence that
grants it.

`cost-association-in-docs.md` (§22) is adjacent but does **not** carry
this. Its "a time series needs enough history to clear a noise floor, a
cross-sectional snapshot needs only one well-scoped run" is an argument
about whether a figure is *meaningful*, not whether it is *safe to
publish* — a different failure mode. One framing does transfer, and
only as framing: the permitted form here is one run decomposed once,
not a measurement program, so it does not revive the recurring program
§22 closed.

### Proposed text

Under the existing time-series bullet at `:135-137`, appended as
third-level sub-bullets (the nesting depth and shape the Cost/Duration
bullet at `:118-131` already uses, so this is the list's own idiom, not
a new one):

> - One split is permitted. A whole-period figure may be reported as a
>   before value and an after value either side of a pivot, when the
>   pivot is a commit in this repository's own public history — cited
>   by SHA or merge date, and named before the figure is computed. A
>   date the owner nominates, a date read off the data, or a date tied
>   to an engagement is not a pivot, whatever corpus it is applied to.
>   The proposal cites the transcript turn where the pivot was named,
>   which must precede the turn where the split-producing command ran
>   — cited by session identifier and turn index or timestamp, the
>   same locatable-pointer precision the pivot citation itself uses.
> - The two sides report the same statistic in whatever form that
>   statistic is otherwise permitted — a share, a rate, or a median —
>   and exhaustively partition the period the whole-period figure
>   covered. This split adds no new permitted form for any statistic:
>   Duration stays rate-only, never a share, exactly as the
>   Cost/Duration bullet above already states; the split only adds a
>   second point in time for a form already permitted. Neither side's
>   own pool size is published, and the whole-period value of that
>   same statistic is not published beside them, in this artifact or
>   any other, at any time — those together recover both pool sizes by
>   arithmetic once the pooled count is known. A count total on one
>   side of a pivot is pool volume dated against calendar time, which
>   is the cadence this bullet withholds.

**Superseded by rows 21, 23, 24, 26** (this second sub-bullet and the
composition-bullet quote below): the shipped text moved the
whole-period-value bar out of this sub-bullet and into the composition
bullet, closed a mismatched-statistics regression a round-2 attempt
introduced, named a ratio/relative-proportion disclosure as a covered
outcome, and closed a cross-statistic pivot-accumulation gap by adding
a third outcome to the composition bullet's test.
`docs/private-project-redaction.md:148-181` is the current text; this
blockquote is kept as the historical record of what was first
proposed.

Into the composition bullet at `:138-145`, inserted after "…are the
time series the bullet above bars." and before the closing "The figure
that completes the reconstruction…" sentence, so the closer stays the
closer:

> A second before/after split of the same statistic at a different
> pivot is that series too — this repository's history offers a commit
> near any date a reader would want. A later whole-period publication
> of the same statistic, in any artifact, composes with an
> already-published split the same way — together they recover both
> sides' pool sizes by the same arithmetic — and is barred on that
> basis regardless of how much time separates the two publications.

**Superseded by rows 21, 23, 24, 26** — see the note above the split
sub-bullet's quote.

At `.claude/skills/code-review-claude-config/SKILL.md:20`, the P1
bullet becomes:

> - a time series of an otherwise-permitted whole-period figure, or a
>   before/after split that fails any condition of the carve-out's one
>   permitted split

Rejected for that bullet: enumerating the split's conditions inline so
a reviewer can check them without opening the doc. It reads faster, and
it is a second copy of a rule whose first copy this same PR is editing
— the exact drift the merged `redaction-cost-share-denominator` plan
removed at three sibling sites by turning paraphrases into pointers.
The item already cites the doc section eight lines above, so the
pointer resolves for the reader who needs it.

### Approval-gate addition (found necessary by `/plan-review`'s ciso-reviewer pass, not part of the original ask)

The approval gate at `:192-202` requires the proposal to state "the
figure, the exact command or script that produced it, and the artifact
the figure would land in" — nothing about window bounds or pool size.
For a pivot-anchored split specifically, that leaves the owner nothing
to judge thinness with: a short window on either side of the pivot can
isolate one engagement's activity into what reads as an
innocuous share or median, and the owner has no way to tell from the
proposal alone. One sentence closes this, appended to the approval-gate
paragraph:

> For a before/after split under the time-series bullet's permitted
> exception, the proposal additionally states each side's window
> bounds and pool size — both as approval-only input for the owner to
> judge thinness, never as part of the published figure, and never
> quoted in any commit message, PR body, issue, or other public-repo
> artifact. The disclosure travels through the same non-public channel
> the approval citation itself is drawn from.

**Superseded by row 22:** the closing sentence's circular premise (it
assumed the approval-citation channel is non-public, a property the
base Approval-gate paragraph never establishes) was found and fixed.
`docs/private-project-redaction.md:203-209` is the current text; this
blockquote is kept as the historical record of what was first
proposed.

This is a disclosure requirement on the *proposal*, not a change to who
approves or what may be published — G2 (the gate is unsatisfiable by an
agent) is untouched. The non-public-channel restriction closes an
egress gap `/code-review`'s `ciso-reviewer` pass found: without it, the
disclosure itself — window bounds and pool size — could be posted as a
public PR comment, which is publication of exactly what the split
sub-bullet bars. This mirrors the egress rule the sibling
`background-wait-phase2-measurement.md` plan (M4) already established
for this repo's own transcript figures.

### Assumption ledger

**Root problem.** The carve-out bars every calendar-time split outright
(`:135-137`, reinforced at `:138-145`), so a Revisit condition that can
only be evaluated by comparing behavior before and after a public
config change has no publishable form at all — while the bar's own
stated reason, cadence re-exposure, does not reach a split whose
boundary is public and whose sides carry no volume.

**Givens** — conditions this design treats as fixed and beyond its own
reach:

| # | Given | Why it is beyond this plan |
|---|---|---|
| G1 | The rule has no mechanical enforcement surface to amend; it is tier-3 reviewer discipline and a hook cannot see how a figure was computed. `[verified: docs/private-project-redaction.md:92-95]` | A detector would have to infer computation from output. The doc states this as the reason the whole section is reviewer discipline; changing it is a different plan with a different premise. |
| G2 | The approval gate is unsatisfiable by any agent — it requires a durable citation from the owner's own account. `[verified: docs/private-project-redaction.md:192-202]` | The owner is another party. This plan widens what may be proposed; it cannot widen what may be published without them. |
| G3 | `CLAUDE.md`'s absolute bar on any per-project, per-account, or per-engagement figure is untouched and still binds every artifact. `[verified: CLAUDE.md § "Also redact structural fingerprints and provenance" — "absolute, not a factor to weigh, no borderline case"]` | It is the parent rule this section gates a carve-out from. A doc amendment cannot loosen it, and this one does not try. |
| G4 | Figures already published stay as published; remediation is the owner's call. `[verified: docs/private-project-redaction.md:223-227]` | Assigned to the owner by the section itself. |

**Rows:**

| # | Assumption | Tag |
|---|---|---|
| 1 | A permitted before/after comparison must be anchored at a public, already-disclosed event in this repo's own history — never an owner-nominated split point. The pivot's public verifiability is what closes the cadence-reconstruction risk, not agent or owner judgment applied case by case. | `[engineer-verified]` |
| 2 | A new permitted form must be written into the "How it may be reported" closed list; it cannot be added as a separate section or paragraph. The lead-in states "Both have to be satisfied, and neither extends by analogy," so a permitted form stated outside the list is unreachable by construction. | `[verified: docs/private-project-redaction.md:108-110]` |
| 3 | The bar being amended states its own reason as cadence re-exposure, and cadence is defined two bullets above as the engagements' own schedule rather than the tooling's behavior. The amendment is therefore bounded by that reason and does not have to defend against threats the bar was never carrying. | `[verified: docs/private-project-redaction.md:132-137]` |
| 4 | A per-side count restores exactly what the bar withholds — pool volume dated against calendar time — so the split must be dimensionless on both sides. The doc already reasons this way for Cost, barring a raw total because "it scales with pool volume." | `[verified: docs/private-project-redaction.md:129-131]` |
| 5 | Publishing the whole-period value of the split statistic beside both sides, when the pooled count is also published, recovers each side's pool size exactly: with `N = n_A + n_B`, `s = (s_A·n_A + s_B·n_B)/N` solves to `n_A = N(s − s_B)/(s_A − s_B)` whenever the two sides differ. A reviewer will not spot this, so the bar is stated rather than left to the general composition clause. This identity is exact for a mean-shaped statistic — a total or count divided by a total or count, additive across the two sides — and does not hold for an order-statistic form like a median, which is not a weighted average of its two subset medians. The line is mathematical property, not the doc's form-label: the Cost/Duration bullet's own "rate" example ("median cost per session") is a rate that is median-shaped, not mean-shaped, so the identity fails for it the same way it fails for a median count — "exact for rate/share" would misstate this if read as tracking the doc's three form-labels rather than the underlying arithmetic. The composition bar applies uniformly to all three permitted statistic forms regardless — precautionary rather than derived for a non-additive form, exact for a mean-shaped one — a `ciso-reviewer` finding (final unnarrowed cumulative pass, then refined by a plan-review re-check) against a blanket reading of this row's own proof. A share statistic is grouped with rate above by analogy, not derivation: recovering a share via this identity needs dollar-total weights, not the pool-size-count weights (`n_A`, `n_B`) this row's formula uses, and this row does not carry out that separate derivation — left open, since the composition bar's uniform application makes it a rationale-precision gap, not an operative one. | `[verified: arithmetic re-derived this session against the figure forms the Counts bullet permits at docs/private-project-redaction.md:114-117 and the rate example at :122-123; median/order-statistic non-additivity and scope clarification per ciso-reviewer, code-review pass round 7 and plan-review re-check, agent-reviews/ciso-reviewer-{1789098835,1789100114}-pooled-measurement-t.md]` |
| 6 | Successive splits at different pivots reconstruct the barred series, because public pivots are plentiful rather than scarce — this repo's history supplies a commit near any date. Pivot-must-be-public constrains *which* dates are eligible; it does not limit *how many* are used. | `[verified: docs/private-project-redaction.md:159-164 — the existing clause covers successive whole-period figures but names no pivot case]` |
| 7 | Requiring the pivot to be named before the figure is computed closes a channel the public-pivot rule does not reach: selecting among eligible public pivots can itself be driven by private timing knowledge. This clause goes beyond the original engineer answer and was flagged as an addition rather than folded in silently — the engineer reviewed the flag and confirmed keeping it (see below). | `[engineer-verified — confirmed keeping this clause after review of the flagged addition; anchoring evidence for the mechanism: .claude/plans/background-wait-phase2-measurement.md:102, "the same discipline docs/cost-levers-considered.md:474 records"]` |
| 8 | A thin side of the split can isolate one engagement's transcripts and become a per-engagement figure however it is labelled. This is left to the approval gate's owner judgment rather than closed by a minimum-window number: a threshold chosen by looking at the data is barred by this effort's own carry-forward constraint, and the owner is the only party who knows their engagement timing. Row 15/M5 gives the owner the per-side window and pool-size disclosure needed to actually exercise that judgment, rather than leaving the gate to decide with no visibility. | `[verified: .claude/plans/background-wait-phase2-measurement.md:36-43 (carry-forward constraint 1) and docs/private-project-redaction.md:192-209 (the gate, now with M5's disclosure addition)]` |
| 9 | `.claude/skills/code-review-claude-config/SKILL.md:20` must change or the review checklist silently diverges from the doc it mirrors and flags the newly-permitted form as P1 on sight — at the gate that runs on every commit in this repo. | `[verified: .claude/skills/code-review-claude-config/SKILL.md:9-28, whose four bullets restate the doc's bars including "a time series of an otherwise-permitted whole-period figure"]` |
| 10 | Editing that file does **not** trip the hook-enforced skill-review gate, contrary to what a reader of `.claude/rules/review-pipeline-dispatch.md` would expect. The hook's pathspecs are `claude-skills/skills/**/SKILL.md`, `plugins/*/skills/**/SKILL.md`, and `skills/**/SKILL.md`; a path under root `.claude/skills/` matches none of the three. `/skill-review` is still required — by the rule file and by `/code-review`'s dispatcher — just not blocked on. | `[verified: plugins/skill-management/hooks/require-skill-review.sh:105, used as plain git pathspecs at :115-117]` |
| 11 | That file *is* in the citation-checking corpus (`_all_skill_md_files` globs `.claude/skills/*/SKILL.md`), and its existing citation to this doc section is invisible to the checker only because the quoted heading is hard-wrapped across lines 12-13 — `_CITATION_WITH_TARGET_RE`'s heading group is `[^"\n]+`. Unwrapping it onto one line converts an unchecked citation into a CI-checked one at no cost, and it resolves: `_resolve_citation_target` tries `repo_root / "docs/private-project-redaction.md"` first, and `## Publishing a pooled tooling measurement` normalizes to the cited text exactly. | `[verified: claude-skills/skills/tests/test_skills.py:2676, :2764-2804, :2822-2851; select-tests.py:185-188, :475; docs/private-project-redaction.md:91]` |
| 12 | No new test is warranted. The semantic content is unenforceable per G1; the only mechanically checkable axis is citation-target resolution, and row 11's unwrap brings that under the *existing* `test_skill_citations_resolve_to_real_headings` rather than needing the narrowly-targeted new test the merged precedent had to add for `docs/`-side citations. This amendment adds no `docs/`-side citation and renames no heading. | `[verified: test_skills.py:2854-2868 (existing test, real-corpus); .claude/plans/redaction-cost-share-denominator.md row 10 and M5, whose gap was docs/*.md sitting outside the corpus — not this plan's shape]` |
| 13 | `docs/design-decisions/schedulewakeup-denied-by-bare-tool-name.md` needs no edit. Its line 7 withholding ("governed by … and its approval gate, unexercised here") stays true, and its Revisit condition at `:28` is unchanged in wording and in meaning — this amendment makes it evaluable, not different. | `[verified: file read in full]` |
| 14 | The motivating comparison is reachable with the current instrument, so this amendment permits a form that can actually be produced. `cache-rebuild` has `--since Nd` and no `--until`, so the split comes from two nested runs (whole window, then the after-window) with the before side derived by subtraction inside the session; only the two shares are published. The boundary is day-granular, which suits a pivot cited by merge date. | `[verified: transcript-analysis.py:11706-11736 — cache-rebuild's own argument set, no --until]` |
| 15 | Without a disclosure requirement, the approval gate's existing text gives the owner nothing to judge per-side thinness with — a short window can isolate one engagement's activity into what reads as an innocuous share or median. `/plan-review`'s `ciso-reviewer` pass found this (Finding A) against the actual approval-gate text, not a hypothetical. Fixed by adding one sentence requiring the proposal to state each side's window bounds and pool size as approval-only input. | `[verified: ciso-reviewer, plan-review round 1, against the approval-gate paragraph — now at docs/private-project-redaction.md:203-209 for M5's disclosure sentence]` |
| 16 | "Named before the figure is computed" (row 7) needs an evidentiary standard or it is unverifiable after the fact — an agent could compute splits at several eligible pivots privately and present only the flattering one as if named first. `ciso-reviewer` (Finding B) found no artifact ties the ordering claim to evidence. Fixed by requiring the proposal to cite the transcript turn where the pivot was named, preceding the turn where the split-producing command ran — the same durable-record standard the approval citation itself already meets. | `[verified: ciso-reviewer, plan-review round 1]` |
| 17 | The original "share, a rate, or a median" phrasing in the split sub-bullet read as a flat, category-agnostic menu, which would appear to license a Duration *share* — contradicting the untouched Cost/Duration bullet's "Duration may not [be a share]: a share of pooled wall-clock is one hop from billable hours per deliverable" (`:127-128`). `ciso-reviewer` (Finding C, High) flagged this as the same read-in-isolation defect class the merged `redaction-cost-share-denominator` plan's own Option A wording had to catch and fix pre-implementation. Fixed by stating the split sub-bullet inherits each statistic's own existing category restriction rather than granting a flat menu. | `[verified: ciso-reviewer, plan-review round 1, against docs/private-project-redaction.md:127-128]` |
| 18 | M2's added sentence, as first drafted, only barred a second *split* at a different pivot — not a later independent whole-period publication of the same statistic recombining with an already-published split via row 5's arithmetic to recover both sides' pool sizes. `ciso-reviewer` (Finding D) found this gap; the general composition clause elsewhere already extends other bars across separate artifacts and time, so this split-specific clause was narrower than its own sibling rule without a stated reason. Fixed by extending both the split sub-bullet itself and M2's sentence to cover any artifact, at any time. | `[verified: ciso-reviewer, plan-review round 1, against docs/private-project-redaction.md:159-164's "whether both land in one artifact or in separate publications months apart"]` |
| 19 | Row 15/M5's disclosure sentence, as first drafted, said the window bounds and pool size are "never as part of the published figure" but named no restriction on *where* the proposal itself may be posted — this repo is public, so posting that disclosure as a PR comment would itself publish exactly what the split sub-bullet bars. `/code-review`'s `ciso-reviewer` pass (a fresh check against the committed file, not the plan-review round) found this egress gap and pointed at `background-wait-phase2-measurement.md`'s M4 as this repo's own existing precedent for closing it. Fixed by adding the same non-public-channel restriction to M5's sentence. | `[verified: ciso-reviewer, code-review pass on the staged commit; precedent at .claude/plans/background-wait-phase2-measurement.md M4]` |
| 20 | Row 7/16's "transcript turn" citation, as first drafted, named no citation format — unlike the pivot's own "cited by SHA or merge date" in the same sentence, an agent could satisfy the letter of the clause with a bare narrative turn-number assertion pointing at nothing a third party could check, falling short of the doc's own "durable, independently-checkable" approval-citation bar it claims parity with. `ciso-reviewer` (code-review pass) found this a partial, not full, close of the original Finding B. Fixed by requiring the citation to include a session identifier plus turn index or timestamp. | `[verified: ciso-reviewer, code-review pass on the staged commit, against docs/private-project-redaction.md:198's "durable, independently-checkable record" standard]` |
| 21 | A follow-up attempt to close row 23's "same statistic" gap (widening it to cover an algebraically-derivable equivalent, not just a label match) was itself the wrong fix: applied at the split sub-bullet, it turned a same/same comparability *requirement* between the two split sides into a permission for mismatched statistics between them, and left a fourth occurrence (the composition bullet's "second split... at a different pivot" sentence) uncovered. A `plan-architect` round-3 consult found both defects and that "define the open-ended enumeration once, reference it three times" would still be wrong-shaped, since M2's own rationale (row above) already rejects a second pivot-specific site as the duplication the composition clause exists to avoid. Resolution: the split sub-bullet's comparability requirement is restored to a pure same-statistic match (no widening), and the composition bullet is restated once, at the altitude its own rationale already sits at — barring whatever a further figure would *yield* (recovering either side's share of the pool, or extending the split into a series), not gating on the statistic's label. This covers the whole-period-value case, the complement/rescaling case, and the second-split case in one sentence. "Algebraically derivable" no longer appears in any operative sentence in the repo. | `[verified: plan-architect, round-3 consult, against docs/private-project-redaction.md:148-181's on-disk state; also verified "algebraically derivable" exists nowhere else in the repo]` |
| 22 | Row 19's M5 fix ("the same non-public channel the approval citation itself is drawn from") itself assumed a property the base Approval-gate paragraph never establishes — the citation requirement is satisfied equally by a private channel or a public PR comment from the owner's own account — so an agent reading the clause narrowly could treat a public citation channel as license to post the disclosure publicly too. `ciso-reviewer` (code-review pass, round 2) found this circularity. Fixed by stating the non-public-channel requirement on the disclosure directly: "a non-public channel, regardless of which channel carries the approval citation." This does not weaken the base citation requirement, which is untouched. | `[verified: ciso-reviewer, code-review pass round 2, against docs/private-project-redaction.md:192-202's base Approval-gate paragraph; confirmed non-weakening by ciso-reviewer, code-review pass round 4]` |
| 23 | `ciso-reviewer` (code-review pass, round 2) found a reconstruction gap distinct from row 5's basic arithmetic: as first drafted, "the same statistic" in the split sub-bullet and composition bullet gated on label identity, not algebraic equivalence, so a same-partition complement or a rescaling of the split statistic, published under a different label, would still recover both sides' pool sizes via row 5's arithmetic. Row 21 is the resolution this finding required — this row exists because row 21 originally misattributed the finding to row 15 (the approval-gate disclosure requirement, an unrelated row); this row corrects the citation. | `[verified: ciso-reviewer, code-review pass round 2, agent-reviews/ciso-reviewer-1789092539-pooled-measurement-t.md]` |
| 24 | `ciso-reviewer` (code-review pass, round 4, re-reviewing row 21's fix) found the composition bullet's "recovers how much of the pool fell on either side of the pivot" outcome test named four transforms of the whole-period statistic (whole-period value, complement, rescaling, second split) but no direct side-to-side ratio statement — a ratio recovers the same private information (relative pool-size distribution across the pivot) without needing the whole-period value or the Counts bullet's pooled total at all, so it is a distinct route to the same outcome the four named transforms don't cover by name. Fixed by adding a sentence stating a ratio or relative-proportion statement between the two sides recovers the same thing an absolute count would. | `[verified: ciso-reviewer, code-review pass round 4, agent-reviews/ciso-reviewer-1789095915-pooled-measurement-t.md, against docs/private-project-redaction.md:165-175]` |
| 25 | Row 12 (`[verified]`, settled prior round) asserted no new test was warranted for the `code-review-claude-config/SKILL.md` citation. A round-2 `staff-sdet` review found the corpus-wide `test_skill_citations_resolve_to_real_headings` doesn't actually pin that citation's presence — it only validates citations it manages to extract, so a future re-wrap could silently drop CI coverage with no failing test. Row 12's "no new test" claim is superseded by a dedicated positive-presence test, `test_pooled_tooling_measurement_code_review_skill_citation_survives_rewrap`, added instead of the originally-planned parametrize-list extension. Row 12 is kept as the record of the original (incorrect) assessment, per this repo's provenance convention. | `[verified: staff-sdet, code-review pass round 2, agent-reviews/staff-sdet-1789092539-pooled-measurement-t.md; test added at claude-skills/skills/tests/test_skills.py]` |
| 26 | The composition bullet's outcome test named two outcomes — pool-share recovery and series extension — and neither is reached by a split of a *different* statistic at a second pivot: nothing composes arithmetically across statistics, and a series is successive values of one statistic. The approval gate's prior-publication disclosure had the matching narrow scope ("the same or a composing statistic"), so an agent proposing a second, different-statistic split was not required to surface the first split's pivot at all. The plan's own channel-1 rationale (row 6) is not statistic-scoped — boundary choice is the disclosure, whatever figure is split at it — so rationale and rule disagreed in scope. `ciso-reviewer` (final unnarrowed cumulative pass) found this; its quotation of a "same statistic" qualifier in the composition bullet is from this plan's superseded M2 blockquote, not the shipped doc, which carries no such qualifier. Fixed by adding a third outcome to the operative test (a second pivot added to the set of split dates, whatever statistic), removing "a split at a second pivot" from the transform enumeration it no longer belongs in, and widening the gate's disclosure to every prior split under this carve-out with its pivot. This makes a second pivot a flat bar across all statistics, permanently, once one split lands under this carve-out — measuring a *different* public config change later needs a second PR like this one, which is the closed-by-default posture the section already ends on. Four consecutive rounds (21, 23, 24, this row) had been growing the same transform enumeration; a `plan-architect` consult flagged this as the compounding-layer tell and recommended collapsing the enumeration into the outcome test rather than extending it again — if a future round finds another case the outcome test misses, delete the enumeration entirely and ship the bare outcome test, rather than re-litigating this in prose a fourth time. | `[verified: ciso-reviewer, code-review pass round 7, agent-reviews/ciso-reviewer-1789098835-pooled-measurement-t.md; misquote diagnosed and fix directed by a plan-architect round-4 consult (inline return, no findings file); "same statistic" occurs once in docs/private-project-redaction.md, in the split sub-bullet's two-sides comparability requirement]` |
| 27 | "A ratio or relative-proportion statement between the two sides recovers exactly that, the same as an absolute count would" (row 24's fix) has two readings: a proportion of the two pool-size *segments* (a genuine new disclosure, correctly barred) or a ratio of the two already-*published* split values `s_A`/`s_B` (a function of data the split sub-bullet already permits, under which reading the sentence's own justification doesn't hold — a value derivable from already-safe published numbers can't itself be unsafe). An agent has no way to resolve which reading governs from the text alone. `ciso-reviewer` (code-review pass round 7) flagged this `[FYI]`, and a plan-review re-check confirmed it's still open and untracked. Fixed by rewording to name the pool-size-segment reading explicitly, removing the ambiguity rather than adding a note that it exists. | `[verified: ciso-reviewer, code-review pass round 7 and plan-review re-check, agent-reviews/ciso-reviewer-{1789098835,1789100114}-pooled-measurement-t.md]` |
| 28 | A `comment-discipline-reviewer` pass, run after rebasing this branch onto `origin/main` via `/git-feature-branch-sync`, re-flagged the pivot-exclusion sentence's em-dash aside ("owner-nominated, read off the data, or tied to an engagement") as un-listified parallel exclusions — but that exact compression is the fix `comment-discipline-reviewer-1789098835` itself prescribed as one of two valid resolutions for the same sentence, and the same round's own third finding treated a structurally identical illustrative-aside three lines later as defensible to leave as prose. `ciso-reviewer`, dispatched in the same post-rebase round, independently traced the shipped text back to that prior finding and found it a strict improvement, not a regression. Not re-applied: this is a previously-settled prose judgment call reopened by a second reviewer instance disagreeing with the first's own named alternative, not a new defect — re-editing a third time would trip CLAUDE.md's "Compounding defensive layers are a wrong-foundation tell" bullet, not fix anything. | `[verified: comment-discipline-reviewer, pass following the origin/main rebase (returned inline, no findings file); ciso-reviewer, same post-rebase round (returned inline, no findings file), independently cross-checking against comment-discipline-reviewer-1789098835-pooled-measurement-t.md's finding 2 and finding 3]` |
| 29 | `/ready-for-review`'s Step 3 final unnarrowed cumulative pass — this branch's second, `ciso-reviewer` found a cross-bullet scope hole invisible to every prior round because it lands in pre-existing, untouched text: the Cost-share "split along any dimension but project, account, or engagement" bullet (`:124-128`, unchanged since before this PR) never excluded calendar time from "any dimension," so a temporal Cost-share breakdown could be proposed under that bullet's own language, attaching none of the new pivot/citation/disclosure machinery — a gap this PR's own new pivot mechanism activates by giving "split" a second, colliding meaning nearby, not a pre-existing bug this PR merely inherits unchanged (same activation logic as row 26). `[BLOCKER]`. Fixed by fencing the Cost-share bullet against calendar time explicitly and cross-referencing the time-series bullet as the sole authority over any temporal split of any statistic. | `[verified: ciso-reviewer, /ready-for-review Step 3 cumulative pass, agent-reviews/ciso-reviewer-1789106269-pooled-measurement-t-cumulative.md]` |
| 30 | Same cumulative pass: the pivot-naming transcript-turn citation (row 20's fix) stated citation-precision parity with the pivot's own public SHA-or-merge-date citation but never stated whether the transcript-turn citation itself is public or proposal-only — unlike the sibling window-bounds/pool-size disclosure (M5), which needed two rounds (rows 19, 22) to state its non-public-channel requirement explicitly after the same ambiguity. `[CONCERN]`. Fixed by extending the same "approval-only, never published, non-public channel" requirement to the transcript-turn citation, phrased as one shared rule covering both disclosures rather than restating it twice. | `[verified: ciso-reviewer, /ready-for-review Step 3 cumulative pass, agent-reviews/ciso-reviewer-1789106269-pooled-measurement-t-cumulative.md]` |
| 31 | Same cumulative pass, two related findings: (a) `[CONCERN]` the composition bullet's permanent second-pivot bar has no durable record of which pivot, if any, has already been used, so enforcement depends on an unrecorded future search; (b) `[FYI]` the bullet is silent on whether a *different* statistic split at the *same*, already-used pivot is meant to stay available indefinitely. Collapsing "a second pivot" into "any further split at all, same pivot or different, same statistic or different, once one split has published" resolves both at once: (b) is answered directly (barred, same as a second pivot), and (a) becomes a binary "has any split ever been published under this carve-out" check against this repository's own public history — cheaper and more reliable than tracking which specific pivot dates are already spent. This is a one-level-up simplification (CLAUDE.md's "a locally-valid patch can signal a wrong foundation"), not two separate patches. | `[verified: ciso-reviewer, /ready-for-review Step 3 cumulative pass, agent-reviews/ciso-reviewer-1789106269-pooled-measurement-t-cumulative.md]` |
| 32 | Same cumulative pass: the anti-cherry-picking ordering check (row 16's fix, "named before the figure is computed") anchored only to the turn where "the split-producing command" ran, so an agent could run informal exploratory queries against the same corpus/date range first, see which candidate pivot produces a favorable contrast, and only then "name" that pivot and run the compliant-looking final command — satisfying the letter of the ordering check while still choosing the pivot after seeing the data. `[CONCERN]`. Fixed by widening the ordering check to cover any prior command run against the same corpus or date range, not only the final reporting invocation. | `[verified: ciso-reviewer, /ready-for-review Step 3 cumulative pass, agent-reviews/ciso-reviewer-1789106269-pooled-measurement-t-cumulative.md]` |
| 33 | Same `/ready-for-review` Step 3 cumulative pass, `staff-sdet` empirically reproduced (in a `/tmp` copy of the tree) the exact hard-wrap regression `test_pooled_tooling_measurement_code_review_skill_citation_survives_rewrap`'s docstring and row 25 both name as the gap it closes, and found the pre-existing `test_every_citation_shaped_construct_is_extracted` already fails on that same scenario independently — so the re-wrap case was never actually uncaught. The test's real, unique, undocumented value (verified by testing a citation-deletion-and-paraphrase scenario instead) is guarding against the citation being removed or reworded away entirely, with no `§ "..."` construct left for any extraction-based test to see. No assertion or structural change needed — the test calls the same production extraction/resolution pipeline as its corpus-wide sibling, not a source-scanning re-implementation (checklist item 9g), and its dedicated, non-parametrized shape is structurally correct (row 25 already reversed row 12's parametrize-list approach for an unrelated corpus-scoping reason). Fixed by renaming the test to `test_pooled_tooling_measurement_code_review_skill_citation_is_still_present` and rewriting its docstring to name the actual regression guarded against, so a future reader evaluating "what breaks if I delete this test" gets the accurate answer. Row 25's text is kept as the record of the original (partially incorrect) assessment, per this repo's provenance convention. | `[verified: staff-sdet, /ready-for-review Step 3 cumulative pass, agent-reviews/staff-sdet-1789106269-pooled-measurement-t-cumulative.md — empirical reproduction against a /tmp copy of the tree, two mutated SKILL.md variants]` |
| 34 | A fresh `ciso-reviewer` pass, dispatched to verify rows 29-33's fixes against the current on-disk text rather than trust the claimed fixes, found 4 of 5 PASS and one partial FAIL: row 31's "durable, checkable record" language for the repeat-split search duty asserted that this repository's own published history *is* a durable record, without creating any actual artifact a future search could target directly — the search remained the same open-ended repo-history read the original finding (row 31) meant to move away from, just relabeled. Fixed by requiring the commit or PR that publishes a split under this carve-out to state the fixed phrase "pooled tooling measurement carve-out split" in its message or description, and rewording the search duty to grep for that phrase directly rather than reading full history. This is the actual low-cost artifact the verify pass asked for, not a second relabeling. | `[verified: ciso-reviewer, verify pass on rows 29-33, agent-reviews/ciso-reviewer-1789107100-pooled-measurement-t-verify.md; a full-file grep confirmed no stale "second pivot" framing survived the row-31 collapse]` |
| 35 | A fresh `comment-discipline-reviewer` pass on rows 29-33's fixes (not the fixes described in the ledger — the actual on-disk prose) found 3 multi-fact comment-structure violations the fixes themselves introduced: the transcript-turn citation sentence chained the citation requirement, its format, a precision-parity comparison, and its non-publication status into one clause; the repeat-split-accumulation closing sentence semicolon-chained the outcome's scope, the underlying one-split-ever allowance, and the fresh-PR remedy path into one sentence; and the search-duty sentence bundled the search action with a parenthetical definition and a redundant restatement of the composition bullet's own limit. Fixed by splitting each into separate sentences and, for the search-duty restatement, replacing the redundant copy with a cross-reference to the composition bullet's limit instead — single-source-of-truth per CLAUDE.md, not two copies of the same rule to keep in sync. | `[verified: comment-discipline-reviewer, verify pass on rows 29-33, agent-reviews/comment-discipline-reviewer-1789107100-pooled-measurement-t-verify.md]` |
| 36 | Row 34's marker-phrase mechanism, verified against fresh eyes twice more, kept generating new sub-gaps rather than converging: a `comment-discipline-reviewer` pass found the fix had introduced a duplicate restatement of the composition limit (fixed, no ledger row needed — a same-round self-correction), and a `ciso-reviewer` final-verification pass then found three more residual gaps in the marker mechanism itself — a mutable PR-description alternative undermining the marker's durability, the literal phrase colliding with this very PR's own meta-discussion of the rule (this plan file, this finding, and this ledger row all quote it), and no coverage for a split published to a non-repository artifact, which the Approval gate's own generic "artifact" language already permits and which no in-repo marker could ever reach regardless of wording. Three new findings in the same mechanism across two rounds is the compounding-defensive-layers tell (CLAUDE.md, Working Style) — each layer closing a gap the prior layer created — so the fix is not a fourth patch but removing the mechanism: no marker phrase, no grep duty. In its place, the search-duty paragraph now states the backstop that was already true and already reaches every artifact class: the owner approves every figure under this carve-out personally (Approval gate, unconditional, already the case before this PR), and is the one continuous person across every approval, in this repository or any other artifact — a search is diligence input to that judgment, not the enforcement mechanism, and was never claimed to need to be once the actual enforcement point is named accurately. This also resolves row 34's own two open findings, since neither a mutable-field nor a non-repo-artifact gap exists in a mechanism that isn't there. | `[verified: ciso-reviewer, final verification pass, agent-reviews/ciso-reviewer-1789107464-pooled-measurement-t-final.md; comment-discipline-reviewer, same round, agent-reviews/comment-discipline-reviewer-1789107464-pooled-measurement-t-final.md]` |
| 37 | A second final-verification round confirmed row 36's removal actually closed all 3 residual findings (repo-wide grep found no orphaned marker/grep-duty trace outside the historical ledger citations, and the owner's-unconditional-approval framing was checked against the Approval gate's own text and found accurate, not an overstatement) — but a `comment-discipline-reviewer` pass caught two prose regressions the rewrite itself introduced: the new diligence/backstop sentence re-chained three facts into one clause including a restatement of the Approval gate's own owner-approves-personally fact (the same defect class row 35 had already fixed once at this location), and a previously-verified two-sentence split ("Finding nothing is not approval to publish." / "The composition bar above holds...") got re-merged into one sentence by the same edit. Fixed by splitting the diligence sentence into a cross-reference plus a standalone continuous-witness fact, and restoring the two-sentence form. A second-round `ciso-reviewer` verification then confirmed the branch clean from a security-review perspective, with one cosmetic FYI — "record" was reused for two different senses (a citable approval artifact at one site, the owner-as-witness at another) — fixed by using "witness" for the second sense. | `[verified: comment-discipline-reviewer, second final-verification pass, agent-reviews/comment-discipline-reviewer-1789107824-pooled-measurement-t-final2.md; ciso-reviewer, same round, agent-reviews/ciso-reviewer-1789107824-pooled-measurement-t-final2.md — verdict: clean, no blockers]` |

### Mechanisms

**M1 — Add the exception as two third-level sub-bullets under the time-series bullet** (`docs/private-project-redaction.md:135-137`), text as quoted above. Placing it inside the bullet it narrows keeps one site stating what is permitted, and the third level is the shape the Cost/Duration bullet already uses. Row 21's round-3 correction reverts the split sub-bullet's comparability requirement to a pure same-statistic match — a round-2 widening attempt had turned it into a permission for mismatched statistics between the two sides. `anchors: root, row1, row2, row3, row4, row5, row7, row20, row21, row23`

**M2 — Restate the composition bullet's bar** (`:159-181`) to cover successive pivots, a same-statistic republication, and a whole-period-value/complement/rescaling transform of the split statistic, gated on what a further figure would yield rather than on the statistic's label. This is the accumulation case, which is the composition clause's own subject; a second pivot-specific site would be the duplication that clause exists to avoid — row 21's round-3 correction restates this once rather than adding the second site a first attempt introduced. Row 26 adds a third outcome (a second pivot added to the set of split dates, whatever statistic) to close a scope gap the round-3 restatement's transform enumeration didn't reach, and removes that enumeration's fourth item now that the outcome test covers it directly. `anchors: row6, row21, row23, row24, row26, row27`

**M3 — Make the P1 bullet defer** (`.claude/skills/code-review-claude-config/SKILL.md:20`), and while in that item, unwrap the hard-wrapped quoted heading at `:12-13` onto one line so its citation becomes CI-checked. `anchors: row9, row11`

**M5 (addendum) — Two further edits to the approval-gate paragraph beyond the original M5 addition.** Row 22's resolution fixes the disclosure sentence's circular premise: state the non-public-channel requirement on the disclosure directly rather than by reference to the approval citation's own channel. Row 26's resolution widens the prior-publication disclosure requirement from "the same or a composing statistic" to also cover every prior split published under this carve-out, with its pivot, whatever statistic that split reported — closing the scope gap that let a different-statistic split at a new pivot go undisclosed to the owner. `anchors: row22, row26`

**M4 — Otherwise change nothing else.** The scope lead-in (`:108-110`), the Counts and Cost/Duration bullets' existing forms, the cadence bullet, the three sibling withholding sites, `docs/design-decisions/`, and the test tree. `anchors: G1, G3, row12, row13`

**M5 — Add one disclosure sentence to the approval-gate paragraph** (`:203-209`), text as quoted above under "Approval-gate addition." This is the one part of "change nothing else" that plan-review's ciso-reviewer pass showed could not hold: without it, the gate gives the owner no way to judge per-side thinness before approving. It narrows what a compliant proposal must disclose; it does not change who approves (G2 stands) or what the published figure may contain. `anchors: row15, row19`

**Over-powered-primitive check.** The heaviest candidates were a mechanical detector in `deny-private-project-refs.sh` and a new pytest asserting the doc's prose; both are foreclosed by G1 and by row 12. Among the reachable options, two lighter primitives than M1 were checked against the source and both fail:

- *Leave the doc alone and let the approval gate decide case by case.* Fails row 1 directly — the engineer's answer is that the closure must be structural, not owner judgment per case. It also fails on its own terms: the gate governs whether a permitted figure ships, not whether a form is permitted at all, and `:135-137` bars this form before the gate is ever reached.
- *State the exception in a new paragraph outside the two closed lists.* Fails row 2 — "neither extends by analogy" means a permitted form outside the list is not a permitted form.

The one clause that could read as an added layer is row 7's "named before the figure is computed." It is not closing a gap the public-pivot rule created; it closes pivot *selection*, a channel the public-pivot rule does not touch at all, and it is one mechanically-checkable clause rather than machinery. The engineer reviewed this addition and confirmed keeping it.

## Critical files

**One `code-writer` dispatch, `model: sonnet`, no `isolation: "worktree"`** (the session is already anchored in this branch's worktree and the output must land here). Do not split: all three edits in the redaction doc turn on the same piece of state — the exact conditions the exception states — and a second dispatch would have to restate them to write the checklist pointer coherently, which is the skill's named non-split condition. The diff is two files and roughly twenty-five lines.

- **`docs/private-project-redaction.md`** *(modify, three edits)* — the time-series bullet at `:135-137` (M1); the composition bullet at `:138-145` (M2); the approval-gate paragraph at `:156-166` (M5, added during plan-review). Do not touch the heading at `:91` — four citations resolve to it, and renaming it strands all four.
- **`.claude/skills/code-review-claude-config/SKILL.md`** *(modify, two edits in one item)* — the P1 bullet at `:20` (M3); the hard-wrapped citation at `:12-13`, reflowed so `§ "Publishing a pooled tooling measurement"` sits on one unbroken line with no mid-quote line break (M3).

**Superseded (current locations and file count):** rows 25 and 26 added a
third code file and further edits beyond what this section originally
scoped. The shipped diff touches four files, not two:
`docs/private-project-redaction.md` (composition bullet now at
`:159-181`; approval-gate paragraph now at `:192-221`),
`.claude/skills/code-review-claude-config/SKILL.md` (as above, unchanged
by rows 25/26), `claude-skills/skills/tests/test_skills.py` (row 25's new
test), and this plan file itself.

**Reuse, not reinvention:**

- The dimensionless-versus-total reasoning already exists at `:129-131`; the new sub-bullet extends that sentence's logic rather than introducing a second rationale for the same distinction.
- The citation string `` `docs/private-project-redaction.md` § "Publishing a pooled tooling measurement" `` already exists in the file being edited — reflow it, do not retype it.
- **Superseded by row 25:** this line originally read "No new test," on the reasoning that `test_skill_citations_resolve_to_real_headings` (`claude-skills/skills/tests/test_skills.py:2854`) already covers the corpus this diff touches once the unwrap lands (row 11). Row 25 records why a dedicated test was added instead.

## Verification

1. **Scoped test suite:** `.venv/bin/python3 claude/.claude/scripts/select-tests.py`, then run exactly what it prints. Reading the rule table, `docs/private-project-redaction.md` resolves through the `DOCS_DIR` blanket to `claude/.claude/hooks/tests` and `claude-skills/skills/tests`, and `.claude/skills/code-review-claude-config/SKILL.md` resolves through `ROOT_SKILLS_DIR` (`select-tests.py:475`) to `claude-skills/skills/tests` — two plain directories, no directory-plus-contained-file pair, so GH-882's under-collection shape does not apply. This selection is derived from the rule table, not from executing the script; run it and use its actual output.
2. **Lint:** skip. No Python or shell changes in this diff.
3. **`/code-review`**, which loads `code-review-claude-config` — including the P1 item this diff edits. Five read-back checks, none of them mechanical:
   - The diff publishes no figure at all. This PR amends what *may* be published; it must not itself contain a count, share, rate, median, or date range derived from any transcript corpus.
   - The amended sub-bullets are inside the "How it may be reported" list, not beside it (row 2).
   - The reflowed citation's quoted heading sits on one line, and `## Publishing a pooled tooling measurement` at `:91` is byte-unchanged.
   - The split sub-bullet's statistic-form sentence does not grant Duration a share form — it must read as inheriting each statistic's existing category restriction, not as a flat "share, rate, or median" menu (row 17).
   - The approval-gate paragraph's new sentence (M5) requires window bounds and pool size as approval-only input, does not itself get published anywhere, and travels through a non-public channel rather than a PR/issue/commit artifact (rows 15, 19).
   - The pivot-naming citation (M1) names a session identifier plus turn index or timestamp, not a bare narrative claim (row 20).
4. **`/skill-review`** on the staged SKILL.md diff, per `.claude/rules/review-pipeline-dispatch.md`. Note that `require-skill-review.sh` will **not** block this commit — its pathspecs do not cover root `.claude/skills/` (row 10) — so the gate here is the rule, not the hook. Run it anyway; a shortened checklist bullet is exactly the behavioral-equivalence case that skill demands a table for.
5. **`deny-private-project-refs.sh` runs on the commit as usual.** The prescribed text carries no tracker-ID shape, no `#`-prefixed slug, no home-rooted path, and no long hex run, so it should pass on its own content — but a commit message quoting a pivot SHA would trip the long-hex detector only at 32+ characters, so cite a short SHA if one is named at all.

## Out of scope

- **Publishing any figure under the amended rule.** G2 — who approves and what may be published is untouched; M5 adds one disclosure requirement to what a proposal must state, not a new publication path. This PR changes what may be proposed to the owner; it publishes nothing.
- **Revising `.claude/plans/background-wait-phase2-measurement.md`.** That plan is on a sibling branch and is not unblocked automatically by this landing. Three of its own decisions need revisiting there, not here: its closed extraction list is three *counts* (M3), which the split form bars per side; it publishes a whole-period figure whose split-value twin row 5 now bars beside it; and its pre-registered `n ≥ 30` floor (A7) is stated for the pooled row, not per side, with the after-side of a recent pivot likely to be much thinner than the before-side (row 8). Name this dependency direction in the PR body: Phase 2 depends on this amendment, not the reverse.
- **Any edit to `docs/design-decisions/schedulewakeup-denied-by-bare-tool-name.md`.** Row 13 — nothing in it becomes false, and it is a preserved record under Axis 3 with no task scoping it.
- **A new `docs/design-decisions/` entry recording this amendment.** The plan file is the provenance and the amended doc is the current-state description; a third site discussing the same rule is what the merged precedent removed rather than added.
- **Widening `require-skill-review.sh`'s pathspecs to cover root `.claude/skills/`.** Reachable — the hook is in this repo — and deliberately not done. Row 10 is a real gap between that hook and `.claude/rules/review-pipeline-dispatch.md`'s "hook-enforced" claim, but closing it changes a commit gate for every stow consumer on the strength of an observation made in passing, which is a different plan with its own threat model. Raise it to the engineer as a follow-up.
- **A `--until` or `--pivot` flag on `cache-rebuild`.** Row 14 shows the comparison is reachable today via two nested `--since` runs with the before side derived by subtraction. A flag would install a permanent instrument change on every stow consumer to save one arithmetic step in one publication.
- **A minimum-window or minimum-`n` condition on either side of the split.** Row 8 — any such number is either arbitrary or chosen by looking at the data, and the residual it would address is the one the approval gate (with M5's disclosure addition) is best placed to judge case by case.
- **`pr-description/SKILL.md`, `docs/cost-levers-considered.md:493`/`:501`, and the two other sibling withholding sites.** Nothing in any of them becomes false; the merged precedent already converted their paraphrases to pointers, which is why they need no second edit now.
