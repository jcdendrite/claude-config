# Plan: fix the citations and record debris #1090 left behind (GH-1085, G1)

## Context

Goal: fix the wording and record debris that #1090 (GH-1085, the global CLAUDE.md split into Agent Core and Main session groups) left behind, in one small PR that changes no byte of `claude/.claude/CLAUDE.md`.

Why now: #1090 moved the clause "Merge stays human-only; any fork or subagent returns its work to its dispatcher rather than shipping on its own" from Main session's `## Shipping` into Agent Core's `## Working Style`. A hook comment and a test docstring still cite it as "CLAUDE.md's Shipping section". The decision record `docs/design-decisions/global-claude-md-agent-core-and-main-session-groups.md` still reads "Tracker: not yet filed", carries a soft-gate sentence that implies the repo is silent on hook `ask` behavior, and keeps a forward pointer this PR resolves. GH-1094 (`ask-review-permissions.sh`: auto-mode `ask`, path gaps, no-wildcards pin) and GH-1093 (Model & Effort Routing audiences) now exist as trackers.

Intended outcome: the citations resolve, the record's current-state parts point at the trackers, and the user-local `output-preferences.md` is gitignored like its siblings. This is G1. G2 (the Durable text scope line and "the section below" in CLAUDE.md, net-zero bytes) is a separate later PR.

Engineer decisions this plan relies on:
- G split into G1 and G2: "I like the G1/G2 split you recommended".
- The `.gitignore` line moves into G1: "yes on your plan for .gitignore line".
- Rollback is not rewritten; the engineer said "add" a line like "confer with the owner before choosing the approach".

## Approach

This PR does four things. It replaces the two stale "CLAUDE.md's Shipping section" citations (hook comment and test docstring) with a paraphrase of the shipping clause located at "CLAUDE.md's Agent Core". It drops the claim that CLAUDE.md "states this explicitly" and adds one line saying the gate's coverage of forks is unverified. In the decision record, it edits only the parts that describe current state: the soft-gate sentence, the tracker line, the follow-up list, the Forward pointer section that this PR resolves (deleted), and the Rollback line the engineer asked for. It also adds a gitignore entry for `claude/.claude/output-preferences.md`. No byte of `claude/.claude/CLAUDE.md` changes.

**The decision record is in scope, even though `.claude/plans/claude-md-wording-followups.md` treated it as preserved (its G2).** That plan's row 30 sent the record's dangling-phrase follow-ups and the forward pointer to "a separate PR", and this is that PR. CLAUDE.md's Axis 3 decision test decides which lines may change:
- **Editable (describes current state):**
  - :73 makes a present-tense claim about what the repo contains.
  - :100 is an ownership field that currently reads "not yet filed".
  - :118-123 lists follow-ups that are still open.
  - :125-127 is a to-do that becomes false once this PR lands.
  - Rollback is procedure for the future, and the engineer asked for a line there.
- **Read-only (records what happened):**
  - :3, the provenance line.
  - :110 and :112, what #1090 decided and what the scratch test showed.
  - :114 and :116, what #1090 moved and reworded.
- **Current state but deferred:** :93's "Unverified:" list and the other prose-hygiene items. They go to GH-1094 and are not edited here.

**How the delegated question about the output-preferences section is settled.** Lines :114-123 (the Stopping-bullet split, the reworded-lines record, and the follow-up list) already sit directly above `## Forward pointer`, which this PR deletes. So one new heading, `## Moved lines`, inserted before :114 separates them from the output-preferences paragraphs (:110, :112) without moving a single line. That leaves the output-preferences section wholly for #1091, at a cost of two added lines.

