# Consolidate throwaway-git-repo test helpers

## Context

Goal: replace the per-file copies of the "build a throwaway git repo" test helper with one shared builder, so the recipe has a single source of truth.

Ask: "Init repo duplication and “The plan-review marker covers: prefix is written in three places”". This is the engineer's answer naming which PR #1193 deferred duplication findings they meant. This plan covers the first half: PR #1193 deferred row "`_init_repo` duplicates the conftest builder". The prefix half ships on a separate branch. On the scope question below, the engineer answered: "What does the architect think?"

PR #1193 added `_init_repo` to `claude/.claude/hooks/tests/test_announce_approved_plan_path.py:118`, a copy of the recipe already in `hooks/tests/conftest.py`'s `git_repo` fixture. Review deferred it as DAMP test code, with no recorded criterion. An inventory this session found the copy is one of many:
- 11 named per-file helpers in `hooks/tests`. Row 17 adds a twelfth that this inventory missed.
- A builder family in `scripts/tests/conftest.py`.
- A method copy in `scripts/tests/test_author_outcome.py`.
- A `repo` fixture in `scripts/tests/test_review_ledger_lib.py` that spells `init` through a `_git` wrapper (row 18).
- 149 `"git", "init"` lines across `claude/.claude` at the merge-base. Of the 23 sites migrated here, 22 carry such a line and one spells `init` through a `_git` wrapper. The rest are inline test-body lines or sit in purpose-built builders (see Out of scope).

The inventory, as extended by row 18, covers named helpers and fixtures found by two greps: the literal `"git", "init"`, and the wrapper-spelled forms `_git(.., "init")`, `_git_q(.., "init")` and `_run_git(.., "init")`. Other spellings were not swept.

The copies vary in four ways:
- the branch: host default, or `-b main` / `-b <branch>`;
- commit versus no commit;
- the committed file name and content;
- the identity strings.

Some tests depend on the variant they use. Zero-commit HEAD tests, branch-name tests and sentinel-file fixtures are examples.

Scope question (posed to the engineer, answer above): how far to consolidate. The candidates, which are this session's proposals and not the engineer's:
- the hooks-tree helpers only;
- every named helper across trees, via `claude/.claude/tests/helpers.py`;
- also the inline sites.

## Approach

Add one builder pair to `claude/.claude/tests/helpers.py`: `init_git_repo` (no commit) and `init_git_repo_with_commit` (one seed commit). Then move onto it every helper or fixture whose body is only the throwaway-repo recipe, in `hooks/tests`, `scripts/tests` and `helpers.py` itself. Each migrated site keeps its branch, whether it commits, and any seed file its tests touch. This branch leaves three things alone: inline test-body sites, purpose-built builders that only open with the recipe, and `evals/`.

**Scope decision.** The engineer handed this call over (row 2). The choice is the architect's, not the engineer's. It is wider than the session's "Hooks-tree helpers" option and narrower than its "All named helpers, every tree" option:

- **Single source of truth versus DAMP.** DAMP keeps setup readable at the point where the test runs. A named helper has already moved setup out of the test body, so a per-file copy of that helper adds no readability, only drift. Copies of named helpers are therefore defects, not DAMP exceptions.
- **When inline setup is DAMP.** Inline Arrange lines are DAMP's core case. Some inline sites build exactly the state the test asserts on: an unborn HEAD, `--bare`, `--object-format=sha256`, `--separate-git-dir`. For those, test-conventions §6 says: "Prefer inline construction over shared fixtures when the data is central to the test's assertion."
- **Audit structural siblings.** The deferred finding's shape is "a named helper re-implementing the shared builder." Its siblings live in three trees (row 4). A hooks-only pass would leave `scripts/tests/conftest.py`'s `_init_repo` and `helpers.py`'s own two copies in place. Three homes would remain and the root would stay unfixed, so the session's hooks-only option is set aside.
- **Axis 4.** Migrating the 128 remaining lines (about 60 inline, 65 in 11 purpose-built-builder files, 3 in the builder and two `--bare` inits) would add dozens of files. Parallel branches routinely touch those test files, and the edit is mechanical with no change in behavior. They go to Out of scope as one follow-up.
- **test-conventions §6.** "Use factory/builder helpers that supply sensible defaults; tests override only the fields relevant to the scenario." This sets the builder's shape: keyword-only overrides, with the most-used variant as the default.
- **select-tests cost.** Editing `helpers.py` selects the full suite (row 11). The two conftest edits already select hooks/tests, scripts/tests and claude/.claude/tests, so the extra cost is the claude-skills/ and plugins/ tests. That extra applies to this PR and to any later edit of the builder.

