# GH-749: comment-discipline-reviewer lane covers comments in every language

## Context

Goal: make sure `comment-discipline-reviewer` receives changed comments in every file type, so per-spawn lane-narrowing no longer leaves out shell, TOML and other non-markdown comments.

Ask: GH-749, "confirm `.sh` and `.toml` — and likely other non-obvious-but-prose-bearing file types — reliably trigger `comment-discipline-reviewer` when their comments change". Re-scoped by the engineer via label "Re-scope: lane-narrowing (Recommended)".

GH-749 names two PRs. In PR #722, a human reviewer caught over-long comments in `install.sh` and `pyproject.toml`. In PR #733, a human caught five prose findings. The ticket blames code-review's Change-type table for missing those file types. Forensics this session found that premise false:

- The comment-discipline row has been language-agnostic ("code comments, docstrings, …") at both PRs' base commits (`1dfd9870^`, `d6d39301^`) and today (`claude-skills/skills/code-review/SKILL.md:285`).
- PR #722's body says the reviewer was spawned. The cumulative pass handed it `README.md`, `docs/hooks.md` and `tighten-prose/REFERENCES.md`, but not `install.sh` or `pyproject.toml`. Step 0.6 tells the parent to "pass each specialist only the file(s) within their lane", and the comment reviewer's lane is never defined. The likely mechanism is that the parent narrowed that lane to markdown. Transcripts for those sessions are unavailable, so this inference rests on the PR body.
- `evals/review_bench`'s `guess_lens`, which pre-fills a lens label for mined defect candidates and dispatches nothing, guesses `comment-discipline-reviewer` only for markdown paths. That reflects the same assumption.
- PR #733's reviewer was run over all 105 files and missed anyway. That is a reviewer-quality miss, not a dispatch miss, and is out of scope.
- The Step 0.6 deferral of this row to `ready-for-review`'s cumulative pass (#1023, 2026-09-16) postdates both PRs. It does not cause the bug, but it does not fix it either: the cumulative pass applies the same per-spawn lane narrowing.

Intended outcome: the dispatch instructions define this reviewer's lane as every changed comment or durable-doc prose site in any language. A test pins that definition.

## Approach

`comment-discipline-reviewer`'s Change-type row becomes the one place that states its lane: every comment and durable-doc prose site in any language. The row also tells the parent to hand the reviewer the whole diff rather than a file subset, because the reviewer already scopes itself. Step 0.6's narrowing sentence gets two additions: a key for every lane (what the change does, never file type), and a pointer to the row's exemption. One test pins both sites.

Alternatives considered and set aside:
- **Keep parent narrowing, but define the lane as "files whose diff touches a comment, any language".** This still leaves the parent sorting hunks file by file, which is exactly the judgment that produced the markdown shortcut (row 5). The reviewer already does that sorting (row 7).
- **Widen the row's left cell with script/config examples.** Matching was not the defect (row 3). Changing the left cell also forces a lockstep edit of the `SCOPE_EXEMPT_ROW` anchor (test_skills.py:5108-5119) for no dispatch effect.
- **Add "any language" to the agent's Scope.** In #722 the reviewer never received the omitted files (row 5), so the agent body is not where it failed.
- **Pointer only in Step 0.6, with no general key.** This leaves sibling rows with content-keyed lanes open to the same file-type shortcut (row 11).

### Assumption ledger

**Root:** `comment-discipline-reviewer` can receive only part of the diff. Step 0.6's per-spawn narrowing gives no key for its lane, so a parent that narrows by file type drops comments in shell, TOML, and other non-markdown files.

**Givens:**
- G1: A spawned reviewer sees only its prompt and what the prompt points to (`claude-skills/skills/code-review/SKILL.md:268`, "a reviewer sees only its prompt"). Reason: the harness's Agent tool imposes this, so the lane has to travel in the parent's spawn instruction.