**Alternatives set aside:**
- **Retarget the citation to "Working Style".** Rejected: two `## Working Style` headings exist (CLAUDE.md:52 and :137).
- **Quote the clause verbatim.** Rejected: the record (:33) expects the clause to be reworded (the "Merge stays human-only" follow-up), so a verbatim copy would be another place to drift. The H1 name plus a paraphrase survives a rewording.
- **Keep "states this explicitly".** Rejected: the clause says only "returns its work to its dispatcher rather than shipping on its own" (row 2).
- **Put GH-1093 on the tracker line as a tracker for the listed residuals.** Set aside because GH-1093 tracks none of the residuals listed at :97. It goes on the same bullet, marked as tracking something separate.
- **Point from the record to GH-1094 for the prose-hygiene items.** Set aside. The record's reader is "a later `claude/.claude/CLAUDE.md` editor" (:5), and a note about the record's own copy-editing backlog doesn't help that reader. The note would also go stale when GH-1094 lands. The tracker and the PR body hold those items instead.
- **Rewrite the Forward pointer instead of deleting it.** Rejected: a rewrite would only restate what the hook comment now says.

**Open decision for the engineer (does not block implementation):** whether GH-1094 takes the record's prose-hygiene items, which its body does not list today (row 23, Verification 1).

### Assumption ledger

**Root problem:** #1090 moved the shipping clause from Main session's `## Shipping` into Agent Core's `## Working Style`. That left two things behind:
- The hook comment and the test docstring cite a section that no longer holds the clause.
- The decision record says "Tracker: not yet filed". It also keeps a forward pointer that this PR resolves, a follow-up list with no trackers, and a soft-gate sentence that implies the repo says nothing about hook `ask` rendering.

Separately, the user-local `output-preferences.md` has no belt-and-suspenders ignore entry, unlike its siblings.

**Givens:**
- **Given: how Claude Code fills `agent_type` in a Stop payload, forks included, is vendor behavior this repo does not record.** The docs record `agent_type` use only on other events (docs/hooks.md:150 PostToolBatch, :172 PostToolUse), and nothing shows a fork's or a subagent's Stop payload. `[verified: repo grep of docs/ for agent_type]`

The CLAUDE.md byte ratchet (record :129-131) and Axis 3 (`claude/.claude/CLAUDE.md:75-82`) are repo rules this plan works under, not givens: the plan changes no byte of CLAUDE.md, so neither constrains a design choice here.

**Mechanisms:**

| Mechanism | Justification | anchors |
|---|---|---|
| Hook comment :103-108: name Agent Core, paraphrase the clause, drop "states this explicitly", add a fork-coverage line, fix "gate 4 below" to "gate 5 below", replace "take this same branch" | Each change removes a false or ambiguous statement in the one block this PR scopes. The gate-number fix is a description, not a record, so it is in-file cleanup. | `anchors: root, row1, row2, row3, row4, row5, row6` |
| Test docstring :672-673: same citation, stated for a subagent only | The test covers `general-purpose` only. DAMP test code (a named exception to single source of truth) lets the docstring stand alone. | `anchors: row1, row2, row7` |
| Record :73: replace its third sentence with a section citation | The repo has a dated finding and a soft-gate classification. Citing them states the repo's position without restating it (`.claude/rules/design-decisions.md`, "Cite, don't restate"). | `anchors: row9, row10` |
| Record :100: tracker line | Fills in "not yet filed", names the residual that has no tracker, and marks GH-1093 as tracking something separate. | `anchors: row20, row21, row22` |
| Record: insert `## Moved lines` before :114 | Settles the delegated question without moving a line. | `anchors: row15, row16` |
| Record :121 suffix | Points the one follow-up that has a tracker at it. | `anchors: row17, row19, row23a` |
| Record: delete :125-127 | This PR does the follow-up it names. | `anchors: row14` |
| Record Rollback: one new first Procedure bullet | The engineer's request. The other bullets stay as they are. | `anchors: row24` |
| `.gitignore`: new block after :67 | Mirrors the sibling user-local blocks at :43-67. | `anchors: row25, row28, row29` |
| No new test and no CHANGELOG entry | The changed text is a comment, a docstring and markdown, so a test on it would be a source-scan proxy. The gate the comment describes already has tests (`test_agent_type_present_silent`, `test_missing_session_id_silent`, `test_jq_absent_exits_zero_empty_stdout`), and the group test pins the clause's placement under Agent Core. The deleted Forward pointer was the only prose that named the hook comment and docstring as citers of that clause. This plan accepts that loss: both sites now name "Agent Core", which the group test's pin keeps in place. | `anchors: row30, row31, row32` |

