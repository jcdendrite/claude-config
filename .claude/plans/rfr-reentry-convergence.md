# Plan: ready-for-review fix-loop convergence

## Context

Goal: make `/ready-for-review` step 3's fix loop converge, without weakening the guarantee that every pushed HEAD got a full, unnarrowed cumulative review.

The engineer observed that once a cumulative pass comes back dirty and gets fixed, every later round is another full cumulative review, and questioned whether that is excessive. Investigation found the full re-review is the correct reading of the design (`docs/design-decisions/reviewer-responsibility-bounded-to-diff.md`), but four things make the loop churn:

- `ready-for-review/SKILL.md`'s loop-back text supports two readings. Reading A: step 3 re-runs over the fixed bytes. Reading B: the fix commit's staged-diff review suffices. The CI-watch "Land the fix" arm states Reading B outright.
- Earlier cumulative passes' findings are not carried into the next pass.
- The disposition step has no outcome for a finding that contradicts a fix an earlier round applied, so it forces a revert — GH-752's round-4-reverts-round-2 shape.
- `code-writer`'s self-review has no prose row, so step-3 fixes write new prose the next pass's `comment-discipline-reviewer` flags.

Nothing caps how many cumulative passes one run can make. Intended outcome: all of these closed, with the byte-exact cumulative cache marker unchanged.

Engineer decisions this session: target thrash/re-discovery over cost; keep the cumulative guarantee byte-exact; Reading A; the comment-discipline deferral (83174e5a) stays out of scope; contradictions route to `plan-architect` first under a current-text-wins binary verdict, escalating to the human; include a pass cap; the `code-writer` prose row applies to all dispatches; a CI fix is treated the same as a failed run of local tests.

## Approach

Every step-3 re-run stays a full, byte-exact cumulative review. Every fix, including a CI fix, re-enters the gate at step 2. The loop converges through four changes instead:
- Carry each pass's decisions into the next pass, but never its coverage.
- Send a finding that would undo an earlier fix to the architect instead of applying it automatically.
- Cap consecutive dirty passes.
- Give `code-writer` a prose self-review.

Everything is prose plus test pins. No hook, script, or marker changes. `/ready-for-review` (RFR) owns everything about the loop: the disposition record, whether a pass counts as clean or dirty, and the cap. `code-review` owns what one review does with that history: the carry-forward rules for spawns, the contradiction route, and the clean definition.

1. **Make the loop rule read one way (Reading A), including the sibling arms.**
   - **Overview.** Lines 15–17 become one line, verbatim: "Run steps in order. Halt on failures unless the step is marked **warn only**. After a fix produced by step 2, 3, or 4, return to step 2 and continue in order. Step 3 then re-reviews the fixed cumulative diff in full, because its cache marker misses on the changed bytes. Step 4 does not re-run on its own output."
     - A blank line then separates it from line 18, which stays untouched. With the break, the new pin ends on a blank line. The alternative was one pinned region running through line 18, which would duplicate `_PINNED_HALT_DEFERS_CLAUSE`'s coverage. Net −1 line.
     - The sentence names step 2 so the rule covers every source of a fix, matching line 18's list of halting steps. The CI arm points here.
   - **Step 0 (lines 22–24).** Rewrite in place as one line: "Write the active-session marker so this skill's own pushes (step 7, reached after every fix loop) are not self-blocked by the `require-ready-for-review.sh` hook:". Today's "(step 3 fix → push → loop back to step 2)" describes a push before the re-review, which no path makes under Reading A (row 29). Net −2 lines.
   - **Step 3 (line 88).** Keep everything through "…write the cache marker instead — `~/.claude/scripts/marker.sh write cumulative-review`." Replace the rest, including "Do not re-run `/code-review` on its own output (loop risk).", with, verbatim: "If ADDRESS rows remain, dispatch one `code-writer` per `subagent-delegation`'s review-round default, covering every one. Its fix commit goes through the standard staged-diff `/code-review` + marker gate, and the Overview's fix-loop rule then brings the loop back through step 2 to a full pass of this step over the fixed bytes, within the cap below."
   - **CI watch, "Land the fix" (line 200)**, verbatim: "5. **Land the fix.** Step 8 removed this session's active marker and `require-ready-for-review.sh` denies a push without one, so re-run step 0's `marker.sh activate` command, then treat the fix as a step-2 failure's fix under the Overview's fix-loop rule, which carries it through step 8."
     - This is a pointer, not restated mechanics (finding d).
     - Today's text states Reading B word for word. Under the active-marker bypass, it pushes a HEAD that no cumulative pass reviewed (rows 5, 11, 14).

