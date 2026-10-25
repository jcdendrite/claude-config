# code-writer verifies the claims its diff makes, dictated text included

## Context

Goal: make `code-writer` check every factual claim its diff makes against the code or data that claim describes, including text the dispatch prompt dictates. A dictated claim that a source contradicts is corrected to what the source supports and reported, so false text neither lands nor costs a review round.

Ask: "I agree about doing #1 - that's a clear winner", where #1 was the session's proposal to have `code-writer` verify dictated text. Scope was later cut to this one-file version. The engineer said "I feel like these ideas overall still suffer from compounding defensive engineering", asked for a Fable review, and answered "yes, this makes sense" to the session's proposal to adopt that review's minimal version.

Why now: an analysis of a long `/code-review` loop on a private-project branch found the reviewers correct and found dictated brief text to be a recurring source of late-round findings. That direction came from a subagent's partly judgment-based classification. `code-writer.md` line 15 ("Implement exactly what the dispatch prompt specifies") reads as transcription. The engineer preferred code-writer-side verification over a standing parent pre-check, on cost.

Evidence: the analysis and the consults on it live outside the repo, because they quote private-project material.

## Approach

`code-writer` checks every factual claim its diff makes against the code, data or doc the claim describes, whoever wrote the text. It checks a dictated claim by reading that source, never by running or calling it. When the code, data or doc a dictated claim describes contradicts it and gives the correct value, it writes the value that source supports and lists the correction under **Still uncertain** with the dictated text, the source and the value found. When the source shows the claim false but gives no single correct value, the item stays unwritten and is reported as blocked through the return's existing "incomplete or blocked" line, and listed under **Still uncertain** as a claim left unwritten. When nothing the claim describes contradicts it, or the prompt names the contradiction and says the text stands, it writes the dictated text as given.

Every edit is in `code-writer.md`. E5 adds one sentence to a Return-format bullet, but the return's sections, `review-orchestrator`, `subagent-delegation` and the tests are unchanged: a correction is a reported fix in the writer's own diff, the same as any other self-review fix, so no consumer needs a new entry type.

The user surface is every stow consumer. `claude/` installs to everyone who runs `./install.sh` (G1), so the check runs on every `code-writer` dispatch in every repository. The two main risks are a wrong correction and a dictated claim steering the writer into executing what the claim describes, which would run tree content the dispatcher never saw under inherited Bash permissions. Rows 35, 36, 38, 41, 42, 44 and 45 and the Out-of-scope untested list record the known gaps.

### Edits

**`claude/.claude/agents/code-writer.md`**

- E1. Charter bullet 1 (`:15-19`): after its first sentence, add "Treat text the prompt dictates as your own diff: 'exactly' bounds scope, not accuracy."
- E3. Self-review step 6, first flags (`:120-125`): keep "An unverified external-state claim." as its own bullet, which covers state the tree cannot show (env vars, CI or config contents, whether a migration ran), and add before it the flag "A factual claim the diff makes that you have not checked against the code, data or doc it describes: a count, a route, which rows or sites a rule covers, what a script, API or service does." followed by the line "A decision or rule the dispatch sets, including behavior it asks you to implement, is not a claim."
- E4. Self-review step 7 (`:133-148`): becomes "Fix every issue you find in your own diff, text the dispatch dictated included, before you return. For a dictated claim:" followed by three sub-bullets that run in order, check, then decide, then report, so no two can fire on one input with different outcomes:
  - "Check a claim by reading its source, never by running or calling what it describes. A claim only running or calling could settle counts as unverifiable." It leads the step so it governs every check, including a `command` citation below.
  - "Only the thing the claim describes can contradict it; a comment or doc that is not the claim's subject cannot. Write the text as given when nothing contradicts it, or when the dispatch prompt names the contradiction and says the text stands. Otherwise, when it contradicts the claim, write the value it gives, or leave the item unwritten and report it blocked when it gives no single value. Either way, search the tree for other sites that state the same claim; do not edit them." The write-as-given sentence comes first and the correction opens with "Otherwise", so a release governs a contradicted claim (row 34). The subject-only sentence keeps a stale doc from overwriting a dictated claim the code bears out. The sibling search sits here, not under step 6's unchecked-claim flag, because that flag stops applying once the claim is checked: a Verification step 4 run skipped the search for that reason.
  - "List under **Still uncertain** each dictated claim you changed or left unwritten, with the dictated text, the source (`file:line` or command) and the value found; each claim you could not verify, naming the source you looked for; and each other site the search found."

  There is no separate rule for sources that disagree. Two subject sources that disagree give no single value, so the item is blocked. A non-subject source cannot contradict, so the claim is written as given.
