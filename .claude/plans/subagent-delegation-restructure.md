# Restructure subagent-delegation/SKILL.md and add a per-file line-cap override

## Context

Goal: make `claude-skills/skills/subagent-delegation/SKILL.md` readable by splitting its stacked paragraphs into sub-headings and sub-bullets without cutting load-bearing prose, and add a per-file `limit_for` override so the added lines do not trip the skill-length gate.

Ask: the engineer said "Yes I agree with moving forward with plan-it. I think that should be done in a new session though", agreeing to plan the restructure and cap override the prior session recommended. The scope points (post-return sub-heading, un-stacked bullets, protocol stays in this file, override sized after the draft) are the prior session's recommendation, not the engineer's words. Later answers, by selected label: sub-heading "Add the sub-heading (Recommended)"; docs entry "Yes, add one line (Recommended)"; no-match bullet "Split into sub-bullets (Recommended)"; landing order "Two landings: hook first (Recommended)"; verification size "Shrink it (Recommended)", whose option description (keep only a short wording check, replace `hold` with `git diff > patch` and `git restore`, drop the `hook` subcommand) was the session's proposal; CHANGELOG "Yes, keep the entry (Recommended)".

Why now: the file sits at 201 lines against a 200-line default cap, and recent additions stacked post-return handling and the `review-orchestrator` protocol into long single lines. The file ships through `claude-skills/` to every contributor who runs `./install.sh`, so the change affects every stow consumer.

Intended outcome: a skill whose dispatch-time guidance and post-return handling sit under separate headings, whose long protocol lines are sub-bullets, and whose cap override carries its own stated reason.

## Approach

Restructure `subagent-delegation/SKILL.md` without rewording any of its sentences. Add a ``#### After a `code-writer` return`` heading over the two post-return paragraphs, and split the `review-orchestrator` protocol's intro (:194), Stage step (:197) and step-4 `no-match` bullet (:200) into sub-bullets. Cap that one file at its restructured line count through a new `limit_for` arm, with its tests, one `docs/skills.md` line and one `CHANGELOG.md` entry. A cap raise is needed because the file restates no whole paragraph it could delete and the protocol cannot leave the file.

The work ships as two PRs, cap first, because the commit gate runs the main checkout's copy of the hook, not this worktree's (row 26). Landing 1 carries the cap, its tests, the docs line, the CHANGELOG entry and this plan, and it stages no SKILL.md. It drafts and reviews the restructure only so the engineer can size N from a real count, then discards the draft. Landing 2 re-drafts the restructure from "SKILL.md edits" and commits it, on a branch cut from `main` after landing 1 merges and the main checkout pulls it. Each landing reaches every stow consumer on `git pull` (G5). Landing 1 changes a gate every contributor's commits run, and landing 2 changes a skill every session can load.

A bare `:N` is a line of `claude-skills/skills/subagent-delegation/SKILL.md` as it stands at this plan's base.

**Review of the choices handed over** (rows 1-6, 24, 36, 38, 39):
- Sub-heading (row 1): endorsed. M1 covers its level, its placement and the alternatives.
- One `docs/skills.md` line (row 3): endorsed. `docs/skills.md:169-175` is where the cap policy names each override and why it is allowed. An arm with no entry there would read as the "routine relief for hitting the cap" that `docs/skills.md:172` rejects (M6).
- Protocol stays in this file: endorsed (G2, G3).
- Override sized after the draft, with the number the engineer's call: endorsed. "Cap override" states the floor on N, and M4 recommends how to size it.
- Two landings, hook first (row 24): endorsed, and required, because the live gate denies a single commit holding both (row 26). M7 designs the sequence, how landing 2 gets its draft, and each landing's branch.
- `docs/skills.md:174`'s "not a precedent for raising another skill's cap": this override does not lean on pr-description's. It rests on its own reason (M4, M6).
- Shortening per `docs/skills.md:170-171` instead of a raise: does not suffice, because it frees no line (row 10, M4).
- Splitting the `no-match` bullet (row 6): the engineer's answer settles it, and on this branch it supersedes the prior branch's SETTLED keep row (row 5). M3 designs the split, which keeps the pre-step first.
- Shrinking the verification (row 36): endorsed. M8 keeps only the wording check and drops the hook probe (row 37).
- Dropping the draft hold, the dispatching session's further step (row 39): endorsed. The hold needed its own guards against overwrite, an empty patch and a missing file, and it saved no step, because landing 2 runs `/skill-review` on its own staged draft either way. Landing 1 discards its draft, landing 2 re-drafts from "SKILL.md edits", and the probe's line-count check holds the re-draft to the count N was sized from (M7, M8).
- Keeping the CHANGELOG entry (row 38): the entry stands as "Cap override" states it.

**SKILL.md edits** (dispatch 1, in both landings: landing 1 drafts, reviews and discards them, and landing 2 re-drafts and commits them). Every sentence keeps its words and its order. The edits add only line breaks, list markers, the indentation a nested item needs, the new heading, and one connecting token, `Then:`, at :200 (row 7):
- Above :173, insert ``#### After a `code-writer` return`` and a blank line. The heading covers :173 and :175 and ends at :177's `###` heading (M1).
- :194 becomes four parts, in this order (M2):
  - its first sentence as a lead paragraph, followed by a blank line;
  - three bullets, one sentence each: "Stage the review target ...", "After every dispatch, relay ..." and "A return that lacks ...";
  - a blank line;
  - "Otherwise follow these steps in order:" as its own paragraph above the numbered list.
