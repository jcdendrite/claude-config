# Split test_transcript_review_trace.py at named seams

## Context

Goal: bring `claude/.claude/scripts/tests/test_transcript_review_trace.py` (2,695 lines) under the 1,000-line limit (`docs/design-decisions/code-file-line-limit.md`) by splitting it at named, subject-based seams, and move `test_review_trace_parses_omit_plan_review_spawns` from `tests/test_transcript_cli.py` into the review-trace test file that owns `review_trace.py`'s CLI wiring.

Ask: "close out GH-1432 and continue to the follow-up PRs please." (engineer, this session). The first follow-up and its scope come from `.claude/plans/cli-subparser-move.md` § Out of scope and PR 1451's body: split the file at a named seam (never `_part2`, per `.claude/rules/transcript-analysis-tests.md`), move the test, prove the split with `claude/.claude/scripts/tests/_move_fidelity.py test-ids`, and drop the second `TestBuildParser` exception sentence from `docs/transcript-analysis-architecture.md`. Which follow-up goes first is the session's recommendation, not the engineer's choice.

Why now: PR 1451 left the test in `test_transcript_cli.py` because the destination file was over the limit; the move waits on this split.

Evidence gathered (exploration subagent, read-only; line numbers approximate and to be re-checked by the plan):
- `TestReviewTrace` is lines 48-1895; `TestReviewerSpawnRoundLabels` is 1943-2607 with its own self-contained helpers (`_spawn_rec`, `_bash_rec`, `_spawn_rounds`, plan-review/ready-for-review SKILL.md helpers); two small classes follow (`TestReviewTracePreBranchAttribution`, `TestComputeDenySummaryDataGroupBoundaryFreshRead`).
- The whole file tests one module, `transcript_analysis/review_trace.py`. A new `test_transcript_review_trace_<seam>.py` is owned by `review_trace` through the prefix match in `test_transcript_analysis_module_test_files.py`, so `MODULES_TESTED_ELSEWHERE` needs no entry.
- A new test file that imports `transcript_analysis` must be added to `TEST_FILES` in `test_transcript_package_module_prefixes.py`.
- Only the spawn-round tests read `plan-review/SKILL.md` and `ready-for-review/SKILL.md`. `select-tests.py` pins `TRANSCRIPT_REVIEW_TRACE_TEST_PATH` to the file name. `test_select_tests.py` lists the file in `_KNOWN_CROSS_DOMAIN_READS`. Both references follow the SKILL.md-reading group wherever it lands.
- `review_trace.py:562` (docstring) and `claude/.claude/tests/helpers.py:3673` (comment) name the file; the doc names it at `docs/transcript-analysis-architecture.md` lines 783-787 and 803-804. Older `.claude/plans/*.md` mentions are preserved records and stay.
- `_move_fidelity.py test-ids` keys on `Class::name` with the module dropped, so IDs survive a file move; the moved test's ID changes only if its class name does.

## Approach

Split `tests/test_transcript_review_trace.py` into four files along `review_trace.py`'s own function seams, and move `test_review_trace_parses_omit_plan_review_spawns` into the base file. The base keeps the timeline tests and gains that CLI wiring test. `_detection` takes the per-record event tests, `_deny_summary` the `--deny-summary` tests, and `_spawn_rounds` the reviewer-spawn round labels. Every test moves verbatim and keeps its `Class::name` ID, so `_move_fidelity.py test-ids` proves the split with one `--rename`, for the CLI test.

Paths below are under `claude/.claude/scripts/` unless they start at the repository root. A bare `:N` is a line of `tests/test_transcript_review_trace.py` at `<base>`, the merge-base.

**Manifest** (rows 1, 3-6). A range runs from its first named method's first line, decorators included, to its last named method's last line.
- `tests/test_transcript_review_trace.py`, about 570 lines, in this order:
  - the module docstring (replaced, below), the trimmed import block, `_since_until_epochs` (:36-40), and the `review-trace` separator comment (:43-45);
  - `class TestReviewTrace:` with :575-848 (the GH-896 section header through `test_record_with_no_timestamp_excluded_no_crash`), :866-878 (`test_until_subsecond_record_included`), and :1002-1166 (`test_deny_only_plain_timeline_restricts_to_denial_sessions` through `test_dedup_before_branch_filter_pins_ordering`, the GH-482 header included), 24 methods;
  - `TestReviewTracePreBranchAttribution` (:2609-2667), unchanged;
  - a new last class, `TestReviewTraceArgparseWiring`, holding `test_review_trace_parses_omit_plan_review_spawns` verbatim from `tests/test_transcript_cli.py:336-340`.
