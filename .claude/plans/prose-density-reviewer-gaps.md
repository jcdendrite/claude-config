# Prose-density reviewer gaps (GH-1006)

## Context

Goal: close the gap that lets dense, hard-to-follow prose reach human PR review despite `comment-discipline-reviewer` and `tighten-prose` already existing, by first establishing which gap is real: checklist scope (holistic density is not a check) or trigger scope (the reviewer is not dispatched). Fix only the one that is real, rather than adding a third mechanism.

Ask: cited to GH-1006 — "investigate both candidate gaps (checklist scope vs. trigger scope) before picking a fix, rather than adding a third overlapping mechanism." Step 4 selections:

- Checklist gap: "Add a readability angle (Recommended)"
- Trigger gap: "Widen row scope only (Recommended)"
- #919 citation-placement evidence: "Yes, leave to #1005 (Recommended)"
- Validation: "I actually think a regression fixture would be a good idea, but have the architect run an adversarial review against it"
- Fixture home versus the scratchpad rule: "Commit the fixture (Recommended)"
- Scope beyond the Ask at plan review: "M4: wrong-altitude example", "M5: row right-column rewrite", "M8: replay the real complaint sites", "I think all of those but can you have the architect do an adversarial review"
- After that review: "Drop M4 (architect's recommendation)"

Why now: in one week (2026-09-06 to 2026-09-13) the human reviewer filed four independent "too dense / can't follow" complaints across PRs #972, #963, #928, and #919. `review-trace` showed `comment-discipline-reviewer` fired repeatedly on #972 and #963, yet the complaint recurred there. It recorded zero reviewer spawns on #928. Firing on #919 is unconfirmed (no recoverable transcript). Intended outcome: a single targeted change to whichever mechanism is actually failing, with evidence from the four complaint sites that the change would have caught them.

## Approach

Add one `comment-discipline-reviewer` angle, **Paragraph readability**. Bound the two carve-outs that let the complained-about shapes through. Name instruction files in `/code-review`'s comment row. The evidence comes from a local replay of the real complaint sites against today's reviewer body and the new one. A committed synthetic fixture guards the angle against removal and silent rewording.

Alternatives set aside:

- **Bound the carve-outs and add no angle.** A paragraph of consecutive single-fact sentences would still pass every existing angle (row 2).
- **Widen Multi-fact comment structure to paragraphs.** That puts a sentence-level predicate and a paragraph-level predicate in one angle. The agent's Scope section also cites Multi-fact for new plan files, so plans would quietly inherit paragraph review.
- **Dispatch `tighten-prose` from `/code-review`.** It rewrites text but never names sites or judges whether content belongs. It would also be the third overlapping mechanism the Ask rules out.
- **A hook that measures sentence or paragraph length.** This is also a third mechanism. `docs/design-decisions/prose-rules-promoted-to-global-claude-md.md` already declined a bare numeric sentence target, because such a target pushes writers to split sentences that must stay whole.
- **An always-loaded-file example on Prose at the wrong altitude (the former M4).** Dropped after the architect's adversarial review (rows 37–38). `code-review/SKILL.md` § "Domain: Claude Code config" already assigns placement and altitude for instruction files to `ai-instruction-and-memory-files`, and the predicate needs the load-path investigation this reviewer's closed-form design excludes.

### Assumption ledger

**Root problem.** Dense paragraphs reach human review for three reasons:
- every `comment-discipline-reviewer` angle judges a sentence or a site, never how a paragraph is arranged;
- two of the agent's carve-outs exempt the complained-about shapes by their own wording;
- `/code-review`'s comment row does not name instruction files.

