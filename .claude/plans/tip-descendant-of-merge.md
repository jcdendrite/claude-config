# cleanup-merged-branches: classify a tip that descends from a merged PR's head

## Context

`cleanup-merged-branches.sh`'s `classify_branch()` detects a merged branch two ways — exact tip match against a merged PR's `headRefOid`, and tip being a strict ancestor of `headRefOid` (local is behind what was merged). It has no check for the reverse case: local tip is a *descendant* of `headRefOid` — the branch kept receiving commits (review-round fixups, resyncs) after the PR was squash-merged from an earlier snapshot. That case currently falls through to `skip-stale-name`, whose message ("likely a reused branch name") is actively misleading here — the branch's content really was merged, it just has extra unmerged commits sitting on top. The fix adds a third detection basis — `git merge-base --is-ancestor <headRefOid> <tip>` — with its own classification label, so this class of branch is surfaced accurately (a y/N prompt, or a dry-run listing) instead of being silently misclassified as a stale/reused name.

## Approach

`classify_branch()` gets a third ancestry basis, `pr-head-descendant`. It runs after the existing forward (`pr-head-ancestor`) scan and its fetch-and-rescan have both missed. It rescans the same merged rows for one whose `headRefOid` is a strict ancestor of the local tip. On a hit it emits a Tier B verdict carrying the PR number, the merge date, and a count of the tip's commits that are in neither that head nor `origin/<default>`. Tier B renders it with its existing machinery: the TTY `[y/N]` prompt, the non-TTY skip, and the dry-run "Probable merges (would prompt):" section. Only the per-branch text is new, for example `PR #801, merged 2026-06-01; local tip is 1 commit(s) ahead of that PR's merged head and origin/main`.

**Verdict grammar: add a mandatory leading basis field to the existing `tier-b:` token instead of adding a new token.**
- `tier-b:[<stale-pr>]` becomes `tier-b:reachable:[<stale-pr>]`.
- The new case is `tier-b:pr-head-descendant:<pr>:<merged-date>:<ahead-count>`.

A new `tier-b-descendant:` token is rejected. The forward-direction plan already rejected that shape for Tier A (`.claude/plans/classify-branch-ancestor-merged-head.md`, M2):
- The glob `tier-b:*` does not match `tier-b-descendant:…`.
- Neither verdict `case` statement has a `*)` default arm.
- So a forgotten arm drops the branch from the sweep silently.

Keeping the tier letter in the token means `checked_out_skip_line()` needs no edit. The detection loop's `tier-b:*` arm records `MERGED_BRANCHES` and `TIER_VALUES` "B" for every basis and uses the basis only to pick the info text. A branch with an unrecognized basis therefore still lands in the prompt tier. The basis comes first here, while Tier A's comes last, because Tier B's two bases carry different fields. A leading discriminator lets the parse branch on the basis without guessing from how many colons there are. Both existing `reachable` info strings stay byte-identical. Tests pin the no-PR string in full and the stale-PR string by its prefix (ledger row 11).

**Helper: generalize it instead of copying it.** `merged_row_containing_tip TIP ROWS` becomes `merged_row_by_tip_ancestry BASIS TIP ROWS`. BASIS is `pr-head-ancestor` or `pr-head-descendant`, and it only decides the operand order of `git merge-base --is-ancestor`. Two alternatives were set aside:
- **A sibling function.** It would duplicate the row-parse loop. CLAUDE.md's sibling-audit rule says to abstract once two arms share a shape.
- **Extracting only the triple parse.** That still leaves two copies of the loop.

The BASIS values are the verdict's own basis names, so the helper, the verdicts, and the docs all use one term for each concept. The helper's precondition, that no row's oid equals the tip, holds for both bases. The embedded classifier returns `matched:` on any equality before the stale path runs.

**Placement: after the forward re-scan and before the `skip-stale-name` fallback, inside the existing `[ -n "$tip" ] && [ -n "${stale_rows:-}" ]` block.** This has three consequences:
- **Reachability still wins.** Reachability from `origin/<default>` still outranks both ancestry bases, as it does today.
- **The forward basis wins over the descendant basis when rows disagree.** A tip contained in some PR's merged head is fully merged, even if it also sits on top of an older row's head. Two alternative orderings would let an earlier row's Tier B hit hide a later row's Tier A proof: running the descendant scan before the fetch loop, or testing both directions row by row.
- **No new network call.** A descendant hit's `headRefOid` is in the tip's own history, so its object is already local. The new code runs only `merge-base` and `rev-list`. The per-row fetch loop runs on exactly the same inputs as today.

The reverse direction therefore has no "PR ref aged out" failure mode of its own. A `headRefOid` whose object is not local cannot be an ancestor of the tip, so the scan misses and falls through to today's verdict. The existing unfetchable-ref tests now run through the new scan with missing objects, so they keep guarding it with no new fetch-path tests.

**Count: `git rev-list --count "$tip" "^${oid}" "^refs/remotes/origin/${DEFAULT_BRANCH}"`, with `?` as the fallback.** `<oid>..<tip>` is rejected: it also counts default-branch commits that a merge resync pulled onto the branch. The Context names resyncs as one way this shape arises, so one follow-up commit plus a resync could read as dozens of commits. Excluding `origin/<default>` counts only the commits a `y` would remove from every ref this script can see.
- **Always at least 1.** Reachability runs first and returns early, so a descendant hit means the tip is not on `origin/<default>`.
- **`?` fallback.** It keeps the verdict well-formed if `rev-list` fails, for example when `refs/remotes/origin/<default>` is missing. That matches `classify_branch`'s contract of always returning 0.
- **Computed in `classify_branch`.** That is the only place that holds the oid. The count travels in the verdict, so the detection loop makes no git call.

**Accepted consequence.** Until now every Tier B branch was reachable from `origin/<default>`, so answering `y` discarded no commit. A `pr-head-descendant` branch carries at least one commit that is nowhere on `origin/<default>`. Deletion is `git branch -D` plus a remote-branch delete. The safeguard is the prompt text naming that count. The default answer stays N, and a non-TTY run still skips.

Over-powered-primitive check: nothing here is heavier than what the script already uses. It reuses the script's own `merge-base --is-ancestor` and `rev-list --count` calls and adds no network call, flag, tier, or output section.

### Assumption ledger

**Root:** a local branch whose tip strictly descends from its merged PR's `headRefOid` has its merged content in and extra commits on top. The script reports it as `skip-stale-name` ("likely a reused branch name"). That diagnosis is wrong, and it hides the extra commits behind a claim that they belong to a different branch.

**Givens**
- **G1: `gh pr list`'s `headRefOid` is the PR head at merge time, and GitHub owns it.** Nothing the local branch does later updates it, so the design has to work from that snapshot.
- **G2: the script's stdin is not a TTY on its documented invocation path.** The harness owns stdin for a Bash tool call. On that path a Tier B verdict becomes a "no TTY for prompt" skip, and `--dry-run` is the surface that shows the per-branch text.

**Rows**
1. The descendant case lands in Tier B: `TIER_VALUES` "B", with the TTY prompt, the non-TTY skip, and the dry-run section reused. `[engineer-verified: "Fold into Tier B (Recommended)"]`
2. Output surfaces are reused verbatim, with no new verdict token (M2). `[unverified]`
3. The prompt and the dry-run text state a commit count. `[engineer-verified: "Show the count (Recommended)"]`
4. The count excludes `origin/<default>` (M4). `[unverified]`
5. On the `stale:` path no merged row's oid equals the tip, so a successful `--is-ancestor <oid> <tip>` means a strict descendant. `[verified: cleanup-merged-branches.sh:450-452 emits matched: on any equality; case arms :486-502]`
6. A `headRefOid` that is an ancestor of the local tip is in the local object store, and one whose object is not local cannot pass the test. `[unverified]`: this is git's object model and was not run while authoring this plan. If it is wrong (for example in a shallow clone), the scan misses and falls through to today's verdict.
7. Reachability runs before every stale-row scan and returns early. A descendant hit therefore implies the tip is not on `origin/<default>`, and the count is at least 1. `[verified: cleanup-merged-branches.sh:504-511]`
8. PR numbers are digits only, and dates are digits and dashes or blank. The colon-delimited verdict fields therefore split unambiguously. `[verified: cleanup-merged-branches.sh:463-477]`
9. Two `case` statements consume verdicts, and neither has a `*)` arm. `tier-b:*` does not match a `tier-b-descendant:` prefix. `[verified: cleanup-merged-branches.sh:604-613, :652-691]`
10. The dry-run split and the confirmation pass look only at the `TIER_VALUES` letter. `[verified: cleanup-merged-branches.sh:705-711, :774-797]`
11. Tests pin the no-PR `reachable` info string in full (`:1288`) and only the prefix `a merged PR #33 shares this name` of the stale-PR one (`:1980`). `[verified: test_cleanup_merged_branches.py:1288, :1980]`
12. Only `TestDescendantOfMergedHeadStaysStale` supplies a `headRefOid` that is a strict ancestor of the tip. Every other stale-path test uses an unrelated oid, a placeholder oid, or a strict-descendant oid that is not yet local. No other existing test changes outcome. `[verified: grep of "headRefOid": <variable> in test_cleanup_merged_branches.py; fixtures :1008-1068]`
13. A `y` answer runs `git branch -D` and deletes the remote branch when one exists. `[verified: cleanup-merged-branches.sh:902, :911-913]`
14. `rev-list` with several `^` exclusions counts the commits reachable from the tip and from none of the excluded refs. `[unverified]`: not run while authoring this plan. `TestDescendantAheadCountExcludesDefaultBranchCommits` proves it.
15. Some repos merge PRs with merge commits. In those repos, a genuinely reused name branched from `origin/<default>` after the old PR merged also has that PR's head in its history. It also classifies as `pr-head-descendant`, so it gets a Tier B prompt instead of today's `skip-stale-name`. Ancestry alone cannot tell it apart from a branch that was continued and merge-resynced. `[unverified]`: inferred from the ancestry relation, not reproduced. This is accepted because the verdict never auto-deletes and M4's count shows only the branch's own commits.