2. **Carry-forward, reviewer side: `code-review/SKILL.md`, "Ripple effect triage".**
   - Line 263: "prior findings + what's been applied" becomes "the prior decisions described directly below".
   - New block inserted after line 264. The bullet form below states the rules it must carry; item 6 supersedes its layout with a one-paragraph compression that keeps every rule, and the shipped text in `code-review/SKILL.md` is authoritative:
     ```markdown
     On a re-review, prior decisions are this session's context plus every disposition record `ready-for-review/SKILL.md` § "3. Code review (halt on findings)" wrote for this branch. Pass each spawn the record paths with these rules:

     - Finish your own review before opening the records. A record never narrows what you review or suppresses a finding the current text supports; name in your findings any record you cannot parse, or whose claim the current text contradicts.
     - For each earlier ADDRESS row your own agent raised, confirm the fix landed as the finding required. A missing or partial fix is a new finding.
     - Re-flag a site an earlier fix or verdict rewrote only under a different rule than the one behind the rewrite, or for a fact the rewrite dropped. When your fix would move a site back toward its earlier wording, name the finding it contradicts.

     ```
   - RFR line 85 (SCOPE_RULE, pinned by exact equality), verbatim: "This pass reviews the cumulative diff with no responsibility-boundary narrowing — see `code-review/SKILL.md`'s Step 0.6 for the rule and why. Decisions from earlier in this branch's fix loop — per-commit rounds and prior cumulative passes alike — feed in as context per `code-review/SKILL.md` § "Ripple effect triage", never as a substitute for this pass. The cache marker is written only from a clean pass of this step's own cumulative `/code-review`, never from a fix commit's staged-diff pass."

3. **Disposition record and cap: two new paragraphs in RFR step 3, directly after line 88, each preceded by a blank line.** Both describe RFR's loop: a record per cumulative pass, a count of cumulative passes, and a check before RFR's own fix dispatch. Step 3 is therefore their altitude, and `code-review` stays a skill about one review. The move also takes the largest block out of `code-review`'s tight budget (item 6).
   - **Record**, verbatim: "**Disposition record.** Once each pass's `/code-review` returns, `Write` its disposition table, even an empty one, to `agent-reviews/code-review-dispositions-<suffix>.md`, reusing that round's `<suffix>` or running `findings-path-suffix.sh` once if nothing spawned. This branch's records are those whose `<suffix>` carries the same slug after its first hyphen. Add an Outcome column holding each row's fix route, consult verdict, or DEFER criterion, and amend a cell if the landed fix departs from that row's suggested fix. The pass is clean when every row is resolved as `code-review/SKILL.md` § "Step — Record review completion" counts it, and dirty otherwise. The record authorizes nothing: the `cumulative-review` marker stays the only authorization, and later reviews read the record only as context."
   - **Cap**, verbatim: "**Cap.** Before dispatching a dirty pass's fix, list this branch's records newer than its newest clean one, in suffix-timestamp order, counting any record you cannot parse as dirty. If one of them already carries a cap row, stop and ask the human, blocking. Otherwise, if they number two or more, first dispatch `plan-architect` with `MODE=consult`, carrying the records' paths and the plan path if one exists, to judge whether the loop is converging (*proceed*) or its foundation is wrong (*stop*). Add its answer to this pass's record as a table row whose finding cell reads `cap` and whose Outcome is the verdict. A *stop*, a return that reads as neither verdict, or an orchestrator disagreement with the return, is a blocking stop-and-ask to the human."
   - **No status line.** The record has no status line, so clean and dirty come from the table's own rows, and nothing can contradict them (CISO F5, fixed at the foundation). This departs from "dirty iff any row is on the `code-writer` route" in one case. That rule would call a pass clean when it halted on a pending stop-and-ask or on an endorsed new-primitive consult, and so reset the count. Deriving clean from the set of findings line 453 counts as resolved closes that hole.
   - **One threshold (finding c).** The cap fires at the second consecutive dirty pass, before its fix dispatch. The number is two dirty records, taken from the round-3 gate's `_LIB_REVIEWER_ROUND_STATE_CAP=2`. The round-3 gate acts at the third review spawn because a spawn is the only event a hook can see. Prose can act one step earlier, where the fix is decided, so the consult judges the fix before anyone writes it (row 9).
   - **When the human steps in.** Four cases:
     - Any later dirty pass in the same run, because a cap row is already present.
     - A *stop* verdict.
     - A return that reads as neither verdict (a hedged or partial analysis), which the orchestrator never rounds to *proceed*.
     - The orchestrator disagrees with the return.

     The consult's answer is always written as a cap row, so a neither-verdict return gets a cap row whose Outcome cell reads `no verdict`. A cap row of any kind sends the next dirty pass to the human, so a human "continue" after a *stop* or a verdict-less return cannot quietly hand the next decision back to the architect.
   - **Unparseable records count as dirty.** A garbled or truncated record can therefore only bring the consult earlier.
   - **The contradiction consult and the cap consult stay separate, in sequence.** The contradiction consult runs inside `/code-review`'s disposition step. The cap check runs afterwards, because whether any fix remains depends on the contradiction verdicts. This replaces the earlier "one consult can carry both", which was circular.
   - **Where the count comes from.** The records live on disk, so the count survives step 1's handoff deferral. That deferral always fires mid-loop, right after a fix commit (rows 10, 16).

