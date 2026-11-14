# Move workflow tests out of the hooks tree and replace hand-rolled YAML parsing

## Context

Goal: relocate the tests that read `.github/workflows/tests.yml` out of `claude/.claude/hooks/tests/` into `claude/.claude/tests/`, and replace hand-rolled YAML parsing with `yaml.safe_load` wherever a standard parser is available, sweeping the whole repo including pre-existing sites.

Why now: the owner's review of merged PR #1463 (GH-1440, split of the CI `tests` job) called both anti-patterns out and asked for a follow-up PR. Both predate that PR in places, and the review says that is no excuse to skip them.

Ask: the owner's review of PR #1463, quoted: "Tests for workflows should not be colocated with hooks tests. This may be a pre-existing issue. Please fix this architectural error in a follow-up PR."; "it looks like you're hand-rolling YAML parsing in python. Surely, there's a standard way to handle this. Check your guidelines around hand-rolling logic versus using a standard third-party lib. This problem may be pre-existing as well."; "The pre-existing nature of a problem is not an excuse to ignore it in your follow-up PR. Please sweep for the anti-pattern thoroughly". Engineer, this session:
- Destination, first asked as a question: "why not have a new subdirectory for workflow tests in claude/.claude/tests/ ?"
- Destination, decided after the staff-sdet and plan-architect consults and a check of colocating under `.github/`: selected "Flat in claude/.claude/tests/ (Recommended)".
- Extra scope: selected "More test-tree YAML reads".
- Extra scope: selected "Evals harness + docs + plugin bump".
- Extra scope: selected "select-tests.py cleanup".
- YAML sweep scope: selected "Fix defects, justify the rest (Recommended)".
- Floor tests: selected "Leave them in hooks/tests (Recommended)" for test_git_floor.py and test_python_floor.py.

## Approach

The three tests that read `.github/workflows/tests.yml` move from `claude/.claude/hooks/tests/` into `claude/.claude/tests/`, which `select-tests.py` and its test-source scanners already cover. Every hand-rolled YAML read in the test trees switches to `yaml.safe_load` or the repo's `parse_frontmatter`. Two hand-rolled sites stay, each with a one-line in-code reason, because they run where PyYAML is unavailable. This is the first of two PRs (rows 28 and 29). The evals harness's own frontmatter parsers, their documented invocations and the `skill-management` plugin bump follow in a separate PR (Out of scope).

**Destination and what it takes.** The moved files sit flat in `claude/.claude/tests/` (row 1). The `CLAUDE_TESTS_DIR` domain rule and both test-source scanners already cover that directory, so selection needs no new rule and no scanner edit (rows 4 and 5). Its one cost is the shell-exec guard. The hooks tree's autouse guard stops applying to the moved files. No conftest can replace it, because `claude/.claude/tests/` has none and adding one needs the forbidden `claude/.claude/tests/__init__.py` (D3). The two moved files that run bash, `test_ci_path_filter.py` and `test_ci_workflow_gates.py`, each install the guard with a module-level autouse fixture, as `claude/.claude/tests/test_shell_exec_guard.py:44-47` does (row 3). That restores the guard they ran under before the move without a conftest.

Alternatives set aside:

- A `workflows/` subdirectory of `claude/.claude/tests/`, which the engineer asked about. D3 bars a conftest there too, so it pays the same guard cost, and flat avoids only its scanner edits. Set aside because:
  - No test file in the repo nests under a `tests/` directory (row 5).
  - With no conftest, the subdirectory gives no fixture scoping and no import isolation.
  - `_is_test_source_change` and `_test_corpus` see only a test file directly inside a `tests/` directory, so it needs edits to both. A missed edit fails silently, because both mutual-consistency tests stay green (row 5).
  - The name over-promises. `test_git_floor.py`, `test_python_floor.py`, `test_select_tests.py` and `test_ci_python_pin_lib.py` also read `tests.yml`, and they stay in their own trees (rows 7 and 25).
- Colocating under `.github/`: no convention for it was found. It needs CI's pytest and ruff roots in `tests.yml`, `FULL_SUITE_TARGETS`, a `select-tests.py` domain rule, `pyproject.toml`, the README and `CLAUDE.md` test commands, and `.claude/rules/test-tree-packaging.md` with its test to change. How GitHub handles extra files under `.github/workflows/` is unverified.
- A repo-root `tests/`: outside CI's pytest roots and `FULL_SUITE_TARGETS`, so it would need a workflow and `select-tests.py` change.

**`select-tests.py` mapping.** No new row. A moved file already maps through the prefix-based `CLAUDE_TESTS_DIR` rule. `.github/workflows/tests.yml` stays unmatched, so an edit to it runs the full suite. That is right for the file that defines the CI run, which tests in three test trees read. The `GITHUB_ACTIONS_WORKFLOWS_RULE_MD` row is deleted rather than retargeted, because its premise is false (row 8).

**SKIP_REGEX.** Parse the workflow, then read the shell assignment with `shlex` (row 9).

**Frontmatter in test trees.** A file that already imports `validate_skill_structure`, by name or through `test_agent_roster.py`, reads through `parse_frontmatter` or `_declared_tool_names`: `test_agent_roster.py:204`, `test_send_back_skipped_self_review.py:753` and six key reads in `test_skills.py`. `test_transcript_period_cost.py:5994` needs one scalar and imports no validator. It therefore calls `yaml.safe_load` on the fence split, as `test_doc_counts.py:170-172` does. That adds no routing edge (row 17). `evals/test_review_bench_fixtures.py` already splits rendered frontmatter with `arms._split_frontmatter`, so its `name:` and `model:` asserts load that block with `yaml.safe_load` (row 19).

- **Selection completeness.** The validator importer set in `select-tests.py` gains four hooks-tree files. Three already import the validator undeclared, and `test_send_back_skipped_self_review.py` does once it imports `_declared_tool_names` (row 18). `test_select_tests.py` gains a scan that fails on any undeclared importer, so the set cannot drift again unnoticed (row 32).
- **Malformed SKILL.md.** In `test_skills.py`, a malformed SKILL.md fails only that skill's tests, with an error naming the file, and the rest of the module still collects (row 16).

