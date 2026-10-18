"""Tests for install.sh's Claude Code minimum-version check -- the advisory
warning for a `claude` older than the version that honors `"onFailure": "block"`
on gate registrations.

The block is extracted by its INSTALL_TEST_FIXTURE markers and run with a stub
`claude` as the only command on PATH. The harness prepends `set -e`, as install.sh
runs, and ends the script with a sentinel line, so a block that let a failed
`claude --version` abort the run keeps the sentinel from printing. The stub
answers only the exact argv `--version`, so a block that asks for the version any
other way sees no version and warns that it could not read one.
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

_REPRINT_FIXTURE_START = "# INSTALL_TEST_FIXTURE: claude-code-version-reprint — start\n"
_REPRINT_FIXTURE_END = "# INSTALL_TEST_FIXTURE: claude-code-version-reprint — end"

_PRESENCE_FIXTURE_START = "# INSTALL_TEST_FIXTURE: presence-check — start\n"
_PRESENCE_FIXTURE_END = "# INSTALL_TEST_FIXTURE: presence-check — end"

# Every tool in install.sh's presence-check loop except `claude`.
_TOOLS_OTHER_THAN_CLAUDE = ("stow", "git", "gh", "jq", "sha256sum", "python3")

_AFTER_BLOCK_SENTINEL = "reached the line after the block"

# EX_USAGE from sysexits.h: the exit status for a command invoked with the wrong arguments.
_STUB_USAGE_EXIT_STATUS = 64

# Bounds a hung local bash run; no vendor value applies.
_BASH_RUN_TIMEOUT_SECONDS = 10


def _extract_marked_block(fixture_start: str, fixture_end: str) -> str:
    """Same marker-delimited extraction strategy as the other
    test_install_sh_*.py files."""
    install_text = _INSTALL_SH.read_text()
    start = install_text.find(fixture_start)
    assert start != -1, f"{fixture_start!r} not found in {_INSTALL_SH}"
    end = install_text.find(fixture_end, start)
    assert end != -1, f"{fixture_end!r} not found after start marker in {_INSTALL_SH}"
    return install_text[start + len(fixture_start) : end]


def _extract_claude_code_version_check_block() -> str:
    return _extract_marked_block(_FIXTURE_START, _FIXTURE_END)


def _extract_presence_check_block() -> str:
    return _extract_marked_block(_PRESENCE_FIXTURE_START, _PRESENCE_FIXTURE_END)


def _extract_claude_code_version_reprint_block() -> str:
    return _extract_marked_block(_REPRINT_FIXTURE_START, _REPRINT_FIXTURE_END)


def _claude_code_minimum_version() -> str:
    match = re.search(r'^CLAUDE_CODE_MINIMUM_VERSION="([0-9]+\.[0-9]+\.[0-9]+)"$', _INSTALL_SH.read_text(), re.MULTILINE)
    assert match, f"{_INSTALL_SH} does not assign CLAUDE_CODE_MINIMUM_VERSION a dotted three-field version"
    return match.group(1)


def _run_claude_code_version_check(
    tmp_path: Path, *, version_script: str, with_reprint: bool = False
) -> subprocess.CompletedProcess:
    """Run the block under `set -e` like install.sh, with a stub `claude` that runs
    `version_script` for the argv `--version` and exits 64 silently for any other argv.
    `with_reprint` appends the reprint block after the check block, as install.sh orders them."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
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
    reprint_block = _extract_claude_code_version_reprint_block() if with_reprint else ""
    script = "set -e\n" + _extract_claude_code_version_check_block() + reprint_block + f"\necho '{_AFTER_BLOCK_SENTINEL}'\n"
    return subprocess.run(
        [_BASH, "-c", script],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=_BASH_RUN_TIMEOUT_SECONDS,
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

    @pytest.mark.parametrize(
        "version_script",
        [
            pytest.param("echo 'unreleased build'", id="no-version-in-output"),
            pytest.param("echo '2.1'", id="two-field-version"),
            pytest.param("exit 3", id="version-flag-fails"),
        ],
    )
    def test_an_unparsable_version_warns_once_naming_the_minimum_and_does_not_end_the_run(
        self, tmp_path: Path, version_script: str
    ) -> None:
        result = _run_claude_code_version_check(tmp_path, version_script=version_script)

        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        stderr_lines = result.stderr.splitlines()
        assert len(stderr_lines) == 1, f"expected exactly one warning line, got stderr={result.stderr!r}"
        assert stderr_lines[0].startswith("[install] warning:")
        assert "could not read" in stderr_lines[0]
        assert _claude_code_minimum_version() in stderr_lines[0]
        assert result.stdout == f"{_AFTER_BLOCK_SENTINEL}\n"


class TestClaudeCodeVersionWarningReprint:
    def _run_reprint_block(self, assignment: str) -> subprocess.CompletedProcess:
        """Run the block under `set -eu` after `assignment`, so an unset variable that the block expands bare aborts the run."""
        script = "set -eu\n" + assignment + _extract_claude_code_version_reprint_block()
        return subprocess.run(
            [_BASH, "-c", script],
            capture_output=True,
            text=True,
            check=False,
            env={"PATH": "", "HOME": ""},
            timeout=_BASH_RUN_TIMEOUT_SECONDS,
        )

    def test_a_stored_warning_is_reprinted_once_on_stderr(self) -> None:
        result = self._run_reprint_block("claude_code_version_warning='[install] warning: stored text'\n")

        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert result.stderr == "[install] warning: stored text\n"
        assert result.stdout == ""

    @pytest.mark.parametrize(
        "assignment",
        [
            pytest.param("", id="variable-unset"),
            pytest.param("claude_code_version_warning=''\n", id="variable-empty"),
        ],
    )
    def test_no_stored_warning_prints_nothing(self, assignment: str) -> None:
        result = self._run_reprint_block(assignment)

        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert result.stderr == ""
        assert result.stdout == ""

    @pytest.mark.parametrize(
        ("version_script", "expected_warning_count"),
        [
            pytest.param("echo '2.1.294 (Claude Code)'", 2, id="below-minimum"),
            pytest.param("echo 'unreleased build'", 2, id="unparsable-version"),
            pytest.param("echo '2.1.295 (Claude Code)'", 0, id="at-minimum"),
        ],
    )
    def test_the_check_then_reprint_sequence_prints_the_warning_twice_or_not_at_all(
        self, tmp_path: Path, version_script: str, expected_warning_count: int
    ) -> None:
        result = _run_claude_code_version_check(tmp_path, version_script=version_script, with_reprint=True)

        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        stderr_lines = result.stderr.splitlines()
        assert len(stderr_lines) == expected_warning_count, f"stderr={result.stderr!r}"
        assert len(set(stderr_lines)) <= 1, f"the reprinted line differs from the first: {stderr_lines!r}"
        assert all(line.startswith("[install] warning:") for line in stderr_lines)
        assert result.stdout == f"{_AFTER_BLOCK_SENTINEL}\n"

    def test_the_reprint_immediately_precedes_the_last_prompt_contributor_intent_call(self) -> None:
        """Source-position tripwire: keeps the reprint the last output before the prompt,
        so the warning surfaces before the user answers."""
        install_text = _INSTALL_SH.read_text()
        reprint_end_marker = install_text.find(_REPRINT_FIXTURE_END)
        assert reprint_end_marker != -1, f"{_REPRINT_FIXTURE_END!r} not found in {_INSTALL_SH}"
        reprint_end_of_line = install_text.find("\n", reprint_end_marker)
        assert reprint_end_of_line != -1, f"{_REPRINT_FIXTURE_END!r} is not followed by a newline in {_INSTALL_SH}"
        last_prompt_call = install_text.rfind("\nprompt_contributor_intent\n")
        assert last_prompt_call != -1, f"no bare prompt_contributor_intent call line in {_INSTALL_SH}"
        assert reprint_end_of_line <= last_prompt_call, "the reprint block ends after the last prompt call"
        between = install_text[reprint_end_of_line:last_prompt_call]
        assert between.strip() == "", f"non-whitespace between the reprint and the last prompt call: {between!r}"


class TestPresenceCheckReportsAnAbsentClaude:
    def test_claude_absent_from_path_is_reported_by_the_presence_check_and_exits_1(self, tmp_path: Path) -> None:
        """The presence check reports a missing `claude` and exits 1."""
        bin_dir = tmp_path / "bin-no-claude"
        bin_dir.mkdir()
        for tool in _TOOLS_OTHER_THAN_CLAUDE:
            stub = bin_dir / tool
            stub.write_text("#!/bin/sh\nexit 0\n")
            stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        script = "set -e\n" + _extract_presence_check_block()

        result = subprocess.run(
            [_BASH, "-c", script],
            capture_output=True,
            text=True,
            check=False,
            env={"PATH": str(bin_dir), "HOME": str(tmp_path)},
            timeout=_BASH_RUN_TIMEOUT_SECONDS,
        )

        assert result.returncode == 1, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert result.stdout.splitlines()[:1] == ["Missing dependencies: claude"], f"stdout={result.stdout!r}"


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
