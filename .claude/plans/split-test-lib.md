# Split test_lib.py into per-domain test files

## Context

Goal: split `claude/.claude/hooks/tests/test_lib.py` (11,682 lines) into `test_lib_<topic>.py` files, each under the 1,000-line limit that `docs/design-decisions/code-file-line-limit.md` sets for test files. The split must be behavior-preserving. `_lib.sh` changes only in two comments that would otherwise name the wrong test file.

Ask: "test lib.py is absolutely massive and i think it jeeds to he decomposed like transcript abalusis", then "Ok sounds good let’s plan-it". Scope and shape were accepted via labels "Tests only (Recommended)", "Series of PRs (Recommended)" and "Proceed under #1210". The engineer then widened scope to the two `_lib.sh` comment fixes (`_lib.sh:2348`, `:4960`) via label "Allow the 2 edits (Recommended)". They kept the stale-name fixes in `select-tests.py:653` and three `docs/design-decisions/` files via label "Keep the edits (Recommended)".

Why now: `test_lib.py` is the largest file in the repo. It is too large for any reviewer to read whole in one Read call. Test selection is not a reason: `select-tests.py`'s blanket hooks-domain rule already selects the whole `claude/.claude/hooks/tests` directory for any hooks change. Issue #1210 tracks this split, folded from #1118. #1118's scope is to split `test_lib.py` "by `_lib` domain", following #1117's in-file sections once #1117 lands and the existing `test_lib_*.py` siblings until then. #1117 has not landed, so this plan follows the siblings and `_lib.sh`'s current function order. #1118's acceptance invariant still applies: the set of collected node IDs is equal before and after, and no existing assertion is edited.

#1210 was deferred "until the harness freeze lifts". The only freeze mechanism in the repo is the review benchmark's harness-closure hash (`evals/review_bench/analysis.py:749`). It covers only modules imported from `review_bench.runner`, `.adjudicate` and `.analysis`, so it cannot see `test_lib.py`.

Intended outcome: a series of PRs on the transcript-analysis precedent (`.claude/plans/decompose-transcript-handoff-nudge-b.md` is the closest worked example). Each PR moves a group of `test_lib.py` sections verbatim into new files. When the series is done, `test_lib.py` is either deleted or reduced under the limit. Splitting `_lib.sh`, and #1117's in-file reorganization of it, stay out of scope.

## Approach

Split `test_lib.py` over four sequential PRs into 23 new `test_lib_<family>.py` files plus one shared helper module, `_lib_sh_helpers.py`. Phase 3 waits until open PR #1318 merges or closes, and a merged #1318 adds a 24th file in phase 4 (M10). Each phase copies its sections by line range from a scratch copy of its own base, so no test is retyped. The repo's own `_move_fidelity.py` and a scratch comment-block check verify every move. `test_lib.py` stays as a residual of about 630 lines. It keeps the `_lib_parse_tool_input_or_deny` and `_lib_jq` contract tests, which `_lib.sh` and its four plugin copies cite by this file's name. Two other `_lib.sh` comments cite tests that move, so phases 2 and 3 each reword one of them to name the new file (row 5).

Each file holds one `_lib.sh` function family, the seam `.claude/plans/code-file-size-splits.md:193` names for this split. A phase creates each of its files whole. Only `_lib_sh_helpers.py` is created in one phase and extended in a later one.

Alternatives set aside:
- **One PR.** That would put about 11,000 moved lines in one review (row 2), and the engineer chose a series (row 7).
- **Deleting `test_lib.py`.** After the split, six comment lines still name it: `_lib.sh:399` and `:525`, and one in each of the four plugin copies of `_lib.sh` (row 3). Editing any file under a plugin directory requires that plugin's version bump (`.claude/rules/review-pipeline-dispatch.md`, plugin-directory bullet). Keeping the parse-contract tests in `test_lib.py` keeps all six lines true.
- **Three PRs.** Each would move about 3,700 lines. Four keep every phase under about 3,000, and they let phase 1 carry both full-suite triggers (M1).
- **Retyping moved code with Write.** See M5.
- **conftest.py, the residual, or a copy per file as the shared-helper home.** See M4.
- **Moving sections into existing siblings.** Examples are `TestLibAcquireAppendLockCalledTwice` into `test_lib_append_line_locked.py`, or the worktree-lock tests into `test_lib_worktree_collision_guard.py`. The collision-guard file is already 2,193 lines. An existing destination would also mix moved and pre-existing nodes in one file's fidelity check.
- **One credentials file of about 915 lines.** `_lib_strip_shell_quotes` is a quoting primitive. Its tests compose it with the fragment matchers (base :4505–4543), so it moves with them, and the credentials file lands near 855 lines.
- **A plan file per phase**, as the transcript-analysis series used. Every phase here is a pure move that this plan fully specifies. The plan gate passes on a committed, unmodified plan (row 17), so phases 2–4 run under this plan without three more plan cycles. M9 sends a phase back for a plan revision when its base has drifted.

**Root:** `claude/.claude/hooks/tests/test_lib.py` is 11,682 lines, over eleven times the 1,000-line test-file limit (row 18). The split moves its tests into files at or under the limit, keeps every collected test ID and its outcome unchanged, and edits no assertion. `[verified: Context's wc -l; docs/design-decisions/code-file-line-limit.md:19; #1118's invariant as quoted in .claude/plans/code-file-size-splits.md:198]`

**Givens:**
- G1. pytest collects only files that match its `python_files` patterns, and this repo sets none. So the test modules import a module named `_lib_sh_helpers.py`, but pytest never collects it. Reason: pytest owns collection. `[verified: pyproject.toml:17–42 sets no python_files; docs/transcript-analysis-architecture.md:787 calls the scripts tree's _move_fidelity_helpers.py "a plain module, not a test file itself"]`
- G2. Merge is human-only, so each phase starts from a default-branch tip that already holds the previous phase. Reason: the engineer owns merge. `[verified: ~/.claude/CLAUDE.md, Working Style's merge bullet]`
- G3. Other open PRs may edit `test_lib.py` between phases. Reason: their authors own them. At round-1 review, #1318 was the only open PR that touched it (row 25). Phases 2–4 re-scan before they start (Verification 0). `[verified: staff-platform-engineer round-1 finding R1, from gh pr list --state open; not re-run in this revision, which has no shell]`

**Mechanisms:**
- **M1: Four sequential phases, each one PR and one `code-writer` dispatch.** `anchors: root, G2, row2, row5, row7, row14, row26`
  - The phases move about 2,860, 2,850, 2,330 and 2,960 lines (row 2).
  - Phase 1 carries the two citation edits in `select-tests.py` and `helpers.py`. Both files are global triggers (row 14), so only phase 1 runs the full suite.
  - Phases 2 and 3 each edit one `_lib.sh` comment. `_lib.sh` is not a global trigger, so that edit adds the scripts tests to the phase's selection, not the full suite (row 14).
  - No phase splits into more than one dispatch. Every step edits `test_lib.py`, and all steps share one range manifest and one fidelity procedure.
  - Each later phase branches from the default tip after the previous phase merges. Phase 3 also waits until #1318 merges or closes (M10).
- **M2: 23 files, one `_lib.sh` function family each, each at most 1,000 lines.** Critical files names each file's ranges. The largest is about 855 lines (row 2). A merged #1318 adds a 24th, `test_lib_trial_merge_verdict.py` (M10). `anchors: root, row1, row2, row18`
- **M3: `test_lib.py` keeps the parse contract, which `_lib.sh` and its four plugin copies cite by this file's name.** `anchors: row1, row3, row4, row5, row9, row22, row23`
  - These base ranges stay. Each is named by its first and last node, with its 041fa346 lines.
    - `_REQUIRE_CODE_REVIEW_HOOK` with its comment (:79–82).
    - The `# Shell harness:` comment through `_parse_ok_fields` (:108–160), which holds `_HARNESS_TEMPLATE` and `_run_harness`.
    - `test_valid_bash_payload_returns_ok` through `test_command_containing_unit_separator_denies_via_require_code_review` (:175–570).
    - `test_hung_jq_denied_within_timeout` through `test_timeout_absent_fallback_valid_payload_returns_ok` (:605–683).
    - `test_missing_emit_deny_loud_fail` (:1072–1110).
    - `test_null_literal_input_denied` through `test_newline_injection_in_tool_name_denied` (:1275–1300).
  - Together they hold every test that `_lib.sh:399`, `_lib.sh:525` and the four plugin copies cite.
  - `_lib_sh_with_unreadable_schema` and `_lib_sh_with_missing_config_sh` (:85–106) sit between the first two residual ranges, but they are not residual. Phase 3 moves them to `test_lib_config_gates.py`, their only reader (row 9).
  - The two groups that `_lib.sh:2348` and `:4960` cite move, and the moving phase rewords that comment to name the new file (row 5).
    - Phase 2 moves the gh `--help` drift guard into `test_lib_tool_argv.py`. The guard runs from its banner at :5013 through `test_gh_help_root_no_value_placeholder_flags_stay_within_help_and_version`.
    - Phase 3 moves `_run_resume_context_index_dir` and `TestLibResumeContextIndexDir` (:5869–5988) into `test_lib_resume_context.py`.
  - Each group adjoins its new file's other base lines, so each of those two files takes one contiguous range (row 22).
