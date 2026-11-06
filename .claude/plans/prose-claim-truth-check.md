# Check that claims in comments and durable docs are true

## Context

Goal: give `/code-review` a lens that asks whether a factual claim in a comment or durable doc is true, for both a changed claim and an unchanged claim that a code change makes false.

Ask: GH-1397, "How should the toolkit check that claims in comments and durable docs are true?" Engineer-selected labels on the design questions: "Angle + tripwire (Recommended)" for where the duty lives, and "Defer to follow-up (Recommended)" for the mechanical reference checker.

Why now: no `/code-review` tripwire, checklist item or reviewer angle applies the "Quantitative or causal claims in prose" rule (`claude/.claude/CLAUDE.md` § Engineering Judgment) to comments or docs. `comment-discipline-reviewer`'s nine angles judge how prose is written or where it lives, and none judges whether it is true. That reviewer also reviews only added or modified prose.

Measurement (this repository's own PR history only, one account, no ledger or transcript data): of the 150 most recent merged PRs (2026-10-02 to 2026-11-05), about 33 contain a post-first-commit fix whose headline says it corrects a false, stale or overstated prose claim. About 50 of the 819 post-first-commit fix commits do so. Rough split by class: false ~28, overstated ~11, stale ~10; by surface: durable docs and plan files ~25, code comments and docstrings ~20, skill or agent bodies ~4, instruction files ~2. This is a lower bound classified from commit headlines without reading diffs. The per-class splits are low confidence (about ±30% relative). It does not support a "no new mechanism" outcome.

Intended outcome: one reviewer angle for changed claims, one inline tripwire for the stale-claim arm, with the unverifiable-claim reporting rule and the deferral behaviour stated.

## Approach

Add one `comment-discipline-reviewer` angle, `False or unsourced claim`, that checks each settleable claim in added or modified prose against the thing it describes and flags an unsourced number or causal claim. Add one `/code-review` Step 1.5 tripwire, `Stale claim in unchanged prose`, that searches for unchanged prose still describing a name whose contract the diff changed. The angle rides the reviewer's existing deferral to `ready-for-review`'s cumulative pass, and the tripwire runs inline in every round. The change adds no agent, hook, test, fixture or numbered checklist item.

### The issue's questions

- **Measurement.** The Context's baseline justifies a new mechanism. Its stale class justifies a separate stale arm (row 22). It measures no catch rate. Verification's fixture cells show only that the wording works on synthetic cases (row 21). The outcome test is re-running the Context's headline count over PRs merged after this lands (Out of scope).
- **What counts as a claim.** A claim is a declarative statement of fact in added or modified prose. These are not claims, and neither check judges them:
  - rules and instructions, a number inside one included ("retry at most 3 times");
  - design judgments and rationale;
  - a record's account of past events;
  - quoted text.

  A claim is settleable when reading the repository can confirm or refute it. Three kinds are settleable:
  - what a named file, symbol, flag, config key, heading, script, hook or test is, contains or does;
  - a count or other number about the repository;
  - a cause and effect in the code.

  A settleable claim names its subject, and that subject is the source CLAUDE.md's bullet asks for (row 17). Every other claim is unsettleable, whatever it is about: runtime, history, external services or a third-party tool. No claim is excluded for being about state outside the repository. "When a claim cannot be verified" below sets the rule for unsettleable claims.
- **The line between this angle and Prose restating the code.** The test is whether the passage would be deleted even if it were true.
  - Prose restating the code owns a passage that should not exist even when true: a comment or docstring repeating what the code beside it shows, or a doc passage on how the code works (row 26). A false one takes that angle's deletion fix, not a correction (row 12).
  - The new angle owns a claim that stays when true: a feature doc's account of what the code does for its user or how to use it, a count, a claim about a file other than the one the prose sits with, or a cause and effect across files.
  - The angle's body states this test. Verification measures it in both directions: the precedence cell for a restating comment, and the usage-doc cells' deletion bar for a feature doc's claim.
- **Where the duty lives.** An angle plus a tripwire (row 1). Changed claims go to `comment-discipline-reviewer`, which already walks every added or modified prose site (M1). Stale claims go to Step 1.5 (M3), because the reviewer cannot see unchanged prose (row 6) and the orchestrator already holds the changed code (row 7).
- **How much the checks read, and the deferral.**
  - The angle's reads:
    - It reads each settleable claim's subject at the diff's state: the file, symbol or heading the claim names.
    - It uses `Grep` when the claim names a subject without its path or quantifies over a set.
    - It never traces further and never runs anything.
    - It reads nothing for an unsettleable claim, because that claim's finding rests on the missing source.
  - The angle shares Step 0.6's deferral automatically, because Step 0.6 defers the reviewer's whole row (row 23).
  - The tripwire reads the changed files, which the review already reads in full, plus one literal-token search per name whose contract the diff changes. A diff that changes no such name searches nothing, so a prose-only fix round pays nothing.
  - A tripwire hit in a record is no finding (rows 32-34). The records are the classes Step 1.5's "Preserved-record edits" names, plans under `.claude/plans/`, and dated design-decision records. A record describes a past state, and its body stays read-only.
  - A tripwire hit counts only where the prose describes the changed name, so a common-word token's ordinary uses draw nothing.
  - The tripwire does not share the deferral. Step 1.5 has no per-context switch, and a stale claim first caught in the cumulative pass costs another cumulative round (row 9).
- **When a claim cannot be verified.**
  - The reviewer never reports a claim false without citing what contradicts it: the subject's `file:line`, or for a count or quantified claim, the path and the hits.
  - An unsettleable claim is a finding only when it is a number or cause-and-effect claim naming no source. That breaks CLAUDE.md's "Quantitative or causal claims in prose" bullet on its face, so the reviewer needs no access to the subject (row 17).
  - That finding's fix names the source, marks the claim unverified, or deletes it.
  - Any other unsettleable claim draws no finding, following Restated canonical rule's fail-open (row 10).

  So an unsourced number about state outside the repository is a finding, and an unsourced claim about that state that is neither a number nor a cause and effect is not. Step 1.5's "Unverified external-state claim" tripwire overlaps the first case inline for the state it names (row 5), and both ask for the same fix. A "could not verify" finding would have no edit to ADDRESS and no DEFER criterion that fits, and it would recur at every cumulative pass (row 11).
- **Whether fixes cause new false claims.** The headline-only measurement cannot tell (row 14), so the design guards the fix path either way:
  - `/code-review`'s Output format item 5 already bars a prose fix from adding a fact the finding does not require.
  - The angle's fix takes its value from the subject the reviewer read.
  - `code-writer`, which carries ADDRESS fixes, self-reviews against every angle in the reviewer file and re-checks dictated claim text against its source (row 13).
  - The tripwire runs in every fix round that changes code.

### Assumption ledger

**Root problem.** A false claim in an added or modified comment or durable doc, or an unchanged one that a code change makes false, reaches merge unless a reviewer happens to notice it. No `/code-review` tripwire, checklist item or `comment-discipline-reviewer` angle asks whether a repository-internal claim is true (row 5). The Context's measurement shows later fix commits correcting such claims often enough to need a mechanism.

**Givens.** None. Every condition the design holds fixed is an artifact of this repository and so within its reach: rows 23-25 record them, and Out of scope records the plan's choice to keep each. Row 19's by-name dispatch behaviour is not a given either, because Verification dispatches the reviewer by path and the design does not rest on it.

**Rows.**

1. Where the duty lives is an angle plus a tripwire. `[engineer-verified: "Angle + tripwire (Recommended)"]`
2. The mechanical reference checker is deferred to a follow-up. `[engineer-verified: "Defer to follow-up (Recommended)"]`
3. The option descriptions relayed with those labels were the session's proposal, not the engineer's words. `[unverified]` They described a tenth `comment-discipline-reviewer` angle for added or modified prose, and an always-on Step 1.5 item for a code change contradicting an adjacent unchanged comment or doc that names the touched symbol. This plan adopts both on its own reasoning (M1, M3), so neither detail carries override protection.
4. The issue's questions are the six the dispatch relayed. `[unverified]` The issue body was not read when this section was written. Before implementing, `gh issue view 1397` confirms none is missing.
5. No current angle, tripwire or checklist item asks whether a repository-internal claim in a comment or doc is true. `[verified: claude/.claude/agents/comment-discipline-reviewer.md § "Core review angles"; code-review/SKILL.md:75-78, :132-141]` Step 1.5's "Unverified external-state claim" tripwire covers state the author cannot observe, in the diff or its commit message. Its examples are environment variables, secrets, CI or config contents, applied migrations, authorship, and a file's presence on `main`.
6. The reviewer judges only sites that appear as added or modified lines in the diff it is handed, and an unchanged passage is out of its scope. `[verified: comment-discipline-reviewer.md:197-205]` An unchanged claim that a code change makes false is therefore outside every angle, whatever the angle says.
7. `/code-review` reads every changed file fully and holds `Grep` and `Bash`. `[verified: code-review/SKILL.md:4, :84]` The orchestrator already has the changed code and its in-file comments in context when it applies Step 1.5.
8. A defect outside the staged boundary that the change causes stays in scope, and a finding the change activates is not orthogonal under DEFER criterion 1. `[verified: code-review/SKILL.md:38, :395]` A stale claim the change creates is therefore an ADDRESS finding, not an item 14 note.
9. A fix that reopens `ready-for-review` step 3 re-spawns the deferred row against the new cumulative diff. `[verified: docs/design-decisions/comment-discipline-reviewer-deferred-to-cumulative-pass.md, second paragraph]` That a cumulative-pass finding costs more than the same finding in a staged round is an inference from that mechanism, not a measurement.
10. Restated canonical rule, the reviewer's other angle that reads outside the diff, fails open: "No home located, no finding". `[verified: comment-discipline-reviewer.md:176-177]`
11. Every reviewer-spawned finding is tagged ADDRESS or DEFER from a closed list, and a reviewer's "FYI" is not a disposition. `[verified: code-review/SKILL.md:344, :393-403]` A finding saying only "could not verify" has no edit to ADDRESS and fits no DEFER criterion. With the text unchanged, it would recur at every cumulative pass.
12. Prose restating the code is deleted, not corrected, and that angle takes precedence over the altitude angle. `[verified: docs/design-decisions/prose-restating-code-is-deleted.md; code-review/SKILL.md:246]` A false claim that restates the code takes deletion too, so the new angle defers to that angle.
13. `code-writer` carries ADDRESS fixes on the default route and self-reviews prose against `comment-discipline-reviewer`'s angles. It also flags unchecked factual claims and re-checks dictated claim text against its source. `[verified: claude/.claude/agents/code-writer.md:113-115, :125-155, :169; code-review/SKILL.md:348]` The new angle reaches every fix `code-writer` authors with no new dispatch.
14. The Context's measurement was classified from commit headlines without reading diffs. `[verified: this plan's Context]` It cannot tell whether a corrected claim came from an earlier fix commit, or which review step caught it.
15. Item 12a's bullet names must equal the agent's angle headers, in order, and a header's name cannot contain an em dash. `[verified: claude/.claude/hooks/tests/test_design_decision_files.py:549-631]`
16. `_SCOPE_CITED_ANGLE_NAMES` must change only when the Scope section names an angle, and only Paragraph readability's block is hash-pinned. `[verified: test_design_decision_files.py:634-705, :857-897; claude/.claude/hooks/tests/fixtures/paragraph-readability.json "angle" field]` An angle appended after Restated canonical rule, with no Scope edit, touches neither.
17. "Quantitative or causal claims in prose" requires a number or cause-and-effect claim to name its source, and its last sentence names ticket, PR-body and handoff prose as the surfaces carrying it. "Be precise" requires verifying claims against actual code. `[verified: claude/.claude/CLAUDE.md:45, :56]` Neither excludes comments or durable docs, so together they ground the angle's two arms without a CLAUDE.md edit.
18. The 12a Item-ownership co-owner cell reads "via Step 1.5's inline tripwire", in the singular, meaning "Non-durable comment". `[verified: code-review/SKILL.md:474]` A second prose tripwire makes that reference ambiguous.
19. Edits under `claude/.claude/**` go live only on `git pull` of the stowed checkout, so a by-name dispatch loads the pre-change agent body. `[verified: repo CLAUDE.md § "Working in this repo"]` Before merge, a by-name dispatch of `comment-discipline-reviewer` runs the old body, not this branch's.
20. How often a claim's subject lies inside the diff, already read in full, rather than outside it is not measured. `[unverified]` The angle's added reads per cumulative pass are unknown.
21. Neither check's precision nor recall is known. `[unverified]` Verification's K=5 fixture cells show that the wording works on synthetic cases, not a catch rate.
22. A prior plan rejected a Step 1.5 tripwire whose check had to locate a candidate across a multi-file corpus on every commit, for a rarely-firing class. `[verified: .claude/plans/design-decision-ssot-duplication-check.md, "A fifth Step 1.5 inline tripwire"]` This tripwire's search key is a literal name the diff itself changes, and it searches nothing when no such name changed. The Context puts its class at about ten of the roughly fifty commits, a low-confidence split.
23. Step 0.6 defers `comment-discipline-reviewer`'s Change-type row out of staged-diff commit-gate rounds, and its pass runs in `ready-for-review`'s cumulative review. `[verified: claude-skills/skills/code-review/SKILL.md:52]` It is in reach, because Step 0.6 is this repository's text and `docs/design-decisions/comment-discipline-reviewer-deferred-to-cumulative-pass.md` records why. The plan keeps it (Out of scope), because reversing it needs dispatch-cost evidence the plan does not have. Lifted, the angle would run in staged rounds too, and the two arms would no longer differ in timing. M1's case against an inline check on changed claims rests on this deferral and would lapse with it. The stale arm would still need the tripwire, because the reviewer's in-diff scope (row 6) does not depend on the deferral.
24. `comment-discipline-reviewer` holds `Read`, `Grep`, `Glob` and `Write`, with no `Bash` or network tool. `[verified: claude/.claude/agents/comment-discipline-reviewer.md:6]` It is in reach, because the `tools:` frontmatter is this repository's file and the closed-form tool design is recorded in `README.md`'s `comment-discipline-reviewer` paragraph and `docs/design-decisions/reviewer-persona-roster-operations.md`. The plan keeps it (Out of scope). Lifted, the angle could verify external-state claims and claims about third-party tool behaviour, which this plan leaves to Step 1.5's external-state tripwire or to a separate change.
25. `code-review/SKILL.md` is capped at 550 lines until #1351 restructures it, and it has 525 lines now. `[verified: claude/.claude/hooks/check-skill-length.sh:132-134; Read of the file ends at line 525]` This plan adds two lines. The cap is in reach, because `check-skill-length.sh` is this repository's hook, and #1351 owns the restructure. The plan keeps the cap (Out of scope). Lifted, the tripwire could carry more text than the one bullet this plan budgets.
26. CLAUDE.md's "Don't restate the code" bullet limits a comment or docstring to what is non-obvious from the code, and a feature doc to what the code does for its user and how to use it, not how it works. `[verified: claude/.claude/CLAUDE.md:116]` A comment repeating the line beside it is deleted even when true. That a usage doc's statement of a user-facing default stays when true is this plan's inference from the bullet, not its text.
27. The repository's precedent for a fixture-verified angle runs each cell at K=5 against the bars must-flag at least 4 of 5 and must-pass at most 1 of 5, and records the rates, date and model. `[verified: claude/.claude/hooks/tests/fixtures/paragraph-readability.json "note"; docs/design-decisions/paragraph-readability-review-angle.md § "Re-running the fixture's hash check after a rewording"]`
28. Assertion 9 covers Paragraph readability alone, through its own fixture path, a single-paragraph must-flag and must-pass pair, and that angle's three carve-out names. Its hash pin makes a reword fail CI but cannot force the re-measure. `[verified: claude/.claude/hooks/tests/test_design_decision_files.py:857-986; docs/design-decisions/paragraph-readability-review-angle.md:57-59]` A pin for this plan's cells would need a new fixture format, holding a scratch tree, a diff and per-site expectations, and a new test.
29. `test_description_length` caps an agent's `description` at 1000 characters, and `test_frontmatter_parses_strictly` fails on an unquoted value containing `: `. `[verified: claude/.claude/hooks/tests/test_agent_roster.py:92, :433-448, :571-579]` The reviewer's unquoted `description` is 861 characters. `[verified: a count of comment-discipline-reviewer.md:5]`
30. A dispatched subagent returns only its final text and reported usage, so its summary is the dispatching session's only direct view of what it did. `[verified: docs/precompact-hook-behavior.md:66]` Its own transcript is a `subagents/*.jsonl` file that a content-grep can locate. `[verified: docs/transcript-analysis.md:67]`
31. A subagent transcript records each tool call as a `"type":"tool_use"` entry carrying its `"name"`, and its first record holds the dispatch prompt. `[verified: claude/.claude/scripts/transcript_analysis/turn_shape.py:130-133, which parses each message's tool_use blocks and their name]` Verification step 4's positive control still checks each run's file before a zero-search reading counts.
32. Step 1.5's "Preserved-record edits" tripwire names already-applied migrations, changelog or incident records, and anchor fixtures. It names neither plan files nor design-decision records. CLAUDE.md's Axis 3 makes a record read-only, and its decision test asks whether text records something that happened or describes how the code currently behaves. `[verified: claude-skills/skills/code-review/SKILL.md:77; claude/.claude/CLAUDE.md:71-78]` A stale-claim hit in a record has no permitted fix, so it cannot be a finding.
33. A plan under `.claude/plans/` is provenance for the change it gates. `[verified: claude-skills/skills/plan-it/SKILL.md:17]` Once that change merges, the plan records what was planned, so the tripwire treats it as a record.
34. A design-decision record carries a dated provenance line. A decision that a later one overturns is edited in place to say so, in a dated note immediately under that line. `[verified: .claude/rules/design-decisions.md:16-32]` One record already uses such a note for a factual claim a later change made false. `[verified: docs/design-decisions/ready-for-review-fix-loop-convergence.md:3-5]` The tripwire treats a dated decision record as a record, because whether a change overturns a recorded decision is a design judgment a literal-token hit cannot make (Out of scope).
35. The reviewer's "How to work" step 2 walks every added or modified site against every angle. Its step 4 sweep searches the diff's file set for a found label's literal token and names every hit. `[verified: comment-discipline-reviewer.md:186-196, :206-214]` A false claim has no such token, so the sweep stays as it is. Swept by its wording, a found false claim would also pull in a restating comment that shares it, which the boundary assigns to Prose restating the code.
36. With eight must-flag bars at K=5 and an assumed 90% hit rate per cell, all eight clear together about half the time. `[verified: binomial arithmetic: at p = 0.9, P(at least 4 of 5) = 0.919, and 0.919^8 ≈ 0.51]` The 90% rate is an assumption from staff-sdet's review, not a measurement. The K=10 re-run for a one-run miss raises the joint chance to about 0.73 under the same assumption, which is staff-sdet's estimate.

**Mechanisms.**

- **M1 — `False or unsourced claim`, a new last angle in `comment-discipline-reviewer`.** `anchors: root, row5, row10, row11, row12, row17, row20, row23, row24, row26, row35`. The reviewer already walks every added or modified prose site against every angle, reads every changed file fully, and holds the read and search tools the check needs (row 24), so the angle costs no new dispatch. Its added reads per cumulative pass are not measured (row 20). The reviewer's token sweep stays unchanged, because step 2's walk already reaches every site and a false claim has no literal token (row 35). Its unsettleable-claim rule is row 10's fail-open narrowed by row 17: only an unsourced number or causal claim, a defect on its face wherever its subject lives, becomes a finding. Its line with Prose restating the code is whether the passage would be deleted even if true (row 26). Alternatives set aside:
  - **A new claim-accuracy reviewer agent.** Heavier: another spawn in every cumulative pass, roster and canary entries, and its own Change-type row, for a check the existing reviewer's reads already reach.
  - **A Step 1.5 inline check on changed claims.** Lighter in dispatches, but it would re-verify, every staged round, prose that fix rounds keep rewriting. That is the cost row 23's deferral exists to avoid.
  - **`code-writer`'s existing self-review bullet alone.** Prose the session writes inline never passes through `code-writer`. A self-review also shares the authoring context a fresh reviewer exists to escape, per `docs/design-decisions/reviewer-persona-roster-operations.md`'s `comment-discipline-reviewer` paragraph.
- **M2 — Widen the reviewer's charter to row 17's two bullets, and drop the angle count from its `description`.** `anchors: row17, row29`. The `description` and the opening paragraph each list the CLAUDE.md rules the angles apply, and both gain the two bullets. The framing sentence under "Core review angles" stops re-listing the rules and points to the opening paragraph, leaving two lists to keep in step instead of three. "the nine core review angles" becomes "the core review angles", because every new angle makes a count false. The `description` edit adds at most 100 characters net, which keeps the value under 970 of its 1000-character cap (row 29).
- **M3 — `Stale claim in unchanged prose`, a new Step 1.5 tripwire after "Non-durable comment".** `anchors: row6, row7, row8, row9, row22, row32, row33, row34`. Its trigger is visible on the diff surface: a renamed, removed or redefined name. Its search key is that name's literal token, and its search skips records (rows 32-34). Its finding is in scope as a defect the change causes (row 8), so it needs no disposition rule of its own. It is the plan's widest-scope mechanism, a repository search in every round that changes a contract, so lighter primitives were checked:
  - **Search only the changed files.** The review already holds them, but a doc describing a script, flag or config key sits in a different file from it by construction.
  - **Defer the tripwire to the cumulative pass.** That is one search per PR, but Step 1.5 has no per-context switch to defer with, and a stale claim first caught there costs another cumulative round (row 9).
  - **Widen M1 to unchanged prose.** This breaks the reviewer's in-diff scope rule (row 6), makes its exhaustive enumeration repository-wide, and asks a prose reviewer to judge which code contracts changed.
  - **The mechanical reference checker.** It is deferred (row 2), and it would catch a vanished name, not a changed behavior.
- **M4 — Item 12a gains the angle's index bullet, last, and its question names row 17's two bullets.** `anchors: row15, row16`. This is index maintenance the lockstep test enforces.
- **M5 — The 12a Item-ownership co-owner cell names the "Non-durable comment" tripwire.** `anchors: row18`. The edit changes wording only, not the cell's owner.
- **M6 — A design-decision record, `docs/design-decisions/false-or-unsourced-claim-checks.md`.** `anchors: root, row9, row11, row27, row28, row36`. Both earlier angles shipped with one: `restated-canonical-rule-review-angle.md` and `paragraph-readability-review-angle.md`. Without a record, only this plan explains why the stale arm runs inline while the angle defers, and why a could-not-verify claim draws no finding. With no committed fixture (row 28), the record is also where K, the bars, the K=10 re-run and each cell's measured rate live. It cites this plan's Verification as the cells' full text, and it says plainly that the re-run rule is honor-system.
- **M7 — A `CHANGELOG.md` `[Unreleased]` entry.** `anchors: root`. Every stow consumer's `/code-review` changes on `git pull`. The restating-the-code angle's entry is the precedent (`CHANGELOG.md:60-67`).

### Dispatch split

Use one `code-writer` dispatch for all four files. The angle header literal `False or unsourced claim` is shared state that the agent file, item 12a's bullet, the record and the changelog entry all consume, and `plan-it` Step 5 names that shape as the do-not-split case. The session runs Verification's fixture cells after `code-writer` returns, because they need subagent dispatches. It then writes the measured rates into the record, the one edit whose content only the session holds.

## Critical files

Paths are relative to the worktree root.

**Modify:**

- `claude/.claude/agents/comment-discipline-reviewer.md`
  - Frontmatter `description`, per M2: name §Engineering Judgment's "Quantitative or causal claims in prose" bullet and §Working Style's "Be precise" bullet beside the rules it already names. Replace "the nine core review angles" with "the core review angles". Add at most 100 characters net, and no `: ` (row 29).
  - Opening paragraph (lines 9-12): name the same two bullets.
  - Framing sentence under "Core review angles" (lines 61-64): point to the rules the opening paragraph names instead of re-listing them.
  - New angle, last, after Restated canonical rule, with the header literal exactly `**False or unsourced claim** — `. In about fifteen lines, its body states:
    - What a claim is and is not, and which claims are settleable, per Approach § "What counts as a claim", including that a settleable claim's subject is its source.
    - The false-claim predicate: an added or modified sentence states a settleable claim that its subject, read at the diff's state, contradicts or supports only in part. The finding cites the subject's `file:line`, or for a count or quantified claim, the path and the hits. Its fix is the statement the subject supports.
    - How to verify: read the subject the claim names, with `Grep` when the claim names no path or quantifies over a set ("every", "no", "only"). Only the subject can contradict the claim, never another doc restating it.
    - The unsourced-claim predicate: an unsettleable number or cause-and-effect claim naming no source, wherever its subject lives. Its fix names the source, marks the claim unverified, or deletes it. Any other unsettleable claim draws no finding.
    - The boundary, per Approach § "The line between this angle and Prose restating the code": a passage that would be deleted even if true is a Prose restating the code finding, with deletion as its fix, and draws none here. A feature doc's account of what the code does for its user or how to use it is not such a passage.
  - Leave "How to work" step 4's token sweep unchanged (row 35).
  - "Output format" violation-type list: add `False or unsourced claim`.
  - Leave the Scope section unchanged, and with it `_SCOPE_CITED_ANGLE_NAMES`. The angle applies to instruction files like the other angles, and not to a `plan_path:` review. `/agent-review` owns final length and voice.
- `claude-skills/skills/code-review/SKILL.md`, net two added lines, 527 against the 550 cap (row 25).
  - Step 1.5: one bullet after "Non-durable comment", `**Stale claim in unchanged prose**`, stating:
    - The trigger: the diff renames or removes a name other code or docs refer to (an exported function or class, a CLI flag, config key, environment variable, file path, script, hook or heading), or changes what one accepts, returns, prints, exits with, allows or defaults to.
    - The defect: a comment or durable-doc passage the diff leaves unchanged still states the old contract.
    - The search:
      - It reads the changed files' own comments.
      - It runs a `Grep` of the repository for each such name's literal token.
      - A hit counts only in a comment or doc whose prose describes that name.
      - A hit never counts in a record: a class "Preserved-record edits" names, a plan under `.claude/plans/`, or a dated design-decision record (rows 32-34).
      - A diff changing no such name searches nothing.
      - The bullet names design-decision records by class, not by this repository's `docs/design-decisions/` path, because the skill installs into every stow consumer's repositories.
    - The scope: a hit the change makes false is a defect the change causes (Step 0.6), not an item 14 pre-existing issue. Its fix takes the new value from the changed code, or deletes prose restating the code.
    - The timing: it always runs inline, including rounds where Step 0.6 defers `comment-discipline-reviewer`.
  - Item 12a: widen the question sentence to name the "Quantitative or causal claims in prose" and "Be precise" bullets. Add a last bullet, on one line: `False or unsourced claim — a claim its subject contradicts, or a number or causal claim naming no source`.
  - Item ownership, 12a row: replace "via Step 1.5's inline tripwire" with "via Step 1.5's inline "Non-durable comment" tripwire". Add no row.
- `CHANGELOG.md`: one `[Unreleased]` `### Changed` entry in the existing entries' shape. It has a bold one-sentence lead and one sub-bullet each for the angle (added or modified prose, cumulative pass), the tripwire (unchanged prose, every round) and the unverifiable-claim rule, and it ends "The change is live on `git pull`."

**Create:**

- `docs/design-decisions/false-or-unsourced-claim-checks.md`, per `.claude/rules/design-decisions.md`: an H1, then a provenance line dated the day it is written, with no `Formerly §N` clause. It cites, never restates, `claude/.claude/agents/comment-discipline-reviewer.md` § "Core review angles" and `claude-skills/skills/code-review/SKILL.md` § "Step 1.5 — Judgment-activation pass". It uses no label this plan defines (M1, row numbers). It records why the design has its shape and how its wording was measured, never the rules themselves:
  - Changed claims go to the reviewer and share its deferral, while stale claims run inline: the reviewer judges only added or modified lines, and a cumulative-pass finding reopens `ready-for-review`'s step 3.
  - A claim the reviewer cannot settle by reading draws a finding only when it is an unsourced number or causal claim, wherever its subject lives. The missing source is visible on the face of the text, and a "could not verify" finding has no fix and recurs.
  - The reviewer checks no external-state claim for truth, because it has no `Bash` or network tool. Step 1.5's existing tripwire asks inline for such a claim's evidence.
  - The reviewer stays one agent at `effort: high`, because the new angle's cross-reference is a precedence clause, like the restating angle's precedence over the altitude angle.
  - The maintainer deferred a mechanical reference checker to a follow-up.
  - The fixture evidence, which the session adds after Verification steps 3-4:
    - The rule each fixture cell guards, K=5, and the bars.
    - The K=10 re-run for a cell that misses by one run, with the note that it raises the chance all must-flag bars clear together (row 36).
    - Each cell's measured rate, date and model.
  - Where the cells live: the record cites `.claude/plans/prose-claim-truth-check.md` § "Verification" for the cells' text and the scratch trees' contents.
  - The guard's limit: no committed fixture or test guards the wording, so re-running the cells after any edit to the angle's block or the tripwire bullet is an honor-system rule.
  - The tripwire cells apply the bullet alone, outside a real `/code-review` run, so they do not measure it in the orchestrator's full context.
  - A Sources list names the two cited files, `comment-discipline-reviewer-deferred-to-cumulative-pass.md`, `prose-restating-code-is-deleted.md`, `paragraph-readability-review-angle.md` for the K and bars, and this plan.

No test or fixture file changes. `test_item_12a_index_matches_agent_angle_headers` already pins the new bullet to the new header. A committed regression fixture is set aside (row 28, Out of scope).

**Read, do not modify:** `claude/.claude/CLAUDE.md` (rows 17, 26), `claude/.claude/agents/code-writer.md` (row 13), `claude/.claude/hooks/tests/fixtures/paragraph-readability.json` (row 27), and `docs/design-decisions/paragraph-readability-review-angle.md`, a dated record.

## Verification

Run every command from the worktree root.

1. `wc -l claude-skills/skills/code-review/SKILL.md` prints at most 550, and 527 is expected. `check-skill-length.sh` also enforces the cap at commit.
2. `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py` must select and pass `claude/.claude/hooks/tests/test_design_decision_files.py` and `claude/.claude/hooks/tests/test_agent_roster.py`.
   - The first holds the 12a lockstep test, the Scope-tuple test, the Paragraph readability hash check, and the filename and provenance checks the new record must pass.
   - The second holds `TestAgentFrontmatter::test_description_length` and `TestAgentFrontmatter::test_frontmatter_parses_strictly`, which guard M2's `description` edit (row 29).
   - If `select-tests.py` does not select either, that is a bug in its rule table, per the repo CLAUDE.md.
   - No Python or shell file changes, so `ruff` and ShellCheck do not apply.
3. **Angle fixture cells**, per `.claude/rules/skill-and-agent-self-review.md`, kept in the session scratchpad and never in the repo (row 28).
   - Dispatch a `general-purpose` subagent with `model: sonnet`. Tell it to act as the reviewer the worktree's `claude/.claude/agents/comment-discipline-reviewer.md` defines, by absolute path, using only `Read`, `Grep`, `Glob` and `Write`. A by-name dispatch would run the pre-change body (row 19).
   - Build one synthetic scratch tree. `bin/fetch.sh` reads `LIMIT` with a default of 20 and exits with status 1 when `LIMIT` is 0. `hooks/` holds exactly four executable hook scripts and no helper file. `install.sh` and `bin/sync.sh` both call `fetch.sh`.
   - Write a must-flag diff and a must-pass diff over the tree. Each cell below names the rule it guards.
   - Run each diff's review K=5 times (row 27), and score each cell by its site:
     - A must-flag cell meets its bar at a `False or unsourced claim` finding on its site in at least 4 of 5 runs, carrying the evidence named below.
     - A must-pass cell meets its bar at such a finding in at most 1 of 5 runs.
     - Another angle firing on a site does not count against its cell, except in one case.
     - A Prose restating the code finding on a settleable claim in `docs/usage.md` counts against the cell, must-flag or must-pass. That is the boundary's second direction. The new angle owns a feature doc's claim, and one line drawing both findings gets opposite fixes.
   - Must flag, as lines the must-flag diff adds to `docs/usage.md`:
     - "`LIMIT` defaults to 10", citing `fetch.sh`'s default line. It guards the false-claim predicate on a named subject.
     - "`hooks/` holds three hooks", citing the four files. It guards a count about the repository.
     - "Only `install.sh` calls `fetch.sh`", citing `sync.sh`'s call, which only a search finds. It guards the `Grep` rule for a claim that names no path or quantifies over a set.
     - "A full fetch takes about 40 seconds", with a fix that names a source, marks the claim unverified or deletes it. It guards the unsourced-number arm.
     - "Running the fetch nightly prevents rate-limit errors", with the same fix shape. It guards the unsourced-causal arm.
   - Precedence, in the must-flag diff: the comment `# exits with status 2 when LIMIT is 0` directly above `fetch.sh`'s exit line. It shares no claim with a must-flag line, so it measures only the boundary's first direction. It meets its bar at a Prose restating the code finding with a deletion fix in at least 4 of 5 runs, and at a `False or unsourced claim` finding on that site in at most 1 of 5.
   - Must pass, as lines the must-pass diff adds to `docs/usage.md` unless stated:
     - "`LIMIT` defaults to 20". It guards that a true claim draws nothing.
     - "Setting `LIMIT` to 0 makes `fetch.sh` exit with status 1". It guards that a settleable cause and effect has its subject as its source, so the unsourced arm stays silent.
     - "A full fetch takes about 40 seconds", with its producing command named. It guards that a named source clears the unsourced arm.
     - The instruction "Retry a failed fetch at most 3 times". It guards that a rule is not a claim, even with a number in it.
     - "The CI runner caches `~/.cache` between jobs". It guards the fail-open for an unsettleable claim that is neither a number nor a cause and effect.
     - "The failing run used `LIMIT=10`", in a dated incident record. It guards that a record's account of past events is not a claim, even with a number in it.
     - A block quote of a user's report that "`fetch.sh` ignores `LIMIT`". It guards that quoted text is not a claim, even when false.
   - A cell that misses its bar by one run, at 3 of 5 on a must-flag bar or 2 of 5 on a must-pass bar, gets five more runs of its diff (row 36). It is then scored over all ten runs, against at least 8 or at most 2. The other cells' extra runs are recorded but not scored. Only a cell that still misses, or that missed by more than one run, has missed its bar.
   - When a cell misses its bar, a `code-writer` re-dispatch revises the angle's wording, and both diffs re-run at K=5. A bar still missed after one revision goes to the engineer with the measured rates, before commit.
4. **Tripwire fixture cells**, under step 3's scratchpad rule, at K=5. Dispatch a `general-purpose` subagent with `model: sonnet` to apply only the worktree `code-review/SKILL.md`'s `Stale claim in unchanged prose` bullet, by absolute path, to a scratch tree and diff. Tell it to use only `Read`, `Grep`, `Glob` and `Write`, and give each dispatch prompt a unique run token.
   - The contract diff runs over a tree where `bin/fetch.sh` takes `--max` and `config.toml` sets `limit = 20`. It renames `--max` to `--count` and sets `limit` to 50. These unchanged lines are its cells, each naming the rule it guards:
     - Must flag: a `docs/usage.md` line naming `--max`. It guards the vanished-name case.
     - Must flag: a `README.md` line saying the `limit` key defaults to 20. It guards the redefined-contract case the deferred checker would miss.
     - Must pass, one cell over three sites that each say the `limit` key defaults to 20: an earlier `CHANGELOG.md` entry, a plan under `.claude/plans/`, and a `docs/design-decisions/` record with a dated provenance line. A run counts against the cell when any of the three draws a finding. It guards the record skip for each class the bullet names (rows 32-34).
     - Must pass: a `docs/notes.md` line using "limit" in its ordinary sense, "the API's rate limit". It guards that a hit counts only where the prose describes the changed name.
     - Must pass: a `README.md` line saying `limit` caps how many items one fetch returns, still true after the change. It guards that a hit counts only where the prose states the old contract.
   - A must-flag cell meets its bar at a finding that names the doc line and treats it as caused by the change, not as an item 14 note, in at least 4 of 5 runs. A must-pass cell meets its bar at a finding in at most 1 of 5.
   - The no-contract diff rewrites a function's body without changing its flags, output or defaults, and rewords one `docs/usage.md` line. It carries two must-pass bars:
     - It draws a finding in at most 1 of 5 runs, which guards the trigger.
     - It searches in at most 1 of 5 runs, which guards the bullet's "A diff changing no such name searches nothing".
   - Read each no-contract run's searches from its transcript with Bash, not from its summary (row 30):
     - Find the run's `subagents/*.jsonl` file with `grep -rl '<run token>'` over the active account's config dir, spelled out as a literal path because the repo CLAUDE.md bars `$CLAUDE_CONFIG_DIR` in Bash, and over each root `~/.claude/transcript-config-dirs` declares (`docs/transcript-analysis.md` § "Corpus scope: the declared-roots file"). `-l` prints file names only. Keep only a match under a `subagents/` directory, because the run token also appears in the parent session's transcript. Read each count as zero or nonzero only, because a tool call can appear twice in a transcript.
     - Count every `Grep`, `Glob` or `Bash` call in that file, whatever its path, with `grep -oE '"name":"(Grep|Glob|Bash)"' <file> | wc -l`. A nonzero count is a search.
     - As a positive control, `grep -c '"name":"Read"' <file>` must print a nonzero count (row 31). Without it, report the zero-search bar as unmeasured, not met.
   - Step 3's two re-run rules apply, with the tripwire bullet as the wording a miss revises.
5. **Record the rates.** Write each cell's measured rate and K, the date and the model into the record (M6), and name any bar that ships unmet.
6. **Pipeline**, per `.claude/rules/review-pipeline-dispatch.md`: `/skill-review` for `code-review/SKILL.md`, hook-enforced at commit, and `/agent-review` for `comment-discipline-reviewer.md`. The record and the changelog entry reach `comment-discipline-reviewer` through its Change-type row in the cumulative pass.
7. **`/code-review`'s "Reshapes reviewer ownership" row** fires on the agent file's new angle. Read it by its `agents/*.md` limb: spawn the edited persona plus its 12a co-owners, whose cell is a judgment cell naming no persona. State that reading on the `Spawn decisions:` line. M5's wording edit is not a substantive routing-table change.
8. **Dogfood**, after step 5: dispatch step 3's by-path reviewer once over the branch's final diff. A by-name dispatch, the cumulative pass's included, would run the pre-change body (row 19). Address every `False or unsourced claim` finding it raises on the record or the changelog entry before commit.

## Out of scope

- **A mechanical checker for file, flag and symbol references in prose.** It is deferred to a follow-up. `[engineer-verified: "Defer to follow-up (Recommended)"]`
- **Claims in plan files.** Three reasons keep them out:
  - A plan inside a diff is outside the reviewer's scope.
  - A `plan_path:` review applies only three angles.
  - A plan's claims carry the assumption ledger's tags.

  The Context does not split plan files from durable docs, so this leaves an unmeasured share of the problem unaddressed.
- **Claims in commit messages and PR bodies.** `pr-description`'s content-claim and uncited-number checks cover PR bodies, and Step 1.5's external-state tripwire already reads commit messages.
- **Checking whether a durable-prose claim about third-party tool or vendor behavior is true.** The angle flags such a claim only when it is a number or cause and effect naming no source. Checking its truth needs a network tool the reviewer lacks (row 24). Step 1.5's external-state tripwire does not name such claims either. Covering them is a separate change.
- **A committed, hash-pinned regression fixture for the angle or the tripwire.** It needs a new fixture format and test rather than an extension of assertion 9 (row 28). Its pin would trigger a re-measure without forcing one. The record states the honor-system re-run rule instead (M6).
- **Editing `claude/.claude/CLAUDE.md`'s "Quantitative or causal claims in prose" bullet** so its last sentence names comments and durable docs. That sentence lists authoring-time claim-verification steps per surface, while the angle is a review check, and the file loads for every agent of every stow consumer (row 17).
- **A stale-claim bullet in `code-writer`'s self-review.** The tripwire runs in the `/code-review` that follows every `code-writer` return. For a dictated claim, `code-writer`'s step 7 already searches for other sites and reports them without editing.
- **Reversing Step 0.6's deferral for the new angle (row 23), changing the reviewer's tools (row 24), or splitting the reviewer or changing its `effort:`.** `EXPECTED_EFFORT` in `claude/.claude/hooks/tests/test_agent_roster.py` stays as it is.
- **Raising `code-review/SKILL.md`'s 550-line cap or restructuring the file (row 25).** #1351 owns the restructure, and the two lines this plan adds fit under the cap.
- **Re-measuring after merge.** Re-running the Context's headline classification over PRs merged after this lands is the outcome test, and it cannot run before merge.
- **Prompting a supersession note in a dated design-decision record that a change makes stale.** The tripwire skips such records (row 34). Whether a change overturns a recorded decision is a design judgment a literal-token hit cannot make. The note stays the duty of the change that supersedes the decision, per `.claude/rules/design-decisions.md`.
