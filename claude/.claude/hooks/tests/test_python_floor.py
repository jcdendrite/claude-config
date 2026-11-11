"""Tests for the Python floor: the constants and probe in `_config.sh`, install.sh's check, and the statements tied to CI's pin.

`_config.sh` is sourced before PATH narrows to the stub directory, because it calls `dirname` while loading.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path

import pytest
from helpers import HOOKS_DIR, REPO_ROOT, SCRIPTS_DIR

from .test_bash_floor import README_REQUIREMENTS, _run_in_bash, readme_requirements_bullet
from .test_git_floor import (
    _BASH_RUN_TIMEOUT_SECONDS,
    _git_minimum_version,
    _output_of,
    _write_executable,
    python_floor_fields,
    run_install_sh_in_tmp_repo,
)
from .test_install_sh_claude_code_version_check import _extract_marked_block, _extract_presence_check_block

_CONFIG_SH = HOOKS_DIR / "_config.sh"
_CI_PYTHON_PIN_LIB = SCRIPTS_DIR / "_ci-python-pin-lib.sh"
_INSTALL_SH = REPO_ROOT / "install.sh"
_PYPROJECT = REPO_ROOT / "pyproject.toml"
_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "tests.yml"

_FIXTURE_START = "# INSTALL_TEST_FIXTURE: python-floor-check — start\n"
_FIXTURE_END = "# INSTALL_TEST_FIXTURE: python-floor-check — end"

_AFTER_BLOCK_SENTINEL = "reached the line after the block"

# The four places a floor change touches, named in every tie's failure message.
_FLOOR_CHANGE_SITES = (
    'A floor change touches _config.sh (_PYTHON_FLOOR_MAJOR and _PYTHON_FLOOR_MINOR), the python-version pin in '
    '.github/workflows/tests.yml, pyproject.toml\'s [tool.ruff] target-version, and README.md § "Requirements".'
)


def _floor_major() -> str:
    return python_floor_fields()[0]


def _floor_minor() -> str:
    return python_floor_fields()[1]


def _floor_version() -> str:
    return f"{_floor_major()}.{_floor_minor()}"


def _stub_output_and_dotted_version(minor_offset: int) -> tuple[str, str]:
    """The probe stub's `<major> <minor>` output and the dotted version the probe prints, `minor_offset` minors from the floor."""
    shifted_minor = int(_floor_minor()) + minor_offset
    return f"{_floor_major()} {shifted_minor}", f"{_floor_major()}.{shifted_minor}"


def _floor_stub_output() -> str:
    return _stub_output_and_dotted_version(0)[0]


def _major_relative_to_floor(major_offset: int) -> str:
    return str(int(_floor_major()) + major_offset)


def _digits(count: int) -> str:
    return "9" * count


def _extract_python_floor_check_block() -> str:
    return _extract_marked_block(_FIXTURE_START, _FIXTURE_END)


def _write_python3_stub(bin_dir: Path, body: str, *, calls_log: Path | None = None) -> None:
    """A `python3` that ignores its arguments and runs `body`, first appending its arguments to `calls_log` when given."""
    bin_dir.mkdir(exist_ok=True)
    log_line = f"printf '%s\\n' \"$*\" >> '{calls_log}'\n" if calls_log is not None else ""
    _write_executable(bin_dir / "python3", f"#!/bin/sh\n{log_line}{body}\n")


def _link_real_python3(bin_dir: Path) -> None:
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "python3").symlink_to(sys.executable)


def _run_predicate(major: str, minor: str) -> subprocess.CompletedProcess:
    return _run_in_bash(
        '. "$1" || exit 99; _python_version_meets_floor "$2" "$3"',
        str(_CONFIG_SH),
        major,
        minor,
        timeout=_BASH_RUN_TIMEOUT_SECONDS,
    )


# The shell options a caller may run under: bash's defaults, and a hook's `set -uo pipefail`.
DEFAULT_SHELL_OPTIONS = ""
STRICT_SHELL_OPTIONS = "set -uo pipefail"


