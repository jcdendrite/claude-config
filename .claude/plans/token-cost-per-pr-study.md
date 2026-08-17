# Tokens and cost per PR against size, complexity, and rework

## Context

**Goal:** produce a defensible per-PR cost dataset for this repo, and from it a
client-presentable account of what drives AI-tooling cost per unit of shipped
work and which mitigations are measured to help.

This repo already prices its own transcripts well: `transcript-analysis.py`
computes per-turn dollars by token class, model, and context bucket, and
`docs/cost-levers-considered.md` registers roughly forty cost levers already
investigated and closed with measured reasons. What is missing is a *unit of
delivered work*. Spend is legible today only as a weekly dollar total
(`docs/cost-ledger.md`, one row per ISO week per machine), which no client can
act on — it answers "what did this month cost" but not "what does shipping a
change cost, and why do some changes cost many times others."

**Why now — the source data is being deleted.** Measured across this repo's
whole corpus on 2026-08-17 (all 58 matching project directories under
`~/.claude/projects/`, not one): 233 top-level session `.jsonl` files, oldest
modified 2026-08-01 22:33, newest 2026-08-17 13:22 — a **16-day** observable
window. `cost --this-repo --since 30d` confirms it, warning verbatim: `earliest
turn found is 2026-08-02, more than 1 day after the requested --since window
start (2026-07-18) — this root's local corpus does not fully cover the requested
window.` Meanwhile 558 PRs have merged since 2026-03-31. Only the ~125 merged
inside that window have any surviving transcript, and each further day destroys
more.

**Outcome:** a `pr-cost` subcommand that emits and durably records one row per
merged PR; an in-repo case study recording method, numbers, and honest limits;
and a client-facing charted artifact built from that record.

## Approach

Add a `pr-cost` subcommand to `transcript-analysis.py` that, in a **single local
corpus pass**, groups dollars and tokens by each record's *attributed* branch,
joins that against PR size, rework, and mechanical review-surface metrics from
`git` and `gh`, and appends one row per merged PR to a ledger outside the repo
so rows outlive the transcript window. Then seed it from the current window,
have the engineer hand-code two complexity axes on a cost-stratified sample of
50 **from pre-implementation kickoff artifacts only**, and report every predictor
neutrally.

The complexity axes are grounded in this repo's existing framing rather than
invented: `docs/design-decisions.md` §14 holds that the cost gating a change is
"reviewer time (file count, domain complexity, risk concentration)" and not
implementation time, and `plan-it`/`plan-review` encode "review surface (file
count, domain spread, risk concentration)" as the canonical effort vocabulary.

**"Single pass" scopes the local `.jsonl` scan only.** The `gh` enrichment is a
separate bounded set of network calls, not a second corpus pass. The plan does
**not** claim to avoid per-item `gh` calls: discovery is one bulk `gh pr list`,
but commit and review-round-trip counts require `gh pr view <n>` per PR, so the
rate-limit and partial-failure handling below is sized for hundreds of sequential
round-trips. Single-pass matters because deriving the 2026-08-17 branch split
across separate subprocess runs produced totals differing by $1.48 — a
difference-of-totals proxy for unbranched spend came out *negative*, which is
impossible for a real quantity.

### Assumption ledger

**Root problem.** Per-PR AI-tooling cost is not measurable with today's tooling,
and the transcripts needed to measure it are deleted on a rolling ~16-day window,
so history not captured now is permanently unrecoverable.

**Givens** — conditions beyond this design's reach:

| # | Given | Why it is a given |
|---|---|---|
| G1 | Transcripts already deleted cannot be recovered, so no retention change reconstructs the 433 merged PRs predating the surviving window. [verified: 233 session files across 58 project dirs, oldest mtime 2026-08-01 22:33, measured 2026-08-17; 558 merged PRs since 2026-03-31] | Deleted files are gone. This given covers *only* already-lost history — the go-forward window is **not** a given; Out of scope declines raising `cleanupPeriodDays` with reasons. |
| G2 | Squash merges leave no head branch name in local git. [verified: `git log origin/main --merges` = 0 across all 578 commits; three recent commit bodies carry squashed sub-commit messages and trailers but no branch name] | GitHub's squash-merge behavior. Establishes *why* the API join is needed. |
| G3 | Per-model prices are vendor-set, hardcoded with a fetch date, and expire. [verified: `transcript_analysis/pricing.py:13` `_PRICING_SOURCE_URL`, `:14` `_PRICING_FETCH_DATE` = 2026-08-02, `:34` `_MODEL_BASE_INPUT_RATES`, `:49` `_MODEL_RATE_EXPIRES`] | Anthropic sets prices. Rows under different rate tables are not comparable. |
| G4 | The authoritative branch→PR join, pre-squash commit count, and review round-trip count all require the GitHub API. [verified: no merge commits rules out local branch recovery; only 43 of 558 branch refs survive; `gh pr list --state merged --json number,headRefName` returned `HTTP 503` throughout 2026-08-17] | GitHub owns both the data and its uptime. |

