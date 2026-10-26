# `/ready-for-review` for a branch stacked on another open PR's branch

*2026-10-23.*

`/ready-for-review` opens its PR last (step 8), and the push gate requires the gate's completion marker before `gh pr create` runs. With no stack base named, a stacked branch's cumulative diff, divergence check and fidelity-review diff file all include the base PR's already-reviewed work. The engineer names the stack base once, in gh's own per-branch key, and every base-dependent step uses it or stops.

## Why `branch.<branch>.gh-merge-base`

- `marker.sh status` recomputes the `cumulative-review` hash by running `pr-diff-against-base.sh` with no arguments, so the base has to be repository state. A flag on the script or an environment variable would not reach that call, and an environment variable does not survive across Bash tool calls.
- gh's PR-create command reads this key when `--base` is absent. A custom key such as `branch.<name>.rfr-base` would work for the gate, but a hand-run PR create would still target the default branch.
- Pointing the branch's upstream (`branch.<name>.merge`) at the stack base would break steps 6 and 8, which rely on the branch's own `origin/<branch>` tracking.
- gh's checkout and create code never write the key. In the `cli/cli` trunk source, `pkg/cmd/pr/checkout/checkout.go` writes only `branch.<branch>.remote`, `pushRemote` and `merge`, and `pkg/cmd/pr/create/create.go` only reads the key. That covers those two files only.
- Step 1 sets the key only from the engineer's own words, never by inference.

## The one rule

- With the key set, a step uses its value or stops.
- With no key set, `pr-diff-against-base.sh` resolves its base from the base of the PR `gh pr view` returns for this branch (merged or closed included), else the default branch, and `branch-divergence-status.sh` uses the default branch. Both scripts also:
  - a failed git read of HEAD or the key stops the step;
  - measurement goes through the fully qualified `refs/remotes/origin/<base>` ref.
- With no key set, of the two scripts, only `branch-divergence-status.sh` fetches the default branch, using a named destination plus a verify, and a verify failure prints the "failed or timed out" line.
- A stop exits non-zero with one stderr line.
  - An unusable value gets its line from `_lib_print_unusable_stack_base` (`claude/.claude/hooks/_lib.sh`), the one home of the fix text. The line names the script, the key, its value and the reason. It says the engineer runs the fix, not the agent.
  - A git read failure prints its own line instead, naming the failed git call and its status. The line says to retry the command and, if it fails again, to run that git call by hand to see its error. A key edit would not fix a git read failure, because the failure is git's, not the value's.
    - All three git calls the helper makes (`symbolic-ref`, `config` and `check-ref-format`) go through `_lib_stack_base_git`, the one home of the read-failure line.
    - The HEAD and key reads run before any value exists, so their stop can fire on a branch with no key set.
    - `check-ref-format` runs only after a value was read.
- Step 5 passes the `Base branch:` name to `pr-description`, which asks the engineer instead of running its comparison when the name holds a character outside the set that "Where each stop fires" lists for a usable name, or starts with `-` (`pr-description/SKILL.md`).

Where each stop fires:

- **The value cannot be read or is not a usable branch name:** `_lib_configured_stack_base`, called by both scripts.
  - Unusable means empty, a leading `-` or `origin/`, rejected by `git check-ref-format "refs/heads/<value>"`, holding a character outside `[A-Za-z0-9._/-]`, `HEAD`, or this branch's own name.
  - `git check-ref-format` accepts a leading `-`, shell metacharacters, quotes, non-ASCII text, bidi controls and `HEAD`, and rejects `..`, `@{` and spaces, so those tests are explicit.
- **The stack base cannot be fetched from origin:** `branch-divergence-status.sh`, at steps 1 and 6.
  - A timeout and a branch deleted on origin fail the same way.
  - The reason therefore says to retry the command before changing the key.
  - It then names `git ls-remote origin refs/heads/<base>`, which tells a base missing from origin apart from any other fetch failure, and gives the next step for each.
  - The fetch uses `+refs/heads/<base>:refs/remotes/origin/<base>` and then verifies the ref, so a narrowed `remote.origin.fetch` cannot leave it missing or stale.
  - `/respond-pr`'s precheck runs the same recipe and handles a failed run as `respond-pr/SKILL.md` step 3 states.