**Rows:**
1. [engineer-verified: "Re-scope: lane-narrowing (Recommended)"] The fix targets per-spawn lane narrowing, not the Change-type table's file-type coverage. anchors: root
2. [unverified] The session wrote this option description: "Plan a fix stating that comment-discipline-reviewer's lane is every changed comment in any language (code-review row + Step 0.6 narrowing language), align the markdown-only eval routing, add a pinning test. #733's reviewer-quality miss stays out of scope." This plan adopts all of it except the eval-routing alignment (see Out of scope). anchors: row1
3. [verified: claude-skills/skills/code-review/SKILL.md:285] The row's left cell names no file type, so a `.sh` or `.toml` comment change already matches the row. Matching is not the defect. anchors: root
4. [verified: claude-skills/skills/code-review/SKILL.md:35] Step 0.6 says "pass each specialist only the file(s) within their lane" and gives no key for what a lane is. Neither code-review/SKILL.md nor comment-discipline-reviewer.md defines this reviewer's lane. Four of the row's six example surfaces are markdown. anchors: root
5. [unverified] PR #722's cumulative pass narrowed the reviewer to markdown files under that sentence. This is inferred from the file list in the PR body; the transcripts are unavailable. anchors: row4
6. [verified: claude/.claude/scripts/pr-diff-against-base.sh:3,16-18; claude-skills/skills/code-review/SKILL.md:290] The diff artifact the note resolves is the whole staged or cumulative diff. A file subset can enter only through the wording of the parent's spawn prompt (inference). anchors: row4
7. [verified: claude/.claude/agents/comment-discipline-reviewer.md:18,25-32; claude-skills/skills/code-review/SKILL.md:428] The reviewer scopes itself:
   - Its Scope section limits it to comment and durable-doc sites, with no file-type limit.
   - Its Input contract judges scope only from the diff it was handed.
   - The Item-ownership preamble says each agent "self-scopes against the diff".

   anchors: row4
8. [verified: claude-skills/skills/code-review/SKILL.md:52] In staged-diff commit-gate rounds on a feature branch, the row does not spawn. A whole-diff spawn therefore runs only where that boundary does not apply, mainly `ready-for-review`'s cumulative pass. anchors: root
9. [unverified] Whole-diff input raises the reviewer's read cost on large cumulative diffs, because its step 1 says "Read every changed file fully" (comment-discipline-reviewer.md:124). Accepted because row 8 limits this to one spawn per cumulative pass. PR #733 shows an unnarrowed spawn over 105 files already happens today. anchors: row8
10. [unverified] The reviewer treats a shell or TOML `#` comment as in scope under "New or modified comments (code comments, docstrings)". Not exercised this session. anchors: row7
11. [verified: claude-skills/skills/code-review/SKILL.md:263-264,270] Other rows also have content-keyed lanes that cross file types:
    - The `ciso-reviewer` always-spawn rule covers private-project prose ("a documentation-only diff is not exempt").
    - The `staff-product-engineer` rule "fires on the channel, not on the file-path domain".
    - Line 270 keys the Change type column on what the change does, "not on file types".

    anchors: root
12a. [engineer-verified: "Keep general key (Recommended)"] Step 0.6 keeps the general lane-key sentence that applies to every row, not only the comment-discipline pointer. anchors: row11
12. [unverified] No sibling row has a documented miss caused by file-type narrowing. M2 fixes the sentence that allows the shortcut; it does not respond to an observed failure. anchors: row11
13. [verified: claude-skills/skills/tests/test_skills.py:5043-5062,5340-5350,5356-5366] The helpers `_scope_exempt_change_type_row_text` and `_heading_section_text` already exist. `test_comment_discipline_row_points_to_its_deferral` is the existing literal-pin precedent for a pointer between the row and Step 0.6. anchors: row4
14. [verified: claude/.claude/hooks/tests/test_check_skill_length.py:323-346] code-review/SKILL.md has a 500-line cap. Both edits extend existing single-line paragraphs (lines 35 and 285) and add no lines. anchors: root
15. [verified: evals/review_bench/arms.py:35-43] arms.py requires the clause "Read every changed file fully" to appear exactly once in comment-discipline-reviewer.md. This plan leaves that file untouched. anchors: row7
16. [verified: evals/review_bench/defects.py:335-353] `guess_lens` is a path-only guess at a mined candidate's lens, which the engineer confirms or overrides ("real routing is judgment over code-review/SKILL.md's Item-ownership table, which this function does not read"). It dispatches no reviewer. anchors: root

