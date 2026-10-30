"""Tests for install.sh's git floor check -- the hard-fail that enforces, at
install time, the git version `README.md` § "Requirements" documents.

Sections:

- the block over a version matrix, run under `set -e` with a stub `git` as the only command on PATH;
- the block's diagnostics: the git that won PATH, and what an unreadable `git --version` printed;
- the whole script, which must exit before its first mutation of `$HOME`;
- the README bullet states the constant's value as the requirement;
- the CI pin: tests.yml's `pin-git` step names a build of the floor's git, the runner runs that build,
  and the step's `run:` body refreshes the apt index before it installs that build by name and holds it.

The block is extracted by its INSTALL_TEST_FIXTURE markers. A trailing sentinel line
shows whether the run got past the block.
"""
from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
import yaml
from helpers import REPO_ROOT

from .test_bash_floor import README_REQUIREMENTS, exact_output_env

_INSTALL_SH = REPO_ROOT / "install.sh"
_README = REPO_ROOT / "README.md"
_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "tests.yml"
_PIN_STEP_ID = "pin-git"
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


def _pin_step() -> dict:
    """The `pin-git` step of tests.yml's `tests` job, located by id through yaml.safe_load."""
    steps = yaml.safe_load(_WORKFLOW.read_text())["jobs"]["tests"]["steps"]
    pin_steps = [step for step in steps if step.get("id") == _PIN_STEP_ID]
    assert len(pin_steps) == 1, (
        f"Expected exactly one step with id: {_PIN_STEP_ID} in tests.yml -- did the step move or get renamed?"
    )
    return pin_steps[0]


def _pin_step_env() -> dict:
    return _pin_step().get("env", {})


def _upstream_version_of(package_version: str) -> str:
    """The upstream part of a Debian version: between the optional `epoch:` and the last `-revision`."""
    without_epoch = package_version.split(":", 1)[-1]
    return without_epoch.rpartition("-")[0] or without_epoch


class TestCiGitPinMatchesTheFloor:
    def test_ci_git_pin_matches_the_floor(self) -> None:
        """The pinned build's major.minor is the floor, so a floor change cannot leave CI testing another git."""
        pinned_package_version = _pin_step_env().get("CI_GIT_PACKAGE_VERSION")
        assert isinstance(pinned_package_version, str), (
            f"tests.yml's {_PIN_STEP_ID} step env CI_GIT_PACKAGE_VERSION must be a quoted string, got {pinned_package_version!r}"
        )

        upstream_version = _upstream_version_of(pinned_package_version)
        pinned_major_minor = ".".join(upstream_version.split(".")[:2])
        assert pinned_major_minor == _git_minimum_version(), (
            f"tests.yml pins git {upstream_version} but install.sh's GIT_MINIMUM_VERSION is {_git_minimum_version()}"
        )


@pytest.mark.skipif(
    not os.environ.get("GITHUB_ACTIONS"),
    reason="CI-only: compares the pinned build to the runner's installed git",
)
def test_ci_git_equals_the_floor() -> None:
    pinned_package_version = _pin_step_env()["CI_GIT_PACKAGE_VERSION"]
    running_git = subprocess.run(
        ["git", "--version"], capture_output=True, text=True, check=True, timeout=_BASH_RUN_TIMEOUT_SECONDS
    ).stdout.strip()
    floor_change_sites = (
        'A floor change touches install.sh (GIT_MINIMUM_VERSION), README.md § "Requirements", '
        f"and the {_PIN_STEP_ID} step's env in .github/workflows/tests.yml."
    )

    assert running_git == f"git version {_upstream_version_of(pinned_package_version)}", (
        f"CI's `git --version` printed {running_git!r} but tests.yml pins {pinned_package_version}. {floor_change_sites}"
    )
    for package in ("git", "git-man"):
        installed_version = subprocess.run(
            ["dpkg-query", "-W", "-f=${Version}", package],
            capture_output=True,
            text=True,
            check=True,
            timeout=_BASH_RUN_TIMEOUT_SECONDS,
        ).stdout
        assert installed_version == pinned_package_version, (
            f"CI has {package} {installed_version!r} installed but tests.yml pins {pinned_package_version}. "
            f"{floor_change_sites}"
        )


# Differs from the workflow pin so a version hard-coded in the step body fails the tests.
_FIXTURE_PACKAGE_VERSION = "1:9.99.0-1fixture1"

# The `apt-cache` stub prints this on stdout, so a test can tell the step let the policy table reach its output.
_APT_POLICY_TABLE_MARKER = "stub apt-cache policy table"

