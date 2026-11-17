"""Contract tests for helpers.check_scaled_path_stubs_warm and helpers.install_unwarmed_stub_guard."""
from __future__ import annotations

import asyncio
import functools
import os
import shlex
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

import helpers
import pytest
from helpers import (
    UnwarmedStubError,
    check_scaled_path_stubs_warm,
    install_unwarmed_stub_guard,
    write_prewarmed_stub,
    write_scaled_timeout_shim,
)

COLD_BODY = "exit 0\n"
MODE_WITHOUT_READ_BITS = 0o111
# Bounds a check that returns at once, so only a blocked open can reach it; long enough that a loaded host does not trip it.
FIFO_CHECK_WATCHDOG_SECONDS = 5


def _make_marker_directory(entry: Path) -> Path:
    marker_directory = entry / helpers._SCALED_TIMEOUT_MARKER_DIRNAME
    marker_directory.mkdir(parents=True)
    return entry


def _write_cold_stub(path: Path, script: str = f"#!/bin/sh\n{COLD_BODY}") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(script)
    path.chmod(0o755)
    return path


def _path_env(*entries: Path | str) -> dict[str, str]:
    return {"PATH": os.pathsep.join(str(entry) for entry in entries)}


def _warm_stub(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    return write_prewarmed_stub(path, f"#!/bin/sh\n{COLD_BODY}")


@pytest.fixture
def marker_bin(tmp_path: Path) -> Path:
    return _make_marker_directory(tmp_path / "bin")


@pytest.mark.parametrize(
    "script",
    [
        *(pytest.param(f"{shebang}\nexit 0\n", id=shebang) for shebang in sorted(helpers._PREWARM_SHEBANGS)),
        pytest.param("#!/bin/sh", id="shebang-only-without-newline"),
        pytest.param(f"#!/bin/sh\n:\n{helpers._PREWARM_GUARD_LINE}\n", id="guard-line-on-line-three"),
        pytest.param(f"#!/bin/sh\n{helpers._PREWARM_GUARD_LINE} && :\nexit 0\n", id="guard-line-with-trailing-command"),
        pytest.param(f"#!/bin/sh\n {helpers._PREWARM_GUARD_LINE}\nexit 0\n", id="guard-line-with-leading-space"),
    ],
)
def test_cold_sh_family_stub_beside_the_marker_raises_naming_the_stub(script: str, tmp_path: Path, marker_bin: Path) -> None:
    cold_stub = _write_cold_stub(marker_bin / "cold-stub", script)

    with pytest.raises(UnwarmedStubError) as raised:
        check_scaled_path_stubs_warm(_path_env(marker_bin), None, tmp_path)

    assert str(cold_stub) in str(raised.value)


def _build_warm_stub(marker_bin: Path, tmp_path: Path) -> None:
    _warm_stub(marker_bin / "warm-stub")


def _build_symlink_to_cold_stub(marker_bin: Path, tmp_path: Path) -> None:
    cold_stub = _write_cold_stub(tmp_path / "elsewhere" / "cold-stub")
    (marker_bin / "linked-stub").symlink_to(cold_stub)


def _build_python_executable(marker_bin: Path, tmp_path: Path) -> None:
    _write_cold_stub(marker_bin / "python-tool", "#!/usr/bin/env python3\nprint(1)\n")


def _build_cold_stub_without_execute_bit(marker_bin: Path, tmp_path: Path) -> None:
    _write_cold_stub(marker_bin / "not-executable").chmod(0o644)


def _build_non_utf8_executable(marker_bin: Path, tmp_path: Path) -> None:
    binary = marker_bin / "binary"
    binary.write_bytes(b"\xff\xfe\x00\x80\xc3\x28")
    binary.chmod(0o755)


def _build_empty_executable(marker_bin: Path, tmp_path: Path) -> None:
    empty = marker_bin / "empty"
    empty.write_bytes(b"")
    empty.chmod(0o755)


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(_build_warm_stub, id="stub-written-by-write-prewarmed-stub"),
        pytest.param(_build_symlink_to_cold_stub, id="symlink-to-a-cold-stub"),
        pytest.param(_build_python_executable, id="python-shebang"),
        pytest.param(_build_cold_stub_without_execute_bit, id="cold-sh-file-without-execute-bit"),
        pytest.param(_build_non_utf8_executable, id="non-utf8-executable"),
        pytest.param(_build_empty_executable, id="empty-executable"),
    ],
)
def test_file_the_check_never_flags_passes(
    build: Callable[[Path, Path], None], tmp_path: Path, marker_bin: Path
) -> None:
    build(marker_bin, tmp_path)

    check_scaled_path_stubs_warm(_path_env(marker_bin), None, tmp_path)


