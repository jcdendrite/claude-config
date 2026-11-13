# Where the parent's rule for a code-writer's Still uncertain entries lives

## Context

Goal: decide, with the alternatives weighed, where the rule that a parent session acts on a `code-writer` return's **Still uncertain** entries belongs, and what to do about the no-op-dispatch paragraph that PR #1453 deleted from `subagent-delegation/SKILL.md`.

Ask: the engineer's words on PR #1453 were "Why did you remove this??! This is important. Maybe you had a good reason. If so, explain." and "Doesn't \"still uncertain\" only apply when code-writer is dispatch? And isn't code-writer only dispatched from the code-review skill? If that's all true, then why not put this instruction in code-review?" In this session they said "I actually think this really needed a planet process to consider alternatives", "I'm not sure" where the rule belongs, "subagent delegation seems like the wrong place because it's general", "I'm a little hesitant to remove it, even though it's redundant", and "I think I'll want this to be handled with the planet process" (the engineer's wording for `plan-it`). The engineer accepted that `code-writer` is spawned from several places: "fair point about the fact that CodeWriter is spawned from multiple places. I did not realize that."

Why now: PR #1453 is open and ready, not merged. It was authored without a plan, so the placement and the deletion were never put to the engineer as choices.

What #1453 does today, in three files:
- `claude-skills/skills/subagent-delegation/SKILL.md`, § "Implementation work → `code-writer`": adds a paragraph telling the parent to fix a **Still uncertain** entry or record it as an ADDRESS row, never to forward it to the next reviewer as a known leftover. It also deletes the "A dispatch must return something the parent does not already have" paragraph.
- `claude/.claude/agents/review-orchestrator.md` step 6 and the `Not addressed:` return item: relays each **Still uncertain** entry verbatim to the dispatcher.
- `claude/.claude/agents/code-writer.md`, **Still uncertain** bullet: a check that covered only part of a claim is reported there, even when the dispatch adds a separate verification field.

Evidence gathered in exploration, to be re-verified by whoever relies on it:
- The dispatch sites, the file-length ratchet and the no-op paragraph's recorded status are verified in the Approach ledger (rows 5-6 and 11-17). Only `subagent-delegation` and `review-orchestrator` mention **Still uncertain** today.
- `claude/.claude/hooks/evaluate-code-writer-self-review.py` already parses the writer's **Still uncertain** label, so a mechanical check on the return already exists in the repo.
- Across skills and agent files, the usual home for how a parent handles a subagent's return is the dispatching or general skill, with about seven sites, rather than the dispatched agent's own file, with two or three.

Intended outcome: one reviewed placement decision with alternatives and reasons, a decision on the deleted paragraph, and a decision on what happens to #1453. Out of scope: the #1211 issue body edit, which needs the engineer's yes in the session, and merging.

## Approach

The design makes four decisions:
- Rule home: `subagent-delegation/SKILL.md` § "Implementation work → `code-writer`" (rows 4 and 28).
- No-op-dispatch paragraph: byte-identical to `main` (row 10).
- `review-orchestrator` path: the dispatcher acts on relayed **Still uncertain** entries at the head of step 4's `no-match` bullet (rows 20 and 36).
- Uncovered dispatch sites: `respond-pr` and `ready-for-review`'s CI fix cite the rule (rows 6-7).

The change goes on a new branch from `origin/main`, as the engineer selected (row 29), named `still-uncertain-parent-rule` (row 30). For why not #1453's branch, see rows 14-16.

**Why this file.** The engineer selected `subagent-delegation` (row 28) against their earlier lean (row 2). That concern holds for Step 1, which states the general delegation tests. Step 2 is the per-type layer: four of its six `###` headings name a subagent type, and the sixth section carries the `review-orchestrator` return protocol (row 5). The code-writer section already says how the parent reviews a returned dispatch (row 4), so the rule joins an existing home instead of opening a second one.

**Alternatives set aside.**
- `code-review`, the engineer's first suggestion. It would reach every path, because every fix commit passes through `/code-review`, and the file has room (536 lines of a temporary 550-line cap). It loses on ownership and on one path it cannot reach:
  - `code-review` already hands the dispatch and everything after it to the code-writer section (`code-review/SKILL.md:328,356`; "The parent keeps the commit, the `/code-review` re-run, and the marker", `subagent-delegation/SKILL.md` on `main`, `:170-171`). The rule there would make a second home for how a parent handles a `code-writer` return.
  - Under `review-orchestrator`, the next round's `code-review` runs in a fresh agent that never saw the previous writer's return.
  - The suggestion rested on `code-writer` having one dispatch site, which the engineer has since set aside (row 3).
  - The cap above 500 is a stopgap until #1351 restructures the file (`check-skill-length.sh:132-134`).
- `code-writer.md` Return format. No parent reads it. A dispatcher sees only an agent's description (G1), and a return's text is data to its parent, not an instruction (`claude/.claude/CLAUDE.md:10`).
- `code-writer`'s `description`, the one writer surface every dispatcher sees. It is loaded in every session's agent listing, and its job is routing. `review-orchestrator.md:3`'s pointer into `subagent-delegation` is the precedent. But the dispatch sites are a closed set, and per-site citations reach them at no always-loaded cost (row 7).
- A copy in each dispatching skill. That is seven copies with no named exception, since every site can cite one home (CLAUDE.md §Engineering Judgment, "Single source of truth"). `ready-for-review` and `handoff` are already at their 200-line cap.
- A mechanical check extending `evaluate-code-writer-self-review.py`. The evaluator reads the writer's own transcript before the return reaches the parent (`:2-6`). It can check what the writer reported, not what the parent did next. A parent-side hook would mean a new hook, test, `docs/hooks.md` entry and settings registration for one observed failure. The no-op rule escalated to a hook only after its prose rule recurred (`docs/design-decisions/no-op-dispatch-hook-gate.md:5`).
- `claude/.claude/CLAUDE.md`. It loads on every turn. The no-op rule moved there only on evidence that the skill body had not reached the session (`docs/design-decisions/no-op-dispatch-guard.md:7`), and no such evidence exists here (row 9).
- Dropping the `review-orchestrator` relay, back to an earlier plan's accepted gap (row 23). Entries on that path would be dropped again, which is the failure the rule names.
- Sending relayed entries through the `HALT:` arm's inline takeover. An earlier engineer-approved plan dropped a halting relay for exactly that cost (row 20).
- Alternative B, from the architect's adversarial review (reviewer output): `review-orchestrator` re-dispatches `code-writer` itself and halts on any entry that survives. Fable's design review (reviewer output) set it aside for three reasons. Each is confirmed at its cited lines, and the engineer's choice followed (rows 36-37):
  - It sends an entry for a claim the writer left unwritten to `HALT:` every time, because a second dispatch gets the same disagreeing sources. On `no-match`, any `HALT:` entry moves the rest of the review loop inline (`subagent-delegation/SKILL.md:201`). That is the cost an earlier plan named when it dropped a halting relay (`.claude/plans/code-writer-verify-dictated-claims.md:40`).
  - It puts a second `code-writer` dispatch inside one orchestrator round, against "Dispatch one `code-writer` per round" (`subagent-delegation/SKILL.md:169`) and item 6's "once" (`review-orchestrator.md:22-24`). E2's dispatch falls between rounds instead, and the next round reviews it.
  - Only the dispatcher can ask the user without ending the round. The agent's one route to a human is `HALT:` (`review-orchestrator.md` item 8).
- Alternative C: the orchestrator re-dispatches only for an entry naming another site that states a changed claim, and relays the rest. Set aside for now, because it keeps B's second in-round dispatch for that kind and makes the agent sort entries by kind. Revisit if dogfood shows those entries dominate.
- New commits on #1453's branch. The length ratchet compares against #1453's shorter `HEAD`, so restoring the paragraph is denied unless two more lines of `main`'s content go first (rows 15-16). No same-file restatement frees two whole lines, and `docs/skills.md:171` licenses no other cut.
- A force-pushed rewrite of #1453's branch: reset it to `origin/main`, re-apply the same hunks there, and keep #1453's PR and comment thread. The engineer did not pick it (row 29), and it rewrites the history of a branch under an open PR.

**Edits.**
- E1, `subagent-delegation/SKILL.md`, code-writer section. Prepend #1453's rule to the paragraph that begins "A finding surviving a second dispatch stops being delegated". Keep it on that paragraph's single line. Change "the return's" to "a return's" (row 18). Reword the trigger (rows 39-41). The prepended text reads: "When a return's **Still uncertain** names a claim the writer left unwritten, or another site that states a claim it changed, act on that entry before the next review: have it fixed, or record it as an ADDRESS row. Never drop the entry. Forwarding it to the next reviewer as a known leftover does not count."
- E2, same file, step 4's `no-match` bullet in § "Exception: gate/review loops stay orchestrator-driven" (`:201`), a single line holding the whole bullet. Insert the text below, then one space, between "`no-match`: " and "with any `HALT:` entry", leaving the rest of the bullet unchanged: "First act on each **Still uncertain** entry under `Not addressed:` that `subagent-delegation/SKILL.md` § "Implementation work → `code-writer`" requires acting on. Send every one a fix can settle to one `code-writer` dispatch of your own, then stage the paths its return names as step 2 stages `Fix paths:`. Ask the user now about each one no fix settles, since an ADDRESS row you log here reaches no later round. Then,"
- E3, `review-orchestrator.md`. Port #1453's item 6 and item 9 text. Then point item 6's dispatcher clause at `subagent-delegation/SKILL.md` § "Exception: gate/review loops stay orchestrator-driven", the target item 9's `HALT:` line already uses.
- E4, `code-writer.md`. Port #1453's **Still uncertain** bullet unchanged (row 8).
- E5, `respond-pr/SKILL.md` step 6. Append to the line "The parent keeps the commit (step 8) and every reply (step 7).": "Before step 8, it acts on the return's **Still uncertain** entries per `subagent-delegation/SKILL.md` § "Implementation work → `code-writer`"."
- E6, `ready-for-review/SKILL.md`, the CI section's step 4, "**Offer, don't act.**" (`:199`). Append: "Before landing the fix below, act on that dispatch's **Still uncertain** entries per `subagent-delegation/SKILL.md` § "Implementation work → `code-writer`"." Never append to `:200` (row 7).
- E7, `claude-skills/skills/tests/test_skills.py:9531`. Insert `"Not addressed:"` before `"HALT:"` in `_REVIEW_ORCHESTRATOR_RETURN_ITEMS`, so the tuple and E8's list share one order.
- E8, `docs/design-decisions/review-orchestrator-gate-release-decoupling.md:34`. Change "`Fix paths:`, `Marker:` and `HALT:`" to "`Fix paths:`, `Marker:`, `Not addressed:` and `HALT:`", which puts `Not addressed:` before `HALT:` as `review-orchestrator.md` item 9 orders them (row 38).

**Sequence.** These steps are the session's own, in order:
1. Before `/plan-review`, `branch-management` creates `still-uncertain-parent-rule` from `origin/main` in its own worktree (row 30). This file moves to that worktree's `.claude/plans/still-uncertain-parent-rule.md`, and the session deletes it at its current path in #1453's worktree (row 31).
2. `/plan-review` runs on the moved file.
3. Phases 0 and 1 in Critical files, then `/code-review`, the commit, `/ready-for-review` and the new PR, per `claude/.claude/CLAUDE.md` § "Shipping".
4. After the new PR opens, reply to the engineer's two #1453 comments with its link and the reasons. Rows 10-13 answer the comment on the removed paragraph. Rows 3-4 and the `code-review` alternative above answer the comment proposing `code-review`.

The #1453 actions row 32 lists need the engineer's yes, and none is authorized. Until the engineer decides, that branch stays as #1453's branch.

**Assumption ledger.**

**Root.** A `code-writer` return's **Still uncertain** entries need a rule, kept in one home, that reaches whichever parent receives the return. #1453 made room for that rule by deleting a paragraph the repo keeps on purpose.

**Givens.**
- G1. A dispatcher sees a subagent's `description`, never its body, which is the subagent's own prompt. Reason: a harness property of agent definitions.

**Rows.**
1. The engineer had not settled where the rule belongs when this plan was first drafted. [engineer-verified: "I'm not sure"] The quote answered where the rule belongs. `anchors: root`
2. The engineer's earlier lean was away from `subagent-delegation`. [engineer-verified: "subagent delegation seems like the wrong place because it's general"] Row 28's selection went against this lean. This row claims no change in the engineer's view beyond that selection. `anchors: root`
3. `code-writer` has more than one dispatch site. [engineer-verified: "fair point about the fact that CodeWriter is spawned from multiple places. I did not realize that."] It answers the premise of the engineer's earlier question "isn't code-writer only dispatched from the code-review skill?". `anchors: root`
4. Mechanism: within the selected file, the rule's one home is § "Implementation work → `code-writer`". That section already defines the parent's review of a returned dispatch (`subagent-delegation/SKILL.md` on `main`, `:153-160`). `review-orchestrator.md:3` sends its dispatcher to this file for how to "act on each return". [verified] `anchors: row28`
5. On `main`, Step 2's `###` headings sit at `:80`, `:101`, `:118`, `:135`, `:178` and `:183`. Four name a subagent type (`:101`, `:118`, `:135`, `:178`), and the `:183` section carries the `review-orchestrator` return protocol (`:195`). E1 and E2 add no line, so these numbers hold after the edits. [verified: primary checkout's copy, see row 15] `anchors: row4`
6. Every dispatch site except two already names `subagent-delegation` or its code-writer section: `claude/.claude/CLAUDE.md:165`, `code-review/SKILL.md:328,356,359`, `ready-for-review/SKILL.md:91`, `plan-it/SKILL.md:99,181`, `handoff/SKILL.md:103` and `review-orchestrator.md` item 6. The exceptions are `respond-pr` step 6 (`respond-pr/SKILL.md:58-64`) and `ready-for-review`'s CI-fix step (`:199`). [verified: Grep for `code-writer` and `subagent-delegation` in each file] `anchors: row4`
7. Mechanism: E5 and E6 cite the section from the two uncovered sites.
    - Line counts: each appends to a line that already holds a whole paragraph, so neither file gains a line. `respond-pr` is 138 lines, and `ready-for-review` stays at its 200-line limit. [verified: `wc -l` of both]
    - E6 wording: E6 names its moment as "landing the fix below" because a bare "step 5" in `ready-for-review` reads as a gate step, the way `:200` cites steps 0, 2 and 9.
    - E6 placement: E6 joins `:199`, not `:200`, because `test_skills.py:7018-7042` requires `:200`'s pinned clause to end at a structural boundary (`_assert_pinned_clause_right_bounded`, `:5974-5999`).
    - Provenance: the architect's adversarial review (reviewer output) raised both E6 points. E6's wording changed after row 33's selection, and its line did not.

    [verified] `anchors: row6`
