# Let /ready-for-review run for a stacked PR

## Context

Goal: a branch stacked on another open PR's branch can complete `/ready-for-review` against its stack base instead of the default branch.

Ask: cited to the engineer-attached brief "Brief: let /ready-for-review run for a stacked PR (base is not the default branch)", on which the engineer invoked `/plan-it`.

Problem: `pr-diff-against-base.sh` resolves the base from `gh pr view --json baseRefName` and falls back to the default branch when no PR exists. `/ready-for-review` opens the PR last (step 8), so for a stacked branch the cumulative diff is taken against the default branch and includes the whole base PR's work, which has already had its own cumulative review. The push gate requires the ready-for-review marker, so the PR cannot be opened first to fix the base. `marker.sh status` re-runs `pr-diff-against-base.sh` with no arguments to recompute the `cumulative-review` hash, so any base override must be visible to that no-arg call as well. Step 8's `gh pr create` passes no `--base`.

Intended outcome: the engineer states a stack base once, every base-dependent step (divergence check, cumulative diff, fidelity-review diff file, behind-base recheck, `gh pr create`) uses it, and a stale base fails closed.

## Approach

The engineer names a stack base once, in gh's own per-branch key `branch.<branch>.gh-merge-base`. With the key set, every base-dependent step uses its value or stops with one line telling the engineer how to fix the key. With no key set, every step runs today's code unchanged.

**Design.**

1. **Source of truth.** `git config branch.<branch>.gh-merge-base <stack-base>`, called "the key" below.
   - `gh pr create` already reads it when `--base` is absent (row 15), and gh's checkout and create code never write it (row 16).
   - `ready-for-review` step 1 sets it only from the engineer's own words, never by inference.
2. **One rule: with the key set, use its value or stop.** The gate stops when the value:
   - cannot be read, meaning any `git symbolic-ref` or `git config` status other than 0 and 1, a cap kill included (row 26);
   - is not a valid branch name other than this branch: empty, starting with `-`, rejected by `git check-ref-format "refs/heads/<value>"`, or this branch's own name (row 18);
   - cannot be fetched from origin, checked by `branch-divergence-status.sh` at steps 1 and 6. A timeout and a deleted branch fail alike there (row 11), so the reason reads "fetch failed (network, or the stack base is gone from origin)" and tells the engineer to retry the step before changing the key;
   - differs from the base of this branch's OPEN PR, checked by `pr-diff-against-base.sh` at step 3. A merged or closed PR's base is ignored (row 30).

   Every stop exits non-zero with one stderr line from one formatter. The line names the script, the key, its value and the reason, gives the fix, and says the engineer runs it, not the agent:
   - `git config --replace-all branch.<branch>.gh-merge-base <stack-base>` corrects the value.
   - If the stack base merged, the engineer rebases this branch's own commits onto the branch it merged into, `git rebase --onto origin/<merged-into> <old stack-base commit>`, then runs `git config --unset-all branch.<branch>.gh-merge-base`. The line names no fork point (Out of scope).
   - The open-PR reason also offers `gh pr edit --base <value>`.

   Steps 1 and 6 halt when the divergence script fails. A failed `status` recompute never reads `live`, so step 3 runs `pr-diff-against-base.sh` itself and halts on its failure (row 34). Every stop therefore lands before step 7's marker, on the CI-watch path too. `pr-diff-against-base.sh` never fetches (row 7), so it can read a deleted stack base's stale local ref. Step 1's fetch is what catches a deleted stack base, and the design doc records that ordering dependency.
3. **Helpers in `claude/.claude/hooks/_lib.sh`**, beside the default-branch layers (row 23):
   - `_lib_configured_stack_base STDERR_PREFIX REPO_ROOT` derives the branch from `git symbolic-ref -q HEAD` with `refs/heads/` stripped, reads the key with `git config --get`, and validates the value. Only `git config` exit 1 means unset.
     - Exit 0 prints `<branch> <value>` on one line. Branch names cannot hold a space, so the split is unambiguous.
     - Exit 1 means no key or a detached HEAD, with no output.
     - Exit 2 means a stop, with the formatter's line on stderr and empty stdout.
     - Git calls go through `_lib_capped`, and each status is tested explicitly. Callers capture with `rc=0; out=$(...) || rc=$?`, which suspends `set -e` inside the function (row 28), and treat every status other than 0 and 1 as a stop.
   - `_lib_print_unusable_stack_base STDERR_PREFIX BRANCH VALUE REASON` prints the stop line. It is the one home of the fix text.
