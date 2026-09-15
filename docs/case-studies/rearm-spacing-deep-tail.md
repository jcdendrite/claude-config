# Does a deeper context tail need a wider `REARM_SPACING`? Measured, and why 80,000 holds

*Part of the [claude-config case studies](../case-studies.md).*

**The problem.** [`handoff-hard-block-position.md`](handoff-hard-block-position.md)
found that raising the hard-block floor to 470,000 tokens cut cost per
shipped PR. That study's own mediator instrument also found sessions
running further past the *advisory* threshold before anything
intervenes — a deep-context tail forming underneath the collapsed block
rate. The nudge's re-arm spacing (`REARM_SPACING`, currently 80,000
tokens — how far apart repeated nudges fire once the advisory threshold
is crossed) was tuned before that tail existed. A deeper tail plausibly
lengthens how long an operator takes to notice and respond to a nudge,
which is exactly the quantity 80,000 was calibrated against.

**Question.** Now that the tail is measurably deeper, does a re-test on
fresh data still support 80,000, or does a wider spacing pencil out —
either on cost or on the dismissal-risk margin the original value was
chosen for?

**Short answer: keep 80,000.** Two independent, pre-registered gates were
run against the same corpus, and neither recommends a change — for two
different reasons.

- **Cost gate: no candidate wins.** Under the realistic compliance model,
  no wider or narrower spacing's dollar saving over 80,000 cleared the
  $50 materiality floor this register already applies elsewhere — see
  the plan for the exact per-candidate figures.
- **Dismissal-risk gate: the margin eroded, but the fix is worse than the
  problem.** 80,000 was originally chosen because it was roughly 2.6x the
  median operator-response lag measured in an earlier design study. This
  run's fresh measurement of that same lag came back at 107,262 tokens.
  80,000 no longer clears its own 2x design margin against that figure.
  Restoring a 2x margin would require a spacing at or above 214,524 —
  above the 120,000 ceiling the gate's tie-break sets. That ceiling
  exists because a 200k-context model fires its nudge at 80,000. Any
  spacing above 120,000 pushes the re-arm point outside that window
  entirely. That would silently turn the repeating nudge into a one-shot
  warning for that whole model class. This is the same failure shape as
  the already-documented "hard block is unreachable on a 200k-window
  model" limitation. No value under the ceiling restores the margin, so
  the pre-registered tie-break keeps 80,000 and records the erosion as a
  documented limitation instead of acting on it.

## How this was measured

`rearm-backtest` replays real session data against candidate spacings and
reports, per candidate, dollar cost under two compliance models
(`perfect` and `realistic`) and mean context depth (`C_bar`). One run, on
this machine, against the last 14 days:

```
python3 <branch-worktree>/claude/.claude/scripts/transcript-analysis.py rearm-backtest \
  --this-repo --since 14d --spacings 40000,80000,120000,160000
```

274 sessions in scope, 526 joined operator-response-lag samples — both
well above this study's own admissibility floors (30 sessions, 10 lag
samples). A second machine's replication run was not needed: the
pre-registered replication rule only requires a second corpus when a
change is recommended, and this run recommended none.

The full per-candidate dollar-cost and `C_bar` table is not reproduced
here — see [`handoff-nudge-deep-tail-lever.md:142–182`](../../.claude/plans/handoff-nudge-deep-tail-lever.md)
for the exact figures. An earlier study on this same lever found a
realistic-arm dollar spread between 40,000 and 80,000 small enough to
call indistinguishable from noise (`.claude/plans/rearm-hook-band-spacing.md:37–39,47–51`);
the $50 floor above sits well past that noise floor, and this run's
closest challenger didn't clear it either.

**Why a wider spacing doesn't win on dismissal risk either.** 80,000 was
originally chosen over a tied 40,000 candidate solely for separation from
the then-median operator-response lag (`docs/handoff-nudge.md:20`). This
run's fresh lag median, 107,262, exceeds 80,000 outright, not just the
2.6x margin the original value targeted.

## What this doesn't cover

The same plan and the same run also evaluated two other levers, both
declined for reasons unrelated to `REARM_SPACING`'s cost or margin:

- **Two-tier (softer-then-harder) nudge** — declined; no retrospective
  price exists for the build, since today's log carries one advisory
  severity and no historical fire can be classified tier-1 vs. tier-2.
- **Nudge-phrasing change** — declined; the current phrasing already
  clears its own pre-registered adherence thresholds.

Neither is covered here; see [`cost-levers-considered.md`](../cost-levers-considered.md)'s
register entry and [`handoff-nudge-deep-tail-lever.md`](../../.claude/plans/handoff-nudge-deep-tail-lever.md)
for both.

## Sources

- **`claude/.claude/scripts/transcript-analysis.py`** — `rearm-backtest`
  subcommand, run against this repo's own transcript corpus, Linux
  machine, 2026-09-14.
- **`.claude/plans/handoff-nudge-deep-tail-lever.md`** — this study's own
  plan, including the pre-registered cost and dismissal-risk gates, the
  replication rule, and the full run output.
- **`.claude/plans/rearm-hook-band-spacing.md`** — the original spacing
  study, source of the noise-floor precedent and the lag figure 80,000
  was originally calibrated against.
- **[`case-studies/handoff-hard-block-position.md`](handoff-hard-block-position.md)** —
  the prior study whose deep-tail mediator finding motivated this
  re-test.
- **[`cost-levers-considered.md`](../cost-levers-considered.md)** — this
  study's own lever-table entry, covering all four levers this plan
  evaluated.
