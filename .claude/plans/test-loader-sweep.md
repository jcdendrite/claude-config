# Transcript-analysis test-loader sweep

## Context

Goal: switch the 26 transcript test files whose subjects already live in the `transcript_analysis/` package from the by-path `transcript-analysis.py` shim loader to direct package imports, and keep `test_transcript_package_module_prefixes.py`'s test-file attribute check non-vacuous for them.

Ask: "Sweep the 26 (Recommended)" — selected label, answering whether this PR should sweep the 26 already-moved files given that the merged prep plan leaves loader removal to each later phase.

Why now: PR #1270 retargeted `fake_projects`, `cost_ledger_file`, and `cost_ledger_enabled` to patch package modules directly, so a test file no longer needs the shim for those fixtures. `.claude/plans/decomposition-prep.md` (G2, `:170`; Out of scope, `:372`) gives loader removal to each later phase "as its subject moves". 26 of the 40 shim-loading files read only package-resident names: their subjects have already moved, so no future phase owns their loaders. The other 14 read a shim-only name (`build_parser`, `main`, or a `cmd_*` still defined in the shim) and stay under G2.

Intended outcome:
- the 26 files import `transcript_analysis.<module>` directly
- the guard test pins their `<module>.<name>` reads to real bindings
- `docs/transcript-analysis-architecture.md` describes the result

## Approach

Each of the 26 files drops its shim loader. Each binds the package modules it reads with one unaliased `from transcript_analysis import ...` line. Each `_mod.<module>.<name>` read becomes `<module>.<name>`. Each flat `_mod.<name>` read becomes an attribute of the name's home module, per the flat-name table in Critical files. One `code-writer` dispatch applies that rewrite to all 26 files with a throwaway codemod that lives outside the worktree.

The prefix guard resolves both read shapes through one shared helper. Its list holds all 26 files, the 3 holdouts it already covers, and `test_transcript_shared_fixtures.py`. It fails a listed file in three cases: the file has no checkable read, it binds a package module in a shape the guard cannot resolve, or it imports a package name instead of a module. It also fails any loader-free test file that imports from the package but is missing from the list. The shim drops the test-only passthroughs that lose their last reader in this sweep.

Alternatives set aside:
- **Deleting `_mod.` and nothing else.** 17 of the 26 files also read flat re-exports, `_config`, or stdlib modules through `_mod` (row 4). One re-export also has a different name in its home module (row 5).
- **Importing names instead of modules** (`from transcript_analysis.corpus import _parse_ts`).
  - Name imports would translate the flat `_mod.<name>` reads faithfully, because those reads already go through the shim's import-time bindings.
  - They would also move the 1,440 lines of `_mod.<module>.<name>` reads (row 29) from call-time lookup to import-time lookup. A monkeypatch of the module attribute would then stop reaching those reads.
  - Module imports change the lookup for the flat reads only, and row 10 shows that change is safe.
  - Module imports also match 7 of the 9 test-side package import statements (row 11).
  - The guard can check a `<module>.<name>` read but not a bare name.
- **Adding direct imports but keeping the loader**, as the hybrid holdouts do. Each file would still execute the whole shim, which is what the Ask removes.
- **Hand-editing the 26 files, deriving TEST_FILES by discovery, or leaving the shim alone:** see M1, M2, and M3.

**Open decisions: none.** The selected label covers the sweep. Two choices go beyond the session's option description. The engineer sees both when approving the plan rather than through a separate question:
- M3 adds `transcript-analysis.py` and `test_transcript_analysis_cost_import_direction.py` to the file list. Both edits follow from the sweep under the CLAUDE.md rules M3 cites.
- M2's completeness test adds `test_transcript_shared_fixtures.py` to TEST_FILES. That change edits only the guard, which is already on the list, and the file passes every check unchanged (row 30).

The codemod in M1 changes how the conversion runs, not what it produces, so it needs no decision.

### Assumption ledger

**Root:** 26 test files execute the whole `transcript-analysis.py` shim only to reach names that already live in `transcript_analysis/`. The prefix guard's test-file check sees only the `_mod.<module>.<name>` read shape. Converting those files without changing the guard would therefore silently remove the guard's coverage of them. This PR moves the 26 files to direct package imports, changes no test's ID or outcome, and keeps the guard checking every one of them.

**Givens:**
- G1: The 14 holdout files keep their loaders until their subjects move. The shim keeps `build_parser`, `main`, and the `cmd_*` functions it defines itself until the `cli.py` phase. The governing decomposition plan's phase order owns both. `[verified: .claude/plans/decomposition-prep.md:169-170, :372]`
- G2: Agents run `select-tests.py`, not the full suite. This repo's CLAUDE.md imposes it. `[verified: CLAUDE.md § Commands]`
- G3: No `.py` identifier may carry a ticket-prefixed token. No comment or docstring may follow a tracker ID with a phase or step qualifier. A repo test enforces both over every tracked `.py` file under `claude/`. `[verified: claude/.claude/hooks/tests/test_ticket_reference_discipline.py:1-8]`