**Mechanisms**
- **M1: classify a strict-descendant tip as Tier B with basis `pr-head-descendant`.** `anchors: root, row1, row5, row12, row15`
- **M2: extend `tier-b:` with a mandatory leading basis field (`reachable` | `pr-head-descendant`) instead of adding a new token.** The loop's `tier-b:*` arm records Tier B for every basis and uses the basis only to pick the text. `anchors: row2, row8, row9, row10, row11`
- **M3: generalize the scan helper into `merged_row_by_tip_ancestry BASIS TIP ROWS` instead of adding a sibling copy.** `anchors: row5`. Both bases share the row loop and the no-equality precondition.
- **M4: compute the count with `git rev-list --count "$tip" "^$oid" "^refs/remotes/origin/$DEFAULT_BRANCH"` inside `classify_branch`, with `?` as the fallback, and carry it in the verdict.** `anchors: row3, row4, row7, row13, row14`
- **M5: run the descendant scan after the forward re-scan and before the `skip-stale-name` fallback.** `anchors: row6, row7`. The forward basis outranks it, reachability outranks both, and no fetch is added.

## Critical files

- **`claude/.claude/scripts/cleanup-merged-branches.sh`**
  - **Helper `:331-360`:**
    - Rename it to `merged_row_by_tip_ancestry BASIS TIP ROWS`.
    - Work out the operand order from BASIS once, before the row loop. An unrecognized BASIS returns 1 (no hit).
    - Keep `[ -n "$oid" ] || continue` and the `2>/dev/null` on the ancestry call.
    - Rewrite its comment to cover both bases, keeping the precondition sentence.
    - Pass `pr-head-ancestor` at the two existing call sites, `:514` and `:540`.
  - **`classify_branch`:**
    - `:396`: add `descendant_row ahead_count` to the `local` line.
    - `:509`: change to `printf 'tier-b:reachable:%s\n' "${stale_pr:-}"`.
    - New block after the re-scan's closing `fi` (`:546`), still inside the `:513` block. On a `pr-head-descendant` hit, split the triple into pr, oid, and date. Then run `ahead_count=$(git rev-list --count "$tip" "^${oid}" "^refs/remotes/origin/${DEFAULT_BRANCH}" 2>/dev/null) || ahead_count='?'` and `printf 'tier-b:pr-head-descendant:%s:%s:%s\n' "$pr_number" "$merged_date" "$ahead_count"`, then return 0.
  - **Detection loop, `tier-b:*` arm `:667-676`:**
    - Split off `_basis` first.
    - For `pr-head-descendant`, split out `_pr_number`, `_merged_date`, and `_ahead_count`. Record `PR #${_pr_number}, merged ${_merged_date}; local tip is ${_ahead_count} commit(s) ahead of that PR's merged head and origin/${DEFAULT_BRANCH}`.
    - For any other basis, take `_stale_pr` from the rest of the verdict and keep both existing strings byte-for-byte.
    - Keep `MERGED_BRANCHES+=` and `TIER_VALUES+=("B")` outside the basis branch.
  - **Comments.** Each describes current behavior:
    - Header `:4-13`: Tier B gains the `pr-head-descendant` basis.
    - Header `:15-24`: a merged-by-name match whose tip strictly descends from that merge now prompts instead of being skipped.
    - `classify_branch` docstring `:368-372`: the new helper name.
    - Verdict list `:374-388`: both `tier-b:` shapes, with `<ahead-count>` defined.
    - Closing paragraph `:390-394`.
  - **No edit:** `checked_out_skip_line` `:600-614`, the dry-run block `:702-765`, or the confirmation pass `:774-797`.
  - **Reuse:** the existing `merge-base --is-ancestor … 2>/dev/null` idiom and the `rev-list --count` shape at `:937`. Add no bash-4 constructs (`test_no_bash4_constructs.py`).
