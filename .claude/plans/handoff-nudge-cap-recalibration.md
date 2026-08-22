# Re-ground the handoff-nudge absolute cap against cost-per-work, not nudge frequency

## Context

**Goal: replace `HANDOFF_NUDGE_ABS_CAP`'s current 360000-token default — grounded
against a nudge-frequency ceiling, not against cost — with a value read off a
fresh per-PR cost-vs-context measurement, using the already-implemented but
never-run `pr-cost` ledger before the recoverable transcript window deletes the
data needed to make it.**

The engineer's stated premise for wanting a much lower cap — that per-token
billing is quadratic in context size — does not hold: `_price_turn`
(`transcript_analysis/pricing.py:431`) prices every token class (input, output,
cache-read, cache-write) at a flat per-token rate. Billing is linear.

But a different, already-measured fact in this repo supports the same
practical conclusion for a different reason. `handoff-boundary-decision-rule.md`
(merged, read-only) priced 396 transcripts by turn-index and found a U-shaped
cost-per-output-token curve: cheapest at turn-index 40–80 (mean context 136k,
1.00x), rising to 1.31x at 80–150 (mean 199k), 1.93x at 150–300 (mean 301k), and
3.14x at 300+ (mean 498k) — but *also* 3.55x at turns 0–5 (mean 60k), because a
fresh session pays a real ramp-up cost. The current 360000 cap sits deep in the
expensive tail; the cheap zone is close to the engineer's own guess of
100–150k, but "keep context as low as possible" overshoots it, since handing
off before the ramp amortizes is itself expensive.

That curve buckets by session turn-index, not by delivered work, so it can't
directly answer "what does it cost, in tokens and dollars, to ship one PR" —
which is exactly the question the engineer asked, and exactly what PR #690's
`pr-cost` subcommand was built to answer. `pr-cost` has shipped since
2026-08-19 (`cmd_pr_cost`, `transcript-analysis.py:7407`) but has never been run
with `--record` on this machine — the ledger file does not exist. Standing
behind both curves is the same fact PR #690 flagged as urgent and unresolved:
the local transcript corpus is on Claude Code's rolling `cleanupPeriodDays`
deletion window, so every day this stays undone permanently shrinks the
population recoverable for either measurement.

**Why now, beyond the engineer's request:** the engineer independently reports
having kept context deliberately small across recent sessions and wants to
know whether that effort actually lowered $/PR — the `pr-cost` backfill this
plan performs answers that question in the same pass as grounding the cap,
since both need the same ledger populated before the window closes further.

**Source discipline for two secondary citations the request leaned on** is
graded and bounded in A4/A5 below — neither grounds a specific figure here.

