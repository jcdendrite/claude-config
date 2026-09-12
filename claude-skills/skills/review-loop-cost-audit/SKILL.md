---
name: review-loop-cost-audit
description: Diagnose whether a branch's review-loop spend is a stuck loop, plan-grinding, or ordinary large-diff work between review gates, by correlating review-round-cost's dated round table against a git-log code-freeze instant, via a corpus-wide sweep or a single-branch deep audit. For a narrative timeline of session prompts use transcript-narrative; for raw toolkit metrics use transcript-analysis.
argument-hint: "[branch-name | sweep] [output-path]"
---

## Step 0 — Scope and safety

Quote the resolved-scope header verbatim for every `transcript-analysis.py` subcommand run below, per `transcript-analysis/SKILL.md` § "Scope confirmation". Report a zero-match run with its header, never as a bare absence. Before trusting any figure, cross-check a `--this-repo` run against `cost --summary`, since `--this-repo` does not imply single-account scope. `cost --summary` is scoped to the active account only, on its own `Scope:` line.

This skill never invokes `marker.sh` and never invokes a review skill, directly or by dispatching a subagent to do either on its behalf.

Caveats beyond what is stated below are not restated here — see `transcript-analysis/SKILL.md` § "Caveats" and `docs/transcript-analysis.md`.

## Step 1 — Mode select

No argument, or `sweep` → sweep mode (Step 2). An explicit branch name → deep audit (Step 3). Deep audit never infers its subject from the current branch — the session running the audit is usually anchored in the audit's own worktree, not the branch under investigation.

## Step 2 — Sweep: rank candidates, never a verdict

```bash
python3 ~/.claude/scripts/transcript-analysis.py review-round-cost --this-repo
```

Read each branch's reconciliation line (`round $ X of Y branch $ (Z%)`) and rank by `Y`, the branch total — already a complete per-branch dollar ranking, so no session/turn-count screening is needed. Default cut is the top 20 branches by rank, not a dollar threshold, so the cut ports to a repo of any corpus size. When the caller instead gives an absolute minimum, use that.

Cross-check completeness:

```bash
python3 ~/.claude/scripts/transcript-analysis.py buckets --this-repo
```

A branch with zero rounds never appears in `review-round-cost`'s table, so the set difference against this `buckets` listing is the completeness check.

For each candidate, note the round mix (a `plan-review`-heavy mix is the plan-grinding signature) and run:

```bash
python3 ~/.claude/scripts/transcript-analysis.py subagent-mix --this-repo --branches <branch>
```

for within-branch reviewer skew and dispatches per round. A corpus-wide unfiltered `subagent-mix --this-repo` run supplies a ranking-aid denominator only — label it explicitly as not a threshold. Per-type dispatch share scales with both branch size and corpus age, so it ranks but does not threshold.

Output a ranked candidate list with the reason each ranked, stating plainly that the sweep has not distinguished thrash from ordinary large-diff work. When more than one candidate is plausible, use `AskUserQuestion` to hand the chosen branch to Step 3.

## Step 3 — Deep audit of one branch

In order:

**(a) Confirm scope.**
```bash
python3 ~/.claude/scripts/transcript-analysis.py buckets --this-repo --branches <branch>
```

**(b) Dated round table and reconciliation line.**
```bash
python3 ~/.claude/scripts/transcript-analysis.py review-round-cost --this-repo --branches <branch>
```

**(c) Resolve code-churn dates, tiered.**
- **Tier 1** — a live local ref: `git rev-parse --verify --quiet <branch>` succeeds → from the repo's worktree root, run `TZ=UTC git log --reverse --date=iso-local --format='commit %h %ad %s' --name-only origin/main..<branch>` (substitute the repo's own default-branch ref for `origin/main`). Keep this to one statement with no `$(...)`, per the worktree Bash-guard's Trigger A/B/E discipline.

  `TZ=UTC` is load-bearing: round timestamps are UTC, so the commit clock must be too. A date flag that renders `%ad` in the author's local zone puts a commit authored near local midnight in the adjacent UTC day. Compare the two clocks as instants, never as date strings.
- **Tier 2** — no local ref: resolve the PR number with `pr-link --repo owner/repo --this-repo --branches <branch>`, then `git fetch origin refs/pull/<N>/head:refs/pr-audit/<N> --no-tags`, re-run the Tier 1 `git log` call against `refs/pr-audit/<N>` in place of `<branch>`, then `git update-ref -d refs/pr-audit/<N>`. Use a named ref, not `FETCH_HEAD`. `FETCH_HEAD` is repo-global, so a concurrent fetch from another worktree can clobber it.
- **Tier 3** — both unavailable: stop and report the churn signal as unavailable. Label the outside-review-window share — the % of the branch's review-round dollars (from `review-round-cost`'s table) that falls outside its dated round windows — as non-diagnostic. This is a real, printed outcome, not a fallback to a weaker proxy.

