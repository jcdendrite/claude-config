"""Tests for _stow_migration_lib.sh's resumable-failure and trust-boundary
contract. Sources the real library into a bash subprocess and calls
stow_migrate_adopted_dir directly against a fake $HOME and repo, the same
sourcing pattern hooks/tests/test_marker_lib.py uses for _lib.sh.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

LIB_SH = Path(__file__).resolve().parents[1] / "_stow_migration_lib.sh"

# Mirrors _STOW_MIGRATION_COMPLETE_SENTINEL in _stow_migration_lib.sh -- a
# drift between the two shows up as a loud assertion failure below, not a
# silent pass, since every test that checks it also checks the migration's
# other observable effects (exit code, target contents).
MIGRATION_COMPLETE_SENTINEL = ".migration-complete"


def _run_migrate(repo_dir: Path, name: str, home: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", f'. "{LIB_SH}"; stow_migrate_adopted_dir "$1" "$2"',
         "run_migrate", str(repo_dir), name],
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": str(home)},
    )


class TestPartialStepCCopyResumability:
    """A step (c) cp -R that fails partway (one unreadable backup entry)
    leaves $target existing but only partially populated. Bare
    `[ -e "$target" ]` cannot tell that state apart from a completed
    migration -- reproduces staff-platform-engineer's permission-000-file
    repro and pins the fix (the completion sentinel)."""

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_retry_does_not_silently_accept_partial_target_and_completes_once_unblocked(
        self, tmp_path: Path
    ) -> None:
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        repo = tmp_path / "repo"
        (repo / "claude" / ".claude" / "plans").mkdir(parents=True)

        # Simulate a prior run that got through step (a) (backup populated)
        # and step (b) (symlink unlinked, $target absent) but never
        # completed step (c).
        backup_dir = home / ".claude-config-relocate-backup" / "plans.20260101000000"
        (backup_dir / "subdir").mkdir(parents=True)
        backup_dir.chmod(0o700)
        (backup_dir / "plan1.md").write_text("plan1\n")
        (backup_dir / "subdir" / "f.md").write_text("sub\n")
        unreadable = backup_dir / "unreadable.md"
        unreadable.touch()
        unreadable.chmod(0o000)

        target = home / ".claude" / "plans"
        sentinel = backup_dir / MIGRATION_COMPLETE_SENTINEL

        # Run 1: cp -R fails on the unreadable entry, leaving $target
        # partially populated.
        result1 = _run_migrate(repo, "plans", home)
        assert result1.returncode == 1, result1.stderr
        assert (target / "plan1.md").exists()
        assert (target / "subdir" / "f.md").exists()
        assert not (target / "unreadable.md").exists()
        assert not sentinel.exists()

        # Run 2: the retry a user (or a re-run of install.sh) performs. The
        # underlying permission problem is still unfixed -- this must fail
        # loudly again, not silently return 0 because $target now exists.
        result2 = _run_migrate(repo, "plans", home)
        assert result2.returncode == 1, (
            "a retry against a still-incomplete target must not silently "
            f"succeed; got exit {result2.returncode}, stderr={result2.stderr!r}"
        )
        assert result2.stderr.strip() != "", (
            "a retry that still can't complete the migration must report "
            "why, not go silent"
        )
        assert not sentinel.exists()

        # Fix the underlying problem: a further retry now completes fully.
        unreadable.chmod(0o644)
        result3 = _run_migrate(repo, "plans", home)
        assert result3.returncode == 0, result3.stderr
        assert (target / "unreadable.md").exists()
        assert sentinel.exists()

        # A subsequent run is a true, silent no-op.
        result4 = _run_migrate(repo, "plans", home)
        assert result4.returncode == 0
        assert result4.stdout == ""
        assert result4.stderr == ""


class TestNewestBackupSelection:
    """_stow_migration_lib_newest_backup must skip an empty candidate and
    pick the newest non-empty one -- no existing test constructs more than
    one candidate backup directory."""

    def test_resumes_from_newest_non_empty_backup_skipping_older_empty_one(
        self, tmp_path: Path
    ) -> None:
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        repo = tmp_path / "repo"
        (repo / "claude" / ".claude" / "plans").mkdir(parents=True)

        backup_root = home / ".claude-config-relocate-backup"
        older_empty = backup_root / "plans.20260101000000"
        older_empty.mkdir(parents=True)
        newer_populated = backup_root / "plans.20260102000000"
        newer_populated.mkdir(parents=True)
        (newer_populated / "p.md").write_text("# plans from newer backup\n")

        result = _run_migrate(repo, "plans", home)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        target = home / ".claude" / "plans"
        assert target.is_dir() and not target.is_symlink()
        assert (target / "p.md").read_text() == "# plans from newer backup\n", (
            "must resume from the newer, populated backup, not the older, "
            "empty one"
        )


class TestBackupRootSymlinkGuard:
    """$_STOW_MIGRATION_BACKUP_ROOT is a fixed, predictable path shared with
    relocate-claude-config.sh's own BACKUP_DIR -- reproduces
    ciso-reviewer's pre-planted-symlink repro and pins the ported guard."""

    def test_preplanted_symlink_backup_root_refuses_rather_than_writes_through_it(
        self, tmp_path: Path
    ) -> None:
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        repo = tmp_path / "repo"
        plans_source = repo / "claude" / ".claude" / "plans"
        plans_source.mkdir(parents=True)
        (plans_source / "secret.md").write_text("secret\n")
        (home / ".claude" / "plans").symlink_to(plans_source)

        elsewhere = tmp_path / "attacker-owned"
        elsewhere.mkdir()
        (home / ".claude-config-relocate-backup").symlink_to(elsewhere)

        result = _run_migrate(repo, "plans", home)

        assert result.returncode == 1
        assert "already exists as a symlink" in result.stderr
        assert list(elsewhere.iterdir()) == [], (
            "migration must not write backup content through a pre-planted "
            "symlink at the shared backup root"
        )
        # Refusal must happen before step (b) unlinks the live symlink.
        assert (home / ".claude" / "plans").is_symlink()
