# Transcript-analysis decomposition, phase B: context-distribution, context-composition and cache-efficiency

## Context

Goal: move `cmd_context_distribution`, `cmd_context_composition` and `cmd_cache_efficiency`, with their private helpers, their `add_parser` blocks and their tests, out of the `claude/.claude/scripts/transcript-analysis.py` shim into one `transcript_analysis/` module per command, as a pure verbatim move with no behavior change.

Ask: "Ok if there’s no coupling two PRs it is let’s continue" — the engineer's reply to the session's proposal to split the five remaining shim commands into two PRs, this one first (context-distribution, context-composition, cache-efficiency) and the other later (edit-format, instrument-authoring). The grouping is the session's wording, accepted by that reply. Tracker: GH-1116, whose "small groups" row this PR finishes in part. The governing plan is `.claude/plans/transcript-analysis-decomposition.md`.

Why now: phase C (#1317) merged, and the shim now holds five `cmd_*` functions, in a 2,936-line file. These three share no helper with each other or with the other two (surveyed this session by `git grep` and AST scan), so they move as one PR without ordering constraints.

Intended outcome: the shim loses the three commands, their helpers and their parser blocks. Three new modules hold them, each with a registrar that keeps the command at its current position in `build_parser()`, so `--help` output and every command's stdout stay byte-identical. The tests move with the code, and the documents that call the context-composition code unmigrated are corrected.

Decisions this session:
- Two PRs, this one first: the engineer's reply quoted in the `Ask:` line.
- Where the `main()`-calling test in `test_context_composition.py` goes: the engineer selected "Move to legacy file (Recommended)".
- Whether to rename `test_context_composition.py`: the engineer selected "Rename (Recommended)".
- Whether to waive the governing plan's revert rehearsal for this PR: the engineer selected "Waive it (Recommended)".
- Whether to keep the new bootstrap subprocess tests in this PR: the engineer selected "Keep them (Recommended)".
- The session's own defaults, not decided by the engineer: one module per command; each registrar moves with its command; `cmd_*` bindings stay in the shim as noqa'd test-only names; `_cache_efficiency_args` moves to a shared helper file; cross-group test classes stay in the legacy file byte-identical; prose in moved ranges stays verbatim and known stale lines are deferred.

## Approach

Each command moves verbatim into a package module of its own: `transcript_analysis/context_distribution.py`, `context_composition.py`, and `cache_efficiency.py`. Each module gets one `_add_<command>_subparser(subparsers)` registrar, and `build_parser()` calls it at its old block's position. The tests move into three test files that load no shim. `test_context_composition.py` becomes `test_transcript_context_composition.py`, and `_cache_efficiency_args` moves to a new `tests/_cache_efficiency_helpers.py`. Tests that read `build_parser` or `main`, which only the shim has, stay in or move to the legacy file. Nothing moves into `conftest.py`.

This is a pure move, and no stow consumer sees a behavior change:
- Help, stdout, stderr, and exit codes stay byte-identical across single-root and multi-root runs (Verification 6).
- The parser dumps differ only by three declared handler relocations.
- `docs/transcript-analysis.md` and the `/transcript-analysis` skill name these commands only as CLI invocations, which do not change (row 46).
- The only visible difference is that an uncaught exception's traceback names the new module files instead of the shim.

**Part 1: production layout.** Copy moved code verbatim, extracting it by line range from Verification 1's scratch snapshot. Never retype it. Extract with a script that reads the snapshot and writes the new file. If a write-shape hook denies that, stop and report instead of retyping. Shim line ranges are at `6f24337b` (row 3); a moved HEAD invokes the Rebase rule. Only these edits are allowed:
- the production rename map below;
- each module's docstring and imports;
- the registrar wrappers and their parameter name;
- wrap-only reflow past 130 columns, with the count reported. Exactly one moved line needs it, :1512 (row 34). Any other line that needs it signals a bad substitution: stop and report.

Every module starts with its docstring, then `from __future__ import annotations`, then stdlib imports, then one `from transcript_analysis import …` line (row 5). ruff F821 and F401 decide the stdlib imports. Each authored docstring below is one logical line; wrap it in `sessions.py`'s shape. No `.py` text the plan adds names a ticket or a line number.

- **`transcript_analysis/context_distribution.py`** holds :313-505 verbatim (`cmd_context_distribution`, `_context_distribution_report`), then `_add_context_distribution_subparser`.
  - Package import: `from transcript_analysis import corpus, pricing, render, scope`. Expected stdlib: `argparse`, `sys`, `Sequence`, `Path`.
  - Docstring: `"""The context-distribution command: cmd_context_distribution, each session's peak main-thread context-at-turn bucketed at candidate percentage and absolute-token thresholds with each threshold's session and dollar shares, and its subparser registration.\n\nImports corpus, pricing, render, and scope by module (attribute access, not by name) -- see scope.py's own top-of-file comment for why."""`
- **`transcript_analysis/context_composition.py`** holds the design-pointer comment :1294, then :1296-1723 verbatim: 21 names, each with its leading comment. They are `_CATEGORY_USER_TEXT`, `_CATEGORY_ASSISTANT_TEXT`, `_CATEGORY_ASSISTANT_THINKING`, `_CATEGORY_COMPACT_SUMMARY`, `_CATEGORY_TOOL_CALL`, `_CATEGORY_TOOL_RESULT`, `_CATEGORY_UNCLASSIFIED`, `_normalize_composition_tool_name`, `_classify_content_item`, `_split_context_sequences`, `_context_composition_turn_rate_scale`, `_CONTEXT_COMPOSITION_RESIDUAL_INSTABILITY_REFUSAL_THRESHOLD`, `_CONTEXT_COMPOSITION_SPLIT_DISCREPANCY_TOLERANCE`, `_context_composition_residual_instability`, `_new_context_composition_stats`, `_merge_context_composition_stats`, `_scan_context_composition_sequence`, `_scan_context_composition_session`, `cmd_context_composition`, `_context_composition_report`, and `_print_context_composition_report`. The banner rules and bare `#` line :1290-1293 are deleted, not moved. Then `_add_context_composition_subparser`.
  - Package import: `from transcript_analysis import corpus, pricing, read_scope, render, scope, subagents`. Expected stdlib: `argparse`, `json`, `math`, `sys`, `Counter`, `defaultdict`, `Sequence`, `Path`.
  - Docstring: `"""The context-composition command: cmd_context_composition, rate-weighted token-turns by content-item category over each context sequence, gated by the static-prefix reconciliation residual's per-sequence instability, and its subparser registration.\n\nImports corpus, pricing, read_scope, render, scope, and subagents by module (attribute access, not by name) -- see scope.py's own top-of-file comment for why."""`
- **`transcript_analysis/cache_efficiency.py`** holds :1727-1968 verbatim: the leading comment :1727-1730 and 10 names. They are `_COLD_READ_COLLAPSE_MARGIN`, `_cache_prefix_total`, `_is_cold_read_collapse`, `_new_cache_efficiency_stats`, `_merge_cache_efficiency_stats`, `_scan_cache_efficiency_group`, `_print_cache_efficiency_table`, `_print_cache_efficiency_report`, `cmd_cache_efficiency`, and `_cache_efficiency_report`. Then `_add_cache_efficiency_subparser`.
  - Package import: `from transcript_analysis import corpus, pricing, render, scope`. Expected stdlib: `argparse`, `sys`, `Sequence`, `Path`.
  - Docstring: `"""The cache-efficiency command: cmd_cache_efficiency, the per-thread cold-cache read-collapse census of cache read and write tokens, with a per-account breakdown above one root, and its subparser registration.\n\nImports corpus, pricing, render, and scope by module (attribute access, not by name) -- see scope.py's own top-of-file comment for why."""`
- **Registrars.** Each takes `build_parser()`'s subparsers action as `subparsers` and returns `None`, in the `sessions.py:52-69` shape.
  - Docstring: `"""Register the <command> subparser on build_parser()'s subparsers action, binding cmd_<name> through set_defaults(func=...)."""`
  - Each body is its shim block verbatim, with two substitutions: the `sub.add_parser(` receiver `sub` → `subparsers`, and `_add_project_scope_args` → `scope._add_project_scope_args`.
  - Blocks: context-distribution :2223-2256, context-composition :2342-2378, cache-efficiency :2380-2409.
- **Production rename map.** It applies to code only, never to docstrings or comments. It has 31 entries, and ruff F821 drives the prefixes. Never run a regex over bare names (row 7).
  - To `scope.` (9): `_resolve_cost_roots`, `_DO_NOT_PUBLISH_BANNER`, `_parse_since_nd_arg`, `_resolve_project_scope`, `_projects_glob`, `_redaction_ordinals`, `_scan_root_transcripts`, `_root_index_for_path`. Also `_print_resolved_scope` → `scope.print_resolved_scope`.
  - To `pricing.` (15): `_price_turn`, `_context_window_for_model`, `_session_peak_context`, `_CONTEXT_DISTRIBUTION_THRESHOLD_PCTS`, `_CONTEXT_DISTRIBUTION_THRESHOLD_ABS`, `_FAST_MODE_RATE_MULTIPLIER`, `_INFERENCE_GEO_US_RATE_MULTIPLIER`, `_CACHE_READ_MULTIPLIER`, `_CACHE_WRITE_1H_MULTIPLIER`, `_CACHE_WRITE_5M_MULTIPLIER`, `_cache_write_split`, `_context_at_turn`, `_count_subagent_spawns`, `_warn_if_subagent_format_drift`. Also `_dedup_turns_by_request_id` → `pricing.dedup_turns_by_request_id`.
  - To `corpus.` (2): `_parse_ts`, `_read_session_file_partitioned`.
  - To `render.` (3): `_context_distribution_rows`, `_content_text`, `_pct_of`.
  - `_READ_SCOPE_CHARS_PER_TOKEN` → `read_scope._READ_SCOPE_CHARS_PER_TOKEN`, and `_MCP_TOOL_BUCKET_LABEL` → `subagents._MCP_TOOL_BUCKET_LABEL`.
  - The existing `scope.PROJECTS_DIR` reads are already in module form. The `scope.` calls keep `TestRootsThreadingSpy` intercepting (row 9).

**Part 2: shim.**
- Delete :313-507 and :1290-1970, leaving two blank lines between the remaining top-level definitions. That removes the 33 moved names, the banner, and the leading comments that moved with their names.
- In `build_parser()`:
  - :2223-2256 becomes `_add_context_distribution_subparser(sub)`.
  - :2342-2409 becomes two consecutive calls: `_add_context_composition_subparser(sub)`, then `_add_cache_efficiency_subparser(sub)`.
  - Each run keeps one blank line on each side, so registration order and the `--help` listing stay as they are.
- Add one by-name import block per module, each at its sorted position (ruff I001 decides).
  - **cache_efficiency.** The block imports `_add_cache_efficiency_subparser`, `_cache_efficiency_report  # noqa: F401`, and `cmd_cache_efficiency  # noqa: F401`, with this comment: `# _add_cache_efficiency_subparser is read bare by this file's own build_parser (the cache-efficiency subparser's registration). cmd_cache_efficiency and _cache_efficiency_report are read only via _mod.<name> from test files.` (row 8)
  - **context_distribution, context_composition.** Each block imports the registrar and `cmd_<name>  # noqa: F401`, with this comment: `# _add_<name>_subparser is read bare by this file's own build_parser (the <command> subparser's registration). cmd_<name> is read only via _mod.cmd_<name> from test files.` (row 8)
- Rewrite the two import blocks whose comments name a moved consumer (row 36):
  - `transcript_analysis.read_scope` (:181-187) drops `_READ_SCOPE_CHARS_PER_TOKEN`. Its comment becomes `# Read bare by this file's own still-monolithic build_parser (the read-scope subcommand's own set_defaults).`
  - `transcript_analysis.subagents` (:287-293) drops `_MCP_TOOL_BUCKET_LABEL`. Its comment becomes `# Read bare by this file's own still-monolithic build_parser (the subagents subcommand's own set_defaults).`
- **Orphaned imports.** For each name ruff F401 flags after the deletion, a `_mod.<name>` grep decides its fate. The grep is `git grep -nE "_mod\.<name>\b|\(_mod, *[\"']<name>[\"']" -- claude/.claude/scripts/tests`, and a hit counts only in a file whose `_SCRIPT` names `transcript-analysis.py`, and only on a line outside the moved test slices (rows 10, 11).
  - **A test reads it.** Keep the import with `# noqa: F401 -- read only via _mod.<name> from test files`.
  - **No test reads it.** Remove the import.
  - **At plan time (row 10):**
    - Keep, with the noqa: `_parse_ts`, `_read_session_file_partitioned`, `_cache_write_split`, `_context_at_turn`, `_context_window_for_model`, `_price_turn`, `_content_text`, `_parse_since_nd_arg`, `_scan_root_transcripts`.
    - Remove: `import math`; `defaultdict` from the `collections` line; from `transcript_analysis.pricing`, `_CACHE_READ_MULTIPLIER`, `_CACHE_WRITE_1H_MULTIPLIER`, `_CACHE_WRITE_5M_MULTIPLIER`, `_CONTEXT_DISTRIBUTION_THRESHOLD_ABS`, `_CONTEXT_DISTRIBUTION_THRESHOLD_PCTS`, `_FAST_MODE_RATE_MULTIPLIER`, `_INFERENCE_GEO_US_RATE_MULTIPLIER`, `_count_subagent_spawns`, `_session_peak_context`, `_warn_if_subagent_format_drift`; `_context_distribution_rows` from `render`; `_projects_glob` from `scope`; and the two names the rewritten blocks drop.
  - **Unexpected names.** A flagged name outside these 25 gets the same rule, and the return lists it.
- The module-list comment (:18-25) stays as written: no `_mod.<module>` reader is lost or added (row 12).

**Part 3: tests.** The three new test files load no shim. They import package modules directly, as the `fake_projects` fixture permits (row 20). Nothing on a moved line changes except the test rename maps below, applied to code only, and wrap-only reflow.
- **`tests/test_transcript_context_distribution.py`** holds legacy :762-805 (`_CONTEXT_DISTRIBUTION_ABS_TABLE_HEADER`, `_extract_context_distribution_row`, `_extract_context_distribution_abs_row`) and `TestContextDistribution` (:1547-1779).
  - Rename map (7): `_mod._context_distribution_report` → `context_distribution._context_distribution_report`; `_mod.cmd_context_distribution` → `context_distribution.cmd_context_distribution`; `_mod.scope` → `scope`; `_mod._DO_NOT_PUBLISH_BANNER` → `scope._DO_NOT_PUBLISH_BANNER`; `_mod._price_turn` → `pricing._price_turn`; `_mod._session_peak_context` → `pricing._session_peak_context`; `_mod._context_distribution_rows` → `render._context_distribution_rows`.
  - Package import: `from transcript_analysis import context_distribution, pricing, render, scope`.
  - Docstring: `"""Tests for transcript_analysis/context_distribution.py's cmd_context_distribution: threshold crossings and dollar shares in the percentage and absolute-token tables, main-thread-only peaks, requestId dedup, the --no-redact refusal and banner, the empty scope, and the pricing.py and render.py helpers it reads."""`
- **`tests/test_transcript_cache_efficiency.py`** holds `TestCachePrefixTotal`, `TestCacheEfficiencyClassifier`, `TestCacheEfficiencyGroupScan`, and `TestCacheEfficiency` (:2887-3185).
  - Rename map (8): `_mod.<name>` → `cache_efficiency.<name>` for `_cache_prefix_total`, `_is_cold_read_collapse`, `_new_cache_efficiency_stats`, `_scan_cache_efficiency_group`, `_cache_efficiency_report`, and `cmd_cache_efficiency`; `_mod.scope` → `scope`; `_mod._DO_NOT_PUBLISH_BANNER` → `scope._DO_NOT_PUBLISH_BANNER`.
  - Package import: `from transcript_analysis import cache_efficiency, scope`, plus `from ._cache_efficiency_helpers import _cache_efficiency_args`.
  - Docstring: `"""Tests for transcript_analysis/cache_efficiency.py: the prefix total, the read-collapse classifier, the per-group scan's chain keying and compaction reset, and cmd_cache_efficiency end to end, including the --no-redact refusal and the per-account breakdown."""`
- **`tests/_cache_efficiency_helpers.py`** holds `_cache_efficiency_args` (:2872-2884) verbatim and needs no imports. Docstring: `"""Test helpers shared by test_transcript_cache_efficiency.py and by test_transcript_analysis.py's cross-group cache-efficiency tests."""` Both consumers import it as `from ._cache_efficiency_helpers import _cache_efficiency_args` (row 18).
- **`tests/test_transcript_context_composition.py`** replaces `tests/test_context_composition.py`, which is deleted (row 16). It holds every top-level node of the old file except `TestConfigDirRouting`: `_context_composition_args` and 17 classes.
  - Delete the loader (:16-20) and the imports only it used. F401 decides; `importlib.util` and `sys` are expected, and `Path` stays for a type hint at :587.
  - The docstring's first line changes `transcript-analysis.py's` to `transcript_analysis/context_composition.py's`. The rest of the docstring stays verbatim.
  - Package import: `from transcript_analysis import context_composition, corpus, read_scope, scope, subagents`. The `.conftest` import line stays as it is.
  - Rename map (23): `_mod.<name>` → `context_composition.<name>` for the 18 moved names it reads (`_scan_context_composition_session`, `_new_context_composition_stats`, `_merge_context_composition_stats`, `_print_context_composition_report`, `_context_composition_residual_instability`, `_split_context_sequences`, `_scan_context_composition_sequence`, `_context_composition_report`, `cmd_context_composition`, `_classify_content_item`, `_normalize_composition_tool_name`, and all seven `_CATEGORY_*` constants); `_mod._parse_ts` → `corpus._parse_ts`; `_mod._DO_NOT_PUBLISH_BANNER` → `scope._DO_NOT_PUBLISH_BANNER`; `_mod.scope` → `scope`; `_mod._READ_SCOPE_CHARS_PER_TOKEN` → `read_scope._READ_SCOPE_CHARS_PER_TOKEN`; `_mod._MCP_TOOL_BUCKET_LABEL` → `subagents._MCP_TOOL_BUCKET_LABEL`.
- **Legacy `test_transcript_analysis.py`**:
  - Delete the nine moved names and the `# context-distribution` banner (:1542-1544).
  - Insert `TestConfigDirRouting` (old composition file :730-752) verbatim after `TestConfigDirFlag` (:133-268) (rows 13, 14).
  - Import `_cache_efficiency_args` from the helper module. Remove only the imports F401 then flags; `_compact_boundary_rec` is expected.
  - These stay byte-identical: the `# cache-efficiency` banner (:2867-2869, which now heads `TestCacheEfficiencyArgparseWiring`), `TestCacheEfficiencyArgparseWiring`, `TestCacheRebuildCrossInstrumentReconciliation`, `TestFormatDriftCanary`, `_UNCONDITIONAL_HEADER_CASES` and its two classes, and `TestConfigDirFlag`.
- **`tests/conftest.py`.** In `_write_jsonl`'s docstring (:40), `test_context_composition.py` becomes `test_transcript_context_composition.py`. This is the only edit to the file, and it promotes nothing (row 36).
- **`test_transcript_package_module_prefixes.py`.** Append `context_distribution.py`, `context_composition.py`, and `cache_efficiency.py` to `PRODUCTION_MODULES`. Append the three new test files to `TEST_FILES`, which `test_loader_free_package_importing_test_files_are_in_test_files` requires (row 20).
- **`test_transcript_cli_bootstrap.py`** gains five definitions after the git-and-sessions dispatch test (:633-661). They reuse `_seed_priced_account` and `_isolated_config_env` and add no transcript seed. The only new setup is an empty second config dir (rows 24, 25, 49).
  - `test_transcript_analysis_context_and_cache_help_exits_zero` is parametrized over `(command, flag_tokens)` with `ids=["context-distribution", "context-composition", "cache-efficiency"]`.
    - `flag_tokens` is `("--since", "[--no-redact]")` for context-distribution and context-composition, and `("[--no-redact]",)` for cache-efficiency. No flag is unique to one of the three (row 51).
    - A one-line comment above the cases: `# The usage-line form [--no-redact], because each registrar's --config-dir help also names --no-redact.`
    - It runs `_run("transcript-analysis.py", command, "--help")`. It asserts exit 0, that `f"transcript-analysis.py {command}"` is in stdout (row 26), and that each flag token is in stdout.
    - Docstring: `"""Proves, in a fresh interpreter, that build_parser calls the context-distribution, context-composition, and cache-efficiency registrars, and that each registrar still adds its --no-redact flag and, where it has one, its --since flag."""`
  - Three per-case callables:
    - `_assert_context_distribution_seeded_cells` asserts `"Sessions in scope: 1   Total priced dollars: 2.00"` is in stdout.
    - `_assert_context_composition_seeded_cells` asserts `"Sequences scanned: 1   Turns scanned: 1"` is in stdout.
    - `_assert_cache_efficiency_seeded_cells` asserts `_table_cols(stdout, header_contains="Thread", row_contains="main", occurrence=1)["Turns"] == "1"`. Two roots print three `Thread` headers, one per table, and `occurrence=1` reads the all-roots table (row 50).
  - `test_transcript_analysis_context_and_cache_subprocess_finds_seeded_session` is parametrized over `(command, expected_token, resolved_root_token, assert_seeded_cells)` with the same `ids`.
    - Its tokens are `## Context distribution report`, `## Context composition report`, and `## Cache efficiency by thread`, each printed by one handler only (row 25).
    - Its resolved-root tokens are `context-distribution: account-2: scanned 0 transcripts`, `context-composition: account-2: scanned 0 transcripts`, and `### account-2`. Each prints only when the handler received `_resolve_cost_roots`'s list with the second root in it (rows 49, 50).
    - Parametrization runs before `tmp_path` exists, and every case has the same argv shape, so the body builds argv from `command` and needs no argv factory. It sets `config_dir = _seed_priced_account(tmp_path)`, creates `extra_config_dir = tmp_path / "account2"` holding only an empty `projects/`, and runs `_run("transcript-analysis.py", command, "--config-dir", str(extra_config_dir), env=_isolated_config_env(config_dir, tmp_path))`.
    - It asserts exit 0, the token, the resolved-root token, and the callable, in that order.
    - Docstring: `"""Proves, in a fresh interpreter, that real argparse dispatches context-distribution, context-composition, and cache-efficiency each to its own cmd_* handler through set_defaults(func=...), and that the subcommand-level --config-dir reaches _resolve_cost_roots. All three refuse the top-level --config-dir, so _isolated_config_env pins the first account and the subcommand flag adds an empty second one."""`
  - The file grows from 1,073 to about 1,130 lines. The PR body flags it (row 23).

**Part 4: docs and comments.**
- **`transcript_analysis/__init__.py`.** Append `context_distribution, context_composition, cache_efficiency` to the command-group module list.
- **`docs/transcript-analysis-architecture.md`** (row 36):
  - **Intro.**
    - Add the three modules to the imports-back-into list (:16-22).
    - Delete the two still-unmigrated sentences, :25-26 and :47-48.
    - After :36, add: "`context_composition.py` imports `read_scope.py` (for `_READ_SCOPE_CHARS_PER_TOKEN`) and `subagents.py` (for `_MCP_TOOL_BUCKET_LABEL`)."
  - **Registrar paragraph (:54-64).** Add context-distribution, context-composition, and cache-efficiency to the registrar sentence. Add `cmd_context_distribution`, `cmd_context_composition`, `cmd_cache_efficiency`, and `cache_efficiency.py`'s `_cache_efficiency_report` to the test-only bindings sentence, whose read shape `_mod.cmd_<command>` becomes `_mod.<name>`.
  - **`read_scope.py` section (:355-357).** "`cmd_read_scope` and `_READ_SCOPE_CHARS_PER_TOKEN` are the two names reached bare from the shim" becomes "`cmd_read_scope` is the one name reached bare from the shim", and add: "`context_composition.py` reads `_READ_SCOPE_CHARS_PER_TOKEN` as `read_scope._READ_SCOPE_CHARS_PER_TOKEN`."
  - **`subagents.py` section (:486-488).** The two-names sentence becomes "`cmd_subagents` is the one name reached bare from the shim, which `build_parser()` wires up. `context_composition.py` reads `_MCP_TOOL_BUCKET_LABEL` as `subagents._MCP_TOOL_BUCKET_LABEL`."
  - **Three new `### <module>.py` sections** go after `sessions.py` (row 21). Each states the module's responsibilities, its module imports, and that its registrar is the only name `build_parser()` reaches bare. The `context_distribution.py` section also states that the thresholds and `_session_peak_context` stay in `pricing.py` and `_context_distribution_rows` stays in `render.py`. The `context_composition.py` section names its two cross-command reads.
  - **Cache-rebuild paragraph (:780-784).** End the sentence after "it spans both cache-rebuild and cache-efficiency." Then add: "`.claude/plans/transcript-analysis-decomposition.md`'s rule for a cross-group test (stated there for `_UNCONDITIONAL_HEADER_CASES`) keeps such a test in the legacy file at least until every group it references has moved." (row 19)
  - **Tests section.** After the turn-shape paragraph (:891-901), add a paragraph that:
    - names the three new test files and states that none loads the shim;
    - names `tests/_cache_efficiency_helpers.py` as a plain module that the legacy file also imports, as `from ._cache_efficiency_helpers import ...` (see `.claude/rules/test-tree-packaging.md`);
    - states that `TestCacheEfficiencyArgparseWiring` and `TestConfigDirRouting` stay in `tests/test_transcript_analysis.py` because they read `build_parser` and `main`, which only the shim has.

**Prose rule.** Moved text stays verbatim apart from the rename maps. Known stale lines inside moved ranges move unchanged and are deferred (Out of scope; row 35). Prose outside the moved ranges that the move makes false is fixed in this PR (Part 2's import comments, `conftest.py`, and the doc edits; row 36).

**Decision: the shim keeps four test-only bindings, not three.** The session proposed `cmd_context_distribution`, `cmd_context_composition`, and `cmd_cache_efficiency`. `TestCacheRebuildCrossInstrumentReconciliation` also reads `_mod._cache_efficiency_report` (row 8), so the shim binds it too.
- Retargeting that class to `_mod.cache_efficiency._cache_efficiency_report` would edit a byte-identical class and add `cache_efficiency` to the shim's module-import list.
- A by-name noqa binding is the lighter primitive, and the `cli.py` phase removes it with the rest.

**Decision: `TestConfigDirRouting` moves to the legacy file whole.** The engineer selected "Move to legacy file (Recommended)" for its `main()`-calling test (row 14). Its sibling test asserts `"context-composition" in _mod._SUBCOMMANDS_REFUSING_TOP_LEVEL_CONFIG_DIR`, and it moves with the class (row 15):
- The tuple it pins is consumed only by `main()`, so it tests CLI routing, the same concern as its sibling and `TestConfigDirFlag`.
- The shim binds that tuple bare for `main()` (row 13), so the class moves byte-identical and `nodes --verbatim` proves it.
- Splitting the class would author a new class and edit test text, which a pure move avoids.

**Decision: `TestCacheEfficiencyArgparseWiring` stays in the legacy file, byte-identical.** It reads `_mod.build_parser()` (row 17). The new test files load no shim, and the `cli.py` phase owns the legacy file's parser tests.
- A shim loader in `test_transcript_cache_efficiency.py`, as four older family files carry, would make the file shim-loading for two tests.
- Retargeting the test to call `cache_efficiency._add_cache_efficiency_subparser` on a fresh parser would stop proving `build_parser()`'s wiring.

**Decision: the cross-group classes stay byte-identical.** `TestFormatDriftCanary` spans subagents, skill-pair, and cache-efficiency. `TestCacheRebuildCrossInstrumentReconciliation` spans cache-rebuild and cache-efficiency. Every group either spans has moved once this PR lands, so the governing rule now allows a move but does not require it (row 19). `TestMultiRootFormatOutliers` and `TestPerRecordConsumersCreditPrefixToTheBranchEntered` set the precedent by staying after their groups moved. Moving either class would widen the diff into a family file and its helper module. Their future home is in Out of scope.

**Decision: `_cache_efficiency_args` goes to `tests/_cache_efficiency_helpers.py`.** Two files read it (row 18), and one copy is required (CLAUDE.md § Engineering Judgment, "Single source of truth").
- A helper module is the series' shape: ten `_<family>_helpers.py` modules exist.
- `conftest.py` is shared by every test file, wider than the two readers need. A fixture added there is the revert hazard the governing plan's item 7 names (row 29).
- Importing from a test module couples two test files.

**Decision: the bootstrap pair goes in `test_transcript_cli_bootstrap.py`, reusing `_seed_priced_account`.** The file is already over 1,000 lines, and nothing enforces the limit (row 23). Phase C set the series' convention of flagging the file in the PR body rather than splitting it ad hoc (row 24). One existing seed gives all three handlers a seed-derived cell (row 25), so the session's proposal of a composition seed with a tool block is not needed. The pair is also this PR's only durable pin on the context-distribution and context-composition registrars. The help case pins their flags (row 51). The dispatch case's empty second account proves each handler resolved roots through `_resolve_cost_roots`, which a `func=_<command>_report` binding or a lost `extra_config_dirs` dest skips (rows 49, 50). `TestCacheEfficiencyArgparseWiring` already pins cache-efficiency's `func` and both dests (legacy :3193-3205).
- An in-process `build_parser().parse_args([command]).func` check would be lighter and would pin `func`, but it runs no handler in a fresh interpreter, and it breaks the series' bootstrap shape.
- A dispatch run without `--config-dir`, asserting the `account-1` scan line, is lighter too. But cache-efficiency prints no such line at one root, and the run cannot catch a lost `--config-dir` flag or `extra_config_dirs` dest.
- A fresh-parser registrar test in each new test file would add three test definitions beyond the two row 48 keeps in this PR.
- A sibling bootstrap file would split on line count, not concept, and needs `_run` extracted into a helper module.

**Decision: `pricing.py`'s and `render.py`'s context-distribution leaves stay where they are.** `_session_peak_context`, both `_CONTEXT_DISTRIBUTION_THRESHOLD_*` tuples, and `_context_distribution_rows` have no production reader besides context-distribution (row 42). Pulling them into `context_distribution.py` would edit two leaf modules and change what this PR proves from a verbatim move into a refactor. Out of scope records it.

**Decision: no revert rehearsal for this PR.** The governing plan requires one at each phase boundary (row 29). Phase C's waiver covered only that phase. The engineer waived it for this PR as well by selecting "Waive it (Recommended)" (row 29). The reasons that follow are this plan's, not the engineer's (row 47):
- This PR adds nothing to `conftest.py`'s fixtures, so the plan's stated failure mode does not arise.
- Before merge, reverting this branch's commit only restores the merge-base tree.
- After merge, a revert stays clean only until the next phase touches these files, which the PR body's rollback statement already covers.

Verification carries no rehearsal step.

**Alternatives set aside.** At the whole-plan level:
- **One `context.py` for context-distribution and context-composition.** The two share no helper (row 4). Composition's docstring names distribution, but its code calls nothing in it.
- **One module for all three.** It would hold about 1,000 lines (row 6), and it groups commands that share nothing, against the package's seam rule (row 5).
- **Cache-efficiency inside `cache_rebuild.py`.** The two share no code. `TestCacheRebuildCrossInstrumentReconciliation` reconciles their outputs, which a test does across modules.
- **Leaving the `add_parser` blocks in the shim**, the form 19 older modules use. Every module moved since session-signal carries a registrar (row 5), and leaving the blocks would keep about 100 lines the `cli.py` phase must move anyway.
- **A separate declared-roots capture pair.** `--config-dir` extras reach the same multi-root code in the moved functions (row 27).

**Assumption ledger.**

**Root:** The shim (2,936 lines) still holds the context-distribution, context-composition, and cache-efficiency commands. Their 33 top-level names (:313-505, :1290-1723, :1727-1968) and three `add_parser` blocks (:2223-2256, :2342-2378, :2380-2409) come to about 960 lines. Their tests are about 590 lines of the 5,930-line legacy test file, plus the 828-line `test_context_composition.py`, which loads its own shim copy. This PR moves them into three package modules and three loader-free test files and leaves one definition per name. CLI bytes stay identical, and the parser configuration changes only by three declared handler relocations. `[verified: transcript-analysis.py read at the cited ranges, last line 2936; test_transcript_analysis.py last line 5930; test_context_composition.py last line 828]`

**Givens:**
- G1. The shim keeps `build_parser()`, `main()`, and every unmigrated `cmd_*` until the `cli.py` phase. Reason: changing it needs a decision outside this plan, the engineer's phase order recorded in the tracker; without it, this plan would also have to rehome `build_parser()` and `main()`, the `cli.py` phase's work. `[verified: transcript-analysis-decomposition.md:155-161]`
- G2. pytest's `prepend` import mode makes test basenames global, so new files need unique names. Reason: pytest owns this behavior. `[verified: transcript-analysis-decomposition.md:233-236]`
- G3. A monkeypatch reaches code only through the binding that code reads at call time. Reason: Python import semantics. `[unverified — relayed from turn-shape-and-git-commands.md G3]`
- G4. Agents run `select-tests.py`, not the full suite, outside CLAUDE.md's two named cases. Reason: changing it needs a decision outside this plan, a change to the project's test-scope rule in CLAUDE.md § "Commands"; without it, Verification 4 would run the full suite instead of two `select-tests.py` passes. `[verified: CLAUDE.md "Commands"]`

**Rows:**
1. The engineer accepted the session's proposal to split the five remaining shim commands into two PRs, this one first. `[engineer-verified: "Ok if there’s no coupling two PRs it is let’s continue"]` The tag covers only the two-PR split and its order, read against that proposal.
2. The grouping wording and the session's eight proposals are the session's, not the engineer's. `[unverified]` This plan keeps proposals 1, 2, 4, 5, 6, and 8. It widens 3 to four bindings (row 8) and narrows 7 to one reused seed (row 25). It also keeps `TestCacheEfficiencyArgparseWiring` in the legacy file (row 17). That the registrar form is the tracker's decision is `[verified: gh issue view 1116, "Subparser placement (decided 2026-10-15 ...)"]`.
3. Shim regions at `6f24337b`:
   - context-distribution :313-505;
   - context-composition banner :1290-1294, then :1296-1723;
   - cache-efficiency :1727-1968, including its leading comment :1727-1730;
   - blocks :2223-2256, :2342-2378, :2380-2409, registered in the order cost, context-distribution, edit-format, read-scope, instrument-authoring, context-composition, cache-efficiency, cost-trend.

   `[verified: read]`
4. Coupling. No range reads a name defined in another range or in edit-format or instrument-authoring code. Outside the ranges, only docstrings and comments name a moved symbol: shim :183, :289, :746, :762-771, :801, :1226, and `cache_rebuild.py:284`. That answers the condition in the engineer's reply (row 1). `[verified: read of each range; grep]`
5. Seam precedent:
   - 32 package modules define a `cmd_*`, and 13 of them carry the 15 `_add_*_subparser` registrars, each named for one command.
   - Every module moved since session-signal carries one.
   - The package shape is a docstring, then `from __future__ import annotations`, stdlib imports, and one unaliased `from transcript_analysis import …` line.

   `[verified: grep "^def cmd_" and "^def _add_\w+_subparser"; read sessions.py:1-69]`
6. Size arithmetic: the moved bodies are 193, 428, and 242 lines, and the blocks 101. The three modules land at about 250, 490, and 295 lines. One module for all three would come to about 1,000. The shim falls to about 1,965 lines. `[verified: line arithmetic over row 3]`
7. Every production rename-map target is already bound by its module: `scope.py` :276, :524, :679, :690, :710, :798, :857, :894, :1088; `pricing.py` :27-29, :47-48, :119, :135, :140, :200, :215, :232, :468, :522, :564, :642; `corpus.py` :118, :228; `render.py` :27, :66, :82; `read_scope.py:21`; `subagents.py:231`. `[verified: grep]`
8. Staying readers of moved names, after the move:
   - `_mod.cmd_context_distribution` and `_mod.cmd_context_composition`: legacy :3922, :3923, both read at import time by `_UNCONDITIONAL_HEADER_CASES`, so a missing binding fails collection of the whole legacy file;
   - `_mod.cmd_cache_efficiency`: :3198, :3203, :3717, :3726, :3747, :3767;
   - `_mod._cache_efficiency_report`: :3238.

   No staying test reads any other moved name. `[verified: grep -o of every _mod.<name> read in the tests directory]`
9. `TestRootsThreadingSpy` patches `_mod._resolve_project_scope` and `_mod._print_resolved_scope` by string (:4140-4141), and also `_mod.scope._resolve_project_scope` and `_mod.scope.print_resolved_scope` (:4145-4146). It is parametrized over `_UNCONDITIONAL_HEADER_CASES`, which holds context-distribution and context-composition rows and no cache-efficiency row (:3900-3949). `[verified: read]`
10. After the deletion, shim code stops reading 25 imported names: `math`, `defaultdict`, 14 of the 15 pricing names (all but `_dedup_turns_by_request_id`), `_parse_ts`, `_read_session_file_partitioned`, `_context_distribution_rows`, `_content_text`, `_projects_glob`, `_parse_since_nd_arg`, `_scan_root_transcripts`, `_READ_SCOPE_CHARS_PER_TOKEN`, and `_MCP_TOOL_BUCKET_LABEL`. Shim-loading tests outside the moved slices still read nine of them:
    - `_mod._parse_ts`: legacy :380-387, :507-526;
    - `_mod._read_session_file_partitioned`: :2346;
    - `_mod._cache_write_split`: test_transcript_cost.py:242-272;
    - `_mod._context_at_turn`: test_transcript_plan_boundary.py:398;
    - `_mod._context_window_for_model`: test_transcript_cost.py:307;
    - `_mod._price_turn`: legacy :959-1087; test_transcript_cost.py; test_transcript_plan_boundary.py:401;
    - `_mod._content_text`: :370-377;
    - `_mod._parse_since_nd_arg`: :478-493;
    - `_mod._scan_root_transcripts`: :823-944.

    - `_mod._session_peak_context` and `_mod._context_distribution_rows` lose their only readers when `TestContextDistribution` moves.
    - The composition file's reads of the two cross-command names move with it.
    - These names keep shim readers in edit-format or instrument-authoring code: `json`, `Counter`, `Sequence`, `_dedup_turns_by_request_id`, `_DO_NOT_PUBLISH_BANNER`, `_resolve_cost_roots`, `_resolve_project_scope`, `_print_resolved_scope`, `_redaction_ordinals`, `_root_index_for_path`, `_pct_of`, and `_SPAWN_TOOL_NAMES`.
    - Both test_transcript_cost.py and test_transcript_plan_boundary.py load the shim (:36, :18).

    `[verified: grep]`
11. Only `TestRootsThreadingSpy` patches a shim binding by string among transcript tests (:4140-4141, :4181-4182), and both names it patches stay bound. `[verified: grep "setattr\(_mod,"]`
12. The moved slices read only these from the shim:
    - the moved names;
    - `_mod.scope` (legacy :1649, :3102; composition :651, :683, :707);
    - `_mod._DO_NOT_PUBLISH_BANNER` (:1665, :3119; composition :616);
    - `_mod._price_turn`, `_mod._session_peak_context`, and `_mod._context_distribution_rows` (:1732-1774);
    - `_mod._parse_ts` (composition :357), `_mod._READ_SCOPE_CHARS_PER_TOKEN` (:800), and `_mod._MCP_TOOL_BUCKET_LABEL` (:826).

    No moved slice reads a `_mod.<module>.<name>` other than `_mod.scope`, so the shim's module-list comment stays true. `[verified: grep -o]`
13. `TestConfigDirRouting`'s first test reads `_mod._SUBCOMMANDS_REFUSING_TOP_LEVEL_CONFIG_DIR`, which the shim binds bare for `main()` (:231, :2906). Its second test calls `_mod.main()`, the same refusal `TestConfigDirFlag`'s parametrized test (:212-250) already runs for every tuple member, context-composition included. The legacy file holds no class of that name. `[verified: read; grep]`
14. The engineer chose where the `main()`-calling test in `test_context_composition.py` goes. `[engineer-verified: "Move to legacy file (Recommended)"]` The tag covers only that test's destination.
15. The option's description, its placement beside `TestConfigDirFlag`, and the sibling test's placement are the session's or this plan's. `[unverified]`
16. The engineer chose whether to rename `test_context_composition.py` to `test_transcript_context_composition.py`. `[engineer-verified: "Rename (Recommended)"]` The tag covers only the rename.
17. `TestCacheEfficiencyArgparseWiring` reads `_mod.build_parser()` (:3194, :3201). The doc sends a test that reads a shim-only name to a shim-loading file (doc :731-735). Phase C kept `TestBuildParser`'s pr-link tests in the legacy file. Four family files load their own shim for an argparse-wiring class: test_transcript_plan_boundary.py:538, test_transcript_cache_rebuild.py:533, test_transcript_cache_rebuild_ttl_rules.py:36, and test_transcript_pr_cost_export.py:746. `[verified: read; grep]`
18. `_cache_efficiency_args` is read by `TestCacheEfficiency` (moving), `TestCacheRebuildCrossInstrumentReconciliation` (:3238), and `TestFormatDriftCanary` (:3717-3767). `TestCacheEfficiencyArgparseWiring` names it only in its docstring. The tests directory holds ten `_*_helpers.py` modules, and `_commit_gate_helpers.py` is the 16-line shape. `[verified: grep; Glob; read]`
19. The doc states the cross-group rule as keeping such a test "in the legacy file until every group it references has moved" (doc :782-784). After this PR, every group `TestFormatDriftCanary` and `TestCacheRebuildCrossInstrumentReconciliation` reference has moved, so that sentence's reason no longer holds for the latter. Doc :829-831 states only what `TestFormatDriftCanary` spans, which stays true. `[verified: read]`
20. The prefix test requires the following:
    - every module in `PRODUCTION_MODULES` imports siblings only as `from transcript_analysis import <m>` and reads only real sibling attributes (:245-372);
    - every loader-free `test_*.py` that imports the package is listed in `TEST_FILES` (:474-485);
    - each listed file makes at least one resolvable read (:427-437).

    `_cache_efficiency_helpers.py` is not globbed as `test_*.py`. `[verified: read]`
21. The architecture-doc test requires a `` ### `<name>.py` `` heading for every package module. `[verified: test_transcript_analysis_architecture_doc.py:14-49]`
22. The import-cycle test discovers modules by glob (:25) and adds a `test_module_imports_first_in_fresh_interpreter[<module>]` ID per module (:142-143). Nothing imports the new modules except the shim, so they close no cycle. `[verified: grep]`
23. The limit reads: "Limit: 1,000 lines, for production and test files alike." `[verified: docs/design-decisions/code-file-line-limit.md:19]` The same doc says nothing enforces it until F1 (:50-53), and no test or hook names it. `[verified: grep]`
24. Bootstrap file facts:
    - It is 1,073 lines, of flat tests sharing `_run` (:30-42). `[verified: read]`
    - `_seed_priced_account` (:180-205) writes one 1M-input `claude-sonnet-5` turn, and `_isolated_config_env` (:208-220) pins `CLAUDE_CONFIG_DIR` and an absent declared-roots file. `[verified: read]`
    - No test names any of the three commands. All three are in `_SUBCOMMANDS_REFUSING_TOP_LEVEL_CONFIG_DIR` (scope.py:701-707), so a subprocess case cannot pass the top-level `--config-dir`. `[verified: grep; read]`
    - Phase C flagged the file in the PR body rather than splitting it. `[verified: turn-shape-and-git-commands.md M8]`
25. Dispatch tokens and seed cells:
    - `## Context distribution report` prints only at shim :430, `## Context composition report` only at :1651, and `## Cache efficiency by thread` only at :1873. `Sessions in scope:` also prints in rearm_backtest.py:457, so the cell check uses the whole summary line. `[verified: grep]`
    - A 1M-input `claude-sonnet-5` turn prices at $2.00. `[verified: legacy :1640-1642 asserts it]`
    - Over that seed, with or without an empty second root, context-composition prints `Sequences scanned: 1   Turns scanned: 1` and cache-efficiency's all-roots `main` row reads `Turns` `1`. `[unverified: derived by reading :1436-1551, :1653-1656, and :1777-1848]` The empty root yields no session, and context-distribution's summary line counts sessions, not roots (:437-476). `[verified: read]`
26. A subparser's `--help` usage line carries `transcript-analysis.py <command>`. `[unverified: argparse's subparser prog composition not read; Verification 1's help captures confirm it]`
27. `capture` pins `CLAUDE_CONFIG_DIR` to `--account` and points `TRANSCRIPT_CONFIG_DIRS_FILE` at an absent file (_move_fidelity.py:501-513). `_resolve_cost_roots` unions the config dir, declared roots, and `--config-dir` extras into one root list (scope.py:760-795). An extra therefore reaches the same multi-root code in the moved functions as a declared root. `[verified: read]`
28. Verification 1's seed gives `s1` three turns whose static-prefix residual is 50 each, so context-composition is not refused over account 1. `r1` in account 2 gives residuals of 50 and 300, which refuses. `s1`'s third turn reads 0 against a prior prefix of 150, which is one cold event. `[unverified: derived from :1317-1551, :1734-1848, and pricing.py:200-225; Verification 1's liveness tokens confirm it]`
29. Revert rehearsal. Governing plan item 7 reads: "At each phase boundary, `git revert` the phase's merge commit on a scratch branch and run the full suite. Baseline parity alone does not prove a staged PR is independently revertible — a conftest fixture promotion in PR N can leave PR N−1's revert uncollectable." `[verified: transcript-analysis-decomposition.md:276-279]` Phase C's waiver covered that phase only. `[verified: turn-shape-and-git-commands.md row 27]` The engineer waived the rehearsal for this PR. `[engineer-verified: "Waive it (Recommended)"]` The tag covers only the waiver of the rehearsal for this PR, read against the question whether to waive it for this PR as well as phase C.
30. Tools run through `scripts/venv-run.sh <tool>`, the form CLAUDE.md § Commands specifies. `[verified: CLAUDE.md "Commands"]` `nodes --rename` needs Python 3.12's `tokenize`. `[verified: _move_fidelity.py:16, :286-287]` The wrapper exits 127 when a tool is missing. `[unverified — relayed from turn-shape-and-git-commands.md row 30]`
31. `test-ids` keys each test by the parts after its last `test_` module component, so a moved or renamed file keeps its tests' IDs. A declared `--expect-new` ID must occur more often after than before. `[verified: _move_fidelity.py:745-750, :813-818]`
32. `nodes` reverses a new name only where it occurs as code with no word character or dot before it, and flags `rename-ambiguous` when a new name already occurs as code in the original (_move_fidelity.py:201-206, :337-340). No module name the test rename maps introduce occurs as code in a moved test slice; every hit is in a string or docstring. `[verified: read; grep]`
33. `remainder` drops every top-level import statement, with any comment on its lines, before comparing. `[verified: _move_fidelity.py:32-33, :375-391]`
34. Shim :1512 is 120 columns and grows to 136 once its two pricing constants are renamed. ruff's line length is 130. `[verified: column count of :1512; pyproject.toml:2]` No other renamed line passes 130. `[unverified: spot-checked :463, :1329, :1867, :1868, :1959]`
35. Stale prose inside moved ranges:
    - shim :1854-1855, "matching every other fixed-width table in this file", which is false in `cache_efficiency.py`;
    - shim :392, "now that this subcommand scans multiple roots by default", which narrates a past change;
    - shim :1790-1807, which names `_read_scope_growth_for_group` (read_scope.py:129) and `_cost_report` (cost.py:502) without their modules;
    - composition test :623-624, which cites `TestCostMultiRootReport` in test_transcript_analysis.py at :6419 though the class is at test_transcript_cost.py:860.

    The phrase "this module's own tests" at shim :356-358, :368, :1586, :1902, and :1918 becomes accurate after the move. The design pointer at :1294 names an existing file. `[verified: read; grep]`
36. The move makes these texts false outside the moved ranges:
    - shim import comments :182-184 and :288-290;
    - `conftest.py:40`, which names the renamed file;
    - architecture doc :25-26, :47-48, :355-357, :486-488, and :782-784 (row 19).

    The unqualified mentions at shim :746, :762-771, :801, :1226 and `cache_rebuild.py:284` stay true. Committed plans, `docs/reports/`, `docs/case-studies/`, and `docs/cost-levers-considered.md` are preserved records. `[verified: grep]`
37. `select-tests.py:598` maps any change under `claude/.claude/scripts` to the scripts tests, and :797-802 add the hook tests for package changes, so no new row is needed. `[verified: read]`
38. The branch-attribution structure test pins `"gitBranch"` reads, and the shim holds none, so no key changes. `[verified: grep]`
39. None of the seven new basenames exists anywhere in the repo. `[verified: Glob]`
40. No moved test carries the `timing` marker. The legacy file's one timing test (:476) stays. `[verified: grep]`
41. CHANGELOG.md names none of phase C's modules, so a pure move adds no entry. `[verified: grep]` `require-stow-reminder.sh` fires on any file added under `claude/.claude/` and requires the PR body to name `install.sh` or `stow`. `[verified: require-stow-reminder.sh:4-49]`
42. `_session_peak_context`, both `_CONTEXT_DISTRIBUTION_THRESHOLD_*` tuples, and `_context_distribution_rows` have no production reader besides the context-distribution code. `[verified: grep]`
43. `nudge-error-mode-analysis.sh` runs the shim on Python 3.11, and the shim imports every new module. `[unverified — relayed from turn-shape-and-git-commands.md]`
44. `_context_composition_args` (composition :23-37) builds the same Namespace shape as conftest's `_context_distribution_args` (:1278-1292). `[verified: read]`
45. `--since 1d` excludes every 2026-05-19 record on any later run date, and `--since 3650d` includes it until 2036. `[unverified: _parse_since_nd_arg's arithmetic not read]`
46. No hook, plugin, or eval names the three commands. `docs/transcript-analysis.md` and `claude-skills/skills/transcript-analysis/SKILL.md` name them only as CLI invocations, and no doc names a moved internal symbol. `[verified: grep]`
47. The waiver option's description was the session's wording, relaying this plan's reasoning, not the engineer's reason. `[unverified]` The fact it states holds for this plan: item 7's named hazard is a conftest fixture promotion (row 29), Part 3 limits this PR's `conftest.py` change to the :40 docstring line, and `_cache_efficiency_args` goes to a helper module instead. `[verified: this plan; Verification 7's conftest.py check confirms it]` That a revert of this PR applies cleanly until a later commit touches a file it touches is this plan's inference; no revert was run. `[unverified]`
48. The new subprocess tests in `test_transcript_cli_bootstrap.py` are new coverage, which the `Ask:` line does not call for. The engineer chose to keep them in this PR. `[engineer-verified: "Keep them (Recommended)"]` The tag covers only keeping the bootstrap tests in this PR. The option's rationale, including the phase C precedent, is the session's. `[unverified]`
49. Root-resolution observable for context-distribution and context-composition:
    - Each `cmd_*` passes `_resolve_cost_roots`'s list as `roots` (shim :320-321, :1574-1575). Only a non-None `roots` prints `<command>: account-N: scanned K transcripts` on stdout (:388-425, :1607-1634), so a `func=_<command>_report` binding prints no such line.
    - `_resolve_cost_roots` reads `extra_config_dirs`, rejects an extra without `projects/`, and appends it after the default root (scope.py:760-795). An empty readable `projects/` scans as 0 transcripts (scope.py:832-837).
    - `_seed_priced_account` returns `tmp_path / "account"` (bootstrap :185).

    `[verified: read]` `_redaction_ordinals` numbers roots by sorted resolved path (scope.py:295-296), so `<tmp_path>/account` is account-1 and `<tmp_path>/account2` is account-2. `[unverified: derived from that sort; Verification 1's run-06 confirms it for the same name pair]`
50. Cache-efficiency's observable: `### account-N` prints only when `per_account` is passed, which needs more than one scan root (shim :1872-1880, :1936-1942, :1968). `per_account` holds every root's ordinal, so an empty second root still prints `### account-2`. Each of the three tables prints its own `Thread` header (:1856-1859), and `_table_cols` without `occurrence` requires exactly one header match (conftest.py:1148-1157). `[verified: read]`
51. Registrar flags:
    - context-distribution and context-composition each add `--since` (shim :2243-2246, :2362-2368). `[verified: read]`
    - All three add `--config-dir` (:2233-2242, :2352-2361, :2390-2399) and `--no-redact` (:2247-2255, :2369-2377, :2400-2408), so no flag is unique to one of them. `[verified: read]`
    - Each `--config-dir` help names `--no-redact` (:2239, :2358, :2396), so a bare `--no-redact` token matches without the flag. `[verified: read]`
    - `--since` occurs in no other help text of those two blocks or of `_add_project_scope_args` (scope.py:516-521). `[verified: grep]`
    - `_parse_since_nd_arg`, `_resolve_cost_roots`, and the handlers read `since` and `no_redact` through `getattr` with a default (scope.py:1096, :787; shim :361, :1580, :1912), so dropping `--since` or `--no-redact` raises nothing at dispatch. `[verified: read]`
    - argparse renders the store_true flag as `[--no-redact]` in the subcommand's usage line. `[unverified: argparse usage formatting not read; Verification 1's help captures confirm it]`
52. In `claude/` and `docs/`, `test_context_composition` occurs only at `conftest.py:40` (the file name) and old composition file :731 (the method `TestConfigDirRouting` carries into the legacy file). `[verified: grep]`

**Mechanisms:**
- M1: Three modules, one per command. `anchors: row4, row5, row6`. Alternatives: see Alternatives set aside.
- M2: Three per-command registrars, called at the old blocks' positions. `anchors: row3, row5`.
- M3: Four test-only shim bindings. `anchors: row8`. The lighter primitive, by-name binding, beats retargeting staying tests (Decision above).
- M4: F401 plus a grep limited to shim-loading files decides each orphaned import, with string-form patch targets counted. `anchors: row10, row11, row12`.
- M5: Three loader-free test files with the test rename maps, the composition file renamed. `anchors: G2, row12, row16, row20, row32`.
- M6: `tests/_cache_efficiency_helpers.py`. `anchors: row18, row29`. Two lighter alternatives fail: `conftest.py` reaches every test file and is the governing plan's named revert hazard, and a test-module import couples two test files.
- M7: `TestConfigDirRouting` moves to the legacy file whole. `TestCacheEfficiencyArgparseWiring` and the cross-group classes stay byte-identical. `anchors: row8, row13, row14, row15, row17, row19`.
- M8: Two parametrized bootstrap tests over the existing `_seed_priced_account` plus an empty second config dir, accepting more than 1,000 lines. The help case pins each registrar's flags, and the dispatch case pins root resolution through `_resolve_cost_roots`. `anchors: row23, row24, row25, row26, row49, row50, row51`. Three lighter alternatives fail: an in-process parse check pins `func` but runs no handler in a fresh interpreter; a dispatch run without `--config-dir` misses cache-efficiency's skipped root resolution and any lost `--config-dir` dest; and a new transcript seed buys nothing the existing one lacks.
- M9: The `__init__` docstring, the shim import comments, `conftest.py:40`, and the doc edits. `anchors: row19, row21, row35, row36`.
- M10: The unchanged move-fidelity harness with one capture pair whose runs reach multi-root through `--config-dir` extras. `anchors: G3, row27, row28, row30, row31, row32, row33, row34`. Two lighter alternatives fail: the selected suite alone proves neither byte-identical help and output nor verbatim nodes, and diff review by eye is not mechanical.
- M11: One `code-writer` dispatch, with no stages. `anchors: row8, row12`.
- M12: The selected suite runs in two passes, split by the `timing` marker. `anchors: G4, row40`.

## Critical files

**Dispatch: one `code-writer`, the move.** The shim, the legacy test file, the bootstrap file, the prefix test, and the architecture doc are shared by all three commands, so no split yields disjoint file sets (plan-it Step 5's dispatch-split rule). The brief requires:
- copy moved code verbatim, extracted by line range from the parent's `<scratch>/before-shim.py`, `<scratch>/before-legacy.py`, and `<scratch>/before-composition.py`, applying only Parts 1–4's edits;
- keep new production code runnable on Python 3.11 (row 43);
- run every tool through `scripts/venv-run.sh <tool>` (row 30); if the wrapper reports a tool missing, stop and return without substituting another interpreter;
- if F821 flags a name absent from the rename maps, the stdlib, conftest, or `_cache_efficiency_helpers.py`, stop and report;
- if a moved line other than :1512 needs reflow, stop and report (row 34);
- if any new file exceeds 1,000 lines, stop and report; `test_transcript_cli_bootstrap.py` is exempt from this rule (M8), and only its count is reported;
- delete `tests/test_context_composition.py` from the working tree only, and do no staging and no commits.

Its return lists:
- every F401 disposition from Parts 2 and 3;
- the wrap-only reflow count;
- every new bootstrap test ID;
- `wc -l` for each new and changed file.

Files:
- **Create:**
  - `claude/.claude/scripts/transcript_analysis/context_distribution.py`, `…/context_composition.py`, `…/cache_efficiency.py`;
  - `claude/.claude/scripts/tests/test_transcript_context_distribution.py`, `…/test_transcript_context_composition.py`, `…/test_transcript_cache_efficiency.py`, `…/_cache_efficiency_helpers.py`.
- **Delete:** `claude/.claude/scripts/tests/test_context_composition.py`.
- **Modify:**
  - `claude/.claude/scripts/transcript-analysis.py`;
  - `claude/.claude/scripts/transcript_analysis/__init__.py`;
  - `claude/.claude/scripts/tests/test_transcript_analysis.py`, `…/conftest.py` (one docstring line), `…/test_transcript_cli_bootstrap.py`, `…/test_transcript_package_module_prefixes.py`;
  - `docs/transcript-analysis-architecture.md`.
- **Also in the diff:** `.claude/plans/context-and-cache-commands.md`.

Its verification runs from the worktree root:
- `scripts/venv-run.sh pytest` over:
  - the three new test files;
  - `test_transcript_analysis.py`, `test_transcript_cli_bootstrap.py`, `test_transcript_package_module_prefixes.py`, `test_transcript_analysis_architecture_doc.py`, `test_transcript_analysis_import_cycles.py`;
  - `test_transcript_cost.py` and `test_transcript_plan_boundary.py`, the readers of retained shim bindings (row 10);
  - `claude/.claude/hooks/tests/test_nudge_error_mode_analysis.py`, which runs the real shim.
- `scripts/venv-run.sh ruff check claude/.claude/ claude-skills/ plugins/ evals/`, clean.
- `wc -l` on every new and changed file.

**Parent:** run Verification 0–1 before the dispatch, and Verification 3–9 after it, then `/code-review` and the commit. A review-round edit re-runs Verification 3, 5, 7, and 9 and the dispatch's test list above. It re-runs Verification 4 and 6 only when the round edits the shim or a package module.

**Rebase rule.** A rebase or merge that brings in a change under `claude/.claude/scripts`, `claude/.claude/hooks`, or `docs/transcript-analysis-architecture.md` invalidates every line range, snapshot, capture, and junit baseline. All of them are keyed to the pre-rebase `HEAD`. Verification 0 and 10 detect such a change.
- Before the commit: run the Abort procedure, then rebase, then restart at Verification 0 with a fresh `<scratch>`.
- After the commit: stop and report to the engineer.

**Abort procedure.** If the dispatch is interrupted, the code-writer cleans nothing, and the parent recovers:
1. `git status --short` must list only the seven modified files above, the one deleted file, the seven created files, and the plan. On any other path, stop and report, touching nothing.
2. Restore each modified file and the deleted file with its own literal `git restore --source=HEAD -- <path>` call.
3. Delete each created file by its literal path.
4. Success: `git status --short` lists at most the plan.

Never use `git clean`, `git checkout .`, `git reset --hard`, or `git stash`.

**Explicitly unchanged:**
- `_move_fidelity.py`, `select-tests.py`, `test_select_tests.py`, and `test_transcript_analysis_branch_attribution_structure.py` (rows 37, 38);
- every other package module, `pricing.py`, `render.py`, `read_scope.py`, `subagents.py`, and `cache_rebuild.py` included (rows 36, 42);
- every `conftest.py` line except :40;
- `docs/transcript-analysis.md`, `claude-skills/skills/transcript-analysis/SKILL.md`, README.md, `docs/scripts.md`, and CHANGELOG.md (rows 41, 46);
- every hook, and every preserved record (row 36).

**Reuse:**
- `scope._add_project_scope_args`;
- the registrar shape at `sessions.py:52-69`;
- conftest's `fake_projects`, `fake_config_dir_factory`, `_asst`, `_user_msg`, `_priced`, `_bash_use`, `_agent_use`, `_tool_result`, `_table_cols`, `_compact_boundary_rec`, `_write_jsonl`, `_write_subagent_jsonl`, `_write_cost_root`, and `_context_distribution_args`;
- bootstrap's `_run`, `_seed_priced_account`, and `_isolated_config_env`;
- `tests/_commit_gate_helpers.py` as the helper-module shape;
- `_move_fidelity.py` unchanged.

**PR body carries:**
- each Verification result, with the harness counts: moved production nodes (33), moved test nodes (9 plus 19), and the junit-derived moved test counts;
- measured `wc -l` for every new or changed file. It flags the shim (about 1,965), the legacy file (about 5,355), and `test_transcript_cli_bootstrap.py` (about 1,130) as over 1,000 lines, and names the bootstrap file as an F1 candidate (M8);
- the reflow count, expected to be 1;
- that `test_context_composition.py` is renamed to `test_transcript_context_composition.py` and no longer loads the shim;
- that the engineer waived the revert rehearsal for this PR (row 29);
- the rollback statement: "A plain `git revert` of the squash commit is expected to apply cleanly and restore the pre-PR tree only while no later commit has touched a file this PR touches or read a name it adds (the three modules, their three registrars, `tests/_cache_efficiency_helpers.py`). This is an inference; no revert was run. The second small-groups PR is expected to edit the shim, the legacy test file, `transcript_analysis/__init__.py`, `docs/transcript-analysis-architecture.md`, `test_transcript_cli_bootstrap.py`, and the append-only `PRODUCTION_MODULES` and `TEST_FILES` tuples in `test_transcript_package_module_prefixes.py`. Those are the predictable conflict sites, so once it merges, fix forward. Cross-phase reverts run last-in-first-out.";
- the re-stow instruction: after pulling, re-run `./install.sh`. It is needed only when `scripts/transcript_analysis/` is not inside a folded directory symlink. Without it the shim raises `ModuleNotFoundError` at import and every subcommand fails. The PR-create gate requires the body to name `install.sh` or `stow` (row 41);
- that no CHANGELOG entry is added, matching phase C (row 41);
- that edit-format and instrument-authoring follow in the second PR (row 1);
- the follow-ups in Out of scope.

## Verification

Run everything from the worktree root. Notation:
- `<mf>` is `scripts/venv-run.sh python3 claude/.claude/scripts/tests/_move_fidelity.py`.
- `<pkg>` is `claude/.claude/scripts/transcript_analysis`, `<tests>` is `claude/.claude/scripts/tests`, and `<shim>` is `claude/.claude/scripts/transcript-analysis.py`.
- `<scratch>` is one `mktemp -d` directory, created before step 0. Use its printed path literally.
- `<attempt>` is a fresh `mktemp -d` directory for each dispatch attempt.

Run each git command and each capture as its own literal call. Seed files with the Write tool.

0. **Preconditions.**
   - `scripts/venv-run.sh python3 -c "__import__('tokenize').FSTRING_MIDDLE"`, `scripts/venv-run.sh pytest --version`, and `scripts/venv-run.sh ruff --version` each exit 0 (row 30). Otherwise stop before the dispatch is paid for.
   - Check main drift, each as its own call: `git fetch origin main`, where a non-zero exit stops the run, then `git diff --name-only HEAD...origin/main -- claude/.claude/scripts claude/.claude/hooks docs/transcript-analysis-architecture.md`. Any output invokes the Rebase rule.
   - `git status --short` shows nothing but the plan.
1. **Baseline, before the dispatch.**
   - Copy each of the eight files Parts 2–4 change or delete into `<scratch>`, each with its own plain `cp <path> <scratch>/before-<name>` call: `before-shim.py`, `before-legacy.py`, `before-composition.py`, `before-bootstrap.py`, `before-prefix.py`, `before-conftest.py`, `before-init.py`, and `before-arch.md`. Create an empty `<scratch>/empty.py` with the Write tool.
   - **Account 1.** Seed `<scratch>/account/projects/-home-user-parityrepo/`. Every assistant record carries model `claude-sonnet-5`, a distinct `requestId`, and the listed usage. Every record carries its file's `sessionId`, `isSidechain: false` unless stated, and a `2026-05-19T10:MM:00.000Z` timestamp.
     - `s1.jsonl` (row 28):
       - 10:00 user, string content of 400 `a` characters;
       - 10:01 assistant, content `[{"type": "text", "text": <80 b characters>}]`, usage `{"input_tokens": 150, "output_tokens": 10}`;
       - 10:02 user, string content of 120 `c` characters;
       - 10:03 assistant, content `[{"type": "tool_use", "id": "t1", "name": "mcp__probe__ping", "input": {"command": "ls"}}]`, usage `{"input_tokens": 50, "cache_read_input_tokens": 150, "output_tokens": 10}`;
       - 10:04 user, content `[{"type": "tool_result", "tool_use_id": "t1", "content": <40 d characters>}]`;
       - 10:05 assistant, content `[{"type": "text", "text": <40 e characters>}]`, usage `{"input_tokens": 14, "cache_creation": {"ephemeral_1h_input_tokens": 0, "ephemeral_5m_input_tokens": 200}, "output_tokens": 10}`.
     - `s2.jsonl`: 10:10 assistant, content `[]`, usage `{"input_tokens": 450000, "output_tokens": 0}`.
     - `s2/subagents/agent-a1.jsonl`: 10:11 assistant with `isSidechain: true` and `sessionId` `s2`, content `[]`, usage `{"input_tokens": 900000, "cache_read_input_tokens": 100, "output_tokens": 0}`.
   - **Account 2.** Seed `<scratch>/account2/projects/-home-user-refusalrepo/r1.jsonl`: a 10:20 user record of 400 `a` characters, then two assistant records at 10:21 and 10:22 with content `[]` and usage `{"input_tokens": 150}` and `{"input_tokens": 400}`.
   - Write `<scratch>/runs.txt` with run-01 to run-23, in this order:
     - run-01 `context-distribution`
     - run-02 `context-distribution --since 3650d`
     - run-03 `context-distribution --since 1d`
     - run-04 `context-distribution --no-redact`
     - run-05 `context-distribution --this-repo --projects x`
     - run-06 `context-distribution --config-dir <scratch>/account2`
     - run-07 `context-distribution --config-dir <scratch>/account2 --no-redact`
     - run-08 `context-distribution --config-dir <scratch>/missing`
     - run-09 `context-composition`
     - run-10 `context-composition --since 3650d`
     - run-11 `context-composition --since 1d`
     - run-12 `context-composition --no-redact`
     - run-13 `context-composition --config-dir <scratch>/account2`
     - run-14 `context-composition --config-dir <scratch>/account2 --projects *parityrepo`
     - run-15 `context-composition --config-dir <scratch>/account2 --no-redact`
     - run-16 `cache-efficiency`
     - run-17 `cache-efficiency --no-redact`
     - run-18 `cache-efficiency --projects *nomatch*`
     - run-19 `cache-efficiency --config-dir <scratch>/account2`
     - run-20 `cache-efficiency --config-dir <scratch>/account2 --no-redact`
     - run-21 `--config-dir <scratch>/account context-distribution`
     - run-22 `--config-dir <scratch>/account context-composition`
     - run-23 `--config-dir <scratch>/account cache-efficiency`
   - Capture twice: `<mf> capture --script <shim> --account <scratch>/account --runs <scratch>/runs.txt --all-help --out <scratch>/before-a`, then the same into `before-b`. Then `<mf> compare-captures <scratch>/before-a <scratch>/before-b` must be clean, with no mask. A difference means a nondeterministic run (row 45): stop and report.
   - Coverage checks on `before-a`:
     - It holds `help-top` plus one help capture and one `.parser` file per subcommand. `git grep -c "add_parser("` over the shim and `<pkg>` governs the count.
     - Each of the three moved commands' `.parser` files contains `default func=<callable <script>:cmd_<name>>`.
     - Each of the three moved commands' help `.stdout` contains `transcript-analysis.py <command>` (row 26) and its Part 3 bootstrap flag tokens (row 51).
   - Corpus liveness. `before-a` must hold these, each producible only by the seed:
     - `Sessions in scope: 2` in run-01, `Sessions in scope: 0` in run-03, and in run-06 `Sessions in scope: 3` and, on stdout, `account-2: scanned 1 transcripts`;
     - in run-09, the category table's heading `Category (rate-weighted token-turns share)`, a `tool_call:mcp__*` row, and a `tool_result:mcp__*` row;
     - `Turns excluded from weighting` and `No priced turns in scope.` in run-11;
     - `REFUSED:` in run-13;
     - in run-14, the category table and, on stdout, `WARNING: context-composition: account-2: no transcripts found`;
     - in run-16, a `main` row whose `ColdTok` is `200` and `ColdEvts` is `1`, and a `sidechain` row whose `Turns` is `1`;
     - `### account-2` in run-19;
     - exit 2 with `--no-redact is refused` on stderr in run-07, run-15, and run-20; exit 2 in run-05; exit 2 with `is not a directory` in run-08; and exit 2 with `has no effect here` in run-21 to run-23.

     If one is missing, fix the seed and recapture both sides.
   - **Junit baseline.** Run `scripts/venv-run.sh pytest` over `test_transcript_analysis.py`, `test_context_composition.py`, `test_transcript_cli_bootstrap.py`, `test_transcript_package_module_prefixes.py`, `test_transcript_analysis_architecture_doc.py`, and `test_transcript_analysis_import_cycles.py`. Run it once with `-m "not timing" --junitxml=<scratch>/before-parallel.xml` and once with `-m timing -n0 --junitxml=<scratch>/before-timing.xml`. All tests pass.
2. **Dispatch** per Critical files, with a fresh `<attempt>`.
3. **Lint and sizes.**
   - `scripts/venv-run.sh ruff check claude/.claude/ claude-skills/ plugins/ evals/` is clean.
   - Record `wc -l` for every new and changed file. Every new file is at most 1,000 lines.
   - 3.11 evidence is an argument, not a parse check. The moved lines already run on 3.11, the rename map only prepends `module.` to a name, and the authored lines are docstrings, imports, and registrar wrappers. Read the `git diff` of the three modules and confirm no line outside the verbatim moved text contains an f-string. If a 3.11 interpreter exists on the machine, `py_compile` the three modules with it. Otherwise record that the 3.11 compile did not run.
4. **Selected suite** (G4, M12). Run two passes, each as a Bash call with `run_in_background`, and start the second only after the first finishes:
   - `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py -m "not timing"`;
   - then `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py -m timing -n0`.

   Both must pass. Edit nothing until both finish. Before claiming or fixing a failure in a file outside Critical files, reproduce it at the merge-base, per CLAUDE.md:
   - Run `git merge-base HEAD origin/main` as its own call.
   - Run `git worktree add <scratch>/merge-base <sha>`.
   - Run the failing test there with `scripts/venv-run.sh pytest`, invoked from that worktree.
   - Run `git worktree remove <scratch>/merge-base`.
5. **Test-ID parity.**
   - Rerun step 1's two junit commands over the same files, with `test_context_composition.py` replaced by the three new test files, into `<attempt>/after-parallel.xml` and `<attempt>/after-timing.xml`.
   - `<mf> test-ids --before <scratch>/before-parallel.xml --before <scratch>/before-timing.xml --after <attempt>/after-parallel.xml --after <attempt>/after-timing.xml --file-prefix claude/.claude/scripts/tests/` must be clean, with one single-quoted `--expect-new` for each of nine IDs (rows 22, 31):
     - `test_transcript_analysis_context_and_cache_help_exits_zero[<command>]` for context-distribution, context-composition, and cache-efficiency;
     - `test_transcript_analysis_context_and_cache_subprocess_finds_seeded_session[<command>]` for the same three;
     - `test_module_imports_first_in_fresh_interpreter[<module>]` for `context_distribution`, `context_composition`, and `cache_efficiency`.

     No `--rename` is needed. If the dispatch returns any other set of bootstrap IDs, stop and report.
6. **CLI and parser parity.**
   - Capture into `<attempt>/after` with step 1's flags.
   - `<mf> compare-captures <scratch>/before-a <attempt>/after --moved-callable cmd_context_distribution=transcript_analysis.context_distribution cmd_context_composition=transcript_analysis.context_composition cmd_cache_efficiency=transcript_analysis.cache_efficiency` must be clean.
   - No `.stdout`, `.stderr`, `.exit`, or `.argv` file may differ.
7. **Move fidelity.**
   - **Production nodes.**
     - Run `<mf> nodes --before <scratch>/before-shim.py` with one `--after` per new module and `--moved` set to Part 1's 33 names.
     - Pass `--verbatim` for the 18 names that read no renamed name: the seven `_CATEGORY_*` constants, `_split_context_sequences`, `_CONTEXT_COMPOSITION_RESIDUAL_INSTABILITY_REFUSAL_THRESHOLD`, `_CONTEXT_COMPOSITION_SPLIT_DISCREPANCY_TOLERANCE`, `_context_composition_residual_instability`, `_new_context_composition_stats`, `_merge_context_composition_stats`, `_COLD_READ_COLLAPSE_MARGIN`, `_is_cold_read_collapse`, `_new_cache_efficiency_stats`, `_merge_cache_efficiency_stats`, and `_print_cache_efficiency_report`.
     - Pass one `--rename` per entry of Part 1's 31-entry map.
     - It must be clean, with `compared-nodes: 33`.
   - **Shim remainder.**
     - Run `<mf> remainder --before <scratch>/before-shim.py --after <shim> --removed <the 33> --changed build_parser`.
     - Declare `--import-added` for the seven triples of the three new import blocks.
     - Declare `--import-removed` for Part 2's 16 removed import triples.
     - It reports only five `remainder-comments`, each matching a `git diff` hunk that deletes the banner :1290-1294 or a comment block that moved with its name (:1296-1299, :1387-1388, :1391-1393, :1727-1730). Any other finding stops the run.
     - `git diff` of `build_parser` shows exactly the three blocks replaced by three calls. Check the import-line comments in `git diff` by eye (row 33).
   - **New modules.** For each new module, `<mf> remainder --before <scratch>/empty.py --after <module> --added __doc__ <its top-level names> --import-added <its triples>` is clean. The exceptions are the moved leading-comment `remainder-comments` findings: four in `context_composition.py` (:1294, :1296-1299, :1387-1388, :1391-1393), one in `cache_efficiency.py`, and none in `context_distribution.py`.
   - **Test nodes, legacy source.**
     - Run `<mf> nodes --before <scratch>/before-legacy.py`, with `--after` set to `test_transcript_context_distribution.py`, `test_transcript_cache_efficiency.py`, `_cache_efficiency_helpers.py`, and the final legacy file.
     - `--moved` the nine names: `_CONTEXT_DISTRIBUTION_ABS_TABLE_HEADER _extract_context_distribution_row _extract_context_distribution_abs_row TestContextDistribution _cache_efficiency_args TestCachePrefixTotal TestCacheEfficiencyClassifier TestCacheEfficiencyGroupScan TestCacheEfficiency`.
     - `--verbatim` four names: `_CONTEXT_DISTRIBUTION_ABS_TABLE_HEADER _extract_context_distribution_row _extract_context_distribution_abs_row _cache_efficiency_args`.
     - Pass the union of Part 3's context-distribution and cache-efficiency maps, 13 distinct entries.
     - It must be clean, with `compared-nodes: 9`.
   - **Test nodes, composition source.**
     - Run `<mf> nodes --before <scratch>/before-composition.py`, with `--after` set to `test_transcript_context_composition.py` and the final legacy file.
     - `--moved` its 19 top-level names, `--verbatim _context_composition_args TestConfigDirRouting`, and pass Part 3's 23 composition renames.
     - It must be clean, with `compared-nodes: 19`.
   - **Other remainders.**
     - The legacy file, with `--removed <the nine>`, `--added TestConfigDirRouting`, and its import triples, reports only the `# context-distribution` banner's `remainder-comments`.
     - Each new test file and the helper module, compared against `empty.py` with `--added __doc__` and its names, is clean.
     - `remainder` is clean for:
       - `__init__.py` (`--changed __doc__`);
       - the prefix test (`--changed PRODUCTION_MODULES TEST_FILES`);
       - bootstrap (`--added` its five new names).
     - `conftest.py`, with `--changed _write_jsonl`, is clean, and `git diff` shows only :40 changed.
8. **Negative controls.** For each control:
   - run a plain `sha256sum <file>` first, and save its output line to `<attempt>/ctl-<x>.sha256` with the Write tool;
   - edit by hand, run the check, then undo by hand;
   - `sha256sum -c <attempt>/ctl-<x>.sha256` must then pass;
   - never use `git checkout` or `git stash`;
   - put each control capture in a fresh `<attempt>/ctl-<x>` directory, and compare it against `<attempt>/after` with no `--moved-callable`.

   The controls:
   - (a) In `cache_efficiency.py`'s registrar, bind `func=_cache_efficiency_report`. Exactly `help-cache-efficiency.parser` and the run-19 and run-20 files differ, because the bypassed `_resolve_cost_roots` drops the `--config-dir` extra. Then run `test_transcript_analysis.py::TestCacheEfficiencyArgparseWiring` and `test_transcript_cli_bootstrap.py`. Both wiring tests fail. Of the six new bootstrap IDs, exactly the cache-efficiency dispatch ID fails, on its missing `### account-2` (row 50). This shows the dispatch case catches a binding that skips root resolution.
   - (b) In `context_distribution.py`'s registrar, give `--since` `default="1d"`. Exactly `help-context-distribution.parser` and the run-01, run-04, and run-06 files differ. `help-context-distribution.stdout` stays equal. Then run `test_transcript_cli_bootstrap.py`. Of the six new IDs, exactly the context-distribution dispatch ID fails, on its seeded-cells assertion, because `1d` excludes the seed's 2026-05-19 turn (row 45). Its help ID passes, because the help text shows no default.
   - (c) In `context_distribution.py`, bind `_resolve_project_scope` by name with `from transcript_analysis.scope import _resolve_project_scope` and call it bare at its one call site. Run `test_transcript_analysis.py::TestRootsThreadingSpy` and `test_transcript_package_module_prefixes.py`. The context-distribution row of `test_resolve_project_scope_and_header_receive_identical_multi_root_list` fails and no other spy row does, and at least `test_production_modules_import_package_siblings_by_module_only` fails (rows 9, 20).
   - The harness's own committed self-tests already pin `node-ast`, `moved-callable-unused`, and `id-missing`, so this step adds no controls for them `[unverified — relayed from turn-shape-and-git-commands.md Verification 8]`.
   - (d) Three separate edits, each followed by a run of `test_transcript_cli_bootstrap.py` alone:
     - in `context_composition.py`'s registrar, bind `func=lambda _args: None`; exactly the context-composition dispatch ID fails;
     - delete `_add_cache_efficiency_subparser(sub)` from the shim's `build_parser`; both cache-efficiency IDs fail, and no other new ID does;
     - in `context_distribution.py`'s registrar, delete the `--no-redact` `add_argument` call; exactly the context-distribution help ID fails. This shows the `--config-dir` help's mention of `--no-redact` does not satisfy the `[--no-redact]` token (row 51).
   - (e) Remove `"test_transcript_cache_efficiency.py"` from `TEST_FILES`. `test_loader_free_package_importing_test_files_are_in_test_files` fails.
9. **Leftovers and single homes.** Every `git grep` here passes `--untracked`, because the seven new files stay untracked until the commit. Each check that expects nothing is paired with a probe that must hit in the same paths.
   - `git grep --untracked -nE "^def (cmd_context_distribution|_context_distribution_report|cmd_context_composition|_context_composition_report|_classify_content_item|cmd_cache_efficiency|_cache_efficiency_report|_scan_cache_efficiency_group)\b|^(_COLD_READ_COLLAPSE_MARGIN|_CATEGORY_USER_TEXT|_CONTEXT_COMPOSITION_RESIDUAL_INSTABILITY_REFUSAL_THRESHOLD)\b" -- claude/.claude/scripts` returns one hit per name, in its owning module.
   - `git grep --untracked -n "^def _cache_efficiency_args" -- <tests>` returns one hit, in `_cache_efficiency_helpers.py`.
   - `git grep --untracked -nwE "_mod|spec_from_file_location" --` over the three new test files and the helper module returns nothing. Probe: `git grep --untracked -l "^from transcript_analysis import" --` over the three new test files lists all three.
   - `git grep --untracked -n "from _config_dir import" --` over the three new modules returns nothing. Probe: `^from transcript_analysis import` over the three new modules lists all three.
   - `git grep --untracked -nE "_mod\.(_context_distribution_report|_context_composition_report|_scan_context_composition|_classify_content_item|_CATEGORY_|_cache_prefix_total|_is_cold_read_collapse|_scan_cache_efficiency_group|_new_cache_efficiency_stats|_session_peak_context|_context_distribution_rows|_READ_SCOPE_CHARS_PER_TOKEN|_MCP_TOOL_BUCKET_LABEL)" -- <tests>` returns nothing. Probe: `git grep --untracked -n "context_composition._classify_content_item(" -- <tests>` hits.
   - `git grep --untracked -nE "_mod\.(cmd_context_distribution|cmd_context_composition|cmd_cache_efficiency|_cache_efficiency_report)\b" -- <tests>` returns only row 8's nine sites.
   - `git grep -n "still-unmigrated context-composition" -- docs/transcript-analysis-architecture.md` returns nothing. Probe: `git grep -n "context_composition.py" -- docs/transcript-analysis-architecture.md` hits.
   - `git grep -nE "_READ_SCOPE_CHARS_PER_TOKEN|_MCP_TOOL_BUCKET_LABEL" -- <shim>` returns nothing. Probe: the same grep over `<pkg>/context_composition.py` hits both names.
   - `git grep --untracked -n "test_context_composition\.py" -- claude/ docs/` returns nothing. The pattern names the file, because the moved method `test_context_composition_registered_in_subcommands_with_own_config_dir` keeps the bare prefix (row 52). Probes:
     - `git grep --untracked -n "test_transcript_context_composition" -- <tests>/conftest.py` hits;
     - `git grep --untracked -n "def test_context_composition_registered_in_subcommands_with_own_config_dir" -- <tests>/test_transcript_analysis.py` hits once.
   - Last, the parent stages the change with one literal `git add <path>` call per Critical file. The call on `test_context_composition.py` stages its deletion. Then `scripts/venv-run.sh pytest claude/.claude/tests/test_pytest_collection_config.py::TestNoBareSameDirectorySiblingImports claude/.claude/hooks/tests/test_ticket_reference_discipline.py` must pass. Both list files with `git ls-files`.
10. **Main re-check, before `/ready-for-review`.** Repeat step 0's `git fetch` and `git diff` calls. Any output invokes the Rebase rule's after-commit branch: stop and report that output to the engineer.

## Out of scope

- **Stale prose inside the moved ranges** (row 35): shim :1854-1855, :392, and :1790-1807, and composition test :623-624. DEFER: each moves verbatim so that `nodes` stays a clean proof. Raise them as one follow-up.
- **Relocating the cross-group classes whose groups have all moved:** `TestFormatDriftCanary` and `TestCacheRebuildCrossInstrumentReconciliation`, alongside `TestPerRecordConsumersCreditPrefixToTheBranchEntered` and `TestMultiRootFormatOutliers` (row 19). The move belongs to the phase that dissolves the legacy file.
- **Moving `_session_peak_context`, the two `_CONTEXT_DISTRIBUTION_THRESHOLD_*` tuples, and `_context_distribution_rows` into `context_distribution.py`** (row 42). That edits two leaf modules and is a refactor, not a move.
- **Deduplicating `_context_composition_args` against conftest's `_context_distribution_args`** (row 44). It moves verbatim here.
- **Deleting the moved `main()` refusal test,** which repeats `TestConfigDirFlag`'s context-composition case (row 13). The engineer chose to move it (row 14); removing a test is beyond a move.
- **Pinning context-distribution's and cache-efficiency's membership in `_SUBCOMMANDS_REFUSING_TOP_LEVEL_CONFIG_DIR`.** Only `TestConfigDirRouting`'s first test pins a member, context-composition. `TestConfigDirFlag` parametrizes over the tuple itself, so dropping either sibling drops its refusal case with no test failing (row 13). DEFER: a one-line membership assertion for each, beside the moved class, is new authoring, not a move.
- **Splitting the shim, the legacy test file, or `test_transcript_cli_bootstrap.py` below 1,000 lines.** All three were already over the limit, and F1's offender assessment owns the call (row 23).
- **`_UNCONDITIONAL_HEADER_CASES` and its two classes, `TestBuildParser`, and `TestCacheEfficiencyArgparseWiring`.** The `cli.py` phase owns them.
- **edit-format and instrument-authoring.** They move in the second PR (row 1).
- **The governing plan's revert rehearsal.** The engineer waived it for this PR (row 29).
- **Any CLI, help-text, or output change, and public names for the registrars.**
- **Preserved records naming moved symbols** (row 36).
