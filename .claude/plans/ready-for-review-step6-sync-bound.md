# Bound `ready-for-review` step 6's behind-check

## Context

Goal: give `ready-for-review` step 6 a stated rule for a branch that is behind the base, so a base that keeps moving cannot force repeated full-suite re-runs on a clean trial merge.

Ask: "yeah let's start with step 6 bound." Rule selected from the options offered: "Clean: report only; conflicts: halt (Recommended)", with the typed addition "Have the architect do an adversarial review against it." The engineer's earlier typed "Include P2" is superseded: asked "Do you still want P2 in this change?" after being shown the CI-watch restart's post-handoff rewrite risk, they selected "Drop P2 (Recommended)".

Step 6 of `claude-skills/skills/ready-for-review/SKILL.md` tells the agent to re-run the divergence detection recipe before handing off, and says nothing about what to do when the branch is behind. An agent that fills that gap by syncing changes the tree hash, which invalidates the `verification` marker, so step 2 re-runs in full. Step 1 already syncs and re-verifies once per gate. Nothing bounds a second sync at step 6.

## Approach

Step 6 now re-runs the divergence recipe every time. A branch that is behind with a clean trial merge passes without a sync, and the Completion summary reports how far behind it is. A trial-merge conflict sends the gate back to step 1, which syncs and verifies again. A recipe exit 1 halts step 6 until the recipe, re-run in place, succeeds.

- **Behind with `Trial merge: CLEAN`.** The branch passes without a sync, and the Completion summary reports the behind-count.
- **Behind with `Trial merge: CONFLICT`.** Step 6 halts and returns the gate to step 1. Step 1's in-sync precondition syncs before step 2 runs again.
- **Recipe failure (non-zero exit).** Step 6 halts. The agent re-runs the recipe once inside step 6, and does not restart the gate, because step 1 would then sync. If the re-run fails too, the agent reports the script's stderr cause to the engineer and stops. The in-place re-run exists only while the session lives: a fresh session restarts at step 0, where step 1 syncs a behind branch (the design doc says so).

The CI watch's "Land the fix" item (L200), its pin and `ready-for-review-fix-loop-convergence.md` stay unchanged (row 28). The design doc names the CI-watch path's residual.

The engineer's selected rule stands (row 1). Three details of the session's description of it change:
- **Re-entry point.** A conflict returns to step 1, not step 2.
- **Trigger.** The recipe runs every time. The trigger "if steps 3–5 produced new commits" is dropped.
- **Exit 1.** The step halts and the recipe is re-run in place. The gate does not restart, and the step does not pass unconfirmed.

### Adversarial review of the selected rule

1. **What the gate certifies, and why a clean trial merge needs no sync.** The strongest attack is that a clean textual merge can hide a semantic conflict. The attack is sharper in this repo: a base commit can add a `test_skills.py` pin against SKILL.md text that this branch rewrote in another hunk. That merges cleanly and still fails tests.
   - Step 1 syncs before step 2 runs, and the Overview's fix loop returns to step 2, not step 1 (row 27). So every gate run verifies and reviews a tree that holds the base as of that run's step-1 sync. On the CI-watch path, the tree holds the base as of the original run's step-1 sync, because that path re-enters at step 2 (row 22). The gate does not test base commits that land after that point.
   - A step-6 sync would move that point forward by the gate's own run time, and it has a cost. Step 2 re-runs, because the tree key changes (row 7). Steps 4 and 5 re-run, because they have no cache. Step 3 re-reviews only when the cumulative diff bytes change (row 44).
   - Sync-on-clean with a recheck after each sync has no fixed point, because the base can move again during each re-run (G1). That is the loop the Root names. A single sync with no recheck does have a fixed point, and it is the first rejected alternative below.
   - After handoff the base keeps moving, so no rule inside the gate can keep the tree current at merge. The gate does not catch drift after step 1. Whether a consumer's own merge-time or CI controls catch it is each repo's setting (G2), and the design doc enumerates none of them.
   - The design doc states no PR-run behavior (row 24). This argument rests on no CI, merge-ref, or branch-protection property (G2).
   - Neither the gain nor a gate's run time was measured.
   - Verdict: accept the residual. The design doc names it and states the detection points as above.
2. **Marker keying.** The push gate matches the completion marker to HEAD SHA (row 11).
   - In the clean case, step 6's check never changes HEAD, so step 7's marker matches what the push gate checks.
   - In the conflict case, step 7 runs only after step 1's sync, and L44 already makes the marker record the post-resync HEAD (row 5).
   - The marker has no base binding. The marker and the behind-count therefore certify base freshness only as of step 6.
   - The count reaches only the Completion summary, which the engineer who ran the gate reads. It is not a merge-time signal.
   - Verdict: no defect. The design doc records the limit.
3. **Re-entry at step 1, not step 2.** Step 1 syncs on any behind > 0 because it runs before step 2 pays for verification. Step 6 runs after verification.
   - Syncing inside step 6 and then re-entering at step 2 has two flaws. It restates L44's sync rule and post-resync marker rule in a second place (row 5). It also skips step 1's context-budget precondition (row 6) before a loop as costly as a step-2/3/4 fix loop.
   - Returning to step 1 fixes both without new prose.
   - Verdict: return to step 1.