**Rows:**
1. This PR converts exactly the 26 files named in the Context. `[engineer-verified: "Sweep the 26 (Recommended)"]`. This tag does not cover the option description, which was the session's own proposal.
2. The engineer passed over "Sweep 26 + drop build_parser", so the 12 files that read only `build_parser`/`main` stay as they are. `[unverified]`: this is an inference from an option they did not pick.
3. None of the 26 files reads a name the shim defines itself. A grep of every top-level def, class, and assignment in the shim found hits only in holdout files. `[verified: Grep of transcript-analysis.py:281-5193 top-level names against test_transcript_*.py]`
4. 9 files read only `_mod.<module>.<name>`: `audit_routing_samples`, `audit_routing_shape`, `cache_rebuild_switch_delta`, `cache_rebuild_ttl_accumulation`, `cache_rebuild_ttl_footing`, `denials`, `gh_cli`, `pr_cost_ledger`, `rearm_backtest_nudge_log` (each `test_transcript_`-prefixed). The other 17 also read flat re-exports, `_config`, or stdlib modules through `_mod`. Critical files lists those reads per file. So the conversion is a rename plus a lookup of each flat name's home module, not a pure rename. `[verified: Grep tool (ripgrep) "\b_mod\.(_\w*|[A-Z]\w*)" per file; Grep for every lowercase non-package name the shim binds (stdlib imports, defaultdict, iter_sessions, hook_denial_key, cmd_*, build_parser, main)]`
5. Every flat name in the table has a top-level binding in its home module. `_resolve_scan_roots` is the shim's alias for `scope.resolve_scan_roots`. `[verified: corpus.py:13, 118, 215; pricing.py:155, 520; scope.py:275, 470, 522, 688, 1106; cost.py:58, 174, 502; redaction.py:49; reviewer_yield.py:226-599; transcript-analysis.py:256]`
6. `_mod.<module>` is the same object as `transcript_analysis.<module>`. `_mod._config` and `_mod.hashlib`/`os`/`subprocess`/`datetime`/`UTC` are the shim's own imports of the same `sys.modules` entries. `_mod.datetime` is the `datetime` class, not the module (`transcript-analysis.py:23`). `_config` is the top-level `claude/.claude/scripts/_config.py`, which `pr_cost.py:23`, `pr_cost_export.py:16`, and `cost_ledger.py:28` also import. `[verified: transcript-analysis.py:8-63; test_transcript_denials.py:13-20; decomposition-prep.md row 10]`
7. At top level the shim assigns to no other module's attribute and calls no other module's function. It touches neither `sys` nor `os.environ`. The loader's `sys.path.insert` is redundant because pytest's `pythonpath` already holds the scripts directory. Dropping the loader therefore removes no side effect a test could rely on. `[verified: Grep "^[a-z_]+\.[A-Za-z_]+\s*=|^[a-z_]+\.[a-z_]+\(|^sys\.|^os\.environ" in the shim: no matches; pyproject.toml:18]`
8. No swept file, conftest, or test helper defines a local, parameter, fixture, or function named after a package module. The new module bindings therefore shadow nothing, and every `<module>.<name>` the guard resolves is a module read. `[verified: Grep; the only hits are keyword arguments at test_transcript_cost_ledger.py:156, 168, 213, 223 and a docstring at test_transcript_pr_cost_export_accounts.py:753]`
9. In the 26 files, `_mod` appears only as an attribute base and in the loader, except for one docstring (`test_transcript_cost_ledger_record_gates.py:150`). No file calls `setattr(_mod, "...")`. `_SCRIPT` and `_spec` each appear exactly three times per file, all in the loader. No `tests/_*.py` helper uses `_mod`. `[verified: Grep tool (ripgrep) "\b_mod\b[^.]", "_SCRIPT\b", "\b_spec\b" (count 3 in every shim-loading file), and "_mod\b" in tests/_*.py]`
10. Only one swept test patches a name that its file also reads flat. `test_transcript_pr_cost.py:698-704` reads `_mod.scope._resolve_project_scope` before patching it, and no flat read follows the patch. So rewriting flat reads changes no patched object's identity. `[verified: Grep of setattr targets for every table name]`
11. Test-side code holds 9 `from transcript_analysis...` statements. 7 bind package modules unaliased: `test_transcript_analysis_branch_attribution.py:14`, `test_transcript_shared_fixtures.py:4`, `test_transcript_review_rounds.py:15`, `conftest.py:32`, `_pr_cost_helpers.py:10`, `_handoff_nudge_helpers.py:6`, and `test_author_outcome.py:14`. The two exceptions are `test_author_outcome.py:13` (`as ao`) and `conftest.py:33` (`from transcript_analysis.corpus import SUBAGENT_SUBDIR`). `[verified: Grep "^\s*(from|import) transcript_analysis" in tests/]`
12. Ruff selects E, F, B, I, UP, and SIM. After the loader goes, F401 flags a now-unused `importlib.util`, `sys`, or `Path` import. F821 flags a missing module import. I001 sorts and merges the inserted import lines. `[verified: pyproject.toml:6]`. Whether `ruff check --fix` applies the F401 removal without `--unsafe-fixes` is `[unverified]`. The recipe removes by hand any F401 that `--fix` leaves.
13. The guard's test-file check matches only `_mod.<module>.<name>`. A converted file's `<module>.<name>` reads would match nothing and pass silently. `[verified: test_transcript_package_module_prefixes.py:264-286]`
14. The production check already resolves `<module>.<name>` through unaliased `from transcript_analysis import <module>` bindings. `_unresolvable_sibling_imports` flags every relative `from` import. Applied as-is to test files, it would flag the `from .conftest import ...` imports that `.claude/rules/test-tree-packaging.md` requires. The production side has three tests: by-module-only (:173-189), unaliased binding (:192-203), and attribute (:231-261). The test-file side has only the attribute twin. `[verified: test_transcript_package_module_prefixes.py:121-150, 173-261]`
15. TEST_FILES holds 14 names. 11 are among the 26. The other 3 (`cost_counts`, `plan_boundary`, `handoff_signal_response`) are holdouts that still read `_mod.<module>.<name>`. `[verified: :31-46]`
16. Non-vacuity assertions have in-repo precedent. `[verified: test_transcript_analysis_cost_import_direction.py:79-89; test_transcript_analysis_architecture_doc.py:52-57]`
17. After the sweep, these shim bindings have no `_mod` reader left in any test file:
    - `import hashlib` (:11)
    - `import _config` (:26)
    - `_compute_pr_cost_branch_totals` and `_compute_workstream_dollars` (:94-95)
    - reviewer_yield's eight noqa'd names (:224-231)
    - 11 entries of the module tuple (:35-63): `audit_routing`, `denials`, `gh_cli`, `read_scope`, `rearm_backtest`, `render`, `review_trace`, `reviewer_yield`, `spend_over_threshold`, `subagents`, `workstream_cost`

    Each binding's comment gives test-file `_mod` reads as its only reason. Each of those 11 modules is still imported through its own `from transcript_analysis.<module> import` block. Every other noqa'd cost, pricing, redaction, and render passthrough keeps a holdout reader. Examples: `_model_rates` at `test_transcript_plan_boundary.py:135`, `_cost_report` at `test_transcript_analysis.py:2790`, and `_REDACT_MAP_MISS_TOKEN` at `test_transcript_cost.py:494`. `[verified: Grep tool (ripgrep) of all 23 names and modules as "_mod\.<name>\b" across the 14 holdout files: zero hits; transcript-analysis.py:11-279]`
