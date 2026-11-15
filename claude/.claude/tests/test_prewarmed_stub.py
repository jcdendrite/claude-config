"""Contract tests for helpers.write_prewarmed_stub, which pays a stub's slow first exec at write time without running its body."""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path

# A namespace import beside the name imports: the tests read `_PREWARM_EXEC_PATH` and monkeypatch it, and the helper
# reads that module global at call time, so only a patched attribute on the module object reaches it.
import helpers
import pytest
from helpers import (
    ShellExecGuardError,
    _forced_fallback_path_env,
    assert_each_stub_warm_exec_ed_once,
    install_resume_context_stub,
    install_shell_exec_guard,
    record_subprocess_runs,
    write_prewarmed_stub,
)

ACCEPTED_SHEBANGS = ["#!/usr/bin/env bash", "#!/usr/bin/env sh", "#!/bin/bash", "#!/bin/sh"]
ENV_BASH_SHEBANG = "#!/usr/bin/env bash"
SENTINEL_BYTES = b"sentinel bytes the helper must not rewrite\n"
NO_GUARD_LINE = ":"


@pytest.fixture
def recorded_runs(monkeypatch: pytest.MonkeyPatch) -> list[tuple[tuple, dict]]:
    """Every `subprocess.run` call as `(args, kwargs)`, each still run for real."""
    return record_subprocess_runs(monkeypatch)


def _log_appending_script(shebang: str, log: Path) -> str:
    return f"{shebang}\nprintf 'ran\\n' >> {shlex.quote(str(log))}\n"


def _empty_directory(tmp_path: Path) -> Path:
    empty = tmp_path / "empty"
    empty.mkdir()
    return empty


@pytest.mark.parametrize("shebang", ACCEPTED_SHEBANGS)
def test_warm_exec_runs_once_with_only_path_and_guard_variable_and_no_body_line(
    shebang: str, tmp_path: Path, recorded_runs: list[tuple[tuple, dict]]
) -> None:
    stub = tmp_path / "stub"
    log = tmp_path / "ran.log"

    returned = write_prewarmed_stub(stub, _log_appending_script(shebang, log))

    assert returned == stub
    assert len(recorded_runs) == 1
    args, kwargs = recorded_runs[0]
    assert args[0] == [str(stub)]
    assert kwargs["env"] == {"PATH": helpers._PREWARM_EXEC_PATH, helpers._PREWARM_GUARD_VARIABLE: str(stub)}
    assert os.access(stub, os.X_OK)
    assert not log.exists(), "the warm exec ran a body line"

    subprocess.run([str(stub)], check=True)

    assert log.read_text() == "ran\n"


def test_env_bash_stub_warms_under_the_bash_its_test_runs(tmp_path: Path, recorded_runs: list[tuple[tuple, dict]]) -> None:
    write_prewarmed_stub(tmp_path / "stub", f"{ENV_BASH_SHEBANG}\nexit 0\n")

    warm_exec_path = recorded_runs[0][1]["env"]["PATH"]

    assert shutil.which("bash", path=warm_exec_path) == shutil.which("bash")


@pytest.mark.parametrize(
    "script",
    [
        pytest.param("#!/usr/bin/env python3\nprint(1)\n", id="python-shebang"),
        pytest.param("#!/bin/shx\n", id="shx-interpreter"),
        pytest.param("#!/bin/bashx\n", id="bashx-interpreter"),
        pytest.param("#!/usr/bin/env bashx\n", id="env-bashx-interpreter"),
        pytest.param("echo hi\n", id="no-shebang"),
        pytest.param("", id="empty-script"),
        pytest.param("#! /bin/bash\n", id="space-after-hashbang"),
        pytest.param("#!/bin/bash\r\n", id="crlf-first-line"),
        pytest.param("#!/bin/bash -e\n", id="trailing-flag"),
    ],
)
def test_first_line_outside_the_grammar_raises_before_anything_is_created(script: str, tmp_path: Path) -> None:
    stub = tmp_path / "stub"

    with pytest.raises(ValueError, match="shebang"):
        write_prewarmed_stub(stub, script)

    assert list(tmp_path.iterdir()) == []


def test_relative_path_raises_before_anything_is_created(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValueError, match="absolute"):
        write_prewarmed_stub(Path("relative-stub"), "#!/bin/sh\n")

    assert list(tmp_path.iterdir()) == []


