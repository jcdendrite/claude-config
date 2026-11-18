# Split test_skills.py by skill

## Context

Goal: decompose `claude-skills/skills/tests/test_skills.py` (10,144 lines at base `1c79879b`) into files of at most 1,000 lines each, split along real seams (by skill or cross-cutting concern), with no test lost and no assertion edited.

Ask: "Hey there should be a github issue filed for decomposing test_skills.py Can you find it and plan-it?" (the issue is #1118, whose first scope bullet is "`test_skills.py` by skill"; its other bullets are out of scope here). Engineer's answer on phasing, order and seams: "Let the plan-it process decide the number of PRs, the order, and the seams". Engineer's answer on the shared helper location: "skills/tests seems correct but let's make sure we are following proper conventions for test locations. Ask the sdet and verify-sources".

Why now: #1118 was reopened on 2026-10-30 when #1210 closed. Only the `test_lib.py` split shipped. The file-size policy in `docs/design-decisions/code-file-line-limit.md` sets 1,000 lines for test files, and the file is about ten times that.

Evidence gathered (this session, reports from read-only subagents, treat as leads where marked):
- Shape: the tree `claude-skills/skills/tests/` has no `conftest.py`, no `__init__.py`, and no fixtures. Sibling files are flat `test_<topic>.py`.
- Shared helpers: `_skill_file` (142 references), `_skill_body`, `_agent_body`, `_skill_description`, `_heading_section_text`, `_raw_heading_section_text`, `_assert_pinned_clause_right_bounded`, `_section_between`, `_normalized_anchor_text`, `_extract_citations`, and the `_all_*_paths` globbing helpers are read across many sections. Helpers with a single reader move with it.
- Largest single section: citation/cross-reference integrity, lines 3588-4728 (1,141 lines), which must itself split. The code-review pins (7045-7920, 876 lines) are under the limit but near it.
- Seam hazards from the SDET consult:
  - Two `monkeypatch.setattr(sys.modules[__name__], ...)` tests near line 8952 patch module globals that `_all_citation_extraction_doc_paths` reads (row 11).
  - `select-tests.py` hard-codes `test_skills.py` in `SKILLS_TESTS_IMPORTING_CONFIG` (line 305) and `SKILLS_TESTS_IMPORTING_SKILL_EVALS_RUNNER` (line 329). `test_select_tests.py:1776` mirrors it with a literal (row 5).
  - Seven parametrize sites build IDs from absolute paths, so before and after junit runs must come from the same checkout path (row 15).
  - `_CODE_REVIEW_STEP_0_1_HEADING` is defined twice with identical values (row 14).
- Import mechanics, verified at the pytest docs (`explanation/pythonpath.html`): in the default `prepend` import mode, a test file's directory goes to the front of `sys.path`, so a sibling helper module imports by bare name; test file basenames must be unique across unpackaged trees; default `python_files` does not collect `_*_helpers.py`. `pyproject.toml` sets no `--import-mode` and no `python_files`.
- Live citations of `test_skills.py` by name (about 12 live files plus dated records that stay unedited) were enumerated; the residual-file option keeps most of them true.
- Precedent: `.claude/plans/split-test-lib.md` and `claude/.claude/scripts/tests/_move_fidelity.py` (`nodes`, `remainder`, `test-ids`).

## Approach

Split `claude-skills/skills/tests/test_skills.py` over four sequential PRs into 19 new `test_<skill or concern>.py` files and one shared helper module, `claude-skills/skills/tests/_skill_md_helpers.py`. `test_skills.py` stays as a residual of about 970 to 990 lines holding the cross-skill corpus and invocation contracts, because two plugin files, one SKILL.md, three docs and both of `select-tests.py`'s skills-tests importer sets cite those tests by this file's name (M3).

Phase 1 creates the whole helper and moves the code-review and review-ledger pins. It also carries every edit to a global-trigger file, so it is the only phase whose select-tests run is the full suite (M1, M7). Phase 2 moves the review-gate pins, phase 3 the planning, PR-lifecycle, agent and memory pins, and phase 4 the handoff, citation and corpus-scan pins. Each phase copies its sections by line range from a scratch copy of its own base, so no test is retyped, and `_move_fidelity.py` plus a scratch comment-block check verify every move (M5, M13). A `code-writer` dispatch builds and verifies each phase and returns its tree uncommitted. The main session then reviews, commits and opens the PR, and, before each dispatch, the main session has asked the engineer to hold other `test_skills.py` merges until the phase merges (M1, M11).

Alternatives set aside:
- **One PR.** It would put about 9,200 moved lines in one review (row 2).
- **Three PRs.** Each would move about 3,100 lines. Four keep phases 2–4 between 2,100 and 2,700 moved lines and let the global-trigger edits ride in the smallest phase (rows 2, 6).
- **Deleting `test_skills.py`.** The residual's tests are cited by this name from two `plugins/` files (a version bump per plugin), from `plan-review/SKILL.md` (a `/skill-review` gate), and from `select-tests.py`'s importer sets and `test_select_tests.py:1776`'s hand-typed literal (rows 5, 7, 8). Keeping those tests here keeps every one of those sites true unedited.
- **One file per skill.** Many skills carry a single short class (`TestTightenProseScopeCarveOutPhrases` is 15 lines), and some pins read several skills at once (the prompt-contract sites, the dispatcher-sync tests). Each file instead holds one skill, or a few skills that share a concern, under the limit.
- **`conftest.py`, `claude/.claude/tests/helpers.py`, the residual, or a copy per file as the shared-helper home.** See M4.
- **Naming the new files in the comments of `select-tests.py`, `test_select_tests.py` and `_skill_auxiliary_files.py`.** See M7.
- **Moving tests into the existing siblings** (`test_trigger_detector.py`, `test_rules_frontmatter.py`, `test_plugin_manifests.py`). One file's fidelity check would then mix moved and pre-existing nodes, the reason `.claude/plans/split-test-lib.md` gives for the same choice.
- **A plan file per phase.** Every phase is a pure move this plan specifies. M10 sends a phase back for a plan revision when its base has drifted.

**Root:** `claude-skills/skills/tests/test_skills.py` is 10,144 lines at base `1c79879b`, about ten times the 1,000-line test-file limit (row 23). The split moves its tests into files of at most 1,000 lines, keeps every collected test ID and its outcome, and edits no assertion (row 31). `[verified: the Context's measurement, and Read of the file ending at :10144; docs/design-decisions/code-file-line-limit.md:19; #1118's invariant as quoted at .claude/plans/split-test-lib.md:9]`

**Givens:**
- G1. In pytest's default `prepend` import mode, an unpackaged test file's directory goes on `sys.path`, so a sibling module imports by bare name. Default `python_files` never collects `_skill_md_helpers.py`. Basenames across unpackaged trees must be unique. Reason: pytest owns import and collection. `[verified: pytest docs explanation/pythonpath.html, as relayed from this session's verify-sources check and not re-fetched; pyproject.toml:18 and :24 set pythonpath and addopts but no --import-mode or python_files]`
- G2. Merge is human-only, so each phase starts from a default-branch tip that already holds the previous phase. Reason: the engineer owns merge. `[verified: ~/.claude/CLAUDE.md, Working Style's merge bullet]`
- G3. Other PRs edit `test_skills.py` between phases, since it is where a SKILL.md change's pins land today. Row 30 gives the measured rate. Reason: their authors own them. `[verified: row 30's rate was re-derived from git log this pass; Verification 0 lists the open PRs per phase]`

**Mechanisms:**
- **M1: Four sequential phases, each one PR and one `code-writer` dispatch.** `anchors: root, G2, G3, row2, row6, row8, row21, row24, row29, row35`
  - Phase 1 moves about 1,770 lines, phase 2 about 2,170, phase 3 about 2,680 and phase 4 about 2,610 (row 2).
  - Phase 1 holds every edit to `select-tests.py` and `helpers.py`, the two global-trigger files the split touches, so only phase 1 runs the full suite (row 6, M7).
  - Each of phases 2–4 also edits a file under `claude/.claude/hooks/tests/`, so its select-tests run reaches `test_design_decision_files.py`, which scans this tree (row 21).
  - No phase splits into more than one dispatch. Every step edits `test_skills.py`, and all steps share one range manifest and one fidelity procedure.
  - Each later phase branches from the default tip after the previous phase merges.
  - The main session runs Verification 0's open-PR query and M11's merge-hold ask before the dispatch.
  - The dispatch runs the rest of Verification 0 through 10. It returns a verified, uncommitted tree and the PR-body material Verification 0 lists, and it commits nothing.
  - The main session then runs `/code-review`, `/skill-review` in phase 2 (row 8), the commit, `/ready-for-review` and the PR, and Verification 11 at handoff.
  - Each phase branch's first commit meets the diff-size gate (row 35). The main session asks the engineer for `Proceed anyway` once per phase branch, and on that answer runs `~/.claude/scripts/diff-size-ack.sh`. Row 34's answer approved the projected total and writes no latch.
  - A rebuild (M11) stays on the phase's branch name after the main session returns it to the fetched default tip, so its latch still holds. A phase moved to a new branch asks again.
- **M2: 19 new test files, each one skill or one concern, each at most 1,000 lines.** Critical files names each file's ranges. The largest are about 840 lines (`test_ready_for_review_skill.py`), 820 (`test_handoff_and_resume_skills.py`) and 815 (`test_pr_lifecycle_skills.py`, `test_review_dispatch_contracts.py`) (row 2). Every basename is new (row 18). `anchors: root, G1, row1, row2, row18, row24`
  - The 1,141-line citation block splits three ways. Its extractor and resolver go to the helper (M4), its grammar tests to `test_skill_citation_grammar.py`, and its doc-specific pins to `test_doc_cross_references.py`.
  - The 876-line code-review block splits at `_CODE_REVIEW_DISPOSITION_CHALLENGE_ANCHOR`. The clean-definition and disposition-rule regions go to `test_code_review_disposition_rules.py`, and the disposition-challenge and round-cap regions go to `test_code_review_challenge_and_round_cap.py`. `TestCodeReviewDispositionChallengeRegionPin` reads six constants that the disposition-rule regions define, so those six go to the helper (row 3).
- **M3: `test_skills.py` keeps the cross-skill corpus and invocation contracts that other files cite by its name.** `anchors: row2, row4, row5, row7, row8`
  - The residual is the module docstring and imports, `_skill_description` through `BUILTIN_NAME_ONLY_SKILLS`, the specialist, name-only, name-dispatched and model-invokable trigger contracts, frontmatter strictness, description length, the listing budget, the disposition-rule anchors test, the Trigger-A fence scan, the skillOverrides docs cross-check, the destructive-cleanup permissions pin, and the config-get key check. Critical files gives the ranges.
  - These hold every test that a keeper site in row 7 cites, so none of those sites is edited.
  - Keeping `test_disposition_rule_anchors_present` and `test_every_config_get_key_named_in_a_skill_is_in_the_config_schema` here keeps `test_skills.py` a true member of both importer sets, so `select-tests.py:329` and `test_select_tests.py:1776` stay unedited (rows 4, 5).
  - Rejected, also keeping the prompt-contract sites that `agent-review/SKILL.md:181` cites. They add about 280 lines and push the residual past 1,000 (row 2), so that SKILL.md gets one locator edit in phase 2 instead (M7).
- **M4: One helper module, `claude-skills/skills/tests/_skill_md_helpers.py`, holds exactly the top-level names that two or more planned files read, plus the names its own helper functions read, and phase 1 creates it whole.** `anchors: G1, row3, row6, row10, row11, row12, row13, row16, row17, row25, row26, row39`
  - Critical files lists its names under phase 1. Every other helper moves with its only reader (row 3).
  - A name that ruff's F821 shows crossing a file boundary, which row 3 does not list, stops the phase for a plan revision (M10). The helper therefore changes only in phase 1.
  - Every importing file uses the bare form `from _skill_md_helpers import ...`, as the tree already does for `helpers` (row 16). The name differs from `claude/.claude/scripts/tests/_skill_helpers.py` so the two are not confused.
  - The helper imports none of `_config`, `run_skill_evals` and `validate_skill_structure`, so `select-tests.py`'s by-name importer sets keep naming the test files that import those modules (row 5).
  - The two monkeypatch tests move with `_all_citation_extraction_doc_paths`, the function whose module globals they patch (row 11).
  - Rejected, `conftest.py`. The tree has no fixtures, so it would exist only as an import target. An unpackaged `conftest` is the module-name collision `.claude/rules/test-tree-packaging.md` and `TestConftestModuleNamesAreUnique` describe (row 17).
  - Rejected, `claude/.claude/tests/helpers.py`. It is a global trigger, so every later edit there would run the full suite, and these helpers serve only this tree (row 6).
  - Rejected, the residual as the home. Every new file would import from a test module that calls few of the names it exports.
  - Rejected, a copy per file. `_skill_file` alone would exist in most of the 20 files, and `nodes` reports each copy as `node-duplicate` (row 13).
  - Accepted loss: pytest does not rewrite the helper's `assert` statements, because the helper is not a test module. A failing helper assert loses pytest's introspection of intermediate values, and no outcome changes. Rejected, `pytest.register_assert_rewrite`. It must run before the helper's first import, and the tree has no `conftest.py` or package `__init__.py` to call it from (rows 16, 39).
  - Accepted loss: `TestCrossDomainReadCompleteness` parses only `test_*.py`, so `plugins`, `claude/.claude/CLAUDE.md`, `claude/.claude/agents` and `plan-architect.md` leave its view for this tree. Explicit selection tests still pin each of those routes to `SKILLS_TESTS_DIR` (row 10).
- **M5: Copy by line range from a scratch base copy, never retype.** The procedure is `.claude/plans/split-test-lib.md` M5, with `test_skills.py` in place of `test_lib.py`. `anchors: G3, row1, row12, row15, row28`
  - Each range is resolved at the phase's own base. Its start is the quoted banner or first node Critical files names, found by grep. Its end is the last line of its named last node. Line numbers here are `1c79879b` guides only.
  - A range starts at the first non-blank line after the previous top-level node ends, so the comments above its first node move with it.
  - Each new file is its hand-written header, then one `sed -n '<start>,<end>p' <scratch>/before_test_skills.py >> <file>` call per range, with one `printf '\n\n' >> <file>` call between ranges.
  - The range delete is the phase's first edit of `test_skills.py`: one `sed '<a>,<b>d;<c>,<d>d;...' <scratch>/before_test_skills.py > claude-skills/skills/tests/test_skills.py` call naming every range. Right after it, `wc -l` must equal the base count minus the summed range lengths, before any other edit.
  - Ranges that phase 1's helper extractions split are contiguous again at later bases. Critical files names each later range as it stands at its own base.
  - Every before and after pytest run of a phase comes from that phase's worktree path, because some parametrize IDs embed absolute paths (row 15).
- **M6: Headers.** `anchors: G1, row16, row36`
  - Each new file opens with a one-line docstring naming the skills or concern it covers, and no phase, plan or issue. Then comes `from __future__ import annotations`, then only the imports its code reads, ordered by `ruff check --select I --fix <file>`.
  - A new file carries one kind of comment outside its moved ranges. Above each import of a module that `pyproject.toml`'s `pythonpath` resolves, it copies verbatim the base import-region block that names that module's directory: :49–50 for `_config`, :53–54 for `_skill_auxiliary_files`, :57–58 for `helpers` and :77–79 for `validate_skill_structure`. Every file in this tree that imports `helpers` carries such a comment (row 36).
  - The `from _skill_md_helpers import (...)` statement carries no comment, because a same-directory import needs no `pythonpath` entry (G1).
- **M7: Out-of-tree citations of `test_skills.py`.** `anchors: row5, row6, row7, row8, row9, row20, row22, row32`
  - In `select-tests.py`, `test_select_tests.py` and `_skill_auxiliary_files.py`, phase 1 rewrites each file-name locator whose referent leaves `test_skills.py` by the end of the series into the `SKILLS_TESTS_DIR` directory form. It keeps any test or helper name the sentence gives, and changes verb agreement only where the new subject needs it. These comments explain why a change selects `SKILLS_TESTS_DIR`, which those rules select as a whole (row 9), so the directory form is true at every phase boundary.
  - Phase 1 also adds `test_code_review_disposition_rules.py` to `SKILLS_TESTS_IMPORTING_CONFIG` (`select-tests.py:305`), because that file imports `_config` (row 4). `helpers.py:3785` names its referent's new file, since phase 1 moves the referent there for good.
  - Every other citing site names the referent's new file, in the phase that moves the referent. Critical files lists each one (row 32).
  - Keeper sites (row 7), the two records in `docs/cost-levers-considered.md` and the commit-pinned recall fixture stay unedited (row 22).
  - Rejected, file names in the routing files. Every phase that moves a cited referent would then edit `select-tests.py` and run the full suite (row 6).
  - Rejected, editing the routing files once after phase 4. Their comments would read false for three phases.
  - No file under `plugins/` is edited, and neither is `plan-review/SKILL.md` (rows 7, 8). `agent-review/SKILL.md` (phase 2) must pass `/skill-review`, and `.claude/rules/citation-grammar.md` (phase 4) gets `/code-review`'s rule-file review.
- **M8: In-tree locators.** A position word ("above", "below", "further below", "this module", "This section") whose referent the split moves to another file is rewritten to name the referent's file. When the referent reaches its final file only in a later phase, the rewrite names the referent itself instead. The surrounding claim stays as written. `anchors: row19`
  - Critical files lists the nine sites, L1–L9, by phase. Each PR body declares each edit with its old and new text.
  - The implementer's own grep may find more inside moved or residual code. The same rule covers them, and only declared edits may appear as fidelity findings (Verification 4–6).
- **M9: `_CODE_REVIEW_STEP_0_1_HEADING` is assigned twice to the same literal, and phase 1 keeps one assignment, in the helper.** Its readers land in two files, so M4 already sends it to the helper. Phase 1 moves the `:8001` assignment there and deletes the `:9646` one. `nodes` cannot take this name, because the base defines it twice, so Verification 4 checks it by exact-line grep. `anchors: row3, row13, row14`
- **M10: Each phase re-resolves its anchors at its own base, and stops for a plan revision instead of guessing a home.** `anchors: G3, row1, row18, row30`
  - It stops when a quoted anchor is missing or matches more than one line, when a planned name is missing, or when a planned basename already exists in a collected tree (row 18).
  - A top-level name added to `test_skills.py` after `1c79879b` goes to the planned file whose skill or concern its body reads, per that file's docstring.
    - If this phase or an earlier one creates that file, this phase moves the name there, in base order. The PR body declares it, and it is listed to `nodes`.
    - If a later phase creates that file, the name waits for that phase.
    - A name that fits no planned file, or that would push a file past 1,000 lines, stops the phase.
- **M11: A conflict with the default tip at handoff rebuilds the phase, and a merge hold keeps conflicts rare.** `anchors: G2, G3, row2, row30, row37`
  - The rule, its `git merge-tree --write-tree` check and its escalation after a second rebuild are `.claude/plans/split-test-lib.md` M10's, applied to this plan's files. A rebuild is a new dispatch from Verification 0.
  - Before that dispatch, the main session asks the engineer to confirm a reset of the phase branch to the fetched default tip. It keeps the branch name, so the diff-size latch holds. Verification 0 runs only after the reset, because its basename `find`, `<base>` and anchor checks would otherwise read the failed attempt's files and head.
  - The reset discards the failed attempt's commits. For phase 1 that includes the plan file's commit, so the main session carries `.claude/plans/split-test-skills.md` over (saved outside the tree before the reset) and commits it with the rebuilt phase.
  - Other PRs touched `test_skills.py` 55 times in 63 days (row 30), and the four phases together move about 91% of its lines (row 2). One open phase moves 17% to 26% of them, and every phase also rewrites the import block, so a competing merge that touches the file conflicts with an open phase often enough to hold merges.
  - Before each dispatch, the main session shows the engineer Verification 0's open-PR list and asks for a merge hold: no other PR that edits `test_skills.py` merges from the phase's `<base>` until the phase merges. Any listed PR the engineer wants first merges before `<base>` is recorded.
  - Each phase runs its dispatch, review and `/ready-for-review` back to back, so a hold spans one phase.
  - Without a hold, the second-rebuild escalation still caps each phase at two rebuilds before the engineer decides.
  - At each handoff, the main session re-runs the open-PR query (Verification 11). The PR body lists each PR it prints, next to the old-name-to-new-module map. A PR green on the old base can merge after the phase with no conflict and then fail on a moved name (row 37), so the map is its author's notice. The engineer decides merge order against those PRs.
- **M12: Rollback is a last-in-first-out `git revert` of each phase's squash commit.** `anchors: G2, row27`
  - Phases 2–4 import the helper that phase 1 creates, and every phase prunes `test_skills.py`'s import block, so each phase reverts cleanly only after every later phase.
  - A revert stays mechanical only while no later commit touches a file the phase created or a range it deleted. After that, rollback is a forward fix built under this plan's fidelity procedure.
  - Each PR body carries this statement.
  - A revert's real test is its own PR, because CI runs the full suite on every push at the default tip the revert meets (row 40).
  - No phase rehearses its revert locally. A rehearsal at a phase's branch point predates every later commit, so it cannot test the revert-window condition above, and an out-of-order revert is what leaves `test_skills.py` uncollectable, which the order above forbids.
- **M13: A scratch script checks that every top-level comment block lands in its owner's file.** `nodes` and `remainder` both pass a banner left behind unchanged (row 13). The script, its pass condition, its count guard and its seeded-copy run are `.claude/plans/split-test-lib.md` M12 and Verification 4, applied to this plan's files, with the changes below. `anchors: row13, row19, row36, row38`
  - An import-region block is a top-level comment block whose next top-level statement in the parsed module's `tree.body` is an `import` or a `from` import. The base has four, at :49–50, :53–54, :57–58 and :77–79 (row 36).
  - Import-region blocks are left out of the owner comparison and the count guard, so the owner rule never reaches past the imports to `_PLUGINS_DIR`. The check asserts two things about them instead:
    - `test_skills.py`'s import-region blocks equal the base's, in order, less each block whose import the phase prunes. Only phase 4 prunes one, :53–54 with the `SKILL_AUXILIARY_MD_NAMES` import (row 38), and its PR body declares it.
    - Each new file's import-region blocks are verbatim copies of base import-region blocks (M6).
  - Every other block's owner is the first named top-level node after it. A block with no following named node has no owner, and the check always reports it as a mismatch for the file that holds it. The base has none, and the stranded-banner seed (Verification 6) creates one. Every banner in this split lands with that owner (row 19), so no owner override is needed.
  - The seeded run uses two shapes, and each must fail (Verification 6). One drops a banner line from a new file. The other re-inserts a moved banner into `test_skills.py`, the stranded-banner failure the check exists for.
  - Rejected, a `comments` subcommand in `_move_fidelity.py`. That is a repo change with its own seeded-defect tests (Out of scope).

**Assumption rows:**
1. Every top-level node after the imports falls in exactly one range that Critical files assigns: to a new file, to the helper, or to the residual. A walk over every top-level `def`, `class` and assignment line at `1c79879b`, range by range, accounts for every line but about 30 blank separators. `[verified: Grep of top-level def/class/assignment lines in test_skills.py this pass]`
2. Sizes. Moved lines per phase are about 1,770, 2,170, 2,680 and 2,610. Each new file adds about 25 to 50 header and separator lines, so the largest files land near 840, 820 and 815 lines, the helper near 560, and the residual near 970 to 990. M5's delete leaves the blank separators between ranges in place, so a scratch dry run of all four phases' deletes over the base leaves 986 lines before phase 4's import edits, which change it by about 15. That leaves only 10 to 30 lines of headroom under 1,000. `[verified: staff-platform-engineer's round 2 dry run of M5's delete over Critical files' ranges, which this pass did not repeat; the per-phase moved counts are arithmetic over row 1's base spans; Verification 0 repeats the dry run in phase 1 and Verification 8 measures the real sizes]`
3. Shared-name readers. `[verified: Grep of each listed name's references this pass]`
   - Read in two or more planned files, so bound for the helper:
     - `_PLUGINS_DIR`, `_GLOBAL_CLAUDE_MD`, `_AGENTS_DIR`, `_agent_body`, `_skill_body`, `_skill_file`, `_all_skill_md_paths`, `_TRIGGER_A_EXCLUSION_MARKER_RE`, `_FENCE_OPEN_RE`, `_INLINE_CODE_RE`, `_blank_frontmatter`, `_closed_fence_line_indices`, `_all_skill_md_files`, `_CITATION_WITH_TARGET_RE`, `_BARE_CITATION_RE`, `_Citation`, `_extract_scope_anchor_region`, `_heading_section_text`, `_raw_heading_section_text` and `_assert_pinned_clause_right_bounded`.
     - `_fenced_code_blocks` and `_fence_excluded_by_marker`, also read by the stowed-path contract (:9073–9074).
     - `_blank_code_regions`, also read by pr-description (:1690).
     - `_extract_citations`, also read at :6077, :6092 and :9980.
     - `_resolve_citation_target`, read by the helper's `_assert_citation_resolves_to_heading` (:3795) and by the citation-grammar code (:3730, :4675, :4696).
     - `_assert_citation_resolves_to_heading`, also read at :4482, :9852 and :9964.
     - `_normalized_anchor_text`, also read across the code-review regions (:7198–7826).
     - `_scope_exempt_change_type_row_text`, also read by the prompt-contract site (:5778).
     - `_change_type_table_left_columns` has one reader file, phase 2's scope-rules range (:5225, :5259, :5276). It goes to the helper only because it sits contiguously with `_scope_exempt_change_type_row_text` in one range, and this is a deliberate exception to M4's rule.
     - `_TRIGGER_A_EXCLUSION_MARKER_RE`, `_FENCE_OPEN_RE`, `_INLINE_CODE_RE`, `_CITATION_WITH_TARGET_RE`, `_BARE_CITATION_RE` and `_Citation`, listed above, are read only by helper functions that other files read. They go to the helper as the closure of those functions.
     - `_STEP4_HEADING` (:8707, :9379).
     - `_section_between`, also read at :3989 and :8546.
     - `_CODE_REVIEW_FINDING_DISPOSITION_HEADING` (:7293, :9369), `_CODE_REVIEW_RIPPLE_HEADING` (:7990, :9329) and `_CODE_REVIEW_STEP_0_1_HEADING` (:8013, :9994).
     - Six constants that the disposition-rule ranges define and `TestCodeReviewDispositionChallengeRegionPin` also reads, each given as its definition line, then its reads in that class: `_CODE_REVIEW_CONTRADICTION_ROUTE_ANCHOR` (:7077; :7683), `_CODE_REVIEW_DEFER_INVARIANT_ANCHOR` (:7210; :7642), `_CODE_REVIEW_NEW_PRIMITIVE_ROUTE_ANCHOR` (:7238; :7692), `_CODE_REVIEW_DEFER_CRITERIA_HEADING` (:7318; :7665), `_CODE_REVIEW_INVALID_DEFER_HEADING` (:7319; :7666) and `_CODE_REVIEW_HEAVIER_MECHANISM_RULE_OPENING` (:7320; :7693–7695).
     - `_all_doc_paths` (:4405, :9017, :9153).
     - `_PLAN_ARCHITECT_PATH` and `_PLAN_ARCHITECT_CONSULT_HEADING` (:6617, :7624–7801, :9923).
   - Read in one planned file only: `_skill_description` and the skill-discovery helpers (residual), the `_bullet_*` helpers (pr-lifecycle), `_ROUTING_MD_PATH` (dispatch contracts, which also takes the :10124 test that reads it), the `_READY_FOR_REVIEW_*` headings, `_PLAN_ARCHITECT_DESIGN_PRINCIPLES_HEADING`, the plan-review tripwire constants other than `_STEP4_HEADING`, `_list_items`, `_write_skill_files`, `_respond_pr_step3_text`, `_stowed_path_templating_matches`, `_all_agent_md_paths`, `_strip_yaml_frontmatter` and `_all_citation_extraction_doc_paths`.
   - Names not listed were not grepped one by one this pass. Round 1's staff-sdet AST reader walk over every range found no other cross-file read, and this pass did not re-run it, so their placement is `[unverified]`. Ruff's F821 catches any that cross a file boundary, and M4 then stops the phase.
4. `config_schema` has two code readers, `TestCodeReviewFixPlanPilotRegionPin` (:7492) and `test_every_config_get_key_named_in_a_skill_is_in_the_config_schema` (:7924). `extract_governing_rule` has one, `test_disposition_rule_anchors_present` (:2802). Its hits at :4816 and :4821 are a docstring. `[verified: Grep and Read]`
5. `SKILLS_TESTS_IMPORTING_CONFIG` (`select-tests.py:305`) and `SKILLS_TESTS_IMPORTING_SKILL_EVALS_RUNNER` (:328–331) name `test_skills.py`. The `CONFIG_MODULE` and `CONFIG_DIR_MODULE` rows select those members file by file (:934–942), and `test_select_tests.py:1776` hand-types the `test_skills.py` literal. `[verified: Read]`
6. `select-tests.py` and `helpers.py` are two of the three `GLOBAL_TRIGGER_PATHS` (`select-tests.py:531–535`), and a change to any of them selects the full suite (:1056–1058). `[verified: Read]`
7. Keeper sites cite tests the residual keeps, so they stay true unedited. `[verified: repo-wide Grep for test_skills outside .claude/plans, and Grep for each cited test's location]`
   - `plan-review/SKILL.md:40`, `plugins/claude-hook-review/skills/claude-hook-review/SKILL.md:37` and `docs/worktree-bash-guard.md:80` cite the Trigger-A scan.
   - `plugins/skill-management/scripts/validate_skill_structure.py:5` cites the suite that imports the validator, which the residual still does.
   - `evals/README.md:308` cites `test_disposition_rule_anchors_present`. `test_rules_frontmatter.py:406` cites `TestCorpusBudgetFunction`. `claude/.claude/hooks/tests/test_doc_counts.py:132` cites `BUILTIN_NAME_ONLY_SKILLS`.
   - `docs/design-decisions/skill-review-gate-disarms-on-empty-base-relative-diff.md:79` cites `test_frontmatter_parses_strictly` (:2441) and `corpus_budget_violations`.
   - `select-tests.py:329` and :392 (whose line numbers were already stale), and `test_select_tests.py:1455`, :1543–1546 and :1776.
8. Editing any file under a plugin directory requires that plugin's version bump, and `require-skill-review.sh` blocks a commit holding a SKILL.md edit until `/skill-review` passes. `[verified: .claude/rules/review-pipeline-dispatch.md]`
9. `select-tests.py` selects `SKILLS_TESTS_DIR` as a whole for every skills-tests read its comments explain: the skills-tree rules (:705–707), the evals README (:926), the auxiliary-files module (:931), and the plugin, docs, settings and CLAUDE.md rows (:960–1012). `[verified: Read]`
10. `TestCrossDomainReadCompleteness` parses only `test_*.py` (`_test_corpus`, `test_select_tests.py:143–161`) and resolves only single-name module assignments over five seeded constants (:44–50, :117–140). Explicit selection tests still pin each route the helper's path constants carry: the agents dir (:1269–1277), `GLOBAL_CLAUDE_MD` (:1465–1474), plugin skills (:744–751) and plugin agents (:753–760, :1553–1560). `[verified: Read]`
11. The two monkeypatch tests patch `REPO_ROOT` and `CLAUDE_DIR` on `sys.modules[__name__]` (:8959, :8969). `_all_citation_extraction_doc_paths` reads both as its own module's globals to glob the two rules dirs (:8933–8946), so the tests must share its module. Each still reaches its `matched no` assertion while `_all_doc_paths` reads the helper's unpatched `REPO_ROOT`. `[verified: Read of :8897–8971; Verification 3 confirms the outcomes]`
12. No test under `claude-skills/skills/tests/` carries `mark.timing` or reads `__file__`, and the only module patches are at :8959 and :8969. `[verified: Grep]`
13. `_move_fidelity.py` facts. `[verified: Read]`
    - `nodes` reports `node-missing` for a moved name the before file does not define exactly once (:292–295), and `node-duplicate` for one defined more than once across the `--after` files (:300–303).
    - `remainder --removed` requires the name to be gone after (:413–417).
    - `test-ids` keys each testcase by the classname parts after its last `test_` part, plus its name (:741–746). It filters by `--file-prefix` (:749–751, :786), and fails when neither side has a testcase (:794–795).
14. `_CODE_REVIEW_STEP_0_1_HEADING` is assigned at :8001 and :9646 to the same literal. `[verified: Grep]`
15. Parametrize IDs at seven sites, :2754, :8996, :9008, :9017, :9133, :9144 and :9153, come from `ids=lambda p: str(p)` over absolute paths. So before and after runs must share a worktree path, and a finding line that names a test ID carries the worktree's absolute path. `[verified: Grep this pass]`
16. `claude-skills/skills/tests/` has no `__init__.py` and no `conftest.py`, so a bare sibling import is correct there. `TestNoBareSameDirectorySiblingImports` scans only the three `claude/.claude` trees and skips unpackaged ones (`claude/.claude/tests/test_pytest_collection_config.py:454–467`, :486–487). `[verified: Glob and Read]`
17. `.claude/rules/test-tree-packaging.md:9–13` and `TestConftestModuleNamesAreUnique`'s docstring (`test_pytest_collection_config.py:249–254`) describe how two `conftest` modules without a package identity collide under prepend mode. `[verified: Read]`
18. No file anywhere in the repo has any planned basename, and `pyproject.toml:18` lists the `pythonpath` directories that a bare name also resolves against. `[verified: Glob; Read]`
19. In-tree locators. This pass grepped `test_skills.py` for "above", "below", "this file", "this module", "This section", "further below", "elsewhere in", "earlier in" and "later in", and read each hit outside a string literal. The hits whose referent changes file are L1–L9 in Critical files. Every other hit is pinned SKILL.md text or points inside its own planned file. `[verified: Grep and Read]`
20. `test_design_decision_files.py` globs `claude-skills/skills/tests/*.py` for design-decision citations (:163). `test_skills.py` cites §8 at :27 (residual) and §58 at :1353 (phase 3's `test_agent_body_pins.py`). That makes the comment at :151–153, which calls `test_skills.py` its tree's sole citing file, false in phase 3. `[verified: Read and Grep]`
21. Phases 2–4 each edit a file under `claude/.claude/hooks/tests/` (`test_agent_roster.py`, `test_design_decision_files.py`, `test_hook_alignment.py`), so each select-tests run selects that tree, including `test_design_decision_files.py`. No skills-tests rule selects that file. `[verified: select-tests.py:702 maps any path under the hooks dir to HOOKS_TESTS_DIR, and the skills-tests rules at :705–707 add only SKILLS_TESTS_DIR; Verification 2 still checks the printed targets]`
22. Records left unedited: `docs/cost-levers-considered.md:379` and :405 narrate a past pass, and `evals/fixtures/explore-recall-cases.json` is ground truth pinned at commit `eff170d05e73` (its :2). `[verified: Grep and Read; ~/.claude/CLAUDE.md Scope discipline, Axis 3]`
23. The limit is 1,000 lines for test files, and nothing enforces it yet. `[verified: docs/design-decisions/code-file-line-limit.md:19 and :50–57; Grep found no enforcing test]`
24. The engineer delegated the phasing. `[engineer-verified: "Let the plan-it process decide the number of PRs, the order, and the seams"]`
25. The engineer placed the helper in this tree, pending convention checks. `[engineer-verified: "skills/tests seems correct but let's make sure we are following proper conventions for test locations. Ask the sdet and verify-sources"]`
26. The session's summary, that the SDET and pytest-docs checks support a sibling helper imported by bare name and reject `conftest.py` and `claude/.claude/tests/helpers.py`, is the session's own gloss. G1 and rows 16–18 re-check what this plan relies on. `[unverified]`
27. The default branch takes squash merges only, so each phase lands as one revertable commit. `[unverified — relayed through .claude/plans/split-test-lib.md row 29; not re-read this pass]`
28. The worktree Bash guard refuses a `$(...)` assigned in one statement and reused in a later one, so every later call types the scratch path and base SHA out literally. `[verified: ~/.claude/CLAUDE.md, Working Style's script-first bullet]`
29. `select-tests.py` forwards its own arguments to pytest (:1336). `[verified: Read]`
30. Churn. 55 commits touched `test_skills.py` in the 63 days before 2026-11-16, and 11 of them came on or after 2026-11-01. M11's merge hold bounds the rebuilds this causes. `[verified: git log --oneline --since="63 days ago" -- claude-skills/skills/tests/test_skills.py | wc -l gave 55 on 2026-11-16, and the same command with --since=2026-11-01 gave 11. The round 1 reviewer's figure of 54 was one day earlier]`
31. #1118's acceptance invariant: the collected node IDs are equal before and after, and no existing assertion is edited. `[verified: .claude/plans/split-test-lib.md:9, which quotes #1118; the issue itself was not read this pass]`
32. Each file-level citing site in Critical files cites the referent named there, and that referent lands in the file named there. `[verified: Grep with context of every site this pass, and of each referent's base line]`
33. Phase 1's deletion of the duplicate `_CODE_REVIEW_STEP_0_1_HEADING` assignment (M9) is in scope. `[engineer-verified: "Keep the dedupe (Recommended)"]`
34. The plan's projected diff, about 19,300 changed lines over four PRs, stays as four phases. `[engineer-verified: "Proceed anyway"]`
35. `require-diff-size-prompt.sh` denies a `git commit` that brings the branch's cumulative diff against `origin/<base>` to 400 or more changed lines, `.claude/plans/` excluded. The deny holds until the engineer answers `Proceed anyway` and `diff-size-ack.sh` writes the branch's latch. The latch is per branch, and the hook's roster bar denies the ack command to a `code-writer`. `[verified: claude/.claude/hooks/require-diff-size-prompt.sh:4–14 and :47–49]`
36. `test_skills.py`'s import region holds four comment blocks: :49–50, :53–54, :57–58 and :77–79, above the `_config`, `_skill_auxiliary_files`, `helpers` and `run_skill_evals` imports. Every file in `claude-skills/skills/tests/` that imports `helpers` carries a `pythonpath` comment above that import: `test_skills.py`, `test_plugin_manifests.py:16–17`, `test_rules_frontmatter.py:52–53` and `test_trigger_detector.py:24–25`. `[verified: Read of test_skills.py:1–96; Grep of the tree]`
37. A PR that adds a test to `test_skills.py` reading a name an earlier phase moved can merge after that phase with no textual conflict. It then fails with `NameError`, because each phase leaves `test_skills.py` importing only the names it still reads (Verification 7). This pass did not check whether branch protection requires up-to-date branches, or whether `gh pr list --json files` truncates a large PR's file list. `[unverified — inferred, not reproduced; gh api repos/<owner>/<repo>/branches/main/protection --jq .required_status_checks.strict and gh pr list --help settle the two open facts]`
38. `SKILL_AUXILIARY_MD_NAMES` is read in code at :3540, :3710 and :4629, all in phase 4's URL-placement and citation-grammar ranges. So phase 4 prunes its import, and the :53–54 block above it, from `test_skills.py`. `[verified: Grep this pass]`
39. pytest rewrites asserts only in the test modules its collection discovers and in `conftest.py` files, and `pytest.register_assert_rewrite` must run before its target module's first import. `[verified: staff-sdet's round 2 read of _pytest/assertion/rewrite.py, which seeds conftest at :80, rewrites conftest.py at :229, matches python_files patterns at :242 and registers names at :261; this pass did not re-read it]`
40. CI runs the full test suite on every push, as the repo's contributor instructions state. `[verified: CLAUDE.md Commands section's "CI runs the full suite on every push"]`

## Critical files

Below, `tests/` means `claude-skills/skills/tests/`.
- Each range names its first line by a quoted banner or comment prefix or a node name, and its last node by name (M5). The phase greps for both at its own base.
- `:N` is the `1c79879b` line, a guide only (M10).
- Each new file's parenthesis names what its docstring covers (M6).
- Each phase is one `code-writer` dispatch over that phase's files. It runs Verification 0–10 and returns its tree uncommitted, and the main session commits (M1).

**Phase 1.** Its select-tests run is the full suite (row 6).
- Create `tests/_skill_md_helpers.py`, with the docstring `"""Helpers shared by two or more of the claude-skills/skills/tests test files."""`. It takes these ranges, in base order:
  - `_PLUGINS_DIR` with its comment (:88) through `_skill_file` (:117);
  - `_all_skill_md_paths` (:2628–2633);
  - `_TRIGGER_A_EXCLUSION_MARKER_RE` (:2652) through `_fence_excluded_by_marker` (:2690);
  - the comment beginning `# An opening fence:` (:3187) through `_all_skill_md_files` (:3291);
  - the banner beginning `# --- Cross-reference integrity:` (:3588) through `_resolve_citation_target` (:3704);
  - `_assert_citation_resolves_to_heading` (:3773–3804);
  - `_extract_scope_anchor_region` (:4813–4845);
  - `_normalized_anchor_text` (:5029–5036);
  - `_change_type_table_left_columns` (:5176) through `_scope_exempt_change_type_row_text` (:5206);
  - `_STEP4_HEADING` (:5285);
  - `_section_between` (:5298–5312);
  - `_heading_section_text` (:5949) through `_assert_pinned_clause_right_bounded` (:5999);
  - `_CODE_REVIEW_CONTRADICTION_ROUTE_ANCHOR` (:7077);
  - `_CODE_REVIEW_DEFER_INVARIANT_ANCHOR` (:7210);
  - `_CODE_REVIEW_FINDING_DISPOSITION_HEADING` through `_CODE_REVIEW_NEW_PRIMITIVE_ROUTE_ANCHOR` (:7237–7238);
  - `_CODE_REVIEW_DEFER_CRITERIA_HEADING` through `_CODE_REVIEW_HEAVIER_MECHANISM_RULE_OPENING` (:7318–7324);
  - `_CODE_REVIEW_RIPPLE_HEADING` (:7950);
  - `_CODE_REVIEW_STEP_0_1_HEADING` (:8001) (M9);
  - `_all_doc_paths` (:8897–8921);
  - `_PLAN_ARCHITECT_PATH` through `_PLAN_ARCHITECT_CONSULT_HEADING` (:9860–9861).
  - Its locators (M8):
    - L2, in `_blank_frontmatter`'s docstring: "which is imported in this module:" becomes "which sibling test modules import:".
    - L3, in the citation banner: "This section resolves" becomes "test_skill_citations_resolve_to_real_headings resolves", rewrapped within the banner.
    - L4, in `_assert_citation_resolves_to_heading`'s docstring: "citation tests below:" becomes "citation tests that import it:".
- Create `tests/test_code_review_disposition_rules.py` (code-review's clean definition, and its contradiction-route, defer-invariant, new-primitive and fix-plan-pilot regions). It imports `config_schema` (row 4). Its five ranges skip the six constants that row 3 sends to the helper, which it imports from there.
  - `_CODE_REVIEW_RECORD_COMPLETION_HEADING` (:7045) through `TestCodeReviewCleanDefinitionCountsSettledAndDefer` (:7074);
  - the comment beginning `# The whole contradiction-route region:` (:7079) through `TestCodeReviewContradictionRouteRegionPin` (:7207);
  - the comment beginning `# The whole defer-invariant region:` (:7212) through `_PINNED_DEFER_INVARIANT_CLAUSE` (:7234);
  - `_ENFORCEMENT_INVARIANT_CONSULT_STOP_SENTENCE` (:7239) through `_CODE_REVIEW_FIX_PLAN_PILOT_ANCHOR` (:7317);
  - the comment beginning `# The whole fix-plan pilot region` (:7326) through `TestCodeReviewFixPlanPilotRegionPin` (:7494).
- Create `tests/test_code_review_challenge_and_round_cap.py` (code-review's disposition-challenge and round-cap regions, its ripple carry-forward paragraph, and Step 0.1's ledger `show`). It imports the same six constants from the helper.
  - `_CODE_REVIEW_DISPOSITION_CHALLENGE_ANCHOR` (:7497) through `TestRoundCapSecondNoiseVerdictStop` (:7919);
  - the comment beginning `# The re-review carry-forward paragraph` (:7952) through `TestCodeReviewRippleCarryForwardPin` (:7998);
  - `TestCodeReviewFirstRoundLedgerShow` (:8004–8026).
- Create `tests/test_review_ledger_wiring.py` (the review-ledger literals, block delimiters and prose controls that skill and agent bodies carry).
  - `TestReviewLedgerAuthoringEffortLiteral` (:708) through `TestReviewLedgerReviewerLaneLiteral` (:751);
  - `_REVIEW_LEDGER_LIB` (:8029) through `TestReviewLedgerPublishAndInjectionControls` (:8294).
- Modify `tests/test_skills.py`.
  - Delete the ranges above and the duplicate `_CODE_REVIEW_STEP_0_1_HEADING` line (:9646) first (M5, M9). Then add one parenthesised `from _skill_md_helpers import (...)` statement that lists every helper name the file still reads, and prune its other imports.
  - L1, in the Trigger-A banner: "defined further below," becomes "defined in _skill_md_helpers.py,".
  - L5, in `_all_agent_md_paths`'s docstring: "_agent_body() above" becomes "_agent_body() in _skill_md_helpers.py".
- Modify `claude/.claude/scripts/select-tests.py`.
  - At :305, add `"claude-skills/skills/tests/test_code_review_disposition_rules.py"` to `SKILLS_TESTS_IMPORTING_CONFIG`, keeping `test_skills.py`.
  - Use the directory form (M7) at :204, :421, :428, :456, :466, :478, :737, :762, :766, :774, :835–836 and :898. For example, :774's "test_skills.py's test_scripts_are_executable (SKILLS_TESTS_DIR) globs" becomes "SKILLS_TESTS_DIR's test_scripts_are_executable globs", and :737's "SKILLS_TESTS_DIR's test_skills.py imports the module" becomes "SKILLS_TESTS_DIR's tests import the module".
  - :329 and :392 stay as they are (row 7).
- Modify `claude/.claude/scripts/tests/test_select_tests.py` to the directory form at :584, :669, :745, :754, :810, :1135, :1186, :1382, :1393, :1403, :1412, :1466, :1477, :1496 and :1554. :1455, :1543–1546 and :1776 stay as they are (row 7).
- Modify `claude/.claude/scripts/_skill_auxiliary_files.py:7`: "claude-skills/skills/tests/test_skills.py, which uses the names for:" becomes "the claude-skills/skills/tests/ test files, which use the names for:". Its "Two consumers" stays true.
- Modify `claude/.claude/tests/helpers.py:3785`: "test_skills.py's TestCodeReviewDispositionChallengeRegionPin" becomes "test_code_review_challenge_and_round_cap.py's TestCodeReviewDispositionChallengeRegionPin".
- Modify `docs/design-decisions/architect-authored-fix-plan-pilot.md:154` to name `claude-skills/skills/tests/test_code_review_disposition_rules.py` as the home of `_PINNED_FIX_PLAN_PILOT_CLAUSE`.
- Commit `.claude/plans/split-test-skills.md`, this plan, with phase 1.

**Phase 2.** Before its commit, the main session runs `/skill-review`, which must pass for `agent-review/SKILL.md` (row 8, M1).
- Create `tests/test_review_scope_rules.py` (the review skills' invalid skip rationales, the SCOPE_RULE and CACHE_RULE anchors, the Change-type table, and the reviewer-input rules in claude-hook-review's section 10 and code-review's Step 0.6).
  - `_INVALID_SKIP_HEADING` (:4730) through `test_scope_exempt_row_excludes_security_controls_row` (:5280), one range at this base;
  - `test_claude_hook_review_section10_handoff_rules_present` (:5520) through `test_step_0_6_narrowing_keys_lanes_by_change_not_file_type` (:5651).
  - L6, in `test_scope_rule_anchors_present`'s docstring: "test_disposition_rule_anchors_present above." becomes "test_disposition_rule_anchors_present in test_skills.py.".
- Create `tests/test_ready_for_review_skill.py` (ready-for-review's steps 1–4, 6 and 7, its fix loop, CI watch, completion marker and session-id redaction).
  - `_READY_FOR_REVIEW_STEP3_HEADING` (:5315) through `test_ready_for_review_step2_takes_the_fallback_git_diff_base_from_the_recipe_line` (:5517);
  - `test_ready_for_review_step_4_has_the_rerun_that_recovers_omitted_plan_review_rows` (:5935–5943);
  - `_READY_FOR_REVIEW_STEP1_HEADING` (:6679) through `TestReadyForReviewCiWatchLandsFixUnderFixLoopRule` (:7042);
  - `_READY_FOR_REVIEW_STEP6_HEADING` (:8297) through `TestReadyForReviewStep6BehindBaseRule` (:8380);
  - `_READY_FOR_REVIEW_STEP7_HEADING` (:8572) through `test_ready_for_review_step3_halts_on_empty_cumulative_diff` (:8692).
- Create `tests/test_review_dispatch_contracts.py` (reviewer dispatch: the prompt-contract sites, findings_path wiring, the declaration block and the routing reviewer table).
  - `test_plan_review_hands_comment_discipline_reviewer_a_plan_path` (:5653) through `test_plan_prose_findings_never_block_and_plan_diffs_stay_out_of_lane` (:5932);
  - the banner beginning `# --- findings_path wiring in plan-review/ROUTING.md` (:9172) through `test_findings_path_retired_recipe_expressions_absent_from_every_skill_body` (:9637);
  - `test_review_dispatchers_still_hand_reviewers_a_relative_agent_reviews_findings_path`, from its decorator (:10124) through its last line. No range ends at "the end of the file", because later PRs append there.
  - L7, in `test_findings_path_recipe_tokens_present_in_code_review_and_plan_review`'s docstring: "test_invalid_skip_rationale_labels_match_across_review_skills above" becomes "test_invalid_skip_rationale_labels_match_across_review_skills in test_review_scope_rules.py".
- Modify `tests/test_skills.py`. Delete the ranges first (M5), then prune imports.
- Modify `claude-skills/skills/agent-review/SKILL.md:181` to name `claude-skills/skills/tests/test_review_dispatch_contracts.py` as the file whose dispatch-site list names every site.
- Modify the locators at:
  - `claude/.claude/hooks/tests/test_agent_roster.py:81`, naming `test_ready_for_review_skill.py`;
  - `claude/.claude/scripts/tests/test_pr_create_preflight.py:817`, naming `test_ready_for_review_skill.py`;
  - `claude/.claude/scripts/tests/test_findings_path_suffix.py:38`, naming `test_review_dispatch_contracts.py`.

**Phase 3.**
- Create `tests/test_convention_skills.py` (feature-flags, terraform-conventions, and the agent and skill bodies that point at convention skills).
  - the comment beginning `# feature-flags is name-only` (:379) through `test_terraform_conventions_major_version_signal_pinned_verbatim` (:546);
  - `TestConventionSkillWiring` (:629–705).
- Create `tests/test_agent_body_pins.py` (the code-writer and skill-fidelity-reviewer agent bodies).
  - `TestCodeWriterSelfReviewScope` (:825–846);
  - `_TEST_TO_FIT_RULE` (:1057) through `TestSkillFidelityReviewerArchitectConsultCheck` (:1365).
- Create `tests/test_memory_skill_and_global_instructions.py` (the memory skills, and the marker-gate and memory-body rules of `claude/.claude/CLAUDE.md`).
  - `TestMemorySkillSectionOrdinalCrossReferences` (:754–822);
  - `TestMemoryStoreAuditGatePauseWiring` (:849–1054);
  - `TestGlobalInstructionsDescribeMarkerGatesAsContentAddressed` (:2082) through `TestMemorySkillPreservesActionPrescribingTrigger` (:2202).
- Create `tests/test_pr_lifecycle_skills.py` (pr-description, branch-management, respond-pr, tighten-prose and git-feature-branch-sync).
  - `TestPrDescriptionTwoModeDispatch` (:1368) through `TestRespondPrEditBoundary` (:1938);
  - `TestTightenProseScopeCarveOutPhrases` (:2019–2031);
  - the comment beginning `# respond-pr step 3's failed-recipe sentence:` (:8381) through `test_git_feature_branch_sync_sends_a_stacked_branch_to_the_recipes_base` (:8569).
  - L8, in `TestPrDescriptionTwoModeDispatch`'s docstring: "The wiring tests above" becomes "The wiring tests in test_convention_skills.py".
- Create `tests/test_plan_pipeline_skills.py` (plan-it's Steps 5 and 7, plan-review's tripwire provenance, and the plan-architect agent).
  - `_PLAN_REVIEW_REFERENCES_MD` (:5283) through `_EXPECTED_TRIPWIRE_COUNT` (:5295), one range at this base;
  - `_PLAN_IT_STEP7_HEADING` (:6101) through `TestPlanItStep7PushConsent` (:6194);
  - the comment `# Step 5 holds these literals.` (:6452) through `TestPlanItStep5ContextCheckAndGrammarLiterals` (:6676);
  - `_step4_tripwire_names` (:8695) through `TestStep4TripwireProvenanceParity` (:8801);
  - `_PLAN_ARCHITECT_DESIGN_PRINCIPLES_HEADING` (:9862) through `test_plan_architect_cites_engineering_judgment_heading_that_exists` (:9990).
- Create `tests/test_review_orchestrator_and_review_pr.py` (the review-orchestrator protocol and agent body, the routing gate's `unrouted` args, and review-pr).
  - `_SUBAGENT_DELEGATION_GATE_LOOP_HEADING` (:9640) through `test_review_orchestrator_agent_body_cites_skill_heading_that_exists` (:9857), one range at this base;
  - `test_review_orchestrator_resume_path_names_ledger_show_in_code_review_step_0_1` (:9992) through `test_review_pr_skill_mentions_findings_path_only_in_the_preamble` (:10121).
- Modify `tests/test_skills.py`. Delete the ranges first (M5), then prune imports.
- Modify the locators at:
  - `docs/design-decisions/plan-architect-writes-its-own-plan-sections.md:23`, naming `test_plan_pipeline_skills.py` (the consult never-edit sentence, read at :6619);
  - `docs/design-decisions/review-orchestrator-routing-gate.md:101`, naming `test_review_orchestrator_and_review_pr.py` (the `unrouted` and `_ROUTING_GATE_DESIGN_RECORD` pins, :9692–9812);
  - `docs/design-decisions/review-orchestrator-gate-release-decoupling.md:34` and :101, naming `test_review_orchestrator_and_review_pr.py`;
  - `claude/.claude/scripts/tests/test_plan_shape.py:1346`, naming `test_plan_pipeline_skills.py`;
  - `claude/.claude/scripts/tests/test_pr_cost_section.py:832`, naming `test_pr_lifecycle_skills.py` (`test_declares_cost_heading_literal`, :1836);
  - `claude/.claude/hooks/tests/test_design_decision_files.py:151–153`, where "claude-skills/skills/tests/test_skills.py and repo-root install.sh are named explicitly rather than swept in by a directory glob: each is the sole citing file in its tree" becomes "claude-skills/skills/tests/ and repo-root install.sh are named explicitly rather than swept in by a wider glob: each is the only citing location in its tree". The rest of the sentence stays (row 20).

**Phase 4.**
- Create `tests/test_handoff_and_resume_skills.py` (handoff, brief and resume-context, and the executable bit of every stowed script).
  - `TestContinuityFileBucketCrosscheck` (:1941) through `TestHandoffCollectStepPinsLoadBearingClauses` (:2333), one range at this base;
  - the comment beginning `# Durable-handoff-location:` (:3012) through `test_handoff_and_brief_reference_find_consumed_continuity_file` (:3173);
  - `_HANDOFF_WARRANT_CHECK_HEADING` (:5946) through `TestResumeCommandDirectoryClauses` (:6450), one range at this base.
- Create `tests/test_forked_skills.py` (`context: fork` skills and the validator's fork-background rule).
  - `TestValidateContextForkRequiresExplicitBackground` (:2549–2609);
  - the comment beginning `# Skills carrying context: fork.` (:3294) through `TestForkedSkillsCarryReturnAndGuardInstructions` (:3414).
- Create `tests/test_skill_eval_cases.py` (the trigger-case files' shape and the disposition-fidelity case validator). It takes `_LEGACY_TRIGGER_METHODS` (:2816) through `TestValidateDispositionFidelityCase` (:2952).
- Create `tests/test_skill_url_placement.py` (citation URLs stay out of SKILL.md bodies and runtime-read auxiliary files).
  - the banner beginning `# --- Citation placement:` (:3176) through `_URL_RE` (:3185);
  - `test_skill_bodies_carry_no_citation_urls` (:3417) through `test_prose_url_detector_flags_a_leaked_citation_and_spares_code` (:3585).
- Create `tests/test_skill_citation_grammar.py` (every `§` citation in the skill tree is extracted and resolves, and the citation pipeline's own unit tests).
  - `_citation_sources_for_skill_md` (:3707) through `test_skill_citations_resolve_to_real_headings` (:3770);
  - the comment beginning `# A citation-shaped construct` (:4352) through `test_citation_report_names_citing_file_line_and_target_for_unresolvable_citation` (:4727);
  - `_all_citation_extraction_doc_paths` (:8924) through `test_all_citation_extraction_doc_paths_raises_when_global_rules_dir_empty` (:8971) (row 11).
  - L9, in `test_skill_citations_resolve_to_real_headings`'s docstring: "test_skill_bodies_carry_no_citation_urls above." becomes "test_skill_bodies_carry_no_citation_urls in test_skill_url_placement.py.".
- Create `tests/test_doc_cross_references.py` (cross-references from docs and agent bodies to skill headings, the threat-model tier wording, and the redaction-policy list items). It takes `test_handoff_nudge_doc_cites_handoff_warrant_check_section` (:3807) through `test_case_study_pooled_figure_markers_are_registered_or_new` (:4350).
- Create `tests/test_per_account_path_contracts.py` (transcript-analysis's scope sentence, and the per-account state-path and stowed-path contracts over skill, agent and doc prose).
  - the comment beginning `# Pinned verbatim: transcript-analysis/SKILL.md's "Scope confirmation"` (:8804) through `_strip_yaml_frontmatter` (:8894);
  - `TestPerAccountStatePathContract` (:8974) through `TestStowedScriptPathContract` (:9169).
- Modify `tests/test_skills.py`. Delete the ranges first (M5), then prune imports.
- Modify the locators at:
  - `docs/skills.md:176`, naming `test_skill_url_placement.py`;
  - `.claude/rules/citation-grammar.md:23`, naming `claude-skills/skills/tests/test_skill_citation_grammar.py`, which holds both the resolution test and the extraction-completeness test;
  - `docs/design-decisions/review-orchestrator-gate-release-decoupling.md:59`, naming `test_forked_skills.py`;
  - `claude/.claude/hooks/tests/test_hook_alignment.py:1942`, naming `test_doc_cross_references.py` (`_hooks_md_tier_vocabulary`, :4017);
  - `claude/.claude/scripts/tests/test_transcript_review_rounds.py:4526`, where "test_skills.py's citation-resolution tests" becomes "test_skill_citation_grammar.py's and test_doc_cross_references.py's citation-resolution tests";
  - `claude/.claude/scripts/tests/test_config_py.py:572`, naming `test_handoff_and_resume_skills.py`.

**`test_skills.py` after phase 4** keeps these base ranges, and its module docstring is not edited:
- the module docstring and imports (:1–86);
- `_skill_description` (:120) through `BUILTIN_NAME_ONLY_SKILLS` (:280);
- `TestSpecialistSkillTriggerContracts` (:283–376);
- `TestNameOnlySkillContracts` (:548) through `TestNameDispatchedNoTriggerContracts` (:626);
- `TestModelInvokableSkillTriggerContracts` (:2336) through `TestCorpusBudgetFunction` (:2546);
- `_DISPOSITION_RULE_ANCHOR_RE` (:2612) through `_EXPECTED_DISPOSITION_RULE_ANCHORS` (:2625);
- the banner beginning `# --- Trigger A regression:` (:2636) through `_TRIGGER_A_ASSIGN_RE` (:2651);
- `_trigger_a_matches` (:2693) through `test_disposition_rule_anchors_present` (:2813);
- `test_skill_overrides_documented_in_docs_skills_md` (:2955) through `test_destructive_cleanup_forms_require_ask_not_allow` (:3009);
- `test_every_config_get_key_named_in_a_skill_is_in_the_config_schema` (:7922–7947).

This list names no file under `plugins/`, and it does not name `plan-review/SKILL.md` (row 7).

**Reuse:**
- `claude/.claude/scripts/tests/_move_fidelity.py`'s `nodes`, `remainder` and `test-ids`, unchanged (row 13). The scratch comment-block script imports its `_load_source` and `_comment_tokens` (M13).
- `helpers.py`'s path constants and markdown helpers (`heading_section_bounds`, `heading_texts`, `normalize_heading`, `change_type_table_rows` and the rest), imported unchanged by whichever file reads them.
- `.claude/plans/split-test-lib.md`'s M5, M10 and M12 and its Verification 0–4 and 10, as the procedure this plan adapts.

Projected diff: about 19,300 changed lines across the four PRs, about 9,700 added and 9,600 deleted: about 3,750 in phase 1, 4,500 in phase 2, 5,600 in phase 3 and 5,500 in phase 4.

## Verification

Run these steps from the phase worktree's root, for each phase.
- The `code-writer` dispatch runs steps 0–10, except step 0's open-PR query. The main session runs that query before the dispatch, and step 11 at handoff (M1).
- Every pytest pass, select-tests runs included, runs as a background Bash call that logs to the scratch directory. The step waits for it to finish before reading its result.
- No command uses `$(...)`. Below, `<scratch>` and `<base>` stand for values that an earlier call printed, typed out literally (row 28).

0. **Baseline, before any edit.**
   - The main session runs `gh pr list --state open --limit 200 --json number,headRefOid,files --jq '.[] | select(any(.files[]; .path == "claude-skills/skills/tests/test_skills.py")) | "\(.number) \(.headRefOid)"'`. It shows the engineer each PR listed, with M11's merge-hold ask (G3, row 30). Row 37's open fact is whether `files` truncates a large PR's list, so the main session also runs the same query without the `select` and flags every PR whose list holds 100 or more files, for the engineer to check by hand.
   - In phase 1 only, dry-run M5's delete over the base copy with every phase's ranges, as `sed` on a scratch file. Stop for a plan revision when the predicted residual exceeds 990 lines (row 2).
   - Run `mktemp -d /tmp/split-test-skills-p<phase>.XXXXXX` once, with the phase number typed in, and `git rev-parse HEAD`. A rebuild (M11) takes a new directory.
   - Run `git show <base>:claude-skills/skills/tests/test_skills.py > <scratch>/before_test_skills.py`, then `wc -l <scratch>/before_test_skills.py`.
   - List the base copy's top-level names by AST and compare them with this plan's ranges (M10). A missing planned name stops the phase. A new name is placed per M10, and the PR body records where.
   - Generate the phase's `--moved` and `--removed` lists from the manifest by the same AST listing: every top-level name whose first line falls in a range the phase moves. Type no name by hand.
   - For each file the phase creates, `find claude/.claude claude-skills plugins evals -name '<basename>'` prints nothing (row 18). A hit stops the phase (M10).
   - Run `grep -rn 'mark.timing' claude-skills/skills/tests/`. It must print nothing (row 12). If it prints a hit, every pytest run below gains a separate `-m timing -n0` pass, as `.claude/plans/split-test-lib.md` Verification 0 does.
   - Run `scripts/venv-run.sh pytest claude-skills/skills/tests/ --junitxml=<scratch>/before.xml`. Then `grep -cE '<(failure|error)[ />]' <scratch>/before.xml` prints 0, so a kept outcome is a pass. A failing baseline stops the phase for the engineer.
   - The PR body records:
     - `<base>`, M11's rebuild rule, and step 11's commands;
     - M12's rollback statement;
     - the manifest: each target file, each range's anchors with the base lines they resolved to, and each moved name's target file, which is also M11's old-name-to-new-module map;
     - each declared locator edit, with its old and new text (M8);
     - each import triple the phase adds to or prunes from `test_skills.py` (step 5);
     - M5's post-delete line count;
     - each command's printed output and exit status in steps 3–6, with each absolute home path replaced by `<worktree>`, because test IDs embed it (row 15).
1. **Build.** Create each file per M5 and M6, then make the phase's other Critical-files edits.
2. **Scoped suite.**
   - Stage the new files with `git add -- <paths>`.
   - Run `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py -m "not timing"`, then the same command with `-m timing -n0` (row 29). Every selected test passes.
   - In phase 1, select-tests selects the full suite on its own (row 6).
   - In phases 2–4, the printed targets must include `claude/.claude/hooks/tests` (row 21). If they do not, also run `scripts/venv-run.sh pytest claude/.claude/hooks/tests/test_design_decision_files.py`. No phase widens the run otherwise.
3. **Test-ID parity.**
   - Rerun step 0's pass to `<scratch>/after.xml`, from the same worktree path (row 15).
   - Both XML files exist, and `grep -c '<testcase ' <file>` prints a number above 0 for each.
   - `scripts/venv-run.sh python3 claude/.claude/scripts/tests/_move_fidelity.py test-ids --before <scratch>/before.xml --after <scratch>/after.xml --file-prefix claude-skills/skills/tests/` exits 0, and its before and after counts are equal (row 13).
4. **Node fidelity.**
   - Run `scripts/venv-run.sh python3 claude/.claude/scripts/tests/_move_fidelity.py nodes --before <scratch>/before_test_skills.py --after <each file the phase creates> --moved <every top-level name in the phase's ranges>`. `compared-nodes` equals the number of moved names.
   - The only findings are the phase's declared in-node locator edits: L2 and L4 in phase 1, L6 and L7 in phase 2, L8 in phase 3, and L9 in phase 4. For each one, `git diff --no-index` of its base span against its new span shows only the declared lines.
   - Each created file's top-level name set, from Verification 0's AST listing, equals the manifest's names for that file. `nodes` checks only the `--moved` names, so this catches a name added to a new file or the helper.
   - Phase 1 leaves `_CODE_REVIEW_STEP_0_1_HEADING` out of `--moved` (M9). `grep -cFx '_CODE_REVIEW_STEP_0_1_HEADING = "## Step 0.1 — Short-circuit already-reviewed diff"'` prints 2 for the base copy, 1 for the helper and 0 for `test_skills.py`.
5. **Remainder.**
   - Run `_move_fidelity.py remainder --before <scratch>/before_test_skills.py --after claude-skills/skills/tests/test_skills.py --removed <the moved names> --import-added <each triple the phase adds> --import-removed <each triple it prunes>`. Phase 1 also lists `_CODE_REVIEW_STEP_0_1_HEADING` under `--removed` and adds `--changed _all_agent_md_paths` (L5). Step 7's F401 and F821 prove the declared triples right.
   - It prints no `import-diff` and no `remainder-ast` finding.
   - Each `remainder-comments` finding is one of three kinds, and step 6 confirms each:
     - a block that left with its section;
     - the Trigger-A banner carrying L1, in phase 1;
     - the :53–54 import-region block, pruned with its import in phase 4 (M13).
   - `remainder` exits 1 whenever it prints a finding, so this step reads the findings, not the exit status.
6. **Comment blocks** (M13).
   - Run the scratch check over the base copy, `test_skills.py` and each file the phase creates, with the manifest's name-to-file map and the declared top-level comment edits (L1 and L3, both in phase 1). It passes on the conditions `.claude/plans/split-test-lib.md` Verification 4 states, its count guard included, with M13's import-region rule.
   - Then rerun it once on each of two seeded copies. Each run swaps one seeded copy, which keeps its file's name, in for that file. It must exit non-zero and name that file.
     - Dropped line: pick one line of the phase's banner below that `grep -cxF '<line>' <file>` counts exactly once in its file, then run `grep -vxF '<line>' <file> > <scratch>/<file name>`.
     - Stranded banner: run `sed -n '<a>,<b>p' <scratch>/before_test_skills.py > <scratch>/banner.txt` over the same banner's base lines, then `cat claude-skills/skills/tests/test_skills.py <scratch>/banner.txt > <scratch>/test_skills.py`.
   - The banner for each phase:
     - Phase 1: the citation banner in `_skill_md_helpers.py`. Its dropped line is one that L3 does not touch, and its bare `#` lines occur more than once, so they never qualify.
     - Phase 2: the findings_path banner in `test_review_dispatch_contracts.py`.
     - Phase 3: the six-line comment above `_review_orchestrator_protocol_section` (:9649–9654) in `test_review_orchestrator_and_review_pr.py`.
     - Phase 4: the URL-placement banner in `test_skill_url_placement.py`.
7. **Lint.** `scripts/venv-run.sh ruff check claude/.claude/ claude-skills/ plugins/ evals/` reports nothing. F401 and F821 prove that each file imports exactly what it reads.
8. **Size.** `wc -l` shows at most 1,000 lines for each file the phase creates and for the helper. After phase 4 it also shows at most 1,000 for `test_skills.py`, which row 2 puts at 970 to 990.
9. **Citations and scope.**
   - `git grep -n 'test_skills\.py' -- . ':!docs/reports' ':!.claude/plans' ':!evals/fixtures'` lists only the keeper sites (row 7), the two records in `docs/cost-levers-considered.md` (row 22), and the sites a later phase edits. After phase 4 that last group is empty.
   - A scratch pass lists every comment line and docstring line that holds one of row 19's position words, in each file under `claude-skills/skills/tests/` that the phase creates or edits. It reads comments with `tokenize` and docstrings with `ast.get_docstring`, and it skips other string literals, which hold pinned SKILL.md text. Each hit whose referent sits in another file is a declared edit.
   - `grep -lE '^(from|import) _config( |$)' claude-skills/skills/tests/*.py` lists exactly this tree's members of `SKILLS_TESTS_IMPORTING_CONFIG`. The same grep for `run_skill_evals` lists exactly this tree's members of `SKILLS_TESTS_IMPORTING_SKILL_EVALS_RUNNER` (rows 4, 5).
   - `grep -cE '^(from|import) (_config|run_skill_evals|validate_skill_structure)( |$)' claude-skills/skills/tests/_skill_md_helpers.py` prints 0 (M4).
   - `git diff --name-only <base>` lists only the phase's Critical files.
10. **Untouched surfaces.** `git diff --quiet <base> -- plugins/ claude-skills/skills/plan-review/SKILL.md` exits 0 in every phase. In phases 1, 3 and 4, `git diff --quiet <base> -- claude-skills/skills/agent-review/SKILL.md` exits 0 too.
11. **Base check, at each handoff for merge**, run by the main session (M11).
    - Re-run step 0's open-PR query. The PR body lists each PR it prints, next to the old-name-to-new-module map, and the engineer decides merge order against them.
    - Run `git fetch origin main`, then `git merge-tree --write-tree origin/main HEAD`, and act on its exit status as `.claude/plans/split-test-lib.md` Verification 10 states.
    - On a clean result:
      - Sync the branch with the default tip, per the `git-feature-branch-sync` skill.
      - Rerun step 0's name comparison against the synced head's `test_skills.py`.
      - A new name whose M10 home is a file this phase or an earlier one created rebuilds the phase from step 0.
      - CI's two passes must pass on the synced head before the engineer merges.
    - After phase 4, every top-level name left in `test_skills.py` comes from the residual ranges above or is a post-base name that M10 placed there.

## Out of scope

- **#1118's other bullets, and the other test files over the limit.** Each is its own split.
- **The 1,000-line enforcing check**, child F2 of `docs/design-decisions/code-file-line-limit.md`. It is separate work (row 23).
- **A `comments` subcommand in `_move_fidelity.py`.** This is the second split to need the scratch comment-block check, so promoting it is a fair follow-up. It is a repo change with its own seeded-defect tests (M13).
- **A select-tests rule that selects `test_design_decision_files.py` for a change under `claude-skills/skills/tests/`.** That test globs this tree (row 20), but no rule selects it for a change here, a gap that predates this split. Each phase covers it through Verification 2 instead (row 21). The fix is a rule-table change with its own `test_select_tests.py` cases.
- **A scanner view of `_skill_md_helpers.py`'s path constants.** M4's accepted loss leaves any path constant the helper later gains outside `TestCrossDomainReadCompleteness` (row 10). The follow-up extends `_test_corpus` to `claude-skills/skills/tests/_*.py`, or adds an explicit route test per helper path constant. Either is a `test_select_tests.py` change with its own cases.
- **A committed test that compares the skills-tests importer sets with the tree's importers.** Verification 9 checks them by grep in each phase. A committed test is a `test_select_tests.py` change with its own cases.
- **Citations that were stale before this split.** These are `select-tests.py:392–394`'s line numbers, the residual docstring's "Run with: pytest claude/.claude/", and `docs/cost-levers-considered.md:379`'s old path, which is also a record.
- **Dated records, other plan files, and the commit-pinned recall fixture** (row 22). They are preserved records.
- **Any file under `plugins/`, and `plan-review/SKILL.md`.** Their citations stay true (rows 7, 8).
- **Renaming, rewording, merging or fixing any moved test**, beyond the declared locator edits and M9's duplicate. #1118's invariant forbids editing any assertion (row 31).
- **Moving tests into existing siblings.** See Approach.
- **Contributor guidance on where new pins go.** Each new file's docstring names its skill or concern, and this pass's grep found no live doc that names `test_skills.py` as the place to add a pin.
- **A CHANGELOG entry.** The split changes no hook, skill or script behavior, the same reason `.claude/plans/split-test-lib.md` gives.