# Stubs for the commands the pin step calls.
# The logging stubs record `sudo ` ahead of a call that came through `sudo`.
# Each argument is logged wrapped in `<` `>`, so a space inside one argument stays visible
# and the log does not depend on the shebang bash's quoting rules.
_LOGGING_STUB = """#!/bin/bash
{ printf '%s%s' "${STUB_VIA_SUDO:+sudo }" "${0##*/}"; printf ' <%s>' "$@"; printf '\\n'; } >> "$STUB_LOG"
"""
# The `apt-get` stub exits 100 on the subcommand named by STUB_APT_GET_FAILS_ON, after an error line of its own.
_APT_GET_STUB = (
    _LOGGING_STUB
    + """if [ "$1" = "${STUB_APT_GET_FAILS_ON:-}" ]; then
  echo "E: stub apt-get $1 failed" >&2
  exit 100
fi
"""
)
_APT_CACHE_STUB = _LOGGING_STUB + f"echo '{_APT_POLICY_TABLE_MARKER}'\n"
# `sudo` accepts only `apt-get` and `apt-mark`, running the sibling stub of that name with STUB_VIA_SUDO set,
# and exits 2 on any other command or flag form (such as `-n` or `env`).
_SUDO_STUB = """#!/bin/bash
case "$1" in
  apt-get|apt-mark) STUB_VIA_SUDO=1 exec "${0%/*}/$1" "${@:2}" ;;
  *) echo "sudo stub: unstubbed command $1" >&2; exit 2 ;;
esac
"""
_GIT_VERSION_STUB = """#!/bin/bash
echo 'git version 2.50.0'
"""

# The step's own sentence for a failed install; the apt-get stub's error line does not contain it.
_INSTALL_FAILURE_SENTENCE = "apt could not install git and git-man"


def _run_pin_step(
    tmp_path: Path, *, env_overrides: dict[str, str] | None = None
) -> tuple[subprocess.CompletedProcess, list[str]]:
    """Run the `pin-git` step's `run:` body under bash against stub commands; return the result and the stubs' call log."""
    stub_dir = tmp_path / "stubs"
    stub_dir.mkdir()
    stub_texts = {
        "sudo": _SUDO_STUB,
        "apt-get": _APT_GET_STUB,
        "apt-mark": _LOGGING_STUB,
        "apt-cache": _APT_CACHE_STUB,
        "git": _GIT_VERSION_STUB,
    }
    for command, text in stub_texts.items():
        _write_executable(stub_dir / command, text)
    call_log = tmp_path / "stub-calls.log"
    call_log.touch()
    script_path = tmp_path / "pin-git-step.sh"
    script_path.write_text(_pin_step()["run"])

    env = {
        "PATH": str(stub_dir),
        "HOME": str(tmp_path),
        "STUB_LOG": str(call_log),
        "CI_GIT_PACKAGE_VERSION": _FIXTURE_PACKAGE_VERSION,
        **(env_overrides or {}),
    }
    result = subprocess.run(
        [_BASH, str(script_path)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=_BASH_RUN_TIMEOUT_SECONDS,
    )
    return result, call_log.read_text().splitlines()


def _calls_to(call_log: list[str], command: str) -> list[str]:
    return [line for line in call_log if line.startswith((f"{command} ", f"sudo {command} "))]


class TestPinStepRunBody:
    """The step's apt calls and their order decide what CI runs on, so the `run:` body executes here against stubs."""

    def test_the_step_refreshes_the_index_shows_the_policy_then_installs_and_holds_both_packages_under_sudo(
        self, tmp_path: Path
    ) -> None:
        result, call_log = _run_pin_step(tmp_path)

        assert result.returncode == 0, _output_of(result)
        assert _APT_POLICY_TABLE_MARKER in result.stdout, _output_of(result)
        assert call_log == [
            "sudo apt-get <update> <--error-on=any>",
            "apt-cache <policy> <git> <git-man>",
            "sudo apt-get <install> <-y> <--no-remove> <--allow-downgrades> "
            f"<git={_FIXTURE_PACKAGE_VERSION}> <git-man={_FIXTURE_PACKAGE_VERSION}>",
            "sudo apt-mark <hold> <git> <git-man>",
        ]

    def test_a_missing_package_version_exits_non_zero_naming_the_key(self, tmp_path: Path) -> None:
        result, call_log = _run_pin_step(tmp_path, env_overrides={"CI_GIT_PACKAGE_VERSION": ""})

        assert result.returncode != 0, _output_of(result)
        assert "CI_GIT_PACKAGE_VERSION" in result.stderr, _output_of(result)
        assert call_log == [], call_log

    def test_a_failing_index_refresh_exits_non_zero_and_makes_no_install_call(self, tmp_path: Path) -> None:
        result, call_log = _run_pin_step(tmp_path, env_overrides={"STUB_APT_GET_FAILS_ON": "update"})

        assert result.returncode != 0, _output_of(result)
        assert "E: stub apt-get update failed" in result.stdout + result.stderr, _output_of(result)
        assert "sudo apt-get <update> <--error-on=any>" in call_log, call_log
        assert not [line for line in _calls_to(call_log, "apt-get") if " <install> " in line], call_log
        assert _calls_to(call_log, "apt-mark") == [], call_log

    def test_a_failing_install_exits_1_explains_the_failure_and_does_not_hold(self, tmp_path: Path) -> None:
        result, call_log = _run_pin_step(tmp_path, env_overrides={"STUB_APT_GET_FAILS_ON": "install"})

        assert result.returncode == 1, _output_of(result)
        assert _INSTALL_FAILURE_SENTENCE in result.stderr, _output_of(result)
        assert _FIXTURE_PACKAGE_VERSION in result.stderr, _output_of(result)
        assert _APT_POLICY_TABLE_MARKER in result.stdout, _output_of(result)
        assert "E: stub apt-get install failed" in result.stdout + result.stderr, _output_of(result)
        assert [line for line in _calls_to(call_log, "apt-get") if line.startswith("sudo apt-get <install> ")], call_log
        assert _calls_to(call_log, "apt-mark") == [], call_log