4. **Overview L15/L17 and the convergence doc's "one way, not two".**
   - L15 covers fixes produced by steps 2–4. A conflict sync is not one of those, so step 6's own text routes it.
   - L17's step-6 exemption covers halts fixed inside step 6. The exit-1 re-run in place is one of those. The conflict case leaves step 6. Either reading of L17 produces the same agent behavior there, because step 1's budget check is unconditional text.
   - The convergence doc's L11 "one way, not two" concerns the loop-back wording's two readings of whether step 3 re-reviews a fix in full (row 33). The conflict route creates no second reading, because it passes through step 1 and then steps 2–3 in order.
   - This reading is for a future Overview editor, so it belongs in the design doc. SKILL.md deliberately leaves it out, because the file is at its line cap (G3).
   - M5's pin catches an edit that turns "return to step 1" back into step 2.
   - Verdict: L15 and L17 stay unedited. The design doc links the convergence doc and records the reading.
5. **The CI watch's "Land the fix" item stays as it is.**
   - It re-enters at step 2 under the Overview's fix-loop rule and never runs step 1 (row 22). When a branch is behind with a clean trial merge, step 6 now hands the CI fix off without a sync.
   - Where a repo's CI tests the PR merged with its base, a CI failure caused by base commits the branch lacks is not reproduced at step 2. The fix is then verified against a tree without those commits.
   - A conflict on this path returns to step 1, and step 1's sync then runs after handoff, possibly on a PR under review. The sync skill's own rule decides whether to rebase, merge, or stop and ask (row 43). This exposure covers only the conflict case. Restarting the gate from step 0 would extend it to every behind branch.
   - Verdict: L200, its pin and the convergence doc stay unchanged [engineer-verified: "Drop P2 (Recommended)"]. The design doc names the CI-watch residual.
6. **Can a conflict still reach step 6?** Yes, from two sources:
   - Base commits that land after this run's step-1 sync on lines this branch changed. This repo's shared `CHANGELOG.md` `[Unreleased]` section makes that plausible (row 23).
   - On the CI-watch path, base commits that landed since the original run's step-1 sync, because that path skips step 1 (row 22).
   - On git below 2.38 the conflict arm cannot fire. There, `git merge-tree --write-tree` fails and the script prints `Trial merge: CLEAN` (row 37). The README floor is git 2.31.
   - Verdict: the bullet stays.
7. **How far behind.** No threshold. A behind-count does not measure risk: one base commit can break the branch semantically, while fifty doc commits may not. Any cutoff would be an ungrounded number. `respond-pr` L24 already proceeds on CLEAN at any count (row 10).
8. **The old trigger is the wrong signal.** New branch commits do not make a branch behind; elapsed time does. The range is also wrong: L121 names fix commits from steps 2/3/4, and step 5 writes the PR body, not commits.
   - Running the recipe every time costs one fetch, bounded at 2 seconds, plus one trial merge. It also supplies the count the Completion line needs.
9. **Exit 1 at step 6.**
   - Step 1 runs the same script under "(halt on fail)". So when step 6 gets an exit 1 after step 1 passed, the bounded fetch is the likely cause. Every exit-1 path prints its cause on stderr (row 9).
   - Passing unconfirmed fails. Step 7's closed list (L133) does not name that outcome, so a strict reader withholds the marker, and no text names the recovery (row 36).
   - Adding the outcome to step 7's closed list also fails. It alters a second pinned clause (`test_skills.py` L6696-6703), and it counts a check that never ran as complete.
   - Restarting the gate fails too. Step 1 would sync a behind branch whose trial merge may be clean, which undoes the clean case.
   - Re-running the recipe once in place recovers a transient failure. A successful re-run ends in one of step 6's defined outcomes. If the re-run fails too, the bullet tells the agent to report the stderr cause to the engineer and stop, so step 6 stays halted and step 7 withholds the marker under L131 and L133 (row 36). The trigger is any non-zero exit, so a `set -e` abort is covered too.
   - The sentence sits on L123's physical line, so the file's line count does not change (G3).
   - Verdict: an exit 1 halts step 6, and the recipe is re-run in place.
10. **False CLEAN and stacked PRs.** Both are recorded as design-doc residuals and not fixed here.
    - When `git merge-tree` fails, the script prints `Trial merge: CLEAN`. That includes git below 2.38, against a README floor of 2.31 (row 37, item 6). Step 1 still syncs on any behind > 0, but step 6's clean case does not. A fix would change the script's output contract, which `respond-pr`'s precheck and this skill share. The SessionStart hook documents the same gap.
    - The script measures against origin/HEAD's default branch, not the PR's base (row 38). For a stacked PR the count is wrong. A spurious conflict returns to step 1, which syncs against the same wrong base it already uses.
11. **The conflict case has no cap.** Each conflict's resolution produces a new HEAD, and the push gate needs a completed gate on that HEAD (row 11). So the re-verification is owed under any rule.
    - A cap would add a human stop whose only answers are the same re-run or a deferral. Step 1's context-budget precondition already offers that deferral on every re-entry.
    - The promise that a moving base cannot force repeated re-verification therefore covers only the clean case (Root). The design doc and the CHANGELOG bullet say the same.

**Verdict.** Keep the selected rule ("Clean: report only; conflicts: halt"). Change three details of the session's description: a conflict returns to step 1, the recipe runs every time, and an exit 1 halts step 6 so the recipe can be re-run in place. Leave the CI watch's "Land the fix" item unchanged (row 28). None of these contradicts the engineer's label.

### Alternatives set aside