def test_rejected_script_leaves_an_existing_file_unchanged(tmp_path: Path) -> None:
    existing = tmp_path / "stub"
    existing.write_bytes(SENTINEL_BYTES)

    with pytest.raises(ValueError, match="shebang"):
        write_prewarmed_stub(existing, "#!/usr/bin/env python3\nprint(1)\n")

    assert existing.read_bytes() == SENTINEL_BYTES


def test_shebang_alone_without_trailing_newline_ends_after_the_guard_line(tmp_path: Path) -> None:
    stub = tmp_path / "stub"

    write_prewarmed_stub(stub, "#!/bin/sh")

    assert len(stub.read_text().splitlines()) == 2
    plain_run = subprocess.run([str(stub)], stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False)
    assert plain_run.returncode == 0
    assert plain_run.stdout == ""
    assert plain_run.stderr == ""


@pytest.mark.parametrize("shebang", ["#!/bin/bash", ENV_BASH_SHEBANG])
def test_shell_startup_files_do_not_run_during_the_warm_exec(
    shebang: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sentinel = tmp_path / "startup-ran"
    startup_file = tmp_path / "startup.sh"
    startup_file.write_text(f"echo startup-ran\ntouch {shlex.quote(str(sentinel))}\n")
    monkeypatch.setenv("BASH_ENV", str(startup_file))
    monkeypatch.setenv("ENV", str(startup_file))

    write_prewarmed_stub(tmp_path / "stub", f"{shebang}\nexit 0\n")

    assert not sentinel.exists()


def test_env_bash_stub_warms_under_the_session_bash_when_the_test_has_emptied_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recorded_runs: list[tuple[tuple, dict]]
) -> None:
    session_bash = shutil.which("bash")
    assert session_bash is not None
    monkeypatch.setenv("PATH", str(_empty_directory(tmp_path)))

    write_prewarmed_stub(tmp_path / "stub", f"{ENV_BASH_SHEBANG}\nexit 0\n")

    warm_exec_path = recorded_runs[0][1]["env"]["PATH"]
    assert warm_exec_path.split(os.pathsep)[0] == os.path.dirname(os.path.abspath(session_bash))
    assert shutil.which("bash", path=warm_exec_path) == session_bash


def test_exec_path_puts_the_session_bash_directory_ahead_of_the_default_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_bash_directory = tmp_path / "session-bash"
    monkeypatch.setattr(shutil, "which", lambda command: str(session_bash_directory / command))

    assert helpers._prewarm_exec_path() == os.pathsep.join([str(session_bash_directory), os.defpath])


def test_exec_path_is_the_default_path_alone_when_the_session_has_no_bash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda command: None)

    assert helpers._prewarm_exec_path() == os.defpath


def test_directory_name_with_space_dollar_and_backtick_warms_and_runs(tmp_path: Path) -> None:
    awkward_directory = tmp_path / "dir with $dollar and `tick`"
    awkward_directory.mkdir()
    log = awkward_directory / "ran.log"
    stub = awkward_directory / "stub"

    write_prewarmed_stub(stub, _log_appending_script("#!/bin/bash", log))
    subprocess.run([str(stub)], check=True)

    assert log.read_text() == "ran\n"


