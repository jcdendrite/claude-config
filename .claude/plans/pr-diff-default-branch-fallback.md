# Fix pr-diff-against-base.sh's hardcoded `main` fallback

## Context

Goal: fix `~/.claude/scripts/pr-diff-against-base.sh` so it resolves the
correct base branch instead of assuming `main` when no PR is open yet for
the current branch.

Why now: an agent hit this during `/ready-for-review` step 3 in a
repository whose actual default branch is `develop`, not `main`. The
script fell back to `BASE_REF=main`, then failed with "could not resolve
merge-base against origin/main" because `origin/main` doesn't exist there.
The agent had to manually compute `git merge-base origin/develop HEAD`
instead.

Intended outcome: the script resolves the repo's actual default branch
(via `git symbolic-ref refs/remotes/origin/HEAD`, the same mechanism
`require-ready-for-review.sh` already uses) before falling back to a
hardcoded name, so this failure mode doesn't recur in any repo whose
default branch isn't `main`.

## Approach

Resolve the repository's own default branch from `origin/HEAD` before
falling back to any hardcoded name, and only in the branch where `gh pr
view` already failed. When `gh` succeeds, its reported base stays
authoritative and nothing changes. When it fails, the script asks `git
symbolic-ref --quiet --short refs/remotes/origin/HEAD`, strips the
`origin/` prefix, and — only if that ref is unset — tries `origin/main`,
`origin/master`, `origin/develop` in that order. If neither resolves, the
script exits 1 with a message naming the resolution failure rather than
silently diffing against a branch that may not exist.

The resolution shape is lifted from `require-ready-for-review.sh:255-274`,
which already does exactly this (symbolic-ref, then the same
three-candidate loop in the same order). `cleanup-merged-branches.sh:289-298`
independently implements the same idea. `pr-diff-against-base.sh` is the
only site of the three that skips the check entirely, so the fix is
scoped to it.

**Prescribed script body** (`claude/.claude/scripts/pr-diff-against-base.sh`,
replacing lines 6-13; the shebang, header comment, and `set -euo pipefail`
are unchanged):

```bash
# Resolves the repo's own default branch, which is not always main.
resolve_default_branch() {
  local origin_head candidate
  if origin_head=$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null); then
    printf '%s\n' "${origin_head#*/}"
    return 0
  fi
  # Reached only when origin/HEAD is unset, so the name below is a guess, not a lookup.
  for candidate in main master develop; do
    if git rev-parse --verify "origin/$candidate" >/dev/null 2>&1; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

if ! BASE_REF=$(gh pr view --json baseRefName --jq .baseRefName 2>/dev/null); then
  # gh exits nonzero for "no PR open yet" and for auth/network failure alike.
  if ! BASE_REF=$(resolve_default_branch); then
    printf 'pr-diff-against-base.sh: gh pr view failed and no default branch resolved from origin\n' >&2
    exit 1
  fi
  printf 'pr-diff-against-base.sh: gh pr view failed; defaulting base to %s\n' "$BASE_REF" >&2
fi
```

The merge-base block and the final `git diff` stay byte-identical. Every
command substitution that can fail sits in an `if` condition, which
`set -e` exempts (`claude/.claude/rules/shell-script-conventions.md`);
parameter expansion `${origin_head#*/}` (strip through the first `/`)
replaces the `| sed` the hook uses, avoiding a `pipefail` interaction. `local origin_head candidate` is
declared separately from assignment, so ShellCheck SC2155 does not fire.
Nothing here is bash-4-only, so `test_no_bash4_constructs.py` stays green.

**Alternatives weighed and set aside.**

*`gh repo view --json defaultBranchRef` instead of `git symbolic-ref`* —
rejected, and not a close call. The code path being fixed is reached
*precisely because a `gh` invocation just returned nonzero*. That failure
folds "no PR open yet" together with auth failure, rate limiting, and no
network. In the second half of that fold, a second `gh` call fails for
the same reason, so the fix would not fix the bug at all in those cases.
It also adds a network round trip to a path a local ref read answers, and
it diverges from the two sibling implementations in this repo. Its one
real advantage — reading the remote's live default even when local
`origin/HEAD` is stale — is narrow, and the candidate loop plus a loud
merge-base failure already covers the stale case adequately.

