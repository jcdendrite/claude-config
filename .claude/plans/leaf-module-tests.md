# GH-1116: leaf-module tests

## Context

Goal: move the tests of the leaf modules `corpus`, `scope`, `redaction`, `pricing` and `render` out of `claude/.claude/scripts/tests/test_transcript_analysis.py` into per-module test files, in two PRs under this one plan, so the legacy file shrinks toward what the final `cli.py` phase owns.

Ask: tracker #1116's unchecked box "leaf-module tests — move the tests of corpus/scope/redaction/pricing/render out of `test_transcript_analysis.py`", also carrying `TestRepoTrackedAgentTypeNames`, `TestSanitizeTableCell`, `TestRootScopedDisplayLabel` and `TestSubagentFormatContract`, and the `select-tests.py` agents-dir row for the file that receives `test_real_agents_directory_allowlists_code_writer`. Phase split accepted via label "Two PRs, one plan (Recommended)". Engineer: "Yes. And autonomous shipping is active."

Why now: the shim-reader retarget (#1350, #1361) merged, so the legacy file no longer reads shim bindings other than `build_parser` and `main`, and the leaf tests can move without touching the shim. Governing plan: `.claude/plans/transcript-analysis-decomposition.md`. Tracker: #1116, commits say `Refs GH-1116`, no comment is posted on it.

Measured at `origin/main` 28c5d3e1 by a read-only exploration (evidence, not a design conclusion): the legacy file is 4,573 lines. The leaf-module tests are about 3,139 lines, not the tracker's 1,500–2,000 estimate: scope about 1,134 (about 1,371 with four ambiguous classes), pricing about 636, corpus about 539, redaction about 329, render about 264. The `cli.py` phase's classes are about 616 lines. Cross-module classes that the architecture doc says stay in the legacy file are about 603 lines. No `test_transcript_{corpus,scope,redaction,pricing,render}.py` exists, and the convention is `test_transcript_<module>.py`. Scope alone is over the 1,000-line limit, so it needs two files.

Straddling classes the plan must place: `TestHelpers` (mixed, render-dominant), `TestSingleLevelProjectsGlob` (corpus, but two tests call `_mod.build_parser()`), `TestParseAbsoluteWindowArgs`, `TestRedactionOrdinalStability`, `TestPoisonedProjectsDirGlobal`, `TestThisRepoUnionsAcrossRoots`. `docs/transcript-analysis-architecture.md` currently says `TestSubagentFormatContract` and `TestIsFreshUserPrompt` stay in or relate to the legacy file; the tracker body, rewritten this session at the engineer's request, lists both as moving with this phase and is the later statement.

## Approach

Each leaf-module test moves verbatim out of `test_transcript_analysis.py` (below, L) into a per-module file, in two sequenced PRs. PR A creates `test_transcript_pricing.py`, `test_transcript_redaction.py`, and `test_transcript_render.py`. PR B is cut from `origin/main` after PR A merges and creates `test_transcript_scope.py`, `test_transcript_scope_scan.py`, and `test_transcript_corpus.py`. Each PR has two commits. Commit 1 edits L in place: it splits the three classes that mix subjects, and in PR B it frees the `scope.py` loads from the shim path. Commit 2 is a pure move that `_move_fidelity.py nodes --verbatim` proves byte for byte. No production code changes.

**Placement rule.** A test moves with the module that owns the function it exercises, even when one of its tests drives that function end to end through a command (rows 4, 5). A test stays in L when it reads `build_parser` or `main` (G1), spans command groups (G6), or reads a `SKILL.md` (row 15). A class that mixes these is split in place, not moved whole.

**Straddling classes.**
- `TestHelpers` (A1). The 20 `render` tests keep the class name and move to the render file. The three `test_parse_ts_*` tests go to a new `TestParseTs` and the three `test_iso_date_*` tests to a new `TestIsoDate`, both in L until PR B.
- `TestSingleLevelProjectsGlob` (B1). Its two tests that read `_mod.build_parser()`, `test_cli_accepts_a_multi_segment_projects_value_at_parse_time` and `test_every_projects_registration_across_every_subcommand_has_no_argparse_type`, go to a new `TestProjectsFlagHasNoArgparseType`, which stays in L. The rest moves to scope.
- `TestRootCountDescDirectUnit` (B1). `test_absent_state_literal_appears_in_both_skill_files` goes to a new `TestSkillFilesQuoteRootCountDescLiteral`, which stays in L. The rest moves to scope.
- Whole classes:
  - `TestParseAbsoluteWindowArgs`, `TestRedactionOrdinalStability` (`_redaction_ordinals` is scope.py's), `TestPoisonedProjectsDirGlobal`, and `TestThisRepoUnionsAcrossRoots` go to scope.
  - `TestBuildRedactMapDirectUnit` goes to redaction.
  - `TestDedupTurnsByRequestId` goes to pricing.
- The four new classes carry no docstring (row 3).

**Commit 1, in place in L.**
- A1:
  - Make the `TestHelpers` split above.
  - Insert `TestParseTs`, then `TestIsoDate`, directly after `TestHelpers`, with their methods in their original order.
  - Cut and paste the method text, changing no character inside it.
- B1:
  - Make the two splits above. Each new class goes directly after its source class, with its methods in their original order.
  - In `test_projects_dir_honors_claude_config_dir`, `test_scope_import_does_not_crash_when_home_unset`, and `test_resolve_scan_roots_default_root_when_home_unset`, change `scope_path = _SCRIPT.parent / "transcript_analysis" / "scope.py"` to `scope_path = Path(scope.__file__)`.
  - Replace the whole docstring of `test_projects_dir_honors_claude_config_dir` with `"""Execs a fresh copy of scope.py because the imported module caches PROJECTS_DIR on first access."""`. The old one names `_mod above`, which the scope file will not have, and its claims that PROJECTS_DIR is computed at import time and that a module-level assignment re-runs are false (row 16).

**Commit 2, the move.** Each destination receives its nodes in their L order.
- PR A, 17 nodes:
  - `test_transcript_render.py`: `TestHelpers`, `TestSanitizeTableCell`, `TestIsFreshUserPrompt`, `TestIsTaskNotification`.
  - `test_transcript_redaction.py`: `TestProjectFamily`, `TestRootScopedDisplayLabel`, `TestRepoTrackedAgentTypeNames`, `TestCorpusFingerprintRootRemovalLimits`, `TestBuildRedactMapDirectUnit`.
  - `test_transcript_pricing.py`: `TestPriceTurnArity`, `TestPriceTurnSpeedGeoMultipliers`, `_PRICING_VARIANT_USAGE_CASES`, `TestPricingVariant`, `TestReportableUnpricedModelIds`, `TestFormatDriftDetected`, `TestDedupTurnsByRequestId`, `TestCacheMissReason`.
  - L loses the seven `# ---` banners whose middle lines read `Helpers`, `_project_family (redaction.py)`, `redaction: root-scoped display labels and the --this-repo agent-type allowlist`, `_sanitize_table_cell (render.py)`, `_is_fresh_user_prompt (render.py)`, `_is_task_notification (render.py)`, and `cache_miss_reason (pricing.py)`.
- PR B, 30 nodes:
  - `test_transcript_scope.py`: `test_projects_dir_honors_claude_config_dir`, `test_scope_import_does_not_crash_when_home_unset`, `test_resolve_scan_roots_default_root_when_home_unset`, `TestIsoDate`, `TestParseSinceNdArg`, `TestParseAbsoluteWindowArgs`, `TestRepoScopedProjectSlugsGuard`, `TestResolveProjectScope`, `TestPathToProjectSlug`, `TestThisRepoUnionsAcrossRoots`, `TestSingleLevelProjectsGlob`, `TestPoisonedProjectsDirGlobal`, `TestResolveScanRoots`, `TestRedactionOrdinalStability`, `TestResolvedScopeHeaderDirectUnit`, `TestRootCountDescDirectUnit`.
  - `test_transcript_scope_scan.py`: `TestScanRootTranscripts`, `TestIterScopedSessionsUnreadableRoot`, `_assert_scan_error_chain_suppressed`, `TestScanGapCounter`, `TestGlobScopedSessionsSelection`.
  - `test_transcript_corpus.py`: `TestParseTs`, `_read_tool_use`, `_text_marker_assistant`, `_text_markers`, `TestCorpusSubagentDirAndUtf8ErrorHandling`, `TestIterSessionsOrdering`, `TestIterSessionsSubagentMerge`, `TestSubagentFormatContract`, `TestProjectsGlobStepsToParent`.
  - L loses the banners whose middle lines read `iter_sessions subagent merge` and `subagent format contract`. The middle line of the `_repo_scoped_project_slugs guard, build_parser, and iter_sessions ordering` banner becomes `# build_parser`, because `TestBuildParser` is the only class left under it.
- **New-file shape.** Each file holds a header, then the moved nodes, two blank lines apart.
  - The header holds the file's imports: stdlib and third-party imports, one unaliased `from transcript_analysis import <modules>` line, and relative `from .conftest import ...` imports (`.claude/rules/test-tree-packaging.md`).
  - Only the scope pair gets a module docstring, one line each:
    - `test_transcript_scope.py`: `"""scope.py's tests, except the scan traversal and its gap counter, which test_transcript_scope_scan.py holds."""`
    - `test_transcript_scope_scan.py`: `"""scope.py's scan traversal and scan-gap counter; test_transcript_scope.py holds scope.py's other tests."""`
  - No file gets a section banner or any other statement (rows 3, 25).
- **L** loses the moved nodes, the listed banners, and every import nothing left in it reads. Commit 2 changes nothing else in L.
- **`test_transcript_package_module_prefixes.py`.** Append the PR's three files to `TEST_FILES`, in the order listed above. It grows from 59 entries to 62 in PR A and to 65 in PR B (row 11).
- **PR A only, `select-tests.py`** (rows 13, 14):
  - Replace the comment above `TRANSCRIPT_COST_COUNTS_TEST_PATH` (:126-128) with `# test_transcript_cost_counts.py and test_transcript_redaction.py read` / `# claude/.claude/agents/ through redaction._REPO_AGENT_DEFINITIONS_DIR, a` / `# production constant TestCrossDomainReadCompleteness's scan cannot see.`
  - After that constant, add `TRANSCRIPT_REDACTION_TEST_PATH = "claude/.claude/scripts/tests/test_transcript_redaction.py"`.
  - In the `AGENTS_DIR` row of `CROSS_DOMAIN_EXCEPTIONS` (:850-857), replace `TRANSCRIPT_ANALYSIS_TEST_GLOB` with `TRANSCRIPT_REDACTION_TEST_PATH`.
  - In the comment block above `CROSS_DOMAIN_EXCEPTIONS` (:736-744), replace `TRANSCRIPT_ANALYSIS_TEST_GLOB` with `TRANSCRIPT_REDACTION_TEST_PATH`. Delete the sentence "The glob matches several files and is selected for the one of them that reads it."
- **PR A only, `test_select_tests.py`.**
  - In `test_agents_dir_change_selects_the_declared_agents_dir_readers` (:1074), replace `_mod.TRANSCRIPT_ANALYSIS_TEST_GLOB` with `_mod.TRANSCRIPT_REDACTION_TEST_PATH`.
  - Add `_mod.TRANSCRIPT_REDACTION_TEST_PATH` to `_FILE_TARGETS` (:2073-2090), after `_mod.TRANSCRIPT_COST_COUNTS_TEST_PATH`.
- **Prose** (rows 3, 17). Each edit anchors on content and rewraps only the lines it touches.
  - PR A, `docs/transcript-analysis-architecture.md`:
    - Replace the sentence starting "Each of `corpus.py`, `scope.py`" (:760-761) with "`pricing.py`'s, `redaction.py`'s, and `render.py`'s tests live in `tests/test_transcript_pricing.py`, `tests/test_transcript_redaction.py`, and `tests/test_transcript_render.py`. `corpus.py` and `scope.py` are exercised through `transcript-analysis.py`'s existing test suite (`tests/test_transcript_analysis.py`)."
    - Replace "`TestIsFreshUserPrompt` stays in `tests/test_transcript_analysis.py` because it tests `render.py`, not the judgment-pair command." (:935-936) with "`TestIsFreshUserPrompt` lives in `tests/test_transcript_render.py` because it tests `render.py`, not the judgment-pair command."
  - PR A, `test_transcript_context_composition.py` :311 and :416: change `test_transcript_analysis.py's` to `test_transcript_pricing.py's`.
  - PR B, `docs/transcript-analysis-architecture.md`:
    - Replace PR A's two sentences with "`corpus.py`'s, `pricing.py`'s, `redaction.py`'s, and `render.py`'s tests live in `tests/test_transcript_corpus.py`, `tests/test_transcript_pricing.py`, `tests/test_transcript_redaction.py`, and `tests/test_transcript_render.py`. `scope.py`'s scan traversal and scan-gap counter are tested in `tests/test_transcript_scope_scan.py`, and the rest of `scope.py` in `tests/test_transcript_scope.py`. `TestProjectsFlagHasNoArgparseType` stays in `tests/test_transcript_analysis.py` because it reads `build_parser`. `TestSkillFilesQuoteRootCountDescLiteral` stays there too, because `select-tests.py` declares that file, not the scope files, as a `SKILL.md` reader."
    - Replace "`TestSubagentFormatContract` stays there too: it pins" (:890-891) with "`TestSubagentFormatContract` lives in `tests/test_transcript_corpus.py`: it pins", keeping the rest of that sentence.
  - PR B, `test_transcript_cost.py` :2743: change `test_transcript_analysis.py's` to `test_transcript_scope_scan.py's`.

**Assumption ledger.**

**Root:** L is 4,573 lines, and about 3,100 of them test one of the five leaf modules rather than the `cli.py` phase's `build_parser` and `main` or a cross-group behavior. Moving those tests to per-module files leaves L holding only what the governing plan's later phases own. `[verified: line count and class spans, Grep of L at 28c5d3e1]` `[unverified: the 3,100 is span arithmetic; each PR's test-ids totals recount the tests]`

**Givens:**
- G1. The shim keeps `build_parser()` and `main()` until the `cli.py` phase, so a test that reads either stays in L. Reason: changing this needs a decision outside this plan, the governing plan's phase order. `[verified: transcript-analysis-decomposition.md:155-161]`
- G2. Agents run `select-tests.py`, not the full suite, outside CLAUDE.md's two named cases. Reason: changing this needs an edit to CLAUDE.md § "Commands". `[verified: CLAUDE.md "Commands"]`
- G3. Code files stay under 1,000 lines. No test enforces the limit yet. Reason: it is a repo decision this plan does not own. `[verified: docs/design-decisions/code-file-line-limit.md:19, :50-57]`
- G4. This machine runs the suite with one xdist worker, and a scoped `select-tests.py` run takes 37 to 51 minutes. Reason: machine memory lies outside the plan's reach. `[unverified — relayed by the dispatching session]`
- G5. Four `test_shellcheck.py` tests fail on this machine at 28c5d3e1. `scripts/venv-run.sh` execs `.venv/bin/<tool>` without putting `.venv/bin` on PATH (:44-45). So `shutil.which("shellcheck")` (`test_shellcheck.py:104-105`) finds system `shellcheck` 0.9.0, which fails the check against the `shellcheck-py==0.11.*` pin (:152-159, `requirements-dev.txt:5`). Open PR #1379 makes `venv-run.sh` prepend the main checkout's `.venv/bin` to PATH, which fixes it. Reason: another session owns #1379, and merging it is the engineer's. `[verified: scripts/venv-run.sh:44-45; test_shellcheck.py:104-105, :152-159; requirements-dev.txt:5]` `[verified: /plan-review round 4 ran /usr/bin/shellcheck --version, which reports 0.9.0, and scripts/venv-run.sh shellcheck --version, which reports 0.11.0. It re-read scripts/venv-run.sh:44-45, which still execs .venv/bin/<tool> with PATH unchanged, and ran gh pr view 1379 --json state,mergeCommit, which returned "state":"OPEN" and "mergeCommit":null.]` `[verified: /plan-review round 3 read gh pr diff 1379, which makes the PATH change above]` `[unverified — relayed: the count of four failures, from /plan-review round 2. Verification 8 runs test_shellcheck.py alone before the selected run.]`
- G6. A test spanning several command groups stays in L until every group it references has moved. Reason: the governing plan's rule for cross-group tests. `[verified: transcript-analysis-decomposition.md:157-161; docs/transcript-analysis-architecture.md:836-838]`

**Rows:**
1. The engineer chose the phase split `[engineer-verified: "Two PRs, one plan (Recommended)"]`. The PR contents (PR A pricing, redaction, render; PR B scope, corpus) and the after-merge cut were the dispatching session's own proposal under that label. This plan adopts them on rows 19 and 20, not on the engineer's authority. `[unverified]`
2. `[engineer-verified: "Yes. And autonomous shipping is active."]` This covers the branch name and shipping only.
3. The engineer's prose rule, quoted in `.claude/plans/shim-reader-retarget-pr2.md` row 26, says docstrings should not restate the code and should carry non-obvious information. `[verified: that plan's row 26]` The dispatching session relayed it as standing for this plan. `[unverified]`
4. Every top-level node in L, and the leaf module each one reads, was mapped by Grep. `[verified: Grep of top-level defs and of every <leaf>.<attr> read, at 28c5d3e1]`
   - Each of PR A's 17 nodes reads only its own module, apart from two:
     - `TestBuildRedactMapDirectUnit` also calls `scope._redaction_ordinals` (:4216).
     - `TestDedupTurnsByRequestId` drives `cost._cost_report` (:1514, :1531).
   - PR B's scope nodes drive `buckets`, `cost`, and `skill_invocation` end to end in a few tests (:1981, :1988, :3024, :3832, :3850, :3873, :4048, :4051).
5. The straddling classes, by line at 28c5d3e1. `[verified: read]`
   - `TestHelpers` (:414-527) holds 20 `render` tests, three `corpus._parse_ts` tests (:437-445), and three `scope._iso_date` tests (:447-456).
   - `TestSingleLevelProjectsGlob` (:3691-3852) reads `_mod.build_parser()` only at :3721 and :3730.
   - `TestRootCountDescDirectUnit` reads `SKILLS_DIR` only in :4147-4159.
   - `TestParseAbsoluteWindowArgs` uses `corpus._parse_ts` only to compute expected values (:565-584).
   - `TestRedactionOrdinalStability` calls `scope._redaction_ordinals` (:4030-4031).
   - Of the three classes that commit 1 splits, none holds a member other than its docstring and its test methods: no helper method, fixture, or class attribute. `[verified: Grep of class-body lines at four-space indentation in L at 28c5d3e1]`
6. L reads `_mod` only at :193-320, :1766, :1773, :1998-2057, :3721, and :3730. It reads `_SCRIPT` at those loader lines and at :119, :134, and :154. `[verified: Grep]`
7. Module reads left in L after the moves. `[verified: Grep]`
   - No remaining class reads `pricing.`, `redaction.`, or `render.`, so PR A drops those three names from L's package import.
   - `corpus.` and `scope.` stay read, for example at :2265, :2891, and :3884.
   - Every relative helper import in L (:54-68) is read only by classes that stay. The architecture doc's "the legacy file imports it too" sentences therefore stay true.
   - Of the `.conftest` names (:69-96), nothing left in L reads `_TASK_NOTIFICATION_TEXT`, `_task_notification_msg`, or `_tool_result` after PR A, nor `_write_cost_root` or `_write_subagent_dispatch` after PR B. `_priced` stays read, for example at :1793 and :2296.
8. The only comments in L outside top-level node spans are :99-101, :2548-2552, and fifteen `# ---` banner blocks (:329 through :4329). No comment sits at four-space indentation, and every deeper comment is followed by a statement at its own indentation, so no comment sits in a class body outside a member. No indented comment trails a node. `[verified: Grep for ^# ; multiline Grep for an indented comment followed by blank lines and a top-level statement]`
9. L's only autouse fixture is `TestRepoTrackedAgentTypeNames._clear_cache` (:673), and it moves with its class. L has no module-level `pytestmark`. Its one `timing` mark is in `TestParseSinceNdArg` (:534). `[verified: Grep]`
10. The harness, `_move_fidelity.py`. `[verified: read]` Its own committed self-tests cover it. `[verified: docs/transcript-analysis-architecture.md:806-813]`
    - `nodes` compares top-level defs, classes, and single-name assignments, from the first decorator line through `end_lineno` (:147-159). `--verbatim` requires byte-identical lines (:335-336).
    - `remainder` drops imports and declared nodes. It then compares the remaining statements and the comment blocks outside dropped spans (:375-405, :451-481).
    - `test-ids` keys a test as `Class::name`, without the module (:745-750). An unchanged class name therefore keeps its ID across files. `--rename` matches an exact ID or an `::` or `[` prefix (:775-779). An ID present on one side only prints `id-missing` or `id-unexpected` (:807-812).
    - `remainder --changed` drops the named node from both sides, so a changed class's body is never compared (:466-467).
    - `nodes` compares each named node's text, but not the nodes' order (:295-323).
11. The prefix guard, `test_transcript_package_module_prefixes.py`. `[verified: read]`
    - `TEST_FILES` has 59 entries (:57-117).
    - A file that imports the package and calls no `spec_from_file_location` must be listed (:498-509). `test_transcript_scope.py` will call it on `scope.py`, so listing it is a choice that keeps its reads checked, not a requirement.
    - A listed file needs unaliased imports and at least one resolvable `<module>.<name>` read. `__file__` is in `IMPLICIT_MODULE_ATTRS` (:122).
    - The pin test's two sets select by the spec name `"transcript_analysis"` or by a path constant containing `"transcript-analysis.py"` (:531-546). The moved `scope.py` loads pass other spec names (:120, :135, :155) and a function-local path, so they enter neither set.
12. `select-tests.py` is in its own `GLOBAL_TRIGGER_PATHS` (:458-462), so PR A's selected run widens to the full suite (:918-920). That is CLAUDE.md's first named case. `[verified: read]`
13. The agents row: `CROSS_DOMAIN_EXCEPTIONS` :850-857, its comment :736-744, and the constant comment :126-129. `test_select_tests.py` pins the row's targets as a set (:1065-1077). It lists file targets in `_FILE_TARGETS` (:2073-2090), which the directory and file existence tests read (:2319-2333). `[verified: read]`
14. After PR A, no `test_transcript_analysis*.py` file reads the real agents directory. `[verified: Grep]`
    - In L, `_repo_tracked_agent_type_names` appears only in `TestRepoTrackedAgentTypeNames`.
    - `subagent_mix.py` calls it at :263 behind `this_repo and`, at :555 in `cmd_cost_counts`, and at :467 in `_partition_spawn_counts_by_disclosure`, which only `cmd_cost_counts` calls (:537).
    - L's one `cmd_subagent_mix` row (:2563) and `test_transcript_analysis_branch_attribution.py` (:588-592) pass `this_repo` False. No such file calls `cmd_cost_counts` or `_partition_spawn_counts_by_disclosure`.
    - L's other `cmd_subagent_mix` hits are three docstring lines in `TestParseAbsoluteWindowArgs` (:558, :576, :578), which moves in PR B.
15. The `SKILL.md` row. `[verified: Grep]`
    - `_is_hooks_or_skills_change` selects `TRANSCRIPT_ANALYSIS_TEST_GLOB` (:799), which matches `test_transcript_analysis*.py` only (:119).
    - `TestSkillFilesReportObservedScopeNotUnionGuarantee` (:4162-4195) stays in L, so the row keeps a reader either way.
    - Moving the literal test would need a new constant in that row. It would also need edits to every `test_select_tests.py` expectation that names the row's targets (:432, :445, :513, :875, :1410, :1601, :1635, :1650, :2820).
16. `scope.PROJECTS_DIR` resolves lazily and is cached in module globals (scope.py:45-82). `[verified: read]` `Path(scope.__file__)` names the same file as `_SCRIPT.parent / "transcript_analysis" / "scope.py"`. `[unverified: B1's test run checks it]`
17. Prose sites. `[verified: Grep]`
    - Sites that name a moved test as living in L: the architecture doc :760-761, :889-891, and :935-936; `test_transcript_context_composition.py` :311 and :416, citing L:982 in `TestPriceTurnArity`; and `test_transcript_cost.py` :2743-2744, citing L:844 in `TestScanRootTranscripts`.
    - Other references name a moved class but not L: `test_transcript_cost_counts.py` :226, `test_transcript_subagent_mix.py` :569 and :805, `test_transcript_cost.py` :1717, and `test_transcript_edit_format.py` :432. They stay true.
    - Prose on L's staying nodes. `[verified: read of L at 28c5d3e1]`
      - `TestConfigDirFlag`'s docstring (:167-169) says `test_projects_dir_honors_claude_config_dir` "covers above", which PR B makes false. That docstring and `test_no_flag_leaves_projects_dir_unchanged_after_parsing`'s (:202-205) also say that test covers import-time behavior, which row 16 shows was already false.
      - `# Helpers` (:409-411) heads `TestHelpers`, `TestParseSinceNdArg`, and `TestParseAbsoluteWindowArgs`. `# _sanitize_table_cell (render.py)` (:791-793) heads every node from :796 up to the `# cache-efficiency` banner (:1754), including `TestScanRootTranscripts` and the pricing and corpus nodes.
      - PR A deletes both banners. Until PR B merges, `# _table_cols self-tests` (:329-331) then heads `TestTableColsHelper`, `TestParseTs`, `TestIsoDate`, `TestParseSinceNdArg`, `TestParseAbsoluteWindowArgs`, `TestScanRootTranscripts`, `_read_tool_use`, `_text_marker_assistant`, `_text_markers`, and `TestCorpusSubagentDirAndUtf8ErrorHandling`. PR B moves all of them but `TestTableColsHelper`.
18. Projected file sizes: render about 270 lines, redaction about 320, pricing about 680, corpus about 430, scope_scan about 790, and scope about 870. L ends near 1,440. `[unverified: span arithmetic; each commit-2 dispatch reports wc -l]`
19. Cutting both PRs from main would conflict:
    - Both edit L's package-import block.
    - PR A's `TestCorpusFingerprintRootRemovalLimits` (:4062) sits between PR B's `TestRedactionOrdinalStability` (:4018) and `TestResolvedScopeHeaderDirectUnit` (:4079).
    - Both append to `TEST_FILES`'s tail.
    - Both edit the architecture doc at :760-761.

    `[verified: line numbers above]`
20. PR B's proofs compare against L as PR A leaves it. PR B needs no plan edit: `require-plan-review.sh` arms only on an uncommitted or modified plan file (:4-15), and this plan reaches main with PR A. `[verified: read]`
21. No new class name or file name collides with an existing one: `TestParseTs`, `TestIsoDate`, `TestProjectsFlagHasNoArgparseType`, `TestSkillFilesQuoteRootCountDescLiteral`, and the six file names. `[verified: Grep; Glob]`
22. The moved `GH-1312` docstring (:2453) is a bare tracker ID, which `test_ticket_reference_discipline.py` allows (:191). `[verified: read]`
23. Neither PR changes `transcript-analysis.py` or any module under `transcript_analysis/`, so no CLI output can change. `capture --all-help` is not run, and Verification 6 checks the changed-file list instead. `[verified: Critical files]`
24. The governing plan's Verification 7 requires a revert rehearsal at each phase boundary. `[verified: transcript-analysis-decomposition.md:276-279]` The engineer's earlier waiver covered only the two shim-retarget PRs. `[verified: shim-reader-retarget-pr2.md row 12]` Neither PR here changes `conftest.py`, which is the failure the rehearsal guards against. The engineer waived the rehearsal for both PRs. `[engineer-verified: "Waive for both (Recommended)"]`
25. The repo convention for module docstrings. 10 of 10 sampled per-module test files open with a "Tests for transcript_analysis/<module>.py" docstring (buckets, fail_seq, commit_gate, pr_link, turn_shape, denials, gh_cli, read_scope, edit_format, duration). `[verified: Grep]` This plan departs from that convention under row 3.
26. The selected suite's owner. `[verified: read]`
    - `/ready-for-review` step 2 skips its commands only when `marker.sh check verification` matches, and only that step writes the marker, after its own passing run (`ready-for-review/SKILL.md:52`). After any fix the skill returns to step 2 (:15).
    - Step 2 halts on a failed command (:15). For a failure in code the diff does not touch, it offers only waiting for the owner or a separate branch (:64). Step 7 then writes no completion marker (:129-130), and `require-ready-for-review.sh` denies `gh pr create` without one (:399-424). So no failure, pre-existing or not, lets the gate open the PR, which step 8 does.
27. What the suite run sees. `[verified: read]`
    - `test_ticket_reference_discipline.py` builds its corpus from `git ls-files` (:34-38), which lists index entries only, so a new file is invisible to it until staged.
    - `select-tests.py` takes its changed paths from the merge-base diff, the dirty tree, and untracked files (:958-986). A run after commit 2 still selects for the whole PR, and after a rebase it does not count `origin/main`'s own changes.
28. Sync points. `[verified: read]`
    - `git-feature-branch-sync` defaults to rebase on a personal branch with no stacked dependents (`SKILL.md:58`, `:154`).
    - `/ready-for-review` step 1 syncs a branch that is behind (:47). Step 6 does not sync after a clean trial merge, and sends a conflicting one back to step 1 (:121).
    - In the 14 days before review, 47 commits touched `claude/.claude/scripts/` or the architecture doc, and 8 of them touched L. `[unverified — relayed from /plan-review round 1]`
29. `remainder` against a stub file. `[verified: read of _move_fidelity.py]`
    - It always drops imports, and drops `--added` names from the after side, before comparing what is left (:375-391, :466-467).
    - Each import the stub lacks prints as `import-diff: undeclared added import` (:434-448). Any other leftover statement or comment block prints as `remainder-ast` or `remainder-comments` (:470-480).
    - It ends with `remaining-statements:` and the after side's leftover count (:481, :842).
30. `/ready-for-review`'s active marker. `[verified: read]`
    - Step 0 writes it with `marker.sh activate ready-for-review`, which overwrites any earlier one (`marker.sh:870-879`).
    - It is live only while its mtime is under 60 minutes old, and the first check that finds it older deletes it (`_lib.sh:3079-3088`). Only `require-ready-for-review.sh` refreshes the mtime, on a Bash call (:199-201, :220-227; `_lib.sh:3108-3122`). Waiting on a background run makes no Bash call, so a run over 60 minutes lets the marker lapse.
    - On a first run no PR exists, so the hook allows step 6's `git push` without the marker (:381-397). The lapse matters at step 3: while the marker is live, reviewer spawns neither count as review rounds nor meet the round-3 consult gate (`log-reviewer-round.sh:78-81`, `require-architect-consult.sh:81-84`).
31. The worktree guard and Verification 3's pipeline. `docs/worktree-bash-guard.md` lists five refused shapes, built on `$(...)`, `$CLAUDE_CONFIG_DIR`, an unquoted variable-built path, or `$PPID` (:21-27). Its convention for an ad-hoc call is one statement with no nested `$(...)` and no `$CLAUDE_CONFIG_DIR` (:112-114). The `grep | sort` pipeline matches none of the five shapes. `[verified: read]` `[verified: /plan-review round 4 ran grep -v '^[[:space:]]*$' <file> | LC_ALL=C sort > <new file>, and diff <a> <b> as separate single statements with /tmp targets, and the guard accepted each]` `[unverified: a ~/.local/state target, which <scratch> uses, and any && chain were not run under the guard]`

**Mechanisms:**
- M1: One file per leaf module. Scope splits at its scan seam: traversal, gap counting, and glob selection go in one file, and resolution, flags, headers, and ordinals in the other. `anchors: root, G3, row18, row21`. The alternatives each fail:
  - One scope file breaks G3.
  - A split by size alone has no subject a reader can predict.
- M2: Placement by owning module, with in-place splits only where a subset of a class reads `build_parser` or a `SKILL.md`. `anchors: G1, G6, row4, row5, row15`. The alternatives each fail:
  - Leaving `TestSingleLevelProjectsGlob` whole in L strands nine scope tests until the `cli.py` phase, which would have to split the class anyway.
  - Moving the `SKILL.md` literal test costs a new row target and nine `test_select_tests.py` expectation edits, all for one test (row 15).
  - Moving `TestHelpers` whole puts three `corpus._parse_ts` and three `scope._iso_date` tests in the render file against the placement rule and guarantees a later move, while the split cuts two contiguous blocks, six short tests in all (row 5).
- M3: Two commits per PR, an in-place commit and then a pure move. `anchors: row10`. A single commit checked by `nodes` without `--verbatim` was set aside: `nodes` cannot compare a split class or a non-name edit such as the `scope_path` change, so those changes would rest on review alone.
- M4: A scratch mover that cuts and pastes AST spans, so no moved line is retyped. `anchors: row8, row10`. The lighter primitives each fail:
  - Having the code-writer re-emit about 3,100 lines through Write risks a retyping slip in each one. `--verbatim` catches such a slip, but each slip costs a round.
  - Hand-read line ranges cut with `sed` miss decorator lines, and A1 and B1 shift the lines anyway. AST spans are exact.
- M5: Commit 1 is proved by a sorted-line diff of L against its baseline, whose `<` and `>` lines must be exactly the PR's listed ones, and by `test-ids` from the baseline to commit 2 with the commit-1 renames. `anchors: row5, row10, row31`.
  - A cut-and-paste regroup leaves L's non-blank lines unchanged as a multiset, apart from the listed lines. So the diff catches a changed character in a moved or retained line, a dropped decorator, an added comment or docstring, and an altered `class` line. In PR B it also covers the three `scope_path` functions, whose bodies `remainder --changed` exempts (row 10).
  - `test-ids` keys each test on `Class::name`, so a test in the wrong class prints `id-missing` and `id-unexpected` (row 10). A1's and B1's `-k` runs attribute a commit-1 breakage before commit 2 exists. They do not check membership, because a split test passes in either class, so a misplaced class boundary first shows at Verification 6's `test-ids`.
  - The diff misses line order, blank-line-only changes, and which class a non-test member sits in. Accepting that is reasonable: cutting and pasting whole methods does not reorder lines inside one, member order is cosmetic and `_move_fidelity.py` compares no order either (row 10), blank lines carry no behavior, and the three split classes hold no non-test member other than their docstrings (row 5).
  - A class docstring moved between a split class and its new class passes every commit-1 check: the diff sees the same lines, `remainder --changed` drops both classes, and `test-ids` keys carry no docstring. That gap is accepted, because a docstring changes no behavior.
  - A scratch AST comparator with mutation controls was set aside, because beyond the diff and `test-ids` it adds only member order.
- M6: Swap the glob for an exact path in the agents row. `anchors: row13, row14`. Keeping the glob as safe over-selection was set aside, because the row's comment states that each target reads the agents directory, which would become false.
- M7: Six `TEST_FILES` additions. `anchors: row11`.
- M8: Prose edits limited to row 17's L-naming sites, one replaced docstring, two one-line module docstrings, and one banner. `anchors: row3, row16, row17, row25`.
- M9: PR B is cut from `origin/main` after PR A merges. `anchors: row19, row20`. The alternatives each fail:
  - Independent cuts conflict (row 19).
  - Stacking PR B on PR A's unmerged branch fails because the squash merge forces a rebase that voids PR B's snapshots.
- M10: Four sequenced `code-writer` dispatches, A1, A2, B1, and B2. Each commit-1 tree is the before-image of its commit-2 proof. `anchors: row10`.
- M11: Proofs by `nodes`, `remainder` (on L, and as a census of each new file against a stub), `test-ids`, commit 1's sorted-line diff, and a run of each new file alone, then one selected-suite run. `anchors: G2, G4, G5, row12, row23, row29`.
- M12: Sync with `origin/main` only before any snapshot exists and after both commits' proofs, stopping on the conditions the Sync rule lists. `anchors: row27, row28`. The alternatives each fail:
  - A trigger on all of `claude/.claude/scripts/` halts on merges that touch no proof input (row 28).
  - A trigger on every test input halts on changes that can alter only test outcomes, which the selected suite reruns on the synced tree.
  - Rebasing and re-proving unattended needs a second proof procedure, with snapshots rebuilt from the rebased commits.
- M13: `/ready-for-review` step 2 as each PR's only selected-suite run, preceded by `test_shellcheck.py` alone and followed by one re-activation of the gate's active marker. `anchors: G2, G4, G5, row26, row27, row30`. The alternatives each fail:
  - A separate run before commit 2 doubles the longest step, because step 2 runs the suite again regardless (row 26). Staging the new files for such a run would also leave index entries that the Abort procedure's `git restore --source=HEAD` and `rm` do not clear.
  - A stop at Verification 0 on #1379's merge state holds every proof for a fix only the selected run reads, and it tests a PR number rather than what `test_shellcheck.py` resolves (G5).

## Critical files

**Dispatches.** Every dispatch goes to `code-writer` with `model: sonnet`. A dispatch does no staging and no commits. Each returns to the parent, which runs the named Verification steps and makes the commit. A1 runs before A2, and B1 before B2, because each commit-2 proof reads the tree its commit 1 left. PR B's dispatches start only after PR A merges (M9). A commit-2 move is one dispatch, not one per file, because the mover, the import rule, and the stop conditions are shared background. A hook, gate, or permission denial stops the dispatch or Verification step that hit it: report the denial text to the parent, which reports it to the engineer, and try no other command form, tool, or dispatch.
- **A1, the `TestHelpers` split (PR A commit 1).**
  - Files: L only.
  - Make exactly Approach "Commit 1" A1. Change no character inside a moved method.
  - Verification it runs:
    - `scripts/venv-run.sh ruff check claude/.claude/ claude-skills/ plugins/ evals/`
    - `scripts/venv-run.sh pytest -n0 claude/.claude/scripts/tests/test_transcript_analysis.py -k "TestHelpers or TestParseTs or TestIsoDate"`
  - Return the new classes' line numbers.
- **A2, the move and its consequences (PR A commit 2).**
  - Files:
    - Modify: L, `test_transcript_package_module_prefixes.py`, `select-tests.py`, `test_select_tests.py`, `test_transcript_context_composition.py`, and `docs/transcript-analysis-architecture.md`.
    - Create: the three PR A files.
  - **The mover.** Write `<scratch>/move_nodes.py`.
    - It takes L, the ordered move map, and the banner edits.
    - Each node's lines run from its first decorator line through `end_lineno`, the span `_move_fidelity.py` uses.
    - It appends each node, in map order, below the header already in its destination, two blank lines apart. Each file ends with one newline.
    - It deletes the nodes and banners from L, leaving two blank lines between the remaining top-level statements at each cut.
    - It exits non-zero and writes nothing when a name is missing or defined twice, or when a banner does not match exactly once.
  - **Order of work:**
    1. Write each new file's header with no imports yet.
    2. Run the mover.
    3. Add imports from `scripts/venv-run.sh ruff check --select F821 <file>`.
    4. Place them with `ruff check --select I001 --fix <file>`.
    5. Remove L's unused imports with `ruff check --select F401 --fix <L>`.
    6. Apply no other ruff fix, and never edit a moved node's text.
  - Make the other commit-2 edits exactly as Approach states. Write no other comment or docstring.
  - Stop and report on any of these:
    - a mover refusal;
    - an F821 name that only an L-local helper outside the map defines;
    - a new file of 1,000 lines or more;
    - any failure in its own verification.
  - Verification it runs:
    - `scripts/venv-run.sh ruff check claude/.claude/ claude-skills/ plugins/ evals/`
    - `scripts/venv-run.sh pytest -n0`, passing the three new files, L, `test_transcript_package_module_prefixes.py`, `test_select_tests.py`, and `test_transcript_analysis_architecture_doc.py`, all under `claude/.claude/scripts/tests/`.
    - `scripts/venv-run.sh pytest -n0` on each new file alone, one call per file.
  - Return:
    - the node count per file;
    - L's removed imports as `remainder` triples, such as `transcript_analysis:pricing` and `.conftest:_tool_result` (row 7);
    - `wc -l` of every changed and created file;
    - each prose edit as `file:line old → new`.
- **B1, two splits and the shim-free `scope.py` loads (PR B commit 1).**
  - Files: L only.
  - Make exactly Approach "Commit 1" B1. Change no character inside a moved method.
  - Verification it runs:
    - `scripts/venv-run.sh ruff check claude/.claude/ claude-skills/ plugins/ evals/`
    - `scripts/venv-run.sh pytest -n0 claude/.claude/scripts/tests/test_transcript_analysis.py -k "TestSingleLevelProjectsGlob or TestProjectsFlagHasNoArgparseType or TestRootCountDescDirectUnit or TestSkillFilesQuoteRootCountDescLiteral or test_projects_dir_honors or test_scope_import_does_not_crash or test_resolve_scan_roots_default_root"`
- **B2, the move and its consequences (PR B commit 2).**
  - Files:
    - Modify: L, `test_transcript_package_module_prefixes.py`, `test_transcript_cost.py` (the :2743 docstring only), and `docs/transcript-analysis-architecture.md`.
    - Create: the three PR B files.
  - Write `<scratch>/move_nodes.py` anew, in PR B's own `<scratch>`, to A2's mover spec. Follow A2's order of work, stop conditions, and return shape.
  - Verification it runs:
    - `scripts/venv-run.sh ruff check claude/.claude/ claude-skills/ plugins/ evals/`
    - `scripts/venv-run.sh pytest -n0`, passing the three new files, L, `test_transcript_package_module_prefixes.py`, and `test_transcript_analysis_architecture_doc.py`.
    - `scripts/venv-run.sh pytest -n0` on each new file alone, one call per file.

**Files, PR A** (branch `GH-1116/leaf-module-tests`):
- Create, all under `claude/.claude/scripts/tests/`: `test_transcript_pricing.py`, `test_transcript_redaction.py`, and `test_transcript_render.py`.
- Modify:
  - L, `claude/.claude/scripts/tests/test_transcript_analysis.py`;
  - `claude/.claude/scripts/tests/test_transcript_package_module_prefixes.py`;
  - `claude/.claude/scripts/select-tests.py`;
  - `claude/.claude/scripts/tests/test_select_tests.py`;
  - `claude/.claude/scripts/tests/test_transcript_context_composition.py`;
  - `docs/transcript-analysis-architecture.md`.
- Commit: `.claude/plans/leaf-module-tests.md`, this plan, as Verification 0 states.

**Files, PR B** (its own branch and worktree per `branch-management`, from `origin/main` after PR A merges):
- Create, all under `claude/.claude/scripts/tests/`: `test_transcript_scope.py`, `test_transcript_scope_scan.py`, and `test_transcript_corpus.py`.
- Modify: L, `claude/.claude/scripts/tests/test_transcript_package_module_prefixes.py`, `claude/.claude/scripts/tests/test_transcript_cost.py`, and `docs/transcript-analysis-architecture.md`.
- PR B does not edit this plan (row 20).

**Sync rule** (M12). The branch takes in `origin/main` only at Verification 0, before any snapshot exists, and at Verification 7, after both commits' proofs. Nothing rebases or merges between them, so every snapshot and junit baseline is read against the tree it was taken from.
- At Verification 0, rebase onto `origin/main` whenever it is ahead.
- At Verification 7, and before any sync `/ready-for-review` would make, list the incoming paths with `git diff --no-renames --name-only HEAD...origin/main`. Without `--no-renames`, an incoming rename lists only its new path, so a renamed Critical file would pass unseen.
  - If one is among the PR's Critical files, stop and report to the engineer without syncing. Such a change can alter what the proofs compared: for example, `origin/main` deleting a node that commit 2 moves can merge cleanly and leave the node re-added in its new file. Both commits are local, so the engineer chooses between rebasing and rerunning Verification 3 and 6, or restarting the PR on the new base.
  - Otherwise run `git rebase origin/main` (row 28) and continue. It cannot conflict, because the PR's commits touch only its Critical files.
  - A sync step that does not complete stops the run and goes to the engineer. The step is a rebase or merge that git refuses or halts on a conflict, or a push the remote rejects. The engineer aborts any rebase or merge in progress. The executor leaves it as it is.

**Abort procedure.** If a dispatch is interrupted, the parent recovers. The code-writer cleans nothing up.
1. `git status --short` must list only the PR's Critical files. On any other path, stop and report without touching anything.
2. Restore each modified file with its own `git restore --source=HEAD -- <path>` call.
3. Delete each of the PR's three new files that `git status --short` lists as untracked, with its own `rm -- <path>` call.
4. `git status --short` then prints nothing.
5. A restart reruns what was interrupted with the same `<scratch>`, whose snapshots still describe HEAD.

Never use `git clean`, `git checkout .`, `git reset --hard`, or `git stash`.

**Explicitly unchanged:**
- the repository root's `scripts/venv-run.sh`, whose PATH fix #1379 owns (G5);
- `tests/conftest.py`, `tests/_move_fidelity.py`, and every other `tests/_*.py` helper module;
- `transcript-analysis.py` and every module under `transcript_analysis/`;
- `docs/transcript-analysis.md` and CHANGELOG.md;
- every skill, hook, and rule file;
- every committed plan, this one included once PR A has committed it.

**Reuse:**
- `_move_fidelity.py`'s `nodes`, `remainder`, and `test-ids`, unchanged;
- `conftest.py`'s fixtures and record builders, imported unchanged;
- ruff's I001 and F401 fixes for import placement and removal.

**PR body carries:**
- `Refs GH-1116`.
- What moved: each new file's node count and the `test-ids` testcase totals.
- The two commits and each one's proof results.
- PR A also states the agents-row change and why the glob left that row.
- The selected-suite result. PR A's body also says `select-tests.py` widened to the full suite (row 12).
- The rollback statement: "Revert last in, first out: PR B before PR A. Reverting PR A alone after PR B merges conflicts in `test_transcript_analysis.py`, because both PRs cut adjacent regions. This is an inference; no revert was run." (row 19)
- No production code, CLI, or CHANGELOG change.
- The Out of scope follow-ups, listed in the body only.

## Verification

Run everything from the worktree root, each git command as its own literal call. Notation:
- `<scratch>` is `~/.local/state/leaf-module-tests/<pr>-<base>`, with `<pr>` `a` or `b` and `<base>` Verification 1's recorded SHA. It sits outside the repository and outside `/tmp`, because the proof chain spans hours. Each PR's scratch is its own.
- A step that reads a `<scratch>` file first lists it with one `ls --` call. A missing snapshot or junit baseline stops the run, and none is ever rebuilt from a later tree.
- `<tests>` is `claude/.claude/scripts/tests`, L is `<tests>/test_transcript_analysis.py`, and `<mf>` is `scripts/venv-run.sh python3 <tests>/_move_fidelity.py`.
- `<w1>` is the environment prefix `PYTEST_XDIST_AUTO_NUM_WORKERS=1` (G4). Every pytest and `select-tests.py` call carries it.
- A junit run into `<x>` is `<w1> scripts/venv-run.sh pytest`, passing L and each of the PR's new files that exists, with `--junitxml=<x>.xml`.
- Per-PR parameters:
  - PR A:
    - Commit-1 renames, six `--rename` pairs, `TestHelpers::<m>=TestParseTs::<m>` for each `test_parse_ts_*` method and `TestHelpers::<m>=TestIsoDate::<m>` for each `test_iso_date_*` method.
    - Commit-1 `remainder` flags: `--changed TestHelpers --added TestParseTs TestIsoDate`.
    - Commit-1 diff lines: exactly `> class TestParseTs:` and `> class TestIsoDate:`.
    - Commit 2: 17 moved names; seven deleted banners expected.
  - PR B:
    - Commit-1 renames, three pairs: `TestSingleLevelProjectsGlob::<m>=TestProjectsFlagHasNoArgparseType::<m>` for its two methods, and `TestRootCountDescDirectUnit::test_absent_state_literal_appears_in_both_skill_files=TestSkillFilesQuoteRootCountDescLiteral::test_absent_state_literal_appears_in_both_skill_files`.
    - Commit-1 `remainder` flags: `--changed TestSingleLevelProjectsGlob TestRootCountDescDirectUnit test_projects_dir_honors_claude_config_dir test_scope_import_does_not_crash_when_home_unset test_resolve_scan_roots_default_root_when_home_unset --added TestProjectsFlagHasNoArgparseType TestSkillFilesQuoteRootCountDescLiteral`.
    - Commit-1 diff lines, each with its four-space indentation where it has one:
      - `> class TestProjectsFlagHasNoArgparseType:` and `> class TestSkillFilesQuoteRootCountDescLiteral:`;
      - three `<     scope_path = _SCRIPT.parent / "transcript_analysis" / "scope.py"` lines and three `>     scope_path = Path(scope.__file__)` lines;
      - one `<` line for each non-blank line of `test_projects_dir_honors_claude_config_dir`'s old docstring, eight in all (:109-111 and :113-117 at 28c5d3e1), and `>     """Execs a fresh copy of scope.py because the imported module caches PROJECTS_DIR on first access."""`.
    - Commit 2: 30 moved names; two deleted banners and one changed banner expected.

Per PR, in order:

0. **Preconditions.**
   - `~/.claude/scripts/autonomous-shipping-active.sh` exits 0 in the session that makes each commit, push, or `gh pr create`. A session that resumes, restarts, or lands a fix commit past this step runs it again before its first such act. A non-zero exit means stop before that act and ask the engineer, because row 2's quote does not carry to another session.
   - `scripts/venv-run.sh pytest --version` and `scripts/venv-run.sh ruff --version` each exit 0.
   - PR A first runs `git diff --cached --name-only`, which must print exactly `.claude/plans/leaf-module-tests.md`. Any other path stops the step and goes to the engineer. It then commits the plan with the message `Refs GH-1116`, after which `git status --short` prints nothing.
   - Run `git fetch origin main`, then `git rev-list --count HEAD..origin/main`. A non-zero count means run `git rebase origin/main` now, before any snapshot exists (Sync rule). A rebase that does not complete stops the run (Sync rule).
   - PR B, in a fresh worktree from `origin/main`:
     - `git grep -n "TRANSCRIPT_REDACTION_TEST_PATH" origin/main -- claude/.claude/scripts/select-tests.py` hits.
     - `git grep -n "^class TestParseTs" origin/main -- <tests>/test_transcript_analysis.py` hits.
     - Otherwise stop and report that PR A is not in this base.
1. **Baseline.**
   - Run `git rev-parse HEAD` and record it as `<base>`. Run `mkdir -p ~/.local/state/leaf-module-tests`, then `mkdir <scratch>`. If the second `mkdir` fails because the directory exists, stop.
   - Copy L to `<scratch>/L-base.py`. Copy every other file the PR modifies to `<scratch>/before-<basename>`. Use one `cp` call per file.
   - Do a junit run into `<scratch>/base`.
2. **Dispatch commit 1** (A1 or B1).
3. **Commit-1 proof** (M5).
   - Sorted-line diff. Run `grep -v '^[[:space:]]*$' <scratch>/L-base.py | LC_ALL=C sort > <scratch>/base.sorted`, then the same on L into `<scratch>/c1.sorted`, then `diff <scratch>/base.sorted <scratch>/c1.sorted`. Its `<` and `>` lines, counted with repeats, must be exactly the PR's commit-1 diff lines, and any other line fails. Its change-command lines, such as `812a813`, and its `---` separators carry no file content.
   - `<mf> remainder --before <scratch>/L-base.py --after <L>` with the PR's commit-1 flags is clean.
   - `scripts/venv-run.sh ruff check claude/.claude/ claude-skills/ plugins/ evals/` is clean.
4. **Commit 1.** Run `/code-review`. Then stage L with `git add <path>` and commit with `Refs GH-1116`. Copy L to `<scratch>/L-c1.py`.
5. **Dispatch commit 2** (A2 or B2).
6. **Commit-2 proof.**
   - `<mf> nodes --before <scratch>/L-c1.py --after <each new file> --moved <every moved name> --verbatim <every moved name>` exits 0, printing `compared-nodes: 17` for PR A and `compared-nodes: 30` for PR B.
   - `<mf> remainder --before <scratch>/L-c1.py --after <L> --removed <every moved name> --import-removed <the dispatch's triples>`. Every finding must be a `remainder-comments` line for one of the PR's listed banners. A `remainder-ast`, `import-diff`, or `declared-mismatch` finding stops the run.
   - New-file census, for each new file:
     - Write `<scratch>/stub-<basename>`: empty for PR A's files, and for each scope file its one-line docstring from Approach "New-file shape".
     - `<mf> remainder --before <scratch>/stub-<basename> --after <new file> --added <the file's moved names>` prints `remaining-statements: 0`, or `1` for a scope file. Every finding it prints, and so its exit 1, comes from `import-diff: undeclared added import` lines. The file then holds only imports, its declared docstring, and its moved names, with no comment outside them (row 29).
   - `wc -l` of each new file is under 1,000 (G3).
   - `git status --short` lists exactly the dispatch's Files, each Modify path as modified and each Create path as untracked (row 23).
   - PR A only, the agents-directory readers (rows 13, 14). Use the Grep tool on `<tests>` with glob `test_transcript_analysis*.py` and pattern `_repo_tracked_agent_type_names|_REPO_AGENT_DEFINITIONS_DIR|_partition_spawn_counts_by_disclosure|cmd_subagent_mix|cmd_cost_counts`. Any hit outside this list stops the run, because the agents row would no longer select that file:
     - L's `_UNCONDITIONAL_HEADER_CASES` row for `subagent-mix`, whose args set `"this_repo": False`;
     - the three docstring lines of `TestParseAbsoluteWindowArgs` that name `cmd_subagent_mix`;
     - the `cmd_subagent_mix` call in `test_transcript_analysis_branch_attribution.py`, whose args set `"this_repo": False`.
   - Prose checks, using the Grep tool with `multiline: true` on the edited files. A wrapped match is still a match. Each pattern must match nothing, and its probe on the `<scratch>/before-*` copy must match.
     - PR A, the architecture doc: `TestIsFreshUserPrompt\`\s+stays` and `Each\s+of\s+\`corpus\.py\``. `test_transcript_context_composition.py`: `test_transcript_analysis\.py's\s+(own\s+)?'?representative`.
     - PR B, the architecture doc: `TestSubagentFormatContract\`\s+stays` and `\`scope\.py\`\s+are\s+exercised`. `test_transcript_cost.py`: `test_transcript_analysis\.py's\s+test_skipped_counts`.
   - `scripts/venv-run.sh ruff check claude/.claude/ claude-skills/ plugins/ evals/` is clean.
   - Each new file passes alone: run `<w1> scripts/venv-run.sh pytest -n0 <new file>` once per new file. Every other run here imports L in the same process, and PR A's agents row selects `test_transcript_redaction.py` without L.
   - Do a junit run into `<scratch>/c2`. `<mf> test-ids --before <scratch>/base.xml --after <scratch>/c2.xml`, with the PR's commit-1 renames, is clean, and its before and after totals are equal. This also proves commit 1's test membership (M5). An `id-missing` or `id-unexpected` finding that names a class commit 1 split or created stops the run and goes to the engineer, because commit 1 is already committed and no snapshot is rebuilt from a later tree.
7. **Commit 2 and sync.**
   - Run `/code-review`. Then stage each changed or created file with its own `git add <path>` call, and commit with `Refs GH-1116`.
   - A `deny-private-project-refs` denial on any text this PR moves or authors stops the run and goes to the engineer. Never edit moved text to pass the hook.
   - Run `git fetch origin main`, then apply the Sync rule's Verification 7 check.
   - PR A: rerun step 6's agents-directory check on the synced tree.
8. **Selected suite and hand-off** (G2, M13).
   - Run `/ready-for-review`, which opens the PR with the body Critical files lists. Its step 2 is the PR's only selected-suite run (row 26). That run follows commit 2, so tracked-file scanners such as `test_ticket_reference_discipline.py` see the new files (row 27).
   - In step 2, first run `<w1> scripts/venv-run.sh pytest -n0 claude/.claude/hooks/tests/test_shellcheck.py`. If it fails, stop and report to the engineer that both commits and all proofs are done, and that the selected run would fail on G5 until #1379 or an equivalent fix lands. A later run resumes at Verification 7's `git fetch origin main`.
   - Then run `<w1> scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py` as a background Bash call, and edit nothing until it finishes. In PR A it selects the full suite (row 12), whose duration on this machine is unmeasured. In PR B the scoped run takes 37 to 51 minutes (G4).
   - Only while the `/ready-for-review` run that activated the marker is still in flight (its step 9 has not run and the run has not halted or deferred), re-run `~/.claude/scripts/marker.sh activate ready-for-review` after the run returns, because the wait can outlast the gate's active marker (row 30). Otherwise leave the marker alone and report to the engineer.
   - The run must pass. A failing `timing`-marked test is rerun alone with `<w1> scripts/venv-run.sh pytest -m timing -n0 <node id>`, per the marker's own definition in `pyproject.toml`, before it counts.
   - Check a failure in a file outside Critical files at the merge base, per `claude/.claude/CLAUDE.md`'s "Prove your change caused a failing check" bullet and `ready-for-review/SKILL.md`'s "Pre-existing failures" paragraph (:64). Run `git merge-base HEAD origin/main` as its own call and write the printed SHA literally into `git worktree add`, because the bullet's `$(...)` form is a shape the worktree guard refuses (row 31). The rerun's collected paths must begin with the new worktree's root. A failure that reproduces at the merge base stops at step 2 and goes to the engineer, and no separate branch is opened (row 26). One that does not is this PR's to fix.
   - A fix never edits a moved node's text. It lands as its own commit through `/code-review`, and `/ready-for-review` then returns to step 2 (row 26). A prose fix also reruns step 6's prose checks.
   - Before `/ready-for-review` syncs, in its step 1 or after its step 6 sends it back there (row 28), apply the Sync rule's check and record `git rev-parse origin/main` when the check runs. After the sync, if `git rev-parse origin/main` prints a different SHA, stop and report to the engineer, because the sync took in a commit the check did not list. A sync that does not complete stops the run under that rule's last bullet, whoever runs it. PR A then reruns Verification step 6's agents-directory check on the synced tree. The rerun completes, and stops the run on a hit, before `/ready-for-review` re-runs its step 2 on the synced tree.
   - PR A's run ends with its PR open, unless a stop reported to the engineer ends it first. PR B starts only after the engineer merges PR A.

## Out of scope

- **The `cli.py` phase's classes**, which stay in L: `TestConfigDirFlag`, `TestConfigDirRouting`, `TestCacheEfficiencyArgparseWiring`, `TestBuildParser`, `TestProjectsFlagHasNoArgparseType`, `_UNCONDITIONAL_HEADER_CASES` with its two classes, and the loader (G1).
- **Cross-group classes**, which stay in L under G6: `TestCacheRebuildCrossInstrumentReconciliation`, `TestFormatDriftCanary`, `TestMultiRootFormatOutliers`, `TestPerRecordConsumersCreditPrefixToTheBranchEntered`, and `TestSubagentRecordsFollowTheDispatchingBranch`.
- **The `SKILL.md` pins in L**, `TestSkillFilesReportObservedScopeNotUnionGuarantee` and `TestSkillFilesQuoteRootCountDescLiteral`. Moving them needs the `select-tests.py` row change row 15 describes.
- **`TestTableColsHelper`.** It tests a `conftest.py` helper, not a leaf module. `test_transcript_shared_fixtures.py` is a candidate home. DEFER: outside the Ask.
- **L staying over 1,000 lines**, about 1,440 after both PRs (row 18). The `cli.py` phase shrinks it further.
- **Renaming `TestHelpers`** in the render file. A rename turns 20 test IDs into renames and makes the move unprovable by `nodes --verbatim`.
- **Prose that was already stale and that this change neither causes nor touches**, listed in PR A's body as follow-ups:
  - `test_transcript_context_composition.py` :616-617 cites `TestCostMultiRootReport` in L, and L has no such class.
  - `claude/.claude/hooks/tests/test_check_claude_md_length.py` :889-890 cites L for a test in `test_transcript_denials.py` (:324).
  - `select-tests.py` :112 says the glob has "two siblings", but it matches six files.
- **`TestConfigDirFlag`'s stale docstring prose** (row 17). PR B makes its "covers above" false, and its import-time wording, like :202-205's, was already false. Editing it would add `--changed TestConfigDirFlag` to PR B's commit-1 `remainder`, which drops that whole class from the comparison, for a few docstring words in a class the `cli.py` phase moves and owns. PR B's body lists it as a follow-up.
- **The banner between the PRs** (row 17). Until PR B merges, `# _table_cols self-tests` heads nine nodes it does not describe, and PR B's moves end that. Keeping `# Helpers` until PR B was set aside: it would still head `TestScanRootTranscripts` and the corpus nodes, and it changes both PRs' banner counts and proof expectations, all for an interim state. PR A's body states it.
- **The governing plan's revert rehearsal**, which the engineer waived for both PRs (row 24).
- **The `venv-run.sh` PATH fix** behind G5's `test_shellcheck.py` failures. Open PR #1379 owns it. This plan neither duplicates it nor adds a PATH prefix of its own, and Verification 8 checks for its effect just before the selected run.
- **The active marker lapsing during a long step 2** (row 30). Its single home is `/ready-for-review` step 2, which could re-activate the marker after a long run as the skill already does on its CI-watch path (`claude-skills/skills/ready-for-review/SKILL.md` § "CI watch (out-of-band)", :198). That is a follow-up in the skill. This plan leaves skill files unchanged and carries only Verification 8's one re-activation line.
- **Committing the mover**, a single-use scratch tool.
- **GH-1116 tracker edits.** No comment is posted on the tracker; the PR bodies state what each PR moved.