4. **Contradiction route: a new sibling region `DISPOSITION_RULE:code-review-contradiction-route` after line 359, plus the clean-definition sentence.**
   - Region. The bullet form below states the rules it must carry; item 6 supersedes its layout with a compressed form (the end marker sits inline after the last sentence, as in the sibling regions), and the shipped text in `code-review/SKILL.md` is authoritative.
     ```markdown
     <!-- DISPOSITION_RULE:code-review-contradiction-route start -->
     **A finding whose fix would undo a fix an earlier round applied is also a design question, in every round, staged commit-gate rounds included.** Write `plan-architect — consult` for it on the `Fix route:` line. Dispatch, verbatim relay, and the disagreement stop-and-ask follow the rule above, and the consult also carries the earlier finding and its fix. A finding against a site an earlier verdict already settled, or that two earlier rounds' fixes already rewrote, goes straight to the human as a blocking stop-and-ask, with no consult.

     The current text wins unless the finding names a defect, under a stated rule, that the current text actually has. One consult carries every such finding in the round and returns one verdict per finding:

     - *Keep current text* — resolved, and nothing is dispatched. Log it as `--disposition ADDRESS` with the verdict in `--rationale`. Never available to a finding the enforcement-invariant rule below covers.
     - *Apply this round's fix* — an ordinary ADDRESS row on the `code-writer` route.
     - *Cannot choose* — a blocking stop-and-ask to the human.
     <!-- DISPOSITION_RULE:code-review-contradiction-route end -->

     ```
   - **Consult or human.** A finding that would undo an earlier applied fix goes to the consult. A finding that contradicts a site a verdict already settled goes straight to the human. So does any finding against a site two earlier rounds' fixes already rewrote. That bounds A→B→C drift under a new rule each time, which never exactly reverts and passes the reviewer's different-rule test. If step 4 or a CI re-entry put clean passes between drift steps, the cap count would reset each time and miss it (row 30). The prior draft listed "flags a settled site" both as a consult trigger and as a human-stop case; this split removes that overlap.
   - **Staged rounds (CISO F1).** The route applies in staged commit-gate rounds too, where session context records earlier rounds' fixes. A single consult can settle *keep* on a finding outside the enforcement-invariant class. That is a residual the engineer accepts, and the design doc says so plainly (item 7).
   - **Ledger.** The existing slot already logs a finding it doesn't fix as `--disposition ADDRESS` with the verdict in `--rationale`. *Keep* uses the same shape, so `review-ledger.sh` needs no change (row 18).
   - **Clean definition (line 453).** Prepend, verbatim: "A finding DEFERred under the closed list, or settled *keep current text* by a contradiction consult, counts as resolved. " The DEFER half states today's behavior (row 12). The earlier draft extended this to the new-primitive slot's *Rejected* branch. That extension is dropped (see Out of scope).

5. **`code-writer.md` prose row.**
   - **Table row.** Append after line 129, as the table's last row: `| Comments, docstrings, durable-doc prose (docs, READMEs, skill/agent bodies) | `comment-discipline-reviewer` |`. Step 4 already tells `code-writer` to mine each reviewer file for its "review angles only", and that lands on `comment-discipline-reviewer.md`'s `## Core review angles` heading (verified, `comment-discipline-reviewer.md:38`). So the row needs to name no section.
   - **Step 5 addition.** Append after "When in doubt, read." (line 106), wrapped to the file's width: "Added or rewritten prose beyond a whitespace or typo fix always gets the prose row's read, even on one line: in a one-paragraph-per-line file, one line is a whole paragraph."
   - **Two baseline bullets.** Insert after line 63, the end of the "New behavior ships with a test" bullet, and before line 64's "As you write, let CLAUDE.md…" bullet:
     - "Never move text a skill or agent body loads at runtime into an edit-time-only file such as `REFERENCES.md`."
     - "If a change would push a file past its line cap, or alter a clause a test pins verbatim, and the dispatch prompt does not direct it, stop and report it under **Still uncertain** — do not trim elsewhere to make room, and do not edit the pin."

     The "does not direct it" clause keeps the guard from blocking a pin update the plan directs, which Dispatch 1 itself needs.
   - Keeping facts intact during compression is already stated in CLAUDE.md §Code Comments ("trim narration, never the fact") and in the reviewer's verbosity angle. It is not restated.

6. **Line budget.**
   - **`code-review/SKILL.md`.** The baseline is 495, not 483: `8bd5c082` landed a "Round-cap architect consult" section on `origin/main` after this row was verified, before implementation started. The item-2 block and the region as originally specified would add 16 lines, ending at 511, 11 over the cap. A `plan-architect` consult found a compression that preserves every fact — flattening both bullet lists into prose — landing the file at exactly 500. The line-263 and line-453 edits change text in place without adding lines. There is no room for another paragraph. If the wording needs one, the implementer stops and reports rather than trimming existing rules.
   - **RFR.** The Overview saves 1 line, step 0 saves 2, and the record and cap add 4. Unwrapping step 2's first hard-wrapped paragraph (lines 51–54) into one line saves 3. The file ends at 198.
     - The unwrap changes no word, and most of RFR is already one paragraph per line.
     - It buys line room, not byte room. RFR still grows by about the size of the two new paragraphs. The line cap stands in for size, and the plan discloses the unwrap as a reformat rather than calling it a compression (row 25).
   - **Overflow homes rejected:**
     - A runtime auxiliary file for `code-review`. The repo rule bars adding one to route around a length cap, and `check-skill-length.sh`'s path pattern would leave it uncapped (row 24).
     - Moving `code-review`'s Item ownership table into a runtime `ROUTING.md`, following plan-review's precedent. That needs changes to `limit_for` and the tests: a hook edit this plan otherwise avoids. It is also unnecessary once the record and cap move to RFR.

7. **New design-decisions entry: `docs/design-decisions/ready-for-review-fix-loop-convergence.md`.** It is warranted for three reasons:
   - The decision spans two skills and an agent.
   - It rejects five carry-forward homes and a hook-enforced cap.
   - Its rationale would sit at the wrong altitude inside a skill body.

   Skill text does not link back to it. What it covers is listed under Critical files.

