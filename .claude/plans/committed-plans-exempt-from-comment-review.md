# Plan: narrow comment-discipline review of plan files

## Context

Goal: stop `comment-discipline-reviewer` from flagging plan-file prose under rules that assume a reader who has not seen the planning document, while keeping plans readable to a human.

Ask: "yes already-committed plans should be excluded from comment discipline reviewer but unstaged plans are still under discipline because they need to be readable by a human" — clarified by the engineer: "as uncommitted, which would cover both unstaged and staged plans"; "let's go with the narrower reading"; "I like that fourth option as well"; "I agree with the architect's recommendations".

Why now: in the merged scope-anchor PR, the third cumulative `/code-review` pass returned four findings, all in the plan file, all under survives-the-PR, "used to be X", "this session" and run-on rules. A `plan-architect` consult judged §Durable text does not cover plan files, and each proposed fix would have broken `plan-it`'s ledger grammar and re-armed the `plan-review` gate.

Intended outcome: one scope rule in the reviewer's own Scope section, and one test that guards the angle names it cites. No skill is touched.

## Approach

A new paragraph in the Scope section of `comment-discipline-reviewer.md` narrows how the reviewer treats plan files under `.claude/plans/`, a name-resolution test guards the angle names it cites, and a `CHANGELOG.md` bullet records the change for stow consumers. The paragraph sets three rules:
- A plan the diff creates is checked for Multi-fact comment structure only.
- A hunk against a plan that existed before the diff is out of scope.
- A label a plan defines is still PR-defined terminology in any other file.

Multi-fact comment structure is the only angle that judges sentence shape alone (row 9). Readability is the reason the engineer kept uncommitted plans under discipline (row 1). Each of the other angles measures prose against the planning document or the PR narrative, and a plan is that document. The split between created and existing plans follows the engineer's narrower reading (rows 3–4). The reviewer can read that split from the diff header it already receives (G1, G2), and at the gating cumulative pass the split falls exactly where row 4 draws it (row 12).

Alternatives set aside:
- **Filter plans out in `pr-diff-against-base.sh`.** Its output is also the subject of the cumulative-review marker and the diff that every reviewer reads (row 17). Filtering there would hide plans from every reviewer instead of narrowing one.
- **A carve-out in `code-review` Step 0.6 or its Change-type row.** The file has 511 lines against a 500-line cap. The row's left column and the Step 0.6 deferral clause are both pinned by tests (row 16).
- **A carve-out in CLAUDE.md §Durable text.** That file is always loaded for every stow consumer and every agent, which is far wider than one reviewer's scope.
- **A comment-discipline pass inside `/plan-review`.** It adds a review pass, and the accepted recommendations exclude it (row 6).
- **Exempting every plan outright.** It contradicts the engineer's "unstaged plans are still under discipline" (row 1).
- **Keeping every angle for created plans.** The fixes those angles ask for break plan grammar (rows 8, 9).

Known costs:
- A Multi-fact finding on a plan the branch adds costs one more `/plan-review` round (row 11). The angle's first-named fix splits sentences inside one ledger row, so row numbers and anchors stay stable (row 10).
- A diff taken against `HEAD` instead of the merge-base skips a plan committed earlier on the branch (row 14). The gating cumulative pass still sees that plan as created (rows 12–13).
- A pasted diff with no file headers gives the reviewer nothing to tell created from existing (row 15). This is accepted because the gating pass always carries headers (row 12).
- This PR's own cumulative pass runs the reviewer as it was before the change, against this plan (row 18). This plan cites no uncommitted source. The new rule settles any finding that pass returns under an angle the rule drops, so none of them is a reason to edit this plan.

### Assumption ledger

**Root:** `comment-discipline-reviewer` applies angles written for a reader who lacks the planning document to the planning document itself. Its Scope lists durable in-repo docs and names no carve-out for plans. `[verified: claude/.claude/agents/comment-discipline-reviewer.md:23-37, :91-96]`

**Givens:**
- G1. The reviewer judges scope only from the diff text it is handed. It has no `Bash`, so it cannot ask git whether a file exists on the base branch. Reason: its closed-form, no-`Bash` design is a separate recorded decision (`docs/design-decisions/reviewer-persona-roster-operations.md`, cited at `docs/design-decisions/duplicated-evidence-at-plan-review.md:7`), and this plan does not reopen it. `[verified: comment-discipline-reviewer.md:6, :18, :122-127]`
- G2. `git diff` marks a file the diff creates with a `new file mode` header line, followed by `--- /dev/null`. Reason: git's patch format imposes this. `[verified: claude/.claude/hooks/tests/fixtures/gh564-incident.diff:1-4, which is real git output]`

