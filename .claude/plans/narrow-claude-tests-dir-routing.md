# Narrow CLAUDE_TESTS_DIR routing off the broad .py predicate

## Context

`select-tests.py`'s `_is_py_source_under_claude_or_plugins` predicate
(`claude/.claude/scripts/select-tests.py:352-360`) routes every tracked
`.py` file under `claude/`, `claude-skills/`, or `plugins/` to
`CLAUDE_TESTS_DIR`, but only two test classes in
`claude/.claude/tests/test_pytest_collection_config.py`
(`TestConftestModuleNamesAreUnique` and `TestNoBareSameDirectorySiblingImports`)
actually depend on file-name or directory shape rather than content
(GitHub Issue #1150). This imposes an avoidable ~50-second wall-clock
tax on frequently-edited files with no relationship to what those two
test classes check — `claude/.claude/scripts/transcript-analysis.py`
alone has paid this tax on 98 historical commits. The intended outcome
is to narrow `CLAUDE_TESTS_DIR` routing to only the inputs that can
actually affect those two test classes, while declaring the coverage
this narrowing would otherwise silently drop for five test files that
import a module from another domain by name — coverage today's broad
routing gives only incidentally, via a `pytest claude/.claude/
--collect-only` collection check two other test classes happen to run.

## Approach

The change has two parts.
1. Split the `CROSS_DOMAIN_EXCEPTIONS` row at `claude/.claude/scripts/select-tests.py:515`. `_is_py_source_under_claude_or_plugins` keeps its scope but selects only `TICKET_REFERENCE_DISCIPLINE_TEST_PATH`. A new strict-subset predicate, `_is_test_tree_packaging_change`, selects `CLAUDE_TESTS_DIR`.
2. Add five declared rows. Each selects the specific test files that import a module from another domain by name. This replaces the incidental coverage the broad row gave those files.

**Part 1: packaging predicate.** It goes between `_is_py_source_under_claude_or_plugins` (352-360) and `_is_test_source_change` (369-375):

```python
def _is_test_tree_packaging_change(path: str) -> bool:
    return _is_py_source_under_claude_or_plugins(path) and (
        path.endswith("conftest.py")
        or Path(path).name == "__init__.py"
        or _is_under(path, HOOKS_TESTS_DIR)
        or _is_under(path, SCRIPTS_TESTS_DIR)
    )
```

The name reuses `.claude/rules/test-tree-packaging.md`'s term for the two checks it matches. Writing it as a subset follows `_is_test_source_change`, the one existing precedent for that shape.

**Part 2: declared import rows.** Place them right after the `SKILL_AUXILIARY_FILES_MODULE` row, the existing row of the same shape:

| Row | Changed module | Selected importers |
|---|---|---|
| A | `p == CONFIG_MODULE` | `HOOKS_TESTS_IMPORTING_CONFIG` |
| B | `p == CONFIG_DIR_MODULE` | `HOOKS_TESTS_IMPORTING_CONFIG`, `REVIEW_LEDGER_SCRIPT_TEST_PATH`, `SKILLS_TESTS_IMPORTING_SKILL_EVALS_RUNNER` |
| C | `_is_under(p, TRANSCRIPT_ANALYSIS_PACKAGE_DIR)` | `REVIEW_LEDGER_SCRIPT_TEST_PATH` |
| D | `p == SKILL_STRUCTURE_VALIDATOR_MODULE` | `HOOKS_TESTS_IMPORTING_SKILL_STRUCTURE_VALIDATOR` |
| E | `p in HOOKS_TESTS_MODULES_IMPORTED_BY_SCRIPTS_TESTS` | `SCRIPTS_TESTS_IMPORTING_HOOKS_TESTS_MODULES` |

**Caveat.** These rows are enumerated by hand from this session's grep (row 5). No test derives them. An importer added later, or one outside the grep's limits, is not caught automatically.

**Alternatives rejected or deferred:**
- **Part 1, unscoped name leg.** It would narrow a `conftest.py` or `__init__.py` outside the three roots from the full-suite fallback to `CLAUDE_TESTS_DIR` alone (row 14).
- **Part 1, exact `== "conftest.py"`.** The reader's pathspec is a suffix match (G2).
- **Part 1, direct-child-only test-dir legs.** The reader's pathspecs are recursive (G2).
- **Part 1, `__init__.py` only where it is an ancestor of a conftest.** This would need git or filesystem access at selection time for a rarely edited file.
- **Part 1, a file or node-ID target.** `TestMultiArgCollectionSpansTestDomains` shares these inputs, and node IDs would break the file-target fidelity checks.
- **Part 2, directory targets (`HOOKS_TESTS_DIR`, etc.).** They would add a whole test domain to every `transcript_analysis/` edit (row 9). Their only advantage, catching future importers within one tree, is weak without a derived check.
- **Part 2, the author_outcome import chain instead of the whole `transcript_analysis/` package.** The package-wide predicate stays correct when modules inside the package change their imports, and it costs only one extra test file.
- **Part 2, a derived import-completeness check.** Considered, and deferred to a follow-up issue by the engineer's selection (row 18).

### Assumption ledger

**Root:** the row at `select-tests.py:515` selects `CLAUDE_TESTS_DIR` for every `.py` file under `claude/`, `claude-skills/`, and `plugins/`. Only the test-tree-packaging checks depend on file names or locations. The plan narrows that routing to exactly those checks' inputs, and replaces the incidental coverage the broad row gave cross-domain importers with declared rows. Narrowing must not under-select any test it previously covered.

**Givens:**
- **G1.** pytest names a conftest by walking up `__init__.py` ancestors and stops at a directory whose name is not an identifier. Reason: pytest owns this algorithm. `[verified: .venv/lib/python3.12/site-packages/_pytest/pathlib.py:839-850; called at claude/.claude/tests/test_pytest_collection_config.py:269-272]`
- **G2.** In a git pathspec without `:(glob)`, `*` also matches `/`. So `*conftest.py` is a suffix match on the whole path, and `claude/.claude/hooks/tests/*.py` is recursive. Reason: git owns pathspec semantics. `[verified: git ls-files -- "*conftest.py" returned claude/.claude/hooks/tests/conftest.py, run by the dispatching session]` The gitglossary pathspec entry was not read.
- **G3.** CI's full suite on every push backstops local selection. Reason: making local selection authoritative would be a decision outside this plan. `[verified: CLAUDE.md "Commands"]`
- **G4.** Importing `a.b.c` executes `a/__init__.py` and `a/b/__init__.py`. Reason: Python owns import semantics. `[verified: Python Language Reference §5.4.2, "Regular packages" — confirmed during plan-review by staff-backend-engineer as standard, unambiguous CPython import-system behavior]`

**Rows:**
1. Inside `CLAUDE_TESTS_DIR`, only two scans enumerate `.py` files by path: `test_pytest_collection_config.py:280-287` and `:445-458`. `[verified: the dispatching session's grep across claude/.claude/tests/*.py]`
2. `TestMultiArgCollectionSpansTestDomains` (`:339-366`) depends on two hardcoded hooks/scripts test files and their conftests. The Part 1 predicate's legs cover all of them. `[verified: read of :339-366]`
3. `TestNestedWorktreeExcludedFromCollection` (`:50`) and `TestTimingMarkerCoverageParity` (`:206-208`) run `pytest claude/.claude/ --collect-only` and assert exit 0. They therefore incidentally check that every test module under `claude/.claude/` still collects. `[verified: read of :24-78, :191-236]`
4. Name imports that cross domains, and what each chain reaches outside its own domain:
   - **4a.** `HOOKS_TESTS_DIR` imports `_config`: `test_install_sh_sentinel_inventory.py:10`, `test_config_lib.py:2134`, `test_install_sh_machine_level_opt_ins.py:10`, `test_doc_counts.py:43`. `_config.py:23` imports `_config_dir`.
   - **4b.** `HOOKS_TESTS_DIR` imports `transcript_analysis.author_outcome` (`test_review_ledger_script.py:22`). Its imports leave the package only at `scope` → `_config_dir` (`author_outcome.py:35`, `scope.py:25`). The package's two `_config` importers, `pr_cost.py` and `pr_cost_export.py`, are not in author_outcome's chain.
   - **4c.** `HOOKS_TESTS_DIR` imports `validate_skill_structure`: `test_global_claude_md_groups.py:19`, `test_agent_roster.py:16`. `validate_skill_structure.py` imports no other module found by the row 5 greps.
   - **4d.** `SKILLS_TESTS_DIR` imports `run_skill_evals` (`test_skills.py:59`, `test_trigger_detector.py:22`), which imports `_config_dir` (`evals/run_skill_evals.py:55`).
   - **4e.** `SCRIPTS_TESTS_DIR` imports modules from the hooks test tree:
     - `test_author_outcome.py:41` imports `hooks.tests.conftest`, which imports `helpers` (`conftest.py:20`).
     - `test_config_parser_parity.py:24` imports `hooks.tests.test_config_lib`, which imports `helpers` and `_config` (`test_config_lib.py:28`, `:2134`).
     - Both also execute `claude/.claude/hooks/__init__.py` and `claude/.claude/hooks/tests/__init__.py` (G4).

   Changes to `helpers.py` already force the full suite (`GLOBAL_TRIGGER_PATHS`), so the `helpers` imports need no row. No existing row routes any other module above to its importers. `[verified: grep and read of each cited line; select-tests.py:259-263, :379-517]`
5. How row 4 was enumerated, and its limits. Three repo-wide greps, excluding worktrees, matched line-start `import`/`from` statements (at any indent) for every module name that resolves through `pyproject.toml`'s `pythonpath` roots or through `claude/.claude`:
   - `_config|_config_dir|_skill_auxiliary_files|transcript_analysis|scripts`
   - `run_skill_evals|validate_skill_structure|measure_subagent_model_resolution|helpers`
   - `conftest|hooks|scripts|tests|test_*|measure_subagent_model_resolution`

   The enumeration does not cover:
   - `importlib.import_module`/`__import__` string imports
   - relative imports
   - modules loaded by file path
   - imports resolved through other `sys.path` entries
   - anything added after this session

   `[verified: those three greps, run this session]`
6. Which of these gaps this PR opens:
   - 4a–4c lose row 3's coverage, because their importers sit under `claude/.claude/`.
   - 4d never had that coverage, because `claude-skills/` isn't collected by `pytest claude/.claude/`.
   - 4e keeps it, because `hooks/tests/conftest.py` and `hooks/__init__.py` still match the name leg and `test_config_lib.py` still matches the `HOOKS_TESTS_DIR` leg.

   Rows cover 4d and 4e as well, per CLAUDE.md's "audit structural siblings" rule. `[verified: read of the predicates and the collect-only scope]`
7. `hooks.tests.*` imports resolve with `claude/.claude` on `sys.path`: `test_author_outcome.py:40` inserts it, and `test_pytest_collection_config.py:313` asserts `claude/.claude` as the packaged trees' package root. `[verified: read]`
8. `resolve_target_paths` drops a file target that a selected directory already contains. So file targets inside `HOOKS_TESTS_DIR` add nothing when `HOOKS_TESTS_DIR` is also selected. `[verified: test_select_tests.py:1152-1164]`
9. `HOOKS_TESTS_DIR` collected 5267 tests, according to the docstring at `test_select_tests.py:1159-1161` `[verified: that docstring; not re-measured]`. That `transcript_analysis/` is edited often is `[unverified: inferred from recent commit subjects, not counted]`.
10. Two fidelity lists govern the new constants. `_FILE_TARGETS` (`test_select_tests.py:1535-1539`) must list every file target, or `test_every_directory_target_exists_on_disk` treats it as a directory and fails. `_EXACT_MATCH_LITERAL_PATH_CONSTANTS` (`:1516-1529`) must list every constant behind an exact-match predicate. `[verified: read of :1512-1656]`
11. Every tracked `*conftest.py` and `*__init__.py` lies under the three roots. `[verified: git ls-files, run by the dispatching session]`
12. No tracked `.py` file sits in a subdirectory below `claude/.claude/{hooks,scripts}/tests/`. `[verified: Glob → none]`
13. The `DOMAIN_RULES` row at `select-tests.py:388` already covers every path under `CLAUDE_TESTS_DIR`. `[verified]`
14. A repo-root `conftest.py` matches no rule today, so it takes the unmatched-path fallback. `[verified: read of select-tests.py:379-517]`
15. `TestCrossDomainReadCompleteness` resolves only module-level `Path`-chain constants, so it needs no change. `[verified: select-tests.py:395-406 and test_select_tests.py:328-363, a header comment plus the dispatching session's read]`
16. Existing tests that go stale are enumerated under Critical files. Two come from Part 2:
    - the `validate_skill_structure.py` fixture at `test_select_tests.py:653`, which gains the 4c targets;
    - the `claude/.claude/hooks/__init__.py` case at `:2110`, which gains the 4e targets. Those targets are outside `HOOKS_TESTS_DIR`, so they survive containment.

    `[verified: grep and read of :647-657, :2106-2123]`
17. `[engineer-verified: "I think folding a fix into this PR is appropriate because we are limiting the trigger pattern of the full suite."]` This covers only the decision to fix the cross-domain import gap in this PR.
18. `[engineer-verified: "Declared rows only, no new scanner."]` This is the option label the engineer selected. It covers only the choices of declared rows over a derived import-completeness check, with that check deferred. The file-target shape (M10) is this plan's choice and is not covered by either quote.

**Mechanisms:**
- M1 — `_is_test_tree_packaging_change` → `(CLAUDE_TESTS_DIR,)`. anchors: root, row1, row2
- M2 — the broad predicate's row becomes `(TICKET_REFERENCE_DISCIPLINE_TEST_PATH,)`, scope unchanged. anchors: root
- M3 — the M1 predicate is a strict subset of the broad one. anchors: row14
- M4 — `path.endswith("conftest.py")`. anchors: G2
- M5 — recursive `_is_under` test-dir legs. anchors: G2, row12
- M6 — `__init__.py` matched anywhere under the three roots. anchors: G1, row11
- M7 — comments and the header block updated for both parts, including a durable line saying the import rows are enumerated by hand. anchors: root, row5
- M8 — routing and predicate tests for Part 1. anchors: root, row14
- M9 — the five declared import rows A–E. anchors: row4, row6, row17, row18
- M10 — file targets naming each importer, not directory targets. anchors: row8, row9
- M11 — row C covers the whole `transcript_analysis/` package rather than author_outcome's import chain. anchors: row4
- M12 — `_FILE_TARGETS` and `_EXACT_MATCH_LITERAL_PATH_CONSTANTS` extended. anchors: row10
- M13 — one routing test per new row, following this file's existing per-row test convention. anchors: row4

## Critical files

Use one `code-writer` dispatch. Both parts touch the same two files, so there are no separate file sets to split across dispatches.

**`claude/.claude/scripts/select-tests.py`**
- **Part 1:**
  - Add `_is_test_tree_packaging_change` between lines 360 and 363.
  - Replace row 515 with `(_is_py_source_under_claude_or_plugins, (TICKET_REFERENCE_DISCIPLINE_TEST_PATH,))` followed by `(_is_test_tree_packaging_change, (CLAUDE_TESTS_DIR,))`, both before the `_is_test_source_change` row.
  - Remove the "Also selects CLAUDE_TESTS_DIR: …" sentence at 347-351.
  - Header entry at 477-479: say the broad predicate selects `TICKET_REFERENCE_DISCIPLINE_TEST_PATH` directly. Add an entry for `_is_test_tree_packaging_change`.
  - The predicate's comment states one durable fact per line and must not call the scans non-recursive:
    - `TestConftestModuleNamesAreUnique` resolves every tracked `*conftest.py` through its `__init__.py` ancestors.
    - `TestNoBareSameDirectorySiblingImports` scans `.py` files under `HOOKS_TESTS_DIR`, `SCRIPTS_TESTS_DIR`, and `CLAUDE_TESTS_DIR`.
    - Both pathspecs let `*` match `/`.
    - `CLAUDE_TESTS_DIR`'s own files are covered by its domain rule.
    - The scope matches the broad predicate's three roots, so a repo-root `conftest.py` still reaches the unmatched-path fallback.
- **Part 2 constants.** Add them next to the other file-path constants, with a one-line comment each:
  - `CONFIG_MODULE` = `claude/.claude/scripts/_config.py`
  - `CONFIG_DIR_MODULE` = `claude/.claude/scripts/_config_dir.py`
  - `TRANSCRIPT_ANALYSIS_PACKAGE_DIR` = `claude/.claude/scripts/transcript_analysis`
  - `SKILL_STRUCTURE_VALIDATOR_MODULE` = `plugins/skill-management/scripts/validate_skill_structure.py`
  - `HOOKS_TESTS_MODULES_IMPORTED_BY_SCRIPTS_TESTS`, a frozenset: `claude/.claude/hooks/__init__.py`, `claude/.claude/hooks/tests/__init__.py`, `claude/.claude/hooks/tests/conftest.py`, `claude/.claude/hooks/tests/test_config_lib.py`
  - `HOOKS_TESTS_IMPORTING_CONFIG`: `claude/.claude/hooks/tests/test_config_lib.py`, `…/test_doc_counts.py`, `…/test_install_sh_machine_level_opt_ins.py`, `…/test_install_sh_sentinel_inventory.py`
  - `REVIEW_LEDGER_SCRIPT_TEST_PATH` = `claude/.claude/hooks/tests/test_review_ledger_script.py`
  - `HOOKS_TESTS_IMPORTING_SKILL_STRUCTURE_VALIDATOR`: `claude/.claude/hooks/tests/test_agent_roster.py`, `…/test_global_claude_md_groups.py`
  - `SKILLS_TESTS_IMPORTING_SKILL_EVALS_RUNNER`: `claude-skills/skills/tests/test_skills.py`, `…/test_trigger_detector.py`
  - `SCRIPTS_TESTS_IMPORTING_HOOKS_TESTS_MODULES`: `claude/.claude/scripts/tests/test_author_outcome.py`, `…/test_config_parser_parity.py`
- **Part 2 rows and header entries:**
  - Add rows A–E from the Approach after the `SKILL_AUXILIARY_FILES_MODULE` row (488).
  - Put one header entry per row next to that row's entry (413-414).
  - Add one durable caveat line: these import rows are enumerated by hand from test import statements, no test derives them, and a new importer of one of these modules must be added to the matching `*_IMPORTING_*` constant.
- **Reuse:** `_is_py_source_under_claude_or_plugins`, `_is_under`, and the existing test-dir constants. `MAPPED_TOP_LEVEL_DIRS` is unchanged.

**`claude/.claude/scripts/tests/test_select_tests.py`**
- **Part 1 updates to existing tests.** Remove `CLAUDE_TESTS_DIR` from the expected targets and reword the docstrings of:
  - `test_hooks_change_selects_hooks_tests_and_transcript_analysis` (~420-433)
  - `test_scripts_change_also_selects_ticket_reference_discipline_test` (~445-459)
  - `test_multi_domain_change_unions_both_target_sets` (~771-780)
  - `test_skills_test_tree_change_selects_skills_tests` (~1007-1022)
  - `test_arbitrary_plugin_py_file_also_selects_hooks_tests` (~1063-1074)

  Also:
  - Update the exact `pytest_argv` lists in `test_domain_selected_paths_are_passed_through_to_run_pytest` (~1965-1988) and `test_argv_none_falls_back_to_sys_argv` (~2055-2076).
  - Docstring only: `test_statusline_command_test_module_change_selects_exactly_three_targets` (~1103-1114).
- **Part 1 and Part 2 both affect:**
  - `test_skill_management_scripts_change_also_selects_hooks_tests` (~647-657): drop `CLAUDE_TESTS_DIR`, add the two 4c files.
  - The `file-inside-directory` case in `test_stderr_scope_matches_recorded_pytest_argv_across_containment_collisions` (~2109-2122): add the two 4e files to the expected argv and update the comment.
- **New Part 1 tests**, next to `test_second_plugin_conftest_py_also_selects_claude_tests_dir`:
  - `claude/.claude/scripts/transcript-analysis.py` selects exactly `{SCRIPTS_TESTS_DIR, TICKET_REFERENCE_DISCIPLINE_TEST_PATH}`.
  - A `.py` file under `HOOKS_TESTS_DIR` and one under `SCRIPTS_TESTS_DIR` each include `CLAUDE_TESTS_DIR`.
  - Direct predicate checks: a `*conftest.py`-suffixed non-exact name matches, and a non-`.py` file under `HOOKS_TESTS_DIR` does not.
  - `select_pytest_targets(["conftest.py"])` stays full-suite with reason `unmatched-path`.
  - `_mod._is_test_tree_packaging_change("claude/.claude/hooks/tests/sub/helper.py") is True` — a direct predicate assertion pinning the recursive `_is_under` legs (M5) against a nested path, since no on-disk fixture has that shape today and every other planned test only exercises a flat, direct-child file.
- **New Part 2 tests (M13).** One exact-target-set test per row, using a real changed path:
  - `_config.py`
  - `_config_dir.py`
  - `claude/.claude/scripts/transcript_analysis/scope.py`. This test first asserts the file exists on disk, since no fidelity list guards `TRANSCRIPT_ANALYSIS_PACKAGE_DIR`.
  - `validate_skill_structure.py`
  - `claude/.claude/hooks/tests/test_config_lib.py`
  - Row E's remaining two frozenset members, `claude/.claude/hooks/tests/__init__.py` and `claude/.claude/hooks/tests/conftest.py` (one assertion each, or a single parametrized test over all four `HOOKS_TESTS_MODULES_IMPORTED_BY_SCRIPTS_TESTS` members) — each checking that `SCRIPTS_TESTS_IMPORTING_HOOKS_TESTS_MODULES`'s two files appear in `target_paths`. Without these, a typo or path drift in either untested member would select the wrong path and go undetected by every other test in this plan.
- **Fidelity lists:**
  - Add every new file-target constant to `_FILE_TARGETS`. This is required, or the directory-existence test fails. Drop the "three" count from its comment.
  - Add `CONFIG_MODULE`, `CONFIG_DIR_MODULE`, `SKILL_STRUCTURE_VALIDATOR_MODULE`, and `*sorted(HOOKS_TESTS_MODULES_IMPORTED_BY_SCRIPTS_TESTS)` to `_EXACT_MATCH_LITERAL_PATH_CONSTANTS`, per that list's own contract.

## Verification

- While iterating: `.venv/bin/pytest claude/.claude/scripts/tests/test_select_tests.py` (see README.md's Tests section for the worktree-relative `.venv` paths).
- Coverage of row 4's import chains. The five per-row tests (M13) must pass. Together they show every chain in row 4 (4a–4e) selects its importing test files. That row 4 is the complete set rests on this session's greps, cited in row 5 with their limits, not on a derived check.
- Gate: `.venv/bin/python3 claude/.claude/scripts/select-tests.py`. `SELECT_TESTS_SCRIPT` is in `GLOBAL_TRIGGER_PATHS` (select-tests.py:259-263), so the tool widens to the full suite with reason `global-trigger` (CLAUDE.md case 1). The run must be green, including `test_pytest_collection_config.py`, an unedited `TestCrossDomainReadCompleteness`, and the rule-table fidelity tests with the extended `_FILE_TARGETS`.
- `.venv/bin/ruff check claude/.claude/ claude-skills/`.

## Out of scope

- The scope of `_is_py_source_under_claude_or_plugins` and its `TICKET_REFERENCE_DISCIPLINE_TEST_PATH` routing.
- `TestCrossDomainReadCompleteness`.
- Routing for a `conftest.py` or `__init__.py` entirely outside the three roots. That gap already exists. M3 keeps such files on the unmatched-path fallback wherever no other rule claims them.
- **A derived import-completeness check.** This was considered: a test that walks every test's imports through `claude/.claude` plus `pyproject.toml`'s `pythonpath` and requires each reached module's selection to cover the importer. It is deferred to a follow-up issue by the engineer's selected option for a smaller diff (row 18). The accepted risk: rows A–E are enumerated by hand from row 5's greps. An importer added later, or one outside those greps' limits, is not caught automatically. This is the same accepted risk as `TestCrossDomainReadCompleteness`'s own "verifies precision, not recall" caveat (select-tests.py:403-406). CI's full suite backstops it (G3).
- File or node-ID targets for the `CLAUDE_TESTS_DIR` cost that remains on hooks/scripts test-file edits.
