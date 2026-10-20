# review-pr: apply the final consult's corrections, then gate posting on approval

## Context

Goal: give `/review-pr` a reachable path in which every verified consult correction is applied to the findings body, the completion marker covers that final body, and posting still waits on the engineer's explicit approval of the exact body.

Ask: "There's a flaw in the review-pr consult application. Agents spawn a final consult without applying its edits. They seem to be stuck unable to figure out how it would work to be able to apply the edits and then get my approval for posting (posting should always be gated on my approval). How do we fix this?" — widened with "I think that Keep loop, apply final round makes the MOST sense by far. Ask the architect to do an adversarial review of that", and for edits the engineer requests in step 8, label "Same as round one (Recommended)". Widened again at plan review: label "Keep it in (Recommended)" (list unapplied corrections from every consult round) and label "Protect my edits" (no consult correction reverses an edit the engineer requested). On the step-8 path, the engineer then wrote: "One consult only. Now I'm not sure whether that makes things too complicated. I mean, if I tell the agent what to edit and then the consultant runs, that might be kind of excessive. Maybe have the architect do an adversarial review on the updated plan now." After that review, the engineer chose label "No consult (Recommended)" for changes after step 8 presents the body.

Problem: `claude-skills/skills/review-pr/SKILL.md` Step 7 makes two rules that together leave no terminal state with applied edits:

- Line 147: any change to the body after a consult, including an edit the human requests in step 8, gets another consult before the body is presented.
- Line 148: the third consult since the last presentation is final, so none of its corrections are applied, and they are shown in step 8 as proposed edits.

