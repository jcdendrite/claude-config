# Per-PR cost forensics: dissecting a disproportionately expensive pull request

*Part of the [claude-config case studies](../case-studies.md).*

**The problem.** A pull request in a private project consumed a disproportionate amount of Claude Code spend at list price. The engineer suspected a foundational defect in this claude-config harness, in that project's own setup, or both, rather than a one-off. This study dissects every non-trivial cause and records the result durably: the branch's own transcripts self-delete on the default 30-day retention window, so without a written record the evidence for this answer would have expired before the question could be revisited.

**Harness version.** The branch ran against a stowed harness pinned at commit `0ce2cfe8` (`origin/main`'s HEAD immediately before the study window opened). The harness was not constant across the study window: this repo's own default branch advanced through numerous commits during the window (independently visible in this repo's public git history), so treat the pinned commit above as the window's starting reference point, not a constant across the full study.

**Short answer.**

- **Review-loop iteration count, carried through context-prefix amplification on every subsequent turn, is the largest attributed causal mechanism** — not the cache-TTL hypothesis the engineer started with. Most of the branch's own spend does not trace to any single named causal mechanism at all; see "Ranked causal decomposition" for the ranking and "Why this generalizes" for the corpus-level evidence this isn't a one-branch anecdote.
- **One instrumentation defect was corrected so this study could trust its own numbers.** `_dispatch_usage_summary` priced without request-ID deduplication, overstating per-dispatch and per-agent-type dollar figures. Every figure below was computed with the corrected function.
- **The one-hour cache tier is a config-selectable lever, not a structural block** (`promptCacheTtl`/`ENABLE_PROMPT_CACHING_1H`). Selecting it would address only idle-gap rebuilds, which rank below review-loop iteration (see "Ranked causal decomposition").
- **The private project's own repo surface is mostly a red herring**, with one real, small, unconditional cost and one conditional cause this study's own verification step ruled out (see "Is the private project's own surface a cause or a red herring?").
- **Review-loop cost is a real, recurring driver across this repo's own history**, not an artifact of this one branch — see "Baseline: this repo's own cost distribution" and "Why this generalizes" for the corpus-level evidence.

## How this was measured

The commands behind every figure are named alongside it in the sections below and collected in Sources. The method is recorded in `.claude/plans/pr-cost-forensics.md`. No pipeline-behavior change ships from this study — recommendations are named but deferred to a separate reviewed plan.

Gate result 5 covers how every figure was re-derived. This document follows this repo's own redaction rules for private-project-identifying content — see CLAUDE.md's "Also redact structural fingerprints and provenance" section and `docs/private-project-redaction.md`. No figure below is tied to the one private branch's own absolute dollar spend, share, ratio, or count; only qualitative findings and structural facts about that branch survive. The only absolute dollar figures this document cites are this repo's own `pr-cost` ledger and this repo's own single-account review-round history, published under the owner authorization recorded in Sources.

## Ranked causal decomposition

Three partitions of the branch's spend were examined, and they are **orthogonal, not additive** — they must not be summed against each other:

- Token class: cache-write, cache-read, output, input share.
- Thread: main vs. subagent.
- Causal mechanism: idle-gap cache rebuild, a sub-slice of cache-write — the only mechanism this study could isolate and name.

Sources: `transcript-analysis.py cost --branches <B>` for the token-class and thread splits, `cache-rebuild --projects <glob> --since 30d` for the idle-gap figures.

Ranked, with what each is grounded in:

1. **Review-round count and reviewer fan-out.** The largest attributed mechanism: the entire subagent thread, a majority of which is reviewer/writer dispatch work. Very likely a further share of the main thread too, since each skill invocation (`/code-review`, `/plan-review`, `/ready-for-review`, and others) loads a large body into a prefix that is then re-read on every subsequent turn. `review-round-cost --branches <B>` attributes only a minority share of branch dollars to an explicit review-round window; see Gate result 4, below, and "Why this generalizes."
2. **No incremental review credit.** Not a separate cost bucket — the mechanism that gives #1 its count. The review-marker mechanism hashes the whole staged (or cumulative) diff on every pass, so N edits force N full re-runs at full reviewer fan-out. The cumulative pre-handoff pass also re-invalidates on its own fix commits.
3. **Unattributed cache-write residual.** Cache-write share minus the portion classified as idle-gap rebuild. The governing prior is [`cold-cache-attribution.md`](cold-cache-attribution.md): a substantial share of cache-write spend corpus-wide is sub-60-second prefix invalidation, most of it with no attributable harness cause. This branch's own residual is judged predominantly the same already-documented, already-unexplained cold population, not a branch-specific defect — see Gate result 3 for the two tests that bound this.
4. **Idle-gap cache rebuild.** The engineer's original hypothesis — real, but third-ranked, not first. The one-hour tier is selectable by config (`promptCacheTtl`/`ENABLE_PROMPT_CACHING_1H`), and idle-gap rebuilds are the only mechanism it addresses. One finding departs from the corpus-wide pattern: the branch's idle-gap rebuilds show no evidence of a concurrent session, where the corpus-wide finding (`cost-levers-considered.md`, "Context cost root cause") is predominantly concurrent-session-driven. This branch's idle gaps read as genuine operator breaks, not session-switching.
5. **Fixed per-turn context floor.** This repo's own always-loaded instruction and skill surface, plus a further addition from a project's own skill descriptions (see "Is the private project's own surface a cause or a red herring?"). Not separately priceable on its own; it is the multiplicand that makes turn count expensive.

**Ruled out:** effort pins (output is a small share of total branch spend — halving every high-effort reviewer's output cannot reach the top rank); model routing (Opus usage is a small minority of spend); compaction (not a contributor on this branch); private-repo size (repo size does not enter the model's prompt — see "Is the private project's own surface a cause or a red herring?").

## Instrument corrections

Two defects in `transcript-analysis.py` were found and fixed so this study's own figures could be trusted. They are recorded here so a future reader of an older report knows which numbers moved and why.

- **`_dispatch_usage_summary` pricing defect (fixed).**
  - The function streamed its JSONL line-by-line and never deduplicated by request ID before pricing, while every other pricing path in the tool does.
  - That double-counted cache-class tokens on any multi-record API response.
  - It also over-summed `output_tokens` across a run, whose values only reach the billed figure on the final record.
  - Both errors materially overstated every per-dispatch and per-agent-type dollar figure the function fed.
  - The affected call site was `_dispatch_usage_summary`'s own caller inside `cmd_subagent_mix`'s model-mix table.
  - Any per-dispatch or per-agent-type dollar total printed by a version of this tool that predates the `dedup_turns_by_request_id` call in `_dispatch_usage_summary` is unreliable and should not be cited forward. See [`design-decisions/plan-architect-consult-mode.md`](../design-decisions/plan-architect-consult-mode.md) for the specific downstream metric this discontinuity affects.
  - The `_dispatch_usage_summary` docstring states the current buffering and dedup behavior.
- **`duration`'s "Sessions" column, mislabeled (fixed).** The column counted activity bursts separated by an idle-gap threshold, not distinct session files, and now prints as `Bursts`. A label defect, not a data defect, and nothing downstream consumed it as a session count.

Attributing branch dollars to review rounds needed no new instrument: `review-round-cost` is the correct one, and Gate result 4 reports its output on this branch.

Two further count disagreements were investigated and found to be definitional, not defects, and are recorded as scope notes rather than fixed: `cost`'s and `subagents`' differing turn-count denominators (see Gate result 1), and `review-trace`'s six-skill scope, which by design excludes `handoff`/`pr-description` events.

## Gate results

Five re-derivation gates were run against real branch data before this document cited a figure they bear on.

1. **Turn-count denominator gap, resolved.** `subagents --branches <B>` (branch-scoped; a `--projects` glob is only an approximation of it) and `cost`'s own priced-turn count use different denominators: `subagents` counts every post-dedup assistant record, split by thread, while `cost` counts only turns carrying a priced `usage` block. The two reconcile exactly once that difference is accounted for — a denominator difference, not a contradiction.
2. **Conditionally loaded project instruction file, plausibly never loaded.** The private project carries an instruction file that the harness loads only when a file under its subtree is read, and the branch's own changes never touch that subtree. A grep of every session file for a `Read`/`Grep`/`Glob` call with a path argument under that subtree found no structural path read. Content from that subtree surfaced only via shell-executed `grep`/`cat`, which carries no structural path argument the harness's auto-loader could react to. The file is not promoted to a ranked cause.
3. **Unattributed cache-write residual, bounded two ways.** Re-running the idle-gap rebuild scan at a threshold well below the default finds a broader population of moderate-sized rebuilds, not a few huge ones — consistent with ordinary prefix-cache churn rather than a single runaway cause. Separately, counting this repo's own default-branch ref moves against every consecutive assistant-turn-pair gap across the branch's sessions finds a small, bounded fraction straddling a ref move — consistent with the corpus-wide cold-cache contribution `cold-cache-attribution.md` already measured, not a larger, branch-specific effect. (See "Harness version," above, for why this repo's harness state was not constant across the measurement period.) The ref-move count itself is omitted, like every other per-engagement count in this document; see CLAUDE.md's "Also redact structural fingerprints and provenance" section.
4. **Most branch dollars fall outside review-round windows.** `review-round-cost` attributes a real but minority share of total branch dollars to an explicit review-round window, with most falling outside it. Window membership is temporal, not causal: a window closes at the next round-open or fresh user prompt (`docs/transcript-analysis.md` § "review-round-cost"). The outside-window share therefore mixes independent implementation with review-caused spend, and this measurement does not split them. Rank #1 means "largest attributed mechanism," not a majority-of-spend claim, and it rests on the subagent-thread floor, which this share does not affect. See "Why this generalizes," below, for the corpus-level version of this same finding.
5. **Every quantitative claim in this document was re-derived against the command or ledger recipe that produces it, named alongside the claim or in Sources, at the time this document was written.**

## Is the private project's own surface a cause or a red herring?

**Mostly red herring, with one real, small, unconditional term.**

- **Red herring:** the private project's own bulk repo surface. The branch touched none of it, and repo size does not enter the model's prompt: reading the tree does not predict what gets read at runtime. `lsp-token-reduction-feasibility.md` already refuted this exact inference shape on this repo's own transcripts — portfolio composition does not predict read composition (see [`cost-levers-considered.md`](../cost-levers-considered.md)).
- **Real, small, unconditional:** a project's own skill descriptions add to the always-resident context floor on every turn — true generically, and verifiable from this repo alone. The one item unconditionally on the wire regardless of what the branch's own changes touched.
- **Tested and closed, not a cause:** the conditionally loaded instruction file (Gate result 2, above) plausibly never loaded, since nothing in any session read a path under its subtree structurally.
- **A useful negative:** model routing; see "Ruled out," above.

## Baseline: this repo's own cost distribution

**Leg 1 — claude-config's own `pr-cost` ledger, the only leg permitted to publish absolute dollars, since it is this repo's own public corpus rather than a private one.** Across the full population of this repo's own recorded ledger rows (N=50, every priced PR this repo's own ledger has recorded, not a shape-filtered subset), total dollars (summed across every priced token class):

- range $0.53–$133.85
- mean $25.81
- median $17.45
- stdev $26.76

An earlier run of the same analysis over a differently scoped corpus showed the same shape. That run does not reproduce against the local ledger, so it is not cited by figure.

**Legs 2 and 3 — deferred.** A per-branch activity ranking and a nearest-neighbor token-class comparison were explored during this study but are not published; see CLAUDE.md's "Redact private-project-identifying content" section and `docs/private-project-redaction.md`.

## Why this generalizes

The ranked decomposition above is one branch's own forensic account — real for this branch, but not by itself evidence that review-loop cost is a general driver across this repo's history. Two corpus-level figures back that broader claim. Both are single-account figures over this repo's own corpus; see Sources for how each one's scope is confirmed.

- **Cost tracks process, not diff size.** Across this repo's own `pr-cost` ledger (N=50), total branch dollars correlate far more strongly with turn count (Pearson r = 0.974) than with commit count (Pearson r = 0.204). An earlier run over a differently scoped corpus showed the same ordering. A branch's own cost tracks how many turns it took far more closely than how much code it produced — the same asymmetry this branch's own ranked decomposition finds at N=1.
- **Review rounds are a real, recurring cost center, not a one-branch anomaly.** Across this repo's own review-round history, scoped to a single account (109 branches, 764 rounds), the mean branch runs 7.01 review rounds. Rounds by review type:

  - 430 code-review
  - 194 plan-review
  - 140 ready-for-review

  Mean cost per round by review type:

  - $3.57 code-review
  - $2.98 plan-review
  - $0.87 ready-for-review

  21.8% of dollars on branches with at least one review round are reviewer-dispatch dollars: subagent dollars dispatched inside a round window (the tool's "Reviewer-dispatch dollars" footer, which counts every in-window subagent, not only reviewers). Separately, 66.3% of those dollars fall outside every round window (the tool's "Non-round dollars" footer). The two figures are not complements, since in-window main-thread turns count toward neither.

  An earlier run of the same command over a differently scoped corpus showed the same pattern. That run does not reproduce locally, so it is not cited by figure.

Read together, these corpus-level figures — not any figure tied to the one private branch — are the statistical backing for "review-loop cost is a genuine driver."

## Recommended, not implemented here

This study's own plan puts every pipeline-behavior change out of scope, on the grounds that a cost investigation is a mandate to measure, not to redesign the review pipeline unreviewed. Recommended as follow-up work, not built here:

- **Incremental review credit.** The review-marker mechanism re-hashes and re-runs a full review pass on every edit, however small. A delta-aware credit mechanism is the single highest-leverage lever named by this study's own ranked decomposition (#1 and #2 together), but designing one safely — without silently skipping a review a reviewer would have flagged — is its own reviewed plan, not a corollary of this one.
- **Capping reviewer fan-out**, and **narrowing `ready-for-review`'s cumulative pass** to the increment since the last clean review rather than the whole branch diff, are both named candidates a future plan should evaluate against the per-review-round cost surface this study reuses.
- **`pr-cost --record` support for an open PR**, so a branch like this one's own cost trajectory is visible before it merges rather than only after — deliberately excluded here as a ledger schema migration on an append-only file, not a phase of this study.

## Sources

- **`claude/.claude/scripts/transcript-analysis.py`** — the subcommands behind the branch-level findings:
  - `cost --branches`: token-class and thread splits.
  - `subagents --branches`: turn-count reconciliation.
  - `cache-rebuild --since`: idle-gap rebuild share, both thresholds.
  - `review-round-cost --branches`: per-review-round cost attribution.
  - These ran at each command's default transcript scope, restricted with `--projects`/`--branches`. They are measurement instruments for the branch-level findings, not publication instruments, and no numeric figure from these runs is published beyond the owner-ruled coarse descriptors recorded below.
  - The corpus-level `review-round-cost --this-repo` invocation below instead passes an explicit single-account `--config-dir`, whose own header confirmed a single scan root.
- **`~/.claude/pr-cost-ledger.tsv`** — Baseline Leg 1 and "Why this generalizes," this repo's own ledger, the corpus this study publishes in absolute dollars and in a single-account correlation. Its scope is a single account's repo:
  - The recipe reads one local file, and its one distinct repo is this repository.
  - Its rows are written by `pr-cost --record`, which refuses more than one scan root unless `--all-accounts` is given.
  - With `--all-accounts`, `pr-cost --record` writes each account's own ledger file separately.
  - The `pr-cost-export` cross-account union was not used.
  - The ledger does not store the transcript project scope each row was recorded under, so a same-named branch in another project on the same account is a residual this record does not rule out.
- **`review-round-cost --this-repo`** — "Why this generalizes," this repo's own single-account review-round aggregate, published under the owner authorization below. Its scope depends on `--config-dir` overriding the scan root before the subcommand runs:
  - It ran against an explicit single-account `--config-dir`.
  - The override is structural for any subcommand outside `transcript_analysis/scope.py`'s `_SUBCOMMANDS_REFUSING_TOP_LEVEL_CONFIG_DIR` tuple.
  - A code trace verifies it holds here.
  - The run's own header, which showed a single scan root, verifies it too.
- **Owner authorization of the published figures.** On 2026-09-30 at 23:12 PDT the owner ratified an earlier version of this record, quoting the reply: "I ratify this authorization as written". On 2026-10-01 at 02:28 PDT the owner ratified the revised version below, selecting the option that directed adding the combination clause below ("Ratify, add the combination clause"). It covers exactly these figures:
  - **Ledger figures:** N=50; total dollars range $0.53–$133.85, mean $25.81, median $17.45, stdev $26.76; Pearson r = 0.974 against turn count and 0.204 against commit count. Computed from `~/.claude/pr-cost-ledger.tsv` by reading the latest row per `(host, repo, pr_number, machine)`, keeping rows with `status` = `ok`, and summing the five `*_usd` columns (`cache_read`, `cache_write_5m`, `cache_write_1h`, `output`, `input`). The Pearson r values use the `turn_count` and `commit_count` columns against that total. At computation time the file held exactly one distinct `repo`, `host`, `machine` and `rate_stamp`.
  - **Review-round figures:** 109 branches; 764 rounds; mean 7.01 rounds per branch; 430 code-review, 194 plan-review and 140 ready-for-review rounds; mean $3.57, $2.98 and $0.87 per round; 21.8% reviewer-dispatch and 66.3% outside-window dollar shares. Source: `transcript-analysis.py --config-dir <single-account-dir> review-round-cost --this-repo`, whose header showed one scan root. The report also printed per-branch rows (raw branch names and dated per-round dollars), dangling-dispatch counts and unpriced-turn counts, which this record withholds. The owner was told these two shares sit beside the pooled round-window figure in [`cost-levers-considered.md`](../cost-levers-considered.md) and chose to publish both.
  - **Harness pin** `0ce2cfe8`.
  - **Coarse branch-level descriptors** (the "minority" and "small, bounded fraction" wording in rank 1 and Gates 3 and 4): the owner ruled they may ship, selecting the option "Keep, you rule they're fine" at 2026-09-30 (before the 23:12 PDT ratification). On 2026-10-01 at 02:52 PDT, separately from and after the 02:28 ratification, the owner ruled that the same class covers these coarse phrases in this record and the plan ("a majority" of the subagent thread, a "small share" for output, a "small minority" for Opus, "most" of the spend not attributed), selecting the option "Yes, the ruling covers them".
- **[`cold-cache-attribution.md`](cold-cache-attribution.md)** — the governing prior for the unattributed cache-write residual (rank #3) and the ref-move cold-cache mechanism (Gate result 3).
- **`.claude/plans/pr-cost-forensics.md`** — this study's own plan, including the full assumption ledger and the pricing and mislabeling fixes.
- **[`design-decisions/plan-architect-consult-mode.md`](../design-decisions/plan-architect-consult-mode.md)** — the `subagent-mix` `Actual$` discontinuity this study's own pricing fix introduces into that metric's history.
- **[`cost-levers-considered.md`](../cost-levers-considered.md)** — this study's own lever-table entry, including the corpus-wide concurrent-session-driven idle-gap finding this branch's idle-gap rebuilds do not show.
