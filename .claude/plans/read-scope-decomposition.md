# Extract read-scope into transcript_analysis/read_scope.py

## Context

**Extract the read-scope command group (`cmd_read_scope` and its helpers,
`claude/.claude/scripts/transcript-analysis.py:3353-3919`) out of the
monolithic `transcript-analysis.py` CLI script into a new
`transcript_analysis/read_scope.py` module, following the shim-plus-package
pattern PR #1125 established for review-trace (`denials.py`/`review_trace.py`,
merged as commit `f21d4132`).**

This is child A1 of epic #1113's transcript-analysis decomposition (tracking
issue #1116 — task list: review-trace done, "read-scope (child A1)" is the
next unchecked item), and it is also epic child #1114's own A-field
prerequisite A1: A-field's A2 extends `read_scope.py` with a reviewer-subagent
split, size buckets, and PARTIAL-view counts, so moving read-scope out of the
monolith now keeps A2's additions off the shrinking shim rather than adding to
it. The two epic tracks share this one PR rather than duplicating the
extraction. Intended outcome: read-scope's 47 tests relocate into a new
`test_transcript_read_scope.py`, the shim's CLI wiring (`build_parser()`)
stays in `transcript-analysis.py` unchanged per the review-trace precedent,
and both new files land under the epic's 1,000-line limit.

## Approach

Move read-scope's production code (shim :3353–3919) into a new command-group
module, `transcript_analysis/read_scope.py`. Move its test slice (legacy
:5209–5933) into `tests/test_transcript_read_scope.py`. Both moves copy code
verbatim; the only edits are module prefixes and test re-points. This follows
the review-trace phase's shim-plus-package pattern. The shim keeps
`build_parser()`. It imports `cmd_read_scope` by name for `build_parser()` and
`_READ_SCOPE_CHARS_PER_TOKEN` by name for context-composition, the one
still-monolithic consumer.

Alternatives considered and set aside:
- **Rewriting context-composition's six reads to `read_scope._READ_SCOPE_CHARS_PER_TOKEN`.** The attribute-access rule covers reassignable globals, and this constant is not one. That rewrite would also break a test in a file this phase otherwise never touches (M3).
- **A leaf-plus-command split like denials/review_trace.** That split existed because the pure classifiers had other consumers. Here the only outside consumer is one constant (M1).

### Assumption ledger

**Root:** read-scope's ~570 lines still live in the monolithic shim. The decomposition's tracking issue lists read-scope as the next phase. A-field's A2 extends read-scope, so moving it first keeps A2's additions out of the monolith. `[verified: .claude/plans/code-file-size-splits.md:135 — "A1 extracts read-scope into transcript_analysis/read_scope.py ... so the monolith doesn't grow"]`

**Givens:**
- G1. pytest's `prepend` import mode imports each test module by basename, which makes test-file basenames a global namespace. New test files therefore take the `test_transcript_*` prefix. Reason: pytest owns this behavior. `[verified: .claude/plans/transcript-analysis-decomposition.md:59-63, citing the pytest docs]`
- G2. A directly invoked `python3 transcript-analysis.py` finds the package only through CPython's `sys.path[0]`. No in-process `_mod.cmd_*` test exercises that path. Reason: CPython owns script bootstrap. `[verified: tests/test_transcript_cli_bootstrap.py:1-11]`
Three conditions that might read as givens are **not** — this repo owns
`transcript-analysis-decomposition.md` and could amend it, so each is a
deliberate decline, not an external fact. They live in **Out of scope** with
their own reasons: the shim-plus-package shape, `build_parser()` staying in
the shim until the final `cli.py` phase, and `_UNCONDITIONAL_HEADER_CASES`
staying in the legacy test file until then.

**Mechanisms:**
- **M1 — One command-group module, `read_scope.py`, holding shim :3353–3919, moved by line-range extraction.** `anchors: root, row1, row19`
  - Two lighter options fail.
    - Leaving the code in the shim does not address the root.
    - Folding it into an existing leaf (`corpus.py`, `pricing.py`) would put a `cmd_*` and a `scope` dependency into a module whose defining property is having neither (governing plan Phase 1).
  - The heavier leaf-plus-command split is also rejected. Review-trace split because its pure classifiers had outside consumers (code-file-size-splits.md M6). Here the only outside consumer is one constant (row 4).
