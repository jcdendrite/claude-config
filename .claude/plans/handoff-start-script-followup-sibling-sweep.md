# Separate handoff's pre-write recipes, default deferrals to a documented limit, sweep review-fix siblings

## Context

Three recurring error modes surfaced by an error-mode analysis of this repo's review traces get their cheapest fix each, in one PR. First, the handoff skill presents its two pre-write commands (marker activation and handoffs-directory creation) as adjacent one-line recipes, and agents fuse them into one Bash call that the marker-shape gate denies. The analysis found that fusion recurring. Second, agents propose or file follow-up issues by default, and the engineer has pushed back on such proposals. Third, when a PR reviewer flags a design distinction at one site, respond-pr fixes only that site and replies FIXED while the same distinction survives at sibling sites, so the reviewer re-raises it comment after comment. The intended outcome is a handoff recipe whose two pre-write commands no longer sit adjacent, with the marker activation kept as the routine first step and run as its own Bash call; deferral dispositions in code-review and respond-pr that accept a documented limit in place of a ticket; and one respond-pr step that sweeps siblings before replying FIXED.

Ask: "yeah that sounds great actually, go do that" — accepting the session's own proposal (session wording, not the engineer's): "plan the first three as one small PR and EM1 as its own, since EM1 touches the shared library and should be reviewed alone", where "the first three" were EM2 (handoff step fusion), EM6 (follow-up issues by default), and EM9 (one-site fixes under review comments). On EM2 the engineer first selected the label "Demote activate to a recovery step (Recommended)"; after plan review showed that premise false for typed `/handoff`, the engineer wrote "Wait how can a typed skill not result in a Skill call? That seems like a Claude code harness bug… unless it's intentional to only count skill calls if they are agent initiated? Something smells here", then "I think userpromptexpansjon in this PR. What does the architect think adversarially", then selected the label "Adjacency-only here; arm + demotion as its own PR (Architect's recommendation)", and then "Keep the paragraph and its test (Recommended)". On EM6 the engineer wrote "Why do you want to add to CLAUDE.md? I question that decision." and then selected the label "Skill sites only, no CLAUDE.md (Recommended)".

## Approach

The handoff skill keeps `marker.sh activate handoff` as its routine first step and now runs it as its own Bash call. Its directory-creation recipe moves to just before drafting, so the two one-line recipes no longer sit side by side. A deferral at code-review's DEFER criterion 2, or at respond-pr's `OUT-OF-SCOPE`/`DEFERRED`, is satisfied by a documented limit by default, and a ticket is filed only on request. respond-pr's `FIXED` replies gain a required `other-sites` field, and step 6's `code-writer` dispatch applies each fix wherever else in the PR's changed files the reviewer's point holds.

Line numbers throughout are pre-edit locators. Apply each edit by its anchor text, or bottom-up within a file.

**EM2: separate the pre-write recipes.** Row 10 blames the fusion on two adjacent one-line fences. Moving the `ensure-account-dir.sh handoffs` section next to the write removes that adjacency and leaves the activation path unchanged. The activation fence then sits directly above the warrant check's inline `--check`. The marker-shape gate denies that chain too (Row 35), so the activation paragraph gains the "as its own Bash call, never chained" clause that code-review already uses at `code-review/SKILL.md:414`. The routine activation stays because it is the only activator on a typed `/handoff` (G2, Row 9). If Row 10's cause is wrong, the clause is the remaining steer (G1). The collect step's repeat-block paragraph stays (Rows 38-39).

Alternatives set aside:
- **Demote the activation to a recovery step**, as the prior draft did. A typed `/handoff` fires no `Skill` hook, so demotion leaves that path with no activator (G2, Row 9).
- **Add a `UserPromptExpansion` arm and demote in this PR.** The engineer moved both to their own PR (Row 33). Out of scope lists the prerequisites.
- **Add the arm and keep the routine manual step.** That leaves two routine activators, and the fusion surface stays open.
- **The clause alone, with no move.** The adjacent fences that Row 10 blames would remain.
- **A start script that runs both commands.** It needs a new script and a settings allow entry for a fusion that costs one retried call.
- **Widen the marker-shape gate.** Declined; see Out of scope.

