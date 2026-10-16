"""Contract tests for helpers.check_shell_exec, helpers.install_shell_exec_guard and helpers.link_bash_into."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from helpers import (
    ShellExecGuardError,
    check_shell_exec,
    install_shell_exec_guard,
    link_bash_into,
    run_hook,
    run_hook_advisory,
    run_hook_context,
    run_hook_payload,
    run_hook_reason,
    run_hook_session_start,
    run_hook_stop,
    run_hook_updated_output,
)

ENV_BASH_SHEBANG_BODY = "#!/usr/bin/env bash\n"
# Bash builtins only: the stub runs under a PATH that holds nothing but bash.
RAN_MARKER_STUB_BODY = ENV_BASH_SHEBANG_BODY + 'echo ran > "${0%/*}/ran"\n'

HOOK_RUNNERS = [
    run_hook,
    run_hook_advisory,
    run_hook_reason,
    run_hook_context,
    run_hook_payload,
    run_hook_stop,
    run_hook_updated_output,
    run_hook_session_start,
]


@pytest.fixture(autouse=True)
def _guard_shell_exec(monkeypatch):
    """This tree has no conftest, so each test installs the guard itself."""
    install_shell_exec_guard(monkeypatch)


def _write_executable(path: Path, body: str) -> Path:
    path.write_text(body)
    path.chmod(0o755)
    return path


@pytest.fixture
def ran_marker_stub(tmp_path: Path) -> Path:
    return _write_executable(tmp_path / "stub.sh", RAN_MARKER_STUB_BODY)


@pytest.fixture
def bash_less_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "bash-less"
    directory.mkdir()
    return directory


@pytest.fixture
def bash_only_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "bash-only"
    directory.mkdir()
    link_bash_into(directory)
    return directory


# -- link_bash_into -----------------------------------------------------------


def test_link_bash_into_links_the_callers_bash(tmp_path: Path) -> None:
    link = link_bash_into(tmp_path)

    assert link == tmp_path / "bash"
    assert link.resolve() == Path(shutil.which("bash")).resolve()


def test_link_bash_into_twice_on_one_directory_succeeds(tmp_path: Path) -> None:
    first_link = link_bash_into(tmp_path)
    second_link = link_bash_into(tmp_path)

    assert first_link == second_link
    assert second_link.resolve() == Path(shutil.which("bash")).resolve()


def test_link_bash_into_fails_over_a_link_to_another_file(tmp_path: Path) -> None:
    other_file = tmp_path / "not-bash"
    other_file.write_text("")
    (tmp_path / "bash").symlink_to(other_file)

    with pytest.raises(AssertionError, match="not the caller's bash"):
        link_bash_into(tmp_path)


def test_link_bash_into_fails_when_the_caller_has_no_bash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    target_dir = tmp_path / "target"
    target_dir.mkdir()

    with pytest.raises(AssertionError, match="bash is not on PATH"):
        link_bash_into(target_dir)


# -- Guard installed on subprocess.run ----------------------------------------


@pytest.mark.parametrize("runner", HOOK_RUNNERS, ids=lambda runner: runner.__name__)
def test_runner_raises_for_an_env_bash_stub_under_a_path_without_bash(
    runner, ran_marker_stub: Path, bash_less_dir: Path
) -> None:
    with pytest.raises(ShellExecGuardError):
        runner(ran_marker_stub, {}, extra_env={"PATH": str(bash_less_dir)})

    assert not (ran_marker_stub.parent / "ran").exists()


@pytest.mark.parametrize("runner", HOOK_RUNNERS, ids=lambda runner: runner.__name__)
def test_runner_raises_for_an_env_bash_stub_when_the_env_has_no_path_key(
    runner, ran_marker_stub: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PATH")

    with pytest.raises(ShellExecGuardError):
        runner(ran_marker_stub, {}, extra_env={"UNRELATED_VARIABLE": "1"})


@pytest.mark.parametrize("runner", HOOK_RUNNERS, ids=lambda runner: runner.__name__)
def test_runner_does_not_raise_for_an_env_bash_stub_with_bash_on_path(
    runner, ran_marker_stub: Path, bash_only_dir: Path
) -> None:
    runner(ran_marker_stub, {}, extra_env={"PATH": str(bash_only_dir)})

    assert (ran_marker_stub.parent / "ran").read_text() == "ran\n"


def test_bare_subprocess_run_raises_for_an_env_bash_stub_under_a_path_without_bash(
    ran_marker_stub: Path, bash_less_dir: Path
) -> None:
    with pytest.raises(ShellExecGuardError):
        subprocess.run([str(ran_marker_stub)], env={"PATH": str(bash_less_dir)}, capture_output=True, check=False)


def test_bare_subprocess_run_raises_the_guard_error_not_the_called_process_error_under_check(
    ran_marker_stub: Path, bash_less_dir: Path
) -> None:
    with pytest.raises(ShellExecGuardError):
        subprocess.run([str(ran_marker_stub)], env={"PATH": str(bash_less_dir)}, capture_output=True, check=True)


def test_bare_subprocess_run_resolves_a_relative_argv0_against_cwd(
    ran_marker_stub: Path, bash_less_dir: Path
) -> None:
    with pytest.raises(ShellExecGuardError):
        subprocess.run(
            [f".{os.sep}{ran_marker_stub.name}"],
            cwd=ran_marker_stub.parent,
            env={"PATH": str(bash_less_dir)},
            capture_output=True,
            check=False,
        )


def test_bare_subprocess_run_does_not_raise_for_an_env_bash_stub_with_bash_on_path(
    ran_marker_stub: Path, bash_only_dir: Path
) -> None:
    result = subprocess.run(
        [str(ran_marker_stub)], env={"PATH": str(bash_only_dir)}, capture_output=True, check=False
    )

    assert result.returncode == 0
    assert (ran_marker_stub.parent / "ran").read_text() == "ran\n"


def test_bare_subprocess_run_of_a_non_shell_program_passes_through() -> None:
    result = subprocess.run(
        [sys.executable, "-c", "print('ok')"], env={"UNRELATED_VARIABLE": "1"}, capture_output=True, text=True, check=False
    )

    assert result.stdout == "ok\n"


def test_bare_subprocess_run_keeps_called_process_error_for_an_ordinary_failure_under_check(
    bash_less_dir: Path,
) -> None:
    with pytest.raises(subprocess.CalledProcessError) as raised:
        subprocess.run(
            [sys.executable, "-c", "raise SystemExit(3)"],
            env={"PATH": str(bash_less_dir)},
            capture_output=True,
            check=True,
        )

    assert raised.value.returncode == 3


def test_bare_subprocess_run_with_shell_true_is_not_inspected(
    ran_marker_stub: Path, bash_less_dir: Path
) -> None:
    result = subprocess.run(
        str(ran_marker_stub), shell=True, env={"PATH": str(bash_less_dir)}, capture_output=True, check=False
    )

    assert result.returncode in {126, 127}
    assert not (ran_marker_stub.parent / "ran").exists()


# -- check_shell_exec control table -------------------------------------------

ENV_ERROR_GNU_CURLY = "/usr/bin/env: ‘bash’: No such file or directory\n"
ENV_ERROR_GNU_ASCII = "/usr/bin/env: 'bash': No such file or directory\n"
ENV_ERROR_BSD_UNQUOTED = "env: bash: No such file or directory\n"
ENV_ERROR_BUSYBOX = "env: can't execute 'bash': No such file or directory\n"
ENV_ERROR_LOCALIZED ="/usr/bin/env: ‘bash’: Aucun fichier ou dossier de ce type\n"
ENV_ERROR_PERMISSION_DENIED = "/usr/bin/env: ‘bash’: Permission denied\n"
ENV_ERROR_OTHER_PROGRAM = "/usr/bin/env: 'missing-tool': No such file or directory\n"
SCRIPT_OWN_COMMAND_NOT_FOUND = "./stub.sh: line 3: missing-tool: command not found\n"
# Look-alikes whose prefix or program name only resembles `env`'s error about `bash`.
ENV_ERROR_PROGRAM_PREFIXED_BASH = "/usr/bin/env: 'bashdb': No such file or directory\n"
ENV_ERROR_FOREIGN_ENV_PREFIX = "xenv: 'bash': No such file or directory\n"

HAS_PATH = {"PATH": "/usr/bin:/bin"}


class Stubs:
    """Files an argv can name: one per shebang the guard distinguishes, and a binary."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.env_bash = _write_executable(directory / "env_bash.sh", ENV_BASH_SHEBANG_BODY + "exit 0\n")
        self.bin_bash = _write_executable(directory / "bin_bash.sh", "#!/bin/bash\nexit 0\n")
        self.binary = Path(sys.executable)
        self.real_bash = Path(shutil.which("bash"))