- `tests/test_transcript_review_trace_detection.py`, about 845 lines: `class TestReviewTrace:` with :49-573 (`test_skill_invocation_appears_in_output` through `test_sidechain_skill_invocation_now_detected`, the architect-consult header included), :850-864 (`test_deny_only_restricts_to_denial_sessions`), :880-1000 (`test_denial_blockingError_key_used_for_display_message` through `test_deny_only_matches_current_format_denial`), :1433-1570 (`test_absent_toolDenialKind_produces_no_friction_event` through `test_unrecognized_toolDenialKind_prints_as_other_kind_not_raw_value`), and :1589-1605 (`test_friction_only_session_renders_timeline_line_default_output`), 52 methods.
- `tests/test_transcript_review_trace_deny_summary.py`, about 620 lines: `class TestReviewTrace:` with :1168-1431 (`test_deny_summary_groups_by_hook_and_command_shape` through `test_deny_summary_with_matching_session_but_zero_denials_prints_explicit_message`), :1572-1587 (`test_friction_only_session_survives_deny_only_with_deny_summary`), and :1607-1894 (`test_deny_summary_prints_corpus_window` through `test_deny_summary_real_corpus_shapes_all_classify_no_other_or_unmatched`), 27 methods. Then `TestComputeDenySummaryDataGroupBoundaryFreshRead` (:2670-2694), unchanged.
- `tests/test_transcript_review_trace_spawn_rounds.py`, about 735 lines: :1897-2606 unchanged. That is `_spawn_rec`, `_PLAN_REVIEW_SKILL_MD`, `_deactivate_plan_review`, `_write_plan_review`, `_expand_recipe_placeholders`, `_READY_FOR_REVIEW_SKILL_MD`, `_bash_rec`, `_spawn_rounds`, and `TestReviewerSpawnRoundLabels`.

Moved methods keep their relative base order, their four-space indentation and their bytes. Each file imports exactly the names its code reads, and ruff's `F` and `I` rules settle that set and its order (row 23). No `tests/_review_trace_helpers.py` is needed, because no helper outside `tests/conftest.py` serves two of the four files (row 6).

Each file's module docstring is one line:
- base: `"""Tests for review_trace.py's timeline: sidechain merge, --since/--until, --deny-only, branch attribution, and CLI wiring."""`
- `_detection`: `"""Tests for the events review_trace.py emits per record: skills, denials, reviewer spawns, consults, and friction."""`
- `_deny_summary`: `"""Tests for review_trace.py's --deny-summary: compute_deny_summary_data and its printed tables."""`
- `_spawn_rounds`: `"""Tests for review_trace.py's reviewer-spawn round labels and --omit-plan-review-spawns."""`

**Seams set aside** (rows 4, 25):
- Moving only the spawn-round block out leaves the base at about 1,985 lines.
- Three seam files (spawn rounds, a deny summary that also takes the friction tests, and sidechain merge) leave the base at about 1,000 lines with no headroom. They also file friction under deny summary, although its tests call the event builder.
- A fifth file for friction or for architect consults adds a `TEST_FILES` entry and doc text for a subject `_detection` already holds under the limit.
- Renaming the base to a seam name such as `_timeline` changes every reference to it for no placement gain. The rule lets the base keep the module's own name (G2).

**Class names** (rows 16, 24, 27). Methods that leave `TestReviewTrace` land in a class of the same name in their seam file, so no moved ID changes. The class's name-only mentions at `tests/test_transcript_denials.py:23`, :68, :96 and `docs/design-decisions/review-trace-fifth-event-kind.md:19` stay accurate unedited. Seam-named classes such as `TestReviewTraceDetection` were set aside. `test-ids --rename` maps only a whole ID or a whole leading class part, so they would need 79 per-test renames (52 plus 27), and the proof would become a check of a hand-built list.

**The CLI test** (rows 18, 19). `TestReviewTraceArgparseWiring` takes the `Test<Command>ArgparseWiring` shape eight transcript-analysis test files use. The moved test keeps calling `cli.build_parser()`, so the base adds `cli` to its `from transcript_analysis import` block. The base is the file named for the module, which is where the rule's CLI-wiring bullet and the architecture doc put the test (G2). The spawn-rounds file, home of the flag's behavior tests, was set aside: `_add_review_trace_subparser` registers every review-trace flag, and the wiring class is where each flag's parse test belongs. `tests/test_transcript_cli.py` loses :335-340.

