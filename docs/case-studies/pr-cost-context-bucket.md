# PR cost by context bucket

What a merged PR costs, in dollars and tokens, against the session context
size it was built at. Companion to
`.claude/plans/handoff-nudge-cap-recalibration.md`, which grounds
`HANDOFF_NUDGE_ABS_CAP`'s 150000 default in this measurement.

## Why a PR-bucketed measurement was needed

A prior measurement (`handoff-boundary-decision-rule.md`) priced 396
transcripts by turn-index and found cost-per-output-token U-shaped, cheapest
at turn-index 40–80 (mean context 136k). That curve buckets by *turn*, not by
*delivered work*, so it can't answer "what does it cost to ship one PR" — the
question that actually motivates the cap. `pr-cost --record` (shipped, never
previously run on this machine) exists precisely to answer that question; this
document is its first real output.

## Method

`pr-cost --record` walked every local branch with recoverable session-
transcript activity and wrote one row per PR to `pr-cost-ledger.tsv`: dollar
cost by token class (cache read, both cache-write tiers, output, input),
token counts, and `mean_context_at_turn` — the mean context size across the
PR's turns. Rows were bucketed by `mean_context_at_turn` at the same edges
the turn-index curve uses (<100k, 100–150k, 150–200k, 200–250k, 250–300k,
300k+), and `$/PR` computed as `sum(cache_read_usd, cache_write_5m_usd,
cache_write_1h_usd, output_usd, input_usd)` averaged per row in the bucket.

**Coverage check:** `gh pr list --state merged --search
"merged:2026-05-20..2026-08-18"` returns 332 merged PRs in the ledger's own
date range, against 145 captured rows (44%). The gap is expected, not a
defect — `pr-cost` only captures a PR whose branch still has local
session-transcript activity, and the local corpus is on a rolling deletion
window; most of the shortfall is already-aged-out transcripts.

## Results: cost by context bucket

145 rows, repo `claude-config`, PRs #278–#698, merged 2026-05-20 to
2026-08-18:

| Bucket | n (PRs) | $/PR | $/1k output tokens | Mean context |
|---|---|---|---|---|
| <100k | 1 | 30.60 | 0.0712 | 88,801 |
| 100–150k | 23 | 19.02 | 0.0915 | 136,158 |
| 150–200k | 55 | 36.63 | 0.0979 | 176,363 |
| 200–250k | 32 | 51.84 | 0.1134 | 223,583 |
| 250–300k | 19 | 50.44 | 0.1345 | 270,696 |
| 300k+ | 15 | 58.29 | 0.1515 | 377,534 |

100–150k is the cheapest bucket with a trustworthy sample (n=23): both cost
measures rise monotonically from there through every larger bucket. `<100k`'s
apparent lower `$/PR` is n=1 — not trustworthy, and does not move the
conclusion. This corroborates the turn-index curve's cheapest point (mean
context 136k) with an independent, PR-bucketed measurement, and set the
`HANDOFF_NUDGE_ABS_CAP` retune to 150000 — the cheapest bucket's upper edge.

## The engineer's side question: did keeping context low already help?

The engineer independently reported having kept context deliberately small
across recent sessions, before this measurement existed to check it against.
Splitting the same 145-row ledger by `merged_at` into the last 3 days present
in the data (2026-08-16 through 2026-08-18, n=37) against everything before
(n=108):

| Cohort | n | Mean $/PR | Median $/PR | Mean context |
|---|---|---|---|---|
| Last 3 days (08-16–08-18) | 37 | 40.78 | 33.56 | 185,437 |
| Everything before | 108 | 41.35 | 30.65 | 222,409 |

A 5-day window (08-14–08-18, n=47 vs. n=98) shows the same shape: mean
context 194,230 vs. 221,964, mean `$/PR` 43.46 vs. 40.12.

**Mean context per session dropped ~17% in the recent cohort — the effort is
real and measurable. `$/PR` itself did not fall; by the median, it rose
slightly.** The two don't move together because `$/PR` is dominated by how
much output a PR requires, not only by the per-token rate: a cheaper
per-token rate lowers cost for the *same* amount of work, but doesn't lower
the amount of work a given PR needs. Keeping context low is necessary for
cheaper tokens but not sufficient for a cheaper PR — the bucket-cost curve
above and this cohort split are answering two different questions.

## Limits of this result

- Bucket sample sizes range from n=1 (`<100k`, discounted) to n=55
  (`150–200k`); only the 100–150k/150–200k comparison this document's
  conclusion rests on has a trustworthy sample on both sides.
- 44% ledger coverage of merged PRs in-window means the population is
  whatever branches still had local transcript activity at capture time, not
  a random sample of all merged PRs — a survivorship bias in the direction of
  more-recent and longer-lived branches.
- The recent-cohort comparison uses calendar-day cutoffs on `merged_at`, not
  a controlled before/after split on when the low-context effort actually
  started; day-level `$/PR` is itself noisy (single-PR-cost outliers move a
  37-PR mean by several dollars), so this is a directional read, not a
  significance-tested one.
- Single-machine, single-repo corpus (`claude-config`). Not validated against
  any other repo or account.
