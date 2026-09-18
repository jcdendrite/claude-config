"""Unit tests for _lib.sh's _lib_is_pseudo_file_path, the predicate the
PII, private-project, backtick-escaping, and stow-reminder hooks share to
decide a body/message source path is unscannable.

These source _lib.sh directly and call the function -- no hook invocation,
no JSON payload -- mirroring test_lib_append_line_locked.py. Each consuming
hook's own test file keeps a subprocess-level case proving its call site
still applies the guard.
"""
from __future__ import annotations

import subprocess

import pytest
from helpers import HOOKS_DIR

LIB_SH = HOOKS_DIR / "_lib.sh"


def _is_pseudo_file_path(path: str) -> int:
    return subprocess.run(
        ["bash", "-c", f'. "{LIB_SH}"; _lib_is_pseudo_file_path "$1"', "_", path],
        capture_output=True,
        text=True,
        check=False,
    ).returncode


class TestLibIsPseudoFilePath:
    @pytest.mark.parametrize(
        "path",
        [
            "-",
            "/dev/stdin",
            "/dev/fd/0",
            "/proc/self/fd/0",
            "/proc/1234/fd/3",
            "/dev/fd/12",
            "/proc/self/fd/12",
        ],
    )
    def test_pseudo_path_is_accepted(self, path):
        assert _is_pseudo_file_path(path) == 0

    @pytest.mark.parametrize(
        "path",
        [
            "/tmp/body.md",
            "/tmp/dev/fd/0",
            "body-/dev/stdin",
            "/dev/stdin.md",
            "",
            "-x",
            "/dev/fd",
            "/dev/fdx",
            "/proc/self/status",
            "/proc/self/fdinfo/0",
            "/tmp/proc/1/fd/0",
        ],
        ids=[
            "real_path",
            "path_containing_dev_fd",
            "path_containing_dev_stdin",
            "dev_stdin_prefix_only",
            "empty_string",
            "dash_prefixed_non_dash",
            "dev_fd_without_slash",
            "dev_fd_prefix_only",
            "proc_self_non_fd",
            "proc_self_fdinfo",
            "path_containing_proc_fd",
        ],
    )
    def test_non_pseudo_path_is_rejected(self, path):
        assert _is_pseudo_file_path(path) == 1
