# Replace home-rooted placeholder paths in test fixtures

## Context

Goal: stop a conflicted sync-merge of `origin/main` into a feature branch from being denied at `git commit` by the redaction gate's home-rooted-path detector because of synthetic test fixtures.

This change makes the two edited files stop matching the detector. The files listed under Out of scope can still match until the follow-up lands.

`deny-private-project-refs.sh` scans the whole staged delta of a merge commit, so it denies the commit on fixture content that is already public upstream. The gate seeing the whole delta is deliberate (`.claude/plans/merge-aware-review-gates.md`); the defect is the fixture.

## Approach

After this change, two synthetic fixtures that matched the redaction gate's home-rooted-path detector outside the gate's test-tree exclusion carry no home-rooted prefix. Other files can still match (Out of scope). Nothing else changes: not the gate, not the detector, and no other fixture.

**Transcript test.** `TestSkillInvocationRepoScope.test_scope_matches_by_literal_name_not_glob` in `claude/.claude/scripts/tests/test_transcript_analysis.py`:

- Its three cwd/worktree/toplevel stubs use `/r*/main`.
- Its two project dirs use `-r*-main` (`mine`) and `-rX-main` (`theirs`).
- The comment that quotes the path-to-slug mapping reads `"/r*/main" -> "-r*-main"`.
- Its docstring says the `*`/`?`/`[` metacharacters come from "any path component", because the fixture has no home prefix.

The rest of the file stays as it is. The test checks exact-name matching against glob matching, as it always has.

**helpers.py.** The default `plan_file_path` of `exitplanmode_input` in `claude/.claude/tests/helpers.py` is `/nonexistent/.claude/plans/test-plan.md`. The signature, docstring, and every call site stay as they are. The default must satisfy three constraints:
- It matches no detector (row 12).
- It is a non-empty absolute path (row 11).
- It names a file that does not exist (row 11).

The literal `/nonexistent/` states the last constraint itself, so no comment is needed.

**Root:** The Root bullets and rows 4 and 9 describe the tree before this change. The baseline is the merge-base with `origin/main` at plan time, equivalently the parent of the commit that lands the fix. Verification steps 1 and 2 own the post-change state.

At that baseline, two synthetic fixtures outside the gate's `claude/.claude/hooks/tests/**` exclusion hold literals that match the redaction gate's home-rooted-path detector:
- Four path strings in `test_scope_matches_by_literal_name_not_glob`. A conflicted sync-merge whose staged delta carries them is denied at `git commit`, even though the content is already public.
- The default value of `plan_file_path` in `exitplanmode_input`. A future commit whose added lines include it is denied.

**Givens**

- **G-1** — The redaction gate scans a merge's full staged delta, not only the branch's own new content. `.claude/plans/merge-aware-review-gates.md` records this choice: "narrowing a scanner's input is a security regression". Reversing it would be a gate-design decision outside this plan.
- **G-2** — The home-rooted detector is `_LIB_HOME_ROOTED_PATH_REGEX` in `claude/.claude/hooks/_lib.sh` (`_lib.sh:2928`). It is always on and has no allowlist. It is the gate's contract, shared by every consumer of `_lib.sh`. Loosening it would be a threat-model decision for the gate, outside this plan.

**Rows**

1. `_path_to_project_slug` is `re.sub(r"[/.]", "-", path)`, a pure character substitution. So `/r*/main` maps to `-r*-main`, and the test depends on no home prefix. `[verified: claude/.claude/scripts/transcript_analysis/scope.py:74-82]`
2. `_repo_scoped_project_slugs` runs `Path.resolve()` on the worktree, cwd, and toplevel strings and compares them only with each other (`scope.py:140-188`). It builds slugs from the unresolved worktree strings (`:190`). Any nonexistent absolute path therefore works, as long as all three stubs use the same string. The sibling tests' `/repo/...` stubs already rely on this. `[verified: scope.py:130-190 read; test file getcwd sites grepped]`
3. The gate scans only the `+` lines of `git diff --cached` and excludes only `claude/.claude/hooks/tests/**`. Both files in this plan are outside that exclusion. Two consequences:
   - This PR's own commit passes, because the replaced lines appear only as `-` lines.
   - A later sync-merge that brings in either file brings only the current lines.

   `[verified: claude/.claude/hooks/deny-private-project-refs.sh:512-520]`