18. The other 14 tuple modules each keep at least one holdout reader: `redaction`, `pricing`, `cache_rebuild_rules`, `cost_ledger`, `pr_cost_ledger`, `handoff_nudge`, `cache_rebuild`, `cost`, `handoff_signal_response`, `ledger_common`, `plan_boundary`, `pr_cost`, `pr_cost_export`, and `subagent_mix`. Together with `corpus` and `scope`, which the shim reads bare, they make up the 16 tuple entries that survive. `[verified: same ripgrep search, at least one hit per module]`
19. The docstring at `test_transcript_analysis_cost_import_direction.py:82-83` names `_compute_workstream_dollars` as one of the shim's cost imports. The module docstring at `:8-12` says test files read `_mod.cost.<name>`. That claim stays true because `test_transcript_cost.py` and `test_transcript_handoff_signal_response.py` keep 22 such lines. `[verified]`
20. `docs/transcript-analysis-architecture.md:471-476` says every pr-cost-family test file loads its own shim copy and reads `_mod.<module>.<name>`. `:535-538` describes the guard's test-file scope as `_mod.<module>.<name>` reads. `[verified]`
21. `select-tests.py` maps `.claude/plans/` to no targets. So a baseline taken when the committed plan is the only diff selects nothing. It reads the committed diff, the dirty tree, and untracked files together. Any `docs/` path selects the hooks and skills test directories, and the architecture doc also selects the scripts test directory. It forwards extra argv to pytest. `[verified: select-tests.py:570, 788, 790, 839-868, 902-930, 1142, 1199]`
22. `_move_fidelity.py test-ids` compares the multiset of outcomes for each test ID across junit XML runs. It keys each ID on the components after the last `test_` component of the classname. It reports an `expect-new-mismatch` for a listed ID that does not grow, and an `id-unexpected` for any new ID that is not listed. `--rename` takes `OLD=NEW`, and `--expect-new` takes several IDs. `[verified: _move_fidelity.py:592-666, 722-723, 774-780]`
23. `test_transcript_reviewer_yield.py` holds the only `timing`-marked test among the 26. `test_transcript_analysis.py` holds the only one among the 14 holdouts. Timing tests run under `-m timing -n0`. `[verified: Grep "mark\.timing"; pyproject.toml:25, 31-32]`
24. The branch name contains `/`, so this worktree sits four levels below the main worktree root, where the contributor `.venv` lives. `[verified: worktree path; README.md:523]`
25. Some swept files are long. `test_transcript_reviewer_yield.py` runs to at least line 1,986, and `test_transcript_review_trace.py` runs to at least line 1,638. `[verified: last grep-hit line numbers]`
26. `git grep -E` and `-G` take POSIX regexes, and `\b` is not a POSIX construct. `-w` is git's own word-boundary match and does not depend on the regex engine. `-F` takes a fixed string. `-P` is optional at compile time, and git dies when it is absent. `[verified: /Library/Developer/CommandLineTools/usr/share/man/man1/git-grep.1:161-163, 187-201]`. On this machine, `git grep -nE "\b_mod\._model_rates\b" -- claude/.claude/scripts/tests` returns 0 lines. `git grep -nwF "_mod._model_rates"` over the same path returns hits, starting at `test_transcript_cost.py:292`. `git grep -nwF "_mod._model_rate"` returns nothing. `[verified: run by the planning session on this branch]`. So every grep whose result authorizes a deletion or passes a check first runs against a known-positive hit. The codemod uses Python `re`, which does not depend on git's regex backend.
27. `code-writer` never commits and leaves its changes unstaged. The plan commits nothing between dispatches. So `git diff --exit-code` fails on any file a dispatch already changed, and it cannot prove that a negative control was undone. `[verified: claude/.claude/agents/code-writer.md:21-22]`
28. The file-write worktree gate exits 0 for a path outside every git repository. `[verified: claude/.claude/hooks/require-worktree-for-file-writes.sh:130-132]`
29. The 26 files hold 1,783 lines that contain `_mod.`. 1,440 of those lines hold a `_mod.<package module>.<name>` read. In all 26, the `_mod = importlib.util.module_from_spec(_spec)` line sits five lines below the `_SCRIPT = ...` line. That matches the eight-line loader block read at `test_transcript_denials.py:13-20`, `test_transcript_pr_cost.py:36-43`, and `test_transcript_rearm_backtest_nudge_log.py:7-14`. `[verified: Grep counts and line numbers]`
30. On the unconverted tree, each of the 14 current and 15 appended TEST_FILES entries holds at least one `_mod.<module>.<name>` read. `test_transcript_shared_fixtures.py` reads `scope.config_dir`, `cost_ledger.config_dir`, `cost_ledger._cost_ledger_path`, `ledger_common.config_dir`, and `pr_cost_ledger.config_dir`, and each resolves to a top-level binding. So the non-vacuity and attribute checks pass before conversion. `[verified: Grep counts; test_transcript_shared_fixtures.py:4-24; scope.py:30-32; cost_ledger.py:29, 55; ledger_common.py:16; pr_cost_ledger.py:22]`
31. Among `tests/test_*.py`, only `test_transcript_shared_fixtures.py` imports from `transcript_analysis` without calling `spec_from_file_location`. `test_transcript_review_rounds.py`, `test_transcript_analysis_branch_attribution.py`, and `test_author_outcome.py` also import from the package, but each loads the shim. No test file has a `from transcript_analysis.<module> import` line. So a TEST_FILES twin of the by-module-only test passes today. `[verified: Grep of package imports and spec_from_file_location in tests/]`
32. All 40 shim-loading test files bind the shim as `_mod`. `evals/` has no `spec_from_file_location` call. `[verified: Grep]`. Hook tests reference the shim only as a path string, a symlink target, or a subprocess, in the hits read. `[verified for test_nudge_error_mode_analysis.py, test_nudge_transcript_toolkit.py, test_deny_private_project_refs.py; not exhaustive]`. Verification 1 runs the hooks tests, which backstops the rest.

