"""Tests for the ~/.local/bin PATH setup step in install.sh."""
from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest

_INSTALL_SH = Path(__file__).resolve().parents[4] / "install.sh"
_BASH = shutil.which("bash") or "/bin/bash"


_FIXTURE_START = "# INSTALL_TEST_FIXTURE: local-bin-path — start\n"
_FIXTURE_END = "# INSTALL_TEST_FIXTURE: local-bin-path — end"


def _extract_local_bin_block() -> str:
    """Return the ensure_local_bin_on_path function body from install.sh.

    Same extraction strategy as test_install_sh_continuity_hardening.py:
    delimited by marker comments rather than shell-syntax matching, so a
    future reorder or nested conditional can't silently pick up the wrong
    text while the test keeps passing.
    """
    install_text = _INSTALL_SH.read_text()
    start = install_text.find(_FIXTURE_START)
    assert start != -1, f"{_FIXTURE_START!r} not found in {_INSTALL_SH}"
    end = install_text.find(_FIXTURE_END, start)
    assert end != -1, f"{_FIXTURE_END!r} not found after start marker in {_INSTALL_SH}"
    block = install_text[start + len(_FIXTURE_START) : end]
    assert "ensure_local_bin_on_path" in block, (
        f"extracted block is missing the function; markers in {_INSTALL_SH} are "
        f"probably misplaced. Got: {block!r}"
    )
    return block