4. Four path strings in `test_scope_matches_by_literal_name_not_glob` match the detector: the three stubs and the comment quoting the mapping. The two slug names do not match the detector. They change together with the paths, because the test drives the real slug function (row 1). `[verified at baseline: detector grep of the test file (4 hits); test function read]`
5. The detector requires a literal `/home/` or `/Users/`. The file's other `-home-u-*` slug literals and its roughly 150 `-home-user-*` ones contain no such substring, so the detector never matches them. `[verified: _lib.sh:2928; grep of the file for home-u]`
6. The docstring of `test_scope_matches_by_literal_name_not_glob` says the metacharacters occur "in any path component", which describes the fixture's shape. The production docstring at `scope.py:250` keeps the "home or username path" wording, because it describes where a metacharacter comes from in real use. `[verified: scope.py:250; test function docstring read]`
7. `/r*/main`, `-r*-main`, and `-rX-main` match none of the six structural detectors and not the tracker-ID scan. `[verified: _lib.sh:2915-2931 and docs/private-project-redaction.md:60-67, by inspection; Verification step 1 re-checks the home-rooted detector mechanically]`
8. A glob built from the `mine` slug still matches the `theirs` dir, so the test still tells exact-name matching apart from glob matching. `[unverified — fnmatch '*' semantics; discharged by Verification step 3]`
9. The default value of the `plan_file_path` parameter of `exitplanmode_input` is `claude/.claude/tests/helpers.py`'s only detector match. `[verified at baseline: detector grep of helpers.py (1 hit); exitplanmode_input read]`
10. No test relies on the default or asserts its value:
    - `exitplanmode_input` is imported only by `claude/.claude/hooks/tests/test_require_plan_review.py`.
    - All 30 of that file's call sites pass `plan_file_path=` explicitly: 22 pass `""`, 6 pass a `tmp_path` file, 1 passes a missing `tmp_path` file, and 1 passes a `/tmp` literal.
    - The string `plans/test-plan.md` appears nowhere else in the repo.

    `[verified: repo-wide grep of exitplanmode_input and plans/test-plan.md]`
11. Each of these facts about `claude/.claude/hooks/require-plan-review.sh` holds on `ExitPlanMode`:
    - A non-empty `planFilePath` decides the call outright.
    - An empty `planFilePath` falls through to the repo-relative check (comment at `:100-108`, code at `:109-111`).
    - The hook hashes the path with `sha256sum -- "$PLAN_MODE_FILE_PATH"`.
    - A relative path therefore resolves against the hook process's working directory (`:113`).
    - A missing or unreadable file fails closed with a deny (`:114-122`).
    - That deny applies only when the payload `cwd` is inside a git repo (`:95-98` allows otherwise).
    - A default that is a non-empty absolute path to a missing file therefore gives any default-using caller the same "cannot read" deny on every machine, inside a git repo.

    `[verified: require-plan-review.sh:95-137 read]`
12. `/nonexistent/.claude/plans/test-plan.md` matches none of the six structural detectors and not the tracker-ID scan. It contains no home prefix, no SSH-directory segment, no key filename, no hex run, none of the internal-hostname detector's six TLD words, and no `#`. The `/nonexistent/` root already appears in three files under `claude/.claude/hooks/tests/`. `[verified: _lib.sh:2915-2931 and docs/private-project-redaction.md:60-67, by inspection; count grep of claude/.claude for the /nonexistent/ root]`
13. `/nonexistent` does not exist on the machines that run this suite. `[unverified — discharged locally by Verification step 4. A machine where it exists changes no test outcome today, because no call site reaches the default (row 10)]`
14. The helper's docstring phrase "matching the real harness shape" refers to the payload's field names (`plan` and `planFilePath` in camelCase), not to the path value. It stays accurate. `[verified: helpers.py exitplanmode_input docstring read]`
15. `claude/.claude/tests/helpers.py` is in `GLOBAL_TRIGGER_PATHS` (`select-tests.py:263-267`), which is checked before domain matching (`:534-536`).
    - `compute_changed_paths` includes dirty and untracked working-tree paths (`:590-618`).
    - So `select-tests.py` selects the full suite on its own, whether it runs before or after the commit.
    - This is CLAUDE.md's first legitimate full-suite case, and nobody widens the run by hand.

    `[verified: claude/.claude/scripts/select-tests.py:250-267, 522-556, 590-618 read]`
16. The gate also scans the `git commit` command string (`:520`) and `gh pr create`/`gh pr edit` bodies (`:4`). A commit message or PR body that quotes the replaced literals would be denied like the fixture lines. `[verified: deny-private-project-refs.sh:4, 520]`
17. The scope covers the transcript test and `claude/.claude/tests/helpers.py`. `[engineer-verified: "Fixture + helpers.py (Recommended)"]`
18. Deferring the three eval fixtures, `deny-reviewer-tree-mutation.sh`, and the seven plan files to a follow-up is a recommendation, not a decision the engineer stated. `[unverified — the engineer selected an option whose description carried the deferral; the deferral is not in their own words]`

**Constraints kept, one line each**

- **Transcript stubs:** the path is nonexistent and absolute, has no home prefix, and keeps the `*` metacharacter (rows 1, 2, 7).
- **Detector and gate:** neither is loosened or narrowed, and no runtime path construction dodges the detector (G-1, G-2).
- **helpers.py default:** it is a non-empty absolute path to a nonexistent file with no home prefix (rows 11, 12).
- **helpers.py signature:** the parameter stays optional, so the change stays a redaction fix (row 10).
- **Coverage:** no new test asserts that fixtures avoid the detector, because the commit gate already scans every added line (row 3).

**Mechanisms**