- :197 becomes step 2's first sentence ("2. Stage: when `Fix paths:` names files, validate every entry before staging any."), then six sub-bullets indented three spaces, in the line's own order (M2):
  - the two pass conditions, as one bullet of two sentences;
  - "Any other entry stops the loop ..." through "which the user stages.";
  - "Then stage every entry ..." through "changes what is staged.";
  - "If that call fails ..." through "partly staged.";
  - "Each entry is a path relative to the repo root ...";
  - "Staging retires any marker ...".
- :200 becomes four lines, in the line's own order (M3):
  - A lead line that keeps its three-space indent and `- ` marker. It holds the Still-uncertain pre-step's three sentences, from "`no-match`: first act on each **Still uncertain** entry ..." through "reaches no later round.", then a space and `Then:`. The pre-step's § citation stays whole on this line.
  - Three sub-bullets indented five spaces, one per `HALT:` case:
    - "With any `HALT:` entry, ..." through "by the skill's own rules.";
    - "With no `HALT:` entry and a fix just staged, ..." through "which reviews the fix.";
    - "With neither, ..." through the line's last sentence, "An uncomputable hash ... reads as no-match."
- Every other line, :196, :198, :199 and :201 included, stays byte-identical.

**Cap override** (dispatch 2, landing 1):
- `claude/.claude/hooks/check-skill-length.sh`: after the pr-description arm (:137-138 of that file) and before `*)`, add three lines in the code-review arm's shape (:132-134 of that file). First the comment `# Hosts the review-orchestrator dispatch protocol, whose one home is this skill.`, then `claude-skills/skills/subagent-delegation/SKILL.md)`, then `echo N ;;`. The header comment (:11-29 of that file) gains nothing (M6).
- N is the engineer's call, with one floor. N must be at least SKILL.md's line count when landing 2 commits. A lower N makes that commit deny (G1), and no in-policy edit shrinks the file back (row 10).
- My recommendation is the count Verification step 1 prints when step 4 re-runs it after `/skill-review`'s last edit to the draft, with no headroom: projected 218 (row 19). The cap then funds exactly this restructure, and any later rule still frees its own lines first. I set headroom aside. Lines reserved for future content are the routine relief `docs/skills.md:172` rejects. pr-description's 3 lines of headroom (row 14) are no precedent, per `docs/skills.md:174` (M4).
- The cost of zero headroom, which the engineer takes on by choosing it: any line a landing-2 review adds to SKILL.md makes landing 2's commit deny. Raising N then takes a further landing, merged and pulled before landing 2 commits, not an edit inside landing 2 (Critical files). Each line of headroom absorbs one such line, at the price above. I expect no such line. `/skill-review` runs on the draft before the checkpoint, and rewording a sentence on a touched line changes no line count (row 27).
- N appears in five places, which must agree: the arm's `echo`, the allow test's staged count, the deny test's staged count of N+1 with its "N+1 lines" assertion, the deny test's "limit N" assertion, and the CHANGELOG entry.
- `claude/.claude/hooks/tests/test_check_skill_length.py` (M5), both new tests built with `make_repo_with_skill_at` and placed after the pr-description pair (:665-712 of that file):
  - `test_subagent_delegation_at_override_cap_allows`: HEAD below N, staged at exactly N, allow.
  - `test_subagent_delegation_over_override_denies`: staged at N+1 and growing. The reason names the path, "N+1 lines" and "limit N".
  - Add `plugins/example-plugin/skills/subagent-delegation/SKILL.md`, `claude-skills/skills/subagent-delegation-example/SKILL.md` and `vendor/claude-skills/skills/subagent-delegation/SKILL.md` to `test_override_caps_do_not_apply_to_sibling_paths` (:386-399 of that file).
  - Dispatch 2 writes the two tests first and runs them before the arm exists. It reports both red: the allow test gets a deny, and the deny test's reason says "limit 200". It then adds the arm and reports both green.
- `docs/skills.md`: one bullet after :175 of that file (M6): "**`subagent-delegation/SKILL.md` carries a per-skill cap above the default** (`limit_for` in `check-skill-length.sh` holds the number). Its gate/review-loop exception section is the `review-orchestrator` dispatch protocol's one home, so that protocol cannot move to a co-located file, and its steps stay lists rather than run-ons."
- `CHANGELOG.md`: a new first bullet under `## [Unreleased]` → `### Changed`, worded as a record of the change (row 28, M6): "**`check-skill-length.sh` gives `subagent-delegation/SKILL.md` a line cap of N, above the 200-line default.** Splitting the skill's run-on `review-orchestrator` protocol lines into lists needed more lines than the default cap left, and the protocol could not move out of the skill. Other skills' caps are unchanged. The change is live on `git pull`."

**Assumption ledger.**

**Root:** `subagent-delegation/SKILL.md` puts its post-return rules (:173, :175) under the dispatch-time `code-writer` guidance with no separating heading. It also packs the `review-orchestrator` protocol's intro (:194) and Stage step (:197) into single lines of five and ten sentences. The file is 201 lines against the 200-line default cap, so any restructure that adds lines needs a per-file cap. `[verified: Read of SKILL.md; check-skill-length.sh:129-142]`

