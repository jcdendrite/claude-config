# Comment-discipline False-angle fix rule

## Context

Goal: cut the review rounds spent on comment and docstring prose by changing what the False-claim angle's fix rule tells a reviewer to write, so a fix is the narrowest true statement with what the restating angle would delete removed, and a claim with nothing left is deleted instead of restated again.

Ask: "yeah I think that sounds good" — the engineer's reply to starting `/plan-it` for the False-angle fix-rule edit plus the `code-review` item-5 alignment (the replay study's "arm 10"). The engineer also ruled that a docstring fact "that can easily be discovered by reading the code" warrants a finding; the bar is discoverable by reading the code, not visible on the adjacent line. Later the engineer asked to widen the False block's restating pointer to that bar: "I'd like it in this change, and I'd like it to be measured." After the first cell runs the engineer said "I think the wording suffers from defensive layers", asked "Let's measure Fable's edit as another arm, yeah?" and, once that arm was measured, confirmed "Yes let's go with your recommendation", which was to ship Fable's single bullet in place of arm 10's two.

Why now: a replay study (3 runs per arm over a 61-unit set and two fresh 45-unit samples) compared the current rule (arm 0) with arm 10, which replaced the fix bullet with two bullets. Arm 10 kept the claim (a NO-FINDING or KEEP-CORRECT verdict, where KEEP-CORRECT may reword it) on fewer of the 28 real findings, 8, 12, 6 against 12, 19, 13.

The verify table holds 30 claims: 24 verified true, 5 partly false and 1 unverifiable. Arms 0, 10 and 11 deleted none of the 30 in any run.

Arm 7 also changed CLAUDE.md line 116 and the reviewer exemptions. It deleted claims in 9 of its 90 original-set unit-runs, 6 of them verified-true. The study found no benefit for those changes over arm 10.

The wording that ships is arm 11, one bullet. A separate Fable review wrote it after the engineer objected that arm 10's two bullets layer a second rule on the first. Arm 11 is arm 10's rule files with that one bullet and Edit 1b's pointer change.

Arm 11 kept the claim on 8, 8, 9 of the 28 real findings over three runs. In 24 of those 25 verdicts the claim stayed with a possible reword (KEEP-CORRECT), so the measure is not "no finding raised". It also deleted outright 4, 5, 4 units that classifier A labels as needing a shorter local version, against 1, 1, 1 for arm 0 and 3, 2, 1 for arm 10. Three runs per arm make every comparison here directional, not statistical.

Sources: the scored result files for arms 0 and 10 are under `agent-reviews/replay-v2/out/` in the PR 2 worktree. Arm 11's are under `~/.local/state/comment-study/replay-arm11/out/`. The scorer is `~/.local/state/comment-study/score_replay_v2.py`, and the replay prompt with each arm's run record is `~/.local/state/comment-study/REPLAY-PROMPT.md`. These study paths and worktrees are local to the author's machine and unpublished, so later mentions of them in this plan are not reader-resolvable.

