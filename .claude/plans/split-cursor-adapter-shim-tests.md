# Split test_cursor_adapter_shim.py below the code-file line limit

## Context

Goal: replace `claude/.claude/scripts/tests/test_cursor_adapter_shim.py` (4,366 lines at `bc9c9194`, 902 collected tests) with topic-cohesive test files that are each under the repo's 1,000-line code-file limit, with no test lost or weakened and no assertion edited.

Ask: "I'd like you to collaborate with teh cursor-adapter-phase4 session which has a lot of follow-up work and it's too much for taht agent to do" (engineer's words). The specific item, splitting this file, is item 3 on the `cursor-adapter-phase4` session's follow-up list; that is the peer's wording, not the engineer's. On starting now rather than waiting for the peer's package-conversion plan: "yep, agreed, start now" (engineer's words, to the session's proposal). On the one over-limit class: "I don't want to overindex on the 1000 file limt if the seam isn't a good one to split on. what does the architect think?" (engineer's words).

Why now: PRs #1508 (message cleanup), #1519 (the install-test split) and #1518 (`cursor-adapter.py` decomposed into an entry plus seven part files) are merged, so the file's names are stable on `origin/main`. The peer session is planning a possible package conversion on branch `cursor-adapter-package`. It has no code, PR or Critical files list yet. It expects no direct overlap with this file, because the file tests `hook-shim.py`, which the #1518 plan keeps out of scope. It named `select-tests.py` and `conftest.py` as the plausible collision points (peer-reported, not verified).

Intended outcome: one PR containing this plan and a mechanical move of test code into several new files plus one shared helper module. `select-tests.py` stays correct for every new file. `.claude/plans/` files that cite this file's line numbers stay as written.

