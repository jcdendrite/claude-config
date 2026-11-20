# Split `test_cursor_adapter_gate_matrix.py` into topic files and a shared helper

## Context

Goal: move the 2,388-line `claude/.claude/hooks/tests/test_cursor_adapter_gate_matrix.py` into several files under the repository's 1,000-line limit, with no change to any test body, assertion, fixture or parametrization, and with the same collected test IDs before and after.

Ask: "I'd like you to collaborate with teh cursor-adapter-phase4 session which has a lot of follow-up work and it's too much for taht agent to do" and "message the peer please. ignore the cleanup". The item itself came from that peer session, in its own words and not the engineer's: split this file "as a pure move like #1519 and #1537", with the engineer's approval for sending it, per the peer.

Why now: the file is past the size limit, and a pending package-conversion PR for `cursor-adapter.py` edits `_cursor_payloads.py`, so moved helpers cannot go there (it is 969 lines).

Constraints the peer relayed (the peer's words, not the engineer's):

- Leave `claude/.claude/hooks/tests/_cursor_payloads.py` alone.
- Leave `.claude/plans/cursor-adapter.md` unedited. The PR body says the Phase 5a session retargets its file names.
- Targets: delete the file, create `test_cursor_adapter_gate_matrix_<topic>.py` files plus one shared helper under `claude/.claude/hooks/tests/`, edit `CURSOR_ADAPTER_READING_TESTS` in `claude/.claude/scripts/select-tests.py` (`:41-44`) to list every new test file, and edit the tests bullet at `docs/scripts.md:629`.
- Do not touch helper-only import enforcement in `select-tests.py`, pytest assertion rewriting in the conftest files, or `hook-shim.py`.
- The matrix section is the hard seam, and the peer's estimate (from banner lines, not a measured cut) suggested the data types in the helper and the test classes in topic files. The peer asked for the architect's view of the seam.
- Tier: cooperative.

Measured facts from exploration (line numbers from the file at the base commit):

- Banners: `:75` builders, `:168` what a hook receives, `:461` the matrix, `:738` triggers, `:1621` leading cd, `:1923` generated entries, `:2157` flags, `:2230` session id.
- The matrix and triggers banners together span `:461`-`:1620`, which is 1,160 lines. Only four test classes sit there, 281 lines in total (`TestGateMatrix` `:1306`-`:1503`, `TestInformationalArms` `:1506`-`:1540`, `TestDeclinedSessionHooksImportArm` `:1569`-`:1584`, `TestChildPayloadImportArm` `:1587`-`:1618`). The other 879 lines are data types, tables and helpers.
- `TestGateMatrix` is 198 lines, so it is not itself the over-limit unit.
- Names read by more than one banner section: `GATE_CLASSES`, `DIRECT_HOOK_TIMEOUT_SECONDS`, `World` with `world` and `assert_no_effects`, `Registration`, `ALL_REGISTRATIONS`, `EXCEPTIONS`, `Trigger` with `TRIGGERS`, `_const`, `_stage_a_change`, `_require_worktree_sentinel`, `_run_outside`, `_direct`, `_decision`, `_arm_args`. The builders banner (`:75`-`:458`) and the session-id banner (`:2230` to the end) read no matrix names.
- Import-time work (`ALL_REGISTRATIONS`, `EXCEPTIONS`, `TRIGGER_CASES`, `DECLINED_SESSION_REGISTRATIONS`, `TOOL_EVENT_REGISTRATIONS`, `DENY_OUTCOMES`) runs once per importing module, so it must not be copied into more than one file.
- The `adapter` fixture import is read only by classes in the generated-entries banner. No `pytestmark` and no timing marks exist. `world` is the only fixture defined here.
- `select-tests.py` selects the whole hooks tests directory for any path under `claude/.claude/hooks`, so a new test file or helper in `hooks/tests/` needs no routing entry, but every new test file that reads the adapter must be in `CURSOR_ADAPTER_READING_TESTS`. A helper must not be listed there.
- `test_select_tests.py` reads `CURSOR_ADAPTER_READING_TESTS` by constant and holds no literal of the old file name. Plans under `.claude/plans/` cite the old name and stay as written.

## Approach

Replace `claude/.claude/hooks/tests/test_cursor_adapter_gate_matrix.py` with six topic files named `test_cursor_adapter_gate_matrix_<topic>.py` and one shared module, `claude/.claude/hooks/tests/_cursor_gate_matrix_helpers.py`, in one PR that one `code-writer` dispatch builds (M1, M2). The seam runs between the matrix's harness and its arm tests: the helper takes the registrations, the `World` a hook runs in with its `world` fixture, the deny-trigger table and the hook runners, which the arms, leading_cd, generated_entries and trigger_flags files read (M3). The arms file keeps the four matrix test classes with every table and payload builder only they read, and gains the effect check's positive controls and the bootstrap row (M2). The helper lands near 545 lines and the arms file, the largest, near 856 (row 2).

Each file is cut by line range from a scratch copy of the base, so no line is retyped, edited or added (M5). Three files bind `world` from the helper by module-level assignment, the form the install split's files use (M6, row 7). The original file is deleted. `CURSOR_ADAPTER_READING_TESTS` and its comment in `select-tests.py`, one test name in `test_select_tests.py` and `docs/scripts.md:629` name the new files instead (M7). `_move_fidelity.py`'s `nodes` and `test-ids` and a scratch line check show that nothing was lost or edited (M8).

On the seam the peer relayed: the helper does hold the shared types and data, as the peer suggested, but its members are chosen by which files read them, not by kind (M3). The arms file holds the 281 lines of matrix test classes beside the roughly 360 lines of tables and builders that only those classes read, so the matrix's tests and their expectations stay in one file.