**Givens:**
- G1. The length gate denies a staged SKILL.md that is over its `limit_for` cap and longer than its base version, counting lines with `awk 'END{print NR}'`. Reason: `check-skill-length.sh` and `_lib.sh` own the policy, and changing the metric is a separate decision (Out of scope). `[verified: check-skill-length.sh:129-142, :159; _lib.sh:3098-3101, :3216-3226]`
- G2. The `review-orchestrator` protocol's one home is `subagent-delegation/SKILL.md` § "Exception: gate/review loops stay orchestrator-driven". Reason: `docs/design-decisions/review-orchestrator-gate-release-decoupling.md` owns that decision, and `test_skills.py` pins the section. `[verified: review-orchestrator-gate-release-decoupling.md:21, :32-43; test_skills.py:9917-10023]`
- G3. At a cap, shorten before extracting, delete restated text rather than flatten a list, and never add a co-located runtime file to route around a cap. Reason: `docs/skills.md` § "Skill architecture notes" and `.claude/rules/skill-and-agent-self-review.md` own these rules. `[verified: docs/skills.md:170-174; read of the rule file]`
- G4. Agents run `select-tests.py`, not the full suite. Reason: the repo `CLAUDE.md` § "Commands" owns it. `[verified: CLAUDE.md]`
- G5. `claude-skills/` and `claude/` install to every stow consumer. Reason: the repo `CLAUDE.md` section "Plans in this repo affect all stow users" owns it. `[verified: CLAUDE.md]`
- G6. A commit staging a SKILL.md needs `/skill-review`'s marker, and every commit needs `/code-review`'s. Reason: the hooks named in `.claude/rules/review-pipeline-dispatch.md` own these gates. `[verified: read of the rule file]`

