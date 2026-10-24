# `/ready-for-review` for a branch stacked on another open PR's branch

*2026-10-23.*

`/ready-for-review` opens its PR last (step 8), and the push gate requires the gate's completion marker before `gh pr create` runs. Every base-dependent step therefore took its base from the default branch, and a stacked branch's cumulative diff, divergence check and fidelity-review diff file all included the base PR's already-reviewed work. The engineer now names the stack base once, in gh's own per-branch key, and every base-dependent step uses it or stops.

## Why `branch.<branch>.gh-merge-base`

- `marker.sh status` recomputes the `cumulative-review` hash by running `pr-diff-against-base.sh` with no arguments, so the base has to be repository state. A flag on the script or an environment variable would not reach that call, and an environment variable does not survive across Bash tool calls.
- gh's PR-create command reads this key when `--base` is absent. A custom key such as `branch.<name>.rfr-base` would work for the gate, but a hand-run PR create would still target the default branch.
- Pointing the branch's upstream (`branch.<name>.merge`) at the stack base would break steps 6 and 8, which rely on the branch's own `origin/<branch>` tracking.
- gh's checkout and create code never write the key. In the `cli/cli` trunk source, `pkg/cmd/pr/checkout/checkout.go` writes only `branch.<branch>.remote`, `pushRemote` and `merge`, and `pkg/cmd/pr/create/create.go` only reads the key. That covers those two files only.
- Step 1 sets the key only from the engineer's own words, never by inference.

## The one rule

With the key set, a step uses its value or stops. With no key set, every step resolves its base as the default-branch path does, with three changes: a failed git read of HEAD or the key stops the step, the default-branch fetch uses a named destination plus a verify (a verify failure prints the "failed or timed out" line), and measurement goes through the fully qualified `refs/remotes/origin/<base>` ref. A stop exits non-zero with one stderr line. An unusable value gets its line from `_lib_print_unusable_stack_base` (`claude/.claude/hooks/_lib.sh`), the one home of the fix text. That line names the script, the key, its value and the reason, and says the engineer runs the fix, not the agent. A git read failure prints its own line instead, naming the failed git call and its status and saying to retry the command and, if it fails again, to run that git call by hand to see its error, because the failure is git's, not the value's, and a key edit would not fix it. All three git calls the helper makes (`symbolic-ref`, `config` and `check-ref-format`) go through `_lib_stack_base_git`, the one home of that line. The HEAD and key reads run before any value exists, so their stop can fire on a branch with no key set. `check-ref-format` runs only after a value was read.

Where each stop fires:

