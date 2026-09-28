# Extract the pr-cost family into transcript_analysis/

## Context

Continue epic #1113's decomposition of
`claude/.claude/scripts/transcript-analysis.py` (tracking issue #1116) with
its next command-group phase: extract the pr-cost family. This is the
largest remaining command group by the governing plan's own test-line-count
methodology (`cmd_pr_cost` + `cmd_pr_cost_export`, ~4,664 test lines),
ahead of cache-rebuild's ~4,155 — both named remaining groups in the
governing plan (audit-routing, cost-ledger) turned out stale on
re-measurement, having grown past their originally-recorded sizes without
overtaking either of these two. The intended outcome matches every prior
phase: `cmd_pr_cost` and `cmd_pr_cost_export` move out of the shim into new
modules under `transcript_analysis/`, the group's own exclusive test
coverage moves with them into dedicated test file(s), and two genuinely
shared, `cmd_*`-independent helper blocks currently stranded in the shim
(gh-repo-resolution, used by `cmd_pr_link`/`cmd_pr_cost`/`cmd_workstream_cost`;
`_resolve_machine_identity`, used by `cmd_cost_ledger`/`cmd_pr_cost`) move
into new leaf modules alongside it, so the commands that stay in the shim
gain a real, non-circular way to reach that logic instead of the shim
remaining the only place it lives.

Two design refinements below went past what the engineer had approved at
first pass and were confirmed via a follow-up `AskUserQuestion`, resolving
`plan-architect`'s two open decisions:

1. **Command split.** Combining `cmd_pr_cost` and `cmd_pr_cost_export` into
   one `pr_cost.py` would run ~1,120 lines — over the 1,000-line cap — and
   `cmd_pr_cost_export` reads nothing from `cmd_pr_cost`'s code, making the
   command boundary a clean second seam. **Confirmed:** split into three
   production modules — `pr_cost.py`, `pr_cost_export.py`,
   `pr_cost_ledger.py` — not two. `[engineer-verified: "Yes, pr_cost.py +
   pr_cost_export.py + pr_cost_ledger.py (Recommended)"]`
2. **Leaf split.** The two shared helper blocks serve different consumer
   sets (gh-repo-resolution: pr-link/pr-cost/workstream-cost;
   machine-identity: cost-ledger/pr-cost/pr-cost-export) and different
   concerns (network calls vs. local ledger files). **Confirmed:** two leaf
   modules — `gh_cli.py`, `ledger_common.py` — not one combined leaf.
   `[engineer-verified: "Yes, gh_cli.py + ledger_common.py (Recommended)"]`

## Approach

pr-cost and pr-cost-export move out of the shim into five package modules:
- `ledger_common.py` and `gh_cli.py` are leaves. They hold the logic the shim's still-unmigrated cost-ledger, pr-link, and workstream-cost also call.
- `pr_cost_ledger.py` is a leaf holding the pr-cost ledger's on-disk format.
- `pr_cost.py` and `pr_cost_export.py` are the two command modules.

Their tests move into seven test files plus one family helper module. `cmd_pr_cost` and `cmd_pr_cost_export` move whole, matching the read-scope precedent. The shim keeps `build_parser()`, and it imports by name only what its own remaining code reads.

Alternatives considered and set aside:
- **A two-module split with a single shared leaf** (the engineer's first-pass approval). `pr_cost.py` would exceed the cap, and one leaf would join two unrelated concerns (M1) — superseded by the confirmed refinement above.
- **Keeping the shared helpers in the shim.** It is infeasible. `pr_cost.py` cannot import from the shim (row 6).
- **Promoting every shared test helper to `conftest.py`.** That pushes conftest over the 1,000-line test limit (row 21).

### Assumption ledger

**Root:** The pr-cost family still lives in the monolithic shim: `cmd_pr_cost`, `cmd_pr_cost_export`, their ledger, and the gh and machine-identity helpers they need, about 2,060 lines. It is the largest remaining group by test lines, 4,575 across three sections plus a 478-line shared machine-identity section. #1116's task list takes the remaining groups largest first. `[verified: section headers at legacy test :19795, :21233, :22911, :24295, :25754; gh issue view 1116]` The ordering against cache-rebuild's ~4,155 comes from Step 3 research. `[unverified — not re-measured here]`

**Givens:**
- G1. pytest's `prepend` import mode imports test modules by basename, so new test files keep the `test_transcript_*` prefix. Reason: pytest owns this behavior. `[verified: transcript-analysis-decomposition.md:59-63]`
- G2. `from m import n` binds `n` at import time. A monkeypatch therefore reaches code only through the binding that code reads at call time. Reason: Python language semantics. `[verified: transcript-analysis-decomposition.md row 1, citing the import-statement reference]`
- G3. A directly invoked `python3 transcript-analysis.py` finds the package only through CPython's `sys.path[0]`. Reason: CPython owns script bootstrap. `[verified: tests/test_transcript_cli_bootstrap.py:1-11]`

Three conditions look like givens but are not. This repo owns each one, so each is a deliberate decline, and each is listed in **Out of scope** with its reason: the shim-plus-package shape, `build_parser()` staying in the shim, and `_UNCONDITIONAL_HEADER_CASES` staying in the legacy file.

**Mechanisms:**
- **M1 — Five modules.** `ledger_common.py` (leaf), `gh_cli.py` (leaf), `pr_cost_ledger.py` (leaf), `pr_cost.py` (command), `pr_cost_export.py` (command), each moved by line-range extraction. `anchors: root, row1, row2, row6, row7, row10, row11`
  - Lighter: one `pr_cost.py` holding all pr-cost-exclusive code. It exceeds the cap (row 11).
  - Lighter: leave the shared helpers in the shim. The package may not import from the shim (row 6).
  - Lighter: one leaf for both shared blocks. Rejected on cohesion — the consumer sets are disjoint (row 7). `[engineer-verified: "Yes, gh_cli.py + ledger_common.py (Recommended)"]`; the three-module command split is likewise `[engineer-verified: "Yes, pr_cost.py + pr_cost_export.py + pr_cost_ledger.py (Recommended)"]`.
- **M2 — Package modules reach each other by attribute access** (`scope._resolve_cost_roots`, `pr_cost_ledger._parse_pr_cost_ledger_file_text`), per the governing plan's M2. `ledger_common.py` and `pr_cost_ledger.py` each bind `config_dir` by name from `_config_dir`, as `scope.py` does. Every patch that routes the machine-identity or pr-cost-ledger path (row 14) retargets to the new binding. `anchors: G2, row14, row15`
  - Lighter: read `scope.config_dir()` and add no new binding. Rejected: that couples identity-file routing to corpus-root routing. Tests steer those two independently today (row 15).
  - Lighter: call `_config_dir.config_dir()` by attribute. Rejected: no existing patch reaches it, so every routing site would still need a new target, and `fake_projects`' other consumers would too.
- **M3 — `gh_cli.py` imports `pr_cost_ledger` by module**, for the two degraded-status constants `_gh_call_with_backoff` returns. `anchors: row9`
  - Rejected: move the status enum into `gh_cli.py`. The ledger parser, and pr-cost-export ("makes no gh call"), would then depend on the gh module.
  - Rejected: duplicate the two strings. That breaks single source of truth.
- **M4 — Shim imports.**
  - It imports by name exactly the moved names its own remaining code reads (rows 7, 12).
  - It adds `gh_cli`, `ledger_common`, `pr_cost`, `pr_cost_export`, and `pr_cost_ledger` to its module-import list for the tests' `_mod.<module>.<name>` channel.
  - It drops imports that no remaining shim code reads.
  - Exception: `_compute_pr_cost_branch_totals` stays with `# noqa: F401`, because a test file this phase does not edit reads it (row 13).

  `anchors: row7, row12, row13, row17`
- **M5 — `cmd_pr_cost` and `cmd_pr_cost_export` move whole.** This matches read-scope, where the shim keeps only `build_parser()` wiring. Nothing in either command's size or shape argues for a shim wrapper. `anchors: row11, row12`
- **M6 — Test layout: seven test files along module seams and the legacy file's own section seams.** The seven pr-cost-family-only helpers move to a new `tests/_pr_cost_helpers.py`. The three helpers staying tests also use move to `conftest.py`. `anchors: row19, row20, row21, row22`
  - Rejected: all ten helpers into conftest. It lands at about 1,175 lines, over the limit (row 21), and it widens family-only helpers to suite scope.
  - Rejected: new files importing from the legacy test module. Read-scope's plan rejected this too: it couples test modules and runs the legacy loader twice.
- **M7 — Test re-point rules.** `anchors: G2, row16, row17, row18`
  - (a) Moved test code: every read of a name this phase moves becomes `_mod.<owning module>.<name>`.
  - (b) Staying test code changes only where its `_mod.<name>` loses its shim binding. That covers four `_MACHINE_IDENTITY_FILENAME` reads.
  - (c) Each monkeypatch targets the binding its code under test reads. The five sites are in rows 14 and 16. `test_transcript_workstream_cost.py:489` stays unchanged (row 17).
- **M8 — `_ledger_path_is_git_tracked` moves to `ledger_common.py` byte-for-byte.** Its fail-closed docstring moves with it, and all three refusal call sites stay inside their writing functions. `anchors: row7, row27`
- **M9 — One `code-writer` dispatch, staged internally.** Leaves come first with a green checkpoint, then the command modules. Both stages land in one commit; the Stage 1 checkpoint is an internal validation gate, not a commit boundary, so Stage 1's state is never independently reviewed or merged without Stage 2. Stage 1 also retargets the `config_dir` patches in `conftest.py`'s `fake_projects` and `test_transcript_analysis.py`'s `cost_ledger_enabled` (row 14) to the new `ledger_common`/`pr_cost_ledger` bindings: `TestMachineIdentity` stays in the legacy file until Stage 2, but it exercises the machine-identity code Stage 1 already moved, so Stage 1's "must be green" checkpoint is not a real signal until these two retargets land with it.
  - Rejected: sequenced dispatches. Every stage edits the same four files (shim, legacy tests, conftest, doc). `gh_cli.py` also needs `pr_cost_ledger.py` (M3), so the first stage cannot land alone.
  - Rejected: leaving the `fake_projects`/`cost_ledger_enabled` retargets for Stage 2 alongside the test-file moves. `TestMachineIdentity` runs in Stage 1's own scoped-suite checkpoint (it lives in the not-yet-moved legacy file), so a stale patch there would let the checkpoint pass on a coincidental `CLAUDE_CONFIG_DIR` pin (row 14) instead of failing loudly.

  `anchors: row6, row9, row14`
- **M10 — No `select-tests.py` change.** `anchors: row23`
- **M11 — One new bootstrap test, `pr-cost --help`.** pr-cost-export already has a seeded subprocess test. `anchors: G3, row30`

**Assumptions:**

1. `[engineer-verified: "pr_cost.py + pr_cost_ledger.py"]` plus the engineer's typed text `[engineer-verified: "That split makes sense. What does plan-architect think?"]`. This covers splitting pr-cost's command logic from its ledger read/write/lock/parse code, refined per assumption 3 into a three-way split.
2. `[engineer-verified: "Extracting into a new leaf module makes sense to me. What does plan-architect think?"]` This covers extracting the shared gh-repo and machine-identity helpers into new leaf code now, rather than duplicating them, refined per assumption 4 into a two-leaf split.
3. Three production modules (`pr_cost.py`, `pr_cost_export.py`, `pr_cost_ledger.py`) instead of two. `[engineer-verified: "Yes, pr_cost.py + pr_cost_export.py + pr_cost_ledger.py (Recommended)"]`
4. Two leaf modules (`gh_cli.py`, `ledger_common.py`) instead of one. `[engineer-verified: "Yes, gh_cli.py + ledger_common.py (Recommended)"]`
5. Line numbers below are at `33506ff5`. `origin/main` (`8a4d1e4c`) shifts them, and none of its hunks touch a span this plan moves. `[verified: git diff hunk headers HEAD..origin/main — shim :18, :50, :1980, :9125, :9185, :11129, :11161, :12554; legacy tests :6109; conftest :331, :348]`
   - shim spans after :1980 move +3;
   - legacy-test spans after :6109 move +3;
   - `conftest.py` grows by 57 lines.
6. The package may not import back from the shim. `[verified: docs/transcript-analysis-architecture.md:124-127]` Every shim name the pr-cost span reads must therefore move or already live in the package. That set includes four cost-ledger-section constants: `_COST_LEDGER_CONFLICT_MARKERS` (read :6870), `_COST_LEDGER_LOCK_TIMEOUT_S` and `_COST_LEDGER_LOCK_POLL_INTERVAL_S` (read :7000–7021), and `_MACHINE_LABEL_RE` (read :6812, :7652). `[verified: grep]`
7. Consumers of the shared names. `[verified: grep]`
   - `ledger_common` names:
     - cost-ledger reads `_MACHINE_LABEL_RE` :6039, `_COST_LEDGER_CONFLICT_MARKERS` :6103, both lock constants :6297–6319, `_ledger_path_is_git_tracked` :6472, `_resolve_machine_identity` :6493, and `_warn_machine_identity_absent_from_ledger` :6590;
     - pr-cost reads them at :7801, :7827, :7853;
     - pr-cost-export reads `_MACHINE_IDENTITY_RE` :8214 and `_ledger_path_is_git_tracked` :8294.
   - `gh_cli` names:
     - pr-link reads `_classify_gh_error` and `_GH_ERROR_KIND_NETWORK` :2234–2237, `_git_remote_origin_host_and_owner_repo` :2259, `_gh_host_qualified_repo` :2262, and `_GH_CALL_TIMEOUT_S` :2292, :2315, :2325;
     - workstream-cost reads `_git_remote_origin_host_and_owner_repo`, `_gh_auth_preflight_ok`, `_resolve_pinned_gh_repo`, `_gh_discover_merged_prs`, and `_gh_discover_closed_unmerged_pr_branches` at :8456–8470.
8. The machine-identity helpers (:5784–5906) and `_ledger_path_is_git_tracked` (:5908–5965) are pure functions of their arguments, `config_dir()`, their own constants, and the filesystem or `git`. They hold no `cmd_*` state, so they qualify as leaves under Phase 1's rule. `[verified: Read of :5784-5965]`
9. `gh_cli`'s package dependencies are these, and no others. `[verified: Read of :6684-6768, :7024-7306]`
   - `_gh_call_with_backoff` returns `_PR_COST_STATUS_DEGRADED_RATE_LIMIT` and `_PR_COST_STATUS_DEGRADED_NETWORK` (:7147–7150).
   - `_resolve_pinned_gh_repo` calls `_assign_root_scoped_redact_label` (:7238–7239).
10. pr-cost-export reads nothing from the pr-cost command span (:7309–8006). `[verified: Read of :8009-8399]` Its names come from:
    - `pr_cost_ledger`;
    - `ledger_common`;
    - `scope`: `_resolve_cost_roots`, `_redaction_ordinals`, `print_resolved_scope`;
    - `redaction`;
    - `_config`;
    - `_config_dir.declared_roots_file_is_overridden`.
11. Estimated module sizes, as moved lines before headers:
    - `ledger_common` about 198;
    - `gh_cli` about 335;
    - `pr_cost_ledger` about 318;
    - `pr_cost` about 729;
    - `pr_cost_export` about 391.

    Both commands in one file come to about 1,120 moved lines plus a header. The limit is 1,000 lines, exact, for production and test code alike. `[verified: arithmetic over the Critical-files spans; limit at .claude/plans/code-file-size-splits.md rows 60-61 and #1116's body]`
12. `build_parser()` reads `_PR_COST_ASOF_WINDOW_DAYS_DEFAULT` :12466, `_DEFAULT_PR_COST_PLAN_FILE_GLOB` :12473, `cmd_pr_cost` :12483, and `cmd_pr_cost_export` :12505. `[verified: Read]`
13. After the move, no remaining shim code reads these imports: `_assign_root_scoped_redact_label`, `_new_pr_cost_agg`, `_compute_pr_cost_branch_totals`, `declared_roots_file_is_overridden`, and stdlib `secrets` and `hashlib`. `[verified: grep over the shim minus the moved spans; ruff F401 confirms]` Their test readers: `[verified: grep]`
    - `_compute_pr_cost_branch_totals`: `test_transcript_workstream_cost.py:304`, plus moved tests.
    - `_new_pr_cost_agg`: legacy :20342, which moves.
    - `secrets`: `_mod.secrets` at :25849, which moves.
14. `_machine_identity_path` (:5803) and `_pr_cost_ledger_path` (:6788) call the shim's by-name `config_dir`. `[verified]` These sites patch that binding to route one of the two paths:
    - `conftest.py:728`, in `fake_projects`;
    - legacy :5982, in `cost_ledger_enabled`, which seeds an identity at :5980;
    - `TestMachineIdentity` :25769 and :26128.

    The autouse fixture pins `CLAUDE_CONFIG_DIR` to `tmp_path/isolated-claude-config` (`conftest.py:789`). That is the same directory `TestMachineIdentity` uses, so a patch left on the old binding there would pass silently instead of failing. `[verified: conftest.py:789; legacy :25767]`
15. `fake_projects` (`conftest.py:727`) and pr-cost tests (:21941, :22733) patch `scope.config_dir` separately, to steer corpus-root resolution. `[verified]` `_cost_ledger_report(args, today, roots=None)` (:6349) resolves roots when none are passed. `TestMachineIdentity`'s cost-ledger cases therefore depend on identity routing and root routing staying independent. `[unverified — inferred from the signature; not traced into the body]`
16. Other patches in the moved slices that name a moved or rebound symbol:
    - :20703 `_mod._resolve_project_scope`: the code under test becomes `pr_cost.py`, which reads `scope._resolve_project_scope`;
    - :25513 `_mod._format_pr_cost_ledger_row`: `pr_cost_export.py` reads `pr_cost_ledger._format_pr_cost_ledger_row`;
    - :25849 `_mod.secrets`.

    Every other patch there targets a shared module object: `subprocess`, `time`, `_mod._config`, `_mod.os`, `_mod.tempfile`, or `_mod.scope.*`. `[verified: grep of setattr across :19795-26231]`
17. `test_transcript_workstream_cost.py:489` patches `_mod._gh_auth_preflight_ok`. `cmd_workstream_cost` stays in the shim and reads the shim's by-name binding, so the patch keeps working unchanged. `[verified]`
18. Staying legacy tests read two moved names. `[verified: grep]`
    - `_mod._GH_CALL_TIMEOUT_S` at :2914, :3030, :3072 (pr-link tests): the shim keeps binding it, so these stay unchanged.
    - `_mod._MACHINE_IDENTITY_FILENAME` at :5980, :11083, :11098, :11115: the shim no longer binds it, so these re-point.
19. Test slices and counts. `[verified: grep of "    def test_"]` The counts include 11 `parametrize` decorators. The Step 3 evidence says they are all literal. `[unverified here]`
    - local-mechanics :19795–21232: 75 tests;
    - gh-integration :21233–22910: 64 tests;
    - pr-cost-export :24295–25753: 61 tests;
    - machine identity :25754–26231, the end of the file: 25 tests.
20. Helpers these slices use. `[verified: grep of call sites]`
    - Family-only helpers :19804–20004: `_enable_pr_cost`, `_pr_cost_args`, `_pr_cost_export_args`, `_fake_pr_cost_subprocess_run`, `_argv_carries_repo_pin`, `_sample_pr_cost_row`, `_legacy_row_line`.
    - Helpers staying tests also use: `_cost_ledger_args` :5986, `_cost_ledger_row` :6003, `_two_declared_roots` :16675.
    - Four of these reach through `_mod`:
      - `_enable_pr_cost` reads `_MACHINE_IDENTITY_FILENAME`;
      - `_sample_pr_cost_row` reads `_PR_COST_LEDGER_COLUMNS`;
      - `_legacy_row_line` reads `_format_pr_cost_ledger_row`;
      - `_two_declared_roots` patches `scope.PROJECTS_DIR`.
21. `conftest.py` is 862 lines, and 919 after syncing `origin/main`. Adding all ten helpers (about 255 lines) would take it to about 1,175. `[verified: wc -l; diff stat; arithmetic]`
22. `scripts/tests/` has no non-test sibling module today, and the directory is a package. `[verified: ls]` A sibling module therefore needs `from ._x import ...` (`.claude/rules/test-tree-packaging.md`). `pyproject.toml` sets no `python_files`, so pytest does not collect `_pr_cost_helpers.py`. `[verified: grep]`
23. Test selection already covers every new file. Any path under `claude/.claude/scripts/` selects `scripts/tests/`, and `config-keys.psv` selects `scripts/tests/` as well. No moved test reads a hook or SKILL.md by path; the only `hooks/` strings are file-path data for the mechanical-proxies test. `[verified: select-tests.py:65-73, :140, :486, :503; grep of the slices]`
24. The doc-drift test requires exactly one `### \`<module>.py\`` heading per package module. `[verified: test_transcript_analysis_architecture_doc.py:15-32]`
25. No moved span has a local variable named `corpus`, `cost`, `gh_cli`, `ledger_common`, `pr_cost_ledger`, `pricing`, `redaction`, `render`, or `scope`. The only hits are `corpus=` inside f-strings at :8239–8248. `[verified: grep of the spans]`
26. Four positional cross-references point into a different module after the move: :6687 "(see above)", :8116 "gate above", :8150 "branch above", and :8163 "message above". Every other "above" or "below" in the production spans stays within its own module. `[verified: grep]`

    The same pass over the test spans (:19795–26231, excluding :22911–24294, which is a different command family) finds one stale reference and no others. `[verified: grep of '\b(above|below)\b' plus direct Read at each boundary-adjacent hit]` Of the 43 in-scope hits, most sit deep enough inside their destination file's assembled ranges that the referenced content can't cross a file seam; the plan-review round checked every hit within 100 lines of a destination-range boundary directly:
    - :21933's "test_account_ordinal_is_resolved_path_sorted_not_scan_order above" is already dangling in the current file — no matching test definition precedes it anywhere in the pr-cost spans — and stays dangling in its destination, `test_transcript_gh_cli.py`. Rewrite it to name the actual prior test, `test_mismatch_with_non_default_ordinal_labels_output_account_two` (:21902, same destination file), or drop the positional reference and state the divergence directly.
    - :19876, :20999, :21049, :21319, :21705, :21810, :24450, and :25868 all reference content that lands in the same destination file as the reference itself. No edit needed.

    The remaining 34 of the 43 in-scope hits sit far enough inside their destination range that a distance-from-boundary heuristic, not a direct read, established safety. That is a disclosed scope limit appropriate to a doc-drift-only risk, not an exhaustive verification — a future phase reusing this pass as a template should re-apply the same heuristic-plus-boundary-spot-check shape, not assume every hit was directly read.
27. The governing plan's own note on `cost_ledger`'s sentinel and git-tracked checks: `[verified: transcript-analysis-decomposition.md:304-308]`

    > the opt-in sentinel (`:8659-8671`) and the git-tracked refusal (`_ledger_path_is_git_tracked:8184-8238`, invoked `:8679`) must stay in the same function as the write, not hoist into a shared layer a future command could bypass.

    This plan reads the note as binding the refusal call and the sentinel, not the predicate's file location. The predicate already has three callers, and one of them is moving into the package (row 7). The other reading, keeping the predicate in the shim, would leave `pr_cost.py` unable to reach it (row 6). `[unverified reading — flag for plan-review]`
28. No sibling script, hook, or skill imports a moved name. The only hits are the shim, the legacy test file, prose in `docs/transcript-analysis.md`, and a dated report. `[verified: git grep]`
29. `_UNCONDITIONAL_HEADER_CASES` has no pr-cost or pr-cost-export row. Its pr-link row reads `_mod.cmd_pr_link`, which stays in the shim. `[verified: grep :16451-16532]`
30. `test_transcript_cli_bootstrap.py` already has a seeded pr-cost-export subprocess test (:262). It has no pr-cost test. `[verified: grep]`

## Critical files

**Precondition.** Sync the branch with `origin/main` (`git-feature-branch-sync`) before dispatch. Then re-locate every span below by its first and last symbol, not its line number (row 5).

**Create — production.** Moved code is copied verbatim. Only three kinds of edit are allowed:
- module prefixes;
- the four cross-reference fixes in row 26: rewrite "above" to name the owning module, e.g. "in pr_cost.py";
- each module's docstring and import header.

The test side has one equivalent fix, also from row 26: rewrite :21933's dangling "above" reference (landing in `test_transcript_gh_cli.py`) to name `test_mismatch_with_non_default_ordinal_labels_output_account_two` directly, or drop the positional reference.

Every module starts with `from __future__ import annotations`.

- `transcript_analysis/ledger_common.py` takes shim :5743–5745, :5747–5751, :5756–5763, and :5784–5965. That runs from `_MACHINE_IDENTITY_FILENAME` through `_ledger_path_is_git_tracked`, including its docstring.
  - Imports: `contextlib`, `os`, `re`, `secrets`, `subprocess`, `sys`, `tempfile`, `Sequence`, `Path`, and `from _config_dir import config_dir`.
  - Docstring: `"""Recording primitives shared by the cost-ledger and pr-cost ledgers: the generated per-config-dir machine identity, the git-tracked destination check, the machine-label format, the merge-conflict markers both parsers refuse, and the local-lock timing both --record paths use."""`
- `transcript_analysis/pr_cost_ledger.py` takes :6617–6682 (columns, legacy header, typed-column tuples, status enum, join-confidence enum) and :6771–7022 (`_PrCostLedgerParseError` through `_acquire_pr_cost_ledger_lock`).
  - Imports: `contextlib`, `errno`, `fcntl`, `math`, `os`, `stat`, `sys`, `tempfile`, `time`, `datetime`, `Sequence`, `Path`, `from _config_dir import config_dir`, and `from transcript_analysis import ledger_common`.
  - Docstring: `"""The pr-cost ledger's on-disk format: column schema, status and join-confidence enums, path resolution, canonical parser and formatter, append-only upsert, crash-safe write, and the --record lock.\n\nImports ledger_common by module (attribute access, not by name) -- see scope.py's own top-of-file comment for why."""`
- `transcript_analysis/gh_cli.py` takes :6684–6689 (`_GH_CALL_DEGRADED_*`), :6722–6753 (gh and git-remote constants plus the port-syntax comment, but not :6754), :6756–6768 (the stderr regexes), and :7024–7306 (`_git_remote_origin_host_and_owner_repo` through `_gh_discover_closed_unmerged_pr_branches`).
  - Imports: `json`, `re`, `subprocess`, `sys`, `time`, `Sequence`, and `from transcript_analysis import pr_cost_ledger, redaction`.
  - Docstring: `"""gh and git-remote access shared by pr-link, pr-cost, and workstream-cost: origin host/owner/repo parsing, gh stderr classification, rate-limit backoff, auth preflight, effective-repo pinning, and merged/closed PR discovery.\n\nImports pr_cost_ledger and redaction by module (attribute access, not by name) -- see scope.py's own top-of-file comment for why."""`
- `transcript_analysis/pr_cost.py` takes :6691–6720 (as-of default, plan glob, risk globs, test-file regex), :6754 (`_GIT_SHA_RE`), and :7309–8006 (`_gh_pr_view_enrichment` through `_pr_cost_report`). The shim's section-divider comment at :6609–6615 is not carried over; its redaction invariant moves into the docstring.
  - Imports: `argparse`, `fcntl`, `fnmatch`, `json`, `subprocess`, `sys`, `datetime` and `UTC`, `Sequence`, `Path`, `PurePosixPath`, `import _config`, and `from transcript_analysis import corpus, cost, gh_cli, ledger_common, pr_cost_ledger, pricing, redaction, render, scope`. Let ruff settle the exact stdlib set.
  - Docstring: `"""The pr-cost command family: cmd_pr_cost and every helper used only by it -- the branch-to-merged-PR join, per-PR gh enrichment, mechanical review-surface proxies, and the read/--record report.\n\nEvery stdout/stderr path routes branch and repo values through redaction._assign_root_scoped_redact_label, never raw.\n\nImports its package dependencies by module (attribute access, not by name) -- see scope.py's own top-of-file comment for why."""`
- `transcript_analysis/pr_cost_export.py` takes :8009–8399.
  - Imports: `argparse`, `contextlib`, `hashlib`, `os`, `sys`, `tempfile`, `datetime` and `UTC`, `Sequence`, `Path`, `import _config`, `from _config_dir import declared_roots_file_is_overridden`, and `from transcript_analysis import ledger_common, pr_cost_ledger, redaction, scope`.
  - Docstring: `"""The pr-cost-export command family: cmd_pr_cost_export and every helper used only by it -- collapsing every declared account's pr-cost ledger to current rows, redacting them, and publishing one TSV. Makes no gh call and scans no transcript corpus."""`
- **Rename map, from existing package modules:**
  - `_assign_root_scoped_redact_label` → `redaction.`;
  - `_compute_pr_cost_branch_totals` and `_new_pr_cost_agg` → `cost.`;
  - `_fmt_usd` and `_pct_value` → `render.`;
  - `_parse_ts` → `corpus.`;
  - `_PRICING_FETCH_DATE` → `pricing.`;
  - `_resolve_cost_roots`, `_resolve_project_scope`, and `_redaction_ordinals` → `scope.`;
  - `_print_resolved_scope` → `scope.print_resolved_scope`, because the old name is a shim alias.
- **Cross-references between the new modules:** each takes its owning-module prefix. Ruff's F821 finds every bare name that needs one.

**Create — tests.** Each file gets a one-line docstring naming its module and the loader from `test_transcript_read_scope.py:1-31`. The legacy section-comment headers (:19795–19801, :21233–21239, :24295–24300, :25754–25759) are dropped.
- `tests/_pr_cost_helpers.py` takes legacy :19804–20004 verbatim, except four `_mod.` references: `ledger_common._MACHINE_IDENTITY_FILENAME`, `pr_cost_ledger._PR_COST_LEDGER_COLUMNS`, and `pr_cost_ledger._format_pr_cost_ledger_row` (twice). It imports `from transcript_analysis import ledger_common, pr_cost_ledger`. Docstring: `"""Test helpers shared by the pr-cost family's test files (test_transcript_pr_cost*.py, test_transcript_gh_cli.py, test_transcript_ledger_common.py)."""`
- `tests/test_transcript_ledger_common.py` takes `TestMachineIdentity`, :25762–26231.
- `tests/test_transcript_gh_cli.py` takes :20352–20455 (`TestGhDiscover*`), :21242–21633 (`TestClassifyGhError` through `TestGhCallWithBackoffElapsedBudgetCap`), and :21738–22082 (`TestResolvePinnedGhRepo*`, `TestGhHostQualifiedRepo`).
- `tests/test_transcript_pr_cost_ledger.py` takes :20456–20483, :20922–21123, :21200–21232, :24564–24587, and :25700–25717.
- `tests/test_transcript_pr_cost.py` takes :20007–20351, :20484–20921, and :21124–21199.
- `tests/test_transcript_pr_cost_gh.py` takes :21634–21737 and :22083–22910.
- `tests/test_transcript_pr_cost_export.py` takes the row rendering and `--out` write path: :24303–24563, :24588–24610, :25328–25699, and :25718–25753.
- `tests/test_transcript_pr_cost_export_accounts.py` takes per-account gating, ordinals, and provenance: :24611–25327.
- Every moved test applies M7(a).
- It also applies these patch retargets:
  - :20703 → `_mod.scope`;
  - :25513 → `_mod.pr_cost_ledger`;
  - :25769 and :26128 → `_mod.ledger_common`;
  - :25849 → `_mod.ledger_common.secrets`.
- Nothing else on an `assert` line changes.

**Modify:**
- `claude/.claude/scripts/transcript-analysis.py`
  - Delete the spans above, keeping two blank lines between each surviving neighbour pair.
  - Add the five modules to the `from transcript_analysis import (...)` list and to its comment at :37–40.
  - Add by-name import blocks, in read-scope's commented style, naming each consumer:
    - `gh_cli`: the nine names in row 7;
    - `ledger_common`: the seven cost-ledger names in row 7;
    - `pr_cost`: `cmd_pr_cost`, `_PR_COST_ASOF_WINDOW_DAYS_DEFAULT`, and `_DEFAULT_PR_COST_PLAN_FILE_GLOB`;
    - `pr_cost_export`: `cmd_pr_cost_export`.
  - Drop the imports ruff F401 reports (row 13).
  - Keep `_compute_pr_cost_branch_totals` with `# noqa: F401`, and update the cost-import comment at :61–65 to say only tests read it now.
  - `build_parser()` and every `cmd_*` body stay unchanged.
- `claude/.claude/scripts/tests/test_transcript_analysis.py`
  - Delete :19795–22910 and :24295 through the end of the file.
  - Import `_cost_ledger_args`, `_cost_ledger_row`, and `_two_declared_roots` from `.conftest`, and delete their definitions.
  - Re-point the four `_MACHINE_IDENTITY_FILENAME` reads (row 18).
  - In `cost_ledger_enabled` (:5982), add `monkeypatch.setattr(_mod.ledger_common, "config_dir", lambda: cfg_dir)`. Change the docstring bullet at :5973–5975 to say both bindings are patched.
- `claude/.claude/scripts/tests/conftest.py`
  - Add `_cost_ledger_args`, `_cost_ledger_row`, and `_two_declared_roots`. In `_two_declared_roots`, `_mod.scope` becomes the conftest-level `scope`, imported beside `pricing`.
  - In `fake_projects`, add `monkeypatch.setattr(mod.ledger_common, "config_dir", lambda: tmp_path)` and `monkeypatch.setattr(mod.pr_cost_ledger, "config_dir", lambda: tmp_path)`. Update its docstring's binding list.
  - Add the seven new test files to the module docstring's consumer list.
- `claude/.claude/scripts/tests/test_transcript_cli_bootstrap.py` gets one test, `test_transcript_analysis_pr_cost_help_exits_zero`. It asserts exit 0, and that `--asof-window-days` and `default: 3` appear in stdout.
- `docs/transcript-analysis-architecture.md`
  - Exception paragraph: add `pr_cost.py` and `pr_cost_export.py` to "the only modules the shim imports back into". Add one sentence saying `build_parser()` wires up `cmd_pr_cost` (with its two default constants) and `cmd_pr_cost_export` from the shim.
  - Add five `### ` sections after the current last one, in this order: `ledger_common.py`, `gh_cli.py`, `pr_cost_ledger.py`, `pr_cost.py`, `pr_cost_export.py`. Each follows read-scope's shape: what it owns, which modules it imports by module, and which names the shim reaches bare.
    - `gh_cli.py`'s section notes its import of `pr_cost_ledger` (M3).
    - `pr_cost.py`'s section notes its import of the `cost.py` command group.
    - `ledger_common.py`'s and `pr_cost_ledger.py`'s sections note their own `config_dir` binding.
  - Tests section: list the seven files and `_pr_cost_helpers.py`. Add the three helpers to conftest's shared list, and update the `fake_projects` sentence to cover four `config_dir` bindings.

**Explicitly unchanged:** `select-tests.py`, `test_select_tests.py` (row 23), `test_transcript_workstream_cost.py` (row 17), `cost.py`, `scope.py`, `redaction.py`, `docs/pr-cost.md`, `docs/transcript-analysis.md` (row 28).

**Reuse:**
- `read_scope.py:1-16` for the docstring and by-module import shape.
- `test_transcript_read_scope.py:1-31` for the loader.
- `scope.py`'s by-name `config_dir` binding as the pattern for M2.
- `test_transcript_cli_bootstrap.py`'s `_run`.

**Dispatch.** One `code-writer` dispatch (M9) covers every file above. Its instructions:
- Stage 1: create `ledger_common.py`, `pr_cost_ledger.py`, and `gh_cli.py`, and repoint the shim's remaining pr-cost code to them by name. Also make the `conftest.py` `fake_projects` and `test_transcript_analysis.py` `cost_ledger_enabled` `config_dir` retarget edits from the "Modify" section now (row 14) — `TestMachineIdentity` isn't moved until Stage 2 but exercises the Stage-1-moved machine-identity code, so these retargets must land before the checkpoint for it to be a real signal. Run the scoped suite, which must be green before stage 2.
- Stage 2: create `pr_cost.py` and `pr_cost_export.py`, and move the tests. Add one new test to `TestMachineIdentity` (landing in `test_transcript_ledger_common.py`) that patches `ledger_common.config_dir` and the shim's `config_dir` to two *different* directories and asserts `_resolve_machine_identity` routes through `ledger_common.config_dir` — the existing revert checks at :25769/:26128 can pass on a coincidental shared `CLAUDE_CONFIG_DIR` pin (row 14) instead of failing on a wrong binding, and this closes that gap.
- Extract moved code by line range from unmodified scratch copies. Never retype it.
- Let ruff F821 and F401 drive prefixes and import pruning.
- The seven test files that consume `tests/_pr_cost_helpers.py` import it as `from ._pr_cost_helpers import ...` (`.claude/rules/test-tree-packaging.md`) — not a bare `from _pr_cost_helpers import ...`.
- Apply M7's three rules mechanically.
- State in the PR body why `_ledger_path_is_git_tracked` was judged safe to move into `ledger_common.py` while its three refusal call sites (:6472, :7801, :8294) were not (row 27): it is a pure boolean predicate with no `sys.exit` of its own, and the actual refusal decision stays inline in each writing function per M8, so no future write path can route through a shared wrapper that skips the check. Also state the ambiguity itself: the governing plan's note equates the predicate with "the refusal," while this plan's reading narrows that to cover only the predicate's file location — name this as a judgment call, not the only defensible reading.
- Verify with steps 1–11 below.

## Verification

Run everything from the worktree root. `<venv>` is the worktree-relative `.venv` from README.md's Tests section. Scratch files live outside the repo; print counts only.

0. **Baseline, before the dispatch and after the sync.**
   - Copy the unmodified shim, legacy test file, and conftest to scratch.
   - Save `<venv>/bin/pytest --collect-only -q` IDs for `test_transcript_analysis.py`.
   - Capture top-level and per-subcommand `--help`.
   - Build a scratch stub harness. It is never committed:
     - a `git init` repo with `origin` set to `https://github.com/owner/repo.git`;
     - a `gh` script first on `PATH` that answers `auth status` (exit 0), `repo view`, `pr list --state merged`/`closed`, `pr view`, and `api` with fixed JSON;
     - a seeded account with one branch matching the merged PR;
     - `.pr-cost-enabled` and `.cost-ledger-enabled` in that account.
   - This harness is happy-path only by design: it proves subprocess/import wiring survives the move (G3), not gh failure-mode behavior. It never scripts a non-zero exit, rate limit, or malformed response, so it does not backstop `_gh_call_with_backoff`'s retry path, `_classify_gh_error`, or the degraded-status codes — that coverage lives in the moved unit tests (row 19's gh-integration counts) and is checked by step 2's test-ID parity instead.
   - Capture stdout, stderr, and exit code for each run below, plus the ledger and export file bytes. Mask `captured_at` and `exported_at`.
     - (a) `pr-cost` in read mode;
     - (b) `pr-cost --record`, with `PR_COST_LEDGER_PATH` outside any git tree;
     - (c) `pr-cost-export --out <scratch>` over two accounts;
     - (d) `workstream-cost --check-pr-status`;
     - (e) `pr-link --branches <b>`, with and without `--repo`;
     - (f) `cost-ledger --record`, which also exercises the machine-identity mint.
   - Record `wc -l` for the shim, the legacy test file, and conftest.
1. **Scoped suite.** Run `<venv>/bin/python3 claude/.claude/scripts/select-tests.py`. The diff touches `claude/.claude/scripts/`, which selects `scripts/tests/`, and `docs/`, which adds `hooks/tests/` and `skills/tests/`. Every selected test passes, including:
   - `test_transcript_analysis_architecture_doc.py`;
   - `test_transcript_workstream_cost.py`, which pins row 17;
   - `test_context_composition.py`, `test_token_analyzer.py`, and `test_analyze_context.py`.
2. **Test parity.** Strip each ID's file prefix and compare sorted lists. Step 0's legacy list must equal the combined post-move lists of the legacy file and the seven new files, duplicates included. The new bootstrap test (M11) and the new `TestMachineIdentity` distinct-directories discriminator test (M9's Stage 2 instruction) are each counted separately, as a second named exception alongside the bootstrap test.
3. **CLI parity.** Every `--help` capture must be byte-identical to step 0's. Every stub-harness capture (a)–(f) must match step 0's on stdout, stderr, exit code, and file bytes, after masking.
4. **Patch reach.** Confirm the moved tests at :20703, :25513, :25769, :26128, and :25849 pass. Then, in scratch only, revert each retarget to its old `_mod` target, one at a time, and confirm that test fails. :25769 and :26128 may pass silently on revert (row 14). If they do, record it in the PR body rather than weakening the check. Then check the new Stage-2 discriminator test's own discriminating power: in scratch, wire `_resolve_machine_identity` to read the shim's `config_dir` binding instead of `ledger_common.config_dir`, and confirm the new test fails; confirm it passes when wired to `ledger_common.config_dir` as specified. This is the check that :25769/:26128's known blind spot (previous paragraph) relies on — an unverified new test would leave that gap open under a different name.
5. **Hook sandbox.** Confirm step 1 ran `claude/.claude/hooks/tests/test_nudge_error_mode_analysis.py`. If it did not, run it alone.
6. **Lint.** `<venv>/bin/ruff check claude/.claude/scripts/`
7. **Leftovers.** List every top-level `def`, `class`, and `NAME =` in the moved spans. After the move, `git grep -nE '^(def |class )?(<names, |-joined>)\b' claude/.claude/scripts/transcript-analysis.py` must return nothing. The indented by-name import lines are excluded by the `^` anchor.
8. **Prefix correctness.** A scratch script parses each new module with `ast`. For every `Attribute(Name(<module>), <name>)` whose module is a `transcript_analysis` package module, it asserts that `<name>` is a top-level binding of that module. F821 cannot catch a wrong but existing prefix, such as `pr_cost_ledger._gh_call_with_backoff`.
9. **Move fidelity.**
   - In scratch copies of the five modules, strip every `(corpus|cost|gh_cli|ledger_common|pr_cost_ledger|pricing|redaction|render|scope)\.` prefix that is followed by `_` or `print_resolved_scope`, and map `print_resolved_scope` back to `_print_resolved_scope`.
   - Drop each module's docstring and import header, and concatenate the rest in source order.
   - Diff the result against the step 0 spans. Only the four row-26 lines may differ.
   - Do the same for the test files: rewrite `_mod.<module>.` back to `_mod.` and diff against the step 0 slices. Only the M7(c) patch lines, the dropped headers, the row-26 fix at :21933, and the new `TestMachineIdentity` distinct-directories discriminator test (M9's Stage 2 instruction) may differ.
   - After the commit, run `git blame -C -C -s` on every new file, put the counts in the PR body, and name the command in the `/code-review` spawn prompts.
10. **Sizes.** Report measured `wc -l` for every new and shrunk file in the PR body, not estimates. A file over 1,000 lines is flagged there as an F1 candidate; it is not split further ad hoc.
11. **This phase's own revert.** In a throwaway worktree, `git revert --no-commit` the squashed commit and diff the result against step 0's pre-dispatch snapshot (shim, legacy test file, conftest). Assert zero diff. This is independent of the adjacent-phase reasoning below: it proves this phase's own commit reverts cleanly, not just that no later phase depends on what it added.

The governing plan's cross-phase revert rehearsal is omitted, distinct from step 11 above. This phase's conftest promotions (`_cost_ledger_args`, `_cost_ledger_row`, `_two_declared_roots`) have their other consumers in `test_transcript_analysis.py`, which this phase already edits, so no adjacent phase's revert depends on them. Read-scope's plan omitted that cross-phase rehearsal on the same reasoning.

## Out of scope

- **The bodies of `cmd_pr_link`, `cmd_workstream_cost`, `cmd_cost_ledger`, and `cmd_spend_over_threshold`.** Only their import sources change.
- **Neutral names for gh messages and constants.** Workstream-cost's gh failures still print a `pr-cost:` prefix, from `_gh_call_with_backoff` and `_pr_cost_abort_on_gh_failure`. `gh_cli.py` also keeps `_PR_COST_RATE_LIMIT_*` and `_PR_COST_GH_PR_LIST_LIMIT`, and `ledger_common.py` keeps its `_COST_LEDGER_*` names. Renaming them would change output or names, so this diff would stop being a pure move.
- **Changing the shim-plus-package shape, moving `build_parser()` early, or relocating `_UNCONDITIONAL_HEADER_CASES`.** Each is reachable, but each is a deliberate decline. The governing plan defers all three to the `cli.py` phase. `[verified: transcript-analysis-decomposition.md:144-161]`
- **Moving `TestComputePrCostBranchTotals` and `TestPrCostDedupBeforePricing` into `test_transcript_cost.py`.** They test `cost.py`'s function in pr-cost's context, and that file is already at 3,972 lines.
- **`config-schema-audit.md`'s line citations (:205, :213, :225), and dated `docs/reports/*` citations.** #1116 assigns the first to the cost-ledger phase. The reports are historical records.
- **A committed, gh-stubbed pr-cost subprocess test.** No in-repo harness fakes gh as a subprocess yet. Verification step 3 covers the path in scratch.
- **An import-direction guard test for the new modules.** Neither the read-scope nor the review-trace phase added one.
- **The stale `transcript_analysis/__init__.py` docstring, and `review_rounds.py`'s line-number citations.** Both are left for the `cli.py` phase.
- **Any CLI surface change** (Verification step 3).
