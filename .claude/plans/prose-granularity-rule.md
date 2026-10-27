# Prose granularity rule

## Context

Goal: stop review-round churn on docstrings, comments, and durable docs that are written at the wrong granularity, meaning they restate what the code already says, or feature docs that drift into implementation detail.

Ask: "Docstrings are only supposed to explain what is non-obvious from the code, and docs about features should be higher level." / "agents ask me about whether or not to fix inaccuracies in the prose without realizing - which requires my direct input - that the prose is written at the wrong level of granularity in the first place." The session drafted a three-part fix: a granularity rule in CLAUDE.md §Durable text applied at authoring time, an existence check by reviewers before raising accuracy findings on prose, and a delete-or-raise-altitude verdict on code-review's contradiction route. It was accepted via "yep, start that please". Widened: "Also there should probably be prose for the code writer to indicate this rule as well". Supplied stress-test data: "You may want to use this as data to stress test the heuristics your PR adds and/or use as fixtures". Narrowed after review: "Your plan seems very scattered if I'm going to be honest. I think you understand what we're trying to accomplish, but you have a list of guidelines that feels like compounding defensive engineering." The engineer accepted an architect consult's smaller design ("Yeah, this sounds good."), with revisions: "I think what you mean to say is if it's non-obvious"; "What is behavior at its reader's level? I think you need to explain that to the agent."; for the code-writer bullet, "Don't restate what can be gleaned from the code"; on a published-API exception, "I'm not so certain about the public API one because that's so rare, and when it happens, that can just be another pass of adding doc strings for public API documentation." / "it's easy to add it afterwards in the cases where something is a public API, and it's hard for the agent to tell whether that's the case, and it's disruptive to ask every single time." / "I actually think it should be removed entirely"; and "that plus the research that you did, it needs to be stored in some references file".

Why now: in later review rounds, prose that restates the code drifts every time the code changes. Each drift becomes an accuracy finding, and each finding is posed to the engineer as fix-or-keep. Neither option removes the prose, so it keeps drifting. A local audit of the session corpus confirmed this pattern. Its figures are not reproduced here because the corpus spans private projects. Nothing in `claude/.claude/CLAUDE.md` §Durable text, `code-writer`, `comment-discipline-reviewer`, or `/code-review` currently says that prose restating the code should not exist. The contradiction route's consult can only return *Keep current text*, *Apply this round's fix*, or *Cannot choose*.

Intended outcome: prose that restates the code is not written in the first place. When a reviewer finds it, the fix the session relays is deletion, not a corrected rewrite.

Counter-evidence the design must respect: many accuracy findings on prose are real user-facing doc errors where correction is right. In one branch, deletion caused its own churn as later reviewers flagged the removal. The rule must target restatement of code, not prose in general.

## Approach

Make prose that restates the code a named defect with one home: a new **Don't restate the code** bullet in `claude/.claude/CLAUDE.md` §Durable text, whose remedy is deletion. `/code-review`'s per-finding suggested fix, `comment-discipline-reviewer`'s altitude angle and `code-writer`'s authoring baseline point at that bullet. A design record keeps the reasons and the primary-source research behind it.

**Changes.** Exact text is given where a byte cap or a test makes the wording load-bearing.

- **M1: `claude/.claude/CLAUDE.md` §Durable text.**
  - In the "Where to put it" bullet (line 107), "the fix is relocation, not deletion." becomes "the fix is relocation, not deletion, unless the prose restates the code."
  - Add this bullet directly above the **One line, not a paragraph.** bullet (line 116), in ASCII punctuation:
    > - **Don't restate the code.** A comment or docstring states only what is non-obvious from the code: what its name, signature, branches, types and tests show is obvious. A feature doc states what it does for its user and how to use it, not how it works. Delete a restatement: a shorter or corrected restatement still restates.
  - By hand count, the two edits add 362 bytes and one line (row 22).
- **M2: `claude-skills/skills/code-review/SKILL.md`**, with zero net lines.
  - Output format item 5 (line 245) becomes:
    > 5. **Suggested fix** (concrete, not "consider improving"; for a finding about prose, the replacement text itself, adding no fact the finding does not require). For prose that restates the code, per CLAUDE.md §Durable text, the fix names the lines to delete.
  - Item 12a's altitude bullet (line 136) becomes "Prose at the wrong altitude — for its reader, or restating the code".
