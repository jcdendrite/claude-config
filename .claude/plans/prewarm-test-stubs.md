# Pre-warm test stubs before the scaled timeout shim

## Context

Goal: stop cap-engagement hook tests from flaking on macOS because a freshly written stub script's first exec runs inside the scaled `timeout` shim's cap.

Ask: "I actually think you should try doing /plan-it on this." Scope, first selected: "Bash/sh stubs everywhere (Recommended)"; after an adversarial architect review, re-selected: "Widen to any route (Recommended)". The engineer chose the labels only. The descriptions are the session's own proposal: migrate every sh-family stub on PATH in any test that installs the scaled shim by any route (directly, via `_write_conditional_sleep_shim`, or via the `git_timeout_shim`/`gh_timeout_shim` fixtures), including shared stub-writing helpers and stubs that are the cap's target, with python-shebang stubs left out of scope for an exposure reason and the helper asserting the warm exec exits 0.

Problem: tests write fake `jq`/`tail`/`git`/`ps`/`head` executables into a temp dir on PATH and install a `timeout` shim that divides each production cap by `TIMEOUT_SCALE_DIVISOR` (3), so a 2s `_lib_capped_for` cap becomes about 0.667s (`claude/.claude/tests/helpers.py`, `write_scaled_timeout_shim`). The shim runs the real `timeout`, which then execs the stub, so the stub's first exec happens inside the timer. On one macOS host that first exec took about 1.4-2.3s, later execs 0.26s or less. A capped call that is not the one under test (e.g. the payload-parsing `jq` in `nudge-long-turn-subagent.sh`) can then be killed, and the test fails on an assertion about something else. The failing set varies per run (13-20 failures of ~15k tests across scoped runs). CI (Linux) passes.

Evidence gathered this session: with a throwaway pytest plugin that execs each freshly chmod-ed temp executable once, all 13 recorded failing tests passed serially, while the same 13 failed 3-6 at a time in three unpatched serial runs. The cause of the slow first exec is not identified.

Why now: scoped local runs on macOS are unreliable, so real regressions are hard to tell from noise.

Intended outcome: each stub that sits on PATH under the scaled shim is warmed once, side-effect free, at the point it is written; production caps and the divisor stay unchanged. A separate change (not this plan) makes `select-tests.py` honor the `timing` marker split CI uses.

## Approach

Add `write_prewarmed_stub(path, script)` to `claude/.claude/tests/helpers.py`. It writes an sh-family stub with a guard line after the shebang, makes it executable, and execs it once with only PATH and the guard's variable in its environment, so the slow first exec happens at write time and no body line runs. Every sh-family PATH stub that a scaled-shim test reaches is then written through it. Migration starts with the scaled shim itself and `_write_conditional_sleep_shim`. No dispatch starts until a timing probe on an affected macOS host shows that a guarded exec keeps the warm-up's effect. A paired merge-base-versus-branch run of the recorded failures on that host then decides whether the change passes.

**The helper** (M1-M3):
- Signature `write_prewarmed_stub(path: Path, script: str) -> Path`, placed in helpers.py's scaled-timeout section, above `write_scaled_timeout_shim`.
- It raises `ValueError` naming the problem, before it touches the filesystem, in two cases:
  - `path` is not absolute, since `subprocess.run` would resolve a bare name through PATH.
  - Line 1 of `script`, the text before its first `"\n"`, is outside the grammar below.
- The grammar accepts exactly `#!/usr/bin/env bash`, `#!/usr/bin/env sh`, `#!/bin/bash` and `#!/bin/sh`, with nothing else on the line. It rejects everything else, including:
  - a python shebang, no shebang, and an empty script;
  - a CRLF first line, `#! /bin/bash`, and `#!/bin/shx`;
  - any trailing text, such as the flag word in `#!/bin/bash -e`. No test stub has trailing text (row 36). Rejecting it means no flag can keep the guard from ending the warm exec, which carries no timeout (row 12).
- It removes any existing entry at `path` with `unlink(missing_ok=True)`, so a symlink there is replaced rather than written through.
- It writes line 1, then this guard as line 2, then the rest of `script` verbatim: `[ -z "${CLAUDE_CONFIG_TEST_PREWARM+set}" ] || { printf '%s' "$CLAUDE_CONFIG_TEST_PREWARM"; exit 0; }`. A script that is a shebang alone, with no trailing newline, gets the guard and nothing after it. The variable name is a module constant. The guard uses only shell builtins and POSIX expansion, so it parses under `/bin/sh` and no PATH stub can intercept it.
- It sets mode `0o755`, the mode `write_scaled_timeout_shim` (:3131) and `_write_conditional_sleep_shim` (`hooks/tests/conftest.py:262`) already set.
- It execs `[str(path)]` once through `subprocess.run`, with `env={"PATH": _PREWARM_EXEC_PATH, <variable>: str(path)}`, `stdin=subprocess.DEVNULL`, `capture_output=True`, `text=True`, `check=False` and no `timeout` argument. Nothing else enters that environment, so `BASH_ENV`, `ENV` and exported shell functions cannot run before the guard (row 35).
- `_PREWARM_EXEC_PATH` is a module constant that the helper reads at call time. helpers.py sets it once at import, before any test changes PATH: the absolute directory of the bash that `shutil.which("bash")` finds, then `os.defpath`. When that lookup finds nothing, it is `os.defpath` alone.
  - An `#!/usr/bin/env bash` stub therefore warms under the bash its test runs (row 33).
  - A closed or empty PATH that a test sets cannot hide `bash` from the warm exec, because the constant never reads the test's PATH (rows 34, 35).
  - `os.defpath` alone fails the first point, and the ambient PATH at write time fails the second.
- It raises `AssertionError` unless that exec exited 0 and printed exactly the path on stdout. The message carries the path, the exec's PATH, the return code, stdout and stderr, as `path=<path>`, `PATH=<value>`, `returncode=<n>`, `stdout=<repr>` and `stderr=<repr>`. stderr is not required to be empty, because an interpreter warning writes there without saying anything about the guard. It returns `path`.
- Docstring facts, one sentence each:
  - A newly written executable's first exec can outlast a scaled cap, so the helper pays it at write time.
  - The guard ends that exec before any body line runs.
  - The exec's environment holds only PATH and the guard's variable, so no shell startup file or exported function runs first.
  - The exec's PATH resolves `bash` the way `link_bash_into` does, so an `env bash` stub warms under the interpreter its test runs.
  - An existing entry at `path` is removed first, because writing through a symlink would rewrite its target.

**Where it lands** (M4, M5):
- `write_scaled_timeout_shim` writes its own shim through the helper. Its own unlink and the comment above it (:3091-3094) go, because the helper now removes the entry. Its docstring gains one sentence: "Write each sh-family stub a capped call can reach with write_prewarmed_stub, since a newly written executable's first exec can outlast a scaled cap."
- One comment line above `TIMEOUT_SCALE_DIVISOR = 3` (:3066): "The scaled cap bounds every capped call a hook makes under this shim, not only the call a test stalls."
- `_write_conditional_sleep_shim`, the shared sh-family writers in helpers.py, the two conftests and `test_python_floor.py`, then each candidate file's own sites, all by the rule below. `assert_cap_engaged` and `assert_cap_not_engaged` do not change.

