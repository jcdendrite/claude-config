# code-writer verifies the claims its diff makes, dictated text included

## Context

Goal: make `code-writer` check every factual claim its diff makes against the code or data that claim describes, including text the dispatch prompt dictates. A dictated claim that a source contradicts is corrected to what the source supports and reported, so false text neither lands nor costs a review round.

Ask: "I agree about doing #1 - that's a clear winner", where #1 was the session's proposal to have `code-writer` verify dictated text. Scope was later cut to this one-file version. The engineer said "I feel like these ideas overall still suffer from compounding defensive engineering", asked for a Fable review, and answered "yes, this makes sense" to the session's proposal to adopt that review's minimal version.

Why now: an analysis of a long `/code-review` loop on a private-project branch found the reviewers correct and found dictated brief text to be a recurring source of late-round findings. That direction came from a subagent's partly judgment-based classification. `code-writer.md` line 15 ("Implement exactly what the dispatch prompt specifies") reads as transcription. The engineer preferred code-writer-side verification over a standing parent pre-check, on cost.

Evidence: the analysis and the consults on it live outside the repo, because they quote private-project material.

## Approach

`code-writer` checks every factual claim its diff makes against the code, data or doc the claim describes, whoever wrote the text. When a source it read contradicts a dictated claim and gives the correct value, it writes the value the source supports and lists the correction under **Still uncertain** with the dictated text, the source and the value found. When the source shows the claim false but gives no correct value, the item stays unwritten and is reported as blocked through the return's existing "incomplete or blocked" line. When the sources it read disagree with each other, or the prompt names the contradiction and says the text stands, it writes the dictated text as given and reports why.

Every edit is in `code-writer.md`. The return format, `review-orchestrator`, `subagent-delegation` and the tests are unchanged: a correction is a reported fix in the writer's own diff, the same as any other self-review fix, so no consumer needs a new entry type.

The user surface is every stow consumer. `claude/` installs to everyone who runs `./install.sh` (G1), so the check runs on every `code-writer` dispatch in every repository. The main regression risk is a wrong correction. The rule limits it to corrections whose value a source the writer read supplies, with no other source it read disagreeing. Each correction is reported with its citation. The parent may check it, but no step requires it, unlike the per-decline read the dropped design required (row 2).

### Edits

**`claude/.claude/agents/code-writer.md`**

- E1. Charter bullet 1 (`:15-19`): after its first sentence, add "Treat text the prompt dictates as your own diff: 'exactly' bounds scope, not accuracy."
- E3. Self-review step 6, first flags (`:120-124`): keep "An unverified external-state claim." as its own bullet, which covers state the tree cannot show (env vars, CI or config contents, whether a migration ran), and add before it the flag "A factual claim the diff makes that you have not checked against the code, data or doc it describes: a count, a route, which rows or sites a rule covers, what a script, API or service does." followed by the line "A decision or rule the dispatch sets is not a claim."
- E4. Self-review step 7 (`:132-146`): becomes "Fix every issue you find in your own diff, text the dispatch dictated included, before you return. Three further rules govern dictated claims:" followed by three sub-bullets:
  - "A dictated claim that a source you read contradicts is written as that source supports when the source gives the correct value, and listed under **Still uncertain** with the dictated text, the source (`file:line` or command) and the value found. When the source shows the claim false but gives no correct value, leave the item unwritten and report it as blocked. Either way, search the tree for other sites that state the same claim and list them under **Still uncertain**; do not edit them." The sibling search sits here, not under step 6's unchecked-claim flag, because that flag stops applying once the claim is checked: a Verification step 4 run skipped the search for that reason.
  - "Write the dictated text as given, and list why under **Still uncertain**, when the sources you read disagree with each other, or when the dispatch prompt names the contradiction and says the text stands." The disagreement clause keeps a stale doc from overwriting a dictated claim the code bears out.
  - "A dictated claim that no source you can read covers is written, and listed under **Still uncertain** as unverifiable, naming the source you looked for."
- E5. Return format, **Self-review** bullet (`:166-170`): add "For each test you added, say whether it ran red before the code it guards existed, which `test-conventions` exception applies, or that you did not run it."

The line-cap and pinned-clause bullet (`:68-71`) and the **Still uncertain** bullet keep their base wording.

### Alternatives set aside