**Alternatives set aside.**
- **Narrowed or delta re-review at step 3.** The engineer chose byte-exact. It was also rejected twice before at row level (`scope-code-review-delta-rounds.md:7`, `comment-discipline-once.md`).
- **Other homes for carry-forward:**
  - Session context only: lost at step 1's handoff seam, which is where long loops end up.
  - `review-ledger.sh`: keyed by session, its enum allows only ADDRESS|DEFER, and the round-3 doc says it does not keep pace with `/code-review` runs.
  - Fix-commit messages: no home for *keep* verdicts or clean passes, and they would push loop state into every stow consumer's public history.
  - A PR-body block: no PR exists before step 6 on a first run.
  - `SendMessage` continuation of the prior reviewer: already measured and rejected in `docs/cost-levers-considered.md`.
- **Record and cap in `code-review/SKILL.md`.** That is the wrong altitude, because both belong to RFR's loop. The budget also fails: roughly +30 lines against 17 of room.
- **A `status:` summary line in the record.** It is a second field that can contradict the rows it summarizes (CISO F5).
- **One merged consult for the cap and a contradiction.** The cap question depends on the contradiction verdicts.
- **Narrowing the CI fix to steps 3 and 7 (staff-product-engineer B7).** The engineer directed parity with a local failure (row 5). Skipping step 4 would leave the CI diagnosis's `/root-cause-analysis` invocation unaudited (row 14). Skipping step 5 would leave the PR body out of step with the new commit; step 5 is the gate's only body-vs-branch sync (`ready-for-review/SKILL.md` § "5. PR description (unconditional; warn + fix)").
- **Contradiction as a sixth DEFER criterion.** DEFER leaves the finding as outstanding work and never decides whether the current text has the defect.
- **The orchestrator deciding contradictions inline.** The orchestrator is the party whose dispositions oscillated, and the engineer chose architect-first.
- **A third limb inside `code-review-new-primitive-route`.** That region's lead sentence and follow-up branches are about heavier mechanisms. Merging would blur both rules, and the disposition-fidelity eval extracts each region as a standalone rule (row 13).
- **A hook-enforced cap.** See rows 6–7 and M6.
- **A per-session cap counter.** It resets at the handoff seam.

**Assumption ledger.**

Root: `/ready-for-review` step 3 correctly re-reviews the full cumulative diff after every fix, but its loop churns for five reasons:
- The loop-back text reads two ways, and the CI-watch arm states the wrong reading outright.
- Decisions from earlier cumulative passes are not carried forward.
- A finding that contradicts an earlier applied fix has no outcome except ADDRESS.
- `code-writer` never self-reviews prose.
- Nothing bounds consecutive dirty passes.

Givens:
- G1. Reviewer spawns are stateless, fresh `Agent` dispatches. They know about earlier rounds only through their prompt and the files it points them to. The harness imposes this dispatch model. Continuing a prior instance instead was measured and rejected in `docs/cost-levers-considered.md`, a decision outside this plan.
- G2. In consult mode, `plan-architect` returns advisory text and writes nothing. `plan-architect.md` owns that read-only charter.

Rows:
1. Keep the cumulative guarantee byte-exact: no narrowing, no marker change. `[engineer-verified]` `anchors: root`
2. Reading A: step 3 re-runs over the fixed bytes, and step 4 keeps "not on its own output". `[engineer-verified]` `anchors: root`
3. Contradictions:
   - The architect goes first, through the Fix-route slot.
   - The current text wins by default.
   - The verdict is binary.
   - The human steps in on can't-choose, disagreement, or a second contradiction.
   - Verdicts are settled, and *keep* counts as resolved.

   `[engineer-verified]` `anchors: root`
4. Include a cap, with the number taken from the round-3 doc, routed architect-then-human. Add the `code-writer` prose row to all dispatches. The comment-discipline deferral is out of scope. `[engineer-verified]` `anchors: root`
5. "A CI fix should be treated the same as a failed run of local tests." `[engineer-verified]` `anchors: root`
6. The round-3 hook cannot serve as the cap. `require-architect-consult.sh:80-83` and `log-reviewer-round.sh:78-81` both return early while this session's `.ready-for-review-active.d` marker is live, which lasts from step 0 until step 8 or step 1's deferral. So no reviewer spawn inside an RFR run is counted or gated. `[verified: both hooks; ready-for-review/SKILL.md:20-40,146-151]` `anchors: row4`
7. `log-reviewer-round.sh:123-140` writes the latch with no RFR bypass. So a contradiction or cap consult inside RFR permanently silences the round-3 gate for that branch. That is intended under the latch's documented meaning. `[verified: log-reviewer-round.sh; round3-plan-architect-consult-gate.md:95-102]` `anchors: row4`
8. The round-3 number is 2 recorded rounds, and the third distinct state trips the gate. It was measured on staged `/code-review` rounds per PR, not on cumulative passes. Applying it to cumulative passes is a transfer the engineer directed. The design doc names the population mismatch, so no reader takes "2" as validated for cumulative passes. `[verified: _lib.sh:3058-3067; round3 doc:5-13]` `anchors: row4`
9. The cap fires at the second consecutive dirty pass's fix decision, one step before the round-3 gate's own trigger point (the third review spawn). This is my reading of "number from the round-3 doc": it borrows the number, not the gate's timing. `[unverified — interpretation]` `anchors: row4`
10. Counting consecutive dirty passes since the branch's newest clean record is my reading of the session's "per RFR run" framing. It survives the handoff seam and fires no later than a per-session count would. `[unverified — interpretation; the engineer's words were "Include it"]` `anchors: row4`
11. The active marker bypasses `require-ready-for-review.sh`'s push gate outright (:16-22, :174). During a run, RFR's own prose is therefore the only thing that holds the every-pushed-HEAD guarantee. That includes the CI arm, which re-activates the same bypass. So the loop rule must read one way and be pinned by a test. `[verified]` `anchors: row2`
12. A DEFER-only review already counts as clean. RFR step 5 consumes a Deferred block that step 3 returned (:114-116), and that block exists only when step 3 passed with DEFERs outstanding. `code-review/SKILL.md:396` also expects commits to land with DEFERs open. `[verified]` `anchors: row3`
13. Building the contradiction route as a sibling region is my interpretation of "a new third trigger through the existing slot", not an override. If one region is required, the change is a move, not a redesign. `[unverified — interpretation]` `anchors: row3`
14. Today's text runs a step-2 failure's fix through a step-2 re-run (line 18), and "Run steps in order" (line 15) then carries it through steps 3–8. The own-output exclusion (lines 16–17) names only a step's own fixes, so step 4 re-runs. Line 200 instead lands a CI fix through the staged-diff gate and pushes: Reading B. Step 4 should re-run on the CI path for three reasons:
    - `skill-fidelity-report.sh` includes subagent invocations (:16).
    - The CI diagnosis runs `/root-cause-analysis` (line 198).
    - That skill is outside step 4's pipeline exclusion (line 104).

    `[verified: ready-for-review/SKILL.md:15-18, 104, 198, 200; skill-fidelity-report.sh:16]` `anchors: row5`