**Rows:**
1. Already-committed plans are excluded, and uncommitted plans stay under discipline so that they remain readable by a human. `[engineer-verified: "yes already-committed plans should be excluded from comment discipline reviewer but unstaged plans are still under discipline because they need to be readable by a human"]`
2. "Unstaged" in row 1 means uncommitted, whether or not the plan is staged. `[engineer-verified: "as uncommitted, which would cover both unstaged and staged plans"]`
3. The engineer chose the narrower of two readings of "committed". `[engineer-verified: "let's go with the narrower reading"]` The quote covers only that choice.
4. The narrower reading is that "committed" means already on the base branch, so a plan the branch adds stays in scope. `[unverified]`: this is the wording of the option the engineer was shown, not the engineer's own words.
5. The engineer accepted the architect consult's recommendations. `[engineer-verified: "I agree with the architect's recommendations"]` The quote covers only the acceptance.
6. The content of those recommendations is as it was relayed to plan authoring, because the consult's return is not a committed file. `[unverified]` The recommendations:
   - A plan the diff adds gets Multi-fact comment structure only.
   - A hunk against an existing plan is out of scope.
   - A label a plan defines stays PR-defined terminology in other files.
   - The rule lives only in the agent's Scope section.
   - `code-review`, `plan-review`, `plan-it`, tests, and `pr-diff-against-base.sh` are not edited.
   - No comment-discipline pass is added to `plan-review`.
7. The merged plan from the scope-anchor PR still carries the prose that the third review pass flagged: 8 hits for "this session", "findings report", or "Unused: kept". `[verified: Grep count on .claude/plans/plan-scope-anchor-rule.md at authoring]`
8. That plan's row 22 exists only to keep later row numbers and anchors stable. The "used to be X" fix, which deletes the row and renumbers, would rewrite every anchor after it. `[verified: .claude/plans/plan-scope-anchor-rule.md:67]`
9. Multi-fact comment structure judges sentence shape alone. Each of the other angles measures prose against context outside the prose:
   - Comment verbosity flags rationale that is "doing the PR description's job" (:47-48).
   - "Used to be X" sends prior-version framing to "the commit message or PR body" (:86-88).
   - The survives-the-PR self-test asks about a reader who never read the "planning doc" (:91-96).
   - PR-defined terminology flags a label that resolves "against an external plan document" (:81-84).
   - Wrong altitude and Restated canonical rule flag what `plan-it` asks a plan to hold: design detail, and the exact rule text its diff adds elsewhere (for example `.claude/plans/plan-scope-anchor-rule.md:95`, `:98`).

   `[verified: comment-discipline-reviewer.md:45-110; plan-it/SKILL.md:108-114]`