**(d) Classify commits and take the freeze instant.** Classify each commit as code-bearing or artifact-only. Default artifact glob is `.claude/plans/*.md` (matching `pr-cost --plan-file-glob`'s own default). Accept an explicit glob argument to extend it. The **code-freeze instant** is the last code-bearing commit's author instant. A branch whose commits are one squashed WIP commit carries no usable per-commit date series — return **Inconclusive** (Step 4) rather than reading a single commit as an immediate freeze.

**(d2) Confirm the branch has actually frozen.** A freeze partition presupposes a freeze. Record the tip SHA and run `TZ=UTC git log -1 --date=iso-local --format='%h %cd' <branch>`, then apply both tests:

- Tip newer than the newest round in Step (b)'s table → the branch moved after the last round the corpus observed.
- Tip moved during this audit (re-read the ref after Step (c) and compare SHAs) → the corpus was read mid-flight.

Either one returns **Inconclusive — branch still active** (Step 4), but run Step (g)'s gate-denial scan first. That scan needs no freeze date, so a still-active branch can still carry a **Gate-denial churn** verdict. Report the tip SHA with the verdict so a later re-run can tell whether the branch moved since.

**(e) Partition.** Split Step (b)'s round table at the code-freeze instant and report the rounds and dollars that fall after it. When the freeze instant is at or after the newest round, say **partition vacuous — no rounds fall after the freeze** and return **Inconclusive**. Never report that case as zero post-freeze rounds. Zero-by-construction and zero-after-checking are different findings. Only the second is a clean bill of health.

**(f) Per-session skew.**
```bash
python3 ~/.claude/scripts/transcript-analysis.py subagent-mix --this-repo --branches <branch> --per-session
```
`--this-repo` alone routinely puts more than one root in scope, so expect this to be refused — fall back to the aggregate run below when it is.
```bash
python3 ~/.claude/scripts/transcript-analysis.py subagent-mix --this-repo --branches <branch>
```
Per-session granularity is unavailable under multi-root scope. Use the aggregate run's `Top subagent types` column, the per-branch dispatch-count breakdown, as the skew signal instead.

**(g) Characterize the post-freeze rounds.** Required before any thrash verdict in Step 4, and skipped only when Step (e) found no post-freeze rounds. The gate-denial scan below is the exception: it needs no freeze date and runs even when Step (d2) or Step (e) already returned Inconclusive. The counts from Steps (b)–(f) establish only that rounds ran after the freeze, not what they did. Invoke `transcript-narrative` for the branch and establish three things:

- Whether successive rounds of the same skill raised new findings each time or re-surfaced ones already raised.
- Whether repeated `ready-for-review` rounds re-ran an identical denial with the command shape unadapted.
- Whether the rounds are spread across sessions by crashes, stale worktree locks, or resumed handoffs rather than by re-review.

Where the narrative cannot settle which of these applies, the verdict is **Inconclusive**.

## Step 4 — Verdict rubric

The **post-freeze round share** — post-freeze rounds as a fraction of the branch's rounds — is the only discriminating signal. It yields a candidate rather than a verdict. Step 3(g)'s narrative read is what confirms or rejects the label. A thrash label is a claim that read must evidence, and never a threshold the share alone can clear. Never decide from the outside-review-window share: a stuck loop or plan-grinding branch and ordinary large-diff work can land in the same outside-review-window-share band, so the metric alone does not discriminate between them.

Report Step 3(f)'s skew and dispatches-per-round as descriptive context, never as criteria. Neither tracks the freeze partition: a branch with no post-freeze rounds can carry the corpus's highest within-branch skew. Requiring them as co-signals suppresses true positives.

**Verdict per round type, not per branch.** A branch's `code-review`, `plan-review`, and `ready-for-review` rounds routinely diverge — productive code review alongside genuine plan-grinding on one unimplemented slice, for instance. One label per branch destroys that finding. Emit a verdict for each round type that has post-freeze rounds, then a one-line branch summary naming the divergence when the verdicts differ.

- **Stuck loop** — post-freeze rounds of this type keep opening and re-surface findings already raised, rather than new ones each round.
- **Plan-grinding** — post-freeze `plan-review` rounds iterating a plan whose feature is still unimplemented, with post-freeze commits touching only artifact paths.
- **Gate-denial churn** — post-freeze `ready-for-review` rounds re-running an identical denial across sessions with the command shape unadapted. Distinct from stuck loop: nothing is being re-reviewed.
- **Legitimate large-diff work** — code-bearing commits spread across the branch's whole date range (no early freeze), whatever the outside-review-window share reads.
- **Inconclusive** — Tier 3, a single squashed commit (Step 3d), a branch still active (Step 3d2), or a vacuous partition (Step 3e).

State the non-discrimination rule in words. Carry no dollar total, no per-branch cost share, and no figure from either corpus into the verdict text — this is a repo-wide publication rule, not a per-branch choice.

## Step 5 — Artifact and return

Write one file: to the caller-supplied output-path argument, or under `mktemp -d` when none is given — state plainly to the caller that the `mktemp -d` default is temporary. Before writing to a caller-supplied path, confirm it does not resolve inside a git-tracked tree unless that tree's `.gitignore` covers it, matching `transcript-narrative/SKILL.md`'s own guard.

The file opens with a not-for-publication line, then carries the quoted scope headers, the round table, the churn table, the verdict, and which caveats applied. Return only the path, the verdict, and the two or three discriminating facts — never the tables inline. A subagent that invokes this skill by name returns the same three things, keeping every table in the subagent's own context and the artifact file.
