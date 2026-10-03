# Decompose the transcript-analysis dispatch group

## Context

Goal: move the dispatch command group (`subagents`, `subagent-mix`, `cost-counts`) out of the `transcript-analysis.py` shim into the `transcript_analysis` package, co-moving its production code and its test slice, as the next phase of tracking issue #1116.

Ask: "Follow up PR should decompose transcript analysis where this PR touched that file and test transcript analysis" (engineer's review of #1194); "Both in sequence and start with whichever one touches the most code. Don't be influenced by my comment"; "Yes" to starting `/plan-it` for this work.

The dispatch group goes first because the session found #1194 changed more code there (the `_dispatch_usage_summary` dedup fix and the subagent-mix test slice) than in `cmd_duration` (a single column rename, owned by #1116's session-signal phase and planned separately afterward). The unit is the whole group, not only the functions #1194 touched, per the governing plan's rule that each PR moves one group's production code and its test slice together. This phase must not run concurrently with #1175 (handoff-nudge A, still open).

## Approach

The dispatch group moves out of the shim into two new package modules, copied verbatim in source order:
- `transcript_analysis/subagents.py` takes `cmd_subagents` and `_MCP_TOOL_BUCKET_LABEL`.
- `transcript_analysis/subagent_mix.py` takes `cmd_subagent_mix`, `cmd_cost_counts`, `_dispatch_usage_summary`, and every helper only they use.

The tests move into four new test files, split at command seams, plus a small family helper module, `tests/_subagent_helpers.py`. The shim keeps `build_parser()`. It imports the three `cmd_*` names by name, plus `_MCP_TOOL_BUCKET_LABEL` for the context-composition code that has not moved yet.

The Step 3 evidence needed eight corrections. Each one changes the plan:
- **`scripts/tests/` is a package.** `claude/.claude/scripts/tests/__init__.py` exists, so same-directory imports must be relative (row 21).
- **The four `_subagents_args` uses at legacy :8783–8840 are in `TestFormatDriftCanary`, not skill-pair.** That class covers subagents, skill-pair, and cache-efficiency, so it stays. `_subagents_args` therefore needs a home both the legacy file and the new files can import (rows 17, 20).
- **Block C (`TestSubagentFormatContract`) is not a dispatch-group test.** It pins corpus.py's on-disk contract and reads no dispatch-group name, so it stays for the leaf-module-tests phase (row 18).
- **Two string-target monkeypatches need retargeting.** Step 3 missed them: `setattr(_mod, "open", …, raising=False)` at :1873 and `setattr(_mod, "_partition_spawn_counts_by_disclosure", …)` at :2485 (row 22).
- **`cmd_cost_counts` reads `scope.config_dir()` by attribute, so conftest's `fake_projects` needs no new patch.** The engineer chose this over a by-name binding (rows 12, 59).
- **`_priced_sidechain_asst` goes to conftest, not the family helper module.** It is a record builder that rearm-backtest tests also call, at :12698 (rows 20, 25).
- **The shim loses six imports and keeps two only for tests** (row 14).
- **Seven cross-file pointers go stale, and one more evals docstring was already stale.** One of the seven, test_pr_cost_section.py :803–807, also cites a test that does not exist, so M10 rewrites it rather than re-pointing it. conftest :529–533 needs no edit (rows 26, 57).

No stow consumer sees a change. CLI help, stdout, and exit codes stay byte-identical, and so does stderr on every non-crash path (Verification 3). An uncaught exception's traceback names the new module files instead of the shim. The only non-test reader of cost-counts' stderr discards it (row 52).

Alternatives considered and set aside:
- **One production module.** It would be about 890 lines and fit under the limit. But `cmd_subagents` shares no helper with the other two commands. This is the same reasoning that split cost_ledger.py from workstream_cost.py (M2).
- **A third module, `cost_counts.py`.** It would need extraction from non-contiguous ranges and a command-to-command import of `_UNKNOWN_SUBAGENT_TYPE`. That constant's own comment says it exists so the two counting paths agree, which is an argument for one home (M2).
- **Moving only `_dispatch_usage_summary` or only subagent-mix.** See M1.
- **One or two test files.** The slice is about 2,440 lines (M6).

### Assumption ledger

**Root:** The dispatch group still lives in the shim:
- `cmd_subagents`: :871–1073
- two labels: :1076–1083
- `cmd_subagent_mix` through `_dispatch_usage_summary`: :1465–2119

Its roughly 2,440-line test slice still lives in the legacy file: :995–2932 minus a redaction island, :8299–8745, and :10630–10842. `[verified: Read of every span; top-level and class-def greps at d48a31f8]`

**Givens:**
- G1. pytest's `prepend` import mode imports test modules by basename, so new test files keep the `test_transcript_*` prefix and unique basenames. Reason: pytest owns this behavior. `[verified: transcript-analysis-decomposition.md:59–63]`
- G2. `from m import n` binds `n` at import time. A monkeypatch therefore reaches code only through the binding that code reads at call time. Reason: Python language semantics. `[verified: transcript-analysis-decomposition.md row 1]`
- G3. A directly invoked `python3 transcript-analysis.py` finds the package only through CPython's `sys.path[0]`. Reason: CPython owns script bootstrap. `[verified: test_transcript_cli_bootstrap.py:1–11]`

Four conditions look like givens but are not. This repo owns each one, so each is a deliberate decline listed in **Out of scope**:
- `build_parser()` staying in the shim;
- the cross-command test classes and `_UNCONDITIONAL_HEADER_CASES` staying in the legacy file;
- #981's counting behavior;
- ruff's line length.

**Mechanisms:**

- **M1: Scope is the whole dispatch group.** That means subagents, subagent-mix, and cost-counts. `anchors: root, row1, row2, row3, row4`
  - Lighter, rejected: move only `_dispatch_usage_summary`. Its only caller is `cmd_subagent_mix` (:1647), and package modules may not import from the shim.
  - Lighter, rejected: move only subagent-mix and cost-counts. That leaves `TestSubagentsDeclaredRootsMultiRoot` covering one moved and one unmoved command, so it would have to stay or split. It also leaves a roughly 225-line `cmd_subagents` for its own phase, which would pay a whole phase's fixed overhead.
- **M2: Two production modules, split at the command seam, each filled by line-range extraction.** `anchors: row5, row6, row7, row36`
  - `subagents.py` takes :871–1073, then :1076–1079 (the `_MCP_TOOL_BUCKET_LABEL` comment and constant).
  - `subagent_mix.py` takes :1081–1083 (the `_UNREQUESTED_MODEL_LABEL` comment and constant), then :1465–2119.
  - Lighter, rejected: one module (see alternatives above).
  - Heavier, rejected: a separate `cost_counts.py` (see alternatives above).
- **M3: Where the shared names go.** `anchors: row6, row9`
  - `_MCP_TOOL_BUCKET_LABEL` goes to `subagents.py`. The shim imports it by name for `_normalize_composition_tool_name` (:3497). This mirrors `_READ_SCOPE_CHARS_PER_TOKEN`, which read_scope.py owns and composition reads through the shim (shim :161–167; architecture doc :24–25).
    - Rejected: leaving it in the shim. Package modules cannot import from the shim.
    - Rejected: moving it into `render.py`. That edits an existing leaf beyond a move. The composition phase can re-home it when it moves.
  - `_UNREQUESTED_MODEL_LABEL` goes to `subagent_mix.py`. Its only reader is :1657.
- **M4: Import discipline and the rename maps.** `anchors: G2, row10, row11, row12, row15`
  - `subagents.py`:
    - `from transcript_analysis import corpus, pricing, redaction, render, scope`
    - stdlib: `argparse`, `sys`, `defaultdict`, `Path`.
  - `subagent_mix.py`:
    - `from transcript_analysis import corpus, pricing, redaction, render, review_rounds, scope`
    - stdlib: `argparse`, `json`, `re`, `sys`, `defaultdict`, `datetime.{UTC,date,datetime}`, `Path`.
  - Neither module has a `from transcript_analysis.<m> import` line.
  - Rename map:
    - These go to `scope.`: `_resolve_cost_roots`, `_branch_filter`, `_parse_since_nd_arg`, `_parse_absolute_window_args`, `_DO_NOT_PUBLISH_BANNER`, `_resolve_project_scope`, `_redaction_ordinals`, `_root_index_for_path`, `config_dir`.
    - `_print_resolved_scope` becomes `scope.print_resolved_scope`.
    - These go to `corpus.`: `_parse_ts`, `_index_subagent_dispatches`.
    - These go to `pricing.`: `_count_subagent_spawns`, `_warn_if_subagent_format_drift`, `_SPAWN_TOOL_NAMES`, `_MODEL_BASE_INPUT_RATES`, `_MODEL_RATE_EXPIRES`, `_PRICING_SOURCE_URL`, `_price_turn`.
    - `_dedup_turns_by_request_id` becomes `pricing.dedup_turns_by_request_id`.
    - These go to `redaction.`: `_root_scoped_display_label`, `_repo_tracked_agent_type_names`.
    - These go to `render.`: `_fam`, `_content_text`, `_sanitize_table_cell`, `_fmt_usd`.
    - These go to `review_rounds.`: `REVIEW_SKILLS`, `compute_review_round_counts`.
    - Unchanged: every stdlib name and every name the module defines itself.
  - Prefix code references only. Docstrings name several of these symbols in prose, so let ruff F821 drive every prefix and never run a regex over bare names.
  - Wrap-only reflow is allowed if E501 flags a prefixed line. Report the count.
  - Chosen: `config_dir` is read as `scope.config_dir()`, by attribute, not through a by-name binding from `_config_dir` (row 59). It is one more rename-map entry. It is behavior-identical for cost-counts: `scope.config_dir` and the shim's `config_dir` are the same `_config_dir.config_dir` function, which reads `CLAUDE_CONFIG_DIR` at call time, and nothing in production reassigns either name. It removes a `fake_projects` line, a doc paragraph, a negative control, and a silent-failure mode where a future test patches only `scope.config_dir`. It departs from cost_ledger.py, ledger_common.py, and pr_cost_ledger.py, which bind it by name for their own roots.
- **M5: Shim changes.** `anchors: row6, row9, row14`
  - Delete :871–1085, leaving two blank lines between `cmd_duration` and `cmd_judgment_pair`.
  - Delete :1465–2121, leaving two blank lines between `cmd_skill_invocation` and `cmd_skill_pair`.
  - Add `subagent_mix` and `subagents` to the module-import tuple (:35–56) and to its comment (:30–34).
  - Add two by-name import blocks in the file's commented style:
    - `cmd_cost_counts` and `cmd_subagent_mix` → `build_parser`'s `set_defaults`.
    - `_MCP_TOOL_BUCKET_LABEL` → `_normalize_composition_tool_name`; `cmd_subagents` → `p_sub.set_defaults`.
  - Delete `_index_subagent_dispatches`, `_MODEL_BASE_INPUT_RATES`, `_MODEL_RATE_EXPIRES`, `_PRICING_SOURCE_URL`, `_root_scoped_display_label`, and `compute_review_round_counts`.
  - Keep two imports that only staying legacy tests read, each with a noqa comment:
    - `_repo_tracked_agent_type_names  # noqa: F401 -- read only via _mod._repo_tracked_agent_type_names from test files`
    - `_sanitize_table_cell  # noqa: F401 -- read only via _mod._sanitize_table_cell from test files`
  - Run ruff's autofix only as `--select I --fix`. If F401 flags any name outside these eight, stop and re-check row 14.
- **M6: Test layout: four files split at command seams, plus `tests/_subagent_helpers.py`.** `anchors: G1, row16, row20, row21, row36`
  - The helper module holds only helpers used by more than one file, or by the legacy file plus a new one: `_sum_column_across_rows`, `_subagent_mix_args`, `_subagents_args`.
  - Every other family helper stays local to the one file that uses it.
  - The subagent-mix tests split at the dollar-column seam. `TestSubagentMixDollars` is the only caller of `_priced_sidechain_asst` within the slice, and nothing else crosses that seam. `TestDispatchUsageSummaryDedupBeforePricing` names the builder only in a docstring (row 20).
  - Rejected: one or two files. The slice is about 2,440 lines, and subagent-mix alone is about 1,480.
  - Rejected: importing from the legacy test module. Read-scope, pr-cost, cache-rebuild, and audit-routing all rejected it.
- **M7: Tests that stay in the legacy file.** `anchors: row17, row18, row19`
  - These stay as they are: `TestFormatDriftCanary`, the `_UNCONDITIONAL_HEADER_CASES` rows at :9992–9994, and `TestSubagentFormatContract`.
  - These redaction-leaf classes also stay: `TestRootScopedDisplayLabel` and `TestRepoTrackedAgentTypeNames` (:2147–2331).
  - The `# subagent-mix` banner (:995–997) currently sits above the redaction island. Replace it with a three-line banner in the file's `# ---` style, directly above `TestRootScopedDisplayLabel`, reading `# redaction: root-scoped display labels and the --this-repo agent-type allowlist`.
- **M8: Test-side retargets in moved code.** `anchors: G2, row22, row23, row24, row45, row51`
  - `_mod.cmd_subagents` → `_mod.subagents.cmd_subagents`.
  - `_mod._MCP_TOOL_BUCKET_LABEL` → `_mod.subagents._MCP_TOOL_BUCKET_LABEL`.
  - `_mod.<any of the 15 subagent_mix names>` → `_mod.subagent_mix.<name>`.
  - `_mod._MODEL_RATE_EXPIRES` (:1883) → `_mod.pricing._MODEL_RATE_EXPIRES`.
  - `_mod._repo_tracked_agent_type_names` → `_mod.redaction._repo_tracked_agent_type_names`, at all eight reads in the moved ranges: :2342, :2344, :2347, :2380, :2382, :2393, :2597, and :2599. The new files then reach that function only through its owning module, and only the staying `TestRepoTrackedAgentTypeNames` reads the shim's passthrough (row 14).
  - `monkeypatch.setattr(_mod, "open", …)` (:1873) → `monkeypatch.setattr(_mod.subagent_mix, "open", …)`. Keep `raising=False` and the comment.
  - `monkeypatch.setattr(_mod, "_partition_spawn_counts_by_disclosure", …)` (:2485–2488) → `monkeypatch.setattr(_mod.subagent_mix, …)`.
  - Leave these unchanged:
    - reads of names the shim still binds: `_mod._parse_ts` (four reads, :2030–2031 and :2065–2066), `_mod._BUILT_IN_AGENT_TYPES`, `_mod._DO_NOT_PUBLISH_BANNER`, `_mod.SUBAGENT_SUBDIR`, `_mod._cost_report`, `_mod.main`;
    - module-object patches: `_mod.redaction.*`, `_mod.scope.*`, `time.time`, `sys.argv`.
  - Wrap-only reflow is allowed when a retarget pushes a line past 130 characters. Legacy :2086 is the known case: it becomes 131 (row 51). Report the count.
- **M9: conftest.** `anchors: row12, row20, row25`
  - Insert `_priced_sidechain_asst` byte-identical (legacy :122–137) directly after `_priced`. This follows audit-routing's M5 rule: record builders used by more than one command group live in conftest.
  - Leave `fake_projects` unchanged. `cmd_cost_counts` reads `scope.config_dir()`, which `fake_projects` already patches (row 12).
  - Add the four new test files to the module docstring's consumer list (:4–20). The helper module imports nothing from conftest, so it is not listed.
  - Rejected: putting the builder in the family helper module. `TestExtractRearmSessionTurnsModelAndPosition` (:12698) would then import a dispatch-family helper.
  - Rejected: duplicate copies. Record builders are not a DAMP exception.
  - Cost: conftest grows by about 18 lines past a limit it already exceeds.
- **M10: Re-point the docstring and comment pointers at the moved tests and code.** `anchors: row26, row44, row57, row58`
  - `test_pr_cost_section.py`:
    - :396–397 and :692–693 → `test_transcript_cost_counts.py`.
    - :803–807, `TestCombinedZeroState`'s docstring, cites two Python-level zero-state tests, but only the spawns one exists (row 57). Rewrite it to this text, wrapped at the docstring's width: "The one table-to-non-table seam: the rounds table's fixed zero-count rows immediately followed by the spawns section's bare sentence with no table at all. At the Python level, test_transcript_cost_counts.py's test_zero_spawns_renders_the_sentence_not_a_table pins only the spawns side, never the two as one rendered body." List it under "Incidental edits" in the PR body.
  - `evals/test_measure_subagent_model_resolution.py:199` → `test_transcript_subagent_mix.py::TestSubagentMixModelMix::test_non_string_meta_model_does_not_crash_the_run`.
  - `evals/measure_subagent_model_resolution.py`:
    - :20–22 names both mirrored functions in one sentence. Point it at `claude/.claude/scripts/transcript_analysis/corpus.py` for `_index_subagent_dispatches` and `claude/.claude/scripts/transcript_analysis/subagent_mix.py` for `_dispatch_usage_summary`.
    - :250–251, a `#` comment naming `_agent_frontmatter_model` → `claude/.claude/scripts/transcript_analysis/subagent_mix.py`.
    - :412–413 → `subagent_mix.py`.
    - :454–455 → `corpus.py`. This line was already stale; it is the same defect in the same file. List it under "Incidental edits" in the PR body.
    - :57 stays as it is (Out of scope, row 58).
- **M11: Six bootstrap tests.** `anchors: G3, row27`
  - `--help` tests:
    - `subagents --help` asserts `--since`;
    - `subagent-mix --help` asserts `--reprice-as`;
    - `cost-counts --help` asserts `--branches`.
  - Seeded runs, reusing `_seed_reviewer_dispatch_account` and `_isolated_config_env`:
    - `subagents`: exit 0, and `sidechain` appears in stdout, which proves the paired subagent file was read.
    - `subagent-mix`: exit 0, and `staff-backend-engineer(1)` appears in stdout.
  - `cost-counts --this-repo` with no `--branches`: exit 2, and `--branches is required` appears in stderr. This refusal path is the bootstrap proof for cost-counts. Its success path needs `--this-repo`, which resolves slugs from `git worktree list` in the working directory; the docstring says so.
  - Each docstring states the fact it proves, matching :653–658.
- **M12: Docs and the package docstring.** `anchors: row23, row28, row54, row56`
  - `__init__.py`: add `subagents` and `subagent_mix` to the command-group module list.
  - Architecture doc:
    - In :16–18, add both modules to the list of modules the shim imports back into.
    - After :37, add: `build_parser()` likewise wires up `subagents.py`'s `cmd_subagents` and `subagent_mix.py`'s `cmd_subagent_mix`/`cmd_cost_counts` from the shim. Then add: the still-unmigrated context-composition code reads `subagents.py`'s `_MCP_TOOL_BUCKET_LABEL` by name from the shim.
    - Rewrite :75–76, :92, and :140 so they name the new modules and the prefixed reads (`redaction._repo_tracked_agent_type_names`, `review_rounds.compute_review_round_counts`) instead of "the shim's".
    - After `workstream_cost.py`, add `### \`subagents.py\`` and `### \`subagent_mix.py\`` sections. They land in Stage 1 (M14). Cover:
      - each module's responsibilities and its module imports;
      - `subagent_mix.py`'s `config_dir` read. State that `cmd_cost_counts` reads `scope.config_dir()` by attribute, per the rule at :63–65, so `fake_projects`' existing `scope.config_dir` patch isolates it (rows 12, 56);
      - the names each module exposes bare to the shim.
    - Name the projects directory by its code expression, `config_dir() / "projects"`, never `~/.claude/projects/`, which the doc state-path scan rejects (row 54).
    - In Tests:
      - add `_priced_sidechain_asst` to conftest's shared list (:362–365);
      - add a paragraph naming the four files, their seams, the helper module, and why `TestFormatDriftCanary` and `TestSubagentFormatContract` stay;
      - update :411–413 for the renamed prefix test (M13).
- **M13: Extend the committed prefix test instead of adding a second one.** `anchors: row29`
  - The parent runs `git mv` on `test_transcript_cost_ledger_module_prefixes.py` to `test_transcript_package_module_prefixes.py` once, after step 0's snapshots and before the dispatch. The code-writer's charter forbids staging, so it edits the file at its new name.
  - Add `subagents.py` and `subagent_mix.py` to `PRODUCTION_MODULES`. Add the four new test files to `TEST_FILES`.
  - Reword the module and function docstrings to say "each module in `PRODUCTION_MODULES`" instead of naming two modules.
  - Rejected: a new per-phase file. It would duplicate about 140 lines of AST logic.
  - Rejected: extending without renaming. The filename would then misdescribe what the file checks.
  - Rejected: a scratch-only check, which cost-ledger's M11 already rejected.
  - Earlier phases' modules stay out of scope.
- **M14: One `code-writer` dispatch in two internal stages.** `anchors: row22, row23`
  - Stage 1 moves the production code, adds the two architecture-doc module sections the drift test requires, and keeps the legacy tests unedited as an oracle. Stage 2 moves the tests.
  - Rejected: sequenced dispatches. Both stages edit the shim, conftest, and the legacy file.
- **M15: No `select-tests.py` change.** `anchors: row30`

**Assumptions:**

1. The engineer's #1194 review body reads "Follow up PR should decompose transcript analysis where this PR touched that file and test transcript analysis". `[verified: the review body on PR #1194, fetched via gh api this session]`
2. `[engineer-verified: "Both in sequence and start with whichever one touches the most code. Don't be influenced by my comment"]`
3. #1194 changed more code in the dispatch group than in `cmd_duration`, so the dispatch group goes first. This is the session's claim. `[unverified — I could not run git; three plan-review reviewers ran git show -U0 3805f43f read-only this session and agree: on the shim, about 11 changed lines in cmd_duration against about 96 in _dispatch_usage_summary, and in the legacy test file about 42 against about 400]`
4. The group is subagents, subagent-mix, and cost-counts, as #1116 defines it. `[unverified — relayed; I can't read #1116]` Widening from "where this PR touched" to the whole group began as the session's inference. The engineer settled it by selecting "Whole group (Recommended)" (row 39). Supporting facts:
   - `_UNKNOWN_SUBAGENT_TYPE` is shared by `cmd_subagent_mix` (:1633) and `_spawn_counts_by_agent_type` (:1798), and its comment says why (:1825–1828).
   - `TestSubagentsDeclaredRootsMultiRoot` tests both subagents and subagent-mix (:10640, :10658).
   - `cmd_subagents` reads only `_MCP_TOOL_BUCKET_LABEL` from the group.

   `[verified: Read]`
5. Spans. `[verified: Read]`
   - :871–1073: `cmd_subagents`.
   - :1076–1079: `_MCP_TOOL_BUCKET_LABEL` and its comment.
   - :1081–1083: `_UNREQUESTED_MODEL_LABEL` and its comment.
   - :1086: `cmd_judgment_pair` (stays).
   - :1465–2119: `cmd_subagent_mix` through `_dispatch_usage_summary`.
   - :2122: `cmd_skill_pair` (stays).
6. 17 top-level names move.
   - To `subagents.py`: `cmd_subagents`, `_MCP_TOOL_BUCKET_LABEL`.
   - To `subagent_mix.py`: `_UNREQUESTED_MODEL_LABEL`, `cmd_subagent_mix`, `_spawn_counts_by_agent_type`, `_AGENT_FRONTMATTER_MODEL_RE`, `_agent_frontmatter_model`, `_DECLARED_PIN_BUILT_IN`, `_UNKNOWN_SUBAGENT_TYPE`, `_AGENT_TYPE_NAME_RE`, `_declared_pin`, `_WITHHELD_AGENT_TYPE_LABEL`, `_partition_spawn_counts_by_disclosure`, `_COST_COUNTS_ROUNDS_CAPTION`, `_COST_COUNTS_SPAWNS_CAPTION`, `cmd_cost_counts`, `_dispatch_usage_summary`.
   - Outside the spans, the shim reads only these: `set_defaults` at :6962, :7012, and :7716, and `_MCP_TOOL_BUCKET_LABEL` at :3497.

   `[verified: top-level grep; grep of each name]`
7. Estimated sizes:
   - `subagents.py`: 207 moved lines, about 225 with its header.
   - `subagent_mix.py`: 658 moved lines, about 680 with its header.

   `[verified: line arithmetic over row 5; header size estimated from workstream_cost.py:1–16]`
8. No non-test code outside the shim reads a moved name in code. These mention moved names only in comments or docstrings, and every function they name still exists: cost.py:775 and :814, redaction.py:278, scope.py:743. evals/measure_subagent_model_resolution.py mirrors the logic and does not import it. `[verified: grep]`
9. Readers of the two labels. `[verified: grep]`
   - `_MCP_TOOL_BUCKET_LABEL`: :1016, :3497, test_context_composition.py:826 (`_mod.`), and legacy :8527–8528.
   - `_UNREQUESTED_MODEL_LABEL`: :1657 and legacy :1300.
10. The two modules depend on exactly the M4 lists. `[verified: Read of the spans against the shim's import blocks :8–243]`
11. review_rounds.py imports only `corpus`, `pricing`, `redaction`, `render`, and `scope`, so `subagent_mix` → `review_rounds` creates no cycle. No package module imports the shim. `[verified: grep of `^from|^import` across transcript_analysis/]`
12. `cmd_cost_counts` reads `config_dir() / "projects"` (shim :1956). Read as `scope.config_dir()`, it is covered by the `scope.config_dir` patch `fake_projects` already applies (conftest :877). The other four package callers of `config_dir()` bind it by name (scope.py:25–27, ledger_common.py:16, pr_cost_ledger.py:20, cost_ledger.py:29), each for its own root. `[verified: grep; Read conftest :851–882; three plan-review reviewers confirmed :877]`
13. Two `TestCostCounts` tests would also pass if the corpus were not found, because their assertions hold on empty output: `test_zero_spawns_renders_the_sentence_not_a_table` and `test_no_branch_name_appears_anywhere_in_output`. The condition predates this phase, and a verbatim move does not change it. `[verified: traced by two plan-review reviewers from legacy :2395–2560 and scope.py:427–447]`
14. Shim imports that lose their last code reader:
    - `_index_subagent_dispatches` (:1612)
    - `_MODEL_BASE_INPUT_RATES` (:1527–1528; :6052 and :7009 are string literals)
    - `_MODEL_RATE_EXPIRES` (:2100)
    - `_PRICING_SOURCE_URL` (:1759)
    - `_root_scoped_display_label`
    - `compute_review_round_counts` (:1964)
    - `_repo_tracked_agent_type_names`
    - `_sanitize_table_cell`

    Test readers through `_mod`:
    - `_MODEL_RATE_EXPIRES`: only legacy :1883, which moves.
    - `_repo_tracked_agent_type_names`: staying redaction tests at :2221–2330. The moved tests' eight reads (:2342–2599) do not keep it alive, because M8 retargets them to `_mod.redaction.`.
    - `_sanitize_table_cell`: staying `TestSanitizeTableCell` at :3859–3868.
    - The other five have none.

    Every other name the spans read keeps a shim reader, for example `_count_subagent_spawns` :2149, `_warn_if_subagent_format_drift` :2197, `_SPAWN_TOOL_NAMES` :3333, `config_dir` :4269, `SUBAGENT_SUBDIR` :4938, `REVIEW_SKILLS` :1109.

    `[verified: grep of each name in the shim and in scripts/tests/]`
15. ruff selects `E`, `F`, `B`, `I`, `UP`, and `SIM`, with line-length 130. `[verified: pyproject.toml:2, :6]`
16. Test slice layout. `[verified: class-def grep; Read]`
    - Block A:
      - :995–997: banner.
      - :1000–1022: `_subagent_mix_args`.
      - :1025–1038: `_cost_counts_args`.
      - Classes: `TestSubagentMix` :1041, `_write_agent_frontmatter` :1172, `TestSubagentMixModelMix` :1180, `TestSubagentMixDollars` :1399, `TestDispatchUsageSummaryDedupBeforePricing` :1708, `TestDeclaredPinPathSafety` :2076, `TestSubagentMixSince` :2106.
      - Redaction island: :2147–2331.
      - Classes: `TestTrackedAgentFilenamesMatchAgentTypeNameCharset` :2333, `TestSpawnCountsByAgentType` :2353, `TestCostCounts` :2370, `TestSubagentMixMultiRoot` :2581–2931.
    - Block B:
      - :8299–8301: banner.
      - :8304–8318: `_subagents_args`.
      - :8321–8744: five `TestSubagents*` classes.
      - `TestSkillPairSubagentFile` (:8746) stays.
    - Block D: `TestSubagentsDeclaredRootsMultiRoot`, :10630–10841.
17. `TestFormatDriftCanary` (:8773–8911) calls `cmd_subagents` in four methods, `cmd_skill_pair` in one, and `cmd_cache_efficiency` in four. `[verified: Read]`
18. `TestSubagentFormatContract` (:8918–8971) reads only `_mod.SUBAGENT_SUBDIR`, `_mod.iter_sessions`, `_write_subagent_jsonl`, and `_user_msg`. `[verified: Read]`
19. The two redaction classes test `redaction.py` functions. `TestTrackedAgentFilenamesMatchAgentTypeNameCharset` reads `_mod._AGENT_TYPE_NAME_RE`, and its docstring frames it as a cost-counts assumption. `[verified: Read :2147–2352]`
20. Where each helper is used. `[verified: grep]`
    - `_sum_column_across_rows` (:62–88): :2620 and :8651.
    - `_column_values_for_matching_rows` (:91–110): :1166 and :1393 only.
    - `_priced_sidechain_asst` (:122–137): 15 call sites in :1414–1696, all in `TestSubagentMixDollars`, plus :12698. `TestDispatchUsageSummaryDedupBeforePricing` names it only in a docstring (:1712).
    - `_mcp_use` (:140–142): :8518–8519 only.
    - `_write_agent_frontmatter`: :1189 and :1244 only.
    - `_subagent_mix_args`: subagent-mix classes, plus :10651 and :10709.
    - `_subagents_args`: Block B, Block D, :8783–8840, and :9992.
    - `_cost_counts_args`: `TestCostCounts` only.
21. `claude/.claude/scripts/tests/__init__.py` exists, so same-directory imports are relative (`.claude/rules/test-tree-packaging.md`). `[verified: Glob]`
22. Patch sites in the moving slices. `[verified: grep of setattr]`
    - Retarget:
      - :1873 `setattr(_mod, "open", …, raising=False)`;
      - :2485–2488 `setattr(_mod, "_partition_spawn_counts_by_disclosure", …)`.
    - Leave as they are:
      - :2129, :8584, and :10751 `time.time`;
      - :2392 and :2596 `_mod.redaction._REPO_AGENT_DEFINITIONS_DIR`;
      - :2569 `sys.argv`;
      - :2757, :2761, :8718, and :8722 `_mod.scope.*`.
23. Under Stage 1's temporary re-export, exactly two tests fail: `test_file_that_fails_to_open_returns_empty_summary` and `test_backstop_assertion_fires_on_bypassed_partition_step`. Each patch lands on the shim, while the moved function reads its own module's globals. `[unverified — inferred from G2; Stage 1 runs it]` That holds only because Stage 1 also adds the two `###` doc sections. `test_architecture_doc_documents_every_package_module` requires a heading for every module file on disk, so without them it would be a third failure that controls nothing. `[verified: test_transcript_analysis_architecture_doc.py:29–49]`
24. Tests that moved in earlier phases read moved names through the owning module (cache-rebuild M7(a); cost-ledger row 33). `[verified: cache-rebuild-decomposition.md:120–123; cost-ledger-decomposition.md:299]`
25. Audit-routing's M5 moved generic record builders that other command groups call into conftest. It rejected both a family helper module and duplicates (audit-routing-decomposition.md:160–170). conftest is about 1,050 lines (`_dead_pid` at :1045). `[verified: Read]`
26. Docstrings that point at a moved test or moved code by file:
    - test_pr_cost_section.py: :396–397, :692–693, :805;
    - evals/test_measure_subagent_model_resolution.py:199, which names legacy :1343;
    - evals/measure_subagent_model_resolution.py: :20–22, :250–251 (a `#` comment naming `_agent_frontmatter_model`), :412–413, and :454–455. The last is already wrong, because `_index_subagent_dispatches` has lived in corpus.py since Phase 1.

    conftest :529–533, docs/case-studies/**, and docs/design-decisions/plan-architect-consult-mode.md:15 name functions only.

    `[verified: grep; Read]`
27. test_transcript_cli_bootstrap.py has no test for these three commands. Reusable pieces: `_run` (:27–37), `_isolated_config_env` (:199–211), and `_seed_reviewer_dispatch_account` (:318–354), which seeds one `staff-backend-engineer` Agent dispatch with a paired `.jsonl` and `.meta.json`. cost-counts' success path needs `git worktree list` in the working directory (scope.py:85–115). `[verified: Read]`
28. Architecture-doc passages that go stale: :16–18, :75–76, :92, :140, and :411–413. The drift test requires one `### \`<module>.py\`` heading per module (test_transcript_analysis_architecture_doc.py:15–42). `[verified: Read]`
29. The prefix test's scope is set by `PRODUCTION_MODULES` and `TEST_FILES` (:20–25). Only the architecture doc (:411) names the file. `[verified: Read; grep]`
30. `select-tests.py`. `[verified: Read]`
    - Any scripts/ path selects scripts/tests/.
    - `TRANSCRIPT_ANALYSIS_TEST_GLOB` (:100–107) exists for tests that read hooks or SKILL.md files by path. No moved test does: the legacy file's `HOOKS_DIR`/`SKILLS_DIR` reads are at :11043–11084 and :11480.
    - `_REVIEW_BENCH_SCRIPTS_DEPENDENCIES` (:76–86) imports neither new module.
    - An `AGENTS_DIR` change selects hooks, skills, and review_bench tests, but not scripts/tests (:697).
31. None of the three commands reads `no_redact`, so the governing plan's multi-root `--no-redact` refusal test (transcript-analysis-decomposition.md:298–303) has no site here. These guards move verbatim inside their own functions:
    - subagent-mix's multi-root `--per-session` refusal (:1516–1524);
    - cost-counts' refusals (:1938–1953);
    - cost-counts' render-time backstop (:1989–1995).

    `[verified: Read of every span]`
32. #981: subagent-mix counts CR/PR/RR only from a `Skill` tool_use whose `input.skill` is in `REVIEW_SKILLS` (:1662–1665). `[verified: Read]` The claim that the issue asks for evidence before any behavior change is `[unverified — relayed from Step 3]`.
33. #1175 is an open issue; its stated scope touches the shim's import block, the architecture doc, and legacy ranges disjoint from this phase's. `[unverified — relayed from Step 3; no local worktree carrying a handoff_nudge module or a moved _priced_sidechain_asst was found]` `[verified: gh pr view 1175 errors and gh issue view 1175 returns an open issue, run in the authoring session; gh pr list shows no open PR naming it; the open PRs with handoff in the title or branch (#673, #703, #921, #969) touch none of the shim, the architecture doc, conftest, the legacy test file, or the prefix test]` The parent re-checks both at dispatch time with the read-only commands in Precondition and records the result in the dispatch prompt. The plan file is not edited after review.
34. Line numbers are at d48a31f8. `[verified: gitStatus]`
35. Every in-span "above"/"below" comment points within its own function: :940, :968, :974, :1057, :1543, :1575, :1585, :1594, :1754. No production comment is edited. `[verified: grep]`
36. The limit is 1,000 lines for production and test files (docs/design-decisions/code-file-line-limit.md:19). No test enforces it yet. `[verified: Read; grep]`
37. Collected node IDs must be set-equal before and after (docs/reports/2026-08-10-repo-quality-audit/findings.md:385–387). `[verified: Read]`
38. The shim is 8,037 lines, the legacy file 14,649, and conftest 1,049. `[verified: Read of each file's last line at d48a31f8]`

39. Scope is the whole dispatch group. `[engineer-verified: "Whole group (Recommended)"]` (selected label, answering whether to keep the whole group rather than only what #1194 touched; this settles row 4's inference)
40. The six CLI bootstrap tests (M11) stay in scope. `[engineer-verified: "Keep (Recommended)"]` (selected label, answering whether to keep the bootstrap tests)
41. The prefix-test rename and extension (M13) stay in scope. `[engineer-verified: "Keep rename and extend (Recommended)"]` (selected label)
42. The docstring-pointer edits (M10), including the already-stale evals pointer, stay in scope. `[engineer-verified: "Keep, list in PR body (Recommended)"]` (selected label)

43. This plan's revert-rehearsal step is replaced by row 48's rollback statement. `[engineer-verified: "Replace with a ledger sentence (Recommended)"]` (selected label, answering what to do with Verification's revert-rehearsal step)
44. M10 adds evals :250–251. It rewrites test_pr_cost_section.py :803–807 to what is true instead of confirming tests that do not exist. The missing real-path zero-rounds test and the vacuity-prone `TestCostCounts` tests become follow-ups. `[engineer-verified: "Add :250-251 and fix :805 wording (Recommended)"]` (selected label, answering how M10 treats the two pointers plan-review found)
45. The moved tests' eight `_mod._repo_tracked_agent_type_names` reads move to `_mod.redaction.`. `[engineer-verified: "Retarget to redaction (Recommended)"]` (selected label, answering whether the moved tests keep reading the shim's passthrough)
46. Verification drops these: account B; runs (b), (e), (f), (g), (h), and (j); the hook-sandbox and prefix-correctness steps; and the control that repeated Stage 1's result. `[engineer-verified: "Trim (Recommended)"]` (selected label, answering whether to trim Verification)
47. Run (i) needs no git write. `_repo_scoped_project_slugs` derives one slug from each path `git worktree list --porcelain` prints, through `_path_to_project_slug`, which replaces every `/` and `.` with `-` (scope.py:74–82, :129–132, :190). It then requires the working directory to sit at or under a listed worktree, and its `git rev-parse --show-toplevel` to be one of them (:142–188). All three git calls on the path are reads: `git worktree list --porcelain`, `git rev-parse --show-toplevel`, and `git -C <agents dir> ls-files` (redaction.py:292), the last reached when the spawn table is non-empty. A project directory named by this worktree's printed path therefore round-trips when the command runs from the worktree root. `cmd_cost_counts` reads only `config_dir() / "projects"` (shim :1956). `[verified: Read of scope.py:74–190 and shim :1936–1998]`
48. Rollback is a plain revert of this phase's commit range. It stays valid until the next phase edits the shim's import block or the architecture doc; after that, fix forward. No step rehearses it, and the governing plan's post-merge rehearsal is waived (rows 43, 60). `[unverified — not rehearsed]`
49. The new test files import conftest builders that earlier phases promoted: `_table_cols` (#681); `_priced`, `_extract_grand_total`, `_cost_args`, and `_write_subagent_jsonl` (#692); `_write_subagent_dispatch` (#706); and `_two_declared_roots` (#1136), which Block D calls five times (:10643–10800). Reverting any of those phases before this one leaves the new files uncollectable, so cross-phase reverts run last-in-first-out. M9's own promotion only appends to conftest, so reverting this phase first is safe. `[unverified — PR attribution relayed from plan-review's git log -S on conftest; I could not run git. The Block D call sites are verified by grep.]`
50. Within the moved spans, the wall clock is read in one place: `cmd_subagent_mix`'s `datetime.now(UTC).date()` (shim :1553), compared with `_MODEL_RATE_EXPIRES` (:2100). Every rate expires at `_PRICING_FETCH_DATE` + 90 days, which is 2026-12-04 at d48a31f8 (pricing.py:21, :46, :81). `[verified: grep of the shim for clock reads; grep of pricing.py]`
51. Legacy :2086 is 105 characters. M8's two `subagent_mix.` prefixes make it 131, past ruff's 130 (row 15). The longest line the `_mod.redaction.` retarget touches, :2347, becomes 92. `[verified: Read of :2086 and :2333–2599]` No other retargeted line passes 130. `[unverified — relayed from two plan-review reviewers' scan of every retargeted line]`
52. An uncaught exception's traceback names the new module files instead of the shim. The only non-test reader of cost-counts' stderr, `pr-cost-section.sh:46`, discards it with `2>/dev/null`. `[verified: grep]`
53. `TestConftestModuleNamesAreUnique`, `TestNoBareSameDirectorySiblingImports`, and test_ticket_reference_discipline.py build their corpus from `git ls-files` (test_pytest_collection_config.py:281, :448; test_ticket_reference_discipline.py:36). `git ls-files` lists staged files but not untracked ones. `[verified: grep]`
54. test_skills.py's `test_doc_has_no_state_path` scans every `docs/**/*.md` outside reports and case studies, and its pattern rejects `~/.claude/projects/` (:5986–5990, :6042–6047, :6164–6172). The architecture doc defines no `<config-dir>` token. `[verified: Read; grep]`
55. `require-stow-reminder.sh` denies `gh pr create` when the branch adds a file under `claude/.claude/` and neither `stow` nor `install.sh` appears in the body (:131, :221). `[verified: grep]` New files under `claude/.claude/scripts/` reach every stow consumer through the folded `~/.claude/scripts` symlink, with no `./install.sh` re-run. `[unverified — relayed from plan-review, citing governing-plan row 8 and install.sh:29]`
56. The architecture doc's attribute-access rule covers reads of another module's reassignable global, and it names `scope.config_dir` (:63–65). `subagent_mix.py` reads `scope.config_dir()` by attribute, so the rule applies as written. `[verified: Read]`
57. `TestCombinedZeroState`'s docstring (test_pr_cost_section.py:803–807) cites a rounds-table and a spawns-sentence zero-state test at the Python level. Only the spawns one exists. In `TestCostCounts`, `test_review_round_table_renders_actual_round_counts` pins one nonzero round, and no legacy test asserts the all-zero rounds table. `[verified: Read of legacy :2370–2560; grep of the legacy file for the all-zero total row]`
58. evals/measure_subagent_model_resolution.py:57 says the constant "matches transcript-analysis.py's SUBAGENT_SUBDIR". The shim still binds `SUBAGENT_SUBDIR` after this phase (row 14). M5, by contrast, deletes the shim's `_index_subagent_dispatches` import, the name :454–455 cites. `[verified: Read of evals :55–57; grep]`

59. `cmd_cost_counts` reads `config_dir` as `scope.config_dir()`, not through a by-name binding. `[engineer-verified: "I want the lighter read"]` (the engineer's reply after the plan offered the by-name binding or the lighter `scope.config_dir()` read)
60. The governing plan's post-merge revert rehearsal (transcript-analysis-decomposition.md:276–279) is waived for this phase. `[engineer-verified: "Waive it, that’s excessive"]` (the engineer's reply to whether to keep that rehearsal)

**Plan-review should re-check:** M9's conftest promotion, M13's rename, row 33, Stage 1's doc-section placement (row 23), and run (i)'s slug seeding (row 47).

## Critical files

**Precondition.** Do not run concurrently with #1175.
- Before Verification step 0, the parent runs these read-only commands: `gh issue view 1175 --json state`; `gh pr list --state open --limit 200 --json number,title,headRefName,body,files`; `git worktree list --porcelain`; `git branch --all --list '*1175*' '*handoff*'`; and `git ls-remote --heads origin`. An open PR matches when its body, title, or branch names 1175 or handoff-nudge. A worktree or branch matches on the same names. The parent records the issue state and every PR, worktree, and branch inspected, each marked match or no-match, plus each matching PR's file list, in the dispatch prompt, not in this plan file: a post-review edit to the plan re-arms the plan-review gate and denies the code-writer's first Write. File-level overlap blocks dispatch even when the line ranges are disjoint. Dispatch only if nothing matches, or every matching PR's files include none of these: the shim, the architecture doc, conftest, the legacy test file, and the prefix test. A match that is a branch or worktree with no PR has no file list to test, so it blocks dispatch until the engineer decides. A closed #1175 does not bypass an open matching PR. The scan cannot see work on another machine that has not been pushed.
- If `origin/main` has moved past d48a31f8, sync the branch (`git-feature-branch-sync`). Then re-locate every span by its first and last symbol (row 34) and re-run the row 14 and row 20 greps. If #1175 already re-homed `_priced_sidechain_asst`, import it from that home instead of M9's promotion.
- A sync after step 0 and before the phase's commit re-runs all of step 0 on the synced base, not only the greps. Once the dispatch has edited the tree, that means the abort procedure, then the sync, then step 0, then a fresh dispatch.
- Immediately before opening the PR, re-check `origin/main`. If it moved and the new commits touch a file under Critical files or a file step 0 baselined, stop and report to the engineer before syncing. Otherwise sync, then re-run Verification 1 and 5. Parity (steps 2–3) was proven against this phase's own base, and a sync that touches none of those files cannot change that result.
- If the synced base carries a repo-local line-limit check (docs/design-decisions/code-file-line-limit.md), its ceiling rows for the shim, the legacy file, and conftest must change to this phase's measured sizes in the same PR. The file holding those rows is not in this list, so name it to the engineer before editing it.

### Create — production

Copy moved code verbatim. Only these edits are allowed:
- M4 prefixes;
- each module's docstring and imports;
- wrap-only reflow, reported in the PR body.

Every module starts with `from __future__ import annotations`. Docstrings and comments carry no issue or PR numbers.

- **`claude/.claude/scripts/transcript_analysis/subagents.py`** takes shim :871–1073, two blank lines, then :1076–1079 (M2–M4). Docstring:
  `"""The subagents command: cmd_subagents -- per-branch isSidechain turn counts by model family, plus tool-result text bytes per thread and per producing tool, every MCP tool name collapsed into _MCP_TOOL_BUCKET_LABEL.\n\nImports corpus, pricing, redaction, render, and scope by module (attribute access, not by name) -- see scope.py's own top-of-file comment for why."""`
- **`claude/.claude/scripts/transcript_analysis/subagent_mix.py`** takes :1081–1083, two blank lines, then :1465–2119 (M2–M4). Docstring:
  `"""The subagent-mix and cost-counts commands: cmd_subagent_mix's per-branch subagent_type spawn counts and per-agentType model-mix and dollar table, and cmd_cost_counts' public-PR-body review-round and spawn counts, which share _UNKNOWN_SUBAGENT_TYPE and the agent-type disclosure allowlist.\n\nImports corpus, pricing, redaction, render, review_rounds, and scope by module (attribute access, not by name) -- see scope.py's own top-of-file comment for why."""`

### Create — tests

Each new test file gets:
- a one-line docstring naming its module and its seam;
- the loader from `test_transcript_read_scope.py:21–28`, comment included;
- `from .conftest import …` and `from ._subagent_helpers import …` for exactly what it uses;
- its own stdlib imports.

Assemble ranges in source order and apply M8 to them. Nothing else on an `assert` line changes, apart from M8's wrap-only reflow.

- **`claude/.claude/scripts/tests/_subagent_helpers.py`**, about 75 lines. It takes legacy :62–88, :1000–1022, and :8304–8318, verbatim, and imports nothing. Docstring: `"""Test helpers shared by the subagent family's test files (test_transcript_subagents.py, test_transcript_subagent_mix*.py) and by test_transcript_analysis.py's cross-subcommand tables."""`
- **`claude/.claude/scripts/tests/test_transcript_subagents.py`**, about 680 lines. Theme: `cmd_subagents`' tables, byte grouping, `--since`, and multi-root disclosure, plus declared-roots multi-root for both commands. It takes legacy :140–142, :8321–8744, and :10630–10841.
- **`claude/.claude/scripts/tests/test_transcript_subagent_mix.py`**, about 840 lines. Theme: subagent-mix's spawn table, model-mix columns, declared-pin path safety, `--since`, and multi-root disclosure. It takes legacy :91–110, :1041–1397, :2076–2144, and :2581–2931.
- **`claude/.claude/scripts/tests/test_transcript_subagent_mix_dollars.py`**, about 705 lines. Theme: the Actual $/Counterfactual $ columns and `_dispatch_usage_summary`'s dedup-before-pricing. It takes legacy :1399–2074.
- **`claude/.claude/scripts/tests/test_transcript_cost_counts.py`**, about 290 lines. Theme: cost-counts' refusals, rendering, the disclosure allowlist, and the charset pin. It takes legacy :1025–1038 and :2333–2578.

### Rename and modify

- `claude/.claude/scripts/tests/test_transcript_cost_ledger_module_prefixes.py` → `claude/.claude/scripts/tests/test_transcript_package_module_prefixes.py` (M13).

### Modify

- **`claude/.claude/scripts/transcript-analysis.py`**: M5. `build_parser()` and every surviving function body stay unchanged.
- **`claude/.claude/scripts/tests/test_transcript_analysis.py`**:
  - Delete :62–88, :91–110, :122–137, :140–142, :2333–2932, :8299–8745, and :10630–10843.
  - Replace :995–2146 with M7's banner.
  - Add `_priced_sidechain_asst` to the `.conftest` import.
  - Add `from ._subagent_helpers import _subagents_args`.
  - Remove only the imports F401 then flags.
  - `_UNCONDITIONAL_HEADER_CASES`, `TestRootsThreadingSpy`, `TestFormatDriftCanary`, and `TestSubagentFormatContract` stay byte-identical.
- **`claude/.claude/scripts/tests/conftest.py`**: M9.
- **`claude/.claude/scripts/tests/test_transcript_cli_bootstrap.py`**: M11.
- **`claude/.claude/scripts/tests/test_pr_cost_section.py`**: docstrings only (M10).
- **`claude/.claude/scripts/transcript_analysis/__init__.py`**: docstring only (M12).
- **`docs/transcript-analysis-architecture.md`**: M12.
- **`evals/measure_subagent_model_resolution.py`**: docstrings and one comment only (M10).
- **`evals/test_measure_subagent_model_resolution.py`**: docstring only (M10).

### Explicitly unchanged

- `select-tests.py` and `test_select_tests.py`;
- `docs/transcript-analysis.md`, since the CLI is identical;
- the transcript-analysis `SKILL.md`;
- every existing package module;
- `test_context_composition.py`, whose `_mod._MCP_TOOL_BUCKET_LABEL` read stays valid through M5's by-name import;
- conftest :529–533.

### Reuse

- `workstream_cost.py:1–16` for module-header shape.
- `test_transcript_read_scope.py:21–28` for the loader.
- `tests/_cache_rebuild_helpers.py:1–3` for the helper-module docstring.
- conftest's builders: `_asst`, `_agent_use`, `_skill_use`, `_tool_result`, `_user_msg`, `_write_jsonl`, `_write_subagent_jsonl`, `_write_subagent_dispatch`, `_two_declared_roots`, `_table_cols`, `_priced`, `_extract_grand_total`, `_cost_args`.
- test_transcript_cli_bootstrap.py's `_run`, `_isolated_config_env`, and `_seed_reviewer_dispatch_account`.

### Dispatch

One `code-writer` dispatch covers every file above (M14). Capture the Verification step 0 baseline before it edits anything. Extract moved code by line range from unmodified scratch copies; never retype it.

- **Stage 1:**
  - Create both production modules and delete both spans from the shim.
  - Add both modules to the module-import tuple.
  - Temporarily import all 17 moved names into the shim by name. This block is lint-dirty by design.
  - Add M12's `### \`subagents.py\`` and `### \`subagent_mix.py\`` sections to the architecture doc. The doc-drift test requires a heading for every module file on disk. Without them, Stage 1 would show a third failure that controls nothing and blur the two-test negative control (row 23). The sections describe M5's final import set (three `cmd_*` plus `_MCP_TOOL_BUCKET_LABEL`), not Stage 1's temporary re-export. A failure outside the two named IDs goes back to the parent, which reproduces it at the merge-base before treating it as in scope.
  - Run select-tests.py. Every selected test must pass except exactly the two node IDs in row 23, which must fail. That result is the negative control for M8's two patch retargets.
- **Stage 2:**
  - Create the helper module and the four test files, and apply M8.
  - Make the legacy, conftest, bootstrap, pointer, `__init__`, and prefix-test edits, and the rest of M12's doc edits.
  - Replace the temporary re-export with M5's final imports.
- **Abort procedure.** If the dispatch is interrupted before this phase's commit, the code-writer returns to the parent and runs no cleanup itself. The parent then does the following, naming every path explicitly:
  1. builds its path lists from `git status --short` and `git diff --cached --name-only` at abort time, never from this plan's file list, then runs `git restore --staged -- <path>…` as a literal call on each staged path, including the prefix test's old and new names;
  2. copies each step 0 snapshot back over its file, with the prefix test restored under its old name;
  3. deletes the created files and the prefix test's new name, each by path from that status output.

  It never runs `git clean`, `git checkout .`, `git reset --hard`, or `git stash`, because the plan file must survive. Success check: `git status --short` lists no path other than the plan file. Trigger the procedure on a blocked return or an abnormal termination of the code-writer. Every git write runs as a literal call, never inside a wrapper script, so the worktree gate judges it. These steps overwrite tracked files and delete created ones. Plan approval covers them, and the parent reports to the engineer first if the tree holds anything this plan does not name. Then restart from Stage 1.
- **PR body:**
  - the Stage 1 result, including the two expected failures;
  - the wrap-only reflow counts for production and for tests (row 51 expects one test line);
  - measured file sizes, flagging every file over 1,000 lines;
  - `git blame -C -C -s` counts;
  - under "Incidental edits": the evals :454–455 pointer and the test_pr_cost_section.py :803–807 docstring rewrite;
  - #981 left as it is;
  - rollback: a plain revert of this phase's commit range, valid until the next phase edits the shim's import block or the architecture doc, then fix-forward (row 48). Cross-phase reverts run last-in-first-out, because the new test files import conftest builders that #681, #692, #706, and #1136 promoted (row 49);
  - the stow line, which `require-stow-reminder.sh` requires (row 55): "New files under `claude/.claude/scripts/` arrive through the folded `~/.claude/scripts` symlink; no `./install.sh` re-run is needed.";
  - follow-ups raised but not fixed here: a real-path all-zero rounds-table test, and non-vacuity preconditions for the two `TestCostCounts` tests row 13 names.

## Verification

Run everything from the worktree root. `<venv>` is `../../../.venv`, because the `.venv` lives only in the main checkout (README.md:521). Scratch files live outside the repo, and every corpus is synthetic. Run each capture and each per-file git command as its own literal Bash call, or as one script under scratch, per docs/worktree-bash-guard.md. Invoke every capture as `<venv>/bin/python3 claude/.claude/scripts/transcript-analysis.py <args>` from the worktree root, in step 0 and in step 3. Never use `~/.claude/scripts/...`: it resolves to the main checkout, so parity would pass vacuously.

0. **Baseline, before the dispatch.**
   - Copy every file this plan modifies or renames to scratch.
   - Save `<venv>/bin/pytest --collect-only -q` IDs for test_transcript_analysis.py, test_transcript_cli_bootstrap.py, test_transcript_cost_ledger_module_prefixes.py, test_pr_cost_section.py, test_context_composition.py, and evals/test_measure_subagent_model_resolution.py.
   - Run test_transcript_analysis.py with `--junitxml=<scratch>/legacy-before.xml`, and record `id -u`. Legacy :8458, `test_unreadable_transcript_file_skipped_without_crash`, skips under uid 0.
   - Capture `--help` for the top level, `subagents`, `subagent-mix`, and `cost-counts`.
   - Seed one synthetic account, A. Name its one project directory by the slug `scope._path_to_project_slug` gives for this worktree's path exactly as `git worktree list --porcelain` prints it: every `/` and `.` becomes `-` (row 47). That slug embeds the checkout's home path, so the seeded directory name, every capture, and `git worktree list` output stay in scratch and appear in no commit message, PR body, or plan edit. The directory holds one session on branch `feat`, with every timestamp on one fixed literal date, `<d>`. The session has:
     - an Agent dispatch of `code-writer` and one of `totally-untracked-agent`, each with a paired `subagents/*.jsonl` and `.meta.json`, one of which carries a `"model"` key;
     - a two-block, one-requestId priced `claude-sonnet-4-6` sidechain run;
     - a `Skill` `code-review` tool_use;
     - an `mcp__srv__tool` tool_use with its tool_result;
     - a Read tool_result.
   - Pin `CLAUDE_CONFIG_DIR` to A and `TRANSCRIPT_CONFIG_DIRS_FILE` to a nonexistent path.
   - Capture stdout, stderr, and the exit code for each run. The letters keep their earlier labels (row 46):
     - (a) `subagents`;
     - (c) `subagent-mix`;
     - (d) `subagent-mix --reprice-as claude-haiku-4-5-20251001 --since-date <d> --until-date <d+1>`;
     - (i) `cost-counts --this-repo --branches feat`, run from the worktree root. It needs no git write, because the command's own git calls are reads (row 47). Its output must show a nonzero spawn row.
   - Confirm today's UTC date is on or before `pricing._PRICING_FETCH_DATE` + 90 days, which is 2026-12-04 at d48a31f8 (row 50). After that date, subagent-mix's stale-rate check (shim :2100) fires and changes its output independently of this phase. Step 3 re-confirms the date and reuses these seeded directories; never re-seed.
   - Record `wc -l` for the shim, the legacy file, and conftest.
1. **Scoped suite.** First stage the seven created files with `git add -- <paths>`; The parent's `git mv` (M13) already staged the rename. Three checks build their corpus from `git ls-files`, which omits untracked files, so until the files are staged those checks are selected without covering them (row 53). Then run `<venv>/bin/python3 claude/.claude/scripts/select-tests.py`. Every selected test must pass.
   - Confirm the selection included:
     - the architecture-doc drift test;
     - `claude/.claude/tests/test_pytest_collection_config.py`, for the conftest-uniqueness and bare-sibling-import tests;
     - test_transcript_cli_bootstrap.py;
     - the renamed prefix test;
     - test_pr_cost_section.py;
     - `claude/.claude/hooks/tests/test_nudge_error_mode_analysis.py`;
     - `evals/test_measure_subagent_model_resolution.py`.
   - Run any missing one alone.
   - A path select-tests.py cannot map is a rule-table bug, not a reason to run the full suite.
2. **Test-ID parity.** Strip each ID's file prefix and compare sorted lists, duplicates included.
   - Step 0's legacy list must equal the post-move legacy list plus the four new files' lists.
   - The prefix test's stripped IDs must be unchanged under the new name.
   - test_transcript_cli_bootstrap.py must gain exactly the six M11 tests.
   - Every other list from step 0 must be unchanged.
   - Per-ID outcomes must match too. Run the legacy file and the four new files with `--junitxml`, and record `id -u`, which must equal step 0's. Map each junit `classname` plus `name` to a node ID by converting the dotted module path to a file path, then apply the same prefix strip. Each ID's outcome (passed or skipped) must equal its step 0 outcome, and skip counts must be equal. Name the one skip-sensitive test, `test_unreadable_transcript_file_skipped_without_crash` (:8458), in both lists.
3. **CLI parity.** Every `--help` capture and runs (a), (c), (d), and (i) must match step 0 byte for byte.
4. **Spy reach and negative controls.** Before each control, record `sha256sum` of every file it edits. Make the edit by hand, run the check, then undo the same edit by hand. After the undo, each file's `sha256sum` must equal its recorded value. Never use `git stash` or `git checkout`.
   - Delete `TestRootsThreadingSpy`'s `_mod.scope` setattr lines (legacy :10227–10228 at d48a31f8). The `subagents` and `subagent-mix` parametrized cases must fail as assertion failures carrying the `was never called` message, not as errors.
   - Stage 1's result is the control for M8's two patch retargets; it is not repeated here.
5. **Lint.** Run `<venv>/bin/ruff check claude/.claude/scripts/ evals/measure_subagent_model_resolution.py evals/test_measure_subagent_model_resolution.py`. It must be clean.
6. **Leftovers and single home.**
   - `git grep -nE '^(def |class )?(<the 17 moved names, |-joined>)\b' claude/.claude/scripts/transcript-analysis.py` returns nothing. Each of the 17 names is defined exactly once under `transcript_analysis/`.
   - `git grep -nE '_mod\.(<the 17 names>|_MODEL_RATE_EXPIRES)\b' claude/.claude/scripts/tests/` returns only these:
     - the `_UNCONDITIONAL_HEADER_CASES` rows;
     - `TestFormatDriftCanary`'s four `_mod.cmd_subagents` calls;
     - test_context_composition.py:826.
   - No `setattr(_mod, "open"` or `setattr(_mod, "_partition_spawn_counts_by_disclosure"` remains.
   - `git grep -n '_mod\._repo_tracked_agent_type_names'` over the four new test files returns nothing.
   - `_priced_sidechain_asst`, `_sum_column_across_rows`, `_subagents_args`, and `_subagent_mix_args` are each defined exactly once under `scripts/tests/`.
7. **Move fidelity.**
   - **Comparator check first.** Run the comparator over scratch copies seeded with five defects:
     - in a test file: one deleted `assert`, one changed string literal, one removed decorator (`@pytest.mark.skipif` at legacy :8458, or `autouse=True` at :2374; no moved slice contains a `parametrize`), and one extra method;
     - in a production module: one deleted statement.

     Seed each defect into its own scratch copy, so each run reports exactly one named node. It must report all five. Every real run must also report its compared-node count, and that count must equal the expected one: the 17 moved production top-level nodes, the helper module's three functions, conftest's `_priced_sidechain_asst`, and, for each new test file, the class and top-level function count of its legacy slices in the step 0 snapshot. That totals 41 at d48a31f8: 17 + 3 + 1, plus 7 (subagents), 7 (subagent_mix), 2 (dollars), and 4 (cost-counts) test-side nodes. `[unverified — relayed from plan-review's AST count]`
   - **Production.**
     - Apply M4's map in reverse, restoring the two aliases.
     - Each top-level node's `ast.dump` must equal its step 0 span.
     - The `#`-comment diff must be empty.
   - **Tests.**
     - Reverse M8's map by name (`_repo_tracked_agent_type_names`, `cmd_subagents`, `_MCP_TOOL_BUCKET_LABEL`, the 15 `subagent_mix` names, `_MODEL_RATE_EXPIRES`), not by module prefix, so the pre-existing `_mod.redaction._REPO_AGENT_DEFINITIONS_DIR` reads at :2392 and :2596 are left alone. A wrap-only reflow leaves the AST unchanged.
     - Every class and function AST must equal its legacy slice. The helper-module functions and conftest's `_priced_sidechain_asst` must match their legacy text byte for byte.
     - The comment diff must be empty, except M7's banner text.
   - After the commit, report `git blame -C -C -s` line counts for each new file, one literal call per file.
8. **Sizes.** Report measured `wc -l` for every new and shrunk file in the PR body. Flag every file over 1,000 lines. The shim, the legacy file, and conftest already are.

## Out of scope

- **Fixing #981's CR/PR/RR undercount** (row 32). A move that must preserve behavior can't carry a behavior change that alters published numbers.
- **Leaf-module tests left in the legacy file.** These are `TestRootScopedDisplayLabel`, `TestRepoTrackedAgentTypeNames`, `TestSubagentFormatContract`, and `TestSanitizeTableCell`, plus the shim's two noqa passthroughs. After M8's retarget, only these staying classes read those passthroughs. The leaf-module-tests phase owns them.
- **`TestFormatDriftCanary` and the `_UNCONDITIONAL_HEADER_CASES` rows.** They cover more than one command, so they stay until the `cli.py` phase. `[verified: transcript-analysis-decomposition.md:155–161]`
- **Re-homing `_MCP_TOOL_BUCKET_LABEL` into a leaf.** The context-composition phase decides that.
- **The session-signal phase (`cmd_duration`).** It gets its own plan.
- **`_priced_sidechain_asst`'s docstring.** It moves byte-identical and still names subagent-mix alone, although rearm-backtest tests also call it.
- **Three pre-existing "in this file" phrasings at shim :930, :1552, and :1564.** They move verbatim.
- **select-tests.py's agents-directory gap.** An `agents/*.md` change does not select `scripts/tests/`, yet `TestTrackedAgentFilenamesMatchAgentTypeNameCharset` and `test_real_agents_directory_allowlists_code_writer` read the real agents directory. The gap predates this phase (row 30); raise it to the reviewer.
- **Sizes of the shim, the legacy file, and conftest.** Each was over 1,000 lines before this phase.
- **Retrofitting the prefix test to earlier phases' modules.**
- **Stale shim line citations that predate this phase.** These are review_rounds.py:42, :62, :72, :92, :125; `_config.py:5`; config-schema-audit.md; and docs/cost-levers-considered.md:254. None points into the dispatch ranges. `[unverified — relayed from Step 3]`
- **Any CLI surface change, or renaming moved names or output strings.**
- **evals/measure_subagent_model_resolution.py:57's `SUBAGENT_SUBDIR` pointer.** It names the shim, where the constant is bound but not defined. This phase leaves that binding in place, so the pointer reads the same after the move as before (row 58). M10 fixes :454–455 because M5 deletes the shim's `_index_subagent_dispatches` import. That reason does not reach :57.
- **A real-path all-zero rounds-table test, and non-vacuity preconditions for `TestCostCounts`' zero-spawns and branch-name tests** (rows 13, 57). Both gaps predate this phase, and a verbatim move can't add assertions. Raise both to the engineer as follow-ups.
- **Adding `evals/` to CI's ruff and pytest roots.** CI covers only `claude/.claude/`, `claude-skills/`, and `plugins/` (.github/workflows/tests.yml:154, :165). Verification 5 lints the two edited evals files locally, and step 1's scoped run covers the evals test. CI checks neither.
- **A pre-merge revert rehearsal** (row 43), and the governing plan's post-merge revert rehearsal for this phase, which the engineer waived (row 60). Row 48's rollback statement stands in for the pre-merge one.