15. The main session's `Write` into `agent-reviews/` is ungated. `deny-reviewer-tree-mutation.sh` exits early for callers that are not review-only (:23-26), and `require-plan-review.sh:197-204` exempts `agent-reviews/`. `[verified: both hooks]` `anchors: row16`
16. A record found on the filesystem is the only carry-forward that survives step 1's seam, which fires right after a fix commit lands (RFR:18). Three facts support this:
    - `agent-reviews/` is gitignored through `info/exclude`.
    - It resolves per worktree, because `findings_path` is relative.
    - `/handoff` carries no findings paths and no ledger.

    `[verified: findings-path-suffix.sh:27-35; code-review/SKILL.md:299; grep of handoff/SKILL.md]` `anchors: root`
17. The slug is a 20-character hint and is not unique. Two branches sharing a prefix in one worktree would mix records. This is an accepted residual. `[verified: findings-path-suffix.sh:13-15]` `anchors: row16`
18. `review-ledger.sh` accepts only ADDRESS|DEFER (:165-166), and `session-marker-dashboard.sh:105-107` counts only those two. `[verified]` `anchors: row3`
19. The orchestrator writes the record itself, so the record is self-attested. That puts it on par with the `cumulative-review` marker write, whose self-attestation is already a named residual. Deriving clean and dirty from the rows leaves no summary field to contradict them. `[verified: ready-for-review-cumulative-diff-cache.md:13]` `anchors: row16`
20. Reviewers follow the ordered read, the anti-oscillation rule, and the unparseable-record rule, even though all three reach them only through the spawn prompt. `[unverified]` `anchors: root`
21. `plan-architect` returns one clean binary verdict per finding when asked for one. `[unverified]` `anchors: row3`
22. These changes reduce the number of dirty passes. `[unverified]` No measurement can see this loop yet: `review-loop-cost-audit`'s Stuck-loop verdict freezes at the last code-bearing commit, and every RFR fix commit is code-bearing. `anchors: root`
23. RFR is exactly 200 lines, and `code-review/SKILL.md` is 495 (the +16 budget this fed went stale when `8bd5c082` landed a "Round-cap architect consult" section on `origin/main` after this row was verified, moving the baseline up from 483). `check-skill-length.sh` denies a staged SKILL.md that is both over its limit and longer than its committed version. The limit is 200 by default and 500 for code-review. So RFR's net change must be ≤ 0, and code-review can grow by at most 5 lines. `[verified: check-skill-length.sh:5-12, 101-110; wc -l of code-review/SKILL.md at origin/main after 8bd5c082 = 495]` `anchors: root`
24. `code-review` has no runtime auxiliary file, only the edit-time `REFERENCES.md`. `.claude/rules/skill-and-agent-self-review.md` bars adding one "as a way to route around a file's length cap". `check-skill-length.sh`'s staged-path pattern matches only `SKILL.md` and plan-review's `ROUTING.md`, so a new file would be uncapped. `[verified: Glob; that rule file; check-skill-length.sh:101-110, 125]` `anchors: row23`
25. No test or hook reads RFR lines 22–24 or 51–54. Every RFR pin matches with whitespace collapsed, and the right-bound check looks only at a pin's trailing edge. So unwrapping those lines changes no test outcome. It also removes no words, so RFR's byte size still grows. `[verified: grep of test*.py and hooks for those phrases; test_skills.py:3914, 4397-4408]` `anchors: row23`
26. The disposition-fidelity eval is local-only and never runs in CI. No `disposition-cases.json` is committed. A new region therefore needs only `_EXPECTED_DISPOSITION_RULE_ANCHORS` updated. `[verified: evals/README.md:10-34; Glob; test_skills.py:1941-1948]` `anchors: row3`
27. `subagent-delegation/SKILL.md:169` already assumes Reading A: "an inline fix would skip the re-review `ready-for-review` step 3 runs on a dispatched one". `[verified]` `anchors: row2`
28. `skill-fidelity-reviewer` reports every `architect-consult` row as `[DISCLOSED]` whatever prescribed it, so the new triggers need no change there. `[verified: skill-fidelity-reviewer.md:129-137]` `anchors: row3`
29. Step 0 (lines 22–24) describes the loop as "step 3 fix → push → loop back to step 2". Only line 200's current text pushes before a re-review. Once line 200 changes, no path pushes before step 7 (lines 88, 106, 132). `require-ready-for-review.sh:19` and `docs/hooks.md:43` carry the same "fix → push → loop" shape. `[verified: grep]` `anchors: row2`
30. A *keep* verdict counts as resolved, so a pass whose only open finding got *keep* is clean and resets the cap's count. The cap is not expected to fire on GH-752's revert shape. It bounds consecutive dirty passes of new-finding churn. The contradiction route and the settled-site stop, extended to sites two earlier fixes rewrote, bound oscillation at one site. Named residual: churn of new findings across *different* sites, with dirty passes alternating with clean ones, escapes the cap by its reset rule. `[engineer-verified: "clean, as designed"; plan-architect consult]` `anchors: row3`

