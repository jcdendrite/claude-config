# GH-1116: final `cli.py` phase and the transcript-analysis test-placement rule

## Context

Goal: finish the transcript-analysis decomposition by moving `build_parser()` and `main()` out of the shim into `transcript_analysis/cli.py`, relocating the last classes out of the legacy `test_transcript_analysis.py`, and recording a test-placement rule so a later contributor never has to decompose the tests again.

Ask: "do we need to modify the architecture markdown for transcript analysis to indicate exactly when to add to test_transcript_analysis versus create a new test suite file ? I don't want us to have to do decomp again" (engineer, this session). Scope decisions, engineer's selected labels: "Two PRs (Recommended)", "Move verbatim, record the overage (Recommended)", "Shim re-exports both names (Recommended)", "Yes, add it (Recommended)". On the gap between the size check's two halves the engineer said: "I accept the gap between F1 and F2. I aim to go from F1 to F2 quickly".

The decomposition (tracker GH-1116, closed) has moved every leaf module and every command group into `claude/.claude/scripts/transcript_analysis/`. The shim `claude/.claude/scripts/transcript-analysis.py` still holds `build_parser()` and `main()`, because while commands lived in the shim a `cli.py` would have had to import back into it. The governing plan, `.claude/plans/transcript-analysis-decomposition.md` (final phase, lines 155-161), commits this phase to moving them and to relocating `_UNCONDITIONAL_HEADER_CASES` with its two classes atomically.

