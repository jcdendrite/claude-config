# `/ready-for-review` and a base that moves during the gate

*2026-10-21.*

**Superseded in part (2026-10-23):** `branch-divergence-status.sh` now exits 1, instead of printing `Trial merge: CLEAN`, when `git merge-tree` cannot complete, and the README floor is git 2.43. So the git-below-2.38 sentence in "What the gate does not catch after step 1" and the Residuals bullet on a failed `git merge-tree` no longer hold. Every other clause here stands.

`ready-for-review/SKILL.md` § "6. Final hygiene recheck (halt on fail)" re-runs the divergence detection recipe every time. A branch that is behind the base with `Trial merge: CLEAN` passes without a sync, and the Completion summary reports the behind-count. A branch that is behind with `Trial merge: CONFLICT` halts step 6 and returns the gate to step 1, which syncs before verification runs again. A recipe failure halts step 6, is re-run in place once, and a second failure is reported to the engineer and the gate stops. The bound on a moving base covers only the clean case: a conflict has to be resolved before merge under any rule, and the resolution's new HEAD needs a completed gate before the push gate releases a push on a branch with an open PR.

## What the gate certifies about the base

The verified tree holds the base as of the run's step-1 check. Fix loops return to step 2, not step 1, so no fix loop inside one run syncs again. On the CI-watch path (`ready-for-review/SKILL.md` § "CI watch (out-of-band)"), a CI fix also re-enters at step 2, so the tree holds the base as of the original run's step-1 check. Nothing in the gate keeps the tree current after handoff, because the base keeps moving whenever other contributors merge.

## Why a clean trial merge is reported, not synced

- A step-6 sync gains only the gate's own run time: it moves the point at which the tree last held the base forward by that long.
- Its cost is a repeated gate. Step 2 re-runs, because the `verification` marker is keyed on the tree ([ready-for-review-verification-cache.md](ready-for-review-verification-cache.md)). Step 3 reuses its clean review only when the cumulative diff is byte-identical ([ready-for-review-cumulative-diff-cache.md](ready-for-review-cumulative-diff-cache.md)). Steps 4 and 5 re-run.
- Rechecking after each sync has no fixed point while the base moves, because each re-run gives the base time to move again. A single sync with no recheck has a fixed point, but pays the same cost for the same small gain.
- When step 2's scope exception or the caches make the re-run cheap, the gain is still only the run time, so those cases get no carve-out.
- The justification needs no CI or branch protection.
- The clean case matches `respond-pr/SKILL.md` § "Steps" (its divergence precheck), which blocks only on CONFLICT.

## What the gate does not catch after step 1

- A semantic conflict inside a textually clean merge is not caught by the gate, for example a base commit that adds a test pinning text the branch rewrote in another hunk. Whether a consumer's own merge-time or CI controls catch it is each repo's setting, and this doc enumerates none of them.
- The clean case adds no sync and no force-push. It moves nothing on the remote beyond what step 6's existing push bullet already pushes.
- The completion marker binds HEAD only. The behind-count and the `Trial merge:` verdict live only in the Completion summary, so a marker at HEAD certifies neither a conflict check nor a distance from the base. On git below 2.38 the verdict is not a check at all (see "Residuals").
- The count reaches only the Completion summary. That Completion line is deliberately unpinned: the step-6 text is pinned, and Completion restates it so the report stands alone. [ready-for-review-fix-loop-convergence.md](ready-for-review-fix-loop-convergence.md) records the same choice for another Completion restatement.

## Why a conflict returns to step 1

- Step 1 stays the one home for the sync rule and the post-resync marker rule, and its context-budget precondition runs before any re-sync or re-verification. A sync inside step 6 followed by re-entry at step 2 would restate the first and skip the second.
- There is no cap on repeated conflicts. Each conflict's resolution produces a new HEAD, and the push gate requires a completed gate on that HEAD for a branch with an open PR, so the re-verification is owed under any rule (see "Residuals" for the exception when the PR's base is not the default branch). A live active marker releases `git push` during the gate, and a failed `gh pr view` lets the push through. A cap would add a human stop whose only answers are the same re-run or a deferral, and step 1's context-budget precondition runs on every re-entry and offers the deferral once it trips.
- The Overview's "A halt on step 6 stays outside this routing" covers halts fixed inside step 6, such as the in-place re-run below. The conflict case leaves step 6, and step 1's budget check is unconditional, so either reading of that sentence gives the same agent behavior.
- [ready-for-review-fix-loop-convergence.md](ready-for-review-fix-loop-convergence.md) requires the loop-back wording to read one way, not two. The conflict route creates no second reading, because it passes through step 1 and then steps 2 and 3 in order.
- On the CI-watch path, which runs after handoff, step 1's sync can rewrite a PR already under review. `git-feature-branch-sync/SKILL.md` § "Rule of thumb" decides whether to rebase, merge, or stop and ask.
- Step 6's push bullet runs before its divergence bullet, so a CONFLICT return can publish a HEAD that step 1's sync then replaces. The sync can rebase and force-push, merge, or stop and ask (`git-feature-branch-sync/SKILL.md` § "Rule of thumb"). This is accepted.