4. **Consumers.** Three scripts measure this branch against a base (row 24). The two below read the key. The third, the SessionStart hook `check-branch-divergence.sh`, stays unchanged (row 37, Out of scope).
   - `pr-diff-against-base.sh` calls the helper inside the non-`--staged` arm, before L70, so `--staged` never reads the key.
     - Exit 1 runs today's L70-87 unchanged, so a branch with no key gets identical output and the pinned lines stay valid (row 22).
     - Exit 0 replaces today's `gh pr view` call with `gh pr view --json state,baseRefName --jq '.state, .baseRefName'` (row 29), uncapped like today's call (row 8). A gh failure, or a reply that is not two lines, means no open PR, as a gh failure does today. An `OPEN` PR with another base stops. Otherwise the script prints one stderr line naming the key and its value, then runs L79-87 on the value. On the key path a merge-base failure at L79-81 stops through the formatter, reason "no merge-base with origin/<value> (not fetched yet, or unrelated history)", and the no-key arm keeps L80's line (row 7).
     - Any other status exits 1, because the script's exit 2 means a usage error (row 28).
   - `branch-divergence-status.sh` calls the helper before L33. Exit 1 keeps L33-36, exit 0 uses the value, and any other status exits 1.
     - Its fetch becomes `+refs/heads/<base>:refs/remotes/origin/<base>`, then `git rev-parse --verify --quiet refs/remotes/origin/<base>^{commit}` (row 27). The named destination keeps a narrowed `remote.origin.fetch` from leaving the ref missing or stale, and `+` lets a force-pushed stack base update.
     - A fetch or verify failure stops for a stack base and keeps L57's line for the default branch.
     - It measures against the resolved base. Stdout keeps `Default branch: <name>` for the default branch and prints `Base branch: <name>` for a stack base. It makes no gh call (row 5).
5. **Markers and push gate unchanged.** The `cumulative-review` hash covers diff bytes only (row 9), and `status` recomputes through the same script, which reads the same key (row 8). The push gate computes no base (row 10). Fidelity tooling reaches the base only through the diff file (row 25).
6. **Step 8 passes `--base` explicitly.** It appends `--base "<name>"` when step 6 printed `Base branch: <name>`, and a non-stacked PR keeps today's command. The push gate matches `gh pr create` by those words alone, so the same marker allows the flag (row 10). The redaction hook scans the base name like the rest of the command (row 31). It denies a tracker-named stack base in an opted-in repo, as it would a tracker-ID title, and the engineer decides.
7. **Skill edits, each replacing a line in place.**
   - `ready-for-review/SKILL.md`, at its 200-line cap (G3, row 20):
     - L43: when the engineer has said this branch is stacked on another branch, run `git config "branch.<branch>.gh-merge-base" "<stack-base>"` first, both names quoted literal text from their words, the stack base without an `origin/` prefix.
     - L71 says "PR-vs-base diff".
     - L112 also passes `pr-description` the branch on step 1's `Base branch:` line, when one printed. Step 5 runs before step 6 (row 33).
     - L150 adds the `--base "<name>"` clause after the backticked `gh pr create` span, which keeps its single `gh pr create --title` occurrence (row 21).
   - `pr-description/SKILL.md` L170 defines `<base>` as `origin/<name>` for the base the caller passed, else the open PR's base, else the default branch (row 32). No PR exists at step 5, so without it a stacked branch's body is checked against the stack base's commits too.
   - `git-feature-branch-sync/SKILL.md` L15-16 and L34: the `main` placeholder and "Reports the default branch" become the base the recipe reports, which is the stack base when the key is set (row 14). Without them, a `Base branch:` report sits beside rebase and merge commands against `origin/main`.

**Alternatives set aside.**