**Coordination.** A concurrent session (`handoff-hard-block`) is separately
converting this same nudge from informational-only to a hard block with
escalation-ladder rules (`HANDOFF_NUDGE_BLOCK_AFTER`). Confirmed via
cross-session message this session: that work touches only the post-first-fire
escalation path and its own doc sections ("Why this block-after count", "Known
limitations"), never `compute_threshold()`, `HANDOFF_NUDGE_ABS_CAP`,
`PCT_THRESHOLD`, or "Why this cap" — the two changes are on disjoint lines and
can land in either order.

## Approach

**Two steps, strictly ordered, mirroring how 360000 itself was grounded:**
first populate a per-PR context/cost dataset that does not yet exist, then
read a cap off it — following the still-uncovered gap A7 identifies in the
prior 300k/360k rejection.

### Root problem and givens

**Root problem:** `HANDOFF_NUDGE_ABS_CAP` was calibrated to a nudge-frequency
ceiling (≤50% session-share, to bound dismissal-fatigue risk — see
`docs/handoff-nudge.md` "Why this cap"), not to cost-per-unit-of-work
efficiency. A separate, already-merged measurement shows the current value
sits in a region measured at 1.93x–3.14x the cheapest observed cost-per-output-
token rate. No dataset yet exists that measures cost per *PR* (the engineer's
actual question) against context size, and the raw material for building one
is being deleted daily.

| # | Given | Why it is fixed |
|---|---|---|
| G1 | Every token class is billed at a flat per-token rate — no quadratic term exists anywhere in the pricing model. | Vendor pricing mechanic. `[verified: transcript_analysis/pricing.py:431-456 _price_turn, read this session]`. This falsifies the request's own stated mechanism; the design below is grounded in the measured cost-per-work curve instead, not in per-token billing shape. |
| G2 | Transcripts already aged out of the local corpus cannot be recovered — no retention change reconstructs history predating whatever window survives at implementation time. | Deleted files are gone; this is the only irreversible part. `[verified: token-cost-per-pr-study.md G1, re-derive the current surviving-window size at implementation time]`. The go-forward retention window itself (`cleanupPeriodDays`) is **not** a given — it is a real, configurable `settings.json` key that plan already evaluated and declined to raise, for reasons unrelated to this plan (see Out of Scope); this plan doesn't reopen that decision, and doesn't rest any conclusion on the window being fixed. |
| G3 | Cache-read/cache-write TTL pricing and base per-token rates are vendor-set. | Same source as G1 — establishes *why* a cost-per-work curve, not a pricing-formula argument, is the only way to grow evidence for a lower cap. |

**C1 — design constraint, not a given** (this plan's own Critical Files already
list the file that would change it, so it fails the "outside this plan's own
reach" test a given requires — recorded here for visibility instead): a
GH-556 regression pin (`test_old_120k_constant_no_longer_fires_on_1m_models`)
asserts the hook does **not** fire at 135000 tokens on a 1M-window model.
`[verified: claude/.claude/hooks/tests/test_nudge_handoff_near_context_cap.py:1578-1582,
read this session]`. Any candidate cap ≤135000 inverts this pin — M3 below
treats that as a fork requiring an explicit decision, and M5 updates the test
in step.

### Mechanisms

**M1 — Backfill the `pr-cost` ledger across the still-recoverable local corpus.**
*(anchors: root, G2)*. The dataset needed to answer "what does a PR cost, in
tokens and dollars, at what context size" does not exist — `pr-cost-ledger.tsv`
has zero rows on this machine. `pr-cost --record` (no `--pr` filter, so it
walks every branch with local corpus activity) is already-implemented,
already-tested tooling; running it is the whole of this mechanism, not new
code. Requires two engineer actions outside this plan's diff, both consent
gates the tool itself enforces and this plan does not bypass:

1. Touch `~/.claude/.pr-cost-enabled` (the opt-in sentinel `install.sh` prompts
   for on fresh installs; absent on this machine per this session's check).
2. Choose a `--machine-label` (`^[a-z0-9]{1,8}$`, must not equal this
   machine's hostname).

*Over-powered-primitive check* — two lighter alternatives considered and
rejected:

1. **Reuse the existing turn-index curve alone, skip fresh capture.** Rejected:
   that curve buckets by session turn-index, not by PR, so it cannot answer
   "tokens/cost to ship a PR" — the engineer's actual question — and its own
   source plan documents it as a one-off, non-reproducible, point-in-time
   measurement not meant to be trusted indefinitely.
2. **A brand-new bespoke bucketing subcommand reading raw transcripts
   directly, bypassing `pr-cost`.** Rejected: duplicates dedup
   (`dedup_turns_by_request_id`), pricing (`_price_turn`, `_cache_write_split`),
   and branch attribution (`_attributed_branch`) that `pr-cost` already solved
   and tested — a single-source-of-truth violation for no benefit, since
   `pr-cost`'s schema already carries `mean_context_at_turn` and
   `sum_context_at_turn` per row.

**M2 — One-off analysis bucketing captured ledger rows by `mean_context_at_turn`.**
*(anchors: root, G1)*. Once M1 populates real rows, bucket them (suggested
edges: <100k, 100–150k, 150–200k, 200–250k, 250–300k, 300k+, mirroring the
existing turn-index table's shape) and report $/PR, $/1k output tokens, and
session count per bucket — flagging any bucket too thin to trust, the same
caveat `token-cost-per-pr-study.md`'s own A9 already anticipated for this
population size. Separately break out the engineer's self-identified
recent-low-context cohort (the last few days of deliberately-small-context
sessions) against the rest, to answer the engineer's own side question about
whether that effort already moved $/PR.

*Not a committed subcommand* — matches the precedent both existing curves in
this repo already set (`context-distribution`'s absolute-bucketing addition
was committed because capture is recurring; this one-time cap decision is not).
Documented in the case-study/lever-register writeup as a point-in-time
measurement, same caveat as the turn-index table.

**M2 results (this session, `pr-cost-ledger.tsv`, 145 rows, repo `claude-config`,
PRs #278–#698, merged 2026-05-20 to 2026-08-18):**

| Bucket | n (PRs) | $/PR | $/1k output tokens | Mean context |
|---|---|---|---|---|
| <100k | 1 | 30.60 | 0.0712 | 88,801 |
| 100–150k | 23 | 19.02 | 0.0915 | 136,158 |
| 150–200k | 55 | 36.63 | 0.0979 | 176,363 |
| 200–250k | 32 | 51.84 | 0.1134 | 223,583 |
| 250–300k | 19 | 50.44 | 0.1345 | 270,696 |
| 300k+ | 15 | 58.29 | 0.1515 | 377,534 |

`$/PR` is `sum(cache_read_usd, cache_write_5m_usd, cache_write_1h_usd,
output_usd, input_usd)` averaged per row in the bucket (`opus_dollars` is a
breakdown of that same total by model family, not additive — confirmed
against `_new_pr_cost_row`, `transcript-analysis.py:7305-7349`, this
session). 100–150k is the cheapest bucket with a trustworthy sample (n=23):
both cost measures rise monotonically from there through every larger
bucket. `<100k`'s apparent lower $/PR is n=1 — not trustworthy, consistent
with A11's anticipated thin-bucket risk — so it does not move the
conclusion below. This corroborates A2's turn-index curve (cheapest at mean
context 136k) with an independent, PR-bucketed measurement.

**Sanity check (Verification step 1):** `gh pr list --state merged --search
"merged:2026-05-20..2026-08-18"` returns 332 merged PRs in the ledger's own
date range, against the ledger's 145 captured rows (44%). The gap is
expected, not a defect: `pr-cost` only captures a PR whose branch still has
local session-transcript activity, and this plan's own premise is that the
local corpus is on a rolling deletion window — most of the shortfall is
already-aged-out transcripts, not a `pr-cost` bug. 145 rows across 6 buckets
(range n=1 to n=55) is enough to trust the 100–150k/150–200k comparison that
this plan's conclusion rests on.

*Over-powered-primitive check* — two lighter alternatives considered and
rejected:

1. **A permanent `pr-cost --bucket-by-context` report mode.** Rejected: no
   identified recurring caller for context-bucketed output specifically;
   `docs/handoff-nudge.md` already tells a future revisit to "re-run
   `context-distribution`," not to invent a second standing report surface for
   one threshold decision.
2. **Deriving buckets from `context-distribution`'s existing percentiles
   instead of `pr-cost` rows.** Rejected: that command answers "how much of a
   session's own context window got used," a capacity question; `pr-cost` rows
   answer "what did this unit of shipped work cost," the question actually
   being asked here.

**M3 — Pick the new cap from M2's curve, honoring C1 as a hard constraint.**
*(anchors: root, C1)*. If the curve's cheapest defensible bucket sits above
135000, the cap moves there directly. If it sits at or below 135000, C1's own
regression pin forks the decision — either the cap stays at the lowest value
above 135000 the curve still supports, or the pin itself is deliberately
revised with its own stated cause (not silently overwritten). This fork is
resolved after M2 produces real numbers, not pre-committed here.

**M4 — Apply the chosen cap across every hand-synced site.** *(anchors: root)*.
This repo has no single source of truth for this literal — it is duplicated by
convention across a bash hook, a Python mirror constant, and prose in three
docs, the same hand-sync pattern `absolute-token-handoff-threshold.md` already
flagged as a defect class it declined to fix wholesale. Following the existing
convention rather than re-architecting it (Axis 4 — this plan changes a
number, not the duplication pattern):

- `nudge-handoff-near-context-cap.sh`'s `compute_threshold()` fallback default
  and its header-comment mention.
- `transcript-analysis.py`'s `_HANDOFF_NUDGE_ABS_CAP` mirror constant and its
  comment (used by `rearm-backtest`, `plan-boundary`, and `spend-over-threshold`
  — `cmd_spend_over_threshold` (`:7639`) also calls `_hook_effective_fire_threshold`
  directly at `:7645` to compute each session's own fire threshold for its
  dollar-share report, per `docs/handoff-nudge.md`'s "How to read
  spend-over-threshold output" section; missed in an earlier pass of this
  plan, caught by `staff-platform-engineer` at plan-review). Also update the
  in-string literal inside `rearm-backtest`'s printed report description
  (`:9632`, `"...the fixed 360,000-token _HANDOFF_NUDGE_ABS_CAP..."`) — a
  quoted-string prose mention, not a `#`-comment, so it sits outside a literal
  reading of "mirror constant and comment."
- `transcript_analysis/pricing.py`'s `_CONTEXT_DISTRIBUTION_THRESHOLD_ABS`
  tuple (`:96-98`), which includes `360_000` specifically because — per its
  own comment — it is "the live 1M-model effective threshold today," so
  `context-distribution`'s report can show the value the hook is actually
  configured to fire at, not just candidate values. Add the new cap in its
  place (or alongside it) so that property still holds after M3 picks a value.
- `docs/handoff-nudge.md`'s "Why this cap" section (new grounding narrative
  replacing the 2026-08-08 session-share measurement basis), its per-model
  table, its example `--check` JSON output, and its "Known limitations"
  bullet on the model→window table (`:127`, "the absolute cap (360000 by
  default)") — a fourth literal mention outside the three named subsections,
  caught at plan-review. **Constraint, not just
  content:** `test_doc_counts.py`'s `_count_handoff_nudge_abs_cap_default`
  DocCountFact derives the cap's ground truth *behaviorally* (runs the hook
  against a synthetic transcript, reads the emitted threshold back) and
  regex-matches it against four exact phrase shapes across this file and
  README.md (`\| 1M \(default\) \| 400000 \| (\d+)`, `capped at (\d+) tokens`,
  `past a (\d+)-token prefix on the largest context window`, `(\d+) tokens
  \(default\): the absolute-token cap`). The rewrite must keep the numeric
  value inside these exact phrasings — a paraphrase that drops one loses the
  match entirely (a missing-match failure, not a wrong-value one), and the
  behavioral derivation means correctly updating the hook's own default and
  these four phrasings is what makes the test pass, not a fifth hand-synced
  assertion to remember.
- `README.md`'s three prose mentions matched by the same DocCountFact (two
  in one sentence, plus the "N tokens (default)" glossary-style line).
- `docs/transcript-analysis.md`'s `rearm-backtest` description.
- A new dated row in `docs/cost-levers-considered.md` recording this retune's
  verdict and measured reason — appended, not editing any prior row (those are
  read-only historical records per the repo `CLAUDE.md`, Axis 3).
- A `CHANGELOG.md` Unreleased entry, per this repo's established convention.

A stow consumer who already set `HANDOFF_NUDGE_ABS_CAP` explicitly (e.g., a
personal override chosen under the old 360000 rationale) never reaches the
fallback default and is unaffected either way — not a bug, but worth one line
in the `CHANGELOG.md` entry so an existing override isn't mistaken for having
silently adopted the new grounding.

**M5 — Update the regression suite for whatever M3 decides.** *(anchors: C1)*.
`test_old_120k_constant_no_longer_fires_on_1m_models` and any other test
asserting the literal 360000 (comments/docstrings in
`test_transcript_analysis.py:14395,14779,14835` describe it) get updated in
lockstep with M4, not left to drift.

Plan-review (`staff-sdet` and `staff-platform-engineer`, independently
convergent) found the grep-for-the-literal method above structurally misses a
third category: a **fixture/input value chosen to equal the cap, feeding an
assertion on a derived value that never itself mentions the literal.** One
confirmed instance — `test_transcript_analysis.py:8393`,
`test_context_at_turn_exactly_equal_to_threshold_counts_as_above` in
`TestSpendOverThreshold`, whose docstring states its whole purpose is pinning
the `>=`-vs-`>` boundary by setting `input=360_000` to "the same point the
real hook fires at." After M3's retune, this stops sitting on the boundary —
360000 sits well above any candidate cap in the 100–200k range, so the
assertion (`Share == "100.0%"`) keeps passing while the off-by-one it exists
to catch goes untested: a silent coverage regression, not a CI failure.
**Required, not optional:** re-derive this fixture's input value from
`_mod._hook_effective_fire_threshold("claude-sonnet-5")` at test-call time
(mirroring how `test_nudge_handoff_near_context_cap.py`'s
`test_fires_at_exactly_threshold_for_model` already derives its own boundary
from `DEFAULT_ABS_CAP`/`KNOWN_MODEL_THRESHOLDS` instead of hardcoding it) —
not a literal swap to the new number, which would only reproduce the same
staleness risk at the next retune. Also update the stale docstring at `:8285`
("own fire threshold (360,000 for claude-sonnet-5)"), both outside the
original three cited line numbers.

`test_nudge_handoff_near_context_cap.py:61`'s `DEFAULT_ABS_CAP = 360_000`
module constant (driving `LARGE_THRESHOLD`, `KNOWN_MODEL_THRESHOLDS`, and
every parametrized test built off it) needs updating too — not itself a
silent-drift risk (a miss here fails loudly: `test_silent_one_below_threshold_for_model`
would assert silence at a token count the retuned hook already fires at), but
named explicitly here so it isn't left to an implementation-time hunt.

### Assumption ledger

| Row | Assumption | Tag |
|---|---|---|
| A1 | Billing is linear per token by class; no quadratic term exists. | `[verified: transcript_analysis/pricing.py:431-456, read this session]` |
| A2 | Cost-per-output-token is U-shaped by turn-index: 3.55x at turns 0–5 (mean 60k), falling to 1.00x at 40–80 (mean 136k), then rising to 1.31x at 80–150 (mean 199k), 1.93x at 150–300 (mean 301k), 3.14x at 300+ (mean 498k). | `[verified: .claude/plans/handoff-boundary-decision-rule.md, read this session]`. That plan's own text flags the underlying script as one-off and non-reproducible — cited here as directional corroboration for M1's necessity, not re-asserted as current fact. |
| A3 | A fresh session's first 5–10 turns cost 3.55x/1.23x more per unit of output than the cheapest band — premature handoff is not free. | `[verified: same source as A2]` |
| A4 | Chroma Research's "Context Rot" (July 2025) confirms reliability degrades with input length but states no universal safe-token-count threshold. | `[verified: direct fetch of trychroma.com/research/context-rot this session]` |
| A5 | The cited Reddit thread is community discussion with no primary-source numeric grounding of its own. | `[engineer-verified]` — engineer pasted the full thread this session and agreed it is a "stepping stone," not a citable source; not used to ground any figure here. |
| A6 | `pr-cost-ledger.tsv` has zero rows on this machine; `--record` requires the absent `~/.claude/.pr-cost-enabled` sentinel and a `--machine-label`. | `[verified: filesystem check this session]` |
| A7 | The prior 360000→300000 retune rejection compared two values both inside the 300–400k tail bucket and does not cover a move into the 100–200k range. | `[verified: docs/cost-levers-considered.md "From handoff-boundary-decision-rule.md" row, cross-checked against handoff-boundary-decision-rule.md's own bucket table]` |
| A8 | GH-556's regression pin fails any candidate cap ≤135000 unless deliberately revised. | `[verified: test_nudge_handoff_near_context_cap.py:1578-1582]` |
| A9 | `handoff-hard-block` (concurrent session) touches only the escalation ladder past first fire, never `compute_threshold`/`ABS_CAP`/"Why this cap". | `[verified: cross-session message this session, both directions]` |
| A10 | The engineer wants the cap value itself picked from fresh measurement, not pre-committed to 100–150k or 200k. | `[engineer-verified]` |
| A11 | The `pr-cost` backfill population size and its per-bucket sample counts are unknown until M1 runs — the curve in M2 may turn out too thin in some buckets to support a confident M3 decision. | `[unverified]` — if so, M3's fallback is to combine M2's thin curve with A2's turn-index curve as corroboration, stated explicitly as a lower-confidence basis, rather than blocking the plan entirely. |

## Critical files

**Modify:**

- `claude/.claude/hooks/nudge-handoff-near-context-cap.sh` — `compute_threshold()`'s
  fallback default and header comment (M4).
- `claude/.claude/hooks/tests/test_nudge_handoff_near_context_cap.py` — default-value
  assertions, `DEFAULT_ABS_CAP` at `:61`, and the GH-556 regression test per M3/M5's outcome.
- `claude/.claude/scripts/transcript-analysis.py` — `_HANDOFF_NUDGE_ABS_CAP` mirror
  constant and comment, `cmd_spend_over_threshold`'s consumption of it via
  `_hook_effective_fire_threshold`, and the in-string literal at `:9632` (M4).
- `claude/.claude/scripts/transcript_analysis/pricing.py` —
  `_CONTEXT_DISTRIBUTION_THRESHOLD_ABS` tuple and its comment (M4), so the
  candidate-threshold sweep still shows the hook's actual configured value.
  **Reuse, do not reimplement:** `cmd_pr_cost`
  (`:7407`), `_price_turn`/`_cache_write_split` (`transcript_analysis/pricing.py`),
  `_attributed_branch`/`dedup_turns_by_request_id` — all already power `pr-cost`
  and need no changes for M1/M2.
- `claude/.claude/scripts/tests/test_transcript_analysis.py` — comment/docstring
  mentions of 360,000 at `:14395,14779,14835,8285`; the `:8393` boundary
  fixture in `TestSpendOverThreshold` re-derived from
  `_hook_effective_fire_threshold`, not hardcoded (M5).
- `docs/handoff-nudge.md` — "Why this cap" rewrite, per-model table, example
  `--check` JSON (M4).
- `docs/transcript-analysis.md` — `rearm-backtest` description's cap mention (M4).
- `README.md` — two prose mentions (M4).
- `docs/cost-levers-considered.md` — new dated row (M4); do not edit existing rows.
- `CHANGELOG.md` — Unreleased entry (M4).

**Create:**

- `docs/case-studies/pr-cost-context-bucket.md` (or fold into
  `docs/case-studies/token-cost-per-pr.md` if that file already exists by
  implementation time from the parallel `token-cost-per-pr-study.md` work) —
  the M2 bucket table, method, sample sizes per bucket, and the
  recent-low-context-cohort comparison, in this repo's established
  question/method/numbers/limits shape.

**Do not touch (Axis 3, read-only historical records):** any `.claude/plans/*.md`
file already merged — `handoff-boundary-decision-rule.md`,
`absolute-token-handoff-threshold.md`, `token-cost-per-pr-study.md`, and every
other plan surfaced by this session's `grep` for "360000" that lives under
`.claude/plans/`.

## Verification

1. `pr-cost --record --machine-label <label>` completes; ledger row count is
   sanity-checked against `gh pr list --state merged` for the same window.
2. M2's bucket report reproduces from the populated ledger; every bucket cited
   in the plan's final grounding carries a stated sample size.
3. `../../../.venv/bin/pytest claude/.claude/hooks/tests/test_nudge_handoff_near_context_cap.py claude/.claude/hooks/tests/test_doc_counts.py claude/.claude/scripts/tests/test_transcript_analysis.py` passes with updated assertions — `test_doc_counts.py` included explicitly since M4 calls its four-phrase regex match a hard constraint, not left to an eventual full-suite CI run to catch a missed phrasing.
4. `../../../.venv/bin/ruff check claude/.claude/` and
   `scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck` clean.
5. Manual `nudge-handoff-near-context-cap.sh --check` against a synthetic
   transcript sized just above and just below the new cap confirms it actually
   binds where M3 intends, on a 1M-window model ID.
6. `docs/handoff-nudge.md`'s worked `--check` example output matches the new
   threshold value.

## Out of scope

- The escalation ladder (`HANDOFF_NUDGE_BLOCK_AFTER`) and any hard-block
  conversion of this nudge — owned by the concurrent `handoff-hard-block`
  session per this plan's Coordination note.
- Phases 2–4 of `token-cost-per-pr-study.md` (hand-coded novelty/ambiguity
  ratings on a stratified sample, the client-facing charted artifact) — this
  plan only needs Phase 1's capture mechanism run against the corpus, not the
  full case study those later phases build toward.
- Re-tuning `HANDOFF_NUDGE_REARM_SPACING` or the 40%-of-window percentage arm
  for 200k-window models — unaffected by this change as long as the chosen cap
  clears 80000 (the 200k arm's own threshold).
- The rearm-backtest lag discrepancy (30,624 vs. 52,184 tokens) the concurrent
  session flagged while grounding its own `BLOCK_AFTER` work — this plan's
  measurement doesn't read `.handoff-nudge.log` or `REARM_SPACING` at all, so
  it's unaffected either way; left for whoever next touches that figure.
- Raising `cleanupPeriodDays` to slow future transcript-window data loss —
  already evaluated and declined in `token-cost-per-pr-study.md`'s own Out of
  Scope, for reasons unrelated to this plan (recovers none of the
  already-deleted history, grows scan cost linearly, is a stowed-settings
  change with its own review surface). This plan does not reopen that
  decision.
