# GH-1085 Phase 2: wording follow-ups to the global CLAUDE.md

## Context

Widen and merge four rules in `claude/.claude/CLAUDE.md` without growing the file. Phase 1 (#1090) is merged; its plan `.claude/plans/claude-md-agent-core.md` is a preserved record and stays as written. GH-1085 lists these Phase 2 items: widen "understand intent" beyond code and configuration, widen "walk through approach" beyond code, generalize "verify, don't guess" to how code or technology works, and merge the "check, don't assume" cluster. The file is over its 25,600-byte cap, so `check-claude-md-length.sh` denies any commit larger than HEAD.

## Approach

This change widens three rules and merges one pair in `claude/.claude/CLAUDE.md`:

- Lines 32 and 64 become one Engineering Judgment bullet that opens with "Never assume" and deletes line 64.
- Line 28 extends to infrastructure and architecture.
- Line 140 extends to any solution, recommendation, or finding.
- Three trims elsewhere in the same commit pay for the growth.

Line numbers are pre-edit. After line 64 is deleted, every line below it shifts up by one.

### Exact edits

```markdown
:28  - Before proposing changes, understand the intent of the existing code, configuration, infrastructure, or architecture.
:32  - Never assume how code or technology works (a CLI flag, tool behavior, or API detail) or what the environment, stack, or project conventions are. Check the actual docs, code, config, or runtime behavior.
:64  (deleted)
:140 - Walk through your proposed approach and explain tradeoffs before writing code or committing to a solution, recommendation, or finding.
```

The merged bullet's last sentence ("config, or runtime behavior") is plan-architect's wording. It keeps the method-agnostic "verify" that line 32 carried, since running a command is how most CLI and environment facts get checked.

### Applied trims

Each trim deletes an exact substring and leaves the rest of the line as it is.

| ID | Line | Delete exactly | Saves | Still stated at |
|---|---|---|---|---|
| A | :166 (Main › Agent Briefing) | ` Exit plan mode in the parent before delegating execution work.` (last sentence, leading space) | 63 | :166, first sentence ("call `ExitPlanMode` in the parent first") |
| C | :187 only, the `**`high` (the default):**` sub-bullet (Main › Model & Effort Routing). The same text also occurs at :188, the `xhigh` sub-bullet, which stays untouched. | `; see `docs/design-decisions.md` §24 in the claude-config repo`, leaving `(e.g. `code-writer`)` | 63 | :184 cites the same §24 file |
| E | :183 only, the `**Always dispatch `general-purpose` with an explicit `model`.**` bullet. `remaining ` also occurs at :174, which stays untouched. | Two substrings: `Its routine remaining use` becomes `Its routine use`, and `; code-writing now routes to `code-writer`` is deleted | 52 | :182 routes code-writing to `code-writer` |

### Why "Never assume" instead of "When uncertain"

The reason is semantic, not the byte budget. "When uncertain" makes the check depend on how confident the agent feels. A confidently wrong belief, such as a hallucinated flag or a stale remembered API, never trips it. That is exactly the failure the rule exists to catch. "Never assume" depends on the act of relying on an unchecked belief, whatever the agent's confidence. It is also the trigger line 64 already used for environment facts ("Before assuming anything…"). When checking is impossible, line 61 already says what to do: "say so and name what would resolve it."

### Byte budget

Hand-counted UTF-8 bytes (ASCII = 1, `—` = 3, `§` = 2). Verification 1 measures them.

| Edit | Delta |
|---|---|
| :28 widened | +31 |
| :32 replaced and :64 deleted | −43 |
| :140 widened | +56 |
| Growth before trims | +44 |
| Trims A, C, E | −178 |
| **Net** | **−134** |

### Assumption ledger

**Root problem:** line 32 makes checking depend on the agent feeling uncertain, so a confident wrong belief about a flag, tool, or API never triggers a check. Line 64 says the same about the environment in a second bullet. Lines 28 and 140 are scoped to code only. The file is over its byte cap, so every addition is paid for in the same commit.

**Givens:**
- **G1. The byte ratchet stays.** The gate denies when the staged size is over 25,600 bytes and larger than the base. Removing that constraint means raising the cap, which is a decision outside this plan. `[verified: claude/.claude/hooks/_lib.sh:1743-1752; claude/.claude/hooks/check-claude-md-length.sh:52,122; docs/design-decisions/global-claude-md-agent-core-and-main-session-groups.md, Byte margin section]`
- **G2. Preserved records stay as written.** Axis 3 of `claude/.claude/CLAUDE.md` § Scope discipline is a repo rule this plan does not own. It covers the dated decision record and the plan files that quote the replaced text. `[verified: claude/.claude/CLAUDE.md, Scope discipline Axis 3]`

**Mechanisms:**

| Mechanism | Justification | anchors |
|---|---|---|
| Merge :32 and :64 into one bullet at :32's position, delete :64 | Both say "check, don't guess" about neighboring objects. One bullet is the single source. Line 32 sits next to line 29's "this project's actual stack". Both lines are in Agent Core, so no group changes. | `anchors: row1, row15, row19` |
| Trigger "Never assume" | Depends on the act, not on the agent's confidence. Also the trigger line 64 already ships. | `anchors: row2, row21, row22` |
| Object "how code or technology works", with line 32's old list as a parenthetical | Widens the object from three examples to the class. The examples stay as anchors. | `anchors: row3` |
| Last sentence names "runtime behavior" as a source | Keeps line 32's method-agnostic "verify". Docs, code, and config alone would drop running a command. | `anchors: row17, row9` |
| :28 extends to infrastructure and architecture | The engineer's wording point. | `anchors: row5, row6` |
| :140 extends to solution, recommendation, or finding; stays in Main session | Its action needs a user. Line 54 is the matching rule for dispatched agents. | `anchors: row4, row19, row20` |
| Growth paid by same-commit trims A, C, E | The ratchet compares each commit with HEAD. Alternatives set aside: (a) raising `GLOBAL_CLAUDE_MD_BYTE_LIMIT` is ruled out by G1; (b) moving a paragraph into a path-scoped rule is heavier and changes when the text loads; (c) tightening the new wording is excluded because the engineer asked for trims from elsewhere. | `anchors: G1, row7, row8` |
| CLAUDE.md and CHANGELOG.md land in one commit | Trims committed before the growth would lower HEAD, and the gate would then deny the growth commit. | `anchors: row8` |
| One CHANGELOG.md entry | Every stow consumer's agents change behavior. | `anchors: row27, row28` |

None of these mechanisms is heavier than the task needs. They are all wording edits.

| # | Assumption | Tag |
|---|---|---|
| 1 | The merge covers lines 32 and 64 only. | `[engineer-verified: "Merge :32 + :64 only (Recommended)"]` |
| 2 | "Never assume", including its broader scope. | `[engineer-verified: "I think "Never assume" is good but it's different than "When uncertain". It seems like the scope is broader though, which is overall a good thing -- I'd want this to appply to confident guesses."]` |
| 3 | "a piece of" is dropped. | `[engineer-verified: "I think "a piece of" is mostly redundant. And leaving it out gets agents to apply the directive more broadly."]` |
| 4 | Line 140 extends beyond code, with this wording. | `[engineer-verified: "I want the guideline to apply not just to writing code but to any solution, recommendation, finding" / "I like widening to "solution, recommendation, or finding"."]` |
| 5 | Line 28 says "architecture", not "system architecture". | `[engineer-verified: "I think "system architecture" is redundant with "architecture"."]` |
| 6 | Line 28's full list is "code, configuration, infrastructure, or architecture", in that order. | `[unverified: the session's reading of row 5; the engineer chose no option letter]` |
| 7 | The engineer picks the trims. They picked A, C, and E. | `[engineer-verified: "name candidates for me to choose for byte trimming from elsewhere" / selected labels "A: Agent Briefing plan-mode repeat, C: duplicate 4th design-decisions cite, E: 'remaining' and 'now routes'"]` |
| 8 | The gate denies when staged bytes exceed 25,600 and exceed the base; the base is HEAD outside a merge or rebase. The line limit is 200. | `[verified: claude/.claude/hooks/_lib.sh:1700-1754; check-claude-md-length.sh:111-116]` |
| 9 | The merged bullet ends "config, or runtime behavior." | `[engineer-verified: selected label "Runtime behavior (Recommended)"]` |
| 10 | The baseline is 31,552 bytes and 196 lines; 195 lines after line 64 is deleted. | `[verified: wc -c and wc -l on claude/.claude/CLAUDE.md this session]` |
| 11 | Per-edit deltas and each trim's savings. | `[unverified: hand-counted; Verification 1 measures]` |
| 12 | The group test pins "Walk through your proposed approach" to exactly one line below `# Main session`. None of its pinned fragments are on lines 21, 28, 32, 64, 166, 183, or 187. | `[verified: claude/.claude/hooks/tests/test_global_claude_md_groups.py:27-95, 155-168, 262-275]` |
| 13 | The drift guard asserts the Attribution bullet's lead-in inside the first `## Working Style` body. Deleting line 64 leaves line 62 where it is. | `[verified: claude/.claude/hooks/tests/test_nudge_answer_provenance.py:63-69, 162-163]` |
| 14 | `test_doc_counts.py` reads CLAUDE.md only for the "Ground every choice" count. `test_skills.py` reads it for marker-content, MEMORY.md, and path-templating phrases. None overlap the edited or trimmed text. | `[verified: test_doc_counts.py:149-181; claude-skills/skills/tests/test_skills.py:1685-1761, 5807-6049]` |
| 15 | Only preserved plans quote the replaced text or the trimmed text: `.claude/plans/precompact-review-snapshot.md:39` (old :32), `.claude/plans/claude-md-agent-core.md:134` (old :140). | `[verified: repo grep]` |
| 16 | §24 is `docs/design-decisions/effort-tier-routing-clamp.md`, whose line 9 holds the code-writer `high` rationale. Line 184 already cites it. | `[verified: those files]` |
| 17 | Line 32 has been read as licensing live experiments, not only reads of documentation. | `[verified: .claude/plans/precompact-review-snapshot.md:39, "They need a real, live experiment"]` |
| 18 | Any change to Agent Core reopens the decision record's "Open residuals and re-review triggers". Its contents cover the fork and shipping clause, the permissions stub, gaps (a)–(h), and the auto-mode `ask`. None sits at lines 28, 32, or 64. Result: re-reviewed, unaffected. | `[verified: docs/design-decisions/global-claude-md-agent-core-and-main-session-groups.md, Open residuals section]` |
| 19 | A rule whose trigger can arise in a subagent goes to Agent Core. Line 140 stays in Main session because a dispatched agent "has no user to walk through an approach with". Line 54 is the dispatched-agent half. | `[verified: decision record; test_global_claude_md_groups.py:274; claude/.claude/CLAUDE.md:54]` |
| 20 | "Committing to" in line 140 names a decision point, meaning treating something as settled, not a position within a reply, so it fits with line 103 "Lead with the answer". | `[unverified: plan-architect's reading]` |
| 21 | Taken literally, "Never assume" can't be satisfied. Agents may over-verify or treat it as aspirational. The effect hasn't been measured. | `[unverified]` |
| 22 | The deleted line 64 already opened with an unconditional "Before assuming anything". | `[verified: claude/.claude/CLAUDE.md:64]` |
| 23 | A report from a subagent or tool about what it read counts as a check, not an assumption. The merged bullet does not require re-reading delegated findings; the more specific delegation rules govern. | `[unverified: plan-architect's reading]` |
| 24 | `select-tests.py` maps the global CLAUDE.md to `claude/.claude/hooks/tests` and `claude-skills/skills/tests`. | `[verified: claude/.claude/scripts/tests/test_select_tests.py:921-930]` |
| 25 | `select-tests.py` maps CHANGELOG.md and the plan file to no tests, so they don't widen the run. | `[verified: claude/.claude/scripts/select-tests.py:380-381; staff-sdet call of select_pytest_targets on the three paths]` |
| 26 | This worktree is four levels below the repo root, because the branch name contains `/`. README's three-level `../../../.venv` resolves wrongly here. | `[verified: README.md:520; worktree layout .claude/worktrees/GH-1085/<slug>/]` |
| 27 | CHANGELOG.md logs "all notable changes", and Phase 1's consumer-visible CLAUDE.md changes are listed under [Unreleased] › Changed. | `[verified: CHANGELOG.md:3, 9-18]` |
| 28 | Two widened triggers count as notable. | `[unverified: plan-architect's judgment]` |
| 29 | `select-tests.py` does not select `claude/.claude/scripts/tests` for this diff, so the known `test_respond_pr_lib.py` failure does not appear in the scoped run. | `[verified: staff-sdet, select-tests.py:500]` |
| 30 | The decision record's four dangling phrases and the `advance-past-commit-stall.sh` forward pointer are a separate PR. | `[engineer-verified: selected label "Separate PR (Recommended)"]` |
| 31 | The "Merge stays human-only…" sentence is pinned as a whole sentence, so rewording it needs a test edit. | `[verified: test_global_claude_md_groups.py:74-78]` |

