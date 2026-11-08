# Diff-size prompt, PR 3: the plan-time check

## Context

Goal: make `/plan-it` and `/plan-review` ask the same split-or-proceed question at plan time that PR 2's commit gate asks at commit time, and pin the restated 400 threshold so it cannot drift from `_lib.sh`.

Ask: GH-1364 piece 4 (the diff-size prompt), PR 3 of 3, as scoped by `.claude/plans/diff-size-prompt.md` M7, M8 and PR 3's CHANGELOG bullet from M9. This plan's scope is cited to the design file and the ticket, not quoted from the engineer.

PR 1 (shared helpers) and PR 2 (the commit gate) are merged. The edit sites held at `plan-it/SKILL.md:135` and `plan-review/SKILL.md:96` when checked on `main` at `6409726e`. Both skills' sentences are prescribed verbatim in M7, so this plan restates no wording.

## Approach

PR 3 appends M7's two prescribed sentences to `claude-skills/skills/plan-it/SKILL.md:135` and `claude-skills/skills/plan-review/SKILL.md:96`. It registers one `DocCountFact` in `test_doc_counts.py` that ties every restated threshold in a skill, doc or hook header to `_lib.sh`'s constant, and it adds M9's CHANGELOG bullet for the plan-time question. It departs from M8 in one place: the fact also pins the two restatements in `require-diff-size-prompt.sh`'s header, which PR 2 added after M8 was written (row 7).

Alternatives weighed:
- **M8's five occurrences only.** Set aside: the hook header's lines 5 and 68 restate the threshold the same way, and `claude/.claude/CLAUDE.md` § "Engineering Judgment" has the fix applied to every affected sibling arm.
- **Rewording the hook header to say "the threshold" instead of the number.** Set aside: it edits a gate file this PR otherwise leaves untouched, and two occurrences in a test file already in scope close the same drift.
- **Reading the constant by sourcing `_lib.sh` in a bash subprocess.** Set aside: M8 prescribes the anchored source scan, which fails loudly on a renamed or duplicated assignment, and the file's other hook-literal pin, `_count_claude_md_length_hook_limit`, scans the same way.
- **A "keep in sync" comment beside the constant instead of a test.** Set aside: nothing enforces a comment, and the design file's row 25 keeps the test pin.
- **Pinning `_lib.sh:4707`'s comment and PR 2's CHANGELOG restatement.** Set aside, with the reasons in Out of scope.

**Assumption ledger**

**Root:** `/plan-it` and `/plan-review` never project a plan's diff size, so the split-or-proceed question first reaches the engineer at the commit gate, after the code is written. No test ties the threshold's restatements outside `_lib.sh` to the constant (Context).

**Givens:**
- G1. `plan-it/SKILL.md` stays under a 200-line cap and `plan-review/SKILL.md` under a 500-line cap. Reason: raising either cap is a `check-skill-length.sh` decision outside this plan, and M7 avoids it by adding no line. [verified: `claude/.claude/hooks/check-skill-length.sh:130-141`]

