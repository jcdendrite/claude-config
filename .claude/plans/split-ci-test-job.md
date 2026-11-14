# Split the CI test job so it keeps headroom under the 25-minute cap

## Context

Goal: keep the required `tests` check well under its 25-minute `timeout-minutes` cap by running the parallel pytest pass and the serial `timing` pytest pass as separate jobs, with an aggregate job that keeps the `tests` check context.

Ask: ticket GH-1440, option 2 ("Give the parallel pass and the `timing` pass a job each, plus an aggregate job named `tests`"), which the ticket marks "Recommended as the first step". Option 1 (raise the cap) and option 3 (directory slicing) are not requested.

Why now: one `tests` run on a large PR took 21m08s of the 25-minute cap, split as about 36s setup, 15m40s for `Run tests (parallel)`, 4m49s for `Run tests (timing, serial)`, and under 1s for `Lint`. Recent `main` runs of `tests.yml` took about 16 to 22 minutes by `createdAt` to `updatedAt` across the five successful runs listed by `gh run list --workflow tests.yml --branch main`. The ticket's own estimate is about 16 minutes for the longer job after the split.

Intended outcome: the longest job is setup plus the parallel pass, `tests` stays the required-check context with no branch-protection edit, a failed or cancelled slice can never leave `tests` passing, and the workflow-parsing tests that hard-code `jobs.tests.steps` are reworked to match.

## Approach

Run the two pytest passes as the two legs of one matrix job, `test-slice` (`slice: [parallel, timing]`, `fail-fast: false`). Its step list is today's `tests` job, with each pass and Lint gated to its leg. Add a small aggregate job that keeps the id `tests`, runs on every outcome, and passes only when `needs.test-slice.result` is `success`. The `detect` step stays inside each leg, so no job is ever skipped and the aggregate never has to judge a skip. One test holds both jobs' keys equal to an expected literal, so any added or changed job key fails by construction. A second holds the four work steps, found by command, equal to an expected literal, and a behavior test runs the aggregate's real shell body (M5).

How this differs from the session's proposed defaults (row 18), all three of which were the session's own, not the engineer's:
- **(a) Matrix and aggregate kept; the `include` legs and the separate `detect` job replaced.** No leg needs a field beyond its name, so a one-dimension `slice` list replaces `include`. A separate `detect` job would add a serial job ahead of both legs, job-output plumbing, and a job-level skip that the aggregate would then have to tell apart from a bad skip. Its only saving is two short legs on a deny-list-only diff (M3).
- **(b) Dissolved.** With detect in each leg, a leg whose diff is not test-relevant succeeds with its steps skipped, as today's job does. The aggregate's rule shrinks to "only `success` passes", which fails a failed or cancelled leg and a failed detect step alike. M5's schema check forbids a job-level `if:` on `test-slice`, so no leg is skipped as a job. The proposal lacked two keys:
  - `fail-fast: false`, without which a failing leg cancels the other and suppresses its report (row 9).
  - `if: always()` rather than `!cancelled()` on the aggregate, so a cancelled run fails `tests` instead of skipping it (row 10).
- **(c) Applied as one 25-minute cap.** The parallel leg is the binding one, at about 16m16s on the worst measured run (row 12), so 25 leaves it about 8m44s of headroom, which is the Ask's goal. The timing leg shares that cap (Out of scope). The aggregate gets 5.

Accepted costs:
- Each run pays a second runner's setup, about 36s (row 12), plus a seconds-long aggregate job.
- Each run holds two runner slots while the timing leg runs, about 5m25s (row 12), so a burst of runs reaches the account's concurrent-job limit sooner. A run held at that limit shows `tests` pending in a queue, not failing.
- Each leg runs its own `apt-get update` and package installs, including the `--error-on=any` update (`tests.yml:142`), so a transient mirror error gets two chances per run. Re-running the failed jobs re-runs the failed leg and the aggregate, not both legs (row 23).
- A hung `timing` leg holds `tests` pending until the 25-minute cap, the same bound today's single job has, and holds its runner that long.
- A cancelled run reports `tests` as failed rather than cancelled, on a PR and on a superseded `main` push alike (row 25). Branch protection treats both as not passing. A superseded `main` run may also send a run-failure notification. It may also show on the README's workflow badge (`README.md:3`) until the newer run ends. Neither effect was checked. Verification step 5 observes both a supersede and a manual cancel, but only on a `pull_request` run, and records each run's run-level `conclusion` (`gh run view <run-id> --json conclusion`). The badge and notification key off that run-level value, whichever event started the run. The red mark on a superseded `main` commit is an accepted cost. The PR body names the post-merge observation: the first superseded `main` run, read with `gh run list --workflow tests.yml --branch main --json conclusion,event,headSha`. If the effect is unwanted, the fallback is to restrict `cancel-in-progress` to `pull_request` events.
- In `/ready-for-review`'s bucket table, `fail` outranks `cancel` (row 24). A cancel on the current head, such as a manual cancel or a lost runner, therefore dispatches a `/root-cause-analysis` diagnosis on `tests`. A cancel by a newer push still stops at that skill's staleness check. The aggregate's log line names `cancelled`, so the diagnosis finds the cause in one read. Changing that table is a separate change (Out of scope).

No option keeps a cancelled run's `tests` in the `cancel` bucket without failing open or widening the token:
- `!cancelled()` on the aggregate skips it on a cancelled run, and a skipped job reports success to a required check (row 10).
- A step-level `!cancelled()` inside the `always()` job skips the only step, so the job succeeds.
- The aggregate cancelling its own run through the API needs a token that can cancel runs, against `permissions: {}`.

The aggregate job, in full, appended after `test-slice`:

```yaml
  tests:
    needs: test-slice
    # always(), not !cancelled(): a cancelled run must fail this required check, not skip it.
    if: always()
    runs-on: ubuntu-24.04
    # Bounds a stuck runner for one shell comparison; no vendor value applies.
    timeout-minutes: 5
    permissions: {}
    steps:
      - name: Require every test-slice leg to succeed
        env:
          SLICE_RESULT: ${{ needs.test-slice.result }}
        run: |
          set -euo pipefail
          if [[ "$SLICE_RESULT" == "success" ]]; then
            exit 0
          fi
          echo "test-slice finished as '$SLICE_RESULT'; tests passes only when every leg succeeds." >&2
          exit 1
```

### Assumption ledger

**Root:** The single `tests` job runs setup, the parallel pass and the serial `timing` pass back to back, so its 25-minute per-job cap must cover their sum. One large-PR run used 21m08s of it (row 12). The fix must put the passes in separate jobs while the `tests` context that branch protection requires keeps reporting (G1). That context must never report passing when a pass failed or was cancelled.

