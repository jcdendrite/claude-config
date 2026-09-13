# Amend the split cap and bring three case studies into compliance

## Context

This repository's public case-study documentation contains three
already-merged case studies (`handoff-hard-block-position.md`,
`handoff-threshold-impact.md`, `pr-cost-context-bucket.md`) that violate
`docs/private-project-redaction.md`'s pooled-tooling-measurement carve-out
as currently written: each publishes per-side pool sizes, and/or
cross-machine pooled Cost/Duration absolutes outside the permitted
share-of-spend form, and/or more before/after split comparisons than the
carve-out's "one split, ever" cap permits. Separately, the carve-out's own
cap is worded more narrowly than its stated enforcement rationale requires:
it bars any second split "regardless of label or statistic," which would
gut a case study's own cap-sensitivity table (`handoff-threshold-impact.md`
Tier 0's four-row before/after-at-different-thresholds table) that was
actually decision-critical in choosing the current handoff-nudge cap, even
though reporting one statistic across several parameter values of one
pivot poses no more cadence-disclosure risk than a single split does. This
surfaced during a routine reconciliation of this same doc's account/machine
-scope wording (PR #986); the doc's own "Remediation" section requires
flagging an already-published wrongly-scoped figure to the owner rather
than an agent silently fixing it, which happened in-session before this
plan was written. The intended outcome is twofold: amend the carve-out's
cap from "one split, ever" to "one pivot, ever, with one statistic
reportable across several threshold-parameter values on that pivot," and
bring all three case studies into compliance with the amended doc —
preserving every actually decision-relevant figure (the cap-sensitivity
table's full shape) while removing every non-compliant one (per-side pool
sizes, cross-machine pooled Cost absolutes, calendar-dated series, and any
split beyond what the amended one-pivot cap permits).

**Engineer confirmations this session:**
- Editing these three case studies in place is authorized — not routed
  through an addendum/errata pattern. `[engineer-verified]`
- A forward-fixing commit (not a git-history rewrite) satisfies the doc's
  own "Remediation" section; the engineer was shown that this does not
  retract the original figures from prior commits, and confirmed proceeding
  on that basis. `[engineer-verified]`
- `pr-cost-context-bucket.md` is folded into this same plan rather than
  tracked separately, since it shares the same violation shapes.
  `[engineer-verified]`
- The carve-out's cap unit is one pivot per published case study or
  artifact, not a repo-wide running count. `[engineer-verified]`
- Statistic scope on that one pivot is wide: several statistics, and one
  statistic at several parameter values, may all ride it.
  `[engineer-verified]`
- A split may cross an account or machine boundary only where the
  underlying statistic's own mode already crosses it elsewhere in the doc
  (Cost's dimensionless share-of-spend mode, Counts' unscoped reporting);
  Cost rates, medians, and totals stay single-account/single-machine, and
  so does all of Duration. Where a split does cross, both sides must pool
  the same accounts and machines. `[engineer-verified]`
- `handoff-hard-block-position.md`'s after-era block-rate headline becomes
  the macOS-only 4.2%, replacing the pooled 3.5% figure.
  `[engineer-verified]`
- `handoff-hard-block-position.md`'s Tier 1 headline becomes macOS's own
  before/after median pair ($40.97 → $36.03, already in the file's own
  table), replacing the pooled-mean 47.5% figure. Linux's after-era median
  stays as standalone corroboration, not differenced against the macOS
  before value. `[engineer-verified]`
- "No per-side pool size" is kept exactly as currently worded — no edit.
  `[engineer-verified]`
- **Composition disclosure and approval.** This plan's re-selected Tier 1
  headline for `handoff-hard-block-position.md` (macOS median $40.97
  before [08-24..08-29] → $36.03 after [08-31..09-07], a ~12% decline)
  composes with the already-published `handoff-threshold-impact.md` Tier
  1 headline (median $30.79 [`merged_at` < 08-23, an open-ended
  historical baseline, not a tight window] → $31.63 [08-23..08-29], same
  apparent machine/account set, same statistic). Together they disclose
  one open-ended historical reading plus three tightly-clustered
  median-cost-per-PR points spanning roughly two weeks (08-23 through
  09-07) across two artifacts — the composition "Composition is
  publication" and the amended bar 2's cross-artifact clause both reach.
  Named to the engineer this session (`/plan-review` round 1,
  `ciso-reviewer` finding); the engineer approved retaining both
  headlines as published, given both underlying figures are already
  public and no new figure is created by the composition. A `/code-review`
  pass's fresh `ciso-reviewer` dispatch found the original `[≤08-16]`
  window label did not match the source table (the Tier 1 table's Before
  row is computed over `merged_at` < 08-23, not a tight ~08-16 window);
  the engineer re-confirmed the approval against this corrected picture.
  `[engineer-verified: session case-study-split-redaction-57 [523abe],
  AskUserQuestion "Composition gap" turn, answer "Disclose and approve
  as-is"; citation corrected and re-confirmed same session, AskUserQuestion
  "Composition re-check" turn, answer "Yes, still approve as-is"]`

## Approach

Amend the carve-out's repeat cap from a repo-wide "one split, ever" to
**one pivot per published artifact**, narrow the split's account/machine
condition so it crosses a boundary only where the underlying statistic's
own mode already crosses, and then bring three published case studies
into compliance with the amended text using a single collapsing rule —
**on either side of a pivot, publish only a rate, a dimensionless share, a
median, or a growth multiple**. The cap amendment preserves
`handoff-threshold-impact.md`'s cap-sensitivity table (one statistic at
four parameter values of one pivot), which was decision-critical in
setting today's `HANDOFF_NUDGE_ABS_CAP`; the account/machine narrowing is
what makes the two mismatched-composition headlines in
`handoff-hard-block-position.md` a rule consequence rather than an
editorial preference.

