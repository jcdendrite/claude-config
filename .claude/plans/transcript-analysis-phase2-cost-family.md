# Transcript-analysis decomposition — Phase 2 (cost command family)

## Context

Move the cost command family out of the monolithic
`claude/.claude/scripts/transcript-analysis.py` (10,097 lines, 26 subcommands)
into the `transcript_analysis/` package, continuing the phased decomposition
whose governing plan is `.claude/plans/transcript-analysis-decomposition.md`
(Phase 1 merged as PR #681, commit `98a3615`). Phase 1 moved only "leaf"
modules with no dependency on any `cmd_*` function (`corpus.py`, `scope.py`,
`redaction.py`, `pricing.py`, `render.py`); this phase moves the first actual
command group, per the plan's Phases section ("cost family (2,362 test
lines)" listed largest-first, ahead of reviewer-yield, review-trace,
audit-routing, and cost-ledger). Doing this now keeps the decomposition
moving while the shape of a command-group phase is still fresh from Phase 1.

This session re-verified every line number, symbol, and test-class boundary
in a fresh worktree rather than trusting the handoff brief's own survey —
several of its details turned out to be incomplete (see Approach below) and
one (the assumption-20 test-coverage gap) turned out to already be closed.

## Approach

Create `transcript_analysis/cost.py` holding the cost family's production
code (`cmd_cost`, `cmd_cost_trend`, `_cost_report`, and every helper used
only by them), promote four small generic argparse/formatting helpers that
`cost.py` needs but Phase 1 didn't discover into the existing leaf modules
they already belong with, and move the corresponding 15 test classes into a
new `tests/test_transcript_cost.py`. `transcript-analysis.py` re-imports
everything back so every existing call site, test monkeypatch, and the
`_UNCONDITIONAL_HEADER_CASES` cross-family table keep working unchanged.

### Assumption ledger

**Root problem:** same as the governing plan's — one file is the only
importable unit and one test file shares 964+ tests with no seam smaller
than the whole module. This phase's slice: the cost family is the largest
still-monolithic command group.

**Given** (fixed, outside this plan's reach):

- G1. The governing plan's Phases section already fixed "cost family,
  largest-first" as the next group to move — not re-litigated here.
  `[verified: .claude/plans/transcript-analysis-decomposition.md:134-139,
  quoted below]`

**Mechanisms:**

- **M1 — Dedicated `cost.py` module rather than folding the three
  `_print_*_table` helpers into the existing `render.py`.** `anchors: root`.
  Lighter alternative considered and rejected: `render.py` already holds
  generic, stateless formatting helpers (`_fmt_usd`, `_pct_of`, `_fam`) with
  nothing that prints a whole report table to stdout for one command;
  dropping cost's three ~20-line table printers there would be the lightest
  *file-count* option but breaks that module's own cohesion (confirmed by
  reading `render.py` this session — 286 lines, no report-printing shape
  anywhere in it). A dedicated module matches the plan's own stated
  per-command-group pattern and keeps `render.py`'s "generic formatting,
  no command awareness" contract intact.
- **M2 — Promote `_branch_filter`, `_parse_since_nd_arg`, `_projects_glob`
  into `scope.py`, and `_fmt_date` into `render.py`, in this phase.**
  `anchors: row3`. These four are called directly from inside the
  cost-family code being moved (verified by scanning every monolith-defined
  name called within `transcript-analysis.py:3932-4542,6144-6326` — see
  assumption 3) but are themselves leaf-shaped (pure `argparse.Namespace ->
  value` or `float -> str`, no `cmd_*` dependency) and are shared by 10-14
  other still-monolithic command groups apiece. Two lighter alternatives
  were considered and rejected: (a) leave them in the monolith and have
  `cost.py` import them back — impossible, since `transcript-analysis.py`
  is not an importable package member and the monolith itself imports
  `cost.py` before these names would be defined, a real circular-import
  dead end, not a style preference; (b) duplicate their bodies into
  `cost.py` — rejected because these are production code, where CLAUDE.md's
  DRY rule (not the DAMP test-code exception) governs, and duplicating a
  10-14-call-site helper risks silent drift the next time one of the other
  9 groups' phases touches it. Promoting them now is exactly Phase 1's own
  leaf-module criterion applied to a dependency Phase 1 didn't need to
  discover; `_projects_glob`'s neighbor in the file, `_is_fresh_user_prompt`
  (`:153-177`), is not moved — it is not called anywhere in the cost block
  (confirmed by the same scan), so it stays out under the same rule.