**Rows:**
1. The engineer chose to add the post-return sub-heading. `[engineer-verified: "Add the sub-heading (Recommended)"]` The label covers adding a sub-heading only.
2. The heading's text "After a `code-writer` return", its `####` level and its place above :173 come from the session's option wording and the relayed scope points, not from the engineer's words. `[unverified]` M1 judges them on the merits.
3. The engineer chose to record the override in `docs/skills.md`. `[engineer-verified: "Yes, add one line (Recommended)"]`
4. The remaining scope points are a prior session's recommendation, relayed by the dispatching session: un-stack :194, :197 and :200; keep the protocol in this file; size the override after the draft, with the number the engineer's call. `[unverified]`
5. A prior branch's review ledger holds a SETTLED row on "no-match bullet stacks the Still-uncertain pre-step and three parallel HALT arms in one list item", which is :200. Its rationale: "engineer keep after architect consult: stacking predates the branch, first act fixes the order, split costs 4-5 lines past the length gate". The engineer's answer on this branch (row 6) supersedes that row here. `[verified: Grep of the review-narrative ledger; Read of :199-201]`
6. The engineer chose to split :200 into sub-bullets. `[engineer-verified: "Split into sub-bullets (Recommended)"]` The label covers the split, not its shape (row 23).
7. Every pinned phrase lies inside one sentence. Both extractors collapse whitespace and bound the section at the next `## ` line. Line breaks, list markers, a `####` heading and :200's added `Then:`, all between sentences, therefore leave every pin intact. `[verified: test_skills.py:6226-6236, :9917-10023; claude/.claude/tests/helpers.py:264-280; test_require_review_orchestrator_routing.py:224-234, :286-296]`
8. Citation resolution accepts any ATX heading level, so a `####` heading is citable and leaves existing citations of :135's `###` heading resolving. `[verified: helpers.py:153, :174-191]`
9. Three sites cite § "Implementation work → `code-writer`" for the Still-uncertain rule that moves under the new heading: `respond-pr/SKILL.md:64`, `ready-for-review/SKILL.md:199` and `subagent-delegation/SKILL.md:200`. Five sites cite it for the routing rule and its carve-outs, which stay directly under the `###` heading: `code-review/SKILL.md:331` and `:359`, `plan-it/SKILL.md:181`, `docs/design-decisions/round3-consult-verdict-routing.md:44`, and :129. `[verified: Grep outside .claude/plans/]`
10. No whole paragraph in the file restates a rule stated elsewhere in it. The one partial restatement is :175's second and third sentences, which repeat :129. They share a one-line paragraph with a sentence nothing else states, so deleting them frees no line. The "Stays inline" list (:41-54) parallels the description's DO NOT TRIGGER clause, but that clause is routing text, not the rule's canonical statement. `[verified: Read of SKILL.md]`
11. The file mixes hard-wrapped paragraphs (for example :17-20 and :137-168) with one-line paragraphs (:82, :129, :173, :175, :194-201). `[verified: Read]`
12. No test or validator besides the hook checks a SKILL.md's line count. `[verified: Grep of every test_*.py for line-limit patterns, and of plugins/skill-management/scripts/validate_skill_structure.py; a pattern search, not a proof]`
13. The existing overrides are code-review at 550, plan-review's SKILL.md and ROUTING.md at 500, and pr-description at 250. The header comment describes each one in prose. Only the code-review arm carries an in-arm comment. `[verified: check-skill-length.sh:11-29, :129-142]`
14. `pr-description/SKILL.md` is 247 lines against its 250 cap. `[verified: Grep line count]`
15. `test_check_skill_length.py` holds an allow test and a deny test per override, and a sibling-path parametrize. `make_repo_with_skill_at` and `make_skill_content` build the fixtures. `[verified: test_check_skill_length.py:52-68, :386-418, :665-712]`
16. `docs/hooks.md:166` points at `limit_for` for the per-skill overrides without listing them, so it needs no edit. `[verified: Grep]`
17. `select-tests.py` selects `claude/.claude/hooks/tests` whole for this SKILL.md, which is a member of `SKILL_FILES_READ_BY_HOOK_TESTS` (`select-tests.py:966`, `select-tests.py:368-375`). It selects `claude-skills/skills/tests` whole for any SKILL.md change (`select-tests.py:705`). It maps `CHANGELOG.md` and `.claude/plans/` to no tests (`select-tests.py:710`, `select-tests.py:709`). `[verified: Read of select-tests.py:353, :368-377, :700-711, :955-984]`
18. The design record's :32 reads "The protocol lives in the skill, and the agent description stays a thin shell." `[verified: Read of review-orchestrator-gate-release-decoupling.md:15-43, :99]`
19. The restructure adds about 17 lines and about 130 bytes: 2 for the heading, 6 at :194, 6 at :197 and 3 at :200. That projects 218 lines. `[unverified: projection from the shapes above; Verification step 1 prints the real count, and step 2's probe fails any draft that adds a different number of lines]`
20. Three existing accommodations answer the same gap between line count and body size. `docs/skills.md:171` bars flattening lists to fit. `docs/skills.md:174` gives pr-description a raise because trimming its words frees no line. This plan's override exists because un-flattening costs lines. The shared gate already supports an opt-in byte limit, which `check-claude-md-length.sh` uses. `[verified: docs/skills.md:171, :174; _lib.sh:3118-3125]`
21. No SKILL.md in the repo uses a `####` heading. `claude/.claude/CLAUDE.md` uses two, and this file uses eight bold run-in labels (:38, :41, :56, :68, :73, :143, :150, :159). `[verified: Grep]`
22. :56-66 restate the no-op-dispatch rule of `claude/.claude/CLAUDE.md` § "Agent Briefing". That section sits under "Main session", which subagents do not follow. `[verified: read of the loaded global CLAUDE.md]`
23. The split's shape in M3 is this plan's design, not the engineer's words. The option description shown with row 6's label was the dispatching session's proposal. `[unverified]` M3 judges the shape on the merits.
24. The engineer chose two landings with the hook first. `[engineer-verified: "Two landings: hook first (Recommended)"]` The label covers the number and the order of the landings.
25. What each landing holds, each landing's branch, where the plan file goes and how landing 2 gets its draft come from the dispatching session's option description and this plan's design, not the engineer's words. `[unverified]` M7 judges them on the merits.
26. The commit gate is the stowed hook, not this worktree's copy. `claude/.claude/settings.json:381` runs `~/.claude/hooks/check-skill-length.sh`. That path resolves into the main checkout. The main checkout's copy of `limit_for` has no `subagent-delegation` arm. An arm added here therefore gates nothing until it merges and the main checkout pulls it, as the repo `CLAUDE.md`'s "go live on `git pull`" says. The tests cannot show this, because they run the worktree's copy. `[verified: settings.json:381; Grep of ~/.claude/hooks/check-skill-length.sh finds the pr-description arm at :137 and no subagent-delegation match; `readlink -f ~/.claude/hooks/check-skill-length.sh` resolves into the main checkout; helpers.py:31-34 builds `HOOKS_DIR` from helpers.py's own resolved path, which is this worktree's; repo CLAUDE.md]`
27. Rewording a sentence on a line this restructure touches changes no line count. Each such line holds whole, unwrapped sentences, so only a new sentence line, list item or heading adds a line. `[unverified: inference from Read of :173-201]`
28. The prior per-skill cap changes each added a `CHANGELOG.md` bullet under `## [Unreleased]` → `### Changed`, newest first. Each names the hook, the skill and the numbers, then gives the reason and the effect on other skills (`CHANGELOG.md:139`, `CHANGELOG.md:508`). `.claude/rules/changelog-entries.md` requires an entry to record what changed, not describe current behavior. `[verified: Read of CHANGELOG.md:1-22, :130-143, :502-511; the rule file]`
29. `git restore --source=HEAD --staged --worktree -- <path>` returns both the index entry and the file to HEAD. `git diff --quiet -- <path>` exits 0 only when the working-tree file matches its index entry. `grep -x` matches only whole lines, and `-A1` also prints the line after each match. `[unverified: documented behavior of git and grep, not read or run this session; Verification step 5's `git status --short` shows the first on the real file]`
30. `require-plan-review.sh` arms only when a plan file under `.claude/plans/` is uncommitted or modified. A plan that landing 2's branch inherits unmodified from `main` arms nothing. `[verified: require-plan-review.sh:4-15]`
31. `branch-management` puts a plan file on its implementation branch and bars a plan-only branch that merges apart from its implementation. `/plan-it` Step 7 commits the reviewed plan to the implementation branch before implementation starts. `[verified: branch-management/SKILL.md:166-173; plan-it/SKILL.md:151-154]`
32. Against the base, the planned draft removes no token and adds exactly these whitespace-separated tokens: 12 `-` markers (3 at :194, 6 at :197, 3 at :200), the heading's `####`, `After`, `a`, `` `code-writer` `` and `return`, and `Then:` once. Top-level `- ` lines gain 3, three-space-indented `- ` lines 6, five-space-indented `- ` lines 3, and `#### ` lines 1. `[verified: derived from "SKILL.md edits" against Read of :173, :194, :197 and :200; a token count of a simulated draft against the base gives the same multiset and gains]`
33. `test_check_skill_length.py` is 1,398 lines, and `docs/design-decisions/code-file-line-limit.md:19` sets a 1,000-line limit for test files too. `[verified: Grep line count; Read of code-file-line-limit.md:1-22]`
34. No shipped check enforces that limit yet. `[verified: Grep of every .py file, pyproject.toml, ruff.toml and .github/ for code-file-line-limit, file.length, too.many.lines and max.module.lines (regex, case-insensitive) finds no match; a pattern search, not a proof]`
35. `test_skills.py:9930-9931` reads "Every other rule in the protocol is deliberately unpinned." `[verified: Grep of test_skills.py]`
36. The engineer chose to shrink the verification. `[engineer-verified: "Shrink it (Recommended)"]` The label covers shrinking it, not the shrunk shape (row 37).
37. The shrunk shape comes from the dispatching session's option description and this plan's design, not the engineer's words: the wording check alone and no hook probe. That description's draft hold is gone (row 39). `[unverified]` M8 judges the shape on the merits.
38. The engineer chose to keep the CHANGELOG entry. `[engineer-verified: "Yes, keep the entry (Recommended)"]`
39. Dropping the draft hold goes one step past the shrink the engineer's label "Shrink it (Recommended)" covered. The option description shown with that label kept a hold, through `git diff > patch` and `git restore` (Ask line). Landing 1 now discards its draft, and landing 2 re-drafts from "SKILL.md edits". That is the dispatching session's design, which this plan adopts. `[unverified]` M7 and M8 judge it on the merits.
40. Each `limit_for` case line is indented four spaces, and its `echo` sits on the next line (`check-skill-length.sh:133-138`). A whole-line match on the bare path literal therefore matches no arm, so Verification steps 6 and 7 allow leading whitespace and nothing else before the path. `[verified: Read of check-skill-length.sh:129-142]`

