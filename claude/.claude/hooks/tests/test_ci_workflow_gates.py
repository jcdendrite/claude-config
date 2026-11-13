"""The required `tests` check in tests.yml must fail whenever a test pass does not succeed.

The workflow runs each pytest pass in its own `test-slice` matrix leg.
The job id `tests` names the aggregate job that the tests.yml header ties to the required-check context.
Two structural checks read the parsed workflow and a behavior test executes the aggregate's `run:` body.
The invariants:

- both jobs' keys equal one expected literal, apart from `test-slice`'s steps and the aggregate step's `run:` body;
- each pytest step's `if:` is the install step's condition plus its own leg test, because conftest.py's
  require_direnv() hard-fails a pytest step that runs without the install step;
- the four work steps, the install step keyed by its name, the pytest steps keyed by their `-m` marker
  expression and the Lint step keyed by `ruff check`, equal one expected literal, so a changed command,
  `shell:` or `env:` cannot turn a failed pass green;
- the install step's `run:` is the one exemption from that literal, so an install-command edit needs no test
  edit, while its `name`, `if:` and every other key stay pinned;
- exactly one step has the id `detect` and it carries no `if:`, so no leg can skip its change detection;
- every step whose `if:` reads `steps.detect` comes after the detect step, so the condition sees its output;
- Lint runs in the `timing` leg only, so it reports once;
- the legs named across the steps' `if:` conditions equal the matrix legs;
- no `test-slice` step carries `continue-on-error`;
- the aggregate step's `run:` body passes only on `success`.

Both literals are change detectors: a deliberate edit to either job's keys or to a work step's command
(other than the install command, which the literal omits) updates them in the same change.
Each structural check also runs against permanent negative fixtures, which show that the check reads
the real workflow and can fail. A positive control, an install-command edit that must not raise,
shows that the install step's `run:` exemption is live.

These tests prove the workflow's parsed structure and the aggregate's shell comparison. How GitHub Actions
evaluates `needs.test-slice.result` for a matrix with a failed or cancelled leg, and whether `always()`
schedules the aggregate on a cancelled run, is covered only by observing a real run.
"""
from __future__ import annotations

import copy
import re
import shlex
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
from helpers import CI_TEST_SLICE_JOB_ID, ci_test_slice_job, ci_tests_workflow_jobs

_AGGREGATE_JOB_ID = "tests"
_INSTALL_STEP_NAME = "Install stow and direnv"
_PIN_GIT_STEP_ID = "pin-git"
_DETECT_STEP_ID = "detect"
_BASH = shutil.which("bash") or "bash"

# Bounds a hung local bash run; no vendor value applies.
_BASH_RUN_TIMEOUT_SECONDS = 10

_SHARED_GATE_CONDITION = "steps.detect.outputs.changed == 'true'"
_PARALLEL_LEG_CONDITION = f"{_SHARED_GATE_CONDITION} && matrix.slice == 'parallel'"
_TIMING_LEG_CONDITION = f"{_SHARED_GATE_CONDITION} && matrix.slice == 'timing'"

_SCHEMA_MESSAGE = "jobs differ from the expected literal"
_FAIL_FAST_MESSAGE = "test-slice must set fail-fast to the boolean false"
_WORK_STEPS_MESSAGE = "work steps differ from the expected list"
_DETECT_STEP_MESSAGE = "the detect step must be the only step with its id and must carry no `if:`"
_DETECT_ORDER_MESSAGE = "every step whose `if:` reads steps.detect must come after the detect step"
_LEG_SET_MESSAGE = "legs named by step conditions differ from the matrix legs"
_CONTINUE_ON_ERROR_MESSAGE = "test-slice steps must not set continue-on-error"

