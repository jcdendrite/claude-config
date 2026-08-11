"""Tests for install.sh's $CLAUDE_SESSION_MAY_BE_ACTIVE computation -- the
concurrency guard the plans/handoffs/briefs migration and the generic
un-adopt loop both gate on before touching ~/.claude, to avoid racing a live
Claude Code session's writes to it.
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

_INSTALL_SH = Path(__file__).resolve().parents[4] / "install.sh"
_BASH = shutil.which("bash") or "/bin/bash"

_FIXTURE_START = "# INSTALL_TEST_FIXTURE: session-concurrency-check — start\n"
_FIXTURE_END = "# INSTALL_TEST_FIXTURE: session-concurrency-check — end"


def _extract_concurrency_check_block() -> str:
    """Same marker-delimited extraction strategy as the other
    test_install_sh_*.py files."""
    install_text = _INSTALL_SH.read_text()
    start = install_text.find(_FIXTURE_START)
    assert start != -1, f"{_FIXTURE_START!r} not found in {_INSTALL_SH}"
    end = install_text.find(_FIXTURE_END, start)
    assert end != -1, f"{_FIXTURE_END!r} not found after start marker in {_INSTALL_SH}"
    block = install_text[start + len(_FIXTURE_START) : end]
    assert "CLAUDE_SESSION_MAY_BE_ACTIVE" in block, (
        f"extracted block is missing the concurrency check; markers in {_INSTALL_SH} "
        f"are probably misplaced. Got: {block!r}"
    )
    return block


def _make_fake_bin_dir(tmp_path: Path, *, include_pgrep: bool, pgrep_exit: int = 1) -> Path:
    """A PATH directory holding just what the extracted block needs: `id`
    (a real symlink -- the block computes `$(id -u)`) and, unless
    include_pgrep is False, a fake `pgrep` that ignores its arguments and
    exits with the given code. Excluding pgrep this way (rather than trying
    to strip it from the real PATH) is what makes the "pgrep unavailable"
    case reproducible regardless of what's actually installed on the test
    machine."""
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    real_id = shutil.which("id")
    assert real_id, "id must be on PATH to build a usable fake bin dir"
    (bin_dir / "id").symlink_to(real_id)
    if include_pgrep:
        fake_pgrep = bin_dir / "pgrep"
        fake_pgrep.write_text(f"#!/bin/sh\nexit {pgrep_exit}\n")
        fake_pgrep.chmod(fake_pgrep.stat().st_mode | stat.S_IEXEC)
    return bin_dir


def _run_concurrency_check(bin_dir: Path) -> subprocess.CompletedProcess:
    script = "set -e\n" + _extract_concurrency_check_block() + '\nprintf \'%s\' "$CLAUDE_SESSION_MAY_BE_ACTIVE"\n'
    return subprocess.run(
        [_BASH, "-c", script],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PATH": str(bin_dir)},
    )


class TestSessionConcurrencyCheck:
    def test_pgrep_finds_a_claude_process_sets_a_nonempty_reason(self, tmp_path: Path) -> None:
        bin_dir = _make_fake_bin_dir(tmp_path, include_pgrep=True, pgrep_exit=0)

        result = _run_concurrency_check(bin_dir)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert result.stdout != "", "a detected session must produce a non-empty reason"
        assert "session is currently running" in result.stdout

    def test_pgrep_finds_no_claude_process_leaves_it_empty(self, tmp_path: Path) -> None:
        bin_dir = _make_fake_bin_dir(tmp_path, include_pgrep=True, pgrep_exit=1)

        result = _run_concurrency_check(bin_dir)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert result.stdout == "", (
            f"no detected session must leave the variable empty; got {result.stdout!r}"
        )

    def test_pgrep_unavailable_fails_closed_with_a_nonempty_reason(self, tmp_path: Path) -> None:
        """Fail-closed, not fail-open: if this machine can't verify whether
        a session is running, the caller must treat that the same as "a
        session might be running," not proceed as if it verified none is."""
        bin_dir = _make_fake_bin_dir(tmp_path, include_pgrep=False)

        result = _run_concurrency_check(bin_dir)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert result.stdout != "", "an unverifiable state must produce a non-empty reason"
        assert "pgrep not found" in result.stdout