- **M3: `claude/.claude/agents/comment-discipline-reviewer.md`.**
  - The altitude angle's paragraph (lines 127-131) becomes:
    > **Prose at the wrong altitude** — content placed where its reader doesn't match: a feature deep-dive inside a README overview, implementation detail inside an agent spec meant to stay lazy-loaded and terse, a doc back-reference inside a skill body, a docstring or comment restating what the code shows. The fix is relocation — name where the content belongs. For content that restates the code, per CLAUDE.md §Durable text, the fix is deletion.
  - Inline output item 5, "Concrete fix", lists "the deletion" after "the relocation target".
  - Nothing else in the file changes, so the angle count, the frontmatter description and every angle header stay as they are.
- **M4: `claude/.claude/agents/code-writer.md`.** Add this sub-bullet to the Implementation baseline's "As you write" list, after the PR-narration sub-bullet (lines 80-81):
  > - Don't restate what the code already shows: its name, signature, branches, exception and return types, and what its tests pin (§Durable text's "Don't restate the code" bullet). Such prose only drifts. What earns a comment or docstring is what the code can't carry: why this approach, an invariant another site relies on, or an external fact such as a vendor quirk, a spec clause, or a regression's issue.
- **M5: `claude/.claude/hooks/tests/test_global_claude_md_groups.py`.** Add `("dont-restate-the-code", "**Don't restate the code.**", "core"),` to `_PLACEMENTS`, after the `durable-text-rules` row (line 89).
- **M6: `docs/design-decisions/prose-restating-code-is-deleted.md`**, created per `.claude/rules/design-decisions.md` with an H1, a blank line, and an italic date line with no `Formerly` clause. It cites `claude/.claude/CLAUDE.md` § "Durable text" and `claude-skills/skills/code-review/SKILL.md` § "Output format" for the rule and its remedy, restates neither, and records why:
  - A corrected or shorter restatement still restates, so it drifts at the next code change and draws the next accuracy finding.
  - Item 5 carries the remedy, so every route that applies a fix applies the deletion, and the contradiction route needs no verdict of its own.
  - The rule has no published-API exception. Published APIs are rare. When one exists, a human who knows it is published can direct a docstring pass afterwards. An agent can't reliably tell whether code is published, and asking every time is disruptive. PEP 8, Go and Google give every public name a docstring, and Google and Rust document a public API's errors. This rule departs from them by design.
  - A `## Sources` section lists the primary sources below. Each quote came through WebFetch and is near-verbatim, so Verification step 6 re-fetches it before commit. The record names nothing from the engineer's private example (G2).
    - PEP 8, https://peps.python.org/pep-0008/: "Comments that contradict the code are worse than no comments."; "Inline comments are unnecessary and in fact distracting if they state the obvious."; "Write docstrings for all public modules, functions, classes, and methods."
    - PEP 257, https://peps.python.org/pep-0257/: the one-line docstring "should NOT be a 'signature' reiterating the function/method parameters".
    - Google Python Style Guide, https://google.github.io/styleguide/pyguide.html: "never describe the code. Assume the person reading the code knows Python (though not what you're trying to do) better than you do."; a docstring is "mandatory for every function that has one or more of: being part of the public API, nontrivial size, non-obvious logic"; Args, Returns and Raises "can be omitted in cases where the function's name and signature are informative enough"; Raises should "list all exceptions that are relevant to the interface".
    - Google engineering practices, https://google.github.io/eng-practices/review/reviewer/looking-for.html: "Usually comments are useful when they explain why some code exists, and should not be explaining what some code is doing."; "mostly comments are for information that the code itself can't possibly contain, like the reasoning behind a decision."; "regular expressions and complex algorithms often benefit greatly from comments that explain what they're doing."
    - Go, https://go.dev/doc/comment: "Every exported (capitalized) name should have a doc comment."; "Doc comments should not explain internal details such as the algorithm used in the current implementation."
    - Rust API guidelines, https://rust-lang.github.io/api-guidelines/documentation.html: a public function's errors and panics are documented in its Errors and Panics sections. This entry is a description, not a quote.
    - Diátaxis, https://diataxis.fr/how-to-guides/: "How-to guides must be written from the perspective of the user, not of the machinery."; how-to guides "are about goals, projects and problems, not about tools."