**Mechanisms:**
- M1: a `####` heading above :173, covering :173 and :175. `anchors: row1, row2, row8, row9, row21`. Lighter primitives checked:
  - A bold run-in label is the file's own pattern (row 21) and costs no line. But it marks only its own paragraph, so :175 would still read as part of the review-round paragraph. It also gives no citation a target.
  - A `###` sibling heading would move the Still-uncertain rule out of the section three sites cite for it (row 9).
  - Also set aside: moving the plan-phase return review (:153-157) under the new heading. Those sentences scope to plan-phase dispatches. Under a heading named for every `code-writer` return, they would read as binding review-round fix dispatches too, which changes behavior.
- M2: split :194 and :197 at sentence boundaries into bullets and sub-bullets. `anchors: root, row4, row7`. Set aside: making :194's three sentences new numbered steps, because renumbering breaks :200's "as step 2 stages `Fix paths:`".
- M3: split :200 into a lead line that holds the Still-uncertain pre-step and ends in `Then:`, and three nested sub-bullets, one per `HALT:` case. `anchors: row5, row6, row7, row23`. The nesting keeps the pre-step first, the order the SETTLED row's ordering reason protects (row 5). The last sub-bullet keeps :200's final sentence, because that sentence elaborates the preceding one's "a `check` that degraded to no-match". Set aside:
  - No connecting token. The three cases would hang off the pre-step's last sentence, so they could read as sub-cases of asking the user about entries no fix settles.
  - The pre-step as a fourth sibling sub-bullet. It would read as a fourth alternative rather than the act done first, and its "first" would need a capital, a second token change.
  - Also nesting the pre-step's two dispositions. A second list in one item needs a continuation paragraph between the two lists, which costs lines and a second connecting phrase.
- M4: a per-file cap at the draft's line count. `anchors: root, row10, row11, row12, row13, row14, row19, row27`. A cap raise is heavier than shortening. Lighter primitives checked:
  - Deleting same-file restated text (`docs/skills.md:171`) frees no line (row 10).
  - Extracting the protocol to a co-located runtime file is barred by G2 and G3.
  - Relocating it to a path-scoped rule, `docs/skills.md:170`'s one exception, fails because the protocol is needed when a dispatch returns, not when a file is read.
  - Unwrapping the hard-wrapped paragraphs (row 11) would free lines but shrink only the measurement, not the body. That is the routing-around G3 bars, and it reformats most of the file.
  - Raising the 200-line default changes every skill's cap for one file's need.
- M5: an allow test at N, a deny test at N+1, and three sibling paths. The pair runs red before the arm exists and green after it, which shows the fixtures tell the two states apart. `anchors: row15`.
- M6: the `docs/skills.md` bullet, a one-line in-arm comment and a CHANGELOG entry, with no header prose. `anchors: row3, row13, row16, row28, row38`. Set aside: a header paragraph like the other overrides'. The header's pr-description paragraph (:25-29 of the hook) already restates `docs/skills.md:174`, so a third copy of this arm's reason would add duplication. Landing 2 adds no CHANGELOG entry, because it changes layout only and every sentence keeps its words.
- M7: two landings, cap first. Landing 1 drafts and reviews SKILL.md so the engineer sizes N from a real count, then discards the draft. Landing 2 re-drafts it from "SKILL.md edits" on its own branch, and the probe's line-count check holds the re-draft to the count N was sized from (M8). `anchors: row4, row19, row24, row25, row26, row29, row30, row31, row39`. Two PRs are a heavier coordination pattern than one commit. Lighter primitives checked:
  - One commit holding the cap and the restructure: the live gate runs the main checkout's hook, which lacks the arm, so it denies that commit (row 26).
  - One PR whose SKILL.md commit the engineer makes outside the gated session: workable only as the engineer's own act, and the engineer chose two landings (row 24).
  - Committing the draft on landing 2's branch while landing 1 is in review: the same live gate denies it.
  - Leaving the draft uncommitted in this worktree through landing 1: PR 1's tree stays dirty for its whole review, and the draft is lost if the worktree is removed.
  - Holding the reviewed draft as a patch outside the repo for landing 2 to apply. The patch needs its own guards against overwrite, an empty patch and a missing file. It saves no step, because landing 2 runs `/skill-review` on its own staged draft either way. A re-draft that passes the probe can differ from landing 1's draft only in where an added token or a line break sits, or in whitespace inside a line, and landing 2's `/skill-review` reads those (Out of scope).

  Nothing of landing 1's draft survives the discard, so "SKILL.md edits" is the only carrier of the reviewed shape. An edit landing 1's `/skill-review` makes outside that section goes into it before the discard (Verification step 4).

  Landing 1 carries the plan file. It is the branch `/plan-it` Step 7 commits the plan to, and it holds implementation, so it is not the plan-only branch `branch-management` bars (row 31). Landing 2's PR cites the plan by its path on `main`.