All mechanisms are text edits. None is heavier than the task needs.

| # | Assumption | Tag |
|---|---|---|
| 1 | The shipping clause is at CLAUDE.md:96, under `# Agent Core` (:1) › `## Working Style` (:52). A second `## Working Style` sits at :137 under `# Main session` (:130). "Agent Core" is one unique H1. | `[verified: claude/.claude/CLAUDE.md:1,52,96,130,137]` |
| 2 | The clause says "any fork or subagent returns its work to its dispatcher rather than shipping on its own". No CLAUDE.md text says a subagent is "never force-continued", and `## Shipping` (:190-195) says nothing about subagents. So "states this explicitly" is false today. | `[verified: claude/.claude/CLAUDE.md:96,190-195]` |
| 3 | The opening line scopes Main session, which holds `## Shipping`, to the main session and forks. | `[verified: claude/.claude/CLAUDE.md:3]` |
| 4 | Gate 4 (:109) exits on any non-empty `AGENT_TYPE`. The `session_id` gate is number 5 (:111-113). The comment's "gate 4 below" (:106) names the wrong gate. | `[verified: claude/.claude/hooks/advance-past-commit-stall.sh:103-113]` |
| 5 | The hook is wired on `Stop` only. | `[verified: claude/.claude/settings.json:201-206]` |
| 6 | It is unknown whether a fork's turn end reaches this hook, and whether it carries `agent_type` if it does. | `[unverified; the given above]` |
| 7 | The test sets only `agent_type="general-purpose"`. | `[verified: claude/.claude/hooks/tests/test_advance_past_commit_stall.py:671-685]` |
| 8 | The other Shipping citations still resolve, so they stay: (a) REASON (:225) cites "Shipping policy" for autonomous shipping, which `## Shipping` :192 still holds. (b) `docs/commit-stall-block.md:69` names `## Shipping` as the defense against a silent stop, and :192 and :194 still carry that defense. (c) `docs/worktree-bash-guard.md:74` is a site-sweep row recording where a past sweep found Trigger B, and its heading still exists. | `[verified: those lines; claude/.claude/CLAUDE.md:190-194]` |
| 9 | Record :73's "Nothing in this repo states how a hook `ask` resolves there" is literally true for auto mode, but it implies the repo is silent. In fact `docs/security-hardening.md:228-236` records a hook `ask` rendering under `acceptEdits` and `bypassPermissions` (2026-08-08, Claude Code 2.1.223) and says auto mode is untested. Lines :217-220 and :239-240 call this hook's `ask` a soft gate. | `[verified: docs/security-hardening.md:199-240]` |
| 10 | The target heading is exactly `## WebFetch domain allowlisting — considered and rejected`. The record is inside test_skills.py's extraction corpus (it checks that no citation-shaped text is hard-wrapped or unextracted) but outside its resolution corpus, so no test checks that this citation resolves. Verification 2's "Citation resolves" check does. | `[verified: docs/security-hardening.md:199; claude-skills/skills/tests/test_skills.py:3703,5777, per staff-sdet run]` |
| 11 | Each part of the record is classified by Axis 3 as listed in the Approach. | `[verified: claude/.claude/CLAUDE.md:82; the record]` |
| 12 | Decision files are already edited in place when a later decision supersedes them. The directory's convention therefore does not freeze a record's current-state lines. | `[verified: .claude/rules/design-decisions.md, Supersession bullet]` |
| 13 | The prior plan treated the record as preserved (G2), and its row 30 sent the record's follow-ups and forward pointer to a separate PR. That row records the engineer's selected label "Separate PR (Recommended)". | `[verified: .claude/plans/claude-md-wording-followups.md:62,111,146]` |
| 14 | The Forward pointer (:125-127) says the hook's citation "needs a follow-up", and this PR does it. | `[verified: record:125-127]` |
| 15 | Lines :114-123 sit directly above `## Forward pointer`, so inserting a heading before :114 and deleting :125-127 moves no line. | `[verified: record:108-127]` |
| 16 | No file outside `.claude/plans/` cites the record's "The output-preferences deferral" or "Forward pointer" headings. | `[verified: repo grep]` |
| 17 | All four dangling phrases are still in CLAUDE.md: :96, :100, :121, :141. | `[verified: grep of claude/.claude/CLAUDE.md]` |
| 18 | The post-merge spot-check result goes to GH-1085. | `[verified: record:26; .claude/plans/claude-md-agent-core.md:417]` |
| 19 | GH-1091 covers "the rules above": its "Adjacent items" section says "fix the phrase here". | `[verified: gh issue view 1091, body]` |
| 20 | GH-1094 covers gaps (c), (g) and (h) (its Work step 5) and whether a hook `ask` reaches a human under auto mode (step 1). It names no work for gaps (a), (b), (e) or (f). | `[verified: gh issue view 1094, body]` |
| 21 | GH-1093 is the Model & Effort Routing audiences issue, and it tracks none of the residuals listed at :97. | `[verified: gh issue view 1093 title]` plus `[unverified: that its body tracks none of the residuals]` |
| 22 | B (an `agent_type`-keyed deny) stays unfiled, so the fork and identity-gate residual has no tracker issue. | `[unverified: relayed without an engineer quote]` |
| 23 | GH-1094's body does not list the record's prose-hygiene items (hook-internals altitude, the :93 "Unverified:" line, repeated residual statements, the :116 framing). | `[verified: gh issue view 1094, body]` |
| 23a | GH-1085's "Separate small PRs and follow-up issues" list names GH-1093, GH-1094 and #1083 but not the dangling-phrase follow-ups, so GH-1085 cannot be cited as their tracker. | `[verified: gh issue view 1085, body]` |
| 24 | Rollback gets one added line and is not otherwise rewritten. | `[engineer-verified: "add" / "confer with the owner before choosing the approach"]` |
| 25 | G is split into G1 and G2, and the `.gitignore` line is part of G1. | `[engineer-verified: "I like the G1/G2 split you recommended" / "yes on your plan for .gitignore line"]` |
| 26 | G1 changes no byte of CLAUDE.md. The CLAUDE.md phrase fixes belong to G2 or GH-1091. | `[unverified: the session's relay of what its recommended split contains; row 25's quote covers the split, not its contents]` |
| 27 | Issues are cited as bare `GH-<n>`. That matches the record's own GH-1085 (:5, :26, :157) and seven other decision files, and `GH-` is on the redaction allowlist. | `[verified: grep of docs/design-decisions/; repo CLAUDE.md redaction section]` |
| 28 | No `.gitignore` entry covers `claude/.claude/output-preferences.md`. The sibling user-local blocks are at :43-67. No file exists at that path in this worktree or in the main checkout. | `[verified: .gitignore:1-149; Glob]` |
| 29 | README calls the file user-local and never committed. | `[verified: README.md:452]` |
| 30 | No test pins `.gitignore` entries or the record's content. `select-tests.py` matches `.gitignore` to no rule and falls back to the full suite. | `[verified: grep of test_*.py; claude/.claude/scripts/tests/test_select_tests.py:771-779]` |
| 31 | `test_design_decision_files.py` checks the single H1, the provenance line, and that no `## N.` heading exists. None of the edits touches these. | `[verified: claude/.claude/hooks/tests/test_design_decision_files.py:18,90,242,287,382]` |
| 32 | No CHANGELOG entry is needed. No CHANGELOG line mentions a `.gitignore` entry, and the other three files change no behavior. | `[verified: grep of CHANGELOG.md]` plus `[unverified: judgment that an ignore line is not "notable"]` |
| 33 | This worktree is four levels below the repo root because the branch name contains `/`. README's `../../../.venv` does not resolve here, and `.venv` exists at the repo root. | `[verified: worktree layout .claude/worktrees/GH-1085/<slug>/; README.md:520; .venv/pyvenv.cfg at repo root]` |
| 34 | `test_respond_pr_lib.py::TestValidCommentId::test_non_numeric_id_is_invalid[٤٢]` fails under `LANG=en_US.UTF-8` and passes under `LC_ALL=C.UTF-8`. Whether CI's runner default is C.UTF-8 is inferred. | `[verified: staff-platform-engineer run, agent-reviews findings]` plus `[unverified: CI runner locale]` |

