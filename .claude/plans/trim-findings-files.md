# Trim reviewer findings files

## Context

Goal: cut what the review loop reads from reviewer findings files, without narrowing reads of FYI or other non-blocking findings.

Ask: "maybe we move to trimming the findings file" and "narrowing finding reads are problematic because that ignores FYIs and other non-blocker/concern items". The task description came from an agent-authored handoff file the engineer invoked `/plan-it` with, and names three candidate trims: the Recommendations restatement, ledger restatements, and unprompted Answers. Step 4 selections: "Remove it (Recommended)", "Cap it to pointers (Recommended)", "Same PR (Recommended)".

Why now: in sampled findings files from this repo's own review rounds, the per-finding sections are the largest share, followed by `## Answers`, then `## Recommendations`, then restatements of prior decisions. Every character a dispatcher reads stays in its context for the rest of its session (G1), so the text the loop does not need for dispositions is re-read on every later turn.

## Approach

Delete the `## Recommendations` section from the shared `### File-based output` block in all 10 reviewer agents. Instead, each finding's H2 carries a numbered id and a severity tag, most severe first. Cap `## Answers` to pointer lines, and write confirmations of prior decisions that gain no new site as one line of ids. Both findings-file dispatchers (code-review and plan-review) then read the whole file whatever its finding count, and the existing recipe-sync test is extended to pin that phrase. Two new block-content tests pin the heading template and the absence of `## Recommendations`, and the pointer line's `<N>` is defined as the count of `R<n>` headings.

Alternatives set aside:
- **Shrink Recommendations to id-plus-severity lines.** This still repeats every heading's tag.
- **Read only Recommendations when the count is 0.** This narrows reads, which row 1 forbids.
- **Drop Answers entirely.** A "no issue" answer would have no home.
- **Put the prior-decision rule in the spawn-prompt text** (`code-review/SKILL.md:268`, `ROUTING.md:58`). That gives the rule two homes and misses ready-for-review's spawn. The shared block is the single place that defines the file's shape.
- **Write a new design-decision file.** The file-based-output decision is amended, not overturned. A second file would split the description of the findings-file format across two files.

**Root:** Reviewer findings files carry text the review loop does not need for dispositions:
- a Recommendations section that restates every finding;
- Answers sections that restate analysis;
- prior decisions restated row by row.

Every character a dispatcher reads stays in its context for the rest of the session.

**Givens:**
- G1. A `Read` result stays in the reading agent's context for the rest of its session (the main thread, or `review-orchestrator`'s context when routing is on). The harness's context model imposes this.
- G2. The block must be duplicated across agent files, because agent bodies have no import mechanism (`test_agent_roster.py:305-307`). The platform imposes this.
- G3. Downstream project-layer reviewers may keep their own copy of the old block. Their owners maintain that coupling (CLAUDE.md, "Other repos may consume this one's layout; that coupling is theirs to maintain").

**Mechanisms:**
- M1. Delete `## Recommendations`. Each finding H2 becomes `## R<n> [<severity>] <angle-name>`, most severe first. anchors: root, row2, row12. Two lighter options were checked: a summary of id-plus-severity lines (still duplicates the heading tags), and keeping Recommendations as the only read when the count is 0 (narrows reads, against row1). Deleting the section is the lightest option.
- M2. Both dispatchers `Read` the whole findings file, whatever its finding count. The phrase is pinned by adding it to the existing `_FINDINGS_PATH_RECIPE_TOKENS` tripwire. anchors: row1, row6, row19. The lighter option, no pin, leaves nothing to stop a later edit from narrowing reads again. A separate new test would duplicate the tuple's existing two-dispatcher sync.
- M3. `## Answers` holds one or two lines per question, citing an `R<n>` finding or the evidence. anchors: root, row4, row14.
- M4. Prior decisions the current text still matches with no new site, and earlier fixes confirmed landed in full, go on one line by id. Anything else about a prior decision is a finding citing its id: a new site it would cover, a contradiction, a reopened row, a missing or partial fix, or an unparseable record. anchors: root, row16, row21, row30, row31.
- M5. A review with no findings writes a line saying so. anchors: row13.
- M6. Move the anchor for skill-fidelity's dismissals section from "before `## Recommendations`" to "after the per-finding H2s". anchors: row22.
- M7. Amend the design doc's read-back sentence in place. anchors: row9, row18.
- Over-powered-primitive check: no mechanism here is heavier than the surface it edits. Every change is a deletion or rewording in an existing file, plus three test edits: one tuple entry, one re-pinned assertion, and the two block-content tests. The heavier candidate was a hook or parser that validates findings-file shape at write time. It is set aside because the byte-identity test (row8) already keeps the 10 specs uniform, and nothing downstream parses the shape (row10).