- M8: one probe script, kept outside the repo and run by the parent, holding only the wording check. It compares SKILL.md's blob at HEAD with the working-tree file by token order, added-token multiset, line-start counts and total line count. The line count holds a landing-2 re-draft to the count N was sized from (M7). Nothing else is scripted. Landing 2's commit is its own cap check, because it runs the live gate on the real file (G1, row 26). `anchors: row19, row26, row32, row36, row37, row39`. Set aside:
  - A permanent test: the wording check compares against HEAD, so it means nothing once landing 2 commits.
  - Reading `git diff --word-diff=porcelain` output by eye: it passes on an empty diff, and a removed `--` token prints as `---`, which a prefix-based header skip hides.
  - Parsing `git diff HEAD --word-diff=porcelain` with a positional header skip: workable, but comparing the HEAD blob with the file directly leaves no header to skip and no `~` or context lines to parse.
  - A probe that runs a copy of the hook on the real draft padded to N and N+1 lines. M5's tests already show the arm allows at N and denies at N+1, and landing 2's commit runs the live hook on the real file, so the probe re-tests both. The one fact only it checked, that the arm names this file's real path, Verification step 6 checks with one whole-line `grep`.
  - Checking where each added token sits. It needs sentence-boundary logic for every break, which turns the one-shot wording check into a parser of this restructure. `/skill-review`'s read judges placement in each landing, and the gap is accepted (Out of scope).

## Critical files

**Landing 1** (M7): branch `subagent-delegation-restructure`, this worktree, PR 1. It carries this plan file, which `/plan-it` Step 7 commits here before implementation (row 31). Its commit stages no SKILL.md, so the live gate passes it (row 26).
1. Dispatch 1, `code-writer`, `model: sonnet`: `claude-skills/skills/subagent-delegation/SKILL.md` only, per "SKILL.md edits". It stages nothing and commits nothing. Its return carries Verification step 1's output.
2. The parent runs Verification steps 1-4 on the draft. Step 4 is `/skill-review` on the staged draft.
3. Checkpoint: the session gives the engineer the count step 1 prints when step 4 re-runs it, M4's recommendation and the zero-headroom cost from "Cap override", and takes N.
4. The parent discards the draft with Verification step 5, which leaves SKILL.md at HEAD in this worktree.
5. Dispatch 2, `code-writer`, `model: sonnet`, carrying N: the hook, the test file, `docs/skills.md` and `CHANGELOG.md`, per "Cap override". It stages nothing and commits nothing. Its return carries Verification step 6's red and green runs.
6. The parent runs Verification step 6, then `/code-review`, commits, runs `/ready-for-review` and opens PR 1.

**Landing 2** (M7): a new branch and worktree created per `branch-management` from `origin/main` after PR 1 merges, PR 2. The branch inherits this plan unmodified from `main`, so it adds no plan file and arms no plan-review gate (row 30).
1. The engineer updates the main checkout to a `main` that holds PR 1. The session commits nothing until Verification step 7 prints the arm and N.
2. Dispatch 1 again, `code-writer`, `model: sonnet`: `claude-skills/skills/subagent-delegation/SKILL.md` only, per "SKILL.md edits", against this branch's HEAD. It stages nothing and commits nothing. Its return carries Verification step 1's output.
3. The parent stages the draft with `git add claude-skills/skills/subagent-delegation/SKILL.md` and runs Verification step 8. It then runs `/skill-review` on the staged draft and `/code-review`, and stages SKILL.md again after any edit either review makes. It then runs step 9, commits, runs `/ready-for-review` and opens PR 2. That commit runs the live gate on the real file, which is landing 2's cap check (G1, row 26).
4. PR 2 therefore opens only after its commit and `/ready-for-review`. From PR 1's merge to PR 2's merge, the cap sits at N while `main`'s file is still 201 lines, so any contributor's branch can grow the file by up to N − 201 lines without freeing any. Merge is human-only, so the engineer controls how long that window lasts. Growth that reaches `main` before landing 2's branch is cut sits in the re-draft's base, so steps 8 and 9 count it and the cases below apply. Growth that merges while PR 2 is open is not counted by those steps: the merged file could sit above N, and the gate would deny only a later commit that grows it further (G1).

**When landing 2's count differs from N** (Verification steps 8 and 9):
- Lower, because `main` shrank the file or a review removed a line: the live gate allows the commit. Lowering N to the count is the engineer's call. It would edit the arm and both tests and add a CHANGELOG line inside landing 2, and that commit still passes, because the live cap is the higher N.
- Higher: the live gate denies landing 2's commit, and no edit inside landing 2 can raise the live cap. Either remove the growth, or first land a cap raise as its own PR (the arm, both tests and a CHANGELOG line), merged and pulled before landing 2 commits. SKILL.md is never committed past the gate any other way.

**Modify** (the file limit for both landings):
- `claude-skills/skills/subagent-delegation/SKILL.md` (dispatch 1; drafted and discarded in landing 1, re-drafted and committed in landing 2).
- `claude/.claude/hooks/check-skill-length.sh` (dispatch 2, landing 1).
- `claude/.claude/hooks/tests/test_check_skill_length.py` (dispatch 2, landing 1).
- `docs/skills.md` (dispatch 2, landing 1).
- `CHANGELOG.md` (dispatch 2, landing 1).

**Also in the diff:** `.claude/plans/subagent-delegation-restructure.md`, in landing 1.

