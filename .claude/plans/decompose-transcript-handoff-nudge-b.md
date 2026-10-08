# Decompose transcript-analysis: handoff-nudge B

## Context

Goal: move the plan-boundary and handoff-signal-response groups (about 1,890 legacy test lines and about 930 production shim lines) out of the `transcript-analysis.py` shim into `transcript_analysis/` package modules with their tests, behavior-preserving, as handoff-nudge B of tracker #1116.

Ask: #1116 line 34 "handoff-nudge B — plan-boundary, handoff-signal-response (~1,985 test lines)"; the engineer's brief names `.claude/plans/decompose-transcript-handoff-nudge-a.md` as the template. On the #1222 gate the engineer typed "I think baselines now. Does architect agree?". On the module and test-file layout the engineer typed "Ask the architect (one spawn for all responses)".

Why now: #1116 orders the remaining groups largest-first, and a group's shared core (now `transcript_analysis/handoff_nudge.py`, merged in A, PR #1226) moves before its consumers. B is the consumer half of that group.

Outcome: the shim loses both groups and its seven-name bare-read block. Tests move with the code. Each new module and test file stays within the 1,000-line target.

## Approach

Plan-boundary and handoff-signal-response leave the shim for two new package modules. `transcript_analysis/plan_boundary.py` (about 330 lines) and `transcript_analysis/handoff_signal_response.py` (about 640 lines) are copied verbatim in shim source order, and only the by-module prefixes change. Their tests move into three new test files. The three transcript builders that both handoff-signal-response test files read go into the existing `tests/_handoff_nudge_helpers.py`. The shim keeps `build_parser()` and imports the two `cmd_*` names for `set_defaults`. It drops the seven-name `handoff_nudge` block and its own `config_dir` import, because the last code that reads either one bare leaves in this phase.

**The two decisions the engineer handed to this plan:**

- **Layout: two modules and three test files** (rows 30–31). I weighed the session's proposal on its own merits and reached the same layout. The tests split this way:
  - `test_transcript_plan_boundary.py` holds all plan-boundary tests.
  - `test_transcript_handoff_signal_response_detection.py` holds the tests for shim :4384–4653: the signal classifiers, the excerpt and forward-context windows, and session-row detection.
  - `test_transcript_handoff_signal_response.py` holds the tests for :4656–4971: ranking, cards, the benchmark, the renderers, and the command end to end.
  - The test seam follows production source order, the same within-module split A used for rearm-backtest.
- **#1222: I agree with taking baselines now** (rows 32–34, M13). Waiting would change none of this phase's baselines:
  - #1222's file list, as relayed, is test infrastructure only.
  - This phase's CLI baseline captures production behavior. Its fidelity baseline is scratch copies of the shim and the legacy test file. #1222 touches none of those.
  - A already took its baselines while #1222 was open.
  - The worst case, #1222 landing mid-phase, costs one re-run of step 0, which the sync rule already requires.

  Waiting would block this phase on an issue nobody has picked up. Four places record the departure from #1116's wording: row 34, the precondition's #1222 check, the sync rule, and the PR body's #1222 note. Editing #1116 itself is the engineer's call (Out of scope).

The Step 3 evidence needed these corrections. Each one changes the plan:
- **Removing the shim's `config_dir` import (:28, not :27) breaks three patch sites, not one** (row 16). Each would raise AttributeError: `fake_projects` (conftest :1223), legacy :4325 in `TestCacheEfficiency`, and `test_transcript_ledger_common.py:145`. M7 handles all three.
- **After the delete, F401 flags six shim imports** (row 8): `statistics`, `date`, `config_dir`, `_cache_miss_reason`, `_model_rates`, and `_compute_workstream_dollars`. Tests still read the last three through `_mod` (row 9), so they stay, each with a `# noqa: F401`. The first three go.
- **No select-tests.py edit is needed**, so Verification 1 runs scoped, not as a global trigger (row 23).
- **The `--no-redact` and log-read obligations are already met** by tests that move verbatim (rows 27–28). So this phase adds no test like A's M11 or M17.
- **Three handoff-signal-response builders cross the test seam** (row 12), so they need a shared home (M5).

Alternatives set aside:
- **Three modules, with the detector helpers split out.** Each helper has one consumer, and that consumer is inside `handoff_signal_response.py` (row 5). A separate module would be a split by theme, which A rejected. The combined module is about 640 lines, so size forces no split (row 4).
- **One module for both commands.** It would be about 975 lines, right at the limit, for two commands that read none of each other's names (row 2).
- **One handoff-signal-response test file.** It would be about 1,410 lines (row 13).
- **A test seam by exact consumer.** Detector and session-row tests would be about 300 lines. Everything else, including the excerpt and forward-context tests that `_handoff_signal_response_cards` consumes, would be about 990 lines, too close to the limit.
- **A test seam by fixture style** (pure-function tests against transcript-driven tests). It keeps every builder local, but the seam would be which builders a test calls, not which code it tests.
- **A short alias such as `hsr = _mod.handoff_signal_response` in the new test files.** It would cut the reflow, but the prefix test checks only `_mod.<module>.<name>` reads, so aliased reads would escape it.
- **Keeping the shim's `config_dir` import with a `# noqa`.** It would avoid the three test-site edits, but it would keep an import no code reads, only so that three patches that reach nothing don't raise (M7).

### Assumption ledger

**Root:** Plan-boundary (shim :4032–4346) and handoff-signal-response (:4349–4971) still live in the shim. They read seven `handoff_nudge` names through the shim's by-name block (:128–142) and `config_dir` through the shim's own import (:28). Their tests, about 1,895 lines, still live in the legacy file at :66–72 and :9560–11449. `[verified: Read of both spans, the import block, and the legacy slices at a6a17a9e; class/def grep of the legacy file]`

**Givens:**
- G1. pytest imports test modules by basename, so new test files keep the `test_transcript_*` prefix and unique basenames. Reason: pytest owns this behavior. `[verified: A plan G1; Glob finds no existing file with any of the five new basenames]`
- G2. `from m import n` binds `n` at import time, so a monkeypatch reaches code only through the binding that code reads at call time. Reason: Python language semantics. `[verified: A plan G2]`
- G3. A directly invoked `python3 transcript-analysis.py` finds the package only through `sys.path[0]`. Reason: CPython owns script bootstrap. `[verified: A plan G3]`
- G4. #1222's content and merge timing belong to another work item. Reason: another work item owns it. `[unverified — relayed: open, no PR]`
- G5. `monkeypatch.setattr(obj, name, value)` raises AttributeError when `obj` has no attribute `name`. Reason: pytest owns this behavior (`raising=True` is the default). `[unverified — documented pytest default, not re-read this session; Stage 2's run exercises all three sites]`

