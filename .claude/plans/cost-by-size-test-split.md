# Split test_transcript_review_cost_by_size.py at a named seam

## Context

Goal: bring `claude/.claude/scripts/tests/test_transcript_review_cost_by_size.py` (1,107 lines) under the 1,000-line limit (`docs/design-decisions/code-file-line-limit.md`) by splitting it at a named, subject-based seam, and move `test_review_cost_by_size_all_accounts_flag_wires_to_all_accounts` from `tests/test_transcript_cli.py` into the review-cost-by-size test file that owns the command's CLI wiring.

Ask: "sounds good" — the engineer's reply, in the prior session, to the session's recommended follow-up order, whose first item is this split (carried by the handoff file). The scope comes from `.claude/plans/review-trace-test-split.md` § Out of scope and PR 1457's body: split at a named seam (never `_part2`, per `.claude/rules/transcript-analysis-tests.md`), move the test, prove the split with `claude/.claude/scripts/tests/_move_fidelity.py test-ids`, and drop the last `TestBuildParser` exception sentence from `docs/transcript-analysis-architecture.md`.

Why now: PR 1457 left the test in `tests/test_transcript_cli.py` because its destination file is over the limit; the move waits on this split.

Evidence for the figures below (class ranges, helper use, registries, reads of docs) sits in the Assumption ledger rows under Approach, each with its source.

## Approach

Split `tests/test_transcript_review_cost_by_size.py` in two at `review_cost_by_size.py`'s own seam between the statistics it computes and prints and the data that feeds them. A new `tests/test_transcript_review_cost_by_size_aggregates.py` takes `_record`, `TestComputeAggregates` and `TestPrintedReport`. The base keeps the PR join, round scan and command tests, and gains `test_review_cost_by_size_all_accounts_flag_wires_to_all_accounts` in a new last class, `TestReviewCostBySizeArgparseWiring`. Whole top-level nodes move verbatim under their own names, so no helper serves both files and `_move_fidelity.py test-ids` proves the split with one `--rename`, for the CLI test.

Paths below are under `claude/.claude/scripts/` unless they start at the repository root. A bare `:N` is a line of `tests/test_transcript_review_cost_by_size.py` at `<base>`, the merge-base.

**Manifest** (rows 1, 3, 4, 6).
- `tests/test_transcript_review_cost_by_size_aggregates.py`, about 296 lines, in this order:
  - the module docstring (below);
  - an import block of `import math`, `import pytest` and `from transcript_analysis import review_cost_by_size`, in the order ruff's `I` rule sets;
  - `_record` (:80-81);
  - `TestComputeAggregates` and `TestPrintedReport` (:138-422), unchanged.
- `tests/test_transcript_review_cost_by_size.py`, about 822 lines:
  - the module docstring (:1-5) is replaced (below);
  - `import math` (:6) goes, and `cli` joins the `from transcript_analysis import` line at :16;
  - `_record` and the two blank lines after it (:80-83) go;
  - :138-424 goes, which is both moved classes and the two blank lines after them;
  - a new last class follows `TestCmdReviewCostBySize`: `class TestReviewCostBySizeArgparseWiring:`, holding the test verbatim from `tests/test_transcript_cli.py:327-334`.
- Every other line stays, with its bytes.

Each module docstring is one line:
- base: `"""Tests for review_cost_by_size.py's PR join, code-review round scan, command, and CLI wiring."""`
- `_aggregates`: `"""Tests for review_cost_by_size.py's aggregates and its printed aggregate and join-diagnostics lines."""`