- **M7: `CHANGELOG.md`.** Add one `[Unreleased]` → `### Changed` bullet naming the new §Durable text rule and its deletion remedy, item 5's deletion fix shape, the pointers in `comment-discipline-reviewer` and `code-writer`, and that the change is live on `git pull`.

**Alternatives set aside.** Out of scope lists the earlier draft's cut layers, each with its reason.

- *A ninth `comment-discipline-reviewer` angle for restatement.* The altitude angle already owns where content belongs. A new angle would also change item 12a's lockstep list and the description's "eight core review angles" (row 20).
- *The rule in `code-writer` alone.* The main session, the reviewers and the `plan-architect` consult also write or propose prose, and Agent Core reaches all of them (row 15).
- *A copy of the check in each reviewer agent.* Item 5 is the one suggested-fix shape every finding passes through, so one edit covers every reviewer (row 17).

### Assumption ledger

**Root:** Prose that restates the code drifts each time the code changes. Each drift becomes an accuracy finding whose suggested fix is a corrected rewrite, posed to the engineer as fix-or-keep. No rule names deletion as the remedy, so the prose survives every round and drifts again.

**Givens:**

- **G1: the global `CLAUDE.md` byte cap stays 30720.** `check-claude-md-length.sh` denies a commit that grows the file past it. Every stow consumer loads the file in every session, so raising the cap is a decision outside this plan. `[verified: claude/.claude/hooks/check-claude-md-length.sh:59, :131]`
- **G2: no private material enters the PR.** The repo is public, and its redaction rules bar the engineer's private example, so every case this plan carries is synthetic. `[verified: CLAUDE.md § "Redact private-project-identifying content"]`

**Rows:**

