# Free lines in code-review/SKILL.md by deleting restated text, then restore the verdict list and un-join the commit-message paragraph (GH-1079)

## Context

Goal: free lines in `claude-skills/skills/code-review/SKILL.md` (495 of 500) by deleting text the file already states elsewhere, then use the freed lines to restore the contradiction-route verdict list as an explicit list and un-join the commit-message authoring paragraph, without raising the cap and without moving content to another file.

Ask: GH-1079 follow-up after #1217 merged. The engineer's words on scope: "I think #1 the architect's path is fine, but there's several sessions parked waiting on lines to free up and I don't think 14 lines will be sufficient". Line-budget answer, selected label: "Verdict list + un-join". PR split, selected label: "One PR, item 4 first then item 1 (Recommended)". Item 4 (relocating the Item ownership table) is dropped from this plan in favor of in-place shortening, per the architect consult and the engineer's "#1 the architect's path is fine".

Why now: #1217 shortened the file in place but dropped the verdict-list restoration because #1216 rewrote that paragraph, and the commit-message paragraph was left joined to hold the cap. Other sessions (e.g. a per-round `plan-architect` consult) need room in the same file.

## Approach

Free 16 lines in `claude-skills/skills/code-review/SKILL.md`. Every one comes from deleting a block the same file already states, or from merging Item ownership rows whose owner cells are identical. Spend 9 of them on two restorations: the contradiction-route verdicts become a three-item list (+6), and the commit-message recipe goes back to its pre-#1217 shape (+3). Everything lands in one commit. The file goes from 495 to 488 lines, which leaves 12 lines of headroom under the 500 cap for parked work (5 today).

**Line budget for `code-review/SKILL.md`.** Line numbers are at HEAD `af973e93`. Each edit is located by its text, and edits are applied from the bottom of the file upward so the cited numbers above each one stay valid.

- **U, `:481-485`** (commit-message block, 5 lines → 8). Restore the pre-#1217 shape and keep today's wording:
  - Line 1 is the bold lead plus today's recipe text, verbatim from "**Authoring the commit message.**" through "…which resolve the argument statically and fail closed on it." It is followed by "Never author the message any of these ways:".
  - A blank line.
  - The heredoc bullet, verbatim.
  - A new bullet: `- **`-F` pointed at a pseudo-file.** ` followed by today's sentence, verbatim, from "`-F` names a regular on-disk file, never `-`, `/dev/stdin` or `/dev/fd/*`, in any spelling:" through "…denied outright rather than scanned."
  - The `-m "$(cat …)"` bullet, verbatim.
  - A blank line.
  - Today's three sentences as their own paragraph, verbatim, from "Keep the file out of `$HOME`…" through "…both match on the `-F` argument."
  - A blank line.

  Net **+3**.
- **D8, `:423`.** Delete the sentence "The dispatcher fires reviewers per file-path domain detection." It contradicts `:269`. **0** lines.
- **D1, Item ownership rows `:432`, `:433`, `:434`, `:436`, `:443`, `:446`** (all six end `| judgment (any reviewer) | — |`). Replace them with one row at `:432`:

  `| **6–9, 9a, 9b, 9d, 12, 14** (dead exports, unnecessary wrappers, inline business logic, repeated in-house logic, repeated domain discriminants, unnamed semantic bounds, suppressions without rationale, stripped WHY comments, pre-existing issues in unchanged code) | judgment (any reviewer) | — |`

  Net **−5**.
- **D2, `:457-459`** (rows 28, 29 and 30 all read `` `staff-backend-engineer` | `ciso-reviewer` ``). Replace them with one row:

  `` | **28–30** (auth boundary coverage, input validation at boundaries, error response leakage) | `staff-backend-engineer` | `ciso-reviewer` | ``

  Net **−2**.
- **D6, `:405-406`.** Delete the "Unlike the marker write…" paragraph and the blank line above it. Append " By failure case:" to the end of `:404`, so the failure-case list keeps its lead-in. Net **−2**.
- **V, `:377`** (the contradiction-route region; the start anchor at `:376` does not move). Split only at two sentence boundaries, keeping every sentence verbatim and in order:
  - The text from "**A finding whose fix would undo…" through "…returns exactly one of the three verdicts per finding" stays on one line. Its final period becomes a colon.
  - A blank line, then three bullets. Each is one existing sentence with `- ` in front:
    - "*Keep current text* resolves it … rule below covers."
    - "*Apply this round's fix* is an ordinary ADDRESS row on the `code-writer` route."
    - "*Cannot choose* is a blocking stop-and-ask to the human."
  - A blank line, then everything else on one line, from "A finding with no explicit per-finding verdict…" through "…carries from the PR block.", with the end anchor inline as it is today.

  Net **+6**.