## Critical files

| Path | Action |
|---|---|
| `claude/.claude/CLAUDE.md` | Replace lines 28, 32, and 140 with the exact text under "Exact edits", delete line 64, and apply trims A, C, and E. Touch nothing else. |
| `CHANGELOG.md` | Add one bullet at the top of the [Unreleased] › Changed list (newest first, above Phase 1's entry): (1) the verify rule fires on any unchecked belief about how code or technology works, or about the environment, stack, or project conventions, and not only when the agent feels uncertain, with the separate environment-assumption bullet merged into it; (2) the main session's walk-through rule now also applies before committing to a solution, recommendation, or finding; (3) "understand the intent" now covers infrastructure and architecture. |
| `.claude/plans/claude-md-wording-followups.md` | This plan, committed before implementation per plan-it Step 7. |

Read-only references, not to be edited: `claude/.claude/hooks/tests/test_global_claude_md_groups.py`, `claude/.claude/hooks/tests/test_nudge_answer_provenance.py`, `claude/.claude/hooks/tests/test_doc_counts.py`, `claude/.claude/hooks/check-claude-md-length.sh`, `claude/.claude/hooks/_lib.sh`, `docs/design-decisions/global-claude-md-agent-core-and-main-session-groups.md`.

**Reuse:** the gate is the check. `git cat-file -s :<path>` is the gate's own measurement (`_lib.sh:1745`), and a `git commit` through `check-claude-md-length.sh` is the live test.

**Dispatch split:** one `code-writer` dispatch (`model: sonnet`) edits `claude/.claude/CLAUDE.md` and `CHANGELOG.md` and runs Verification 1–3. The parent then runs `/code-review` and commits both files in one commit.

## Verification

1. **Size.** From the worktree root, after staging, read both figures from the index, as the gate does: `git cat-file -s :claude/.claude/CLAUDE.md` must be less than or equal to `git cat-file -s HEAD:claude/.claude/CLAUDE.md`, and `git show :claude/.claude/CLAUDE.md | wc -l` must print `195`. If the staged size is larger, go back to the engineer. Never raise the cap.
2. **Pins and diff shape.**
   - `git grep -c "Walk through your proposed approach" -- claude/.claude/CLAUDE.md` prints `claude/.claude/CLAUDE.md:1`.
   - `git grep -n -e "When uncertain about a CLI flag" -e "Before assuming anything about the environment"` hits only under `.claude/plans/`.
   - `git diff --cached -U0 -- claude/.claude/CLAUDE.md` shows hunks at exactly pre-edit lines 28, 32, 64, 140, 166, 183, and 187, and each post-edit line equals the text in "Exact edits" and "Applied trims". A hunk anywhere else, such as :174 or :188, is a defect.
3. **Tests.** From the worktree root, run `../../../../.venv/bin/python3 claude/.claude/scripts/select-tests.py` (four levels up; see row 26).
   - Expect at least `claude/.claude/hooks/tests` and `claude-skills/skills/tests` to be selected.
   - `test_global_claude_md_groups.py`, `test_nudge_answer_provenance.py`, `test_doc_counts.py`, and `test_skills.py` must pass.
4. **Review and commit.** Run `/code-review` on the staged diff. It invokes `ai-instruction-and-memory-files` for CLAUDE.md. Give its compression-diff audit the "Still stated at" column for each trim. Then run `git commit`; the length gate allowing it is the live confirmation of step 1.
5. **PR body.** It states: the semantic justification for "Never assume"; the decision record's re-review result (reopened by the Agent Core edits, re-reviewed, unaffected, row 18); and the line 61 overlap named under Out of scope.

## Out of scope

- **Line 44 "Ground every choice" and line 61 "Be precise"** are outside the engineer's merge selection (row 1). One overlap remains: line 61's "verify claims against actual code — not against what the code or a sensible design should do" and the merged bullet both point to the actual code over what it should do. Their objects differ: line 61 governs claims the agent states, and the merged bullet governs premises the agent acts on. Line 61 is left as it is.
- **Preserved records** stay untouched (G2): `.claude/plans/precompact-review-snapshot.md`, `.claude/plans/claude-md-agent-core.md`, and the dated decision record. The record's re-review result goes in the PR body, not into the record.
- **Raising `GLOBAL_CLAUDE_MD_BYTE_LIMIT`** is excluded (G1).
- **The decision record's dangling-phrase follow-ups and the `advance-past-commit-stall.sh` forward pointer** go in a separate PR (row 30).
- **Trims D and F** from the candidate list (D drops the "test is the source of truth" clause at line 189; F drops "regardless of how the target was determined" at line 21) were offered and not chosen.
- **Tightening the newly worded bullets to save bytes** is excluded, because the engineer asked for trims from elsewhere.
- **A `_PLACEMENTS` row in `test_global_claude_md_groups.py` pinning the merged bullet in Agent Core** is excluded. The old lines 32 and 64 were unpinned, so this change loses no coverage, and the group test is a read-only reference for this plan. Adding the pin is a separate decision.
- **README's "exactly three levels deep" worktree claim** (`README.md:520`) is wrong for a slash-in-branch worktree. Fixing it is unrelated to this change.