def _bin_dir_with_real_shim(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    if not write_scaled_timeout_shim(bin_dir):
        pytest.skip("neither timeout nor gtimeout is on PATH, so the writer wrote no shim")
    return bin_dir


def test_cold_stub_beside_the_shim_the_real_writer_wrote_raises_naming_only_the_cold_stub(tmp_path: Path) -> None:
    bin_dir = _bin_dir_with_real_shim(tmp_path)
    cold_stub = _write_cold_stub(bin_dir / "cold-stub")

    with pytest.raises(UnwarmedStubError) as raised:
        check_scaled_path_stubs_warm(_path_env(bin_dir), None, tmp_path)

    assert str(cold_stub) in str(raised.value)
    assert str(bin_dir / "timeout") not in str(raised.value)


def test_directory_holding_only_the_shim_the_real_writer_wrote_passes(tmp_path: Path) -> None:
    bin_dir = _bin_dir_with_real_shim(tmp_path)

    check_scaled_path_stubs_warm(_path_env(bin_dir), None, tmp_path)


@pytest.fixture
def bin_dir_with_shim_over_stand_in_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A bin directory holding the shim the real writer wrote over a stand-in `timeout`, so no host binary is needed."""
    stand_in_directory = tmp_path / "stand-in-bin"
    stand_in_directory.mkdir()
    stand_in_timeout = stand_in_directory / "timeout"
    stand_in_timeout.write_text("#!/bin/sh\nexit 0\n")
    stand_in_timeout.chmod(0o755)
    monkeypatch.setenv("PATH", str(stand_in_directory))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    assert write_scaled_timeout_shim(bin_dir)
    return bin_dir


def test_marker_directory_lands_beside_a_shim_written_over_a_stand_in_timeout(
    tmp_path: Path, bin_dir_with_shim_over_stand_in_timeout: Path
) -> None:
    bin_dir = bin_dir_with_shim_over_stand_in_timeout

    assert (bin_dir / "timeout").is_file()
    assert os.access(bin_dir / "timeout", os.X_OK)
    assert (bin_dir / helpers._SCALED_TIMEOUT_MARKER_DIRNAME).is_dir()
    check_scaled_path_stubs_warm(_path_env(bin_dir), None, tmp_path)


def test_cold_stub_beside_a_shim_written_over_a_stand_in_timeout_raises_naming_only_the_cold_stub(
    tmp_path: Path, bin_dir_with_shim_over_stand_in_timeout: Path
) -> None:
    bin_dir = bin_dir_with_shim_over_stand_in_timeout
    cold_stub = _write_cold_stub(bin_dir / "cold-stub")

    with pytest.raises(UnwarmedStubError) as raised:
        check_scaled_path_stubs_warm(_path_env(bin_dir), None, tmp_path)

    assert str(cold_stub) in str(raised.value)
    assert str(bin_dir / "timeout") not in str(raised.value)


def test_entry_holding_a_warm_and_a_cold_stub_raises_naming_only_the_cold_stub(tmp_path: Path, marker_bin: Path) -> None:
    warm_stub = _warm_stub(marker_bin / "warm-stub")
    cold_stub = _write_cold_stub(marker_bin / "cold-stub")

    with pytest.raises(UnwarmedStubError) as raised:
        check_scaled_path_stubs_warm(_path_env(marker_bin), None, tmp_path)

    assert str(cold_stub) in str(raised.value)
    assert str(warm_stub) not in str(raised.value)


def test_marker_entry_listed_twice_on_path_lists_its_cold_stub_once(tmp_path: Path, marker_bin: Path) -> None:
    cold_stub = _write_cold_stub(marker_bin / "cold-stub")

    with pytest.raises(UnwarmedStubError) as raised_for_listed_once:
        check_scaled_path_stubs_warm(_path_env(marker_bin), None, tmp_path)
    with pytest.raises(UnwarmedStubError) as raised_for_listed_twice:
        check_scaled_path_stubs_warm(_path_env(marker_bin, marker_bin), None, tmp_path)

    assert str(raised_for_listed_twice.value).count(str(cold_stub)) == 1
    assert str(raised_for_listed_twice.value) == str(raised_for_listed_once.value)


@pytest.mark.parametrize(
    "mode",
    [
        pytest.param(0o654, id="group-execute-bit-only"),
        pytest.param(0o645, id="other-execute-bit-only"),
    ],
)
def test_cold_stub_with_only_a_group_or_other_execute_bit_is_flagged(mode: int, tmp_path: Path, marker_bin: Path) -> None:
    cold_stub = _write_cold_stub(marker_bin / "cold-stub")
    cold_stub.chmod(mode)

    with pytest.raises(UnwarmedStubError) as raised:
        check_scaled_path_stubs_warm(_path_env(marker_bin), None, tmp_path)

    assert str(cold_stub) in str(raised.value)


def test_message_gives_both_remedies(tmp_path: Path, marker_bin: Path) -> None:
    _write_cold_stub(marker_bin / "cold-stub")

    with pytest.raises(UnwarmedStubError) as raised:
        check_scaled_path_stubs_warm(_path_env(marker_bin), None, tmp_path)

    assert "helpers.write_prewarmed_stub" in str(raised.value)
    assert "not on this launch's PATH" in str(raised.value)


def test_message_names_the_entry_holding_the_marker_directory(tmp_path: Path, marker_bin: Path) -> None:
    _write_cold_stub(marker_bin / "cold-stub")

    with pytest.raises(UnwarmedStubError) as raised:
        check_scaled_path_stubs_warm(_path_env(marker_bin), None, tmp_path)

    assert f"marker directory in {marker_bin}" in str(raised.value)


def test_cold_stub_inside_a_subdirectory_of_a_scanned_entry_is_not_flagged(tmp_path: Path, marker_bin: Path) -> None:
    _write_cold_stub(marker_bin / "subdirectory" / "cold-stub")

    check_scaled_path_stubs_warm(_path_env(marker_bin), None, tmp_path)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="os.mkfifo is unavailable on this platform")
def test_executable_fifo_in_a_scanned_entry_is_skipped_without_blocking(tmp_path: Path, marker_bin: Path) -> None:
    fifo_path = marker_bin / "executable-fifo"
    os.mkfifo(fifo_path)
    fifo_path.chmod(0o755)
    raised_errors: list[Exception] = []

    def run_check() -> None:
        try:
            check_scaled_path_stubs_warm(_path_env(marker_bin), None, tmp_path)
        except Exception as error:
            raised_errors.append(error)

    check_thread = threading.Thread(target=run_check, daemon=True)
    check_thread.start()
    try:
        check_thread.join(FIFO_CHECK_WATCHDOG_SECONDS)
        finished_in_time = not check_thread.is_alive()
    finally:
        # A writer end releases a reader stuck in open(); closing it again gives that reader EOF.
        os.close(os.open(fifo_path, os.O_RDWR | os.O_NONBLOCK))
        check_thread.join(FIFO_CHECK_WATCHDOG_SECONDS)

    assert finished_in_time
    assert raised_errors == []


def test_cold_stub_passes_when_no_path_entry_holds_a_marker(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    _write_cold_stub(bin_dir / "cold-stub")

    check_scaled_path_stubs_warm(_path_env(bin_dir), None, tmp_path)


def test_cold_stub_in_a_separate_entry_under_basetemp_raises_while_the_marker_sits_in_another(
    tmp_path: Path, marker_bin: Path
) -> None:
    separate_entry = tmp_path / "fake-bin"
    cold_stub = _write_cold_stub(separate_entry / "gh")

    with pytest.raises(UnwarmedStubError) as raised:
        check_scaled_path_stubs_warm(_path_env(separate_entry, marker_bin), None, tmp_path)

    assert str(cold_stub) in str(raised.value)


def test_two_cold_stubs_raise_one_error_naming_both(tmp_path: Path, marker_bin: Path) -> None:
    first_stub = _write_cold_stub(marker_bin / "first-stub")
    second_stub = _write_cold_stub(tmp_path / "fake-bin" / "second-stub")

    with pytest.raises(UnwarmedStubError) as raised:
        check_scaled_path_stubs_warm(_path_env(marker_bin, tmp_path / "fake-bin"), None, tmp_path)

    assert str(first_stub) in str(raised.value)
    assert str(second_stub) in str(raised.value)


@pytest.fixture
def owned_basetemp_with_marker(tmp_path: Path) -> tuple[Path, Path]:
    """`(basetemp, marker entry)`, with the marker entry under basetemp."""
    basetemp = tmp_path / "owned"
    return basetemp, _make_marker_directory(basetemp / "marked")


@pytest.mark.parametrize("entry_name", ["outside", "owned-other"])
def test_cold_stub_in_a_non_marker_entry_outside_basetemp_passes(
    entry_name: str, tmp_path: Path, owned_basetemp_with_marker: tuple[Path, Path]
) -> None:
    basetemp, marker_entry = owned_basetemp_with_marker
    _write_cold_stub(tmp_path / entry_name / "cold-stub")

    check_scaled_path_stubs_warm(_path_env(tmp_path / entry_name, marker_entry), None, basetemp)


def test_cold_stub_in_a_non_marker_entry_equal_to_basetemp_raises(owned_basetemp_with_marker: tuple[Path, Path]) -> None:
    basetemp, marker_entry = owned_basetemp_with_marker
    cold_stub = _write_cold_stub(basetemp / "cold-stub")

    with pytest.raises(UnwarmedStubError) as raised:
        check_scaled_path_stubs_warm(_path_env(basetemp, marker_entry), None, basetemp)

    assert str(cold_stub) in str(raised.value)


def test_cold_stub_in_a_symlinked_entry_pointing_under_basetemp_raises(
    tmp_path: Path, owned_basetemp_with_marker: tuple[Path, Path]
) -> None:
    basetemp, marker_entry = owned_basetemp_with_marker
    _write_cold_stub(basetemp / "real-cold-bin" / "cold-stub")
    linked_entry = tmp_path / "linked-bin"
    linked_entry.symlink_to(basetemp / "real-cold-bin")

    with pytest.raises(UnwarmedStubError) as raised:
        check_scaled_path_stubs_warm(_path_env(linked_entry, marker_entry), None, basetemp)

    assert str(linked_entry / "cold-stub") in str(raised.value)


def test_cold_stub_in_a_marker_entry_outside_basetemp_raises(tmp_path: Path, marker_bin: Path) -> None:
    cold_stub = _write_cold_stub(marker_bin / "cold-stub")

    with pytest.raises(UnwarmedStubError) as raised:
        check_scaled_path_stubs_warm(_path_env(marker_bin), None, tmp_path / "owned")

    assert str(cold_stub) in str(raised.value)


def test_ambient_path_is_read_when_env_is_none(tmp_path: Path, marker_bin: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_cold_stub(marker_bin / "cold-stub")
    monkeypatch.setenv("PATH", str(marker_bin))

    with pytest.raises(UnwarmedStubError):
        check_scaled_path_stubs_warm(None, None, tmp_path)


def test_env_without_path_passes_while_the_ambient_path_holds_a_cold_stub(
    tmp_path: Path, marker_bin: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_cold_stub(marker_bin / "cold-stub")
    monkeypatch.setenv("PATH", str(marker_bin))

    check_scaled_path_stubs_warm({"HOME": str(tmp_path)}, None, tmp_path)


def test_absent_ambient_path_passes_when_env_is_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PATH")

    check_scaled_path_stubs_warm(None, None, tmp_path)


def test_env_with_bytes_keys_and_values_raises(tmp_path: Path, marker_bin: Path) -> None:
    cold_stub = _write_cold_stub(marker_bin / "cold-stub")

    with pytest.raises(UnwarmedStubError) as raised:
        check_scaled_path_stubs_warm({b"PATH": os.fsencode(marker_bin)}, None, tmp_path)

    assert str(cold_stub) in str(raised.value)


@pytest.mark.parametrize(
    ("path_entry", "cwd_name", "chdir_name"),
    [
        pytest.param("bin", "tmp", None, id="bare-name-with-cwd-at-its-parent"),
        pytest.param("", "bin", None, id="empty-entry-with-cwd-at-the-directory"),
        pytest.param("bin", None, "tmp", id="bare-name-after-chdir-to-its-parent"),
    ],
)
def test_relative_or_empty_entry_resolves_against_cwd_or_the_process_cwd(
    path_entry: str,
    cwd_name: str | None,
    chdir_name: str | None,
    tmp_path: Path,
    marker_bin: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directories = {"tmp": tmp_path, "bin": marker_bin, None: None}
    cold_stub = _write_cold_stub(marker_bin / "cold-stub")
    if directories[chdir_name] is not None:
        monkeypatch.chdir(directories[chdir_name])

    with pytest.raises(UnwarmedStubError) as raised:
        check_scaled_path_stubs_warm({"PATH": path_entry}, directories[cwd_name], tmp_path)

    assert cold_stub.name in str(raised.value)


def test_nonexistent_entry_after_a_marker_directory_does_not_hide_its_cold_stub(tmp_path: Path, marker_bin: Path) -> None:
    cold_stub = _write_cold_stub(marker_bin / "cold-stub")
    missing_entry = tmp_path / "missing"

    with pytest.raises(UnwarmedStubError) as raised:
        check_scaled_path_stubs_warm(_path_env(marker_bin, missing_entry), None, tmp_path / "owned")

    assert str(cold_stub) in str(raised.value)
    assert str(missing_entry) not in str(raised.value)


def test_nonexistent_entry_and_regular_file_entry_under_basetemp_pass(tmp_path: Path, marker_bin: Path) -> None:
    _warm_stub(marker_bin / "warm-stub")
    regular_file_entry = tmp_path / "plain-file"
    regular_file_entry.write_text("not a directory\n")

    check_scaled_path_stubs_warm(_path_env(marker_bin, tmp_path / "missing", regular_file_entry), None, tmp_path)


def test_path_of_nonexistent_entries_only_passes(tmp_path: Path) -> None:
    check_scaled_path_stubs_warm(_path_env(tmp_path / "missing-one", tmp_path / "missing-two"), None, tmp_path)


@pytest.mark.skipif(os.geteuid() == 0, reason="root can list a mode 000 directory")
def test_unlistable_entry_under_basetemp_passes(tmp_path: Path, marker_bin: Path) -> None:
    _warm_stub(marker_bin / "warm-stub")
    unlistable_entry = tmp_path / "unlistable"
    unlistable_entry.mkdir()
    unlistable_entry.chmod(0)
    try:
        check_scaled_path_stubs_warm(_path_env(marker_bin, unlistable_entry), None, tmp_path)
    finally:
        unlistable_entry.chmod(0o755)


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read a file with no read bits")
def test_cold_stub_that_can_be_executed_but_not_read_passes(tmp_path: Path, marker_bin: Path) -> None:
    unreadable_stub = _write_cold_stub(marker_bin / "unreadable-stub")
    unreadable_stub.chmod(MODE_WITHOUT_READ_BITS)
    try:
        check_scaled_path_stubs_warm(_path_env(marker_bin), None, tmp_path)
    finally:
        unreadable_stub.chmod(0o755)


class _LaunchRecorder:
    """Counts `Popen.__init__` calls through a wrapper installed beneath the guard."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.launch_count = 0
        original_init = subprocess.Popen.__init__

        @functools.wraps(original_init)
        def recording_init(popen_self, *args, **kwargs):
            self.launch_count += 1
            return original_init(popen_self, *args, **kwargs)

        monkeypatch.setattr(subprocess.Popen, "__init__", recording_init)


def _sentinel_writing_script(sentinel: Path) -> str:
    return f"#!/bin/sh\n: > {shlex.quote(str(sentinel))}\n"


class _StubWithSentinel(NamedTuple):
    stub: Path
    sentinel: Path

    @property
    def marker_bin(self) -> Path:
        return self.stub.parent


@pytest.fixture
def cold_stub_under_marker(tmp_path: Path) -> _StubWithSentinel:
    """A cold stub in a marker directory, written before any recorder or guard is installed."""
    sentinel = tmp_path / "cold-ran"
    marker_bin = _make_marker_directory(tmp_path / "cold-bin")
    return _StubWithSentinel(_write_cold_stub(marker_bin / "cold-stub", _sentinel_writing_script(sentinel)), sentinel)


@pytest.fixture
def warm_stub_under_marker(tmp_path: Path) -> _StubWithSentinel:
    """A warm stub in a marker directory, written before any recorder or guard is installed."""
    sentinel = tmp_path / "warm-ran"
    marker_bin = _make_marker_directory(tmp_path / "warm-bin")
    return _StubWithSentinel(write_prewarmed_stub(marker_bin / "warm-stub", _sentinel_writing_script(sentinel)), sentinel)


def _launch_run(command: str, env: dict[str, str], cwd: Path | None) -> None:
    subprocess.run([command], env=env, cwd=cwd, check=False)


def _launch_call(command: str, env: dict[str, str], cwd: Path | None) -> None:
    subprocess.call([command], env=env, cwd=cwd)


def _launch_check_call(command: str, env: dict[str, str], cwd: Path | None) -> None:
    subprocess.check_call([command], env=env, cwd=cwd)


def _launch_check_output(command: str, env: dict[str, str], cwd: Path | None) -> None:
    subprocess.check_output([command], env=env, cwd=cwd)


def _launch_popen(command: str, env: dict[str, str], cwd: Path | None) -> None:
    subprocess.Popen([command], env=env, cwd=cwd).wait()


def _launch_run_with_shell(command: str, env: dict[str, str], cwd: Path | None) -> None:
    subprocess.run(command, shell=True, env=env, cwd=cwd, check=False)


def _launch_asyncio_exec(command: str, env: dict[str, str], cwd: Path | None) -> None:
    async def launch() -> None:
        process = await asyncio.create_subprocess_exec(command, env=env, cwd=cwd)
        await process.wait()

    asyncio.run(launch())


@pytest.mark.parametrize(
    ("launch", "names_directory_by_bare_name"),
    [
        pytest.param(_launch_run, False, id="subprocess-run"),
        pytest.param(_launch_call, False, id="subprocess-call"),
        pytest.param(_launch_check_call, False, id="subprocess-check-call"),
        pytest.param(_launch_check_output, False, id="subprocess-check-output"),
        pytest.param(_launch_popen, False, id="subprocess-popen"),
        pytest.param(_launch_run_with_shell, False, id="subprocess-run-shell-true"),
        pytest.param(_launch_asyncio_exec, False, id="asyncio-create-subprocess-exec"),
        pytest.param(_launch_run, True, id="subprocess-run-with-cwd"),
    ],
)
def test_launch_of_a_cold_stub_raises_before_its_child_starts(
    launch: Callable[[str, dict[str, str], Path | None], None],
    names_directory_by_bare_name: bool,
    cold_stub_under_marker: _StubWithSentinel,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _LaunchRecorder(monkeypatch)
    install_unwarmed_stub_guard(monkeypatch, tmp_path)
    marker_bin = cold_stub_under_marker.marker_bin
    path_entry = marker_bin.name if names_directory_by_bare_name else str(marker_bin)
    cwd = marker_bin.parent if names_directory_by_bare_name else None

    with pytest.raises(UnwarmedStubError):
        launch("cold-stub", {"PATH": path_entry}, cwd)

    assert recorder.launch_count == 0
    assert not cold_stub_under_marker.sentinel.exists()


def test_launch_of_a_warm_stub_passes_through_the_guard_and_runs(
    warm_stub_under_marker: _StubWithSentinel, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = _LaunchRecorder(monkeypatch)
    install_unwarmed_stub_guard(monkeypatch, tmp_path)

    completed = subprocess.run(["warm-stub"], env=_path_env(warm_stub_under_marker.marker_bin), check=False)

    assert completed.returncode == 0
    assert recorder.launch_count == 1
    assert warm_stub_under_marker.sentinel.exists()


def test_launch_without_env_reads_the_ambient_path_and_raises_before_its_child_starts(
    cold_stub_under_marker: _StubWithSentinel, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = _LaunchRecorder(monkeypatch)
    install_unwarmed_stub_guard(monkeypatch, tmp_path)
    monkeypatch.setenv("PATH", str(cold_stub_under_marker.marker_bin))

    with pytest.raises(UnwarmedStubError):
        subprocess.run(["cold-stub"], check=False)

    assert recorder.launch_count == 0
    assert not cold_stub_under_marker.sentinel.exists()


def test_installed_guard_raises_for_a_cold_stub_in_a_non_marker_entry_under_its_basetemp(
    tmp_path: Path, marker_bin: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    basetemp = tmp_path / "owned"
    sentinel = tmp_path / "cold-ran"
    cold_stub = _write_cold_stub(basetemp / "fake-bin" / "cold-stub", _sentinel_writing_script(sentinel))
    recorder = _LaunchRecorder(monkeypatch)
    install_unwarmed_stub_guard(monkeypatch, basetemp)

    with pytest.raises(UnwarmedStubError) as raised:
        subprocess.run(["cold-stub"], env=_path_env(cold_stub.parent, marker_bin), check=False)

    assert str(cold_stub) in str(raised.value)
    assert recorder.launch_count == 0
    assert not sentinel.exists()


def test_installed_guard_passes_a_cold_stub_in_a_non_marker_entry_outside_its_basetemp(
    tmp_path: Path, marker_bin: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _warm_stub(marker_bin / "warm-stub")
    outside_entry = _write_cold_stub(tmp_path / "outside" / "cold-stub").parent
    recorder = _LaunchRecorder(monkeypatch)
    install_unwarmed_stub_guard(monkeypatch, tmp_path / "owned")

    completed = subprocess.run(["warm-stub"], env=_path_env(outside_entry, marker_bin), check=False)

    assert completed.returncode == 0
    assert recorder.launch_count == 1


def test_warm_exec_of_a_new_stub_passes_while_the_ambient_path_holds_a_cold_stub(
    tmp_path: Path, marker_bin: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_cold_stub(marker_bin / "cold-stub")
    monkeypatch.setenv("PATH", str(marker_bin))
    install_unwarmed_stub_guard(monkeypatch, tmp_path)

    second_stub = write_prewarmed_stub(marker_bin / "second-stub", f"#!/bin/sh\n{COLD_BODY}")

    assert second_stub == marker_bin / "second-stub"


def test_guard_still_raises_when_a_second_init_wrapper_is_installed_after_it(
    cold_stub_under_marker: _StubWithSentinel, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_unwarmed_stub_guard(monkeypatch, tmp_path)
    guarded_init = subprocess.Popen.__init__

    def later_wrapper(popen_self, args, *init_args, **init_kwargs):
        return guarded_init(popen_self, args, *init_args, **init_kwargs)

    monkeypatch.setattr(subprocess.Popen, "__init__", later_wrapper)

    with pytest.raises(UnwarmedStubError):
        subprocess.run(["cold-stub"], env=_path_env(cold_stub_under_marker.marker_bin), check=False)


def test_popen_init_is_the_original_again_once_the_monkeypatch_context_exits(tmp_path: Path) -> None:
    original_init = subprocess.Popen.__init__

    with pytest.MonkeyPatch.context() as scoped_monkeypatch:
        install_unwarmed_stub_guard(scoped_monkeypatch, tmp_path)
        guarded_init = subprocess.Popen.__init__
        scoped_monkeypatch.setattr(
            subprocess.Popen, "__init__", lambda popen_self, *args, **kwargs: guarded_init(popen_self, *args, **kwargs)
        )

    assert subprocess.Popen.__init__ is original_init
