# Cost-ledger decomposition

## Context

Decompose the cost-ledger command group out of the monolithic
`claude/.claude/scripts/transcript-analysis.py` into
`claude/.claude/scripts/transcript_analysis/`, as the fifth phase of the
tracking-issue-#1116 decomposition. This follows the same pattern as the
four already-merged phases (cost family, reviewer-yield, review-trace,
audit-routing) — move a command group's production code and its test
slice together into a new package module, leaving a thin shim behind.

Doing this now continues the governing plan's established phase
ordering (`.claude/plans/transcript-analysis-decomposition.md:150-153`),
right after audit-routing merged (PR #1149, `11165f9c`). The intended
outcome is two new modules —
`transcript_analysis/cost_ledger.py` (`cmd_cost_ledger` and its real
implementation, `_cost_ledger_report`) and
`transcript_analysis/workstream_cost.py` (`cmd_workstream_cost`) — with
identical CLI behavior, byte-identical ledger output, and no stow-consumer-visible
change. `cmd_spend_over_threshold`, which merely shares the file's
section banner with cost-ledger but has no functional relationship to
it, is explicitly excluded from this phase (see Out of scope) — it
belongs to a future handoff-nudge-family phase alongside rearm-backtest,
plan-boundary, and handoff-signal-response.

## Approach

cost-ledger moves out of the shim into one new package module, `transcript_analysis/cost_ledger.py`. It takes shim :4165–4839, which is 24 top-level names copied in source order. `_cost_ledger_report` moves as one unit, with its sentinel gate and git-tracked refusal still inline. workstream-cost moves into a second new module, `transcript_analysis/workstream_cost.py` (:4842–4925). The shim keeps `build_parser()` and imports only `cmd_cost_ledger` and `cmd_workstream_cost` by name. The cost-ledger tests (legacy :5629–6949) move into two new files, split at the `--record` refusal-gate seam. Every test-side reader of a moved or deleted shim binding is retargeted to the module that owns the name.

The Step 3 evidence needs seven corrections. Each one changes the plan:
- **conftest does need changes.** `cost_ledger_file` patches the shim's `_cost_ledger_path` and reads the header constants off the shim (conftest.py:904–911). Both `_UNCONDITIONAL_HEADER_CASES` classes use it (:10931, :11091), and both stay in the legacy file. So the fixture must retarget to `mod.cost_ledger` (rows 11–12). `cost_ledger_enabled` and `fake_projects` must also cover the new module's own `config_dir` binding (M8).
- **`test_transcript_workstream_cost.py` already exists.** It is a full 549-line file and holds every workstream-cost test. The cost-ledger block holds none. Its :489 is a live shim-level patch, an eleventh retarget, not a stale reference (row 17).
- **Five more files need edits:**
  - `test_transcript_ledger_common.py`: 11 reads of moved names (row 18).
  - `test_transcript_reviewer_yield.py`: 2 lines (row 19).
  - `test_transcript_analysis_cost_import_direction.py`: its shim back-import assertion fails once the alias goes (row 20).
  - `test_transcript_cli_bootstrap.py`: every moved family has bootstrap tests here (row 35).
  - `transcript_analysis/__init__.py`: its module list.
- **One moved name was missing and one range was wrong.** `_COST_LEDGER_READ_HEADER` (:4481–4484) was missing, and `_reviewer_gap_pp` ends at :4478. The dependency list also needs `_config` (as a module), `config_dir`, and the stdlib imports. `_redaction_ordinals` belongs to `scope`, not `redaction` (rows 2–4).
- **A source-grep tripwire would pass vacuously** once the code it checks leaves the shim (:5734–5738, row 16).
- **The test slice is 1,321 lines.** That is over the per-phase 1,000-line target, so it splits (rows 21–23).
- **The architecture doc has six more stale passages beyond :340.** Each describes cost-ledger or workstream-cost as living in the shim (row 36).

No stow consumer sees a change. CLI help, stdout, stderr, exit codes, and ledger bytes stay identical (Verification 3). The only visible difference is two new files under `~/.claude/scripts/transcript_analysis/`, delivered through the existing folded symlink (row 37).

Alternatives considered and set aside:
- **Fold `cmd_workstream_cost` into `cost.py`.** The relayed Step 4 answer declined this. Separately, `cost.py` is already 1,230 lines, past the 1,000-line target (row 21).
- **Fold `cmd_workstream_cost` into `cost_ledger.py`.** The two groups share no helper (row 6). They only shared a region of the shim.
- **Split cost-ledger into a format module and a report module**, like `pr_cost_ledger.py`/`pr_cost.py`. The pr-cost split exists because `gh_cli.py` reads `pr_cost_ledger`'s status enum. Nothing outside cost-ledger reads any cost-ledger name (row 5), and the combined module is about 700 lines.
- **Keep the three `compute_*` shim aliases with `# noqa: F401`**, so the import-direction test needn't change. The aliases exist only because cost-ledger code lived in the shim, and this phase removes that code (row 20). Keeping them leaves a back-import that no production code reads, which the architecture doc would then have to describe as a test-only channel.
- **One cost-ledger test file.** It would be about 1,350 lines (row 21).

Implementation is one `code-writer` dispatch. Production code, tests, conftest, and docs all depend on the same rename map (M3) and retarget map (M7). Splitting the work would restate both maps in every prompt and let two agents resolve the same open question differently.

### Assumption ledger

**Root:** cost-ledger (shim :4159–4839) and workstream-cost (:4842–4925) still live in the monolithic shim. The legacy test file still holds 1,321 cost-ledger test lines (:5629–6949). The governing plan names cost-ledger as the phase after audit-routing, and audit-routing has merged. `[verified: Read of shim :4150–5060 and legacy :5620–6960; transcript-analysis-decomposition.md:150–153; gitStatus 11165f9c "Audit routing decomposition (#1149)"]`

**Givens:**
- G1. pytest's `prepend` import mode imports test modules by basename. New test files therefore keep the `test_transcript_*` prefix and stay basename-unique. Reason: pytest owns this behavior. `[verified: transcript-analysis-decomposition.md:59–63]`
- G2. `from m import n` binds `n` at import time. A monkeypatch therefore reaches code only through the binding that code reads at call time. Reason: Python language semantics. `[verified: transcript-analysis-decomposition.md:113]`
- G3. A directly invoked `python3 transcript-analysis.py` finds the package only through CPython's `sys.path[0]`. No in-process test exercises that path. Reason: CPython owns script bootstrap. `[verified: test_transcript_cli_bootstrap.py:1–11]`

Four conditions look like givens but are deliberate declines. Each is recorded as a row or in **Out of scope**:
- `build_parser()` stays in the shim.
- `_UNCONDITIONAL_HEADER_CASES` stays in the legacy file (row 32).
- `cmd_spend_over_threshold` stays in the shim (row 30).
- The `compute_*` functions keep their public names.

**Mechanisms:**

- **M1: One production module, `transcript_analysis/cost_ledger.py`.** `anchors: root, row1, row2, row5, row21, row34`
  - Module docstring: the banner prose from :4159–4163. Add the import-discipline sentence from audit_routing.py:6–7. Add one sentence saying `config_dir` is bound by name the way pr_cost_ledger.py binds it.
  - Body: :4165–4839, verbatim and in source order, under the M3 rename map. Three exceptions:
    1. Drop the two-line comment and the `# noqa: F811` at :4785–4787. They describe a `denials` import the new module doesn't have.
    2. Wrap :4789 in the parenthesized style of :4749–4751 if ruff's E501 flags it once prefixed (row 27).
    3. In `_cost_ledger_report`'s docstring (:4586–4588) and the comment at :4742–4743, rename the three `_compute_*` shim aliases to their qualified public names. After M5 those aliases exist nowhere.
  - `_cost_ledger_report` moves as one unit. Its sentinel gate (:4638–4703) and git-tracked refusal (:4705–4720) stay inline before the write. The refusal keeps calling the already-shared `ledger_common._ledger_path_is_git_tracked` (row 34). Nothing is restructured.
  - Rejected, lighter: leaving the code in the shim fails the root. Folding it into cost.py is ruled out by row 21.
  - Rejected, heavier: a format/report split (row 5).
- **M2: `transcript_analysis/workstream_cost.py`.** `anchors: row1, row4, row6, row31`
  - It holds `_print_workstream_session_stats` and `cmd_workstream_cost` (:4842–4925), verbatim under the M3 map.
  - The docstring follows audit_routing.py:1–8.
  - Rejected: folding into cost.py (row 31) or into cost_ledger.py (row 6).
- **M3: Import discipline and the rename map.** `anchors: G2, row3, row4, row9, row27`
  - Sibling package modules are imported by module and read by attribute:
    - cost_ledger.py: `from transcript_analysis import cost, ledger_common, pricing, render, review_trace, reviewer_yield, scope`
    - workstream_cost.py: `from transcript_analysis import cost, gh_cli, render, scope`
  - Neither module has a `from transcript_analysis.<m> import` line.
  - cost_ledger.py also has `import _config` (precedent: pr_cost.py:22) and `from _config_dir import config_dir` (row 9).
  - cost_ledger.py rename map, from shim bare name to new form:
    - `_resolve_scan_roots` → `scope.resolve_scan_roots`
    - `_resolve_project_scope` → `scope._resolve_project_scope`
    - `_print_resolved_scope` → `scope.print_resolved_scope`
    - `_compute_cost_trend_data` → `cost.compute_cost_trend_data`
    - `_compute_deny_summary_data` → `review_trace.compute_deny_summary_data`
    - `_compute_reviewer_yield_data` → `reviewer_yield.compute_reviewer_yield_data`
    - the four `_REVIEWER_*` constants → `reviewer_yield.<same>`
    - `_pct_value` → `render._pct_value`
    - `_PRICING_FETCH_DATE` and `_warn_if_subagent_format_drift` → `pricing.<same>`
    - the seven ledger_common names (row 3) → `ledger_common.<same>`
    - Unchanged: `_config.*`, `config_dir`, and every stdlib name.
  - workstream_cost.py rename map:
    - The three scope names above, plus `_redaction_ordinals` → `scope._redaction_ordinals`.
    - `_compute_workstream_dollars` → `cost._compute_workstream_dollars`.
    - The five gh_cli names (row 4) → `gh_cli.<same>`.
    - `_fmt_usd` and `_pct_of` → `render.<same>`.
- **M4: Shim changes.** `anchors: row2, row7, row8`
  - Delete :4159–4926.
  - Insert a one-line banner above `cmd_spend_over_threshold`, in the `# --- <name>: <what> ---` form its handoff-nudge neighbors use (:5781, :6689, :7006): `# --- spend-over-threshold: per-week share of session spend at or above the handoff nudge's fire threshold ---`.
  - Add `cost_ledger` and `workstream_cost` to the module-import tuple (:38–57) and to its comment (:33–37).
  - Import `cmd_cost_ledger` and `cmd_workstream_cost` by name. Give each its own block, commented in the style of the pr_cost_export import at :145–149.
  - Delete every name that row 7 marks for deletion.
  - Keep `import _config`, rewritten as `import _config  # noqa: F401 -- test files patch _mod._config's attributes as a module passthrough`. This mirrors :13.
  - Update three comments that name removed readers:
    - the cost block (:86–88): `_compute_workstream_dollars`'s remaining bare caller is handoff-signal-response.
    - the gh_cli block (:112–116): only pr-link reads it now.
    - the reviewer_yield block (:223–229): drop the `_REVIEWER_*` mentions.
  - Don't run ruff's autofix beyond `--select I --fix`. F401's autofix would delete imports that tests read through `_mod` (row 7).
- **M5: Close the three `compute_*` back-imports.** `anchors: row7, row19, row20`
  - Delete shim :103, :221, and :244.
  - Retarget the moved tests' reads (M7). Retarget test_transcript_reviewer_yield.py:406 and :462 to `_mod.reviewer_yield.compute_reviewer_yield_data`.
  - In `test_transcript_analysis_cost_import_direction.py`:
    - Replace `SANCTIONED_NAMES` with two constants. `COST_PUBLIC_FUNCTION_NAMES = {"compute_cost_trend_data"}` serves test (a). That name is now read by cost_ledger.py. `SHIM_BACK_IMPORTED_COST_NAMES: set[str] = set()` serves test (b).
    - Give test (b) a non-vacuity assertion: the shim still has at least one `ImportFrom` of `transcript_analysis.cost`. It keeps `cmd_cost`, `cmd_cost_trend`, and `_compute_workstream_dollars`. Without this, an empty expected set would pass on a module-name typo. It is the same guard `test_package_directory_is_not_empty` gives the architecture-doc test.
    - Rewrite the docstring and both failure messages to describe the closed state. Drop the "need updating once cost-ledger's own migration phase lands" sentence and the stale `:37`/`:54` citations.
- **M6: Two test files, split at the `--record` refusal-gate seam.** `anchors: G1, row21, row22, row23, row24`
  - `tests/test_transcript_cost_ledger.py`, about 825 lines:
    - `_reviewer_dispatch_records` (:5634–5652).
    - Legacy :5655–6377 and :6908–6949: path resolution, read mode, serialization, parser hostility, the gap floor, record parity, write fidelity, auto-create, idempotence, degenerate corpora, concurrency, and CLI wiring.
  - `tests/test_transcript_cost_ledger_record_gates.py`, about 560 lines:
    - Legacy :6379–6906: `TestCostLedgerPublishSafety` and `TestCostLedgerSentinelGate`.
    - This covers every path where `--record` must refuse and write nothing, plus each gate's boundary success cases.
  - Each file gets:
    - A one-line docstring that names the module and its seam.
    - The loader from test_transcript_audit_routing.py:25–32, comment included.
    - `from .conftest import ...` for exactly the builders it uses.
    - Its own stdlib imports.
    - Its classes, verbatim in legacy order under M7.
  - No family helper module: nothing crosses the seam (row 23).
  - Rejected: one file (row 21). Also rejected: importing from the legacy test module, which read-scope, pr-cost, cache-rebuild, and audit-routing all rejected.
- **M7: Test-side retargets.** `anchors: G2, row14, row15, row16, row17, row18, row19, row33, row38`
  - In both new files:
    - `_mod.<moved name>` → `_mod.cost_ledger.<name>`
    - `_mod._compute_cost_trend_data` → `_mod.cost.compute_cost_trend_data`
    - `_mod._compute_deny_summary_data` → `_mod.review_trace.compute_deny_summary_data`
    - `_mod._compute_reviewer_yield_data` → `_mod.reviewer_yield.compute_reviewer_yield_data`
    - `_mod._REVIEWER_*` → `_mod.reviewer_yield._REVIEWER_*`
    - `_mod._pct_value` → `_mod.render._pct_value`
    - The ten string-target patches (row 14) → `monkeypatch.setattr(_mod.cost_ledger, ...)`.
    - The tripwire at legacy :5738 reads `Path(_mod.cost_ledger.__file__).read_text()` instead of `_SCRIPT`.
    - Leave these unchanged: module-object patches (`_mod.scope`, `_mod._config`, `_mod.subprocess`), and reads of names the shim still binds (`_mod._resolve_project_scope`, `_mod.datetime`, `_mod.UTC`, `_mod.ledger_common.*`).
  - test_transcript_ledger_common.py:
    - Retarget its 11 reads (row 18) to `_mod.cost_ledger.`.
    - Update the comment at :157–162 to name `mod.cost_ledger.config_dir`.
  - test_transcript_workstream_cost.py:
    - The docstring names workstream_cost.py.
    - Retarget the 8 reads to `_mod.workstream_cost.`.
    - Change :489 to `monkeypatch.setattr(_mod.gh_cli, "_gh_auth_preflight_ok", ...)`.
    - Leave the 14 `_mod._compute_workstream_dollars` reads alone, because the shim still binds that name.
  - Legacy file:
    - Delete :5629–6950, the block and its banner.
    - Leave `_UNCONDITIONAL_HEADER_CASES`'s `_mod.cmd_cost_ledger` row (:10908) as is (row 32).
    - Remove only the imports ruff's F401 then flags.
- **M8: conftest fixtures.** `anchors: G2, row9, row10, row11, row12, row13`
  - `cost_ledger_file`: read both header constants from `mod.cost_ledger`, and patch `_cost_ledger_path` there.
  - `cost_ledger_enabled`:
    - Patch `mod.cost_ledger.config_dir` instead of `mod.config_dir`. No consumer runs shim-resident code (row 11).
    - Update its docstring's binding names, and its consumer mention of test_transcript_analysis.py's cost-ledger section.
  - `fake_projects`:
    - Add `monkeypatch.setattr(mod.cost_ledger, "config_dir", lambda: tmp_path)`.
    - Docstring: drop cost-ledger from the list of shim-resident commands. Name cost_ledger next to ledger_common and pr_cost_ledger. Change "four bindings" to "five".
  - Module docstring (:4–19): add the two new test files.
  - Rejected lighter alternatives:
    - Have `_cost_ledger_path` read `scope.config_dir()`. This couples ledger-path resolution to the binding root resolution uses. `cost_ledger_enabled` also doesn't repoint scope's binding, so the default path would move silently under that fixture.
    - Read `ledger_common.config_dir`. This borrows another module's by-name import. test_transcript_ledger_common.py:131–150 exists to keep those bindings separate.
- **M9: Bootstrap tests.** `anchors: G3, row35`
  - Add to test_transcript_cli_bootstrap.py:
    - `cost-ledger --help`, asserting `--record` appears in stdout.
    - `workstream-cost --help`, asserting `--check-pr-status` appears in stdout.
    - One real-subprocess run for each command, reusing `_run`, `_seed_priced_account`, and `_isolated_config_env`.
  - cost-ledger run: read mode, against a ledger holding only the canonical header and separator, written as literals the way :248 writes the pr-cost header.
    - Pass `COST_LEDGER_PATH` explicitly in the env, so a contributor's own shell value can't leak in.
    - Assert exit 0 and `2026-W21` in stdout. That is the seed's unrecorded week.
    - Read mode needs no sentinel and no wall clock.
  - workstream-cost run: default mode. Assert exit 0 and `WORKSTREAM COST SOURCES (` in stdout (scope.py:495).
  - Each test's docstring states the fact it proves, as at :582–585.
- **M10: Docs and the package docstring.** `anchors: row36, row29`
  - `__init__.py`: add cost_ledger and workstream_cost to the command-group list.
  - Architecture doc:
    - Add a `### \`cost_ledger.py\`` section and a `### \`workstream_cost.py\`` section. Cover each module's responsibilities, its module imports, and the one name the shim imports from it. For cost_ledger.py, also cover its `config_dir` binding and state that the sentinel gate and git-tracked refusal run inline in `_cost_ledger_report`. For workstream_cost.py, state that it reads `cost._compute_workstream_dollars`.
    - Fix every stale passage row 36 lists.
    - Add a Tests paragraph naming both cost-ledger test files and their seam, and noting that workstream_cost.py's tests live in test_transcript_workstream_cost.py.
  - Leave preserved records untouched (row 29).
- **M11: Commit Verification step 6's prefix-correctness check as a permanent test.** `anchors: row21`
  - New file `claude/.claude/scripts/tests/test_transcript_cost_ledger_module_prefixes.py`, committed alongside the migration rather than run once as scratch.
  - Same shape as Verification step 6's `ast` script: over `cost_ledger.py` and `workstream_cost.py`, every `Attribute(Name(<package module>), attr)` names a top-level binding of that module, none appears in `Store` context, and neither file has a `from transcript_analysis.<m> import` line. Over `test_transcript_cost_ledger.py`, `test_transcript_cost_ledger_record_gates.py`, and `test_transcript_workstream_cost.py`, every `_mod.<module>.<name>` names a top-level binding of that module.
  - This closes a gap the prior four phases also left open (none of them committed an equivalent check) without retrofitting them — scope stays this phase's own two new modules and their test files.
  - Rejected: leaving it scratch-only, per Verification step 6 as originally drafted. A one-time run at migration time can't catch a later edit that reintroduces a stale reference on a code path the rest of the suite doesn't exercise.

**Assumptions:**

1. The moving spans are the cost-ledger banner (:4159–4163), cost-ledger code (:4165–4839), and workstream-cost (:4842–4925). `cmd_spend_over_threshold` and its nudge-log helpers (:4928–5049) stay. `[verified: Read of shim :4150–5060]`
2. Moved top-level names:
   - cost_ledger.py (24): `_COST_LEDGER_COLUMNS`, `_COST_LEDGER_HEADER_LINE`, `_COST_LEDGER_SEPARATOR_LINE`, `_COST_LEDGER_ISO_WEEK_RE`, `_COST_LEDGER_NOTE_MARKDOWN_LINK_RE`, `_CostLedgerParseError`, `_cost_ledger_path`, `_parse_cost_ledger_iso_week`, `_parse_cost_ledger_pct_cell`, `_cost_ledger_note_violation`, `_parse_cost_ledger_row_cells`, `_parse_cost_ledger_file_text`, `_format_reviewer_gap_cell`, `_format_cost_ledger_row`, `_upsert_cost_ledger_row`, `_write_cost_ledger_file`, `_reviewer_gap_pp`, `_COST_LEDGER_READ_HEADER`, `_format_cost_ledger_read_row`, `_print_cost_ledger_read`, `_acquire_cost_ledger_lock`, `cmd_cost_ledger`, `_default_cost_ledger_preamble`, `_cost_ledger_report`.
   - workstream_cost.py (2): `_print_workstream_session_stats`, `cmd_workstream_cost`.
   - `build_parser()` reads both `cmd_*` names bare (:8311, :8433).

   `[verified: grep of the shim's top-level defs and assigns; grep for set_defaults]`
3. cost-ledger reads these names from other modules, grouped by owning module:
   - scope: `_resolve_project_scope`, `print_resolved_scope`, `resolve_scan_roots`
   - cost: `compute_cost_trend_data`
   - review_trace: `compute_deny_summary_data`
   - reviewer_yield: `compute_reviewer_yield_data`, `_REVIEWER_VERDICT_FINDINGS_FOUND`, `_REVIEWER_VERDICT_ZERO_FINDING`, `_REVIEWER_YIELD_ACTIVE_FLOOR`, `_REVIEWER_YIELD_INSUFFICIENT`
   - render: `_pct_value`
   - pricing: `_PRICING_FETCH_DATE`, `_warn_if_subagent_format_drift`
   - ledger_common: `_MACHINE_LABEL_RE`, `_COST_LEDGER_CONFLICT_MARKERS`, `_COST_LEDGER_LOCK_TIMEOUT_S`, `_COST_LEDGER_LOCK_POLL_INTERVAL_S`, `_ledger_path_is_git_tracked`, `_resolve_machine_identity`, `_warn_machine_identity_absent_from_ledger`
   - `_config`: `config_enabled`, `ConfigSchemaEmptyError`, `ConfigSchemaRowTruncatedError`, `schema`
   - `_config_dir`: `config_dir`
   - stdlib: `argparse`, `contextlib`, `errno`, `fcntl`, `math`, `os`, `re`, `stat`, `sys`, `tempfile`, `time`, `datetime.{UTC,date,datetime}`, `pathlib.Path`, `collections.abc.Sequence`

   `[verified: Read of :4159–4839 against the shim's import blocks :8–263]`
4. workstream-cost reads these names from other modules:
   - scope: `resolve_scan_roots`, `_resolve_project_scope`, `print_resolved_scope`, `_redaction_ordinals`
   - cost: `_compute_workstream_dollars`
   - gh_cli: `_git_remote_origin_host_and_owner_repo`, `_gh_auth_preflight_ok`, `_resolve_pinned_gh_repo`, `_gh_discover_merged_prs`, `_gh_discover_closed_unmerged_pr_branches`
   - render: `_fmt_usd`, `_pct_of`
   - stdlib: `argparse`, `statistics`, `sys`, `datetime.{UTC,datetime}`

   `[verified: Read of :4842–4925; shim :254 imports _redaction_ordinals from scope]`
5. No package module reads a moved name in code, and neither does any non-test code outside the shim. pr_cost.py, pr_cost_ledger.py, reviewer_yield.py, review_trace.py, cost.py, and render.py mention some of these names, but only in comments and docstrings, and every function they name still exists after the move. `[verified: grep of transcript_analysis/, evals/, plugins/, claude-skills/, claude/.claude/{hooks,skills,agents}/]`
6. Neither moved group calls a helper from the other. `[verified: Read of both spans]`
7. These shim names are left unused by production code after the move. The list gives each one's remaining readers and what happens to it:
   - `errno`, `fcntl`, `stat`: no readers. Delete.
   - The seven ledger_common names: no readers. Delete the block (:127–136). The `ledger_common` module import stays.
   - gh_cli's `_gh_auth_preflight_ok`, `_resolve_pinned_gh_repo`, `_gh_discover_merged_prs`, `_gh_discover_closed_unmerged_pr_branches`: only test_transcript_workstream_cost.py:489 reads one, and it is retargeted. Delete. `_git_remote_origin_host_and_owner_repo` stays because pr-link uses it (:2253).
   - The four `_REVIEWER_*` constants, `_pct_value`, `_PRICING_FETCH_DATE`, `_compute_cost_trend_data`, `_compute_deny_summary_data`: only moved tests read them. Delete.
   - `_compute_reviewer_yield_data`: moved tests plus test_transcript_reviewer_yield.py:406 and :462 read it. Retarget those readers, then delete.
   - `_config`: test_transcript_pr_cost.py:607–695 and test_transcript_pr_cost_export_accounts.py:249–342 patch `_mod._config`. Keep it, with the noqa comment.
   - Still used elsewhere in the shim, so kept: `statistics`, `contextlib`, `tempfile`, `time`, `math`, `date`, `datetime`, `UTC`, `config_dir`, `_redaction_ordinals`, `_warn_if_subagent_format_drift`, `_compute_workstream_dollars` (:7586), `_fmt_usd`, `_pct_of`, and the three scope names.

   `[verified: grep of the shim for each name outside :4159–4926; grep of scripts/tests/ for _mod.<name> and setattr(_mod, "<name>")]`
8. ruff selects `F`. An unused import therefore fails lint, and F821 catches a missed rename. `RUF100` is not selected, so a stale `noqa` does not fail lint. `[verified: pyproject.toml:6]`
9. Every package module that calls `config_dir()` binds it by name from `_config_dir`: 3 of 3 (scope.py:25–27, ledger_common.py:16, pr_cost_ledger.py:20). `[verified: grep of transcript_analysis/]`
10. `_config.config_enabled` resolves `config_dir` through `_config.py`'s own by-name binding (`_config.py:23`). No per-module `config_dir` patch reaches the sentinel check.
    - The moved `setattr(_mod, "config_dir", ...)` sites at :6219 and :6483 therefore already have no effect on it.
    - Their refusals come from the autouse fixture's `CLAUDE_CONFIG_DIR`, which holds no sentinel (conftest.py:974).
    - Retargeting keeps them aimed at the binding the moved code actually has. It does not make them load-bearing.

    `[verified: _config.py:23, :402, :431; conftest.py:974; Read of both tests]`
11. The conftest fixtures work as follows:
    - `cost_ledger_file` patches `mod._cost_ledger_path` and reads `mod._COST_LEDGER_HEADER_LINE` and `mod._COST_LEDGER_SEPARATOR_LINE` (:904–911).
    - `fake_projects` patches four `config_dir` bindings (:875–879).
    - `cost_ledger_enabled` patches `mod.config_dir` and `mod.ledger_common.config_dir` (:944–945).
    - `cost_ledger_enabled` is used only by the moved tests and test_transcript_ledger_common.py. None of them runs shim-resident code.

    `[verified: Read of conftest.py:850–946; grep of consumers]`
12. If `cost_ledger_file` still pointed at the shim, it would not intercept `cost_ledger.py`'s own `_cost_ledger_path()`. The header test's cost-ledger row runs in read mode, so it would hit the missing-ledger exit (shim :4617–4629) before printing its header. `[unverified: inferred from the code path; Verification step 5 runs it]`
13. Every consumer of conftest's `fake_projects` loads the shim as `_mod`. The fixture already patches `mod.ledger_common` and `mod.pr_cost_ledger`, which only the shim exposes. test_token_analyzer.py overrides the fixture locally (:100–104). Adding `mod.cost_ledger` is therefore safe. `[verified: conftest.py:878–879; test_token_analyzer.py:100–104]`
14. Patch sites in the moved block:
    - Retarget (10): `_cost_ledger_path` at :5716, :5728, :6176, :6195, :6216, :6353, :6814; `config_dir` at :6219, :6483; `datetime` at :6941.
    - Leave alone (module-object patches): `_mod.scope` at :6390, :6931; `_mod._config` at :6506, :6526–6527, :6547–6548; `_mod.subprocess` at :6687, :6713, :6771.

    `[verified: grep of setattr sites in the block]`
15. The `datetime` patch is load-bearing. `cmd_cost_ledger` reads its own module's `datetime.now` (:4564), and the test's corpus sits in 2026-W23. Left on the shim, the patch misses: the real clock's week has no priced turns, and `--record` exits 1. `[verified: Read of :4564, :4756–4762, and legacy :6908–6948; step 5 runs it]`
16. `test_old_ledger_file_not_found_wording_absent_from_source` (:5734–5738) greps `_SCRIPT`. Once the code leaves the shim, it would pass without checking anything. `[verified: Read]`
17. test_transcript_workstream_cost.py exists (549 lines) and holds every workstream-cost test.
    - It reads `_mod.cmd_workstream_cost` and `_mod._print_workstream_session_stats` at :381, :400, :419, :428, :456, :492, :516, :540.
    - It patches `_mod._gh_auth_preflight_ok` at :489. That is the shim binding `cmd_workstream_cost` reads bare today (:4897).
    - The cost-ledger block has no workstream test.

    `[verified: Read of :1–40 and :320–549; grep]`
18. Moved names are also read outside the block:
    - test_transcript_ledger_common.py at :45, :49, :83, :87, :168, :169, :344, :350, :363, :410, :427.
    - Legacy :10908 (`_mod.cmd_cost_ledger`), which stays valid because the shim still imports that name for `build_parser()`.

    `[verified: grep across claude/]`
19. test_transcript_reviewer_yield.py reads `_mod._compute_reviewer_yield_data` at :406 and :462. That file already reads `_mod.reviewer_yield.` twice elsewhere. `[verified: grep]`
20. The import-direction test pins the shim's back-import from cost to `{"compute_cost_trend_data"}` (:28, :76–84). It pins cost.py's public function surface to the same set (:65–73). Its docstring anticipates this phase (:14–17). After M5, cost.py's surface is unchanged and the shim's back-import set is empty. `[verified: Read]`
21. Sizes:
    - The test slice :5629–6949 is 1,321 lines.
    - The M6 files come to about 825 and 560 lines: moved lines plus a header of about 30 lines, estimated from test_transcript_audit_routing.py:1–32.
    - The production spans are 675 and 84 lines, so about 700 and 105 with headers.
    - cost.py is 1,230 lines and conftest is 1,047.

    `[verified: line arithmetic over Read spans; grep line counts]`
22. Each decomposition phase targets a 1,000-line limit for modules and test files alike. No check enforces it yet. `[verified: code-file-size-splits.md:175; docs/design-decisions/code-file-line-limit.md:19, :50–57; no line-limit test under claude/]`
23. The seam holds up. Legacy :6379–6906 is exactly `TestCostLedgerPublishSafety` and `TestCostLedgerSentinelGate`. `_reviewer_dispatch_records` is used only by `TestCostLedgerRecordParity` (:6007, :6015, :6078, :6084). `[verified: Read; grep]`
24. No moved test reads a hook or a SKILL.md by path. Neither new file therefore needs select-tests.py's `TRANSCRIPT_ANALYSIS_TEST_GLOB` exception (select-tests.py:65–73). `[verified: Read of the whole block]`
25. select-tests.py maps any `scripts/` path to `scripts/tests/`. It maps an architecture-doc edit to `scripts/tests/`, `hooks/tests/`, and `skills/tests/`. `[unverified: relayed from audit-routing-decomposition.md row 24; Verification step 1 confirms it empirically]`
26. Neither command carries the multi-root `--no-redact` guard that the governing plan wants tested before those commands split (transcript-analysis-decomposition.md:298–303). `[verified: Read of :4159–4925]`
27. Once prefixed, :4789 comes to about 131 columns, against a 130-column limit. `[verified: my character count; pyproject.toml:2]`
28. The new `test_transcript_cost_ledger.py` imports `_priced_opus` from conftest (used at legacy :6138). #1149 moved that builder into conftest. Reverting #1149 after this phase merges therefore requires reverting this phase first. That last-in-first-out order holds for any stacked phase. `[verified: audit-routing-decomposition.md:36–37; test_transcript_analysis.py:40]`
29. Some preserved records cite the moved code at shim locations: docs/reports/**, docs/design-decisions/review-trace-and-cost-ledger-widen.md:19, and claude/.claude/hooks/tests/config-schema-audit.md:205–213. The config-schema-audit.md line cites are already stale. No test reads any of those cites. `[verified: grep; grep of config-schema-audit.md's readers]`
30. Relayed Step 4 answer 1 excludes `cmd_spend_over_threshold`. Per the relay, that was the session's own scope call, not the engineer's. Supporting facts: the function calls `_extract_rearm_session_turns` (:5900) and `_print_nudge_log_diagnostic` (:5033), and both live in the handoff-nudge region. `[unverified as an engineer decision; supporting facts verified: Read of :4928–5049, grep]`
31. Relayed Step 4 answer 2 gives workstream-cost its own module. It arrived without an engineer quote. Supporting fact: cost.py is already 1,230 lines. `[unverified as an engineer decision; supporting fact verified: row 21]`
32. `_UNCONDITIONAL_HEADER_CASES` and its two classes stay in the legacy file until the final phase. `[verified: transcript-analysis-decomposition.md:157–161]`
33. Moved tests read a moved module's names as `_mod.<module>.<name>`. In test_transcript_audit_routing.py, 18 of 19 `_mod.` reads go through `_mod.audit_routing.`, even though the shim also imports its `cmd_*` names. `[verified: grep -o of that file]`
34. The governing plan requires the sentinel and git-tracked checks to stay in the same function as the write (transcript-analysis-decomposition.md:304–308). Both sit inside `_cost_ledger_report` (:4638–4703, :4705–4720). The function that does the git-tracked check is already shared at ledger_common.py:164. `[verified: Read; grep]`
35. Every previously moved family has `--help` and real-subprocess tests in test_transcript_cli_bootstrap.py, for example cost at :214–236 and audit-routing at :550–641. Three helpers are reusable: `_run` (:27–37), `_seed_priced_account` (:171–196, seeded 2026-05-19, which falls in 2026-W21), and `_isolated_config_env` (:199–211). `[verified: Read]`
36. The architecture-doc drift test requires a `### \`<name>.py\`` heading for every package module (test_transcript_analysis_architecture_doc.py:14–49). These passages go stale after the move:
    - :16–18: the shim's back-import list, and the sentence "Cost-ledger still calls…"
    - :115–117
    - :125–126
    - :207–209
    - :226–232
    - :241–245
    - :339–342

    `[verified: Read]`
37. New files under `scripts/` need no stow change, because `~/.claude/scripts` is a single folded symlink. `[verified: transcript-analysis-decomposition.md:120]`
38. `TestRootsThreadingSpy` already spies `_mod.scope._resolve_project_scope` and `_mod.scope.print_resolved_scope` (:11113–11114). `cost_ledger.py`'s `scope.*` calls are therefore still intercepted. `[verified: Read]`

## Critical files

### Create
- `claude/.claude/scripts/transcript_analysis/cost_ledger.py`: M1, M3.
- `claude/.claude/scripts/transcript_analysis/workstream_cost.py`: M2, M3.
- `claude/.claude/scripts/tests/test_transcript_cost_ledger.py`: M6, M7.
- `claude/.claude/scripts/tests/test_transcript_cost_ledger_record_gates.py`: M6, M7.
- `claude/.claude/scripts/tests/test_transcript_cost_ledger_module_prefixes.py`: M11.

### Modify
- `claude/.claude/scripts/transcript-analysis.py`: M4, M5.
- `claude/.claude/scripts/transcript_analysis/__init__.py`: M10.
- `claude/.claude/scripts/tests/test_transcript_analysis.py`: M7. Delete :5629–6950, then remove the imports F401 flags.
- `claude/.claude/scripts/tests/conftest.py`: M8.
- `claude/.claude/scripts/tests/test_transcript_workstream_cost.py`: M7.
- `claude/.claude/scripts/tests/test_transcript_ledger_common.py`: M7.
- `claude/.claude/scripts/tests/test_transcript_reviewer_yield.py`: M5 (:406, :462 only).
- `claude/.claude/scripts/tests/test_transcript_analysis_cost_import_direction.py`: M5.
- `claude/.claude/scripts/tests/test_transcript_cli_bootstrap.py`: M9.
- `docs/transcript-analysis-architecture.md`: M10.

### Reuse rather than reimplement
- `transcript_analysis/pr_cost_ledger.py:1–21` is the closest structural template for cost_ledger.py's header, stdlib set, and `config_dir` binding.
- `transcript_analysis/audit_routing.py:1–8` supplies the docstring's import-discipline sentence.
- `test_transcript_audit_routing.py:25–32` supplies the test-file loader and its sys.modules comment.
- Call `ledger_common._ledger_path_is_git_tracked`, which is already shared. Don't copy it.
- conftest's builders and fixtures (`_cost_ledger_args`, `_cost_ledger_row`, `_priced`, `_priced_opus`, `_hook_deny`, `_write_subagent_dispatch`, and so on) are imported, not redefined.
- test_transcript_cli_bootstrap.py already has `_run`, `_seed_priced_account`, and `_isolated_config_env`.

### Dispatch
One `code-writer` dispatch covers every file above. Its verification commands are Verification step 1 (select-tests.py) and step 8 (ruff). Capture the Verification step 0 baseline before the dispatch edits anything.

The PR body must state row 28's revert-order dependency on #1149, and must list every file over 1,000 lines (Verification step 9).

## Verification

Run everything from the worktree root. `<venv>` means `../../../.venv`, per README.md's Tests section. Scratch files live outside the repo, and every corpus is synthetic.

0. **Baseline, before any edit.**
   - Snapshot every file this plan modifies.
   - Save the `<venv>/bin/pytest --collect-only -q` IDs for:
     - test_transcript_analysis.py
     - test_transcript_workstream_cost.py
     - test_transcript_ledger_common.py
     - test_transcript_reviewer_yield.py
     - test_transcript_analysis_cost_import_direction.py
     - test_transcript_cli_bootstrap.py
   - Capture `--help` for the top level, `cost-ledger`, and `workstream-cost`.
   - Seed one synthetic account in scratch:
     - one priced `claude-sonnet-5` turn on a named branch, timestamped in the current ISO week;
     - `machine-id` containing `7e57c0de`;
     - `.cost-ledger-enabled` present.
   - Pin `CLAUDE_CONFIG_DIR` to that account, `TRANSCRIPT_CONFIG_DIRS_FILE` to a nonexistent path, and `COST_LEDGER_PATH` to a scratch ledger.
   - Invoke `<venv>/bin/python3 claude/.claude/scripts/transcript-analysis.py`. For each run below, in order, capture stdout, stderr, the exit code, and the ledger's bytes:
     - (a) `cost-ledger` with no ledger file. Expect exit 1.
     - (b) `cost-ledger --record --note baseline`.
     - (c) `cost-ledger`, in read mode.
     - (d) `cost-ledger --record` again. Expect a duplicate refusal and exit 1.
     - (e) Run (b) again with the sentinel removed. Expect exit 1.
     - (f) Run (b) again with a second declared root, and with `git init` run in the ledger's directory. Expect exit 2.
     - (g) `workstream-cost`.
   - `--check-pr-status` is excluded: it needs live `gh`, and its stubbed tests already cover it.
   - Run the before and after captures within the same ISO week.
   - Record `wc -l` for the shim, the legacy test file, and conftest.
1. **Scoped suite.** Run `<venv>/bin/python3 claude/.claude/scripts/select-tests.py`. Every selected test must pass.
   - Confirm the selection included:
     - the architecture-doc drift test;
     - the import-direction test;
     - `claude/.claude/tests/test_pytest_collection_config.py`, for `TestConftestModuleNamesAreUnique` and `TestNoBareSameDirectorySiblingImports`;
     - test_transcript_cli_bootstrap.py;
     - `claude/.claude/hooks/tests/test_nudge_error_mode_analysis.py`.
   - If the last one is missing, run it alone.
   - If select-tests.py cannot map a path, per CLAUDE.md that is a bug in its rule table, not a reason to run the full suite.
2. **Test-ID parity.** Strip each ID's file prefix and sort the lists.
   - Step 0's legacy list must equal the combined post-move lists of the legacy file and the two new files, duplicates included.
   - The ID lists for test_transcript_workstream_cost.py, test_transcript_ledger_common.py, test_transcript_reviewer_yield.py, and the import-direction test must be unchanged.
   - test_transcript_cli_bootstrap.py must gain exactly the four M9 tests.
3. **CLI parity.** Every `--help` capture and every run (a)–(g) must match step 0 byte for byte, ledger bytes included.
4. **Leftovers and single home.**
   - `git grep -nE '^(def |class )?(<the 26 moved names, |-joined>)\b' claude/.claude/scripts/transcript-analysis.py` returns nothing. Each of the 26 names is defined exactly once under `transcript_analysis/`.
   - `git grep -nE '_mod\.(<the 26 moved names>|_compute_cost_trend_data|_compute_deny_summary_data|_compute_reviewer_yield_data|_REVIEWER_[A-Z_]+|_pct_value)\b' claude/.claude/scripts/tests/` returns only the `_mod.cmd_cost_ledger` row in `_UNCONDITIONAL_HEADER_CASES`.
   - No `setattr(<_mod|mod>, "_cost_ledger_path"|"datetime"|"_gh_auth_preflight_ok"|"config_dir", …)` string-target patch remains in these places:
     - the two new files;
     - conftest's `cost_ledger_file` and `cost_ledger_enabled`;
     - test_transcript_workstream_cost.py.
5. **Negative controls for each patch.** These turn rows 12, 15, and 17 into runs. Make each edit by hand, run the check, undo the same edit by hand, and confirm `git diff --stat` matches its state before the control. Don't use `git stash` or `git checkout`: the tree holds the phase's uncommitted work.
   - Point `cost_ledger_file`'s patch back at `mod`. `-k "TestAllSubcommandsSingleRootHeader and cost-ledger"` must fail.
   - Point the CLI-wiring test's `datetime` patch back at `_mod`. That test must fail.
   - Point test_transcript_workstream_cost.py:489's patch back at `_mod`. `test_auth_preflight_failure_exits_1_with_stderr_message` must fail.
   - Add `from transcript_analysis.cost import compute_cost_trend_data as _x` to the shim. The import-direction test's shim test must fail.
   - The two `config_dir` retargets have no negative control (row 10).
6. **Prefix correctness.** Satisfied by running M11's committed test (`test_transcript_cost_ledger_module_prefixes.py`), not a scratch script:
   - Over cost_ledger.py and workstream_cost.py:
     - Every `Attribute(Name(<package module>), attr)` names a top-level binding of that module.
     - None appears in `Store` context.
     - Neither file has a `from transcript_analysis.<m> import` line.
   - Over every new or edited test file: every `_mod.<module>.<name>` names a top-level binding of that module.
7. **Move fidelity.**
   - **Production.**
     - Apply M3's rename map in reverse, as a text substitution, to each new module.
     - Each top-level node's `ast.dump` must equal the corresponding node from step 0's spans.
     - The `#`-comment diff must be exactly the three lines M1 drops at :4785–4787. The reverse map undoes M1's docstring renames.
     - The module docstring's banner text must equal :4159–4163.
   - **Tests.**
     - Reverse M7's map.
     - Each class's and function's AST must equal its legacy slice, except the tripwire's `_SCRIPT` read, which is the one planned difference.
     - The comment diff must be empty.
   - After the commit, report `git blame -C -C -s` line counts for each new file.
8. **Lint.** `<venv>/bin/ruff check claude/.claude/scripts/` must be clean.
9. **Sizes.** Report measured `wc -l` for every new and shrunk file. Flag in the PR body any file over 1,000 lines. conftest, the shim, and the legacy test file already are.
10. **This phase's own revert.** In a throwaway worktree, run `git revert --no-commit` on the phase's squashed commit.
    - Every modified file must be byte-identical to step 0's snapshot.
    - Every created file must be gone.
    - This validates only this phase's isolated revertibility, immediately after it lands with nothing built on top. It does not exercise row 28's LIFO ordering constraint — that only becomes checkable once a later phase actually lands on top of this one and a revert of this phase is attempted first. No verification step in this plan can exercise that; it's a structural limit of validating rollback for a stacked-phase migration at the time each phase merges, not a gap this phase can close.

## Out of scope

- **`cmd_spend_over_threshold` and its nudge-log helpers**: `_NUDGE_LOG_MAX_READ`, `_read_bounded_log_lines`, and `_print_nudge_log_diagnostic` (shim :4928–5049). They stay for a future handoff-nudge-family phase alongside rearm-backtest (:5781), plan-boundary (:6689), and handoff-signal-response (:7006). They share only a region of the shim with cost-ledger (row 30). Dependency map for whoever plans that phase:
  - `cmd_spend_over_threshold` calls `_extract_rearm_session_turns` (:5900). The relayed consult also named `_hook_effective_fire_threshold` (:5845) as part of that machinery; I did not re-read it.
  - `_read_bounded_log_lines` is shared with rearm-backtest's `_parse_nudge_log_entries` (:6060), per its own docstring at :5019–5021.
  - `_print_nudge_log_diagnostic` reads the shim's own `config_dir` binding (:5040).
  - `TestSpendOverThreshold` (legacy :6960–7114) patches `_mod.config_dir` at :7108.
  - `_spend_over_threshold_args` (:6956) supplies `_UNCONDITIONAL_HEADER_CASES` (:10898).
- **Renaming the three `compute_*` functions back to private names.** Package modules already read each other's private names; pr_cost.py reads `cost._compute_pr_cost_branch_totals`, for example. But the rename is churn with no behavior value, in a diff whose value is that it preserves behavior. M5's test (a) keeps cost.py's public surface pinned.
- **Retargeting the other `_mod.` reads.** This covers test_transcript_workstream_cost.py's 14 `_mod._compute_workstream_dollars` reads and the pr-cost test files' `_mod._config` patches. Both shim bindings stay live, so neither change is required.
- **Making the two `config_dir` patches that have no effect (row 10) load-bearing.**
- **Deduplicating `_acquire_cost_ledger_lock` and `_write_cost_ledger_file`** against their near-copies in pr_cost_ledger.py (docs/reports/2026-08-22-discovery-audit/findings.md:266, SC4). A refactor must not ride along with a behavior-preserving move.
- **The shim module docstring's stale claims** (:2–5). It says pr-link is the only subcommand that calls gh, and its write-flag list omits `cost-ledger --record`. Both predate this phase; raise them to the reviewer.
- **Stale shim citations in preserved records** (row 29).
- **conftest's size.** It was 1,047 lines, over the limit, before this phase. Its consumer-list docstring is also incomplete beyond the two files this phase adds.