10. The Multi-fact angle names "a separate sentence per independent fact" as its first fix, and that fix fits inside one ledger row. `[verified: comment-discipline-reviewer.md:55-58]`
11. Editing a committed plan makes it active. `require-plan-review.sh` then denies the next Write or Edit of any other file until a fresh `/plan-review` passes. `[verified: claude/.claude/hooks/_lib.sh:597-599, :623; claude/.claude/hooks/require-plan-review.sh:4-9, :26-32]`
12. The gating cumulative pass diffs the branch against its merge-base with the base branch. A plan the branch adds therefore carries `new file mode` in that diff even after it is committed on the branch. `[verified: claude/.claude/scripts/pr-diff-against-base.sh:79-84; claude-skills/skills/ready-for-review/SKILL.md:74, :82]`
13. `gh pr create`, and every push once a PR is open, require a fresh cumulative pass at that HEAD. `[verified: docs/design-decisions/comment-discipline-reviewer-deferred-to-cumulative-pass.md:9-14]` The hook itself was not re-read.
14. In a diff taken against `HEAD`, a plan committed earlier on the branch appears as a modification, so the rule skips it in that pass. Which diff a presentation-path review uses is `[unverified]`, because `code-review/SKILL.md:46` names that context without defining its diff.
15. A pasted or ad-hoc diff may carry no file headers. `[verified: code-review/SKILL.md:290 names that fallback]`
16. `code-review/SKILL.md` has 511 lines against a 500-line cap. The left column of its comment/prose row must exact-match `SCOPE_EXEMPT_ROW`, and the Step 0.6 deferral clause is pinned verbatim. `[verified: the file ends at :511; claude/.claude/hooks/check-skill-length.sh:106-109; claude-skills/skills/tests/test_skills.py:4514-4530, :4793-4804]`
17. `pr-diff-against-base.sh --record` records its output as the subject of the cumulative-review marker. `ready-for-review` hands the same diff file to `/code-review` and to every reviewer it spawns. `[verified: pr-diff-against-base.sh:12-15, :84; ready-for-review/SKILL.md:74, :82]`
18. A `comment-discipline-reviewer` dispatched by name loads the stowed copy, which resolves into the main checkout rather than this branch's worktree. `[unverified]`: the root `CLAUDE.md` says `claude/` is stowed into `$HOME` and goes live on `git pull`, but the symlink target was not read.
19. Behavior changes to reviewer agents carry a bullet under `[Unreleased]` → `### Changed`. Both the scratch-execution change for the Bash-holding personas and this reviewer's deferral to the cumulative pass have one. `[verified: CHANGELOG.md:12, :147; git log -- CHANGELOG.md lists recent behavior-change PRs (GH-1110, GH-1094, GH-1099) touching it]`20. Before this change, no test pins the agent's Scope section. The item-12a index test scans only the `## Core review angles` section, and the angle list does not change. `[verified: claude/.claude/hooks/tests/test_design_decision_files.py:536-543; Grep of *.py for "comment-discipline-reviewer" found no Scope pin]`
21. PR-defined terminology accepts a label only when it is "defined in the code or named explicitly there". `[verified: comment-discipline-reviewer.md:74-77]` A committed plan that defines the label could be read as meeting that bar. That reading is an inference.
22. Step 1.5's "Non-durable comment" tripwire covers comments, not doc prose, so `code-review` needs no edit for plans. `[verified: code-review/SKILL.md:78, :141]`
23. `plan-review` has no check that the source of a `[verified: <source>]` row is citable. `[verified: Grep for "citable" across claude-skills/skills/plan-review/ found no match; plan-it/SKILL.md:95 states the requirement]`
24. The CHANGELOG bullet stays in this PR. `[engineer-verified: "Keep the CHANGELOG bullet"]` The quote is the option label the engineer selected, and covers only that choice.
25. The engineer preferred the fourth option, which was the session's proposal to keep plans in scope for readability rules and exempt them from the rules that assume a reader without the planning document. `[engineer-verified: "I like that fourth option as well"]` The quote covers only the preference. The option's wording is the session's, and row 6 narrows it.
26. The engineer wants a name-resolution test added, in `test_design_decision_files.py`. `[engineer-verified: "let’s add the test"]` The quote covers the decision to add it, and it supersedes only the "tests are not edited" item among row 6's relayed recommendations, not row 5's acceptance of the rest. The test's design, its file and its cases are the session's and `staff-sdet`'s proposal, and the `test_design_decision_files.py` idiom it follows is `[unverified]` beyond the session's read of lines 536-680.

**Mechanisms:**
- M1. A Scope paragraph in `comment-discipline-reviewer.md`. The agent is the only reader of its own angle list, so its Scope section is the narrowest surface that changes what it flags. The change is a prose edit to a lazy-loaded agent body. Every alternative home is wider or blocked. `anchors: root, row1, row6, row16, row17`
- M2. `new file mode` as the test for a created plan. The diff text is the reviewer's only evidence (G1). Git puts the marker in that text (G2). At the gating pass, the marker falls where row 4 draws the line. `anchors: row4, row12, row13`
- M3. Multi-fact comment structure only, for a plan the diff creates. It keeps the readability discipline the engineer asked for and drops the angles that measure a plan against itself. `anchors: row1, row2, row9, row10`
- M4. The label sentence. It closes the reading in which a committed plan counts as the label's definition for other files. `anchors: row6, row21`
- M5. A `CHANGELOG.md` bullet, which follows the precedent for reviewer behavior changes. `anchors: row19`
- M6. A name-resolution test in `test_design_decision_files.py`. The existing lockstep test does not see the Scope paragraph, so an angle rename would leave the carve-out naming an angle that no longer exists, with no CI signal. The test checks only that the two cited names resolve to headers and appear in Scope, so it freezes no wording. `anchors: row20, row26`

## Critical files

One `code-writer` dispatch covers files 1 to 3, with Verification step 1 as its check command. The session commits file 4 under `plan-it` Step 7.