**Registries** (rows 7, 10-15):
- `select-tests.py`: rename `TRANSCRIPT_REVIEW_TRACE_TEST_PATH` to `TRANSCRIPT_REVIEW_TRACE_SPAWN_ROUNDS_TEST_PATH` at :140, :735 and :902, with the value `"claude/.claude/scripts/tests/test_transcript_review_trace_spawn_rounds.py"`. The file name in the comments at :137 and :730 becomes `test_transcript_review_trace_spawn_rounds.py`.
- `tests/test_select_tests.py`: the constant at :2209, and both reader paths at :2260 and :2262, which become the spawn-rounds path.
- `tests/test_transcript_package_module_prefixes.py`: the three new file names join `TEST_FILES` after :90.
- No `MODULES_TESTED_ELSEWHERE` entry (row 15).

**Prose references** (rows 8, 19, 20):
- `transcript_analysis/review_trace.py:562`: `test_transcript_review_trace.py` becomes `test_transcript_review_trace_spawn_rounds.py`, in place.
- `claude/.claude/tests/helpers.py:3673`: `test_transcript_review_trace.py's` becomes `test_transcript_review_trace_detection.py's`, in place. The line stays under 130 columns, so the paragraph is not rewrapped.
- `docs/transcript-analysis-architecture.md:783-787` becomes: "`tests/test_transcript_cli.py` holds one test that is an exception to the rule's bullet on one command's CLI wiring, in `TestBuildParser`. `test_review_cost_by_size_all_accounts_flag_wires_to_all_accounts` belongs in `tests/test_transcript_review_cost_by_size.py`, which is over the line limit, so the move waits for a split of that file at a named seam."
- `docs/transcript-analysis-architecture.md:803-804`: "`review_trace.py`'s in `tests/test_transcript_review_trace.py`," becomes "`review_trace.py`'s in `tests/test_transcript_review_trace.py` and three seam files (`tests/test_transcript_review_trace_detection.py`, `tests/test_transcript_review_trace_deny_summary.py`, `tests/test_transcript_review_trace_spawn_rounds.py`),", rewrapped to the paragraph.

**Assumption ledger.**

**Root:** `tests/test_transcript_review_trace.py` is 2,695 lines, over the 1,000-line limit, and `test_review_trace_parses_omit_plan_review_spawns` waits in `tests/test_transcript_cli.py` as a documented exception until that file splits. This change splits the file at named seams and moves that test, with no test added, dropped or edited. `[verified: read of the file; code-file-line-limit.md:19; architecture doc :783-787]`