Mechanisms:
- **M1: loop-rule rewrite** (RFR Overview, step 0, line 88, line 200) plus two pins. Text only. `anchors: row2, row5, row11, row14, row29`
- **M2: carry-forward instructions** (code-review line 263 and the block after it, RFR line 85). They live in one home, the spawn prompt, not in about 10 reviewer agent bodies. `anchors: root`
- **M3: disposition record in `agent-reviews/`, written by RFR step 3.** It is heavier than carrying decisions inline because it adds a new persisted artifact. The lighter options fail:
  - Session context: lost at the seam (row 16).
  - `review-ledger.sh`: keyed by session, with an ADDRESS|DEFER-only enum (row 18).
  - Fix-commit messages: no home for *keep* verdicts or clean passes, and they publish loop state into every consumer's history.
  - PR-body block: no PR exists before step 6.
  - `SendMessage` continuation: see G1.

  `anchors: row16, row19`
- **M4: contradiction region.** The lighter options fail:
  - A sixth DEFER criterion never decides anything.
  - The engineer rejected inline orchestrator judgment.
  - Reviewer-side anti-oscillation alone can't decide a finding that passes the different-rule test.

  `anchors: row3, row13`
- **M5: clean-definition sentence at line 453** and its pin. `anchors: row3, row12`
- **M6: prose cap in RFR step 3, read from the records.**
  - Heavier option rejected: hook enforcement. The RFR bypass blinds the hook (row 6). Telling cumulative spawns from staged spawns inside RFR would need new hook state. And a cap that re-arms at each clean pass is what the round-3 doc rejects for its latch.
  - Lighter option rejected: a per-session count (row 16).

  `anchors: row4, row6, row8, row9, row10`
- **M7: `code-writer` prose row and two guard bullets.** `anchors: row4`
- **M8: design-decisions entry.** `anchors: root`
- **M9: three new test pins, the replaced scope pin, and the anchor set.** `anchors: row11, row23, row26`
- **M10: unwrap RFR lines 22–24 and 51–54 for line room.** This is lighter than every other place the overflow could go:
  - A runtime auxiliary file is barred by the repo rule, and nothing would cap it (row 24).
  - Moving the Item ownership table out needs hook and test edits.

  `anchors: row23, row24, row25`

## Critical files

**Dispatch split: two `code-writer` dispatches, run in parallel.** Their file sets don't overlap, and the design doc describes Dispatch 2's row from this plan, not from Dispatch 2's output. Both share the parent's feature worktree, so pass no `isolation: "worktree"`. Each prompt names this plan's path, its item numbers, and its verification command.

**Dispatch 1: skills, tests, and the design doc** (items 1–4, 6, 7):
- `claude-skills/skills/ready-for-review/SKILL.md`
  - Edits: lines 15–17, 22–24, 51–54 (unwrap only, no word changes), 85 (inside the SCOPE_RULE region), 88 plus the two new paragraphs after it, and 200.
  - Net change −2; the final file is 198 lines, and it must stay ≤ 200.
  - Leave these untouched: line 18 (pinned by `_PINNED_HALT_DEFERS_CLAUSE`), line 106, the CACHE_RULE region, and every HOOK_TEST_FIXTURE block.
  - Keep the literals `pr-diff-against-base.sh --record --diff-file` (test_skills.py:4262) and `~/.claude/scripts/marker.sh write cumulative-review` (test_enforce_marker_script_shape.py:36).
- `claude-skills/skills/code-review/SKILL.md`
  - Line 263: in-place edit.
  - The new block after line 264.
  - The new region after line 359.
  - Line 453: the prepended sentence.
  - Net +5 against the 495 baseline; the final file is 500 lines. Fit the text by compressing the new wording, never by trimming existing rules.
