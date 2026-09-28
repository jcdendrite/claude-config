# Decompose audit-routing out of transcript-analysis.py

## Context

Extract the audit-routing command family (`cmd_audit_routing`,
`cmd_audit_routing_shape`, `cmd_audit_routing_samples`) out of the
9,628-line monolith `claude/.claude/scripts/transcript-analysis.py` into a
new package module, continuing the multi-phase decomposition tracked by
issue #1116 (governing plan
`.claude/plans/transcript-analysis-decomposition.md`).

Three prior phases already landed: read-scope (PR #1128), pr-cost family
(PR #1136), and cache-rebuild (PR #1145). The governing plan's explicit
ordering (line 150: "cost family, reviewer-yield, review-trace,
audit-routing, cost-ledger, then the remainder") turns out to already have
reviewer-yield and review-trace extracted too — confirmed this session via
`transcript_analysis/{reviewer_yield,review_trace}.py` both existing on
disk — leaving audit-routing as the next unclaimed group. The family is
762 exclusive production lines and roughly 1,248 test-class lines,
well-isolated: no direct `PROJECTS_DIR` read, no `--no-redact` multi-root
refusal guard, and no shared private helper across the three commands
beyond a small set of family-only classifiers and constants (the three
commands deliberately duplicate their judgment-span state machines for
cross-validation by tests, and that duplication is preserved as-is).
Intended outcome: the same shim-plus-package shape as the three prior
phases — a new `transcript_analysis/audit_routing.py` module, a relocated
test slice, an updated architecture doc, and a still-green doc-drift test.

## Approach

audit-routing moves out of the shim into one new package module,
`transcript_analysis/audit_routing.py`. It takes `AUDIT_JUDGMENT_SKILLS`,
the three `cmd_*` functions, and their nine family-only helpers and
constants, copied verbatim in source order. Its tests go into three new
test files, one per subcommand, plus a family helper module,
`tests/_audit_routing_helpers.py`. Four generic record builders that other
command groups also use move to `conftest.py`. The shim keeps
`build_parser()` unchanged and imports the three `cmd_*` names by name.
`_UNCONDITIONAL_HEADER_CASES`, its two classes, and
`TestMultiRootFormatOutliers` stay in the legacy file. They now read the
two args factories from the helper module.

The Step 3 evidence needs four corrections, and each one changes the
design:
- **One test file is too big.** It would be about 1,430 lines, which is
  over the per-phase 1,000-line target (rows 21, 23). That forces a split.
- **Four generic builders are missed.** `_priced_opus`, `_exit_plan_mode`,
  `_thinking_block`, and `_read_use` are family helpers that tests outside
  this family also call (row 14). They cannot move with the family.
- **Legacy lines :7938–7959 are this family's own code.** They hold the
  samples family's header and args factory, not another class's code, so
  they move (row 17).
- **A second legacy consumer exists.** `TestMultiRootFormatOutliers` also
  reads `_audit_routing_samples_args` and `_read_use` (row 22).

Alternatives considered and set aside:
- **Promoting `_audit_routing_shape_args`/`_audit_routing_samples_args` to
  `conftest.py` next to `_audit_routing_args`.** This was the Step 3
  session's proposal, not the engineer's. The concern behind it was stale
  duplicate copies left in the legacy file. The helper module removes
  that concern just as well: the legacy file imports both factories from
  it, and each factory keeps a single definition. I rejected the proposal
  for three reasons:
  - conftest is already over the limit at 1,016 lines (row 20).
  - cache-rebuild is the most recent precedent for exactly this shape: an
    args factory that the legacy cross-subcommand table reads. It put
    that factory in its family helper module (row 34).
  - The helper module has to exist anyway for `_extract_corpus_class_tokens`
    and `_extract_shape_d1`, which cross between the new files (row 18).
    Promoting the factories to conftest would add conftest lines and
    still leave the module in place.

  The cost is that the family's three args factories live in two homes.
  `_audit_routing_args` stays in conftest because
  `test_transcript_cost.py:18, :554` imports it from there (Out of
  scope).
- **A leaf-plus-command production split.** No size limit forces it, and
  no outside consumer needs it (M1).
- **One test file.** It is over the limit (M4).

### Assumption ledger

**Root:** audit-routing is still in the monolithic shim: 762 exclusive
production lines (:1097–1104, :2563–2818, :5372–5869) and 1,248
test-class lines. The governing plan lists it next in extraction order,
after reviewer-yield and review-trace, and both of those have already
moved. `[verified: Read of the three spans; transcript-analysis-decomposition.md:150–153; shim :39–57 imports reviewer_yield and review_trace as modules]`

**Givens:**
- G1. pytest's `prepend` import mode imports test modules by basename, so
  new test files keep the `test_transcript_*` prefix. Reason: pytest owns
  this behavior. `[verified: transcript-analysis-decomposition.md:59-63]`
- G2. `from m import n` binds `n` at import time. A monkeypatch therefore
  reaches code only through the binding that code reads at call time.
  Reason: Python language semantics. `[verified: transcript-analysis-decomposition.md row 1]`
- G3. A directly invoked `python3 transcript-analysis.py` finds the
  package only through CPython's `sys.path[0]`. Reason: CPython owns
  script bootstrap. `[verified: tests/test_transcript_cli_bootstrap.py:1-11]`

Five conditions look like givens but are not. This repo owns each one, so
each is a deliberate decline listed in **Out of scope**:
- the shim-plus-package shape;
- `build_parser()` staying in the shim;
- the cross-command tests staying in the legacy file;
- the three deliberately duplicated judgment-span state machines;
- conftest's size.

**Mechanisms:**
- **M1: One production module, `audit_routing.py`, filled by line-range
  extraction in source order.** The spans are :1097–1104, then
  :2563–2818, then :5372–5869, separated by two blank lines. `anchors:
  root, row3, row5, row6, row9, row23`
  - Heavier, rejected: a leaf-plus-command split (for example, an
    `audit_routing_rules.py` for `_classify_opus_turn` and the buckets).
    The module is about 785 lines, under the limit. No non-test code
    outside the family reads any helper (row 5). By contrast,
    review-trace split because its classifiers had outside consumers, and
    cache-rebuild split because it was over the limit.
  - Heavier, rejected: one module per command. All three read
    `_classify_opus_turn` and `AUDIT_JUDGMENT_SKILLS`, so this needs a
    fourth, shared leaf to carry about 760 lines.
  - Lighter, not possible: leaving `AUDIT_JUDGMENT_SKILLS` in the shim.
    All three commands read it, and package modules may not import from
    the shim (row 9).
- **M2: Import discipline.**
  - stdlib: `argparse`, `json`, `random`, `sys`.
  - Package: `from transcript_analysis import corpus, pricing, redaction,
    render, scope`, read by module only.
  - No sibling is imported by name. cache-rebuild's by-name exception (its
    M3) was forced by E501. Here no prefixed line should pass 130 columns
    (row 11).

  `anchors: G2, row7, row8, row10, row11`
- **M3: Shim imports.** `anchors: row4, row12, row13`
  - Add `audit_routing` to the module-import list (:39–57) and to its
    comment (:34–38). This is the tests' `_mod.audit_routing` channel.
  - Import `cmd_audit_routing`, `cmd_audit_routing_samples`, and
    `cmd_audit_routing_shape` by name in the commented style the file
    already uses.
  - Drop `_RedactMapKey`, `_RECENT_LOOKBACK_N`, `_format_samples_as_markdown`,
    `_recent_assistant_text`, and `_recent_tool_trail`.
- **M4: Test layout.** Three test files split along the subcommand seam,
  plus `tests/_audit_routing_helpers.py`. `anchors: G1, row18, row20,
  row21, row23, row34`
  - The helper module holds the four family helpers that more than one
    file reads:
    - `_extract_corpus_class_tokens`;
    - `_audit_routing_shape_args`;
    - `_extract_shape_d1`;
    - `_audit_routing_samples_args`.
  - Every other family helper stays local to the one file that uses it.
  - Rejected: one file. It is about 1,430 lines.
  - Rejected: two files. They still need the helper module, since
    `_extract_shape_d1` and `_audit_routing_shape_args` cross to samples,
    and the seam is less nameable.
  - Rejected: conftest promotion of the two factories (above).
  - Rejected: new files importing from the legacy test module.
    Read-scope, pr-cost, and cache-rebuild all rejected this, because it
    couples test modules and runs the legacy loader twice.
- **M5: Four generic record builders move to `conftest.py`,
  byte-identical.** They are `_priced_opus` (placed after `_opus`), and
  `_read_use`, `_exit_plan_mode`, and `_thinking_block` (placed after
  `_skill_use`). `anchors: row14, row15, row20`
  - Rejected: the family helper module. Four other command groups still
    in the legacy file call these builders. Later phases would then
    import `_exit_plan_mode` from `_audit_routing_helpers` or re-home it.
  - Rejected: duplicate copies. Record builders are not a DAMP exception
    (row 15).
  - Cost: conftest grows by about 30 lines past a limit it already
    exceeds. F1 owns conftest's size.
- **M6: Tests that span several commands stay in the legacy file,
  byte-identical.** `anchors: row13, row16, row22`
  - The `_UNCONDITIONAL_HEADER_CASES` rows at :12331, :12341, and :12342.
  - `TestAllSubcommandsSingleRootHeader` and `TestRootsThreadingSpy`.
  - `TestMultiRootFormatOutliers`, which spans cost, audit-routing-samples,
    and judgment-pair.
  - `test_transcript_cost.py`'s shared-redact-map test (:536–561).
- **M7: Test re-point rules.** `anchors: G2, row5, row13, row19, row30`
  - (a) In moved test code, every `_mod.<name>` read of a moved name
    becomes `_mod.audit_routing.<name>`. This includes the three `cmd_*`
    names, `_classify_opus_turn`, and `_D1_BUCKETS`. It matches
    `_mod.review_trace.cmd_review_trace` and `_mod.read_scope.cmd_read_scope`,
    and it spares the `cli.py` phase from rewriting these calls. Where a
    line would pass 130 columns, wrap it only, changing no token.
  - (b) Test code that stays does not change. `_mod.cmd_audit_routing*`
    still resolves through the shim's by-name binding.
  - (c) `_mod.render.*` and `_mod._REDACT_MAP_MISS_TOKEN` reads in moved
    code stay as they are, because neither is a moved name.
  - (d) No monkeypatch needs retargeting.
- **M8: One `code-writer` dispatch in two internal stages, one commit.**
  `anchors: row5, row16`
  - Stage 1 moves the production code and adds a temporary by-name
    re-export of every moved top-level name from the shim. The unedited
    legacy tests then act as the oracle for the moved code.
  - Stage 2 moves the tests and prunes the shim's imports.
  - Lighter, rejected: a single stage. Once the tests are rewritten,
    nothing independently checks the production move. The oracle costs
    one scoped run.
  - Rejected: separate sequenced dispatches. Both stages edit the shim
    and the legacy test file.
- **M9: No `select-tests.py` change.** `anchors: row24`
- **M10: Six bootstrap tests, one help + one subprocess test per
  subcommand.** `test_transcript_cli_bootstrap.py`'s own established
  convention gives every subcommand in a multi-subcommand family its own
  pair (turn-shape's three subcommands each get one; review-trace's base
  command and its `--deny-summary` variant each get one). Audit-routing
  has the same three-subcommand shape as turn-shape, so all three —
  not only the base `audit-routing` command — get a help test and a
  seeded-subprocess test. Skipping the other two would silently drop
  them from the one CI layer that exercises the real
  `sys.path[0]`-bootstrapped package import, which is exactly the risk a
  later `build_parser()`/`cli.py`-phase import change could break
  unnoticed. `anchors: G3, row25`
- **M11: No positional-comment rewrites.** No "above"/"below" reference
  crosses the new seam. `anchors: row26`

**Assumptions:**

1. Line numbers are at `23b47058`, with a clean tree. If `origin/main`
   moves, re-locate each span by its first and last symbol. `[verified:
   gitStatus; Read]`
2. `transcript_analysis/reviewer_yield.py` and `review_trace.py` already
   exist, and the shim imports both. That makes audit-routing the next
   group in the governing plan's order. `[verified: shim :39–57,
   :211–240; transcript-analysis-decomposition.md:150–152]`
3. Production layout: `[verified: Read :1095–1107, :2561–2821,
   :5370–5872]`
   - :1097–1100: the comment. :1101–1104: `AUDIT_JUDGMENT_SKILLS`.
   - :2563–2568: `_AUDIT_CLASSES`, `_ORCHESTRATION_TOOLS`, and
     `_CODE_READ_TOOLS`.
   - :2571–2600: `_classify_opus_turn`.
   - :2603–2818: `cmd_audit_routing`. The Step 3 evidence said it ends at
     :2819, but it ends at :2818.
   - :5372–5423: the shape helpers and constants.
   - :5426–5680: `cmd_audit_routing_shape`. The Step 3 evidence said
     :5682.
   - :5683–5869: `cmd_audit_routing_samples`.
   - :5872: the turn-shape divider, which stays.
4. Outside the spans, only `build_parser()` reads a moved name: :8792,
   :9447, and :9473. The other hits are prose that names the function
   (`redaction.py:121`, `cost.py:1173`, shim :5961). That prose stays
   accurate. `[verified: grep of every moved name over scripts/ excluding
   tests]`
5. No non-test code outside the family reads any moved helper or
   constant. Tests read only `_mod._classify_opus_turn` (:3708) and
   `_mod._D1_BUCKETS` (:7861, :7884, :8155). `[verified: grep]`
6. Moved production lines: 8 + 256 + 498 = 762. `[verified: arithmetic
   over row 3]`
7. The spans' package dependencies: `[verified: Read of spans; shim
   :143–259]`
   - `scope`:
     - `_parse_since_nd_arg`;
     - `resolve_scan_roots` (shim alias `_resolve_scan_roots`);
     - `_resolve_project_scope`;
     - `print_resolved_scope` (shim alias `_print_resolved_scope`);
     - `_redaction_ordinals`;
     - `_root_index_for_path`.
   - `redaction`:
     - `_RedactMapKey` (annotation only; precedent is `cost.py:724`,
       `cache_rebuild.py:142`);
     - `_build_redact_map`;
     - `_derive_proj_label`;
     - `_assign_session_redact_label`;
     - `_redact_session_id`;
     - `_redact_proj_label`.
   - `pricing`: `dedup_turns_by_request_id` (shim alias
     `_dedup_turns_by_request_id`) and `_price_turn`.
   - `render`: `_content_text`, `_fam`, `_recent_assistant_text`,
     `_recent_tool_trail`, `_format_samples_as_markdown`, and
     `_RECENT_LOOKBACK_N`.
   - `corpus`: `_parse_ts`. `_CODE_WRITE_TOOLS` is already read as
     `corpus._CODE_WRITE_TOOLS` (:2593).
   - **Trap:** `_context_at_turn` at :2714 is a tuple-unpacking local. It
     never reads pricing's function and must not be prefixed.
8. No local in the spans is named `corpus`, `pricing`, `redaction`,
   `render`, or `scope`. The closest names are `corpus_totals` and
   `scope_label`. No identifier `audit_routing` exists anywhere under
   `scripts/`. `[verified: Read of spans; grep]`
9. Package modules may not import from the shim
   (`docs/transcript-analysis-architecture.md:14-16`). Every dependency
   in row 7 is a package module. `[verified]`
10. The spans never read `PROJECTS_DIR` or `config_dir` directly and
    contain no `global` statement. They reach roots only through
    `scope.resolve_scan_roots` and `scope._resolve_project_scope`.
    `[verified: Read of spans]`
11. ruff's limit is 130 columns (`pyproject.toml:2`). The longest moved
    line after prefixing is :5851, at about 125 columns. `[verified:
    pyproject.toml:2]` for the limit; the line length is `[unverified —
    hand-counted, not measured by a tool]`.
12. Shim import fallout: `[verified: grep of shim and tests]`
    - `_RedactMapKey` (:185), `_RECENT_LOOKBACK_N` (:189),
      `_format_samples_as_markdown` (:195), `_recent_assistant_text`
      (:198), and `_recent_tool_trail` (:199) have no other shim reader
      and no `_mod.<name>` test reader. The one hit at :8246 is a
      docstring.
    - `random` is still read at :6154, :6214, and :8137.
    - `_redaction_ordinals`, `_root_index_for_path`,
      `_assign_session_redact_label`, `_redact_session_id`, and
      `_redact_proj_label` keep other shim readers (:743–971).

    The remaining imports were not grepped one by one. `[unverified —
    ruff F401 decides, under the noqa rule in Critical files]`
13. Test code that stays and reads `_mod.cmd_audit_routing*`: legacy
    :12331, :12341, :12342, :12925, and `test_transcript_cost.py:554`.
    None of them patches a shim helper that audit-routing reads.
    `[verified: grep; Read test_transcript_cost.py:536–561]`
14. Callers of the generic builders outside this family: `[verified:
    grep]`
    - `_priced_opus`: :6500 (cost-ledger).
    - `_exit_plan_mode`: :15123–15598 (plan-boundary).
    - `_thinking_block`: :5203 (edit-format), :15870, and :16373
      (handoff-signal).
    - `_read_use`: :10817 and :10832 (subagents), and :12923
      (`TestMultiRootFormatOutliers`).
    - None of the four is defined anywhere else under `claude/`.
15. conftest hosts the record-builder family: `_edit_use` :291,
    `_agent_use` :360, `_opus` :418, `_skill_use` :815. The
    code-file-size-splits plan's M9 and read-scope's M5 both rule that
    shared record builders go to conftest and are never duplicated.
    `[verified: conftest grep; code-file-size-splits.md:400;
    read-scope-decomposition.md:73-74]`
16. `_UNCONDITIONAL_HEADER_CASES` spans :12311–12359, with rows at
    :12331, :12341, and :12342. Two classes consume it: :12362 and
    :12522. The spy patches both the shim names and
    `_mod.scope._resolve_project_scope`/`print_resolved_scope`
    (:12550–12556), so it still intercepts audit_routing's `scope.*`
    calls after the move. `[verified: Read :12306–12375, :12522–12568]`
17. Test block layout: `[verified: Read; class-def grep]`
    - :3430–3433: the `# audit-routing` header.
    - :3435–3449: `_priced_opus`.
    - :3452–3495: context-distribution helpers. They are not this
      family's and stay.
    - :3498–3503: `_exit_plan_mode` and `_thinking_block`.
    - :3506–3518: `_extract_corpus_class_tokens`. :3521–3531:
      `_extract_sonnet_tier_dollar_estimate`.
    - :3534–3840: `TestAuditRouting`.
    - :7478–7481: the shape header. :7483–7493: `_read_use`, `_grep_use`,
      `_glob_use`. :7496–7506: `_audit_routing_shape_args`. :7509–7608:
      the four `_extract_shape_*`.
    - :7611–7935: `TestAuditRoutingShape`.
    - :7938–7941: the samples header. :7943–7959:
      `_audit_routing_samples_args`. This is the family's own code,
      contrary to the Step 3 note.
    - :7962–8555: `TestAuditRoutingSamples`.
    - :8558: the turn-shape header.
    - :12958–12979: `TestAuditRoutingMultiRootRedaction`.
18. Where each family helper is used: `[verified: grep]`
    - `_extract_corpus_class_tokens`: routing, and shape at :7856.
    - `_extract_shape_d1`: shape, and samples at :8155.
    - `_audit_routing_shape_args`: shape, samples at :8153, and legacy
      :12341.
    - `_audit_routing_samples_args`: samples, and legacy :12342 and
      :12925.
    - `_extract_sonnet_tier_dollar_estimate`: routing only.
    - `_grep_use`, `_glob_use`, `_extract_shape_d2`, `_extract_shape_d3`,
      and `_extract_shape_d3_xtab`: shape only.
19. No moved span contains a `setattr(` call. `[verified: grep of the
    test file]`
20. `conftest.py` is 1,016 lines. The 1,000-line limit covers test files,
    but nothing enforces it until F1 lands, and conftest already exceeds
    it. `[verified: Read of conftest tail;
    docs/design-decisions/code-file-line-limit.md:19, :50-57]`
21. Estimated sizes, as moved lines plus about 30 header lines per test
    file: `[verified: arithmetic over rows 3 and 17; header allowance
    estimated from test_transcript_cache_rebuild.py:1-29]`
    - `audit_routing.py`: 766 → about 785.
    - `test_transcript_audit_routing.py`: 342 → about 375.
    - `test_transcript_audit_routing_shape.py`: about 410 → about 440.
    - `test_transcript_audit_routing_samples.py`: 594 → about 625.
    - `_audit_routing_helpers.py`: about 76.
    - One combined test file would be about 1,430.
    - conftest: about 1,048.
    - About 1,471 lines leave the legacy file.
22. `TestMultiRootFormatOutliers` (:12886–12955) spans cost,
    audit-routing-samples, and judgment-pair. `[verified: Read]`
23. Every phase targets the 1,000-line limit for modules and test files
    alike. `[verified: code-file-size-splits.md:175]`
24. Test selection: `[verified: select-tests.py grep]`
    - Any path under `scripts/` selects `scripts/tests/` (:381).
    - `.py` source under `claude/` selects the ticket-reference test and
      `claude/.claude/tests/` (:515).
    - The architecture doc edit selects `scripts/tests/`, `hooks/tests/`,
      and `skills/tests/` (:503–504).
    - The new file names do not match `TRANSCRIPT_ANALYSIS_TEST_GLOB`
      (:72). That glob exists only for tests that read hooks or SKILL.md
      by path, and no moved test does.
25. Bootstrap facts: `[verified]`
    - `test_transcript_cli_bootstrap.py` has no audit-routing test.
    - audit-routing is not in `_SUBCOMMANDS_REFUSING_TOP_LEVEL_CONFIG_DIR`
      (`scope.py:520-526`).
    - A top-level `--config-dir` makes `resolve_scan_roots` return that
      single root (`scope.py:353-355`).
    - `--since` has no default (shim :8777–8780), so the seed needs no
      timestamp.
26. Every production "above"/"below" in the spans is intra-function or
    output text: :2584, :2648, :5473, :5625, :5679, :5708. The moved test
    spans have none. `[verified: grep of both files]`
27. The doc-drift test requires exactly one `### \`<module>.py\``
    heading per module. `[verified:
    test_transcript_analysis_architecture_doc.py:14-32]`
28. Outside `scripts/`, only dated plans and audit reports cite moved
    symbols or old shim line numbers. `docs/transcript-analysis.md`,
    `claude-skills/`, and hooks name none. `[verified: repo grep
    excluding scripts/]`
29. No `test_transcript_audit_routing*.py` exists yet. `[verified: Glob]`
30. Moved tests already read `_mod.render._pretty_tool_call` and
    `_mod.render._BASH_COMMAND_DISPLAY_CHARS` (:8278, :8285, :8356,
    :8364). `[verified: Read]`
31. The governing plan requires a revert rehearsal because a conftest
    promotion can break an adjacent phase's revert (:276–279). This
    phase's conftest edit only adds names and deletes none. No earlier
    phase's revert can therefore lose a name it needs. Adjacency with
    cache-rebuild's conftest-docstring and legacy-import hunks can cause
    a textual conflict, but it cannot make the tree uncollectable.
    `[verified: transcript-analysis-decomposition.md:276-279; this
    plan's conftest edits]`
32. `fake_projects` patches `request.module._mod.scope`, so it works in
    any test module that defines `_mod`. `[verified: conftest.py:819-849]`
33. The governing plan's figure of 1,290 test lines predates later
    edits. This plan uses its own counts from rows 17 and 21.
    `[unverified — the older figure was not re-derived]`
34. Precedents for helpers that stay in the legacy file:
    - pr-cost's M6 put its three legacy-shared helpers in conftest, when
      conftest was 919 lines.
    - cache-rebuild put `_cache_rebuild_args` in its helper module, when
      conftest was 1,010 lines.

    `[verified: pr-cost-decomposition.md:86-87, :175;
    cache-rebuild-decomposition.md M5, M6, row 20]`

**Plan-review should re-check:**
- M4's rejection of the Step 3 conftest proposal;
- M5's conftest growth;
- row 11's hand-counted line length.

## Critical files

**Precondition.** If `origin/main` has moved past `23b47058`, sync with
`git-feature-branch-sync`. Then re-locate every span below by its first
and last symbol (row 1).

**Create: production.**
- **`claude/.claude/scripts/transcript_analysis/audit_routing.py`** takes
  shim :1097–1104, :2563–2818, and :5372–5869, in that order, separated
  by two blank lines.
  - Moved code is verbatim, except for rename-map prefixes and the
    header.
  - Expect zero reflows (row 11). Any reflow must be wrap-only, and the
    PR body must report it.
  - Header: the docstring, then `from __future__ import annotations`,
    then `argparse`, `json`, `random`, and `sys`, then `from
    transcript_analysis import corpus, pricing, redaction, render,
    scope`.
  - Docstring: `"""The audit-routing command family: cmd_audit_routing,
    cmd_audit_routing_shape, and cmd_audit_routing_samples -- per-turn
    Opus routing-class classification, the code-read turn-shape
    distributions, and a seeded sample of code-read turns for manual
    curation.\n\nImports corpus, pricing, redaction, render, and scope by
    module (attribute access, not by name) -- see scope.py's own
    top-of-file comment for why."""`
  - Rename map:
    - `_parse_since_nd_arg`, `_resolve_project_scope`,
      `_redaction_ordinals`, and `_root_index_for_path` → `scope.`
    - `_resolve_scan_roots` → `scope.resolve_scan_roots`
    - `_print_resolved_scope` → `scope.print_resolved_scope`
    - `_RedactMapKey`, `_build_redact_map`, `_derive_proj_label`,
      `_assign_session_redact_label`, `_redact_session_id`, and
      `_redact_proj_label` → `redaction.`
    - `_dedup_turns_by_request_id` → `pricing.dedup_turns_by_request_id`
    - `_price_turn` → `pricing.`
    - `_content_text`, `_fam`, `_recent_assistant_text`,
      `_recent_tool_trail`, `_format_samples_as_markdown`, and
      `_RECENT_LOOKBACK_N` → `render.`
    - `_parse_ts` → `corpus.`
  - **Do not prefix `_context_at_turn` (:2714).** Let ruff F821 drive
    every prefix. Never run a regex over bare names.

**Create: tests.** Each test file follows these rules:
- A one-line docstring (prescribed below).
- The loader and its `sys.modules` comment from
  `test_transcript_cache_rebuild.py:22-29`.
- Imports from `._audit_routing_helpers` and `.conftest` only. Never use
  a bare sibling import (`.claude/rules/test-tree-packaging.md`).
- Apply M7(a) and change nothing else on any line.
- Drop the three `# audit-routing*` section headers.
- Assemble ranges in source order.

The files:
- **`tests/_audit_routing_helpers.py`** takes :3506–3518, :7496–7506,
  :7509–7531, and :7943–7959 verbatim. It needs no imports beyond `from
  __future__ import annotations`. Docstring: `"""Test helpers shared by
  the audit-routing family's test files (test_transcript_audit_routing*.py)
  and by test_transcript_analysis.py's cross-subcommand tables."""`
- **`tests/test_transcript_audit_routing.py`** takes :3521–3531,
  :3534–3840, and :12958–12979. Docstring: `"""Tests for
  transcript_analysis/audit_routing.py (cmd_audit_routing): per-turn Opus
  routing-class breakdown, judgment spans, and --redact."""`
- **`tests/test_transcript_audit_routing_shape.py`** takes :7488–7493,
  :7534–7608, and :7611–7935. Docstring: `"""Tests for
  transcript_analysis/audit_routing.py (cmd_audit_routing_shape): D1/D2/D3
  code-read turn-shape distributions."""`
- **`tests/test_transcript_audit_routing_samples.py`** takes
  :7962–8555. Docstring: `"""Tests for transcript_analysis/audit_routing.py
  (cmd_audit_routing_samples): seeded code-read turn samples, JSON and
  markdown."""`

**Modify:**
- **`claude/.claude/scripts/transcript-analysis.py`**
  - Delete :1097–1106, :2563–2820, and :5372–5871. Each deletion leaves
    exactly two blank lines at its seam.
  - Make the M3 import changes. The by-name block carries the comment
    `# All three names below are read bare by this file's own
    still-monolithic build_parser` / `# (each audit-routing subcommand's
    own set_defaults).`
  - `build_parser()` and every surviving `cmd_*` stay unchanged.
  - If ruff F401 flags an import whose name a test reads as
    `_mod.<name>`, keep it with `# noqa: F401 -- read only via
    _mod.<name> from test files` rather than dropping it.
- **`claude/.claude/scripts/tests/test_transcript_analysis.py`**
  - Delete :3430–3451, :3498–3842, :7478–8557, and :12958–12981. The
    context-distribution helpers at :3452–3495 stay where they are.
  - Add `_exit_plan_mode`, `_priced_opus`, `_read_use`, and
    `_thinking_block` to the `.conftest` import (:21–50).
  - Add `from ._audit_routing_helpers import _audit_routing_samples_args,
    _audit_routing_shape_args`.
  - Let ruff F401 prune any imports this leaves unused.
  - `_UNCONDITIONAL_HEADER_CASES`, `TestAllSubcommandsSingleRootHeader`,
    `TestRootsThreadingSpy`, and `TestMultiRootFormatOutliers` stay
    byte-identical.
- **`claude/.claude/scripts/tests/conftest.py`**
  - Insert `_priced_opus` (byte-identical to legacy :3435–3449) directly
    after `_opus` (:418–437).
  - Insert `_read_use`, `_exit_plan_mode`, and `_thinking_block`
    (byte-identical to :7483–7485, :3498–3499, and :3502–3503) directly
    after `_skill_use` (:815–816).
  - Append the three new test files to the docstring's consumer list
    (:4–18). The helper module imports nothing from conftest, so it is
    not listed.
- **`claude/.claude/scripts/tests/test_transcript_cli_bootstrap.py`**
  (six tests, one help + one subprocess test per subcommand, matching
  the file's own turn-shape-family precedent at :62–130 — see M10):
  - Add `test_transcript_analysis_audit_routing_help_exits_zero`. It
    asserts exit 0 and that `--redact` appears in stdout.
  - Add
    `test_transcript_analysis_audit_routing_subprocess_finds_seeded_turn`,
    following the turn-shape pattern at :62–83. It seeds one
    `claude-opus-5` assistant record with a Read `tool_use` and
    `output_tokens: 4321`, then runs `_run("transcript-analysis.py",
    "--config-dir", str(config_dir), "audit-routing")`. It asserts exit
    0, `Corpus aggregate`, and `4,321` in stdout.
  - Add `test_transcript_analysis_audit_routing_shape_help_exits_zero`.
    It asserts exit 0 and that `--since` appears in stdout.
  - Add
    `test_transcript_analysis_audit_routing_shape_subprocess_finds_seeded_session`,
    seeding the same minimal session shape as
    `test_transcript_analysis_turn_shape_subprocess_finds_seeded_session`
    (:63–83). It runs `_run("transcript-analysis.py", "--config-dir",
    str(config_dir), "audit-routing-shape")` and asserts exit 0 and that
    `"Opus code-read turn-shape distributions"` appears in stdout — the
    header `cmd_audit_routing_shape` prints unconditionally (shim,
    inside the moved span), before any D1/D2/D3 bucket is populated, so
    this needs no turn matching the D1/D2/D3 classifiers to prove the
    subprocess bootstrap resolved.
  - Add `test_transcript_analysis_audit_routing_samples_help_exits_zero`.
    It asserts exit 0 and that `--seed` appears in stdout.
  - Add
    `test_transcript_analysis_audit_routing_samples_subprocess_finds_seeded_turn`,
    seeding the same session as the `audit-routing` subprocess test
    above (a `claude-opus-5` assistant record with a Read `tool_use`). It
    runs `_run("transcript-analysis.py", "--config-dir",
    str(config_dir), "audit-routing-samples")` and asserts exit 0 and
    that `result.stdout` parses as valid JSON (`json.loads(result.stdout)`
    does not raise) — proving the subprocess bootstrap resolves the
    package and produces this subcommand's JSON stream, independent of
    whether the minimal seed happens to produce a non-empty
    `candidates` list.
- **`docs/transcript-analysis-architecture.md`**
  - In :16–17, add `audit_routing.py` to the list of modules the shim
    imports back into.
  - After :35, add: "`build_parser()` likewise wires up
    `audit_routing.py`'s `cmd_audit_routing`, `cmd_audit_routing_shape`,
    and `cmd_audit_routing_samples` from the shim."
  - After the `cache_rebuild.py` section, add a `### \`audit_routing.py\``
    section. It says that the module:
    - holds the audit-routing command family (the three `cmd_*`
      functions and every helper only they use);
    - imports `corpus`, `pricing`, `redaction`, `render`, and `scope` all
      by module, matching `cost.py`'s convention;
    - has three names reached bare from the shim: the three `cmd_*`
      functions.
  - In Tests, add `_exit_plan_mode`, `_priced_opus`, `_read_use`, and
    `_thinking_block` to conftest's shared list (:317–319).
  - After the cache-rebuild paragraph, add a paragraph covering three
    points:
    - It names the three per-subcommand files and
      `tests/_audit_routing_helpers.py`, noting that the helper module is
      a plain module whose consumers import it relatively.
    - Each file keeps its own family-only helpers local.
    - `TestMultiRootFormatOutliers` stays in the legacy file, because it
      spans cost, audit-routing-samples, and judgment-pair.
- **`claude/.claude/scripts/transcript_analysis/__init__.py`**: docstring
  only. Add `audit_routing` to the command-group modules list.

**Explicitly unchanged:**
- `select-tests.py` and `test_select_tests.py`;
- `test_transcript_cost.py`;
- `docs/transcript-analysis.md` and the transcript-analysis `SKILL.md`;
- every existing package module;
- `build_parser()`.

**Reuse:**
- `read_scope.py:1-16` for the single-module header shape.
- `test_transcript_cache_rebuild.py:1-29` for the loader and the
  relative-import shape.
- `tests/_cache_rebuild_helpers.py` for the helper-module shape.
- conftest's record builders.
- `test_transcript_cli_bootstrap.py`'s `_run` and the turn-shape seeded
  pattern.

**Dispatch.** One `code-writer` dispatch covers every file above (M8).
- **Stage 1.**
  - Create `audit_routing.py`.
  - Delete the shim spans.
  - Add `audit_routing` to the module-import list.
  - Temporarily import all 16 moved top-level names into the shim by
    name. This block is lint-dirty by design.
  - Run the scoped suite. It must be green before Stage 2.
- **Stage 2.**
  - Create the helper module and the three test files, and apply M7.
  - Make the legacy, conftest, bootstrap, doc, and `__init__` edits.
  - Replace the temporary re-export with M3's final imports.
- **Abort procedure.** If the dispatch is interrupted between the
  stages, discard the working tree and restart from Stage 1. No partial
  commit exists to resume from.
- Extract moved code by line range from unmodified scratch copies. Never
  retype it.
- The PR body states:
  - M4's helper-module-versus-conftest rationale;
  - M5's conftest growth;
  - the reflow count;
  - measured file sizes;
  - `git blame -C -C -s` counts;
  - Stage 1's gate result (for example, "Stage 1 gate: N passed");
  - Verification step 9's (move-fidelity) pass/fail result explicitly
    (for example, "Step 9 AST move-fidelity: production and test spans
    match byte-for-byte modulo the rename map") — step 9 is a one-off
    scratch script, not a committed regression test, so its result is
    otherwise unrecoverable from the merged diff alone.
- Verify with steps 0–12 below.

## Verification

Run everything from the worktree root. `<venv>` is the worktree-relative
`.venv` from README.md's Tests section. Scratch files live outside the
repo, and the corpus is synthetic.

0. **Baseline, before the dispatch.**
   - Copy these unmodified files to scratch: the shim, the legacy test
     file, conftest, `test_transcript_cli_bootstrap.py`, the architecture
     doc, and `__init__.py`.
   - Save the `<venv>/bin/pytest --collect-only -q
     claude/.claude/scripts/tests/test_transcript_analysis.py` IDs.
   - Capture top-level `--help` plus `--help` for each of the three
     subcommands.
   - Seed two synthetic accounts. Across them, include:
     - Opus turns of every class;
     - a Skill judgment-span opener;
     - a plan-mode user message plus `ExitPlanMode`;
     - a requestId group split across records;
     - a Sonnet turn;
     - one priced (`claude-opus-5`) and one unpriced Opus model;
     - multi-file Read turns;
     - in-window and out-of-window timestamps;
     - two projects.
   - Pin `CLAUDE_CONFIG_DIR` and `TRANSCRIPT_CONFIG_DIRS_FILE` the way
     `_isolated_config_env` does. Capture stdout, stderr, and the exit
     code for each run:
     - (a) `--config-dir <acct1> audit-routing`
     - (b) `audit-routing --redact` across both declared roots
     - (c) `--config-dir <acct1> audit-routing --since 30d --top 1`
     - (d) `audit-routing-shape`
     - (e) `audit-routing-samples --seed 7 --sample 5`
     - (f) `audit-routing-samples --seed 7 --format md`
     - (g) `audit-routing --since not-a-window`, which should exit
       nonzero.
   - Record `wc -l` for the shim, the legacy test file, and conftest.
1. **Scoped suite.** Run `<venv>/bin/python3
   claude/.claude/scripts/select-tests.py`. It selects the targets in row
   24 plus `test_select_tests.py`. Every selected test passes, including
   the architecture-doc drift test and
   `TestNoBareSameDirectorySiblingImports`.
2. **Test-ID parity.** Strip each ID's file prefix and compare the sorted
   lists. Step 0's list must equal the combined post-move lists of the
   legacy file and the three new files, duplicates included.
3. **CLI parity.** Every `--help` capture and every run (a)–(g) must be
   byte-identical to step 0's.
4. **Spy reach.** In scratch, delete `TestRootsThreadingSpy`'s two
   `_mod.scope` `setattr` lines (:12555–12556), then run
   `<venv>/bin/pytest claude/.claude/scripts/tests/test_transcript_analysis.py -k "TestRootsThreadingSpy"`.
   **Before this phase's move**, 2 of the class's 23 parametrized cases
   already fail this way (`cost` and `cost-trend`, whose `cmd_*` already
   call `scope.*` by module attribute from an earlier phase — comment at
   :12552–12554). **After the move**, the 3 audit-routing cases join
   them, so the expected failure count is **5**, not 3 — confirm exactly
   5 failures, and use `-k "TestRootsThreadingSpy and audit-routing"` to
   isolate the 3 new ones specifically (verified: no other entry in
   `_UNCONDITIONAL_HEADER_CASES` contains "audit-routing" as a
   substring). Then restore the two deleted lines and confirm the class
   is fully green again.
5. **Hook sandbox.** Confirm step 1 ran
   `claude/.claude/hooks/tests/test_nudge_error_mode_analysis.py`. If it
   did not, run that file alone.
6. **Lint.** Run `<venv>/bin/ruff check claude/.claude/scripts/`.
7. **Leftovers and single home.**
   - `git grep -nE '^(def |class )?(AUDIT_JUDGMENT_SKILLS|_AUDIT_CLASSES|_ORCHESTRATION_TOOLS|_CODE_READ_TOOLS|_classify_opus_turn|cmd_audit_routing|cmd_audit_routing_shape|cmd_audit_routing_samples|_count_read_file_paths|_d1_bucket|_d2_bucket|_D1_BUCKETS|_D2_BUCKETS|_D3_CASES|_D1_DISPATCHABLE_BUCKETS|_D3_DISPATCHABLE_CASES)\b'
     claude/.claude/scripts/transcript-analysis.py` must return nothing.
   - `git grep -nE '^def (_priced_opus|_exit_plan_mode|_thinking_block|_read_use|_grep_use|_glob_use|_extract_corpus_class_tokens|_extract_sonnet_tier_dollar_estimate|_audit_routing_shape_args|_audit_routing_samples_args|_extract_shape_d1|_extract_shape_d2|_extract_shape_d3|_extract_shape_d3_xtab)\('
     claude/.claude/scripts/tests/` must return exactly 14 lines, each at
     its planned home.
8. **Prefix correctness.** Run a scratch `ast` script over
   `audit_routing.py` and the three test files.
   - In production code, every `Attribute(Name(<package module>),
     <name>)` must name something that module defines at top level.
   - No such attribute may appear in `Store` context. This catches
     `pricing._context_at_turn`.
   - `audit_routing.py` has no `from transcript_analysis.<module>
     import` line.
   - In test code, every `_mod.audit_routing.<name>` must name something
     `audit_routing.py` defines.
9. **Move fidelity.**
   - **Production.** Strip the rename-map prefixes, but only where each
     is followed by a mapped name. Map the three aliases back. Each
     top-level node's `ast.dump` must equal the step 0 spans (:1097–1104,
     :2563–2818, :5372–5869). The `#` comment-line diff must be empty.
   - **Tests.** Rewrite `_mod.audit_routing.` back to `_mod.`. Each
     top-level node's AST must equal its step 0 slice, and the
     comment-line diff must be empty. The four conftest builders and the
     four helper-module functions must match their legacy lines byte for
     byte.
   - After the commit, run `git blame -C -C -s` on every new file and
     report the counts.
10. **Sizes.** Report measured `wc -l` for every new and shrunk file. A
    file over 1,000 lines is flagged in the PR body. conftest will be
    one; it is not split here.
11. **This phase's own revert.** In a throwaway worktree, run `git
    revert --no-commit` on the squashed commit.
    - The diff must be zero against step 0's snapshot of all six
      modified files.
    - None of the five created files may remain.
12. **Cross-phase revert rehearsal, empirically checked rather than
    argued.** Row 31 argues (not verifies) that this phase's
    append-only conftest promotion can't break an earlier phase's
    revert, since it only adds names and deletes none. Convert that
    argument to a fact: in a throwaway worktree with this phase's
    commit applied on top, `git revert --no-commit` the merged
    cache-rebuild commit (`23b47058`) and confirm the full scoped suite
    still collects and passes. This is the same rehearsal the governing
    plan defaults to requiring for a conftest promotion (transcript-analysis-decomposition.md:276-279)
    — row 31's structural argument is sound, but a couple of minutes of
    empirical confirmation closes the "argued, not verified" gap rather
    than leaving it as an assumption.

## Out of scope

- **Deduplicating the three judgment-span state machines.** The
  duplication is deliberate: see the docstrings at :5430–5432 and
  :5691–5692. The cross-validation tests depend on it.
- **Consolidating `AUDIT_JUDGMENT_SKILLS` with `REVIEW_TRACE_SKILLS`/
  `REVIEW_SKILLS`.** That would change behavior, and the
  code-file-size-splits plan already declined it (:668).
- **Moving `_audit_routing_args` out of conftest.** The move would edit
  `test_transcript_cost.py`'s import for no functional gain.
- **Changing the shim-plus-package shape, moving `build_parser()` early,
  or relocating `_UNCONDITIONAL_HEADER_CASES`, its two classes, or
  `TestMultiRootFormatOutliers`.** Each is within reach and each is
  declined here. The governing plan defers them to the `cli.py` phase
  (:144–161).
- **Adding a multi-root disclosure guard or banner to audit-routing.**
  Its `--redact` is opt-in, and it has no refusal of the governing plan's
  assumption-20 kind. Adding one changes behavior.
- **conftest.py's size (1,016 → about 1,048).** F1 owns it.
- **Relocating the context-distribution helpers at legacy :3452–3495.**
  They lose the `# audit-routing` header they sat under. The
  context-distribution phase moves them.
- **`_priced_opus`'s docstring.** It claims audit-routing-only use, but
  cost-ledger at :6500 also uses it. The move is byte-identical.
- **The architecture doc's back-import list omitting `author_outcome.py`.**
  The shim imports two names from it at :58. This is a pre-existing
  omission in the sentence this phase edits, so I'm raising it for the
  reviewer rather than fixing it here.
- **`TestRootsThreadingSpy`'s comment (:12552–12554).** It names only
  cost and cost-trend.
- **Stale shim line citations in dated plans and reports.**
- **Renaming moved names or output strings, or any other CLI surface
  change** (Verification step 3).
