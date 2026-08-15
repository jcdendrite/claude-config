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

## Locating the break: a fixed breakpoint boundary

On a cold turn, `cache_read_input_tokens` is the size of the prefix that
still matched. Its distribution locates the break.

| Account | p25 | median | p75 | share in 20k–50k | share at exactly 0 |
|---|---|---|---|---|---|
| A | 25,398 | 25,412 | 25,412 | 83.0% | 5.0% |
| B | 20,794 | 25,398 | 25,412 | 89.0% | 4.6% |

The median and p75 agree to the token across two independently-billed
accounts. On roughly 85% of cold events the first ~25,412 tokens survive
and everything after is rewritten; only ~5% lose the whole prefix.

The break is therefore **not** at the front — the system prompt and tool
schemas persist — but at the next cache breakpoint. Restricted to events
with a sub-60-second gap, no observable field change, no prior tool call
and no intervening record, this population is 205,949,577 cache-write
tokens on account A and 438,707,436 on account B.

## Discriminating mutation from breakpoint churn

Two mechanisms predict a fixed surviving-read boundary:

- **Shared-state mutation** — something rewrites content that sits after
  breakpoint 1, invalidating every running session. Predicts bursty,
  time-clustered events that co-occur across concurrent sessions.
- **Breakpoint churn** — the client repositions rolling cache breakpoints
  as the conversation grows. Predicts regular, per-session events spaced by
  roughly constant conversation growth, uncorrelated across sessions.

Measured:

| Signal | Account A | Account B |
|---|---|---|
| Turns between consecutive cold events (median) | 1 | 1 |
| Coefficient of variation | 2.34 | 2.43 |

A coefficient of variation above 2 is strongly bursty, not periodic, which
does not fit a mechanism driven by steady conversation growth.

Synchronization was then measured directly, counting **distinct sessions**
cold per minute against the independence baseline implied by the
per-session cold rate and the number of sessions active that minute:

| | Minutes with ≥2 sessions active | Observed ≥2 cold | Expected if independent | Lift |
|---|---|---|---|---|
| A | 909 | 4.51% | 2.80% | 1.61x |
| B | 5,793 | 3.85% | 2.26% | 1.70x |

Across the two separately-billed accounts on the same machine, conditioned
on minutes where both were active, the lift is weaker still: 1.44x at a
±0 minute window, 1.28x at ±1, 1.17x at ±2.

**Synchronization is real but weak — 1.6–1.7x within an account.** That is
far too small to carry the population. A single shared mutation
invalidating every running session would produce near-total co-occurrence;
this does not. The mechanism behind the bulk of cold events is therefore
**not** established, and no shared-state explanation should be inferred
from these numbers.

Confirmed sources remain confirmed: git pulls invalidate running sessions
at 5.5–8.4x lift, but carry only ~4% of cold tokens.

### The strongest remaining lead

The median gap between consecutive cold events within a session is **one
turn** — cold events arrive in runs, not as isolated invalidations. A
one-off prefix mutation cannot produce that: it would cause a single cold
turn, after which the new prefix re-caches and subsequent turns are warm.

Runs at a fixed ~25,412-token survivor instead suggest that content sitting
immediately *after* the first breakpoint changes on many turns, so that
region never stabilizes into a reusable cache entry. Per-turn-varying
injected content is the shape that would do this. Identifying what occupies
that region is the next measurement, and it needs an instrument that can
see the assembled request — transcripts do not record it.

## Testing the leading hypothesis: wire-level capture

Claude Code's `OTEL_LOG_RAW_API_BODIES=file:<dir>` (documented at
`monitoring-usage.md`) writes the untruncated, wire-format request/response
JSON for every API attempt — the instrument the prior section identified as
missing. This section's measurements are scoped differently from the rest of
this document: a live instrumented session plus a 15-session, 11,317-turn
sample from local transcripts on one account, not the multi-account corpus
used above. Figures here should not be pooled with it.

**Request structure, confirmed by SHA-256 comparison across turns.** The
wire order is `tools → system[0..3] → messages[]`. `system[2]` carries
`cache_control: {scope: "global"}` — shared cross-session, not just within
one conversation: a brand-new session's first turn read tools-plus-base-
system as a cache **hit** rather than a miss. The fixed survivor floor
measured directly at **22,050 tokens**, identical across two unrelated
sessions on the same account — consistent with the corpus's ~25,412-token
median (a different account/CLI version would size its global-scope block
differently) and pinning the number precisely for this account.

**The leading hypothesis — refuted.** The candidate mechanism was the
per-turn `ToolSearch` "deferred tools" reminder rewriting itself as tools
load, invalidating everything after it. Two tests:

1. Loading a tool via `ToolSearch` across separate `--resume` invocations
   left the reminder text unchanged on the next turn — loaded-tool state
   turned out to be in-process, not persisted, so this test was invalid by
   construction.
2. Loading three tools sequentially *within one process* (state genuinely
   persisted): the `tools` array measurably grew and reordered (12→13
   entries, schema inserted near the front, hash changed) — yet
   `cache_read_input_tokens` matched the full prior-turn total on every
   following round-trip. Cache stayed 100% warm.

The mechanism does not reproduce under direct, instrumented test.

**Attachment-logged events explain a minority.** Claude Code records certain
context-refresh events as `attachment`-typed transcript entries
(`date_change`, `skill_listing`, `deferred_tools_delta`,
`mcp_instructions_delta`, `agent_listing_delta`, `hook_additional_context`,
among others). Restricting to mid-session cold turns (excluding each
session's unclassifiable first turn) and checking whether *any* attachment
event immediately preceded each one:

| | Count | Share of mid-session cold turns |
|---|---|---|
| Preceded by some attachment event | 47 | 18.3% |
| Preceded by no attachment event | 210 | 81.7% |

Among the types that do occur, the `_delta`-suffixed ones and `date_change`
carry real lift over the ~2.3% sample-wide cold rate — `deferred_tools_delta`
13.9%, `mcp_instructions_delta` 18.2%, `agent_listing_delta` 16.7%,
`skill_listing` 8.5%, `date_change` 27.3% (n=11, too small to trust
precisely) — but collectively they account for well under a fifth of cold
turns. `hook_success`, `task_reminder`, and `queued_command` — the three
most frequent attachment types by volume — sit at or below the baseline
rate and predict nothing.

**Ordinary TTL expiry does not explain the remainder either.** Of the 210
mid-session cold turns with no attachment marker, 80% occurred with a
wall-clock gap under 5 minutes since the prior turn (median ≈ 0); only 8.1%
had a gap ≥60 minutes, the threshold this document's own ground-truth
construction (see "Ground truth without circularity") uses for idle expiry.
These are rapid, same-session rewrites, not TTL lapses.

**Conclusion.** Roughly two-thirds of all cold events in this sample are
explained by neither the leading content-drift hypothesis, nor any
harness-logged event, nor idle-time TTL expiry. Pinning the actual
mechanism requires wire-level capture of an occurrence as it happens —
transcripts do not retroactively record the assembled request, so this
can't be recovered from history after the fact.

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
- The wire-level capture section's 15-session sample is single-account and
  was not selected randomly — it is the largest local transcripts by file
  size, which skews toward long, tool-heavy sessions. It should not be
  treated as representative of the account's full cold-event population.
