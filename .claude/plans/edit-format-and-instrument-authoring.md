# Move edit-format and instrument-authoring out of the transcript-analysis shim

## Context

Goal: move the last two `cmd_*` commands, `edit-format` and `instrument-authoring`, with their helpers, `add_parser` blocks and tests, out of `claude/.claude/scripts/transcript-analysis.py` into `claude/.claude/scripts/transcript_analysis/` modules, so the shim holds no command bodies.

Ask: GH-1116's task list, row "small groups, batched by size — edit-format, instrument-authoring, turn-shape (×3), context-distribution, context-composition, cache-efficiency, pr-link, commit-gate, sessions". Turn-shape, pr-link, commit-gate and sessions shipped in #1317. Context-distribution, context-composition and cache-efficiency shipped in #1324, whose plan names edit-format and instrument-authoring as the next PR. No engineer words beyond the ticket row are quoted for scope.

Why now: these are the only two commands left in the shim after #1324, and the later phases (a test-only shim-reader retarget, leaf-module tests, `cli.py`) depend on the shim being free of command bodies.

Intended outcome: `transcript_analysis/edit_format.py` and `transcript_analysis/instrument_authoring.py` own their commands, helpers and subparser registrars. `--help` and every command's output stay byte-identical. Collected test IDs stay set-equal, plus new bootstrap IDs. The tracker (GH-1116) is the series tracker, so the PR body uses `Refs GH-1116`.

