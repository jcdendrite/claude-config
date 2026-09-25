# Replace home-rooted placeholder paths in test fixtures

## Context

Goal: stop a conflicted sync-merge of `origin/main` into a feature branch from being denied at `git commit` by the redaction gate's home-rooted-path detector because of upstream's own synthetic test fixtures.

`claude/.claude/scripts/tests/test_transcript_analysis.py` on `origin/main` holds four placeholder paths with a home-rooted prefix, plus two slug names that mirror them. `deny-private-project-refs.sh` scans the whole staged delta of a merge commit, so it denies the commit on content already public upstream. The gate seeing the whole delta is deliberate (`.claude/plans/merge-aware-review-gates.md`); the defect is the fixture. `claude/.claude/tests/helpers.py` has one sibling match. Now: a sibling worktree's PR is blocked on this.

## Approach

Delete the home-rooted prefix from the two synthetic fixtures that match the redaction gate's home-rooted-path detector outside the gate's test-tree exclusion. In the transcript test, the stubs become `/r*/main`. In `claude/.claude/tests/helpers.py`, the default `plan_file_path` of `exitplanmode_input` becomes `/nonexistent/.claude/plans/test-plan.md`. Nothing else changes: not the gate, not the detector, and no other fixture.

**Transcript test.** `TestSkillInvocationRepoScope.test_scope_matches_by_literal_name_not_glob` changes as follows:

- Its three cwd/worktree/toplevel stubs use `/r*/main`.
- Its two project dirs use `-r*-main` (`mine`) and `-rX-main` (`theirs`).
- The comment that quotes the path-to-slug mapping becomes `"/r*/main" -> "-r*-main"`.

The rest of the file stays as it is. The edit only deletes the `/home/<u>` path prefix and the `-home-u` slug prefix, and the test still checks exactly what it checked before.

