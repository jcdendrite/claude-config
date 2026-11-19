"""Wiring tests: this tree's conftest installs helpers.install_shell_exec_guard and helpers.install_unwarmed_stub_guard."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import helpers
import pytest
from helpers import ShellExecGuardError, UnwarmedStubError


def test_env_bash_stub_under_a_bash_less_path_raises_the_guard_error(tmp_path: Path) -> None:
    stub = tmp_path / "stub.sh"
    stub.write_text("#!/usr/bin/env bash\nexit 0\n")
    stub.chmod(0o755)
    bash_less_dir = tmp_path / "bash-less"
    bash_less_dir.mkdir()

    with pytest.raises(ShellExecGuardError):
        subprocess.run([str(stub)], env={"PATH": str(bash_less_dir)}, capture_output=True, check=False)


def test_cold_stub_in_a_basetemp_entry_apart_from_the_scaled_shim_raises_the_unwarmed_stub_error(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    marker_bin = tmp_path / "bin"
    (marker_bin / helpers._SCALED_TIMEOUT_MARKER_DIRNAME).mkdir(parents=True)
    cold_dir = tmp_path_factory.mktemp("cold-bin")
    cold_stub = cold_dir / "cold-stub"
    cold_stub.write_text("#!/bin/sh\nexit 0\n")
    cold_stub.chmod(0o755)

    with pytest.raises(UnwarmedStubError, match=re.escape(str(cold_stub))):
        subprocess.run(
            [sys.executable, "-c", ""],
            env={"PATH": f"{cold_dir}{os.pathsep}{marker_bin}"},
            capture_output=True,
            check=False,
        )


def test_cold_stub_outside_this_workers_basetemp_does_not_raise_the_unwarmed_stub_error(tmp_path: Path) -> None:
    marker_bin = tmp_path / "bin"
    (marker_bin / helpers._SCALED_TIMEOUT_MARKER_DIRNAME).mkdir(parents=True)
    cold_dir_outside_basetemp = Path(tempfile.mkdtemp(prefix="cold-bin-outside-basetemp-"))
    try:
        cold_stub = cold_dir_outside_basetemp / "cold-stub"
        cold_stub.write_text("#!/bin/sh\nexit 0\n")
        cold_stub.chmod(0o755)

        result = subprocess.run(
            [sys.executable, "-c", ""],
            env={"PATH": f"{cold_dir_outside_basetemp}{os.pathsep}{marker_bin}"},
            capture_output=True,
            check=False,
        )
    finally:
        shutil.rmtree(cold_dir_outside_basetemp, ignore_errors=True)

    assert result.returncode == 0