**Engineer-verified** — settled by direct instruction; not to be revised from
later investigation without pausing to ask:

| # | Decision |
|---|---|
| E1 | Corpus is `claude-config` only, scoped via `--this-repo`. [engineer-verified] |
| E2 | Complexity = mechanical proxies over the full population, plus engineer-coded ratings on a cost-stratified sample. [engineer-verified] |
| E3 | All three deliverables ship: subcommand plus docs, in-repo case study, client-facing charted artifact. [engineer-verified] |
| E4 | Findings reported **neutrally** across all predictors; no pre-committed headline. [engineer-verified] |
| E5 | Hand-coded ratings are made from **pre-implementation kickoff artifacts only** — never from the merged diff, its size, or its cost — to keep the coded predictor blind to the outcome. [engineer-verified] |
| E6 | **One** client-facing deliverable at the end; no interim artifact off Phase 2. [engineer-verified] |
| E7 | Report **both** a direct per-PR cost and a fully-loaded cost that allocates unattributed spend. [engineer-verified] |

**Mechanisms:**

| # | Mechanism | Justification | Anchors |
|---|---|---|---|
| M1 | New `pr-cost` subcommand, single local pass, grouping by attributed branch | No subcommand emits dollars by branch: `buckets` gives branch × model turn *counts* only; `cost --branches` filters to a supplied list rather than decomposing. Lighter primitives rejected below. | root |
| M2 | Append-only ledger at `$CLAUDE_CONFIG_DIR/pr-cost-ledger.tsv`, outside the repo, with the weekly ledger's git-tree refusal | The only way a row outlives the deletion window; keeping the default outside the repo means pointing the tool at a client repo later cannot leak client branch names into a public tree. | root, G1 |
| M3 | Triple join: `gh` `headRefName` authoritative, plan-file slug and **commit-SHA overlap** as independent cross-checks, with a per-row `join_confidence` | G2 leaves no branch name locally and only 43 of 558 refs survive. Plan-slug covers 76%; SHA overlap covers the rest and additionally detects mid-work branch renames that would otherwise orphan a PR's early spend. | G2, G4 |
| M4 | Reuse `dedup_turns_by_request_id`, `_price_turn`, `_cache_write_split`, `_attributed_branch`, `_resolve_cost_roots` unchanged | Each solves a correctness problem that would silently corrupt the dataset if reimplemented. All five verified at module scope, already multi-consumer, none private to another command's flow. | root |
| M5 | Rate-table stamp per row, plus retained per-class token counts | G3 makes cross-row dollar comparison invalid without it; retained token counts make re-derivation under one table possible. | G3 |
| M6 | Engineer-coded novelty and ambiguity on a cost-stratified sample of 50, rated from kickoff artifacts | The mechanical proxies operationalize review surface but are blind to whether work was novel and whether the ask was clear — the two candidate drivers most likely to explain residual variance. | E2, E5, root |
| M7 | Read-mode "uncaptured PRs still in window" listing, plus a documented capture cadence | Closes the capture-trigger gap without a hook. Mirrors the weekly ledger's own solved pattern: its default read mode lists ISO weeks present in the corpus with no ledger row. | G1 |
| M8 | Single-root enforcement: refuse (exit 2) whenever more than one scan root resolves | Mirrors `cost --summary`'s existing CLI-boundary refusal. `pr-cost` durably *writes*, so an unenforced union is worse than for a read-only report. **M8 enforces single-root, not claude-config-identity** — a single-root run against another repo passes it cleanly, which is correct for a shipped tool. E1 compliance for *this study* therefore remains a procedural `--this-repo` commitment; M8 narrows the blast radius but does not structuralize E1, and must not be cited as if it did. | G1 |

**Over-powered-primitive check on M1.** Three lighter mechanisms considered:

1. **Extend `pr-link` with cost and size columns.** Rejected. Its contract is a
   caller-supplied branch list (`--branches` required, `:3431-3433`) with one
   `gh pr list --head <branch>` call per branch (`:3461`). This study needs the
   inverse — discover merged PRs in a window, then find their branches. Its
   per-record filter also uses raw `gitBranch` (`:3444`) rather than
   `_attributed_branch` and excludes `isSidechain` records outright (`:3445`), so
   extending it inherits two undercounts against a corpus where subagents carry
   30.7% of spend.