**Givens:**
- G1. The limit is 1,000 lines for test files as for production files. Reason: `docs/design-decisions/code-file-line-limit.md` owns it, and revisiting it is outside this plan. `[verified: code-file-line-limit.md:19]`
- G2. `.claude/rules/transcript-analysis-tests.md` sets placement. A split module's tests go in `tests/test_transcript_<module>_<seam>.py`, never a positional name. One command's CLI wiring goes in the file of the module that registers its subparser. One family's shared helpers go in `tests/_<family>_helpers.py`. Reason: the rule file owns placement, and changing it is outside this plan. `[verified: read of the rule]`
- G3. Agents run `select-tests.py`, not the full suite, unless it selects the full suite itself. Reason: the repo `CLAUDE.md` § "Commands" sets it. `[verified: CLAUDE.md "Commands"]`
- G4. A full run on this machine has about 97 environmental failures. Reason: the machine's environment lies outside the repository. `[unverified: relayed by the dispatching session from the prior PR's run]`

**Rows:**
1. The base file holds its imports at :1-33, `_since_until_epochs` at :36-40, a separator comment at :43-45, and `TestReviewTrace` at :48-1894 with 103 methods, every one named `test_*`. Then come the spawn-round helpers at :1897-1940, `TestReviewerSpawnRoundLabels` at :1943-2606, `TestReviewTracePreBranchAttribution` at :2609-2667, and `TestComputeDenySummaryDataGroupBoundaryFreshRead` at :2670-2694. `[verified: read of :1-48, :1880-1950, :2600-2695; grep of top-level and method lines]`
2. `review_trace.py`'s top-level functions fall into the four subjects. `_review_trace_session_events` (:174) builds each session's events and `cmd_review_trace` (:733) renders them. `_print_deny_summary` (:52), `_fresh_records_and_group_boundaries` (:612) and `compute_deny_summary_data` (:638) form the deny summary. `_deduped_position_of_each_record` (:501), `_command_segments_by_line` (:532), `_runs_plan_review_deactivate` (:558) and `_attach_reviewer_spawn_rounds` (:585) form the spawn-round labels. `_add_review_trace_subparser` (:902) is the CLI wiring. `[verified: grep of review_trace.py's top-level defs; roles read from names and from the tests that call them]`
3. `TestReviewTrace`'s subjects by range:
   - skill and slash invocation events, :49-300;
   - legacy denial parsing, :302-345;
   - reviewer-spawn detection, :347-379;
   - architect-consult classification under its header, :381-573;
   - sidechain merge, sort and forward-fill under the GH-896 header, :575-810;
   - `--since`/`--until`, :811-848 and :866-878;
   - denial events in both record formats, :850-864 and :880-1000;
   - the `--deny-only` gate and the timeline's denial line, :1002-1028;
   - branch and model attribution under the GH-482 header, :1030-1166;
   - the deny summary, :1168-1431 and :1607-1894;
   - friction events, :1433-1605.

   `[verified: read of :286-1170 and :1400-1650; grep of method lines]`
4. Every friction test calls `_review_trace_session_events`, except :1572, which calls `compute_deny_summary_data`. `[verified: read of :1433-1606]`
5. :850 and :987 call only `_review_trace_session_events` and assert whether a denial event is present. The `--deny-only` gate itself sits in `cmd_review_trace`, which :1002 pins (its docstring at :1003-1006). `[verified: read]`
6. `_spawn_rec`, `_bash_rec`, `_spawn_rounds` and the recipe helpers are used only inside `TestReviewerSpawnRoundLabels`. `_since_until_epochs` is used only at :818, :832, :873, :1123 and :2650, all in base-kept ranges. Every other helper the four files use comes from `tests/conftest.py`. `[verified: grep]`
7. Only the spawn-round block reads a `SKILL.md`: `_PLAN_REVIEW_SKILL_MD` (:1904) and `_READY_FOR_REVIEW_SKILL_MD` (:1926, read at :2135). `[verified: grep]`
8. `CONSULT_CLASSIFICATION_TABLE` is read only at :439, in the architect-consult section. `helpers.py:3671-3680` names "test_transcript_review_trace.py's Python-classifier test" as one of its consumers. `[verified: grep; read of helpers.py:3671-3680]`
9. The only comments in `TestReviewTrace`'s body outside a method are the three section headers at :381-383, :575-577 and :1030-1032. `[verified: grep of the file for lines starting with four spaces and "#"]`
10. The :902 row must name the spawn-rounds file, and :901 needs no change. `TestCrossDomainReadCompleteness` resolves module-level path constants built on `helpers.SKILLS_DIR` (`tests/test_select_tests.py:40-48`, :118). It fails when `select_pytest_targets([path])` does not cover the reading file (:345-367). `select-tests.py:902` maps `plan-review/SKILL.md` to `TRANSCRIPT_REVIEW_TRACE_TEST_PATH` (:140) alone, while :901 maps `ready-for-review/SKILL.md` to all of `tests/`. `[verified: read]`
11. `_KNOWN_CROSS_DOMAIN_READS` (`tests/test_select_tests.py:2225-2264`) pairs the base file with both `SKILL.md` paths at :2260-2263. `TestKnownCrossDomainReadsAudit` (:2267-2279) fails a pair whose read path does not select its reader. The constant also sits in `_FILE_TARGETS` (:2209). `[verified: read]`
12. Each exact-path constant at `select-tests.py:126-140` is named for its file's stem, as `TRANSCRIPT_DENIALS_TEST_PATH`, `TRANSCRIPT_REVIEW_ROUNDS_TEST_PATH`, `TRANSCRIPT_COST_COUNTS_TEST_PATH`, `TRANSCRIPT_REDACTION_TEST_PATH` and `TRANSCRIPT_REVIEW_TRACE_TEST_PATH` are. `TRANSCRIPT_REVIEW_TRACE_TEST_PATH` occurs only at `select-tests.py:140`, :735 and :902 and at `tests/test_select_tests.py:2209`. `[verified: read; grep]`
13. `select-tests.py`'s `GLOBAL_TRIGGER_PATHS` (:480-484) holds `claude/.claude/tests/helpers.py` and `select-tests.py` itself, so this diff's `select-tests.py` run selects the full suite on its own. `[verified: read]`
14. `test_loader_free_package_importing_test_files_are_in_test_files` (`tests/test_transcript_package_module_prefixes.py:519-530`) fails any test file that imports `transcript_analysis` without the shim and is missing from `TEST_FILES` (:61). The base is listed at :90. `[verified: read]`
15. `_owning_module` assigns a test file to the longest package module its stem matches as `test_transcript_<module>` or `test_transcript_<module>_...` (`tests/test_transcript_analysis_module_test_files.py:33-37`). No package module's name starts with `review_trace_`. `[verified: read; glob of transcript_analysis/]`
16. `test-ids` keys each testcase as `Class::name`, with the module dropped (`tests/_move_fidelity.py:745-750`). `--rename` maps a whole before ID, its leading `::` part, or its `[param]` stem (:775-779). It reports `id-missing`, `id-unexpected` and `id-outcome` findings (:801-812). `[verified: read]`
17. `nodes` compares only top-level statements, matched by name (`tests/_move_fidelity.py:169-177`, :295-313), and `--verbatim` adds a byte comparison (:335-336). `remainder` compares the after file's statements and top-level comment blocks outside the declared names and the imports, and diffs import triples against `--import-added` and `--import-removed` (:434-481). Neither reaches a method of a class split across files. `[verified: read]`
18. Eight transcript-analysis test files carry a `Test<Command>ArgparseWiring` class: `test_transcript_skill_invocation.py:653`, `test_transcript_cache_rebuild.py:535`, `test_transcript_pr_cost_export.py:934`, `test_transcript_pr_link.py:390`, `test_transcript_cache_efficiency.py:385`, `test_transcript_cache_rebuild_ttl_rules.py:37`, `test_transcript_plan_boundary.py:539` and `test_transcript_read_cost_command.py:17`. `test_transcript_cost_ledger.py:786`'s `TestCostLedgerDefaultPathCliWiring` is the exception. Three of them call `cli.build_parser()` (skill_invocation :655, cache_efficiency :391, pr_link :392). The moved test reads only `cli` (`tests/test_transcript_cli.py:336-340`, imported at :7). `[verified: grep; read]`
19. `docs/transcript-analysis-architecture.md:783-787` names both `TestBuildParser` exceptions, and :803-804 lists the base as `review_trace.py`'s test file. `tests/test_transcript_review_cost_by_size.py` is 1,107 lines, so the exception sentence that stays remains true. `[verified: read]`
20. Outside `.claude/plans/`, the base file's name occurs at `review_trace.py:562`, `helpers.py:3673`, `select-tests.py:137`, :140 and :730, `tests/test_select_tests.py:2260` and :2262, `tests/test_transcript_package_module_prefixes.py:90`, and the architecture doc at :786 and :804. The :562 docstring cites the known-miss tests at :2389-2429, inside the spawn-round block. `[verified: grep; read of review_trace.py:558-564]`
21. Neither the base file nor `tests/test_transcript_cli.py` carries a `timing` mark, so neither side needs a separate `-m timing -n0` pass. `[verified: grep of tests/ for "mark.timing" and "pytestmark"]`
22. `test_ticket_reference_discipline.py` builds its corpus from `git ls-files` (:34-52), so its per-file checks skip an untracked file. `[verified: read]`
23. ruff runs with `line-length = 130` and the rules `E`, `F`, `B`, `I`, `UP` and `SIM` (`pyproject.toml:1-6`). The four docstrings and the renamed constant's line fit in 130 columns. `[verified: read; column counts of the prescribed lines]`
24. The duplicate-name check `test_no_duplicate_top_level_test_name_within_a_file` (`test_ticket_reference_discipline.py:317`) works per file. Neither that file nor `tests/test_pytest_collection_config.py` checks class names across files. `[verified: read of both files' test names]`
25. The four files land at about 570, 845, 620 and 735 lines. `[unverified: arithmetic over row 3's ranges plus estimated import blocks; Verification step 2 measures it]`
26. This PR is the review-trace follow-up `.claude/plans/cli-subparser-move.md:232` names: split at a named seam, move the test, and prove the split with `test-ids`. `[verified: read]`
27. `TestReviewTrace` is named without a file at `tests/test_transcript_denials.py:23`, :68 and :96 and at `docs/design-decisions/review-trace-fifth-event-kind.md:19`. `[verified: grep]`
28. The engineer asked the session to continue to the follow-up PRs. `[engineer-verified: "close out GH-1432 and continue to the follow-up PRs please."]`
29. pytest cannot collect a lone `git show` copy of the base file, because its `from .conftest import` (:17) needs the tests package around it. `[unverified]`
30. The Ask line's quote is the engineer's, and the review-trace test split goes first. `[engineer-verified: "Yes, proceed"]`, the label selected against the question asking whether the engineer said the quote and wants this follow-up first.
31. The two edits outside the tests and the architecture doc, `review_trace.py:562` and `claude/.claude/tests/helpers.py:3673`, stay in the diff. `[engineer-verified: "Keep both (Recommended)"]`
32. This plan's projected diff of about 4,400 changed lines goes ahead as one PR, past the 400-line gate. `[engineer-verified: "Proceed anyway"]`

**Mechanisms:**
- M1: Four files at `review_trace.py`'s function seams, per the manifest. `anchors: root, row1, row2, row3, row4, row5, row25`. The alternatives are under "Seams set aside".
- M2: A verbatim move. Top-level nodes move whole, and `TestReviewTrace` methods move into a same-named class in each file. `anchors: row9, row16, row24, row27`. The seam-named alternative is under "Class names".
- M3: No family helper file. `anchors: row6`.
- M4: The CLI test lands in the base's new `TestReviewTraceArgparseWiring`. `anchors: row18, row19`.
- M5: The registry edits, with the `select-tests.py` constant renamed for its new file's stem. `anchors: row7, row10, row11, row12, row14, row15`. Keeping the old name with a new value was set aside, because the name would then call the file something it is not.
- M6: The prose edits. `anchors: row8, row19, row20, row26`.
- M7: The fidelity proof: a merge-base worktree, `test-ids`, `nodes --verbatim`, `remainder`, a scratch method check, and the full-suite comparison. `anchors: row13, row16, row17, row21, row22, row29`. Two pieces are heavier than `test-ids` alone. Lighter primitives checked for the merge-base worktree:
  - Running the before pass in this worktree before the first edit works once, but no reviewer can rerun it after the move lands.
  - `git show <base>:<path>` into `<scratch>` serves `nodes`, `remainder` and the method check, but pytest cannot run that copy (row 29).
  - `git stash` is unsafe here, because the stash stack is shared across worktrees.

  Lighter primitives checked for the method check:
  - `test-ids` alone proves IDs and outcomes, so a method edited during the move that still passes goes unseen.
  - `nodes` reaches only top-level statements (row 17).
  - `git diff --color-moved` marks moved lines for a reader but gives no exit status.
- M8: One `code-writer` dispatch. `anchors: root, row1`. The reason is under Critical files.

## Critical files

**Dispatch: one `code-writer`, without a split.** All three new files take their content from the base file, and the registry and prose edits name the new files, so no two parts have disjoint inputs (row 1). The brief requires:
- Verification steps 1-11, run by the agent, with each command's output and exit status in the return, including the `select-tests.py` summary, any failing node IDs, and their merge-base result;
- no edit to any moved line;
- staging only the three new files, at Verification step 8, and no commits.

**Create** (under `claude/.claude/scripts/tests/`):
- `test_transcript_review_trace_detection.py`;
- `test_transcript_review_trace_deny_summary.py`;
- `test_transcript_review_trace_spawn_rounds.py`.

**Modify, tests** (under `claude/.claude/scripts/tests/`):
- `test_transcript_review_trace.py`;
- `test_transcript_cli.py` (:335-340 only);
- `test_transcript_package_module_prefixes.py` (`TEST_FILES` only);
- `test_select_tests.py` (:2209, :2260 and :2262 only).

**Modify, other:**
- `claude/.claude/scripts/select-tests.py` (:137, :140, :730, :735 and :902 only);
- `claude/.claude/scripts/transcript_analysis/review_trace.py` (:562 only);
- `claude/.claude/tests/helpers.py` (:3673 only);
- `docs/transcript-analysis-architecture.md` (:783-787 and :803-804 only).

**Also in the diff:** `.claude/plans/review-trace-test-split.md`.

**Reuse:** `tests/_move_fidelity.py`'s `test-ids`, `nodes` and `remainder`; the `tests/conftest.py` builders the moved tests already import; `cli.build_parser()`. No new helper or committed script. The method check stays in `<scratch>`, uncommitted.

**PR body requirement.** The PR body carries:
- `<scratch>/method_fidelity.py` verbatim;
- Verification steps 4-7 with their outputs;
- a list of every authored hunk:
  - the four import blocks;
  - the four module docstrings;
  - the four `class TestReviewTrace:` headers;
  - `TestReviewTraceArgparseWiring`;
  - the edits in the other seven files.

Only the method check proves byte-identity for the 103 split `TestReviewTrace` methods. With the script and the hunk list, a reviewer rereads the authored lines and reruns the proof for the moved ones.

Projected diff: about 4,400 lines. About 2,150 leave `test_transcript_review_trace.py` and about 10 enter it, about 2,200 are added across the three new files, and about 45 change across the other seven files. About 4,350 of the total is the verbatim move.

## Verification

Run from the worktree root. `<scratch>` is a new directory outside the repository from `mktemp -d`, `<base>` is the SHA `git merge-base HEAD origin/main` prints, and `<tests>` is `claude/.claude/scripts/tests`.

1. Before side, from the merge-base:
   - `git worktree add --detach <scratch>/base <base>`
   - With `<scratch>/base` as the working directory, run `scripts/venv-run.sh pytest <tests>/test_transcript_review_trace.py <tests>/test_transcript_cli.py --junitxml=<scratch>/before.xml`. One pass covers both files (row 21).
2. `wc -l` over the four review-trace files: each is under 1,000, near 570, 845, 620 and 735.
3. `scripts/venv-run.sh ruff check claude/.claude/ claude-skills/ plugins/ evals/` is clean.
4. Test IDs:
   - `scripts/venv-run.sh pytest <tests>/test_transcript_review_trace.py <tests>/test_transcript_review_trace_detection.py <tests>/test_transcript_review_trace_deny_summary.py <tests>/test_transcript_review_trace_spawn_rounds.py <tests>/test_transcript_cli.py --junitxml=<scratch>/after.xml`
   - `scripts/venv-run.sh python3 <tests>/_move_fidelity.py test-ids --before <scratch>/before.xml --after <scratch>/after.xml --rename TestBuildParser::test_review_trace_parses_omit_plan_review_spawns=TestReviewTraceArgparseWiring::test_review_trace_parses_omit_plan_review_spawns` exits 0 and prints equal before and after counts. Any finding blocks. Never add a `--rename` or `--expect-new` to clear one.
5. Top-level nodes: `scripts/venv-run.sh python3 <tests>/_move_fidelity.py nodes --before <scratch>/base/<tests>/test_transcript_review_trace.py --after <tests>/test_transcript_review_trace_spawn_rounds.py --after <tests>/test_transcript_review_trace_deny_summary.py --moved <names> --verbatim <names>` exits 0 and prints `compared-nodes: 10`. `<names>` is `_spawn_rec _PLAN_REVIEW_SKILL_MD _deactivate_plan_review _write_plan_review _expand_recipe_placeholders _READY_FOR_REVIEW_SKILL_MD _bash_rec _spawn_rounds TestReviewerSpawnRoundLabels TestComputeDenySummaryDataGroupBoundaryFreshRead`.
6. Base remainder: `scripts/venv-run.sh python3 <tests>/_move_fidelity.py remainder --before <scratch>/base/<tests>/test_transcript_review_trace.py --after <tests>/test_transcript_review_trace.py --removed <names> --changed __doc__ TestReviewTrace --added TestReviewTraceArgparseWiring --import-added transcript_analysis:cli --import-removed <dropped>` exits 0. `<dropped>` is every import triple the trimmed block no longer carries, in the tool's form, such as `functools`, `helpers:SKILLS_DIR` and `.conftest:_user_msg`. Step 3's ruff `F401` pass shows none of them is still read.
7. Moved methods:
   - Write `<scratch>/method_fidelity.py`, which is not committed. It takes the base copy first, then the three files that define `TestReviewTrace`. It parses each with `ast`. For each top-level `TestReviewTrace` class, it maps every function directly in the class body to its source lines, from its first decorator through `end_lineno`. It prints the method count per file and per side. It exits 0 only when all of these hold:
     - both sides hold the same names;
     - no name repeats on the after side;
     - every method's lines are byte-identical;
     - the after side counts are 24 in the base, 52 in `_detection` and 27 in `_deny_summary`, and 103 on the before side;
     - the CLI test's lines, from its first decorator through `end_lineno`, are byte-identical between `TestBuildParser` in `<scratch>/base/<tests>/test_transcript_cli.py` and `TestReviewTraceArgparseWiring` in `<tests>/test_transcript_review_trace.py`.
   - `scripts/venv-run.sh python3 <scratch>/method_fidelity.py <scratch>/base/<tests>/test_transcript_review_trace.py <tests>/test_transcript_review_trace.py <tests>/test_transcript_review_trace_detection.py <tests>/test_transcript_review_trace_deny_summary.py <scratch>/base/<tests>/test_transcript_cli.py` exits 0 and prints 103 per side.
   - Seeded-violation control: run the script against scratch copies of the four after files, each copy carrying one defect, and each run must exit non-zero. The defects are an edited decorated method, a dropped method, a method present in two files, a method dedented out of its class, and an edited CLI test body. Delete the copies afterwards.
   - `sed -n '48,1894p' <scratch>/base/<tests>/test_transcript_review_trace.py | grep -c '^    def '` prints 103, so the check did not pass on an empty map.
   - `grep -n -F -e 'architect-consult classification' -e 'GH-896: merge/sort' -e 'GH-482: per-record' <tests>/test_transcript_review_trace.py <tests>/test_transcript_review_trace_detection.py` prints three lines: two in the base and one in `_detection` (row 9).
8. `git add -- <tests>/test_transcript_review_trace_detection.py <tests>/test_transcript_review_trace_deny_summary.py <tests>/test_transcript_review_trace_spawn_rounds.py`, so `test_ticket_reference_discipline.py`'s corpus includes them (row 22).
9. `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py`. Its stderr reports the full suite, for `global-trigger` (row 13). Then:
   - These pass:
     - `test_select_tests.py`: `TestCrossDomainReadCompleteness` and `TestKnownCrossDomainReadsAudit`.
     - `test_transcript_package_module_prefixes.py`: `test_loader_free_package_importing_test_files_are_in_test_files` and `test_test_files_reference_only_real_module_attributes`.
     - `test_transcript_analysis_module_test_files.py`: `test_every_package_module_has_a_named_test_file_or_an_entry`.
     - `test_ticket_reference_discipline.py`, for each new file: its cases of `test_no_ticket_prefixed_identifier` and `test_no_duplicate_top_level_test_name_within_a_file`.
   - Step 4 already judges a failure in its five files, since it compares each outcome with the merge-base.
   - An ID that fails only on this tree blocks. To decide, rerun every other failing node ID with `<scratch>/base` as the working directory, per `CLAUDE.md`'s "Prove your change caused a failing check", and compare node IDs, not counts. G4's count is a prediction, not the criterion.
10. References:
    - `git grep -n 'test_transcript_review_trace\.py' -- ':!.claude/plans/'` prints two lines: the `TEST_FILES` entry and the architecture doc's per-module list.
    - `git grep -n TRANSCRIPT_REVIEW_TRACE_TEST_PATH -- ':!.claude/plans/'` prints nothing.
    - `git grep -n test_review_trace_parses_omit_plan_review_spawns -- ':!.claude/plans/'` prints only the base file's definition.
11. `git worktree remove <scratch>/base`.

## Out of scope

- **Splitting `tests/test_transcript_review_cost_by_size.py` and moving `test_review_cost_by_size_all_accounts_flag_wires_to_all_accounts`.** That file is over the limit too (row 19). Its split is a follow-up of its own, and the architecture doc keeps that test as the one remaining exception.
- **Seam-named classes for the moved `TestReviewTrace` methods.** They are set aside under "Class names" above.
- **Any edit inside a moved test or section header**, the ticket-numbered headers and the docstring cross-references between tests that now sit in different files included. Verification steps 4-7 prove a pure move, and nothing else.
- **The other GH-1432 follow-ups:** the invariant test, the in-process round-trip tests, and the stale plan citations. Each is its own change.
- **Mentions of the old layout in `.claude/plans/*.md`.** They are preserved records (`CLAUDE.md` §Working Style "Scope discipline", Axis 3).
- **The line limit's enforcing check.** It waits on the offender assessment `docs/design-decisions/code-file-line-limit.md` names.