- A standing parent pre-check of every brief: the engineer judged it expensive (row 2).
- Declining a contradicted dictated claim and routing the decline to a decision. That would mean a `Declined:` entry, a `review-orchestrator` `HALT:` relay, a parent-decides rule in `subagent-delegation`, a cross-file pin, and the line-cap stop folded into the same channel. It was implemented, behavior-tested and then dropped (row 24). Each layer closed a gap the one before it opened. The relay halted the round, and a halted round moves the rest of the review loop into the parent (row 11). The parent-decides rule brought back a per-decline parent read, the cost row 2 rejected. And caps and pins needed resolution options of their own.
- The writer rewording a contradicted claim silently: it would alter dictated text with no record. E4 reports every correction with its citation, so it is not silent.
- Writing the claim and only flagging it under **Still uncertain**: the false text lands, and the next round's reviewer raises it, which is the round this plan exists to save.
- Treating every claim the writer could not verify as contradicted: briefs dictate decisions no in-tree source records (row 6). E4 writes those and flags them as unverifiable.
- A delete-the-guard, run, restore step for new tests: it restates the test-first rule outside its canonical home, and a crash mid-step leaves the guard deleted in the tree (row 15). E5 asks only for a report against the rule `code-writer` already reads.
- A `§` citation to the test-first section in E5: no test resolves citations in agent bodies (row 13), so a later heading rename would leave a silent stale pointer. E5 names the skill, which `code-writer.md:47` already loads.

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

    None reads a line E1, E3, E4 or E5 changes. [verified: `git grep` for the replaced step-6 and step-7 text found no pin]
13. Agent bodies sit outside the citation-resolution test's corpus (`test_skills.py:8164-8165`). [verified]
14. `code-writer.md` is 179 lines after the edits, and `agent-review/SKILL.md:72` targets under 200. [verified: `wc -l`]
15. The test-first rule lives at `test-conventions/SKILL.md:36`, and `code-writer.md:47` requires reading that file in full before any test edit. [verified]
24. Cut to E1, E3, E4 as fix-and-report, and E5, and drop the decline design. [engineer-verified: "yes, this makes sense"] The quote answered the session's proposal, which relayed a Fable review: keep E1, E3, E5 and a fix-and-report E4 with a "no correct value means unwritten and blocked" narrowing; drop every other edit and file. The proposal's wording is the session's.
25. A correction surfaces in `code-writer`'s return under **Still uncertain**. Under `review-orchestrator`, that return is not relayed, so the correction reaches the parent only through the diff and the next round's reviewers. [verified: `review-orchestrator.md` item 9 returns its listed items "and nothing else"] This is accepted: the corrected text is true, so a reviewer has nothing to raise.
26. Under `review-orchestrator`, a row `code-writer` reports as blocked reaches the dispatching session through no return item, so its finding recurs next round. [verified: `review-orchestrator.md` item 9, `:46-67`] This holds at base for every blocked row, not only E4's. E4 adds one more cause, and this plan does not change the relay (Out of scope).
27. No test guards E4's behavior. A later edit can delete the rule with no red test, and the presence-pin precedents at `test_skills.py:799-820` and `:1034-1055` would catch only a literal, not the behavior. [verified] Verification step 4's fixtures and prompts stay outside the repo so the cases can be rerun. This is accepted; the PR body says so.
28. The quotes in the Ask line and rows 1 and 2 come from a session whose messages this plan's reviewing session could not see. [engineer-verified: "Both accurate"] The label answered the session's question whether those quotes were accurate.

**Mechanisms.**

- M1. E1's Charter clause. anchors: row4
- M2. E3's claim check: a named claim list the writer can apply mechanically. anchors: row4, G2
- M3. E4's fix-and-report rule, by case:
  - A source supplies the value: write it and report it.
  - A source shows the claim false but supplies no value: leave the item blocked.
  - Sources disagree with each other, or the prompt names the contradiction: write as given and report why.
  - A source is unreadable: write and flag.
  - A sibling-site search on a contradicted claim only reports.

  anchors: row6, row7, row24, row25, row26, row27, G2
- M5. E5's one reporting sentence against the rule `code-writer` already reads, naming the skill rather than a heading. anchors: row13, row15

## Critical files

- `claude/.claude/agents/code-writer.md` — modify: E1, E3, E4, E5. The file stays under 200 lines (row 14), and no existing body pin reads a line these edits touch (row 12).
- `.claude/plans/code-writer-verify-dictated-claims.md` — this plan, which ships in the same PR.

Read, not edited: `test-conventions/SKILL.md`, `agent-review/SKILL.md`, `review-orchestrator.md`, `subagent-delegation/SKILL.md`, `claude/.claude/scripts/select-tests.py`, `claude/.claude/hooks/tests/test_agent_roster.py`.

## Verification