`REFERENCES.md:344-349` gives the invariant behind both rules: "every presented body [is] one the consult reviewed." Applying any consult's corrections therefore always requires one more consult, so a session that applies the final round's edits (as one did, at the engineer's instruction) is left with a stale completion marker and no sanctioned path to re-mark, present, and post. A second gap: Step 7 orders `marker.sh write review-pr` "after the consult", not after the last change to the body, so the session that applied the edits could not tell where the marker write belonged.

Why now: the stall happened in a real `/review-pr` session. The final consult's small corrections (line-number fixes and wording changes) were stranded.

## Approach

The step-7 consult loop stays, and it ends by applying the third consult's verified corrections with no consult after them. The marker is then written, and step 8 presents a fresh `Read` of the body with every unreviewed `Edit` shown verbatim. After step 8 presents the body, no change gets a consult. The engineer chose that (row 5) after the second adversarial review below. The rows and Critical files are written for it.

**Decision (settled, row 5): what follows a change made after step 8 presents the body**

Why this is a genuine decision: the engineer gave two statements that pull in different directions. They answered "One consult only.", then wrote "if I tell the agent what to edit and then the consultant runs, that might be kind of excessive". A file-recorded instruction from an earlier plan points toward a consult after step-8 feedback (row 7).

- **(A) No consult. Chosen.** The human's change is applied as its own `Edit`, the marker is re-written, and the body is re-presented from a fresh `Read` with that change's verbatim old/new pair. "Protect my edits" holds by construction, because no consult runs that could reverse the change.
- **(B) One consult per step-8 change. The engineer's earlier typed answer, not chosen.** Delta it would have needed:
  - Row 8's second sentence becomes `A change after step 8 presents it, including an edit the human requests, gets one consult.`
  - Row 9 becomes `- The third consult, and each consult after step 8 presents the body, is final: verify and apply its corrections as for any consult. Between it and the next presentation, make no other change except one that clears a \`marker.sh write\` refusal, and propose any other beside the body.`
  - Rows 24-25 come back, so seven SKILL.md lines change, line 145 included.
  - Row 21's last fact changes per row 25.
  - The trace gains T12 (Verification).
  - Cost: one `plan-architect` dispatch per step-8 change.
- **Excluded:**
  - (C) The full loop of up to three consults after a step-8 change. "One consult only." rules it out.
  - (D) A split rule: a directed edit gets no consult, and a request that hands the substance to the session gets one. It needs the session to classify every request, and requests like "make finding 3 less harsh" sit on the line. It also brings back (B)'s protection rule.

Why (A):
1. **Every option ends where (A) does.** The last consult's corrections are either applied with no consult after them (row 9) or shown unapplied, which is the stall the engineer reported. Under (B), the step-8 consult's own corrections become new unreviewed pairs. A consult after step 8 adds advice. It never produces a consult-reviewed body.
2. **For a directed edit, that advice is mostly blocked or marginal.** "Protect my edits" forbids applying a reversal. What remains is the consult adjusting how the session realized the instruction (wording, line numbers), or re-reading text no change has touched since the last consult. The human who gave the instruction is the best judge of whether it was realized. The verbatim pair and the fresh `Read` put exactly that in front of them.
3. **(A) removes the step-8 layer stack** described under "Second review" below.
4. **It differs in principle from the step-7 loop, so it is consistent with keeping that loop.** In step 7, the consult's corrections originate with a session reading untrusted PR content. After step 8, the change originates with the human.
5. **Residual risk.** A step-8 request that hands the substance to the session ("look at finding 2 again") produces session-authored changes, possibly derived from untrusted content, and no consult reviews them. They reach the human as verbatim pairs, and the human can ask for a consult. The skill does not encode that request, because the human's own instruction governs it.

**Adversarial review**

*First review: "Keep loop, apply final round."* `plan-architect` ran this in the Step 5 dispatch, at the engineer's request (row 2).

- **(a) It breaks the "every presented body is consult-reviewed" invariant.**
  - The break is real. The invariant itself was a session proposal (row 6), but the order it served came from the engineer's file-recorded words (row 7): "Make the necessary edits to your review, then have the architect review it, then present to me for final approval." and "Re-consult after every edit round (Recommended)".
  - "Keep loop, apply final round makes the MOST sense by far" supersedes that order for the final round.
  - The consult is advisory and holds no write tool (O1). A further consult reads the same untrusted PR content, so it adds no independent defense against steering.
  - What a further consult would catch is a misapplied correction, or a chat copy that differs from the file. The verbatim pairs (row 12) and the fresh `Read` (row 27) put both in front of the human.
  - `marker.sh`'s checks are shape checks, not a content review (row 13).
  - Verdict: holds.
- **(b) Protected findings.** SKILL.md:104 binds "any step 7 pass or the consult" regardless of round. Holds (row 14, trace T8).
- **(c) Untrusted input.** SKILL.md:145 does not depend on the round, and row 9 applies the final round "as for any consult". Holds.
- **(d) Convergence.** A review runs at most three consults and stops early at any consult whose verified corrections change nothing. The cap now bounds only cost and latency (row 16).
- **(e) Marker ordering.** Fixed by row 10. A sibling stall at :154 (a marker refusal after the third consult) is now fixed by row 9's explicit refusal allowance.
- **(f) The step-8 path.** See the open decision above.
- **(g) Lighter alternatives.**
  1. One consult, apply, present. The staff-product-engineer reviewer is right that this differs from the chosen design in degree, not in kind. The engineer chose the loop twice (row 1; row 7's file record), so I do not reopen it. What the loop still buys is that a consult reads the applied corrections of rounds one and two, which catches a misapplied correction before the human sees it. The PR body should say so, so a later reviewer can weigh it.
  2. A sanctioned "the human approves the unapplied corrections" path. It costs a round trip on every review that reaches round three, and the human first sees a body missing corrections the session already verified, which is the complaint.
  3. No cap. In the worst case it never ends.

*Second review: the updated plan.* The engineer asked for this (row 26). `plan-architect` ran it in this re-dispatch.

- **On the step-8 path, the compounding-layers tell fires.** Each layer closed a gap the one before it opened:
  1. Reading "Same as round one" as the full loop.
  2. A per-presentation count reset ("since the last presentation").
  3. A consult that could now reverse the human's edit.
  4. A "Protect my edits" sentence.
  5. Two more clauses the ciso-reviewer showed it needs: a definition of "the human", and precedence for the scrub rule.

  The staff-product-engineer reviewer's line-167 scoping problem came from the count reset too. The foundation under all of it is running a consult after a change the human directed. (A) removes that foundation, and with it the whole stack.
- **On the step-7 path, the tell does not fire.** The ciso-reviewer's fresh-`Read` and verbatim-pair controls do not patch the pairs list. They make the human backstop, which the design already rests on (REFERENCES.md:354), read the bytes the marker hashes. They cost two in-place clause edits, and they also close a pre-existing gap between the chat copy and the file. The ciso-reviewer's transcript-visible consult count would be a layer on top of a gap the pairs already bound, so I decline it (dispositions below).
- **Verdict: no simpler design is available within the engineer's choices.** Under (A), the change is six in-place SKILL.md rewrites and two REFERENCES.md edits, with no new bullet and no new definition.

**Plan-review findings: disposition.** I read the three findings files. For review-pr-post.sh, select-tests.py, check-skill-length.sh, and the test files, I rely on the reviewers' reads where noted and did not reopen those files.

- **ciso-reviewer**
  - Fresh `Read` plus verbatim old/new strings: accepted (rows 12 and 27).
  - Narrowing the no-consult exception: accepted for step 7. Between the third consult and presentation, the only changes allowed are its verified corrections and a fix for a marker refusal. Anything else is proposed beside the body (row 9). After presentation, the rule under (A) depends on timing, not cause, and the pairs bound it.
  - Fail-closed count that relies on visible dispatches: declined. Under rows 12 and 27, an overcount (believing three consults ran when fewer did) sends edits to the human as verbatim pairs, which is the review the design rests on.
  - Defining "the human" and precedence for scrub: dissolved under (A), because no protection rule exists and no label exists to spoof. Restored with both edges under (B) (row 24).
  - Row 13's overclaim: accepted (row 13 and row 21's Residual sentences).
  - Auto-mode post prompt and the edit-after-write fixtures: Out of scope.
- **staff-product-engineer**
  - Stale REFERENCES heading: accepted (row 21, plus a grep anchor).
  - Scoping line 167: accepted, as "after the last consult and not shown at an earlier presentation" (row 12).
  - Row 5: now an explicit open decision.
  - Traces for finish, decline, and a third consult that removes a finding: accepted (T1-T11).
  - Who ran the review, and rows 6-7 cited together: accepted.
  - The "the same way" wording: accepted ("as for any consult").
  - Stalled-session recovery and the loop's remaining value: accepted as PR-body notes.
  - :164 plural and :154 consult interplay: declined. "The consult" names the step, as at :104 and :123, and rows 8-9 govern the fix at :154. Each would add a changed line with no change in behavior.
  - Recovery text for the post script's refusal: Out of scope.
- **staff-sdet**
  - Grep that cannot see REFERENCES.md: accepted, with single-line anchors and a recorded pre-edit control.
  - Presence check: accepted.
  - Step 1 reworded to say it is a structural guard only: accepted.
  - Trace rows in (state, line, action) form covering the three missing cells: accepted.
  - Integration test: Out of scope.

**Assumption ledger**

Root: `/review-pr` Step 7 has no reachable end state in which every verified consult correction is in the findings body, the completion marker covers that body, and posting waits on the engineer's approval of it. SKILL.md:147 sends every change after a consult to another consult, and SKILL.md:148 forbids applying the third consult's corrections. Together they rule that end state out.

Givens: none. Every condition the design leans on lives in this repository and is in reach. The ones it deliberately leaves unchanged are O1-O3 under **Out of scope**.

Rows:
- Row 1 [assumption]: the engineer chose to keep the re-consult loop and apply the final round [engineer-verified: "Keep loop, apply final round makes the MOST sense by far"] — anchors: root
- Row 2 [assumption]: the engineer asked for this design to be attacked before adoption [engineer-verified: "Ask the architect to do an adversarial review of that"] — anchors: row1 — `plan-architect` ran it in the Step 5 dispatch ("First review" above). Verdict: adopt.
- Row 3 [assumption]: posting stays gated on the engineer's approval [engineer-verified: "posting should always be gated on my approval"] — anchors: root — Step 8's approval line (SKILL.md:171) and `review-pr-post.sh` are unchanged.
- Row 4 [assumption]: two details come from the session's option description, not from the engineer: "up to 3", and showing applied final-round corrections "as old/new pairs marked as not re-reviewed by a consult" [unverified] — anchors: row1 — "Keep loop" keeps the existing cap of three.
- Row 5 [assumption]: after step 8 presents the body, a change gets no consult — anchors: row8 — Settled.
  - The engineer answered the session's step-8 question with [engineer-verified: "One consult only."]. That answer rules out the full loop. It also corrects this plan's earlier reading of "Same as round one (Recommended)" as up to three consults.
  - The engineer then doubted any consult there [engineer-verified: "if I tell the agent what to edit and then the consultant runs, that might be kind of excessive"].
  - After the second adversarial review, the engineer chose (A) [engineer-verified: "No consult (Recommended)"]. The option's description text was the session's [unverified].
- Row 6 [assumption]: the invariant "every presented body is one the consult reviewed", and the unapplied-third-round stop, were session and reviewer proposals [verified: .claude/plans/review-pr-finding-hygiene.md:99, tagged `[unverified]` there] — anchors: row1
- Row 7 [assumption]: two earlier engineer instructions set the order edits → consult → present — anchors: row1, row5
  - "Make the necessary edits to your review, then have the architect review it, then present to me for final approval." [verified: .claude/plans/review-pr-finding-hygiene.md:100, which reads it as covering step-8 feedback]
  - "Re-consult after every edit round (Recommended)" [verified: same file, :109]
  - They are a file record, not words from this session. The engineer's answers here supersede them where they conflict: row 1 for the final round, and row 5 for step 8 once the engineer answers.
- Row 8 [mechanism]: rewrite SKILL.md:147 in place as `- Before step 8 presents the body, any change after a consult gets another consult, up to three in all. A change after step 8 presents it, including an edit the human requests, gets none.` — anchors: row1, row5 — The count runs per review and never resets, because no consult runs after a presentation.
- Row 9 [mechanism]: rewrite SKILL.md:148 in place as `- The third consult is final: verify and apply its corrections as for any consult. Between it and step 8's presentation, make no other change except one that clears a \`marker.sh write\` refusal, and propose any other beside the body.` — anchors: row1 — "As for any consult" points at :145-146. The window is bounded by the consult, so it cannot be read as forbidding a step-8 change.
- Row 10 [mechanism]: rewrite SKILL.md:150 in place as `After the last consult and every change to the body, from any tree of the repo (the marker is keyed to the main tree's root):` — anchors: root
  - "The last consult" keeps the write after a consult that changes nothing.
  - Re-running the write is safe. It re-hashes after its checks [verified: marker.sh:784-788] and overwrites with `>` [verified: marker.sh:790].
- Row 11 [mechanism]: rewrite SKILL.md:162 in place as `- A summary of each consult's corrections: every finding removed or downgraded, with its reason, and every correction not applied, with the disagreement named.` — anchors: row9
  - Old 148's "open disagreements named" needs a new home.
  - Extending it to rounds one and two is a sibling fix [verified: SKILL.md:162 lists only removals and downgrades today].
- Row 12 [mechanism]: rewrite SKILL.md:167 in place as ``- Every `Edit` made after the last consult and not shown at an earlier presentation, each as its verbatim old and new strings, marked as not reviewed by any consult.`` — anchors: row4, row8, row13
  - It covers third-round corrections, marker-refusal fixes, and step-8 changes.
  - It never re-shows a stale pair.
  - Line 160's "beside the body, never inside it" keeps "consult" out of the body, as :137 requires.
- Row 13 [assumption]: for every `Edit` made after the last consult, the human's reading of the presented text is the only content review — anchors: row8, row9
  - `marker.sh`'s pre-marker checks are an attribution check (prefix, trailer, diff-only disclosure) and a credential-shape scan [verified: marker.sh:766-783]. They are shape checks, not a content backstop.
  - Rows 12 and 27 make that human review read the marked bytes and the exact changes [verified: REFERENCES.md:354 names the human's approval as the backstop].
- Row 14 [assumption]: the protected-findings rule binds the final round with no edit [verified: SKILL.md:104, "in any step 7 pass or the consult"] — anchors: row9
- Row 15 [assumption]: verify-before-apply binds the final round with no edit [verified: SKILL.md:145 does not depend on the round] — anchors: row9
- Row 16 [assumption]: the loop ends. A review runs at most three consults and stops early when a consult's verified corrections change nothing [verified: SKILL.md:147 fires only on a change, as row 8 keeps] — anchors: row8 — The cap now bounds only cost and latency.
- Row 17 [assumption]: later rounds carry smaller edits [unverified] — anchors: row1
  - The only evidence is the one stalled session in Context.
  - This row backs alternative (g)1's rejection, not correctness, and stays out of durable prose.
  - A large third-round correction is possible. Trace T7 covers it, and the review burden for it falls on the human.
- Row 18 [assumption]: a body fix after a failed marker write is governed by when it happens [verified: SKILL.md:154 with rows 8-9] — anchors: row8, row9
  - Before step 8 presents the body and before the third consult, the fix gets another consult (T5).
  - After the third consult, row 9 allows it with no consult (T6).
  - After presentation, row 8 gives it no consult.
- Row 19 [assumption]: nothing outside SKILL.md and REFERENCES.md encodes the consult count or the final-round rule [verified: grep across the worktree; staff-product-engineer and staff-sdet independently confirmed it] — anchors: root
  - `test_review_pr_post.py:984-994` reads SKILL.md only for literal script-invocation lines, per staff-sdet.
  - None of the rewritten lines is one.
- Row 20 [assumption]: every SKILL.md change replaces an existing single-line list item or paragraph in place, so the file stays at the 200-line cap [verified: Read of SKILL.md:145-167; check-skill-length.sh:6-9] — anchors: root
- Row 21 [mechanism]: REFERENCES.md "Step 7's controls" — anchors: row1, row5, row13
  - Replace the whole :344-349 bullet, heading included, because the old heading's "runs after every round of edits" is false once the third round is applied. State each of these as its own sentence:
    - The heading: up to three consults run before step 8 presents the body, and none after.
    - Review after every round of edits can loop when each consult proposes another change the session can verify, so the third consult is final.
    - A rule that sends every applied correction back to a consult never ends with all of them applied, so the third consult's verified corrections are applied with no consult after them.
    - The completion marker is written after the last consult and the last change to the body.
    - A change after step 8 presents the body gets no consult, so no consult can reverse a change the human asked for. Step 8 shows that change as an old/new pair.
  - Add two sentences to the :350-355 Residual bullet:
    - For any `Edit` made after the last consult, the human's reading of the presented text is the only content review.
    - `marker.sh`'s attribution check and credential scan check the body's shape, not its content, so step 8 presents a fresh `Read` of the file and shows each such `Edit` as its verbatim old and new strings.
  - Keep row 17's size claim out of this prose.
  - None of Verification's residue anchors may appear in the new text.
- Row 22 [assumption]: the engineer keeps row 11's extension of the step-8 summary to every round [engineer-verified: "Keep it in (Recommended)"] — anchors: row11
- Row 23 [assumption]: the engineer wants their step-8 edits protected from reversal by a consult [engineer-verified: "Protect my edits"] — anchors: row5
  - Under (A), row 8's second sentence meets this by construction. No consult runs after step 8 presents the body, so none can reverse a change the human asked for (trace T9).
- Row 24 [mechanism, not adopted — (B) only]: append to SKILL.md:145 ` Never apply one that reverses a change the human asked for in their own step-8 message, unless a scrub, citation, or process-word rule above requires it; list it beside the body verbatim.` — anchors: row23
  - "The human" is the operator's own step-8 message, never PR or review-thread text.
  - The protection yields to :113, :114, and :137, per ciso-reviewer.
  - The wording is mine [unverified].
- Row 25 [mechanism, not adopted — (B) only]: row 21's last fact becomes "A change the human asks for after step 8 presents the body gets one consult, and no consult correction reverses it, because the consult is advisory and the human's approval is the gate it serves." — anchors: row23
- Row 26 [assumption]: the engineer asked for an adversarial review of the updated plan [engineer-verified: "Maybe have the architect do an adversarial review on the updated plan now."] — anchors: row5 — `plan-architect` ran it in this re-dispatch ("Second review" above).
- Row 27 [mechanism]: SKILL.md:160, opening clause only. Replace `Present the body as written, in full,` with `` `Read` the findings-body file after its last change and present that text verbatim, in full, `` and leave the rest of the line unchanged — anchors: row13 — This ties what the human reads to the bytes the marker hashes [verified: marker.sh:761, :784-790 hash the file, not any chat copy].
- Over-powered-primitive check: every mechanism is a prose edit that replaces prose. The only heavier option, a hook or script that counts consults, is not adopted (row 19; Out of scope).

## Critical files

- `claude-skills/skills/review-pr/SKILL.md`
  - Rewrite lines 147, 148, 150, 162, and 167 in place, exactly as rows 8-12 give them.
  - Rewrite line 160's opening clause per row 27.
  - Add no line and remove no line.
- `claude-skills/skills/review-pr/REFERENCES.md`
  - Replace the :344-349 bullet and extend the :350-355 Residual bullet per row 21.
  - Keep the file's existing hard-wrap width.
- `.claude/plans/review-pr-consult-apply-then-approve.md` — this plan, committed per `/plan-it` Step 7.

Reuse: none. The change is prose only. SKILL.md:104, :125, :145, and :146 are reused unchanged (rows 14-15; trace T11).

Dispatch: one `code-writer` dispatch covers both skill files. They share one rationale, and splitting them would restate it in each prompt.

## Verification

1. **Control, before any edit.** From the worktree root, run `grep -n -e 'apply none of its corrections' -e 'After the consult,' -e 'unapplied corrections' -e 'since the last presentation' -e 'requests in step 8, gets another consult' -e 'corrections unapplied' -e 'runs after every round of edits' -e 'presentation keeps every edit' claude-skills/skills/review-pr/SKILL.md claude-skills/skills/review-pr/REFERENCES.md`. It must print exactly SKILL.md:147, :148, :150, :167 and REFERENCES.md:344, :347, :348 (confirmed in this dispatch). Record the output in the PR body as the control.
2. **Residue, after editing.** The same command must print nothing.
3. **Presence and exactness.**
   - `git diff --numstat -- claude-skills/skills/review-pr/SKILL.md` must print `6	6`, so the line count is unchanged at the cap.
   - `git diff -U0 -- claude-skills/skills/review-pr/SKILL.md` must replace only lines 147, 148, 150, 160, 162, and 167.
   - Each `+` line must equal its row's quoted text character for character. For :160, that is row 27's opening clause with the rest of the line unchanged.
   - Read the rewritten REFERENCES.md bullets and confirm each of row 21's facts appears exactly once.
4. **Structural guard.** Run `.venv/bin/python3 claude/.claude/scripts/select-tests.py`.
   - Per staff-sdet's reading, it selects `review-pr/tests`, all of `claude-skills/skills/tests/`, and all of `claude/.claude/scripts/tests/`, which includes `test_review_pr_post.py` and `test_review_pr_lib.py`.
   - None of those tests reads the rewritten lines. This step guards against collateral damage only. The rule set's correctness rests on step 6.
5. **Reviews.** Run `/skill-review` on the SKILL.md diff (`require-skill-review.sh` enforces it at commit), then `/code-review`.
6. **Trace.** Record it in the PR body as rows of (starting state | SKILL.md line that fires | resulting action), each re-derivable from the edited text:
   - T1: consult 1's verified corrections change nothing | :147 does not fire | :150 write → :160 present → approve → post → :190 finish.
   - T2 (early stop): consult 1 changes the body, then consult 2 proposes nothing verifiable | :147 fires once | :150 → present with no pairs → approve → post → finish.
   - T3: consults 1-3 each change the body | :147 twice, then :148 | the third consult's corrections are applied → :150 → present with :167 pairs → approve → post → finish.
   - T4 (the stalled session's state): the third consult has returned and its corrections are unapplied | :148 | apply → :150 → present with pairs → approve → post → finish. No line demands a fourth consult.
   - T5: the marker write fails after consult 1 | :154, then :147 | fix → consult 2 → … → :150 → present.
   - T6: the marker write fails after consult 3 | :148's refusal allowance | fix with no consult → :150 → present, with the fix among :167's pairs.
   - T7: consult 3 proposes removing a non-protected finding, and the session verifies it | :148 | applied. :162 lists the removal with its reason, and :167 shows its pair. The two listings agree.
   - T8: consult 3 proposes downgrading a protected finding | :104 | not applied. :162 lists it as not applied with the disagreement, and :164 lists the evidence.
   - T9: the human asks in step 8 to downgrade a non-protected finding | :147's second sentence | one `Edit` (:146) → :150 → re-present from a fresh `Read` (:160), with :167 showing only that pair → approve → post → finish. No consult runs, so none can reverse the edit.
   - T10: the human declines at step 8 | :190 | no post, then finish.
   - T11: the body changes after the marker write, and the post refuses on the hash | :125 | re-run the :152 write → present again → approve → post. Per ciso-reviewer's read of `review-pr-post.sh:135-139, :165`, the refusal comes before the marker is consumed.
7. **PR-body notes.**
   - In-flight sessions: a session still carrying the old text recovers by re-running the marker write, then presenting, getting approval, posting, and running finish. This is safe per marker.sh:784-790.
   - The loop's remaining value is catching misapplied round-one and round-two corrections before the human sees them, so a later reviewer can weigh the loop against its cost.

## Out of scope

In-reach conditions the design deliberately leaves unchanged:

- O1: `plan-architect` holds no write tool, so the session authors every body `Edit` [verified: REFERENCES.md:350-351; `plan-architect`'s tool grant]. Granting it one would let the consult edit text derived from untrusted content directly, and the design does not need that.
- O2: the completion marker binds a SHA-256 of the body, and `review-pr-post.sh` re-verifies that hash before posting [verified: marker.sh:761, :784-790]. That binding ties the posted text to the approved text (row 3), so the design keeps it.
- O3: SKILL.md is at its 200-line cap. `check-skill-length.sh` denies a commit when a staged SKILL.md is over its limit and longer than the committed version [verified: check-skill-length.sh:6-9]. Every edit here rewrites in place, so raising the cap is not needed.

Deferred or declined:

- Mechanical enforcement of the consult count, including ciso-reviewer's "count only visible consult dispatches" rule. The count stays prose (row 19). The pairs bound an overcount, which is why the rule is declined (Approach).
- An integration test of write → edit → re-write → post. The contract predates this plan (SKILL.md:125). Per staff-sdet, `test_review_pr_post.py:345` already covers the unsafe direction: a post refuses a body that differs from the marked hash. Follow-up candidate.
- A recovery step in `review-pr-post.sh`'s hash-mismatch refusal message. That needs a script and test change, and SKILL.md:125 already states the recovery (T11). Follow-up candidate.
- Whether `review-pr-post.sh`'s permission prompt appears in auto mode [unverified; docs/auto-mode.md not read]. The condition is pre-existing, and the design relies on SKILL.md:171's explicit approval, not on the prompt.
- Wording at SKILL.md:154 and :164 (declined in Approach).
- `.claude/plans/review-pr-finding-hygiene.md` rows 39, 40, and 49 describe the old rule. That plan is a committed, preserved record and is not edited.

