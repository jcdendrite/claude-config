"""Parity test for .github/workflows/tests.yml's triplicated `if:` gate.

Three steps in the `tests` job — "Install stow and direnv", "Run tests
(parallel)", "Run tests (timing, serial)" — must all gate on the same
`steps.detect.outputs.changed == 'true'` condition for
conftest.py's require_direnv() hard-fail guarantee to hold: direnv is
installed only when that condition is true, so a test reachable whenever
it's true must run in exactly the steps that condition also gates.
Currently only cross-reference comments on each step enforce this; this
test makes the invariant machine-checked.

The three `if:` values are not fully textually identical: the timing step
adds `!cancelled() &&` so a parallel-pass failure doesn't suppress its own
report (see that step's own comment in tests.yml). This test therefore
asserts the shared gate condition is present, textually identical, in all
three, rather than asserting whole-string equality across all three
`if:` fields.
"""
from __future__ import annotations

import yaml
from helpers import REPO_ROOT

_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "tests.yml"

_DIRENV_GATED_STEP_NAMES = (
    "Install stow and direnv",
    "Run tests (parallel)",
    "Run tests (timing, serial)",
)

_SHARED_GATE_CONDITION = "steps.detect.outputs.changed == 'true'"

_TIMING_STEP_IF_PREFIX = "${{ !cancelled() && "
_TIMING_STEP_IF_SUFFIX = " }}"


def _tests_job_steps() -> list[dict]:
    workflow = yaml.safe_load(_WORKFLOW_PATH.read_text())
    return workflow["jobs"]["tests"]["steps"]


def _if_values_by_name(step_names: tuple[str, ...]) -> dict[str, str]:
    steps = _tests_job_steps()
    by_name = {step["name"]: step.get("if", "") for step in steps if step.get("name") in step_names}
    missing = set(step_names) - set(by_name)
    assert not missing, f"step(s) not found in tests.yml's tests job: {missing}"
    return by_name


def test_direnv_gated_steps_all_carry_the_shared_gate_condition():
    if_values = _if_values_by_name(_DIRENV_GATED_STEP_NAMES)
    for name, if_value in if_values.items():
        assert _SHARED_GATE_CONDITION in if_value, (
            f"{name!r}'s if: ({if_value!r}) is missing the shared gate condition "
            f"{_SHARED_GATE_CONDITION!r} — the three direnv-dependent steps must "
            "stay in sync for require_direnv()'s CI hard-fail guarantee to hold"
        )


def test_install_and_parallel_steps_are_gated_by_nothing_else():
    # "Install stow and direnv" and "Run tests (parallel)" carry no extra
    # conjunct today (unlike the timing step's documented !cancelled()
    # addition) -- pins that the two stay byte-identical to each other, so a
    # future edit adding an extra condition to only one of them is caught
    # here rather than only via the substring check above.
    if_values = _if_values_by_name(("Install stow and direnv", "Run tests (parallel)"))
    assert if_values["Install stow and direnv"] == if_values["Run tests (parallel)"] == _SHARED_GATE_CONDITION


def test_timing_step_is_gated_by_nothing_else_but_the_documented_cancelled_check():
    # Pins the timing step's `if:` to the exact documented `!cancelled() &&`
    # wrapper around the shared gate condition -- an edit that negates or
    # OR-weakens the condition (e.g. `!(...)` or `always() || ...`) would
    # still contain the shared-condition substring and pass the check above
    # undetected, but fails this full-string pin.
    if_values = _if_values_by_name(("Run tests (timing, serial)",))
    assert if_values["Run tests (timing, serial)"] == (
        _TIMING_STEP_IF_PREFIX + _SHARED_GATE_CONDITION + _TIMING_STEP_IF_SUFFIX
    )
