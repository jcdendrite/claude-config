# Split test_cursor_adapter_install.py below the code-file line limit

## Context

Goal: replace `claude/.claude/scripts/tests/test_cursor_adapter_install.py` (6,491 lines at `33589d60`, 871 collected tests) with topic-cohesive test files that are each under the repo's 1,000-line code-file limit, with no test lost, renamed in behavior, or weakened.

Ask: "I'd like you to collaborate with teh cursor-adapter-phase4 session which has a lot of follow-up work and it's too much for taht agent to do" (engineer's words). The specific item, "Decompose claude/.claude/scripts/tests/test_cursor_adapter_install.py (6,506 lines; limit 1,000 per docs/design-decisions/code-file-line-limit.md)", comes from the `cursor-adapter-phase4` session's follow-up list, which that session relayed as agreed with the engineer; it is the peer's wording, not the engineer's.

Why now: PR #1498 (Cursor adapter Phase 4) merged, so the file's names are stable on `origin/main`. The `cursor-adapter-phase4` session is splitting `claude/.claude/scripts/cursor-adapter/cursor-adapter.py` next, and the larger test file is hard to review and edit in the meantime.

Intended outcome: one PR containing this plan and a mechanical move of test code into several new files plus one shared helper module. `select-tests.py` stays correct for every new file. The sibling shim test file is a separate PR and is out of scope here.

Evidence gathered (from a research subagent, not yet design conclusions):
- Precedent: PR #1494 split `test_skills.py` by moving line ranges unchanged into `test_<topic>.py` files plus an underscore-prefixed helper module, and checked fidelity with `claude/.claude/scripts/tests/_move_fidelity.py` and equal collected-ID counts.
- `claude/.claude/scripts/tests/` is a packaged tree, so same-directory sibling imports must be `from .x import`.
- `claude/.claude/scripts/select-tests.py` names this file in `SCRIPTS_TESTS_IMPORTING_HOOKS_TESTS_MODULES`, and `docs/scripts.md` lists it.
- No line-limit enforcement test or exception row exists yet for this file.

## Approach

Replace `claude/.claude/scripts/tests/test_cursor_adapter_install.py` with ten topic files named `test_cursor_adapter_install_<topic>.py` and one shared module, `claude/.claude/scripts/tests/_cursor_install_helpers.py`, in one PR that one `code-writer` dispatch builds (M1, M2). Each file is cut from a scratch copy of the base by line range, so no test is retyped, and each test class moves whole with the constants and functions only its file reads. The largest file lands near 860 lines (row 2).

The helper holds the `World` harness, its two fixtures and the 17 other names that two or more files read or that a helper function reads (M3). Each test file binds the two fixtures by module-level assignment, the form `test_branch_records_lib.py` already uses to keep ruff's F811 quiet (M6, row 9). The original file is deleted, and `select-tests.py`'s importer set and `docs/scripts.md` name the new files instead (M7). `_move_fidelity.py`'s `nodes` and `test-ids` and a scratch line check show that nothing was lost or edited (M8).

Alternatives set aside:
- **The research subagent's nine groups.** Its first group is over the limit before any header (row 2).
- **A residual `test_cursor_adapter_install.py`.** Outside `.claude/plans/`, only two sites name the file, and this PR edits both anyway (row 14). A residual would hold an arbitrary topic under the old name.
- **The fixtures in `claude/.claude/scripts/tests/conftest.py`.** That would make `world` a fixture of every test in the tree, and that conftest already exceeds 1,900 lines (row 24).
- **A sub-package with its own `conftest.py`.** It needs two more `__init__.py` markers under `.claude/rules/test-tree-packaging.md`. Its test files would also leave the importer scan, which reads only `SCRIPTS_TESTS_DIR/test_*.py` (row 13).
- **Importing the fixtures by name.** Ruff's F811 would fire on every test parameter named `world` or `fast_world` (row 9).
- **The harness in `claude/.claude/hooks/tests/_cursor_payloads.py`.** Only these files use `World`, and every edit there selects the whole `SCRIPTS_TESTS_IMPORTING_HOOKS_TESTS_MODULES` set (row 12).
- **One file per class.** The file holds 48 test classes, many under 60 lines, and classes in one section share constants that would then cross files.
- **More than one dispatch.** Every file comes from one base copy and one fidelity run, and the helper's name list decides every file's imports.

**Root:** `claude/.claude/scripts/tests/test_cursor_adapter_install.py` is 6,491 lines, about six and a half times the 1,000-line limit for test files. The split moves its tests into files of at most 1,000 lines, keeps every collected test ID and its outcome, and edits no assertion. `[verified: Read of the file at `84d74417`, ending at :6506, then `git diff 84d74417 33589d60` on it, which removes 15 lines; `wc -l` of the `33589d60` copy printed 6,491; docs/design-decisions/code-file-line-limit.md:19]`

**Givens:**
- G1. pytest makes a fixture-decorated object bound in a test module's namespace a fixture of that module, under its fixture name, whichever module defined it. Reason: pytest owns fixture discovery. `[verified: test_branch_records_lib.py:49–:53 and test_branch_records_archive.py:53 bind helper fixtures this way, and their tests request them by parameter; pytest's own source was not read this pass]`
- G2. Merge is human-only, so the engineer decides when this PR merges relative to others. Reason: the engineer owns merge. `[verified: ~/.claude/CLAUDE.md, Working Style's merge bullet]`
- G3. The split lands after the `cursor-adapter-phase4` session's item 1 branch, `cursor-adapter-claudecode-doc-fix`, which merged as PR #1505 and is on `origin/main` at `33589d60`. It deleted one test in `TestCheckCommand` and edited one tuple in `TestSessionStartReportShape`, both in the planned `reports` file. Other PRs may also edit the file while this PR is open. Reason: their authors own them, and the engineer chose to land that branch first. `[engineer-confirmed: "Sure let's go with (a)"; verified: `gh pr list --state all --head cursor-adapter-claudecode-doc-fix` printed MERGED, and `git diff 84d74417 33589d60` on the file shows both edits; peer-reported: that session holds no uncommitted edit to the file and no Phase 5a class]`

**Mechanisms:**
- **M1: One PR, built by one `code-writer` dispatch and finished by the main session.** `anchors: root, G2, G3, row1, row17, row27`
  - The work stays one dispatch (Approach, "More than one dispatch").
  - Verification is the runbook, in order. `code-writer` runs only step 3, the build, because its charter limits it to editing the files it is given and read-only checks on the file it touched (`claude/.claude/agents/code-writer.md`). The main session runs every other step, and may hand the verbose pytest and fidelity runs to a `general-purpose` subagent with `model: sonnet`.
- **M2: Ten topic files and one helper, each at most 1,000 lines.** Critical files gives each file's ranges and docstring. `anchors: root, row1, row2, row4, row8`
  - Each test class moves whole, with the constants and functions only its file reads. No class is split and no name changes.
  - The topics follow the base's own section banners: install's writes, prerequisites, uninstall, status, the other reports, the sessionStart entry, write failures, unusable inputs, the sandbox grant, and the grant's directory list.
  - Every file shares the `test_cursor_adapter_install_` prefix, so the family that shares the helper stays one glob.
- **M3: The helper holds exactly the names that two or more planned files read, plus the names those names read.** `anchors: G1, row3, row4, row5, row6, row7, row10`
  - The 20 names are `RECORDED_AFTER_PYTHON`, `Result`, `World`, `make_tool_bin`, `world`, `fast_world`, `replace_tool`, `load_adapter_copy_with`, `HOOK_ENV_SURFACE`, `SURFACES`, `run_surface`, `Crash`, `crash_at`, `stop_crashing`, `ORIGINAL_HOOKS`, `hooks_document`, `save_hooks`, `find_entry`, `mutate_remove` and `use_settings` (row 3). Every other name moves with its only reader (row 4).
  - `exploding_callee` never moves to the helper, because its test asserts that the failing frame's file is the test's own (row 6).
  - A name that ruff's F821 shows crossing a file boundary, and that row 3 does not list, stops the plan for a plan revision instead of a guessed home.
  - The helper's docstring takes over the base docstring's second paragraph (:3–:5), which describes the harness every test runs in.
- **M4: Section banners stay with their sections.** `anchors: row1, row8, row21`
  - Each `# --- ... ---` banner goes to the file that takes the first test class after it. Where helper nodes sit between a banner and that class (:1937, :2085, :2579), the banner is a one-line range of its own.
  - A helper range takes only its nodes' lines. A comment directly above a node, with no blank line between, moves with that node: :157 goes to the helper, and :169, :2104 and :4977 go with their status, uninstall and reports nodes.
  - No comment or docstring inside a moved range changes (row 8).
- **M5: Copy by line range from a scratch copy of the base, never retype.** `anchors: G3, row1, row11, row25`
  - Each range's first line is its quoted banner or first node, found by grep in `<scratch>/before.py`. Its last line is the last line of its named last node, from `_move_fidelity.py`'s `_load_source` spans of `<scratch>/before.py`. The script fails when an anchor matches zero or several lines. The `:N` numbers in Critical files are base guides only.
  - One re-runnable scratch script builds each file from nothing: it writes the file's header (M6), then each range in base order from `<scratch>/before.py`, with two blank lines between ranges. A retried build rewrites the file whole and never appends to a leftover.
  - `scripts/venv-run.sh ruff check --select I --fix <file>` then orders the header's imports, the only lines it can move.
  - The original stays in place until the main session's `git rm` (M1).
  - Every before and after pytest run comes from the same worktree path.
- **M6: Headers.** `anchors: G1, row9, row10, row19`
  - Each file opens with its docstring from Critical files, then `from __future__ import annotations`, then imports of exactly the names its code reads, each from the module that defines it.
  - A file that imports from `hooks.tests` copies the base's `sys.path.insert(0, str(CLAUDE_DIR))` line (:30), and its `hooks.tests` imports carry the comments of :31 and :32 verbatim.
  - Each test file imports the helper as `from . import _cursor_install_helpers as install_helpers` and `from ._cursor_install_helpers import (...)`. Both sit above the `sys.path.insert` line, as in `test_branch_records_lib.py:25`. That placement keeps ruff's E402 quiet without a `# noqa`, which the "no other comment" rule below forbids. After its imports it carries `test_branch_records_lib.py:49`'s comment verbatim, then `world = install_helpers.world` and `fast_world = install_helpers.fast_world`.
  - Every test file binds both fixtures, because `fast_world` requests `world` (row 19).
  - A header carries no other comment.
- **M7: Routing and docs.** `anchors: row12, row13, row14, row15`
  - In `select-tests.py`, the ten new paths replace :343 in `SCRIPTS_TESTS_IMPORTING_HOOKS_TESTS_MODULES`, sorted. All ten join, because the status and uninstall files import no `hooks.tests` module directly and depend on `_cursor_payloads.py` through the helper (row 13). The set then holds ten entries from this family.
  - Three comments say the set's members import a hooks-tests module "by name", which goes false for a helper-routed member. Critical files gives each site and its wording. The dispatch finds the three sites by their comment text, because the `:N` numbers are base guides.
  - `docs/scripts.md:544` names the new files in place of the old one.
  - Neither `.claude/plans/cursor-adapter.md` (row 15, Out of scope) nor `test_select_tests.py`, whose tests read the set by its constant name, changes.
- **M8: Fidelity checks.** `anchors: root, row16, row18, row20, row21, row23`
  - `test-ids` compares every test's ID and outcome across both CI passes, before and after (Verification 5).
  - `nodes` compares every moved node's AST and inner comments, in two runs because of the fixture bindings (Verification 6, row 20). Its `compared-nodes` count equals the base's node count minus 2, so a name missing from the `--moved` list cannot pass.
  - A scratch line check confirms that every non-blank line of `<scratch>/before.py` from :45 on appears in the new files at least as many times. It catches a lost or edited banner or block comment, and prints how many lines it compared (Verification 7, row 21).
  - Ruff's F401, F821 and F811 show that each file imports exactly what it reads and shadows nothing (Verification 4).
  - A fixture binding or import that is wrong changes a test outcome or a ruff result, so no separate check of either is planned.
- **M9: The original must not change under the open PR.** `anchors: G2, G3, row14, row15`
  - The peer's hold (Verification 1) covers it. If the original changes on `origin/main` anyway, git reports a modify/delete conflict, and the recovery runs in this order:
    1. Abort the interrupted sync.
    2. Restore the original into the tree.
    3. Fetch, then take `<base>` as `git rev-parse origin/main`.
    4. Rebuild from Verification 2, since every range is anchored.
  - Never resolve that conflict by keeping the deletion.
  - The PR body carries the class-to-file map, so the author of a PR written against the old file can re-point it.

**Assumption rows:**
1. Every top-level node and banner from :45 to :6491 falls in exactly one range that Critical files assigns. The ranges hold 6,363 lines, and the 42 two-line blank separators between them hold the other 84 of the 6,447. `[verified: Grep of every top-level def, class, assignment and banner line this pass, walked range by range; Read of :6488–:6506 at `84d74417`; `#1505` removed 15 lines at about :3031, so the ranges after it sit 15 lines earlier at `33589d60`]`
2. Sizes. The ranges give these line counts:
   - roundtrip 723;
   - prerequisites 805;
   - uninstall 575;
   - status 586;
   - reports 680;
   - session_start 727;
   - write_failures 518;
   - unusable_inputs 593;
   - sandbox_grant 788;
   - sandbox_dirs 211;
   - the helper 157.

   A 30- to 50-line header and the separators bring the largest to about 860 (prerequisites) and 835 (sandbox_grant). The research subagent's first group (`TestInstallRoundTrip`, `TestInstallerTargets`, `TestInstallRecordRoundTrip`, `TestInstallCrashInjection`, `TestFailureAfterTheFirstWrite` and `TestWriteTargetSafety`) spans about 1,100 base lines before any header. `[verified: arithmetic over row 1's ranges; header sizes are estimates, and Verification 8 measures]`
3. Names that two or more planned files read, or that a helper function reads. `[verified: Grep of each name's references this pass]`
   - `World` and `world`: every test file. `fast_world`: every test file but sandbox_dirs. `Result`: `World.run`, `run_surface`, uninstall (:2251), session_start (:3372), unusable_inputs (:3622, :4244, :4299), write_failures (:4540) and sandbox_grant (:5829).
   - `RECORDED_AFTER_PYTHON`: `make_tool_bin` (:107) and prerequisites (:1010). `make_tool_bin`: the `world` fixture (:125) and roundtrip (:426).
   - `replace_tool`: prerequisites (:892–:1066) and session_start (:1626, :1701).
   - `load_adapter_copy_with`: uninstall (:2470), unusable_inputs (:4414, :4719), write_failures (:4597), status (:5049) and reports (:4184–:4221, :5332, :5402).
   - `HOOK_ENV_SURFACE`, `SURFACES` and `run_surface`: prerequisites, session_start, uninstall (:2131–:2132), status, reports and unusable_inputs.
   - `Crash`, `crash_at` and `stop_crashing`: roundtrip (:1970–:2081) and sandbox_grant (:5582–:5619).
   - `ORIGINAL_HOOKS`: roundtrip, prerequisites (:5274), uninstall, reports, write_failures, unusable_inputs and sandbox_grant (:6002).
   - `hooks_document` and `save_hooks`: roundtrip (:330–:412), prerequisites (:1397–:1400), uninstall, status, reports and write_failures (:3824). `find_entry`: prerequisites (:1398) and status (:2728–:2862).
   - `mutate_remove`: prerequisites (:1399) and status's `TAMPER_CASES` (:2641).
   - `use_settings`: unusable_inputs (:4499–:4524), write_failures (:4581) and reports (:4998, :5026, :5346).
4. Names that one planned file reads, so they move with it. `[verified: Grep]`
   - Status: `TAMPER_PHRASES` (:2763), `HOSTILE_EVENT_KEY` and its fragments (:2809–:2861), `expected_tamper_lines` (:2735, :2752), the other eleven `mutate_*` functions, `TAMPER_CASES`, `TAMPER_SHAPES`, `SHAPE_CASE_PAIRS`, `write_raw_log` (:3506–:3554, :5195–:5233) and `LITERAL_SHELL_SELECTING_LABELS`.
   - Session_start: `run_hook_env_process` (:1571, :1729, :1832, :3317–:3486), `SESSION_START_PAYLOAD`, `directory_tree`, `EntryKilled`, `HOST_WALK_CASES` with the other host-walk helpers (:1547–:1824), and `run_hook_env_with_stdin_held_open`.
   - Unusable_inputs: `plant` (:3587, :4314–:4338), `run_without_blocking`, `SETTINGS_SHAPES` and `RECORD_DAMAGE`.
   - Reports: `UNTRANSLATABLE_SETTINGS` (:4998, :5026, :5346) and `SHIM_LOAD_FAILURES`.
   - Prerequisites: `RUN_MODE_FILES` and the two `stow_without_*` functions. Uninstall: `EVENTS_SHARED_WITH_THE_ENGINEER` and `NOTHING_INSTALLED_PREFIX`. Write_failures: `WRITE_FAILURES` and `exploding_callee`.
   - The sandbox names: sandbox_grant reads :5508–:5547's, and sandbox_dirs reads :6300–:6349's. Neither file reads the other's.
5. Every `monkeypatch.setattr` targets `world.adapter`, its `os` or `subprocess`, a loaded shim, or `world.adapter.load_shim()`. Nothing reads `sys.modules` or `globals()` or declares a `global`. So no fixture or test depends on which module holds it, apart from row 6. `[verified: Grep of monkeypatch.setattr( and of sys.modules, __name__, __file__ and global]`
6. `TestAnUnexpectedExceptionEndsAsOneLine` asserts that the failure line names `Path(__file__).name` (:4571) and the frame `exploding_callee` (:4572), whose `raise` is at :4536. `exploding_callee` must therefore share that class's file. `[verified: Read of :4535–:4575]`
7. The helper's code holds no `assert` statement, and `find_entry` raises `AssertionError` explicitly (:2101). Whether pytest rewrites the helper's asserts therefore changes no message. `[verified: Read of :45–:179, :1940–:1956, :2088–:2101, :2582–:2583 and :4438–:4442]`
8. No class reads another class. The position words in comments (:907 "above", :6219 "other row", :6299 and :6325 "below") point inside their own planned file. The :5391 banner's "no other row" speaks of the suite. So no comment needs an edit. `[verified: Grep for Test-class references and for above, below, this file, this module and other row]`
9. `test_branch_records_lib.py:49–:53` and `test_branch_records_archive.py:53` bind helper fixtures by module-level assignment under the comment "The fixtures live once in the helper module. An import would trip F811 on every test parameter that names one." The repo's fixture imports by name (`test_cursor_adapter_generate.py:24`, `test_diff_size_ack.py:43`) bring in a renamed or an autouse fixture, which no test parameter shadows. `[verified: Read; Grep for F811 and noqa: F401]`
10. `claude/.claude/scripts/tests/` is a package, so a sibling imports as `from .x import`, and `test_branch_records_lib.py:25` uses `from . import _x as y`. At least 17 `_*_helpers.py` modules there are imported this way. No `_*.py` module in the tree imports a `hooks.tests` module yet. `[verified: .claude/rules/test-tree-packaging.md; Grep of ^from \._ and of hooks.tests in _*.py]`
11. No file named `_cursor_install_helpers.py` or `test_cursor_adapter_install_*` exists. `[verified: Glob]`
12. `SCRIPTS_TESTS_IMPORTING_HOOKS_TESTS_MODULES` (`select-tests.py:339–:350`) names the file at :343. A change to a `HOOKS_TESTS_MODULES_IMPORTED_BY_SCRIPTS_TESTS` member, `_cursor_payloads.py` (:291) among them, selects exactly that set (:963). A change to `_cursor_identity_fixtures.py` selects all of `SCRIPTS_TESTS_DIR` (:965), and so does any path under `claude/.claude/scripts/`, the helper included (:708). `select-tests.py` is a `GLOBAL_TRIGGER_PATHS` member (:536–:540), so this PR's run is the full suite. `[verified: Read and Grep]`
13. `test_select_tests.py:2024–:2031` requires every `SCRIPTS_TESTS_DIR/test_*.py` that imports `_cursor_payloads` or `_cursor_identity_fixtures` to be a set member. It never reads a `_*.py` helper. The status and uninstall files' ranges read no `hooks.tests` name, so each depends on `_cursor_payloads` only through the helper, and nothing enforces their membership. All ten paths stay in the set. `[verified: Read of test_select_tests.py:2011–:2066; Grep of every hooks.tests name in the base file; staff-sdet's AST walk]`
14. Outside `.claude/plans/`, only `select-tests.py:343` and `docs/scripts.md:544` name the file. `[verified: repo-wide Grep]`
15. `.claude/plans/cursor-adapter.md` names the file as the home of three Phase 5a classes still to be written: `TestCheckStateRemoval` (:3046), `TestSandboxGrantWritten` (:3050) and `TestWorkspaceSandboxReport` (:3051). Its other hits were not classified one by one, and this plan edits none of them. `[verified: Grep of `.claude/plans/cursor-adapter.md` at `33589d60`]`
16. Three tests carry `@pytest.mark.timing` (:1425, :1436, :5496). CI runs a parallel pass without them and a serial pass with `-m timing -n0`. `select-tests.py` passes its own arguments through to pytest (:1343). `[verified: Grep; README.md:583; Read]`
17. `require-diff-size-prompt.sh` denies a `git commit` that brings the branch's diff against `origin/<base>` to 400 or more changed lines, `.claude/plans/` excluded, until the engineer answers `Proceed anyway` and `diff-size-ack.sh` writes the branch's latch. The hook denies that ack command to a `code-writer`. `[verified: claude/.claude/hooks/require-diff-size-prompt.sh:4–:14 and :47–:49]`
18. `_move_fidelity.py` facts. `[verified: Read]`
    - `nodes` reports `node-missing` for a name the before file does not define exactly once or that no after file defines, and `node-duplicate` for one that the after files define more than once (:292–:303). It compares each node's AST and its comments (:337–:347). A single-target assignment counts as a named node (:149–:150).
    - `test-ids` keys a testcase by the classname parts after its last `test_` part, plus its name (:741–:746). It unions repeated `--before` and `--after` files (:929–:930) and fails when neither side has a testcase (:794–:795).
    - `_comment_tokens` lists every comment token with its line (:186–:187). The module imports only the standard library (:12).
19. `fast_world` requests `world` by parameter (:132). The helper module is never collected, so a test file that bound only `fast_world` would fail at setup. `[verified: pytest 8.4.2 _pytest/fixtures.py:1822-1850, via staff-sdet's review]`
20. Each test file's `world` and `fast_world` bindings are named nodes, so one `nodes` run over every after file would report both as `node-duplicate`. Verification 6 checks them in a separate run against the helper alone. `[verified: _move_fidelity.py:149–:150 and :300–:303]`
21. `nodes` compares only comments inside a node, and no residual remains for `remainder` to compare. The top-level comments are therefore otherwise unchecked: every `# --- ... ---` banner, and the block comments at :157, :169, :2104, :2639, :2660 and :4977 among others. `[verified: _move_fidelity.py:344–:347; Grep listing of the file's banners; Read]`
22. CI runs the full suite on every push. `[verified: CLAUDE.md, Commands section]`
23. The Context's count of 871 collected tests is the research subagent's 872 less the one test #1505 deleted. `[verified: baseline pytest at `33589d60` ran 868 tests outside the timing marker and 3 with it, with no failure or error in either XML file; Verification 2 re-records the count at `<base>`, and Verification 5 compares against it]`
24. `claude/.claude/scripts/tests/conftest.py` already exceeds 1,900 lines, and its fixtures reach every test in the tree. `[verified: Grep of its definitions, the last at :1944, and its autouse fixtures at :1640–:1689]`
25. This plan's `:N` line numbers and the other per-range figures were measured at `84d74417`. Rows 1 and 2 and Out of scope's reports budget are re-derived at `33589d60`. At `33589d60` the file is 6,491 lines, and every `:N` after about :3031 sits 15 lines earlier. Anchors, not `:N`, decide every range (M5). `[verified: git diff 84d74417 33589d60 on the file; wc -l of the `33589d60` copy]`
26. The PR may carry a projected diff of this size without a split. The engineer was shown about 13,450 changed lines, measured at `84d74417`, and the Projected diff line below states the figure at `33589d60`. `[engineer-verified: "Proceed anyway"]`
27. #1505's effects on this plan are all in rows 1, 2, 15, 23 and 25, G3, the projected diff and Out of scope's reports budget. The `:N` numbers, the deleted test's 15 lines and `.claude/plans/cursor-adapter.md`'s shifted citations are covered there. The dispatch re-derives every range at its own base through M5's anchors, and Verification 2 records the baseline. `[verified: git diff 84d74417 33589d60 on the three files #1505 and #1508 touched that this plan cites]`

## Critical files

Below, `tests/` means `claude/.claude/scripts/tests/`.
- Ranges are anchored per M5, and `:N` is a base guide only.
- The `code-writer` dispatch makes every change below except the `git rm` and the plan file's commit, which the main session makes (M1).

Files:
- Create `tests/_cursor_install_helpers.py`. Its docstring is `"""The world the cursor-adapter.py install tests run in, and the helpers their files share.`, then a blank line, then :3–:5 verbatim, closing quotes included. Its ranges:
  - `RECORDED_AFTER_PYTHON` (:45) through `load_adapter_copy_with` (:154);
  - the comment beginning `# The three reports that carry the tamper comparison.` (:157) through `SURFACES` (:159);
  - `run_surface` (:174–:179);
  - `Crash` (:1940) through `ORIGINAL_HOOKS` (:1956);
  - `hooks_document` (:2088) through `find_entry` (:2101);
  - `mutate_remove` (:2582–:2583);
  - `use_settings` (:4438–:4442).
- Create `tests/test_cursor_adapter_install_roundtrip.py`, docstring `"""cursor-adapter.py install: its writes and their round trip, the tool-path rule, crash injection and the shim's record."""`.
  - `TestInstallRoundTrip` (:196–:461);
  - the banner `# --- the tool-path rule ---` (:464) through `TestInstallerTargets` (:762);
  - the banner `# --- crash injection ---` (:1937) alone;
  - `TestInstallCrashInjection` (:1959–:2082);
  - the banner `# --- the install record read by the shim ---` (:2544) through `TestInstallRecordRoundTrip` (:2576).
- Create `tests/test_cursor_adapter_install_prerequisites.py`, docstring `"""cursor-adapter.py prerequisites and the contract, the probes, the run-mode line and install's own prerequisite check."""`.
  - the banner `# --- prerequisites and the contract ---` (:765) through `TestRunModeWarning` (:1522);
  - the banner `# --- install's own prerequisite check ---` (:5242) through `TestInstallRefusesAnEntryThatCouldNotRun` (:5288).
- Create `tests/test_cursor_adapter_install_uninstall.py`, docstring `"""cursor-adapter.py ownership, the print set and uninstall, an unusable pre-install file included."""`.
  - the banner `# --- ownership, the print set and uninstall ---` (:2085) alone;
  - the comment beginning `# The events the adapter writes entries under` (:2104) through `TestUninstallOwnership` (:2541);
  - the banner `# --- a pre-install file uninstall cannot use ---` (:4607) through `TestUnusablePreInstallFile` (:4636);
  - the banner `# --- uninstall with nothing installed ---` (:4866) through `TestUninstallWithNothingInstalled` (:4971).
- Create `tests/test_cursor_adapter_install_status.py`, docstring `"""cursor-adapter.py status: the tamper comparison, what it reads from the log, its exit status and its Shell canary."""`.
  - `TAMPER_PHRASES` (:160) through `HOSTILE_EVENT_KEY_FRAGMENTS` (:171);
  - `expected_tamper_lines` (:182–:193);
  - the banner `# --- the tamper comparison and status ---` (:2579) alone;
  - `mutate_matcher` (:2586) through `TestStatusAndCanary` (:2952);
  - the banner ``# --- what `status` reads from the log ---`` (:3492) through `TestStatusLogReading` (:3560);
  - the banner `# --- the exit status of status ---` (:5035) through `TestStatusExitStatus` (:5052);
  - the banner `# --- status's canary for a Shell-selecting registration that never fires ---` (:5133) through `TestShellCanaryFlag` (:5239).
- Create `tests/test_cursor_adapter_install_reports.py`, docstring `"""cursor-adapter.py plain check and what its reports print, the sessionStart report's shape included."""`.
  - the banner `# --- plain check ---` (:2955) through `TestPlantedEntriesAndTopLevelMembers` (:3227);
  - the banner `# --- a shim that does not load ---` (:4164) through `TestUnloadableShimReports` (:4232);
  - the banner `# --- text a report prints beyond a count ---` (:4730) through `TestPrintedGuidance` (:4793);
  - the banner `# --- the comparison beside a failed generation ---` (:4974) through `TestComparisonBesideAFailedGeneration` (:5032);
  - the banner `# --- the top-level members finding's way out ---` (:5055) through `TestStaleContractVersionAndAbsentHooksFile` (:5130);
  - the banner `# --- the sessionStart report's shape ---` (:5291) through `TestSmallBranches` (:5444).
- Create `tests/test_cursor_adapter_install_session_start.py`, docstring `"""cursor-adapter.py's sessionStart entry: its log, its host-walk and cursor_version reports, and the state it writes."""`.
  - the banner `# --- the log's field table ---` (:1525) through `TestLogRotationAndSweep` (:1934);
  - the banner `# --- the sessionStart check's writes ---` (:3230) through `TestFirstCallMark` (:3489);
  - the banner `# --- a sessionStart entry whose payload stream never closes ---` (:5447) through `TestHookEnvPayloadHeldOpen` (:5503).
- Create `tests/test_cursor_adapter_install_write_failures.py`, docstring `"""cursor-adapter.py write failures: a failure after the first write, the write targets, fsync and an uncaught exception."""`.
  - the banner `# --- a failure after the first write ---` (:3695) through `TestWriteTargetSafety` (:4074);
  - `exploding_callee` (:4535) through `TestAnUnexpectedExceptionEndsAsOneLine` (:4604);
  - the banner `# --- fsync ---` (:4796) through `TestFlushToDisk` (:4863).
- Create `tests/test_cursor_adapter_install_unusable_inputs.py`, docstring `"""cursor-adapter.py inputs it cannot use: a damaged record, config directory, manifest key, planted file or file shape."""`.
  - the banner `# --- a record or a comparison that cannot be used ---` (:3563) through `TestDamagedRecord` (:3692);
  - the banner `# --- the refusals of validate_config_directory ---` (:4077) through `TestConfigDirectoryRefusals` (:4161);
  - the banner `# --- a manifest key that is not a clean path ---` (:4235) through `SETTINGS_SHAPES` (:4435);
  - `TestFailuresBeforeTheFirstWrite` (:4445–:4532);
  - the banner `# --- a hooks file whose version the adapter does not write ---` (:4639) through `TestRecordTheShimRefuses` (:4727).
- Create `tests/test_cursor_adapter_install_sandbox_grant.py`, docstring `"""cursor-adapter.py's user-level sandbox.json grant, and a grant left pending."""`.
  - the banner `# --- the user-level sandbox.json grant ---` (:5506) through `TestGrantPending` (:6293).
- Create `tests/test_cursor_adapter_install_sandbox_dirs.py`, docstring `"""The directories cursor-adapter.py's sandbox grant lists, each written by an identity consumer."""`.
  - the banner `# --- the directories the grant lists, written by each identity consumer ---` (:6296) through `TestSandboxWritableDirs` (:6506).
- Delete `tests/test_cursor_adapter_install.py` with `git rm`, run by the main session after every file above exists (M1).
- Modify `claude/.claude/scripts/select-tests.py` (M7).
  - The comment at :337–:338 becomes "SCRIPTS_TESTS_DIR test files that import a HOOKS_TESTS_MODULES_IMPORTED_BY_SCRIPTS_TESTS member by name, directly or through a SCRIPTS_TESTS_DIR helper module.", wrapped as `#` lines.
  - The comments at :284–:285 and :757–:759 gain the same "directly or through a SCRIPTS_TESTS_DIR helper module" phrase.
  - :343 becomes the ten `claude/.claude/scripts/tests/test_cursor_adapter_install_*.py` paths, sorted.
- Modify `docs/scripts.md:544`: "`test_cursor_adapter_generate.py` and `test_cursor_adapter_install.py`" becomes "`test_cursor_adapter_generate.py` and the `test_cursor_adapter_install_*.py` files with their shared `_cursor_install_helpers.py`".
- Commit `.claude/plans/split-cursor-adapter-install-tests.md`, this plan, with the change.

**Reuse:**
- `tests/_move_fidelity.py`'s `nodes` and `test-ids`, unchanged. The scratch scripts import its `_load_source` (row 18).
- `test_branch_records_lib.py:49`'s fixture-binding comment and form (row 9).
- `helpers.py`'s `CLAUDE_DIR` and `ambient_umask`, and `_cursor_payloads.py`'s builders, imported unchanged.

Projected diff: about 13,420 changed lines, about 6,925 added and 6,495 deleted. The eleven new files hold about 6,905 lines, the deleted file 6,491, and `select-tests.py` and `docs/scripts.md` change about 20 between them.

## Verification

Run these steps in order from the worktree root. `<scratch>` and `<base>` stand for values an earlier call printed, typed out literally. Each pytest run takes several minutes, so it runs in the background or with an extended timeout. A killed run is repeated before its XML is trusted. Command output copied into the PR body has each absolute home path replaced by `<worktree>`.

1. **Hold and merge check.**
   - After `git fetch origin main`, `gh pr list --state merged --base main --head cursor-adapter-claudecode-doc-fix --json number,url` prints one PR. An empty result means the doc-fix change is not on `origin/main`. The plan then stops and tells the engineer (G3).
   - Sync the branch to the new `origin/main` tip per the `git-feature-branch-sync` skill, then print `git rev-parse HEAD` as `<base>`.
   - Ask the `cursor-adapter-phase4` session to hold any edit to `test_cursor_adapter_install.py`, its Phase 5a classes included, until this PR merges (G3).
   - Optionally, `gh pr list --state open --limit 200 --json number,headRefName,files` shows other open PRs that touch the file, for the engineer to see.
2. **Baseline.**
   - Run `mktemp -d /tmp/split-cursor-install.XXXXXX` once for `<scratch>`.
   - Run `git show <base>:claude/.claude/scripts/tests/test_cursor_adapter_install.py > <scratch>/before.py`.
   - Run `scripts/venv-run.sh pytest claude/.claude/scripts/tests/test_cursor_adapter_install.py -m "not timing" --junitxml=<scratch>/before.xml`, then the same file with `-m timing -n0 --junitxml=<scratch>/before_timing.xml` (row 16). `grep -E '<(failure|error)[ />]' <file>` prints nothing for each XML file. A failing baseline stops the plan for the engineer.
   - Record `<base>` and both baseline testcase counts (row 23).
3. **Build** (the `code-writer` dispatch; M1).
   - Create each file per M5 and M6 and make M7's edits. Leave the original in place. The build script stops when `git cat-file -e <base>:<path>` finds any destination path at `<base>`.
   - The inputs are `<scratch>` and `<base>`. The dispatch returns the uncommitted tree and the build script's path in `<scratch>`.
   - The prompt names the build script and `ruff --fix` as the specified work. It says the moved ranges are verbatim and exempt from self-review edits, and that the dictated docstrings stand. It limits the self-review to headers, M7's edits and docstrings.
   - A failing step 4 through 8 returns to this step as a rewrite by the build script, never as a hand edit of a moved range.
4. **Lint.** `scripts/venv-run.sh ruff check claude/.claude/ claude-skills/ plugins/ evals/` reports nothing. An F821 on a name row 3 does not list stops the plan (M3).
5. **Test-ID parity.** Rerun step 2's two pytest passes over `claude/.claude/scripts/tests/test_cursor_adapter_install_*.py`, from the same worktree path, to `<scratch>/after.xml` and `<scratch>/after_timing.xml`. Then `scripts/venv-run.sh python3 claude/.claude/scripts/tests/_move_fidelity.py test-ids --before <scratch>/before.xml --before <scratch>/before_timing.xml --after <scratch>/after.xml --after <scratch>/after_timing.xml` exits 0, and its before and after counts are equal (row 18).
6. **Node fidelity** (row 20).
   - `scripts/venv-run.sh python3 claude/.claude/scripts/tests/_move_fidelity.py nodes --before <scratch>/before.py --after <the helper> --after <each of the ten test files> --moved <every top-level name but world and fast_world>` exits 0. The `--moved` list comes from `_load_source(<scratch>/before.py)`. Its `compared-nodes` equals `len(_load_source(<scratch>/before.py).nodes) - 2`.
   - The same command with `--after <the helper>` alone and `--moved world fast_world` exits 0 with `compared-nodes: 2`.
7. **Line check** (row 21). A scratch script, using `collections.Counter`, confirms that every non-blank line of `<scratch>/before.py`, from the first line of `RECORDED_AFTER_PYTHON` on, appears in the eleven new files at least as many times. It prints the count of lines it compared, which equals the count of those lines in `before.py` (5,190 at `33589d60`, 5,202 at `84d74417`, re-derived at `<base>`). It exits non-zero on any shortfall.
8. **Size, delete, stage and suite.**
   - `wc -l` prints at most 1,000 for every new file (row 2).
   - Run `git rm claude/.claude/scripts/tests/test_cursor_adapter_install.py`. The file must be gone before the suite, because the importer scan at `test_select_tests.py:2025-2031` flags a non-member that imports `_cursor_payloads`.
   - Stage with `git add -- <the ten test files, the helper, select-tests.py, docs/scripts.md and this plan file>` as its own call, because `deny-invisible-commit-content.sh` denies a chained add-and-commit.
   - `git grep -nE --untracked 'test_cursor_adapter_install([^_]|$)' -- . ':!.claude/plans'` prints nothing (row 14).
   - Run `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py -m "not timing"`, then the same command with `-m timing -n0` (row 16). Every selected test passes. `select-tests.py` changes, so it selects the full suite on its own (row 12), and that run includes `test_select_tests.py`'s importer scan (row 13).
9. **Review, commit and publish.**
   - Run `/code-review` on the staged diff. Any edit after step 3 reruns steps 4 through 8, without step 8's `git rm` after the first pass, then restages and re-reviews. This applies to `/ready-for-review` rounds too.
   - Ask the engineer for `Proceed anyway`. On that answer, run `~/.claude/scripts/diff-size-ack.sh` as its own bare call from the worktree root, because it measures the index (row 17). Then commit.
   - Run `git push -u origin split-cursor-adapter-install-tests`, then `/ready-for-review`, which opens the PR.
     - The PR body carries the class-to-file map, step 5's before and after counts and step 6's `compared-nodes` line. The map comes from a scratch listing of each new file's top-level classes, saved in `<scratch>` and passed to the skill by path.
     - The handoff message names `<scratch>` and asks the engineer to remove it after the merge.
     - If a sync after the first push moves HEAD, steps 4 and 8 rerun before the next push.
   - If the original changes on `origin/main` before the merge, apply M9's conflict rule.

## Out of scope

- **Splitting `test_cursor_adapter_shim.py`.** It is a separate PR, per the Ask.
- **Other files over the limit**, the scripts-tests `conftest.py` included (row 24). Each is its own split.
- **The 1,000-line enforcing check.** `docs/design-decisions/code-file-line-limit.md` defers it until an offender assessment exists.
- **Any edit to `.claude/plans/cursor-adapter.md`.** Its records stay as written, and its Phase 5a destination lines belong to the `cursor-adapter-phase4` session's plan (row 15). The main session relays the class-to-file map to that session instead. The PR body's class-to-file map assigns each a destination and a line budget:
  - `TestCheckStateRemoval` fits `test_cursor_adapter_install_reports.py`, which holds plain `check` and lands near 730 lines, leaving about 270.
  - `TestSandboxGrantWritten` and `TestWorkspaceSandboxReport` do not fit in `test_cursor_adapter_install_sandbox_grant.py`. That session adds a new file for them, such as `test_cursor_adapter_install_sandbox_workspace.py`, and the PR body's map says so. They are the same size class as the grant file's existing tests, about 20 lines per test. `.claude/plans/cursor-adapter.md` lists more than 30 rows under its `TestWorkspaceSandboxReport` entry. The grant file lands near 835 lines, leaving about 165.
- **A guard on members of `SCRIPTS_TESTS_IMPORTING_HOOKS_TESTS_MODULES` that import a hooks-tests module only through a helper.** The importer scan sees only direct imports in `test_*.py`, so the status and uninstall files' membership is kept by hand (row 13). A missed local selection still runs in CI's full suite (row 22). The fix is a `test_select_tests.py` change with its own cases.
- **A `comments` subcommand in `_move_fidelity.py`.** This is the third split to need a scratch check of top-level comments, so promoting it is a fair follow-up. It is a repo change with its own seeded-defect tests.
- **Renaming, rewording, merging or fixing any moved test or comment**, including the :5391 banner's "no other row" (row 8). The Root forbids editing any assertion.
- **A CHANGELOG entry.** The split changes no hook, skill or script behavior.