1. `[engineer-verified: "Docstrings are only supposed to explain what is non-obvious from the code, and docs about features should be higher level."]` The rule covers comments and docstrings, and separately feature docs. `anchors: M1`
2. `[engineer-verified: "that the prose is written at the wrong level of granularity in the first place"]` Agents pose accuracy fixes on prose whose real defect is its granularity. `anchors: root, M2`
3. `[engineer-verified: "you have a list of guidelines that feels like compounding defensive engineering"]` The design narrows to one canonical rule and its pointers. Where this plan departs from the three-part draft accepted via "yep, start that please", that draft is superseded. `anchors: root, out-of-scope`
4. `[engineer-verified: "Yeah, this sounds good."]` The engineer accepted the architect consult's smaller design. `anchors: M1, M2, M3, M4, M5, M6, M7`
5. `[unverified]` That design's content is the consult's, relayed in the session's summary, and the engineer accepted it whole rather than line by line. Two wordings here differ from the consult's. Item 5's deletion clause is a second sentence, not a parenthetical nested in the existing one. The reviewer's altitude paragraph states the relocation instruction before the deletion exception, not split by it. Both keep the meaning and avoid the Multi-fact and Paragraph readability shapes that agent flags. `anchors: M2, M3`
6. `[engineer-verified: "I think what you mean to say is if it's non-obvious"]` The bullet keeps only what is non-obvious from the code. `anchors: M1`
7. `[engineer-verified: "What is behavior at its reader's level? I think you need to explain that to the agent."]` The bullet spells out a feature doc's level instead of naming it. `anchors: M1`
8. `[unverified]` The spelled-out level, "what it does for its user and how to use it, not how it works", is the consult's wording. It also carries the guard Context's counter-evidence asks for: a README usage line states how to use something, so an accuracy finding on it is corrected, not deleted. `anchors: M1, M3`
9. `[engineer-verified: "Also there should probably be prose for the code writer to indicate this rule as well"]` `code-writer` gets its own bullet naming the rule. `anchors: M4`
10. `[engineer-verified: "Don't restate what can be gleaned from the code"]` That bullet leads with the instruction not to restate what the code shows. `anchors: M4`
11. `[engineer-verified: "I actually think it should be removed entirely"]` No surface carries a published-API exception. `anchors: M1, out-of-scope`
12. `[engineer-verified: "that's so rare, and when it happens, that can just be another pass of adding doc strings for public API documentation." / "it's hard for the agent to tell whether that's the case, and it's disruptive to ask every single time."]` The design record gives four reasons for that exclusion. Published APIs are rare. A human-directed docstring pass adds their docs easily. An agent can't tell what is public. Asking each time is disruptive. `anchors: M6`
13. `[engineer-verified: "that plus the research that you did, it needs to be stored in some references file"]` The exclusion's reasons and the primary-source research go in a committed file. `anchors: M6`
14. `[verified: Grep of docs/rules-references.md headings; Grep "^## Sources" across docs/design-decisions/; docs/design-decisions/prose-rules-promoted-to-global-claude-md.md:17]` `docs/rules-references.md` holds one section per rule file and none for the global `CLAUDE.md`. 68 decision records carry a `## Sources` section, including the record for the last prose rules promoted into the global `CLAUDE.md`. `anchors: M6`
15. `[verified: claude/.claude/CLAUDE.md:87-117; claude/.claude/hooks/tests/test_global_claude_md_groups.py:308; claude-skills/skills/plan-it/SKILL.md:57]` §Durable text sits in Agent Core. Every subagent but `Explore` and `Plan` loads `CLAUDE.md`, so `code-writer`, the reviewers and the `plan-architect` consult all see M1. `anchors: M1, out-of-scope`
16. `[verified: Grep for "relocation, not deletion" outside .claude/plans/]` The phrase appears only at `claude/.claude/CLAUDE.md:107` and `claude/.claude/agents/comment-discipline-reviewer.md:130`. `anchors: M1, M3`
17. `[verified: claude-skills/skills/code-review/SKILL.md:239-245, :344, :374]` Item 5 shapes every finding's suggested fix. The fix route dispatches ADDRESS rows to `code-writer`, and the contradiction route's *apply this round's fix* is an ordinary ADDRESS row on that route. Once item 5 names a deletion, every route that applies a fix applies the deletion. `anchors: M2, out-of-scope`
18. `[verified: claude-skills/skills/code-review/SKILL.md:371]` The consult lets the current text win unless the finding names a defect, under a stated rule, that the current text has. A finding asking to restore a deleted restatement names none, so it resolves *keep current text*. `anchors: M2, out-of-scope`
19. `[verified: claude-skills/skills/code-review/SKILL.md:130]` Item 12 already allows removing a comment that "restates WHAT the code does", so an M2 deletion draws no stripped-WHY finding. `anchors: M2`
20. `[verified: claude/.claude/hooks/tests/test_design_decision_files.py:551, :560, :606, :617, :790, :958]` Item 12a's bullet names, read before " — ", must equal the agent's angle headers in order. The paragraph-readability fixture hashes only that angle's block, which ends at the altitude header. Text after a " — " and inside the altitude paragraph leaves both unchanged. `anchors: M2, M3`
21. `[verified: claude/.claude/hooks/tests/test_global_claude_md_groups.py:49-53, :89]` `_PLACEMENTS` pins a core rule by a fragment that must occur on exactly one line above `# Main session`. §Durable text's only row pins "**No PR-defined terminology**". `anchors: M5`
22. `[verified: wc -c and wc -l on claude/.claude/CLAUDE.md; wc -c on the bullet text]` `claude/.claude/CLAUDE.md` is 30314 bytes and 179 lines. M1 adds 362 bytes, the 325-character bullet plus its newline and the 36-byte clause, and one line. That makes 30676 bytes, 44 under G1. Verification step 3 measures it. `anchors: M1`
23. `[verified: CHANGELOG.md:5-13]` Consumer-visible changes get an `[Unreleased]` → `### Changed` bullet, and entries say when the change goes live. `anchors: M7`
24. `[verified: docs/cost-levers-considered.md:740, :751]` The deferred lever is a `code-writer` rule to prefer deleting or rewording prose over adding it in review fixes, and its suggested-fix-wording form already lives in item 5. M4 is an authoring rule on restating prose only, so "The `code-writer` form stays deferred" stays true. `anchors: out-of-scope`
25. `[verified: docs/design-decisions/global-claude-md-agent-core-and-main-session-groups.md:100-120]` Any Agent Core change reopens that record's residuals. Every residual there concerns fork shipping, settings-edit gating or hook mechanics, and M1 touches none. `anchors: out-of-scope`
26. `[engineer-verified: "You may want to use this as data to stress test the heuristics your PR adds and/or use as fixtures"]` The engineer offered a private example as test data. Under G2 it is neither reproduced nor committed, so every Verification case is synthetic. `anchors: out-of-scope`

