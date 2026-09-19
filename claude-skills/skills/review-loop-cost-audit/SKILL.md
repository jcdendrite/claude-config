---
name: review-loop-cost-audit
description: Decompose why a branch's review loop cost what it did and ran as many rounds as it did, by where the dollars concentrated and what triggered each expensive round, via a corpus-wide sweep or a single-branch deep audit. Reports shares, causes, and cheaper levers; a code-freeze thrash test is one flag among several. For a narrative timeline of session prompts use transcript-narrative; for raw toolkit metrics use transcript-analysis.
argument-hint: "[branch-name | sweep] [output-path]"
---

## Step 0 — Scope and safety

Quote the resolved-scope header verbatim for every `transcript-analysis.py` subcommand run below, per `transcript-analysis/SKILL.md` § "Scope confirmation". Report a zero-match run with its header, never as a bare absence. Before trusting any figure, cross-check a `--this-repo` run against `cost --summary`, since `--this-repo` does not imply single-account scope. `cost --summary` is scoped to the active account only, on its own `Scope:` line. `--branches` matches the branch-name string only and pools same-named branches across roots, so record which roots the branch's sessions actually live in rather than assuming one.

This skill never invokes `marker.sh` and never invokes a review skill, directly or by dispatching a subagent to do either on its behalf.

`judgment-pair` and `user-input` print raw human prompt text regardless of `--redact`. Keep that text in the artifact file only, never in the return or in any tracked file.

Caveats beyond what is stated below are not restated here — see `transcript-analysis/SKILL.md` § "Caveats" and `docs/transcript-analysis.md`.

## Step 1 — Mode select

No argument, or `sweep` → sweep mode (Step 2). An explicit branch name → deep audit (Step 3). Deep audit never infers its subject from the current branch, except when the caller's handoff or request names this skill with no target and the branch it is handing off is unambiguous. The session running the audit is otherwise usually anchored in the audit's own worktree, not the branch under investigation.

## Step 2 — Sweep: rank candidates, never a verdict

```bash
python3 ~/.claude/scripts/transcript-analysis.py review-round-cost --this-repo
```

Read each branch's reconciliation line (`round $ X of Y branch $ (Z%)`) and rank by `Y`, the branch total. Also note each branch's top-round share of `X`, computed from its round table — a branch whose spend sits in one round and a branch whose spend is spread across many need different audits. Default cut is the top 20 branches by rank, not a dollar threshold, so the cut ports to a repo of any corpus size. When the caller instead gives an absolute minimum, use that.

Cross-check completeness:

```bash
python3 ~/.claude/scripts/transcript-analysis.py buckets --this-repo
```

A branch with zero rounds never appears in `review-round-cost`'s table, so the set difference against this `buckets` listing is the completeness check.

For each candidate, note the round mix and run:

```bash
python3 ~/.claude/scripts/transcript-analysis.py subagent-mix --this-repo --branches <branch>
```

for within-branch reviewer skew and dispatches per round. A corpus-wide unfiltered `subagent-mix --this-repo` run supplies a ranking-aid denominator only — label it explicitly as not a threshold. Per-type dispatch share scales with both branch size and corpus age, so it ranks but does not threshold.

Output a ranked candidate list with the reason each ranked, stating plainly that the sweep has not decomposed any branch's cost. When more than one candidate is plausible, use `AskUserQuestion` to hand the chosen branch to Step 3.

## Step 3 — Deep audit of one branch

In order:

**(a) Confirm scope.**
```bash
python3 ~/.claude/scripts/transcript-analysis.py buckets --this-repo --branches <branch>
```

**(b) Dated round table, round instants, and reconciliation line.**
```bash
python3 ~/.claude/scripts/transcript-analysis.py review-round-cost --this-repo --branches <branch>
```
The table is date-only. Take round instants from the event timeline, which prints an ISO instant per skill invocation, reviewer spawn, architect consult, and denial:
```bash
python3 ~/.claude/scripts/transcript-analysis.py review-trace --this-repo --branches <branch>
```
A round is one skill invocation, not one review pass: its window runs from its invocation to the session's next skill invocation. A single invocation can hold several reviewer waves separated by fix work, so list each round's reviewer spawns with their instants and count the waves, reading a gap of tens of minutes as a wave boundary. Report the waves per round next to the round's dollars. Never describe a multi-wave round as one review.

**(c) Cost decomposition.** Pure arithmetic on the Step (b) table, plus one check.

