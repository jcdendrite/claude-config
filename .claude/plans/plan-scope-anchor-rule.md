# Plan: anchor plan and diff scope to the engineer's ask

## Context

Goal: stop a plan, and the diff that implements it, from growing past what the engineer asked for, by giving every scope gate the engineer's own words and approved file list as its yardstick.

Ask: "Keep each plan, and the diff that implements it, to what I asked for and the file list I approved. Anything past that is my call — ask me instead of deciding it." — the engineer selected "Accept as written" for this wording, which `plan-architect` drafted and tightened from the engineer's earlier sentence "Stop plans and their PRs from growing far past what I asked for."

Why now: a merged-history study of this repo found one branch (#1035) that started as a small ask and reached 8 files and +1233 lines (its named precedent, #1031, was 5 files and +689) after a design pivot and repeated dirty review rounds. Every scope gate passed because each measures against the plan's own Context, which the plan's author writes. Evidence: the history study's findings report (unpublished scratch, not committed) and `plan-review/SKILL.md` Step 4.

Intended outcome: one collapsing rule with a single home in `plan-it`. It replaces the current author-measured scope checks with an ask-measured one, adds no hook, no test and no new file, and stays within the Critical files list below.

## Approach

The anchor for every scope gate becomes two things the engineer controls: their request, quoted on an `Ask:` line, and the plan's approved Critical files list. Anything past either one goes to the engineer as a question. It no longer goes back to the author's judgment. This takes text edits across three skill files, none of which is code:
- an `Ask:` line and the file-limit rule in `plan-it`, the rule's home;
- a measure-against-the-Ask question and a quote-provenance check in `plan-review`;
- one clause on `code-review` Step 1.5's out-of-scope tripwire that checks each commit's files against the approved list.

Challenge of the engineer's original sentence ("far past what I asked for"), which shaped the Ask above:
- **"Far past" cannot fire.** A reviewer asked "is this far?" has to judge, and the gates already judged each step of #1035 reasonable against the plan's own Context (row 9). "Far" is the engineer's verdict on the total, so it cannot serve as the trigger. Any item past the anchor triggers the question, and the engineer decides whether it is far.
- **"What I asked for" can only be measured once it is written down.** Both anchors are named: the quoted Ask and the approved file list.
- **"Each plan and the diff that implements it"** covers plan-time growth and post-approval diff growth (the 27% from helper extractions).
- **Legitimate growth is asked about, not blocked.** Tests and docs a called-for deliverable needs count as called for. A review fix inside listed files does not fire. A review fix that needs a new file fires once. A widening the engineer requested passes once their words are appended to the Ask line.
- **What it deliberately does not prevent:** review rounds that compound without a pivot (the round-cap consult owns that); line growth inside listed files (`code-review` Step 1 owns that); plan length; an engineer knowingly approving a large plan.
- **Tested against real PRs:** it fires three times on #1035 (first plan, the pivot, and the commit that stages the post-approval `_lib.sh` and test files); it stays silent on a 3-file +67-line PR; a PR that grew at the engineer's own request passes once that request is on the Ask line.

Alternatives set aside:
- A numeric Budget line from a precedent — replaced by the file list (row 5).
- A pytest bare-tag check — declined by the engineer (row 4).
- Recording post-approval acceptance in the PR body — the next `code-review` round reads the plan, not the PR body, so it would ask again, and the plan's file list would go stale.
- A CLAUDE.md Axis 1 sentence — always loaded for every stow consumer, fails the byte ratchet, and restates plan mechanics in a global file (M5, row 23).
- A hook comparing staged paths with Critical files — heavier, and Critical files is free prose with no path grammar.

Known cost: a file accepted after approval edits the plan, which re-arms `require-plan-review.sh` (row 18) for one more review. That friction is intended. The Step 1.5 check fires after the file is built, so a no costs that work. A branch already in progress whose plan predates the tests-and-docs sentence is asked once per unlisted test.

### Assumption ledger

**Root:** plans and their diffs grow past the engineer's ask because no gate takes the ask as input. `plan-review` Step 4 Q2, B7 and the Step 4 routing paragraph measure against the plan's own Context. CLAUDE.md Axis 1 measures against "the ticket", which a `plan-it` branch often lacks.

**Givens:**
- G1. A spawned reviewer sees its prompt, not the session's messages, so only the session running `/plan-review` can check a quote's provenance. Reason: the harness defines what a subagent receives. `[unverified]`: known harness behavior, not re-checked in docs.
- G2. An `AskUserQuestion` answer is a selected label plus typed text, and the option descriptions are the model's. Reason: harness tool contract. `[verified: claude/.claude/hooks/nudge-answer-provenance.sh:3-5, :26]`

**Rows:**
1. The session's draft sentence set this plan's direction, and its wording was open to tightening and challenge. `[engineer-verified: "Yes something like this "Stop plans and their PRs from growing far past what I asked for." I was hoping the plan-it process would fine tune this and challenge it"]` This covers only that; the sentence itself was the session's draft.
2. The engineer's earlier "this type of scope creep that is so far removed from what's reasonable has to stop." came through a prior session's brief and was not heard this session. `[unverified]`
3. The tightened Ask above is the architect's draft, which the engineer accepted. `[engineer-verified: "Accept as written"]` The label covers only the acceptance of the wording shown in the question.
4. The quote check is a `/plan-review` item with no pytest test. `[engineer-verified: "Plan-review item only (Recommended)"]` The label text was the session's option; only the selection is the engineer's.
5. The plan's file list is the size limit, in place of a numeric Budget line. `[engineer-verified: "Yes, file list is the limit (Recommended)"]`
6. "No numeric Budget unless the engineer names a precedent" is the session's gloss on row 5, not the label. `[unverified]`
7. Applying row 5's limit to the post-approval diff, and not only to the plan, rests on the accepted Ask's "the diff that implements it" (row 3). That row 5's label meant it too is the architect's reading. `[unverified]`
8. Nobody has decided that a plan-links script belongs in this PR. The engineer's "By this PR I meant the PR you were working on. Or was that the other session" settles no scope, and rule-only is the dispatching session's default. `[unverified]`
9. Every existing scope gate measures against the plan's own Context, and none takes the ask as input. `[verified: findings report §3; plan-review/SKILL.md:89, :108, :138]`
10. `plan-review` Step 4 sends a condition that defines *what* the plan delivers back to the author. `[verified: plan-review/SKILL.md:108]`
11. B5 already flags, to the author, a bare tag or a claim beyond its quote on an added or changed row. The ledger cross-check flags a contradiction of quoted content to the human. Nothing checks that a new quote is actually the engineer's. `[verified: plan-review/SKILL.md:130; plan-review/ROUTING.md:55]`
12. Axis 1 measures against "the ticket", and its bucket 2 lets the session keep an out-of-scope file on its own rationale. `[verified: claude/.claude/CLAUDE.md:68-71]`
13. `code-review` Step 1.5 is the post-approval check every commit passes: `code-review` is the `git commit` gate, and its "Out-of-scope file edits" tripwire fires on diff surface but names no source for "the stated task". `code-review` reads the diff and CLAUDE.md, never the branch plan or `plan-it`'s text, which it names only as a procedure at the Fix route. `code-writer` loads neither skill. `[verified: code-review/SKILL.md:3, :73, :76, :317, :364-372; code-writer.md:15-17, :77]`
14. In a dispatched agent, an "ask" step becomes a report in its return, and the agent takes no action the ask gates. `[verified: claude/.claude/CLAUDE.md Agent Core preamble]`
15. #1035 facts. Precedent #1031 was 5 files, +689. The first implementation commit (3815cd87) was 11 files. The final diff is 8 files, +1233. The plan named five paths. The `_lib.sh` helpers plus two test files are 329 of 1233 lines (27%), added by review-driven commits. `[verified: findings report §1-§2; not re-derived from git]`
16. The extraction had a repo rule behind it: pure bash moves into a sourceable lib with direct unit tests. `[verified: claude/.claude/rules/shell-script-conventions.md last bullet; .claude/rules/bash-unit-test-seams.md]`
17. #1097 grew at the engineer's own request: "If the WORKTREE_ROOT stub tests should have a unit tested helper, that needs to be fixed." `[verified: .claude/plans/fix-announce-resume-newline-leak.md:20]` Its +608 size comes from findings §4.
18. Editing a reviewed plan, ledger rows included, re-arms `require-plan-review.sh` on the next Write/Edit. `/plan-review`'s own writes pass while its active marker is live. `[verified: claude/.claude/hooks/require-plan-review.sh:4-5, :18-20, :31-32; plan-review/SKILL.md:15]`
19. `code-review/SKILL.md` is 511 lines against its 500-line limit, and the length gate denies growth of an over-limit file, so its edit must add no line; skill files carry no byte limit. `plan-it/SKILL.md` is 146 of 200 lines; `plan-review/SKILL.md` is 289 of 500. `[verified: wc -l this session; check-skill-length.sh:106-115]`
20. No test pins any sentence being edited. `[verified: grep for "appropriately sized", "goes back to the author", "Out-of-scope file edits", "engineer-verified", "Axis 1", "File boundary" matched no test file]` `[unverified]` for the `code-review` Step 1.5 line: `code-review/REFERENCES.md:16` maps that tripwire to Axis 1 and stays accurate for the general case.
21. `plan-architect` authors Approach through Out of scope, not Context, so the `Ask:` line needs no agent edit. `[verified: claude/.claude/agents/plan-architect.md:37]`
22. (Unused: kept so later row numbers and anchors stay stable.)
23. The file limit applies after approval through `code-review` Step 1.5's check against the plan's Critical files, with no CLAUDE.md edit. `[engineer-verified: "I think plan-it and code-review. Claude.md was never the right place imo."]` The quote covers only that choice of homes. A CLAUDE.md sentence would also fail the CLAUDE.md byte ratchet (31407 bytes against a 25600-byte limit, `check-claude-md-length.sh:52`, verified with `wc -c`).
24. The Fix route sends a review fix that cannot be expressed in a file already in the diff to a `plan-architect` consult; an endorsed consult on a plan branch re-dispatches `plan-it` Step 5, which edits the plan and re-arms the plan gate. A new test, fixture, or doc file alone does not fire it. `[verified: code-review/SKILL.md:354-372; docs/design-decisions/code-reviews-fix-route-unifies-42s.md:9]`
25. The `announce-plan-review-verdict` branch's plan names `_lib.sh` inside its Critical files section only as a reuse citation (:110, :116, :121 under Reuse), never as a path to create or modify (:69-81 list those), so a check for "omitted from the section" would pass it and the M5 clause tests for "does not name as a path to create or modify". `[verified: .claude/plans/announce-plan-review-verdict.md:57, :69-81, :110, :116, :121 on that branch, read by plan-architect this session]` That the final diff nonetheless carries `_lib.sh` changes inherits row 15's source.
26. `plan-review` writes its completion marker only on Approve or Approve with changes, so Request changes leaves the plan gate armed; its fix-or-ask rule already puts a blocking `AskUserQuestion` before the verdict. `[verified: plan-review/SKILL.md:270, :284]`
27. Dropping the file from the diff after a no, and the exception for a file a merge from the default branch brings in, are the architect's additions. `[unverified]` The reasoning for the drop is that the no already settled that the file does not ship. What the engineer said about a no: `[engineer-verified: "I would say stop and ask me again."]` That quote covers only the stop-and-ask: the session asks how to handle what the file served and does no rework of its own.

**Mechanisms:**
- **M1 — `Ask:` line** in `plan-it` Step 2 and in the Context section item. It gives every gate the engineer's words as an input, and a quoted text line is the lightest primitive that can do that. It reuses Step 5's quote rules rather than restating them. `anchors: row9`
- **M2 — Critical files is the file limit** (`plan-it` Critical files item). This sentence is the rule's single home. `code-review` Step 1.5 and `plan-review`'s since-last-commit clause each state only their own check. The sentence asks the author to list tests and docs, so the limit doesn't fire on the first test file. `anchors: row5`
- **M3 — `plan-review` measures against the Ask.** Question 2 lists the items the Ask does not call for, and also the items added since the plan's last commit, and puts them to the engineer as one `AskUserQuestion` once question 3 is clean and before any reviewer spawns. An unanswered or declined item gives Request changes, so no completion marker is written (row 26), and in a dispatched run the ask becomes a report (row 14). `/plan-review` records the answers itself, so the recording does not re-arm the gate (row 18). The `:108` paragraph defers to question 2. Two lighter primitives fail:
  - A finding to the author is today's routing, which let #1035's pivot through (row 10).
  - A file count printed on the verdict line is passive: it asks nothing and blocks nothing.

  `anchors: row9, row10, row5, row14, row26`
- **M4 — quote provenance.** The session running `/plan-review` checks only quotes added or changed since the plan's last commit, not a spawned reviewer. A cited ticket goes through B5. An unfound quote goes into the ask. It is the plan-review item the engineer chose (row 4) and closes the gap B5 leaves (row 11). `anchors: row4, row11`
- **M5 — `code-review` Step 1.5 checks each commit against the approved list.** Its out-of-scope tripwire already fires on diff surface at the gate every commit passes (row 13). The clause adds the yardstick and a blocking stop-and-ask. A no drops the file and returns the rework to the engineer (row 27), and a file a merge from the default branch brings in is not a change. It measures the diff, not the plan's account of it, which is how #1035's `_lib.sh` escaped (row 25). `code-review` never loads `plan-it` (row 13), so the clause states the check and cites `plan-it` only for recording a yes. On plan branches it turns Axis 1's required-edit clause and bucket 2 into an ask (row 12), as the accepted Ask directs (row 3). Two lighter primitives fail:
  - `plan-it` text alone: nothing loaded after approval reads it (row 13).
  - The Fix-route consult plus `plan-review`'s since-last-commit clause: it skips test-only fixes and additions made during implementation (row 24), and #1035's list never grew (row 25). It stays as the before-build catch where it fires.

  The edit adds no line (row 19). `anchors: row3, row5, row7, row12, row13, row23, row24, row25, row27`

## Critical files

Three skill files, edited in one `code-writer` dispatch (the edits are independent prose sentences; `plan-review` and `code-review` restate only their own check, so no split is needed), plus this plan. `claude/.claude/CLAUDE.md` is not edited. Verification command: see Verification.

1. **`claude-skills/skills/plan-it/SKILL.md`**:
   - `:29` (Step 2). Append:
     > Under the goal sentence, add an `Ask:` line: what the engineer asked for, quoted under Step 5's `[engineer-verified]` rules (wording the session drafted and the engineer accepted goes in as `Ask: "<wording>" — accepted via label "<label>"`), or cited to the ticket it came from. When the engineer widens the request later, append their words to that line as a further quote. `/plan-review` measures the plan against this line.
   - `:110`. Change the parenthetical to "(lead with a one-sentence goal, then Step 2's `Ask:` line)".
   - `:112` (Critical files item). Append:
     > Name every file the diff will touch, tests and docs included: once approved, this list is the change's file limit. A file added after approval needs the engineer's answer first; the session then adds it to the list itself, with its own `[engineer-verified: "<quote>"]` row or, if it widens the Ask, on the Ask line.
2. **`claude-skills/skills/plan-review/SKILL.md`**:
   - After `:94` (the last over-elaboration marker) and before item 3 at `:96`, insert, at column 0 like its neighbouring lines (an indented insert would render as part of the "Could be done in N lines" bullet):
     > **Measure against the ask.** Size the plan against the Context's `Ask:` line, not the plan's account of itself. A plan without one goes back to the author. List each deliverable or Critical file the Ask does not call for. Tests and docs a called-for deliverable needs count as called for, and an item you are unsure of goes on the list. Also list every Critical file added and every change to what the plan delivers since the plan's last commit on this branch, once it has one. Leave off an item an `[engineer-verified: "<quote>"]` row already covers. An item on the list is the engineer's call, not a finding for the author. Once question 3 is clean, put the list to the engineer as one `AskUserQuestion` before spawning any reviewer, and record each answer on its own ledger row quoting it; an unanswered or declined item makes the verdict **Request changes**.
   - `:108`: replace "is feature scope and goes back to the author" with "is feature scope, which question 2 measures against the `Ask:` line". Change nothing else in that paragraph.
   - After `:110`, insert:
     > **Quote provenance.** Check, yourself and never through a spawned reviewer (which cannot see this session's messages), every quote the `Ask:` line or an `[engineer-verified: "<quote>"]` row attributes to the engineer that was added or changed since the plan's last commit on this branch — every quote, before its first commit. Each must appear among the engineer's own messages or selected labels this session; an accepted session draft is checked by its label and against the wording the question showed. Add any quote you cannot find there, a relayed one or one lost to compaction included, to question 2's `AskUserQuestion` instead of returning it to the author. A ticket cited on the `Ask:` line is a citation: check it as B5 does.
3. **`claude-skills/skills/code-review/SKILL.md:76`** — append on the same line (the file is 511 lines against its 500-line limit, and the length gate denies any growth):
   > When the plan named for this branch's slug exists under `.claude/plans/`, a changed file its Critical files section does not name as a path to create or modify (a reuse citation is not a listing; a listed directory or glob covers what it contains), other than the plan itself and a file a merge from the default branch brings in, is a blocking stop-and-ask to the human even when the change requires it. Record a yes per `plan-it/SKILL.md` § "Step 5 — Architecture design". A no drops that file from the diff, and how to handle what it served goes back to the engineer rather than being reworked on your own.
4. **`.claude/plans/plan-scope-anchor-rule.md`** (this plan). It carries its own `Ask:` line and is committed per `plan-it` Step 7.

Reuse: Step 5's existing quote rules, B5, the ledger cross-check, and the `code-review` phrase "blocking stop-and-ask to the human" stay as they are. M4 adds only the provenance check they lack.

## Verification

1. Run the project's scoped suite, `.venv/bin/python3 claude/.claude/scripts/select-tests.py`. For the worktree-relative `.venv` path, see README's Tests section. Expect green, including `test_skills.py`'s citation check on the new `code-review` citation (expected, not checked).
2. `claude/.claude/CLAUDE.md` is absent from `git diff --stat origin/main...HEAD`; `wc -l claude-skills/skills/code-review/SKILL.md` still prints 511; `check-skill-length.sh` passes all three SKILL.md files.
3. Run `/skill-review` on all three skill diffs; `require-skill-review.sh` enforces it.
4. Behavior fixtures, kept in a scratchpad and never committed (`.claude/rules/skill-and-agent-self-review.md`). Give a fresh `general-purpose` agent (`model: sonnet`) the new Step 4 text plus each case below:
   - #1035's first plan (`git show 7fc75d72:.claude/plans/announce-plan-review-verdict.md`, run in the `announce-plan-review-verdict` worktree, read-only) with the relayed ask as its `Ask:` line. The list must name the skill Step-1 Write, the sibling file, `marker.sh`, and `helpers.py`.
   - The same plan run as a dispatched agent with no engineer reachable. It must return the list and Request changes.
   - `.claude/plans/fix-announce-resume-newline-leak.md` with row 17's quote appended to its `Ask:` line. The list must omit `_lib.sh` and its unit test.
   - #1181's plan. The list must be empty.
   - For M5, give a fresh agent the revised `code-review` Step 1.5 line, the `announce-plan-review-verdict` plan's Critical files, and a staged set of `_lib.sh` plus its test file. It must stop and ask. The hook, its test file and the plan itself must come back silent. Two more cases: a staged set including a file brought in by a merge from the default branch must come back silent for that file, and a scripted "no" must yield a dropped file plus a question back to the engineer, with no rework.

## Out of scope

- **A numeric Budget or precedent-size line** (rows 5-6). If the Ask names a precedent ("like X"), it is already on the Ask line as a quote, and question 2 lists any item the Ask does not call for.
- **A pytest bare-tag check** (row 4).
- **Review rounds compounding without a pivot, and line growth inside listed files.** The round-cap consult and `code-review` Step 1 own these, and a file limit cannot measure them.
- **Re-tagging the 31 bare `[engineer-verified]` rows in merged plans.** `plan-it` keeps them valid.
- **`plan-architect.md` and `claude/.claude/CLAUDE.md`.** `plan-architect.md` needs no edit (row 21). CLAUDE.md gets none: Axis 1 stays the general rule, and Step 1.5 carries the plan-branch case (M5).
- **Pre-existing: `plan-it/SKILL.md:124`'s draft-PR handoff.** It tells the session to open a draft PR, but `require-ready-for-review.sh` gates "creating a PR" (header :4-5), and a grep found no `--draft` exemption. A separate PR repairs it.
- **A plan-sharing step to replace PR #1035** (a proposed `plan-share-links.sh`). Its own branch and plan, written under this rule with the engineer's own words as its Ask (row 8).