- **A `--base` flag on `pr-diff-against-base.sh`.** `marker.sh status` calls it with no arguments (row 8), so the flag would still need a persistent store.
- **An environment variable.** It does not survive across Bash tool calls or sessions, so a later `status` call would not see it (row 8).
- **A new key such as `branch.<name>.rfr-base`.** Same mechanism as the chosen key, but gh ignores it (row 15), so a hand-run `gh pr create` would target the default branch.
- **Pointing the branch's upstream (`branch.<name>.merge`) at the stack base.** Steps 6 and 8 rely on the branch's own `origin/<branch>` tracking (row 33).
- **Auto-detecting the base from open PR heads.** It needs a network scan and a heuristic the engineer would confirm anyway.
- **One resolver with a precedence chain (open PR, key, closed PR, default) and PR-lookup and default-layer parameters.** Only `pr-diff-against-base.sh` reads a PR, so "key set: use it or stop; no key: today's code" gives the same answers and leaves every no-key path byte-identical.
- **A named trigger per cause, each with its own remedy text.** The causes differ only in the reason clause, so one formatter holds the fix text once.
- **A check that the stack base's own PR merged** (`gh pr list`, a tip comparison and an acknowledgement key). Dropped per row 6, and its case goes to Out of scope.
- **Letting an open PR's base silently override a disagreeing key.** It hides a retarget or a wrong base, against row 2's posture (row 4).
- **Folding the base ref into the `cumulative-review` hash.** It contradicts the recorded decision in row 9.
- **Treating a key equal to the default branch as unset.** Such a key measures against the same branch, so only the output label would differ.
- **Relying on gh's key read at step 8.** It keeps the base out of the command text the redaction hook scans (row 31).
- **`git fetch origin <base>` under the configured refspec.** A narrowed `remote.origin.fetch` leaves `origin/<base>` missing or stale while the fetch succeeds (row 27).
- **Printing a fork point in the stop line, or adding a `git rebase --onto` recipe to the sync skill.** The line names `--onto` and leaves the old base commit to the engineer, who runs the rebase, and Out of scope records the gap.

**Assumption ledger.**

Root. `/ready-for-review` takes every base-dependent fact from an open PR's base or the default branch: the sync check, the cumulative diff, the fidelity diff file, the behind recheck, and the PR's target. It has no way to learn a stack base before the PR exists, so a stacked branch is reviewed against the default branch, the base PR's work included.

Givens.
- G1. GitHub sets each PR's `baseRefName`, deletes or keeps a merged base branch, and decides whether to retarget a stacked PR. Reason: GitHub and each repository's admins own these.
- G2. gh defines what the `gh-merge-base` key means, which gh commands read or write it, and which gh versions read it. Reason: the vendor owns it.
- G3. `ready-for-review/SKILL.md` stays within the 200-line cap. Reason: `check-skill-length.sh` sets the cap repo-wide (`docs/skills.md` L155), and changing it needs a decision outside this plan.

