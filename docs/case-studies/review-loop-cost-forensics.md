# Per-PR cost forensics: dissecting a $100-class pull request

*Part of the [claude-config case studies](../case-studies.md).*

**The problem.** A small, single-purpose pull request in a private project's monorepo — a CI workflow addition and its plan file — consumed a $100-class amount of Claude Code spend at list price. The engineer suspected a foundational defect in this claude-config harness, in that project's own setup, or both, rather than a one-off. This study dissects every non-trivial cause and records the result durably: the branch's own transcripts self-delete on the default 30-day retention window, so without a written record the evidence for this answer would have expired before the question could be revisited.

**Harness version.** The branch ran against a stowed harness pinned at commit `0ce2cfe8` (`origin/main`'s HEAD immediately before the study window opened). The harness was not constant across the study window: this repo's own default branch advanced through numerous commits during the window (independently visible in this repo's public git history), so treat the pinned commit above as the window's starting reference point, not a constant across the full study.

**Short answer.**

- **Only 16.1% of the branch's spend is attributed to a named causal mechanism at all.** The dominant term is review-loop iteration count, carried through context-prefix amplification on every subsequent turn — not the cache-TTL hypothesis the engineer started with.
- **One instrumentation defect was fixed before this study could trust its own numbers.** `_dispatch_usage_summary` priced without request-ID deduplication, overstating per-dispatch and per-agent-type dollar figures by roughly 1.8x. Fixed early in this study's own plan, before any other figure was trusted; every figure below is re-derived post-fix.
- **The account under study cannot receive the 1-hour cache tier at all**, regardless of any config change — closing the engineer's original hypothesis as unreachable rather than merely unconfigured.
- **The private project's own repo surface is mostly a red herring**, with one small, real, unconditional cost (its skill-description baseline) and one conditional cause this study's own verification step ruled out (see "Is the private project's own surface a cause or a red herring?").
- **Against both available baselines, this branch is a genuine outlier**, not a typical instance of a known pattern — see "Baseline: how unusual was this branch, really?".

## How this was measured

The full method, including every fix, new attribution surface, and gate below, was pre-registered in `.claude/plans/pr-cost-forensics.md` before any figure in this document was cited. Three sequenced steps: correct one pricing defect and one mislabeled column; add `subagent-mix --per-dispatch` (new) and reuse the already-shipped `review-round-cost`; re-measure against real branch data and author this record. No pipeline-behavior change ships from this study — recommendations are named but deferred to a separate reviewed plan.

Every figure below was re-derived against the command that produces it at the moment this document was written, not carried forward from an earlier discovery-phase estimate. Where a figure is described as a private branch's own measurement, only its share or ratio form is published — never an absolute dollar amount — per this study's own redaction rule: a figure is a property of the *work* (retained) if it measures a process — session, dispatch, round, or turn count — and a property of the *artifact* (generalized to an order of magnitude, or dropped) if it measures the repo itself — byte size, file count, path shape. The one exception is claude-config's own `pr-cost` ledger, the only corpus this study is permitted to publish in absolute dollars.

## Ranked causal decomposition

Three partitions of the branch's spend are available, and they are **orthogonal, not additive** — they must not be summed against each other.

| Partition | Slices | Exhaustive? |
|---|---|---|
| Token class | cache_write_5m 43.8% / cache_read 36.3% / output 19.9% / input ~0% | yes |
| Thread | main 59.8% / subagent 40.2% | yes |
| Causal mechanism | idle-gap rebuild excess 16.1% (a sub-slice of cache_write_5m) | **no — this is the whole of it** |

Sources: the token-class shares and the thread split both come from `transcript-analysis.py cost --projects <glob> --branches <B>`. The idle-gap figures come from `cache-rebuild --projects <glob> --since 60d` at its default 100,000-token threshold.

Ranked, with what each is grounded in:

1. **Review-round count and reviewer fan-out.** Floor: 40.2% — the entire subagent thread, a majority of which is reviewer/writer dispatch work. Very likely a further share of the 59.8% main thread too, since each skill invocation (`/code-review`, `/plan-review`, `/ready-for-review`, and others) loads a large body into a prefix that is then re-read on every subsequent turn. **Measured directly this session:** `review-round-cost --branches <B>` attributes 33.9% of total branch dollars to an explicit review-round window, with 66.1% falling outside any round window, most plausibly implementation work interleaved between review passes that this boundary rule correctly excludes from counting as review overhead. This is a third of total branch spend attributed to explicit review activity specifically, not a majority — "predominantly review-loop-driven" overstates it. Rank #1 still holds on the strength of the 40.2% subagent-thread floor, the largest single named mechanism among every rank below, but the honest framing is "the largest attributed single mechanism," not "the majority of branch spend."
2. **No incremental review credit.** Not a separate cost bucket — the mechanism that gives #1 its count. The review-marker mechanism hashes the whole staged (or cumulative) diff on every pass, so N edits force N full re-runs at full reviewer fan-out, and the cumulative pre-handoff pass re-invalidates on its own fix commits.
3. **Unattributed cache-write residual: roughly a quarter of branch spend.** Cache-write share minus the portion classified as idle-gap rebuild. The governing prior is [`cold-cache-attribution.md`](cold-cache-attribution.md): 40–48% of cache-write spend corpus-wide is sub-60-second prefix invalidation, roughly two-thirds of it with no attributable harness cause. This branch's residual is judged predominantly the same already-documented, already-unexplained cold population, not a branch-specific defect — see Gate result 3 for the two tests that bound this.
4. **Idle-gap cache rebuild: 16.1% of branch spend; the 5-minute-to-1-hour-tier-addressable sub-slice is roughly 13.6%.** The engineer's original hypothesis — real, but third-ranked, not first. Unreachable on this account regardless of any config change: it has never once recorded a non-zero one-hour cache-write token. **One finding that inverts the corpus-wide pattern:** none of the branch's idle-gap rebuilds — a low-tens count — had a concurrent session active, where the corpus-wide finding (`cost-levers-considered.md`, "Context cost root cause") is 92.9% concurrent-session-driven. This branch's idle gaps are genuine operator breaks, not session-switching — the opposite of the typical corpus.
5. **Fixed per-turn context floor.** A multi-thousand-estimated-token baseline on every turn (byte-count-derived, not tokenizer-exact) — this repo's own always-loaded instruction and skill surface, plus a further addition from the private project's own several-dozen skill descriptions. Not separately priceable on its own; it is the multiplicand that makes turn count expensive.

**Ruled out, each with the figure that forecloses it:** effort pins (output is only 19.9% of total branch spend — halving every high-effort reviewer's output cannot reach the top rank); model routing (Opus 5.0%); compaction (zero on this branch); private-repo size (the branch touched none of the project's bulk-dependency surface).

## Instrument corrections

Two defects in `transcript-analysis.py` were found and fixed before this study's own figures could be trusted, and are recorded here so a future reader of an older report knows which numbers moved and why.

- **`_dispatch_usage_summary` pricing defect (fixed).** The function streamed its JSONL line-by-line and never deduplicated by request ID before pricing, while every other pricing path in the tool does. This double-counted cache-class tokens on any multi-record API response and over-summed `output_tokens` across a run whose values only reach the billed figure on the final record — a roughly 1.8x overstatement on every per-dispatch and per-agent-type dollar figure the function fed (`subagent-mix --per-dispatch`, and the per-round join before its own independent dedup step landed). Any per-dispatch or per-agent-type dollar total printed by this tool before this fix landed is unreliable and should not be cited forward; see [`design-decisions/plan-architect-consult-mode.md`](../design-decisions/plan-architect-consult-mode.md) for the specific downstream metric this discontinuity affects.
- **`duration`'s "Sessions" column, mislabeled (fixed).** The column counted activity bursts separated by an idle-gap threshold, not distinct session files — a label defect, not a data defect, and nothing downstream consumed it as a session count. Renamed; the computed value is unchanged.
- **Per-review-round cost duplication, closed.** `review-round-cost` (pre-existing) is the correct instrument for attributing branch dollars to review rounds; see Gate result 4 for its output on this branch.

Two further count disagreements were investigated and found to be definitional, not defects, and are recorded as scope notes rather than fixed: `cost`'s and `subagents`' differing turn-count denominators (see Gate result 1), and `review-trace`'s six-skill scope, which by design excludes `handoff`/`pr-description` events.

## Gate results

Five re-derivation gates were run against real branch data before this document cited a figure they bear on.

1. **Turn-count denominator gap, resolved.** `subagents --branches <B>` (branch-scoped, not the discovery phase's `--projects`-glob approximation) reports 818 main-thread turns and 815 sidechain turns, summing to 1,633 — exactly matching the arithmetic prediction against `cost`'s own 1,517 priced turns. The residual (116 records) is usage-less assistant records that `cost` correctly excludes and `subagents` correctly counts; a denominator difference, not a contradiction.
2. **Nested private-project instruction file, plausibly never loaded.** The private repo carries a nested instruction file several times the size of this repo's own root instruction file, sitting under a subtree the branch's own changes never touch. A grep of every session file for a `Read`/`Grep`/`Glob` call with a path argument under that subtree returned zero matches across all sessions. One session does quote content from that subtree, but exclusively via shell-executed `grep`/`cat`, which carries no structural path argument the harness's auto-loader could react to. The nested file is not promoted to a ranked cause.
3. **Unattributed cache-write residual, bounded two ways.** Re-running the idle-gap rebuild scan at a threshold well below the default (30,000 vs. 100,000 cache-write tokens) finds a broader population of moderate-sized rebuilds, not a few huge ones — consistent with ordinary prefix-cache churn rather than a single runaway cause. Separately, counting this repo's own default-branch ref moves against every consecutive assistant-turn-pair gap across the branch's sessions finds a small, bounded fraction straddle a ref move — consistent with the corpus-wide 3–5% cold-cache contribution `cold-cache-attribution.md` already measured, not a larger, branch-specific effect. (This repo's own commit history in the study window is independently public; the exact count is withheld here because pairing it with the study's own date window would let a reader cross-reference the two into an identifying fingerprint neither figure alone would give away.)
4. **Main-thread cost is not predominantly review-loop-driven under a strict definition — rank #1 restated, not overturned.** `review-round-cost` attributes 33.9% of total branch dollars to an explicit review-round window (66.1% outside it) — a third of spend, not a majority. The #1 rank's underlying 40.2% subagent-thread floor is unaffected by this correction, so rank #1 is restated to "largest attributed mechanism," not "majority of spend," not overturned.
5. **Every quantitative claim in this document was re-derived against the command that produces it, named alongside the claim, at the time this document was written** — not carried forward from the plan's own discovery-phase figures, which predate the pricing fix and a week of further corpus growth. Where a figure changed only within measurement noise of the discovery-phase estimate (the token-class and thread splits above moved by roughly 0.2 percentage points), the newer figure is the one cited.

## Is the private project's own surface a cause or a red herring?

**Mostly red herring, with one real, small, unconditional term.**

- **Red herring:** the private project's multi-megabyte committed dependency bundle, its large file count, and its collection of on-demand rule files. The branch touched none of them, and repo size does not enter the model's prompt — reading the tree does not predict what gets read at runtime.
- **Real, small, unconditional:** the private project's several dozen skill descriptions carry an always-resident token cost, adding a double-digit-percent increase to this repo's own always-loaded baseline on every one of the branch's roughly 1,500 priced turns. The one item unconditionally on the wire regardless of what the branch's own changes touched.
- **Tested and closed, not a cause:** the nested instruction file (Gate result 2, above) plausibly never loaded, since nothing in any session read a path under its subtree structurally.
- **A real finding, but not a cost cause:** the private repo's own local settings accumulated a large number of one-off shell-command allow entries. Settings content is not part of the model's prompt, so this does not belong in the cost narrative — it is reported to the engineer separately as a security observation against this repo's own permissions-scoping guidance, not remediated here.
- **A useful negative:** the account under study already pins its model default to Sonnet, consistent with the observed 95.0% Sonnet spend share. No model-routing lever remains to pull.

## Baseline: how unusual was this branch, really?

Three legs, chosen because each is produced by an instrument that already works at the scope this study needed, without a cross-repo redaction bypass.

**Leg 1 — claude-config's own `pr-cost` ledger (the only leg permitted to publish absolute dollars).** Filtered to this repo's own rows carrying a plan file, a risk-surface flag, and a small (≤5, this ledger's own corpus-wide median) changed-file count — the closest available population to "a small PR with a plan file and a CI-adjacent change" — 14 rows, all under one rate stamp, so no cross-rate-stamp re-derivation was needed. Total dollars (summed across every priced token class) range **$7.65–$50.51**, mean **$23.79**, median **$18.41**. The branch under study, at a $100-class total, is several times this population's own maximum and roughly an order of magnitude above its mean — a different tier of spend than any row in this comparable population.

**Leg 2 — the account's own branch-activity ranking.** Ranked by active minutes across every branch with local corpus activity on the account under study (roughly two dozen comparable branches, excluding `HEAD` and the trunk branch as non-comparable), the branch under study ranks **#1**, roughly **12% above the runner-up**. This branch was not merely expensive in absolute terms — it was the single most active branch on the account by session-active time, by a clear margin.

**Leg 3 — class decomposition against the nearest-neighbor branches.** `cost --branches` restricted to the two next-most-active branches on the same account, combined: cache_read 50.0% / cache_write_5m 37.6% / output 12.4% / input ~0%, thread split main 61.2% / subagent 38.8%. The branch under study's own class mix (43.8% / 36.3% / 19.9% / ~0%, main 59.8% / subagent 40.2% — see the decomposition table above) sits close to this neighbor population on the thread split, but noticeably higher on output share and lower on cache-read share — consistent with a branch that ran more review-and-fix iterations (each round's own output tokens) rather than one long, mostly-reading investigation.

Branch names, this account's identity, and the repo identity are withheld throughout — see this repo's own redaction discipline (`CLAUDE.md`, "Redact private-project-identifying content").

## Recommended, not implemented here

This study's own plan puts every pipeline-behavior change out of scope, on the grounds that a cost investigation is a mandate to measure, not to redesign the review pipeline unreviewed. Recommended as follow-up work, not built here:

- **Incremental review credit.** The review-marker mechanism re-hashes and re-runs a full review pass on every edit, however small. A delta-aware credit mechanism is the single highest-leverage lever named by this study's own ranked decomposition (#1 and #2 together), but designing one safely — without silently skipping a review a reviewer would have flagged — is its own reviewed plan, not a corollary of this one.
- **Capping reviewer fan-out**, and **narrowing `ready-for-review`'s cumulative pass** to the increment since the last clean review rather than the whole branch diff, are both named candidates a future plan should evaluate against the per-review-round cost surface this study added.
- **`pr-cost --record` support for an open PR**, so a branch like this one's own cost trajectory is visible before it merges rather than only after — deliberately excluded here as a ledger schema migration on an append-only file, not a phase of this study.

## Sources

- **`claude/.claude/scripts/transcript-analysis.py`** — `cost --branches` (token-class and thread splits), `subagents --branches` (turn-count reconciliation), `cache-rebuild --since` (idle-gap rebuild share, both thresholds), `review-round-cost --branches` (per-review-round cost attribution), `duration` (Baseline Leg 2), all run against the account under study's own declared transcript roots, restricted with `--projects`/`--branches` rather than a bare `--config-dir`.
- **`~/.claude/pr-cost-ledger.tsv`** — Baseline Leg 1, this repo's own ledger, the one corpus this study publishes in absolute dollars.
- **[`cold-cache-attribution.md`](cold-cache-attribution.md)** — the governing prior for the unattributed cache-write residual (rank #3) and the ref-move cold-cache mechanism (Gate result 3).
- **`.claude/plans/pr-cost-forensics.md`** — this study's own plan, including the full assumption ledger, the pre-registered redaction rule, and the pricing and mislabeling fixes.
- **[`design-decisions/plan-architect-consult-mode.md`](../design-decisions/plan-architect-consult-mode.md)** — the `subagent-mix` `Actual$` discontinuity this study's own pricing fix introduces into that metric's history.
- **[`cost-levers-considered.md`](../cost-levers-considered.md)** — this study's own lever-table entry, including the corpus-wide 92.9%-concurrent idle-gap finding this branch's own zero-concurrent result (across a low-tens count of idle-gap rebuilds) inverts.