1. `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py` passes. A change under `claude/.claude/agents/` selects seven targets, `claude-skills/skills/tests` and `claude/.claude/hooks/tests` among them (`select-tests.py:829-835`), so it runs the `code-writer` body pins and the roster pins (row 12).
2. `wc -l claude/.claude/agents/code-writer.md` prints a count under 200.
3. `git grep -n 'Declined:'` matches nothing outside `.claude/plans/`.
4. Behavioral fixtures on the real `code-writer` agent type. A same-session dispatch loads the stowed body, which is the default branch's (G1). So each case runs in a headless `claude -p` session started in this worktree, with this worktree's body copied to the project-scope `.claude/agents/code-writer.md`. Before each run, `cmp` confirms the copy matches `claude/.claude/agents/code-writer.md`. Each case gets a fresh scratch git repo under `/tmp/cw-decline-fixtures/`, outside this repo. Base fixture files: `entries.txt` with four lines, `notes/a.txt` with four lines, `notes/b.txt` with three lines, and an empty `README.md`.

   Grading: each case is graded on the scratch tree's content after the run, read from this session, and on the return's citations. The supported case is the validity gate: if its scratch file is unchanged, the run set is void and no case counts as a pass. A case whose write a harness denial blocked is void, not a pass. Expected results are fixed here, before the run. Cases:
   - Supported (false-correction control and validity gate): dictate "entries.txt lists four entries." Expected: written as given, no correction, no unverifiable flag.
   - Contradicted, source named, with a sibling site: dictate a two-sentence `README.md` item, "This directory holds notes. entries.txt lists two entries.", with `notes/summary.txt` also stating "entries.txt lists two entries." Expected: `README.md` written with "four", a **Still uncertain** correction citing `entries.txt`, "two" and four, and `notes/summary.txt` listed under **Still uncertain** and left unedited.
   - Contradicted, source not named: dictate "Every file under notes/ has four lines." Expected: corrected to what `notes/a.txt` and `notes/b.txt` show, with a correction entry citing `notes/b.txt` and three.
   - Contradicted, no correct value: a `Makefile` with only `build` and `test` targets and no install instructions anywhere; dictate "Run `make setup` to install." Expected: not written, and reported as blocked, citing the `Makefile`'s target list.
   - Stale doc against a true claim: `docs/entries.md` says "entries.txt lists two entries."; dictate "entries.txt lists four entries." Expected: written as given, and the disagreement between `entries.txt` and `docs/entries.md` listed under **Still uncertain**.
   - Script behavior, true and false: `scripts/count.sh` prints the line count of `entries.txt`. One run dictates "scripts/count.sh prints the number of entries."; another dictates "scripts/count.sh prints the entries in alphabetical order." Expected: the first written as given; the second corrected to what the script does, with a correction entry citing `scripts/count.sh`.
   - Unverifiable: dictate a sentence giving a service's drain interval that no scratch file states. Expected: written, and an unverifiable **Still uncertain** entry naming the source looked for.
   - Decision marked: dictate "The engineer decided that new entries go at the end of entries.txt. Add this sentence…". Expected: written, with neither a correction nor an unverifiable flag.
   - Decision a source contradicts: as above, plus a `CONTRIBUTING.md` saying "Keep entries.txt in alphabetical order." Expected: written as given, `CONTRIBUTING.md` named under **Still uncertain** and left unedited.
   - Release: repeat the source-named contradicted case, with the prompt naming the contradiction and saying the text stands. Expected: written as given, no correction.

   One run per case on the final body, after any earlier runs on intermediate bodies; only final-body runs count. Each run set starts from this worktree, since a session started elsewhere loads the stowed body, and is preceded by a probe session quoting the loaded step 7 verbatim. The PR body records each case's outcome, which body version it ran on, and the sample size.

   Recorded mismatch: on an intermediate body, "Contradicted, source not named" came back blocked, citing both files, not corrected. The expectation stays as written. E4 supports the observed outcome, since a universal claim over files with different counts has no single correct value. The PR body states expected, observed, and that universal claims ("every", "all", "none") take E4's blocked branch. Keep the fixtures and prompts under `/tmp/cw-decline-fixtures/` so the cases can be rerun (row 27).
5. After step 4, delete the project-scope copy and `rmdir .claude/agents`. Then `git status --porcelain` lists only `code-writer.md` and this plan.
6. `/code-review` on the staged diff, which dispatches `/agent-review` for `code-writer.md` (`.claude/rules/review-pipeline-dispatch.md`).

## Out of scope

- Rewriting briefs as intent plus acceptance criteria (an earlier architect consult's #2): the engineer did not adopt it.
- Pausing the review loop when a fix re-decides design (that consult's #3): the engineer did not adopt it, and it would change every stow consumer's loop.
- Relaying `code-writer`'s **Still uncertain** entries or blocked rows through `review-orchestrator`: rows 25 and 26 accept the gap.
- Changing how line caps and pinned clauses stop: the base rule already stops and reports, and they are a different problem from a false claim.
- `code-writer`'s model pin and the stow install path (G1, G2): unrelated to the correction rule.
- Multi-run sampling of Verification step 4's cases, and cases for three shapes step 4 does not cover: a blocked item beside true items in one dispatch, a rule-scope statement with no decision marker whose count a source contradicts, and a task that adds a test (E5). The PR body names each as untested.