**Mechanisms:**
- M1: Convert all 26 files in one pass of a throwaway codemod, using the recipe in Critical files. Then hand-fix the prose that the leftover greps surface. `anchors: row1, row3, row4, row5, row6, row7, row8, row9, row10, row11, row12, row25, row28, row29`

  Lighter options set aside:
  - Hand edits through `Edit`, split across three dispatches. That means 1,783 `_mod.` lines over 26 files, two of them longer than 1,600 lines (rows 25, 29). Three agents would each re-read the same recipe and could apply it differently.
  - `sed` one-liners. POSIX regexes have no lookbehind, and `sed` cannot compute each file's import line.
  - A refactoring library such as libcst. `requirements-dev.txt` declares none, and installing one needs the engineer's confirmation.

  The codemod uses only the Python standard library. It is never committed, so it gets no test file of its own. The dispatch gate checks its output directly.
- M2: Generalize the guard. `anchors: row11, row13, row14, row15, row16, row30, row31`
  - Extract the production check's resolver into a shared helper. The test-file check then resolves `<module>.<name>` the same way and keeps its `_mod` arm for the holdouts.
  - Split the absolute-import shapes out of `_unresolvable_sibling_imports`. Test files are then held to them without their relative `.conftest` imports being flagged.
  - Mirror the production by-module-only test over TEST_FILES.
  - Add a per-file non-vacuity assertion.
  - Add a completeness test: every loader-free test file that imports from the package must be in TEST_FILES.
  - List the 15 newly converted files and `test_transcript_shared_fixtures.py`.

  Alternatives set aside:
  - Deriving TEST_FILES by discovery would pull in the three hybrid files. `test_author_outcome.py:13`'s alias would fail the import-shape check. That forces either an edit outside the Ask or an exemption list, and an exemption list is just hardcoding again.
  - A hardcoded list alone leaves future conversions enforced only by doc prose. The completeness test closes that gap without discovery's exemptions, because it skips any file that still loads the shim.
  - Listing the files without generalizing the check leaves it vacuous for them (row 13).
  - Dropping converted files from TEST_FILES gives up the coverage the Context keeps.
  - Reusing `_unresolvable_sibling_imports` unchanged fails every test file (row 14).
- M3: Delete the shim passthroughs from row 17. Rewrite the comments that list them, and fix the docstring from row 19. `anchors: row17, row18, row19, row26, row32`

  Lighter options set aside:
  - Rewriting only the comments would leave `# noqa: F401` on imports nothing reads. CLAUDE.md § Ground every choice says: "add a one-line comment naming the alternative considered and why it does not apply. No rationale = no suppression."
  - Leaving the shim untouched would keep comments that describe reads which no longer exist. For example, `:11` cites `test_transcript_reviewer_yield.py`'s `_mod.hashlib`. CLAUDE.md § Durable text forbids that.

  CLI capture parity is not run. Removing a binding nothing reads cannot change output, and ruff F821 rejects any removed name the shim still reads.
- M4: Rewrite the two doc passages from row 20. `anchors: row20`
- M5: Three sequenced `code-writer` dispatches. `anchors: root, row25, row29`
  - The guard lands first, so the conversion gates on it. It passes on the unconverted tree through its `_mod` arm (row 30).
  - The conversion is one dispatch, because the codemod reads the files instead of the agent (M1).
  - The shim prune comes last, because its orphan set is computed from the converted tree.
  - Parallel dispatches were set aside. Each dispatch's output is the next one's input. They would also share this worktree and see each other's half-edited files in their pytest and ruff runs.
  - The recipe and table live once, in Critical files. Each prompt cites that block instead of restating it.