- **M4: One family helper module, `claude/.claude/hooks/tests/_lib_sh_helpers.py`, holds exactly the top-level helpers that two or more of the split's files read.** `anchors: G1, row9, row10, row12, row30`
  - Phase 1 moves `_LIB_SH`, `_SKILL_MANAGEMENT_PLUGIN_LIB` and `_run_lib_call` there, with their comments. Phase 4 adds `_gate_diff_base`, `_staged_diff_hash`, `_forge_state_marker` and `_assert_valid_tree_oid`.
  - Every other helper moves with its only reader. A shared helper that row 9 missed follows the same rule, in the phase where ruff's F821 first reports it.
  - Its docstring is `"""Helpers shared by two or more of the test_lib*.py files that unit-test _lib.sh."""`
  - Rejected, conftest.py. It holds the hooks tree's fixtures and cross-hook helpers, and editing it also selects four scripts tests (row 10). `_run_lib_call` serves only the `_lib.sh` family.
  - Rejected, the residual as the home. Every new file would then import from a test module that never calls `_run_lib_call` itself, which puts the helper in the wrong place.
  - Rejected, a copy per file. `_run_lib_call` would then exist in 11 files with no named duplication exception, and `nodes` reports each copy as `node-duplicate` (row 12).
  - Accepted loss: `TestCrossDomainReadCompleteness` scans only `test_*.py` files, so `_LIB_SH` and `_SKILL_MANAGEMENT_PLUGIN_LIB` leave its view in phase 1 (row 30).
    - Both paths stay in its view through other hooks test files, whose reads select the same `HOOKS_TESTS_DIR` target. The scan still flags any narrowing that drops the hooks tests for either path.
    - Only a future narrowing to a per-file target list that omits the new files would go unflagged. CI's full-suite passes would still run them.
    - Keeping the two assignments visible would need either a copy per reader, which `nodes` reports as `node-duplicate`, or a scanner change outside this plan's files.
- **M5: Copy by line range from a scratch base copy, and never retype.** `anchors: G3, row12, row16, row31`
  - Verification 0 creates the scratch directory outside the repo, writes the base copy and records the base SHA. Every later call spells out that path and SHA literally, and no call uses `$(...)` (row 31).
  - Resolve each range at the phase's own base. Its start is the quoted banner or node that Critical files names, found by grep. Its end is the last line of its named last node. This plan's line numbers are 041fa346 guides only (row 1).
  - A range starts at the first non-blank line after the previous top-level node ends, so the comments above its first node move with it.
  - Build each new file from its hand-written header. Then append each of its ranges with one `sed -n '<start>,<end>p' <scratch>/before_test_lib.py >> <new file>` call per range, with one `printf '\n\n' >> <new file>` call between ranges. Each call is a single statement.
  - The range delete is the phase's first edit of `test_lib.py`. It is one `sed '<a>,<b>d;<c>,<d>d' <scratch>/before_test_lib.py > claude/.claude/hooks/tests/test_lib.py` call that names every range, so every line number refers to the base.
  - Right after the delete, `wc -l` of `test_lib.py` must equal the base copy's count minus the summed range lengths. Every other edit of `test_lib.py` (imports, docstrings, locators) comes after this check.
  - Copying line ranges, not AST spans, carries the banner comments between nodes (row 16).
  - Rejected, retyping each range with Write. That is about 2,900 lines of transcription per phase, at high output cost, and every slip becomes a fidelity finding to chase.
  - Rejected, deleting with Edit. Each `old_string` would have to restate a whole deleted range.
  - Rejected, `sed -i`. BSD `sed` reads the argument after `-i` as a backup suffix. An in-place delete that runs after any other edit also deletes the wrong lines without an error.
- **M6: Headers.** `anchors: row10, row15`
  - Each new file opens with a one-line docstring that names the `_lib.sh` functions it covers. It names no phase, plan or tracker issue.
  - Next comes `from __future__ import annotations`, then only the imports the file's code reads, ordered by ruff's `I` rule (`ruff check --select I --fix <file>`).
  - Siblings are imported relatively (`from ._lib_sh_helpers import`, `from .conftest import`, `from .test_config_lib import`), per `.claude/rules/test-tree-packaging.md`.
- **M7: The two importers keep their import shapes and change only the module they name.** `anchors: row11`
  - Phase 1 changes `test_scaled_timeout_shim.py` to `from . import test_lib, test_lib_capped_for`. Its four `test_lib_capped_for_*` entries in `_PROTECTED_FUNCTIONS` read `test_lib_capped_for.<name>`, and `test_timeout_absent_fallback_valid_payload_returns_ok` stays `test_lib.<name>`. Its parametrize ids come from `fn.__name__`, so its node IDs do not change.
  - Phase 3 changes `test_resume_command_template_contract.py` to `from .test_lib_resume_context import _UNUSABLE, _continuity_file_resume_cwd`.
- **M8: Fix each citation in the phase that makes it false, and change only its locator.** `anchors: row3, row5, row6, row13, row19, row20, row21, row23, row24`
  - A locator is a file name, or a position word ("above", "below", "elsewhere in this file") whose referent now sits in another file. Rewrite it to name the referent's new file, and leave the surrounding claim as written.
  - Critical files lists the sites per phase. Inside moved or residual code, the same rule covers any further locator the implementer finds. The PR body declares each such edit with its old and new text, so a reviewer can see that it changed only the locator. Only declared edits may show up as fidelity findings (Verification 3 and 4).
  - Dated records and other plan files stay unedited (row 19).
  - The four plugin copies of `_lib.sh` stay unedited, because none of them cites a moved test (row 23).
- **M9: Each phase re-resolves its anchors at its own base.** The phase stops for a plan revision, instead of guessing a home, in either of two cases. A quoted anchor may be missing or match more than one line. A top-level node may fall outside every range this plan assigns and outside the residual. `anchors: G3, row1, row17`
- **M10: #1318 gates phase 3, and a conflict with the default tip forces a rebuild.** `anchors: G3, row25, row26, row27, row28, row32, row34, row35, row36`
  - Phases 1 and 2 start now. None of their ranges holds one of #1318's `test_lib.py` hunks (row 25).
  - Phases 2–4 each re-scan the open PRs that edit `test_lib.py` before they start (Verification 0). The engineer decides whether a phase waits for any PR besides #1318.
  - Phase 3 starts only after #1318 merges or closes. It then re-resolves its own and phase 4's anchors at its new base (M9).
  - If #1318 merged, phase 4 changes three things at its own base. Critical files marks each one.
    - The fallbacks range starts at the first top-level node after `test_merge_tree_merge_base_flag_changes_computed_tree`, because #1318 deletes `_make_git_rejecting_write_tree`.
    - That file's docstring names the merge-tree-failure class that replaces `TestGateDiffBaseGitVersionFallback`, in place of the git-version fallbacks.
    - The `_lib_trial_merge_verdict` section moves to a new `test_lib_trial_merge_verdict.py`, one family per file (M2). Any of its helpers that another file reads goes to `_lib_sh_helpers.py`, under M4's rule.
  - At each handoff for merge, Verification 10 checks the phase PR against the default tip.
    - A conflict in any file the phase edits rebuilds the phase from step 0 at the new tip. That covers any overlap with #744 or #1292 (row 28). A rebuild takes a new scratch directory, a new base copy and freshly resolved ranges. It never hand-resolves a conflict.
    - A clean result syncs the branch with the tip, and CI's full suite must pass on the synced head (row 32). A failure there that CI on the default tip does not share also rebuilds the phase.
  - Why a conflict is the trigger, and not any `test_lib.py` commit:
    - A default-branch edit to a line the phase deletes or edits conflicts with the phase (row 35). So a clean merge leaves every moved line as it stood at `<base>`, and the evidence from steps 2–4 still holds.
    - What can still go stale depends on the rest of the tree. For example, a new `test_lib.py` test may call `_run_lib_call`, which phase 1 moves out. CI on the synced head re-runs the suite to catch that.
    - A rebuild per `test_lib.py` commit would also redo a phase for commits that touch none of its lines.
    - Rejected, comparing the hunks of `git diff -U0 <base> origin/main` against the manifest's ranges. A conflict already marks each such overlap, and the comparison is a hand check.
  - After a phase's second rebuild, the engineer decides before a third, for example on a freeze window for `test_lib.py`.
  - Each PR body states its base SHA, this rule and Verification 10's commands, so whoever merges it sees all three (Verification 0).