**Migration rule** (M5). A write site is in scope when all four hold:
1. The text it writes, however the test builds it (a literal, an f-string or a joined value), has a first line in the helper's grammar. A `shutil.copy2` or `copyfile` of a tracked file is not a site: the test authored none of that text, and a guard would change the system under test.
2. It makes that file executable.
3. The file's directory goes on a PATH the code under test runs with: an `env` PATH value, `monkeypatch.setenv("PATH", ...)`, or a returned PATH string.
4. At least one test that runs the site installs the scaled shim.
   - Evaluate this per executing test: an inline site against its own test, and a helper or fixture against each test that reaches it, directly or through another fixture.
   - A test installs the shim when it calls `write_scaled_timeout_shim`, or calls `_write_conditional_sleep_shim` or a `git_timeout_shim`/`gh_timeout_shim` `install` with no `exit_status` and a positive `sleep_seconds` (row 7).

An in-scope site becomes one `write_prewarmed_stub(path, script)` call in place of its `write_text` and `chmod`. Five further rules apply:
- A later rewrite of the same stub goes through the helper too.
- A helper or fixture that is in scope for any caller test is migrated whole, so its other callers are warmed as well.
- A site whose file text a test reads back, by hash, diff or line position, stays as it is and is reported with that reason. The guard adds a line 2, which shifts every later line by one. The plan review's Grep found no sh-stub assertion that depends on a stub's line numbers (row 38).
- A site where condition 3 or 4 cannot be settled by reading the file stays as it is. The dispatch reports it to the session as unsettled, with what it could not settle.
- A stub that `write_scaled_timeout_shim` replaces before anything execs it is not a site (row 8).

The rule keys on whether a test installs the shim, which a reader checks in the test file. It does not key on which hook calls are capped: that audit was never done, and its answer changes as hooks change (row 22).

**Order and packaging** (M6):
1. Verification 1's fresh-exec probe runs on the affected host before any dispatch. It tests whether a guarded exec warms a `#!/bin/bash` stub and an `#!/usr/bin/env bash` stub (row 3), and its stop rules can end the plan before dispatch A. The session then makes the merge-base worktree (Verification).
2. Dispatch A lands the helper, its tests and every shared writer as one commit. The session then runs A's derived checks (Verification 3).
3. Dispatches B1, B2 and B3 migrate the candidate files' own sites, one at a time, one commit each. Critical files names each dispatch's files and checks.
4. After B3, the session runs Verifications 5-7. A failure the triage rule blocks on, or any branch-arm failure in Verification 7, goes to the engineer with the session's diagnosis.
5. A, B1, B2, B3 and B4 ship as separate commits in one PR (rows 43, 48 and 51). Before merge, backing the change out reverts B4, B3, B2, B1, then A, because every B file imports the helper. If main squash-merges the PR, backing it out after merge is one revert of that commit. Each revert is plain, since the change is test code and leaves no state behind.

**Alternatives set aside:**
- **Warm `$1` inside the scaled shim before its timer starts.** Every argument reaches the stub body, so no warm call is free of side effects (row 4). It warms only the shim's direct child, not a stub the wrapped command execs. It also gives the shim a second job, and its logs are the suite's evidence that a cap fired (helpers.py:3057-3061). anchors: row2, row4.
- **A per-interpreter "harmless" argument such as `--version`.** The stub body decides what any argument does (row 4). anchors: row4.
- **A bare warm exec with `BASH_ENV` pointing at an `exit 0` file.** A `#!/bin/sh` stub does not read `BASH_ENV` (row 23), and it still needs a warm exec at every write site, so it saves nothing over the guard. anchors: row5, row23.
- **Committing the throwaway plugin's global `chmod` interception.** A bare exec runs the row 4 side effects. It also fires on production scripts that tests copy into temp directories, where no guard line can go. anchors: row4.
- **A conftest hook that intercepts `chmod` and rewrites each sh-family temp file through the helper.** It would need no per-site migration. But it rewrites every sh-family temp file any test chmods, whether or not that file goes on a PATH under the shim, and the guard line changes text a test may hash, diff or read by line position (M5). anchors: row4.
- **Warming at `assert_cap_engaged` entry, or warming siblings from inside `write_scaled_timeout_shim`.** Neither knows a test's stub paths, stubs are often written after the shim, and some scaled tests never enter `assert_cap_engaged` (row 11). anchors: row11.
- **Lowering the divisor or raising production caps.** That loosens every cap-boundary test's bound, and the measured 2.3s first exec exceeds even an unscaled 2s cap (row 1). anchors: row1, row2.
- **One pre-warmed dispatcher script with per-test symlinks, or a content-keyed cache of warm stubs.** Both rest on the untested premise that an exec through a symlink to a warm file is itself warm, and both reshape every stub. anchors: row1, row37.

The helper is the lightest primitive here: one extra exec per stub write, at the site that already writes the stub.

### Assumption ledger

**Root:** A freshly written sh-family stub's first exec can outlast the scaled cap it runs under, so an affected macOS host kills a capped call the test is not about (Context, Problem). This change warms each in-scope stub when it is written, with no change to the shim's scaling or to any production cap. [verified for the mechanism: `helpers.py:3108-3121`; unverified for the timing: row 1]

**Givens:**
- **G1.** The slow first exec is a property of the host's OS or security software, and its cause is unidentified (candidates: endpoint-security software, OS assessment of new executables). Reason: that software lies outside the repository, and this repo's tests must pass for every contributor without a host change. The design depends only on row 1's first-exec/later-exec asymmetry, not on the cause. [unverified]
- **G2.** CI runs on Linux and passes, so only a local run on an affected macOS host can show the fix works. Reason: GitHub owns the runner image. [unverified: Context, Problem paragraph]
- **G3.** Agents run `select-tests.py` rather than the full suite, unless it selects the full suite itself. Reason: the repo `CLAUDE.md` § "Commands" sets this, and changing a repo-wide contributor workflow is a decision outside this plan. [verified: `CLAUDE.md` "Commands"]

**Rows:**
1. [unverified] On one macOS host, a fresh stub's first exec took about 1.4-2.3s and later execs about 0.26s or less. A throwaway plugin that exec'd each freshly chmod-ed temp executable once made all 13 recorded failures pass serially, while 3-6 of the 10 in-shim IDs failed in each of three unpatched serial runs. These are the session's measurements, relayed in Context and not re-run by this dispatch. Verification 1 re-times first and later execs, and Verification 7 re-runs row 31's 10 in-shim IDs at the merge-base and on the branch.
2. [verified: `helpers.py:3066`, `:3108-3121`; `test_scaled_timeout_shim.py:100`] The shim divides a 1-9-leading integer duration by `TIMEOUT_SCALE_DIVISOR` (3) and runs the real `timeout` with the wrapped command, so the wrapped command's exec, a stub's first exec included, falls inside the scaled duration. A 2s cap becomes 0.666s.
3. [unverified] A guarded exec that exits at line 2 warms a stub as well as the bare exec did. If the cost sat in something only a body line triggers, it would not. Verification 1's guarded arms measure this before dispatch A, for `#!/bin/bash` and `#!/usr/bin/env bash` stubs, each against an unguarded control of its own shebang.
4. [verified: `test_marker_script.py:6199-6211` and `:6247-6256`; `hooks/tests/conftest.py:356-368`; `helpers.py:1610-1613` with `test_resume_context_stub.py:22-24`] Stubs carry side effects a bare warm exec would trigger: a call counter that decides when the stub stalls, an invocation log a test reads, `mkdir` call-number claims, and an invocation log a test asserts is empty after install.
5. [verified: `test_python_floor.py:81-85`, reached by `test_check_python_floor.py:310-311`; `test_branch_divergence_status.py:704`; `test_marker_lib.py:1314`; `helpers.py:1611`] `#!/bin/sh` stubs are in scope, so the guard uses POSIX syntax only.
6. [unverified] `${name+word}` expands to `word` when `name` is set, even to an empty value, and to nothing when it is unset, in bash and in POSIX sh; `[`, `printf` and `exit` are builtins in both. Not checked against the POSIX text in this dispatch. Verification 2 runs the guard under `#!/bin/sh` and `#!/bin/bash`, and CI runs it under the Linux runner's `/bin/sh`.
7. [verified: `hooks/tests/conftest.py:240-249`, `:320-326`, `:447-449`; `test_advance_past_commit_stall.py:704`] `git_timeout_shim` and `gh_timeout_shim` install through `_write_conditional_sleep_shim`. It installs the scaled shim only when `exit_status` is unset and `sleep_seconds > 0`, so a fixture call with `exit_status` installs none.
8. [verified: Grep for the four route names under `claude/.claude/`; `test_lib_capped_for.py:270`; `test_lib_credentials.py:535-536`; `test_check_python_floor.py:104`, `:306-316`; `helpers.py:3094`] 37 test files name a route at the plan's base, besides `helpers.py` and the hooks conftest. Main has since added two more, which B4's `write_shim` serves. Eight hold no candidate site:
   - `test_lib_capped_for.py` and `test_lib_credentials.py` name a route only in a comment.
   - `test_advance_past_commit_stall.py` uses only the `exit_status` arm.
   - `test_deny_default_branch_push.py`, `test_check_claude_md_length.py`, and scripts `test_ci_watch.py` and `test_review_pr_lib.py` hold no sh-family shebang literal.
   - `test_check_python_floor.py`'s one literal (:104) is a `timeout` stand-in that `write_scaled_timeout_shim` unlinks and replaces in that file's one scaled test.

   The other 29 are the B1-B3 candidates.