2. **An ad-hoc `jq`/shell pipeline, not committed.** Rejected here, though it is
   the precedent set in `docs/case-studies/effort-estimation-review-surface.md`
   ("committing a new subcommand to detect a one-off is over-powered"). That
   turns on the analysis being a one-off; G1 makes capture recurring, and E3 asks
   for something reusable. It would also reproduce the non-atomicity above.
3. **A `PostToolUse` hook.** A hook that *computes* cost is rejected on this
   repo's own closed-lever finding — "hook payloads carry no token counts, so it
   would estimate anyway." A hook that merely *triggers* `pr-cost --record` after
   a `gh pr merge` call sidesteps that objection and is a genuinely lighter
   trigger, but is declined for now because merges here are frequently performed
   by the human on GitHub's web UI, outside any tool call — it would fire on an
   unpredictable subset. M7's read-mode listing covers every PR regardless of how
   it merged. Revisit if the listing proves insufficient.

**Assumptions:**

| # | Assumption | Tag |
|---|---|---|
| A1 | Every record carries `gitBranch`; `worktree-agent-*` records resolve to the dispatching branch via a per-session timestamp index. | [verified: `_session_branch_index` `:3932`, `_attributed_branch` `:3958`] |
| A2 | Pricing must run after `dedup_turns_by_request_id`; skipping it inflates dollars and turn counts. | [verified: `pricing.py:160`, `_price_turn` `:314`] |
| A3 | `pr-link` joins branch→PR but reports no dollars, tokens, or PR size, and drops sidechain records. | [verified: `:3427-3493`] |
| A4 | PR number is 100% recoverable from local git; branch name is not. 125/125 in-window subjects match `(#NNN)$`; 200/200 of the last 200 first-parent subjects do; 95/125 add exactly one `.claude/plans/*.md`, 0 add more than one. | [verified: `git log origin/main --since=2026-08-01` subject and name-only scan, 2026-08-17] |
| A5 | Population is ~125 PRs merged 2026-08-01..16 (~7.8/day) against 142 distinct branches — so some branches never became merged PRs, and some may be pre-rename aliases of ones that did. | [verified: git log subject count; 142 from `buckets --this-repo`] |
| A6 | No lines-changed, diff-size, or complexity metric exists in the toolkit; the cost ledger has no branch/PR/session column; no CSV/TSV dataset is tracked. | [verified: grep across docs, the package, and `git ls-files`] |
| A7 | `gh pr list --state merged --json number,headRefName,additions,deletions,changedFiles,mergedAt` returns every field and `headRefName` survives merge; `gh pr view <n> --json commits,reviews` yields commit and review counts (`commits`/`files` are `pr view` fields, not `pr list` fields). | [unverified] — GitHub 503 throughout 2026-08-17. **Phase 0 gates Phase 1.** If `headRefName` does not survive merge, M3's primary join fails and coverage falls back to plan-slug plus SHA overlap — bring that to the engineer, do not absorb it. |
| A8 | `gh pr list --limit` defaults to **30**, so any unbounded call silently truncates a 125- or 558-PR population with no error. | [verified: `gh pr list --help` on this machine] |
| A9 | Per-PR dollar cost is right-skewed and predictors are collinear, so rank correlation is the appropriate summary rather than Pearson or a multivariate fit at N≈125. | [unverified] — Phase 3 tests distribution and inter-predictor collinearity **before** choosing the statistic. |
| A10 | ~17% of in-window spend is attributable to no PR branch: named feature branches ≈83.3% ($4,935.53), `main` 15.9% ($941.34), `HEAD` 0.9% ($51.49). Unbranched spend is within the live-corpus noise floor (~$1-2); no exact figure certified. | [verified: `cost --this-repo --since 30d` unfiltered vs. `--branches` runs, 2026-08-17] |
| A11 | Window totals: $5,916.07 (a repeat run minutes later read $5,926.88 — the corpus grows live). Context class 87.4% (cache read 57.9%, write-1h 18.7%, write-5m 10.8%); output 12.6%, input 0.1%. Main 69.3%, subagent 30.7%. | [verified: `cost --this-repo --since 30d`, 2026-08-17; redaction active, no `STALE PRICING` banner. **Provenance:** these are this repo's own spend only — `--this-repo` resolves to `claude-config` worktree project dirs by identity, and a single-account `--summary` run over the same window scanned the same 208 transcripts for $6,032.19, the gap being live corpus growth between runs. No other account's or client's spend is included in any figure in this plan.] |
| A12 | `worktree-agent-*` spend cannot be a separate bucket — `_attributed_branch` folds it into the dispatching branch by design. Report as folded, never absent. | [verified: `:3958`; a `--branches worktree-agent-*` run matched nothing] |
| A13 | `buckets` skips records with no `gitBranch` (`:244`), so it cannot show the absent bucket. `pr-cost` must count unbranched records rather than inherit this skip. | [verified: `:244`] |
| A14 | The corpus spans 58 glob-matching project dirs, of which `--this-repo` identity-matches 55; the tool reports scanning 204 of 233 glob-found files. The 233/204 and 58/55 gaps are unexplained. | [unverified] — Phase 2 must **enumerate and classify** every excluded file and dir, confirming both that none carries priced turns on an in-window branch (the denominator question — a ~12% gap would bias every reported number) and that none belongs to a different project (the disclosure question). `pr-cost` resolves roots by identity via `--this-repo`, not the looser glob that produced the 58/233 figures, so the second risk is already contained by existing code; auditing it explicitly makes that containment checkable rather than incidental. |
| A15 | The existing multi-dir fixture convention (`fake_projects.parent / "-home-user-repoN"`), the subagent-transcript helper, and the `monkeypatch.setattr(subprocess, "run", fake_run)` seam used by `TestPrLink` already support everything these tests need — no new fixture machinery is required. | [verified: `tests/conftest.py`; `test_transcript_analysis.py` `TestPrLink` ~3288, multi-dir sites ~3190/3555/5884] |
| A16 | The weekly ledger's `_upsert_cost_ledger_row` keys on `(week, machine)` and **refuses** a duplicate key without `--force`, on the principle that silently rewriting history is how a ledger stops being one. `pr-cost` inherits the refusal but not the replacement: the as-of rule makes a single capture per PR the norm, and a `--force` correction appends a superseding row rather than overwriting. | [verified: `transcript-analysis.py:6936`] |
| A17 | `cost-ledger --record` is gated behind a machine-level opt-in sentinel registered by `install.sh:405`, precisely because it is a write-taking subcommand shipped to every stow user. | [verified: `install.sh:405`] |
| A18 | Allocating the A10 residual pro-rata by each PR's direct cost assumes overhead scales with direct cost. Both this and the equal-per-PR alternative are positive affine transforms of direct cost, so neither changes any rank correlation — fully-loaded cost carries magnitude information only, never independent driver evidence. | [verified: affine transforms preserve rank order, so Spearman correlation is invariant under them] — the *allocation choice* remains a modeling assumption the case study states; the *invariance* is arithmetic, not an assumption. |

## Critical files

**Modify:**

- `claude/.claude/scripts/transcript-analysis.py` — add `cmd_pr_cost` and parser.
  **Reuse (verified at module scope, all multi-consumer):**
  `dedup_turns_by_request_id`, `_price_turn`, `_cache_write_split`
  (`transcript_analysis.pricing`); `_session_branch_index` (`:3932`) and
  `_attributed_branch` (`:3958`); `_resolve_cost_roots` and `--this-repo`
  (`transcript_analysis.scope`); `_fmt_usd` and markdown rendering
  (`transcript_analysis.render`). Ledger writes follow the weekly ledger's
  temp-file/atomic-rename/sibling-lock *pattern* (~`:6692-7214`) — note its
  parser/formatter is markdown- and 10-column-specific, so a ~25-column TSV
  ledger is new code of comparable size, budgeted under Phase 1.
- `claude/.claude/scripts/tests/test_transcript_analysis.py` — see test coverage below.
- `install.sh` — register a `pr-cost` opt-in sentinel alongside the existing
  `cost-ledger` one (A17), so a write-taking subcommand shipped to all stow users
  keeps consent parity.
- `docs/transcript-analysis.md` — `pr-cost` section.
- `docs/case-studies.md` — index line. `docs/cost-levers-considered.md` — a row
  for any lever opened or closed. `CHANGELOG.md` — Unreleased entry.

**Create:**

- `docs/pr-cost.md` — subcommand reference and ledger schema. Must state: the
  re-record contract; the rate-table re-derivation rule (cross-table comparison
  re-derives dollars from retained token counts under **one** table, never
  compares `usd` columns directly); the no-backup/no-replication caveat scoped to
  this ledger being the *sole* surviving record once transcripts expire; the
  cloud-sync and bare-repo-dotfile-manager residual paths the git-tree check does
  not close, restated rather than cross-referenced because these rows carry
  branch names where the weekly ledger's carry none; which columns are
  claude-config-specific and inert elsewhere; and a one-line note that the row
  parser is strict on column count, so adding a column later needs a migration
  step. It must also state that branch-name scrubbing is **best-effort**: the
  always-on detectors catch six structural shapes and bare tracker IDs, but a
  branch encoding a client name in plain English (`feature/acme-onboarding`) is
  caught only by the opt-in `~/.claude/private-projects.md` blocklist, which is
  empty by default. Populating that blocklist is a **precondition** for running
  `pr-cost` against a non-`claude-config` repo, not an optional hardening step —
  and the console-print path is exposed here just as the ledger is, so keeping
  the ledger outside the repo does not cover it.
- `docs/case-studies/token-cost-per-pr.md` — the study, in the established
  four-part shape (question, how measured, the numbers, honest limits). Sole home
  for findings; `docs/pr-cost.md` carries schema only.
- A client-facing artifact (`dataviz` skill, published via `Artifact`). Not
  committed. Spec in Phase 4.

**Ledger schema additions beyond the obvious.** `captured_at`; `join_confidence`;
`supersedes`, referencing the row a correction replaces so readers take the latest
per `(pr_number, machine)`; a `status` column flagging degraded rows, as a fixed
enum carrying no API error text; `unpriced_turns` and `unpriced_tokens`
(an unrecognized model ID is excluded from pricing, not priced at $0 — without
this the row silently understates); and the **additive components** behind every
ratio — `sum_context_at_turn` alongside `mean_context_at_turn`, and `opus_dollars`
alongside `opus_dollar_share` — so cross-PR rollups are not computed as an
average of averages.

**Test coverage required.** Worktree-agent attribution; dedup before pricing;
mid-session branch switch through `pr-cost`'s own grouping (reusing
`_attributed_branch` does not prove the new wiring calls it correctly);
unparseable/missing timestamp on a worktree-agent record (an untested branch of
the reused function); multi-session accumulation for one branch (the shape
single-pass depends on); a PR whose branch has zero records; a branch with
records but no merged PR; empty-`gitBranch` records counted, not skipped;
a model ID absent from the price table surfacing in `unpriced_*`; a branch
matching more than one merged PR, exercising the Phase 2 tie-break in each of its
three arms; plan-slug/`headRefName`/SHA-overlap disagreement; a branch name that
the redaction detectors *would* alter, confirming it still joins and still passes
the join-integrity check because both run on raw pre-scrub values, while the
ledger cell and console output carry the scrubbed form; `gh` failure by class
(below); `gh pr list` pagination truncation (A8); an unforced re-record refusing,
and a `--force` correction appending a superseding row while leaving prior rows
byte-identical; and **the M8 refusal** — exit 2 when more than one root resolves,
plus no raw branch name printed under a default machine-wide invocation. Single-pass is asserted with the suite's existing spy convention
(`monkeypatch.setattr` on the scan entry point, ~16125-16238), counting exactly
one scan for a multi-PR corpus — output correctness alone cannot prove atomicity.
`gh` is faked through the existing `TestPrLink` seam, extended to dispatch by PR
number across the bulk-list-plus-per-PR-view call shape.