8. `respond-pr` step 6 asks for its own per-comment return fields (`respond-pr/SKILL.md:60-62`). That is the case #1453's `code-writer.md` clause "This holds even when the dispatch adds a separate verification field" covers, so the clause stays. [verified] `anchors: row7`
9. No transcript shows whether the parent in #1453's triggering failure had loaded `subagent-delegation`. [unverified] A CLAUDE.md copy of the rule waits on that evidence (Out of scope). `anchors: row4`
10. The no-op paragraph stays byte-identical to `main`. [engineer-verified: "I'm a little hesitant to remove it, even though it's redundant"] The engineer also asked for the reason behind the removal: "Why did you remove this??! This is important. Maybe you had a good reason. If so, explain." `anchors: root`
11. The deletion contradicted recorded decisions:
    - `docs/design-decisions/no-op-dispatch-guard.md:22` keeps both copies under the stand-alone-prose exception and words them identically so `git grep` shows drift. Its Sources (`:31`) cite the skill copy.
    - `.claude/plans/guard-no-op-dispatch-rule.md:140` put trimming the skill paragraph out of scope.
    - The superseding `no-op-dispatch-hook-gate.md` changes the enforcement and keeps the CLAUDE.md bullet as "the primary surface" (`:27`). It does not retire the skill copy.

    [verified] `anchors: row10`