- **Concentration:** the top round's share of round dollars and of branch dollars. Also the fewest rounds that cover 60% of round dollars.
- **Main vs fan-out:** for each concentrated round, the split between `main $` and `agent $`, and its `agents` count. Main-heavy means the orchestrator did the work; agent-heavy means fan-out width. The remedies differ.
- **Window check:** a concentrated round's window may contain more than review work. Confirm from Step (b)'s timeline that the window holds reviewer waves, and name what else it spans (consults, code-writer dispatches, hook denials, gaps over an hour). A round whose main-thread cost is unexplained by its timeline is reported as a window artifact, not as an expensive orchestrator.
- **Round mix:** `ready-for-review` rounds typically dispatch no agents and are cheap. Report review rounds separately from gate re-runs; "N rounds" alone overstates the review effort when most are gate re-runs.
- **Non-round share:** `review-round-cost` prices only a round's own window, so the fix a round causes lands outside it and the residual includes implementation. State that on every line that reports the residual. Name its components by count only — code-writer dispatches, architect consults, resyncs with the default branch, session-startup burn from the branch's row in `workstream-cost --this-repo` — since the toolkit yields one reconciliation number, not a split.

**(d) Resolve code-churn dates, tiered.**
- **Tier 1** — a live local ref: `git rev-parse --verify --quiet <branch>` succeeds → from the repo's worktree root, run `TZ=UTC git log --reverse --date=iso-local --format='commit %h %ad %s' --name-only origin/main..<branch>` (substitute the repo's own default-branch ref for `origin/main`). Keep this to one statement with no `$(...)`, per the worktree Bash-guard's Trigger A/B/E discipline.

  `TZ=UTC` is load-bearing: round timestamps are UTC, so the commit clock must be too. A date flag that renders `%ad` in the author's local zone puts a commit authored near local midnight in the adjacent UTC day. Compare the two clocks as instants, never as date strings. `%ad` is the author instant; use it for every ordering test below.
- **Tier 2** — no local ref: resolve the PR number with `pr-link --repo owner/repo --this-repo --branches <branch>`, then `git fetch origin refs/pull/<N>/head:refs/pr-audit/<N> --no-tags`, re-run the Tier 1 `git log` call against `refs/pr-audit/<N>` in place of `<branch>`, then `git update-ref -d refs/pr-audit/<N>`. Use a named ref, not `FETCH_HEAD`. `FETCH_HEAD` is repo-global, so a concurrent fetch from another worktree can clobber it.
- **Tier 3** — both unavailable: report the churn signal as unavailable and skip Step (e)'s commit joins. Do not substitute a weaker proxy for it. Steps (c), (f) and (g) still run.

