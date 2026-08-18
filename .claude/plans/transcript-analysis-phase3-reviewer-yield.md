# Transcript-analysis decomposition — Phase 3 (reviewer-yield command group)

## Context

Move the `reviewer-yield` command group — `cmd_reviewer_yield` and every
helper used only by it — out of `claude/.claude/scripts/transcript-analysis.py`
into a new `transcript_analysis/reviewer_yield.py` module, continuing the
package decomposition begun in Phase 1 (`.claude/plans/transcript-analysis-decomposition.md`)
and Phase 2 (`.claude/plans/transcript-analysis-phase2-cost-family.md`,
merged as PR #692). This is the governing plan's own next phase in its
largest-first ordering (reviewer-yield: 1,413 test lines, second only to the
already-moved cost family's 2,362). Outcome: `cmd_reviewer_yield` and its
exclusive helpers live in a dedicated leaf-consuming module; the shim
re-exports everything unchanged for external callers and for the four
still-monolithic command groups that reach into this group's symbols.

All line numbers below were read fresh this session from
`claude/.claude/scripts/transcript-analysis.py` and
`claude/.claude/scripts/tests/test_transcript_analysis.py` at their current
(post-Phase-2-merge) state — none are carried over from either prior plan
file, both of which predate this move and are stale for it.

## Approach

Mirror Phase 2's established shape exactly: a new `transcript_analysis/`
module importing sibling leaf modules by attribute access, a new
`test_transcript_reviewer_yield.py` test file with its own `_mod` loader
boilerplate, and a shim re-export block. The one structural difference from
Phase 2 is that reviewer-yield has **four** distinct cross-group couplings
(Phase 2's cost family had two, both one-directional in the same direction);
three follow Phase 2's already-proven M3 pattern (still-monolithic caller
reaches back into the moved module via a shim alias) but the fourth runs the
other way — reviewer-yield needs a symbol currently owned by a group that
hasn't moved yet, which the M3 pattern cannot serve, so that one symbol is
promoted into an existing leaf module instead.

**Root problem:** `cmd_reviewer_yield` and its exclusive helpers are
entangled in an 11,211→10,299-line monolith the governing plan is
decomposing group by group; this phase is next by the plan's own size
ordering. **Givens accepted from the governing plan:** the package's
leafward-import discipline (monolith imports the package, never the
reverse) — `[verified: claude/.claude/scripts/transcript-analysis.py:37,
`from transcript_analysis import corpus, cost, pricing, redaction, render,
scope` and no import in the other direction anywhere in
transcript_analysis/*.py]`; the `test_transcript_*` new-test-file naming
convention — `[verified: transcript-analysis-decomposition.md:233-236]`; and
the sibling-module-by-attribute-access import style —
`[verified: claude/.claude/scripts/transcript_analysis/cost.py:4-6 docstring,
"Imports scope, corpus, pricing, render, and redaction by module (attribute
access, not by name)"]`.

### Assumption ledger

1. **The CLI subcommand is literally `"reviewer-yield"`**, backing function
   `cmd_reviewer_yield`, lines 3153-3269.
   `[verified: transcript-analysis.py:9554 sub.add_parser("reviewer-yield", ...); :9574 p_reviewer_yield.set_defaults(func=cmd_reviewer_yield); :3153 def cmd_reviewer_yield(...); :3271 def cmd_skill_pair(...) — confirms cmd_reviewer_yield ends at 3269, blank line 3270]` anchors: root

2. **Production symbols exclusive to this group** (move wholesale into
   `reviewer_yield.py`), with fresh line ranges:
   - Constants block: `_REVIEWER_NO_CONCERNS_GAP_MAX_CHARS`/`_RE`,
     `_REVIEWER_FOUND_ISSUES_RE`, `_REVIEWER_APPROVE_WITH_CONCERNS_RE`,
     `_REVIEWER_REQUEST_CHANGES_RE`, `_REVIEWER_VERDICT_FINDINGS_FOUND`/
     `_ZERO_FINDING`/`_UNCLASSIFIED`, `_REVIEWER_YIELD_ACTIVE_FLOOR`:
     2469-2497
   - `_scan_reviewer_transcript`: 2723-2789
   - `_classify_reviewer_verdict`: 2792-2812
   - `_CITED_PATH_CANDIDATE_MAX_CHARS`/`_CITED_PATH_CANDIDATE_RE`: 2818-2825
   - `_extract_cited_paths`: 2828-2836
   - `_CITED_PATH_LINE_SUFFIX_RE`/`_CITED_PATH_WORKTREE_PREFIX_RE`: 2841-2847
   - `_normalize_cited_path`: 2850-2909
   - `_is_reviewer_subagent_type`: 2912-2917 (see row 4 — shared, not exclusive)
   - `_code_write_target_path`: 2920-2924
   - `_build_tool_result_ts_map`: 2927-2954
   - `_index_parent_edits`: 2957-2990
   - `_CITED_PATH_PLAN_FILE_MARKER`: 2995
   - `_is_plan_file_candidate`: 2998-3003
   - `_dispatch_self_reference_keys`: 3006-3021
   - `_reviewer_yield_cited_keys`: 3024-3043
   - `_compute_reviewer_yield_data`: 3046-3150 (see row 4 — shared, promote to public name)
   - `cmd_reviewer_yield`: 3153-3269
   - `_REVIEWER_PREFIX`/`_REVIEWER_EXACT_NAMES`: 959-961 (see row 4 — shared)
   `[verified: fresh Read/grep this session against transcript-analysis.py at its current tip]` anchors: root

3. **`_agent_frontmatter_model` (2551-2565), `_declared_pin` (2578-2609), and
   `_dispatch_usage_summary` (2612-2720) are subagent-mix-exclusive**, not
   reviewer-yield's, despite sitting physically between `_index_subagent_dispatches`
   (2500) and `_scan_reviewer_transcript` (2723) — no reviewer-yield call
   site references any of the three. They stay in the monolith.
   `[verified: grep for each name across transcript-analysis.py this session — call sites confined to cmd_subagent_mix's own body, none in cmd_reviewer_yield/_compute_reviewer_yield_data]` anchors: row2

4. **Four cross-group couplings exist; three are M3-shaped (Phase 2's proven
   pattern), one is not:**

   a. **`_compute_reviewer_yield_data`** is also called by `cmd_cost_ledger`
      (`:7075`, cost-ledger group, not yet moved) and its `agg2` return value
      feeds `_reviewer_gap_pp` (`:6796-6817`, cost-ledger's own helper,
      staying in the monolith). Structurally identical to Phase 2's
      `compute_cost_trend_data` (also called forward by `cmd_cost_ledger` at
      `:6859` and `:7042` via a shim alias). **Resolution:** promote to
      public `compute_reviewer_yield_data` in `reviewer_yield.py`; shim
      re-exports `compute_reviewer_yield_data as _compute_reviewer_yield_data`
      for `cmd_cost_ledger`'s unchanged bare call.
      `[verified: transcript-analysis.py:7075 reviewer_data = _compute_reviewer_yield_data(...); :7076 _reviewer_gap_pp(reviewer_data["agg2"]); cost.py:627 def compute_cost_trend_data(...) / transcript-analysis.py:54 from transcript_analysis.cost import compute_cost_trend_data as _compute_cost_trend_data — the precedent this mirrors]` anchors: row2

   b. **`_reviewer_gap_pp`** (6796-6817) itself stays in the monolith — its
      only caller is `cmd_cost_ledger`, and it is physically positioned in
      cost-ledger's section, not reviewer-yield's. It reads
      `_REVIEWER_VERDICT_FINDINGS_FOUND`/`_ZERO_FINDING`/`_UNCLASSIFIED`
      bare, so the shim must re-export those three names alongside the
      `compute_reviewer_yield_data` alias from row (a).
      `[verified: transcript-analysis.py:6796-6817 read this session — no reviewer-yield-exclusive call site]` anchors: row4a

   c. **`_is_reviewer_subagent_type`** (2912-2917) plus `_REVIEWER_PREFIX`/
      `_REVIEWER_EXACT_NAMES` (959-961) are also read by
      `_review_trace_session_events` (`:1484`, review-trace group, not yet
      moved). Same M3 shape as (a): review-trace hasn't moved, reviewer-yield
      is moving now, so reviewer-yield becomes the owner. **Resolution:**
      move all three into `reviewer_yield.py`; shim re-exports
      `_is_reviewer_subagent_type` bare for `_review_trace_session_events`'s
      unchanged call. When review-trace's own phase runs, that plan decides
      whether to keep importing from `reviewer_yield.py` or relocate
      ownership — not this plan's call.
      `[verified: transcript-analysis.py:1484 if not _is_reviewer_subagent_type(stype): inside def _review_trace_session_events(:1381)]` anchors: row2

   d. **`_index_subagent_dispatches`** (2500-2545) is called by both
      `cmd_reviewer_yield`'s own `_compute_reviewer_yield_data` (`:3073`) and
      `cmd_subagent_mix` (`:2330`, subagent-mix group, not yet moved).
      Neither group has moved before the other, so this is the same M3 shape
      as (a) and (c): reviewer-yield moves first, becomes the owner, shim
      re-exports `_index_subagent_dispatches` bare for `cmd_subagent_mix`'s
      unchanged call.
      `[verified: transcript-analysis.py:2330 call site inside cmd_subagent_mix (:2203); :3073 call site inside _compute_reviewer_yield_data]` anchors: row2

   e. **`_CODE_WRITE_TOOLS`** (defined once, `:3609`) is read by
      reviewer-yield's `_index_parent_edits` (`:2982`) **and** by
      `_classify_opus_turn` (`:3635`, audit-routing group, not yet moved) —
      but unlike (a)/(c)/(d), `_CODE_WRITE_TOOLS` is physically owned by the
      *not-yet-moved* audit-routing section (defined at `:3609`, well past
      reviewer-yield's own span), and reviewer-yield's `_index_parent_edits`
      reaches forward into it today only because both are still in the same
      module. Once `_index_parent_edits` moves into `reviewer_yield.py`, it
      cannot reach back into the monolith — the package's leafward-import
      discipline runs one way only (row root's given). The M3 shim-alias
      pattern doesn't apply here because the direction is reversed: this is
      a forced promotion, not a re-export. **Resolution:** promote
      `_CODE_WRITE_TOOLS` (a zero-dependency `frozenset[str]` constant, no
      logic) into `corpus.py`, imported by attribute access
      (`corpus._CODE_WRITE_TOOLS`) from both `reviewer_yield.py` and the
      still-monolithic `_classify_opus_turn`.
      **Alternative considered:** `pricing.py`, which already holds a
      similarly-shaped tool-name-set constant (`_SPAWN_TOOL_NAMES`,
      `pricing.py:383`). Rejected — `pricing.py`'s docstring scopes it to
      "rate tables, per-turn pricing, token counts, context windows,
      dedup"; `_CODE_WRITE_TOOLS` classifies tool_use blocks for
      edit-attribution routing, not pricing, and `_SPAWN_TOOL_NAMES`'s
      presence there is itself Phase 2 reconciliation debt (added to serve
      `_count_subagent_spawns`, a pricing-adjacent drift check), not a
      clean precedent to extend. `corpus.py`'s docstring ("JSONL transcript
      read/parse and session iteration") is the closer fit: classifying a
      tool_use block's name is a record-shape concern, matching corpus.py's
      existing role, and adds no new module for one four-item frozenset.
      `[verified: transcript-analysis.py:3609 _CODE_WRITE_TOOLS definition; :2982 and :3635 both call sites, read fresh this session; pricing.py:1-3 and corpus.py:1-2 docstrings compared this session]` anchors: root

5. **Test symbols exclusive to this group** (move into
   `test_transcript_reviewer_yield.py`), fresh line ranges from
   `test_transcript_analysis.py`:
   - Section comment + preamble: `_reviewer_yield_args` (1553-1567),
     `_n_cited_reviewer_dispatches` (1568-1599)
   - `TestIsReviewerSubagentType`: 1600-1610
   - `TestReviewerYield`: 1611-2651
   - `TestExtractCitedPaths`: 2652-2723
   - `TestNormalizeCitedPath`: 2724-2849
   - `TestBuildToolResultTsMap`: 2850-2876
   - `TestIndexParentEdits`: 2877-2933
   - `TestReviewerYieldCitedKeys`: 2934-2972
   - `TestDispatchSelfReferenceKeys`: 2973-2987
   Full span 1548-2987 = 1,440 lines, within 2% of the governing plan's
   1,413-line bucket figure (comparable to Phase 2's own ~4.6%
   estimate-vs-actual drift) — confidently the right group, not
   over/under-inclusive.
   `[verified: fresh Read of test_transcript_analysis.py:1545-1612 and :2980-3015 this session — section boundaries confirmed by the file's own "# reviewer-yield" / "# skill-pair" comment markers at :1548 and :2989]` anchors: root

6. **`_reviewer_yield_args` has an external reference that must stay behind:**
   `_UNCONDITIONAL_HEADER_CASES` (a table used by tests that themselves stay
   in the legacy file until the final phase, per the governing plan's
   Out-of-scope section) references `_reviewer_yield_args` at
   `test_transcript_analysis.py:14565`. **Resolution:** DAMP-duplicate a
   small copy of `_reviewer_yield_args` in the legacy test file — the same
   disposition Phase 2 gave `_cost_args` for the identical shape.
   `[verified: staff-sdet plan-review pass this session confirmed line 14565's content verbatim]` anchors: row5

7. **`_n_cited_reviewer_dispatches` depends on `_write_subagent_dispatch`**
   (defined at `test_transcript_analysis.py:61`), a shared record-builder
   with **71** total call sites (72 minus its own `def` line) — **46** of
   which sit inside the reviewer-yield classes being moved (lines
   1590-2638) and travel with them; only **25** remain in the legacy file
   post-move (24 in `TestSubagentMix*`/`TestDeclaredPinPathSafety`, plus one
   at `:8630` inside `_reviewer_dispatch_records`, used by
   `TestCostLedgerRecordParity`). This is Phase 2's own bucket-(c) shape
   (`_priced`-style: fan-out across ≥2 files post-move → promote to
   `conftest.py` rather than duplicate) — the promotion call is still
   correct even though the call sites split roughly 2:1 toward the moving
   file rather than concentrating in the legacy file as first assumed.
   **Resolution:** promote `_write_subagent_dispatch` to `conftest.py`. This
   is **not** a pure move: the function's body reads `_mod.SUBAGENT_SUBDIR`
   (a reference to the calling test file's own dynamically-loaded `_mod`
   global via `spec_from_file_location`), and `conftest.py` has no `_mod` in
   scope. `conftest.py:24` already imports `SUBAGENT_SUBDIR` directly via
   `from transcript_analysis.corpus import SUBAGENT_SUBDIR` for its sibling
   `_write_subagent_jsonl` — the promoted function's body must be edited to
   read the bare `SUBAGENT_SUBDIR` name the same way, not just relocated
   verbatim. The constant is never monkeypatched anywhere, so this edit is
   safe, but it is a source change, not a mechanical import-path update.
   `[verified: staff-sdet review this session — grep-confirmed 71 call sites split 46/25, and conftest.py:24's existing SUBAGENT_SUBDIR import pattern]` anchors: row5

8. **`TestCostLedgerRecordParity`** (`test_transcript_analysis.py:8922`,
   staying in the legacy file — cost-ledger not yet moved) calls
   `_mod._compute_reviewer_yield_data(...)` directly, which the shim alias
   from row 4a keeps working unchanged.
   `[verified: staff-sdet plan-review pass this session confirmed line 8922's content verbatim]` anchors: row4a

9. **`_edit_use` (`test_transcript_analysis.py:128`) and `_write_use`
   (`:132`)** — two more test-preamble helpers, structurally identical in
   shape to row 7's `_write_subagent_dispatch` but missed by this session's
   original discovery pass. Both are called inside the moving reviewer-yield
   range (`_edit_use` 16 times, `_write_use` 8 times, e.g. lines 2064, 2246,
   2373, 2454, 2531, 2609, 2884, 2926-2927) **and** by test classes staying
   in the legacy file: `_edit_use` at `:8878`/`:8884`
   (`TestCostLedgerRecordParity`) and `:10407-10575`
   (`TestAuditRoutingSamples`); `_write_use` at `:7500-7554`
   (`TestInstrumentAuthoring`) and `:8884`. Neither is a `conftest.py`
   member today. Left undecided, `test_transcript_reviewer_yield.py` (which
   per this plan imports only from `conftest`) fails at class-collection
   time with `NameError`. **Resolution:** apply row 7's identical
   promote-to-`conftest.py` treatment — same shared-fan-out shape, same
   fix. Confirm at implementation time whether either helper shares row 7's
   `_mod.SUBAGENT_SUBDIR`-shaped body dependency before promoting.
   `[verified: staff-sdet review this session — grep-confirmed all cited line numbers]` anchors: row5

### Reuse rather than reimplement

- `scope.py`'s already-public `resolve_scan_roots`/`print_resolved_scope`,
  and its still-private-but-attribute-reachable `_resolve_project_scope`/
  `_parse_since_nd_arg` — `cmd_reviewer_yield` calls all four today via the
  shim's underscore-prefixed aliases; `reviewer_yield.py` calls them as
  `scope.resolve_scan_roots(...)` etc., matching `cost.py`'s own
  attribute-access convention.
  `[verified: scope.py def list read this session — resolve_scan_roots:295 and print_resolved_scope:461 are public (no leading underscore); _resolve_project_scope:332 and _parse_since_nd_arg:661 remain private]`
- `corpus.py`'s `_parse_ts` — already used by `_build_tool_result_ts_map`
  and `_index_parent_edits`; no new promotion needed, already package-internal.
- `render.py`'s `_content_text` — already used by `_scan_reviewer_transcript`.
- `pricing.py`'s `_SPAWN_TOOL_NAMES` — already used by
  `_compute_reviewer_yield_data`.
- `conftest.py`'s existing record builders (`_asst`, `_user_msg`,
  `_tool_result`, `_agent_use`, `_write_jsonl`) — split test file imports
  them unchanged, same as Phase 2.

### Naming

New test file: `test_transcript_reviewer_yield.py`, matching the
`test_transcript_*` prefix Phase 2 established. `_compute_reviewer_yield_data`
is promoted to public `compute_reviewer_yield_data` per row 4a, mirroring
`_compute_cost_trend_data` → `compute_cost_trend_data`'s already-precedented
treatment.

## Critical files

### Create

- `claude/.claude/scripts/transcript_analysis/reviewer_yield.py` — module
  docstring scoped to "the reviewer-yield command family: cmd_reviewer_yield
  and every helper used only by it, plus the two symbols review-trace and
  subagent-mix still reach back into." Imports `corpus`, `pricing`, `render`,
  `scope` by module (attribute access), per `cost.py`'s documented
  convention. Contains every symbol in assumption-ledger rows 2 and 4a/4c/4d.
- `claude/.claude/scripts/tests/test_transcript_reviewer_yield.py` — own
  `spec_from_file_location`/`module_from_spec`/`exec_module` `_mod`
  boilerplate (not shared with `test_transcript_analysis.py`, per Phase 2's
  precedent); `from conftest import (...)` for shared fixtures/builders.
  Contains every class/helper in assumption-ledger row 5.

### Modify

- `claude/.claude/scripts/transcript-analysis.py` — remove the moved
  production symbols; add shim re-exports:
  - `from transcript_analysis import reviewer_yield  # noqa: F401` alongside
    the existing `corpus, cost, pricing, redaction, render, scope` import
  - `from transcript_analysis.reviewer_yield import (cmd_reviewer_yield,
    _is_reviewer_subagent_type, _index_subagent_dispatches,
    _REVIEWER_VERDICT_FINDINGS_FOUND, _REVIEWER_VERDICT_ZERO_FINDING,
    _REVIEWER_VERDICT_UNCLASSIFIED, _extract_cited_paths,
    _normalize_cited_path, _build_tool_result_ts_map, _index_parent_edits,
    _reviewer_yield_cited_keys, _dispatch_self_reference_keys,
    _CITED_PATH_CANDIDATE_MAX_CHARS, ...)` — bare names for the ones read
    bare by still-monolithic code (`p_reviewer_yield.set_defaults`,
    `_review_trace_session_events`, `cmd_subagent_mix`, `_reviewer_gap_pp`)
    or by the new split test file reaching `_mod.<name>` directly. This
    list must be exhaustive over every exclusive symbol row 2 names, not
    only the four cross-group-coupled ones — `staff-backend-engineer`'s
    plan-review pass this session found the seven names above missing from
    an earlier draft's list by diffing against `cost.py`'s actual shim
    block (`transcript-analysis.py:39-53`), which re-exports every
    `_mod`-touched symbol, not only cross-group ones; confirm the full set
    against every moved symbol's test-file usage before finalizing, the
    same way.
  - `from transcript_analysis.reviewer_yield import compute_reviewer_yield_data as _compute_reviewer_yield_data`
  - remove `_CODE_WRITE_TOOLS`'s local definition at `:3609`; read it as
    `corpus._CODE_WRITE_TOOLS` from `_classify_opus_turn`
- `claude/.claude/scripts/transcript_analysis/corpus.py` — add
  `_CODE_WRITE_TOOLS: frozenset[str] = frozenset({"Edit", "Write",
  "MultiEdit", "NotebookEdit"})`, verbatim value from its current home.
- `claude/.claude/scripts/tests/test_transcript_analysis.py` — remove the
  moved test classes/helpers (row 5); add a small DAMP-duplicated copy of
  `_reviewer_yield_args` for the `_UNCONDITIONAL_HEADER_CASES` table
  reference (row 6); no change needed for `TestCostLedgerRecordParity`
  (row 8) since the shim alias keeps `_mod._compute_reviewer_yield_data`
  working.
- `claude/.claude/scripts/tests/conftest.py` — promote `_write_subagent_dispatch`
  (row 7) and `_edit_use`/`_write_use` (row 9) here. `_write_subagent_dispatch`'s
  body needs editing (`_mod.SUBAGENT_SUBDIR` → the already-imported bare
  `SUBAGENT_SUBDIR`), not a pure move — see row 7. Update the 25 call sites
  remaining in the legacy `test_transcript_analysis.py` (not "~70" — see
  row 7's corrected count) plus `_edit_use`/`_write_use`'s legacy-file call
  sites (row 9) to the promoted import.
- `docs/transcript-analysis-architecture.md:16` — update the "`cost.py` is
  the one deliberate, temporary exception" prose: Phase 3 introduces
  `reviewer_yield.py` as a second monolith→package back-read exception (row
  4a/4c/4d), so the sentence is no longer accurate once this phase merges.
  The governing plan's M4 doc-drift test checks the module inventory
  structurally, not this sentence's content, so this edit needs its own
  line item rather than relying on that test to catch it — mirroring how
  Phase 2's own plan named this exact edit explicitly rather than leaving
  it to the M4 test.
  `[verified: docs/transcript-analysis-architecture.md:16 read fresh this session, staff-backend-engineer's plan-review pass this session]`
- `claude/.claude/scripts/tests/test_transcript_cli_bootstrap.py` — add a
  subprocess-based CLI-bootstrap test for `reviewer-yield`, mirroring the
  `cost`/`cost-trend` pair Phase 2 added to this same file for the identical
  reason. This file currently covers exactly 6 subcommands (global `--help`,
  `turn-shape`, `turn-shape-samples`, `buckets`, `cost`, `cost-trend`) and
  has zero `reviewer-yield` coverage — the governing plan's claim that all
  26 subparsers already have subprocess golden-output coverage (Verification
  item 2) does not hold today; each phase adds its own subcommand's coverage,
  the way Phase 2 did. Without this, a broken shim re-export (e.g. a missing
  name from the list above) would pass every in-process `_mod.cmd_reviewer_yield(...)`
  test while failing for real CLI invocations — the exact gap subprocess
  coverage exists to catch.
  `[verified: staff-sdet plan-review pass this session — read test_transcript_cli_bootstrap.py in full, confirmed 6 subcommands covered, zero reviewer-yield references]`

## Verification

Follow the governing plan's own Verification section
(`transcript-analysis-decomposition.md:238-283`), applied to this phase:

1. Baseline parity — full suite pass count must not fall:
   `../../../.venv/bin/pytest claude/.claude/`
2. **CLI golden-output diff via real subprocess for `reviewer-yield`
   specifically** — the governing plan's item 2 claims all 26 subparsers
   already have subprocess coverage; this does not hold today (confirmed
   this session: `test_transcript_cli_bootstrap.py` covers 6 subcommands,
   none of them `reviewer-yield`). This phase adds `reviewer-yield`'s own
   subprocess test to `test_transcript_cli_bootstrap.py` (see Critical
   Files > Modify) — `reviewer-yield --help` plus a synthetic-corpus run
   asserting real output, the same shape as Phase 2's `cost`/`cost-trend`
   pair in that file.
3. Late-binding regression test — call `cmd_reviewer_yield` from
   `reviewer_yield.py` after reassigning `scope.PROJECTS_DIR` in a different
   module, assert the reassigned root's marker appears in output.
4. Revert rehearsal — `git revert` this phase's merge commit on a scratch
   branch, run the full suite.
5. Lint: `../../../.venv/bin/ruff check claude/.claude/`.
6. Doc-drift test (M4) — must fail before the architecture-doc update and
   pass after. Separately, manually confirm the `docs/transcript-analysis-architecture.md:16`
   prose edit (Critical Files > Modify) landed — M4 checks the module
   inventory structurally and does not inspect this sentence's content.
7. **Phase-3-specific — the four cross-group call sites, by name:**
   - Row 4a: `pytest claude/.claude/scripts/tests/test_transcript_analysis.py -k TestCostLedgerRecordParity`
     still passes, proving `_mod._compute_reviewer_yield_data`'s shim alias
     (`compute_reviewer_yield_data as _compute_reviewer_yield_data`) works.
   - Row 4b: the same `TestCostLedgerRecordParity` run also exercises
     `_reviewer_gap_pp`, proving the `_REVIEWER_VERDICT_*` bare re-exports
     work.
   - Row 4c: `pytest claude/.claude/scripts/tests/test_transcript_analysis.py -k TestReviewTrace`
     (or whatever class covers `_review_trace_session_events`) still passes,
     proving `_is_reviewer_subagent_type`'s bare re-export works.
   - Row 4d: `pytest claude/.claude/scripts/tests/test_transcript_analysis.py -k TestSubagentMix`
     still passes, proving `_index_subagent_dispatches`'s bare re-export
     works.
   Confirm each class name against the current file at implementation time —
   not independently re-verified this session beyond the two already cited
   in rows 6/8.

## Out of scope

- Moving review-trace, audit-routing, cost-ledger, or subagent-mix
  themselves — each is a later phase per the governing plan's ordering; this
  plan only resolves the coupling *this* phase's move creates for them.
- Deciding whether `_is_reviewer_subagent_type`/`_REVIEWER_PREFIX`/
  `_REVIEWER_EXACT_NAMES` should eventually live somewhere other than
  `reviewer_yield.py` once review-trace moves — that phase's own plan makes
  the call (row 4c).
- Relocating `_reviewer_gap_pp` out of the monolith — it stays with
  cost-ledger's still-unmoved code (row 4b).
