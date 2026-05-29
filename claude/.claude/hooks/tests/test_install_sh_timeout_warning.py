"""Tests for the `timeout` availability warning in install.sh."""
from __future__ import annotations

import contextlib
import shutil
import subprocess
from pathlib import Path

_INSTALL_SH = Path(__file__).resolve().parents[4] / "install.sh"
_BASH = shutil.which("bash") or "/bin/bash"


class TestInstallShTimeoutWarning:
    def test_warning_emitted_to_stderr_when_timeout_absent(self, tmp_path: Path) -> None:
        """install.sh emits a timeout-not-found warning to stderr when `timeout`
        is not in PATH, exit code is 0, and stdout does not contain the warning.

        Strategy: extract only the timeout-availability check block from install.sh
        and run it in a sandboxed environment where `timeout` is absent. This avoids
        the complexity of stubbing out all of install.sh's runtime dependencies (stow,
        claude, jq, etc.) just to reach the warning code.

        The extracted check block is:
            if ! command -v timeout >/dev/null 2>&1; then
              printf '[install] warning: ...' >&2
              ...
            fi
        """
        # Build a PATH with standard POSIX tools but no timeout binary.
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()

        # Symlink all system binaries from /usr/bin and /bin EXCEPT timeout.
        for system_dir in [Path("/usr/bin"), Path("/bin")]:
            if not system_dir.is_dir():
                continue
            for cmd_path in system_dir.iterdir():
                if cmd_path.name == "timeout":
                    continue  # Deliberately omit timeout — that is what we test.
                dest = bin_dir / cmd_path.name
                if not dest.exists():
                    with contextlib.suppress(OSError, PermissionError):
                        dest.symlink_to(cmd_path)

        # Extract the timeout warning block from install.sh and run it directly.
        # This is more robust than running install.sh in full (which requires
        # stow, claude, gh, etc. to be present or stubbed).
        install_text = _INSTALL_SH.read_text()
        # Find the timeout warning block.
        warning_marker = "if ! command -v timeout"
        start = install_text.find(warning_marker)
        assert start != -1, f"timeout warning block not found in {_INSTALL_SH}"
        # Extract to the matching fi.
        block_start = start
        fi_pos = install_text.find("\nfi\n", block_start)
        assert fi_pos != -1, "closing 'fi' for timeout block not found"
        timeout_block = install_text[block_start:fi_pos + 4]  # include '\nfi\n'

        result = subprocess.run(
            [_BASH, "-c", timeout_block],
            capture_output=True,
            text=True,
            check=False,
            env={"PATH": str(bin_dir)},
        )
        # Warning goes to stderr, not stdout.
        assert "timeout" in result.stderr, (
            f"Expected timeout warning on stderr; got stderr={result.stderr!r}\n"
            f"stdout={result.stdout!r}\nreturncode={result.returncode}"
        )
        assert "timeout" not in result.stdout, (
            f"Warning must go to stderr only; found 'timeout' in stdout={result.stdout!r}"
        )
        # The timeout block must exit 0 — it is a warning, not a hard failure.
        assert result.returncode == 0, (
            f"Timeout warning block must exit 0; got {result.returncode}\n"
            f"stderr={result.stderr!r}"
        )