- `claude-skills/skills/tests/test_skills.py`
  - Replace the ready-for-review entry of `_PINNED_SCOPE_CLAUSES` (the block starting at :3867) with the new line-85 text.
  - Add `("code-review", "code-review-contradiction-route")` to `_EXPECTED_DISPOSITION_RULE_ANCHORS` (:1944), and change the comment's "four" (:1941) to "five".
  - Add five right-bounded pins next to `_PINNED_HALT_DEFERS_CLAUSE` (:4658). Each reuses `_raw_heading_section_text` and `_assert_pinned_clause_right_bounded`:
    - The Overview fix-loop clause, from "After a fix produced by step 2, 3, or 4" through "on its own output.", under `_READY_FOR_REVIEW_OVERVIEW_HEADING`. The new blank line after it is its right bound.
    - The CI "Land the fix" item, from "**Land the fix.**" through "through step 8.", under `## CI watch (out-of-band)`.
    - The line-453 paragraph, from "A finding DEFERred under the closed list" through "record it by running this command exactly once:", under `## Step — Record review completion`. The fenced command block that follows is its right bound.
    - Step 3's "**Cap.**" paragraph, from "**Cap.**" through "a blocking stop-and-ask to the human.", under the step-3 heading. It is the mechanism that bounds the loop, and every clause is a condition-to-action binding, so the whole paragraph is pinned. It is step 3's last paragraph, so the next heading is its right bound.
    - Step 3's "**Disposition record.**" clean/dirty definition, from "The pass is clean when" through "only as context.", under the same heading. It feeds the Cap's count of records newer than the newest clean one, and its last sentence, "The record authorizes nothing", is an authorization invariant with no other pin.
  - `_section_between`'s docstring (:4183–4186) says none of its headings is last in its file. Both new headings are, so correct that clause without naming files or headings and without asserting which sections are last, so the claim does not go stale when a section is appended. The docstring's count of headings the module bounds is also stale ("four") and is dropped or corrected. The end-of-file handling itself already works.
  - Test comments and docstrings for the new pins state the guarded regression in plain terms, with no review-round labels or plan-defined terms (for example "Reading A"). Each comment names the concrete regression instead, such as the fix commit's staged-diff review being treated as sufficient in place of a full step-3 pass.
- `docs/design-decisions/ready-for-review-fix-loop-convergence.md` (new)
  - Format per `.claude/rules/design-decisions.md`: provenance line `*2026-09-17.*`, with no "Formerly" clause.
  - Cover:
    - Reading A, with step 2 named and step 4's own-output exclusion.
    - Why the active-marker bypass makes RFR's prose the guarantee's only enforcement during a run.
    - The CI arm: a CI failure is a step-2 failure, and each CI fix costs a full step 2–8 pass. Step 4 audits the diagnosis's skill invocation.
    - The record:
      - Its home in RFR step 3.
      - Clean and dirty derived from the rows, with no summary line.
      - The five rejected homes.
      - Its self-attestation, on par with `ready-for-review-cumulative-diff-cache.md`'s second named residual.
    - The contradiction route: its default, why it is binary, and that it also applies in staged rounds. State plainly that a single-consult *keep* on a finding outside the enforcement-invariant class is a residual the engineer accepted, and that only the invariant class is excluded.
    - The cap:
      - The borrowed number, and the population mismatch behind it: it was measured on staged per-PR rounds, not cumulative passes, so "2" is not validated for cumulative passes.
      - Its timing, one step before the round-3 trigger, and why.
      - Why the round-3 hook can't carry it.
      - The latch side effect.
    - How often the new stops interrupt a run, and why that is acceptable. The new blocking stops fire only after the loop has shown it isn't converging (a dirty pass after an architect verdict), or when a finding contradicts a site a verdict already settled. They replace silent thrash, such as a round-4 fix reverting round 2's. Autonomous shipping's "without pausing" covers routine progress, not a loop that is failing to converge.
    - The keep/cap interaction, stated as ledger row 30 states it, including the named residual of new-finding churn across different sites.
    - The prose row.
  - Nothing about the new-primitive slot's *Rejected* branch.
  - End with a Sources list. No per-project figures.
- Verification: `.venv/bin/python3 claude/.claude/scripts/select-tests.py`, `.venv/bin/ruff check claude/.claude/ claude-skills/`, and `wc -l` on both SKILL.md files.

**Dispatch 2: `code-writer.md`** (item 5):
- `claude/.claude/agents/code-writer.md`: the table row after line 129, the step-5 sentence after line 106, and the two baseline bullets between lines 63 and 64. The file has no length gate (`check-skill-length.sh:125` matches only SKILL.md), and it is 148 lines now.
- Verification: `.venv/bin/python3 claude/.claude/scripts/select-tests.py`.

**Reuse:**
- `findings-path-suffix.sh` for the record's suffix and the ignore-list entry.
- The new-primitive slot's dispatch, relay, and disagreement rules, by reference.
- `_PINNED_SCOPE_CLAUSES`, `_raw_heading_section_text`, and `_assert_pinned_clause_right_bounded`.
- `comment-discipline-reviewer.md`'s `## Core review angles` as the prose row's source of review angles.

**Checked, not changed:** `marker.sh`, `_lib.sh`, `require-architect-consult.sh`, `log-reviewer-round.sh`, `require-ready-for-review.sh`, `review-ledger.sh`, `check-skill-length.sh`, `ci-watch.sh`, `skill-fidelity-report.sh`, `comment-discipline-reviewer.md`, `handoff/SKILL.md`.

## Verification

