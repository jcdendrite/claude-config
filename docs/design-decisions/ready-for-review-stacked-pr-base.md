# `/ready-for-review` for a branch stacked on another open PR's branch

*2026-10-23.*

`/ready-for-review` opens its PR last (step 8), and the push gate requires the gate's completion marker before `gh pr create` runs. With no stack base named, a stacked branch's cumulative diff, divergence check and fidelity-review diff file all include the base PR's already-reviewed work. The engineer names the stack base once, in gh's own per-branch key, and every base-dependent step uses it or stops.

The stops, notices, name-shape rules and caps live in the headers of `claude/.claude/scripts/branch-divergence-status.sh`, `claude/.claude/scripts/pr-diff-against-base.sh` and `_lib_configured_stack_base` (`claude/.claude/hooks/_lib.sh`). This file records the reasoning and consequences those headers do not show.

## Why `branch.<branch>.gh-merge-base`

- `marker.sh status` recomputes the `cumulative-review` hash by running `pr-diff-against-base.sh` with no arguments, so the base has to be repository state. A flag on the script or an environment variable would not reach that call, and an environment variable does not survive across Bash tool calls.
- gh's PR-create command reads this key when `--base` is absent. A custom key such as `branch.<name>.rfr-base` would work for the gate, but a hand-run PR create would still target the default branch.
- Pointing the branch's upstream (`branch.<name>.merge`) at the stack base would break steps 6 and 8, which rely on the branch's own `origin/<branch>` tracking.
- gh's checkout and create code never write the key. In the `cli/cli` trunk source, `pkg/cmd/pr/checkout/checkout.go` writes only `branch.<branch>.remote`, `pushRemote` and `merge`, and `pkg/cmd/pr/create/create.go` only reads the key. That covers those two files only.
- Step 1 sets the key only from the engineer's own words.

## The one rule

- With the key set, a step uses its value or stops.
- With no key set, `pr-diff-against-base.sh` resolves its base from the base of the PR `gh pr view` returns for this branch (merged or closed included), else the default branch. `branch-divergence-status.sh` reads an open PR's base itself, as "The no-key read" below states.
- Both scripts measure through the fully qualified `refs/remotes/origin/<base>` ref, and a failed git read of HEAD or the key stops the step.
- With no key set, only `branch-divergence-status.sh` fetches, and it fetches the base it measures.
- A stop exits non-zero, and the stop itself is one stderr line.
  - An unusable value gets its line from `_lib_print_unusable_stack_base` (`claude/.claude/hooks/_lib.sh`), the one home of the fix text. The line says the engineer runs the fix, not the agent.
  - A git read failure gets its own line from `_lib_stack_base_git`. A key edit would not fix it, because the failure is git's, not the value's.
- The name-shape tests are explicit because `git check-ref-format` accepts a leading `-`, shell metacharacters, quotes, non-ASCII text, bidi controls and `HEAD`, and rejects `..`, `@{` and spaces.
- A step that composes a command from a base name takes it from the `Default branch:` or `Base branch:` line the divergence recipe printed, not from the `baseRefName` that `gh pr view` returned. A key or PR-derived base on that line has passed the shared name-shape check.

Reasoning behind individual stops:

- **A stack base that cannot be fetched.** A timeout and a branch deleted on origin fail the same way, so the stop line says to retry before changing the key.
- **A PR-derived base that cannot be fetched.** The stop does not use the key-fix formatter, because no key was set.
- **An OPEN PR targeting another base.** `pr-diff-against-base.sh` stops at step 3, after any step-1 sync. It ignores a merged or closed PR's base, because `gh pr view` with no argument can return one.
- **`/respond-pr`'s precheck.** It runs the same recipe and handles a failed run as `respond-pr/SKILL.md` step 3 states.

## The no-key read

An already-open stacked PR has no key, so without this read its sync check would measure the default branch. With no key set, `branch-divergence-status.sh` therefore reads an open PR's base itself and measures it when it differs from the default branch.

A set key wins and no `gh` call is made. A set key that disagrees with an open PR keeps stopping at `pr-diff-against-base.sh`, with no new stop in the divergence script.