9. [verified: `helpers.py:1603-1615`, `:2802-2822`, `:2840-2870`, `:2889-2893`, `:2901-2922`, `:2969-2976`, `:3023-3047`, `:3071-3132`, python at `:3401-3417` and `:3420-3610`; `scripts/tests/conftest.py:144-268` (python), `:527-555`, `:558-587`, `:596-620`, `:697-700` (a git hook), `:1746-1774`, from `:1890`; one Grep for all the helpers.py stub-writer names together] Shared sh-family stub writers live in helpers.py, both conftests and `test_python_floor.py`. That Grep, which also matched the two python writers' names, found callers in 11 route-naming files. Which writer each caller uses, and whether a scaled test reaches it, was not checked per writer, so dispatch A applies M5 to each one.
10. [verified: `helpers.py:375`, `:392`, `:496-502`, `:521-545`, `:566-589`; `hooks/tests/conftest.py:455-460`; `scripts/tests/conftest.py:1625-1627`] Both test trees wrap `subprocess.run` in `check_shell_exec`. It raises for an `#!/usr/bin/env bash` target in three cases:
    - it runs with an env that lacks PATH;
    - it exits 126 or 127 with uncaptured stderr;
    - its captured stderr is `env`'s missing-bash error.

    The warm exec passes PATH and captures output, so only a host where neither the session's PATH nor `os.defpath` holds `bash` trips it. `ShellExecGuardError` subclasses `AssertionError`, so the helper's contract holds in those trees.