Classify each commit as code-bearing or artifact-only. Default artifact glob is `.claude/plans/*.md` (matching `pr-cost --plan-file-glob`'s own default). Accept an explicit glob argument to extend it. A branch whose commits are one squashed WIP commit carries no usable per-commit date series; skip the joins in (e) and say why. Never read a single commit as an immediate freeze.

Record the tip SHA and re-read it after this step. A changed SHA means the corpus was read mid-flight; report it with the results so a later re-run can tell whether the branch moved. Committer time alone moving (a resync or rebase) is not new work and does not make the branch active.

**(e) Round→commit interleave and the freeze flag.** Join each round's window to the commits authored inside or after it, by author instant. Report per round: commits authored between its invocation and the session's next round, and the rounds that produced no commit. Then the **code-freeze instant**, the last code-bearing commit's author instant, and the flag: post-freeze rounds N, and their share of round dollars.

- A freeze partition presupposes rounds after the freeze. When the freeze instant is after the newest round's invocation, print **no rounds start after the freeze**. That is zero-by-construction, a healthy loop that ends on a fix. It is not the same finding as zero-after-checking, and only zero-after-checking is a clean bill of health. Only rounds that started after the freeze can carry the thrash flags in Step 4.
- Neither outcome is an audit verdict. The audit always continues to (f) and (g).

**(f) Descriptive context.** Report, without using as criteria:
```bash
python3 ~/.claude/scripts/transcript-analysis.py subagent-mix --this-repo --branches <branch>
python3 ~/.claude/scripts/transcript-analysis.py review-trace --this-repo --branches <branch> --deny-summary
python3 ~/.claude/scripts/transcript-analysis.py fail-seq --this-repo --branches <branch>
```
`--per-session` on `subagent-mix` is refused under a multi-root `--this-repo` scope, so use the aggregate run's `Top subagent types` column as the skew signal. The denial census names which gate produced the denials and which command shapes recur; a gate that denies a benign command shape repeatedly is a retry cost inside the review budget. Read `fail-seq` as a one-line check on whether debugging drove cost.

**(g) Attribute a trigger to each sampled round.** The sample is the rounds Step (c) needs to cover 60% of round dollars, plus every round Step (e) found with no following commit. Do not read all rounds.

Extract the human decision points:
```bash
python3 ~/.claude/scripts/transcript-analysis.py judgment-pair --this-repo --branches <branch> --out <artifact-dir>/judgment-pairs.md
```
Invoke `transcript-narrative` for the branch only for a sampled round the pairs and the round's own findings text leave unattributed.

Assign each sampled round one trigger class:

- **new-finding** — the round's reviewers raised an issue not raised before.
- **fix-induced** — the round's findings are defects the previous round's own fix introduced.
- **human-scope-expansion** — a human turn added work (promoted a deferred finding, overrode a recommendation, widened scope) before the round.
- **gate-denial** — the round re-ran because a hook denied a step, not because of review content.
- **pipeline-mandatory** — the pipeline required the round regardless of findings (a gate re-run after a push).
- **unattributed** — no primary source settles it.

A class is assigned only from a primary source: the round's own findings text or a human turn, cited by session and turn or by findings-file path. Commit titles may corroborate a class and never establish one. Default to `unattributed` and print the count; a table that is mostly `unattributed` is a useful audit, and one that is confidently mislabeled is not. Where the read cannot tell fix-induced from new-finding, use `unattributed`.

Also note whether rounds are spread across sessions by crashes, stale worktree locks, or resumed handoffs rather than by re-review. That is session churn, not loop churn, and it does not support a stuck-loop flag.

Causes carry no judgment. `human-scope-expansion` and `new-finding` are often the branch working correctly. Only the flags in Step 4 carry a judgment.

## Step 4 — Report

Emit the six parts below. Carry no dollar total, no per-branch cost share, and no figure from either corpus into text that leaves the artifact — this is a repo-wide publication rule, not a per-branch choice. Within the artifact, quote shares and counts, and quote dollars only in the round table.

1. **Headline** — rounds split by type, with the review-round versus gate-re-run split and the concentration fact. Name the waves-per-round finding when a round held more than one wave.
2. **Where the money went** — three shares: top round of round dollars, round versus non-round, and main versus fan-out within the concentrated rounds. State the non-round caveat from Step (c).
3. **Why the rounds happened** — the trigger-class table over the sampled rounds, `unattributed` counted explicitly, each row citing its source.
4. **Flags** — each raised or not raised, with its evidence:
   - **Concentration** — one round or few rounds carry most of the round dollars, after the Step (c) window check.
   - **Stuck loop** — rounds that started after the freeze keep re-surfacing findings already raised.
   - **Plan-grinding** — rounds that started after the freeze iterate a plan whose feature is unimplemented, with post-freeze commits touching only artifact paths.
   - **Gate-denial churn** — rounds that started after the freeze re-ran an identical denial with the command shape unadapted. Nothing is being re-reviewed.
   - **Denial-retry waste** — one gate's benign-command denials recurring across reviewer spawns.
   - **Mandatory-round inflation** — gate re-runs make up most of the round count.
   - **Legitimate large-diff work** — code-bearing commits spread across the branch's whole date range with no early freeze.

   The four thrash flags need rounds that started after the freeze, so they are not-evaluable when Step (e) printed that none did. The post-freeze round share only nominates a candidate: raise a thrash flag on the Step (g) read of those rounds' findings, never on the share alone, and name the round type it applies to (`code-review`, `plan-review`, or `ready-for-review`), since the types routinely diverge on one branch. Never decide from the outside-review-window share: a stuck loop and ordinary large-diff work can land in the same band. Reviewer skew and dispatches-per-round stay descriptive, not criteria, because neither tracks the freeze partition.
5. **What would have been cheaper** — one to three levers tied to the top cause, each naming the evidence it rests on. A lever that rests only on an `unattributed` class is not a lever. This part is what answers why the branch cost what it did.
6. **Caveats** — which of the Step 0 caveats applied, the tip SHA, and the roots the branch's sessions live in.

## Step 5 — Artifact and return

Write one file: to the caller-supplied output-path argument, or under `mktemp -d` when none is given — state plainly to the caller that the `mktemp -d` default is temporary. Before writing to a caller-supplied path, confirm it does not resolve inside a git-tracked tree unless that tree's `.gitignore` covers it, matching `transcript-narrative/SKILL.md`'s own guard.

The file opens with a not-for-publication line, then carries the quoted scope headers, the round table with waves per round, the churn table, the six report parts, and which caveats applied. Return only the path, the headline, the top cause, and the one or two levers — as shares and counts, with no dollars and no quoted prompt text, never the tables inline. A subagent that invokes this skill by name returns the same items, keeping every table in the subagent's own context and the artifact file. For a publish-ready aggregate, use `cost-counts --this-repo --branches <branch>` rather than figures from this artifact.
