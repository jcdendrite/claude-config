# Shim-reader retarget (GH-1116)

## Context

Goal: make the transcript-analysis test files read package modules directly instead of through the shim's `# noqa: F401` re-exports, then delete the test-only shim bindings, in two test-only PRs.

Ask: "Don't get caught up on semantics. Do what is correct architecturally and ask staff sdet for guidance" — said after the engineer approved the helper-versus-`cmd_*` split ("I think that split makes sense. What does the architect think?") and the architect replaced it with a split by test file. The tracker (GH-1116) assigns this retarget to the work between the edit-format and instrument-authoring phase and the final `cli.py` phase.

Why now: the edit-format and instrument-authoring phase (PR #1333) left the shim defining only `build_parser()` and `main()` as top-level functions. Roughly 13 test files still load the shim as `_mod` and read about 70 noqa'd re-exports, which keeps the shim a re-export table and would break about 190 `_mod.cmd_*` reads at once when the `cli.py` phase moves `build_parser`. `TestRootsThreadingSpy` also carries shim-level patches that intercept no production call, with stale "either call shape" comments.

Intended outcome: every test file imports the package modules it exercises and keeps `_mod.` only for `main` and `build_parser`; the shim keeps only the bindings `build_parser` reads; collected node IDs and test outcomes are unchanged; and the dead spy patches are gone.

## Approach

Retarget every `_mod.<name>` read in the 13 test files that load the shim, except `_mod.build_parser` and `_mod.main`, to the owning package module's own binding. Then delete the 83 shim import bindings the shim's own code never reads. The work ships as two PRs cut by test file. PR 1 covers `tests/test_transcript_analysis.py`. PR 2 covers the other 12 files, is cut from `main` after PR 1 merges, and lands last. Each PR has two commits: a pure rename that a forward AST replay proves, then the deletions and prose fixes. No production code changes. CLI help and parser dumps stay byte-identical. Collected test IDs and outcomes stay the same, except that PR 2 adds one ID, the new pin.

Line numbers and the aggregate counts in Context, Root, and rows 2 and 12 (83 unread bindings, 1,412 and 1,369 reads) are at this plan's base, HEAD `bec96afd`. PR 1's figures were re-derived at `f1a93927` after three rebases, and a fourth rebase onto `a96fd05b` moved none of them (a read-only recount): 410 reads, 39 deletions (16 `cmd_*` and 23 leaves), and 84 F401 candidates, which leaves PR 2's 45. PR 2's file totals (965 and its per-file counts) are stale and PR 2 recounts them at its own base (Verification 4). Commit 1 adds import lines at the top of each test file, so the commit-2 edits in test files anchor on content, not line numbers.

**Read form (M1).**
- A retargeted read is `<binding>.<name>`, where `<binding>` is the file's own binding of the owning module.
- A new binding is an unaliased `from transcript_analysis import <module>` statement, one per file (row 11).
- A file that already binds the module reuses that binding. `test_author_outcome.py` reuses its `ao` alias (row 10). `test_transcript_review_rounds.py` extends its :15 import line.
- The stdlib passthroughs `_mod.os` and `_mod.tempfile` become `os` and `tempfile`.
- `_mod.build_parser` and `_mod.main` stay. Every loader therefore stays until the `cli.py` phase (G1, row 4).
- The 141 reads of production `cmd_*` bindings also retarget, even though `build_parser` still reads those bindings bare. Left alone, they would all break when `cli.py` moves `build_parser` (row 18).
- Assertion lines change only by these proven reference re-points. Earlier phases read "no edited assertions" the same way (row 20).

**Rename table.** It is derived from the shim's top-level import statements, never from a diff or a grep (row 2):
- `import X` maps `_mod.X` to `X`.
- `from transcript_analysis import M` maps `_mod.M` to M's binding.
- `from transcript_analysis.M import O`, or `… import O as N`, maps `_mod.N` to `<M's binding>.O`. N is O unless aliased. The three aliased leaves therefore map to their real names:
  - `_mod._dedup_turns_by_request_id` → `pricing.dedup_turns_by_request_id`;
  - `_mod._print_resolved_scope` → `scope.print_resolved_scope`;
  - `_mod._resolve_scan_roots` → `scope.resolve_scan_roots`.
- Only `ast.Attribute` nodes whose value is the Name `_mod` are rewritten. A `setattr(_mod, "…")` argument, a string, a docstring, and a comment are left as they are.
- `test_transcript_period_cost.py` has six module-level aliases (:37-42), `period_cost = _mod.period_cost` through `render_module = _mod.render`. They are removed, and their loads then read the module binding directly. `cost_module` becomes `cost` at :2501 and :2885, and the other four names stay as they are. `render_module` has no load, so `render` gets no import (row 9).

**PR 1: `tests/test_transcript_analysis.py`.**
- **Commit 1, the rename.** It retargets 404 of row 5's 410 reads (the six `render._is_fresh_user_prompt` and `render._is_task_notification` reads land in commit 2, per M3) and adds one parenthesized `from transcript_analysis import (…)` statement, which ruff places.
- **Commit 2, the semantic changes.**
  - In `TestRootsThreadingSpy`, delete the two `monkeypatch.setattr(_mod, …)` pairs (:2618-2619, :2659-2660) and the three-line "either call shape" comment below each (:2620-2622, :2661-2663). The `scope` patches that follow (:2623-2624, :2664-2665) patch what production reads (row 15). Verification 7's fail control proves this before the deletion.
  - In the same file, fix each docstring or comment line that spells a retargeted read with `_mod.` (e.g. :133). Change only that spelling.
  - In the shim, delete the 39 bindings whose only reader was this file: 16 test-only `cmd_*` and 23 leaves (row 6).
    - Remove each one's import line and its `# noqa: F401` rationale.
    - Remove the three aliased-import statements, each with the comment line above it (:200-201, :261-262, :264-265).
    - Remove the `transcript_analysis.corpus` block, which loses all four of its names.
    - In each block that keeps a name, remove only the comment sentence that names a deleted one, and rewrap the rest. A block left with one bare-read name takes the shape of the `transcript_analysis.friction_count` block (:137-140).
  - Append `"test_transcript_analysis.py"` to `TEST_FILES`.
  - Make PR 1's doc edits, listed below.

**PR 2: the other 12 files.** The files are `test_transcript_cost.py`, `test_transcript_review_rounds.py`, `test_transcript_cache_rebuild_ttl_rules.py`, `test_transcript_ledger_common.py`, `test_transcript_handoff_signal_response.py`, `test_transcript_pr_cost_gh.py`, `test_transcript_pr_cost_export.py`, `test_transcript_plan_boundary.py`, `test_transcript_cost_counts.py`, `test_transcript_cache_rebuild.py`, `test_author_outcome.py`, and `test_transcript_period_cost.py`.
- **Commit 1, the rename.** It retargets 965 reads (row 5) and removes period_cost's aliases. `test_transcript_ledger_common.py` gains `import os` and `import tempfile` (row 21).
- **Commit 2, the semantic changes.**
  - Make the prose fixes, as in PR 1, in all 12 files.
  - In the shim, delete the remaining 45 bindings: 25 leaves, the 18 package-module bindings other than `scope`, `os`, and `tempfile` (row 6). The shim then has no `# noqa: F401` left.
    - The whole-module block and its comment (:13-41) become `from transcript_analysis import scope`, under the one comment line `# scope is read bare by main(), as scope.PROJECTS_DIR.`
    - The `transcript_analysis.cost` block keeps `cmd_cost` and `cmd_cost_trend`. Its comment becomes `# cmd_cost and cmd_cost_trend are read bare by this file's own build_parser, via p_cost/p_cost_trend.set_defaults.`
    - The `transcript_analysis.pricing`, `.redaction`, and `.render` blocks go entirely. The `transcript_analysis.scope` block keeps its three bare-read names.
  - Append seven files to `TEST_FILES`, in this order: `test_transcript_cost.py`, `test_transcript_review_rounds.py`, `test_transcript_cache_rebuild_ttl_rules.py`, `test_transcript_ledger_common.py`, `test_transcript_pr_cost_gh.py`, `test_transcript_pr_cost_export.py`, and `test_transcript_cache_rebuild.py`. Four of the 12 are already listed. `test_author_outcome.py` stays off the list because its `ao` alias fails the guard's unaliased-import check (row 11).
  - Add the pin.
  - Make PR 2's doc and docstring edits.

**The pin (PR 2, commit 2; M7).** Add one test to `test_transcript_package_module_prefixes.py`, named `test_shim_copy_reads_only_build_parser_and_main`.
- It selects every `tests/test_*.py` that makes a top-level `spec_from_file_location("transcript_analysis", …)` call. The file's shim copy is the target of its top-level `module_from_spec` assignment. The rule skips the by-path `scope.py` copies in `test_transcript_analysis.py` (:82-85, :97-102, :117-122), which load inside functions under other names (rows 22, 29).
- It cross-checks that selection against a second set that does not depend on the spec name. The second set holds every `tests/test_*.py` whose top-level `spec_from_file_location` call takes a path argument that contains the string constant `"transcript-analysis.py"`, either directly or through a Name that a top-level assignment binds to a value containing it.
  - The two sets must be equal. The failure names each file that is in only one of them.
  - Today both sets hold exactly the 13 files (row 36).
  - Equality, rather than containment alone, also fails when the second set comes up empty.
- In each selected file, every `ast.Attribute` on the copy's name reads `build_parser` or `main`. Every other Load of the name is the sole argument of an `exec_module(...)` call.
- It asserts that at least one file is selected and that each selected file makes at least one allowed read. This mirrors the per-file assertion in `test_test_files_each_have_a_resolvable_module_attribute_read` (:437-447), so the pin cannot pass vacuously on any one file.
- Docstring: `"""Every test file that loads a shim copy reads only build_parser and main through it, so no package name needs a re-export in the shim."""`
- In the module docstring, after the sentence ending "going unchecked silently.", add "It also pins every test file that loads a shim copy to reading only `build_parser` and `main` through it." The scope sentence "Scope stays those modules and test files" becomes "Scope stays those modules, those test files, and the shim-loading test files".

**Doc and docstring edits (M9; row 17).** In each PR, every edited sentence is true for that PR's end state. PR 1's committed doc wording supersedes the quoted PR 1 text below where they differ (:57-67, and the :782-784 example name). The commit also removes ", which calls into the shim" from the Tests section, an edit this list missed. PR 2's anchors resolve against the committed text.
- `docs/transcript-analysis-architecture.md`:
  - **:57-67.** PR 1 replaces the text from "The shim also binds `cmd_judgment_pair`" through "not the shim name." with two sentences: "The shim also binds, each with `# noqa: F401` and annotated at its import site, the package modules and leaf names that only shim-loading test files other than `tests/test_transcript_analysis.py` read through their copy. A test that needs to steer a command patches the module whose binding the handler reads at call time, not the shim." PR 2 deletes the first of those two sentences.
  - **:782-784.** PR 1 replaces "It reaches package modules as `_mod.<module>.<name>` (e.g. `_mod.cost._cost_report`, `_mod.redaction._REDACT_MAP_MISS_TOKEN`) — the same channel the shim-reimport exception above relies on." with "`tests/test_transcript_analysis.py` reads only those two names through its copy and imports every other package module it reads directly, as above. The other shim-loading test files reach package modules through their copy, as `_mod.<module>.<name>` (e.g. `_mod.cost._cost_report`) or as a leaf name the shim re-exports." PR 2 replaces those two sentences with "It reads only those two names through its copy and imports every package module it reads directly, as above."
  - **:793-795 (PR 2).** "(`from transcript_analysis import author_outcome`), with a small `spec_from_file_location`-loaded shim copy reserved for the argparse-wiring and `cmd_author_outcome` end-to-end tests." becomes "(`from transcript_analysis import author_outcome as ao`), with a small `spec_from_file_location`-loaded shim copy reserved for the argparse-wiring tests, which read `build_parser`."
  - **:853-859 (PR 2).** Append "It also requires every test file that loads a shim copy to read only `build_parser` and `main` through it."
  - **:964-966 (PR 1).** "reads `cmd_edit_format` and `cmd_instrument_authoring` through the shim." becomes "reads `edit_format.cmd_edit_format` and `instrument_authoring.cmd_instrument_authoring` directly."
- **`tests/test_transcript_analysis_cost_import_direction.py` :7-12 (PR 2).** The docstring's last sentence, from "This guard pins the production import-direction exception only" to its end, becomes "This guard pins the production import-direction exception only, not test files' own `from transcript_analysis import cost` imports, which reach cost.py's private helpers for patching."
- **`tests/test_transcript_cli_bootstrap.py` :9 (PR 2).** "no `_mod.cmd_*`-style test exercises" becomes "no in-process test exercises".

**Decision: cut by test file, not by binding kind (M2).** Whether a binding can be deleted depends on whether the shim's own code reads it bare, not on its kind (rows 2, 6). A cut by kind would touch every file in both PRs and would leave the production `cmd_*` reads for `cli.py` (row 18). PR 1 takes the one file that holds all 47 test-only `cmd_*` reads, the dead patches, and 39 of the deletions. The dead patches are the only non-mechanical test edit. PR 2 lands last because its deletions need every reader gone. It is cut from `main` after PR 1 merges, and Verification 0 checks that its base contains PR 1 (row 38). Stacking PR 2 on PR 1's branch was set aside: PR 1's squash merge would then force a rebase, and the Rebase rule voids every baseline that a rebase spans. A single PR was weighed. The proofs cost the same at any size, but one PR would put the dead-patch change in the same review as 1,375 mechanical edits.

**Decision: read package modules directly, not as `_mod.<module>.<name>` (M1).** The documented convention is the `_mod.<module>.<name>` form (doc :782-784). That form would leave about 670 module reads to edit a second time at `cli.py`. `review_rounds`, `author_outcome`, and `audit_routing` have no module binding in the shim (row 2), so that form would also need new noqa'd re-exports, which contradicts G1's end state. The cost is reversing the documented convention, which both PRs' doc edits carry.

**Decision: two commits per PR (M3).** Commit 1 is a pure rename, so the replay can prove it exactly. PR 1 departs from this in one place: after the third rebase, the six `render._is_fresh_user_prompt` and `render._is_task_notification` retargets sit in commit 2, so commit 1 carries 404 of the 410 reads. The replay proves commit 1 once those six names are rewritten in a scratch copy, and the PR body says so. Commit 2 holds the deletions and prose, which need review attention. A squash merge drops the split, but review and the replay keep it.

**Decision: a scratch replay checker, not a committed tool (M4).** Only the replay catches a wrong-but-existing target or an edit to an assertion that still passes. `nodes --rename` cannot express this table (row 13). A `retarget` subcommand would take `_move_fidelity.py` past the 1,000-line limit (G4, row 14). The checker has two uses, because the `cli.py` phase can prove its own retarget with `nodes` (row 26). Verification 5's seeded defects prove the checker non-vacuous in place of committed self-tests. PR 2 runs a subset of them on its own copies every time, because only PR 2's files reach the alias and aliased-binding paths (row 35). The checker and the codemod come from separate dispatches, so the proof does not just re-run the code it checks.

**Decision: one durable pin (M7).** The plan ends with a convention: shim copies serve only `build_parser` and `main`. No existing check enforces it. ruff F401 stops an unused import. It does not stop a test from reading a production binding (`_mod.cmd_cost`) or `_mod.scope.<name>` through the copy, and the `cli.py` move would then break that read. A text check for `# noqa: F401` in the shim stops re-exports, but not those reads.

**Decision: `test_author_outcome.py` reuses `ao` and stays off `TEST_FILES`.** `author_outcome.` would push five calls past 130 columns (row 10), and `ao` is already the file's own binding. Unaliasing `ao` across the file is left to the `cli.py` phase (Out of scope).

**Decision: delete the dead shim patches, not retarget them.** No shim function body loads `_resolve_project_scope` or `_print_resolved_scope` (rows 3, 15), so those patches intercept nothing.

**Decision: `_UNCONDITIONAL_HEADER_CASES` stays in place.** Only its reads retarget. The governing plan relocates it in the `cli.py` phase (G1).

**Decision: this plan owns the tracker's leaf-class reads.** The reads in `TestRepoTrackedAgentTypeNames`, `TestSanitizeTableCell`, and test_transcript_cost_counts.py:60 retarget along with their files. The leaf-module-tests phase then only has to move those classes (row 19).

**Assumption ledger.**

**Root:** Thirteen test files load `transcript-analysis.py` as `_mod` and make 1,412 `_mod.<attr>` reads. Of those, 1,369 read something other than `build_parser` or `main`. To serve them, the 1,146-line shim keeps 83 import bindings its own code never reads, each under `# noqa: F401`. This plan retargets the 1,369 reads to their owning modules and deletes the 83 bindings. Only `build_parser` and `main` then cross the shim, so the `cli.py` phase inherits a 43-read retarget. `[unverified: this session's AST survey, which is not committed (items 1-2); Verification 4 and 8 recount]`

**Givens:**
- G1. The shim keeps `build_parser()` and `main()` until the `cli.py` phase, and then reduces to a package import and a `__main__` guard. Reason: changing this needs a decision outside this plan, the governing plan's phase order. `[verified: transcript-analysis-decomposition.md:155-161]`
- G2. A monkeypatch reaches code only through the binding that code reads at call time. A by-name import is a snapshot of the binding taken at import time. Reason: Python import semantics. `[unverified — relayed from edit-format-and-instrument-authoring.md G3]`
- G3. Agents run `select-tests.py` rather than the full suite, except in CLAUDE.md's two named cases. Reason: changing this needs an edit to CLAUDE.md § "Commands". `[verified: CLAUDE.md "Commands"]`
- G4. Code files are capped at 1,000 lines. This is a recorded decision that no check enforces yet. Reason: changing it needs a decision outside this plan. `[verified: docs/design-decisions/code-file-line-limit.md:19, :50-57; Glob found no enforcing test]`

**Rows:**
1. The engineer approved a split and asked for the architect's view: `[engineer-verified: "I think that split makes sense. What does the architect think?"]` The engineer then delegated the design and asked for staff-sdet's guidance: `[engineer-verified: "Don't get caught up on semantics. Do what is correct architecturally and ask staff sdet for guidance"]` The by-file split, the read form, the commit shape, the checker, and the pin are this plan's own calls under that delegation. `[unverified]`
2. The shim has 142 top-level bindings. Its own code never reads 83 of them bare:
   - 18 of its 19 package-module bindings (only `scope` is read, at :1141);
   - 16 test-only `cmd_*`;
   - 47 leaves;
   - `os` (:8) and `tempfile` (:10).

   Three leaves are aliased (:201, :262, :265). `review_rounds`, `author_outcome`, and `audit_routing` have no module binding in :21-41. `[verified: read of shim :1-326, :1105-1146]` `[unverified for the counts: survey item 1]`
3. The shim defines exactly two functions, `build_parser` (:326) and `main` (:1105). No shim function body therefore reads a by-name leaf. `[verified: read; survey item 1]`
4. Every one of the 13 files reads `_mod.main` or `_mod.build_parser`, 43 reads in all. `[unverified: survey item 2]`
5. Reads to retarget per file, each file's total minus its `build_parser` and `main` reads:
   - PR 1: `test_transcript_analysis.py` 410.
   - PR 2 (965 in all): `test_transcript_cost.py` 389, `_review_rounds` 91, `_cache_rebuild_ttl_rules` 84, `_ledger_common` 82, `_handoff_signal_response` 76, `_pr_cost_gh` 62, `_pr_cost_export` 62, `_plan_boundary` 44, `_cost_counts` 35, `_cache_rebuild` 16, `test_author_outcome.py` 14, and `_period_cost` 10, six of which sit on the alias lines.

   `[unverified: survey item 2; Verification 4 recounts]`
6. Deletion partition:
   - **PR 1 (39):**
     - 16 `cmd_*`: `cmd_buckets`, `cmd_cache_efficiency`, `cmd_commit_gate`, `cmd_context_composition`, `cmd_context_distribution`, `cmd_duration`, `cmd_edit_format`, `cmd_fail_seq`, `cmd_instrument_authoring`, `cmd_judgment_pair`, `cmd_pr_link`, `cmd_sessions`, `cmd_skill_invocation`, `cmd_skill_pair`, `cmd_struggle`, `cmd_user_input`.
     - 23 leaves:
       - corpus: `SUBAGENT_SUBDIR`, `_parse_ts`, `_read_session_file_partitioned`, `iter_sessions`;
       - cache_efficiency: `_cache_efficiency_report`;
       - pricing: `_cache_miss_reason`, `_dedup_turns_by_request_id`;
       - redaction: `_project_family`, `_repo_tracked_agent_type_names`;
       - render: `_content_text`, `_fam`, `_is_fresh_user_prompt`, `_is_task_notification`, `_sanitize_table_cell`, `_strip_task_notifications`;
       - scope: `_iter_scoped_sessions`, `_parse_absolute_window_args`, `_parse_since_nd_arg`, `_repo_scoped_project_slugs`, `_resolved_scope_header`, `_scan_root_transcripts`, `_print_resolved_scope`, `_resolve_scan_roots`.
   - **PR 2 (45):**
     - 25 leaves:
       - cost: `_accumulate_per_account_turn`, `_cost_report`, `_cost_trend_report`, `_print_branch_exclusion_diagnostic`, `_print_model_id_table`, `_print_thread_table`, `_print_token_class_table`;
       - pricing: `_TOKEN_CLASSES`, `_cache_write_split`, `_context_at_turn`, `_context_bucket`, `_context_window_for_model`, `_model_rates`, `_price_turn`;
       - redaction: `_BUILT_IN_AGENT_TYPES`, `_REDACT_MAP_MISS_TOKEN`, `_build_redact_map`, `_corpus_fingerprint`, `_redact_proj_label`, `_redact_session_id`;
       - render: `_fmt_date`;
       - scope: `_DO_NOT_PUBLISH_BANNER`, `_redaction_ordinals`, `_resolve_cost_roots`, `_resolve_project_scope`.
     - 18 modules: `cache_rebuild`, `cache_rebuild_rules`, `corpus`, `cost`, `cost_ledger`, `gh_cli`, `handoff_nudge`, `handoff_signal_response`, `ledger_common`, `period_cost`, `plan_boundary`, `pr_cost`, `pr_cost_export`, `pr_cost_ledger`, `pricing`, `redaction`, `render`, `subagent_mix`.
     - `os` and `tempfile`.
   - A leaf read by both the legacy file and a PR 2 file waits for PR 2.

   `[unverified: survey item 3; Verification 8 recomputes]`
7. Snapshot reads. The survey found 4 same-function pairs of a by-name `_mod.<leaf>` read and a patch of its owner attribute, all read-before-patch: legacy :2606/:2623 and :2647/:2664, and cost :1549/:1558 and :1572/:1581. `[unverified: survey item 4; the checker recomputes them, including the aliased rows the survey's name match missed]` conftest.py patches only `PROJECTS_DIR`, `config_dir`, `_cost_ledger_path`, `_usage_drift_warned`, and `_subagent_format_drift_detected`, and no by-name shim binding copies any of them. `[verified: grep of conftest.py setattr calls]`
8. Shadowing:
   - In the legacy file, `sessions` is a local in 18 test methods, each time a plain assignment (:2764-:3363). The one `_mod.cmd_sessions` read is at module level, in `_UNCONDITIONAL_HEADER_CASES` (:2423).
   - In period_cost, `cost` is a comprehension variable at :4079. Neither `cost_module` load is in that scope.

   `[verified: grep]` The survey found no other local that collides with a module name the retarget introduces. `[unverified: survey item 5; the checker's shadow precondition re-checks]`
9. period_cost's aliases are at :37-42. `cost_module` is loaded only at :2501 and :2885, and `render_module` is never loaded. period_cost is already in `TEST_FILES` (:70). After the swap, its 325 module-attribute reads come under the attribute check for the first time. `[verified: grep]`
10. `test_author_outcome.py` binds `author_outcome as ao` (:13). Its `_mod.cmd_author_outcome(type("A", …)())` calls at :3246, :3357, :3619, :3648, and :3703 are about 126 columns wide. `author_outcome.` would make them about 136 columns, and `ao.` about 124, against ruff's limit of 130. `[verified: grep; pyproject.toml:2]` `[unverified: the column counts are by hand]` Staff-sdet's plan review measured those five lines at 126 columns, and found no line over 130 columns after simulating the rename in all 13 files. `[unverified — relayed model output]`
11. A file in `TEST_FILES` may not bind a package module through an alias (test_transcript_package_module_prefixes.py:185-212, :406-417). A file that loads the shim is not required to be listed (:484-495). `TEST_FILES` has 51 entries in insertion order (:55-107), and 4 of the 13 files are among them. `[verified: read]`
12. ruff runs with line length 130 and rules E, F, B, I, UP, and SIM. RUF100 is not selected, so ruff does not flag a stale `# noqa`. `[verified: pyproject.toml:2, :6]` `requirements-dev.txt` pins `ruff==0.6.*`, and every `noqa` in the shim is a `noqa: F401`. `[verified: requirements-dev.txt:3; grep]` Staff-sdet's plan review ran `ruff check --no-cache --select F401 --ignore-noqa` on the base shim under ruff 0.6.9 and got 83 findings, matching row 2. `[unverified — relayed model output, not re-run this session; Verification 8 runs it and stops if ruff rejects the flag]`
13. `nodes --rename` cannot gate this change. It exits 2 when two entries share a NEW name (_move_fidelity.py:282-284). It reports `rename-ambiguous` when a NEW name already occurs in the node (:337-340). The legacy file reads both `_mod._resolve_project_scope` (:2606) and `_mod.scope._resolve_project_scope` (:2623), and both become `scope._resolve_project_scope`. `[verified: read]`
14. `_move_fidelity.py` is 961 lines long. It runs as a script with no relative imports, and its self-tests import it as `from ._move_fidelity import main`. A new subcommand would take it past G4's limit. A sibling module could share its helpers only through a bare sibling import, which `TestNoBareSameDirectorySiblingImports` fails. `[verified: read of :1-34 and :951-961; _move_fidelity_helpers.py:6; .claude/rules/test-tree-packaging.md]`
15. In `TestRootsThreadingSpy`:
    - the shim patches (:2618-2619, :2659-2660) patch names that no shim function reads (row 3);
    - the `scope` patches (:2623-2624, :2664-2665) patch what production reads;
    - the `real_*` captures (:2606-2607, :2647-2648) come before every patch;
    - the tests assert the spies ran (non-empty at :2629-2630, index `[-1]` at :2669-2670).

    `[verified: read]`
16. test_transcript_review_rounds.py:3464 compares `args.func` to `_mod.cmd_review_round_cost`, the same binding the shim's own `set_defaults` reads (:229-233), so it cannot fail today. Legacy :1676 and :1681 compare the registrar's handler to the shim's test-only import of that handler (:56-63). After the retarget, all three compare against the owning module. `[verified: grep; read]`
17. The retarget makes this text false or vacuous:
    - architecture doc :57-67, :782-784, :793-795, :853-859, and :964-966;
    - shim :13-20 and the per-block comments;
    - test_transcript_analysis_cost_import_direction.py:7-12;
    - test_transcript_cli_bootstrap.py:9;
    - docstrings and comments in the 13 files that spell a retargeted read with `_mod.` (e.g. test_transcript_analysis.py:133).

    No package module, `docs/*.md` file other than the architecture doc, or rule file names a `_mod.` read. `[verified: grep; read]`
18. If the 141 production `cmd_*` reads waited for `cli.py`, they would all break once `build_parser` moves, because the shim would no longer bind them. Keeping them readable would mean re-adding noqa'd bindings, which contradicts G1's end state. `[unverified: the count comes from survey item 2; the consequence is derived]`
19. GH-1116's tracker assigns the reads in `TestRepoTrackedAgentTypeNames` (:632), `TestSanitizeTableCell` (:759), and test_transcript_cost_counts.py:60 to the leaf-module-tests phase. `[unverified — relayed from this session's consult return; the tracker body was not read]`
20. Earlier phases read "no edited assertions" mechanically: references may be re-pointed, and nothing else on an `assert` line changes. `[verified: read-scope-decomposition.md:222, pr-cost-decomposition.md:249]`
21. Outside the loader lines, the only bare `_mod` uses are the four `setattr(_mod, …)` lines. No test patches by a dotted string. `test_transcript_ledger_common.py` is the only file that reads `_mod.os` or `_mod.tempfile` without importing them. `[unverified: survey items 2, 6, and (a)]`
22. All 13 files load the shim with `spec_from_file_location("transcript_analysis", …)` plus `module_from_spec`, without registering it in `sys.modules`. Inside three functions, the legacy file also loads `scope.py` by path under other names (:82-85, :97-102, :117-122). `[verified: read of legacy :61-68 and :80-122, and period_cost :28-35; grep of the loader comment in all 13]`
23. The checker needs Python 3.12 for exact source positions inside f-strings. `[unverified: PEP 701 was not read; Verification 0 checks for 3.12 through tokenize.FSTRING_MIDDLE]`
24. Neither PR adds a file under `claude/.claude/`, so `require-stow-reminder.sh` does not apply. Neither adds a CHANGELOG entry. `[unverified — relayed from edit-format-and-instrument-authoring.md row 37]`
25. Governing plan item 7 requires a revert rehearsal at each phase boundary: `git revert` the phase's merge commit on a scratch branch, then run the full suite. `[verified: transcript-analysis-decomposition.md:276-279]` Each earlier waiver covered one PR. `[verified: edit-format-and-instrument-authoring.md row 30]` Asked whether to waive the rehearsal for both retarget PRs, the engineer chose to waive it for both. `[engineer-verified: "Waive for both PRs (Recommended)"]` Verification therefore has no revert step.
26. The `cli.py` phase's own retarget (`_mod.build_parser` and `_mod.main` to `cli.<name>`) has injective new names, so `nodes --rename` can prove it. The replay checker therefore has no third use. `[unverified: derived from row 13]`
27. `select-tests.py` maps any change under `claude/.claude/scripts` to the scripts tests. `[unverified — relayed from context-and-cache-commands.md row 37]`
28. Deleting an import the shim's code does not read cannot change the parser or the help text, because `build_parser` reads only bare names, and those stay. Each package module imports cleanly as the first import of a fresh interpreter, so no module depends on the shim having imported another first. `[verified: test_transcript_analysis_import_cycles.py:1-2; row 3]`
29. Today, the pin's selection rule picks exactly the 13 files. `[unverified: derived from row 22 and from the other spec_from_file_location callers under tests/ (test_token_analyzer.py, test_analyze_context.py, test_select_tests.py, test_post_crash_sessions.py), none of which uses that spec name; Verification 11 runs the pin]`
30. Staff-sdet's plan-stage guidance, relayed by the session, is the source of: the forward replay; the identity, snapshot, and shadow checks; the seeded controls; the dead-patch control; the separate deletion commit; and the pin. `[unverified — relayed model output; none of its scratch-derived numbers is relied on]` Its round-1 plan review is the source of: the PR 2 checker controls; the `store` and `import-comments` controls; the pin's cross-check and per-file assertion; the prose greps outside the test files; the F401 end-state check; stopping on E501; and the PR 1 precondition for PR 2. `[unverified — relayed model output; rows 10, 12, and 35-38 re-ground what this plan relies on]`
31. The two-PR count is the session's Context framing, not the engineer's words. `[unverified]`
32. Neither PR changes a conftest.py line, so neither can cause the hazard governing plan item 7 names, a conftest fixture promotion that leaves an earlier PR's revert uncollectable. `[verified: this plan's Critical files; grep found no \b_mod\b in any conftest.py under claude/.claude/scripts/tests; transcript-analysis-decomposition.md:278-279]`
33. Asked whether the new pin test stays in this plan, though the Ask does not name a standing guard, the engineer chose to keep it. `[engineer-verified: "Keep the pin (Recommended)"]`
34. Asked whether the 8 `TEST_FILES` additions stay in this plan, though the retarget does not require them, the engineer chose to keep them. `[engineer-verified: "Keep the additions (Recommended)"]`
35. Only PR 2's files reach two of the checker's paths:
    - The only top-level `<name> = _mod.<attr>` statements in the 13 files are period_cost's six aliases (:37-42).
    - The only aliased package import is test_author_outcome.py:13 (`author_outcome as ao`). Its :14 also binds `corpus`.
    - PR 1's controls run on the legacy file, which has neither.
    - period_cost's `cost_module` load at :2501 is at module level.
    - test_transcript_ledger_common.py reads `_mod.os` (:195, :512) and `_mod.tempfile` (:492), and imports neither module.

    `[verified: grep; read of test_author_outcome.py:1-40]`
36. Pin signals:
    - Each of the 13 files binds `_SCRIPT = Path(__file__).parent.parent / "transcript-analysis.py"` at top level and passes it to a top-level `spec_from_file_location("transcript_analysis", _SCRIPT)` call.
    - No other `tests/test_*.py` makes a top-level `spec_from_file_location` call whose path names that file. No test file imports a `_mod` from another module.
    - The 13 files read `_mod.build_parser` or `_mod.main` on 1 to 18 lines each. `test_transcript_cache_rebuild.py` reads them on one line.
    - `test_test_files_each_have_a_resolvable_module_attribute_read` (:437-447) asserts at least one read per `TEST_FILES` file. `_calls_spec_from_file_location` (:470-481) counts any spec call as loading a copy.

    `[verified: grep; read of test_transcript_package_module_prefixes.py:437-495]`
37. The shim and the architecture doc spell `_mod` only in prose:
    - The shim spells `_mod` as a word only in comments within :8-317: the `os` and `tempfile` lines, the whole-module block's comment (:15, :18), the cost block's comment (:100), each test-only `cmd_*` block's comment, and the rationale on each noqa'd leaf outside the cost block.
    - Shim identifiers such as `_print_model_id_table` (:108) hold `_mod` as a substring, so a shim grep needs word boundaries.
    - The architecture doc spells `_mod.` only at :62, :783, and :855.

    `[verified: grep]` After PR 1, the shim's word-bounded `_mod` lines are the 23 that Verification 10 lists. `[unverified: derived from row 6 and the grep]`
38. PR 2's checks need PR 1 in their base. At base, the legacy file reads `_mod._resolve_project_scope` on 13 lines and holds the four `setattr(_mod, …)` lines. `test_transcript_analysis.py` appears nowhere in test_transcript_package_module_prefixes.py. PR 1 removes those reads and lines, and adds that `TEST_FILES` entry. `[verified: grep; row 15]` On a base without PR 1, Verification 8's set equality and its empty `(getattr|…)` grep fail, and the pin fails on the legacy file, each under a misleading cause. `[unverified: derived]`

**Mechanisms:**
- M1: Direct `<binding>.<name>` reads, with unaliased new imports and existing bindings reused. `anchors: row2, row10, row11, row18`. The `_mod.<module>.<name>` alternative is set aside in its Decision above.
- M2: Two PRs cut by test file, with PR 2 cut from `main` after PR 1 merges and landing last. `anchors: row4, row6, row31, row38`.
- M3: Two commits per PR, a pure rename followed by the semantic changes. `anchors: row13, row15, row20`.
- M4: A scratch forward-replay checker, written by its own dispatch, with identity, shadow, and snapshot checks, and with a control subset that PR 2 runs on its own copies. `anchors: row5, row7, row8, row13, row14, row23, row26, row30, row35`. Each lighter primitive fails:
  - `nodes --rename` rejects this table (row 13).
  - `remainder` cannot apply a rename. Every changed statement would have to be declared `--changed`, and so would go unproven (row 13).
  - Test parity, the prefix guard, and ruff together still pass a wrong target that exists, and a flipped assertion that still passes (row 11).
  - A committed `_move_fidelity.py` subcommand breaks G4 for a tool that has two uses (rows 14, 26).
- M5: The deletion set comes from ruff F401 with noqa ignored plus a `git grep` reader scan, with a probe of the base tree. The same ruff run checks each PR's end state as a set: PR 2's 45 bindings after PR 1, and none after PR 2. `anchors: row6, row12, row21`.
- M6: Eight `TEST_FILES` additions, with `test_author_outcome.py` held back. `anchors: row9, row11`.
- M7: One pin that limits shim-copy reads to `build_parser` and `main`, with its selection cross-checked by the shim path and a per-file read assertion. `anchors: root, G1, row22, row29, row30, row36`. Each lighter primitive fails:
  - ruff F401 alone allows `_mod.cmd_cost` and `_mod.scope.<name>` reads (row 12).
  - A `noqa: F401` text check on the shim stops re-exports but not those reads.
  - No pin leaves the `cli.py` move's retarget surface unguarded until that phase.
- M8: The dead shim patches are deleted behind a fail control. `anchors: row3, row15`.
- M9: Doc and prose edits that keep each PR's text true, checked by greps with expected hits. `anchors: row17, row37`.
- M10: Dispatches K, R1, D1, R2, and D2, sequenced within each PR, with K running in parallel with R1. `anchors: row5, row14`.
- M11: The selected suite runs as two passes, split by the `timing` marker. `anchors: G3, row27`.
- M12: Capture parity of help and parser dumps for the shim deletions. `anchors: row28`.

## Critical files

**Dispatches.** Every dispatch is to `code-writer`. Each returns to the parent, which runs the named Verification steps and makes the commit. Within a PR the dispatches run in sequence, because commit 1's output is commit 2's input. PR 2 uses one rename dispatch for all 12 files. The codemod and the table rule are shared background, so splitting that dispatch would restate them in every brief (plan-it Step 5).
- **K, the replay checker (once, at PR 1's start).**
  - It writes `<toolbox>/retarget_check.py` to Verification 2's spec, and nothing else.
  - It touches no repository file, so it runs in parallel with R1.
  - PR 2 reuses the file. K is re-dispatched only if `<toolbox>` is gone, and Verification 5's controls (a)-(k) then run again. Its controls (l)-(o) run in PR 2 either way.
  - Its verification: `scripts/venv-run.sh python3 <toolbox>/retarget_check.py --help` exits 0.
- **R1 and R2, the rename (commit 1).** R1 covers `test_transcript_analysis.py`. R2 covers the 12 PR 2 files. The brief requires:
  - Derive the table from the shim at HEAD, per Approach "Rename table". If any shim import binds a name from somewhere other than `transcript_analysis`, `transcript_analysis.<module>`, or the stdlib, stop and report.
  - Write a scratch codemod, `<toolbox>/retarget_apply.py`. R2 reuses it if it is present.
    - It rewrites each qualifying `ast.Attribute` by its source span. It takes UTF-8 byte offsets from `lineno`, `col_offset`, `end_lineno`, and `end_col_offset`, and works right to left within a line.
    - In R2, it also removes period_cost's aliases.
    - Never retype a read. Do not read `<toolbox>/retarget_check.py`.
  - Add each file's imports per Approach "Read form". Place them with `scripts/venv-run.sh ruff check --select I001 --fix <file>`, and apply no other ruff fix.
  - Run `scripts/venv-run.sh ruff check claude/.claude/ claude-skills/ plugins/ evals/`.
  - Stop and report on any of these:
    - a `_mod.<attr>` that is neither a table row nor `build_parser` or `main`;
    - a read inside a function, lambda, comprehension, or class body that binds the target's root name;
    - a Store or Del of a `_mod.<attr>`;
    - any E501. Do not reflow the line; the parent decides (row 10).
  - Touch no comment, docstring, string, `setattr(_mod, …)` argument, or other file. Do no staging and no commits.
  - Run every tool through `scripts/venv-run.sh`. If the wrapper reports a tool missing, stop and return.
  - Return the per-file replaced counts, the import statements added, and the codemod's path.
- **D1 and D2, the deletions and prose (commit 2).** The parent passes in the deletion set from Verification 8 and the `prose:` lists from Verification 4. The brief requires:
  - Make exactly the commit-2 edits in Approach for that PR, anchored on content, and leave every other line unchanged.
  - Stop and report if ruff F401 flags a shim import outside the deletion set, or if F821 flags anything.
  - Do no staging and no commits.
  - Return each deleted binding, each prose edit as `file:line old → new`, and `wc -l` for every changed file.

**Files, PR 1:**
- **Modify:**
  - `claude/.claude/scripts/tests/test_transcript_analysis.py`;
  - `claude/.claude/scripts/transcript-analysis.py`;
  - `claude/.claude/scripts/tests/test_transcript_package_module_prefixes.py`;
  - `docs/transcript-analysis-architecture.md`.
- **Also in the diff:** `.claude/plans/shim-reader-retarget.md`.

**Files, PR 2:**
- **Modify:**
  - the 12 files Approach "PR 2" names, all under `claude/.claude/scripts/tests/`;
  - `claude/.claude/scripts/transcript-analysis.py`;
  - `claude/.claude/scripts/tests/test_transcript_package_module_prefixes.py`;
  - `claude/.claude/scripts/tests/test_transcript_analysis_cost_import_direction.py`;
  - `claude/.claude/scripts/tests/test_transcript_cli_bootstrap.py`;
  - `docs/transcript-analysis-architecture.md`.
- **Also in the diff:** the plan, only if it is revised.

Neither PR creates a file.

**Parent sequence, per PR.** The parent runs these steps in order:
1. Verification 0 and 1.
2. R, with K alongside it in PR 1.
3. Verification 4 and 5, then `/code-review`, then commit 1 (Verification 6).
4. Verification 7 (PR 1 only) and Verification 8.
5. D, then Verification 10 and 11.
6. `/code-review`, then commit 2, then Verification 12, which ends with `/ready-for-review`.

A review-round edit to commit 1's files re-runs Verification 4. A review-round edit after commit 2 re-runs Verification 10.

**Rebase rule.** A rebase or merge that brings in a change under `claude/.claude/scripts`, `claude/.claude/hooks`, or `docs/transcript-analysis-architecture.md` invalidates every snapshot, junit baseline, capture, count, and deletion set. All of them are keyed to the pre-rebase tree. Verification 0 and 12 detect such a change.
- Before commit 1: run the Abort procedure, rebase, and restart at Verification 0 with a fresh `<scratch>`.
- After commit 1: stop and report to the engineer.

**Abort procedure.** If a dispatch is interrupted, the code-writer cleans nothing up. The parent recovers:
1. `git status --short` must list only this PR's Critical files. On any other path, stop and report without touching anything.
2. Restore each listed file with its own literal `git restore --source=HEAD -- <path>` call.
3. On success, `git status --short` lists at most the plan.

Never use `git clean`, `git checkout .`, `git reset --hard`, or `git stash`.

**Explicitly unchanged:**
- `tests/conftest.py` and `tests/_move_fidelity.py`;
- every package module under `claude/.claude/scripts/transcript_analysis/`;
- `select-tests.py` and `docs/transcript-analysis.md`;
- every skill, hook, and rule file, and CHANGELOG.md;
- every preserved record (row 17).

**Reuse:**
- `_move_fidelity.py`'s `capture --all-help`, `compare-captures`, and `test-ids`, unchanged;
- `_move_fidelity.py`'s `module:name[:asname]` import-triple shape (`_import_triples`), for the checker's import comparison;
- the prefix test's existing `TESTS_DIR`;
- the `transcript_analysis.friction_count` block shape at shim :137-140.

**PR body carries:**
- `Refs GH-1116`.
- Each Verification result with its counts: replaced reads per file, deleted bindings, prose edits, and test counts.
- PR 2 only: before this change, test_transcript_review_rounds.py:3464's `args.func` assertion compared against the binding the shim's own `set_defaults` reads, so it could not fail. It now compares against `review_rounds.cmd_review_round_cost` (row 16).
- This plan retargeted the reads in the tracker's leaf classes, so the leaf-module-tests phase only moves those classes (row 19).
- The rollback statement: "A plain `git revert` of PR 2's squash commit restores its 45 bindings and its 12 files' reads together. PR 1's revert applies cleanly only while PR 2 is unmerged, because PR 2 deletes bindings that PR 1's restored reads need, such as `_cost_report` and `os`. Revert last-in-first-out. This is an inference; no revert was run."
- No CHANGELOG entry is added, and no file is added under `claude/.claude/` (row 24).
- The engineer waived the revert rehearsal for both PRs, and neither PR changes a conftest.py line (rows 25, 32).
- The Out of scope follow-ups, listed in the body only.

## Verification

Run everything from the worktree root. Notation:
- `<toolbox>` is one `mktemp -d` directory, created at PR 1's start and reused by PR 2 while it exists. `<scratch>` is a fresh `mktemp -d` per PR, and `<attempt>` a fresh one per dispatch attempt.
- `<chk>` is `scripts/venv-run.sh python3 <toolbox>/retarget_check.py`. `<mf>` is `scripts/venv-run.sh python3 claude/.claude/scripts/tests/_move_fidelity.py`.
- `<shim>` is `claude/.claude/scripts/transcript-analysis.py`, `<sd>` is `claude/.claude/scripts`, and `<tests>` is `claude/.claude/scripts/tests`.
- The junit set is these files:
  - the 13 shim-loading files;
  - under `<tests>`: `test_transcript_package_module_prefixes.py`, `test_transcript_analysis_architecture_doc.py`, `test_transcript_analysis_import_cycles.py`, `test_transcript_cli_bootstrap.py`, and `test_transcript_analysis_cost_import_direction.py`;
  - `claude/.claude/hooks/tests/test_nudge_error_mode_analysis.py`.
- A junit run into `<x>` is two `scripts/venv-run.sh pytest` passes over the junit set. The first is `-m "not timing" --junitxml=<x>-parallel.xml`, and the second is `-m timing -n0 --junitxml=<x>-timing.xml`.

Run each git command and each capture as its own literal call. Write seed and control files with the Write tool.

0. **Preconditions, each PR.**
   - `scripts/venv-run.sh python3 -c "__import__('tokenize').FSTRING_MIDDLE"`, `scripts/venv-run.sh pytest --version`, and `scripts/venv-run.sh ruff --version` each exit 0. The first proves Python 3.12+ (row 23).
   - `git fetch origin main`, then `git diff --name-only HEAD...origin/main -- claude/.claude/scripts claude/.claude/hooks docs/transcript-analysis-architecture.md`. Any output invokes the Rebase rule.
   - `git status --short` shows nothing but the plan.
   - PR 2 only: the base contains PR 1 (row 38).
     - `git grep -n "_mod\._resolve_project_scope" -- <tests>/test_transcript_analysis.py` prints nothing.
     - `git grep -n "test_transcript_analysis\.py" -- <tests>/test_transcript_package_module_prefixes.py` hits PR 1's `TEST_FILES` entry.
     - If either fails, stop and report that PR 1 is not in this base.
1. **Baseline, each PR, before R.**
   - Run `git rev-parse HEAD` and record the output as `<base>`.
   - Copy every file the PR modifies to `<scratch>/before-<basename>`, each with its own `cp` call.
   - Create an empty `<scratch>/account/projects/` directory.
   - Run `<mf> capture --script <shim> --account <scratch>/account --all-help --out <scratch>/before-a`, then the same into `before-b`. `<mf> compare-captures <scratch>/before-a <scratch>/before-b` must be clean, which proves the capture is deterministic.
   - Do a junit run into `<scratch>/before`. Every test passes.
2. **Checker spec, for dispatch K.**
   - **Usage:** `retarget_check.py --shim FILE [--scripts-dir DIR] --after FILE (--before FILE | --prose-only) [--base NAME] [--keep NAME...]`. `--base` defaults to `_mod`, `--keep` to `build_parser main`, and `--scripts-dir` to the directory `--shim` is in. It exits 2 on Python older than 3.12.
   - **Table:** built from `--shim`'s top-level imports, per Approach "Rename table".
   - **Bindings:** built from `--after`'s top-level imports: the local name each import binds for a stdlib module or a `transcript_analysis` module.
   - **Sites:** every `ast.Attribute` in `--before` whose value is the Name `<base>`.
     - A kept attribute is skipped, and a table attribute is a site.
     - Any other attribute is an `unmapped` finding.
     - A site in a Store or Del context is a `store` finding. The replay still rewrites it.
   - **Aliases:**
     - A top-level single-target `Assign` whose value is a module-row site is dropped from the replay.
     - Every Load of the alias's target name is rewritten to that row's binding.
     - Rebinding the target name anywhere else is an `alias-store` finding.
   - **Preconditions:**
     - `import-missing`: a used row whose module has no binding in `--after`.
     - `shadow`: a site or alias load whose target's root name is also bound in some enclosing scope. The scopes are any enclosing function, lambda, comprehension, or class body, and module level. An import in `--after` and a dropped alias statement do not count.
       - The binding forms that count are assignment, augmented or annotated assignment, `for`, `with`, `except`, the walrus operator, parameters, imports, `def` and `class` names, and `global` or `nonlocal`.
     - `snapshot`: a site of a by-name row (`_mod.N` → `<M>.O`) whose innermost function also holds a patching call.
       - The call's first argument is `<base>`, `<base>.<M's shim name>`, or `<M's binding>`.
       - Its second argument is the string N when the first is `<base>`, and the string O otherwise.
       - The finding fires when that call sits on an earlier line than the site, or in a loop the site is also in.
       - Such a call inside any function decorated `pytest.fixture` or `pytest.fixture(...)` fires for every site of that row.
   - **Replay:** replace each site's span by UTF-8 byte offsets, rewrite the alias loads, and drop the alias statements.
   - **Comparisons, of the replayed `--before` against `--after`:**
     - `ast`: the `ast.dump` of each module, with every top-level `Import` and `ImportFrom` removed. It reports each side's line for the first top-level statement that differs.
     - `comments`: the ordered COMMENT token texts outside top-level import statements.
     - `import-comments`: the ordered comments on import-statement lines.
     - `imports`: each import triple that `--after` adds must be a binding that the sites and alias loads need and `--before` lacked. No triple may be removed. A needed binding that `--after` lacks is reported only as `import-missing`, so each defect has one class.
   - **`identity`:** load `--shim` with `spec_from_file_location` under a name absent from `sys.modules`, with `--scripts-dir` first on `sys.path`. For each used row, `getattr(shim, N)` must be the row's target object: `importlib.import_module`, then `getattr` for O.
   - **`prose`:** an informational line for each STRING or COMMENT token in `--after` that contains `<base>.` followed by a table name. It never affects the exit status. `--prose-only` runs only this check.
   - **Output:**
     - One line per finding, prefixed by its class.
     - Then a summary: `replaced: N; alias-statements-removed: A; alias-loads-renamed: L; rows-used: R; prose: P`. `L` counts only loads whose name changes.
     - Exit 0 when clean, 1 on any finding, and 2 on bad input.
3. **Rename dispatch R**, per Critical files, with a fresh `<attempt>`.
4. **Rename proof.** This proves three things: only table reads changed, each new read is the same object as the old one, and no read changes meaning because of a patch or a local shadow.
   - For each file R touched, `<chk> --shim <shim> --before <scratch>/before-<file> --after <tests>/<file>` exits 0.
   - In PR 1, commit 1 leaves the six `_mod._is_fresh_user_prompt` and `_mod._is_task_notification` reads in place (M3). For `test_transcript_analysis.py`, copy the file to `<scratch>/after-<file>`, rewrite those six reads to `render.<name>` in the copy, and pass the copy as `--after`. `replaced` is then row 5's 410.
   - `replaced` equals the file's count in row 5. For period_cost, `replaced` is 4, `alias-statements-removed` is 6, and `alias-loads-renamed` is 2. A mismatch stops the run, and the parent reports both numbers.
   - Save each file's `prose:` lines to `<attempt>/prose-<file>.txt` for D.
   - `scripts/venv-run.sh ruff check claude/.claude/ claude-skills/ plugins/ evals/` is clean.
   - Do a junit run into `<attempt>/c1`. `<mf> test-ids --before <scratch>/before-parallel.xml --before <scratch>/before-timing.xml --after <attempt>/c1-parallel.xml --after <attempt>/c1-timing.xml` must be clean.
5. **Checker controls.** Each control seeds one defect into copies in a fresh `<attempt>/ctl-<x>/` directory, then runs `<chk>` on them. Each must exit 1 with only its named finding class. An exit of 0, or a different class, is a checker defect: stop.
   - Controls (a)-(k) run in PR 1, and in PR 2 only if K ran again.
   - Controls (l)-(o) run in PR 2 every time, on PR 2's own copies, because only PR 2's files carry top-level alias statements and an aliased package binding (row 35).
   - (a) In the after copy, change one site's binding to another imported module that binds the same name. Expect `ast`. The prefix guard and pytest are not expected to catch this, because the object exists.
   - (b) In the after copy, flip one assertion's `==` to `!=`. Expect `ast`.
   - (c) Edit one word of a docstring in the after copy, and expect `ast`. In a separate run, edit one word of a comment, and expect `comments`.
   - (d) In the after copy, revert one site to its `_mod.` spelling, using an f-string site if there is one. Expect `ast`.
   - (e) Add one unneeded module to the after copy's import statement. Expect `imports`.
   - (f) In both copies, insert `<root> = None` as the first statement of a function that holds a site. Expect `shadow`.
   - (g) In both copies, move a by-name read below its patch. PR 1 uses `real_resolve` in `test_review_trace_scope_and_header_receive_identical_multi_root_list`. PR 2 uses test_transcript_cost.py's `_accumulate_per_account_turn` pair. Expect `snapshot`.
   - (h) In a shim copy at `<attempt>/ctl-h/transcript-analysis.py`, append `_fam = None` after the imports (in PR 2, `_fmt_date = None`). Run with `--scripts-dir <sd>`. Expect `identity`, naming that row.
   - (i) In both copies, add `_probe = _mod._no_such_name` at module level. Expect `unmapped`.
   - (j) At module level, add `_mod.<leaf> = None` to the before copy and the retargeted spelling to the after copy, for a by-name row the file already reads. PR 1 uses `_mod._fam = None` and `render._fam = None`. PR 2 uses a by-name row that test_transcript_cost.py reads. Expect `store`.
   - (k) In the after copy, append `  # probe` to one single-line top-level import statement. Expect `import-comments`.
   - (l) In the period_cost after copy, change the module-level `cost._LIST_PRICE_CAVEAT` load (base :2501, a former `cost_module` load) to `pricing._LIST_PRICE_CAVEAT`. Expect `ast`.
   - (m) In both period_cost copies, append the module-level line `def _alias_store_probe(): period_cost = None`. Expect `alias-store`. The rebinding sits inside its own function because a module-level rebinding would also make every `period_cost` load a `shadow` finding.
   - (n) In the test_transcript_ledger_common.py after copy, delete the added `import tempfile` line. Expect `import-missing`.
   - (o) In the test_author_outcome.py after copy, change one retargeted `ao.cmd_author_outcome` read to `corpus.cmd_author_outcome`, a module the file binds at base :14. Expect `ast`.
6. **Commit 1.** Run `/code-review`. Then stage each file with its own `git add <path>` call and commit.
7. **Dead-patch fail control, PR 1 only.**
   - Run `sha256sum <tests>/test_transcript_analysis.py` and save the output to `<attempt>/ctl-dead.sha256`.
   - In `TestRootsThreadingSpy`, delete only the two pairs of `monkeypatch.setattr(scope, "_resolve_project_scope", …)` and `monkeypatch.setattr(scope, "print_resolved_scope", …)` lines.
   - Run `scripts/venv-run.sh pytest <tests>/test_transcript_analysis.py::TestRootsThreadingSpy`.
     - Every row of `test_resolve_project_scope_and_header_receive_identical_multi_root_list` must fail on "was never called".
     - `test_review_trace_scope_and_header_receive_identical_multi_root_list` must fail with an `IndexError`.
     - Every other test in the class must pass.
   - Undo the edit by hand, then run `sha256sum -c <attempt>/ctl-dead.sha256`.
   - This proves the `scope` patches are the only ones production reaches. Verification 10's parity then proves that deleting the shim patches leaves the class's outcomes unchanged.
8. **Deletion set, before D.**
   - **Candidates:** `scripts/venv-run.sh ruff check --no-cache --select F401 --ignore-noqa <shim>` lists every shim import that the shim's own code leaves unread (row 12). If ruff rejects `--ignore-noqa`, stop and report. Ruff names an aliased import by its original name, so map each candidate to the name the shim binds before grepping: `_dedup_turns_by_request_id`, `_print_resolved_scope`, and `_resolve_scan_roots` appear as `dedup_turns_by_request_id`, `print_resolved_scope`, and `resolve_scan_roots`.
   - **Readers:** run `git grep -noE "_mod\.(<the candidates, as an alternation>)\b" -- '*.py'`, so each hit carries its file name. A hit in a file whose loader does not name `transcript-analysis.py` is inspected and reported, not counted.
   - **Deletion set:** the candidates that have no reader. It must equal row 6's list for this PR. Any difference stops the run.
   - **Probe:** the same `git grep` run at `<base>`, as `git grep -ohE "…" <base> -- '*.py' | sort | uniq -c`, must count every deletion-set name at least once. This proves the scan can see the names it is checking for.
   - Run `git grep -nE "(getattr|hasattr|setattr|delattr)\(_mod\b|patch\.object\(_mod\b" -- '*.py'`. Hits in files whose loader is not the shim are ignored. Otherwise, in PR 1 it returns only the four `TestRootsThreadingSpy` lines, and in PR 2 it returns nothing.
9. **Deletion dispatch D**, per Critical files, with a fresh `<attempt>`.
10. **Deletion proof.** This proves four things: no reader of a deleted binding is left, the deletions change no CLI help or parser configuration, no test outcome changes, and no stale `_mod.` spelling is left in the edited prose.
    - Re-run step 8's reader `git grep` on the working tree, with step 8's other-loader exemption: no deletion-set name may appear in a file whose loader names `transcript-analysis.py`. A hit elsewhere (for example `_mod.os` in `test_mark_terminal.py`) is inspected and reported, not counted. The `(getattr|…)\(_mod` grep returns no line in a shim-loading file.
    - Run `scripts/venv-run.sh ruff check --no-cache --select F401 --ignore-noqa <shim>`. In PR 1, the set of names its findings report equals PR 2's 45 in row 6. In PR 2, it reports nothing, and `git grep -n "noqa" -- <shim>` prints nothing. Probe: `git grep -n "^def build_parser" -- <shim>` hits.
    - PR 1 only: `git grep -nE "(^|[^A-Za-z0-9_])_mod([^.A-Za-z0-9_]|$)" -- <tests>/test_transcript_analysis.py` returns only the two loader statements and prose that names the copy itself. One such line is in `test_projects_dir_honors_claude_config_dir`'s docstring ("for the same reason _mod"). Probe: `git grep -n "_mod.build_parser" -- <tests>/test_transcript_analysis.py` hits.
    - For each file the PR touched, `<chk> --shim <scratch>/before-transcript-analysis.py --scripts-dir <sd> --prose-only --after <tests>/<file>` reports `prose: 0`.
    - Check the prose outside the test files with two greps, each against its expected hits (row 37). Any other hit, or a missing one, means a stale or wrong sentence: stop and report the line.
      - `git grep -nE "(^|[^A-Za-z0-9_])_mod([^A-Za-z0-9_]|$)" -- <shim>`.
        - PR 1: exactly 23 hits, all comments. They are the `os` and `tempfile` import lines; the whole-module block's two comment lines that spell `_mod.<module>` and `` `_mod` `` (base :15, :18); the cost block's "The seven noqa'd names below" line (base :100); and the rationale line of each of PR 2's 18 pricing, redaction, render, and scope leaves in row 6.
        - PR 2: no hits.
      - `git grep -nE "_mod\." -- docs/transcript-analysis-architecture.md`.
        - PR 1: the line or lines of PR 1's new sentence "The other shim-loading test files reach package modules through their copy, …", which spell `_mod.` twice between them, plus the guard-shape line "it checks both the `<module>.<name>` and `_mod.<module>.<name>` read" (base :855).
        - PR 2: only the guard-shape line.
    - `scripts/venv-run.sh ruff check claude/.claude/ claude-skills/ plugins/ evals/` is clean.
    - Run `<mf> capture --script <shim> --account <scratch>/account --all-help --out <attempt>/after`. Then `<mf> compare-captures <scratch>/before-a <attempt>/after` must be clean (row 28).
    - Do a junit run into `<attempt>/c2`. `<mf> test-ids` from `<scratch>/before` to `<attempt>/c2` must be clean. In PR 2, pass one `--expect-new test_shim_copy_reads_only_build_parser_and_main`.
    - Run the selected suite (G3) as two passes: `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py -m "not timing"`, then `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py -m timing -n0`.
      - Run each as a background Bash call, and start the second only after the first finishes. Edit nothing until both finish. Both must pass.
      - Before claiming or fixing a failure in a file outside Critical files, reproduce it at the merge-base, per CLAUDE.md:
        - run `git merge-base HEAD origin/main`;
        - run `git worktree add <scratch>/merge-base <sha>`;
        - run the failing test there;
        - run `git worktree remove <scratch>/merge-base`.
11. **Pin controls, PR 2 only.** For each control:
    - run `sha256sum` on the file first and save the output;
    - edit by hand, then run `scripts/venv-run.sh pytest <tests>/test_transcript_package_module_prefixes.py::test_shim_copy_reads_only_build_parser_and_main`;
    - undo the edit by hand, then pass `sha256sum -c`.

    The controls:
    - (a) Add `_PIN_PROBE = _mod.scope` at module level in `test_transcript_cache_rebuild.py`. The pin fails, naming that file and `scope`.
    - (b) Add `_PIN_PROBE = getattr(_mod, "main")` in the same place instead. The pin fails on the bare use.
    - (c) Add `_PIN_PROBE = _mod.cmd_cost` at module level in `test_transcript_analysis.py`. The pin fails, naming that file and `cmd_cost`, which shows a second file is selected.
    - (d) In `test_author_outcome.py`'s `_spec = …` loader line, change the spec name `"transcript_analysis"` to `"transcript_analysis_pin_probe"`. The pin fails on the cross-check, naming that file.
    - (e) `test_transcript_cache_rebuild.py` makes its allowed reads on one line (row 36). On that line, rename `_mod` to `_pin_probe`. The pin fails on the per-file allowed-read assertion, naming that file.
12. **Commit 2 and hand-off.**
    - Run `/code-review`. Then stage each Critical file with its own `git add <path>` call and commit.
    - `scripts/venv-run.sh pytest claude/.claude/tests/test_pytest_collection_config.py::TestNoBareSameDirectorySiblingImports claude/.claude/hooks/tests/test_ticket_reference_discipline.py` must pass.
    - Repeat step 0's `git fetch` and `git diff` calls. Any output means stop and report it to the engineer.
    - Run `/ready-for-review`, then open the PR.

## Out of scope

- **Relocating `_UNCONDITIONAL_HEADER_CASES`, its two classes, `TestBuildParser`, and `TestConfigDirRouting`.** The `cli.py` phase owns this (G1).
- **The shim's "still-monolithic" comments.** The `cli.py` phase rewrites them along with `build_parser`.
- **Retargeting `_mod.build_parser` and `_mod.main`, and removing the loaders.** This is the `cli.py` phase's work. Its new names are injective, so `nodes --rename` can prove it (row 26).
- **Unaliasing `ao` and listing `test_author_outcome.py` in `TEST_FILES`.** DEFER to the `cli.py` phase. Once that phase drops the file's loader, the guard requires the listing, and its unaliased-import check then requires dropping `ao` (row 11).
- **Removing `_mod_prefixed_module_attribute_reads` from the prefix guard.** After PR 2 it finds no reads, and the pin forbids the shape. DEFER: PR 2's guard edits stay additive, and the `cli.py` phase can drop the shape together with the loaders.
- **Moving `TestRepoTrackedAgentTypeNames`, `TestSanitizeTableCell`, and the other leaf classes out of the legacy file.** The leaf-module-tests phase owns the moves (row 19).
- **GH-1116 tracker edits and follow-up issues.** The PR bodies state what this plan took over.
- **Renaming the legacy file's local `sessions` variables or period_cost's `cost` comprehension variable.** The shadow precondition proves neither reaches a retargeted read (row 8).
- **Splitting files over 1,000 lines.** The shim, `test_transcript_analysis.py`, `test_transcript_cost.py`, `test_transcript_period_cost.py`, `test_transcript_review_rounds.py`, and `test_author_outcome.py` were over the limit before this change. Neither PR grows a file by more than its import lines. Splitting the shim belongs to the `cli.py` phase.
- **A shim copy obtained through a helper module, or loaded inside a function or fixture.** The pin's selection and its cross-check both read each file's own top-level `spec_from_file_location` call, so a file that imports its copy from a helper, or loads it inside a function, escapes both. No test file does so today (row 36). DEFER: the `cli.py` phase removes the loaders.
- **PR 2's verification-recipe refinements.** DEFER to PR 2, which revises the plan at its own base, where the recipe can be checked against real counts and the real pin:
  - a pin control that puts a file in the first set only, such as editing `_SCRIPT` in an existing file, since controls (d) and (e) exercise only the second set and the per-file read assertion;
  - a prose grep over the architecture doc for phrases like "through the shim", written to tolerate line wraps;
  - a checker control for a call-form `@pytest.fixture(...)` that patches a by-name row.
- **Committing the replay checker** (M4, row 26).
- **Any production-code, CLI, help-text, or output change.**
- **Preserved records that name `_mod.` read shapes**: committed plans, `docs/reports/`, `docs/case-studies/`, and CHANGELOG.md.
- **The governing plan's revert rehearsal, for both PRs.** The engineer waived it (row 25).