Alternatives set aside:
- **The peer's types-only helper (`Registration`, `World`, `Trigger` and `WriteTrigger`).** Leading cd, generated entries and the trigger flags also read `ALL_REGISTRATIONS`, `EXCEPTIONS`, `TRIGGERS`, `TRIGGER_TOOL_CLASSES`, `_const` and four runners (row 3), so those must be shared too. `WriteTrigger`'s only instances are in `INFORMATIONAL_WRITE_TRIGGERS`, which only the arms read (row 4), so moving the type alone parts it from its one table.
- **Every matrix table in the helper, leaving the arms file only its four classes.** The helper would grow toward 900 lines of tables that one file reads, and each `TestGateMatrix` meta-test would compare lists defined in another file.
- **Two helper modules, one for the harness and one for the trigger table.** One helper lands near 545 lines (row 2), so the split buys nothing under the limit. The trigger table's type and setup functions take a `World`, so the second module would only import the first.
- **The base's banners as every file boundary.** The leading-cd file would then hold `TestEffectDetectorsHavePositiveControls` and `TestBootstrapProjectPluginsRow`, neither of which runs a leading cd (M2).
- **A separate effects file for those two classes.** It would be a file of about 175 lines whose subject, the effect check every arm asserts, lives in the arms file.
- **The trigger flags in the arms file.** The drawn and inert flags serve the attended acceptance run, which no arm reads, and with them the arms file would land near 940 lines.
- **The builders as their own file.** It would hold one class and its two key tables, 91 lines, in a file of about 125 lines, apart from `TestTranslatedPayload`, which compares the same payloads and twins.
- **The session-id section in the payloads file.** It reads no name the payloads sections define, and it has its own banner.
- **The `world` fixture in `claude/.claude/hooks/tests/conftest.py`.** That would make `world` a fixture of every hooks test, a wider scope than three files need.
- **Importing `world` by name.** Ruff's F811 would fire on every test parameter named `world` (row 7).
- **The harness in `_cursor_payloads.py`.** The pending package-conversion PR edits that file (G2).
- **A residual `test_cursor_adapter_gate_matrix.py`.** Outside `.claude/plans/`, two sites name the file and this PR edits both (row 12). A residual would hold an arbitrary topic under the old name.
- **`assert_no_effects` in the helper beside `World`.** Once the effect controls join the arms, only the arms read it (row 4), and in a test module its asserts keep pytest's rewriting (G3, row 9).
- **More than one dispatch.** Every file comes from one base copy and one fidelity run, and the helper's name list decides every file's imports.

**Root:** `claude/.claude/hooks/tests/test_cursor_adapter_gate_matrix.py` is 2,388 lines, over twice the 1,000-line limit for test files. The split moves its tests into files of at most 1,000 lines and keeps every collected test ID and its outcome. Three invariants hold: every non-blank line from :71 on moves unchanged into exactly one new file; the arms file takes the base docstring (:1–:7) whole; and no line is added to moved code. [verified: Read of the whole file at `1b4b117c`, ending at :2388; docs/design-decisions/code-file-line-limit.md:19]

**Givens:**
- G1. Merge is human-only, so the engineer decides when this PR merges relative to others. Reason: the engineer owns merge. [verified: CLAUDE.md, "AI agents: don't merge your own PRs"]
- G2. The `cursor-adapter-phase4` session owns the adapter's follow-up work, Phase 5a included, and a pending package-conversion PR edits `claude/.claude/hooks/tests/_cursor_payloads.py` (969 lines). Other PRs may also edit the files this PR touches while it is open. Reason: their authors own them. [unverified: peer-reported in the Context; the line count is the exploration subagent's]
- G3. pytest rewrites `assert` statements only in `conftest.py` files, modules matching `python_files`, modules named on the command line and modules registered for rewriting, so a `_*.py` helper's asserts are not rewritten. Reason: pytest owns assertion rewriting. [verified: `pyproject.toml` sets no `python_files` and nothing under `claude/` calls `register_assert_rewrite` (Grep this pass); the rule itself is the merged shim plan's G3 reading of `_pytest/assertion/rewrite.py:227–:259`, not re-read this pass]
- G4. pytest makes a fixture-decorated object bound in a test module's namespace a fixture of that module, whichever module defined it. Reason: pytest owns fixture discovery. [verified: `claude/.claude/scripts/tests/test_cursor_adapter_install_roundtrip.py:35–:37` binds `world` this way and its tests request it by parameter (:42); pytest's own source was not read this pass]

**Mechanisms:**
- **M1: One PR, built by one `code-writer` dispatch and finished by the main session.** `anchors: root, G1, row18, row19`
  - The work stays one dispatch (Approach, "More than one dispatch").
  - Verification is the runbook, in order. `code-writer` runs only step 3, the build, because its charter limits it to editing the files it is given and running narrowly scoped read-only checks (row 19). The main session runs every other step. It may hand the verbose pytest and fidelity runs to a `general-purpose` subagent with `model: sonnet`.
- **M2: Six topic files and one helper, each at most 1,000 lines.** Critical files gives each file's ranges and docstring. `anchors: root, row1, row2, row4, row11, row23, row24`
  - Every class moves whole, with the names only its file reads. No name changes and no line is added.
  - The topics:
    - payloads: the builders and what a hook receives;
    - arms: the matrix's and the triggers' four test classes, the effect check's positive controls, and the bootstrap row;
    - leading_cd: the leading-cd verdict equality;
    - generated_entries: the generated matchers and the Recovery section;
    - trigger_flags: the inert and drawn flags;
    - session_id: one session id across the shim, the library and the Shell rewrite.
  - The builders section joins "what a hook receives" because its one class pins the Cursor payloads and twins that `TestTranslatedPayload` compares.
  - `TestEffectDetectorsHavePositiveControls` and `TestBootstrapProjectPluginsRow` sit under the base's leading-cd banner, but neither runs a leading cd. The first shows that the arms' effect check catches each effect class. The second is the one arm whose effects differ from its reference run, and `NO_WRITE_TRIGGER_YET` names it as the row that reaches that hook's write (:1102). Both join the arms.
  - Every boundary is a topic boundary that would hold without the limit, which follows the preference quoted in row 23 (row 24).
  - Every file shares the `test_cursor_adapter_gate_matrix_` prefix, so the family stays one glob.
- **M3: The helper holds the names that two or more planned files read, plus the names those names read, plus the trigger table's two coverage lists.** `anchors: G3, row3, row4, row5, row9, row11`
  - The 26 names by that rule are `GATE_CLASSES`, `DIRECT_HOOK_TIMEOUT_SECONDS`, `Registration`, `_registrations`, `ALL_REGISTRATIONS`, `_exceptions`, `EXCEPTIONS`, `TIME_VALUE_PATTERN`, `World`, `world`, `_const`, `_stage_a_change`, `_memory_path`, `_write_memory_index`, `_require_worktree_sentinel`, `_existing_file_in_the_main_checkout`, `Trigger`, `_bash`, `_read`, `TRIGGERS`, `TRIGGER_TOOL_CLASSES`, `_row_matcher`, `_arm_args`, `_run_outside`, `_direct` and `_decision` (row 3).
  - `NO_TRIGGER_YET` and `NO_TRIGGER_TOOL_CLASSES` break that rule on purpose, though only the arms read them.
  - Both stay directly below `TRIGGERS` in the helper, beside `TRIGGER_TOOL_CLASSES`.
  - The comment at :984 says "no trigger above" of `TRIGGERS`, so this placement keeps it true (row 11).
  - `assert_no_effects` moves to the arms, its only reader (row 4). There its asserts keep pytest's rewriting (G3).
  - Each import-time table is defined in one module, so each runs once per process (row 5): `ALL_REGISTRATIONS` and `EXCEPTIONS` in the helper, `TRIGGER_CASES` and `DECLINED_SESSION_REGISTRATIONS` in the arms, and `TOOL_EVENT_REGISTRATIONS` and `DENY_OUTCOMES` in generated_entries.
  - The helper's one plain assert, in `_decision`, loses rewriting (row 9). Verification 9 names that in the PR body.
  - `world` is the only fixture the helper defines (row 6).
  - A name that ruff's F821 shows crossing a file boundary, and that rows 3 and 4 do not place, stops the plan for a plan revision instead of a guessed home.
