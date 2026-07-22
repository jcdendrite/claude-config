"""Tests for the ~/.claude / ~/.claude.json permission-hardening step in install.sh."""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

_INSTALL_SH = Path(__file__).resolve().parents[4] / "install.sh"
_BASH = shutil.which("bash") or "/bin/bash"


_FIXTURE_START = "# INSTALL_TEST_FIXTURE: continuity-hardening — start\n"
_FIXTURE_END = "# INSTALL_TEST_FIXTURE: continuity-hardening — end"


def _extract_hardening_block() -> str:
    """Return the ~/.claude / ~/.claude.json chmod block from install.sh.

    Strategy: extract only the hardening block and run it in isolation,
    same approach as test_install_sh_timeout_warning.py — avoids stubbing
    all of install.sh's runtime dependencies (stow, claude, gh, jq, etc.)
    just to reach this block.

    The block is delimited by explicit marker comments rather than located
    by matching its own shell syntax. Syntax matching would silently pick up
    unrelated logic, or silently drop a guard, whenever the block is
    reordered or a nested conditional is added — and a test that extracts
    the wrong text still passes.
    """
    install_text = _INSTALL_SH.read_text()
    start = install_text.find(_FIXTURE_START)
    assert start != -1, f"{_FIXTURE_START!r} not found in {_INSTALL_SH}"
    end = install_text.find(_FIXTURE_END, start)
    assert end != -1, f"{_FIXTURE_END!r} not found after start marker in {_INSTALL_SH}"
    block = install_text[start + len(_FIXTURE_START) : end]
    assert "chmod 700" in block and "chmod 600" in block, (
        f"extracted block is missing a chmod; markers in {_INSTALL_SH} are "
        f"probably misplaced. Got: {block!r}"
    )
    return block


def _run_hardening_block(test_home: Path) -> subprocess.CompletedProcess:
    """Run the extracted hardening block with $HOME pointed at an isolated dir.

    `set -e` is prepended to match install.sh's own line 2, so the block is
    exercised under the same abort-on-error semantics it runs with in
    production. Without it a future edit could introduce a statement that
    fails fatally in install.sh but is invisible here, since the block's exit
    status would come from whichever statement happened to land last.

    The isolated directory is held in a local named test_home; HOME is set
    only inside the per-invocation subprocess env below. os.environ is never
    mutated, so the real $HOME is unreachable from these tests — the block
    under test runs chmod, so a leaked real $HOME would narrow the caller's
    own home directory.
    """
    env = dict(os.environ)
    env["HOME"] = str(test_home)
    return subprocess.run(
        [_BASH, "-c", "set -e\n" + _extract_hardening_block()],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


class TestInstallShContinuityHardening:
    def test_claude_dir_becomes_owner_only(self, tmp_path: Path) -> None:
        """~/.claude ends up mode 700 after the hardening step runs."""
        test_home = tmp_path / "home"
        claude_dir = test_home / ".claude"
        claude_dir.mkdir(parents=True, mode=0o775)

        result = _run_hardening_block(test_home)

        assert result.returncode == 0, f"hardening block must exit 0; stderr={result.stderr!r}"
        dir_mode = stat.S_IMODE(claude_dir.stat().st_mode)
        assert dir_mode == 0o700, (
            f"expected ~/.claude to be 0700 after install.sh hardening, got {oct(dir_mode)}"
        )

    def test_claude_json_becomes_owner_only(self, tmp_path: Path) -> None:
        """~/.claude.json ends up mode 600 after the hardening step runs."""
        test_home = tmp_path / "home"
        test_home.mkdir(parents=True)
        claude_json = test_home / ".claude.json"
        claude_json.write_text("{}")
        claude_json.chmod(0o664)

        result = _run_hardening_block(test_home)

        assert result.returncode == 0, f"hardening block must exit 0; stderr={result.stderr!r}"
        file_mode = stat.S_IMODE(claude_json.stat().st_mode)
        assert file_mode == 0o600, (
            f"expected ~/.claude.json to be 0600 after install.sh hardening, got {oct(file_mode)}"
        )

    def test_missing_claude_json_does_not_fail_run(self, tmp_path: Path) -> None:
        """A fresh machine with no ~/.claude.json yet installs silently.

        Asserting on stderr rather than only on the exit status: chmod
        failures are deliberately non-fatal, so an exit-status assertion
        alone would still pass if the existence guard were dropped. The
        observable contract for an absent path is that nothing is attempted
        and the user is told nothing.
        """
        test_home = tmp_path / "home"
        claude_dir = test_home / ".claude"
        claude_dir.mkdir(parents=True, mode=0o775)
        # Deliberately no ~/.claude.json.

        result = _run_hardening_block(test_home)

        assert result.returncode == 0, (
            f"hardening block must not fail when ~/.claude.json is absent; stderr={result.stderr!r}"
        )
        assert result.stderr == "", (
            f"an absent ~/.claude.json must not produce a warning; stderr={result.stderr!r}"
        )
        assert not (test_home / ".claude.json").exists()

    def test_missing_claude_dir_does_not_fail_run(self, tmp_path: Path) -> None:
        """A run before stow has created ~/.claude installs silently.

        Asserts on stderr for the same reason as the ~/.claude.json case
        above — a non-fatal chmod makes the exit status alone insufficient to
        detect a dropped existence guard.
        """
        test_home = tmp_path / "home"
        test_home.mkdir(parents=True)
        # Deliberately no ~/.claude directory.

        result = _run_hardening_block(test_home)

        assert result.returncode == 0, (
            f"hardening block must not fail when ~/.claude is absent; stderr={result.stderr!r}"
        )
        assert result.stderr == "", (
            f"an absent ~/.claude must not produce a warning; stderr={result.stderr!r}"
        )
        assert not (test_home / ".claude").exists()

    def test_symlinked_claude_dir_is_warned_about_not_chmodded(
        self, tmp_path: Path
    ) -> None:
        """A symlinked ~/.claude must be left alone, not dereferenced and narrowed.

        `stow` tree-folds a target directory that does not already exist into
        a single symlink pointing back into the repo checkout. `chmod` follows
        symlinks, so chmod-ing that path would set 700 on the checkout's own
        directory instead of a private one — and would leave the user with no
        indication that the hardening did not apply to a private location.
        """
        test_home = tmp_path / "home"
        test_home.mkdir(parents=True)
        link_target = tmp_path / "checkout" / "claude" / ".claude"
        link_target.mkdir(parents=True)
        # chmod rather than mkdir(mode=...): the mkdir mode argument is masked
        # by the caller's umask, so the starting mode would vary by environment
        # and this assertion would encode the umask rather than the behavior.
        link_target.chmod(0o775)
        (test_home / ".claude").symlink_to(link_target)

        result = _run_hardening_block(test_home)

        assert result.returncode == 0, (
            f"hardening block must not fail on a symlinked ~/.claude; stderr={result.stderr!r}"
        )
        target_mode = stat.S_IMODE(link_target.stat().st_mode)
        assert target_mode == 0o775, (
            f"symlink target must be left at its original mode, got {oct(target_mode)}"
        )
        assert "symlink" in result.stderr, (
            f"a skipped chmod must warn the user; stderr={result.stderr!r}"
        )