`pr-diff-against-base.sh` never fetches, so it can read a deleted stack base's stale local ref. Step 1's fetch is what catches a deleted stack base, and step 1 runs before step 3. That ordering is a dependency. `git branch -D` removes the deleted branch's `branch.<name>` section, so a reused branch name does not inherit a stale key.

A failed `status` recompute never reads `live`, so step 3 runs the script itself and halts on its failure. Every stop therefore lands before step 7's marker, on the CI-watch path too.

`/respond-pr`'s precheck is a defined arm, not a residual: `respond-pr/SKILL.md` step 3 names CONFLICT, CLEAN and a failed recipe. The failed-recipe abort follows the CONFLICT arm's marker handling, so step 9's `marker.sh deactivate respond-pr` is not reached and the marker stays armed until eviction. That armed state is accepted. While it is armed, `require-respond-pr.sh` releases every surface it gates, not only comment commands. `docs/hooks.md` § "Gate deadlock recovery" holds the eviction conditions, the per-call refresh and the missing repo scope. That gate is a cooperative mistake-catcher, not a security boundary.

When the stack base merged, the stop line's fix is `git rebase --onto origin/<merged-into> <old stack-base commit>` followed by `git config --unset-all` on the key. After a squash merge, a plain `git rebase origin/<merged-into>` replays the base's commits, because the squash commit is one combined patch, not one patch per base commit. The line names no fork point.

## Step 8 and the redaction hook

Step 8 passes `--base "<name>"` when step 6 printed `Base branch: <name>`. The push gate matches the create command by the words `gh pr create` alone, so the flag rides on the same completion marker. The redaction hook scans the full command text, so a tracker-named stack base is denied in an opted-in repo as a tracker-ID title would be, and the engineer decides what to do. Relying on gh's key read instead would keep the base out of the scanned text.

## Markers stay unchanged

No marker changes: [ready-for-review-cumulative-diff-cache.md](ready-for-review-cumulative-diff-cache.md) records the decision that the `cumulative-review` hash excludes the base, and `status` recomputes through the same script that reads the same key.

## Push-gate wording

The push gate's two deny strings that named the cumulative diff (`gh pr ready` and a push to a branch with an open PR) and `docs/skills.md`'s `/ready-for-review` entry say "PR-vs-base diff", the repository's term for that diff. The gate computes no base, and "default branch" would misname a stacked branch's diff.

## Alternatives set aside

- **A `--base` flag on `pr-diff-against-base.sh`, or an environment variable.** Neither reaches `marker.sh status`.
- **Auto-detecting the base from open PR heads.** It needs a network scan and a heuristic the engineer would confirm anyway.
- **One resolver with a precedence chain** (open PR, key, closed PR, default). A set key wins in both scripts, so the key path is already shared. The no-key paths stay separate because they accept different PRs. The divergence script takes only an OPEN PR's base, shape-checks it and caps `gh` at 10 seconds. `pr-diff-against-base.sh` takes the base of a PR in any state and runs `gh` with no cap. The "merged or closed PR" residual records the resulting split.
- **Step 1 writing the key from an open PR's base.** It persists a key from text read off a remote. Reading the PR base inside the script writes no key and no config, and the shared name-shape check vets the name in code, not in prose.
- **A named trigger per cause, each with its own remedy text.** The causes differ only in the reason clause.
- **Letting an open PR's base silently override a disagreeing key.** It hides a retarget or a wrong base.
- **Folding the base ref into the `cumulative-review` hash.** It contradicts the decision cited above.
- **Treating a key equal to the default branch as unset.** Such a key measures against the same branch, so only the output label would differ.
- **A check that the stack base's own PR merged**, with an acknowledgement key. It is more machinery than the fail-closed rule needs.
- **Widening the sync skill's frontmatter trigger to name a stack base.** The trigger clause reads "syncing a feature branch with the default branch". Of the entry points this change touches, `ready-for-review` step 1 and the SessionStart advisory invoke the skill by name. `respond-pr`'s CONFLICT arm routes to it instead, and that route depends on the description's first line, "Rebase vs merge a feature branch; force-push safely." The description loads for every stow consumer.

## Residuals