1. **`claude/.claude/agents/comment-discipline-reviewer.md`**: insert the text below after `:37`, which ends the "Out of scope: PR descriptions…" paragraph, and before `## Core review angles` at `:39`. Leave a blank line on each side.

   ```markdown
   Plan files under `.claude/plans/` get a narrower scope, because a plan is
   itself the planning document the survives-the-PR self-test assumes its
   reader never saw:

   - A plan the diff creates (its header carries `new file mode`) is in scope
     for Multi-fact comment structure only.
   - A hunk against a plan that existed before the diff is out of scope. That
     plan is already committed.
   - A label a plan defines is still PR-defined terminology in any other file.
   ```

   Insert the block unindented, without the three leading spaces this list item adds. Nothing else in the file changes. The `description:` frontmatter, the angles, How to work, and Output format all stay as they are.
2. **`CHANGELOG.md`**: add a new bullet at the top of `### Changed` under `## [Unreleased]` (`:7`):

   ```markdown
   - **`comment-discipline-reviewer` narrows its scope for plan files under `.claude/plans/`.** A plan the diff creates is checked for Multi-fact comment structure only. A hunk against a plan that existed before the diff is not reviewed. A label a plan defines is still flagged as PR-defined terminology in any other file.
   ```
3. **`claude/.claude/hooks/tests/test_design_decision_files.py`**: add a test that every angle name the new Scope paragraph cites resolves to a header in `## Core review angles`. It freezes no wording. Follow the file's item-12a idiom (`_CORE_REVIEW_ANGLES_SECTION_RE`, `_core_review_angle_headers`, `_item_12a_index_violations`):
   - Add a module constant holding the two cited names, "Multi-fact comment structure" and "PR-defined terminology".
   - Add a pure function taking the agent text. It returns a violation for each cited name that is missing from the parsed angle headers, and for each cited name that is missing from the Scope section, so deleting the reference fails too. Find the Scope section with a regex anchored as `^## Scope$` under `re.MULTILINE`, up to the next `## ` heading. Collapse whitespace in the Scope text before the substring test, so a line reflow that splits a cited name does not fail. Return a violation when the Scope section or the angle headers cannot be parsed, as `_item_12a_index_violations` does.
   - Add one test that runs the function on the real agent file, like `test_item_12a_index_matches_agent_angle_headers`. Its docstring names the drift it guards against: an angle rename that leaves the Scope paragraph citing a name that no longer exists.
   - Add fault-injection tests in `TestFaultInjection`'s style. Each asserts exactly one violation and pins a message fragment naming the angle or section at fault. Four cases: a renamed angle header, a cited name removed from the Scope section, a missing `## Scope` heading, and a missing `## Core review angles` section.
4. **`.claude/plans/committed-plans-exempt-from-comment-review.md`**: this plan.

Reuse:
- The new text refers to the angles by their existing names, Multi-fact comment structure and PR-defined terminology, without restating them. The Multi-fact angle's "Not a violation" carve-outs apply unchanged.
- The discriminator is git's own diff header, so no script changes.

## Verification