Evidence gathered (from a research subagent and an architect consult, not yet design conclusions):
- Precedent: `.claude/plans/split-cursor-adapter-install-tests.md` (merged as #1519) moved line ranges unchanged into `test_cursor_adapter_install_<topic>.py` files plus `_cursor_install_helpers.py`, and checked fidelity with `claude/.claude/scripts/tests/_move_fidelity.py` (`nodes`, `test-ids`) and a scratch line check.
- The file holds 902 collected tests across 16 test classes and 2 module-level tests, and 15 `@pytest.mark.timing` marks in `TestShimArguments`, `TestShimPrologue`, `TestShimHookRun` and `TestDirectoryPin`.
- `TestDirectoryPin` (:3413-:4312) is one class of 900 lines. With the shell-probe block and two module-level tests that must sit beside it, its file would be about 1,040 lines. The architect consult recommended splitting it at its own banner `# --- the environment line and its host-PID part ---` (:3981). M3 and rows 4 and 5 carry the rationale.
- The test at :4315 patches this module's own `SHELL_PROBE_TIMEOUT_SECONDS` through `sys.modules[__name__]`, so `_has_builtin`, that constant's binding and that test must share one module. The import-time shell probes (:3341-:3344) must not run once per importing file.
- `select-tests.py:356` lists the file in `SCRIPTS_TESTS_IMPORTING_HOOKS_TESTS_MODULES`; `test_select_tests.py:1987`, `conftest.py:49`, `test_cursor_adapter_translate.py:2` and `docs/scripts.md:629` also name it.

## Approach

Replace `claude/.claude/scripts/tests/test_cursor_adapter_shim.py` with eight topic files named `test_cursor_adapter_shim_<topic>.py` and one shared module, `claude/.claude/scripts/tests/_cursor_shim_helpers.py`, in one PR that one `code-writer` dispatch builds (M1, M2). `TestDirectoryPin` splits at its own `# --- the environment line and its host-PID part ---` banner, and its second half becomes `TestPinEnvironmentLineAndShells` (M3). Every other class moves whole, and the largest file lands near 945 lines (row 2).

Each file is cut by line range from scratch copies of the base, so no test is retyped (M6). The helper holds the loaded shim and the 26 other names that two or more files read or that a helper name reads (M4). No fixture crosses a file, so no file binds one (row 10). The original file is deleted. `select-tests.py`'s importer set, one literal in `test_select_tests.py`, one comment in the scripts-tests `conftest.py`, one docstring in `test_cursor_adapter_translate.py` and `docs/scripts.md` name the new files instead (M8). `_move_fidelity.py`'s `nodes` and `test-ids` and a scratch line check show that nothing was lost or edited beyond the one added class line (M9).

Alternatives set aside:
- **`TestDirectoryPin` kept whole.** Its file would hold about 1,040 lines (Context). M3 gives the reason the split is the better seam.
- **`TestDirectoryPin` split at its later banner, ``# --- behaviour under each shell that has `builtin` ---`` (:4241).** The environment-line tests read `SHELL_PROBES` (:4200, :4219), as the shell rows do. The probe results would then cross a file and need the helper, where :4321's patch could no longer reach `_has_builtin` (row 8).
- **The outcome-row table (`Row` through `OUTCOME_ROWS`, about 285 lines) in the helper, so `TestShimArguments` can join payload validation.** It moves one class's parametrize table away from that class to serve one deadline test (:999). The outcomes file fits without it (row 2). It stays the fallback if Verification 8 measures that file over 1,000 lines.
- **The research subagent's candidate grouping.** Its arguments-plus-payload group puts `TestShimArguments` apart from `OUTCOME_ROWS`, which that class reads (row 7), so it would force the table above into the helper. It also parts `TestShimTextRules`' parser edges from payload validation, though both test what the shim does with a payload it cannot use.
- **`_has_builtin` and the probe results in the helper.** The module-level test at :4315 patches its own module's `SHELL_PROBE_TIMEOUT_SECONDS` (row 8).
- **Public names for the shared helpers.** Every call site would change, so the move would stop being a copy.
- **A residual `test_cursor_adapter_shim.py`.** Outside `.claude/plans/`, five sites name the file, and this PR edits all five anyway (row 14). A residual would hold an arbitrary topic under the old name.
- **The shared names in the scripts-tests `conftest.py`.** None is a fixture (row 10), so a conftest home buys nothing over an imported module, and it would widen a file the `cursor-adapter-package` session may edit (G2).
- **More than one dispatch.** Every file comes from one base copy and one fidelity run, and the helper's name list decides every file's imports.

**Root:** `claude/.claude/scripts/tests/test_cursor_adapter_shim.py` is 4,366 lines, over four times the 1,000-line limit for test files. The split moves its tests into files of at most 1,000 lines and keeps every collected test and its outcome. It edits no moved line and adds one, the class line of M3, so only that class's 22 test functions change the class part of their IDs. [verified: Read of the file at `bc9c9194`, ending at :4366; docs/design-decisions/code-file-line-limit.md:19; row 5]

**Givens:**
- G1. Merge is human-only, so the engineer decides when this PR merges relative to others. Reason: the engineer owns merge. [verified: ~/.claude/CLAUDE.md, Working Style's merge bullet]
- G2. The `cursor-adapter-package` session plans a package conversion with no code, PR or Critical files list yet, and named `select-tests.py` and `conftest.py` as its plausible collision points. The `cursor-adapter-phase4` session owns the adapter's remaining follow-up work. Other PRs may also edit these files while this PR is open. Reason: their authors own them. [unverified: peer-reported in the Context]
- G3. pytest rewrites `assert` statements only in `conftest.py` files, modules matching `python_files`, modules named on the command line and modules marked for rewrite. Reason: pytest owns assertion rewriting. [verified: `.venv/lib/python3.14/site-packages/_pytest/assertion/rewrite.py:227–:259` in the main checkout; `pyproject.toml` sets no `python_files`, so the default `test_*.py` and `*_test.py` apply]

**Mechanisms:**
- **M1: One PR, built by one `code-writer` dispatch and finished by the main session.** `anchors: root, G1, row3, row20, row21`
  - The work stays one dispatch (Approach, "More than one dispatch").
  - Verification is the runbook, in order. `code-writer` runs only step 3, the build, because its charter limits it to editing the files it is given and running read-only checks on what it changed (row 21). The main session runs every other step. It may hand the verbose pytest and fidelity runs to a `general-purpose` subagent with `model: sonnet`.
- **M2: Eight topic files and one helper, each at most 1,000 lines.** Critical files gives each file's ranges and docstring. `anchors: root, row1, row2, row7, row13`
  - Every test class but `TestDirectoryPin` moves whole, with the names only its file reads. No name changes.
  - The topics follow the base's own section banners:
    - outcomes: the outcome rows, the failure outcomes, and arguments and the deadline, with `TestRecoverySentence`;
    - input: payload validation, unknown tools and the payload cap, and the text rules;
    - prologue: the prologue, interpreter isolation and the tool-path rule's two conditions;
    - log: the log and the process-start count;
    - hook_run: the hook run;
    - faults: shim faults;
    - pin: the modes beyond the hook run and the directory pin;
    - pin_environment: the second half of `TestDirectoryPin` and the two module-level tests (M3).
  - Arguments and the deadline join the outcomes file because `TestShimArguments` reads `OUTCOME_ROWS` (row 7).
  - Every file shares the `test_cursor_adapter_shim_` prefix, so the family that shares the helper stays one glob.
- **M3: `TestDirectoryPin` splits at its own :3981 banner.** `anchors: row4, row5, row8, row9, row18`
  - `TestDirectoryPin` keeps :3413–:3979, the directory-pin tests. `TestPinEnvironmentLineAndShells` takes :3981–:4312: the environment line, its host-PID part, the strip, and the prefix under each candidate shell.
  - The line `class TestPinEnvironmentLineAndShells:` is the one line this PR adds to moved code. No other line of either half changes.
  - The two module-level tests (:4315, :4325) stay module-level and join the new class's file, because :4321 patches its own module and both read the probe names (row 8).
  - This is the architect's recommendation under the engineer's deferral (row 4). The seam is a topic boundary that nothing crosses (row 5), so it is the right split with or without the limit.
  - The new class's 22 test functions change only the class part of their IDs. Verification 5 maps each by one rename.
- **M4: The helper holds exactly the names that two or more planned files read, plus the names those names read.** `anchors: G3, row6, row7, row8, row12`
  - The 27 names are `SHIM_MODULE`, `RECOVERY_SENTENCE`, `OUTPUT_CAP`, `PAYLOAD_CAP`, `_scratch`, `_workspace`, `_assert_deny_text_rules`, `_probe_ran`, `_run_hook_body`, `_run_probe`, `HOOK_STARTED_TOUCH`, `HOOK_STARTED_THEN_SLEEP`, `_assert_hook_started`, `SHIM_MODULE_EVENTS`, `_recorded_wrapper`, `CANARY`, `FAULT_PROGRAM`, `_run_fault_program`, `_candidate_shells`, `SHELL_PROBE_TIMEOUT_SECONDS`, `CANDIDATE_SHELLS`, `_walkless_env`, `_environment_line`, `_pin_prefix`, `_pin`, `_pin_check` and `_prefixed` (row 6). Every other name moves with its only reader (row 7).
  - `_has_builtin` and the probe results never move to the helper, because :4321 patches the module that holds `_has_builtin` (row 8).
  - A name that ruff's F821 shows crossing a file boundary, and that row 6 does not list, stops the plan for a plan revision instead of a guessed home.
  - The helper's docstring takes over the base docstring's second paragraph (:4–:5), which names where the translators are tested and the home every shim run uses.
  - No fixture moves to the helper (row 10).
- **M5: Section banners and comments stay with their sections.** `anchors: row1, row13, row19`
  - Each top-level `# --- ... ---` banner goes to the file that takes the first test class after it. Where helper nodes sit between a banner and that class (:2657, :3314), the banner is a one-line range of its own.
  - A comment directly above a node, with no blank line between, moves with that node. So :79 goes to prologue, :1779 to log, :2138 to hook_run, :3326 to the helper, and :3346–:3347 to pin_environment.
  - The in-class banners (:2452, :2505, :2810, :4241) move inside their classes. The :3981 banner becomes the new class's first body line.
  - No comment or docstring inside a moved range changes (row 13).
- **M6: Copy by line range from scratch copies of the base, never retype.** `anchors: row1, row5, row18, row22, row23`
  - The build script first writes `<scratch>/before_split.py`: `<scratch>/before.py` with `class TestPinEnvironmentLineAndShells:` inserted directly above the one line that reads `    # --- the environment line and its host-PID part ---`. The script fails unless that line occurs exactly once, and unless deleting the inserted line gives back `before.py` byte for byte.
  - Every range is cut from `before_split.py`.
    - Its first line is its quoted banner or comment. Where it has none, the first line is that of its first node.
    - The script finds the first line by grep or from `_move_fidelity.py`'s `_load_source` spans. A node's first line includes its decorators.
    - Its last line is the last line of its named last node, from those spans.
    - The script fails when an anchor matches zero or several lines.
    - The `:N` numbers in Critical files are `before.py` guides only.
  - One re-runnable scratch script builds each file from nothing: it writes the file's header (M7), then each range in base order, with two blank lines between ranges. A retried build rewrites the file whole and never appends to a leftover.
  - `scripts/venv-run.sh ruff check --select I --fix <file>` then orders the header's imports, the only lines it can move.
  - The build also writes `<scratch>/fidelity.py`, a wrapper that calls `_move_fidelity.main` with argv lists it builds itself. `nodes`' `--moved` list comes from `_load_source(<scratch>/before_split.py)`. `test-ids`' `--rename` list comes from the new class's test functions in that file. The wrapper saves the `--rename` list to `<scratch>/renames.txt`, and the build asserts that its length equals the count of `def test_` lines after the banner in `before.py`. No command line then carries about 117 names.
  - The original stays in place until the main session's `git rm` (M1).
  - Every before and after pytest run comes from the same worktree path.
- **M7: Headers.** `anchors: row10, row11, row16, row24`
  - Each file opens with its docstring from Critical files, then `from __future__ import annotations`, then imports of exactly the names its code reads, each from the module that defines it.
  - Same-directory imports sit above the `sys.path.insert` line, as in `test_cursor_adapter_install_roundtrip.py:15–:27`. They are `from ._cursor_shim_helpers import (...)` and, in pin_environment only, `from .conftest import require_zsh`.
  - Each file that imports from `hooks.tests` copies the base's `sys.path.insert(0, str(CLAUDE_DIR))` line (:28). The helper and all eight test files do (row 16).
  - Each `hooks.tests` import statement carries `# noqa: E402 (the import needs the sys.path entry above)`, the form `_cursor_install_helpers.py:25` uses (row 24).
  - No file imports the helper under an alias or binds a fixture (row 10).
  - A header carries no other comment.
- **M8: Routing and docs.** `anchors: row14, row15, row16`
  - In `select-tests.py`, the eight new paths replace :356 in `SCRIPTS_TESTS_IMPORTING_HOOKS_TESTS_MODULES`, sorted. The set's comments already cover a member that reaches a hooks-tests module through a helper, so none changes (row 15).
  - `test_select_tests.py:1987` names the outcomes file in place of the old one (row 16).
  - The scripts-tests `conftest.py:49` names `_cursor_shim_helpers.py` as its example. `test_cursor_adapter_translate.py:2` and `docs/scripts.md:629` name the new family. Critical files gives each wording.
  - `.claude/plans/` files that cite this file's lines stay as written.
- **M9: Fidelity checks.** `anchors: root, row17, row18, row19`
  - `test-ids` compares every test's ID and outcome across both CI passes, before and after, with one rename per test function of the new class (Verification 5).
  - `nodes` compares every moved node's AST and inner comments against `before_split.py` (Verification 6). `before.py` cannot be its `--before`, because `TestDirectoryPin`'s span there holds both halves (row 18).
  - One `nodes` run covers every after file, because no name is defined in two of them (row 10). Its `compared-nodes` count equals `before_split.py`'s node count, so a name missing from `--moved` cannot pass.
  - The build's check that `before_split.py` is `before.py` plus one line shows that the class line is the only added line (M6).
  - A scratch line check confirms that every non-blank line of `before.py` from :75 on appears in the new files at least as many times. It catches a lost or edited banner or block comment, and prints how many lines it compared (Verification 7, row 19).
  - The same check runs in the other direction. With each file's header (docstring and imports) removed, the new files' non-blank line multiset and top-level statement count equal `before_split.py`'s from :75 on. A surplus fails as a shortfall does, which catches an overlapping range or a leftover from a retried build.
  - `nodes` names no unnamed top-level statement, such as `INSTALL_RECORD_FAULT_ROWS["install-record-no-wrapper"] = _no_wrapper_row` (:290). The line check, the statement count and `test-ids` cover those instead.
  - `test-ids` unions the two passes, so it cannot see a test that loses its `timing` mark. Only `nodes`' AST comparison and a count do. The count of `@pytest.mark.timing` lines across the new files equals the 15 in `before.py`, and each pass's testcase count equals its baseline count separately.
  - The file that defines `_has_builtin` must be the file that holds the `sys.modules[__name__]` test. A mis-homed `_has_builtin` would still pass, about 10 seconds slower. So the step also checks the file placement and that the test's time in `after.xml` is well under `SHELL_PROBE_TIMEOUT_SECONDS`.
  - Before the real run, a scratch copy of the new files with one banner line removed and one edited must make the line check exit non-zero for each. A scratch copy with one assertion edited must make `nodes` report `node-ast`.
  - Ruff's F401, F821 and F811 show that each file imports exactly what it reads and shadows nothing (Verification 4).
  - A wrong import changes a ruff result or a test outcome, so no separate check of imports is planned.
- **M10: The original must not change under the open PR, and each conflict-prone file has one resolution.** `anchors: G1, G2, row14, row15`
  - The holds (Verification 1) cover the original. If it changes on `origin/main` anyway, git reports a modify/delete conflict, and the recovery runs in this order:
    1. Resolve the conflict by taking `origin/main`'s version of the original, and finish the sync.
    2. Take `<base>` as the synced `git rev-parse HEAD`.
    3. Run `git diff --quiet <base> -- claude/.claude/scripts/tests/test_cursor_adapter_shim.py`, which must exit 0 before the baseline.
    4. Rebuild from Verification 2, since every range is anchored. The rebuild consumes that version, and Verification 8's `git rm` removes it again.
  - Never resolve that conflict by keeping the deletion before step 4 has consumed `origin/main`'s version.
  - A conflict in `select-tests.py`'s importer set keeps every entry from `origin/main`'s side, drops the old shim path, adds the eight new paths and keeps the set sorted. If the set or the shim tests moved or were renamed on that side, the plan stops for the engineer.
  - A conflict in any other edited file takes `origin/main`'s side, then reapplies the one-line swap only if the old name remains. The files are the scripts-tests `conftest.py`, `test_select_tests.py`, `test_cursor_adapter_translate.py` and `docs/scripts.md`. If `origin/main` already re-listed or renamed the shim tests, the plan stops for the engineer.
  - After any resolution, Verification 4 and 8 rerun, with step 8's grep included.
  - The PR body carries the class-to-file map, so the author of a PR written against the old file can re-point it.

**Assumption rows:**
1. Every top-level node and comment from :75 to :4366 falls in exactly one range that Critical files assigns. The ranges hold 4,224 base lines plus the class line, and the 36 blank gaps between ranges hold the other 68 of the 4,292. Lines :1–:74 are the docstring and imports, which the headers replace (M7). [verified: Grep of every top-level def, class, assignment and banner line; Read of each range's boundary lines and of every gap this pass; arithmetic over Critical files' ranges]
2. Sizes. The ranges and their separators give these line counts before any header:
   - outcomes 879;
   - input 547;
   - prologue 424;
   - log 401;
   - hook_run 512;
   - faults 272;
   - pin 655;
   - pin_environment 391;
   - the helper 210.

   A header runs about 40 to 85 lines. Even a header that imported every name the base imports would hold about 85, so the outcomes file lands at most near 964, most likely near 945. The others land near 605, 480, 455, 570, 325, 710, 440 and 255 lines. [verified: arithmetic over row 1's ranges; header sizes are estimates, and Verification 8 measures]
3. The engineer chose to start this split now rather than wait for the `cursor-adapter-package` session's plan. [engineer-verified: "yep, agreed, start now"]
4. The engineer does not want a split at a seam that is not a good one merely to meet the 1,000-line limit, and asked for the architect's view on `TestDirectoryPin`. [engineer-verified: "I don't want to overindex on the 1000 file limt if the seam isn't a good one to split on. what does the architect think?"]
5. Nothing crosses the :3981 seam. [verified: Grep of `self.`, of indented `def _` lines and of class-level assignments in the file; Read of :3407–:3421, :3700–:3729, :3924–:3999 and :4240–:4312; Grep of the class's `def test_` lines]
   - Nothing belongs to `TestDirectoryPin` as a whole: no decorator sits above it (:3411–:3412), its first body line is a test (:3414), and its one class attribute, `REFUSED_INPUTS` (:3609), is read only at :3632.
   - Every `self.` helper the first half calls is defined above :3981: `_sources` (:3471), `_leading_cd_payload` (:3676), `_directory_bash_enters` (:3711), `_pin_directory` (:3842) and `_file_tool_directory` (:3845).
   - Every one the second half calls is defined below it: `_walk_calls` (:3983) through `_stripped_command` (:4141), `_pinned_command` (:4243), `_run_in` (:4249), `_same_as_unprefixed` (:4259) and the `_row_*` methods that :4302 reaches by `getattr`.
   - The halves hold 567 lines (:3413–:3979) and 332 (:3981–:4312). The second half holds 22 test functions, from :3999 to :4306.
6. Names that two or more planned files read, or that a helper name reads. [verified: Grep of each name's references this pass]
   - `SHIM_MODULE`: read across files, for example :981, :1521, :2817, :3960 and :4364. `RECOVERY_SENTENCE`: `_assert_deny_text_rules` (:100) and `TestRecoverySentence` (:111–:113).
   - `OUTPUT_CAP`: outcomes (:356, :1011–:1021) and hook_run (:2443, :2448). `PAYLOAD_CAP`: input (:2026–:2055) and hook_run (:2309).
   - `_scratch` and `_workspace`: helper functions and most files. `_assert_deny_text_rules`: outcomes (:576), input (:1949) and pin (:3440, :3561, :3645).
   - `_probe_ran`: outcomes, input (:1079, :3176), log (:1755), pin (:2109, :3708) and hook_run (:2352). `_run_probe`: outcomes, input (:1069, :1980, :3175), log (:1750) and pin (:2084, :3705). `_run_hook_body`: outcomes (:166, :632–:689, :970–:1026) and input (:1210, :1215).
   - `HOOK_STARTED_TOUCH` and `HOOK_STARTED_THEN_SLEEP`: outcomes (:173–:174, :182) and hook_run (:2318–:2399). `_assert_hook_started`: outcomes (:992, :1001) and hook_run (:2320–:2401).
   - `SHIM_MODULE_EVENTS`: log (:1917) and faults (:2988). `_recorded_wrapper`: outcomes (:731, :741), hook_run (:2152) and faults (:2814).
   - `CANARY`: outcomes (:643–:835), prologue (:1433), log (:1678–:1710), hook_run (:2607–:2654) and faults. `FAULT_PROGRAM`: `_run_fault_program` (:2730). `_run_fault_program`: faults (:2752, :2786) and pin_environment (:4073, :4084).
   - `_candidate_shells`: `CANDIDATE_SHELLS` (:3341). `CANDIDATE_SHELLS`: pin (:3716) and pin_environment (:3342, :4251). `SHELL_PROBE_TIMEOUT_SECONDS`: pin (:3722) and pin_environment (:3334, :4256, :4321).
   - `_walkless_env` and `_pin_prefix`: input (:1132, :1135), log (:1823–:1852), pin and pin_environment. `_environment_line`: `_pin_prefix` (:3384).
   - `_pin`, `_pin_check` and `_prefixed`: pin (:3421–:3978) and pin_environment (:4002–:4247).
7. Names that one planned file reads, so they move with it. [verified: Grep]
   - Outcomes: `_edit_record` (:301, :303, :711, :721), `HOOK_STARTED_THEN_IGNORE_TERM` (:990), `TRANSLATED_TOOLS` (:707), `_usage_args` (:869), and `OUTCOME_ROWS` with every row builder and outcome-ID constant. `TestShimArguments` reads `OUTCOME_ROWS` at :999, besides the failure outcomes' reads (:567–:804).
   - Input: `_framing_cases`, `FRAMING_FIELDS`, `PARSER_EDGES`, `MIDDLE_KEEPS_LEADING_CD` and `_nesting_depth_between_encoder_and_decoder_limits`.
   - Prologue: `SYSTEM_PATH` (:1295, :1316), `_canary_writer`, `FLAG_PROBE` and `_imported_non_standard_names`.
   - Log: `LOG_FIELD_NAMES`, `LOG_WRITER_PROGRAM`, `ALLOWED_TOP_LEVEL_MODULES` and `SEEDED_STARTS`.
   - Hook_run: `CONTENTION_HOG_LIFETIME_SECONDS`, `gnu_wrapper`, `_timed_run`, `_timing_bound`, `_big_command_payload`, `_Clock`, `_StandInProcess`, `stand_in` and `_call_run_wrapped`. Faults: `FAULT_CLASSES` and `_canary_payload`.
   - Pin: `_non_shell_pin_payloads`, `NON_SHELL_PIN_CASES` and `DIRECTORY_INPUT_REFUSAL_TEXT` (:3643).
   - Pin_environment: `_has_builtin`, `SHELL_PROBES` (:4200, :4219, :4329–:4331), `NO_BUILTIN_SHELL_PARAMS` (:4304–:4305), the three `BEHAVIOUR_*` names, `ZSH_ONLY_ROWS`, `_ROW_SHELLS` and `_stand_in_bash_env` (:3997, :4048). `BUILTIN_SHELL_PARAMS` (:3343), which nothing reads, moves with `SHELL_PROBES`, the name it reads.
8. :4321 runs `monkeypatch.setattr(sys.modules[__name__], "SHELL_PROBE_TIMEOUT_SECONDS", 0.2)` and then calls `_has_builtin` (:4322), which reads that name from its own module's globals (:3334). With `_has_builtin` defined in pin_environment and the name imported there from the helper, the patch replaces the binding `_has_builtin` reads. `_directory_bash_enters` (:3722) reads pin's own binding, which no test patches. No other code reads `sys.modules`, `globals()`, `__file__` or a `global`, and no `monkeypatch` call names its target by string. [verified: Read of :3326–:3338, :3711–:3723 and :4315–:4322; Grep]
9. `load_shim()` (:75) runs once, in the helper. `_candidate_shells()` (:3341) calls only `shutil.which` and `Path.exists` (:3317–:3323), so the helper starts no process for it. The shell probes, `_has_builtin` over each candidate (:3342), run only in pin_environment, wherever that module is imported, as they ran wherever the base was imported. [verified: Read of :75 and :3317–:3357]
10. `gnu_wrapper` (:2150) and `stand_in` (:2242) are requested only by `TestShimHookRun` (:2268–:2630). `wrapper` (:2812) is a method fixture of `TestShimFaults`. `require_zsh` (:73) is a plain function the second half calls (:4218–:4239). So no fixture crosses a file, no file binds one, and no named node is defined in two after files. [verified: Grep of `pytest.fixture`, `gnu_wrapper`, `stand_in` and `require_zsh`]
11. `claude/.claude/scripts/tests/` is a package, so a sibling imports as `from .x import`. The install split's files import their helper as `from ._cursor_install_helpers import (...)`, for example `test_cursor_adapter_install_status.py:12`. `from helpers import ...` stays bare, because `pyproject.toml:21` puts `claude/.claude/tests` on `pythonpath`. [verified: .claude/rules/test-tree-packaging.md; Read]
12. The helper's functions hold plain `assert` statements: `_assert_deny_text_rules` (:99–:105), `_run_hook_body` (:128), `_run_probe` (:147), `_assert_hook_started` (:178), `_recorded_wrapper` (:2146), `_pin` (:3396) and `_prefixed` (:3409). Under G3, pytest does not rewrite them there. Every pass and fail stays the same, and each assert that carries a message keeps it. A failing bare assert there loses pytest's operand display. Five `_*.py` helpers in this tree already carry 52 plain asserts, `hooks/tests/_cursor_payloads.py` carries 4, and nothing in `claude/` calls `register_assert_rewrite`. [verified: Read of the cited lines; Grep counts this pass]
13. No class reads another class, and no comment or docstring names a test class. The position words in comments, "row below" at :1767 and :2891, point inside their own planned files. So no comment needs an edit. [verified: Grep for `Test*.` references and for test-class names in comments and docstrings; Grep for above, below, this file and this module]
14. Outside `.claude/plans/`, five sites name the file: `select-tests.py:356`, `test_select_tests.py:1987`, the scripts-tests `conftest.py:49`, `test_cursor_adapter_translate.py:2` and `docs/scripts.md:629`. No module imports from it. [verified: repo-wide Grep excluding `.claude/plans/`; `.github/` and `.claude/rules/` hold no hit]
15. `SCRIPTS_TESTS_IMPORTING_HOOKS_TESTS_MODULES` (`select-tests.py:340–:361`) names the file at :356. Its comment (:337–:339) and those at :284–:285 and :769–:770 already say "directly or through a SCRIPTS_TESTS_DIR helper module". A change to a `HOOKS_TESTS_MODULES_IMPORTED_BY_SCRIPTS_TESTS` member, `_cursor_payloads.py` (:291) among them, selects that set (:974). Any path under `claude/.claude/scripts/`, the helper included, selects all of `SCRIPTS_TESTS_DIR` (:719). `select-tests.py` is a `GLOBAL_TRIGGER_PATHS` member (:547–:551), so this PR's run is the full suite. It passes its own arguments through to pytest (:1354, :1411). [verified: Read and Grep]
16. `test_select_tests.py:1982–:1988` asserts that a `_cursor_payloads.py` change selects two set members, and :1987 names this file. The importer scan (:2024–:2031) requires every `SCRIPTS_TESTS_DIR/test_*.py` that imports `_cursor_payloads` or `_cursor_identity_fixtures` to be a set member, and never reads a `_*.py` helper. Each of the eight files reads a `_cursor_payloads` name directly: outcomes (:189), input (:1037), prologue (:1246), log (:1568), pin (:2063), hook_run (:2157), faults (:2784) and pin_environment (:4042). So the scan requires all eight in the set, and the original must leave the tree before the suite runs. [verified: Read of :1982–:2039; Grep of `make_shim_env(` and `shell_pre_payload(`]
17. Fifteen tests carry `@pytest.mark.timing`: :986 and :997 in `TestShimArguments`, :1375 in `TestShimPrologue`, nine from :2266 to :2434 in `TestShimHookRun`, and :3963, :3970 and :4115 in `TestDirectoryPin`. The Context's 12 is the research subagent's count, which missed the last three. CI runs a parallel pass without the marker and a serial pass with `-m timing -n0`. [verified: Grep; README.md:570]
18. `_move_fidelity.py` facts. [verified: Read]
    - `nodes` names each top-level def, class and single-target assignment (:146–:153), and a node's first line includes its decorators (:156–:158).
    - It compares the source from a node's first line to its last (:142–:143, :306–:307), as an AST and as a comment-token list (:337–:347). A class whose body differs before and after therefore fails `node-ast`.
    - It reports `node-missing` for a name the before file does not define exactly once or no after file defines, and `node-duplicate` for one that the after files define more than once (:292–:303).
    - `test-ids` keys a testcase by its classname parts after the last `test_` part, plus its name (:741–:746). So a class keeps its IDs across files, and a module-level test keys by its name alone.
    - `--rename OLD=NEW` renames an ID equal to OLD or starting with `OLD::` or `OLD[` (:771–:775). One rename per test function therefore covers its parametrized IDs, and a class-wide rename would also rename the first half's IDs.
    - `test-ids` unions repeated `--before` and `--after` files (:929–:930) and fails when neither side has a testcase (:794–:795). `main` takes an argv list (:947). The module imports only the standard library (:12).
19. `nodes` compares only comments inside a node, and no residual remains for `remainder` to compare. The top-level comments are therefore otherwise unchecked: every top-level banner, and the comments at :79, :159–:160, :560, :1764–:1767, :1779, :2138, :3114, :3326 and :3346–:3347 among others. [verified: _move_fidelity.py:344–:347; Grep and Read of the file's banners and comments]
20. `require-diff-size-prompt.sh` denies a `git commit` that brings the branch's diff against `origin/<base>` to 400 or more changed lines, `.claude/plans/` excluded, until the engineer answers `Proceed anyway` and `diff-size-ack.sh` writes the branch's latch. The hook denies that ack command to a `code-writer`. The engineer answered at plan review (row 27), and the hook's latch still needs `diff-size-ack.sh` at commit time. [verified: claude/.claude/hooks/require-diff-size-prompt.sh:4–:14 and :47]
21. `code-writer` does not stage or commit, runs only read-only checks on what it changed, and does not run the full suite. [verified: claude/.claude/agents/code-writer.md:23–:30]
22. This plan's `:N` numbers were measured at `bc9c9194`, the worktree's HEAD at session start and the merge of #1518. Anchors, not `:N`, decide every range (M6), and Verification 2 re-records `<base>`. [verified: the session's git status names `bc9c9194` as HEAD, with only this plan untracked]
23. No file named `_cursor_shim_helpers.py` or `test_cursor_adapter_shim_*.py` exists. [verified: Glob]
24. Of the 29 `# noqa: E402` comments in `claude/.claude/scripts/tests/`, 11 carry the reason "(the import needs the sys.path entry above)", among them `_cursor_install_helpers.py:25` and the install files'. The base's two (:29, :36) carry none. `~/.claude/CLAUDE.md` requires a one-line rationale on an inline suppression. [verified: Grep; ~/.claude/CLAUDE.md, Engineering Judgment's "Ground every choice" bullet]
25. The engineer chose to keep the one-line edits to the scripts-tests `conftest.py:49` and `test_cursor_adapter_translate.py:2`, which the Ask does not name. [engineer-verified: "Keep both edits"]
26. The Ask line's first quote, from an earlier session, is the engineer's own wording. [engineer-verified: "Yes, that is mine"]
27. The engineer chose to ship the projected diff of about 9,170 changed lines as one PR. [engineer-verified: "Proceed anyway"]

## Critical files

Below, `tests/` means `claude/.claude/scripts/tests/`.
- Ranges are anchored per M6. `:N` is a `before.py` line at `bc9c9194` and a guide only. In `before_split.py`, every line from :3981 on sits one line later.
- The `code-writer` dispatch makes every change below except the `git rm` and the plan file's commit, which the main session makes (M1).

Files:
- Create `tests/_cursor_shim_helpers.py`. Its docstring is `"""The loaded hook-shim.py module and the helpers its test files share.`, then a blank line, then :4–:5 verbatim, closing quotes included. Its ranges:
  - `SHIM_MODULE` (:75) through `PAYLOAD_CAP` (:78);
  - `_scratch` (:83) through `_assert_deny_text_rules` (:105);
  - `_probe_ran` (:116) through `_run_probe` (:148);
  - `HOOK_STARTED_TOUCH` (:172) through `HOOK_STARTED_THEN_SLEEP` (:173);
  - `_assert_hook_started` (:177–:178);
  - `SHIM_MODULE_EVENTS` (:1778);
  - `_recorded_wrapper` (:2142–:2147);
  - `CANARY` (:2659) through `FAULT_PROGRAM` (:2705);
  - `_run_fault_program` (:2721–:2740);
  - `_candidate_shells` (:3317–:3323);
  - the comment beginning `# A project choice: the probe runs at collection` (:3326) through `SHELL_PROBE_TIMEOUT_SECONDS` (:3327);
  - `CANDIDATE_SHELLS` (:3341);
  - `_walkless_env` (:3367) through `_pin_prefix` (:3384);
  - `_pin` (:3393) through `_prefixed` (:3410).
- Create `tests/test_cursor_adapter_shim_outcomes.py`, docstring `"""hook-shim.py: the Recovery sentence, the outcome rows, the failure outcomes, and the arguments and the deadline."""`.
  - `TestRecoverySentence` (:108–:113);
  - `_edit_record` (:151–:154);
  - the banner `# --- the outcome rows ---` (:157) through `_hook_row` (:169);
  - `HOOK_STARTED_THEN_IGNORE_TERM` (:174);
  - `_timeout_row` (:181) through `TestShimArguments` (:1027).
- Create `tests/test_cursor_adapter_shim_input.py`, docstring `"""hook-shim.py: payload validation, unknown tools and the payload cap, and the text rules."""`.
  - the banner `# --- payload validation ---` (:1030) through `TestPayloadValidation` (:1238);
  - the banner `# --- unknown tools and the payload cap ---` (:1922) through `TestUnknownToolsAndCap` (:2055);
  - the banner `# --- text rules ---` (:3112) through `TestShimTextRules` (:3311).
- Create `tests/test_cursor_adapter_shim_prologue.py`, docstring `"""hook-shim.py: the prologue, interpreter isolation and the tool-path rule's two conditions."""`.
  - the comment beginning `# The PATH the shim fixes after the install record` (:79) through `SYSTEM_PATH` (:80);
  - the banner `# --- prologue ---` (:1241) through `TestShimPrologue` (:1516);
  - the banner `# --- interpreter isolation ---` (:2999) through `TestShimInterpreterIsolation` (:3109);
  - the banner `# --- the tool-path rule's two conditions ---` (:4338) through `TestToolPathPredicates` (:4366).
- Create `tests/test_cursor_adapter_shim_log.py`, docstring `"""hook-shim.py: the log and the process-start count."""`.
  - the banner `# --- the log ---` (:1519) through `TestShimLogConcurrency` (:1759);
  - the banner `# --- process-start count ---` (:1762) through `ALLOWED_TOP_LEVEL_MODULES` (:1776);
  - the comment beginning ``# POSIX `os.spawn*` is`` (:1779) through `TestShimExecCount` (:1919).
- Create `tests/test_cursor_adapter_shim_hook_run.py`, docstring `"""hook-shim.py: the hook run, its peak memory under endless output, and its in-process rows with a stand-in for Popen."""`.
  - the banner `# --- the hook run ---` (:2135) through `CONTENTION_HOG_LIFETIME_SECONDS` (:2139);
  - `gnu_wrapper` (:2150) through `TestShimHookRun` (:2654).
- Create `tests/test_cursor_adapter_shim_faults.py`, docstring `"""hook-shim.py: shim faults and self_check."""`.
  - the banner `# --- shim faults ---` (:2657) alone;
  - `FAULT_CLASSES` (:2706) through `_canary_payload` (:2718);
  - `TestShimFaults` (:2743–:2996).
- Create `tests/test_cursor_adapter_shim_pin.py`, docstring `"""hook-shim.py: the modes beyond the hook run, and the directory pin."""`.
  - the banner `# --- the modes beyond the hook run ---` (:2058) through `TestModesThatNeedNoWrapper` (:2132);
  - the banner `# --- the directory pin ---` (:3314) alone;
  - `DIRECTORY_INPUT_REFUSAL_TEXT` (:3359–:3364);
  - `TestDirectoryPin` (:3413), whose `before_split.py` span ends at :3979.
- Create `tests/test_cursor_adapter_shim_pin_environment.py`, docstring `"""hook-shim.py: the pin prefix's environment line and its host-PID part, and the prefix under each candidate shell."""`.
  - `_has_builtin` (:3330–:3338);
  - `SHELL_PROBES` (:3342) through `BEHAVIOUR_PARAMS` (:3357);
  - `_stand_in_bash_env` (:3387–:3390);
  - `TestPinEnvironmentLineAndShells`, the inserted class line followed by :3981–:4312, through `test_the_recorded_bash_is_probed_as_having_builtin_and_runs_every_shell_independent_behaviour_row` (:4335).
- Delete `tests/test_cursor_adapter_shim.py` with `git rm`, run by the main session after every file above exists (M1).
- Modify `claude/.claude/scripts/select-tests.py` (M8): :356 becomes these eight paths, in this order: `claude/.claude/scripts/tests/test_cursor_adapter_shim_faults.py`, `..._hook_run.py`, `..._input.py`, `..._log.py`, `..._outcomes.py`, `..._pin.py`, `..._pin_environment.py` and `..._prologue.py`, each written out in full on its own line.
- Modify `tests/test_select_tests.py:1987`: the path literal becomes `"claude/.claude/scripts/tests/test_cursor_adapter_shim_outcomes.py"`.
- Modify `tests/conftest.py:49`: "as in test_cursor_adapter_shim.py." becomes "as in _cursor_shim_helpers.py.".
- Modify `tests/test_cursor_adapter_translate.py:2`: "is in test_cursor_adapter_shim.py." becomes "is in the test_cursor_adapter_shim_*.py files.". The line stays under 130 characters.
- Modify `docs/scripts.md:629`: "`test_cursor_adapter_shim.py`," becomes "the `test_cursor_adapter_shim_*.py` files with their shared `_cursor_shim_helpers.py`,".
- Commit `.claude/plans/split-cursor-adapter-shim-tests.md`, this plan, with the change.

**Reuse:**
- `tests/_move_fidelity.py`'s `nodes`, `test-ids`, `main` and `_load_source`, unchanged (row 18).
- The install split's header shape: `test_cursor_adapter_install_roundtrip.py:1–:33` and `_cursor_install_helpers.py:1–:32` (M7).
- `helpers.py`'s `CLAUDE_DIR` and `HOOKS_DIR`, `_cursor_payloads.py`'s and `_cursor_identity_fixtures.py`'s names, and `conftest.py`'s `require_zsh`, imported unchanged.

Projected diff: about 9,170 changed lines, about 4,800 added and 4,370 deleted. The nine new files hold about 4,785 lines, the deleted file 4,366, and the five edited files change about 17 between them.

## Verification

Rules for every step:
- Run the steps in order from the worktree root.
- `<scratch>` and `<base>` stand for values an earlier call printed, typed out literally.
- Run each pytest pass in the background or with an extended timeout, because it takes several minutes.
- Delete a pass's XML before its run, and read the pytest exit status and the XML's testcase count next to it. A missing, empty or stale XML then fails the step instead of passing it.
- Pin the xdist worker count to the same value for every pass. Rerun a failed testcase alone with `-n0` up to twice on the same side. Only a failure that reproduces after and not before returns to step 3.
- In command output copied into the PR body, replace each absolute home path with `<worktree>`.

1. **Hold and sync.**
   - Run `git fetch origin main`, sync the branch to the new `origin/main` tip per the `git-feature-branch-sync` skill, then print `git rev-parse HEAD` as `<base>`.
   - Ask the `cursor-adapter-phase4` and `cursor-adapter-package` sessions to hold any edit to `test_cursor_adapter_shim.py` until this PR merges. Ask each to say before it edits `select-tests.py`'s importer set or the scripts-tests `conftest.py:49` (G2, M10).
   - Optionally, `gh pr list --state open --limit 200 --json number,headRefName,files` shows other open PRs that touch the file, for the engineer to see.
2. **Baseline.**
   - Run `mktemp -d /tmp/split-cursor-shim.XXXXXX` once for `<scratch>`.
   - Run `git show <base>:claude/.claude/scripts/tests/test_cursor_adapter_shim.py > <scratch>/before.py`.
   - Run `scripts/venv-run.sh pytest claude/.claude/scripts/tests/test_cursor_adapter_shim.py -m "not timing" --junitxml=<scratch>/before.xml`, then the same file with `-m timing -n0 --junitxml=<scratch>/before_timing.xml` (row 17). `grep -E '<(failure|error)[ />]' <file>` prints nothing for each XML file, and each XML's testcase count is printed and nonzero. A failing baseline stops the plan for the engineer.
   - Record `<base>`, both baseline testcase counts and the list of skipped IDs. The counts' sum is the Context's 902 unless `<base>` moved the file.
3. **Build** (the `code-writer` dispatch; M1).
   - Write `<scratch>/before_split.py`, create each file per M6 and M7, write `<scratch>/fidelity.py`, and make M8's edits. Leave the original in place. The build script stops when `git cat-file -e <base>:<path>` finds any destination path at `<base>`.
   - The inputs are `<scratch>` and `<base>`. The dispatch returns the uncommitted tree and the build script's path in `<scratch>`.
   - The prompt names the build script and `ruff --fix` as the specified work. It says the moved ranges and the one class line are dictated and exempt from self-review edits, and that the dictated docstrings stand. It limits the self-review to headers, M8's edits and the two scratch scripts.
   - The prompt says `test_select_tests.py`'s importer-scan params for `_cursor_payloads` and `_cursor_identity_fixtures` are red until step 8's `git rm`, because the original is still in the tree. It forbids running that file, touching the original, and re-adding the old path to the importer set.
   - A failing step 4 through 8 returns to this step as a rewrite by the build script, never as a hand edit of a moved range.
4. **Lint.** `scripts/venv-run.sh ruff check claude/.claude/ claude-skills/ plugins/ evals/` reports nothing. An F821 on a name row 6 does not list stops the plan (M4).
5. **Test-ID parity.**
   - Rerun step 2's two pytest passes over `claude/.claude/scripts/tests/test_cursor_adapter_shim_*.py`, from the same worktree path, to `<scratch>/after.xml` and `<scratch>/after_timing.xml`.
   - Read `<scratch>/fidelity.py` once, to confirm it passes its lists unchanged to `_move_fidelity.main`.
   - `scripts/venv-run.sh python3 <scratch>/fidelity.py test-ids` runs `test-ids --before <scratch>/before.xml --before <scratch>/before_timing.xml --after <scratch>/after.xml --after <scratch>/after_timing.xml --rename <each line of <scratch>/renames.txt>` and exits 0. Its before and after counts are equal (row 18).
   - `<scratch>/renames.txt` holds one `TestDirectoryPin::<test>=TestPinEnvironmentLineAndShells::<test>` line per test function of the new class, 22 at `bc9c9194` (row 5).
6. **Node fidelity** (M9).
   - `scripts/venv-run.sh python3 <scratch>/fidelity.py nodes` runs `nodes --before <scratch>/before_split.py --after <the helper> --after <each of the eight test files> --moved <every top-level name of before_split.py>` and exits 0.
   - Its `compared-nodes` equals `len(_load_source(<scratch>/before_split.py).nodes)`, about 117 at `bc9c9194` by this plan's Grep.
   - The same run re-checks that deleting the class line from `before_split.py` gives back `before.py` byte for byte.
7. **Line check** (row 19). A scratch script, using `collections.Counter`, confirms that every non-blank line of `<scratch>/before.py`, from `SHIM_MODULE = load_shim()` (:75) on, appears in the nine new files at least as many times. It prints the count of lines it compared, which equals the count of those lines in `before.py`, re-derived at `<base>`. It exits non-zero on any shortfall or surplus. Step 7 also runs M9's timing-mark count, `_has_builtin` placement and time bound, and seeded-defect controls, before the real run.
8. **Size, delete, stage and suite.**
   - `wc -l` prints at most 1,000 for every new file (row 2). An outcomes file over 1,000 stops the plan for a revision that moves the outcome-row table to the helper (Approach, alternatives).
   - Run `git rm claude/.claude/scripts/tests/test_cursor_adapter_shim.py`. The file must be gone before the suite, because the importer scan at `test_select_tests.py:2024–:2031` flags a non-member that imports `_cursor_payloads` (row 16).
   - Stage with `git add -- <the helper, the eight test files, select-tests.py, test_select_tests.py, conftest.py, test_cursor_adapter_translate.py, docs/scripts.md and this plan file>` as its own call, because `deny-invisible-commit-content.sh` denies a chained add-and-commit.
   - `git grep -nE --untracked 'test_cursor_adapter_shim([^_]|$)' -- . ':!.claude/plans'` prints nothing (row 14).
   - Run `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py -m "not timing"`, then the same command with `-m timing -n0` (row 17). Every selected test passes. `select-tests.py` changes, so it selects the full suite on its own (row 15), and that run includes `test_select_tests.py`'s importer scan (row 16).
9. **Review, commit and publish.**
   - Run `/code-review` on the staged diff.
   - Any edit after step 3 reruns steps 4 through 8. Step 8's `git rm` runs only on the first pass.
   - After such a rerun, restage and re-review. This applies to `/ready-for-review` rounds too.
   - Ask the engineer for `Proceed anyway` (row 20). On that answer, run `~/.claude/scripts/diff-size-ack.sh` as its own bare call from the worktree root, because it measures the index. Then commit.
   - Run `git push -u origin split-cursor-adapter-shim-tests`, then `/ready-for-review`, which opens the PR.
     - The PR body carries the class-to-file map, each new file's line count, step 5's before and after counts, and step 6's `compared-nodes` line. It lists the skipped IDs from `after.xml` and says CI's first green run is the witness for them and for the shell rows this host does not collect. It says the line-check and wrapper scripts live in `<scratch>` and cannot be re-run by a reviewer. It names the helper's loss of pytest assertion rewriting, which leaves `_assert_deny_text_rules`' message-less asserts failing as a bare `AssertionError`. The map comes from a scratch listing of each new file's top-level classes, saved in `<scratch>` and passed to the skill by path. It names `TestPinEnvironmentLineAndShells` as the second half of `TestDirectoryPin`.
     - The handoff message names `<scratch>` and asks the engineer to remove it after the merge.
     - If a sync after the first push moves HEAD, steps 4 and 8 rerun before the next push.
   - If a sync meets a conflict on the original, `select-tests.py` or the scripts-tests `conftest.py`, apply M10's rule for that file.

## Out of scope

- **Other files over the limit**, the scripts-tests `conftest.py` included. Each is its own split.
- **The 1,000-line enforcing check.** `docs/design-decisions/code-file-line-limit.md:50–:53` defers it until an offender assessment exists.
- **The `cursor-adapter-package` conversion**, and any change to `hook-shim.py` itself.
- **Any edit to `.claude/plans/` files that cite this file's lines.** Their records stay as written.
- **Room for later tests.** The outcomes file lands near 945 lines and leaves about 55. A later addition there past that budget needs its own change, such as moving the outcome-row table to the helper. The PR body's per-file line counts let the `cursor-adapter-phase4` session see each budget.
- **Renaming, rewording, merging or fixing any moved test, helper or comment.** This includes giving the helpers public names, registering the helper for assertion rewriting (row 12), and the `SHELL_PROBE_TIMEOUT_SECONDS` comment about the probe running "at collection", which sits in the helper though the probe runs in pin_environment. The Root forbids editing any moved line.
- **A `comments` subcommand in `_move_fidelity.py`.** This split again needs a scratch check of top-level comments, so promoting it is a fair follow-up. It is a repo change with its own seeded-defect tests.
- **A CHANGELOG entry.** The split changes no hook, skill or script behavior.