@pytest.fixture(
    params=[pytest.param(DEFAULT_SHELL_OPTIONS, id="default-options"), pytest.param(STRICT_SHELL_OPTIONS, id="set-uo-pipefail")]
)
def shell_options(request: pytest.FixtureRequest) -> str:
    return request.param


def _run_probe(
    bin_dir: Path,
    *,
    shell_options: str = DEFAULT_SHELL_OPTIONS,
    caller_setup: str = "",
    runner: tuple[str, ...] = (),
    cwd: Path | None = None,
    env_extra: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Run `_python3_floor_status RUNNER...` with only `bin_dir` on PATH; the returncode is the probe's status.

    `shell_options` runs before `_config.sh` is sourced, and `caller_setup` after PATH narrows, just before the probe."""
    env = {"HOME": str(bin_dir), "PATH": os.environ["PATH"], **(env_extra or {})}
    script = f'{shell_options}\n. "$1" || exit 99; PATH="$2"; {caller_setup}\n_python3_floor_status "${{@:3}}"'
    return _run_in_bash(script, str(_CONFIG_SH), str(bin_dir), *runner, env=env, cwd=cwd, timeout=_BASH_RUN_TIMEOUT_SECONDS)


def _run_install_block(bin_dir: Path) -> subprocess.CompletedProcess:
    """Run install.sh's python block under `set -e`, then print a sentinel and the version the block captured."""
    script = (
        'set -e\n. "$1"\nPATH="$2"\n'
        + _extract_python_floor_check_block()
        + f"\nprintf '%s %s\\n' '{_AFTER_BLOCK_SENTINEL}' \"$python3_found_version\"\n"
    )
    return _run_in_bash(
        script,
        str(_CONFIG_SH),
        str(bin_dir),
        env={"HOME": str(bin_dir), "PATH": os.environ["PATH"]},
        timeout=_BASH_RUN_TIMEOUT_SECONDS,
    )


# Parametrize values that depend on the floor are zero-argument callables resolved in the test body.
# A failed floor read then fails one test instead of aborting collection.
class TestPredicate:
    @pytest.mark.parametrize(
        "fields",
        [
            pytest.param(lambda: (_floor_major(), _floor_minor()), id="exactly-the-floor"),
            pytest.param(lambda: (_floor_major(), str(int(_floor_minor()) + 1)), id="minor-above"),
            pytest.param(lambda: (_floor_major(), "100"), id="three-digit-minor-above-numerically-but-below-lexically"),
            pytest.param(lambda: (_major_relative_to_floor(1), "0"), id="higher-major-with-a-lower-minor"),
            pytest.param(lambda: (_floor_major(), f"0{_floor_minor()}"), id="leading-zero-minor-is-the-decimal-floor-not-octal"),
            pytest.param(lambda: ("08", "0"), id="leading-zero-major-is-decimal-8-not-an-octal-error"),
        ],
    )
    def test_at_or_above_the_floor_returns_0(self, fields: Callable[[], tuple[str, str]]) -> None:
        major, minor = fields()
        result = _run_predicate(major, minor)

        assert result.returncode == 0, _output_of(result)
        assert result.stderr == "", _output_of(result)

    @pytest.mark.parametrize(
        "fields",
        [
            pytest.param(lambda: (_floor_major(), str(int(_floor_minor()) - 1)), id="one-minor-below"),
            pytest.param(lambda: (_floor_major(), "9"), id="single-digit-minor-below-numerically-but-above-lexically"),
            pytest.param(lambda: (_floor_major(), "08"), id="leading-zero-is-decimal-not-octal"),
            pytest.param(lambda: (_major_relative_to_floor(-1), "99"), id="lower-major-with-a-higher-minor"),
        ],
    )
    def test_below_the_floor_returns_1(self, fields: Callable[[], tuple[str, str]]) -> None:
        major, minor = fields()
        result = _run_predicate(major, minor)

        assert result.returncode == 1, _output_of(result)
        assert result.stderr == "", _output_of(result)

    @pytest.mark.parametrize(
        "fields",
        [
            pytest.param(lambda: ("", ""), id="both-empty"),
            pytest.param(lambda: (_floor_major(), ""), id="empty-minor"),
            pytest.param(lambda: (_floor_major(), f"{_floor_minor()}a"), id="trailing-letter"),
            pytest.param(lambda: ("x", "y"), id="non-numeric"),
            pytest.param(lambda: ("x", _floor_minor()), id="non-numeric-major-with-a-valid-minor"),
            pytest.param(lambda: ("", _floor_minor()), id="empty-major-with-a-valid-minor"),
            pytest.param(lambda: (_digits(10), "0"), id="ten-digit-major"),
            pytest.param(lambda: (_digits(1000), _floor_minor()), id="thousand-digit-major"),
            pytest.param(lambda: (_floor_major(), _digits(10)), id="ten-digit-minor"),
            pytest.param(lambda: (_floor_major(), _digits(20)), id="twenty-digit-minor"),
            pytest.param(lambda: (_floor_major(), _digits(1000)), id="thousand-digit-minor"),
        ],
    )
    def test_a_field_that_is_not_one_to_nine_digits_returns_2(self, fields: Callable[[], tuple[str, str]]) -> None:
        major, minor = fields()
        result = _run_predicate(major, minor)

        assert result.returncode == 2, _output_of(result)

    def test_nine_digit_minor_is_still_a_number(self) -> None:
        result = _run_predicate(_floor_major(), _digits(9))

        assert result.returncode == 0, _output_of(result)

    def test_nine_digit_major_is_still_a_number(self) -> None:
        result = _run_predicate(_digits(9), "0")

        assert result.returncode == 0, _output_of(result)


class TestProbeAgainstStubs:
    @pytest.mark.parametrize(
        "stub_output_and_version",
        [
            pytest.param(lambda: _stub_output_and_dotted_version(0), id="exactly-the-floor"),
            pytest.param(lambda: _stub_output_and_dotted_version(1), id="minor-above"),
            pytest.param(lambda: (f"{_floor_major()} 100", f"{_floor_major()}.100"), id="three-digit-minor"),
        ],
    )
    def test_at_or_above_the_floor_returns_0_and_prints_the_dotted_version(
        self, tmp_path: Path, shell_options: str, stub_output_and_version: Callable[[], tuple[str, str]]
    ) -> None:
        reported, expected_version = stub_output_and_version()
        _write_python3_stub(tmp_path / "bin", f"echo '{reported}'")

        result = _run_probe(tmp_path / "bin", shell_options=shell_options)

        assert result.returncode == 0, _output_of(result)
        assert result.stdout == f"{expected_version}\n", _output_of(result)

    def test_below_the_floor_returns_1_and_prints_the_dotted_version(self, tmp_path: Path, shell_options: str) -> None:
        reported, expected_version = _stub_output_and_dotted_version(-1)
        _write_python3_stub(tmp_path / "bin", f"echo '{reported}'")

        result = _run_probe(tmp_path / "bin", shell_options=shell_options)

        assert result.returncode == 1, _output_of(result)
        assert result.stdout == f"{expected_version}\n", _output_of(result)

    def test_only_the_first_line_counts_when_a_later_line_is_below_the_floor(self, tmp_path: Path, shell_options: str) -> None:
        below_floor, _ = _stub_output_and_dotted_version(-1)
        _write_python3_stub(tmp_path / "bin", f"echo '{_floor_stub_output()}'; echo '{below_floor}'")

        result = _run_probe(tmp_path / "bin", shell_options=shell_options)

        assert result.returncode == 0, _output_of(result)
        assert result.stdout == f"{_floor_version()}\n", _output_of(result)

    def test_only_the_first_line_counts_when_a_later_line_is_junk(self, tmp_path: Path, shell_options: str) -> None:
        reported, expected_version = _stub_output_and_dotted_version(-1)
        _write_python3_stub(tmp_path / "bin", f"echo '{reported}'; echo 'ZQXJ line two'")

        result = _run_probe(tmp_path / "bin", shell_options=shell_options)

        assert result.returncode == 1, _output_of(result)
        assert result.stdout == f"{expected_version}\n", _output_of(result)

    @pytest.mark.parametrize(
        "stub_body",
        [
            pytest.param(lambda: "exit 0", id="empty-output"),
            pytest.param(lambda: "echo garbage", id="garbage"),
            pytest.param(lambda: f"echo {_floor_major()}", id="one-field"),
            pytest.param(lambda: f"echo '{_floor_stub_output()} 1'", id="three-fields"),
            pytest.param(lambda: "exit 1", id="exit-1-with-no-output"),
            pytest.param(lambda: f"echo '{_floor_stub_output()}'; exit 1", id="valid-line-then-exit-1"),
            pytest.param(
                lambda: f"echo 'Python launcher banner'; echo '{_floor_stub_output()}'", id="banner-line-before-the-version"
            ),
            pytest.param(lambda: f"echo '{_floor_major()} {_digits(1000)}'", id="thousand-digit-minor"),
            pytest.param(lambda: f"echo 'ZQXJ {_floor_minor()}'", id="non-numeric-major"),
            pytest.param(lambda: f"echo '{_digits(1000)} {_floor_minor()}'", id="thousand-digit-major"),
        ],
    )
    def test_an_unreadable_version_returns_2_and_prints_nothing(
        self, tmp_path: Path, shell_options: str, stub_body: Callable[[], str]
    ) -> None:
        _write_python3_stub(tmp_path / "bin", stub_body())

        result = _run_probe(tmp_path / "bin", shell_options=shell_options)

        assert result.returncode == 2, _output_of(result)
        assert result.stdout == "", _output_of(result)

    def test_a_missing_python3_returns_2_and_prints_nothing(self, tmp_path: Path, shell_options: str) -> None:
        (tmp_path / "bin").mkdir()

        result = _run_probe(tmp_path / "bin", shell_options=shell_options)

        assert result.returncode == 2, _output_of(result)
        assert result.stdout == "", _output_of(result)
        assert result.stderr == "", _output_of(result)

    def test_a_glob_character_in_the_output_is_not_expanded_against_the_working_directory(
        self, tmp_path: Path, shell_options: str
    ) -> None:
        """An unquoted field split would expand `*` to the file named after the floor's minor and read the floor version."""
        _write_python3_stub(tmp_path / "bin", f"echo '{_floor_major()} *'")
        workdir = tmp_path / "workdir"
        workdir.mkdir()
        (workdir / _floor_minor()).write_text("")

        result = _run_probe(tmp_path / "bin", shell_options=shell_options, cwd=workdir)

        assert result.returncode == 2, _output_of(result)
        assert result.stdout == "", _output_of(result)

    def test_stderr_from_python3_does_not_reach_stderr(self, tmp_path: Path, shell_options: str) -> None:
        _write_python3_stub(tmp_path / "bin", f"echo '{_floor_stub_output()}'; echo 'shim noise' >&2")

        result = _run_probe(tmp_path / "bin", shell_options=shell_options)

        assert result.returncode == 0, _output_of(result)
        assert result.stderr == "", _output_of(result)

    def test_a_caller_ifs_without_a_space_does_not_change_the_result(self, tmp_path: Path) -> None:
        """The field split reads the two version fields on a space, so a caller's `IFS=$'\\n'` must not merge them."""
        _write_python3_stub(tmp_path / "bin", f"echo '{_floor_stub_output()}'")

        result = _run_probe(tmp_path / "bin", caller_setup="IFS=$'\\n'")

        assert result.returncode == 0, _output_of(result)
        assert result.stdout == f"{_floor_version()}\n", _output_of(result)

    def test_a_runner_is_invoked_with_python3_and_the_isolating_flags(self, tmp_path: Path) -> None:
        argv_record = tmp_path / "runner-argv"
        runner = tmp_path / "recording-runner"
        _write_executable(runner, f"#!/bin/sh\nprintf '%s\\n' \"$1\" \"$2\" \"$3\" \"$4\" > '{argv_record}'\nexec \"$@\"\n")
        _write_python3_stub(tmp_path / "bin", f"echo '{_floor_stub_output()}'")

        result = _run_probe(tmp_path / "bin", runner=(str(runner),))

        assert result.returncode == 0, _output_of(result)
        assert result.stdout == f"{_floor_version()}\n", _output_of(result)
        assert argv_record.read_text().splitlines() == ["python3", "-I", "-S", "-c"], _output_of(result)


class TestProbeAgainstTheRealInterpreter:
    def test_the_running_interpreter_meets_the_floor_and_prints_its_own_version(self, tmp_path: Path) -> None:
        _link_real_python3(tmp_path / "bin")

        result = _run_probe(tmp_path / "bin")

        assert result.returncode == 0, _output_of(result)
        assert result.stdout == f"{sys.version_info.major}.{sys.version_info.minor}\n", _output_of(result)
        assert result.stderr == "", _output_of(result)

    def test_a_pythonpath_sitecustomize_does_not_change_the_result(self, tmp_path: Path) -> None:
        """Without `-I -S`, the planted `sitecustomize` prints a first line `2 7`, which reads as below the floor."""
        _link_real_python3(tmp_path / "bin")
        planted = tmp_path / "planted"
        planted.mkdir()
        (planted / "sitecustomize.py").write_text("print('2 7')\n")

        result = _run_probe(tmp_path / "bin", env_extra={"PYTHONPATH": str(planted)})

        assert result.returncode == 0, _output_of(result)
        assert result.stdout == f"{sys.version_info.major}.{sys.version_info.minor}\n", _output_of(result)


class TestConstants:
    def test_an_inherited_floor_value_does_not_move_the_floor(self) -> None:
        """The constants are assigned unconditionally, so an exported 99 is ignored."""
        script = '. "$1" >/dev/null 2>&1; printf "%s %s" "$_PYTHON_FLOOR_MAJOR" "$_PYTHON_FLOOR_MINOR"'
        without_export = _run_in_bash(script, str(_CONFIG_SH), timeout=_BASH_RUN_TIMEOUT_SECONDS)
        with_export = _run_in_bash(
            "export _PYTHON_FLOOR_MINOR=99\n" + script, str(_CONFIG_SH), timeout=_BASH_RUN_TIMEOUT_SECONDS
        )

        assert without_export.stdout.split(" ")[1].isdigit(), _output_of(without_export)
        assert with_export.stdout == without_export.stdout, _output_of(with_export)

    def test_sourcing_twice_succeeds_and_leaves_both_constants_writable(self) -> None:
        result = _run_in_bash(
            '. "$1" && . "$1" && declare -p _PYTHON_FLOOR_MAJOR _PYTHON_FLOOR_MINOR',
            str(_CONFIG_SH),
            timeout=_BASH_RUN_TIMEOUT_SECONDS,
        )

        assert result.returncode == 0, _output_of(result)
        declarations = result.stdout.splitlines()
        assert len(declarations) == 2, _output_of(result)
        for declaration in declarations:
            attributes = declaration.split(" ")[1]
            assert "r" not in attributes, f"constant is readonly, so a second source fails: {declaration!r}"


class TestInstallBlock:
    @pytest.mark.parametrize(
        "stub_output_and_version",
        [
            pytest.param(lambda: _stub_output_and_dotted_version(0), id="exactly-the-floor"),
            pytest.param(lambda: _stub_output_and_dotted_version(1), id="minor-above"),
            pytest.param(lambda: (f"{_floor_major()} 100", f"{_floor_major()}.100"), id="three-digit-minor"),
        ],
    )
    def test_at_or_above_the_floor_is_silent_and_captures_the_dotted_version(
        self, tmp_path: Path, stub_output_and_version: Callable[[], tuple[str, str]]
    ) -> None:
        reported, expected_version = stub_output_and_version()
        _write_python3_stub(tmp_path / "bin", f"echo '{reported}'")

        result = _run_install_block(tmp_path / "bin")

        assert result.returncode == 0, _output_of(result)
        assert result.stderr == "", _output_of(result)
        assert result.stdout == f"{_AFTER_BLOCK_SENTINEL} {expected_version}\n", _output_of(result)

    @pytest.mark.parametrize(
        "stub_output_and_version",
        [
            pytest.param(lambda: _stub_output_and_dotted_version(-1), id="one-minor-below"),
            pytest.param(lambda: (f"{_floor_major()} 9", f"{_floor_major()}.9"), id="single-digit-minor-below"),
        ],
    )
    def test_below_the_floor_exits_1_naming_the_version_the_floor_the_path_and_the_readme(
        self, tmp_path: Path, stub_output_and_version: Callable[[], tuple[str, str]]
    ) -> None:
        reported, expected_version = stub_output_and_version()
        _write_python3_stub(tmp_path / "bin", f"echo '{reported}'")

        result = _run_install_block(tmp_path / "bin")

        assert result.returncode == 1, _output_of(result)
        assert _AFTER_BLOCK_SENTINEL not in result.stdout, _output_of(result)
        assert expected_version in result.stderr, _output_of(result)
        assert _floor_version() in result.stderr, _output_of(result)
        assert str(tmp_path / "bin" / "python3") in result.stderr, _output_of(result)
        assert README_REQUIREMENTS.text in result.stderr, _output_of(result)

    @pytest.mark.parametrize(
        "stub_body",
        [
            pytest.param("exit 0", id="empty-output"),
            pytest.param("echo garbage", id="garbage"),
            pytest.param("echo 3", id="one-field"),
            pytest.param("exit 1", id="python3-fails"),
        ],
    )
    def test_an_unreadable_version_exits_1_naming_the_floor_and_saying_no_version_was_read(
        self, tmp_path: Path, stub_body: str
    ) -> None:
        _write_python3_stub(tmp_path / "bin", stub_body)

        result = _run_install_block(tmp_path / "bin")

        assert result.returncode == 1, _output_of(result)
        assert _AFTER_BLOCK_SENTINEL not in result.stdout, _output_of(result)
        assert _floor_version() in result.stderr, _output_of(result)
        assert "no version" in result.stderr, _output_of(result)
        assert README_REQUIREMENTS.text in result.stderr, _output_of(result)

    def test_a_path_with_no_python3_says_none_was_found(self, tmp_path: Path) -> None:
        (tmp_path / "bin").mkdir()

        result = _run_install_block(tmp_path / "bin")

        assert result.returncode == 1, _output_of(result)
        assert "none found" in result.stderr, _output_of(result)
        assert _AFTER_BLOCK_SENTINEL not in result.stdout, _output_of(result)

    def test_the_running_interpreter_reaches_the_line_after_the_block(self, tmp_path: Path) -> None:
        _link_real_python3(tmp_path / "bin")

        result = _run_install_block(tmp_path / "bin")

        assert result.returncode == 0, _output_of(result)
        assert _AFTER_BLOCK_SENTINEL in result.stdout, _output_of(result)


class TestPythonFloorCheckPrecedesEveryMutation:
    def test_a_below_floor_python3_ends_the_whole_script_before_it_changes_home(self, tmp_path: Path) -> None:
        """Runs a tmp copy of install.sh, so a block moved below the first `mkdir` leaves files under HOME."""
        result, home = run_install_sh_in_tmp_repo(
            tmp_path,
            python3_stub=f"#!/bin/sh\necho '{_stub_output_and_dotted_version(-1)[0]}'\n",
            git_version_script=f"echo 'git version {_git_minimum_version()}.0'",
        )

        assert result.returncode == 1, _output_of(result)
        assert _floor_version() in result.stderr, _output_of(result)
        left_in_home = list(home.iterdir())
        assert left_in_home == [], (
            f"install.sh changed HOME before the Python floor check ended it: {left_in_home!r} {_output_of(result)}"
        )


class TestPresenceCheckReportsAnAbsentPython3:
    def test_python3_absent_from_path_is_reported_by_the_presence_check_and_exits_1(self, tmp_path: Path) -> None:
        """An absent python3 is reported by name by the presence check, which runs before the floor block."""
        bin_dir = tmp_path / "bin-no-python3"
        bin_dir.mkdir()
        for tool in ("stow", "git", "gh", "jq", "sha256sum", "claude"):
            _write_executable(bin_dir / tool, "#!/bin/sh\nexit 0\n")

        result = _run_in_bash(
            "set -e\n" + _extract_presence_check_block(), env={"PATH": str(bin_dir)}, timeout=_BASH_RUN_TIMEOUT_SECONDS
        )

        assert result.returncode == 1, _output_of(result)
        assert "Missing dependencies: python3" in result.stdout, _output_of(result)


class TestFloorMatchesTheStatementsTiedToIt:
    def test_the_floor_equals_the_python_version_ci_pins(self) -> None:
        result = _run_in_bash(
            '. "$1" && read_ci_python_pin "$2"', str(_CI_PYTHON_PIN_LIB), str(_WORKFLOW), timeout=_BASH_RUN_TIMEOUT_SECONDS
        )

        assert result.returncode == 0, _output_of(result)
        assert result.stdout.strip() == _floor_version(), (
            f"tests.yml pins Python {result.stdout.strip()!r} but _config.sh's floor is {_floor_version()}. {_FLOOR_CHANGE_SITES}"
        )

    def test_the_floor_equals_ruffs_target_version(self) -> None:
        target_version = tomllib.loads(_PYPROJECT.read_text())["tool"]["ruff"]["target-version"]
        major, minor = python_floor_fields()

        assert target_version == f"py{major}{minor}", (
            f"pyproject.toml's [tool.ruff] target-version is {target_version!r} but _config.sh's floor is {major}.{minor}. "
            f"{_FLOOR_CHANGE_SITES}"
        )

    def test_the_floor_equals_the_version_the_readme_python_bullet_opens_with(self) -> None:
        bullet = readme_requirements_bullet("- **Python:**")
        stated_requirement = re.match(r"- \*\*Python:\*\* `python3` ([0-9]+\.[0-9]+) or newer", bullet)
        assert stated_requirement, f"README Requirements Python bullet does not open with '`python3` X.Y or newer': {bullet!r}"

        stated_version = stated_requirement.group(1)
        assert stated_version == _floor_version(), (
            f"README.md § \"Requirements\" states Python {stated_version} but _config.sh's floor is {_floor_version()}. "
            f"{_FLOOR_CHANGE_SITES}"
        )


class TestEnsurepipHintNamesTheVersionTheBlockCaptured:
    def test_the_hint_reads_the_variable_the_python_floor_check_assigns(self) -> None:
        """The hint sits outside every fixture block, and an unset variable there prints the wrong package without an error."""
        assigned = re.search(r"^(\w+)=\$\(_python3_floor_status", _extract_python_floor_check_block(), re.MULTILINE)
        assert assigned, "the python-floor-check block does not assign the version captured from _python3_floor_status"
        hint = re.search(r"sudo apt install python\$\{(\w+)\}-venv", _INSTALL_SH.read_text())
        assert hint, "install.sh has no `sudo apt install python${...}-venv` ensurepip hint"

        assert hint.group(1) == assigned.group(1), (
            f"the ensurepip hint reads ${{{hint.group(1)}}} but the python-floor-check block assigns {assigned.group(1)}"
        )