- **M3 — Promote `compute_cost_trend_data` to a public name; keep every
  other moved symbol at its current (mostly already-private) name.**
  `anchors: row5`. `_compute_cost_trend_data` is called from two functions
  that stay in the monolith for now (`_cost_ledger_report:7243`,
  `_print_cost_ledger_read:7060`, both cost-ledger, a separate later phase
  per the plan's Phases section) — a forward dependency from the
  not-yet-migrated monolith back into `cost.py`. This mirrors the governing
  plan's own Naming section precedent exactly: Phase 1 promoted only the
  four names that crossed a real module boundary
  (`read_session_file`, `dedup_turns_by_request_id`, `resolve_scan_roots`,
  `print_resolved_scope`) and left everything else private. The shim
  re-imports it aliased back (`from transcript_analysis.cost import
  compute_cost_trend_data as _compute_cost_trend_data`) so both the
  cost-ledger call sites and `TestCostLedgerRecordParity`'s direct
  `_mod._compute_cost_trend_data(...)` call (`tests/test_transcript_analysis.py:10677`,
  a cost-ledger test staying behind) keep working with no edits. No lighter
  alternative applies — a plain rename-on-export is already the minimal
  fix; the alternative of leaving it private and having the monolith import
  the private name works identically in Python (no enforced privacy) but
  reproduces the exact "public API reached through a private door" signal
  mismatch Phase 1's own Naming section rejected.
- **M4 — Split the 15 cost-family test classes into a new
  `tests/test_transcript_cost.py`; DAMP-duplicate small cross-referenced
  local test helpers, promote genuinely file-wide ones to `conftest.py`.**
  `anchors: row8`. The governing plan's Naming section already commits to
  `test_transcript_*`-prefixed new test files (assumption/Naming, `G1`) —
  this is the first phase to actually create one, since Phase 1 moved no
  `cmd_*` test coverage. Every local (non-`conftest.py`) helper function the
  15 classes call was enumerated this session (regex scan over the
  corrected per-class line ranges, cross-checked against every `^def` in
  `test_transcript_analysis.py`) and falls into three buckets — see
  assumption 9 for the concrete list. Lighter alternative considered:
  import the small-cross-reference helpers from the old file into the new
  one instead of duplicating. Rejected under CLAUDE.md's named DAMP
  exception for test code — a 2-3-call-site helper (`_cost_args`,
  `_write_cost_root`, the `_extract_*`/`_md_table_cols` family) is cheaper
  to duplicate than to add a cross-test-file import dependency, and matches
  how `_cost_args` et al. are themselves plain fixture-builder functions,
  not pytest fixtures needing conftest's injection machinery.

**Assumptions:**

| # | Assumption | Tag |
|---|---|---|
| 1 | Current symbol locations in `transcript-analysis.py`: `_session_branch_index:3932-3957`, `_attributed_branch:3958-3990`, `cmd_cost:3991-4007`, `_accumulate_per_account_turn:4008-4021`, `_print_token_class_table:4022-4044`, `_print_model_id_table:4045-4058`, `_print_thread_table:4059-4079`, `_cost_report:4080-4542`, `cmd_cost_trend:6144-6154`, `_compute_cost_trend_data:6155-6214`, `_cost_trend_report:6217-6326`. `_accumulate_per_account_turn`, `_session_branch_index`, and `_attributed_branch` are not in the handoff brief's symbol list but are cost-family-exclusive (confirmed by whole-file grep for each name — no caller outside `_cost_report`/`cmd_cost`). | `[verified: grep + Read this session against 98a3615-based worktree]` |
| 2 | The multi-root `--no-redact` refusal is centralized in `_resolve_cost_roots` (`transcript_analysis/scope.py:551-557`, moved there in Phase 1) plus 8 point-of-use defense-in-depth duplicates for direct `_*_report`/`cmd_*` callers that bypass it — cost's own copy is `transcript-analysis.py:4150-4156`. The governing plan's assumption 20 obligation ("later phases must keep each guard co-located with its command", Out-of-scope: "the later command-group phases should land it before splitting those `cmd_*` bodies") reads per-body, not as a blanket 8-site test in whichever phase moves first — and is already satisfied for the cost site: `TestCostMultiRootRedaction::test_no_redact_refused_at_cost_report_directly` (or its current name; docstring at `tests/test_transcript_analysis.py:6638-6640` states explicitly it tests `_cost_report`'s own guard, "bypassing that CLI-level boundary") already calls `_cost_report(no_redact=True, roots=[root_a, root_b])` directly and asserts `SystemExit(2)`. This test moves intact with `TestCostMultiRootRedaction`; **no new test is needed this phase**. | `[verified: Read of the test body and its docstring this session, plus grep confirming the guard's 8 sites and their line numbers]` |
| 3 | The only monolith-defined (not-yet-in-package) names called from anywhere inside the cost-family code block (`transcript-analysis.py:3932-4542,6144-6326`) are `_branch_filter`, `_fmt_date`, `_parse_since_nd_arg`, and `_projects_glob` — every other call resolves to a name already re-exported from the Phase-1 package (`scope.py`/`corpus.py`/`pricing.py`/`render.py`/`redaction.py`) or to another cost-family symbol moving in the same commit. | `[verified: Python script this session — extracted every bare `name(` call in the two ranges, filtered to names matching a top-level `^def` in transcript-analysis.py]` |
| 4 | `_branch_filter` has 14 total call sites, `_parse_since_nd_arg` 14, `_fmt_date` 10, `_projects_glob` (unchanged from Phase 1's own note) shared across many groups — all well beyond the cost family, confirming they are generic multi-group helpers, not cost-specific, and that moving them is a leaf-module promotion, not a cost-family-scope expansion. | `[verified: grep -c per name this session]` |
| 5 | `_compute_cost_trend_data` is called from `_cost_ledger_report:7243` and `_print_cost_ledger_read:7060`, both cost-ledger (a separate, later phase per the plan's Phases section, not folded into this PR). `TestCostLedgerRecordParity` (`tests/test_transcript_analysis.py:10640`, staying behind) calls `_mod._compute_cost_trend_data(...)` directly at `:10677`. | `[verified: grep this session]` |
| 6 | The governing plan's Phases section lists cost-trend as part of the single "cost family" entry, not a separate phase: *"Phases 2..N — command groups, largest first per assumption 15: cost family (2,362 test lines), reviewer-yield (1,413), review-trace (1,380), audit-routing (1,290), cost-ledger (1,128), then the remainder."* — no separate "cost-trend" line anywhere in that section. `cmd_cost_trend` does not call `_cost_report` or the `_print_*_table` helpers, but every other helper it uses (`_resolve_cost_roots`, `_resolve_project_scope`, `_redaction_ordinals`, `_scan_root_transcripts`, `_dedup_turns_by_request_id`, `_parse_ts`, `_fam`, `_context_bucket`, `_price_turn`) is already in the Phase-1 package. | `[verified: Read of the plan file's Phases section this session; grep for cmd_cost_trend's call graph]` |
| 7 | The 15 cost-family test classes are **not contiguous** — interleaved with `TestScanRootTranscripts`, `TestPriceTurnArity`, `TestPriceTurnSpeedGeoMultipliers`, `TestDedupTurnsByRequestId`, `TestContextDistribution`, and others staying behind. Corrected line ranges (re-derived this session; the handoff brief's own table had two boundary errors — see row 8): `TestCost:5576-6070`, `TestCostResolveRoots:6088-6220`, `TestCostMultiRootReport:6221-6389`, `TestCostCorpusCoverageWarning:6390-6467`, `TestCostMultiRootRedaction:6534-6692`, `TestCostByProject:6693-6877`, `TestCostByAccount:6878-7033`, `TestCostThreadSplit:7034-7119`, `TestCostMarkdownTablePrinters:7120-7157`, `TestCostBranchFilter:7383-7439`, `TestCostTokensColumn:7440-7474`, `TestCostSummary:7513-7965`, `TestCostWorktreeAgentBranchCarryForward:7966-8071`, `TestCostTrend:9596-9697`, `TestCostTrendConfigDir:9698-9920`. Sum: ~2,470 lines of class bodies (not counting the local preamble helpers in row 9) — the plan's stated "2,362 test lines" for the whole group is a coarse prior estimate, not re-derived here to the line; actual moved-line count should be reported from the real diff, not either estimate. | `[verified: `awk '/^class |^def /'` sweep over the full span this session, cross-checked against every class-start grep hit]` |
| 8 | Two of the handoff brief's/first-pass discovery's class-end boundaries were wrong: `TestCost` actually ends at `:6070` (not `:6087`) — `:6071-6086` is the `_write_cost_root` helper def, not part of the class body; `TestCostTrendConfigDir` actually ends at `:9920` (not `:10014`) — `:9921-10014` are `cost_ledger_enabled`/`_cost_ledger_args`/`_cost_ledger_row`/`_cache_rebuild_args` fixture/helper defs serving the next (cost-ledger/cache-rebuild) classes, not cost-trend's own body. Both were caught by an `awk '/^class |^def /'` boundary sweep, not by trusting either prior pass's stated range. | `[verified: this session, see command output above]` |
| 9 | Local (non-`conftest.py`) helper functions the 15 classes call, bucketed by disposition: **(a) move wholesale, cost-exclusive, zero external refs** — `_cost_trend_args:9576-9583`, `_extract_cost_trend_row:9584-9595` (both confirmed zero external call sites: `grep -c` for each returns hits only inside `TestCostTrend`/`TestCostTrendConfigDir`'s own range, `:9596-9920`). **(b) DAMP-duplicate — cost-domain-named, small (2-3) external call sites in classes staying behind** (`TestRedactionOrdinalStability:16670`, `TestRearmBacktestReport:17265`): `_cost_args:5121-5145` (120+ total call sites; a first grep pass this session found 3 external sites and missed a 4th, `TestMultiRootFormatOutliers:16460` region, call at `:16484` — re-found on a second, wider pass; external sites now: `:16484,16700,16703,17280` — the miss itself is evidence the bucket-(b) counts below are a floor, not a final tally, so a wider no-range-clipping grep is required at implementation time before any bucket-(a) helper is deleted), `_write_cost_root:6071-6086` (external at `:10264-10370` cost-ledger, `:16695-16697`, `:17723-17774`), and the report-output-parsing family `_extract_grand_total:148`, `_extract_md_grand_total:164`, `_extract_account_totals:172`, `_extract_summary_unpriced:185`, `_extract_unpriced_total:136`, `_md_table_cols:61` (external hits confirmed at `:16488,17281`; full external-reference count needs a final grep pass at implementation time — treat the 2 confirmed sites as a floor, not a ceiling). **(c) promote to `conftest.py` — each read this session; none couples to cost-specific logic, but each has outgrown DAMP-scale duplication**: `_priced:5068-5098` is documented as cost's own helper ("Build an assistant record with explicit priced usage fields for cost tests") but its own docstring already documents rearm-backtest reusing it, and it has ~150 call sites spanning cost-ledger (`:10025-11502`), rearm-backtest (`:16473-17966`), and other classes staying behind — too many to DAMP-duplicate without real drift risk, so it moves to `conftest.py` despite its cost-family origin, not because it lacks one. `_opus:5030-5049` and `_priced_opus:5051-5066` are audit-routing's own helpers by docstring ("for audit-routing tests" / "audit-routing's dollar-headline tests") that cost-family tests also call — same promotion logic, opposite origin. `_priced_sidechain_asst:195-210` is subagent-mix's helper (its own docstring calls itself "a sidechain counterpart to TestCost's own `_priced`") and is called from within the cost-family range too. All four are pure `_asst`-record builders (call `conftest._asst`, set a `usage` dict, return) with no cost-specific coupling — safe to promote as plain functions, matching `conftest.py`'s documented convention of plain functions taking explicit arguments rather than injected fixtures. | `[verified: regex scan of every class-scoped call site this session, cross-referenced against every top-level `^def` in the test file; all four bucket-(c) bodies read this session]` |
| 10 | The architecture-doc-drift test (`tests/test_transcript_analysis_architecture_doc.py`) does a pure name-set match: `### <name>.py` headings in `docs/transcript-analysis-architecture.md` vs. `transcript_analysis/*.py` on disk (excluding `__init__.py`) — no line-count or content-quality assertion. Adding `### cost.py` with prose in the doc's established per-module format satisfies it; no richer content is required by the test itself (richer content is still owed to a human reader per the doc's own existing style). | `[verified: Read of the full 57-line test file this session]` |
| 11 | Every new test file loads its own independent copy of `transcript-analysis.py` via the exact `spec_from_file_location`/`module_from_spec`/`exec_module` boilerplate at `tests/test_transcript_analysis.py:22-29` (with its own explanatory comment on why `sys.modules` is never touched) — `test_transcript_cost.py` needs the identical boilerplate, not a shared import of `test_transcript_analysis.py`'s `_mod`. Shared fixtures (`fake_projects`, `fake_config_dir_factory`, `cost_ledger_file`, `_table_cols`, `_asst`, `_user_msg`, `_write_jsonl`, `_bash_use`, `_tool_result`, `_agent_use`) already resolve via `conftest.py`'s `request.module._mod` pattern and need no change to work for a second test file defining its own `_mod`. | `[verified: Read of test_transcript_analysis.py:1-29 and conftest.py's fake_projects/cost_ledger_file docstrings this session]` |
| 12 | `_UNCONDITIONAL_HEADER_CASES` and its two parametrized test classes (currently `tests/test_transcript_analysis.py:~15875`, references `cmd_cost`/`cmd_cost_trend` among ~15 other groups) stay in the legacy file per the governing plan's own Out-of-scope section — they keep working unchanged because `cmd_cost`/`cmd_cost_trend` remain accessible as `_mod.cmd_cost`/`_mod.cmd_cost_trend` via the shim's re-export, identical to how Phase 1's leaf-module re-exports already work for every other still-referenced name. | `[verified: grep for cmd_cost/cmd_cost_trend references in that table this session; same re-export mechanism Phase 1 already established]` |
| 13 | Current baseline (this worktree, post-Phase-1, pre-Phase-2 diff): `1193 passed in 57.87s` for `pytest tests/test_transcript_analysis.py tests/test_context_composition.py -q`. This is the number Phase 2's parity check must not fall below (it should rise, since new test files add no new tests by themselves but Phase 2's move is behavior-preserving). | `[verified: ran this session in the current worktree]` |

