"""Wiring test: this tree's conftest installs the shell-exec guard from helpers.install_shell_exec_guard."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from helpers import ShellExecGuardError


def test_env_bash_stub_under_a_bash_less_path_raises_the_guard_error(tmp_path: Path) -> None:
    stub = tmp_path / "stub.sh"
    stub.write_text("#!/usr/bin/env bash\nexit 0\n")
    stub.chmod(0o755)
    bash_less_dir = tmp_path / "bash-less"
    bash_less_dir.mkdir()

    with pytest.raises(ShellExecGuardError):
        subprocess.run([str(stub)], env={"PATH": str(bash_less_dir)}, capture_output=True, check=False)