**Kept hand-rolled, with a one-line in-code reason:**

- `_ci-python-pin-lib.sh`'s sed reader, which runs before `.venv` and its PyYAML exist (D2).
- `subagent_mix.py`'s `model:` regex, which runs under the user's own `python3` (D1).

**Assumption ledger**

Root — the owner's review of PR #1463 asks that tests of `.github/workflows/` not sit in the hooks test tree, and that hand-rolled YAML parsing be replaced with a standard parser repo-wide, pre-existing sites included. `[verified: this plan's Ask line]`

Givens:

- **G1.** PyYAML (`requirements-dev.txt:4`, `pyyaml==6.*`) is the only YAML library declared. Any other (python-frontmatter, ruamel.yaml, bashlex) is a new dependency, which needs the engineer's confirmation under `CLAUDE.md` § Safety. Reason: outside this plan.

Rows:

1. `[engineer-verified: "Flat in claude/.claude/tests/ (Recommended)"]` The moved files sit directly in `claude/.claude/tests/`. *anchors: root.*
2. `[verified: pyproject.toml:18,24; Glob of the three basenames; tests.yml:188,193; select-tests.py:489]` Mechanism: the directory is `claude/.claude/tests/`, which stays unpackaged (D3). Under prepend mode each file imports under its bare basename. Each of the three basenames occurs once in the repo. `from helpers import` resolves to the same-directory `helpers.py`, which `pythonpath` already lists. A repo-root `tests/` would sit outside CI's pytest roots and `FULL_SUITE_TARGETS`. *anchors: row1.*
3. `[verified: claude/.claude/tests/helpers.py:432-438,490-493,515-533,550,1487-1488,2774-2786,2799-2801; claude/.claude/hooks/tests/test_ci_path_filter.py:68-84,283-285; claude/.claude/hooks/tests/test_ci_workflow_gates.py:50,382-402; claude/.claude/hooks/tests/conftest.py:455-460; claude/.claude/tests/test_shell_exec_guard.py:44-47; claude/.claude/tests/helpers.py:566-589; claude/.claude/hooks/tests/test_shell_exec_guard_wired.py:11-19; claude-skills/skills/test-conventions/SKILL.md:109; claude/.claude/tests/test_pytest_collection_config.py:313-324]` Mechanism: each of the two moved files that run bash installs the shell-exec guard with a module-level autouse `_guard_shell_exec` fixture, one call into `helpers.install_shell_exec_guard`, in the shape `claude/.claude/tests/test_shell_exec_guard.py:44-47` has. This restores the guard both files ran under before the move. The guard raises only when argv[0] is a `#!/usr/bin/env bash` script or a `hook-class: gate` hook, or when bash runs under an env with no `PATH` key. Every argv[0] in these files is `grep`, `git`, `sys.executable` or bash itself, and both bash calls pass an env that carries `PATH`, so the guard does not fire today. Its coverage differs by file. `test_ci_path_filter.py`'s `run_ci_detect_step` runs a bare `bash`, so a later `helpers.py` edit that drops `PATH` from its env fails there. `test_ci_workflow_gates.py`'s only bash call uses the absolute `_BASH` path (`helpers.py:517`), so its fixture restores parity with the old tree and catches nothing today. Alternatives set aside:
   - A hand-written `PATH` tripwire test: it restates the guard's predicate, disagrees with it on `env=None`, covers one call in one file, and breaks on a safe helper change.
   - Importing one shared fixture instead of a per-module copy: it trips ruff F401, the reason `hooks/tests/conftest.py:457-459` gives for per-tree copies. The per-module body is one call into the single-home helper, so no guard logic is copied.
   - A conftest: for every conftest under `claude/.claude/`, `TestConftestModuleNamesAreUnique` requires package root `claude/.claude/`, which would need `claude/.claude/tests/__init__.py` (D3).

   *anchors: row2.*
4. `[verified: select-tests.py:505-506,665]` A path under `claude/.claude/tests/` matches the `CLAUDE_TESTS_DIR` domain rule, which tests with the prefix-based `_is_under`. It selects `claude/.claude/tests`. *anchors: row2.*
5. `[verified: select-tests.py:616-628; claude/.claude/scripts/tests/test_select_tests.py:144-162,345-388,1571-1582; Glob "**/tests/*/test_*.py" matched nothing]` A `test_*.py` directly inside `claude/.claude/tests/` is already in both `_is_test_source_change`'s and `_test_corpus`'s corpus, so neither changes. A file one level deeper would fall out of both, and both mutual-consistency tests would stay green. `test_statusline_command_test_module_change_selects_exactly_three_targets` already pins the selection a moved file gets: `CLAUDE_TESTS_DIR`, `TICKET_REFERENCE_DISCIPLINE_TEST_PATH` and `SELECT_TESTS_TEST_PATH`. So no test is added for a moved file's own selection. No test file in the repo nests under a `tests/` directory. *anchors: row2.*
6. `[verified: claude/.claude/tests/test_pytest_collection_config.py:454-467,481-487]` `TestNoBareSameDirectorySiblingImports` scans `claude/.claude/tests/*.py`, where each moved file's `from helpers import` becomes a bare import of a same-directory sibling. The test skips any directory with no `__init__.py`, so it passes with no change. *anchors: row2.*
7. `[verified: select-tests.py:1010-1014; claude/.claude/hooks/tests/test_git_floor.py:324-331; claude/.claude/hooks/tests/test_python_floor.py:33; claude/.claude/scripts/tests/test_ci_python_pin_lib.py:17; claude/.claude/scripts/tests/test_select_tests.py:25,2488; claude/.claude/tests/helpers.py:54]` No mapping is added for `.github/workflows/tests.yml`. It stays an unmatched path, which runs the full suite. *anchors: row4.*
8. `[verified: claude/.claude/hooks/tests/test_ci_path_filter.py:40-51,99-107; select-tests.py:346-347,370-372,810-812,947-948]` Delete the `GITHUB_ACTIONS_WORKFLOWS_RULE_MD` constant and row. Its comment says `test_ci_path_filter.py` reads the rule file. In fact the path is one candidate string fed to `grep -E` as input, the shape the `CHANGELOG_MD` comment already classifies as not a read. The `RULES_DIR` row keeps mapping the file to `SKILLS_TESTS_DIR` and `HOOKS_TESTS_DIR`. Retargeting the row instead would edit the same lines and keep a false comment. *anchors: row4.*
9. `[verified: tests.yml:79-83; claude/.claude/tests/helpers.py:2721-2730; claude/.claude/hooks/tests/test_ci_path_filter.py:54-58,248-261]` Mechanism: `_skip_regex_pattern` takes the detect step's `run:` from `extract_ci_detect_step_run_block` (`yaml.safe_load`). It asserts exactly one line whose stripped form starts `SKIP_REGEX=`, `shlex.split`s that line into exactly one word, and strips the prefix. This is the file's own yaml-then-shlex idiom (`_parallel_pytest_argv`). The two exactly-one asserts are deliberately strict: a benign edit such as a trailing comment on the assignment line fails loudly and names the line, where the old regex tolerated it. Alternatives set aside:
   - `re.search` over the run string: still hand-parses POSIX quoting, which stdlib `shlex` implements.
   - Running the line in bash and printing the variable: spawns a shell for a quoting question `shlex` answers.
   - bashlex: a new dependency (G1).

   *anchors: root.*