**Operational contracts to state in Phase 1, not discover in Phase 2.**

*`gh` call shape — one architecture, stated once.* Discovery is a single
`gh pr list` with an explicit `--limit` sized to the population, and Phase 2
asserts returned-count equals expected-count (A8). Enrichment is `gh pr view <n>`
**per PR**. Folding those lookups into aliased `gh api graphql` batches is a real
optimization but is **deferred, not adopted**: it would widen Phase 0's
verification surface to a second field-availability contract, and it changes the
failure taxonomy below (a batch failure degrades every PR in it, and the budget is
GraphQL complexity points rather than call count). Adopt it only if rate limiting
actually bites during Phase 2, and re-run Phase 0 against the GraphQL field set
first. The test seam fakes the bulk-list-plus-per-PR-view shape accordingly.

*Failure handling.* A `gh auth status` preflight runs before the batch — an auth
failure surfacing mid-run would otherwise leave a dataset that looks complete but
has empty enrichment columns throughout. Thereafter failures are differentiated:
auth/config errors fail the whole run loudly; rate-limit responses back off and
resume; transient network errors degrade that row and mark `status`. Each call
carries a timeout; a per-PR progress line goes to stderr. `gh` must operate
against the same repo identity as the resolved corpus root — M8 gates the local
scan roots only, not `gh`'s target repo.