# Every key and value of both jobs except test-slice's steps and the aggregate step's `run:` body.
_EXPECTED_JOBS_WITHOUT_STEP_BODIES = {
    CI_TEST_SLICE_JOB_ID: {
        "runs-on": "ubuntu-24.04",
        "strategy": {"fail-fast": False, "matrix": {"slice": ["parallel", "timing"]}},
        "timeout-minutes": 30,
        "env": {"PYTEST_XDIST_AUTO_NUM_WORKERS": "${{ vars.PYTEST_XDIST_AUTO_NUM_WORKERS }}"},
    },
    _AGGREGATE_JOB_ID: {
        "needs": CI_TEST_SLICE_JOB_ID,
        "if": "always()",
        "runs-on": "ubuntu-24.04",
        "timeout-minutes": 5,
        "permissions": {},
        "steps": [
            {
                "name": "Require every test-slice leg to succeed",
                "env": {"SLICE_RESULT": "${{ needs.test-slice.result }}"},
            },
        ],
    },
}

# (identity, step), sorted by identity.
_EXPECTED_WORK_STEPS = [
    (
        _INSTALL_STEP_NAME,
        {
            "name": _INSTALL_STEP_NAME,
            "if": _SHARED_GATE_CONDITION,
        },
    ),
    (
        "not timing",
        {
            "name": "Run tests (parallel)",
            "if": _PARALLEL_LEG_CONDITION,
            "run": 'pytest claude/.claude/ claude-skills/ plugins/ evals/ -v -m "not timing"',
        },
    ),
    (
        "ruff check",
        {
            "name": "Lint",
            "if": _TIMING_LEG_CONDITION,
            "run": "ruff check claude/.claude/ claude-skills/ plugins/ evals/",
        },
    ),
    (
        "timing",
        {
            "name": "Run tests (timing, serial)",
            "if": _TIMING_LEG_CONDITION,
            "run": "pytest claude/.claude/ claude-skills/ plugins/ evals/ -v -m timing -n0",
        },
    ),
]

_LEG_TEST = re.compile(r"matrix\.slice == '([^']+)'")


def _without_step_bodies(jobs: dict) -> dict:
    """A copy of `jobs` minus test-slice's steps and the aggregate steps' `run:` bodies."""
    stripped = copy.deepcopy(jobs)
    stripped.get(CI_TEST_SLICE_JOB_ID, {}).pop("steps", None)
    for step in stripped.get(_AGGREGATE_JOB_ID, {}).get("steps", []):
        step.pop("run", None)
    return stripped


def _assert_jobs_match_expected_schema(jobs: dict) -> None:
    assert _without_step_bodies(jobs) == _EXPECTED_JOBS_WITHOUT_STEP_BODIES, (
        f"{_SCHEMA_MESSAGE}. Update the literal only with an edit that still fails `tests` "
        "whenever a test-slice leg does not succeed."
    )
    # `0 == False` in Python, so the equality above accepts `fail-fast: 0`.
    assert jobs[CI_TEST_SLICE_JOB_ID]["strategy"]["fail-fast"] is False, (
        f"{_FAIL_FAST_MESSAGE}: a failing leg would otherwise cancel the other and suppress its report"
    )


def _pytest_marker_expression(run: str) -> str:
    try:
        tokens = shlex.split(run)
    except ValueError:
        return "pytest (unparsable)"
    if "-m" in tokens and tokens.index("-m") + 1 < len(tokens):
        return tokens[tokens.index("-m") + 1]
    return "pytest (no -m)"


def _work_step_identity(step: dict) -> str | None:
    """The install step's name, or the command-derived identity of a pytest or lint step; None for any other step.

    A pytest or lint step is found by its command, not its name, so a rename cannot hide one.
    """
    if step.get("name") == _INSTALL_STEP_NAME:
        return _INSTALL_STEP_NAME
    words = str(step.get("run", "")).split()
    if words[:1] == ["pytest"]:
        return _pytest_marker_expression(step["run"])
    if words[:2] == ["ruff", "check"]:
        return "ruff check"
    return None


def _step_as_compared(identity: str, step: dict) -> dict:
    """The step as the pair-list equality sees it: the install step minus its `run:`, any other step whole."""
    if identity != _INSTALL_STEP_NAME:
        return step
    return {key: value for key, value in step.items() if key != "run"}