10. `[verified: claude/.claude/hooks/tests/test_ci_path_filter.py:226-233,270-275; select-tests.py:607-613,638-647,968]` The move and the fix make two docstrings false. `TestPytestTwoPassSplit` contrasts itself with a regex-read SKIP_REGEX. The third Gaps bullet says a conftest edit outside the directory does not select the file. After the move, a `conftest.py` edit under `claude/`, `claude-skills/` or `plugins/` selects `CLAUDE_TESTS_DIR`, which holds the file. Only `evals/conftest.py` still selects nothing but the review-bench tests. *anchors: row9.*
11. `[verified: grep "from validate_skill_structure import": test_skills.py:81, test_rules_frontmatter.py:55, test_announce_branch_review_history.py:22, test_global_claude_md_groups.py:19, test_agent_roster.py:30]` Five test files read frontmatter through `parse_frontmatter`, the existing pattern. The exception is `test_doc_counts.py:170-172`, a regex fence split followed by `yaml.safe_load`. That is still a parser, so it is unchanged. *anchors: root.*
12. `[verified: claude/.claude/hooks/tests/test_agent_roster.py:201-210,782-797,1035-1044]` Line 204's `^tools:` regex contradicts the same file's parser-based readers and misses the YAML list form. Fix: call `_declared_tool_names(AGENTS_DIR / name)` and test set membership. *anchors: row11.*
13. `[verified: claude/.claude/hooks/tests/test_send_back_skipped_self_review.py:35,752-754; claude/.claude/hooks/tests/test_doc_counts.py:41; claude/.claude/hooks/tests/test_deny_reviewer_tree_mutation.py:23]` Line 753's `startswith("tools:")` read becomes `_declared_tool_names(CODE_WRITER_PATH)`, imported `from .test_agent_roster`. Two files in the tree already use that same-tree, package-relative import. Reusing the normalizer avoids a third copy of comma-or-list handling. The import makes the file a transitive validator importer, so it joins the importer set in the same commit (row 18). *anchors: row12.*
14. `[verified: claude-skills/skills/tests/test_skills.py:120-133,152-153,167-168,218-219,240-245,357-358,579-580,618-626; plugins/skill-management/scripts/validate_skill_structure.py:105-107,157-161]` `test_skills.py` reads `user-invocable` and `disable-model-invocation` as substrings of the raw frontmatter, but `corpus_budget_violations` reads the parsed key's truthiness. `_skill_description` returns that raw frontmatter today, so its TRIGGER checks also see any `when_to_use` text. Fix: add `_parsed_frontmatter` on `parse_frontmatter`, and have `_skill_description` return the parsed `description` followed by `when_to_use`, the two keys the validator counts as listing text. A `description`-only return was set aside: `test_name_dispatched_skill_has_no_trigger_block` (618-626) would then pass on a TRIGGER block moved into `when_to_use`, which the model still sees. *anchors: row11.*
15. `[verified: grep of every SKILL.md for those two keys and for when_to_use; Grep "\A---" matched all 44 files under claude-skills/skills/*/SKILL.md and plugins/*/skills/*/SKILL.md]` Every current value of either key is a bare `true` or `false`, no SKILL.md sets `when_to_use`, and every SKILL.md opens with a frontmatter fence. So the parsed reads select the same skills, and the TRIGGER checks see the same TRIGGER text. Dropping discovery's `frontmatter and` guard changes no selection either. *anchors: row14.*
16. `[verified: claude-skills/skills/tests/test_skills.py:296,2350,2459,2479; plugins/skill-management/scripts/validate_skill_structure.py:64-68,92-102]` Discovery runs at collection, as class attributes. `parse_frontmatter` raises a `yaml.YAMLError`, or a `ValueError` from its fence search, and neither names the file. So `_parsed_frontmatter` re-raises both, and a non-mapping block, as a `ValueError` that names the file. Discovery reads through `_discovery_frontmatter`, which maps that error to `{}`. Contract for one malformed SKILL.md:
   - Collection of `test_skills.py` still succeeds, and every other skill keeps its coverage.
   - The malformed skill reads as carrying no flags. It stays in the model-invokable lists, or in the name-only or name-dispatched list it is registered in, and each of those lists' tests calls `_parsed_frontmatter` and fails naming the file.
   - The commit-time validator already rejects such frontmatter, so reaching this path takes a `--no-verify` commit or an uncommitted edit.

   *anchors: row14.*