- **M11: Rollback is a last-in-first-out `git revert` of each phase's squash commit.** `anchors: G2, row29, row32`
  - A phase's revert is valid only while no later commit on the default branch touches a file the phase edits, and no later file imports a module the phase creates.
  - Phases 2–4 import `_lib_sh_helpers.py`, so phase 1 is revertable only after phases 2–4 are reverted.
  - Every phase prunes `test_lib.py`'s import block, so phases 2 and 3 each revert cleanly only after every later phase.
  - Phase 4 reverts alone, because the `test_lib.py` before it still defines the four helpers it moves.
  - Each PR body carries this rollback statement (Verification 0).
  - A revert's real test is its own PR. CI runs both full-suite passes on every pull request (row 32), at the default tip the revert actually meets.
  - No phase rehearses its revert locally.
    - The precedent rehearses because "a conftest fixture promotion in PR N can leave PR N−1's revert uncollectable" (row 29). `_lib_sh_helpers.py` has that shape, but the failure needs an out-of-order revert, which the order above forbids.
    - A rehearsal at phase 2's branch point also predates every later commit, so it cannot test the validity condition above either.
- **M12: A scratch script checks that every top-level comment block lands in its owner's file.** `anchors: row12, row16`
  - `nodes` and `remainder` both pass a banner left behind unchanged in `test_lib.py` (row 12).
  - The script lives in the phase's scratch directory, outside the repo. Verification 4 states its inputs and pass condition.
  - It inserts the worktree's `claude/.claude/scripts/tests` directory, typed out as an absolute path, at the front of `sys.path`. It then imports `_load_source` and `_comment_tokens` from `_move_fidelity`, which imports only the standard library (`_move_fidelity.py:35–53`).
  - A top-level comment block is a run of comment lines on consecutive line numbers, outside every top-level statement. That is `_comment_blocks`' rule, but `_comment_blocks` returns no line numbers (`_move_fidelity.py:394–405`), so the script groups `_comment_tokens`' output itself.
  - A block's owner is the first named top-level node after it, or the file's last node when none follows. That is M5's range-start rule stated by node, so the check does not reuse the line numbers it tests.
  - Unlike `_move_fidelity.py`, the script has no self-tests (row 12). So Verification 4 adds a count guard against a vacuous pass, and one run on a seeded copy that must fail.
  - Rejected, a floor of one block per after file. `test_lib_emit.py`'s two ranges hold no column-0 comment line at 041fa346 (this revision's grep of `^#` lines), so that floor would fail a correct split.
  - Rejected, a `comments` subcommand in `_move_fidelity.py`. That is a repo change, with its own seeded-defect tests (row 12), outside this plan's files.
  - Rejected, grepping each banner in its target file. That is not mechanical, and it shows a block arrived but not that it left `test_lib.py`.
  - Rejected, running `remainder` on the receiving files joined in base order. Joining them needs the same line ranges the check exists to test.