**Outside the repo, never committed:** the probe script, in `~/.local/state/claude-probes/subagent-delegation-restructure/`, which each landing saves from the end of Verification (M8).

**Reuse:** `make_repo_with_skill_at`, `make_skill_content`, `run_hook`, `run_hook_reason`, `bash_input` and `CHECK_SKILL_LENGTH_HOOK` from the existing test module. No new helper.

Projected diff: about 63 lines across both landings. Landing 1 holds about 40: 3 in the hook, about 35 in the test file, 1 in `docs/skills.md` and 1 in `CHANGELOG.md`. Landing 2 holds 23 in SKILL.md: 2 heading lines, 1 removed and 7 added at each of :194 and :197, and 1 removed and 4 added at :200.

## Verification

Run each step from the root of the worktree its landing names. Before step 2 first runs in each landing, the parent saves the script at the end of this section, verbatim, as `~/.local/state/claude-probes/subagent-delegation-restructure/probe.py` (M8). The parent runs the probe itself rather than relying on a dispatch's report of it.

Landing 1, on dispatch 1's draft:

1. `awk 'END{print NR}' claude-skills/skills/subagent-delegation/SKILL.md` prints the draft's line count, the count the gate takes (`_lib.sh:3220`). Projected: 218.
2. `python3 ~/.local/state/claude-probes/subagent-delegation-restructure/probe.py` prints `PASS`. It compares SKILL.md's blob at HEAD with the working-tree file, the comparison `git diff HEAD` makes. It therefore gives the same result staged or not, and it has no diff header to skip. It checks tokens, their order and counts, and fails unless all four hold:
   - HEAD's whitespace-separated tokens all appear in the draft, in order, so no token was removed, changed or moved;
   - the draft's added tokens equal row 32's multiset exactly, which an empty diff or a surplus `-` fails;
   - top-level `- ` lines, three-space-indented `- ` lines, five-space-indented `- ` lines and `#### ` lines gain exactly 3, 6, 3 and 1;
   - the draft's line count is exactly HEAD's plus 17 (row 19), projected 218, which holds a landing-2 re-draft to the count N was sized from (M7).

   It does not check where an added token or a line break sits. Where each break falls, that the heading sits directly above :173, and that `Then:` ends the `no-match` lead line are judged by `/skill-review`'s read, in step 4 and in landing 2 (Out of scope). `grep -n 'Then:' claude-skills/skills/subagent-delegation/SKILL.md` prints exactly one line, the `no-match` lead line, ending "reaches no later round. Then:". A `FAIL` means the draft departs from "SKILL.md edits", or that the base changed a line those edits touch. Restore the planned shape, or treat the departure as a plan change.
3. `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py` passes. For this SKILL.md it selects `claude/.claude/hooks/tests` whole (`select-tests.py:966`) and `claude-skills/skills/tests` whole (`select-tests.py:705`). Those directories hold `test_skills.py`'s protocol pins and citation-resolution tests and `test_require_review_orchestrator_routing.py` (row 17).
4. Stage the draft and run `/skill-review` on it, which also judges where each break, the heading and `Then:` sit, and whether `Then:` reads right (M3). After its last edit, re-run steps 1-3. The checkpoint takes the count step 1 then prints. An edit `/skill-review` makes that "SKILL.md edits" does not already describe is a plan change: write it into that section, and into the probe when it changes the probe's counts, before step 5. Landing 2 re-drafts from that section alone (M7).
5. After the checkpoint, `git restore --source=HEAD --staged --worktree -- claude-skills/skills/subagent-delegation/SKILL.md` discards the draft, returning SKILL.md and its index entry to HEAD (row 29). `git status --short` then lists no SKILL.md.

Landing 1, after dispatch 2:

6. Dispatch 2's return shows both new tests red before the arm exists and green after it (M5). Then:
   - Step 3 passes again. It now also selects `claude/.claude/scripts/tests` for the hook change (`select-tests.py:978`).
   - `scripts/venv-run.sh ruff check claude/.claude/hooks/tests/test_check_skill_length.py` exits 0.
   - `scripts/list-shell-files.sh | xargs -0 scripts/venv-run.sh shellcheck` exits 0.
   - N reads the same in all five places "Cap override" lists, and it equals the engineer's choice.
   - `grep -nx -A1 '[[:space:]]*claude-skills/skills/subagent-delegation/SKILL\.md)' claude/.claude/hooks/check-skill-length.sh` prints two lines: the arm's case line, then `echo N ;;` with the engineer's N (row 40). It checks the arm's path against this plan's literal, which the tests cannot do when a test and the arm share a typo. The whole-line match prints nothing for a prefixed path or one joined onto another arm's `|` alternation.
   - Before `/code-review`, `git diff --cached --name-only` lists no SKILL.md.

Landing 2:

7. Before any landing-2 commit, `grep -nx -A1 '[[:space:]]*claude-skills/skills/subagent-delegation/SKILL\.md)' ~/.claude/hooks/check-skill-length.sh` prints the live arm's case line and its `echo` line (row 40). The number that `echo` line prints is N in every later step.
   - When it prints nothing, the main checkout has not pulled PR 1. Stop, tell the engineer, and commit nothing.
   - When that N differs from the cap in PR 1's `CHANGELOG.md` entry, which records the engineer's checkpoint answer, report both numbers to the engineer before landing 2 goes on.