17. `[verified: claude/.claude/scripts/tests/test_transcript_period_cost.py:5989-5996; claude/.claude/hooks/tests/test_doc_counts.py:170-172; grep: no scripts-tree test imports validate_skill_structure]` The review-pr `name` is read by line prefix. Fix: `yaml.safe_load` of the frontmatter fence split, the `test_doc_counts.py` form. The scripts tree therefore gains no validator importer, and `select-tests.py` gains no scripts-tree set or union row (row 32 weighs this against `parse_frontmatter`). *anchors: row11.*
18. `[verified: select-tests.py:253,287-291,709-710,916; claude/.claude/scripts/tests/test_select_tests.py:682-687; grep "validate_skill_structure|test_agent_roster import": test_announce_branch_review_history.py:22, test_doc_counts.py:41, test_deny_reviewer_tree_mutation.py:23]` `HOOKS_TESTS_IMPORTING_SKILL_STRUCTURE_VALIDATOR` declares two of the five hooks-tree files that import the validator today. It omits three:
   - `test_announce_branch_review_history.py`, which imports it by name.
   - `test_doc_counts.py` and `test_deny_reviewer_tree_mutation.py`, which import `test_agent_roster.py`, which imports it.

   A validator change selects none of the three, though a broken validator breaks their import. The set gains them and `test_send_back_skipped_self_review.py` (row 13). *anchors: row11.*
19. `[verified: evals/test_review_bench_fixtures.py:10-22,1290-1299,1313-1318,1374-1393; evals/review_bench/arms.py:28,62-73; requirements-dev.txt:4; tests.yml:119,188; grep: no evals/ module imports yaml today]` The `name:` and `model:` substring asserts at 1295-1296, 1315-1316 and 1392 become lookups in `yaml.safe_load` of the block `arms._split_frontmatter` already returns. CI installs `requirements-dev.txt` and collects `evals/`, so this file can be the first `evals/` module to import `yaml`. Both written values, `bench-<lens>` and `inherit`, load as strings. The `description:` substring and the line filter at 1375-1377 stay textual, because byte preservation is what they test. In the real-file test, the load also errors if the one-line edit leaves a real agent's rendered frontmatter unparseable. *anchors: row11.*
20. `[verified: claude/.claude/scripts/tests/test_ci_python_pin_lib.py:114-122; claude/.claude/hooks/tests/test_python_floor.py:511-520]` The sed reader stays (D2). A test already cross-checks it against `yaml.safe_load` on the real workflow. `test_python_floor.py` calls that production reader on purpose, because the floor must equal what `install-dev.sh` reads, so it is unchanged too. *anchors: root.*
21. `[verified: claude/.claude/scripts/transcript_analysis/subagent_mix.py:374-391; README.md:241]` The regex stays (D1). No agent file quotes `model:` today (grep). *anchors: root.*
22. `[verified: claude-skills/skills/tests/test_skills.py:2387-2389,8773-8777; claude/.claude/hooks/tests/test_global_claude_md_groups.py:388-392; evals/review_bench/arms.py:62-73]` Body extraction at a `---` fence is not YAML parsing, because the body is Markdown that yaml cannot load. These sites stay. *anchors: root.*
23. `[engineer-verified: "Fix defects, justify the rest (Recommended)"]` This label sets the YAML sweep's scope. *anchors: root.*
24. `[unverified]` The plan reads that label as: fix every site where PyYAML is importable, and keep-and-justify a site only where PyYAML is unavailable or where a parser round trip would rewrite bytes. That reading is the session's relayed option description and this plan's interpretation, not the engineer's words. This PR applies it to the test trees and the two kept sites, and the follow-up PR applies it to the evals harness (row 29). *anchors: row23.*
25. `[engineer-verified: "Leave them in hooks/tests (Recommended)"]` `test_git_floor.py` and `test_python_floor.py` stay in `claude/.claude/hooks/tests/`. *anchors: root.*
26. `[unverified]` The session reports PR #1463's review-ledger row `dfc0b0e2e9a9` for the SKIP_REGEX regex. This checkout's ledger has no rows, since it is keyed by branch, so the row was not read. *anchors: row9.*
27. `[engineer-verified: "More test-tree YAML reads"]` The engineer selected the test-tree frontmatter reads beyond the two definite defects (`test_skills.py`, `test_send_back_skipped_self_review.py`, `test_transcript_period_cost.py`, `test_review_bench_fixtures.py`) as scope. Row 29 places them in this PR. *anchors: row23.*
28. `[engineer-verified: "Split now"]` This plan is cut to a `Projected diff:` below 400 changed lines. The label answered `/plan-review`'s size question on the draft that projected about 590. *anchors: root.*
29. `[unverified]` The cut between the two PRs is the session's proposal, not the engineer's words. This PR carries Dispatch 1 and Dispatch 2 as Critical files names them. A later PR on its own branch and plan carries the evals harness parsers, their doc rewrites and the plugin bump (Out of scope). The cut leaves this PR no edit to an evals production module, a plugin directory or a doc beyond `README.md:547` and `CHANGELOG.md`, so no row or Verification step here rests on the follow-up. *anchors: row28.*
30. `[engineer-verified: "Evals harness + docs + plugin bump"]` The engineer selected the evals harness's YAML reads, their doc command rewrites and the `skill-management` plugin bump as scope. Row 29 places all three in the follow-up PR. *anchors: row23.*
31. `[engineer-verified: "select-tests.py cleanup"]` The engineer selected deleting the `GITHUB_ACTIONS_WORKFLOWS_RULE_MD` row and extending the validator importer set as scope. Row 29 places both in this PR. *anchors: row23.*
32. `[verified: claude/.claude/scripts/tests/test_select_tests.py:144-162,345-367,673-687,1819-1857]` Mechanism: `test_select_tests.py` derives every `_test_corpus` file that imports `validate_skill_structure`, by name or through another corpus module that does. It fails unless `select_pytest_targets([SKILL_STRUCTURE_VALIDATOR_MODULE])` covers each one, by the `_is_under`/`_expand_target` idiom at 356-359. The AST walk is extracted as `_imported_module_basenames` and shared with `_undeclared_payload_builder_importers`, the existing importer scan. Controls: a seeded undeclared importer in each import form must be flagged, and the real scan must find `test_agent_roster.py` (by name) and `test_doc_counts.py` (transitive). The 673-687 test cannot catch drift, because it builds its expected set from the constant it checks. Lighter primitives checked:
   - Plain `yaml.safe_load` at each test site, so no new edge exists: chosen for `test_transcript_period_cost.py` (row 17) and `test_review_bench_fixtures.py` (row 19). It does not remove the need for the scan, because the set is already three importers short (row 18).
   - Hand-adding the three missing members with no scan: that is how the set fell three short.
   - Selecting `HOOKS_TESTS_DIR` for a validator change: covers every importer with no set, but runs the whole hooks test tree for a change six of its files depend on.
   - Deriving the set inside `select-tests.py`: runs the same AST scan in production code on every selection, for a guarantee the test gives in CI.

   The scan sees only `_test_corpus`'s `test_*.py` files, so a helper module that imported the validator would escape it. None does today (grep). The scan's other residual gaps (subprocess reach, dynamic loads and one-hop closure) are named in Out of scope. `_test_corpus` also leaves out `evals/`, so no `evals/` edit, in this PR or the follow-up, changes the scan's result. *anchors: row18.*
