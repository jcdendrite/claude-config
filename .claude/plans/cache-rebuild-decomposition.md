# Extract cache-rebuild into transcript_analysis/

## Context

Continue epic #1113's decomposition of
`claude/.claude/scripts/transcript-analysis.py` (tracking issue #1116) with
its next command-group phase: extract `cmd_cache_rebuild`. This is the
largest remaining command group measured this session (1,302 exclusive
production lines, 4,138 test lines), following the same pattern as the two
already-merged phases: read-scope (PR #1128) and the pr-cost family (PR
#1136). The intended outcome matches every prior phase: `cmd_cache_rebuild`
and `_cache_rebuild_report` move out of the shim into new modules under
`transcript_analysis/`, the group's own exclusive test coverage moves with
them into dedicated test files, and the shim keeps `build_parser()` plus the
handful of by-name imports its remaining code still reads.

Research this session found that `cmd_cache_efficiency` — a separate,
independent subcommand adjacent to `cmd_cache_rebuild` in both the shim and
the test file, with zero production-code cross-references — is small
(~246 production lines, ~322 test lines, 5 test classes). Whether to bundle
it into this phase or scope strictly to cache-rebuild was an open question.
The engineer leaned toward cache-rebuild only but asked for `plan-architect`'s
independent judgment before settling it:
`[engineer-verified: "I think cache-rebuild only, but what does plan-architect
think?"]`. `plan-architect` agreed on its own analysis (documented in the
Approach section's M1 below) — the two commands share no production seam,
bundling them moves rather than removes the one test coupling, and it adds a
third production module, a seventh test file, and a third architecture-doc
section to what is already the largest phase in the series. **Confirmed:
scope is cache-rebuild only; cache-efficiency is deferred to its own later,
small phase.**

## Approach

cache-rebuild moves out of the shim into two package modules:
- `cache_rebuild_rules.py` is a leaf. It holds the family's constants and its 16 pure helpers.
- `cache_rebuild.py` holds `cmd_cache_rebuild`, `_cache_rebuild_report`, and the two CLI defaults.

Its tests move into six test files plus one family helper module. Both functions move whole and verbatim. The shim keeps `build_parser()` and imports by name only the three names it still reads. cache-efficiency stays in the shim for a later phase.

**Scope: cache-rebuild only.** I agree with the engineer's lean (row 1). This is my own reading of the evidence, not a relay of the lean:
- **No production seam.** Nothing in production couples the two commands (row 6). Moving them together would be two independent extractions in one diff.
- **Bundling moves the test coupling instead of removing it.** The one test coupling is `TestCacheRebuildCrossInstrumentReconciliation`, which the Step 3 research missed (row 7). With bundling, the coupling moves to `TestFormatDriftCanary`'s four cache-efficiency methods, which stay in the legacy file and would then need `_cache_efficiency_args` from outside it. Either way there is exactly one cross point.
- **Bundling adds review surface for no gain.** It adds:
  - a third production module (the two commands share no code, so neither cache-rebuild module could hold cache-efficiency without mixing concerns);
  - a seventh test file;
  - a third architecture-doc section;
  - a second CLI-parity surface.

  All of that lands on what is already the largest phase: 1,302 production lines and 4,138 test lines (rows 4, 17). It also makes the revert unit coarser than the governing plan's one group per PR (row 31).
- **Adjacency is not a seam.** Extraction works by line range, and cache-efficiency's spans stay contiguous in both files after the cut.
- **cache-efficiency fits the small-group remainder better.** It is about 570 lines in total, which puts it in #1116's remainder of small groups. Batching small groups together spreads a phase's fixed cost better than attaching one to the largest phase.

Alternatives considered and set aside:
- **Bundling cache-efficiency.** Rejected above.
- **One production module.** It would hold 1,302 moved lines, over the 1,000-line limit (row 33).
- **Promoting the family's test helpers to `conftest.py`.** conftest is already at 1,010 lines (row 20).
- **Reading the leaf by module from `cache_rebuild.py`.** This follows the command-module convention, but it forces reflowing output-producing lines past ruff's 130-column limit (M3).

### Assumption ledger

**Root:** cache-rebuild is still in the monolithic shim: 1,302 exclusive production lines (:4475–5776) and a 4,138-line test block (:5959–10096). It is the largest remaining command group measured this session, and #1116 takes the remaining groups largest first. `[verified: Read of both spans; class-def grep]` The ordering against groups not measured this session comes from Step 3 research. `[unverified — not re-measured here]`

**Givens:**
- G1. pytest's `prepend` import mode imports test modules by basename, so new test files keep the `test_transcript_*` prefix. Reason: pytest owns this behavior. `[verified: transcript-analysis-decomposition.md:59-63]`
- G2. `from m import n` binds `n` at import time, so a monkeypatch reaches code only through the binding that code reads at call time. Reason: Python language semantics. `[verified: transcript-analysis-decomposition.md row 1]`
- G3. A directly invoked `python3 transcript-analysis.py` finds the package only through CPython's `sys.path[0]`. Reason: CPython owns script bootstrap. `[verified: tests/test_transcript_cli_bootstrap.py:1-11]`

Four conditions look like givens but are not. This repo owns each one, so each is a deliberate decline listed in **Out of scope**:
- the shim-plus-package shape;
- `build_parser()` staying in the shim;
- `_UNCONDITIONAL_HEADER_CASES` staying in the legacy file;
- ruff's line length.

**Mechanisms:**
- **M1 — Scope is cache-rebuild only.** cache-efficiency's code and its five test classes stay in place. `anchors: root, row1, row2, row6, row7, row31`
  - Rejected: bundling cache-efficiency. See the scope rationale above.
- **M2 — Two production modules, split at the boundary between helpers and command, each moved by line-range extraction.**
  - `cache_rebuild_rules.py` takes :4478–4960.
  - `cache_rebuild.py` takes the two CLI defaults (:4475–4476) and :4961–5776.
  - `_cache_rebuild_report`'s 805-line body is not split.
  - The CLI defaults go with the command, matching pr-cost, which put `_PR_COST_ASOF_WINDOW_DAYS_DEFAULT` in `pr_cost.py`.

  `anchors: root, row4, row5, row8, row9, row10, row33`
  - Lighter: one module. It is over the limit (row 33).
  - Lighter: split `_cache_rebuild_report` into functions to fit one module. That is a behavioral refactor, not a move.
  - Heavier, rejected: three modules (a classification leaf, a pricing/verdict leaf, and the command). No size limit forces it, and it adds a cross-leaf import.
- **M3 — Import discipline.**
  - The leaf reads `corpus` and `pricing` by module.
  - `cache_rebuild.py` reads `corpus`, `pricing`, `redaction`, `render`, and `scope` by module.
  - `cache_rebuild.py` imports the 31 leaf names its report reads by name.

  G2's hazard does not apply to those 31 names: none is reassigned at runtime and no test patches one (row 11). Reading them by name keeps the report body identical to the shim's apart from short prefixes on existing modules. `anchors: G2, row11, row12, row13, row14`
  - Rejected: by-module access to the leaf names (`cache_rebuild_rules.` is 20 characters). Row 14 estimates at least 8 report lines would pass 130 columns. Those lines include f-string literals whose reflow risks changing output. Each reflow is also an edit beyond a pure move.
  - Rejected: a per-file E501 ignore. `pyproject.toml` reserves E501 ignores for frozen data (row 13).
  - Rejected: a shorter leaf name that fits the limit. It drops the family prefix that groups `pr_cost*` and these modules.
  - **Plan-review should weigh this one.** It is the first command-group module to import a sibling by name. Three leaves already import pure names this way (row 12).
- **M4 — Shim imports.**
  - Add `cache_rebuild` and `cache_rebuild_rules` to the module-import list, which is the tests' `_mod.<module>` channel.
  - Import `cmd_cache_rebuild`, `_CACHE_REBUILD_DEFAULT_SINCE`, and `_CACHE_REBUILD_DEFAULT_THRESHOLD` by name, in read-scope's commented style.
  - Drop `bisect`.
  - Keep `_corpus_fingerprint` with `# noqa: F401`, because it is now read only by test files this phase does not edit (row 15).

  `anchors: row5, row15, row29`
- **M5 — Test layout: six test files along thematic seams, plus `tests/_cache_rebuild_helpers.py`.**
  - The helper module holds the 13 helpers used by more than one file, plus `_cache_rebuild_args`, which the legacy table also reads.
  - Helpers used in only one file stay local to that file.
  - The seams keep every test-side positional reference inside its own file (row 21), and every file lands under about 910 lines (row 33).

  `anchors: G1, row17, row18, row19, row20, row21, row32, row33`
  - Rejected: promoting the helpers to `conftest.py`. It is already over the limit, and the move would widen family helpers to suite scope.
  - Rejected: new files importing from the legacy test module. Read-scope and pr-cost both rejected this: it couples test modules and runs the legacy loader twice.
  - Rejected: a contiguous split of groups D and E into four files. It needs three positional-comment rewrites (:8217, :8246, :8373) and a `_ttl_verdict_ts` docstring rewrite. The chosen three-file grouping pairs each verdict rule's unit tests with its own full-pipeline boundary test, which is the pairing those docstrings already name.
- **M6 — Tests that span two commands stay in the legacy file.** This follows the governing plan's rule for cross-group tables: they stay until every group they reference has moved.
  - `TestCacheRebuildCrossInstrumentReconciliation` stays. It moves verbatim to sit directly after `TestCacheEfficiencyArgparseWiring`. Its one `_mod._cache_rebuild_report` read is retargeted.
  - The `_UNCONDITIONAL_HEADER_CASES` row stays byte-identical. The legacy file imports `_cache_rebuild_args` from `._cache_rebuild_helpers`.

  `anchors: row7, row16, row20`
  - Rejected: moving the reconciliation class and promoting `_cache_efficiency_args` to conftest. conftest is over the limit, and the move would pull cache-efficiency's own helper out of its family before its phase.
- **M7 — Test re-point rules.** `anchors: G2, row11, row16, row29`
  - (a) In moved test code, every `_mod.<name>` read of a name this phase moves becomes `_mod.<defining module>.<name>`. Where that pushes a line past 130 columns, wrap it at an existing bracket or argument boundary, changing no token.
  - (b) Staying test code changes in exactly one place: the reconciliation class's `_mod._cache_rebuild_report`. `_mod.cmd_cache_rebuild`, `_mod._parse_ts`, and `_mod._DO_NOT_PUBLISH_BANNER` keep their shim bindings.
  - (c) No monkeypatch retargets. The block has zero `setattr` sites.
- **M8 — Positional cross-references.** Three production lines get rewritten (row 22). No test line does (row 21). `anchors: row21, row22`
- **M9 — One `code-writer` dispatch in two internal stages.**
  - Stage 1 is the production move plus a temporary by-name re-export of every moved name from the shim. The untouched legacy tests then run against the moved code as an oracle.
  - Stage 2 moves the tests and prunes the shim's imports.
  - Both stages land in one commit. The Stage 1 checkpoint is an internal validation gate, not a commit boundary.

  `anchors: row11, row16`
  - Rejected: separate sequenced dispatches. Both stages edit the shim and the legacy test file.
  - If the dispatch is interrupted between Stage 1 and Stage 2, see the Dispatch section's abort procedure below.
- **M10 — No `select-tests.py` change.** `anchors: row24`
- **M11 — Two bootstrap tests:** `cache-rebuild --help` and a seeded subprocess run, following read-scope's pair. `anchors: G3, row26`

**Assumptions:**

1. `[engineer-verified: "I think cache-rebuild only, but what does plan-architect think?"]` This covers the engineer's lean toward cache-rebuild-only and their request for independent judgment. It does not cover a final decision.
2. Scope is cache-rebuild only. The engineer's lean (row 1) and this plan's own analysis (M1) agree, so there is no conflict to escalate. Confirmation costs little but blocks nothing. `[unverified — the engineer's quote is a lean, not yet a confirmed decision]`
3. Line numbers are at `0d155589` (clean, includes #1136). If `origin/main` moves, re-locate spans by their first and last symbol. `[verified: gitStatus; Read]`
4. Production layout: `[verified: Read :4469-5779]`
   - :4469–4473: the section-divider comment, citing `.claude/plans/context-cost-root-cause.md`;
   - :4475–4476: the CLI defaults;
   - :4478–4960: the constants plus 16 helpers;
   - :4961–5776: `cmd_cache_rebuild` and `_cache_rebuild_report`;
   - :5779: the cost-ledger divider.
5. Outside :4475–5776, only `build_parser()` reads moved names: `_CACHE_REBUILD_DEFAULT_SINCE` at :10380–10381, `_CACHE_REBUILD_DEFAULT_THRESHOLD` at :10384–10387, and `cmd_cache_rebuild` at :10409. `[verified: grep of every moved name over claude/.claude/scripts/ excluding tests]`
6. Production code has no cache-efficiency coupling. The only mentions are prose: a comment naming cache-efficiency's sidechain row (:4557–4558), and a comment naming `_scan_cache_efficiency_group` (:5184). `[verified: grep "cache_efficiency" over scripts/]`
7. Test coupling: `[verified: grep; Read :7748-7783, :15280-15339]`
   - `TestCacheRebuildCrossInstrumentReconciliation` (:7748–7783) calls both `_mod._cache_rebuild_report` and `_mod._cache_efficiency_report(_cache_efficiency_args(), ...)` (:7778). The research's "zero cross-references" claim holds for production code only.
   - `TestFormatDriftCanary`'s cache-efficiency methods (:15280–15339) read `_mod.cmd_cache_efficiency` and `_cache_efficiency_args`.
8. The leaf's package dependencies: `[verified: Read :4475-4960]`
   - `corpus._parse_ts`;
   - `pricing._price_turn`, `_cache_write_split`, `_model_rates`, `_FAST_MODE_RATE_MULTIPLIER`, and `_INFERENCE_GEO_US_RATE_MULTIPLIER`;
   - stdlib `re` and `Sequence`.

   Two corrections to the research list:
   - `_context_at_turn` appears in the spans only as a tuple-unpacking local (:4749, :4779, :4818, :5285). It is never a read of pricing's function, so it must not be prefixed.
   - `_MODEL_BASE_INPUT_RATES` appears only in docstrings.
9. The command's package dependencies: `[verified: Read :4961-5776; shim :158, :248]`
   - `scope`: `_resolve_cost_roots`, `_DO_NOT_PUBLISH_BANNER`, `_parse_since_nd_arg`, `_resolve_project_scope`, `print_resolved_scope`, `_redaction_ordinals`, `_root_index_for_path`, and `PROJECTS_DIR`, which is already read by attribute;
   - `redaction`: `_RedactMapKey`, `_build_redact_map`, `_corpus_fingerprint`;
   - `corpus`: `_read_session_file_partitioned`, `_parse_ts`;
   - `pricing`: `dedup_turns_by_request_id`, `_cache_write_split`, `_price_turn`, `_cache_miss_reason`;
   - `render`: `_pct_of`, `_fmt_usd`;
   - stdlib: `argparse`, `bisect`, `statistics`, `sys`, `defaultdict`, `Sequence`, `Path`.

   `_dedup_turns_by_request_id` (:158) and `_print_resolved_scope` (:248) are shim aliases.
10. Package modules may not import from the shim (`docs/transcript-analysis-architecture.md:14-16`). Neither new module needs to, per rows 8–9. `[verified]`
11. No `global` statement appears in the spans. No test patches any moved name: there are zero `setattr` calls in :5959–10096, and no `_mod.<moved name>` patch anywhere else. `[verified: Read of spans; grep of setattr over the test file]`
12. Import convention: `[verified: grep "^from transcript_analysis" across the package]`
    - 12 package modules import at least one sibling by module.
    - Three leaves import pure names by name: `scope.py:31`, `redaction.py:17-18`, and `pricing.py:16`.
    - No command-group module imports a sibling by name.
13. `pyproject.toml` sets `line-length = 130` and selects `E`. Its only per-file E501 ignore covers a frozen data fixture, and its comment says why. `[verified: pyproject.toml:2, :6, :8-15]`
14. By-module leaf access would push at least these lines past 130 columns: :5083, :5210, :5214, :5325, :5647, :5678 (three leaf constants in one f-string line), :5697, and :5773. `[unverified — lengths estimated from Read, not measured by a tool]`
15. Shim import fallout: `[verified: grep]`
    - `bisect` is read only at :5409–5410, and no test reads `_mod.bisect`.
    - `_corpus_fingerprint` is read only at :5045, but tests read `_mod._corpus_fingerprint` at `test_transcript_cost.py:1174, :1179` and `test_transcript_analysis.py:17422`.
    - Every other import the spans read is still read by surviving shim code: `_cache_miss_reason` :9050, `_FAST_MODE_RATE_MULTIPLIER` :3879, `_model_rates` :8850, `statistics` :6471, `_RedactMapKey` :2616.
16. `_UNCONDITIONAL_HEADER_CASES` row :16440 reads `_mod.cmd_cache_rebuild` and `_cache_rebuild_args`. Two classes consume the table: `TestAllSubcommandsSingleRootHeader` (:16472) and `TestRootsThreadingSpy` (:16632); the research named only the first. `[verified: Read :16412-16669]`
    - The spy patches both the shim-level names and `_mod.scope._resolve_project_scope`/`print_resolved_scope` (:16651–16657). After the move it therefore still intercepts cache-rebuild through `scope.*`.
    - The row needs no retarget. The shim keeps binding `cmd_cache_rebuild`, just as the already-moved `read-scope` (:16447) and `cost` (:16433) rows read `_mod.cmd_*` bare.
17. Test block layout: `[verified: class-def grep]`
    - :5959–5961: the `# cache-rebuild` header;
    - :5964–6204: helpers;
    - 45 classes: B :6206–6555, C :6556–8012, D :8013–8424, E :8425–10094;
    - :10097: cost-ledger's `_reviewer_dispatch_records`.
18. Where each helper is used: `[verified: grep -o]`
    - `_cache_rebuild_args`: groups C and E, plus :16440.
    - `_extract_cache_rebuild_*`: groups C and E (:9656, :9657, :9718, :10063).
    - `_extract_ttl_verdict_*` and the four `_ttl_verdict_*_records` builders: from :8484 on, across three destination files.
    - Used in one file only:
      - `_tool_result_record`/`_meta_marker_record`: only :6218–6464;
      - `_ttl_verdict_ts`: only :9533–9955;
      - `_pure_1h_write_plus_read_records`: only :9553–9709;
      - `_RATE_FOOTING_*`, `_rate_footing_records`, `_run_ttl_verdict_on_origin`: only :9760–9958.
    - Group D uses no family helper.
    - The hit at :19882 is a test name, not a call. `[verified: Read]`
19. Only `_run_ttl_verdict_on_origin` (:9811) reads `_mod`, and it stays local. The record builders call conftest's `_priced` (`conftest.py:434`). `[verified: Read]`
20. `conftest.py` is 1,010 lines. No test enforces a file-size limit yet. `[verified: Grep line count; grep of the test trees for a size check found none]`
21. Test-side "above"/"below" references under the chosen layout: `[verified: grep of '\b(above|below)\b' over :5959-10094; direct Read at :6753-6758, :8213-8246, :8369-8373, :8905-8974, :9004-9012, :9519-9540]`
    - :8217 and :8246 point to `TestCacheRebuildTtlVerdictDominantTierShareThreshold`. :8373 points to `TestCacheRebuildTtlVerdictTierSplitCrossCheck`. All three stay "below" in `test_transcript_cache_rebuild_ttl_rules.py`.
    - :9521 ("fixtures below") stays local.
    - :6925 ("TestAttributeIdleGapCause above") and :6757 hold in their files.

    The remaining hits were judged intra-class by their distance from a destination-range boundary, not read directly. This is a disclosed heuristic, the same as pr-cost's row 26.
22. Three production positional references cross the new module seam. Every other in-span above/below reference is intra-function or not positional. `[verified: grep; Read]`
    - :4479 says "above", meaning the pricing constants.
    - :4551 says "the concurrency split below", meaning the report.
    - :5683 says "`_CACHE_REBUILD_TTL_DOMINANT_TIER_SHARE_MIN` above", meaning the leaf.
23. The `# cost-ledger` header at :5954–5956 is displaced, not stray. It belongs to the content at :10097+ and was separated from it by the cache-rebuild block. Deleting the block restores the adjacency. `[verified: Read :5954-5961; _reviewer_dispatch_records callers only at :10470-10547]`
24. Test selection: `[verified]`
    - Any path under `claude/.claude/scripts/` selects `scripts/tests/` (`select-tests.py:381`).
    - No moved test reads a hook or SKILL.md by path. The legacy file's `HOOKS_DIR`/`SKILLS_DIR` reads sit at :17504–17537, :17933, and :19763+. So the new file names need no `CROSS_DOMAIN_EXCEPTIONS` entry (:486).
25. The doc-drift test requires exactly one `### \`<module>.py\`` heading per module. `[verified: test_transcript_analysis_architecture_doc.py:14-32]`
26. `test_transcript_cli_bootstrap.py` has no cache-rebuild test. Read-scope's help-plus-seeded-subprocess pair is at :445–497. cache-rebuild is in `_SUBCOMMANDS_REFUSING_TOP_LEVEL_CONFIG_DIR` (`scope.py:523`). `[verified]`
27. No local variable in either span is named `corpus`, `pricing`, `redaction`, `render`, `scope`, or `cache_rebuild_rules`. `[verified: Read of both spans]`
28. Other docs: `[verified: grep]`
    - `docs/transcript-analysis.md:1043` names `_cache_rebuild_report` by function name only.
    - `claude-skills/skills/transcript-analysis/SKILL.md` names no moved symbol.
    - `docs/cost-levers-considered.md:254` cites `transcript-analysis.py:7782-7783`, already stale per the discovery audit's C23.
29. The shim keeps binding `_parse_ts` (:61) and `_DO_NOT_PUBLISH_BANNER` (:232), because surviving shim code reads both (for example cache-efficiency at :4426). Moved tests' `_mod._parse_ts`/`_mod._DO_NOT_PUBLISH_BANNER` reads therefore stay unchanged, which answers the research's binding concern. `[verified]`
30. The `cache-write-analysis` worktree's unimplemented plan cites absolute shim line numbers inside this span. `[unverified — Step 3 research; not re-read]`
31. The governing plan moves one group's production code and test slice per PR. `[verified: transcript-analysis-decomposition.md:150-153]`
32. No `test_transcript_cache_rebuild*.py` exists yet. `[verified: Glob of scripts/tests]`
33. Estimated sizes, as moved lines plus a header allowance of about 35 lines for tests and about 55 for `cache_rebuild.py`, which includes its 31-name import block. `[verified: arithmetic over the Critical-files spans; header allowances estimated from test_transcript_read_scope.py:1-35]`
    - Production:
      - `cache_rebuild_rules.py`: 483 → about 495;
      - `cache_rebuild.py`: 818 → about 873;
      - both together would be 1,301 moved lines.
    - Tests:
      - `_cache_rebuild_helpers.py`: 222 → about 230;
      - attribution: 868 → about 903;
      - core: 523 → about 558;
      - switch_delta: 397 → about 432;
      - ttl_rules: 845 → about 880;
      - ttl_accumulation: 661 → about 696;
      - ttl_footing: 576 → about 611.

**Plan-review should re-check:** the M3 by-name import choice, row 14's estimate, row 21's heuristic portion, row 30, and M6's decision to keep the reconciliation class in the legacy file.

## Critical files

**Precondition.** If `origin/main` has moved past `0d155589`, sync the branch (`git-feature-branch-sync`), then re-locate every span below by its first and last symbol (row 3).

**Create — production.** Moved code is copied verbatim. Only four kinds of edit are allowed:
- module prefixes on the five existing package modules;
- the three row-22 rewrites below;
- each module's docstring and import header;
- wrap-only reflow, which M3 should make unnecessary. Report any reflow in the PR body.

Every module starts with `from __future__ import annotations`. Docstrings and comments carry no issue or PR numbers.

- **`transcript_analysis/cache_rebuild_rules.py`** takes shim :4478–4960, from the idle-gap-boundary comment through `_negate_switch_delta_for_display`.
  - Imports: `re`, `Sequence`, and `from transcript_analysis import corpus, pricing`.
  - Docstring: `"""The cache-rebuild family's pure rules: per-call cause classification against the vendor's 5m/1h cache tiers, subagent idle-gap cause attribution, per-call priced excess and cacheTtl switch deltas, and the --ttl-verdict per-root reducers, plus every label constant they emit.\n\nImports its package dependencies by module (attribute access, not by name) -- see scope.py's own top-of-file comment for why."""`
  - Rewrite :4479 to `# (_CACHE_WRITE_5M_MULTIPLIER/_CACHE_WRITE_1H_MULTIPLIER in pricing.py, same source).`
  - Rewrite :4551 to `# and cache_rebuild.py's concurrency split -- session start has no prior cache to`
- **`transcript_analysis/cache_rebuild.py`** takes :4475–4476 and :4961–5776. The section divider at :4469–4473 is not carried over; its citation moves into the docstring.
  - Imports: `argparse`, `bisect`, `statistics`, `sys`, `defaultdict`, `Sequence`, `Path`, and `from transcript_analysis import corpus, pricing, redaction, render, scope`.
  - A by-name `from transcript_analysis.cache_rebuild_rules import (...)` block listing exactly the names ruff F821 reports. There should be 31.
  - Docstring: `"""The cache-rebuild command family: cmd_cache_rebuild and its report -- full-prefix cache rebuilds after the vendor's 5m/1h cache TTL expires during an idle gap, priced against a warm-cache read at the same token count (.claude/plans/context-cost-root-cause.md records the corpus finding this reproduces).\n\nImports corpus, pricing, redaction, render, and scope by module -- see scope.py's own top-of-file comment for why. Imports cache_rebuild_rules' constants and pure functions by name, since none is reassigned at runtime; a test that patches one must also patch this module's binding."""`
  - Rewrite :5683 to `# _CACHE_REBUILD_TTL_DOMINANT_TIER_SHARE_MIN in cache_rebuild_rules.py).`
- **Rename map.**
  - `_parse_ts` and `_read_session_file_partitioned` → `corpus.`;
  - `_price_turn`, `_cache_write_split`, `_model_rates`, `_cache_miss_reason`, `_FAST_MODE_RATE_MULTIPLIER`, and `_INFERENCE_GEO_US_RATE_MULTIPLIER` → `pricing.`;
  - `_dedup_turns_by_request_id` → `pricing.dedup_turns_by_request_id`;
  - `_RedactMapKey`, `_build_redact_map`, and `_corpus_fingerprint` → `redaction.`;
  - `_pct_of` and `_fmt_usd` → `render.`;
  - `_resolve_cost_roots`, `_DO_NOT_PUBLISH_BANNER`, `_parse_since_nd_arg`, `_resolve_project_scope`, `_redaction_ordinals`, and `_root_index_for_path` → `scope.`;
  - `_print_resolved_scope` → `scope.print_resolved_scope`.

  **Do not prefix `_context_at_turn`** (row 8). Let ruff F821 drive every prefix; never run a regex over bare names.

**Create — tests.** Each test file gets a one-line docstring naming its module and theme, plus the loader from `test_transcript_read_scope.py:1-28`. It imports from `.conftest` and `._cache_rebuild_helpers`, never with a bare sibling import (`.claude/rules/test-tree-packaging.md`). The `# cache-rebuild` header (:5959–5961) is dropped. Assemble ranges in source order.

- **`tests/_cache_rebuild_helpers.py`** takes :5964–6185 verbatim, from `_cache_rebuild_args` through `_ttl_verdict_dominant_1h_mixed_root_records`.
  - Imports: `re` and `from .conftest import _priced`.
  - Docstring: `"""Test helpers shared by the cache-rebuild family's test files (test_transcript_cache_rebuild*.py) and by test_transcript_analysis.py's cross-subcommand tables."""`
- **`tests/test_transcript_cache_rebuild.py`** covers core report mechanics. It takes :6556–6752, :6822–6919, and :7785–8012.
- **`tests/test_transcript_cache_rebuild_attribution.py`** covers subagent idle-gap cause attribution, both unit and report tests. It takes :6188–6555, including the file-local `_tool_result_record` and `_meta_marker_record`, and :6920–7419.
- **`tests/test_transcript_cache_rebuild_switch_delta.py`** covers 5m→1h switch-delta pricing, display rounding, and per-dispatch dispersion. It takes :6753–6821 and :7420–7747.
- **`tests/test_transcript_cache_rebuild_ttl_rules.py`** covers `--ttl-verdict` wiring, each verdict rule's unit tests, the reducer, and each rule's full-pipeline boundary test. It takes :8013–8469, :8911–8959, and :9180–9518.
- **`tests/test_transcript_cache_rebuild_ttl_accumulation.py`** covers per-root accumulation and dominance reduction. It takes :8470–8910 and :8960–9179.
- **`tests/test_transcript_cache_rebuild_ttl_footing.py`** covers pure-1h idle-band reads, rate-multiplier footing, the unpriced and cache-miss-reason disclosures, and the default-path regression. It takes :9519–10094, including the file-local `_ttl_verdict_ts`, `_pure_1h_write_plus_read_records`, `_RATE_FOOTING_*`, `_rate_footing_records`, and `_run_ttl_verdict_on_origin`.
- Every moved test applies M7(a). There are no patch retargets. Nothing else on an `assert` line changes.

**Modify:**
- **`claude/.claude/scripts/transcript-analysis.py`**
  - Delete :4469–5778, leaving two blank lines between :4466 and the cost-ledger divider.
  - Make the M4 import changes, and add the two modules to the comment at :35–39.
  - `build_parser()` and every surviving `cmd_*` body stay unchanged.
- **`claude/.claude/scripts/tests/test_transcript_analysis.py`**
  - Delete :5959–10096, except `TestCacheRebuildCrossInstrumentReconciliation` (:7748–7784). Move that class verbatim to directly after `TestCacheEfficiencyArgparseWiring`, above the `# cost-ledger` header (row 23).
  - Retarget the class's `_mod._cache_rebuild_report` (:7774) to `_mod.cache_rebuild._cache_rebuild_report`.
  - Add `from ._cache_rebuild_helpers import _cache_rebuild_args`.
  - Let ruff F401 prune the imports this leaves unused.
  - `_UNCONDITIONAL_HEADER_CASES`, `TestRootsThreadingSpy`, and `TestFormatDriftCanary` stay unchanged.
- **`claude/.claude/scripts/tests/conftest.py`**: docstring only. Append the six new test files and `tests/_cache_rebuild_helpers.py` to the consumer list at :4–12. No fixture changes.
- **`claude/.claude/scripts/tests/test_transcript_cli_bootstrap.py`**
  - Add `test_transcript_analysis_cache_rebuild_help_exits_zero`. It asserts exit 0 and that `30d` and `100,000` each appear in stdout. These are single tokens because argparse only wraps at whitespace.
  - Add `test_transcript_analysis_cache_rebuild_subprocess_finds_seeded_call`. It seeds one priced main-thread assistant record timestamped an hour before now, so it falls inside the default `--since 30d`. It uses `_isolated_config_env` and asserts exit 0 and `Calls scanned: 1`.
- **`docs/transcript-analysis-architecture.md`**
  - In the exception paragraph (:14–32), add `cache_rebuild.py` to the list of modules the shim imports back into. Add one sentence saying that `build_parser()` wires up `cmd_cache_rebuild` and its `--since`/`--threshold` default constants from the shim.
  - After `pr_cost_export.py`, add a `### \`cache_rebuild_rules.py\`` section. It states: a leaf, imports `corpus` and `pricing` by module, and no name reached bare from the shim.
  - Then add a `### \`cache_rebuild.py\`` section. It states:
    - the five modules imported by module;
    - the by-name leaf import and its patch-both-bindings consequence;
    - the three names the shim reaches bare.
  - In the Tests section, list the six files and `_cache_rebuild_helpers.py`. Note that the reconciliation class stays in the legacy file because it spans two commands.
- **`claude/.claude/scripts/transcript_analysis/__init__.py`**: docstring only. Add `cache_rebuild_rules` to the leaf-modules list and `cache_rebuild` to the command-group-modules list. pr-cost's own review round updated this same docstring in-PR (`0d155589`) rather than deferring it — this phase follows that precedent instead of the "left for the `cli.py` phase" deferral an earlier draft of this plan wrongly attributed to pr-cost.

**Explicitly unchanged:** `select-tests.py`, `test_select_tests.py`, `docs/transcript-analysis.md`, the transcript-analysis `SKILL.md`, the existing package modules, and all cache-efficiency code and tests.

**Reuse:**
- `read_scope.py:1-16` for the header shape.
- `test_transcript_read_scope.py:1-28` for the loader.
- `tests/_pr_cost_helpers.py` for the helper-module shape.
- `test_transcript_cli_bootstrap.py`'s `_run` and `_isolated_config_env`, and the record shape from `_seed_read_scope_account`.

**Dispatch.** One `code-writer` dispatch (M9) covers every file above.
- **Stage 1.** Create both production modules and delete the span from the shim. Temporarily import every moved top-level name into the shim by name, so the unedited legacy tests resolve `_mod.<name>` against the moved code. This block is lint-dirty by design. Run the scoped suite; it must be green before Stage 2.
- **Stage 2.**
  - Create the helper module and the six test files, and apply M7.
  - Make the legacy, conftest, bootstrap, and doc edits.
  - Replace the temporary re-export with M4's final imports.
- **Abort procedure.** If the dispatch is interrupted between Stage 1 and Stage 2 (crash, context limit, failed gate), discard the whole working tree and restart the dispatch from Stage 1. There is no partial-progress commit to resume from, and none is needed — Stage 1's edits are never committed on their own.
- Extract moved code by line range from unmodified scratch copies. Never retype it.
- In the PR body, state:
  - M3's by-name rationale: no reassigned or patched leaf names, and the E501 reflow the alternative forces;
  - M6's reconciliation-class decision;
  - the count of wrap-only reflows;
  - measured file sizes;
  - `git blame -C -C -s` counts;
  - Stage 1's scoped-suite result (e.g. "Stage 1 gate: N passed"), so a reviewer can confirm the internal gate actually ran even though the intermediate state isn't kept.
- Verify with steps 0–11 below.

## Verification

Run everything from the worktree root. `<venv>` is the worktree-relative `.venv` from README.md's Tests section. Scratch files live outside the repo, and the corpus is synthetic.

0. **Baseline, before the dispatch.**
   - Copy the unmodified shim, legacy test file, conftest, `test_transcript_cli_bootstrap.py`, and `docs/transcript-analysis-architecture.md` to scratch.
   - Save the `<venv>/bin/pytest --collect-only -q` IDs for `test_transcript_analysis.py`.
   - Capture top-level `--help`, `cache-rebuild --help`, and `cache-efficiency --help`.
   - Seed a two-account synthetic corpus. Timestamp every record one to two days before seeding, so the default `--since 30d` holds through step 3. It needs:
     - main and subagent files;
     - idle gaps in the 5m–1h and >1h bands;
     - an own-Bash marker whose command contains `sleep 5`;
     - a background-task meta marker;
     - a mixed-tier root;
     - a fast-mode call;
     - an unpriced model.
   - Capture stdout, stderr, and exit code for each run below. Set up accounts per `_isolated_config_env`.
     - (a) `cache-rebuild`;
     - (b) `cache-rebuild --ttl-verdict`;
     - (c) `cache-rebuild --ttl-verdict --config-dir <acct2>`;
     - (d) `cache-rebuild --no-redact`;
     - (e) `cache-rebuild --no-redact --config-dir <acct2>`, which should refuse with exit 2;
     - (f) `cache-efficiency --config-dir <acct2>`, as a check that the neighbouring command is untouched.
   - Record `wc -l` for the shim, the legacy test file, and conftest.
1. **Scoped suite.** Run `<venv>/bin/python3 claude/.claude/scripts/select-tests.py`. It selects `scripts/tests/`, `test_select_tests.py`, `claude/.claude/tests/` and `test_ticket_reference_discipline.py` (both via `_is_py_source_under_claude_or_plugins`, since the diff edits and creates `.py` source under `claude/`), and, because of the `docs/` edit, `hooks/tests/` and `skills/tests/`. Every selected test passes, including the architecture-doc drift test and `TestNoBareSameDirectorySiblingImports` (`claude/.claude/tests/test_pytest_collection_config.py`).
2. **Test-ID parity.** Strip each ID's file prefix and compare the sorted lists. Step 0's list must equal the combined post-move lists of the legacy file and the six new files, duplicates included.
3. **CLI parity.** Every `--help` capture and every run (a)–(f) must be byte-identical to step 0's.
4. **Spy reach.** In scratch, delete `TestRootsThreadingSpy`'s two `_mod.scope` `setattr` lines (:16656–16657). Confirm the `cache-rebuild` parametrized case fails, then restore them. This proves `cache_rebuild.py` reaches scope through attribute access (row 16).

   Also prove the by-name leaf import's dual-patch requirement (M3, the riskiest new choice in this phase): in a throwaway scratch script, `monkeypatch.setattr(cache_rebuild_rules, "<a moved constant>", <changed value>)` alone and confirm `cache_rebuild.py`'s behavior is unaffected; then additionally `monkeypatch.setattr(cache_rebuild, "<same name>", <changed value>)` and confirm it now takes effect. Scratch-only verification, not a committed test — no current test patches any of the 31 names (row 11), but the new module's docstring asserts this dual-patch requirement, and nothing yet demonstrates it's true.
5. **Hook sandbox.** Confirm step 1 ran `claude/.claude/hooks/tests/test_nudge_error_mode_analysis.py`. If it did not, run it alone.
6. **Lint.** Run `<venv>/bin/ruff check claude/.claude/scripts/`.
7. **Leftovers.** After the move, `git grep -nE '^(def |class )?(<moved top-level names, |-joined>)\b' claude/.claude/scripts/transcript-analysis.py` must return nothing. The `^` anchor excludes the indented by-name import lines.
8. **Prefix correctness.** Run a scratch `ast` script over both production modules and the six test files.
   - In production code, every `Attribute(Name(<package module>), <name>)` must name something that module defines at top level, not something it merely imports.
   - No such attribute may appear in `Store` context. This catches a `pricing._context_at_turn` unpacking target.
   - In test code, every `_mod.<module>.<name>` must name something `<module>` itself defines.
9. **Move fidelity.**
   - **Production.** Strip the rename-map prefixes, but only when followed by `_`, `print_resolved_scope`, or `dedup_turns_by_request_id`. Map the two aliases back. Then compare each top-level node's `ast.dump` against the step 0 spans (:4475–4476 plus :4961–5776 for the command module; :4478–4960 for the leaf). The `#` comment lines must differ only at the three row-22 lines.
   - **Tests.** Rewrite `_mod.(cache_rebuild|cache_rebuild_rules).` back to `_mod.`. Each top-level node's AST must match the step 0 slice, except the reconciliation class's one retarget. The comment-line diff must be empty.
   - **Positional-comment re-check.** The comment-line-diff checks above prove comments were copied verbatim, not that a heuristically-classified "above"/"below" reference (row 21's unread remainder) is still geometrically true in its destination file. Grep `\b(above|below)\b` in each new production and test file and manually re-confirm any hit row 21 didn't verify by direct read.
   - AST equality tolerates wrap-only reflow. Report the reflow count.
   - After the commit, run `git blame -C -C -s` on every new file and put the counts in the PR body.
10. **Sizes.** Report measured `wc -l` for every new and shrunk file in the PR body. A file over 1,000 lines is flagged there, not split further ad hoc.
11. **This phase's own revert.** In a throwaway worktree, `git revert --no-commit` the squashed commit. Assert a zero diff against step 0's snapshot of the shim, legacy test file, conftest, `test_transcript_cli_bootstrap.py`, and `docs/transcript-analysis-architecture.md`. Also assert that none of the 9 newly created files (both production modules, the six new test files, and `tests/_cache_rebuild_helpers.py`) remains on disk.

The governing plan's cross-phase revert rehearsal is omitted. This phase promotes no conftest fixture, and its conftest edit is docstring-only, so no adjacent phase's revert depends on it.

## Out of scope

- **Extracting cache-efficiency (M1).** This includes moving `TestCacheRebuildCrossInstrumentReconciliation` and `TestFormatDriftCanary`'s cache-efficiency methods, and giving `_cache_efficiency_args` a shared home. That phase settles all three.
- **Splitting `_cache_rebuild_report` into functions.** That is a behavioral refactor, not a move.
- **Renaming moved names or output strings.** Examples include `_CACHE_MISS_REASON_MODEL_CHANGED` and the `_TTL_*` labels.
- **Changing the shim-plus-package shape, moving `build_parser()` early, or relocating `_UNCONDITIONAL_HEADER_CASES`.** Each is reachable, and each is declined here; the governing plan defers all three to the `cli.py` phase. `[verified: transcript-analysis-decomposition.md:144-161]`
- **Raising ruff's line length or adding a per-file E501 ignore.** The repo owns this setting, and M3 avoids needing either.
- **conftest.py's size (1,010 lines) and rewording its consumer list as a category.** F1's assessment owns the size. The category rewrite needs an audit of which `test_transcript_*.py` files actually import conftest's builders.
- **`docs/cost-levers-considered.md:254`'s stale line citation.** It predates this phase (the discovery audit's C23) and sits in a dated "From …" record section.
- **:5332's dangling "see `_CAUSE_IDLE_5M_1H`'s own docstring caveat".** It predates this phase, and the constants have no docstrings. It moves verbatim.
- **`TestRootsThreadingSpy`'s comment (:16653–16655).** It names only cost and cost-trend as examples. The list already omits read-scope and pr-cost.
- **Re-anchoring the `cache-write-analysis` branch's plan (row 30).** Its absolute shim citations go stale when this lands. It needs a re-read before its own implementation, not a mechanical rebase.
- **An import-direction guard test for the new modules.** No prior phase added one.
- **Any CLI surface change** (Verification step 3).