*`git remote set-head origin --auto` to repair a missing `origin/HEAD`*
(the `cleanup-merged-branches.sh:291` shape) — rejected as the heavier
primitive. It is a network call that can hang with no timeout, inside a
script `/ready-for-review` blocks on, and it mutates local refs from a
read-only diff-printing script. Same objection as the `gh` alternative:
it is unreachable-remote-fragile in exactly the half of the fold where it
would be needed.

*Keeping `main` as a last-ditch value when nothing resolves* — rejected
in favor of exiting 1. Falling through produces "could not resolve
merge-base against origin/main" for a condition whose real cause is "this
repo has no resolvable default branch," which is what the reporter
actually hit and had to diagnose by hand. This is the one behavior change
beyond the bug fix; it converts one unhelpful error into one accurate
error and never turns a working run into a failing one.

*Extracting a shared resolver across all three call sites* — deferred
(see Out of scope).

### Assumption ledger

**Root problem:** `pr-diff-against-base.sh:11` assigns `BASE_REF=main`
unconditionally when `gh pr view` fails, so in any repository whose
default branch is not `main` the script aborts at the merge-base step and
`/ready-for-review` step 3 produces no diff.

**Givens** (conditions the design treats as fixed):

- **G1 — The call contract is fixed at zero arguments, stdout-is-the-diff.**
  `ready-for-review/SKILL.md:71-73` invokes the script bare, and
  `test_skills.py:2649-2659` asserts step 3 dispatches through it.
  Changing the contract means changing a skill body plus its enforcing
  test, which is a separate decision from this bug.
  `[verified: claude/.claude/skills/ready-for-review/SKILL.md:65-73, claude/.claude/skills/tests/test_skills.py:2649-2659]`
- **G2 — GitHub owns the PR's base branch, and it need not be the repo
  default.** A stacked PR targets another feature branch. The script
  cannot second-guess a base `gh` reports successfully. `[unverified]` —
  no `gh` call was run this session; the claim rests on GitHub's PR
  model, not on observed output. Load-bearing only for keeping `gh`
  first, which is already today's behavior, so a wrong premise here
  regresses nothing.
- **G3 — `_make_repo_with_remote`'s signature can be extended but not
  changed.** 134 call-site occurrences across 9 test files unpack its
  2-tuple positionally with `tmp_path` as the sole argument. Those tests
  belong to other domains (cleanup, worktree, select-tests, cost,
  fidelity, divergence). `[verified: grep of claude/.claude for
  _make_repo_with_remote(, 134 occurrences across 9 files]`

**Mechanism rows:**

1. Resolve via `git symbolic-ref --quiet --short refs/remotes/origin/HEAD`,
   prefix-stripped, as the first fallback source. This is the exact
   mechanism the reporter confirmed returns the correct answer in the
   failing repository, and two sibling scripts in this repo already use
   it. `[verified: claude/.claude/hooks/require-ready-for-review.sh:255-274,
   claude/.claude/scripts/cleanup-merged-branches.sh:289-298]` `anchors: root`
2. When `origin/HEAD` is unset, probe `main`, `master`, `develop` in that
   order with `git rev-parse --verify`. Same list and same order as the
   hook, so the two agree on any repo where both run.
   `[verified: claude/.claude/hooks/require-ready-for-review.sh:258-264]`
   `anchors: row1`
3. Lighter-primitive check on rows 1-2: two heavier alternatives exist and
   both fail. `gh repo view --json defaultBranchRef` re-enters the tool
   that just failed, so it cannot serve the auth/network half of the
   fold. `git remote set-head origin --auto` adds an untimed network call
   and a local-ref write to a read-only script. The chosen mechanism is
   two local ref reads with no writes and no network.
   `[verified: claude/.claude/scripts/pr-diff-against-base.sh:7-13 for the
   fold; claude/.claude/scripts/cleanup-merged-branches.sh:291 for the
   set-head shape]` `anchors: row1`