- **D5, `:346-347`.** Delete "Disposition turns on complexity/risk/test coverage…" and the blank line after it. Net **−2**.
- **D3, `:303-304`.** Delete the "Report every matched row's verdict…" paragraph and the blank line after it. Net **−2**.
- **D7, `:224`.** Delete the Exclusions bullet "Domain checklist items for domains where no files were changed". Net **−1**.
- **D4, `:140-141`.** Delete the blank line and item 12a's trailing paragraph, "Distinct from item 12…". The 12a bullets at `:133-139` stay untouched. Net **−2**.
- **Net −7.** The file goes from 495 to 488 lines, which is 12 lines of headroom. Acceptance is staged count = committed count − 7, and staged count ≤ 500.

**Sibling arm.** `claude-skills/skills/plan-review/ROUTING.md:79` has the same contradiction as D8: "The dispatcher fires reviewers per touched domain." contradicts `ROUTING.md:33`. Delete the same sentence there. It costs 0 lines.

**Follow-on doc edits caused by this change:**
- `docs/design-decisions/specialist-reviewer-roster.md:9`: D2 turns "across nine rows" false. Replace the clause "across nine rows (`code-review/SKILL.md:328, 347, 351, 356, 357, 358, 360, 361, 365`; row 347 covers two items)" with "in `code-review/SKILL.md`'s Item ownership table". This drops both the row count, which nothing pins and the next table edit would falsify again, and the line citations, which are already stale.

