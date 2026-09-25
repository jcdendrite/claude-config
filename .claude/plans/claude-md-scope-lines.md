# Fix two dangling scope pointers in the global CLAUDE.md (GH-1085 follow-up G2)

## Context

Goal: make the two scope pointers in `claude/.claude/CLAUDE.md` that the Agent Core move left dangling resolve correctly, without growing a file that is already over its byte ceiling.

The move folded the old top-level comments section into `### Durable text` under `## Prose and Output Format`. Since then, "the section below" at :100 points at a subsection, and "This section governs" at :121 can be read as all of Prose. The G1 PR (merged) left both for this follow-up. The opening line (:3), "Merge stays human-only" (:96) and "the rules above" (:141, tracked in GH-1091) stay untouched.

## Approach

Replace each position-relative pointer with the section's name, `§Durable text`. At :100, "in the section below" becomes "in §Durable text". At :121, "This section governs" becomes "§Durable text governs". The net change is −1 byte and 0 lines. In the same commit, delete the two resolved bullets (:128-129) from the decision record's follow-up list so it stops listing them as open.

Two edits are the minimum, because the move broke two separate references. The `§` sigil is deliberate: bare "Durable text" is a generic noun phrase in both citing sentences.
- At :100, bare "in Durable text" lands right after "durable in-repo docs", so "constraints in durable text" parses as a generic noun phrase and stops working as a pointer.
- At :121, "Durable text" starts the sentence, so its capital letter no longer marks it as a name.
- `§Durable text` is how every other file names this section, and it still nets −1 byte.

Alternatives set aside:
- **Bare "Durable text" at both sites (net −5).** Rejected for the generic-noun collision above.
- **"in Durable text, below" or "in Durable text below" at :100.** Costs +1 or +2 bytes, so net-zero would need a compensating trim elsewhere. It also keeps a positional cue, and a positional reference is what went stale here.
- **"This subsection governs" at :121 (+3 bytes).** Still a relative reference, so the next heading-level change would break it again.
- **"These rules govern" at :121 (−1 byte).** Reuses :100's subject phrase for the opposite scope 21 lines away.
- **Delete :121's scope clause and keep only the `pr-description` routing.** Drops the "only" restriction, which CLAUDE.md's "Never drop or flatten a fact … to shorten a sentence" rule forbids.
- **Leave the record untouched.** It would then list two fixed items as open.
- **Mark the record's bullets "resolved" instead of deleting them.** That is change narration in a durable doc; git history already holds it.

### Assumption ledger

**Root problem:** #1090 folded the old top-level comments section into `### Durable text` under `## Prose and Output Format`. That left two pointers whose target is unclear:
- :100 "the section below" now points at a subsection.
- :121 "This section" can be read as all of Prose, which contradicts :100.

Both must resolve without growing a CLAUDE.md that is already over its byte ceiling.

**Givens:**
- **Given: CLAUDE.md cannot grow in bytes or lines relative to HEAD.** Removing this would need a decision outside this plan: the limit is fixed at `check-claude-md-length.sh:52`, and the record's Byte margin section (:131-139) makes net-zero a standing rule for every CLAUDE.md edit. `[verified: check-claude-md-length.sh:52,122; _lib.sh:1603-1619; record :133]`

**Mechanisms:**

| Mechanism | Justification | anchors |
|---|---|---|
| :100 "in the section below" → "in §Durable text" | A name survives both moves and heading-level changes. The position-relative wording is what broke. | `anchors: root, row1, row6, row7, row8` |
| :121 "This section governs" → "§Durable text governs" | Naming the subsection rules out the Prose reading. It uses the same term as :100 (one term per concept). | `anchors: root, row2, row6, row7, row8` |
| Record: delete :128-129 | The follow-up list describes current state (Axis 3), and these two items are now done. | `anchors: row3, row4, row5` |
| PR body notes that the record's re-review trigger fired and no residual changed | :110 says any change to Agent Core reopens that section. | `anchors: row12` |
| No new test, no CHANGELOG entry | No rule's content changes. A phrase pin would be a proxy for the text itself, and no test pins the old phrases. | `anchors: row10, row14` |