Rows.
1. [engineer-verified: "Ask the architect"] The engineer left the stack base's storage to this design, answering "Where should the stack base live ... ?".
2. [engineer-verified: "Fail closed with guidance (Recommended)"] When the configured stack base no longer exists on origin, the gate fails closed with guidance.
3. [engineer-verified: "Allow; open PR with --base <stack-base> (Recommended)"] The gate lets a stacked branch finish and opens its PR against the stack base.
4. [unverified] Applying row 2's fail-closed posture to an unusable key value and to an open PR whose base differs from the key is this design's inference. The question the engineer answered covered only a base missing on origin. The brief asked for a guard when the stack base "merges or is retargeted". Asked earlier whether to drop the merged-PR check or this posture, the engineer typed "I guess keep all? I'm not sure.", so the row stays open.
5. [engineer-verified: "Apply architect's changes (Recommended)"] The engineer accepted the consult's two changes: `branch-divergence-status.sh` makes no PR lookup, and the new design doc gives the markers rationale in one line.
6. [engineer-verified: "Ask the architect. To me this feels over engineered"] Asked "Keep T4 with the ack key, or drop T4?", the engineer left the choice to this design and found the design over-engineered. This row covers only that T4, the earlier draft's check that the stack base's own PR had merged, is dropped, and that the design should be simpler.
7. [verified: `pr-diff-against-base.sh` L70-87] The script takes `gh pr view`'s `baseRefName`, else `_lib_default_branch_or_guess`, then diffs from `git merge-base origin/<base> HEAD`. When `origin/<base>` does not resolve, it exits 1 at L79-81. It never fetches and takes no base override.
8. [verified: `marker.sh` L12-15, L1013; `_lib.sh` L1228-1237] `marker.sh status` recomputes the `cumulative-review` value by running `pr-diff-against-base.sh` with no arguments under a 15-second cap, which exists because the script calls `gh pr view`. Any base input must therefore be repository state, not an argument or an environment variable.
9. [verified: `marker.sh` L656-704; `docs/design-decisions/ready-for-review-cumulative-diff-cache.md` L9] `write cumulative-review` hashes the subject `--record` stored. The decision doc reads: "The preimage is the diff bytes alone, deliberately excluding the merge-base SHA and the base-branch ref".
10. [verified: `require-ready-for-review.sh` L338-340, L392-397, L399-414] The push gate detects `gh pr create` by the words `gh pr create` alone, so flags after them change nothing. It skips the PR-existence check for that command, allows it when a completion marker holds HEAD, and computes no diff or base. The marker therefore blocks opening a PR first.
11. [verified: `branch-divergence-status.sh` L33-36, L54-63] The script resolves origin/HEAD's default branch only, fetches with `origin "$DEFAULT_BRANCH"`, prints `Default branch: <name>`, and makes no gh call. The fetch runs under a 2-second `timeout` with stderr discarded, so a timeout and a missing remote branch reach the same failure line.
12. [verified: `check-branch-divergence.sh` L59-71, L102-128] The SessionStart hook exits silently on a detached HEAD, on the default branch, or with no local `origin/<default>` ref. Otherwise it measures against the default branch and tells the agent to offer `/git-feature-branch-sync`. It always exits 0.
13. [verified: `docs/design-decisions/ready-for-review-base-drift.md` L30, L48; `respond-pr/SKILL.md` L24] The base-drift doc records as a residual that the divergence script measures against the default branch for a PR with another base, and that the step-6-to-step-1 loop then exits only through the context-budget deferral or the engineer. `respond-pr`'s precheck means to run the recipe against the PR's base branch.
14. [verified: `git-feature-branch-sync/SKILL.md` L15-16, L34-36, L46-47] The sync skill uses `main` as "the default-branch placeholder", says the recipe "Reports the default branch", and rebases or merges against `origin/main`.
15. [verified: `/usr/share/man/man1/gh-pr-create.1` L2, L38-40] The gh 2.100.0 manual reads: "If not provided, the value of gh-merge-base git branch config will be used. If not configured, the repository's default branch will be used."
16. [verified: repository grep for `gh-merge-base`, no match; gh source on the `cli/cli` repository's trunk, `pkg/cmd/pr/checkout/checkout.go` and `pkg/cmd/pr/create/create.go`] Nothing in this repository reads or writes the key. `gh pr checkout` writes only `branch.<branch>.remote`, `pushRemote` and `merge`, and `gh pr create` only reads the key, when `--base` is absent. This covers those two gh files only.
17. [unverified] GitHub retargets a stacked PR only when its merged base branch is deleted, and deleting head branches on merge is a per-repository setting. A merged stack base that stays on origin therefore trips no stop, the residual Out of scope names.
18. [unverified] `git check-ref-format "refs/heads/<name>"` rejects `..`, `@{`, spaces and control characters but not a leading `-`, and `--branch` mode expands `@{-N}`. The `test_lib.py` cases for each shape settle this row.
19. [unverified] `git branch -d` and `-D` remove the deleted branch's `branch.<name>` config section, so a reused branch name does not inherit an old key. If wrong, an inherited key stops the gate once its stack base leaves origin, and is used while it remains.
20. [verified: `ready-for-review/SKILL.md`, 200 lines; `check-skill-length.sh` L11, L118] The skill is at the cap, so any added line must be offset by a removed one.
21. [verified: `test_require_ready_for_review.py` L122-139, L2025, L2633; `test_hook_alignment.py` L3295-3300] Both tests require exactly one `gh pr create --title` occurrence. One of them runs the span up to the closing backtick through the hook.
22. [verified: `test_skills.py` L5208, L5242, L5251; `test_pr_diff_against_base.py` L29-46, L271, L286, L340, L358, L380, L414, L1095, L1109-1110; `test_marker_script.py` L238-258; `test_branch_divergence_status.py` L3-4, L55; `test_default_branch_parity.py` L1-5, L88-89, L145] Existing tests pin steps 3 and 4's invocation literals, the default arm's "defaulting base to" and "no default branch resolved" lines, `Default branch:`, the divergence script's "could not resolve origin/HEAD" line, and its gh-free description. Both gh shims answer only an argv holding a literal `baseRefName` element and exit 1 otherwise, so the key path's `state,baseRefName` call reads as no open PR under them.
23. [verified: `_lib.sh` L1293-1346, L4628-4630; `.claude/rules/bash-unit-test-seams.md`] `_lib.sh` holds the default-branch layers, so the new helpers extend a lib whose domain already covers them, the seams rule's first preference. `_lib_review_ledger_path` prints `<scope> <path>` on one line, the precedent for a two-field result.
24. [verified: grep for `_lib_default_branch_or_guess "` and `_lib_default_branch_from_origin_head "` outside tests] There are 9 call sites in 8 files outside `_lib.sh`, plus 3 consumers inside it (L1282, L1464, L4659). Only `pr-diff-against-base.sh`, `branch-divergence-status.sh` and `check-branch-divergence.sh` measure this branch against a base. The rest ask which branch is the default and keep it: session title, push-gate bypass, settings guard, `worktree-ensure.sh`, `cleanup-merged-branches.sh`, the verification-cache sentinel, the gate-diff anchor, and the review-ledger scope.
25. [verified: Step 3 exploration of `skill-fidelity-report.sh`; `ready-for-review/SKILL.md` L96, L99; grep of `transcript_analysis/review_trace.py`] Fidelity tooling keys on the branch name, and step 4's reviewer reads the diff file `pr-diff-against-base.sh` wrote, so the base reaches it only through that script. `review_trace.py` matches none of `default_branch`, `origin/main`, `merge-base`, `baseRefName` or `git diff`, so it takes no base.
26. [verified: `_lib.sh` L4641-4656, L107-120, L156-164] `_lib_review_ledger_path` reads HEAD once with `symbolic-ref -q`, reading status 1 as a detached HEAD and any higher status as a git failure. A `_lib_capped_for` cap kill exits 124 or 143 on SIGTERM and 137 on SIGKILL, and callers must fail closed on any status they do not recognise.
27. [verified: `worktree-ensure.sh` L202-209] `worktree-ensure.sh` fetches with `+refs/heads/<branch>:refs/remotes/origin/<branch>`, with a comment that a named destination keeps a narrowed `remote.origin.fetch` from leaving the ref stale. It then verifies `refs/remotes/origin/<branch>^{commit}`.
28. [verified: `branch-divergence-status.sh` L13; `pr-diff-against-base.sh` L22, L48, L55] Both scripts run `set -euo pipefail`, and `pr-diff-against-base.sh` exits 2 for a usage error.
29. [verified: `review-pr-checkout.sh` L330] `--jq '.headRefOid, .baseRefName'` is the precedent for reading two `gh pr view` fields as two lines from one call.
30. [unverified] `gh pr view` with no argument can return the branch's merged or closed PR. `ready-for-review/SKILL.md` L77 relies on it ("usually because this branch's PR is already merged"), and `gh-pr-view.1` L16-17 states no state filter. The key path reads `state`, so neither answer breaks it.
31. [verified: `deny-private-project-refs.sh` L27-29, L51-56, L214-215; `test_deny_private_project_refs.py` L1249-1272] The redaction hook scans the full command text in claude-config and in opted-in repos. It never reads the current branch name, and it denies a tracker-named branch typed after `--head`.
32. [verified: `pr-description/SKILL.md` L170-173, 243 lines; `check-skill-length.sh` L22, L115-116] `pr-description` compares the body against `git log <base>..HEAD` without defining `<base>`, and its cap is 250 lines.
33. [verified: `ready-for-review/SKILL.md` L112, L121, L123, L148] L112 lists what step 5 passes `pr-description`, and step 5 runs before step 6's recipe. Step 6 checks the branch against `origin/<branch>`, and step 8 halts when the branch has no remote tracking.
34. [verified: `marker.sh` L260, L1013; `ready-for-review/SKILL.md` L68, L77] When `pr-diff-against-base.sh` fails inside `status`, the value is empty and never reads `live`. Step 3 then runs the script itself and halts on a non-zero exit.
35. [verified: `CHANGELOG.md` L5-9; `.claude/rules/design-decisions.md`] `[Unreleased]` → `### Changed` holds earlier `/ready-for-review` behavior changes. A design-decision file needs a slug matching `^[a-z][a-z0-9-]*\.md$`, an H1, and a dated italic provenance line with no `Formerly` clause.
36. [engineer-verified: "Keep the edit (Recommended)"] Asked whether to keep the `pr-description/SKILL.md` L170 edit, which the brief does not mention, the engineer kept it.
37. [engineer-verified: "Cut it (Recommended)"] Asked whether to keep the `check-branch-divergence.sh` SessionStart edit, the engineer cut it. This row covers only that the hook edit is cut.

Mechanisms.
- M1. The key is the one store for a stack base, set only from the engineer's words. anchors: root, row1, row3, row8, row15, row16, row19, row33, G2.
  - Lighter options fail: a `--base` flag and an environment variable both fail on row 8. A custom key works, but gh ignores it (row 15). Repointing upstream tracking breaks row 33.
- M2. One fail-closed rule: with the key set, use its value or stop with one formatter's line. It covers an unreadable or invalid value, a failed stack-base fetch, a missing merge-base, and an open PR with another base. anchors: row2, row4, row6, row7, row11, row17, row18, row30, row34, G1.
- M3. Two `_lib.sh` helpers: the key read with its exit map, and the stop-line formatter. anchors: row23, row26, row28.
- M4. `pr-diff-against-base.sh` takes the key path or runs today's code, and checks an open PR's base on the key path only. anchors: row7, row8, row22, row28, row29, row30.
- M5. `branch-divergence-status.sh` measures against the key when it is set, fetches into a named destination with a verify, and stays gh-free. Row 13's residual narrows to a branch with no key. anchors: row5, row11, row13, row27.
- M6. No marker, push-gate or fidelity change. anchors: row9, row10, row25.
- M7. Step 8 passes `--base` explicitly. anchors: row3, row10, row21, row31.
- M8. In-place line edits: four to `ready-for-review/SKILL.md`, one to `pr-description/SKILL.md`, and two to `git-feature-branch-sync/SKILL.md`. anchors: row14, row20, row21, row32, row33, G3.
- M9. Docs: a new design-decision file, the base-drift doc's narrowed residual, one doc entry and a CHANGELOG bullet. anchors: row5, row13, row35.
- M10. Tests for each helper exit, each consumer's key path and stops, the unchanged no-key paths, the `status` recompute, and a stacked `gh pr create` at both hooks. anchors: row10, row18, row22, row31.

## Critical files

One `code-writer` dispatch. The `Base branch:` line and the stop line are both script output and skill text, so one context keeps them consistent, and the two hook-layer cases pin today's hook behavior rather than change a hook. Its brief states that existing tests keep every assertion: only `_gh_shim_source` in `test_pr_diff_against_base.py` gains an optional argument and `_run_script` in `test_branch_divergence_status.py` gains a gh shim, and new tests are added. Verify with `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py`, `scripts/list-shell-files.sh | xargs -0 scripts/venv-run.sh shellcheck`, and `wc -l` on the three `SKILL.md` files.

- `claude/.claude/hooks/_lib.sh` — the two helpers of Design item 3, after `_lib_default_branch_or_guess` (L1315-1346), each header carrying its exit map. Reuse `_lib_capped`.
- `claude/.claude/scripts/pr-diff-against-base.sh` — header L2-4 and the key path before L70.
- `claude/.claude/scripts/branch-divergence-status.sh` — header L1-11; the helper call before L33; the named-destination fetch and verify at L54-59; measurement and the `Base branch:` line at L61-83.
- `claude/.claude/hooks/tests/test_lib.py` — direct `bash -c '. _lib.sh; ...'` cases for `_lib_configured_stack_base`:
  - unset; a valid slashed value on branch `feat/x`; a detached HEAD; a key set in the main worktree read from a linked one (`_add_linked_worktree`, L6033);
  - empty; a leading `-`; `a..b`; `@{-1}`; a space; this branch's own name;
  - a git shim exiting 128, then 124, on `config`.
  - Every non-zero exit leaves stdout empty, and exit 2's stderr starts with the passed prefix, names the key, and says the engineer runs the fix, not the agent.
- `claude/.claude/scripts/tests/test_pr_diff_against_base.py` — `_gh_shim_source` gains an optional raw reply for the `state,baseRefName` call; existing callers keep today's behavior. Cases:
  - A three-tier fixture: the default tip, a stack base adding `base.txt`, and the branch adding `own.txt`. With the key set and no PR, stdout holds `own.txt` and not `base.txt`, and the no-key control holds both. One case uses a slash-containing stack base.
  - An OPEN PR agreeing with the key gives the same diff. OPEN and MERGED replies with another base run on one fixture, parametrized by state: OPEN exits 1 with empty stdout and one stderr line naming both branches and the key, and MERGED uses the key.
  - An exit-0 empty reply and a single `OPEN` line each give the key's diff and exit 0.
  - A leading-`-` key, a key equal to this branch, and a key whose `origin/<value>` was never fetched each exit 1 with empty stdout, a line prefixed `pr-diff-against-base.sh:` naming the key, and no "defaulting base to" line.
  - The ordering pin, under `_env_with_gh_shim(tmp_path, None)`: with the stack base deleted on the bare remote (as `test_branch_divergence_status.py` L65 does) and its local ref still present, the script exits 0 with the key's diff.
- `claude/.claude/scripts/tests/test_branch_divergence_status.py` — `_run_script` (L24) puts a fail-loud gh shim on PATH for every case, asserting no call, and the L3-4 docstring is reworded. Cases:
  - No key: `Default branch:` as L55 pins it.
  - Key set: `Base branch: <name>` and a behind count against the stack base, also under a narrowed `remote.origin.fetch`.
  - The stack base deleted on the bare remote, with its local ref still present: exit 1 with a line naming the key, "fetch failed" and the retry clause.
  - An invalid key exits 1 with this script's prefix.
- `claude/.claude/hooks/tests/test_require_ready_for_review.py` — a variant of `test_completion_marker_at_head_allows_create_with_active_marker_live` (L2536) whose command appends `--base stack` allows.
- `claude/.claude/hooks/tests/test_deny_private_project_refs.py` — beside L1249, under `private_projects_optin_repo`: `gh pr create --base` with that test's tracker-named branch denies naming its ID, and `--base stack` allows.
- `claude/.claude/hooks/tests/test_marker_script.py` — a fixture on a non-default branch where `origin/main` is behind `origin/<stack>`, which is behind HEAD, set with `git update-ref` as `_arm_default_branch_ref` does. With the key set and no PR, `pr-diff-against-base.sh --record`, then `marker.sh write cumulative-review`, then `marker.sh status` reads `live`. After `git config --unset-all`, `status` reads `historical`, and "could not verify" is not in stderr. The existing shim stays unchanged (row 22).
- `claude-skills/skills/ready-for-review/SKILL.md` — L43, L71, L112 and L150 replaced in place. The file stays at 200 lines.
- `claude-skills/skills/pr-description/SKILL.md` — L170 replaced in place.
- `claude-skills/skills/git-feature-branch-sync/SKILL.md` — L15-16 and L34 replaced in place.
- `claude-skills/skills/tests/test_skills.py` — one test reusing `_section_between`: step 1 names `gh-merge-base`, and step 8 names `--base` and `Base branch:`.
- `docs/scripts.md` — the L167 `pr-diff-against-base.sh` entry: the key path and its stops. Re-derive its "two `gh pr view` calls" count against the code.
- `docs/design-decisions/ready-for-review-base-drift.md` — narrow L30's parenthetical and the L48 residual to a branch with no key set. L48 keeps its loop consequence for that case.
- `docs/design-decisions/ready-for-review-stacked-pr-base.md` (new). Sources: this plan. It covers:
  - why the key, and what gh's source shows about its writers (row 16);
  - the one rule and where each stop fires, with the step-1-before-step-3 ordering dependency and the open-PR mismatch firing at step 3, after any step-1 sync;
  - the tracker-named base at step 8;
  - the alternatives above and the residuals in Out of scope;
  - why markers stay unchanged, in one line citing `ready-for-review-cumulative-diff-cache.md` (row 5).
- `CHANGELOG.md` — one `[Unreleased]` → `### Changed` bullet. `/ready-for-review`, the divergence recipe and `respond-pr`'s precheck use a set stack base and stop on an unusable one.
- `.claude/plans/rfr-stacked-pr-base.md` — this plan, committed at plan-it Step 7.

`plugins/skill-management/hooks/_lib.sh` keeps its default-branch copy unchanged, because no plugin calls the new helpers.

## Verification

1. `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py` passes. The `_lib.sh` edit may widen the selection on its own, which is the documented case for a wider run.
2. `scripts/list-shell-files.sh | xargs -0 scripts/venv-run.sh shellcheck` and `scripts/venv-run.sh ruff check claude/.claude/ claude-skills/ plugins/ evals/` pass.
3. `wc -l` prints 200 or fewer for `ready-for-review/SKILL.md` and 250 or fewer for `pr-description/SKILL.md`, and `git-feature-branch-sync/SKILL.md` stays within the cap `check-skill-length.sh` sets for it.
4. The new tests show each of these:
   - With the key set and no PR, the cumulative diff holds only the branch's own commits, and the no-key control on the same fixture holds the stack base's.
   - `marker.sh status` reads `live` after a stack-base write and `historical` once the key is unset, with no "could not verify" in stderr.
   - `branch-divergence-status.sh` measures against the key when it is set, prints `Default branch:` when it is not, and never calls gh.
   - Each stop exits non-zero with empty stdout and no default-branch fallback line: an invalid key at both scripts, a stack base deleted on origin at the divergence script, and an open PR with another base or a never-fetched stack base at `pr-diff-against-base.sh`.
   - With a completion marker, the push gate allows `gh pr create ... --base stack`, and the redaction hook denies a tracker-named `--base` in an opted-in repo.
5. Existing tests keep every pre-existing assertion. `git diff origin/main -- <existing test files> | grep -E '^-[[:space:]]*assert'` prints nothing. The `gh pr create --title` extraction tests in `test_require_ready_for_review.py` and `test_hook_alignment.py` pass unchanged.
6. The review pipeline runs `/skill-review` on the three `SKILL.md` diffs (hook-enforced).

## Out of scope

- **A stack base that merged but whose branch stays on origin.** In a repository that keeps merged branches, GitHub does not retarget the PR (row 17), so no stop fires and the PR keeps targeting the merged branch. The check that caught this case was dropped (row 6). The follow-up is filed as a GitHub issue after this PR merges.
- **Auto-detecting a stack base.** Set aside in Approach. The engineer states it once.
- **Recording the key when a branch is created** (`branch-management`). The gate records it at step 1, and capture at creation is a separate change.
- **`branch-divergence-status.sh` reading an open PR's base when no key is set** (row 13). A PR whose base is not the default branch, such as a release-branch PR, is still measured against the default branch at steps 1 and 6 and in `respond-pr`'s precheck. The script stays gh-free (row 5). The follow-up is filed as a GitHub issue after this PR merges.
- **`respond-pr`'s outcome for a failed recipe.** `respond-pr/SKILL.md` L24 names only CONFLICT and CLEAN. A fetch failure already exits 1 there, and a stop joins that undefined arm. The CHANGELOG names the behavior change.
- **A rebase recipe for after a stack base squash-merges.** By this design's unverified reading of git's patch-id skip, a plain `git rebase origin/<new-base>` replays the old base's commits, so the stop line names `git rebase --onto` but gives no way to find the old base commit it takes. `git-feature-branch-sync` carries no such recipe today, and the engineer runs the rebase.
- **Discovering the key.** The key is set only when the engineer states the stack, in conversation or in `/ready-for-review`'s arguments. Otherwise the branch is reviewed against the default branch, as today.
- **The SessionStart advisory on a stacked branch.** `check-branch-divergence.sh` stays unchanged (row 37), so it still measures a stacked branch against the default branch (row 12). An agent following its offer via `git-feature-branch-sync` gets the stack-base measurement, because that skill runs `branch-divergence-status.sh`, which reads the key.
- **`select-tests.py`'s `origin/main` diff** (`select-tests.py` L941). It over-selects tests on a stacked branch, which is the safe direction, and CI runs the full suite.
- **A transient `gh pr view` failure on the key path.** It reads as no open PR, so that run skips the open-PR check. It is the same class of fallback `pr-diff-against-base.sh` has today, and the design doc records it.
