# Per-PR cost forensics: dissecting a disproportionately expensive pull request

*Part of the [claude-config case studies](../case-studies.md).*

**The problem.** A small, single-purpose pull request in a private project consumed a disproportionate amount of Claude Code spend at list price. The engineer suspected a foundational defect in this claude-config harness, in that project's own setup, or both, rather than a one-off. This study dissects every non-trivial cause and records the result durably: the branch's own transcripts self-delete on the default 30-day retention window, so without a written record the evidence for this answer would have expired before the question could be revisited.

**Harness version.** The branch ran against a stowed harness pinned at commit `0ce2cfe8` (`origin/main`'s HEAD immediately before the study window opened). The harness was not constant across the study window: this repo's own default branch advanced through numerous commits during the window (independently visible in this repo's public git history), so treat the pinned commit above as the window's starting reference point, not a constant across the full study.

**Short answer.**

- **Review-loop iteration count, carried through context-prefix amplification on every subsequent turn, is the largest attributed causal mechanism** — not the cache-TTL hypothesis the engineer started with. Most of the branch's own spend does not trace to any single named causal mechanism at all; see "Ranked causal decomposition" for the ranking and "Why this generalizes" for the corpus-level evidence this isn't a one-branch anecdote.
- **One instrumentation defect was fixed before this study could trust its own numbers.** `_dispatch_usage_summary` priced without request-ID deduplication, overstating per-dispatch and per-agent-type dollar figures. Fixed early in this study's own plan, before any other figure was trusted; every figure below is re-derived post-fix.
- **The account this branch ran on cannot receive the 1-hour cache tier at all**, regardless of any config change — closing the engineer's original hypothesis as unreachable rather than merely unconfigured.
- **The private project's own repo surface is mostly a red herring**, with one real, small, unconditional cost (its own skill descriptions adding to the per-turn context floor) and one conditional cause this study's own verification step ruled out (see "Is the private project's own surface a cause or a red herring?").
- **Against this repo's own ledger, this branch is a genuine outlier**, not a typical instance of a known pattern — see "Baseline: how unusual was this branch, really?" and "Why this generalizes" for the corpus-level evidence that review-loop cost is a real, recurring driver, not an artifact of this one branch.

## How this was measured

The full method, including every fix, new attribution surface, and gate below, was pre-registered in `.claude/plans/pr-cost-forensics.md` before any figure in this document was cited. Three sequenced steps:

1. Correct one pricing defect and one mislabeled column.
2. Add `subagent-mix --per-dispatch` (new) and reuse the already-shipped `review-round-cost`.
3. Re-measure against real branch data and author this record.

No pipeline-behavior change ships from this study — recommendations are named but deferred to a separate reviewed plan.

Every figure below was re-derived against the command that produces it at the moment this document was written, not carried forward from an earlier discovery-phase estimate. This document follows this repo's own redaction rules for private-project-identifying content — see CLAUDE.md's "Also redact structural fingerprints and provenance" section and `docs/private-project-redaction.md`. No figure below is tied to the one private branch's own absolute dollar spend, share, ratio, or count; only qualitative findings and structural facts about that branch survive. The only absolute dollar figures this document cites are this repo's own public `pr-cost` ledger and this repo's own single-account review-round history — the same public-corpus footing as the ledger, not the pooled-tooling-measurement carve-out.

## Ranked causal decomposition

Three partitions of the branch's spend were examined, and they are **orthogonal, not additive** — they must not be summed against each other:

- Token class: cache-write, cache-read, output, input share.
- Thread: main vs. subagent.
- Causal mechanism: idle-gap cache rebuild, a sub-slice of cache-write — the only mechanism this study could isolate and name.

Sources: `transcript-analysis.py cost --branches <B>` for the token-class and thread splits, `cache-rebuild --projects <glob> --since 60d` for the idle-gap figures.

Ranked, with what each is grounded in:

1. **Review-round count and reviewer fan-out.** The largest attributed mechanism: the entire subagent thread, a majority of which is reviewer/writer dispatch work. Very likely a further share of the main thread too, since each skill invocation (`/code-review`, `/plan-review`, `/ready-for-review`, and others) loads a large body into a prefix that is then re-read on every subsequent turn. Measured directly this session via `review-round-cost --branches <B>`: a real but minority share of total branch dollars falls inside an explicit review-round window, with most falling outside any round window — most plausibly implementation work interleaved between review passes that this boundary rule correctly excludes from counting as review overhead. Rank #1 rests on the subagent-thread floor, not on the review-round-window share — see Gate result 4, below, and "Why this generalizes" for the corpus-level version of this same finding.
2. **No incremental review credit.** Not a separate cost bucket — the mechanism that gives #1 its count. The review-marker mechanism hashes the whole staged (or cumulative) diff on every pass, so N edits force N full re-runs at full reviewer fan-out. The cumulative pre-handoff pass also re-invalidates on its own fix commits.
3. **Unattributed cache-write residual.** Cache-write share minus the portion classified as idle-gap rebuild. The governing prior is [`cold-cache-attribution.md`](cold-cache-attribution.md): a substantial share of cache-write spend corpus-wide is sub-60-second prefix invalidation, most of it with no attributable harness cause. This branch's own residual is judged predominantly the same already-documented, already-unexplained cold population, not a branch-specific defect — see Gate result 3 for the two tests that bound this.
4. **Idle-gap cache rebuild.** The engineer's original hypothesis — real, but third-ranked, not first. Unreachable on this account regardless of any config change: it has never once recorded a non-zero one-hour cache-write token. One finding that inverts the corpus-wide pattern: none of the branch's idle-gap rebuilds had a concurrent session active, where the corpus-wide finding (`cost-levers-considered.md`, "Context cost root cause") is predominantly concurrent-session-driven. This branch's idle gaps are genuine operator breaks, not session-switching — the opposite of the typical corpus.
5. **Fixed per-turn context floor.** This repo's own always-loaded instruction and skill surface, plus a further addition from a project's own skill descriptions — a project's own skill descriptions add to the always-resident context floor on every turn, true generically and verifiable from this repo alone. Not separately priceable on its own; it is the multiplicand that makes turn count expensive.

**Ruled out:** effort pins (output is a small share of total branch spend — halving every high-effort reviewer's output cannot reach the top rank); model routing (Opus usage is a small minority of spend); compaction (never occurred on this branch); private-repo size (the branch touched none of the project's bulk-dependency surface).

## Instrument corrections

Two defects in `transcript-analysis.py` were found and fixed before this study's own figures could be trusted, and are recorded here so a future reader of an older report knows which numbers moved and why.

- **`_dispatch_usage_summary` pricing defect (fixed).** The function streamed its JSONL line-by-line and never deduplicated by request ID before pricing, while every other pricing path in the tool does. This double-counted cache-class tokens on any multi-record API response and over-summed `output_tokens` across a run whose values only reach the billed figure on the final record, materially overstating every per-dispatch and per-agent-type dollar figure the function fed. The affected call sites were `_dispatch_usage_summary`'s own callers, i.e. `subagent-mix --per-dispatch`. Any per-dispatch or per-agent-type dollar total printed by this tool before this fix landed is unreliable and should not be cited forward; see [`design-decisions/plan-architect-consult-mode.md`](../design-decisions/plan-architect-consult-mode.md) for the specific downstream metric this discontinuity affects.
- **`duration`'s "Sessions" column, mislabeled (fixed).** The column counted activity bursts separated by an idle-gap threshold, not distinct session files — a label defect, not a data defect, and nothing downstream consumed it as a session count. Renamed; the computed value is unchanged.
- **Per-review-round cost duplication, closed.** `review-round-cost` (pre-existing) is the correct instrument for attributing branch dollars to review rounds; see Gate result 4 for its output on this branch.

Two further count disagreements were investigated and found to be definitional, not defects, and are recorded as scope notes rather than fixed: `cost`'s and `subagents`' differing turn-count denominators (see Gate result 1), and `review-trace`'s six-skill scope, which by design excludes `handoff`/`pr-description` events.

## Gate results

Five re-derivation gates were run against real branch data before this document cited a figure they bear on.

1. **Turn-count denominator gap, resolved.** `subagents --branches <B>` (branch-scoped, not the discovery phase's `--projects`-glob approximation) and `cost`'s own priced-turn count use different denominators: `subagents` counts every post-dedup assistant record, split by thread, while `cost` counts only turns carrying a priced `usage` block. The two reconcile exactly once that difference is accounted for — a denominator difference, not a contradiction.
2. **Nested private-project instruction file, plausibly never loaded.** The private repo carries a nested instruction file, sitting under a subtree the branch's own changes never touch. A grep of every session file for a `Read`/`Grep`/`Glob` call with a path argument under that subtree returned zero matches across all sessions. One session does quote content from that subtree, but exclusively via shell-executed `grep`/`cat`, which carries no structural path argument the harness's auto-loader could react to. The nested file is not promoted to a ranked cause.
3. **Unattributed cache-write residual, bounded two ways.** Re-running the idle-gap rebuild scan at a threshold well below the default finds a broader population of moderate-sized rebuilds, not a few huge ones — consistent with ordinary prefix-cache churn rather than a single runaway cause. Separately, counting this repo's own default-branch ref moves against every consecutive assistant-turn-pair gap across the branch's sessions finds a small, bounded fraction straddling a ref move — consistent with the corpus-wide cold-cache contribution `cold-cache-attribution.md` already measured, not a larger, branch-specific effect. (This repo's harness state advanced through numerous commits during the measurement period — see "Harness version," above, whose pinned commit is a reference point for that reason, not a withheld study-window secret. The ref-move count itself is omitted here for the same reason every other per-engagement count is omitted from this document: see CLAUDE.md's "Also redact structural fingerprints and provenance" section.)
4. **Main-thread cost is not predominantly review-loop-driven under a strict definition — rank #1 restated, not overturned.** `review-round-cost` attributes a real but minority share of total branch dollars to an explicit review-round window, with most falling outside it. The #1 rank's underlying subagent-thread floor is unaffected by this correction, so rank #1 is restated to "largest attributed mechanism," not "majority of spend," not overturned. See "Why this generalizes," below, for the corpus-level version of this same finding.
5. **Every quantitative claim in this document was re-derived against the command that produces it, named alongside the claim, at the time this document was written** — not carried forward from the plan's own discovery-phase figures, which predate the pricing fix and a week of further corpus growth.

## Is the private project's own surface a cause or a red herring?

**Mostly red herring, with one real, small, unconditional term.**

- **Red herring:** the private project's own bulk repo surface — its dependency footprint, file count, and on-demand rule files. The branch touched none of it, and repo size does not enter the model's prompt: reading the tree does not predict what gets read at runtime. `lsp-token-reduction-feasibility.md` already refuted this exact inference shape on this repo's own transcripts — portfolio composition does not predict read composition (see [`cost-levers-considered.md`](../cost-levers-considered.md)).
- **Real, small, unconditional:** a project's own skill descriptions add to the always-resident context floor on every turn — true generically, and verifiable from this repo alone. The one item unconditionally on the wire regardless of what the branch's own changes touched.
- **Tested and closed, not a cause:** the nested instruction file (Gate result 2, above) plausibly never loaded, since nothing in any session read a path under its subtree structurally.
- **A useful negative:** the account this branch ran on already pins its model default to Sonnet. No model-routing lever remains to pull.

## Baseline: how unusual was this branch, really?

**Leg 1 — claude-config's own `pr-cost` ledger, the only leg permitted to publish absolute dollars, since it is this repo's own public corpus rather than a private one.** Across the full population of this repo's own recorded ledger rows (N=219, every priced PR this repo's own ledger has recorded, not a shape-filtered subset), total dollars (summed across every priced token class):

- range $2.00–$187.60
- mean $43.02
- median $33.26
- stdev $36.84

The branch under study is far more expensive than any comparably-scoped engagement in this repo's own ledger — a different tier of spend than this population's own recorded range.

**Legs 2 and 3 — deferred.** A per-branch activity ranking and a nearest-neighbor token-class comparison, both scoped to the account this branch ran on, were explored during this study but are not published here: neither instrument can be run at the pooled, cross-account scope this repo's own redaction rules require for a figure with private-account provenance. Deferred to future cross-account pooled-aggregate tooling, not carried forward as an unredactable branch-level comparison.

See CLAUDE.md's "Redact private-project-identifying content" section for the redaction discipline this document follows throughout.

## Why this generalizes

The ranked decomposition above is one branch's own forensic account — real for this branch, but not by itself evidence that review-loop cost is a general driver across this repo's history. Two corpus-level figures back that broader claim. Both rest on this repo's own single-account corpus, not a cross-account pooled measurement, so each may report absolute counts and dollar figures directly. The two figures' safety guarantees are not the same kind, though both hold here. The first figure reuses Baseline Leg 1's own ledger, safe by construction: a separate physical file per account, with no union code path at all. The second figure's safety instead depends on `--config-dir` overriding the scan root before the subcommand runs. That override is structural for any subcommand outside `transcript_analysis/scope.py`'s `_SUBCOMMANDS_REFUSING_TOP_LEVEL_CONFIG_DIR` tuple. Both a code trace and the live header quoted in Sources, below, verify it holds here.

- **Cost tracks process, not diff size.** Across this repo's own `pr-cost` ledger (N=219), total branch dollars correlate far more strongly with turn count (Pearson r = 0.908) than with commit count (Pearson r = 0.683). A branch's own cost is driven predominantly by how many turns it took, not by how much code it produced — the same asymmetry this branch's own ranked decomposition finds at N=1.
- **Review rounds are a real, recurring cost center, not a one-branch anomaly.** Across this repo's own review-round history, scoped to a single account (171 branches, 1,462 rounds), the mean branch runs 8.55 review rounds. Rounds by review type:

  - 805 code-review
  - 319 plan-review
  - 338 ready-for-review

  Mean cost per round by review type:

  - $1.98 code-review
  - $1.73 plan-review
  - $1.41 ready-for-review

  75.1% of branch dollars fall outside every review-round window — ordinary implementation work, not review overhead. Reviewer-dispatch dollars specifically inside round windows account for 11.4% of total branch dollars. This is the same shape Gate result 4, above, found on this one branch: review-loop cost is real and recurring, but it is the largest *attributed* mechanism, not the majority of spend.

Read together, these corpus-level figures — not any figure tied to the one private branch — are the statistical backing for "review-loop cost is a genuine driver."

## Recommended, not implemented here

This study's own plan puts every pipeline-behavior change out of scope, on the grounds that a cost investigation is a mandate to measure, not to redesign the review pipeline unreviewed. Recommended as follow-up work, not built here:

- **Incremental review credit.** The review-marker mechanism re-hashes and re-runs a full review pass on every edit, however small. A delta-aware credit mechanism is the single highest-leverage lever named by this study's own ranked decomposition (#1 and #2 together), but designing one safely — without silently skipping a review a reviewer would have flagged — is its own reviewed plan, not a corollary of this one.
- **Capping reviewer fan-out**, and **narrowing `ready-for-review`'s cumulative pass** to the increment since the last clean review rather than the whole branch diff, are both named candidates a future plan should evaluate against the per-review-round cost surface this study added.
- **`pr-cost --record` support for an open PR**, so a branch like this one's own cost trajectory is visible before it merges rather than only after — deliberately excluded here as a ledger schema migration on an append-only file, not a phase of this study.

## Sources

- **`claude/.claude/scripts/transcript-analysis.py`** — `cost --branches` (token-class and thread splits), `subagents --branches` (turn-count reconciliation), `cache-rebuild --since` (idle-gap rebuild share, both thresholds), `review-round-cost --branches` (per-review-round cost attribution), all run against the account this branch ran on's own declared transcript roots, restricted with `--projects`/`--branches` rather than a bare `--config-dir`. The corpus-level `review-round-cost --this-repo` invocation below instead passes an explicit single-account `--config-dir`, confirmed single-root by its own header: `REVIEW ROUND COST SOURCES (this repo (137 project dirs); 1 root (~/.claude/transcript-config-dirs declared but contributed no additional root))`.
- **`~/.claude/pr-cost-ledger.tsv`** — Baseline Leg 1 and "Why this generalizes," this repo's own ledger, the corpus this study publishes in absolute dollars and in a single-account correlation.
- **`review-round-cost --this-repo`** — "Why this generalizes," this repo's own single-account review-round aggregate, run against an explicit single-account `--config-dir` — the same public-corpus footing as Baseline Leg 1, not the pooled-tooling-measurement carve-out.
- **[`cold-cache-attribution.md`](cold-cache-attribution.md)** — the governing prior for the unattributed cache-write residual (rank #3) and the ref-move cold-cache mechanism (Gate result 3).
- **`.claude/plans/pr-cost-forensics.md`** — this study's own plan, including the full assumption ledger and the pricing and mislabeling fixes.
- **[`design-decisions/plan-architect-consult-mode.md`](../design-decisions/plan-architect-consult-mode.md)** — the `subagent-mix` `Actual$` discontinuity this study's own pricing fix introduces into that metric's history.
- **[`cost-levers-considered.md`](../cost-levers-considered.md)** — this study's own lever-table entry, including the corpus-wide concurrent-session-driven idle-gap finding this branch's own zero-concurrent result inverts.
