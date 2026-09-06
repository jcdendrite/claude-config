# Did dropping the handoff-nudge cap from 360,000 to 150,000 actually help?

*Part of the [claude-config case studies](../case-studies.md).*

**The problem.** `handoff-nudge-cap-recalibration.md` (2026-08-22, see [`cost-levers-considered.md`](../cost-levers-considered.md)) retuned the handoff nudge's absolute token cap from 360,000 to 150,000. The retune was grounded in a cost-per-`mean_context_at_turn`-bucket analysis of a 145-row ledger. The 100–150k bucket was the cheapest bucket with a trustworthy sample, and cost rose monotonically through every larger bucket. That measurement was a snapshot at one point in time, bucketed by context level, not a before/after comparison of the retune's own effect once it shipped. This study is that comparison.

**Question.** Now that the 150,000 cap has been live for about a week, does the corpus show it doing what it was designed to do — and at what cost?

**Short answer.** Four instrument tiers, one verdict:

- **Tier 0 (mechanism-engagement gate) clears cleanly and robustly.** The share of session dollars spent after a session has already crossed its fire threshold dropped from 89.5% to 58.3%, and the direction holds at every absolute-cap value tested from 100,000 to 360,000 — no sign flip.
- **Tier 1 (primary cost outcome, cost per shipped PR) does not show a clean win.** Mean cost per claude-config PR rose from $41.89 to $51.87, and the upper quartile widened.
- **This study's own pre-registered "overhead dominance" rule is not triggered.** The after-era median ($31.63) stays inside the before-era's interquartile range.
- **Tier 3 (mediator instrument) explains why.** Sessions-per-branch and continuation "startup burn" as a share of total dollars both rose monotonically across the transition, consistent with more handoffs happening under the lower cap.
- **Tier 2 (review-quality guardrails) shows no decline.**
- **Net verdict.** The gate got tighter and the overhead grew with it — a real trade, not a free win, and not a regression either.

## How this was measured

Four instrument tiers, each answering a different question, deliberately not collapsed into one headline:

- **Tier 0 — mechanism-engagement gate.** `spend-over-threshold`: the share of a session's dollars spent once its own context has already crossed the nudge's fire threshold. This is *expected to improve by construction* whenever the cap drops — a lower cap makes "already past threshold" true earlier in more sessions — so it carries no cost verdict on its own. It answers "is the gate actually engaging more," not "did this save money."
- **Tier 1 — primary outcome.** Total cost per shipped PR, dollars primary, tokens decomposed by class. Two components: a `claude-config`-only component (this repo's own `pr-cost` ledger, its historical unscoped basis) and a cross-account component (the same mechanism run against every other repo this machine's six Claude Code accounts have session activity in, `--record --all-accounts --this-repo`, folded into one aggregate — never broken out per account or repo, see Publication scope below).
- **Tier 2 — quality guardrail.** Review rounds, hook-denial friction, and reviewer-agent findings, `--this-repo`. A quality *loss* between eras would be a stop condition for any cost claim; a quality *gain* is not itself a win condition, since this tier guards Tier 1, it doesn't compete with it.
- **Tier 3 — handoff-overhead mediator.** Sessions-per-branch and continuation "startup burn" (a continuation session's own first 5 main-thread turns' dollars — reusing the repo's existing first ramp-curve turn-index bucket boundary, not a new cutoff) as a share of branch dollars. This is what explains the *sign* of Tier 1: a branch with more sessions may mean more genuine handoffs, or just more days of unrelated work touching it — the instrument measures session/branch shape, not a literal handoff count.

**Era boundaries.** Before era: sessions/PRs dated on or before 2026-08-16. Excluded/transition window: 2026-08-17 through 2026-08-22 (the week the retune actually shipped and stabilized — held out of the primary comparison, reported separately as a falsification check). After era: 2026-08-23 onward. All figures below are machine-wide across this machine's six configured Claude Code accounts unless labeled "claude-config-only"; per this study's publication-scope rule, no per-account, per-repo, or per-container figure is ever broken out — only the aggregate.

**Corpus scope and honest limits:**

- **Point-in-time snapshot, not a frozen dataset.** Every figure here was captured over 2026-08-28 to 2026-08-29 against a live, continuously-accruing transcript corpus — including this study's own authoring sessions. A `workstream-cost` corpus-stability re-run during this same authoring window (default scope) moved from 391 to 393 branches and $21,323.47 to $21,352.67 total branch dollars between two runs a few hours apart; the startup-burn share held exactly at 2.3% both times. Treat every count below as accurate to within same-day corpus drift, not exact to the decimal.
- **`--until` windows don't compose with a lower bound.** `reviewer-yield` has no `--since` — its three checkpoint runs (through 2026-08-16, through 2026-08-22, unbounded) are each cumulative from this repo's transcript history, not scoped to a single era. Era-scoped findings are recovered only by differencing adjacent checkpoints (see Tier 2 below); the checkpoint closest to a genuine "before" figure is itself a since-inception cumulative total, not an isolated before-era count.
- **Boundary-spanning sessions exist but don't corrupt the Tier 0 figures used here.** 15 of 1,625 machine-wide sessions (0.9%) have turns on both sides of the 2026-08-23T00:00:00Z boundary. `spend-over-threshold` (Tier 0's instrument) buckets a whole session by its first timestamp, so each spanning session lands entirely in one era rather than being split — a conservative simplification, not a double-count, and at 0.9% of sessions it does not move any figure below by a meaningful amount.
- **Untimestamped reviewer dispatches: zero, both windows.** Every reviewer-dispatch tool-use record in the corpus (roughly 2,076 before the meta.json-pairing filter `reviewer-yield` itself applies) carries a parseable timestamp, at both the through-08-16 and through-08-22 checkpoints — this is a clean result, not a caveat.
- **Date-level, not turn-level, granularity mismatch.** Every era boundary above is a whole calendar day; a session's own turns are timestamped to the second. Sub-day placement error is possible for sessions active exactly at a boundary, bounded by the boundary-spanning finding above.
- **The after-era merged-PR population is a moving target.** claude-config merged 28 PRs in the after era as of the 2026-08-28 capture that grounded the retune decision; 34 as of this write-up (2026-08-29) — real organic growth from an actively-merging repo, not a discrepancy. The Tier 1 claude-config-only figures below use a *captured* denominator (26 after-era rows, 160 before-era rows) that is smaller than either population count, since `pr-cost`'s 3-day as-of window deliberately holds back very recently merged PRs whose spend attribution may still be incomplete.