- **M2 — `read_scope.py` reaches every name from another module by attribute access**, using `from transcript_analysis import corpus, pricing, render, scope`. `anchors: row2, row12, row13, row20, row21`
  - The lighter option is by-name import, which `redaction.py` uses for `_sanitize_table_cell`. It fails here on three names that tests patch or spy on through the module attribute:
    - `scope._resolve_project_scope` (row 12);
    - `scope.print_resolved_scope` (row 12);
    - `scope.PROJECTS_DIR` (row 13).
  - A per-name mix of the two styles adds a judgment call that cost, reviewer_yield, and review_trace never make.
- **M3 — The shim imports `cmd_read_scope` and `_READ_SCOPE_CHARS_PER_TOKEN` by name, and adds `read_scope` to its module-import list** for the tests' `_mod.read_scope.<name>` channel. `anchors: row4, row5, row6, row7`
  - Rejected: rewriting context-composition's six reads to `read_scope._READ_SCOPE_CHARS_PER_TOKEN`.
    - The attribute-access rule exists for reassignable globals, and this constant is never reassigned or patched (row 6).
    - Every other still-monolithic consumer of a moved name in the shim imports it by name (row 7).
    - It would break `test_context_composition.py:800`'s `_mod._READ_SCOPE_CHARS_PER_TOKEN` read (row 5).
  - Rejected: giving context-composition its own chars-per-token pin. It reverses a deliberate earlier reuse (row 8), so it is a design change, not a move (Out of scope).
- **M4 — The tests move to `test_transcript_read_scope.py`.** The file uses the loader from `test_transcript_review_trace.py:1-31`, and the tests reach moved names as `_mod.read_scope.<name>`. `anchors: row9, row11`
  - Rejected: keeping `_mod.<name>` through new `noqa: F401` shim re-exports. The final `cli.py` phase would have to unwind them (code-file-size-splits.md:330).
- **M5 — `_compact_boundary_rec` moves to `conftest.py`, byte-identical.** `anchors: row10`
  - Rejected: keeping two copies. Record builders are not a DAMP exception, and conftest already hosts that family (code-file-size-splits.md M9).
  - Rejected: having the new file import from the legacy test module. That couples two test modules and runs the legacy file's loader a second time.
- **M6 — Two real-subprocess bootstrap tests for read-scope:** `--help`, and a run against a seeded account. The seeded run pins the environment with `_isolated_config_env`, as the cost tests do, and does not pass a top-level `--config-dir` as the review-trace tests do. This covers the bootstrap path G2 describes. `anchors: root, row16`
- **M7 — No `select-tests.py` or `test_select_tests.py` change.** `anchors: row14, row15`
- **M8 — One `code-writer` dispatch covers every file.** `anchors: row1, row9`
  - Rejected: separate production and test dispatches. Both halves need the same coupling map (rows 2, 4, 5, 12), and they must land green together.

**Assumptions:**

1. Read-scope's production code is shim :3353–3919. It starts with the constant's rationale comment (:3353–3355) and ends with `_print_read_scope_report` (:3919). :3922 onward is instrument-authoring, which has its own `# ----` header. The earlier exploration's ":3356–4216" included instrument-authoring. `[verified: Read of shim :3340-4224]`
2. The group's dependencies on other modules are exactly these. The earlier exploration missed `_parse_ts`, `_context_at_turn`, and `_pct_of`. `[verified: Read of the range; shim import block :52-198]`
   - `scope`:
     - `PROJECTS_DIR` :3745
     - `_resolve_cost_roots` :3722
     - `_DO_NOT_PUBLISH_BANNER` :3760–3761
     - `_parse_since_nd_arg` :3763
     - `_resolve_project_scope` :3766
     - `print_resolved_scope`, called through the shim alias `_print_resolved_scope` :3767
     - `_root_index_for_path` :3784
   - `corpus`:
     - `_parse_ts` :3523
     - `_read_session_file_partitioned` :3780
   - `pricing`: `_context_at_turn` :3518
   - `render`: `_pct_of`, from :3819 on
