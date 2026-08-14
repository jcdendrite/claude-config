# Cold prompt-cache attribution

Validation of the classifier that separates a **cold** prompt-cache
re-write (the prefix was not served from cache) from an ordinary
**incremental append** (the prefix was served; new content was written on
top). Companion to `.claude/plans/cost-attribution-integrity.md` Step 1.

The distinction matters because context tokens — cache read plus both
cache-write tiers — are 87.7% of measured spend, and only cold re-writes
are potentially avoidable.

## Why a classifier is needed at all

The API reports `cache_read_input_tokens` and `cache_creation` token counts
per turn, but no hit/miss signal. Cache state must therefore be inferred,
and the inference has to be validated before any cost figure derived from
it can be trusted.

## Ground truth without circularity

Both candidate rules are scored against turn pairs whose cache state is
fixed by vendor TTL semantics rather than by either rule's own output:

| Set | Construction | Why the label holds |
|---|---|---|
| **Cold** | Consecutive turns >60 min apart, on an account whose main thread never receives the 1-hour TTL | Only a 5-minute entry could exist, and it has provably expired |
| **Warm** | Consecutive turns <60 s apart in the same session and thread | Well inside any TTL; the entry should still be resident |

Restricting the cold set to a zero-1h-TTL account is load-bearing: on an
account that does receive the 1-hour tier, a 60-minute gap does not
guarantee expiry.

## Candidate rules

- **R1 (incumbent):** cold ⟺ `cache_creation > cache_read_input_tokens`.
- **R2 (candidate):** cold ⟺ read *collapse* exceeds a margin `T`, where
  collapse = `(prior_turn_total − read) / prior_turn_total` and
  `prior_turn_total` = the prior turn's `read + cache_creation`.

R2's premise: a warm cache serves the *whole* matched prefix as a read, so
an append leaves read near the prior turn's total regardless of how much
new content is written. R1 cannot express that, because a single large tool
result on a short prefix produces `write > read` while the prefix was
served normally.

## Result

Measured over 41,997 consecutive-turn pairs (106 cold, 39,940 warm).

Read-collapse distribution, showing the two sets barely overlap:

| Set | p05 | p25 | median | p75 | p95 |
|---|---|---|---|---|---|
| Cold | +0.785 | +0.900 | +0.933 | +0.960 | +1.000 |
| Warm | +0.000 | +0.000 | +0.001 | +0.016 | +0.364 |

Rule scores:

| Rule | True-positive rate (cold) | False-positive rate (warm) |
|---|---|---|
| R1 `write > read` | 93.4% | 4.6% |
| R2 `T = 0.10` | 100.0% | 9.6% |
| R2 `T = 0.25` | 100.0% | 6.0% |
| **R2 `T = 0.50`** | **100.0%** | **4.5%** |
| R2 `T = 0.75` | 97.2% | 3.7% |
| R2 `T = 0.90` | 75.5% | 2.4% |

**R2 at `T = 0.50` is adopted.** It dominates R1 — strictly better
detection (100% vs 93.4%) at a marginally lower false-positive rate — and
sits at the maximum of Youden's J (+0.955) across the tested thresholds.
`T` is set from this separation, not assumed.

## What the validated rule does to the headline number

Volume attributed to cold re-writes barely moves between the two rules,
which is itself worth recording: R1's *classification* was unreliable, but
its aggregate was not far off.

| Account | Total cache-write tokens | R1 cold | R2 cold (`T`=0.50) |
|---|---|---|---|
| A | 567,862,487 | 76.6% | 76.4% |
| B | 1,810,574,187 | 61.4% | 60.6% |

## The finding that matters

Partitioning R2's cold events by the gap that preceded them:

| Account | Cold within 60 s of the prior turn | Share of all cache-write tokens | Events |
|---|---|---|---|
| A | 270,510,876 tokens | 47.6% | 1,797 |
| B | 718,633,668 tokens | 39.7% | 8,721 |

**Roughly 40–48% of all cache-write spend is prefix invalidation occurring
within a minute of the previous turn** — which no TTL expiry can explain.
Both accounts run a byte-identical harness, and both show it.

This is the population worth attributing. Under the incumbent rule these
turns were indistinguishable from large appends, which is why the effect
had not previously been isolated.

## Attribution of the sub-60-second population

Scoring the same corpus under the validated classifier, restricted to
sub-60-second pairs, and computing lift as
P(signal | cold) / P(signal | warm) within that population:

| Signal | Account A lift | Account B lift | Cold tokens (A / B) |
|---|---|---|---|
| **(no record between turns)** | **1.6x** | **1.6x** | 205,465,412 / 438,676,713 |
| `system` record | below threshold | 2.3x | — / 9,675,727 |
| `Read` tool call | 1.2x | 0.6x | 15,227,469 / 18,591,972 |
| `Agent` spawn | below threshold | 1.0x | — / 7,390,610 |
| `Bash` tool call | 0.2x | 0.3x | 7,585,632 / 26,611,497 |
| `Edit` tool call | below threshold | 0.2x | — / 6,226,794 |

**No harness action predicts these events.** Roughly 80% of cold events in
this population have no intervening record at all, carrying 205M tokens on
account A and 439M on account B — and every named tool or record type sits
at or below 1.0x lift, meaning cold turns are no more likely to follow them
than warm turns are.

### A prior correlation table is retracted

An earlier pass over the same corpus, scored under the **incumbent**
`write > read` rule, reported high lift for `file-history-snapshot`
records (9.1–9.5x), `AskUserQuestion` (4.2–7.3x), `Agent` spawns
(2.2–3.2x), and `ExitPlanMode` (16.5x). Those correlations do not survive
the validated classifier and are withdrawn.

The explanation is the incumbent rule's known defect: each of those signals
appends a large block of content to the prompt, which inflates
`cache_creation` and trips `write > read` whether or not the prefix was
served. The earlier table was measuring *content growth*, not cache
invalidation. This is the specific failure mode that motivated validating
the classifier before building on it.

### What this implies

The sub-60-second cold population is real and large, but it is not
attributable to any harness action visible in the transcript. Remaining
candidates all sit outside what a transcript can show — vendor-side cache
eviction, cache-breakpoint placement, or request-construction details that
change the prefix without emitting a record.

This bears directly on whether harness changes can reduce it. On this
evidence, they cannot: there is no harness action to change.

## Confirmed mechanism: live stow mutation during a running session

`~/.claude/CLAUDE.md`, and the skill, agent, and rule files beside it, are
symlinks into the repository's main worktree. Their content therefore
changes the moment the main branch's HEAD moves — while sessions are
running, and without emitting any transcript record. This is the documented
"changes go live on `git pull`" behavior, seen from the cache's side.

Test: for each consecutive turn pair in a session, did a main-branch ref
move land in the gap between the two turns? Compare cold rates.

| Account | Pairs straddling a stowed-file-changing ref move | Cold rate | Baseline (no ref move) | Lift |
|---|---|---|---|---|
| A | 140 | 45.0% | 5.3% | **8.42x** |
| B | 675 | 24.1% | 4.4% | **5.46x** |

Measured over 118 ref moves on the main branch, 112 of which changed a file
under `claude/.claude/`. Restricting to those 112 slightly *raises* the lift
in both accounts (8.26x → 8.42x, 5.39x → 5.46x), which is the direction the
mechanism predicts.

This is the strongest predictor found. Every signal in the preceding
attribution table sits at or below 2.3x.

**Its volume contribution is nonetheless small.** Attributable cold tokens
are 22,636,769 on account A (5.2% of that account's cold total) and
41,011,719 on account B (3.7%). The mechanism fires at most once per ref
move per running session, which bounds its exposure regardless of how
reliably it fires.

The finding generalizes beyond git: any mid-session mutation of the prompt
prefix would produce the same signature, and the repository's own tooling
is only one source of such mutation. What makes this instance measurable is
that ref moves are timestamped; other prefix mutations are not.

## Limits of this result

- The warm set's 4.5% false-positive rate is not separable from a genuine
  sub-minute invalidation rate: the construction assumes <60 s implies
  warm, so a real invalidation inside that window is indistinguishable from
  a misclassification. This bounds precision on the warm side; it does not
  affect the cold set, where the label is independent of the rule.
- The rule answers *whether* a prefix was served, not *why* it was not.
  Idle expiry and structural invalidation share one signature.
- The rule is binary and pools full with partial-breakpoint collapse. Where
  the distinction matters, report collapse magnitude alongside the count.
- The cold set is small (n=106), being restricted to long-gap turns on one
  account. It is sufficient to separate the two distributions, not to
  characterize cold-turn behavior in general.