Every mechanism is a text edit, and none is heavier than the task needs.

| # | Assumption | Tag |
|---|---|---|
| 1 | :100 ends "…carry the further constraints in the section below." Its target, `### Durable text` (:113), is a subsection of `## Prose and Output Format` (:98). | `[verified: claude/.claude/CLAUDE.md:98-113]` |
| 2 | :121 sits under `#### When to write it and what to include` (:119) inside `### Durable text`. Read as the whole Prose section, "This section governs comments and durable docs only" contradicts :100's "These rules govern every text surface you author — … PR bodies, commit messages…". | `[verified: CLAUDE.md:100,113,119-121]` |
| 3 | The record lists both phrases as open follow-ups at :128-129, below :126 (Merge stays human-only) and :127 (GH-1091). | `[verified: record :124-129]` |
| 4 | Under Axis 3's decision test, G1's merged plan classified this follow-up list as editable current state ("lists follow-ups that are still open"). | `[verified: .claude/plans/record-followups.md:20-24; CLAUDE.md:75-82]` |
| 5 | Decision files are already edited in place under the Supersession convention, so the directory does not freeze current-state lines. | `[verified: .claude/rules/design-decisions.md, Supersession bullet]` |
| 6 | Every other file that names this section writes "§Durable text": README.md:244, plan-it/SKILL.md:71, code-review/SKILL.md:71,130, code-writer.md:71,117, comment-discipline-reviewer.md:10,41, and CHANGELOG.md:14 ("References to the old name now read `§Durable text`"). | `[verified: repo grep]` |
| 7 | CLAUDE.md's own internal pointers use bare heading names (:95 "Safety's marker bullet", :178 "Agent Briefing, above") and never `§` for its own sections. Deviating here is justified because "Durable text" is also a generic noun phrase in both citing sentences ("durable in-repo docs", "durable docs"), and "Safety" and "Agent Briefing" are not. | `[verified: CLAUDE.md grep for §/above/below]` for the convention; `[unverified: judgment on how a reader parses the bare form]` |
| 8 | Byte math: "the section below" is 17 bytes, and "§Durable text" is 14 (U+00A7 is two bytes in UTF-8), so −3. "This section" is 12 bytes, so +2. Net −1 byte, 0 lines. | `[unverified: hand count; Verification 1 re-derives it with wc]` |
| 9 | The file is 195 lines and 31418 bytes, against a byte limit of 25600. The ratchet denies only when new > limit and new > old, on both the line and byte dimensions. | `[verified: check-claude-md-length.sh:52; _lib.sh:1616-1619; wc -lc this session]` |
| 10 | No test pins either phrase or CLAUDE.md's byte count. `test_global_claude_md_groups.py` pins only the heading's placement (:111, :248). `test_output_preferences_layering.py` reads Prose's bold lead-ins only (:91-93). A `*.py` grep for "section below", "This section governs", "further constraints" and "durable docs only" finds no CLAUDE.md pin. | `[verified: grep; the two test files]` |
| 11 | `§Durable text` does not form a citation candidate. `_CITATION_CANDIDATE_RE` is `§\s*"`, which needs a quote (test_skills.py:3674). `test_design_decision_files.py`'s `§` patterns are numeric only (:71, :99, :108, :119, :195). | `[verified: both files]` |
| 12 | Record :110, "Any later change to CLAUDE.md Agent Core reopens this section", fires because Prose sits in Agent Core (:1-129). None of the section's residuals (the fork/identity gate, settings gaps a–h) involves Prose or Durable text. | `[verified: record :95-110; CLAUDE.md headings]` |
| 13 | The only other files that quote the old phrases are earlier plans (`output-format-best-practices.md:70`, `trim-comment-verbosity.md:83`, `claude-md-agent-core.md`, `record-followups.md`). They are preserved plan records and are not edited. | `[verified: repo grep]` |
| 14 | No CHANGELOG entry is needed: both edits re-point a reference and change no rule. | `[unverified: judgment that this is not "notable" under CHANGELOG.md:3]` |
| 15 | `select-tests.py` maps `claude/.claude/CLAUDE.md` to the hooks and skills tests, and covers all of `docs/` with a blanket rule. | `[verified: select-tests.py:166-171,187-192; test_select_tests.py:921]` |
| 16 | The net-zero-or-fewer target and the exclusions (:3, :96, :141) come from the prior session's handoff note. The engineer made no statement about them this session. | `[unverified: handoff relay]` |