12. The skill copy has a reader the CLAUDE.md copy misses: a non-fork subagent that loads the skill, since "only the main session and forks follow Main session" (`claude/.claude/CLAUDE.md:3`). [verified] `anchors: row10`
13. The line cap did not license the cut. `docs/skills.md:171` frees lines only by deleting a paragraph restated "elsewhere in the same file". It counts a gate as stating a rule only where "its own header lists no known gap". The CLAUDE.md copy is in another file, and `deny-no-op-dispatch.sh` has listed gaps (`no-op-dispatch-hook-gate.md:25-36`). [verified] `anchors: row10`
14. `check-skill-length.sh` denies a commit when a staged skill file is over its limit and longer than its committed version (`:6-9`). Outside an in-progress merge, rebase, cherry-pick or revert, the committed version is `HEAD` (`_lib.sh:1666-1670`, `:3169-3175`, `:3218`). [verified] `anchors: row16`
15. `subagent-delegation/SKILL.md` is 202 lines on `origin/main` and 194 at #1453's `HEAD`, against a 200-line limit. #1453 removed a 12-line paragraph and added a 4-line one. This worktree's copy, unmodified at session start, is 202 lines. [verified: Reads of both branch copies; Verification step 1 re-checks `origin/main`] `anchors: row16`
16. Mechanism: a new branch from `origin/main`. Its commit compares the file against `origin/main`'s count, and the edits add no line (row 17), so the ratchet allows it. On #1453's branch the comparison is against its shorter `HEAD`, and restoring the paragraph alone puts the file over the limit (row 15). [verified: rows 14-15] The engineer selected this branch (row 29). `anchors: root`
17. E1 and E2 add no line. E1 joins `main:176`, a single line holding one whole paragraph, and E2 joins `main:201`, a single line holding the whole `no-match` bullet. Both halves of E1's paragraph say what the parent does after a dispatch returns. A standalone E1 paragraph would cost two lines, which the ratchet denies because the file is already over its limit (row 15). No offsetting cut is pre-approved, so a review finding that asks for a separate paragraph stops for the engineer. [verified: `main:173-176`, `:201`] `anchors: row16`
18. E1's "a return's" replaces "the return's" so the rule also covers plan-phase returns. #1453 placed it right after the review-round carve-outs (branch `:164`), where "the return" reads as the review-round dispatch's. [verified] `anchors: row4`
19. `Not addressed:` appears nowhere in § "Exception: gate/review loops stay orchestrator-driven" [verified: Grep], which `docs/design-decisions/review-orchestrator-gate-release-decoupling.md:21` calls "the protocol's one home". #1453's item 6 sends the relayed entries to the dispatcher, but the protocol the dispatcher follows has no step for them. `anchors: row20`
20. Mechanism: E2 heads the `no-match` bullet, so the dispatcher acts on every relayed entry E1's trigger names (row 41) before either of the bullet's arms runs. It stages its own fix as step 2 stages `Fix paths:`, so the bullet's existing "name the paths just staged" and "a fix just staged" carry that fix into the next round's review. Step 3's `check` need not rerun, because the outcome is already `no-match` and step 2 says staging retires any earlier marker. Asking the user about an entry no fix settles adds no `HALT:` entry, so the round neither halts nor moves inline. E2 differs from the halting relay an earlier plan dropped on the engineer's approval. That relay's cost was that "a halted round moves the rest of the review loop into the parent" (`.claude/plans/code-writer-verify-dictated-claims.md:40`, `:68` and its row 24). [verified: `subagent-delegation/SKILL.md:198-201`] `anchors: row19`
21. In the orchestrator loop, E1's ADDRESS-row option has no reader. The next round's `code-review` disposes only that round's findings (`code-review/SKILL.md:352`), so an ADDRESS row the dispatcher logged would never be dispatched. E2 therefore asks the user about an entry no fix settles. [verified] `anchors: row20`
22. The `match` arm needs no clause. A round that dispatched `code-writer` writes no marker (`review-orchestrator.md` item 7), so `check` can match afterward only through another session's marker written mid-round (design record `:23`). This residual is accepted. A stop at the Persist, Stage or Check step needs none either, because step 4's lead line keeps the verbatim relay that already showed the user every entry. [verified] `anchors: row20`
23. #1453's relay reverses an accepted gap in `.claude/plans/code-writer-verify-dictated-claims.md` (rows 25-26, Out of scope `:213`). Those rows are `[verified]` acceptances, not engineer quotes, and the engineer's two #1453 comments raise no objection to the relay. [verified] `anchors: row20`
24. The rule is not the parent check of dictated text the engineer rejected in that plan's row 2 ("The parent verifying it seems expensive"). It acts on what the writer already reported and adds no read of the dispatch. [verified] `anchors: row4`
25. Mechanism: E7 extends `test_review_orchestrator_return_item_named_at_both_ends` (`test_skills.py:9705-9720`). That test exists because "the agent reports it and the dispatching session acts on it". It fails before E2 lands. E8 keeps the design record's list of pinned items accurate. [verified] `anchors: row20`
26. Accretion check. E2 and E7 are the fourth and fifth pieces on the sibling-site channel, after the writer's report, the parent rule and the relay. The one-level-up fix would let `code-writer` fix the other sites itself. That reverses the earlier plan's row 7 and the Charter's file-scope bar (`code-writer.md:15-19`, `:150-151`), so it is Out of scope and goes to the engineer. The parent rule survives that fix either way, because claims the writer leaves unwritten still need the parent. [verified] `anchors: root`
27. Round state is keyed per branch (`_lib.sh:5208-5227`), so a new branch also restarts `require-architect-consult.sh`'s count. #1453's ledger already holds six rounds. [verified] Whether #1453's recorded count already triggers the round-3 consult is [unverified]. `anchors: row16`
28. The rule lives in `subagent-delegation`. [engineer-verified: "subagent-delegation (Recommended)"] The label names the file only. Row 4 picks the section within it. `anchors: root`
29. The new work goes on a new branch from main. [engineer-verified: "New branch from main (Recommended)"] The label names the branch's base only. It does not cover the branch's name, #1453's PR, branch or worktree, or any force-push. `anchors: root`
30. Mechanism: `branch-management` creates `still-uncertain-parent-rule` in a linked worktree, where `worktree-ensure.sh` fetches and starts from the default tip itself (`branch-management/SKILL.md:57-59`). The branch name is the session's choice, not the engineer's. The plan's new path is `.claude/plans/<topic-slug>.md`, with the slug taken from the branch name (`plan-it/SKILL.md:23`). [verified] `anchors: row29`
31. This file is untracked in #1453's worktree, so moving it out adds no commit to `writer-still-uncertain-handling`. [verified: session-start `git status` lists it as `??`] If it has been committed there since, removing it takes an ordinary commit on that branch, never a history rewrite. `anchors: row30`
32. Closing #1453, removing branch `writer-still-uncertain-handling` or its worktree, and any force-push each need the engineer's yes, and none is authorized. Neither selected label covers them (rows 28-29). Each also reaches past this change into #1453, which `claude/.claude/CLAUDE.md:26` says to flag and confirm first. [verified] `anchors: row29`
33. E5 and E6 stay in the plan. [engineer-verified: "Keep E5 + E6 (Recommended)"] The label selects those two edits only, and the engineer typed "I think those makes sense but have the architect adversely review those choices". `anchors: row7`
34. E7 and E8 stay in the plan. [engineer-verified: "Keep E7 + E8 (Recommended)"] The label selects those two edits only; the same typed request for an adversarial architect review covers all four edits. `anchors: row25`
35. The two #1453 review comments quoted on the `Ask:` line are the engineer's. [engineer-verified: "Yes, both are mine"] `anchors: root`
36. The engineer chose among the session's options for where the dispatcher acts on relayed entries. [engineer-verified: "I think a backtick is the more correct choice"] Row 37 records which option "a backtick" names. `anchors: row20`
37. The engineer meant A′, the option the session offered as "Keep dispatcher design, move E2", amended per Fable's design review into E2 as written. [engineer-verified: "Yes, A′"] The label answers the session's question whether "a backtick" meant A′, and it covers only that. The option wording and the amendment are the session's and the reviewer's, not the engineer's. `anchors: row36`
38. E8 stays, though the architect's adversarial review (reviewer output) advised dropping it. That review cited the record's bar, "pin the contract's ends and nothing else" (`:32`), and `code-review/SKILL.md:82`, under which a design-decision record with a dated provenance line is never a stale-claim hit. The engineer's selection in row 34 stands, and Fable's design review (reviewer output) also kept E8. [verified: design record `:32`, `:34`; `code-review/SKILL.md:82`] `anchors: row34`
39. E1's trigger reads "a claim the writer left unwritten, or another site that states a claim it changed", the wording Fable's design review (reviewer output) proposed. [engineer-verified: "Yes, reword it (Recommended)"] The label covers only the trigger wording. `anchors: row4`
40. The reword leaves the file's length unchanged. E1 still joins `:176`, one line holding one whole paragraph between blank lines, and the reword changes words within that line. [verified: `subagent-delegation/SKILL.md:175-177`] `anchors: row17`
41. Under the reworded trigger, an entry naming only a claim the writer changed needs no action of its own. The writer's change is in its diff, which the parent reads line by line after a plan phase (`subagent-delegation/SKILL.md:157-158`) and the `/code-review` re-run reviews after a review round (`:170-171`). E2 acts only on entries the section requires acting on, so such an entry gets no dispatch and no question. It stays in the verbatim relay, and the next round reviews the change as part of the staged fix. [verified] `anchors: row39`

