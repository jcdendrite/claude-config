"""Tests for install.sh's git floor check -- the hard-fail that makes the git
version `README.md` § "Requirements" documents an enforced, install-time
requirement rather than prose.

Sections:

- the block over a version matrix, run under `set -e` with a stub `git` as the only command on PATH;
- the block's diagnostics: the git that won PATH, and what an unreadable `git --version` printed;
- the whole script, which must exit before its first mutation of `$HOME`;
- the README bullet states the constant's value as the requirement.

The block is extracted by its INSTALL_TEST_FIXTURE markers. A trailing sentinel line
shows whether the run got past the block.
"""
from __future__ import annotations

import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
from helpers import REPO_ROOT

from .test_bash_floor import README_REQUIREMENTS, exact_output_env

_INSTALL_SH = REPO_ROOT / "install.sh"
_README = REPO_ROOT / "README.md"
_CONFIG_SH_REPO_PATH = Path("claude") / ".claude" / "hooks" / "_config.sh"
_BASH = shutil.which("bash") or "bash"

_FIXTURE_START = "# INSTALL_TEST_FIXTURE: git-floor-check — start\n"
_FIXTURE_END = "# INSTALL_TEST_FIXTURE: git-floor-check — end"

_AFTER_BLOCK_SENTINEL = "reached the line after the block"

# EX_USAGE from sysexits.h: the exit status for a command invoked with the wrong arguments.
_STUB_USAGE_EXIT_STATUS = 64

# Bounds a hung local bash run; no vendor value applies.
_BASH_RUN_TIMEOUT_SECONDS = 10

# Tools install.sh's presence check requires, other than the `git` and `claude` stubs the whole-script run writes itself.
_TOOLS_THAT_JUST_EXIT_ZERO = ("stow", "gh", "jq", "sha256sum", "python3")


def _extract_git_floor_check_block() -> str:
    """Same marker-delimited extraction strategy as the other
    test_install_sh_*.py files."""
    install_text = _INSTALL_SH.read_text()
    start = install_text.find(_FIXTURE_START)
    assert start != -1, f"{_FIXTURE_START!r} not found in {_INSTALL_SH}"
    end = install_text.find(_FIXTURE_END, start)
    assert end != -1, f"{_FIXTURE_END!r} not found after start marker in {_INSTALL_SH}"
    return install_text[start + len(_FIXTURE_START) : end]


def _git_minimum_version() -> str:
    match = re.search(r'^GIT_MINIMUM_VERSION="([0-9]+\.[0-9]+)"$', _extract_git_floor_check_block(), re.MULTILINE)
    assert match, f"{_INSTALL_SH} does not assign GIT_MINIMUM_VERSION a dotted two-field version in the git floor block"
    return match.group(1)