- **M4: Section banners and comments stay with their sections.** `anchors: row1, row11, row21`
  - Each top-level `# --- ... ---` banner goes to the file that takes the first test class after it. The matrix (:461) and triggers (:738) banners go to the arms as one-line ranges of their own, because helper nodes sit between each and `TestGateMatrix`. Every other banner opens its file's single range.
  - A comment directly above a node, with no blank line between, moves with that node. So :72, :575–:576, :812 and :984 go to the helper, and :503–:504, the table comments from :1074 to :1132 and :1558–:1560 go to the arms.
  - No comment or docstring inside a moved range changes (row 11).
  - The arms file takes the base docstring whole, because its two notes (:3–:7) describe the arms.
- **M5: Copy by line range from a scratch copy of the base, never retype.** `anchors: row1, row16, row20, row22`
  - Every range is cut from `<scratch>/before.py`.
    - Its first line is its quoted banner or comment. Where it has none, the first line is that of its first node, decorators included.
    - Its last line is the last line of its named last node, from `_move_fidelity.py`'s `_load_source` spans.
    - The script fails when an anchor matches zero or several lines.
    - The `:N` numbers in Critical files are guides only.
  - One re-runnable scratch build script writes each file from nothing: its header (M6), then each range in base order, with two blank lines between ranges. A retried build rewrites each file whole and never appends to a leftover.
  - The arms file's docstring is `before.py`'s :1–:7, written by line slice.
  - `scripts/venv-run.sh ruff check --select I --fix <file>` then orders each header's imports, the only lines it can move.
  - The build also writes two scratch scripts. `<scratch>/fidelity.py` calls `_move_fidelity.main` with argv lists it builds from `_load_source(<scratch>/before.py)`, so no command line carries about 110 names. `<scratch>/line_check.py` runs Verification 7.
  - The original stays in place until the main session's `git rm` (M1).
  - Every before and after pytest run comes from the same worktree path.
- **M6: Headers.** `anchors: G4, row6, row7, row8, row25`
  - Each file opens with its docstring from Critical files, then `from __future__ import annotations`, then imports of exactly the names its code reads, each from the module that defines it.
  - Same-directory modules import in relative form: `from ._cursor_payloads import (...)`, `from ._cursor_identity_fixtures import ...` and `from ._cursor_gate_matrix_helpers import (...)`. `from helpers import` stays bare. No file carries a `sys.path` line (row 8).
  - The arms, leading_cd and trigger_flags files, whose tests request `world`, also import `from . import _cursor_gate_matrix_helpers as gate_matrix_helpers`. After their imports they carry the comment `# The fixture lives once in the helper module. An import would trip F811 on every test parameter that names it.` and then `world = gate_matrix_helpers.world` (row 7). The comment is the install files' own, made singular because one fixture is bound.
  - generated_entries imports `adapter_under_empty_home` from `._cursor_payloads` with the base's ``# noqa: F401 (the `adapter` fixture)`` comment verbatim (:45, row 6).
  - No other file binds or imports a fixture. A header carries no other comment.
- **M7: Routing and docs.** `anchors: row12, row13, row14, row15`
  - In `select-tests.py`, the six new paths replace :42 in `CURSOR_ADAPTER_READING_TESTS`, sorted, because each reads the adapter's shim or data files (row 15). The helper is not listed: it is no test file, and a change to it already selects `HOOKS_TESTS_DIR` (row 13).
  - The constant's comment (:37–:38) drops "two", and says the tests run the shim "or" read its JSON files, because trigger_flags reads them without running the shim (row 15).
  - `test_select_tests.py:2037`'s test name drops "two" (row 14). Its body reads the constant and does not change.
  - `docs/scripts.md:629` names the new family and its helper.
  - `HOOKS_TESTS_MODULES_IMPORTED_BY_SCRIPTS_TESTS`, the scripts importer scan, the conftest files and `hook-shim.py` do not change (row 13, Out of scope).
- **M8: Fidelity checks.** `anchors: root, row10, row16, row17, row21`
  - `test-ids` compares every test's ID and outcome before and after, with no `--rename` (Verification 5, row 17).
  - `nodes` compares every moved node's AST and inner comments against `before.py` (Verification 6). It runs twice, because the three `world` bindings are named nodes (row 16). The first run's `compared-nodes` equals the base's node count less one, so a name missing from `--moved` cannot pass.
  - A scratch line check covers what `nodes` does not, every top-level banner and comment (row 21). It confirms that every non-blank base line from :71 on appears in the new files at least as many times, and in the other direction that the new files, headers removed, hold no surplus line or statement (Verification 7).
  - The same step checks the arms docstring against `before.py`'s :1–:7 and that the new files carry no `timing` mark (row 10).
  - Before the real run, seeded defects must make the line check and `nodes` fail.
  - Ruff's F401, F821 and F811 show that each file imports exactly what it reads and shadows nothing (Verification 4).
  - A wrong fixture binding or import changes a ruff result or a test outcome, so no separate check of either is planned.