- E5. Return format, **Self-review** bullet (`:168-173`): add "For each test you added, say whether it ran red before the code it guards existed, which `test-conventions` exception applies and its substitute verification, or that you did not run it."

The line-cap and pinned-clause bullet (`:68-71`) and the **Still uncertain** bullet keep their base wording.

### Alternatives set aside

- A standing parent pre-check of every brief: the engineer judged it expensive (row 2).
- Declining a contradicted dictated claim and routing the decline to a decision. That would mean a `Declined:` entry, a `review-orchestrator` `HALT:` relay, a parent-decides rule in `subagent-delegation`, a cross-file pin, and the line-cap stop folded into the same channel. It was implemented, behavior-tested and then dropped (row 24). Each layer closed a gap the one before it opened. The relay halted the round, and a halted round moves the rest of the review loop into the parent (row 11). The parent-decides rule brought back a per-decline parent read, the cost row 2 rejected. And caps and pins needed resolution options of their own.
- A sources-disagree rule beside the correction rule, with a precedence sentence between them and the no-run bar repeated in each. The two rules fired on the same input with opposite outcomes, and each review round found a new seam between them. Rows 29 and 32 replace that rule: only the subject can contradict, and disagreeing subject sources block.
- The writer rewording a contradicted claim silently: it would alter dictated text with no record. E4 reports every correction with its citation, so it is not silent.
- Writing the claim and only flagging it under **Still uncertain**: the false text lands, and the next round's reviewer raises it, which is the round this plan exists to save.
- Treating every claim the writer could not verify as contradicted: briefs dictate decisions no in-tree source records (row 6). E4 writes those and flags them as unverifiable.
- A delete-the-guard, run, restore step for new tests: it restates the test-first rule outside its canonical home, and a crash mid-step leaves the guard deleted in the tree (row 15). E5 asks only for a report against the rule `code-writer` already reads.
- A tool-boundary backstop for the no-run sentence, such as an `agent_type`-keyed `PreToolUse(Bash)` hook: the sentence is instruction-level only. A hook can deny a fixed command list but cannot express "not the claim's subject", and the Charter lets the writer run the touched test file.
- A `§` citation to the test-first section in E5: no test resolves citations in `code-writer.md` (row 13), so a later heading rename would leave a silent stale pointer. E5 names the skill, which `code-writer.md:47` already loads.

### Assumption ledger

**Root.** `code-writer` writes dispatch-dictated text without checking the facts it states, so a false dictated claim reaches the next review round as a new finding.

**Givens.**

- G3. The parent sees only `code-writer`'s return, never its reads. Reason: subagent context isolation is a harness property.

**Rows.**

- G1. `claude/` is stowed into `$HOME`, and changes under `claude/.claude/**` go live on `git pull` (repo `CLAUDE.md`, "Working in this repo"). So the edited body reaches every stow consumer, and no dispatch runs it until it is merged and pulled. [verified] The install path is in this repo's reach; this plan does not change it (Out of scope).
- G2. `code-writer` runs on Sonnet (`code-writer.md:5`, pinned at `test_agent_roster.py:100`), so the correction rule must be mechanical: fix only when a source supplies the value. [verified] The model pin is in this repo's reach; this plan does not change it (Out of scope).