11. [verified: `helpers.py:3189-3194`; `test_marker_script.py:6243-6258`] `assert_cap_engaged` receives only the shim's `bin_dir`, and at least one scaled test never enters it.
12. [verified: `requirements-dev.txt` has no `timeout` match; global `CLAUDE.md` "Ground every choice"] No pytest timeout plugin is declared, and a timeout literal on the warm exec would need a primary-source citation. Under each of the four accepted shebangs the guard ends the exec at line 2, so the call carries none.
13. [verified: `select-tests.py:480-484`, `:984-986`] `helpers.py` is a global trigger, so `select-tests.py` runs the full suite for this diff, `CLAUDE.md` "Commands" case 1.
14. [verified: `select-tests.py:1142`, `:1264`, `:1321`; `.github/workflows/tests.yml:179`, `:186`; `pyproject.toml:31`] `select-tests.py` passes its own arguments to pytest after the targets, so `-m "not timing"` and `-m timing -n0` reproduce CI's two passes. The `timing` marker exists for serial isolation.
15. [verified: `docs/design-decisions/code-file-line-limit.md:19`, `:50-57`; Grep found no test citing that doc] `helpers.py` is already past the 1,000-line limit, and no check enforces the limit yet, so the added lines fail nothing.
16. [verified: `claude/.claude/tests/test_resume_context_stub.py:1`, `:9`] Tests of a helpers.py stub writer already live in `claude/.claude/tests/` and import from `helpers`.
17. [verified: case-insensitive Grep for `prewarm|pre-warm|warm_stub|_warm\b` under `claude/.claude/`, no match; Grep for `def _write_executable`] No warm-up helper exists to reuse. Four local `_write_executable` copies exist (`test_shell_exec_guard.py:50`, `test_plan_push_preflight.py:273`, `test_git_floor.py:70`, `test_venv_run.py:71`), none in helpers.py.
18. [engineer-verified: "Bash/sh stubs everywhere (Recommended)"] Scope question 1: the change covers bash and sh stubs.
19. [engineer-verified: "Widen to any route (Recommended)"] Scope question 2: the scope widens to any route by which a test installs the scaled shim.
20. [unverified] The boundary detail behind rows 18-19 is the session's own option text, not the engineer's words (Context, `Ask:` line). M5 puts it into operational terms.
21. [unverified] Each python-shebang stub under the shim is the cap's target or sits under a large cap: `reconcile-project-plugins.sh`'s 30s `claude` call, scaled to 10s; the `gh pr list` shim in `test_review_pr_lib.py`; `test_ci_watch.py`'s direnv stall shim. This is an earlier architect review's claim, not re-read here, and that review did not run `scripts/tests`.
22. [unverified] No one has audited which hand-rolled stubs sit under a scaled cap beside a non-target capped call. The survey's per-file counts (about 40 hand-rolled stubs) are unverified.
23. [unverified] bash invoked as `sh` and dash do not read `BASH_ENV`. Not checked against bash(1) in this dispatch. Only an alternative that was set aside rests on it.
24. [verified: Read of `test_marker_script.py:6185-6259`] `test_marker_script.py`, a B1 file, runs past line 6,258.
25. [verified: Grep of `^from \.(test_|_)\w+ import` across test files; `test_scaled_timeout_shim.py:39`, `:288-304`] No B1-B3 file imports a stub writer from another B1-B3 file, so every writer shared across files is in dispatch A, except `write_shim`, which main added after the plan's base and which B4 migrates. The Grep misses `from . import test_lib, test_lib_capped_for` at `test_scaled_timeout_shim.py:39`. That B2 file reads test functions' source from `test_lib.py`, a B1 file, and fails when one names a scaling identifier, so B1's checks run it.
26. [engineer-verified: "Keep both (Recommended)"] Asked whether to keep the one-line comment above `TIMEOUT_SCALE_DIVISOR` and the one docstring sentence on `write_scaled_timeout_shim`, neither of which the Ask calls for, the engineer kept both. M4 carries them.
27. [engineer-verified: "Separate change (Recommended)"] Asked whether the `select-tests.py` change (run CI's two pytest passes by default) stays out of this PR, the engineer chose a separate change. The plan's earlier statement that the engineer had said so was the session's own proposal.
28. [engineer-verified: "Scaled-shim tests only (Recommended)"] Asked whether row 18's "everywhere" meant every bash/sh stub or only those within scaled-shim tests, the engineer selected this label. Stubs in tests that never install the scaled shim stay out of scope.
29. [unverified] That option's description was the session's text, not the engineer's.
30. [engineer-verified: "I think you need to make sure to run a test on the Mac to ensure the flake doesn’t happen"] Asked what the plan should do about a regression check for new unwarmed stubs, the engineer typed this instead of choosing an option. The plan must include a real macOS test run that shows the flake does not happen, which Verification 7 is. The quote does not choose between deferring and adding the tripwire.
31. [verified: the session's recorded list of 13 failing node IDs; Read of each test and its stub site; Grep of `test_lib_gh.py` and `test_lib_worktree_collision_guard.py` for the four route names, no match] Ten of the 13 recorded failures are in tests that install the scaled shim, and three are not. Node IDs are under `claude/.claude/hooks/tests/`. Each entry gives the stub site, the M5 verdict and the dispatch. Other stubs that the tests' fixtures write were not traced.
   - `test_deny_pii_in_commits.py::TestDenyPiiInCommits::test_work_tree_check_sigterm_immune_denied`: `git_timeout_shim` (:489-503); in; A.
   - `test_check_skill_length.py::TestCheckSkillLength::test_head_timeout_false_denies_shrinking_file`: `git_timeout_shim` (:1102-1131); in; A.
   - `test_marker_lib.py::TestLibAdvanceOffsetPastCompleteLines::test_slow_path_tail_timeout_returns_offset_unchanged`: inline `tail` (:1859-1867); in; B1.
   - `test_lib_gh.py::TestLibGh::test_propagates_gh_own_nonzero_exit_status`: python `gh` shim from `_lib_sh_helpers.py:41-70`, under `_lib_gh 5`'s unscaled cap (`_lib.sh:160-163`); out, a python stub in a test with no scaled shim; none.
   - `test_nudge_long_turn_subagent.py::TestNudgeLongTurnSubagent::test_scan_lock_release_point_matches_the_read_scan_write_scope`: inline `jq` (:505-513); in; B3.
   - `test_lib_worktree_collision_guard.py::TestWorktreeLockIsMine::test_same_session_other_pid_live_registered_but_ps_fails_is_mine`: `#!/bin/bash` `ps` stub from `_install_failing_ps` (:204-217), under `_lib_capped`'s unscaled 5s cap (`_lib.sh:50-51`, `:3704`); out, no scaled shim; none.
   - `test_lib_worktree_collision_guard.py::TestRegisteredLiveSid::test_ps_failure_for_live_registered_pid_returns_1`: the same `ps` stub; out, no scaled shim; none.
   - `test_nudge_long_turn_subagent.py::TestNudgeLongTurnSubagent::test_jq_count_stage_timeout_leaves_no_output_and_offset_unchanged`: inline `jq` (:867-875); in; B3.
   - `test_marker_script.py::TestMarkerScriptStatusCompletionMarkers::test_code_review_value_computation_times_out_gracefully`: inline `git` (:3482-3490); in; B1.
   - `test_nudge_long_turn_subagent.py::TestNudgeLongTurnSubagent::test_bounded_window_avoids_jq_timeout_that_an_unbounded_read_would_hit`: inline `jq` (from :965); in; B3.
   - `test_require_ready_for_review.py::TestRequireReadyForReview::test_current_head_git_timeout_denies`: `git_timeout_shim` (A) and the `fake_gh_pr_exists` fixture's `#!/bin/bash` `gh` (:38-49, B2); in.
   - `test_require_architect_consult.py::TestRequireArchitectConsult::test_subagent_type_hung_jq_allows_within_timeout`: inline `jq` (from :300); in; B2.
   - `test_require_ready_for_review.py::TestRequireReadyForReview::test_current_branch_git_timeout_arms_the_gate`: the same two sites as `test_current_head_git_timeout_denies`; in; A and B2.
32. [unverified] The three out-of-scope failures run under an unscaled 5s cap, which row 1's 1.4-2.3s first exec does not explain. Either a first exec sometimes takes over 5s, or those failures are not cap kills. The warm-up plugin made them pass, so a first-exec effect is implicated either way. Row 44 undercuts that last inference, since the three also pass in serial runs without the plugin.
33. [verified: `helpers.py:396-414`; `README.md:106`] `link_bash_into` links into a closed PATH the bash that `shutil.which("bash")` finds on the caller's PATH (:404). README.md has a macOS contributor put Homebrew's bash 5.2 or newer ahead of `/bin`, because `/bin/bash` there is 3.2. A test's `#!/usr/bin/env bash` stub therefore runs under that bash, and a warm exec under `os.defpath` alone would run `/bin/bash` instead.
34. [verified: CPython 3.14 `posixpath.py:21`, `os.py:104`] `os.defpath` is `/bin:/usr/bin` on POSIX. It follows the session bash's directory in the warm exec's PATH, and stands alone when the session's PATH holds no `bash`. Only CI shows that the warm exec resolves `bash` on the Linux runner (Verification 8).
35. [verified: `test_transcript_gh_cli.py:1730`; `test_shell_lib_function_liveness.py:139`; `test_record_session_config_keys.py:278-286`; `test_bash_floor.py:84`] Existing tests already reduce a child's environment to PATH, or drop `BASH_ENV` and `ENV`, so that a shell startup file or an exported function cannot reach the child.
36. [verified: Grep of the test trees for an sh-family shebang followed by a space, whose only match is `test_shell_dialect.py:140`'s own fixture] No test stub uses a shebang with trailing text today, so the grammar rejects no current site.
37. [unverified] Whether an exec through a fresh symlink to an already-warm binary is slow on the affected host is untested. Closed-PATH tests build such links into directories that hold the scaled shim. This plan does not probe it, because a slow link would widen the fix rather than invalidate it. One a scaled test reaches would surface as a failure that Verification 6 has the session check by hand.
38. [unverified] No sh-stub assertion depends on a stub's line numbers. The plan review reported a Grep for `LINENO`, `BASH_LINENO` and `line N:` stub output across the three test trees, and this dispatch did not re-run it.
39. [verified: `_pytest/junitxml.py:647-663` and `_pytest/config/__init__.py:100-111`, in the shared `.venv`'s pytest] pytest's JUnit `<testsuite>` element carries `tests`, `skipped`, `errors`, `failures` and `time`, and its `tests` count includes skipped and errored tests. pytest exits 0 when every test passed and 1 when some failed. Codes 2-5 mean an interruption, an internal error, a usage error or no tests collected.
40. [unverified] Row 47's "guard" is the suite-wide tripwire that M7 and Out of scope name, not the line-2 guard M2 adds. That reading is the session's, from the question row 47 answers, not the engineer's words.
41. [unverified] The warm exec runs when a test writes a stub, outside any cap's timer, so this change cannot make an intermittent timing failure more likely than the merge-base does. Each B dispatch's report checks this for every site it migrates in a `timing`-marked test. Nothing checks a shared writer that a `timing`-marked test calls inside its measured region. The triage rule's intermittent arm rests on this row.
42. [engineer-verified: "Investigate them first"] Asked what to do about the three recorded failures in tests that never install the scaled shim (rows 31, 32), the engineer selected this label. Row 44 records the investigation.
43. [engineer-verified: "Proceed anyway"] Asked by the session "This plan projects about 470 changed lines (an estimate; threshold 400). Split into smaller PRs now, or proceed?", the engineer selected this label. The change ships as one PR (M6).
44. [verified: the session's unpatched `-n 0` reruns of the three IDs on the affected macOS host, relayed to this dispatch and not re-run by it] The three out-of-scope IDs do not reproduce as first-exec cap kills. They fit load-induced timeouts in a heavily loaded parallel run. They lie outside the helper's reach. The evidence, read with row 32's unscaled 5s caps:
    - Run serially and unpatched three times in a row, they passed every time, in 3.11s, 1.66s and 2.22s.
    - They also passed in an unpatched serial rerun of the 13 and in the warm-up plugin's runs.
    - They failed only in one full `-n auto` run of `claude/.claude/hooks/tests/`, which took about 75 minutes under very high machine load.

    This conclusion is the session's, not the engineer's.
45. [engineer-verified: "Simplify to paired pass rates (Recommended)"] Asked how the next revision should treat the verification machinery (the recorder plugin, differential classifier, warm plugin and counted pairs, which the round-2 plan review found generating its own defects), the engineer selected this label.
46. [unverified] That option's description was the session's own proposal, not the engineer's words. It proposed:
    - one macOS check in place of the recorder, the classifier, the warm plugin, the counted pairs and the separate default-run step;
    - running the 10 in-shim recorded failures serially at the merge-base and on the branch, back to back, repeated a few times;
    - discarding a window whose merge-base arm fails no test, and requiring the branch arm to pass all;
    - keeping the full-suite CI-style passes, with the failure count in the PR body.

    Verification 7 puts it into operational terms and holds its counting rule. Its window thresholds, per-arm flags, arm alternation and priming run come from a later architect review, relayed by the session, not from the engineer. Verification 1's guarded arms are a plan-architect addition, so that row 3 is settled before any dispatch.
47. [engineer-verified: "I think the guard is worthwhile for a follow up PR but leave it out of this one"] The engineer typed this answer to the plan's open decision on a tripwire. It covers only this: the standing guard against new unwarmed stubs belongs to a follow-up PR and stays out of this one.
48. [engineer-verified: "Proceed anyway"] The projected diff rose to about 510 lines (Critical files) after row 43's answer, which named about 470. The session re-asked "This plan projects about 510 changed lines (an estimate; threshold 400). Split into smaller PRs now, or proceed?", and the engineer selected this label. The change ships as one PR (M6).
49. [unverified] If the branch arm behaved exactly like the merge-base arm and windows were independent, Verification 7 would pass about 1% of the time at most, whatever the merge-base arm's per-window pass rate. A plan-architect hand computation gives this, not a run:
    - For each N from 5 to 12, take the chance that the fifth counted window is window N, times the chance that all N branch arms pass.
    - Sum over N, then maximize over the merge-base arm's per-window pass rate. The sum peaks near a rate of 0.72, at about 0.0099.
50. [engineer-verified: "Keep four arms (Recommended)"] Asked whether Verification 1 stays at four arms (a `#!/bin/bash` pair and an `#!/usr/bin/env bash` pair, each with a same-shebang control) or shrinks, the engineer selected this label. Verification 1 stays as written.
51. [engineer-verified: "Fix it in this PR (Recommended)"] The cumulative review after the rebase found `write_shim` in `claude/.claude/scripts/tests/_branch_records_helpers.py`, which the plan's Critical files did not name. The session asked whether to fix it in this PR or leave it as a follow-up, and the engineer selected this label. The fix is dispatch B4.

**Mechanisms:**
- **M1. `write_prewarmed_stub` in helpers.py: an absolute path, the old entry removed, one exec per write whose environment holds only the guard's variable and a PATH that resolves the session's bash, and no timeout.** anchors: root, row1, row2, row10, row12, row15, row16, row17, row33, row34, row35.
- **M2. The POSIX guard on line 2, and a warm exec that must exit 0 and print exactly the path.** anchors: row3, row4, row5, row6.
- **M3. `ValueError` for any first line other than the four sh-family shebangs, before anything is written.** anchors: row5, row12, row18, row20, row21, row36.
- **M4. `write_scaled_timeout_shim` warms its own shim, with the docstring sentence and the divisor comment.** anchors: row2, row7, row11, row26.
- **M5. The four-condition migration rule, evaluated per executing test, with shared writers migrated whole and unsettled sites reported.** anchors: row7, row8, row9, row19, row20, row22, row28, row29, row38.
- **M6. The fresh-exec probe, then dispatch A and its derived checks, then B1-B3 one at a time, then the full suite and the paired macOS check, as separate commits in one PR, with failures triaged against a pinned merge-base.** anchors: row1, row3, row14, row24, row25, row30, row31, row39, row41, row43, row44, row45, row46, row48, row49.
- **M7. No change to `assert_cap_engaged` or `assert_cap_not_engaged`, and no suite-wide tripwire, which row 47 leaves to a follow-up PR.** anchors: row11, row22, row40, row47.

## Critical files

Paths are repo-relative. Line numbers are at the plan's base.

**Dispatch A** (`code-writer`, first, one commit):
- `claude/.claude/tests/helpers.py`:
  - M1-M4, including the removal of `write_scaled_timeout_shim`'s own unlink and its comment (:3091-3094).
  - When M5 holds for a caller test, route each of these writers through the helper:
    - `install_resume_context_stub` (:1603-1615)
    - `_write_arg_matching_exit_shim` (:2802-2821)
    - `_make_git_blocking_on_arg` (:2840-2870)
    - `path_with_failing_tool_shim`'s no-pattern arm (:2890-2893)
    - `_make_git_recording_argv` (:2901-2922)
    - `build_path_with_silent_python3` (:2969-2976)
    - `_forced_fallback_path_env` (:3033-3047)
  - `_write_lingering_git_wrapper` (:3401) and `make_reconciler_claude_shim` (:3420) write python stubs and stay as they are.
- `claude/.claude/tests/test_prewarmed_stub.py` (new). Its cases:
  - Parametrized over the four accepted shebangs, for a stub whose body appends to a log:
    - A spy that wraps `subprocess.run` in `helpers` records exactly one call, with argv `[str(path)]` and env `{"PATH": helpers._PREWARM_EXEC_PATH, <variable>: str(path)}`. A spy fits here because that one call is the behavior under test.
    - For `#!/usr/bin/env bash`, `shutil.which("bash", path=<the recorded PATH>)` and `shutil.which("bash")` return equal path strings, so the warm exec ran the test's bash (row 33). The comparison is on the strings, because file identity would also pass a PATH built from `os.defpath` alone on any host where `/bin/bash` and the ambient bash are one file.
    - After the helper returns, the file is executable and the log does not exist.
    - Running the stub plainly then appends one line.
  - `ValueError`, with nothing created at the path, for each of:
    - an interpreter outside the grammar: `#!/usr/bin/env python3`, `#!/bin/shx`, `#!/bin/bashx`, `#!/usr/bin/env bashx`;
    - no shebang, and an empty script;
    - a malformed line 1: `#! /bin/bash`, and `#!/bin/bash` followed by `\r\n`;
    - trailing text: `#!/bin/bash -e`;
    - a relative path.
  - With a file of known bytes already at the path, a `#!/usr/bin/env python3` script raises `ValueError` and leaves those bytes unchanged.
  - A script that is `#!/bin/sh` alone, with no trailing newline, is accepted, and its file ends after the guard line.
  - With `BASH_ENV` and `ENV` monkeypatched to a file that prints a line and creates a sentinel, the helper succeeds for `#!/bin/bash` and `#!/usr/bin/env bash`, and the sentinel does not exist. It has no `#!/bin/sh` leg, because a non-interactive sh reads neither variable (row 23), so that leg could not fail.
  - With PATH monkeypatched to an empty directory, the helper succeeds for an `#!/usr/bin/env bash` stub.
  - In a directory whose name holds a space, a `$` and a backtick, the helper succeeds and a plain run appends one line.
  - With a symlink at the path pointing at a sentinel file, the sentinel's bytes are unchanged afterwards and the path is a regular file.
  - `AssertionError` on each failure branch, with the message holding that case's `path=`, `PATH=`, `returncode=`, `stdout=` and `stderr=` values:
    - Nonzero exit: with `helpers._PREWARM_EXEC_PATH` monkeypatched to an empty directory, an `#!/usr/bin/env bash` stub's warm exec exits 127, because `env` finds no `bash`.
    - Wrong stdout: with `subprocess.run` replaced by a stand-in that returns exit 0, stdout `unexpected` and stderr `diagnostic`, the helper raises. A stand-in fits here because no accepted shebang makes the guarded exec exit 0 with other stdout.
  - Under `install_shell_exec_guard(monkeypatch)` (`helpers.py:566`), the `#!/usr/bin/env bash` success case still succeeds, and the nonzero-exit case still raises `AssertionError`. The hooks and scripts trees install that guard on every test (row 10), and `claude/.claude/tests/` does not.
- `claude/.claude/hooks/tests/test_scaled_timeout_shim.py`: one new case, parametrized over `write_scaled_timeout_shim` and `_write_conditional_sleep_shim` with a positive `sleep_seconds`.
  - A spy that wraps `subprocess.run` in `helpers` records exactly one call per file the writer wrote, each with argv `[str(path)]` and the guard's variable set to that path, and no other call. Those files are `timeout` for the first writer, and the fake binary and `timeout` for the second, which installs the scaled shim itself (`hooks/tests/conftest.py:243`).
  - It skips when neither `timeout` nor `gtimeout` is on PATH, because `write_scaled_timeout_shim` then writes nothing (`helpers.py:3083-3085`). It therefore runs on Linux.
  - It lives here rather than in `test_prewarmed_stub.py` because the hooks conftest is not importable from `claude/.claude/tests/` (`helpers.py:2462-2464`), and four hooks test files already import this writer from it.
  - No unit test covers the other A writers' wiring, which only Verification 7 on the macOS host exercises, and only for writers the 10 IDs' tests reach.
- `claude/.claude/hooks/tests/conftest.py`:
  - `_write_conditional_sleep_shim` (:253-262) in every case.
  - When M5 holds for a caller test: `sed_call_counting_shim` (:356-368) and `sed_split_stage_shim` (:398-414).
- `claude/.claude/scripts/tests/conftest.py`:
  - When M5 holds for a caller test:
    - `_git_shim_that_fails_on_worktree_subcommand` (:527-555)
    - `_git_shim_that_fails_on_subcommand` (:558-587)
    - `_git_shim_that_logs_terminal_prompt_on_fetch` (:596-620)
    - `_make_gh_pr_view_shim` (:1746-1774)
    - `_failing_tool_shims` (from :1890)
  - The python direnv shims (:144-268) and the git hook at :697-700 stay.
- `claude/.claude/hooks/tests/test_python_floor.py`: `_write_python3_stub` (:81-85) calls the helper in place of `_write_executable`, which warms its callers in this file and in `test_check_python_floor.py`.
- A's report names every writer and wrapping fixture it migrated, and each one it left with its M5 reason.
- A's own checks:
  - `scripts/venv-run.sh pytest claude/.claude/tests/test_prewarmed_stub.py claude/.claude/tests/test_resume_context_stub.py claude/.claude/hooks/tests/test_scaled_timeout_shim.py -m "not timing"`, then the same files with `-m timing -n0`.
  - `scripts/venv-run.sh ruff check claude/.claude/tests/ claude/.claude/hooks/tests/conftest.py claude/.claude/scripts/tests/conftest.py claude/.claude/hooks/tests/test_python_floor.py claude/.claude/hooks/tests/test_scaled_timeout_shim.py`.
  - A failure in `test_prewarmed_stub.py`, or in the case A adds to `test_scaled_timeout_shim.py`, is A's to fix. A returns any other failing node ID with its output, unfixed, and never edits a test to clear it. The session judges it by the triage rule (Verification).

**After A** (the session, before B1): A's derived checks (Verification 3).

**Dispatches B1-B3** (`code-writer`, one at a time, in this order, one commit each). Each dispatch:
- Migrates its files' own sites by M5, and leaves a file with no in-scope site untouched.
- Reports, per file, the sites migrated, any file left untouched, and each site left with its M5 reason:
  - a first line outside the helper's grammar, python included;
  - a data literal, never written as an executable;
  - not on a PATH;
  - no scaled test runs it;
  - a test reads its text back;
  - a copy of a tracked file;
  - unsettled, with what could not be settled.
- States, for each site it migrates in a `timing`-marked test, that the write precedes the test's measured region (row 41).
- Runs, in order: `scripts/venv-run.sh pytest <its files> -m "not timing"`, then `scripts/venv-run.sh pytest <its files> -m timing -n0`, then `scripts/venv-run.sh ruff check <its files>`. For B1, the two pytest runs also take `claude/.claude/hooks/tests/test_scaled_timeout_shim.py` (row 25).
- Returns each failing pytest node ID with its output, unfixed, and never edits a test to clear a failure. The session judges it by the triage rule (Verification).

The file sets:
- **B1**, marker and `_lib.sh` tests, under `claude/.claude/hooks/tests/`: `test_marker_script.py`, `test_marker_lib.py`, `test_lib_markers.py`, `test_lib.py`, `test_lib_command_words.py`, `test_lib_git_refs.py`, `test_lib_realpath.py`, `test_lib_resume_context.py`.
- **B2**, gate-hook tests, under `claude/.claude/hooks/tests/`: `test_require_architect_consult.py`, `test_require_plan_review.py`, `test_require_ready_for_review.py`, `test_require_skill_review.py`, `test_require_stow_reminder.py`, `test_deny_pii_in_commits.py`, `test_deny_private_project_refs.py`, `test_deny_escaped_backticks_in_pr_body.py`, `test_check_skill_length.py`, `test_hook_alignment.py`, `test_scaled_timeout_shim.py`.
- **B3**, advisory-hook and script tests: under `claude/.claude/hooks/tests/`, `test_nudge_error_mode_analysis.py`, `test_nudge_handoff_near_context_cap.py`, `test_nudge_long_turn_subagent.py`, `test_nudge_memory_store_audit.py`, `test_record_session_end.py`, `test_consume_durable_continuity_file_on_read.py`, `test_announce_resume_command.py`; under `claude/.claude/scripts/tests/`, `test_reconcile_project_plugins.py`, `test_branch_divergence_status.py`, `test_cleanup_merged_branches.py`.
- **B4**, a post-rebase addition committed after B3: `write_shim` in `claude/.claude/scripts/tests/_branch_records_helpers.py` migrates whole under M5, with no new spy case, like the other shared writers B1-B3 migrated whole. Main added `write_shim` and the two timing tests that reach it, `test_branch_records_archive.py` and `test_branch_records_lib.py`, after this plan's base, so no earlier file list named them. B4's checks are `scripts/venv-run.sh pytest` of those two test files with `-m timing -n0`, and `ruff check`. The engineer chose "Fix it in this PR (Recommended)" when the cumulative review found it (row 51).

**Why this split.** A's helper signature and shared writers are every B dispatch's input, so A goes first. Once A lands, the 29 candidate files share no stub writer (row 25). They therefore partition into disjoint sets, each specified by M5 and its own file list, with no other dispatch's context restated. One dispatch for all 29, `test_marker_script.py` among them (row 24), would hold every file's reads in a single context. The B dispatches run one at a time rather than in parallel because each runs `-m timing -n0` cap tests, and a concurrent dispatch's load would disturb their timing, the very failure being fixed.

**Reuse:**
- `write_scaled_timeout_shim`'s unlink (:3094) moves into the helper, so every stub, the shim included, replaces a surviving symlink rather than writing through it.
- `link_bash_into`'s `shutil.which("bash")` lookup (`helpers.py:404`) and `os.defpath` build the warm exec's PATH (rows 33, 34). The environment otherwise follows the reduced ones `test_transcript_gh_cli.py:1730` and `test_shell_lib_function_liveness.py:139` already use (row 35).
- `subprocess.DEVNULL` for stdin, as at `helpers.py:96`.
- `install_shell_exec_guard` (`helpers.py:566`) for the unit tests' guard leg.

**Not touched:**
- `claude/.claude/hooks/tests/test_git_floor.py`, whose `_write_executable` reaches a scaled test only through the `timeout` stand-in the scaled shim replaces (row 8).
- The eight route-naming files row 8 lists.
- `claude/.claude/scripts/tests/_review_pr_stack_helpers.py` and `claude/.claude/hooks/tests/_lib_sh_helpers.py`, each holding a python `gh` shim.
- `claude/.claude/hooks/tests/test_lib_gh.py` and `claude/.claude/hooks/tests/test_lib_worktree_collision_guard.py`, whose three recorded failures install no scaled shim and pass in serial reruns (rows 28, 31, 44).
- The test files that only Verification 3's derived list adds, which are run but not edited.

Projected diff: about 525 lines across 34 files: the 5 only dispatch A edits, plus the 29 candidates, one of which (`test_scaled_timeout_shim.py`) A also edits. Roughly 80 are in helpers.py, 220 in the new test file and 25 in A's case in `test_scaled_timeout_shim.py`. Most of the rest is about three changed lines per migrated site, plus an import line per file. B4 adds about 5 lines in `_branch_records_helpers.py`, a 35th file, on top of the about 525. That exceeds the 400-line split threshold, and rows 43 and 48 record the engineer's answers.

## Verification

Run from the worktree root. `<scratch>` is a new directory outside the repository from `mktemp -d`, made once before Verification 1. Scratch scripts live there and are never committed.

**Merge-base worktree.** The session makes it after Verification 1 and before dispatch A, so the triage of A's own checks can use it, by the recipe `.claude/plans/cost-by-size-test-split.md` ran (`:158-161`, `:191`):
- Run `git merge-base HEAD origin/main` as its own call, and record the SHA it prints as `<base>`.
- Run `git worktree add --detach <scratch>/base <base>`, with that SHA pasted literally.
- Every merge-base run, from the triage of A's own checks through Verification 7, uses `<scratch>/base` as its working directory, so a branch sync mid-plan cannot move the control.
- After Verification 7, run `git worktree remove <scratch>/base`. Run it on every exit, a stopped run included, and say in the session's report whether it was removed.

**Triage rule.** On the affected host, tests fail from the flake being fixed and from load, at the merge-base as on the branch. A failure therefore blocks only when this change caused it (`CLAUDE.md` "Prove your change caused a failing check"). Verifications 3, 4 and 6 apply it, and Verification 7 does not:
- A failure whose traceback passes through `write_prewarmed_stub` blocks.
- A failing node ID with no counterpart at `<base>`, such as one in `test_prewarmed_stub.py`, blocks.
- Rerun each other failing node ID once with `-n0` on the branch and once in `<scratch>/base`, and compare node IDs, not counts.
- One that fails in `<scratch>/base` is pre-existing: record it, and do not attribute it to this change.
- One that passes in `<scratch>/base` and passes on the branch rerun is intermittent: record it. It does not block, because the warm exec runs outside any cap's timer (row 41).
- One that passes in `<scratch>/base` and fails again on the branch blocks until the session diagnoses it by hand. A cap kill in a test whose stub a later B dispatch migrates is an expected diagnosis and does not block.

No other test run, agent dispatch or build runs on the host during Verifications 1 and 7, because load changes exec timing (row 44). Verification 1 reads `uptime`'s load averages at the start of every window, and Verification 7 reads load as its table row states.

1. **Fresh-exec probe** (the affected macOS host, before dispatch A). It tests row 3 before any repository code for the helper exists. A scratch script in `<scratch>` runs windows of four interleaved arms, 10 files each, every file in its own new directory. Every exec is timed. The arms form two pairs, one for `#!/bin/bash` and one for `#!/usr/bin/env bash`, and each pair has two arms:
   - Control: fresh unguarded stubs of that shebang that `exit 0`, each exec'd twice.
   - Guarded: fresh stubs of that shebang whose line 2 is M1's guard and whose body appends to a log. The scratch script execs each once as the helper will, with only the guard's variable and a PATH it builds as M1 builds `_PREWARM_EXEC_PATH`, then once plainly under the ambient PATH. A log written by the first exec is a guard defect, and stops the probe.
   - A control stub is slow when its first exec takes over 0.667s, the scaled 2s cap (`scaled_cap(2)`, `helpers.py:3148-3151`), and its second takes 0.26s or less, row 1's later-exec figure. A guarded stub stays cold when its plain exec takes over 0.667s.
   - A window counts for a pair when at least 3 of that pair's 10 control stubs are slow. At that rate, 10 files that behave like the control would all be fast with probability about 3% (0.7 to the 10th power).
   - In a window that counts for a pair, the pair's count is its guarded stubs that stayed cold:
     - a count of 0 clears the pair, and row 3 holds for that shebang;
     - a count of 2 or more fails the pair, so row 3 is false for that shebang, and the session stops before dispatch A and reports to the engineer;
     - a count of exactly 1 leaves the pair to its next counted window.
   - Windows run until both pairs are decided or six have run. A pair still undecided after six stops the probe, and the session reports to the engineer that the host is not reproducing.
   - The one exception is an `#!/usr/bin/env bash` pair whose control counted no window. The host then never made enough of those stubs slow to test the guard, so its guarded result is an observation, not a test. Record it as one and go on.
   - Record each window's per-arm counts and load averages in the PR body.
2. **Helper unit tests.** `scripts/venv-run.sh pytest claude/.claude/tests/test_prewarmed_stub.py` passes on macOS and in CI, as does the case A adds to `test_scaled_timeout_shim.py`. They cover every case Critical files lists for them (rows 4-6, 10, 33-36).
3. **A's derived checks** (the session, after A, before B1).
   - Start a name list with every writer and fixture A's report names.
   - Grep `claude/.claude/` for each name on the list. A matching `test_*.py` file joins the run set. In any other matching file, such as a conftest or `hooks/tests/_lib_sh_helpers.py`, each function or fixture that uses the name joins the name list.
   - Repeat until a pass adds no name. One pass would miss `scripts/tests/test_diff_size_ack.py`, which reaches `_make_git_recording_argv` only through `_lib_sh_helpers.py`'s `_git_env_recording_shim` (`_lib_sh_helpers.py:256-262`, `test_diff_size_ack.py:125-126`).
   - Run the set with `scripts/venv-run.sh pytest <files> -m "not timing"`, then `scripts/venv-run.sh pytest <files> -m timing -n0`, and judge failures by the triage rule. A migrates `write_scaled_timeout_shim` and `_write_conditional_sleep_shim` in every case, so the set always holds the 37 route-naming files.
4. **Per-dispatch checks** for A and B1-B3, run and returned as Critical files states. The next step starts only when no returned ID blocks under the triage rule.
5. **Residual-site audit** (after B3). Grep the 34 files, and `_branch_records_helpers.py`, for a first line in the helper's grammar. Every match that does not reach `write_prewarmed_stub` must appear in a dispatch report with its M5 reason. List those reasons in the PR body.
6. **Full suite, CI's two passes** (after Verification 5). `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py -m "not timing"`, then `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py -m timing -n0`. Both select the full suite through the `helpers.py` global trigger (rows 13-14, G3). Then run `scripts/venv-run.sh ruff check claude/.claude/`. No tracked shell file changes, so ShellCheck has nothing new to check.
   - Judge failures by the triage rule.
   - A failure in a test that installs the scaled shim, pre-existing or not, may be a site M5 missed. The session checks it by hand and reports it to the engineer.
   - Record in the PR body, with the tip SHA, each pass's failure count, failing node IDs and pytest's elapsed-time line, beside Context's 13-20. That figure came from earlier windows, so it is a comparison, not a control.
   - Report the three out-of-scope IDs' outcomes separately, as load observations (rows 31, 44).
7. **macOS paired check** (after Verification 6, on the host where Verification 1 counted its windows). This is the macOS test run row 30 requires, in the form rows 45 and 46 record, and the plan's central pass/fail evidence.
   - The gate set is row 31's 10 node IDs in tests that install the scaled shim.
   - Before the priming run, record `<base>` and the tip SHA, which `git rev-parse HEAD` prints in its own call.
   - An arm is one run of `scripts/venv-run.sh pytest <the 10 IDs> -n0 --basetemp=<arm-dir>/tmp --junitxml=<arm-dir>/junit.xml`, where `<arm-dir>` is a new directory under `<scratch>` for that arm alone. The shared base directory keeps only one session's `tmp_path` directories (`pyproject.toml:25-29`). Each arm's own `--basetemp` therefore keeps a failing arm's directories for diagnosis.
   - A scratch script in `<scratch>` runs one arm per call and prints one table row for it:
     - its tree and its place in the window;
     - pytest's exit code;
     - the JUnit `tests`, `skipped`, `errors` and `time` values;
     - the failing IDs;
     - `uptime`'s load averages and the count of other running pytest processes, at both ends of the arm.

     The session tallies the rows by eye against the rules below, so no unreviewed code decides pass or fail.
   - An arm is valid when pytest exits 0 or 1 and its JUnit shows 10 tests, 0 skipped and 0 errors (row 39). The session checks this for every arm in both trees, the priming run included, before reading its failing IDs. An invalid arm is a fault in the run, not a data point in either direction: its window neither counts nor passes, and the session stops and reports the arm's row to the engineer.
   - Priming run: one uncounted arm in `<scratch>/base`, before the first window. Its checkout wrote every tracked file fresh, so its first run may pay first-exec costs the branch worktree already paid in Verification 6. Never count its failing IDs.
   - A window is two arms run back to back, one in `<scratch>/base` and one in the worktree at the branch tip. Odd-numbered windows run the merge-base arm first, and even-numbered windows run the branch arm first.
   - A window counts when its merge-base arm fails at least 1 of the 10 IDs. Any other window is discarded.
   - Windows run until 5 count or 12 have run.
   - Pass: 5 windows count, and no branch arm failed in any window run.
   - After 12 windows with fewer than 5 counted, stop and report to the engineer that the host is not reproducing.
   - A branch-arm failure fails the check, in a counted window or a discarded one. The session stops the run, keeps that arm's directory, and reports the failure to the engineer with its own diagnosis. The diagnosis chooses among:
     - a site M5 missed or a dispatch left;
     - row 3;
     - load, judged from the arm's readings at both ends.
   - An ID that failed in every merge-base arm, the priming run included, made every window count on its own. The session names it in its report to the engineer, whether the check passed or stopped.
   - Only the engineer can waive a branch-arm failure. The session never reruns an arm, a window or an ID to replace a failed result, and resumes windows only on the engineer's word.
   - Row 49 bounds how often this rule passes a branch no better than the merge-base.
   - Record in the PR body the 10 IDs, `<base>`, the tip SHA, each window's arm order and every arm's table row, the priming run's included.
   - A later commit re-runs this check at the new tip before the PR is presented as ready only when it changes an executable line in the helper or in a migrated site. A docstring, comment, blank-line or plan-file-only change does not.
   - A re-run recreates the base at the recorded `<base>` and repeats the priming run.
   - The PR body names the tip that was tested. It also says that later commits changed no executable line, when that holds.
8. **CI** passes both pytest steps (`.github/workflows/tests.yml:179`, `:186`) on Linux. That shows the guard parses under the runner's `/bin/sh`, the warm exec resolves `bash` there, and the extra exec breaks no test there (rows 6, 33, 34, G2).
9. `git diff --name-only origin/main...HEAD` lists only Critical files paths and this plan.

**What Verification 7 cannot show:**
- How another host behaves. It samples one host, under the load present while its windows run.
- That its windows are independent. Back-to-back windows on one host share its load drift, so row 49's bound, which assumes independence, may not hold.
- Its error rate beyond row 49's bound on a false pass. Nothing bounds a false fail. One load failure in a branch arm ends the check (row 44), so expect to waive some failures. Its power against a partial fix is unstated.
- That its two trees differ only by this change. `<scratch>/base` sits under `mktemp -d`'s directory and the branch worktree under `.claude/worktrees/`, so a path-keyed cause (G1) could treat their tracked scripts differently. Both arms' stubs sit under `<scratch>`. The merge-base worktree stays where the cited precedent made it, rather than at a location no plan has run.
- That any stub outside the 10 IDs' tests is warm. Verification 5 shows only that each residual site has a recorded reason.
- How a stub of any other shebang behaves in a test. The 10 IDs' stubs are all `#!/bin/bash`, and Verification 1 times guarded `#!/usr/bin/env bash` stubs only outside a test.
- Whether a stub added after this change is warmed. Row 47 leaves the tripwire for that to a follow-up PR.
- Anything on Linux. CI never showed the flake (G2), so Verification 8 shows only that the change breaks nothing there.

## Out of scope

- **Python-shebang stubs.** Each one under the shim is the cap's target or sits under a large cap (row 21). The helper rejects them (M3). The recorded `test_lib_gh.py` failure is a python stub in a test with no scaled shim, so it is out on both counts (row 31).
- **Stubs in tests that never install the scaled shim** (row 28), including stubs under a real, unscaled cap. Three of the 13 recorded failures sit here, and rows 31, 32 and 44 record why warming stubs cannot fix them. This plan leaves them unfixed, and Verification 6 reports their outcomes separately.
- **Executables a capped command runs from outside PATH, and copies of production scripts or binaries in temp directories.** Examples are git hooks such as `scripts/tests/conftest.py:697-700`'s `reference-transaction` hook, and askpass or fsmonitor programs. They share the bug's shape, but the Ask's boundary is stubs on PATH, and a copy of production text cannot take a guard line. One that a scaled test reaches would surface only as a failure, diagnosed by hand.
- **Fresh symlinks to real binaries on a test's PATH**, such as closed-PATH farms. Whether one is slow is untested (row 37). One that a scaled test reaches would surface only as a failure, diagnosed by hand.
- **`TIMEOUT_SCALE_DIVISOR` and every production cap stay as they are.** Changing either loosens every cap-boundary test's bound and does not remove the first-exec cost (Alternatives).
- **Finding the cause of the slow first exec.** It is host-level (G1), and the design does not depend on it.
- **Attributing a failure to a specific cap kill.** Verification 7 compares the 10 recorded IDs' pass rates at the merge-base and on the branch (rows 45, 46). It does not say which capped call killed a failing test. A residual failure is diagnosed by hand.
- **Making `select-tests.py` run CI's two passes by default.** It alters every contributor's default local run, so it is a separate change (row 27). Verification 6 passes the marker arguments by hand instead.
- **A suite-wide tripwire that fails a new hand-rolled stub.** It is left to a follow-up PR (rows 40, 47). Notes for that PR:
  - No static signal separates a PATH stub from a shebang literal used as data.
  - A teardown scan of each directory that holds the scaled shim is feasible but incomplete. It misses a stub in a separate PATH directory, such as the `fake_gh_pr_exists` `gh` in two recorded failures (row 31).
  - Without a tripwire, a new unguarded stub can bring the flake back on macOS while CI on Linux stays green (M7).
- **Merging the four local `_write_executable` copies into helpers.py, or splitting helpers.py under the line limit** (rows 15, 17).