**Rows:**
1. `.claude/plans/diff-size-prompt.md` is the design of record for PR 3. Where this plan departs from it, this plan names the departure and governs for PR 3, and it edits none of that file's rows. [verified: `diff-size-prompt.md:32`, and its row 64, engineer-verified there]
2. M7 prescribes both skill sentences verbatim, each appended to the end of an existing line, so neither adds a line. `plan-it/SKILL.md:135` is the Critical files item. `plan-review/SKILL.md:96` is the "Measure against the ask." paragraph, ending "An unanswered or declined item makes the verdict **Request changes**." [verified: `diff-size-prompt.md:313-317`; both lines read this session]
3. `plan-it/SKILL.md` is 181 lines and `plan-review/SKILL.md` is 302. [verified: `plan-it/SKILL.md` read whole this session; `wc -l` of `plan-review/SKILL.md` printed 302 this session]
4. plan-architect reads `plan-it/SKILL.md` for its section grammar, so the Critical files sentence reaches it with no agent-file edit. [verified: `claude/.claude/agents/plan-architect.md` § "MODE=plan-sections", the charter this dispatch runs under; `diff-size-prompt.md` row 22]
5. `_LIB_DIFF_SIZE_PROMPT_THRESHOLD_LINES=400` at `_lib.sh:4708` is the threshold's only assignment, and `_lib.sh:4798` and `diff-size-ack.sh:31` read the variable. [verified: grep this session]
6. Outside `_lib.sh:4708` and `.claude/plans/`, the threshold is restated at `docs/hooks.md:177`, `README.md:192`, `require-diff-size-prompt.sh:5` and `:68`, `_lib.sh:4707`'s comment and `CHANGELOG.md:542`. `test_lib_diff_size.py` and `test_require_diff_size_prompt.py:50` use 400 as fixture literals. `docs/transcript-analysis.md:1520`'s "400-799 lines" names a `review_cost_by_size` bucket, not the threshold. [verified: a tree grep this session for 400 beside "lines", "changed", "diff" or "threshold"]
7. The two hook-header restatements are the drift shape M8 pins, and M8's list predates PR 2's hook. The fact pins them as two more occurrences, a departure from M8. [engineer-verified: "Keep both pins (Recommended)"] A `DocCountFact` occurrence already pins a shell comment, `install.sh:684`'s "so one function reports all 21." [verified: `require-diff-size-prompt.sh:5`, `:68`; `test_doc_counts.py:617-621`; `install.sh:684`]
8. The `DocCountFact` driver runs `re.findall` with no flags, requires exactly one match per occurrence, and parses a digit capture with `int()`. `_count_claude_md_length_hook_limit` is the source-scan ground-truth model, and it checks its own scan with `_assert_exactly_one_match`. [verified: `test_doc_counts.py:76-91`, `:227-250`, `:662-676`, `:707-716`]
9. Each pinned phrase sits on one source line and matches once in its file today. None of the three `plan-review/SKILL.md` phrases is in that file before M7's text lands. [verified: grep this session of `docs/hooks.md:177`, `README.md:192`, `require-diff-size-prompt.sh:5` and `:68`; a grep of `claude-skills/skills/plan-review/` for 400 and for `Projected diff` matched nothing]
10. All 11 ground-truth functions in `test_doc_counts.py` are named `_count_*`, the limit and threshold ones included (`:227`, `:253`, `:267`, `:319`). [verified: grep this session]
11. Every file the new fact reads already selects the hooks test tree in `select-tests.py`, so that file needs no edit. `_lib.sh` and the hook select it through the hooks-directory rule, `plan-review/SKILL.md` through `SKILL_FILES_READ_BY_HOOK_TESTS`, and `docs/` and `README.md` through their own rows. [verified: `select-tests.py:313`, `:622`, `:887-888`]
12. CHANGELOG bullets leave the threshold out, because a changelog entry is a record a later threshold change must not rewrite. M9 has PR 3 add a bullet naming the plan-time question. PR 2's bullet sits at `CHANGELOG.md:536-543`, under `### Added` (`:529`) in `## [Unreleased]` (`:5`). [verified: `diff-size-prompt.md:322`, `:355`; `CHANGELOG.md` read this session]
13. The commit-time prompt fires whether or not the plan-time question was answered. [verified: `diff-size-prompt.md` row 31, engineer-verified there]
14. Once installed, M7's text has `/plan-review` return Request changes on any plan whose Critical files names a file and carries no `Projected diff:` line, for every stow consumer. [verified: M7's text at `diff-size-prompt.md:317`; the repo `CLAUDE.md` § "Working in this repo" on plans affecting all stow users]
15. One `code-writer` dispatch takes all four files, because they share M7's and M8's text as context and no file set is specifiable without restating the other's. [verified: `plan-it/SKILL.md:98-114`]

**Mechanisms:**
- **M1. The plan-time estimate.** Append M7's first sentence (`diff-size-prompt.md:314`) verbatim to the end of `plan-it/SKILL.md:135`, after one space. plan-architect then ends a file-naming plan's Critical files with a `Projected diff:` line.
  anchors: row1, row2, row3, row4
- **M2. The plan-time question.** Append M7's blockquoted text (`diff-size-prompt.md:317`, without its `> ` prefix) verbatim to the end of `plan-review/SKILL.md:96`, after one space. The question rides in the ask-measure `AskUserQuestion`. It is asked once per plan, and `Split now` is recorded as a claim row. Separately, a missing `Projected diff:` line returns Request changes.
  anchors: row1, row2, row3, row13, row14
- **M3. One threshold, pinned.** In `claude/.claude/hooks/tests/test_doc_counts.py`:
  - Two path constants beside `:186-187`: `_LIB_SH_REL_PATH = "claude/.claude/hooks/_lib.sh"` and `_DIFF_SIZE_PROMPT_HOOK_REL_PATH = "claude/.claude/hooks/require-diff-size-prompt.sh"`.
  - `_count_diff_size_prompt_threshold_lines()` after `_count_claude_md_length_hook_limit` (`:227-250`), in its shape. It runs `re.findall(r"^_LIB_DIFF_SIZE_PROMPT_THRESHOLD_LINES=(\d+)$", text, re.MULTILINE)` over `_lib.sh`, passes the matches to `_assert_exactly_one_match` with an `Occurrence` naming that file, and returns `int(matches[0])`. Its docstring is one line: "Return the diff-size prompt threshold from its one assignment in _lib.sh."
  - One `DocCountFact` after the length-hook fact (`:546-559`), labeled `f"_LIB_DIFF_SIZE_PROMPT_THRESHOLD_LINES in {_LIB_SH_REL_PATH}"`. It carries seven occurrences, each a raw-string pattern with a description naming its file and phrase:
    - `claude-skills/skills/plan-review/SKILL.md`: `` `Projected diff:` line states (\d+) or more changed lines ``
    - `claude-skills/skills/plan-review/SKILL.md`: `an estimate; threshold (\d+)\)`
    - `claude-skills/skills/plan-review/SKILL.md`: `` cut to a `Projected diff:` below (\d+) changed lines ``
    - `docs/hooks.md`: `brings the branch's diff against its base to (\d+) or more changed lines`
    - `README.md`: `brings the branch's diff to (\d+) or more changed lines`
    - `_DIFF_SIZE_PROMPT_HOOK_REL_PATH`: `origin/<base> to (\d+) or more changed lines`
    - `_DIFF_SIZE_PROMPT_HOOK_REL_PATH`: `size the (\d+) edge was taken from`
  anchors: row5, row6, row7, row8, row9, row10, row11