@pytest.fixture
def stubs(tmp_path: Path) -> Stubs:
    return Stubs(tmp_path)


def _finished(argv, returncode: int, stderr) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(argv, returncode, stdout=None, stderr=stderr)


# (argv builder, env, returncode, stderr, expect guard error)
CONTROL_ROWS = {
    "never-ran-gnu-curly-quotes": (lambda s: [str(s.env_bash)], HAS_PATH, 127, ENV_ERROR_GNU_CURLY, True),
    "never-ran-gnu-ascii-quotes": (lambda s: [str(s.env_bash)], HAS_PATH, 127, ENV_ERROR_GNU_ASCII, True),
    "never-ran-bsd-unquoted": (lambda s: [str(s.env_bash)], HAS_PATH, 127, ENV_ERROR_BSD_UNQUOTED, True),
    "never-ran-busybox-cant-execute": (lambda s: [str(s.env_bash)], HAS_PATH, 127, ENV_ERROR_BUSYBOX, True),
    "never-ran-localized-message": (lambda s: [str(s.env_bash)], HAS_PATH, 127, ENV_ERROR_LOCALIZED, True),
    "never-ran-permission-denied-126": (lambda s: [str(s.env_bash)], HAS_PATH, 126, ENV_ERROR_PERMISSION_DENIED, True),
    "never-ran-stderr-not-captured": (lambda s: [str(s.env_bash)], HAS_PATH, 127, None, True),
    "never-ran-bytes-argv-and-bytes-stderr": (
        lambda s: [os.fsencode(s.env_bash)], HAS_PATH, 127, ENV_ERROR_GNU_CURLY.encode(), True
    ),
    "never-ran-path-argv-element": (lambda s: [s.env_bash], HAS_PATH, 127, ENV_ERROR_GNU_ASCII, True),
    "script-own-command-not-found-127": (lambda s: [str(s.env_bash)], HAS_PATH, 127, SCRIPT_OWN_COMMAND_NOT_FOUND, False),
    "script-own-env-failure-about-another-program": (
        lambda s: [str(s.env_bash)], HAS_PATH, 127, ENV_ERROR_OTHER_PROGRAM, False
    ),
    "program-prefixed-bash-env-error": (
        lambda s: [str(s.env_bash)], HAS_PATH, 127, ENV_ERROR_PROGRAM_PREFIXED_BASH, False
    ),
    "foreign-env-prefix-error": (lambda s: [str(s.env_bash)], HAS_PATH, 127, ENV_ERROR_FOREIGN_ENV_PREFIX, False),
    "script-with-a-different-shebang":(lambda s: [str(s.bin_bash)], HAS_PATH, 127, ENV_ERROR_GNU_CURLY, False),
    "exit-0-with-env-looking-stderr": (lambda s: [str(s.env_bash)], HAS_PATH, 0, ENV_ERROR_GNU_CURLY, False),
    "exit-1-is-not-an-exec-failure": (lambda s: [str(s.env_bash)], HAS_PATH, 1, ENV_ERROR_GNU_CURLY, False),
    "empty-stderr-is-captured-and-not-env": (lambda s: [str(s.env_bash)], HAS_PATH, 127, "", False),
    "binary-argv0-is-not-a-target": (lambda s: [str(s.binary)], HAS_PATH, 127, ENV_ERROR_GNU_CURLY, False),
    "bash-dash-c-has-no-target": (lambda s: ["bash", "-c", "exit 0"], HAS_PATH, 127, ENV_ERROR_GNU_CURLY, False),
    "script-behind-absolute-bash-is-not-a-target": (
        lambda s: [str(s.real_bash), str(s.env_bash)], HAS_PATH, 127, ENV_ERROR_BSD_UNQUOTED, False
    ),
    "script-behind-bare-bash-is-not-a-target": (
        lambda s: ["bash", str(s.env_bash)], HAS_PATH, 127, ENV_ERROR_BSD_UNQUOTED, False
    ),
    "no-path-script-behind-absolute-bash-is-not-a-target": (
        lambda s: [str(s.real_bash), str(s.env_bash)], {"HOME": "/tmp"}, 0, "", False
    ),
    "no-path-env-bash-script": (lambda s: [str(s.env_bash)], {"HOME": "/tmp"}, 0, "", True),
    "no-path-bash-dash-c": (lambda s: ["bash", "-c", "exit 0"], {"HOME": "/tmp"}, 0, "", True),
    "no-path-bytes-env-keys": (lambda s: ["bash", "-c", "exit 0"], {b"HOME": b"/tmp"}, 0, "", True),
    "no-path-absolute-bash-dash-c": (lambda s: [str(s.real_bash), "-c", "exit 0"], {"HOME": "/tmp"}, 0, "", False),
    "no-path-script-with-a-different-shebang": (lambda s: [str(s.bin_bash)], {"HOME": "/tmp"}, 0, "", False),
    "no-path-binary-argv0": (lambda s: [str(s.binary)], {"HOME": "/tmp"}, 0, "", False),
    "inherited-env-is-not-inspected": (lambda s: [str(s.env_bash)], None, 0, "", False),
    "path-key-present-with-empty-value": (lambda s: [str(s.env_bash)], {"PATH": ""}, 0, "", False),
}