- M6: Test-ID parity through `test-ids` on explicit-file runs before and after. `anchors: row21, row22, row23`
- M7: Guard each negative control with a pre-control copy and `cmp`. `anchors: row13, row14, row16, row27, row28`
  - `git diff --exit-code` cannot prove the undo (row 27).
  - Committing after each dispatch was set aside. CLAUDE.md § Code Review requires `/code-review` before each commit. Three commits would mean three review rounds, which triggers the third-round consult that § Model & Effort Routing describes.
  - Restoring with `git stash` or `git checkout` was set aside. The stash stack is shared across worktrees and sessions. `git checkout -- <file>` would discard a dispatch's uncommitted work.

## Critical files

Paths are under `claude/.claude/scripts/tests/` unless shown in full. In commands, `<the 26 files>` expands to full repo-relative paths. `<venv>` is the main worktree root's `.venv`, which is `../../../../.venv` from this worktree's root (row 24). `<scratch>` is the directory the parent creates with `mktemp -d -t loader-sweep` before Verification 0. It lies outside every git repository, so the worktree gate allows writes there (row 28). The parent names its absolute path in every dispatch prompt. No dispatch writes a ticket-prefixed identifier, or a tracker ID followed by a phase qualifier, into `.py` text (G3).

**File limit.** The diff touches exactly these 30 files:
- `test_transcript_package_module_prefixes.py` (dispatch 1)
- the 26 test files listed under dispatch 2
- `claude/.claude/scripts/transcript-analysis.py`, `test_transcript_analysis_cost_import_direction.py`, and `docs/transcript-analysis-architecture.md` (dispatch 3)

The codemod lives in `<scratch>` and is never committed. `test_transcript_shared_fixtures.py` is named in TEST_FILES, but the diff does not edit it.