- **M4. The CHANGELOG bullet.** One `### Added` bullet under `## [Unreleased]`, directly after PR 2's bullet (`CHANGELOG.md:536-543`), one fact per sentence and no threshold number. It states:
  - in a bold lead, that `/plan-it` and `/plan-review` now ask the diff-size question at plan time;
  - that plan-architect ends a plan's Critical files with a `Projected diff:` line, an estimate of its changed lines leaving out `.claude/plans/`;
  - that `/plan-review` asks split now or proceed anyway once per plan when that line reaches the commit gate's threshold, in the same `AskUserQuestion` as its ask-measure list;
  - the rollout effect: a plan that names a file and has no `Projected diff:` line gets Request changes until one is added;
  - that the commit gate still asks at its threshold whatever the plan-time answer was.
  anchors: row12, row13, row14

## Critical files

One `code-writer` dispatch carries all four files (row 15). The writer reads M7 and M8 in `.claude/plans/diff-size-prompt.md` (`:313-329`) for the verbatim text and patterns, and this plan's M1-M4 for placement and the departure from M8. Its own checks are Verification steps 1-3. The parent runs step 4.

- `claude-skills/skills/plan-it/SKILL.md`: M1, at `:135`.
- `claude-skills/skills/plan-review/SKILL.md`: M2, at `:96`.
- `claude/.claude/hooks/tests/test_doc_counts.py`: M3. Reuse `Occurrence`, `DocCountFact`, `_assert_exactly_one_match` and `REPO_ROOT`. The registry's parametrized test picks the new fact up with no new test method.
- `CHANGELOG.md`: M4.

Session-owned: `.claude/plans/diff-size-plan-check.md`, this plan.

Projected diff: about 75 changed lines (additions plus deletions), leaving out this plan file. The two skill edits change one line each, about 4 lines together. `test_doc_counts.py` gains about 65: two constants, a ground-truth function about the size of the 24-line model, and a seven-occurrence fact at five lines per occurrence. The CHANGELOG bullet is about 5. The figure is an estimate, below the 400 threshold.

## Verification

The `code-writer` dispatch runs steps 1-3 after all four edits land, since step 1's three `plan-review/SKILL.md` patterns match only once M2's text is in. The parent runs step 4 after it returns.

1. `scripts/venv-run.sh pytest -v claude/.claude/hooks/tests/test_doc_counts.py` passes, and its output lists a passing case whose id carries `_LIB_DIFF_SIZE_PROMPT_THRESHOLD_LINES`.
2. `scripts/venv-run.sh ruff check claude/.claude/hooks/tests/test_doc_counts.py` passes.
3. `wc -l claude-skills/skills/plan-it/SKILL.md claude-skills/skills/plan-review/SKILL.md` prints 181 and 302, the counts before the edit (row 3). Neither edit added a line, and both files stay under G1's caps of 200 and 500.
4. `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py -n 2` passes. Run it in the background; the dispatching session estimates about 55 minutes.

## Out of scope

- PR 2's CHANGELOG bullet restates the threshold at `CHANGELOG.md:542` ("denies at 400 lines"), though M8 leaves numbers out of changelog bullets. This PR neither rewrites nor pins it. Another PR's entry is a record (`claude/.claude/CLAUDE.md` § "Working Style", Axis 3 item 2), and unpinned it stays a true statement of the release that ships 400.
- `_lib.sh:4707`'s comment, "Lower edge of the 400-799 bucket", stays unpinned. It sits on the line above the assignment, so an edit to the value shows it, and it states a fact about `review_cost_by_size.py`'s buckets.
- No test pins `plan-it/SKILL.md`'s `Projected diff:` sentence, the producer of the line `/plan-review` requires. The design file leaves that grammar unpinned (`diff-size-prompt.md:436`), and a reworded sentence would show as Request changes on file-naming plans without failing a test.
- The 400 fixture literals in `test_lib_diff_size.py` and `test_require_diff_size_prompt.py` stay literal, under the DAMP-test-code exception in `claude/.claude/CLAUDE.md` § "Engineering Judgment".
- Everything the design file's Out of scope lists after its PR 3 item (`diff-size-prompt.md:426-438`), including a limit on plan-file size and a `plan-shape.py` check of the `Projected diff:` line.
