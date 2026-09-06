# Close PR #785 deferred findings

## Context

Close 4 findings the engineer deferred from PR #785's own review, all under
`claude/.claude/hooks/`. PR #785 ("Close PR #771 deferred findings") left:

- Finding 1: a brittle wall-clock upper-bound assertion, fixed in one of
  its own new tests but left unfixed in 4 sibling tests with the identical
  pattern.
- Finding 2: 3 of `require-ready-for-review.sh`'s 4 fail-open `_lib_capped`
  timeout paths untested.
- Finding 3: one `gh`-network call using a bare `timeout 5` instead of the
  repo's own capped wrapper.
- Finding 4: test coverage for only one arm of a two-arm detection
  asymmetry the hook's header comment documents.

This session verified all 4 findings' current locations against
`origin/main @ 15071232` before planning. Two premise corrections surfaced
during that verification. Finding 1 actually affects 4 sibling tests, not
3. Finding 4's helper is called by 4 hooks, not 6 — the other 2
deliberately don't reuse it, a documented exception, not a bug.

Intended outcome: one PR closing all 4 findings, each with a targeted fix,
landing via the standard `plan-it` → `plan-review` → `code-writer` →
`code-review` → `ready-for-review` pipeline.

## Approach

Close all four findings in one PR: extract the duplicated git-timeout-shim
scaffolding into a `hooks/tests/conftest.py` fixture and convert all five
call sites to the lower-bound margin PR #785 already established, add two
fail-open regression tests plus one hook-level full-path `git push` test,
add a parametrized unit test pinning `_lib_fragment_invokes_git`'s own
documented contract, and swap the one bare `timeout 5 gh` call to
`_lib_capped`. Reading the hook turned up a fifth item the batch should
absorb: `require-ready-for-review.sh:60-66` labels the `CURRENT_BRANCH`/`DEFAULT_BRANCH`
timeout paths "fail OPEN," but a timeout there withholds the default-branch
bypass and hands the decision to the gate below rather than exiting 0 — the
finding-2 test is what settles that, and the comment gets corrected in the
same PR if it does.

**Root problem.** Four review findings deferred from PR #785 leave three
gaps in `claude/.claude/hooks/`: a brittle wall-clock upper bound duplicated
across four sibling tests, three uncovered `_lib_capped` timeout paths and
one uncovered detection arm, and one git-adjacent call escaping the repo's
own timeout wrapper.

**Givens**

- **G1 — `timing`-marked tests run under `-n auto` by default.** `pyproject.toml:24`
  sets `addopts = ["-n", "auto", "--strict-markers"]` with no `-m` filter,
  and the marker's own text names `-m timing -n0` as the serial mode.
  Repo-wide collection config governing every domain's suite; changing it is
  outside this batch's reach. `[verified: pyproject.toml:24-27]`
- **G2 — the 5s cap is fixed in shared library policy.** `_lib_capped`
  hardcodes 5s via `_lib_capped_for 5 "$@"`, and every capped call in
  `require-ready-for-review.sh` uses the bare wrapper with no per-call
  override. Changing the cap is a repo-wide change cited by every hook, not
  this batch's. `[verified: _lib.sh:27-29; require-ready-for-review.sh:184,196,197,200,238]`
- **G3 — `timeout(1)` presence is a machine property.** Stock macOS ships
  no GNU coreutils, so every shim test guards with
  `shutil.which("timeout")` and skips. Platform-imposed.
  `[verified: test_deny_pii_in_commits.py:229-230; test_require_ready_for_review.py:403-404]`

**Assumption ledger**

1. The four sibling tests assert `elapsed < 9.5` with no lower bound.
   `[verified: test_deny_pii_in_commits.py:242, 269, 296, 322]`
2. The already-fixed sibling asserts only `elapsed > 4`.
   `[verified: test_require_ready_for_review.py:427-431]`
3. All five duplicate the same scaffolding: two `shutil.which` skip guards,
   the shim write plus `chmod(0o755)`, `env = {"PATH": f"{tmp_path}:{os.environ['PATH']}"}`,
   and a `time.monotonic()` wrapper. `hooks/tests/conftest.py` defines no
   timing or shim fixture.
   `[verified: test_deny_pii_in_commits.py:218-322; test_require_ready_for_review.py:400-431; conftest.py fixture inventory at :57-238]`
4. Both consumer files live in `claude/.claude/hooks/tests/`, so that
   directory's `conftest.py` is the narrowest shared home.
   `claude/.claude/tests/helpers.py` is wider and sits in
   `GLOBAL_TRIGGER_PATHS`, which forces a full-suite selection on every
   future edit. `[verified: select-tests.py:173-182]`