3. Every name in row 2 is still used elsewhere in the shim after the move, so no shim import becomes unused. `[verified: grep — e.g. :862, :866, :869, :910, :4244, :4247, :4532, :4656; _pct_of/_parse_ts have 63 call sites file-wide]`
4. Two places outside the range execute read-scope names:
   - `_classify_content_item` (context-composition) reads `_READ_SCOPE_CHARS_PER_TOKEN` at :4340, :4342, :4345, :4348, :4353, and :4360.
   - `build_parser()` calls `p_read_scope.set_defaults(func=cmd_read_scope)` at :12796.

   Every other mention names a symbol or subcommand that still exists after the move, so each stays accurate: shim :4220, :4235, :4655, :4803–4813, :4900–4923, :4965, :5674; `corpus.py:101`; `pricing.py:209`; `scope.py:521`. `[verified: grep]`
5. `test_context_composition.py:800` reads `_mod._READ_SCOPE_CHARS_PER_TOKEN` through its own copy of the shim. `[verified: grep]`
6. `_READ_SCOPE_CHARS_PER_TOKEN` is never reassigned, marked `global`, or monkeypatched. `[verified: grep of _READ_SCOPE_ across claude/.claude/scripts/ — the definition plus reads only]`
7. The shim imports every moved name its own still-monolithic code uses by name: `denials` :81–87, `review_rounds` :142–149, `review_trace` :150–156, `reviewer_yield` :157–178. Its only attribute read of a sibling-module name is `scope.PROJECTS_DIR`, which is reassignable. `[verified: shim :37-198]`
8. Context-composition reuses read-scope's constant deliberately: its plan lists it among the existing symbols it reuses. `[verified: .claude/plans/context-composition-analyzer.md:213]`
9. The test slice is legacy :5209–5933. It has 47 `def test_` methods and no `parametrize`. :5936 onward is instrument-authoring. The earlier exploration's ":5214–5977" and "~50–55 tests" were both wrong. `[verified: grep of test defs/parametrize filtered to the range; Read of :5925-5994]`
   - Section header :5209–5211.
   - Ten helpers :5214–5292.
   - `TestReadScope` :5295–5501.
   - `TestScanReadScopeSession` :5503–5933.
10. Of the ten slice helpers, only `_compact_boundary_rec` is used outside the slice: by the cache-efficiency test at legacy :6434. Conftest defines none of the ten. The slice calls none of the 30 helpers the legacy file defines above :5209. `[verified: grep]`
11. `_UNCONDITIONAL_HEADER_CASES`'s read-scope row (legacy :17225–17226) builds its args with an inline lambda, not `_read_scope_args`. It calls `_mod.cmd_read_scope`, which the shim keeps exporting (M3). `[verified: Read]`
12. `TestRootsThreadingSpy` spies on both `_mod._resolve_project_scope`/`_mod._print_resolved_scope` and `_mod.scope._resolve_project_scope`/`_mod.scope.print_resolved_scope`. `[verified: legacy :17439-17457]` A `read_scope.py` that imported either name from `scope` by name would bind it at import time, bypass the spy, and fail that row. That follows from the governing plan's row 1.
13. `fake_projects` patches `scope.PROJECTS_DIR` and `scope.config_dir` on `request.module._mod`. It therefore works in any test module that defines `_mod` at module level. `TestReadScope`'s `roots=None` calls pass only if `read_scope.py` reads `scope.PROJECTS_DIR` by attribute. `[verified: conftest.py:698-725]`
14. No slice test does any of the following: `setattr` on `_mod`, a hook or SKILL.md read by path, or a subprocess call. `[verified: grep of setattr(_mod,, HOOKS_DIR, SKILL.md, subprocess. — no hit in :5209-5933]`
15. Test selection already covers the new file. `[verified: select-tests.py:65-73, :381, :486; test_transcript_review_trace.py:24]`
    - Any path under `claude/.claude/scripts/` selects all of `scripts/tests/` (:381).
    - `TRANSCRIPT_DENIALS_TEST_PATH` exists only because that file reads hooks by path (:65–73).
    - `test_transcript_review_trace.py` has the same module-level `_SCRIPT` constant shape (:24) and no hook reads, and it has no entry.