- **Sync once at step 6, with no recheck.** This has a fixed point, but every gate that ends behind pays for step 2 again (row 7), plus steps 4 and 5, plus step 3 when the diff bytes change (row 44). In return it cuts the stale window from the gate's run time to one step-2 re-run. After handoff the gate cannot close that window anyway (G1), and neither the gain nor the run time was measured. It also contradicts the selected "Clean: report only" (row 1).
- **Sync on clean, with a recheck after each sync.** Each re-run gives the base time to move again (G1), so this variant has no fixed point. That is the unbounded loop the Root names.
- **Sync on clean but skip re-verification.** This breaks the first leg of the cumulative-diff-cache doc's compensating control (row 8).
- **Delete the step-6 bullet and let step 1 own divergence.** A base commit that lands on this branch's lines after step 1's sync would reach handoff unseen. Deleting the bullet would also drop the engineer's selected "conflicts: halt".
- **Cap repeated step-6 conflicts.** Each new HEAD owes a re-verification under any rule, and step 1's budget precondition already bounds the loop (item 11).
- **Exit 1 passes unconfirmed and is reported.** Step 7's closed list does not name this outcome, so a strict reader withholds the marker, and no text names the recovery (row 36).
- **Add step 6's exit-1 outcome to step 7's closed list.** This alters a second pinned clause and counts a check that never ran as complete (item 9).
- **Exit 1 restarts the gate.** Step 1 would sync a behind branch whose trial merge may be clean (item 9).
- **Condition the clean case on a repo attestation, such as the verification-cache sentinel.** The justification in item 1 needs no CI, so there is nothing to attest.
- **Distinguish a failed `merge-tree` in the script now.** This changes an output contract that `respond-pr` shares (item 10), so it is a follow-up.
- **Put the behind-count in the PR body.** The body goes stale as the base moves, `pr-description` owns the body, and the count is transient.
- **CI watch: commit the fix and rerun the gate from step 0.** The engineer dropped this (row 28). The reasons the session showed are in row 29.
- **CI watch: invoke `/git-feature-branch-sync` inside the CI item before step 2.** This restates L44 (row 5) and skips step 1's budget check (row 6). It would also rewrite a handed-off PR on every behind branch.

### Assumption ledger

**Root.** `ready-for-review` step 6 names no action for a branch that is behind the base. An agent that syncs there on a clean trial merge changes the tree and re-runs step 2, and a base that keeps moving can force that again and again. The bound this plan sets covers only that clean case. A conflict has to be resolved before merge under any rule, and the new HEAD owes a re-verification.

**Givens.**
- G1. The base branch moves whenever other contributors merge, both during the gate and after handoff. Reason: other parties own those merges.
- G2. Each stow consumer's repo and its admin decide whether the repo has CI, what a PR run tests, and whether merge requires an up-to-date branch. Reason: another party owns these settings, and the skill installs to every stow consumer. The clean case's justification (Approach item 1, row 27) rests on none of them.
- G3. `ready-for-review/SKILL.md` stays under the 200-line cap. Reason: `docs/skills.md` L148 sets the cap repo-wide (`check-skill-length.sh`), and `ready-for-review-verification-cache.md` L43 already rejected a per-skill override. Changing the cap needs a decision outside this plan.