5. The shim must read `os.environ["PATH"]` at call time, not fixture-setup
   time — `fake_gh_pr_exists` prepends its own bin dir via
   `monkeypatch.setenv`, and the current tests get the ordering right only
   because the read happens in the test body.
   `[verified: test_require_ready_for_review.py:32-43, 418]`
6. A `REPO_ROOT` timeout empties the variable and `exit 0`s — the only path
   where the timeout itself produces an allow.
   `[verified: require-ready-for-review.sh:184-187]`
7. The baseline for `repo_on_feature_branch` + `fake_gh_pr_exists` + no
   completion marker is deny, so a shimmed allow is a real decision
   inversion rather than a vacuous pass.
   `[verified: test_require_ready_for_review.py:386-431 asserts deny on exactly those fixtures]`
8. A `CURRENT_BRANCH` timeout does not exit 0; the empty value fails
   `[ -n "$CURRENT_BRANCH" ]` and falls through to the gate.
   `[verified: require-ready-for-review.sh:196, 206-208]`
9. On a repo checked out on `main`, shimming `rev-parse --abbrev-ref` traces
   to deny: line 184 passes through, line 196 times out, line 197 returns
   empty (the fixture sets `refs/remotes/origin/main` as a plain ref, not
   `origin/HEAD` as a symbolic ref), the candidate loop recovers
   `DEFAULT_BRANCH="main"`, line 206's bypass is withheld because
   `CURRENT_BRANCH` is empty, `fake_gh_pr_exists` supplies PR 42, and the
   marker check finds no match. **`[unverified]` on the execution axis** —
   derived by reading, not run. Step 3 below is what verifies it.
10. The header comment labels `REPO_ROOT`, `CURRENT_BRANCH`/`DEFAULT_BRANCH`,
    and the candidate loop as uniformly failing open. Row 9 says that holds
    for `REPO_ROOT` only. `[verified: require-ready-for-review.sh:60-66]`
11. Line 225 uses a bare `timeout 5 gh pr view`; no `_lib_capped_for` call
    exists in the file. `[verified: require-ready-for-review.sh:225]`
12. `_lib.sh:23-25` documents the exact gap: a bare `timeout 5 …` is
    `command not found` (127) on stock macOS, silently yielding empty
    output. `deny-reviewer-tree-mutation.sh:237-240` is an in-repo
    precedent comment for the same swap.
    `[verified: _lib.sh:20-29; deny-reviewer-tree-mutation.sh:237-240]`