**Mechanisms:**

- **M1: the §Durable text bullet and the altitude exception.** This is the one home every other surface cites. `anchors: root, row1, row6, row7, row8, row11, row15, row16, row22`
- **M2: `/code-review` item 5 and item 12a.** Item 5 is the one suggested-fix shape every finding passes through, and item 12a puts restatement on the per-round checklist. `anchors: root, row2, row17, row18, row19, row20`
- **M3: `comment-discipline-reviewer`'s altitude paragraph and output item.** It reconciles the second "relocation, not deletion" site with M1. `anchors: row8, row16, row20`
- **M4: `code-writer`'s baseline sub-bullet.** `anchors: row9, row10`
- **M5: the `_PLACEMENTS` row.** It fails the suite if the bullet's label is renamed, duplicated, or moved below `# Main session`. `anchors: row21`
- **M6: the design record.** A new file is heavier than a citation, so three lighter homes were checked. The PR body lives outside the repo, and row 13 asks for a stored file. `docs/rules-references.md` holds one section per rule file (row 14). A references file beside `claude/.claude/CLAUDE.md` would stow into every consumer's config directory, and no such file exists. `anchors: row12, row13, row14`
- **M7: the `CHANGELOG.md` bullet.** `anchors: row23`

## Critical files

One `code-writer` dispatch carries every edit. Every edit points at the same M1 bullet, so a split would restate that background in each prompt, which `plan-it/SKILL.md` § "Step 5 — Architecture design" rules out. The dispatch runs Verification steps 2-3; the session runs step 1 after it returns.

- **Modify** `claude/.claude/CLAUDE.md`: M1.
- **Modify** `claude-skills/skills/code-review/SKILL.md`: M2.
- **Modify** `claude/.claude/agents/comment-discipline-reviewer.md`: M3.
- **Modify** `claude/.claude/agents/code-writer.md`: M4.
- **Modify** `claude/.claude/hooks/tests/test_global_claude_md_groups.py`: M5.
- **Create** `docs/design-decisions/prose-restating-code-is-deleted.md`: M6.
- **Modify** `CHANGELOG.md`: M7.

Reuse: item 12's existing restatement test, the existing `_PLACEMENTS` table, and the `## Sources` form of `docs/design-decisions/prose-rules-promoted-to-global-claude-md.md`. Nothing adds a script, a verdict, a ledger disposition or a config key.

## Verification

1. Run `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py`. Its selection must include and pass `claude/.claude/hooks/tests/test_global_claude_md_groups.py` with M5's row, `claude/.claude/hooks/tests/test_agent_roster.py`, and these in `claude/.claude/hooks/tests/test_design_decision_files.py`: `test_item_12a_index_matches_agent_angle_headers`, `test_paragraph_readability_fixture_matches_agent_angle`, `test_provenance_line_is_well_formed` and `test_relative_links_resolve_to_existing_files`. A changed path it cannot map is a rule-table bug to report, not a reason to widen the run.
2. Run `scripts/venv-run.sh ruff check claude/.claude/hooks/tests/test_global_claude_md_groups.py`.
3. Hard gate: `wc -c claude/.claude/CLAUDE.md` prints at most 30720, and `wc -l claude/.claude/CLAUDE.md` at most 200. If M1 passes the byte cap, stop and report rather than trim another bullet, per `code-writer.md`'s line-cap rule. `check-claude-md-length.sh` backstops it at commit.
4. Walk these synthetic cases against the landed text in a scratch file outside the repo, per `.claude/rules/skill-and-agent-self-review.md`. Each has one expected outcome. A case whose outcome needs this plan to resolve is a wording defect, fixed before `/code-review`.
   - a. Docstring "Return the retry delay in seconds: 2 ** attempt." on an internal one-line `_retry_delay_seconds(attempt)`: delete.
   - b. A finding that case a's docstring went stale when the body became `3 ** attempt`: item 5's fix names the docstring's lines to delete, not a corrected docstring.
   - c. `timeout_seconds = 30  # seconds`: delete, because the name shows the unit. Its twin `timeout = 30  # seconds`: keep, because nothing in the code shows the unit.
   - d. Docstring "Raises ValueError if name is empty." on an internal `_require_name(name)` whose body raises `ValueError` on an empty string: delete, because the branch and the exception type show it.
   - e. `# Checks that an empty name raises ValueError` above `test_empty_name_raises_value_error`: delete. Its twin `# Regression for issue #<n>: an empty name crashed the parser` above the same test: keep, because the issue is an external fact.
   - f. `# lookup() bisects this list, so keep it sorted` above `entries.sort()`: keep, because it states an invariant another site relies on.
   - g. A README usage line naming a flag the diff renamed: correct it, because it states how to use the tool.
   - h. A README feature section with one sentence on what the feature does for its user, then a paragraph on its internal call order: delete the paragraph and keep the sentence, because call order is how it works and the code is its only home.
   - i. A finding asking to restore a docstring an earlier round deleted as restatement: the contradiction route's consult returns *keep current text*.