*Ledger writes.* One row per lock acquisition, matching the pattern's sizing. The
key is `(pr_number, machine)` with reader-side aggregation, mirroring the weekly
ledger's machine-separated rows, so two machines recording the same PR do not
silently discard each other.

*Redaction ordering — the join runs on raw values.* M3's join computation,
`join_confidence`, and the Verification join-integrity check all operate on **raw,
pre-scrub** branch names. Scrubbing applies only at the ledger-append and
console-print step. This ordering is load-bearing: redaction is not injective, so
scrubbing before comparison could map two distinct names to one placeholder (false
match) or fire on one side only (false disagreement) — a control that corrupts the
data it protects. Branch names pass the redaction hook's own tracker-ID and
structural detectors at that write/print boundary, as a data-minimization filter,
since `deny-private-project-refs.sh` fires only on `git commit`/`gh pr create`/
`gh pr edit`/mutating `gh api` and cannot gate a ledger write or a terminal.
`status` is a **fixed enum** carrying no embedded API error text; any diagnostic
text goes to stderr, never into a ledger cell.

*Argument safety.* Any `git` ref argument sourced from PR or branch data uses a
commit SHA or `--` disambiguation, never a raw branch string in an option
position. Commit SHAs are git-generated and cannot take that shape, so M3's
SHA-overlap calls add no new surface.