- **`claude/.claude/scripts/tests/test_cleanup_merged_branches.py`**
  - **No fixture changes.** Reuse:
    - `_make_descendant_of_merged_head_branch` (`:1045-1068`) and `_make_ancestor_merged_branch` (`:1008-1042`).
    - `_run_script` (`:278`), which runs non-TTY.
    - The pty `Popen` pattern from `test_reachable_no_pr_tty_n_survives` (`:1107-1137`).
    - `_commit` and `_rev_parse`.
  - **Replace `TestDescendantOfMergedHeadStaysStale` (`:2104-2126`) with `TestDescendantOfMergedHeadPromptsAsTierB`.** Its docstring states the new contract: the branch is offered for a prompt, never auto-deleted, and never called a reused name. Methods:
    1. **Non-TTY run.** Stdout contains `Skipped 1 probable-merge branch(es) (no TTY for prompt): feat/ahead-of-merge` and does not contain `likely a reused branch name`. `stderr == ""`. The ref survives.
    2. **`--dry-run`.** `Probable merges (would prompt):` and `PR #801, merged 2026-06-01; local tip is 1 commit(s) ahead of that PR's merged head and origin/main` are present. `Would clean up (confirmed merged):` and `likely a reused branch name` are absent.
    3. **pty, reply `n\n`.** Stdout contains `[y/N]` and `1 commit(s) ahead of that PR's merged head`. The ref survives.
    4. **Branch checked out.** Also create an ordinary Tier A branch so the run gets past the early exit, as `TestCheckedOutTierBBranchReportsSkip` (`:1874-1902`) does. `Skipped: feat/ahead-of-merge (currently checked out)` is present, and the ref survives.
    5. **pty, reply `y\n`.** The one basis where a `y` now discards commits that exist nowhere else (per the Approach's "Accepted consequence"), so it gets its own direct assertion rather than relying on the generic delete-path tests. Assert both the local ref and the remote branch are gone after the reply, mirroring `TestTierBReachableNoMergedPR`'s existing TTY-`y` coverage for plain Tier B.
  - **New `TestMalformedDescendantVerdictFieldsFailClosed`.** Mirrors `TestClassifierValidatesMergedRowFields` (`:2302-2436`) for the new `tier-b:pr-head-descendant:<pr>:<merged-date>:<ahead-count>` token: a malformed `mergedAt` on the winning descendant row degrades to a blank date field exactly like the existing `pr-head-ancestor` token does, rather than corrupting the parse or crashing. Also covers the `ahead_count='?'` fallback (M4): force `git rev-list --count` to fail for this one call (e.g. by removing `refs/remotes/origin/main` between fixture setup and the run, or another means that fails only the count call and not the ancestry check that must still succeed) and assert the dry-run/prompt text shows `? commit(s) ahead` rather than a raw error or an empty field.
  - **New `TestReusedNameInMergeCommitRepoGetsPromptNotStaleSkip`.** Covers ledger row 15's accepted false positive directly, rather than leaving it asserted-but-unverified: build a repo whose PRs merge via merge commit (not squash), then a genuinely unrelated branch reusing an old, already-merged PR's name, branched from `origin/main` *after* that old PR's merge commit landed — so the old `headRefOid` is in the new branch's history purely through the merge-commit-repo topology, not through any real continuation of that PR's work. Assert it draws the `pr-head-descendant` Tier B prompt (not `skip-stale-name`), that the ahead-count reported is the new branch's own commit(s) only, and that it is never auto-deleted — confirming the "non-destructive prompt with a factual count" safety net the Approach claims for this case actually holds for it.
  - **New `TestDescendantAheadCountExcludesDefaultBranchCommits`.**
    - Setup: build the descendant fixture. Then commit a distinct file (not `file.txt`) on `main` with plain git and push it. `_commit` overwrites `file.txt`, which would make the resync merge conflict. Then run `git merge -q --no-edit main` into the branch.
    - Assertion: `--dry-run` shows `2 commit(s) ahead` (the follow-up commit plus the merge commit) and not `3 commit(s)`.
    - This test pins the `^origin/<default>` exclusion through a resync merge. `TestReusedNameInMergeCommitRepoGetsPromptNotStaleSkip` pins the same term through merge-commit topology.
  - **New `TestAncestorBasisOutranksDescendantBasis`.**
    - Setup: run `_make_ancestor_merged_branch(…, 902)`. Then give gh two explicit `"state": "MERGED"` rows in this order: `[{901, headRefOid: main's tip}, {902, headRefOid: merged_head}]`. Row 901's head is a strict ancestor of the tip. Row 902's head exists only on the remote.
    - Assertion: a non-TTY run deletes the branch as Tier A.
    - It would survive if the descendant scan ran before the fetch loop or row by row.
  - **`TestGenuineReuseNotAncestorStaysStale` (`:2073-2101`): docstring only.** Its sibling-commit fixture is already "neither an ancestor nor a descendant", so it is the negative case for both bases. The docstring should say so.
  - **Unchanged tests that now also run through the descendant scan and keep guarding it:**
    - `TestAncestorCheckFallsBackWhenRefUnfetchable`.
    - `test_both_rows_unresolvable_falls_through_cleanly`, which checks empty stderr with missing objects.
    - `test_malformed_head_ref_oid_falls_through_without_reaching_git`, whose GIT_TRACE check also covers the new `merge-base` arguments.
    - `TestFetchLoopCapInterruptsHungRemote`.
- **`docs/scripts.md`**, the canonical description:
  - `:108`: qualify "A same-named merged PR whose tip doesn't match … is skipped" with the `pr-head-descendant` exception, which goes to Tier B.
  - `:111`: the Tier B bullet names both bases. It says the descendant prompt and dry-run line show the PR, the merge date, and the count of commits in neither that head nor `origin/<default>`. Those are the commits a `y` discards.
  - `:110` (Tier A) is unchanged.
- **`CHANGELOG.md`**: add a `### Changed` entry under `## [Unreleased]`, in the style of `:150` but much shorter. It covers:
  - the old misreport;
  - the new Tier B prompt with its count;
  - the branch is never auto-deleted;
  - a `y` force-deletes the local and remote branch, including those commits;
  - no new network calls;
  - a checked-out descendant branch now gets the "currently checked out" line;
  - the change is live for every stow consumer on `git pull`.

  End the entry with `See GH-1110.`

**Dispatch split:** one `code-writer` dispatch covering all four files. The script, the test assertions, and both doc surfaces share the basis names and the exact info string, and a split would restate them in every prompt.

## Verification

```bash
.venv/bin/pytest claude/.claude/scripts/tests/test_cleanup_merged_branches.py   # inner loop
.venv/bin/python3 claude/.claude/scripts/select-tests.py                        # scoped suite for this diff
.venv/bin/ruff check claude/.claude/scripts/tests/test_cleanup_merged_branches.py
```

`select-tests.py` maps `claude/.claude/scripts/**` to the scripts test directory. It also maps a `.sh` file there to `test_shellcheck.py` (`select-tests.py:381`, `:428-430`). ShellCheck for the script edit is therefore covered without hand-widening the run.

The fixtures cannot replace one operator check, because their remote is a local bare repo:
- Run `cleanup-merged-branches --dry-run` in a repo holding a branch that got commits after its PR was squash-merged.
- Confirm the branch now appears under `Probable merges (would prompt):` with a plausible count, not in the reused-name skip line.
- Quote only placeholder repo and branch names in the PR body.

**Review surface:** one script (the helper generalization, one new block, one case arm, and comments), one test file (one rewritten class and four new classes), and two doc surfaces. Risk concentrates in the `tier-b:*` parse, and tests pin its `reachable` strings.

## Out of scope

- **Renaming Tier B's dry-run heading or its non-TTY skip line.** "Probable merges" and "probable-merge" fit a partly merged branch only loosely. The per-branch text carries the difference, and a test pins the heading (`:1285`).
- **Telling a reused name apart from a continued branch when the old PR head is on `origin/<default>`.** This happens in merge-commit repos (ledger row 15). Ancestry cannot separate the two, and both now get a non-destructive prompt with a factual count — `TestReusedNameInMergeCommitRepoGetsPromptNotStaleSkip` verifies that safety net directly rather than leaving it asserted.
- **Reproducing a genuine shallow clone to test ledger row 6's object-model claim directly.** The existing unfetchable-ref tests (`TestAncestorCheckFallsBackWhenRefUnfetchable` and the descendant-scan tests that now share its code path) exercise the same missing-object fallback a shallow clone would hit; a real shallow clone is accepted as equivalent by inference, not reproduced.
- **Printing the per-branch text in the non-TTY skip line.** This gap already exists for all Tier B branches, and `--dry-run` is the surface that shows the detail.
- **Branches resynced by rebase.** The rewritten history no longer contains the old head, so these keep `skip-stale-name`.
- **Merging the two identical Tier A hit blocks at `:514-520` and `:540-546`.** The duplication already exists and is unrelated to this change.
- **A recovery breadcrumb after a `y`, such as printing the deleted tip SHA.** The gap already exists for every deletion path. This change makes it matter more, because a `pr-head-descendant` `y` discards unique commits. It is worth its own issue.