1. Run `.venv/bin/python3 claude/.claude/scripts/select-tests.py`. It should pick up `test_skills.py` (pins, anchor set, citation resolution), the design-decision file tests, and the agent-roster tests. If it misses one, that is a bug in select-tests' rule table, not a reason to widen the run by hand.
2. Run `.venv/bin/ruff check claude/.claude/ claude-skills/`.
3. Check line counts with `wc -l`: ready-for-review should be 198 and code-review 500. `check-skill-length.sh` enforces the caps again at commit.
4. Grep checks:
   - In `ready-for-review/SKILL.md`, "own output" appears only in the Overview and in step 4's own paragraph.
   - In `ready-for-review/SKILL.md`, "loop risk", "step 3's pattern", and "fix → push" no longer appear.
   - `code-review-dispositions` appears only in RFR step 3, the single home of the path.
   - `status:` appears in neither SKILL.md's new text.
5. Run `/skill-review` on both SKILL.md files (hook-enforced). Its behavior test should walk these scenarios:
   1. A step-3 fix lands. Step 2 re-runs, step 3 runs a full pass on the cache miss, and step 4 re-runs.
   2. A step-4 fix lands. Step 3 runs a full pass, and step 4 is skipped unless step 3 produced a new fix.
   3. A CI failure. The marker is re-activated and the fix is handled as a step-2 failure's fix. Steps 2–8 run, and step 8 writes the completion marker and deactivates.
   4. A pass-2 finding moves a site back toward pass-1 wording. It goes to the contradiction consult, and *keep* resolves it. With nothing else outstanding, the pass is clean and the cache marker is written.
   5. The same contradiction, but in the enforcement-invariant class. A *keep* return does not resolve it, so it stays ADDRESS or becomes a blocking stop-and-ask.
   6. A finding against a site a verdict already settled. The human is asked, and no second consult runs.
   7. A second consecutive dirty pass. The cap consult runs before its fix dispatch, and its answer is recorded as a cap row. The next dirty pass in the run goes to the human.
   8. Combined: the second consecutive dirty pass also carries a contradiction whose verdict is *apply*. The contradiction consult runs inside `/code-review`, then the cap consult runs. The record holds both the contradiction's Outcome and a cap row.
   9. A stale, forged, or unparseable record. If a record marks a site settled while the current text still has the defect, the reviewer flags the defect and names the record. An unparseable record is named in the findings and counts as dirty for the cap.
   10. The cap consult returns *stop*, returns text that matches neither verdict, or the orchestrator disagrees with a *proceed*. Each is a blocking stop-and-ask, no fix is dispatched, and step 8 withholds the completion marker. The record holds a cap row (Outcome `no verdict` in the neither-verdict case), so the next dirty pass in the run goes to the human.
   11. Drift at one fix-only site, A→B→C under a different rule each time, with a clean pass between drift steps. The third touch goes to the human without a consult, even though the cap count reset.
6. Run `/agent-review` on `code-writer.md`.
7. Run `/code-review`, then `/ready-for-review`. Its cumulative pass is the first real run of the new loop and writes the first real record; check that record's shape by hand.
8. Before committing, confirm the new design doc has no figure broken out per project, per account, or per engagement. Any tooling figure must meet `docs/private-project-redaction.md` § "Publishing a tooling measurement".

## Out of scope

- **Timing of the comment-discipline deferral.** The engineer's decision.
- **Any narrowed or delta step-3 review**, and any change to `marker.sh`, `_lib_cumulative_diff_hash` or `pr-diff-against-base.sh`.
- **Hook enforcement of the cap**, and removing the RFR bypass from the round-3 gate or recorder (row 6). Also out: honoring `round_consult_round2_pilot` in the prose cap.
- **Counting the new-primitive slot's *Rejected* verdict as resolved** (`code-review/SKILL.md:355`). "Heavier mechanism rejected" does not mean "defect absent". Closing that gap needs its own rule, such as re-routing the finding to a lighter fix, not a clause borrowed from the contradiction route.
- **Records for staged-diff commit-gate rounds.** Session context carries them. A staged round's adjustment to a fix before that fix's commit goes unrecorded, and that residual is accepted.
- **Adding a KEEP value to `review-ledger.sh`** and its dashboard count.
- **CI watch's "Offer, don't act" confirmation** (RFR:199). It stays unchanged: the engineer's direction governs how a CI fix lands, not whether the agent starts one without being asked.
- **Re-watching CI after a gate push.** `ci-watch.sh` records its launch SHA (:117–122), and the gate launches the watch only at steps 1 and 6. So no later push is watched, whether from step 7 or from a CI fix. This predates the plan and affects the whole gate.
- **Who fixes a step-2 failure.** Line 18 says to dispatch `code-writer`; line 56 says the parent edits inline. This predates the plan. The CI arm keeps its `code-writer` dispatch.
- **The "fix → push → loop" wording** in `require-ready-for-review.sh:19` and `docs/hooks.md:43` (row 29). The same stale shape, outside this change's files. The stale "removed at step 7" header line in `require-ready-for-review.sh` is in the same position: noticed, not touched.
- **Moving `code-review`'s Item ownership table into a runtime `ROUTING.md`.** Not needed once the record and cap live in RFR.
- **Where a "finding surviving a second dispatch" goes** (`subagent-delegation/SKILL.md:179`).
- **The "staff-\* reviewer angles" wording** in the global CLAUDE.md and in `subagent-delegation` describing `code-writer`'s self-review. It becomes slightly stale once the prose row exists.
- **Building a convergence measurement** that can see this loop.
- **Raising the skill line caps.**
