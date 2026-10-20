# Pass PATH through to bash subprocesses in review-pr tests

## Context

Goal: make four pytest tests pass on macOS 26 by forwarding the inherited `PATH` into the bash subprocesses they spawn. The change is test-only.

Ask: "I'd like you to own the fix and run plan-it on it" (engineer, this session). Scope origin, relayed by a peer session and confirmed by the engineer via the label "Yes, I said it": "yes, separate branch that the peer is responsible for. for sure".

Why now: a `/ready-for-review` gate on another branch cannot record its `verification` marker while these four tests fail on the contributor's machine. They fail identically at the merge-base, and they pass on Ubuntu CI.

Failing tests:

1. `test_review_pr_checkout.py::TestProvenanceWrite::test_successful_checkout_then_marker_write_pins_mode_field_in_both_files`
2. `test_review_pr_finish.py::TestArtifactAndMarkerRemoval::test_removes_provenance_artifacts_and_marker`
3. `test_review_pr_finish.py::TestArtifactAndMarkerRemoval::test_marker_is_removed_when_run_from_inside_a_linked_worktree`
4. `test_review_pr_finish.py::TestInteriorFailureStillReachesExitZero::test_an_unremovable_file_does_not_stop_the_rest_of_the_cleanup[artifact]`

## Approach

Forward the inherited `PATH` into the HOME-isolated bash subprocesses whose asserted outcome depends on `sha256sum`. Use the repo's existing idiom, `{"HOME": str(home), "PATH": os.environ["PATH"]}`. It goes in two places:

- `_run` in `test_review_pr_finish.py`, where `PATH` is now always set and `path_prefix` is still prepended when given. This fixes failures 2-4.
- `marker_env` in `test_review_pr_checkout.py`, which fixes failure 1.

No production script changes. No new helper. No new comments.

The session's draft is right on the two failing sites. Two refinements from this pass:

- **`os.environ["PATH"]` instead of `.get('PATH', '')`.** Under the old form, an unset `PATH` became `PATH=""`, which is worse than bash's default. It also matches the 28 existing uses of the idiom (`git grep -n '"HOME": str([a-z_]*), "PATH"' -- claude`).
- **Delete the no-op `.pop("CLAUDE_CONFIG_DIR", None)` line at checkout `:835`.** It sits on the edited lines, and the dict is a literal with only `HOME`.

Alternatives considered and set aside:

- **Hardcoded `PATH`** (e.g. `"/usr/bin:/bin:/usr/sbin:/sbin"`, as `test_config_get.py:39` does with `/usr/bin:/bin`). `_run`'s `path_prefix` branch already inherits `os.environ["PATH"]`. A hardcoded list in the other branch would run one test's two invocations against different tool sets, e.g. `test_a_failed_removal_leaves_the_worktree_for_a_retry` at `test_review_pr_finish.py:310` (shimmed) and `:318` (unshimmed). Those would be different `git` binaries inside a single before/after comparison.
- **Prepend only `dirname(shutil.which("sha256sum"))`.** It exposes less, but it ties the helper to one tool by name, so the next `/sbin`-only tool hits the same wall. It also diverges from the repo idiom.
- **A shared conftest helper** (e.g. `_home_isolated_env(home)`). Rejected because the idiom is an uncommented two-key literal at ~20 existing sites (row 5). A helper for three new sites would make the codebase inconsistent rather than DRY. This falls under CLAUDE.md's named exception (3), "a small duplicated value beats a bad abstraction".
- **A `shasum -a 256` fallback in production.** See Out of scope.

**Root:** Four pytest tests fail on macOS 26 because they spawn bash with an env that has no `PATH`. Python resolves `bash` through `os.defpath` (`/bin:/usr/bin`) and so runs `/bin/bash` 3.2.57, whose compiled-in default `PATH` lacks `/sbin`. `sha256sum` cannot then be found.

**Accepted trade-off:** after the fix these two sites run whichever `bash` is first on the inherited `PATH` (Homebrew bash 5.3.20 on this machine), not `/bin/bash` 3.2.57. The `timeout` cap in `_lib_capped_for` (`hooks/_lib.sh:126-137`) is also enforced now, as on Ubuntu CI. Before, it ran uncapped. No assertion in the four target tests depends on either. `test_no_bash4_constructs.py` remains the guard for bash-3.2 compatibility. [verified: `staff-sdet` run in a scratch directory; not independently re-run by the session]

**Givens:**

- G1. macOS 26.6 ships `sha256sum` only at `/sbin/sha256sum`; `/usr/bin/sha256sum` is absent. Apple owns the OS layout. [verified: session check on this machine, per dispatch evidence]
- G2. `/bin/bash` 3.2.57 started with no `PATH` in its environment uses its compiled-in default, `/usr/gnu/bin:/usr/local/bin:/bin:/usr/bin:.`. The bash build fixes this value. Homebrew bash 5.3.20's own default includes `/sbin`, but `os.defpath` never selects it for these sites. [verified: `env -i HOME=/tmp bash -c 'echo $PATH'`, this session; interpreter versions verified by the `staff-sdet` run]

**Rows:**