**Rows.** Numbering is kept so the plan diffs cleanly against the round-2 findings. Removed rows are marked in place.
1. [engineer-verified: "Clean: report only; conflicts: halt (Recommended)"] At step 6, a behind branch with a clean trial merge is not synced. A behind branch with conflicts halts.
2. [engineer-verified: "Have the architect do an adversarial review against it."] Approach records an adversarial review of row 1 and a verdict.
3. [unverified] "Noted in the Completion summary and the gate proceeds; conflicts halt, sync via git-feature-branch-sync, then re-enter at step 2" is the session's description of the option, not the engineer's words. It was shown beside row 1's label, so this plan reads that label's "halt" as halt-then-sync, not as a stop for the human. That reading is the plan's inference. This plan keeps the description's clean case and moves its re-entry point to step 1 (M2).
4. [verified: `ready-for-review/SKILL.md` L121, L123] L123 names no action for behind > 0. Its re-run trigger reads "if steps 3–5 produced new commits", while L121 names fix commits from "steps 2/3/4".
5. [verified: `ready-for-review/SKILL.md` L44] Step 1 syncs on any behind > 0 and re-runs step 2. It also owns the rule that step 7's marker records the post-resync HEAD SHA.
6. [verified: `ready-for-review/SKILL.md` L35, L44] Step 1's context-budget precondition comes before its sync bullet. Entering step 1 therefore runs the budget check before any re-sync or re-verification.
7. [verified: `docs/design-decisions/ready-for-review-verification-cache.md` L7; `ready-for-review/SKILL.md` L49] The `verification` marker is `HEAD^{tree}`. Any sync changes it, so step 2 re-runs.
8. [verified: `docs/design-decisions/ready-for-review-cumulative-diff-cache.md` L11] That doc's compensating control has two legs. First: "in a repo that has opted into the `verification` cache, step 2's verification is cached on the tree it executed, and a rebase always changes the tree, so it always re-runs verification there too." Second: "CI already runs on every push, independent of any cache." This plan adds no rebase path that skips step 2 and removes none, because a conflict reaches a rebase only through step 1, which re-runs step 2. The plan rests nothing on the second leg, which G2 leaves to each consumer, and the clean case adds no push.
9. [verified: `claude/.claude/scripts/branch-divergence-status.sh` L9-11, L20-23, L33-36, L46-49, L54-59, L61-83; `claude/.claude/hooks/_config.sh` L40-67] The script prints `Behind: N commit(s)`. When N > 0 it also prints `Trial merge: CLEAN` or `Trial merge: CONFLICT in: …`. It exits 1 in four cases: no git repository, origin/HEAD unresolved, no `timeout`/`gtimeout`, or a failed 2-second fetch. Each of those paths writes its cause to stderr before exiting. The bash-floor check it sources through `_lib.sh` also writes to stderr before failing. Step 1 runs the script first under "(halt on fail)", so an exit 1 at step 6 after step 1 passed points to the fetch.
10. [verified: `respond-pr/SKILL.md` L24] `respond-pr` proceeds on CLEAN when behind > 0 and blocks only on CONFLICT. Row 1 brings step 6 in line with it.
11. [verified: `require-ready-for-review.sh` L24-29, L399-414, per this session's exploration evidence and staff-platform-engineer round-2 R7; not reopened here] The push gate matches the completion marker to HEAD SHA and does not check a behind-count.
12. [verified: `git-feature-branch-sync/SKILL.md` L145] The sync skill force-pushes a rebased personal branch that has no stacked dependents and no in-flight review anchoring. Elsewhere it merges, or stops and asks. The conflict case reuses step 1's existing sync path and adds no new push path.
13. Removed: an unverified claim about GitHub's `pull_request` merge ref that no mechanism used.
14. Removed: an unverified claim about branch-protection strictness that no mechanism used.
15. [verified: `ready-for-review/SKILL.md` L15, L17; `test_skills.py` L5764-5771, L5813-5818] Both Overview sentences are pinned. Neither is edited (Approach item 4), so both pins keep passing.
16. [verified: `ready-for-review/SKILL.md` L131-133; `test_skills.py` L6692-6708] Step 7 withholds the marker when a step ends in an outcome its own text does not define as complete. The new bullet defines "behind with CLEAN" as passing, so step 7 and its pins need no edit.
17. [verified: repo grep for "Branch is not behind the base", "Branch: clean, pushed" and "steps 3–5 produced new commits"; `test_skills.py` L6638-6644, L6786-6792; `test_hook_alignment.py` L3283] No test or doc restates L123 or L171. Those strings match only SKILL.md and this plan.
    - `test_skills.py` L6638-6644 pins only step 6's PR-open-path bullet.
    - `test_skills.py` L6786-6792 asserts only the `session_id` sentence in Completion.
    - `test_hook_alignment.py` L3283 pins only the step-6 heading.
18. [verified: `test_skills.py` L5729, L5787, L5832, L5914, L5956, L6747] Six ready-for-review pin sites use `_raw_heading_section_text` with `_assert_pinned_clause_right_bounded` (L5434). The substring-token pin at L6638-6644 is the exception.
19. [verified: `ready-for-review/SKILL.md` L49 with L167, L68 with L168] Both cache-hit reports appear in their step's text and again in a Completion bullet. The behind-count report follows the same two-place pattern.
20. [verified: `README.md` L61; `.claude/rules/design-decisions.md`] `docs/design-decisions/` holds "one file per non-obvious design decision", found by slug or `git grep`. Four `ready-for-review-*.md` siblings exist.
21. [verified: `CHANGELOG.md` L5-7, L240; grep of README, CLAUDE.md, CONTRIBUTING.md, `docs/skills.md`] `[Unreleased]` → `### Changed` logs earlier `/ready-for-review` behavior changes. No rule or test requires an entry.
22. [verified: `ready-for-review/SKILL.md` L200] The CI watch's "Land the fix" item re-enters at step 2 under the Overview's fix-loop rule and never runs step 1.
23. [unverified] A step-6 conflict needs a base commit that lands after the last step-1 sync on lines this branch changed. How often that happens was not measured. In round 1 of plan-review, the platform reviewer reported heavy `CHANGELOG.md` churn on `main` in this repo's history (staff-platform-engineer R6; not re-derived here), which makes such a conflict plausible.
24. [verified: `.github/workflows/tests.yml` L11-14] This repo's workflow triggers are `push` to `main` and `pull_request`. The design doc states no PR-run behavior, and row 27's justification needs none.
25. [engineer-verified: "Design-decision doc (Recommended)"] The new design-decision file (M6) stays in this change. The engineer selected it from the options in plan-review Step 4's scope question. The slug `ready-for-review-base-drift.md` is the architect's choice.
26. [engineer-verified: "CHANGELOG bullet"] The CHANGELOG bullet (M7) stays in this change. The engineer selected it from the same question.
27. [verified: `ready-for-review/SKILL.md` L15, L44] Step 1 syncs before step 2. The Overview's fix loop returns to step 2, not step 1, so fix loops inside one gate run do not re-sync.
28. [engineer-verified: "Drop P2 (Recommended)"] The CI-watch change set the consult named P2 leaves this plan. The engineer selected this label for the question "Do you still want P2 in this change?". It supersedes their earlier answer, "Include P2", which no longer stands.
29. [unverified] The reasons the session showed with that question are the session's words, not the engineer's: the CI-watch restart can rebase and force-push a PR already handed off, it pushes the fix before step 2 verifies it, and it writes the fix against the pre-sync tree.
30. Removed: the Land-the-fix pin's names, which only the dropped CI-watch change edited.
31. [verified: `claude/.claude/agents/code-writer.md` L21, L66-69] `code-writer` does not commit. Unless the dispatch prompt directs it, it stops before altering a verbatim-pinned clause or pushing a file past its line cap.
32. Removed: the clean-tree precondition for a restarted CI item.
33. [verified: `ready-for-review-fix-loop-convergence.md` L11, L15; `.claude/plans/rfr-reentry-convergence.md` L9] The convergence doc's "The CI arm" paragraph routes a CI fix through the Overview's fix-loop rule. L11's "one way, not two" refers to the loop-back wording's two readings of whether step 3 re-reviews a fix in full.
34. Removed: the supersession rule for the dropped convergence-doc note.
35. Removed: the push order on a restarted CI path. Row 43 replaces it.
36. [verified: `ready-for-review/SKILL.md` L17, L131-133] Step 7 withholds the marker in two cases: a halt-on-fail step leaves a finding unresolved (step 6 is one of those steps), or a step ends in an outcome its own text does not define as complete. Step 7's closed list names no step-6 outcome. Because an exit 1 now halts step 6, a successful in-place re-run ends in one of step 6's defined outcomes, so the closed list needs no new entry. A halt that does not clear leaves step 6 incomplete, and step 7 withholds the marker. L17 keeps a step-6 halt outside the fix-loop routing, which matches a re-run in place.
37. [verified: `branch-divergence-status.sh` L7, L73-83; `check-branch-divergence.sh` L24-31; `README.md` L97] A failed `git merge-tree` prints `Trial merge: CLEAN`. That includes git below 2.38, which rejects `--write-tree`, while the README's floor is git 2.31. On those versions step 6's conflict arm cannot fire. The SessionStart hook's header accepts the gap because later checkpoints gate; this change removes step 6 from those checkpoints on those versions, and the design doc and CHANGELOG bullet say so. Step 1 still syncs on any behind > 0.
38. [verified: `branch-divergence-status.sh` L25-36, L61] The script measures against origin/HEAD's default branch, not the PR's `baseRefName`.
39. [verified: `test_branch_divergence_status.py` L73, L93, per staff-sdet round-1 R5; not reopened] The script's own test asserts only `CLEAN` and `CONFLICT` substrings, not the `Trial merge:` lines the new bullet branches on.
40. [verified: `test_skills.py` L5409-5419, L5443-5447, L5920-5928] `_heading_section_text` returns the section whitespace-normalized. The match-is-None arm is the assertion at L5445-5447. Its message carries `pinned:` and `section:`, and it embeds the section as a `repr` (L5446). L5920-5928 is the precedent for a negative-space assertion beside a clause pin.
41. [verified: `claude/.claude/scripts/select-tests.py` L827] Every path under `docs/` maps to the hooks and skills test dirs, so `select-tests.py` selects `test_design_decision_files.py` for the new file.
42. [verified: `ready-for-review-fix-loop-convergence.md` L39] There is precedent for recording a Completion restatement as deliberately unpinned.
43. [verified: `ready-for-review/SKILL.md` L44, L176, L200; `git-feature-branch-sync/SKILL.md` L123, L145] The CI watch resolves after the gate finishes, possibly hours later, and its fix re-enters at step 2. A step-6 conflict on that path returns to step 1, whose sync then runs on a PR already handed off. The sync skill rebases and force-pushes only a personal branch with no stacked dependents and no in-flight review anchoring. Elsewhere it merges, or stops and asks, and its pre-flight checklist rules out a rebase while SHA-anchored review is in flight.
44. [verified: `ready-for-review/SKILL.md` L68, L88-104, L106-111; `ready-for-review-cumulative-diff-cache.md` L7, L9] After a sync, step 3 reuses its clean review when the cumulative diff is byte-identical, because its marker hashes the diff bytes and excludes the merge-base. Step 4 has no cache, and step 5 is "Never skipped". A sync's re-run therefore costs step 2 (row 7) and steps 4 and 5, plus step 3 when the diff bytes change.

**Mechanisms.**
- M1. Replace L123 in place with one bullet. The bullet runs the recipe every time. Behind with CLEAN passes with no sync and reports the count. Behind with CONFLICT halts and returns to step 1. An exit 1 halts, and the recipe is re-run in place. anchors: root, row1, row4, row7, row9, row10, row27, row36, G3.
  - Two lighter exit-1 options fail:
    - Passing unconfirmed leaves the outcome outside step 7's closed list (row 36).
    - Adding the outcome to that list alters a second pinned clause (row 31) and counts a check that never ran as complete.
- M2. A conflict returns to step 1, not step 2. anchors: row3, row5, row6, row11, row12, row15, row43.
  - Two lighter options fail:
    - Syncing inside step 6 and re-entering at step 2 restates row 5's rule and skips row 6's budget check.
    - Halting for the human with no sync adds an interrupt that step 1's own sync path (row 12) does not need.
- M3. Drop the "steps 3–5 produced new commits" trigger. anchors: row4, row9, row23.
- M4. Replace Completion L171 in place so it names the behind-count. anchors: row9, row19, G3.
- M5. Add one step-6 test. It first asserts a right-bounded clause pin on the whole bullet. It then checks that the section, with that clause removed, carries no sync instruction. anchors: row17, row18, row40.
  - Two lighter options fail:
    - A bare clause pin misses a new sibling bullet that re-fills the gap.
    - Token pins in the style of L6638 would break the pattern of the six neighbouring pins (row 18), and a trim at the line cap could reword the rule around them.
  - The docstring states that the whole-bullet pin is deliberate, and that the negative half is a lexical tripwire.
- M6. Add a new design-decision file. anchors: row15, row20, row22, row24, row25, row27, row33, row37, row38, row39, row42, row43, row44, G2.
  - Two lighter homes fail:
    - The committed plan file is not where a future step-6 editor looks (row 20).
    - SKILL.md has no line budget (G3).
- M7. Add one CHANGELOG bullet. anchors: row21, row26.
  - The PR body alone fails, because it does not reach a stow consumer who reads the changelog on pull.
- M8–M10. Removed with the CI-watch change (row 28).

## Critical files

One `code-writer` dispatch (`model: sonnet`) covers files 1–4. Splitting fails for two reasons. The pin must match the SKILL.md text byte for byte. The design doc and the CHANGELOG bullet describe the same clauses, so every dispatch would need the same context.

The dispatch prompt must carry the following:
- **No pin alteration.** L123 and L171 are unpinned today (row 17). `code-writer.md` L66-69's stop stays in force for any other pinned clause and for any change to SKILL.md's 200-line count.
- **Commits.** `code-writer` does not commit. The parent lands one commit holding the test, both SKILL.md edits, the design doc and the CHANGELOG bullet, so the pin and the text it pins land together.
- **Rollback.** One `git revert` of that commit undoes the whole change. No state, marker format, or script output changes, so nothing needs migrating.
- **Order.**
  1. Write the test and confirm the red run (Verification 1). The red run is expected: it is the evidence the pin discriminates, so the dispatch prompt tells `code-writer` not to fix the code on it.
  2. Edit the two SKILL.md lines and confirm the green run.
  3. Write the docs, then return. `code-writer` does not run Verification 2: it would push SKILL.md to 201 lines, colliding with the line-cap stop. The parent runs Verification 2 after the return, and no hook gates a transient 201st line before a commit.

1. `claude-skills/skills/ready-for-review/SKILL.md`. Two in-place replacements of one physical line each, net zero lines.
   - L123 becomes exactly:
     ```markdown
     - Branch has no merge conflict with the base branch. Re-run the divergence detection recipe (`git-feature-branch-sync/SKILL.md` § "Detecting divergence"). Behind with `Trial merge: CLEAN` passes: report the behind-count in the Completion summary. Do not sync here: a sync changes the tree, so step 2 would re-run. Behind with `Trial merge: CONFLICT` halts this step: return to step 1, whose in-sync precondition runs the sync. A recipe failure (non-zero exit) halts this step: re-run the recipe here once rather than restarting the gate, whose step 1 would sync; if it fails again, report its stderr cause to the engineer and stop.
     ```
   - L171 becomes exactly:
     ```markdown
     - Branch: clean, pushed, PR #N ready for review. Add step 6's behind-count when it found the branch behind with a clean trial merge.
     ```
   - L15, L17, L44, L118, step 7 and L200 stay untouched. Keep the file's trailing newline.
2. `claude-skills/skills/tests/test_skills.py`. Insert the following immediately before `_READY_FOR_REVIEW_STEP7_HEADING` (L6686). Nothing else in the file changes.
   - `_READY_FOR_REVIEW_STEP6_HEADING = "## 6. Final hygiene recheck (halt on fail)"`.
   - `_PINNED_STEP6_BEHIND_BASE_CLAUSE`: the L123 text above without its leading `- `. It includes the exit-1 sentence. Its one-line comment reads: `# Step 6's behind-base bullet: no sync on a clean trial merge, a conflict returns the gate to step 1, and a recipe failure is re-run once in place, then reported.`
   - `class TestReadyForReviewStep6BehindBaseRule` with one test, `test_step6_behind_base_bullet_is_the_only_sync_rule`. It reuses `_skill_file` (L100), `_raw_heading_section_text` (L5422), `_heading_section_text` (L5409) and `_assert_pinned_clause_right_bounded` (L5434), in two steps:
     1. Assert the whitespace-normalized clause right-bounded against `_raw_heading_section_text`. The right edge lands on the blank line before "With a PR open, launch the CI watch now".
     2. Take `_heading_section_text` for the same heading and remove the normalized pinned text once. Assert first that the remainder does not contain `git-feature-branch-sync`, with a failure message naming that token. Then assert that it has no `re.search(r"\bsync", remainder, re.IGNORECASE)` match, with a failure message naming the matched text and about 30 characters of context on each side.
   - Both checks stay in one test because the negative check depends on the positive match. A separate negative test would fail spuriously in the red phase.
   - The class docstring reads, in substance: "Pin step 6's behind-base bullet as one whole clause, deliberately, and check that no other step-6 text says "sync". That second check is a lexical tripwire on the skill's own vocabulary ("sync" and the sync skill's name), not proof that no step-6 text moves the branch. Fails when step 6 syncs on a clean trial merge, stops returning a conflict to step 1, or stops halting on a recipe failure."
3. `docs/design-decisions/ready-for-review-base-drift.md` (new).
   - **Format.** The H1 is `` # `/ready-for-review` and a base that moves during the gate ``. Then comes a blank line and the provenance line `*2026-10-21.*`, with no `Formerly` clause, per `.claude/rules/design-decisions.md`.
   - **Citations and wording.**
     - Cite steps rather than restate them, each citation on one line per `.claude/rules/citation-grammar.md`. Use these headings exactly:
       - `` `ready-for-review/SKILL.md` § "6. Final hygiene recheck (halt on fail)" ``
       - `` `ready-for-review/SKILL.md` § "7. Record gate completion" ``
       - `` `ready-for-review/SKILL.md` § "CI watch (out-of-band)" ``
       - `` `git-feature-branch-sync/SKILL.md` § "Rule of thumb" ``
     - Use no plan-local labels (no P-numbers, M-numbers, or row numbers) and no "used to be" framing (CLAUDE.md §Durable text).
     - Assert no GitHub behavior. State each consumer-dependent point conditionally ("where a repo has one"). Never write "in general". Say "is not caught by the gate" instead of naming where a miss surfaces.
   - **Sections.** Each records reasons only.
     - **What the gate certifies about the base.** The verified tree holds the base as of the run's step-1 sync, because fix loops return to step 2. On the CI-watch path it holds the base as of the original run's step-1 sync, because a CI fix re-enters at step 2. Nothing in the gate keeps the tree current after handoff.
     - **Why a clean trial merge is reported, not synced.**
       - A sync there gains only the gate's run time.
       - Its cost: step 2 re-runs, because its key is the tree; link `ready-for-review-verification-cache.md`. Step 3 reuses its clean review when the cumulative diff is byte-identical; link `ready-for-review-cumulative-diff-cache.md`. Steps 4 and 5 re-run.
       - Rechecking after each sync has no fixed point while the base moves. A single sync with no recheck has one, but pays the same cost for the same small gain.
       - When step 2's scope exception or the caches make the re-run cheap, the gain is still only the run time, so those cases get no carve-out.
       - The justification needs no CI or branch protection.
       - The clean case matches `respond-pr`'s precheck.
     - **What the gate does not catch after step 1.**
       - The gate does not catch drift after step 1. Whether a consumer's own merge-time or CI controls catch it is each repo's setting; the doc enumerates none of them.
       - The clean case pushes nothing.
       - The completion marker binds HEAD only. With the behind-count it certifies no textual conflict as of step 6 and a stated distance from the base, not freshness.
       - The count reaches only the Completion summary. The Completion line is deliberately unpinned: the step-6 text is pinned, and Completion restates it so the report stands alone. Link `ready-for-review-fix-loop-convergence.md` for that precedent.
     - **Why a conflict returns to step 1.**
       - Step 1 stays the one home for the sync rule and the post-resync marker rule, and its context-budget precondition runs first.
       - There is no cap: each conflict's new HEAD needs a completed gate anyway, and the budget precondition is the bound.
       - The moving-base bound covers only the clean case.
       - The Overview's "A halt on step 6 stays outside this routing" covers halts fixed inside step 6.
       - Link `ready-for-review-fix-loop-convergence.md`. Say why the conflict route creates no second reading of its "one way, not two": the route passes through step 1 and then steps 2–3 in order.
       - On the CI-watch path, which runs after handoff, step 1's sync can rewrite a PR under review. The sync skill's "Rule of thumb" decides whether to rebase, merge, or stop and ask.
     - **Why a failed check at step 6 halts and is re-run in place.**
       - Step 1 already ran the same recipe under halt-on-fail, so the fetch is the likely cause, and the script names its cause on stderr.
       - A re-run in place ends in one of the step's defined outcomes. A second failure is reported to the engineer, who acts on the named cause.
       - The in-place re-run exists only while the session lives. A fresh session restarts at step 0, where step 1 syncs a behind branch.
       - Restarting the gate would let step 1 sync a behind branch whose merge is clean.
       - Passing unchecked would leave the outcome outside step 7's list of outcomes complete without full execution.
     - **Residuals.**
       - A semantic conflict in a clean merge after step 1 is not caught by the gate.
       - A false `Trial merge: CLEAN` when `git merge-tree` fails, including on git below 2.38 against the README floor of 2.31. On those versions step 6's conflict arm cannot fire, so a conflicting branch is handed off. This change removes step 6 from the checkpoints that the SessionStart hook's header counts as gating that gap. The fix is a follow-up, because `respond-pr` shares the output contract.
       - For stacked PRs, the script measures against origin/HEAD's default branch, so the count and the conflict verdict are unreliable.
       - The bullet branches on the full `Trial merge:` literals, which the script's own test pins only by substring.
       - On the CI-watch path, where a repo's CI tests the PR merged with its base, a failure caused by base commits the branch lacks is not reproduced at step 2. Step 6's clean case then hands the fix off without those commits.
     - **Rejected alternatives.**
       - A threshold.
       - A conflict cap.
       - A single sync with no recheck.
       - Sync without re-verification.
       - Deleting the step-6 check.
       - The count in the PR body.
       - Exit 1 passing unchecked.
       - Exit 1 restarting the gate.
       - Conditioning the clean case on a CI attestation.
       - A CI fix rerunning the gate from step 0. On a personal branch, step 1's sync would rebase and force-push a PR already handed off, before step 2 verifies the fix (cite the sync skill's "Rule of thumb").
     - **Sources.** `ready-for-review/SKILL.md`, `respond-pr/SKILL.md`, `git-feature-branch-sync/SKILL.md`, `claude/.claude/scripts/branch-divergence-status.sh`, `claude/.claude/hooks/check-branch-divergence.sh`, `claude/.claude/hooks/require-ready-for-review.sh`, and this plan's path.