8. On the staged re-draft, steps 1-3 pass, and step 1's count equals N. Otherwise the count cases under Critical files apply.
9. After the last `/skill-review` or `/code-review` edit and before the commit, `git diff --quiet -- claude-skills/skills/subagent-delegation/SKILL.md` exits 0, so the staged blob the gate counts is the file steps 1 and 2 read (row 29). A non-zero exit means an edit is unstaged: stage it and run this step again. Then re-run steps 1-3. Step 1's count must be at most N, or the "Higher" case under Critical files applies. The commit itself is landing 2's cap check. It runs the live `check-skill-length.sh` on the real file, which allows the commit at N lines or fewer and denies it above N (G1, row 26).

The probe script:

```python
"""One-shot wording check for the subagent-delegation restructure plan. Run from a worktree root."""
import collections
import pathlib
import subprocess
import sys

SKILL_PATH = "claude-skills/skills/subagent-delegation/SKILL.md"
EXPECTED_ADDED_TOKENS = collections.Counter(
    {"-": 12, "####": 1, "After": 1, "a": 1, "`code-writer`": 1, "return": 1, "Then:": 1}
)
EXPECTED_LINE_START_GAINS = {"- ": 3, "   - ": 6, "     - ": 3, "#### ": 1}
EXPECTED_ADDED_LINE_COUNT = 17


def line_start_counts(text):
    lines = text.splitlines()
    return {prefix: sum(line.startswith(prefix) for line in lines) for prefix in EXPECTED_LINE_START_GAINS}


def wording_failures():
    base_text = subprocess.run(
        ["git", "show", f"HEAD:{SKILL_PATH}"], check=True, capture_output=True, text=True
    ).stdout
    draft_text = pathlib.Path(SKILL_PATH).read_text()
    failures = []
    remaining_draft_tokens = iter(draft_text.split())
    if not all(token in remaining_draft_tokens for token in base_text.split()):
        failures.append("a HEAD token is missing, changed or out of order")
    added_tokens = collections.Counter(draft_text.split()) - collections.Counter(base_text.split())
    if added_tokens != EXPECTED_ADDED_TOKENS:
        failures.append(f"added tokens are {dict(added_tokens)}")
    base_starts, draft_starts = line_start_counts(base_text), line_start_counts(draft_text)
    gains = {prefix: draft_starts[prefix] - base_starts[prefix] for prefix in EXPECTED_LINE_START_GAINS}
    if gains != EXPECTED_LINE_START_GAINS:
        failures.append(f"line-start gains are {gains}")
    added_line_count = len(draft_text.splitlines()) - len(base_text.splitlines())
    if added_line_count != EXPECTED_ADDED_LINE_COUNT:
        failures.append(f"the draft adds {added_line_count} lines, not {EXPECTED_ADDED_LINE_COUNT}")
    return failures


if __name__ == "__main__":
    wording_failure_list = wording_failures()
    print("\n".join(f"FAIL: {failure}" for failure in wording_failure_list) or "PASS")
    sys.exit(1 if wording_failure_list else 0)
```

## Out of scope

- Retargeting the Still-uncertain citations (`respond-pr/SKILL.md:64`, `ready-for-review/SKILL.md:199`, `subagent-delegation/SKILL.md:200`) to the new heading. The `###` section they cite still contains the rule (row 9). Retargeting only :200 would split three sibling citations, and retargeting all three adds two SKILL.md files to the diff.
- Un-stacking :175 and :201, which the scope points do not name. :201 is one condition and its stop, not a list.
- Deleting :56-66, which restate a `claude/.claude/CLAUDE.md` rule (row 22). `docs/skills.md:171` licenses deleting same-file restatements only. The CLAUDE.md copy sits under "Main session", so the skill's copy is the one a subagent loading this skill follows.
- The hook header's per-override prose (`check-skill-length.sh:11-29`), which restates `docs/skills.md:172-174`. That duplication predates this change, and this change does not extend it (M6).
- Measuring skill length in bytes instead of lines (row 20). In my judgment, the gate's existing opt-in byte limit would make list structure nearly free and flattening pointless. That would remove the need for this override, for `docs/skills.md:171`'s rule and for pr-description's stated reason in one change. But it changes every skill's cap for every stow consumer and needs its own threshold derivation, so it is a separate decision for the engineer.
- Normalizing the file's mixed wrapping (row 11).
- A mechanical check of where each added token or line break sits, an accepted gap. The probe checks HEAD's tokens and their order, the added tokens and the line counts (M8), not placement. A break moved into the middle of a sentence, paired with two bullets merged so the counts hold, passes it, as does the heading placed above :175 instead of :173. `/skill-review`'s read judges placement in each landing, and Verification step 2's `grep -n 'Then:'` prints the one `Then:` line for that read. I accept the gap as I accept the structure-blind pins in the next bullet: closing it needs sentence-boundary logic that would turn the one-shot wording check into a parser of this restructure.
- A level-aware check of the new heading and list structure, an accepted gap. No test sees heading level or list structure, because every extractor collapses whitespace and bounds a section at the next `## ` line (row 7). A later edit that turns the `####` heading into `###` would move the Still-uncertain rule out of the section three sites cite (row 9). One that re-flattens :197 would remove the cap's stated reason. Every test would stay green in both cases. I accept the gap, as `test_skills.py` already leaves every other protocol rule unpinned (row 35). Closing it needs a new level-aware helper, because the `## `-bounded extractors cannot tell the two cases apart.
- A permanent test tying each `limit_for` arm to a real file path. No arm has one, so a mistyped path would leave the real file at the default with every test green. Verification step 6 checks this arm's path once against this plan's literal, and step 7 against the live hook.
- Bringing `test_check_skill_length.py` under the 1,000-line test-file limit. It is 1,398 lines before this change (row 33), and this change adds about 35. Any exception row a later file-length check carries must count that growth (row 34).