33. `[verified: claude/.claude/hooks/tests/test_ci_path_filter.py:264-295; select-tests.py:597-613,968; claude/.claude/scripts/tests/test_select_tests.py:356-359; Glob "**/conftest.py": four tracked, three under claude/ or plugins/]` After the move, `test_parallel_invocation_collects_the_repo_lint` reads `hooks/tests/test_shellcheck.py` across trees, through a node-id string that `TestCrossDomainReadCompleteness` cannot see. Its selection for a `test_shellcheck.py` or `conftest.py` edit rests on `_is_test_tree_packaging_change`, whose comment names only the two packaging checks. Fix: a parametrized `test_select_tests.py` test that `select_pytest_targets` covers `claude/.claude/tests/test_ci_path_filter.py` for `claude/.claude/hooks/tests/test_shellcheck.py` and for each of the three conftests. The predicate's comment gains one line naming this dependent. *anchors: row10.*
34. `[verified: the five newest commits on main each carry one PR number in the subject, e.g. 71d915fe "... (#1463)"; Grep of CHANGELOG.md for test_skills.py and test_agent_roster.py found no entry]` `main` takes one commit per PR, so after merge the PR is the revert unit. Dispatch 1 and Dispatch 2 land as separate commits, so each is revertable before merge. This PR's one CHANGELOG bullet lands with Dispatch 1's commit. Dispatch 2 changes only test assertions and two comments, and no CHANGELOG entry names `test_skills.py` or `test_agent_roster.py`, so its commit message describes it instead. *anchors: root.*
35. `[verified: select-tests.py:498-502,1010-1014; the staff-sdet and staff-platform-engineer round-1 findings on compute_changed_paths at select-tests.py:1050]` `select-tests.py` is a `GLOBAL_TRIGGER_PATHS` member, checked before the unmatched-path fallback, and the changed-path set is cumulative against the merge-base. Once Dispatch 1 edits it, every `select-tests.py` run on this branch is a full suite. So neither dispatch runs it. Each runs the test files it touches, and the session runs the full suite in the background once after each commit (Verification 1). *anchors: root.*
36. `[engineer-verified: "Keep in this PR (Recommended)"]` The label answered `/plan-review`'s question whether to keep the per-module shell-exec guard fixtures in `test_ci_path_filter.py` and `test_ci_workflow_gates.py`, added after a round-3 review finding. The question named the change as touching `test_ci_workflow_gates.py` content and as not part of the original ask. *anchors: row3.*

## Critical files

Implementation runs as two sequenced `code-writer` dispatches against plan `.claude/plans/move-workflow-tests-and-use-yaml-parser.md`, each landing as its own commit after its own `/code-review` (row 34). Their file sets are disjoint, and the session owns a third:

- Dispatch 1 owns every `select-tests.py` and `test_select_tests.py` edit, and the one test edit that adds a validator importer.
- Dispatch 2 owns the remaining test-tree YAML reads and the two kept-site comments, and starts after Dispatch 1's commit.
- The session owns `CHANGELOG.md`, adding this PR's bullet and staging it before `/code-review` runs on Dispatch 1's staged diff, so the reviewed diff is the committed diff.
- The session also owns git and the repo-wide checks. It performs the three `git mv` moves and takes Verification 3's before-list before dispatching Dispatch 1, because `code-writer` does not stage and does not run repo-wide collection. It then dispatches Dispatch 1 to edit the moved files in place.
- Dispatch 2 is dispatched only after Dispatch 1's commit and after Verification 1's gate for that commit returned green. A red gate is fixed by amending Dispatch 1's commit before Dispatch 2 starts, not by stacking a commit. No edit touches the worktree while that gate runs, so the result is attributable to Dispatch 1's commit alone.

Neither dispatch runs `select-tests.py`. Once Dispatch 1 edits it, every run on this branch is a full suite with reason `global-trigger` (row 35). Each dispatch instead runs `scripts/venv-run.sh pytest` on the test files it edits or moves, plus Verification 2's ruff command, and leaves the full suite to the session's Verification 1.

Reuse rather than reimplement:

- `extract_ci_detect_step_run_block` (`claude/.claude/tests/helpers.py:2721`)
- `parse_frontmatter` (`plugins/skill-management/scripts/validate_skill_structure.py:54`)
- `_declared_tool_names` (`claude/.claude/hooks/tests/test_agent_roster.py:1035`)
- `_test_corpus` and the AST walk inside `_undeclared_payload_builder_importers` (`claude/.claude/scripts/tests/test_select_tests.py:144,1820`)
- `arms._split_frontmatter` (`evals/review_bench/arms.py:62`)

**Dispatch 1: relocate the workflow tests and fix validator selection.**

- `claude/.claude/tests/test_ci_path_filter.py`, moved there by the session, gets these edits:
  - `_skip_regex_pattern` follows row 9; import the helper, and delete `_WORKFLOW_PATH`, which only that function reads.
  - `TestPytestTwoPassSplit`'s docstring drops its "unlike SKIP_REGEX above" contrast.
  - The third Gaps bullet (lines 274-275) becomes: "A collection hook added to evals/conftest.py is judged only by CI's full suite, since select-tests.py selects only the review-bench tests for that edit."