## Critical files

### Create

- `claude/.claude/scripts/transcript_analysis/cost.py` — `cmd_cost`,
  `_accumulate_per_account_turn`, `_print_token_class_table`,
  `_print_model_id_table`, `_print_thread_table`, `_session_branch_index`,
  `_attributed_branch`, `_cost_report`, `cmd_cost_trend`,
  `compute_cost_trend_data` (renamed public, see M3), `_cost_trend_report`.
  Imports `scope`, `corpus`, `pricing`, `render`, `redaction` by module
  (attribute access, matching every existing package module's discipline —
  no `PROJECTS_DIR`-shaped global lives here, but the discipline is the
  house style regardless).
- `claude/.claude/scripts/tests/test_transcript_cost.py` — the 15 cost-family
  classes (assumption 7's corrected ranges) plus bucket-(a)/(b) local
  helpers (assumption 9), its own `_mod`-loading boilerplate mirroring
  `test_transcript_analysis.py:1-29`, and `from conftest import ...` for the
  shared fixtures already used by cost tests today. One retarget needed
  during the move, not a copy-paste: `TestCostByAccount`'s fault-injection
  test (`tests/test_transcript_analysis.py:6940-6952`) does
  `monkeypatch.setattr(_mod, "_accumulate_per_account_turn", flaky_accumulate)`
  relying on `_cost_report`'s bare-name call resolving against the shim's
  own globals — once both symbols move into `cost.py` together, that same
  bare-name call inside `_cost_report`'s body resolves against `cost.py`'s
  globals instead, so the patch target becomes `monkeypatch.setattr(_mod.cost,
  "_accumulate_per_account_turn", flaky_accumulate)` in the new file. A
  stale patch here fails loudly (`DID NOT RAISE`) under the baseline-parity
  check, not silently, but is worth fixing at move time rather than as a
  surprise CI failure.
- `claude/.claude/scripts/tests/test_transcript_cli_bootstrap.py` — add one
  subprocess-level `cost` (and `cost-trend`) invocation against a seeded
  corpus, per Verification item 2. This file has no subcommand coverage
  beyond `--help` and `buckets` today.

### Modify

- `claude/.claude/scripts/transcript-analysis.py` — remove the moved
  symbols' bodies; add `from transcript_analysis import cost` to the
  existing package-import block (`:37`) and explicit re-exports for every
  moved bare name (matching the existing pattern at `:38-105`), including
  the `compute_cost_trend_data as _compute_cost_trend_data` alias (M3).
  Add `_branch_filter`, `_parse_since_nd_arg`, `_projects_glob` to the
  existing `scope` import block; add `_fmt_date` to the existing `render`
  import block. Argparse wiring (`p_cost.set_defaults(...)`,
  `p_cost_trend.set_defaults(...)`) needs no change — it already references
  the bare names, which remain resolvable via the re-exports.
- `claude/.claude/scripts/transcript_analysis/scope.py` — add
  `_branch_filter`, `_parse_since_nd_arg`, `_projects_glob` (moved verbatim
  from `transcript-analysis.py:144-197`, unmodified bodies).
- `claude/.claude/scripts/transcript_analysis/render.py` — add `_fmt_date`
  (moved verbatim from `transcript-analysis.py:209-210`).
- `claude/.claude/scripts/tests/test_transcript_analysis.py` — remove the 15
  moved classes and their exclusive (bucket-(a)) local helpers; leave
  bucket-(b) helpers in place for their remaining external call sites.
- `claude/.claude/scripts/tests/conftest.py` — add bucket-(c) helpers
  (`_opus`, `_priced`, `_priced_opus`, `_priced_sidechain_asst`), each
  confirmed this session (assumption 9) to be a pure `_asst`-record builder
  with no cost-specific coupling, moved as plain functions matching
  `conftest.py`'s existing convention (not injected pytest fixtures).
- `docs/transcript-analysis-architecture.md` — add a `### cost.py` heading
  in the established per-module format (Reads/writes what, self-contained
  or cross-module, one-paragraph responsibility statement), and update the
  "leafward-only... a circular import this decomposition avoids" framing at
  `:9-14` to describe the new one-directional exception this phase
  introduces (monolith → `cost.py` for cost-ledger's still-unmigrated use of
  `compute_cost_trend_data`) rather than silently contradicting it.
- `claude/.claude/scripts/tests/test_transcript_analysis_architecture_doc.py`
  — no change expected (assumption 10); its set-based assertion should pass
  once `cost.py` exists and is documented.

### Reuse rather than reimplement

- `_resolve_cost_roots` (`scope.py:486`), `_scan_root_transcripts`
  (`scope.py:562`), `_price_turn`/`dedup_turns_by_request_id` (`pricing.py`),
  `_fam`/`_fmt_usd`/`_pct_of` (`render.py`), `_redact_proj_label`/
  `_redaction_ordinals`/`_corpus_fingerprint` (`redaction.py`/`scope.py`) —
  already Phase-1 package code; `cost.py` calls these via module attribute
  access exactly as `transcript-analysis.py` does today, no new wrapper.
- `conftest.py`'s existing `fake_projects`, `fake_config_dir_factory`,
  `cost_ledger_file`, `_table_cols` fixtures — `test_transcript_cost.py`
  uses them unchanged via the `request.module._mod` mechanism already
  proven for two independently-loaded `_mod` copies.

## Verification

1. **Baseline parity.** `1193 passed` (assumption 13) must hold or rise —
   never fall — after the split, running
   `../../../.venv/bin/pytest claude/.claude/scripts/tests/test_transcript_analysis.py claude/.claude/scripts/tests/test_transcript_cost.py claude/.claude/scripts/tests/test_context_composition.py -q`
   from this worktree, plus the full suite
   `../../../.venv/bin/pytest claude/.claude/` for the repo-wide check the
   governing plan's own Verification item 1 requires.
2. **CLI subprocess coverage for `cost`/`cost-trend` — new, not a re-run.**
   `tests/test_transcript_cli_bootstrap.py` today (72 lines, 4 tests) has no
   multi-root or zero-match scenario for *any* subcommand, and exercises
   only `--help` plus one representative `buckets` invocation — it does not
   touch `cost` or `cost-trend` at all (re-verified by reading the file this
   session; an earlier draft of this plan asserted broader existing
   coverage that does not exist). Since this phase is the first to move a
   `cmd_*` body's import wiring, add one subprocess-level test to that file
   — `python3 transcript-analysis.py --config-dir <tmp> cost` (and
   `cost-trend`) against a seeded corpus, asserting on stdout, matching the
   file's existing `test_transcript_analysis_buckets_subprocess_finds_seeded_session`
   shape — as the one check that would catch a broken
   `from transcript_analysis import cost` re-export in the real shim
   entrypoint, which every in-process `_mod.cmd_cost(...)` test cannot see.
   This is new work, added to Critical files below.
3. **Assumption-20 guard coverage.** Confirm
   `TestCostMultiRootRedaction`'s direct-call `_cost_report(no_redact=True,
   roots=[...])` test (assumption 2) still exists and still passes after
   the move — this is the phase's entire obligation under the governing
   plan's assumption 20, already satisfied, not something to add.
4. **Sibling-script and hook-sandbox coverage.** `test_token_analyzer.py`,
   `test_analyze_context.py`, and `test_nudge_error_mode_analysis.py` must
   pass unchanged — none of them touch cost-family code today, so this is a
   pure regression check that the `scope.py`/`render.py` additions
   (`_branch_filter`, `_parse_since_nd_arg`, `_projects_glob`, `_fmt_date`)
   don't collide with anything the siblings already import.
5. **Revert rehearsal.** Per the governing plan's Verification item 7:
   `git revert` this phase's merge commit on a scratch branch and run the
   full suite, proving this PR is independently revertible without
   depending on a later phase's fixture promotion.
6. **Lint.** `../../../.venv/bin/ruff check claude/.claude/`.
7. **Doc-drift test.** `tests/test_transcript_analysis_architecture_doc.py`
   passes once `cost.py` is documented (assumption 10).
8. **Architecture-doc accuracy.** The updated `:9-14` framing in
   `docs/transcript-analysis-architecture.md` is prose, not test-enforced —
   review it by eye against the actual import direction this phase adds.

## Out of scope

- Moving any command group other than cost (reviewer-yield, review-trace,
  audit-routing, cost-ledger, or the remainder) — each is its own later
  phase per the governing plan.
- Creating `cli.py` / moving `build_parser()` or `main()` — reserved for the
  final phase.
- Removing the `PROJECTS_DIR` global — excluded by the governing plan for
  the whole decomposition, not just this phase.
- Relocating `_UNCONDITIONAL_HEADER_CASES` or its two test classes.
- Landing the parametrized multi-root `--no-redact` refusal test across the
  other 7 guarded subcommands — only the cost site's obligation falls due
  this phase (assumption 2), and it's already met.
- Any CLI surface change.
- Running a final exhaustive external-reference grep for each bucket-(b)
  helper in assumption 9 (the confirmed counts there are a floor, not a
  ceiling) — bucket-(c) bodies are already read and confirmed this session;
  bucket-(b)'s exact total external-reference count is implementation-time
  verification, since it changes no design decision, only which exact lines
  move.