**Mechanisms:**
- **M1: the lane sentence plus a whole-diff handoff in row 285.** Two sentences go into the right cell (exact text in Critical files). anchors: row4, row6, row7
  - It is wider in input scope than a file subset, so two lighter options were checked:
    - Parent narrowing to comment-bearing files fails row 5: it keeps the parent judgment that broke.
    - Widening the left cell fails row 3: matching already worked.
  - The cost is bounded by rows 8 and 9.
- **M2: a lane key plus a pointer in Step 0.6 line 35.** It is one clause, no heavier than the sentence it fixes. anchors: row4, row11, row12
  - Pointer-only fails row 11.
  - Relying on line 270 alone fails row 4: line 270 governs which rows match, while line 35's narrowing has no key.
- **M3: one literal-pin test covering both sites.** It follows the precedent in row 13. A rule stated only in the row would otherwise be invisible to someone applying Step 0.6's narrowing. anchors: row13

## Critical files

Use one `code-writer` dispatch. The test pins the SKILL.md text literally, so the two edits share context and should not be split.

1. `claude-skills/skills/code-review/SKILL.md`. Two in-line edits, no new lines (row 14):
   - **Line 35.** Append after "…so their findings stay in-scope." This is outside the `SCOPE_RULE` anchors, which start at line 37:
     ```markdown
     Judge a lane by what each file's change does, the way the Change type column keys its rows, never by file type. The comment-discipline row's spawn instruction exempts that row from this per-spawn narrowing.
     ```
   - **Line 285, right cell.** Insert between "…restated canonical rule)." and "Step 1.5's inline…":
     ```markdown
     Its lane is every comment and durable-doc prose site in the diff, whatever the file's language or type. Hand it the whole diff, never a per-spawn file subset, since it scopes itself to those sites.
     ```
   - Leave untouched: the left cell, line 52's `SCOPE_EXEMPT_ROW` and `SCOPE_RULE` regions, and the diff-artifact note at line 290. The inserted text contains no `|`. Use ASCII apostrophes throughout.
2. `claude-skills/skills/tests/test_skills.py`. Add two tests, one per site, directly after `test_comment_discipline_row_points_to_its_deferral` (~5340-5350), so each site shows red on its own:
   - Add a module constant `_CODE_REVIEW_STEP_0_6_HEADING = "## Step 0.6 — Pre-judgment table check"`, following the `_CODE_REVIEW_STEP_0_1_HEADING` precedent (~6361). Copy the em dash from SKILL.md:33, never an ASCII stand-in.
   - Reuse `_skill_file`, `_scope_exempt_change_type_row_text` (~5043) and `_heading_section_text` (~5356). Do not write a new table or section walker.
   - `test_comment_discipline_row_states_its_whole_diff_lane`: in the row text, assert the literals "Its lane is every comment and durable-doc prose site in the diff, whatever the file's language or type." and "Hand it the whole diff, never a per-spawn file subset".
   - `test_step_0_6_narrowing_keys_lanes_by_change_not_file_type`: in `_heading_section_text(skill_md_path, _CODE_REVIEW_STEP_0_6_HEADING)`, assert both full line-35 sentences from item 1.
   - Write each assertion with the tuple-loop idiom at test_skills.py:5322, so no line exceeds ruff's 130-column limit and each failure message names the missing literal.
   - Docstrings, durable fact only. Row test: "The comment/prose row states an any-language, whole-diff lane, so file-type narrowing cannot drop shell or TOML comments." Step 0.6 test: "Step 0.6's per-spawn narrowing keys lanes by what a change does and points to the comment/prose row's exemption, so narrowing by file type cannot drop a reviewer's in-lane files." Write both docstrings across multiple lines to stay under 130 columns.
3. `.claude/plans/comment-discipline-dispatch-filetypes.md`, committed under plan-it Step 7.

## Verification