def _run_local_bin_block(test_home: Path, path: str | None = None) -> subprocess.CompletedProcess:
    """Define ensure_local_bin_on_path, call it, with $HOME pointed at an isolated dir.

    `set -e` matches install.sh's own line 2, same rationale as the
    continuity-hardening tests. os.environ is never mutated, so the real
    $HOME is unreachable — the block under test writes to shell rc files.
    """
    env = dict(os.environ)
    env["HOME"] = str(test_home)
    if path is not None:
        env["PATH"] = path
    script = "set -e\n" + _extract_local_bin_block() + "\nensure_local_bin_on_path\n"
    return subprocess.run(
        [_BASH, "-c", script],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _bin_dir_without(tmp_path: Path, excluded_names: set[str]) -> Path:
    """Build a PATH dir mirroring system binaries except the excluded names."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for system_dir in [Path("/usr/bin"), Path("/bin"), Path("/usr/local/bin"), Path("/opt/homebrew/bin")]:
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


def _bin_dir_with_failing_syntax_check(tmp_path: Path, shell_name: str) -> Path:
    """Build a PATH dir mirroring system binaries, but with a stub `shell_name`
    binary whose `-n` syntax check always fails.

    Used to exercise the no-prior-backup removal branch: the function's own
    appended text is always valid, so a genuinely invalid result can only be
    produced by pairing it with pre-existing broken content (see the
    restore-from-backup test) — a first run with no pre-existing file needs a
    stub to observe the sibling "no backup, remove the file" branch.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / shell_name
    stub.write_text('#!/bin/sh\nif [ "$1" = "-n" ]; then\n  exit 1\nfi\nexit 0\n')
    stub.chmod(0o755)
    for system_dir in [Path("/usr/bin"), Path("/bin"), Path("/usr/local/bin"), Path("/opt/homebrew/bin")]:
        if not system_dir.is_dir():
            continue
        for cmd_path in system_dir.iterdir():
            dest = bin_dir / cmd_path.name
            if not dest.exists():
                with contextlib.suppress(OSError, PermissionError):
                    dest.symlink_to(cmd_path)
    return bin_dir


class TestInstallShLocalBinPath:
    def test_creates_bashrc_with_path_export_when_absent(self, tmp_path: Path) -> None:
        test_home = tmp_path / "home"
        test_home.mkdir()

        result = _run_local_bin_block(test_home)

        assert result.returncode == 0, f"block must exit 0; stderr={result.stderr!r}"
        bashrc = (test_home / ".bashrc").read_text()
        assert '.local/bin' in bashrc
        assert 'export PATH="$HOME/.local/bin:$PATH"' in bashrc

    def test_creates_zshrc_with_path_export_when_absent(self, tmp_path: Path) -> None:
        if not shutil.which("zsh"):
            pytest.skip("zsh not installed on this machine")
        test_home = tmp_path / "home"
        test_home.mkdir()

        result = _run_local_bin_block(test_home)

        assert result.returncode == 0, f"block must exit 0; stderr={result.stderr!r}"
        zshrc = (test_home / ".zshrc").read_text()
        assert '.local/bin' in zshrc
        assert 'export PATH="$HOME/.local/bin:$PATH"' in zshrc

    def test_preserves_existing_rc_content(self, tmp_path: Path) -> None:
        test_home = tmp_path / "home"
        test_home.mkdir()
        bashrc = test_home / ".bashrc"
        bashrc.write_text("alias ll='ls -la'\n")

        result = _run_local_bin_block(test_home)

        assert result.returncode == 0, f"block must exit 0; stderr={result.stderr!r}"
        content = bashrc.read_text()
        assert "alias ll='ls -la'" in content
        assert '.local/bin' in content

    def test_second_real_run_is_a_byte_for_byte_no_op(self, tmp_path: Path) -> None:
        """The actual production workflow: install.sh re-runs on every
        `git pull`. A second run against the first run's own real output
        (BEGIN/END wrapper included) must not duplicate anything."""
        test_home = tmp_path / "home"
        test_home.mkdir()
        bashrc = test_home / ".bashrc"
        bashrc.write_text("alias ll='ls -la'\n")

        first = _run_local_bin_block(test_home)
        assert first.returncode == 0, f"first run must exit 0; stderr={first.stderr!r}"
        after_first = bashrc.read_text()

        second = _run_local_bin_block(test_home)
        assert second.returncode == 0, f"second run must exit 0; stderr={second.stderr!r}"
        assert bashrc.read_text() == after_first, (
            "a second run must be a byte-for-byte no-op on the first run's real output"
        )

    def test_idempotent_when_path_already_referenced(self, tmp_path: Path) -> None:
        test_home = tmp_path / "home"
        test_home.mkdir()
        bashrc = test_home / ".bashrc"
        original = 'export PATH="$HOME/.local/bin:$PATH"\n'
        bashrc.write_text(original)

        result = _run_local_bin_block(test_home)

        assert result.returncode == 0, f"block must exit 0; stderr={result.stderr!r}"
        assert bashrc.read_text() == original, "must not duplicate an existing PATH reference"

    def test_symlinked_rc_without_companion_warns_and_leaves_target_untouched(
        self, tmp_path: Path
    ) -> None:
        test_home = tmp_path / "home"
        test_home.mkdir()
        real_dotfiles = tmp_path / "dotfiles"
        real_dotfiles.mkdir()
        target = real_dotfiles / "bashrc"
        target.write_text("echo hello\n")
        (test_home / ".bashrc").symlink_to(target)

        result = _run_local_bin_block(test_home)

        assert result.returncode == 0, f"block must exit 0; stderr={result.stderr!r}"
        assert target.read_text() == "echo hello\n", "symlink target must not be written through"
        assert not (test_home / ".bashrc.local").exists()
        assert "symlink" in result.stderr

    def test_symlinked_rc_with_companion_manages_companion_instead(
        self, tmp_path: Path
    ) -> None:
        test_home = tmp_path / "home"
        test_home.mkdir()
        real_dotfiles = tmp_path / "dotfiles"
        real_dotfiles.mkdir()
        target = real_dotfiles / "bashrc"
        target.write_text('[ -f ~/.bashrc.local ] && source ~/.bashrc.local\n')
        (test_home / ".bashrc").symlink_to(target)

        result = _run_local_bin_block(test_home)

        assert result.returncode == 0, f"block must exit 0; stderr={result.stderr!r}"
        assert target.read_text() == '[ -f ~/.bashrc.local ] && source ~/.bashrc.local\n', (
            "the symlink target itself must not be written through"
        )
        companion = test_home / ".bashrc.local"
        assert companion.exists(), "companion file must be created and managed"
        assert '.local/bin' in companion.read_text()

    def test_dangling_symlinked_rc_falls_back_to_warning(self, tmp_path: Path) -> None:
        """A symlinked rc file pointing at a nonexistent target must fall
        back to warning (using the symlink's own path in the message), not
        trust a partial/garbage `readlink -f` capture — locks in the BSD
        vs. GNU dangling-symlink fallback fix."""
        test_home = tmp_path / "home"
        test_home.mkdir()
        (test_home / ".bashrc").symlink_to(tmp_path / "nonexistent-target")

        result = _run_local_bin_block(test_home)

        assert result.returncode == 0, f"block must exit 0; stderr={result.stderr!r}"
        assert not (test_home / ".bashrc.local").exists(), (
            "a dangling symlink must not produce a companion write"
        )
        assert "symlink" in result.stderr

    def test_symlinked_rc_with_direct_path_and_companion_mention_skips_companion_write(
        self, tmp_path: Path
    ) -> None:
        """A resolved symlink target that already has ~/.local/bin directly
        must win over an incidental mention of the companion's basename
        (e.g. in a comment) — the direct match makes the companion question
        moot, so no companion file should be created."""
        test_home = tmp_path / "home"
        test_home.mkdir()
        real_dotfiles = tmp_path / "dotfiles"
        real_dotfiles.mkdir()
        target = real_dotfiles / "bashrc"
        target.write_text(
            'export PATH="$HOME/.local/bin:$PATH"\n'
            '# see .bashrc.local for machine-specific overrides\n'
        )
        (test_home / ".bashrc").symlink_to(target)

        result = _run_local_bin_block(test_home)

        assert result.returncode == 0, f"block must exit 0; stderr={result.stderr!r}"
        assert not (test_home / ".bashrc.local").exists(), (
            "an already-direct PATH match must not trigger a redundant companion write"
        )

    def test_symlinked_companion_itself_symlinked_is_not_written_through(
        self, tmp_path: Path
    ) -> None:
        """A companion resolved via the symlink branch is itself a symlink —
        must not follow that second layer of indirection to an unintended
        write target."""
        test_home = tmp_path / "home"
        test_home.mkdir()
        real_dotfiles = tmp_path / "dotfiles"
        real_dotfiles.mkdir()
        target = real_dotfiles / "bashrc"
        target.write_text('[ -f ~/.bashrc.local ] && source ~/.bashrc.local\n')
        (test_home / ".bashrc").symlink_to(target)

        other_users_file = tmp_path / "other_users_file"
        other_users_file.write_text("do not touch\n")
        (test_home / ".bashrc.local").symlink_to(other_users_file)

        result = _run_local_bin_block(test_home)

        assert result.returncode == 0, f"block must exit 0; stderr={result.stderr!r}"
        assert other_users_file.read_text() == "do not touch\n", (
            "must not write through a symlinked companion"
        )
        assert "symlink" in result.stderr

    def test_idempotent_with_non_canonical_existing_path_reference(
        self, tmp_path: Path
    ) -> None:
        """A pre-existing PATH line in a different (but still .local/bin
        referencing) form must also be treated as already-handled — this
        documents the matcher's actual substring contract rather than only
        round-tripping the function's own output."""
        test_home = tmp_path / "home"
        test_home.mkdir()
        bashrc = test_home / ".bashrc"
        original = "PATH=$PATH:$HOME/.local/bin\n"
        bashrc.write_text(original)

        result = _run_local_bin_block(test_home)

        assert result.returncode == 0, f"block must exit 0; stderr={result.stderr!r}"
        assert bashrc.read_text() == original, (
            "a differently-formatted existing PATH reference must not be duplicated"
        )

    def test_comment_only_local_bin_mention_does_not_block_setup(
        self, tmp_path: Path
    ) -> None:
        """A stale comment mentioning .local/bin (not an active export) must
        not false-positive as already-configured — PATH still needs to be
        set up for real."""
        test_home = tmp_path / "home"
        test_home.mkdir()
        bashrc = test_home / ".bashrc"
        bashrc.write_text("# TODO: someday add ~/.local/bin to PATH\n")

        result = _run_local_bin_block(test_home)

        assert result.returncode == 0, f"block must exit 0; stderr={result.stderr!r}"
        content = bashrc.read_text()
        assert "# TODO: someday add ~/.local/bin to PATH" in content
        assert 'export PATH="$HOME/.local/bin:$PATH"' in content, (
            "a comment-only mention must not block the real export from being appended"
        )

    def test_append_failure_restores_from_backup_without_data_loss(
        self, tmp_path: Path
    ) -> None:
        """A permission-denied append (rc file becomes unwritable) must
        restore the original content and warn, not silently discard the
        backup and leave PATH unconfigured with no diagnostic."""
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            pytest.skip("root bypasses file permission checks")
        test_home = tmp_path / "home"
        test_home.mkdir()
        bashrc = test_home / ".bashrc"
        original = "alias ll='ls -la'\n"
        bashrc.write_text(original)
        bashrc.chmod(0o444)
        try:
            result = _run_local_bin_block(test_home)
        finally:
            bashrc.chmod(0o644)

        assert result.returncode == 0, f"block must exit 0; stderr={result.stderr!r}"
        assert bashrc.read_text() == original, "original content must survive an append failure"
        assert "could not append to" in result.stderr
        leftover_backups = list(test_home.glob(".bashrc.bak.*"))
        assert not leftover_backups, (
            f"backup must be consumed by the restore, not left stranded; found {leftover_backups}"
        )

    def test_backup_creation_failure_skips_without_data_loss(self, tmp_path: Path) -> None:
        """An unwritable home directory (cp cannot create the backup) must
        leave the original rc file untouched and warn, not attempt an
        unprotected append. The read-only directory also blocks the earlier
        zsh iteration's new-file creation, so stderr carries that unrelated
        warning too — this only asserts the bashrc-specific message."""
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            pytest.skip("root bypasses directory permission checks")
        test_home = tmp_path / "home"
        test_home.mkdir()
        bashrc = test_home / ".bashrc"
        original = "alias ll='ls -la'\n"
        bashrc.write_text(original)
        test_home.chmod(0o555)
        try:
            result = _run_local_bin_block(test_home)
        finally:
            test_home.chmod(0o755)

        assert result.returncode == 0, f"block must exit 0; stderr={result.stderr!r}"
        assert bashrc.read_text() == original, "original content must be untouched"
        assert "could not back up" in result.stderr

    def test_no_stray_backup_file_after_successful_append(self, tmp_path: Path) -> None:
        """A successful append must clean up its own backup, not leave a
        stray .bak.<timestamp> file behind in the user's home directory."""
        test_home = tmp_path / "home"
        test_home.mkdir()
        bashrc = test_home / ".bashrc"
        bashrc.write_text("alias ll='ls -la'\n")

        result = _run_local_bin_block(test_home)

        assert result.returncode == 0, f"block must exit 0; stderr={result.stderr!r}"
        leftover_backups = list(test_home.glob(".bashrc.bak.*"))
        assert not leftover_backups, (
            f"backup file must be removed after a successful append; found {leftover_backups}"
        )

    def test_syntax_check_failure_with_prior_content_restores_from_backup(
        self, tmp_path: Path
    ) -> None:
        """Appending to a file that becomes syntactically invalid must
        restore the pre-append content byte-for-byte — this is the entire
        point of backing up before appending."""
        test_home = tmp_path / "home"
        test_home.mkdir()
        bashrc = test_home / ".bashrc"
        original = 'echo "unterminated\n'
        bashrc.write_text(original)

        result = _run_local_bin_block(test_home)

        assert result.returncode == 0, f"block must exit 0; stderr={result.stderr!r}"
        assert bashrc.read_text() == original, "original content must be restored byte-for-byte"
        assert "restored from backup" in result.stderr
        leftover_backups = list(test_home.glob(".bashrc.bak.*"))
        assert not leftover_backups, (
            f"backup file must be consumed by the restore; found {leftover_backups}"
        )

    def test_syntax_check_failure_without_prior_backup_removes_file(
        self, tmp_path: Path
    ) -> None:
        """A first run (no pre-existing rc file, so no backup) that fails its
        syntax check must remove the file, not leave a broken one behind."""
        test_home = tmp_path / "home"
        test_home.mkdir()
        bin_dir = _bin_dir_with_failing_syntax_check(tmp_path, "bash")

        result = _run_local_bin_block(test_home, path=str(bin_dir))

        assert result.returncode == 0, f"block must exit 0; stderr={result.stderr!r}"
        assert not (test_home / ".bashrc").exists(), (
            "a failed first-run syntax check must remove the file, not leave it broken"
        )
        assert "removed the file" in result.stderr

    def test_skips_shell_that_is_not_installed(self, tmp_path: Path) -> None:
        if not shutil.which("zsh"):
            pytest.skip("zsh not installed on this machine; nothing to exclude from PATH")
        test_home = tmp_path / "home"
        test_home.mkdir()
        bin_dir = _bin_dir_without(tmp_path, {"zsh"})

        result = _run_local_bin_block(test_home, path=str(bin_dir))

        assert result.returncode == 0, f"block must exit 0; stderr={result.stderr!r}"
        assert not (test_home / ".zshrc").exists(), "must not create a rc file for an absent shell"
        assert (test_home / ".bashrc").exists()