- **A stack base that merged but whose branch stays on origin.** GitHub retargets a stacked PR only when its merged base branch is deleted, and deleting head branches on merge is a per-repository setting. In a repository that keeps merged branches no stop fires, and the PR keeps targeting the merged branch.
- **A merged or closed PR with no key set.** `branch-divergence-status.sh` ignores it and measures the default branch, while `pr-diff-against-base.sh` still reads its base, so one run can sync against one branch and review against another. Unifying them means changing one script's PR-state handling, which this change leaves alone.
- **The SessionStart advisory** (`check-branch-divergence.sh`) measures a stacked branch against the default branch.
  - The hook fetches and measures through `origin/<default branch>`, and its header says it never reads the key.
  - Under a narrowed `remote.origin.fetch` the advisory can read a stale ref.
  - `git-feature-branch-sync/SKILL.md` § "Detecting divergence" tells the agent what to do after the advisory.
- **Any `gh pr view` failure or unparseable reply on the key path** reads as no open PR, so that run skips the open-PR check, for an existing PR as well as a new one.
  - A persistent failure (no gh, no auth, a wrong host) disables the open-PR stop on every run.
  - `marker.sh status` discards the script's stderr, so a recompute through `status` gives no notice of the skipped check.
- **A stale key after the stack base merges** hard-stops `/respond-pr`'s precheck.
  - Nothing unsets the key on merge.
  - Once the base branch is gone from origin, the divergence script exits 1 on its fetch stop. The precheck re-runs it once and aborts. The skill's instruction, not a gate, keeps it from posting a review reply until the engineer applies the stop line's fix.
  - This is the accepted post-merge state for `/respond-pr` as well as for the gate. "No rebase recipe" below covers the missing fork-point.
- **`respond-pr` measures against the key, not the open PR's base.** With the key set, its precheck runs `branch-divergence-status.sh`, which reads the key and never the PR. An OPEN PR retargeted to another branch while the stack base stays on origin therefore passes the precheck. `/ready-for-review` step 3 then stops on the mismatch, before the marker the push gate requires.
- **An already-open stacked PR** has no key. The divergence script reads the PR's base with no key set, so the sync check measures it, and `pr-diff-against-base.sh` already resolves the same base for the cumulative diff. When a key is set and disagrees with the PR's base, step 3 stops, and the divergence script does not.
- **The review base is trusted from one write.**
  - Condition: the key's value alone selects the review scope, and any origin branch that shares history passes.
  - Consequence: a wrong value narrows the diff with no mechanical check beyond the origin fetch, and an empty diff halts at step 3. The missing check applies whenever step 3 finds no OPEN PR: before the PR exists, when only a merged or closed PR exists, and on the `gh pr view` failures or unparseable replies the residual above lists.
  - An OPEN PR with a working gh and a key differing from its base stops at step 3 (`pr-diff-against-base.sh`), as "The one rule" states.
  - Unsetting the key falls back to the wider review.
  - Pre-check: after step 1's fetch, `git log --oneline origin/<stack-base>..HEAD` lists the commits the review covers.
- **A stack base whose history is unrelated to HEAD**, at the divergence script.
  - Condition: the base shares no history with HEAD, so `git merge-tree` cannot compute a verdict.
  - Consequence: `/respond-pr`'s precheck re-runs the recipe once and then stops, and step 1's sync stops before any rebase, so the branch is not rewritten.
  - Signal: `Behind:` equals the base's whole commit count and no `Trial merge:` line follows.
  - `TestBehindWithUnrelatedHistories` in `test_branch_divergence_status.py` exercises the default-branch form, an orphan branch with no key set. The key path reaches the same `_lib_trial_merge_verdict` call.
  - The no-key form is recorded in [ready-for-review-base-drift.md](ready-for-review-base-drift.md).
- **No rebase recipe** for a squash-merged stack base, or for a stack base rewritten by a force-push, whose changed commits a plain `git rebase origin/<merged-into>` replays. `git-feature-branch-sync` carries none, and the engineer finds the stack base's last commit before the merge. A force-pushed stack base trips no stop, so step 1 sees behind > 0 and its sync does the plain rebase that replays the rewritten base commits. Only afterwards does the engineer find that commit and redo the rebase with `--onto`.