Evidence carried from exploration (survey of `origin/main` at 5e423978; the plan author re-derives line numbers at implementation time):
- edit-format occupies shim lines 315-714; instrument-authoring occupies 716-1094. The two ranges share no helper and are referenced only inside their own range, apart from docstring prose.
- Registration order in `build_parser()`: cost, context-distribution registrar, edit-format block (1351-1379), read-scope block (1381-1412), instrument-authoring block (1414-1433), context-composition registrar, cache-efficiency registrar, cost-trend. The read-scope block stays between the two moved blocks.
- Legacy tests: edit-format slice at `test_transcript_analysis.py` 1521-2060 (45 collected tests), instrument-authoring slice at 2205-2605 (48 collected tests). `_UNCONDITIONAL_HEADER_CASES` reads `_mod.cmd_edit_format` and `_mod.cmd_instrument_authoring` (about lines 3358 and 3362), so the shim keeps both as test-only bindings.
- edit-format reads tool inputs and tool_result error text, offers `--config-dir` and `--no-redact`, and refuses `--no-redact` across multiple roots. instrument-authoring reads Bash and Write content but prints aggregate counts only and has no `--no-redact`. Both names are already in `scope.py`'s `_SUBCOMMANDS_REFUSING_TOP_LEVEL_CONFIG_DIR`, so `scope.py` needs no change.
- Docs and tests that the move touches: `docs/transcript-analysis-architecture.md` (module list, registrar sentence, test-only bindings sentence, one section per module, tests paragraph), `transcript_analysis/__init__.py` docstring, `test_transcript_package_module_prefixes.py` (`PRODUCTION_MODULES`, `TEST_FILES`), and optionally `test_transcript_cli_bootstrap.py`.
- Prose rule, carried from #1324: moved text stays verbatim, known stale lines inside the moved ranges are deferred, and prose outside the moved ranges that the move makes false is fixed.
- Template: `.claude/plans/context-and-cache-commands.md` (#1324), including its scripted Verification (`tests/_move_fidelity.py` `nodes`, `remainder`, `test-ids`, `capture`, `compare-captures`, negative controls).

## Approach

Each command moves verbatim into a package module of its own, `transcript_analysis/edit_format.py` and `transcript_analysis/instrument_authoring.py`. Each module gets one `_add_<command>_subparser(subparsers)` registrar, and `build_parser()` calls it at its old block's position. The tests move verbatim into two test files that load no shim. The shim keeps `cmd_edit_format` and `cmd_instrument_authoring` only as noqa'd test-only bindings. After this PR the shim defines no `cmd_*` function.

This is a pure move, and no stow consumer sees a behavior change:
- Help, stdout, stderr, and exit codes stay byte-identical across single-root and multi-root runs (Verification 6).
- The parser dumps differ only by two declared handler relocations.
- `docs/transcript-analysis.md` names edit-format only as a CLI command, and the `/transcript-analysis` skill names neither command (row 39).
- The only visible difference is that an uncaught exception's traceback names the new module files instead of the shim.

**Part 1: production layout.** Copy moved code verbatim, extracting it by line range from Verification 1's scratch snapshot. Never retype it. Extract with a script that reads the snapshot and writes the new file. If a write-shape hook denies that, stop and report instead of retyping. Shim line ranges are at `5e423978` (row 3). A moved HEAD invokes the Rebase rule. Only these edits are allowed:
- the production rename map below;
- each module's docstring and imports;
- the registrar wrappers and their parameter name;
- wrap-only reflow past 130 columns, with the count reported. Exactly one moved line needs it, :1091 (row 16). Split its f-string into two adjacent f-strings after `:6,}`. Any other line that needs reflow signals a bad substitution: stop and report.

Every module starts with its docstring, then `from __future__ import annotations`, then stdlib imports, then one `from transcript_analysis import …` line (row 5). ruff F821 and F401 decide the stdlib imports, and I001 decides blank lines. Each authored docstring below is one logical line plus one paragraph. Wrap it in `cache_efficiency.py:1-7`'s shape. No `.py` text the plan adds names a ticket or a line number.

- **`transcript_analysis/edit_format.py`** holds :315-713 verbatim: 23 names, each with its leading comment. They are `EDIT_FAMILY_TOOLS`, `_EDIT_KNOWN_FAILURE_PATTERNS`, `_EDIT_GOVERNANCE_PATTERNS`, `_EDIT_FORMAT_UNCLASSIFIED`, `_EDIT_CAUSE_REDACTED_CREDENTIAL`, `_EDIT_CAUSE_WHITESPACE_ONLY`, `_EDIT_CAUSE_CONTENT_DIFFERS`, `_EDIT_CAUSE_ABANDONED_NO_RETRY`, `_EDIT_CAUSE_IDENTICAL_RETRY`, `_EDIT_CAUSE_OWNER_NOT_TRACKED`, `_REDACTED_CREDENTIAL_TOKEN`, `_EDIT_OLD_STRING_SIZE_BUCKETS`, `_EDIT_OLD_STRING_SIZE_OVERFLOW_LABEL`, `_EDIT_FORMAT_CHARS_PER_TOKEN`, `_old_string_size_bucket`, `_tool_result_text`, `_new_edit_format_stats`, `_merge_edit_format_stats`, `_edit_notfound_cause`, `_scan_edit_format_session`, `cmd_edit_format`, `_edit_format_report`, and `_print_edit_format_report`. Then `_add_edit_format_subparser`.
  - Package import: `from transcript_analysis import pricing, render, scope`. Expected stdlib: `argparse`, `json`, `re`, `sys`, `Counter`, `Sequence`, `Path`.
  - Docstring: `"""The edit-format command: cmd_edit_format, the Edit/Write/MultiEdit call census with per-tool failure classification, governance-hook re-bucketing, not_found cause attribution, and old_string/new_string/Write token overhead, with a per-account breakdown above one root, and its subparser registration.\n\nImports pricing, render, and scope by module (attribute access, not by name) -- see scope.py's own top-of-file comment for why."""`
- **`transcript_analysis/instrument_authoring.py`** holds the design-pointer comment :720-721, then :723-1094 verbatim: 25 names, each with its leading comment. They are `_INSTRUMENT_AUTHORING_HEREDOC_OPEN_RE`, `_INSTRUMENT_AUTHORING_INLINE_INTERPRETER_FLAGS`, `_INSTRUMENT_AUTHORING_INLINE_INTERPRETER_RE`, `_INSTRUMENT_AUTHORING_SCOPE_MAIN`, `_INSTRUMENT_AUTHORING_SCOPE_SUBAGENT`, `_INSTRUMENT_AUTHORING_SHAPE_BASH`, `_INSTRUMENT_AUTHORING_SHAPE_WRITE`, `_INSTRUMENT_AUTHORING_COHORT_ZERO_DISPATCH`, `_INSTRUMENT_AUTHORING_COHORT_DISPATCHED`, `_INSTRUMENT_AUTHORING_SIZE_BUCKETS`, `_INSTRUMENT_AUTHORING_SIZE_OVERFLOW_LABEL`, `_instrument_authoring_size_bucket`, `_extract_heredoc_payloads`, `_extract_shell_arg_at`, `_extract_inline_program_payloads`, `_bash_authoring_payload_chars`, `_is_scratchpad_write_path`, `_new_instrument_authoring_stats`, `_merge_instrument_authoring_stats`, `_new_instrument_authoring_cohort_totals`, `_scan_instrument_authoring_session`, `_aggregate_instrument_authoring_sessions`, `cmd_instrument_authoring`, `_instrument_authoring_report`, and `_print_instrument_authoring_report`. The banner rules and bare `#` line :716-719 are deleted, not moved. Then `_add_instrument_authoring_subparser`.
  - Package import: `from transcript_analysis import pricing, render, scope`. Expected stdlib: `argparse`, `re`, `Counter`, `Iterable`, `Sequence`, `Path`, `PurePosixPath`.
  - Docstring: `"""The instrument-authoring command: cmd_instrument_authoring, the aggregate-only census of Bash heredoc and inline-program calls and Write-to-scratchpad calls, size-bucketed by main-thread and subagent scope and split into zero-dispatch and dispatched session cohorts, and its subparser registration.\n\nImports pricing, render, and scope by module (attribute access, not by name) -- see scope.py's own top-of-file comment for why."""`
- **Registrars.** Each takes `build_parser()`'s subparsers action as `subparsers` and returns `None`, in the `cache_efficiency.py:261-294` shape.
  - Docstring: `"""Register the <command> subparser on build_parser()'s subparsers action, binding cmd_<name> through set_defaults(func=...)."""`
  - Each body is its shim block verbatim, with two substitutions: the `sub.add_parser(` receiver `sub` → `subparsers`, and `_add_project_scope_args` → `scope._add_project_scope_args`.
  - Blocks: edit-format :1351-1379, instrument-authoring :1414-1433.
- **Production rename map.** It applies to code only, never to docstrings or comments. It has 9 entries, and ruff F821 drives the prefixes. Never run a regex over bare names (row 6).
  - To `scope.` (6): `_resolve_cost_roots`, `_DO_NOT_PUBLISH_BANNER`, `_resolve_project_scope`, `_redaction_ordinals`, `_root_index_for_path`. Also `_print_resolved_scope` → `scope.print_resolved_scope`.
  - To `pricing.` (2): `_SPAWN_TOOL_NAMES`. Also `_dedup_turns_by_request_id` → `pricing.dedup_turns_by_request_id`.
  - To `render.` (1): `_pct_of`.
  - The existing `scope.PROJECTS_DIR` reads at :581 and :1036 are already in module form. The `scope.` calls keep `TestRootsThreadingSpy` intercepting (row 10).

**Part 2: shim.**
- Delete :315-1095. Exactly two blank lines then sit between the last import statement and `def build_parser`. That removes the 48 moved names, the instrument-authoring banner, and the leading comments that moved with their names.
- In `build_parser()`, :1351-1379 becomes `_add_edit_format_subparser(sub)`, and :1414-1433 becomes `_add_instrument_authoring_subparser(sub)`. The read-scope block between them stays. Each call keeps one blank line on each side, so registration order and the `--help` listing stay as they are.
- Add one by-name import block per module, each at its sorted position (ruff I001 decides). Each block imports the registrar and `cmd_<name>  # noqa: F401`, with this comment: `# _add_<name>_subparser is read bare by this file's own build_parser (the <command> subparser's registration). cmd_<name> is read only via _mod.cmd_<name> from test files.` (row 9)
- **Orphaned imports.** For each name ruff F401 flags after the deletion, a `_mod.<name>` grep decides its fate. The grep is `git grep -nE "_mod\.<name>\b|\(_mod, *[\"']<name>[\"']" -- claude/.claude/scripts/tests`. A hit counts only in a file whose `_SCRIPT` names `transcript-analysis.py`, and only on a line outside the moved test slices (row 7).
  - **A test reads it.** Keep the import with `# noqa: F401 -- read only via _mod.<name> from test files`. On an aliased import line, put that rationale on its own comment line above the import, and end the import line with `# noqa: F401`, the shape :254-255 already uses (row 17).
  - **No test reads it.** Remove the import.
  - **At plan time (row 7):**
    - Keep, with the noqa: `_DO_NOT_PUBLISH_BANNER`, `_redaction_ordinals`, `_resolve_cost_roots`, and `_resolve_project_scope` in the `transcript_analysis.scope` block; the aliased `_print_resolved_scope` (:252) and `_dedup_turns_by_request_id` (:191).
    - Remove: `import json`; `import re`; the `from collections import Counter` line; the `from collections.abc import Iterable, Sequence` line; `PurePosixPath` from the `pathlib` line; `_SPAWN_TOOL_NAMES` from the `pricing` block; `_pct_of` from the `render` block; `_root_index_for_path` from the `scope` block.
  - **Unexpected names.** A flagged name outside these 15 gets the same rule, and the return lists it.
- The module-list comment (:17-24) stays as written: no `_mod.<module>` reader is lost or added (row 12).

**Part 3: tests.** The two new test files load no shim. They import package modules directly, as the `fake_projects` fixture permits (row 18). Nothing on a moved line changes except the test rename maps below, applied to code only, and wrap-only reflow.
- **`tests/test_transcript_edit_format.py`** holds legacy :1526-2059: the 12 helpers `_edit_format_args`, `_edit_tool_use`, `_write_tool_use`, `_multi_edit_tool_use`, `_error_result`, `_extract_call_census_count`, `_extract_known_failure_count`, `_extract_governance_count`, `_extract_cause_count`, `_extract_unclassified_count`, `_extract_unpaired_count`, and `_extract_account_edit_calls`, then `TestEditFormat` and `TestScanEditFormatSession`.
  - Rename map (7): `_mod.<name>` → `edit_format.<name>` for `_edit_format_report`, `EDIT_FAMILY_TOOLS`, `cmd_edit_format`, `_scan_edit_format_session`, and `_old_string_size_bucket`; `_mod.scope` → `scope`; `_mod._DO_NOT_PUBLISH_BANNER` → `scope._DO_NOT_PUBLISH_BANNER`.
  - Imports: `re`, `pytest`, `from transcript_analysis import edit_format, scope`, and the conftest helpers F821 names (expected: `_bash_use`, `_hook_deny_current`, `_opus`, `_thinking_block`, `_user_msg`, `_write_cost_root`, `_write_jsonl`).
  - Docstring: `"""Tests for transcript_analysis/edit_format.py's cmd_edit_format: the per-tool call census and failure classification, governance re-bucketing, not_found cause attribution, token overhead, the per-account breakdown and its ordinals, the --no-redact refusal and banner, and the per-session scan dict."""`
- **`tests/test_transcript_instrument_authoring.py`** holds legacy :2210-2603: the 4 helpers `_instrument_authoring_args`, `_extract_instrument_authoring_call`, `_extract_instrument_authoring_bucket_count`, and `_extract_instrument_authoring_cohort`, then `TestInstrumentAuthoring` and `TestScanInstrumentAuthoringSession`.
  - Rename map (5): `_mod.<name>` → `instrument_authoring.<name>` for `_instrument_authoring_report`, `_scan_instrument_authoring_session`, `_instrument_authoring_size_bucket`, `_new_instrument_authoring_stats`, and `_aggregate_instrument_authoring_sessions`.
  - Imports: `re`, `pytest`, `from transcript_analysis import instrument_authoring`, and the conftest helpers F821 names (expected: `_agent_use`, `_asst`, `_bash_use`, `_opus`, `_user_msg`, `_write_jsonl`, `_write_subagent_jsonl`, `_write_use`).
  - Docstring: `"""Tests for transcript_analysis/instrument_authoring.py's cmd_instrument_authoring: heredoc, inline-program, and scratchpad-Write classification, main-thread and subagent scope attribution, size buckets, spawn-dispatch cohorts, and the aggregate-only output that prints no authored payload."""`
- **Legacy `test_transcript_analysis.py`**:
  - Delete :1521-2061 and :2205-2605. Two blank lines then precede `_read_tool_use` and the `# cache-efficiency` banner. That removes the 20 moved names and the `# edit-format` and `# instrument-authoring` banners (row 11).
  - Remove only the imports F401 then flags. Expected: `_bash_use`, `_hook_deny_current`, `_thinking_block`, and `_write_use` from the `.conftest` block (row 13).
  - These stay byte-identical: `_read_tool_use`, `TestCorpusSubagentDirAndUtf8ErrorHandling`, `_UNCONDITIONAL_HEADER_CASES` and its two classes, `TestRootsThreadingSpy`, `TestConfigDirFlag`, and `TestBuildParser`.
- **`test_transcript_package_module_prefixes.py`.** Append `edit_format.py` and `instrument_authoring.py` to `PRODUCTION_MODULES`. Append the two new test files to `TEST_FILES`, which `test_loader_free_package_importing_test_files_are_in_test_files` requires (row 18).
- **`test_transcript_cli_bootstrap.py`** gains four definitions after the context-and-cache dispatch test (:726-752). They reuse `_run`, `_seed_read_scope_account`, and `_isolated_config_env` and add no seed. The only new setup is an empty second config dir (rows 23-26).
  - `test_transcript_analysis_edit_format_and_instrument_authoring_help_exits_zero` is parametrized over `(command, flag_tokens)` with `ids=["edit-format", "instrument-authoring"]`.
    - `flag_tokens` is `("[--config-dir DIR]", "[--no-redact]")` for edit-format and `("[--config-dir DIR]",)` for instrument-authoring (row 25).
    - A one-line comment above the cases: `# Usage-line forms, because edit-format's --config-dir and --no-redact help texts each name the other flag.`
    - It runs `_run("transcript-analysis.py", command, "--help")`. It asserts exit 0, that `f"transcript-analysis.py {command}"` is in stdout, and that each flag token is in stdout.
    - Docstring: `"""Proves, in a fresh interpreter, that build_parser calls the edit-format and instrument-authoring registrars, and that each registrar still adds its --config-dir flag and, where it has one, its --no-redact flag."""`
  - Two per-case callables:
    - `_assert_edit_format_seeded_cells` asserts `re.search(r"total assistant output tokens \(all sessions\): 200(?=\s)", stdout)`.
    - `_assert_instrument_authoring_seeded_cells` asserts `re.search(r"zero_dispatch +sessions= +1 \(100\.0% of sessions\)", stdout)`.
  - `test_transcript_analysis_edit_format_and_instrument_authoring_subprocess_finds_seeded_session` is parametrized over `(command, expected_token, resolved_root_token, assert_seeded_cells)` with the same `ids`.
    - Its tokens are `## Edit-family call census` and `## Inline instrument-authoring census`, each printed by one handler only (row 24).
    - Its resolved-root tokens are `EDIT FORMAT SOURCES (*; 2 roots)` and `INSTRUMENT AUTHORING SOURCES (*; 2 roots)`. Each prints only when the handler received `_resolve_cost_roots`'s list with the second root in it (row 24).
    - The body sets `config_dir = _seed_read_scope_account(tmp_path)`, with the one-line comment `# _seed_read_scope_account's one turn carries output tokens, which edit-format's census reports.` It creates `extra_config_dir = tmp_path / "account2"` holding only an empty `projects/`, and runs `_run("transcript-analysis.py", command, "--config-dir", str(extra_config_dir), env=_isolated_config_env(config_dir, tmp_path))`.
    - It asserts exit 0, the token, the resolved-root token, and the callable, in that order.
    - Docstring: `"""Proves, in a fresh interpreter, that real argparse dispatches edit-format and instrument-authoring each to its own cmd_* handler through set_defaults(func=...), and that the subcommand-level --config-dir reaches _resolve_cost_roots. Both refuse the top-level --config-dir, so _isolated_config_env pins the first account and the subcommand flag adds an empty second one."""`
  - The file grows from 1,164 to about 1,229 lines. The PR body flags it (row 8).

**Part 4: docs and comments** (row 20).
- **`transcript_analysis/__init__.py`.** Append `edit_format, instrument_authoring` to the command-group module list, after `cache_efficiency`.
- **`docs/transcript-analysis-architecture.md`**:
  - **:8-9.** "Its own body still holds `build_parser()`, `main()`, and every not-yet-moved `cmd_*` subcommand handler." becomes "Its own body holds `build_parser()` and `main()`, and defines no `cmd_*` subcommand handler."
  - **:11-12.** "once every command group has followed" becomes a statement that the shim now holds only `build_parser()` and `main()` and that `cli.py` is the next move.
  - **:14-16.** End the first sentence at "so no circular import is possible.", deleting "while `cmd_*` functions remain split across both the shim and the package".
  - **:22-23.** Add `edit_format.py` and `instrument_authoring.py` to the imports-back-into list, after `cache_efficiency.py`.
  - **Registrar paragraph (:52-63).** Add edit-format and instrument-authoring to the registrar sentence. Add `cmd_edit_format` and `cmd_instrument_authoring` to the test-only bindings sentence, before `cache_efficiency.py`'s `_cache_efficiency_report`. Also name the six leaf bindings this PR turns test-only (`_dedup_turns_by_request_id`, `_DO_NOT_PUBLISH_BANNER`, `_redaction_ordinals`, `_resolve_cost_roots`, `_resolve_project_scope`, `_print_resolved_scope`), or say they are annotated at the import site, because `TestRootsThreadingSpy` patches two of them with `raising=True`.
  - **Two new `### <module>.py` sections** go after `cache_efficiency.py` (:715-721), before `## Sibling scripts` (row 19). Each states the module's responsibilities, that it imports `pricing`, `render`, and `scope` all by module, matching `cost.py`'s convention, and that its registrar is the one name `build_parser()` reaches bare.
  - **:757-759.** "— `build_parser`, `main`, or a `cmd_*` function the shim itself defines —" becomes "— `build_parser` or `main` —".
  - **Tests section.** After the last paragraph (:930-938), add a paragraph that names `tests/test_transcript_edit_format.py` and `tests/test_transcript_instrument_authoring.py`, states that neither loads the shim and each imports its package modules directly, and states that `_UNCONDITIONAL_HEADER_CASES` in `tests/test_transcript_analysis.py` still reads `cmd_edit_format` and `cmd_instrument_authoring` through the shim.

**Prose rule** (the session's default, row 2). Moved text stays verbatim apart from the rename maps and the one reflow. Known stale lines inside moved ranges move unchanged and are deferred (Out of scope; row 21). Prose outside the moved ranges that the move makes false is fixed in this PR (Part 4; row 20). Four of Part 4's edits (:8-9, :11-12, :14-16, :757-759) fix sentences that turn vacuous rather than strictly false: each describes or presupposes shim-defined `cmd_*` functions, which no longer exist. The plan treats a reader-misleading presupposition as false.

**Decision: two modules, one per command.** This is the session's default (row 2). The two ranges share no helper (row 4). One combined module would group commands that share nothing, against the package's seam rule (row 5), and would come to about 870 lines (row 8). `denials.py` was weighed for edit-format's governance patterns, but the two share no code, and the moved comment (:334-338) states the narrower purpose.

**Decision: the shim keeps two test-only `cmd_*` bindings and six leaf bindings.** `_UNCONDITIONAL_HEADER_CASES` reads `_mod.cmd_edit_format` and `_mod.cmd_instrument_authoring` at import time, so a missing binding fails collection of the whole legacy file (row 9). Shim-loading tests outside the moved slices still read six leaf names the shim's own code stops reading (row 7).
- Retargeting `_UNCONDITIONAL_HEADER_CASES` would edit a byte-identical cross-group table, which the governing plan relocates in the `cli.py` phase (row 40).
- Retargeting the six leaf readers would edit test files this PR does not otherwise touch. A by-name noqa binding is the lighter primitive.

**Decision: `_read_tool_use` and `TestCorpusSubagentDirAndUtf8ErrorHandling` stay in the legacy file.** They sit under the `# edit-format` banner but test `corpus.py` and read no moved name (row 11). Moving them would put corpus tests in a command file.

**Decision: the bootstrap pair goes in `test_transcript_cli_bootstrap.py`, reusing `_seed_read_scope_account`.** Nothing else pins either registration (row 26), and the new registrars are the only new code paths. The engineer keeps the pair (row 31).
- `_seed_priced_account`, phase B's seed, gives edit-format no seed-derived cell: its one turn carries no tool call and 0 output tokens (row 23).
- An in-process `build_parser().parse_args([command]).func` check would pin `func` but runs no handler in a fresh interpreter and cannot see root resolution.
- A new seed with Edit and Bash calls would add about 30 lines for cells that prove nothing more about dispatch.
- No test at all leaves a deleted registrar call or flag invisible to every committed test (row 26).

**Decision: two `nodes` passes over the legacy test source.** The instrument-authoring helpers use `scope` as a parameter name, so the edit-format map's `_mod.scope` → `scope` entry would flag `rename-ambiguous` on them (row 14). Each pass gets its own map.

**Decision: the revert rehearsal is waived (row 30).** The governing plan requires one at each phase boundary, and phase B's waiver covered that PR only (row 30). This PR changes no `conftest.py` line, so the rehearsal's named hazard, a conftest fixture promotion, cannot arise from it. The engineer selected the waiver, so Verification carries no post-merge revert step.

**Alternatives set aside.** At the whole-plan level:
- **Leaving the `add_parser` blocks in the shim.** Every module moved since session-signal carries a registrar (row 5), and leaving them would keep about 50 lines the `cli.py` phase must move anyway.
- **Folding edit-format into `read_scope.py`.** Their docstrings mirror each other's contract, but they share no code.
- **A separate declared-roots capture pair.** `--config-dir` extras reach the same multi-root code in the moved functions (row 28).

**Assumption ledger.**

**Root:** The shim (1,964 lines) still holds the edit-format and instrument-authoring commands. Their 48 top-level names (:315-713, :720-1094) and two `add_parser` blocks (:1351-1379, :1414-1433) come to about 830 lines. Their tests are 942 lines of the 5,354-line legacy test file (:1521-2061, :2205-2605). This PR moves them into two package modules and two loader-free test files and leaves one definition per name, so the shim defines no `cmd_*`. CLI bytes stay identical, and the parser configuration changes only by two declared handler relocations. `[verified: transcript-analysis.py read at the cited ranges, last line 1964; test_transcript_analysis.py read at the cited ranges, last line 5354]`

**Givens:**
- G1. The shim keeps `build_parser()` and `main()` until the `cli.py` phase. Reason: changing it needs a decision outside this plan, the governing plan's phase order; without it, this plan would also have to rehome `build_parser()` and `main()`. `[verified: transcript-analysis-decomposition.md:155-161]`
- G2. pytest's `prepend` import mode makes test basenames global, so new files need unique names. Reason: pytest owns this behavior. `[verified: transcript-analysis-decomposition.md:233-236]`
- G3. A monkeypatch reaches code only through the binding that code reads at call time. Reason: Python import semantics. `[unverified — relayed from context-and-cache-commands.md G3]`
- G4. Agents run `select-tests.py`, not the full suite, outside CLAUDE.md's two named cases. Reason: changing it needs a decision outside this plan, a change to CLAUDE.md § "Commands"; without it, Verification 4 would run the full suite. `[verified: CLAUDE.md "Commands"]`

**Rows:**
1. The Ask is GH-1116's small-groups row, and #1324's plan names edit-format and instrument-authoring as the second PR. `[verified: context-and-cache-commands.md:7, :579]`
2. The session's five defaults for this PR are the session's, not the engineer's: one module per command with the two registrar names; the prose rule; the two test file names; no GH-1116 tracker edit and no follow-up issue in this PR; and the shim staying over 1,000 lines, estimated at about 1,130. `[unverified]` This plan keeps all five. It corrects the size estimate to about 1,144 (row 8).
3. Shim regions at `5e423978`:
   - edit-format :315-713, 23 names, whose top-level comment blocks are :315-318, :321-324, :332-338, :355-358, :361-364, :367-369, and :378-380;
   - instrument-authoring banner :716-719, design pointer :720-721, then :723-1094, 25 names, whose other top-level comment blocks are :723-727, :730-733, :739-744, and :758-761;
   - blocks :1351-1379 and :1414-1433, with the read-scope block :1381-1412 between them, registered in the order cost, context-distribution, edit-format, read-scope, instrument-authoring, context-composition, cache-efficiency, cost-trend (:1287-1453).

   `[verified: read]`
4. Coupling. Neither range reads a name defined in the other, and no other shim code reads a name from either. Outside the ranges, only docstrings and comments name either command: `cost.py:707`, `read_scope.py:384-402`, `cache_efficiency.py:176-200`, `scope.py:553`, and about 13 test docstrings (the list is non-exhaustive; each hit was checked to name no file location). Also `read_scope.py:18`, a comment naming `_EDIT_FORMAT_CHARS_PER_TOKEN`. `[verified: read of both ranges; grep]`
5. Seam precedent:
   - 35 package modules define a `cmd_*`, and 16 of them carry the 18 `_add_*_subparser` registrars, each named for one command.
   - The package shape is a docstring, then `from __future__ import annotations`, stdlib imports, and one unaliased `from transcript_analysis import …` line (`cache_efficiency.py:1-15`). The registrar shape is `cache_efficiency.py:261-294`.

   `[verified: grep "^def cmd_" and "^def _add_\w+_subparser"; read]`
6. Every production rename-map target is already bound by its module: `scope.py` :276, :509, :524, :679, :690, :710, :857; `pricing.py` :232, :465; `render.py:66`. `[verified: grep]`
7. After the deletion, shim code stops reading 15 imported names: `json`, `re`, `Counter`, `Iterable`, `Sequence`, `PurePosixPath`, `_SPAWN_TOOL_NAMES`, `_pct_of`, `_root_index_for_path`, `_dedup_turns_by_request_id`, `_DO_NOT_PUBLISH_BANNER`, `_redaction_ordinals`, `_resolve_cost_roots`, `_resolve_project_scope`, and `_print_resolved_scope`. `argparse`, `sys`, `Path`, and `scope` keep readers in `build_parser()` and `main()` (:1959). Shim-loading tests outside the moved slices still read six of them:
   - `_mod._dedup_turns_by_request_id`: legacy :1138-1514, :5105;
   - `_mod._DO_NOT_PUBLISH_BANNER`: test_transcript_cost.py:1185-1193, test_transcript_review_rounds.py:1548-3212, test_transcript_plan_boundary.py:266-274, test_transcript_handoff_signal_response.py:69-77;
   - `_mod._redaction_ordinals`: legacy :4811-4812, :4997; test_transcript_cost.py:1154;
   - `_mod._resolve_cost_roots`: test_transcript_cost.py:716-856, :4076-4125;
   - `_mod._resolve_project_scope`: legacy :2786-2808, :3552, :3593, :3772-3839, :4534-4591; test_transcript_handoff_signal_response.py:725;
   - `_mod._print_resolved_scope`: legacy :3553, :3594.

   No test reads the other nine. Each file cited loads the shim through a `_SCRIPT` naming `transcript-analysis.py`. `[verified: grep]`
8. Size arithmetic:
   - The shim falls from 1,964 to about 1,144 lines: 781 deleted lines, 47 net block lines, 7 removed import lines, 2 added rationale lines, and 12 added import-block lines.
   - The modules land at about 455 and 420 lines, so one combined module would come to about 870.
   - The test files land at about 555 and 415 lines. The legacy file falls to about 4,410, and the bootstrap file grows to about 1,229.

   `[verified: line arithmetic over rows 3, 7, 11, and 23]`
9. Staying readers of moved names: `_mod.cmd_edit_format` and `_mod.cmd_instrument_authoring` at legacy :3358 and :3362, both read at import time by `_UNCONDITIONAL_HEADER_CASES`. No staying test reads any other moved name, and no other test file reads one. `[verified: grep -o of every _mod.<name> read in the tests directory]`
10. `TestRootsThreadingSpy` patches `_mod._resolve_project_scope` and `_mod._print_resolved_scope` by string (:3564-3565), and `_mod.scope._resolve_project_scope` and `_mod.scope.print_resolved_scope` too (:3569-3570). It is parametrized over `_UNCONDITIONAL_HEADER_CASES`, which holds edit-format and instrument-authoring rows. After the move, the moved code reaches only the `scope.` patches. `[verified: read]`
11. Test slices:
    - edit-format :1521-2061 is the banner :1521-1523, 14 names (:1526-2059), and two blank lines;
    - instrument-authoring :2205-2605 is the banner :2205-2207, 6 names (:2210-2603), and two blank lines;
    - `_read_tool_use` and `TestCorpusSubagentDirAndUtf8ErrorHandling` (:2062-2202) sit under the edit-format banner, test `corpus.py`, and read no moved name.

    `[verified: read; grep]`
12. The moved slices read only these from the shim: the moved names, `_mod.scope` (:1821, a `setattr` target), and `_mod._DO_NOT_PUBLISH_BANNER` (:1838). No moved slice reads a `_mod.<module>.<name>`, so the shim's module-list comment stays true. `[verified: grep -o]`
13. After the deletion, `_bash_use`, `_hook_deny_current`, `_thinking_block`, and `_write_use` have no reader left in the legacy file. `re`, `pytest`, and the other conftest names keep readers. `[verified: grep]`
14. `scope` occurs as code in the instrument-authoring helpers (:2218-2233, a parameter) and nowhere as code in the edit-format slice: :1818 and :1899 are docstrings. `nodes` flags `rename-ambiguous` when a new name occurs as code in the original node (_move_fidelity.py:337-340). `[verified: grep; read]`
15. `remainder` drops each top-level import statement and any comment on its lines, then groups the other comments into blocks of consecutive lines (_move_fidelity.py:375-405, :476-480). A statement's span starts at its first line or decorator, so a comment line above an import is outside it (:157-159). `nodes` compares each moved node's AST after reversing renames, plus its comments (:326-352). `[verified: read]`
16. Shim :1091 is 130 columns and grows to 137 once `_pct_of` becomes `render._pct_of`. ruff's line length is 130 (pyproject.toml:2). `[verified: column count of :1091 by hand; pyproject.toml:2]` No other renamed line passes 130. `[unverified: spot-checked :599 (to 118), :643, :688 (to 122), :712, :1086 (to 125), and :1093]` Splitting the f-string at a replacement-field boundary leaves the merged AST unchanged. `[unverified: CPython's implicit-concatenation merge not read; Verification 7's node-ast check confirms it]`
17. The shim's one aliased noqa import, :254-255, carries its rationale on a comment line above and ends with `# noqa: F401`. `[verified: read]` An `as` import cannot join a parenthesized block without failing ruff I001. `[unverified: ruff's combine-as-imports default not read; ruff check confirms it]`
18. The prefix test requires the following:
    - every module in `PRODUCTION_MODULES` (:25-52) imports siblings only by module and reads only real sibling attributes;
    - every loader-free `test_*.py` that imports the package is listed in `TEST_FILES` (:53-103, enforced at :474-491).

    `[verified: read]`
19. The architecture-doc test requires a `` ### `<name>.py` `` heading for every package module (test_transcript_analysis_architecture_doc.py:15-42). The import-cycle test globs modules (:25) and adds one `test_module_imports_first_in_fresh_interpreter[<module>]` ID per module (:142-143). `[verified: grep]`
20. The move makes these texts false or vacuous outside the moved ranges:
    - architecture doc :8-9, :11-12, :14-16, :22-23, :52-63, and :757-759;
    - the `__init__.py` docstring's module list (:3-9).

    These stay true: `cost.py:707`, `read_scope.py:384-402`, `cache_efficiency.py:176-200`, `scope.py:553`, the test docstrings that name an edit-format test by function name (test_transcript_read_scope.py:232, test_transcript_cache_efficiency.py:259), and `docs/transcript-analysis.md` :683 and :881. Committed plans, `docs/reports/`, `docs/case-studies/`, `docs/cost-levers-considered.md`, and CHANGELOG.md are preserved records. `[verified: grep; read]`
21. Stale prose inside the moved ranges:
    - shim :334-338 calls `_denial_hook_label`'s extraction "above", but it lives at denials.py:259;
    - shim :361 cites `_lib.sh:980`, but the `[REDACTED-CREDENTIAL]` replacement now sits at `_lib.sh:3857`;
    - shim :378-380 cites `measure_overhead.py` and "this plan's headline token figures", but only `.claude/plans/hashline-edit-format.md` names that script.

    The phrase "this module's own tests" at :568-569, :585-586, and :1027-1028 becomes accurate after the move. The design pointer :720-721 names an existing heading (delegate-instrument-authoring.md:143). `[verified: grep]`
22. Inside `_scan_instrument_authoring_session`, a local named `scope` (:949) shadows the `scope` module import. That function reads no `scope.` attribute. `[verified: read :929-991]`
23. Bootstrap file facts:
    - It is 1,164 lines, of flat tests sharing `_run` (:31-43).
    - `_seed_read_scope_account` (:761-793) writes one main-thread assistant record with a Read `tool_use` and 200 output tokens, plus its non-error `tool_result`, under `tmp_path / "account"`. `_isolated_config_env` (:209-221) pins `CLAUDE_CONFIG_DIR` and an absent declared-roots file.
    - `_seed_priced_account` (:181-206) writes one turn with empty content and 0 output tokens.
    - No test names edit-format or instrument-authoring. Both are in `_SUBCOMMANDS_REFUSING_TOP_LEVEL_CONFIG_DIR` (scope.py:701-707), so a subprocess case cannot pass the top-level `--config-dir`.

    `[verified: read; grep]`
24. Dispatch observables:
    - `## Edit-family call census` prints only at shim :634, `## Inline instrument-authoring census` only at :1054, and `total assistant output tokens (all sessions): ` only at :686.
    - The header reads `<SUBCOMMAND> SOURCES (<label>; N roots)` above one root (scope.py:650-651, :676), and the multi-root glob label for the default `--projects` stays `*` (scope.py:517, :611, :912-916).
    - A `func=_<command>_report` binding passes `roots=None`, which scans one root (shim :581, :1036).

    `[verified: grep; read]` Over `_seed_read_scope_account` plus an empty second root, edit-format prints `total assistant output tokens (all sessions): 200`, and instrument-authoring prints `zero_dispatch  sessions=     1 (100.0% of sessions)`. `[unverified: derived from :467-547, :929-1008, :1080-1094; Verification 5 confirms it]`
25. Registrar flags:
    - edit-format adds `--config-dir` and `--no-redact` (:1360-1378), and instrument-authoring adds `--config-dir` only (:1424-1432). `_add_project_scope_args` adds `--projects` and `--this-repo` (scope.py:509-521). `[verified: read]`
    - edit-format's `--config-dir` help names `--no-redact` (:1366), and its `--no-redact` help names `--config-dir` (:1376), so a bare token matches without the flag. `[verified: read]`
    - argparse renders the flags as `[--config-dir DIR]` and `[--no-redact]` in the subcommand's usage line. `[unverified: argparse usage formatting not read; Verification 1's help captures confirm it]`
26. No committed test pins either registration. `TestBuildParser` (:2845-2911) names neither command. `TestConfigDirFlag`'s parametrized refusal (:212-250) asserts only exit 2 and that stderr names `--config-dir` and the subcommand. `[verified: read; grep]` argparse's invalid-choice error for an unregistered subcommand also satisfies those assertions. `[unverified: derived; Verification 8 (c) confirms it]`
27. argparse raises an unrecognized-arguments error on the top-level parser, so run-12's output does not depend on instrument-authoring's own flags. `[unverified: argparse internals not read; Verification 8 (b) confirms it]`
28. `_resolve_cost_roots` unions the config dir, declared roots, and `--config-dir` extras into one root list (scope.py:760-795), so an extra reaches the same multi-root code in the moved functions as a declared root. `[verified: read]` `capture` pins `CLAUDE_CONFIG_DIR` to `--account` and points `TRANSCRIPT_CONFIG_DIRS_FILE` at an absent file. `[unverified — relayed from context-and-cache-commands.md row 27]`
29. Verification 1's seed produces the liveness tokens listed there. `[unverified: derived from :384-547, :771-1008, and scope.py:276-296; Verification 1's liveness check confirms it]`
30. Revert rehearsal. Governing plan item 7 reads: "At each phase boundary, `git revert` the phase's merge commit on a scratch branch and run the full suite." `[verified: transcript-analysis-decomposition.md:276-279]` Phase B's waiver covered that PR only. `[verified: context-and-cache-commands.md row 29]` The engineer was asked about this PR and selected the label "Waive it (Recommended)". `[engineer-verified: "Waive it (Recommended)"]` This PR changes no `conftest.py` line. `[verified: this plan's Critical files]`
31. The bootstrap pair is new coverage, which the `Ask:` line does not call for. The engineer kept phase B's pair, a tag scoped to that PR (context-and-cache-commands.md row 48), and was asked about this one and selected the label "Keep them (Recommended)". `[engineer-verified: "Keep them (Recommended)"]`
32. Tools run through `scripts/venv-run.sh <tool>`. `[verified: CLAUDE.md "Commands"]` `nodes --rename` needs Python 3.12's `tokenize`. `[verified: _move_fidelity.py:285-287]`
33. `test-ids` keys each test by the parts after its last `test_` module component, so a moved file keeps its tests' IDs. A declared `--expect-new` ID must occur more often after than before. `[verified: _move_fidelity.py:745-750, :813-818]`
34. `select-tests.py` maps any change under `claude/.claude/scripts` to the scripts tests and adds the hook tests for package changes. `[unverified — relayed from context-and-cache-commands.md row 37]`
35. None of the four new basenames exists anywhere in the repo. `[verified: Glob]`
36. No moved test carries the `timing` marker. The legacy file's one timing test (:501) stays. `[verified: grep]`
37. A pure move adds no CHANGELOG entry, and `require-stow-reminder.sh` requires the PR body to name `install.sh` or `stow` when a file is added under `claude/.claude/`. `[unverified — relayed from context-and-cache-commands.md row 41]`
38. `nudge-error-mode-analysis.sh` runs the shim on Python 3.11, and the shim imports both new modules. `[unverified — relayed from context-and-cache-commands.md row 43]`
39. No hook, plugin, eval, or skill names a moved internal symbol. `docs/transcript-analysis.md` names edit-format only as a CLI command (:683, :881). `claude-skills/` names neither command: its one `edit-format` hit is the case-study filename at `review-pr/REFERENCES.md:544`. `[verified: grep of the repo]`
40. The governing plan keeps `_UNCONDITIONAL_HEADER_CASES` and its two classes in the legacy file until every referenced group has moved, then relocates them in the `cli.py` phase. `[verified: transcript-analysis-decomposition.md:155-161]`

**Mechanisms:**
- M1: Two modules, one per command. `anchors: row4, row5, row8`. Alternatives: see the first Decision and Alternatives set aside.
- M2: Two per-command registrars, called at the old blocks' positions. `anchors: row3, row5`.
- M3: Two test-only `cmd_*` bindings and six leaf bindings, by name with noqa. `anchors: row7, row9, row40`. The lighter primitive, by-name binding, beats retargeting staying tests (Decision above).
- M4: F401 plus a grep limited to shim-loading files decides each orphaned import. Aliased keeps follow the :254-255 shape. `anchors: row7, row17`.
- M5: Two loader-free test files with per-command rename maps. The two banners are deleted, and the corpus tests stay. `anchors: G2, row11, row12, row13, row18`.
- M6: Two `nodes` passes over the legacy test source. `anchors: row14, row15`.
- M7: Two parametrized bootstrap tests over the existing `_seed_read_scope_account` plus an empty second config dir, accepting more than 1,000 lines. `anchors: row23, row24, row25, row26, row31`. Three lighter alternatives fail: an in-process parse check pins `func` but runs no handler and sees no root resolution; `_seed_priced_account` gives edit-format no seed-derived cell; and no test leaves both registrations unpinned.
- M8: The `__init__` docstring and the architecture-doc edits. `anchors: row19, row20, row21`.
- M9: The unchanged move-fidelity harness with one capture pair whose runs reach multi-root through `--config-dir` extras. `anchors: G3, row15, row16, row28, row29, row32, row33`. Two lighter alternatives fail: the selected suite alone proves neither byte-identical help and output nor verbatim nodes, and diff review by eye is not mechanical.
- M10: One `code-writer` dispatch, with no stages. `anchors: row3, row20`.
- M11: The selected suite runs in two passes, split by the `timing` marker. `anchors: G4, row36`.

## Critical files

**Dispatch: one `code-writer`, the move.** The shim, the legacy test file, the bootstrap file, the prefix test, `__init__.py`, and the architecture doc are shared by both commands, so no split yields disjoint file sets (plan-it Step 5's dispatch-split rule). The brief requires:
- copy moved code verbatim, extracted by line range from the parent's `<scratch>/before-shim.py` and `<scratch>/before-legacy.py`, applying only Parts 1–4's edits;
- keep new production code runnable on Python 3.11 (row 38);
- run every tool through `scripts/venv-run.sh <tool>` (row 32); if the wrapper reports a tool missing, stop and return without substituting another interpreter;
- if F821 flags a name absent from the rename maps, the stdlib, or conftest, stop and report;
- if a moved line other than :1091 needs reflow, stop and report (row 16);
- if any new file exceeds 1,000 lines, stop and report; `test_transcript_cli_bootstrap.py` is exempt from this rule (M7), and only its count is reported;
- do no staging and no commits.

Its return lists:
- every F401 disposition from Parts 2 and 3;
- the wrap-only reflow count;
- every new bootstrap test ID;
- `wc -l` for each new and changed file.

Files:
- **Create:**
  - `claude/.claude/scripts/transcript_analysis/edit_format.py`, `…/instrument_authoring.py`;
  - `claude/.claude/scripts/tests/test_transcript_edit_format.py`, `…/test_transcript_instrument_authoring.py`.
- **Modify:**
  - `claude/.claude/scripts/transcript-analysis.py`;
  - `claude/.claude/scripts/transcript_analysis/__init__.py`;
  - `claude/.claude/scripts/tests/test_transcript_analysis.py`, `…/test_transcript_cli_bootstrap.py`, `…/test_transcript_package_module_prefixes.py`;
  - `docs/transcript-analysis-architecture.md`.
- **Also in the diff:** `.claude/plans/edit-format-and-instrument-authoring.md`.

Its verification runs from the worktree root:
- `scripts/venv-run.sh pytest` over:
  - the two new test files;
  - `test_transcript_analysis.py`, `test_transcript_cli_bootstrap.py`, `test_transcript_package_module_prefixes.py`, `test_transcript_analysis_architecture_doc.py`, `test_transcript_analysis_import_cycles.py`;
  - `test_transcript_cost.py`, `test_transcript_review_rounds.py`, `test_transcript_plan_boundary.py`, and `test_transcript_handoff_signal_response.py`, the readers of retained shim bindings (row 7);
  - `claude/.claude/hooks/tests/test_nudge_error_mode_analysis.py`, which runs the real shim.
- `scripts/venv-run.sh ruff check claude/.claude/ claude-skills/ plugins/ evals/`, clean.
- `wc -l` on every new and changed file.

**Parent:** run Verification 0–1 before the dispatch, and Verification 3–9 after it, then `/code-review` and the commit. A review-round edit re-runs Verification 3, 5, 7, and 9 and the dispatch's test list above. It re-runs Verification 4 and 6 only when the round edits the shim or a package module.

**Rebase rule.** A rebase or merge that brings in a change under `claude/.claude/scripts`, `claude/.claude/hooks`, or `docs/transcript-analysis-architecture.md` invalidates every line range, snapshot, capture, and junit baseline. All of them are keyed to the pre-rebase `HEAD`. Verification 0 and 10 detect such a change.
- Before the commit: run the Abort procedure, then rebase, then restart at Verification 0 with a fresh `<scratch>`.
- After the commit: stop and report to the engineer.

**Abort procedure.** If the dispatch is interrupted, the code-writer cleans nothing, and the parent recovers:
1. `git status --short` must list only the six modified files above, the four created files, and the plan. On any other path, stop and report, touching nothing.
2. Restore each modified file with its own literal `git restore --source=HEAD -- <path>` call.
3. Delete each created file by its literal path.
4. Success: `git status --short` lists at most the plan.

Never use `git clean`, `git checkout .`, `git reset --hard`, or `git stash`.

**Explicitly unchanged:**
- `tests/conftest.py`, `_move_fidelity.py`, `select-tests.py`, and `test_select_tests.py` (rows 30, 34);
- every other package module, `scope.py` included, whose refusal tuple already lists both commands (row 23);
- `docs/transcript-analysis.md`, `claude-skills/skills/transcript-analysis/SKILL.md`, README.md, `docs/scripts.md`, and CHANGELOG.md (rows 37, 39);
- every hook, and every preserved record (row 20).

**Reuse:**
- `scope._add_project_scope_args`;
- the module and registrar shape at `cache_efficiency.py:1-15` and `:261-294`;
- the aliased noqa shape at shim :254-255;
- conftest's `fake_projects`, `fake_config_dir_factory`, `_opus`, `_asst`, `_user_msg`, `_bash_use`, `_agent_use`, `_write_use`, `_thinking_block`, `_hook_deny_current`, `_write_jsonl`, `_write_subagent_jsonl`, and `_write_cost_root`;
- bootstrap's `_run`, `_seed_read_scope_account`, and `_isolated_config_env`;
- `_move_fidelity.py` unchanged.

**PR body carries:**
- `Refs GH-1116`;
- each Verification result, with the harness counts: moved production nodes (48), moved test nodes (14 plus 6), and the junit-derived moved test counts;
- measured `wc -l` for every new or changed file. It flags the shim (about 1,144), the legacy file (about 4,410), and `test_transcript_cli_bootstrap.py` (about 1,229) as over 1,000 lines;
- the reflow count, expected to be 1;
- that the shim defines no `cmd_*` function after this PR;
- the engineer's answers on the revert rehearsal and the bootstrap pair (rows 30, 31);
- the rollback statement: "A plain `git revert` of the squash commit is expected to apply cleanly and restore the pre-PR tree only while no later commit has touched a file this PR touches or read a name it adds (the two modules and their two registrars). This is an inference; no revert was run. A later phase that edits the shim, the legacy test file, or `test_transcript_package_module_prefixes.py` makes those the predictable conflict sites, so once one merges, fix forward. Cross-phase reverts run last-in-first-out.";
- the re-stow instruction: after pulling, re-run `./install.sh`. It is needed only when `scripts/transcript_analysis/` is not inside a folded directory symlink. Without it the shim raises `ModuleNotFoundError` at import and every subcommand fails. The PR-create gate requires the body to name `install.sh` or `stow` (row 37);
- that no CHANGELOG entry is added, matching phase B (row 37);
- the follow-ups in Out of scope, listed in the body only, since this PR files no issue (row 2).

## Verification

Run everything from the worktree root. Notation:
- `<mf>` is `scripts/venv-run.sh python3 claude/.claude/scripts/tests/_move_fidelity.py`.
- `<pkg>` is `claude/.claude/scripts/transcript_analysis`, `<tests>` is `claude/.claude/scripts/tests`, and `<shim>` is `claude/.claude/scripts/transcript-analysis.py`.
- `<scratch>` is one `mktemp -d` directory, created before step 0. Use its printed path literally.
- `<attempt>` is a fresh `mktemp -d` directory for each dispatch attempt.

Run each git command and each capture as its own literal call. Seed files with the Write tool.

0. **Preconditions.**
   - `scripts/venv-run.sh python3 -c "__import__('tokenize').FSTRING_MIDDLE"`, `scripts/venv-run.sh pytest --version`, and `scripts/venv-run.sh ruff --version` each exit 0 (row 32). Otherwise stop before the dispatch is paid for.
   - Check main drift, each as its own call: `git fetch origin main`, where a non-zero exit stops the run, then `git diff --name-only HEAD...origin/main -- claude/.claude/scripts claude/.claude/hooks docs/transcript-analysis-architecture.md`. Any output invokes the Rebase rule.
   - `git status --short` shows nothing but the plan.
1. **Baseline, before the dispatch.**
   - Copy each of the six files Parts 2–4 change into `<scratch>`, each with its own plain `cp <path> <scratch>/before-<name>` call: `before-shim.py`, `before-legacy.py`, `before-bootstrap.py`, `before-prefix.py`, `before-init.py`, and `before-arch.md`. Create an empty `<scratch>/empty.py` with the Write tool.
   - **Account 1.** Seed `<scratch>/account/projects/-home-user-parityrepo/`. Every assistant record carries `"type": "assistant"`, model `claude-sonnet-5`, a distinct `requestId`, and usage `{"input_tokens": 10, "output_tokens": 10}`. Every user record carries `"type": "user"` and `"role": "user"`. Every record carries its file's `sessionId`, `isSidechain: false` unless stated, and a `2026-05-19T10:MM:00.000Z` timestamp. Each tool call is one `tool_use` block, and each listed result is one `tool_result` block with `"is_error": true`.
     - `s1.jsonl`:
       - 10:00 assistant, `Edit` `e1` with input `{"file_path": "/w/a.py", "old_string": "x=1", "new_string": "x=2"}`;
       - 10:01 user, result for `e1`, content `"String to replace not found in file."`;
       - 10:02 assistant, `Edit` `e2` with input `{"file_path": "/w/a.py", "old_string": "x = 1", "new_string": "x = 2"}`;
       - 10:03 assistant, `Write` `w1` with input `{"file_path": "/tmp/scratch/probe.py", "content": <40 g characters>}`;
       - 10:04 user, result for `w1`, content `"Blocked by plan-review gate: run /plan-review first."`;
       - 10:05 assistant, `Bash` `b1` with input `{"command": "python3 -c 'print(1)'"}`;
       - 10:06 assistant, `Agent` `a1` with input `{"subagent_type": "general-purpose", "prompt": "p"}`.
     - `s2.jsonl`:
       - 10:10 assistant, `Bash` `b2` with input `{"command": "cat <<EOF > /tmp/probe.py\n<60 f characters>\nEOF"}`;
       - 10:11 user, result for `orphan-1`, content `"String to replace not found"`.
     - `s2/subagents/agent-s1.jsonl`, every record with `isSidechain: true` and `sessionId` `s2`:
       - 10:12 assistant, `Edit` `e3` with input `{"file_path": "/w/b.py", "old_string": <150 h characters>, "new_string": "y"}`;
       - 10:13 assistant, `Bash` `b3` with input `{"command": "node -e 'console.log(1)'"}`.
   - **Account 2.** Seed `<scratch>/account2/projects/-home-user-otherrepo/r1.jsonl`: one 10:20 assistant record, `Edit` `e4` with input `{"file_path": "/w/c.py", "old_string": "abcde", "new_string": "z"}`.
   - Write `<scratch>/runs.txt` with run-01 to run-15, in this order:
     - run-01 `edit-format`
     - run-02 `edit-format --no-redact`
     - run-03 `edit-format --this-repo --projects x`
     - run-04 `edit-format --projects *nomatch*`
     - run-05 `edit-format --config-dir <scratch>/account2`
     - run-06 `edit-format --config-dir <scratch>/account2 --no-redact`
     - run-07 `edit-format --config-dir <scratch>/missing`
     - run-08 `edit-format --config-dir <scratch>/account2 --projects *parityrepo`
     - run-09 `instrument-authoring`
     - run-10 `instrument-authoring --projects *nomatch*`
     - run-11 `instrument-authoring --config-dir <scratch>/account2`
     - run-12 `instrument-authoring --no-redact`
     - run-13 `instrument-authoring --config-dir <scratch>/missing`
     - run-14 `--config-dir <scratch>/account edit-format`
     - run-15 `--config-dir <scratch>/account instrument-authoring`
   - Capture twice: `<mf> capture --script <shim> --account <scratch>/account --runs <scratch>/runs.txt --all-help --out <scratch>/before-a`, then the same into `before-b`. Then `<mf> compare-captures <scratch>/before-a <scratch>/before-b` must be clean, with no mask. A difference means a nondeterministic run: stop and report.
   - Coverage checks on `before-a`:
     - It holds `help-top` plus one help capture and one `.parser` file per subcommand. `git grep -c "add_parser("` over the shim and `<pkg>` governs the count.
     - Each moved command's `.parser` file contains `default func=<callable <script>:cmd_<name>>`.
     - Each moved command's help `.stdout` contains `transcript-analysis.py <command>` and its Part 3 bootstrap flag tokens (row 25).
   - Corpus liveness (row 29). `before-a` must hold these, each producible only by the seed:
     - in run-01, a `whitespace_only` cause row with count 1, a `plan-review` governance row with count 1, an `unpaired` summary line ending `: 1`, and a `100-299` size row with count 1;
     - `DO NOT PUBLISH` on both stdout and stderr in run-02;
     - `mean old_string chars/edit: n/a` in run-04;
     - in run-05, `EDIT FORMAT SOURCES (*; 2 roots)`, an `account-1` row with `calls=` 3, and an `account-2` row with `calls=` 1;
     - `account-2  no edit-family calls` in run-08;
     - in run-09, a `Bash (heredoc/-c/-e)` `main` row with count 2, a `Bash (heredoc/-c/-e)` `subagent` row with count 1, a `Write (scratchpad)` `main` row with count 1, and `zero_dispatch` and `dispatched` cohort lines each with `sessions=` 1;
     - `zero_dispatch` with `sessions=` 0 in run-10;
     - in run-11, `INSTRUMENT AUTHORING SOURCES (*; 2 roots)` and `zero_dispatch` with `sessions=` 2;
     - exit 2 in run-03; exit 2 with `--no-redact is refused` on stderr in run-06; exit 2 with `is not a directory` in run-07 and run-13; exit 2 with `unrecognized arguments: --no-redact` in run-12; and exit 2 with `has no effect here` in run-14 and run-15.

     If one is missing, fix the seed and recapture both sides.
   - **Junit baseline.** Run `scripts/venv-run.sh pytest` over `test_transcript_analysis.py`, `test_transcript_cli_bootstrap.py`, `test_transcript_package_module_prefixes.py`, `test_transcript_analysis_architecture_doc.py`, and `test_transcript_analysis_import_cycles.py`. Run it once with `-m "not timing" --junitxml=<scratch>/before-parallel.xml` and once with `-m timing -n0 --junitxml=<scratch>/before-timing.xml`. All tests pass.
2. **Dispatch** per Critical files, with a fresh `<attempt>`.
3. **Lint and sizes.**
   - `scripts/venv-run.sh ruff check claude/.claude/ claude-skills/ plugins/ evals/` is clean.
   - Record `wc -l` for every new and changed file. Every new file is at most 1,000 lines.
   - 3.11 evidence is an argument, not a parse check. The moved lines already run on 3.11, the rename map only prepends `module.` to a name, and the authored lines are docstrings, imports, registrar wrappers, and the one reflow. Read the `git diff` of the two modules. Confirm that no line outside the verbatim moved text contains an f-string, except the two halves of the reflowed :1091, which keep its quote style. If a 3.11 interpreter exists on the machine, `py_compile` the two modules with it. Otherwise record that the 3.11 compile did not run.
4. **Selected suite** (G4, M11). Run two passes, each as a Bash call with `run_in_background`, and start the second only after the first finishes:
   - `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py -m "not timing"`;
   - then `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py -m timing -n0`.

   Both must pass. Edit nothing until both finish. Before claiming or fixing a failure in a file outside Critical files, reproduce it at the merge-base, per CLAUDE.md:
   - Run `git merge-base HEAD origin/main` as its own call.
   - Run `git worktree add <scratch>/merge-base <sha>`.
   - Run the failing test there with `scripts/venv-run.sh pytest`, invoked from that worktree.
   - Run `git worktree remove <scratch>/merge-base`.
5. **Test-ID parity.**
   - Rerun step 1's two junit commands over the same files plus the two new test files, into `<attempt>/after-parallel.xml` and `<attempt>/after-timing.xml`.
   - `<mf> test-ids --before <scratch>/before-parallel.xml --before <scratch>/before-timing.xml --after <attempt>/after-parallel.xml --after <attempt>/after-timing.xml --file-prefix claude/.claude/scripts/tests/` must be clean, with one single-quoted `--expect-new` for each of six IDs (rows 19, 33):
     - `test_transcript_analysis_edit_format_and_instrument_authoring_help_exits_zero[<command>]` for edit-format and instrument-authoring;
     - `test_transcript_analysis_edit_format_and_instrument_authoring_subprocess_finds_seeded_session[<command>]` for the same two;
     - `test_module_imports_first_in_fresh_interpreter[<module>]` for `edit_format` and `instrument_authoring`.

     No `--rename` is needed. If the dispatch returns any other set of bootstrap IDs, stop and report.
6. **CLI and parser parity.**
   - Capture into `<attempt>/after` with step 1's flags.
   - `<mf> compare-captures <scratch>/before-a <attempt>/after --moved-callable cmd_edit_format=transcript_analysis.edit_format cmd_instrument_authoring=transcript_analysis.instrument_authoring` must be clean.
   - No `.stdout`, `.stderr`, `.exit`, or `.argv` file may differ.
7. **Move fidelity.**
   - **Production nodes.**
     - Run `<mf> nodes --before <scratch>/before-shim.py --after <pkg>/edit_format.py --after <pkg>/instrument_authoring.py` with `--moved` set to Part 1's 48 names.
     - Pass `--verbatim` for the 40 names that read no renamed name: the 19 edit-format names from `EDIT_FAMILY_TOOLS` through `_edit_notfound_cause` in Part 1's order, the 20 instrument-authoring names from `_INSTRUMENT_AUTHORING_HEREDOC_OPEN_RE` through `_new_instrument_authoring_cohort_totals`, and `_aggregate_instrument_authoring_sessions`.
     - Pass one `--rename` per entry of Part 1's 9-entry map.
     - It must be clean, with `compared-nodes: 48`.
   - **Shim remainder.**
     - Run `<mf> remainder --before <scratch>/before-shim.py --after <shim> --removed <the 48> --changed build_parser`.
     - Declare `--import-added` for the four triples of the two new import blocks.
     - Declare `--import-removed` for Part 2's nine removed import triples.
     - It reports only `remainder-comments` findings (row 15). Their before sides are exactly row 3's twelve comment blocks: the seven edit-format blocks, the banner-and-pointer block :716-721, and the four instrument-authoring blocks. Their after sides are empty or one of the two rationale lines Part 2 adds above the aliased imports. Each finding matches a `git diff` hunk. Any other finding stops the run.
     - `git diff` of `build_parser` shows exactly the two blocks replaced by two calls. Check the import-line comments in `git diff` by eye.
   - **New modules.** For each new module, `<mf> remainder --before <scratch>/empty.py --after <module> --added __doc__ <its top-level names> --import-added <its triples>` is clean, apart from its moved leading-comment `remainder-comments` findings: seven in `edit_format.py`, and five in `instrument_authoring.py` (:720-721, :723-727, :730-733, :739-744, :758-761).
   - **Test nodes, edit-format.**
     - Run `<mf> nodes --before <scratch>/before-legacy.py`, with `--after` set to `test_transcript_edit_format.py` and the final legacy file.
     - `--moved` Part 3's 14 edit-format names, `--verbatim` its 12 helpers, and pass its 7 renames.
     - It must be clean, with `compared-nodes: 14`.
   - **Test nodes, instrument-authoring.**
     - Run `<mf> nodes --before <scratch>/before-legacy.py`, with `--after` set to `test_transcript_instrument_authoring.py` and the final legacy file.
     - `--moved` Part 3's 6 instrument-authoring names, `--verbatim` its 4 helpers, and pass its 5 renames.
     - It must be clean, with `compared-nodes: 6`.
   - **Other remainders.**
     - The legacy file, with `--removed <the 20>` and `--import-removed` for the four conftest triples, reports only the two banners' `remainder-comments`.
     - Each new test file, compared against `empty.py` with `--added __doc__` and its names, is clean.
     - `remainder` is clean for:
       - `__init__.py` (`--changed __doc__`);
       - the prefix test (`--changed PRODUCTION_MODULES TEST_FILES`);
       - bootstrap (`--added` its four new names).
8. **Negative controls.** For each control:
   - run a plain `sha256sum <file>` first, and save its output line, one per edited file, to `<attempt>/ctl-<x>.sha256` with the Write tool;
   - edit by hand, run the check, then undo by hand;
   - `sha256sum -c <attempt>/ctl-<x>.sha256` must then pass;
   - never use `git checkout` or `git stash`;
   - put each control capture in a fresh `<attempt>/ctl-<x>` directory, and compare it against `<attempt>/after` with no `--moved-callable`.

   The controls:
   - (a) In `edit_format.py`'s registrar, bind `func=_edit_format_report`. Exactly `help-edit-format.parser` and the run-05 to run-08 files differ, because the bypassed `_resolve_cost_roots` drops the extra root, the refusal, and the directory check (row 24). Then run `test_transcript_cli_bootstrap.py`. Of the four new IDs, exactly the edit-format dispatch ID fails, on its missing `EDIT FORMAT SOURCES (*; 2 roots)`.
   - (b) In `instrument_authoring.py`'s registrar, delete the `--config-dir` `add_argument` call. Exactly `help-instrument-authoring.parser`, `help-instrument-authoring.stdout`, and the run-11, run-13, and run-15 files differ (row 27). Then run `test_transcript_cli_bootstrap.py`: exactly the two instrument-authoring IDs fail. Then run `test_transcript_analysis.py::TestConfigDirFlag`: it passes whole, which shows the bootstrap help case is the only committed pin on that flag besides the dispatch case, which also passes it.
   - (c) Three separate edits, each followed by a run of `test_transcript_cli_bootstrap.py` alone:
     - delete `_add_edit_format_subparser(sub)` from the shim's `build_parser`; both edit-format IDs fail, and no other new ID does. Then run `test_transcript_analysis.py::TestConfigDirFlag`: it passes whole (row 26);
     - in `edit_format.py`'s registrar, delete the `--no-redact` `add_argument` call; exactly the edit-format help ID fails. This shows the `--config-dir` help's mention of `--no-redact` does not satisfy the `[--no-redact]` token (row 25);
     - in `instrument_authoring.py`'s registrar, bind `func=lambda _args: None`; exactly the instrument-authoring dispatch ID fails.
   - (d) In `instrument_authoring.py`, bind `_resolve_project_scope` by name with `from transcript_analysis.scope import _resolve_project_scope` and call it bare at its one call site. Run `test_transcript_analysis.py::TestRootsThreadingSpy` and `test_transcript_package_module_prefixes.py`. The instrument-authoring row of `test_resolve_project_scope_and_header_receive_identical_multi_root_list` fails and no other spy row does, and at least `test_production_modules_import_package_siblings_by_module_only` fails (rows 10, 18).
   - (e) Remove `"test_transcript_edit_format.py"` from `TEST_FILES`. `test_loader_free_package_importing_test_files_are_in_test_files` fails.
   - The harness's own committed self-tests already pin `node-ast`, `moved-callable-unused`, and `id-missing`, so this step adds no controls for them. `[unverified — relayed from context-and-cache-commands.md Verification 8]`
9. **Leftovers and single homes.** Every `git grep` whose paths can reach a new file passes `--untracked`, because the four new files stay untracked until the commit. Each check that expects nothing is paired with a probe that must hit in the same paths.
   - `git grep --untracked -nE "^def (cmd_edit_format|_edit_format_report|_scan_edit_format_session|_edit_notfound_cause|cmd_instrument_authoring|_instrument_authoring_report|_scan_instrument_authoring_session|_extract_heredoc_payloads)\b|^(EDIT_FAMILY_TOOLS|_EDIT_GOVERNANCE_PATTERNS|_INSTRUMENT_AUTHORING_HEREDOC_OPEN_RE)\b" -- claude/.claude/scripts` returns one hit per name, in its owning module.
   - `git grep -n "^def cmd_" -- <shim>` returns nothing. Probe: `git grep -n "^def build_parser" -- <shim>` hits.
   - `git grep --untracked -nwE "_mod|spec_from_file_location" --` over the two new test files returns nothing. Probe: `git grep --untracked -l "^from transcript_analysis import" --` over the two new test files lists both.
   - `git grep --untracked -n "from _config_dir import" --` over the two new modules returns nothing. Probe: `^from transcript_analysis import` over the two new modules lists both.
   - `git grep --untracked -nE "_mod\.(_edit_format_report|_scan_edit_format_session|_old_string_size_bucket|EDIT_FAMILY_TOOLS|_instrument_authoring_report|_scan_instrument_authoring_session|_instrument_authoring_size_bucket|_new_instrument_authoring_stats|_aggregate_instrument_authoring_sessions)\b" -- <tests>` returns nothing. Probe: `git grep --untracked -n "edit_format._scan_edit_format_session(" -- <tests>` hits.
   - `git grep --untracked -nE "_mod\.(cmd_edit_format|cmd_instrument_authoring)\b" -- <tests>` returns only the two `_UNCONDITIONAL_HEADER_CASES` rows (row 9).
   - `git grep -nE "not-yet-moved|remain split|the shim itself defines" -- docs/transcript-analysis-architecture.md` returns nothing. Probe: `git grep -n "instrument_authoring.py" -- docs/transcript-analysis-architecture.md` hits.
   - `git grep -nwE "_SPAWN_TOOL_NAMES|_pct_of|_root_index_for_path|PurePosixPath" -- <shim>` returns nothing. Probe: `git grep --untracked -nE "pricing\._SPAWN_TOOL_NAMES|render\._pct_of" -- <pkg>/edit_format.py <pkg>/instrument_authoring.py` hits both names.
   - Last, the parent stages the change with one literal `git add <path>` call per Critical file. Then `scripts/venv-run.sh pytest claude/.claude/tests/test_pytest_collection_config.py::TestNoBareSameDirectorySiblingImports claude/.claude/hooks/tests/test_ticket_reference_discipline.py` must pass. Both list files with `git ls-files`.
10. **Main re-check, before `/ready-for-review`.** Repeat step 0's `git fetch` and `git diff` calls. Any output invokes the Rebase rule's after-commit branch: stop and report that output to the engineer.

## Out of scope

- **Stale prose inside the moved ranges** (row 21): shim :334-338, :361, and :378-380. DEFER: each moves verbatim so that `nodes` stays a clean proof. List them as one follow-up in the PR body.
- **Renaming the local `scope` in `_scan_instrument_authoring_session`,** which shadows the module import inside that function (row 22). DEFER: the rename edits moved text, and nothing in the function reads the module today.
- **`_UNCONDITIONAL_HEADER_CASES` and its two classes, `TestRootsThreadingSpy`'s shim-level patches, `TestBuildParser`, and the shim's noqa'd test-only bindings.** Once no command reads a bare shim binding, the shim-level patches (:3564-3565, :3605-3606) intercept no production call. The governing plan relocates the table in the `cli.py` phase (row 40), and the test-only shim-reader retarget the Context names owns the rest.
- **Moving `_read_tool_use` and `TestCorpusSubagentDirAndUtf8ErrorHandling` out of the legacy file** (row 11). They test `corpus.py`, and a leaf-module tests phase owns them.
- **Pinning edit-format's and instrument-authoring's membership in `_SUBCOMMANDS_REFUSING_TOP_LEVEL_CONFIG_DIR`.** `TestConfigDirFlag` parametrizes over the tuple itself, so dropping either name drops its refusal case with no test failing. Capture runs 14 and 15 catch it at verification time only. DEFER: a membership assertion is new authoring, the same follow-up #1324 deferred for its siblings.
- **Pinning edit-format's `--no-redact` and `extra_config_dirs` wiring through real argparse.** No committed test passes `--no-redact` through argparse at HEAD, so the move leaves the gap where it was. A dest typo can arise only from retyping, which Part 1 forbids, and the `.parser` dump in Verification 6 records each action's `dest`. The engineer asked for the architect's view on a reviewer's proposal to add a pin; the architect recommended deferring it, and the `cli.py` phase owns parser-wiring tests, as the phase-B plan states. The existing `*ArgparseWiring` classes (for example `TestCacheEfficiencyArgparseWiring`) are the precedent that phase follows.
- **An AST assertion that the shim's top-level functions are exactly `build_parser` and `main`.** The architecture doc gains a sentence saying the shim defines no `cmd_*`, and Verification 9's grep checks it once. DEFER: a durable assertion is new authoring, and the `cli.py` phase rewrites that shape again.
- **Splitting the shim, the legacy test file, or `test_transcript_cli_bootstrap.py` below 1,000 lines.** All three were already over the limit before this PR, and the shim's split is the `cli.py` phase's (row 8).
- **GH-1116 tracker edits and follow-up issues.** The session's default keeps both out of this PR (row 2).
- **The governing plan's revert rehearsal,** which the engineer waived (row 30).
- **Any CLI, help-text, or output change, and public names for the registrars.**
- **Preserved records naming moved symbols** (row 20).