**EM6: the deferral default.**
- **code-review.** A DEFER row is already rendered into the PR body (Row 18). Criterion 2 drops the ticket as a precondition. The row's `--rationale` names where the remediation would land, so the documented limit carries what a ticket used to record.
- **respond-pr.** The ticket fields gain `none — documented at <file:line>`. That line must exist when the reply posts: either an existing doc or comment, or a one-line statement that step 6's batch writes (Row 40). The reply says that no follow-up is scheduled and that the reviewer can ask for a ticket. The labels at lines 38 and 40 stop promising a separate PR or a follow-up. Line 125 also covers a ticket requested after a `none` reply.
- **Field names stay.** The reply miner's tokens are unchanged (Row 21).

Alternatives set aside:
- **A CLAUDE.md rule.** The engineer declined it (Row 4).
- **Explicit engineer acceptance of each deferral, logged as an engineer SETTLED row.** That asks a question at every deferral (Rows 6-7).
- **Renaming `follow-up-ticket` and `link-or-ticket`.** That changes the reply format and gains no behavior.
- **"The PR body" as respond-pr's `<where>`.** No respond-pr step writes the PR body (Row 40).
- **Allowing only an already-existing line as `<where>`, with no writer.** Then `none` is unavailable whenever no line states the limit yet, which restores the ticket default.

**EM9: the sibling sweep.** A required field is respond-pr's own forcing mechanism, and it shows the reviewer whether the sweep ran (Rows 23-25). The field takes `file:line` entries or `none — searched <what>`. Step 6 states the reviewer's point as one general sentence, which gives the sweep a defined reach (Row 41). The edit boundary is the PR's changed files. Siblings found outside them are listed but not edited (Row 24).

Alternatives set aside:
- **Rely on the global rule "Audit structural siblings before scoping a fix narrowly" alone.** That rule was already in force when the one-site failure happened (Row 23).
- **Add a step-6 sentence with no field.** Nothing in the reply would expose a skipped sweep.
- **Sweep in step 4's holistic triage.** Triage works across comments, but sibling sites carry no comment.

**Dispatch.** One `code-writer` dispatch covers all five files (Row 27).

**Assumption ledger**

Root: Three skill texts each steer agents into a recurring error mode:
- `handoff/SKILL.md` opens with two adjacent one-line fenced recipes, and agents fuse them into one Bash call that the marker-shape gate denies.
- code-review's DEFER criterion 2 and respond-pr's `OUT-OF-SCOPE`/`DEFERRED` fields make a ticket the price of any deferral.
- respond-pr's `FIXED` path never looks past the cited site.