Run every command from the worktree root. The contributor venv lives only in the main checkout, and this branch's worktree sits four levels below it because the branch name contains a `/`. Write `.venv/bin/` below as `../../../../.venv/bin/` (README's three-level `../../../.venv` prefix does not resolve here).

1. **Red-green.** Write the new tests first, then run `.venv/bin/pytest claude-skills/skills/tests/test_skills.py -k "test_comment_discipline_row_states_its_whole_diff_lane or test_step_0_6_narrowing_keys_lanes_by_change_not_file_type"`. Both should fail with an `AssertionError` naming a missing literal, not a collection, import or heading-not-found error. Make the SKILL.md edits and re-run; it should pass.
2. **Scoped suite.** From the worktree root, run `.venv/bin/python3 claude/.claude/scripts/select-tests.py` and expect all green. That includes the unchanged pins, which should stay green because neither the left cell nor any anchor region moved:
   - `test_comment_discipline_row_points_to_its_deferral`
   - `test_code_review_staged_diff_instruction_lives_in_its_own_note_only`
   - `test_scope_exempt_row_resolves_to_real_change_type_row`
   - the `_PINNED_SCOPE_CLAUSES` test
3. **Lint.** Run `.venv/bin/ruff check claude/.claude/ claude-skills/`.
4. **Single source of truth and no added lines.** Run `git grep -n "whatever the file's language or type" -- ':!.claude/plans/'`. It should hit only code-review/SKILL.md and test_skills.py; the plan file quotes the sentence by design. `wc -l claude-skills/skills/code-review/SKILL.md` should print 496, the same count as at the base.
5. **Skill review.** The SKILL.md edit goes through `/skill-review`, which is hook-enforced (`.claude/rules/review-pipeline-dispatch.md`). Expect `/code-review` to match the "Reshapes reviewer ownership" row, because line 35 changes how every spawn is narrowed.

## Out of scope

- **`evals/review_bench` routing** (`defects.py:335-353`, `test_review_bench_mining.py:305-306,382,385-386,437`). `guess_lens` is a path-only guess at a mined candidate's lens, confirmed by the engineer, and it dispatches nothing (row 16). It cannot have caused the miss. A path alone cannot tell a comment defect in a `.sh`/`.toml` file from a code defect there, so "aligning" it would mislabel every code defect in those files, or would need new hunk-inspection machinery. It would also shift mined labels and benchmark results with no dispatch benefit.
- **PR #733's miss.** The reviewer ran over all 105 files and still missed; that is a reviewer-quality problem, not a dispatch problem (Context).
- **The `comment-discipline-reviewer.md` body.** Its Scope already has no file-type limit (row 7), and arms.py pins its read clause (row 15). Narrowing its "Read every changed file fully" step to comment-bearing files would need a `LENS_READ_CLAUSES` change. That cost trade-off is accepted under row 9. Row 10 is the residual risk.
- **Giving `comment-discipline-reviewer` `Bash`.** It has none (`claude/.claude/agents/comment-discipline-reviewer.md:6`), so it reads only the diff path or text the parent hands it. Granting it would reverse the closed-form tool design documented at `README.md:247`, and the lane would still have to travel in the spawn prompt (G1).
- **Mechanical enforcement of whole-diff receipt.** `skill-fidelity-reviewer` checks spawn events, not prompt contents (`skill-fidelity-reviewer.md:116-121`). A hook or prompt-content check would be heavier than an instruction gap for a cooperative agent warrants.
- **The row's left cell, the `SCOPE_EXEMPT_ROW` shorthand, and a definition of "hygiene tweak".** Matching works (row 3). Editing them only causes lockstep churn.
- **A new design-decision entry.** The rule and its reason fit in the row's two sentences. `comment-discipline-reviewer-deferred-to-cumulative-pass.md` and `reviewer-responsibility-bounded-to-diff.md` are dated records that this change does not contradict, so they stay read-only.
- **`code-writer.md:138`, `README.md:247`, `ready-for-review/SKILL.md:82`, `.claude/rules/review-pipeline-dispatch.md`.** These are already language-agnostic or unrelated to per-spawn narrowing.
