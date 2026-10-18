"""Tests for install.sh's Claude Code minimum-version check -- the advisory
warning for a `claude` older than the version that honors `"onFailure": "block"`
on gate registrations.

The block is extracted by its INSTALL_TEST_FIXTURE markers and run with a stub
`claude` as the only command on PATH. The harness prepends `set -e`, as install.sh
runs, and ends the script with a sentinel line, so a block that let a failed
`claude --version` abort the run keeps the sentinel from printing. The stub
answers only the exact argv `--version`, so a block that asks for the version any
other way sees no version and stays silent.
"""
from __future__ import annotations

import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
from helpers import REPO_ROOT

_INSTALL_SH = REPO_ROOT / "install.sh"
_README = REPO_ROOT / "README.md"
_BASH = shutil.which("bash") or "bash"

_FIXTURE_START = "# INSTALL_TEST_FIXTURE: claude-code-version-check — start\n"
_FIXTURE_END = "# INSTALL_TEST_FIXTURE: claude-code-version-check — end"

_AFTER_BLOCK_SENTINEL = "reached the line after the block"

# EX_USAGE from sysexits.h: the exit status for a command invoked with the wrong arguments.
_STUB_USAGE_EXIT_STATUS = 64


def _extract_claude_code_version_check_block() -> str:
    """Same marker-delimited extraction strategy as the other
    test_install_sh_*.py files."""
    install_text = _INSTALL_SH.read_text()
    start = install_text.find(_FIXTURE_START)
    assert start != -1, f"{_FIXTURE_START!r} not found in {_INSTALL_SH}"
    end = install_text.find(_FIXTURE_END, start)
    assert end != -1, f"{_FIXTURE_END!r} not found after start marker in {_INSTALL_SH}"
    return install_text[start + len(_FIXTURE_START) : end]


def _claude_code_minimum_version() -> str:
    match = re.search(r'^CLAUDE_CODE_MINIMUM_VERSION="([0-9]+\.[0-9]+\.[0-9]+)"$', _INSTALL_SH.read_text(), re.MULTILINE)
    assert match, f"{_INSTALL_SH} does not assign CLAUDE_CODE_MINIMUM_VERSION a dotted three-field version"
    return match.group(1)


def _run_claude_code_version_check(tmp_path: Path, *, version_script: str | None) -> subprocess.CompletedProcess:
    """Run the block under `set -e` like install.sh, with a stub `claude` that runs
    `version_script` for the argv `--version` and exits 64 silently for any other argv.
    A None `version_script` leaves no `claude` on PATH."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    if version_script is not None:
        stub = bin_dir / "claude"
        stub.write_text(
            "#!/bin/sh\n"
            'if [ "$#" -eq 1 ] && [ "$1" = "--version" ]; then\n'
            f"{version_script}\n"
            "else\n"
            f"exit {_STUB_USAGE_EXIT_STATUS}\n"
            "fi\n"
        )
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    env = {"PATH": str(bin_dir), "HOME": str(tmp_path)}
    script = "set -e\n" + _extract_claude_code_version_check_block() + f"\necho '{_AFTER_BLOCK_SENTINEL}'\n"
    return subprocess.run(
        [_BASH, "-c", script],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=10,
    )


class TestClaudeCodeVersionCheckBlock:
    @pytest.mark.parametrize(
        "reported_version",
        [
            pytest.param("2.1.294", id="one-patch-below"),
            pytest.param("2.0.999", id="lower-minor-higher-patch"),
            pytest.param("1.99.999", id="lower-major"),
            pytest.param("2.1.9", id="single-digit-patch-below-numerically-but-above-lexically"),
            pytest.param("2.1.08", id="zero-padded-field-is-decimal-not-octal"),
            pytest.param("1.0.0", id="lower-in-two-fields-warns-once"),
        ],
    )
    def test_below_the_minimum_warns_once_on_stderr_and_does_not_end_the_run(
        self, tmp_path: Path, reported_version: str
    ) -> None:
        result = _run_claude_code_version_check(tmp_path, version_script=f"echo '{reported_version} (Claude Code)'")

        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        stderr_lines = result.stderr.splitlines()
        assert len(stderr_lines) == 1, f"expected exactly one warning line, got stderr={result.stderr!r}"
        assert stderr_lines[0].startswith("[install] warning:")
        assert reported_version in stderr_lines[0]
        assert _claude_code_minimum_version() in stderr_lines[0]
        assert "/hooks" in stderr_lines[0] and "/permissions" in stderr_lines[0]
        assert result.stdout == f"{_AFTER_BLOCK_SENTINEL}\n"

    @pytest.mark.parametrize(
        "reported_version",
        [
            pytest.param("2.1.295", id="exactly-the-minimum"),
            pytest.param("2.1.296", id="one-patch-above"),
            pytest.param("2.1.1000", id="four-digit-patch-above-numerically-but-below-lexically"),
            pytest.param("2.2.0", id="higher-minor-lower-patch"),
            pytest.param("3.0.0", id="higher-major"),
            pytest.param("2.10.0", id="two-digit-minor"),
            pytest.param("02.01.0295", id="zero-padded-fields-equal-to-the-minimum"),
        ],
    )
    def test_at_or_above_the_minimum_is_silent(self, tmp_path: Path, reported_version: str) -> None:
        result = _run_claude_code_version_check(tmp_path, version_script=f"echo '{reported_version} (Claude Code)'")

        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert result.stderr == ""
        assert result.stdout == f"{_AFTER_BLOCK_SENTINEL}\n"

    def test_an_absent_claude_is_silent(self, tmp_path: Path) -> None:
        result = _run_claude_code_version_check(tmp_path, version_script=None)

        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert result.stderr == ""
        assert result.stdout == f"{_AFTER_BLOCK_SENTINEL}\n"

    @pytest.mark.parametrize(
        "version_script",
        [
            pytest.param("echo 'unreleased build'", id="no-version-in-output"),
            pytest.param("echo '2.1'", id="two-field-version"),
            pytest.param("exit 3", id="version-flag-fails"),
        ],
    )
    def test_an_unparsable_version_is_silent(self, tmp_path: Path, version_script: str) -> None:
        result = _run_claude_code_version_check(tmp_path, version_script=version_script)

        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert result.stderr == ""
        assert result.stdout == f"{_AFTER_BLOCK_SENTINEL}\n"


class TestMinimumVersionMatchesTheDocumentedRequirement:
    def test_readme_requirements_bullet_names_the_minimum_version(self) -> None:
        """The constant is the executable copy of the version `README.md` § "Requirements" documents."""
        minimum_version = _claude_code_minimum_version()
        readme_text = _README.read_text()
        requirements_section = re.search(r"^## Requirements\n(.*?)(?=^## )", readme_text, re.MULTILINE | re.DOTALL)
        assert requirements_section, "README.md has no Requirements section"
        bullets = [line for line in requirements_section.group(1).splitlines() if line.startswith("- **Claude Code CLI:**")]
        assert len(bullets) == 1, f"expected one Claude Code CLI bullet in Requirements, got {bullets!r}"
        assert minimum_version in bullets[0], f"README Requirements does not name {minimum_version}: {bullets[0]!r}"