## Critical files

Phase 0 is parent work with no dispatch, after `/plan-review` (Sequence step 2). From the new worktree, port #1453's two agent-file hunks: `git diff origin/main...writer-still-uncertain-handling -- claude/.claude/agents/code-writer.md claude/.claude/agents/review-orchestrator.md | git apply`. If it does not apply cleanly, the Phase 1 dispatch re-applies that text from #1453's branch copies.

Phase 1 is one `code-writer` dispatch (`model: sonnet`) for E1-E3 and E5-E8, with E7 first, and Verification steps 5-8 as its verification command. Every edit applies one rule's scope, so splitting would restate that background in each prompt.

- `claude-skills/skills/subagent-delegation/SKILL.md` — modify: E1 on `:176`, E2 at the head of `:201`. Step 1's no-op paragraph stays byte-identical to `main`, and the line count holds (row 15).
- `claude/.claude/agents/review-orchestrator.md` — modify: #1453's item 6 and item 9 text (Phase 0), then E3's retarget.
- `claude/.claude/agents/code-writer.md` — modify: #1453's **Still uncertain** bullet, unchanged (Phase 0).
- `claude-skills/skills/respond-pr/SKILL.md` — modify: E5.
- `claude-skills/skills/ready-for-review/SKILL.md` — modify: E6 on `:199`. Its line count holds (row 7).
- `claude-skills/skills/tests/test_skills.py` — modify: E7.
- `docs/design-decisions/review-orchestrator-gate-release-decoupling.md` — modify: E8.