**Assumption rows:**
1. Every top-level node after the imports falls in exactly one range that Critical files assigns, or in one of M3's residual ranges. Each range is named by its first banner or node and its last node. Line numbers are guides at the base only. `[verified: this pass's grep of top-level def, class, assignment and "# ---" banner lines at 041fa346; staff-sdet round 1 resolved every range by AST and found one overlap, :85–106, which M3 now leaves out of the residual]`
2. The phases move about 2,860, 2,850, 2,330 and 2,960 lines. Each new file adds 40 to 60 header lines, so the largest new files are about 855 lines (`test_lib_credentials.py`), 825 (`test_lib_config_gates.py`) and 800 (`test_lib_resume_context.py`). `test_lib_tool_argv.py` is about 660. The residual is about 630. `[verified: arithmetic over row 1's base spans and row 22's two groups; these are estimates, and Verification 6 measures the real sizes]`
3. Eight comment lines in five production files name `test_lib.py`. `claude/.claude/hooks/_lib.sh` has four: :399 cites `test_missing_emit_deny_loud_fail`, :525 cites "test_lib.py's OK-path tests", :2348 cites "test_lib.py's traversal-scope guard" (the gh drift guard), and :4960 cites `TestLibResumeContextIndexDir`'s docstring. Each plugin copy cites `test_lib.py::test_valid_bash_payload_returns_ok`: `plugins/lovable-cloud/hooks/_lib.sh:80`, `plugins/plugin-semver/hooks/_lib.sh:82`, `plugins/npm-semver/hooks/_lib.sh:82` and `plugins/skill-management/hooks/_lib.sh:514`. `[verified: grep]`
4. The split changes tests and does not split `_lib.sh`. `[engineer-verified: "Tests only (Recommended)"]`
5. The engineer allows two comment edits in `_lib.sh`, at :2348 and :4960. `[engineer-verified: "Allow the 2 edits (Recommended)"]`
6. The split keeps its locator-only edits outside the test trees: one comment at `claude/.claude/scripts/select-tests.py:653`, and lines in three `docs/design-decisions/` files. `[engineer-verified: "Keep the edits (Recommended)"]`
7. The engineer chose a series. The session's option description, "3-4 PRs, each moving a group of sections, each checking that collected node IDs match before and after", is the session's own proposal and is `[unverified]`. This plan sizes four phases from row 2 and keeps the per-PR check (Verification 2). `[engineer-verified: "Series of PRs (Recommended)"]`
8. The work proceeds under #1210. `[engineer-verified: "Proceed under #1210"]`
9. Which planned files read each top-level helper. `[verified: grep at 041fa346]`
   - `_run_lib_call` has 75 calls. They fall in the ranges of 11 planned files, and none falls in the residual. `_LIB_SH` has 152 references.
   - `_SKILL_MANAGEMENT_PLUGIN_LIB` is read at :893 (the capped-for range) and :10661–10773 (the skill-management plugin range).
   - Only residual ranges read `_HARNESS_TEMPLATE`, `_run_harness`, `_parse_ok_fields` and `_REQUIRE_CODE_REVIEW_HOOK`. Only the config-gates ranges (:6705–7264) read `_lib_sh_with_unreadable_schema` and `_lib_sh_with_missing_config_sh`.
   - Code on both sides of the :9986 seam reads `_gate_diff_base`, `_staged_diff_hash`, `_forge_state_marker` and `_assert_valid_tree_oid`. Only code before the seam reads `_git_inprogress_state` and `_git_supports_sha256_object_format`. Only code after it reads `_timeout_binary_present` and the `_make_*` git shims.
   - Each of these helper families is read in one planned file only: `_fragment_*`, `_extract_git_subcmd*`, `_command_invokes_*`, `_tool_argv_from_subcmd`, `_words_start_with`, `_list_contains`, the command-words helpers, `_strip_shell_quotes`, `_sed_is_gnu`, `_porcelain_record`, `_resolve_default_branch`, `_commit_sentinel_at_ref`, `_linked_worktree_root`, `_first_live_linked_worktree`, `_valid_session_id_component`, `_cap_fire_path_env`, `_config_lines`, `_length_ratchet_exceeded` and `_missing_plugin_ids`.
   - The helpers not listed here were not grepped, so their placement is `[unverified]`. Ruff's F821 catches any that cross a file boundary (M4).
10. Precedents for sharing helpers. `[verified: grep and glob this pass; select-tests.py:218–224]`
    - In `claude/.claude/hooks/tests/`, 28 files import from `conftest.py` and 11 import names from a sibling test module. That directory has no `_*_helpers.py` module.
    - `claude/.claude/scripts/tests/` holds 11 `_<topic>_helpers.py` family modules.
    - `conftest.py` is in `HOOKS_TESTS_MODULES_IMPORTED_BY_SCRIPTS_TESTS`, so editing it also selects four scripts tests.
11. The two importers. `test_resume_command_template_contract.py:16` imports `_UNUSABLE` and `_continuity_file_resume_cwd`. `test_scaled_timeout_shim.py:39` imports the module and reads five of its functions at :289–293, with `ids=lambda fn: fn.__name__` at :296. `[verified: Read]`
12. What the fidelity harness checks. `[verified: Read of _move_fidelity.py]`
    - `nodes` compares each named top-level node's AST and the comments inside it. It reports a moved name defined more than once across the `--after` files as `node-duplicate`, and it prints a `compared-nodes` count.
    - `remainder` drops the named nodes and every import, then compares what is left, including the comment blocks outside dropped nodes.
    - `test-ids` compares per-test outcomes keyed by class and name without the module (:745–750), so a test keeps its ID across a file move.
    - The harness has its own seeded-defect self-tests (`docs/transcript-analysis-architecture.md:779–785`).
    - `nodes` cannot name a bare statement. `test_lib.py` has exactly one, `assert _REALPATH_M_FALLBACK_MAX_DEPTH >= 2` at :5176, in phase 1's realpath range.
    - `nodes` compares only each node's own lines (`segment`, :143–144). `remainder` reports a comment block only when the two sides differ (:476–480). So a top-level comment block left behind unchanged passes both.
    - `_load_source` (:169–177) yields each top-level node's name and lines, and `_comment_tokens` (:187–188) yields each comment's line and text.
13. Position locators that the split makes false. This revision grepped `test_lib.py` for "above", "below", "elsewhere in this file", "this file", "this module", "previous test" and "same file", and read each hit whose referent could land in another file. `[verified: grep and Read at 041fa346]`
    - Moved docstrings: `test_active_bypass_marker_live_and_touch_toctou_stub_touch_deletes_marker_first` says "use above" (:2450–2451), `test_active_bypass_marker_live_find_hang_capped_withholds_bypass` says "elsewhere in this file" (:2492), and `TestReviewerRoundStateKeyDuringRebase` says "this file's other tests cover" (:10401).
    - A docstring that stays in `test_lib.py` through phase 1: `test_review_pr_completion_marker_fields_wrong_session_returns_1` places `_lib_marker_value_present`'s glob "above" (:1530–1531).
    - Banners: :1464 places `_lib_marker_value_present` "above", and :2359 places `_lib_valid_session_id_component`'s table "above". The banners at :4548, :4570, :7711–7712 and :8810 each locate a referent "above" too. In each case an earlier or the same phase moves the referent to another file.
    - `test_jq_field_list_extraction_is_non_empty` (:480) cites "test_lib.py's own builds_path_re", which phase 1 moves.
    - Every other hit's referent lands in the hit's own file. One of them, :3286's "the hash-computing helpers above", names no node above it in `test_lib.py`. It matches `_lib.sh`'s order instead, where `_lib_head_tree_hash` (`_lib.sh:1296`) precedes the sentinel helper (:1324). Phase 2 moves that banner and the `_lib_head_tree_hash` tests into one file, so the split leaves it as it is.
14. Test-selection facts. `[verified: Read of select-tests.py]`
    - `helpers.py` and `select-tests.py` are both `GLOBAL_TRIGGER_PATHS` (:448–452, re-read this revision). The set's only other member is `pyproject.toml`, so `_lib.sh` is not a global trigger.
    - The hooks domain rule selects all of `claude/.claude/hooks/tests` (:599).
    - No exact-path constant names `test_lib.py`. Only the comment at :653 does.
    - A shell change in the hooks directory also selects the scripts tests (:827).
15. `test_shell_lib_function_liveness.py` builds its corpus from `git ls-files` (:48), so the new files must be staged before the scoped run. `test_ticket_reference_discipline.py` parametrizes over tracked files (:123, :183, :316). Its new IDs, one set per new file, are the only node IDs the series adds. It also scans comments for tracker-ID qualifiers. `[verified: Read]`
16. The precedent copies line ranges, rather than AST spans, so that comments between nodes move too. `[verified: .claude/plans/decompose-transcript-handoff-nudge-b.md:68–71]`
17. The plan gate passes through when every plan file is committed and unmodified. `[verified: claude/.claude/hooks/require-plan-review.sh:13–15]`
18. The limit is 1,000 lines for test files, and nothing enforces it yet. `[verified: docs/design-decisions/code-file-line-limit.md:19 and :50–57; grep found no enforcing test]`
19. Some citing files record past states, so they stay unedited: `docs/reports/2026-08-10-repo-quality-audit/findings.md`, `docs/reports/2026-08-22-discovery-audit/findings.md`, and every other `.claude/plans/*.md` file. The `docs/design-decisions/` lines this plan edits describe where tests live now, so they are descriptions, not records. `[verified: grep; ~/.claude/CLAUDE.md Scope discipline, Axis 3]`
20. Some citations are already stale, and this split does not make them worse. `[verified: grep]`
    - `test_require_plan_review.py:47` and `test_require_memory_skill.py:31` credit `_FORCED_FALLBACK_REALPATH_SHIM` to `test_lib.py`, but `helpers.py:2549` defines it.
    - `.claude/rules/bash-unit-test-seams.md:18–21` counts "six lib-level test files" and lists four of the eight `test_lib*.py` files.
    - `claude/.claude/scripts/tests/test_autonomous_shipping_active.py:4` cites the range :1204–1344, which does not hold `TestAutonomousShippingActive` (:6946). Phase 3 rewrites this citation anyway, because its file name becomes false.
21. Critical files assigns every live citation of `test_lib.py` to the phase that makes it false. These sites need no edit, because what they cite stays in `test_lib.py`: `_lib.sh:399` and `:525`, the four plugin copies, `docs/design-decisions/bats-core-adoption-declined.md:14`, `test_hook_alignment.py:3350`, `test_record_session_end.py:281`, `test_consume_durable_continuity_file_on_read.py:384`, `test_require_plugin_version_bump.py:425`, `test_require_npm_version_bump.py:535`, `test_require_skill_review.py:1397`, `test_require_worktree_for_git_writes.py:703` and `test_deny_reviewer_tree_mutation.py:1446`. `[verified: repo-wide grep for "test_lib.py" at 041fa346, rechecked for these sites in this revision]`
22. The two groups M3 moves adjoin their new files' other base lines, and neither carries a locator that the move makes false. `[verified: Read of test_lib.py:4990–5160 and :5840–5999 at 041fa346]`
    - `TestCommandInvokesToolSubcmd` ends at :5010, and the drift guard's banner starts at :5013.
    - `TestLibResumeContextTmpdirRoot` ends at :5866, `_run_resume_context_index_dir` starts at :5869, and `TestLibResumeContextIndexDir` ends at :5988. `test_lib_print_recovery_hint_prints_reload_command_to_stderr_only` starts at :5991.
    - The drift guard's "above" references point at its own banner and tests. The index-dir docstrings cite only `_lib_resume_context_index_dir`.
23. Three sites outside `test_lib.py` cite the moved groups, and none is a plugin file. `_lib.sh:2348` is the only site naming the "traversal-scope guard". `_lib.sh:4960` and `claude/.claude/scripts/tests/test_find_consumed_continuity_file.py:154` are the only sites naming `TestLibResumeContextIndexDir`. No other file names the drift guard's helpers or tests. Under `plugins/`, the only lines naming `test_lib` are row 3's four. `[verified: grep this revision for "traversal-scope", "TestLibResumeContextIndexDir", the drift guard's top-level names, and "test_lib" under plugins/]`
24. Each of row 6's sites names `test_lib.py` as the home of something a phase moves, so the split would leave each one false. `[verified: Read of select-tests.py:653–654; grep of docs/design-decisions/ for "test_lib.py" this revision]`
25. Open PR #1318, "Raise the supported git floor to 2.43", edits `test_lib.py` in four hunks. `[verified: staff-platform-engineer round-1 finding R1, from gh pr list --state open and the PR's diff; not re-read in this revision]`
    - It deletes a test inside phase 3's resume-context range, at about base :6095.
    - It deletes `_make_git_rejecting_write_tree` and `_make_git_rejecting_merge_base_flag` (about :9990). The first is the fallbacks range's start anchor.
    - It replaces `TestGateDiffBaseGitVersionFallback` with `TestGateDiffBaseMergeTreeFailure`.
    - It adds about 180 lines: a `# --- _lib_trial_merge_verdict` banner, `TestTrialMergeVerdict` and five helpers, between phase 4's skill-management-plugin range and its validate-plugin-id range.
    - It also edits five locator files the phases edit, plus `_lib.sh` and `plugins/skill-management/hooks/_lib.sh`. Its nearest `_lib.sh` hunk is about 150 lines from phase 3's comment edit.
    - None of these hunks falls in a phase 1 or phase 2 range.
26. Against #1318, the engineer chose to start now and gate phase 3. `[engineer-verified: "Start now, gate ph. 3 (Recommended)"]`
27. The session's option description for that label is the session's own wording: "Phases 1-2 go ahead now. Phase 3 waits until #1318 merges or closes, then re-resolves its anchors at its new base. A merged #1318 also gets its new TestTrialMergeVerdict section a home in phase 4." M10 follows it. `[unverified]`
28. #744 and #1292 also edit `select-tests.py`, and #744 also edits `helpers.py`. Those files hold phase 1's locator sites (`select-tests.py:653`, `helpers.py:2545` and `:2565`). Neither PR has a hunk near those lines. The nearest is #1292's, at `select-tests.py:578`. `[verified: the dispatching session's read of the open PRs' diffs this session, at heads #744 bde021c6ebb9, #1292 af977b760651 and #1318 c374478072d5]`
29. Rollback facts. `[verified: .claude/plans/decompose-transcript-handoff-nudge-a.md:394 records squash-only merges from gh api; this plan's Critical files and row 9; .claude/plans/transcript-analysis-decomposition.md:276–279]`
    - The default branch takes squash merges only, so each phase lands as one commit.
    - Phases 2–4 each create files that import `_lib_sh_helpers.py`, which phase 1 creates.
    - Every phase edits `test_lib.py`'s import block. Phases 1 and 4 both edit `test_require_skill_review.py` and `test_require_plan_review.py`, and phases 2 and 4 both edit `test_lib_reviewer_round_state.py`.
    - The precedent's governing plan rehearses each phase's revert because "a conftest fixture promotion in PR N can leave PR N−1's revert uncollectable". `_lib_sh_helpers.py` has that shape.
30. The cross-domain-read scanner. `[verified: Read of claude/.claude/scripts/tests/test_select_tests.py:118–162, :199–204 and :345–367; grep of hooks/tests for module-level "_lib.sh" path assignments; select-tests.py:599 and :811]`
    - `TestCrossDomainReadCompleteness` parses only `test_*.py` files and resolves only module-level single-name assignments. For each resolved path, it checks that `select_pytest_targets` covers the file that reads it.
    - After phase 1, no `test_*.py` assigns `_LIB_SH` or `_SKILL_MANAGEMENT_PLUGIN_LIB`. The new files import them.
    - `test_require_skill_review.py:49` still assigns `plugins/skill-management/hooks/_lib.sh`, and at least 14 other hooks test files assign `claude/.claude/hooks/_lib.sh`, such as `test_config_lib.py:30`.
    - Both paths select the `HOOKS_TESTS_DIR` directory, `select-tests.py:599` for the hooks directory and `:811` for plugin hooks. That target covers every new file.
31. The harness's worktree Bash guard refuses a `$(...)` assigned to a variable and used in a later statement (Trigger A), and a bare `$(git ...)` (Trigger E). Its documented fallback is one statement with literal arguments. `[verified: docs/worktree-bash-guard.md:21–27 and :112–114]`
32. Run-length and fail-closed facts. `[verified: Read of .github/workflows/tests.yml:11–14, :34, :141 and :148; Read of _move_fidelity.py; grep of test_lib.py for mark.timing]`
    - CI runs on every pull request and on every push to `main` (:11–14).
    - CI caps its job at 20 minutes, and its two pytest passes are `-m "not timing"` and `-m timing -n0` over `claude/.claude/ claude-skills/ plugins/ evals/`.
    - `test-ids` exits non-zero on a missing or unparseable XML (`_move_fidelity.py:761–764`) and when neither side has a testcase (:798–799). It cannot tell a stale XML from an earlier run, which a fresh scratch directory per build rules out.
    - The residual keeps `test_hung_jq_denied_within_timeout`, a `timing` test (base :605). So every phase's two timing XMLs hold at least one testcase.
33. The Bash tool's foreground limit is 600 s, so a long foreground pass can be killed before pytest writes its junit XML. `[unverified — relayed from staff-platform-engineer round-1 finding R4]`
34. `git merge-tree --write-tree` exits 1, not 0, when the merge it computes conflicts. `[verified: claude/.claude/hooks/_lib.sh:1540–1541; git-merge-tree(1) EXIT STATUS, which also gives a status other than 0 or 1 for an error]`
35. A default-branch edit to a line that a phase deletes or edits conflicts with that phase. Every line of a moved range is gone from the phase's `test_lib.py`, so the phase's own diff covers each one. `[unverified — git's three-way merge rule, from git-merge(1); not read this pass]`
36. `gh pr list --json` serves `files` and `headRefOid`. `[verified: gh pr list --state open --json number,headRefOid,files ran this session and returned both fields]`

## Critical files

Below, `hooks/tests/` means `claude/.claude/hooks/tests/`.
- Each range names its first line by a quoted banner prefix or a node name, and its last node by name (M5). The phase greps for both at its own base.
- `:N` is the 041fa346 line, a guide only (M9).
- Each locator site in another file is a hit of Verification 7's grep. At a later base, find it by grepping its file for `test_lib.py`, in the order the line numbers give.
- Each new file's parenthesis names the `_lib.sh` functions its docstring covers.

**Phase 1**, one `code-writer` dispatch. Its select-tests run is the full suite (row 14).
- Create `hooks/tests/_lib_sh_helpers.py`. It takes `_LIB_SH` and `_SKILL_MANAGEMENT_PLUGIN_LIB` with their comments, from the comment beginning `# Path to _lib.sh: test lives in hooks/tests/` (:74–77), and `_run_lib_call` (:163).
- Create `hooks/tests/test_lib_emit.py` (`_lib_emit_deny`'s gate label, `_lib_emit_allow_with_context`).
  - `test_deny_gate_label_unset_falls_back_to_basename_derived_label` (:571) through `test_deny_gate_label_set_renders_in_reason`.
  - `test_lib_emit_allow_with_context_emits_expected_envelope` (:1113) through `test_lib_emit_allow_with_context_degrades_to_no_output_when_jq_absent`.
- Create `hooks/tests/test_lib_capped_for.py` (`_lib_capped_for`, `_lib_status_consistent_with_cap_kill`). It takes the comment beginning `# The one cap-boundary test that runs against the real timeout(1)` (:686) through `test_lib_capped_for_aborts_on_unset_seconds_argument`.
- Create `hooks/tests/test_lib_session_identity.py` (`_lib_hook_claude_pid`, `_lib_valid_session_id_component`, and the check that every hook building a path from a session id validates it).
  - `test_lib_hook_claude_pid_equal_to_ppid_is_accepted` (:1146) through `test_lib_hook_claude_pid_rejects_non_numeric_value`.
  - The banner beginning `# --- _lib_valid_session_id_component -` (:2149) through `test_valid_session_id_component_rejects_path_escaping_ids`.
  - The banner beginning `# --- Universal adoption of the session-id guard -` (:2650) through `test_every_hook_that_paths_a_session_id_validates_it`.
- Create `hooks/tests/test_lib_worktree.py` (`_lib_worktree_lock_absent`, `_lib_first_live_linked_worktree`).
  - `test_lib_worktree_lock_absent_reports_absent_under_eacces` (decorator at :1198) through `test_lib_worktree_lock_absent_stale_read_survives_opposite_race`.
  - The banner beginning `# --- _lib_first_live_linked_worktree -` (:2702) through `test_first_live_linked_worktree_counts_a_review_shaped_worktree_under_another_trees_worktrees_dir`.
- Create `hooks/tests/test_lib_markers.py` (`_lib_marker_value_present`, `_lib_active_bypass_marker_live`, `_lib_active_bypass_marker_live_and_touch`).
  - The banner beginning `# --- _lib_marker_value_present -` (:1303) through `test_marker_value_present_restores_nullglob_state`.
  - The banner beginning `# --- _lib_active_bypass_marker_live -` (:2217) through `test_active_bypass_marker_live_at_3601_seconds_already_expired`.
  - Its locators (row 13). The docstrings at :2450–2451 ("use above") and :2492 ("elsewhere in this file") name `test_lib.py`. The comment ending "table for _lib_valid_session_id_component above" (:2359) names `test_lib_session_identity.py` in place of "above".
- Create `hooks/tests/test_lib_payload_matchers.py` (`_lib_skill_basename`, `_lib_args_lead_word_is`, the agent-roster predicates, `_lib_list_contains`).
  - The banner beginning `# --- _lib_skill_basename -` (:1618) through `test_args_lead_word_is_with_missing_arguments_under_set_u_returns_one`.
  - The banner beginning `# --- _lib_is_no_gate_release_agent -` (:1967) through `test_is_reviewer_persona_rejects_agents_that_are_not_reviewer_personas_and_absent_type`.
  - The banner beginning `# --- _lib_list_contains -` (:8733) through `TestListContains`.
- Create `hooks/tests/test_lib_gh.py` (`_lib_gh`, `_lib_origin_owner_repo`, `_lib_case_insensitive_ne`). It takes the banner beginning `# --- _lib_gh -` (:1798) through `TestLibCaseInsensitiveNe`.
- Create `hooks/tests/test_lib_realpath.py` (`_lib_realpath_m`, `_lib_normalize_path_lexically`). It takes the banner beginning `# --- _lib_realpath_m -` (:5159) through `TestLibRealpathM`, including the bare `assert` at :5176.
- Modify `hooks/tests/test_lib.py`.
  - Delete the ranges first (M5). Then import from `._lib_sh_helpers` whichever of the three helpers it still reads, and prune its other imports.
  - In `__doc__`, drop the clause naming `_lib_marker_value_present` and `_lib_is_no_gate_release_agent`.
  - In `test_jq_field_list_extraction_is_non_empty`'s docstring, name `test_lib_session_identity.py` as the home of `builds_path_re`.
  - The `_lib_review_pr_completion_marker_fields` banner's "unlike _lib_marker_value_present above" (:1464) and `test_review_pr_completion_marker_fields_wrong_session_returns_1`'s "cross-session glob above" (:1530–1531) each name `test_lib_markers.py` in place of "above". Both stay in `test_lib.py` until phase 2 moves them. Phase 1 moves their referent, so phase 1 fixes them (M8).
- Modify `hooks/tests/test_scaled_timeout_shim.py` at :9–10, :36–39 and :289–293, per M7.
- Modify the locators at:
  - `hooks/tests/test_require_skill_review.py:1437`;
  - `hooks/tests/test_enforce_marker_script_shape.py:785`;
  - `hooks/tests/test_require_routing_read.py:211`;
  - `hooks/tests/test_require_plan_review.py:1062`;
  - `claude/.claude/tests/helpers.py:2545` and `:2565`;
  - `claude/.claude/scripts/select-tests.py:653`;
  - `claude/.claude/scripts/tests/test_select_tests.py:614` and `:886`;
  - `claude/.claude/scripts/tests/test_transcript_denials.py:663`;
  - `docs/design-decisions/bats-core-adoption-declined.md:13` (where `_run_lib_call` now lives) and `:79`;
  - `docs/design-decisions/review-orchestrator-routing-gate.md:100`.
- Commit `.claude/plans/split-test-lib.md`, this plan, with phase 1.

**Phase 2**, one `code-writer` dispatch.
- Create `hooks/tests/test_lib_review_pr.py` (`_lib_review_pr_completion_marker_fields`, `_lib_parse_pr_identity`, the review-pr artifact helpers).
  - The banner beginning `# --- _lib_review_pr_completion_marker_fields -` (:1460) through `test_parse_pr_identity_accepts_dot_and_hyphen_alongside_alnum`. Its two locators already name `test_lib_markers.py` (phase 1).
  - `TestLibReviewPrArtifactPath` (:8169) through `TestLibReviewPrReviewerFindingsDir`.
- Create `hooks/tests/test_lib_git_refs.py` (`_lib_default_branch_from_origin_head`, `_lib_default_branch_or_guess`, `_lib_verification_cache_sentinel_present`, `_lib_head_tree_hash`). It takes the banner beginning `# --- _lib_default_branch_from_origin_head / _lib_default_branch_or_guess -` (:3017) through `test_head_tree_hash_capped_timeout_returns_absent_not_a_hang`.
- Create `hooks/tests/test_lib_fragment_matchers.py` (the fragment primitives through `_lib_commit_fragment_has_worktree_target`, their quote-blindness composition tests, `_lib_strip_shell_quotes`).
  - The banner beginning `# --- _lib_fragment_command_word / _lib_fragment_invokes_tool /` (:3575) through `test_commit_fragment_has_worktree_target_fails_safe_when_tool_missing`.
  - The banner beginning `# --- GH-783: caller-contract quote-blindness + composition -` (:4505) through `test_lib_strip_shell_quotes_composed_with_extract_git_subcmd_detects_quoted_subcommand`.
  - The banner beginning `# --- _lib_strip_shell_quotes -` (:7595) through `test_lib_strip_shell_quotes_sed_absent_returns_nonzero`.
- Create `hooks/tests/test_lib_command_words.py` (`_lib_command_words` and the extractors built on it). It takes the banner beginning `# --- _lib_command_words and the extractors built on it -` (:3851) through `test_command_words_python3_hang_is_capped_and_returns_the_killed_status`.
- Create `hooks/tests/test_lib_tool_argv.py` (`_lib_command_invokes_git_subcmd`, `_lib_command_invokes_tool_subcmd`, `_lib_tool_argv_from_subcmd` with its gh `--help` drift guard, `_lib_words_start_with`). It takes the banner beginning `# --- _lib_command_invokes_git_subcmd / _lib_command_invokes_tool_subcmd -` (:4545) through `test_gh_help_root_no_value_placeholder_flags_stay_within_help_and_version`, so the range ends with the drift guard (M3). Its banner comments "the fragment-matcher primitives above" (:4548) and "the _lib_extract_git_subcmd_args banner above" (:4570) name `test_lib_fragment_matchers.py`.
- Modify `hooks/tests/test_lib.py`. Delete the ranges first (M5) and prune imports. The banner comment ending "_lib_strip_shell_quotes's own comment above establishes" (:7711–7712) names `test_lib_fragment_matchers.py`. The banner comment ending "TestCommandInvokesGitSubcmd already uses above" (:8809–8810) names `test_lib_tool_argv.py`.
- Modify `claude/.claude/hooks/_lib.sh:2348`, the comment line that cites "test_lib.py's traversal-scope guard". Replace `test_lib.py's` with `test_lib_tool_argv.py's`, and change no other line.
- Modify the locators at:
  - `claude/.claude/scripts/tests/test_review_pr_finish.py:6`;
  - `hooks/tests/test_design_decision_files.py:123`;
  - `hooks/tests/test_lib_reviewer_round_state.py:637`;
  - `hooks/tests/test_guard_settings_session_keys.py:88`, `:118` and `:840`;
  - `claude/.claude/scripts/tests/test_default_branch_parity.py:54` and `:136`.

**Phase 3**, one `code-writer` dispatch. It starts only after #1318 merges or closes (M10).
- Create `hooks/tests/test_lib_config_gates.py` (`_lib_config_dir`, `_lib_worktree_enforcement_active`, `_lib_autonomous_shipping_sentinel_present`, `_lib_autonomous_shipping_active`, `_lib_permission_prompt_tracking_active`, `_lib_round_consult_gate_disabled`).
  - `_lib_sh_with_unreadable_schema` (:85) through `_lib_sh_with_missing_config_sh` (M3).
  - `_run_config_dir` (:5797) through `TestLibConfigDir`.
  - The first comment block after `test_lib_sanitize_for_terminal_does_not_strip_c1_bytes` (:6596) through `TestRoundConsultGateDisabled`. That block's first line recurs at :6788, so the node before it is the anchor.
- Create `hooks/tests/test_lib_resume_context.py` (`_lib_resume_context_tmpdir_root`, `_lib_resume_context_index_dir`, `_lib_print_recovery_hint`, `_lib_linked_worktree_root`, `_lib_continuity_file_resume_cwd`, the resume-context verdict, `_lib_sanitize_for_terminal`). It takes `_run_resume_context_tmpdir_root` (:5843) through `test_lib_sanitize_for_terminal_does_not_strip_c1_bytes`, so the range includes `TestLibResumeContextIndexDir` (M3).
- Create `hooks/tests/test_lib_credentials.py` (the shared credential-guard constants, `_lib_strip_env_file_flag_args`, the PEM block regex, `_lib_redact_credential_shaped_strings`, `_lib_config_lines`).
  - The banner beginning `# --- Shared credential-guard constants -` (:7304) through `test_lib_credential_value_regex_compiles_under_grep_and_jq`.
  - The banner beginning `# --- _lib_strip_env_file_flag_args -` (:7706) through `TestRedactCredentialShapedStrings`.
  - The banner beginning `# --- _lib_config_lines -` (:8603) through `test_lib_config_lines_counts_raw_line_numbers_through_skipped_lines`.
- Modify `hooks/tests/test_lib.py`. Delete the ranges first (M5) and prune imports.
- Modify `claude/.claude/hooks/_lib.sh:4960–4961`, the comment that cites `TestLibResumeContextIndexDir`'s docstring. Replace `test_lib.py` with `test_lib_resume_context.py`, and rewrap within those two lines so the comment keeps its line count.
- Modify `hooks/tests/test_resume_command_template_contract.py` at :5–6 and :16, per M7.
- Modify the locators at:
  - `claude/.claude/scripts/tests/test_find_consumed_continuity_file.py:154`;
  - `hooks/tests/test_advance_past_commit_stall.py:396`;
  - `hooks/tests/test_restore_authorization_boundary_on_compact.py:86`;
  - `hooks/tests/test_marker_lib.py:1994`;
  - `claude/.claude/scripts/tests/test_autonomous_shipping_active.py:4`, which names the new file and `TestAutonomousShippingActive` with no line range;
  - `hooks/tests/test_deny_credential_bash_reads.py:487` and `:578`.

**Phase 4**, one `code-writer` dispatch.
- Create `hooks/tests/test_lib_commit_predicates.py` (`_lib_length_ratchet_exceeded`, `_lib_command_concludes_commit`, `_lib_command_concludes_marker_gated_commit`, `_lib_split_fragments`' pipefail contract, the fragment-level commit predicates).
  - The banner beginning `# --- _lib_length_ratchet_exceeded -` (:8654) through `TestLengthRatchetExceeded`.
  - The banner beginning `# --- _lib_command_concludes_commit / _lib_command_concludes_marker_gated_commit -` (:8804) through `TestFragmentConcludesCommitQuotedDifferential`.
- Create `hooks/tests/test_lib_gate_diff_base.py` (`_lib_git_inprogress_state`, and `_lib_gate_diff_base`'s anchor and topology tests). It takes two ranges.
  - The banner beginning `# --- _lib_git_inprogress_state / _lib_gate_diff_base / _lib_staged_diff_hash -` (:9310) through `_git_inprogress_state` (:9326).
  - `_git_supports_sha256_object_format` (:9386) through `test_merge_tree_merge_base_flag_changes_computed_tree` (:9983).
  - The four helpers between those ranges go to `_lib_sh_helpers.py` (M4).
- Create `hooks/tests/test_lib_gate_diff_base_fallbacks.py` (`_lib_gate_diff_base`'s git-version, cap-fault and no-cap fallbacks, its documented origin-forgery behavior, the round-state key during a rebase, `_lib_staged_diff_hash`). It takes `_make_git_rejecting_write_tree` (:9986) through `TestStagedDiffHash`.
  - If #1318 has merged, the range starts at the first top-level node after `test_merge_tree_merge_base_flag_changes_computed_tree`. The docstring then names the merge-tree-failure tests in place of the git-version fallbacks (M10).
  - In `TestReviewerRoundStateKeyDuringRebase`'s docstring (:10401), "this file's other tests cover" becomes "the other tests here and in test_lib_gate_diff_base.py cover" (row 13).
- Create `hooks/tests/test_lib_review_ledger.py` (`_lib_acquire_append_lock`, `_ledger_sweep_window_days`). It takes `TestLibAcquireAppendLockCalledTwice` (:10498) through `TestLedgerSweepWindowDays`.
- Create `hooks/tests/test_lib_skill_management_plugin_lib.py` (`_lib_conflict_marker_deny_paths`, and the parity of the gate-base closure between the stowed and plugin copies). It takes the banner beginning `# --- _lib_conflict_marker_deny_paths -` (:10657) through `test_shared_closure_function_is_identical_across_stowed_and_plugin_lib`.
- Create `hooks/tests/test_lib_validate_plugin_id.py` (`_lib_validate_plugin_id`). It takes the dashed line directly above `# _lib_validate_plugin_id / _lib_missing_plugin_ids -- direct unit coverage` (:10777) through `TestLibValidatePluginId`.
- Create `hooks/tests/test_lib_missing_plugin_ids.py` (`_lib_missing_plugin_ids`, `_lib_file_exceeds_size_cap`, `_lib_read_guarded_settings`). It takes `_missing_plugin_ids` (:10946) through the end of the file.
- Only if #1318 has merged, create `hooks/tests/test_lib_trial_merge_verdict.py` (`_lib_trial_merge_verdict`). It takes the banner beginning `# --- _lib_trial_merge_verdict` through the last node before the `_lib_validate_plugin_id` banner block (M10).
- Modify `hooks/tests/_lib_sh_helpers.py`. Append `_gate_diff_base` (:9329) through `_assert_valid_tree_oid` (:9383) as one range. It holds `_gate_diff_base`, `_staged_diff_hash`, `_forge_state_marker` and `_assert_valid_tree_oid`.
- Modify `hooks/tests/test_lib.py`. Delete the ranges first (M5) and prune imports.
- Modify `docs/design-decisions/git-native-commit-hooks-declined.md:17` to name the same tests in their new homes. Those homes are `test_lib_commit_predicates.py` from `TestCommandConcludesCommit` to the end of the file, `_git_inprogress_state` in `test_lib_gate_diff_base.py`, and the four gate-diff helpers in `_lib_sh_helpers.py`.
- Modify the locators at:
  - `hooks/tests/test_require_skill_review.py:3285`, `:3297`, `:3451`, `:3697` and `:3722`;
  - `hooks/tests/test_lib_reviewer_round_state.py:336` and `:357`;
  - `hooks/tests/test_require_code_review.py:1200`, `:1221`, `:1243`, `:1558` and `:1584`;
  - `hooks/tests/test_require_plan_review.py:2479` and `:2499`;
  - `hooks/tests/test_check_skill_length.py:989` and `:1010`;
  - `hooks/tests/test_deny_pii_in_commits.py:1825`;
  - `hooks/tests/test_deny_private_project_refs.py:1788`;
  - `hooks/tests/test_marker_script.py:1269`;
  - `hooks/tests/test_review_ledger_script.py:3883`;
  - `hooks/tests/test_bootstrap_project_plugins.py:520` and `:528`;
  - `claude/.claude/scripts/tests/test_reconcile_project_plugins.py:9`.

This list names `claude/.claude/hooks/_lib.sh` only for the two comment edits in phases 2 and 3, and it names no file under `plugins/` (rows 5 and 23).

**Reuse:**
- `claude/.claude/scripts/tests/_move_fidelity.py`'s `nodes`, `remainder` and `test-ids` subcommands, unchanged (row 12). The scratch comment-block script imports its `_load_source` and `_comment_tokens` (M12).
- The existing `conftest.py` fixtures and helpers, such as `isolated_home`, `opted_in_with_worktree`, `git_repo`, `_worktree_lock_reason`, `REVIEW_WORKTREE_NAME` and `_add_worktree_under_worktrees_dir`. Whichever new file reads one imports it unchanged, as it does the `helpers.py` and `test_config_lib.py` names.

## Verification

Run these steps from the worktree root, for each phase.
- Every pytest pass, including select-tests runs, runs as a background Bash call that logs to the scratch directory. The step waits for it to finish before reading its result (rows 32 and 33).
- No command uses `$(...)`. A value that one call prints, such as the scratch path or a SHA, is typed out literally in every later call (row 31). Below, `<scratch>` and `<base>` stand for those typed-out values.

0. **Baseline, before any edit.**
   - Phases 2–4 first re-scan the open PRs that edit `test_lib.py` (G3, row 36) with `gh pr list --state open --limit 200 --json number,headRefOid,files --jq '.[] | select(any(.files[]; .path == "claude/.claude/hooks/tests/test_lib.py")) | "\(.number) \(.headRefOid)"'`. Any PR it lists other than #1318 goes to the engineer, who decides whether the phase waits for it, as phase 3 waits for #1318.
   - Phase 3 also confirms that #1318 has merged or closed (M10).
   - Run `mktemp -d /tmp/split-test-lib-p<phase>.XXXXXX` once, with the phase number typed in. `<scratch>` is the path it prints. A rebuild (M10) takes a new directory.
   - Run `git rev-parse HEAD`. `<base>` is the SHA it prints.
   - Run `git show <base>:claude/.claude/hooks/tests/test_lib.py > <scratch>/before_test_lib.py`, then `wc -l <scratch>/before_test_lib.py`. Phase 4 also copies `claude/.claude/hooks/tests/_lib_sh_helpers.py` to `<scratch>/before_lib_sh_helpers.py` the same way.
   - Run `scripts/venv-run.sh pytest claude/.claude/hooks/tests/test_lib.py claude/.claude/hooks/tests/test_scaled_timeout_shim.py claude/.claude/hooks/tests/test_resume_command_template_contract.py -m "not timing" --junitxml=<scratch>/before-parallel.xml`. Then run the same files with `-m timing -n0 --junitxml=<scratch>/before-timing.xml`.
   - The PR body records:
     - `<base>`, the rebuild rule and step 10's commands (M10);
     - the rollback statement (M11);
     - the manifest: each target file, each range's anchors with the base lines they resolved to, and each moved name's target file;
     - each declared locator edit, with its old and new text (M8);
     - M5's post-delete line counts;
     - for every run of `test-ids`, `nodes`, `remainder`, the comment-block check and its seeded copy in steps 2–4, the command's printed output and exit status, labelled with the step and the input files it ran on.
1. **Scoped suite.**
   - Stage the new files with `git add -- <paths>` (row 15).
   - Run `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py -m "not timing"`, then the same command with `-m timing -n0`. Every selected test passes.
   - In phase 1, select-tests selects the full suite on its own (row 14). No phase widens the run by hand.
2. **Test-ID parity.**
   - Rerun step 0's two passes over `test_lib.py`, the phase's new files and the two importers. Write the results to `<scratch>/after-parallel.xml` and `<scratch>/after-timing.xml`.
   - Before `test-ids` runs, each of the four XML files must exist, and `grep -c '<testcase ' <file>` must print a number above 0 for each. A missing file or a zero count fails this step. The residual's `timing` test keeps both timing files non-empty in every phase (row 32).
   - Run `scripts/venv-run.sh python3 claude/.claude/scripts/tests/_move_fidelity.py test-ids --before <scratch>/before-parallel.xml --before <scratch>/before-timing.xml --after <scratch>/after-parallel.xml --after <scratch>/after-timing.xml`. It exits 0, and its before and after counts are equal.
   - An outcome mismatch on a `timing` test counts only if it recurs on one re-run of the after side.
     - Rerun the after timing pass to `<scratch>/after-timing-rerun.xml`, which gets the same presence check. Then rerun `test-ids` with it in place of `after-timing.xml`, against the recorded `before-timing.xml`.
     - The before side is not re-run. After the first edit, the base exists only as `<scratch>/before_test_lib.py`, whose relative imports cannot resolve outside the package.
3. **Node fidelity.**
   - Run `_move_fidelity.py nodes --before <scratch>/before_test_lib.py --after <each new file> --after claude/.claude/hooks/tests/_lib_sh_helpers.py --moved <every top-level name in the phase's ranges>`.
   - `compared-nodes` equals the number of moved names.
   - The only findings are the phase's declared in-node locator edits (M8). For each one, `git diff --no-index` of its base span against its new span shows only the declared locator lines.
   - Phase 1 only: `grep -Fx` finds the bare `assert` line from :5176 in `test_lib_realpath.py` (row 12).
4. **Remainder and comment blocks.**
   - Run `_move_fidelity.py remainder --before <scratch>/before_test_lib.py --after claude/.claude/hooks/tests/test_lib.py --removed <the moved names> --changed <the declared residual nodes>`. Phase 1 declares `__doc__`, `test_jq_field_list_extraction_is_non_empty` and `test_review_pr_completion_marker_fields_wrong_session_returns_1`. No later phase declares a residual node.
   - The only `remainder-ast` finding allowed is phase 1's removal of the bare `assert`.
   - Each `remainder-comments` finding is a block that left with its section, or a declared locator edit. The comment-block check below confirms both.
   - Run the comment-block check (M12) as `scripts/venv-run.sh python3 <scratch>/comment_blocks.py`, with these inputs:
     - the base copies from step 0;
     - the path of `test_lib.py` and of each new file, plus `_lib_sh_helpers.py` in phases 1 and 4, the phases that write it;
     - the manifest's moved-name-to-file map, keyed by file name;
     - each declared locator edit to a top-level comment, as its base line and its new line.
   - The check passes when all of these hold.
     - Each after file's top-level comment blocks, in order, equal the base blocks whose owner maps to that file, in base order, with the declared edits applied. An owner the map does not name stays in `test_lib.py`. An extended file's own base blocks come first, so phase 4's `_lib_sh_helpers.py` keeps phase 1's blocks ahead of any it gains.
     - Each moved name is defined in exactly the file the map names.
     - The count guard holds (M12). The script prints the base copies' block count and each after file's count. The base count is above 0, the phase's new files' counts sum to more than 0, and all the after counts sum to the base count.
   - Then run the seeded copy (M12).
     - Pick a new file that takes a banner of two or more lines, and one line of that banner that occurs once in the file. Run `grep -vxF '<that line>' claude/.claude/hooks/tests/<that file> > <scratch>/<that file>`.
     - The dropped line shortens one block and changes no count, so only the per-file comparison can catch it.
     - Rerun the check with the seeded copy's path in place of that file's. The copy keeps the file's name, which the map keys on. The run must exit non-zero and name that file.
   - Step 5 covers the `import-diff` findings.
5. **Lint.** `scripts/venv-run.sh ruff check claude/.claude/ claude-skills/ plugins/ evals/` reports nothing. F401 and F821 prove that each file imports exactly what it reads.
6. **Size.** In every phase, `wc -l` shows at most 1,000 lines for `_lib_sh_helpers.py` and each file that phase creates. After phase 4 it also shows at most 1,000 for `test_lib.py`. Phases 1–3 check no size for `test_lib.py`, since its residual reaches the limit only after phase 4.
7. **Citations.**
   - Run `git grep -n 'test_lib\.py' -- . ':!docs/reports' ':!.claude/plans'`. Every hit names a test or helper that is still in `test_lib.py`, apart from three stale hits that predate the split (row 20): `test_require_memory_skill.py:31`, `test_require_plan_review.py:47` and `.claude/rules/bash-unit-test-seams.md:19`.
   - `git diff --name-only <base>` lists only this phase's Critical files.
8. **Production files.**
   - `git diff --quiet <base> -- plugins/` exits 0 in every phase.
   - `git diff --quiet <base> -- claude/.claude/hooks/_lib.sh` exits 0 in phases 1 and 4.
   - In phases 2 and 3, `git diff -U0 <base> -- claude/.claude/hooks/_lib.sh` shows one hunk, on that phase's Critical-files comment lines. The hunk changes only the cited file's name, plus, in phase 3, the wrap of its two lines.
9. **Residual, phases 3 and 4.**
   - After phase 3, `test_lib.py` defines neither `_lib_sh_with_unreadable_schema` nor `_lib_sh_with_missing_config_sh`, and `test_lib_config_gates.py` defines each once. Either name left in `test_lib.py` fails this step.
   - After phase 4, every top-level name left in `test_lib.py` comes from M3's residual ranges. Any other name stops the series for a plan revision (M9).
10. **Base check, at each handoff for merge** (M10).
    - Run `git fetch origin main`, then `git merge-tree --write-tree origin/main HEAD` in the phase worktree.
    - Exit 1 means the default tip conflicts with the phase (row 34), so the phase rebuilds from step 0.
    - Exit 0 means the phase syncs its branch with the default tip, per the `git-feature-branch-sync` skill. CI's two passes must then pass on the synced head before the engineer merges (row 32). A failure that CI on the default tip does not share also means a rebuild.
    - Any other exit status stops the phase for the engineer.

## Out of scope

- **Splitting `_lib.sh`, or #1117's in-file reorganization of it.** The Context sets both aside (row 4).
- **Editing `_lib.sh` beyond its two comments, or editing any plugin copy of it.** The engineer allowed only those two edits (row 5), and the plugin copies cite only tests that stay in `test_lib.py` (rows 3 and 23).
- **The 1,000-line enforcing check**, child F2 of `.claude/plans/code-file-size-splits.md`. It is separate work.
- **The other test files over the limit.** These include `test_lib_worktree_collision_guard.py` (2,193 lines) and child E's other seams: `test_skills.py`, `test_deny_private_project_refs.py` and `test_marker_script.py`. Each is its own split.
- **Moving tests into existing `test_lib_*.py` siblings.** See Approach.
- **A `comments` subcommand in `_move_fidelity.py`, or a scanner corpus that includes `_*_helpers.py`.** M12 uses a scratch script instead, and M4 accepts the scanner's narrower view (row 30). Each would be a repo change outside this plan's files.
- **A local revert rehearsal for any phase.** M11 gives the reason.
- **The stale citations listed in row 20.** They predate this split. The rule-file one would also need its own `ai-instruction-and-memory-files` review.
- **Dated records and other plans that cite `test_lib.py` line numbers** (row 19). They are preserved records.
- **Renaming, rewording, merging or fixing any moved test**, beyond the declared locator edits. #1118's invariant forbids editing any assertion.
- **A CHANGELOG entry.** `CHANGELOG.md:3` scopes the changelog to notable changes, and this split changes no hook, skill or script behavior.
- **The note on #1210 about the harness freeze.** It is outward-facing, and the session handles it at PR time.