@pytest.mark.parametrize("row_id", CONTROL_ROWS)
def test_check_shell_exec_control_table(row_id: str, stubs: Stubs) -> None:
    build_argv, env, returncode, stderr, expect_guard_error = CONTROL_ROWS[row_id]
    argv = build_argv(stubs)
    completed = _finished(argv, returncode, stderr)

    if expect_guard_error:
        with pytest.raises(ShellExecGuardError):
            check_shell_exec(argv, env, None, completed)
    else:
        check_shell_exec(argv, env, None, completed)


def test_check_shell_exec_advises_capturing_stderr_when_stderr_is_not_captured(stubs: Stubs) -> None:
    argv = [str(stubs.env_bash)]
    completed = _finished(argv, 127, None)

    with pytest.raises(ShellExecGuardError, match="stderr was not captured") as raised:
        check_shell_exec(argv, HAS_PATH, None, completed)

    assert "Capture stderr for this call" in str(raised.value)
    assert "126/127" in str(raised.value)


def test_check_shell_exec_advises_link_bash_into_when_captured_stderr_is_an_env_error(stubs: Stubs) -> None:
    argv = [str(stubs.env_bash)]
    completed = _finished(argv, 127, ENV_ERROR_GNU_CURLY)

    with pytest.raises(ShellExecGuardError, match="the script never ran") as raised:
        check_shell_exec(argv, HAS_PATH, None, completed)

    assert "helpers.link_bash_into" in str(raised.value)
    assert "stderr was not captured" not in str(raised.value)


def test_check_shell_exec_resolves_a_relative_target_against_cwd(stubs: Stubs) -> None:
    argv = [f".{os.sep}{stubs.env_bash.name}"]
    completed = _finished(argv, 127, ENV_ERROR_GNU_CURLY)

    with pytest.raises(ShellExecGuardError):
        check_shell_exec(argv, HAS_PATH, stubs.directory, completed)


def test_check_shell_exec_ignores_a_missing_target(tmp_path: Path) -> None:
    argv = [str(tmp_path / "does-not-exist.sh")]

    check_shell_exec(argv, HAS_PATH, None, _finished(argv, 127, ENV_ERROR_GNU_CURLY))


def test_check_shell_exec_ignores_a_call_with_an_empty_argv() -> None:
    check_shell_exec([], HAS_PATH, None, _finished([], 127, None))


def test_shell_exec_guard_error_is_an_assertion_error() -> None:
    assert issubclass(ShellExecGuardError, AssertionError)