4. Exit 1 with a distinct message when neither source resolves, rather
   than falling through to `origin/main`. The fall-through emits an error
   naming a branch the repo may never have had, which is the misleading
   symptom the reporter debugged by hand.
   `[verified: claude/.claude/scripts/pr-diff-against-base.sh:15-18]`
   `anchors: root`
5. Leave the `gh pr view` call and the merge-base block untouched, so the
   two existing tests that cover them (`TestNormalPathAgainstMain`,
   `TestMergeBaseFailure`) pass unmodified. Resolution runs only inside
   the failure branch. `[verified:
   claude/.claude/scripts/tests/test_pr_diff_against_base.py:60-101]`
   `anchors: row4`
6. Residual limitation, accepted: a repo with `origin/HEAD` unset *and*
   both `origin/main` and `origin/develop` present picks `main`, which
   may be wrong. The stderr line names the branch chosen on every
   fallback run, so the wrong pick is visible rather than silent, and
   this is strictly a superset of today's behavior. `[verified: the
   prescribed "defaulting base to %s" line names the resolved branch]`
   `anchors: row2`
7. Add a defaulted `default_branch: str = "main"` parameter to
   `_make_repo_with_remote` and a matching `initial_branch: str = "main"`
   to `_init_repo`, threading it to the bare `git init`, the local `git
   init`, `git push -u`, and `git remote set-head`. A defaulted keyword
   leaves all existing positional callers byte-identical, and the
   alternative — a local copy of the scaffolding in the test file — would
   duplicate all four of those call sites.
   `[verified: claude/.claude/scripts/tests/conftest.py:581-610]`
   `anchors: root`
8. Four new tests pin the fix, row 2's candidate-loop success path, the
   new error path, and the invariant most at risk from a careless later
   refactor (gh's answer must beat the resolved default). The primary
   fix test uses a default-branch name (`trunk`) outside the
   `main`/`master`/`develop` candidate list so it isolates row 1 from
   row 2, per `staff-sdet` plan-review Finding 1. `[verified: existing
   shim helpers _gh_shim_source/_env_with_gh_shim at
   claude/.claude/scripts/tests/test_pr_diff_against_base.py:22-51 cover
   all four]` `anchors: row1, row2, row4, row5`

## Critical files

One `code-writer` dispatch. The three files are one coupled change — the
conftest parameter exists only to serve the new test, and the test only
passes with the script change — so splitting would force the same
background into two prompts.

**Modify — `claude/.claude/scripts/pr-diff-against-base.sh`**
Replace lines 6-13 with the prescribed body in Approach. Lines 1-5 and
15-20 are unchanged. The existing three-line comment at lines 8-10
collapses to the single sentence shown; its "both fall back to main"
clause is no longer true after this change, and
`claude/.claude/rules/shell-script-conventions.md` calls for one sentence
per non-obvious fact rather than a rationale block.

**Modify — `claude/.claude/scripts/tests/conftest.py`**
- `_init_repo` (line 581): add `initial_branch: str = "main"`, use it in
  the `--initial-branch=` argument at line 586. Leave the existing
  comment at lines 584-585 in place — it still explains why the flag is
  passed at all.
- `_make_repo_with_remote` (line 597): add `default_branch: str = "main"`,
  use it at the bare `git init` (line 601), the `_init_repo` call
  (line 604), `git push -q -u origin` (line 607), and `git remote
  set-head origin` (line 609).
- Do not touch `_make_feature_branch` — its existing `return_to: str =
  "main"` parameter is already the needed knob.

**Modify — `claude/.claude/scripts/tests/test_pr_diff_against_base.py`**
- Extend the import at line 16 to `from .conftest import _commit,
  _init_repo, _make_feature_branch, _make_repo_with_remote`
  (relative-sibling form is required by
  `.claude/rules/test-tree-packaging.md`).
- Fix the stale reference at line 28: the script has no `|| echo main`
  fallback, it has an `if !` block. Reword to name the default-branch
  fallback path instead.
- Add to `TestGhPrViewFailureFallback`. The default branch is deliberately
  `trunk`, not `develop` — `develop` is itself one of the row-2 candidate
  names, so a `develop`-named scaffold can't tell "resolved via
  `symbolic-ref`" apart from "accidentally resolved via the candidate
  loop because the name happens to collide" (`staff-sdet` plan-review
  Finding 1). `trunk` sits outside `main`/`master`/`develop`, so this
  only passes if row 1 itself resolved it:

```python
    def test_gh_pr_view_failure_resolves_non_main_default_branch(self, tmp_path):
        # "trunk" is outside main/master/develop so this only passes via
        # symbolic-ref, not the candidate loop.
        local, _bare = _make_repo_with_remote(tmp_path, default_branch="trunk")
        _make_feature_branch(local, "feat/on-trunk", return_to="trunk")
        subprocess.run(["git", "checkout", "-q", "feat/on-trunk"], cwd=local, check=True)

        env = _env_with_gh_shim(tmp_path, None)
        result = _run_script(local, env)

        assert result.returncode == 0
        assert "+work on feat/on-trunk" in result.stdout
        assert "defaulting base to trunk" in result.stderr