## Critical files

Paths are repo-relative.

| Path | Action |
|---|---|
| `claude/.claude/hooks/advance-past-commit-stall.sh` | Replace comment lines 103-108 with the block below. Touch no code and no other comment. |
| `claude/.claude/hooks/tests/test_advance_past_commit_stall.py` | Replace the docstring at 672-673 with the text below. |
| `docs/design-decisions/global-claude-md-agent-core-and-main-session-groups.md` | Apply edits R1-R6 below. Touch no other line. |
| `.gitignore` | Insert the block below after line 67, with one blank line on each side. |
| `.claude/plans/record-followups.md` | This plan, committed before implementation (plan-it Step 7). |

**Hook comment**, replacing :103-108:

```bash
# 4. A Stop payload with agent_type set is never force-continued, because
# CLAUDE.md's Agent Core tells any fork or subagent to return its work to its
# dispatcher rather than ship.
# This gate covers a fork only if the fork's Stop payload carries agent_type,
# which is unverified.
# AGENT_TYPE-unreadable and AGENT_TYPE-absent pass this gate as the main
# session does; that's safe only because a jq/read failure also empties
# SESSION_ID, which gate 5 below independently denies — load-bearing on that
# ordering, not an explicit fail-closed check on this field itself.
```

**Test docstring**, replacing :672-673:

```python
    """A Stop payload with agent_type set is never force-continued: CLAUDE.md's
    Agent Core tells a subagent to return its work to its dispatcher rather
    than ship."""
```

**Record edits** (line numbers are before any edit):
- **R1 (:73).** Replace the third sentence, keeping the first two, so the bullet reads:
  ``- Whether a hook `ask` reaches a human under auto mode is unverified. `docs/auto-mode.md` says auto mode replaces per-action permission prompts with a background classifier. `docs/security-hardening.md` § "WebFetch domain allowlisting — considered and rejected" records the modes where a hook `ask` was verified to render, leaves auto mode untested, and calls `ask-review-permissions.sh`'s `ask` a soft gate.``
  Keep the citation on one line (`.claude/rules/citation-grammar.md`).
- **R2 (:100).** Replace the tracker line with:
  ``- Tracker: GH-1094 for gaps (c), (g) and (h), which it fixes only if the hook survives its evaluation of a first-party `permissions.ask` rule, and for whether a hook `ask` reaches a human under auto mode. Gaps (a), (b), (e) and (f) and the fork and identity-gate residual have no tracker issue. GH-1093 separately tracks the Model & Effort Routing section's audiences.``
- **R3 (before :114).** Insert `## Moved lines` followed by one blank line.
- **R4 (:121).** Leave the lead-in at :118 unchanged. Append ` Tracked in GH-1091.` to the "the rules above" item only.
- **R5 (:125-127).** Delete the `## Forward pointer` heading, its paragraph, and the blank line after the paragraph (:128), so `## Byte margin` follows the blank line at :124 that precedes the removed heading.
- **R6 (before :152).** Insert `- Confer with the repo owner before choosing the approach.` as the first Procedure bullet.

**`.gitignore` block**, inserted after :67:

```gitignore
# Same belt-and-suspenders for the user-local output-preferences file described
# in README.md's Output preferences section. It is never committed; this entry
# prevents a copy inside the stowed tree from landing in git history.
claude/.claude/output-preferences.md
```

The comment names no location for the file, so it stays true whichever path the open output-preferences `@`-import work chooses.

**Edit anchoring.** Every edit above is anchored on its existing text, not on the line number, so a retry is a no-op when the edit is already present: the hook comment's first line `# 4. Subagents are never force-continued`, the docstring's first line `"""Subagents are never force-continued`, the record's `Nothing in this repo states how a hook`, `- Tracker: not yet filed.`, `The Stopping bullet is split across the group boundary.`, the `- The output-preferences bullet's "the rules above"` item, `## Forward pointer`, and `- Revert the whole squash commit`, and the `.gitignore` line `claude/.claude/transcript-config-dirs`. The `.gitignore` block goes after the `transcript-config-dirs` line, separated from it by the existing blank line, with one blank line after the block. The hook-line shift (six comment lines become nine) moves later hook line numbers by +3, so the REASON string cited above at :225 sits at :228 after the edit.