Two conditions look like givens but aren't. This repo owns each one, so each is a deliberate decline listed in **Out of scope**:
- `build_parser()` staying in the shim, with the cross-command legacy tables;
- `TestCacheMissReason` staying in the legacy file.

**Mechanisms:**

- **M1: Scope is both command groups and their test slices, plus removing the seven-name block and the shim's `config_dir` binding.** `anchors: root, row2, row8, row16`
  - Lighter, rejected: move one command now and one later. The seven-name block can't go until both have moved: four of its names serve plan-boundary and three serve handoff-signal-response. Both phases would also edit the shim, the legacy file, conftest, and the architecture doc.
  - Lighter, rejected: keep `config_dir` with a `# noqa: F401`. See the last alternative above.
- **M2: Two modules, each filled by copying its shim line range verbatim.** `anchors: row1, row2, row4, row5, row30, row31`
  - `plan_boundary.py` takes :4034–4346, which is 8 top-level nodes. The banner at :4032 is dropped.
  - `handoff_signal_response.py` takes :4352–4971: 8 constants and 16 functions, with every comment between nodes. The banner at :4349–4350 is dropped. Its design-plan pointer moves into the module docstring.
  - Copying line ranges, rather than AST node spans, keeps the comments between nodes.
- **M3: Import discipline and rename map.** `anchors: G2, row6, row7`
  - `plan_boundary.py`:
    - `from transcript_analysis import handoff_nudge, pricing, render, scope`
    - stdlib: `argparse`, `sys`, `defaultdict`, `Sequence`, `datetime.{UTC,date,datetime}`, `Path`.
  - `handoff_signal_response.py`:
    - `from transcript_analysis import corpus, cost, handoff_nudge, pricing, redaction, render, scope`
    - stdlib: `argparse`, `json`, `os`, `random`, `re`, `statistics`, `sys`, `defaultdict`, `Sequence`, `datetime.date`, `Path`.
  - Each module also starts with `from __future__ import annotations`. No module has any other import line, and neither has a `from transcript_analysis.<m> import` line.
  - Rename map:
    - These go to `pricing.`: `_model_rates`, `_price_turn`, `_cache_miss_reason`.
    - `_dedup_turns_by_request_id` becomes `pricing.dedup_turns_by_request_id`.
    - These go to `render.`: `_fam`, `_content_text`, `_fmt_usd`, `_pct_of`.
    - These go to `redaction.`: `_assign_session_redact_label`, `_redact_session_id`.
    - `_compute_workstream_dollars` goes to `cost.`.
    - These go to `scope.`: `_resolve_cost_roots`, `_DO_NOT_PUBLISH_BANNER`, `_parse_since_nd_arg`, `_resolve_project_scope`, `config_dir`.
    - `_print_resolved_scope` becomes `scope.print_resolved_scope`.
    - `_resolve_scan_roots` becomes `scope.resolve_scan_roots`.
    - These go to `handoff_nudge.`: the seven block names.
  - `scope.PROJECTS_DIR`, `corpus.split_command_segments`, and `corpus.read_session_file` are already prefixed and stay as they are.
  - Prefix code references only. Let ruff F821 drive every prefix, and never run a regex over bare names, because docstrings name these symbols in prose.
  - Wrap-only reflow is allowed where E501 flags a line. Report the count.
- **M4: Shim changes.** `anchors: row3, row8, row9, row10`
  - Delete :4032–4973, so that :4030–4031 stay blank before `_add_project_scope_args` (:4974).
  - Delete the seven-name block (:128–142).
  - Delete `import statistics` (:17) and `from _config_dir import config_dir` (:28). Change :24 to `from datetime import UTC, datetime`.
  - Add `# noqa: F401` to three imports:
    - `_compute_workstream_dollars` (:96);
    - `_cache_miss_reason` (:166), with `-- read only via _mod._cache_miss_reason from test files`;
    - `_model_rates` (:172), with `-- read only via _mod._model_rates from test files`.
  - Rewrite the cost block's comment (:87–92) to state:
    - the eleven noqa'd names are read only via `_mod.<name>` from test files;
    - `_compute_pr_cost_branch_totals` is one of them;
    - `cmd_cost`/`cmd_cost_trend` are the two names this file's own code calls bare, via `set_defaults`.
  - Add `handoff_signal_response` and `plan_boundary` to the module-import tuple (:36–62) and to its comment (:30–35). In that comment, `corpus` moves out of the test-only list. The closing clause becomes: corpus and scope are the two modules this file's own code reads bare, as `corpus.split_command_segments` (:3354) and `scope.PROJECTS_DIR`.
  - Add two by-name blocks in the rearm/spend style (:185–189):
    - `cmd_handoff_signal_response`, with the comment `# Read bare by this file's own build_parser (the handoff-signal-response` / `# subcommand's own set_defaults).`
    - `cmd_plan_boundary`, with the same comment naming plan-boundary.
  - Run ruff's autofix only as `--select I --fix`.
- **M5: Test layout: three files, plus three builders added to `tests/_handoff_nudge_helpers.py`.** `anchors: G1, row11, row12, row13, row30`
  - `_check_result_json`, `_handoff_advisory_attachment`, and `_check_call_turn` are read on both sides of the seam, so they move verbatim into the existing family helper module. Every other helper stays local to the one file that uses it.
  - Rejected: a new `_handoff_signal_response_helpers.py`. #1116 groups both commands as handoff-nudge B, and the existing module's docstring already scopes it to that family.
  - Rejected: conftest as their home. Each builder serves one command group, and the rule from audit-routing's M5 puts only builders shared across command groups in conftest.
  - Rejected: duplicate copies. Record builders are not a DAMP exception.