- **M-1 Rewrite the transcript test in place:** four path strings become `/r*/main`, two slug strings become `-r*-main` and `-rX-main`, and the comment's quoted mapping follows. All changes stay inside the one test. `anchors: root, row1, row2, row4, row7, row8`
- **M-2 Leave the file's other `-home-u-*` and `-home-user-*` slug literals alone.** They cause no gate denial. `anchors: row5`
- **M-3 Reword the transcript test's docstring** so the metacharacters occur "in any path component". `anchors: row6`
- **M-4 Replace the `/home/<u>` prefix in the default of `plan_file_path` with `/nonexistent`**, keeping the `.claude/plans/test-plan.md` tail. `anchors: root, row9, row10, row11, row12, row13`
- **M-5 Keep the signature, docstring, and all 30 call sites of `exitplanmode_input` unchanged.** `anchors: row10, row14`
- **M-6 Describe the change in the commit message and PR body without quoting the replaced literals**, for example "drop the home-rooted prefix from two test fixtures". `anchors: row16`

## Critical files

- **Modify** `claude/.claude/scripts/tests/test_transcript_analysis.py`, inside `TestSkillInvocationRepoScope.test_scope_matches_by_literal_name_not_glob` only: its docstring, the two project-dir names, the three stubs, and the mapping comment.
- **Modify** `claude/.claude/tests/helpers.py`, the default value of `plan_file_path` in `exitplanmode_input` only.
- **Reuse:** the class's existing `_worktree_porcelain` helper stays unchanged.
- **Dispatch: one `code-writer` dispatch covering both files, in a single phase.** The two file sets are disjoint. A split still fails the test for splitting, for three reasons:
  - Both edits rest on the same background (G-2's detector contract, row 3's `+`-line scan, row 16's commit-message scan), which each prompt would have to restate.
  - The helpers.py edit alone makes `select-tests.py` run the full suite (row 15). A second dispatch would pay for a second full-suite run.
  - The whole change is a handful of lines.

  The dispatch prompt must say that the commit message and PR body must not quote the replaced path or slug literals (M-6). Its verification command is Verification steps 1-8.

## Verification

A linked worktree has no `.venv` of its own (README.md, Tests section). Run steps 3-7 with `../../../.venv/bin/<tool>` in place of `.venv/bin/<tool>`.

1. `git grep -nE '/(Users|home)/[A-Za-z0-9_.-]+' -- claude/.claude/scripts/tests/test_transcript_analysis.py claude/.claude/tests/helpers.py` prints nothing and exits 1.
2. `git grep -cE '/(Users|home)/[A-Za-z0-9_.-]+' -- ':(exclude)claude/.claude/hooks/tests/**'` no longer lists either edited file, and lists exactly the files enumerated under Out of scope.
3. `.venv/bin/python3 -c "import fnmatch; print(fnmatch.fnmatchcase('-rX-main', '-r*-main'))"` prints `True` (discharges row 8).
4. `.venv/bin/python3 -c "import os; print(os.path.exists('/nonexistent/.claude/plans/test-plan.md'))"` prints `False` (discharges row 13 on this machine).
5. `.venv/bin/pytest "claude/.claude/scripts/tests/test_transcript_analysis.py::TestSkillInvocationRepoScope::test_scope_matches_by_literal_name_not_glob"` passes. This gives fast feedback on the one behavior-bearing edit before the full suite runs.
6. `.venv/bin/python3 claude/.claude/scripts/select-tests.py`. Its stderr should report a full-suite run with reason `global-trigger` naming `claude/.claude/tests/helpers.py` (row 15). That run is the documented command for this diff, so nobody widens it by hand. CI runs the full suite again on push.
7. `.venv/bin/ruff check claude/.claude/scripts/tests/test_transcript_analysis.py claude/.claude/tests/helpers.py`
8. The PR's own `git commit` passes `deny-private-project-refs.sh`. This is not proof the fix worked, because a deletions-only commit passes regardless. It checks the new lines, and the commit message, against the user-populated project blocklist, which this plan cannot inspect.

This plan includes no `git blame` step for the `exitplanmode_input` default. The gate scans only added lines (row 3), so the age of the line has no bearing on whether the fix passes.

## Out of scope

- The gate, `_lib.sh`, and the gate's exclusion list (G-1, G-2).
- Home-rooted-detector matches in other tracked files outside `claude/.claude/hooks/tests/`, deferred to a follow-up (row 18):
  - `evals/fixtures/misfire-plan-review-instead-of-code-review.jsonl`, `evals/fixtures/no-trigger-typo.jsonl`, and `evals/fixtures/skill-fired-code-review.jsonl`, with 1 matching line each.
  - `claude/.claude/hooks/deny-reviewer-tree-mutation.sh`, with 1 matching line.
  - Seven files under `.claude/plans/`, with 10 matching lines in total. These plans are provenance for past changes, so the follow-up must first decide whether they are editable at all.
  - Reproduce the list with `git grep -cE '/(Users|home)/[A-Za-z0-9_.-]+' -- ':(exclude)claude/.claude/hooks/tests/**'`.
- The transcript test file's `-home-u-*` and `-home-user-*` slug literals (row 5).
- Making the `plan_file_path` parameter of `exitplanmode_input` required, which is a signature change beyond this fix.
- Refactoring the tests.