Reuse:
- Step 2's staging validation, which E2 cites instead of restating.
- `test_review_orchestrator_return_item_named_at_both_ends`, which E7 extends instead of adding a test.
- #1453's reviewed wording for the rule and both agent files.

Not touched: `code-review/SKILL.md` (its alternative above) and the files Out of scope names.

Projected diff: about 25 lines, across E1, E2, E3's retarget, E5-E8 and the Phase 0 hunks.

## Verification

1. Right after `branch-management` creates the new worktree, there: `git rev-parse HEAD origin/main` prints one SHA twice, and `git show origin/main:claude-skills/skills/subagent-delegation/SKILL.md | wc -l` prints row 15's `origin/main` count. Any other count stops the work, because rows 15-17's arithmetic no longer holds.
2. After the move: `.claude/plans/writer-still-uncertain-handling.md` no longer exists in #1453's worktree, and `git -C <#1453's worktree> status --porcelain` lists nothing under `.claude/plans/`.
3. After the edits: `wc -l claude-skills/skills/subagent-delegation/SKILL.md claude-skills/skills/ready-for-review/SKILL.md claude-skills/skills/respond-pr/SKILL.md` matches the same three files on `origin/main` (rows 7 and 15).
4. E1, E2 and the no-op paragraph:
   - `git diff origin/main -- claude-skills/skills/subagent-delegation/SKILL.md` changes exactly two lines, E1's (`:176`) and E2's (`:201`).
   - E2's line still opens with the `no-match` bullet marker, followed at once by "First act on each".
   - The bullet's original text resumes after "Then, " unchanged.
   - `git grep -n "A dispatch must return something the parent does not already have" -- claude/.claude/CLAUDE.md claude-skills/skills/subagent-delegation/SKILL.md` shows one hit in each file.