## Why a failed check at step 6 halts and is re-run in place

- Step 1 already ran the same recipe under halt-on-fail, so a failure at step 6 most likely comes from the bounded fetch. The script names which of its failure conditions fired on stderr, not the underlying fetch error, because the fetch discards git's own output.
- A re-run in place that succeeds ends in one of step 6's defined outcomes. A second failure is reported to the engineer, who acts on the named cause, and step 7 withholds the completion marker because step 6 did not complete.
- The in-place re-run exists only while the session lives. A fresh session restarts at step 0, where step 1 syncs a behind branch.
- Restarting the gate would let step 1 sync a behind branch whose merge is clean, which undoes the clean case.
- Passing unchecked would leave the outcome outside `ready-for-review/SKILL.md` § "7. Record gate completion", whose closed list names the outcomes that count as complete without full execution.

## Residuals

- A semantic conflict in a clean merge after step 1 is not caught by the gate (see above).
- A failed `git merge-tree` prints `Trial merge: CLEAN`. That includes git below 2.38, which rejects `--write-tree`, against the README floor of 2.31. On those versions step 6's conflict arm cannot fire. Step 1 still syncs on any behind count above zero. What is lost is a conflict from base commits that land after step 1's check: during the gate's own run time and, on the CI-watch path (which skips step 1), during the time since the original run, which can be hours. `check-branch-divergence.sh`'s header accepts the same gap because later checkpoints gate, and this change removes step 6 from the checkpoints that count as gating it. The fix is a follow-up, because it changes an output contract that `respond-pr` shares.
- For a PR whose base is not origin/HEAD's default branch, such as a stacked PR or a PR against a release branch, the divergence script still measures against the default branch. The script never reads the PR's `baseRefName`, while step 1 captures it and names `origin/<base>`. The behind-count and the `Trial merge:` verdict are then measured against the wrong branch. Step 6's conflict arm can report CONFLICT again after a completed step-1 sync. The "no cap" reasoning in "Why a conflict returns to step 1" holds only when the measured branch is the one the sync targets. On such a PR the step-6 to step-1 loop exits only through step 1's context-budget deferral or the engineer stopping the run.
- The bullet branches on the full `Trial merge:` literals, which the script's own test pins only by substring.
- On the CI-watch path, where a repo's CI tests the PR merged with its base, a failure caused by base commits the branch lacks is not reproduced at step 2. Step 6's clean case then hands the fix off without those commits.

## Rejected alternatives

- **A behind-count threshold.** A count does not measure risk: one base commit can break the branch semantically while fifty documentation commits may not, so any cutoff would be an ungrounded number. `respond-pr` also proceeds on CLEAN at any count.
- **A cap on repeated conflicts.** See "Why a conflict returns to step 1".
- **A single sync with no recheck.** Every gate that ends behind pays for a second verification, and the gain is bounded by the gate's run time.
- **Sync without re-verification.** It breaks the first leg of the compensating control in [ready-for-review-cumulative-diff-cache.md](ready-for-review-cumulative-diff-cache.md): a rebase always changes the tree, so verification must re-run.
- **Deleting the step-6 check and leaving divergence to step 1.** A base commit that lands on the branch's lines after step 1's check would reach handoff unseen.
- **The count in the PR body.** The body goes stale as the base moves, `pr-description` owns the body, and the count is transient.
- **A non-zero exit that passes unchecked.** Step 7's closed list does not name that outcome, so a strict reader withholds the marker and no text names the recovery. Adding the outcome to the list would alter a second pinned clause and count a check that never ran as complete.
- **A non-zero exit that restarts the gate.** See "Why a failed check at step 6 halts and is re-run in place".
- **Conditioning the clean case on a CI attestation.** The justification above needs no CI, so there is nothing to attest.
- **A CI fix that reruns the gate from step 0.** Step 1's sync can rebase and force-push, merge, or stop and ask on a PR already handed off, before step 2 verifies the fix (`git-feature-branch-sync/SKILL.md` § "Rule of thumb").

## Sources

- `claude-skills/skills/ready-for-review/SKILL.md`, `claude-skills/skills/respond-pr/SKILL.md`, `claude-skills/skills/git-feature-branch-sync/SKILL.md`.
- `claude/.claude/scripts/branch-divergence-status.sh`, `claude/.claude/hooks/check-branch-divergence.sh`, `claude/.claude/hooks/require-ready-for-review.sh`.
- `.claude/plans/ready-for-review-step6-sync-bound.md` — full assumption ledger, mechanism list, and out-of-scope residuals.