16. read-scope is a member of `_SUBCOMMANDS_REFUSING_TOP_LEVEL_CONFIG_DIR`, so `main()` refuses a top-level `--config-dir` for it. A subprocess bootstrap test must pin `CLAUDE_CONFIG_DIR` and `TRANSCRIPT_CONFIG_DIRS_FILE` instead. `[verified: scope.py:520-526; test_transcript_cli_bootstrap.py:198-210]`
17. The drift test requires a `### `read_scope.py`` heading with nothing else on the line. `[verified: test_transcript_analysis_architecture_doc.py:14-26]`
18. Neither sibling script (`token-analyzer.py`, `analyze-context.py`) nor any hook references read-scope. `[verified: grep over claude/.claude/scripts/ including both sibling scripts; repo-wide grep found no claude/.claude/hooks/ hit]`
19. The new files come in under the engineer's 1,000-line limit, which applies to production and test files alike. `[verified: arithmetic over rows 1 and 9; limit recorded at .claude/plans/code-file-size-splits.md:549-550]`
    - `read_scope.py` is 567 moved lines plus a header.
    - `test_transcript_read_scope.py` is about 721 moved lines plus its loader and imports.
20. The moved code has no local variable named `corpus`, `pricing`, `render`, or `scope`, so no local can shadow the new module prefixes. The review-trace phase hit exactly that shadowing with `denials`. `[verified: Read of the range — locals are scope_label, thread_scope, scan_roots, resolved_scan_roots]`
21. `_print_resolved_scope` is a shim-local alias of `scope.print_resolved_scope` (shim :197). Ruff's F821 will flag the bare name in the moved code, but it will not say what the name should become. The moved call must be `scope.print_resolved_scope`. `[verified: shim :197]`

## Critical files

**Create:**
- `claude/.claude/scripts/transcript_analysis/read_scope.py` takes shim :3353–3919.
  - Module docstring, verbatim:

    ```
    The read-scope command family: cmd_read_scope and every helper used only by it -- the per-session Read-call census, repeat-whole-file-read detection, and per-file-and-sessionId prompt-token growth.

    Imports corpus, pricing, render, and scope by module (attribute access, not by name) -- see scope.py's own top-of-file comment for why.
    ```

  - Imports: `argparse`, `sys`, `Counter` and `defaultdict` from `collections`, `Sequence` from `collections.abc`, `Path`, and `from transcript_analysis import corpus, pricing, render, scope`. Include `from __future__ import annotations`, matching `review_trace.py:11`.
  - Rename map for the moved code:
    - `_resolve_cost_roots` → `scope._resolve_cost_roots`
    - `_DO_NOT_PUBLISH_BANNER` → `scope._DO_NOT_PUBLISH_BANNER`
    - `_parse_since_nd_arg` → `scope._parse_since_nd_arg`
    - `_resolve_project_scope` → `scope._resolve_project_scope`
    - `_print_resolved_scope` → `scope.print_resolved_scope` (row 21)
    - `_root_index_for_path` → `scope._root_index_for_path`
    - `_parse_ts` → `corpus._parse_ts`
    - `_read_session_file_partitioned` → `corpus._read_session_file_partitioned`
    - `_context_at_turn` → `pricing._context_at_turn`
    - `_pct_of` → `render._pct_of`
    - `scope.PROJECTS_DIR` is already qualified and stays as is.
- `claude/.claude/scripts/tests/test_transcript_read_scope.py` takes legacy :5209–5933 minus `_compact_boundary_rec` (:5259–5260).
  - Docstring: `"""Tests for transcript_analysis/read_scope.py (cmd_read_scope)."""`
  - Loader and its three-line comment copied from `test_transcript_review_trace.py:24-31`.
  - Imports `re` and `pytest`, plus a `from .conftest import (...)` list: `_asst`, `_compact_boundary_rec`, `_opus`, `_tool_result`, `_user_msg`, `_write_cost_root`, `_write_jsonl`, `_write_subagent_jsonl`, and whatever else ruff's F821 reports.
  - Re-point moved names from `_mod.X` to `_mod.read_scope.X`:
    - `_read_scope_report`
    - `cmd_read_scope`
    - `_scan_read_scope_session`
    - `_read_scope_cohort_bucket_token_total`
    - every `_READ_SCOPE_*` constant
  - Leave `_mod.scope`, `_mod._DO_NOT_PUBLISH_BANNER`, and `_mod._parse_ts` as they are. They are not moved names.