**Assumptions:**
1. [engineer-verified: "narrowing finding reads are problematic because that ignores FYIs and other non-blocker/concern items"] No dispatcher may read less than a reviewer's whole findings file, FYI findings included.
2. [engineer-verified: "Remove it (Recommended)"] Remove the `## Recommendations` section.
3. [unverified] The session's own proposal behind row 2, not the engineer's words: each per-finding H2 carries its `[BLOCKER]`/`[CONCERN]`/`[FYI]` tag, and the read-back becomes one full read. This plan adopts it and adds an `R<n>` id (row 12) and a no-findings line (row 13).
4. [engineer-verified: "Cap it to pointers (Recommended)"] Cap `## Answers` to pointers.
5. [unverified] The session's own proposal behind row 4: an answer is one or two lines citing the finding it rests on, with no new analysis, and an answer that needs analysis becomes a finding. This plan widens "the finding" to "the finding or its evidence" (row 14).
6. [engineer-verified: "Same PR (Recommended)"] Change plan-review's read-back in this PR.
7. [unverified] The session's own proposal behind row 6 included fixing `ROUTING.md:52`'s §12 cite. This plan drops that fix (row 17).
8. [verified: `claude/.claude/hooks/tests/test_agent_roster.py:48-50`, `:361-393`] The `### File-based output` block is byte-identical across all 10 CANARY_AGENTS, apart from the H1 line, and a change must land in all 10 at once.
9. [verified: repo-wide grep for `Recommendations` and `[BLOCKER]`, excluding `.claude/plans`; `claude-skills/skills/tests/test_skills.py:1124`] The only sites are the 10 agent blocks, `skill-fidelity-reviewer.md:222`, `code-review/SKILL.md:302`, `plan-review/ROUTING.md:52`, and `docs/design-decisions/reviewer-findings-path-output.md:5`. No test, hook, script, eval or plugin asserts on the removed tokens today; item 8 adds a test that asserts their absence. One test pins the neighboring phrase "between the per-finding H2s and" that the `skill-fidelity-reviewer.md:222` edit rewrites (Critical files item 7).
10. [verified: `_review-ledger-lib.sh:738`; `review-orchestrator.md:16-19`, `:62-64`; `review-loop-cost-audit/SKILL.md:125-127`] No consumer parses findings-file sections or R-ids. The orchestrator reads through code-review's rule. Its return names findings by checklist id and `file:line`.
11. [verified: `claude-skills/skills/ready-for-review/SKILL.md:100`] ready-for-review already reads the whole file, so it needs no edit.
12. [verified: `plan-review/ROUTING.md:83-110`; `code-review/SKILL.md:432-464`; `_review-ledger-lib.sh:347-351`] The `R` prefix collides with no checklist id and no ledger id. `F` would collide with plan-review's F1–F5, and bare numbers with code-review's numbered items.
13. [verified: `docs/design-decisions/reviewer-findings-path-output.md:5`; one sampled findings file in this repo's own `agent-reviews/` corpus] People read `agent-reviews/` files directly, and sampled reviewers put their verdict inside `## Recommendations` ("No ... concerns", "Approve with concerns"). Removing that section removes the only place file-mode output states a verdict, while every inline output format still ends with one, so a review with no findings needs an explicit line. The orchestrator already gets "Found 0 issues" from the pointer line.
14. [verified: `claude-skills/skills/code-review/SKILL.md:396`] A reviewer's FYI defaults to ADDRESS. Forcing a "no issue" answer into an FYI finding would therefore add a default-ADDRESS row, so answers may cite evidence directly.
15. [unverified] No dispatch question needs a multi-line answer that is not a finding. The spawn prompts written in skill text were checked (`code-review/SKILL.md:266-268`, `ROUTING.md:35`, `ready-for-review/SKILL.md:100`). The free-text questions an orchestrator composes at runtime were not.
16. [verified: one sampled findings file in this repo's own `agent-reviews/` corpus, one review round] The restatements of prior decisions come from reviewers listing rows taken from what spawn prompts hand them (`code-review/SKILL.md:268`; `ROUTING.md:35`, `:58`). Only one round was sampled, so how general this is stays unchecked.
17. [verified: `docs/design-decisions.md:5`; `claude/.claude/hooks/tests/test_design_decision_files.py:29-34`, `:120`] Legacy `docs/design-decisions.md` §N cites are a supported form, barred only for the reassigned §59 and §60. The §12 cite at `ROUTING.md:52` resolves.
18. [verified: `claude/.claude/hooks/tests/test_doc_counts.py:480-484`] The phrase "All eight reviewer agents write structured" is pinned to `len(REVIEWER_AGENTS)`, so the doc edit must leave it byte-intact.
19. [verified: `claude-skills/skills/tests/test_skills.py:7323-7354`] The tripwire requires each token to appear in both `code-review/SKILL.md` and `ROUTING.md`. The existing tokens `agent-reviews/<agent-name>-<suffix>.md` and ``not `run_in_background` `` must survive the edit.
20. [verified: Step 3 exploration, `code-review/SKILL.md` at 496 lines against the 500-line ceiling in `docs/skills.md`] The edit to `code-review/SKILL.md:302` stays within that one line, so the file's line count does not change.
21. [verified: `plan-review/ROUTING.md:58`; `plan-review/SKILL.md:271`; `code-review/SKILL.md:268`] Reviewers must still report reopened rows, missing fixes and unparseable records in full. The one-line rule must not suppress them.
22. [verified: `claude/.claude/agents/skill-fidelity-reviewer.md:222`, `:241`] The dismissals section is anchored to `## Recommendations`. `## Architect consults observed` is anchored to the dismissals section and needs no change.
23. [verified: CLAUDE.md, "Changes under `claude/.claude/**` go live on `git pull`"] The agent blocks and dispatcher skills go live together when the merged change is pulled. This branch's own review rounds therefore still run the old pair. A session that loaded the old code-review text before a pull still reads a new-shape file whole, because `Read` returns the whole file unless given an offset.
24. [unverified] The ordering of section sizes in the Context paragraph comes from a throwaway script and a few sampled files, not from a committed measurement. The plan states no figures.
25. [engineer-verified: "Both are mine"] The two quotes on the Ask line ("maybe we move to trimming the findings file" and "narrowing finding reads are problematic because that ignores FYIs and other non-blocker/concern items", also quoted in row 1) are the engineer's own words.
26. [engineer-verified: "R<n> ids in headings"] Keep the `R<n>` ids in finding headings (M1).
27. [engineer-verified: "No-findings line"] Keep the no-findings line (M5).
28. [engineer-verified: "Prior-decision one-liner"] Keep the prior-decision one-liner (M4).
29. [engineer-verified: "Test pin on read-back"] Keep the test pin on the read-back phrase (M2).
30. [verified: `code-review/SKILL.md:376`] A new site with the same failure mode as a live DEFER is a repeat, which the orchestrator logs as a carry using the reviewer's `--cited-line`. A bare id on the one-liner would drop that site.
31. [verified: `code-review/SKILL.md:268`] The ledger digest never suppresses a finding the current text supports, so the one-liner covers only prior decisions the current text still matches with no new site.
32. [verified: `plan-review/ROUTING.md:35`; `code-review/SKILL.md:268`] Re-reviews hand reviewers earlier rounds' findings files and disposition records, and every round numbers from `R1`. An `R<n>` is therefore meaningful only inside its own file.
33. [engineer-verified: "Yes, add the clause (Recommended)"] Define `<N>` in the pointer line as the count of `R<n>` headings, FYIs included (Critical files item 1). The yield metric parses the pointer line (`transcript_analysis/reviewer_yield.py:35`, `:212-215`), so the definition makes its zero bucket match the files.
34. [engineer-verified: "Add it (Recommended)"] Add the block-content assertion to `test_agent_roster.py` (Critical files item 8).
35. [unverified] A Fable-model consult read the committed plan and found the foundation sound and the scope right for one PR. Its claim that the whole file is already read in practice, since `Read` has no section addressing, rests on its reading of the dispatcher text, not on a transcript measurement.

## Critical files

Use one `code-writer` dispatch, not split. Three reasons:
- The 10 agent edits must land together for `TestFileBasedOutputBlockConsistency` to pass.
- The two dispatcher sentences and the test tuple share one exact phrase.
- Every file depends on the same new file-shape context, so a split would restate it in each prompt.

1. The `### File-based output` block in all 10 files: `claude/.claude/agents/{ciso-reviewer,comment-discipline-reviewer,skill-fidelity-reviewer,staff-analytics-engineer,staff-backend-engineer,staff-data-engineer,staff-frontend-engineer,staff-platform-engineer,staff-product-engineer,staff-sdet}.md`.
   - Replace everything from "Structure the file as:" through the item-2 Answers sentences with the text below, identical in every file.
   - Keep each file's own `# <agent-name>` H1 line.
   - Keep the `_BLOCK_END_SENTINEL` line as the block's last line.
   - The fenced text below is indented deeper than the agent files. Match each file's existing indentation, taking `staff-sdet.md`'s current block as the anchor: "Structure the file as:" sits at 3 spaces, its sub-bullets at 3 spaces, and "2. Return inline" at column 0.
   ```
      Structure the file as:
      - `# <agent-name>` (H1 title)
      - One H2 per finding, most severe first: `## R<n> [<severity>] <angle-name>`,
        numbering findings from `R1`, with `<severity>` one of `BLOCKER`,
        `CONCERN`, or `FYI`; then file:line, issue, production failure mode,
        required property
      - `R<n>` is local to this file: cite a finding in another file by that
        file's name and its id
      - With no findings, a line saying so
      - No summary section — the parent reads the whole file
      - `## Answers`, only when the dispatch prompt poses specific questions:
        one or two lines per question, citing the `R<n>` finding or the
        evidence (file:line, or the search you ran) it rests on. An answer
        that needs more support than that is a finding: write it as one and
        cite it.
      - Prior decisions the prompt hands you (a ledger digest, disposition
        records, assumption-ledger rows) that the current text still matches
        with no new site, and earlier fixes you confirmed landed in full: name
        them on one line, by id where one exists, without restating them.
        Anything else about one is a finding that cites its id: a new site it
        would cover, a contradiction, a reopened row, a missing or partial fix,
        or a record you cannot parse.
   2. Return inline **only** the pointer line:
      `Wrote findings to <path>. Found <N> issues. <One-sentence summary>.`
      `<N>` is the number of `R<n>` headings, FYIs included.
      Do not include findings inline when `findings_path` is present (the parent
      reads them from the file) — doing so is a defect.
      Answer the prompt's questions in the file, not in the inline return.
      The inline summary stays one sentence regardless of how many questions
      the prompt asks.
   ```
2. `claude/.claude/agents/skill-fidelity-reviewer.md:222`: replace "between the per-finding H2s and `## Recommendations`" with "after the per-finding H2s".
3. `claude-skills/skills/code-review/SKILL.md:302`: replace "`Read` the findings file — Recommendations section first, full file when count > 0;" with "`Read` the whole findings file, whatever its finding count;". This is an in-line edit with no added lines.
4. `claude-skills/skills/plan-review/ROUTING.md:52`: replace "`Read` the findings file, starting with `## Recommendations` and reading the full file when the count is non-zero." with "`Read` the whole findings file, whatever its finding count." Keep the §12 cite unchanged.
5. `claude-skills/skills/tests/test_skills.py:7299-7326`: add the token ``"`Read` the whole findings file, whatever its finding count"`` to `_FINDINGS_PATH_RECIPE_TOKENS`. This reuses the existing two-dispatcher tripwire rather than adding a new test.
   - Reword the comment at `:7301` ("two surviving contract tokens") and the comment at `:7322` (ready-for-review is "not a verbatim match for these two tokens") so both cover the read-back token.
   - Say in the comment that the pin guards removal of the sentence, not a qualifier appended later, and that ready-for-review is left out because its read-back sentence carries no scope qualifier.
   - Add the token first and run the tuple test red against the unedited dispatchers, then make the two dispatcher edits and confirm green.
6. `docs/design-decisions/reviewer-findings-path-output.md:5`: replace "The parent reads the file after the reviewer returns, starting with the `## Recommendations` section." with: "The parent reads the whole file after the reviewer returns, whatever its finding count. The file has no summary section: each finding's heading carries its severity tag, so a summary would only repeat text the parent already reads." Leave "All eight reviewer agents write structured" and the byte measurements untouched.
7. `claude-skills/skills/tests/test_skills.py:1124`, in `test_file_based_output_relaxes_heading_to_suggestion`: change `assert "between the per-finding H2s and" in body` to pin the new placement wording "after the per-finding H2s". Leave the relaxation-language assertion at `:1126-1127` unchanged. Keep the edited sentence at `skill-fidelity-reviewer.md:222` on one physical line, because the match is a raw substring.

8. `claude/.claude/hooks/tests/test_agent_roster.py`, in `TestFileBasedOutputBlockConsistency` (`:301`): add two separate test methods, read through `_extract_file_based_output_block` on the first of `CANARY_AGENTS` rather than the whole file. One asserts the block contains the `## R<n> [<severity>] <angle-name>` heading template, the other asserts it does not contain `## Recommendations`. The byte-identity test then spreads both to all 10 agents. Run each red against the unedited agents before the block edit. Keep the heading template on one physical line in the agent files, because the match is a raw substring.

Reuse: `TestFileBasedOutputBlockConsistency` (`test_agent_roster.py:301`) already enforces the 10-file sync, so the new assertion is the only edit that file needs.

Effort: about 15 files in one domain (reviewer-contract prose). The risk sits in the 10-file identical block, which a test enforces, and in two read-back sentences.

## Verification

- Run `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py` after the edits. It runs the selected suites rather than listing them, and its `running ...` line names directories, not test files. Pass criteria:
  - The `running ...` line lists `claude/.claude/hooks/tests` and `claude-skills/skills/tests`, or says it fell back to the full suite.
  - The configured pytest prints no line for a passing test, so pass `-v` through `select-tests.py` (it forwards extra arguments to pytest). The output then shows `TestFileBasedOutputBlockConsistency`, `test_findings_path_recipe_tokens_present_in_code_review_and_plan_review`, `test_file_based_output_relaxes_heading_to_suggestion`, the two new block-content tests from item 8, and the doc-count and design-decision tests as PASSED.
  - If a directory is missing from the line, report a gap in select-tests' rule table rather than widening the run by hand.
- After the dispatcher edits, revert `plan-review/ROUTING.md`'s read-back sentence (the red-first run aborts at the code-review assertion and never reaches it), confirm the tuple test goes red, restore the sentence, and finish with a green run of `test_findings_path_recipe_tokens_present_in_code_review_and_plan_review`.
- Run `git grep -n 'Recommendations' -- . ':!.claude/plans'` and expect hits only in `claude/.claude/hooks/tests/test_agent_roster.py`, and only the absence assertion and its message there.
- Run `wc -l claude-skills/skills/code-review/SKILL.md` and expect 496 or fewer lines.
- Review pipeline:
  - `/code-review` invokes `skill-review` for `code-review/SKILL.md` and `plan-review/ROUTING.md`. Both are hook-gated (`plugins/skill-management/hooks/require-skill-review.sh:201-203`).
  - It invokes `agent-review` for the 10 agent files.
  - It spawns `comment-discipline-reviewer` for the prose.
- Behavioral check, after merge only: this branch's own review rounds still run the old block (row 23). After merging and pulling, spot-check that one findings file has `R<n> [<severity>]` headings that still name the checklist item where one applies, no summary section, and pointer-only Answers. Also confirm that a repeat at a live DEFER site appears as a finding citing the decision's id, not inside the one-line list of prior decisions. Run one round whose spawn prompt hands a prior-decision digest containing a row the reviewer will contradict, and expect that row written in full, not on the id line. Run one plan-review round whose revision contradicts an `[engineer-verified: "<quote>"]` ledger row, and expect it written as a finding for the human. Obtain a zero-finding reviewer file by aiming a round at a trivially clean diff, and inspect it for the no-findings line. No pre-merge check exists for these, so they are checked after merge only. List all of them as unchecked in the PR body: the heading spot-check (including that headings still name the checklist item), the live-DEFER repeat, the contradicted prior-decision row, the `[engineer-verified]` contradiction, and the zero-finding file; each run is one sample of stochastic behavior and can show a failure but not its absence.

## Out of scope

- Converting legacy `docs/design-decisions.md §N` cites, including `ROUTING.md:50`/`:52`, `code-review/SKILL.md:300` and `ready-for-review/SKILL.md:100`. The form is supported (row 17), and a conversion across about 150 sites is its own change.
- The "All eight reviewer agents" phrase in the design doc. The test pins it to the stack-specialist count (row 18).
- Each agent's Inline output verdict-line lists. That path runs only when a write fails and is capped at 2K tokens.
- `agent-review` checklist item 15's "(`staff-*` and `ciso-reviewer` only)" wording. It is narrower than the 10-agent canary set, but it concerns contract presence, not file shape.
- skill-fidelity-reviewer's inline verdict tags (`[SILENT-SKIP]` and the others) alongside the shared severity tags. This is pre-existing and unchanged.
- The audit follow-ups: the routing default, handoff scoping, and the rest.
- A presence pin on the block's "No summary section" sentence. The absence test pins one heading name; a summary re-added under another name would pass. Whether to pin the sentence too is the engineer's call.
- Bounding the length of per-finding bodies in file-mode output. It is the only lever that would cut substantially, but the design doc says the file exists to give humans each specialist's full analysis, so it is a separate trade.
- A write-time hook or parser that validates findings-file shape. It is heavier than needed (see the over-powered-primitive check).
