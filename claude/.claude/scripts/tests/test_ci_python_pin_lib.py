"""Direct unit tests for _ci-python-pin-lib.sh, sourced standalone.

install-dev.sh reads the Python minor CI pins from .github/workflows/tests.yml
through read_ci_python_pin. These tests hold the parsing matrix, so
test_install_dev.py needs only one subprocess case per branch.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml
from helpers import REPO_ROOT, setup_python_steps

_LIB = Path(__file__).parent.parent / "_ci-python-pin-lib.sh"
_REAL_WORKFLOW = ".github/workflows/tests.yml"


def _read_pin(workflow_file: Path | str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    script = f'set -euo pipefail\n. "{_LIB}"\nread_ci_python_pin "$1"\n'
    return subprocess.run(
        ["bash", "-c", script, "bash", str(workflow_file)],
        capture_output=True,
        text=True,
        check=False,
        cwd=cwd,
    )


def _workflow(tmp_path: Path, text: str) -> Path:
    workflow_file = tmp_path / "tests.yml"
    workflow_file.write_text(text)
    return workflow_file


@pytest.mark.parametrize(
    ("workflow_text", "expected_pin"),
    [
        pytest.param("        with:\n          python-version: '3.14'\n", "3.14", id="single-quoted"),
        pytest.param('          python-version: "3.12"\n', "3.12", id="double-quoted"),
        pytest.param("          python-version: '3.9'\n", "3.9", id="single-digit-minor"),
        pytest.param("          python-version: '3.10'\n", "3.10", id="two-digit-minor-keeps-both-digits"),
        pytest.param("python-version: '3.14'\n", "3.14", id="unindented"),
        pytest.param("          python-version:   '3.14'   # CI pin\n", "3.14", id="spacing-and-trailing-comment"),
        pytest.param(
            "  a:\n    python-version: '3.14'\n  b:\n    python-version: '3.14'\n", "3.14",
            id="same-pin-repeated-is-one-distinct-value",
        ),
        pytest.param(
            "          # python-version: '3.11'\n          python-version: '3.14'\n", "3.14",
            id="commented-out-line-is-ignored",
        ),
        pytest.param(
            "          python-version: 3.10\n          python-version: '3.14'\n", "3.14",
            id="unquoted-line-is-ignored",
        ),
    ],
)
def test_a_single_quoted_minor_is_printed(tmp_path: Path, workflow_text: str, expected_pin: str) -> None:
    result = _read_pin(_workflow(tmp_path, workflow_text))

    assert result.returncode == 0, result.stderr
    assert result.stdout == f"{expected_pin}\n"
    assert result.stderr == ""


@pytest.mark.parametrize(
    ("workflow_text", "problem"),
    [
        pytest.param("jobs: {}\n", "no quoted", id="no-python-version-line"),
        pytest.param("          python-version: 3.14\n", "no quoted", id="only-an-unquoted-line"),
        pytest.param("          # python-version: '3.14'\n", "no quoted", id="only-a-commented-out-line"),
        pytest.param(
            "          python-version: '3.12'\n          python-version: '3.14'\n", "several Python versions",
            id="two-distinct-pins",
        ),
        pytest.param("          python-version: '3.14.1'\n", "not a minor version X.Y", id="patch-version"),
        pytest.param("          python-version: '3.x'\n", "not a minor version X.Y", id="wildcard-minor"),
        pytest.param("          python-version: '3'\n", "not a minor version X.Y", id="major-only"),
        pytest.param("          python-version: ''\n", "no quoted", id="empty-pin-is-no-pin"),
        pytest.param("          python-version: '3.14 '\n", "not a minor version X.Y", id="trailing-space-in-pin"),
    ],
)
def test_anything_but_exactly_one_minor_fails_with_an_actionable_error(
    tmp_path: Path, workflow_text: str, problem: str,
) -> None:
    result = _read_pin(_workflow(tmp_path, workflow_text))

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.startswith("ERROR: ")
    assert problem in result.stderr


def test_an_unreadable_workflow_file_fails_naming_the_file(tmp_path: Path) -> None:
    missing_file = tmp_path / "absent.yml"

    result = _read_pin(missing_file)

    assert result.returncode == 1
    assert result.stdout == ""
    assert f"ERROR: cannot read {missing_file}" in result.stderr


def test_the_real_workflow_pin_equals_the_pin_yaml_loads_from_it() -> None:
    workflow = yaml.safe_load((REPO_ROOT / _REAL_WORKFLOW).read_text())
    yaml_pins = {step["with"]["python-version"] for _, step in setup_python_steps(workflow)}
    assert len(yaml_pins) == 1, yaml_pins

    result = _read_pin(_REAL_WORKFLOW, cwd=REPO_ROOT)

    assert result.returncode == 0, result.stderr
    assert result.stdout == f"{next(iter(yaml_pins))}\n"
