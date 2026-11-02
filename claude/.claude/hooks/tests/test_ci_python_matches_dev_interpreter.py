"""CI's Python minor version must equal the interpreter the test suite runs under locally.

The suite runs through `scripts/venv-run.sh` in the shared `.venv`, which `install-dev.sh` builds from
the `python<X.Y>` that `tests.yml` pins and rebuilds when it runs on another minor.
A venv built by hand, or one not yet rebuilt, can still run another minor than CI's pin.
That divergence lets a test pass in one place and fail in the other.
In CI the check holds by construction, because `setup-python` installs the pinned version.
Only `tests.yml` is read, because it is the workflow that runs the suite.
A future CI job that runs a floor interpreter would need the single-pin rule here changed.
"""
from __future__ import annotations

import re
import sys

import pytest
import yaml
from helpers import REPO_ROOT, SETUP_PYTHON_ACTION, setup_python_steps

_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "tests.yml"
_MINOR_VERSION_FORM = re.compile(r"\d+\.\d+")


def _pinned_minor(step_label: str, step: dict) -> str:
    step_inputs = step.get("with")
    assert isinstance(step_inputs, dict) and step_inputs, f"{step_label} has no `with` inputs"
    assert "python-version" in step_inputs, (
        f"{step_label} sets no `python-version`; the pin must be a quoted X.Y there"
    )
    pin = step_inputs["python-version"]
    assert isinstance(pin, str) and _MINOR_VERSION_FORM.fullmatch(pin), (
        f"{step_label} has python-version {pin!r}; the pin must be a quoted X.Y string, "
        "because an unquoted 3.10 loads as the float 3.1 and a patch version or expression is not a minor"
    )
    return pin


def _assert_every_pin_is_the_running_minor(
    workflow: dict, running_minor: str, *, running_executable: str = sys.executable,
) -> None:
    labelled_steps = setup_python_steps(workflow)
    assert labelled_steps, f"no {SETUP_PYTHON_ACTION} step in {_WORKFLOW}"
    pins = {_pinned_minor(step_label, step) for step_label, step in labelled_steps}
    if pins == {running_minor}:
        return
    if len(pins) > 1:
        raise AssertionError(
            f"tests.yml pins several Python minors {sorted(pins)} but this suite runs under one interpreter; "
            "pin one X.Y in every setup-python step."
        )
    (pin,) = pins
    raise AssertionError(
        f"tests.yml pins Python {pin} but this suite runs under {running_minor} ({running_executable}). "
        "Rerun `./install-dev.sh` in the main checkout, which rebuilds a venv on another minor from "
        f"`python{pin}`. To change the pin instead, change tests.yml."
    )


def _workflow_with_steps(*steps: dict) -> dict:
    return {"jobs": {"tests": {"steps": list(steps)}}}


def _setup_python_step() -> dict:
    return {"uses": "actions/setup-python@abc123", "with": {"python-version": "3.14"}}


def test_every_ci_python_pin_is_the_interpreter_running_this_suite() -> None:
    running_minor = f"{sys.version_info.major}.{sys.version_info.minor}"

    _assert_every_pin_is_the_running_minor(yaml.safe_load(_WORKFLOW.read_text()), running_minor)


def test_a_matching_pin_passes() -> None:
    _assert_every_pin_is_the_running_minor(_workflow_with_steps(_setup_python_step()), "3.14")


def test_a_mismatched_pin_names_the_pin_the_running_interpreter_and_the_installer() -> None:
    running_executable = "/fake/checkout/.venv/bin/python"

    with pytest.raises(AssertionError) as failure:
        _assert_every_pin_is_the_running_minor(
            _workflow_with_steps(_setup_python_step()), "3.12", running_executable=running_executable,
        )

    message = str(failure.value)
    assert f"pins Python 3.14 but this suite runs under 3.12 ({running_executable})" in message
    assert "`python3.14`" in message
    assert "install-dev.sh" in message