**Incidental finding, not in scope.** `docs/transcript-analysis.md:260-280`
documents `pr-link`'s columns as `Branch / PR# / Title / Author comments / Total
comments`; the code prints `Branch / PR / Opus / Sonnet / IssueCmt / ReviewCmt`
(`:3451`). Out of scope for this plan — it is a different subcommand — and left
unfixed deliberately rather than tracked, so a future contributor touching
`pr-link` should expect the drift to still be there.

## Phases

Phase 1 ships as its own PR. **Phase 2 produces no repo diff** — it is an
operational run writing only to the external ledger — so no PR is expected for
it. Phase 3's worksheet is not committed. Phase 4 ships as a second PR carrying
the case study, the `case-studies.md` index line, and the levers-register row.

**Phase 0 — unblock A7.** Confirm field list and `headRefName` retention once the
API recovers. Gates Phase 1's schema.

**Phase 1 — the instrument.** Implement `pr-cost`, its tests, the M7 read-mode
uncaptured listing, the M8 refusal, and `install.sh` registration. Per-PR row:
PR number, head branch, merged date, rate stamp, `captured_at`, `join_confidence`,
`status`; dollars and tokens by class; `unpriced_turns`/`unpriced_tokens`; turn
count, session count, `opus_dollars`, `sum_context_at_turn`; `additions`,
`deletions`, `changed_files`, commit count, review-comment count; and mechanical
proxies — distinct top-level dirs, distinct file extensions, `tests_changed`,
`plan_file_added`, and a risk-surface flag. The risk-surface path list and the
plan-slug convention are **configurable with claude-config defaults**, so the
tool is not inert for other stow users.

**As-of rule, and the correction contract.** A branch accrues transcript activity
after merge, so a row captured too early understates. The close-out window is a
**measured parameter, not a literal**: compute, across the surviving corpus, the
gap between each branch's last priced turn and its PR's `mergedAt`, and set the
window at a stated percentile of that distribution (p95 unless the measurement
argues otherwise). Capture runs no earlier than merge plus that window.

Because the window means each PR is captured once, after its spend is final,
re-recording is an exception rather than a routine step, and the weekly ledger's
refuse-without-`--force` default fits. An unforced re-record of an
already-captured PR refuses and names `--force`.

**Corrections append, they do not replace.** M2 calls this an append-only ledger,
and a replacing correction would contradict that. A `--force` correction appends a
*new* row carrying the same `(pr_number, machine)` key with a fresh `captured_at`
and a `supersedes` reference; readers take the latest row per key. This keeps the
full correction history — a single-slot audit column would lose everything before
the most recent correction, which matters because G3's rate expiry and A11's live
corpus both make more than one correction per PR plausible, and this ledger is the
sole surviving record once transcripts age out.

**Phase 2 — seed and audit.** Record all in-window PRs, then audit before
analyzing: join outcome per PR (matched / no PR / `gh` error / disagreement);
`gh` returned-vs-expected count; PRs with zero attributed records; the A14
enumeration and classification of every excluded file and dir; orphan-branch
classification — each of the ~17 branches with records but no merged PR resolved
as genuinely abandoned *or* a pre-rename alias, via commit-SHA overlap, with
renamed branches' dollars folded into the correct PR row rather than left in the
residual; and the **kickoff-artifact eligibility rate**, broken down by artifact
type (plan file / originating issue / first commit message), since Phase 3's
coding frame depends on it and the plan currently only asserts the rate is high.

**Branch-to-PR tie-break, stated here rather than presupposed.** When one branch
matches more than one merged PR: highest commit-SHA overlap wins; if overlap ties,
the most recent `mergedAt` wins; if still ambiguous, the row is marked
`join_confidence: low` and excluded from Phase 3's analysis rather than guessed.

**Phase 3 — hand-code and analyze.** Restrict the coding frame to PRs having a
kickoff artifact — the plan file if present, else the originating issue, else the
first commit message; PRs with none are ineligible and the exclusion is reported.
Stratify by dollar cost into deciles and draw **5 per decile at random** (state
that within-decile selection is random; note that a fixed 5/decile draw needs
inverse-probability reweighting for any population-level estimate, since decile
sizes do not divide evenly at N≈125).

The worksheet shows **only the kickoff artifact text** — no line counts, no file
counts, no dollars, no decile (E5). Rubric anchors, fixed now rather than
invented at execution time:

*Novelty:* 1 — mechanical repeat of an existing pattern. 2 — follows an existing
pattern in a new place. 3 — extends a pattern in a way requiring a new decision.
4 — introduces a mechanism with no precedent in the repo. 5 — approach not known
at kickoff; the work includes finding it.

*Ambiguity at kickoff:* 1 — exact files and change specified, acceptance stated.
2 — outcome specified, implementation left open. 3 — outcome clear, at least one
material sub-decision unresolved. 4 — solution shape genuinely open; alternatives
named but unchosen. 5 — problem itself under-defined; the work includes deciding
what to build.

Code 8 PRs first, check for drift, revise anchors, then run the remaining 42.

Report the artifact-type mix within the sampled 50, and check whether ratings
differ systematically by artifact type — a rich plan file gives the rater more
signal than a bare commit message, and since `plan_file_added` is *also* one of
the mechanical proxies, unchecked differential richness would let the nested
comparison below partly measure artifact thinness rather than a genuine second
signal. Report that check as a stated robustness caveat either way.

Then: test A9's distributional assumptions and compute an inter-predictor
Spearman correlation matrix **before** choosing the summary statistic. Report each
predictor's marginal rank correlation with cost, with N and bootstrap interval.

**The nested comparison, with its method named.** Marginal correlations alone
cannot answer M6's "adds explanatory power" question, but a fit over ~10-13
individual predictors at N≈50 is exactly the overfitting A9 rejected at N≈125. So
the comparison runs on a **reduced composite**: collapse the mechanical proxies
into a single review-surface score, with the collinearity matrix computed above
determining which proxies are redundant and dropped, then compare that one
composite against the composite plus the two coded axes. Two or three degrees of
freedom, not thirteen. This is reported as directional evidence with its N≈50
caveat, never as a settled effect.

**Direct and fully-loaded cost (E7) — and where fully-loaded may not be used.**
Report both. Fully-loaded allocates the A10 residual pro-rata by direct cost, with
an equal-per-PR split as a sensitivity check (A18). But both allocations are
positive affine transforms of direct cost — a single global scalar applied to
every PR — so rank correlation is invariant under them and every predictor's
correlation with fully-loaded cost is *identical*, not merely similar, to its
correlation with direct cost. Fully-loaded cost is therefore confined to magnitude
and budgeting reporting (the artifact's "what does a PR cost" chart); it is **not**
run as a second target in the driver analysis, and the two must not be presented
as independent confirmation of each other. The sensitivity check tests the
magnitude narrative only.

**Phase 4 — deliverables.** Case study and artifact. Before publishing, re-run
the tool and diff against the numbers as written — the corpus grows live (A11),
so Phase 2's seed figures can drift from Phase 4's write-up.

*Artifact spec:* three charts, each with the claim it supports — per-PR cost
distribution (what shipping a change costs, direct and fully-loaded); cost against
PR size (the intuitive predictor, reported at its measured strength whatever that
is); and the token-class decomposition (where the money actually goes). The
external-validity caveat appears **in the artifact**, not only in the case study.
Mitigations draw on `docs/cost-levers-considered.md`, but that register is written
for an internal audience and cites this repo's own settings keys, hooks, and
subcommands — Phase 4 includes an explicit curation step separating verdicts
describing *portable mechanisms* from repo-specific ones, and only the portable
subset, generalized, reaches the artifact.

## Verification

- `../../../.venv/bin/pytest claude/.claude/` and
  `../../../.venv/bin/ruff check claude/.claude/` from this worktree;
  `scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck`.
- **Join integrity:** for the 95 in-window PRs adding exactly one plan file, `gh`'s
  `headRefName` must equal the plan-file slug. Disagreement is a join defect. The
  remaining 30 are validated by commit-SHA overlap, so no PR ships unvalidated.
- **Attribution conservation, with a stated tolerance:** dollars attributed across
  all PRs plus the A10 residual must reconcile against a single `cost --this-repo`
  reference run captured at the same time, **to within the live-corpus drift** —
  exact equality is unachievable (two runs minutes apart differed by $1.48). A gap
  materially larger than that drift means attribution is dropping records.
- **Dedup regression:** a fixture session with a multi-content-block turn prices
  identically through `pr-cost` and `cost --branches`.
- **Idempotence and correction:** an unforced re-record of an already-captured PR
  refuses and leaves the file byte-identical; a `--force` correction appends
  exactly one superseding row and leaves every prior row byte-identical, with a
  reader taking the latest per `(pr_number, machine)`.
- **Redaction:** the M8 refusal is tested, and Phase 4 adds a pre-publish gate
  against the rendered artifact content — `Artifact` is not `git commit`/`gh pr
  create`/`gh pr edit`/`gh api`, so no hook gates it and the obligation otherwise
  has no enforcement point. That gate is **scripted, not manual**, reusing the
  redaction hook's own detector constants from `_lib.sh` rather than restating
  the patterns, and it is exercised with one **allow case** (clean content
  publishes) and one **deny case** (planted violation blocks) before the artifact
  ships. A manual "eyeball it first" step would read as a control while
  functioning as a to-do note, and an untested control is indistinguishable from
  an absent one — which matters most here, since the artifact is the only
  deliverable aimed at an audience outside this repo.

## Out of scope

- **Raising `cleanupPeriodDays`.** A real, documented lever — deliberately
  declined, not treated as immovable. It is a top-level `settings.json` key,
  default 30 days, minimum 1, no documented maximum, and it governs both
  `projects/<project>/<session>.jsonl` and the `subagents/` subdirectory, which
  age out together rather than independently
  ([verified: code.claude.com/docs/en/settings.md "Available settings";
  code.claude.com/docs/en/claude-directory.md "Cleaned up automatically"]).
  Declined because: it cannot recover the 433 already-deleted PRs (G1); it grows
  every scan's cost linearly in corpus size; it is a stowed-settings change
  affecting every stow user; and the vendor documentation points the opposite way
  on security grounds, noting transcripts are unencrypted at rest and
  recommending *lowering* the value to shorten exposure. The ledger is needed
  regardless — no finite retention removes the need to capture. Raising it is its
  own change with its own plan.

  **Open question worth one line in the case study:** the documented default is
  30 days, but the measured window is 16 (no transcript predates 2026-08-01).
  Whether a lower value is set on this machine, or the age cutoff is computed on
  a basis other than the modification time this plan measured, is unresolved —
  the vendor docs say only "older than this period" and do not state
  creation-versus-modification. It does not change the study's bound, which is
  set by what actually survives, but the discrepancy should be named rather than
  papered over.
- Private-client-repo corpora (E1). Note M8 does **not** enforce this: it refuses
  an ambiguous multi-root resolution, and a clean single-root run against any
  repo — including a private one — passes it. Keeping this study to
  `claude-config` remains a procedural `--this-repo` commitment. M2 anticipates
  the tool being pointed at a client repo later, so no reader should treat M8 as
  a barrier against that.
- An interim client artifact off Phase 2 (E6).
- Fixing `pr-link`'s column drift or its sidechain/attribution undercounts.
- **Force-push and rebase effects on a recorded row.** `additions`/`deletions`
  reflect the merged diff, which can differ from what was reviewed; a branch
  force-pushed mid-work may leave records the SHA-overlap check cannot match. Both
  are accepted residual risks, stated in the case study's limits rather than
  guarded against.
- Branch-name reuse across two merged PRs — accepted residual risk, noted in the
  case study; the tie-break is stated in Phase 1 but not defended against
  adversarially.
- **Any causal claim, and any generalization of the dollar figures.** Observational
  data from one repo, one operator, and a 16-day window covering ~125 of 558
  merged PRs — the most recent slice, not a random sample — in a meta-repo whose
  PRs are unusually documentation- and review-heavy. What transfers is the
  *taxonomy of drivers and the measurement method*, not the numbers; the case
  study and the artifact must both say so.
- Acting on the findings. Adding or changing a cost lever is a separate change
  with its own plan and levers-register entry.