**Seams set aside** (rows 4, 5, 18):
- A `_command` file taking `TestCmdReviewCostBySize` and the CLI test, with the join and scan tests left in the base, was the dispatching session's proposal. `_review_cost_by_size_args`, `_merged_pr_sized`, `_write_code_review_session` and `_PROJECT_SLUG` serve both of its sides (row 5). G2 would then put them in a new `tests/_review_cost_by_size_helpers.py`, a third file, and the move would grow from about 290 lines to about 540. Importing them from the base test module instead breaks the same rule.
- Moving only `TestComputeAggregates` leaves the base at about 935 lines, and `_record` would serve both files. `TestPrintedReport` also calls `_compute_aggregates` (:315), so that cut splits one subject across two files.
- Splitting `TestPrintedReport` so its two `_print_join_diagnostics` tests (:396-422) stay in the base needs a same-named class in each file and a method-level byte check. Those two tests pin a report printer beside `_print_aggregates`, under a class docstring (:311) that names both, and the `_aggregates` docstring names them too.
- Renaming the base to a seam name changes every reference to it for no placement gain. G2 lets the base keep the module's own name.

The base lands about 178 lines under the limit. If it later grows past the limit, the `_command` seam above, with its helper file, is the next cut.

**The CLI test** (rows 7, 8, 20). `TestReviewCostBySizeArgparseWiring` takes the `Test<Command>ArgparseWiring` shape nine test files use. It lands last in the base, as `TestReviewTraceArgparseWiring` did in the review-trace split. The moved test calls `cli.build_parser()` and reads `review_cost_by_size.cmd_review_cost_by_size`, so the base adds only `cli` to its import. The base is the file named for the module that registers the subparser, which is where G2's CLI-wiring bullet puts the test. `TestCmdReviewCostBySize`, which drives `cmd_review_cost_by_size` with hand-built args, was set aside as the test's class, because the wiring class is where each flag's parse test belongs.

`tests/test_transcript_cli.py` loses :326-334, the method and the blank line before it. Its `from transcript_analysis import` line at :7 becomes `from transcript_analysis import cli, scope`. The moved test holds the name's only two code reads in the file (row 7).

**Registries** (rows 9-11):
- `tests/test_transcript_package_module_prefixes.py`: `"test_transcript_review_cost_by_size_aggregates.py",` joins `TEST_FILES` after :125.
- No `MODULES_TESTED_ELSEWHERE` entry (row 10).
- No `select-tests.py` or `tests/test_select_tests.py` edit. The two doc-path constants (:30-31) stay in the base, and no exact-path constant or known cross-domain read names the file (row 11).

**Prose** (row 19), in `docs/transcript-analysis-architecture.md`:
- :783-787, the exception paragraph and the blank line after it, goes.
- :805 becomes the three lines below. The deleted paragraph is the doc's only mention of the file, and the section records split modules' test files, as :790-805 does for `scope.py` and `review_trace.py`.

```text
`tests/test_transcript_review_trace_spawn_rounds.py`), `review_cost_by_size.py`'s in
`tests/test_transcript_review_cost_by_size.py` and `tests/test_transcript_review_cost_by_size_aggregates.py`,
`read_scope.py`'s in
```

**Assumption ledger.**

**Root:** `tests/test_transcript_review_cost_by_size.py` is 1,107 lines, over the 1,000-line limit. `test_review_cost_by_size_all_accounts_flag_wires_to_all_accounts` waits in `tests/test_transcript_cli.py` as the architecture doc's last documented exception until that file splits. This change splits the file at a named seam and moves that test, with no test added, dropped or edited. `[verified: read of the file; code-file-line-limit.md:19; architecture doc :783-786]`