## Critical files

- `claude/.claude/CLAUDE.md`. Two in-line substitutions, nothing else:
  - :100: `…carry the further constraints in the section below.` → `…carry the further constraints in §Durable text.`
  - :121: `This section governs comments and durable docs only — …` → `§Durable text governs comments and durable docs only — …` (the rest of the line is unchanged)
  - Copy the `§` from an existing site such as CHANGELOG.md:14 so it is U+00A7 and not a lookalike.
- `docs/design-decisions/global-claude-md-agent-core-and-main-session-groups.md`. Delete :128 and :129. Keep the lead-in (:124) and the two remaining bullets (:126-127) unchanged.
- `.claude/plans/claude-md-scope-lines.md`. The plan, committed with the change per plan-it Step 7.

**Reuse:** the repo's existing `§Durable text` term (row 6), `select-tests.py`, and the commit-time length gate, which verifies the ratchet on its own.

**Dispatch split:** one `code-writer` dispatch for one phase covering both files. Land them in one commit so the record is never out of sync with CLAUDE.md. Its verification command is Verification 4.

## Verification

Run each item as its own single-statement Bash call from the worktree root.

1. `wc -lc claude/.claude/CLAUDE.md` reports 195 lines and 31417 bytes, one fewer than HEAD's 31418. `git diff --numstat HEAD -- claude/.claude/CLAUDE.md` reports `2	2`.
2. `git grep -n -e "the section below" -e "This section governs" -- claude/.claude/CLAUDE.md docs/design-decisions/global-claude-md-agent-core-and-main-session-groups.md` finds nothing. `git grep -n "§Durable text" -- claude/.claude/CLAUDE.md` matches exactly :100 and :121.
3. Read the record at :122-128. The lead-in is followed only by the "Merge stays human-only" and "the rules above … GH-1091" bullets, and `## Byte margin` follows.
4. `../../../../.venv/bin/python3 claude/.claude/scripts/select-tests.py` passes. The tests that bear on this change are `test_global_claude_md_groups.py`, `test_output_preferences_layering.py`, `test_design_decision_files.py`, and `test_skills.py::test_every_citation_shaped_construct_is_extracted`.
5. `/code-review` runs, including the `ai-instruction-and-memory-files` review for the CLAUDE.md edit. The commit then passes `check-claude-md-length.sh` without a deny.
6. The PR body states that record :110's re-review trigger fired and no residual changed (row 12). It also states that there is no CHANGELOG entry, and why (row 14).

## Out of scope

- **:3 (the opening line) and :96 ("Merge stays human-only").** Both wait for the post-merge spot-check (record :26-31, :33, :126).
- **:141 ("the rules above").** Tracked in GH-1091 (record :127).
- **:95 ("Safety's marker bullet").** Already an accepted risk (record :64).
- **:182 ("per Codebase discovery").** It names a heading in `subagent-delegation/SKILL.md:98` without naming that file. `claude-md-agent-core.md:464` records it as predating #1090, so it is not a dangling phrase from the move. It is a candidate for a separate fix.
- **A test that resolves `§Name` references to CLAUDE.md headings.** No test resolves `§Durable text` today (`claude-md-agent-core.md:337`), so a future rename would break these pointers silently. That guard is worth building, but it is a separate change.