5. E6 and E8:
   - `git diff origin/main -- claude-skills/skills/ready-for-review/SKILL.md docs/design-decisions/review-orchestrator-gate-release-decoupling.md` changes one line in each.
   - In `ready-for-review` the changed line is `:199`, ending in "Before landing the fix below, act on that dispatch's **Still uncertain** entries…".
   - `ready-for-review`'s `:200` is untouched.
   - In the design record the changed line is `:34`, which lists `Fix paths:`, `Marker:`, `Not addressed:` and `HALT:` in the order `_REVIEW_ORCHESTRATOR_RETURN_ITEMS` holds them.
   - `scripts/venv-run.sh pytest claude-skills/skills/tests/test_skills.py -k land_the_fix_clause_matches_live_text` passes.
6. Red first: after E7 and before E2, `scripts/venv-run.sh pytest claude-skills/skills/tests/test_skills.py -k return_item_named_at_both_ends` fails on the `Not addressed:` case only. After E2 it passes.
7. `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py` passes. Its skills tests include the citation-resolution check for the new citations in E2, E5 and E6. E3's citation sits in an agent body outside that corpus, and it reuses item 9's existing target text.
8. `scripts/venv-run.sh ruff check claude-skills/skills/tests/test_skills.py` passes.
9. `/code-review` runs `/skill-review` on the three `SKILL.md` files (hook-enforced) and `/agent-review` on the two agent files. The commit then clears `check-skill-length.sh`.

## Out of scope

- The #1211 issue body edit, which needs the engineer's yes in the session, and merging.
- Letting `code-writer` fix other sites that state a claim it corrected (row 26). It reverses `.claude/plans/code-writer-verify-dictated-claims.md` row 7 and the Charter's file-scope bar, so it is a separate decision for the engineer.
- Rewording the rule beyond row 18's one word and row 39's trigger. #1453's review rounds already revised it.
- `claude/.claude/CLAUDE.md`. Neither the Agent Briefing no-op bullet nor the `code-writer` routing bullet at `:165` changes. A CLAUDE.md copy of the rule waits on row 9's evidence.
- A hook for the rule. Revisit if parents keep forwarding entries after this lands.
- `check-skill-length.sh` comparing against `HEAD` rather than the merge-base (row 14). Changing that is a gate-policy change outside this ask.
- Moving `code-writer.md`'s "This holds even when the dispatch adds a separate verification field" into the Return format's lead sentence. A #1453 round placed it in the bullet, and the two sentences state different facts.
- `plan-it/SKILL.md` and `handoff/SKILL.md`, which already route to the section (row 6).
- The #1453 actions row 32 lists. Each waits for the engineer's yes.