13. The swap is not behavior-neutral on a machine with neither `timeout`
    nor `gtimeout`. The bare form currently fails open via 127 (command
    not found), so the gate never runs. `_lib_capped` instead runs `gh`
    for real and returns a genuine PR number, so the gate applies. A slow
    or hung `gh` in that lookup (a stalled proxy, a keychain credential
    prompt with no one to answer it) is no longer bounded by the
    accidental 127 fail-open — it can now stall the `PreToolUse` call
    indefinitely, governed only by the harness's own hook timeout if any
    (`_lib.sh:14-15`'s comment on `_lib_jq` names the same dependency).
    State all three consequences in the PR body rather than claiming no
    behavior change. `[verified: staff-platform-engineer dispatch this
    round, reading _lib.sh:38-48, 41-48; require-ready-for-review.sh:225-228]`
14. No test pins the literal `timeout 5 gh` string, so the swap breaks
    nothing. `[verified: grep for "timeout 5" across claude/.claude/hooks/tests/ — one unrelated hit at test_deny_network_installs.py:149]`
15. Zero tests exercise `_lib_fragment_invokes_git`; the only occurrence in
    the test tree is a prose mention.
    `[verified: grep across claude/.claude/hooks/tests/ — sole hit test_deny_network_installs.py:90]`
16. `_lib.sh:523-524` states the helper's own Accepts/Rejects contract,
    naming `/usr/bin/git status` as accepted and `ls .github/`,
    `cat .gitignore`, `grep github.com`, `./git-foo` as rejected. The
    implementation matches: it accepts a word equal to `git` or ending in
    `/git`. `[verified: _lib.sh:517-538]`
17. `test_lib.py:51-60` already has `_run_lib_call(call, env)`, which
    sources `_lib.sh` and runs one statement. `[verified]`
18. Four hooks call `_lib_fragment_invokes_git`; the two non-callers are
    documented deliberate exceptions, not bugs.
    `[verified: dispatch exploration — deny-network-installs.sh:84-85, require-worktree-for-git-writes.sh:155-162]`
19. `test_require_ready_for_review.py:180-185` already establishes the idiom
    for a default-branch scenario: `repo_on_feature_branch` plus an inline
    `git checkout -q main`. No new fixture is needed. `[verified]`
20. `select-tests.py` maps every path under `claude/.claude/hooks/` to
    `HOOKS_TESTS_DIR`, and the `.sh` edit additionally triggers the
    shellcheck/bash4 targets. `[verified: select-tests.py:239-245]`

**Mechanisms**

- **M1 — a `git_timeout_shim` factory fixture plus an
  `assert_cap_engaged` context manager in
  `claude/.claude/hooks/tests/conftest.py`** (anchors: row3, row4, row5).
  CLAUDE.md's sibling-audit rule is the deciding one: "abstract into a
  shared helper once two or more share it." Five arms share it, and the
  margin already drifted once — one arm fixed, four left brittle, which is
  the exact failure mode single-source-of-truth exists to prevent. DAMP
  protects test *intent*, and intent stays local: each test keeps its own
  docstring, fixture seeding, shim match condition, and decision assertion.
  Only undifferentiated mechanism moves. Lighter primitives weighed and
  rejected: (i) fix the four assertions and extract nothing — leaves five
  copies of a constant that has already drifted; (ii) a module-level helper
  duplicated in each of the two files — relocates the duplication instead
  of removing it, and the margin constant still lives in two places; (iii)
  `claude/.claude/tests/helpers.py` — heavier, not lighter, since it is a
  `GLOBAL_TRIGGER_PATHS` entry that forces a full-suite selection on every
  future edit (row 4).

  Shape: the fixture takes `tmp_path`, performs both `shutil.which` skip
  guards, and returns `install(match_condition: str) -> dict[str, str]`
  that writes the shim and builds the PATH env at call time (row 5). Name
  the sleep duration and the floor as module constants in `conftest.py` so
  neither can drift again. The context manager wraps the `run_hook` call,
  times it, and asserts the floor — chosen over a plain
  `assert_cap_engaged(elapsed)` function because the plain form leaves two
  lines of `time.monotonic()` bookkeeping at each site and is omittable,
  and omission is the drift being fixed. An autouse or implicitly-asserting
  fixture is heavier still and was rejected as invisible at the call site.

- **M2 — two new `timing` tests, not four** (anchors: row6, row8, row9,
  G1). `test_repo_root_git_timeout_allows` shims `rev-parse --show-toplevel`
  and asserts `allow`, inverting the baseline deny (row 7) — this alone
  discharges the finding's "at least one fail-open path" with the
  strongest available evidence. `test_current_branch_git_timeout_arms_the_gate`
  shims `rev-parse --abbrev-ref` (match on `$2 = "--abbrev-ref"`, so it
  does not also catch line 238's `rev-parse HEAD`) on a repo checked out on
  `main` per row 19. The lighter primitive — a single REPO_ROOT test — was
  rejected because it leaves the header's `CURRENT_BRANCH` claim
  prose-only, and reading says that claim is the one that is wrong. No
  dedicated test for `symbolic-ref` (line 197) or the candidate loop (line
  200): both land on the same downstream branch as the second test, so a
  third would buy a duplicate assertion at full serial cost.

- **M3 — correct the header comment, gated on M2's observed result**
  (anchors: row10). This is a description of current behavior, not a
  preserved record, so Axis 3 does not bar it, and the file is already
  open for finding 3. If the second test denies, replace lines 62-65 with:

  ```
  # A REPO_ROOT timeout exits 0 and allows immediately — the only timeout
  # path in this hook that does.
  # A CURRENT_BRANCH, DEFAULT_BRANCH, or candidate-loop timeout does not
  # allow directly: it withholds the default-branch bypass and lets the
  # gate below decide, which can still deny.
  # Only CURRENT_HEAD fails closed on that timeout (see its own comment below).
  ```

  If it allows instead, the reading in row 9 is wrong: leave the comment
  alone, and write the test's docstring to what the hook actually does. Do
  not edit the hook to match the prediction.

- **M4 — swap line 225 to `_lib_capped`** (anchors: row11, row12, row13,
  row14). Mechanical, with an in-repo precedent comment to mirror. Do
  **not** add an exit-status check: `_lib.sh:26` says callers must check
  the status, but here the existing `[ -z "$PR_NUMBER" ]` test *is* the
  documented fail-open, and adding a status check would flip this call
  closed — a defensive layer closing no gap. Net correctness improvement
  on any platform with `timeout` or `gtimeout` present; row 13's two
  consequences on a platform with neither (real PR-number lookup now
  runs, and a hang in that lookup is no longer bounded by the prior
  wrapper's accidental 127 fail-open) belong in the PR body, per
  `staff-platform-engineer`'s plan-review pass. Update the comment at
  221-223, which currently names the bare wrapper:

  ```
  # Network call, capped via _lib_capped so a hanging gh doesn't stall the tool-call.
  # On error or timeout, fail open — offline or flaky-network work must not brick.
  # Uses _lib_capped rather than a bare `timeout 5`: without GNU coreutils, bare
  # `timeout 5` is "command not found" (127), which silently fails this check open.
  ```

- **M5 — one parametrized unit test plus one hook-level test; no
  per-consumer coverage** (anchors: row15, row16, row17, row18). The unit
  test in `test_lib.py` is the single-source coverage: the helper is pure
  string logic with no per-caller variation, so the other three consumers
  gain nothing from their own subprocess-level tests. Parametrize it over
  the Accepts/Rejects lists the helper's own doc comment already names
  (row 16), turning that comment into an executable spec — do not invent
  cases beyond the two lists. The hook-level test earns its place for a
  different reason than coverage: `require-ready-for-review.sh:56-59`
  documents an asymmetry between the two arms, and PR #785 pinned only the
  gap side. `/usr/bin/git push origin feature` on `repo_on_feature_branch`
  + `fake_gh_pr_exists` + no marker should deny, making the header's claim
  test-backed in both directions. This one is not `timing`-marked, so it
  runs in the parallel job.

- **M6 — one PR, one `code-writer` dispatch** (anchors: root). The four
  findings do not partition into non-overlapping file sets:
  `test_require_ready_for_review.py` is touched by findings 1, 2, and 4,
  and `require-ready-for-review.sh` by findings 2 and 3. Parallel
  dispatches would share this worktree and clobber silently. plan-it's
  split rule therefore resolves to a single dispatch with ordered steps.
  The PR #771 precedent (three fixed, two filed) applies when a finding
  needs a decision the batch cannot settle; none of these do — three are
  test-only and one is a one-line hook change with a documented in-repo
  precedent. The one filing candidate is conditional: if M2's second test
  produces a third behavior neither row 9 nor the header predicts, file
  that as a tracking issue and land the rest rather than expanding this PR
  into a hook-behavior change.

## Critical files

Single `code-writer` dispatch, steps in this order (later steps depend on
earlier ones' output).

**Step 1 — extract the shim scaffolding.**
- `claude/.claude/hooks/tests/conftest.py` — add the `git_timeout_shim`
  factory fixture, the `assert_cap_engaged` context manager, and the two
  named constants (shim sleep seconds, cap-engaged floor seconds). Reuse:
  the shim body, `chmod(0o755)`, and PATH-prepend recipe are lifted
  verbatim from `test_require_ready_for_review.py:411-418`.

**Step 2 — convert the five existing call sites (finding 1).**
- `claude/.claude/hooks/tests/test_deny_pii_in_commits.py:218-322` — four
  tests. Drop `assert elapsed < 9.5`; route through the new fixture and
  context manager. Keep each docstring and each `assert decision == "deny"`
  local.
- `claude/.claude/hooks/tests/test_require_ready_for_review.py:386-431` —
  the already-fixed test converts to the same shape; its `elapsed > 4`
  becomes the shared floor constant.

**Step 3 — add the fail-open regression tests (finding 2).**
- `claude/.claude/hooks/tests/test_require_ready_for_review.py` — two new
  `@pytest.mark.timing` tests. Reuse: `repo_on_feature_branch` (`:59`),
  `fake_gh_pr_exists` (`:32`), and the inline `git checkout -q main` idiom
  at `:180-185` for the second test. Record the second test's observed
  decision; it gates Step 4.

**Step 4 — hook edits (findings 2 and 3).**
- `claude/.claude/hooks/require-ready-for-review.sh:225` —
  `timeout 5 gh pr view …` → `_lib_capped gh pr view …`; update the comment
  at `:221-223`. Reuse: `deny-reviewer-tree-mutation.sh:237-240`'s
  precedent wording.
- `claude/.claude/hooks/require-ready-for-review.sh:62-65` — header
  correction, **only if** Step 3's second test denied.

**Step 5 — detection coverage (finding 4).**
- `claude/.claude/hooks/tests/test_lib.py` — parametrized
  `_lib_fragment_invokes_git` test over the Accepts/Rejects lists at
  `_lib.sh:523-524`. Reuse: `_run_lib_call` (`test_lib.py:51-60`).
- `claude/.claude/hooks/tests/test_require_ready_for_review.py` — one
  non-timing test for `/usr/bin/git push origin feature`, sited next to
  `test_full_path_gh_invocation_bypasses_detection` (`:708-728`) and
  mirroring its docstring shape.

Deliberately **not** touched: `claude/.claude/tests/helpers.py` — a
`GLOBAL_TRIGGER_PATHS` entry (`select-tests.py:179`); putting the fixture
there would force a full-suite selection on every future edit to it.

## Verification

Two invocations, both from the repo root with the worktree-relative venv:

```bash
.venv/bin/python3 claude/.claude/scripts/select-tests.py
.venv/bin/python3 claude/.claude/scripts/select-tests.py -m timing -n0
```

The first is the project's documented scoped command (`CLAUDE.md` Commands
section); every changed path is under `claude/.claude/hooks/`, which
`select-tests.py:245` maps to `HOOKS_TESTS_DIR`, and the `.sh` edit
additionally selects the shellcheck and bash4-construct targets via
`_is_hooks_dir_shell_script_change` (`:239-240`). No `helpers.py` or
`pyproject.toml` edit, so nothing widens to the full suite.

The second is required because this PR's subject *is* the timing tests'
wall-clock bounds. `pyproject.toml:24` sets no `-m` filter, so the first
run does collect them — but under `-n auto`, which is the parallel-load
condition the removed upper bound failed against. `pyproject.toml:26`
names `-m timing -n0` as the marker's own documented serial mode; run it
to confirm the six shim tests pass serially as well as under load.

Also run, per `CLAUDE.md` Commands:

```bash
scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck
.venv/bin/ruff check claude/.claude/
```

Expected new-test outcomes to check by name rather than by suite-green
alone:
- REPO_ROOT test → `allow`, elapsed above the floor.
- CURRENT_BRANCH test → **expected deny** per row 9, but this is the
  plan's one unverified behavioral prediction. If it allows, do not force
  it; leave the header comment unedited (M3) and write the docstring to
  observed behavior.
- Full-path `git push` test → `deny`.
- `_lib_fragment_invokes_git` parametrization → every Accepts case exits
  0, every Rejects case exits nonzero.

## Out of scope

- **Dedicated timing tests for `symbolic-ref` (`:197`) and the
  default-branch candidate loop (`:200`).** Both funnel into the same
  downstream branch as the `CURRENT_BRANCH` test, so each would add a full
  serial ~5s run for a duplicate assertion. Row 9's trace shows the
  candidate loop recovering `DEFAULT_BRANCH` when `symbolic-ref` returns
  empty, which is the interesting half and is already exercised
  incidentally by the `CURRENT_BRANCH` test.
- **Per-consumer full-path `git push` tests in `deny-pii-in-commits.sh`,
  `deny-reviewer-tree-mutation.sh`, and `deny-private-project-refs.sh`.**
  The shared helper is pure string logic with no per-caller variation (row
  16), so the `test_lib.py` unit test covers all four callers by
  construction. Three more subprocess-level tests, each with its own repo
  fixture, would add cost without new information.
- **Changing the 5s cap or introducing `_lib_capped_for` in this hook**
  (G2). Repo-wide library policy cited by every hook.
- **Adding an exit-status check to the `gh pr view` call.** The existing
  empty-string test is the documented fail-open; a status check would flip
  it closed and is a defensive layer closing no gap.
- **Reworking `deny-network-installs.sh`'s local
  `_install_word_matches_name` or `require-worktree-for-git-writes.sh`'s
  scope boundary** to call the shared helper (row 18). Both carry explicit
  comments naming the non-reuse as deliberate; revisiting either is a
  separate decision.
- **A `pyproject.toml` change to run `timing` tests serially by default**
  (G1). Repo-wide collection config affecting every domain, and it would
  force a full-suite selection on this PR.

## Scope reconciliation (post-implementation)

M2's "two new tests, not four" and the Out-of-scope exclusion of dedicated
`symbolic-ref`/candidate-loop timing tests were both superseded during
`/code-review`.

- 5 `timing` tests shipped, not the 2 M2 named:
  `test_repo_root_git_timeout_allows`,
  `test_current_branch_git_timeout_arms_the_gate`,
  `test_default_branch_symbolic_ref_timeout_still_allows_via_candidate_loop`,
  `test_candidate_loop_exhausted_arms_the_gate`,
  `test_gh_pr_view_timeout_allows`.
- Out-of-scope's "duplicate assertion" premise held for only the
  candidate-loop-exhausted path, not the symbolic-ref-succeeds path: the
  latter produces `allow`, not `deny` — a distinction row 9's trace had not
  separated.
- M4 shipped `test_gh_pr_view_timeout_allows` and a `gh_timeout_shim`
  fixture in `conftest.py` — a regression test M4's text never scoped.