1. Before inserting Critical file 1's text, run the new real-file test against the unchanged agent file. It must fail, because the Scope section does not yet cite the two names. Then run the scoped tests from the worktree root: `../../../.venv/bin/python3 claude/.claude/scripts/select-tests.py` (see README.md:545). Expect green. The selected set should include `test_agent_roster.py` and `test_design_decision_files.py`, which now holds the new name-resolution test and its fault-injection cases. That is expected, not checked.
2. Run `/agent-review` on the agent diff, per `.claude/rules/review-pipeline-dispatch.md`.
3. Run a behavioral fixture replay, and keep the fixtures in a `mktemp -d` scratch directory outside the repo (`.claude/rules/skill-and-agent-self-review.md`). This is a one-time manual acceptance check, not a suite member, because an LLM judges the rule.
   1. Write `git diff 3b5910a1^ 3b5910a1` to `<scratch>/real.diff`. It is a real plan created with `new file mode`, plus three `SKILL.md` edits. It is the one input with ground truth for the original bug: the base agent flagged the created plan's prose under the dropped angles.
   2. Build a synthetic fixture in a throwaway `git init` repo. Commit `.claude/plans/existing-plan.md` on `main`, then branch:
      - Add `.claude/plans/new-plan.md` with benign ledger rows in real plan grammar (bracketed citation lists, an `anchors:` line, a parenthetical source), each stating a single fact.
      - Add one run-on row to `new-plan.md` that chains four independent facts through semicolons and parentheticals. It must draw one Multi-fact finding.
      - Add a single-fact "checked this session" row and a single-fact "findings report" citation row to `new-plan.md`, and define the label "Defense A" there.
      - Append the same two bait kinds, plus a line quoting the literal text `new file mode`, to `existing-plan.md`.
      - Define a second label, "Defense B", in `existing-plan.md`.
      - Add `docs/example.md`, which uses "Defense A" and "Defense B" without defining them.
      - Write this state with `git diff main...HEAD` to `<scratch>/fixture-mixed.diff`, so the headers are git's own.
      - Write a second state, where the branch edits only `existing-plan.md`, to `<scratch>/fixture-existing-only.diff`.
      - Delete the throwaway repo once the diff files are written, so the stand-in cannot reach git.
   3. Run the stand-in so that it reaches the answer only through the diff text. Dispatch `general-purpose` with `model: sonnet`, tell it to Read this worktree's `claude/.claude/agents/comment-discipline-reviewer.md` and follow its body, and hand it only the diff file path. Forbid `Bash`, name no repository path, and ask it to report its available tools. The stand-in cannot match the agent's pinned `effort: medium`, which is a known gap.
   4. Judge each site separately, and tolerate incidental findings elsewhere. Run each arm three times. A finding counts as a flag when it names a site in a plan hunk. The agent's sweep of every use of a label reports a count and is not a flag.
      - Must flag, in at least two of three runs: the run-on row in `new-plan.md` under Multi-fact comment structure, and "Defense A" and "Defense B" used in `docs/example.md` under PR-defined terminology.
      - Must not flag, in all three runs: a finding under any angle other than Multi-fact comment structure at a plan site. That covers the "this session" row, the "findings report" citation, the "Defense A" definition line, anything in the `existing-plan.md` hunks, and the quoted `new file mode` line. A Multi-fact finding on a benign ledger row is a known cost of the retained angle, so record it without failing the check.
      - `fixture-existing-only.diff` must return zero findings and the closing **No comment-discipline concerns** verdict.
      - On `real.diff`, the edited arm must return no finding under a dropped angle at a site in the created plan.
   5. Run the control arm the same way against the base-branch agent text, on `fixture-mixed.diff` and on `real.diff`. Save the text with `git show origin/main:claude/.claude/agents/comment-discipline-reviewer.md` into `<scratch>/agent-base.md`. On `fixture-mixed.diff` the control must flag the "this session" row and a line in the `existing-plan.md` hunk in at least two of three runs. On `real.diff` it must flag at least one site in the created plan under a dropped angle. If it does not, that input does not discriminate: strengthen the bait or drop the arm, and rerun.
   6. Name two known limits in the PR body. A plan created by `git mv` appears with `rename from` and no `new file mode`, so it reads as pre-existing. The stand-in's effort differs from the agent's pin.

   Do not dispatch by agent type here, because a named dispatch loads the stowed copy rather than the edit (row 18).
4. Check that `git diff --stat origin/main...HEAD` names exactly the four Critical files.

## Out of scope

- **`/plan-review` enforcement that the source of a `[verified: <source>]` row is citable** (`plan-it/SKILL.md:95`). No such check exists today (row 23). Adding one is a separate change.
- **The other findings from the scope-anchor PR's third review pass, and the replacement for #1035.** Both are separate work.
- **`code-review/SKILL.md`, `plan-review`, `plan-it`, `pr-diff-against-base.sh`, and every test file except `test_design_decision_files.py`.** The first two are blocked or pinned (rows 16, 17). The rest need no edit (rows 20, 22). The accepted recommendations exclude all of them (row 6), and the engineer's later decision adds only the one test (row 26).
- **CLAUDE.md §Durable text.** A carve-out there would change the rule for every agent and every stow consumer.
- **The agent's `description:` frontmatter.** `/code-review` dispatches the agent by name from its Change-type row (`code-review/SKILL.md:285`), so the carve-out needs no routing change.
- **A verbatim test pin on the new Scope text.** An LLM judges the rule, so a text pin would freeze the wording without testing behavior. Verification step 3 tests the behavior.
- **A behavioral CI test of the reviewer's verdicts.** Every method is a probabilistic model classification, and a single-sample pass or fail gives a flaky signal. Verification step 3 is a one-time manual check instead. The name-resolution test in Critical files 3 is the one deterministic layer.
- **A design-decision file.** The Scope paragraph states its own reason, and this plan carries the ledger.
- **Defined handling for a diff with no file headers.** It is never the gating pass (rows 12, 15).
- **Retroactive edits to plans already on the base branch**, including `.claude/plans/plan-scope-anchor-rule.md`. The new rule puts their hunks out of scope.
