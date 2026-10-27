# Shim-reader retarget, PR 2 (GH-1116)

## Context

Goal: retarget the 12 remaining shim-loading test files to read package modules directly, add a pin test that fixes the shim copy's allowed reads to `build_parser` and `main`, and delete the shim's remaining test-only bindings, in one test-only PR.

Ask: "Yes, start." — the engineer's answer to "Do you want me to start PR 2 now?" The standing Ask from the parent plan `.claude/plans/shim-reader-retarget.md` carries: "Don't get caught up on semantics. Do what is correct architecturally and ask staff sdet for guidance".

Why now: PR 1 (#1350, squash `9aba41d7`) retargeted `test_transcript_analysis.py` and deleted 39 shim bindings. PR 2 is the second half of that plan and must be cut from a base containing PR 1. After it, the shim re-exports only what `build_parser` and `main` read, which unblocks the `cli.py` phase.

Intended outcome: the 12 files import the package modules they exercise and keep `_mod.` only for `main` and `build_parser` (or the documented `author_outcome as ao` exception); the shim loses the 45 test-only bindings that Ruff F401 `--ignore-noqa` reports at this base; collected node IDs and test outcomes are unchanged; and a new pin test fails if any shim-loading test file reads anything else through its shim copy. One further test, in `test_author_outcome.py`, asserts the `author-outcome` subcommand dispatches to `cmd_author_outcome`.

Inputs at base `9aba41d7`, recounted this session (the parent plan's PR 2 totals are stale):
- 973 non-`build_parser`/`main` reads in the 12 files, against the parent plan's 965. `test_transcript_cost.py` has 396 (plan 389) and `test_transcript_pr_cost_export.py` has 63 (plan 62).
- 45 F401 `--ignore-noqa` candidates in the shim, matching the plan's 45.
- Line anchors in the parent plan are stale. Edits anchor on content.

Carried from the parent plan and PR 1's review: the Out-of-scope DEFER bullet on PR 2's verification-recipe refinements (a first-set-only pin control, a wrap-tolerant prose grep, a call-form fixture checker control), the three staff-sdet notes (no seeded control for the checker's fixture clause; the row-36 citation also rests on rows 22 and 29; Approach line 15 says 83 shim import bindings against 84 at that base), and the DEFER that the shim-reads-only-two-names claim is unpinned until this PR's pin test.

## Approach

PR 2 finishes the parent plan's design at base `9aba41d7`, in two commits. Commit 1 retargets every `_mod.<name>` read in the 12 files, other than `build_parser` and `main`, to the owning module's own binding, and a forward replay proves it a pure rename. Commit 2 deletes the shim's 45 test-only bindings, fixes the prose they leave false, adds seven files to `TEST_FILES`, and adds a pin test that limits every shim copy to `build_parser` and `main`. No production code changes, CLI help and parser dumps stay byte-identical, and collected test IDs and outcomes stay the same except for two new IDs, the pin and the `author-outcome` wiring test.

PR 2 reuses PR 1's replay checker after re-running PR 1's controls against it, and adds five controls of its own. It adopts all three deferred recipe refinements: pin control (f), the wrap-tolerant doc Grep in Verification 8, and checker control (p).

**Commit 1, the rename (dispatch R2).**
- **Table.** It is derived from the shim's top-level imports at HEAD, never from a diff or a grep:
  - `import X` maps `_mod.X` to `X`.
  - `from transcript_analysis import M` maps `_mod.M` to the file's binding of M.
  - `from transcript_analysis.M import O` maps `_mod.O` to `<M's binding>.O`. No aliased import is left in the shim at this base, so every leaf keeps its name.
  - The table is not injective: `_mod._price_turn` and `_mod.pricing._price_turn` both become `pricing._price_turn` (row 17).
- **Sites.** Only `ast.Attribute` nodes whose value is the Name `_mod` are rewritten, and `build_parser` and `main` are skipped (G1, row 4). Strings, comments, docstrings, and `setattr(_mod, "…")` arguments stay as they are.
- **Bindings.**
  - Each file gets one unaliased `from transcript_analysis import <m>` statement, or an extension of its existing unaliased one (review_rounds :15, author_outcome :14).
  - `test_author_outcome.py` reuses `ao` for `author_outcome` (row 7).
  - `_mod.os` and `_mod.tempfile` become `os` and `tempfile`, so `test_transcript_ledger_common.py` gains `import os` and `import tempfile`.
- **period_cost.** Its six alias statements (:45-50) go, and their loads then read the module binding directly. `cost_module` becomes `cost` at its two loads (:2556, :2940). `render_module` has no load, so it adds no `render` import (row 8).
- Assertion lines change only by these proven re-points. The one assertion whose strength changes is review_rounds :3532's `args.func` comparison, which now compares against the owning module (row 13).

**Commit 2, the semantic changes (dispatch D2).**
- **The shim**, `claude/.claude/scripts/transcript-analysis.py`, loses row 5's 45 bindings:
  - Delete the `import os` and `import tempfile` lines.
  - Replace the whole-module block and its comment (:13-41) with `from transcript_analysis import scope`. Add no comment, because `main()`'s own use of `scope.PROJECTS_DIR` shows why the import exists.
  - In the `transcript_analysis.cost` block, delete the seven noqa'd names and the four comment lines. Add no comment, because `build_parser`'s `set_defaults` calls show why `cmd_cost` and `cmd_cost_trend` stay.
  - Delete the `transcript_analysis.pricing`, `.redaction`, and `.render` blocks.
  - In the `transcript_analysis.scope` block, delete the four noqa'd names. Its three bare-read names stay.
  - In `main()`'s comment (:1044), change `(_resolve_cost_roots)` to `(scope._resolve_cost_roots)`. After the deletion the shim no longer binds the bare name (row 6).
  - The shim then holds no `noqa` (row 19).
- **`test_author_outcome.py`.** Add `test_author_outcome_dispatches_to_cmd_author_outcome` to `TestBuildParserWiring`, asserting `_mod.build_parser().parse_args(["author-outcome"]).func is ao.cmd_author_outcome`. It carries no docstring, because its name shows what it checks. No test asserted that `author-outcome` dispatches to `cmd_author_outcome`: the three wiring tests check defaults, flag exclusion and flag parsing, and the retargeted reads call the command directly. The engineer selected "Add pin in commit 2 (Recommended)" on the review question that proposed this test.
- **The other 11 test files.** D2 edits none of them. The checker's `--prose-only` mode reports `prose: 0` for each at this base, so no docstring or comment in them spells a retargeted read. Verification 3 and 8 keep that check as a regression guard, and a non-zero count stops the run.
- **`test_transcript_package_module_prefixes.py`.**
  - Append seven files to `TEST_FILES`, in this order: `test_transcript_cost.py`, `test_transcript_review_rounds.py`, `test_transcript_cache_rebuild_ttl_rules.py`, `test_transcript_ledger_common.py`, `test_transcript_pr_cost_gh.py`, `test_transcript_pr_cost_export.py`, and `test_transcript_cache_rebuild.py`. `TEST_FILES` goes from 52 entries to 59. `test_author_outcome.py` stays off, because its `ao` alias fails the unaliased-import check (row 9).
  - Extract the per-call test inside `_calls_spec_from_file_location` as `_is_spec_from_file_location_call(node)`. That helper and the pin both use it.
  - Add the pin, below.
  - In the module docstring, change "Scope stays those modules and test files" to "Scope stays those modules, those test files, and the shim-loading test files". Add no sentence describing the pin, because its name and body show what it checks. Rewrap to the docstring's existing width.
- **`test_transcript_analysis_cost_import_direction.py`.** The docstring's last sentence, from "This guard pins the production import-direction exception only" to its end, becomes "This guard pins the production import-direction exception only, not test files' own `from transcript_analysis import cost` imports, which reach cost.py's private helpers for patching."
- **`test_transcript_cli_bootstrap.py` :9.** "no `_mod.cmd_*`-style test exercises" becomes "no in-process test exercises".
- **`docs/transcript-analysis-architecture.md`** (row 18). Each edit anchors on content and rewraps only the lines it touches.
  - Delete the sentence "The shim also binds package modules, leaf names, and the stdlib `os` and `tempfile` that shim-loading test files read through their copy." (:57-58).
  - Replace the two sentences from "`tests/test_transcript_analysis.py` reads only those two names" through "or as a leaf name the shim re-exports." (:788-791) with "It reads only those two names through its copy and imports every package module it reads directly." The parent plan's draft of this sentence ended "as above", which points at the unaliased form and so misdescribes `ao`.
  - In the `author_outcome.py` sentence (:799-802), "(`from transcript_analysis import author_outcome`)" becomes "(`from transcript_analysis import author_outcome as ao`)". "reserved for the argparse-wiring and `cmd_author_outcome` end-to-end tests." becomes "reserved for the argparse-wiring tests, which read `build_parser`." (row 4).
  - After "a permanent check rather than a one-time migration-time script." (:865-866), add "It also requires every test file that loads a shim copy to read only `build_parser` and `main` through it."

**The pin (M6).** Add one test to `test_transcript_package_module_prefixes.py`, named `test_shim_copy_reads_only_build_parser_and_main`. Its docstring is one line stating only what the test name does not: "Only the two names the cli.py phase retargets may be read through a copy; any other read couples a test to a shim binding, even one production code also reads." One code comment above the skip rules states the two exemptions: "Function-scope scope.py loads are skipped, and exec_module(copy) is the one allowed bare use of the copy." D2 writes these two lines and no other docstring or comment (row 26).
- **First set.** It holds every `TESTS_DIR.glob("test_*.py")` file that makes a `spec_from_file_location` call outside any function, lambda, or class body, with the string `"transcript_analysis"` as its first argument.
  - The file's copy is the Name target of its module-level assignment from a `module_from_spec(...)` call. A selected file with no such assignment fails, named.
  - The rule skips `test_transcript_analysis.py`'s function-scope `scope.py` loads (row 2).
- **Second set.** It holds every such file whose module-level `spec_from_file_location` call takes a path argument containing the string constant `"transcript-analysis.py"`, either directly or through a Name that a module-level assignment binds to a value containing it.
- The two sets must be equal and non-empty. The failure names each file that is in only one of them. Both hold the same 13 files today (row 2).
- In each first-set file, every `ast.Attribute` whose value is the copy's Name is a Load of `build_parser` or `main`. Every other Load of the Name is the sole argument of an `exec_module(...)` call.
- Each first-set file makes at least one allowed read, so the pin cannot pass vacuously on any one file.
- A failure names the file, the line, and the attribute or use.

**Recipe decisions.**
- **All three deferred refinements are adopted, as one check each.**
  - Pin control (f) puts a file in the first set only. Controls (d) and (e) exercise only the second set and the per-file read assertion. Without (f), a pin that checked only one direction of the set equality would pass every control.
  - Verification 8's multiline Grep over the architecture doc replaces the parent plan's `_mod\.` grep of it, which the new pattern subsumes. It also catches a phrase-level claim such as ", which calls into the shim", which PR 1's edit list missed. A `git grep` cannot match a phrase across a line wrap. Three expected matches keep the check cheap (row 18).
  - Checker control (p) seeds a patch inside a call-form `@pytest.fixture(...)`. That is the fixture-clause arm PR 2's files reach, and no control covered it (rows 14, 15). A bare-decorator control was set aside, because no PR 2 file uses that form.
- **The checker is reused behind an integrity run.** Verification 0 reruns PR 1's controls (a)-(k) on PR 1's saved copies with `<toolbox>/verify2/controls.py`. That one command proves the checker as it stands now (row 14).
  - Trusting the file without the rerun was set aside. No hash of it was recorded, and `<toolbox>` is mutable and outside the repository.
  - A fresh checker for PR 2 was set aside. It costs one more dispatch and eleven re-anchored controls, with no evidence that the existing checker is wrong.
  - If `<toolbox>` or either file is gone, or the rerun deviates, K is dispatched again (Critical files), and controls (a)-(k) also run on PR 2's copies. K's spec stays in the parent plan's Verification step 2 rather than being copied here, because only this contingency needs it.
  - PR 2's own controls (l)-(p) run every time. Only PR 2's files carry top-level alias statements, an aliased package binding, a missing stdlib import, and call-form fixtures (rows 7, 8, 15).
- **A scratch runner seeds the controls, not Write-tool copies.** `test_transcript_period_cost.py` reaches line 5,951 and `test_transcript_review_rounds.py` line 5,412, so writing each seeded copy whole would cost that text per control. A runner modeled on `<toolbox>/verify2/controls.py` asserts that each substitution lands where intended. PR 1 ran its controls the same way (row 14).
- **Counts are checked, not carried.** Context's figures are a snapshot. Verification 3's `replaced` recounts each file's reads, Verification 6's ruff run recounts the deletion set, and `test-ids` recounts the tests. A mismatch with a snapshot figure stops the run, and the parent reports both numbers. The PR body states only recounted figures.
- **E501.** R2 stops and reports any E501 instead of reflowing. The parent then reflows only that line, inside its brackets, by hand, and reruns Verification 3. The replay ignores source positions, so the reflowed line stays provable (row 14).
- **The `args.func` statement is narrower than the parent plan's.** The parent said the assertion "could not fail". It could fail if `set_defaults` wired a different shim name. What it could not catch is the shim binding the wrong object under the right name, so the PR body says that (row 13).
- **`test_author_outcome.py` reuses `ao` and stays off `TEST_FILES`.** `ao` is already the file's own binding. Unaliasing it is left to the `cli.py` phase (Out of scope).

**Carried items.** Context's carried notes resolve as follows:
- The checker's fixture clause had no seeded control: control (p) is that control.
- The parent's row-36 citation also rested on its rows 22 and 29: this plan's pin selection rests on row 2's grep, which covers both.
- The parent's Approach said 83 shim bindings against 84 at that base: PR 2 relies on neither. Its figure is row 5's 45, read from the shim at this base.
- The claim that shim copies read only two names was unpinned: the pin pins it.

**Assumption ledger.**

**Root:** At base `9aba41d7`, 12 test files load `transcript-analysis.py` as `_mod` and make 973 reads of names other than `build_parser` and `main` through it. To serve them, the shim keeps 45 import bindings that its own code never reads, each under `# noqa: F401`. PR 2 retargets those reads to the owning modules, deletes the 45 bindings, and pins every shim copy to `build_parser` and `main`. The `cli.py` phase then inherits only those two names' reads. `[unverified: 973 is this session's relayed AST recount; Verification 3 recounts]` `[verified for 45: read of the shim, rows 5 and 6]`

**Givens:**
- G1. The shim keeps `build_parser()` and `main()` until the `cli.py` phase, so every loader stays. Reason: changing this needs a decision outside this plan, the governing plan's phase order. `[verified: transcript-analysis-decomposition.md:155-161]`
- G2. Agents run `select-tests.py` rather than the full suite, outside CLAUDE.md's two named cases. Reason: changing this needs an edit to CLAUDE.md § "Commands". `[verified: CLAUDE.md "Commands"]`
- G3. A monkeypatch reaches code only through the binding that code reads at call time, and a by-name import snapshots the binding at import time. Reason: Python import semantics. `[unverified — relayed from the parent plan's G2]`
- G4. This machine runs the suite with one xdist worker. A two-worker run was killed for low memory, and the one-worker selected run took 1h21m. Reason: machine memory lies outside the plan's reach. `[unverified — relayed from this session's PR 1 runs]`
- G5. Four `test_shellcheck.py` failures reproduce at the merge-base, because the system `shellcheck` is 0.9.0 against the `shellcheck-py==0.11.*` pin. Reason: fixing it means installing software, which needs the engineer. `[verified: requirements-dev.txt:5 for the pin]` `[unverified — relayed: the version and the reproduction; Verification 8 reproduces them again]`

**Rows:**
1. The engineer started PR 2: `[engineer-verified: "Yes, start."]` The recipe decisions above are this plan's own calls, under the delegation the `Ask:` line carries. `[unverified]`
2. Loaders.
   - Exactly 13 calls under `claude/.claude/` pass the spec name `"transcript_analysis"` to `spec_from_file_location`. One sits at module level in each of the 12 files and in `test_transcript_analysis.py` (:102). Each passes `_SCRIPT` and binds the copy as `_mod` through `module_from_spec`.
   - `_SCRIPT` is bound at module level to `Path(__file__).parent.parent / "transcript-analysis.py"` in the five files read.
   - Every other `spec_from_file_location` call under `claude/.claude/` uses another spec name and another script. `test_transcript_analysis.py`'s three `scope.py` loads (:120, :135, :155) sit inside functions.

   `[verified: grep of "spec_from_file_location(" under claude/.claude; read of the loader lines in test_transcript_analysis.py, period_cost, author_outcome, ledger_common, and review_rounds]` For the files whose `_SCRIPT` line was not read, the pin's own set equality checks it on every run.
3. Reads to retarget, per file: `test_transcript_cost.py` 396, `_review_rounds` 91, `_cache_rebuild_ttl_rules` 84, `_ledger_common` 82, `_handoff_signal_response` 76, `_pr_cost_export` 63, `_pr_cost_gh` 62, `_plan_boundary` 44, `_cost_counts` 35, `_cache_rebuild` 16, `test_author_outcome.py` 14, and `_period_cost` 10, 973 in all. `[unverified — relayed AST recount; Verification 3 recounts]` period_cost's 10 are the six alias values (:45-50) and four reads (:1417, :1418, :5941, :5951). `[verified: grep]`
4. Kept reads. The 12 files read `_mod.build_parser` or `_mod.main` on 25 lines, 1 to 5 per file. `test_transcript_cache_rebuild.py` reads them on one line (:540). `test_author_outcome.py`'s three are all `_mod.build_parser()` (:2451, :2458, :2463). `[verified: grep]`
5. The deletion set, 45 bindings, none of them an aliased import:
   - `os` (:8) and `tempfile` (:10);
   - 18 of the whole-module block's 19 names (:21-41), all but `scope`: `cache_rebuild`, `cache_rebuild_rules`, `corpus`, `cost`, `cost_ledger`, `gh_cli`, `handoff_nudge`, `handoff_signal_response`, `ledger_common`, `period_cost`, `plan_boundary`, `pr_cost`, `pr_cost_export`, `pr_cost_ledger`, `pricing`, `redaction`, `render`, `subagent_mix`;
   - 25 leaves:
     - cost (:84-90): `_accumulate_per_account_turn`, `_cost_report`, `_cost_trend_report`, `_print_branch_exclusion_diagnostic`, `_print_model_id_table`, `_print_thread_table`, `_print_token_class_table`;
     - pricing (:156-162): `_TOKEN_CLASSES`, `_cache_write_split`, `_context_at_turn`, `_context_bucket`, `_context_window_for_model`, `_model_rates`, `_price_turn`;
     - redaction (:174-179): `_BUILT_IN_AGENT_TYPES`, `_REDACT_MAP_MISS_TOKEN`, `_build_redact_map`, `_corpus_fingerprint`, `_redact_proj_label`, `_redact_session_id`;
     - render (:182): `_fmt_date`;
     - scope (:201, :205-207): `_DO_NOT_PUBLISH_BANNER`, `_redaction_ordinals`, `_resolve_cost_roots`, `_resolve_project_scope`.

   `[verified: read of shim :1-254]` No `<module>.` attribute read of any of the 18 modules occurs in shim code outside strings. `[verified: grep]` That no shim code reads any of the 45 bare is ruff's claim. `[unverified: Verification 6 runs ruff F401]`
6. What stays, and why.
   - `main()` assigns `scope.PROJECTS_DIR` (:1072) and reads `_SUBCOMMANDS_REFUSING_TOP_LEVEL_CONFIG_DIR` (:1047). `[verified: read of :1036-1078]`
   - `build_parser` reads `cmd_cost`, `cmd_cost_trend`, `_add_project_scope_args`, and `_iso_date` bare. `[unverified: those lines were not read; Verification 6's ruff run confirms]`
   - `main()`'s comment at :1044 names `_resolve_cost_roots` bare. It is the only `build_parser` or `main` comment that names a deleted binding. `[verified: grep]`
   - `build_parser` reads only bare names, and every name it reads stays, so the deletions cannot change help text or parser configuration. `[unverified: derived; Verification 8's capture parity checks it]`
7. Existing bindings.
   - `test_author_outcome.py` binds `author_outcome as ao` (:13) and `corpus` (:14).
   - `test_transcript_review_rounds.py` binds `corpus, pricing, render, review_rounds, scope` (:15).
   - None of the other ten imports from `transcript_analysis`.
   - `test_transcript_ledger_common.py` reads `_mod.os` (:195, :512) and `_mod.tempfile` (:492), and imports neither module.

   `[verified: grep]`
8. period_cost's aliases sit at :45-50: `period_cost`, `gh_cli`, `pricing`, `scope`, `cost_module`, and `render_module`, each bound to `_mod.<module>`. `cost_module` is loaded only at :2556 and :2940, and `render_module` is never loaded. `[verified: grep]`
9. The prefix guard.
   - `TEST_FILES` (:55-108) has 52 entries. It lists 4 of the 12 files (`cost_counts`, `plan_boundary`, `handoff_signal_response`, `period_cost`) and `test_transcript_analysis.py`.
   - A listed file may not bind a package module through an alias (:407-418) or import `from transcript_analysis.<m>` (:421-435).
   - Each `<module>.<name>` read in a listed file must name a top-level binding of that module (:386-404). The check sees only whether the name exists.
   - `_calls_spec_from_file_location` (:471-482) reports whether a file loads a copy, not what it reads through it.

   `[verified: read]`
10. Asked whether the `TEST_FILES` additions stay, the engineer chose to keep them. `[engineer-verified: "Keep the additions (Recommended)"]` That answer, in the parent plan's row 34, covered both PRs' eight additions. PR 2's share is seven.
11. Asked whether the new pin test stays, the engineer chose to keep it. `[engineer-verified: "Keep the pin (Recommended)"]` (parent plan row 33)
12. Asked whether to waive the governing plan's revert rehearsal for both retarget PRs, the engineer chose to waive it. `[engineer-verified: "Waive for both PRs (Recommended)"]` (parent plan row 25) Verification therefore has no revert step. The rehearsal guards against a conftest fixture promotion that leaves an earlier PR's revert uncollectable. `[verified: transcript-analysis-decomposition.md:276-279]` PR 2 changes no `conftest.py` line, so it cannot cause that. `[verified: Critical files]`
13. `test_transcript_review_rounds.py:3532` asserts `args.func == _mod.cmd_review_round_cost`. That is the same binding the shim's `p_review_round_cost.set_defaults(func=cmd_review_round_cost)` reads (:803), so the assertion cannot catch the shim binding the wrong object under that name. The five other `args.func ==` assertions in the 12 files already compare against the owning module's attribute: handoff_signal_response :274, pr_cost_export :753, plan_boundary :545, and pr_cost_gh :924 and :954. `[verified: grep]`
14. The toolbox.
    - `<toolbox>/retarget_check.py` takes `--shim`, `--scripts-dir`, `--after`, `--before` or `--prose-only`, `--base`, and `--keep` (:853-860).
    - Its `ast` comparison uses `ast.dump` without source positions (:669).
    - Its snapshot check fires for every site of a row patched inside a function decorated `pytest.fixture` or `pytest.fixture(...)` (:530-537, :590-597).
    - `<toolbox>/verify2/controls.py` reruns PR 1's controls (a)-(k) on PR 1's saved copies. `<toolbox>/verify2/junit_run.sh` runs exactly this plan's junit set, in its two passes.
    - PR 1's codemod is not in `<toolbox>`.

    `[verified: Glob of <toolbox>; read]` No hash of the checker was recorded, so its sameness with the file PR 1's controls proved stays unverified until Verification 0's integrity run. `[unverified]`
15. All ten `@pytest.fixture` decorators in the 12 files use the call form, `@pytest.fixture()` or `@pytest.fixture(autouse=True)`: two in cost_counts, seven in period_cost, and one in review_rounds. `[verified: grep]`
16. test_transcript_cost.py reads `_mod._accumulate_per_account_turn` at :1549 and :1572, each before its function's `monkeypatch.setattr(_mod.cost, "_accumulate_per_account_turn", …)` at :1558 and :1581. `[verified: grep]` conftest.py patches none of the by-name rows. `[unverified — relayed from the parent plan's row 7, which grepped conftest's setattr calls]`
17. test_transcript_cost.py reads both `_mod._price_turn` (:215) and `_mod.pricing._price_turn` (:1777), and both become `pricing._price_turn`. `[verified: grep]` `nodes --rename` exits 2 when two entries share a new name. `[unverified — relayed from the parent plan's row 13, a read of _move_fidelity.py:282-284]`
18. Doc and docstring prose.
    - The architecture doc holds the sentences PR 2 makes false at :57-58 (the shim binds names for test reads), :788-791 (other shim-loading files read through their copy), and :799-802 (author_outcome's copy serves `cmd_author_outcome`). Its guard paragraph at :860-866 does not mention the pin.
    - At this base, Verification 8's multiline Grep pattern matches the doc at :57-58, :668, :789-791, and :862.
    - `test_transcript_analysis_cost_import_direction.py:7-12` and `test_transcript_cli_bootstrap.py:9` each name the `_mod.` channel.
    - The cost-import-direction test needs the shim to keep a `from transcript_analysis.cost import` statement (:79-89). `cmd_cost` and `cmd_cost_trend` keep it.

    `[verified: read; Grep]`
19. The words `_mod`, `test`, `tests`, and `noqa` occur in the shim only on lines PR 2 deletes or replaces (:8-207). Identifiers such as `rearm_backtest` contain `test`, but not as a whole word. `[verified: grep]`
20. pyproject's addopts set `-n auto` (:24). xdist reads `PYTEST_XDIST_AUTO_NUM_WORKERS` for `-n auto` (xdist/plugin.py:17 in the shared venv). `select-tests.py` leaves a preset value alone (:991, :1233-1238). `[verified: read]`
21. PR 1's pre-image of `test_transcript_analysis.py` reads `_mod._cost_report` or `_mod.os` on 14 lines. `[verified: grep of <toolbox>/verify2/s4/before-test_transcript_analysis.py]` Reverting PR 1 alone after PR 2 merges would therefore restore reads of bindings PR 2 deleted. `[unverified: derived; no revert was run]`
22. ruff runs with line length 130, and RUF100 is not selected. `[verified: pyproject.toml:2, :6]` Some retargets lengthen a line, such as `_mod.cmd_handoff_signal_response` becoming `handoff_signal_response.cmd_handoff_signal_response`. `[unverified — relayed: a staff-sdet in-memory replay of the rename found no line over 130 columns; R2 still stops on E501, and I001 import placement is the one remaining source]`
23. Shim-alias names retired before PR 2 still appear in prose outside PR 2's files, such as `_dedup_turns_by_request_id` at docs/transcript-analysis.md:935 and `_resolve_scan_roots` at :1924. `[verified: read]`
24. Asked whether the four quotes this plan carries from earlier GH-1116 sessions (the `Ask:` line's standing quote and rows 10-12) are theirs and still hold for PR 2, the engineer chose "Yes, all four hold (Recommended)". `[engineer-verified: "Yes, all four hold (Recommended)"]`
25. Asked whether PR 2 keeps the pin and its docstring, the seven `TEST_FILES` additions, the docstring edits in the cost-import-direction and cli-bootstrap tests, the four architecture-doc edits, and the three adopted recipe refinements, the engineer chose "Keep all (Recommended)". `[engineer-verified: "Keep all (Recommended)"]`
26. During plan review the engineer said "Please make sure the doc string edits and the doc strings in general don’t restate what the code already show shows doc strings should include non-obvious information". `[engineer-verified: "Please make sure the doc string edits and the doc strings in general don’t restate what the code already show shows doc strings should include non-obvious information"]` This plan therefore prescribes one-line docstrings that state a non-obvious fact, adds no pin sentence to the module docstring, and adds no comment to the shim's `scope` and `cost` imports. The same rule governs the D2 brief.

**Mechanisms:**
- M1: Direct `<binding>.<name>` reads, with unaliased new imports, existing bindings reused, and period_cost's aliases removed. `anchors: row3, row7, row8, row9`. The parent plan's Decision set the `_mod.<module>.<name>` alternative aside.
- M2: Two commits, a pure rename followed by the semantic changes. The replay proves the first exactly, and the second gets review attention. `anchors: root, row17`.
- M3: PR 1's checker, reused behind an integrity rerun, with controls (l)-(p) from a scratch runner and K only as a fallback. `anchors: row14, row15, row16, row17`. Each lighter primitive fails:
  - `nodes --rename` rejects this table (row 17).
  - Junit parity, the prefix guard, and ruff together still pass a wrong target that exists and a flipped assertion that still passes, because the guard checks only that a name exists (row 9).
  - Reusing the checker without the rerun rests on a hash nobody recorded (row 14).
- M4: The deletion set comes from ruff F401 with noqa ignored, plus a reader grep and a probe at `<base>`. The same ruff run checks the end state. `anchors: row5, row6`.
- M5: Seven `TEST_FILES` additions, with `test_author_outcome.py` held back. `anchors: row7, row9, row10`.
- M6: The pin, with two cross-checked selection sets, a per-file allowed-read assertion, and controls (a)-(f). `anchors: G1, row2, row4, row11`. Each lighter primitive fails:
  - ruff F401 alone allows `_mod.cmd_cost` and `_mod.scope.<name>`, because those bindings stay (row 6).
  - A `noqa` text check on the shim stops re-exports, but not reads of the bindings that stay.
  - `_calls_spec_from_file_location` sees that a file loads a copy, not what the file reads through it (row 9).
- M7: Prose checks: the checker's `--prose-only` mode per test file, a whole-word shim grep, a `_mod` grep over two docstrings, and a wrap-tolerant phrase Grep over the doc. `anchors: row18, row19`.
- M8: Two sequenced `code-writer` dispatches, R2 then D2, with K only on the fallback. `anchors: row3, row5, row14`.
- M9: Junit-set parity and the selected suite, each run as a `not timing` pass and a serial `timing` pass, all with one xdist worker. `anchors: G2, G4, G5, row20`.
- M10: Capture parity of help text and parser dumps across the deletions. `anchors: row6`.

## Critical files

**Dispatches.** Every dispatch goes to `code-writer` with `model: sonnet`. Each returns to the parent, which runs the named Verification steps and makes each commit. R2 runs before D2, because D2's input, the deletion set, is derived from the tree R2 leaves. One rename dispatch covers all 12 files, because the table rule and the codemod are shared background that a split would restate in every brief (plan-it Step 5).
- **R2, the rename (commit 1).**
  - Files: the 12 test files only.
  - Derive the table from the shim at HEAD, per Approach "Commit 1". If a shim import binds a name from anywhere other than `transcript_analysis`, `transcript_analysis.<module>`, or the stdlib, stop and report.
  - Write a scratch codemod at `<scratch>/retarget_apply.py`, outside the repository, and reuse it on a retry. PR 1's codemod is gone (row 14).
    - It rewrites each site by its source span. It takes UTF-8 byte offsets from `lineno`, `col_offset`, `end_lineno`, and `end_col_offset`, and works right to left within a line.
    - It removes period_cost's six alias statements and renames the two `cost_module` loads.
    - Never retype a read. Do not read `<toolbox>/retarget_check.py` or anything under `<toolbox>/verify2/`, so the proof does not re-run the code it checks.
  - Add each file's imports per Approach "Commit 1". Place them with `scripts/venv-run.sh ruff check --select I001 --fix <file>`, and apply no other ruff fix.
  - Stop and report on any of these:
    - a `_mod.<attr>` that is neither a table row nor `build_parser` or `main`;
    - a site inside a function, lambda, comprehension, or class body that binds the target's root name;
    - a Store or Del of a `_mod.<attr>`;
    - any E501. Do not reflow the line.
  - Touch no comment, docstring, string, `setattr(_mod, …)` argument, or other file. Do no staging and no commits.
  - Run every tool through `scripts/venv-run.sh`. If it reports a tool missing, stop and return.
  - Verification it runs: `scripts/venv-run.sh ruff check claude/.claude/ claude-skills/ plugins/ evals/`.
  - Return the per-file replaced counts, the import statements added, and the codemod's path.
- **D2, the deletions, prose, guard, and pin (commit 2).**
  - Files: the shim; `test_author_outcome.py` (the wiring test only); `test_transcript_package_module_prefixes.py`; `test_transcript_analysis_cost_import_direction.py`; `test_transcript_cli_bootstrap.py`; and `docs/transcript-analysis-architecture.md`.
  - The parent passes in Verification 6's deletion set.
  - Make exactly the edits in Approach "Commit 2" and "The pin", anchored on content, and leave every other line unchanged.
  - Write no docstring or comment beyond the ones Approach prescribes. Each of those states only a non-obvious fact, never what the code or test name already shows.
  - Stop and report if ruff F401 flags a shim import outside the deletion set, or if F821 flags anything.
  - Do no staging and no commits.
  - Verification it runs: `scripts/venv-run.sh ruff check claude/.claude/ claude-skills/ plugins/ evals/`, then `scripts/venv-run.sh pytest -n0 claude/.claude/scripts/tests/test_transcript_package_module_prefixes.py claude/.claude/scripts/tests/test_transcript_analysis_cost_import_direction.py`.
  - Return each deleted binding, each prose edit as `file:line old → new`, and `wc -l` for every changed file.
- **K, only on Verification 0's fallback.** It writes `<scratch>/retarget_check.py` to the spec in the parent plan's Verification step 2 ("Checker spec, for dispatch K"), and nothing else, so it runs alongside R2. Its verification: `scripts/venv-run.sh python3 <scratch>/retarget_check.py --help` exits 0. `<chk>` then names that file.

**Files:**
- **Modify:**
  - the 12 test files, all under `claude/.claude/scripts/tests/`: `test_transcript_cost.py`, `test_transcript_review_rounds.py`, `test_transcript_cache_rebuild_ttl_rules.py`, `test_transcript_ledger_common.py`, `test_transcript_handoff_signal_response.py`, `test_transcript_pr_cost_gh.py`, `test_transcript_pr_cost_export.py`, `test_transcript_plan_boundary.py`, `test_transcript_cost_counts.py`, `test_transcript_cache_rebuild.py`, `test_author_outcome.py`, and `test_transcript_period_cost.py`;
  - `claude/.claude/scripts/transcript-analysis.py`;
  - `claude/.claude/scripts/tests/test_transcript_package_module_prefixes.py`;
  - `claude/.claude/scripts/tests/test_transcript_analysis_cost_import_direction.py`;
  - `claude/.claude/scripts/tests/test_transcript_cli_bootstrap.py`;
  - `docs/transcript-analysis-architecture.md`.
- **Create:** `.claude/plans/shim-reader-retarget-pr2.md`, this plan. The PR creates no other file, and none under `claude/.claude/`.

**Parent sequence.** Commit this plan alone, as its own commit, before Verification 0, so `git status --short` prints nothing there. Then run Verification 0 through 10 in order. K runs alongside step 2 only on step 0's fallback. Reruns key on the files an edit touches, not on commit boundaries:
- An edit to any of the 12 test files after R2 reruns Verification 3 and 4. D2's wiring test in `test_author_outcome.py` is exempt, because Verification 3 and 4 run before D2 and the test is a commit 2 addition.
- An edit to any D2-owned file after Verification 8 reruns the checks of Verification 8 that read that file, plus Verification 9 when the edit touches the pin.
- Only an edit to the shim or a test body reruns Verification 8's selected suite. A docstring, comment, or doc edit reruns its own checks and ruff.

**Rebase rule.** A rebase or merge that brings in a change under `claude/.claude/scripts`, `claude/.claude/hooks`, or `docs/transcript-analysis-architecture.md` voids every capture, junit baseline, count, and deletion set, because each is keyed to the pre-rebase tree. Verification 0 and 10 detect such a change.
- Before commit 1: run the Abort procedure, rebase, and restart at Verification 0 with a fresh `<scratch>`.
- After commit 1: stop and report to the engineer.

**Abort procedure.** If a dispatch is interrupted, the code-writer cleans nothing up. The parent recovers:
1. `git status --short` must list only Critical files. On any other path, stop and report without touching anything.
2. Restore each listed file with its own literal `git restore --source=HEAD -- <path>` call.
3. `git status --short` then prints nothing.

Never use `git clean`, `git checkout .`, `git reset --hard`, or `git stash`.

**Explicitly unchanged:**
- `tests/conftest.py` and `tests/_move_fidelity.py`;
- every package module under `claude/.claude/scripts/transcript_analysis/`;
- `select-tests.py` and `docs/transcript-analysis.md`;
- every skill, hook, and rule file, and CHANGELOG.md;
- every committed plan, the parent plan included.

**Reuse:**
- `<toolbox>/retarget_check.py`, `<toolbox>/verify2/controls.py`, and `<toolbox>/verify2/junit_run.sh`, each unchanged;
- `_move_fidelity.py`'s `capture --all-help`, `compare-captures`, and `test-ids`, unchanged;
- the prefix test's `TESTS_DIR`, and the call-shape test inside its `_calls_spec_from_file_location`, extracted for the pin.

**PR body carries:**
- `Refs GH-1116`.
- Each Verification result with its recounted figures: each file's `replaced`, period_cost's alias counts, the 45 deleted bindings, the prose edits, `TEST_FILES` going from 52 entries to 59, the testcase totals from `test-ids`, and the selected-suite result, naming any failure reproduced at the merge-base.
- Commit 1 is a pure rename that the forward replay proves. Commit 2 holds the deletions, the prose, the guard additions, the pin, and the `author-outcome` wiring test.
- The `args.func` statement: "Before this change, test_transcript_review_rounds.py's `args.func` assertion compared against the shim's own `cmd_review_round_cost` binding, the one its `set_defaults` reads, so it could not catch the shim binding the wrong handler under that name. It now compares against `review_rounds.cmd_review_round_cost`." (row 13)
- The rollback statement: "A plain `git revert` of PR 2's squash commit restores its 45 bindings and its 12 files' reads together. PR 1's revert applies cleanly only while PR 2 is unmerged, because PR 2 deletes bindings that PR 1's restored reads need, such as `_cost_report` and `os`. Revert last-in-first-out. This is an inference; no revert was run." (row 21)
- The three deferred recipe refinements were adopted, each with the control or check that proves it.
- No CHANGELOG entry is added, and no file is added under `claude/.claude/`.
- The engineer waived the revert rehearsal for both PRs, and PR 2 changes no `conftest.py` line (row 12).
- The Out of scope follow-ups, listed in the body only.

## Verification

Run everything from the worktree root. Notation:
- `<toolbox>` is the scratch directory outside the repository that PR 1 created. It holds `retarget_check.py` and PR 1's runners under `verify2/`. The session holds its path, and this public plan names it only by placeholder.
- `<scratch>` is `<toolbox>/pr2`, created by one `mkdir` in Verification 1. If that `mkdir` fails because the directory exists, stop. If `<toolbox>` is gone, create a fresh directory outside the repository and use it for both.
- `<attempt>` is a fresh `mkdir` of `<scratch>/attempt-<n>` per dispatch attempt.
- `<chk>` is `scripts/venv-run.sh python3 <toolbox>/retarget_check.py`, or K's replacement under `<scratch>`. `<mf>` is `scripts/venv-run.sh python3 claude/.claude/scripts/tests/_move_fidelity.py`.
- `<shim>` is `claude/.claude/scripts/transcript-analysis.py`, `<sd>` is `claude/.claude/scripts`, and `<tests>` is `claude/.claude/scripts/tests`.
- `<w1>` is the environment prefix `PYTEST_XDIST_AUTO_NUM_WORKERS=1`. Every pytest and `select-tests.py` call carries it, and on a `-n0` call it has no effect (G4, row 20).
- The junit set is these files:
  - the 13 shim-loading files, the 12 plus `test_transcript_analysis.py`;
  - under `<tests>`: `test_transcript_package_module_prefixes.py`, `test_transcript_analysis_architecture_doc.py`, `test_transcript_analysis_import_cycles.py`, `test_transcript_cli_bootstrap.py`, and `test_transcript_analysis_cost_import_direction.py`;
  - `claude/.claude/hooks/tests/test_nudge_error_mode_analysis.py`.
- A junit run into `<x>` is two `<w1> scripts/venv-run.sh pytest` passes over the junit set. The first is `-m "not timing" --junitxml=<x>-parallel.xml`, and the second is `-m timing -n0 --junitxml=<x>-timing.xml`. `<w1> bash <toolbox>/verify2/junit_run.sh <worktree root> <x>` runs exactly these two passes and prints each one's exit status (row 14).

Run each git command and each capture as its own literal call.

0. **Preconditions.**
   - `scripts/venv-run.sh python3 -c "__import__('tokenize').FSTRING_MIDDLE"`, `scripts/venv-run.sh pytest --version`, and `scripts/venv-run.sh ruff --version` each exit 0. The first proves Python 3.12 or later, which the checker needs.
   - Run `git fetch origin main`, then `git diff --name-only HEAD...origin/main -- claude/.claude/scripts claude/.claude/hooks docs/transcript-analysis-architecture.md`. Any output invokes the Rebase rule.
   - `git status --short` prints nothing.
   - PR 1 is in the base. `git grep -n "_mod\._resolve_project_scope" -- <tests>/test_transcript_analysis.py` prints nothing, and `git grep -n '"test_transcript_analysis.py"' -- <tests>/test_transcript_package_module_prefixes.py` hits. Otherwise stop and report that PR 1 is not in this base.
   - **Checker integrity.** Run `scripts/venv-run.sh python3 <toolbox>/verify2/controls.py`.
     - It passes when its `base` line shows exit 0 with no classes, and each of its `a`, `b`, `c-doc`, `c-comment`, and `d` through `k` lines shows exit 1 with exactly its `expect` class.
     - A traceback, any other result, or a missing file sends the parent to K, which then runs alongside step 2.
1. **Baseline, before R2.**
   - Run `git rev-parse HEAD` and record the output as `<base>`.
   - Run `mkdir <scratch>`. Copy each file that Critical files modifies to `<scratch>/before-<basename>`, each with its own `cp` call.
   - Run `mkdir -p <scratch>/account/projects`.
   - Run `<mf> capture --script <shim> --account <scratch>/account --all-help --out <scratch>/before-a`, then the same into `<scratch>/before-b`. `<mf> compare-captures <scratch>/before-a <scratch>/before-b` must be clean, which proves the capture is deterministic.
   - Do a junit run into `<scratch>/before`. Every test passes.
2. **Dispatch R2**, per Critical files, with a fresh `<attempt>`.
3. **Rename proof.** It proves three things: only table reads changed, each new read is the same object as the old one, and no read changes meaning because of a patch or a local shadow.
   - For each of the 12 files, `<chk> --shim <shim> --before <scratch>/before-<file> --after <tests>/<file>` exits 0. Save its output to `<attempt>/chk-<file>.txt`.
   - Each file's `replaced` equals its count in row 3. For period_cost, `replaced` is 4, `alias-statements-removed` is 6, and `alias-loads-renamed` is 2. The 12 `replaced` values sum to 967, row 3's 973 less the six alias values. A mismatch stops the run, and the parent reports both numbers.
   - Each file's `prose:` count is 0. A non-zero count stops the run, and the parent reports it.
   - `scripts/venv-run.sh ruff check claude/.claude/ claude-skills/ plugins/ evals/` is clean.
   - Do a junit run into `<attempt>/c1`. `<mf> test-ids --before <scratch>/before-parallel.xml --before <scratch>/before-timing.xml --after <attempt>/c1-parallel.xml --after <attempt>/c1-timing.xml` must be clean.
4. **Checker controls.** The parent writes `<scratch>/controls.py`, modeled on `<toolbox>/verify2/controls.py`.
   - Each control seeds one defect by a substitution asserted to match exactly once, or the first match where named. It writes the seeded copies to `<scratch>/ctl-<x>/before.py` and `after.py`.
   - The before copy starts from `<scratch>/before-<file>`, and the after copy from `<tests>/<file>` as R2 left it.
   - Each runs `<chk> --shim <scratch>/before-transcript-analysis.py --scripts-dir <sd> --before <scratch>/ctl-<x>/before.py --after <scratch>/ctl-<x>/after.py`.
   - Each must exit 1 with only its named finding class. An exit of 0, or a different class, is a checker defect: stop.

   The controls:
   - (l) In the period_cost after copy, change the module-level `cost._LIST_PRICE_CAVEAT` load in the `_CAVEAT_LINE` assignment (base :2556, a former `cost_module` load) to `pricing._LIST_PRICE_CAVEAT`. Expect `ast`.
   - (m) In both period_cost copies, append the module-level line `def _alias_store_probe(): period_cost = None`. Expect `alias-store`. The rebinding sits in its own function, because a module-level rebinding would also make every `period_cost` load a `shadow` finding.
   - (n) In the ledger_common after copy, delete the added `import tempfile` line. Expect `import-missing`.
   - (o) In the author_outcome after copy, change the first `ao.cmd_author_outcome` read to `corpus.cmd_author_outcome`, a module the file binds at :14. Expect `ast`.
   - (p) In both test_transcript_cost.py copies, append a module-level fixture: the decorator line `@pytest.fixture(autouse=True)`, then `def _snapshot_probe(monkeypatch):`, then the body line `monkeypatch.setattr(_mod, "_cost_report", None)`. The checker leaves that call's arguments alone, so both copies match. Expect `snapshot`, once per `_mod._cost_report` site.
   - If K ran again, also run (a)-(k) as the parent plan's Verification step 5 defines them, on test_transcript_cost.py's copies, with its PR 2 variants for (g), (h), and (j).
5. **Commit 1.** Run `/code-review`. Then stage each of the 12 files with its own `git add <path>` call, and commit.
6. **Deletion set, before D2.**
   - **Candidates:** `scripts/venv-run.sh ruff check --no-cache --select F401 --ignore-noqa <shim>`. If ruff rejects `--ignore-noqa`, stop and report. No PR 2 candidate is an aliased import, so each finding names the shim's own binding.
   - **Readers:** `git grep -noE "_mod\.(<the candidates, as an alternation>)\b" -- '*.py'`. A hit in a file whose loader does not name `transcript-analysis.py`, such as `_mod.os` in `test_mark_terminal.py`, is inspected and reported, not counted.
   - **Deletion set:** the candidates with no reader in a shim-loading file. It must equal row 5's 45 names. Any difference stops the run.
   - **Probe:** `git grep -ohE "<the same pattern>" <base> -- '*.py' | sort | uniq -c` must count every one of the 45 names at least once. This proves the scan can see the names it checks for.
   - `git grep -nE "(getattr|hasattr|setattr|delattr)\(_mod\b|patch\.object\(_mod\b" -- '*.py'` returns no line in a shim-loading file. Probe: the same grep at `<base>` hits the `setattr(_mod.<module>, …)` sites in `test_transcript_ledger_common.py` and `test_transcript_pr_cost_gh.py`, which R2 rewrites.
7. **Dispatch D2**, per Critical files, with a fresh `<attempt>`.
8. **Deletion proof.** It proves four things: no reader of a deleted binding is left, the deletions change no help text or parser configuration, no test outcome changes, and no edited prose still describes the retired read channel.
   - Repeat step 6's reader grep and its `(getattr|…)` grep on the working tree. Neither may hit a shim-loading file.
   - `scripts/venv-run.sh ruff check --no-cache --select F401 --ignore-noqa <shim>` reports nothing.
   - `git grep -nwiE "_mod|tests?|noqa" -- <shim>` prints nothing (row 19). Probe: `git grep -n "^def build_parser" -- <shim>` hits.
   - For each of the 12 files, `<chk> --shim <scratch>/before-transcript-analysis.py --scripts-dir <sd> --prose-only --after <tests>/<file>` reports `prose: 0`.
   - `git grep -nw "_mod" -- <tests>/test_transcript_analysis_cost_import_direction.py <tests>/test_transcript_cli_bootstrap.py` prints nothing. Probe: the same grep at `<base>` hits each file's docstring line.
   - **Wrap-tolerant doc check.** Run the Grep tool on `docs/transcript-analysis-architecture.md` with `multiline: true` and the pattern `through\s+(the|its|their|a)\s+(shim|copy)|calls\s+into\s+the\s+shim|re-?exports?|shim\s+also\s+binds|via\s+the\s+shim|_mod\b`.
     - It must match exactly three sentences: the friction-count sentence ending "reads a bare integer from stdout" (base :668), D2's new sentence "It reads only those two names through its copy …", and the guard-shape sentence that names `_mod.<module>.<name>` (base :862).
     - Any other match, or a missing one, means a stale or wrong sentence: stop and report it.
     - Probe: the same Grep on `<scratch>/before-transcript-analysis-architecture.md` also matches :57-58 and :789-791.
   - `scripts/venv-run.sh ruff check claude/.claude/ claude-skills/ plugins/ evals/` is clean.
   - Run `<mf> capture --script <shim> --account <scratch>/account --all-help --out <attempt>/after`. Then `<mf> compare-captures <scratch>/before-a <attempt>/after` must be clean (row 6).
   - Do a junit run into `<attempt>/c2`. `<mf> test-ids` from `<scratch>/before` to `<attempt>/c2`, with two `--expect-new` flags, `test_shim_copy_reads_only_build_parser_and_main` and `TestBuildParserWiring::test_author_outcome_dispatches_to_cmd_author_outcome`, must be clean. The first ID is module-level and the second is class-qualified, because `_testcase_id` keys on the dotted classname.
   - Run the selected suite (G2) as two passes:
     - `<w1> scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py -m "not timing"`, then `<w1> scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py -m timing -n0`.
     - Run each as a background Bash call, and start the second only after the first finishes. Expect a run of over an hour (G4). Edit nothing until both finish.
     - Both must pass, apart from failures reproduced at the merge-base as below.
   - Before claiming or fixing a failure in a file outside Critical files, reproduce it at the merge-base, per CLAUDE.md:
     - run `git merge-base HEAD origin/main`;
     - run `git worktree add <scratch>/merge-base <sha>`;
     - run only the failing test IDs there, with `<w1> scripts/venv-run.sh pytest`;
     - run `git worktree remove <scratch>/merge-base`.

     G5's four `test_shellcheck.py` failures count as pre-existing only once they fail there too. The PR body names them.
9. **Pin controls.** For each control:
   - run `sha256sum` on the file first, and save the output to `<attempt>/pin-<x>.sha256`;
   - edit by hand with the Edit tool, then run `scripts/venv-run.sh pytest -n0 <tests>/test_transcript_package_module_prefixes.py::test_shim_copy_reads_only_build_parser_and_main`;
   - undo the edit by hand, then pass `sha256sum -c <attempt>/pin-<x>.sha256`.

   Each run must fail as stated, with a message that names the file. The controls:
   - (a) Add `_PIN_PROBE = _mod.scope` at module level in `test_transcript_cache_rebuild.py`. The pin fails, naming that file and `scope`.
   - (b) Add `_PIN_PROBE = getattr(_mod, "main")` in the same place instead. The pin fails on the bare use.
   - (c) Add `_PIN_PROBE = _mod.cmd_cost` at module level in `test_transcript_analysis.py`. The pin fails, naming that file and `cmd_cost`, which shows a second file is selected.
   - (d) In `test_author_outcome.py`'s `_spec = …` loader line, change the spec name `"transcript_analysis"` to `"transcript_analysis_pin_probe"`. The file falls into the second set only, and the cross-check fails, naming it.
   - (e) On `test_transcript_cache_rebuild.py`'s one allowed-read line (row 4), rename `_mod` to `_pin_probe`. The per-file allowed-read assertion fails, naming that file.
   - (f) In `test_transcript_ledger_common.py`'s `_SCRIPT = …` line, change `"transcript-analysis.py"` to `"transcript-analysis-pin-probe.py"`. The file falls into the first set only, and the cross-check fails, naming it.
   - (g) Wiring-test control, same sha256 procedure on the shim. At the `p_author_outcome.set_defaults(func=cmd_author_outcome)` line, change `cmd_author_outcome` to `cmd_cost`. Run `scripts/venv-run.sh pytest -n0 <tests>/test_author_outcome.py::TestBuildParserWiring`. Only `test_author_outcome_dispatches_to_cmd_author_outcome` fails.
10. **Commit 2 and hand-off.**
    - Run `/code-review`. Then stage each file D2 changed with its own `git add <path>` call, and commit.
    - `scripts/venv-run.sh pytest -n0 claude/.claude/tests/test_pytest_collection_config.py::TestNoBareSameDirectorySiblingImports claude/.claude/hooks/tests/test_ticket_reference_discipline.py` must pass.
    - Repeat step 0's `git fetch` and `git diff` calls. Any output means stop and report it to the engineer.
    - Run `/ready-for-review`, then open the PR with the body Critical files lists.

## Out of scope

- **The `cli.py` phase's work.** It relocates `_UNCONDITIONAL_HEADER_CASES`, its two classes, `TestBuildParser`, and `TestConfigDirRouting`. It retargets `_mod.build_parser` and `_mod.main`, removes the loaders, and rewrites the shim's "still-monolithic" comments along with `build_parser` (G1).
- **Unaliasing `ao` and listing `test_author_outcome.py` in `TEST_FILES`.** DEFER to the `cli.py` phase. Once that phase drops the file's loader, the guard requires the listing, and its unaliased-import check then requires dropping `ao` (row 9).
- **Removing `_mod_prefixed_module_attribute_reads` from the prefix guard.** After PR 2 it finds no reads, and the pin forbids the shape. DEFER: PR 2's guard edits stay additive, and the `cli.py` phase can drop the shape together with the loaders.
- **A shim copy obtained through a helper module, or loaded inside a function or fixture.** Both pin sets read each file's own module-level `spec_from_file_location` call, so such a copy escapes both. No test file does so today (row 2). DEFER: the `cli.py` phase removes the loaders.
- **Synthetic-source unit tests for each branch of the pin.** Controls (a)-(f) prove the pin once, by hand, on real files. A permanent unit layer would also cover the no-assignment failure, the non-empty guard, the function-scope skip, the `exec_module` exemption, and the direct-constant arm of the second set. DEFER: the `cli.py` phase deletes the loaders and with them the pin, so a permanent layer would outlive its subject.
- **A checker control for the bare `@pytest.fixture` form.** No PR 2 file uses that form (row 15).
- **Shim-alias names retired before PR 2 that still appear in prose outside its files**, such as those in `docs/transcript-analysis.md` (row 23). PR 2 neither caused them nor touches those files. The PR body lists them as a follow-up, from a fresh grep.
- **Committing the replay checker.** PR 2 is its last use, because the `cli.py` phase's own retarget has injective new names that `nodes --rename` can prove.
- **Splitting files over 1,000 lines.** The shim, `test_transcript_cost.py`, `test_transcript_period_cost.py`, `test_transcript_review_rounds.py`, and `test_author_outcome.py` were over the limit before PR 2, which grows none of them by more than its import lines, except `test_author_outcome.py`, which also gains the wiring test.
- **Any production-code, CLI, help-text, or output change.**
- **Preserved records that name `_mod.` read shapes or carry stale counts**: committed plans, the parent plan included; `docs/reports/`; `docs/case-studies/`; and CHANGELOG.md.
- **The governing plan's revert rehearsal.** The engineer waived it for both PRs (row 12).
- **Installing a newer system `shellcheck`.** That needs the engineer (G5).
- **GH-1116 tracker edits and follow-up issues.** The PR body states what PR 2 took over.