### Assumption ledger

**Root:** The throwaway-repo recipe (`git init`, a test identity, an optional seed commit) has no single home. Twenty-three named helpers, fixtures and builder prologues across `hooks/tests`, `scripts/tests` and `helpers.py` each re-implement it (PR #1193 deferred row "`_init_repo` duplicates the conftest builder").

**Givens:**
- G1. Each host's `init.defaultBranch` setting decides the branch for any `git init` run without `-b`. The machine or CI image owns that setting, not this repository.
- G2. git defines the remaining semantics: HEAD stays unborn until the first commit, and `-b` names the first branch. git imposes this.

**Rows:**
1. The work targets the init-repo duplication from PR #1193's deferred findings. [engineer-verified: "Init repo duplication"]
2. The engineer handed the scope decision to the architect, so the boundary below is the architect's choice. [engineer-verified: "What does the architect think?"]
3. The "covers:" prefix half ships on a separate branch. That split is the session's, not the engineer's words. Both branches will likely edit `test_announce_approved_plan_path.py`, at separate hunks (`:31-32` versus `:118-125` and `:389`). [unverified]
4. The recipe-only helpers and fixtures in scope are listed below. [verified: read each definition this session] Line numbers are as of the pre-rebase tree and have drifted. Locate each site by symbol; `test_hook_alignment.py`'s helper is `_init_repo_with_commit`.
   - In `hooks/tests`:
     - `test_lib.py:2088` and `:2161`
     - `test_lib_worktree_collision_guard.py:54`
     - `test_hook_alignment.py:2590`
     - `test_announce_approved_plan_path.py:118`
     - `test_require_architect_consult.py:29`
     - `test_log_reviewer_round.py:42`
     - `test_advance_past_commit_stall.py:65`
     - `test_lib_reviewer_round_state.py:30`
     - `test_require_code_review.py:1887`
     - `test_marker_lib.py:143`
     - `test_marker_worktree_keying.py:41`
     - `conftest.py:313-426` (six fixtures)
   - In `scripts/tests`: `conftest.py:996`, `test_author_outcome.py:714` and `test_review_ledger_lib.py`'s `repo` fixture, which spells `init` through its `_git` wrapper and uses `branch="feature"` with no commit.
   - In `helpers.py`: the seed prologue of `bare_remote_with_default_branch` at `:868-875`, and `init_ci_detect_step_test_repo` at `:1609-1616`.
5. Some tests rewrite the seed file their helper committed, so that file must be preserved:
   - `test_require_architect_consult.py` and `test_log_reviewer_round.py`: `_stage_change` rewrites `f.txt`, starting from "first\n".
   - `test_lib_reviewer_round_state.py:136-252`: rewrites `f.txt` as "first\n…".
   - `test_advance_past_commit_stall.py`: `dirty_repo` at `:76-82` leaves `f` as a tracked, unstaged edit.
   - `test_hook_alignment.py:2609`: rewrites `file.txt`.
   - `conftest.py` `git_repo`: stages a second change to `file.txt`.

   These sites read no seed and take the defaults:
   - `test_announce_approved_plan_path.py` (`_run_real_marker_write` writes only plan files).
   - The `test_author_outcome.py` call sites other than the SETTLED test.

   [verified: greps and reads cited]

   The `test_author_outcome.py` SETTLED test (`:1022`) reads `file.txt:1` through its `--source` and `--cited-line` args, so it keeps `file_name="file.txt", content="first\n"`. [verified: read of the test's args]
6. No hook, script, plugin or test reads the configured identity, so setting every site to `t@t.com`/`t` changes no behavior. [verified: grep of `claude/.claude` and `plugins` found no `user.email`, `user.name`, `%ae`, `%an`, `%ce`, `%cn`, `Author:` or `git var` read.] The `GIT_AUTHOR_*` env lines at `test_lib.py:2458`/`:2562` and `test_marker_script.py:173` are inline sites outside scope.
7. `test_marker_lib.py:143` and `test_marker_worktree_keying.py:41` make no commit.
   - Every test that depends on a zero-commit HEAD is inline and untouched (`test_lib.py:2640-2646`, `test_lib_reviewer_round_state.py:198-201`, `test_marker_script.py:3153/:4044/:4325/:4546`). [verified for the `test_lib.py` and `test_lib_reviewer_round_state.py` sites; the `test_marker_script.py` sites are covered by the "not re-read" bullet below]
   - No test of a migrated no-commit site depends on the unborn HEAD, so only the contract test below pins it. [verified: plan-review's SDET pass]
   - The `test_marker_script.py` lines come from the inventory and are untouched either way. [not re-read]
8. Only one migrated helper is imported by name from other files: `scripts/tests/conftest.py`'s `_init_repo`. Seven files import it: `test_autonomous_shipping_active`, `test_branch_divergence_status`, `test_cleanup_idle_open_pr_worktrees`, `test_cleanup_merged_branches`, `test_findings_path_suffix`, `test_pr_diff_against_base` and `test_select_tests`. [verified: grep]
9. The CI detect step depends only on file names, so changing `init_ci_detect_step_test_repo`'s seed message from "initial" to "init" changes no behavior. Swapping `git add .` for `git add -- README.md` is also equivalent, because README.md is the only file present at that point. [verified: `.github/workflows/tests.yml:72` runs `git diff --name-only "$BASE" "$HEAD"`; `helpers.py:1609-1615`]
10. Every tree, conftest files included, can import `helpers.py` as the top-level module `helpers`. [verified: `pyproject.toml:18` pythonpath; `hooks/tests/conftest.py:20`; 20 scripts test files already import `helpers`]
11. Editing `helpers.py` makes select-tests.py select `FULL_SUITE_TARGETS` plus any domain targets outside it. select-tests.py needs no table change, because every new import here is of `helpers`, and no new `hooks.tests.*` import is added. [verified: `select-tests.py:364-368`, `:769-773`, `:163-206`]
12. `FULL_SUITE_TARGETS` does not include `evals/`, and no evals file imports `helpers` today. [verified: `select-tests.py:355`; grep of `evals/`]
13. `git init -b <name>` behaves the same as `--initial-branch=<name>`. [verified: `-b` already sets the branch for 60 callers via `test_lib.py:2163`, and at `helpers.py:866` and `:870`]
14. No migrated caller relies on the old helpers' stricter `mkdir` raising an error. [verified: no file in `hooks/tests`, `scripts/tests` or `tests` references `FileExistsError`. `exist_ok=True` only turns a raise into success.]
15. `helpers.py` functions have no direct unit tests today. [verified: `claude/.claude/tests/` holds only `test_statusline_command.py` and `test_pytest_collection_config.py`] No consumer of `init_git_repo` fails if it commits, so its unborn-HEAD contract needs its own test. [verified: plan-review SDET pass]
16. git 2.43.0 on this machine supports `GIT_CONFIG_COUNT`/`GIT_CONFIG_KEY_0`/`GIT_CONFIG_VALUE_0` env-only config, which needs git 2.31 or later. [verified: `git --version`; the 2.31.0 release notes (`/usr/share/doc/git/RelNotes/2.31.0.txt`) say "Two new ways to feed configuration variable-value pairs via environment variables have been introduced"]
17. `test_require_code_review.py:1887` was missing from the original inventory. Asked whether to keep it in this PR, the engineer selected: "Keep it in scope (Recommended)". [engineer-verified: "Keep it in scope (Recommended)"]
18. The `repo` fixture in `scripts/tests/test_review_ledger_lib.py` was also missing from the inventory, because it spells `init` through a `_git` wrapper. Asked whether to keep it in this PR, the engineer selected: "Keep it in scope (Recommended)". [engineer-verified: "Keep it in scope (Recommended)"]

**Mechanisms:**
- M1. The builder lives in `helpers.py`. Making it a global trigger is a wider-scope choice, and three lighter homes each fail:
  - `hooks/tests/conftest.py` would force each scripts consumer to import `hooks.tests.conftest`, with a hand-added select-tests row. It would also stop `helpers.py`'s own copies from using the builder unless the dependency direction is inverted.
  - A new `hooks/tests/_repo_helpers.py` has the same cross-tree import problem and also needs a new select-tests mapping.
  - One builder per tree leaves three homes in place.

  anchors: root, row10, row11
- M2. The builder is two functions, not one function with a `commit` flag. An unborn HEAD versus a resolvable one is the distinction tests depend on, so the function name carries it. anchors: row7
- M3. The defaults are `branch=None`, `file_name="f.txt"`, `content="x\n"` and identity `t@t.com`/`t`. `branch=None` keeps G1's host-default behavior. Defaulting to `branch="main"` would silently pin host-default sites, even though by call count `branch="main"` is more common (52 of `test_lib.py`'s 67 calls). The seed defaults match `test_lib.py`, the collision guard (32 calls) and four conftest fixtures. Those sites then need no seed override. anchors: G1, row5, row6
- M4. The migration rule has three parts:
  - Each site keeps its original branch, commit-or-not, and seed file and content. The only exceptions are the row-5 sites that never read the seed; they take the defaults.
  - A helper whose preserved arguments equal the defaults, or that only passes `branch` through, is deleted and its calls renamed.
  - A helper that has to pass a non-default seed (`file_name` or `content`) shrinks to a one-line delegation and keeps its name and call sites. Such a helper names the file's scenario defaults, which test-conventions §6 endorses.
  - The threshold: a branch is scenario data the reader should see at the call, so `branch=` alone is passed per call (DAMP), as at `test_lib.py`'s 60 calls. A seed override is setup noise the test never reads at the call, so it stays inside a retained delegation rather than repeated per call (32 times in `test_log_reviewer_round.py` alone).

  anchors: row5, row8
- M5. Scope is exactly the 23 sites listed in row 4. The rule that produced the list was "a helper or fixture whose body is the recipe plus at most one sentinel file, together with `helpers.py`'s internal copies". Only 4 of the 11 deferred files were sampled against it. Out of scope therefore names the deferred fixtures already known to fit the rule. anchors: row2, row4

## Critical files

Every file below is edited in one `code-writer` dispatch, with `helpers.py` first. The dispatch is not split. Every call-site edit depends on the builder's signature and on M4's preservation rule, so split prompts would each have to restate that background. Parallel dispatches would also share one worktree.

- `claude/.claude/tests/helpers.py`
  - Add the builder pair near `_run_git` (`:812`). Write the init line with the literal `"git", "init"`:
    ```python
    def init_git_repo(path: Path, *, branch: str | None = None) -> Path:
        """Create `path` as a git repo with a test identity and no commit. `branch=None` keeps the host's init.defaultBranch."""
        # mkdir(parents=True, exist_ok=True); ["git", "init", "-q"] plus ["-b", branch] when branch is not None;
        # git config user.email t@t.com; git config user.name t; return path

    def init_git_repo_with_commit(
        path: Path, *, branch: str | None = None, file_name: str = "f.txt", content: str = "x\n"
    ) -> Path:
        """`init_git_repo`, then commit one seed file so HEAD resolves."""
        # init_git_repo; mkdir the seed file's parent (parents, exist_ok); write; git add -- file_name;
        # git commit -q -m init; return path
    ```
  - `bare_remote_with_default_branch`: replace the seed recipe (`:868-875`) with `init_git_repo_with_commit(seed, branch=branch, file_name=file_name, content=file_content)`. Keep the bare repo, the remote/push lines and the clone unchanged.
  - `init_ci_detect_step_test_repo`: set `repo = init_git_repo_with_commit(tmp_path / "repo", file_name="README.md", content="initial\n")` (replacing `:1609-1616`). Keep the rest (row 9).
- `claude/.claude/hooks/tests/conftest.py`: add `init_git_repo_with_commit` to the existing `from helpers import`. Keep every fixture's directory name and docstring.
  - `git_repo` uses `branch="main", file_name="file.txt", content="first\n"` (review-ledger scope depends on the branch name) and keeps its staged second change.
  - `opted_in_repo` uses `file_name=".claude/worktree-required", content="# sentinel\n"`.
  - `stray_marker_repo`, `staged_marker_repo` and `repo_with_optout` call the builder with defaults, then keep their sentinel lines.
  - `non_opted_repo` returns the builder's result with defaults.
- `claude/.claude/scripts/tests/conftest.py`
  - `_init_repo` (`:996`) becomes a one-line delegation: `init_git_repo(path, branch=initial_branch)`. Keep its existing `--initial-branch` rationale comment, which still explains the `"main"` default.
  - Correct the docstring to: """Initialise a git repo on `initial_branch` with a test identity and no commit."""
  - Add `from helpers import init_git_repo`.
  - Reword the retained `--initial-branch` comment so it explains the `"main"` default without naming a flag the code no longer spells.
- `claude/.claude/scripts/tests/test_author_outcome.py`: delete the `_make_git_repo` method (`:714`). Each call site uses `repo = init_git_repo_with_commit(tmp_path / "repo", branch="main")`. The SETTLED test's call also passes `file_name="file.txt", content="first\n"`, because its `--source` and `--cited-line` args cite `file.txt:1`. Add `from helpers import init_git_repo_with_commit`; the file does not import `helpers` today.
- `claude/.claude/scripts/tests/test_review_ledger_lib.py`: the `repo` fixture (`:349`) becomes `return init_git_repo(tmp_path / "repo", branch="feature")`, with no commit. Add `init_git_repo` to the existing `from helpers import` line. The local `_git` helper stays, because other code in the file uses it.
- `claude/.claude/scripts/tests/test_select_tests.py`: reword the comment at `:2131` that cites `_init_repo`'s `--initial-branch=main`, so it no longer names a flag the helper does not spell. Comment-only.
- `claude/.claude/tests/test_git_repo_builders.py` (new): a unit contract check for the builder pair, with six tests:
  - `init_git_repo(path)` sets non-empty local `user.email` and `user.name`.
  - Each builder called with no branch keeps the host default (env-only `init.defaultBranch`), parametrized over both builders.
  - `init_git_repo_with_commit(path, branch="probe")` lands on `probe`.
  - `init_git_repo(path, branch="probe")` leaves HEAD unborn (`git rev-parse --verify HEAD` fails) and `git symbolic-ref --short HEAD` prints `probe`.
  - `init_git_repo_with_commit(path)` leaves HEAD resolvable, with `f.txt` tracked and holding `"x\n"`.
  - `init_git_repo_with_commit(path, file_name="-a/b.txt", content="y\n")` tracks the nested seed with that content; the leading dash pins the builder's `--` before the pathspec.
- New `helpers` imports: every hooks test file below that loses its local helper or gains a builder call adds the names it uses to its `from helpers import` line, or adds that line.
- `claude/.claude/hooks/tests/test_lib.py`
  - Delete `_init_repo` (`:2088`). Its 7 calls become `init_git_repo_with_commit(repo)`.
  - Delete `_init_repo_on_branch` (`:2161`). Its 60 calls become `init_git_repo_with_commit(repo, branch="<same>")`.
  - Leave the inline sites at `:2646`, `:6339` and `:6654` untouched.
- `claude/.claude/hooks/tests/test_lib_worktree_collision_guard.py`: delete `_init_opted_in_repo` (`:54`). Its 32 calls become `init_git_repo_with_commit(repo)`.
- `claude/.claude/hooks/tests/test_announce_approved_plan_path.py`: delete `_init_repo` (`:118`). Its one call (`:389`) becomes `init_git_repo_with_commit(repo)`.
- `claude/.claude/hooks/tests/test_marker_lib.py`: delete `_init_repo` (`:143`). Its 18 calls become `init_git_repo(repo)`.
- `claude/.claude/hooks/tests/test_require_code_review.py`: delete `_init_repo_on_branch`. Its 9 calls become `init_git_repo_with_commit(repo, branch="main")`. The file's `helpers` import already carries the builder.
- `claude/.claude/hooks/tests/test_marker_worktree_keying.py`: delete `_init_repo` (`:41`). Its 2 calls become `init_git_repo(other_repo)`.
- These helpers become one-line delegations that keep their names and call sites:
  - `test_hook_alignment.py:2590`: `file_name="file.txt", content="first\n"`
  - `test_require_architect_consult.py:29`: `branch="main", content="first\n"`
  - `test_log_reviewer_round.py:42`: `branch="main", content="first\n"`
  - `test_advance_past_commit_stall.py:65`: `branch="main", file_name="f", content="a\n"`
  - `test_lib_reviewer_round_state.py:30`: `init_git_repo_with_commit(repo, branch=branch, content="first\n")`, keeping its `branch="main"` parameter

  All five files are in `claude/.claude/hooks/tests/`.
- `.claude/plans/consolidate-init-repo-test-helpers.md`: this plan, committed with the change.

Reuse: helpers.py's existing `_run_git` (`:812`) is not used inside the builder. It captures stdout, while every migrated site except the review-ledger fixture lets git's output pass through. That fixture captured output, which `-q` makes moot.

## Verification

1. Before the first edit, record the collected-test count with `.venv/bin/pytest --collect-only -q -n0 claude/.claude/hooks/tests claude/.claude/scripts/tests claude/.claude/tests`. After the last edit, the count must be exactly 10 higher. Seven come from the new contract tests (six test functions, one parametrized over both builders). Three come from `hooks/tests/test_ticket_reference_discipline.py`, which parametrizes three checks over every test file, so the new file adds one case to each. This catches a test lost to a broken import. `--collect-only` does not run fixture bodies, so a broken fixture first surfaces in step 7.
2. Sum `git grep -c '"git", "init"' -- claude/.claude` across files. The total is 149 at the merge-base and must be 128 after: 22 sites removed and 1 added in the builder. The new contract test calls the builders and spells no `"git", "init"`.
3. `git grep -n -E 'def (_init_repo|_init_repo_on_branch|_init_opted_in_repo|_init_repo_with_commit|_make_git_repo)\b' -- claude/.claude` must list exactly six definitions: the five hooks delegations and `scripts/tests/conftest.py`'s `_init_repo`. This checks names only. Step 4 checks the bodies. Also, `git grep -h -E '(_git|_git_q|_run_git)\([^,()]+, "init"' -- claude/.claude | wc -l` is 19 at the merge-base and must be 18 after, which confirms the review-ledger fixture's wrapper-spelled `init` is gone.
4. Per-site equivalence review: walk the diff site by site against row 4 and the Critical files list. For each of the 23 sites, confirm that the branch argument, commit-or-not, `file_name` and `content` match the pre-migration helper. The suite cannot detect drift in these, because no consumer reads them (row 15).
5. Run `claude/.claude/hooks/tests`, `claude/.claude/scripts/tests` and `claude/.claude/tests` at the merge-base and at HEAD with the host default branch forced off `main`, using env-only config: `GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=init.defaultBranch GIT_CONFIG_VALUE_0=probe-default`. Whole trees are needed because the pin-bearing fixtures (`git_repo`, `scripts/tests/conftest.py`'s `_init_repo`) are consumed mostly by untouched files. The two runs must give identical pass/fail outcomes for every test present in both. `test_git_repo_builders.py` has no merge-base counterpart and must pass at HEAD. This catches a dropped or added `-b` pin.
6. Run `.venv/bin/ruff check claude/.claude/ claude-skills/`.
   Remove any `subprocess` imports that become unused only where ruff reports them.
7. Run `.venv/bin/python3 claude/.claude/scripts/select-tests.py`. `helpers.py` is in `GLOBAL_TRIGGER_PATHS`, so the script selects and runs the full suite by itself. That is CLAUDE.md's case 1, so no separate full-suite run is warranted. Case 2 does not apply, because nothing here makes a whole-repo claim beyond what that run covers.

The new `test_git_repo_builders.py` pins the builder contract directly. No migrated consumer discriminates `init_git_repo`'s unborn HEAD from a committed one (row 15), and the follow-up sweep would make that contract load-bearing for the inline zero-commit tests.

## Out of scope

- **Purpose-built builders that open with the recipe.** These are the per-file builders listed in the inventory:
  - `test_check_skill_length.py`, `test_check_claude_md_length.py`, `test_require_plan_review.py`
  - `test_guard_settings_session_keys.py`, `test_deny_reviewer_tree_mutation.py`, `test_require_respond_pr.py`
  - `test_require_stow_reminder.py`, `test_require_ready_for_review.py`, `test_stow_packages.py`
  - `test_install_sh_un_adopt_loop.py`, `test_install_sh_stow_adopt_ignore.py`

  Only some of these were checked against M5's rule.
  - Known to fit the rule, so the first follow-up targets: `test_deny_reviewer_tree_mutation.py:41` and `:61`, `test_check_skill_length.py:60` and `:75`, and `test_guard_settings_session_keys.py`'s `_init_settings_repo_on_branch` (line 88). Also known to fit, in a file outside the eleven above: `test_require_skill_review.py`'s `_init_repo_with_novel_reach_probe_skill` (host default branch, seed `file.txt`/`"first\n"`, then one staged SKILL.md).
  - Borderline: `test_check_skill_length.py:97`.
  - Genuinely purpose-built: `test_require_stow_reminder.py:37`.
  - DAMP does not protect the recipe copies. They are deferred on Axis 4 size grounds only.
  - The follow-up is recorded nowhere yet. The PR body will list it, and the engineer decides whether it becomes an issue.
- **Inline test-body sites.** 128 `"git", "init"` lines remain after this change in total: 65 in the purpose-built-builder files above, about 60 inline in test bodies, and 3 in the new builder and two `--bare` inits. Some inline lines are DAMP-protected special forms or build the state the test asserts on. The rest are plain boilerplate for the same follow-up.
- **`evals/test_review_bench_mining.py:36` `_init_repo` and its 3 importers.** This is a recipe-only sibling. Routing it through `helpers.py` would create a dependency that a later `helpers.py` edit never re-tests, because `evals/` is outside `FULL_SUITE_TARGETS` (row 12). Closing that gap needs a select-tests.py row, which is itself a global trigger and an infrastructure change.
- **select-tests.py.** It needs no edits, and `helpers.py` keeps its global-trigger status.
- **Duplicated git helpers with a different recipe:**
  - `_stage_change`/`_commit_staged`: byte-identical at `test_require_architect_consult.py:39-45` and `test_log_reviewer_round.py:52-58`.
  - `_git`/`_git_q` wrappers in 4 or more hooks test files. `helpers.py` already has `_run_git`.
  - Four `bare_remote` fixtures. `helpers.py:850` is documented as their generalization.
  - The whole-fixture copy at `test_lib_reviewer_round_state.py:421`, which is labeled DAMP.

  These have the same bug shape but fall outside the Ask.
- **Isolating the builder from host git config** (signing, hooks path, template dir, ambient `GIT_*` env). All 23 old copies behaved the same, so this is deliberately out of scope. Any isolation must keep `branch=None` reading the host `init.defaultBranch` (G1).
- **Clone-side identity configs elsewhere in `helpers.py`.** The `t@t.com`/`t` pairs outside `init_git_repo` are excluded.
- **Unswept wrapper-spelled inline sites.** `test_require_npm_version_bump.py`, `test_set_session_title_from_branch.py`, `test_check_branch_divergence.py`, `test_require_plugin_version_bump.py` and `test_lib.py` (about line 7520) spell `init` through a wrapper. Four of those 18 calls are the seed prologue of a `bare_remote` fixture, already deferred under the `bare_remote` bullet above. The other 14 are inline test-body setup. All are out of scope.
- **Pinning a branch where the host default is used today.** That would change behavior (G1).
- **A test that enforces the convention.** No mechanical check can tell DAMP-legitimate inline setup from a copied recipe. A name-based check is trivial to evade. An allowlist that only shrinks over time would cost more than it saves across about 40 files.
- **Recording the "a named-helper copy is not DAMP" rule durably in `test-conventions/SKILL.md`.** That is a global skill edit gated by `/skill-review`. For now the rule lives in this plan and should go in the PR body.
- **The "covers:" prefix duplication.** It ships on a separate branch (row 3).
