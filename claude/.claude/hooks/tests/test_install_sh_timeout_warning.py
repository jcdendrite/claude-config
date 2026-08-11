"""Tests for the `timeout`/`gtimeout` availability warning in install.sh."""
from __future__ import annotations

import contextlib
import shutil
import subprocess
from pathlib import Path

_INSTALL_SH = Path(__file__).resolve().parents[4] / "install.sh"
_BASH = shutil.which("bash") or "/bin/bash"


def _extract_timeout_warning_block() -> str:
    """Extract only the timeout/gtimeout-availability check block from
    install.sh, for running in a sandboxed environment. This avoids the
    complexity of stubbing out all of install.sh's runtime dependencies
    (stow, claude, jq, etc.) just to reach the warning code.

    The extracted check block is:
        if ! command -v timeout >/dev/null 2>&1 && ! command -v gtimeout >/dev/null 2>&1; then
          printf '[install] warning: ...' >&2
          ...
        fi
    """
    install_text = _INSTALL_SH.read_text()
    warning_marker = "if ! command -v timeout"
    start = install_text.find(warning_marker)
    assert start != -1, f"timeout warning block not found in {_INSTALL_SH}"
    fi_pos = install_text.find("\nfi\n", start)
    assert fi_pos != -1, "closing 'fi' for timeout block not found"
    return install_text[start:fi_pos + 4]  # include '\nfi\n'


def _bin_dir_excluding(tmp_path: Path, *excluded_names: str) -> Path:
    """Build a PATH dir with standard POSIX tools symlinked from /usr/bin
    and /bin, omitting the named binaries."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for system_dir in [Path("/usr/bin"), Path("/bin")]:
        if not system_dir.is_dir():
            continue
        for cmd_path in system_dir.iterdir():
            if cmd_path.name in excluded_names:
                continue
            dest = bin_dir / cmd_path.name
            if not dest.exists():
                with contextlib.suppress(OSError, PermissionError):
                    dest.symlink_to(cmd_path)
    return bin_dir


class TestInstallShTimeoutWarning:
    def test_warning_emitted_to_stderr_when_neither_timeout_nor_gtimeout_present(
        self, tmp_path: Path
    ) -> None:
        """install.sh emits a timeout-not-found warning to stderr when neither
        `timeout` nor `gtimeout` is in PATH, exit code is 0, and stdout does
        not contain the warning."""
        bin_dir = _bin_dir_excluding(tmp_path, "timeout", "gtimeout")
        timeout_block = _extract_timeout_warning_block()

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

    def test_warning_suppressed_when_only_gtimeout_present(self, tmp_path: Path) -> None:
        """A Homebrew-coreutils machine (gtimeout present, timeout absent) is
        covered by _lib_capped_for's own gtimeout probe, so the onboarding
        warning must not fire for it."""
        bin_dir = _bin_dir_excluding(tmp_path, "timeout", "gtimeout")
        gtimeout_target = shutil.which("gtimeout") or shutil.which("timeout")
        assert gtimeout_target is not None, "no timeout-shaped binary found to stand in for gtimeout"
        (bin_dir / "gtimeout").symlink_to(gtimeout_target)
        timeout_block = _extract_timeout_warning_block()

        result = subprocess.run(
            [_BASH, "-c", timeout_block],
            capture_output=True,
            text=True,
            check=False,
            env={"PATH": str(bin_dir)},
        )
        assert result.stderr == "", f"Expected no warning; got stderr={result.stderr!r}"
        assert result.returncode == 0