**Modify:**
- `claude/.claude/scripts/transcript-analysis.py`
  - Delete :3353–3919 and one of the two blank-line pairs around it, keeping two blank lines between edit-format and the instrument-authoring header.
  - Add `read_scope` to the `from transcript_analysis import (...)` list at :41–51, in the order ruff's `I` rule requires. Add it to the module list in the comment at :37–40.
  - Add this by-name import block:

    ```python
    from transcript_analysis.read_scope import (
        # Both names below are read bare by this file's own still-monolithic code:
        #   _READ_SCOPE_CHARS_PER_TOKEN -> _classify_content_item
        #   cmd_read_scope              -> p_read_scope.set_defaults
        _READ_SCOPE_CHARS_PER_TOKEN,
        cmd_read_scope,
    )
    ```

  - Leave these unchanged:
    - `build_parser()`'s read-scope block (:12765–12796);
    - `_classify_content_item` (:4330–4361);
    - every narrative mention listed in row 4.
- `claude/.claude/scripts/tests/test_transcript_analysis.py`
  - Delete :5209–5933 and one adjacent blank-line pair.
  - Add `_compact_boundary_rec` to the `.conftest` import list at :22–47.
  - Leave `_mod.cmd_read_scope` at :17225 unchanged (row 11).
- `claude/.claude/scripts/tests/conftest.py`
  - Add `_compact_boundary_rec`, byte-identical to legacy :5259–5260, beside the other record builders (after `_tool_result`, :334).
  - Add `test_transcript_read_scope.py` to the docstring's consumer list at :4–8.
- `claude/.claude/scripts/tests/test_transcript_cli_bootstrap.py` gets two tests and one seed helper. The seeded test follows the cost pair's shape at :213–222 and the review-trace docstring wording at :389–392.
  - `test_transcript_analysis_read_scope_help_exits_zero` asserts exit 0 and `--since` in stdout.
  - `_seed_read_scope_account(tmp_path)` builds one main-thread `Read` tool_use record and a string tool_result record.
  - `test_transcript_analysis_read_scope_subprocess_finds_seeded_read` runs `_run("transcript-analysis.py", "read-scope", env=_isolated_config_env(config_dir, tmp_path))` and asserts exit 0 and `Read calls: 1` in stdout.
- `docs/transcript-analysis-architecture.md`
  - In the exception paragraph at :14–25:
    - Add `read_scope.py` to "the only modules the shim imports back into".
    - Add one sentence: `build_parser()` wires up `read_scope.py`'s `cmd_read_scope`, and the still-unmigrated context-composition code reads `read_scope.py`'s `_READ_SCOPE_CHARS_PER_TOKEN` by name from the shim.
  - Add a `### `read_scope.py`` section after `review_trace.py`'s, in the same shape:
    - What it owns: the Read-call census by cohort and scope (`_scan_read_scope_session`), repeat-whole-file-read detection, and per-file-and-sessionId prompt-token growth.
    - That it imports `corpus`, `pricing`, `render`, and `scope` by module.
    - That `cmd_read_scope` and `_READ_SCOPE_CHARS_PER_TOKEN` are reached from the shim, per the exception paragraph above.
  - In the Tests section at :165–181:
    - Add "`read_scope.py`'s in `tests/test_transcript_read_scope.py`".
    - Add `_compact_boundary_rec` to the conftest shared-fixture list.

**Explicitly unchanged:** `select-tests.py`, `test_select_tests.py` (row 15), `test_context_composition.py` (M3), `token-analyzer.py`, `analyze-context.py` (row 18), and `corpus.py`, `pricing.py`, `scope.py` (row 4).

**Reuse:**
- `review_trace.py:1-17` for the docstring and the by-module import shape.
- `test_transcript_review_trace.py:1-31` for the loader and the relative conftest import.
- `test_transcript_cli_bootstrap.py`'s `_run` (:26) and `_isolated_config_env` (:198).
- conftest's record builders.

**Dispatch.** One `code-writer` dispatch (M8) covers every file above. Its instructions:
- Extract moved code by line range from unmodified copies, using a scratch script. Never retype moved code.
- Let ruff's F821 drive each module prefix, and apply row 21's rename by hand.
- Re-point test references mechanically, per the Create list. Change nothing else on an `assert` line.
- Verify with Verification steps 1 through 9.