The reviewer edit and the item-5 edit are below. Edit 1b, the pointer reword at `comment-discipline-reviewer.md:217`, is in Approach.
- `claude/.claude/agents/comment-discipline-reviewer.md`, the False-angle bullet "The fix is the statement the subject supports." (line 212 at this branch's base). Replace it with one bullet, quoted under Edit 1 in Approach.
- `claude-skills/skills/code-review/SKILL.md` Output format item 5 (line 254): change "the fix names the lines to delete" to "names the text to delete".

Out of scope: `claude/.claude/CLAUDE.md` lines 116 and 118, the reviewer's exemptions, and any change to the review loop itself.

## Approach

The change is Edits 1 to 4 below, measured per Re-measure. The re-run before commit is how this plan meets the honor-system rule in `docs/design-decisions/false-or-unsourced-claim-checks.md` § "The guard's limit". The changelog entry is there because every stow consumer's `/code-review` changes on `git pull`.

**Edit 1, the reviewer (M1).** This line replaces line 212 whole. Keep the two-space indent, and keep the bullet one unwrapped line, as arm 11 ran it (row 5). Line 168 shows the file already holds unwrapped bullet lines.

```markdown
  - The fix is the narrowest statement the subject supports, less what Prose restating the code would delete. Nothing left, delete the claim.
```

The bullet adds no rule of its own. It sends the question of what to cut to the Restating angle, which already owns it, so a False-angle fix and a Restating fix cannot disagree.

**Edit 1b, the restating pointer (M8).** At `comment-discipline-reviewer.md:217`, change "repeating what the code beside it shows" to "repeating what reading the code shows". Nothing else in the pointer changes. The Restating angle it routes to (line 148) says "what the code already shows", so the pointer then matches the angle and the engineer's bar. Verification step 3a runs it on both the base body and the edited body, but its fixture shows the claim's falsity on the adjacent line and both bodies pass, so the pointer wording is not measured in isolation. The acceptance cell's contrast with the base body also includes Edit 1.

**Edit 2, item 5 (M2).** The sentence after the edit reads: "Instead, for prose that restates the code, the fix names the text to delete (CLAUDE.md §Durable text)." Nothing else in item 5 changes (rows 10, 11).

**Edit 3, the record (M5).** `docs/design-decisions/false-or-unsourced-claim-checks.md` gains three things:
- A section `Why a false claim's fix deletes or narrows it`, placed after the section on unsettleable claims. It states three facts:
  - A rewritten claim is modified prose, which the reviewer judges again in the next round (row 19). So the fix is the narrowest true statement with what the restating angle would delete removed, and a claim with nothing left is deleted, not corrected.
  - The Restating angle already owns the test for what to cut, so the False angle's fix defers to it. A separate rule for appended exceptions or for what the reader needs would restate that test.
  - The replay comparison that chose this wording is in `.claude/plans/comment-rule-false-angle-fix.md` § "Context". The record carries arm 11's replay figures, and the comparison with arms 0 and 10 goes in the PR body (rows 23-24).
- A dated `###` subsection at the end of § "How the wording was measured", after the "Unmeasured and caveats" list. It holds the re-run's date, model, K and per-cell rates in the shape of "Measured rates", and any K=10 re-run or unmet bar. The 2026-11-05 subsection stays as written, because it records a past measurement (row 16).
- This plan's path as a new line in § "Sources".

No sentence in the record compares the rule with its prior wording, per CLAUDE.md §Durable text.

**Edit 4, the changelog (M6).** Add one bullet under `## [Unreleased]` → `### Changed`, in the shape of the entry at `CHANGELOG.md:19-23`:
- A bold headline saying that `comment-discipline-reviewer`'s fix for a false claim is now the narrowest statement the subject supports, less what the restating-the-code check would delete, and a claim with nothing left is deleted.
- A sub-bullet saying that `/code-review` output item 5 now names the text to delete, not the lines, for prose that restates the code.
- "The change is live on `git pull`." (row 22).

**Re-measure (M4).**
- Re-run the angle cells from `.claude/plans/prose-claim-truth-check.md` § "Verification", step 3, against this worktree's edited reviewer by absolute path. Verification step 3 below gives the run details.
- Do not re-run the tripwire cells (that plan's step 4): neither edit touches the tripwire bullet (row 17).
- Report a cell that still misses its bar after the K=10 rule to the engineer with the rates before commit.
- The shipped bullet and the pointer line must equal Edits 1 and 1b byte for byte, checked by `cmp` against the measured body (Verification step 2). A reword of either goes to the engineer first and voids every measured rate, because it swaps measured wording for unmeasured wording. That covers a reword from `/agent-review` or `/code-review`. The engineer chose to reword once, after the first runs, and the record's rates are for the reworded body only.
- The runs the record cites are the ones already made on the measured body, listed in row 37. They are not re-run after the edit. The measured body is a scratch copy at `~/.local/state/comment-study/fable-arm-reviewer.md`, and the replay body is `~/.local/state/comment-study/replay-arm11/rules/`.
- Edit 1b is applied before any cell runs, because the precedence cell exercises the pointer. That cell also guards the pointer's adjacent case.
- The cells score whether a finding exists, which passes on the old wording too, so each run's fix kind is also scored. A fix is a deletion or the narrowest true statement. A fix that restates the corrected full set of an omitted list, such as naming every caller a claim of "only X" left out, is scored as a narrowing, because no rule in either body separates it from one. The cells therefore cannot show the fix-kind shift. The replay is the only evidence for it.
- Each run quotes the pointer sentence it read and the `NARROW:` header line. The edited body has no "Narrow a claim" bullet, so its runs write `NARROW: none` and quote "reading the code shows". The base body's runs quote "beside it". A run whose quotes do not match its arm is not counted.
- A subagent in this environment may lack `Grep` and `Glob`. The run prompt then allows read-only `Bash` and requires a `LISTING:` header line naming the tool used. A run on a cell that makes claims about a directory's contents, the must-flag cell, is void without that line, because a run that cannot list the directory misses the claim. Other cells need no listing, and their runs count without it.
- A miss on a step 3 cell cannot be attributed to the edit, because those cells have no control arm and the K=5 pass chance is noisy. The engineer judges a reported miss.
- A step 3a cell result is credited to Edit 1b only where the base body fails the bar on the same diff in the same session.
- **Bars and scoring changes.** The original bars were: precedence cell, restating with deletion in at least 8 of 10 and a False finding in at most 2 of 10; negative cell, any finding in at most 2 of 10. After the first runs the architect advised scoring the precedence cell by fix kind with one finding counted per site, and the negative cell by Restating findings only. The engineer approved (row 38). The record carries both the raw and the rescored rates. Fixtures were also repaired after the first runs, and the PR body says so (row 38).

Alternatives set aside:
- **Arm 7's wider change** (CLAUDE.md lines 116 and 118, the reviewer's exemptions). It deleted verified-true claims, and the study found no benefit for it over arm 10 (Context). It also stays out of scope.
- **Arm 10's two bullets.** The second bullet patches a gap the first leaves, and its exception clause is an exception to that patch. The engineer objected to the layering. Arm 11 met every cell bar arm 10 met and kept the claim on about as many real findings on the replay (directional at three runs).
- **A pointer clause naming "a function it calls in the same file".** It is tuned to one fixture, a one-line delegation, and changes what counts as restating for every comment about a calling function. Nothing measures it outside that fixture.
- **New fixture cells for the fix's shape.** A synthetic cell would measure less than the replay did on real units. The existing cells guard what the edit could regress without anyone noticing: detection, and precedence against the restating angle.
- **The re-run's rates in the PR body only.** The record's only rates would then describe a wording that no longer ships, and nothing durable would show the honor-system rule was met. The repo's precedent for a reworded angle writes the rates into the artifact that holds the measurement (row 18).
- **Broadening item 5 to name a False-angle deletion.** It isn't needed: the Unsourced arm's "deletes it" already sits alongside item 5 as written (row 11).
- **Matching the tripwire's fix wording.** No measurement covers it, and changing it re-opens the tripwire's own cells (Out of scope).

No mechanism here is heavier than a prose edit. M4 reuses the repository's existing cells and bars and builds no new machinery. Two lighter options fail. Skipping the re-run breaks rows 13-14. Re-running only the must-flag cells would drop the precedence cell, which the new deletion arm is most likely to move, because that arm overlaps the restating angle's deletion fix.

### Assumption ledger

**Root:** The False angle's fix rule tells a reviewer to replace a false claim with "the statement the subject supports". That replacement is new modified prose, so the next round judges it again, and a fix that restates more of the subject can draw a further finding. The Restating angle already decides what prose to cut, so the fix rule should defer to it, not add a second test.

**Givens:**
- **G1.** A reviewer's output varies from run to run, so a wording's effect shows only over repeated runs. Reason: the vendor owns the model's sampling, not this repository. [verified: the Context's three arm-10 runs left different counts]
- **G2.** A by-name dispatch of `comment-discipline-reviewer` runs the body stowed from the main checkout, not this worktree's edited body. Reason: the stow layout fixes where `~/.claude/agents/` resolves. [verified: repo `CLAUDE.md` "Working in this repo" ("`claude/` is stowed into `$HOME`"); `.claude/plans/prose-claim-truth-check.md:197`]

