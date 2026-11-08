# Dispatch prompt contract

## Context

Goal: give orchestrator-to-subagent dispatch prompts one authoritative, testable specification, so a hand-written prompt can no longer contradict the target agent's charter.

Ask: GH-1410 ("Subagent dispatch prompts are hand-written per call; a scope sentence contradicted the reviewer's charter"), run as `/plan-it 1410`. The issue asks for the right mechanism for dispatch prompts in general and explicitly rejects a one-off fix for the `comment-discipline-reviewer` row alone. Engineer's scoping answers, selected labels: "Convention + pilot (Recommended)", "Agent body owns it (Recommended)", "Tests on contracts + skill text (Recommended)".

Problem: during #1403's `/ready-for-review` step 3, the orchestrating session hand-wrote four `comment-discipline-reviewer` prompts that listed a `.claude/plans/` file as a prose site. The agent's `## Scope` says a plan file appearing in a diff is out of scope, and the reviewer followed the prompt and raised a wrong finding. `/code-review` hands this reviewer the whole diff, yet its general spawn rule still asks every prompt for a diff scope and a specific question. `/plan-review` instead limits the same reviewer to a closed field list. The orchestrator also hand-copied the declaration block and reworded the prior-decisions and return-format lines.

Why now: the dispatch sites (`/code-review`, `/plan-review`, `/plan-it`, `/ready-for-review`, `plan-architect` modes, `code-writer` rounds) each leave part of the prompt to the orchestrator's judgment, and nothing checks a prompt against the target agent's charter.

Intended outcome: a convention that names, per agent, which prompt fields are accepted and which side wins on a scope conflict, piloted on the two reviewer dispatch sites that caused the bug, with tests that fail when a dispatching skill's prompt spec and the agent's contract disagree.

## Approach

Each dispatched agent's `## Input contract` becomes the one home for what its dispatch prompt may carry. It holds a closed list of fields, each marked derived, free-form or fixed. It also holds shared sentences saying the agent's own file decides what it reviews. The same sentences have the agent name any prompt text it sets aside.