**Givens:**
- G1. The limit is 1,000 lines for test files as for production files. Reason: `docs/design-decisions/code-file-line-limit.md` owns it, and revisiting it is outside this plan. `[verified: code-file-line-limit.md:19]`
- G2. `.claude/rules/transcript-analysis-tests.md` sets placement. A split module's tests go in `tests/test_transcript_<module>_<seam>.py`, never a positional name. One command's CLI wiring goes in the file of the module that registers its subparser. One family's shared helpers go in `tests/_<family>_helpers.py`. Reason: the rule file owns placement, and changing it is outside this plan. `[verified: read of the rule]`
- G3. Agents run `select-tests.py`, not the full suite, unless it selects the full suite itself. Reason: the repo `CLAUDE.md` § "Commands" sets it. `[verified: CLAUDE.md "Commands"]`
- G4. Some tests fail on this machine for environmental reasons, at the merge-base as on a branch. Reason: the machine's environment lies outside the repository. `[unverified: relayed by the dispatching session from the prior PR's run]`

**Rows:**
1. The base file is 1,107 lines. Its docstring is :1-5, `import math` is :6, and `from transcript_analysis import gh_cli, review_cost_by_size, scope` is :16. Module constants run :29-49 and helpers :52-135, with `_record` at :80-81 between the blank lines :78-79 and :82-83. Then come `TestComputeAggregates` (:138-307, 16 `test_*` methods), `TestPrintedReport` (:310-422, 8 and four helpers), `TestJoinRoundsToPrs` (:425-605, 7), `TestScanCodeReviewRounds` (:608-700, 4) and `TestCmdReviewCostBySize` (:703-1107, 18 and `_two_root_corpus`). `[verified: read of :1-140, :300-435, :600-770, :1086-1107; grep of class and method lines]`
2. `review_cost_by_size.py`'s top-level functions fall on one seam. `_scan_code_review_rounds` (:78) and `_join_rounds_to_prs` (:97) build the per-PR records. `_spearman_rho` (:151) through `_compute_aggregates` (:207) compute the statistics, and `_print_aggregates` (:256) and `_print_join_diagnostics` (:280) print them. `_print_pr_rows` (:302), `cmd_review_cost_by_size` (:312) and `_add_review_cost_by_size_subparser` (:363) are the command. `[verified: grep of top-level defs; roles read from names and from the tests that call them]`
3. :138-422 reads `_compute_aggregates`, `_log_log_slope`, `_MEASURES`, `_print_aggregates`, `_JoinCounts`, `_print_join_diagnostics` and, through `_record`, `_PrRecord`. :425-1107 reads `_join_rounds_to_prs`, `_JoinCounts`, `_scan_code_review_rounds`, `cmd_review_cost_by_size` and `_PRIVATE_VIEW_BANNER`. `[verified: grep -o of "review_cost_by_size\.\w+"]`
4. Of the file's module-level names, :138-422 reads only `_record`, `math`, `pytest` and `review_cost_by_size`. `_record` is read only at :144-391, and `math` only at :177 and :248. None of the 24 moved `test_*` methods is parametrized, since the file's `parametrize` marks sit at :561, :598, :704, :845, :935, :961, :989 and :1011. `[verified: grep of the file for underscore-prefixed names, "math", "parametrize"]`
5. Under a `_command` cut, four names would serve both files. `_review_cost_by_size_args` is read in the scan class (:633-694) and the command class (:716 on). `_merged_pr_sized` is read in the join class (:457-612), the scan class (:661) and the command class (:753 on). `_write_code_review_session` is read in the scan class (:626-654) and the command class (:745 on). `_PROJECT_SLUG` is read in the scan class (:624-694) and the command class (:729 on). `[verified: grep]`
6. The file's top-level comments sit at :29, :36, :38, :40-41, :46-47 and :112-113, none inside :80-83 or :136-424. `[verified: grep of lines starting "# "]`
7. In `tests/test_transcript_cli.py`, the moved test is :327-334, the last method of `TestBuildParser` (:278-334), with blank lines at :326 and :335-336. `review_cost_by_size` is imported at :7 and read in code only at :331 and :333. At :218-219 the string occurs only inside a function name and a docstring. `[verified: read of :1-30, :200-340; grep]`
8. Nine test files carry a `Test<Command>ArgparseWiring` class: `test_transcript_cache_rebuild.py:535`, `test_transcript_review_trace.py:553`, `test_transcript_skill_invocation.py:653`, `test_transcript_cache_efficiency.py:385`, `test_transcript_pr_cost_export.py:934`, `test_transcript_pr_link.py:390`, `test_transcript_plan_boundary.py:539`, `test_transcript_read_cost_command.py:17` and `test_transcript_cache_rebuild_ttl_rules.py:37`. `TestReviewTraceArgparseWiring` calls `cli.build_parser()`, and its file imports `cli` in its `from transcript_analysis import` block (:4-8). `[verified: grep; read]`
9. `test_loader_free_package_importing_test_files_are_in_test_files` (`tests/test_transcript_package_module_prefixes.py:522-533`) fails any test file that imports `transcript_analysis`, loads no shim, and is missing from `TEST_FILES` (:61). The base is listed at :125. `[verified: read]`
10. `_owning_module` assigns a test file to the longest package module its stem matches as `test_transcript_<module>` or `test_transcript_<module>_...` (`tests/test_transcript_analysis_module_test_files.py:33-39`). `review_cost_by_size.py` is the only package module whose name starts `review_cost`, and `MODULES_TESTED_ELSEWHERE` (:13) has no entry for it. `[verified: read; glob of transcript_analysis/review_cost*; grep]`
11. Outside `.claude/plans/`, the base's name occurs only at `tests/test_transcript_package_module_prefixes.py:125` and `docs/transcript-analysis-architecture.md:785`. `select-tests.py:923-926` maps the architecture doc, `docs/transcript-analysis.md` and `docs/private-project-redaction.md` to `SCRIPTS_TESTS_DIR`, so the doc reads behind :30-31 need no exact-path constant. `tests/test_select_tests.py` does not name the file. `[verified: grep of the repository; read of select-tests.py]`
12. This diff's `select-tests.py` run is scoped, not the full suite. Its paths match the `SCRIPTS_DIR` rule (`select-tests.py:639`), the architecture-doc row (:923), the `DOCS_DIR` rule (:928), `_is_py_source_under_claude_or_plugins` (:941) and `_is_test_tree_packaging_change` (:942). The expected targets are therefore the scripts, hooks and skills test directories plus `claude/.claude/tests`. `.claude/plans/` maps to no tests (:645). None of the paths is in `GLOBAL_TRIGGER_PATHS` (:480-484). `[verified: read]`
13. `test-ids` keys each testcase as `Class::name`, with the module dropped (`tests/_move_fidelity.py:741-746`). `--rename` maps a whole before ID or its leading `::` part (:771-775). The command reports `id-outcome`, `id-missing` and `id-unexpected` findings (:797-808). `[verified: read]`
14. `nodes` takes a repeatable `--after` and the `--moved` and `--verbatim` lists (`tests/_move_fidelity.py:883-889`), and compares top-level nodes only. `remainder` takes `--removed`, `--added` and `--changed`, with `__doc__` naming the module docstring, plus `--import-added` and `--import-removed` triples of the form `module:name` or a bare name (:891-899, :354-358). It compares the kept statements and the comment blocks outside the declared names and the imports (:447-477). Neither reaches a method of a class that stays. `[verified: read]`
15. Neither the base file nor `tests/test_transcript_cli.py` carries a `timing` mark. `[verified: grep for "mark.timing" and "pytestmark"]`
16. `claude/.claude/hooks/tests/test_ticket_reference_discipline.py` builds its corpus from `git ls-files` (:36, :279), so its per-file checks skip an untracked file. `select-tests.py` selects it for any `.py` change under `claude/` (:941). `[verified: read; grep]`
17. ruff runs with `line-length = 130` and the rules `E`, `F`, `B`, `I`, `UP` and `SIM` (`pyproject.toml:1-6`). The two docstrings run about 98 and 105 columns. `[verified: read; column counts of the prescribed lines]`
18. The base lands at about 822 lines and the `_aggregates` file at about 296. `[unverified: arithmetic over row 1's ranges; Verification step 2 measures it]`
19. `docs/transcript-analysis-architecture.md:783-786` is the exception paragraph, followed by a blank line at :787, and is the doc's only `TestBuildParser` mention. :800-806 lists per-module test files, including `review_trace.py`'s seam files at :802-805. The doc names no `review_cost_by_size.py` test file elsewhere. It lists none of the `test_transcript_read_cost_*.py` files that `TEST_FILES` holds at :129-133. `[verified: read of :778-870; grep]`
20. The review-trace split placed `TestReviewTraceArgparseWiring` last in its base and proved its move with `test-ids`, `nodes` and `remainder` (`.claude/plans/review-trace-test-split.md:30`, :186-220). `[verified: read]`
21. pytest cannot collect a lone `git show` copy of the base file, because its `from ._pr_cost_helpers import` and `from .conftest import` (:18-19) need the tests package around it. `[unverified]`
22. The Ask line's "sounds good" is the engineer's reply, in a prior session, to the session's follow-up order with this split first. The handoff file carried it, and this session asked the engineer whether they said it and want this split next. `[engineer-verified: "Yes, proceed"]`, the label selected for that question. The seam, the architecture doc's new list entry and the dispatch shape are this plan's choices, and the engineer has answered no question on them. `[unverified]`
23. This plan's projected diff of about 630 changed lines goes ahead as one PR, past the 400-line gate. `[engineer-verified: "Proceed anyway"]`

**Mechanisms:**
- M1: Two files at the module's statistics seam, per the manifest. `anchors: root, row1, row2, row3, row4, row18`. The alternatives are under "Seams set aside".
- M2: A verbatim move of whole top-level nodes under their own names. No class splits across files, so no moved ID changes. `anchors: row1, row4, row6, row13`.
- M3: No family helper file, because no helper serves both files. `anchors: row4, row5`.
- M4: The CLI test lands in the base's new last class, and the CLI file's import drops `review_cost_by_size`. `anchors: row7, row8, row20`.
- M5: One registry edit, the `TEST_FILES` entry. `anchors: row9, row10, row11`.
- M6: The two architecture doc edits. `anchors: row19`.
- M7: The fidelity proof: a merge-base worktree, `test-ids`, `nodes --verbatim`, two `remainder` runs, and a `cmp` of the moved CLI test. `anchors: row12, row13, row14, row15, row16, row21`. The merge-base worktree is heavier than `test-ids` alone. Lighter primitives checked for it:
  - Running the before pass in this worktree before the first edit works once, but no reviewer can rerun it after the move lands.
  - `git show <base>:<path>` into `<scratch>` serves `nodes`, `remainder` and the `cmp`, but pytest cannot run that copy (row 21).
  - `git stash` is unsafe here, because the stash stack is shared across worktrees.

  The dispatching session proposed an uncommitted method-check script like the review-trace split's. That script served 103 methods split across files. Here no class splits (row 4).
  `nodes --verbatim` covers every moved class whole.
  One 8-line method leaves a class that stays.
  A `cmp` of its two extracted ranges proves its bytes.
  `git diff --numstat` bounds what `TestBuildParser` lost. Primitives checked in place of the `cmp`:
  - `test-ids` alone proves the ID and outcome, so an edit that still passes goes unseen.
  - `nodes` reaches only top-level statements (row 14).
  - `git diff --color-moved` marks moved lines for a reader but gives no exit status.
- M8: One `code-writer` dispatch. `anchors: root, row1, row7`. The reason is under Critical files.

## Critical files

**Dispatch: one `code-writer`, without a split.** The `_aggregates` file takes its content from the base file. The CLI test's two ends are one move. The `TEST_FILES` entry and the doc lines name the new file. So no two parts have disjoint inputs (rows 1, 7). Dispatch it with `model: sonnet` and without `isolation: "worktree"`. The brief requires:
- Verification steps 1-12, run by the agent, with each command's output and exit status in the return, including the `select-tests.py` summary, any failing node IDs, and their merge-base result;
- no edit to any moved line;
- staging only the `_aggregates` file, at Verification step 9, and no commits.

**Create:**
- `claude/.claude/scripts/tests/test_transcript_review_cost_by_size_aggregates.py`.

**Modify, tests:**
- `claude/.claude/scripts/tests/test_transcript_review_cost_by_size.py`;
- `claude/.claude/scripts/tests/test_transcript_cli.py` (:7 and :326-334 only);
- `claude/.claude/scripts/tests/test_transcript_package_module_prefixes.py` (`TEST_FILES` only).

**Modify, other:**
- `docs/transcript-analysis-architecture.md` (:783-787 and :805 only).

**Also in the diff:** `.claude/plans/cost-by-size-test-split.md`.

**Reuse:** `tests/_move_fidelity.py`'s `test-ids`, `nodes` and `remainder`; `cli.build_parser()`; `sed`, `tail` and `cmp` for the moved method. No new helper, committed script or scratch script.

**PR body requirement.** The PR body carries:
- Verification steps 4-8 with their outputs;
- a list of every authored hunk:
  - the two module docstrings;
  - the two import blocks, and the CLI file's import line;
  - the `class TestReviewCostBySizeArgparseWiring:` line;
  - the `TEST_FILES` entry;
  - the two architecture doc edits.

With that list, a reviewer rereads only the authored lines and reruns the proof for the moved ones.

Projected diff: about 630 lines. About 298 leave `test_transcript_review_cost_by_size.py` and about 13 enter it, about 296 make up the `_aggregates` file, 11 change in `test_transcript_cli.py`, 1 in `test_transcript_package_module_prefixes.py`, and about 9 in the architecture doc. About 590 of the total is the verbatim move.

## Verification

Run from the worktree root. `<scratch>` is a new directory outside the repository from `mktemp -d`, `<base>` is the SHA `git merge-base HEAD origin/main` prints, and `<tests>` is `claude/.claude/scripts/tests`.

1. Before side, from the merge-base:
   - `git worktree add --detach <scratch>/base <base>`
   - With `<scratch>/base` as the working directory, run `scripts/venv-run.sh pytest <tests>/test_transcript_review_cost_by_size.py <tests>/test_transcript_cli.py --junitxml=<scratch>/before.xml`. One pass covers both files (row 15).
2. `wc -l <tests>/test_transcript_review_cost_by_size.py <tests>/test_transcript_review_cost_by_size_aggregates.py`: each is under 1,000, near 822 and 296.
3. `scripts/venv-run.sh ruff check claude/.claude/ claude-skills/ plugins/ evals/` is clean.
4. Test IDs:
   - `scripts/venv-run.sh pytest <tests>/test_transcript_review_cost_by_size.py <tests>/test_transcript_review_cost_by_size_aggregates.py <tests>/test_transcript_cli.py --junitxml=<scratch>/after.xml`
   - `scripts/venv-run.sh python3 <tests>/_move_fidelity.py test-ids --before <scratch>/before.xml --after <scratch>/after.xml --rename TestBuildParser::test_review_cost_by_size_all_accounts_flag_wires_to_all_accounts=TestReviewCostBySizeArgparseWiring::test_review_cost_by_size_all_accounts_flag_wires_to_all_accounts` exits 0 and prints equal before and after counts. Any finding blocks. Never add a `--rename` or `--expect-new` to clear one.
5. Moved nodes: `scripts/venv-run.sh python3 <tests>/_move_fidelity.py nodes --before <scratch>/base/<tests>/test_transcript_review_cost_by_size.py --after <tests>/test_transcript_review_cost_by_size_aggregates.py --moved _record TestComputeAggregates TestPrintedReport --verbatim _record TestComputeAggregates TestPrintedReport` exits 0 and prints `compared-nodes: 3`.
6. Base remainder: `scripts/venv-run.sh python3 <tests>/_move_fidelity.py remainder --before <scratch>/base/<tests>/test_transcript_review_cost_by_size.py --after <tests>/test_transcript_review_cost_by_size.py --removed _record TestComputeAggregates TestPrintedReport --changed __doc__ --added TestReviewCostBySizeArgparseWiring --import-added transcript_analysis:cli --import-removed math` exits 0.
7. CLI-file remainder: `scripts/venv-run.sh python3 <tests>/_move_fidelity.py remainder --before <scratch>/base/<tests>/test_transcript_cli.py --after <tests>/test_transcript_cli.py --changed TestBuildParser --import-removed transcript_analysis:review_cost_by_size` exits 0.
8. The moved CLI test:
   - `git diff HEAD --numstat -- <tests>/test_transcript_cli.py` prints `1`, `10` and the path: the import line, and the method with one blank line.
   - `sed -n '327,334p' <scratch>/base/<tests>/test_transcript_cli.py > <scratch>/cli_test_before.txt`. Its first line is `    def test_review_cost_by_size_all_accounts_flag_wires_to_all_accounts(self):`.
   - `tail -n 8 <tests>/test_transcript_review_cost_by_size.py > <scratch>/cli_test_after.txt`. `tail -n 9 <tests>/test_transcript_review_cost_by_size.py` starts with `class TestReviewCostBySizeArgparseWiring:`.
   - `wc -l` prints 8 for each of the two files, so the `cmp` cannot pass on empty files.
   - `cmp <scratch>/cli_test_before.txt <scratch>/cli_test_after.txt` exits 0.
9. `git add -- <tests>/test_transcript_review_cost_by_size_aggregates.py`, so `test_ticket_reference_discipline.py`'s corpus includes it (row 16).
10. `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py`. Its stderr names `claude/.claude/scripts/tests` among its targets and does not report the full suite (row 12). Then:
    - These pass:
      - `test_transcript_package_module_prefixes.py`: `test_loader_free_package_importing_test_files_are_in_test_files` and `test_test_files_reference_only_real_module_attributes`.
      - `test_transcript_analysis_module_test_files.py`: `test_every_package_module_has_a_named_test_file_or_an_entry`.
      - `test_select_tests.py`: `TestCrossDomainReadCompleteness`.
      - `claude/.claude/hooks/tests/test_ticket_reference_discipline.py`, for the new file: its cases of `test_no_ticket_prefixed_identifier` and `test_no_duplicate_top_level_test_name_within_a_file`. The run output shows a case for each test whose ID contains `test_transcript_review_cost_by_size_aggregates.py`, and a missing case fails.
    - Step 4 already judges a failure in its three files, since it compares each outcome with the merge-base.
    - An ID that fails only on this tree blocks. To decide, rerun every other failing node ID with `<scratch>/base` as the working directory, per `CLAUDE.md`'s "Prove your change caused a failing check", and compare node IDs, not counts (G4).
11. References:
    - `git grep -n 'test_transcript_review_cost_by_size' -- ':!.claude/plans/'` prints three lines: the two `TEST_FILES` entries and the architecture doc's new line.
    - `git grep -n test_review_cost_by_size_all_accounts_flag_wires_to_all_accounts -- ':!.claude/plans/'` prints only the base file's definition.
    - `git grep -n TestBuildParser -- docs/transcript-analysis-architecture.md` prints nothing.
    - `git ls-files --error-unmatch` exits 0 for each `tests/test_transcript_review_cost_by_size*.py` path the architecture doc now names, run after step 9.
12. `git worktree remove <scratch>/base`. Run it on every exit, a blocked run included, and state in the return whether it was removed.

## Out of scope

- **The other GH-1432 follow-ups:** the four stale plan citations, the set-not-shrinking invariant test, and the in-process round-trip tests of each registrar plus `cmd_sessions --paths`. Each is its own change.
- **A `_command` split of the base and a `tests/_review_cost_by_size_helpers.py`.** They are set aside under "Seams set aside", and the base stays under the limit without them.
- **Any edit inside a moved test or class**, docstring cross-references included. Verification steps 4-8 prove a pure move, and nothing else.
- **The `cli.main()` tests at `tests/test_transcript_cli.py:218-275`.** The architecture doc names only the `TestBuildParser` test as an exception, so they stay.
- **Recording `read_cost.py`'s seam files in the architecture doc's per-module list** (row 19). That gap predates this change and is not its subject.
- **Mentions of the old layout in `.claude/plans/*.md`.** They are preserved records (`CLAUDE.md` §Working Style "Scope discipline", Axis 3).
- **The line limit's enforcing check.** It waits on the offender assessment `docs/design-decisions/code-file-line-limit.md` names.