**Givens:**
- G1. Branch protection on `main` requires the `tests` context. The session's token got a 403 reading it, so whether `main` uses classic protection or a ruleset is unknown. Reason: the repo admin owns that setting, and keeping the job id `tests` needs no edit under either form.
- G2. `timeout-minutes` bounds each job separately: "The maximum number of minutes to let a job run before GitHub automatically cancels it." (`docs/rules-references.md:141-144`, quoting GitHub's workflow-syntax reference). Reason: the vendor imposes it.

**Mechanisms:**
- **M1.** In `.github/workflows/tests.yml`, change the work job's id from `tests` to `test-slice` and add `strategy: {fail-fast: false, matrix: {slice: [parallel, timing]}}`. Keep `runs-on`, `env`, `timeout-minutes: 25` and every step, in today's order.
  - `fail-fast` gets the comment "# A failing leg must not cancel the other, so both passes report."
  - "Run tests (parallel)" gets `if: steps.detect.outputs.changed == 'true' && matrix.slice == 'parallel'`.
  - "Run tests (timing, serial)" gets `if: steps.detect.outputs.changed == 'true' && matrix.slice == 'timing'`. This drops the `${{ !cancelled() && … }}` wrapper and its comment at :181-182, because `fail-fast: false` now serves that purpose.
  - Lint gets `if: steps.detect.outputs.changed == 'true' && matrix.slice == 'timing'` and the comment `# Runs in the timing leg only, so it reports once.` Staying after the pytest step keeps today's skip-when-the-pass-failed behavior (row 1).
  - Every other step's `if:` is unchanged.
  - Rewrite the job comment at :26-33 to say that each leg runs one pass, with today's reasons for each. Its closing line becomes "Re-derive this cap whenever the parallel pass grows.", because the parallel leg dominates and sets the cap.
  - The install step's comment at :119 becomes: each "Run tests" step's `if:` must stay this condition plus its own `matrix.slice` test, because conftest.py's require_direnv() hard-fail guarantee depends on it. The two pytest steps' cross-reference comments point to that comment.
  - Lighter primitives, each rejected for this coordination pattern:
    - Raising the cap is option 1, which is not requested (row 17).
    - Running both passes concurrently in one job puts the `timing` tests under sibling load (row 20) and still leaves one job's cap.
    - Two hand-written jobs duplicate nine steps, including the Python pin (row 3) and the git pin.
    - Requiring each slice in branch protection, with no aggregate, needs a protection edit (G1).
  - Heavier alternatives, each rejected:
    - A composite setup action moves `setup-python` out of `tests.yml`, which `read_ci_python_pin` reads (row 3).
    - A reusable workflow adds a second file and a caller/callee layer for the same result.
    - A third-party aggregate action adds a dependency for a five-line check.
    - YAML anchors would still emit two `pin-git` step ids, and GitHub's support for them was not checked.

  anchors: root, row1, row3, row4, row5, row9, row12, row17, row20, G2
- **M2.** Append the aggregate job shown above.
  - Replace header lines :6-7 with these three lines, keeping :1-5:
    - `# A job-level "name:", when set, replaces the id as the context string, so the "tests" job carries none.`
    - `# The workflow-level "name:" does not affect the context string.`
    - `# The "tests" job is the aggregate at the end of this file, and passes only when every test-slice leg succeeds.`

    Today's :6-7 say only the job id sets the context, which would let a later editor add a cosmetic `name:` that renames it (row 11). If row 11 misreads GitHub, the new lines are only over-cautious.
  - `permissions: {}` holds because the job checks out nothing.
  - The `env:` indirection lets a test run the real `run:` body (M5).
  - The body exits 0 only on a positive `success` match and otherwise falls through to `exit 1`. A shell without `[[` therefore fails closed too (row 22).

  anchors: root, row8, row10, row11, row12, row22, G1
- **M3.** `detect` stays a step in each leg. Both legs check out the same SHAs with full history, so they compute the same `changed` value. A leg whose diff is deny-list-only skips every gated step and succeeds, exactly as today's job does. A failed detect step fails its leg and therefore the aggregate.

  anchors: row6, row13, row18, row19
- **M4.** In `claude/.claude/tests/helpers.py`, next to `extract_ci_detect_step_run_block`:
  - Add `CI_TEST_SLICE_JOB_ID = "test-slice"`.
  - Add `ci_tests_workflow_jobs()`, the parsed `jobs` mapping of `_CI_DETECT_STEP_WORKFLOW`.
  - Add `ci_test_slice_job()`, that mapping's `CI_TEST_SLICE_JOB_ID` entry.
  - Make `extract_ci_detect_step_run_block` read its steps through `ci_test_slice_job()`.
  - Switch `test_ci_path_filter.py`, `test_git_floor.py` and `test_select_tests.py` to these helpers instead of indexing `"tests"`. M5 deletes the fifth site's file, `test_ci_watch_workflow_parity.py`.
  - Delete each switched file's workflow-path constant, local job-steps function and `yaml` import once nothing else in it uses them. M5 names which ones go.

  One home means the next rename touches one line, not five. `helpers.py` is in the diff regardless (row 2) and is already a global trigger (row 7), so this costs no extra test selection.

  anchors: row2, row7
- **M5.** Rework the workflow tests. One new file, `claude/.claude/hooks/tests/test_ci_workflow_gates.py`, sits beside the other `tests.yml` readers and holds two structural checks and one behavior test. Each structural check is a function of a parsed mapping. It runs once against the real workflow and once per permanent negative fixture. Each fixture deep-copies the real parsed mapping and seeds one break. It must raise the `AssertionError` of the assertion that break targets, matched on that assertion's message. This is the shape of `_assert_every_pin_is_the_running_minor` and its fixtures (`test_ci_python_matches_dev_interpreter.py:38-178`), and of `_ci_step_roots` and its fixtures (`test_select_tests.py:2266-2396`).
  - The module docstring lists these invariants, one item each:
    - both jobs' keys equal one expected literal, apart from `test-slice`'s steps and the aggregate step's `run:` body;
    - each pytest step's `if:` is the install step's condition plus its own leg test, because conftest.py's require_direnv() hard-fails a pytest step that runs without the install step;
    - the four work steps, each keyed by its command, equal one expected literal (the install step's `run:` excepted), so a changed command, `shell:` or `env:` cannot turn a failed pass green;
    - the detect step carries no `if:`, so no leg can skip its change detection;
    - Lint runs in the `timing` leg only, so it reports once;
    - every matrix leg owns a work step, and no `test-slice` step carries `continue-on-error`;
    - the aggregate step's `run:` body passes only on `success`.
  - The schema check reads the parsed `jobs` mapping:
    - With `test-slice`'s `steps` and the aggregate steps' `run:` bodies removed, the mapping equals one expected literal. The literal holds both job ids, every key and value of the aggregate and its one step, and `test-slice`'s `runs-on`, `timeout-minutes`, `env` and `strategy`.
    - `test-slice`'s `strategy["fail-fast"] is False`, because Python's `0 == False` lets the equality accept `fail-fast: 0`.
    - Its message says to update the literal only with an edit that still fails `tests` whenever a leg does not succeed.
  - The gate check reads the `test-slice` job:
    - It finds the four work steps. "Install stow and direnv" is found by name. The two pytest steps and Lint are found by command, as `_steps_running` does (`test_select_tests.py:2262-2263`), so a rename cannot hide one.
    - Each work step's key is its command-derived identity, never its step name: the `-m` expression (`not timing` or `timing`) for a pytest step, and `ruff check` for Lint. Only the install step is keyed by its name. A swap of the two pytest `run:` bodies therefore moves each identity onto the other step's `if:`, so the swapped-bodies fixture below is not vacuous.
    - The (identity, step) pairs, sorted by identity, equal one expected list, with each step as its whole parsed mapping. That one equality pins each step's `name`, `if:` and full `run:` text. The expected `if:` values are:
      - the install step: the shared condition;
      - `not timing`: the shared condition plus `matrix.slice == 'parallel'`;
      - `timing`: the shared condition plus `matrix.slice == 'timing'`;
      - `ruff check`: the same `'timing'` gate.
    - The legs that `matrix.slice == '<leg>'` names across every step's `if:` equal `set(strategy.matrix.slice)`. This holds across a deliberate edit that updates both literals, because a leg whose gated steps all skip is a green leg.
    - No step carries `continue-on-error` (row 21). The pair list already covers the four work steps, so this assertion guards the rest.
  - Negative fixtures, one break each:
    - For the schema check:
      - the aggregate step's `SLICE_RESULT` set to `${{ job.status }}`;
      - the aggregate's `if` set to `${{ !cancelled() }}`;
      - `name: tests` on `test-slice`;
      - `fail-fast: 0`.
    - For the gate check, each naming the assertion it targets:
      - the parallel pytest step's leg test removed (pair list);
      - the two pytest steps' `run:` bodies swapped (pair list);
      - ` || true` appended to the timing pytest step's `run:` (pair list). No existing test catches it, because `_matching_pytest_line` (`test_ci_path_filter.py:209-223`) matches only the phrases `-m timing` and `-n0`, and `_leading_positional_roots` (`test_select_tests.py:2249-2259`) stops at the first option;
      - a third leg in `matrix.slice` with no step gated to it (leg set);
      - `continue-on-error: true` on the pinned-git step, which the pair list does not cover (`continue-on-error`);
      - `if: matrix.slice == 'parallel'` on the detect step (detect assertion);
      - the detect step's `id` renamed (detect assertion);
      - `shell: bash` on the install step (pair list).

      One positive control changes only the install step's `run:` text and must pass, which shows the exemption is live. It is a new fixture shape in this file: a mutation that must not raise.

      The equality rejects any other key, such as `shell:`, `env:`, `working-directory:` or `continue-on-error`. List equality also fails a missing or duplicated step.
    - The install step's `run:` is the one exemption. The exemption removes only the `run` key from the actual and the expected install mapping before comparing. A failed or no-op install cannot flip a failing pytest pass green. A missing stow skips its tests silently, as today. A missing direnv fails loudly in the parallel leg (`conftest.py:118-131`, `require_direnv()`).
    - The detect step has its own assertion and message. It finds the step through the exactly-one-`id: detect` lookup that `extract_ci_detect_step_run_block` (`helpers.py`) already uses, so zero or two matches fail instead of reading as "no `if:`". It then requires the step to carry no `if:`.
    - The pair list runs first, so a fixture that also breaks a later assertion still matches the pair list's message.
  - Equality fails any added or changed job key, or work-step key, by construction, apart from the install step's `run:`, so no fixture per key is needed. The fixtures show that each check reads the real aggregate, `test-slice` and its steps.
  - The behavior test writes the aggregate step's real `run:` body to a file and runs it under bash, as `_run_pin_step` does (`test_git_floor.py:394-429`).
    - The env is built fresh, holding only `PATH` and, when the case sets it, `SLICE_RESULT`. It is never copied from `os.environ`, so an exported `SLICE_RESULT` or `BASH_ENV` cannot mask a case.
    - The cases:
      - `success` exits 0;
      - `failure`, `cancelled`, `skipped` and the empty string each exit 1, with stderr naming the value;
      - an unset `SLICE_RESULT` exits nonzero.
  - Both literals are change detectors. A deliberate edit to either job's keys or to a pytest or Lint step's command, such as a runner-label bump, a re-derived cap or a new pytest flag, updates them in the same PR.
  - Alternatives set aside:
    - A pin and a fixture per key: each new key needs both, and a key no pin lists passes, as `name`, `permissions` or `defaults` on `test-slice` would.
    - Key allow-lists: they close the key set but not the values, so a widened `permissions` or a changed `env` expression passes.
    - Equality over `test-slice`'s whole step list: it copies about 150 lines of workflow into the test and fails on every unrelated step edit (Out of scope). The four work steps' literal copies about 20.
  - Delete `claude/.claude/scripts/tests/test_ci_watch_workflow_parity.py`:
    - The gate check holds its invariant.
    - Two of its three tests pin `if:` strings this change replaces.
    - No other file names it (row 31).
  - `test_ci_path_filter.py`:
    - Delete `_tests_job_steps`. `test_parallel_and_timing_passes_are_distinct_steps` and the collect-only probe's `_parallel_pytest_argv` (:264-277) read `ci_test_slice_job()["steps"]` instead.
    - Delete `test_timing_pass_step_runs_even_if_parallel_pass_was_cancelled`. Its invariant, that a parallel-pass failure cannot suppress the timing pass's report, now rests on `fail-fast: false`, which the schema check pins.
    - `TestPytestTwoPassSplit`'s docstring names the `test-slice` job's step list and drops its clause about checking the timing step's `if:`.
    - The distinct-steps assertion message at :248-251 says the passes must be separate steps so that each runs in its own matrix leg.
    - Delete the `yaml` import. `_WORKFLOW_PATH` stays, for `_skip_regex_pattern` (:55).
  - `test_git_floor.py`: `_pin_step` reads `ci_test_slice_job()["steps"]`, and its docstring names that job. Delete `_WORKFLOW` and the `yaml` import.
  - `test_select_tests.py`: :2345 reads `ci_test_slice_job()["steps"]`, and the messages at :2272 and :2277 name that job. Delete `_TESTS_WORKFLOW_PATH` and the `yaml` import.

  anchors: row2, row4, row5, row6, row8, row10, row11, row21, row22, row31
- **M6.** Update the two docs:
  - Rewrite the `README.md:575` sentence to "two passes, each in its own job", with no "then".
  - Add one `README.md` sentence saying the required `tests` check passes only when both jobs succeed.
  - Add one `CHANGELOG.md` Unreleased › Changed entry for the split.

  The CHANGELOG entry names no check-run strings beyond `tests` (row 11).

  anchors: row15, row16

**Rows:**
1. Current workflow shape. [verified: Read of `.github/workflows/tests.yml`]
   - One job, id `tests`, with no job-level `name:` (:24) and `timeout-minutes: 25` (:34). The header names that id as the required context (:1-7).
   - Every setup and test step is gated on `steps.detect.outputs.changed == 'true'` (:101-189).
   - The timing step adds `!cancelled() &&` (:185). Lint (:189) does not, so a failed pass skips it.
   - One `actions/setup-python` step carries the only `python-version` pin (:100-106).
2. Five sites hard-code `jobs["tests"]["steps"]`. [verified: grep for `["tests"]`, `jobs.tests` and `tests.yml` across the tree outside `.claude/plans/`; a second sweep with the Grep tool, which honors `.gitignore`, for `jobs\.|"tests"|tests\.yml` over `evals/`, `install-dev.sh`, `install.sh`, `plugins/` and `scripts/`]
   - The second sweep re-covers the paths a reviewer's truncated sweep missed. It found only `install-dev.sh:3`, `:8`, `:53` and `:95`, which all name the file for the Python-pin read, and that read accepts any job (row 3). `_ci-python-pin-lib.sh` lives under `claude/.claude/scripts/`, not `scripts/`, and row 3 covers it.
   - The sites are `claude/.claude/tests/helpers.py:2713`, `claude/.claude/scripts/tests/test_ci_watch_workflow_parity.py:40`, `claude/.claude/hooks/tests/test_ci_path_filter.py:206`, `claude/.claude/hooks/tests/test_git_floor.py:291` and `claude/.claude/scripts/tests/test_select_tests.py:2345`.
   - The `"tests"` keys in `test_ci_python_matches_dev_interpreter.py:60-176` belong to synthetic fixtures.
   - `ci-watch.sh:286-287` and `review-pr-acquire.sh:136` pass every check through without filtering on a name.
3. Both Python-pin readers accept one setup-python step in any job. [verified: `helpers.py:2725-2732`, `test_ci_python_matches_dev_interpreter.py:38-56`, `_ci-python-pin-lib.sh:14-22`, `test_ci_python_pin_lib.py:114-122`] Each reads every job, or every quoted `python-version:` line, and requires one distinct value. Two hand-written jobs would put the pin in two places, against the "single home" comment at `tests.yml:103`.
4. `_ci_step_roots` requires exactly two `pytest` steps and one `ruff check` step in the step list it gets, found by command. [verified: `test_select_tests.py:2244-2287`] Keeping two literal pytest steps leaves that count and the negative fixtures at :2349-2396 unchanged.
5. `test_ci_path_filter.py` matches literal pytest lines on `-m "not timing"` and on `-m timing` with `-n0` (:209-228). It also shlex-splits the parallel line and runs it as a collect-only probe (:264-311). [verified: Read] A `${{ matrix.* }}` argument in that line would break both.
6. `require_direnv()` hard-fails in CI when direnv is missing. The parity test pins the three direnv-gated steps' `if:` strings exactly. [verified: `conftest.py:118-131`, `test_ci_watch_workflow_parity.py:26-80`]
7. `helpers.py` is in `GLOBAL_TRIGGER_PATHS`, so `select-tests.py` selects the full suite for this diff on its own. [verified: `select-tests.py:475-479`]
8. A matrix job's `needs.<job_id>.result` is `success` only when every leg succeeded. [unverified: recollection of GitHub's contexts reference. The platform reviewer's fetch of that reference found the four values `success`, `failure`, `cancelled` and `skipped` but no statement on how a matrix aggregates its legs, so Verification step 5 observes it] The aggregate fails on every other value, so a misreading can only fail a run. The one unsafe case would be GitHub reporting `success` for a matrix with a failed leg.
9. `strategy.fail-fast` defaults to true, which cancels the matrix's other in-progress legs when one fails. [unverified: recollection; Verification steps 4 and 5] The plan sets it to `false` explicitly, so the job behaves the same either way. This row grounds only why the key is needed.
10. A job skipped by its `if:` reports success to a required check. [unverified: recollection of GitHub's docs on job conditions; Verification steps 4 and 5] Under that reading, `!cancelled()` on the aggregate would let a cancelled run pass `tests`. `always()` is safe under either reading. GitHub's caution against `always()` concerns work that can hang on a critical failure, such as fetching sources [unverified, same source]. The aggregate fetches nothing.
11. A job's check-run name is its job-level `name:` when set, otherwise its id, and matrix legs append their values. [unverified: recollection; Verification step 4 reads `jobs.<job_id>.name`, and step 6 observes the names] This matters only in the safe direction. The aggregate keeps today's shape with no `name:` (row 1), so its context stays `tests`. No leg of `test-slice` can report `tests` while that job carries no `name:`, which M5's schema check holds.
12. On the 21m08s run, setup took about 36s, the parallel pass 15m40s, the timing pass 4m49s and Lint under 1s. [verified: Context § "Why now", from the session's reading of that run; the architect did not re-run it] So the parallel leg needs about 16m16s of its 25 minutes, and the timing leg about 5m25s.
13. The repo's recorded CI decision relies on the job behind the required check always running, with selectivity applied to steps rather than to the job. [verified: `docs/design-decisions/ci-stays-an-unconditional-full-suite-backstop.md:13`] Keeping detect in each leg keeps that shape for every job. A separate detect job would gate the slice job itself.
14. Only `tests.yml` and `dependabot.yml` exist under `.github/`, and `tests.yml` has no `merge_group` trigger. [verified: Glob of `.github/**`; `tests.yml:11-14`] No other workflow or merge queue depends on the job layout.
15. `README.md:575` says the serial pass runs "then", after the parallel pass. [verified: Read] After the split the two run concurrently, so the sentence goes stale.
16. CI layout changes get a CHANGELOG entry. [verified: `CHANGELOG.md:183` and `:700`; the latter records a workflow rename that kept the job id `tests`]
17. The Ask is GH-1440's option 2. Options 1 and 3 are not requested. [verified: the Context's `Ask:` line quoting the ticket; the architect did not read the ticket itself]
18. The session proposed three defaults as its own, not the engineer's: [unverified: session proposal]
    - (a) a `strategy.matrix` with two `include` legs, a separate `detect` job, and an aggregate job with id `tests`;
    - (b) a slice skipped because detect found nothing test-relevant counts as acceptable, while any failed or cancelled slice, or a failed detect, fails the aggregate;
    - (c) per-job `timeout-minutes` re-derived from measured durations.

    This plan keeps (a)'s matrix and aggregate, but replaces `include` with a one-dimension list and the detect job with detect in each leg (M1, M3). It dissolves (b) into "only `success` passes" (M2) and applies (c) as M1's and M2's caps.
19. The deny-list covers five paths. [verified: `tests.yml:74`] A diff touching only those paths now runs two legs whose gated steps all skip, plus the aggregate, instead of one such job. The cost is runner start-up on a rare diff.
20. The `timing` marker covers tests that assert a wall-clock bound or need serial isolation from subprocess-spawn contention, and they run with `-m timing -n0`. [verified: `pyproject.toml:31`] Running them beside the parallel pass's workers would bring that contention back.
21. `continue-on-error` on a job or step can let a failure finish as success. [unverified: the platform and security reviewers' recollection; neither found GitHub stating its effect on `needs.<job_id>.result`] M5's schema check forbids the key on both jobs and the aggregate's step, and its gate check forbids it on every `test-slice` step, so the design does not depend on which result it yields.
22. A `run:` step with no `shell:` runs under bash on an Ubuntu runner. [unverified: recollection of the workflow-syntax reference; Verification step 4] Under a shell where `[[` is not a command, the aggregate's `if` test is false and the body reaches `exit 1`. A misreading therefore turns every run's `tests` red, which Verification step 6's green run would show.
23. GitHub's "Re-run failed jobs" re-runs only the failed jobs and the jobs that depend on them. [unverified: the platform reviewer's report] It only bounds a setup flake's re-run cost, so nothing in the design depends on it.
24. `/ready-for-review`'s CI-watch resolution first stops on a newer head, then takes the first matching bucket, with `fail` ahead of `cancel`. `fail` dispatches a `/root-cause-analysis` diagnosis on the failing checks, while a `cancel`-only snapshot is reported neutrally. [verified: `claude-skills/skills/ready-for-review/SKILL.md:187-198`]
25. The workflow's concurrency group is `tests-${{ github.ref }}` with `cancel-in-progress: true`, and the workflow runs on `pull_request` and on `push` to `main`. [verified: `tests.yml:11-21`] A newer push therefore cancels an older run on a PR branch and on `main` alike.
26. The plan's `Projected diff:` of about 440 changed lines goes ahead as one PR. [engineer-verified: "Proceed anyway"] The engineer chose that label to the question "This plan projects about 440 changed lines (an estimate; threshold 400). Split into smaller PRs now, or proceed?", and it covers only the size decision.
27. For Verification step 5's failing-leg probe, the session prepares the probe commit and gives the engineer the command to push it, and the engineer pushes it. [engineer-verified: "prepare a failing probe commit and give me the command to push it"] The quote answers "Who should run that probe?" and covers only that commit and its push. It names no branch or PR for the probe (row 33). Row 28 names the gates the session's own commit of it meets.
28. The session's commit and push gates admit a probe commit only through a clean review. [verified: `claude/.claude/hooks/require-code-review.sh:16-24`, `:66-69`, `:114-137`; `claude/.claude/hooks/require-ready-for-review.sh:4-6`, `:24-29`, `:37-45`; `claude-skills/skills/code-review/SKILL.md:401-407`, `:460`]
    - `require-code-review.sh` allows the session's `git commit` only when a `/code-review` marker holds the staged diff's hash. With no merge, rebase, cherry-pick or revert in progress, it also allows a commit whose staged diff is empty, such as `git commit --allow-empty`.
    - `require-ready-for-review.sh` allows the session's push to a branch with an open PR only when the completion marker holds the local HEAD SHA or `/ready-for-review` is running. A `gh pr create` needs that marker even while the skill runs.
    - Both act only on the session's own Bash tool calls.
    - A step that fails CI on purpose matches none of `/code-review`'s closed-list DEFER criteria, so each review of it either removes the probe or stops for the engineer. `render` carries a finding the engineer keeps into the PR body.
29. Fine-grained PATs cannot read the Checks API, so `gh pr checks` 403s under one. `ci-watch.sh` then passes `CI_CHECKS_GH_TOKEN` to `gh` as `GH_TOKEN`, per call. [verified: `docs/scripts.md:217-221`; `claude/.claude/scripts/ci-watch.sh:249-251`]
30. `/ready-for-review` opens the PR ready for review, not as a draft. [verified: `claude-skills/skills/ready-for-review/SKILL.md:154`] Verification steps 4 and 5 therefore run before it opens the PR.
31. No file outside `.claude/plans/` and `agent-reviews/` names `test_ci_watch_workflow_parity`. [verified: Grep for `workflow_parity` across the tree] `conftest.py` and `test_ci_watch.py` name only the install step (row 6).
32. A required context that never reports blocks merge rather than passing. [unverified: the platform reviewer's reading] It matters only if the aggregate is never scheduled on a cancelled run, which Verification step 5 records.
33. The probe lands on a scratch branch and a draft scratch PR against `main`, never on this PR's branch. The scratch branch carries B, an empty commit on H0, under the probe P. The engineer also makes the step's other write calls from their own terminal:
    - pushing B to the scratch branch;
    - opening the draft scratch PR;
    - the re-run and the cancel;
    - closing the scratch PR and deleting its branch.

    The session reads run state with `gh run view` and A's checks with `gh pr checks`. [unverified: the architect's proposal; row 27's quote names neither where the probe lands nor these calls, so step 5's first item asks the engineer to confirm both] Step 5's "Gates and credentials" list gives why the probe stays off this PR's branch.
34. A `pull_request` run's check runs attach to the PR's head commit, not to the PR. `gh pr checks` lists the checks on the PR's current head commit, so a second PR whose head is that same commit would list them too. [unverified: recollection of GitHub's behavior; the repo states neither. `ci-watch.sh:265-274` records the head SHA before it watches, so that a later push reads as superseded, and `:286-287` reads checks by PR number] B gives every scratch run a head commit this PR never carries, so no scratch result reaches this PR under either reading. Under this reading, S's and the cancelled attempt's checks drop out of `gh pr checks <scratch PR>` once P is the scratch head.
35. The plan's `Projected diff:` of about 495 changed lines (range about 465 to 525) goes ahead as one PR. [engineer-verified: "Proceed anyway"] The engineer chose that label to the question "The plan's diff estimate has grown from about 440 to about 495 changed lines (range 465–525) after review rounds added a stricter whole-step check on the CI work steps. Your earlier "Proceed anyway" was given at about 440. Does it still stand at about 495, or should the plan be split now?", and it covers only the size decision. It supersedes row 26's figure.
36. The engineer accepts row 33's scratch-branch, draft-scratch-PR route for Verification step 5, with the session preparing the probe commit and printing each command. [engineer-verified: "Yes, use this route"] The engineer chose that label to the question "Verification step 5 proposes this probe route for you to run from your terminal: a scratch branch with an empty base commit under the failing probe commit, a draft scratch PR, then the push, rerun, cancel and close-and-delete commands. I prepare the probe commit and print each command. Is that route acceptable?", and it covers only that route. Row 33's `[unverified]` tag stands for the claims about GitHub's and the gates' behavior it makes, not for the engineer's acceptance.
37. Apt retries go in a separate follow-up PR, not this one. [engineer-verified: "Separate follow-up PR"] The engineer chose that label to the question "For the apt retries (`Acquire::Retries` drop-in step): should they go into this PR, or be a separate follow-up PR?", and it covers only where the retries go. Out of scope records the follow-up, and row 38 holds its apt claims.
38. The follow-up's mitigation depends on two claims. The first is the `Acquire::Retries` and timeout values in effect on the runner, which the runner image may set itself: the platform reviewer found that `runner-images`' `configure-apt.sh` writes a `zz-retries` drop-in setting `Acquire::Retries "1"` and 15-second timeouts on x64, and says apt spends every retry on one URI before trying the next mirror. The second is whether retries cover the index fetches of `apt-get update --error-on=any`. [unverified: the platform reviewer's fetch of `configure-apt.sh` on `main`, not checked against the revision `ubuntu-24.04` runs, and the platform engineer's recollection on the second claim, both as the session reported them] Nothing in this PR depends on either, and a CI step printing `apt-config dump Acquire::Retries Acquire::http::Timeout Acquire::https::Timeout` would show the effective values.

## Critical files

One `code-writer` dispatch covers every file below. The workflow and its five parsing sites share one structure that separate dispatches would each have to restate (row 2), and `helpers.py` must land with its callers.

- `.github/workflows/tests.yml`: M1, M2. Reuse every existing setup step as it stands, with its SHA-pinned actions and the `ubuntu-24.04` label.
- `claude/.claude/tests/helpers.py`: M4. Reuse `_CI_DETECT_STEP_WORKFLOW` (:54). `extract_ci_detect_step_run_block` (:2709-2719) reads through the new helpers.
- `claude/.claude/scripts/tests/test_ci_watch_workflow_parity.py`: deleted (M5).
- `claude/.claude/hooks/tests/test_ci_path_filter.py`: M5. Reuse `_matching_pytest_line` and `_pytest_step_matching` unchanged.
- `claude/.claude/hooks/tests/test_git_floor.py`: M5 (`_pin_step`, :289-296).
- `claude/.claude/scripts/tests/test_select_tests.py`: M5 (:2243, :2272, :2277, :2345). Reuse `_ci_step_roots` and its negative fixtures unchanged.
- `claude/.claude/hooks/tests/test_ci_workflow_gates.py` (new): M5. Reuse `ci_tests_workflow_jobs()` and `CI_TEST_SLICE_JOB_ID`. Reuse four existing shapes:
  - the check-plus-fixtures shape of `_assert_every_pin_is_the_running_minor` (`test_ci_python_matches_dev_interpreter.py:38-178`);
  - `_steps_running`'s match of a step by its command's leading words (`test_select_tests.py:2262-2263`);
  - `_run_pin_step`'s script file, `shutil.which("bash")` lookup and explicit env (`test_git_floor.py:36`, `:394-429`);
  - the bash-run timeout constant and its comment (`test_git_floor.py:46-47`).
- `README.md`: M6 (:575).
- `CHANGELOG.md`: M6 (Unreleased › Changed).

These files stay unchanged on purpose:
- `install-dev.sh`, `_ci-python-pin-lib.sh`, `test_ci_python_matches_dev_interpreter.py` and `test_ci_python_pin_lib.py` (row 3).
- `conftest.py` and `test_ci_watch.py`, whose comments name the install step, which keeps its name (row 6).
- `ci-watch.sh` and `review-pr-acquire.sh`, which pass every check through (row 2).
- `claude-skills/skills/ready-for-review/SKILL.md`, whose bucket table stays as it is (row 24, Out of scope).

Changed lines per file, counting removed lines as well as added ones:
- about 67 to 70 in `tests.yml`;
- about 18 to 20 in `helpers.py`;
- 80 removed with the parity test file;
- about 245 to 290 in the new test file;
- about 34 to 38 in `test_ci_path_filter.py`;
- about 15 across `test_git_floor.py` and `test_select_tests.py`;
- about 8 to 10 across `README.md` and `CHANGELOG.md`.

The new test file is the main uncertainty. Its figure starts from an itemized 225 to 262 lines for the docstring, constants, both expected literals, both checks, a copy-and-mutate helper, the fixtures and the bash behavior test. It adds about 15 to 20 lines for the four work steps' literal in place of an eight-line pair list, and one fixture. The closest precedent, one check and its fixtures, is 178 lines (`test_ci_python_matches_dev_interpreter.py`). The total runs from about 465 to 525. That is above the about 440 lines row 26's decision covered, so row 35 records the engineer's answer at this figure.

Projected diff: about 495 lines, above `/plan-review`'s 400-line threshold.

## Verification

1. `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py` passes. It selects the full suite on its own, because `helpers.py` is a global trigger (row 7).
2. `scripts/venv-run.sh ruff check claude/.claude/ claude-skills/ plugins/ evals/` is clean. This also catches imports the rework leaves unused.
3. `scripts/venv-run.sh pytest -v claude/.claude/hooks/tests/test_ci_workflow_gates.py` lists every fixture M5 names as its own passing case. These fixtures are the standing check that each structural check reads the real workflow and can fail.
4. Before `/ready-for-review` opens the PR (row 30), check rows 8-11 and 22 against GitHub's own docs:
   - Read the `needs` context in the contexts reference.
   - Read `strategy.fail-fast`, `jobs.<job_id>.if`, `jobs.<job_id>.name` and the default `run` shell in the workflow-syntax reference.
   - Read the status-check functions.
   - Quote the literal lines in the PR body, per `verify-sources`.

   If row 8 or row 10 reads otherwise, stop and re-plan rather than patching. A row the docs leave unstated stays `[unverified]`, and steps 5 and 6 are its only evidence. Row 8's matrix aggregation is expected to be such a row.
5. Before `/ready-for-review` opens the PR (row 30), observe the deny paths on real runs of a draft scratch PR (row 33). This step runs whatever step 4 finds, because row 8 is the one premise whose misreading fails open. H0 is the implementation's head commit.

   The session's conduct in this step:
   - It pushes nothing and runs no `gh` write.
   - It prepares two local commits on a scratch branch: B, an empty commit on H0, and the probe commit P on B (row 27).
   - It prints each engineer command below, for the engineer to run from their own terminal (rows 27 and 33).
   - It reads run state between those commands with `gh run view`, and A's checks with `gh pr checks`.
   - It confirms each printed command's syntax, and its own reads' flags, with that command's `--help` first.

   Procedure:
   - The session first asks the engineer to confirm row 33's protocol: the scratch branch, the draft scratch PR, and each engineer-run push and `gh` write below. It records the reply verbatim. An answer the engineer already gave when the plan was presented serves instead.
   - The session creates a scratch worktree on a new scratch branch at H0, per the `branch-management` skill, so this PR's branch never carries B or P.
     - It commits B with `git commit --allow-empty`. B keeps H0's tree under a new SHA, so no scratch run's head is a commit this PR carries (row 34).
     - It prepares P on B. P adds, right after the detect step, a step with `if: matrix.slice == 'parallel'` and `run: exit 1`.
     - It runs `/code-review` on P's staged change before committing it, as for any commit. Row 28 expects that review to stop for the engineer.
     - Once B and P exist, the session re-anchors in this PR's worktree.
   - The engineer pushes B with `git -C <scratch worktree> push origin <B's full SHA>:refs/heads/<scratch branch>`. The engineer then opens a draft PR from that branch against `main`, titled as a probe not to be merged. Its run is S.
   - Once `gh run view <S> --json jobs` shows both `test-slice` legs in progress, the engineer pushes P with `git -C <scratch worktree> push origin <P's full SHA>:refs/heads/<scratch branch>`. Its run is A, and the concurrency group cancels S (row 25).
   - Nothing else is pushed to the scratch branch until A has completed. Then record:
     - S's conclusion, and `tests`'s conclusion in S or that it never reported;
     - each A leg's conclusion, and `tests`'s conclusion and log line;
     - A's check names and `tests`'s bucket, from `gh pr checks <scratch PR> --json name,bucket`, the fields `ci-watch.sh:286-287` reads;
     - whether `gh pr checks <scratch PR> --required` lists `tests` as required.

     The two `gh pr checks` reads use the session's `gh` login, or `CI_CHECKS_GH_TOKEN` passed per call as `GH_TOKEN` when that login cannot read the Checks API (row 29).
   - The engineer runs `gh run rerun <S>`. Once both legs of that attempt are in progress, the engineer runs `gh run cancel <S>`. Record the same fields as for S, plus the attempt's `headSha` from `gh run view <S> --json headSha`. That SHA should be B, because a re-run is expected to keep its run's commit [unverified: recollection]. Then this attempt is a manual cancel of a run whose tree carries no probe. If the SHA is P, the attempt is not that observation: record it and report.
   - S's and the cancelled attempt's buckets are derived from their conclusions, not read, because `gh pr checks` lists only the scratch head's checks (row 34).
   - The engineer closes the scratch PR unmerged and deletes its branch. The session then removes its own scratch worktree and local scratch branch. Record these for the PR body that `/ready-for-review` authors:
     - the scratch PR's URL, H0, B, P and the run URLs;
     - the engineer's confirmation, quoted;
     - the results above.

   Expected results:
   - In A, the parallel leg fails, the timing leg succeeds, and `tests` fails with a log line naming `failure`. `gh pr checks` puts `tests` in the `fail` bucket, and `--required` lists `tests`.
   - In S and in its cancelled attempt, `tests` fails with a log line naming `cancelled`.

   Stop and re-plan if `tests` passes or shows as skipped in any of the three runs, or if A's timing leg is cancelled. If either `gh pr checks` read lacks `tests`, the context did not bind: stop and report, as in step 6. A cancelled run whose `tests` instead shows as cancelled or never reports keeps the design but changes Accepted costs, so record it and revise them before the PR opens:
   - A cancelled `tests` removes the `/ready-for-review` bucket cost.
   - A `tests` that never reports leaves that head without its required context, which blocks merge rather than passing (row 32). It contradicts the costs' claim that a cancelled run reports `tests` as failed.

   Stalls. Each of these leaves this step without its evidence:
   - a gate denies B or P;
   - `/code-review` removes P or rewrites it so it no longer fails the parallel leg, or the review stops and the engineer does not keep P;
   - the engineer declines row 33's protocol;
   - a `gh run view` read returns 403, or a `gh pr checks` read does after its per-call fallback.

   On a stall, the session stops this step and reports which stall it hit, with any denial or error verbatim. It does not route around the stall, and it does not start `/ready-for-review`. A 403 report also lists the read commands, which the engineer may run under their own login and relay. The PR does not open until A has shown row 8's deny path, a failed leg failing `tests`. The one exception is the engineer saying, in their own words, to open it without that observation, and the PR body then quotes those words.

   If a later head's `tests.yml` parses differently from H0's, repeat this step on that head. The test is one `scripts/venv-run.sh python3 -c` comparison of `yaml.safe_load` over `git show <rev>:.github/workflows/tests.yml` at H0 and at that head, and the session prints its `True` or `False`. A comment-only edit parses the same and needs no repeat.

   Gates and credentials:
   - The session's only writes in this step are B and P, local commits on the scratch branch. `require-code-review.sh` gates both like any commit, and it admits B because B stages nothing (row 28). The session runs no push and no `gh` write, and neither commit reaches this PR's branch.
   - The engineer's pushes and `gh` writes run in the engineer's own terminal, outside the session's gates (row 28). Making them is the engineer's decision (rows 27 and 33). Nothing in this step has the session bypass or work around a gate.
   - Each printed push names its commit's full SHA and the full `refs/heads/` ref, so it cannot carry the probe onto this PR's branch, whatever the engineer's terminal has checked out.
   - The scratch PR opens as a draft, which GitHub does not merge until it is marked ready [unverified: recollection]. `tests.yml:14` sets no `types:` filter on `pull_request`, so the draft runs the same workflow as the real PR.
   - Running the probe on this PR instead would put a deliberately failing commit through both gates' reviews, where each review stops for the engineer and a kept finding lands in the PR body (row 28). It would also need a revert, a check that the reverted tree equals H0's, and a re-run of the real head.
   - The engineer's calls use the engineer's own git and `gh` login. The session's reads use its own.
   - No token is minted or widened to clear a 403, which is a stall (above). G1's protection read stays with the repo admin.
6. On the real PR's run of its final head, check the following:
   - Identify the evidence first. Read the PR's head SHA with `gh pr view <PR> --json headRefOid`, as `ci-watch.sh:269` does, and find the `tests.yml` run whose `headSha` equals it. Every read below names that PR or that run.
   - For each B and P that step 5 used, `git merge-base --is-ancestor <commit> <head>` exits 1, so no scratch run's head is a commit of this PR (row 34). The PR body records each B and P and this head SHA.
   - `gh pr checks <PR>` lists one check per leg plus `tests`, all passing.
   - `gh pr checks <PR> --required` lists `tests` as required and passing. This shows the aggregate satisfies the required context without reading protection settings (G1).
   - While a leg is still running, run `gh pr checks <PR> --json name,bucket` once, the fields `ci-watch.sh` snapshots. Record in the PR body whether `tests` is listed pending or absent. Count it absent only when the same output lists a `test-slice` leg as pending, since an empty list at the start is not evidence. If `tests` is absent, stop and report instead of relying on the watch's `pass` verdict. Absent means `ci-watch.sh` can snapshot both legs as `pass` before `tests` reports, and the ready-for-review `pass` row can then report green early. The background watch starts when the PR opens and the run lasts about 16 minutes, so take this read right after the push. If the window is missed, re-run the read on a `gh run rerun` of the real PR, or state in the PR body that the watch's `pass` for this head is unconfirmed. This extends ledger row `db81ac62c5d6`, whose premise stays `[unverified]` until the read lands.
   - `gh run view <run-id> --json headSha,jobs` shows each leg finishing under 25 minutes, the parallel leg near the ~16 minutes and the timing leg near the ~5 minutes row 12 predicts.
   - The same read lists every step's name and conclusion per leg, and the lists show that each named work step ran in its own leg. Any other conclusion: stop and report. This PR's diff touches paths outside the deny-list (row 19), so detect sets `changed` to `true` in both legs:
     - parallel leg: "Run tests (parallel)" is `success`, and "Run tests (timing, serial)" and "Lint" are `skipped`;
     - timing leg: "Run tests (timing, serial)" and "Lint" are `success`, and "Run tests (parallel)" is `skipped`;
     - both legs: "Skip — no test-relevant changes" is `skipped`.

     A green run alone is silent here, for two reasons:
     - A leg whose gated steps all skip is green by design (M3, M5).
     - M5's gate check compares the workflow's `if:` text to its own expected text, so a mistake made the same way in both passes it.

     Which `-m` expression each pass ran rests on M5's command-keyed pins and the durations above. A collected-count comparison is not added: this PR edits no pytest argv, and `TestTimingMarkerCoverageParity` (`claude/.claude/tests/test_pytest_collection_config.py`) already asserts that the `not timing` and `timing` selections sum to the unfiltered total.

   Confirm the `--required` flag with `gh pr checks --help`, and `--is-ancestor`'s exit codes with `git merge-base --help`, first. The `steps[].conclusion` field name is unconfirmed, and `gh run view --help` is unlikely to list nested job fields. Confirm the field name, and the `skipped` value for a step its `if:` skipped, in step 5's `gh run view <A> --json jobs` output before step 6 relies on them. Run A's timing leg has both kinds of `if:`-skipped step. The `gh pr checks` reads use the session's `gh` login, or `CI_CHECKS_GH_TOKEN` passed per call as `GH_TOKEN` when that login cannot read the Checks API, as `ci-watch.sh` does (row 29). A 403 on the `gh run view` read is step 5's 403 stall. If `tests` never appears or stays pending, the context did not bind: stop and report.

## Out of scope

- Option 1 (raise the 25-minute cap) and option 3 (directory slicing): not requested (row 17).
- Lowering the matrix job's cap, or a per-leg cap through a `timeout-minutes` expression: the Ask's goal is headroom under 25. A hung timing leg already resolves `tests` no later than today's single job does (Approach, Accepted costs). A per-leg value would add a second number to re-derive, for a faster failure on a rare hang.
- Making the `timing` tests tolerate sibling-worker load, so both passes could share one job: that means rewriting those tests, outside this Ask (row 20).
- Branch-protection settings: unchanged, since the required context stays `tests` (G1).
- Branch-protection hardening, a note for the repo admin, who owns the setting (G1):
  - Restrict edits to `.github/workflows/**` and these pin tests with CODEOWNERS or a ruleset path rule.
  - Bind the required `tests` check to the GitHub Actions app, if the protection form supports it.

  M5's checks guard only against an honest regression, because one PR can edit the workflow and its tests together. That exposure is the same today.
- Pinning the keys of `test-slice` steps other than the four work steps, beyond `continue-on-error`, such as a setup step's `env:` or the detect step's own `run:`. Closing them means equality over the whole step list, which copies about 150 lines of workflow into the test and fails on every unrelated step edit. The install step's `run:` joins them: a `run:` that writes `$GITHUB_ENV` or `$GITHUB_PATH` could alter the pytest steps, which takes a crafted edit. The detect step's `if:` is the exception, pinned absent (M5). The pytest and Lint steps are pinned whole (M5).
- Workflow-level keys, and the YAML reader's view of the file: the schema check reads only `jobs`. Each residual is either unchanged from today or needs crafted input, which is beyond the cooperative threat model these checks guard:
  - Workflow-level `permissions` sets `test-slice`'s token scope, and no test reads it. That exposure is the same today, and it does not touch whether `tests` passes.
  - Workflow-level `defaults` and `env` reach the aggregate step. Turning either into a fail-open takes a deliberately crafted shell template or env value.
  - PyYAML keeps the last of duplicate keys [unverified: the security reviewer's report], and GitHub's parser may read such a file differently. Exploiting that takes a deliberately crafted duplicate key, and how GitHub treats one was not checked.
- A `/ready-for-review` bucket-table change, so a cancelled `tests` beside cancelled legs reads as `cancel`: a `SKILL.md` edit is a separate change gated by `/skill-review` (`.claude/rules/review-pipeline-dispatch.md`), and the cost it would remove is a rare false diagnosis (row 24).
- The detect step's direct `${{ github.event.* }}` SHA interpolation (`tests.yml:51-56`), which both legs inherit unchanged: those values are GitHub-issued SHAs, and this plan does not touch the step.
- The `tests.yml` path constants left in `test_ci_path_filter.py:32` (still read at :55), `test_ci_python_matches_dev_interpreter.py:20` and `test_ci_python_pin_lib.py:17`: the path does not change, so this diff adds no drift to them.
- The dated design-decision records that describe the two-pass split, `docs/design-decisions/ci-stays-an-unconditional-full-suite-backstop.md` and `docs/design-decisions/fixture-setup-caching-declined.md`: they are preserved records, and their claims stay true. The steps stay gated, and the `tests` job still always runs and reports (row 13).
- The apt-setup flake mitigation goes in a separate follow-up PR (row 37). That PR measures the current apt failure rate with `gh run list --workflow tests.yml` and `gh run view --log-failed`, recording each failure's error text, and then decides the mitigation. Row 38 lists what it must confirm first. Accepted costs records the exposure this PR adds.
- Caching apt archives with `actions/cache` is not planned:
  - Both index fetches, the plain `apt-get update` and the strict `--error-on=any` one in the "Install the pinned git package" step, stay on the network.
  - An offline install would drop the signed-index verification the git pin relies on, which that step's comments describe.