- **No merge-base with `origin/<value>`:** `pr-diff-against-base.sh`, at step 3.
  - If the stack base was never fetched, the line says to run ~/.claude/scripts/branch-divergence-status.sh, which fetches it, before changing the key.
  - Otherwise the histories are unrelated.
- **An OPEN PR for the branch targets another base:** `pr-diff-against-base.sh`, at step 3, after any step-1 sync.
  - It parses the `gh pr view` reply with `_lib_open_pr_base`.
  - A merged or closed PR's base is ignored, because `gh pr view` with no argument can return one.

`pr-diff-against-base.sh` never fetches, so it can read a deleted stack base's stale local ref. Step 1's fetch is what catches a deleted stack base, and step 1 runs before step 3. That ordering is a dependency. `git branch -D` removes the deleted branch's `branch.<name>` section, so a reused branch name does not inherit a stale key.

A failed `status` recompute never reads `live`, so step 3 runs the script itself and halts on its failure. Every stop therefore lands before step 7's marker, on the CI-watch path too.

`/respond-pr`'s precheck is a defined arm, not a residual: `respond-pr/SKILL.md` step 3 names CONFLICT, CLEAN and a failed recipe. The failed-recipe abort handles the skill's active marker as the CONFLICT arm's abort does. Neither abort names a deactivation, so step 9's `marker.sh deactivate respond-pr` is not reached. The skill's bypass marker therefore stays armed until eviction (`docs/hooks.md` § "Gate deadlock recovery"). While it is armed, `require-respond-pr.sh` releases every surface it gates, not only comment commands. Each Bash call in the session refreshes the marker, so it stays armed as long as the session keeps issuing Bash calls. Eviction happens when the session's process dies or after 60 idle minutes. The marker is not scoped to a repo, because its path carries no repo hash. `marker.sh deactivate respond-pr` ends it. That gate is a cooperative mistake-catcher, not a security boundary, and re-invoking `/respond-pr` re-activates the marker.

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
- **One resolver with a precedence chain** (open PR, key, closed PR, default). Only `pr-diff-against-base.sh` reads a PR, so "key set: use it or stop; no key: the default-branch path" gives the same answers. The no-key base resolution stays as "The one rule" lists it.
- **A named trigger per cause, each with its own remedy text.** The causes differ only in the reason clause.
- **Letting an open PR's base silently override a disagreeing key.** It hides a retarget or a wrong base.
- **Folding the base ref into the `cumulative-review` hash.** It contradicts the decision cited above.
- **Treating a key equal to the default branch as unset.** Such a key measures against the same branch, so only the output label would differ.
- **A check that the stack base's own PR merged**, with an acknowledgement key. It is more machinery than the fail-closed rule needs.
- **Widening the sync skill's frontmatter trigger to name a stack base.** The trigger clause reads "syncing a feature branch with the default branch". Of the entry points this change touches, `ready-for-review` step 1 and the SessionStart advisory invoke the skill by name. `respond-pr`'s CONFLICT arm routes to it instead, and that route depends on the description's first line, "Rebase vs merge a feature branch; force-push safely." The description loads for every stow consumer.

## Residuals

- **A stack base that merged but whose branch stays on origin.** GitHub retargets a stacked PR only when its merged base branch is deleted, and deleting head branches on merge is a per-repository setting. In a repository that keeps merged branches no stop fires, and the PR keeps targeting the merged branch.
- **`branch-divergence-status.sh` with no key set** measures against the default branch for a PR with another base, such as a release-branch PR. See [ready-for-review-base-drift.md](ready-for-review-base-drift.md).
- **The SessionStart advisory** (`check-branch-divergence.sh`) measures a stacked branch against the default branch.
  - The hook fetches and measures through `origin/<default branch>`, and its header says it never reads the key.
  - Under a narrowed `remote.origin.fetch` the advisory can read a stale ref.
  - After the advisory, the agent follows `git-feature-branch-sync/SKILL.md` § "Detecting divergence": run the recipe, sync to the branch it names, and on a non-zero exit report its stderr and sync nothing against the advisory's branch.