- `claude/.claude/hooks/tests/test_ci_workflow_gates.py` moves, by the session's `git mv`, to `claude/.claude/tests/`. Its only content change is the module-level autouse `_guard_shell_exec` fixture, with `install_shell_exec_guard` added to its `from helpers import` (row 3). `test_ci_path_filter.py` gets the same fixture and import.
- `claude/.claude/hooks/tests/test_ci_python_matches_dev_interpreter.py` moves, by the session's `git mv`, to `claude/.claude/tests/` with no content change.
- `claude/.claude/hooks/tests/test_send_back_skipped_self_review.py` (752-754): row 13. Delete `_code_writer_declared_tools`, and call `_declared_tool_names(CODE_WRITER_PATH)` imported `from .test_agent_roster`.
- `claude/.claude/scripts/select-tests.py`:
  - Delete `GITHUB_ACTIONS_WORKFLOWS_RULE_MD` (346-347), its comment paragraph (810-812) and its row (948).
  - `HOOKS_TESTS_IMPORTING_SKILL_STRUCTURE_VALIDATOR` (287-291) adds `test_announce_branch_review_history.py`, `DOC_COUNTS_TEST_PATH`, `test_deny_reviewer_tree_mutation.py` and `test_send_back_skipped_self_review.py` (row 18). Its comment, and the one at 709-710, say the members import it "by name or through test_agent_roster.py".
  - `_is_test_tree_packaging_change`'s comment (597-606) gains: "CLAUDE_TESTS_DIR's test_ci_path_filter.py also depends on this selection, because its lint-collection probe collects hooks/tests/test_shellcheck.py." (row 33)
- `claude/.claude/scripts/tests/test_select_tests.py`:
  - Delete the two `GITHUB_ACTIONS_WORKFLOWS_RULE_MD` tests (1191-1210) and its `_EXACT_MATCH_LITERAL_PATH_CONSTANTS` entry (2258).
  - The 673-687 test's docstring stops enumerating importers.
  - Extract `_imported_module_basenames(source) -> set[str]` from `_undeclared_payload_builder_importers` (1820-1834), which then calls it. Its parametrized fixture test (1845-1857) passes unchanged.
  - Add `_skill_structure_validator_importers(sources) -> set[str]` and row 32's tests: the real-corpus coverage check with its two non-vacuity members, and a parametrized seeded-importer test. The seeded forms are `from validate_skill_structure import X`, `import validate_skill_structure`, and `from .test_x import Y` where the fixture's `test_x` imports the validator. A comment-only mention must not count.
  - Add row 33's parametrized selection test.
- `README.md:547`: `test_ci_python_matches_dev_interpreter.py`'s new path.
- This dispatch's verification:
  - `scripts/venv-run.sh pytest` on the three moved files, `test_send_back_skipped_self_review.py` and `test_select_tests.py`, and the ruff command.
  - Verification 3's node-id comparison, which the session runs. Its diff holds only `test_select_tests.py` additions and deletions, and any parameter id that names a moved file's path.
  - The session then commits and runs Verification 1, which is Dispatch 1's gate with Dispatch 2 absent.

**Dispatch 2: test-tree YAML reads.** It makes no `select-tests.py` edit and adds no validator importer.

- `claude/.claude/hooks/tests/test_agent_roster.py` (201-210): row 12.
- `claude-skills/skills/tests/test_skills.py` (rows 14-16):
  - Add `_parsed_frontmatter(skill_file: Path) -> dict`, `_discovery_frontmatter(skill_file: Path) -> dict` and `_listing_text(frontmatter: dict) -> str`.
  - `_skill_description` returns `_listing_text(_parsed_frontmatter(_skill_file(skill_name)))`, with its docstring corrected.
  - The four discovery reads (152-153, 167-168, 218-219, 240-245) use `_discovery_frontmatter` and drop the `frontmatter and` guard. Lines 153 and 168 test `.get("user-invocable") is False`. Lines 219 and 243 test the falsiness of `.get("disable-model-invocation")`, matching `validate_skill_structure.py:157`.
  - Lines 357-358 assert `disable-model-invocation` truthy, and 579-580 assert it falsy, through `_parsed_frontmatter`.
  - Add tests:
    - `_parsed_frontmatter` raises a `ValueError` containing the file's path for each of malformed YAML, an unclosed block and a scalar-only block.
    - `_discovery_frontmatter` returns `{}` for malformed YAML.
    - `_listing_text` includes `when_to_use`.
    - A discovery-level test over a temporary skills tree, with `SKILLS_DIR` and `_PLUGINS_DIR` patched because the discovery functions read those globals. The tree holds one valid, one malformed and one frontmatter-less SKILL.md. Discovery does not raise, and `_model_invokable_skills()` lists the malformed and the frontmatter-less skill. This pins the dropped `frontmatter and` guard (round-1 finding on collection resilience).
    - A test through `_skill_description` itself on that tree: a skill whose `TRIGGER when:` sits only in `when_to_use` is seen by the name-dispatched negative check, and `_skill_description(<malformed>)` raises a `ValueError` naming the path.
- `claude/.claude/scripts/tests/test_transcript_period_cost.py` (5993-5996): row 17. Assert that `yaml.safe_load` of the fence-split block gives a `name` equal to `period_cost._REVIEW_PR_SKILL_NAME`.
- `evals/test_review_bench_fixtures.py`: `import yaml`, and lines 1295-1296, 1315-1316 and 1392 assert on the `name` and `model` values of `yaml.safe_load(frontmatter)` (row 19). The `tools` asserts in the same tests also compare the parsed dict's `tools` value, so the write is not checked only by `arms._parse_tools_field`, its own regex twin.
- `claude/.claude/scripts/_ci-python-pin-lib.sh`: after line 7, add "# Read with sed, not a YAML library, because install-dev.sh calls this before .venv and its PyYAML exist." and "# test_ci_python_pin_lib.py checks this reader against yaml.safe_load on the real workflow."
- `claude/.claude/scripts/transcript_analysis/subagent_mix.py:374`: above the constant, add "# A regex, not PyYAML, because this package runs under the user's own python3, which install.sh adds no packages to."
- This dispatch's verification:
  - `scripts/venv-run.sh pytest` on every test file it edits and on `claude/.claude/scripts/tests/test_ci_python_pin_lib.py`, which runs the edited shell library, plus the ruff command.
  - Verification 3's node-id comparison, which the session runs. Its diff holds only `test_skills.py` additions.
  - The session then commits and runs Verification 1.