- **The value cannot be read or is not a usable branch name** (empty, leading `-` or `origin/`, rejected by `git check-ref-format "refs/heads/<value>"`, holding a character outside `[A-Za-z0-9._/-]`, `HEAD`, or this branch's own name): `_lib_configured_stack_base`, called by both scripts. `git check-ref-format` accepts a leading `-`, shell metacharacters, quotes, non-ASCII text, bidi controls and `HEAD`, and rejects `..`, `@{` and spaces, so those tests are explicit.
- **The stack base cannot be fetched from origin:** `branch-divergence-status.sh`, at steps 1 and 6. A timeout and a branch deleted on origin fail the same way, so the reason says to retry the command before changing the key. `/respond-pr`'s precheck runs the same recipe, re-runs a failed run once, then stops naming the PR number and the recipe's stderr, without routing to `/git-feature-branch-sync`, whose detection runs the same script. The fetch uses `+refs/heads/<base>:refs/remotes/origin/<base>` and then verifies the ref, so a narrowed `remote.origin.fetch` cannot leave it missing or stale.
- **No merge-base with `origin/<value>`:** `pr-diff-against-base.sh`, at step 3. If the stack base was never fetched, the line says to run ~/.claude/scripts/branch-divergence-status.sh, which fetches it, before changing the key. Otherwise the histories are unrelated.
- **An OPEN PR for the branch targets another base:** `pr-diff-against-base.sh`, at step 3, after any step-1 sync. It parses the `gh pr view` reply with `_lib_open_pr_base`. A merged or closed PR's base is ignored, because `gh pr view` with no argument can return one.

`pr-diff-against-base.sh` never fetches, so it can read a deleted stack base's stale local ref. Step 1's fetch is what catches a deleted stack base, and step 1 runs before step 3. That ordering is a dependency. `git branch -D` removes the deleted branch's `branch.<name>` section, so a reused branch name does not inherit an old key.

A failed `status` recompute never reads `live`, so step 3 runs the script itself and halts on its failure. Every stop therefore lands before step 7's marker, on the CI-watch path too.

`/respond-pr`'s precheck is a defined arm, not a residual: its step 3 names CONFLICT, CLEAN and a failed recipe. The failed-recipe abort handles the skill's active marker as the CONFLICT arm's abort does. Neither abort names a deactivation, so step 9's `marker.sh deactivate respond-pr` is not reached. The precheck sentence no longer claims to measure against the PR's base branch, because the recipe never read it.

When the stack base merged, the stop line's fix is `git rebase --onto origin/<merged-into> <old stack-base commit>` followed by `git config --unset-all` on the key. After a squash merge, a plain `git rebase origin/<merged-into>` replays the old base's commits, because the squash commit is one combined patch, not one patch per base commit. That was reproduced on git 2.43: the plain rebase stopped on the first replayed base commit, and `--onto` replayed only the branch's own commit. The line names no fork point.

## Step 8 and the redaction hook

Step 8 passes `--base "<name>"` when step 6 printed `Base branch: <name>`. The push gate matches the create command by the words `gh pr create` alone, so the flag rides on the same completion marker. The redaction hook scans the full command text, so a tracker-named stack base is denied in an opted-in repo as a tracker-ID title would be, and the engineer decides what to do. Relying on gh's key read instead would keep the base out of the scanned text.

## Markers stay unchanged

No marker changes: [ready-for-review-cumulative-diff-cache.md](ready-for-review-cumulative-diff-cache.md) records the decision that the `cumulative-review` hash excludes the base, and `status` recomputes through the same script that reads the same key.

## Push-gate wording

The push gate's two deny strings that named the cumulative diff (`gh pr ready` and a push to a branch with an open PR) and `docs/skills.md`'s `/ready-for-review` entry say "PR-vs-base diff", the repository's term for that diff, because the gate computes no base and "default branch" was wrong for a stacked branch.

## Alternatives set aside

- **A `--base` flag on `pr-diff-against-base.sh`, or an environment variable.** Neither reaches `marker.sh status`.
- **Auto-detecting the base from open PR heads.** It needs a network scan and a heuristic the engineer would confirm anyway.
- **One resolver with a precedence chain** (open PR, key, closed PR, default). Only `pr-diff-against-base.sh` reads a PR, so "key set: use it or stop; no key: the old code" gives the same answers and leaves the no-key base resolution as it was, apart from the three changes "The one rule" lists.
- **A named trigger per cause, each with its own remedy text.** The causes differ only in the reason clause.
- **Letting an open PR's base silently override a disagreeing key.** It hides a retarget or a wrong base.
- **Folding the base ref into the `cumulative-review` hash.** It contradicts the decision cited above.
- **Treating a key equal to the default branch as unset.** Such a key measures against the same branch, so only the output label would differ.
- **A check that the stack base's own PR merged**, with an acknowledgement key. It was dropped as more machinery than the fail-closed rule needs.
- **Widening the sync skill's frontmatter trigger to name a stack base.** The trigger clause reads "syncing a feature branch with the default branch", but the entry points this change touches invoke the skill by name (`ready-for-review` step 1, the SessionStart advisory) or route to it (`respond-pr`'s CONFLICT arm, which depends on the description's first line, "Rebase vs merge a feature branch; force-push safely."), and the description loads for every stow consumer.

## Residuals

- **A stack base that merged but whose branch stays on origin.** GitHub retargets a stacked PR only when its merged base branch is deleted, and deleting head branches on merge is a per-repository setting. In a repository that keeps merged branches no stop fires, and the PR keeps targeting the merged branch.
- **`branch-divergence-status.sh` with no key set** still measures against the default branch for a PR with another base, such as a release-branch PR. See [ready-for-review-base-drift.md](ready-for-review-base-drift.md).
- **The SessionStart advisory** (`check-branch-divergence.sh`) still measures a stacked branch against the default branch. After the advisory, `git-feature-branch-sync` tells the agent to run the recipe and sync to the branch its `Default branch:` or `Base branch:` line names, not the advisory's. If the recipe exits non-zero, the skill reports its stderr and stops, and syncs nothing against the advisory's branch. The hook deliberately keeps its old fetch and measurement form, so `branch-divergence-status.sh`'s header no longer claims to match it, the hook's own header comment says it never reads the key, and under a narrowed `remote.origin.fetch` the advisory can read a stale ref.
- **Any `gh pr view` failure or unparseable reply on the key path** reads as no open PR, so that run skips the open-PR check with no notice, for an existing PR as well as a new one. A persistent failure (no gh, no auth, a wrong host) disables the open-PR stop on every run. The no-key fallback prints `gh pr view failed; defaulting base to <name>` for the same failure.
- **`respond-pr` measures against the key, not the open PR's base.** With the key set, its precheck runs `branch-divergence-status.sh`, which reads the key and never the PR. An OPEN PR retargeted to another branch while the stack base stays on origin therefore passes the precheck. `/ready-for-review` step 3 then stops on the mismatch, before the marker the push gate requires.
- **The review base is trusted from one write.** The key's value alone selects the review scope: any origin branch that shares history passes, so a wrong write narrows the diff with no mechanical check, though an empty diff halts at step 3 and `pr-diff-against-base.sh` prints a `using branch.<b>.gh-merge-base` stderr line on every key-path run that reaches the diff. Before the PR exists the OPEN-PR stop cannot fire, so only the origin fetch checks the value. Step 3 reads the key itself, while step 8 takes `--base` from step 6. For a new PR the Completion `Branch:` line shows the key's value. For an existing PR it names the PR's base, so no human-visible surface shows the review base, and a stale key, an existing PR and a failed `gh pr view` review the stale base with no stop. Unsetting the key falls back to the wider review. The `cumulative-review` hash excludes the base, by the decision cited above.
- **A stack base whose history is unrelated to HEAD**, at the divergence script. `git merge-tree` fails, the capture ends `|| true`, and the script prints `Trial merge: CLEAN`, so `/respond-pr`'s precheck proceeds on the false CLEAN. Step 1's sync is triggered by the base's whole commit count and defaults to a rebase onto the unrelated tip, which rewrites the branch and offers a force-push. If that rebase completes, a merge-base then exists, so step 3's stop never fires and the cost is a rewritten branch and a whole-history diff, not an unreviewed pass. Otherwise step 3's merge-base stop fires before the marker. The engineer sees `Behind: <the base's whole commit count>` with `Trial merge: CLEAN`, and the offered sync's default rebase makes the stop moot. Before setting the key, `git merge-base origin/<stack-base> HEAD` printing nothing means the histories are unrelated, so do not set the key. The no-key form is recorded in [ready-for-review-base-drift.md](ready-for-review-base-drift.md).
- **No rebase recipe** for a squash-merged stack base, or for a stack base rewritten by a force-push, whose changed commits a plain `git rebase origin/<merged-into>` replays. `git-feature-branch-sync` carries none, and the engineer finds the old base commit. A force-pushed stack base trips no stop, so step 1 sees behind > 0 and its sync does the plain rebase that replays the old base commits. Only afterwards does the engineer find the old base commit and redo the rebase with `--onto`.
