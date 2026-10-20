# Decompose transcript-analysis: handoff-nudge A (shared core, rearm-backtest, spend-over-threshold)

## Context

Goal: move the handoff-nudge A group (the shared rearm/handoff core, the rearm-backtest command, and the spend-over-threshold command; phase issue #1175 of tracking issue #1116) out of the monolithic `transcript-analysis.py` shim into the `transcript_analysis` package, together with its tests, preserving behavior and reusing the shape of the dispatch-group phase (`.claude/plans/decompose-transcript-dispatch.md`).

Why now: the dispatch-group phase (#1214) merged, and the architect consult on the next phase picked this one. It holds the shared core that handoff-nudge B (plan-boundary, handoff-signal-response) consumes, and package modules cannot import from the shim, so B cannot move first. The governing plan is `.claude/plans/transcript-analysis-decomposition.md`.

Intended outcome: three new package modules and their test files, the shim reduced to imports and `build_parser()` wiring for these commands, no change to CLI output.

Ask: "lets plan the next phase i want the architect to indicate what should be next". On the architect's pick of A: "Architect is correct".

## Approach

The handoff-nudge A group moves out of the shim into three new package modules. Code is copied verbatim, node by node, in shim source order:
- `transcript_analysis/handoff_nudge.py` is the shared core and has no `cmd_*` of its own. It holds:
  - the hook's fire-threshold mirror;
  - the turn-index ramp curve;
  - `_extract_rearm_session_turns` and its whole-session scope filter;
  - the bounded `.handoff-nudge.log` reader, its parser, the lag join, and the diagnostic footer.
- `transcript_analysis/rearm_backtest.py` holds `cmd_rearm_backtest`, `_rearm_backtest_report`, and every helper only they use.
- `transcript_analysis/spend_over_threshold.py` holds `cmd_spend_over_threshold`.

The tests move into four files split at the same seams, plus `tests/_handoff_nudge_helpers.py`. Two new tests join them:
- M11's direct-call `--no-redact` test;
- M17's positive diagnostic-footer test.

The shim keeps `build_parser()`. It imports the two `cmd_*` names for `set_defaults`, plus the seven core names that plan-boundary and handoff-signal-response still read bare. Those seven names are why A goes before B: package modules can't import from the shim.

One shared predicate also gets a single home (D1):
- `_is_fresh_user_prompt` moves verbatim from the shim into render.py.
- review_rounds.py's duplicate is deleted.
- The shim, review_rounds, and rearm_backtest all read render's copy (M5).

#1009 also edited review_rounds.py and the shim. It landed first, as d603ae99, which is the base this plan is measured at. Its session reported no edit to the predicate or its duplicate (rows 13, 53).

No stow consumer sees a CLI change:
- CLI help, stdout, stderr, and exit codes stay byte-identical (Verification 3). The one exception: an uncaught traceback names the new files (row 40).
- judgment-pair and review-round-cost output also stays unchanged, because Verification 7 proves every predicate copy equivalent (row 51).
- A consumer whose `~/.claude/scripts` isn't a folded directory symlink must re-run `./install.sh`. The PR body's stow line says so (row 47).

The Step 3 evidence needed these corrections. Each one changes the plan:
- **Split by consumer, not by theme.** The explorer's split (nudge-log / turn-extraction / backtest-plus-spend) has two problems:
  - It puts spend's command next to rearm's report.
  - It separates `_print_nudge_log_diagnostic` from `_read_bounded_log_lines`, which that function's docstring calls "above" (row 39).

  This plan puts everything that B or spend also reads in the core module, and everything only rearm reads in rearm (M3).
- **The cited `--no-redact` test doesn't reach the guard that moves.** Legacy :11100 runs through `cmd_rearm_backtest`, then `scope._resolve_cost_roots`, whose own refusal (scope.py:766–772) stays where it is. No test calls the inline guard in `_rearm_backtest_report` (:4855–4861) directly. M11 adds that one direct-call test, as cost, plan-boundary, and cache-rebuild already have (row 19).
- **Legacy :4609's `config_dir` patch passes vacuously once production moves** (row 18). Verification 4(b) controls for it once, and M17 controls for it permanently.
- **The hook-contract class reads the real hook at class level, and select-tests' completeness check can't see that read** (row 24). The hooks-dir `.sh` rule already selects the new file, so M10 only corrects select-tests.py's comments: no new constant, no test_select_tests.py edit (rows 66–67).
- **No conftest builder moves, and `fake_projects` gains no patch line** (M6). conftest changes only in docstrings.
- **docs/transcript-analysis.md, cost.py, scope.py, and test_transcript_workstream_cost.py need no edit.** Their pointers name symbols, not files (row 38).
- **#1175's claim that rearm-backtest calls `config_dir()` doesn't hold** (row 17).

Round-1 review needed these changes:
- **The prefix guard can't see scope.py's lazy `PROJECTS_DIR`** (row 61). M15 now derives the names a module-level `__getattr__` resolves, so rearm_backtest.py can join `PRODUCTION_MODULES` with no production change.
- **The footer's positive path had no test** (row 62). M17 adds one. It also makes M6's attribute read an invariant that CI checks.
- **Step 1 runs the full suite, because M10 edits select-tests.py**, which is a global trigger (row 64). Verification 1 now runs CI's two passes.
- **The dispatch gate tests every open PR's file list**, not only PRs that match by name (see the Critical files precondition and row 65).
- **`errno` stays in the legacy file's imports** at d603ae99 (row 60).

Alternatives considered and set aside:
- **Two modules, with spend inside the core.** That puts a `cmd_*` into the module B's two commands import. Spend shares nothing with rearm that the core doesn't already hold.
- **One module.** It would be about 1,060 lines with its header, over the 1,000-line limit (row 8).
- **The explorer's three-way split by theme.** See the first correction above.
- **Keeping the hook-contract class in the legacy file.** The class tests an A function, and the governing plan moves each group's tests with its code.

### Engineer decisions

**D1. `_is_fresh_user_prompt` gets one home.** The engineer answered with neither listed option (row 48). This plan follows row 49's reading of that answer: the shim's copy moves into render.py, and review_rounds.py's copy is deleted (M5). The coordination that answer asked for is done: the #1009 session replied, and #1009 landed first (rows 13, 53).

**D2. The post-merge revert rehearsal** (transcript-analysis-decomposition.md:276–279) is waived for this phase (row 54). Row 45's rollback statement takes its place in the PR body.

**D3. A positive diagnostic-footer test** is added (row 62; M17).

### Assumption ledger

**Root:** The A group still lives in the shim: spend at :3300–3424, and core plus rearm at :4156–5061. Its test slice, about 1,700 lines, still lives in the legacy file at :4452–4616, :9648–11150, and :11156–11190. B can't move until A's shared core is in the package, because package modules can't import from the shim. `[verified: Read of both shim spans' boundaries and of each test slice's boundaries; top-level and class-def greps at d603ae99]`

**Givens:**
- G1. pytest imports test modules by basename, so new test files keep the `test_transcript_*` prefix and unique basenames. Reason: pytest owns this behavior. `[verified: transcript-analysis-decomposition.md:59–63]`
- G2. `from m import n` binds `n` at import time, so a monkeypatch reaches code only through the binding that code reads at call time. Reason: Python language semantics. `[verified: transcript-analysis-decomposition.md row 1]`
- G3. A directly invoked `python3 transcript-analysis.py` finds the package only through `sys.path[0]`. Reason: CPython owns script bootstrap. `[verified: test_transcript_cli_bootstrap.py:1–11]`
- G4. The content and merge timing of #1222 belong to another work item. Reason: another work item owns it, and the engineer deferred #1222 (row 3).

Three conditions look like givens but aren't. This repo owns each one, so each is a deliberate decline listed in **Out of scope**:
- `build_parser()` staying in the shim;
- the cross-command legacy tables staying put;
- the parametrized cross-command `--no-redact` test.

**Mechanisms:**

- **M1: Scope is the whole A group: core, rearm-backtest, spend-over-threshold, and their test slice.** `anchors: root, row1, row6`
  - Lighter, rejected: move the core only. The shim would then import back about 16 core names for rearm's report and spend, and their tests would split across two phases.
  - Lighter, rejected: move rearm only. Its core helpers must move too, since the package can't import the shim. Spend also calls two of them directly: `_extract_rearm_session_turns` (:3341) and `_print_nudge_log_diagnostic` (:3363, :3385). It reaches three more transitively.
- **M2: Three modules, each filled by node extraction in source order.** `anchors: row4, row5, row8`
  - `handoff_nudge.py` takes, in order:
    - :3388–3424: `_NUDGE_LOG_MAX_READ`, `_read_bounded_log_lines`, `_print_nudge_log_diagnostic`.
    - :4160–4196: the two threshold constants and the ramp-bucket constants, with their comments.
    - :4200–4206: `_NUDGE_LOG_LINE_KINDS` and its comment.
    - :4209–4229: `_ramp_curve_turn_index_bucket` and `_hook_effective_fire_threshold`.
    - :4275–4546: `_extract_rearm_session_turns` through `_operator_response_lag_from_log`.
    - :4760–4779: `_session_matches_rearm_scope`.
  - `rearm_backtest.py` takes :4198, :4232–4272, :4549–4757, and :4782–5061.
  - `spend_over_threshold.py` takes :3303–3385.
  - Top-level nodes are separated by two blank lines.
  - The section banners at :3300 and :4156–4158 are dropped, and the module docstrings replace them. rearm's docstring keeps the banner's pointer to its design plan.
- **M3: Each name goes to a module by who reads it.** `anchors: row6, row7, row39`
  - Names that B or spend also reads go to the core. Names only rearm reads go to rearm.
  - `_NUDGE_LOG_MAX_READ` goes to the core:
    - Two functions read it: core's `_read_bounded_log_lines` and rearm's `_rearm_backtest_log_size_lines`.
    - rearm reads it as `handoff_nudge._NUDGE_LOG_MAX_READ`, so legacy :10924's patch, once retargeted, reaches both readers.
  - `_print_nudge_log_diagnostic` goes to the core, although only spend calls it. It stays next to `_read_bounded_log_lines`, so its docstring's "above" stays true with no docstring edit.
  - `_hook_observable_boundaries` and `_nudge_conversion_from_log` go to rearm. Each has one reader there, at :4903 and :5014.
- **M4: Import discipline and rename map.** `anchors: G2, row9, row10, row61`
  - `handoff_nudge.py`:
    - `from transcript_analysis import corpus, pricing, scope`
    - stdlib: `contextlib`, `defaultdict`, `Iterable`, `Sequence`, `Path`.
  - `rearm_backtest.py`:
    - `from transcript_analysis import handoff_nudge, render, scope`
    - stdlib: `argparse`, `statistics`, `sys`, `defaultdict`, `Sequence`, `datetime.{UTC,date,datetime}`, `Path`.
  - `spend_over_threshold.py`:
    - `from transcript_analysis import corpus, handoff_nudge, render, scope`
    - stdlib: `argparse`, `defaultdict`, `datetime.{UTC,datetime}`.
  - Each module also starts with `from __future__ import annotations`. No module has any other import line, and none has a `from transcript_analysis.<m> import` line (Verification 7's import-set check).
  - Rename map:
    - These go to `scope.`: `_resolve_cost_roots`, `_branch_filter`, `_parse_since_nd_arg`, `_DO_NOT_PUBLISH_BANNER`, `_resolve_project_scope`, `_redaction_ordinals`, `config_dir`.
    - `_print_resolved_scope` becomes `scope.print_resolved_scope`.
    - `_resolve_scan_roots` becomes `scope.resolve_scan_roots`.
    - `_parse_ts` goes to `corpus.`.
    - `_context_window_for_model` and `_price_turn` go to `pricing.`.
    - `_dedup_turns_by_request_id` becomes `pricing.dedup_turns_by_request_id`.
    - `_pct_of` goes to `render.`.
    - `_is_fresh_user_prompt` goes to `render.` (M5).
    - From rearm and spend, these go to `handoff_nudge.`: `_NUDGE_LOG_MAX_READ`, `_ramp_curve_turn_index_bucket`, `_extract_rearm_session_turns`, `_session_matches_rearm_scope`, `_ramp_curve_from_corpus`, `_parse_nudge_log_entries`, `_operator_response_lag_from_log`, `_print_nudge_log_diagnostic`.
  - Prefix code references only. Docstrings name many of these symbols in prose, so let ruff F821 drive every prefix, and never run a regex over bare names.
  - `scope.PROJECTS_DIR` (:4852) is already prefixed and stays as it is.
  - Wrap-only reflow is allowed where E501 flags a line. Report the count.
- **M5: `_is_fresh_user_prompt` gets one home, render.py, and every reader reads it there.** `anchors: row11, row48, row49, row50, row51, row52, row53`
  - render.py: insert the shim's :292–316 verbatim, docstring included, after render.py:32 (`_content_text`), with two blank lines on each side. Its bare `_content_text` call resolves to render's own function, so it needs no prefix.
  - review_rounds.py:
    - Delete :45–66, so :43–44 stay blank before the :67 `# Mirrors cmd_skill_invocation's own <command-name> regex` comment.
    - :152's call becomes `render._is_fresh_user_prompt(scan_rec)`.
    - The deleted docstring's stale `transcript-analysis.py:199-223` citation (:48) goes with it.
  - The shim:
    - Delete :292–318, so :290–291 stay blank before `_iso_date` (:319).
    - Add `_is_fresh_user_prompt` to the existing `from transcript_analysis.render import (` block (:178–187), with no comment, because judgment-pair reads it bare at :961 and :993.
  - rearm_backtest: `_hook_observable_boundaries` reads `render._is_fresh_user_prompt` (M4).
  - These stay unchanged (row 52): legacy `TestIsFreshUserPrompt`, judgment-pair's body, and test_transcript_review_rounds.py.
  - Rejected: reading `review_rounds._is_fresh_user_prompt` by attribute. Two copies would remain, which row 48 rules out.
  - Rejected: a third copy in rearm_backtest. Row 48 rules it out, and no named duplication exception applies.
  - Rejected: corpus.py or handoff_nudge.py as the home:
    - corpus.py would gain its first package import (row 50).
    - judgment-pair and review-round-cost have nothing to do with handoff nudges.
- **M6: `_print_nudge_log_diagnostic` reads `scope.config_dir()` by attribute.** `anchors: G2, row15, row16`
  - It is the same function object the shim binds, so behavior is identical.
  - `fake_projects` already patches `scope.config_dir` (conftest :919), so conftest needs no new patch line.
  - This follows the architecture doc's attribute rule (:69–71) and the dispatch phase's precedent for `cmd_cost_counts` (decompose-transcript-dispatch.md:92–99, :344).
  - Rejected: a by-name binding (the cost_ledger pattern). It would need a sixth `fake_projects` patch line plus a doc paragraph.
  - The shim keeps its own `config_dir` import, and `fake_projects` keeps its `mod.config_dir` line (conftest :920). Both are for handoff-signal-response (:5971).
  - M17 is this read's permanent control.
- **M7: Shim changes.** `anchors: row4, row6`
  - Make M5's two shim edits.
  - Delete :3300–3426, so :3298–3299 stay blank before the turn-shape banner (:3427).
  - Delete :4156–5063, so :4154–4155 stay blank before the plan-boundary banner (:5064).
  - Add `handoff_nudge`, `rearm_backtest`, and `spend_over_threshold` to the module-import tuple (:35–58) and to its comment (:30–34).
  - Add three by-name blocks in the file's commented style:
    - `from transcript_analysis.handoff_nudge import (` with the comment `# The seven names below are read bare by this file's own still-unmigrated code:` / `#   _extract_rearm_session_turns/_ramp_curve_from_corpus/_ramp_curve_turn_index_bucket/` / `#     _session_matches_rearm_scope -> plan-boundary` / `#   _hook_effective_fire_threshold/_operator_response_lag_from_log/_parse_nudge_log_entries` / `#     -> handoff-signal-response`.
    - `rearm_backtest` → `cmd_rearm_backtest`, with the comment `# Read bare by this file's own still-monolithic build_parser (the rearm-backtest` / `# subcommand's own set_defaults).`
    - `spend_over_threshold` → `cmd_spend_over_threshold`, with the same comment naming spend-over-threshold.
  - Run ruff's autofix only as `--select I --fix`. F401 is expected to flag nothing in the shim. If it flags anything, stop and re-check row 6.
- **M8: Test layout: four files split at the module seams, plus `tests/_handoff_nudge_helpers.py`.** `anchors: G1, row29, row30, row31`
  - The helper module holds two helpers:
    - `_spend_over_threshold_args`, verbatim. Both the spend file and legacy :7952 use it.
    - `_ramp_curve_from_records`. Both the core file and legacy plan-boundary use it. Its two `_mod.` reads, both on :9686, become `handoff_nudge.` through `from transcript_analysis import handoff_nudge`, following `_pr_cost_helpers.py:8`.
  - Every other helper stays local to the one file that uses it.
  - Rejected: merging the two rearm files into one. That file would be about 1,150 lines.
  - Rejected: conftest as the helpers' home. These are family helpers, not record builders (the dispatch phase's M6/M9 rule).
  - Rejected: importing from the legacy module.
- **M9: Test-side retargets.** `anchors: G2, row26, row27, row28`
  - These core names go to `_mod.handoff_nudge.`: `_hook_effective_fire_threshold`, `_ramp_curve_turn_index_bucket`, `_RAMP_CURVE_BUCKET_LABELS`, `_parse_nudge_log_entries`, `_operator_response_lag_from_log`, `_session_matches_rearm_scope`, `_extract_rearm_session_turns`, `_NUDGE_LOG_MAX_READ`.
  - These rearm names go to `_mod.rearm_backtest.`: `_hook_observable_boundaries`, `_simulate_rearm_spacing`, `_nudge_conversion_from_log`, `_parse_rearm_spacings_arg`, `_REARM_BACKTEST_DEFAULT_SPACINGS`, `_rearm_backtest_log_size_lines`, `_rearm_backtest_report`, `cmd_rearm_backtest`.
  - `_mod.cmd_spend_over_threshold` becomes `_mod.spend_over_threshold.cmd_spend_over_threshold`.
  - Names the shim still binds are retargeted too. B's phase deletes the seven-name block, so B never needs to touch A's test files.
  - `monkeypatch.setattr(_mod, "config_dir", _raise_value_error)` (:4609) becomes `monkeypatch.setattr(_mod.scope, "config_dir", _raise_value_error)`.
  - `monkeypatch.setattr(_mod, "_NUDGE_LOG_MAX_READ", …)` (:10924) becomes `monkeypatch.setattr(_mod.handoff_nudge, "_NUDGE_LOG_MAX_READ", …)`.
  - These stay unchanged:
    - reads of names the shim keeps for itself: `_mod._model_rates`, `_mod._parse_ts`, `_mod._cost_report`, `_mod._redaction_ordinals`;
    - the `_mod.scope.*` patches at :10655 and :11107;
    - the `Path.exists` patch at :10885;
    - the legacy reads that stay, at :7952 and :11249.
  - Wrap-only reflow is allowed where E501 flags a line. Report the count.
- **M10: Correct select-tests.py's comments.** `anchors: row22, row23, row24`
  - No new constant, and no test_select_tests.py edit (rows 66–67). The hooks-dir `.sh` rule already selects `test_transcript_handoff_nudge.py` (SCRIPTS_TESTS_DIR) on a hook change, and the glob no longer reads any hook after the move.
  - Replace the :100–106 comment with one that says the glob-matched `test_transcript_analysis.py` (with its two siblings) reads SKILL.md files by path, and `test_transcript_denials.py`, which isn't glob-matched, shells into hook scripts by path and so gets its own exact-path constant.
  - Replace the :546–548 `_is_hooks_or_skills_change` audit-list comment with: `TRANSCRIPT_ANALYSIS_TEST_GLOB` reads SKILL.md files by path, and `TRANSCRIPT_DENIALS_TEST_PATH` shells into hook scripts.
  - Append to the `_is_hooks_dir_shell_script_change` comment (:604–607): `test_transcript_handoff_nudge.py` (SCRIPTS_TESTS_DIR) also fires `nudge-handoff-near-context-cap.sh`, which sources `_lib.sh` and `_config.sh`; that read is a class attribute, so `TestCrossDomainReadCompleteness` can't see it. Then add: the hook's read closure is `.sh` files plus config-keys.psv, and the hooks-dir `.sh` rule and the `CONFIG_KEYS_PSV` row both map to SCRIPTS_TESTS_DIR, so the test needs no exact-path constant, and a read of any other file type would need one.
  - select-tests.py is a global trigger even for a comment-only edit (row 64).
  - Rejected: a `TRANSCRIPT_HANDOFF_NUDGE_TEST_PATH` constant (rows 66–67).
  - Rejected: a `test_transcript_analysis*` name for the core file. Selection would then rest on a name coincidence, and the glob comment's "two siblings" would go stale.
- **M11: The `--no-redact` obligation.** `anchors: row19, row20, row21, row56`
  - The inline guard moves verbatim, and Verification 7's AST check confirms it did. The CLI-level test (legacy :11100) also moves verbatim.
  - Add one new class to the rearm file, `TestRearmBacktestReportNoRedactGuard`, mirroring legacy :11487:
    - Docstring: `"""_rearm_backtest_report refuses multi-root --no-redact itself, ahead of any output, independent of _resolve_cost_roots' CLI-level refusal."""`
    - One method, `test_no_redact_refused_by_report_itself_when_multi_root`.
    - It builds two `_write_cost_root` roots, each holding one priced session.
    - It calls `_mod.rearm_backtest._rearm_backtest_report(_rearm_backtest_args(no_redact=True), date(2026, 8, 2), roots=[root_a, root_b])`.
    - It asserts `SystemExit` with code 2, that stdout is empty (`capsys.readouterr().out == ""`), and that stderr contains `--no-redact`. Today the guard is the first exit in `_rearm_backtest_report`; the stderr assertion keeps a reordered or replaced exit path from satisfying the test. These assertions go beyond legacy :11487's.
  - It is a separate class so that `TestRearmBacktestReport`'s AST still equals its legacy slice.
  - This meets rearm-backtest's per-command obligation, read the way the cost-family phase read it.
  - spend-over-threshold reads no `no_redact`.
- **M12: conftest changes docstrings only.** `anchors: row15, row29`
  - Add the four new test files to the consumer list (:4–22). The helper module imports nothing from conftest, so it is not listed.
  - In `fake_projects` (:907–909), replace "spend-over-threshold and rearm-backtest stay in the shim (not yet moved into the package) and call config_dir() via their own separate import" with "handoff-signal-response stays in the shim (not yet moved into the package) and calls config_dir() via its own separate import". The rest of the docstring stays the same.
- **M13: Four bootstrap tests.** `anchors: G3, row32, row33, row57`
  - `rearm-backtest --help` asserts `--spacings`.
  - `spend-over-threshold --help` asserts `--since DATE`.
  - Two seeded runs with `_seed_priced_account` and `_isolated_config_env`:
    - `rearm-backtest`: exit 0 and `Sessions in scope: 1`.
    - `spend-over-threshold`: exit 0 and `2026-W21`.
  - The seeded runs' docstrings follow #1222 item 6 (row 33) and claim nothing more:
    - rearm: `"""Proves, in a fresh interpreter, that rearm_backtest.py resolves through sys.path[0] alone and that real argparse dispatches rearm-backtest to cmd_rearm_backtest through set_defaults(func=...). "Sessions in scope: 1" shows the seeded session was read and priced."""`
    - spend: the same shape, naming spend_over_threshold.py, spend-over-threshold, cmd_spend_over_threshold, and the 2026-W21 row.
  - If the precondition's `gh issue view 1222` shows item 6's text has changed, stop and report rather than adapt.
  - Never reuse the "no in-process test can see a broken re-export" boilerplate (row 33).
  - Verification 4(e) is the negative control.
- **M14: Docs and the package docstring.** `anchors: row14, row35`
  - `__init__.py`:
    - Add `rearm_backtest` and `spend_over_threshold` to the command-group list.
    - The shared group becomes "the shared ledger/gh-access/handoff-nudge modules (ledger_common, gh_cli, pr_cost_ledger, handoff_nudge)".
  - Architecture doc:
    - In :14–19, add the three modules to the list of modules the shim imports back into.
    - After :43, add: "`build_parser()` likewise wires up `rearm_backtest.py`'s `cmd_rearm_backtest` and `spend_over_threshold.py`'s `cmd_spend_over_threshold` from the shim. The still-unmigrated plan-boundary and handoff-signal-response code reads seven `handoff_nudge.py` names by name from the shim."
    - In the render section (:110–112), add `_is_fresh_user_prompt` after `_strip_task_notifications` in the helper list. After "Self-contained.", add: "`_is_fresh_user_prompt` is the one genuine-user-message predicate: the shim's judgment-pair, `review_rounds.py`'s round-window detection, and `rearm_backtest.py`'s boundary detection all read this copy."
    - In :213–215, change "the same re-expression pattern `review_rounds.py` uses for `_is_fresh_user_prompt` and `_SLASH_COMMAND_RE`" to "the same re-expression pattern `review_rounds.py` uses for `_SLASH_COMMAND_RE`".
    - After :355, add three sections: `### \`handoff_nudge.py\``, `### \`rearm_backtest.py\``, and `### \`spend_over_threshold.py\``.
      - Each covers the module's responsibilities, its module imports, and the names it exposes bare to the shim.
      - The core's section states that `_print_nudge_log_diagnostic` reads `config_dir() / ".handoff-nudge.log"` as `scope.config_dir()` by attribute, so `fake_projects`' existing patch isolates it.
      - The rearm section states that the multi-root `--no-redact` refusal stays inline in `_rearm_backtest_report`.
      - Name paths by code expression only, never `~/.claude/…`.
    - In :396–398, change "(for spend-over-threshold and rearm-backtest, not yet moved into the package)" to "(for handoff-signal-response, not yet moved into the package)".
    - In Tests, after :465, add a paragraph that names the four files and their seams, plus the helper module and why the legacy file imports it.
- **M15: Extend the prefix test, and teach it scope.py's lazy attribute.** `anchors: row34, row61, row63`
  - Add the three modules to `PRODUCTION_MODULES` and the four new test files to `TEST_FILES`.
  - Add `_lazy_module_attrs(tree)`, which finds the module-level `def __getattr__` and reads its first parameter's name. It returns every `str` `Constant` among the operands of each `!=` `Compare` in that function whose operands include that parameter's `Name`. `_top_level_names` adds that set. For scope.py today the set is `{"PROJECTS_DIR"}` (scope.py:49).
  - The derivation returns the string literals compared against `__getattr__`'s name parameter. Any other shape (a deny-list or positive `==` compare, set or dict membership, `startswith`, `match`, a vararg-only signature) yields no names, so the guard fails loudly on a read it cannot explain.
  - Only the allow-list form (`if name != "X": raise AttributeError`), which is scope.py's shape, derives names. A deny-list compare (`==`) adds nothing, so the guard cannot accept a name the module does not provide. The helper's docstring states this assumption. A `!=` compare that guards a `return` instead of a `raise` would still over-derive; the docstring's assumption covers that residual limit.
  - Two tests pin the derivation against over-accept. One reads scope.py from disk and asserts its derived set equals exactly `{"PROJECTS_DIR"}`. The other parses a synthetic deny-list-shaped `__getattr__` source and asserts it adds no name.
  - Add one clause to `_top_level_names`' docstring: "plus each name a module-level PEP 562 `__getattr__` resolves (the string literals it compares its own parameter against)". The new helper gets a summary line plus a short paragraph stating the allow-list assumption. The module docstring stays as it is.
  - Nothing in scope.py or other production code changes.
  - Rejected: leaving rearm_backtest.py out of `PRODUCTION_MODULES`. It carries the most M4 renames of the three, which is the exact typo class the guard exists for.
  - Rejected: a hardcoded `{"scope": {"PROJECTS_DIR"}}` allowance in the test. It would restate scope.py's knowledge in a second place. Deriving it from scope.py's own AST keeps scope.py the single source. The exact-set test asserts the derived set, so it fails loudly if scope.py changes; it is not a hardcoded allowance.
  - Rejected: a top-level `PROJECTS_DIR` binding in scope.py. It would defeat the lazy resolution that scope.py's `__getattr__` exists for.
- **M16: One `code-writer` dispatch in two internal stages.** `anchors: row28`
  - Rejected: sequenced dispatches. Both stages edit the shim and the legacy file.
- **M17: One positive diagnostic-footer test.** `anchors: row15, row16, row62`
  - Add `TestSpendOverThresholdDiagnosticFooter` to the spend file, with one method, `test_footer_counts_schema_drift_lines_in_the_scope_config_dir_log(self, fake_projects, tmp_path, capsys)`.
  - It writes one priced claude-sonnet-5 session above threshold under `fake_projects`, as legacy :4602–4604 does.
  - It writes `tmp_path / ".handoff-nudge.log"` with:
    - three `schema-drift session=<id> event=Stop` lines, the shape legacy :10943 uses;
    - one `nudged session=<id> est=100000 model=claude-sonnet-5 window=1000000 event=Stop` line, which must not count.
  - It calls `_mod.spend_over_threshold.cmd_spend_over_threshold(_spend_over_threshold_args())`.
  - It asserts that stdout contains `f"Diagnostic: 3 schema-drift line(s) in {tmp_path / '.handoff-nudge.log'}"`.
  - Docstring: `"""The diagnostic footer counts the schema-drift lines in scope.config_dir()'s .handoff-nudge.log and names that path. fake_projects' scope.config_dir patch reaches the footer only while handoff_nudge.py reads config_dir by attribute. The test discriminates only because the autouse _isolate_transcript_corpus_lookups fixture pins CLAUDE_CONFIG_DIR away from fake_projects' tmp_path, so a by-name config_dir() reads a directory with no log."""`
  - The docstring carries that fixture clause rather than the two-directory shape at test_transcript_ledger_common.py:131–150, which is a larger change. `[verified: Read of both; the autouse fixture is at conftest.py:993–1019]`
  - It is a separate class so `TestSpendOverThreshold`'s AST still equals its legacy slice. Verification 7 excludes it by name.
  - Verification 4(d) is its negative control.

**Assumptions:**

1. `[engineer-verified: "Architect is correct"]`, answering the architect's pick of handoff-nudge A as the next phase.
2. `[engineer-verified: "No as far as I know"]`, answering whether anyone is working on #1175 on any machine. The tag covers only what the engineer knows. The precondition scan still runs.
3. `[engineer-verified: "That can be done later"]`, answering who implements #1222 and when. It covers deferring #1222 only. It does not say A may run alongside an open #1222 PR, which is why the precondition blocks on one. Landing #1222 before A's step-0 baseline was the architect's earlier recommendation, not the engineer's.
4. Spans. `[verified: Read; top-level grep at d603ae99; git diff 72830ca2 d603ae99 -U0 -- claude/.claude/scripts/transcript-analysis.py shows three hunks, all outside A's spans]`
   - Spend: banner :3300, `cmd_spend_over_threshold` :3303–3385, `_NUDGE_LOG_MAX_READ` :3388, `_read_bounded_log_lines` :3391–3405, `_print_nudge_log_diagnostic` :3408–3424, turn-shape banner :3427.
   - Rearm: banner :4156–4158, constants :4160–4206, functions :4209–5061, plan-boundary banner :5064.
   - Every A node and both banners sit exactly 9 lines below their 72830ca2 positions, a uniform shift. The spans are contiguous, and their bordering blank lines are unchanged.
   - The shim diff's three hunks (+1 at base :232, +8 at :1113, +17 at :6817) all fall outside A's spans, so the spans are byte-identical to 72830ca2. `[verified: round-2 platform and backend reviewers, from that diff]`
   - Verification 7 still compares against step 0's copies at d603ae99.
5. 25 top-level names move. `[verified: top-level grep at d603ae99; the same shim diff shows no hunk in A's spans, and both round-2 reviewers' AST checks found 25 nodes wholly inside them]`
   - 16 to core: `_NUDGE_LOG_MAX_READ`, `_read_bounded_log_lines`, `_print_nudge_log_diagnostic`, `_HANDOFF_NUDGE_ABS_CAP`, `_HANDOFF_NUDGE_PCT_THRESHOLD`, `_RAMP_CURVE_TURN_INDEX_BUCKETS`, `_RAMP_CURVE_TURN_INDEX_OVERFLOW_LABEL`, `_RAMP_CURVE_BUCKET_LABELS`, `_NUDGE_LOG_LINE_KINDS`, `_ramp_curve_turn_index_bucket`, `_hook_effective_fire_threshold`, `_extract_rearm_session_turns`, `_ramp_curve_from_corpus`, `_parse_nudge_log_entries`, `_operator_response_lag_from_log`, `_session_matches_rearm_scope`.
   - 8 to rearm: `_REARM_BACKTEST_DEFAULT_SPACINGS`, `_hook_observable_boundaries`, `_nudge_conversion_from_log`, `_simulate_rearm_spacing`, `_parse_rearm_spacings_arg`, `_rearm_backtest_log_size_lines`, `cmd_rearm_backtest`, `_rearm_backtest_report`.
   - 1 to spend: `cmd_spend_over_threshold`.
6. Outside the spans, the shim's code reads moved names only in these places. #1009 added no reader. `[verified: grep of each name at d603ae99]`
   - `set_defaults` at :6790 and :6924.
   - plan-boundary at :5133, :5213, :5214, :5225, and :5227.
   - handoff-signal-response at :5662, :5971, and :5972.
   - No file outside the shim and the legacy test file reads any of the 25 names in code. cost.py and test_transcript_workstream_cost.py name them only in docstrings.
7. Readers inside A. `[verified: grep at d603ae99]`
   - Rearm is the only code reader of these: `_hook_observable_boundaries` (:4903), `_nudge_conversion_from_log` (:5014), `_simulate_rearm_spacing` (:4998), `_parse_rearm_spacings_arg` (:4866), `_rearm_backtest_log_size_lines` (:4941), and `_REARM_BACKTEST_DEFAULT_SPACINGS` (:4736).
   - Spend is the only caller of `_print_nudge_log_diagnostic` (:3363, :3385).
   - `_NUDGE_LOG_MAX_READ` is read at :3400–3401 and at :4804 and :4821.
8. Estimated sizes: core about 425 lines, rearm about 560, spend about 100. Everything in one module would be about 1,060. `[verified: line arithmetic over row 4, whose span lengths are unchanged; header size estimated from workstream_cost.py:1–16]`
9. The three modules depend on exactly the M4 lists.
   - At 72830ca2 this was `[verified: Read of every span against the shim's import blocks, plus the round-1 backend reviewer's symtable extraction]`.
   - At d603ae99 the spans are byte-identical apart from a uniform shift (row 4). `[verified: the shim diff cited in row 4, plus an independent symtable free-name extraction by each round-2 platform and backend reviewer that reproduced M4's lists and rename map]` Verification 7's import-set check re-proves it.
10. No import cycle. `[verified: import grep at d603ae99]`
    - render.py imports no package module (render.py:7–10).
    - Among package modules, pricing and scope import only corpus (pricing.py:16; scope.py:34–39), and corpus imports none (corpus.py:6–10).
    - No package module loads the shim; a grep for `spec_from_file_location` under `transcript_analysis/` finds nothing.
11. `_is_fresh_user_prompt`. `[verified: Read; grep at d603ae99]`
    - The shim's copy is at :292–316. judgment-pair reads it at :961 and :993. A reads it at :4268. Legacy `TestIsFreshUserPrompt` (:6960) reads it at :6963–7002 as `_mod._is_fresh_user_prompt`.
    - review_rounds.py:45–64 mirrors it. The two bodies differ only in reading the shim's by-name `_content_text` versus `render._content_text`, which are the same function. The docstrings differ, and the shim's is the longer one.
    - review_rounds' copy has one reader, review_rounds.py:152. No other package module reads either copy.
12. #1175 prescribes promoting `_is_fresh_user_prompt` into render.py and deleting the review_rounds copy. `[unverified — relayed from Step 3; I can't read issues]`
13. #1009 merged into main as d603ae99, the base this plan is measured at. `[engineer-verified: "1009 just landed"]`, stated by the engineer this round. `[verified: gitStatus recent commits]` Its file list is 13 files `[verified: round-2 platform reviewer's git diff --stat 72830ca2 d603ae99]`: review_rounds.py, the shim, the legacy test file, test_transcript_review_rounds.py, corpus.py, scope.py, claude/.claude/tests/helpers.py, docs/transcript-analysis.md, .claude/plans/review-round-cost-pooled.md, .claude/skills/code-review-claude-config/SKILL.md, CHANGELOG.md, claude-skills/skills/tests/test_skills.py, and docs/private-project-redaction.md. None of the last five affects A.
14. The architecture doc names `_is_fresh_user_prompt` only at :215, inside the :213–215 sentence that lists review_rounds' re-expressions. Its render section (:108–112) lists render's helpers. Its review_rounds section (:139–161) doesn't name the predicate. `[verified: grep; Read at d603ae99]`
15. `config_dir` in the shim. `[verified: grep; Read; conftest :918–923 at d603ae99]`
    - The shim's only bare readers are :3415 (A) and :5971 (B).
    - `scope.config_dir` and the shim's `config_dir` are the same `_config_dir.config_dir` object. `main()` (:7159) reassigns neither; it reassigns only `scope.PROJECTS_DIR` (:7195).
    - `fake_projects` patches both (conftest :919–920).
16. scope.py calls `config_dir()` only at :70 and :727.
    - :70 is the lazy `PROJECTS_DIR`, which is bypassed once `fake_projects` sets it.
    - :727 is `_resolve_cost_roots`, which spend never calls.
    - `resolve_scan_roots` (:466–500) reaches `_projects_dir()` and `declared_transcript_roots()`, not `config_dir()`.
    - So in the spend test, the retargeted :4609 patch raises only inside the diagnostic's own `try`.

    `[verified: Read scope.py:42–79, :466–500, :689–774; grep at d603ae99]`
17. rearm-backtest never calls `config_dir()` itself; see the comment at :4921–4922. It reaches `scope.config_dir()` only through `_resolve_cost_roots`. `[verified: Read]`
18. Under Stage 1, legacy :4609 still passes. Its patch lands on the shim's binding, while the moved diagnostic reads `scope.config_dir()`, which `fake_projects` points at a `tmp_path` with no log. `[unverified — inferred from G2 and row 16; the round-1 SDET traced the same result; Verification 4(b) is its control]`
19. The `--no-redact` guards. `[verified: grep for refused_by tests; Read :11100–11112 at d603ae99]`
    - The inline guard is at :4855–4861.
    - Legacy :11100 reaches scope.py:766–772 through `cmd_rearm_backtest`, not the inline guard.
    - No test calls `_rearm_backtest_report` with multiple roots and `no_redact`.
    - Direct-call precedents exist: test_transcript_cost.py:1242, legacy :11487, and test_transcript_cache_rebuild.py:496.
    - Spend reads no `no_redact`.
20. The governing plan asks later phases to land a multi-root `--no-redact` refusal test "before splitting those `cmd_*` bodies, so a dropped guard fails CI" (transcript-analysis-decomposition.md:298–303). The cost-family phase read this per command: "only the cost site's obligation falls due this phase" (transcript-analysis-phase2-cost-family.md:277–279). `[verified: Read]`
21. At multi-root scope, rearm prints only a pooled size and no path, whatever `redact` is (:4800–4812). The guard exists for CLI parity (help text :6909–6918). A dropped guard would leak no raw path, so one direct-call test is proportionate. `[verified: Read]`
22. Only `TestParseNudgeLogEntriesRealHookLineContract` reads a hook by path, at class level (`_NUDGE_HOOK = HOOKS_DIR / …`, :9966). No A test reads a SKILL.md. `[verified: grep]`
23. select-tests maps hooks and skills changes to `TRANSCRIPT_ANALYSIS_TEST_GLOB` plus `TRANSCRIPT_DENIALS_TEST_PATH` (:100–108, :667). The denials constant is the precedent for a hook reader the glob doesn't match. `[verified: Read at d603ae99]`
24. `TestCrossDomainReadCompleteness` resolves only module-level, single-`Name` `Assign` targets and never descends into a `ClassDef` (test_select_tests.py:116–139). It would not flag the moved class. `[verified: Read; grep at d603ae99]`
25. test_select_tests.py lists `TRANSCRIPT_DENIALS_TEST_PATH` at :428, :440, :498, :817, :1268, :1428, :1462, :1477, :1893, and :2487. `[verified: grep at d603ae99]` (no longer load-bearing; see row 67)
26. `_mod.<A name>` reads. `[verified: grep at d603ae99]`
    - The legacy file holds 112 such lines (113 reads, since :9686 holds two).
    - :7952 and :11249 stay. The other 110 lines move, including :9686 inside `_ramp_curve_from_records`.
    - The moving slices also read names the shim keeps:
      - `_mod._model_rates` (:4499, :4547, :9770, :10716);
      - `_mod._parse_ts` (:10433, :10438);
      - `_mod._cost_report` (:10610, :10803);
      - `_mod._redaction_ordinals` (:10946).
    - No moving slice reads `_mod.scope.<name>`.
27. Patch sites in the slices. `[verified: grep of setattr at d603ae99]`
    - Retarget: :4609 and :10924.
    - Leave unchanged: :10655 and :11107 (`_mod.scope`), and :10885 (`Path.exists`).
28. Under Stage 1's temporary re-export, exactly one legacy test fails: `TestRearmBacktestReport::test_truncation_can_drop_an_early_block_line_causing_real_misclassification` (:10901). Its `_NUDGE_LOG_MAX_READ` patch (:10924) lands on the shim, while the moved readers read the core's global. `[unverified — inferred from G2; the round-1 SDET and backend reviewer traced the same result; Stage 1 runs it]`
    - This holds only if Stage 1 also adds the three `###` sections, because the drift test requires a heading for every module file on disk (test_transcript_analysis_architecture_doc.py:35–49). `[verified: Read at 72830ca2]`
    - M5's move adds no failure: no test patches `_is_fresh_user_prompt`, and the shim's by-name import keeps `_mod._is_fresh_user_prompt` bound (rows 51, 52).
29. Where each helper is used. `[verified: grep at d603ae99]`
    - `_spend_over_threshold_args` (:4457): the spend tests and :7952.
    - `_ramp_curve_from_records` (:9682): :9769, :9786, :9797, :11606, :11709, :11710.
    - `_rearm_backtest_args` (:9653): `TestRearmBacktestReport` only.
    - `_tool_use_asst` (:9674): `TestHookObservableBoundaries` only.
    - `_priced_sidechain_asst` (conftest :536): :11184 is the legacy file's only use.
30. `_pr_cost_helpers.py:8` imports package modules directly and reads `<module>.<name>` (:17, :202). `[verified: Read at 72830ca2]`
31. Test classes by start line. `[verified: class-def grep at d603ae99]`
    - Core: `TestHookEffectiveFireThreshold` :9689, `TestRampCurveFromCorpus` :9743, `TestParseNudgeLogEntries` :9867, `TestParseNudgeLogEntriesRealHookLineContract` :9959, `TestOperatorResponseLagFromLog` :10030, `TestSessionMatchesRearmScope` :10427, `TestExtractRearmSessionTurnsModelAndPosition` :11156.
    - Rearm: `TestHookObservableBoundaries` :9703, `TestSimulateRearmSpacing` :9802, `TestParseRearmSpacingsArg` :10397, `TestRearmBacktestReport` :10595 (about 554 lines).
    - Nudge-log: `TestNudgeConversionFromLog` :10113, `TestRearmBacktestLogSizeLines` :10461.
    - Spend: `TestSpendOverThreshold` :4461.
    - Estimated file sizes: core about 420, rearm about 780, nudge-log about 445, spend about 205 with M17.
32. No bootstrap test covers either command. These are reusable: `_run`, `_isolated_config_env` (:201–213), and `_seed_priced_account` (:173–198; claude-sonnet-5, 1M input, 2026-05-19). `--projects` defaults to `"*"` (shim :6014). `[verified: Read at d603ae99]`
33. #1222. `[verified: the round-2 platform reviewer read gh issue view 1222 as open, found item 6's text matching the quote below, and found no open PR title or body naming 1222 or 1175; the precondition re-reads it]`
    - #1222 is an open issue with no PR. It touches conftest.py, test_transcript_cli_bootstrap.py, select-tests.py, test_select_tests.py, and the prefix test.
    - Item 6 reads: "Reword each site to what the subprocess run uniquely proves: the module resolves in a fresh interpreter through `sys.path[0]` alone, and real argparse dispatches to the command through `set_defaults(func=...)`."
    - The current boilerplate's "no in-process … test can see a broken re-export" is inaccurate. A broken by-name import fails the shim's `exec_module`, and with it every in-process test. `[verified: Read bootstrap :1–11, :216–218]`
    - The seeded runs add real `build_parser()` dispatch for these two commands, which no in-process test exercises. The existing `--help` tests already prove `sys.path[0]` resolution. `[unverified — relayed from the round-1 SDET]`
34. The prefix test's scope is its two tuples, `PRODUCTION_MODULES` and `TEST_FILES` (:20–29). Its production check and its test-file check both resolve names through `_top_level_names` (:52–69). `[verified: Read at d603ae99]`
35. The architecture doc's edit sites are listed in M14, and every M14 site is unchanged since 72830ca2. The drift test needs one heading per module. `[verified: Read of the doc at d603ae99]`
36. No repo-local line-limit check exists at d603ae99. `[verified: grep across *.py, *.sh, *.toml, *.yml for ceiling identifiers returns nothing]`
37. CHANGELOG.md has no decomposition-phase entry, so by precedent this phase adds none. `[verified: grep at d603ae99]`
38. These pointers name symbols, not files, and stay accurate:
    - docs/transcript-analysis.md :1132, :1511, :1533, :1552, :1591;
    - docs/handoff-nudge.md :118;
    - cost.py :184 and :1169;
    - test_transcript_workstream_cost.py:109.

    scope.py:684 names the command. `[verified: grep; Read at d603ae99]`
39. In-span prose pointers. `[verified: Read at d603ae99]`
    - "in this file" at :4279 and :4876–4877 was already stale.
    - "above" at :3412–3413 stays true under M3.
    - At :4243–4244, "see its own docstring" resolves to render.py's copy, which keeps the shim's full docstring (M5).
40. No .sh or non-test .py invokes either command, so no script reads their stderr. scope.py names rearm-backtest only in a tuple of subcommand names. `[verified: grep over *.sh, *.py, *.md at d603ae99]`
    - Two scripts do invoke the shim for other subcommands, so a non-folded layout without a re-run of `./install.sh` would break them at the shim's import:
      - `claude/.claude/hooks/nudge-error-mode-analysis.sh:151` runs `friction-count`. It fails open, so the nudge would silently stop firing.
      - `claude/.claude/scripts/pr-cost-section.sh:38` runs `cost`. It prints a re-run message.
    - `[verified: Read of both lines]` The breakage itself is conditional on that layout and unverified.
41. Clock reads. `[verified: grep; Read at d603ae99]`
    - Rearm reads the date once (:4834) and prints `generated <today>` in two titles (:4962, :5019).
    - Spend prints no date.
    - The package's other clock reads are scope.py:916 and render.py:241. scope.py:916 is in `_parse_since_nd_arg`, which runs only for `--since Nd`, and no rearm capture passes that. render.py:241 is in audit-routing-samples' markdown, which A never reaches.
42. No new basename collides with an existing file. `[verified: Glob at d603ae99]`
43. `handoff_nudge` is also a config-key name. A repo-wide grep at 72830ca2 found it only in config tests, the hook, and config-keys.psv. At d603ae99, a grep of `claude/.claude/scripts/` finds only config tests. `[verified: grep]` That no test scans scripts/ for config-key tokens is `[unverified — inferred from those greps]`.
44. Line numbers are at d603ae99. `[verified: gitStatus]`
45. Rollback is `git revert` of this phase's squash-merge commit on main. That revert also restores review_rounds.py's copy. `[verified: gh api repos shows allow_squash_merge true, merge/rebase false; last 12 main commits single-parent]` The revert itself is not rehearsed, since row 54 waives the rehearsal.
    - The revert stays valid only while no later commit on main touches a file in this PR's file list, created files included. After that, fix forward.
    - A later file that imports a created module or `_handoff_nudge_helpers`, or reads `_mod.handoff_nudge.<name>`, also invalidates a clean revert. It touches no file in this PR's list, so the revert applies textually and leaves that file failing at collection.
    - Check with `git grep` for the three module names, `_handoff_nudge_helpers`, and the 26 moved names. It must find no reader outside this PR's files.
    - The revert PR's full-suite CI is the check.
    - #1222, which rebases onto this phase and edits four of its files, is the likeliest such commit.
46. The new test files import conftest builders that earlier phases promoted, including `_priced_sidechain_asst` (#1214; conftest :536). Cross-phase reverts therefore run last-in-first-out. `[verified: conftest :536; decompose-transcript-dispatch.md M9]` The other PR attributions are `[unverified]`.
47. Four rules carried over from the dispatch phase.
    - ruff selects `E`, `F`, `B`, `I`, `UP`, and `SIM`, with line length 130. `[verified: pyproject.toml :2, :6]`
    - `git ls-files` omits untracked files. `[unverified — relayed from decompose-transcript-dispatch.md]`
    - `require-stow-reminder.sh` needs the PR body to contain `install.sh` or `stow`, case-insensitive, whenever a file is added under `claude/.claude/`. `[verified: Read require-stow-reminder.sh :4–17, :29–37, :220–221]`
      - Its header (:29–37) says every nested new file needs a re-stow. That doesn't hold on a folded layout, where `~/.claude/scripts` is a directory symlink into the checkout. `[verified for this machine: round-2 platform reviewer's readlink of ~/.claude/scripts, plus the install.sh :47–51 comment on why stow folds scripts/. Other consumers' layouts stay unverified.]`
      - stow-packages.sh lists two package rows and says nothing about folding. `[verified: Read]`
    - `<venv>` is `../../../.venv`. `[unverified — relayed]`
48. D1: `[engineer-verified: "Don’t create tech debt. Put code in one place and coordinate with the session responsible for 1009"]`, answering which copy of `_is_fresh_user_prompt` rearm should read. The engineer picked neither listed option. The tag covers one home for the code and coordination with #1009's session. It names no module and no landing order.
49. Row 48 means: the shim's copy moves into render.py, review_rounds.py's copy is deleted, and every reader reaches render's. `[unverified — the parent's reading, which this plan adopts; it matches #1175's relayed text (row 12)]`
50. render.py is the right home. `[verified: Read; import grep at d603ae99]`
    - The predicate reads only `_content_text`, which render.py owns (render.py:27–32).
    - render.py imports no package module, so it stays a leaf (row 10).
    - Every reader already imports render: the shim (:51, :178–187), review_rounds (review_rounds.py:35), and rearm_backtest (M4, for `_pct_of`).
    - corpus.py imports no package module today and would gain its first.
51. Moving the predicate changes no behavior. `[verified: Read; grep]` Verification 7 re-checks it mechanically.
    - The shim's and review_rounds' bodies differ only in two names for one function (row 11).
    - Inside render.py, the shim's bare `_content_text` resolves to that same function.
    - No code or test patches `_content_text` or `_is_fresh_user_prompt`.
52. Tests that reach the predicate. `[verified: grep; Read select-tests.py:76–86, :669–670 at d603ae99]`
    - Legacy `TestIsFreshUserPrompt` reads `_mod._is_fresh_user_prompt`, which the shim's by-name import from render keeps bound. This matches the architecture doc's statement that render.py is exercised through the shim's suite (:367–369).
    - No test reads `review_rounds._is_fresh_user_prompt`. test_transcript_review_rounds.py:189 covers review_rounds.py:152's call through `compute_review_round_costs`. Its docstring (:190–192) names the predicate without a module.
    - select-tests adds the review_bench tests and test_measure_subagent_model_resolution.py for any render.py or review_rounds.py change. render.py gains no import, so `_REVIEW_BENCH_SCRIPTS_DEPENDENCIES` stays correct.
53. The #1009 session's reply, relayed by the parent: it did not edit `_is_fresh_user_prompt` or review_rounds.py's duplicate, and the line numbers shifted. `[unverified — relayed by the parent]`
    - The re-measure agrees. Both copies keep row 11's shape at their new lines, and their callers changed only position. `[verified: Read]`
    - The landing order is settled: #1009 landed first, this phase is based on it, and #1009 needs no rebase.
54. D2: `[engineer-verified: "Waive, state rollback (Recommended)"]`, answering whether this phase waives the governing plan's post-merge revert rehearsal (transcript-analysis-decomposition.md:276–279). It covers this phase only. Row 45's rollback statement replaces the rehearsal.
55. Ask provenance: `[engineer-verified: "Yes, I said that"]`, answering whether the Ask line's first quote is theirs. The quote came from the earlier session's handoff file, not from this session's messages. It covers that one quote only.
56. Scope: `[engineer-verified: "New --no-redact test"]`, selected when asked which items beyond a verbatim move to keep (M11).
57. Scope: `[engineer-verified: "Four bootstrap tests"]`, selected in the same question (M13).
58. Scope: `[engineer-verified: "select-tests.py changes"]`, selected in the same question (M10); narrowed by rows 66–67.
59. Scope: `[engineer-verified: "render.py + review_rounds.py"]`, selected in the same question (M5). It covers keeping those two file edits in this phase. Row 49 stays the parent's reading of the "one home" answer.
60. After the delete, F401 flags `HOOKS_DIR` and `_priced_sidechain_asst` in the legacy file, and not `errno`. `[verified: grep at d603ae99]`
    - The `errno` import (:3) predates #1009. What is new since 72830ca2 is its readers at :8918–8996, inside `TestScanGapCounter` (:8484), outside every slice. Those come from #1009.
    - `HOOKS_DIR` is read only at :9966, and `_priced_sidechain_asst` only at :11184, both inside slices.
    - This supersedes the round-1 SDET's `errno` note, which held at 72830ca2.
61. The prefix guard misreads scope.py's lazy attribute. `[verified: Read; grep at d603ae99]`
    - `_rearm_backtest_report` reads `scope.PROJECTS_DIR` (shim :4852), and that read moves verbatim.
    - scope.py provides `PROJECTS_DIR` only through two mechanisms, and no top-level statement binds it:
      - its module-level `__getattr__` (:42–51), whose body compares its parameter against the single literal `"PROJECTS_DIR"` (:49);
      - `_projects_dir`'s `globals()` cache (:68–79).
    - `_top_level_names` collects only `tree.body` defs, assignments, and imports, so the production check would fail on rearm_backtest.py.
    - M15's derivation returns the string literals compared against `__getattr__`'s name parameter, and any other shape yields nothing. Only `!=` compares count. Deny-list (`==`) and positive-`==` (`if name == "X": return ...`) shapes add nothing. Scope.py's `!=` compare is the allow-list shape, so it derives exactly `{"PROJECTS_DIR"}` `[verified: staged helper and tests/test_transcript_package_module_prefixes.py::test_scope_lazy_module_attrs_are_exactly_projects_dir and ::test_lazy_module_attrs_ignores_deny_list_shaped_getattr, green in the post-fix full-suite run]`.
    - scope.py's is the package's only `__getattr__`; the other one under `scripts/` is analyze-context.py:38, outside the package. #1009's scope.py edits added no other lazily provided attribute.
    - Every other sibling attribute in M4's map is a top-level binding at d603ae99:
      - scope.py: the `config_dir` import (:28–33), :271, :466, :503, :658, :669, :689, :898, :903;
      - corpus.py: :214;
      - pricing.py: :131, :223, :511;
      - render.py: :39.

      `render._is_fresh_user_prompt` becomes one under M5.
62. Footer test: `[engineer-verified: "2 yes add the positive footer test"]`, answering the parent's proposal to add a positive diagnostic-footer spend test as a standing control for M6's `scope.config_dir()` read (M17). The proposal used `fake_projects` plus a `.handoff-nudge.log` of N schema-drift lines, and asserted the footer's count and path. That wording is the parent's; the engineer's words are only the quote.
63. This revision: `[engineer-verified: "Yes proceed"]`, answering whether to revise the plan with the round-1 review findings and the M15 fix the architect recommended. It covers making the revision. Each finding's resolution here is this plan's, not the engineer's.
64. Step 1 can't run scoped. `[verified: Read; grep at d603ae99]`
    - select-tests.py is in `GLOBAL_TRIGGER_PATHS` (select-tests.py:366–370). Any diff that edits it selects the full suite plus the outside-root targets the other changed paths add, with reason `global-trigger` (:771–775).
    - select-tests passes its own arguments through to pytest (:1055, :927–933). pyproject's addopts sets `-n auto` (:25).
    - CI runs `-m "not timing"` in parallel, then `-m timing -n0` serially, over claude/.claude/, claude-skills/, and plugins/. It never runs evals/ (tests.yml :159, :166, :170).
65. Open PRs #744 and #718 edit select-tests.py and test_select_tests.py. Neither names 1175, 1222, or handoff-nudge. `[verified: gh pr diff 744 and 718 against M10's three current select-tests.py regions (:100–106, :546–548, :604–607 at d603ae99); the precondition's gh pr list re-reads it]` Both PRs' diffs are indexed against an older select-tests.py blob (93d94dce6, not d603ae99), so their base-side hunk ranges are not d603ae99 line numbers. In that blob M10's three regions sit at :51–54, :379–380, and :419–421, and the PRs' select-tests.py hunks are #744 :123–129 and #718 :29–36 and :351–357. In d603ae99 terms, matching each hunk by its context, those are the `CLAUDE_SETTINGS_JSON` line (:256), the `SKILLS_TESTS_DIR` constants (:38) and the `DOMAIN_RULES` rows after `SCRIPTS_DIR` (:518). Result: disjoint in both numberings. This phase no longer edits test_select_tests.py, so that file is not compared. Under the overlap rule they are recorded, not blocking.
66. Delegation on M10's scope: `[engineer-verified: "Do what is correct here, don’t anchor on what I say just because I said it. What does the architect think"]`, said when the `TRANSCRIPT_HANDOFF_NUDGE_TEST_PATH` constant was raised. The engineer's words are only the quote.
67. The architect's advice on that question, and the resulting scope: drop the constant. The hooks-dir `.sh` rule already selects the new file when the hook changes, and after the move the glob no longer reads any hook. M10 is now comment corrections only, and the ten-site test_select_tests.py edit (row 25) is dropped. The architect's advice is its own. The handoff-nudge hook's read closure is `.sh` files plus config-keys.psv, and both already map to SCRIPTS_TESTS_DIR (select-tests.py's hooks-dir `.sh` rule and `CONFIG_KEYS_PSV` row); a read of any other file type would need a constant. `[verified: architect consult return and Read of test_transcript_handoff_nudge.py:204, nudge-handoff-near-context-cap.sh:84, _lib.sh, _config.sh:31, select-tests.py rules at the hooks-dir .sh predicate and the CONFIG_KEYS_PSV row; grep of test_transcript_analysis.py, test_transcript_analysis_architecture_doc.py, and test_transcript_analysis_cost_import_direction.py for HOOKS_DIR and hooks/ finds no hook read, only string fixtures at test_transcript_analysis.py :10093–:10616]`
68. M11 and M15 additions: `[engineer-verified: "I agree with the architect"]`, said after the architect's consult on them. The engineer's words are only the quote.
69. Provenance of those additions. `[verified: architect consult return and this session's staff-sdet review findings; Read of test_transcript_rearm_backtest.py:755-773, test_transcript_package_module_prefixes.py, test_transcript_cost.py:1242, test_transcript_read_scope.py:236, test_transcript_cache_rebuild.py:496, and legacy test_transcript_analysis.py :3392, :4330, :11487 at d603ae99]`
    - The consult returned keep-both: (a) M11's added stderr assertion and (b) M15's `!=`-only derivation plus its two over-accept tests. Both stay in this phase.
    - Both additions came from staff-sdet during code review, not from the engineer.
    - The helper `_lazy_module_attrs` is new in this phase, so pinning its allow-list assumption is part of making it correct.
    - Sibling `*_report_itself` tests assert less. They are left for the out-of-scope parametrized test.

**Plan-review should re-check:**
- M15's derivation and its stated limits (row 61), plus Stage 2's pre-derivation control;
- M17 and Verification 4(d);
- the precondition's non-blocking disposition for overlapping PRs that don't match by name;
- row 60's supersession of the `errno` note;
- rows 49–51;
- M3's placement of `_print_nudge_log_diagnostic`;
- M11's new class;
- row 28's single expected Stage 1 failure.

## Critical files

**Precondition.** Before Verification step 0, the parent runs these read-only commands:
- `gh issue view 1175 --json state`
- `gh issue view 1222 --json state,body`
- `gh pr list --state open --limit 200 --json number,title,headRefName,body,files`
- `git worktree list --porcelain`
- `git branch --all --list '*1175*' '*1222*' '*handoff*'`
- `git ls-remote --heads origin`

The scan follows these rules:
- **Critical files** are the paths listed under Create and Modify below, plus `claude/.claude/hooks/nudge-handoff-near-context-cap.sh`. The moved hook-contract test fires that hook (row 22), and handoff_nudge.py's threshold constants mirror it. Paths under Explicitly unchanged and Reuse are not Critical files.
- **Match rule.** A PR, branch, or worktree matches when its title, body, or ref names 1175, 1222, `handoff-nudge`, or `handoff_nudge`, case-insensitively. This phase's own branch and worktree are excluded.
- **Overlap rule.** Every open PR, whether it matches or not, overlaps when its `files` list includes a Critical file. File-level overlap counts even when the line ranges are disjoint. Treat a file list of 100 or more entries as possibly truncated, and re-read it with `gh api repos/{owner}/{repo}/pulls/<n>/files --paginate`. `[unverified: gh's per-PR file cap]`
- **Record.** Record every PR, branch, and worktree inspected. Mark each as match or no-match and as overlap or no-overlap, and list each overlapping PR's overlapping files. Put the record in the dispatch prompt, never in this plan file.
- **Dispatch condition.**
  - A matching PR that overlaps blocks dispatch until the engineer picks the landing order. An open #1222 PR overlaps by construction.
  - A matching branch or worktree with no PR blocks dispatch until the engineer decides.
  - A match without overlap is record-only. Four open PRs match by name today without overlapping `[unverified — round-2 platform reviewer's scan; the precondition's gh pr list re-reads it]`.
  - A non-matching PR that overlaps does not block. Its disposition is that whichever of the two lands second rebases, and this phase's PR body names it with its overlapping files. #744 and #718 are expected here (row 65).
  - Reason: git surfaces any textual conflict at that rebase. The semantic backstop is the second lander's full-suite CI plus the sync rules below.
  - For each overlapping non-blocking PR (#744, #718), record in the dispatch record that its select-tests.py hunks are disjoint from M10's three regions (:100–106, :546–548, :604–607 at d603ae99), with the hunk ranges compared. Map the PR diff's base-side ranges onto the dispatch base first, since a PR's diff can be indexed against an older blob (row 65).
  - A closed #1175 or #1222 does not bypass an open matching PR.
  - #1009 has merged (row 13) and no longer gates dispatch.
- **Known blind spot.** The scan can't see unpushed work on another machine (row 2).

Sync rules:
- Whenever `origin/main` has moved past d603ae99, sync with `git-feature-branch-sync` and key the follow-up on what the new commits touch.
  - If they touch any file under `claude/.claude/scripts/`, `claude/.claude/tests/`, or `docs/transcript-analysis*.md`:
    - Redo step 0, or re-capture the Verification 3 baseline from the new `origin/main` in a scratch checkout with the same account path and the same capture script.
    - Re-run Verifications 3 and 7, the row checks below, and Verifications 1 and 5.
    - After step 0 has run, first run the abort procedure, then sync, redo step 0, and start a fresh dispatch.
  - If they touch none of those, sync, re-run the row checks, and re-run Verifications 1 and 5.
  - The row checks are:
    - Re-locate every span by its first and last symbol, including render.py's insertion point and review_rounds.py's predicate and caller.
    - Re-run the check behind every ledger row tagged `[verified: …]` that cites a grep, a Glob, or a line number.
    - Correct each row whose check changed before dispatching.
- Immediately before opening the PR, re-check `origin/main`. If new commits touch a Critical file, stop and report to the engineer. Otherwise, apply the rule above.
- If the synced base carries a line-limit check, its ceiling rows must change. Name that file to the engineer before editing it.

If no #1222 PR exists at dispatch, the PR body states that #1222 rebases onto this phase. It lists this phase's edits to conftest.py, test_transcript_cli_bootstrap.py, select-tests.py (comments only), and the prefix test, which gains `test_scope_lazy_module_attrs_are_exactly_projects_dir` and `test_lazy_module_attrs_ignores_deny_list_shaped_getattr` and derives lazy names from `!=` compares only. It also notes that M13's two seeded-run docstrings already follow item 6.

### Create — production

Copy code verbatim. Only these edits are allowed: M4 prefixes, each module's docstring and imports, and wrap-only reflow. Every module starts with `from __future__ import annotations`.

- **`claude/.claude/scripts/transcript_analysis/handoff_nudge.py`** (M2–M6). Docstring: `"""The handoff-nudge family's shared core, with no cmd_* of its own: the hook's fire-threshold mirror, the turn-index ramp curve, the single per-session dedup-and-price pass, and the bounded .handoff-nudge.log reader and parser that rearm-backtest, spend-over-threshold, plan-boundary, and handoff-signal-response share.\n\nImports corpus, pricing, and scope by module (attribute access, not by name) -- see scope.py's own top-of-file comment for why."""`
- **`claude/.claude/scripts/transcript_analysis/rearm_backtest.py`** (M2–M5). Docstring: `"""The rearm-backtest command: cmd_rearm_backtest backtests candidate re-arm band spacings for the handoff nudge's one-shot fire against the recorded corpus. .claude/plans/handoff-nudge-rearm-backtest.md holds the design.\n\nImports handoff_nudge, render, and scope by module (attribute access, not by name) -- see scope.py's own top-of-file comment for why."""`
- **`claude/.claude/scripts/transcript_analysis/spend_over_threshold.py`** (M2, M4). Docstring: `"""The spend-over-threshold command: cmd_spend_over_threshold reports each ISO week's share of session spend earned at or above the handoff nudge's own fire threshold.\n\nImports corpus, handoff_nudge, render, and scope by module (attribute access, not by name) -- see scope.py's own top-of-file comment for why."""`

### Create — tests

Each new test file gets:
- a one-line docstring;
- the loader from `test_transcript_subagents.py:29–36`, comment included;
- `from .conftest import …` and `from ._handoff_nudge_helpers import …` for exactly the names it uses;
- its own stdlib imports.

Extract each node by its AST `lineno`–`end_lineno` from the step 0 scratch copy, then apply M9. Nothing else on an `assert` line changes, apart from wrap-only reflow.

- **`claude/.claude/scripts/tests/_handoff_nudge_helpers.py`** (M8). It takes :4457–4458 and :9682–9686, with `import argparse` and `from transcript_analysis import handoff_nudge`. Docstring: `"""Test helpers shared by the handoff-nudge family's test files and by test_transcript_analysis.py's cross-subcommand table and plan-boundary tests."""`
- **`claude/.claude/scripts/tests/test_transcript_handoff_nudge.py`** holds the seven core classes from row 31 and `from helpers import HOOKS_DIR`. Docstring: `"""Tests for transcript_analysis/handoff_nudge.py: fire threshold, ramp curve, per-session turn extraction and scope, and nudge-log parsing, including a contract test against the real hook."""`
- **`claude/.claude/scripts/tests/test_transcript_rearm_backtest.py`** holds `_rearm_backtest_args` (:9653–9671), `_tool_use_asst` (:9674–9679), the four rearm classes from row 31, and M11's new class. Docstring: `"""Tests for transcript_analysis/rearm_backtest.py: boundary detection, spacing replay, --spacings parsing, and the report end to end."""`
- **`claude/.claude/scripts/tests/test_transcript_rearm_backtest_nudge_log.py`** holds `TestNudgeConversionFromLog` and `TestRearmBacktestLogSizeLines`. Docstring: `"""Tests for transcript_analysis/rearm_backtest.py's nudge-to-handoff conversion classifier and per-root log-size disclosure line."""`
- **`claude/.claude/scripts/tests/test_transcript_spend_over_threshold.py`** holds `TestSpendOverThreshold` and M17's `TestSpendOverThresholdDiagnosticFooter`. Docstring: `"""Tests for transcript_analysis/spend_over_threshold.py's cmd_spend_over_threshold, including its nudge-log diagnostic footer."""`

### Modify

- **`claude/.claude/scripts/transcript-analysis.py`**: M7. `build_parser()` and every surviving function body stay unchanged.
- **`claude/.claude/scripts/transcript_analysis/render.py`**: M5's insertion only. The engineer's D1 answer adds this file (row 48).
- **`claude/.claude/scripts/transcript_analysis/review_rounds.py`**: M5's deletion and the :152 retarget only (row 48).
- **`claude/.claude/scripts/tests/test_transcript_analysis.py`**:
  - Delete :4452–4616, :9648–11150, and :11156–11190.
  - Add `from ._handoff_nudge_helpers import _ramp_curve_from_records, _spend_over_threshold_args`.
  - Remove only the imports F401 then flags. Expected: `HOOKS_DIR` (leaving `SKILLS_DIR` on its line) and `_priced_sidechain_asst` (row 60).
  - All other content stays byte-identical.
- **`claude/.claude/scripts/tests/conftest.py`**: docstrings only (M12).
- **`claude/.claude/scripts/tests/test_transcript_cli_bootstrap.py`**: M13.
- **`claude/.claude/scripts/tests/test_transcript_package_module_prefixes.py`**: M15.
- **`claude/.claude/scripts/select-tests.py`**: M10 (comments only).
- **`claude/.claude/scripts/transcript_analysis/__init__.py`**: docstring only (M14).
- **`docs/transcript-analysis-architecture.md`**: M14.

### Explicitly unchanged

- every other existing package module, scope.py included (M15);
- docs/transcript-analysis.md and docs/handoff-nudge.md;
- test_transcript_workstream_cost.py and test_transcript_review_rounds.py (row 52);
- the transcript-analysis SKILL.md;
- CHANGELOG.md;
- `_REVIEW_BENCH_SCRIPTS_DEPENDENCIES`: no review_bench source imports a new module, and render.py gains no import (row 52).

### Reuse

- `workstream_cost.py:1–16` for module-header shape.
- `test_transcript_subagents.py:29–36` for the loader.
- `_pr_cost_helpers.py:1–8` for a helper module that imports the package.
- conftest's builders: `_priced`, `_asst`, `_user_msg`, `_tool_result`, `_bash_use`, `_write_jsonl`, `_write_cost_root`, `_table_cols`, `_cost_args`, `_extract_grand_total`, `_priced_sidechain_asst`.
- The bootstrap file's `_run`, `_isolated_config_env`, and `_seed_priced_account`.

### Dispatch

One `code-writer` dispatch covers every file above (M16). Capture the step 0 baseline before it edits anything. Extract moved code from unmodified scratch copies; never retype it.

- **Stage 1:**
  - Create the three production modules and delete both spans from the shim.
  - Make M5's render.py, review_rounds.py, and shim edits. `_is_fresh_user_prompt`'s shim import goes in once, in its final form, not through the temporary re-export.
  - Add the three modules to the module-import tuple.
  - Temporarily import all 25 moved names into the shim by name. This block is lint-dirty by design.
  - Add M14's three `###` sections, describing M7's final import set.
  - Run select-tests.py's two passes as in Verification 1. Stage 1 leaves select-tests.py untouched, so these runs are scoped.
  - Every selected test must pass except the single node ID in row 28, which must fail. That result is the control for the `_NUDGE_LOG_MAX_READ` retarget.
  - Any other failure goes back to the parent, which reproduces it at the merge-base before treating it as in scope.
- **Stage 2:**
  - Create the helper module and the four test files, applying M9, M11, and M17.
  - M15, in this order:
    1. Add the three modules to `PRODUCTION_MODULES` and the four test files to `TEST_FILES`.
    2. Run the prefix test alone. It must fail in exactly one test, `test_production_modules_reference_only_real_sibling_attributes`, with the message naming `rearm_backtest.py reads scope.PROJECTS_DIR`. Any other result goes back to the parent.
    3. Add `_lazy_module_attrs`, the docstring clause, and M15's two over-accept tests together. The tests call the helper, so adding them at step 1 would add `NameError` failures to step 2's run. After this step all five prefix-test functions must pass.
  - Make the legacy, conftest, bootstrap, select-tests, `__init__`, and remaining doc edits.
  - Replace the temporary re-export with M7's final blocks.
- **Abort procedure.** On a blocked return or an abnormal termination, the code-writer runs no cleanup and returns to the parent. The parent then:
  1. Builds its path lists from `git status --short` and `git diff --cached --name-only`, then removes this plan file's path from both lists. The plan file is never an argument to any step below.
  2. Runs `git restore --staged -- <path>…` as a literal call on each remaining staged path.
  3. Copies each step 0 snapshot back over its file.
  4. Deletes each created file by path.

  The parent never runs `git clean`, `git checkout .`, `git reset --hard`, or `git stash`. Success check: `git status --short` lists only the plan file. If the tree holds anything this plan doesn't name, the parent reports to the engineer first.
- **PR body:**
  - the Stage 1 result, and Stage 2's pre-derivation prefix-test result;
  - reflow counts for M4 and M9, each reported separately;
  - measured `wc -l` for every new and shrunk file, flagging every file over 1,000 lines;
  - `git blame -C -C -s` counts;
  - the #1009 coordination outcome: #1009 landed first; its session reported no edit to the predicate or its duplicate; this phase is measured on d603ae99;
  - each non-matching open PR that overlaps a Critical file, with its overlapping files, noting that whichever lands second rebases;
  - the #1222 rebase note, when it applies;
  - the rollback statement (row 45, including its later-importer condition and the revert PR's full-suite CI check), in place of the waived revert rehearsal (row 54), and the last-in-first-out note (row 46);
  - the stow line: "New files land under `claude/.claude/scripts/`. If `~/.claude/scripts` is a directory symlink into this checkout (stow's folded layout), `git pull` alone picks them up. Otherwise re-run `./install.sh`: the shim imports the three new modules at load, so every transcript-analysis subcommand fails with ImportError until they are linked. That includes the two callers outside this change's own commands: `hooks/nudge-error-mode-analysis.sh` (`friction-count`), which fails open, so the nudge silently stops firing, and `scripts/pr-cost-section.sh` (`cost`), which prints a re-run message.";
  - the follow-ups in Out of scope.

## Verification

Run everything from the worktree root. `<venv>` is `../../../.venv`. Scratch files live outside the repo, and every corpus is synthetic. Every CLI capture runs as `<venv>/bin/python3 claude/.claude/scripts/transcript-analysis.py <args>`, never through `~/.claude/scripts/`. Run each git command as its own literal call. Run captures only through step 0's capture script.

0. **Baseline, before the dispatch.**
   - Copy every file this plan modifies to scratch.
   - Save `--collect-only -q` IDs for the legacy file, the bootstrap file, the prefix test, and test_select_tests.py.
   - Run the legacy file in the same two-pass shape as Verification 1, with `-v -m "not timing" --junitxml=<scratch>/legacy-before-parallel.xml`, then `-v -m timing -n0 --junitxml=<scratch>/legacy-before-timing.xml`. Record `id -u`.
   - Write two scratch scripts:
     - a seeding script that builds the account below, with every token count written as a literal;
     - a capture script. It pins `CLAUDE_CONFIG_DIR` to the account, `TRANSCRIPT_CONFIG_DIRS_FILE` to a nonexistent path, `COLUMNS=100`, and `PYTHONHASHSEED=0`. It writes stdout, stderr, and the exit code of each run below into an output directory named by its argument.
   - The account holds one project directory, `-home-user-parityrepo`. Every timestamp falls on one literal date, `<d>`. It contains:
     - session `s-main` on branch `feat`: priced claude-sonnet-5 main-thread turns with input 100,000, 160,000, 260,000, and 320,000 tokens and output 1,000 each, separated by genuine user messages, plus one priced sidechain turn;
     - session `s-small` on claude-sonnet-4-5, with input 70,000 and 90,000;
     - a `.handoff-nudge.log` holding these lines:
       - a `nudged` line for `s-main`;
       - a later `nudged … action=block ignored=1 skills=-` line;
       - a `handoff session=s-main` line;
       - a `schema-drift` line;
       - a `nudged` line for a session ID not in scope.
   - Runs:
     - `--help` for the top level, `rearm-backtest`, and `spend-over-threshold`;
     - (a) `spend-over-threshold`
     - (b) `spend-over-threshold --since <d>`
     - (c) `rearm-backtest`
     - (d) `rearm-backtest --no-redact`
     - (e) `rearm-backtest --spacings 40000,90000 --branches feat`
     - (f) `rearm-backtest --spacings abc`
   - Run the capture script twice, into two output directories, and diff them. Any difference stops the plan before dispatch.
   - Record a `sha256sum` manifest of every file in the account directory.
   - Record `wc -l` for the shim, the legacy file, and conftest.
1. **Full suite (the global-trigger exception).**
   - Stage the eight created files with `git add -- <paths>` (row 47).
   - Run `<venv>/bin/python3 claude/.claude/scripts/select-tests.py -v -m "not timing" --junitxml=<scratch>/after-parallel.xml`, then `<venv>/bin/python3 claude/.claude/scripts/select-tests.py -v -m timing -n0 --junitxml=<scratch>/after-timing.xml`, matching CI's two passes. CI passes `-v` too (tests.yml :159, :166). Every selected test must pass in both.
   - This diff edits select-tests.py, so both runs must print a `select-tests: running the full suite (global-trigger: …)` line (row 64). It is the one whole-repo run this plan expects, not a widening by hand.
   - Under `-n auto`, pytest prints no per-file lines without `-v`, and select-tests prints a target list only in its scoped branch. So answer the two confirmations below by grepping the `-v` output's node IDs. Alternatively, run `--collect-only -q` through select-tests once first and grep that.
   - Confirm the first run collected `evals/test_review_bench_*.py` and `evals/test_measure_subagent_model_resolution.py`. CI never runs evals/, so this step is their only gate for the render.py and review_rounds.py edits (row 52).
   - Confirm that the prefix test's five test functions (the three original plus M15's two over-accept tests) passed, with all three new modules in `PRODUCTION_MODULES` and the four new test files in `TEST_FILES`.
2. **Test-ID parity.** Strip each ID's file prefix and compare sorted lists.
   - Step 0's legacy list must equal the post-move legacy list plus the four new files' lists, minus M11's ID and M17's ID.
   - The bootstrap file gains exactly the four M13 IDs.
   - The prefix test gains exactly M15's two over-accept tests; test_select_tests.py's list is unchanged.
   - Per-ID outcomes must match step 0, at the same `id -u`. Compare the union of Verification 1's `after-parallel.xml` and `after-timing.xml` against the union of step 0's two legacy XML files, restricted to the legacy file's and the four new files' IDs.
3. **CLI parity.**
   - First confirm the account directory still matches step 0's manifest.
   - Re-run the same capture script, with the same environment, against that same directory at the same path.
   - Every capture must match step 0 byte for byte. The one exception: the `generated YYYY-MM-DD` substring in (c), (d), and (e), which one scratch `sed` script masks identically in both captures.
4. **Negative controls.** Before each control, record `sha256sum` of every file it edits. Make the edit by hand, run the check, then undo the edit by hand and confirm the hashes match. Never use `git stash` or `git checkout`.
   - (a) Delete `TestRootsThreadingSpy`'s two `_mod.scope` setattr lines in its parametrized test (legacy :8167–8168 at base). The `spend-over-threshold` node must fail with `_resolve_project_scope was never called`. Other already-moved commands' nodes fail too; assert only the spend node.
   - (b) Make `handoff_nudge._print_nudge_log_diagnostic`'s `scope.config_dir()` call unguarded. `test_nudge_log_diagnostic_footer_swallows_unresolvable_config_dir` must fail with `ValueError`.
   - (c) Delete `_rearm_backtest_report`'s inline guard. M11's test must fail. `test_no_redact_refused_with_multi_root` must still pass, because it reaches scope's guard.
   - (d) In handoff_nudge.py, add `from _config_dir import config_dir` and change the diagnostic's call to bare `config_dir()`. M17's test must fail. The retargeted swallow test still passes, which shows the vacuity M17 closes. A bare `config_dir()` cannot reach the real config directory during a test, because the autouse fixture sets `CLAUDE_CONFIG_DIR` to a per-test temporary directory and `config_dir()` reads that variable at call time `[verified: scripts/_config_dir.py:33-38, tests/conftest.py:993-1019]`. M17's failure is the control.
   - (e) Point the shim's `p_rearm_backtest.set_defaults(func=...)` at `cmd_spend_over_threshold`. M13's rearm seeded-run test must fail, while its `rearm-backtest --help` test passes.
   - (f) In rearm_backtest.py, change `scope.PROJECTS_DIR` to `scope.PROJECTS_DIRS`. `test_production_modules_reference_only_real_sibling_attributes` must fail, naming `scope.PROJECTS_DIRS`.
   - Two controls already run during the dispatch and are not repeated here:
     - Stage 1's result controls the :10924 retarget.
     - Stage 2's pre-derivation prefix run controls M15's derivation.
5. **Lint.** `<venv>/bin/ruff check claude/.claude/ claude-skills/ plugins/` must be clean. That is CI's exact command `[verified: tests.yml :170]`.
6. **Leftovers and single home.**
   - `git grep -nE '^(def |class )?(<25 names>|_is_fresh_user_prompt)\b' claude/.claude/scripts/transcript-analysis.py` returns nothing. Each of those 26 names is defined exactly once under `transcript_analysis/`.
   - `git grep -nE '_mod\.(<25 names>)\b' claude/.claude/scripts/tests/` returns only two lines: the `_UNCONDITIONAL_HEADER_CASES` spend row and `_plan_boundary_turn_index`'s read.
   - No `setattr(_mod, "_NUDGE_LOG_MAX_READ"` remains anywhere. No `setattr(_mod, "config_dir"` remains in the four new files.
   - `_spend_over_threshold_args`, `_ramp_curve_from_records`, `_rearm_backtest_args`, and `_tool_use_asst` are each defined once.
   - Single home for the predicate:
     - `git grep -nE 'def _is_fresh_user_prompt\b' -- claude/ claude-skills/ evals/` returns exactly one line, in render.py.
     - `git grep -n 'review_rounds\._is_fresh_user_prompt' -- claude/ claude-skills/ evals/ docs/` returns nothing.
7. **Move fidelity.**
   - **Comparator check first.** Seed one scratch defect per verifier path, each in a scratch copy, never in the worktree. The comparator must report each defect and name the check that caught it:
     - Production AST: a deleted statement in `_rearm_backtest_report`.
     - Production comment diff: one changed `#` comment in handoff_nudge.py's constants block.
     - Import set: an added `from _config_dir import config_dir` in handoff_nudge.py.
     - Tests AST:
       - a deleted `assert`;
       - a changed string literal;
       - the removed `@pytest.mark.parametrize` at legacy :10847;
       - an extra method.
     - Test comment diff: one changed `#` comment inside a moved test.
     - Helper byte-for-byte: one changed character in `_tool_use_asst`.
     - Node count: one whole moved class dropped from its new file, which gives a count of 43.
     - render.py remainder: a changed literal in `_pct_of`.
     - render.py inserted-predicate equality: one changed key in the inserted `_is_fresh_user_prompt`. It targets the check that the inserted node equals the shim's step 0 :292–316 node.
     - Tests M9 reversal: one core name retargeted to the wrong module in a scratch test copy, such as `_mod.rearm_backtest._parse_nudge_log_entries`. It targets the by-name reversal of M9 and the two string-target setattr reversals, which must be no looser than a name-for-name map.
     - review_rounds.py module check: review_rounds.py:152's call pointed at `render._content_text`.
     - review_rounds.py predicate equivalence: one changed key in a scratch copy of step 0's review_rounds.py predicate.
     - Legacy remainder: one deleted test in the remaining legacy file.
   - **Node count.** Every real run reports its compared-node count, which must be 44:
     - 26 production nodes: the 25 A nodes, plus render.py's `_is_fresh_user_prompt`;
     - 4 helper nodes;
     - 14 test classes.

     M11's class and M17's class are excluded by name. The render.py and review_rounds.py module checks below report separately.
   - **Import set.** Each new module's set of import statements, collected from every `Import` and `ImportFrom` anywhere in the module, must equal M4's list plus `from __future__ import annotations` exactly, with no alias.
   - **Production.**
     - Reverse M4's map by name, including the three aliases. Never strip prefixes like `scope.`, because `scope.PROJECTS_DIR` is already present at base.
     - Each node's `ast.dump` must equal its step 0 span.
     - render.py's `_is_fresh_user_prompt` node needs no reversal. It must equal the shim's step 0 :292–316 node.
     - The `#`-comment diff must be empty, except for the dropped banners at :3300 and :4156–4158.
   - **render.py remainder.** Step 0's render.py module AST must equal the post-move module AST with the inserted node removed.
   - **review_rounds.py.** Both checks run against step 0's copy:
     - Step 0's module AST, minus its `_is_fresh_user_prompt` FunctionDef, must equal the post-move module AST once `render._is_fresh_user_prompt` is reversed by name to `_is_fresh_user_prompt`. The `#`-comment diff must be empty.
     - Drop the deleted FunctionDef's docstring and reverse `render._content_text` to `_content_text`. The result must equal the shim's step 0 :292–316 node with its docstring dropped. This is row 51's mechanical check.
   - **Tests.**
     - Reverse M9 by name.
     - In `_ramp_curve_from_records`, reverse its two `handoff_nudge.` reads to `_mod.`.
     - Reverse the two string-target setattrs by node ID only, since :10655 and :11107 patch `_mod.scope` at base.
     - Every class and function AST must equal its legacy slice.
     - `_spend_over_threshold_args`, `_rearm_backtest_args`, and `_tool_use_asst` must match byte for byte.
     - The comment diff must be empty, except for the two dropped banners at :4452–4454 and :9648–9650.
   - **Legacy remainder.** Step 0's legacy copy, with the three deleted ranges removed, must differ from the post-move file only in import lines.
   - After the commit, report `git blame -C -C -s` counts for each new file.
8. **Sizes.** Report `wc -l` for every new and shrunk file. Every new file must be at most 1,000 lines.

## Out of scope

- **Phase B:** moving plan-boundary and handoff-signal-response, deleting the shim's seven-name block, and dropping `fake_projects`' `mod.config_dir` line. B's phase owns all three.
- **`_is_fresh_user_prompt_for_narrative`.** Its own docstring (shim :525) defines a different predicate, not a copy, so it stays in the shim for its own phase.
- **The parametrized cross-command `--no-redact` refusal test.** Only rearm-backtest's obligation falls due in this phase (M11). That test should also assert stderr names `--no-redact` across the six sibling direct-call `*_report_itself` tests, which assert less than M11 does (at most `code == 2` and empty stdout). They are `test_no_redact_refused_by_cost_report_itself_even_when_called_directly` (test_transcript_cost.py), `test_no_redact_refused_by_read_scope_report_itself_even_when_called_directly` (test_transcript_read_scope.py), `test_no_redact_refused_by_cache_rebuild_report_itself_even_when_called_directly` (test_transcript_cache_rebuild.py), and the legacy file's `test_no_redact_refused_by_edit_format_report_itself_even_when_called_directly` (:3392), `test_no_redact_refused_by_report_itself_even_when_called_directly` (:4330), and `test_no_redact_refused_by_report_itself_when_multi_root` (:11487) `[verified: grep of the four files at d603ae99]`. The parametrized test augments them: it adds one cross-command test, and the siblings stay untouched in this phase.
- **`_UNCONDITIONAL_HEADER_CASES` and `TestRootsThreadingSpy`.** They span groups, so they stay in the legacy file until the `cli.py` phase.
- **The `cli.py` phase's Store assertion.** When `main()` moves into the package, its `scope.PROJECTS_DIR = …` (shim :7195) becomes a Store through a sibling attribute. The prefix test's production check rejects that. The `cli.py` phase owns it.
- **The `--spacings` default literal.** `build_parser`'s `default="40000,80000,120000"` (shim :6921) restates `_REARM_BACKTEST_DEFAULT_SPACINGS` (:4198). After the move the two live in different files, and no test ties the literal to the constant. A verbatim move keeps both. A follow-up could derive the default from the constant.
- **A permanent single-home tripwire for `_is_fresh_user_prompt`.** Declined:
  - #1009 has already landed, and its merge resolution was the re-duplication path the finding named.
  - What remains is the general rule against duplicated helpers, which no other helper enforces per symbol.
  - Verification 6 checks the move once, and the architecture doc's render section names the one home (M14).
- **require-stow-reminder.sh's header claim** (:29–37) that every nested new file needs a re-stow. It contradicts a folded layout (row 47). This phase only words its own stow line conditionally. Raise the hook text to the engineer.
- **Stale prose that moves verbatim:**
  - "in this file" at :4279 and :4876–4877;
  - `_ramp_curve_from_records`' docstring naming one class;
  - the legacy file's `# plan-boundary` banner (:11151–11153), which will head `TestCacheMissReason` after the delete. It heads an A class today, and the legacy remainder stays byte-identical.
- **Legacy :4309's dead `_mod.config_dir` patch in `TestCacheEfficiency`.** cache-efficiency reads scope's binding, so the patch never takes effect. It has the same bug shape but sits outside this slice; raise it to the reviewer.
- **Stale line citations that predate this phase:**
  - review_rounds.py:68 cites shim :2371, but the regex is now at :1165;
  - config-schema-audit.md.

  review_rounds.py:48's citation goes with its deleted node (M5).
- **Case studies and reports that name moved symbols.** They are preserved records.
- **Sizes of the shim, the legacy file, and conftest.** Each was over 1,000 lines before this phase.
- **A CHANGELOG entry.** No earlier decomposition phase added one.
- **Any CLI surface change.**