```

- Add a class covering row 2's candidate-loop success path — the one
  branch of `resolve_default_branch` none of the other new tests reach
  (`staff-sdet` plan-review Finding 2). `git remote set-head origin
  --delete` removes the local `origin/HEAD` symref while leaving the
  `origin/main` tracking ref itself resolvable, which is exactly the
  condition row 2 exists to handle:

```python
class TestCandidateLoopFallback:
    def test_missing_origin_head_falls_back_to_candidate_loop(self, tmp_path):
        # When origin/HEAD's symref is absent, the main/master/develop
        # candidate loop must still recover origin/main.
        local, _bare = _make_repo_with_remote(tmp_path)
        subprocess.run(["git", "remote", "set-head", "origin", "--delete"], cwd=local, check=True)
        _make_feature_branch(local, "feat/no-symref", return_to="main")
        subprocess.run(["git", "checkout", "-q", "feat/no-symref"], cwd=local, check=True)

        env = _env_with_gh_shim(tmp_path, None)
        result = _run_script(local, env)

        assert result.returncode == 0
        assert "+work on feat/no-symref" in result.stdout
        assert "defaulting base to main" in result.stderr
```

- Add a class covering the gh-wins invariant. The merge-base is
  `staging`'s tip, so `-work on staging` appears only if the reported
  base was honored; had the default branch been used, the diff would
  show `-init` instead:

```python
class TestReportedBaseOverridesDefaultBranch:
    def test_stacked_pr_diffs_against_reported_base_not_repo_default(self, tmp_path):
        # Regression test: gh's reported base must win over the repo's own
        # default branch (a stacked PR targets another feature branch).
        local, _bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "staging")
        subprocess.run(["git", "checkout", "-q", "staging"], cwd=local, check=True)
        _make_feature_branch(local, "feat/stacked", return_to="staging")
        subprocess.run(["git", "checkout", "-q", "feat/stacked"], cwd=local, check=True)

        env = _env_with_gh_shim(tmp_path, "staging")
        result = _run_script(local, env)

        assert result.returncode == 0
        assert "+work on feat/stacked" in result.stdout
        assert "-work on staging" in result.stdout
        assert result.stderr == ""
```

- Add a class covering the new error path:

```python
class TestDefaultBranchUnresolvable:
    def test_repo_without_origin_aborts_naming_the_resolution_failure(self, tmp_path):
        # Regression test: no origin remote at all must produce a message
        # naming the resolution failure, not a stale "origin/main" guess.
        local = tmp_path / "no-remote"
        _init_repo(local)
        _commit(local, "init")

        env = _env_with_gh_shim(tmp_path, None)
        result = _run_script(local, env)

        assert result.returncode == 1
        assert result.stdout == ""
        assert "no default branch resolved" in result.stderr