### Assumption ledger

**Root problem.** The carve-out's repeat cap is worded more narrowly than
its own enforcement rationale requires and its account/machine condition
more broadly, and three already-published case studies carry per-side
pool sizes, cross-machine composition mismatches, calendar-dated series,
and splits the cap does not reach.

**Givens** (fixed beyond this plan's reach):

- **G1.** Remediation is forward-looking only and is never a retraction —
  git history retains every published figure regardless of what the tip
  says, and the same source bars an agent-run rewrite.
  `[verified: docs/private-project-redaction.md:412-418]`
- **G2.** The pooled-measurement tier is reviewer discipline with no hook
  behind it; a pooled figure's safety depends on how it was computed,
  which a hook cannot see. This plan adds no mechanical enforcement.
  `[verified: docs/private-project-redaction.md:93-95]`
- **G3.** No fresh corpus capture is available. Both machines' windows
  closed 2026-09-07 and the macOS side was reported by a peer session on
  that machine, so any figure not already present in the published text
  cannot be recomputed here — every fix is a subtraction or a re-selection
  among figures already in the files.
  `[verified: docs/case-studies/handoff-hard-block-position.md:29,145]`
- **G4.** `pr-cost --record` resolves a single account by default, so
  `pr-cost-context-bucket.md`'s ledger is single-account, single-machine
  and its bucket table's Cost rates need no scope fix.
  `[verified: claude/.claude/scripts/transcript-analysis.py:8510]`
- **G5.** `docs/case-studies/**` sits outside `_all_doc_paths()` as
  preserved records, so no existing test asserts anything about these
  three files' contents; the only test surface this plan can break is the
  citation grammar.
  `[verified: .claude/plans/redaction-carveout-followups.md:90]`

**Assumption rows:**

1. The cap unit is one pivot per published case study or artifact, not a
   repo-wide running count. `[engineer-verified]`
2. Statistic scope on that one pivot is wide: several statistics, and one
   statistic at several parameter values, may all ride it.
   `[engineer-verified]`
3. A split may cross an account or machine boundary only where the
   underlying statistic's own mode already crosses it elsewhere in this
   doc — Cost's dimensionless share-of-spend mode and Counts' unscoped
   reporting. Cost rates, medians, and totals stay single-account and
   single-machine, and so does all of Duration. `[engineer-verified]`
4. Where a split *does* cross, both sides must pool the same accounts and
   machines. Without this, a macOS-only before side against a pooled
   after side stays permitted and the headline changes (rows 5-6) have no
   anchor in the doc. `[engineer-verified]`
5. `handoff-hard-block-position.md`'s after-era block-rate headline
   becomes the macOS-only 4.2%, replacing the pooled figure at `:58`.
   `[engineer-verified]`
6. `handoff-hard-block-position.md`'s Tier 1 headline becomes macOS's own
   before/after median pair, already present in the file's own Tier 1
   table at `:80-81`, replacing the pooled mean decline. Linux's
   after-era median stays as standalone corroboration, not differenced
   against the macOS before value. The ~12% relative decline the pair
   implies ($40.97 → $36.03) is a deterministic derivation from this same
   engineer-verified pair, not a new figure. `[engineer-verified]`
7. The pooled after-era block rate at `:58` is removed entirely rather
   than demoted. Retaining it beside a macOS-only before value reproduces
   exactly the composition pairing row 5 exists to remove. Anchors: row
   4. `[verified: docs/private-project-redaction.md:199-205]`
8. Tier 3 arms A and B are structural siblings of the Tier 0 and Tier 1
   defect — both difference a macOS before value against a pooled or
   Linux after value. Arm A takes the identical fix as row 6 (macOS pair,
   Linux as standalone corroboration); arm B has no macOS-only after
   value available under G3, so its pair is withdrawn and the direction
   stated qualitatively. Anchors: rows 4, 6.
9. **"No per-side pool size" needs no edit, and
   `handoff-hard-block-position.md:135`'s pre-registered-floor sentence
   already clears it.** The bullet bars publishing *a side's pool size*;
   a statement that both sides clear a floor fixed before the data
   publishes a methodological threshold, not either side's volume, and
   gives no cadence resolution beyond "at least ten." No permissive
   language is added.
   `[verified: docs/private-project-redaction.md:248-251; docs/case-studies/handoff-hard-block-position.md:135]`
10. **The same file's `:87` cross-check does not clear it.** Stating that
    a public `gh pr list` query "returns a PR count matching the pooled
    ledger's `n` exactly" publishes the before side's pool size by
    reference to a query any reader can run. The qualitative
    corroboration the sentence actually carries — the recovered ledger is
    complete, not merely non-empty — survives the equivalence claim's
    removal at no cost. Anchors: row 9.
    `[verified: docs/private-project-redaction.md:199-205,248-251]`
11. Reporting one machine's own within-machine ratio is not the barred
    machine-dimension share split. The bar's worked case is a machine's
    slice of a *pooled* denominator; a rate whose denominator is that
    machine's own population decomposes nothing.
    `handoff-hard-block-position.md:112` already argues this in the
    file's own words and PR #977 shipped it.
    `[verified: docs/private-project-redaction.md:151-158; docs/case-studies/handoff-hard-block-position.md:112]`
12. Publishing two separately-scoped single-machine medians is likewise
    permitted. `:103`'s "no per-machine dimension" governs a pooled
    figure decomposed by machine; the machine-cardinality paragraph
    states that a machine "partitions no engagement" for this operator,
    and PR #977 preserved a per-machine median on that reading. Anchors:
    row 6.
    `[verified: docs/private-project-redaction.md:102-104,319-331; .claude/plans/redaction-carveout-followups.md:198]`
13. Dropping a per-side `n` does not remove a per-side pool size while a
    Total and a Mean remain on the same row — Total ÷ Mean re-derives it.
    Every per-side raw total must go with the `n`. This is what forces
    the collapsing rule rather than cell-by-cell removal.
    `[verified: docs/private-project-redaction.md:199-205; docs/case-studies/handoff-threshold-impact.md:66-69]`
14. A held-out transition window's own value is a third calendar point
    and is withheld; its *ordinal* position ("falls between the two
    sides") discloses no figure and preserves
    `handoff-threshold-impact.md`'s monotonicity argument. Anchors: row
    2.
15. `pr-cost-context-bucket.md`'s cohort comparison fails the split
    conditions at the root, not at the margin: its pivot is "the last 3
    days present in the data," which "Public pivot" disqualifies
    outright, and it runs a second cohort split at a 5-day window. No
    rewording rescues it; the section goes qualitative.
    `[verified: docs/private-project-redaction.md:223-228; docs/case-studies/pr-cost-context-bucket.md:61-71]`
16. `handoff-threshold-impact.md:60`'s crossing-share pair survives. It
    is a distinct statistic with its own two-point split at the study's
    one pivot, with the study's declared transition window held out
    between its sides, so it satisfies the amended exhaustive-partition
    condition — and it is the figure `docs/handoff-nudge.md:132` depends
    on. Anchors: rows 2, 14.
    `[verified: docs/case-studies/handoff-threshold-impact.md:27,60; docs/handoff-nudge.md:132]`
17. `docs/handoff-nudge.md` carries no figure from
    `handoff-hard-block-position.md` today. `:32` and `:135` are
    qualitative pointers and `:132`'s figures come from the other study
    (row 16), so the decision-6 and decision-7 changes require no edit
    there. `[verified: docs/handoff-nudge.md:32,132,135]`
18. The heading rename breaks no test. No *live* `§ "The one permitted
    split"` citation exists outside a preserved plan record; the two
    live prose references are at `docs/private-project-redaction.md:357`
    and `.claude/skills/code-review-claude-config/SKILL.md:20-21`. One
    citation-grammar-shaped hit does survive at
    `.claude/plans/redaction-split-repeat-cap-amendment.md:74`, but
    `.claude/plans/**` sits outside both `_all_doc_paths()` and this
    test's citation-extraction corpus (G5's reasoning applies
    identically here), so it is inert rather than absent.
    `[verified: repo-wide grep this session; claude-skills/skills/tests/test_skills.py:3129-3143,4566-4590]`
19. `.claude/plans/narrow-provenance-redaction-rule.md:430-437` holds the
    only surviving copy in the tip tree of two per-side Cost means and
    two per-side pool sizes; the site it quotes
    (`docs/cost-levers-considered.md:509-512`, the old
    $49.55→$26.01 (n=19/n=49) figure) was already swept of them by PR
    #977 — that line range now holds unrelated cache-rebuild
    subagent-split material. It also raises, as an open question for
    the engineer, exactly the composition question this plan answers.
    `[verified: .claude/plans/narrow-provenance-redaction-rule.md:430-437; docs/cost-levers-considered.md:509-512]`
20. `.claude/plans/redact-account-root-count.md:153` restates `47.5%` as
    a then-correct instruction. A dimensionless percentage is not itself
    barred content, and the line is a preserved record of a decision that
    was right when made, so it is left alone. Anchors: row 19.
    `[verified: .claude/plans/redact-account-root-count.md:153]`
21. This plan's Tier 1 re-selection for `handoff-hard-block-position.md`
    composes with `handoff-threshold-impact.md`'s already-published Tier
    1 split into one open-ended historical reading plus three
    tightly-clustered median-cost-per-PR points across two artifacts.
    Both figures are already public and the composition creates no new
    figure, so the engineer approved retaining both headlines as
    published rather than re-selecting a non-composing statistic.
    `[engineer-verified: session case-study-split-redaction-57 [523abe],
    AskUserQuestion "Composition gap" turn, answer "Disclose and approve
    as-is"; citation corrected and re-confirmed same session,
    AskUserQuestion "Composition re-check" turn, answer "Yes, still
    approve as-is"]`

### Mechanisms

**M1 — amend bar 2 from a repo-wide allowance to a per-artifact pivot
budget, and reconcile bar 1's dependent sentence.** `anchors: row1, row2`.
Bar 1's closing clause ("It cannot qualify as the split, since the split
requires single-account, single-machine scope") becomes false under row 3
and is redirected to the split's own condition rather than restated.

Over-powered-primitive check on amending the shared doc at all. Two
lighter primitives exist, both rejected: (i) leave the cap at "one, ever"
and delete the cap-sensitivity table — rejected, it destroys the
decision-critical evidence for today's shipped `HANDOFF_NUDGE_ABS_CAP`
value; (ii) grant a one-off exception in the case study's own prose,
leaving the doc untouched — rejected, the doc is the single authoritative
home for the carve-out's conditions, and a per-artifact exception stated
only in a case study cannot be checked by `code-review-claude-config`'s
P1 item, which reads the doc.

**M2 — narrow the split's account/machine condition to inherit, not
override, the statistic's own crossing mode; require matched composition
on both sides.** `anchors: row3, row4`.

**M3 — the per-side rate-only rule, applied uniformly to every
era-labelled table and sentence in the three case studies.** `anchors:
row13`. On either side of a pivot, publish only a rate, a dimensionless
share, a median, or a growth multiple. Drop every raw count, raw total,
and pool size sitting on a side, plus any companion figure that
re-derives a dropped one arithmetically. Whole-period figures that are
not split at the pivot are untouched.

Over-powered-primitive check on a collapsing rule rather than per-site
removals. Two lighter primitives exist, both rejected: (i) remove only
the literal figures the four named violation classes name, cell by cell —
rejected per row 13, a retained Total÷Mean re-derives a dropped `n`, so
cell-by-cell removal leaves the class open; (ii) round or generalize the
counts instead of dropping them — rejected, cadence survives rounding,
and `.claude/skills/code-review-claude-config/SKILL.md:23` directs a
rounded figure to get *more* scrutiny, not less.

**M4 — re-select each mismatched headline from a figure already present
in the file, rather than recomputing.** `anchors: row5, row6, row8`,
constrained by G3.

### Phases

1. **Doc amendment.** `docs/private-project-redaction.md` and
   `.claude/skills/code-review-claude-config/SKILL.md`. Lands first;
   every case-study edit is justified against the amended text.
2. **`handoff-hard-block-position.md` and its downstream restatements.**
3. **`handoff-threshold-impact.md` and `pr-cost-context-bucket.md`.**
4. **The two plan restatement sites.**

Phases 2 and 3 both touch `docs/cost-levers-considered.md` (disjoint
sections, `:535-544` and `:305-315`) — sequence them rather than
parallelizing, since overlapping edits in one worktree clobber silently.

## Critical files

### Phase 1

**`docs/private-project-redaction.md`** — four verbatim replacements plus
two dependent-reference edits.

`:180-184` — replace the final sentence only. From `It cannot qualify as
the split, since the split requires single-account, single-machine
scope.` to:

```
   Whether it may be the figure a split reports is
   governed by that split's own account and machine condition below.
```

`:185-198` — replace the whole of bar 2 with:

```
2. **One pivot per artifact.** This carve-out defines exactly one
   narrow exception to "No time series" above: the split. Each
   published artifact may carry at most one pivot. Every figure that
   artifact splits rides that same pivot: several statistics may, and
   one statistic may be reported at several parameter values of its
   own, so long as all of them split at the same commit. A second
   pivot in the same artifact is barred, regardless of label or
   statistic, same or different. Reporting one statistic across
   several parameter values of one pivot adds no calendar point, which
   is why the cap counts pivots and not figures.

   The cap is per artifact, not a running total across everything ever
   published. Two artifacts each splitting at their own pivot are two
   permitted splits. What they may not do is compose: where a later
   artifact's split would extend or restate an earlier one's into more
   than two calendar points for the same statistic, "Composition is
   publication" below governs and bars it.

   Only a split published under this carve-out spends an artifact's
   pivot. Content predating this carve-out does not, whatever shape it
   takes. Where such content sits beside a new proposal, the
   composition bar below governs instead.
```

`:218` — `### The one permitted split` → `### The permitted split`.

`:241-245` — replace the whole bullet with:

```
- **One account, one machine, unless the statistic's own mode already
  crosses.** Both sides resolve to a single `CLAUDE_CONFIG_DIR`
  account and machine. The two exceptions "Account and machine scope"
  below already grants — Cost's dimensionless share-of-spend mode, and
  Counts' unscoped reporting — carry into a split of that same
  statistic in that same mode, and nothing else does. A Cost rate,
  median, or total stays single-account and single-machine on both
  sides, and so does every Duration figure, since Duration has no
  crossing mode to inherit. Where a split does cross, both sides pool
  the same accounts and machines: a before side drawn from one machine
  against an after side pooling two is a change of composition dressed
  as a change over time, and neither side's value means what the pair
  implies.
```

`:246-247` — replace with:

```
- **Exhaustive partition.** Together, the two sides cover exactly the
  period the whole-period figure covered. One transition window
  bracketing the pivot may be held out instead, where the change
  needs time to stabilize. State its bounds; publish no figure
  computed on it. A held-out window's own value is a third calendar
  point, which the "No time series" bar withholds.
```

`:248-251` — **no edit.** Per row 9.

`:357` — `For a split under "The one permitted split" above` → `For a
split under "The permitted split" above`.

`:364` — extend the approval-only disclosure to cover a held-out window:

```
- each side's window bounds and pool size, and the same for any
  held-out transition window, so the owner can judge whether either
  side is thin enough to isolate one engagement;
- where the split crosses an account or machine boundary under "One
  account, one machine, unless the statistic's own mode already
  crosses" above: which accounts or machines contribute to each side,
  and any already-published or routinely-automated single-account or
  single-machine exact figure of the same quantity, so the owner can
  weigh whether the crossing side and that exact figure together
  isolate one account's or machine's own value by subtraction — the
  same disclosure duty the pooled-Count paragraph below already
  carries for the analogous case.
```

This closes a gap `/plan-review`'s `ciso-reviewer` pass surfaced: M2
lets a split inherit a statistic's existing crossing mode without
inheriting the disclosure duty the doc already requires for that same
crossing risk elsewhere (`:366-376`'s pooled-Count paragraph).
`[engineer-verified: the engineer's "I approve the plan" in the
/handoff invocation that closed the prior session covers this bullet's
drafted wording]`

`:401-406` — replace the prior-instance-search paragraph with:

```
For a split, the proposal additionally names any split already
published under this carve-out that reports the same statistic, and
states what that split and this one would together disclose. The cap
is per artifact, so a prior split elsewhere does not by itself
disqualify a new one; composing with it into more than two calendar
points for the same statistic does. That search is diligence, not
enforcement. The owner is the one continuous witness, across this
repository and any other publication artifact, to what has already
shipped under this carve-out. Finding nothing is not approval to
publish.
```

**`.claude/skills/code-review-claude-config/SKILL.md`** — `:19-21`,
replace the last checklist sub-bullet and add one:

```
- a time series of an otherwise-permitted whole-period figure, or a
  before/after split that fails any condition of the carve-out's
  permitted split
- a second before/after pivot in the same artifact, a per-side pool
  size on either side of one, or two sides drawn from different
  account or machine compositions
```

Reuse: the pinned citation at `:12` targets § "Publishing a pooled
tooling measurement" and is untouched.

### Phase 2

**`docs/case-studies/handoff-hard-block-position.md`** — the full rework.

`:13` — replace the Tier 0 short-answer bullet:

```
- **Tier 0 (mechanism-engagement gate) confirms the shift exactly where predicted, and nowhere else.** The `action=block` fire rate collapsed from 29.8% of nudge fires (before) to 4.2% (after) on the one machine whose data covers both eras — moving at the 2026-08-30 boundary (PR #769), with no detectable further shift specifically at the 2026-08-31 boundary (PR #782). This confirms the study's own falsifiable prediction that #782 changed nothing measurable about reachability.
```

`:14` — replace the Tier 1 bullet:

```
- **Tier 1 (primary cost outcome, cost per shipped PR) is a win on the one machine covering both eras.** Median cost per shipped claude-config PR fell about 12% there ($40.97 → $36.03). The other machine's after-era median ($10.98) is consistent in direction and is reported as a standalone reading, not differenced against a before-era value it has no counterpart for. A two-point before/after comparison at this sample size, with no variance data available, supports a directional read rather than a tested result. This is the opposite of the precedent's own finding when the advisory cap tightened — there, cost per PR *rose* alongside a mechanism win; here it *fell* alongside one.
```

`:15` — replace the Tier 3 bullet:

```
- **Tier 3 (mediator instrument) explains why, in the predicted direction.** Startup-burn share of branch dollars fell on the one machine covering both eras (3.6% → 2.3%), with the other machine's after-era share (2.1%) consistent in direction as a standalone reading. The share of session dollars spent past the *advisory* threshold rose, but its two sides draw on different machine compositions, so no figure is published for it — fewer forced handoffs, and a real deep-context tail still forming underneath the collapsed block rate.
```

`:18` — in the net-verdict bullet, replace the closing clause `falling
mean and median cost per PR, falling mediator overhead, no guardrail
decline` with `a falling median cost per PR on the one machine covering
both eras, falling mediator overhead, no guardrail decline`. Leave the
rest of the bullet intact.

`:45-74` — replace the whole Tier 0 section:

```
## Tier 0 — the mechanism engages exactly where predicted

**Primary reading: one machine (macOS), both eras, same account set, same attribution method (session-first-timestamp) — no cross-machine mixing.**

| Era | `action=block` share of nudge fires |
|---|---|
| Before (08-24..08-29) | 29.8% |
| After (08-31..09-07) | 4.2% |

The block rate collapsed by roughly 86% relative (29.8% → 4.2%) on this single, fully-covered source — the cleanest reading this study has, since it involves no cross-machine mixing of attribution methods or eras. Neither side's fire or block count is published: a count on one side of a pivot is pool volume dated against calendar time.

**Corroborating: the other machine's (Linux) after-era block rate, 2.7%, block-time attributed** (a more precise method — see "How this was measured" — but with no usable before-era counterpart on this machine): lower still than macOS's own after-era rate, reinforcing the same direction rather than contradicting it. It is not differenced against macOS's before-era rate, and no pooled cross-machine rate is reported: the before era has only one machine's data, so a pooled after-era figure would change machine composition across the pivot rather than measure a change across it.

**Falsifiable sub-boundary check** (Linux-only — unaffected by the retention gap, since it uses only after-era data, and needs the raw `est=` values only Linux's block-time capture provides). This study's own arithmetic (PR #769 and PR #782 select the identical reachability boundary at every context-window size) predicts no further shift specifically at the 2026-08-31 `#782` boundary. Taking each after-era day's own median `est=` in turn, every one of them — the `#782` merge day included — falls inside a single narrow band, with no jump at that sub-boundary. The prediction holds. No per-day value or count is published: a per-day series is exactly the calendar axis the "No time series" bar withholds. The same after-era values also confirm the floor moved from clustering near the old 230,000 position to clustering near the new 470,000 one, both public defaults in this repository's own history.

**Attribution-method check.** The median absolute difference between a Linux block line's `est=` and its matched turn's own `context_at_turn` is 401 tokens (max 25,987) — small relative to the ~470,000-token values being matched, so block-time attribution is a good approximation, and the two attribution methods used across the two machines agree on direction and rough magnitude (macOS's own before/after: 29.8%→4.2%; Linux's block-time after-era rate: 2.7%) — the sensitivity arm the plan called for, produced by necessity rather than by design.
```

`:76-87` — replace the whole Tier 1 section:

```
## Tier 1 — primary outcome: median cost per PR fell on the one machine covering both eras

**Primary reading: one machine (macOS), both eras, same account set — the same no-cross-machine-mixing rule Tier 0's primary reading follows.**

| Era | Median cost per shipped claude-config PR (macOS) |
|---|---|
| Before (`merged_at` 08-24..08-29) | $40.97 |
| After (`merged_at` 08-31..09-07) | $36.03 |

The median fell about 12% on this single, fully-covered source, satisfying the pre-registered "clean win" criterion on this axis (Tier 1's after-era median at or below the before-era median). A two-point before/after comparison at this sample size, with no variance data available, supports a directional read rather than a tested result.

**Corroborating: the other machine's (Linux) after-era median, $10.98** — a standalone single-machine reading, consistent in direction with the macOS decline. It is not differenced against the macOS before-era value: the before era has no Linux counterpart, so a macOS-to-Linux comparison would be a change of machine composition dressed as a change over time.

No pooled median is reported. Computing one correctly would require raw per-PR values from both machines, which this study's aggregate-only cross-machine protocol doesn't provide (see "Corpus scope and honest limits"). The two machines' after-era medians differ from each other by more than the macOS pair differs across the pivot, which is itself informative: cost-per-PR variance is wide enough that one machine's median is not representative of the other's population, which is the second reason the before/after reading stays on macOS alone.

**The reversal-candidate rule's other clause — after-era median exceeding the before-era interquartile range's upper bound — cannot be evaluated.** The before-era IQR requires raw per-PR values this study does not have from the macOS side. This is reported as a genuine evaluability gap, not treated as passing by default.

**Independent cross-check.** `gh pr list --state merged --search "merged:2026-08-24..2026-08-29"` against this repo directly — bypassing both machines' local transcript-derived ledgers — confirms the recovered before-era ledger is complete, not merely non-empty. No count is published on either side of that comparison.
```

`:38` — the pooled-`n` double-counting caveat now bears only on Tier 3.
Reword to state that Tier 1's comparison no longer pools across
machines, so the risk it names applies to Tier 3's pooled branch counts
alone (which `:39` already covers).

`:104-112` — Tier 3 arm A: restructure so the before/after pair is
macOS on both sides (`3.6% → 2.3%`), with Linux's after-era share as a
standalone corroborating reading beneath, explicitly not differenced
against the macOS before value. Keep `:112`'s existing within-machine
-ratio argument.

`:114-121` — Tier 3 arm B. Drop the `Sessions` column (per-side pool
sizes) and withdraw the two share values: their sides draw on different
machine compositions and no macOS-only after value exists under G3. Keep
the direction and the finding — the deep-context tail absorbing part of
what the collapsed block rate gives back — plus the existing ISO-week
disclosure, and state that no figure is published because the two sides'
compositions differ.

`:129` — Confound 3 ("Single before-era source"). Sharpen: the asymmetry
is now resolved for Tiers 0, 1, and 3A, whose comparisons run on macOS
both sides, and is why Tier 3B publishes no figure at all.

`:135` — keep the pre-registered n≥10 sentence verbatim per row 9,
changing only `mean/median reporting` to `median reporting`.

`:137` — Numeric revisit trigger, **unchanged**. Its second sentence
already names the future path.

`:139-141` — replace the update section:

```
## Update to `docs/handoff-nudge.md`

The Known-limitations section's "hard block is unreachable on a 200k-window model" bullet points at this study's measured before/after block rate (29.8%→4.2% on the one machine covering both eras) and the confirmation that this unreachability predates PR #782 — it was already true after PR #769, per the plan's mechanism-verification arithmetic. The revisit trigger at line 32 ("once at least two contributors report a hard block on a session they consider legitimate, or after enough elapsed time that fresh corpus data materially changes the picture") is unchanged in substance: this study found a cost win on the one machine covering both eras, which argues against tightening the floor further, but the still-rising deep-tail spend share (Tier 3 arm B) means the trigger stays in place rather than being relaxed — a future report of a hard block on a session considered legitimate remains the signal to revisit, this study's win notwithstanding.
```

`:147` — Sources. `the independent, transcript-corpus-free cross-check
confirming the recovered before-era n` → `the independent,
transcript-corpus-free cross-check confirming the recovered before-era
ledger is complete`.

`:29` — consistency check only. Verify its "no per-machine dollar or
count figure is broken out beyond what's needed to disclose which
machine covers which era" still reads true against the reworked tables.

**`docs/cost-levers-considered.md`** — `:541`, drop the pooled-rate
clause so the row carries the macOS pair only. `:542`, replace the
verdict and headline with the median finding ($40.97 → $36.03, ~12%,
macOS both eras) plus Linux's after-era median as a standalone
corroborating reading; keep the existing directional-read caveat sentence
verbatim. `:543`, apply the same fix — the startup-burn pair becomes
macOS-only with Linux as corroboration, and the advisory-threshold share
figures are withdrawn with the direction kept. `:544` needs no change.

**`docs/case-studies.md`** — `:16`, replace `mean cost per shipped PR
fell 47.5%` with the median finding on the one machine covering both
eras. Everything else in the blurb stands.

**`docs/handoff-nudge.md`** — check-only, no edit expected. `:32`,
`:132`, and `:135` carry no figure from
`handoff-hard-block-position.md` (row 17).

### Phase 3

**`docs/case-studies/handoff-threshold-impact.md`** — apply the
collapsing rule throughout.

- `:36` — drop the two captured per-side denominators; keep the
  qualitative point that the captured denominator trails the merged
  population, and keep the two public merged-PR population counts
  (own-history, `gh`-recomputable).
- `:40-45` — Tier 0 table: drop the `Sessions` column (per-side pool
  sizes). Withdraw the excluded-window row's share and the sensitivity
  row's share. `:47`'s two arguments survive qualitatively: the
  transition window's own value falls between the two sides (an
  ordering, not a figure), and a narrower immediately-prior before-window
  sensitivity arm shows the same before-high/after-low pattern.
- `:51-56` — **the cap-sensitivity table stays.** Drop only the `(n=…)`
  annotations from all four rows.
- `:60` — **keep the crossing-share pair**; drop only the two
  parenthesized per-side `n` values.
- `:66-69` — Tier 1 table: drop `n (captured)` and `Total` together.
  Keep `Mean`, `Median`, `IQR`.
- `:73` — drop the token decomposition entirely.
- `:79-86` — Tier 2 table: drop `Branches` and `Reviewer spawns`; keep
  `Spawns/branch`. Convert the two denial totals and their category
  breakdowns, and the user-facing-friction pair, to rate or ratio form.
- `:92-96` — keep the differenced-window reasoning and the
  findings-per-dispatch ratios; drop the raw cumulative and per-window
  counts.
- `:102-111` — Tier 3 table: drop the excluded-era rows' figures, and
  drop `Branches`, `Startup burn`, and `Total $` from the claude-config
  -only rows. Keep `Sessions/branch` and `Burn share` on every remaining
  row. Rewrite `:111` for two eras, keeping the monotonicity argument in
  ordinal form.
- `:129`, `:131`, `:135` — consistency pass: drop the digits, keep the
  qualitative role. `:135` needs no change.

**`docs/case-studies/pr-cost-context-bucket.md`** — `:57-80`, withdraw
both cohort tables and both cohort splits. Keep the finding qualitatively
(mean context per session was materially lower in the most recent cohort
while `$/PR` did not fall), keep `:75-80`'s explanatory paragraph in full
(it cites no figures), and add one sentence stating that the comparison
used a data-derived cutoff rather than a public-commit pivot, which the
carve-out's split conditions do not permit. Trim `:91-95`'s corresponding
limits bullet to match. The bucket table (`:41-48`) and coverage check
(`:29-34`) are untouched.

**`docs/cost-levers-considered.md`** — `:314` only, in the section this
phase covers: the startup-burn row publishes three calendar points
including one computed on the held-out window. Reduce to the before/after
pair. `:311`, `:312`, `:313`, and `:315` need no change.

### Phase 4

**`.claude/plans/narrow-provenance-redaction-rule.md`** — `:430-437`,
strike the parenthetical carrying the two per-side Cost means and the two
per-side pool sizes, keeping the dimensionless percentage and the
file-and-line reference. Replace the paragraph's closing "raised to the
engineer directly, not resolved in this PR" with a pointer recording that
this plan answers the question and how.

**`.claude/plans/redaction-carveout-followups.md`** — `:198`, add one
sentence to the Phase D status recording that this plan executes the
split-related subset of the remaining gap classes and naming, by class,
what it does not (see Out of scope). Do not restate any figure.

## Verification

`.venv/bin/python3 claude/.claude/scripts/select-tests.py` — the repo's
documented scoped test command. Expect the citation-grammar tests in
`claude-skills/skills/tests/test_skills.py` to be selected by the
`.claude/skills/code-review-claude-config/SKILL.md` edit; nothing else in
this diff has test coverage (G5).

**Hook-enforced gates.** The SKILL.md edit puts `/skill-review` in the
required path — `require-skill-review.sh` blocks `git commit` until its
behavioral-equivalence marker is written. Run `/code-review` after Phase
1 lands so its `code-review-claude-config` project-layer P1 item
evaluates the case studies against the amended doc, not the old one.

**Structural sweep** — the primary check, since the collapsing rule is
structural rather than literal. In all three case studies, every table
row labelled with an era, a window, a checkpoint, or a cohort must carry
only rate, share, median, order-statistic, or growth-multiple columns.
Confirm no era-labelled table retains a column headed `Sessions`,
`Branches`, `Nudge fires`, `action=block fires`, `n`, `n (captured)`,
`Total`, `Total $`, `Startup burn`, `Dispatches`, or `Reviewer spawns`,
and that no prose sentence restates a value one of those columns used to
carry.

**Literal sweep** — `git grep -n '47\.5%' -- docs/ .claude/skills/` and
`git grep -n '3\.5%' -- docs/case-studies/handoff-hard-block-position.md
docs/cost-levers-considered.md` must both return nothing. `git grep -n
'n=' -- docs/case-studies/` must return nothing in an era-, window-, or
cohort-labelled context.

**Citation sweep** — `git grep -n 'one permitted split'` must return
hits only under `.claude/plans/` (preserved records).

**Redaction hook** — `deny-private-project-refs.sh` fires on commit and
covers this diff and this plan file.

**Cross-file consistency** — after Phases 2 and 3,
`docs/cost-levers-considered.md:535-544` and `docs/case-studies.md:16`
must state no figure the reworked `handoff-hard-block-position.md` no
longer states, and `docs/handoff-nudge.md:32,132,135` must remain
accurate unchanged.

## Out of scope

- **Retraction or history rewrite.** Forward-fixing only, per G1 and the
  engineer's confirmation already recorded in Context.
- **Re-measurement of any kind.** G3 fixes this: every change is a
  subtraction or a re-selection among figures already published. No
  macOS-only after-era value is computed for Tier 3B, no before-era IQR
  is recovered, and `pr-cost-context-bucket.md`'s cohort comparison is
  not re-run against a public-commit pivot.
- **The remaining `redaction-carveout-followups.md` Phase D gap classes
  that fall outside this plan's four violation classes:**
  `handoff-threshold-impact.md`'s cross-account component at `:75` (a PR
  count over private-project repos, on neither closed list);
  `handoff-hard-block-position.md:74`'s attribution-difference token
  figures (whole-period, single-machine, on neither closed list); and
  whether hook-denial, branch, findings, and nudge-log line counts pooled
  across accounts are permitted count types at all. This plan makes none
  of them worse and resolves none of them.
- **Discharging the new `:364` disclosure duty for already-published
  crossing content.** The duty this plan adds governs what a *new*
  proposal for approval must include going forward — it does not
  retroactively audit content already published before this amendment
  lands, the same principle bar 2's "content predating this carve-out
  does not spend a pivot" already applies to the cap itself. Whether
  `handoff-threshold-impact.md:60`'s nudge-log crossing-share pair
  specifically needs that disclosure discharged is bundled with the
  bullet above's already-open "permitted count types" question, since
  discharging a disclosure for a split whose permissibility is itself
  unresolved is premature. `ciso-reviewer` (`/code-review` pass)
  flagged this gap; the Tier 3 machine-wide Burn-share rows the same
  finding named are unaffected — Cost's dimensionless share-of-spend
  mode is already an established crossing exception, not an open
  question.
- **Mechanical enforcement of any of this.** G2 — the tier is reviewer
  discipline, and the only enforcement added here is the
  `code-review-claude-config` checklist bullet.
- **`.claude/plans/redact-account-root-count.md:153`** — left as a
  preserved record of a decision that was correct when made (row 20).
- **The two `.claude/plans/` files carrying the old heading name in
  quoted or narrative form** (`pooled-measurement-time-series
  -carveout.md:171`, `redaction-split-repeat-cap-amendment.md:14,62,74`)
  — preserved records under Axis 3, and none is a live citation.