Givens:
- G1: Which commands an agent fuses into one call is model behavior. This repo can steer it only through the text the agent reads.
- G2: A typed `/skillname` expands without a `Skill` tool call, so no `Skill`-matched tool hook fires on that path. The harness owns slash-command expansion [verified: Claude Code hooks reference, https://code.claude.com/docs/en/hooks, `UserPromptExpansion` section: "a `PreToolUse` hook matching the `Skill` tool fires only when Claude calls the tool, but typing `/skillname` directly bypasses `PreToolUse`. `UserPromptExpansion` fires on that direct path."; `claude/.claude/scripts/transcript-analysis.py:1085`].

Rows 3 and 12-17 were withdrawn with the demotion (Row 33). Their numbers are not reused.

Rows:
- Row 1 [assumption]: one PR covers EM2, EM6, and EM9 [engineer-verified: "yeah that sounds great actually, go do that"] — anchors: root — The proposal it accepted is the session's wording.
- Row 2 [assumption]: EM2 was to demote the manual activation, a selection made before plan review showed the premise false for typed `/handoff` [engineer-verified: "Demote activate to a recovery step (Recommended)"] — anchors: root — Superseded by the engineer's later selection in Row 33; kept as history.
- Row 4 [assumption]: EM6 edits skill sites only and leaves CLAUDE.md alone [engineer-verified: "Why do you want to add to CLAUDE.md? I question that decision." and "Skill sites only, no CLAUDE.md (Recommended)"] — anchors: root — The label names where the rule lives; the evals fixtures and CHANGELOG entries in Critical files are tests and records for that rule, not a second rule surface [unverified].
- Row 5 [assumption]: the session's option description, not the engineer's words: "amend code-review's DEFER criterion 2 and respond-pr's DEFERRED disposition so an engineer-accepted documented limit satisfies the deferral instead of a ticket" [unverified] — anchors: row4
- Row 6 [assumption]: the engineer treats a documented limit as enough and wants follow-up tickets weighed before anyone proposes them. Quoted: "yeah documented limit is enough."; "there's a lot of open issues. we need to prioritize"; "sounds like the others can be punted" [unverified] — anchors: row4 — The session relayed these from earlier sessions' transcripts. They are not this session's words, and this design did not re-read the transcripts.
- Row 7 [assumption]: "engineer-accepted" in Row 5 means different things in the two skills [unverified; the respond-pr flow is verified at `respond-pr/SKILL.md:32`, `:58`] — anchors: row5, row6
  - For code-review, the deferral is surfaced in the recommendation and in the PR body's deferred-findings table. A ticket is filed only when the engineer asks.
  - For respond-pr, the `none` default is the agent's decision at reply time. Replies post after the step-8 commit, and no step asks the engineer first. The reviewer sees the reply and can ask for a ticket. This widens Row 5's "engineer-accepted" for respond-pr.
- Row 8 [assumption]: the hard block fires only while this session has no live handoff bypass marker. A live marker idles out after 60 minutes unless the nudge hook refreshes it [verified: `nudge-handoff-near-context-cap.sh:650-656`; `_lib.sh:2577-2581`, `:2642-2652`] — anchors: row38
- Row 9 [assumption]: on a typed `/handoff`, the skill's own `marker.sh activate handoff` step is the only activation. `activate-handoff-bypass.sh` runs on `PostToolUse` with matcher `Skill`, and a typed invocation makes no `Skill` call (G2) [verified: `claude/.claude/settings.json:443`, `:506-511`] — anchors: row33, row36
- Row 10 [assumption]: agents fuse the two recipes because they are adjacent single-line fences at the top of the skill [unverified] — anchors: root, G1 — This cause is inferred from the session's trace analysis.
- Row 11 [assumption]: `handoff/SKILL.md` has 200 lines, exactly its cap. `check-skill-length.sh` denies a commit only when the staged file is over its limit and longer than the committed version [verified: Read of `handoff/SKILL.md` through line 200; `check-skill-length.sh:6-11`] — anchors: row36
- Row 18 [assumption]: a DEFER row is rendered into the PR body's "Deferred review findings" table, or by `/ready-for-review` step 8 when no PR exists yet. A deferral is therefore documented with no ticket [verified: `code-review/SKILL.md:410-416`] — anchors: row19
- Row 19 [mechanism]: in `claude-skills/skills/code-review/SKILL.md:381`, replace only the final sentence, `If it's pre-existing structural debt, DEFER is valid only when a ticket is filed for the remediation and the issue is surfaced to the human in the recommendation.`, with `` If it's pre-existing structural debt, DEFER is valid only when the issue is surfaced to the human in the recommendation. The DEFER row's `--rationale` names where the remediation would land. `review-ledger.sh render` carries that row into the PR body as the documented limit (see "Review-findings persistence" below), so file a ticket only when the engineer asks for one. `` — anchors: row4, row5, row7, row18, row43 — No pin or other site restates the replaced sentence [verified: grep for "ticket is filed"]. The `--rationale` clause is this design's addition and keeps criterion 2 from losing its record of where the work belongs.
- Row 20 [assumption]: `OUT-OF-SCOPE`'s `link-or-ticket` field (URL or "will create") has the same ticket-by-default shape as `DEFERRED`'s `follow-up-ticket`. The structural-sibling rule therefore brings it into the fix, even though the session's option description named only `DEFERRED` [unverified; field text verified at `respond-pr/SKILL.md:39`, `:41`] — anchors: row5
- Row 21 [assumption]: keeping the field names and adding `other-sites` before `commit_sha` leaves every reply classification unchanged. The miner classifies a deferral reply by the presence of `where-tracked`, `deferral-reason`, or `follow-up-ticket`. It classifies a FIXED reply by `disposition: fixed-` plus a hex `commit_sha:` value that it finds by search [verified: `evals/review_bench/mine_pr_comments.py:62-64`, `:325-338`]. The test file's reply fixtures are hand-copied from step 5 and documented as never reading SKILL.md, so they drift silently [verified: `evals/test_review_bench_mining.py:2014-2018`] — anchors: row42
- Row 22 [mechanism]: `claude-skills/skills/respond-pr/SKILL.md` deferral edits — anchors: row4, row6, row7, row20, row21, row40, row43
  - Line 38 becomes: `` - **`OUT-OF-SCOPE`** — the comment identifies a real issue outside this PR's subject, which this PR will not fix. `` (three-space indent kept).
  - Line 39 becomes: `` Required fields: `acknowledgment` (confirm the issue is real), `where-tracked` (ticket, backlog, or documented limit), `link-or-ticket` (URL, "will create", or `none — documented at <file:line>`; see below). `` (five-space indent kept).
  - Line 40 becomes: `` - **`DEFERRED`** — the change is valid and within this PR's subject, but intentionally postponed. ``
  - Line 41 becomes: `` Required fields: `acknowledgment`, `deferral-reason`, `follow-up-ticket` (URL to an existing ticket; `"will create: <one-line summary>"`, created before the skill session ends; or `none — documented at <file:line>`; see below). `` (five-space indent kept).
  - Insert one paragraph and one blank line between the blank line that follows the `AGREE-NO-CHANGE` fields and the line starting `**Worked examples**`, at the list's three-space indent: `` **A documented limit, not a ticket, is the default for `OUT-OF-SCOPE` and `DEFERRED`.** Write `none — documented at <file:line>`, naming a line that states the limit when the reply posts: an existing doc or comment, or the one-line statement step 6 writes. The reply posts with the same timing as a `FIXED` reply's `commit_sha`, after step 8's commit and push, so the named line is on the pushed branch. The reply says plainly that no follow-up is scheduled and that the reviewer can ask for a ticket in a reply. Promise a new ticket (`will create`) only when the reviewer or engineer asks for one. ``
  - In line 125, insert `` A ticket filed on request after a `none` reply gets the same correction for that reply. `` before `Nothing re-reads those artifacts for you.` The three phrases that `TestRespondPrPromiseRedemption` pins stay verbatim (`test_skills.py:1780`, `:1788`, `:1795`).
  - The step-6 sentence that writes the limit statement is part of Row 25's line-53 text.
- Row 23 [assumption]: respond-pr has no sibling or sweep language. The global CLAUDE.md rule "Audit structural siblings before scoping a fix narrowly" already binds every session, yet the one-site failure happened anyway [verified: full Read of `respond-pr/SKILL.md`; global CLAUDE.md §Engineering Judgment] — anchors: root
- Row 24 [assumption]: the sweep edits only the PR's changed files, which matches respond-pr's change set and CLAUDE.md Axis 1's file boundary. A sibling found outside those files is listed as `found, not changed: <file:line>` and is not edited. That listing gives the global rule's "scope follows the bug" a disclosure without an out-of-boundary edit [unverified] — anchors: row25, row43
- Row 25 [mechanism]: `respond-pr/SKILL.md` sweep edits — anchors: row21, row23, row24, row41, row43
  - In line 35, insert `` `other-sites` (the `file:line` of each other site step 6 fixed for the same point, plus `found, not changed: <file:line>` for each sibling outside the PR's changed files, or `none — searched <what>`), `` before `` `commit_sha` ``.
  - In line 48, insert `` | `other-sites: handlers/import.ts:31` `` before `` | `commit_sha: <sha>` ``. This gives the field a populated example.
  - Line 53 becomes: `` 6. Dispatch one `code-writer` covering the whole `FIXED` set as a single batch, not one dispatch per comment — step 4's holistic triage is why the batch is the right unit. For each `FIXED` comment, the prompt states the reviewer's point as one general sentence, naming the construct and the behavior it requires, and has `code-writer` apply the fix wherever that sentence holds in the PR's changed files. `code-writer` searches the whole repository for sites where that sentence holds, edits only those inside the PR's changed files, and returns, per comment, the `file:line` of each site it changed, each site it examined and left unchanged with the reason, and each sibling it found outside the changed files, unedited. The same batch writes, for each `OUT-OF-SCOPE` or `DEFERRED` comment whose `none — documented at` value has no existing line to name, a one-line statement of the limit at the site the comment cites, as a code comment or a doc sentence in that file's own syntax, and returns its `file:line`. Dispatch the batch whenever it has at least one `FIXED` comment or one such statement to write. The parent keeps the commit (step 8) and every reply (step 7). Then, for each unresolved comment: ``
  - Two lighter primitives fail: the global rule alone (Row 23), and a step-6 sentence with no field, where nothing in the reply exposes a skipped sweep.
- Row 26 [assumption]: `code-review/SKILL.md` has 488 lines against a cap of 500, and `respond-pr/SKILL.md` has 125 against the default 200. Row 19 adds no line, and Row 22 adds two [verified: Read; `check-skill-length.sh:11-13`] — anchors: row19, row22, row25
- Row 27 [mechanism]: one `code-writer` dispatch covers all five files. Every edit is small (Rows 19, 22, 25, 36, 42), and `CHANGELOG.md` spans all three error modes. A split would put two `select-tests.py` runs on one shared worktree and gain no useful parallelism — anchors: root
- Row 28 [assumption]: the `OUT-OF-SCOPE` widening in Row 20 stays in the plan [engineer-verified: "Widen EM6 to respond-pr's OUT-OF-SCOPE disposition"] — anchors: row20 — Selected at plan review's measure-against-the-ask question; the engineer also asked "And what does the architect think about this widening?", answered by relaying Row 20's own rationale.
- Row 29 [assumption]: the collect step's repeat-block paragraph and its test were to come out, as part of the demotion package [engineer-verified: "Delete the collect step's 'repeat block is expected' paragraph and its test"] — anchors: root — Superseded by Row 39; kept as history.
- Row 30 [assumption]: the changelog entries stay [engineer-verified: "Three CHANGELOG bullets under Unreleased"] — anchors: root
- Row 31 [assumption]: the engineer questioned whether it is intended that a typed skill produces no `Skill` call [engineer-verified: "Wait how can a typed skill not result in a Skill call? That seems like a Claude code harness bug… unless it's intentional to only count skill calls if they are agent initiated? Something smells here"] — anchors: G2 — The quote asks a question and records no decision. G2's documented harness behavior answers it.
- Row 32 [assumption]: the engineer first proposed shipping the `UserPromptExpansion` arm in this PR and asked for the architect's adversarial view [engineer-verified: "I think userpromptexpansjon in this PR. What does the architect think adversarially"] — anchors: row33 — Superseded by Row 33's selection.
- Row 33 [assumption]: EM2 ships adjacency-only in this PR, and the `UserPromptExpansion` arm and the demotion ship together as their own PR [engineer-verified: "Adjacency-only here; arm + demotion as its own PR (Architect's recommendation)"] — anchors: root
- Row 34 [assumption]: the details behind Row 33's label come from the consult's recommendation, not from the engineer's words. They are: keep the routine activation, move the directory-creation section next to the write, and add an own-Bash-call clause, because the activation fence then sits above the warrant check's inline `--check` [unverified] — anchors: row33
- Row 35 [assumption]: `enforce-marker-script-shape.sh` denies `marker.sh activate handoff` when it is chained to any non-`marker.sh` command, so a fused activation plus `--check` is denied just as the old fused pair was [verified: `enforce-marker-script-shape.sh:1020-1022`, `:1033-1037`, `:1063-1086`, `:1088-1090`] — anchors: row36
- Row 36 [mechanism]: `claude-skills/skills/handoff/SKILL.md` edits, which keep the file at 200 lines — anchors: row9, row10, row11, row33, row34, row35
  - In the paragraph after the `activate-gate` fence (pre-edit line 17), replace `Run this first, before the warrant check below: it suppresses` with ``Run this first, before the warrant check below, as its own Bash call, never chained — `enforce-marker-script-shape.sh` denies it chained to a non-`marker.sh` command such as the warrant check's `--check`. It suppresses``. The rest of the paragraph is unchanged and stays on one line.
  - Cut the section that runs from `## Before writing: create the handoffs directory` through the blank line after its closing fence (pre-edit lines 19-27). Paste it unchanged, `write-target` fixture comment included, immediately before `## Verify the handoff file with Bash, never Read`. It lands after the blank line that ends the collect section.
  - The `## Before writing:` sections then run in this order: activate, warrant check, collect, create directory.
  - No other line changes. The line-37 deactivate sentence ("the marker activated above") stays true, and the line-47 paragraph stays (Row 39).
- Row 37 [assumption]: moving the section is position-safe [verified] — anchors: row36
  - Tests find fixtures by id anywhere in the file (`claude/.claude/tests/helpers.py:79-103`). The `write-target` tests (`test_skills.py:2896`, `:2922`) therefore still run the recipe.
  - The warrant-check pins slice from their heading to the next `## ` heading (`test_skills.py:5389-5391`). That is the collect heading both before and after the move.
  - No test asserts the order of handoff's sections. Docs that mention the warrant check or the directory step cite the warrant section by heading or describe `ensure-account-dir.sh`'s caller (`docs/handoff-nudge.md:93`, `docs/cost-levers-considered.md:226`, `:232`, `docs/scripts.md:314-318`); none states section order.
  - A chain led by `ensure-account-dir.sh` fast-exits the marker-shape gate (`enforce-marker-script-shape.sh:1020-1022`). No `marker.sh` fence sits next to the section's new position.
  - Nothing before drafting writes into the handoffs directory. A grep of `nudge-handoff-near-context-cap.sh` for "handoffs" finds nothing.
- Row 38 [assumption]: the collect step's repeat-block paragraph (pre-edit line 47) is still load-bearing under adjacency-only [verified: `handoff/SKILL.md:17`, `:43`, `:47`; Row 8] — anchors: row8, row29, row33
  - Line 17's "If the block fires anyway, see "Before writing: collect in-flight background dispatches" below" points to this paragraph, and no recovery section replaces its resume instruction.
  - A block during the collect wait is still reachable. The block needs a non-live marker (Row 8). The routine activation can fail, which line 17 calls non-fatal, or the marker can idle out across a multi-turn wait.
  - The paragraph's instruction is correct in both cases: the block ends the turn, and the collect step resumes on the next turn. Its clause "it just means the session is still past its threshold" leaves out the marker condition. That imprecision predates this PR.
- Row 39 [assumption]: Row 29's deletion lapsed with the demotion, so this PR does not apply it. Line 47 and its pin (`test_skills.py:2160-2167`) stay unchanged, and the demotion follow-up revisits both [engineer-verified: "Keep the paragraph and its test (Recommended)"] — anchors: row29, row38
- Row 40 [assumption]: no respond-pr step writes the PR body, and replies post only after step 8's single commit. A `file:line` written by step 6's batch therefore exists when the reply posts [verified: `respond-pr/SKILL.md:32`, `:53-57`, `:83`] — anchors: row22, row43
- Row 41 [assumption]: EM9's field needs three constraints before it can show whether the sweep ran [unverified] — anchors: row25
  - `none` names what was searched, because a bare reason proves nothing.
  - Entries are `file:line`, because sibling fixes have no thread of their own.
  - Step 6 states the point as one general sentence (the construct and the behavior it requires), so the sweep reaches neither only the cited site nor every loosely similar site.
- Row 42 [mechanism]: `evals/test_review_bench_mining.py` edits — anchors: row21, row22, row25
  - Update `_RESPOND_PR_FIXED_EXAMPLE` (pre-edit lines 2019-2024) to Row 25's new line-48 example, which places `other-sites` before `commit_sha`. Every `_fixed_reply_body` caller then exercises that order, including `TestClassifyReply`'s `fixed-with-a-40-hex-sha` case, which asserts `("FIXED", <sha>)`.
  - Beside `_RESPOND_PR_DEFERRED_REPLY`, add two constants. One is a `DEFERRED` reply carrying `follow-up-ticket: none — documented at <file:line>`. The other is an `OUT-OF-SCOPE` reply carrying `where-tracked: documented limit` and `link-or-ticket: none — documented at <file:line>`. Each also carries the no-follow-up sentence from Row 22's paragraph. The comment at pre-edit line 2030 already covers constants written from the field lists.
  - Add both constants to `TestClassifyReply`'s parametrize list (pre-edit lines 3574-3593), expecting `("out-of-scope-or-deferred", None)`, and to the sibling parametrize list at pre-edit lines 2598-2609. Both go through the production `classify_reply`.
  - Re-wrap `_RESPOND_PR_FIXED_EXAMPLE`'s last fragment so no line exceeds the 130-column `line-length` in `pyproject.toml`.
  - In the fixture comment, note that the deferral fixtures guard against classifier tightening, not SKILL.md drift.
- Row 43 [assumption]: the four defaults the revision chose stand [engineer-verified: "respond-pr 'none' is the agent's call at reply time", "Step 6 writes a one-line limit statement when none exists", "Required other-sites field with 'found, not changed' disclosure", "Criterion-2 DEFER rationale names where the remediation would land"] — anchors: row7, row19, row22, row24, row25, row40, row41 — Each label was selected at plan review after the revision; the option descriptions behind them are the session's wording.

## Critical files

One `code-writer` dispatch (Row 27):
- `claude-skills/skills/handoff/SKILL.md`: the pre-edit line-17 paragraph and the moved directory section (Row 36).
- `claude-skills/skills/code-review/SKILL.md`: the last sentence of line 381 only (Row 19).
- `claude-skills/skills/respond-pr/SKILL.md`: lines 35, 38, 39, 40, 41, 48, 53, and 125, plus the inserted paragraph (Rows 22, 25).
- `evals/test_review_bench_mining.py`: the fixture constants and the `TestClassifyReply` parametrize list (Row 42).
- `CHANGELOG.md`: three bullets at the top of `## [Unreleased]` → `### Changed`, in the file's existing shape of a bold lead sentence with sub-bullets:
  1. **`/handoff` keeps its marker activation as the first step, run as its own Bash call, and now creates the handoffs directory just before drafting.**
     - The two one-line recipes no longer sit next to each other. `enforce-marker-script-shape.sh` denies the activation chained to a non-`marker.sh` command.
     - A run that the warrant check stops no longer creates the directory.
  2. **code-review's DEFER criterion 2 and respond-pr's `OUT-OF-SCOPE`/`DEFERRED` replies take a documented limit by default, and a ticket is filed only on request.**
     - A criterion-2 DEFER row names where the remediation would land, and the PR body carries it.
     - A respond-pr `none — documented at <file:line>` reply names a line on the pushed branch, which respond-pr may add to the diff as a one-line limit statement, and says no follow-up is scheduled.
  3. **respond-pr `FIXED` replies carry a required `other-sites` field, and step 6 applies each fix at sibling sites in the PR's changed files.**
     - Siblings found outside those files are listed as `found, not changed` and are not edited.

The dispatch's verification is items 1-4 below. The session also commits `.claude/plans/handoff-start-script-followup-sibling-sweep.md` itself, per `/plan-it` Step 7.

Reuse:
- `extract_skill_command` (`claude/.claude/tests/helpers.py:79`), unchanged. The `write-target` fixture is found by its id.
- `classify_reply` (`evals/review_bench/mine_pr_comments.py:325`) is the production function the new fixtures go through.
- code-review's existing DEFER `render` persistence documents the limit (Row 18).
- respond-pr's step-6 batch and step-8 single commit carry the limit statements (Row 40).
- respond-pr's line-125 guideline governs the requested-ticket path.

## Verification

1. **Residue and order.**
   - Before editing, `git grep -n -e "a ticket is filed for the remediation" -e "belongs in a separate PR" -e "intentionally left for a follow-up" -- claude-skills/` prints `code-review/SKILL.md:381` and `respond-pr/SKILL.md:38` and `:40`. After editing, the same command prints nothing.
   - `git grep -n "^## Before writing:" -- claude-skills/skills/handoff/SKILL.md` prints the activate, warrant-check, collect, and create-directory headings in that order.
2. **Line counts and named lines.**
   - `wc -l claude-skills/skills/handoff/SKILL.md` prints 200.
   - Before commit, `git diff HEAD -- claude-skills/skills/handoff/SKILL.md` shows only two changes: the replaced activation paragraph, and the directory section removed above the warrant-check heading and re-added byte-identical above `## Verify the handoff file with Bash, never Read`.
   - Before commit, `git diff HEAD --numstat -- claude-skills/skills/handoff/SKILL.md claude-skills/skills/code-review/SKILL.md claude-skills/skills/respond-pr/SKILL.md` prints `10	10`, `1	1`, and `10	8`. The respond-pr figure is eight single-line replacements (lines 35, 38, 39, 40, 41, 48, 53, 125) plus the paragraph and its blank line.
   - Any other change means the diff touched lines this plan does not name.
3. **Tests.** Run `.venv/bin/python3 claude/.claude/scripts/select-tests.py`. Its run covers:
   - The `activate-gate`/`deactivate-gate` execution test (`test_nudge_handoff_near_context_cap.py:2570-2610`), which is selected for `handoff/SKILL.md` (`select-tests.py:741`).
   - The `write-target` tests (`test_skills.py:2896`, `:2922`), run from the section's new position.
   - The warrant-check pins (`test_skills.py:5376-5397`).
   - `TestRespondPrPromiseRedemption` (`test_skills.py:1763-1795`), whose three line-125 phrases survive Row 22.
   - `evals/test_review_bench*.py`. These are selected only because this diff edits `evals/test_review_bench_mining.py` (`select-tests.py:543`, `:559`); a respond-pr SKILL.md change alone does not select them. Confirm that the printed targets include them.
4. **Lint.** Run `.venv/bin/ruff check evals/test_review_bench_mining.py`. The documented lint paths (`claude/.claude/ claude-skills/`) do not reach this file, and no Python file under those paths changes.
   - `evals/` is outside CI's pytest and ruff paths (`.github/workflows/tests.yml:159`, `:166`, `:170`), so the local runs in items 3 and 4 are the only evidence. The dispatch report and the PR body record the printed `select-tests.py` targets, the `evals/test_review_bench*.py` pass count, and the `ruff check` result.
5. **Reviews.** Run `/skill-review` on each of the three SKILL.md diffs; `require-skill-review.sh` enforces this at commit. Then run `/code-review`.
6. **Trace, recorded in the PR body.** Each row must be re-derivable from the edited text.
   - T1, a model-invoked `Skill(handoff)`: the hook activates the marker on load, and the skill's own activation then runs as its own Bash call. The run continues with the warrant check, collect, `ensure-account-dir.sh handoffs`, the draft, `check-handoff.py`, `handoff-record-conversion.sh`, and deactivate.
   - T2, a typed `/handoff`: no hook activation happens (G2). The skill's own activation is the first and only one. The rest of the run matches T1.
   - T3, the warrant check says no: the warrant section's closing deactivate runs and the skill stops. The handoffs directory is never created.
   - T4, the marker is not live during the collect wait, because the activation failed or idled out, and a block fires: the turn ends, and the next turn resumes the collect step per that step's resume paragraph.
   - EM6: a criterion-2 DEFER row names the remediation's home and is rendered into the PR body, with no ticket. A respond-pr `DEFERRED` reply carries `follow-up-ticket: none — documented at <file:line>` naming a line on the pushed branch, plus the no-follow-up sentence.
   - EM6, a batch with no `FIXED` comments: the step-6 dispatch still runs to write the limit statement at the cited site, `code-writer` returns its `file:line`, step 8 commits and pushes it, and the reply names that line.
   - EM9: a `FIXED` reply's `other-sites` lists the sibling `file:line`s changed in the same commit, or reads `none — searched <what>`.

## Out of scope

- **The `UserPromptExpansion` arm and the demotion, as their own follow-up PR (Row 33).** No issue is filed; the engineer named the follow-up. Its prerequisites:
  - A human types a slash command while a scratch `UserPromptExpansion` hook sits in gitignored `settings.local.json`, recording key names and `command_name` only. That capture settles the real `command_name` format for stowed, plugin, and directory-qualified skills, the matcher semantics on `command_name`, and the `expansion_type` values.
  - A cross-version check of how an older CLI treats an unknown event key in the shared `settings.json`.
  - The arm ships only together with the demotion. An arm beside the routine manual step would leave two routine activators, with the fusion surface still open.
  - The follow-up also revisits the collect step's repeat-block paragraph (Rows 38-39) and every doc site that describes the routine activation.
- **Widening the marker-shape gate to admit `marker.sh activate handoff; <command>`.** The gate is this repo's own hook, so the plan could change it, and deliberately does not. Its tier line lists `irreversible`, and the repo CLAUDE.md's hook threat model says such a gate "is never relaxed on false-positive cost alone" [verified: `enforce-marker-script-shape.sh:1016-1022`, `:1033-1090`].
- **The after-writing pair** (`handoff-record-conversion.sh`, then `marker.sh deactivate handoff`). It is a structural sibling of the fused pair. Fused in written order, though, its leading token is not `marker.sh`, so the marker-shape gate fast-exits [verified: `enforce-marker-script-shape.sh:1020-1022`].
- **Other skills' activation steps** (plan-review, ready-for-review, ai-instruction-and-memory-files, respond-pr). Each activation fence is followed by prose, not by a second fenced recipe, so the adjacency the root names does not recur [verified: `plan-review/SKILL.md:19-22`, `ready-for-review/SKILL.md:25-28`, `ai-instruction-and-memory-files/SKILL.md:17-20`, `respond-pr/SKILL.md:14-18`].
- **Subagent reach of handoff's activate and deactivate steps.** This is pre-existing, and this PR changes neither step.
- **CLAUDE.md**, which the engineer declined (Row 4).
- **Permissive follow-up mentions.** None of these requires a ticket, and the trace attributes the one skill-driven pushback to criterion 2:
  - `code-review/SKILL.md:341` ("propose grouping fixes into this PR vs follow-ups").
  - `plan-review/SKILL.md:269` (the reviewer "can … create follow-up tickets").
  - `memory-store-audit`'s "file as issue" verdict.
- **`review-pr/SKILL.md:108`**, the reviewer-side merge of findings across sites. It already names every site.
- **Editing sites outside the PR's changed files in the EM9 sweep.** Found siblings are disclosed, not edited (Row 24).
- **New prose pins for the EM2, EM6, and EM9 text.** No runtime layer checks any of these steers (G1 for EM2; reply content for EM6 and EM9). No existing pin held the changed text, so there is no coverage parity to keep. This gap is accepted. Row 42's fixtures are not prose pins: they run the new reply shapes through the production classifier.
- **A `select-tests.py` rule that maps `respond-pr/SKILL.md` to `evals/`.** The evals fixtures are hand-copied and never read SKILL.md (`evals/test_review_bench_mining.py:2014-2018`), so the rule table has no read dependency to encode.
- **Preserved records:** past `CHANGELOG.md` entries and the committed `.claude/plans/` files.