- **M9: The original must not change under the open PR, and each conflict-prone file has one resolution.** `anchors: G1, G2, row12, row13`
  - Verification 1's hold covers the original. If it changes on `origin/main` anyway, git reports a modify/delete conflict, and the recovery runs in this order:
    1. Resolve the conflict by taking `origin/main`'s version of the original, and finish the sync.
    2. Take `<base>` as the synced `git rev-parse HEAD`.
    3. Run `git diff --quiet <base> -- claude/.claude/hooks/tests/test_cursor_adapter_gate_matrix.py`, which must exit 0.
    4. Rebuild from Verification 2, since every range is anchored. Verification 8's `git rm` then removes the original again.
  - Never resolve that conflict by keeping the deletion before step 4 has consumed `origin/main`'s version.
  - A conflict in `CURSOR_ADAPTER_READING_TESTS` keeps every entry from `origin/main`'s side, drops the old path, adds the six new paths in Critical files' order and keeps `test_cursor_adapter_lib_parity.py`. If that side renamed the constant or re-listed the gate-matrix tests, the plan stops for the engineer.
  - A conflict in `test_select_tests.py` or `docs/scripts.md` takes `origin/main`'s side, then reapplies the one-line change only if the old text remains. Otherwise the plan stops for the engineer.
  - After any resolution, Verification 4 and 8 rerun, step 8's greps included.
  - The PR body carries the class-to-file map, so the author of a PR written against the old file can re-point it.