def _write_executable(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _write_git_stub(bin_dir: Path, *, version_script: str) -> None:
    """A `git` that runs `version_script` for the argv `--version` and exits 64 silently for any other argv."""
    _write_executable(
        bin_dir / "git",
        "#!/bin/sh\n"
        'if [ "$#" -eq 1 ] && [ "$1" = "--version" ]; then\n'
        f"{version_script}\n"
        "else\n"
        f"exit {_STUB_USAGE_EXIT_STATUS}\n"
        "fi\n",
    )


def _run_git_floor_check(
    tmp_path: Path, *, version_script: str | None
) -> subprocess.CompletedProcess:
    """Run the block under `set -e` like install.sh, with the stub `git` as the only command on PATH.

    A `version_script` of None leaves PATH holding an empty directory, so no `git` resolves."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    if version_script is not None:
        _write_git_stub(bin_dir, version_script=version_script)
    env = {"PATH": str(bin_dir), "HOME": str(tmp_path)}
    script = "set -e\n" + _extract_git_floor_check_block() + f"\necho '{_AFTER_BLOCK_SENTINEL}'\n"
    return subprocess.run(
        [_BASH, "-c", script],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=_BASH_RUN_TIMEOUT_SECONDS,
    )


def _output_of(result: subprocess.CompletedProcess) -> str:
    return f"stdout={result.stdout!r} stderr={result.stderr!r}"


class TestGitFloorCheckBlock:
    @pytest.mark.parametrize(
        "reported_version",
        [
            pytest.param("2.43.0", id="exactly-the-floor"),
            pytest.param("2.43.7", id="patch-above"),
            pytest.param("2.44.0", id="minor-above"),
            pytest.param("2.100.0", id="three-digit-minor-above-numerically-but-below-lexically"),
            pytest.param("3.0.0", id="higher-major"),
            pytest.param("2.50.1 (Apple Git-155)", id="vendor-suffix"),
        ],
    )
    def test_at_or_above_the_floor_is_silent_and_does_not_end_the_run(
        self, tmp_path: Path, reported_version: str
    ) -> None:
        result = _run_git_floor_check(tmp_path, version_script=f"echo 'git version {reported_version}'")

        assert result.returncode == 0, _output_of(result)
        assert result.stderr == "", _output_of(result)
        assert result.stdout == f"{_AFTER_BLOCK_SENTINEL}\n", _output_of(result)

    @pytest.mark.parametrize(
        "reported_version",
        [
            pytest.param("2.42.4", id="one-minor-below"),
            pytest.param("2.40.1", id="enough-for-the-code-but-below-the-floor"),
            pytest.param("2.39.5 (Apple Git-154)", id="vendor-suffix-below"),
            pytest.param("2.5.0", id="single-digit-minor-below-numerically-but-above-lexically"),
            pytest.param("1.99.0", id="lower-major"),
        ],
    )
    def test_below_the_floor_exits_1_naming_the_version_the_floor_and_the_readme(
        self, tmp_path: Path, reported_version: str
    ) -> None:
        result = _run_git_floor_check(tmp_path, version_script=f"echo 'git version {reported_version}'")

        assert result.returncode == 1, _output_of(result)
        assert _AFTER_BLOCK_SENTINEL not in result.stdout, _output_of(result)
        assert reported_version in result.stderr, _output_of(result)
        assert _git_minimum_version() in result.stderr, _output_of(result)
        assert README_REQUIREMENTS.text in result.stderr, _output_of(result)

    @pytest.mark.parametrize(
        "version_script",
        [
            pytest.param("exit 0", id="empty-output"),
            pytest.param("echo 'unreleased build'", id="no-version-in-output"),
            pytest.param("echo 'git version 2'", id="one-field-version"),
            pytest.param("exit 1", id="version-flag-fails"),
        ],
    )
    def test_an_unreadable_version_exits_1_naming_the_floor_and_the_readme(
        self, tmp_path: Path, version_script: str
    ) -> None:
        result = _run_git_floor_check(tmp_path, version_script=version_script)

        assert result.returncode == 1, _output_of(result)
        assert _AFTER_BLOCK_SENTINEL not in result.stdout, _output_of(result)
        assert _git_minimum_version() in result.stderr, _output_of(result)
        assert README_REQUIREMENTS.text in result.stderr, _output_of(result)


class TestGitFloorCheckDiagnostics:
    """What the failure message tells an operator whose install.sh run was refused."""

    @pytest.mark.parametrize(
        "version_script",
        [
            pytest.param("echo 'git version 2.42.0'", id="below-the-floor"),
            pytest.param("echo 'unreleased build'", id="unreadable"),
        ],
    )
    def test_both_failures_name_the_git_that_won_path(self, tmp_path: Path, version_script: str) -> None:
        result = _run_git_floor_check(tmp_path, version_script=version_script)

        assert result.returncode == 1, _output_of(result)
        assert f"git resolved on PATH: {tmp_path / 'bin' / 'git'}\n" in result.stderr, _output_of(result)

    def test_a_path_with_no_git_says_none_was_found(self, tmp_path: Path) -> None:
        result = _run_git_floor_check(tmp_path, version_script=None)

        assert result.returncode == 1, _output_of(result)
        assert "git resolved on PATH: none found\n" in result.stderr, _output_of(result)
        assert _AFTER_BLOCK_SENTINEL not in result.stdout, _output_of(result)

    def test_a_warning_line_before_a_valid_version_is_refused_and_quoted(self, tmp_path: Path) -> None:
        """A wrapper that prints a warning ahead of `git version 2.43.0` is refused, and the message shows the warning."""
        result = _run_git_floor_check(
            tmp_path, version_script="echo 'warning: shim fell back to a default'; echo 'git version 2.43.0'"
        )

        assert result.returncode == 1, _output_of(result)
        assert "warning: shim fell back to a default" in result.stderr, _output_of(result)
        assert "exited with status 0" in result.stderr, _output_of(result)
        assert _AFTER_BLOCK_SENTINEL not in result.stdout, _output_of(result)

    def test_a_valid_version_with_a_failing_exit_status_is_refused_and_names_the_status(self, tmp_path: Path) -> None:
        """`git version 2.43.0` followed by exit status 1 is refused, and the message names that status."""
        result = _run_git_floor_check(tmp_path, version_script="echo 'git version 2.43.0'; exit 1")

        assert result.returncode == 1, _output_of(result)
        assert "exited with status 1" in result.stderr, _output_of(result)
        assert 'printed "git version 2.43.0"' in result.stderr, _output_of(result)
        assert _AFTER_BLOCK_SENTINEL not in result.stdout, _output_of(result)

    def test_text_a_failing_git_wrote_to_stderr_reaches_stderr_unaltered(self, tmp_path: Path) -> None:
        """A version-manager shim that explains itself on stderr only is the case the passthrough exists for."""
        shim_explanation = "shim: no version of git is set for this directory"
        result = _run_git_floor_check(tmp_path, version_script=f"echo '{shim_explanation}' >&2; exit 1")

        assert result.returncode == 1, _output_of(result)
        assert f"{shim_explanation}\n" in result.stderr, _output_of(result)
        assert result.stderr.count(shim_explanation) == 1, _output_of(result)

    def test_silent_output_is_named_as_nothing(self, tmp_path: Path) -> None:
        result = _run_git_floor_check(tmp_path, version_script="exit 1")

        assert result.returncode == 1, _output_of(result)
        assert 'printed "<nothing>"' in result.stderr, _output_of(result)


class TestGitFloorCheckPrecedesEveryMutation:
    def test_a_below_floor_git_ends_the_whole_script_before_it_changes_home(self, tmp_path: Path) -> None:
        """Runs a tmp copy of install.sh, so a block moved below the first `mkdir` leaves files under HOME."""
        tmp_repo = tmp_path / "repo"
        (tmp_repo / _CONFIG_SH_REPO_PATH.parent).mkdir(parents=True)
        (tmp_repo / "install.sh").write_text(_INSTALL_SH.read_text())
        (tmp_repo / _CONFIG_SH_REPO_PATH).write_text((REPO_ROOT / _CONFIG_SH_REPO_PATH).read_text())
        stub_dir = tmp_path / "stubs"
        stub_dir.mkdir()
        for tool in _TOOLS_THAT_JUST_EXIT_ZERO:
            _write_executable(stub_dir / tool, "#!/bin/sh\nexit 0\n")
        _write_executable(stub_dir / "claude", "#!/bin/sh\necho '2.1.295 (Claude Code)'\n")
        _write_git_stub(stub_dir, version_script="echo 'git version 2.42.0'")
        home = tmp_path / "home"
        home.mkdir()
        env = {k: v for k, v in exact_output_env().items() if k != "CLAUDE_CONFIG_DIR"}
        env["HOME"] = str(home)
        env["PATH"] = f"{stub_dir}:/usr/bin:/bin"

        result = subprocess.run(
            [_BASH, str(tmp_repo / "install.sh")],
            capture_output=True,
            text=True,
            check=False,
            cwd=tmp_repo,
            env=env,
            stdin=subprocess.DEVNULL,
            timeout=_BASH_RUN_TIMEOUT_SECONDS,
        )

        assert result.returncode == 1, _output_of(result)
        assert _git_minimum_version() in result.stderr, _output_of(result)
        left_in_home = list(home.iterdir())
        assert left_in_home == [], (
            f"install.sh changed HOME before the git floor check ended it: {left_in_home!r} {_output_of(result)}"
        )


class TestMinimumVersionMatchesTheDocumentedRequirement:
    def test_readme_requirements_bullet_states_the_minimum_version_as_the_requirement(self) -> None:
        """The constant is the executable copy of the version `README.md` § "Requirements" states as the requirement."""
        minimum_version = _git_minimum_version()
        readme_text = _README.read_text()
        requirements_section = re.search(r"^## Requirements\n(.*?)(?=^## )", readme_text, re.MULTILINE | re.DOTALL)
        assert requirements_section, "README.md has no Requirements section"
        bullets = [line for line in requirements_section.group(1).splitlines() if line.startswith("- **`git` version:**")]
        assert len(bullets) == 1, f"expected one git version bullet in Requirements, got {bullets!r}"
        stated_requirement = re.match(r"- \*\*`git` version:\*\* git ([0-9]+\.[0-9]+) or newer", bullets[0])
        assert stated_requirement, f"README Requirements git bullet does not open with 'git X.Y or newer': {bullets[0]!r}"
        assert stated_requirement.group(1) == minimum_version, (
            f"README Requirements states git {stated_requirement.group(1)} but install.sh enforces {minimum_version}"
        )