1. All four failures have one cause: `sha256sum: command not found` from `marker.sh:236` or `hooks/_lib.sh:1170`. In the finish tests, `review-pr-finish.sh:86-90` then skips marker cleanup. [verified: session RCA, per dispatch evidence]
2. Exporting `/sbin` into pytest's own `PATH` does not help, because these call sites never forward `PATH`. [verified: session run with `PATH=/sbin:$PATH` failed the same 4]
3. Finish `_run` (`test_review_pr_finish.py:128-138`) builds the env for failures 2-4 at `:207`, `:224` and `:494`. It sets `PATH` only when `path_prefix` is given. [verified: read this pass]
4. Failure 1's `marker.sh write review-pr` call uses a HOME-only env (`test_review_pr_checkout.py:834-841`). The checkout-script run before it already inherits `PATH` through `_shimmed_env` (`conftest.py:179-184`), which is why only the marker step fails. [verified: read this pass]
5. `{"HOME": str(home), "PATH": os.environ["PATH"]}` is the repo's established idiom for a HOME-isolated subprocess env. It appears uncommented at `conftest.py:420`, which runs this same `review-pr-finish.sh`, and in many places in `hooks/tests/test_lib.py` (`:2009`, `:2153`, `:5200`, `:5385`, `:5472`, `:5526`, among others). [verified: grep this pass]
6. Finish `:176` cannot reach `sha256sum`: it exits at the argument check (`review-pr-finish.sh:32-35`) before `_lib.sh` is sourced. Finish `:527` (`HOME=""`) cannot either: it exits at the config-dir guard (`:73-76`) before `marker.sh` runs. [verified: `review-pr-finish.sh` read this pass]
7. `test_review_pr_findings_path.py:37-39` `_run` has the same HOME-only env, but no asserted outcome there depends on `sha256sum`, so it is excluded. Four of its five tests run `marker.sh resolve-session-id`. `marker.sh:236` runs at load time under `set -u` only, so the missing `sha256sum` leaves `_STAGED_DIFF_EMPTY_DIGEST` empty. The `resolve-session-id` arm (`marker.sh:926-928`) never reads that variable. The only effect is a "command not found" line on stderr that no assertion inspects. `TestUsageErrors` exits at `review-pr-findings-path.sh:21-24`, before `_lib.sh` is sourced. All five tests pass on this machine today. [verified: read by `plan-architect` consult; not run] [unverified: that the stderr line actually appears]
8. Always inheriting `PATH` changes no other caller's outcome on Ubuntu CI. Shim callers already inherited it (`test_review_pr_finish.py:135`), and Ubuntu's `/usr/bin/sha256sum` was already inside bash's default `PATH`. [unverified: confirmed only by the scoped run below and CI on push]
9. Two finish tests pass vacuously on macOS 26 today:
   - `TestSessionWideWorktreeSweep::test_leaves_other_sessions_artifacts_and_marker_untouched` (`:255-266`)
   - The `[marker]` case of `test_an_unremovable_file_does_not_stop_the_rest_of_the_cleanup` (`:477-502`)

   Both assert that a marker survives, and a skipped marker cleanup satisfies that trivially. After the fix, both exercise the real `rm -f` path for the first time on this machine. [verified: read this pass]
10. This session owns the fix and its plan. [engineer-verified: "I'd like you to own the fix and run plan-it on it"]
11. The fix ships on its own branch (`test-env-pass-through-path`), separate from the peer's work. [engineer-verified: "Yes, I said it" — the engineer's selected label, answering whether they said "yes, separate branch that the peer is responsible for. for sure"]
12. The fix shape is the session's own RCA proposal. A peer session accepted it as the fix to verify; the engineer has not approved it. Plan approval settles it. [unverified]
13. The findings-path file is excluded from this change. [engineer-verified: "Ask the architect" — the engineer's typed answer to whether to keep that file; the exclusion itself is the `plan-architect` consult's recommendation, which the engineer delegated to]

**Mechanisms:**

- **Finish `_run`.** Replace the env construction with `env = {"HOME": str(home), "PATH": os.environ["PATH"]}`. When `path_prefix` is given, set `env["PATH"] = f"{path_prefix}{os.pathsep}{env['PATH']}"`, the same shape as `test_review_pr_checkout.py:403`. Leave the docstring alone; it still accurately says "path_prefix, when given, is prepended to a real, inherited PATH". — anchors: row3, row5
- **Checkout `marker_env`.** Replace `:834-835` with `marker_env = {"HOME": str(isolated_home), "PATH": os.environ["PATH"]}` and delete the no-op pop. — anchors: row4, row5
- **Leave finish `:176` and `:527` and findings-path `_run` (`:38-39`) unchanged.** — anchors: row6, row7
- **No helper and no comment.** The idiom is uncommented at every existing site, so a comment here would be the odd one out. The regression guard is macOS-local: Ubuntu CI passes with a HOME-only env and cannot see this class of failure. — anchors: row5
- **Over-powered-primitive check.** Inheriting the whole `PATH` matches the existing precedent; it is no wider than what is already in place. Of the two lighter options above, the hardcoded `PATH` fails on row3, because it splits the tool sets inside one helper. The single-tool prepend fails on row5, because it ties the helper to one tool and breaks from the idiom. — anchors: row3, row5