The previous phase (PRs #1382 and #1387, `.claude/plans/leaf-module-tests.md`) moved the leaf modules' tests out of the legacy test file. `docs/transcript-analysis-architecture.md` § "Tests" records where each module's tests went but states no rule for where a new test goes, so the next contributor defaults to the file that already carries the loader boilerplate. That is how the legacy file grew. An architect consult this session found the first draft of the rule wrong in three ways:
- It routed per-command argparse wiring to the legacy file, although thirteen test files already load their own shim copy for it.
- It gave cross-cutting tests an unbounded home.
- It ignored file size, which is what forced the decomposition.

The code-file size check is not scheduled and enforces nothing today. It has two stages: #1119's F1 offender assessment, then its F2 check. The sources are `.claude/plans/code-file-size-splits.md` and `docs/design-decisions/code-file-line-limit.md`. This plan therefore verifies size by `wc -l` on the files it creates, as `leaf-module-tests.md` did, and leaves enforcement to F2.

## Approach

PR 1 moves every test out of `test_transcript_analysis.py` (below, L) into the file its subject names and deletes L. It also ships the placement rule as a path-scoped `.claude/rules/transcript-analysis-tests.md`, which `docs/transcript-analysis-architecture.md` § "Tests" cites, and a structural test that every package module has a test file named for it. PR 2 is cut from `origin/main` after PR 1 merges. It moves `build_parser()` and `main()` verbatim into `transcript_analysis/cli.py`, leaves the shim as an entry point that re-exports both, and points `test_transcript_cli.py` at `cli` directly. Neither PR changes CLI behavior.

**Where each L node goes (PR 1).** Each test goes to the file named for its subject: the code whose defect it would catch.
- `tests/test_transcript_cli.py`, new: L's loader block (:86-93), `TestConfigDirFlag`, `TestConfigDirRouting`, `test_review_cost_by_size_without_this_repo_is_refused_through_main`, `TestCacheEfficiencyArgparseWiring`, `TestBuildParser`, `TestProjectsFlagHasNoArgparseType`. These are every L test that reads `build_parser` or `main` (row 6). One file, not separate `main` and `build_parser` files: together they come to about 360 lines (row 10), and the rule names one file (M3).
- `tests/test_transcript_analysis_multi_root_scope.py`, new: `_fake_gh_pr_list_run`, `_UNCONDITIONAL_HEADER_CASES` with the comment block directly above it (:672-676), `TestAllSubcommandsSingleRootHeader`, `TestRootsThreadingSpy`, `TestMultiRootFormatOutliers`, `TestSkillFilesQuoteRootCountDescLiteral`, `TestSkillFilesReportObservedScopeNotUnionGuarantee` (M5).
- `tests/test_transcript_subagents_format_drift.py`, new: `TestFormatDriftCanary`, which after commit 1 holds only its four `cmd_subagents` tests (M4).
- Existing files, each gaining nodes at its end:
  - `tests/test_transcript_skill_pair.py`: `TestSkillPairFormatDriftCanary`.
  - `tests/test_transcript_cache_efficiency.py`: `TestCacheEfficiencyFormatDriftCanary`.
  - `tests/test_transcript_cache_rebuild.py`: `TestCacheRebuildCrossInstrumentReconciliation`.
  - `tests/test_transcript_shared_fixtures.py`: `TestTableColsHelper`.
  - `tests/test_transcript_analysis_branch_attribution.py`: `TestPerRecordConsumersCreditPrefixToTheBranchEntered`, `TestSubagentRecordsFollowTheDispatchingBranch`.
- L's docstring, imports, and six banners are dropped, and L is deleted.

**PR 1 commit 1, in place in L.** Split `TestFormatDriftCanary` (row 7). It keeps its four `cmd_subagents` tests. Directly after it, insert `TestSkillPairFormatDriftCanary`, holding `test_drift_warning_also_fires_in_skill_pair`, then `TestCacheEfficiencyFormatDriftCanary`, holding the four `*_cache_efficiency*` tests. Methods keep their original order and text, and neither new class gets a docstring.

**PR 1 commit 2, the move.** A scratch mover cuts and pastes AST spans, so no moved line is retyped (M7).
- New-file headers: imports, then for two files a one-line module docstring above them:
  - `test_transcript_analysis_multi_root_scope.py`: `"""Every subcommand's resolved-scope header and roots threading, plus the SKILL.md text describing multi-root scope."""`
  - `test_transcript_subagents_format_drift.py`: `"""subagents.py's format-drift canary; test_transcript_subagents.py holds subagents.py's other tests."""`
  - `test_transcript_cli.py` gets no docstring. Its header is its imports, then L's loader block verbatim.
- Imports: stdlib and third-party lines, one unaliased `from transcript_analysis import <modules>` line, and relative `from .conftest import ...` and `from ._<family>_helpers import ...` lines (`.claude/rules/test-tree-packaging.md`).
- `test_transcript_package_module_prefixes.py`: in `TEST_FILES`, replace the `"test_transcript_analysis.py"` entry with `"test_transcript_cli.py"`, then append `"test_transcript_analysis_multi_root_scope.py"` and `"test_transcript_subagents_format_drift.py"`. That takes it from 66 entries to 68 (row 12).
- Nothing else changes in this commit. Authored text waits for commit 3, so commit 2's proofs expect no finding beyond imports (M6).

**PR 1 commit 3, authored text.**
- **The rule file**, `.claude/rules/transcript-analysis-tests.md` (M8). It holds the fenced frontmatter below, a blank line, the heading line `## Where a transcript-analysis test goes`, a blank line, then the fenced body below. Write it exactly as given.

```markdown
---
paths:
  - "claude/.claude/scripts/transcript-analysis.py"
  - "claude/.claude/scripts/transcript_analysis/**"
  - "claude/.claude/scripts/tests/test_transcript_*.py"
  - "claude/.claude/scripts/tests/test_author_outcome.py"
  - "claude/.claude/scripts/tests/_*_helpers.py"
  - "claude/.claude/scripts/tests/conftest.py"
---
```

```markdown
Paths below are under `claude/.claude/scripts/`. Put each test in the file named for its subject: the
code whose defect the test would catch. A module the test runs only to build fixtures, or reads only as an
oracle to agree with, is not its subject.

- **One package module:** `tests/test_transcript_<module>.py`, or `tests/test_transcript_<module>_<seam>.py`
  once that module's tests are split. A new module's test file ships in the change that creates the module.
- **A shared helper reached through one command:** the command's file when the test pins that command's call
  or the values it passes, and the helper's own module's file when the command is only a convenient driver.
- **One command's CLI wiring** (its flags, defaults, and `set_defaults(func=...)`): the file of the module
  that registers its subparser, or `tests/test_transcript_cli.py` when `build_parser()` registers it inline.
- **`build_parser()` or `main()` across commands**, such as a walk of every subparser or `main()`'s
  top-level `--config-dir` handling: `tests/test_transcript_cli.py`.
- **An invariant several modules share:** `tests/test_transcript_analysis_<invariant>.py`. A test that
  reads a `SKILL.md` by path needs that prefix, or its own `select-tests.py` row, so a skill edit selects it.
- **A structural test over package source:** a file named for the property it checks.
- **Helpers:** one family's shared helpers go in `tests/_<family>_helpers.py`. `tests/conftest.py` holds
  only fixtures and helpers several families share, and their own tests go in
  `tests/test_transcript_shared_fixtures.py`.

No test file is a catch-all named for the tool as a whole.
`tests/test_transcript_analysis_module_test_files.py` fails when a package module has neither a test file
named for it nor an entry in that file's `MODULES_TESTED_ELSEWHERE`.

**Size.** `docs/design-decisions/code-file-line-limit.md` sets the line limit, for test files as for code.
A test file that would cross it splits at a named seam, and the new file's suffix names that seam. Never
split by position, as in `_part2`.
```

- **The structural test**, `tests/test_transcript_analysis_module_test_files.py` (M9).
  - Its docstring is `"""Pins that every transcript_analysis module has a test file named for it, or a MODULES_TESTED_ELSEWHERE entry naming the file that tests it. The match is by file name only and does not show that the file covers the module."""`, wrapped under ruff's 130-column line length.
  - `MODULES_TESTED_ELSEWHERE` maps `"author_outcome"` to `"test_author_outcome.py"`, `"cache_rebuild_rules"` to `"test_transcript_cache_rebuild_ttl_rules.py"`, and `"sessions"` to `"test_transcript_analysis_multi_root_scope.py"` (row 23). The one comment above it reads `# Modules whose tests live in a file named for something else, mapped to that file.`
  - Listing helpers, the only functions that read the tree:
    - `_package_module_names()` returns the stems of `transcript_analysis/*.py` other than `__init__`.
    - `_transcript_test_stems()` returns the stems of `tests/test_transcript_*.py`.
    - `_test_file_names()` returns the names of the files in `tests/`.
  - Pure functions, which take every input as an argument and read nothing from disk. The two `_violations` functions follow `rule_frontmatter_violations` (row 34): each returns a list of violation strings, empty when its input passes.
    - `_owning_module(test_stem, module_names)` returns the longest module name `m` for which `test_stem` is `test_transcript_<m>` or starts with `test_transcript_<m>_`, and `None` when no module matches. Its result must not depend on the order of `module_names`.
    - `_owned_modules(test_stems, module_names)` returns the set of non-`None` owners of `test_stems`.
    - `_unnamed_module_violations(module_names, test_stems, entries)` returns one string per module that is neither owned nor a key of `entries`. Each names the module and both remedies.
    - `_entry_violations(module_names, test_stems, entries, test_file_names)` returns one string per key that is not a module, per key that is owned, and per value that `test_file_names` lacks. Each names the key or the value.
  - It has eight tests. The first two run against the real tree, passing the listing helpers' results and `MODULES_TESTED_ELSEWHERE`:
    1. `test_every_package_module_has_a_named_test_file_or_an_entry` asserts `_unnamed_module_violations` returns `[]`.
    2. `test_every_entry_names_a_module_with_no_named_file_and_an_existing_test_file` asserts `_entry_violations` returns `[]`.
    3. `test_owning_module_takes_the_longest_matching_module_name` is parametrized over two module-name lists, `["cost", "cost_ledger", "pr_cost"]` and `["cost_ledger", "pr_cost", "cost"]`, so the shorter name comes both first and last. For each list it expects `cost_ledger` for `test_transcript_cost_ledger_record_gates`, `cost` for `test_transcript_cost_counts`, `pr_cost` for `test_transcript_pr_cost`, and `None` for both `test_transcript_costly` and `test_transcript_analysis_branch_attribution`.
    4. `test_package_directory_is_not_empty` is the non-vacuity guard: both the real module set and the real owned set are non-empty.
    5. `test_module_with_no_test_file_and_no_entry_is_reported`: module names `["alpha"]`, no test stems, no entries. `_unnamed_module_violations` returns exactly one string, naming `alpha`.
    6. `test_entry_whose_key_is_not_a_module_is_reported`: module names `["alpha"]`, test stems `["test_transcript_alpha"]`, entries `{"beta": "test_beta_elsewhere.py"}`, file names `["test_transcript_alpha.py", "test_beta_elsewhere.py"]`. `_entry_violations` returns exactly one string, naming `beta`.
    7. `test_entry_whose_key_is_owned_is_reported`: module names `["alpha"]`, test stems `["test_transcript_alpha"]`, entries `{"alpha": "test_alpha_elsewhere.py"}`, file names `["test_transcript_alpha.py", "test_alpha_elsewhere.py"]`. `_entry_violations` returns exactly one string, naming `alpha`.
    8. `test_entry_whose_test_file_is_missing_is_reported`: module names `["alpha"]`, no test stems, entries `{"alpha": "test_alpha_elsewhere.py"}`, no file names. `_entry_violations` returns exactly one string, naming `test_alpha_elsewhere.py`.

    Each synthetic input in tests 5-8 trips one branch only, so "exactly one" also shows the other branches stay quiet on it.
- **`docs/transcript-analysis-architecture.md` § "Tests"** (row 15). The section stays a catalog. Each edit below anchors on content and rewraps only the lines it touches.
  - Right under the heading, add `` `.claude/rules/transcript-analysis-tests.md` sets which file a new test goes in. This section records where the existing tests live. ``
  - After that paragraph, add a paragraph that names the seven tests M3 leaves in the cli file as exceptions to the rule (row 8). Its lead line is "`tests/test_transcript_cli.py` holds seven tests that are exceptions to the rule's bullet on one command's CLI wiring. Each belongs in its command's file:". Then a four-item list:
    - "`TestCacheEfficiencyArgparseWiring`'s two tests, in `tests/test_transcript_cache_efficiency.py`"
    - "`TestBuildParser`'s `test_this_repo_and_projects_mutually_exclusive_on_skill_invocation` and `test_bare_skill_invocation_leaves_projects_unset`, in `tests/test_transcript_skill_invocation.py`"
    - "`TestBuildParser`'s `test_review_cost_by_size_all_accounts_flag_wires_to_all_accounts`, in `tests/test_transcript_review_cost_by_size.py`"
    - "`TestBuildParser`'s `test_pr_link_without_repo_flag_defaults_to_none` and `test_pr_link_with_repo_flag_parses_value`, in `tests/test_transcript_pr_link.py`"

    The paragraph gives no reason they stay, so PR 2's switch of the cli file to `cli` leaves it true.
  - :782-785. Replace both sentences with: "`TestProjectsFlagHasNoArgparseType` lives in `tests/test_transcript_cli.py` because it walks every subparser `build_parser()` registers. `TestSkillFilesQuoteRootCountDescLiteral` lives in `tests/test_transcript_analysis_multi_root_scope.py`, which `select-tests.py` selects as a `SKILL.md` reader."
  - :811. Change "the same boilerplate `test_transcript_analysis.py` uses" to "the same boilerplate `tests/test_transcript_cli.py` uses".
  - :828. After "requests them from a file that loads no shim.", add "It also holds `TestTableColsHelper`, the tests of `conftest.py`'s `_table_cols`."
  - :857-862. Replace from "`TestCacheRebuildCrossInstrumentReconciliation` stays in" through "every group it references has moved." with: "`TestCacheRebuildCrossInstrumentReconciliation` lives in `tests/test_transcript_cache_rebuild.py`: it tests cache-rebuild's sidechain W5m accumulator, and runs cache-efficiency only as the oracle that accumulator must agree with."
  - :871-873. Replace with: "`TestMultiRootFormatOutliers` lives in `tests/test_transcript_analysis_multi_root_scope.py` rather than with the rest of the family: it spans cost, audit-routing-samples, and judgment-pair."
  - :898-899. After the `test_transcript_subagents.py` bullet, add the bullet "- `tests/test_transcript_subagents_format_drift.py` covers `cmd_subagents`'s format-drift canary".
  - :907-911:
    - "The first three share" becomes "The first four share".
    - "`tests/test_transcript_analysis.py` imports it too. All four consumers" becomes "`tests/test_transcript_analysis_multi_root_scope.py` imports it too. All its consumers".
    - "used by more than one file, or by `tests/test_transcript_analysis.py` as well:" becomes "used by more than one file:".
  - :913-914. Replace the `TestFormatDriftCanary` sentence with: "Each command's format-drift canary tests live with that command: `cmd_skill_pair`'s in `tests/test_transcript_skill_pair.py` and `cmd_cache_efficiency`'s in `tests/test_transcript_cache_efficiency.py`."
  - :946. Change "`tests/test_transcript_analysis.py` imports only" to "`tests/test_transcript_analysis_multi_root_scope.py` imports only".
  - :957-959. Change "more than one of those files, or `tests/test_transcript_analysis.py`, reads. The legacy file imports it too, as" to "more than one of those files, `tests/test_transcript_analysis_multi_root_scope.py`, or `tests/test_transcript_analysis_branch_attribution.py` reads. Each imports it as".
  - :969-974:
    - "or `tests/test_transcript_analysis.py`, reads. The legacy file imports it too, as" becomes "or `tests/test_transcript_analysis_branch_attribution.py`, reads. Each imports it as".
    - "stays in `tests/test_transcript_analysis.py` because it also spans" becomes "lives in `tests/test_transcript_analysis_branch_attribution.py` because it also spans".
  - :981-983:
    - "`tests/test_transcript_analysis.py`'s cross-subcommand header table both read. The legacy file imports it too, as" becomes "`tests/test_transcript_analysis_multi_root_scope.py`'s cross-subcommand header table both read. Both import it as".
    - "`sessions.py` has no test file of its own. The cross-subcommand header table covers its header." becomes "`sessions.py` has no test file of its own: that header table covers its header."
  - :992-996:
    - "and `tests/test_transcript_analysis.py` both read. The legacy file imports it too, as" becomes "and `tests/test_transcript_cache_rebuild.py` both read. Both import it as".
    - "`TestCacheEfficiencyArgparseWiring` and `TestConfigDirRouting` stay in `tests/test_transcript_analysis.py` because they read `build_parser` and `main`, which only the shim has." becomes "`TestConfigDirRouting` lives in `tests/test_transcript_cli.py` with `main()`'s other `--config-dir` tests. `TestCacheEfficiencyArgparseWiring` is there too, one of the exceptions named at the start of this section."
  - :1000-1001. Change "in `tests/test_transcript_analysis.py` reads" to "in `tests/test_transcript_analysis_multi_root_scope.py` reads".
- **Helper-module docstrings** (rows 13, 14). In each, swap the L name for the files that now read it, rewrapping under ruff's 130-column line length:
  - `_subagent_helpers.py`: `test_transcript_subagents.py,` becomes `test_transcript_subagents*.py,`, and `test_transcript_analysis.py's` becomes `test_transcript_analysis_multi_root_scope.py's`.
  - `_audit_routing_helpers.py`, `_handoff_nudge_helpers.py`, `_commit_gate_helpers.py`, `_cache_rebuild_helpers.py`: `test_transcript_analysis.py's` becomes `test_transcript_analysis_multi_root_scope.py's`.
  - `_session_signal_helpers.py`: `test_transcript_analysis.py's` becomes `test_transcript_analysis_branch_attribution.py's`.
  - `_skill_helpers.py`: "and by test_transcript_analysis.py's cross-subcommand tables." becomes "and by test_transcript_analysis_multi_root_scope.py's cross-subcommand tables and test_transcript_analysis_branch_attribution.py's per-record attribution classes."
  - `_cache_efficiency_helpers.py`: "and by test_transcript_analysis.py's cross-group cache-efficiency tests." becomes "and by test_transcript_cache_rebuild.py's cross-instrument reconciliation test."
- **Other prose naming L** (row 13):
  - `conftest.py` :1600: `test_transcript_analysis.py's` becomes `test_transcript_subagents_format_drift.py's`.
  - `test_transcript_cost.py` :4392-4394: the banner's middle lines become the single line `# format-drift canary (cost side)`.
  - `test_transcript_shared_fixtures.py`: its docstring becomes `"""Tests of conftest's shared fixtures and helpers. This file deliberately loads no transcript-analysis.py shim, so a fixture that comes to depend on the shim fails here first."""`, wrapped under ruff's 130-column line length.
  - `select-tests.py` :112-115: replace those lines with `# test_transcript_analysis_multi_root_scope.py, one of the files the glob` / `# below matches, reads specific SKILL.md files by path, not by import.` / `# Domain-narrowing can't see that dependency, so it's declared here as a` / `# cross-domain exception rather than folded into the scripts domain rule.` Lines :116-118 stay.

**PR 2 commit 1, the move.**
- `transcript_analysis/cli.py`: line 1 is `"""build_parser() and main() for transcript-analysis.py, which re-exports both."""`, line 2 is blank, and the rest is the shim's lines from `import argparse` through `main()`'s last line, byte for byte. At 0a66c019 those are :7-1021, so the file comes to 1,017 lines (row 32). It is built with `sed`, never retyped.
- `transcript-analysis.py` keeps lines 1-6, its shebang, docstring, and the blank line after them. Lines 7-14 are exactly the block below, with one blank line before the comment and two before the guard. Its executable bit stays.

```python
from transcript_analysis.cli import build_parser, main

# Test files read both names from a loaded copy of this script, and tests/_move_fidelity.py reads build_parser.
__all__ = ["build_parser", "main"]


if __name__ == "__main__":
    main()
```

- `test_transcript_analysis_cost_import_direction.py` (row 18). Point it at `cli.py`:
  - `SHIM_SCRIPT` becomes `CLI_MODULE`, the path `REPO_ROOT / "claude" / ".claude" / "scripts" / "transcript_analysis" / "cli.py"`.
  - Rename every identifier containing `shim` to its `cli` form: `SHIM_IMPORT_SOURCE_MODULE`, `SHIM_BACK_IMPORTED_COST_NAMES`, `_shim_imported_cost_names`, `test_shim_back_import_from_cost_matches_sanctioned_set`, `test_shim_still_imports_from_cost_module`.
  - In docstrings and messages, "the shim" and "transcript-analysis.py" become "cli.py". After the edit, neither `shim` nor `transcript-analysis.py` appears in the file.
- `docs/transcript-analysis-architecture.md`: add the section below as the last one under the doc's "The package" heading, after the `instrument_authoring.py` section's paragraph. It is needed in this commit because the architecture-doc test requires a heading for every module (row 18).
  - Heading line: three `#` characters, a space, then `cli.py` wrapped in backticks, the same shape as every other module heading there.
  - Body: "`build_parser()`, which composes every subcommand's parser, and `main()`, which parses the command line, handles the top-level `--config-dir`, and calls the chosen subcommand's handler. `main()` refuses the top-level `--config-dir` for every subcommand in `scope._SUBCOMMANDS_REFUSING_TOP_LEVEL_CONFIG_DIR`, and otherwise reassigns `scope.PROJECTS_DIR`. `build_parser()` registers 23 subcommands inline. Every other subcommand registers through its owning module's `_add_<command>_subparser`, the form a new subcommand uses. `transcript-analysis.py` re-exports both functions."

**PR 2 commit 2, `cli`'s own test file and prose.**
- `test_transcript_cli.py` (M11):
  - Delete the loader block, from the `_SCRIPT` assignment through `_spec.loader.exec_module(_mod)`, together with its comment.
  - Add `cli` to the package import line, and replace every `_mod.` with `cli.` (21 sites).
  - Remove the imports this leaves unused with `ruff check --select F401 --fix`. Nothing else in the file changes.
- `docs/transcript-analysis-architecture.md` (row 33):
  - Replace :6-24, from "`transcript-analysis.py` stays a plain top-level script" through "`cmd_read_scope` from the shim.", with: "`transcript-analysis.py` stays a plain top-level script at its own path — every existing caller (the hook nudge, the `/transcript-analysis` skill, a contributor's own shell history) keeps working unchanged. Its body imports `build_parser()` and `main()` from `transcript_analysis/cli.py`, re-exports both, and calls `main()`. Everything else lives in the `transcript_analysis/` package (`claude/.claude/scripts/transcript_analysis/`)." Follow that with a paragraph break and: "`cli.py` imports the command-group modules, and no package module imports `cli.py`, so no circular import is possible. `build_parser()` wires up `review_trace.py`'s `cmd_review_trace`/`REVIEW_TRACE_SKILLS` and `read_scope.py`'s `cmd_read_scope` from `cli.py`."
  - Every other sentence that places `build_parser()`, `main()`, or a bare import in the shim now places it in `cli.py`:
    - "from the shim" becomes "from `cli.py`".
    - "the shim's `build_parser()`" becomes "`cli.py`'s `build_parser()`".
    - "The shim reads" becomes "`cli.py` reads".
    - "the shim back-import exception" becomes "the `cli.py` back-import exception".
    - In :58-59, "not the shim" becomes "not `cli.py`".
    - In :71, "every other module (and the shim) builds on this one" becomes "every other module builds on this one".
  - Leave alone:
    - :267, which says a name is "no longer reached bare from the shim", still true;
    - :354's provenance of `_compute_deny_summary_data`;
    - :684, which runs the script itself;
    - the § "Tests" sentences about test files that load a shim copy.
  - In § "Tests", PR 1 shifts every line, so this edit anchors on text. Replace the whole sentence that starts "A test file that reads a name only the shim has" and ends "the same boilerplate `tests/test_transcript_cli.py` uses." (PR 1's wording; :808-811 at 0a66c019, starting mid-line on :808) with: "A test file that reads `build_parser` or `main` imports `cli` like any other package module. Older test files that load their own copy of `transcript-analysis.py` via `spec_from_file_location` keep working, because the script re-exports both names."
- `transcript_analysis/__init__.py` (row 20). Its docstring becomes:
  - `"""transcript-analysis.py's decomposed modules; docs/transcript-analysis-architecture.md says what each one holds.`
  - (blank line)
  - `session_pricing is the one module with a stability promise, for importers outside this repository.`
  - `scope.py's docstring states the import discipline its reassignable PROJECTS_DIR requires.`
  - `transcript-analysis.py itself is a plain top-level script outside this package; it re-exports cli's`
  - `build_parser and main.`
  - `"""`
- `test_transcript_cli_bootstrap.py` :9-11 (row 21). Replace "and the one a later phase's import changes could break silently while every in-process test still passes." with "so an import change in the script or the package can break it while every in-process test still passes." After PR 2 no later decomposition phase exists, and "phase" is plan vocabulary the durable-text rule bars.

**Assumption ledger.**

**Root:** L holds 1,436 lines of tests whose subjects are spread across a dozen modules, because no stated rule says where a new test goes. Tests therefore accrete in the file that already carries the shim loader, and the shim still holds `build_parser()` and `main()`. Relocating every L test by subject, stating the rule where a contributor editing a transcript test loads it, enforcing its module-to-file half, and moving the CLI into the package removes both the accreted file and the reason it accreted. `[verified: L line count by Grep; docs/transcript-analysis-architecture.md:775-1002 states no placement rule]` `[engineer-verified: "do we need to modify the architecture markdown for transcript analysis to indicate exactly when to add to test_transcript_analysis versus create a new test suite file ? I don't want us to have to do decomp again"]`

**Givens:**
- G1. Agents run `select-tests.py`, not the full suite, outside CLAUDE.md's two named cases. Reason: changing this needs an edit to CLAUDE.md § "Commands". `[verified: CLAUDE.md "Commands"]`
- G2. Code and test files stay under the 1,000-line limit, and nothing enforces it until F2 lands. Reason: the limit and its timing are a repo decision this plan does not own. `[verified: docs/design-decisions/code-file-line-limit.md:19, :50-57]`
- G3. This machine runs pytest with one xdist worker, through `PYTEST_XDIST_AUTO_NUM_WORKERS=1`. Reason: machine memory lies outside the plan's reach. `[unverified — relayed by the dispatching session]`
- G4. The shim keeps its path and executable bit. Reason: `pr-cost-section.sh` :38 and :46, `skill-fidelity-report.sh` :16 and :23, `nudge-error-mode-analysis.sh` :151, and every stow consumer's shell history run it by path. `[verified: Grep]`
- G5. This phase moves `build_parser()` and `main()` into `cli.py`, reduces the shim to a package import and a `__main__` guard, and relocates `_UNCONDITIONAL_HEADER_CASES` with its two classes in one move. Reason: the governing plan fixes this phase's content. `[verified: .claude/plans/transcript-analysis-decomposition.md:155-161, :185-188]`
- G6. The governing plan requires a revert rehearsal at each phase boundary, and the engineer waived it for PR 1 and PR 2, so neither PR runs one. Reason: only the engineer can waive the governing plan's verification contract. `[verified: transcript-analysis-decomposition.md:276-279]` `[engineer-verified: "Waive for both (Recommended)"]` Row 29 records the answer.
  - Separately, neither PR promotes or changes a fixture, the case the rehearsal guards against (:278-279). PR 1 edits one `conftest.py` docstring line, and PR 2 edits no `conftest.py`. `[verified: Critical files, Files PR 1 commit 3 and Files PR 2; row 13's Grep of conftest.py:1600]`

**Rows:**
1. `[engineer-verified: "Two PRs (Recommended)"]` The contents and order of the two PRs were the dispatching session's proposal under that label. This plan adopts them on M1, not on the engineer's authority. `[unverified]`
2. `[engineer-verified: "Move verbatim, record the overage (Recommended)"]` This covers moving `cli.py` byte for byte and recording its size. Splitting its 23 inline subparser blocks into `_add_<command>_subparser` helpers is the dispatching session's reading of the follow-up. `[unverified]`
3. `[engineer-verified: "Shim re-exports both names (Recommended)"]` This covers the shim keeping `build_parser` and `main` importable. That the other twelve shim-loading test files stay on the shim, and switch later, is the dispatching session's reading. `[unverified]` `test_transcript_cli.py`'s own switch in PR 2 is this plan's choice (M11).
4. `[engineer-verified: "Yes, add it (Recommended)"]` This covers adding a structural module-to-test-file check. Its file, map, and matching rule are this plan's choices (M9).
5. `[engineer-verified: "I accept the gap between F1 and F2. I aim to go from F1 to F2 quickly"]` This covers adding no line-count test here. Size is checked by `wc -l`, as `leaf-module-tests.md` did (Verification step 6).
6. L at 0a66c019. `[verified: Grep of top-level lines and of `_mod.` reads]`
   - Besides its loader, it has 18 top-level nodes: 15 classes, `test_review_cost_by_size_without_this_repo_is_refused_through_main`, `_fake_gh_pr_list_run`, and `_UNCONDITIONAL_HEADER_CASES`. Commit 1 adds two classes, so L-c1 has the 20 the move map lists. Its loader block is :86-93, with comment :87-89.
   - Its other comments outside node spans are six `# ---` banners (:281, :361, :424, :513, :658, :1192) and the block :672-676 directly above `_UNCONDITIONAL_HEADER_CASES`.
   - It reads `_mod.main` 7 times: `TestConfigDirFlag` at :126, :146, :174, :212, and :233; `TestConfigDirRouting` at :255; and the through-main test at :273.
   - It reads `_mod.build_parser` 14 times: `TestCacheEfficiencyArgparseWiring` at :373 and :380, `TestBuildParser` 10 times between :434 and :508, and `TestProjectsFlagHasNoArgparseType` at :1038 and :1047.
7. Each moved class's subject. `[verified: read and Grep]`
   - `TestConfigDirRouting`'s first test only asserts that `scope._SUBCOMMANDS_REFUSING_TOP_LEVEL_CONFIG_DIR` holds `context-composition` (:240-241), so its subject is scope's data. It moves with its class (M2), whose second test drives that refusal through `main()` (:243-261).
   - `TestTableColsHelper` tests `conftest.py`'s `_table_cols`, which L imports at :79.
   - `TestCacheRebuildCrossInstrumentReconciliation`'s docstring names cache-rebuild's sidechain W5m accumulator as the code under test, and cache-efficiency's `Write5m` column as the oracle (:388-393).
   - `TestFormatDriftCanary` holds 9 tests:
     - four drive `cmd_subagents` (:519, :535, :543, :565), one `cmd_skill_pair` (:554), and four `cmd_cache_efficiency` (:596, :607, :616, :637);
     - each command calls `pricing._warn_if_subagent_format_drift` itself (`subagents.py:171`, `skill_pair.py:93`, `cache_efficiency.py:256`);
     - that function has direct unit tests in `test_transcript_pricing_session_helpers.py` (:310-327);
     - two cache-efficiency docstrings name `_scan_cache_efficiency_group`'s accumulation as their target (:617-624, :638-644).
     So no test in the class has pricing as its subject.
   - `TestCostFormatDriftCanary` lives in cost's own test file (`test_transcript_cost.py:4398`), the per-command precedent.
   - The multi-root group calls `cmd_*` functions directly and reads neither `build_parser` nor `main`. Its two `TestSkillFiles*` classes read `SKILL.md` through `SKILLS_DIR` inside test bodies (:1152-1185).
   - `test_transcript_analysis_branch_attribution.py`'s docstring covers the pre-branch and sidechain attribution steps that L's two branch classes test through each consumer (:1-4, L :1197-1200).
8. `TestBuildParser`'s ten tests. `[verified: read, Grep]`
   - Tests :433, :454, :460, and :481 pin the `--this-repo`/`--projects` pair across subcommands. `scope._add_project_scope_args` registers that group (`scope.py:509-516`), and `skill_invocation.py:265` hand-rolls its own.
   - Test :496 pins review-trace, which `build_parser()` registers inline (shim :328).
   - Five tests pin one command's own registration: :438 and :443 skill-invocation's, :487 review-cost-by-size's, and :502 and :507 pr-link's.
9. Thirteen test files load a shim copy: L, plus `test_transcript_` plan_boundary, pr_cost_gh, ledger_common, cost, handoff_signal_response, cache_rebuild, pr_cost_export, cache_rebuild_ttl_rules, review_rounds, cost_counts, and period_cost, plus `test_author_outcome.py`. `test_transcript_cache_efficiency.py`, `test_transcript_skill_invocation.py`, `test_transcript_pr_link.py`, and `test_transcript_review_cost_by_size.py` load none. `[verified: Grep of `spec_from_file_location` and `_SCRIPT =`]`
10. File sizes. `[verified: ripgrep line counts at 0a66c019]`
    - Current: L 1,436, cache_efficiency 316, cache_rebuild 555, skill_pair 212, shared_fixtures 24, branch_attribution 636, subagents 939, pricing 653, scope 900, scope_scan 773, review_cost_by_size 1,057.
    - Projected after PR 1: cli about 360, multi_root_scope about 550, subagents_format_drift about 95, skill_pair about 230, cache_efficiency about 385, cache_rebuild about 600, shared_fixtures about 100, branch_attribution about 890. `[unverified: span arithmetic; Verification step 6 measures]`
    - The four `cmd_subagents` canary tests span about 64 lines, which would take `test_transcript_subagents.py` past 1,000.
11. `select-tests.py`. `[verified: read]`
    - `TRANSCRIPT_ANALYSIS_TEST_GLOB` (:119) is selected on any hook or skill change (:819). Its comment (:112-115) names L as the `SKILL.md` reader.
    - No other `test_transcript_analysis_*.py` file names `SKILL.md` or `SKILLS_DIR`, so after PR 1 the multi-root file is the glob's only `SKILL.md` reader.
    - `select-tests.py` is in `GLOBAL_TRIGGER_PATHS` (:467-471), so editing its comment widens PR 1's selected run to the full suite (:940-942). That is CLAUDE.md's first named case.
12. `test_transcript_package_module_prefixes.py`. `[verified: read]`
    - `TEST_FILES` (:58-125) has 66 entries, L among them (:110).
    - A test file that imports the package and makes no `spec_from_file_location` call must be listed (:506-517).
    - `test_shim_copy_reads_only_build_parser_and_main` (:604-635) finds shim loaders by spec name and requires each to read `build_parser` or `main` at least once.
    - `PRODUCTION_MODULES` (:27-57) lists a subset of modules, so `cli.py` need not join it.
    - `test_production_modules_reference_only_real_sibling_attributes` (:380-397) fails a listed module that assigns through a sibling module's attribute. `main()`'s `scope.PROJECTS_DIR = ...` (shim :1020) is such an assignment.
13. Prose naming L outside the architecture doc. `[verified: Grep]`
    - Sites this change makes false: eight helper-module docstrings (`_subagent_helpers.py:2`, `_audit_routing_helpers.py:2`, `_handoff_nudge_helpers.py:1`, `_skill_helpers.py:2`, `_commit_gate_helpers.py:1`, `_cache_efficiency_helpers.py:1`, `_session_signal_helpers.py:2`, `_cache_rebuild_helpers.py:2`), `conftest.py:1600`, `test_transcript_cost.py:4392-4394`, and `select-tests.py:112`.
    - Already stale before this change: `test_check_claude_md_length.py:889` and `test_transcript_context_composition.py:616`.
    - Dated records under `docs/reports/` and `docs/case-studies/` name L, and are preserved.
    - `test_select_tests.py:1839` asserts a path prefix the glob still satisfies.
14. Which files read each helper after PR 1, from L's uses at :413-417, :686-722, :1107-1131, and :1204-1431. `[verified: Grep]`
    - `_subagents_args`: subagents_format_drift and multi_root_scope.
    - `_skill_helpers`: skill_pair, multi_root_scope, and branch_attribution.
    - `_session_signal_helpers`: branch_attribution.
    - `_gate_args`, `_audit_routing_*_args`, `_rearm_backtest_args`, and `_spend_over_threshold_args`: multi_root_scope.
    - `_cache_rebuild_args`: multi_root_scope and cache_rebuild.
    - `_cache_efficiency_args`: cache_efficiency and cache_rebuild.
15. The architecture doc's § "Tests" (:775-1002) names L or "the legacy file" at :782-785, :811, :857-862, :871-873, :908-911, :913-914, :946, :958-959, :971-974, :981-982, :992-996, and :1000-1001. `[verified: read]` :990's "None of the three loads the shim" stays true, because no file gains a loader (M3).
16. The shim. `[verified: Grep, read]`
    - Imports are :7-193, `build_parser` :196-981, `main` :984-1021, and the guard :1024-1025. It has no other top-level definition and no lambda.
    - Every callable `build_parser` registers comes from a package module or is a builtin (`type=_iso_date` from scope, `int`, `float`). So the parser dump needs no `--moved-callable`.
    - `build_parser` makes 23 inline `sub.add_parser` calls and 21 `_add_<command>_subparser` calls.
17. The 21 `_add_<command>_subparser` docstrings say "Register the X subparser on build_parser()'s subparsers action" without placing `build_parser`, so PR 2 leaves them alone. `[verified: Grep; buckets.py:77-80]`
18. What PR 2 breaks or picks up. `[verified: read]`
    - `test_shim_still_imports_from_cost_module` (`test_transcript_analysis_cost_import_direction.py:77-87`) fails once the shim imports nothing from `transcript_analysis.cost`.
    - `test_transcript_analysis_branch_attribution_structure.py` scans the shim and the package (:20-22, :143), so it covers `cli.py`.
    - `test_transcript_analysis_import_cycles.py` globs the package (:25), so `cli.py` gets a fresh-interpreter import test.
    - `test_transcript_analysis_architecture_doc.py` requires a level-3 heading holding only `cli.py` in backticks (:14-49).
19. Stow and callers. `[verified: read]`
    - `install.sh` links `claude/.claude/` entry by entry, so a new file nested under `scripts/` is live after `git pull` with no reinstall (README.md:91-97). That holds only when `<config-dir>/scripts` is a folded directory symlink. `CHANGELOG.md:286`, `:535`, and `:558` record the real-directory variant, where `git pull` links no new file; `:535` names `scripts/transcript_analysis/` itself.
    - `_move_fidelity._load_parser` calls `build_parser()` on an exec'd copy of the script (:528-548), and reads no other name (:539).
    - Earlier decomposition phases that added a module the shim imports touched no CHANGELOG entry. `[unverified this session: relayed from the backend plan review, commits 17b35367, 5e423978, and 97e6d6a1]`
    - The hook test symlinks only the shim into its sandbox (`test_nudge_error_mode_analysis.py:80-87`), and the package resolves through the symlink-resolved `sys.path[0]`, as the shim's existing package imports already do. `[unverified: relies on the governing plan's assumption 6; PR 2's selected run includes the test]`
20. `__init__.py` enumerates 50 modules, and it points at a shim "top-of-file comment" the shim lacks (:15-17). `scope.py`'s docstring states the `PROJECTS_DIR` import discipline (:5-10). `[verified: read]`
21. `test_transcript_cli_bootstrap.py:9-11` says "a later phase's import changes could break [it] silently". `[verified: read]`
22. Ruff selects E, F, B, I, UP, and SIM (`pyproject.toml:6`), so the shim's unused `build_parser` import fails F401 unless exported. `[verified: read]` `__all__` counts as a use for F401 in this non-package script. `[verified: ruff 0.6.9, the pinned ruff==0.6.*, passes with __all__ and reports F401 without it]`
23. The map entries. `[verified: read, Grep, Glob]`
    - `test_author_outcome.py` holds author_outcome's tests (architecture doc :820-824).
    - `sessions.py` has no test file, and the header table covers its header (:983; L :722).
    - `test_transcript_cache_rebuild_ttl_rules.py` imports `cache_rebuild_rules` (:8).
    - Every other module has a `test_transcript_<module>[_<seam>].py` by longest-prefix match. `[verified: Glob listings compared by hand; the test's own first run re-checks]`
24. A one-line package-module glob already exists at three sites: `test_transcript_analysis_architecture_doc.py:29-32`, `test_transcript_package_module_prefixes.py:133-134`, and `test_transcript_analysis_import_cycles.py:25`. `[verified: read]`
25. Four of the five structural tests over package source use the `test_transcript_analysis_` prefix: architecture_doc, cost_import_direction, import_cycles, and branch_attribution_structure. The exception is `test_transcript_package_module_prefixes.py`. `[verified: Glob]`
26. Rule-file constraints. `[verified: read]`
    - `test_rules_frontmatter.py` requires a non-empty `paths` list whose project-rule literal prefixes resolve (:138-242), and bars two rule files with an identical `paths` set (:285-320).
    - `test_skills.py`'s citation checks cover `.claude/rules/*.md` (:8290-8315).
    - The precedent is `test-tree-packaging.md`, 28 lines with a list of double-quoted globs.
    - `ai-instruction-and-memory-files` reviews rule files through `/code-review`, without hook enforcement (`.claude/rules/review-pipeline-dispatch.md`).
27. `_move_fidelity.py`. `[verified: read]`
    - `nodes` takes a repeated `--after` and reports `node-duplicate` for a name defined in two after files (:300-307).
    - `remainder` drops imports with their line comments (docstring :32-33) and the declared nodes. `--removed __doc__` names the module docstring (:898).
    - `capture` runs the script by subprocess for each argv (:655-657), and `--all-help` adds a parser dump (:17-29).
    - `test-ids` keys a test as the dotted parts after the last `test_`-prefixed component, joined with `::`, plus its name (:745-750).
28. `test_transcript_cache_rebuild.py` defines `_SCRIPT`, `_spec`, and `_mod` too (:23-30). So `nodes` cannot carry L's loader names without a `node-duplicate` finding, and the loader is proved by census instead (M13). `[verified: read]`
29. Revert rehearsal. `[engineer-verified: "Waive for both (Recommended)"]` The engineer waived the governing plan's rehearsal for PR 1 and PR 2, so Verification carries no rehearsal step. The label covers only that waiver. The engineer's earlier waivers covered other PRs: `leaf-module-tests.md` row 24 and `shim-reader-retarget-pr2.md` row 12. `[verified: leaf-module-tests.md:156]`
30. The plan file is untracked on this branch and ships with PR 1. PR 2 does not edit it. `[verified: git status at dispatch]` `require-plan-review.sh` arms only on an uncommitted or modified plan. `[unverified this session: relayed from leaf-module-tests.md row 20]`
31. `.claude/plans/code-file-size-splits.md:175`: "Once F2 lands, a phase that would create a file over the limit splits it along a named seam, or records an exception with a reason." `[verified: read]`
32. `cli.py`'s size: shim lines 7-1021 are 1,015 lines, plus a docstring line and a blank line, makes 1,017, which is 17 over the limit. `[verified: arithmetic on the read line numbers]` `[unverified: Verification step 3 of PR 2 measures]`
33. The architecture doc's PR 2 sites. `[verified: Grep of "shim"]`
    - The intro is :6-59.
    - Sentences that place a name in the shim: :71, :254, :288, :303, :368, :379, and the "reached bare from the shim" sentences between :448 and :630.
    - Allowed residues: :267, :354, :684, and § "Tests"'s shim-copy loader sentences.
34. The pure-check precedent. `claude-skills/skills/tests/test_rules_frontmatter.py`'s `rule_frontmatter_violations` (:138-242) returns violation strings for its inputs. The real-tree test calls it at :260, and `TestRuleFrontmatterViolations` (:398-695) drives each red branch with a synthetic input. `[verified: read, Grep]`
35. After commit 1 inserts two class headers above the multi-root group, the comment block above `_UNCONDITIONAL_HEADER_CASES` sits below L's :672-676, near :676-680. L's loader at :86-93 precedes the insertions and keeps its lines. `[unverified: relayed from the SDET plan review's simulated L-c1; Verification names the block by content, so no step depends on the number]`

**Mechanisms:**
- M1: Two sequenced PRs. PR 1, the tests and the rule, merges before PR 2, the move, is cut. `anchors: row1, G5`. Moving the tests first lets PR 2's move be proved against tests already relocated, and the plan ships with the PR that carries the rule. Cutting both PRs from main was set aside: both edit the architecture doc's § "Tests" and `test_transcript_cli.py`.
- M2: Placement by subject, one class at a time. Only `TestFormatDriftCanary` is split, three ways by command. `anchors: row7, row8, row10`. The alternatives each fail:
  - Moving the canary whole to `test_transcript_pricing.py` puts nine command tests in a file none of them has as its subject, while pricing's function keeps its own direct tests (row 7).
  - Keeping the canary whole as an invariant file contradicts the per-command precedent `TestCostFormatDriftCanary` sets.
- M3: Every L test that reads `build_parser` or `main` goes to `test_transcript_cli.py`, and no file gains a shim loader. `anchors: row3, row6, row8, row9`. This leaves seven single-command tests in the cli file: `TestCacheEfficiencyArgparseWiring`'s two and `TestBuildParser`'s five from row 8. § "Tests" names them as exceptions to the rule's CLI-wiring bullet, each with its command's file, so the rule and the catalog agree from PR 1 on. Out of scope names them for the follow-up that retires the shim loaders. The rule file does not list them: it states where a new test goes, the catalog records where existing tests live, and the list needs one home. The alternatives each fail:
  - Moving them now adds a loader to three files that load none (row 9), loaders the follow-up then deletes. It also grows `test_transcript_review_cost_by_size.py` past its current 1,057 lines.
  - Two cli files split by `main` and `build_parser` divide one subject by mechanism, at about 360 lines together (row 10).
- M4: The four `cmd_subagents` canary tests go to a new seam file, because `test_transcript_subagents.py` would cross 1,000 lines (row 10). `anchors: row7, row10, G2`.
- M5: Cross-module groups go to `test_transcript_analysis_<invariant>.py` files: the multi-root group to a new file, and the branch classes to the existing `test_transcript_analysis_branch_attribution.py`. `anchors: row7, row10, row11`. The alternatives each fail:
  - `test_transcript_scope.py` (900 lines) and `test_transcript_scope_scan.py` (773) cannot take about 490 lines, and their subject is scope's own code.
  - A name outside the glob drops the two `SKILL.md` pins from skill-edit selection, and `TestCrossDomainReadCompleteness` cannot see a read made inside a function body.
- M6: PR 1 has three implementation commits: the in-place split, a pure move, and authored text. `anchors: row27, row28`. Putting prose in the move commit, as `leaf-module-tests.md` did, was set aside: docstring edits in existing destination files would add `remainder-ast` findings to enumerate, and commit 3's text then needs review alone.
- M7: A scratch mover cuts and pastes AST spans. It carries the comment block directly above `_UNCONDITIONAL_HEADER_CASES`, copies L's loader block into the cli file, and refuses to write if L holds anything it cannot account for. `anchors: row6, row27, row28`. The lighter options each fail:
  - Re-emitting about 1,330 lines through Write risks a retyping slip per line.
  - Hand-cut `sed` ranges miss decorator lines and the attached comment block.
- M8: The rule is a path-scoped `.claude/rules/` file, and § "Tests" opens by citing it. `anchors: root, row26`. The alternatives each fail:
  - The rule in the architecture doc does not load when a contributor opens a transcript test, which is when the choice is made.
  - The rule in `CLAUDE.md` costs every session context for one subsystem.
  - A skill has no trigger on editing a test.
- M9: The structural test sits in its own property-named file, matches each test file to its longest module name, and keeps an exceptions map whose entries must hold in both directions. Its checks are pure functions, so each red branch gets a committed synthetic case beside the real-tree run. `anchors: row4, row23, row24, row25, row34`. The alternatives each fail:
  - Real-tree assertions alone are green on day one, so a refactor that stops checking one branch stays green. The `zz_probe.py` control exercises one branch once, by hand.
  - Beside the architecture-doc test, it would sit under a name and docstring that pin the doc's module list. That breaks the rule's structural-test clause.
  - A plain prefix match would let a future module `pr` pass on `test_transcript_pr_cost.py`.
  - A map kept in the rule or the doc is unparsed prose.
  - Each map value only has to exist, with no import check. An entry is a reviewed diff line, and the threat is drift, not evasion.
- M10: `cli.py` is a docstring plus the shim's body verbatim. The shim is its docstring, one import, `__all__`, and the guard. `anchors: row2, row3, row16, row22, G4, G5`. The alternatives each fail:
  - `# noqa: F401` needs a rationale comment anyway, and silences the whole line.
  - A redundant `as` alias depends on ruff behavior this session did not check.
- M11: PR 2 points `test_transcript_cli.py` at `cli` and leaves the other twelve loaders. `anchors: row3, row12`. Leaving it on the shim was set aside: `cli.py`'s own test file would then reach `cli` only through the re-export.
- M12: PR 2 retargets the cost import-direction guard to `cli.py` rather than deleting it. Its pin, no public cost name crossing into the CLI, still applies. `anchors: row18`.
- M13: Proofs. Commit 1 gets a sorted-line diff, `remainder`, and `test-ids` renames. Commit 2 gets `nodes --verbatim` over all destinations, a `remainder` of L against a residue stub, a census of each new and grown file, and `test-ids`. PR 2 gets a byte diff of the body, `nodes`, `remainder`, and a before-and-after `capture --all-help --runs` comparison. `anchors: G1, G3, row16, row27, row28, row35`.
- M14: F2 conditional. If F2's check exists on a PR's base, PR 1 deletes L's exception row. PR 2 deletes the shim's row and adds a row for `cli.py`, with its measured size as the ceiling and the reason "verbatim move of build_parser; splitting its inline subparsers is pending". The F2 probe runs at step 0 and again at each PR's second sync point, because a rebase absorbs a newly landed F2 silently and the PR's CI would then fail on `cli.py`. `anchors: row31, row32, G2`.
- M15: No CHANGELOG entry for `cli.py`. `anchors: row19`. The earlier decomposition phases that added a module the shim imports shipped without one, and `cli.py` is the same kind of change: one more file in `transcript_analysis/`, the directory `CHANGELOG.md:535` already tells a real-directory consumer to re-stow. A CHANGELOG line in `:535`'s re-run-`./install.sh` form was set aside, because the exposure equals every earlier phase's. If a consumer's `transcript_analysis/` is a real directory of per-file links, `cli.py` stays unlinked after `git pull` and every shim run fails with `ModuleNotFoundError`, which silences the error-mode nudge (`hooks/nudge-error-mode-analysis.sh:151-154`) and drops the PR body's Cost section (`scripts/pr-cost-section.sh:38-42`).

## Critical files

**Dispatch rules.**
- Every dispatch goes to `code-writer` with `model: sonnet`.
- A dispatch does no staging and no commits.
- The parent runs the named Verification steps and commits.
- Dispatches run in order, because each proof reads the tree the previous commit left.
- A hook, gate, or permission denial stops the dispatch or step that hit it. Report the denial text to the parent, which reports it to the engineer, and try no other command form.

**Dispatches.**
- **A1, PR 1 commit 1.** Files: L only. Make exactly Approach "PR 1 commit 1". Verification it runs: `scripts/venv-run.sh ruff check claude/.claude/ claude-skills/ plugins/ evals/`, then `PYTEST_XDIST_AUTO_NUM_WORKERS=1 scripts/venv-run.sh pytest -n0 claude/.claude/scripts/tests/test_transcript_analysis.py -k "FormatDriftCanary"`.
- **A2, PR 1 commit 2.** Files:
  - Create the three new test files.
  - Modify the five existing destinations and `test_transcript_package_module_prefixes.py`.
  - Delete L from disk.

  **The mover**, `<scratch>/move_nodes.py`:
  - It takes `<scratch>/L-c1.py` and the Approach's move map, in L order.
  - A node's span runs from its first decorator line through `end_lineno`, and also takes a comment block whose last line sits directly above that first line.
  - It refuses and writes nothing unless every top-level statement of L-c1 is one of: the module docstring, an import, one of the loader block's five statements, or a mapped node. Every comment line outside those spans must belong to one of the six banners in row 6.
  - It appends each destination's nodes after that file's existing content, two blank lines apart. In `test_transcript_cli.py`, L-c1's loader lines (:86-93) come before the nodes. Each file ends with one newline.

  **Order of work:**
  1. Write the two new docstrings, so the new files hold only them, plus an empty `test_transcript_cli.py`.
  2. Run the mover.
  3. Add imports from `scripts/venv-run.sh ruff check --select F821 <file>`.
  4. Place them with `--select I001 --fix`.
  5. Apply no other fix, and never edit a moved node's text.

  Stop and report on a mover refusal, on any created or grown file at 1,000 lines or more, or on a failure in its own verification.

  Verification it runs: ruff as in A1. Then `PYTEST_XDIST_AUTO_NUM_WORKERS=1 scripts/venv-run.sh pytest -n0` on the eight destination files and `test_transcript_package_module_prefixes.py`, plus one `-n0` run on each new file alone.

  Return: each file's node count, each existing file's added imports as `remainder` triples, and `wc -l` of every created and grown file.
- **A3, PR 1 commit 3.** Files:
  - Create the rule file and `test_transcript_analysis_module_test_files.py`.
  - Modify the architecture doc, the eight helper modules, `conftest.py`, `test_transcript_cost.py`, `test_transcript_shared_fixtures.py`, and `select-tests.py`.

  Make exactly Approach "PR 1 commit 3". Verification it runs:
  - ruff as in A1.
  - `PYTEST_XDIST_AUTO_NUM_WORKERS=1 scripts/venv-run.sh pytest -n0` on `test_transcript_analysis_module_test_files.py`, `test_transcript_analysis_architecture_doc.py`, and `claude-skills/skills/tests/test_rules_frontmatter.py`.
  - The same on `claude-skills/skills/tests/test_skills.py -k citation`.

  Return each prose edit as `file:line old → new`.
- **B1, PR 2 commit 1.** Files: create `transcript_analysis/cli.py`. Modify `transcript-analysis.py`, `test_transcript_analysis_cost_import_direction.py`, and the architecture doc (the `cli.py` section only).
  - Build `cli.py` by writing its two header lines with Write. Then run `sed -n '<first>,<last>p' claude/.claude/scripts/transcript-analysis.py >> claude/.claude/scripts/transcript_analysis/cli.py`, with the range Verification step 1 recorded. Only then rewrite the shim.
  - Verification it runs: ruff as in A1. Then `PYTEST_XDIST_AUTO_NUM_WORKERS=1 scripts/venv-run.sh pytest -n0` on `test_transcript_analysis_cost_import_direction.py`, `test_transcript_cli.py`, `test_transcript_cli_bootstrap.py`, `test_transcript_package_module_prefixes.py`, `test_transcript_analysis_branch_attribution_structure.py`, `test_transcript_analysis_import_cycles.py`, `test_transcript_analysis_architecture_doc.py`, and `test_transcript_analysis_module_test_files.py`.
- **B2, PR 2 commit 2.** Files: modify `test_transcript_cli.py`, the architecture doc, `transcript_analysis/__init__.py`, and `test_transcript_cli_bootstrap.py`. Make exactly Approach "PR 2 commit 2". Verification it runs: ruff, then pytest `-n0` on `test_transcript_cli.py`, `test_transcript_package_module_prefixes.py`, and `test_transcript_analysis_architecture_doc.py`. Return every line of the architecture doc that still matches `shim`.

**Files, PR 1** (this branch, `GH-1116/cli-phase-and-test-placement-rule`). Paths are under `claude/.claude/scripts/` unless shown in full.
- Commit 0, the plan: `.claude/plans/cli-phase-and-test-placement-rule.md`.
- Commit 1: L, `tests/test_transcript_analysis.py`.
- Commit 2:
  - Create `tests/test_transcript_cli.py`, `tests/test_transcript_analysis_multi_root_scope.py`, and `tests/test_transcript_subagents_format_drift.py`.
  - Delete L.
  - Modify `tests/test_transcript_skill_pair.py`, `tests/test_transcript_cache_efficiency.py`, `tests/test_transcript_cache_rebuild.py`, `tests/test_transcript_shared_fixtures.py`, `tests/test_transcript_analysis_branch_attribution.py`, and `tests/test_transcript_package_module_prefixes.py`.
- Commit 3:
  - Create `.claude/rules/transcript-analysis-tests.md` and `tests/test_transcript_analysis_module_test_files.py`.
  - Modify:
    - `docs/transcript-analysis-architecture.md`;
    - `tests/_subagent_helpers.py`, `tests/_audit_routing_helpers.py`, `tests/_handoff_nudge_helpers.py`, `tests/_skill_helpers.py`, `tests/_commit_gate_helpers.py`, `tests/_cache_efficiency_helpers.py`, `tests/_session_signal_helpers.py`, and `tests/_cache_rebuild_helpers.py`;
    - `tests/conftest.py`, the :1600 docstring line only;
    - `tests/test_transcript_cost.py`, the :4392-4394 banner only;
    - `tests/test_transcript_shared_fixtures.py`, its docstring;
    - `select-tests.py`, the :112-115 comment only.
- Conditional (M14): the file holding F2's exception rows, which the F2 probe names at Verification step 0 or at the second sync point. If the probe finds the check but cannot name that file, stop and ask the engineer.

**Files, PR 2** (its own branch and worktree per `branch-management`, cut from `origin/main` after PR 1 merges).
- Commit 1: create `transcript_analysis/cli.py`. Modify `transcript-analysis.py`, `tests/test_transcript_analysis_cost_import_direction.py`, and `docs/transcript-analysis-architecture.md`.
- Commit 2: modify `tests/test_transcript_cli.py`, `docs/transcript-analysis-architecture.md`, `transcript_analysis/__init__.py`, and `tests/test_transcript_cli_bootstrap.py`.
- Conditional (M14): F2's exception file, as for PR 1.

**Explicitly unchanged:**
- the other twelve shim-loading test files (row 9);
- the 21 `_add_<command>_subparser` docstrings (row 17);
- `tests/_move_fidelity.py` and its helpers;
- every skill, hook, and agent file, and every rule other than the new one;
- `docs/transcript-analysis.md`, `README.md`, and `CHANGELOG.md`;
- `select-tests.py` in PR 2.

**Reuse:**
- `_move_fidelity.py`'s `nodes`, `remainder`, `capture`, `compare-captures`, and `test-ids`, unchanged;
- `conftest.py`'s fixtures and the `_<family>_helpers.py` modules, imported unchanged;
- ruff's F821, I001, and F401 for imports.

**PR bodies.**
- Both bodies carry:
  1. `Refs GH-1116`;
  2. each file's moved-node count, or for PR 2 the `cli.py` move;
  3. the proofs' results;
  4. the selected-suite result.
- PR 1's body also:
  - says `select-tests.py` widened to the full suite (row 11);
  - lists the seven tests M3 leaves in `test_transcript_cli.py`;
  - says every local run was single-worker (G3), so CI's run is the moved tests' first multi-worker run.
- PR 2's body also:
  - states `cli.py`'s measured `wc -l` and its overage against the 1,000-line limit;
  - names the follow-up that splits its inline subparsers;
  - says whether F2 has landed;
  - states in one line: revert PR 2 first, then PR 1.
- Neither body claims a CLI or CHANGELOG change (M15).

**Sync and abort.**
- Take in `origin/main` only at Verification step 0, before any snapshot exists, and again before `/ready-for-review`.
- At the second point:
  1. Run `git fetch origin main` as its own call.
  2. Run `git diff --no-renames --name-only HEAD...origin/main`. If any listed path is one of the PR's Critical files, stop and report without syncing. Otherwise run `git rebase origin/main`.
  3. Repeat the F2 probe from PR 1 step 0 (M14). If the line-count check has appeared, make this PR's M14 row edit as its own commit, after `/code-review`, with `Refs GH-1116`.
- Resume. After a compaction or in a new session, run `ls -- ~/.local/state/cli-phase/`. The one entry that starts with `<pr>-` is `<scratch>`, and more than one stops the run. Rerun the interrupted step against it.
- If a dispatch or a Verification step is interrupted:
  1. `git status --short` must list only Critical files and, after PR 1 step 9's control began, `claude/.claude/scripts/transcript_analysis/zz_probe.py`.
  2. Restore each modified file with its own `git restore --source=HEAD -- <path>`.
  3. Remove each untracked Critical file, and `zz_probe.py` when listed, with `rm -- <path>`.
  4. Rerun the step.
- Never use `git clean`, `git checkout .`, `git reset --hard`, or `git stash`.

## Verification

Run every command from the worktree root, each as its own literal call.

Notation:
- `<tests>` is `claude/.claude/scripts/tests`.
- `<mf>` is `scripts/venv-run.sh python3 <tests>/_move_fidelity.py`.
- `<w1>` is the environment prefix `PYTEST_XDIST_AUTO_NUM_WORKERS=1` (G3). Every pytest and `select-tests.py` call carries it.
- `<scratch>` is `~/.local/state/cli-phase/<pr>-<base>`, with `<pr>` `1` or `2` and `<base>` step 1's SHA. It lives outside the repository and outside `/tmp`. A missing snapshot stops the run, and none is rebuilt from a later tree.

**PR 1**
0. **Preconditions.**
   - Shipping follows CLAUDE.md § "Shipping": run `~/.claude/scripts/autonomous-shipping-active.sh` in each session before its first commit, push, or `gh pr create`.
   - `scripts/venv-run.sh pytest --version` and `scripts/venv-run.sh ruff --version` each exit 0.
   - Commit the plan alone. Stage it with `git add .claude/plans/cli-phase-and-test-placement-rule.md`. `git diff --cached --name-only` must print only that path. Commit with `Refs GH-1116`.
   - Run `git fetch origin main`, then `git rev-list --count HEAD..origin/main`. If the count is not zero, run `git rebase origin/main`.
   - Grep L for the 18 node names and the six banners in row 6. A missing one stops the run.
   - F2 probe (M14). Use Grep for `code-file-line-limit` with glob `**/*.py`; no `.py` file names it at 0a66c019. If a hit is the line-count check, the PR includes its exception-row edit. An unclear hit goes to the engineer.
1. **Baseline.**
   - Run `git rev-parse HEAD`, then `mkdir -p ~/.local/state/cli-phase`, then `mkdir <scratch>`. An existing `<scratch>` stops the run.
   - Copy L to `<scratch>/L-base.py`. Copy every other file the PR modifies to `<scratch>/before-<basename>`, one `cp` per file.
   - Run `<w1> scripts/venv-run.sh pytest -n0 <L> <the five existing destinations> --junitxml=<scratch>/base.xml`.
2. **Dispatch A1.**
3. **Commit-1 proof.**
   - Run `grep -v '^[[:space:]]*$' <scratch>/L-base.py | LC_ALL=C sort > <scratch>/base.sorted`. Do the same for L into `<scratch>/c1.sorted`. Then `diff <scratch>/base.sorted <scratch>/c1.sorted` must print exactly two content lines: `> class TestSkillPairFormatDriftCanary:` and `> class TestCacheEfficiencyFormatDriftCanary:`.
   - `<mf> remainder --before <scratch>/L-base.py --after <L> --changed TestFormatDriftCanary --added TestSkillPairFormatDriftCanary TestCacheEfficiencyFormatDriftCanary` is clean.
4. **Commit 1.** Run `/code-review`. Stage L and commit with `Refs GH-1116`. Copy L to `<scratch>/L-c1.py`.
5. **Dispatch A2.**
6. **Commit-2 proof.**
   - `<mf> nodes --before <scratch>/L-c1.py`, with `--after` for each of the eight destinations, `--moved` and `--verbatim` both listing the 20 names, prints `compared-nodes: 20` and exits 0.
   - Write `<scratch>/L-residue.py`: L-c1's line 1, a blank line, then L-c1's :86-93. Then run `<mf> remainder --before <scratch>/L-c1.py --after <scratch>/L-residue.py --removed <the 20 names>`. Every finding must be one of: a `remainder-comments` line for one of the six banners, a `remainder-comments` line for the comment block that ends on the line directly above `_UNCONDITIONAL_HEADER_CASES`, or an `import-diff` line. Any other finding stops the run.
   - New-file census. For each new file, write `<scratch>/stub-<basename>`:
     - for `test_transcript_cli.py`, L-c1's :86-93;
     - for the multi-root file, its docstring, a blank line, and the comment block that ends on the line directly above `_UNCONDITIONAL_HEADER_CASES` in L-c1 (row 35: commit 1 moves it off :672-676);
     - for the subagents seam file, its docstring.

     Then `<mf> remainder --before <scratch>/stub-<basename> --after <file> --added <its node names>` must print only `import-diff: undeclared added import` findings, and `remaining-statements:` 5, 1, and 1 respectively.
   - Existing-file census. For each grown file, `<mf> remainder --before <scratch>/before-<basename> --after <file> --added <its node names> --import-added <A2's triples for it>` exits 0.
   - `git status --short` shows L deleted, the three new files untracked, and the six Modify paths modified, and nothing else.
   - `wc -l` of every created and grown file is under 1,000 (G2). Record each count for the PR body.
   - `scripts/venv-run.sh ruff check claude/.claude/ claude-skills/ plugins/ evals/` is clean.
   - Each new file passes alone: run `<w1> scripts/venv-run.sh pytest -n0 <file>` once per new file.
   - Run `<w1> scripts/venv-run.sh pytest -n0 <the eight destinations> --junitxml=<scratch>/c2.xml`. Then `<mf> test-ids --before <scratch>/base.xml --after <scratch>/c2.xml` with these renames:
     - `TestFormatDriftCanary::test_drift_warning_also_fires_in_skill_pair=TestSkillPairFormatDriftCanary::test_drift_warning_also_fires_in_skill_pair`;
     - one `TestFormatDriftCanary::<m>=TestCacheEfficiencyFormatDriftCanary::<m>` pair for each of the four `*_cache_efficiency*` methods.

     It must be clean, with equal before and after totals.
7. **Commit 2.** Run `/code-review`. Stage the deletion with `git rm --quiet -- <L>`, and each other file with its own `git add`. Commit with `Refs GH-1116`.
8. **Dispatch A3.**
9. **Commit-3 checks.**
   - Run Grep with `multiline: true` for `test_transcript_analysis\.py` over the architecture doc, the eight helper modules, `conftest.py`, `select-tests.py`, and `test_transcript_cost.py`. It must match nothing. Grep for `legacy file` in the architecture doc must match nothing too.
   - Seeded control for the new test:
     1. Write an empty `claude/.claude/scripts/transcript_analysis/zz_probe.py`.
     2. Run `<w1> scripts/venv-run.sh pytest -n0 <tests>/test_transcript_analysis_module_test_files.py`. It must fail, naming `zz_probe`.
     3. Run `rm -- claude/.claude/scripts/transcript_analysis/zz_probe.py`.
     4. Rerun the test, which must pass. `git status --short` must not list `zz_probe.py`.
   - ruff is clean, and A3's pytest runs pass.
10. **Commit 3.** Run `/code-review`, which also runs `ai-instruction-and-memory-files` on the rule file. Then stage, and commit with `Refs GH-1116`.
11. **Selected suite and hand-off.**
    - Apply the sync check. Then run `/ready-for-review`, whose step 2 runs `<w1> scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py`. It selects the full suite (row 11).
    - Follow `.claude/plans/leaf-module-tests.md` Verification 8 for that run: the `test_shellcheck.py` pre-run, the background call, the marker re-activation, and the merge-base reproduction of a failure outside Critical files. The run must pass.

**PR 2**
0. **Preconditions.**
   - Run `git fetch origin main` as its own call. Then `git ls-tree --name-only origin/main -- claude/.claude/scripts/tests/test_transcript_cli.py` prints the path, and the same command for L prints nothing. Otherwise stop: PR 1 is not in this base.
   - Create the branch and worktree per `branch-management`.
   - Repeat PR 1 step 0's shipping check, tool check, and F2 probe.
1. **Baseline.**
   - Make `<scratch>` as in PR 1. Copy the shim to `<scratch>/shim-base.py`, `test_transcript_cli.py` to `<scratch>/cli-test-base.py`, and every other modified file to `<scratch>/before-<basename>`.
   - Record the body range. Use Grep `-n` on the shim for `^import argparse$` and `^if __name__`. The range runs from the first through the last non-blank line before the second; at 0a66c019 it is 7-1021.
   - `mkdir <scratch>/account`. Write `<scratch>/runs` with three lines, using the absolute path of `<scratch>/account`: `buckets`, then `--config-dir <account> buckets`, then `--config-dir <account> cost`.
   - Run `<mf> capture --script claude/.claude/scripts/transcript-analysis.py --account <scratch>/account --out <scratch>/cap-base --runs <scratch>/runs --all-help`.
   - Run `<w1> scripts/venv-run.sh pytest -n0` on `test_transcript_cli.py`, `test_transcript_cli_bootstrap.py`, `test_transcript_analysis_cost_import_direction.py`, `test_transcript_analysis_import_cycles.py`, `test_transcript_package_module_prefixes.py`, `test_transcript_analysis_architecture_doc.py`, `test_transcript_analysis_branch_attribution_structure.py`, and `test_transcript_analysis_module_test_files.py`, with `--junitxml=<scratch>/base.xml`.
2. **Dispatch B1.**
3. **Commit-1 proof.**
   - Run `sed -n '<first>,<last>p' <scratch>/shim-base.py > <scratch>/shim-body.txt`, then `tail -n +3 claude/.claude/scripts/transcript_analysis/cli.py > <scratch>/cli-body.txt`. Then `diff <scratch>/shim-body.txt <scratch>/cli-body.txt` prints nothing. `head -n 2` of `cli.py` shows the docstring line and an empty line.
   - `<mf> nodes --before <scratch>/shim-base.py --after claude/.claude/scripts/transcript_analysis/cli.py --moved build_parser main --verbatim build_parser main` prints `compared-nodes: 2`.
   - `<mf> remainder --before <scratch>/shim-base.py --after claude/.claude/scripts/transcript-analysis.py --removed build_parser main --added __all__`. Every finding is an `import-diff` line or the one `remainder-comments` line for `__all__`'s comment.
   - `test -x claude/.claude/scripts/transcript-analysis.py` exits 0. If it does not, run `chmod +x claude/.claude/scripts/transcript-analysis.py` and rerun it.
   - Capture into `<scratch>/cap-b1` as in step 1. `<mf> compare-captures <scratch>/cap-base <scratch>/cap-b1` is clean, with no `--moved-callable` (row 16).
   - `wc -l` of `cli.py` and of the shim. Record `cli.py`'s count, expected 1,017, as its overage against 1,000 (rows 2, 32).
   - ruff is clean. B1's pytest runs pass, `test_transcript_cli_bootstrap.py`, `test_transcript_package_module_prefixes.py`, and `test_transcript_analysis_branch_attribution_structure.py` among them. So does `<w1> scripts/venv-run.sh pytest -n0 claude/.claude/hooks/tests/test_nudge_error_mode_analysis.py`.
4. **Commit 1.** Run `/code-review`, then stage, and commit with `Refs GH-1116`. Then `git ls-tree HEAD -- claude/.claude/scripts/transcript-analysis.py` must print mode `100755` (G4). Any other mode stops the run.
5. **Dispatch B2.**
6. **Commit-2 proof.**
   - `<mf> nodes --before <scratch>/cli-test-base.py --after <tests>/test_transcript_cli.py --moved <its six node names> --rename _mod=cli` prints `compared-nodes: 6` and exits 0. Exit 2 stops the run.
   - `<mf> remainder --before <scratch>/cli-test-base.py --after <tests>/test_transcript_cli.py --removed _SCRIPT _spec _mod --changed <the six names>`. Every finding is one of: an `import-diff` line, the one `remainder-ast: delete` line naming both the `sys.path.insert` and the `exec_module` statements, or the one `remainder-comments` line for the loader comment.
   - Grep checks, each matching nothing:
     - in the architecture doc, the patterns `from the shim`, `shim's own`, `the shim's .build_parser`, `The shim reads`, `Its own body holds`, and `cli\.py. phase`, where each unescaped `.` matches the doc's backtick;
     - in the architecture doc, `reads a name[[:space:]]+only the shim has` with `multiline: true`, since the doc wraps that phrase across lines;
     - `shim|transcript-analysis\.py` in the cost import-direction test;
     - `top-of-file comment` in `__init__.py`;
     - `later phase` in `test_transcript_cli_bootstrap.py`.

     Every line B2 returns for `shim` is one of row 33's allowed residues.
   - ruff is clean. Run the junit set from step 1 into `<scratch>/b2.xml`. Then `<mf> test-ids --before <scratch>/base.xml --after <scratch>/b2.xml` with `--rename test_shim_back_import_from_cost_matches_sanctioned_set=test_cli_back_import_from_cost_matches_sanctioned_set test_shim_still_imports_from_cost_module=test_cli_still_imports_from_cost_module` and `--expect-new "test_module_imports_first_in_fresh_interpreter[cli]"` must be clean.
7. **Commit 2 and hand-off.**
   - Run `/code-review`, stage, and commit with `Refs GH-1116`.
   - Apply the sync check, then run `/ready-for-review`. Its step 2 runs `<w1> scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py`, which selects by domain because PR 2 leaves `select-tests.py` alone. The run must pass.

## Out of scope

- **The seven single-command tests M3 leaves in `test_transcript_cli.py`.** These are `TestCacheEfficiencyArgparseWiring`, plus `TestBuildParser`'s `test_this_repo_and_projects_mutually_exclusive_on_skill_invocation`, `test_bare_skill_invocation_leaves_projects_unset`, `test_review_cost_by_size_all_accounts_flag_wires_to_all_accounts`, `test_pr_link_without_repo_flag_defaults_to_none`, and `test_pr_link_with_repo_flag_parses_value`. They move to their command files in the follow-up below, where each can import `cli` with no loader. Moving `test_review_cost_by_size_all_accounts_flag_wires_to_all_accounts` also waits on a seam split of `test_transcript_review_cost_by_size.py`, already 1,057 lines.
- **Switching the other twelve shim-loading test files to `from transcript_analysis import cli`** (row 3). This is a named follow-up, and it carries the item above.
- **Splitting `cli.py`'s 23 inline subparser blocks into `_add_<command>_subparser` helpers in their modules.** That brings `cli.py` under the limit (rows 2, 32). The same follow-up can convert `cli.py`'s name-binding imports to by-module form and add it to `PRODUCTION_MODULES`. Joining needs an exemption for `main()`'s `scope.PROJECTS_DIR` reassignment, or a move of that reassignment, because `test_production_modules_reference_only_real_sibling_attributes` fails it (row 12).
- **Naming outliers the structural test does not flag.** `test_author_outcome.py` is 3,716 lines and carries no `test_transcript_` prefix. `test_transcript_cost_counts.py` tests `subagent_mix.py`'s `cmd_cost_counts`. The structural test checks modules against files, not files against modules.
- **Auditing `conftest.py` for helpers only one family uses.** The rule governs new helpers. This plan does not inventory the existing ones.
- **Prose that was already stale and that this change neither causes nor touches:** `test_check_claude_md_length.py:889` and `test_transcript_context_composition.py:616`. PR 1's body lists both.
- **README.md:272's list of what project rules cover.** It already omits two of the seven rule files, and the architecture doc's citation carries discoverability.
- **Swapping `TRANSCRIPT_ANALYSIS_TEST_GLOB` for an exact path.** The glob over-selects six structural files on skill edits. Swapping it needs edits to nine `test_select_tests.py` expectations (`leaf-module-tests.md` row 15).
- **The F2 check itself, and any F1 record.** M14 adds only the conditional row edits. F1 re-derives its list from `git ls-files`.
- **Dated records naming L**, under `docs/reports/` and `docs/case-studies/`.
- **Committing the mover**, a single-use scratch tool.
