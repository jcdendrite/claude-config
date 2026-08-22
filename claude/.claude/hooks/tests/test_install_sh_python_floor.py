"""Tests for install.sh's Python >= 3.11 preflight check -- the hard-fail
that makes the floor `parse-manifest-dependencies.py`'s docstring states
an enforced, install-time requirement rather than a silent runtime
assumption.
"""
from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

_INSTALL_SH = Path(__file__).resolve().parents[4] / "install.sh"
_BASH = shutil.which("bash") or "/bin/bash"

_FIXTURE_START = "# INSTALL_TEST_FIXTURE: python-floor-check — start\n"
_FIXTURE_END = "# INSTALL_TEST_FIXTURE: python-floor-check — end"

_PRESENCE_FIXTURE_START = "# INSTALL_TEST_FIXTURE: presence-check — start\n"
_PRESENCE_FIXTURE_END = "# INSTALL_TEST_FIXTURE: presence-check — end"


def _extract_python_floor_check_block() -> str:
    """Same marker-delimited extraction strategy as the other
    test_install_sh_*.py files."""
    install_text = _INSTALL_SH.read_text()
    start = install_text.find(_FIXTURE_START)
    assert start != -1, f"{_FIXTURE_START!r} not found in {_INSTALL_SH}"
    end = install_text.find(_FIXTURE_END, start)
    assert end != -1, f"{_FIXTURE_END!r} not found after start marker in {_INSTALL_SH}"
    block = install_text[start + len(_FIXTURE_START) : end]
    assert "version_info" in block, (
        f"extracted block is missing the version check; markers in {_INSTALL_SH} "
        f"are probably misplaced. Got: {block!r}"
    )
    return block


def _make_fake_bin_dir(tmp_path: Path, *, meets_floor: bool) -> Path:
    """A PATH directory holding a fake `python3` that exits 0 for any
    invocation except a `-c` call whose code mentions `version_info` (the
    floor check itself), which exits according to `meets_floor` -- this
    stands in for a real interpreter's `sys.version_info` comparison
    without needing an actual below-floor Python binary to test against."""
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    fake_python3 = bin_dir / "python3"
    version_exit = "0" if meets_floor else "1"
    fake_python3.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "-c" ]; then\n'
        '  case "$2" in\n'
        f'    *version_info*) exit {version_exit} ;;\n'
        "  esac\n"
        "fi\n"
        "exit 0\n"
    )
    fake_python3.chmod(fake_python3.stat().st_mode | stat.S_IEXEC)
    return bin_dir


def _extract_presence_check_block() -> str:
    """Marker-delimited extraction of install.sh's presence-check loop
    (the `missing` array), same strategy as `_extract_python_floor_check_block`."""
    install_text = _INSTALL_SH.read_text()
    start = install_text.find(_PRESENCE_FIXTURE_START)
    assert start != -1, f"{_PRESENCE_FIXTURE_START!r} not found in {_INSTALL_SH}"
    end = install_text.find(_PRESENCE_FIXTURE_END, start)
    assert end != -1, f"{_PRESENCE_FIXTURE_END!r} not found after start marker in {_INSTALL_SH}"
    return install_text[start + len(_PRESENCE_FIXTURE_START) : end]


def _make_fake_bin_dir_missing_only_python3(tmp_path: Path) -> Path:
    """A PATH directory with a stub for every tool install.sh's
    presence-check loop requires except `python3`, which is absent."""
    bin_dir = tmp_path / "fakebin-no-python3"
    bin_dir.mkdir()
    for tool in ("stow", "git", "gh", "jq", "sha256sum", "claude"):
        stub = bin_dir / tool
        stub.write_text("#!/bin/sh\nexit 0\n")
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    return bin_dir


def _extract_c_argument(block: str) -> str:
    """Pull the literal `-c` argument out of the extracted block (not a
    hand-retyped copy), so a test that runs it proves the real comparison,
    not a stand-in string."""
    match = re.search(r"python3 -c '([^']*)'", block)
    assert match, f"no python3 -c '...' invocation found in block: {block!r}"
    return match.group(1)


def _run_python_floor_check(bin_dir: Path) -> subprocess.CompletedProcess:
    script = "set -e\n" + _extract_python_floor_check_block()
    return subprocess.run(
        [_BASH, "-c", script],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PATH": str(bin_dir)},
    )


class TestPythonFloorCheck:
    def test_below_floor_interpreter_fails_with_remediation_text(self, tmp_path: Path) -> None:
        bin_dir = _make_fake_bin_dir(tmp_path, meets_floor=False)

        result = _run_python_floor_check(bin_dir)

        assert result.returncode != 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert "3.11" in result.stdout, "remediation text must name the required version"

    def test_at_floor_interpreter_passes(self, tmp_path: Path) -> None:
        bin_dir = _make_fake_bin_dir(tmp_path, meets_floor=True)

        result = _run_python_floor_check(bin_dir)

        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"

    def test_python3_absent_from_path_caught_by_the_earlier_presence_check(self, tmp_path: Path) -> None:
        """python3 belongs to the presence-check loop (install.sh's `missing`
        array), which runs before the version-floor block -- an absent
        python3 must be reported there, by name, not fall through to a
        raw `command not found` failure inside the version check."""
        bin_dir = _make_fake_bin_dir_missing_only_python3(tmp_path)
        script = "set -e\n" + _extract_presence_check_block()

        result = subprocess.run(
            [_BASH, "-c", script],
            capture_output=True,
            text=True,
            check=False,
            env={"PATH": str(bin_dir)},
        )

        assert result.returncode != 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert "Missing dependencies: python3" in result.stdout


class TestPythonFloorCheckRealComparisonBoundary:
    """`_make_fake_bin_dir` keys its canned exit code on the `-c` argument
    mentioning `version_info` rather than evaluating it -- these tests run
    the literal extracted argument with `sys.version_info` patched, so a
    weakened floor tuple fails here even when it passes every fake-bin-dir
    test above."""

    def test_below_floor_boundary_fails(self) -> None:
        argument = _extract_c_argument(_extract_python_floor_check_block())
        patched = f"import sys; sys.version_info = (3, 10, 9, 'final', 0)\n{argument}"
        result = subprocess.run([sys.executable, "-c", patched], capture_output=True, check=False)
        assert result.returncode != 0

    def test_at_floor_boundary_passes(self) -> None:
        argument = _extract_c_argument(_extract_python_floor_check_block())
        patched = f"import sys; sys.version_info = (3, 11, 0, 'final', 0)\n{argument}"
        result = subprocess.run([sys.executable, "-c", patched], capture_output=True, check=False)
        assert result.returncode == 0