**Dispatch split:** one `code-writer` dispatch. All three edits apply the same idiom, sit in `claude/.claude/scripts/tests/`, and share one background (rows 1-7) and one verification run. Splitting them would mean restating that background in every prompt.

## Critical files

All paths are relative to the worktree root.

- `claude/.claude/scripts/tests/test_review_pr_finish.py`: `_run` (`:128-138`) only.
- `claude/.claude/scripts/tests/test_review_pr_checkout.py`: `:834-835` only.

Reuse: the env literal from `claude/.claude/scripts/tests/conftest.py:420`, and the prefix-join shape from `test_review_pr_checkout.py:403`. Do not modify production scripts or `conftest.py`.

## Verification

Run everything from the worktree root. The worktree has no `.venv` of its own; use the main checkout's.

1. **Check the trigger first.** Run `env -i HOME=/tmp bash -c 'command -v sha256sum'`. It must print nothing and exit 1, while `command -v sha256sum` in the session shell resolves to `/sbin/sha256sum`. If the first command prints a path, this machine no longer reproduces the bug, and a pass after the edit proves nothing.
2. **Negative control, before editing, on the clean branch HEAD.** Run the four tests by node ID with the main checkout's `.venv/bin/pytest`. Expect 4 failed:
   - The checkout failure's message (`marker_result.stderr`) contains `sha256sum: command not found`.
   - The three finish failures are the marker-existence assertions at `:213`, `:226` and `:500`. Their stderr is not echoed, because `returncode` is 0 in every run.
3. **Positive check, after the edit.** Run the same command. Expect 4 passed.
4. **Mechanism control, after the edit.** Rerun step 3's command with pytest's own `PATH` set to the session `PATH` minus the one entry exactly equal to `/sbin`. Keep `/usr/sbin` and `/opt/homebrew/sbin`. Build that literal by hand from `echo "$PATH"` and prefix it as `env PATH=<literal> ...`, with no nested `$(...)`. First run `type -a sha256sum` and confirm the only copy is `/sbin/sha256sum`; a second copy later on `PATH` (for example coreutils gnubin, per README.md:103) makes the control a spurious disconfirmation. Expect the same 4 to fail again at the same assertions as step 2: checkout `:841` with `sha256sum: command not found` in the message, and finish `:213`, `:226` and `:500`. Four failures at any other location, for example in `_build_repo`, are inconclusive. Do not count them as confirmation. This shows the pass comes from forwarding `PATH`, not from an incidental change.
5. **Scoped suite.** Run `.venv/bin/python3 claude/.claude/scripts/select-tests.py` from the main checkout's `.venv` and expect all selected tests to pass. This run covers row 8 and the two tests in row 9 that now exercise the real removal path. The two row-9 tests have no before/after flip on this diff; they only need to stay green. Scratch mutants of `review-pr-finish.sh` show they become meaningful: a widened marker glob (`$REPO_HASH.*`) passes the first test without the fix and fails it at `:266` with the fix, and dropping `|| true` on the marker `rm` passes the `[marker]` case without the fix and fails it at `:496` with the fix. The mutants stay in scratch and are not part of this change.
6. **Lint.** Run `.venv/bin/ruff check claude/.claude/ claude-skills/`.
7. **Linux CI.** CI runs the full suite on Ubuntu on push. It passes before and after this change, so it is a Linux regression check only (row 8). It cannot demonstrate the macOS fix.

## Out of scope

- **A production `sha256sum` fallback** (`shasum -a 256`) in `marker.sh:236` or `hooks/_lib.sh:1170`. README.md:96,103 documents GNU `sha256sum` as a required tool, and a production change reaches every stow consumer. [unverified, worth raising separately] On macOS 26 the production hooks depend on Claude Code's inherited `PATH` containing `/sbin`. This pass did not check whether every stow consumer's launch environment includes it.
- **Echoing `result.stderr` in the finish marker-survival assertions** (`test_review_pr_finish.py:213`, `:226`, `:500`). The cause of these failures was in stderr the whole time, and the assertion message hides it. Adding it widens the deliverables past the four-test fix, so it is a separate follow-up for the engineer to decide.
- **A macOS CI runner.** Ubuntu CI cannot see this class of failure, but adding a runner is a CI-cost decision, not part of a test fix.
- **The other HOME-only env sites.**
  - `test_config_py.py` runs `config-get.sh`, which contains no hashing and no `marker.sh` call (grep this pass).
  - `test_config_get.py:39` already passes an explicit `PATH`.
  - Finish `:176` and `:527` cannot reach `sha256sum` (row 6).
  - `test_review_pr_findings_path.py` `_run` (`:38-39`). It reaches `marker.sh:236`, but no asserted outcome depends on `sha256sum` (row 7). Forwarding `PATH` there would change no test outcome, so it is a follow-up consistency edit, not part of this fix. The criterion for inclusion is "an asserted outcome depends on a tool outside bash's default `PATH`", not "can reach `sha256sum`".