5. Smoke run, K=1, with no discrimination claim. Dispatch a `general-purpose` subagent with `model: sonnet`, and name in its prompt this worktree's `claude/.claude/agents/comment-discipline-reviewer.md` as its instructions and this worktree's `claude/.claude/CLAUDE.md` to read first. Both stowed `~/.claude` copies resolve to the main checkout and carry the old text, so the prompt states that the worktree copies supersede the loaded ones and asks the return to quote the M1 bullet it applied. Hand it a scratch diff adding cases a, c, d and e as must-flag, and c's twin, e's twin, f and g as must-pass. Then ask it, acting as the session at `/code-review` item 5, for the suggested fix to case b's finding and to an accuracy finding on case g. Pass when every must-flag case draws a finding whose fix is deletion, under any angle; no must-pass case draws a deletion; b's fix deletes; and g's fix corrects.
6. Before commit, the session re-fetches every URL in M6's `## Sources` and confirms each committed quote verbatim and the Rust description as accurate. A quote that does not match is corrected or dropped. `code-writer` has no web access, so this step stays with the session.
7. `/code-review` on the staged diff, which dispatches `/skill-review` for `SKILL.md` (hook-enforced), `/agent-review` for the two agent files, and `ai-instruction-and-memory-files` for `CLAUDE.md`.

## Out of scope

- The contradiction route's fourth verdict, its `test_skills.py` pin edits, and a supersession note on `ready-for-review-fix-loop-convergence.md`. Once item 5 makes the fix a deletion, *apply this round's fix* applies it, and a request to restore a deleted restatement resolves *keep current text* (rows 17, 18).
- The straight-to-human stop for a site two rounds already rewrote stays as it is. The session composes that question with M1 loaded. Revisit it only if those stops keep landing on restating prose.
- A guard paragraph and its sub-bullets in `comment-discipline-reviewer`. M1's feature-doc sentence carries the user-facing guard, and the agent cites M1 (row 8).
- `code-writer`'s self-review bullet and its swap of a dictated correction for a deletion. Item 5 already makes the dictated fix a deletion, and the swap would let the implementer overrule the parent's disposition on a judgment call.
- A published-API exception (row 11).
- `docs/cost-levers-considered.md`, whose deferred `code-writer` form stays deferred (row 24).
- `plan-it/SKILL.md`'s comment-content rule. `plan-architect` loads M1, so a plan it writes is drafted under the rule (row 15).
- `comment-discipline-reviewer`'s cadence, which stays deferred to the cumulative pass. M2 and M4 apply in every round.
- A sweep of existing restating prose. Item 5 deletes such prose when a finding next lands on it.
- A committed restatement fixture or a measured discrimination rate. Verification's smoke run claims none, and the engineer's private example stays out of the repo (row 26).
- An edit to `docs/design-decisions/global-claude-md-agent-core-and-main-session-groups.md`. M1 reopens its residuals, but none concerns prose (row 25).