4. `CHANGELOG.md`. Add one bullet under `## [Unreleased]` → `### Changed` (L5-7). Check `git log -p CHANGELOG.md` to see whether recent entries go at the top or the bottom of that section, and place it the same way. Use this text:
   ```markdown
   - **`/ready-for-review` step 6 now hands off a branch that is behind the base without syncing it when its trial merge is clean.** It reports the behind-count in the Completion summary instead, so a base that keeps moving cannot force step 2's verification to re-run on a clean merge. The handed-off tree therefore holds the base only as of step 1's most recent check. A trial-merge conflict at step 6 returns the gate to step 1, which syncs before verification runs again. On git below 2.38 the trial-merge verdict is always clean, so step 6 there reports the count and never returns a conflict. On the CI-watch path, step 1's sync can rebase and force-push a pull request already handed off. A failed divergence check at step 6 halts the step: the check is re-run once in place, and a second failure is reported to the engineer.
   ```
5. `.claude/plans/ready-for-review-step6-sync-bound.md`. Committed at plan-it Step 7; no implementation edit.

## Verification

1. **Red before green.** After the test change and before the SKILL.md edit, run `scripts/venv-run.sh pytest claude-skills/skills/tests/test_skills.py -k Step6BehindBaseRule -v`.
   - A run counts as red only when all of the following hold:
     - It collects exactly one test and exits 1.
     - The failure is the `AssertionError` from `_assert_pinned_clause_right_bounded`'s match-is-None arm (`test_skills.py` L5445-5447), with a message that carries `pinned:` and `section:`.
     - The `section:` payload contains "Branch is not behind the base branch". That fragment has no apostrophe, quote, or non-ASCII character, so the section `repr` (L5446) prints it unescaped.
   - A run that collects zero tests (exit 5), a collection error, a `NameError`, or any other failure is not a valid red.
   - After the SKILL.md edit, the same command must pass with one test.