**Session: `CHANGELOG.md`.** One `### Changed` bullet under `[Unreleased]`, staged before `/code-review` runs on Dispatch 1's staged diff: the three tests that read `tests.yml` moved from `claude/.claude/hooks/tests/` to `claude/.claude/tests/`. Precedent for contributor-facing entries: `CHANGELOG.md:17,192`. Dispatch 2's commit adds no bullet (row 34).

Size by part, in changed lines:

- Dispatch 1, about 205. One of the three moves is a pure rename, and about 140 lines are `test_select_tests.py`.
- Dispatch 2, about 170: about 135 in `test_skills.py`, about 30 across `test_agent_roster.py`, `test_transcript_period_cost.py` and `test_review_bench_fixtures.py`, and three comment lines.
- `CHANGELOG.md`, about 2.

Projected diff: about 380 changed lines across 14 files.

## Verification

1. `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py` passes, and its stderr reports `global-trigger`. The session runs it in the background twice: after Dispatch 1's commit, as Dispatch 1's gate with Dispatch 2 absent, and after Dispatch 2's commit. Dispatch 2 starts only after the first run is green. It runs the full suite on its own decision, which is `CLAUDE.md`'s case 1, so no run is widened by hand. Under local load a `timing`-marked test can fail on contention, so re-run that node alone with `-n0` and treat only a serial failure as a regression. Why it is a full suite:
   - This diff edits `select-tests.py`, a `GLOBAL_TRIGGER_PATHS` member (`select-tests.py:498-502`), and that check runs before the unmatched-path fallback (`select-tests.py:1010-1014`).
   - No `.github/` path is in the diff, so the workflow is not what widens the run.
2. `scripts/venv-run.sh ruff check claude/.claude/ claude-skills/ plugins/ evals/` is clean.
3. Collected node ids match the starting tree's, except for tests this PR adds or deletes. A count check is not enough: the PR adds tests, and a swapped parametrized case keeps its count. The session runs this comparison for each dispatch, keeping its lists in a scratchpad outside the repo:
   - Before the dispatch's first edit, and for Dispatch 1 before the three `git mv` moves, `scripts/venv-run.sh pytest claude/.claude/ claude-skills/ plugins/ evals/ --collect-only -q` exits 0. Keep its lines that contain `::`.
   - After the dispatch's last edit, the same command exits 0, and its `::` lines are kept the same way.
   - For Dispatch 1, rewrite the three moved files' `claude/.claude/hooks/tests/` prefix to `claude/.claude/tests/` in the before list with `sed -E`. Sort both lists and `diff` them.
   - The starting tree is the merge-base for Dispatch 1 and Dispatch 1's commit for Dispatch 2, so the two comparisons together cover the merge-base to HEAD. A reviewer can reproduce either one from a worktree at that start.
   - Every diff line is a test this PR adds or deletes in a file Critical files names, or a parameter id that names a moved file's path. The only deletions are the two `GITHUB_ACTIONS_WORKFLOWS_RULE_MD` tests.
   - No diff line comes from the three moved files, from `test_review_bench_fixtures.py`, or from a `test_skills.py` test parametrized by discovery (`SPECIALIST_SKILLS`, `MODEL_INVOKABLE_SKILLS`, `COMMAND_SKILLS`, `NAME_ONLY_SKILLS`, `NAME_DISPATCHED_SKILLS`).
4. After Dispatch 1's commit, `git diff -M --name-status origin/main...HEAD` lists the three moves as `R` entries.
5. After Dispatch 2, this grep hits exactly seven lines. It hits sixteen on the starting tree. The seven are:
   - `subagent_mix.py`'s kept regex.
   - `test_agent_roster.py`'s `_declared_tools` docstring, which names a `^tools:` regex as the thing avoided.
   - `test_skills.py`'s routing-reviewer test docstring, which names the same regex.
   - Four evals harness lines the follow-up PR replaces: `run_skill_evals.py`'s `startswith("name:")`, the two regex constants in `measure_subagent_model_resolution.py`, and `arms.py`'s `_parse_tools_field`.

   The grep is a tripwire for the shapes this sweep found, not proof that no other shape exists:

```
git grep -nE '\^(name|model|tools):|startswith\("(name|tools):"\)|(user-invocable|disable-model-invocation): (true|false)" (not )?in ' -- '*.py'
```

6. In `/code-review`, re-log the session-reported ledger row `dfc0b0e2e9a9` as ADDRESS with `--relog` (row 26). The diff holds no skill, agent, rule or plugin-directory file, so no per-file-type review joins it (`.claude/rules/review-pipeline-dispatch.md`).
7. Dispatch 2's commit message justifies the two kept hand-rolled sites, `_ci-python-pin-lib.sh` and `subagent_mix.py`. This follows the "Hand-rolled logic in non-trivial domains" bullet in `CLAUDE.md`, which the owner applies to YAML. The PR body:
   - Its "Incidental edits" section names the three already-undeclared validator importers added to the set (row 18).
   - It names the four evals harness lines Verification 5 still hits as the follow-up PR's scope (row 29), because the owner's review asked for a thorough sweep.

## Out of scope

