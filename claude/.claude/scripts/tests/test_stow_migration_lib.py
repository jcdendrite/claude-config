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


def _run_repair(repo_dir: Path, name: str, home: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", f'. "{LIB_SH}"; stow_repair_nested_adoption "$1" "$2"',
         "run_repair", str(repo_dir), name],
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


class TestRepairNestedAdoption:
    """stow_repair_nested_adoption undoes the per-entry adoption that
    stow --adopt's --ignore fails to prevent once the parent .claude/ is
    already unfolded (see install.sh's stow-adopt-ignore comment) --
    reproduces the actual corrupted state found on a machine that ran the
    pre-fix install.sh: $HOME/.claude/briefs itself is a real directory, but
    each file inside it is a symlink back into the repo checkout."""

    def test_replaces_entries_symlinked_into_the_repo_with_real_copies(
        self, tmp_path: Path
    ) -> None:
        home = tmp_path / "home"
        repo = tmp_path / "repo"
        source = repo / "claude" / ".claude" / "briefs"
        source.mkdir(parents=True)
        (source / "task-a.md").write_text("# task a\n")

        target = home / ".claude" / "briefs"
        target.mkdir(parents=True)
        (target / "task-a.md").symlink_to(source / "task-a.md")

        result = _run_repair(repo, "briefs", home)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        repaired = target / "task-a.md"
        assert repaired.is_file() and not repaired.is_symlink(), (
            "a per-entry symlink into the repo must become a real file"
        )
        assert repaired.read_text() == "# task a\n"

    def test_leaves_a_plain_real_entry_and_an_unrelated_symlink_untouched(
        self, tmp_path: Path
    ) -> None:
        """Selectivity: only symlinks resolving into this name's repo source
        are repaired -- a plain real file and a symlink pointing somewhere
        else (e.g. a user's own dotfile symlink) are left exactly as-is."""
        home = tmp_path / "home"
        repo = tmp_path / "repo"
        source = repo / "claude" / ".claude" / "briefs"
        source.mkdir(parents=True)

        target = home / ".claude" / "briefs"
        target.mkdir(parents=True)
        (target / "already-real.md").write_text("# already real\n")
        elsewhere = tmp_path / "elsewhere.md"
        elsewhere.write_text("# not from this repo\n")
        (target / "elsewhere.md").symlink_to(elsewhere)

        result = _run_repair(repo, "briefs", home)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert not (target / "already-real.md").is_symlink()
        assert (target / "already-real.md").read_text() == "# already real\n"
        assert (target / "elsewhere.md").is_symlink(), (
            "a symlink resolving outside this name's repo source must not be touched"
        )

    def test_no_op_when_target_directory_is_absent(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        repo = tmp_path / "repo"
        (repo / "claude" / ".claude" / "briefs").mkdir(parents=True)
        (home / ".claude").mkdir(parents=True)

        result = _run_repair(repo, "briefs", home)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert not (home / ".claude" / "briefs").exists(), (
            "a no-op run must not create the target as a side effect"
        )

    def test_no_op_when_repo_source_directory_is_absent(self, tmp_path: Path) -> None:
        """The function's other no-op branch -- $repo_dir/claude/.claude/NAME
        itself doesn't exist, so expected_source_real can't be resolved.
        Distinct code path from the target-absent case above: a regression
        that treated an unresolvable source as an empty-string prefix could
        make the case glob-match every symlink, real or not."""
        home = tmp_path / "home"
        repo = tmp_path / "repo"
        target = home / ".claude" / "briefs"
        target.mkdir(parents=True)
        (target / "already-real.md").write_text("# already real\n")
        elsewhere = tmp_path / "elsewhere.md"
        elsewhere.write_text("# not from this repo\n")
        (target / "elsewhere.md").symlink_to(elsewhere)

        result = _run_repair(repo, "briefs", home)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert (target / "already-real.md").read_text() == "# already real\n"
        assert (target / "elsewhere.md").is_symlink(), (
            "an unresolvable repo source must not make the function treat "
            "every symlink as in-scope"
        )

    def test_repairs_multiple_entries_in_one_call_leaving_real_ones_alone(
        self, tmp_path: Path
    ) -> None:
        """A loop-early-exit bug, or one entry's failure silently aborting
        the rest, would pass the single-entry tests above but not this one."""
        home = tmp_path / "home"
        repo = tmp_path / "repo"
        source = repo / "claude" / ".claude" / "briefs"
        source.mkdir(parents=True)
        (source / "task-a.md").write_text("# task a\n")
        (source / "task-b.md").write_text("# task b\n")

        target = home / ".claude" / "briefs"
        target.mkdir(parents=True)
        (target / "task-a.md").symlink_to(source / "task-a.md")
        (target / "task-b.md").symlink_to(source / "task-b.md")
        (target / "already-real.md").write_text("# already real\n")

        result = _run_repair(repo, "briefs", home)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        for name, content in (
            ("task-a.md", "# task a\n"),
            ("task-b.md", "# task b\n"),
            ("already-real.md", "# already real\n"),
        ):
            entry = target / name
            assert entry.is_file() and not entry.is_symlink(), f"{name} must be a real file"
            assert entry.read_text() == content

    def test_second_run_is_a_stable_noop(self, tmp_path: Path) -> None:
        """Pins the function's documented idempotency and install.sh's actual
        call pattern -- every name, every run, unconditionally."""
        home = tmp_path / "home"
        repo = tmp_path / "repo"
        source = repo / "claude" / ".claude" / "briefs"
        source.mkdir(parents=True)
        (source / "task-a.md").write_text("# task a\n")

        target = home / ".claude" / "briefs"
        target.mkdir(parents=True)
        (target / "task-a.md").symlink_to(source / "task-a.md")

        first = _run_repair(repo, "briefs", home)
        assert first.returncode == 0, f"stderr={first.stderr!r}"
        repaired = target / "task-a.md"
        mtime_after_first = repaired.stat().st_mtime_ns

        second = _run_repair(repo, "briefs", home)

        assert second.returncode == 0, f"stderr={second.stderr!r}"
        assert second.stdout == "" and second.stderr == "", (
            "a run against already-repaired, plain real files must be "
            f"silent; stdout={second.stdout!r} stderr={second.stderr!r}"
        )
        assert repaired.is_file() and not repaired.is_symlink()
        assert repaired.read_text() == "# task a\n"
        assert repaired.stat().st_mtime_ns == mtime_after_first, (
            "a no-op second run must not rewrite an already-real file"
        )

    def test_dangling_symlink_into_repo_source_is_left_alone_with_a_warning(
        self, tmp_path: Path
    ) -> None:
        """realpath resolves a symlink whose target no longer exists, so a
        deleted repo-side file must not make the repair silently drop the
        last pointer to it -- the documented fallback is warn-and-leave."""
        home = tmp_path / "home"
        repo = tmp_path / "repo"
        source = repo / "claude" / ".claude" / "briefs"
        source.mkdir(parents=True)
        deleted_source_file = source / "task-a.md"
        deleted_source_file.write_text("# task a\n")

        target = home / ".claude" / "briefs"
        target.mkdir(parents=True)
        dangling = target / "task-a.md"
        dangling.symlink_to(deleted_source_file)
        deleted_source_file.unlink()

        result = _run_repair(repo, "briefs", home)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert dangling.is_symlink() and not dangling.exists(), (
            "a dangling symlink whose repo-side file was deleted must be "
            "left as-is, not silently removed"
        )
        assert result.stderr.strip() != "", (
            "leaving a dangling symlink unrepaired must warn, not go silent"
        )

    def test_repaired_entry_preserves_the_source_files_mode(self, tmp_path: Path) -> None:
        """mktemp creates $tmp at 0600; a plain cp onto an existing
        destination leaves that mode alone instead of adopting the source's
        -- cp -p is required, not incidental, to avoid silently narrowing a
        repaired entry's permissions."""
        home = tmp_path / "home"
        repo = tmp_path / "repo"
        source = repo / "claude" / ".claude" / "briefs"
        source.mkdir(parents=True)
        source_file = source / "task-a.md"
        source_file.write_text("# task a\n")
        source_file.chmod(0o644)

        target = home / ".claude" / "briefs"
        target.mkdir(parents=True)
        (target / "task-a.md").symlink_to(source_file)

        result = _run_repair(repo, "briefs", home)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        repaired_mode = (target / "task-a.md").stat().st_mode & 0o777
        assert repaired_mode == 0o644, (
            f"repaired entry must keep the source's mode, not mktemp's 0600; "
            f"got {oct(repaired_mode)}"
        )

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_mktemp_failure_is_caught_and_leaves_the_symlink_in_place(
        self, tmp_path: Path
    ) -> None:
        """Forces the mktemp branch (:223) to fail by making $target
        unwritable -- the mv-failure branch (:240) is not exercised here:
        mktemp always creates $tmp inside $target for same-filesystem mv
        atomicity, so a permission state that blocks mv would already have
        blocked the preceding mktemp, making mktemp-succeeds/mv-fails
        unreachable via plain permission bits. Left undemonstrated for that
        reason, not by oversight."""
        home = tmp_path / "home"
        repo = tmp_path / "repo"
        source = repo / "claude" / ".claude" / "briefs"
        source.mkdir(parents=True)
        (source / "task-a.md").write_text("# task a\n")

        target = home / ".claude" / "briefs"
        target.mkdir(parents=True)
        (target / "task-a.md").symlink_to(source / "task-a.md")
        target.chmod(0o500)
        try:
            result = _run_repair(repo, "briefs", home)
        finally:
            target.chmod(0o700)

        assert result.returncode == 0, (
            f"a per-entry mktemp failure must not abort the function; stderr={result.stderr!r}"
        )
        # A substring of the library's own warning, not blanket non-emptiness:
        # mktemp emits its own diagnostic on this exact failure independent of
        # the library, so a bare "stderr is non-empty" check would still pass
        # if the library's own "could not de-adopt" echo were silently broken.
        assert "could not de-adopt" in result.stderr, (
            f"a failed de-adopt attempt must warn via the library's own "
            f"message, not just mktemp's; stderr={result.stderr!r}"
        )
        assert (target / "task-a.md").is_symlink(), (
            "the original symlink must be left in place when mktemp fails"
        )