**Alternatives considered:**
- **Move the Item ownership table out, with a read hook (#1079 item 4).** Dropped. The engineer accepted path #1 (rows 26–28), and G2 bars a co-located file used as cap relief.
- **Restore the verdict list at #1217's +10.** That version also splits the region's lead into lead, site and standard paragraphs. Set aside: those paragraphs are sequential prose, not a parallel list, and `docs/skills.md:144` bars flattening only parallel lists. The +4 goes to headroom instead.
- **Restore the commit-message recipe exactly as before #1217 (+5).** Set aside. That version puts "Never author the message any of these ways:" on its own paragraph. Keeping it as the closing sentence of the recipe paragraph (as the text does today) separates every fact the comment-discipline finding named, for 2 fewer lines.
- **Delete the six judgment rows and cover them with one intro sentence (−6).** Set aside for the merged row (−5). The merged row keeps every item in the table that "wins over inline mentions".
- **Separate commits for the deletions and the restorations.** Not needed. The file is under 500, so the gate's ceiling is 500 for every commit (G1). One commit costs one review round, and the pin edit has to land with V anyway.

**Branch name.** Keep `GH-1079/restore-verdicts-and-move-ownership-table`. Make the PR title name the real scope (row 25).

**Assumption ledger**

Root: `code-review/SKILL.md` is installed into every stow consumer's `~/.claude/skills/`. It sits at 495 of a 500-line cap, and parked work on the same file needs line room. Two of its blocks are run-ons that #1217 left joined to hold the cap: the contradiction-route verdicts and the commit-message recipe. This plan frees lines only by deleting text the same file already states. It spends some of them restoring those two blocks and leaves the rest as headroom. Inside the blocks it edits, it also closes one contradiction #1217's approved plan meant to remove.

Givens:
- G1 — The cap gate's policy for this file is fixed:
  - The limit is 500.
  - It counts lines.
  - It denies a commit only when the staged file is over the limit **and** longer than the committed version.

  Reason: `docs/skills.md`'s skill-architecture policy and #1079's design consult own the cap's value, metric and ratchet. Changing any of them needs a decision outside this plan. `[verified: claude/.claude/hooks/check-skill-length.sh:6-9, :111-120; docs/skills.md:145]`
- G2 — No content moves to a co-located or runtime file to buy room. Reason: the repo's authoring rule owns this, and reversing it is #1079's item-4 decision, which is outside this plan. `[verified: .claude/rules/skill-and-agent-self-review.md "never as a way to route around a file's length cap"; docs/skills.md:143-146]`
- G3 — The parked sessions own their line needs, including #1187's per-round consult. This plan can maximize headroom but cannot size it. Reason: other sessions and issues own those designs. `[unverified: sizes]`

Rows:
1. `[verified: Read of code-review/SKILL.md (content ends at :495); #1217 PR body "495 lines … awk 'END{print NR}' on HEAD"]` The file is 495 lines. Under G1, any commit may take it to 500, so headroom today is 5. anchors: root
2. `[verified: arithmetic over row 1 and G1]` The additions alone (+9) would reach 504, so they must land with the deletions or after them. One commit does both. A separate deletions commit would also pass the gate, but it adds a review round. anchors: row1
3. `[verified: :432-434, :436, :443, :446 end "| judgment (any reviewer) | — |"; :440 and :447 carry co-owners and are excluded]` D1 merges six rows whose owner and co-owner cells are identical. Every item keeps its owner, and the merged row names each item number. anchors: root
4. `[verified: :457-459 identical cells; :448 "15–19. Infrastructure" is the range-row precedent]` D2 merges rows 28–30. Row 33 has the same cells but is not merged: rows 31 and 32 sit between, with different cells, and merging across them would break the table's number order. anchors: root
5. `[verified: docs/design-decisions/specialist-reviewer-roster.md:9; recount of ciso-reviewer co-owner cells at :428, :448, :452, :457-459, :461, :462, :466]` Today `ciso-reviewer` co-owns ten items across nine rows. After D2 it is still ten items, across seven rows. That doc's line citations (328–365) are already stale against the current `:428-466`, and its row count is not pinned by any test, so the edit drops the count instead of restating it. anchors: row4
6. `[verified: :303 vs :230-235 (concrete-question rule, empty-rationale warning), :269 (spawn per question)]` D3 restates rules the file already states. Only the `ciso-reviewer` checkout example is lost. anchors: root
7. `[verified: claude-skills/skills/tests/test_skills.py:4454-4484]` After D3, `:305` is an unindented line that is not a list item, so it still ends `_invalid_skip_rationale_labels`' scan of the list above it. anchors: row6
8. `[verified: :141 vs :78, :130, :288, :444]` D4's paragraph restates the scope split between items 12 and 12a, the inline tripwire, and the reviewer's lane. anchors: root
9. `[verified: claude/.claude/hooks/tests/test_design_decision_files.py:539-555, :678-696]` `_ITEM_12A_SECTION_RE` captures only contiguous `   - ` lines, so the 12a bullets still parse after D4. The fixture docstring at `:679` calls a trailing paragraph part of "the real SKILL.md shape", which D4 makes false. Its docstring changes, and the fixture stays as a parser boundary case. anchors: row8
10. `[verified: :346 vs :393 ("implementation size … not disposition axes"; "one-line cosmetic fix in already-touched, already-tested code is ADDRESS"), :344]` D5 is restated. anchors: root
11. `[verified: :404 ("in addition to, not instead of, the marker write"; "one event per round-open regardless of outcome"), :402 ("on every invocation"), :25 (compaction/resume use of the ledger)]` D6 drops only the rationale clause. Moving "By failure case:" to `:404` keeps the list's lead-in. anchors: root
12. `[verified: :21 vs :224]` D7's bullet is the inverse of `:21`'s apply-only-when-matched rule. anchors: root
13. `[verified: .claude/plans/relocate-code-review-content.md rows 10, 17, 29, 31; old-branch PR body Settled row 024d1d90e363 "the committed text keeps them joined"]` #1217's approved plan said to delete both dispatcher-fires sentences, but the merged text kept them joined into the paragraph. D8 and its `ROUTING.md:79` sibling are the identical fix in each arm, per CLAUDE.md "Audit structural siblings". anchors: root
14. `[verified: :376-377; test_skills.py:5766-5842 (pin), :4734-4741 (whitespace-collapsed exact equality), :5861-5872]` V keeps every sentence and the anchor names. The pin changes by exactly four tokens: "per finding." becomes "per finding:", and `- ` goes in before each of the three verdicts. The pin compares content only, not list structure, so Verification item 5 checks the structure. anchors: root
15. `[verified: docs/skills.md:144; relocate-code-review-content.md C2 (+10)]` Only the three verdicts form a parallel list. The lead and the tail are sequential prose, so V stops at +6. anchors: row14
16. `[verified: current :481-485; pre-#1217 shape at :484-492 of a pre-#1217 checkout; #1217 PR body "went from 10 lines to 5 … the `-F` pseudo-file prohibition and its rationale are folded into the surrounding paragraph"]` U separates the joined facts again: the recipe, the pseudo-file ban, and the file-location rule. `[unverified]`: I did not read `git show 0de19ea8` or `af973e93~1` (I have no Bash). anchors: root
17. `[verified: claude/.claude/hooks/enforce-marker-script-shape.sh:1123-1124]` The hook's pointer names "Authoring the commit message", so U keeps that bold lead verbatim. No test pins it. anchors: row16
18. `[verified: current :481 text]` U moves today's regular-file sentence into the pseudo-file bullet verbatim, including "in any spelling". It also keeps "the PII gate and the redaction gate" in line 1, so "either gate" in the heredoc bullet still has a referent. anchors: row16
19. `[verified: arithmetic over rows 3–12, 14, 16]` −16 + 9 = −7, giving 488 lines and 12 of headroom. anchors: root
20. `[verified: :470 vs :488-489]` Reserve R1 is not planned. The first two "Do NOT write the marker if" bullets restate the pinned clean definition at `:470`, so deleting them would free 2 lines. But that list guards marker forging, and losing its salience there costs more than 2 lines are worth. No other whole paragraph in the file is restated elsewhere in it. anchors: row19
21. `[engineer-verified: "Uhh is that a plan file unrelated to yours? Don’t touch it. The extraction may still be needed. Remember the architect advised against it in part because it thought the extraction we are doing in this PR is sufficient"]` `docs/design-decisions/ready-for-review-fix-loop-convergence.md:98` ("zero headroom", extract the table) stays untouched by this PR. `[verified: that file's line 98 text, as quoted in the exploration report]` The line stays stale against the 488-line outcome; correcting it is a follow-up (Out of scope). anchors: row26
22. `[verified: :291]` The "Reshapes reviewer ownership" row fires on substantive routing-table edits. D1 and D2 change no owner or co-owner cell, so they are copy edits under that row's own skip clause. `[unverified]` how the `/code-review` orchestrator will rule on this. If it fires, it spawns every persona in the table. anchors: row3
23. `[verified: claude/.claude/agents/code-writer.md:66-69]` `code-writer` stops on any trim made to fit a cap, and on any pin edit, unless the dispatch prompt directs it. So the prompt names each D-edit and the four pin tokens as plan-directed. anchors: row14
24. `[verified: old worktree agent-reviews/review-ledger-1791225697-GH-1079-relocate-cod.md row 61a3c50c1710]` On the #1217 branch, the joined `:481` paragraph was SETTLED with the engineer's words "Just wrap the lines to meet the cap and another session will fix the overflow problem" and the rationale "un-joining waits for item 4 relief". U is that "another session" fix. It is funded by deletions rather than item 4, per rows 26 and 29. `[unverified]` whether this branch's ledger digest shows that row (I did not read #1216's branch scoping). If it does, the un-join is the engineer's later instruction, not a review-driven revert. anchors: row16
25. `[verified: code-review/SKILL.md:76 (the plan is keyed by "this branch's slug"); the plan file is named after the branch slug]` Renaming the branch would mean renaming the plan file too. The worktree path the session is anchored in would still carry the old name. The PR title carries the real scope, so keep the branch name. anchors: root
26. `[engineer-verified: "I think #1 the architect's path is fine, but there's several sessions parked waiting on lines to free up and I don't think 14 lines will be sufficient"]` The engineer accepts path #1, and parked sessions need more room than a 14-line figure. anchors: root
27. `[unverified]` "#1" is the session's proposal label for "shorten in place, no hooks change, no table move". That meaning is the session's description, not the engineer's words. anchors: row26
28. `[engineer-verified: "I think we need a read hook. That's been super critical for the plan review routing table"]` This was said about the table move. Row 26's later acceptance of path #1 drops the move, so no read hook is needed. This plan does not override it. anchors: row26
29. `[engineer-verified: "Verdict list + un-join"]` Both restorations are in scope. anchors: root
30. `[engineer-verified: "One PR, item 4 first then item 1 (Recommended)"]` One PR. anchors: root
31. `[unverified]` With item 4 dropped, the "item 4 first" ordering is moot. That reduction is the session's inference. anchors: row30
33. `[engineer-verified: "I think keep in scope. What does the architect think"]` The `ROUTING.md:79` sibling deletion (D8's other arm) stays in scope. The architect's authored plan already carries it, on row 13's "identical fix in each arm" reasoning. That is the plan's own reasoning relayed, not a fresh consult. anchors: row13
32. `[verified: claude/.claude/scripts/select-tests.py:519-520, :694-695, :703, read by two plan-review reviewers; the selector was not run]` `select-tests.py` selects `test_skills.py`, `test_design_decision_files.py`, `test_reconciliation_block_consistency.py` and `test_check_skill_length.py` for these paths. anchors: root
34. `[verified: claude/.claude/agents/code-writer.md:21-22]` `code-writer` does not stage or commit, so the parent owns `git add`, the staged-count check, the two reviews and the commit, in that order: stage, `/skill-review`, `/code-review`, commit. A fix after either review that touches a gated file re-arms that review. anchors: root
35. `[unverified]` Rollback is a plain revert of the single commit. Reverting only the deletions while keeping the U and V restorations (+9) would leave a file over 500 once parked sessions spend the headroom, so do not revert them separately. The U and V additions can be reverted alone safely. A bare `git revert` was not traced against the length gate. anchors: root

## Critical files

All paths are repo-relative. `.venv` paths use the worktree-relative form `../../../.venv/bin/...`.

**Phase 1: dispatch A (`code-writer` edits the working tree; the parent stages, reviews and makes one commit).**
- `claude-skills/skills/code-review/SKILL.md`: apply U, D2, D1, D8, D6, V, D5, D3, D7 and D4, in that order (bottom-up). This order, not the order of the bullets in Approach, is the apply order. See Approach, "Line budget".
- `claude-skills/skills/tests/test_skills.py`: in `_PINNED_CONTRADICTION_ROUTE_CLAUSE` (`:5766-5842`), make exactly three replacements (four tokens, because the first also changes the full stop to a colon) and nothing else, including the comment above it:
  - "per finding. *Keep current " becomes "per finding: - *Keep current ".
  - "covers. *Apply this round's fix* " becomes "covers. - *Apply this round's fix* ".
  - "route. *Cannot " becomes "route. - *Cannot ".
- `claude/.claude/hooks/tests/test_design_decision_files.py`: the docstring at `:679-681` becomes "Pins the parsed bullet names, in order, for the real SKILL.md bullet shape (annotated and bare bullets, a name containing quotes), plus a trailing paragraph that isn't part of the list." The fixture does not change.
- `claude-skills/skills/plan-review/ROUTING.md`: delete the sentence "The dispatcher fires reviewers per touched domain." from `:79`.
- `docs/design-decisions/specialist-reviewer-roster.md`: in `:9`, make the clause replacement given in Approach.
- The dispatch prompt must say:
  - The plan directs each D-edit and exactly those three replacements (four tokens, row 23). Run `TestCodeReviewContradictionRouteRegionPin` before the pin edit (it must fail once V is applied) and after (it must pass). If it still fails after the three replacements, stop and report. Make no further pin edit.
  - Moved sentences stay verbatim. The only allowed changes are the one colon, the `- ` markers, "either" becoming "any" in U's lead-in, U's new bullet title, and D6's appended "By failure case:".
  - `code-writer` does not stage or commit (`claude/.claude/agents/code-writer.md:21-22`). Tell it to read the committed count with `git show HEAD:claude-skills/skills/code-review/SKILL.md | awk 'END{print NR}'` and to measure the working-tree file with `awk 'END{print NR}' claude-skills/skills/code-review/SKILL.md`. The target is committed − 7. Stop and report if the working-tree count exceeds 500. The parent runs Verification 1 after `git add`.
  - Before each edit, check whether the site is already in its end-state form. When stopping, report each site as done or pending.
- Verification command: Verification items 1 to 5.

**Reuse.** V copies the round-cap consult region's verdict-list shape (`code-review/SKILL.md:316-322`). D1 and D2 copy the range-row shape (`:432`, `:448`). No new script, hook or helper.

**Deliberately untouched:**
- `claude/.claude/hooks/check-skill-length.sh`
- `claude/.claude/hooks/enforce-marker-script-shape.sh`
- Every other hook

The plan file itself is exempt.

## Verification

1. **Line counts**, using the gate's own counter:
   - Committed: `git show HEAD:claude-skills/skills/code-review/SKILL.md | awk 'END{print NR}'` prints 495. Re-read it after any rebase.
   - Staged, after `git add`: `git show :claude-skills/skills/code-review/SKILL.md | awk 'END{print NR}'`. Hard acceptance: ≤ 500 and not above the committed count. The target is committed − 7 (488). A review round that edits `SKILL.md` may legitimately move it.
   - `git show :claude-skills/skills/plan-review/ROUTING.md | awk 'END{print NR}'` must equal its committed count.
2. **Length gate.** `claude/.claude/hooks/check-skill-length.sh` runs on the commit itself, and it must not deny with "skill length". Item 1 mirrors its rule ahead of time. Its own suite (`claude/.claude/hooks/tests/test_check_skill_length.py`) runs under item 3.
3. **Scoped tests.** Run `../../../.venv/bin/python3 claude/.claude/scripts/select-tests.py`. Everything it selects must pass. These tests are the most sensitive to this change:
   - `TestCodeReviewContradictionRouteRegionPin`
   - `test_disposition_rule_anchors_present`
   - `test_invalid_skip_rationale_labels_match_across_review_skills`
   - `test_pinned_scope_clause_matches_live_text`
   - `TestCodeReviewCleanDefinitionCountsSettledAndDefer`
   - the `--authoring-effort` literal test at `test_skills.py:698`
   - the `_REVIEW_LEDGER_PROSE_CONTROLS` code-review params, `test_findings_path_recipe_tokens_present_in_code_review_and_plan_review` and `test_findings_path_suffix.py` (they read the sections that D3 and D6 edit)
   - `test_item_12a_index_matches_agent_angle_headers`
   - `test_item_12a_bullets_parse_annotated_bare_and_quoted_names`
   - `test_reconciliation_block_consistency.py`
   - `test_every_citation_shaped_construct_is_extracted`

   If the selection omits `test_skills.py` or `test_design_decision_files.py`, that is a bug in `select-tests.py`'s rule table. It is not a reason to widen the run by hand.
4. **Lint:** `../../../.venv/bin/ruff check claude-skills/skills/tests/test_skills.py claude/.claude/hooks/tests/test_design_decision_files.py`.
5. **Text and structure checks.** Run each as its own single-statement `grep -c '<pattern>' <file>`. For an absence check, read the printed number, not the exit code.
   - In `claude-skills/skills/code-review/SKILL.md`, each pattern must count exactly 1:
     - `'^- \*Keep current text\* resolves it'`
     - `"^- \*Apply this round's fix\* is an ordinary ADDRESS row"`
     - `'^- \*Cannot choose\* is a blocking stop-and-ask to the human\.$'`
     - `'exactly one of the three verdicts per finding:$'`
     - `'^A finding with no explicit per-finding verdict from the consult'`
     - `'carries from the PR block\.<!-- DISPOSITION_RULE:code-review-contradiction-route end -->$'`
     - `'^\*\*Authoring the commit message\.\*\*'`
     - `'Never author the message any of these ways:$'`
     - `'^- \*\*A shell heredoc\.\*\*'`
     - ``'^- \*\*`-F` pointed at a pseudo-file\.\*\*'``
     - `'^- \*\*.*command substitution\.\*\*'`
     - ``'^Keep the file out of `\$HOME`'``
     - `'in any spelling'`
     - ``'`-F` names a regular on-disk file, never `-`'``
     - `'denied outright rather than scanned'`
     - `'The redaction gate scans the commit command string itself'`
     - ``'both match on the `-F` argument'``
     - `'fail closed on it\. Never author the message any of these ways:$'`
     - `'By failure case:$'`
     - `'^| \*\*6–9, 9a, 9b, 9d, 12, 14\*\* ('`
     - `'^| \*\*28–30\*\* ('`
     - `'judgment (any reviewer) | — |'` (6 today)
   - Other exact counts in the same file:
     - `-- '--authoring-effort high'` must be 3.
     - ``'^| \*\*[0-9].*`ciso-reviewer`[^|]*|$'`` must be 7 (9 today). This is the source for the "seven rows" claim in the roster doc.
   - Absences, each must print 0:
     - In `code-review/SKILL.md`: `'file-path domain detection'`, `'Distinct from item 12'`, `'is actionable'`, `'Disposition turns on'`, `'Unlike the marker write'`, `'Domain checklist items for domains where no files were changed'`, `'either of these ways'`.
     - In `plan-review/ROUTING.md`: `'per touched domain'`.
     - In `specialist-reviewer-roster.md`: `'nine rows'`.
     - In `test_design_decision_files.py`: `'for the real SKILL.md shape:'`.
   - Presence in the docs, exactly 1: `'ten code-review items in `code-review/SKILL.md`'` in `specialist-reviewer-roster.md`.
   - `git diff --stat` lists no change to `docs/design-decisions/ready-for-review-fix-loop-convergence.md` (row 21).
   - Structure, checked by reading:
     - Each new list sits between blank lines, and its lead-in line is unindented and is not a list item.
     - `git diff -U0 -- claude-skills/skills/tests/test_skills.py` shows three hunks and only the four pin tokens. With the passing pin test, this proves V changed no content.
     - Every sentence of HEAD's `:481` appears in the new U block, except the lead-in's "either" becoming "any".
6. **Reviews:**
   - Order: `git add` the edits, then `/skill-review`, then `/code-review`, then the commit. Both review markers hash the staged state (the `/skill-review` marker covers `SKILL.md` and `ROUTING.md`; the `/code-review` marker covers the whole staged diff). Edit the plan file no further once its `/plan-review` is clean, because `require-plan-review.sh` denies the dispatch's Edit calls until the plan is re-reviewed.
   - Run `/skill-review` first. It is hook-enforced for both `SKILL.md` and `ROUTING.md`, and its behavioral-equivalence audit checks rows 6–12's "restated at" evidence.
   - Then run `/code-review`. Row 22 is the evidence for the "Reshapes reviewer ownership" skip.
   - The comment-discipline row is deferred to `/ready-for-review`'s cumulative pass (`code-review/SKILL.md:52`).

## Out of scope

- **Moving the Item ownership table out, and any read hook for it** (#1079 item 4). Dropped per rows 26–28.
- **The cap itself.** Its value, metric and ratchet stay as they are (G1).
- **Further splitting of the contradiction-route region.** Splitting the lead into lead, site and standard paragraphs would add +4. Splitting the tail into separate paragraphs for the no-verdict stop, keep logging and carries would add +2 to +4. Both are sequential prose, not parallel lists (row 15).
- **Reserve R1** (−2, row 20). Parked sessions can spend it in their own plans.
- **`:50-51`.** This text sits inside the pinned `SCOPE_RULE:code-review-staged-diff-only` region, so deleting it needs a pin change and touches what the scope rule means.
- **"Each agent self-scopes against the diff…"** (`:423`, `ROUTING.md:79`). It frees no lines, and the only place it is restated is the agent files, not this file.
- **Pinned regions:**
  - the `## Reconciliation` block
  - the `SCOPE_RULE` and `SCOPE_EXEMPT_ROW` anchors
  - `HOOK_TEST_FIXTURE`
  - the 12a bullets
  - the other `DISPOSITION_RULE` regions
- **Room for parked work beyond 12 lines.** This plan cannot add more without dropping a restoration the engineer selected or deleting text that is not restated. If #1187 or another parked session needs more than 12 lines, the engineer chooses among R1, dropping U (+3) or V (+6), or revisiting item 4.
- **A test pinning the hook pointer's target**, "Authoring the commit message" (`enforce-marker-script-shape.sh:1123-1124`). This is a follow-up.
- **`plan-review/SKILL.md:249`.** It carries the plan-review sibling of D7's deleted bullet ("Domain checklist items for domains the plan doesn't touch"). It is a file the Ask does not name, so it is the engineer's call whether to extend D7 to it.
- **Stale line citations** in `docs/design-decisions/skill-fidelity-reviewer-widened-scope.md:11` and `docs/design-decisions/duplicated-evidence-at-plan-review.md:7`, which point into `code-review/SKILL.md` and were already stale at HEAD.
- **A test guarding U's and V's list structure.** The pin collapses whitespace, so a later session could re-join U with no red test.
- **U's "in any spelling" wording.** The commit gates enforce only literal pseudo-file spellings, and the alias and attached forms are documented known gaps in their headers. U keeps the wording verbatim, so U is not a gate backstop for those spellings.
- **Renaming the branch** (row 25).
- **`docs/design-decisions/ready-for-review-fix-loop-convergence.md:98`.** It says the file has "zero headroom" and needs the table extracted, which the 488-line outcome makes stale. The engineer said not to touch it in this PR (row 21). A later change corrects it once the extraction question is settled.
