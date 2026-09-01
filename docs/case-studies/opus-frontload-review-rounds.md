# Front-loading Opus into authoring: does it cut review rounds?

Pre-registered against `.claude/plans/opus-frontload-review-rounds.md`, whose
gates G0–G3 were fixed before any scan ran. This document reports the
measured result against that bar, not a story assembled afterward.

## Why this measurement was needed

A prior investigation (`docs/cost-levers-considered.md`, "Model routing
(reduce Opus usage)" row) established that Opus's current 15.7%-of-spend
share is correctly sized for its *existing* usage points — `plan-architect`
in `/plan-it` Step 5, and ad hoc `consult`-mode dispatches — but never asked
whether adding Opus at an earlier authoring point would prevent
review-flagged defects from occurring in the first place. Reviewer-yield
data (GH-762, corrected pull 2026-08-30) shows this repo's reviewers are
catching mostly-real defects, so the remaining cost lever is arriving at
review with fewer real defects already present, not spawning fewer or
cheaper reviewers.

The design deliberately runs entirely retrospectively against instruments
this repo already ships — see the plan's Approach section for why a
forward-looking controlled pilot was set aside (a `model:` pin flip and an
effect size this repo has previously measured as unresolvable in the
available window).

## Method

All commands run 2026-09-01, scoped to `jcdendrite/claude-config` only.
Python commands run from this worktree via
`../../../.venv/bin/python3 claude/.claude/scripts/transcript-analysis.py`.

**Phase 1 — baseline round-count distribution and G1 proxy validation.**

- `gh pr list --repo jcdendrite/claude-config --state merged --search "merged:>=2026-08-01" --json number,headRefName,mergedAt --limit 300` → 213 merged PRs.
- `transcript-analysis.py review-trace --this-repo --skill code-review --since 2026-08-01`, redirected to a file; skill-invocation lines grouped by resolved branch to get a per-branch round count (a round = one `/code-review` Skill invocation). 120 distinct branches carried ≥1 invocation, 319 invocations total.
- `transcript-analysis.py review-trace --this-repo --skill plan-review --since 2026-08-01` (same treatment) → 115 distinct branches with ≥1 `/plan-review` invocation, as the plan-it-ran treatment indicator.
- Per-PR commit counts pulled via six batched `gh api graphql` calls (≤40 PRs per query, `pullRequest(number: N) { commits { totalCount } }` aliased per PR) rather than 213 sequential `gh pr view` calls — a cheaper query shape for the same field. Confirmed against `gh pr view --json` with no value first that both approaches read `commits.totalCount`.

**Phase 2 — G2 within-agent model-variation arm.**

- `transcript-analysis.py subagent-mix --this-repo --since 30d`, redirected to a file (its output carries a `DO NOT PUBLISH` banner — real branch names and, this run confirmed, real `subagent_type` values once more than one account root resolves). Only the tabulated `AgentType` summary rows were read back.

**Phase 3 — G0/G3 observational screen.**

- Direct read of each declared account's `pr-cost-ledger.tsv` (`~/.claude/transcript-config-dirs` lists 6 roots; 5 have a ledger file, 1 does not yet), rather than `pr-cost --this-repo --all-accounts`'s own read-mode listing — that listing prints every row in an account's ledger and redacts only the displayed `repo` label, it does not filter by repo. Filtered to `repo == jcdendrite/claude-config` and deduped by `(host, repo, pr_number, machine)` keeping the latest `captured_at` (no `supersedes` corrections were present in this window, so dedup was a no-op).
- Stratified the resulting rows by `changed_files` terciles, split each stratum at its own within-stratum median `opus_dollar_share_pct`, and bootstrapped the adopt-candidate direction (high-Opus-dose stratum shows a *lower* outcome than low-dose) with 2,000 resamples per stratum/outcome pair.

## Results

### G0 — ledger arm feasibility: PASS

186 rows for `repo == jcdendrite/claude-config`, PRs #278–#746, merged
2026-05-20 to 2026-08-25, all `status == ok` with populated
`plan_file_added` and `opus_dollar_share_pct` cells. Bar was ≥100.

Coverage check: `gh pr list --repo jcdendrite/claude-config --state merged --search "merged:2026-05-20..2026-08-25"` returns 365 merged PRs in that
exact window, against 186 captured rows (51%) — the same rolling-retention
gap `pr-cost-context-bucket.md` documents, not a defect.

### G1 — proxy validation: PASS

Round-count distribution across the 213 merged PRs in the 2026-08-01+
window is a small, right-skewed integer, reported as median/IQR per this
repo's own prior finding that a mean hides a right-skewed tail:

| Treatment of branches absent from the code-review skill log | n | Median | Q1 | Q3 | IQR |
|---|---|---|---|---|---|
| Counted as 0 rounds | 213 | 0 | 0 | 2 | 2 |
| Excluded as censored (no transcript coverage at all in-window — 26 branches, the rolling 30-day retention window aged them out) | 187 | 1 | 0 | 2 | 2 |

The median is sensitive to how a branch with no transcript evidence at all
is classified — a confirmed zero-round PR, or unknown. IQR is stable at 2
regardless. Either way, most merged changes need 0–2 `/code-review` rounds.

`commit_count` bucketed by round count (0, 1, 2, 3+), all 213 PRs:

| Rounds | n | Mean `commit_count` |
|---|---|---|
| 0 | 107 | 2.327 |
| 1 | 48 | 2.542 |
| 2 | 29 | 2.862 |
| 3+ | 29 | 6.310 |

Mean `commit_count` rises monotonically across every bucket, every adjacent
bucket pair has ≥10 PRs (bar was ≥10), and the same monotonic shape holds
after excluding the 26 censored branches (bucket 0 becomes n=81,
mean=2.309; buckets 1–3+ are unaffected since none of their PRs were
censored). `commit_count` is licensed as the ledger-era outcome variable
for Phase 3.

### G2 — within-agent arm: BLOCKED, not evaluable on this instrument on this machine

`subagent-mix --this-repo` always resolves 6 roots on this machine, and
under more than one root it redacts `subagent_type` as well as branch names
to opaque `account-N/agent-type-M` labels — documented behavior
(`docs/transcript-analysis.md:198`), confirmed in this run's own output.
This is stricter than the plan's Phase 2 text anticipated, which expected
only branch-name redaction.

Consequence:

- The 30-day window's `AgentType` table has 12 `Declared: sonnet` rows,
  matching the count of on-disk sonnet-pinned custom agents (`code-writer`
  plus 11 reviewer agents). None is individually identifiable by name.
- Summing `Observed: opus` across all 12 gives 245 dispatches, which
  exceeds G2's ≥20 floor. But this sum mixes `code-writer` with 11 other
  agent types and cannot be attributed to `code-writer` specifically.
- The ≥10-distinct-branches leg is unavailable for the same reason —
  branch names are redacted under the same multi-root condition.
- The one `Declared: opus` row in the table is individually identifiable,
  since it is the only such row, and matches `plan-architect`'s expected
  explicit-dispatch pattern (Runs=41, Requested opus(39)/fable(1)/sonnet(1),
  Observed opus(39)/other(1)/sonnet(1)) — not the slippage phenomenon G2 is
  looking for.

None of these four facts individually clears the G2 bar for `code-writer`.

**No reportable G2 verdict.** The corpus may or may not contain a usable
within-agent arm; this instrument, on this machine, cannot say.

### G3 — powered observational screen: adopt-candidate bar not met

186 G0-passing rows, `changed_files` terciles: small (1–4 files, n=78),
medium (5–7, n=50), large (8–84, n=58). Overall median
`opus_dollar_share_pct` is 0.00 (132/186 rows have zero Opus dollar share),
so each stratum's split is effectively "any Opus involvement in the PR" vs.
"none."

| Stratum (n) | Outcome | Low-dose mean | High-dose mean | Adopt-candidate direction | Bootstrap stability |
|---|---|---|---|---|---|
| small (78) | `commit_count` | 1.951 | 2.471 | unfavorable | 13.2% |
| small (78) | `review_comment_count` | 0.590 | 1.118 | unfavorable | 18.6% |
| medium (50) | `commit_count` | 2.147 | 4.000 | unfavorable | 0.4% |
| medium (50) | `review_comment_count` | 0.735 | 0.688 | favorable | 61.8% |
| large (58) | `commit_count` | 3.081 | 6.190 | unfavorable | 0.0% |
| large (58) | `review_comment_count` | 0.865 | 2.095 | unfavorable | 1.4% |

No stratum/outcome pair reaches the pre-registered ≥95%-stability
adopt-candidate bar. Every pair that reached high stability did so in the
confound-consistent direction (more Opus dose, more rework) — exactly what
`/plan-it`'s own complexity-selection criteria predict, which the plan's
G3 rule explicitly excludes from being read as refutation.

## Verdict: inconclusive

Per the plan's pre-registered, deliberately asymmetric G3 rule: reject is
reachable only from G2's within-agent arm, never from the observational
arm alone. G3 doesn't clear the adopt bar, and G2 — the only channel that
could supply a reject reading — is blocked by an instrument limitation on
this machine, not resolved either way. The honest reading is inconclusive
with a named identification bound, not a negative result: this study did
not show front-loading Opus fails to help, it showed this repo's current
corpus and instruments cannot distinguish "no effect" from "the one
plausible channel to test it is instrumentally unreachable here."

No `model:` pin is changed as a result of this study, per its own scope.

## Limits of this result

- **Ledger coverage and survivorship.** 186 of 365 merged PRs in the
  ledger's own date range (51%) — `pr-cost` only captures a PR whose branch
  still had local session-transcript activity at capture time, so the
  population skews toward more-recent and longer-lived branches, not a
  random sample of all merged PRs.
- **G1's censored-branch sensitivity.** The headline round-count median
  flips between 0 and 1 depending on whether a branch with zero transcript
  coverage is treated as a confirmed zero-round PR or excluded as unknown
  — a single-PR margin in either direction moves it. IQR is stable; the
  median is not a number to lean on precisely.
- **Unrandomized assignment.** Every treatment value in every arm
  (`plan_file_added`, `opus_dollar_share_pct`, harness plan-mode slippage)
  was decided historically by the engineer and by harness behavior, not
  randomized. `/plan-it`'s own DO-NOT-TRIGGER criteria select for
  complexity, so the observational arm is confounded in a known direction
  (see the Verdict section above for why this made G3's rule asymmetric).
- **G2 is an instrument gap, not a null result.** `subagent-mix` redacting
  `subagent_type` under the multi-root condition that is unconditional on
  this machine means the one arm designed to supply a "reject" reading
  could not run at all. A different machine with a single declared account
  root, or a future `subagent-mix` change that preserves `subagent_type`
  under redaction, would let it run.
- **Single-machine, single-repo corpus** (`claude-config`, this machine's 6
  declared accounts). Not validated against any other repo or account.
- **Rolling transcript retention.** Local session transcripts age out on a
  30-day rolling window. This is why Phase 3 runs on the durable ledger
  rather than the transcript corpus directly, and why re-running this study
  later will not reproduce these exact G1/G2 figures even with an identical
  method. The corpus itself shifted between two runs minutes apart in this
  session: same-day re-derivation drift measured 106 vs. 107 zero-round
  PRs, 24 vs. 26 censored branches, and 245 vs. 248 sonnet-declared/
  opus-observed dispatches.