- **M6: Test-side retargets.** `anchors: G2, row14, row15`
  - The plan-boundary names go to `_mod.plan_boundary.`, including `args.func == _mod.cmd_plan_boundary` at :10065.
  - The handoff-signal-response names go to `_mod.handoff_signal_response.`, including :10870.
  - `_mod._extract_rearm_session_turns` (:9563) becomes `_mod.handoff_nudge._extract_rearm_session_turns`.
  - `monkeypatch.setattr(_mod, "_PLAN_BOUNDARY_SONNET_MODEL", …)` (:9782) becomes `monkeypatch.setattr(_mod.plan_boundary, "_PLAN_BOUNDARY_SONNET_MODEL", …)`.
  - These stay unchanged:
    - reads of names the shim keeps: `_mod._model_rates`, `_mod._price_turn`, `_mod._DO_NOT_PUBLISH_BANNER`, `_mod._context_at_turn` (legacy :9921, still bound via the shim's bare read at :2851), `_mod.build_parser`;
    - the `_mod.scope` patches at :9818 and :9859;
    - the legacy `_UNCONDITIONAL_HEADER_CASES` row at :7815.
  - Wrap-only reflow is allowed. Report the count.
- **M7: The shim's `config_dir` binding goes, and its three patch sites change.** `anchors: G5, row16, row17, row18`
  - conftest :1223: delete the line (M8).
  - Legacy :4325: `monkeypatch.setattr(_mod, "config_dir", …)` becomes `monkeypatch.setattr(_mod.scope, "config_dir", …)`.
    - This is the binding cache-efficiency actually reads.
    - It matches its siblings' identical tests at :2872, :3305, and :9818.
    - The test's outcome is unchanged (row 17).
  - `test_transcript_ledger_common.py`, in the test at :131–150:
    - :145 retargets to `_mod.scope`.
    - `shim_dir`/`"shim-config"` become `scope_dir`/`"scope-config"`.
    - :134's "not the shim's" becomes "not scope.py's".
    - The method becomes `test_resolve_machine_identity_routes_through_ledger_common_config_dir_not_scopes`.
    - This keeps its two-directory check meaningful against the one route that remains possible (row 18).
  - Rejected: deleting :4325. It would leave the test's `default_dir` setup with nothing reading it.
  - Rejected: `raising=False` at :145. It would create an attribute nothing reads, so the test would pass vacuously.
- **M8: conftest.** `anchors: row16, row22`
  - Delete :1223.
  - In `fake_projects`' docstring, delete the "handoff-signal-response still lives in the shim … so mod.config_dir is patched too." sentence (:1210–1212), and change "five bindings" (:1214) to "four bindings".
  - Add the three new test files and `tests/_handoff_nudge_helpers.py` to the module docstring's consumer list (:4–24).
- **M9: Four bootstrap tests, following every package-moved command since read-scope.** `anchors: G3, row19, row20`
  - `plan-boundary --help` asserts `--config-dir DIR`.
  - `handoff-signal-response --help` asserts `--context-turns N`.
  - Two seeded runs with `_seed_priced_account` and `_isolated_config_env`:
    - plan-boundary: exit 0 and `Sessions scanned: 1`.
    - handoff-signal-response: exit 0 and `## Handoff signal response (0 signal(s) in scope)`.
  - The seeded docstrings follow #1222 item 6, as A's do (bootstrap :769–771). The handoff-signal-response run claims only resolution and dispatch, because its seed carries no signal.
  - The architect proposed this addition. The engineer said keep it (row 39).
- **M10: Extend the prefix test's tuples.** `anchors: row21`
  - Add `plan_boundary.py` and `handoff_signal_response.py` to `PRODUCTION_MODULES`.
  - Add the three new test files to `TEST_FILES`.
  - No helper change: `scope.config_dir` is a top-level import binding (scope.py:28–33), and A's `_lazy_module_attrs` already covers `PROJECTS_DIR`.
- **M11: Docs and docstrings.** `anchors: row22, row25`
  - Architecture doc:
    - In :16–20, add both modules to the list of modules the shim imports back into, and remove `handoff_nudge.py` from it: its only by-name reader in the shim is the seven-name block that M4 deletes.
    - In :46–47, replace "The shim also reads `handoff_nudge.py` names bare; …" with "`build_parser()` likewise wires up `plan_boundary.py`'s `cmd_plan_boundary` and `handoff_signal_response.py`'s `cmd_handoff_signal_response` from the shim."
    - In the `handoff_nudge.py` section, :372–373's consumers become the four module names. Delete :377–381 ("Seven names are reached bare …").
    - After :402, add `### \`plan_boundary.py\`` and `### \`handoff_signal_response.py\``. Each covers:
      - the module's responsibilities;
      - its module imports;
      - where its `--no-redact` refusal lives: inline in `_plan_boundary_report`, and inline in `cmd_handoff_signal_response`, since `scope.resolve_scan_roots` refuses nothing;
      - the one name reached bare from `build_parser()`.

      The handoff-signal-response section also states that its `.handoff-nudge.log` read goes through `scope.config_dir()` by attribute, so `fake_projects`' existing patch isolates it.
    - In :443–446, change "five `config_dir` bindings: `scope.config_dir` and the shim's still-independent `config_dir` (…)" to four bindings, with no shim binding.
    - In :514–531:
      - add three Tests bullets;
      - say that handoff-signal-response's tests split again at its detection/reporting seam;
      - make the helper paragraph name six helpers, and say the legacy file imports only `_spend_over_threshold_args` and `_rearm_backtest_args`, for its cross-subcommand table.
  - `__init__.py`: add `plan_boundary` and `handoff_signal_response` to the command-group list.
  - `_handoff_nudge_helpers.py` docstring: drop "and plan-boundary tests".
  - Legacy :9501's banner `# plan-boundary` becomes `# cache_miss_reason (pricing.py)`, the one class it will head (row 25).
- **M12: One `code-writer` dispatch in two internal stages.** `anchors: row15, row23`
  - Rejected: sequenced dispatches. Both stages edit the shim, the legacy file, and the architecture doc.
- **M13: Baselines now; #1222 rebases onto this phase.** `anchors: G4, row32, row33, row34`
  - The precondition re-reads #1222. If a PR naming 1222 now exists, it blocks, because the engineer's answer was given when no PR existed.
- **M14: No new safety test.** `anchors: row27, row28`
  - The existing direct-call and CLI-level `--no-redact` tests move verbatim.
  - The operator-lag test controls that the log read goes through a `config_dir` binding that `fake_projects` patches. It cannot tell `scope.config_dir` from the other patched bindings, and no test can after the shim binding goes: the read is the same attribute reference by construction (M3 rename map).
- **M15: Post-merge revert rehearsal per the governing plan.** `anchors: row35, row36`
  - A's waiver covered A only.

**Assumptions:**

1. Shim spans. `[verified: Read at a6a17a9e]`
   - Plan-boundary: banner :4032, nodes :4034–4346, blank :4347–4348.
   - Handoff-signal-response: banner :4349–4350, comment :4352–4354, constants :4355–4381, functions :4384–4971, blank :4972–4973.
   - `_add_project_scope_args` follows at :4974.
2. 32 top-level names move. Neither group reads any name of the other. `[verified: Read of both spans]`
   - 8 to plan_boundary: `_PLAN_BOUNDARY_SONNET_MODEL`, `_plan_boundary_turn_index`, `_arm_b_boundary_plus_one_dollars`, `_arm_b_later_turn_dollars`, `_arm_c_turn_dollars`, `_plan_boundary_work_inflation_breakeven`, `cmd_plan_boundary`, `_plan_boundary_report`.
   - 24 to handoff_signal_response: the 8 `_HANDOFF_SIGNAL_*` constants and 16 functions, from `_handoff_signal_bash_check_call` through `cmd_handoff_signal_response`.
3. Outside the spans, the shim reads moved names only at `set_defaults` (:5938, :5973). No file outside the shim and the legacy test file reads any of the 32 in code. `[verified: grep of all 32 names in the shim; repo-wide grep]`
4. Estimated sizes: plan_boundary about 330 lines and handoff_signal_response about 640, or about 975 combined. `[verified: line arithmetic over row 1; header shape from spend_over_threshold.py:1–14]`
5. Inside handoff-signal-response, each helper has one consumer. `[verified: Read]`
   - `_handoff_signal_response_session_rows` consumes the three classifiers.
   - `_handoff_signal_response_cards` consumes `_handoff_signal_excerpt` and `_handoff_signal_forward_context`.
6. M3's import lists and rename map. `[verified: Read of both spans at a6a17a9e]` Verification 7's import-set check re-proves them mechanically.
7. Every rename target is a top-level binding. `[verified: grep]`
   - pricing.py :146, :223, :310, :511;
   - render.py :13, :27, :62, :66;
   - redaction.py :182, :194;
   - cost.py :211;
   - corpus.py :157, :237;
   - scope.py :28–33;
   - handoff_nudge's seven names.

   Nothing imports either new module except the shim, so no cycle is possible.
8. After the delete, F401 flags exactly six shim imports. `[verified: grep at a6a17a9e]` Every other shim import keeps a reader outside the spans.
   - `statistics`, read only at :4864 and :4943;
   - `date`, read only at :4134 and :4770;
   - `config_dir`, read only at :4939;
   - `_cache_miss_reason`, read only at :4273;
   - `_model_rates`, read only at :4073 and :4164;
   - `_compute_workstream_dollars`, read only at :4929.
9. Tests still read three of those through `_mod`. No test reads `_mod.statistics` or `_mod.date`. `[verified: grep]`
   - `_mod._cache_miss_reason`: legacy :9514–9557.
   - `_mod._model_rates`: test_transcript_cost.py, the handoff_nudge, rearm, and spend test files, and moved plan-boundary tests.
   - `_mod._compute_workstream_dollars`: 14 reads in test_transcript_workstream_cost.py.
10. The tuple comment's claim that scope is the only module read bare is already false: :3354 reads `corpus.split_command_segments`, outside this phase. `[verified: grep]`
11. Legacy slices. `[verified: class/def grep]`
    - Plan-boundary: `_extract_arm_dollars` (:66–72, read only at :9937–9939 and :10038), then :9560–10065, with 4 helpers and 7 classes.
    - Handoff-signal-response: banner :10068–10070, 5 helpers (:10072–10156), and 17 classes (:10159–11449, end of file).
12. Helper use across the seam. `[verified: grep]`
    - `_check_call_turn`, `_check_result_json`, and `_handoff_advisory_attachment` are read on both sides of the :10642/:10645 seam.
    - `_handoff_hard_block_attachment` is read only in `TestHandoffSignalResponseSessionRows`.
    - `_handoff_signal_response_args` is read only in the `TestCmd*` classes.
    - `_ramp_curve_from_records` is read only at :9920 and :10023–10024.
13. Estimated test-file sizes: plan-boundary about 555 lines, detection about 540, reporting about 860, helper module about 92. A single handoff-signal-response file would be about 1,410. `[verified: arithmetic over rows 11–12]`
14. `_mod.` reads in the slices. `[verified: grep]`
    - Every `_mod.<32-name>` read retargets.
    - The one string-target patch is at :9782.
    - The kept-name reads are `_mod._model_rates` (:9662, :9694, :9709), `_mod._price_turn` (:9924, :10543–10573), `_mod._DO_NOT_PUBLISH_BANNER`, and `_mod.build_parser`.
    - The `_mod.scope` patches are at :9818 and :9859. The handoff-signal-response slice has no `setattr`.
15. Under Stage 1's temporary re-export, exactly one legacy test fails: `TestPlanBoundaryReport::test_unpriced_sonnet_model_fails_at_report_start_not_mid_scan`. Its patch lands on the shim's binding, while plan_boundary reads its own global. `[unverified — inferred from G2; Stage 1 runs it]`
16. Three `config_dir` patches target a shim module: conftest :1223, legacy :4325, and test_transcript_ledger_common.py:145. test_select_tests.py :2877 and :2901 patch select-tests.py's own `_mod`. `[verified: repo-wide grep]`
17. Legacy :4325's patch is dead today: cache-efficiency reaches `config_dir()` only through `scope._resolve_cost_roots`. That function never checks that the default root exists (scope.py:726–774), so a live `_mod.scope` patch still gives two roots and exit 2. `[verified: Read; shim grep shows no bare config_dir() outside :4939]`
18. The ledger_common test pins `_machine_identity_path`'s route (ledger_common.py:59) by patching two bindings to two distinct directories. Once the shim binding is gone, scope's binding is the remaining alternative route. `[verified: Read of test :131–150 and ledger_common.py:51–59]`
19. The bootstrap convention: every command moved since read-scope has a `--help` test and a seeded subprocess test (bootstrap :448–796). `[verified: grep]`
20. Seeded outputs.
    - plan-boundary prints `Sessions scanned: {n}` (:4278), and handoff-signal-response prints its heading at :4829. `[verified: Read]`
    - The seeded session passes `_session_matches_rearm_scope` with no `--since` and no branch filter. `[unverified — inferred from rearm_backtest.py:374/:457 and its seeded test asserting "Sessions in scope: 1" on the same seed]`
21. The prefix test's tuples are at :20–41. `[verified: Read]`
22. Every edit site in M8 and M11. `[verified: Read of the architecture doc :14–47, :363–402, :443–446, :514–531; __init__.py :1–8; _handoff_nudge_helpers.py :1–2; conftest :4–24, :1196–1227]`
23. select-tests: `GLOBAL_TRIGGER_PATHS` is `helpers.py`, `pyproject.toml`, and select-tests.py (:396–400), and this phase edits none of them. No moved test reads a hook or SKILL.md by path; the `SKILLS_DIR` reads are at legacy :9389–9422, outside the slices. `__init__.py` is in `_REVIEW_BENCH_SCRIPTS_DEPENDENCIES` (:80). `[verified: Read]`
24. After the delete, F401 flags exactly three legacy imports. `[verified: grep]`
    - `random`, read only at :10940;
    - `_exit_plan_mode`, read only in the slices;
    - `_ramp_curve_from_records`.
25. After the delete, the :9500–9502 `# plan-boundary` banner heads only `TestCacheMissReason`, a pricing test. `[verified: Read]`
26. No non-test code loads the shim in-process. The scripts that call the shim (`nudge-error-mode-analysis.sh`, `skill-fidelity-report.sh`, `pr-cost-section.sh`) invoke neither command. `[verified: grep]`
27. Both `--no-redact` obligations are already met. `[verified: Read]`
    - Plan-boundary has a direct-call test (:9801) and a CLI-level test (:9811).
    - Handoff-signal-response's only guard is inline in `cmd_handoff_signal_response`, which `_resolve_scan_roots` never preempts. `test_no_redact_refused_with_multi_root[0|5]` (:10657–10667) reaches it.
28. The operator-lag test (:11305–11325) controls the `scope.config_dir()` read. `[verified: Read]`
    - It writes `.handoff-nudge.log` under `fake_projects`' `tmp_path` and asserts `1 joined`.
    - The autouse fixture pins `CLAUDE_CONFIG_DIR` to `tmp_path/"isolated-claude-config"` (conftest :1321), so a by-name `config_dir()` would find no log.
29. Clock reads. `[verified: Read]`
    - `cmd_plan_boundary` reads the date once (:4131) for `generated <today>` (:4277).
    - `_format_handoff_signal_cards_as_markdown` reads `date.today()` (:4770) for `Generated: <today>`.
    - `--since Nd` reads the clock in scope.
30. Layout delegation: `[engineer-verified: "Ask the architect (one spawn for all responses)"]`, answering how the code and tests should be split. It covers handing the layout decision to the architect. The layout itself is this plan's (M2, M5).
31. Two modules plus three test files was the session's own proposal, not the engineer's. `[unverified — the session's proposal]` This plan adopts it on its own weighing (Approach).
32. `[engineer-verified: "I think baselines now. Does architect agree?"]`, answering whether this phase waits for #1222 or takes baselines now. It covers the engineer's inclination toward baselines now. The agreement and the reasoning are this plan's own (M13, row 33).
33. #1222's facts.
    - It is open, with no PR. It touches conftest.py, test_transcript_cli_bootstrap.py, select-tests.py, test_select_tests.py, and the prefix test. Item 6 governs bootstrap docstrings. `[unverified — relayed via A's row 33 and this run's Step 3; the precondition re-reads it]`
    - A took its baselines while #1222 was open. `[verified: A plan row 3; the shim at a6a17a9e carries A's imports (:45, :52, :58, :128–142, :185–189, :261–265)]`
34. #1116 records #1222 as landing "before the next phase takes its baselines". `[unverified — relayed from Step 3; I can't read issues]` A was that next phase and already proceeded.
35. The governing plan requires a revert rehearsal at each phase boundary (transcript-analysis-decomposition.md:276–279). A's waiver covered A only. `[verified: Read; A plan row 54]`
36. Rollback is `git revert` of the squash commit. `[verified for squash-only merges in A plan row 45]` The revert stays valid only while:
    - no later commit on main touches a file in this PR's list;
    - no later file imports either new module or reads `_mod.plan_boundary.*` or `_mod.handoff_signal_response.*`.
37. Line numbers are at a6a17a9e. `[verified: gitStatus; Reads this session]`
38. Carried from A's row 47:
    - ruff selects E, F, B, I, UP, and SIM, with line length 130;
    - `require-stow-reminder.sh` needs `install.sh` or `stow` in the PR body;
    - `<venv>` is `../../../.venv` `[unverified — relayed]`.

39. `[engineer-verified: "Ask the architect. I say keep M9"]`, answering whether the four bootstrap tests (M9) stay in this phase. It covers keeping M9; the architect authored M9 in this plan, so no separate architect dispatch is made for it.

**Plan-review should re-check:** M7's three patch-site changes, especially the ledger_common rename; row 8's F401 set and the three imports kept with `# noqa`; M5's choice of helper module; row 15's single expected Stage 1 failure; M13's #1222 reasoning.

## Critical files

**Precondition.** Before step 0, the parent runs these read-only commands:
- `gh issue view 1222 --json state,body`
- `gh issue view 1116 --json body`
- `gh pr list --state open --limit 200 --json number,title,headRefName,body,files`
- `git worktree list --porcelain`
- `git branch --all --list '*1222*' '*handoff*' '*plan-boundary*' '*plan_boundary*'`
- `git ls-remote --heads origin`

The scan follows these rules:
- **Critical files** are the Create and Modify paths below, plus `claude/.claude/hooks/nudge-handoff-near-context-cap.sh`. `_HANDOFF_SIGNAL_HOOK_BASENAME` and `_HANDOFF_SIGNAL_HARD_BLOCK_TEXT` mirror that hook.
- **Match rule.** A PR, branch, or worktree matches when its title, body, or ref names `1222`, `handoff-nudge-b`, `plan-boundary`, `plan_boundary`, `handoff-signal`, or `handoff_signal`, case-insensitively. This branch and its worktree are excluded.
- **Overlap rule.** Every open PR overlaps when its `files` list includes a Critical file. Re-read a file list of 100 or more entries with `gh api …/pulls/<n>/files --paginate`.
- **Dispatch condition.**
  - Any open PR naming 1222 blocks dispatch until the engineer confirms that "baselines now" still holds (M13).
  - A matching PR that overlaps blocks until the engineer picks the landing order.
  - A matching branch or worktree with no PR blocks until the engineer decides.
  - A match with no overlap is recorded only.
  - A non-matching PR that overlaps does not block. Whichever lands second rebases, and the PR body names it.
- **Item 6.** If #1222's item 6 text differs from row 33's relayed wording, stop before M9 and report.
- **Record.** Put the record of every PR, branch, and worktree inspected in the dispatch prompt, never in this plan file.

Sync rules:
- Whenever `origin/main` moves past a6a17a9e, sync with `git-feature-branch-sync`.
  - If the new commits touch `claude/.claude/scripts/`, `claude/.claude/tests/`, or `docs/transcript-analysis*.md`:
    - run the abort procedure if step 0 has already run;
    - redo step 0, then the row checks, then Verifications 1, 3, and 7.
  - Otherwise, re-run the row checks and Verifications 1 and 5.
  - The row checks: re-locate every span by its first and last symbol, re-run every grep- or line-cited `[verified]` row, and correct any row whose check changed.
  - #1222 landing first is the expected case for this rule.
- Immediately before opening the PR, re-check `origin/main`. If new commits touch a Critical file, stop and report.

### Create — production

Copy code verbatim. Only these edits are allowed: M3's prefixes, each module's docstring and imports, and wrap-only reflow.

- **`claude/.claude/scripts/transcript_analysis/plan_boundary.py`** (M2, M3). Docstring: `"""The plan-boundary command: cmd_plan_boundary reprices each Opus-anchored session's post-plan-boundary main-thread turns under three arms (continue on Opus, switch to Sonnet in place, fresh Sonnet handoff), plus each arm pair's work-inflation breakeven.\n\nImports handoff_nudge, pricing, render, and scope by module (attribute access, not by name) -- see scope.py's own top-of-file comment for why."""`
- **`claude/.claude/scripts/transcript_analysis/handoff_signal_response.py`** (M2, M3). Docstring: `"""The handoff-signal-response command: cmd_handoff_signal_response audits each observed context-budget signal (the --check result, the advisory injection, the hard-block stop) and whether a same-session /handoff followed it. .claude/plans/handoff-nudge-rationalization-gap.md holds the design.\n\nImports corpus, cost, handoff_nudge, pricing, redaction, render, and scope by module (attribute access, not by name) -- see scope.py's own top-of-file comment for why."""`

### Create — tests

Each file gets:
- a one-line docstring;
- the loader from `test_transcript_subagents.py:29–36`;
- exact-name imports from `.conftest` and `._handoff_nudge_helpers`;
- its own stdlib imports.

Extract each node by AST `lineno`–`end_lineno` from step 0's scratch copy, then apply M6.

- **`claude/.claude/scripts/tests/test_transcript_plan_boundary.py`** takes `_extract_arm_dollars` (:66–72), then :9560–10065. Docstring: `"""Tests for transcript_analysis/plan_boundary.py: boundary-turn location, the three arms' repricing helpers, the work-inflation breakeven, and the report end to end."""`
- **`claude/.claude/scripts/tests/test_transcript_handoff_signal_response_detection.py`** takes `_handoff_hard_block_attachment` (:10129–10146) and the classes at :10159–10642. Docstring: `"""Tests for transcript_analysis/handoff_signal_response.py's per-record extraction: the Bash and tool_use signal classifiers, the excerpt and forward-context windows, and _handoff_signal_response_session_rows' single-pass signal detection."""`
- **`claude/.claude/scripts/tests/test_transcript_handoff_signal_response.py`** takes `_handoff_signal_response_args` (:10072–10086) and the classes at :10645–11449. Docstring: `"""Tests for transcript_analysis/handoff_signal_response.py's reporting side: cmd_handoff_signal_response end to end, spend ranking, curation cards, the startup-burn benchmark, and the aggregate and markdown renderers."""`

### Modify

- **`claude/.claude/scripts/transcript-analysis.py`**: M4. `build_parser()` and every surviving function body stay unchanged.
- **`claude/.claude/scripts/tests/test_transcript_analysis.py`**:
  - Delete :66–74.
  - Delete :9558–11449, so the file ends at :9557 with one trailing newline.
  - Make :9501's banner edit (M11) and :4325's retarget (M7).
  - Remove only the imports F401 then flags. Expected: `random`, `_exit_plan_mode`, and `_ramp_curve_from_records` (row 24).
  - All other content stays byte-identical.
- **`claude/.claude/scripts/tests/_handoff_nudge_helpers.py`**:
  - Append `_check_result_json` (:10089–10099), `_handoff_advisory_attachment` (:10102–10126), and `_check_call_turn` (:10149–10156) verbatim.
  - Add `import json` and `from .conftest import _bash_use, _priced`.
  - Change the docstring (M11).
- **`claude/.claude/scripts/tests/conftest.py`**: M8.
- **`claude/.claude/scripts/tests/test_transcript_ledger_common.py`**: M7's edits to :131–150 only.
- **`claude/.claude/scripts/tests/test_transcript_cli_bootstrap.py`**: M9, appended at the end of the file (795 lines).
- **`claude/.claude/scripts/tests/test_transcript_package_module_prefixes.py`**: M10.
- **`claude/.claude/scripts/transcript_analysis/__init__.py`**: docstring only (M11).
- **`docs/transcript-analysis-architecture.md`**: M11.
- This plan file.

### Explicitly unchanged

- select-tests.py and test_select_tests.py (row 23);
- every existing package module, handoff_nudge.py included, whose docstring names commands, not files;
- docs/transcript-analysis.md and docs/handoff-nudge.md, which name symbols only;
- test_transcript_workstream_cost.py, test_transcript_cost.py, and A's four test files, whose `_mod._model_rates` and `_mod._compute_workstream_dollars` reads keep resolving (row 9);
- the hook, the SKILL.md, and CHANGELOG.md.

### Reuse

- `spend_over_threshold.py:1–14` for the module-header shape.
- `test_transcript_subagents.py:29–36` for the loader.
- conftest's builders.
- The bootstrap file's `_run`, `_seed_priced_account` (:173–198), and `_isolated_config_env` (:201–213).
- A's comparator and capture scripts' shape (A plan Verification 0, 3, 7).

### Dispatch

One `code-writer` dispatch covers every file above (M12). The parent captures step 0 first. Extract moved code from step 0's unmodified scratch copies; never retype it.

- **Stage 1:**
  - Create both production modules.
  - Delete :4032–4973 from the shim.
  - Add both modules to the module-import tuple.
  - Temporarily import all 32 moved names into the shim by name. This block is lint-dirty by design.
  - Leave the seven-name block and `config_dir` in place.
  - Add M11's two `###` sections, because the drift test needs a heading for every module.
  - Run select-tests.py's two passes as in Verification 1.
  - Every selected test must pass except the single node ID in row 15, which must fail. That result is the control for the :9782 retarget.
  - Any other failure goes back to the parent, which reproduces it at the merge-base first.
- **Stage 2:**
  - Create the three test files and the helper-module additions, applying M6.
  - Make M10's change, then run the prefix test alone. All of its tests must pass.
  - Make the legacy, ledger_common, conftest (M7, M8), and bootstrap (M9) edits.
  - Replace the temporary re-export with M4's two final blocks, and delete the seven-name block.
  - Before the remaining M4 import edits, run `ruff check --select F401` on the shim. It must flag exactly row 8's six names. Anything else: stop and re-check rows 8–9.
  - Finish M4.
  - Make the remaining M11 edits.
- **Abort procedure.** On a blocked return or an abnormal termination, the code-writer runs no cleanup and returns to the parent. The parent then:
  1. Builds its path lists from `git status --short` and `git diff --cached --name-only`, then removes this plan file's path from both lists. The plan file is never an argument to any step below.
  2. Runs `git restore --staged -- <path>…` as a literal call on each remaining staged path.
  3. Copies each step 0 snapshot back over its file.
  4. Deletes each created file by path.

  The parent never runs `git clean`, `git checkout .`, `git reset --hard`, or `git stash`. Success check: `git status --short` lists only the plan file. If the tree holds anything this plan doesn't name, the parent reports to the engineer first.
- **PR body:**
  - the Stage 1 result and the F401 set;
  - M3 and M6 reflow counts, reported separately;
  - measured `wc -l` for every new and shrunk file;
  - `git blame -C -C -s` counts;
  - the incidental edits outside the moved slices, each with M7's or M11's reason: legacy :4325, the ledger_common test rename, the :9501 banner, and the tuple comment's corpus correction;
  - each overlapping non-matching PR;
  - the #1222 note, covering four points:
    - baselines were taken with #1222 open, per the engineer's "I think baselines now" and this plan's agreement;
    - this departs from #1116's "lands before the next phase takes its baselines";
    - #1222 rebases onto this phase, which edits conftest.py, test_transcript_cli_bootstrap.py, and the prefix test's tuples;
    - M9's docstrings already follow item 6;
  - the rollback statement (row 36), with the last-in-first-out note: this phase's tests import A's `_handoff_nudge_helpers.py`;
  - the stow line in A's form, naming the two new modules;
  - the follow-ups in Out of scope.

## Verification

Run everything from the worktree root. `<venv>` is `../../../.venv`. Scratch files live outside the repo, and every corpus is synthetic. Every CLI capture runs as `<venv>/bin/python3 claude/.claude/scripts/transcript-analysis.py <args>`, only through step 0's capture script. Run each git command as its own literal call.

0. **Baseline, at a6a17a9e or later, before the dispatch.**
   - Copy every file in Modify to scratch.
   - Save `--collect-only -q` IDs for the legacy file, the bootstrap file, the prefix test, and the ledger_common test.
   - Run the legacy file and the ledger_common test in two passes:
     - `-v -m "not timing" --junitxml=<scratch>/before-parallel.xml`;
     - `-v -m timing -n0 --junitxml=<scratch>/before-timing.xml`.
   - Record `id -u`.
   - Write a seeding script and a capture script. The capture script pins:
     - `CLAUDE_CONFIG_DIR` to the account;
     - `TRANSCRIPT_CONFIG_DIRS_FILE` to a nonexistent path;
     - `COLUMNS=100`;
     - `PYTHONHASHSEED=0`.

     It writes stdout, stderr, and the exit code of each run.
   - The account has one project, `-home-user-parityrepo`. Every timestamp falls on one literal date, `<d>`, and every token count is a literal. All three sessions are on branch `feat`, which gives the benchmark two continuations.
     - `s-plan`:
       - claude-opus-5 turns, separated by genuine user messages, with input/output/cache_read of 1,000/100/50,000, then 2,000/200/100,000 carrying an `ExitPlanMode` tool_use;
       - a claude-sonnet-5 turn of 500/300/150,000 whose `message.diagnostics.cache_miss_reason.type` is `model_changed`;
       - a claude-opus-5 turn of 500/400/155,000.
     - `s-ramp`: claude-sonnet-5 turns with output 300, 400, and 500, each with input 10,000.
     - `s-signal`, on claude-sonnet-5:
       - a turn with input 200,000 calling `~/.claude/hooks/nudge-handoff-near-context-cap.sh --check` as tool `chk1`;
       - its tool_result `{"status":"ok","over_threshold":true,…}`;
       - a turn running `~/.claude/scripts/marker.sh activate ready-for-review`;
       - a `hook_success` attachment from the hook with an `additionalContext` payload;
       - a turn with a text block and a thinking block;
       - a `hook_stopped_continuation` attachment containing `handoff-nudge hard-block point`;
       - a turn with a `Skill` `handoff` tool_use.
     - `.handoff-nudge.log` in the account root holds:
       - `nudged session=s-signal est=150000 model=claude-sonnet-5 window=1000000 event=Stop`;
       - a `schema-drift` line;
       - a `nudged` line for a session not in scope.
   - Runs:
     - `--help` for the top level, `plan-boundary`, and `handoff-signal-response`;
     - (a) `plan-boundary`
     - (b) `plan-boundary --no-redact`
     - (c) `plan-boundary --since 36500d`
     - (d) `handoff-signal-response`
     - (e) `handoff-signal-response --no-redact`
     - (f) `handoff-signal-response --sample 5 --seed 1`
     - (g) `handoff-signal-response --sample 5 --seed 1 --format md --context-turns 2`
     - (h) `handoff-signal-response --context-turns 2`, which must exit 2
     - (i) `handoff-signal-response --sample 5 --seed 1 --no-redact`
   - Run the capture script twice into two directories, and diff them. Any difference stops the plan.
   - Record a `sha256sum` manifest of the account, and `wc -l` for the shim, the legacy file, and conftest.
1. **Scoped suite.**
   - Stage the five created files with `git add -- <paths>`.
   - Run `<venv>/bin/python3 claude/.claude/scripts/select-tests.py -v -m "not timing" --junitxml=<scratch>/after-parallel.xml`, then the same command with `-v -m timing -n0 --junitxml=<scratch>/after-timing.xml`. Every selected test must pass in both.
   - The output must not show a `global-trigger` line (row 23).
   - Grep the `-v` node IDs to confirm the run collected:
     - `claude/.claude/scripts/tests/`;
     - the prefix test;
     - `evals/test_review_bench_*.py` and `evals/test_measure_subagent_model_resolution.py`, which `__init__.py` selects and CI never runs.
2. **Test-ID parity.** Strip each ID's file prefix and compare sorted lists.
   - Step 0's legacy IDs must equal the post-move legacy IDs plus the three new files' IDs.
   - The bootstrap file gains exactly M9's four IDs.
   - The prefix test's IDs are unchanged.
   - The ledger_common test differs by exactly M7's one rename.
   - Per-ID outcomes, at the same `id -u`, must match the union of step 0's XMLs. The renamed ID is compared to its old name.
3. **CLI parity.**
   - First confirm the account still matches step 0's manifest.
   - Re-run the capture script at the same path. Every capture must match step 0 byte for byte.
   - The one exception: the `generated YYYY-MM-DD` substrings in (a)–(c) and the `Generated: YYYY-MM-DD` substring in (g). One scratch `sed` script masks them identically in both captures.
4. **Negative controls.** Before each control, record `sha256sum` of every file it edits. Edit by hand, run the check, undo by hand, and confirm the hashes match. Never use `git stash` or `git checkout`.
   - (a) Delete `TestRootsThreadingSpy`'s two `_mod.scope` setattr lines (:8019–8020). The plan-boundary parametrized node must fail with `_resolve_project_scope was never called`. Assert only that node.
   - (b) In handoff_signal_response.py, add `from _config_dir import config_dir` and call bare `config_dir()`. `test_log_diagnostic_reports_joined_count_and_median_lag` must fail (row 28).
   - (c) Delete `_plan_boundary_report`'s inline guard. `test_no_redact_refused_by_report_itself_when_multi_root` must fail, while `test_no_redact_refused_with_multi_root` still passes.
   - (d) Delete `cmd_handoff_signal_response`'s inline `--no-redact` guard. Both `test_no_redact_refused_with_multi_root[0]` and `[5]` must fail.
   - (e) Point `p_plan_boundary.set_defaults(func=...)` at `cmd_handoff_signal_response`. M9's plan-boundary seeded test must fail, while its `--help` test passes.
   - (f) In plan_boundary.py, change `scope.PROJECTS_DIR` to `scope.PROJECTS_DIRS`. `test_production_modules_reference_only_real_sibling_attributes` must fail, naming it.
   - (g) In ledger_common.py:59, add `from transcript_analysis import scope` and route the call through `scope.config_dir()`. M7's renamed test must fail.
   - Stage 1's result is the control for the :9782 retarget. It is not repeated here.
5. **Lint.** `<venv>/bin/ruff check claude/.claude/ claude-skills/ plugins/` must be clean. This is CI's command.
6. **Leftovers.**
   - `git grep -nE '^(def |class )?(<32 names>)\b' claude/.claude/scripts/transcript-analysis.py` returns nothing. Each of the 32 names is defined exactly once under `transcript_analysis/`.
   - `git grep -nE '_mod\.(<32 names>)\b' claude/.claude/scripts/tests/` returns only legacy :7815.
   - `git grep -nE '_mod\.(<seven core names>)\b' claude/.claude/scripts/tests/` returns nothing.
   - `git grep -nE 'setattr\((_mod|mod), "config_dir"' claude/.claude/scripts/tests/` returns only test_select_tests.py's two lines.
   - `git grep -nE '^from _config_dir|^import statistics|transcript_analysis\.handoff_nudge import' claude/.claude/scripts/transcript-analysis.py` returns nothing.
   - Each moved helper is defined exactly once.
   - `git grep -nE 'seven names|still-independent .config_dir|handoff-signal-response still lives|five .config_dir. bindings' docs/ claude/.claude/scripts/` returns nothing, and `docs/transcript-analysis-architecture.md` :16–20 no longer lists `handoff_nudge.py`. The doc-drift test checks headings only, so this grep is the only check on stale prose.
7. **Move fidelity.**
   - **Comparator check first.** Seed one scratch defect per verifier path, each in a scratch copy, never in the worktree. The comparator must report each defect and name the check that caught it:
     - Production AST: a deleted statement in `_plan_boundary_report` and another in `_handoff_signal_response_session_rows`.
     - Production comment diff: one changed constant comment.
     - Import set: an added `from _config_dir import config_dir`.
     - Tests AST:
       - a deleted `assert`;
       - a changed string literal;
       - the removed `@pytest.mark.parametrize("sample", [0, 5])` at :10657;
       - an extra method.
     - Test comment diff: one changed `#` comment inside a moved test.
     - Helper byte-for-byte: one changed character in `_check_call_turn`.
     - Node count: one moved class dropped, which gives a count of 64.
     - M6 reversal: `_mod.plan_boundary._handoff_signal_excerpt`.
     - Legacy remainder: one deleted remaining test.
     - Helper-module remainder: one changed character in `_spend_over_threshold_args`.
     - Shim remainder: one changed literal in `build_parser()`.
   - **Node count.** Every real run reports a compared-node count of 65:
     - 32 production nodes;
     - 9 test helpers;
     - 24 test classes.
   - **Import set.** Each new module's import statements must equal M3's lists exactly.
   - **Production.**
     - Reverse M3's map by name, including the three aliases. Never strip the prefixes that already existed at base (`scope.PROJECTS_DIR` and the two `corpus.` reads).
     - Each node's `ast.dump` must equal its step 0 span.
     - The `#`-comment diff may differ only in the dropped banners at :4032 and :4349–4350.
   - **Tests.**
     - Reverse M6 by name, and reverse the :9782 string-target setattr by node ID only.
     - Each class and function AST must equal its legacy slice.
     - These helpers must match byte for byte: `_extract_arm_dollars`, `_plan_boundary_args`, `_opus_boundary_session`, `_handoff_signal_response_args`, `_check_result_json`, `_handoff_advisory_attachment`, `_handoff_hard_block_attachment`, `_check_call_turn`.
     - The comment diff may differ only in the dropped banner at :10068–10070.
   - **Remainders.**
     - Shim: step 0's shim with :4032–4973 removed must equal the post-move AST once every import statement is excluded.
     - Legacy: step 0's copy with :66–74 and :9558–11449 removed may differ from the post-move file only in import lines, :9501, and :4325.
     - Helper module: the post-move AST, minus the three appended functions, the two added imports, and the docstring, must equal step 0's.
     - The ledger_common and conftest diffs must contain only M7's and M8's lines.
   - After the commit, report `git blame -C -C -s` counts for each new file.
8. **Sizes.** Report `wc -l` for every new and shrunk file. Every new file must be at most 1,000 lines.
9. **Post-merge revert rehearsal** (M15; transcript-analysis-decomposition.md:276–279).
   - After the human merges, in a scratch worktree at the merge commit, `git revert` the squash commit and run CI's two passes over the full suite. That is the governing plan's prescribed whole-repo case.
   - Waiving it as A did needs the engineer's answer.

## Out of scope

- **Amending #1116's #1222 line.** That is tracker text, and the engineer edits it. The PR body records the departure (M13).
- **#1222's own items.**
- **`TestCacheMissReason` stays in the legacy file.** It tests pricing.py, which the architecture doc says the legacy suite exercises (:414–415). Only its banner changes (M11).
- **`_UNCONDITIONAL_HEADER_CASES`' plan-boundary row (:7815) and the two parametrized tests that read it.** They span groups, so the `cli.py` phase owns them. `handoff-signal-response` appears in neither that table nor `TestRootsThreadingSpy`; the `cli.py` phase owns deciding whether to add it.
- **Retargeting the remaining `_mod._model_rates`, `_mod._compute_workstream_dollars`, and `_mod._cache_miss_reason` reads to their owning modules.** That would let the shim drop three imports that now carry `# noqa`, but it means about 30 reads across five files outside this phase's slices (row 9).
- **Stale prose that moves verbatim:**
  - "in this file" at shim :4399, :4518, and :4882–4883;
  - the hook line citations at :4365 ("around line 647") and legacy :10132 ("lines 644-649"), which were not re-checked;
  - `_ramp_curve_from_records`' docstring naming one class;
  - the ledger_common test docstring's "patches both bindings to the same directory" clause. It was already loose before this phase, because `cost_ledger_enabled` patches ledger_common to a directory other than `fake_projects`' `tmp_path`.
- **require-stow-reminder.sh's header claim** that every nested new file needs a re-stow. A raised it, and it stays with the engineer.
- **Sizes of the shim, the legacy file, and conftest.** Each was over 1,000 lines before this phase.
- **A CHANGELOG entry.** No earlier decomposition phase added one.
- **Any CLI surface change.**