```

**Reuse, not reimplementation**
- The resolution shape comes from
  `claude/.claude/hooks/require-ready-for-review.sh:255-274` — copy the
  *shape*, not the code. That hook's version is wrapped in PreToolUse
  plumbing (`_lib_capped` timeouts, `cd "$CWD"` per command, tool-input
  JSON parsing) that a plain user-facing script has no use for.
- `_gh_shim_source` and `_env_with_gh_shim` (test file lines 22-51)
  already produce every shim variant the new tests need; do not add a
  second shim.
- `_make_feature_branch`'s `return_to` parameter already handles a
  non-`main` starting branch.

**Explicitly not modified**
- `claude/.claude/hooks/require-ready-for-review.sh` and
  `claude/.claude/scripts/cleanup-merged-branches.sh` — both already
  check `origin/HEAD` before any hardcoded name and do not carry this
  bug.
- Documentation — no file under `docs/` references
  `pr-diff-against-base.sh` (repo-wide grep for the script name returns
  only the script, its tests, `ready-for-review/SKILL.md`,
  `test_skills.py`, and two prior plan files).

## Verification

Inner loop while iterating:

```bash
.venv/bin/pytest claude/.claude/scripts/tests/test_pr_diff_against_base.py
```

Red-then-green check, run before the script edit: with only the conftest
and test changes applied, `test_gh_pr_view_failure_resolves_non_main_default_branch`
must fail with `could not resolve merge-base against origin/main` on
stderr. If it passes at that point the test is not pinning the reported
bug. `test_missing_origin_head_falls_back_to_candidate_loop` is not part
of this check — its scaffold's default branch is `main`, so the
pre-fix hardcoded `BASE_REF=main` satisfies it by coincidence; that
test's job is forward coverage of row 2, not bug reproduction.

Gate, per this repo's `CLAUDE.md`:

```bash
.venv/bin/python3 claude/.claude/scripts/select-tests.py
```

For this diff `select-tests.py` selects `claude/.claude/scripts/tests`
(domain rule, `select-tests.py:250`) plus `claude/.claude/hooks/tests` and
`claude/.claude/skills/tests` (cross-domain exception
`_is_scripts_dir_shell_script_change`, `select-tests.py:320`, because a
`.sh` under `scripts/` changed). That selection already runs
`test_shellcheck.py` (`claude/.claude/hooks/tests/test_shellcheck.py:305`,
which lints every tracked shell script) and `test_no_bash4_constructs.py`,
so no separate `shellcheck` invocation is needed. It also runs the full
`scripts/tests` directory, which is what proves the `conftest.py`
signature change did not disturb the 8 other test files that call
`_make_repo_with_remote`.

Not verifiable in this repository: behavior against a real remote whose
default branch is not `main`. The new test reproduces that condition with
a local bare remote and `git remote set-head`, which is the same ref `git
symbolic-ref` reads against a real remote, so the coverage is equivalent
for this script's purposes.

## Out of scope

- **A shared default-branch resolver across all three call sites.**
  `require-ready-for-review.sh`, `cleanup-merged-branches.sh`, and this
  script each resolve the default branch, and after this change all
  three are correct. Unifying them is a real DRY improvement but a
  different change: the three run in genuinely different environments (a
  PreToolUse hook with JSON parsing and per-command timeout wrappers, a
  cleanup script with its own remote-repair step, and a plain script),
  and no shared shell library currently spans `hooks/` and `scripts/`.
  Raise it to the reviewer rather than bundling it.
- **`git remote set-head origin --auto` repair** when `origin/HEAD` is
  unset. Rejected on the merits above, not merely deferred; recorded here
  because it would otherwise look like an oversight against the
  `cleanup-merged-branches.sh` precedent.
- **Distinguishing "no PR open yet" from a `gh` auth/network failure.**
  The script folds them today and continues to. Separating them needs a
  second `gh` call that is unavailable in exactly the case that would
  need it.
- **`_init_repo`'s docstring at `conftest.py:582`** ("with one commit and
  a remote pointing at itself") describes neither behavior — the
  function neither commits nor adds a remote. Real, pre-existing, and
  unrelated to this bug; leave it and mention it to the reviewer.
- **A `docs/scripts.md` entry for `pr-diff-against-base.sh`.** The script
  is currently undocumented there. Adding one is a docs change with its
  own review surface, not a prerequisite for this fix.