2. **Discriminating run for the negative half, on the final SKILL.md. The parent runs it, after `code-writer` returns.** Inject each line below in turn using an exact one-line Edit, and re-run the Verification 1 command after each injection.
   - `- Sync with the base branch when it is behind.`, as a new line immediately after the pinned bullet and before the blank line that follows it. This is the position a later editor is most likely to use, and it is the position the right-bound hands to the negative half. The test must fail with a message that names the matched text `Sync`.
   - ``- If behind, run `/git-feature-branch-sync`.``, as a new line immediately before the pinned bullet. The test must fail with a message that names `git-feature-branch-sync`.

   Remove each injected line with an exact Edit before the next injection. Never `git checkout` or `git restore` the file, because either would discard the uncommitted edits. After both injections, the test passes again and `wc -l claude-skills/skills/ready-for-review/SKILL.md` prints `200`.
3. **Line count.** `wc -l claude-skills/skills/ready-for-review/SKILL.md` prints `200`.
4. **Scoped suite.** Run `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py -v`. Its `-v` output should list:
   - the `claude-skills` tests, which keep the L15/L17, step-1, step-3, step-7, CI-watch and new step-6 pins green;
   - `claude/.claude/hooks/tests/test_design_decision_files.py`, selected through `select-tests.py` L827. It checks the new file's filename grammar, H1, provenance line and relative links.

   If either is missing, report it as a `select-tests.py` rule-table bug. Do not widen the run to the full suite.