- **Follow-up PR: the evals harness sweep.** The engineer selected "Evals harness + docs + plugin bump" (Ask line), and the cut (rows 28 to 30) moves it to a later PR on its own branch with its own plan. That PR's scope:
  - Switch the frontmatter reads in `evals/run_skill_evals.py`, `evals/measure_subagent_model_resolution.py` and `evals/review_bench/arms.py` to `yaml.safe_load`, each with a stated and tested degenerate-input contract.
  - Keep `arms.py`'s byte-preserving `_replace_frontmatter_field` text edit, with a one-line reason and a check that parses the rendered frontmatter.
  - Rewrite every documented bare `python evals/...` invocation as `scripts/venv-run.sh python3 evals/...` in `evals/README.md`, `CONTRIBUTING.md`, `docs/cost-levers-considered.md`'s re-run recipe and `plugins/skill-management/skills/skill-review/SKILL.md`. Find them with a grep over every mention of `run_skill_evals`, `measure_subagent_model_resolution`, `measure_explore_recall` and `run_review_bench`, because the re-run recipe invokes a script by bare path and a `python`-prefix grep misses it.
  - Bump the `skill-management` plugin for the `SKILL.md` edit, honoring `plugin-semver`'s patch-or-minor verdict. The PR's CHANGELOG entry names the plugin and version and says nothing changes for consumers. The `SKILL.md` edit lands after `/skill-review`'s marker is current.
  - Until it lands, the four evals lines Verification 5 names stay hand-rolled, and `run_skill_evals.py:200-202` keeps its stale claim that CI installs only pytest and ruff.
  - The session's full draft covering both PRs, about 590 changed lines, is saved at `~/.claude/handoffs/yaml-sweep-and-workflow-tests-full-plan-draft-590-lines.md`. That path resolves only on the session's own machine, so the follow-up plan restates these requirements and does not depend on it.
- An issue about apt retries in the pin-git step, and the two unrecorded PR #1463 observations about `cancel-in-progress`. The engineer did not ask for them in this change.
- Moving `test_git_floor.py` and `test_python_floor.py`. The engineer chose to leave them (row 25).
- `test_agent_roster.py:1258,1302` test `"Bash" in` the parsed `tools` string, so a tool name merely containing "Bash" would match. The file also carries two parallel tools normalizers (`_declared_tools` at 783, `_declared_tool_names` at 1035). Both read through the parser already, so this is a substring and duplication issue, not YAML parsing.
- `_config.sh`'s hand-written reader for its TOML-subset state file (`_config.sh:202-207`). The Ask names YAML. Whether that reader should change is a separate decision.
- `subagent_mix.py`'s regex does not unquote a quoted `model:` value. No agent quotes one today, and fixing it without PyYAML (D1) would add hand-rolled quoting.
- **D1.** Making PyYAML available to code stowed from `claude/.claude/`, which runs under the user's own `python3` where `./install.sh` installs nothing (`README.md:241`, `README.md:107,111`). `install.sh` is editable, so this is a declined change, not a given. Declined because it adds a runtime dependency for every stow consumer, a new-package decision that needs the engineer's confirmation. `subagent_mix.py`'s regex stays hand-rolled as a result.
- **D2.** Reading the CI Python pin without `sed` in `install-dev.sh`'s pre-`.venv` step (`install-dev.sh:50-56`). The step exists to choose the interpreter that builds `.venv`, and any in-bash alternative is itself a hand-rolled YAML read, so `_ci-python-pin-lib.sh`'s sed stays.
- **D3.** Relaxing `.claude/rules/test-tree-packaging.md` (forbidden `claude/.claude/tests/__init__.py`, so no conftest in `claude/.claude/tests/`). Declined because packaging that directory would create a second `helpers` module object with its own `REPO_ROOT`, per that rule. The two moved files that run bash install the shell-exec guard per module in its place (row 3).
- A tree-wide shell-exec guard for `claude/.claude/tests/`. This is the decision for the destination tree, and this PR declines it. The facts behind that:
  - Four of the tree's five test modules with a direct `subprocess.run` or `run_hook*` call install no guard: `test_statusline_command.py`, `test_git_repo_builders.py`, `test_pytest_collection_config.py` and `test_resume_context_stub.py`. Besides the two moved files that run bash (row 3), only `test_shell_exec_guard.py` installs one (Grep).
  - This predates the PR and follows from D3.
  - Whether any of those four modules' calls is one the guard inspects was not checked.
  - A guard there needs a mechanism other than a conftest.
  - The two moved files that run bash install the guard themselves (row 3). A later `helpers.py` edit that drops `PATH` from `run_ci_detect_step`'s env, or adds an env-bash argv[0], fails in `test_ci_path_filter.py`. The same edit to `_run_aggregate_step` is not covered, because it runs bash by absolute path.
  - No test pins the two fixtures' presence. Each is one call into `helpers.install_shell_exec_guard`, and deleting one restores the pre-fix state silently.
- Validator-edit selection gaps the row 32 scan does not close. All three predate this PR, and CI's full suite is their backstop:
  - Subprocess reach: `test_require_skill_review.py` runs the hook, which shells out to `validate_skill_structure.py`. A validator edit selects no `HOOKS_TESTS_DIR`, so a scoped run never selects that test.
  - Dynamic loads: a module that loads the validator through `importlib.util.spec_from_file_location` is invisible to the scan's import nodes.
  - Closure depth: the scan follows one hop through a declared importer. A module that imports `test_doc_counts` or `test_deny_reviewer_tree_mutation` escapes. None does today.
- Old test paths in preserved records stay (Scope discipline, Axis 3): `CHANGELOG.md:17` and `docs/reports/2026-08-10-repo-quality-audit/findings.md:121`.
- `.github/workflows/tests.yml:113` names `test_ci_python_matches_dev_interpreter.py` by basename only, which stays accurate, so the workflow is not edited.
- Making `validate_skill_structure.parse_frontmatter` itself name the file and reject a non-mapping block, which would make `test_skills.py`'s `_parsed_frontmatter` wrapper unnecessary. `parse_frontmatter` returns a scalar-only block as-is, so `validate()` and `corpus_budget_violations` raise `AttributeError` on one (`validate_skill_structure.py:68,105,157`). Declined here: it changes the plugin's commit-time validator for every consumer, which needs its own review, and the Ask names hand-rolled parsing, not input validation in a reader that already uses a parser.