Alternatives considered for the transcript test and set aside:
- **`/repo/r*/main` (the brief's fallback).** It matches the file's `/repo` root convention and passes every detector, so it would also work. `/r*/main` is preferred because the diff then only deletes a prefix, which a reviewer can check at a glance. An added segment gives the test nothing.
- **`/home/<u>/r*/main` (the docs' placeholder notation).** This keeps a home-rooted shape. It does so by putting `<` and `>` into a real directory name only to fall outside the detector's charset. The test needs no home prefix (row 1), so there is nothing worth keeping.
- **Building the path at runtime so the literal never appears.** This dodges the detector instead of satisfying it. CLAUDE.md's threat-model paragraph says a tier "never licenses the agent this repo guards to use a shape the gate happens to miss."
- **Excluding `claude/.claude/scripts/tests/**` from the gate, as `claude/.claude/hooks/tests/**` already is.** That widens a scanner blind spot to fix one fixture, which contradicts G-1.
- **A new test asserting that no fixture matches the detector.** It would duplicate the commit gate, which already scans every added line (row 3).

**helpers.py.** Line 564's default changes from `/home/<u>/.claude/plans/test-plan.md` to `/nonexistent/.claude/plans/test-plan.md`. Only the prefix segment changes; the `.claude/plans/test-plan.md` tail stays. The signature, docstring, and every call site stay as they are. The replacement has to satisfy three constraints:
- It matches no detector.
- It is non-empty and absolute.
- It names a file that does not exist (row 11).

The literal `/nonexistent/` states that last constraint itself, so no comment is needed.

Alternatives considered for helpers.py and set aside:
- **`/home/<username>/.claude/plans/test-plan.md` (the docs' placeholder notation).** It passes the detector only because `<` falls outside the detector's charset. In `docs/private-project-redaction.md` that notation is a placeholder a reader substitutes. Here it would be a path string the hook actually hashes. The reason for rejecting it matches the transcript test's `/home/<u>` alternative, and one rule for both files is simpler to review.
- **Pure prefix deletion (`/.claude/plans/test-plan.md`), which would mirror the transcript fix.** A root-level `.claude` directory is unlikely but not ruled out, for example in a process running with HOME set to `/`. The default's contract is to name a file that does not exist (row 11).
- **`/tmp/test-plan.md` (the shape used at `test_require_plan_review.py:2376`).** `/tmp` exists and is shared, so nonexistence would depend on machine state.
- **A relative path.** It would resolve against the hook process's working directory, which is the fixture repo (row 11). Nonexistence would then depend on that fixture's contents.
- **`""` as the default.** An empty `planFilePath` switches the call to the repo-relative branch (row 11). That changes behavior for any future caller that relies on the default.
- **Dropping the default so every caller must pass one.** This is safe today, since all 30 call sites already pass it (row 10). It still changes a shared helper's signature, an API decision beyond the redaction fix the engineer scoped.

**Root:** Two synthetic fixtures outside the gate's `claude/.claude/hooks/tests/**` exclusion hold literals that match the redaction gate's home-rooted-path detector:
- Four lines in `claude/.claude/scripts/tests/test_transcript_analysis.py`. They are new upstream relative to older branch points, so a conflicted sync-merge whose staged delta carries them is denied at `git commit`, even though the content is already public.
- Line 564 of `claude/.claude/tests/helpers.py`. It is latent: it denies any future commit whose added lines include it, such as a change to that signature line.

**Givens**

- **G-1** — The redaction gate scans a merge's full staged delta, not only the branch's own new content. `.claude/plans/merge-aware-review-gates.md:155` records this choice: "narrowing a scanner's input is a security regression". Reversing it would be a gate-design decision outside this plan.
- **G-2** — The home-rooted detector is `_LIB_HOME_ROOTED_PATH_REGEX` at `claude/.claude/hooks/_lib.sh:2928`. It is always on and has no allowlist. It is the gate's contract, shared by every consumer of `_lib.sh`. Loosening it would be a threat-model decision for the gate, outside this plan.

**Rows**

1. `_path_to_project_slug` is `re.sub(r"[/.]", "-", path)`, a pure character substitution. So `/r*/main` maps to `-r*-main`, and the test depends on no home prefix. `[verified: claude/.claude/scripts/transcript_analysis/scope.py:74-82]`
2. `_repo_scoped_project_slugs` runs `Path.resolve()` on the worktree, cwd, and toplevel strings and compares them only with each other (`scope.py:140-188`). It builds slugs from the unresolved worktree strings (`:190`). Any nonexistent absolute path therefore works, as long as all three stubs use the same string. The sibling tests' `/repo/...` stubs already rely on this. `[verified: scope.py:130-190 read; test file getcwd sites grepped]`
3. The gate scans only the `+` lines of `git diff --cached` and excludes only `claude/.claude/hooks/tests/**`. Both files in this plan are outside that exclusion. Two consequences:
   - This PR's own commit passes, because the old lines appear only as `-` lines.
   - After this PR lands, a sync-merge that brings in either file brings only the new lines.

   `[verified: claude/.claude/hooks/deny-private-project-refs.sh:512-520]`
4. Exactly four lines in the transcript test file match the detector: 16926, 16930, 16934, and 16936. Slug lines 16915-16916 do not match. They still change together with the paths, because the test drives the real slug function (row 1). `[verified: detector grep of the file this session (4 hits); lines 16905-16944 read this session]`
5. The detector requires a literal `/home/` or `/Users/`. The file's `-home-u-*` slug literals elsewhere (for example 715-839 and 17186-17192) and its roughly 150 `-home-user-*` ones contain no such substring, so the detector never matches them. `[verified: _lib.sh:2928; grep of the file for home-u]`
6. The docstring phrase "in the home or username path" describes where a glob metacharacter comes from in real use. It mirrors the production docstring at `scope.py:250` and does not describe the fixture's shape, so it stays accurate. `[verified: scope.py:250; test file 16909-16913]`
7. `/r*/main`, `-r*-main`, and `-rX-main` match none of the six structural detectors and not the tracker-ID scan. `[verified: _lib.sh:2915-2931 and docs/private-project-redaction.md:60-67, by inspection; Verification step 1 re-checks the home-rooted detector mechanically]`
8. A glob built from the new `mine` slug still matches the new `theirs` dir, so the test still tells exact-name matching apart from glob matching. `[unverified — fnmatch '*' semantics; discharged by Verification step 2]`
9. `claude/.claude/tests/helpers.py:564` is that file's only detector match. The matching literal is the default value of the `plan_file_path` parameter of `exitplanmode_input`. `[verified: detector grep of helpers.py this session (1 hit); helpers.py:564-581 read]`
10. No test relies on the default or asserts the old value:
    - `exitplanmode_input` is imported only by `claude/.claude/hooks/tests/test_require_plan_review.py` (`:25`).
    - All 30 of that file's call sites pass `plan_file_path=` explicitly: 22 pass `""`, 6 pass a `tmp_path` file, 1 passes a missing `tmp_path` file, and 1 passes a `/tmp` literal.
    - The string `plans/test-plan.md` appears nowhere else in the repo.

    `[verified: repo-wide grep of exitplanmode_input and plans/test-plan.md this session]`
11. On `ExitPlanMode`, a non-empty `planFilePath` decides the call outright, and an empty one falls through to the repo-relative check (comment at `:100-108`, code at `:109-111`). The hook hashes the path with `sha256sum -- "$PLAN_MODE_FILE_PATH"`, so a relative path resolves against the hook process's working directory (`:113`). A missing or unreadable file fails closed with a deny (`:114-122`), but only when the payload `cwd` is inside a git repo (`:95-98` allows otherwise, for the old and new default alike). A default that is a non-empty absolute path to a missing file therefore gives any future default-using caller the same "cannot read" deny on every machine, inside a git repo. `[verified: claude/.claude/hooks/require-plan-review.sh:95-137 read]`
12. `/nonexistent/.claude/plans/test-plan.md` matches none of the six structural detectors and not the tracker-ID scan. It contains no home prefix, no SSH-directory segment, no key filename, no hex run, none of the internal-hostname detector's six TLD words, and no `#`. The `/nonexistent/` root already appears in three files under `claude/.claude/hooks/tests/`. `[verified: _lib.sh:2915-2931 and docs/private-project-redaction.md:60-67, by inspection; count grep of claude/.claude for the /nonexistent/ root]`
13. `/nonexistent` does not exist on the machines that run this suite. `[unverified — discharged locally by Verification step 3. A machine where it exists changes no test outcome today, because no call site reaches the default (row 10)]`
14. The helper's docstring phrase "matching the real harness shape" refers to the payload's field names (`plan` and `planFilePath` in camelCase), not to the path value. It stays accurate. `[verified: helpers.py:565-574]`
15. `claude/.claude/tests/helpers.py` is in `GLOBAL_TRIGGER_PATHS` (`select-tests.py:263-267`), which is checked before domain matching (`:534-536`). `compute_changed_paths` includes dirty and untracked working-tree paths (`:590-618`). So `select-tests.py` selects the full suite on its own, whether it runs before or after the commit. This is CLAUDE.md's first legitimate full-suite case, and nobody widens the run by hand. `[verified: claude/.claude/scripts/select-tests.py:250-267, 522-556, 590-618 read]`
16. The gate also scans the `git commit` command string (`:520`) and `gh pr create`/`gh pr edit` bodies (`:4`). A commit message or PR body that quotes the old literals would be denied like the fixture lines. `[verified: deny-private-project-refs.sh:4, 520]`
17. The scope covers the transcript test and `claude/.claude/tests/helpers.py`. `[engineer-verified: "Fixture + helpers.py (Recommended)"]`
18. The engineer accepts deferring the three eval fixtures, `deny-reviewer-tree-mutation.sh`, and the seven plan files to a follow-up. `[unverified — this deferral appeared in the selected option's description, which was the architect's consult recommendation relayed by the session, not the engineer's words]`

**Mechanisms**

- **M-1 Rewrite the transcript test in place:** four path strings become `/r*/main`, two slug strings become `-r*-main` and `-rX-main`, and the comment's quoted mapping follows. All changes stay inside the one test. `anchors: root, row1, row2, row4, row7, row8`
- **M-2 Leave the file's other `-home-u-*` and `-home-user-*` slug literals alone.** They cause no gate denial. `anchors: row5`
- **M-3 Keep the transcript test's docstring unchanged.** `anchors: row6`
- **M-4 Replace the `/home/<u>` prefix in the default at helpers.py:564 with `/nonexistent`**, keeping the `.claude/plans/test-plan.md` tail. `anchors: root, row9, row10, row11, row12, row13`
- **M-5 Keep the signature, docstring, and all 30 call sites of `exitplanmode_input` unchanged.** `anchors: row10, row14`
- **M-6 Describe the change in the commit message and PR body without quoting the old literals**, for example "drop the home-rooted prefix from two test fixtures". `anchors: row16`

## Critical files

- **Modify** `claude/.claude/scripts/tests/test_transcript_analysis.py`, inside `TestSkillInvocationRepoScope.test_scope_matches_by_literal_name_not_glob` only (lines 16915, 16916, 16926, 16930, 16934, 16936).
- **Modify** `claude/.claude/tests/helpers.py`, line 564 only: the default value of `plan_file_path`.
- **Reuse:** the class's existing `_worktree_porcelain` helper stays unchanged.
- **Dispatch: one `code-writer` dispatch covering both files, in a single phase.** The two file sets are disjoint. A split still fails the test for splitting, for three reasons:
  - Both edits rest on the same background (G-2's detector contract, row 3's `+`-line scan, row 16's commit-message scan), which each prompt would have to restate.
  - The helpers.py edit alone makes `select-tests.py` run the full suite (row 15). A second dispatch would pay for a second full-suite run.
  - The whole change is about seven lines.

  The dispatch prompt must say that the commit message and PR body must not quote the old path or slug literals (M-6). Its verification command is Verification steps 1-7.

## Verification

A linked worktree has no `.venv` of its own (README.md, Tests section). Run steps 2-6 with `../../../.venv/bin/<tool>` in place of `.venv/bin/<tool>`.

1. `git grep -nE '/(Users|home)/[A-Za-z0-9_.-]+' -- claude/.claude/scripts/tests/test_transcript_analysis.py claude/.claude/tests/helpers.py` prints nothing and exits 1.
2. `.venv/bin/python3 -c "import fnmatch; print(fnmatch.fnmatchcase('-rX-main', '-r*-main'))"` prints `True` (discharges row 8).
3. `.venv/bin/python3 -c "import os; print(os.path.exists('/nonexistent/.claude/plans/test-plan.md'))"` prints `False` (discharges row 13 on this machine).
4. `.venv/bin/pytest "claude/.claude/scripts/tests/test_transcript_analysis.py::TestSkillInvocationRepoScope::test_scope_matches_by_literal_name_not_glob"` passes. This gives fast feedback on the one behavior-bearing edit before the full suite runs.
5. `.venv/bin/python3 claude/.claude/scripts/select-tests.py`. Its stderr should report a full-suite run with reason `global-trigger` naming `claude/.claude/tests/helpers.py` (row 15). That run is the documented command for this diff, so nobody widens it by hand. CI runs the full suite again on push.
6. `.venv/bin/ruff check claude/.claude/scripts/tests/test_transcript_analysis.py claude/.claude/tests/helpers.py`
7. The PR's own `git commit` passes `deny-private-project-refs.sh`. This also checks the new lines, and the commit message, against the user-populated project blocklist, which this plan cannot inspect.

This plan includes no `git blame` step for helpers.py:564. The gate scans only added lines (row 3), so the age of the line has no bearing on whether the fix passes.

## Out of scope

- The gate, `_lib.sh`, and the gate's exclusion list (G-1, G-2).
- Home-rooted-detector matches in other tracked files outside `claude/.claude/hooks/tests/`, deferred to a follow-up (row 18):
  - `evals/fixtures/misfire-plan-review-instead-of-code-review.jsonl`, `evals/fixtures/no-trigger-typo.jsonl`, and `evals/fixtures/skill-fired-code-review.jsonl`, with 1 matching line each.
  - `claude/.claude/hooks/deny-reviewer-tree-mutation.sh`, with 1 matching line.
  - Seven files under `.claude/plans/`, with 10 matching lines in total. These plans are provenance for past changes, so the follow-up must first decide whether they are editable at all.
  - Reproduce the list with `git grep -cE '/(Users|home)/[A-Za-z0-9_.-]+' -- ':(exclude)claude/.claude/hooks/tests/**'`.
- The transcript test file's `-home-u-*` and `-home-user-*` slug literals (row 5).
- Making the `plan_file_path` parameter of `exitplanmode_input` required, which is a signature change beyond this fix.
- The pr-review-skill worktree and PR #718.
- Refactoring the tests.