The pilot delivers four things:
- `comment-discipline-reviewer`'s contract (M1).
- `/code-review`'s and `/plan-review`'s dispatch sites naming its field tokens and citing it (M2, M3).
- The convention as `agent-review` checklist item 19 (M4).
- Tests that fail when a site and the contract disagree (M5, M6, built on M8's shared helpers).

A design-decision record holds the runtime cells that check the shared sentences, for a by-hand re-run (M9). The convention is general, and the pilot covers one agent, as the engineer's scope choice directs. Out of scope lists every other dispatch site (rows 1, 2).

The issue's questions, answered:

1. **Fixed, derived and free-form parts.**
   - Derived: a value the dispatcher computes by a procedure its own skill names, such as a path a script prints or the plan file's path. The skill keeps the procedure because only it holds the state, and the contract says what the value means to the agent.
   - Free-form: the dispatcher's own words, allowed only in a field the contract marks free-form. It directs the agent's attention within scope.
   - Fixed: text copied verbatim from one named home in the dispatching skill, such as `/code-review`'s prior-decision reading rules (row10). Text only one agent needs goes in that agent's body instead. Text every reviewer shares stays a fixed field in this change, and follow-up 2 in Out of scope moves it.
2. **What a dispatcher may add.** Only the contract's fields. A dispatching site names the tokens it sends, cites the contract, and keeps each derivation procedure. M6 tests that the tokens a site names equal the set listed for it, and that each is a contract field (row4). Scope sentences, site lists, restated reviewer rules and return-format text are not fields, so none goes in. `/code-review` therefore stops sending `comment-discipline-reviewer` three things beyond its fields (rows 8, 10):
   - the "Spawn per question" paragraph's diff scope and specific question;
   - the declared value and the declaration block;
   - the return budget.
3. **Which side wins.** The agent's own file, in both directions. A prompt changes what the agent reviews only through a field the file defines, as `plan_path:` does. When other prompt text names sites, a scope or an exclusion the file contradicts, the agent follows its file and names the set-aside text on one line before its first finding. That line shows the dispatcher its prompt broke the contract, instead of overriding it silently. In #1403 the prompt widened scope to a plan file the Scope section excludes (row7).
4. **Round-specific context.** The `prior_decisions:` field carries it, in all three kinds:
   - derived: paths to earlier findings files, the ledger digest and the disposition records;
   - free-form: which earlier findings were applied or declined, and the commits that fixed them;
   - fixed: the dispatching skill's rules for reading them.

   `/plan-review` sends the prior findings file and the applied-or-declined account (row9). `/code-review` sends the paths, the account and its reading rules (row10). The field never changes scope, which `code-review/SKILL.md:281` already requires of a record.
5. **The declaration block.** It keeps its one home in `code-review/SKILL.md`, with `ROUTING.md`'s copy held identical by a test (row11). A single physical copy would break the repository's rule against shared partials across skills and reverse the engineer's earlier "Own copy plus parity pin (Recommended)" choice. The pilot stops `/code-review` sending the block to `comment-discipline-reviewer`, because the contract lists no `declared:` field and none of its angles reads a declaration (row22). `/plan-review` already sends it none (row9).
6. **Two layers.** The shared sentences fix the failure at runtime whatever the prompt says, and no static test can read a runtime prompt (G2). The tests keep the specifications from drifting. M9 records the runtime cells, and an edit to the shared sentences re-runs them by hand. CI fails when:
   - a dispatching site (M6) names a field the contract lacks;
   - a site drops a field the contract lists for it;
   - a site drops its exclusion sentence;
   - a site carries a forbidden phrase outside its exclusion sentence;
   - the contract loses a shared sentence (M5).

Alternatives set aside:
- A tighter closed list in the skills alone fails: `/code-review`'s row already said "Hand it the whole diff", and the #1403 prompts still listed sites (row8).
- The shared sentences alone would fix this agent at runtime, but the issue rejects a one-off and asks what a prompt may carry.
- A site carrying only a citation, with no tokens, would make the dispatcher `Read` the agent file on every dispatch, and the test could check nothing at the site but the citation.
- A PreToolUse hook could match tokens but cannot judge whether free-form text contradicts scope, which is the actual failure, and the engineer chose tests (row5).
- A path-scoped `.claude/rules/` file for the convention would load on every SKILL.md read in this repository. `agent-review` already checks agent bodies, and its item 15 is the precedent for a per-contract checklist item.
- Moving the shared fixed text into all ten reviewer bodies now would edit every canary agent, beyond the pilot the engineer chose (rows 1, 2).

Root problem: dispatchers write reviewer prompts by hand, no file says which prompt parts a reviewer accepts, and the reviewer has no rule for a prompt that contradicts its own file. A hand-written site list therefore overrode `comment-discipline-reviewer`'s Scope in #1403.

Givens:
- G1. A skill has no inclusion mechanism, and a dispatched agent gets dispatch-prompt text only through the prompt itself. Reason: the harness's skill and subagent loading impose both (`docs/skills.md:162-164`; `code-review/SKILL.md:281`). Apart from `skills:` preload (`claude-skills/skills/agent-review/SKILL.md:34`), no per-agent text-include mechanism exists. Shared reviewer text therefore lives in each agent body, per `docs/design-decisions/no-shared-skill-partials.md`. Follow-up 2 (Out of scope item 2) decides whether preload replaces the per-body copies.
- G2. A dispatch prompt's free-form text exists only in the runtime Agent call, so no static test can read it. Reason: the orchestrating model composes it per run.

Mechanisms (line numbers are pre-edit):

- **M1: in `claude/.claude/agents/comment-discipline-reviewer.md`, replace the first two sentences of `:20` ("The diff under review arrives as a path..." and "From `/plan-review`, a `plan_path:` line...") with the block below.** anchors: root, row3, row7, row12, row13, row16, row24, row33.
  - The rest of `:20`, from "For a path, `Read` it;", becomes the paragraph after the block, unchanged. It holds all four `DIFF_INPUT_CONTRACT_SHARED_SENTENCES`, so they still match (row33).
  - `:22-23` stay as that paragraph's list.
  - The five sentences of the block's last paragraph are the shared sentences M5 pins. They set aside only contradicting text, so the review bench's untokened prompt still works (row16).
  - The new text must not repeat "Read every changed file fully" (row16). The file gains about eight lines.
  - Lighter primitives checked for the field list: the shared sentences alone leave the issue's question of what a dispatcher may add unanswered (root), and a closed list in the skills alone already failed in #1403 (row8).

```markdown
A dispatcher sends only the fields below, each marked derived, free-form or fixed.

- `diff:` — derived: the diff under review, as a path to a diff file or as literal text.
- `plan_path:` — derived: from `/plan-review`, in place of `diff:`, the absolute path of one plan file to review.
- `findings_path:` — derived: where to write findings, per Output format.
- `prior_decisions:` — on a re-review only:
  - derived: the paths of your earlier findings files, the ledger digest and the disposition records;
  - free-form: which earlier findings were applied or declined, and the commits that fixed them;
  - fixed: any rules the dispatching skill gives for reading them.

This file decides what you review. A prompt changes that only through a field this file defines, and no other prompt text widens or narrows it. Free-form text directs your attention within that scope and never changes it. Follow this file over any prompt text that names sites, a scope or an exclusion this file contradicts. Name the text you set aside on one line before your first finding.
```

- **M2: in `claude-skills/skills/code-review/SKILL.md:298`, the `comment-discipline-reviewer` row, insert the two sentences below after "since it scopes itself to those sites."** anchors: row1, row2, row4, row8, row10, row14, row15, row19, row22, row25.
  - The row stays one line, and every literal pinned on it stays (row14).
  - `diff:` names what the resolution note under the table already derives.
  - M6 pins the second sentence verbatim as this site's exclusion sentence. It names the declared value without backticks, because M6 would count a backticked `declared:` as a sent field.

```markdown
Its prompt carries only `diff:`, `findings_path:` and, when the prior-decisions paragraph above applies, `prior_decisions:`, each as `claude/.claude/agents/comment-discipline-reviewer.md` § "Input contract" defines it. Send it no diff scope, specific question, declared value or declaration block from the "Spawn per question" paragraph, and no return budget from the "Other spawned specialists" paragraph below this table.
```

- **M3: in `claude-skills/skills/plan-review/ROUTING.md`, make two in-place edits.** anchors: row1, row2, row4, row9, row14, row15, row19.
  - `:37`: "so its prompt carries only these:" becomes "so its prompt carries only these fields, each as `claude/.claude/agents/comment-discipline-reviewer.md` § "Input contract" defines it:".
  - `:40`: "on a re-review round, its own prior findings file" becomes "on a re-review round, `prior_decisions:` with its own prior findings file", and the rest of the line stays.

- **M4: append the item below to `claude-skills/skills/agent-review/SKILL.md` § "8. Review checklist", after item 18.** anchors: row3, row4, row5, row19, row23. The item is the convention's one home for authors. `agent-review` checks it wherever it runs on an agent file. Item 15 does the same for the file-based output contract. Like item 15, it names test files and no test identifier, so a renamed identifier cannot strand it. The file goes from 175 to about 182 lines under its 200-line cap (row19).

```markdown
19. **Input contract** (agents whose `## Input contract` leads a bullet with a backticked `token:`) — the agent's `## Input contract` is the one home for what a dispatch prompt may carry:
    - One bullet per field, led by its backticked token and marked derived (computed by a procedure the dispatching skill names), free-form (the dispatcher's own words) or fixed (copied verbatim from one named home in the dispatching skill).
    - Text only this agent needs lives in its body, never in a fixed field.
    - The shared sentences `claude/.claude/hooks/tests/test_agent_roster.py` pins, so the agent's own file decides what it reviews and the agent names prompt text it sets aside.
    - Each dispatching skill site names only these tokens, cites this section, and says how it derives each derived value. The dispatch-site list in `claude-skills/skills/tests/test_skills.py` names every such site.
```

- **M5: in `claude/.claude/hooks/tests/test_agent_roster.py`, add a prompt-contract roster beside the diff-input one.** anchors: row5, row6, row13, row29, row30, row31.
  - Replace `TestDiffInputContractAgents._extract_input_contract_section` (`:230-252`) with M8's `input_contract_section`. The heading assertion at `:258` keeps that class failing on a missing section.
  - Add `PROMPT_CONTRACT_AGENTS = ("comment-discipline-reviewer.md",)` and `PROMPT_CONTRACT_SHARED_SENTENCES`, holding M1's five shared sentences verbatim. A one-line comment above the tuple says a wording change re-runs the cells in `docs/design-decisions/agent-input-contract-owns-dispatch-fields.md` by hand.
  - Add `TestPromptContractAgents`. Its parametrized test, `test_agent_contract_has_fields_and_shared_sentences`, asserts, per roster agent, that `input_contract_section` is non-empty, that `input_contract_field_tokens` finds at least one field, and that every shared sentence is present, whitespace-normalized as `:263-266` does.
  - A missing shared sentence's failure message ends with "Re-run the cells in docs/design-decisions/agent-input-contract-owns-dispatch-fields.md by hand after this edit.", as `test_skills.py:5624` does for its precedent (row31).
  - Its completeness test, `test_prompt_contract_roster_is_complete`, asserts that `prompt_contract_agent_names(AGENTS_DIR)` equals `set(PROMPT_CONTRACT_AGENTS)`. It scans every agent file, not `CANARY_AGENTS`, so `code-writer` and `plan-architect` (follow-ups 4, 5) are covered. An agent with no `## Input contract` heading counts as having no fields through the helper's empty return. `skill-fidelity-reviewer`'s bullets lead with bold names, so it stays out (row13).
  - Lighter primitive checked for the completeness test: a hand-kept roster alone lets a new agent adopt field bullets with no shared sentences and no failure.

- **M6: in `claude-skills/skills/tests/test_skills.py`, next to `test_plan_review_hands_comment_discipline_reviewer_a_plan_path` (`:5594`), add `_PROMPT_CONTRACT_DISPATCH_SITES` and two tests.** anchors: row4, row5, row6, row14, row15, row23, row30, row32.
  - Each site is a `NamedTuple` of a label, the agent file name, a function returning the site text, the tokens the site sends, the site's exclusion sentence, and its forbidden phrases:
    - `/code-review`: the row `_scope_exempt_change_type_row_text` returns. It sends `diff:`, `findings_path:` and `prior_decisions:`. Its exclusion sentence is M2's second sentence, and its forbidden phrases are "declaration block", "specific question" and "return budget".
    - `/plan-review`: `ROUTING.md`'s raw text from "- Always spawn `comment-discipline-reviewer`" through "It keys its findings by angle name, not checklist item.". It sends `plan_path:`, `findings_path:` and `prior_decisions:`. Its exclusion sentence is "It gets no Item ownership routing and no Ledger cross-check instruction." (row32), and its forbidden phrases are "declared:" and "Declaration block".
  - Use functions, not module-level text, because `_ROUTING_MD_PATH` is defined further down the module (`:8643`).
  - The parametrized test is `test_prompt_contract_site_matches_agent_contract`. It asserts, per site:
    - the text carries `` `claude/.claude/agents/<agent file>` § "Input contract" ``;
    - `backticked_field_tokens` of the text equals the site's token set;
    - each of those tokens is a field token of the agent's Input contract, read with `input_contract_section` and `input_contract_field_tokens`;
    - the exclusion sentence appears exactly once;
    - each forbidden phrase occurs as often in the site text as in the exclusion sentence, so a contradicting addition fails while the exclusion sentence may name the phrase. An unchanged baseline site that already carries a forbidden phrase outside its exclusion sentence fails here, and the implementer reports it instead of loosening the check.
  - The tie test is `test_prompt_contract_sites_cover_every_contract_agent`. It asserts that the set of agent files the sites name equals `prompt_contract_agent_names(_AGENTS_DIR)`, so an agent with field bullets and no listed site fails (row30).
  - The existing citation test already checks that the cited heading exists (row15). The tests at `:5549-5610` stay as they are.
  - Lighter primitive checked for the tie test: dropping the identifiers from item 19 alone stops a rename from stranding it, but leaves an agent with field bullets and no listed site unchecked.

- **M7: add the entry below as the first bullet under `CHANGELOG.md`'s `[Unreleased]` / `Changed` heading.** anchors: row1. It follows the file's form for a change that goes live on `git pull`.

```markdown
- **`comment-discipline-reviewer` now lists the fields its dispatch prompt may carry, and follows its own scope when a prompt contradicts it.**
  - Its `## Input contract` marks each field derived, free-form or fixed. When prompt text names sites, a scope or an exclusion its file contradicts, it follows its file and names the text it set aside.
  - `/code-review` and `/plan-review` name its fields and cite that section. `/code-review` no longer sends it a diff scope, a specific question or the declaration block.
  - `/agent-review` checks the same contract shape on any agent that adopts it.
  - The change is live on `git pull`.
```

- **M8: in `claude/.claude/tests/helpers.py`, add one field-token definition and the helpers M5 and M6 share.** anchors: row6, row26, row27, row28.
  - A module constant holds the token pattern `` [a-z][a-z0-9_]*: ``, and every helper below builds on it. It admits the underscore in `plan_path:`, `findings_path:` and `prior_decisions:`.
  - `input_contract_section(agent_path)` is the body of `test_agent_roster.py:230-252`. It returns an empty string when the file has no `## Input contract` line, instead of asserting.
  - `input_contract_field_tokens(section_text)` returns the token of each line matching `` ^- `<token>` ``. The match is anchored at line start, so an indented sub-bullet and a bold lead such as ``- **`findings_path`**`` do not count.
  - `backticked_field_tokens(text)` returns every `` `<token>` `` anywhere in the text.
  - `prompt_contract_agent_names(agents_dir)` returns the names of the `*.md` files in `agents_dir` whose Input contract has a field token. Each caller passes its own existing agents-dir constant.
  - Add `claude/.claude/tests/test_input_contract_helpers.py` with literal-fixture tests, in `test_git_repo_builders.py`'s form (row28):
    - `` - `plan_path:` — derived: ... `` yields `plan_path:`;
    - an indented `` - `derived:` ... `` sub-bullet yields nothing;
    - `` - **`findings_path`** — ... `` yields nothing;
    - `backticked_field_tokens` extracts `prior_decision:` from mid-sentence text;
    - `input_contract_section` returns an empty string for a file with no heading, and stops before the next `## ` heading;
    - `prompt_contract_agent_names` over a temporary directory returns the field-bullet agent and not the bold-lead one.
  - Editing `helpers.py` makes `select-tests.py` run the full suite on this branch (row27).
  - Lighter primitives checked:
    - A new helper module beside `helpers.py` maps only to `claude/.claude/tests` (row27). A later edit to it alone would skip both consuming test directories, which repository `CLAUDE.md` calls a rule-table bug.
    - Importing from `test_agent_roster.py` in `test_skills.py` needs the `sys.path` insertion and lint suppression a scripts test carries for the same import (row28). It also needs a new `select-tests.py` row, because no row selects the skills tests for a hooks-test edit (row27).
    - A regex in each test file is the copied helper test-conventions calls a defect, and the two copies can drift (row26).

- **M9: add `docs/design-decisions/agent-input-contract-owns-dispatch-fields.md`, which the implementing session writes after Verification step 4.** anchors: row21, row24, row31.
  - Its form follows `.claude/rules/design-decisions.md`: an H1, a blank line, then the landing date as an italic line with no `Formerly` clause.
  - It is durable text, so it uses none of this plan's labels, such as mechanism or row numbers.
  - A lead paragraph says a dispatched agent's `## Input contract` owns its prompt's fields. It cites `agent-review` item 19 for the rule rather than restating it.
  - A "Why" section gives the alternatives set aside above that a later reader cannot re-derive: the closed list in the skills alone, the hook, and the citation-only site.
  - A "Measurement note" holds step 4's fixture and prompt shapes, the `cmp` result, and a table of arms with K, bar and outcome. It ends "No test re-runs these cells. An edit to the shared sentences in `comment-discipline-reviewer`'s Input contract re-runs them by hand."
  - A "Sources" list names this plan, the agent's `## Input contract` and item 19.
  - Lighter primitives checked:
    - The PR body alone: M5's failure message is durable text, which `CLAUDE.md` §Durable text bars from depending on a PR description.
    - An accepted-K=1 note in this ledger: an editor rewording the sentences reads M5's failure message, not this plan.

Rows:

1. [engineer-verified: "Convention + pilot (Recommended)"] The change is a convention plus a pilot.
2. [unverified] The session's description behind row 1, not the engineer's words: the pilot covers `/code-review`'s and `/plan-review`'s `comment-discipline-reviewer` dispatch sites, and every other dispatch site becomes a listed follow-up.
3. [engineer-verified: "Agent body owns it (Recommended)"] The contract lives in the agent body.
4. [unverified] The session's description behind row 3: each agent's `## Input contract` names its accepted fields and says the charter wins on a widen-or-narrow conflict, and skills reference it instead of restating it. This plan reads "reference" as a site naming the field tokens it sends plus a citation, with a test holding the tokens to the contract. A token is the field's name, and its meaning stays in the agent body.
5. [engineer-verified: "Tests on contracts + skill text (Recommended)"] Enforcement is tests on the contracts and on the skill text.
6. [unverified] The session's description behind row 5: extend the `test_agent_roster.py` and `test_skills.py` patterns, with no new hook.
7. [verified: `comment-discipline-reviewer.md:18-23`, `:55-57`] With a diff, a `.claude/plans/` file in it is out of scope, and the Input contract states no rule for a prompt that contradicts the Scope section.
8. [verified: `code-review/SKILL.md:35`, `:276`, `:298`] Step 0.6 defines diff scope as each spawn's lane files, and the `comment-discipline-reviewer` row is exempt and says "Hand it the whole diff". `:276` still lists diff scope and a specific question for every spawn.
9. [verified: `plan-review/ROUTING.md:37-42`, `:51-53`] `/plan-review` sends this reviewer a closed list (`plan_path:`, `findings_path:`, re-review extras), and it sends the declaration to domain reviewers only.
10. [verified: `code-review/SKILL.md:276`, `:281`, `:319`] `/code-review` sends every spawn the `declared:` value and block when Step 1 declared one, and on a re-review the prior-decision paths "and these rules". `:319` adds a return budget to specialists' prompts.
11. [verified: `test_skills.py:8957-8967`; `docs/skills.md:164`; `.claude/rules/skill-and-agent-self-review.md`; `.claude/plans/reviewer-declared-surface.md` row 63] The declaration block's home is `code-review/SKILL.md`, and a test holds `ROUTING.md`'s copy identical. That pair is the shape the no-shared-partials rule prescribes, and the engineer chose it on that branch by selecting "Own copy plus parity pin (Recommended)".
12. [verified: `comment-discipline-reviewer.md:297-303`; `test_agent_roster.py:300-308`] The shared File-based output block tells every canary reviewer how to treat prior decisions it is handed, and a test holds that block identical across them.
13. [verified: `test_agent_roster.py:55-74`, `:225-297`; `skill-fidelity-reviewer.md:15-39`] The diff-input roster is the precedent: a tuple of agents, shared sentences checked whitespace-normalized, and a completeness test. `skill-fidelity-reviewer` leads its Input contract bullets with bold names, not backticked tokens.
14. [verified: `test_skills.py:5549`, `:5558-5573`, `:5594-5610`, `:8756-8892`] The edits keep these pinned literals:
    - on the row: "Hand it the whole diff, never a per-spawn file subset", the lane sentence, and "Step 0.6 defers this row out of staged-diff commit-gate rounds.";
    - in Reviewer roles: `plan_path:`, "on every round that reaches Step 5 with a plan file" and "Skip it under `/review-pr`'s standing override";
    - in the agent's Input contract: `plan_path:`;
    - in the Ripple section: each declaration clause, once.
15. [verified: `test_skills.py:3639-3679`; `claude/.claude/scripts/_skill_auxiliary_files.py:26`; `plan-it/SKILL.md:94`] A `` `claude/.claude/agents/<name>.md` § "Heading" `` citation in a SKILL.md or `ROUTING.md` resolves from the repository root under the existing citation test. `plan-it/SKILL.md:94` already cites an agent file this way.
16. [verified: `evals/review_bench/arms.py:42`, `:94-102`; `evals/review_bench/runner.py:128-135`] The review bench copies this agent's body, requires "Read every changed file fully" exactly once in it, and dispatches it with an untokened prose prompt.
17. [verified: `deny-no-op-dispatch.sh:51`, `:64`, `:70`, `:108`, `:127`] A prompt under 600 characters is denied only when it matches a no-op token or phrase, and a field-only prompt matches neither.
18. [verified: Step 3 exploration of `log-reviewer-round.sh:121`, `review_trace.py:144-153` and `review_rounds.py:156`; not reopened here] Other prompt-text consumers read only `plan-architect`'s first line or surface no prompt text, so the pilot changes nothing they read.
19. [verified: `check-skill-length.sh:130-140`; `test_check_skill_length.py:340`] `code-review/SKILL.md` has 533 lines under a 550-line cap, and `agent-review/SKILL.md` has 175 under 200. M2 and M3 add no line.
20. [verified: repository `CLAUDE.md`, "`claude/` is stowed into `$HOME`"; `docs/design-decisions/plan-prose-reviewed-inside-plan-review.md:28`] A normal `comment-discipline-reviewer` dispatch runs the installed copy, which resolves to the main checkout's file, not this worktree's edited body. Verification step 4 therefore runs the edited body through `general-purpose`. It compares the installed copy with the merge-base body before it reads the baseline arm, because the main checkout can sit on any branch.
21. [unverified] Five runs per `general-purpose` arm against 4-of-5 bars give a rate for one fixture shape, the #1403 prompt, and none for other contradicting prompts. The baseline arm runs once and enters no bar.
22. [verified: `comment-discipline-reviewer.md:60-213`] None of the agent's angles reads a declared surface or tier.
23. [unverified] Accepted residual: a second dispatch site for an agent that already has a listed site goes unchecked until someone adds it to `_PROMPT_CONTRACT_DISPATCH_SITES`. M6's tie test catches only an agent with no listed site. M4's last bullet has `agent-review` check the list, and the agent's shared sentences still hold at runtime.
24. [unverified] Accepted residual: no test sees free-form prompt text (G2), so the shared sentences are the only layer that does.
25. [unverified] Accepted residual: `/code-review`'s prior-decision rules still reach this reviewer as fixed text a dispatcher could reword. The shared sentences keep a reworded rule from changing scope, and follow-up 2 removes the relay.
26. [verified: `test-conventions/SKILL.md:115`; `test_agent_roster.py:15`; `test_skills.py:57-69`] Both test files already import from `claude/.claude/tests/helpers.py`. Test-conventions says to define a helper two test files share once, "in a plain module that every consuming test imports from the project's shared test-helper location".
27. [verified: `select-tests.py:461-472`, `:619-634`, `:819-900`] A diff touching `claude/.claude/tests/helpers.py` makes `select-tests.py` select the full suite. Any other file under `claude/.claude/tests/` selects that directory (`:629`) and neither `claude/.claude/hooks/tests` nor `claude-skills/skills/tests`. No row selects `claude-skills/skills/tests` for an edit under `claude/.claude/hooks/tests/`.
28. [verified: `test_git_repo_builders.py:1`, `:8`; `.claude/rules/test-tree-packaging.md`; `pyproject.toml:18`; `claude/.claude/hooks/tests/__init__.py` exists; `test_review_ledger_lib.py:32-35`] `claude/.claude/tests/` is unpackaged and on `pythonpath`, so a contract test for a `helpers.py` function sits beside it and imports `from helpers`, as `test_git_repo_builders.py` does. `claude/.claude/hooks/tests/` is a package and is not on `pythonpath`. A scripts test that imports from it first inserts `claude/.claude/` into `sys.path`, under `# noqa: E402`.
29. [verified: Glob and Grep of `claude/.claude/agents/*.md` and `plugins/*/agents/*.md`] Only `comment-discipline-reviewer.md` and `skill-fidelity-reviewer.md` have a `## Input contract` heading. No agent file has a line matching `` ^- `[a-z_]+:` ``, and no plugin ships an agent.
30. [verified: `select-tests.py:873-880`, `:885`] An agent-file edit selects both `claude/.claude/hooks/tests` and `claude-skills/skills/tests`, so both agent scans re-run on any agent edit. A `docs/` edit selects both too.
31. [verified: `docs/design-decisions/plan-prose-reviewed-inside-plan-review.md:26-43`; `test_skills.py:5623-5626`] The repository's precedent for an agent-behavior check is a design-decision record of K=5 cells with 4-of-5 bars and the line "No test re-runs these cells.". A pinned-sentence test's failure message names it: "Re-run the cells in docs/design-decisions/plan-prose-reviewed-inside-plan-review.md by hand after this edit."
32. [verified: Grep of `test_skills.py` for "no Item ownership routing"] `ROUTING.md:42`'s "It gets no Item ownership routing and no Ledger cross-check instruction." has no test pin today.
33. [verified: `comment-discipline-reviewer.md:20-23`; `test_agent_roster.py:66-74`] All four `DIFF_INPUT_CONTRACT_SHARED_SENTENCES` sit in `:20` from "For a path, `Read` it;" on. `:22-23` hold none of them.
34. [engineer-verified: "Shared helper + its tests"] M8's shared helper and its literal-fixture tests stay in the plan.
35. [engineer-verified: "Design-decision record (M9)"] The M9 design-decision record stays in the plan.
36. [engineer-verified: "5-run behavior cells"] Verification step 4's K=5 control and treatment cells stay in the plan.
37. [engineer-verified: "Sibling-site pin on plan-review"] M6's pin on `ROUTING.md:42` stays in the plan.

## Critical files

- `claude/.claude/agents/comment-discipline-reviewer.md` — M1. Keep "Read every changed file fully" at one occurrence (row16).
- `claude-skills/skills/code-review/SKILL.md` — M2.
- `claude-skills/skills/plan-review/ROUTING.md` — M3.
- `claude-skills/skills/agent-review/SKILL.md` — M4.
- `claude/.claude/hooks/tests/test_agent_roster.py` — M5. Reuse: M8's helpers in place of `_extract_input_contract_section`, and the whitespace normalization at `:263-266`.
- `claude-skills/skills/tests/test_skills.py` — M6. Reuse: `_scope_exempt_change_type_row_text`, `_skill_file`, `_AGENTS_DIR`, `_ROUTING_MD_PATH` and M8's helpers.
- `CHANGELOG.md` — M7.
- `claude/.claude/tests/helpers.py` — M8. Reuse: the section extraction at `test_agent_roster.py:230-252`, moved here.
- `claude/.claude/tests/test_input_contract_helpers.py` (new) — M8's literal-fixture tests, in `test_git_repo_builders.py`'s form.
- `docs/design-decisions/agent-input-contract-owns-dispatch-fields.md` (new) — M9.

Dispatch split: one `code-writer` dispatch carries M1 through M8, with Verification steps 1 to 3 as its verification command. Do not split it: M5 and M6 both call M8's helpers and read M1's contract, so every part rests on one contract a second dispatch would have to restate. The implementing session then runs Verification step 4, because it dispatches agents, and writes M9 from its outcomes.

## Verification

1. From the worktree root, run `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py`. It selects the full suite, because the diff touches `claude/.claude/tests/helpers.py` (row27). Every test must pass, M8's helper tests and the citation test included.
2. Run `scripts/venv-run.sh ruff check claude/.claude/tests/helpers.py claude/.claude/tests/test_input_contract_helpers.py claude/.claude/hooks/tests/test_agent_roster.py claude-skills/skills/tests/test_skills.py`.
3. Run each mutation below with `scripts/venv-run.sh pytest <test file> -k <test name>`, using the test names M5 and M6 give. Confirm each named test fails, then revert it:
   - Delete M1's last shared sentence: the M5 parametrized test fails, and its message names M9's record.
   - Delete M2's second sentence from the `/code-review` row: the M6 parametrized test fails on the `/code-review` site.
   - Keep M2's second sentence and append "Also pass the declaration block." to the `/code-review` row: the M6 parametrized test fails on that site.
   - Delete `ROUTING.md:42`'s sentence "It gets no Item ownership routing and no Ledger cross-check instruction.": the M6 parametrized test fails on the `/plan-review` site.
   - Rename `prior_decisions:` to `prior_decision:` in the `/code-review` row: the M6 parametrized test fails on that site. Revert, then make the same rename in `ROUTING.md`'s `:40` line: it fails on the `/plan-review` site.
   - Remove `prior_decisions:` from `ROUTING.md`'s `:40` line, keeping the rest: the M6 parametrized test fails on the `/plan-review` site.
   - Remove the citation from M3's `:37` edit: the M6 parametrized test fails on the `/plan-review` site.
   - In `skill-fidelity-reviewer.md:19`, replace `- **The skill-invocation list** —` with `` - `skill_list:` — ``: the M5 completeness test and the M6 tie test fail.
   - Append a `## Input contract` heading and the line `` - `task_spec:` — free-form: the task. `` to the end of `code-writer.md`, an agent outside `CANARY_AGENTS`: the M5 completeness test and the M6 tie test fail.
4. Runtime cells, by the implementing session, with fixtures in a scratch directory outside the repository and placeholder names only:
   - Write a diff with two hunks: `.claude/plans/example-plan.md` gaining a three-paragraph rationale block, and `docs/example.md` gaining "this used to be a cron job" framing.
   - The prompt lines are a `diff:` line with the diff's path, plus "Prose sites to review: .claude/plans/example-plan.md and docs/example.md", the #1403 shape. No `findings_path:` goes in, so each run returns inline.
   - Body check: run `git merge-base HEAD origin/main`, then write `git show <sha>:claude/.claude/agents/comment-discipline-reviewer.md` to `base-body.md` in the scratch directory. `cmp` it against the installed copy, `~/.claude/agents/comment-discipline-reviewer.md` on a default install (row20).
   - Baseline, once, only when `cmp` reports no difference: dispatch `comment-discipline-reviewer` with `model: sonnet` and the prompt lines. Record whether it flags the plan file. It shows the bug in the real harness and enters no bar. When the copies differ, skip it and say so in M9.
   - Control, K=5: dispatch `general-purpose` with `model: sonnet`. Tell it to read `base-body.md`, act as that agent using only Read, Grep and Glob, and review with the prompt lines.
   - Treatment, K=5: the same wrapper, naming the worktree's `claude/.claude/agents/comment-discipline-reviewer.md` instead of `base-body.md`.
   - Benign-prompt treatment, K=5: the treatment wrapper with prompts that contradict nothing. Three runs carry only the `diff:` line. Two carry a prompt in the shape of `evals/review_bench/runner.py:128-135`'s `REVIEW_PROMPT_TEMPLATE`, naming the diff file and the changed-files list. It shows the shared sentences do not fire on a prompt they should leave alone (row16).
   - A set-aside line is a line before the first finding that names text the prompt gave, such as its "Prose sites to review" list, as not followed. A bare remark that a plan file is out of scope is not one.
   - Pass needs all four bars below. The set-aside line is the discriminating criterion, because only M1's last sentence can produce it:
     - a set-aside line naming the site list in at least 4 of 5 treatment runs and at most 1 of 5 control runs;
     - a finding on the plan file in at most 1 of 5 treatment runs;
     - a "Used to be X" finding on `docs/example.md` in at least 4 of 5 treatment runs;
     - no set-aside line, and the `docs/example.md` finding, in at least 4 of 5 benign-prompt runs.
   - Record how many control runs flag the plan file. Fewer than 4 of 5 means the plan-file criterion did not tell the bodies apart, so a pass shows the new sentence is obeyed and does not show the bug fixed. M9 says so in those words.
   - A cell that scores 3 of 5 on a bar re-runs once before any edit, because a body that works 9 runs in 10 still misses a 4-of-5 bar about 8 times in 100 per criterion. A cell at 2 of 5 or lower, or a second 3 of 5, sends the fix to M1's last paragraph and M5's pin. Step 3's first mutation and every cell then re-run.
   - Then write M9 from the outcomes and re-run step 1.
5. `/agent-review` on the agent file applies item 19 for the first time, to the pilot agent. `/skill-review` covers `code-review/SKILL.md`, `agent-review/SKILL.md` and `ROUTING.md`. `/code-review` dispatches both.

## Out of scope

- Follow-ups, each a separate change under the same convention:
  1. The eight `staff-*` reviewers and `ciso-reviewer`, as `/code-review` and `/plan-review` dispatch them: field lists, with Step 0.6's lane narrowing, the specific question and `declared:` as named fields.
  2. Shared reviewer text sent as fixed fields: `/code-review`'s prior-decision reading rules (`:281`), the declaration block's reviewer instructions and `ROUTING.md`'s Ledger cross-check instruction move into a test-pinned block in each reviewer body, so prompts carry only paths and values. It first decides whether a `skills:` frontmatter preload (Given G1) replaces the per-reviewer-body blocks. It edits all ten canary agents.
  3. `skill-fidelity-reviewer` at `/ready-for-review` step 4.
  4. `plan-architect`'s `MODE=` dispatches from `/plan-it` Step 5 and `/code-review`'s consult, challenge and fix-plan prompts. `log-reviewer-round.sh` and `review_trace.py` also read that first line.
  5. `code-writer` dispatches from `/ready-for-review` and `subagent-delegation`, and the `respond-pr` and `review-pr` dispatch sites.
  6. A general dispatcher rule in `subagent-delegation` or `CLAUDE.md`'s Agent Briefing, once more than one agent carries a contract.
- A free-form `question:` field for `comment-discipline-reviewer`: its row already fixes the question, and a free-form part is the channel #1403's site list took. A later dispatcher that needs one can add it.
- A hook that validates dispatch prompts: the engineer chose tests (row5).
- Scanning plugin agents, or headings other than `## Input contract`, for field bullets: no plugin ships an agent today, and item 19 names the heading (row29). A plugin agent, or an agent under another heading, that adopts the field shape would escape the roster test.
- Noticed, not changed: `agent-review` item 15 scopes the output-contract check to "`staff-*` and `ciso-reviewer` only", while `test_agent_roster.py`'s `CANARY_AGENTS` also holds `comment-discipline-reviewer` and `skill-fidelity-reviewer`. Raise it to the PR reviewer.