5. **Lint.** `scripts/venv-run.sh ruff check claude-skills/skills/tests/test_skills.py`.
6. **Review.** The commit goes through `/code-review`, which dispatches the hook-enforced `/skill-review` for the SKILL.md diff (`.claude/rules/skill-and-agent-self-review.md`).

## Out of scope

- **Step 1's sync rule and its exit-1 halt (L44).** Both stay as they are. Syncing before step 2 costs one verification either way, and step 1's halt is why an exit 1 at step 6 points to the fetch.
- **Step 7 (L131-133) and Overview L15/L17.** Neither is edited. Rows 16 and 36 and Approach item 4 record the readings.
- **The CI watch's "Land the fix" item (L200), its pin, and `ready-for-review-fix-loop-convergence.md`.** All three stay unchanged [engineer-verified: "Drop P2 (Recommended)"]. The design doc names the CI-watch residual and only links the convergence doc.
- **`branch-divergence-status.sh` changes.** Two follow-ups: distinguishing a failed `merge-tree` from CLEAN, and measuring against the PR's base instead of origin/HEAD. Both change an output contract that `respond-pr`'s precheck shares. The design doc names them as residuals.
- **Exact-line assertions in `claude/.claude/scripts/tests/test_branch_divergence_status.py`.** These are left out and recorded as a named residual (row 39). Adding them would need the engineer's answer before the file joins this change's file limit. The recommendation is to leave them out.
- **Merge-time freshness controls.** Whether merge requires an up-to-date branch is each consumer repo's own setting (G2).
- **L118's lead-in "Steps 3–5 may have produced new commits or body writes".** It has the same range slip as the old trigger, since L121 names steps 2/3/4. It is not edited, because this change's line edits are limited to L123 and L171. Raise it to the reviewer.
- **Stale step numbers in `ready-for-review-fix-loop-convergence.md`.** Examples are L11's "step 8's deactivation" and L15's "step 2–8 sequence". This change only links that file. Raise it to the reviewer.

**Not verified:**
- How often a conflict reaches step 6 (row 23).
- The gain from a step-6 sync and a gate's run time (Approach item 1).
- Row 3's reading of the label's "halt" as halt-then-sync.
- What the recipe does on an exit code other than 0 or 1. A `set -e` abort, for example at L61's `git rev-list`, would exit with that command's own status. This is inferred from `set -euo pipefail` at L13 and was not run. The bullet's trigger is any non-zero exit, so that case gets the same re-run and report.
- The `require-ready-for-review.sh` line ranges (row 11) and `test_branch_divergence_status.py` L73/L93 (row 39). Both come from handed-over evidence and were not reopened.