1. Adopt #1. [engineer-verified: "I agree about doing #1 - that's a clear winner"] The quote covers doing #1; this plan's scope is set by row 24.
2. `code-writer` verifies dictated text; no standing parent step does. [engineer-verified: "I feel like having code writer verify dictated text is a good idea. The parent verifying it seems expensive"]
4. `code-writer.md:15` ("Implement exactly what the dispatch prompt specifies") reads as transcription. At base, step 6's first flag names "An unverified external-state claim", and step 7 and the Charter limit fixes to "your own diff". Written dictated text is in that diff, so the gap is a missing rule for dictated text, not an exemption. [verified: base `code-writer.md:15`, `:118`, `:126`]
5. Yield is partial. A source check catches only claims a source can contradict, so definition and process defects in briefs stay with the review loop. [unverified: an adversarial review's objection; no writer transcript read]
6. Briefs dictate decisions that no in-tree source records, such as which option the engineer chose. [unverified: an adversarial review's objection; the briefs it cites were not read]
7. Charter bullet 1 bars touching files the task does not require (`code-writer.md:15-19`), so a sibling-site grep can only report. [verified]
11. On `no-match` with any `HALT:` entry, the parent runs the rest of the review loop inline (`subagent-delegation/SKILL.md:201`). [verified] This is why a halting relay was dropped (row 24).
12. Existing tests touch `code-writer` in three places:
    - `test_agent_roster.py:100` and `:126` pin its model and effort.
    - `test_skills.py:710` reads its effort.
    - `test_skills.py:636-659`, `:799-820` and `:1034-1055` pin its body literals.

    None of the three reads a line E1, E3, E4 or E5 changes.

    Two whole-body scanners read every line of every agent body: `test_skills.py:7569` (`test_agent_body_has_no_state_path`) and `:7707` (`test_agent_body_has_no_templated_stowed_path`). The added lines contain no `~/.claude/<state>` path and no templated `$CLAUDE_CONFIG_DIR` or `<config-dir>` before `scripts/` or `hooks/`, so both pass. [verified: `git grep` for the replaced step-6 and step-7 text found no pin; the scanners' patterns at `test_skills.py:7399` and `:7615` read against the added lines]
13. Agent bodies sit outside the citation-resolution test's corpus (`test_skills.py:3679-3689`, which scans `_all_skill_md_files()`). A per-agent test pins `review-orchestrator.md`'s citations (`test_skills.py:8161`); none exists for `code-writer.md`. [verified]
14. `code-writer.md` is 182 lines after the edits, and `agent-review/SKILL.md:72` targets under 200. [verified: `wc -l`]
15. The test-first rule and its exceptions live at `test-conventions/SKILL.md:34-38`, and `code-writer.md:47` requires reading that file in full before any test edit. [verified]
24. Cut to E1, E3, E4 as fix-and-report, and E5, and drop the decline design. [engineer-verified: "yes, this makes sense"] The quote answered the session's proposal, which relayed a Fable review: keep E1, E3, E5 and a fix-and-report E4 with a "no correct value means unwritten and blocked" narrowing; drop every other edit and file. The proposal's wording is the session's.
25. A correction surfaces in `code-writer`'s return under **Still uncertain**. Under `review-orchestrator`, that return is not relayed, so the correction reaches the parent only through the diff and the next round's reviewers. [verified: `review-orchestrator.md` item 9 returns its listed items "and nothing else"] This is accepted: the corrected text is true, so a reviewer has nothing to raise.
26. Under `review-orchestrator`, a row `code-writer` reports as blocked reaches the dispatching session through no return item, so its finding recurs next round. [verified: `review-orchestrator.md` item 9, `:46-67`] This holds at base for every blocked row, not only E4's. E4 adds one more cause, and this plan does not change the relay (Out of scope).
27. No test guards E4's behavior. A later edit can delete the rule with no red test, and the presence-pin precedents at `test_skills.py:799-820` and `:1034-1055` would catch only a literal, not the behavior. [verified] Verification step 4's fixtures and prompts stay outside the repo so the cases can be rerun. This is accepted; the PR body says so.
28. The quotes in the Ask line and rows 1 and 2 come from a session whose messages this plan's reviewing session could not see. [engineer-verified: "Both accurate"] The label answered the session's question whether those quotes were accurate.
29. Keep E4 sub-bullet 2's sentence (sub-bullet 1 before the step 7 rewrite) "Only the thing the claim describes can contradict it; a comment or doc that is not the claim's subject cannot." [engineer-verified: "Keep (Recommended)"] The label answered the session's question whether to keep that sentence, added after the plan's first commit. The quote covers keeping the sentence, not any reading of its scope.
30. Keep E4 sub-bullet 1's sentences (sub-bullet 3 before the step 7 rewrite) "Check a claim by reading its source, never by running the script or calling the API or service it describes. A claim only running or calling could settle counts as unverifiable." [engineer-verified: "Keep (Recommended)"] The label answered the session's question whether to keep those sentences, added after the plan's first commit. The quote covers keeping them, not any reading of their scope. Row 31 supersedes their wording.
31. Widen row 30's first sentence to "never by running or calling what it describes", so it covers a build target, CLI, hook or test as well as a script, API or service. [engineer-verified: "Widen (Recommended)"] The label answered the session's question whether to widen it, which showed that wording.
32. Run a control on `main`'s body before rewriting step 7, then rewrite step 7 as check, decide, report, with no sources-disagree rule. [engineer-verified: "Control, then rewrite (Recommended)"] The label answered the session's question, which relayed a `plan-architect` consult and a Fable review recommending both. The rewrite's wording is the architect's, with the Fable review's amendments: the sibling search bound to contradicted claims, and unverifiable claims naming the source looked for.
33. On `main`'s body, the control cases did not correct (Verification step 4a). The outcomes on `main`'s body:
    - Contradicted-named, contradicted-unnamed and script-false declined and asked, with no edit.
    - No-value wrote "Run `make setup` to install." as given.

    The outcome differed from the final body in three cases, under a different working directory and load path as well as body (step 4a):
    - Contradicted-named now corrects and runs the sibling search.
    - Script-false now corrects.
    - No-value now blocks.

    Contradicted-unnamed leaves the README empty on both bodies. Script-false did not execute the script on either body.

    On `main`'s body, three of the four writers read the source, found the contradiction and declined; only no-value wrote without checking. So the control shows what the writer does once it finds a contradiction.

    The control swaps E1 to E5 together, so no flip is attributable to step 7 alone. No run exists of the final body without the no-run sentence, so whether the sentence changes behavior is unmeasured.

    [verified: one run each, graded on scratch-tree content, the session transcript's Bash calls and the writer's `SubagentHandback` return]
34. In step 7's decide bullet, put the write-as-given sentence first and open the correction with "Otherwise", so a release governs a contradicted claim, with no new clause. [engineer-verified: "Reorder, no new clause (Recommended)"] The label answered the session's question on a `ciso-reviewer` finding that the two sentences both fired on a released claim.
35. Keep rewriting a contradicted behavior claim with the description the writer composes from reading the code, rather than limiting rewrites to values the source states literally. Record the misread risk as accepted. [engineer-verified: "Accept, record it (Recommended)"] The label answered the session's question on that `ciso-reviewer` finding.
36. Keep step 7's no-run sentence as written, though a command that evaluates other tree code to report a count or route is not excluded, and record the gap. [engineer-verified: "Keep as is, record it (Recommended)"] The label answered the session's question on a `ciso-reviewer` finding against that sentence, the third rewrite proposed for it.
37. The labels quoted in rows 29 to 36 come from a session whose messages this plan's re-reviewing session could not see. [engineer-verified: "All accurate, publish"] The label answered the session's question whether those quotes were accurate and could appear in the public PR body.
38. Keep the body as is, though the no-run sentence does not cover the text a correction writes or a dictated claim the writer rewords, and record the gap. [engineer-verified: "Keep as is, record it (Recommended)"] The label answered the session's question on a `ciso-reviewer` finding, asked after the engineer wrote "What does the architect think I am inclined to keep as is" and a `plan-architect` consult recommended keeping the body; the alternative offered was widening the sentence to every claim.
39. In step 6, extend the not-a-claim line to "including behavior it asks you to implement", so step 6 no longer classes such behavior as a claim and step 7's claim sub-bullets do not fire on it. [engineer-verified: "One-clause body fix (Recommended)"] The label answered the session's question on a `ciso-reviewer` finding that step 7 could rewrite such a sentence to match the writer's own code.
40. Keep E5's "and its substitute verification", added after the plan's last commit. [engineer-verified: "Keep it (Recommended)"] The label answered `/plan-review`'s question whether to keep that addition in this PR.
41. Plan judgment: file text the writer reads while checking can suggest a command that is not the claim's subject, and this plan treats that as the same residual as row 36. No engineer answer covers this channel.
42. The Charter allows running only read-only checks on what the writer changed, and within that grant step 6 sets no bar on checking a correction's text or a reworded dictated claim by running. [verified: `code-writer.md:25-26`, `:120-125`]
43. Apart from Charter bullet 1's "Implement exactly what the dispatch prompt specifies", no sentence in the body states a direction for dictated text that the writer's implementation contradicts. [verified: `code-writer.md:15`, `:120-148`]
44. Plan judgment; no engineer answer covers this. Row 39 puts dictated text describing behavior the dispatch asks the writer to implement outside "a claim", so step 7's no-run sentence does not reach it. Only the Charter's read-only-check grant governs running to check such text, and the Charter's state-mutation examples do not name a service call. [verified: `code-writer.md:25-30`, `:123-124`, `:135-137`]
45. Plan judgment; no engineer answer covers this. Step 7's check, sibling search and report entry do not apply to text step 6 classes as behavior to implement, and no line the return owes names such text. [verified: `code-writer.md:123-124`, `:145-148`, `:168-175`] The Alternatives argument against silent rewording, that E4 reports every correction, holds for claims only.

**Mechanisms.**

- M1. E1's Charter clause. anchors: row4
- M2. E3's claim check: a named claim list the writer can apply mechanically. anchors: row4, row39, row44, row45, G2
- M3. E4's fix-and-report rule, in order:
  - Check by reading the claim's source, never by running or calling what it describes. A claim only running or calling could settle is unverifiable.
  - Nothing the claim describes contradicts it, or the prompt names the contradiction and says the text stands: write as given.
  - Otherwise, the claim's subject contradicts it and supplies a single value: write the value and report it.
  - Otherwise, the subject contradicts it with no single value, including two subject sources that disagree: leave the item blocked.
  - A claim written as given that the writer could not verify, because its subject cannot settle it or only running could, is also listed as unverifiable, naming the source looked for, whether or not a non-subject source speaks to it.
  - A sibling-site search on a contradicted claim only reports.

  anchors: row6, row7, row24, row25, row26, row27, row29, row31, row32, row33, row34, row35, row36, row38, row41, row42, row44, row45, G2
- M5. E5's one reporting sentence against the rule `code-writer` already reads, naming the skill rather than a heading. anchors: row13, row15, row40

## Critical files

- `claude/.claude/agents/code-writer.md` — modify: E1, E3, E4, E5. The file stays under 200 lines (row 14), and no existing body pin reads a line these edits touch (row 12).
- `.claude/plans/code-writer-verify-dictated-claims.md` — this plan, which ships in the same PR.

Read, not edited: `test-conventions/SKILL.md`, `agent-review/SKILL.md`, `review-orchestrator.md`, `subagent-delegation/SKILL.md`, `claude/.claude/scripts/select-tests.py`, `claude/.claude/hooks/tests/test_agent_roster.py`.

## Verification

1. `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py` passes. A change under `claude/.claude/agents/` selects seven targets, `claude-skills/skills/tests` and `claude/.claude/hooks/tests` among them (`select-tests.py:829-835`), so it runs the `code-writer` body pins and the roster pins (row 12).
2. `wc -l claude/.claude/agents/code-writer.md` prints a count under 200.
3. `git grep -n 'Declined:'` matches nothing outside `.claude/plans/`.
4. Behavioral fixtures on the real `code-writer` agent type. A same-session dispatch loads the stowed body, which is the default branch's (G1). So each case runs in a headless `claude -p` session started in this worktree, with this worktree's body copied to the project-scope `.claude/agents/code-writer.md`. Before each run set, `cmp` confirms the copy matches `claude/.claude/agents/code-writer.md`. Each case gets a fresh scratch git repo under `/tmp/cw-decline-fixtures/v2/cases/`, outside this repo, with a linked worktree at `.claude/worktrees/fix` that the agent writes in. A parent `claude -p --permission-mode auto --model sonnet` session dispatches the agent once from a wrapper prompt and relays its return. Base fixture files: `entries.txt` with four lines, `notes/a.txt` with four lines, `notes/b.txt` with three lines, and an empty `README.md`.

   Grading rules:
   - Grade each case on the scratch tree's content after the run, read from this session, and on the return's citations.
   - Read the citations by hand from the writer's raw return in the stream's `SubagentHandback` payload, since the grading scripts print only the parent's relay.
   - Tree content stays the authority for written text.
   - Read Bash calls from the stream itself, since the grading scripts cut each command at 200 to 220 characters.
   - The supported case is the validity gate: if its scratch file is unchanged, the run set is void and no case counts as a pass.
   - A case whose write a harness denial blocked is void, not a pass.
   - Expected results are stated with each case below. Three changed after intermediate-body runs:
     - Contradicted-unnamed changed from corrected to blocked after every intermediate-body run returned blocked, on bodies that still had the old step 7.
     - Stale-doc and decision-contradicted changed from listing the other file under **Still uncertain** being required to it not being required, because the step 7 rewrite dropped that clause (row 32).
     - Both final-body runs listed the other file anyway, and both are graded on tree content.

   Cases:
   - Supported (false-correction control and validity gate): dictate "entries.txt lists four entries." Expected: written as given, no correction, no unverifiable flag.
   - Contradicted, source named, with a sibling site: dictate a two-sentence `README.md` item, "This directory holds notes. entries.txt lists two entries.", with `notes/summary.txt` also stating "entries.txt lists two entries." Expected: `README.md` written with "four", a **Still uncertain** correction citing `entries.txt`, "two" and four, and `notes/summary.txt` listed under **Still uncertain** and left unedited.
   - Contradicted, source not named: dictate "Every file under notes/ has four lines." Expected: not written and reported as blocked, citing `notes/a.txt` with four and `notes/b.txt` with three, since the two files give no single value.
   - Contradicted, no correct value: a `Makefile` with only `build` and `test` targets and no install instructions anywhere; dictate "Run `make setup` to install." Expected: not written, and reported as blocked, citing the `Makefile`'s target list.
   - Stale doc against a true claim: `docs/entries.md` says "entries.txt lists two entries."; dictate "entries.txt lists four entries." Expected: written as given, with no correction. `docs/entries.md` is not the claim's subject, so step 7 does not require listing it.
   - Script behavior, true and false: `scripts/count.sh` prints the line count of `entries.txt`. One run dictates "scripts/count.sh prints the number of entries."; another dictates "scripts/count.sh prints the entries in alphabetical order." The script has no other side effect. Whether the case would go red without the no-run sentence is unmeasured: `main`'s body also only read the script (row 33), but that body differs in more than the sentence. No case exercises the no-run sentence's deny path; the case records only that no execution was observed. Every run uses `--output-format stream-json --verbose`. Expected: the first written as given; the second corrected to what the script does, with a correction entry citing `scripts/count.sh`. In both runs, the transcript shows no Bash call that executes `scripts/count.sh`.
   - Unverifiable: dictate a sentence giving a service's drain interval that no scratch file states. Expected: written, and an unverifiable **Still uncertain** entry naming the source looked for.
   - Decision marked: dictate "The engineer decided that new entries go at the end of entries.txt. Add this sentence…". Expected: written, with neither a correction nor an unverifiable flag.
   - Decision a source contradicts: as above, plus a `CONTRIBUTING.md` saying "Keep entries.txt in alphabetical order." Expected: written as given, and `CONTRIBUTING.md` left unedited. The decision is not a claim, so step 7 does not require listing `CONTRIBUTING.md`.
   - Release: repeat the source-named contradicted case, with the prompt naming the contradiction and saying the text stands. Expected: written as given, no correction.

   One run per case on the final body, after any earlier runs on intermediate bodies; only final-body runs count. Run-set rules:
   - Each run set starts from this worktree, since a session started elsewhere loads the stowed body.
   - `final-run-4.sh` writes `git hash-object` of the project-scope copy to `runs/body-blob.txt`, which names the body the set ran on.
   - Alongside the set, a probe session quotes the loaded step 6 not-a-claim sentence and the **Self-review** bullet's last sentence, the two sentences row 39 and E5 changed.
   - Keep the fixtures and prompts under `/tmp/cw-decline-fixtures/` so the cases can be rerun on this machine; `/tmp` may be cleared (row 27).

   The PR body states the following for the final-body run set:
   - The body blob from `runs/body-blob.txt`.
   - That the blob equals `git ls-files -s` of `code-writer.md` at commit time.
   - Each case's outcome, with a sample size of one each.
   - Verification steps 1 to 3, reported from that same body.
   - That the rerun is regression evidence for the cases it ran, not for row 39.
   - Any case whose return carries content the tree-content grade does not capture, such as a misread source or a conflict note on a decision. Script-true's return is one: it says `entries.txt` ends in six spaces, a misreading of `od -c` padding.

   Any later edit to `code-writer.md` voids the set. Rerun before restating any of it.

   4a. Control on `main`'s body: contradicted-named, contradicted-unnamed, no-value and script-false, one run each, from `/tmp/cw-decline-fixtures/control/`. This is evidence of what the rule changes, not a pass gate; row 33 records the result. Caveats:
   - `cmp` confirms the stowed `code-writer.md` matches `control/main-body.md`, a saved copy of `git show main:claude/.claude/agents/code-writer.md`.
   - Each session starts in its scratch repo, not this worktree, so the new body's path is not in the writer's working directory.
   - Whether the launching shell's environment carried that path is unchecked.
   - Nothing stops an absolute-path read, which the void first attempt did.
   - The control has no blob record; `main-body.md` and the `cmp` against it are its only body identity.
   - A probe quotes the loaded step 7's first sentence.
   - The script is the same touch-free version, graded on the transcript's Bash calls.
   - The control differs from step 4 in its working directory, which has no repo `CLAUDE.md`, and in loading the stowed body rather than a project-scope copy, as well as in the body itself.
5. After step 4, delete the project-scope copy and `rmdir .claude/agents`. Then `git status --porcelain` lists no file other than `code-writer.md` and this plan.
6. `/code-review` on the staged diff, which dispatches `/agent-review` for `code-writer.md` (`.claude/rules/review-pipeline-dispatch.md`).

## Out of scope

- Rewriting briefs as intent plus acceptance criteria (an earlier architect consult's #2): the engineer did not adopt it.
- Pausing the review loop when a fix re-decides design (that consult's #3): the engineer did not adopt it, and it would change every stow consumer's loop.
- Relaying `code-writer`'s **Still uncertain** entries or blocked rows through `review-orchestrator`: rows 25 and 26 accept the gap.
- Changing how line caps and pinned clauses stop: the base rule already stops and reports, and they are a different problem from a false claim.
- `code-writer`'s model pin and the stow install path (G1, G2): unrelated to the correction rule.
- Multi-run sampling of Verification step 4's cases.
- Cases for the shapes Verification step 4 does not cover. The PR body names each as untested:
  - A blocked item beside true items in one dispatch.
  - A rule-scope statement with no decision marker whose count a source contradicts.
  - A task that adds a test (E5).
  - A non-subject doc stating a value when the claim's subject is unreadable, the case where the subject-only sentence decides.
  - A claim only running or calling could settle.
  - Where the writer searches for a claim's source: the body sets no bound.
    - The final-body Unverifiable run searched only inside the worktree.
    - An earlier Unverifiable run, on the body before row 39 (`runs/final-pre-r39/unverifiable.stream.jsonl`), grepped the scratch repo's main checkout recursively after two empty searches inside the worktree, nested worktrees included, though its prompt said to work only inside the worktree.
    - That checkout held identical copies of the worktree files, and its one nested worktree was the dispatch's own. So that run does not show what a read outside the worktree does when the copies differ, which could produce:
      - a correction citing the main checkout's or a sibling worktree's stale copy;
      - a spurious block from disagreeing copies;
      - a just-changed claim read against its pre-change copy.
    - That run's return also named search terms wider than the main-checkout grep ran (`ingest|drain` only), and the grade did not compare the two.
  - Dictated text written first and corrected at self-review: every run read the source before writing, so the 11 runs cover only the decide-before-write order.
  - The no-run sentence over a claim about a build target, CLI, hook, test or service. No-value dictates a build-target claim, and its one final-body run and one control run made no `make` call.
  - Universal claims phrased with "all" or "none"; only "every" ran.
  - A behavior claim over a script that takes more than a one-line read (row 35).
  - Any claim the writer composes itself: every case dictates its claim.
    - That includes a reworded dictated claim, which no case produces, and a correction's own text.
    - The no-run sentence governs neither. The Charter allows running only read-only checks on what the writer changed (`code-writer.md:25-26`), and within that grant step 6 sets no bar on checking such text by running (rows 38, 42).
    - The read that composes a correction is its only check: no run re-read a correction against its source, so a misread lands as written.
    - Script-false's one final-body run composed a correction and only read files, which does not show what the writer does without a no-run sentence.
  - A true claim over a source whose count needs interpretation, such as blank, comment or header lines. This is the main regression risk, a wrong correction; Supported's source is a plain four-line file. One final-body run misread a source anyway: script-true's return invented a six-space tail on `entries.txt`, and its claim was true, so the outcome held.
  - A true count over a source longer than one read window or `head` limit. Every fixture source is 1 to 5 lines, so no run's head-limited read could truncate it.
  - Dictated text carrying a step-6 flag other than the claim flag: a timeout or retry literal, a lint suppression, a new dependency or a history-narrating comment. Step 7 fixes every issue in the writer's diff, dictated text included, but E4's report sub-bullets speak only of claims, so a changed dictated value may surface only as a generic self-review fix. Every case dictates prose into a README.
  - A claim a command answers by evaluating tree code to report a count or route, such as test collection or a route lister (row 36).
  - A run of the final body without the no-run sentence, so whether the sentence changes behavior is unmeasured (row 33).
  - Dictation longer than a short paragraph quoted into an empty README: multi-item briefs, and dictated claims inside code.
    - The longest prompts ran were a two-sentence paragraph, prose naming a contradiction, and a decision sentence before the quoted text.
    - Every prompt introduced its dictation with "exactly as quoted".
    - So three things are unmeasured:
      - detecting a claim stated in unquoted prose or embedded in a longer brief;
      - the cost of checking many claims in one dispatch;
      - the chance of a wrong correction or spurious block across many true claims.
  - A claim about code the writer just changed, where the Charter permits running the touched test file and the no-run sentence forbids running what the claim describes.
  - Dictated text describing behavior the dispatch asks the writer to implement (row 39).
    - No run exercises row 39 in either shape.
    - The plain shape: the dictated text states the new behavior and the writer's current code does the old. What is unmeasured there is which side moves.
    - The mixed brief: the dispatch changes one aspect of a function and dictates a docstring that also misstates an aspect it does not change. What is unmeasured there is whether the writer implements the changed aspect as dictated and still corrects the misstated aspect.
  - The writer running a changed script or client to confirm dictated behavior it was asked to implement, which the no-run sentence no longer reaches (row 44).
  - Whether the parent can see behavior-asked text the writer altered or left disagreeing with the code (row 45).
  - An ADDRESS-row-shaped brief: a reviewer finding plus a fix instruction that embeds a count or route and edits an existing file.
    - The claim is unquoted there.
    - The claim-versus-decision call is unclear, because a fix instruction is often both.