## Verification

Run everything from the worktree root. `<venv>` means the worktree-relative `.venv` path in README.md's Tests section. Scratch outputs go outside the repo; print counts only.

0. **Baseline, before the dispatch.**
   - Copy the unmodified shim and legacy test file to scratch.
   - Save the collected IDs from `<venv>/bin/pytest --collect-only -q claude/.claude/scripts/tests/test_transcript_analysis.py`.
   - Capture `python3 claude/.claude/scripts/transcript-analysis.py [<sub>] --help` for the top level and every subcommand.
   - Build a synthetic scratch corpus, never committed:
     - two account dirs, each with a `projects/` subdirectory;
     - one main transcript containing a targeted Read, the same path read whole-file twice, a Grep with its result, and assistant turns with growing usage;
     - one subagent file under `<session>/subagents/`.
   - Capture stdout, stderr, and the exit code of `read-scope` for four cases, each with `CLAUDE_CONFIG_DIR` and `TRANSCRIPT_CONFIG_DIRS_FILE` pinned:
     - (a) the default run;
     - (b) `--since 9999d --no-redact`;
     - (c) `--config-dir <second account>`, the multi-root case;
     - (d) an empty `projects/`, the zero-match case.
   - Record `wc -l` for the shim and the legacy test file.
1. **Scoped suite.** `<venv>/bin/python3 claude/.claude/scripts/select-tests.py`. This diff touches `claude/.claude/scripts/`, which selects all of `scripts/tests/`. It also touches `docs/`, which selects `hooks/tests/` and `skills/tests/`. Every selected test passes, including the ones below.
   - `test_transcript_analysis_architecture_doc.py`, the doc-drift test.
   - `test_context_composition.py`, which pins M3.
   - `test_token_analyzer.py` and `test_analyze_context.py`, the sibling-script coverage.
2. **Test parity.** Strip the file prefix from each collected ID (keep `Class::test[param]`) and compare sorted lists. Step 0's legacy list must equal the combined post-move lists of `test_transcript_analysis.py` and `test_transcript_read_scope.py`, duplicates included. Exactly 47 IDs move (row 9). The two bootstrap tests are counted separately.
3. **CLI parity.** Every `--help` capture must be byte-identical to step 0's. All four read-scope captures must match step 0's byte for byte, on stdout, stderr, and exit code.
4. **Late binding and spies.** Among the legacy file's collected IDs, exactly two contain `read-scope`: `TestAllSubcommandsSingleRootHeader` and `TestRootsThreadingSpy`. Both passed in step 1. They pin M2's `scope` attribute access (row 12). `TestReadScope`'s `fake_projects` tests pin the `scope.PROJECTS_DIR` read (row 13).
5. **Hook sandbox.** Confirm step 1 ran `claude/.claude/hooks/tests/test_nudge_error_mode_analysis.py`, which runs the shim from a symlinked sandbox and so exercises its new import. If step 1 did not run it, run that file alone and record the gap in the PR body as a rule-table observation.
6. **Lint.** `<venv>/bin/ruff check claude/.claude/scripts/`
7. **Leftovers.**
   - Before the move, list every top-level `def` and `NAME =` binding in shim :3353–3919.
   - After the move, `git grep -nE '^(def )?(<names, |-joined>)\b' claude/.claude/scripts/transcript-analysis.py` must return nothing. The indented by-name import lines are excluded by the `^` anchor.
8. **Assertion preservation.**
   - In a scratch copy of the new test file, rewrite `_mod.read_scope.` back to `_mod.`.
   - Diff it against step 0's legacy :5209–5933. Only these may differ: the docstring, imports, loader, and the removed `_compact_boundary_rec`.
   - The conftest `_compact_boundary_rec` must equal legacy :5259–5260 byte for byte.
   - The legacy file may differ from step 0's copy only by the removed range and the one added import name.