def test_symlink_at_the_path_is_replaced_not_written_through(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.write_bytes(SENTINEL_BYTES)
    stub = tmp_path / "stub"
    stub.symlink_to(target)

    write_prewarmed_stub(stub, "#!/bin/sh\nexit 0\n")

    assert target.read_bytes() == SENTINEL_BYTES
    assert stub.is_file()
    assert not stub.is_symlink()


def test_dangling_symlink_at_the_path_is_replaced_without_creating_its_target(tmp_path: Path) -> None:
    missing_target = tmp_path / "missing-target"
    stub = tmp_path / "stub"
    stub.symlink_to(missing_target)

    write_prewarmed_stub(stub, "#!/bin/sh\nexit 0\n")

    assert not missing_target.exists()
    assert stub.is_file()
    assert not stub.is_symlink()


def test_rewriting_an_already_warmed_stub_replaces_its_body_and_warms_it_again(
    tmp_path: Path, recorded_runs: list[tuple[tuple, dict]]
) -> None:
    stub = tmp_path / "stub"
    first_log = tmp_path / "first.log"
    second_log = tmp_path / "second.log"
    write_prewarmed_stub(stub, _log_appending_script("#!/bin/sh", first_log))

    write_prewarmed_stub(stub, _log_appending_script("#!/bin/sh", second_log))

    assert_each_stub_warm_exec_ed_once(recorded_runs, [stub, stub])
    subprocess.run([str(stub)], check=True)
    assert not first_log.exists()
    assert second_log.read_text() == "ran\n"


def test_warm_exec_that_exits_nonzero_raises_with_the_exec_diagnostics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pathless = _empty_directory(tmp_path)
    monkeypatch.setattr(helpers, "_PREWARM_EXEC_PATH", str(pathless))
    stub = tmp_path / "stub"

    with pytest.raises(AssertionError) as raised:
        write_prewarmed_stub(stub, f"{ENV_BASH_SHEBANG}\nexit 0\n")

    message = str(raised.value)
    assert f"path={stub}" in message
    assert f"PATH={pathless}" in message
    assert "returncode=127" in message
    assert "stdout=''" in message
    assert "bash" in message.split("stderr=", 1)[1], "env's missing-bash error should appear as stderr"


def test_warm_exec_printing_other_than_its_path_raises_with_the_exec_diagnostics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(helpers, "_PREWARM_GUARD_LINE", NO_GUARD_LINE)
    stub = tmp_path / "stub"

    with pytest.raises(AssertionError) as raised:
        write_prewarmed_stub(stub, "#!/bin/sh\nprintf unexpected\nprintf diagnostic >&2\nexit 0\n")

    message = str(raised.value)
    assert f"path={stub}" in message
    assert f"PATH={helpers._PREWARM_EXEC_PATH}" in message
    assert "returncode=0" in message
    assert "stdout='unexpected'" in message
    assert "stderr='diagnostic'" in message


def test_warm_exec_that_cannot_start_raises_with_the_exec_diagnostics_chained_from_the_os_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A stub left without its execute bit makes the real exec raise PermissionError.
    monkeypatch.setattr(Path, "chmod", lambda self, mode, **_kwargs: None)
    stub = tmp_path / "stub"

    with pytest.raises(AssertionError) as raised:
        write_prewarmed_stub(stub, "#!/bin/sh\nexit 0\n")

    message = str(raised.value)
    assert f"path={stub}" in message
    assert f"PATH={helpers._PREWARM_EXEC_PATH}" in message
    assert "shebang='#!/bin/sh'" in message
    assert "could not start" in message
    assert isinstance(raised.value.__cause__, PermissionError)


class TestSharedWritersWarmEveryStubTheyLeave:
    """Each shared writer that leaves a stub must exec it once with the guard variable set to its path."""

    def test_install_resume_context_stub(self, tmp_path: Path, recorded_runs: list[tuple[tuple, dict]]) -> None:
        stub_bin = install_resume_context_stub(tmp_path)

        assert_each_stub_warm_exec_ed_once(recorded_runs, [stub_bin / "resume-context"])

    def test_forced_fallback_path_env(self, tmp_path: Path, recorded_runs: list[tuple[tuple, dict]]) -> None:
        shim_dir = Path(_forced_fallback_path_env(tmp_path).split(os.pathsep)[0])

        assert_each_stub_warm_exec_ed_once(recorded_runs, [shim_dir / "realpath"])


class TestUnderTheShellExecGuard:
    """The hooks and scripts trees install the shell-exec guard on every test; this tree does not."""

    def test_env_bash_stub_still_warms(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        install_shell_exec_guard(monkeypatch)

        write_prewarmed_stub(tmp_path / "stub", f"{ENV_BASH_SHEBANG}\nexit 0\n")

    def test_env_bash_stub_without_bash_on_the_exec_path_raises_the_guards_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_shell_exec_guard(monkeypatch)
        monkeypatch.setattr(helpers, "_PREWARM_EXEC_PATH", str(_empty_directory(tmp_path)))

        with pytest.raises(ShellExecGuardError):
            write_prewarmed_stub(tmp_path / "stub", f"{ENV_BASH_SHEBANG}\nexit 0\n")

    def test_nonzero_warm_exec_the_guard_does_not_flag_raises_the_helpers_own_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_shell_exec_guard(monkeypatch)
        monkeypatch.setattr(helpers, "_PREWARM_GUARD_LINE", NO_GUARD_LINE)
        stub = tmp_path / "stub"

        with pytest.raises(AssertionError) as raised:
            write_prewarmed_stub(stub, "#!/bin/sh\nexit 3\n")

        assert not isinstance(raised.value, ShellExecGuardError)
        message = str(raised.value)
        assert f"path={stub}" in message
        assert "returncode=3" in message
