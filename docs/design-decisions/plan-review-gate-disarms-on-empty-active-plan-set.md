# Plan-review gate disarms on an empty active plan set

*2026-09-19.*

`require-plan-review.sh` allows a non-plan `Write`/`Edit` whenever `_lib_active_plan_files` returns an empty set, whether or not a trusted in-progress merge/cherry-pick/revert base was detected. `_lib_active_plan_hash` returns empty stdout for an empty active set and binds no value to the base.

## The symptom this removes

Change `1c4ba8d8` made `_lib_active_plan_files` merge-aware: mid-merge it excludes a plan file identical to the computed base. The same change made `_lib_active_plan_hash` bind an empty active set to `sha256("plan-review-empty-base:$base")` whenever the base was non-empty. The gate therefore demanded a marker for a plan set that is empty by construction. No `/plan-review` run can honestly produce that marker, because there is no plan to review. `/plan-review` has no "no plan found" branch, so it falls through to the most recent plan in `.claude/plans/`, which mid-sync is upstream's, and reviews content the branch did not author.

That change's tests asserted `_lib_active_plan_files` only, never the hook's verdict, so the suite stayed green. The regression tests now assert the verdict across the clean-merge, conflicted-merge, and cherry-pick states.

## Why dropping the binding is safe

The binding defended one path: a forged base that reaches an admissible anchor. For a Bash-capable actor, that path buys nothing a cheaper bypass doesn't already grant, and needs no forgery at all: `require-plan-review.sh` gates only `Write`/`Edit`/`MultiEdit`/`ExitPlanMode` (its own header comment), so a Bash tool call can write the plan file directly with no gate watching it. Reaching an admissible anchor by hand instead — `git update-ref refs/remotes/origin/<default> <existing-commit>` followed by `git merge --ff-only origin/<default>` — buys nothing that write didn't already buy, and costs an extra step: `--ff-only` refuses a dirty working tree ("local changes ... would be overwritten by merge"), so it only succeeds once the locally-modified plan is staged with `git add` first. The dominating bypass is that ungated Bash write, not `.claude/plans/merge-aware-review-gates.md` residual 1 (the clean-merge zero-gate bypass against the commit gate `require-code-review.sh` fires on `git commit`): residual 1 says nothing about a `Write`/`Edit` gate, so closing it would not change whether this binding is dominated. Reopen this section's conclusion only if the ungated-Bash-write bypass above is ever closed.

This dominance argument holds only for a Bash-capable actor. A Write/Edit-only actor has no Bash shortcut and no way to run `git update-ref`/`git merge` either — see "Accepted residual: a Write-only, existing-commit forged anchor" below for what remains reachable there.

`_lib_gate_diff_base` emits a non-empty base only when the in-progress state's OID is an ancestor of `origin/<default>` or of `HEAD` — but it never verifies that `MERGE_HEAD` or `refs/remotes/origin/<default>` were themselves written by an honest git operation, only that whatever OID they currently name passes the ancestry check. That gap is exactly what the residual below reaches.

## Accepted residual: friction lost across a conflicted window

A merge, cherry-pick, or revert that conflicts in a file other than the plan file cannot auto-commit, so the in-progress ref persists and the base stays non-empty across every `Write`/`Edit` of the conflict-resolution window. Before this change each of those calls was denied until a `/plan-review` pass; now they proceed freely while the plan file matches the base.

For a Bash-capable actor this grants no new capability: a forger who can set the anchor also chooses the operation's shape, and the clean variant auto-commits with zero gates. What is lost is friction on the intervening calls, not a control over the plan file's content, which neither gate reviewed in this state before or after. `require-plan-review.sh` writes no compliance log, so the conflicted path leaves no audit trail the clean bypass lacks. The conflicted-merge regression test pins this window in the suite.

## Accepted residual: a Write-only, existing-commit forged anchor

A Write/Edit-only actor — one without Bash, in a linked worktree — can still reach the forged-anchor path the binding used to defend. In a linked worktree, `MERGE_HEAD` lives in that worktree's own per-worktree gitdir and `refs/remotes/origin/<default>` lives in the main checkout's shared refs; neither is a subpath of the linked worktree's own root, so `require-plan-review.sh:206-208` exempts a Write to either one even while the gate is armed for every other path. Such an actor hand-writes both refs to the OID of an existing commit — another worktree's own WIP plan draft, say, already sitting in the object store — then edits the tracked plan to that commit's own version of the same path. Base substitution then excludes the plan from the active set exactly as it does on the Bash-capable route, and the gate allows what it denied before this change.

The content this route can surface is bounded to a version of that exact plan path already committed somewhere: a novel plan stays untracked (`git ls-files --others` is base-independent) and stays active, since hiding it from that check needs `git add`, which needs Bash. Severity: Low. Agents holding `Write` also hold `Bash`, and a Bash file write bypasses this whole gate, so the Write-only route's marginal exposure over that baseline is small. `test_hand_forged_anchor_no_real_merge_allows_unrelated_write` pins this route as a tested fact, distinct from the real-`git merge` construction the sibling forged-anchor test drives.

## Why `_lib_code_review_marker_value` keeps its sentinel

The code-review arm binds an empty base-relative diff to `sha256("code-review-empty-base:$base")`. This is not the same bug. With a non-empty base, `require-code-review.sh` skips its empty-diff exit and falls through to the marker comparison, so the value is consulted as an authorization. Without the binding, a marker earned in one operation's empty-diff case would validate any other base landing on the same empty result. The plan-review path disarms and consults no marker at all. `test_marker_lib.py` pins the divergence so a sibling-audit sweep does not delete the surviving sentinel for symmetry.

For what a completion marker authorizes, see `docs/hooks.md` § "Marker keying and gate-release authority".

## Pre-analyzed successor

If the empty-active-set disarm ever needs tightening, the analyzed candidate is: when the base-relative active set is empty and the base is non-empty, recompute `_lib_active_plan_files "$repo" ""` and hash that HEAD-relative result. It is declined today because it re-arms the gate on exactly upstream's plan content, converting a guaranteed over-gate into a probabilistic one.

Reopen it only when residual 1 (clean-apply zero-gate) or residual 11 (`bash -c`/`eval` wrapping) of `.claude/plans/merge-aware-review-gates.md` is closed, per residual 14 there. Accumulated annoyance is not a reopening trigger.