**Assumption rows:**
1. Every line from :71 to :2388 falls in exactly one range that Critical files assigns, or in one of 21 blank gaps between ranges. The ranges hold 2,279 lines and the gaps the other 39 of the 2,318. Lines :1–:69 are the docstring and imports, which the headers replace, and :70 is blank. [verified: Read of the whole file this pass; Read of every gap line; arithmetic over Critical files' ranges]
2. Sizes. The ranges and their separators give these line counts before any header:
   - the helper 511;
   - payloads 384;
   - arms 787;
   - leading_cd 169;
   - generated_entries 232;
   - trigger_flags 71;
   - session_id 159.

   With the headers M6 implies, the files land near 545, 415, 856, 200, 260, 84 and 191 lines. [verified: arithmetic over row 1's ranges; header sizes are estimates from each file's reads, and Verification 8 measures]
3. Names that two or more planned files read, or that a helper name reads. [verified: Read of the whole file this pass]
   - `GATE_CLASSES`: `_arm_args` (:1231) and arms (:493, :1308). `DIRECT_HOOK_TIMEOUT_SECONDS`: payloads (:401), `_run_outside` (:1246), `_direct` (:1268) and arms (:1836, :1915).
   - `Registration` and `ALL_REGISTRATIONS`: arms (:493, :1331, :1376, :1566), leading_cd (:1738–:1739) and generated_entries (:1953, :1966). `EXCEPTIONS`: arms (:1313–:1363) and generated_entries (:1953). `_registrations` and `_exceptions` build those two (:492, :501).
   - `World` and `world`: arms, leading_cd and trigger_flags (:2215). `TIME_VALUE_PATTERN`: `World.snapshot` (:665).
   - `_const`: `TRIGGERS` and trigger_flags (:2185). `Trigger` and `TRIGGERS`: arms (:982, :1176–:1195, :1344–:1503, :1547) and trigger_flags (:2169–:2226). `TRIGGER_TOOL_CLASSES`: arms (:1371) and trigger_flags (:2191).
   - `_stage_a_change` and `_require_worktree_sentinel`: `TRIGGERS` (:883, :895, :908) and leading_cd (:1680, :1691). `_memory_path` and `_write_memory_index`: `TRIGGERS` (:948–:960) and arms (:1463, :1466). `_existing_file_in_the_main_checkout`, `_bash` and `_read`: `TRIGGERS`.
   - `_row_matcher`: `_arm_args` (:1229) and arms (:1216). `_arm_args`, `_run_outside`, `_direct` and `_decision`: arms and leading_cd (:1754, :1767, :1770).
   - The Context's list omits `TRIGGER_TOOL_CLASSES`, `_memory_path`, `_write_memory_index` and `_row_matcher`, and lists `assert_no_effects`, which only the arms read under this plan (:1388–:1617, :1804, :1839, :1845).
4. Names that one planned file reads, so they move with it. [verified: Read]
   - Arms: `GATE_REGISTRATIONS` through `ARM_REGISTRATIONS`, `_gate_label`, `assert_no_effects`, `TRIGGER_CASES`, `_commit_in_repo` through `_claude_twin_for` (`WriteTrigger`, `INFORMATIONAL_WRITE_TRIGGERS`, `NO_WRITE_TRIGGER_YET`, `LISTED_WRITES`, `DECLINED_SESSION_WRITES`, `LISTED_NETWORK`, `EVENT_SPEC_IDS` and the payload builders among them), `_shim_arm`, `_import_arm`, `_import_decisions` through `_last_logged_outcome`, `_child_payload`, `DECLINED_SESSION_IMPORT_STDERR`, `DECLINED_SESSION_REGISTRATIONS`, `BOOTSTRAP_PLUGIN_ID` and `LAUNCH_DEADLINE_SECONDS`. `NO_TRIGGER_YET` (:1348) and `NO_TRIGGER_TOOL_CLASSES` (:1372) are read here only too, and M3 places them.
   - Payloads: `COMMON_CURSOR_KEYS` through `SESSION_ID_NAME`, `_run_probe` among them.
   - Leading_cd: `WriteFamily` through `LINKED_WORKTREE_CASES`.
   - Generated_entries: `RECOVERY_DOC` through `DENY_OUTCOMES`.
   - Trigger_flags: `DRAWN_TRIGGER_NAMES_BY_CLASS`, `trigger_flag_failures` and `_trigger_for_table`.
   - Session_id: `_identity_env` through `_pin_rewrite`.
   - No test class reads another class.
5. Import-time work. `ALL_REGISTRATIONS` (:492) reads the settings registrations and each hook's class. `EXCEPTIONS` (:501) loads `parity-exceptions.json`. `TRIGGER_CASES` (:982), `DECLINED_SESSION_REGISTRATIONS` (:1566) and `TOOL_EVENT_REGISTRATIONS` (:1952) filter registrations. `DENY_OUTCOMES` (:2082) parses `hook-shim.py`. M3 defines each in exactly one module. Python runs a module's top level once per process and serves later imports from its module cache, so each runs once per pytest worker, as it did when the base module was imported. [verified: Read; Python's import caching is assumed, not re-read this pass]
6. `world` (:733–:735) is the only fixture the file defines, and it requests only `tmp_path`. Tests in the arms, leading_cd and trigger_flags (:2215) request it. `adapter` comes from importing `adapter_under_empty_home` (:45), whose decorator names it (`_cursor_payloads.py:133`), and only generated_entries requests it (:1972, :2023, :2033, :2137, :2145). So `world` crosses files and `adapter` does not. [verified: Read; Grep]
7. Twelve files under `claude/.claude/scripts/tests/` bind helper fixtures by module-level assignment under the comment "The fixtures live once in the helper module. An import would trip F811 on every test parameter that names one." `test_cursor_adapter_install_roundtrip.py` imports its helper as `from . import _cursor_install_helpers as install_helpers` (:15) and binds at :35–:37. No `claude/.claude/hooks/tests/` file binds one yet. [verified: Grep this pass; Read of the roundtrip file's header]
8. `claude/.claude/hooks/tests/` is a package, and the base imports its siblings as `from ._cursor_identity_fixtures import` and `from ._cursor_payloads import` (:29–:30). `.claude/rules/test-tree-packaging.md` requires that form for a same-directory sibling, and `TestNoBareSameDirectorySiblingImports` enforces it. `from helpers import` stays bare, because `pyproject.toml:21` puts `claude/.claude/tests` on `pythonpath`. So no new file needs the `sys.path` line the scripts-tests helpers carry. [verified: Read; Glob of `claude/.claude/hooks/tests/__init__.py`]
9. Of the helper's ranges, only `_decision` holds a plain `assert` (:1276), and it carries a message. Under G3 it is not rewritten there, so a failure shows that message without pytest's operand display. Every pass and fail stays the same under a normal interpreter. Under `-O` or `PYTHONOPTIMIZE`, a plain assert is stripped and a crashed hook would read as the verdict `"none"`. No script, workflow or pytest config in this repo sets either. `assert_no_effects`' two asserts (:728, :730) move to the arms, a test module, so the `match=` at :1822 sees the same text as before. [verified: Read of every helper range this pass]
10. The file reads no `sys.modules`, `globals()`, `__file__` or `__name__`, declares no `global` and uses no `monkeypatch`. It carries no `pytestmark`, `pytest.mark.timing`, skip or xfail mark. So no test depends on which module holds it, and CI's serial `-m timing -n0` pass selects none of these tests. [verified: Grep this pass printed nothing; README.md:570]
11. Position words in comments and docstrings point inside their planned files. [verified: Grep for above, below, later, earlier, this file and this module; Read]
    - :430 (payloads), :503 (arms, the meta-tests below), :898 (helper, the rows below), :1097 (arms, `INFORMATIONAL_WRITE_TRIGGERS` above), :1962 and :1967 (generated_entries) stay within their files.
    - :984's "no trigger above" names `TRIGGERS`, which M3 keeps above it in the helper.
    - :1459's "denies the new topic file above" stays true in the arms through the parametrized deny tests above it in `TestGateMatrix` (:1405–:1430), which run that trigger. The `new-topic-file` row itself (:945) moves to the helper.
    - :1002 names `TestPlanWriterGateNameOutcomes`, defined in `claude/.claude/scripts/tests/test_cursor_adapter_translate.py:1767`. :1102 names `TestBootstrapProjectPluginsRow`, which joins the same arms file. Neither names a file.
12. References to the old file. [verified: Grep this pass]
    - Edit targets outside `.claude/plans/`: the name at `select-tests.py:42` and at `docs/scripts.md:629`. Two counts also go false, "The two hooks tests below" at `select-tests.py:37` and the test name `..._selects_the_two_reading_hooks_tests_...` at `test_select_tests.py:2037`.
    - No module imports from the file, and `.github/` and `.claude/rules/` hold no hit.
    - Left as written: `.claude/plans/cursor-adapter.md` names the file 13 times, Phase 5a's (:5013) and Phase 5b's (:5018) file lists among them.
13. `select-tests.py` routing. [verified: Read and Grep]
    - Any path under `claude/.claude/hooks` selects `HOOKS_TESTS_DIR` (:733), so the helper and the topic files need no rule of their own.
    - A path under `CURSOR_ADAPTER_DIR` selects `CURSOR_ADAPTER_READING_TESTS` (:999), defined at :41–:44.
    - `HOOKS_TESTS_MODULES_IMPORTED_BY_SCRIPTS_TESTS` (:289–:298) lists the hooks-tests modules that scripts tests import. No scripts test imports the new helper, so the set does not change.
    - `select-tests.py` is a `GLOBAL_TRIGGER_PATHS` member (:558–:562), so this PR's run is the full suite (:1097–:1099).
    - `TestCrossDomainReadCompleteness` scans only `test_*.py` files for module-level repo-path constants (`test_select_tests.py:143–:161`, :385–:402). The file's one such constant, `RECOVERY_DOC` (:1926), names a `docs/` path, which selects `HOOKS_TESTS_DIR` (:1041), so generated_entries stays covered.
14. `test_select_tests.py` reads `CURSOR_ADAPTER_READING_TESTS` by name at :2041, :2047 and :2580, and holds no literal of the old path. [verified: Grep]
15. Every planned test file reads the adapter's shim or data files, and the helper is no test file. [verified: Read]
    - Payloads runs the shim (:256, :378). The arms run it (:1252) and read `tool-map.json` (:1373). Leading_cd runs it (:1754). Generated_entries requests `adapter` (:1972) and parses `hook-shim.py` (:2082). Session_id runs it (:2256, :2262) and loads it (:2383).
    - Trigger_flags runs no shim, but its test at :2215 builds a `World`, which reads `tool-map.json` (:706), and importing the helper loads `parity-exceptions.json` (:498, :501).
16. `_move_fidelity.py` facts. [verified: Read]
    - `nodes` names each top-level def, class and single-target assignment (:146–:153), and a node's first line includes its decorators (:156–:158).
    - It reports `node-missing` for a name the before file does not define exactly once or no after file defines, and `node-duplicate` for one the after files define more than once (:292–:303). It compares each node's AST and its comments (:337–:347).
    - `test-ids` fails when neither side has a testcase (:794–:795). `main` takes an argv list (:947).
17. No class is renamed, split or merged, and the file has no module-level test function. `test-ids` keys a testcase by its classname parts after the last `test_` part plus its name (`_move_fidelity.py:741–:746`), so a class keeps every ID across files and no `--rename` is needed. [verified: Grep of the file's top-level definitions; Read]
18. `require-diff-size-prompt.sh` denies a `git commit` that brings the branch's diff against `origin/<base>` to 400 or more changed lines until the engineer answers `Proceed anyway` and `diff-size-ack.sh` writes the branch's latch. It denies that ack command to a `code-writer` (:47). The projected diff is about 4,955 lines, so the commit needs the acknowledgement. [verified: claude/.claude/hooks/require-diff-size-prompt.sh:4–:8 and :47]
19. `code-writer` does not stage or commit, runs only narrowly scoped read-only checks on what it changed, and does not run the full suite. [verified: claude/.claude/agents/code-writer.md:23–:27]
20. No file named `_cursor_gate_matrix_helpers.py` or `test_cursor_adapter_gate_matrix_*.py` exists under `claude/.claude/hooks/tests/`. [verified: Glob]
21. `nodes` compares only comments inside a node (`_move_fidelity.py:344–:347`), so the top-level comments are otherwise unchecked: every banner and every top-level block comment, :72, :575–:576, :812 and :984 among them. [verified: Read]
22. This plan's `:N` numbers were measured at `1b4b117c`, the worktree's HEAD at session start. Anchors, not `:N`, decide every range (M5), and Verification 2 re-records `<base>`. [verified: the session's git status lists `1b4b117c` as the newest commit, with only this plan untracked]
23. On the shim split's over-limit `TestDirectoryPin` class, the engineer asked for the architect's view of the seam rather than a split made only for the limit. [engineer-verified: "I don't want to overindex on the 1000 file limt if the seam isn't a good one to split on. what does the architect think?"]
24. The engineer holds the same preference for this split's seams. So this plan draws every boundary at a topic and records each alternative it set aside. [unverified: the dispatch relays row 23's quote as the engineer's stance on a different split]
25. Ruff runs with `line-length = 130` and selects `E`, `F`, `B`, `I`, `UP` and `SIM`, so each dictated docstring fits on one line of at most 130 characters. [verified: pyproject.toml:1–:6; character counts of each docstring in Critical files]

26. The plan keeps two edits the peer's target list did not name: the test name at `test_select_tests.py:2037` and the wording of the comment at `select-tests.py:37–:38`. [engineer-verified: "Keep both edits (Recommended)"]
27. The Ask line's first quote and row 23's quote are the engineer's own words. [engineer-verified: "Yes, both are mine"]
28. The plan proceeds as one PR at its projected diff. [engineer-verified: "Proceed anyway"]

## Critical files

Below, `tests/` means `claude/.claude/hooks/tests/`.
- Ranges are anchored per M5. `:N` is a `before.py` line at `1b4b117c` and a guide only.
- The one `code-writer` dispatch makes every change below except the `git rm` and the plan file's commit, which the main session makes (M1). Its check is Verification 4's ruff run.

Files:
- Create `tests/_cursor_gate_matrix_helpers.py`, docstring `"""The gate matrix's harness: the registrations, the world a hook runs in, the deny-trigger table and the hook runners."""`. Its ranges:
  - `GATE_CLASSES` (:71) through `DIRECT_HOOK_TIMEOUT_SECONDS` (:73);
  - `Registration` (:464, its decorator) through `ALL_REGISTRATIONS` (:492);
  - `_exceptions` (:497) through `EXCEPTIONS` (:501);
  - the comment beginning `# Time values are masked before a file is hashed` (:575) through `World` (:712);
  - the `world` fixture (:733–:735);
  - `_const` (:741) through `TRIGGERS` (:981);
  - the comment beginning `# Gate scripts whose registration has no trigger above` (:984) through `TRIGGER_TOOL_CLASSES` (:1011);
  - `_row_matcher` (:1221) through `_run_outside` (:1247);
  - `_direct` (:1260) through `_decision` (:1280).
- Create `tests/test_cursor_adapter_gate_matrix_payloads.py`, docstring `"""Gate matrix: the Cursor payload builders, and what a hook receives through the shim, its payload and its envelope."""`.
  - the banner `# --- builders ---` (:75) through `TestShimEnvelope` (:458).
- Create `tests/test_cursor_adapter_gate_matrix_arms.py`, whose docstring is `before.py`'s :1–:7, closing quotes included. Its ranges:
  - the banner `# --- the matrix ---` (:461) alone;
  - `GATE_REGISTRATIONS` (:493) through `INFORMATIONAL_REGISTRATIONS` (:494);
  - the comment beginning `# The registrations that have a delivered shim arm` (:503) through `_gate_label` (:572);
  - `assert_no_effects` (:715–:730);
  - the banner `# --- triggers ---` (:738) alone;
  - `TRIGGER_CASES` (:982);
  - `_commit_in_repo` (:1014) through `_claude_twin_for` (:1218);
  - `_shim_arm` (:1250) through `_import_arm` (:1257);
  - `_import_decisions` (:1283) through `TestChildPayloadImportArm` (:1618);
  - `TestEffectDetectorsHavePositiveControls` (:1792) through `TestBootstrapProjectPluginsRow` (:1920).
- Create `tests/test_cursor_adapter_gate_matrix_leading_cd.py`, docstring `"""Gate matrix: the shim's verdict on a command with a leading cd equals the Claude-shaped reference's."""`.
  - the banner `# --- leading cd ---` (:1621) through `TestLeadingCdVerdictEquality` (:1789).
- Create `tests/test_cursor_adapter_gate_matrix_generated_entries.py`, docstring `"""Gate matrix: the generated entries' matchers, and the Recovery section every shim deny names."""`.
  - the banner `# --- generated entries: matchers and the Recovery section ---` (:1923) through `TestRecoverySectionResolves` (:2154).
- Create `tests/test_cursor_adapter_gate_matrix_trigger_flags.py`, docstring `"""Gate matrix: the deny triggers' inert and drawn flags, which the attended acceptance run draws from."""`.
  - the banner `# --- the inert and drawn flags ---` (:2157) through `TestTriggerFlags` (:2227).
- Create `tests/test_cursor_adapter_gate_matrix_session_id.py`, docstring `"""Gate matrix: one session id across the shim, the library and the Shell rewrite."""`.
  - the banner `# --- one session id across the shim, the library and the Shell rewrite ---` (:2230) through `TestSessionIdentityAcrossTheShim` (:2388).
- Delete `tests/test_cursor_adapter_gate_matrix.py` with `git rm`, run by the main session after every file above exists (M1).
- Modify `claude/.claude/scripts/select-tests.py` (M7).
  - :42 becomes six paths, each written out in full on its own line, in this order: `claude/.claude/hooks/tests/test_cursor_adapter_gate_matrix_arms.py`, `..._generated_entries.py`, `..._leading_cd.py`, `..._payloads.py`, `..._session_id.py` and `..._trigger_flags.py`. `test_cursor_adapter_lib_parity.py` stays last.
  - In the comment at :37–:38, "The two hooks tests below run the shim" becomes "The hooks tests below run the shim", and "and read its JSON files" becomes "or read its JSON files". No line rewraps.
- Modify `claude/.claude/scripts/tests/test_select_tests.py:2037`: `test_any_path_under_the_adapter_directory_selects_the_two_reading_hooks_tests_and_the_scripts_tests` becomes `test_any_path_under_the_adapter_directory_selects_the_reading_hooks_tests_and_the_scripts_tests`. This is the one file the peer's target list does not name (row 12).
- Modify `docs/scripts.md:629`: "`_cursor_payloads.py`, `test_cursor_adapter_gate_matrix.py` and `test_cursor_adapter_lib_parity.py`" becomes "`_cursor_payloads.py`, the `test_cursor_adapter_gate_matrix_*.py` files with their shared `_cursor_gate_matrix_helpers.py`, and `test_cursor_adapter_lib_parity.py`".
- Commit `.claude/plans/split-cursor-adapter-gate-matrix-tests.md`, this plan, with the change.

**Reuse:**
- `claude/.claude/scripts/tests/_move_fidelity.py`'s `nodes`, `test-ids`, `main` and `_load_source`, unchanged (row 16).
- The fixture-binding form of `claude/.claude/scripts/tests/test_cursor_adapter_install_roundtrip.py:15` and :35–:37, with its comment made singular (M6, row 7).
- `_cursor_payloads.py`'s and `_cursor_identity_fixtures.py`'s names and `helpers.py`'s `HOOKS_DIR`, `REPO_ROOT`, `SCRIPTS_DIR`, `_hook_class` and `symlink_hooks_lib_chain`, imported unchanged.

Projected diff: about 4,955 changed lines, about 2,561 added and 2,393 deleted. The seven new files hold about 2,551 lines, the deleted file 2,388, and the three edited files change about 15 between them.

## Verification

Rules for every step:
- Run the steps in order from the worktree root.
- `<scratch>`, `<base>` and `<workers>` stand for values an earlier call printed or chose, typed out literally.
- Run each pytest pass in the background or with an extended timeout, because it takes minutes.
- Delete a pass's XML before its run, and read the pytest exit status and the XML's testcase count next to it. A missing, empty or stale XML then fails the step instead of passing it.
- Pass the same `-n <workers>` to every pass of steps 2 and 5. Rerun a failed testcase alone with `-n0` up to twice on the same side. Only a failure that reproduces after and not before returns to step 3.
- In command output copied into the PR body, replace each absolute home path with `<worktree>`.

1. **Hold and sync.**
   - Run `git fetch origin main`, sync the branch to the new `origin/main` tip per the `git-feature-branch-sync` skill, then print `git rev-parse HEAD` as `<base>`.
   - Ask the `cursor-adapter-phase4` session and the session that owns the pending package-conversion PR to hold any edit to `test_cursor_adapter_gate_matrix.py` until this PR merges. Ask each to say before it edits `CURSOR_ADAPTER_READING_TESTS` or renames or removes a `_cursor_payloads.py` name these files import (G2, M9).
   - Optionally, `gh pr list --state open --limit 200 --json number,headRefName,files` shows other open PRs that touch the file, for the engineer to see.
2. **Baseline.**
   - Run `mktemp -d /tmp/split-gate-matrix.XXXXXX` once for `<scratch>`.
   - Run `git show <base>:claude/.claude/hooks/tests/test_cursor_adapter_gate_matrix.py > <scratch>/before.py`.
   - `grep -c 'pytest.mark.timing' <scratch>/before.py` prints 0 (row 10). Any other count stops the plan for a revision that adds CI's serial pass to steps 2 and 5.
   - Run `scripts/venv-run.sh pytest claude/.claude/hooks/tests/test_cursor_adapter_gate_matrix.py -m "not timing" -n <workers> --junitxml=<scratch>/before.xml`. `grep -E '<(failure|error)[ />]' <scratch>/before.xml` prints nothing, and the XML's testcase count is printed and nonzero. A failing baseline stops the plan for the engineer.
   - Record `<base>`, the testcase count and the list of skipped IDs.
3. **Build** (the `code-writer` dispatch; M1).
   - Create each file per M5 and M6, write `<scratch>/fidelity.py` and `<scratch>/line_check.py`, and make M7's edits. Leave the original in place. The build script stops when `git cat-file -e <base>:<path>` finds any destination path at `<base>`.
   - The inputs are `<scratch>` and `<base>`. The dispatch returns the uncommitted tree and the build script's path in `<scratch>`.
   - The prompt names the build script and `ruff --fix` as the specified work. It says that the moved ranges, the arms docstring, the dictated docstrings, the comment and M7's edits are dictated and exempt from self-review edits. It limits the self-review to headers, M7's edits and the scratch scripts.
   - The one check `code-writer` may run is `scripts/venv-run.sh ruff check` over the files it created or edited, which is read-only.
   - A failing step 4 through 8 returns to this step as a rewrite by the build script, never as a hand edit of a moved range.
4. **Lint.** `scripts/venv-run.sh ruff check claude/.claude/ claude-skills/ plugins/ evals/` reports nothing. An F821 on a name rows 3 and 4 do not place stops the plan (M3).
5. **Test-ID parity.**
   - Rerun step 2's pass over `claude/.claude/hooks/tests/test_cursor_adapter_gate_matrix_*.py`, from the same worktree path, to `<scratch>/after.xml`.
   - `scripts/venv-run.sh python3 claude/.claude/scripts/tests/_move_fidelity.py test-ids --before <scratch>/before.xml --after <scratch>/after.xml` exits 0, and its before and after counts are equal (row 17).
6. **Node fidelity** (M8).
   - Read `<scratch>/fidelity.py` once, to confirm that it passes its lists unchanged to `_move_fidelity.main`.
   - `scripts/venv-run.sh python3 <scratch>/fidelity.py nodes` runs `nodes --before <scratch>/before.py --after <the helper> --after <each of the six test files> --moved <every top-level name of before.py but world>` and exits 0. Its `compared-nodes` equals `len(_load_source(<scratch>/before.py).nodes) - 1`, about 110 at `1b4b117c`.
   - The wrapper's second run, `nodes --before <scratch>/before.py --after <the helper> --moved world`, exits 0 with `compared-nodes: 1`.
7. **Line check** (M8, row 21).
   - Read `<scratch>/line_check.py` once. It uses `collections.Counter` and exits non-zero on any failure below.
   - It confirms that every non-blank line of `<scratch>/before.py`, from `GATE_CLASSES = ...` (:71) on, appears in the seven new files at least as many times. It prints the count of lines it compared, which equals the count of those lines in `before.py`, re-derived at `<base>`.
   - It runs the same check in the other direction. With each new file's header removed (its module docstring, its import statements and, in three files, the fixture-binding comment and assignment), the new files' non-blank line multiset and top-level statement count equal `before.py`'s from :71 on. A surplus fails as a shortfall does, which catches an overlapping range or a leftover from a retried build.
   - It checks that the arms file's first seven lines equal `before.py`'s :1–:7, and that `pytest.mark.timing` occurs in none of the new files.
   - Before the real run, a scratch copy of the new files with one banner line removed, and another with one banner line edited, must each make it exit non-zero. A scratch copy with one assertion edited must make step 6's `nodes` report `node-ast`.
8. **Size, delete, stage and suite.**
   - `wc -l` prints at most 1,000 for every new file (row 2). An arms file over 1,000 stops the plan for a revision that returns `TestEffectDetectorsHavePositiveControls` through `TestBootstrapProjectPluginsRow` to the leading-cd file, under the banner they sit under in the base.
   - Run `git rm claude/.claude/hooks/tests/test_cursor_adapter_gate_matrix.py`.
   - Stage with `git add -- <the helper, the six test files, select-tests.py, test_select_tests.py, docs/scripts.md and this plan file>` as its own call, because `deny-invisible-commit-content.sh` denies a mutating git command chained ahead of a commit (:35).
   - `git grep -nE --untracked 'test_cursor_adapter_gate_matrix([^_]|$)' -- . ':!.claude/plans' ':!docs/reports'` prints nothing (row 12). The `docs/reports` pathspec skips a dated report that names the old file as a record and stays unedited.
   - `git grep -nE 'two hooks tests|two_reading_hooks' -- claude/.claude/scripts` prints nothing (row 12).
   - A scratch check shows the routing list is complete. It imports `select-tests.py` and compares the set of `claude/.claude/hooks/tests/test_cursor_adapter_gate_matrix_*.py` files on disk with the entries of `CURSOR_ADAPTER_READING_TESTS` that match that glob. The two sets are equal and have six members. A copy of the constant with one entry removed must fail the check. The full-suite run in the next bullet cannot show this, because `select-tests.py` selects the full suite on its own.
   - A failure in the full-suite run that does not touch the gate-matrix files is reproduced at `<base>` first (`git worktree add <tmp> <base>`, then the same test). It returns to step 3 only when it fails after this change and not at `<base>`.
   - Run `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py -m "not timing"`, then the same command with `-m timing -n0`. Every selected test passes. `select-tests.py` changes, so it selects the full suite on its own (row 13).
9. **Review, commit and publish.**
   - Run `/code-review` on the staged diff.
   - Any edit after step 3 reruns steps 4 through 8. Step 8's `git rm` runs only on the first pass. After such a rerun, restage and re-review. This applies to `/ready-for-review` rounds too.
   - Ask the engineer for `Proceed anyway` (row 18). On that answer, run `~/.claude/scripts/diff-size-ack.sh` as its own bare call from the worktree root, because it measures the index. Then commit.
   - Run `git push -u origin split-cursor-adapter-gate-matrix-tests`, then `/ready-for-review`, which opens the PR.
     - The PR body carries the class-to-file map, from a scratch listing of each new file's top-level classes saved in `<scratch>` and passed to the skill by path. It gives each new file's line count and its room under 1,000, step 5's before and after counts, and step 6's two `compared-nodes` lines.
     - It lists the skipped IDs from `after.xml` and says CI's first green run is their witness. It says the line-check and wrapper scripts live in `<scratch>` and cannot be re-run by a reviewer.
     - It names the helper's loss of pytest assertion rewriting for `_decision`'s one assert, which now fails with its message alone (row 9).
     - It says `TestEffectDetectorsHavePositiveControls` and `TestBootstrapProjectPluginsRow` left the base's leading-cd banner for the arms file (M2).
     - It says `NO_TRIGGER_YET` and `NO_TRIGGER_TOOL_CLASSES` sit in the helper below `TRIGGERS` (M3).
     - It says one test in `test_select_tests.py` was renamed (M7).
     - It says the arms docstring's "the new topic file above" (:1459) means the parametrized deny tests above it, since the `new-topic-file` row itself moved to the helper (row 11).
     - It says `.claude/plans/cursor-adapter.md` still names the old file and that the Phase 5a session retargets its file names.
     - The handoff message names `<scratch>` and asks the engineer to remove it after the merge.
     - If a sync after the first push moves HEAD, steps 4 and 8 rerun before the next push.
   - If a sync meets a conflict on the original, `select-tests.py`, `test_select_tests.py` or `docs/scripts.md`, apply M9's rule for that file.

## Out of scope

- **`claude/.claude/hooks/tests/_cursor_payloads.py`.** It stays unedited, because the pending package-conversion PR edits it (G2). No moved name goes there.
- **`.claude/plans/cursor-adapter.md`.** It names the old file 13 times, Phase 5a's and Phase 5b's file lists among them (row 12). It stays unedited, and the PR body says the Phase 5a session retargets its file names. Other `.claude/plans/` files that cite this file also stay as written.
- **The peer's do-not-touch list:** `select-tests.py`'s helper-only import enforcement, pytest assertion rewriting in the conftest files, and `hook-shim.py`. Registering the new helper for rewriting falls under it, so `_decision`'s loss of rewriting stays a PR-body note (row 9).
- **Renaming, rewording, merging or fixing any moved test, helper or comment.** This includes giving the helpers public names. The Root forbids editing a moved line.
- **Room for later tests.** The arms file lands near 856 lines and leaves about 144. The helper, where new trigger rows land, leaves about 455. An addition past a file's budget needs its own change, such as returning the effect controls and the bootstrap row to the leading-cd file. The PR body's per-file line counts let the `cursor-adapter-phase4` session see each budget.
- **The 1,000-line enforcing check.** `docs/design-decisions/code-file-line-limit.md:50–:57` defers it until an offender assessment exists.
- **Other files over the limit.** Each is its own split.
- **A permanent test that every gate-matrix file is in `CURSOR_ADAPTER_READING_TESTS`.** Verification 8's scratch check covers this change. A lasting guard is a repo change with its own seeded-defect test, and the peer's do-not-touch list leaves `select-tests.py` enforcement alone.
- **A `comments` subcommand in `_move_fidelity.py`.** This split again needs a scratch check of top-level comments, so promoting it is a fair follow-up. It is a repo change with its own seeded-defect tests.
- **A CHANGELOG entry.** The split changes no hook, skill or script behavior.