def _assert_work_steps_gate_their_legs(test_slice_job: dict) -> None:
    steps = test_slice_job["steps"]
    work_steps = sorted(
        (
            (identity, _step_as_compared(identity, step))
            for step in steps
            if (identity := _work_step_identity(step)) is not None
        ),
        key=lambda pair: pair[0],
    )
    assert work_steps == _EXPECTED_WORK_STEPS, (
        f"{_WORK_STEPS_MESSAGE}; found {work_steps}. Each pytest step's `if:` must be the install step's "
        "condition plus its own leg test, and Lint must run in the timing leg only. A deliberate edit to a work "
        "step's command (pytest roots, flags, marker expression), a step name, or a step `if:` legitimately "
        "updates the expected literal."
    )

    detect_steps = [step for step in steps if step.get("id") == _DETECT_STEP_ID]
    assert len(detect_steps) == 1, (
        f"{_DETECT_STEP_MESSAGE}: found {len(detect_steps)} steps with id {_DETECT_STEP_ID!r}. "
        "Zero or two matches would otherwise read as a step with no `if:`."
    )
    assert "if" not in detect_steps[0], (
        f"{_DETECT_STEP_MESSAGE}: found `if: {detect_steps[0]['if']}`. "
        "A leg that skips detection runs none of its gated steps and finishes green."
    )
    detect_position = next(position for position, step in enumerate(steps) if step.get("id") == _DETECT_STEP_ID)
    for step in steps[:detect_position]:
        assert "steps.detect" not in str(step.get("if", "")), (
            f"{_DETECT_ORDER_MESSAGE}: step {step.get('name', step.get('id'))!r} runs before the "
            f"step with id {_DETECT_STEP_ID!r}. An output read before its step runs is empty, so the gated step skips."
        )

    legs_named_by_conditions = {
        leg
        for step in steps
        if isinstance(step.get("if"), str)
        for leg in _LEG_TEST.findall(step["if"])
    }
    matrix_legs = set(test_slice_job["strategy"]["matrix"]["slice"])
    assert legs_named_by_conditions == matrix_legs, (
        f"{_LEG_SET_MESSAGE}: conditions name {sorted(legs_named_by_conditions)}, matrix has {sorted(matrix_legs)}. "
        "A leg whose gated steps all skip finishes green."
    )

    steps_with_continue_on_error = [step.get("name", step.get("id")) for step in steps if "continue-on-error" in step]
    assert not steps_with_continue_on_error, f"{_CONTINUE_ON_ERROR_MESSAGE}: {steps_with_continue_on_error}"


def _step_with_name(job: dict, name: str) -> dict:
    return next(step for step in job["steps"] if step.get("name") == name)


def _step_with_id(job: dict, step_id: str) -> dict:
    return next(step for step in job["steps"] if step.get("id") == step_id)


def _parallel_and_timing_pytest_steps(job: dict) -> tuple[dict, dict]:
    return _step_with_name(job, "Run tests (parallel)"), _step_with_name(job, "Run tests (timing, serial)")


def _swap_pytest_run_bodies(job: dict) -> None:
    parallel_step, timing_step = _parallel_and_timing_pytest_steps(job)
    parallel_step["run"], timing_step["run"] = timing_step["run"], parallel_step["run"]


def _remove_parallel_step_leg_test(job: dict) -> None:
    parallel_step, _ = _parallel_and_timing_pytest_steps(job)
    parallel_step["if"] = _SHARED_GATE_CONDITION


def _append_or_true_to_timing_step(job: dict) -> None:
    _, timing_step = _parallel_and_timing_pytest_steps(job)
    timing_step["run"] += " || true"


def _gate_detect_step_to_the_parallel_leg(job: dict) -> None:
    _step_with_id(job, _DETECT_STEP_ID)["if"] = "matrix.slice == 'parallel'"


def _rename_detect_step_id(job: dict) -> None:
    _step_with_id(job, _DETECT_STEP_ID)["id"] = "detect-renamed"