**Read-only references, not to be edited:** `claude/.claude/CLAUDE.md`, `docs/security-hardening.md`, `docs/commit-stall-block.md`, `docs/worktree-bash-guard.md`, `README.md`, `CHANGELOG.md`.

**Dispatch split: one `code-writer` dispatch (`model: sonnet`).** It edits all four files and runs Verification 2-4, 6 and 7. The four files are disjoint, but they share one background: the clause's location and wording, and the fork caveat. The record's R5 also depends on the hook fix. Splitting would mean restating that background in every prompt. The R2 and R4 texts above are final, so the dispatch prompt needs no tracker results. After the dispatch returns, the parent runs `/code-review`, commits, and runs Verification 5 and 8.

## Verification

1. **Trackers (already run during plan review; results in rows 19-23).** GH-1094's body covers gaps (c), (g) and (h) and the auto-mode `ask`. GH-1091's body covers "the rules above". GH-1094's body does not list the prose-hygiene items. Before the PR opens, the engineer decides whether GH-1094 takes them, because that is a write to a public tracker. Whatever they decide, the PR body lists the items, and the record does not.
2. **Citations.** Run these from the worktree root, each as its own call. Absence checks print nothing; presence checks print a match.
   - Absence: `git grep -n "Shipping section" -- claude/`.
   - Absence: `git grep -n "gate 4 below" -- claude/.claude/hooks/advance-past-commit-stall.sh`.
   - Absence: `git grep -n -e "not yet filed" -e "^## Forward pointer" -- docs/design-decisions/`.
   - Presence, hook: `git grep -n -e "CLAUDE.md's Agent Core" -e "gate 5 below" -- claude/.claude/hooks/advance-past-commit-stall.sh` prints one line for each pattern.
   - Presence, test: `git grep -n "CLAUDE.md's" -- claude/.claude/hooks/tests/test_advance_past_commit_stall.py` prints the docstring's first line, ending `CLAUDE.md's` and followed on the next line by `Agent Core`.
   - Presence, record: `git grep -n -e "GH-1094" -e "GH-1093" -e "Tracked in GH-1091" -e "^## Moved lines" -e "Confer with the repo owner" -- docs/design-decisions/global-claude-md-agent-core-and-main-session-groups.md` prints the tracker line (GH-1094 and GH-1093), the "the rules above" item, the new heading and the Rollback bullet.
   - Citation resolves: `git grep -c -F "WebFetch domain allowlisting — considered and rejected" -- docs/security-hardening.md docs/design-decisions/global-claude-md-agent-core-and-main-session-groups.md` prints a nonzero count for both files. The record carries the citation literal, so an ASCII hyphen in place of the em dash fails this check. `test_skills.py` scans the record for citation-shaped text and for hard-wrapped citations but does not resolve them, so this check is the only one that does.
3. **Record diff shape.** Compare against HEAD, not the index, so a staged tree cannot pass vacuously: `git diff HEAD --numstat -- docs/design-decisions/global-claude-md-agent-core-and-main-session-groups.md` prints `6` added and `7` deleted. Then `git diff HEAD -U0 -- docs/design-decisions/global-claude-md-agent-core-and-main-session-groups.md` shows exactly six hunks, at pre-edit :73 (one line replaced, its first two sentences byte-identical to HEAD), :100 (one line replaced), the insert before :114 (two lines added), :121 (one line replaced), :125-128 (four lines deleted) and the insert before :152 (one line added). A hunk anywhere else, including :1-3, :93, :110, :112, :116 or :118, is a defect.
4. **Ignore line.** `git check-ignore -v claude/.claude/output-preferences.md` exits 0 and names the new `.gitignore` line.
5. **No CLAUDE.md change.** Before committing, `git diff HEAD --exit-code -- claude/.claude/CLAUDE.md` exits 0 (against HEAD, so staged edits count). After committing, `git diff --exit-code origin/main...HEAD -- claude/.claude/CLAUDE.md` exits 0.
6. **Lint.** Run `../../../../.venv/bin/shellcheck claude/.claude/hooks/advance-past-commit-stall.sh` and `../../../../.venv/bin/ruff check claude/.claude/hooks/tests/test_advance_past_commit_stall.py`. The path goes four levels up (row 33).
7. **Tests.** From the worktree root, run `../../../../.venv/bin/python3 claude/.claude/scripts/select-tests.py`.
   - Expect stderr to read `running the full suite (unmatched-path: .gitignore)`. The widening is `select-tests.py`'s own (repo CLAUDE.md Commands, case 1), so do not also run the suite by hand.
   - The run takes about four minutes, so give the Bash call `timeout: 600000` or run it in the background.
   - On a `en_US.UTF-8` machine, `claude/.claude/scripts/tests/test_respond_pr_lib.py::TestValidCommentId::test_non_numeric_id_is_invalid[٤٢]` fails, and passes under `LC_ALL=C.UTF-8` (row 34). Run with `LC_ALL=C.UTF-8`, or accept that one test id as the only expected failure. Any other failure counts against this PR until reproduced at the merge-base.
   - A failing `timing`-marked test is re-run alone with `-n0` before it counts as a failure.
   - Issue each Verification bullet as its own single-statement Bash call, because the worktree Bash guard refuses compound calls.
8. **PR body.** "Part of #1085". It must state:
   - Why the record is in scope: the prior plan's row 30 sent this work to a separate PR, and this is it. List the Axis 3 classification from the Approach (edited current-state parts, untouched records), and name the Rollback line as the engineer's request.
   - That no byte of `claude/.claude/CLAUDE.md` changes (Verification 5).
   - A precise description of the :73 fix: the old sentence was literally true about auto mode but implied the repo was silent, and it now cites the recorded finding.
   - Why the hook comment dropped "states this explicitly", and that fork coverage is now marked unverified.
   - Incidental in-file fix: "gate 4 below" now reads "gate 5 below".
   - That the hook comment and the test docstring now depend on the "Agent Core" heading #1090 introduced, so reverting #1090 alone leaves both citing a heading that no longer exists.
   - Deferred items: the prose-hygiene residue (untracked unless the engineer has GH-1094 take it, per Verification 1), the G2 items, the "the rules above" phrase (GH-1091), and B (held, not filed).
   - No CHANGELOG entry, and why (row 32).
   - That the test claim covers the full suite, because `select-tests.py` widened on its own.

## Out of scope

- **CLAUDE.md wording.** These phrases stay as they are:
  - "the rules above" goes to GH-1091.
  - "the section below" and the Durable text scope line are G2.
  - The opening line (:3) and "Merge stays human-only" (:96) wait for the post-merge spot-check.
  - "Safety's marker bullet" is already accepted in the record (:64).
- **B**, the `agent_type`-keyed deny extended to other subagents with a PR-creation arm (record :51), is held and not filed.
- **Prose hygiene in the record** stays unedited and goes to GH-1094: hook-internals detail such as gap (g)'s nested bullet, the :93 "Unverified:" list, repeated residual statements, and the :116 "reworded rather than moved" framing. The :105 re-review trigger watches only `docs/auto-mode.md`, while the finding lives in `docs/security-hardening.md`. That belongs with the same auto-mode `ask` item.
- **`docs/commit-stall-block.md:16` and `docs/hooks.md:162`** treat "not a dispatched subagent" (the first) and "only on the main session (never a subagent)" (the second) as the same thing as an empty `agent_type`, which carries the same unknown about forks as row 6. Neither cites anything stale, so both are raised to the reviewer rather than edited.
- **The `.gitignore` entry's home if the output-preferences `@`-import work moves the file into the stowed tree.** The entry stays correct either way, because it only ignores the path.
- **Record :50's claim that a fork runs in the parent's process identity** is not verified by anything in the repo. It is left untouched because it is part of the analyzed residual.
- **README.md:520's "exactly three levels deep"** is wrong for branch names that contain `/`. README is not scoped here, so this is raised rather than edited.
- **The REASON string at :225** ("this repo's Shipping policy (CLAUDE.md)") still resolves and is left untouched.