- **Any `gh pr view` failure or unparseable reply on the key path** reads as no open PR, so that run skips the open-PR check, for an existing PR as well as a new one.
  - A persistent failure (no gh, no auth, a wrong host) disables the open-PR stop on every run.
  - When `gh pr view` exits non-zero, `pr-diff-against-base.sh`'s `using` line carries a clause saying no open-PR base was checked. An unparseable reply from a successful `gh pr view` adds no clause.
  - `marker.sh status` discards the script's stderr, so a recompute through `status` gives no notice.
  - The no-key fallback prints `gh pr view failed; defaulting base to <name>` for the same failure.
- **A stale key after the stack base merges** hard-stops `/respond-pr`'s precheck.
  - Nothing unsets the key on merge.
  - Once the base branch is gone from origin, the divergence script exits 1 on its fetch stop. The precheck re-runs it once and aborts. The skill's instruction, not a gate, keeps it from posting a review reply until the engineer applies the stop line's fix.
  - This is the accepted post-merge state for `/respond-pr` as well as for the gate. "No rebase recipe" below covers the missing fork-point.
- **`respond-pr` measures against the key, not the open PR's base.** With the key set, its precheck runs `branch-divergence-status.sh`, which reads the key and never the PR. An OPEN PR retargeted to another branch while the stack base stays on origin therefore passes the precheck. `/ready-for-review` step 3 then stops on the mismatch, before the marker the push gate requires.
- **An already-open stacked PR** has no key. The engineer moves it onto the key with `git config branch.<branch>.gh-merge-base <base>`, and step 3's open-PR check then passes because the key equals the PR's base. Until then, step 1's in-sync bullet stops when behind > 0 and the PR's base differs from the branch the recipe names, so no sync runs against the default branch.
- **The review base is trusted from one write.**
  - Condition: the key's value alone selects the review scope, and any origin branch that shares history passes.
  - Consequence: a wrong value narrows the diff with no mechanical check beyond the origin fetch, and an empty diff halts at step 3. The missing check applies whenever step 3 finds no OPEN PR: before the PR exists, when only a merged or closed PR exists, and on the `gh pr view` failures or unparseable replies the residual above lists.
  - An OPEN PR with a working gh and a key differing from its base stops at step 3 (`pr-diff-against-base.sh`), as "The one rule" states.
  - Unsetting the key falls back to the wider review.
  - Pre-check: after step 1's fetch, `git log --oneline origin/<stack-base>..HEAD` lists the commits the review covers.
- **A stack base whose history is unrelated to HEAD**, at the divergence script.
  - Condition: the base shares no history with HEAD, so `git merge-tree` cannot compute a verdict.
  - Behavior: the script prints `Behind:` and exits 1 with one stderr line, "trial merge of origin/<base> into HEAD could not complete". `TestBehindWithUnrelatedHistories` in `test_branch_divergence_status.py` exercises the default-branch form, an orphan branch with no key set. The key path reaches the same `_lib_trial_merge_verdict` call.
  - Consequence: `/respond-pr`'s precheck re-runs the recipe once and then stops, and step 1's sync stops before any rebase, so the branch is not rewritten.
  - Signal: `Behind:` equals the base's whole commit count and no `Trial merge:` line follows.
  - The no-key form is recorded in [ready-for-review-base-drift.md](ready-for-review-base-drift.md).
- **No rebase recipe** for a squash-merged stack base, or for a stack base rewritten by a force-push, whose changed commits a plain `git rebase origin/<merged-into>` replays. `git-feature-branch-sync` carries none, and the engineer finds the stack base's last commit before the merge. A force-pushed stack base trips no stop, so step 1 sees behind > 0 and its sync does the plain rebase that replays the rewritten base commits. Only afterwards does the engineer find that commit and redo the rebase with `--onto`.