## Tier 0 — the gate engages more, robustly

| | Sessions | Above-threshold $ | Total $ | Share |
|---|---|---|---|---|
| Before (weeks ≤ 2026-W33) | 457 | $7,280.17 | $8,131.97 | 89.5% |
| After (≥2026-08-23) | 494 | $1,168.31 | $1,998.90 | 58.4% |
| Excluded window (2026-08-17..08-22, transition) | 620 | $2,486.83 | $2,988.27 | 83.2% |
| Sensitivity: immediately-prior window (2026-08-08..08-16, approximated by the two full ISO weeks spanning it) | 367 | $6,391.75 | $7,091.30 | 90.1% |

The transition window's share (83.2%) sits between the before and after figures, as expected for a week the corpus was still settling into the new cap. The sensitivity arm — using the two weeks immediately preceding the before era's own end, a narrower and more recent comparison than the full four-regime before era (see Confounds below) — shows the same before-era-high, after-era-low pattern (90.1% vs. 58.4%), so the improvement is not an artifact of averaging across the before era's earlier, differently-configured weeks.

**Threshold-value sensitivity: recomputed at 100,000 / 150,000 / 250,000 / 360,000, no sign flip.** `spend-over-threshold` has no CLI flag for the absolute cap by design (a flag would invite quietly retuning it through this tool rather than through a plan), so this recomputation monkeypatches the module constant directly, live against the current corpus:

| Cap | Before share | After share | Δ |
|---|---|---|---|
| 100,000 | 95.5% (n=457) | 80.1% (n=504) | −15.4pp |
| 150,000 (current) | 89.5% (n=457) | 58.3% (n=504) | −31.2pp |
| 250,000 | 73.5% (n=457) | 13.9% (n=504) | −59.6pp |
| 360,000 (prior default) | 50.7% (n=457) | 2.2% (n=504) | −48.5pp |

The after era's share is lower than the before era's at every cap tested — the direction is not sensitive to the specific cap value, only its magnitude is. (Session counts here differ slightly from the table above because this recomputation re-scanned the live corpus a session later than the earlier capture; same-day drift, not a discrepancy.)