**Givens** (conditions fixed beyond this plan's reach):

- **G3.** The reviewer's findings from #972 and #963 cannot be recovered from disk. *Reason: the worktrees that held them were removed.* The only surviving redaction-named worktree is `numeric-fingerprint-redaction-check`, and its `agent-reviews/` never mentions `docs/private-project-redaction.md` (Glob and Grep, this session). The branch names of #972 and #963 were not resolved, so this check is strong but not exhaustive. Phase 0 replays the sites against today's body instead.

Two conditions this plan accepts but could change are recorded in Out of scope rather than as givens, because both are this repository's own decisions: that no CI test runs an LLM reviewer (G1), and that the comment row defers to the cumulative pass (G2). Rows that cite G1 or G2 cite those Out of scope entries.

**Rows:**

1. Every current angle is a per-sentence or per-site predicate. None judges how a paragraph's sentences are arranged. `[verified: claude/.claude/agents/comment-discipline-reviewer.md lines 55–120]`
2. Two carve-outs exempt the measured shapes by their own wording. `[verified: same file]`
   - Comment verbosity's "Separate sentences that each carry a distinct fact are not verbosity" (line 59) clears an eight-sentence paragraph of single facts.
   - Multi-fact's exemption for "a cause and its direct effect, or a claim and the hedge that qualifies it" (lines 70–72) clears a rule fused with its "so" consequence or its exception.
3. Today's reviewer body (`origin/main`) does not give a paragraph-level finding at the #972 and #963 sites in most runs. `[unverified]` Phase 0 tests it. If the row is false, the angle is unnecessary for those sites. What the reviewer returned in September stays unrecoverable (G3), because its body and dispatch have changed since:
   - The cumulative-pass deferral is dated 2026-09-15.
   - The Restated canonical rule angle is dated 2026-09-18.
   - Path-based diff handoff is dated 2026-09-11 (`docs/cost-levers-considered.md` lines 200–205).
4. Add one readability angle. `[engineer-verified: "Add a readability angle (Recommended)"]`
5. Two proposals in the session's option description are not the engineer's words: anchoring the angle to "One idea per sentence" and "Shape follows content", and narrowing the "tightly coupled facts" carve-out. `[unverified]` This plan keeps both anchors. It adds "Lead with the answer", because the #972 complaint is about an instruction fused with its justification. It bounds the carve-out rather than narrowing it (M2, row 7).
6. §Prose and Output Format governs "every text surface you author". Durable docs carry §Durable text as "further constraints" on top of it. `[verified: claude/.claude/CLAUDE.md line 88]` So citing §Prose from the reviewer extends its charter without inventing a rule.
7. CLAUDE.md's "Split multi-fact comments" bullet says "Tightly coupled facts (a cause and its effect, or a claim and its hedge) may share a sentence." `[verified: claude/.claude/CLAUDE.md line 116]` The agent cites that bullet as its source. Narrowing the exemption beyond that text would make the agent stricter than the rule it cites. Limiting the exemption to one pair with no further aside stays inside the text.
8. The charter widens to three §Prose bullets only: "Lead with the answer", "Shape follows content", and "One idea per sentence". "Active voice, plain verbs, no noun stacks" stays in `tighten-prose`'s rewrite territory. `[verified: claude/.claude/CLAUDE.md line 100; claude-skills/skills/tighten-prose/SKILL.md §4]` Citing all of §Prose would invite word-choice findings.
9. Any false positive lands on every stow consumer's docs. `[verified: repo CLAUDE.md, "Plans in this repo affect all stow users"]` M1's Not-a-violation list and Phase 2's `must_pass` cell are the guards.
10. The reviewer is closed-form: it "reads a diff and a fixed rule set, no shell needed". `[verified: docs/design-decisions/reviewer-persona-roster-operations.md line 21]` The new angle judges one paragraph against fixed shapes. It needs none of the open-ended load-path investigation that `duplicated-evidence-at-plan-review.md` declined. `effort: medium` stays, and `EXPECTED_EFFORT` (`test_agent_roster.py` line 123) does not change.
11. Widen the row's scope and add no hook. `[engineer-verified: "Widen row scope only (Recommended)"]`
12. The session's option description also proposed naming `CLAUDE.md`, `AGENTS.md`, and `.claude/rules/` in the row. It relied on the `Spawn decisions:` line plus the cumulative pass for enforcement. `[unverified]` This plan adopts that proposal, with one narrowing on the agent side from row 39: instruction files get every angle except Prose at the wrong altitude.
13. Widening the row does not explain #928's zero spawns. `docs/private-project-redaction.md` (+77 lines) was already inside the row's "doc files" scope at merge commit `9ecaef83`, and still nothing spawned. `[verified: Step 3 evidence C]` The widening covers only #928's `CLAUDE.md` site. Why nothing spawned remains unexplained (see Out of scope).
14. Three code-review tests constrain the row edit. `[verified: claude-skills/skills/tests/test_skills.py lines 5030–5041, 5209–5239, 5262–5272]`
    - Step 0.6's `SCOPE_EXEMPT_ROW` text must exactly match the row's left column.
    - The row must keep "Step 0.6 defers this row out of staged-diff commit-gate rounds." verbatim.
    - The row must not contain `pr-diff-against-base.sh --staged --diff-file`.
    - No test asserts the rest of the right column. `[verified: architect consult, this session: `test_comment_discipline_row_points_to_its_deferral` at line 5269 pins only the Step 0.6 sentence; a search of `*.py` for "carries no `Bash`" and "per the note directly below" found nothing]`
15. A test pins item 12a's sub-bullets to the agent's angle headers, by name and in order. `[verified: claude/.claude/hooks/tests/test_design_decision_files.py lines 536–618, 621–626]` `_SCOPE_CITED_ANGLE_NAMES` gains "Prose at the wrong altitude" because the Scope section will name it (row 39). No test pins a design-decision doc to the angle list, which corrects Step 3 evidence A.
16. Agent descriptions are capped at 1,000 characters. The description is 823 characters today (counted this session). The deltas by mechanism:
    - M3 charter label: +47.
    - M5 TRIGGER list: +72.
    - M5 DO NOT TRIGGER adjacency: +85.
    - Focus-list-to-pointer: −200.

    `[verified: test_agent_roster.py lines 87–91; the deltas are by hand and unverified to the character]` Naming the three §Prose bullets in the description would still not fit, so the description carries the short label and the TRIGGER clause uses the compact list (M5 c). `test_description_length` settles it.
17. `code-review/SKILL.md` has 488 lines against a 500-line cap, and this plan adds one. `[verified: check-skill-length.sh lines 113–114; line count taken this session]`
18. Past `comment-discipline-reviewer` scope changes are logged in `CHANGELOG.md`. `[verified: CHANGELOG.md line 119]`

**Fixture design and adversarial review.** The engineer asked to "have the architect run an adversarial review against it". Rows 19–32 are that review. Each row states an attack, then the design's answer.

19. **Vacuity.** A test that only checks that the agent names the angle duplicates `test_item_12a_index_matches_agent_angle_headers` and never reads the fixture. `[verified: test_design_decision_files.py lines 605–618]` *Answer:* every M7 assertion reads a field from the fixture.
20. **Lockstep removal.** The item 12a test compares two sequences. It still passes if the angle is deleted from both files in the same change. `[verified: same file, lines 594–601]` *Answer:* the fixture's `angle` field must match a current header, so removing or renaming the angle fails CI.
21. **Rewording drift.** A reworded angle can stop covering the fixture's shape while CI stays green, because no structural test can judge coverage (G1). *Answer:* the fixture stores a hash of the angle's text, so any rewording fails CI until someone updates the hash. Residual `[unverified]`: an editor can update the hash without re-running the measurement. That is the same honor system the rule file already relies on, but it now has a trigger.
22. **Caricature.** The old reviewer would also catch a hand-written dense paragraph, so such a fixture cannot show that the change mattered. `evals/README.md` records that two synthetic seed fixtures "measured non-discriminating". `[verified: evals/README.md lines 354–358]` *Answer:* `must_flag` is built to fit inside the current carve-outs (see M7's properties), and Phase 2 runs the old body on it. *Amended:* the three-parallel-rule property this row originally relied on was dropped for the same reason — the old Multi-fact angle already catches that shape. `[engineer-verified: "Approve amendment"]`
23. **Vacuous negative.** A `must_pass` that drops facts would pass trivially. *Answer:* M7 checks that every backticked span and number in `must_flag` also appears in `must_pass`. Phase 2 also runs the new body on `must_pass`.
24. **Self-flagging.** A `.md` fixture would match the row's "doc files" scope, and the new angle would then flag it on this PR. `[unverified]` *Answer:* store the pair as JSON test data under `claude/.claude/hooks/tests/fixtures/`. If the cumulative pass still flags it, DEFER the finding as deliberate test data.
25. **Scratchpad rule.** `.claude/rules/skill-and-agent-self-review.md` says "Keep the fixtures in a scratchpad". `[verified: that file]` The engineer asked for a regression fixture. `[engineer-verified: "I actually think a regression fixture would be a good idea"]` The rule's stated reason fits leak-type content, not a synthetic paragraph whose only defect is its shape. This plan commits the fixture and records the exception in the decision record. The engineer settled the conflict in favor of committing. `[engineer-verified: "Commit the fixture (Recommended)"]`
26. **Redaction and fingerprinting.** Content from this repo's own history is outside the redaction class. `[verified: repo CLAUDE.md § "Redact private-project-identifying content"]` The dispatch still asks for synthetic content. *Answer:* the fixture uses a neutral topic and no tracker-ID shapes. Its sentence and word counts do not mirror the complaint sites. `deny-private-project-refs.sh` checks the commit.
27. **Proxy gap.** A synthetic pair passing says nothing direct about the real sites. *Answer:* Phases 0 and 2 replay #972, #963, and both #928 sites locally, and commit nothing from them.
28. **Harness fit.** `disposition-fidelity` reads rule text only from a skill's `SKILL.md` (`evals/run_skill_evals.py` line 1048). The review bench is a frozen multi-arm campaign that needs many fixtures per gate. `[verified: both files]` Neither fits without harness work. *Answer:* run manual dispatches as M8 describes.
29. **Wrapper fidelity.** A `general-purpose` dispatch that carries an agent body is not the registered agent. Its tools differ, and its effort setting is unknown. `[unverified]` This defense covers the fixture comparison only, where both arms use the same wrapper. Phase 0's gate and Phase 2's site bar are absolute thresholds, so Phase 0 runs the registered agent instead (M8).
30. **Replay input.** A merged commit already carries the author's response to the complaint, so it is the wrong replay input. Each replay uses the complaint comment's `original_commit_id`. `[unverified: that those commits can still be fetched]` Fallback: pass the comment's `diff_hunk` as literal diff text; the agent's Input contract accepts that. Tell the reviewer the surrounding file is unavailable. The reviewer's How-to-work step 1 reads changed files fully, so each site's changed files are extracted at that commit into the scratch root M8 names. Otherwise the reviewer would read today's rewritten text instead of the commit under review.
31. **Test placement.** A fixture in a new `evals/fixtures/` subdirectory matches no `select-tests.py` rule, so selection falls back to the full suite. Repo CLAUDE.md treats that fallback as a rule-table bug. `[verified: claude/.claude/scripts/select-tests.py lines 535–549]` *Answer:* put it in `claude/.claude/hooks/tests/fixtures/`. The `HOOKS_DIR` domain rule maps that directory, and one fixture already lives there.
32. **Coverage residual.** The fixture tests M1's two shapes only. *Amended:* after the `must_flag` amendment (row 22), it tests only shape 2. No measurement exercises shape 1's "Three or more" threshold, because the old Multi-fact angle already catches that shape and a fixture cell for it could not discriminate. `[engineer-verified: "Accept, record residual (Recommended)"]` The replays test M2's bound, through #928's three-fact intro sentence, and M1 under the agent's widened Scope, through #928's `CLAUDE.md` site. No reviewer run can test the row widening, because spawning is orchestrator behavior.

**Scope beyond the Ask line.** `/plan-review` listed three deliverables the Ask and the Step 4 selections do not call for. The engineer kept all three and asked for an adversarial review of them.

33. M4, the always-loaded-file example on Prose at the wrong altitude, was kept at first. `[engineer-verified: "M4: wrong-altitude example"]` After the review in rows 37–38 the engineer dropped it. `[engineer-verified: "Drop M4 (architect's recommendation)"]`
34. M5's right-column rewrite, replacing the row's restated angle list with a pointer to item 12a, stays in scope. `[engineer-verified: "M5: row right-column rewrite"]`
35. M8's replays of the real complaint sites in Phases 0 and 2 stay in scope. `[engineer-verified: "M8: replay the real complaint sites"]`
36. The engineer asked for an adversarial review of rows 33–35: `[engineer-verified: "I think all of those but can you have the architect do an adversarial review"]`. Rows 37–48 record that review (`plan-architect`, `MODE=consult`, this session).

**Architect's adversarial review of M4, M5, M8.**

37. **M4 gives altitude a second owner.** `code-review/SKILL.md` § "Domain: Claude Code config" assigns placement and altitude for `CLAUDE.md`, `AGENTS.md`, and `.claude/rules/*.md` to `ai-instruction-and-memory-files`; `plan-review/SKILL.md` does the same. `[verified: code-review/SKILL.md line 205; plan-review/SKILL.md line 240]` *Disposition:* M4 dropped (row 33).
38. **M4's predicate is not closed-form.** Whether a path-scoped rule "could hold" a carve-out depends on whether the rule must fire before any matching file is open; the repo has a deliberate counter-example where a glob bullet stayed in `CLAUDE.md` for that reason. `[verified: .claude/plans/claude-md-agent-core.md line 302; docs/design-decisions/duplicated-evidence-at-plan-review.md line 7]` Keeping M4 would have contradicted row 10. `check-claude-md-length.sh` does not gate the #928 shape either: it denies only an over-cap file that grew. `[verified: that hook, lines 6–9 and 127]`
39. **M5 double-dispatches altitude on instruction files.** Running both `ai-instruction-and-memory-files` and this reviewer on one file is the shape skill bodies already have; only the altitude angle overlaps. *Answer:* the agent's Scope section excludes Prose at the wrong altitude for instruction files (M5 a).
40. **M5's pointer hides nothing at spawn time.** Item 12a sits at line 132, above the table at line 285, so the orchestrator has read it by Step 2, and the spawned agent carries its own angle list. `[verified: code-review/SKILL.md]` Step 0.6 semantics are unchanged: instruction-file edits newly match the row and defer like every other match. The new left column contains no `|`, so the row parser is safe.
41. **M5's right column fuses rationale before instruction**, which is M1's second shape, and the row is one changed line the cumulative pass will judge. *Answer:* M5 (b) reorders it.
42. **M8's scoring predicate was not reproducible.** "Restructures more than one sentence" and "addresses the owner's quoted complaint" were two different predicates, and neither said whether a one-into-three sentence split counts. *Answer:* M8 registers one predicate per site before Phase 0, with a closed set of fix shapes, and the fixture cells use the same predicate.
43. **Phase 0 could not test the original row 3.** The body that ran in September is not `origin/main` (row 3 lists the three changes since). *Answer:* row 3 now asks about today's body, which is the question that drives the decision.
44. **Row 29's wrapper defense did not reach the Phase 0 gate**, whose threshold is absolute. A false pass ships M1 redundant and nothing downstream catches it, because `must_flag` is built to slip past the old body. *Answer:* Phase 0 runs the registered agent; the wrapper serves only the fixture arms and the new-body site runs.
45. **Replays would read today's files**, since How-to-work step 1 reads every changed file fully. *Answer:* extract each site's changed files at `original_commit_id` into a git-ignored scratch root that is not `agent-reviews/`. That directory holds the branch's review records, which spawns receive as prior decisions. Nothing in the procedure commits them; the PR body gets counts only. #928's content is this public repo's own history, outside the redaction class (row 26).
46. **Sample sizes are smoke checks, and the budget has a worst case.** At a true per-run catch rate of 0.5 a site clears 2 of 3 half the time; at 0.2 and 0.8, about 10% and 90% (exact binomial). Nominal count is 39 dispatches. Each angle-rewording iteration re-runs all 22 new-body runs plus 5 old-arm runs if `must_flag` changes, so two iterations reach about 93. `docs/cost-levers-considered.md` covers production cost levers, not validation budgets, so it sets no cap. `[verified: that file]` The one per-dispatch cost figure in the repo, `REPRESENTATIVE_DISPATCH_COST_USD` in `evals/measure_subagent_model_resolution.py` line 88, was derived from `staff-backend-engineer` runs and does not transfer to small scratch diffs.
47. **G2's record contradicts "once per branch".** `comment-discipline-reviewer-deferred-to-cumulative-pass.md` line 7 says "This is not exactly once per branch: a fix round that reopens step 3 re-spawns the row again." `[verified: that file]` The Out of scope entry for G2 uses the record's wording.
48. **The repo-root `CLAUDE.md` is at exactly 200 lines**, and `check-claude-md-length.sh` denies a staged `CLAUDE.md` that is over the cap and longer than before. `[verified: line count this session; hook lines 6–9 and 127]` M1's list fix adds lines, so a future Paragraph readability fix on that file will be denied until text restated elsewhere in the file is deleted. The existing rule already says so: "At a line cap, delete text restated elsewhere in the same file — never flatten a parallel list into a run-on to fit" (`.claude/rules/skill-and-agent-self-review.md`). This row exists so a fix loop does not flatten a list to pass the gate.

**`/plan-review` round 1 findings** (`staff-product-engineer`, `staff-sdet`, `agent-review`, `skill-review`; findings files under `agent-reviews/`).

49. **M1's lead contradicted its own second shape**, and the Not-a-violation list exempted only the claim-and-hedge half of the pair Multi-fact exempts, so an ordinary "do X because Y" comment would have been flagged (`staff-product-engineer`, BLOCKER). *Applied:* the lead no longer says "not any single sentence"; shape 2 names the single-sentence case as one carrying more than the one exempt pair; the last Not-a-violation bullet covers both halves of the pair; `must_pass` carries a cause-and-effect sentence. `agent-review` item 4 also found the new instruction-file adjacency unnamed in DO NOT TRIGGER. *Applied:* M5 (c) adds it, and the description's Focus list becomes a pointer to free the room (row 16's margin was about 36 characters).
50. **Shape 1 needed a parallel-facts exemption**, or README paragraphs and changelog entries would be flagged (`staff-product-engineer`, CONCERN). *Applied:* a Not-a-violation bullet for parallel facts that are not rules. `agent-review` item 8 found the agent file crossing the 200-line target. *Applied:* Verification 9 states the reason.
51. **"Several" had no threshold in the live text** (`staff-product-engineer`, CONCERN). *Applied:* shape 1 says "Three or more", M7 matches it, and the decision record states why three: the tightly coupled pair Multi-fact exempts is two, so the paragraph shape starts at three. *Amended:* M7 no longer carries the three-parallel-rules property. `[engineer-verified: "Approve amendment"]`
52. **Angle-count growth has no revisit trigger** (`staff-product-engineer`, FYI). *Applied:* the decision record names the trigger (M9). `[verified: comment-discipline-reviewer.md How-to-work step 2 asks the reviewer to check each fix against every other angle, so each angle raises that cost]`
53. **GH-1006 closure was unstated** while #928's zero spawns stay unexplained (`staff-product-engineer`, CONCERN). *Applied:* Verification 8.
54. **M7's hash-mismatch message and `must_pass` shapes were honor-system** (`staff-sdet`, two CONCERNs): the message only pointed at the decision record, and nothing checked that `must_pass` still carried its carve-out shapes. *Applied:* the message states the three pass bars inline; a `must_pass_carve_outs` field with verbatim excerpts is checked structurally; the hash slice convention is pinned in the docstring (FYI). The SDET confirmed by direct simulation that the angle-block hash is non-vacuous and that every test name in Verification 2 exists. `[verified: agent-reviews/staff-sdet-1792106835-GH-1006-prose-densit.md]`
55. **M8's range-overlap test was judgment-dependent for paraphrased findings** (`staff-sdet`, CONCERN). *Applied:* a finding counts only with a six-word verbatim run from the registered range, and prompts ask for verbatim quotes.
56. `skill-review` on the drafted `code-review/SKILL.md` text found no N row: the row's dropped angle list survives at item 12a's bullets, the deferral sentence stays verbatim, and the no-Bash instruction survives reordered. The marker is written at commit time against the real diff, not at plan time.

### Mechanisms

- **M1: new angle `Paragraph readability`, placed third, after Multi-fact comment structure.** `anchors: root, row1, row4`. This is the heaviest change in the plan. The four lighter options in Alternatives each fail. The target wording below is what Phase 2 measures. Change it only to meet Phase 2's bars or to answer `/agent-review`.

  ```markdown
  **Paragraph readability** — a paragraph or list item whose arrangement keeps a reader from finding what it tells them to do. This applies §Prose and Output Format's "Lead with the answer", "Shape follows content", and "One idea per sentence" bullets to the paragraph as a whole. Two shapes realize it:

  - Three or more parallel rules, conditions, or exceptions stacked as consecutive sentences in one paragraph or one list item, where a list would separate them.
  - An instruction fused with its own rationale, elaboration, or exception, so the reader parses the reasoning to find the instruction. The fusion spreads across the sentences around the instruction, or one sentence carries more than the single tightly coupled pair that Multi-fact comment structure exempts, through a dash, colon, parenthetical, or "since" or "so" clause.

  Flag the site and name the fix, keeping every fact:
  - State the instruction first, as its own sentence.
  - Put load-bearing rationale after it, as its own sentence.
  - Turn parallel items into a list.

  Rationale that changes nothing the reader does is a Comment verbosity finding instead.

  Not a violation:
  - A paragraph whose sentences each state one fact in reading order, with no three-or-more set of rules, conditions, or exceptions a list would separate.
  - Consecutive sentences stating parallel facts about one thing's behavior, as in a README paragraph or a changelog entry, when none of them is a rule the reader must apply.
  - A bold label followed by a dash and its definition, as in this section's own headers.
  - One tightly coupled pair sharing a sentence, a cause and its direct effect or a claim and the short hedge that qualifies it, per Multi-fact comment structure. An exception with its own condition and consequence is a second rule, not a hedge.
  ```

  No line in the angle body may start with `**Name** — `. The header parser would read such a line as a new angle (`test_design_decision_files.py` line 548). The fix list above starts no line with `**`.
- **M2: bound the Multi-fact carve-out and point Comment verbosity at the new angle.** `anchors: row2, row7`.
  - Replace Multi-fact's first "Not a violation" bullet with: "One tightly coupled pair sharing a sentence, such as a cause and its direct effect, or a claim and the hedge that qualifies it. The exemption covers the pair alone. A sentence that also carries a third fact, or a further dash, colon, or parenthetical aside, is judged as multi-fact."
  - After line 59's sentence, add: "Paragraph readability judges how those sentences are arranged."
- **M3: widen the charter to three §Prose bullets.** `anchors: row6, row8, row16`. This changes the charter, so two lighter options were checked first. If the charter stays at §Durable text, the angle cites a section that never states the paragraph rules (row 6). If the source is stated only inside the angle, the description and item 12a, which dispatchers read, never show it.
  - Four sites state the charter: the frontmatter `description`, the intro paragraph, the lead sentence under "Core review angles", and item 12a's lead question. The intro paragraph, the "Core review angles" lead, and item 12a name the three bullets, "Lead with the answer", "Shape follows content", and "One idea per sentence", alongside §Durable text and the single-source-of-truth bullet. Only the description uses the short label "§Prose and Output Format's structure bullets", because the cap in row 16 leaves no room and the same file's intro resolves the label. No other file uses the label, since `CLAUDE.md` does not define it.
  - The description's Focus list shrinks to a pointer, "Focus on the eight core review angles in the body", which frees the room row 16 needs. The body's "Core review angles" section is the canonical list; item 12a indexes it.
  - The Output format section's "Violation type" list gains "Paragraph readability" after Multi-fact comment structure.
- **M5: widen the comment row's scope and make its right column a pointer.** `anchors: row11, row12, row14, row39, row41`.
  - New left column, copied exactly into Step 0.6's `SCOPE_EXEMPT_ROW` anchor: "Adds or modifies a comment or durable-doc prose beyond a hygiene tweak (code comments, docstrings, `REFERENCES.md`, doc files, README sections, skill/agent bodies, instruction files such as `CLAUDE.md`, `AGENTS.md`, and `.claude/rules/*.md`)".
  - New right column: "`comment-discipline-reviewer` — enumerate every site violating one of its core review angles (item 12a indexes them). Step 1.5's inline "Non-durable comment" tripwire still runs unconditionally regardless of this spawn. Resolve its diff artifact per the note directly below this table before spawning it, because this reviewer carries no `Bash`. Step 0.6 defers this row out of staged-diff commit-gate rounds."
  - Today the row restates the angle list, a third copy that no test checks. Item 12a is the copy that is tested.
  - (a) Add to the agent's Scope section: "Instruction files (`CLAUDE.md`, `AGENTS.md`, `.claude/rules/*.md`) are in scope for every angle except Prose at the wrong altitude. `ai-instruction-and-memory-files` owns placement and altitude for those files, per `code-review/SKILL.md` § "Domain: Claude Code config"." Add "Prose at the wrong altitude" to `_SCOPE_CITED_ANGLE_NAMES` in `test_design_decision_files.py`.
  - (b) The right column states the diff-artifact instruction before its reason, as written above.
  - (c) The description's TRIGGER clause widens with a compact list: "(REFERENCES.md, doc files, README sections, skill/agent bodies, CLAUDE.md/AGENTS.md, rule files)". Its DO NOT TRIGGER clause gains "altitude or placement on CLAUDE.md/AGENTS.md/rule files (ai-instruction-and-memory-files's lane)", so the new adjacency is named where the dispatcher reads (row 49).
  - The surface list at `code-writer.md` line 138 widens the same way.
- **M6: item 12a bullet.** `anchors: row15`. Add as the third bullet: "   - Paragraph readability — parallel rules stacked as sentences, or an instruction fused with its rationale or exception".
- **M7: committed fixture and its CI check.** `anchors: row19, row20, row21, row22, row23, row24, row25, row31`. This is heavier than a name check, so two lighter options were tried first. A name-resolution check alone misses rewording (row 21). The rule file's re-run obligation alone has no trigger (rows 21 and 25).
  - The fixture is `claude/.claude/hooks/tests/fixtures/paragraph-readability.json`, with fields `angle`, `angle_text_sha256`, `must_flag`, `must_pass`, and `note`.
  - `must_flag` must have these properties:
    - a neutral synthetic topic;
    - five to seven sentences in one paragraph;
    - a distinct non-obvious fact in every sentence, so Comment verbosity's carve-out applies;
    - one instruction whose rationale comes before it or interrupts it;
    - at least one backticked span and one number;
    - nothing that any other angle would flag.
    - **Amended:** dropped the "at least three parallel rules as consecutive sentences" property. The old Multi-fact comment structure angle already catches that shape by recommending a list, contradicting "nothing that any other angle would flag." `[engineer-verified: "Approve amendment"]`
  - `must_pass` holds the same facts, with each instruction first, its rationale after, and parallel rules as a list. It also holds three carve-out shapes the angle must leave alone: one cause-and-effect sentence of the "do X because Y" form, one claim-and-hedge sentence, and one bold-label-and-definition bullet. A `must_pass_carve_outs` field lists those three shapes, each with a verbatim excerpt of the sentence that realizes it.
  - Add assertion 9 to `test_design_decision_files.py` as a pure `_paragraph_readability_fixture_violations()` function, following the module's idiom. It checks five things:
    - `angle` matches a current angle header;
    - `angle_text_sha256` equals the SHA-256 of that angle's block with whitespace collapsed, from its header line (inclusive, starting at its leading `**`) to the line before the next angle header or the end of the section; the docstring states this slice convention;
    - every backticked span and digit run in `must_flag` also appears in `must_pass`;
    - `must_flag` holds at least one backticked span and at least one digit run;
    - `must_pass_carve_outs` names exactly the three shapes above and each excerpt occurs verbatim in `must_pass`, with the bold-label excerpt matching `^- \*\*[^*]+\*\* — `.
  - When the hash check fails, its message prints the recomputed hash, states the three Phase 2 pass bars inline (old body on `must_flag` ≤ 1/5, new body on `must_flag` ≥ 4/5, new body on `must_pass` ≤ 1/5), and names the re-run procedure in the decision record.
  - Fault-injection tests cover a renamed angle, a removed angle, reworded angle text, a dropped literal, a `must_flag` with no literal, and a `must_pass_carve_outs` excerpt absent from `must_pass`.
- **M8: replays and fixture measurement.** `anchors: row3, row22, row27, row28, row29, row30, row42, row44, row45, row46`. The session runs these, and nothing from them is committed.
  - **Scratch root.** Before Phase 0, pick a directory that git ignores (confirm with `git check-ignore`) and that is not `agent-reviews/`. It holds the old agent body, each site's replay diff, and each site's changed files extracted at the complaint's `original_commit_id`. Every prompt names that root.
  - **Registered predicate.** Before Phase 0, record for each site: the complained-about line range at `original_commit_id`, the owner's quote, and the expected fix shape from a closed set:
    - (L) two or more consecutive sentences become a list;
    - (O) an instruction moves ahead of, or splits from, rationale or an exception that preceded or interrupted it;
    - (S) one sentence's facts split apart. S covers #928's three-fact intro sentence, which tests M2.
  - A run **catches** a site when a finding quotes at least six consecutive words verbatim from the registered line range and its concrete fix has that site's shape. A paraphrase with no six-word verbatim run does not count, so every prompt asks the reviewer to quote each flagged site verbatim. The angle label is ignored, because the old body cannot emit the new label. The fixture cells use the same predicate, with O only for `must_flag`. A site whose registered complaint is placement rather than arrangement is reported but not gated, so Phase 2's "rework the angle" step never pulls M1 toward placement. #972's lines 135–143 complaint and #928's `docs/private-project-redaction.md` paragraph complaint are reclassified this way too, though neither is a placement complaint. The first is about where the justification sits relative to the instruction, which M1's fix order already prescribes. The second is about a conceptual defect — a paragraph that conflated what may be counted with how it may be reported — which no arrangement rule can reach. `[engineer-verified: "Reclassify both"]`
  - **Dispatch forms.** Phase 0 runs the registered `comment-discipline-reviewer` with `model: sonnet`, after confirming that the stowed agent file is byte-identical to `origin/main`'s. A registered dispatch reads the stowed agent file, which cannot see this worktree's uncommitted Phase 1 edit, so every run that needs the new body goes through a wrapper instead. The fixture arms and the Phase 2 site runs each use one `general-purpose` dispatch with `model: sonnet`, whose prompt names the agent body's path (the scratch copy for the old body, the worktree file for the new) and the diff path, and asks for inline output. Bodies travel by path, not verbatim, so the parent's context does not carry up to 39 copies of a 195-line file.
  - **Logging.** Record each run's quoted text, angle, and fix word for word in the scratch root, so a second session can re-score.
  - **Sample sizes are smoke checks.** The fixture bars borrow `evals/README.md`'s disposition-fidelity gates, treatment ≥ 0.8 and a baseline drift alarm at ≥ 0.3, which at K=5 become ≥ 4/5 and ≤ 1/5. The site bar is 2 of 3. Neither certifies a rate; both catch a gross failure (row 46). Nominal budget is 39 dispatches, worst case about 93 over two iterations.
- **M9: decision record, CHANGELOG, and README.** `anchors: row18`.
  - **Decision record.** Create `docs/design-decisions/paragraph-readability-review-angle.md` in the shape of `restated-canonical-rule-review-angle.md`. Its provenance line carries the writing date and no `Formerly §N` clause. It cites the agent's "Core review angles" rather than restating them. It also cites `prose-rules-promoted-to-global-claude-md.md`, which recorded that the reviewer enforced no §Prose rule. This decision extends that record and does not supersede it. The record is the canonical home of M7's re-run procedure and of the scratchpad-rule exception (row 25). It also states the "three or more" threshold's reason (row 51) and names the trigger for revisiting `effort: medium` or splitting the reviewer: the next angle that needs a carve-out cross-reference to an existing angle (row 52).
  - **CHANGELOG.** Add an entry under `[Unreleased]` → `### Changed`.
  - **README.** At README.md line 247, replace "checks the diff against CLAUDE.md §Durable text" with "checks the diff against the CLAUDE.md prose rules its agent file names". The README then stops restating a charter that has just widened.

### Phases

- **Phase 0: the session, before any edit.**
  - Set up the scratch root and register each site's predicate (M8).
  - Build replay diffs as row 30 describes for four sites: #972, #963, and #928's `docs/private-project-redaction.md` and `CLAUDE.md` sites.
  - Run the registered reviewer three times per site.
  - For each run, log whether it catches the site under the registered predicate.
  - **Gate:** if today's body catches both #972 and #963 in at least 2 of 3 runs, stop and report to the engineer. Row 3 is then false.
- **Phase 1: one `code-writer` dispatch.** It owns every file in Critical files. Do not split it. The angle's name and wording recur in the agent body, item 12a, the fixture, the hash, and the test, so every split would have to restate them.
- **Phase 2: the session.**
  - Run three fixture cells at K=5:
    - Old body on `must_flag`. Pass: at most 1 of 5 runs catches it.
    - New body on `must_flag`. Pass: at least 4 of 5 runs catch it.
    - New body on `must_pass`. Pass: at most 1 of 5 runs gives any finding.
  - Run the new body three times per Phase 0 site. Pass: it catches every gated site in at least 2 of 3 runs.
  - If the old-body cell fails, the fixture is a caricature. Rework `must_flag`, not the angle.
  - If the new body misses a gated site or `must_flag`, rework the angle's wording, which also changes the hash.
  - If the `must_pass` cell fails, rework the Not-a-violation list or `must_pass`.
  - Iterate at most twice, then report to the engineer.
  - A second, small `code-writer` dispatch owns three edits:
    - It writes the measured rates, date, and model into `note`.
    - It updates the decision record as the last bullet of Phase 2 outcome states.
    - It adds a sentence to M7's hash-mismatch message pointing at `note` for which bars the last measurement met. Without that pointer, the message presents all three bars as met.

### Phase 2 outcome

Iteration 1 ran on 2026-10-16 with Sonnet (`general-purpose`, `model: sonnet`). It used the amended `must_flag` (M7) and the predicates registered in the scratch root's `predicates.md` under "Iteration 1 registration". Iteration 0 failed its bars and led to that amendment. Beyond M8, iteration 1 added a control arm at #972 and #963: the old body through the same wrapper, three runs per site.

Fixture cells (n=5):

- Old body on `must_flag`: 2/5 under the literal predicate. The bar is ≤ 1/5, so the cell **fails**. At a true per-run catch rate of 0.1, 0.2, or 0.3, a result of 2 or more out of 5 occurs about 8%, 26%, or 47% of the time (exact binomial). So one run over the bar does not separate noise from a fixture that still fails to discriminate.
- New body on `must_flag`: 5/5, which **passes**. Every run diagnosed both rationale-first sites as O.
- New body on `must_pass`: 0/5 runs gave any finding, which **passes**.

Sites (n=3 per arm; new body, then control):

- #972 site b, gated: 0/3 and 0/3, so the site **fails**. New-body findings at its range were S splits, never a lead sentence plus a list.
- #972 site c, gated: 3/3 and 3/3, so the site **passes**. It does not separate the bodies: every run in both arms caught the same semicolon ladder through Multi-fact comment structure.
- #963 site a, gated: 2/3 and 0/3, so the site **passes**. One run in each arm proposed an inline colon-and-semicolon list at lines 146–148 rather than a separate list. Its fix shape is borderline for L, so both were scored as misses. Counting both would make the result 3/3 and 1/3.
- #972 site a, reported only: 0/3 and 0/3.
- #928's `docs/private-project-redaction.md` site, reported only: 0/3. The intended three-fact intro sentence may sit just above the registered range. `[unverified: architect inference]`
- #928's `CLAUDE.md` site, reported only: 0/3.

Decision: after the report, the engineer selected "Ship with caveats (Recommended)" and "Literal: 2/5, fails (Recommended)". `[engineer-verified: "Ship with caveats (Recommended)", "Literal: 2/5, fails (Recommended)"]` The option descriptions and the claim scope below were proposed by the session, not by the engineer.

- **Claimed:**
  - The new body's O diagnosis on `must_flag`: 5/5, against the old body's 2/5 under the same registered predicate.
  - The #963 site a separation, 2/3 against 0/3. This is descriptive. No separation threshold was registered for the control arm, which M8 did not plan.
- **Reported as not achieved:**
  - #972 site b.
  - The old-body cell at 2/5 literal.
  - #972 site c, which does not separate the bodies.
- Each site rate is stated with its sample size. Row 46 gives the n=3 binomial context, and the old-body bullet above gives the n=5 context.
- Measurement detail only, not a claim: both old-body catching runs called the cause-and-effect pairs "fine" and led with the instruction only incidentally, in a list rewrite. A diagnosis reading, which was not registered before the runs, would score them 0/5.
- #972 site b is wontfix. `[engineer-verified: "Wontfix, close at merge (Recommended)"]` Per a `plan-architect` consult this session, the paragraph sits inside the angle's own carve-outs:
  - It already leads with its claim.
  - Its rules are mixed in kind and interleaved with a definition and an examples line, so they are not shape 1's parallel, consecutive rules.
  - Reaching it would take a density rule, which Alternatives declines.
- The fixture `note` holds the measured fixture rates, date, and model, copied from this section.
- The decision record names the ship-with-caveats outcome and the three not-achieved items, and points at the note for the rates. It describes every site by shape, the #972 site b residual included, with no scratch site label, since a site label would be PR-defined terminology in a durable doc. Example wording: "a dense paragraph of mixed rules that already leads with its claim draws sentence splits, not a lead plus a list."

Quote provenance, confirmed at `/plan-review` after the plan's last commit:

- Ship call, with the claim scope as the question showed it ("claim the fixture's O diagnosis (5/5) and #963 site a; report #972 site b, the old-body cell, and #972 site c as not achieved" — site labels reworded post-hoc from the issue-number-plus-letter shape for the redaction gate's Slack-channel-shape detector; same sites, same scope): `[engineer-verified: "Confirmed (Recommended)"]`.
- The literal 2/5 old-body score: `[engineer-verified: "Confirmed (Recommended)"]`.
- "Approve amendment" (M7, rows 22 and 51) and "Reclassify both" (M8): `[engineer-verified: "Both confirmed (Recommended)"]`.

## Critical files

Phase 1 is one `code-writer` dispatch, verified with `.venv/bin/python3 claude/.claude/scripts/select-tests.py`:

- `claude/.claude/agents/comment-discipline-reviewer.md`: modify (M1, M2, M3, M5 a and c). Leave "Read every changed file fully" in How-to-work step 1 untouched, because `evals/review_bench/arms.py` line 42 copies it.
- `claude-skills/skills/code-review/SKILL.md`: modify the `SCOPE_EXEMPT_ROW` text at line 52, item 12a at lines 132–139, and the row at line 285 (M3, M5, M6).
- `claude/.claude/agents/code-writer.md`: modify the surface list at line 138 (M5).
- `README.md`: modify line 247 (M9).
- `CHANGELOG.md`: add an `[Unreleased]` entry (M9).
- `docs/design-decisions/paragraph-readability-review-angle.md`: create (M9).
- `claude/.claude/hooks/tests/fixtures/paragraph-readability.json`: create (M7).
- `claude/.claude/hooks/tests/test_design_decision_files.py`: add assertion 9, its fault-injection tests, a module-docstring entry (M7), and "Prose at the wrong altitude" in `_SCOPE_CITED_ANGLE_NAMES` (M5 a).

Reuse rather than reimplement:

- `_core_review_angle_headers`, `_CORE_REVIEW_ANGLES_SECTION_RE`, and `_CORE_REVIEW_ANGLE_HEADER_RE` in `test_design_decision_files.py`, for matching headers and bounding angle blocks.
- The module's `_*_violations()` plus `TestFaultInjection` idiom.
- `hashlib.sha256` from the standard library.
- `docs/design-decisions/restated-canonical-rule-review-angle.md` as the template for the decision record.

## Verification

1. **Phase 0 gate passed.** Record the per-site catch counts in the PR body.
2. **Scoped tests.** Run `.venv/bin/python3 claude/.claude/scripts/select-tests.py` from the worktree root. It should select the hooks, skills, scripts, and review-bench tests through the rules for agents, `code-review/SKILL.md`, docs, and README. Confirm that these pass by name:
   - `test_item_12a_index_matches_agent_angle_headers`
   - `test_scope_cited_angle_names_resolve_to_angle_headers`
   - `test_scope_exempt_row_resolves_to_real_change_type_row`
   - `test_comment_discipline_row_points_to_its_deferral`
   - `test_code_review_staged_diff_instruction_lives_in_its_own_note_only`
   - `test_description_length`
   - assertion 9 and its fault-injection tests
   - the design-decision shape assertions, run against the new record
3. **Lint.** Run `.venv/bin/ruff check claude/.claude/`. No shell files change, so ShellCheck does not apply.
4. **Phase 2 outcome reported, including the failed bars** (see Phase 2 outcome). The fixture's `note` is the one durable home for the fixture-cell rates; this plan's Phase 2 outcome is the session record it is copied from. The PR body points at the note and holds only the uncommitted site-replay counts. It names every item in Phase 2 outcome's "Reported as not achieved" list: #972 site b, the old-body cell, and #972 site c.
5. **Review pipeline** (per `.claude/rules/review-pipeline-dispatch.md`):
   - `/skill-review` for `code-review/SKILL.md`, hook-enforced at commit;
   - `/agent-review` for both agent files;
   - the *Reshapes reviewer ownership* row matches the row-scope and Scope-section edits, so spawn `comment-discipline-reviewer` for it;
   - `/ready-for-review`'s cumulative pass then runs the comment row over the full diff.
6. **Dogfood.** In that cumulative pass, every prose site the diff adds or rewrites must pass Paragraph readability: the new angle text, the Multi-fact carve-out, the agent's Scope, description, and intro, the row, item 12a, `code-writer.md` line 138, the decision record, the CHANGELOG entry, and the README edit. If the decision record cannot be worded honestly in a way that passes, the angle's predicate is wrong and goes back to design.
7. **Redaction.** The commit passes `deny-private-project-refs.sh`, and the fixture holds no tracker-ID shape.
8. **Ticket disposition.** The PR body links the follow-up issue on #928's zero spawns (Out of scope), which must be opened before the PR. GH-1006 closes at merge. Whether that happens through a closing keyword or a manual close is the engineer's call when the PR opens. #972 site b is wontfix, and the decision record carries it as a known residual. `[engineer-verified: "Wontfix, close at merge (Recommended)"]`
9. **Agent length.** The agent file will run to about 220 lines after M1, M2, and M5 (a), past `agent-review`'s 200-line target. Every added line is an anti-pattern description or a carve-out that changes the reviewer's verdict on a realistic input, which is the load-bearing reason that checklist accepts (row 50). `code-writer` reports the final count in its return.

## Out of scope

- **#919's citation-placement shape** stays with issue #1005. `[engineer-verified: "Yes, leave to #1005 (Recommended)"]`
- **Why #928 spawned no reviewer** (row 13). Neither change in this plan addresses it. Recommended follow-up issue: use `review-trace` to check whether `/code-review` or `/ready-for-review` ran on that branch at all.
- **The "Split multi-fact comments" bullet in global `CLAUDE.md`.** Not edited. M2 stays inside its wording (row 7), and any edit there reaches every stow consumer's always-loaded context.
- **`tighten-prose/SKILL.md` §1 and `docs/skills.md` line 30.** Both describe the reviewer by example ("decides whether the content earns its place"), and both stay accurate. Editing them would only pull `/skill-review` into a prose change.
- **New plan files.** They stay in scope for Multi-fact comment structure only.
- **Extending `disposition-fidelity` to agent files, and mining the complaint sites into the review bench as `pr-comment` defects.** The review bench is the durable home for site-level regression evidence. Each of these needs its own harness work (row 28).
- **Numeric density thresholds, or a hook that measures density** (see Alternatives).
- **Running an LLM reviewer in CI** (G1). `evals/README.md` § "Why local only — never CI" records that decision, so every CI check on the angle is structural. Without this condition the fixture would become a model-in-the-loop eval and M7's hash pin would be unnecessary.
- **Changing the Step 0.6 deferral** (G2). `docs/design-decisions/comment-discipline-reviewer-deferred-to-cumulative-pass.md` records that decision: the comment row's pass runs once per cumulative review of a new HEAD, not per commit-gate round. Without this condition the row would also fire per commit-gate round; nothing else in this plan would change.
- **Editing `.claude/rules/skill-and-agent-self-review.md`.** The decision record holds the exception (row 25).
- **Rewriting the complained-about paragraphs in `docs/private-project-redaction.md`.**