def _add_second_step_with_the_detect_id(job: dict) -> None:
    # No `run:` and no install name, so the work-step pair list ignores it and the detect assertion is reached.
    job["steps"].append({"name": "Second detect", "id": _DETECT_STEP_ID})


def _move_detect_step_to_the_end(job: dict) -> None:
    job["steps"].append(job["steps"].pop(job["steps"].index(_step_with_id(job, _DETECT_STEP_ID))))


def _move_detect_step_after_the_first_gated_step(job: dict) -> None:
    steps = job["steps"]
    detect_step = steps.pop(steps.index(_step_with_id(job, _DETECT_STEP_ID)))
    first_gated_position = next(
        position for position, step in enumerate(steps) if "steps.detect" in str(step.get("if", ""))
    )
    steps.insert(first_gated_position + 1, detect_step)


def _set_shell_on_install_step(job: dict) -> None:
    _step_with_name(job, _INSTALL_STEP_NAME)["shell"] = "bash"


def _change_install_step_run_body(job: dict) -> None:
    _step_with_name(job, _INSTALL_STEP_NAME)["run"] = "sudo apt-get install -y stow direnv\n"


def _add_third_leg_without_a_step(job: dict) -> None:
    job["strategy"]["matrix"]["slice"].append("extra")


def _set_continue_on_error_on_pin_git_step(job: dict) -> None:
    _step_with_id(job, _PIN_GIT_STEP_ID)["continue-on-error"] = True


def _use_job_status_as_slice_result(jobs: dict) -> None:
    jobs[_AGGREGATE_JOB_ID]["steps"][0]["env"]["SLICE_RESULT"] = "${{ job.status }}"


def _skip_aggregate_on_cancelled_run(jobs: dict) -> None:
    jobs[_AGGREGATE_JOB_ID]["if"] = "${{ !cancelled() }}"


def _name_test_slice_like_the_aggregate(jobs: dict) -> None:
    jobs[CI_TEST_SLICE_JOB_ID]["name"] = _AGGREGATE_JOB_ID


def _set_fail_fast_to_zero(jobs: dict) -> None:
    jobs[CI_TEST_SLICE_JOB_ID]["strategy"]["fail-fast"] = 0


def test_the_real_workflow_jobs_match_the_expected_schema() -> None:
    _assert_jobs_match_expected_schema(ci_tests_workflow_jobs())


@pytest.mark.parametrize(
    ("mutate", "expected_message"),
    [
        pytest.param(_use_job_status_as_slice_result, _SCHEMA_MESSAGE, id="aggregate-reads-job-status"),
        pytest.param(_skip_aggregate_on_cancelled_run, _SCHEMA_MESSAGE, id="aggregate-skips-on-cancelled-run"),
        pytest.param(_name_test_slice_like_the_aggregate, _SCHEMA_MESSAGE, id="test-slice-carries-a-name"),
        pytest.param(_set_fail_fast_to_zero, _FAIL_FAST_MESSAGE, id="fail-fast-is-zero-not-false"),
    ],
)
def test_the_schema_check_rejects_a_workflow_that_could_let_tests_pass_a_failed_leg(
    mutate: Callable[[dict], None], expected_message: str,
) -> None:
    jobs = copy.deepcopy(ci_tests_workflow_jobs())
    mutate(jobs)

    with pytest.raises(AssertionError, match=re.escape(expected_message)):
        _assert_jobs_match_expected_schema(jobs)


def test_the_real_test_slice_job_gates_each_work_step_to_its_leg() -> None:
    _assert_work_steps_gate_their_legs(ci_test_slice_job())