def test_pins_that_differ_from_each_other_fail_without_an_installer_instruction() -> None:
    workflow = _workflow_with_steps(
        _setup_python_step(), {"uses": "actions/setup-python@abc123", "with": {"python-version": "3.12"}},
    )

    with pytest.raises(AssertionError, match=r"pins several Python minors \['3.12', '3.14'\]") as failure:
        _assert_every_pin_is_the_running_minor(workflow, "3.14")

    assert "install-dev.sh" not in str(failure.value)


def test_a_second_setup_python_step_with_the_wrong_minor_fails_even_when_the_first_matches() -> None:
    workflow = {
        "jobs": {
            "tests": {"steps": [_setup_python_step()]},
            "lint": {"steps": [{"uses": "actions/setup-python@abc123", "with": {"python-version": "3.12"}}]},
        },
    }

    with pytest.raises(AssertionError, match="pins several Python minors"):
        _assert_every_pin_is_the_running_minor(workflow, "3.14")


@pytest.mark.parametrize(
    "workflow",
    [
        pytest.param({}, id="no-jobs-key"),
        pytest.param({"jobs": {"tests": {}}}, id="job-without-steps"),
        pytest.param(_workflow_with_steps({"uses": "actions/checkout@abc123"}), id="no-setup-python-step"),
        pytest.param(_workflow_with_steps({"run": "echo hi"}), id="step-without-uses"),
        pytest.param(_workflow_with_steps({"uses": "actions/setup-python-extra@abc123"}), id="similarly-named-action"),
    ],
)
def test_a_workflow_without_a_setup_python_step_fails_once_naming_the_action(workflow: dict) -> None:
    with pytest.raises(AssertionError) as failure:
        _assert_every_pin_is_the_running_minor(workflow, "3.14")

    assert f"no {SETUP_PYTHON_ACTION} step" in str(failure.value)


@pytest.mark.parametrize(
    ("step", "problem"),
    [
        pytest.param({"uses": "actions/setup-python@abc123"}, "has no `with` inputs", id="missing-with"),
        pytest.param({"uses": "actions/setup-python@abc123", "with": {}}, "has no `with` inputs", id="empty-with"),
        pytest.param({"uses": "actions/setup-python@abc123", "with": None}, "has no `with` inputs", id="null-with"),
        pytest.param({"uses": "actions/setup-python@abc123", "with": "3.14"}, "has no `with` inputs", id="with-not-a-mapping"),
        pytest.param(
            {"uses": "actions/setup-python@abc123", "with": {"python-version-file": ".python-version"}},
            "sets no `python-version`", id="python-version-file-only",
        ),
    ],
)
def test_a_setup_python_step_without_a_python_version_fails_naming_the_step(step: dict, problem: str) -> None:
    with pytest.raises(AssertionError) as failure:
        _assert_every_pin_is_the_running_minor(_workflow_with_steps(step), "3.14")

    message = str(failure.value)
    assert "job 'tests' step 1 (actions/setup-python@abc123)" in message
    assert problem in message


@pytest.mark.parametrize(
    "workflow_text",
    [
        pytest.param("python-version: 3.10", id="unquoted-float-loads-as-3.1"),
        pytest.param("python-version: 3.14", id="unquoted-float-without-trailing-zero"),
        pytest.param("python-version: '3.14.1'", id="patch-version"),
        pytest.param("python-version: '3.x'", id="wildcard-minor"),
        pytest.param("python-version: '3'", id="major-only"),
        pytest.param("python-version: ${{ matrix.python-version }}", id="expression"),
    ],
)
def test_a_python_version_that_is_not_a_quoted_minor_fails_naming_the_step_without_an_installer_instruction(
    workflow_text: str,
) -> None:
    workflow = yaml.safe_load(
        "jobs:\n  tests:\n    steps:\n      - uses: actions/setup-python@abc123\n        with:\n          "
        + workflow_text + "\n",
    )

    with pytest.raises(AssertionError) as failure:
        _assert_every_pin_is_the_running_minor(workflow, "3.14")

    message = str(failure.value)
    assert "job 'tests' step 1 (actions/setup-python@abc123)" in message
    assert "must be a quoted X.Y string" in message
    assert "install-dev.sh" not in message