**Rows:**
1. [engineer-verified: "yeah I think that sounds good"] The change is the False-angle fix-rule edit plus the `code-review` item-5 alignment. The `Ask:` line records this reply.
2. [engineer-verified: "I think the edge calls are all correct"] The replay study's calls on edge-case units stand as scored. This plan does not re-judge which findings were real.
3. [engineer-verified: "that can easily be discovered by reading the code"] A docstring fact matching that quote warrants a finding, per the `Ask:` line.
4. [verified: `claude/.claude/agents/comment-discipline-reviewer.md:212`; a repo-wide grep for "statement the subject supports" outside `.claude/plans/` hit only that line] Line 212 is the only home of the current fix rule.
5. [verified: `~/.local/state/comment-study/replay-arm11/rules/comment-discipline-reviewer.md:200` and `~/.local/state/comment-study/fable-arm-reviewer.md:212`] The one replacement bullet reads identically in the copy the replay arm ran and in the copy the cells ran. It is one unwrapped line in both. The bullet is the wording of a Fable review of arm 10, relayed through a subagent report. The engineer adopted it with "Yes let's go with your recommendation".
6. [verified: the study worktree's `agent-reviews/replay-v2/arm10/` holds only `comment-discipline-reviewer.md`, `code-writer.md` and `CLAUDE.md`; its `code-writer.md:82-86` and `CLAUDE.md:116` read as the base's] Arm 10 differs from the base only in the reviewer, and the replay varied no `code-review/SKILL.md` text. The item-5 edit is therefore an unmeasured wording alignment.
7. [verified: arm 11's copy puts the bullet at line 200, and the base has the bullet at line 212] The replay ran the bullet in an older reviewer body, from before the base gained 12 lines above the angle.
8. [verified: `diff` of `~/.local/state/comment-study/replay-arm11/rules/comment-discipline-reviewer.md` against `~/.local/state/comment-study/fable-arm-reviewer.md` shows only the input-handling lines at 20 to 32 differing; `diff` of the base copy against the cell body shows only the two edited lines] The replay's reviewer body and the shipped body hold the same angle text. They differ in the input-handling section. The result is therefore likely to carry over, but the replay ran no shipped body. The cell runs on the real edited body guard detection and precedence.
9. [verified: `claude-skills/skills/code-review/SKILL.md:254`; a repo-wide grep for "lines to delete" hit only that line and `CHANGELOG.md:108`] Item 5 is the only live site of the phrase. `CHANGELOG.md:108` is a preserved record and stays as written (Axis 3).
10. [unverified] The Context describes Edit 2 as changing "the fix names the lines to delete" to "names the text to delete". This plan reads that as replacing "lines" with "text" and keeping "the fix", because dropping "the fix" leaves the sentence without a subject. The reading is plan-architect's, not the engineer's.
11. [verified: `comment-discipline-reviewer.md:213-215`; `code-review/SKILL.md:254`] The Unsourced arm's fix already "deletes it", alongside item 5's "the replacement text itself" clause. A False-angle deletion therefore needs no further change to item 5.
12. [verified: `docs/design-decisions/prose-restating-code-is-deleted.md:5,13`] Line 5 says item 5 carries the restating remedy. Line 13 says item 5 "names a deletion for restating prose". Both stay true after Edit 2, so that record needs no edit.
13. [verified: `docs/design-decisions/false-or-unsourced-claim-checks.md:59`] Re-running the cells after any edit to the angle's block is an honor-system rule, because no committed fixture or test guards the wording. Edit 1 falls inside that block.
14. [verified: `.claude/rules/skill-and-agent-self-review.md` § "When editing a skill or agent, run the skill on its own diff"] Rewording near a fixture-verified clause needs a fresh fixture pair before staging. The fixtures stay in a scratchpad.
15. [verified: `.claude/plans/prose-claim-truth-check.md:196-221`] Step 3 of that plan's Verification holds the angle cells:
    - one scratch tree, with a must-flag diff and a must-pass diff;
    - five must-flag cells, one precedence cell and seven must-pass cells;
    - each diff reviewed K=5 times by a by-path `general-purpose` Sonnet reviewer;
    - the bars, plus the K=10 re-run rule.
16. [verified: `false-or-unsourced-claim-checks.md:37-55`] The 2026-11-05 run on Sonnet 5.5 met every angle bar with no K=10 re-run. It is a dated measurement.
17. [verified: `code-review/SKILL.md:79-85`; `.claude/plans/prose-claim-truth-check.md:222-237`] The tripwire is its own Step 1.5 bullet with its own cells. Its fix "takes the new value from the changed code or deletes prose restating the code". Neither edit touches it.
18. [verified: `docs/design-decisions/paragraph-readability-review-angle.md:54-55`] After a fixture-verified angle is reworded, the repo's precedent writes the measured rates, date and model into the artifact that holds the measurement.
19. [verified: `comment-discipline-reviewer.md:192`; `false-or-unsourced-claim-checks.md:9`] The reviewer judges added or modified prose, so it judges a rewritten claim again in the next round.
20. [verified: `comment-discipline-reviewer.md:148-151,217-219`] The restating angle's bar is "what the code already shows", and the False block routes a restating comment to that angle. Under either angle the claim draws a finding, so row 3 holds. Edit 1 touches none of those lines. Edit 1b changes one phrase on line 217, "beside it", and nothing else in them.
21. [verified: `CHANGELOG.md:3,19,24`; `.claude/plans/comment-discipline-once.md:121`; `.claude/plans/prose-density-reviewer-gaps.md:280`] The changelog documents "All notable changes". Past behavior changes to `comment-discipline-reviewer` and `/code-review` each got an `[Unreleased]` entry.
22. [verified: repo `CLAUDE.md` "Working in this repo"; `.claude/plans/comment-discipline-once.md:121`] Both edited files sit in stow packages, and an edit to an existing stowed file goes live on `git pull`.
23. [verified: all 45 unit headers in the study worktree's `agent-reviews/replay-v2/out/fresh2-units.md` name paths in this repository] The second fresh sample comes from this repository's own files.
24. [verified: the 31 finding units' sites in the 61-unit set name paths in this repository, and the 23 commits in the two fresh-sample manifests all exist as commit objects in this repository] The finding units and the commit manifests come from this repository. The check did not confirm the 23 commits are all on origin/main. [unverified] For the 30 verify-table claims, a reviewer found 14 whose file column names a doc and 3 (P34, P35, P39) that quote superseded sampled text, so "name paths in this repository" holds for the claims only loosely. [unverified] Nothing checked that the units' source material was produced on one `CLAUDE_CONFIG_DIR` account, which `docs/private-project-redaction.md` § "Publishing a tooling measurement" requires.
25. [verified: `.gitignore:41` lists `agent-reviews/`] The study's inputs and results are gitignored, and they sit in another worktree.
26. [verified: `claude/.claude/scripts/select-tests.py:646`] A `CHANGELOG.md` change maps to no test.
27. [engineer-verified: "Keep both (Recommended)"] The plan may touch `CHANGELOG.md` and `docs/design-decisions/false-or-unsourced-claim-checks.md`, which the Ask did not name. The engineer selected this label when asked whether to keep or trim those two files.
28. [engineer-verified: "Yes, all three"] The engineer confirmed saying the quotes in rows 1 to 3: "yeah I think that sounds good", "I think the edge calls are all correct", and "that can easily be discovered by reading the code".
29. [engineer-verified: "I'd like it in this change, and I'd like it to be measured."] The pointer's wording change belongs in this change (Edit 1b) and gets its own measurement (step 3a).
30. [verified: `git log -L217,219` shows one commit, `fcadb7c4` (#1399); `.claude/plans/prose-claim-truth-check.md:35,96`; `claude/.claude/CLAUDE.md:116`] The pointer's "the code beside it shows" came from that plan's paraphrase of CLAUDE.md line 116, which says "non-obvious from the code" and never says "beside". The pointer has not changed since.
31. [verified: row 37's acceptance runs] "Reading the code" draws a Restating finding on a true non-adjacent fact in 5 of 10 runs on the shipped body, against 0 of 5 on the base body in the same session. That is a measured shortfall against the engineer's ruling (row 3), and the contrast with the base is the only evidence of the pointer and bullet's reach. No adjacent true-claim cell exists, so the 5 of 10 is not compared with an adjacent rate.
32. [engineer-verified: "Drop 'easily' (Recommended)"] Edit 1b's pointer wording omits "easily", as the Restating angle's "what the code already shows" does. The engineer selected this label when asked whether "easily" should bound the pointer.
33. [engineer-verified: "I think the wording suffers from defensive layers"] Arm 10's two bullets are a layered wording, and a single bullet replaces them.
34. [engineer-verified: "Let's definitely do the replay sample."] The single bullet is measured on the replay too, not on the cells alone.
35. [engineer-verified: "Yes let's go with your recommendation"] The change ships the single bullet and Edit 1b, with the measured misses stated. The recommendation was the session's own, made after the cell and replay results.
36. [verified: `~/.local/state/comment-study/replay-arm11/out/` scored with `score_replay_v2.py` classifier A and B] Arm 11 kept the claim on 8, 8, 9 of 28 real findings (NO-FINDING or KEEP-CORRECT, the scorer's "left alone" count) and deleted none of the 30 verify-table claims in any run. It deleted outright 4, 5, 4 units that classifier A labels as needing a shorter local version, against 1, 1, 1 for arm 0 and 3, 2, 1 for arm 10. Under classifier B it deleted claim-needed units 2, 2, 1 times, against 0 for arm 0 and 1, 1, 0 for arm 10. The deletion counts are a cost the scorer does not credit as one. The measured claim is the shift in fix kind. Nothing measures the goal of fewer review rounds.
37. [verified: the cell findings under `~/.local/state/comment-cells-2026-11-12/out/fable-*`, 2026-11-15, Sonnet] These are the rates for the shipped body:
    - **Must-flag:** 5 of 5 on each of five claims, with the `LISTING:` line on every run.
    - **Precedence:** restating with deletion 5 of 5 and a False finding 0 of 5.
    - **Must-pass:** 0 of 5 flagged on each of seven items.
    - **Pointer (false docstring):** deleted in 7 of 7 runs. Base body 5 of 5. No routing effect of Edit 1b is shown, because both bodies pass and the fixture's falsity is visible on the adjacent line.
    - **Negative:** any finding 1 of 5, an Unsourced one. Restating 0 of 5. Arm 10 drew an Unsourced finding in 5 of 10 and the base in 0 of 5.
    - **Acceptance (true docstring on a delegating function):** deleted in 5 of 10 runs, against a bar of 8 of 10. The base body in the same session deleted 0 of 5. Earlier-session runs gave base 0 of 5 and arm 10 1 of 5.
38. [engineer-verified: "Re-run and rescore as advised (Recommended)"] The precedence cell is scored by fix kind with one finding per site, and the negative cell by Restating findings only, with raw rates kept in the record. The architect advised the change after the first runs, so it is a post-hoc scoring change. For the shipped body the rescoring changes no result: it meets the original bars as well. The trigger was arm 10, whose negative-cell Unsourced findings, 5 of 10, missed the original bar of at most 2 of 10. The negative fixture is itself an Unsourced-arm case, an unsettleable external claim with no source, so the rescored cell guards Restating over-reach only. Fixtures were also repaired after the first runs: the first acceptance docstring was not a true claim, and a line that restated the contradicting fact was removed from the must-flag tree.
39. [engineer-verified: "I wouldn't defer."] The security reviewer's finding that the record omits whether limit notes fall under "nothing left, delete the claim" is addressed in this change, not deferred. The quote covers only that the finding is addressed here.
40. [engineer-verified: "Measure it first"] The question whether a partly false limit note (a gate's "does NOT catch" scope) draws a deletion fix is answered by a cell pair on the shipped body before the record says anything about it. The engineer chose this over stating the question as unmeasured and over Fable's narrowing sentence.
41. [verified: the five partly false verify-table claims S53, P26, P35, P38, P39 are not limit notes, per a reviewer's reading of the study files; `replay-arm11/out/` shows PR2-13, the nearest negation-shaped unit, deleted in 2 of 3 arm-11 runs] No measured evidence before step 3c supported the claim that the Restating angle leaves a limit note and the False fix narrows it. Row 36's 0 of 30 deletions is the same for arms 0, 10 and 11 and holds no limit note. The sentence Fable proposed is therefore not written.
42. [engineer-verified: "Say repo-only, flag account scope (Recommended)"] The record states the replay's units come from this repository's tracked files and review findings and that the study's inputs are local and unpublished, and claims nothing about accounts. The PR body states single-account scope was not checked. The option description is the session's own proposal.
43. [verified: `~/.local/state/comment-cells-2026-11-12/out/limit-*.md`, 2026-11-15, Sonnet 5.5, K=5 per diff per body, scored by each run's stated primary fix] On a true "does NOT catch" docstring the shipped body deleted it in 4 of 5 runs and the base body in 2 of 5. On a partly false one the shipped body deleted it in 2 or 3 of 5 and the base body in 1 to 3 of 5, depending on how a run that names both a rewrite and a deletion is scored: `limit-false-shipped-3`, `limit-false-base-2` and `limit-false-base-5` do, and `limit-false-shipped-2` and `-5` end on a deletion. The scoring rule is the run's own first stated fix, and a run that states two at once is reported as ambiguous. No difference is established for the partly false note. The true note's 4 of 5 against 2 of 5 is the larger observed difference, directional at K=5. The two bodies differ in Edit 1's bullet and Edit 1b's pointer and no arm isolates either, so the cause is not known. The fixture's true note names a previous commit that the function's signature can show is absent, and runs split on whether that makes the note restating. A true note of a limit that no parameter or branch shows was not measured. The engineer saw the borderline results before choosing (row 44).
44. [engineer-verified: "Ship, record the rates plainly (Recommended)"] The change ships as measured, and the record states both limit-note rates and makes no claim that the reviewer leaves a limit note alone. The PR body names the true-note over-deletion beside the acceptance miss. The engineer chose this over reverting Edit 1b and over adding a limit-note exemption. The option description is the session's own proposal.

**Mechanisms:**
- **M1. Replace line 212 with the one bullet, word for word and unwrapped.** anchors: root, row1, row2, row3, row4, row5, row7, row20.
- **M2. Item 5 reads "the fix names the text to delete".** anchors: row1, row6, row9, row10, row11.
- **M3. No edit to `prose-restating-code-is-deleted.md`.** anchors: row12.
- **M4. Re-run the angle cells at K=5 on the edited body by path, per Re-measure.** anchors: row8, row13, row14, row15, row17.
- **M5. The record gains the fix rule's reason, a dated rates subsection and a Sources line.** anchors: row16, row18, row19, row23, row24.
- **M6. One `[Unreleased]` changelog entry.** anchors: row21, row22, row26.
- **M8. Reword the restating pointer at line 217 and run the before/after pointer cell and the acceptance cell on it. Neither isolates the pointer, because the pointer cell's falsity shows on the adjacent line and the base body also lacks Edit 1.** anchors: row3, row20, row29, row30, row31.
- **M10. Measure a true and a partly false limit note on the shipped body (step 3c) and record the rates in the dated subsection.** anchors: row3, row19, row39, row40, row41, row43, row44.
- **M7. Nothing from the study or the cell scratchpad is staged.** anchors: row14, row25.
- **M9. Measure the single bullet on the replay as its own arm.** anchors: root, row6, row33, row34, row36.

## Critical files

- **Modify** `claude/.claude/agents/comment-discipline-reviewer.md`: line 212 (M1) and line 217's pointer wording (M8), nothing else.
- **Modify** `claude-skills/skills/code-review/SKILL.md`: line 254 only (M2).
- **Modify** `docs/design-decisions/false-or-unsourced-claim-checks.md`: the new reason section, the dated rates subsection and one Sources line (M5).
- **Modify** `CHANGELOG.md`: one bullet under `## [Unreleased]` → `### Changed` (M6).
- **Not modified:** `docs/design-decisions/prose-restating-code-is-deleted.md` (M3) and the entry at `CHANGELOG.md:108`.
- **Reuse:**
  - The angle cells in `.claude/plans/prose-claim-truth-check.md` § "Verification", step 3.
  - The "Measured rates" subsection's shape for the new rates.
  - The shape of the changelog entry at `CHANGELOG.md:19-23`.
- **Dispatch:** one `code-writer` dispatch handles Edits 1, 1b, 2 and 4 and Edit 3's reason section and Sources line. Verification step 1 checks it. The session then runs the M4 cells, which need subagent dispatches, and writes the rates subsection itself, because only the session holds that content. `.claude/plans/prose-claim-truth-check.md:127` used the same split. The four files share one piece of background, the new rule's wording, so splitting them across dispatches would restate it in each prompt.

Projected diff: about 42 changed lines: 4 in the reviewer, 2 in item 5, 3 in `CHANGELOG.md` and about 33 in the record.

The staged record is 30 lines, 21 of them the dated rates subsection. The pending record edits extend existing paragraphs, and step 3c's rates add about 3 lines.

## Verification

Run every command from the worktree root.

1. `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py` passes. `CHANGELOG.md` maps to no test (row 26). If it selects nothing for a changed path that has tests, that is a bug in its rule table, per the repo CLAUDE.md. No Python or shell file changes, so `ruff` and ShellCheck do not apply.
2. Text checks:
   - `grep -c 'The fix is the statement the subject supports' claude/.claude/agents/comment-discipline-reviewer.md` prints 0.
   - `grep -n 'less what Prose restating the code would delete' claude/.claude/agents/comment-discipline-reviewer.md` prints line 212.
   - `grep -c 'scoping its quantifier' claude/.claude/agents/comment-discipline-reviewer.md` prints 0.
   - `grep -c 'beside it' claude/.claude/agents/comment-discipline-reviewer.md` prints 0.
   - `grep -n 'repeating what reading the code shows' claude/.claude/agents/comment-discipline-reviewer.md` prints line 217, since Edit 1 swaps one line for one line.
   - `grep -n 'the fix names the text to delete' claude-skills/skills/code-review/SKILL.md` prints line 254.
   - `grep -c 'lines to delete' claude-skills/skills/code-review/SKILL.md` prints 0.
   - `wc -l claude-skills/skills/code-review/SKILL.md` matches the base's count. `check-skill-length.sh` enforces the cap at commit.
   - `cmp claude/.claude/agents/comment-discipline-reviewer.md ~/.local/state/comment-study/fable-arm-reviewer.md` prints nothing, which ties the shipped reviewer to the body the cells ran on (row 37).
3. **Angle cells (M4)**, run on the measured scratch body on 2026-11-15 before the edit, and the record's cells (row 37):
   - The scratch tree and diffs rebuild the cells from `.claude/plans/prose-claim-truth-check.md` § "Verification", step 3. They stay in a session scratchpad, never in the repo (row 14).
   - Each run is a `general-purpose` Sonnet dispatch acting as the reviewer the scratch body defines, given by absolute path (G2), with `Read`, `Grep`, `Glob` and `Write`, or read-only `Bash` with a `LISTING:` header line when the first two are absent (Re-measure). It is a fresh dispatch per run, K=5 per diff.
   - Each cell is scored against step 3's bars with its K=10 rule, plus the fix-kind and quote checks. A miss follows Re-measure (M4).
   - The precedence cell is scored by fix kind with one finding per site, and the negative cell by Restating findings only, with the raw rates alongside in the record.

3a. **Non-adjacent cells (M8)**, same scratchpad. Each cell is a diff about a function whose behavior a reader finds only by reading a different function in the same file:
   - **Pointer cell (must-flag):** a false docstring claim about that function. The pointer routes this restating claim. Edit 1b is credited only where the base body fails it, and the base body clears it, so the cell shows no routing effect. Its fixture's falsity is also visible from the adjacent line.
   - **Acceptance cell (must-flag):** a true docstring fact about that function. It runs on both bodies in one session. It is a measured shortfall against the engineer's ruling when the shipped body deletes fewer than the bar, and its contrast with the base body is the only evidence of the shipped wording's reach.
   - **Negative cell (must-pass):** a design rationale or external constraint no reading of the code shows.
   - **Arms.** Each cell runs K=5 on the edited body and K=5 on the base body, on the same diff files in the same session. The base body is `git show HEAD:claude/.claude/agents/comment-discipline-reviewer.md` saved to the scratchpad before any edit, passed by path. Name `HEAD`'s SHA in the PR body.
   - **Scoring.** The primary result is the fix kind on the site: the claim is deleted, not rewritten. The angle label is secondary. A site with both a Restating and a False finding counts as met when at least one of its fixes deletes the claim and none rewrites it.
   - **Bars.** At least 4 of 5 for a must-flag cell and at most 1 of 5 for the negative cell, with the K=10 re-run at 8 and 2.
   - **Reporting.** A miss follows Re-measure (M4). The record's dated subsection carries the edited-body rates. The base-body rates go in the PR body, where a before and after comparison is allowed.
3b. **Replay (M9)**, run on 2026-11-15, the record's replay figures (row 36):
   - The arm is arm 10's three rule files with Edit 1's bullet in place of arm 10's two and Edit 1b's pointer change, at `~/.local/state/comment-study/replay-arm11/rules/`.
   - Three runs over the 61-unit set, one fresh `general-purpose` Sonnet dispatch per run, with the prompt pinned in `~/.local/state/comment-study/REPLAY-PROMPT.md`. That file also records the date and conditions of arms 0 and 10, which ran on 2026-11-12.
   - Scoring is `~/.local/state/comment-study/score_replay_v2.py` under classifiers A and B, beside arms 0 and 10's saved results.
   - The measures are real findings where the claim was kept, a NO-FINDING or KEEP-CORRECT verdict (of 28), the verify-table claims deleted (of 30), and units deleted outright that classifier A labels as needing a shorter local version. The record and the PR body name the first measure that way, never "left alone".
   - Classifier B counts the deleted units whose recorded counterfactual keeps the claim, and the record carries arm 11's three counts (2, 2, 1) beside the classifier A figure.
   - The record's dated subsection carries arm 11's figures and one sentence on corpus scope (row 42): the units and claims come from this repository's own tracked files and review findings, and the study's inputs are local and unpublished.
   - The comparison with arms 0 and 10 goes in the PR body, described as directional at three runs. The PR body also says the study's single-account scope was not checked (row 24).
3c. **Limit-note cells (M10)**, same scratchpad, on the shipped body and the base body in one session:
   - Two diffs on one function that carries a header "does NOT catch" scope note beside code that shows other behavior. In one diff the note is true. In the other it is partly false, because the code now also catches one listed shape.
   - K=5 per diff per body. Each run's fix kind is scored: the note is deleted, narrowed or corrected, or left alone.
   - There is no bar to pass, because the engineer asked a question and not for a behavior. The record's dated subsection carries the rates for both bodies, and the PR body carries the base rates.
   - A run is scored by its own first stated fix. A run that states a rewrite and a deletion with equal weight is reported as ambiguous, and the record gives a range.
   - If the shipped body deletes the partly false note in 3 or more of 5 runs, the session reports the rates to the engineer before the record is written, since that bears on rows 3 and 40. The record states no claim about limit notes beyond the rates and the fixture's shape. It names the base body as the reviewer at `13ccd953`.
4. **Record the rates (M5).** Write each cell's rate, the K, the date, the model, any K=10 re-run and any unmet bar into the record's new dated subsection.
5. **Pipeline**, per `.claude/rules/review-pipeline-dispatch.md`:
   - `/agent-review` on `comment-discipline-reviewer.md`;
   - `/skill-review` on `code-review/SKILL.md`, which a hook enforces at commit;
   - then `/code-review` on the staged diff.
6. Staged-diff checks:
   - `git diff --cached --name-only` lists only the four modified Critical files and this plan file. Nothing from `agent-reviews/` or the cell scratchpad appears (row 25).
   - `git diff --cached --stat` shows about the Projected diff's line counts per file.
   - `git diff --cached` on the reviewer file shows exactly Edit 1's one line in place of line 212 and Edit 1b's one-line change at line 217, with no other hunk.
   - The record section, rates subsection, Sources line and changelog bullet are read in the staged diff, since no test covers them.
   - After staging, `git show :docs/design-decisions/false-or-unsourced-claim-checks.md | grep -c 'kept the claim'` prints at least 1 and the same pipe with `'real findings alone'` prints 0 (row 36). The pipe reads the index, so an unstaged edit fails it.
   - The same pipe with `'2, 2 and 1'`, `'local and unpublished'` and `'Limit-note cell'` each prints a line, the first two in the Replay paragraph and the third in the Cells list.
   - The record's replay figures equal a fresh run of `score_replay_v2.py` over `replay-arm11/out`.

## Out of scope

- **`claude/.claude/CLAUDE.md` lines 116 and 118, and the reviewer's exemptions.** Arm 7 changed them (Alternatives set aside).
- **Measuring the goal of fewer review rounds.** The cells and the replay measure the fix a reviewer writes. A round-2 replay that re-reviews the fixed text and counts findings would test the goal.
- **Whether `CLAUDE.md` line 116's "what its name, signature, branches, types and tests show" is narrower than the engineer's bar.** The acceptance cell's reviewers cited that wording when they left the delegating docstring alone. A follow-up decides it.
- **Any change to the review loop**, including the reviewer's added-or-modified scope (row 19).
- **The tripwire's fix wording at `code-review/SKILL.md:84`.** It is a sibling fix rule: it corrects stale prose to the new value. The replay ran only the reviewer, so whether the tripwire shows the same restate-again pattern is unmeasured. Changing it would re-open the tripwire's own cells.
- **The rest of the False block's pointer at `comment-discipline-reviewer.md:217-219`.** Only the "beside it" wording changes (Edit 1b). Its routing and its deletion-fix clause stay as written.
- **A committed fixture or hash pin for the angle's wording.** The record's § "The guard's limit" weighs both options and builds neither.
- **The 2026-11-05 rates and `CHANGELOG.md:108`.** Both are preserved records.
