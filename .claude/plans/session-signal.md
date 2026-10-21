# Session-signal phase of the transcript-analysis decomposition

## Context

Goal: move the session-signal command group (buckets, fail-seq, struggle, duration, user-input, friction-count) out of `claude/.claude/scripts/transcript-analysis.py` into the `transcript_analysis` package as a pure move, with their tests, in one PR.

Ask: tracker GH-1116's task-list row "session-signal — buckets, fail-seq, duration, struggle, user-input, friction-count (~1,540 test lines)". The PR shape was settled via label "One PR (Recommended)". Widened at plan review to close the select-tests gap that never selects the friction-count hook test for shim or package changes: "Fix it in this PR" (selected label).

The governing plan is `.claude/plans/transcript-analysis-decomposition.md`. The skill phase (#1275) and the registrar-pin and import-cycle tests (#1281) have merged, and the tracker puts session-signal next. The phase must land before the small-groups, leaf-module-tests and final `cli.py` phases. Its prerequisites have closed: #1222 on 2026-10-16 and #1175 on 2026-10-15.

The phase removes about 980 lines from the 4,683-line shim and about 1,660 dedicated test lines from the 8,823-line legacy test file. Behavior, CLI output and `--help` text stay byte-identical. That includes the `friction-count --transcript PATH --checkpoint PATH` contract that `nudge-error-mode-analysis.sh` depends on.

## Approach

The six session-signal commands move out of the shim into six new package modules, one per command: `buckets.py`, `fail_seq.py`, `struggle.py`, `user_input.py`, `duration.py`, and `friction_count.py`. Each module holds its handler, the helpers only it uses, and an `_add_<command>_subparser(subparsers)` registrar that `build_parser()` calls at the old block's position. Each of the three shared constants keeps one home, in the command module that owns it: `fail_seq.py` holds `TEST_RUNNER_RE` and `FAILED_RE`, and `struggle.py` holds `STRUGGLE_PHRASES`. `friction_count.py` and `user_input.py` read them by module. The tests move into six test files that load no shim, one per module. The four helpers that the legacy file also reads move into `tests/_session_signal_helpers.py`. Separately, `select-tests.py` gains one row that selects the friction-count hook test whenever the shim or one of its first-party imports changes (Part 5).

No stow consumer sees a behavior change:
- Help, stdout, stderr, and exit codes stay byte-identical under a single-root and a multi-root corpus (Verification 6).
- The parser dumps differ only by six declared handler relocations.
- `friction-count --transcript PATH --checkpoint PATH` keeps the bare-integer stdout that `nudge-error-mode-analysis.sh` parses.
- The only visible difference: an uncaught exception's traceback names the new module files instead of the shim.

**Part 1: production layout.** Copy moved code verbatim, extracting it by line range from Verification 1's scratch snapshot. Never retype it. Only these edits are allowed:
- the production rename map below;
- each module's docstring and imports;
- the registrar wrapper and its parameter name;
- wrap-only reflow past 130 characters, with the count reported;
- one comment in `STRUGGLE_PHRASES` (:325): `(see test_transcript_analysis.py)` becomes `(see test_transcript_struggle.py)`, because the tests it points at move there (row 42).

Every module starts with its docstring and then `from __future__ import annotations`. No `.py` text names a ticket or a line number. Shim line ranges are at `d7976404` (row 5). If HEAD has moved, re-resolve them by symbol.

- **`transcript_analysis/buckets.py`** holds `cmd_buckets` (:347-405), then `_add_buckets_subparser`.
  - Package import: `from transcript_analysis import corpus, redaction, render, scope`.
  - Docstring: `"""The buckets command: cmd_buckets, the per-branch count of main-thread assistant turns by model family with project and session counts and date range, and its subparser registration.\n\nImports corpus, redaction, render, and scope by module (attribute access, not by name) -- see scope.py's own top-of-file comment for why."""`
- **`transcript_analysis/fail_seq.py`** holds `TEST_RUNNER_RE` and `FAILED_RE` (:298-301), `_longest_fail_streak` (:335-344), `cmd_fail_seq` (:408-494), then `_add_fail_seq_subparser`.
  - Package import: `from transcript_analysis import render, scope`.
  - Docstring: `"""The fail-seq command: cmd_fail_seq, each branch's ordered test-run failed-count sequence with its failing rate and longest failing streak, the TEST_RUNNER_RE and FAILED_RE patterns friction_count.py also reads, and its subparser registration.\n\nImports render and scope by module (attribute access, not by name) -- see scope.py's own top-of-file comment for why."""`
- **`transcript_analysis/struggle.py`** holds `STRUGGLE_PHRASES` (:303-332, inline comments included), `cmd_struggle` (:497-535), then `_add_struggle_subparser`.
  - Package import: `from transcript_analysis import render, scope`.
  - Docstring: `"""The struggle command: cmd_struggle, each branch's count of user turns matching STRUGGLE_PHRASES by the preceding model family, the STRUGGLE_PHRASES lexicon user_input.py and friction_count.py also read, and its subparser registration.\n\nImports render and scope by module (attribute access, not by name) -- see scope.py's own top-of-file comment for why."""`
- **`transcript_analysis/user_input.py`** holds, in shim order:
  - the five helpers `_is_fresh_user_prompt_for_narrative`, `_is_unrecognized_user_list_record`, `_classify_prompt`, `_attribute_model_to_prompt`, and `_truncate_prompt_text` (:538-631);
  - `cmd_user_input` (:634-851);
  - then `_add_user_input_subparser`.

  Its package import is `from transcript_analysis import corpus, redaction, render, scope, struggle`. Docstring: `"""The user-input command: cmd_user_input, the per-session narrative of fresh user prompts classified as initial, follow-up, or explicit correction, its prompt-classification helpers, and its subparser registration.\n\nImports corpus, redaction, render, scope, and struggle by module (attribute access, not by name) -- see scope.py's own top-of-file comment for why."""`
- **`transcript_analysis/duration.py`** holds `cmd_duration` (:854-896), then `_add_duration_subparser`.
  - Package import: `from transcript_analysis import corpus, scope`.
  - Docstring: `"""The duration command: cmd_duration, each branch's span, active, and idle minutes and activity-burst count, and its subparser registration.\n\nImports corpus and scope by module (attribute access, not by name) -- see scope.py's own top-of-file comment for why."""`
- **`transcript_analysis/friction_count.py`** holds :3217-3464 verbatim, then `cmd_friction_count` (:3503-3566), then `_add_friction_count_subparser`.
  - The :3217-3464 range covers `_FRICTION_SIGNAL_WEIGHT` with its leading comment, the three `_friction_*_events` counters, `_friction_signals`, `_FRICTION_CHECKPOINT_SIGNAL_KEYS` with its leading comment block, `_is_valid_checkpoint_int`, `_read_friction_checkpoint`, `_write_friction_checkpoint`, `_consume_new_transcript_lines`, and `_emit_friction_result`.
  - Package import: `from transcript_analysis import denials, fail_seq, render, struggle`.
  - `_friction_signals`' local `denials` and its comment and `# noqa: F811` move verbatim. The comment stays true, because this module binds `denials` as a module too (row 19).
  - Docstring: `"""The friction-count command: cmd_friction_count, one transcript's composite of hook denials, failed test runs, and struggle-phrase user turns, its --checkpoint incremental scan, and its subparser registration.\n\nImports denials, fail_seq, render, and struggle by module (attribute access, not by name) -- see scope.py's own top-of-file comment for why."""`
- **Stdlib imports.** ruff F821/F401 decide each module's stdlib imports (`argparse`, `re`, `sys`, `time`, `json`, `os`, `tempfile`, `contextlib`, `defaultdict`, `datetime`/`UTC`, `Path`, as each body needs them).
- **Registrars.** Each takes `build_parser()`'s subparsers action as `subparsers` and returns `None`.
  - Docstring: `"""Register the <command> subparser on build_parser()'s subparsers action, binding cmd_<name> through set_defaults(func=...)."""`
  - Its body is the shim block verbatim, with three substitutions: `sub` → `subparsers`, `_add_project_scope_args` → `scope._add_project_scope_args`, and `_iso_date` → `scope._iso_date`.
  - Blocks: buckets :3587-3590, fail-seq :3592-3595, struggle :3597-3600, user-input :3602-3626, duration :3628-3632, friction-count :4611-4637.
  - Keep fail-seq's `--branches`-before-scope-args order (row 26).
  - `friction_count.py` imports no `scope`, because its block uses neither substitution.
- **Production rename map.** It applies to code only, never to docstrings or comments. That is 24 entries, and ruff F821 drives the prefixes. Never run a regex over bare names.
  - To `scope.`: `_branch_filter`, `_resolve_project_scope`, `_session_branch_context`, `_attributed_branch`, `_projects_glob`, `_parse_absolute_window_args`. Also `_resolve_scan_roots` → `scope.resolve_scan_roots` and `_print_resolved_scope` → `scope.print_resolved_scope`.
  - To `corpus.`: `iter_sessions`, `_parse_ts`.
  - To `render.`: `_fam`, `_content_text`, `_fmt_date`, `_strip_task_notifications`.
  - To `redaction.`: `_derive_proj_label`, `_project_family`, `_redact_proj_label`, `_build_redact_map`, `_assign_session_redact_label`, `_redact_session_id`.
  - To `denials.`: `hook_denial_key`.
  - Cross-module constants:
    - In `friction_count.py` only: `TEST_RUNNER_RE` → `fail_seq.TEST_RUNNER_RE` and `FAILED_RE` → `fail_seq.FAILED_RE`.
    - In `user_input.py` and `friction_count.py`: `STRUGGLE_PHRASES` → `struggle.STRUGGLE_PHRASES`.
    - Each owning module reads its own constant bare.

**Part 2: shim.**
- Delete the 26 moved top-level names (Verification 7 lists them) and the six blocks.
- Replace the run of five blocks at :3587-3632 with five consecutive calls, keeping one blank line on each side: `_add_buckets_subparser(sub)`, `_add_fail_seq_subparser(sub)`, `_add_struggle_subparser(sub)`, `_add_user_input_subparser(sub)`, `_add_duration_subparser(sub)`.
- Replace the friction-count block with `_add_friction_count_subparser(sub)` directly before `return parser`.
- The `# friction-count` banner (:3213-3215) now heads `cmd_sessions`. It becomes `# sessions`.
- Add one by-name import block per module in the skill phase's style:
  - **buckets, duration, fail_seq, struggle, user_input.** Each block imports the registrar and `cmd_<name>  # noqa: F401`, with this comment: `# _add_<name>_subparser is read bare by this file's own build_parser (the <command> subparser's registration). cmd_<name> is read only via _mod.cmd_<name> from test files.`
  - **friction_count.** The block imports only `_add_friction_count_subparser`, with the comment `# Read bare by this file's own build_parser (the friction-count subcommand's registration).` No test reads `_mod.cmd_friction_count` after the move (row 10).
- The `denials` block keeps only `_drop_denial_command_flag_values`. Its comment becomes `# Read bare by this file's own _command_segment_is_mutating_git.`
- **Orphaned imports.** For each name ruff F401 flags after the deletion, `git grep -n "_mod\.<name>\b" -- claude/.claude/scripts/tests` decides what happens to it:
  - **A test reads it.** Keep the import with `# noqa: F401 -- read only via _mod.<name> from test files`. For `tempfile`, use the `hashlib` precedent's form: `# noqa: F401 -- test_transcript_ledger_common.py reads _mod.tempfile as a stdlib passthrough`.
  - **No test reads it.** Remove the import.
  - **At plan time (row 12):**
    - Keep: `tempfile`, `iter_sessions`, `_strip_task_notifications`, `_parse_absolute_window_args`, `_project_family`, `_build_redact_map`, `_redact_proj_label`, `_redact_session_id`, `_fmt_date`. Part 3's `_mod.iter_sessions` rename applies only to the moved `_collect_runs`. Seven staying legacy tests still read the shim's binding (row 12).
    - Remove: `contextlib`, `time`, `hook_denial_key`, `_derive_proj_label`, `_assign_session_redact_label`.
  - **Unexpected names.** A flagged name outside these 14 gets the same rule, and the return lists it.

**Part 3: tests.** The new test files load no shim. They import package modules directly, which the `fake_projects` fixture permits (architecture doc :525-527).
- **Test rename map** (code only, 19 entries):
  - `_mod.cmd_<x>` → `<x>.cmd_<x>` for buckets, fail_seq, struggle, user_input, duration, and friction_count.
  - `_mod.TEST_RUNNER_RE`, `_mod.FAILED_RE`, and `_mod._longest_fail_streak` → `fail_seq.<name>`.
  - `_mod.STRUGGLE_PHRASES` → `struggle.STRUGGLE_PHRASES`.
  - `_mod._is_fresh_user_prompt_for_narrative`, `_mod._attribute_model_to_prompt`, `_mod._classify_prompt`, and `_mod._truncate_prompt_text` → `user_input.<name>`.
  - `_mod.cmd_review_trace` → `review_trace.cmd_review_trace`.
  - `_mod.iter_sessions` → `corpus.iter_sessions`; `_mod._fam` → `render._fam`; `_mod._content_text` → `render._content_text`.
  - `_mod.scope` → `scope`.

  Nothing else on a moved line changes, apart from wrap-only reflow.
- **`tests/test_transcript_buckets.py`**: `TestBuckets` (:605-758) and `TestInlineSidechainRecordsFollowTheDispatchingBranchInBucketsAndDuration` (:8701-8741). The second class covers two commands, following the `test_transcript_subagents.py` precedent (row 15). Docstring: `"""Tests for transcript_analysis/buckets.py's cmd_buckets: model-family turn counts, --branches, the Proj column's worktree collapse, and inline sidechain records in buckets and duration."""`
- **`tests/test_transcript_fail_seq.py`**: `TestFailSeqRegexes`, `TestLongestFailStreak`, `_collect_runs`, `TestFailSeqEndToEnd`, and `TestFailSeqAttribution`. Docstring: `"""Tests for transcript_analysis/fail_seq.py: TEST_RUNNER_RE, FAILED_RE, _longest_fail_streak, and cmd_fail_seq's run pairing and pre-branch attribution."""`
- **`tests/test_transcript_struggle.py`**: `TestCmdStruggle`. Docstring: `"""Tests for transcript_analysis/struggle.py's cmd_struggle: STRUGGLE_PHRASES matching and task-notification exclusion."""`
- **`tests/test_transcript_user_input.py`**: `TestCmdUserInput`. Docstring: `"""Tests for transcript_analysis/user_input.py's cmd_user_input: prompt classification, filters, --redact, truncation, model attribution, session order, and --out."""`
- **`tests/test_transcript_duration.py`**: `TestDurationGapSplit`. The docstring names the three tests that never call `cmd_duration` (row 44): `"""Tests for transcript_analysis/duration.py: two cmd_duration tests of the Bursts column and the span, idle, and active split at the default gap, and three tests that recompute the idle-gap arithmetic inline without calling cmd_duration."""`
- **`tests/test_transcript_friction_count.py`**: `_friction_count_args`, `TestFrictionCount`, `TestFrictionCountCrossPathEquality`, and `TestFrictionCountCheckpoint`. Every group the cross-path class references has moved once this PR lands (row 15). Docstring: `"""Tests for transcript_analysis/friction_count.py's cmd_friction_count: each friction signal, parity with review-trace's denial count and fail-seq's failing subtotal, and the --checkpoint incremental scan."""`
- **`tests/_session_signal_helpers.py`**: `_SONNET`, `_branch_args`, `_ui_user`, and `_user_input_args`, verbatim. It needs no imports. Docstring: `"""Test helpers shared by the session-signal test files (test_transcript_buckets.py, test_transcript_fail_seq.py, test_transcript_user_input.py) and by test_transcript_analysis.py's per-record attribution classes."""`
- **Legacy `test_transcript_analysis.py`**:
  - Delete the 18 moved names (Verification 7).
  - Import the four helpers via `from ._session_signal_helpers import …`. Remove only the imports F401 then flags.
  - Banners:
    - `# buckets` (:601) becomes `# _project_family (redaction.py)`, since it now heads `TestProjectFamily`.
    - Delete the banners at :529, :573, :769, :852, :4876, :5001, :6074, :6312-6313, and :6398.
    - `# per-record --branches consumers…` (:8497) stays.
  - In `TestBuildParser`, the both-flags pin (:5652) changes `"buckets"` to `"subagents"`. The comment above it stays as written and stays true (row 16).
  - These stay byte-identical: `_UNCONDITIONAL_HEADER_CASES`, `TestRootsThreadingSpy`, `TestPerRecordConsumersCreditPrefixToTheBranchEntered`, `TestSubagentRecordsFollowTheDispatchingBranch`, `TestProjectFamily`, `TestIsFreshUserPrompt`, and `TestResolveProjectScope`.
- **`test_transcript_analysis_branch_attribution.py`**:
  - Delete the shim loader (:27-32), whose comment says it exists only for `_mod.cmd_buckets`.
  - Add `buckets` to the `from transcript_analysis import …` line.
  - :579 becomes `buckets.cmd_buckets(...)`.
  - Remove only the imports F401 then flags.
- **`test_transcript_analysis_branch_attribution_structure.py`**: the key `"transcript-analysis.py:cmd_fail_seq"` becomes `"transcript_analysis/fail_seq.py:cmd_fail_seq"`. Its count and reason stay unchanged (row 17).
- **`test_transcript_cli_bootstrap.py`** gains three definitions after the judgment-pair dispatch test (:493-501). Parametrize to keep the file at or under 1,000 lines (row 21).
  - `_seed_session_signal_account(tmp_path)` writes one session and returns `(config_dir, transcript_path)`. Every record carries `sessionId` `bootstrap-s1`, `gitBranch` `bootstrap-signal`, and a 2026-05-19 timestamp, because duration prints `No timestamp data found.` without one (shim :878-880). The records, built with conftest's helpers:
    - 10:00 a fresh prompt, `"start the bootstrap work"`;
    - 10:01 an opus assistant turn with a Bash `pytest` tool_use;
    - 10:02 its tool_result, `"1 failed, 2 passed"`;
    - 10:03 a fresh prompt, `"that's wrong, try again"`;
    - 10:04 and 10:05 two sonnet assistant turns.
  - Both tests pass `ids=` set to the command names, so each ID ends in `[<command>]`.
  - `test_transcript_analysis_session_signal_help_exits_zero` is parametrized over the six commands, with `--branches`, `--branches`, `--branches`, `--corrections-only`, `--gap-minutes`, and `--checkpoint` as the expected tokens. Docstring: `"""Proves, in a fresh interpreter, that build_parser calls each session-signal registrar."""`
  - `test_transcript_analysis_session_signal_subprocess_finds_seeded_session` is parametrized over five commands, each run as `--config-dir <acct> <argv>`:
    - `fail-seq --branches bootstrap-signal` → `Sequence: 1`;
    - `struggle` → `Unknown`;
    - `duration` → `Span(min)`;
    - `user-input` → `EXPLICIT_CORRECTION`;
    - `friction-count --json --transcript {transcript}` → `"failed_test_runs": 1`. This argv is a template, and the test fills `{transcript}` with the seed's returned path.

    Buckets already has its test at :151. Docstring: `"""Proves, in a fresh interpreter, that real argparse dispatches each session-signal subcommand to its own handler through set_defaults(func=...); each expected token is printed only by that handler."""`
- **`test_transcript_package_module_prefixes.py`**: append the six modules to `PRODUCTION_MODULES`. Add nothing to `TEST_FILES`, because the new files make no `_mod.<module>.<name>` read (row 18).

**Part 4: docs.**
- **`transcript_analysis/__init__.py`**: append `buckets, fail_seq, struggle, user_input, duration, friction_count` to the command-group module list.
- **`docs/transcript-analysis-architecture.md`**:
  - **Intro.**
    - Add the six modules to the imports-back-into list (:16-20).
    - :22-24 becomes "The still-unmigrated command-shape helper `_command_segment_is_mutating_git` likewise calls `denials.py`'s `_drop_denial_command_flag_values` by name from the shim."
    - After :34, add: "`friction_count.py` imports `fail_seq.py` (for `TEST_RUNNER_RE` and `FAILED_RE`) and `struggle.py` (for `STRUGGLE_PHRASES`), and `user_input.py` imports `struggle.py` (for `STRUGGLE_PHRASES`)."
  - **Registrar paragraph (:51-57).** Add the six commands to the registrar sentence, and `cmd_buckets`, `cmd_duration`, `cmd_fail_seq`, `cmd_struggle`, and `cmd_user_input` to the test-only bindings sentence.
  - **`denials.py` section.** :233-235 becomes "`_drop_denial_command_flag_values` is the one name reached bare from a still-unmigrated command-shape helper in the shim — see the exception noted above. `friction_count.py` reads `hook_denial_key` as `denials.hook_denial_key`."
  - **Six new `### <module>.py` sections** after `skill_pair.py` (the drift test requires one per module, row 35). Each states:
    - the module's responsibilities;
    - its module imports;
    - that its `_add_<command>_subparser` is the one name `build_parser()` reaches bare.

    The sections also state these facts:
    - `fail_seq.py` and `struggle.py`: each is the one home of its constants, and those constants' readers are named.
    - `user_input.py`: it scans `scope.PROJECTS_DIR` only, ignoring declared roots, and its `--redact` map comes from `redaction._build_redact_map`'s default root.
    - `friction_count.py`: it reads no scope, and `nudge-error-mode-analysis.sh` runs it through the shim with `--checkpoint` and reads a bare integer from stdout.
  - **Tests section.** After the skill paragraph, add a session-signal paragraph:
    - It names the six test files and states that none loads the shim.
    - It states that `TestInlineSidechain…` covers buckets and duration from `tests/test_transcript_buckets.py`.
    - It names `tests/_session_signal_helpers.py` as a plain module that the legacy file also imports, as `from ._session_signal_helpers import ...` (see `.claude/rules/test-tree-packaging.md`).
    - It states that `TestPerRecordConsumersCreditPrefixToTheBranchEntered` stays in the legacy file because it also spans judgment-pair and pr-link.

**Part 5: select-tests rule table.** One new row selects `test_nudge_error_mode_analysis.py` for a change to the shim or to any of its first-party imports: the package, `_config.py`, and `_config_dir.py` (rows 20, 38, 40, 51). Its new lines stay within ruff's 130-column E501 limit (pyproject.toml:2, :6). The code-writer wraps the row, the comments, and the docstrings below without changing their words. Part 1's reflow allowance and its reported count cover moved code only.
- **`select-tests.py`.**
  - After `DOC_COUNTS_TEST_PATH` (:168-171), add two constants:
    - `TRANSCRIPT_ANALYSIS_SCRIPT = "claude/.claude/scripts/transcript-analysis.py"`, with the comment `# nudge-error-mode-analysis.sh runs this script's friction-count subcommand by path.`
    - `NUDGE_ERROR_MODE_ANALYSIS_TEST_PATH = "claude/.claude/hooks/tests/test_nudge_error_mode_analysis.py"`, with two comment lines: `# test_nudge_error_mode_analysis.py runs nudge-error-mode-analysis.sh, which runs TRANSCRIPT_ANALYSIS_SCRIPT and so loads its first-party imports: the transcript_analysis package, _config.py, and _config_dir.py.` and `# The test builds the script's path inside a function body, so TestCrossDomainReadCompleteness cannot see the read.`
  - Directly after the `TRANSCRIPT_ANALYSIS_PACKAGE_DIR` row (:767), add one `CROSS_DOMAIN_EXCEPTIONS` row. Its predicate is `lambda p: p in (TRANSCRIPT_ANALYSIS_SCRIPT, CONFIG_MODULE, CONFIG_DIR_MODULE) or _is_under(p, TRANSCRIPT_ANALYSIS_PACKAGE_DIR)`, and its target is `(NUDGE_ERROR_MODE_ANALYSIS_TEST_PATH,)`.
  - In the comment block above the table, after the `DOC_COUNTS_TEST_PATH` line (:661), add `# NUDGE_ERROR_MODE_ANALYSIS_TEST_PATH: see its own comment above for citation.`
- **`tests/test_select_tests.py`.**
  - Append `_mod.TRANSCRIPT_ANALYSIS_SCRIPT` to `_EXACT_MATCH_LITERAL_PATH_CONSTANTS` (:1927-1955) and `_mod.NUDGE_ERROR_MODE_ANALYSIS_TEST_PATH` to `_FILE_TARGETS` (:1961-1975).
  - Add `_mod.NUDGE_ERROR_MODE_ANALYSIS_TEST_PATH` to the expected set of the four exact-set tests in row 40. The `CONFIG_DIR_MODULE` test's docstring (:1449-1456) gains one sentence after its fifth-contributor sentence: `A sixth contributor, the TRANSCRIPT_ANALYSIS_SCRIPT row, adds NUDGE_ERROR_MODE_ANALYSIS_TEST_PATH.` The other three docstrings stay true and stay unchanged.
  - After :1482, add `test_transcript_analysis_script_import_closure_selects_the_error_mode_nudge_test`:
    - It is parametrized with `ids=["script", "package-module", "config", "config-dir"]` over `TRANSCRIPT_ANALYSIS_SCRIPT`, `"claude/.claude/scripts/transcript_analysis/friction_count.py"`, `CONFIG_MODULE`, and `CONFIG_DIR_MODULE`.
    - It asserts that the path is a file on disk, that the selection is not the full suite, and that the hand-typed literal `"claude/.claude/hooks/tests/test_nudge_error_mode_analysis.py"` is in `target_paths`.
    - Docstring: `"""test_nudge_error_mode_analysis.py runs the real transcript-analysis.py, and through it the script's first-party imports, as a subprocess of nudge-error-mode-analysis.sh. It builds that path inside a function body, which TestCrossDomainReadCompleteness cannot see, so only this row selects it for these paths. The test path is hand-typed so a constant retyped to another existing file fails here."""`
- **Set aside:**
  - Adding the hook test to `HOOKS_TESTS_IMPORTING_TRANSCRIPT_ANALYSIS`. That set holds by-name importers (:224), which the hook test is not, and its row never matches the shim.
  - Targeting all of `HOOKS_TESTS_DIR`, as the `MARKER_CLEAR_STALE_PY` row does for its subprocess-only coverage (:781). Every remaining phase edits the shim, so each phase would run the whole hooks suite for one dependent file.
  - Matching the shim and the package only, as the session's option description framed it (row 39). That leaves out two files in the shim's import closure, against the table's own transitive-importer precedent (row 40). The engineer kept the extension (row 51).
  - A ground-truth import-walk test like the one behind `_REVIEW_BENCH_SCRIPTS_DEPENDENCIES` (test_select_tests.py:757-812). The row matches the package as a directory, so only a new top-level scripts module imported by the closure could escape it. The table's header comment already accepts hand-enumerated import declarations (:173-180).

**Alternatives set aside.** At the whole-plan level:
- **One module for all six.** It would hold about 990 moved lines plus six registrars and a header, which is over the 1,000-line limit (rows 4, 9).
- **Four modules: a constants leaf, one module for buckets/fail-seq/struggle/duration, `user_input.py`, and `friction_count.py`.** This was the session's Step-4 option description, and it is not the engineer's decision (row 3). It groups four commands that share no helper (row 6), against the package's seam rule (row 7). It also adds a three-constant leaf where reads from the owning module already work without a cycle (row 8).
- **Three modules, with a branch-table module owning the constants.** The seam objection applies here too. The one co-location argument is that audit item S7 later adds `--redact` to all four branch-table commands. That fix is per call site (row 28), so co-location does not make it cheaper.
- **A two-stage dispatch like the skill phase's.** The skill phase staged its dispatch to observe four predicted staying-test failures. Row 11 predicts none here. A production-only stage would also leave the moved tests reading names the shim no longer binds (M11).

**Decision: no revert rehearsal in this phase.** The governing plan, which the engineer approved, prescribes `git revert` of each phase's merge commit plus a full-suite run (transcript-analysis-decomposition.md:276-279). The engineer waived it for the skill phase with "Drop it" (decomposition-skill.md:355). The engineer waived it for this phase by selecting "Drop it (Recommended)" (row 33). The engineer kept the waiver under the corrected rollback reasoning below by selecting "Still drop it (Recommended)" (row 49). The reasons behind that recommended default are this plan's, not the engineer's:
- While no later commit has touched a file this PR touches, a revert of its squash commit applies without a conflict and returns each touched file to the pre-PR content CI already passed. Once a later commit touches one of those files, the revert conflicts textually.
- A later commit that reads a name this PR adds, from a file this PR does not touch, still lets the revert apply cleanly. The revert then deletes that name and leaves a red tree. This PR adds no conftest fixture, so until such a commit lands, no test outside the touched files depends on what it adds.
- A rehearsal proves only that the revert applies right after merge (row 50).
- The rollback statement in the PR body covers the window after a later phase.

### Assumption ledger

**Root:** These still live in the 4,683-line shim:
- six session-signal commands (buckets, fail-seq, struggle, user-input, duration, friction-count);
- their 20 helpers and constants;
- six `add_parser` blocks.

About 1,650 lines of their tests live in the legacy test file. This PR moves them into the package and leaves one definition per name. CLI bytes stay identical. The parser configuration stays unchanged apart from six declared handler relocations. `[verified: transcript-analysis.py:298-896, 3213-3566, 3587-3632, 4611-4637; last line 4683]` The PR also adds the `select-tests.py` row that selects the friction-count hook test for a change in the shim's import closure (row 38).

**Givens:**
- G1. The shim keeps `build_parser()`, `main()`, and every unmigrated `cmd_*` until the `cli.py` phase. Reason: the governing plan's phase order owns this. `[verified: transcript-analysis-decomposition.md:155-161]`
- G2. pytest's `prepend` import mode makes test basenames global, so new files need unique names. Reason: pytest owns this behavior. `[verified: transcript-analysis-decomposition.md:59-63; Glob found none of the 13 new basenames anywhere in the repo]`
- G3. A monkeypatch reaches code only through the binding that code reads at call time. Reason: Python name-binding semantics. `[verified: scope.py:5-10]`
- G4. Agents run `select-tests.py`, not the full suite, outside CLAUDE.md's two named cases. Reason: this repo's CLAUDE.md § Commands imposes it. `[verified]`
- G5. `nodes --rename` needs Python 3.12's `tokenize`. Reason: the harness and the interpreter own this. `[verified: _move_fidelity.py:16]` The venv's interpreter is 3.12.3 `[verified: ../../../../.venv/pyvenv.cfg:3]`. Verification 0 also checks the `tokenize` attribute directly.

**Rows:**
1. The Ask is tracker GH-1116's row "session-signal — buckets, fail-seq, duration, struggle, user-input, friction-count (~1,540 test lines)". `[unverified — relayed; GH-1116 is not readable here]`
2. One PR. `[engineer-verified: "One PR (Recommended)"]` The option's description was the session's proposal and is not covered by this tag.
3. That description's four-module split is the session's proposal, not a decision. M1 settles the layout on rows 6–9 instead. `[unverified]`
4. Tracker rules, relayed:
   - each phase moves its `add_parser` blocks into the owning module in registration order;
   - the 1,000-line limit applies to modules and test files, and an over-limit phase splits until F2 lands;
   - the `cli.py` phase owns `_UNCONDITIONAL_HEADER_CASES`, `TestConfigDirFlag`, the shim's "still-monolithic" comments, and a generic dest test;
   - the leaf-module-tests phase owns the leaf tests.

   `[unverified — relayed from the dispatch]` The limit itself `[verified: code-file-size-splits.md:175]`
5. Shim regions at `d7976404`:
   - constants :298-332;
   - `_longest_fail_streak` :335-344;
   - `cmd_buckets` :347-405, `cmd_fail_seq` :408-494, `cmd_struggle` :497-535;
   - user-input helpers :538-631, `cmd_user_input` :634-851, `cmd_duration` :854-896;
   - friction code :3213-3464 and :3503-3566, with `cmd_sessions` at :3466-3500 staying between them;
   - blocks :3587-3632 and :4611-4637.

   `[verified: read]`
6. Module-level sharing among the six:
   - `TEST_RUNNER_RE` and `FAILED_RE` are read by `cmd_fail_seq` (:453, :467) and by `_friction_failed_test_run_events` (:3288, :3301).
   - `STRUGGLE_PHRASES` is read by `cmd_struggle` (:521), `_classify_prompt` (:598), and `_friction_struggle_turn_events` (:3321).
   - `_longest_fail_streak` is read only by `cmd_fail_seq` (:478).
   - buckets and duration read no group-local name.
   - No package module names any of the three constants.

   `[verified: reads; grep of transcript_analysis/]`
7. Seam precedent:
   - 18 of 21 command-group modules define one `cmd_*`.
   - `cost.py` (2), `subagent_mix.py` (2), and `audit_routing.py` (3) group commands that share helpers (subagent_mix.py:1-5).
   - The skill phase rejected a 610-line `skills.py` because "the three commands share no helper".

   `[verified: grep "^def cmd_"; decomposition-skill.md:255, :358]`
8. Precedent for a constant owned by a command module:
   - `review_rounds.REVIEW_SKILLS` is read by `judgment_pair.py`.
   - `review_rounds._SLASH_COMMAND_RE` is read by `skill_invocation.py` and `review_trace.py`.

   `[verified: architecture doc :28-34]`
9. Size of a single module:
   - moved bodies: :298-896 is 599 lines, :3213-3464 is 252, and :3503-3566 is 64;
   - blocks: 73 lines;
   - six registrar wrappers and a header come on top.

   That puts the total above 1,000. Each of the six modules lands between about 75 and 370 lines. `[verified: line arithmetic]`
10. Staying readers of moved names, after the move:
    - `_mod.cmd_buckets`: :5601, :5608, :6711, :7191, :8040, :8527, :8543, plus test_transcript_analysis_branch_attribution.py:579.
    - `_mod.cmd_fail_seq`: :6713.
    - `_mod.cmd_struggle`: :6715, :8565.
    - `_mod.cmd_duration`: :6717, :8553.
    - `_mod.cmd_user_input`: :8575, :8590, :8619, :8622.

    Nothing outside the moved slices reads `cmd_friction_count` or any other moved name. `[verified: grep]`
11. Only `TestRootsThreadingSpy` patches a shim binding that the moved code reads (:6950-6951 and :6991-6992). It also patches `_mod.scope`'s `_resolve_project_scope` and `print_resolved_scope` (:6955-6956), so its four session-signal rows still intercept. No other `setattr(_mod, …)` exists in the transcript tests. `[verified: grep]` In the other direction, moved code now reads `scope.<name>` and `corpus.iter_sessions` by attribute, so a patch on those module attributes now reaches it. The function-attribute patch sites are legacy :6955-6956, :6996-6997, :7064, and :8015; test_transcript_review_rounds.py:3963 and :5271; and test_transcript_pr_cost.py:704 `[verified: grep]`. None of them runs a moved command except `TestRootsThreadingSpy`'s rows `[unverified — relayed from staff-backend-engineer, who also lists test_transcript_cost.py:1202, :1214, and :2479-2480]`.
12. After the move, shim code stops reading `contextlib`, `tempfile`, `time` (read only at :791), `hook_denial_key`, and ten package helpers. Tests still read these as `_mod.<name>`:
    - `tempfile`: test_transcript_ledger_common.py:492;
    - `_strip_task_notifications`: :401-468;
    - `_parse_absolute_window_args`: :503-520 and test_transcript_review_trace.py:39;
    - `_project_family`: :762-765;
    - `_build_redact_map`: :8386-8429 and test_transcript_cost.py:550;
    - `_redact_proj_label` and `_redact_session_id`: test_transcript_cost.py:493-508;
    - `_fmt_date`: test_transcript_cost.py:1045-1046;
    - `iter_sessions`: :5697, :5741, :5757, :5771, :5788, :5811, and :6000.

    No test reads `contextlib`, `time`, `hook_denial_key`, `_derive_proj_label`, or `_assign_session_redact_label`. `[verified: grep]` That ruff F401 flags exactly these 14 names after the deletion `[unverified — relayed from staff-sdet's ruff run and staff-backend-engineer's AST pass]`. Part 2's unexpected-names rule covers any 15th.
13. The moved test slices read only these from the shim:
    - the moved names;
    - `_mod.cmd_review_trace` (:6333, :6357);
    - `_mod.scope` (:645-750, :776);
    - `_mod.iter_sessions` (:776);
    - `_mod._fam` (:789);
    - `_mod._content_text` (:802).

    No slice uses `scope` as a bare code token. `[verified: grep]`
14. Helper usage:
    - `_ui_user` and `_user_input_args`: `TestCmdUserInput`, and the staying :8572-8622.
    - `_SONNET` and `_branch_args`: the moving `TestFailSeqAttribution` and `TestInlineSidechain…`, and the staying attribution classes.
    - `_collect_runs` and `_friction_count_args`: local to their own slice.

    A second copy of a named helper is a code-review finding (code-review/SKILL.md:106). `_skill_helpers.py` is the shared-module precedent (architecture doc :641-644). `[verified]`
15. A cross-group test stays in the legacy file until every group it references has moved (doc :556-560). A one-command test file may hold a class that covers two commands (doc :587-588). `TestInlineSidechain…` spans buckets and duration only. `TestFrictionCountCrossPathEquality` spans friction-count, fail-seq, and the already-moved review-trace. `[verified]`
16. `TestBuildParser`'s both-flags pin names buckets as its exemplar of "the shim's helper call" (:5650-5652). After the move, buckets is module-registered, while subagents still calls `_add_project_scope_args(p_sub)` from the shim (:3642). The `{"user-input"}` pin (:5642) holds unchanged under the verbatim block. `[verified]`
17. `_RAW_READ_BESIDE_ATTRIBUTION` keys entries by `<path>:<qualname>` and must match the collected reads exactly in both directions. `cmd_fail_seq`'s two literal reads (:422, :433) move. No other moved function holds a literal `"gitBranch"`. `[verified: structure test :142-204; shim reads]` The parametrized `test_raw_read_beside_attribution_still_attributes_its_branch` takes the key string as its ID (structure test :228-229), so the key change renames one test ID `[verified: read]`. No other baseline-file ID changes `[unverified — relayed from staff-sdet]`.
18. Every module in `PRODUCTION_MODULES` must pass three checks: no `from transcript_analysis.<m> import`, no aliased sibling binding, and every `<sibling>.<name>` read must name a real binding. `[verified: prefix test :176-264]`
19. `_friction_signals`' local `denials` (:3330) shadows a module named `denials`, today in the shim and after the move in `friction_count.py`, and its comment (:3328-3329) holds in both. ruff selects E/F/B/I/UP/SIM and not RUF100, so the `# noqa: F811` is harmless either way. `[verified: pyproject.toml:6]` Whether F811 still fires there `[unverified]`
20. The hook runs the real shim's `friction-count … --checkpoint …` under a 10 s cap and accepts only digits on stdout (nudge-error-mode-analysis.sh:151-155). Its test symlinks the real shim and builds that path inside a function (:80-86). select-tests maps a change under `scripts/` to the scripts tests plus listed exceptions (select-tests.py:571, :767, :225-227), so before Part 5 it never selects the hook test. `[verified]`
21. The bootstrap file is 882 lines. Buckets already has a subprocess test (:151-172). One function per command, as in the precedent (:446-501), would push the file past 1,000. `[verified]`
22. `capture` behavior:
    - it runs its runs in file order, from the harness's working directory;
    - it pins `CLAUDE_CONFIG_DIR` to `--account` and points `TRANSCRIPT_CONFIG_DIRS_FILE` at an absent file, and `--env` overrides either;
    - `--all-help` writes one `.parser` file per subcommand;
    - it refuses an existing `--out`.

    `[verified: _move_fidelity.py:501-513, 609-664]`
23. user-input prints `Generated: <today>` (:791), and `_fmt_date` renders a UTC date (render.py:78-79). Captures taken on different UTC dates therefore differ on that line. `[verified]`
24. Scope and redaction behavior of the moved commands:
    - user-input reads `iter_sessions(scope.PROJECTS_DIR, …)` (:667) and ignores declared roots;
    - `_build_redact_map()` defaults to `scope.PROJECTS_DIR` at call time (redaction.py:122);
    - `main()` reassigns `scope.PROJECTS_DIR` for a top-level `--config-dir` (:4678);
    - none of the six is in `_SUBCOMMANDS_REFUSING_TOP_LEVEL_CONFIG_DIR` (scope.py:700-706).

    `[verified]`
25. Registration order: buckets, fail-seq, struggle, user-input, and duration are contiguous, with subagents next. friction-count registers last. Of the 42 `add_parser(` calls, 39 are in the shim and 3 in the package. `[verified: grep]`
26. Block shapes:
    - fail-seq adds `--branches` before the scope args;
    - user-input builds its own `--projects`/`--branches`/`--since`/`--until` with `type=_iso_date`, which `scope.py` already owns;
    - `--gap-minutes`' help states no default.

    `[verified: :3587-3632; shim imports _iso_date from scope :244]`
27. None of the six takes `--no-redact`, so the multi-root refusal test has no site in this phase. `[verified]`
28. S7: buckets, fail-seq, struggle, and duration print raw branch names. The remediation adds a per-command `--redact` that reuses a per-call-site label closure. `[verified: discovery-audit-remediation-plan.md:644-649]` Issue #1157 owns it `[unverified — relayed]`
29. This worktree sits four levels below the main checkout. `../../../../.venv/bin/pytest` and `…/ruff` exist `[verified: Glob]`. The venv's base interpreter is `/usr/bin/python3.12`, version 3.12.3 `[verified: ../../../../.venv/pyvenv.cfg:1-4]`. `…/python3` is a symlink to `/usr/bin/python3` `[verified: staff-sdet's round-2 ls -l]`, and Verification 0 checks that it exists.
30. `select-tests.py` unions committed, dirty, and untracked paths (:892-920). A plans-only diff selects nothing (:577), so baseline junit runs must name their files. `[verified]`
31. One test in the legacy file carries the timing marker (:475). `[verified]`
32. `test-ids` matches moved tests without a declaration, because an ID drops its module. `[unverified — relayed from decomposition-skill.md:637]`
33. Revert rehearsal: the governing plan prescribes it (transcript-analysis-decomposition.md:276-279), and the engineer dropped it for the skill phase (decomposition-skill.md:355). `[verified]` This phase drops it too. `[engineer-verified: "Drop it (Recommended)"]` The label answered whether this phase should drop the rehearsal too. That option's description paraphrased this plan's recommended default and is not covered by this tag.
34. Package conventions: a module docstring, then `from __future__ import annotations`, then sibling imports by unaliased module. `[verified: skill_pair.py:1-15; subagent_mix.py:1-20; row 18]`
35. The architecture-doc test requires a heading per package module. `[verified: test_architecture_doc_documents_every_package_module at :35]` Its exact matching `[unverified — relayed from decomposition-prep.md row 19]`
36. The branch-attribution loader retarget (M8, Part 3) stays in this PR, although the shim's retained `cmd_buckets` binding would let that file pass untouched. `[engineer-verified: "Retarget branch-attribution"]`
37. The bootstrap dispatch tests and their seed (M9) stay in this PR alongside the `--help` pins. `[engineer-verified: "Bootstrap dispatch tests"]`
38. This PR closes the select-tests gap that row 20 describes. `[engineer-verified: "Fix it in this PR"]` The tag covers only that label, read against the session's question of how this PR should handle select-tests never selecting the friction-count hook test for shim or package changes.
39. That option's description was the session's wording, not the engineer's: a select-tests row mapping the shim and `transcript_analysis/` to the hook test, which widens the file list into `select-tests.py` and its tests. Part 5's mechanism is this plan's. Row 51 records the engineer keeping its extension to `_config.py` and `_config_dir.py`. `[unverified]`
40. select-tests rule-table facts at `d7976404`:
    - The hook test's own domain rule exists: `HOOKS_DIR` → `HOOKS_TESTS_DIR` (select-tests.py:570) selects it for any hook or hook-test change.
    - The shim and every package file match the `SCRIPTS_DIR` rule (:571) → `SCRIPTS_TESTS_DIR` and `_is_py_source_under_claude_or_plugins` (:507-515, :809) → `TICKET_REFERENCE_DISCIPLINE_TEST_PATH` (:126). Package files also match the package row (:767) → `test_review_ledger_script.py` (:222-227). Eight package files also match the review-bench row (:78-88, :746-749). The package's `__init__.py` also matches `_is_test_tree_packaging_change` (:528-534, :810) → `CLAUDE_TESTS_DIR`. None of these rows targets `HOOKS_TESTS_DIR` or the hook test.
    - Of the hook tests, only `test_nudge_error_mode_analysis.py` runs the shim, through symlinks it builds at :84-86 and :139-141. `test_review_ledger_script.py` imports the package by name (:25) and is already a target.
    - The shim's first-party imports are the package and `_config` (shim :25, :34-292). Package modules import `_config` and `_config_dir` (scope.py:31, pr_cost.py:23, cost_ledger.py:28-29), and `_config.py` imports `_config_dir` (:23).
    - The table already declares a transitive importer: the `CONFIG_DIR_MODULE` row selects `test_review_ledger_script.py` "via transcript_analysis.author_outcome -> scope" (:608-611, :758-766).
    - A file target must be in `test_select_tests.py`'s `_FILE_TARGETS` (:1961-1975), or `test_every_directory_target_exists_on_disk` (:2194-2201) checks it as a directory. An exact-match constant belongs in `_EXACT_MATCH_LITERAL_PATH_CONSTANTS` (:1922-1955).
    - Four tests assert an exact target set for a path the new row matches: :1311-1320 (the shim), :1432-1446 (`_config.py`), :1448-1466 (`_config_dir.py`), and :1468-1482 (a package module). The `_config_dir.py` test at :751-755 and the review-bench test at :741-749 assert membership only.
    - An import failure anywhere in the closure already fails the `SCRIPTS_TESTS_DIR` tests that load the shim. What only the hook test checks is the contract: the bare-integer stdout, `--checkpoint`, and the shim run through a symlink.

    `[verified: reads of select-tests.py, test_select_tests.py, and the shim; grep of claude/.claude/hooks and the package]` The hook test's two symlink helpers create only the shim's symlink, plus the opt-in marker in the config-dir case, so no package sits beside it `[verified: test_nudge_error_mode_analysis.py:79-86, :133-142]`
41. `select-tests.py` is itself a `GLOBAL_TRIGGER_PATHS` member (:419-423). This PR's diff therefore selects the full suite and prints `select-tests: running the full suite (global-trigger: claude/.claude/scripts/select-tests.py)` on stderr (:1154-1156). That is CLAUDE.md § Commands' first named case. `[verified: read]`
42. Shim :325's `(see test_transcript_analysis.py)` points at tests that move to `test_transcript_struggle.py`. `nodes` compares every moved node's comments, verbatim or not (_move_fidelity.py:348-351). `[verified: read]` That this is the only file pointer in the moved production ranges `[unverified — relayed from staff-backend-engineer]`
43. `TestNoBareSameDirectorySiblingImports` and `test_ticket_reference_discipline.py` list their files with `git ls-files` (test_pytest_collection_config.py:457; test_ticket_reference_discipline.py:36, :279), so they see a new file only once it is staged. `[verified: grep]` `git grep` likewise searches only tracked files unless given `--untracked` `[unverified — relayed from staff-backend-engineer]`.
44. Three of `TestDurationGapSplit`'s five tests recompute the gap arithmetic inline and never call `cmd_duration` (legacy :857-880). The other two call it without `gap_minutes`, so they test only the default gap (:882-922; shim :861). `[verified: read]`
45. `test-ids` keys an ID by the classname components after the `test_` module plus the test name (_move_fidelity.py:745-750). `--rename` matches a whole before ID or its `::`/`[` stem (:775-779) and splits `OLD=NEW` at the first `=` (:98-104). `[verified: read]`
46. `git fetch origin main` updates `origin/main`, which otherwise stays at the last fetch. `git diff --name-only A...B` lists the files changed on `B` since the merge-base of `A` and `B`, so this branch's own commit never appears in Verification 0's or 10's output. `[unverified — git documentation not read here]` If the three-dot reading is wrong, the check lists this PR's own files after the commit and stops the run, which is the safe direction.
47. The hook runs the shim only under Python 3.11 or later (nudge-error-mode-analysis.sh:128), while the venv runs 3.12 (row 29). `[verified: read]` `ast.parse`'s `feature_version` check is best-effort and may accept some 3.12-only syntax `[unverified — relayed from staff-sdet]`.
48. `select-tests.py` forwards its own arguments to pytest verbatim, after the selected targets (select-tests.py:1132, :1004-1010, :1189-1190). pytest's `addopts` already pass `-n auto` (pyproject.toml:24). The `timing` marker's own text says to run those tests serially with `-m timing -n0` (pyproject.toml:31). `[verified: read]`
49. The revert-rehearsal waiver stands under the corrected rollback reasoning: a revert also conflicts once any later commit touches one of this PR's files. `[engineer-verified: "Still drop it (Recommended)"]` The tag covers only that the waiver still stands, read against the session's question of whether it does after that correction.
50. The reason in that option's description was the session's proposal, not the engineer's: a rehearsal only proves the revert applies right after merge. `[unverified]`
51. Part 5's row also selects the hook test when `_config.py` or `_config_dir.py` changes, not only the shim or the package. `[engineer-verified: "Keep the extension (Recommended)"]` The tag covers only keeping that extension, read against the session's question of whether to keep it. Any description that option carried was the session's wording and is not covered.

**Mechanisms:**
- M1: Six command modules, one per command, and no leaf. `anchors: row3, row6, row7, row9`. Alternatives: see Alternatives set aside.
- M2: Each shared constant lives in its owning command module and is read by module. `anchors: row6, row8`. Lighter than a leaf. A duplicate copy breaks single source of truth.
- M3: Per-command registrars called at the blocks' old positions. `anchors: row4, row25, row26`. Leaving the blocks in place contradicts row 4. A shim-created subparser filled in by a module function splits one command across two files.
- M4: The shim keeps `cmd_*` bindings for the five commands that staying tests still read, and none for friction-count. `anchors: row10`
- M5: F401 plus a `_mod.<name>` grep decide each orphaned shim import. `anchors: row12`
- M6: One test file per module that loads no shim, with the test rename map. Cross-command classes are placed per row 15. `anchors: G2, row13, row15, row44`
- M7: `tests/_session_signal_helpers.py`. `anchors: row14`
- M8: Three edits to staying tests: the `TestBuildParser` exemplar, the structure-table key, and the branch-attribution loader. `anchors: row10, row16, row17`
- M9: Two parametrized bootstrap tests and one seed. `anchors: row20, row21, row24`. One function per command breaks the size limit. Dropping the dispatch tests leaves no permanent fresh-interpreter pin of the handler binding, because the parser dump is a per-PR gate.
- M10: The unchanged move-fidelity harness: `nodes`, `remainder`, and `capture --all-help` on a single-root and a multi-root corpus, plus `compare-captures --moved-callable` and `test-ids`. `anchors: G5, row17, row22, row23, row24, row42, row45`. Help-byte parity alone cannot see a handler binding. Only a multi-root run pins that user-input still ignores declared roots.
- M11: One `code-writer` dispatch, with no stages, which also makes Part 5's edits. `anchors: row11, row47`
- M12: One select-tests row maps the shim's first-party import closure to the hook test. Control 8(f) proves that test runs the moved code, and control 8(h) proves the row is what selects it. `anchors: row20, row38, row39, row40, row41, row51`. Lighter and wider alternatives: see Part 5's Set aside list.
- M13: Doc edits. `anchors: row35`
- M14: Command shapes that the worktree Bash guard and git's tracked-file defaults accept: plain `cp` snapshots, Write-tool checksum files, a `git fetch` plus a path-scoped three-dot `git diff` for main drift, untracked-aware greps, and post-staging runs of the two tracked-file scanners. `anchors: row43, row46`. The drift check replaces an open-PR `gh pr list --jq` check. Each fix to that check opened a new gap: a 100-file-per-PR cap, then a `gh pr diff` fallback, then a `>=` that a static guard read as a redirect. A concurrently open PR that touches the same files instead surfaces as an ordinary merge conflict on whichever PR lands second.
- M15: The full suite runs as two background passes split by the `timing` marker. It re-runs only when a review round edits production code or the select-tests pair. `anchors: row41, row48`. A single pass would run the `timing` tests under `-n auto`, against their marker's serial rule.

## Critical files

**Dispatch: one `code-writer`, the move.** The brief requires:
- copy moved code verbatim, extracted by line range from the parent's `<scratch>/before-shim.py` and `<scratch>/before-legacy.py`, applying only Parts 1–4's edits, then make Part 5's edits;
- keep the new production code runnable on Python 3.11, the hook's floor (row 47);
- use `../../../../.venv` as the interpreter path (row 29), and if `…/bin/pytest` or `…/bin/ruff` is missing, stop and return without substituting another interpreter;
- if F821 flags a name absent from the rename maps, the stdlib, conftest, or row 14's helpers, stop and report;
- if any new file exceeds 1,000 lines, stop and report;
- do no staging and no commits.

Its return lists:
- every F401 disposition from Part 2;
- the wrap-only reflow count;
- every new bootstrap and `test_select_tests.py` test ID;
- `wc -l` for each new and changed file.

Files:
- **Create:**
  - `claude/.claude/scripts/transcript_analysis/buckets.py`, `…/fail_seq.py`, `…/struggle.py`, `…/user_input.py`, `…/duration.py`, `…/friction_count.py`;
  - `claude/.claude/scripts/tests/test_transcript_buckets.py`, `…/test_transcript_fail_seq.py`, `…/test_transcript_struggle.py`, `…/test_transcript_user_input.py`, `…/test_transcript_duration.py`, `…/test_transcript_friction_count.py`, `…/_session_signal_helpers.py`.
- **Modify:**
  - `claude/.claude/scripts/transcript-analysis.py`;
  - `claude/.claude/scripts/transcript_analysis/__init__.py`;
  - `claude/.claude/scripts/select-tests.py`;
  - `claude/.claude/scripts/tests/test_transcript_analysis.py`, `…/test_transcript_analysis_branch_attribution.py`, `…/test_transcript_analysis_branch_attribution_structure.py`, `…/test_transcript_cli_bootstrap.py`, `…/test_transcript_package_module_prefixes.py`, `…/test_select_tests.py`;
  - `docs/transcript-analysis-architecture.md`.
- **Also in the diff:** `.claude/plans/session-signal.md`.

Its verification, from the worktree root:
- `../../../../.venv/bin/pytest` over:
  - the six new test files;
  - `test_transcript_analysis.py`, `test_transcript_analysis_branch_attribution.py`, `test_transcript_analysis_branch_attribution_structure.py`, `test_transcript_cli_bootstrap.py`, `test_transcript_package_module_prefixes.py`, `test_transcript_analysis_architecture_doc.py`, `test_transcript_analysis_import_cycles.py`;
  - `test_transcript_cost.py`, `test_transcript_ledger_common.py`, `test_transcript_review_trace.py` (the readers of retained shim imports);
  - `claude/.claude/scripts/tests/test_select_tests.py`;
  - `claude/.claude/hooks/tests/test_nudge_error_mode_analysis.py`.
- `../../../../.venv/bin/ruff check claude/.claude/ claude-skills/ plugins/ evals/`, clean.
- `wc -l` on every new and changed file.

**Parent:** Verification 0–1 before the dispatch. Verification 3–9 after it, then `/code-review` and the commit. A review-round edit re-runs Verification 3, 5–7, and 9 and this dispatch's test list above. It re-runs Verification 4 only when the round edits the shim, a package module, `select-tests.py`, or `test_select_tests.py`, because the dispatch's test list covers every other file in the diff.

**Rebase rule.** A rebase or merge that brings in a change under `claude/.claude/scripts`, `claude/.claude/hooks`, or `docs/transcript-analysis-architecture.md` invalidates every line range, snapshot, capture, and junit baseline. All of them are keyed to the pre-rebase `HEAD`. They also read files outside Critical files: `scope.py`, `corpus.py`, `render.py`, `redaction.py`, `denials.py`, `conftest.py`, `_move_fidelity.py`, `_config.py`, `_config_dir.py`, and the hook. Verification 0 and 10 detect such a change.
- Before the commit: run the Abort procedure, then rebase, then restart at Verification 0 with a fresh `<scratch>`. Never run the Abort procedure after a rebase.
- After the commit: stop and report to the engineer (Verification 10).

**Abort procedure.** If the dispatch is interrupted, the code-writer cleans nothing, and the parent recovers:
1. `git status --short` must list only the ten modified files above, the 13 created files, and the plan. Any other path: stop and report, touching nothing.
2. Restore each modified file with its own literal `git restore --source=HEAD -- <path>` call.
3. Delete each created file by its literal path.
4. Success: `git status --short` lists at most the plan.

Never use `git clean`, `git checkout .`, `git reset --hard`, or `git stash`.

**Explicitly unchanged:**
- `conftest.py`, `_move_fidelity.py`, and its self-tests;
- every other package module, `redaction.py`, `scope.py`, and `denials.py` included;
- `docs/transcript-analysis.md` and the transcript-analysis `SKILL.md`, because the CLI is identical;
- `nudge-error-mode-analysis.sh` and its test;
- `README.md`.

**Reuse:**
- `scope._add_project_scope_args` and `scope._iso_date`;
- conftest's `fake_projects`, `_write_jsonl`, `_asst`, `_user_msg`, `_bash_use`, `_tool_result`, `_agent_use`, `_hook_deny`, `_hook_deny_current`, `_review_trace_args`, `_attr_ts`, and `_table_cols`;
- bootstrap's `_run`;
- the skill modules' registrar shape (skill_pair.py:110-129);
- `tests/_skill_helpers.py` as the helper-module shape;
- `select-tests.py`'s `TRANSCRIPT_ANALYSIS_PACKAGE_DIR`, `CONFIG_MODULE`, `CONFIG_DIR_MODULE`, and `_is_under`, rather than new path literals for them.

**PR body carries:**
- each Verification result, with the harness counts;
- measured `wc -l` for every new or changed file, flagging the shim, the legacy file, `select-tests.py`, and `test_select_tests.py`, which were already over 1,000 lines;
- the reflow count;
- that the engineer waived the revert rehearsal for this phase, and kept the waiver under the corrected rollback reasoning (rows 33, 49);
- the rollback statement: "A plain `git revert` of the squash commit applies cleanly and restores the pre-PR tree only while no later commit has touched a file this PR touches or read a name it adds (the six modules, their `_add_<command>_subparser` registrars, `tests/_session_signal_helpers.py`, or `select-tests.py`'s two new constants). The remaining decomposition phases edit the shim or the legacy test file, so once the next one merges, fix forward. Cross-phase reverts run last-in-first-out.";
- the structure test's one renamed test ID, and why (Verification 5);
- that `select-tests.py`'s own edit selected the full suite (row 41);
- the follow-ups in Out of scope.

## Verification

Run everything from the worktree root. Notation:
- `<venv>` is `../../../../.venv`.
- `<mf>` is `<venv>/bin/python3 claude/.claude/scripts/tests/_move_fidelity.py`.
- `<pkg>` is `claude/.claude/scripts/transcript_analysis`, and `<tests>` is `claude/.claude/scripts/tests`.
- `<scratch>` is one `mktemp -d` directory, created before step 0. Use its printed path literally.
- `<attempt>` is a fresh `mktemp -d` directory for each dispatch attempt.

Run each git command and each capture as its own literal call. Seed files with the Write tool. Before every capture, delete `<scratch>/friction.ckpt` by its literal path.

0. **Preconditions.**
   - `ls <venv>/bin/python3 <venv>/bin/pytest <venv>/bin/ruff` exits 0, and `<venv>/bin/python3 -c "__import__('tokenize').FSTRING_MIDDLE"` exits 0 (G5). Otherwise stop before the dispatch is paid for.
   - Main drift, each its own call: `git fetch origin main`, then `git diff --name-only HEAD...origin/main -- claude/.claude/scripts claude/.claude/hooks docs/transcript-analysis-architecture.md` (row 46). Any output invokes the Rebase rule. A failed call stops the run and goes to the engineer.
   - `git status --short` shows nothing but the plan.
1. **Baseline, before the dispatch.**
   - Copy each of the eight files Parts 1–4 modify into `<scratch>` with its own plain `cp <path> <scratch>/before-<name>` call (`before-shim.py`, `before-legacy.py`, and so on). Step 0 proved the tree matches `HEAD`. Create an empty `<scratch>/empty.py` with the Write tool.
   - Seed `<scratch>/account/projects/-home-user-parityrepo/s1.jsonl`. Every record has `sessionId` `s1`, branch `parity-feat`, and a timestamp on 2026-05-19:
     - (r1) 10:00 user, string `"start the parity work"`;
     - (r2) 10:01 opus assistant with a Bash `pytest` tool_use `t1`;
     - (r3) 10:02 user `tool_result` `t1`, `"3 failed, 17 passed"`;
     - (r4) 10:03 user, string `"that's wrong, try again"`;
     - (r5) 10:04 sonnet assistant;
     - (r6) 10:30 sidechain haiku assistant;
     - (r7) 11:05 user `is_error` `tool_result` `t2`, using `_hook_deny_current`'s denial text;
     - (r8) 11:06 sonnet assistant.
   - Write `<scratch>/runs.txt` with these runs, numbered run-01 to run-19 in this order (absolute scratch paths written literally):
     - run-01 `buckets`
     - run-02 `buckets --branches parity-feat`
     - run-03 `buckets --this-repo --projects x` (exit 2)
     - run-04 `fail-seq --branches parity-feat`
     - run-05 `fail-seq` (exit 2)
     - run-06 `struggle`
     - run-07 `user-input`
     - run-08 `user-input --redact --corrections-only --truncate-chars 5`
     - run-09 `user-input --since 2026-05-20`
     - run-10 `user-input --since 2026-13-01` (exit 2)
     - run-11 `duration`
     - run-12 `duration --gap-minutes 40`
     - run-13 `duration --gap-minutes x` (exit 2)
     - run-14 `friction-count --transcript <s1>`
     - run-15 `friction-count --transcript <s1> --json`
     - run-16 `friction-count --transcript <s1> --checkpoint <scratch>/friction.ckpt`
     - run-17 the same command again (the incremental path)
     - run-18 `friction-count` (exit 2)
     - run-19 `friction-count --transcript <scratch>/absent.jsonl` (exit 1)
   - Capture twice: `<mf> capture --script claude/.claude/scripts/transcript-analysis.py --account <scratch>/account --runs <scratch>/runs.txt --all-help --out <scratch>/before-a`, then the same into `before-b`. Then `<mf> compare-captures <scratch>/before-a <scratch>/before-b --mask 'Generated: [0-9]{4}-[0-9]{2}-[0-9]{2}'` must be clean (row 23). Every later compare uses this mask.
   - Coverage checks on `before-a`:
     - It holds `help-top` plus one `help-<name>` capture and one `.parser` file per subcommand. The count is 42, and `git grep -c "add_parser("` over the shim and `<pkg>` governs.
     - Each of the six `help-<cmd>.parser` files contains `default func=<callable <script>:cmd_<name>>`.
   - Corpus liveness: `before-a` holds a token only the seed can produce:
     - `parity-feat` in run-01, run-02, run-06, and run-11;
     - `Sequence: 3` in run-04;
     - `(matched: "try again")` in run-07;
     - `private-project-1` in run-08;
     - `3` as run-14's whole stdout;
     - `"denials": 1` in run-15;
     - `3` from both run-16 and run-17.

     If one is missing, fix the seed and recapture both sides.
   - **Multi-root.**
     - Seed `<scratch>/account2/projects/-home-user-foreignrepo/f1.jsonl` with one assistant record and one fresh prompt `"foreign-only prompt"`, both on branch `foreign-only-probe`.
     - Write `<scratch>/roots.txt` holding the absolute path of `account2`.
     - Write `<scratch>/multi-runs.txt` with: `buckets`, `fail-seq --branches parity-feat`, `struggle`, `duration`, `user-input`.
     - Capture twice with `--env TRANSCRIPT_CONFIG_DIRS_FILE=<scratch>/roots.txt` into `multi-before-a` and `multi-before-b`. The two must compare clean.
     - Preconditions: multi-runs run-01 (`buckets`) contains `foreign-only-probe`. Multi-runs run-05 (`user-input`) contains `start the parity work` but not `foreign-only prompt` (row 24).
   - Junit baseline. Run `<venv>/bin/pytest` over these six existing test files, once with `-m "not timing" --junitxml=<scratch>/before-parallel.xml` and once with `-m timing -n0 --junitxml=<scratch>/before-timing.xml` (row 30). All pass. The files: `test_transcript_analysis.py`, `test_transcript_analysis_branch_attribution.py`, `test_transcript_analysis_branch_attribution_structure.py`, `test_transcript_cli_bootstrap.py`, `test_transcript_package_module_prefixes.py`, `test_transcript_analysis_architecture_doc.py`.
2. **Dispatch** per Critical files, with a fresh `<attempt>`.
3. **Lint and sizes.**
   - `<venv>/bin/ruff check claude/.claude/ claude-skills/ plugins/ evals/` is clean. These are CI's lint roots.
   - Record `wc -l` for every new and changed file. Every new file is at most 1,000 lines, and so is `test_transcript_cli_bootstrap.py` (row 21).
   - `<venv>/bin/python3 -c "import ast, sys; [ast.parse(open(p, encoding='utf-8').read(), p, feature_version=(3, 11)) for p in sys.argv[1:]]" <pkg>/buckets.py <pkg>/fail_seq.py <pkg>/struggle.py <pkg>/user_input.py <pkg>/duration.py <pkg>/friction_count.py` exits 0 (row 47). The parse is best-effort, so it does not prove 3.11 compatibility.
4. **Selected suite.** Part 5 edits a global-trigger path, so `select-tests.py` selects the full suite (row 41). It runs on the post-dispatch tree, and again only under Critical files' **Parent** rule.
   - Run two passes, each as a Bash call with `run_in_background`, and start the second only after the first finishes (row 48): `<venv>/bin/python3 claude/.claude/scripts/select-tests.py -m "not timing"`, then `<venv>/bin/python3 claude/.claude/scripts/select-tests.py -m timing -n0`. Both pass. Each stderr names the full suite with the `global-trigger` reason and `claude/.claude/scripts/select-tests.py`.
   - Until both passes finish, edit no file, stage nothing, and start no other test run. Step 8's controls edit production files.
   - Reproduce any failure in a file outside Critical files at the merge-base before claiming or fixing it, per CLAUDE.md's merge-base rule. Run `git merge-base HEAD origin/main` and `realpath <venv>` as separate calls, then `git worktree add <scratch>/merge-base <sha>` with the printed SHA. Run the failing test with `<scratch>/merge-base` as the working directory and the printed absolute venv's `bin/pytest`, because `<venv>` is relative to this worktree. Then run `git worktree remove <scratch>/merge-base`.
5. **Test-ID parity.**
   - Rerun step 1's two junit commands over those six files plus the six new test files, into `<attempt>/after-parallel.xml` and `<attempt>/after-timing.xml`.
   - `<mf> test-ids --before <scratch>/before-parallel.xml --before <scratch>/before-timing.xml --after <attempt>/after-parallel.xml --after <attempt>/after-timing.xml --file-prefix claude/.claude/scripts/tests/` must be clean, with:
     - one single-quoted `--expect-new` for each of Part 3's 11 bootstrap IDs: `test_transcript_analysis_session_signal_help_exits_zero[<command>]` for buckets, fail-seq, struggle, user-input, duration, and friction-count, and `test_transcript_analysis_session_signal_subprocess_finds_seeded_session[<command>]` for fail-seq, struggle, duration, user-input, and friction-count. If the dispatch returns any other set of bootstrap IDs, stop and report;
     - one `--rename` for the structure test's parametrized ID (rows 17, 45), passed as one single-quoted argument: `'TestEveryGitBranchReadIsClassified::test_raw_read_beside_attribution_still_attributes_its_branch[transcript-analysis.py:cmd_fail_seq]=TestEveryGitBranchReadIsClassified::test_raw_read_beside_attribution_still_attributes_its_branch[transcript_analysis/fail_seq.py:cmd_fail_seq]'`.
   - `test_transcript_analysis_import_cycles.py` stays out of this junit set. It holds no moved test, and Verification 4's full suite runs its new per-module IDs.
6. **CLI and parser parity.**
   - Capture into `<attempt>/after` and `<attempt>/multi-after` with step 1's flags.
   - Compare `before-a` against `<attempt>/after` with one `--moved-callable cmd_<name>=transcript_analysis.<module>` declaration for each of the six commands. The compare must be clean.
   - The multi-root pair must compare clean.
   - No `.stdout`, `.stderr`, `.exit`, or `.argv` file may differ.
7. **Move fidelity.**
   - **Production nodes.**
     - Run `<mf> nodes --before <scratch>/before-shim.py` with one `--after` per new module and `--moved` set to the 26 names:
       - `TEST_RUNNER_RE FAILED_RE STRUGGLE_PHRASES _longest_fail_streak cmd_buckets cmd_fail_seq cmd_struggle`;
       - `_is_fresh_user_prompt_for_narrative _is_unrecognized_user_list_record _classify_prompt _attribute_model_to_prompt _truncate_prompt_text cmd_user_input cmd_duration`;
       - `_FRICTION_SIGNAL_WEIGHT _friction_denial_events _friction_failed_test_run_events _friction_struggle_turn_events _friction_signals`;
       - `_FRICTION_CHECKPOINT_SIGNAL_KEYS _is_valid_checkpoint_int _read_friction_checkpoint _write_friction_checkpoint _consume_new_transcript_lines _emit_friction_result cmd_friction_count`.
     - Pass `--verbatim` for 14 of the 15 nodes that read no renamed name. `STRUGGLE_PHRASES` is left out, because Part 1 edits its comment:
       - `TEST_RUNNER_RE FAILED_RE _longest_fail_streak _is_unrecognized_user_list_record _truncate_prompt_text`;
       - `_FRICTION_SIGNAL_WEIGHT _friction_signals _FRICTION_CHECKPOINT_SIGNAL_KEYS _is_valid_checkpoint_int _read_friction_checkpoint _write_friction_checkpoint _consume_new_transcript_lines _emit_friction_result cmd_friction_count`.
     - Pass one `--rename` per entry of Part 1's 24-entry map.
     - It reports exactly one finding, `node-comments: STRUGGLE_PHRASES`, whose before and after lists differ only in `test_transcript_analysis.py` → `test_transcript_struggle.py` (row 42). It reports `compared-nodes: 26`.
   - **Shim remainder.**
     - Run `remainder --before <scratch>/before-shim.py --after <shim> --removed <the 26> --changed build_parser`.
     - Declare `--import-added` for the 11 registrar and `cmd_*` triples.
     - Declare `--import-removed` for `contextlib`, `time`, and the three removed package names.
     - It reports only `remainder-comments` for the two moved leading-comment blocks and the `# sessions` banner, each matched to a `git diff` hunk.
     - `git diff` of `build_parser` shows exactly the six blocks replaced by six calls.
     - Check the import-line comments in `git diff` by eye, because `remainder` ignores them.
   - **New modules.** For each new module, `remainder --before <scratch>/empty.py --after <module> --added __doc__ <its top-level names> --import-added <its triples>` is clean. The one exception is `friction_count.py`'s two leading-comment `remainder-comments` findings.
   - **Test nodes.**
     - Run `nodes --before <scratch>/before-legacy.py`, with `--after` set to the six new test files, `_session_signal_helpers.py`, and the final legacy file.
     - `--moved` the 18 names: the 12 classes plus `_collect_runs _ui_user _user_input_args _friction_count_args _SONNET _branch_args`.
     - `--verbatim` five names: `_ui_user _user_input_args _friction_count_args _SONNET _branch_args`.
     - Pass Part 3's 19 renames.
     - It must be clean, with `compared-nodes: 18`.
   - **Test remainders.**
     - Legacy file: `remainder --removed <the 18> --changed TestBuildParser` plus import triples reports only the banner `remainder-comments`, counted against `git diff`. `git diff` inside `TestBuildParser` changes only the one set-literal line.
     - Each new test file and the helper module compared against `empty.py` is clean.
     - `remainder` is clean for:
       - `__init__.py` (`--changed __doc__`);
       - the prefix test (`--changed PRODUCTION_MODULES`);
       - the structure test (`--changed _RAW_READ_BESIDE_ATTRIBUTION`);
       - bootstrap (`--added` its three new names).
     - The branch-attribution file's `git diff` touches only the loader lines, import lines, and :579.
   - **Select-tests.** `git diff` of `select-tests.py` and `test_select_tests.py` shows only Part 5's edits.
8. **Negative controls.** For each control:
   - run a plain `sha256sum <file>` first, and save its output line to `<attempt>/ctl-<x>.sha256` with the Write tool;
   - edit by hand, run the check, then undo by hand;
   - `sha256sum -c <attempt>/ctl-<x>.sha256` must then pass;
   - never use `git checkout` or `git stash`;
   - put each control capture in a fresh `<attempt>/ctl-<x>` directory.

   The controls:
   - (a) Swap `_add_fail_seq_subparser(sub)` and `_add_struggle_subparser(sub)`. A fresh capture compared against `<attempt>/after` differs only in `help-top.stdout` and `help-top.parser`.
   - (b) In `duration.py`, change `--gap-minutes`' `default=30` to `29`. Exactly `help-duration.parser` and `run-11.stdout` differ. `help-duration.stdout` and run-12 stay equal (row 26).
   - (c) Rerun step 6's compare without the `--moved-callable` declarations. Exactly the six `help-<cmd>.parser` files differ.
   - (d) Two scratch copies of `friction_count.py`:
     - change `max(counts) > 0` to `>= 0`; `nodes` must report `node-ast` on `_friction_failed_test_run_events`;
     - separately change one word of `cmd_friction_count`'s docstring; `nodes` must report `node-ast` on `cmd_friction_count`, a verbatim node.
   - (e) In a copy of `after-parallel.xml`, delete one moved `<testcase>`. `test-ids` must report `id-missing`.
   - (f) Make `_emit_friction_result` print `composite=<n>` instead of the bare integer. `test_nudge_error_mode_analysis.py` must fail, which proves the hook path runs the moved code.
   - (g) Two separate edits, each followed by a run of `test_transcript_cli_bootstrap.py` alone:
     - in `user_input.py`'s registrar, bind `func=struggle.cmd_struggle` instead of `cmd_user_input`; exactly the `user-input` dispatch ID fails;
     - delete `_add_duration_subparser(sub)` from the shim's `build_parser`; both `duration` IDs fail, and no other new ID does.
   - (h) Delete Part 5's `CROSS_DOMAIN_EXCEPTIONS` row, then run `test_select_tests.py` alone. Exactly eight IDs fail: the new test's four and the four exact-set tests Part 5 updates.
9. **Leftovers and single homes.** Every `git grep` here passes `--untracked`, because the 13 new files stay untracked until the commit (row 43). Each check that expects nothing is paired with a probe that must hit in the same paths.
   - `git grep --untracked -nE "^def (cmd_buckets|cmd_fail_seq|cmd_struggle|cmd_user_input|cmd_duration|cmd_friction_count|_longest_fail_streak|_classify_prompt|_friction_signals)\b|^(TEST_RUNNER_RE|FAILED_RE|STRUGGLE_PHRASES)\b" -- claude/.claude/scripts` returns one hit per name, in its owning module.
   - `git grep --untracked -nE "^def (_ui_user|_user_input_args|_branch_args)\b|^_SONNET = " -- <tests>` returns one hit each, all in `_session_signal_helpers.py`.
   - `git grep --untracked -nw "_mod" --` over the six new test files and the helper module returns nothing. The word match skips `_model` in `_attribute_model_to_prompt` and in the moved `test_attribute_model_*` names. Probe: `git grep --untracked -l "^from transcript_analysis import" --` over the six new test files lists all six. The bullet above is the helper module's probe.
   - `git grep --untracked -n "from _config_dir import" --` over the six new modules returns nothing. Probe: `git grep --untracked -l "^from transcript_analysis import" --` over the six new modules lists all six.
   - `git grep --untracked -nE "_mod\.cmd_(buckets|fail_seq|struggle|user_input|duration|friction_count)\b" -- <tests>` returns only row 10's legacy-file sites.
   - `git grep --untracked -n "transcript-analysis.py:cmd_fail_seq" -- claude/.claude/scripts` returns nothing. Probe: `git grep --untracked -n "transcript_analysis/fail_seq.py:cmd_fail_seq" -- claude/.claude/scripts` returns the structure test's one key.
   - Last, the parent stages the change with one literal `git add <path>` call per Critical file. Then `<venv>/bin/pytest claude/.claude/tests/test_pytest_collection_config.py::TestNoBareSameDirectorySiblingImports claude/.claude/hooks/tests/test_ticket_reference_discipline.py` passes. Both list files with `git ls-files`, so Verification 4's run saw none of the new files (row 43).
10. **Main re-check, before `/ready-for-review`.** Repeat step 0's `git fetch` and `git diff` calls. Any output invokes the Rebase rule's after-commit branch: stop and report that output to the engineer.

## Out of scope

- **Audit item S7, the missing `--redact` on buckets, fail-seq, struggle, and duration.** This PR is a behavior-preserving move, and the remediation plan owns S7 (row 28).
- **user-input ignoring declared roots.** It is a behavior change. Verification 6's multi-root capture pins the current behavior so that the move cannot change it silently.
- **The shim docstring's write-flag list.** It omits user-input `--out` and friction-count `--checkpoint`. The gap predates this PR, and the `cli.py` phase rewrites the shim. Raise it as a follow-up.
- **No friction-count section in `docs/transcript-analysis.md`.** The CLI is unchanged. Raise it as a follow-up.
- **Renaming `_friction_signals`' local `denials`, which shadows the module import.** It moves verbatim (row 19).
- **Moving the staying `_mod.cmd_*` reads to package modules,** and the readers of the retained shim imports (row 12). These go to the `cli.py` and leaf-module-tests phases (row 4).
- **Classes that stay in the legacy file:**
  - `_UNCONDITIONAL_HEADER_CASES` with its two classes, and `TestRootsThreadingSpy`;
  - `TestPerRecordConsumersCreditPrefixToTheBranchEntered` and `TestSubagentRecordsFollowTheDispatchingBranch`, which span unmoved groups;
  - `TestProjectFamily`, `TestIsFreshUserPrompt`, `TestResolveProjectScope`, and `TestBuildParser`, apart from row 16's single-line pin change.
- **Strengthening `TestDurationGapSplit`.** Three of its five tests recompute the gap arithmetic without calling `cmd_duration`, and no permanent test covers `--gap-minutes` (row 44). They move verbatim. Raise it as a follow-up.
- **Splitting the shim, the legacy test file, `select-tests.py`, or `test_select_tests.py` under 1,000 lines.** All four were already over the limit.
- **The governing plan's revert rehearsal.** The engineer waived it for this phase (rows 33, 49).
- **Any CLI, help-text, or output change, and public names for the registrars.**