9. **Rename-map fidelity for `read_scope.py`.**
   - In a scratch copy of the new module, reverse the 10 declared renames in the Critical files rename map (`scope._resolve_cost_roots` → `_resolve_cost_roots`, `scope._DO_NOT_PUBLISH_BANNER` → `_DO_NOT_PUBLISH_BANNER`, `scope._parse_since_nd_arg` → `_parse_since_nd_arg`, `scope._resolve_project_scope` → `_resolve_project_scope`, `scope.print_resolved_scope` → `_print_resolved_scope`, `scope._root_index_for_path` → `_root_index_for_path`, `corpus._parse_ts` → `_parse_ts`, `corpus._read_session_file_partitioned` → `_read_session_file_partitioned`, `pricing._context_at_turn` → `_context_at_turn`, `render._pct_of` → `_pct_of`).
   - Diff the result against the untouched shim :3353-3919. Only the module docstring, the `from __future__ import annotations` line, and the import block may differ — no other line may.
   - `git blame`/ruff's F821 alone cannot distinguish a rename to the correct target module from a rename to a wrong-but-existing one (e.g. `_parse_ts` mistakenly rewritten to `pricing._context_at_turn` — both resolve, so F821 stays silent). This scripted reverse-diff closes that gap the same way step 8 closes it for the test file.
10. **Move fidelity, after the commit.**
    - Run `git blame -C -C -s` on `read_scope.py` and `test_transcript_read_scope.py`.
    - Lines attributed to the new commit should be limited to headers, imports, module prefixes, and the `_mod.read_scope.` re-points.
    - Put the per-file counts in the PR body.
    - Name this command in the `/code-review` spawn prompts, together with a statement that the diff is a move meant to preserve behavior.
11. **Sizes.** Report measured `wc -l` for the two new files and the two shrunk files in the PR body. Do not estimate.

The governing plan's revert rehearsal (its step 7) exists to catch a conftest-fixture promotion in this phase leaving an earlier phase's revert uncollectable (`transcript-analysis-decomposition.md:276-279`). This phase's only conftest promotion is `_compact_boundary_rec` (assumption 10), whose sole outside-the-slice consumer (`TestCacheEfficiencyClassifier.test_compact_boundary_resets_the_chain`, legacy :6434) stays in `test_transcript_analysis.py` — the same file this phase already touches — so no adjacent phase's revert depends on this promotion. The rehearsal is omitted on that basis, the same reasoning shape the review-trace phase used for its own omission.

## Out of scope

- **A separate chars-per-token pin for context-composition.** It would decouple context-composition's calibration from read-scope's, which is what the comment at shim :3353–3355 recommends between reports. But context-composition's reuse was deliberate (row 8), so changing it is a design decision, not part of a move. Raise it when context-composition's own phase decides whether to import `read_scope` by module or take its own pin.
- **Changing the shim-plus-package shape, moving `build_parser()` out of the shim early, or relocating `_UNCONDITIONAL_HEADER_CASES` or its two classes now.** All three are reachable — this repo owns `transcript-analysis-decomposition.md` and could amend its phasing — but each is a deliberate decline, not a fact outside this plan's reach. The governing plan fixes the shim-plus-package shape for every phase; moving `build_parser()` early would force it to import back into the shim while commands still live there, an unstated circular-import composition pattern the governing plan explicitly defers to the final phase; and `_UNCONDITIONAL_HEADER_CASES` stays in the legacy test file until every group it references has moved, so a contributor mid-series never has to improvise mixed imports from both the shrinking monolith and the growing package. `[verified: .claude/plans/transcript-analysis-decomposition.md:144-161]`
- **Editing narrative mentions of read-scope symbols** in the shim, `corpus.py`, and `pricing.py`. Each still names a symbol that exists after the move (row 4).
- **The stale `transcript_analysis/__init__.py` docstring**, which names only the leaf modules plus `cost`. It predates this phase, and this phase does not need to touch the file. It is a candidate for the `cli.py` phase.
- **Converting the line-number citations in `review_rounds.py`.** This move shifts them again, but the final `cli.py` phase converts them (code-file-size-splits.md:169).
- **An import-direction guard test for `read_scope.py`**, like `test_transcript_analysis_cost_import_direction.py`. Neither the reviewer-yield phase nor the review-trace phase added one.
- **A-field's A2 extensions to read-scope**: the reviewer split, size buckets, and PARTIAL-view counts. They belong to child A2's own PR.
- **Any change to read-scope's subcommand, flags, or output** (Verification step 3).
