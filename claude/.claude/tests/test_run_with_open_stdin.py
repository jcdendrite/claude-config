"""Contract tests for helpers.run_with_open_stdin."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from helpers import ShellExecGuardError, run_with_open_stdin

# Larger than a Linux pipe buffer (64 KiB) and a macOS one (16 KiB to 64 KiB).
OUTPUT_LARGER_THAN_A_PIPE_BUFFER_BYTES = 1_000_000

# The deadline sits far above the timeout the kill test passes and far below the child's runtime.
SLEEPING_CHILD_RUNTIME_SECONDS = 30
KILLED_CHILD_RETURN_DEADLINE_SECONDS = SLEEPING_CHILD_RUNTIME_SECONDS / 3


def test_child_that_reads_stdin_raises_timeout_expired() -> None:
    with pytest.raises(subprocess.TimeoutExpired):
        run_with_open_stdin(["cat"], env=dict(os.environ), timeout=0.5)


def test_timeout_kills_the_child_instead_of_waiting_for_it_to_exit() -> None:
    started = time.monotonic()

    with pytest.raises(subprocess.TimeoutExpired):
        run_with_open_stdin(["sleep", str(SLEEPING_CHILD_RUNTIME_SECONDS)], env=dict(os.environ), timeout=0.5)

    assert time.monotonic() - started < KILLED_CHILD_RETURN_DEADLINE_SECONDS


def test_child_that_ignores_stdin_returns_its_exit_status_and_empty_output() -> None:
    result = run_with_open_stdin(["true"], env=dict(os.environ))

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""


def test_stdout_and_stderr_are_returned_separately_with_the_exit_status() -> None:
    script = "import sys; print('to-stdout'); print('to-stderr', file=sys.stderr); sys.exit(3)"

    result = run_with_open_stdin([sys.executable, "-c", script], env=dict(os.environ))

    assert result.returncode == 3
    assert result.stdout == "to-stdout\n"
    assert result.stderr == "to-stderr\n"


def test_output_larger_than_a_pipe_buffer_does_not_stall_the_child() -> None:
    script = f"import sys; sys.stdout.write('x' * {OUTPUT_LARGER_THAN_A_PIPE_BUFFER_BYTES})"

    result = run_with_open_stdin([sys.executable, "-c", script], env=dict(os.environ), timeout=10)

    assert result.returncode == 0
    assert len(result.stdout) == OUTPUT_LARGER_THAN_A_PIPE_BUFFER_BYTES


def test_env_bash_script_with_no_bash_on_path_raises_shell_exec_guard_error(tmp_path: Path) -> None:
    script = tmp_path / "needs-bash.sh"
    script.write_text("#!/usr/bin/env bash\ntrue\n")
    script.chmod(0o755)
    empty_path_dir = tmp_path / "empty-path"
    empty_path_dir.mkdir()

    with pytest.raises(ShellExecGuardError):
        run_with_open_stdin([str(script)], env={"PATH": str(empty_path_dir)})
