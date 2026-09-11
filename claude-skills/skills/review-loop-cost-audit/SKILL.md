---
name: review-loop-cost-audit
description: Diagnose whether a branch's review-loop spend is a stuck loop, plan-grinding, or ordinary large-diff work between review gates, by correlating review-round-cost's dated round table against a git-log code-freeze date, via a corpus-wide sweep or a single-branch deep audit. For a narrative timeline of session prompts use transcript-narrative; for raw toolkit metrics use transcript-analysis.
argument-hint: "[branch-name | sweep] [output-path]"
---

## Step 0 — Scope and safety

Quote the resolved-scope header verbatim for every `transcript-analysis.py` subcommand run below, per `transcript-analysis/SKILL.md` § "Scope confirmation" — report a zero-match run with its header, never as a bare absence. Before trusting any figure, cross-check a `--this-repo` run against `cost --summary`: `--this-repo` does not imply single-account scope, so this confirms no cross-account pooling before any number here is used. `cost --summary` is scoped to the active account only, stated on its own `Scope:` line.

This skill never invokes `marker.sh` and never invokes a review skill, directly or by dispatching a subagent to do either on its behalf.

Caveats beyond what is stated below are not restated here — see `transcript-analysis/SKILL.md` § "Caveats" and `docs/transcript-analysis.md`.

## Step 1 — Mode select

No argument, or `sweep` → sweep mode (Step 2). An explicit branch name → deep audit (Step 3). Deep audit never infers its subject from the current branch — the session running the audit is usually anchored in the audit's own worktree, not the branch under investigation.

## Step 2 — Sweep: rank candidates, never a verdict

```bash
python3 ~/.claude/scripts/transcript-analysis.py review-round-cost --this-repo
```

Read each branch's reconciliation line (`round $ X of Y branch $ (Z%)`) and rank by `Y`, the branch total — already a complete per-branch dollar ranking, so no session/turn-count screening is needed. Default cut is the top 20 branches by rank, not a dollar threshold, so the cut ports to a repo of any corpus size; when the caller instead gives an absolute minimum, use that.

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
- **Tier 1** — a live local ref: `git rev-parse --verify --quiet <branch>` succeeds → from the repo's worktree root, run `git log --reverse --date=short --format='commit %h %ad %s' --name-only origin/main..<branch>` (substitute the repo's own default-branch ref for `origin/main`) (keep this to one statement, no `$(...)`, per the worktree Bash-guard's Trigger A/B/E discipline).
- **Tier 2** — no local ref: resolve the PR number with `pr-link --repo owner/repo --this-repo --branches <branch>`, then `git fetch origin refs/pull/<N>/head:refs/pr-audit/<N> --no-tags`, re-run the Tier 1 `git log` call against `refs/pr-audit/<N>` in place of `<branch>`, then `git update-ref -d refs/pr-audit/<N>` — a named ref, not `FETCH_HEAD`, since `FETCH_HEAD` is repo-global and a concurrent fetch from another worktree clobbers it.
- **Tier 3** — both unavailable: stop and report the churn signal as unavailable, with the outside-review-window share — the % of the branch's review-round dollars, from `review-round-cost`'s table, falling outside its dated round windows — labeled non-diagnostic. This is a real, printed outcome, not a fallback to a weaker proxy.

**(d) Classify commits and take the freeze date.** Classify each commit as code-bearing or artifact-only. Default artifact glob is `.claude/plans/*.md` (matching `pr-cost --plan-file-glob`'s own default); accept an explicit glob argument to extend it. The **code-freeze date** is the last code-bearing commit's date. A branch whose commits are one squashed WIP commit carries no usable per-commit date series — return **Inconclusive** (Step 4) rather than reading a single commit as an immediate freeze.

**(e) Partition.** Split Step (b)'s round table at the code-freeze date and report the rounds and dollars that fall after it.

**(f) Per-session skew.**
```bash
python3 ~/.claude/scripts/transcript-analysis.py subagent-mix --this-repo --branches <branch> --per-session
```
`--this-repo` alone routinely puts more than one root in scope, so expect this to be refused — fall back to the aggregate run below when it is.
```bash
python3 ~/.claude/scripts/transcript-analysis.py subagent-mix --this-repo --branches <branch>
```
Per-session granularity is unavailable under multi-root scope. Use the aggregate run's `Top subagent types` column, the per-branch dispatch-count breakdown, as the skew signal instead.

## Step 4 — Verdict rubric

Decide only from the freeze partition (Step 3e) and skew (Step 3f) — **never from the outside-review-window share**: a stuck loop or plan-grinding branch and ordinary large-diff work can land in the same outside-review-window-share band, so the metric alone does not discriminate between them.

- **Stuck loop** — rounds keep opening after the freeze date and carry most of the branch's post-freeze dollars, with one reviewer type taking an outsized within-branch share and a high dispatches-per-round ratio.
- **Plan-grinding** — the same pattern, with post-freeze rounds predominantly `plan-review` and post-freeze commits touching only artifact paths.
- **Legitimate large-diff work** — code-bearing commits spread across the branch's whole date range (no early freeze), whatever the outside-review-window share reads.
- **Inconclusive** — Tier 3, or a single squashed commit (Step 3d).

State the non-discrimination rule in words. Carry no dollar total, no per-branch cost share, and no figure from either corpus into the verdict text — this is a repo-wide publication rule, not a per-branch choice.

## Step 5 — Artifact and return

Write one file: to the caller-supplied output-path argument, or under `mktemp -d` when none is given — state plainly to the caller that the `mktemp -d` default is temporary. Before writing to a caller-supplied path, confirm it does not resolve inside a git-tracked tree unless that tree's `.gitignore` covers it, matching `transcript-narrative/SKILL.md`'s own guard.

The file opens with a not-for-publication line, then carries the quoted scope headers, the round table, the churn table, the verdict, and which caveats applied. Return only the path, the verdict, and the two or three discriminating facts — never the tables inline. A subagent that invokes this skill by name returns the same three things, keeping every table in the subagent's own context and the artifact file.