**Hard blocks are new, not just more frequent.** Joining `.handoff-nudge.log` lines (which carry no timestamp field) against each session's own first-record timestamp, machine-wide across all configured accounts' logs (where present):

| Era | Nudged lines | `action=block` | `handoff` conversions |
|---|---|---|---|
| Before | 359 | 0 | 56 |
| Excluded | 328 | 0 | 134 |
| After | 1,118 | 185 | 436 |

Zero hard blocks occurred in the before or excluded eras. 185 did in the after era. This is not a separate co-intervention landing at the same boundary. The escalating-band re-arm mechanism (PR #622) merged 2026-08-11, well inside the before era, so it was already live throughout. The more direct explanation is mechanical: a lower cap means a session re-arms more times per unit of context growth, so the same total session length crosses the block-after-N-nudges escalation threshold far more often. That prediction is confirmed directly: the fraction of sessions whose peak context crosses the fire threshold at all rose from 53.1% (n=367, the same immediately-prior sensitivity window, evaluated at its own real governing cap of 360,000) to 89.7% (n=505, after era, at its own real cap of 150,000). That is a 1.69x increase. This is the retune's own dismissal-as-noise risk materializing, not a separate effect. See the `docs/handoff-nudge.md` update below.

## Tier 1 — primary outcome: cost per shipped PR

**claude-config-only component** (this repo's own `pr-cost` ledger, captured denominators — see the after-era population note above):

| | n (captured) | Total | Mean | Median | IQR |
|---|---|---|---|---|---|
| Before (`merged_at` < 2026-08-23) | 160 | $6,702.01 | $41.89 | $30.79 | [$12.83, $56.04] |
| After (`merged_at` ≥ 2026-08-23) | 26 | $1,348.55 | $51.87 | $31.63 | [$20.44, $89.55] |

**This study's own pre-registered decision rule:** if the after-era median exceeded the before-era IQR's upper bound while Tier 0 improved, this report would state the result as overhead dominance and name the 150,000 cap as a candidate for reversal. It does not — $31.63 sits well inside the before-era's IQR — so that framing is not triggered. The honest reading of the same numbers: the median barely moved (+$0.84), but the mean rose 24% and the upper quartile widened by 60% ($56.04 → $89.55). A minority of after-era PRs are costing noticeably more, not the typical one.

Token decomposition (mechanism detail, not the decision variable): after-era cache-read tokens were 3.5B against 18.8B before-era — the after-era count captures roughly 1/6th as many PRs, so this is not a rate comparison, only a scale note for the raw totals: cache_write_5m 58.2M (after) / 253.6M (before), cache_write_1h 65.3M / 229.9M, output 17.8M / 62.2M, input 59.5K / 902.0K.

**Cross-account component.** The same `pr-cost --record` mechanism run once per repo this machine's accounts have session activity in outside claude-config, folded into a single aggregate (never broken out per account or repo — see Publication scope below): **92 PRs post-dedupe, $5,073.98 total, $408.57 Opus dollars (8.1% of total).** Deduping applied the union rule to duplicate `(host, repo, PR number)` keys found across the corpus's ledgers — summing corpus-derived columns and taking `gh`-sourced columns from a single row, rather than naively double-counting. This component is a single capture-time aggregate, not before/after split — the target repos don't carry the same clean `merged_at`-relative-to-the-boundary framing claude-config's own ledger does, and this study does not attempt to force one.

## Tier 2 — quality guardrail: no decline

`review-trace --this-repo`, date-bounded per era. The before window is 15 days (2026-08-02–08-16), the after window 7 days (2026-08-23–08-29) — unequal lengths, so raw totals aren't directly comparable; per-branch rates are:

| | Branches | `code-review` | `ready-for-review` | `plan-review` | Reviewer spawns | Spawns/branch |
|---|---|---|---|---|---|---|
| Before window | 125 | 77 | 40 | 66 | 1,090 | 8.72 |
| After window | 41 | 108 | 50 | 33 | 408 | 9.95 |

Reviewer-spawn intensity per branch rose (8.72 → 9.95, roughly +14%) rather than holding flat — more scrutiny per branch under the new cap, not less, which is still evidence against a quality decline. Hook-denial friction also held its shape. Before-window denials totaled 842: worktree-enforcement 269, plan-review-routing 146, redaction 125, marker.sh 109, plan-review 69, code-review 47, and eight smaller categories. After-window denials totaled 368 over roughly half the calendar span: worktree-enforcement 181, plan-review 39, redaction 35, marker.sh 32, code-review 21, and seven smaller categories. The same gates dominate in the same rank order in both windows. User-facing friction (rejections, auto-mode blocks, interruptions) fell from 59 events in the before window to 24 in the after window.

**Not comparable to a post-2026-09-06 `review-trace` run.** The table and denial/friction totals above were measured under `review-trace`'s pre-2026-09-06 main-thread-only scan, which excluded subagent (sidechain) transcript records.

**Findings.** `reviewer-yield --this-repo` has no lower-bound date flag, so its three checkpoints are each cumulative since this repo's transcript history began, not era-isolated:

- Through 2026-08-16: 954 dispatches / 638 findings.
- Through 2026-08-22: 1,488 / 1,280.
- Unbounded (as of this capture): 1,882 / 1,934.

Differencing adjacent checkpoints recovers each window's own contribution. The excluded window (08-17–08-22) added 534 dispatches and 642 findings. The after window (08-23 onward, as of capture) added 394 dispatches and 654 findings. Findings-per-dispatch, both derived windows, stay above 1 (more than one finding per dispatch on average). That's consistent with the before-window's own cumulative rate (638/954 ≈ 0.67), which is undercounted only because the cumulative denominator includes many older, lower-yield dispatches the differenced windows don't. No quality-loss stop condition is triggered.

## Tier 3 — the mediator: overhead grew, monotonically, with the cap drop

`workstream-cost` has no `--since`/`--until` of its own; this study filtered its input by each session's own first-record timestamp before calling the same per-branch aggregation the CLI uses, machine-wide and claude-config-only:

| | Branches | Sessions/branch (mean) | Sessions/branch (median) | Startup burn | Total $ | Burn share |
|---|---|---|---|---|---|---|
| **Machine-wide** — before | 239 | 3.46 | 1.0 | $205.76 | $11,910.68 | 1.7% |
| Machine-wide — excluded | 114 | 6.60 | 2.0 | $115.19 | $4,897.15 | 2.4% |
| Machine-wide — after | 79 | 7.39 | 4.0 | $155.24 | $4,576.55 | 3.4% |
| **claude-config-only** — before | 129 | 2.63 | 1.0 | $85.32 | $4,848.31 | 1.8% |
| claude-config-only — excluded | 58 | 3.62 | 2.0 | $51.21 | $2,532.48 | 2.0% |
| claude-config-only — after | 41 | 5.85 | 4.0 | $66.08 | $2,045.93 | 3.2% |

Every one of these six numbers moves the same direction across the three eras: more sessions per branch, and a larger share of branch dollars spent on continuation startup burn. This is exactly the causal story Tier 1's widened upper quartile is consistent with — more of the after era's work involves picking a branch back up in a fresh session, and each pickup pays a real, non-zero startup cost. A branch touched by more sessions is not necessarily more handoffs specifically (it could be unrelated multi-day work on the same branch — this instrument measures shape, not a handoff count, by design), but the monotonic, same-direction movement across all three eras and both scopes is a stronger signal than a two-point before/after comparison alone would be.

**Abandoned-branch candidates.** Across claude-config and the eight cross-account target repos combined (raw last-activity ages, not era-split — this is a point-in-time snapshot, not a per-era measurement), 74 branches have no PR match at all (merged or closed-unmerged); 48 of those clear the 3-day as-of-window threshold this repo already uses elsewhere as its "recent activity" cutoff (reused deliberately rather than inventing a new number). This is reported as raw values per this study's own statistical-framing rule, not as a hard abandoned/active classification — a branch with 3-day-old activity and no PR may simply be open, unrelated work.

## Confounds

In the order this study committed to naming them:

1. **Co-intervention, the dominant threat.** The escalating-band hard-block re-arm mechanism (PR #622) was already live throughout the before era (merged 2026-08-11), so it is not a confound for the *presence* of hard blocks — but the interaction between a lower cap and that pre-existing escalation ladder is real and is exactly what the Tier 0 hard-block finding above measures, not something this study can cleanly separate from "the cap alone."
2. **Retention floor, measured not assumed.** The oldest surviving transcript on this machine, across all six accounts, is 2026-07-22 — well before this study's own before-era start (2026-08-02), so no era boundary here is close enough to the retention floor to risk silent truncation.
3. **As-of window, held uniform.** The same 3-day as-of window governs every account and every repo the cross-account component touches; the after era is not flattered by early capture from whichever account happens to merge fastest.
4. **Within-era heterogeneity.** The before era spans four distinct threshold regimes (flat 120,000 → 60% of window → 40% of window → 40%∧360,000), so it is a *no-hard-block* baseline, not specifically a *360,000-cap* baseline. The sensitivity arm above (2026-08-08..08-16) is the narrower, truly-immediately-prior comparison, and shows the same direction as the full before era.
5. **One-day granularity mismatch**, discussed above.
6. **Volume is not comparable across eras** — only per-PR, per-session, or per-priced-turn normalizations are, which is why every table above reports a rate or a ratio alongside the raw count, never a bare total standing alone as a comparison.
7. **Tier 3 approximates handoff overhead, it does not count it.** Session/branch shape is a permanent measurement-design limit here, not a gap a future code change to this study's own instruments could close; a branch touched by 3 sessions may be 3 genuine handoffs or 3 days of unrelated normal work, and this instrument cannot tell the two apart.

## Statistical framing

Every ratio above prints its own numerator and denominator, and every bucket clears both this study's minimum-denominator floor (a bucket denominator under 10 is reported as raw values plus a point delta, labelled directional-not-decisive, with no percentage headline) and the ≥2-contributor aggregation floor. No significance testing is performed anywhere in this study — at the sample sizes this corpus produces (26 captured after-era claude-config PRs is the smallest primary-outcome bucket), a p-value or confidence interval would manufacture precision the data doesn't support. "Inconclusive" is treated as an acceptable, stated outcome, and Tier 1's own result above is reported as exactly that: not overhead dominance, not a clean win, a real cost shift concentrated in the upper quartile.

**Numeric revisit trigger.** Re-run this battery once claude-config's after-era merged-PR population reaches 160 — matching the before era's own captured sample size, so the two Tier 1 medians and IQRs are compared at comparable statistical power rather than 26-against-160. As of this writing the after-era population is 34 (2026-08-29).

## Update to `docs/handoff-nudge.md`

The Known-limitations section's session-share risk bullet cited the 1.25x–4x figures from the earlier flat-percent-to-`min(pct, absolute_cap)` transition (`absolute-token-handoff-threshold.md`) and flagged the 360,000→150,000 drop as unmeasured. This study measures it: session-share whose peak context crosses the fire threshold rose from 53.1% to 89.7% (a 1.69x increase) between the immediately-prior window (at its own real cap of 360,000) and the after era (at its own real cap of 150,000) — see Tier 0 above. The bullet is updated in place with this figure and a link to this case study.

## Sources

- **`claude/.claude/scripts/transcript-analysis.py`** — `spend-over-threshold` (Tier 0), `pr-cost --record` (Tier 1), `review-trace` (Tier 2), `reviewer-yield --until` (Tier 2, added by this study's plan), `workstream-cost` (Tier 3, added by this study's plan) subcommands. All aggregate figures above come from these subcommands or from direct calls to the same internal functions they call (`_extract_rearm_session_turns`, `_compute_workstream_dollars`), documented inline in this study's own capture scripts.
- **Transcript corpus** — this machine's six configured Claude Code accounts' local session history, 2026-07-22 (oldest surviving transcript) through 2026-08-29 (capture date). Machine-wide figures are aggregated across all six; no per-account, per-repo, or per-container figure is published (this study's own publication-scope rule).
- **`~/.claude/.handoff-nudge.log`** (and the equivalent per-account log where present) — `nudged`/`handoff` line counts, joined to session first-timestamps for era attribution since the log itself carries no timestamp field.
- **`.claude/plans/handoff-threshold-impact-analysis.md`** — this study's own plan, including the pre-registered decision rule and statistical-framing commitments quoted above.
- **[`cost-levers-considered.md`](../cost-levers-considered.md)**, "handoff-nudge-cap-recalibration.md" entry — the original retune this study evaluates.
- **`docs/handoff-nudge.md`** — the nudge's own documentation, including the Known-limitations bullet this study updates.
