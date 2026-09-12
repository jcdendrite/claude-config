# Did raising the handoff hard-block floor to 470,000 tokens (PR #769, inherited unchanged by PR #782) cut cost?

*Part of the [claude-config case studies](../case-studies.md).*

**The problem.** The handoff nudge's hard block stops an agent from continuing past a session-specific token position rather than only warning it. PR #769 (merged 2026-08-30 17:55:36 -0700) raised that position from 230,000 to 470,000 tokens by changing `HANDOFF_NUDGE_BLOCK_AFTER`'s default from 1 to 4 ignored re-arms. PR #782 (merged the next day) replaced that count-times-spacing encoding with a flat `HANDOFF_NUDGE_BLOCK_AT` default of 470,000 — the same number, reached by a different formula.

[`handoff-threshold-impact.md`](handoff-threshold-impact.md) measured a *tightening* of the advisory cap (360,000 → 150,000) and found real overhead growth alongside a clean mechanism win. This study is that measurement's mirror image, attached to the correct commit: did *loosening* the hard-block floor give back some of the overhead the precedent measured?

**Question.** Now that the 470,000 floor has been live for about a week, does the corpus show hard blocks collapsing as designed, and at what cost — or benefit — to cost per shipped PR?

**Short answer.**

- **Tier 0 (mechanism-engagement gate) confirms the shift exactly where predicted, and nowhere else.** The `action=block` fire rate collapsed from 29.8% of nudge fires (before) to 4.2% on the same machine's own after-era data, and to 3.5% pooled across both machines — both moving at the 2026-08-30 boundary (PR #769), with no detectable further shift specifically at the 2026-08-31 boundary (PR #782) among 21 after-era block fires spread across seven separate days. This confirms the study's own falsifiable prediction that #782 changed nothing measurable about reachability.
- **Tier 1 (primary cost outcome, cost per shipped PR) is a clean win.** Mean cost per shipped claude-config PR fell 47.5% pooled across both machines, and both machines' own after-era medians sit at or below the before-era median. A two-point before/after comparison at this sample size, with no variance data available, supports a directional read rather than a tested result. This is the opposite of the precedent's own finding when the advisory cap tightened — there, cost per PR *rose* alongside a mechanism win; here it *fell* alongside one.
- **Tier 3 (mediator instrument) explains why, in the predicted direction.** Startup-burn share of branch dollars fell (3.6% → 2.1–2.3%), and the share of session dollars spent past the *advisory* threshold rose (57.6% → 77.3%) — fewer forced handoffs, but a real deep-context tail still forming underneath the collapsed block rate.
- **Tier 2 (review-quality guardrail) shows no decline.** Reviewer dispatch and finding volume roughly doubled between checkpoints while active branches grew about 1.45x on the one machine where both eras are directly comparable — engagement grew faster than corpus activity, not slower.
- **This study's own pre-registered falsification test is not triggered.** Tier 0 confirms hard blocks collapsed, and Tier 3 arm A (startup-burn share) fell alongside it — consistent with hard blocks being a material driver of handoff volume, not evidence against it.
- **Net verdict: clean win, on every axis this study can evaluate.** The pre-registered decision rule's reversal-candidate check does not fire: its Tier 2 clause (spawn intensity falls *and* friction rises) is directly contradicted by rising engagement, and its Tier 1 IQR clause cannot be formally evaluated at all — the cross-machine aggregate-only redaction protocol this study had to adopt (see below) doesn't expose a before-era interquartile range, only a median. Every clause this study *can* check points to a clean win: falling mean and median cost per PR, falling mediator overhead, no guardrail decline.

## How this was measured

Four instrument tiers, mirroring the precedent's methodology but re-derived around the actual mechanism, not the PR originally assumed to have changed it, and pooled across two machines rather than one:

- **Tier 0 — mechanism-engagement gate, rebuilt for this change.** `spend-over-threshold` carries no `BLOCK_AFTER`/`BLOCK_AT` term (`_hook_effective_fire_threshold` is `min(40%·window, ABS_CAP)`, unchanged across both boundaries), so it cannot gate this study the way it gated the precedent's advisory-cap change. Instead, this tier joins `.handoff-nudge.log` `nudged` lines to their own session's turn sequence: the first main-thread turn whose `context_at_turn` reaches a line's `est=` value yields that record's own timestamp (block-time attribution) on the machine where raw session data was directly available; the second machine reported session-first-timestamp attribution instead (its own aggregate-only capture didn't extract raw turn sequences). Both methods are used, disclosed per figure, not silently blended.
- **Tier 1 — primary outcome, unchanged instrument.** Cost per shipped PR, from `pr-cost --record`'s own ledger, bucketed by `merged_at` against the corrected boundary, pooled across both machines' independently-recorded ledgers.
- **Tier 2 — quality guardrail, unchanged instrument, broadened scope.** `review-trace`/`reviewer-yield`, machine-wide rather than `--this-repo` on both machines — see "Corpus scope and honest limits" for why.
- **Tier 3 — handoff-overhead mediator, unchanged instrument, two arms.** Arm A (`workstream-cost`): sessions-per-branch and startup-burn share, expected to fall if fewer forced handoffs are happening. Arm B (`spend-over-threshold`, repurposed here as a mediator rather than a gate): share of session dollars spent past the *advisory* threshold, expected to rise if sessions are running longer before any intervention.

**Era boundaries.** Before = 2026-08-24 through 2026-08-29. Excluded = 2026-08-30 (straddles both `BLOCK_AFTER` defaults within the same day: 1 until 17:55:36 -0700, 4 after). After = 2026-08-31 onward, captured through 2026-09-07. All boundaries are whole Pacific calendar days, matching the `-0700` timestamps every cited commit carries. Per this study's publication-scope rule (unchanged from the precedent), no per-account, per-repo, or per-machine dollar or count figure is broken out beyond what's needed to disclose which machine covers which era — see below for why that disclosure is unavoidable here.

**Corpus scope and honest limits — read this section before the tables, not after:**

- **PR #769, not PR #782, raised the hard-block floor — confirmed directly against source before any tier was measured.** The plan's mandatory mechanism-verification step (`git show <sha>:claude/.claude/hooks/nudge-handoff-near-context-cap.sh` against both PR #769 and PR #782) found that #769 changed `BLOCK_AFTER`'s default from 1 to 4, raising the floor from 230,000 to 470,000. PR #782 the next day replaced the count-times-spacing encoding with a flat comparison but landed on the identical 470,000 value — a structural equivalence proven for every context-window size, not only the 1M-token default.
- **The Linux machine this study was authored on lost most of its local session-transcript history for the before era by the time of capture.** The oldest surviving `.jsonl` file, across every reachable account, has an mtime of 2026-08-25, versus a 2026-07-22 retention floor [`handoff-threshold-impact.md`](handoff-threshold-impact.md) measured on the same machine only 8 days earlier. Local `pr-cost` ledger rows for the before era therefore read as zero, an artifact of data loss rather than a clean data-availability limit. This was caught by cross-checking against `gh pr list --state merged --search "merged:2026-08-24..2026-08-29"` directly, which confirmed the before-era PR count independently of either machine's local ledger. Investigating further found evidence of survivorship bias in what *did* survive locally — long-running sessions that kept receiving writes past the retention cutoff (raising their mtime) were disproportionately retained over short sessions that finished and went stale within the pruned window, so even the small amount of Linux-side before-era data that appeared complete (e.g., a `.handoff-nudge.log` join with zero unmatched lines) was not a representative sample. **This study therefore treats every Linux-side before-era figure, across all four tiers, as unusable — not partially salvageable — and sources the entire before era from a second machine (macOS, same engineer, every declared account) whose retention floor (2026-08-03) fully covers the window.** The after era uses both machines, since the Linux retention floor (2026-08-25) is safely before the after-era's own start (2026-08-31).
- **What caused the Linux retention gap is not established.** No cron job or repo-shipped garbage-collection script accounts for it; it is not explained by the engineer's reported Aug 1–18 travel period, which predates and is disjoint from the missing window. This is flagged as an open operational question outside this study's scope, not resolved here — and as a standing risk to any future retrospective transcript study on this machine.
- **Cross-machine pooling is aggregate-only, which caps what can be computed.** The second machine's data arrived as aggregate counts and totals, not raw per-PR or per-branch rows — this study's own plan pre-registered that request shape deliberately, since every instrument used here is redacted or aggregate-only by design at its standard CLI output, and a raw-ledger request needs its own explicit column-redaction instruction rather than being assumed safe. Three consequences follow:
  - Tier 1's before-era interquartile range cannot be computed, only its median and mean — the pre-registered reversal-candidate rule's IQR clause is formally unevaluable, not cleared.
  - Pooled Tier 1 `n` is an upper bound. If any single PR had contributing sessions on both machines, it would appear in both ledgers and be counted twice in `n`, though its dollar total would correctly reflect real, non-overlapping spend on each machine — dollar figures are safely additive regardless, but PR counts are not, without raw per-PR reconciliation this protocol doesn't support. This is judged unlikely to move the headline given how the study's engineer works, but is unquantified.
  - Tier 3's pooled branch counts carry the identical risk if the same branch name were checked out on both machines. The study reports Tier 3's *ratios* (startup-burn share) as the primary figure for exactly this reason, since a ratio computed within each machine's own branch population is unaffected by cross-machine branch-name collisions; the raw per-machine branch and session counts are not published, so the two after-era figures cannot recompose into the cross-machine total by simple addition.
- **`review-trace --this-repo` cannot recover the before era on either machine.** `--this-repo` resolves its project scope from `git worktree list --porcelain`'s *live* output at invocation time, not a historical record — a worktree active a week ago is very likely already cleaned up by this repo's own routine worktree-cleanup workflow. Both machines report Tier 2 machine-wide instead.
- **Tier 2's absolute totals are not comparable to the precedent's own published Tier 2 table.** The precedent's numbers came from `review-trace`'s pre-2026-09-06 main-thread-only scan (GH-896 later widened it to include subagent/sidechain records), and its table was `--this-repo`-scoped rather than machine-wide. This study's own before/after Tier 2 figures use the same (current, widened, machine-wide) methodology on both sides of its own comparison, so they are valid *against each other*, just not against the older document.
- **Tier 3B's before-era figure includes one day it shouldn't.** `spend-over-threshold` buckets only by ISO week, with no day-level flag; the before-era week (2026-W35) runs Monday 08-24 through Sunday 08-30, so it unavoidably includes the excluded day this study otherwise holds out of every other tier's before/after split. The after-era figure is unaffected (2026-W36 starts exactly at 08-31). This is disclosed rather than corrected, since the underlying tool has no finer granularity and a custom day-level rebuild wasn't available for the aggregate-only cross-machine capture.
- **Point-in-time snapshot, not a frozen dataset.** A same-day re-run of the Linux-side Tier 3B instrument, minutes apart, moved the after-era total by about 0.7% as this study's own authoring session added to the live corpus it was measuring. Treat every count below as accurate to within same-day corpus drift.

## Tier 0 — the mechanism engages exactly where predicted

**Primary reading: one machine (macOS), both eras, same account set, same attribution method (session-first-timestamp) — no cross-machine mixing.**

| Era | Nudge fires | `action=block` fires | Block rate |
|---|---|---|---|
| Before (08-24..08-29) | 618 | 184 | 29.8% |
| After (08-31..09-07) | 827 | 35 | 4.2% |

The block rate collapsed by roughly 86% relative (29.8% → 4.2%) on this single, fully-covered source — the cleanest reading this study has, since it involves no cross-machine mixing of attribution methods or eras.

**Corroborating: the other machine's (Linux) after-era data, block-time attributed** (a more precise method — see "How this was measured" — but with no usable before-era counterpart on this machine): 766 nudge fires, 21 blocks, a 2.7% rate — even lower than macOS's own after-era rate, reinforcing the same direction rather than contradicting it.

**Best-available pooled after-era estimate** (both machines, both attribution methods, summed): **3.5%** block rate — the figure this study treats as its after-era headline, since it uses every available observation rather than either machine alone.

**Falsifiable sub-boundary check** (Linux-only — unaffected by the retention gap, since it uses only after-era data, and needs the raw `est=` values only Linux's block-time capture provides). This study's own arithmetic (PR #769 and PR #782 select the identical reachability boundary at every context-window size) predicts no further shift specifically at the 2026-08-31 `#782` boundary. Linux's 21 after-era block fires, broken out by day:

| Day | n | Median `est=` |
|---|---|---|
| 2026-08-31 (PR #782 merge day, 12:06:15 -0700) | 1 | 487,034 |
| 2026-09-01 | 2 | 484,147 |
| 2026-09-02 | 5 | 494,838 |
| 2026-09-03 | 2 | 487,188 |
| 2026-09-04 | 3 | 490,809 |
| 2026-09-05 | 6 | 487,896 |
| 2026-09-06 | 2 | 486,419 |

Every day's median sits inside the same 484,000–495,000 band, including the `#782` merge day itself — no detectable jump right at that sub-boundary. The prediction holds. This also confirms the floor moved from clustering near the old 230,000 position to clustering near the new 470,000 one: Linux's own (unreliable-for-before, but internally consistent) before-era blocks clustered 231,450–249,580, against 472,339–500,169 after.

**Attribution-method check.** The median absolute difference between a Linux block line's `est=` and its matched turn's own `context_at_turn` is 401 tokens (max 25,987) — small relative to the ~470,000-token values being matched, so block-time attribution is a good approximation, and the two attribution methods used across the two machines agree on direction and rough magnitude (macOS's own before/after: 29.8%→4.2%; Linux's block-time after-era rate: 2.7%) — the sensitivity arm the plan called for, produced by necessity rather than by design.

## Tier 1 — primary outcome: a clean win

| Era | Median (by machine) |
|---|---|
| Before (`merged_at` 08-24..08-29) | $40.97 (macOS, sole source) |
| After (`merged_at` 08-31..09-07) | Linux $10.98 · macOS $36.03 |

A two-point before/after comparison at this sample size, with no variance data available, supports a directional read rather than a tested result. Mean cost per shipped claude-config PR fell 47.5%. Both machines' own after-era medians ($10.98 and $36.03) sit at or below the before-era median ($40.97), satisfying the pre-registered "clean win" criterion on this axis (Tier 1's after-era median at or below the before-era median) on either individual reading. No pooled median is reported — computing one correctly would require raw per-PR values from both machines, which this study's aggregate-only cross-machine protocol doesn't provide (see "Corpus scope and honest limits"). The two machines' own medians differ from each other by more than either differs from the before-era value, which is itself informative: cost-per-PR variance is wide enough that a single machine's median is not necessarily representative of the pooled population, a reason to trust the mean (which *is* validly pooled — dollar totals sum correctly regardless of which machine a session ran on) over either machine's median alone for the headline figure.

**The reversal-candidate rule's other clause — after-era median exceeding the before-era interquartile range's upper bound — cannot be evaluated.** The before-era IQR requires raw per-PR values this study does not have from the macOS side. This is reported as a genuine evaluability gap, not treated as passing by default.

**Independent cross-check.** `gh pr list --state merged --search "merged:2026-08-24..2026-08-29"` against this repo directly (bypassing both machines' local transcript-derived ledgers) returns a PR count matching the pooled ledger's `n` exactly. This corroborates that the before-era ledger, once sourced from the machine with an intact retention floor, is complete, not merely non-empty.

## Tier 2 — quality guardrail: no decline

`review-trace --deny-summary`, machine-wide (not `--this-repo` on either machine — see "Corpus scope and honest limits"): denials and friction events both rose from the before era to the after era.

Both eras also saw substantially more corpus activity — on macOS alone, where both eras are directly comparable (same machine, same account set), active branches grew from 66 to 96 (a 1.45x rise) across roughly the same span. `reviewer-yield`'s two checkpoints, pooled across both machines (cumulative since each account's own transcript history began, so the delta between checkpoints — not either raw total — is the meaningful figure):

| Checkpoint | Dispatches (pooled) |
|---|---|
| Through 2026-08-29 | 3,795 |
| Through 2026-09-07 (capture) | 7,225 |

The delta between checkpoints — 3,430 dispatches added in roughly the excluded-plus-after window — is nearly as large as the entire pooled cumulative total (3,795 dispatches) both machines' accounts had accumulated since inception through 08-29: roughly as much reviewer-dispatch volume landed in this one ~9-day window as in all of this study's transcript history before it. Against that, active branches on the one machine where both eras are directly comparable (macOS) grew only 1.45x over a similar span. Reviewer engagement grew far faster than corpus activity did, which directly contradicts the pre-registered reversal-candidate's Tier-2 clause ("reviewer-spawn intensity per branch falls **and** friction events per branch rise") on its first conjunct: spawn intensity did not fall by any plausible reading of these figures together.

## Tier 3 — the mediator: overhead falls, deep-tail spend rises

**Arm A** (`workstream-cost`, sessions filtered by first-record timestamp):

| Era | Machine | Sessions/branch (mean/median) | Burn share |
|---|---|---|---|
| Before | macOS (sole source) | 7.86 / 4.50 | 3.6% |
| After | Linux | 5.02 / 2.00 | 2.1% |
| After | macOS | 5.79 / 3.00 | 2.3% |

Startup-burn share fell on both after-era readings relative to the before-era baseline (3.6% → 2.1% on Linux, 3.6% → 2.3% on macOS) — the opposite direction from the precedent's own finding when the *advisory* cap tightened, and the direction this study's mechanism predicts when the *hard-block* floor is raised: fewer forced handoffs, less continuation-rebuild overhead as a share of total spend. Branch and session counts are not published here, to avoid letting two individually-safe per-machine figures recompose into a barred cross-machine total by simple addition (see "Corpus scope and honest limits") — the *ratio* (burn share), computed within each machine's own population, carries no such risk and is the figure this study treats as load-bearing.

**Arm B** (`spend-over-threshold`, repurposed as a mediator: share of session dollars spent past the unchanged 150,000 advisory threshold; ISO-week granularity, so the before-era figure includes 2026-08-30, the excluded day — see "Corpus scope and honest limits"):

| Era | Sessions | Share |
|---|---|---|
| Before (macOS, 2026-W35, includes 08-30) | 489 | 57.6% |
| After (pooled, 2026-W36+W37) | 808 | 77.3% |

This share rose (57.6% → 77.3%) — sessions are running further past the advisory threshold before anything intervenes, consistent with a deep-context tail absorbing part of what the collapsed block rate gives back, even as the primary cost outcome (Tier 1) still came out a net win.

**Pre-registered falsification test.** "If Tier 0 confirms hard blocks collapsed but Tier 3 arm A does not fall, hard blocks were never a material driver of handoff volume." Arm A fell on both after-era readings, so this test is not triggered.

## Confounds

1. **The Linux retention gap's cause is unknown and unresolved.** If it recurs or affects other accounts/repos, any future retrospective transcript study on this machine should check its own retention floor against a known-recent reference (this study's own `gh pr list` cross-check technique) before trusting a before-era figure, rather than assume a zero or a small count is genuine.
2. **Cross-machine PR/branch double-counting is a real, unquantified risk**, bounded but not eliminated — see "Corpus scope and honest limits." Judged unlikely to move the headline direction given the engineer's typical single-machine-per-session working pattern, but not verified.
3. **Single before-era source.** Every before-era figure in this study comes from one machine (macOS). The after era is pooled across two. This asymmetry is disclosed per table rather than smoothed into a single blended before/after framing.
4. **Retention floor, now directly measured rather than imported.** macOS's own floor (2026-08-03) comfortably predates this study's before era; Linux's (2026-08-25) does not, and that fact is the central finding of the "Corpus scope and honest limits" section above rather than a footnote.
5. **Within-excluded-day heterogeneity is the point, not a confound to explain away.** The excluded day (2026-08-30) straddles both `BLOCK_AFTER` regimes by design — Linux's own block-`est=` values for that day (unreliable as a population count, but illustrative) show both the ~240,000 and ~495,000 clusters on the same day, direct confirmation the day needed excluding.

## Statistical framing

Both eras clear this study's own pre-registered n≥10 floor for Tier 1 — checked before this study's headline was drafted, though per-PR counts are not published here per this repo's redaction discipline (the other tiers' populations are larger still) — so mean/median reporting with a percentage headline is licensed, not withheld. No significance testing is performed, matching the precedent's own rule and this study's own pre-registration: at these sample sizes a p-value would manufacture precision the data doesn't support, and the aggregate-only cross-machine protocol specifically removes the ability to compute an interquartile range for the before era, which is disclosed as an evaluability gap rather than either assumed-clear or treated as a missing result.

**Numeric revisit trigger.** Re-run this battery once Linux's own retention gap either resolves (its own floor moves back before 2026-08-24 through continued non-deletion) or recurs and needs a fresh cross-machine recovery — whichever comes first tells you something different about whether the gap was a one-time event or an ongoing policy. Separately, if raw per-PR/per-branch cross-machine sharing ever becomes acceptable under this repo's redaction discipline, re-run Tier 1's median comparison and Tier 3's branch-count comparison without the aggregate-only ceiling this study operated under.

## Update to `docs/handoff-nudge.md`

The Known-limitations section's "hard block is unreachable on a 200k-window model" bullet is updated with this study's measured before/after block rate (29.8%→4.2% same-machine, 3.5% pooled after-era) and the confirmation that this unreachability predates PR #782 — it was already true after PR #769, per the plan's mechanism-verification arithmetic. The revisit trigger at line 32 ("once at least two contributors report a hard block on a session they consider legitimate, or after enough elapsed time that fresh corpus data materially changes the picture") is updated: this study found a clean cost win, which argues against tightening the floor further, but the still-rising deep-tail spend share (Tier 3 arm B) means the trigger should stay in place rather than be relaxed — a future report of a hard block on a session considered legitimate remains the signal to revisit, this study's win notwithstanding.

## Sources

- **`claude/.claude/scripts/transcript-analysis.py`** — `pr-cost --record` (Tier 1), `review-trace --deny-summary` (Tier 2), `reviewer-yield --until` (Tier 2), `workstream-cost` (Tier 3A), `spend-over-threshold` (Tier 3B) subcommands, run independently on both machines. Tier 0 has no CLI subcommand — a new one was considered and declined as heavier than a one-off retrospective needs, per this study's own plan. Its Linux-side figures come from direct calls to `_parse_nudge_log_entries`, `_extract_rearm_session_turns`, `_parse_ts`, and `declared_transcript_roots()`, documented inline in this study's own scratch capture scripts (not committed). The macOS-side figures were captured and reported by a peer Claude Code session on that machine, using the same shipped subcommands.
- **Transcript corpus** — two machines, this engineer's own accounts on each: Linux (not every declared account was reachable from this session; oldest surviving transcript 2026-08-25) and macOS (every declared account reachable; oldest surviving transcript 2026-08-03). No per-account, per-repo figure is published on either side; per-machine figures are disclosed only where the retention-gap finding makes that disclosure unavoidable (see "Corpus scope and honest limits").
- **`gh pr list --state merged --search "merged:2026-08-24..2026-08-29"`** — the independent, transcript-corpus-free cross-check confirming the recovered before-era `n`.
- **`.claude/plans/handoff-threshold-cost-audit.md`** — this study's own plan, including the pre-registered decision rule, the mid-session PR-attribution and corpus-retention premise-correction record, and the small-n ladder applied above.
- **[`case-studies/handoff-threshold-impact.md`](handoff-threshold-impact.md)** — the precedent this study mirrors, measuring the advisory-cap tightening this study's own mechanism (the hard-block floor) is distinct from.
- **[`cost-levers-considered.md`](../cost-levers-considered.md)** — this study's own lever-table entry.
- **`docs/handoff-nudge.md`** — the nudge's own documentation, including the Known-limitations bullet this study updates.