@pytest.mark.parametrize(
    ("mutate", "expected_message"),
    [
        pytest.param(_remove_parallel_step_leg_test, _WORK_STEPS_MESSAGE, id="parallel-step-loses-its-leg-test"),
        pytest.param(_swap_pytest_run_bodies, _WORK_STEPS_MESSAGE, id="pytest-run-bodies-swapped"),
        pytest.param(_append_or_true_to_timing_step, _WORK_STEPS_MESSAGE, id="timing-step-swallows-failure-with-or-true"),
        pytest.param(_set_shell_on_install_step, _WORK_STEPS_MESSAGE, id="install-step-gains-a-shell-key"),
        pytest.param(_gate_detect_step_to_the_parallel_leg, _DETECT_STEP_MESSAGE, id="detect-step-gains-an-if"),
        pytest.param(_rename_detect_step_id, _DETECT_STEP_MESSAGE, id="detect-step-id-renamed"),
        pytest.param(_add_second_step_with_the_detect_id, _DETECT_STEP_MESSAGE, id="detect-step-id-duplicated"),
        pytest.param(_move_detect_step_to_the_end, _DETECT_ORDER_MESSAGE, id="detect-step-moved-after-its-readers"),
        pytest.param(
            _move_detect_step_after_the_first_gated_step, _DETECT_ORDER_MESSAGE, id="detect-step-moved-one-step-too-late",
        ),
        pytest.param(_add_third_leg_without_a_step, _LEG_SET_MESSAGE, id="matrix-leg-without-a-gated-step"),
        pytest.param(
            _set_continue_on_error_on_pin_git_step, _CONTINUE_ON_ERROR_MESSAGE, id="continue-on-error-on-a-setup-step",
        ),
    ],
)
def test_the_gate_check_rejects_a_workflow_where_a_failed_pass_could_report_green(
    mutate: Callable[[dict], None], expected_message: str,
) -> None:
    test_slice_job = copy.deepcopy(ci_test_slice_job())
    mutate(test_slice_job)

    with pytest.raises(AssertionError, match=re.escape(expected_message)):
        _assert_work_steps_gate_their_legs(test_slice_job)


def test_the_gate_check_accepts_a_changed_install_step_run_body() -> None:
    test_slice_job = copy.deepcopy(ci_test_slice_job())
    _change_install_step_run_body(test_slice_job)

    _assert_work_steps_gate_their_legs(test_slice_job)


def _aggregate_run_body() -> str:
    aggregate_steps = ci_tests_workflow_jobs()[_AGGREGATE_JOB_ID]["steps"]
    assert len(aggregate_steps) == 1, f"expected the aggregate to hold one step, found {len(aggregate_steps)}"
    return aggregate_steps[0]["run"]


def _run_aggregate_step(tmp_path: Path, slice_result: str | None) -> subprocess.CompletedProcess:
    """Run the aggregate step's real `run:` body under bash with only PATH and, when given, SLICE_RESULT in its env.

    The env is never copied from os.environ, so an exported SLICE_RESULT or BASH_ENV cannot mask a case.
    PATH names an empty directory, so the body can use only shell builtins.
    """
    script_path = tmp_path / "aggregate-step.sh"
    script_path.write_text(_aggregate_run_body())
    empty_path_dir = tmp_path / "empty-path"
    empty_path_dir.mkdir()
    env = {"PATH": str(empty_path_dir)}
    if slice_result is not None:
        env["SLICE_RESULT"] = slice_result
    return subprocess.run(
        [_BASH, str(script_path)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=_BASH_RUN_TIMEOUT_SECONDS,
    )


def test_the_aggregate_step_exits_zero_when_every_leg_succeeded(tmp_path: Path) -> None:
    result = _run_aggregate_step(tmp_path, "success")

    assert result.returncode == 0, f"stderr: {result.stderr!r}"


@pytest.mark.parametrize("slice_result", ["failure", "cancelled", "skipped", ""])
def test_the_aggregate_step_exits_one_naming_any_result_other_than_success(tmp_path: Path, slice_result: str) -> None:
    result = _run_aggregate_step(tmp_path, slice_result)

    assert result.returncode == 1
    assert f"finished as '{slice_result}'" in result.stderr


def test_the_aggregate_step_fails_when_the_slice_result_is_unset(tmp_path: Path) -> None:
    result = _run_aggregate_step(tmp_path, None)

    assert result.returncode == 1
    assert "SLICE_RESULT" in result.stderr