**Dispatch 1: `code-writer`, the guard.** It owns only `test_transcript_package_module_prefixes.py`. Use exactly the test names below, because Verification 2 lists them.
- Extract `_package_module_attribute_reads(tree, package_names)` from `test_production_modules_reference_only_real_sibling_attributes` (:249-261, the attribute-walk loop; the function itself spans :231-261). It returns each `<module>.<attr>` Attribute node, with its module, whose base Name is bound by `from transcript_analysis import <module>`. The production test keeps its Store assertion (:254-257).
- Move the absolute shapes out of `_unresolvable_sibling_imports` (:121-150) into `_unresolvable_absolute_package_imports(tree, package_names)`. Those shapes are the aliased `from transcript_analysis import <module> as <alias>` and every `import transcript_analysis[.<module>]`. `_unresolvable_sibling_imports` returns its relative-import entries plus that helper's result, so its behavior does not change. Leave the existing self-test at :206-228 unchanged.
- Add `test_unresolvable_absolute_package_imports_flags_only_absolute_shapes`. It asserts the new helper returns `[]` for `from .conftest import x`, `from . import scope`, and `from transcript_analysis import scope`. It also asserts the helper flags `from transcript_analysis import scope as s` and `import transcript_analysis.scope`.
- Rename `test_test_files_reference_only_real_module_attributes_through_mod` to `test_test_files_reference_only_real_module_attributes`. It checks every read the shared helper returns, plus every `_mod.<module>.<name>` read (today's arm, :273-286).
- Add `test_test_files_bind_package_modules_only_by_unaliased_from_import`. It asserts that `_unresolvable_absolute_package_imports` returns nothing for any TEST_FILES file, mirroring :192-203.
- Add `test_test_files_import_package_modules_by_module_only`. It asserts that no TEST_FILES file has a `from transcript_analysis.<module> import ...` line, mirroring :173-189.
- Add `test_test_files_each_have_a_resolvable_module_attribute_read`. Each TEST_FILES file must yield at least one read of either shape, and the failure message names the file.
- Add `test_loader_free_package_importing_test_files_are_in_test_files`.
  - A file is package-importing when it has an `import transcript_analysis[.<module>]`, or an absolute `from transcript_analysis[.<module>] import`.
  - A file is loader-free when it has no call to `spec_from_file_location`, whether through an attribute or a bare name.
  - Every `TESTS_DIR/test_*.py` that is both must appear in TEST_FILES, and the failure message names each missing file.
  - The test also asserts that the set it computed is non-empty.
- Append to TEST_FILES the 15 newly converted files: `test_transcript_` + `audit_routing`, `audit_routing_samples`, `audit_routing_shape`, `cache_rebuild_attribution`, `cache_rebuild_switch_delta`, `cache_rebuild_ttl_accumulation`, `cache_rebuild_ttl_footing`, `denials`, `gh_cli`, `pr_cost`, `pr_cost_export_accounts`, `pr_cost_ledger`, `read_scope`, `review_trace`, `reviewer_yield`. Also append `test_transcript_shared_fixtures.py`.
- Rewrite the module docstring (:1-8) so it states both read shapes and the completeness rule.
- The file must pass on the unconverted tree (row 30). If a check flags a listed file, report it. Do not edit that test file or loosen the check.
- Gate: `<venv>/bin/pytest claude/.claude/scripts/tests/test_transcript_package_module_prefixes.py` and `<venv>/bin/ruff check claude/.claude/scripts/tests/test_transcript_package_module_prefixes.py`.

**Parent, after dispatch 1:** Verification 4(a), 4(d), and 4(e).

**Dispatch 2: `code-writer`, convert all 26 files.** The files are `test_transcript_` + `audit_routing`, `audit_routing_samples`, `audit_routing_shape`, `cache_rebuild_attribution`, `cache_rebuild_switch_delta`, `cache_rebuild_ttl_accumulation`, `cache_rebuild_ttl_footing`, `cost_ledger`, `cost_ledger_record_gates`, `denials`, `gh_cli`, `handoff_nudge`, `handoff_signal_response_detection`, `pr_cost`, `pr_cost_export_accounts`, `pr_cost_ledger`, `read_scope`, `rearm_backtest`, `rearm_backtest_nudge_log`, `review_trace`, `reviewer_yield`, `spend_over_threshold`, `subagent_mix`, `subagent_mix_dollars`, `subagents`, `workstream_cost` (each `.py`).

Write `<scratch>/loader_sweep_codemod.py` using only `ast`, `re`, `pathlib`, and `sys`. Run it with `<venv>/bin/python3` over the 26 files. For each file, it computes the new text in memory and writes the file only when every check passes:
1. Delete the loader block: the `_SCRIPT = ...` line, the three-line comment under it, and the `_spec`, `_mod`, `sys.path.insert`, and `exec_module` lines. Exit nonzero, naming the file, unless that eight-line block occurs exactly once (row 29).
2. Rewrite `_mod.<module>` to `<module>` for every package module, in code and prose alike. The package modules are the stems of `transcript_analysis/*.py`, except `__init__`. Match with Python `re`, using `(?<![\w.])_mod\.` before the module name and `\b` after it.
3. Rewrite each remaining `_mod.<name>` per the flat-name table, with the same boundaries.
4. Parse the result. Exit nonzero, naming the file and line, if any `ast.Name` `_mod` remains. That catches a flat name that is in neither the table nor the module list.
5. Insert these lines after the last top-level import statement:
   - one unaliased `from transcript_analysis import ...` that names exactly the modules steps 2–3 introduced
   - `import _config`, if step 3 rewrote `_mod._config`
   - each stdlib binding step 3 introduced that the file does not already bind at top level
6. Print, for each file, the flat names step 3 rewrote.

Then the agent does the following:

7. Compare step 6's output with the expected flat rewrites below. Report any difference before going further.
8. Run `<venv>/bin/ruff check --fix` on the 26 files, then `<venv>/bin/ruff check` on them. The second run must be clean. Remove by hand any F401 import that `--fix` leaves (row 12).
9. Fix prose by hand. First run `git grep -nwF _mod -- claude/.claude/scripts/tests/test_transcript_cost.py` and require at least one hit (row 26). If it returns nothing, stop and report. Then check each line that `git grep -nwF _mod -- <the 26 files>` or `git grep -nF "transcript-analysis.py" -- <the 26 files>` returns. Where a comment or docstring describes `_mod` or the shim loader, rewrite it to name the package module directly. Delete text that only explained the loader. For example, the phrase "patching _mod's binding" in `test_transcript_cost_ledger_record_gates.py:150` becomes "patching `cost_ledger.config_dir`".

Never alias a package-module import, import a package name directly, or edit a file outside the 26. The self-review reads every import-block hunk, every hand-edited prose hunk, and every hunk `ruff check --fix` introduces (step 8) in full — the `UP`/`SIM`/`B` rule groups can autofix anywhere in a file, not only the import block or the prose the recipe targets. The gate checks the rewritten reads mechanically.

**Flat-name table:**
- `corpus.`: `_parse_ts`, `_read_session_file_partitioned`, `SUBAGENT_SUBDIR`
- `pricing.`: `_model_rates`, `_price_turn`
- `scope.`: `_DO_NOT_PUBLISH_BANNER`, `_resolve_project_scope`, `_redaction_ordinals`, `_parse_absolute_window_args`
- `_mod._resolve_scan_roots` becomes `scope.resolve_scan_roots`. The shim re-exports it under the underscored alias.
- `cost.`: `_cost_report`, `_compute_workstream_dollars`, `_compute_pr_cost_branch_totals`
- `redaction.`: `_REDACT_MAP_MISS_TOKEN`
- `reviewer_yield.`: `cmd_reviewer_yield`, `_is_reviewer_subagent_type`, `_extract_cited_paths`, `_CITED_PATH_CANDIDATE_MAX_CHARS`, `_normalize_cited_path`, `_build_tool_result_ts_map`, `_index_session_edits`, `_reviewer_yield_cited_keys`, `_dispatch_self_reference_keys`
- `_mod._config.<x>` becomes `_config.<x>`, with `import _config`.
- Stdlib: `_mod.hashlib`, `_mod.os`, `_mod.subprocess`, `_mod.datetime`, and `_mod.UTC` become the bare name. `datetime` is the class (row 6), so the binding to add is `from datetime import UTC` or `from datetime import datetime`, never `import datetime`.

**Expected flat rewrites** (each file is `test_transcript_`-prefixed; the 9 files from row 4 have none):
- `audit_routing` (`_REDACT_MAP_MISS_TOKEN`)
- `cost_ledger` (`_resolve_project_scope`, `datetime`, `UTC`)
- `cost_ledger_record_gates` (`_config`, `subprocess`)
- `pr_cost` (`_resolve_project_scope`, `_compute_pr_cost_branch_totals`, `_cost_report`, `_config`, `os`)
- `pr_cost_export_accounts` (`_config`)
- `read_scope` (`_DO_NOT_PUBLISH_BANNER`, `_read_session_file_partitioned`, `_parse_ts`)
- `review_trace` (`_parse_absolute_window_args`, `_parse_ts`)
- `workstream_cost` (`_resolve_project_scope`, `_compute_workstream_dollars`, `_compute_pr_cost_branch_totals`, `_parse_ts`)
- `cache_rebuild_attribution` (`_parse_ts`)
- `handoff_nudge` (`_model_rates`, `_parse_ts`)
- `handoff_signal_response_detection` (`_price_turn`)
- `rearm_backtest` (`_cost_report`, `_model_rates`, `_redaction_ordinals`, `_DO_NOT_PUBLISH_BANNER`)
- `reviewer_yield` (all nine reviewer_yield names, `_parse_ts`, `_resolve_scan_roots`, `_resolve_project_scope`, `SUBAGENT_SUBDIR`, `hashlib`)
- `spend_over_threshold` (`_model_rates`)
- `subagent_mix` (`SUBAGENT_SUBDIR`, `_DO_NOT_PUBLISH_BANNER`)
- `subagent_mix_dollars` (`_cost_report`, `_parse_ts`)
- `subagents` (`_DO_NOT_PUBLISH_BANNER`)

**Gate for dispatch 2:**
- `<venv>/bin/pytest -m "not timing" <the 26 files> claude/.claude/scripts/tests/test_transcript_package_module_prefixes.py`
- `<venv>/bin/pytest -m timing -n0 claude/.claude/scripts/tests/test_transcript_reviewer_yield.py`
- `<venv>/bin/ruff check <the 26 files>`
- From Verification 3: the sanity check, the `_mod`/`_SCRIPT`/`_spec` check, the `spec_from_file_location` check, and the setattr-count check.

**Parent, after dispatch 2:** Verification 4(b), 4(c), 4(f), and 4(g).

**Dispatch 3: `code-writer`, shim prune and docs.** It owns these three files:
- `claude/.claude/scripts/transcript-analysis.py`:
  - First run `git grep -nwF "_mod._model_rates" -- claude/.claude/scripts/tests`. Its output must include the hits at `test_transcript_plan_boundary.py:135, 167, 182`. Then run `git grep -nwF "_mod._model_rate" -- claude/.claude/scripts/tests`. It must return nothing. If either check fails, stop and report, and delete nothing (row 26).
  - Delete each row-17 passthrough for which `git grep -nwF "_mod.<name>" -- claude/.claude/scripts/tests` returns nothing. The grep decides. Row 17 forecasts that every row-17 passthrough goes, so report any that survive.
  - Rewrite the three comments that list the bindings (:28-34, :88-92, :220-223) so they name only what survives. Add a one-line comment next to the surviving passthrough list: reverting the 26 files' conversion without also restoring these pruned bindings breaks collection, because their only remaining readers are the (now-converted) 26 files. Make no other edits to the shim.
- `test_transcript_analysis_cost_import_direction.py`: drop `_compute_workstream_dollars` from the parenthetical at :82-83. Leave :8-12 as it is (row 19).
- `docs/transcript-analysis-architecture.md`:
  - Replace the pr-cost-family loader sentences at :471-476 with one passage that governs all test files. It states two facts:
    - A test file that reads only package-resident names imports those modules directly, unaliased, and reads `<module>.<name>`.
    - A test file that reads a name only the shim has (`build_parser`, `main`, or a `cmd_*` the shim defines) loads its own copy of `transcript-analysis.py` via `spec_from_file_location`. It reaches package modules as `_mod.<module>.<name>`. Keep the existing pointer to the shim-reimport exception on this second shape.
  - At :535-538, state four facts:
    - The guard checks both read shapes in every TEST_FILES file.
    - It requires at least one read per file.
    - It holds listed files to unaliased whole-module imports.
    - It fails any test file that imports from `transcript_analysis` without loading the shim and is missing from TEST_FILES.
- Gate:
  - `<venv>/bin/ruff check claude/.claude/scripts/transcript-analysis.py claude/.claude/scripts/tests/test_transcript_analysis_cost_import_direction.py`
  - Run `<venv>/bin/pytest -m "not timing"` on the 14 holdouts plus `test_transcript_cli_bootstrap.py`, `test_transcript_analysis_cost_import_direction.py`, and `test_transcript_analysis_architecture_doc.py`. Then run `-m timing -n0` on the same list. The 14 holdouts are `test_context_composition.py`, `test_author_outcome.py`, and `test_transcript_` + `analysis`, `analysis_branch_attribution`, `cache_rebuild`, `cache_rebuild_ttl_rules`, `cost`, `cost_counts`, `handoff_signal_response`, `ledger_common`, `plan_boundary`, `pr_cost_export`, `pr_cost_gh`, `review_rounds`.

**Parent, after dispatch 3:** Verification 1, 2, 3, and 5.

## Verification

Run every step from the worktree root. `<files>` means the 26 files plus `test_transcript_package_module_prefixes.py`, listed explicitly by full path.

0. **Baseline (parent, before dispatch 1).**
   - Create `<scratch>` with `mktemp -d -t loader-sweep`.
   - Run `<venv>/bin/pytest -m "not timing" <files> --junitxml=<scratch>/before-parallel.xml`, then `<venv>/bin/pytest -m timing -n0 <files> --junitxml=<scratch>/before-timing.xml`. Every test passes. The run uses an explicit file list because `select-tests.py` selects nothing while the committed plan is the only diff (row 21).
   - Run `grep -cF "monkeypatch.setattr(" <the 26 files> > <scratch>/before-setattr.txt`. The line for `test_transcript_pr_cost.py` must be nonzero, which proves the count is not vacuous.
1. **Scoped suite on the final tree.** Run `<venv>/bin/python3 claude/.claude/scripts/select-tests.py -m "not timing"`, then `<venv>/bin/python3 claude/.claude/scripts/select-tests.py -m timing -n0`.
   - Every selected test passes.
   - The `select-tests:` line names `claude/.claude/scripts/tests`, plus the hooks and skills test directories that the docs change selects (row 21).
   - The run includes `test_transcript_package_module_prefixes.py`, `test_transcript_analysis_cost_import_direction.py`, `test_transcript_analysis_architecture_doc.py`, and `test_transcript_cli_bootstrap.py`.
2. **Test-ID parity.** Re-run step 0's two pytest commands into `<scratch>/after-parallel.xml` and `<scratch>/after-timing.xml`. Then run `<venv>/bin/python3 claude/.claude/scripts/tests/_move_fidelity.py test-ids --before <scratch>/before-parallel.xml --before <scratch>/before-timing.xml --after <scratch>/after-parallel.xml --after <scratch>/after-timing.xml --file-prefix claude/.claude/scripts/tests/ --rename test_test_files_reference_only_real_module_attributes_through_mod=test_test_files_reference_only_real_module_attributes --expect-new test_unresolvable_absolute_package_imports_flags_only_absolute_shapes test_test_files_bind_package_modules_only_by_unaliased_from_import test_test_files_import_package_modules_by_module_only test_test_files_each_have_a_resolvable_module_attribute_read test_loader_free_package_importing_test_files_are_in_test_files`. It reports no findings. Several identically-named test functions exist across the 26 files (e.g. `test_since_malformed_value_exits_nonzero_with_subcommand_in_message` in three of them); `_move_fidelity.py` keys on name, not file, so an `id-missing`/`id-unexpected` finding for one of these names does not say which file lost or gained it — read the full set of occurrences for that ID, not just the count.
3. **Leftovers.**
   - Sanity check first. `git grep -nwF -e _mod -e _SCRIPT -e _spec -- claude/.claude/scripts/tests/test_transcript_cost.py` must return hits. So must `git grep -nF spec_from_file_location -- claude/.claude/scripts/tests/test_transcript_cost.py`. If either returns nothing, the next two checks prove nothing, so stop (row 26).
   - `git grep -nwF -e _mod -e _SCRIPT -e _spec -- <the 26 files>` returns nothing.
   - `git grep -nF spec_from_file_location -- <the 26 files>` returns nothing.
   - Run `grep -cF "monkeypatch.setattr(" <the 26 files> > <scratch>/after-setattr.txt`, then `diff <scratch>/before-setattr.txt <scratch>/after-setattr.txt`. The diff is empty.
   - Each hit of `git grep -nF "transcript-analysis.py" -- <the 26 files>` describes the CLI, never a loader.
   - For each name dispatch 3 deleted from the shim, `git grep -nwF "_mod.<name>" -- claude/.claude/scripts/tests` returns nothing. For each noqa'd binding still in the shim whose comment cites test-file reads, the same grep returns at least one hit.
   - `git status --porcelain` lists only paths in Critical files' file limit, plus this plan while it is uncommitted. It shows no other untracked path.
4. **Negative controls.** Before each control, run `cp <file> <scratch>/control-<letter>.pre-control`. Edit the file by hand and run only `<venv>/bin/pytest claude/.claude/scripts/tests/test_transcript_package_module_prefixes.py`. Then restore with `cp <scratch>/control-<letter>.pre-control <file>`. `cmp <file> <scratch>/control-<letter>.pre-control` must exit 0. Never use `git stash` or `git checkout` (M7).
   - (a) After dispatch 1: at `test_transcript_denials.py:29`, change `_mod.denials._drop_denial_command_flag_values` to `..._valuez`. Only the renamed attribute test fails, and it names the read.
   - (d) After dispatch 1: append `"test_transcript_cli_bootstrap.py"` to TEST_FILES. Only the non-vacuity test fails, and it names that file.
   - (e) After dispatch 1: at `claude/.claude/scripts/transcript_analysis/workstream_cost.py:56`, change `scope.resolve_scan_roots` to `scope.resolve_scan_rootz`. Only `test_production_modules_reference_only_real_sibling_attributes` fails, and it names the read. This control proves the production check still works after the resolver extraction.
   - (b) After dispatch 2: make the same edit as (a) to the now-direct `denials._drop_denial_command_flag_values` read. Find it with `git grep -nF`. Only the renamed attribute test fails, and it names the read.
   - (c) After dispatch 2: add `from transcript_analysis import scope as scope_alias` to `test_transcript_denials.py`. Only the unaliased-import test fails.
   - (f) After dispatch 2: add `from transcript_analysis.scope import resolve_scan_roots` to `test_transcript_denials.py`. Only the by-module-only test fails.
   - (g) After dispatch 2: remove `"test_transcript_denials.py"` from TEST_FILES. Only the completeness test fails, and it names that file.
   - After all seven controls, re-run step 3's `git status --porcelain` check. It must still list only the Critical files' file limit paths plus this plan. A failed or interrupted restore from any control would otherwise ship undetected, since step 3's own run already happened before these controls.
5. **Lint.** `<venv>/bin/ruff check claude/.claude/ claude-skills/` is clean.

## Out of scope

- **Converting the 14 holdout files, or moving `build_parser`/`main` out of the shim** (G1, row 2).
- **Checking the hybrid files' direct reads.** `test_transcript_analysis_branch_attribution.py`, `test_transcript_review_rounds.py`, and `test_author_outcome.py` read package modules directly but still load the shim. The completeness test skips them, so no check covers those reads. Once one of them drops its loader, the completeness test requires it in TEST_FILES. At that point, `test_author_outcome.py:13`'s alias must change.
- **Checking string attribute names in `monkeypatch.setattr(<module>, "<name>", ...)`, or forbidding `<module>.<name> = ...` assignments in test files.** The guard checks attribute reads only, before and after this PR.
- **`README.md:523`'s claim that linked worktrees sit exactly three levels deep.** A branch name containing `/` puts this worktree four levels deep (row 24). Observed here, not fixed here.
- **Records left untouched:** `.claude/plans/decomposition-prep.md` (including its `:385` note about the record-gates docstring, which this sweep makes false), `.claude/plans/transcript-analysis-decomposition.md`, and the tracker.
